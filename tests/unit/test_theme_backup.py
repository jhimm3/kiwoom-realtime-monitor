from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupService
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository


class ThemeBackupServiceTest(unittest.TestCase):
    def test_export_and_import_changes_only_theme_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "data" / "monitor.sqlite3"
            database_path.parent.mkdir()
            Database(database_path).initialize()
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("UPDATE settings SET value = '3' WHERE key = 'decimal_strength'")
                connection.commit()
            finally:
                connection.close()
            StockRepository(database_path).upsert("005930", "삼성전자", "KOSPI")
            repository = ThemeRepository(database_path)
            repository.replace_for_stock("005930", ("반도체",))
            repository.set_color("반도체", "#123456")
            repository.set_stock_theme_color("005930", "반도체", "#654321")
            repository.create_profile("회사")
            repository.select_profile("회사")
            repository.replace_for_stock("005930", ("AI",))
            backup_path = Path(directory) / "themes.json"
            ThemeBackupService(database_path).export_to(backup_path)

            connection = sqlite3.connect(database_path)
            try:
                connection.execute("DELETE FROM profile_stock_themes")
                connection.execute("DELETE FROM profile_themes")
                connection.execute("UPDATE settings SET value = '1' WHERE key = 'decimal_strength'")
                connection.commit()
            finally:
                connection.close()

            ThemeBackupService(database_path).import_from(backup_path)

            connection = sqlite3.connect(database_path)
            try:
                self.assertEqual(('1',), connection.execute("SELECT value FROM settings WHERE key = 'decimal_strength'").fetchone())
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM theme_profiles").fetchone()[0])
            finally:
                connection.close()
            repository.select_profile("기본 테마")
            self.assertEqual(("반도체",), repository.themes_for_stock("005930"))
            repository.select_profile("회사")
            self.assertEqual(("AI",), repository.themes_for_stock("005930"))
