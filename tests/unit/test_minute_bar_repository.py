from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository


class MinuteBarRepositoryTests(unittest.TestCase):
    def test_stores_actual_minute_trade_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            repository.upsert_bars(
                "005930",
                (MinuteOhlcv(datetime(2026, 8, 28, 10, 15), 100, 120, 90, 110, 50, 3.25),),
            )

            connection = sqlite3.connect(path)
            try:
                stored = connection.execute(
                    "SELECT trade_value_eok FROM minute_bars WHERE stock_code=? AND minute=?",
                    ("005930", "2026-08-28T10:15"),
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual((3.25,), stored)

    def test_existing_minute_bars_table_gets_trade_value_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            connection = sqlite3.connect(path)
            try:
                connection.execute("ALTER TABLE minute_bars RENAME TO old_minute_bars")
                connection.execute(
                    "CREATE TABLE minute_bars (trade_date TEXT, stock_code TEXT, minute TEXT, "
                    "open_price INTEGER, high_price INTEGER, low_price INTEGER, close_price INTEGER, volume INTEGER)"
                )
                connection.commit()
            finally:
                connection.close()

            database.initialize()

            connection = sqlite3.connect(path)
            try:
                columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(minute_bars)")}
            finally:
                connection.close()
            self.assertIn("trade_value_eok", columns)


if __name__ == "__main__":
    unittest.main()
