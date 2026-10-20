from __future__ import annotations

import tempfile
import unittest
import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository


class SettingsRepositoryTest(unittest.TestCase):
    def _while_connection_close_is_paused(self, path, operation, while_paused):
        """Pause after a native close, so a second connection can really commit."""
        native_connect = sqlite3.connect
        closed = threading.Event()
        resume = threading.Event()
        results = []
        errors = []

        class PausedConnection:
            def __init__(self, connection):
                self._connection = connection

            def __getattr__(self, name):
                return getattr(self._connection, name)

            def close(self):
                self._connection.close()
                closed.set()
                if not resume.wait(5):
                    raise AssertionError("native connection close was not released")

        def call():
            try:
                results.append(operation())
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=call)

        def connect(*args, **kwargs):
            connection = native_connect(*args, **kwargs)
            if threading.current_thread() is worker and Path(args[0]) == path:
                return PausedConnection(connection)
            return connection

        with patch(
            "kiwoom_monitor.infrastructure.persistence.settings_repository.sqlite3.connect",
            side_effect=connect,
        ):
            worker.start()
            try:
                self.assertTrue(closed.wait(5), "native connection was not closed")
                while_paused(native_connect)
            finally:
                resume.set()
                worker.join(5)
            self.assertFalse(worker.is_alive(), "owned settings worker did not stop")
            if errors:
                raise errors[0]
        return results

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

    def test_inflight_get_cannot_repopulate_cache_after_clear_or_committed_write(self) -> None:
        for update_kind in ("external_clear", "local_write"):
            with self.subTest(update_kind=update_kind), tempfile.TemporaryDirectory() as temporary_directory:
                path = Path(temporary_directory) / "monitor.sqlite3"
                database = Database(path)
                database.initialize()
                repository = database.settings

                def update(native_connect):
                    if update_kind == "local_write":
                        repository.set("ui_mode", "compact")
                    else:
                        with closing(native_connect(path)) as connection, connection:
                            connection.execute("UPDATE settings SET value=? WHERE key=?", ("compact", "ui_mode"))
                        repository.clear_cache()

                self.assertEqual(
                    ["responsive"],
                    self._while_connection_close_is_paused(path, lambda: repository.get("ui_mode"), update),
                )
                # The already-started read may return its snapshot, but cannot poison later reads.
                self.assertEqual("compact", repository.get("ui_mode"))

    def test_late_write_cache_publication_cannot_override_newer_commit_or_clear(self) -> None:
        for update_kind in ("external_clear", "local_write"):
            with self.subTest(update_kind=update_kind), tempfile.TemporaryDirectory() as temporary_directory:
                path = Path(temporary_directory) / "monitor.sqlite3"
                database = Database(path)
                database.initialize()
                repository = database.settings

                def update(native_connect):
                    if update_kind == "local_write":
                        repository.set("ui_mode", "newer")
                    else:
                        with closing(native_connect(path)) as connection, connection:
                            connection.execute("UPDATE settings SET value=? WHERE key=?", ("newer", "ui_mode"))
                        repository.clear_cache()

                self.assertEqual(
                    [None],
                    self._while_connection_close_is_paused(path, lambda: repository.set("ui_mode", "older"), update),
                )
                self.assertEqual("newer", repository.get("ui_mode"))
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual("newer", connection.execute("SELECT value FROM settings WHERE key='ui_mode'").fetchone()[0])

    def test_write_failure_and_commit_ack_cleanup_cannot_leave_stale_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            repository = database.settings
            self.assertEqual("responsive", repository.get("ui_mode"))
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TRIGGER reject_setting BEFORE UPDATE ON settings "
                    "WHEN NEW.value='reject' BEGIN SELECT RAISE(ABORT,'rejected setting'); END"
                )
            with self.assertRaisesRegex(sqlite3.IntegrityError, "rejected setting"):
                repository.set("ui_mode", "reject")
            self.assertEqual("responsive", repository.get("ui_mode"))

            native_connect = sqlite3.connect

            class CloseFailureConnection:
                def __init__(self, connection):
                    self._connection = connection

                def __getattr__(self, name):
                    return getattr(self._connection, name)

                def close(self):
                    self._connection.close()
                    raise RuntimeError("close failed after native commit")

            with patch(
                "kiwoom_monitor.infrastructure.persistence.settings_repository.sqlite3.connect",
                side_effect=lambda *args, **kwargs: CloseFailureConnection(native_connect(*args, **kwargs)),
            ):
                with self.assertRaisesRegex(RuntimeError, "close failed after native commit"):
                    repository.set("ui_mode", "committed")
            self.assertEqual("committed", repository.get("ui_mode"))
            repository.set("ui_mode", "retried")
            self.assertEqual("retried", repository.get("ui_mode"))


