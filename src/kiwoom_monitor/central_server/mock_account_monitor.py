"""Single-owner read-only recovery loop for Kiwoom mock account events."""

from __future__ import annotations

import asyncio
from .diagnostic_replay_contract import captured_workload
import json
import logging
import uuid
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, time as clock_time, timezone
from typing import Awaitable, Callable, TypeVar
from zoneinfo import ZoneInfo

from websockets.asyncio.client import connect

from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
from kiwoom_monitor.application.position_target import PositionAccountObservation, reconcile_position_account_observation
from kiwoom_monitor.application.trade_cost_service import trade_costs_from_pages
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
KST = ZoneInfo("Asia/Seoul")
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
        automation_risk_repository: object | None = None,
        automation_binding_revision: int = 0,
        automation_account_scope: AccountScope | None = None,
        new_orders_enabled_on_start: bool = True,
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
        self._risk_repository = automation_risk_repository
        self._binding_revision = automation_binding_revision
        self._account_scope = automation_account_scope
        self._reconciliation_revision = 0
        self._risk_revision_initialized = False
        self._reconnect_count = 0
        self._balance_mismatch_count = 0
        self._new_orders_enabled_on_start = new_orders_enabled_on_start
        self._risk_costs: dict[tuple[object, str, str], object] = {}
        self._risk_costs_initialized = False
        self._risk_cost_day = None
        self._risk_events = []
        self._risk_event_cursor = 0
        self._position_observation = None
        self._position_session = uuid.uuid4().hex
        self._position_revision = 0
        self._position_generation = 0
        self._position_listener = None
        self._automation_risk_listener = None
        self._automation_risk_cut = None
        self._automation_execution_inputs_enabled = False
        self._automation_execution_cut = None
        self._automation_execution_fingerprint = None
        self.automation_execution_revision = 0
        self._position_order_scope_requested = False
        self._position_loop = None
        self._position_runtime_revision = runtime.observation_revision
        runtime.set_observation_listener(self._runtime_order_changed)

    @property
    def latest_position_observation(self):
        """No TR/DB/refresh; the current owner's last successfully completed recovery."""
        if (not self._accepting or self._runtime.observation_in_progress
                or self._position_runtime_revision != self._runtime.observation_revision):
            return None
        value = deepcopy(self._position_observation)
        return value if (not self._runtime.observation_in_progress
                         and self._position_runtime_revision == self._runtime.observation_revision) else None

    def set_account_order_observation_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("account order observation selection must be boolean")
        if enabled != self._position_order_scope_requested:
            self._position_order_scope_requested = enabled
            self._invalidate_position_observation()

    def position_study_portfolio(self,observation):
        """Borrow existing verified cost/fill caches, without TR or ledger reads."""
        from kiwoom_monitor.application.position_target import position_study_portfolio_from_account_cut
        if not self.position_observation_matches(observation):return None
        if len(self._risk_events)>2000 or len(self._risk_costs)!=1:return None
        try:
            result=position_study_portfolio_from_account_cut(observation,tuple(self._risk_events),tuple(self._risk_costs.values()))
        except (ValueError,TypeError,KeyError,RuntimeError):return None
        return result if self.position_observation_matches(observation) else None

    def position_observation_matches(self,observation):
        """Validate a borrowed cut's epoch without copying, TR, DB or refresh."""
        if not self._accepting or self._runtime.observation_in_progress:return False
        revision=self._runtime.observation_revision;current=self._position_observation
        return (current is not None and current==observation and self._position_runtime_revision==revision
            and current is self._position_observation and not self._runtime.observation_in_progress
            and revision==self._runtime.observation_revision)

    def _runtime_order_changed(self):
        loop = self._position_loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._invalidate_for_execution_change)
            except RuntimeError:
                pass  # The event loop retired during shutdown; getters still check the revision.

    def _invalidate_for_execution_change(self):
        if (self._position_observation is not None or self._automation_risk_cut is not None) and self._position_runtime_revision != self._runtime.observation_revision:
            self._invalidate_position_observation()

    def set_position_observation_listener(self, listener):
        self._position_listener = listener

    @property
    def latest_automation_risk_cut(self):
        """Copied ACKed Risk/account, without duplicating broker order/fill pages."""
        if (not self._accepting or self._runtime.observation_in_progress
                or self._position_runtime_revision != self._runtime.observation_revision):
            return None
        value = deepcopy(self._automation_risk_cut)
        return value if (not self._runtime.observation_in_progress
                         and self._position_runtime_revision == self._runtime.observation_revision) else None

    def set_automation_risk_listener(self, listener):
        if self._automation_risk_listener is not None and self._automation_risk_listener != listener:
            raise RuntimeError("MOCK_AUTOMATION_RISK_LISTENER_ALREADY_OWNED")
        self._automation_risk_listener = listener
        cut = self.latest_automation_risk_cut
        if cut is not None:
            listener(cut)  # Deliver activation's already ACKed cut without another TR.

    def set_automation_execution_inputs_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('automation execution input selection must be boolean')
        if enabled != self._automation_execution_inputs_enabled:
            self._automation_execution_inputs_enabled = enabled
            self._invalidate_position_observation()
            if enabled:
                self._request_recovery()

    def request_automation_execution_recovery(self):
        if self._automation_execution_inputs_enabled:
            # Original dispatch/enrollment runs in an owned native thread. Queue
            # mutation belongs to the monitor's loop, including its waiter wake.
            loop = self._position_loop
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if loop is not None and current is not loop:
                if not loop.is_closed():
                    loop.call_soon_threadsafe(self._request_recovery)
            else:
                self._request_recovery()  # Existing coalesced owner queue.

    @property
    def latest_automation_execution_cut(self):
        if not self._automation_execution_inputs_enabled or self.latest_automation_risk_cut is None:
            return None
        value = deepcopy(self._automation_execution_cut)
        return value if self.latest_automation_risk_cut is not None else None

    def clear_automation_risk_listener(self, listener):
        if self._automation_risk_listener == listener:
            self._automation_risk_listener = None

    def _automation_risk_changed(self):
        if self._automation_risk_listener is None:
            return
        cut = self.latest_automation_risk_cut
        if cut is not None:
            try:
                self._automation_risk_listener(cut)
            except Exception:
                logger.exception("모의 자동운용 Risk 보호 요청 전달 실패")

    def _position_changed(self):
        if self._position_listener is not None:
            try:
                self._position_listener()
            except Exception:
                logger.exception("모의계좌 RAM 관찰 소비 알림 실패")

    def _invalidate_position_observation(self):
        self._position_generation += 1
        self._automation_risk_cut = None
        self._automation_execution_cut = None
        if self._position_observation is not None:
            self._position_observation = None
            self._position_changed()

    @property
    def is_started(self) -> bool:
        """Event loop installed and not yet retired by close; not recovery/readiness."""
        return self._task is not None

    async def start(self, *, initial_read: bool = False) -> None:
        if not self._accepting: raise RuntimeError("MOCK_MONITOR_CLOSED")
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start(initial_read), name="mock-monitor-start")
        await asyncio.shield(self._start_task)

    @captured_workload('account', 'execution_start', actor_per_invocation=True)
    async def _start(self, initial_read: bool) -> None:
        if self._task is not None:
            return
        await asyncio.to_thread(
            self._runtime.start,
            lease_seconds=self._lease_seconds,
            new_orders_enabled=self._new_orders_enabled_on_start,
        )
        self._task = asyncio.create_task(self._run(), name="kiwoom-mock-account-monitor")
        self._heartbeat_task = asyncio.create_task(self._heartbeat(), name="mock-monitor-heartbeat")
        if initial_read:
            await self._recover()
        else:
            self._request_recovery()

    async def close(self) -> None:
        self._accepting = False
        self._automation_risk_listener = None
        self._automation_risk_cut = None
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="mock-monitor-close")
        await asyncio.shield(self._close_task)

    @captured_workload('account', 'execution_stop', actor_per_invocation=True)
    async def _close(self) -> None:
        self._invalidate_position_observation()
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
        if event_type in {"order_execution", "account_balance", "account_balance_changed", "order_changed", "connected", "disconnected"}:
            self._invalidate_position_observation()
        if event_type == "order_execution" and isinstance(value, OrderExecution):
            self._queue.put_nowait((event_type, value))
        elif (
            event_type == "account_balance" and isinstance(value, AccountBalanceChange)
        ) or event_type in {"account_balance_changed", "order_changed", "connected"}:
            self._request_recovery()
        elif event_type == "disconnected":
            self._reconnect_count += 1

    async def refresh_account(self) -> AccountSnapshot:
        if not self._accepting: raise RuntimeError("MOCK_MONITOR_CLOSED")
        recovery = await self._recover()
        return recovery.account

    async def refresh_recovery(self) -> MockAccountRecovery:
        if not self._accepting:
            raise RuntimeError("MOCK_MONITOR_CLOSED")
        return await self._recover()

    def _request_recovery(self) -> None:
        if self._accepting and not self._recovery_queued:
            self._recovery_queued = True
            self._queue.put_nowait(("recover", None))

    @captured_workload('account', 'execution_heartbeat', actor_per_invocation=True)
    async def _heartbeat(self) -> None:
        while not self._heartbeat_stop.is_set():
            try:
                await asyncio.wait_for(self._heartbeat_stop.wait(), timeout=self._heartbeat_seconds)
            except TimeoutError:
                try:
                    await asyncio.to_thread(self._runtime.heartbeat)
                except Exception:
                    self._accepting = False
                    self._invalidate_position_observation()
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

    @captured_workload('account', 'mock_account_recovery', actor_per_invocation=True)
    async def _recover_once(self) -> MockAccountRecovery:
        async with self._recovery_lock:
            self._position_loop = asyncio.get_running_loop()
            self._invalidate_position_observation()
            position_generation = self._position_generation
            scope_requested = self._position_order_scope_requested or self._automation_execution_inputs_enabled
            expected_runtime_revision = self._runtime.observation_revision
            before, before_complete = (), False
            ledger_read_failed = False
            if scope_requested:
                try:
                    before, before_complete = await asyncio.to_thread(self._repository.account_active_intents, self._runtime.account_ref)
                except Exception:
                    ledger_read_failed = True
                    logger.exception("계좌 전체 RAM 관측 원장 읽기 실패")
            query_started_at = self._aware_now()
            if self._read_limiter is None:
                recovery = await self._reader.read()
            else:
                async with self._read_limiter:
                    recovery = await self._reader.read()
            received_at = self._aware_now()
            await asyncio.to_thread(
                self._repository.save_account_snapshot, "mock", recovery.account, received_at,
            )
            records, unmapped = [], []
            for snapshot in recovery.orders:
                record = await asyncio.to_thread(
                    self._repository.find_by_broker_order_id,
                    "mock", self._runtime.account_ref, self._runtime.run_id, snapshot.broker_order_id,
                )
                if record is not None and record.intent.run_id == self._runtime.run_id:
                    expected_runtime_revision += 2
                    records.append(await asyncio.to_thread(self._runtime.reconcile, record.intent.intent_id, snapshot))
                else:
                    unmapped.append(snapshot.broker_order_id)
            reconciled = False
            risk = None
            after, after_complete = (), False
            if scope_requested:
                try:
                    after, after_complete = await asyncio.to_thread(self._repository.account_active_intents, self._runtime.account_ref)
                except Exception:
                    ledger_read_failed = True
                    logger.exception("계좌 전체 RAM 관측 원장 읽기 실패")
            if self._risk_repository is not None:
                active, risk = await self._publish_risk(recovery, query_started_at,
                    active_records=tuple(r for r in after if r.intent.run_id == self._runtime.run_id)
                    if scope_requested and after_complete else None)
                records = list({record.intent.intent_id: record for record in (*records, *active)}.values())
                reconciled = risk.reconciliation_complete
            self._position_revision += 1
            # Broker recovery is account-wide; these local records are run-scoped.
            # Missing cross-run/queued coverage cannot be promoted by an empty TR.
            if position_generation == self._position_generation and self._accepting:
                reasons = ("account_wide_intent_coverage_unverified",)
                if len(records) > 400 or len(unmapped) > 400:
                    reasons += ("order_observation_budget_exceeded",)
                input_ref = f"mock-account-ram:{self._position_session}:{self._position_revision}"
                observed_runtime_revision = self._runtime.observation_revision
                if scope_requested:
                    try:
                        self._position_observation = deepcopy(reconcile_position_account_observation(recovery, before, after,
                            input_ref=input_ref, observed_at=self._aware_now(), ledger_complete=before_complete and after_complete,
                            runtime_stable=(expected_runtime_revision == observed_runtime_revision
                                            and not self._runtime.observation_in_progress),
                            ledger_read_failed=ledger_read_failed))
                        self._position_observation = replace(self._position_observation, risk_snapshot=deepcopy(risk))
                    except Exception:
                        logger.exception("계좌 전체 RAM 관측 대사 실패")
                        self._position_observation = None
                else:
                    self._position_observation = PositionAccountObservation(
                        deepcopy(recovery.account), tuple(deepcopy(records[:400])), input_ref, self._aware_now(),
                        reconciled, False, reasons, tuple(sorted(unmapped)[:400]))
                    self._position_observation = replace(self._position_observation, risk_snapshot=deepcopy(risk))
                self._position_runtime_revision = observed_runtime_revision
                if (risk is not None and expected_runtime_revision == observed_runtime_revision
                        and not self._runtime.observation_in_progress):
                    self._automation_risk_cut = {"risk": deepcopy(risk), "account": deepcopy(recovery.account)}
                    if self._automation_execution_inputs_enabled and self._position_observation is not None:
                        facts = tuple(sorted((order.broker_order_id, order.state.value, order.filled_quantity,
                            order.remaining_quantity, tuple(sorted((f.execution_id, f.quantity, f.price) for f in order.fills)))
                            for order in recovery.orders))
                        if facts != self._automation_execution_fingerprint:
                            self.automation_execution_revision += 1
                            self._automation_execution_fingerprint = facts
                        self._automation_execution_cut = {'risk': risk,
                            'observation': self._position_observation,
                            'recovery': replace(recovery, account=self._position_observation.account)}
                self._position_changed()
                self._automation_risk_changed()
            self._recovered_once = True
            return recovery

    async def _publish_risk(
        self, recovery: MockAccountRecovery, query_started_at: datetime, *, active_records=None,
    ) -> tuple:
        if self._account_scope is None or self._account_scope.account_ref != self._runtime.account_ref:
            raise RuntimeError("MOCK_AUTOMATION_RISK_SCOPE_MISSING")
        cursor = self._risk_event_cursor
        while True:
            page = await asyncio.to_thread(
                self._repository.account_events,
                "mock", self._runtime.account_ref, after_sequence=cursor, limit=1000,
            )
            self._risk_events.extend(page.events)
            cursor = page.next_cursor
            if not page.has_more:
                break
            if len(self._risk_events) >= 100_000:
                raise RuntimeError("MOCK_AUTOMATION_EVENT_HISTORY_LIMIT")
        self._risk_event_cursor = cursor
        events = tuple(self._risk_events)
        now = self._aware_now()
        kst_today = now.astimezone(KST).date()
        fill_days = [
            event.occurred_at.astimezone(KST).date()
            for event in events if event.event_type == "FILL"
        ]
        full_cost_refresh = not self._risk_costs_initialized or self._risk_cost_day != kst_today
        start = min(fill_days, default=kst_today) if full_cost_refresh else kst_today
        cost_query_complete = True
        try:
            if self._read_limiter is None:
                cost_pages = await self._reader.read_trade_cost_pages(start, kst_today)
            else:
                async with self._read_limiter:
                    cost_pages = await self._reader.read_trade_cost_pages(start, kst_today)
        except (OSError, RuntimeError) as error:
            # The four broker recovery reads already succeeded. Publish that
            # evidence with unknown P&L: ENTER stays closed while the existing
            # manage-only EXIT gate can verify the run's held quantity.
            cost_query_complete = False
            logger.warning("모의 자동운용 비용 조회 실패 · %s", type(error).__name__)
        else:
            # Invalid cost contents/scope must still fail; only request errors
            # above are converted to explicitly unavailable cost evidence.
            refreshed_costs = trade_costs_from_pages(cost_pages, start, kst_today, self._account_scope)
            if full_cost_refresh:
                self._risk_costs.clear()
            for cost in refreshed_costs:
                self._risk_costs[(cost.fill_date, cost.stock_code, cost.side)] = cost
            self._risk_costs_initialized = True
            self._risk_cost_day = kst_today
        costs = tuple(self._risk_costs.values())
        active = active_records if active_records is not None else await asyncio.to_thread(
            self._repository.active_intents, "mock", self._runtime.account_ref, self._runtime.run_id)
        if not self._risk_revision_initialized:
            current = await asyncio.to_thread(
                self._risk_repository.load_latest_mock_automation_risk,
                self._runtime.account_ref,
            )
            if current is not None:
                self._reconciliation_revision = current.reconciliation_revision
            self._risk_revision_initialized = True
        self._reconciliation_revision += 1
        snapshot = build_mock_automation_risk_snapshot(
            recovery, events, costs, active,
            execution_run_id=self._runtime.run_id,
            binding_revision=self._binding_revision,
            reconciliation_revision=self._reconciliation_revision,
            observed_at=now,
            broker_query_started_at=query_started_at,
            broker_query_completed_at=now,
            reconnect_count=self._reconnect_count,
            balance_mismatch_count=self._balance_mismatch_count,
            cost_query_complete=cost_query_complete,
        )
        self._balance_mismatch_count = snapshot.balance_mismatch_count
        await asyncio.to_thread(
            self._risk_repository.save_mock_automation_risk_snapshot, snapshot,
        )
        return active, snapshot

    async def _reconcile_execution(self, execution: OrderExecution) -> None:
        await self._owned(lambda: self._reconcile_execution_once(execution))

    @captured_workload('account', 'mock_account_event', actor_per_invocation=True)
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
        self.request_automation_execution_recovery()

    def _aware_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("mock account monitor clock must be timezone-aware")
        return value
