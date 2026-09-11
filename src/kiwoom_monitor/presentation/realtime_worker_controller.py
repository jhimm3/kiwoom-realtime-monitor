"""실시간 체결 QThread의 생성, 신호 연결, 구독 변경과 종료를 관리한다."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker import RealtimeTradeWorker


class RealtimeWorkerController(QObject):
    trade_received = Signal(object)
    order_executed = Signal(object)
    market_state_received = Signal(object)
    program_trade_received = Signal(object)
    diagnostics_changed = Signal(object)
    status_changed = Signal(str)
    connection_failed = Signal(str)
    connection_opened = Signal(object)
    codes_added = Signal(object)
    subscription_ready = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        worker_factory: Callable[[tuple[str, ...]], RealtimeTradeWorker] | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker_factory = worker_factory
        self._worker: RealtimeTradeWorker | None = None

    @property
    def worker(self) -> RealtimeTradeWorker | None:
        return self._worker

    @property
    def available(self) -> bool:
        return self._worker_factory is not None

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def set_factory(
        self,
        worker_factory: Callable[[tuple[str, ...]], RealtimeTradeWorker] | None,
    ) -> None:
        self._worker_factory = worker_factory

    def start(
        self,
        active_codes: tuple[str, ...],
        nxt_codes: tuple[str, ...],
        *,
        followup_codes: tuple[str, ...],
    ) -> bool:
        if self.is_running or self._worker_factory is None:
            return False
        worker = self._worker_factory(active_codes)
        worker.update_codes(active_codes, nxt_codes)
        worker.setParent(self)
        worker.trade_received.connect(self.trade_received.emit)
        worker.order_executed.connect(self.order_executed.emit)
        worker.market_state_received.connect(self.market_state_received.emit)
        worker.program_trade_received.connect(self.program_trade_received.emit)
        worker.diagnostics_changed.connect(self.diagnostics_changed.emit)
        worker.status_changed.connect(self.status_changed.emit)
        worker.connection_failed.connect(self.connection_failed.emit)
        worker.connection_opened.connect(self.connection_opened.emit)
        worker.codes_added.connect(self.codes_added.emit)
        worker.subscription_ready.connect(
            lambda: self.subscription_ready.emit(followup_codes)
        )
        finished_signal = getattr(worker, "finished", None)
        if finished_signal is not None:
            finished_signal.connect(self._release_finished_worker)
        self._worker = worker
        worker.start()
        return True

    def update_codes(
        self, active_codes: tuple[str, ...], nxt_codes: tuple[str, ...]
    ) -> bool:
        if self._worker is None:
            return False
        self._worker.update_codes(active_codes, nxt_codes)
        return True

    def stop(self) -> bool:
        if self._worker is None:
            return True
        return self._worker.stop()

    def _release_finished_worker(self) -> None:
        worker = self.sender()
        if worker is None or worker.isRunning():
            return
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()
