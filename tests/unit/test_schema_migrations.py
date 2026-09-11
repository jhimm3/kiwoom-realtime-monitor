from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.database import Database, MAIN_SCHEMA_VERSION
from kiwoom_monitor.infrastructure.persistence.journal_schema import (
    JOURNAL_SCHEMA_VERSION,
    initialize_journal_database,
)
from kiwoom_monitor.infrastructure.persistence.schema_migrations import (
    SQLiteMigration,
    SQLiteMigrationRunner,
    SchemaMigrationError,
)


class SchemaMigrationTests(unittest.TestCase):
    def test_runner_applies_each_version_once_and_preserves_data(self) -> None:
        with closing(sqlite3.connect(":memory:")) as connection:
            plan = (
                SQLiteMigration(1, "create_items", lambda db: db.execute(
                    "CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)"
                )),
                SQLiteMigration(2, "seed_item", lambda db: db.execute(
                    "INSERT INTO items(value) VALUES('kept')"
                )),
            )
            runner = SQLiteMigrationRunner(connection)
            self.assertEqual((1, 2), runner.apply(plan))
            self.assertEqual((), runner.apply(plan))
            self.assertEqual([("kept",)], connection.execute("SELECT value FROM items").fetchall())

    def test_failed_migration_rolls_back_and_is_not_recorded(self) -> None:
        with closing(sqlite3.connect(":memory:")) as connection:
            runner = SQLiteMigrationRunner(connection)

            def fail(db: sqlite3.Connection) -> None:
                db.execute("CREATE TABLE unfinished(value TEXT)")
                raise RuntimeError("stop")

            with self.assertRaisesRegex(RuntimeError, "stop"):
                runner.apply((SQLiteMigration(1, "fails", fail),))
            versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='unfinished'"
            ).fetchone()
            self.assertEqual([], versions)
            self.assertIsNone(table)

    def test_newer_database_and_changed_migration_identity_are_rejected(self) -> None:
        with closing(sqlite3.connect(":memory:")) as connection:
            runner = SQLiteMigrationRunner(connection)
            runner.prepare()
            runner.record_applied(2, "future")
            with self.assertRaisesRegex(SchemaMigrationError, "newer"):
                runner.ensure_compatible(1)
            with self.assertRaisesRegex(SchemaMigrationError, "already recorded"):
                runner.record_applied(2, "renamed")

    def test_main_database_records_current_baseline_and_keeps_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            database.settings.set("rank_query_type", "3")
            # 기존 앱이 만들던 version 단일 열 표로 되돌려 업그레이드를 재현한다.
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("DROP TABLE schema_migrations")
                connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)")
                connection.execute("INSERT INTO schema_migrations VALUES(1)")
                connection.commit()
            database.initialize()
            with closing(sqlite3.connect(path)) as connection:
                versions = connection.execute(
                    "SELECT version,name FROM schema_migrations ORDER BY version"
                ).fetchall()
            self.assertEqual(MAIN_SCHEMA_VERSION, versions[-1][0])
            self.assertEqual("3", database.settings.get("rank_query_type"))

    def test_existing_journal_data_survives_baseline_registration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            initialize_journal_database(path)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "INSERT INTO trade_fills VALUES(?,?,?,?,?,?,?,?,?)",
                    ("1", "005930", "삼성전자", "매수", "2026-09-10T09:01:00", 1, 70000, "", "KRX"),
                )
                # 마이그레이션 표가 없던 직전 매매일지 DB를 재현한다.
                connection.execute("DROP TABLE journal_schema_migrations")
                connection.commit()
            initialize_journal_database(path)
            with closing(sqlite3.connect(path)) as connection:
                count = connection.execute("SELECT count(*) FROM trade_fills").fetchone()[0]
                version = connection.execute(
                    "SELECT max(version) FROM journal_schema_migrations"
                ).fetchone()[0]
            self.assertEqual(1, count)
            self.assertEqual(JOURNAL_SCHEMA_VERSION, version)


if __name__ == "__main__":
    unittest.main()
