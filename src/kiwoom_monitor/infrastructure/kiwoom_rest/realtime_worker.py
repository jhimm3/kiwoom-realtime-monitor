"""별도 스레드에서 키움 주식체결(0B)을 수신한다."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QThread, Signal
from websockets.asyncio.client import connect

from kiwoom_monitor.application.market_session_schedule import realtime_subscription_target

from .realtime_registration import (
    RegistrationProgress, all_atoms, filter_message, plan_registration, trade_sources,
)

from .realtime import (
    OrderExecution, TradeTick, parse_market_index_ticks, parse_order_executions,
    parse_program_trade_ticks, parse_stock_price_references, parse_trade_ticks,
)


WS_BASE_URLS = {
    "mock": "wss://mockapi.kiwoom.com:10000",
    "real": "wss://api.kiwoom.com:10000",
}


logger = logging.getLogger(__name__)
SUBSCRIPTION_ACK_TIMEOUT_SECONDS = 15.0
REALTIME_REG_INTERVAL_SECONDS = 0.25


def market_session(now: datetime, environment: str) -> str | None:
    """한국 장 시간에 맞는 체결 수신 거래소를 반환한다."""
    target = realtime_subscription_target(
        ("_probe",), {"_probe"}, now, environment=environment,
    )
    if target.krx_codes:
        return "KRX"
    if target.nxt_codes:
        return "NXT"
    return None


def korea_now() -> datetime:
    """Windows에 별도 tzdata가 없어도 항상 한국 표준시를 계산한다."""
    return datetime.now(UTC) + timedelta(hours=9)


class RealtimeTradeWorker(QThread):
    """로그인·구독·PING 응답을 처리하고 체결 틱을 Qt 신호로 전달한다."""

    trade_received = Signal(object)
    order_executed = Signal(object)
    market_state_received = Signal(object)
    program_trade_received = Signal(object)
    stock_reference_received = Signal(object)
    status_changed = Signal(str)
    connection_failed = Signal(str)
    subscription_ready = Signal()
    connection_opened = Signal(object)
    codes_added = Signal(object)
    sources_activated = Signal(object)
    diagnostics_changed = Signal(object)

    def __init__(self, token_provider: Callable[[], str], environment: str, codes: tuple[str, ...], now_provider: Callable[[], datetime] | None = None, nxt_codes: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._token_provider = token_provider
        self._environment = environment
        self._codes = tuple(dict.fromkeys(code for code in codes if code))
        self._nxt_codes = tuple(dict.fromkeys(code for code in nxt_codes if code))
        self._subscription_intent = (self._codes, self._nxt_codes)
        self._now_provider = now_provider or korea_now
        self._abnormal_disconnects = 0
        self._reconnects = 0
        self._connected_once = False
        self._reconnect_pending = False
        self._last_disconnect_reason = ""

    def update_codes(self, codes: tuple[str, ...], nxt_codes: tuple[str, ...] = ()) -> None:
        """소켓을 끊지 않고 다음 수신 반복에서 구독 목록만 교체한다."""
        self._codes = tuple(dict.fromkeys(code for code in codes if code))
        self._nxt_codes = tuple(dict.fromkeys(code for code in nxt_codes if code))
        self._subscription_intent = (self._codes, self._nxt_codes)

    def run(self) -> None:
        while not self.isInterruptionRequested():
            session = market_session(self._now_provider(), self._environment)
            if session is None:
                self.status_changed.emit("실시간 체결 대기: 현재는 KRX/NXT 거래 시간이 아닙니다")
                self._wait_or_stop(15)
                continue
            try:
                asyncio.run(self._receive(session))
                if not self.isInterruptionRequested():
                    if market_session(self._now_provider(), self._environment) == session:
                        self._record_abnormal_disconnect("WebSocket 연결이 예기치 않게 종료됨")
                    self.status_changed.emit("실시간 연결이 종료되어 다시 연결합니다…")
            except Exception as error:
                self._record_abnormal_disconnect(str(error))
                self.connection_failed.emit(str(error))
            if not self.isInterruptionRequested():
                self._wait_or_stop(3)

    def _wait_or_stop(self, seconds: float) -> None:
        """긴 재시도 대기 중에도 종료 요청을 즉시 반영한다."""
        deadline = time.monotonic() + seconds
        while not self.isInterruptionRequested() and time.monotonic() < deadline:
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    async def _receive(self, session: str) -> None:
        if not self._codes:
            return
        token = await asyncio.to_thread(self._token_provider)
        uri = f"{WS_BASE_URLS[self._environment]}/api/dostk/websocket"
        async with connect(uri, open_timeout=15, ping_interval=None) as websocket:
            await websocket.send(json.dumps({"trnm": "LOGIN", "token": token}))
            login = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
            if login.get("return_code") not in (None, 0, "0"):
                raise RuntimeError(f"WebSocket 로그인 실패: {login.get('return_msg', '')}")
            groups, approved, subscribed = {}, set(), ()
            progress = None
            batch_codes = ()
            recv_task = asyncio.create_task(websocket.recv())
            send_task = None
            last_sent = float('-inf')
            deadline = 0.0
            connection_approved = False
            try:
                while not self.isInterruptionRequested():
                    # One tuple snapshot: both lists describe the same GUI update.
                    codes, nxt = self._subscription_intent
                    target = realtime_subscription_target(
                        codes, set(nxt), self._now_provider(), environment=self._environment)
                    if not target.active_codes:
                        return
                    session = "KRX" if target.krx_codes else "NXT"
                    desired = self._subscription_groups(
                        session, target.active_codes, target.nxt_codes, self._environment)
                    wanted = all_atoms(desired)
                    if progress is None:
                        plan = plan_registration(desired, groups)
                        if plan.commands:
                            progress = RegistrationProgress(plan)
                            batch_codes = target.active_codes
                            approved.intersection_update(plan.retained)
                    # Admission follows latest intent even while an older batch finishes.
                    approved.intersection_update(wanted)
                    allowed = approved & wanted
                    now = time.monotonic()
                    if progress is not None and progress.started and now >= deadline:
                        raise RuntimeError("WebSocket 구독 승인 시간 초과")
                    if progress is not None and not progress.started and send_task is None:
                        if now >= last_sent + REALTIME_REG_INTERVAL_SECONDS:
                            command = progress.start()
                            deadline = now + SUBSCRIPTION_ACK_TIMEOUT_SECONDS
                            send_task = asyncio.create_task(websocket.send(json.dumps(command.packet())))
                    tasks = {recv_task}
                    if send_task is not None:
                        tasks.add(send_task)
                    wait = 0.1
                    if progress is not None:
                        due = deadline if progress.started else last_sent + REALTIME_REG_INTERVAL_SECONDS
                        wait = min(wait, max(0.0, due - time.monotonic()))
                    done, _ = await asyncio.wait(tasks, timeout=wait, return_when=asyncio.FIRST_COMPLETED)
                    committed = False
                    if send_task is not None and send_task in done:
                        send_task.result()
                        send_task = None
                        last_sent = time.monotonic()
                        committed = progress.mark_sent()
                    if recv_task in done:
                        message = json.loads(recv_task.result())
                        recv_task = asyncio.create_task(websocket.recv())
                        kind = str(message.get("trnm", "")).upper()
                        if kind == "PING":
                            await websocket.send(json.dumps(message))
                        elif kind in {"REG", "REMOVE"}:
                            if progress is None:
                                raise RuntimeError("realtime_ack_without_command")
                            try:
                                committed = progress.accept_ack(message) or committed
                            except RuntimeError as error:
                                if str(error) == 'realtime_subscription_rejected':
                                    raise RuntimeError(f"WebSocket 구독 실패: {message.get('return_msg', '')}") from error
                                raise
                        else:
                            value = filter_message(message, allowed)
                            for tick in parse_trade_ticks(value):
                                self.trade_received.emit(tick)
                            for execution in parse_order_executions(value):
                                self.order_executed.emit(execution)
                            for tick in parse_market_index_ticks(value):
                                self.market_state_received.emit(tick)
                            for tick in parse_program_trade_ticks(value):
                                self.program_trade_received.emit(tick)
                            for reference in parse_stock_price_references(value):
                                self.stock_reference_received.emit(reference)
                    if committed and progress.done:
                        groups = progress.plan.groups
                        current_codes, current_nxt = self._subscription_intent
                        current_target = realtime_subscription_target(current_codes,set(current_nxt),
                            self._now_provider(),environment=self._environment)
                        current_session = 'KRX' if current_target.krx_codes else 'NXT'
                        wanted = all_atoms(self._subscription_groups(current_session,current_target.active_codes,
                            current_target.nxt_codes,self._environment))
                        progress = None
                        # Keep only old authority when an obsolete batch finishes.
                        if all_atoms(groups) != wanted:
                            continue
                        resets = trade_sources(groups) - {
                            (item.removesuffix('_NX').removesuffix('_AL'),
                             'NXT' if item.endswith('_NX') else 'SOR' if item.endswith('_AL') else 'KRX')
                            for key, item in approved if key == '0B' and item}
                        approved = all_atoms(groups)
                        initial = not connection_approved
                        if initial:
                            if self._connected_once and self._reconnect_pending:
                                self._reconnects += 1
                            self._connected_once = connection_approved = True
                            self._reconnect_pending = False
                            self._emit_diagnostics()
                            self.connection_opened.emit(batch_codes)
                        else:
                            added = tuple(code for code in batch_codes if code not in subscribed)
                            if added:
                                self.codes_added.emit(added)
                        if resets:
                            self.sources_activated.emit(tuple(sorted(resets)))
                        subscribed = batch_codes
                        self.status_changed.emit(f"실시간 체결 구독 중 · {session} · {len(subscribed)}종목")
                        if initial:
                            self.subscription_ready.emit()
            finally:
                tasks = [task for task in (recv_task, send_task) if task is not None]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    def _record_abnormal_disconnect(self, reason: str) -> None:
        self._abnormal_disconnects += 1
        self._reconnect_pending = True
        self._last_disconnect_reason = reason
        self._emit_diagnostics()

    def _emit_diagnostics(self) -> None:
        self.diagnostics_changed.emit({
            "abnormal_disconnects": self._abnormal_disconnects,
            "reconnects": self._reconnects,
            "last_disconnect_reason": self._last_disconnect_reason,
            "updated_at": self._now_provider().isoformat(timespec="seconds"),
        })

    @staticmethod
    def _subscription_groups(session, codes, nxt_codes=(), environment="real"):
        items = (tuple(f"{code}_NX" for code in codes) if session == "NXT"
                 else codes + tuple(f"{code}_NX" for code in nxt_codes if code in codes))
        program_items = tuple(f"{code}_AL" for code in codes) if environment == "real" else codes
        return {"1": [
            {"item": list(items), "type": ["0B"]},
            {"item": list(codes), "type": ["0g"]},
            {"item": list(program_items), "type": ["0w"]},
            {"item": [""], "type": ["00"]},
            {"item": ["001", "101"], "type": ["0J", "0U"]},
        ]}

    @staticmethod
    async def _send_subscription(websocket, session, codes, nxt_codes=(), environment="real"):
        # Packet-only entry for existing callers/tests. Live approval belongs to _receive.
        plan = plan_registration(RealtimeTradeWorker._subscription_groups(
            session, codes, nxt_codes, environment))
        for command in plan.commands:
            await websocket.send(json.dumps(command.packet()))

    def stop(self, timeout_ms: int = 3000) -> bool:
        self.requestInterruption()
        return self.wait(timeout_ms)
