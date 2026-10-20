from __future__ import annotations
import sqlite3, tempfile, unittest
from contextlib import closing
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import Mock, patch
from kiwoom_monitor.domain.time_basis import KST
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSetting, ColumnSettingsRepository

class ColumnSettingsRepositoryTests(unittest.TestCase):
    def _seed(self, path):
        old = "2026-10-20T09:00:00+09:00"
        values = (ColumnSetting("a", True, 0, 100), ColumnSetting("b", False, 1, 150))
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.executescript(
                "CREATE TABLE column_settings(column_name TEXT PRIMARY KEY,visible INTEGER,position INTEGER,width INTEGER);"
                "CREATE TABLE central_column_setting_versions(column_name TEXT PRIMARY KEY,updated_at TEXT NOT NULL);"
            )
            connection.executemany("INSERT INTO column_settings VALUES(?,?,?,?)",
                                   [(s.name, int(s.visible), s.position, s.width) for s in values])
            connection.executemany("INSERT INTO central_column_setting_versions VALUES(?,?)",
                                   [(s.name, old) for s in values])
        return old, values

    def _measure(self, repository, operation):
        native_connect = sqlite3.connect
        changes, statements, connections, commits = [], [], [], []

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

        with patch("kiwoom_monitor.infrastructure.persistence.column_settings_repository.sqlite3.connect",
                   side_effect=lambda *a, **kw: native_connect(*a, **kw, factory=Connection)):
            operation()
        self.assertEqual((1, 1), (len(connections), len(commits)))
        self.assertFalse(any(s.startswith("SELECT") for s in statements))
        return sum(changes), statements

    def test_saves_and_restores_column_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"; Database(path).initialize()
            now = datetime(2026, 10, 18, 9, tzinfo=KST)
            repository = ColumnSettingsRepository(path, now_provider=lambda: now)
            saved = list(repository.list()); saved[0] = ColumnSetting(saved[0].name, False, 2, 123); repository.save(tuple(saved))
            restored = next(item for item in repository.list() if item.name == saved[0].name)
            self.assertFalse(restored.visible); self.assertEqual(123, restored.width)
            self.assertEqual(2, restored.position)
            connection = sqlite3.connect(path)
            self.assertEqual((now.isoformat(),), connection.execute(
                "SELECT updated_at FROM central_column_setting_versions WHERE column_name=?",
                (saved[0].name,),
            ).fetchone())
            connection.close()
            now += timedelta(seconds=1)
            repository.reset()
            restored = next(item for item in repository.list() if item.name == saved[0].name)
            self.assertTrue(restored.visible); self.assertEqual(32, restored.width); self.assertEqual(0, restored.position)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual((now.isoformat(),), connection.execute(
                    "SELECT updated_at FROM central_column_setting_versions WHERE column_name=?",
                    (saved[0].name,),
                ).fetchone())

    def test_native_noop_preserves_versions_and_registers_only_missing_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "columns.sqlite"
            old, values = self._seed(path)
            now = datetime(2026, 10, 20, 10, tzinfo=KST)
            clock = Mock(return_value=now)
            repository = ColumnSettingsRepository(path, now_provider=clock)
            changed, statements = self._measure(repository, lambda: repository.save(values))
            self.assertEqual(0, changed)
            self.assertEqual((2, 2, 1), (
                sum(s.startswith("UPDATE") for s in statements),
                sum(s.startswith("INSERT") for s in statements),
                clock.call_count,
            ))
            with closing(sqlite3.connect(path)) as connection, connection:
                self.assertEqual([("a", old), ("b", old)], connection.execute(
                    "SELECT column_name,updated_at FROM central_column_setting_versions ORDER BY column_name"
                ).fetchall())
                connection.execute("DELETE FROM central_column_setting_versions WHERE column_name='a'")
            self.assertEqual(1, self._measure(repository, lambda: repository.save(values))[0])
            for changed_value in (ColumnSetting("a", True, 0, 111),
                                  ColumnSetting("a", False, 0, 111),
                                  ColumnSetting("a", False, 2, 111)):
                now += timedelta(seconds=1)
                clock.return_value = now
                self.assertEqual(2, self._measure(repository, lambda: repository.save((changed_value, values[1])))[0])
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(now.isoformat(), connection.execute(
                        "SELECT updated_at FROM central_column_setting_versions WHERE column_name='a'"
                    ).fetchone()[0])
                    self.assertEqual(old, connection.execute(
                        "SELECT updated_at FROM central_column_setting_versions WHERE column_name='b'"
                    ).fetchone()[0])
            self.assertEqual(changed_value, next(s for s in repository.list() if s.name == "a"))

    def test_current_database_empty_unknown_and_duplicate_input_contracts_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "columns.sqlite"
            old, values = self._seed(path)
            now = datetime(2026, 10, 20, 10, tzinfo=KST)
            clock = Mock(return_value=now)
            repository = ColumnSettingsRepository(path, now_provider=clock)
            stale_snapshot = repository.list()
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("UPDATE column_settings SET width=222 WHERE column_name='a'")
            self.assertEqual(2, self._measure(repository, lambda: repository.save(stale_snapshot))[0])
            self.assertEqual(values, repository.list())
            clock.reset_mock()
            self.assertEqual(0, self._measure(repository, lambda: repository.save(()))[0])
            clock.assert_called_once_with()
            unknown = ColumnSetting("unknown", True, 7, 777)
            self.assertEqual(1, self._measure(repository, lambda: repository.save((unknown,)))[0])
            now += timedelta(seconds=1)
            clock.return_value = now
            self.assertEqual(1, self._measure(repository, lambda: repository.save((unknown,)))[0])
            with closing(sqlite3.connect(path)) as connection:
                self.assertIsNone(connection.execute(
                    "SELECT 1 FROM column_settings WHERE column_name='unknown'"
                ).fetchone())
                self.assertEqual(now.isoformat(), connection.execute(
                    "SELECT updated_at FROM central_column_setting_versions WHERE column_name='unknown'"
                ).fetchone()[0])
            last = ColumnSetting("a", False, 8, 333)
            repository.save((values[0], last))
            self.assertEqual(last, next(s for s in repository.list() if s.name == "a"))
            self.assertEqual(0, self._measure(repository, lambda: repository.save((last, last)))[0])

    def test_explicit_reset_keeps_full_edit_versions_and_atomic_clock_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            now = datetime(2026, 10, 20, 10, tzinfo=KST)
            clock = Mock(return_value=now)
            repository = ColumnSettingsRepository(path, now_provider=clock)
            defaults = repository.list()
            repository.save(defaults)
            now += timedelta(seconds=1)
            clock.return_value = now
            clock.reset_mock()
            self.assertEqual(2 * len(defaults), self._measure(repository, repository.reset)[0])
            clock.assert_called_once_with()
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual({now.isoformat()}, {row[0] for row in connection.execute(
                    "SELECT updated_at FROM central_column_setting_versions"
                )})
            edited = ColumnSetting(defaults[0].name, False, 9, 123)
            repository.save((edited,))
            before = repository.list()
            clock.side_effect = RuntimeError("controlled clock failure")
            with self.assertRaisesRegex(RuntimeError, "controlled clock failure"):
                repository.reset()
            self.assertEqual(before, repository.list())
            clock.side_effect = None
            repository.reset()
            self.assertEqual(defaults, repository.list())

    def test_save_version_value_clock_and_commit_failures_roll_back_before_retry(self) -> None:
        for failure in ("version", "value", "clock", "commit"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "columns.sqlite"
                old, values = self._seed(path)
                now = datetime(2026, 10, 20, 10, tzinfo=KST)
                clock = Mock(return_value=now)
                if failure == "clock":
                    clock.side_effect = RuntimeError("controlled clock failure")
                elif failure != "commit":
                    table = "central_column_setting_versions" if failure == "version" else "column_settings"
                    with closing(sqlite3.connect(path)) as connection:
                        connection.execute(f"CREATE TRIGGER reject_write BEFORE UPDATE ON {table} "
                            "WHEN NEW.column_name='b' BEGIN SELECT RAISE(ABORT,'controlled write failure'); END")
                repository = ColumnSettingsRepository(path, now_provider=clock)
                batch = (ColumnSetting("a", False, 5, 300), ColumnSetting("b", True, 6, 400))
                native_connect = sqlite3.connect

                class CommitFailure(sqlite3.Connection):
                    def commit(self):
                        raise sqlite3.OperationalError("controlled commit failure")

                def connect(*args, **kwargs):
                    return native_connect(*args, **kwargs, factory=CommitFailure) if failure == "commit" else native_connect(*args, **kwargs)

                with patch("kiwoom_monitor.infrastructure.persistence.column_settings_repository.sqlite3.connect", side_effect=connect):
                    with self.assertRaisesRegex((sqlite3.Error, RuntimeError), "controlled"):
                        repository.save(batch)
                self.assertEqual(values, repository.list())
                with closing(native_connect(path)) as connection:
                    self.assertEqual([("a", old), ("b", old)], connection.execute(
                        "SELECT column_name,updated_at FROM central_column_setting_versions ORDER BY column_name"
                    ).fetchall())
                    connection.execute("DROP TRIGGER IF EXISTS reject_write")
                clock.side_effect = None
                repository.save(batch)
                self.assertEqual(batch, repository.list())
                with closing(native_connect(path)) as connection:
                    self.assertEqual({now.isoformat()}, {row[0] for row in connection.execute(
                        "SELECT updated_at FROM central_column_setting_versions"
                    )})

    def test_nonempty_save_samples_revision_after_acquiring_native_writer_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "columns.sqlite"
            self._seed(path)
            observations = []

            def now():
                with closing(sqlite3.connect(path, timeout=0)) as competing_writer:
                    try:
                        competing_writer.execute("BEGIN IMMEDIATE")
                    except sqlite3.OperationalError as error:
                        if "locked" not in str(error).lower():
                            raise
                        observations.append("blocked")
                    else:
                        observations.append("acquired")
                        competing_writer.rollback()
                return datetime(2026, 10, 20, 10, tzinfo=KST)

            repository = ColumnSettingsRepository(path, now_provider=now)
            repository.save((ColumnSetting("a", False, 8, 333),))
            self.assertEqual(["blocked"], observations)
            observations.clear()
            repository.save(())
            self.assertEqual(["acquired"], observations)

            legacy_path = Path(directory) / "legacy.sqlite"
            with closing(sqlite3.connect(legacy_path)) as connection:
                connection.execute(
                    "CREATE TABLE column_settings(column_name TEXT PRIMARY KEY,visible INTEGER,position INTEGER,width INTEGER)"
                )
            legacy = ColumnSettingsRepository(
                legacy_path, now_provider=Mock(side_effect=RuntimeError("controlled clock failure")),
            )
            with self.assertRaisesRegex(RuntimeError, "controlled clock failure"):
                legacy.save(())
            with closing(sqlite3.connect(legacy_path)) as connection:
                self.assertIsNone(connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='central_column_setting_versions'"
                ).fetchone())
