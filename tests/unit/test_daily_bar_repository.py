from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.daily_bar_repository import DailyBarRepository


class DailyBarRepositoryTests(unittest.TestCase):
    def test_cached_rows_do_not_replace_saved_250_day_high_with_partial_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE daily_bars (stock_code TEXT, trade_date TEXT, high_price INTEGER, "
                "trade_value_eok REAL, close_price INTEGER, PRIMARY KEY(stock_code, trade_date))"
            )
            connection.execute("INSERT INTO daily_bars VALUES ('005930', '2026-08-28', 100, 1.0, 90)")
            connection.commit(); connection.close()

            targets = DailyBarRepository(path).load_targets(("005930",))["005930"]
            self.assertIsNone(targets.high_250_price)
            self.assertEqual(100, targets.high_5_price)

    def test_retain_latest_keeps_250_rows_per_stock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE daily_bars (stock_code TEXT, trade_date TEXT, high_price INTEGER, "
                "trade_value_eok REAL, close_price INTEGER, PRIMARY KEY(stock_code, trade_date))"
            )
            connection.executemany(
                "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?)",
                (("005930", f"2025-{1 + index // 28:02d}-{1 + index % 28:02d}", index, None, index) for index in range(280)),
            )
            connection.commit(); connection.close()

            DailyBarRepository(path).retain_latest(250)
            connection = sqlite3.connect(path)
            count = connection.execute("SELECT COUNT(*) FROM daily_bars WHERE stock_code='005930'").fetchone()[0]
            connection.close()
            self.assertEqual(250, count)

    def test_market_close_finalization_is_saved_per_stock_and_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE market_data_finalization_log (trade_date TEXT, stock_code TEXT, "
                "finalized_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(trade_date,stock_code))"
            )
            connection.commit(); connection.close()
            repository = DailyBarRepository(path)
            repository.mark_finalized(("001210",), date(2026, 8, 28))
            self.assertEqual(
                {"001210"}, repository.finalized_codes(("001210", "005930"), date(2026, 8, 28)),
            )

    def test_finds_only_codes_missing_the_expected_latest_bar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE daily_bars (stock_code TEXT, trade_date TEXT, high_price INTEGER, "
                "trade_value_eok REAL, close_price INTEGER, PRIMARY KEY(stock_code, trade_date))"
            )
            connection.execute(
                "CREATE TABLE daily_bar_sync_log (stock_code TEXT PRIMARY KEY, synced_on TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?)",
                (("005930", "2026-08-28", 100, 1.0, 99), ("000660", "2026-08-27", 200, 2.0, 198)),
            )
            connection.commit(); connection.close()
            missing = DailyBarRepository(path).missing_latest_bar(
                ("005930", "000660", "035420"), date(2026, 8, 28),
            )
            self.assertEqual(("000660", "035420"), missing)
            self.assertEqual(
                date(2026, 8, 28),
                DailyBarRepository(path).latest_trade_date_before(("005930", "000660"), date(2026, 8, 31)),
            )

    def test_weekend_finalization_distinguishes_friday_and_saturday_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE daily_bar_sync_log (stock_code TEXT PRIMARY KEY, synced_on TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO daily_bar_sync_log VALUES (?, ?)",
                (("001210", "2026-08-28"), ("005930", "2026-08-29")),
            )
            connection.commit(); connection.close()
            refreshed = DailyBarRepository(path).refreshed_since(
                ("001210", "005930", "000660"), date(2026, 8, 29),
            )
            self.assertEqual({"005930"}, refreshed)


if __name__ == "__main__":
    unittest.main()
