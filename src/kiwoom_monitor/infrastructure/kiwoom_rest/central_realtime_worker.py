from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import fields
from urllib.parse import urlsplit, urlunsplit

from PySide6.QtCore import QThread, Signal
from websockets.asyncio.client import connect

from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings

from .realtime import MarketIndexTick, OrderExecution, ProgramTradeTick, TradeTick
from .realtime_worker import RealtimeTradeWorker
from .remote_client import CentralServerUnavailable
from .validation_client import RealtimeValidationRecorder


logger = logging.getLogger(__name__)


class CentralRealtimeWorker(QThread):
    """개인 중앙 서버가 배포하는 실시간 이벤트를 기존 Qt 신호로 변환한다."""

    trade_received = Signal(object)
    order_executed = Signal(object)
    market_state_received = Signal(object)
    program_trade_received = Signal(object)
    status_changed = Signal(str)
    connection_failed = Signal(str)
    subscription_ready = Signal()
    connection_opened = Signal(object)
    codes_added = Signal(object)
    diagnostics_changed = Signal(object)

    def __init__(self, source: DataSourceSettings, codes: tuple[str, ...], *,
                 fallback_factory: Callable[[tuple[str, ...], tuple[str, ...]], RealtimeTradeWorker] | None = None,
                 validation_factory: Callable[[tuple[str, ...], tuple[str, ...]], RealtimeTradeWorker] | None = None,
                 validation_recorder: RealtimeValidationRecorder | None = None,
                 fallback_probe_seconds: float = 60.0) -> None:
        super().__init__()
        self._source = source
        self._codes = tuple(dict.fromkeys(code for code in codes if code))
        self._nxt_codes: tuple[str, ...] = ()
        self._fallback_factory = fallback_factory
        self._validation_factory = validation_factory
        self._validation_recorder = validation_recorder
        self._fallback_probe_seconds = max(10.0, float(fallback_probe_seconds))
        self._fallback_worker: RealtimeTradeWorker | None = None
        self._validation_worker: RealtimeTradeWorker | None = None
        self._consecutive_failures = 0

    def update_codes(self, codes: tuple[str, ...], nxt_codes: tuple[str, ...] = ()) -> None:
        self._codes = tuple(dict.fromkeys(code for code in codes if code))
        self._nxt_codes = tuple(dict.fromkeys(code for code in nxt_codes if code))
        if self._fallback_worker is not None:
            self._fallback_worker.update_codes(self._codes, self._nxt_codes)
        if self._validation_worker is not None:
            self._validation_worker.update_codes(self._codes, self._nxt_codes)

    def run(self) -> None:
        while not self.isInterruptionRequested():
            self._ensure_validation_worker()
            try:
                asyncio.run(self._receive())
                self._consecutive_failures = 0
            except Exception as error:
                self._consecutive_failures += 1
                self.connection_failed.emit(str(error))
            if self._fallback_factory is not None and self._consecutive_failures >= 3 \
                    and not self.isInterruptionRequested():
                self._run_local_fallback()
                self._consecutive_failures = 0
            if not self.isInterruptionRequested():
                self._wait_or_stop(3)

    async def _receive(self) -> None:
        async with connect(
            self._websocket_url(),
            additional_headers={"Authorization": f"Bearer {self._source.access_token}"},
            open_timeout=15, ping_interval=20,
        ) as websocket:
            ready = json.loads(await asyncio.wait_for(websocket.recv(), timeout=15))
            if ready.get("type") != "ready":
                raise RuntimeError("중앙 실시간 서버의 준비 응답이 올바르지 않습니다.")
            subscribed: tuple[str, ...] = ()
            subscribed_nxt: tuple[str, ...] = ()
            while not self.isInterruptionRequested():
                if self._codes != subscribed or self._nxt_codes != subscribed_nxt:
                    await websocket.send(json.dumps({
                        "type": "subscribe", "codes": list(self._codes),
                        "nxt_codes": list(self._nxt_codes),
                    }))
                    subscribed, subscribed_nxt = self._codes, self._nxt_codes
                try:
                    event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=1))
                except TimeoutError:
                    continue
                self._dispatch(event)

    def _dispatch(self, event: dict[str, object]) -> None:
        event_type = str(event.get("type", ""))
        payload = event.get("payload")
        if event_type == "trade" and isinstance(payload, dict):
            value = _from_payload(TradeTick, payload)
            self._record_central("trade", value)
            self.trade_received.emit(value)
        elif event_type == "order_execution" and isinstance(payload, dict):
            value = _from_payload(OrderExecution, payload)
            self._record_central("order_execution", value)
            self.order_executed.emit(value)
        elif event_type == "market_state" and isinstance(payload, dict):
            value = _from_payload(MarketIndexTick, payload)
            self._record_central("market_state", value)
            self.market_state_received.emit(value)
        elif event_type == "program_trade" and isinstance(payload, dict):
            value = _from_payload(ProgramTradeTick, payload)
            self._record_central("program_trade", value)
            self.program_trade_received.emit(value)
        elif event_type == "diagnostics" and isinstance(payload, dict):
            self.diagnostics_changed.emit(payload)
        elif event_type == "connection_failed":
            message = str(event.get("message", "중앙 실시간 연결 오류"))
            self.connection_failed.emit(message)
            if self._fallback_factory is not None:
                # 서버 HTTP/WebSocket 자체는 살아 있어도 서버의 키움 원본 연결이
                # 끊겼다면 앱에는 시세가 오지 않는다. 명시적 장애 통지는 즉시
                # 로컬 전환 대상으로 취급한다.
                self._consecutive_failures = max(2, self._consecutive_failures)
                raise CentralServerUnavailable(message)
        elif event_type == "connection_opened":
            codes = tuple(str(code) for code in event.get("codes", []) if code)
            self.connection_opened.emit(codes)
            self.status_changed.emit(f"중앙 실시간 체결 구독 중 · {len(codes)}종목")
        elif event_type == "central_ready":
            codes = tuple(str(code) for code in event.get("codes", []) if code)
            # central_ready는 데스크톱↔시놀로지 연결과 구독 요청 접수만
            # 뜻한다. 시놀로지↔키움 구독 성공은 connection_opened로 따로
            # 전달되므로 여기서 정상 실시간 수신으로 표시하지 않는다.
            self.status_changed.emit(f"시놀로지 서버 연결됨 · 키움 실시간 원본 대기 · {len(codes)}종목")
            self.subscription_ready.emit()
            self._consecutive_failures = 0
        elif event_type == "codes_added":
            self.codes_added.emit(tuple(str(code) for code in event.get("codes", []) if code))
        elif event_type == "subscription_ready":
            self.subscription_ready.emit()

    def _websocket_url(self) -> str:
        parsed = urlsplit(self._source.server_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunsplit((scheme, parsed.netloc, "/api/v1/realtime", "", ""))

    def _wait_or_stop(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not self.isInterruptionRequested() and time.monotonic() < deadline:
            time.sleep(min(0.1, deadline - time.monotonic()))

    def stop(self, timeout_ms: int = 3000) -> bool:
        self.requestInterruption()
        if self._fallback_worker is not None:
            self._fallback_worker.stop(timeout_ms)
        if self._validation_worker is not None:
            self._validation_worker.stop(timeout_ms)
        return self.wait(timeout_ms)

    def _run_local_fallback(self) -> None:
        self._stop_validation_worker()
        worker = self._fallback_factory(self._codes, self._nxt_codes) if self._fallback_factory else None
        if worker is None:
            return
        logger.warning(
            "시놀로지 실시간 연결 실패로 로컬 키움 실시간 전환: %s종목 · %s초",
            len(self._codes), round(self._fallback_probe_seconds),
        )
        self._fallback_worker = worker
        for name in (
            "trade_received", "order_executed", "market_state_received", "program_trade_received",
            "connection_failed", "subscription_ready", "connection_opened", "codes_added", "diagnostics_changed",
        ):
            getattr(worker, name).connect(getattr(self, name).emit)
        worker.status_changed.connect(lambda value: self.status_changed.emit(f"시놀로지 장애 · 로컬 전환 · {value}"))
        self.status_changed.emit("시놀로지 연결 실패 · 로컬 키움 실시간으로 전환합니다")
        worker.start()
        deadline = time.monotonic() + self._fallback_probe_seconds
        while not self.isInterruptionRequested() and time.monotonic() < deadline and worker.isRunning():
            worker.update_codes(self._codes, self._nxt_codes)
            time.sleep(0.2)
        worker.stop(3000)
        self._fallback_worker = None
        if not self.isInterruptionRequested():
            logger.info("로컬 실시간 대체 수신 종료 · 시놀로지 연결 복구 확인")
            self.status_changed.emit("시놀로지 연결 복구 여부를 다시 확인합니다")

    def _record_central(self, event_type: str, value: object) -> None:
        if self._validation_recorder is not None:
            self._validation_recorder.observe_central(event_type, value)

    def _ensure_validation_worker(self) -> None:
        if self._validation_worker is not None or self._validation_factory is None:
            return
        worker = self._validation_factory(self._codes, self._nxt_codes)
        self._validation_worker = worker
        if self._validation_recorder is not None:
            worker.trade_received.connect(lambda value: self._validation_recorder.observe_local("trade", value))
            worker.order_executed.connect(lambda value: self._validation_recorder.observe_local("order_execution", value))
            worker.market_state_received.connect(lambda value: self._validation_recorder.observe_local("market_state", value))
            worker.program_trade_received.connect(lambda value: self._validation_recorder.observe_local("program_trade", value))
        worker.start()

    def _stop_validation_worker(self) -> None:
        worker, self._validation_worker = self._validation_worker, None
        if worker is not None:
            worker.stop(3000)


def _from_payload(model: type, payload: dict[object, object]) -> object:
    allowed = {field.name for field in fields(model)}
    return model(**{str(key): value for key, value in payload.items() if str(key) in allowed})
