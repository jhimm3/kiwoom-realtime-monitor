from __future__ import annotations

import gc
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtWidgets import QApplication

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.presentation.candidate_monitor_dialog import CandidateMonitorDialog
from kiwoom_monitor.presentation.market_news_window import MarketNewsWindow
from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationDialog
from kiwoom_monitor.presentation.research_dialog import ResearchDialog
from kiwoom_monitor.presentation.settings_dialog import SettingsDialog


class _CandidateClient:
    def load_candidate_events(self, **_kwargs):
        return {"events": [], "has_more": False, "next_cursor": None,
                "high_watermark": 0, "quality": {}}


class AuxiliaryWindowGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cls.app.processEvents()
        gc.collect()

    def test_market_news_window_restores_size_and_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("kiwoom_monitor.presentation.market_news_window.QSettings",
                       side_effect=self._settings_factory(directory)):
                first = MarketNewsWindow(Path(directory) / "news.env", Path(directory) / "news.sqlite3")
                first.show()
                first.setGeometry(60, 75, 610, 480)
                first.close()
                first._window_settings.sync()
                second = MarketNewsWindow(Path(directory) / "news.env", Path(directory) / "news.sqlite3")
                second.show()
                self._assert_restored(first, second)
                second.shutdown()

    def test_shadow_research_and_mock_windows_restore_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cases = (
                ("candidate_monitor_dialog", lambda: CandidateMonitorDialog(_CandidateClient())),
                ("research_dialog", lambda: ResearchDialog(Path(directory) / "research")),
                ("mock_automation_dialog", lambda: MockAutomationDialog(object(), object())),
            )
            for module, factory in cases:
                with self.subTest(module=module), patch(
                    f"kiwoom_monitor.presentation.{module}.QSettings",
                    side_effect=self._settings_factory(directory),
                ):
                    first = factory()
                    if module == "mock_automation_dialog":
                        first._context = {"loaded": True}
                    first.show()
                    first.setGeometry(50, 65, 600, 450)
                    first.close()
                    first.stop()
                    first._settings.sync() if module != "mock_automation_dialog" else first._window_settings.sync()
                    second = factory()
                    if module == "mock_automation_dialog":
                        second._context = {"loaded": True}
                    second.show()
                    self._assert_restored(first, second)
                    second.stop()
                    second.close()

    def test_basic_settings_restore_position_and_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "monitor.sqlite3"
            Database(database).initialize()
            repository = SettingsRepository(database)
            with patch("kiwoom_monitor.presentation.settings_dialog.QSettings",
                       side_effect=self._settings_factory(directory)):
                first = SettingsDialog(repository)
                first._save_dialog_size = lambda: None
                first.show()
                first.setGeometry(45, 55, 620, 500)
                first.reject()
                first._window_settings.sync()
                second = SettingsDialog(repository)
                second._save_dialog_size = lambda: None
                second.show()
                self._assert_restored(first, second)
                second.reject()

    def _settings_factory(self, directory: str):
        return lambda _org, name: QSettings(
            str(Path(directory) / f"{name}.ini"), QSettings.Format.IniFormat)

    def _assert_restored(self, first, second) -> None:
        self.assertEqual(first.size(), second.size())
        self.assertEqual(first.pos().y(), second.pos().y())
        screen = self.app.primaryScreen().availableGeometry()
        if first.width() <= screen.width():
            self.assertEqual(first.pos().x(), second.pos().x())
        else:
            # Qt clamps an oversized window to the virtual offscreen display.
            self.assertIn(second.pos().x(), (-1, first.pos().x()))
