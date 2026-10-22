from __future__ import annotations

import unittest
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread
from PySide6.QtWidgets import QApplication

from kiwoom_monitor.infrastructure.kiwoom_rest.historical_high_worker import (
    HistoricalHighWorker,
)
from kiwoom_monitor.presentation.historical_high_worker_controller import (
    HistoricalHighWorkerController,
)


class FakeHistoricalHighWorker(HistoricalHighWorker):
    def __init__(self) -> None:
        QThread.__init__(self)
        self.running = False

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return self.running


class InvalidWorker(QObject):
    pass


class HistoricalHighWorkerControllerTests(unittest.TestCase):
    def test_start_forwards_result_failure_and_finished(self) -> None:
        worker = FakeHistoricalHighWorker()
        controller = HistoricalHighWorkerController(
            worker_factory=lambda _codes: worker
        )
        received: list[tuple[str, object]] = []
        failures: list[str] = []
        finished: list[bool] = []
        controller.received.connect(lambda code, value: received.append((code, value)))
        controller.failed.connect(failures.append)
        controller.finished.connect(lambda: finished.append(True))

        self.assertTrue(controller.start(("005930",)))
        worker.received.emit("005930", "target")
        worker.failed.emit("failed")
        worker.finished.emit()

        self.assertEqual([("005930", "target")], received)
        self.assertEqual(["failed"], failures)
        self.assertEqual([True], finished)

    def test_running_worker_is_reused_without_factory_call(self) -> None:
        worker = FakeHistoricalHighWorker()
        calls: list[tuple[str, ...]] = []
        controller = HistoricalHighWorkerController(
            worker_factory=lambda codes: calls.append(codes) or worker
        )
        self.assertTrue(controller.start(("005930",)))
        self.assertTrue(controller.start(("000660",)))
        self.assertEqual([("005930",)], calls)

    def test_missing_or_invalid_factory_rejects_start(self) -> None:
        failures: list[str] = []
        controller = HistoricalHighWorkerController()
        controller.failed.connect(failures.append)
        self.assertFalse(controller.start(("005930",)))

        controller.set_factory(lambda _codes: InvalidWorker())  # type: ignore[arg-type,return-value]
        self.assertFalse(controller.start(("005930",)))
        self.assertEqual(1, len(failures))


