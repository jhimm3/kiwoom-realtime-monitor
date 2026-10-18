from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from kiwoom_monitor.domain.time_basis import KST

from kiwoom_monitor.infrastructure.persistence.daily_bar_repository import DailyBarRepository


class DailyBarRepositoryTests(unittest.TestCase):
    def test_unverified_cache_preserves_rows_without_today_sync_and_short_verified_source_syncs(self) -> None:
        from dataclasses import replace
        from kiwoom_monitor.application.daily_high_service import DailyBar, DailyHighTargets
        from kiwoom_monitor.infrastructure.persistence.database import Database
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = DailyBarRepository(path)
            targets = DailyHighTargets.from_daily_bars((DailyBar("20260929", 100, None, 95, 90, 85, 10),),
                                                      as_of=date(2026, 9, 30))
            repository.upsert_targets("005930", targets, date(2026, 9, 30))
            self.assertEqual(set(), repository.refreshed_today(("005930",), date(2026, 9, 30)))
            self.assertEqual(1, len(repository.load_targets(("005930",))["005930"].daily_bars))
            verified = replace(targets, collection_verified=True, query_basis_date="2026-09-30", source_exhausted=True)
            repository.upsert_targets("005930", verified, date(2026, 9, 30))
            self.assertEqual({"005930"}, repository.refreshed_today(("005930",), date(2026, 9, 30)))
            self.assertFalse(repository.load_targets(("005930",))["005930"].collection_verified)

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
            self.assertIsNone(targets.high_5_price)
            self.assertEqual("unverified", targets.period_status("5"))

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

    def test_saved_five_and_twenty_day_highs_are_display_values_without_fresh_query_proof(self):
        from datetime import timedelta
        from kiwoom_monitor.application.daily_high_service import DailyBar, DailyHighTargets
        from kiwoom_monitor.infrastructure.persistence.database import Database
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = DailyBarRepository(path)
            day = date(2026, 10, 15)
            bars = tuple(DailyBar((day-timedelta(days=n)).strftime("%Y%m%d"),100+n,None)
                for n in range(20))
            repository.upsert_targets("005930",DailyHighTargets(None,None,None,daily_bars=bars),day)
            target = repository.load_targets(("005930",))["005930"]
            self.assertEqual(104,target.high_5_price)
            self.assertEqual(119,target.high_20_price)
            self.assertIsNone(target.high_250_price)
            self.assertFalse(target.collection_verified)
            self.assertFalse(target.period_verified("5"))
            self.assertFalse(target.period_verified("20"))

    def test_market_close_finalization_is_saved_per_stock_and_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE market_data_finalization_log (trade_date TEXT, stock_code TEXT, "
                "finalized_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(trade_date,stock_code))"
            )
            connection.commit(); connection.close()
            now = datetime(2026, 8, 28, 20, 5, tzinfo=KST)
            repository = DailyBarRepository(path, now_provider=lambda: now)
            repository.mark_finalized(("001210",), date(2026, 8, 28))
            self.assertEqual(
                {"001210"}, repository.finalized_codes(("001210", "005930"), date(2026, 8, 28)),
            )
            repository.mark_unconfirmed("005930", date(2026, 8, 28), ("daily",), 1)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(
                    ("2026-08-28T20:05:00+09:00",),
                    connection.execute("SELECT updated_at FROM market_data_unconfirmed_log").fetchone(),
                )
            now += timedelta(minutes=1)
            repository.set_now_provider(lambda: now)
            repository.mark_unconfirmed("005930", date(2026, 8, 28), ("daily", "minute"), 2)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(
                    [("2026-08-28", "001210", "2026-08-28T20:05:00+09:00")],
                    connection.execute("SELECT trade_date,stock_code,finalized_at FROM market_data_finalization_log").fetchall(),
                )
                self.assertEqual(
                    [("daily,minute", 2, "2026-08-28T20:06:00+09:00")],
                    connection.execute("SELECT missing_parts,attempts,updated_at FROM market_data_unconfirmed_log").fetchall(),
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
