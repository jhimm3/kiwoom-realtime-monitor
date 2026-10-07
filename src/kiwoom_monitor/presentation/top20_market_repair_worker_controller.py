"""TOP20 시장 구분 보완 QThread의 생성과 신호 수명을 관리한다."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal

from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
from kiwoom_monitor.presentation.top20_trade_value import Top20MarketRepairWorker


class Top20MarketRepairWorkerController(QObject):
    completed = Signal(int)
    failed = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        worker_factory: Callable[[MinuteBarRepository], Top20MarketRepairWorker] = Top20MarketRepairWorker,
    ) -> None:
        super().__init__(parent)
        self._worker_factory = worker_factory
        self._worker: Top20MarketRepairWorker | None = None

    @property
    def worker(self) -> Top20MarketRepairWorker | None:
        return self._worker

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def start(self, repository: MinuteBarRepository) -> bool:
        if self.is_running:
            return True
        worker = self._worker_factory(repository)
        worker.setParent(self)
        if not isinstance(worker, Top20MarketRepairWorker):
            self.failed.emit(
                "TOP20 시장 구분 보완 작업 구성이 올바르지 않습니다. 프로그램을 다시 실행해 주세요."
            )
            worker.deleteLater()
            return False
        worker.completed.connect(self.completed.emit)
        worker.failed.connect(self.failed.emit)
        worker.finished.connect(self._release_finished_worker)
        self._worker = worker
        worker.start(QThread.Priority.LowPriority)
        return True

    def _release_finished_worker(self) -> None:
        worker = self.sender()
        if worker is None or worker.isRunning():
            return
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()
