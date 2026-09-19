"""별도 스레드에서 키움 주식체결(0B)을 수신한다."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QThread, Signal
from websockets.asyncio.client import connect

from kiwoom_monitor.application.market_session_schedule import realtime_subscription_target

from .realtime import (
    OrderExecution, TradeTick, parse_market_index_ticks, parse_order_executions,
    parse_program_trade_ticks, parse_stock_price_references, parse_trade_ticks,
)


WS_BASE_URLS = {
    "mock": "wss://mockapi.kiwoom.com:10000",
    "real": "wss://api.kiwoom.com:10000",
}


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
    diagnostics_changed = Signal(object)

    def __init__(self, token_provider: Callable[[], str], environment: str, codes: tuple[str, ...], now_provider: Callable[[], datetime] | None = None, nxt_codes: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._token_provider = token_provider
        self._environment = environment
        self._codes = tuple(dict.fromkeys(code for code in codes if code))
        self._nxt_codes = tuple(dict.fromkeys(code for code in nxt_codes if code))
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
        initial_codes = self._codes
        initial_nxt_codes = self._nxt_codes
        if not initial_codes:
            return
        token = await asyncio.to_thread(self._token_provider)
        uri = f"{WS_BASE_URLS[self._environment]}/api/dostk/websocket"
        async with connect(uri, open_timeout=15, ping_interval=None) as websocket:
            await websocket.send(json.dumps({"trnm": "LOGIN", "token": token}))
            login = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
            if login.get("return_code") not in (None, 0, "0"):
                raise RuntimeError(f"WebSocket 로그인 실패: {login.get('return_msg', '')}")
            subscribed_codes = initial_codes
            subscribed_nxt_codes = initial_nxt_codes
            target = realtime_subscription_target(
                subscribed_codes, set(subscribed_nxt_codes), self._now_provider(),
                environment=self._environment,
            )
            policy_signature = target.signature
            await self._send_subscription(
                websocket, session, target.active_codes, target.nxt_codes, self._environment,
            )
            if self._connected_once and self._reconnect_pending:
                self._reconnects += 1
            self._connected_once = True
            self._reconnect_pending = False
            self._emit_diagnostics()
            self.connection_opened.emit(subscribed_codes)
            self.status_changed.emit(f"실시간 체결 구독 중 · {session} · {len(subscribed_codes)}종목")
            self.subscription_ready.emit()
            while not self.isInterruptionRequested():
                desired_codes = self._codes
                desired_nxt_codes = self._nxt_codes
                target = realtime_subscription_target(
                    desired_codes, set(desired_nxt_codes), self._now_provider(),
                    environment=self._environment,
                )
                if (
                    desired_codes != subscribed_codes
                    or desired_nxt_codes != subscribed_nxt_codes
                    or target.signature != policy_signature
                ):
                    if not desired_codes:
                        return
                    added_codes = tuple(code for code in desired_codes if code not in subscribed_codes)
                    if not target.active_codes:
                        return
                    session = "KRX" if target.krx_codes else "NXT"
                    await self._send_subscription(
                        websocket, session, target.active_codes, target.nxt_codes, self._environment,
                    )
                    subscribed_codes = desired_codes
                    subscribed_nxt_codes = desired_nxt_codes
                    policy_signature = target.signature
                    if added_codes:
                        self.codes_added.emit(added_codes)
                    self.status_changed.emit(f"실시간 체결 구독 변경 · {session} · {len(subscribed_codes)}종목")
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=1)
                except TimeoutError:
                    if not realtime_subscription_target(
                        self._codes, set(self._nxt_codes), self._now_provider(),
                        environment=self._environment,
                    ).active_codes:
                        return
                    continue
                message = json.loads(raw)
                if str(message.get("trnm", "")).upper() == "PING":
                    await websocket.send(json.dumps(message))
                    continue
                for tick in parse_trade_ticks(message):
                    self.trade_received.emit(tick)
                for execution in parse_order_executions(message):
                    self.order_executed.emit(execution)
                for market_tick in parse_market_index_ticks(message):
                    self.market_state_received.emit(market_tick)
                for program_tick in parse_program_trade_ticks(message):
                    self.program_trade_received.emit(program_tick)
                for reference in parse_stock_price_references(message):
                    self.stock_reference_received.emit(reference)

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
    async def _send_subscription(websocket: object, session: str, codes: tuple[str, ...], nxt_codes: tuple[str, ...] = (), environment: str = "real") -> None:
        items = (
            tuple(f"{code}_NX" for code in codes)
            if session == "NXT"
            else codes + tuple(f"{code}_NX" for code in nxt_codes if code in codes)
        )
        program_items = tuple(f"{code}_AL" for code in codes) if environment == "real" else codes
        # 모의투자는 KRX만 지원한다. 실전은 SOR(_AL) 누적 프로그램매매를
        # 구독해 KRX/NXT를 따로 받은 뒤 서로 덮어쓰는 일을 피한다.
        await websocket.send(
            json.dumps(
                {
                    "trnm": "REG",
                    "grp_no": "1",
                    # 전체 희망 종목을 매번 다시 보내므로 기존 그룹은 교체한다.
                    # 유지(1)하면 순위 교체 때 빠진 종목이 누적되어 200개 한도를 넘는다.
                    "refresh": "0",
                    "data": [
                        {"item": list(items), "type": ["0B"]},
                        {"item": list(codes), "type": ["0g"]},
                        {"item": list(program_items), "type": ["0w"]},
                        {"item": [""], "type": ["00"]},
                        {"item": ["001", "101"], "type": ["0J", "0U"]},
                    ],
                }
            )
        )

    def stop(self, timeout_ms: int = 3000) -> bool:
        self.requestInterruption()
        return self.wait(timeout_ms)
