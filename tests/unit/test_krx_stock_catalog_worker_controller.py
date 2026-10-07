from __future__ import annotations

import unittest

from PySide6.QtCore import QObject, QThread

from kiwoom_monitor.infrastructure.krx.stock_catalog_worker import KrxStockCatalogWorker
from kiwoom_monitor.presentation.krx_stock_catalog_worker_controller import KrxStockCatalogWorkerController


class FakeKrxStockCatalogWorker(KrxStockCatalogWorker):
    def __init__(self) -> None:
        QThread.__init__(self)
        self.running = False

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running


class InvalidWorker(QObject):
    pass


class KrxStockCatalogWorkerControllerTests(unittest.TestCase):
    def test_start_forwards_all_worker_signals(self) -> None:
        worker = FakeKrxStockCatalogWorker()
        controller = KrxStockCatalogWorkerController(worker_factory=lambda _stocks, _settings: worker)
        completed: list[tuple[int, bool]] = []
        failures: list[str] = []
        history_failures: list[str] = []
        finished: list[bool] = []
        self.assertTrue(controller.start(
            object(), object(),
            completed=lambda count, cached: completed.append((count, cached)),
            failed=failures.append,
            history_failed=history_failures.append,
            finished=lambda: finished.append(True),
        ))
        worker.completed.emit(120, False)
        worker.failed.emit("failed")
        worker.history_failed.emit("history failed")
        worker.finished.emit()
        self.assertEqual([(120, False)], completed)
        self.assertEqual(["failed"], failures)
        self.assertEqual(["history failed"], history_failures)
        self.assertEqual([True], finished)

    def test_running_worker_is_reused_without_factory_call(self) -> None:
        worker = FakeKrxStockCatalogWorker()
        calls: list[tuple[object, object]] = []
        controller = KrxStockCatalogWorkerController(worker_factory=lambda stocks, settings: calls.append((stocks, settings)) or worker)
        callbacks = dict(completed=lambda _count, _cached: None, failed=lambda _message: None, history_failed=lambda _message: None)
        stocks, settings = object(), object()
        self.assertTrue(controller.start(stocks, settings, **callbacks))
        self.assertTrue(controller.start(object(), object(), **callbacks))
        self.assertEqual([(stocks, settings)], calls)

    def test_invalid_factory_reports_configuration_failure(self) -> None:
        failures: list[str] = []
        controller = KrxStockCatalogWorkerController(worker_factory=lambda _stocks, _settings: InvalidWorker())  # type: ignore[arg-type,return-value]
        self.assertFalse(controller.start(
            object(), object(),
            completed=lambda _count, _cached: None,
            failed=failures.append,
            history_failed=lambda _message: None,
        ))
        self.assertEqual(1, len(failures))


if __name__ == "__main__":
    unittest.main()
