"""Single-owner read-only recovery loop for Kiwoom mock account events."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time as clock_time, timezone
from typing import Awaitable, Callable, TypeVar

from websockets.asyncio.client import connect

from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import (
    KiwoomMockAccountReader,
    MockAccountRecovery,
    snapshot_from_order_execution,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import (
    AccountBalanceChange,
    OrderExecution,
    parse_account_balance_changes,
    parse_order_executions,
)
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
from kiwoom_monitor.domain.order_contract import AccountScope, AccountSnapshot


logger = logging.getLogger(__name__)
MOCK_WS_URL = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"
_Result = TypeVar("_Result")


class _AccountRealtimeCollector:
    """Shared socket lifetime; environment/venue policy belongs to each concrete reader."""

    _environment = "mock"
    _url = MOCK_WS_URL

    def __init__(
        self,
        token_provider: Callable[[], str],
        event_handler: Callable[[str, object], None],
        now_provider: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        account_scope_resolver: Callable[[str], AccountScope | None] | None = None,
        async_token_provider: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._async_token_provider = async_token_provider
        self._event_handler = event_handler
        self._now = now_provider
        self._account_scope_resolver = account_scope_resolver
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None
        self.ready = False
        self.error_code = None

    async def start(self) -> None:
        if self._close_task is not None:
            if not self._close_task.done(): raise RuntimeError("MOCK_REALTIME_CLOSE_IN_PROGRESS")
            self._close_task.result()
            self._close_task = None
            self._closing = False
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name="kiwoom-mock-account-realtime",
            )

    async def close(self) -> None:
        self._closing = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="mock-realtime-close")
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        self.ready = False
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            if not self._session_open():
                await asyncio.sleep(1)
                continue
            try:
                await self._receive()
                self.ready = False
                self._deliver("disconnected", self._environment)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.ready = False
                self.error_code = "ACCOUNT_WS_FAILED"
                self._deliver("disconnected", self._environment)
                logger.warning("%s 계좌 전용 실시간 연결 실패", self._environment)
                await asyncio.sleep(3)

    async def _receive(self) -> None:
        token_task = asyncio.create_task(
            self._async_token_provider() if self._async_token_provider is not None
            else asyncio.to_thread(self._token_provider), name="mock-realtime-token",
        )
        try:
            token = await asyncio.shield(token_task)
        except asyncio.CancelledError:
            await asyncio.gather(asyncio.shield(token_task), return_exceptions=True)
            raise
        if self._closing: return
        async with connect(self._url, open_timeout=15, ping_interval=None) as websocket:
            await websocket.send(json.dumps({"trnm": "LOGIN", "token": token}))
            login = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
            if self._environment == "real" and (login.get("trnm") != "LOGIN" or login.get("return_code") not in (0, "0")):
                raise RuntimeError("ACCOUNT_WS_LOGIN_FAILED")
            if login.get("return_code") not in (None, 0, "0"):
                raise RuntimeError(f"모의계좌 WebSocket 로그인 실패: {login.get('return_msg', '')}")
            await self._send_subscription(websocket)
            while self._session_open():
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=1)
                except TimeoutError:
                    continue
                message = json.loads(raw)
                name = str(message.get("trnm", "")).upper()
                if name == "PING":
                    await websocket.send(json.dumps(message))
                    continue
                if name == "REG":
                    if message.get("return_code") not in (None, 0, "0"):
                        raise RuntimeError(
                            f"모의계좌 WebSocket 구독 실패: {message.get('return_msg', '')}"
                        )
                    if self._environment == "real" and message.get("return_code") not in (0, "0"):
                        raise RuntimeError("ACCOUNT_WS_REG_FAILED")
                    self.ready, self.error_code = True, None
                    self._deliver("connected", self._environment)
                    continue
                if name == "REAL":
                    if self._environment == "real" and not self.ready:
                        continue
                    self._dispatch_realtime(message)

    async def _send_subscription(self, websocket: object) -> None:
        await websocket.send(json.dumps({
            "trnm": "REG",
            "grp_no": "1",
            "refresh": "1",
            "data": [{"item": [""], "type": ["00", "04"]}],
        }))

    def _dispatch_realtime(self, message: dict[str, object]) -> None:
        row_types = {
            str(row.get("type", ""))
            for row in (message.get("data") or ())
            if isinstance(row, dict)
        }
        executions = parse_order_executions(message, self._account_scope_resolver)
        for value in executions:
            self._deliver("order_execution", value)
        if "00" in row_types:
            # 접수·정정·취소와 체결 뒤 잔량은 REST 누적조회로 다시 맞춘다.
            self._deliver("order_changed", None)
        balance_changes = parse_account_balance_changes(message, self._account_scope_resolver)
        for value in balance_changes:
            self._deliver("account_balance", value)
        if "04" in row_types and not balance_changes:
            self._deliver("account_balance_changed", None)

    def _deliver(self, event_type: str, value: object) -> None:
        if self._closing: return
        try:
            self._event_handler(event_type, value)
        except Exception:
            # 계좌 복구 처리의 국소 실패가 전용 실시간 연결을 끊지 않게 한다.
            logger.warning("%s 계좌 실시간 이벤트 전달 실패: %s", self._environment, event_type)

    def _session_open(self) -> bool:
        now = self._now()
        if now.weekday() >= 5:
            return False
        current = now.time().replace(tzinfo=None)
        return clock_time(8) <= current < clock_time(16)


class MockAccountRealtimeCollector(_AccountRealtimeCollector):
    """Preserve the mock-only URL/session and existing constructor contract."""


class RealAccountRealtimeCollector(_AccountRealtimeCollector):
    """Account-only real socket for a non-market credential profile."""

    _environment = "real"
    _url = "wss://api.kiwoom.com:10000/api/dostk/websocket"

    def __init__(self, *args, **kwargs):
        if kwargs.get("account_scope_resolver") is None:
            raise ValueError("REAL_ACCOUNT_SCOPE_RESOLVER_REQUIRED")
        super().__init__(*args, **kwargs)

    def _session_open(self):
        now = self._now()
        return now.weekday() < 5 and clock_time(8) <= now.time().replace(tzinfo=None) < clock_time(20)

    def _dispatch_realtime(self, message):
        # Admission/cancel rows may not produce an execution. Only a verified
        # 9201 match can still request account recovery for these rows.
        if str(message.get("trnm", "")).upper() != "REAL":
            return
        rows = []
        for row in message.get("data") or ():
            if not isinstance(row, dict) or row.get("type") not in {"00", "04"}:
                continue
            values = row.get("values")
            raw = str(values.get("9201", "")).strip() if isinstance(values, dict) else ""
            scope = self._account_scope_resolver(raw) if raw else None
            if scope is not None and scope.environment.value == "real":
                rows.append(row)
                self._deliver("order_changed" if row["type"] == "00" else "account_balance_changed", scope)
        filtered = {"trnm": "REAL", "data": rows}
        for value in parse_order_executions(filtered, self._account_scope_resolver):
            self._deliver("order_execution", value)
        for value in parse_account_balance_changes(filtered, self._account_scope_resolver):
            self._deliver("account_balance", value)


class MockAccountMonitor:
    """Consume 00/04 notifications and refresh the persisted mock account view."""

    def __init__(
        self,
        reader: KiwoomMockAccountReader,
        runtime: ExecutionRuntime,
        repository: ExecutionRepository,
        *,
        now_provider: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        lease_seconds: int = 60,
        read_limiter: asyncio.Semaphore | None = None,
    ) -> None:
        self._reader = reader
        self._read_limiter = read_limiter
        self._runtime = runtime
        self._repository = repository
        self._now = now_provider
        self._lease_seconds = max(10, int(lease_seconds))
        self._heartbeat_seconds = max(5.0, self._lease_seconds / 2)
        self._queue: asyncio.Queue[tuple[str, object | None]] = asyncio.Queue()
        self._recovery_lock = asyncio.Lock()
        self._recovery_queued = False
        self._recovered_once = False
        self._task: asyncio.Task[None] | None = None
        self._start_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._heartbeat_stop = asyncio.Event()
        self._work: set[asyncio.Task[object]] = set()
        self._accepting = True

    async def start(self, *, initial_read: bool = False) -> None:
        if not self._accepting: raise RuntimeError("MOCK_MONITOR_CLOSED")
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start(initial_read), name="mock-monitor-start")
        await asyncio.shield(self._start_task)

    async def _start(self, initial_read: bool) -> None:
        if self._task is not None:
            return
        await asyncio.to_thread(self._runtime.start, lease_seconds=self._lease_seconds)
        self._task = asyncio.create_task(self._run(), name="kiwoom-mock-account-monitor")
        self._heartbeat_task = asyncio.create_task(self._heartbeat(), name="mock-monitor-heartbeat")
        if initial_read:
            await self._recover()
        else:
            self._request_recovery()

    async def close(self) -> None:
        self._accepting = False
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="mock-monitor-close")
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        if self._start_task is not None:
            await asyncio.gather(asyncio.shield(self._start_task), return_exceptions=True)
        if self._task is not None:
            self._queue.put_nowait(("stop", None))
            await asyncio.shield(self._task)
            self._task = None
        if self._work:
            await asyncio.gather(*(asyncio.shield(task) for task in tuple(self._work)), return_exceptions=True)
        self._heartbeat_stop.set()
        if self._heartbeat_task is not None:
            await asyncio.shield(self._heartbeat_task)
            self._heartbeat_task = None
        if self._start_task is not None:
            await asyncio.to_thread(self._runtime.stop)

    async def _owned(self, factory: Callable[[], Awaitable[_Result]]) -> _Result:
        task = asyncio.create_task(factory(), name="mock-monitor-work")
        self._work.add(task)
        def completed(done):
            self._work.discard(done)
            if not done.cancelled(): done.exception()
        task.add_done_callback(completed)
        return await asyncio.shield(task)

    def handle_realtime(self, event_type: str, value: object) -> None:
        if not self._accepting: return
        if event_type == "order_execution" and isinstance(value, OrderExecution):
            self._queue.put_nowait((event_type, value))
        elif (
            event_type == "account_balance" and isinstance(value, AccountBalanceChange)
        ) or event_type in {"account_balance_changed", "order_changed", "connected"}:
            self._request_recovery()

    async def refresh_account(self) -> AccountSnapshot:
        if not self._accepting: raise RuntimeError("MOCK_MONITOR_CLOSED")
        recovery = await self._recover()
        return recovery.account

    def _request_recovery(self) -> None:
        if self._accepting and not self._recovery_queued:
            self._recovery_queued = True
            self._queue.put_nowait(("recover", None))

    async def _heartbeat(self) -> None:
        while not self._heartbeat_stop.is_set():
            try:
                await asyncio.wait_for(self._heartbeat_stop.wait(), timeout=self._heartbeat_seconds)
            except TimeoutError:
                try:
                    await asyncio.to_thread(self._runtime.heartbeat)
                except Exception:
                    self._accepting = False
                    logger.exception("모의계좌 실행 임대 갱신 실패; 계좌 모니터를 중지합니다")
                    return
                if not self._recovered_once:
                    self._request_recovery()

    async def _run(self) -> None:
        while True:
            event_type, value = await self._queue.get()
            try:
                if event_type == "stop": return
                if event_type == "recover":
                    self._recovery_queued = False
                    await self._recover()
                elif event_type == "order_execution" and isinstance(value, OrderExecution):
                    await self._reconcile_execution(value)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("모의계좌 실시간 복구 처리 실패")
            finally:
                self._queue.task_done()

    async def _recover(self) -> MockAccountRecovery:
        return await self._owned(self._recover_once)

    async def _recover_once(self) -> MockAccountRecovery:
        async with self._recovery_lock:
            if self._read_limiter is None:
                recovery = await self._reader.read()
            else:
                async with self._read_limiter:
                    recovery = await self._reader.read()
            received_at = self._aware_now()
            await asyncio.to_thread(
                self._repository.save_account_snapshot, "mock", recovery.account, received_at,
            )
            for snapshot in recovery.orders:
                record = await asyncio.to_thread(
                    self._repository.find_by_broker_order_id,
                    "mock", self._runtime.account_ref, self._runtime.run_id, snapshot.broker_order_id,
                )
                if record is not None and record.intent.run_id == self._runtime.run_id:
                    await asyncio.to_thread(self._runtime.reconcile, record.intent.intent_id, snapshot)
            self._recovered_once = True
            return recovery

    async def _reconcile_execution(self, execution: OrderExecution) -> None:
        await self._owned(lambda: self._reconcile_execution_once(execution))

    async def _reconcile_execution_once(self, execution: OrderExecution) -> None:
        record = await asyncio.to_thread(
            self._repository.find_by_broker_order_id,
            "mock", self._runtime.account_ref, self._runtime.run_id, execution.order_no,
        )
        if record is None or record.intent.run_id != self._runtime.run_id:
            return
        as_of = self._aware_now()
        snapshot = snapshot_from_order_execution(
            execution, account_ref=self._runtime.account_ref, as_of=as_of,
        )
        await asyncio.to_thread(self._runtime.reconcile, record.intent.intent_id, snapshot)

    def _aware_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("mock account monitor clock must be timezone-aware")
        return value
