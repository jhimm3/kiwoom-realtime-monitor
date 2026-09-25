from __future__ import annotations

import os
import tempfile
import sqlite3
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton, QTabWidget

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.presentation.main_window import SettingsDialog
from qt_settings_test_support import wait_until


class SettingsApiHubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_news_ai_settings_are_reachable_from_main_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            open_news_settings = Mock()
            open_shadow_settings = Mock()
            open_research = Mock()
            open_mock_automation = Mock()
            dialog = SettingsDialog(
                SettingsRepository(database_path),
                api_path=Path(directory) / "api.env",
                news_api_settings_opener=open_news_settings,
                shadow_settings_opener=open_shadow_settings,
                research_opener=open_research,
                mock_automation_opener=open_mock_automation,
            )

            buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
            self.assertIn("키움 API 설정", buttons)
            self.assertIn("NAS 연결 설정", buttons)
            self.assertIn("네이버·DART·AI API 연결", buttons)
            self.assertIn("Shadow 후보 조건·상태", buttons)
            self.assertIn("전략 연구 열기", buttons)
            self.assertIn("모의 자동운용 상태·제어", buttons)
            tabs = dialog.findChild(QTabWidget)
            self.assertIsNotNone(tabs)
            self.assertIn("연결", tuple(tabs.tabText(index) for index in range(tabs.count())))
            self.assertIn("전략 연구", tuple(tabs.tabText(index) for index in range(tabs.count())))
            self.assertEqual(
                ("local", "personal_server"),
                tuple(dialog._data_source_mode.itemData(index) for index in range(dialog._data_source_mode.count())),
            )
            buttons["네이버·DART·AI API 연결"].click()
            open_news_settings.assert_called_once_with()
            buttons["Shadow 후보 조건·상태"].click()
            buttons["전략 연구 열기"].click()
            buttons["모의 자동운용 상태·제어"].click()
            open_shadow_settings.assert_called_once_with()
            open_research.assert_called_once_with()
            open_mock_automation.assert_called_once_with()

    def test_saving_settings_does_not_wait_for_a_locked_database_on_gui_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            dialog = SettingsDialog(SettingsRepository(path))
            dialog._ui_mode.setCurrentIndex(1)
            lock = sqlite3.connect(path)
            try:
                lock.execute("BEGIN IMMEDIATE")
                started = time.monotonic()
                dialog._save()
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertIsNotNone(dialog._save_request)
            finally:
                lock.rollback()
                lock.close()
            wait_until(lambda: dialog._save_request is None)
            self.assertEqual(str(dialog._ui_mode.currentData()), SettingsRepository(path).get("ui_mode"))


if __name__ == "__main__":
    unittest.main()
