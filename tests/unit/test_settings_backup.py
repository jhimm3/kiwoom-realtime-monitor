from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.settings_backup import SettingsBackupService


class SettingsBackupServiceTest(unittest.TestCase):
    def test_export_then_import_restores_user_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.db"
            Database(database_path).initialize()
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("INSERT INTO stocks(code, name, market) VALUES ('005930', '삼성전자', 'KOSPI')")
                connection.execute("INSERT INTO themes(theme_name, default_color) VALUES ('반도체', '#123456')")
                theme_id = connection.execute("SELECT theme_id FROM themes WHERE theme_name = '반도체'").fetchone()[0]
                connection.execute("INSERT INTO stock_themes(stock_code, theme_id, custom_color) VALUES ('005930', ?, '#654321')", (theme_id,))
                connection.execute("INSERT INTO stock_aliases(alias, stock_code) VALUES ('삼전', '005930')")
                connection.execute("UPDATE settings SET value = '3' WHERE key = 'decimal_strength'")
                connection.commit()
            finally:
                connection.close()
            backup_path = Path(directory) / "settings.json"
            service = SettingsBackupService(database_path)
            service.export_to(backup_path)

            connection = sqlite3.connect(database_path)
            try:
                connection.execute("UPDATE column_settings SET visible = 0, position = 9, width = 333 WHERE column_name = 'stock'")
                connection.commit()
            finally:
                connection.close()
            service.import_from(backup_path)

            connection = sqlite3.connect(database_path)
            try:
                self.assertEqual(('3',), connection.execute("SELECT value FROM settings WHERE key = 'decimal_strength'").fetchone())
                self.assertEqual(('반도체', '#123456'), connection.execute("SELECT theme_name, default_color FROM themes").fetchone())
                self.assertEqual(('삼전', '005930'), connection.execute("SELECT alias, stock_code FROM stock_aliases").fetchone())
            finally:
                connection.close()

    def test_import_syncs_column_layout_but_keeps_local_widths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.db"
            Database(database_path).initialize()
            backup_path = Path(directory) / "settings.json"
            service = SettingsBackupService(database_path)
            service.export_to(backup_path)

            connection = sqlite3.connect(database_path)
            try:
                connection.execute("UPDATE column_settings SET visible = 0, position = 9, width = 333 WHERE column_name = 'stock'")
                connection.commit()
            finally:
                connection.close()

            service.import_from(backup_path, include_column_widths=False)

            connection = sqlite3.connect(database_path)
            try:
                self.assertEqual((1, 1, 333), connection.execute("SELECT visible, position, width FROM column_settings WHERE column_name = 'stock'").fetchone())
            finally:
                connection.close()
