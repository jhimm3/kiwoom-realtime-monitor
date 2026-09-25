from __future__ import annotations

import tempfile
import unittest
import sqlite3
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
            self.assertEqual(database.settings.get("theme_text_include_subcategories"), "0")
            self.assertEqual(database.settings.get("theme_excel_import_custom_separators"), "")
            self.assertEqual(database.settings.get("theme_excel_import_exclusions"), "")
            self.assertEqual(database.settings.get("window_width"), "1160")
            self.assertEqual(database.settings.get("upper_limit_highlight_enabled"), "1")

            database.settings.set("refresh_interval_seconds", "60")
            self.assertEqual(database.settings.get("refresh_interval_seconds"), "60")

    def test_set_many_persists_values_and_one_version_for_form(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            database.settings.set_many({"ui_mode": "compact", "refresh_interval_seconds": "60"})
            connection = sqlite3.connect(path)
            try:
                rows = connection.execute(
                    "SELECT setting_key,updated_at FROM central_setting_versions "
                    "WHERE setting_key IN ('ui_mode','refresh_interval_seconds')"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(2, len(rows))
            self.assertEqual(1, len({updated_at for _, updated_at in rows}))
            self.assertEqual("compact", database.settings.get("ui_mode"))
            self.assertEqual("60", database.settings.get("refresh_interval_seconds"))


if __name__ == "__main__":
    unittest.main()
