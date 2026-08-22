from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupService


class ThemeBackupServiceTest(unittest.TestCase):
    def test_export_and_import_changes_only_theme_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "data" / "monitor.sqlite3"
            database_path.parent.mkdir()
            Database(database_path).initialize()
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("INSERT INTO stocks(code, name, market) VALUES ('005930', '삼성전자', 'KOSPI')")
                connection.execute("INSERT INTO themes(theme_name, default_color) VALUES ('반도체', '#123456')")
                theme_id = connection.execute("SELECT theme_id FROM themes WHERE theme_name = '반도체'").fetchone()[0]
                connection.execute("INSERT INTO stock_themes(stock_code, theme_id, custom_color) VALUES ('005930', ?, '#654321')", (theme_id,))
                connection.execute("UPDATE settings SET value = '3' WHERE key = 'decimal_strength'")
                connection.commit()
            finally:
                connection.close()
            backup_path = Path(directory) / "themes.json"
            ThemeBackupService(database_path).export_to(backup_path)

            connection = sqlite3.connect(database_path)
            try:
                connection.execute("DELETE FROM stock_themes")
                connection.execute("DELETE FROM themes")
                connection.execute("UPDATE settings SET value = '1' WHERE key = 'decimal_strength'")
                connection.commit()
            finally:
                connection.close()

            ThemeBackupService(database_path).import_from(backup_path)

            connection = sqlite3.connect(database_path)
            try:
                self.assertEqual(('1',), connection.execute("SELECT value FROM settings WHERE key = 'decimal_strength'").fetchone())
                self.assertEqual(('반도체', '#123456'), connection.execute("SELECT theme_name, default_color FROM themes").fetchone())
                self.assertEqual(('005930',), connection.execute("SELECT stock_code FROM stock_themes").fetchone())
            finally:
                connection.close()
