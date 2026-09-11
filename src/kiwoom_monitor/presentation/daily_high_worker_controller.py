"""기간 신고가 일봉 QThread의 생성과 신호 수명을 관리한다."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from kiwoom_monitor.infrastructure.kiwoom_rest.daily_high_worker import DailyHighWorker


class DailyHighWorkerController(QObject):
    received = Signal(str, object)
    failed = Signal(str)
    finished = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        worker_factory: Callable[[tuple[str, ...]], DailyHighWorker] | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker_factory = worker_factory
        self._worker: DailyHighWorker | None = None

    @property
    def worker(self) -> DailyHighWorker | None:
        return self._worker

    @property
    def available(self) -> bool:
        return self._worker_factory is not None

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def set_factory(
        self,
        worker_factory: Callable[[tuple[str, ...]], DailyHighWorker] | None,
    ) -> None:
        self._worker_factory = worker_factory

    def start(
        self, codes: tuple[str, ...], *, followup_codes: tuple[str, ...]
    ) -> bool:
        if self.is_running:
            return True
        if self._worker_factory is None:
            return False
        worker = self._worker_factory(codes)
        worker.setParent(self)
        if not isinstance(worker, DailyHighWorker):
            self.failed.emit(
                "기간 신고가 작업 구성이 올바르지 않습니다. 프로그램을 다시 실행해 주세요."
            )
            worker.deleteLater()
            return False
        worker.received.connect(self.received.emit)
        worker.failed.connect(self.failed.emit)
        worker.finished.connect(lambda: self.finished.emit(followup_codes))
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
