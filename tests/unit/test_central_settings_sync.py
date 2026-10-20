from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from datetime import datetime

from kiwoom_monitor.infrastructure.central_settings_sync import CentralSettingsSyncService, is_shared_setting
from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSetting, ColumnSettingsRepository


class _Client:
    def __init__(self) -> None:
        self.remote: dict[str, list[dict[str, object]]] = {}
        self.upsert_calls: list[tuple[str, list[dict[str, object]]]] = []

    def load_all(self, collection: str) -> list[dict[str, object]]:
        return list(self.remote.get(collection, ()))

    def upsert(self, collection: str, documents: list[dict[str, object]]) -> int:
        self.upsert_calls.append((collection, list(documents)))
        self.remote.setdefault(collection, []).extend(documents)
        return len(documents)


class CentralSettingsSyncTests(unittest.TestCase):
    def test_column_noop_preserves_remote_edit_while_local_changes_and_explicit_reset_are_shared(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'columns.sqlite'
            old = '2026-10-20T00:00:00+00:00'
            now = datetime.fromisoformat('2026-10-20T10:00:00+09:00')
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.executescript('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);'
                    'CREATE TABLE central_setting_versions(setting_key TEXT PRIMARY KEY,updated_at TEXT NOT NULL);'
                    'CREATE TABLE column_settings(column_name TEXT PRIMARY KEY,visible INTEGER,position INTEGER,width INTEGER);'
                    'CREATE TABLE central_column_setting_versions(column_name TEXT PRIMARY KEY,updated_at TEXT NOT NULL);')
                connection.executemany('INSERT INTO column_settings VALUES(?,?,?,?)',
                                       [('rank', 1, 0, 32), ('stock', 1, 1, 333)])
                connection.executemany('INSERT INTO central_column_setting_versions VALUES(?,?)',
                                       [('rank', old), ('stock', old)])
            repository = ColumnSettingsRepository(path, now_provider=lambda: now)
            def versions():
                with closing(sqlite3.connect(path)) as connection:
                    return dict(connection.execute('SELECT column_name,updated_at FROM central_column_setting_versions'))
            client = _Client()
            client.remote = {'app_column_settings': [
                {'owner': 'main_table', 'key': 'stock', 'document': {
                    'column_name': 'stock', 'visible': False, 'position': 9,
                    'updated_at': '2026-10-20T09:30:00+09:00'}},
                {'owner': 'main_table', 'key': 'not_installed', 'document': {
                    'column_name': 'not_installed', 'visible': True, 'position': 40,
                    'updated_at': '2026-10-20T09:45:00+09:00'}},
            ]}
            service = CentralSettingsSyncService(client, now_provider=lambda: now)
            repository.save(repository.list())
            self.assertEqual({'rank': old, 'stock': old}, versions())
            self.assertEqual(1, service.sync(path))  # The partial remote lacked the existing versioned rank column.
            self.assertEqual((ColumnSetting('rank', True, 0, 32), ColumnSetting('stock', False, 9, 333)), repository.list())
            self.assertNotIn('not_installed', versions())
            self.assertEqual(0, service.sync(path))

            repository.save((ColumnSetting('rank', True, 0, 32), ColumnSetting('stock', True, 2, 444)))
            self.assertEqual(old, versions()['rank'])
            self.assertEqual(now.isoformat(), versions()['stock'])
            self.assertEqual(1, service.sync(path))
            payload = client.upsert_calls[-1][1]
            self.assertEqual(['stock'], [value['key'] for value in payload])
            self.assertEqual({'column_name': 'stock', 'visible': True, 'position': 2,
                              'updated_at': now.isoformat()}, payload[0]['document'])
            self.assertNotIn('width', payload[0]['document'])
            self.assertEqual(0, service.sync(path))

            # Width remains local data, while its existing revision coupling is preserved.
            now = datetime.fromisoformat('2026-10-20T10:05:00+09:00')
            repository.save((ColumnSetting('stock', True, 2, 555),))
            self.assertEqual(now.isoformat(), versions()['stock'])
            self.assertEqual(1, service.sync(path))
            self.assertNotIn('width', client.upsert_calls[-1][1][0]['document'])
            self.assertEqual(555, next(column.width for column in repository.list() if column.name == 'stock'))

            for stamp in ('2026-10-20T10:10:00+09:00', '2026-10-20T10:11:00+09:00'):
                now = datetime.fromisoformat(stamp)
                repository.reset()
                # A second reset of already-default values is still an explicit shared action.
                self.assertEqual((ColumnSetting('rank', True, 0, 32), ColumnSetting('stock', True, 1, 142)), repository.list())
                self.assertEqual(stamp, versions()['rank'])
                self.assertEqual(stamp, versions()['stock'])
                self.assertEqual(2, service.sync(path))
                self.assertEqual({'rank', 'stock'}, {value['key'] for value in client.upsert_calls[-1][1]})
                self.assertEqual(0, service.sync(path))

            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("DELETE FROM central_column_setting_versions WHERE column_name='rank'")
            now = datetime.fromisoformat('2026-10-20T11:00:00+09:00')
            repository.save(repository.list())
            self.assertEqual(now.isoformat(), versions()['rank'])
            self.assertEqual('2026-10-20T10:11:00+09:00', versions()['stock'])
            self.assertEqual(1, service.sync(path))
            self.assertEqual(['rank'], [value['key'] for value in client.upsert_calls[-1][1]])
            self.assertEqual(0, service.sync(path))

    def test_equal_save_is_not_a_new_edit_and_changed_value_still_wins_by_instant(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'settings.sqlite'
            old = '2026-10-20T09:00:00+09:00'
            now = datetime.fromisoformat('2026-10-20T10:00:00+09:00')
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.executescript('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);'
                    'CREATE TABLE central_setting_versions(setting_key TEXT PRIMARY KEY,updated_at TEXT NOT NULL);'
                    'CREATE TABLE column_settings(column_name TEXT PRIMARY KEY,visible INTEGER,position INTEGER,width INTEGER);'
                    'CREATE TABLE central_column_setting_versions(column_name TEXT PRIMARY KEY,updated_at TEXT NOT NULL);')
                connection.execute("INSERT INTO settings VALUES('rank_query_type','5')")
                connection.execute("INSERT INTO central_setting_versions VALUES('rank_query_type',?)", (old,))
            repository = SettingsRepository(path, now_provider=lambda: now)
            repository.set('rank_query_type', '5')
            client = _Client()
            client.remote = {'app_settings': [{'owner': 'default', 'key': 'rank_query_type', 'document': {
                'key': 'rank_query_type', 'value': '6', 'updated_at': '2026-10-20T00:30:00+00:00'}}]}
            service = CentralSettingsSyncService(client, now_provider=lambda: now)
            service.sync(path)
            repository.clear_cache()
            self.assertEqual('6', repository.get('rank_query_type'))
            repository.set('rank_query_type', '7')
            self.assertEqual(1, service.sync(path))
            self.assertEqual('7', client.remote['app_settings'][-1]['document']['value'])
            self.assertEqual(now.isoformat(), client.remote['app_settings'][-1]['document']['updated_at'])
            self.assertEqual(0, service.sync(path))
            self.assertEqual('7', SettingsRepository(path).get('rank_query_type'))

    def test_utc_and_kst_versions_compare_instants_in_both_directions(self):
        cases = (
            ('2026-10-15T14:30:00+09:00', '2026-10-15T06:00:00+00:00', 'remote'),
            ('2026-10-15T06:00:00+00:00', '2026-10-15T14:30:00+09:00', 'local'),
        )
        for local, remote, expected in cases:
            with self.subTest(local=local, remote=remote), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'fixture.sqlite3'
                with closing(sqlite3.connect(path)) as c, c:
                    c.executescript('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);'
                                    'CREATE TABLE central_setting_versions(setting_key TEXT PRIMARY KEY,updated_at TEXT NOT NULL);'
                                    'CREATE TABLE column_settings(column_name TEXT PRIMARY KEY,visible INTEGER,position INTEGER,width INTEGER);'
                                    'CREATE TABLE central_column_setting_versions(column_name TEXT PRIMARY KEY,updated_at TEXT NOT NULL);')
                    c.execute("INSERT INTO settings VALUES('rank_query_type','local')")
                    c.execute("INSERT INTO central_setting_versions VALUES('rank_query_type',?)", (local,))
                    c.execute("INSERT INTO column_settings VALUES('stock',1,1,333)")
                    c.execute("INSERT INTO central_column_setting_versions VALUES('stock',?)", (local,))
                client = _Client()
                client.remote = {'app_settings': [{'key': 'rank_query_type', 'document': {
                    'key': 'rank_query_type', 'value': 'remote', 'updated_at': remote}}],
                    'app_column_settings': [{'key': 'stock', 'document': {
                        'column_name': 'stock', 'visible': False, 'position': 2, 'updated_at': remote}}]}
                service = CentralSettingsSyncService(client)
                service.sync(path)
                with closing(sqlite3.connect(path)) as c:
                    self.assertEqual(c.execute('SELECT value FROM settings').fetchone()[0], expected)
                    self.assertEqual(c.execute('SELECT visible FROM column_settings').fetchone()[0], expected == 'local')
                self.assertEqual(service.sync(path), 0)

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
                INSERT INTO settings VALUES('trade_value_5m_alert_eok','200');
                CREATE TABLE central_setting_versions(setting_key TEXT PRIMARY KEY,updated_at TEXT NOT NULL);
                INSERT INTO central_setting_versions VALUES('window_x','2026-09-08T10:00:00+00:00');
                INSERT INTO central_setting_versions VALUES('trade_value_1m_alert_eok','2026-09-08T10:00:00+00:00');
            """)
            connection.close()
            client = _Client()
            client.remote["app_settings"] = [{"owner": "default", "key": "window_x", "document": {"key": "window_x", "value": "remote-local-only", "updated_at": "2026-09-08T10:00:00+00:00"}}]

            saved = CentralSettingsSyncService(client).sync(path)  # type: ignore[arg-type]

            self.assertEqual(1, saved)
            self.assertEqual(["trade_value_1m_alert_eok"], [item["key"] for item in client.upsert_calls[0][1]])

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

    def test_unchanged_settings_and_columns_are_not_posted_every_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "monitor.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT INTO settings VALUES('rank_query_type','5');
                CREATE TABLE central_setting_versions(setting_key TEXT PRIMARY KEY,updated_at TEXT NOT NULL);
                INSERT INTO central_setting_versions VALUES('rank_query_type','2026-09-21T12:00:00+00:00');
                CREATE TABLE column_settings(column_name TEXT PRIMARY KEY,visible INTEGER,position INTEGER,width INTEGER);
                INSERT INTO column_settings VALUES('stock',1,1,333);
                CREATE TABLE central_column_setting_versions(column_name TEXT PRIMARY KEY,updated_at TEXT NOT NULL);
                INSERT INTO central_column_setting_versions VALUES('stock','2026-09-21T12:00:00+00:00');
            """)
            connection.close()
            client = _Client()
            service = CentralSettingsSyncService(client)  # type: ignore[arg-type]

            self.assertEqual(2, service.sync(path))
            client.upsert_calls.clear()
            self.assertEqual(0, service.sync(path))
            self.assertEqual([], client.upsert_calls)


if __name__ == "__main__":
    unittest.main()
