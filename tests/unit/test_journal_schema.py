from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.journal_schema import (
    JOURNAL_SCHEMA_INDEXES, JOURNAL_SCHEMA_TABLES, JOURNAL_SCHEMA_VERSION, initialize_journal_database,
)


EXPECTED_TABLES = {
    "journal_minute_bars",
    "journal_stocks",
    "trade_fills",
    "trade_group_overrides",
    "trade_reviews",
    "daily_trade_costs",
    "journal_bar_backfill",
    "journal_daily_bars",
    "journal_settings",
    "trade_setup_classifications",
    "trade_setup_cycle_overrides",
    "trade_entry_snapshots",
    "journal_sync_states",
    "journal_enrichment_tasks",
    "journal_analysis_revisions",
    "journal_research_links",
    "journal_execution_projection_states",
    "journal_execution_event_projections",
    "journal_selected_settings_receipts",
}


class JournalSchemaTests(unittest.TestCase):
    def test_v9_setup_key_migration_preserves_unassigned_bytes_and_allows_explicit_scoped_edit(self):
        from datetime import datetime
        from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope, LEGACY_ACCOUNT_SCOPE
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            initialize_journal_database(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("DROP TABLE trade_setup_classifications")
                connection.execute("CREATE TABLE trade_setup_classifications(group_id TEXT PRIMARY KEY,automatic_type TEXT NOT NULL,confidence INTEGER NOT NULL,evidence_json TEXT NOT NULL,manual_type TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,origin_broker TEXT NOT NULL DEFAULT 'legacy',origin_environment TEXT NOT NULL DEFAULT 'unknown',origin_account_ref TEXT NOT NULL DEFAULT 'legacy-unassigned',canonical_account_ref TEXT NOT NULL DEFAULT 'legacy-unassigned')")
                connection.execute("DROP TABLE trade_setup_cycle_overrides")
                connection.execute("CREATE TABLE trade_setup_cycle_overrides(group_id TEXT NOT NULL,cycle_index INTEGER NOT NULL,manual_type TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,origin_broker TEXT NOT NULL DEFAULT 'legacy',origin_environment TEXT NOT NULL DEFAULT 'unknown',origin_account_ref TEXT NOT NULL DEFAULT 'legacy-unassigned',canonical_account_ref TEXT NOT NULL DEFAULT 'legacy-unassigned',PRIMARY KEY(group_id,cycle_index))")
                connection.execute("DROP TABLE journal_selected_settings_receipts")
                connection.execute("ALTER TABLE journal_execution_event_projections DROP COLUMN order_plan_json")
                connection.execute("DELETE FROM journal_schema_migrations WHERE version>=10")
                connection.execute("INSERT INTO trade_setup_classifications VALUES(?,?,?,?,?,?,?,?,?,?)", ("same", "기타", 7, '[ "원문" ]', "상따", "2026-09-01T09:00:00", "legacy", "unknown", "legacy-unassigned", "legacy-unassigned"))
                connection.execute("INSERT INTO trade_setup_cycle_overrides VALUES(?,?,?,?,?,?,?,?)", ("same", 0, "돌파", "2026-09-01T09:00:01", "legacy", "unknown", "legacy-unassigned", "legacy-unassigned"))
                before = tuple(connection.execute("SELECT * FROM trade_setup_classifications").fetchone())
                before_override = tuple(connection.execute("SELECT * FROM trade_setup_cycle_overrides").fetchone())
                connection.execute("CREATE TRIGGER reject_v10 BEFORE INSERT ON journal_schema_migrations WHEN NEW.version=10 BEGIN SELECT RAISE(ABORT,'migration receipt rejected'); END")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "migration receipt rejected"):
                JournalRepository(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                self.assertEqual(before, tuple(connection.execute("SELECT * FROM trade_setup_classifications").fetchone()))
                self.assertEqual(before_override, tuple(connection.execute("SELECT * FROM trade_setup_cycle_overrides").fetchone()))
                self.assertEqual(("group_id",), tuple(row[1] for row in connection.execute("PRAGMA table_info(trade_setup_classifications)") if row[5]))
                self.assertEqual(("group_id", "cycle_index"), tuple(row[1] for row in connection.execute("PRAGMA table_info(trade_setup_cycle_overrides)") if row[5]))
                self.assertEqual(9, connection.execute("SELECT MAX(version) FROM journal_schema_migrations").fetchone()[0])
                connection.execute("DROP TRIGGER reject_v10")
            repository = JournalRepository(path)
            scope = AccountScope("kiwoom", AccountEnvironment.MOCK, "11111111-1111-4111-8111-111111111111")
            repository.save_trade_setup_cycle_override("same", 0, "눌림", now=datetime(2026, 10, 23, 10), account_scope=scope)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(before, tuple(connection.execute("SELECT * FROM trade_setup_classifications").fetchone()))
                self.assertEqual(before_override, tuple(connection.execute("SELECT * FROM trade_setup_cycle_overrides WHERE origin_broker='legacy'").fetchone()))
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM trade_setup_cycle_overrides").fetchone()[0])
            reopened = JournalRepository(path)
            self.assertEqual({0: "돌파"}, reopened.load_trade_setup_cycle_overrides("same", LEGACY_ACCOUNT_SCOPE))
            self.assertEqual({0: "눌림"}, reopened.load_trade_setup_cycle_overrides("same", scope))
            repository.save_trade_setup_cycle_override("same", 0, "", now=datetime(2026, 10, 23, 11), account_scope=scope)
            self.assertEqual({0: "돌파"}, reopened.load_trade_setup_cycle_overrides("same", LEGACY_ACCOUNT_SCOPE))
            self.assertEqual({}, reopened.load_trade_setup_cycle_overrides("same", scope))

    def test_initialization_creates_all_journal_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"

            initialize_journal_database(path)
            with closing(sqlite3.connect(path)) as connection:
                tables = {
                    str(row[0]) for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }

            self.assertTrue(EXPECTED_TABLES.issubset(tables))
            with closing(sqlite3.connect(path)) as connection:
                objects = connection.execute("SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            self.assertEqual(JOURNAL_SCHEMA_TABLES, {name for name, kind in objects if kind == "table"})
            self.assertEqual(JOURNAL_SCHEMA_INDEXES, {name for name, kind in objects if kind == "index"})

    def test_initialization_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"

            initialize_journal_database(path)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "INSERT INTO journal_settings VALUES(?,?,?)",
                    ("kept", '{"value":7}', "2026-10-18T09:00:00+09:00"),
                )
                connection.commit()
            initialize_journal_database(path)

            with closing(sqlite3.connect(path)) as connection:
                count = connection.execute("SELECT count(*) FROM trade_fills").fetchone()[0]
                kept = connection.execute(
                    "SELECT value_json,updated_at FROM journal_settings WHERE setting_key='kept'"
                ).fetchone()
            self.assertEqual(0, count)
            self.assertEqual(('{"value":7}', "2026-10-18T09:00:00+09:00"), kept)

    def test_version_8_fixture_adds_execution_projection_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            initialize_journal_database(path)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("DROP TABLE journal_execution_event_projections")
                connection.execute("DROP TABLE journal_execution_projection_states")
                connection.execute("DELETE FROM journal_schema_migrations WHERE version IN (9,12)")
                connection.commit()

            initialize_journal_database(path)

            with closing(sqlite3.connect(path)) as connection:
                version = connection.execute(
                    "SELECT max(version) FROM journal_schema_migrations"
                ).fetchone()[0]
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            self.assertEqual(JOURNAL_SCHEMA_VERSION, version)
            self.assertIn("journal_execution_event_projections", tables)
            self.assertIn("journal_execution_projection_states", tables)

    def test_selected_settings_receipt_migration_preserves_prior_ddl_rows_and_migration_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"
            initialize_journal_database(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("DROP TABLE journal_selected_settings_receipts")
                connection.execute("DELETE FROM journal_schema_migrations WHERE version=11")
                row = ("personal_trade_rules", b"\x00original\xff", "unmodified-original-time")
                connection.execute("INSERT INTO journal_settings VALUES(?,?,?)", row)
                ddl = connection.execute("SELECT sql FROM sqlite_master WHERE name='journal_settings'").fetchone()[0]
                previous = connection.execute("SELECT * FROM journal_schema_migrations WHERE version<=10 ORDER BY version").fetchall()
            initialize_journal_database(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual([row], connection.execute("SELECT * FROM journal_settings").fetchall())
                self.assertEqual(ddl, connection.execute("SELECT sql FROM sqlite_master WHERE name='journal_settings'").fetchone()[0])
                self.assertEqual(previous, connection.execute("SELECT * FROM journal_schema_migrations WHERE version<=10 ORDER BY version").fetchall())
                self.assertEqual([], connection.execute("SELECT * FROM journal_selected_settings_receipts").fetchall())
                self.assertEqual((11, "selected_journal_settings_receipts"), connection.execute(
                    "SELECT version,name FROM journal_schema_migrations WHERE version=11").fetchone())


if __name__ == "__main__":
    unittest.main()
