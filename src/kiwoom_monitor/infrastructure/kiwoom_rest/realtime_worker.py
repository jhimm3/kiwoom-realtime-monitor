"""별도 스레드에서 키움 주식체결(0B)을 수신한다."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, time as clock_time, timedelta

from PySide6.QtCore import QThread, Signal
from websockets.asyncio.client import connect

from .realtime import TradeTick, parse_trade_ticks


WS_BASE_URLS = {
    "mock": "wss://mockapi.kiwoom.com:10000",
    "real": "wss://api.kiwoom.com:10000",
}


def market_session(now: datetime, environment: str) -> str | None:
    """한국 장 시간에 맞는 체결 수신 거래소를 반환한다."""
    if now.weekday() >= 5:
        return None
    current = now.timetz().replace(tzinfo=None)
    if clock_time(9, 0) <= current < clock_time(15, 30):
        return "KRX"
    if environment == "real" and (clock_time(8, 0) <= current < clock_time(9, 0) or clock_time(15, 30) <= current < clock_time(20, 0)):
        return "NXT"
    return None


def korea_now() -> datetime:
    """Windows에 별도 tzdata가 없어도 항상 한국 표준시를 계산한다."""
    return datetime.now(UTC) + timedelta(hours=9)


class RealtimeTradeWorker(QThread):
    """로그인·구독·PING 응답을 처리하고 체결 틱을 Qt 신호로 전달한다."""

    trade_received = Signal(object)
    status_changed = Signal(str)
    connection_failed = Signal(str)
    subscription_ready = Signal()

    def __init__(self, token_provider: Callable[[], str], environment: str, codes: tuple[str, ...], now_provider: Callable[[], datetime] | None = None) -> None:
        super().__init__()
        self._token_provider = token_provider
        self._environment = environment
        self._codes = tuple(dict.fromkeys(code for code in codes if code))
        self._now_provider = now_provider or korea_now

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
                    self.status_changed.emit("실시간 연결이 종료되어 다시 연결합니다…")
            except Exception as error:
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
        codes = tuple(f"{code}_NX" for code in self._codes) if session == "NXT" else self._codes
        uri = f"{WS_BASE_URLS[self._environment]}/api/dostk/websocket"
        async with connect(uri, open_timeout=15, ping_interval=None) as websocket:
            await websocket.send(json.dumps({"trnm": "LOGIN", "token": token}))
            login = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
            if login.get("return_code") not in (None, 0, "0"):
                raise RuntimeError(f"WebSocket 로그인 실패: {login.get('return_msg', '')}")
            await websocket.send(
                json.dumps(
                    {
                        "trnm": "REG",
                        "grp_no": "1",
                        "refresh": "1",
                        "data": [{"item": list(codes), "type": ["0B"]}],
                    }
                )
            )
            self.status_changed.emit(f"실시간 체결 구독 중 · {session} · {len(codes)}종목")
            self.subscription_ready.emit()
            while not self.isInterruptionRequested():
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=1)
                except TimeoutError:
                    if market_session(self._now_provider(), self._environment) != session:
                        return
                    continue
                message = json.loads(raw)
                if str(message.get("trnm", "")).upper() == "PING":
                    await websocket.send(json.dumps(message))
                    continue
                for tick in parse_trade_ticks(message):
                    self.trade_received.emit(tick)

    def stop(self, timeout_ms: int = 3000) -> bool:
        self.requestInterruption()
        return self.wait(timeout_ms)