class SettingsNoopContractTests(unittest.TestCase):
    def test_native_noop_costs_preserve_versions_and_first_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'settings.sqlite'
            old = '2026-10-20T09:00:00+09:00'
            now = datetime.fromisoformat('2026-10-20T10:00:00+09:00')
            values = {'a': 'one', 'b': 'two', 'c': '', 'd': 'four'}
            native_connect = sqlite3.connect
            with closing(native_connect(path)) as connection, connection:
                connection.executescript('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);'
                    'CREATE TABLE central_setting_versions(setting_key TEXT PRIMARY KEY,updated_at TEXT NOT NULL);')
                connection.executemany('INSERT INTO settings VALUES(?,?)', values.items())
                connection.executemany('INSERT INTO central_setting_versions VALUES(?,?)', [(k, old) for k in values])
            clock = Mock(return_value=now)
            repository = SettingsRepository(path, now_provider=clock)
            def measure(batch):
                statements, changes, commits, connections = [], [], [], []
                class Connection(sqlite3.Connection):
                    def __init__(self, *args, **kwargs):
                        super().__init__(*args, **kwargs)
                        connections.append(1)
                        self.set_trace_callback(statements.append)
                    def commit(self):
                        commits.append(1)
                        return super().commit()
                    def close(self):
                        changes.append(self.total_changes)
                        return super().close()
                clock.reset_mock()
                with patch('kiwoom_monitor.infrastructure.persistence.settings_repository.sqlite3.connect',
                           side_effect=lambda *a, **kw: native_connect(*a, **kw, factory=Connection)):
                    repository.set_many(batch)
                self.assertEqual((1, 1, 1), (len(connections), len(commits), clock.call_count))
                self.assertEqual(2 * len(batch), sum(s.startswith('INSERT') for s in statements))
                self.assertEqual(0, sum(s.startswith('SELECT') for s in statements))  # Inline revision lookup is inside the UPSERT.
                self.assertEqual(1, sum(s.startswith('BEGIN') for s in statements))
                return sum(changes)
            self.assertEqual(0, measure(values))
            with closing(native_connect(path)) as connection:
                self.assertEqual([(k, old) for k in values], connection.execute(
                    'SELECT setting_key,updated_at FROM central_setting_versions ORDER BY setting_key').fetchall())
            self.assertEqual(2, measure({'a': 'edited', 'b': 'two'}))
            with closing(native_connect(path)) as connection, connection:
                self.assertEqual(now.isoformat(), connection.execute(
                    "SELECT updated_at FROM central_setting_versions WHERE setting_key='a'").fetchone()[0])
                self.assertEqual(old, connection.execute(
                    "SELECT updated_at FROM central_setting_versions WHERE setting_key='b'").fetchone()[0])
                connection.execute("DELETE FROM central_setting_versions WHERE setting_key='b'")
            self.assertEqual(1, measure({'b': 'two'}))
            self.assertEqual(2, measure({'new': 'born'}))
            self.assertEqual('edited', repository.get('a'))
            self.assertEqual('born', SettingsRepository(path).get('new'))

    def test_value_version_and_commit_failures_roll_back_together_before_retry(self) -> None:
        for failure in ('version', 'value', 'commit'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'settings.sqlite'
                native_connect = sqlite3.connect
                old = '2026-10-20T09:00:00+09:00'
                now = datetime.fromisoformat('2026-10-20T10:00:00+09:00')
                with closing(native_connect(path)) as connection, connection:
                    connection.executescript('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);'
                        'CREATE TABLE central_setting_versions(setting_key TEXT PRIMARY KEY,updated_at TEXT NOT NULL);')
                    connection.execute("INSERT INTO settings VALUES('a','old')")
                    connection.execute("INSERT INTO central_setting_versions VALUES('a',?)", (old,))
                    if failure != 'commit':
                        table = 'central_setting_versions' if failure == 'version' else 'settings'
                        connection.execute(f"CREATE TRIGGER reject_write BEFORE UPDATE ON {table} "
                                           "BEGIN SELECT RAISE(ABORT,'controlled write failure'); END")
                repository = SettingsRepository(path, now_provider=lambda: now)
                self.assertEqual('old', repository.get('a'))
                class CommitFailure(sqlite3.Connection):
                    def commit(self): raise sqlite3.OperationalError('controlled commit failure')
                def connect(*args, **kwargs):
                    return native_connect(*args, **kwargs, factory=CommitFailure) if failure == 'commit' else native_connect(*args, **kwargs)
                with patch('kiwoom_monitor.infrastructure.persistence.settings_repository.sqlite3.connect', side_effect=connect):
                    with self.assertRaisesRegex(sqlite3.Error, 'controlled'):
                        repository.set_many({'a': 'new'})
                with closing(native_connect(path)) as connection, connection:
                    self.assertEqual(('old', old), connection.execute('SELECT s.value,v.updated_at FROM settings s '
                        'JOIN central_setting_versions v ON v.setting_key=s.key').fetchone())
                    connection.execute('DROP TRIGGER IF EXISTS reject_write')
                self.assertEqual('old', repository.get('a'))
                repository.set_many({'a': 'new'})
                self.assertEqual('new', repository.get('a'))
                with closing(native_connect(path)) as connection:
                    self.assertEqual(('new', now.isoformat()), connection.execute('SELECT s.value,v.updated_at FROM settings s '
                        'JOIN central_setting_versions v ON v.setting_key=s.key').fetchone())


if __name__ == "__main__":
    unittest.main()
