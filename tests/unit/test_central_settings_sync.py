from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.central_settings_sync import CentralSettingsSyncService, is_shared_setting


class _Client:
    def __init__(self) -> None:
        self.remote: dict[str, list[dict[str, object]]] = {}

    def load_all(self, collection: str) -> list[dict[str, object]]:
        return list(self.remote.get(collection, ()))

    def upsert(self, collection: str, documents: list[dict[str, object]]) -> int:
        self.remote.setdefault(collection, []).extend(documents)
        return len(documents)


class CentralSettingsSyncTests(unittest.TestCase):
    def test_screen_paths_and_sync_state_are_local_only(self) -> None:
        self.assertFalse(is_shared_setting("window_x"))
        self.assertFalse(is_shared_setting("theme_excel_import_dir"))
        self.assertFalse(is_shared_setting("google_drive_last_upload_success_at"))
        self.assertFalse(is_shared_setting("near_high_sound_fire"))
        self.assertTrue(is_shared_setting("trade_value_1m_alert_eok"))
        self.assertTrue(is_shared_setting("theme_text_import_exclusions"))
        self.assertTrue(is_shared_setting("theme_active_profile"))
        self.assertTrue(is_shared_setting("theme_profiles_initialized"))

    def test_only_versioned_shared_settings_are_uploaded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "monitor.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT INTO settings VALUES('window_x','50');
                INSERT INTO settings VALUES('trade_value_1m_alert_eok','80');
                CREATE TABLE central_setting_versions(setting_key TEXT PRIMARY KEY,updated_at TEXT NOT NULL);
                INSERT INTO central_setting_versions VALUES('window_x','2026-09-08T10:00:00+00:00');
                INSERT INTO central_setting_versions VALUES('trade_value_1m_alert_eok','2026-09-08T10:00:00+00:00');
            """)
            connection.close()
            client = _Client()

            saved = CentralSettingsSyncService(client).sync(path)  # type: ignore[arg-type]

            self.assertEqual(1, saved)
            self.assertEqual("trade_value_1m_alert_eok", client.remote["app_settings"][0]["key"])

    def test_empty_server_uses_first_pc_as_initial_shared_settings_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "monitor.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT INTO settings VALUES('rank_query_type','5');
                INSERT INTO settings VALUES('window_width','1200');
            """)
            connection.close()
            client = _Client()

            saved = CentralSettingsSyncService(client).sync(path)  # type: ignore[arg-type]

            self.assertEqual(1, saved)
            self.assertEqual("rank_query_type", client.remote["app_settings"][0]["key"])

    def test_column_visibility_and_order_sync_but_width_stays_local(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "monitor.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT INTO settings VALUES('rank_query_type','5');
                CREATE TABLE column_settings(column_name TEXT PRIMARY KEY,visible INTEGER,position INTEGER,width INTEGER);
                INSERT INTO column_settings VALUES('stock',1,1,333);
            """)
            connection.close()
            client = _Client()
            client.remote["app_column_settings"] = [{
                "key": "stock", "document": {
                    "column_name": "stock", "visible": False, "position": 9,
                    "updated_at": "2026-09-12T01:00:00+00:00",
                },
            }]

            CentralSettingsSyncService(client).sync(path)  # type: ignore[arg-type]

            connection = sqlite3.connect(path)
            row = connection.execute(
                "SELECT visible,position,width FROM column_settings WHERE column_name='stock'"
            ).fetchone()
            connection.close()
            self.assertEqual((0, 9, 333), row)
            document = client.remote["app_column_settings"][-1]["document"]
            self.assertNotIn("width", document)


if __name__ == "__main__":
    unittest.main()
