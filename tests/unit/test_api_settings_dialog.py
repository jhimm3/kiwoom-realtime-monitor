from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from threading import Event
from PySide6.QtCore import QTimer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.infrastructure.naver_news import (
    LocalNaverNewsConfig,
    NaverNewsCredentials,
    NewsAISettings,
    OfficialNewsSettings,
)
from kiwoom_monitor.presentation.main_window import ApiSettingsDialog
from qt_settings_test_support import dispose_dialogs, wait_until


class ApiSettingsDialogTests(unittest.TestCase):
    def test_excluded_stock_search_modal_save_and_theme_consumers_are_connected(self):
        import gc
        import sqlite3
        from contextlib import closing
        from datetime import datetime
        from types import SimpleNamespace
        from PySide6.QtCore import QCoreApplication, QEvent, QSettings
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QDialogButtonBox, QLabel, QPushButton, QTabWidget
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        from kiwoom_monitor.presentation.main_window import MainWindow
        from kiwoom_monitor.presentation.similar_stock_dialog import SimilarStockDialog, choose_similar_stock
        from kiwoom_monitor.presentation.settings_request_worker import _running_requests

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = Database(root / "monitor.sqlite3")
            database.initialize()
            stocks = StockRepository(database.settings.database_path)
            stocks.upsert_many((("005930", "삼성전자", "KOSPI"), ("000660", "SK하이닉스", "KOSPI")))
            now = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
            loader = SimpleNamespace(load_top_stocks=lambda: (), server_now=lambda: now)
            window = MainWindow(database.settings, loader, stock_lookup=stocks,
                                themes={"삼성전자": "반도체", "SK하이닉스": "반도체"})
            window._app_controller._initial_ranking_timer.stop()
            initial_requests = set(_running_requests)
            created, callback_errors = [], []
            action = ["삼성", "select", 1]
            expected_parent = [None]

            def picker_factory(*args, **kwargs):
                picker = SimilarStockDialog(*args, **kwargs)
                created.append(picker)
                self.assertIs(expected_parent[0], picker.parent())

                def interact():
                    try:
                        picker._search.setText(action[0])
                        QTest.qWait(180)  # Exercise the existing 150ms search signal/debounce.
                        self.assertEqual(action[2], picker._list.count())
                        if action[1] == "select":
                            self.assertTrue(picker._select.isEnabled())
                            picker._select.click()
                        else:
                            if not action[2]:
                                self.assertFalse(picker._select.isEnabled())
                            next(button for button in picker.findChildren(QPushButton)
                                 if button.text() == "이번 종목 무시").click()
                    except BaseException as error:
                        callback_errors.append(error)
                        picker.reject()
                QTimer.singleShot(0, interact)
                return picker

            try:
                # Keep the real MainWindow factory/finished callback; isolate only file locations.
                with patch.object(window, "_api_config_path", return_value=root / "api.env"), \
                        patch("kiwoom_monitor.presentation.settings_dialog.QSettings", side_effect=lambda *_args: QSettings(str(root / "geometry.ini"), QSettings.Format.IniFormat)):
                    window._open_settings()
                    dialog = window._settings_dialog
                    expected_parent[0] = dialog
                    tabs = dialog.findChild(QTabWidget)
                    tabs.setCurrentIndex(next(index for index in range(tabs.count()) if tabs.tabText(index) == "표시 형식"))
                    self.app.processEvents()
                    find = next(button for button in dialog.findChildren(QPushButton) if button.text() == "종목 찾기")
                    with patch("kiwoom_monitor.presentation.settings_dialog.SimilarStockDialog", side_effect=picker_factory):
                        for query, operation, count in (("삼성", "select", 1), ("삼성", "select", 1),
                                                        ("하이닉스", "cancel", 1), ("ZZZ-XQ-999", "cancel", 0)):
                            action[:] = [query, operation, count]
                            prior = len(created)
                            find.click()
                            self.assertEqual(prior + 1, len(created))
                            if callback_errors:
                                raise callback_errors[0]
                            self.assertEqual("삼성전자", dialog._theme_trade_summary_excluded_stocks.text())
                            self.assertIn("확인할 테마 없음", "\n".join(label.text() for label in created[-1].findChildren(QLabel)))
                    dialog._theme_trade_summary_excluded_enabled.setChecked(True)
                    dialog._theme_group_sort_basis.setCurrentIndex(1)
                    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
                    wait_until(lambda: dialog._save_request is None and window._settings_dialog is None)
                    self.assertEqual(1, dialog.result())
                    self.assertEqual("기본 설정 저장 완료", window.statusBar().currentMessage())
                    wait_until(lambda: not (set(_running_requests) - initial_requests))
                    with closing(sqlite3.connect(database.settings.database_path)) as connection:
                        self.assertEqual(("삼성전자",), connection.execute(
                            "SELECT value FROM settings WHERE key='theme_trade_summary_excluded_stocks'"
                        ).fetchone())
                    database.settings.clear_cache()
                    window._ranked_stock_names = {"005930": "삼성전자", "000660": "SK하이닉스"}
                    window._minute_aggregator.today_trade_value_eok = lambda code, _now: {"005930": 100., "000660": 40.}[code]
                    window._refresh_theme_trade_summary()
                    self.assertIn("140.00억", window._theme_trade_summary.text())
                    self.assertIn("40.00억", window._theme_trade_excluded_summary.text())
                    self.assertEqual(({"반도체": 1}, {"반도체": 40.}), window._theme_group_sort_metrics())
                    window._open_settings()
                    restored = window._settings_dialog
                    self.assertEqual("삼성전자", restored._theme_trade_summary_excluded_stocks.text())
                    restored.reject()
                    wait_until(lambda: not (set(_running_requests) - initial_requests))
                # The import helper's explicit themes + parent constructor contract stays valid.
                expected_parent[0] = window
                action[:] = ["삼성", "select", 1]
                with patch("kiwoom_monitor.presentation.similar_stock_dialog.SimilarStockDialog", side_effect=picker_factory):
                    selected, cancelled_all = choose_similar_stock(window, stocks, "입력 오타", ("반도체",))
                if callback_errors:
                    raise callback_errors[0]
                self.assertEqual((("005930", "삼성전자"), False), (selected, cancelled_all))
                self.assertIn("반도체", "\n".join(label.text() for label in created[-1].findChildren(QLabel)))
            finally:
                for worker in tuple(set(_running_requests) - initial_requests):
                    self.assertTrue(worker.wait(3000))
                self.app.processEvents()
                dispose_dialogs()
                self.app.processEvents()
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
                self.app.processEvents()
                gc.collect()

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        dispose_dialogs()

    def test_central_waiting_route_is_presented_as_nas_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(
                Path(directory) / "api.env",
                section="nas",
                active_route="central_waiting",
            )

            self.assertEqual("NAS", dialog._active_route_status.text())
            self.assertIn("#008000", dialog._active_route_status.styleSheet())

    def test_personal_server_failover_can_be_toggled_and_saved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            api_path = Path(directory) / "api.env"
            source_config = DataSourceConfig(api_path.with_name("data_source.json"))
            source_config.save(DataSourceSettings(
                "personal_server", "https://nas.example.test", "token", False,
            ))
            dialog = ApiSettingsDialog(api_path, section="nas")
            self.assertTrue(dialog._local_fallback.isEnabled())
            dialog._local_fallback.setChecked(True)
            dialog._parallel_validation.setChecked(True)
            dialog._mock_app_key.setText("local-key")
            dialog._mock_secret_key.setText("local-secret")
            dialog._validate_and_accept()
            self.assertTrue(source_config.load().local_fallback_enabled)
            self.assertTrue(source_config.load().parallel_validation_enabled)

    def test_nas_resource_usage_shows_storage_categories_and_unlimited_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            dialog._show_nas_resource_usage({
                "process_memory_bytes": 1024,
                "container_memory_bytes": 2048,
                "container_memory_limit_bytes": None,
                "database_size_bytes": 4096,
                "data_disk_used_bytes": 8192,
                "data_disk_total_bytes": 16384,
                "storage_categories": [
                    {"category": "news", "label": "뉴스", "estimated_bytes": 3072, "rows": 1234},
                    {"category": "market", "label": "주식 누적자료", "estimated_bytes": 1024, "rows": 20},
                ],
                "retention_policy": {"mode": "unlimited", "automatic_deletion_enabled": False},
            })
            text = dialog._nas_resource_status.text()

        self.assertIn("뉴스 3.0KB · 1,234건", text)
        self.assertIn("주식 누적자료 1.0KB · 20건", text)
        self.assertIn("자동 정리: 무제한 저장", text)

    def test_pc_resource_usage_shows_separate_categories_and_retention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="kiwoom")
            dialog._show_local_resource_usage({
                "complete": True,
                "total_bytes": 7168,
                "categories": [
                    {"category": "market", "label": "주식·설정 DB", "bytes": 4096, "files": 2},
                    {"category": "news", "label": "뉴스 DB", "bytes": 3072, "files": 1},
                ],
                "retention": ["분봉: 30일 초과분을 앱 시작 뒤 정리"],
            })
            text = dialog._local_resource_status.text()

        self.assertIn("전체 7.0KB", text)
        self.assertIn("주식·설정 DB 4.0KB · 2개 파일", text)
        self.assertIn("뉴스 DB 3.0KB · 1개 파일", text)
        self.assertIn("분봉: 30일", text)

    def test_direct_mode_does_not_offer_failover(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            self.assertFalse(dialog._local_fallback.isEnabled())
            self.assertFalse(dialog.data_source_values.local_fallback_enabled)

    def test_slow_load_keeps_gui_responsive_and_survives_dialog_destruction(self):
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            started, release = Event(), Event()

            def delayed_load():
                started.set()
                release.wait(2)
                return {"ai_provider": "none"}

            with patch("kiwoom_monitor.presentation.api_settings_dialog.CentralOperationalSettingsClient") as client:
                client.return_value.load.side_effect = delayed_load
                dialog._load_nas_operational_settings()
                worker = dialog._operations_worker
                try:
                    wait_until(started.is_set)
                    ticks = []
                    QTimer.singleShot(0, lambda: ticks.append(True))
                    wait_until(lambda: bool(ticks))
                    dialog.reject()
                    dialog.deleteLater()
                    dispose_dialogs()
                finally:
                    release.set()
                    worker.wait(3000)
                    self.assertFalse(worker.isRunning())
                    self.app.processEvents()

    def test_save_only_changed_controls_and_keeps_dialog_open_on_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            DataSourceConfig(root / "data_source.json").save(DataSourceSettings(
                "personal_server", "https://nas.example.test", "token",
            ))
            dialog = ApiSettingsDialog(root / "api.env", section="nas")
            with patch("kiwoom_monitor.presentation.api_settings_dialog.CentralOperationalSettingsClient") as factory:
                client = factory.return_value
                client.load.return_value = {
                    "ai_provider": "none", "ai_model": "", "ai_daily_limit": 20,
                    "news_refresh_seconds": 300, "dart_enabled": False, "revision": 2,
                }
                dialog._load_nas_operational_settings()
                wait_until(lambda: dialog._operations_worker is None)
                dialog.show()
                wait_until(lambda: dialog._operations_worker is None)
                self.assertTrue(dialog.isVisible())
                dialog._nas_ai_daily_limit.setValue(21)
                client.save.side_effect = RuntimeError("revision conflict")
                dialog._validate_and_accept()
                wait_until(lambda: dialog._operations_worker is None)
            client.save.assert_called_once_with({"ai_daily_limit": 21})
            self.assertEqual(21, dialog._nas_ai_daily_limit.value())
            self.assertIn("revision conflict", dialog._nas_operations_status.text())
            self.assertTrue(dialog.isVisible())

    def test_local_server_mode_fills_safe_local_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            dialog._data_source_mode.setCurrentIndex(dialog._data_source_mode.findData("local_server"))
            values = dialog.data_source_values
            self.assertEqual("http://127.0.0.1:8787", values.server_url)
            self.assertGreaterEqual(len(values.access_token), 32)
            self.assertFalse(dialog._local_fallback.isEnabled())

    def test_personal_server_mode_exposes_server_and_failover_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            dialog._data_source_mode.setCurrentIndex(dialog._data_source_mode.findData("personal_server"))
            dialog._server_url.setText("https://nas.example.test/")
            dialog._server_token.setText("server-token")
            dialog._local_fallback.setChecked(True)
            self.assertTrue(dialog._server_url.isEnabled())
            self.assertTrue(dialog._local_fallback.isEnabled())
            self.assertEqual("https://nas.example.test", dialog.data_source_values.server_url.rstrip("/"))

    def test_nas_ai_provider_offers_recommended_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            dialog._nas_ai_provider.setCurrentIndex(dialog._nas_ai_provider.findData("gemini"))
            labels = tuple(dialog._nas_ai_model.itemText(index) for index in range(dialog._nas_ai_model.count()))
            self.assertTrue(any("추천" in label for label in labels))
            self.assertEqual("gemini-3.5-flash-lite", dialog._nas_ai_model.currentData())

    def test_loaded_nas_operations_are_mirrored_to_local_news_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api_path = root / "api.env"
            DataSourceConfig(root / "data_source.json").save(DataSourceSettings(
                "personal_server", "https://nas.example.test", "token",
            ))
            news = LocalNaverNewsConfig(root / "naver_news.dat")
            news.save(
                NaverNewsCredentials("id", "secret"),
                ai=NewsAISettings("openai", "ai-secret", "old", 10),
                official=OfficialNewsSettings("dart-secret", False),
            )
            dialog = ApiSettingsDialog(api_path, section="nas")
            values = {
                "ai_provider": "gemini", "ai_model": "gemini-3.5-flash-lite",
                "ai_daily_limit": 321, "news_refresh_seconds": 600, "dart_enabled": True,
            }

            with patch("kiwoom_monitor.presentation.api_settings_dialog.CentralOperationalSettingsClient") as client:
                client.return_value.load.return_value = values
                dialog._load_nas_operational_settings()
                wait_until(lambda: dialog._operations_worker is None)

            self.assertFalse(hasattr(dialog, "_nas_condition_enabled"))
            self.assertFalse(any(key.startswith("hot_cohort_condition_") for key in dialog._nas_operation_values()))
            self.assertEqual("gemini", news.load_ai().provider)
            self.assertEqual(321, news.load_ai().daily_limit)
            self.assertEqual("ai-secret", news.load_ai().api_key)
            self.assertTrue(news.load_official().dart_enabled)
            self.assertEqual("dart-secret", news.load_official().dart_api_key)


if __name__ == "__main__":
    unittest.main()
