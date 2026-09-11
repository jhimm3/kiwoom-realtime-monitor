"""순위 조회 QThread의 생성과 신호 수명을 한 곳에서 관리한다."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from kiwoom_monitor.infrastructure.kiwoom_rest.ranking_worker import (
    RankingLoader,
    RankingWorker,
)


class RankingWorkerController(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        parent: QObject | None = None,
        worker_factory: Callable[[RankingLoader], RankingWorker] = RankingWorker,
    ) -> None:
        super().__init__(parent)
        self._worker_factory = worker_factory
        self._worker: RankingWorker | None = None

    @property
    def worker(self) -> RankingWorker | None:
        return self._worker

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def start(self, loader: RankingLoader) -> bool:
        if self.is_running:
            return False
        worker = self._worker_factory(loader)
        worker.setParent(self)
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