class HistoricalHighDesktopConnectionTests(unittest.TestCase):
    """Exercise the startup factory and real Qt signals through native persistence."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _desktop(self):
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.app_paths import AppPaths
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        from tests.unit.test_historical_high_service import _MarketCharts

        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        paths = AppPaths(directory, directory / "logs", directory / "monitor.sqlite3")
        client = _MarketCharts(krx=300, nxt=200)
        client.server_now = lambda: datetime.fromisoformat("2026-10-20T10:00:00+09:00")
        application = Mock()
        application.exec.return_value = 0
        # Startup runs normally up to the native database and nested runtime factory.
        # Keep its application loop/window and delayed retention out of this fixture.
        with patch.dict(os.environ), patch.object(sys, "excepthook"), \
                patch.object(bootstrap, "QApplication", return_value=application), \
                patch.object(bootstrap, "MainWindow") as construct, \
                patch.object(bootstrap.QTimer, "singleShot"), \
                patch.object(bootstrap, "create_query_client", return_value=client), \
                patch.object(bootstrap.LocalApiConfig, "load", return_value=SimpleNamespace(environment="mock")):
            construct.return_value = Mock()
            construct.return_value._app_controller.findChildren.return_value = []
            with self.assertRaises(SystemExit) as exited:
                bootstrap._run_desktop(paths, None)
            self.assertEqual(0, exited.exception.code)
            kwargs = construct.call_args.kwargs
        stocks = StockRepository(paths.database_path)
        stocks.upsert("005930", "fixture", "KOSPI")
        return client, kwargs, stocks

    def _window(self, kwargs, stocks):
        from kiwoom_monitor.presentation.app_controller import AppController
        from kiwoom_monitor.presentation.main_window import MainWindow
        from tests.unit.test_main_window import FakeRankingLoader

        # Reuse the existing ranking fixture without scheduling unrelated ranking
        # work. No loader would instead open the real modal API settings dialog.
        with patch.object(AppController, "start_initial_ranking"):
            window = MainWindow(kwargs["settings"], FakeRankingLoader(), stock_lookup=stocks,
                historical_high_worker_factory=kwargs["historical_high_worker_factory"])
        window._ranking_now = lambda: datetime.fromisoformat("2026-10-20T10:00:00+09:00")
        def close():
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
        self.addCleanup(close)
        return window

    def _finish(self, window):
        worker = window._historical_high_worker_controller.worker
        self.assertIsNotNone(worker)
        self.assertTrue(worker.wait(5_000))
        self.app.processEvents()
        self.app.processEvents()
        self.assertIsNone(window._historical_high_worker_controller.worker)

    def test_old_direct_basis_failure_retry_fresh_then_incremental_survives_restart(self):
        import sqlite3
        from contextlib import closing
        from kiwoom_monitor.application.historical_high_service import HistoricalHighEvidence
        from kiwoom_monitor.infrastructure.central_settings_sync import is_shared_setting

        client, kwargs, stocks = self._desktop()
        settings = kwargs["settings"]
        settings.set("historical_high_adjusted_basis_version", "5")
        settings.set("high_distance_period", "historical")
        # Real legacy key loss: the stock target is 300 but the surviving row is 200.
        stocks.update_historical_high_price("005930", 300, 2026, 2026, "2026-10-20",
            occurred_on="20261001", evidence=(HistoricalHighEvidence("month", "20261001", 200),))
        window = self._window(kwargs, stocks)
        client.failures[("005930", "ka10094")] = OSError("controlled required KRX failure")
        with patch("kiwoom_monitor.application.historical_high_service.kst_today", return_value=date(2026, 10, 20)):
            self.assertTrue(window._start_historical_high_loading(("005930",)))
            self._finish(window)
            self.assertEqual("5", settings.get("historical_high_adjusted_basis_version"))
            self.assertIn("controlled required KRX failure", window.statusBar().currentMessage())
            self.assertEqual(200, stocks.load_historical_high_cache("005930").target.evidence[0].high_price)
            client.failures.clear()
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("CREATE TRIGGER reject_historical BEFORE UPDATE OF historical_high_price ON stocks "
                    "BEGIN SELECT RAISE(ABORT, 'controlled historical storage failure'); END")
                connection.commit()
            with patch.object(sys, "excepthook") as unhandled:
                self.assertTrue(window._start_historical_high_loading(("005930",)))
                self._finish(window)
            unhandled.assert_called_once()
            self.assertIsInstance(unhandled.call_args.args[1], sqlite3.IntegrityError)
            self.assertEqual("controlled historical storage failure", str(unhandled.call_args.args[1]))
            self.assertEqual("5", settings.get("historical_high_adjusted_basis_version"))
            self.assertEqual(200, stocks.load_historical_high_cache("005930").target.evidence[0].high_price)
            with closing(sqlite3.connect(settings.database_path)) as connection:
                connection.execute("DROP TRIGGER reject_historical")
                connection.commit()
            client.calls.clear()
            self.assertTrue(window._start_historical_high_loading(("005930",)))
            self._finish(window)
        self.assertEqual("6", settings.get("historical_high_adjusted_basis_version"))
        self.assertFalse(is_shared_setting("historical_high_adjusted_basis_version"))
        self.assertEqual(4, len(client.calls))  # One-time KRX/NXT annual + month refresh.
        saved = stocks.load_historical_high_cache("005930")
        self.assertEqual((300, "2026-10-20", [300]),
            (saved.target.price, saved.checked_on, [item.high_price for item in saved.target.evidence]))
        self.assertEqual(300, window._historical_high_prices["005930"])
        from tests.unit.test_main_window import FakeRankingLoader
        window._app_controller.ranking.completed.emit(FakeRankingLoader().load_top_stocks())
        self.assertEqual("300", window._table.item(0, 13).text())
        self.assertFalse(window._start_historical_high_loading(("005930",)))
        self.assertEqual(4, len(client.calls))  # Same-day refresh remains skipped.
        window.close()
        settings.clear_cache()
        restored = self._window(kwargs, stocks)
        restored._ranking_now = lambda: datetime.fromisoformat("2026-10-21T10:00:00+09:00")
        client.calls.clear()
        with patch("kiwoom_monitor.application.historical_high_service.kst_today", return_value=date(2026, 10, 21)):
            self.assertTrue(restored._start_historical_high_loading(("005930",)))
            self._finish(restored)
        self.assertEqual([("ka10094", "005930"), ("ka10094", "005930_NX")],
            [(api, body["stk_cd"]) for api, _, body, _, _ in client.calls])
        self.assertEqual((300, "2026-10-21"),
            (stocks.load_historical_high_cache("005930").target.price,
             stocks.load_historical_high_cache("005930").checked_on))

    def test_reassembled_nas_factory_keeps_authoritative_partial_result_during_basis_refresh(self):
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
        from kiwoom_monitor.presentation.app_controller import AppRuntimeActions

        client, kwargs, stocks = self._desktop()
        settings = kwargs["settings"]
        settings.set("historical_high_adjusted_basis_version", "5")
        window = self._window(kwargs, stocks)
        source = {"price": 900, "first_year": 2020, "last_year": 2026, "occurred_on": "20201001",
            "evidence": [{"period": "month", "trade_date": "20201001", "high_price": 200}]}
        client.load_stored_historical_high = Mock(return_value=source)
        with patch.object(bootstrap.DataSourceConfig, "load", return_value=DataSourceSettings(mode="personal_server")), \
                patch.object(bootstrap, "create_query_client", return_value=client):
            runtime = kwargs["api_runtime_factory"]()
        self.assertEqual("personal_server", runtime["source_mode"])
        window._app_controller._apply_api_runtime(runtime,
            AppRuntimeActions(window._apply_api_runtime_view, window._capture_api_runtime_view))
        with patch("kiwoom_monitor.application.historical_high_service.kst_today", return_value=date(2026, 10, 20)):
            self.assertTrue(window._start_historical_high_loading(("005930",)))
            self._finish(window)
        client.load_stored_historical_high.assert_called_once_with("005930")
        self.assertEqual([], client.calls)
        saved = stocks.load_historical_high_cache("005930")
        self.assertEqual((900, "2026-10-20", [200]),
            (saved.target.price, saved.checked_on, [item.high_price for item in saved.target.evidence]))
        self.assertEqual("6", settings.get("historical_high_adjusted_basis_version"))
        self.assertEqual(900, window._historical_high_prices["005930"])


if __name__ == "__main__":
    unittest.main()
