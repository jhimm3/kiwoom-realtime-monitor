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

    def test_missing_kind_success_date_retries_initial_history_until_real_success(self) -> None:
        import sqlite3
        import tempfile
        from contextlib import closing
        from datetime import datetime
        from pathlib import Path
        from unittest.mock import Mock, patch
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "query-role.sqlite3"
            Database(path).initialize_query_role(role="fixture-query")
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("DELETE FROM settings WHERE key='kind_name_history_sync_date'")
            fixed = datetime.fromisoformat("2026-10-25T10:00:00+09:00")
            settings = SettingsRepository(path, strict_text=True, now_provider=lambda: fixed)
            stocks = Mock()
            stocks.load_premarket_flags.return_value = None
            stocks.sync_kind_name_history.side_effect = OSError("controlled history failure")
            worker = KrxStockCatalogWorker(stocks, settings)
            failures = []
            worker.history_failed.connect(failures.append)
            with patch("kiwoom_monitor.infrastructure.krx.stock_catalog_worker.fetch_krx_stock_catalog",
                       return_value=(("000001", "fixture", "KOSPI"),)), \
                    patch("kiwoom_monitor.infrastructure.krx.stock_catalog_worker.kst_wall_now", return_value=fixed):
                worker.run()
                stocks.sync_kind_name_history.assert_called_once_with(initial=True)
                self.assertEqual(["controlled history failure"], failures)
                with self.assertRaises(KeyError):
                    settings.get("kind_name_history_sync_date")
                stocks.sync_kind_name_history.reset_mock()
                stocks.sync_kind_name_history.side_effect = None
                worker.run()
                stocks.sync_kind_name_history.assert_called_once_with(initial=True)
                self.assertEqual("2026-10-25 10:00:00", settings.get("kind_name_history_sync_date"))
                stocks.sync_kind_name_history.reset_mock()
                worker.run()
                stocks.sync_kind_name_history.assert_called_once_with(initial=False)

    def test_saved_premarket_reuses_current_list_and_still_syncs_kind_name_notices(self) -> None:
        import tempfile
        from datetime import datetime
        from pathlib import Path
        from unittest.mock import patch
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            settings = database.settings
            settings.set("krx_stock_catalog_date", "2026-11-08 10:00:00")
            stocks = StockRepository(path)
            stocks.save_premarket_catalog("2026-11-09", {
                "flags": {}, "stocks": {"005935": ("삼성전자우", "KOSPI"), "0147B0": ("래블업", "KOSDAQ")},
            })
            worker = KrxStockCatalogWorker(stocks, settings)
            worker.set_now_provider(lambda: datetime.fromisoformat("2026-11-08T23:05:00+00:00"))
            completed, failed = [], []
            worker.completed.connect(lambda count, cached: completed.append((count, cached)))
            worker.failed.connect(failed.append)
            with patch.object(stocks, "sync_kind_name_history") as history, patch(
                "kiwoom_monitor.infrastructure.krx.stock_catalog_worker.fetch_krx_stock_catalog",
            ) as download:
                worker.run()
            download.assert_not_called()
            history.assert_called_once_with(initial=True)
            self.assertEqual([(0, True)], completed)
            self.assertEqual([], failed)
            self.assertEqual("2026-11-08 10:00:00", settings.get("krx_stock_catalog_date"))
            self.assertEqual("2026-11-09 08:05:00", settings.get("kind_name_history_sync_date"))

    def test_running_worker_is_reused_without_factory_call(self) -> None:
        worker = FakeKrxStockCatalogWorker()
        calls: list[tuple[object, object]] = []
        controller = KrxStockCatalogWorkerController(worker_factory=lambda stocks, settings: calls.append((stocks, settings)) or worker)
        callbacks = dict(completed=lambda _count, _cached: None, failed=lambda _message: None, history_failed=lambda _message: None)
        stocks, settings = object(), object()
        self.assertTrue(controller.start(stocks, settings, **callbacks))
        self.assertTrue(controller.start(object(), object(), **callbacks))
        self.assertEqual([(stocks, settings)], calls)
        self.assertTrue(controller.is_running)
        worker.running = False
        worker.finished.emit()
        self.assertIsNone(controller.worker)
        self.assertFalse(controller.is_running)
        self.assertTrue(controller.start(stocks, settings, **callbacks))
        self.assertIs(worker, controller.worker)
        self.assertTrue(controller.is_running)
        self.assertEqual(2, len(calls))

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
