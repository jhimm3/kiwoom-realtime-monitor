from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import fields
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from urllib.parse import urlsplit, urlunsplit

from PySide6.QtCore import QThread, Signal
from websockets.asyncio.client import connect

from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings

from .realtime import (
    MarketIndexTick, OrderExecution, ProgramTradeTick, StockPriceReference, TradeTick,
)
from .realtime_worker import RealtimeTradeWorker
from .remote_client import CentralServerUnavailable, planned_reconnect_remaining
from .validation_client import RealtimeValidationRecorder


logger = logging.getLogger(__name__)


class CentralRealtimeWorker(QThread):
    """개인 중앙 서버가 배포하는 실시간 이벤트를 기존 Qt 신호로 변환한다."""

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
        self._planned_generation = -1
        self._planned_finished_generation = -1
        self._planned_deadline = 0.0
        self._planned_observation_expected = True
        self._reported_gap_generation = -1

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
            self._accept_connection_status(ready.get("connection_status"))
            subscribed: tuple[str, ...] = ()
            subscribed_nxt: tuple[str, ...] = ()
            while not self.isInterruptionRequested():
                self._check_planned_deadline()
                if self._codes != subscribed or self._nxt_codes != subscribed_nxt:
                    await websocket.send(json.dumps({
                        "type": "subscribe", "codes": list(self._codes),
                        "nxt_codes": list(self._nxt_codes),
                    }))
                    subscribed, subscribed_nxt = self._codes, self._nxt_codes
                try:
                    event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=1))
                except TimeoutError:
                    self._check_planned_deadline()
                    if self._validation_recorder is not None:
                        self._validation_recorder.flush_expired()
                    continue
                self._dispatch(event)

    def _dispatch(self, event: dict[str, object]) -> None:
        event_type = str(event.get("type", ""))
        payload = event.get("payload")
        status = event.get("connection_status")
        if (isinstance(status, dict) and type(status.get("generation")) is int
                and status["generation"] < self._planned_generation
                and event_type in {"connection_failed", "connection_opened", "central_ready"}):
            return
        self._accept_connection_status(status)
        if event_type == "connection_status":
            self._accept_connection_status(payload)
        elif event_type == "trade" and isinstance(payload, dict):
            value = _from_payload(TradeTick, payload)
            self._record_central("trade", value, event)
            self.trade_received.emit(value)
        elif event_type == "order_execution" and isinstance(payload, dict):
            value = _from_payload(OrderExecution, payload)
            self._record_central("order_execution", value, event)
            self.order_executed.emit(value)
        elif event_type == "market_state" and isinstance(payload, dict):
            value = _from_payload(MarketIndexTick, payload)
            self._record_central("market_state", value, event)
            self.market_state_received.emit(value)
        elif event_type == "program_trade" and isinstance(payload, dict):
            value = _from_payload(ProgramTradeTick, payload)
            self._record_central("program_trade", value, event)
            self.program_trade_received.emit(value)
        elif event_type == "stock_reference" and isinstance(payload, dict):
            value = _from_payload(StockPriceReference, payload)
            self.stock_reference_received.emit(value)
        elif event_type == "diagnostics" and isinstance(payload, dict):
            self.diagnostics_changed.emit(payload)
        elif event_type == "connection_failed":
            if time.monotonic() < self._planned_deadline:
                self.status_changed.emit("나스 실시간 연결 변경 중 · 기존 순위 유지")
                return
            message = str(event.get("message", "중앙 실시간 연결 오류"))
            self.connection_failed.emit(message)
            if self._fallback_factory is not None:
                # 서버 HTTP/WebSocket 자체는 살아 있어도 서버의 키움 원본 연결이
                # 끊겼다면 앱에는 시세가 오지 않는다. 명시적 장애 통지는 즉시
                # 로컬 전환 대상으로 취급한다.
                self._consecutive_failures = max(2, self._consecutive_failures)
                raise CentralServerUnavailable(message)
        elif event_type == "connection_opened":
            self._planned_deadline = 0.0
            self._planned_finished_generation = self._planned_generation
            codes = tuple(str(code) for code in event.get("codes", []) if code)
            self.connection_opened.emit(codes)
            self.status_changed.emit(f"나스 실시간 체결 구독 중 · {len(codes)}종목")
        elif event_type == "central_ready":
            codes = tuple(str(code) for code in event.get("codes", []) if code)
            # central_ready는 데스크톱↔시놀로지 연결과 구독 요청 접수만
            # 뜻한다. 시놀로지↔키움 구독 성공은 connection_opened로 따로
            # 전달되므로 여기서 정상 실시간 수신으로 표시하지 않는다.
            self.status_changed.emit("나스 실시간 연결 변경 중 · 기존 순위 유지" if time.monotonic() < self._planned_deadline
                                     else f"시놀로지 서버 연결됨 · 키움 실시간 원본 대기 · {len(codes)}종목")
            self.subscription_ready.emit()
            self._consecutive_failures = 0
        elif event_type == "codes_added":
            self.codes_added.emit(tuple(str(code) for code in event.get("codes", []) if code))
        elif event_type == "subscription_ready":
            self.subscription_ready.emit()

    def _accept_connection_status(self, status: object) -> None:
        if not isinstance(status, dict):
            return
        generation = status.get("generation")
        if type(generation) is not int or not 0 <= generation < 2**63 or generation < self._planned_generation:
            return
        remaining = planned_reconnect_remaining(status)
        previous_generation = self._planned_generation
        self._planned_generation = generation
        if remaining is None:
            self._planned_deadline = 0.0
            self._planned_finished_generation = max(generation, self._planned_finished_generation)
            if status.get("outcome") in {"waiting_market", "waiting_subscription"}:
                self.status_changed.emit("나스 실시간 연결 대기 · 거래시간 또는 구독 대기")
            if (status.get("phase") == "READY" and isinstance(status.get("first_trade_at"), str)
                    and generation > self._reported_gap_generation):
                gap = status.get("gap_seconds")
                valid_gap = ((type(gap) is int and 0 <= gap < 2**63)
                             or (type(gap) is float and math.isfinite(gap) and gap >= 0))
                if gap is None or valid_gap:
                    self._reported_gap_generation = generation
                    detail = "이전 체결 관측 없음" if gap is None else f"교체 전후 체결 관측 간격 {gap:.2f}초"
                    self.status_changed.emit(f"나스 실시간 체결 재개 · {detail}")
                    logger.info("NAS 재연결 체결 관측 간격: generation=%s gap_seconds=%s", generation, gap)
            return
        if generation <= self._planned_finished_generation:
            return
        deadline = time.monotonic() + remaining
        self._planned_deadline = deadline if generation > previous_generation else min(self._planned_deadline or deadline, deadline)
        self._planned_observation_expected = status.get("observation_expected") is not False
        self.status_changed.emit("나스 실시간 연결 변경 중 · 기존 순위 유지")

    def _check_planned_deadline(self) -> None:
        if self._planned_deadline and time.monotonic() >= self._planned_deadline:
            self._planned_deadline = 0.0
            self._planned_finished_generation = self._planned_generation
            if self._planned_observation_expected:
                self._dispatch({"type": "connection_failed", "message": "실시간 재연결 대기 시간이 지났습니다."})
            else:
                self.status_changed.emit("나스 실시간 연결 대기 · 거래시간 또는 구독 대기")

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
            "실시간 데이터를 로컬 경로로 임시 수신: %s종목 · %s초",
            len(self._codes), round(self._fallback_probe_seconds),
        )
        self._fallback_worker = worker
        for name in (
            "trade_received", "order_executed", "market_state_received", "program_trade_received",
            "stock_reference_received",
            "connection_failed", "subscription_ready", "connection_opened", "codes_added", "diagnostics_changed",
        ):
            getattr(worker, name).connect(getattr(self, name).emit)
        worker.status_changed.connect(lambda value: self.status_changed.emit(f"로컬 경로로 임시 수신 중 · {value}"))
        self.status_changed.emit("실시간 데이터를 로컬 경로로 임시 수신 중입니다")
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

    def _record_central(
        self, event_type: str, value: object, metadata: dict[str, object] | None = None,
    ) -> None:
        if self._validation_recorder is not None:
            self._validation_recorder.observe_central(event_type, value, metadata=metadata)

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
    values = {str(key): value for key, value in payload.items() if str(key) in allowed}
    scope = values.get("origin_scope")
    if isinstance(scope, dict):
        values["origin_scope"] = AccountScope(
            broker=str(scope.get("broker", "")),
            environment=AccountEnvironment(str(scope.get("environment", ""))),
            account_ref=str(scope.get("account_ref", "")),
        )
    return model(**values)
