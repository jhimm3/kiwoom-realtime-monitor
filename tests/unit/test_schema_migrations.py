from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.database import Database, MAIN_SCHEMA_VERSION
from kiwoom_monitor.infrastructure.persistence.journal_schema import (
    JOURNAL_SCHEMA_VERSION,
    _create_journal_enrichment_tables,
    _migrate_account_scoped_journal_artifacts,
    _migrate_legacy_import_provenance_v8,
    initialize_journal_database,
)
from kiwoom_monitor.infrastructure.persistence.schema_migrations import (
    SQLiteMigration,
    SQLiteMigrationRunner,
    SchemaMigrationError,
)


class SchemaMigrationTests(unittest.TestCase):
    def test_v6_preserves_snapshot_review_and_research_references_as_legacy(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            connection.executescript("""
                CREATE TABLE trade_group_overrides(fill_key TEXT PRIMARY KEY,group_id TEXT,updated_at TEXT);
                CREATE TABLE trade_reviews(group_id TEXT PRIMARY KEY,review TEXT);
                CREATE TABLE trade_setup_classifications(group_id TEXT PRIMARY KEY,automatic_type TEXT);
                CREATE TABLE trade_setup_cycle_overrides(group_id TEXT,cycle_index INTEGER);
                CREATE TABLE trade_entry_snapshots(
                    execution_key TEXT PRIMARY KEY,stock_code TEXT,executed_at TEXT,news_json TEXT
                );
                INSERT INTO trade_group_overrides VALUES('fill-old','group-old','2026-09-12T10:00:00');
                INSERT INTO trade_reviews VALUES('group-old','사용자 복기');
                INSERT INTO trade_setup_classifications VALUES('group-old','돌파');
                INSERT INTO trade_setup_cycle_overrides VALUES('group-old',0);
                INSERT INTO trade_entry_snapshots VALUES(
                    'execution-old','005930','2026-09-12T09:01:00','[{"title":"기존 뉴스"}]'
                );
            """)
            _create_journal_enrichment_tables(connection)
            connection.execute(
                "INSERT INTO journal_research_links VALUES(?,?,?,?,?,?,?,?,?)",
                ("link-old", "execution-old", "run-old", None, "snapshot-old", None,
                 "at_execution", "2026-09-12T09:01:00", "2026-09-12T20:00:00"),
            )
            with connection:
                _migrate_account_scoped_journal_artifacts(connection)

            snapshot = connection.execute(
                "SELECT execution_key,news_json,origin_account_ref FROM trade_entry_snapshots"
            ).fetchone()
            review = connection.execute(
                "SELECT group_id,review,origin_account_ref FROM trade_reviews"
            ).fetchone()
            link = connection.execute(
                "SELECT link_id,execution_ref,snapshot_id,origin_account_ref FROM journal_research_links"
            ).fetchone()
            self.assertEqual(
                ("execution-old", '[{"title":"기존 뉴스"}]', "legacy-unassigned"), snapshot,
            )
            self.assertEqual(("group-old", "사용자 복기", "legacy-unassigned"), review)
            self.assertEqual(
                ("link-old", "execution-old", "snapshot-old", "legacy-unassigned"), link,
            )
        finally:
            connection.close()

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
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "CREATE TABLE trade_fills("
                    "order_no TEXT NOT NULL,stock_code TEXT NOT NULL,stock_name TEXT NOT NULL,"
                    "side TEXT NOT NULL,filled_at TEXT NOT NULL,quantity INTEGER NOT NULL,"
                    "price INTEGER NOT NULL,order_type TEXT NOT NULL DEFAULT '',market TEXT NOT NULL DEFAULT '',"
                    "PRIMARY KEY(order_no,stock_code,filled_at,side))"
                )
                connection.execute(
                    "INSERT INTO trade_fills VALUES(?,?,?,?,?,?,?,?,?)",
                    ("1", "005930", "삼성전자", "매수", "2026-09-10T09:01:00", 1, 70000, "", "KRX"),
                )
                connection.execute(
                    "CREATE TABLE daily_trade_costs("
                    "fill_date TEXT,settlement_date TEXT,stock_code TEXT,side TEXT,"
                    "gross_amount INTEGER,settlement_amount INTEGER,commission INTEGER,tax INTEGER,"
                    "total_cost INTEGER,confirmed_at TEXT,PRIMARY KEY(fill_date,stock_code,side))"
                )
                connection.execute(
                    "INSERT INTO daily_trade_costs VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("2026-09-10", "2026-09-12", "005930", "매수", 70000, 69990, 10, 0, 10,
                     "2026-09-12T20:05:00"),
                )
                connection.execute(
                    "CREATE TABLE trade_group_overrides("
                    "fill_key TEXT PRIMARY KEY,group_id TEXT NOT NULL,updated_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO trade_group_overrides VALUES(?,?,?)",
                    ("1|005930|2026-09-10T09:01:00|매수", "manual:kept", "2026-09-10T10:00:00"),
                )
                connection.commit()
            initialize_journal_database(path)
            with closing(sqlite3.connect(path)) as connection:
                count = connection.execute("SELECT count(*) FROM trade_fills").fetchone()[0]
                version = connection.execute(
                    "SELECT max(version) FROM journal_schema_migrations"
                ).fetchone()[0]
            self.assertEqual(1, count)
            self.assertEqual(JOURNAL_SCHEMA_VERSION, version)
            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute(
                    "SELECT fill_key,origin_broker,origin_environment,origin_account_ref,"
                    "canonical_account_ref FROM trade_fills"
                ).fetchone()
                cost_scope = connection.execute(
                    "SELECT origin_broker,origin_environment,origin_account_ref,canonical_account_ref "
                    "FROM daily_trade_costs"
                ).fetchone()
                override = connection.execute(
                    "SELECT fill_key,group_id,origin_broker,origin_environment,"
                    "origin_account_ref,canonical_account_ref FROM trade_group_overrides"
                ).fetchone()
            self.assertEqual(
                ("1|005930|2026-09-10T09:01:00|매수", "legacy", "unknown",
                 "legacy-unassigned", "legacy-unassigned"),
                row,
            )
            self.assertEqual(
                ("legacy", "unknown", "legacy-unassigned", "legacy-unassigned"),
                cost_scope,
            )
            self.assertEqual(
                ("1|005930|2026-09-10T09:01:00|매수", "manual:kept", "legacy", "unknown",
                 "legacy-unassigned", "legacy-unassigned"), override,
            )

    def test_failed_account_scope_migration_restores_v4_table_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "CREATE TABLE trade_fills("
                    "order_no TEXT,stock_code TEXT,stock_name TEXT,side TEXT,filled_at TEXT,"
                    "quantity INTEGER,price INTEGER,order_type TEXT,"
                    "PRIMARY KEY(order_no,stock_code,filled_at,side))"
                )
                connection.execute(
                    "INSERT INTO trade_fills VALUES(?,?,?,?,?,?,?,?)",
                    ("1", "005930", "삼성전자", "매수", "2026-09-10T09:01:00", 1, 70000, ""),
                )
                connection.execute(
                    "CREATE TABLE journal_schema_migrations("
                    "version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL)"
                )
                connection.executemany(
                    "INSERT INTO journal_schema_migrations VALUES(?,?,?)",
                    (
                        (1, "current_journal_schema_baseline", "2026-09-10"),
                        (2, "market_data_observation_metadata", "2026-09-10"),
                        (3, "journal_sync_deletion_states", "2026-09-10"),
                        (4, "journal_enrichment_ledger", "2026-09-10"),
                    ),
                )
                connection.commit()

            with self.assertRaises(sqlite3.OperationalError):
                initialize_journal_database(path)

            with closing(sqlite3.connect(path)) as connection:
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                version = connection.execute(
                    "SELECT max(version) FROM journal_schema_migrations"
                ).fetchone()[0]
                fill = connection.execute("SELECT order_no,stock_code FROM trade_fills").fetchone()
            self.assertIn("trade_fills", tables)
            self.assertNotIn("trade_fills_v4", tables)
            self.assertEqual(4, version)
            self.assertEqual(("1", "005930"), fill)

    def test_failed_v8_migration_restores_v7_ledger_and_version(self) -> None:
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute(
                "CREATE TABLE journal_legacy_imports("
                "source_collection TEXT,source_key TEXT,source_revision TEXT,"
                "target_collection TEXT,target_key TEXT,imported_at TEXT)"
            )
            connection.execute(
                "INSERT INTO journal_legacy_imports VALUES(?,?,?,?,?,?)",
                ("journal_reviews", "group-1", "2026-09-13T10:00:00", "trade_reviews",
                 "group-1", "2026-09-13T10:01:00"),
            )
            runner = SQLiteMigrationRunner(connection, "journal_schema_migrations")
            runner.prepare()
            names = (
                "current_journal_schema_baseline", "market_data_observation_metadata",
                "journal_sync_deletion_states", "journal_enrichment_ledger",
                "account_scoped_trade_ledger", "account_scoped_journal_artifacts",
                "legacy_import_provenance",
            )
            for version, name in enumerate(names, 1):
                runner.record_applied(version, name)
            connection.commit()
            migrations = tuple(
                SQLiteMigration(version, name, lambda _: None)
                for version, name in enumerate(names, 1)
            ) + (SQLiteMigration(
                8, "strict_legacy_import_provenance", _migrate_legacy_import_provenance_v8,
            ),)

            with self.assertRaises(sqlite3.OperationalError):
                runner.apply(migrations)

            row = connection.execute(
                "SELECT source_collection,source_key,source_revision,target_key "
                "FROM journal_legacy_imports"
            ).fetchone()
            version = connection.execute(
                "SELECT MAX(version) FROM journal_schema_migrations"
            ).fetchone()[0]
            renamed = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='journal_legacy_imports_v7'"
            ).fetchone()
            self.assertEqual(("journal_reviews", "group-1", "2026-09-13T10:00:00", "group-1"), row)
            self.assertEqual(7, version)
            self.assertIsNone(renamed)


if __name__ == "__main__":
    unittest.main()
