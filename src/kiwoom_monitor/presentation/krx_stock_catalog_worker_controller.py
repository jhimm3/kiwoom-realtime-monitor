"""KRX 종목 카탈로그 QThread의 생성과 신호 수명을 관리한다."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject

from kiwoom_monitor.infrastructure.krx.stock_catalog_worker import KrxStockCatalogWorker


class KrxStockCatalogWorkerController(QObject):
    def __init__(
        self,
        parent: QObject | None = None,
        worker_factory: Callable[[object, object], KrxStockCatalogWorker] = KrxStockCatalogWorker,
    ) -> None:
        super().__init__(parent)
        self._worker_factory = worker_factory
        self._worker: KrxStockCatalogWorker | None = None

    @property
    def worker(self) -> KrxStockCatalogWorker | None:
        return self._worker

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def start(
        self,
        stocks: object,
        settings: object,
        *,
        completed: Callable[[int, bool], None],
        failed: Callable[[str], None],
        history_failed: Callable[[str], None],
        finished: Callable[[], None] | None = None,
    ) -> bool:
        if self.is_running:
            return True
        worker = self._worker_factory(stocks, settings)
        worker.setParent(self)
        if not isinstance(worker, KrxStockCatalogWorker):
            failed("KRX 종목 카탈로그 작업 구성이 올바르지 않습니다. 프로그램을 다시 실행해 주세요.")
            worker.deleteLater()
            return False
        worker.completed.connect(completed)
        worker.failed.connect(failed)
        worker.history_failed.connect(history_failed)
        if finished is not None:
            worker.finished.connect(finished)
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
