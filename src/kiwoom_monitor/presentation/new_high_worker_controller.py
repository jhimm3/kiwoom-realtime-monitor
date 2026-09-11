"""신고가 목록 QThread의 생성과 신호 수명을 관리한다."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from kiwoom_monitor.infrastructure.kiwoom_rest.new_high_worker import (
    NewHighLoader,
    NewHighWorker,
)


class NewHighWorkerController(QObject):
    completed = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        worker_factory: Callable[[NewHighLoader], NewHighWorker] = NewHighWorker,
    ) -> None:
        super().__init__(parent)
        self._worker_factory = worker_factory
        self._worker: NewHighWorker | None = None

    @property
    def worker(self) -> NewHighWorker | None:
        return self._worker

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def start(self, loader: NewHighLoader) -> bool:
        if self.is_running:
            return True
        worker = self._worker_factory(loader)
        worker.setParent(self)
        if not isinstance(worker, NewHighWorker):
            self.failed.emit(
                "신고가 목록 작업 구성이 올바르지 않습니다. 프로그램을 다시 실행해 주세요."
            )
            worker.deleteLater()
            return False
        worker.completed.connect(self.completed.emit)
        worker.failed.connect(self.failed.emit)
        worker.finished.connect(self.finished.emit)
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
