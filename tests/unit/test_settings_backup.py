from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from base64 import b64encode
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

            service.import_from(backup_path, include_column_widths=False)

            connection = sqlite3.connect(database_path)
            try:
                self.assertEqual((1, 1, 333), connection.execute("SELECT visible, position, width FROM column_settings WHERE column_name = 'stock'").fetchone())
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

    def test_export_and_import_preserve_all_per_import_theme_rules(self) -> None:
        keys = {
            "theme_new_import_custom_separators": "·",
            "theme_new_import_exclusions": "신규제외",
            "theme_text_import_custom_separators": "+",
            "theme_text_import_exclusions": "텍스트제외",
            "theme_text_include_subcategories": "1",
            "theme_excel_import_custom_separators": "#",
            "theme_excel_import_exclusions": "엑셀제외",
            "theme_image_import_custom_separators": "=",
            "theme_image_import_exclusions": "OCR제외",
        }
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.db"
            Database(database_path).initialize()
            connection = sqlite3.connect(database_path)
            try:
                connection.executemany("UPDATE settings SET value=? WHERE key=?", [(value, key) for key, value in keys.items()])
                connection.commit()
            finally:
                connection.close()
            backup_path = Path(directory) / "settings.json"
            service = SettingsBackupService(database_path)
            service.export_to(backup_path, include_themes=False)
            exported = json.loads(backup_path.read_text(encoding="utf-8"))["settings"]
            self.assertEqual(keys, {key: exported[key] for key in keys})

            connection = sqlite3.connect(database_path)
            try:
                connection.executemany("UPDATE settings SET value='' WHERE key=?", [(key,) for key in keys])
                connection.commit()
            finally:
                connection.close()
            service.import_from(backup_path, include_themes=False)
            connection = sqlite3.connect(database_path)
            try:
                placeholders = ",".join("?" for _ in keys)
                restored = dict(connection.execute(f"SELECT key, value FROM settings WHERE key IN ({placeholders})", tuple(keys)))
            finally:
                connection.close()
            self.assertEqual(keys, restored)

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

    def test_asset_import_rejects_path_escape_and_oversized_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "data" / "monitor.db"
            database_path.parent.mkdir()
            Database(database_path).initialize()
            service = SettingsBackupService(database_path)

            valid = b"valid icon"
            service._import_assets([
                {"path": "data/near_high_icons/interest.png", "content": b64encode(valid).decode("ascii")},
                {"path": "data/near_high_sounds/../../../outside.txt", "content": b64encode(b"bad").decode("ascii")},
                {
                    "path": "data/strength_icons/fire.png",
                    "content": b64encode(b"x" * (2 * 1024 * 1024 + 1)).decode("ascii"),
                },
            ])

            self.assertEqual(valid, (root / "data" / "near_high_icons" / "interest.png").read_bytes())
            self.assertFalse((root / "outside.txt").exists())
            self.assertFalse((root / "data" / "strength_icons" / "fire.png").exists())
