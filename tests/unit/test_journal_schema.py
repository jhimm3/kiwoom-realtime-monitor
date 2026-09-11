from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.journal_schema import initialize_journal_database


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
}


class JournalSchemaTests(unittest.TestCase):
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

    def test_initialization_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite3"

            initialize_journal_database(path)
            initialize_journal_database(path)

            with closing(sqlite3.connect(path)) as connection:
                count = connection.execute("SELECT count(*) FROM trade_fills").fetchone()[0]
            self.assertEqual(0, count)


if __name__ == "__main__":
    unittest.main()
