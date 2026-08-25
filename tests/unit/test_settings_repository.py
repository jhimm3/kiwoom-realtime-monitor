from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.database import Database


class SettingsRepositoryTest(unittest.TestCase):
    def test_settings_are_created_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            self.assertEqual(database.settings.get("refresh_interval_seconds"), "30")
            self.assertEqual(database.settings.get("ui_mode"), "responsive")
            self.assertEqual(database.settings.get("theme_custom_separators"), "")
            self.assertEqual(database.settings.get("theme_image_import_custom_separators"), "")
            self.assertEqual(database.settings.get("theme_image_import_exclusions"), "")
            self.assertEqual(database.settings.get("theme_new_import_custom_separators"), "")
            self.assertEqual(database.settings.get("theme_new_import_exclusions"), "")
            self.assertEqual(database.settings.get("theme_text_import_custom_separators"), "")
            self.assertEqual(database.settings.get("theme_text_import_exclusions"), "")
            self.assertEqual(database.settings.get("theme_excel_import_custom_separators"), "")
            self.assertEqual(database.settings.get("theme_excel_import_exclusions"), "")
            self.assertEqual(database.settings.get("window_width"), "1160")

            database.settings.set("refresh_interval_seconds", "60")
            self.assertEqual(database.settings.get("refresh_interval_seconds"), "60")


if __name__ == "__main__":
    unittest.main()
