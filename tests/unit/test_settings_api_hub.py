from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton, QTabWidget

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.presentation.main_window import SettingsDialog


class SettingsApiHubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_news_ai_settings_are_reachable_from_main_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            Database(database_path).initialize()
            open_news_settings = Mock()
            dialog = SettingsDialog(
                SettingsRepository(database_path),
                api_path=Path(directory) / "api.env",
                news_api_settings_opener=open_news_settings,
            )

            buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
            self.assertIn("키움 API 설정", buttons)
            self.assertIn("NAS 연결 설정", buttons)
            self.assertIn("네이버·DART·AI API 연결", buttons)
            tabs = dialog.findChild(QTabWidget)
            self.assertIsNotNone(tabs)
            self.assertIn("연결", tuple(tabs.tabText(index) for index in range(tabs.count())))
            self.assertEqual(
                ("local", "personal_server"),
                tuple(dialog._data_source_mode.itemData(index) for index in range(dialog._data_source_mode.count())),
            )
            buttons["네이버·DART·AI API 연결"].click()
            open_news_settings.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
