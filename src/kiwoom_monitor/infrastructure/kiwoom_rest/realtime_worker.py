"""별도 스레드에서 키움 주식체결(0B)을 수신한다."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal
from websockets.asyncio.client import connect

from .realtime import TradeTick, parse_trade_ticks


WS_BASE_URLS = {
    "mock": "wss://mockapi.kiwoom.com:10000",
    "real": "wss://api.kiwoom.com:10000",
}


class RealtimeTradeWorker(QThread):
    """로그인·구독·PING 응답을 처리하고 체결 틱을 Qt 신호로 전달한다."""

    trade_received = Signal(object)
    status_changed = Signal(str)
    connection_failed = Signal(str)

    def __init__(self, token_provider: Callable[[], str], environment: str, codes: tuple[str, ...]) -> None:
        super().__init__()
        self._token_provider = token_provider
        self._environment = environment
        self._codes = tuple(dict.fromkeys(code for code in codes if code))

    def run(self) -> None:
        while not self.isInterruptionRequested():
            try:
                asyncio.run(self._receive())
                if not self.isInterruptionRequested():
                    self.status_changed.emit("실시간 연결이 종료되어 다시 연결합니다…")
            except Exception as error:
                self.connection_failed.emit(str(error))
            if not self.isInterruptionRequested():
                time.sleep(3)

    async def _receive(self) -> None:
        if not self._codes:
            return
        token = await asyncio.to_thread(self._token_provider)
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
                        "data": [{"item": list(self._codes), "type": ["0B"]}],
                    }
                )
            )
            self.status_changed.emit(f"실시간 체결 구독 중 · {len(self._codes)}종목")
            while not self.isInterruptionRequested():
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=1)
                except TimeoutError:
                    continue
                message = json.loads(raw)
                if str(message.get("trnm", "")).upper() == "PING":
                    await websocket.send(json.dumps(message))
                    continue
                for tick in parse_trade_ticks(message):
                    self.trade_received.emit(tick)

    def stop(self) -> None:
        self.requestInterruption()
        self.wait()
