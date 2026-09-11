"""분봉 보완 QThread의 생성과 신호 수명을 한 곳에서 관리한다."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from kiwoom_monitor.infrastructure.kiwoom_rest.minute_history_worker import (
    MinuteHistoryWorker,
)


class MinuteHistoryWorkerController(QObject):
    history_received = Signal(str, object)
    status_changed = Signal(str)
    failed = Signal(str)
    finished = Signal(object, bool)

    def __init__(
        self,
        parent: QObject | None = None,
        worker_factory: Callable[[tuple[str, ...]], MinuteHistoryWorker] | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker_factory = worker_factory
        self._worker: MinuteHistoryWorker | None = None

    @property
    def worker(self) -> MinuteHistoryWorker | None:
        return self._worker

    @property
    def available(self) -> bool:
        return self._worker_factory is not None

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def set_factory(
        self,
        worker_factory: Callable[[tuple[str, ...]], MinuteHistoryWorker] | None,
    ) -> None:
        self._worker_factory = worker_factory

    def start(
        self,
        codes: tuple[str, ...],
        *,
        followup_codes: tuple[str, ...],
        forced: bool,
    ) -> bool:
        if self.is_running:
            return True
        if self._worker_factory is None:
            return False
        worker = self._worker_factory(codes)
        worker.setParent(self)
        if not isinstance(worker, MinuteHistoryWorker):
            self.failed.emit(
                "분봉 보강 작업 구성이 올바르지 않습니다. 프로그램을 다시 실행해 주세요."
            )
            worker.deleteLater()
            return False
        worker.history_received.connect(self.history_received.emit)
        worker.status_changed.connect(self.status_changed.emit)
        worker.failed.connect(self.failed.emit)
        worker.finished.connect(
            lambda: self.finished.emit(followup_codes, forced)
        )
        worker.finished.connect(self._release_finished_worker)
        self._worker = worker
        worker.start()
        return True

    def _release_finished_worker(self) -> None:
        worker = self.sender()
        if worker is None or worker.isRunning():
            return
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()
