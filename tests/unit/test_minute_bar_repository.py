from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.daily_high_service import DailyBar, DailyHighTargets
from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse,
    DataCompleteness,
    DataValueKind,
    MarketDatasetKind,
    ObservationOrigin,
)
from kiwoom_monitor.infrastructure.persistence.daily_bar_repository import DailyBarRepository
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import (
    MarketDataMetadataRepository,
)
from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository


class MinuteBarRepositoryTests(unittest.TestCase):
    def test_realtime_and_query_minute_bars_store_distinct_observation_meaning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            live_minute = datetime.now().replace(second=0, microsecond=0)
            query_minute = live_minute - timedelta(minutes=1)
            repository.upsert_many({
                "005930": (MinuteOhlcv(live_minute, 100, 110, 90, 105, 20, 1.25),),
            })
            repository.upsert_bars(
                "000660", (MinuteOhlcv(query_minute, 200, 210, 190, 205, 30),)
            )

            metadata = MarketDataMetadataRepository(path)
            live = metadata.load(
                MarketDatasetKind.MINUTE_BAR, "005930", live_minute.isoformat(timespec="minutes")
            )
            queried = metadata.load(
                MarketDatasetKind.MINUTE_BAR, "000660", query_minute.isoformat(timespec="minutes")
            )

            assert live is not None and queried is not None
            self.assertEqual(ObservationOrigin.REALTIME, live.origin)
            self.assertEqual(DataCompleteness.IN_PROGRESS, live.completeness)
            self.assertEqual(DataValueKind.ACTUAL, live.value_kind)
            self.assertEqual(CandidateUniverse.RANKING_VISIBLE, live.candidate_universe)
            self.assertEqual(ObservationOrigin.QUERY, queried.origin)
            self.assertEqual(DataCompleteness.COMPLETE, queried.completeness)
            self.assertEqual(DataValueKind.ESTIMATED, queried.value_kind)

    def test_minute_bar_and_metadata_are_rolled_back_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            minute = datetime(2026, 9, 10, 10, 15)

            with patch(
                "kiwoom_monitor.infrastructure.persistence.minute_bar_repository.upsert_market_data_metadata",
                side_effect=RuntimeError("metadata failed"),
            ), self.assertRaisesRegex(RuntimeError, "metadata failed"):
                repository.upsert_bars(
                    "005930", (MinuteOhlcv(minute, 100, 110, 90, 105, 20),)
                )

            with closing(sqlite3.connect(path)) as connection:
                count = connection.execute("SELECT count(*) FROM minute_bars").fetchone()[0]
            self.assertEqual(0, count)

    def test_daily_bars_store_complete_and_in_progress_observation_meaning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            observed_at = datetime(2026, 9, 10, 19, 0)
            targets = DailyHighTargets.from_daily_bars((
                DailyBar("20260910", 110, 5.0, 105, 100, 90, 20),
                DailyBar("20260909", 100, 4.0, 95, 90, 80, 10),
            ), as_of=observed_at.date())
            DailyBarRepository(path).upsert_targets(
                "005930", targets, observed_at.date(), observed_at=observed_at
            )

            metadata = MarketDataMetadataRepository(path)
            current = metadata.load(MarketDatasetKind.DAILY_BAR, "005930", "2026-09-10")
            previous = metadata.load(MarketDatasetKind.DAILY_BAR, "005930", "2026-09-09")

            assert current is not None and previous is not None
            self.assertEqual(DataCompleteness.IN_PROGRESS, current.completeness)
            self.assertEqual(DataCompleteness.COMPLETE, previous.completeness)
            self.assertEqual("kiwoom-ka10081", current.source)

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

    def test_market_index_ticks_are_merged_into_one_minute_bar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"; Database(path).initialize()
            repository = MinuteBarRepository(path); minute = datetime(2026, 9, 1, 9, 5)
            repository.upsert_market_index_minutes({("kospi", minute): (2600.0, 2602.0, 2599.0, 2601.0, 1500.0)})
            repository.upsert_market_index_minutes({("kospi", minute): (2601.0, 2604.0, 2598.0, 2603.0, 1700.0)})
            connection = sqlite3.connect(path)
            try:
                row = connection.execute(
                    "SELECT open_value,high_value,low_value,close_value,trade_value_eok FROM market_index_minute_bars"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual((2600.0, 2604.0, 2598.0, 2603.0, 1700.0), row)

    def test_market_index_history_is_saved_as_one_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            minute = datetime(2026, 9, 8, 9, 1)

            repository.replace_market_index_history(
                {("kospi", minute): (2600.0, 2601.0, 2599.0, 2600.5, None)},
                {"kospi": (("2026-09-08T00:00", 2590.0, 2610.0, 2580.0, 2600.0, 10, 20.0),)},
            )

            with closing(sqlite3.connect(path)) as connection:
                minute_count = connection.execute("SELECT count(*) FROM market_index_minute_bars").fetchone()[0]
                daily_count = connection.execute("SELECT count(*) FROM market_index_daily_bars").fetchone()[0]
            self.assertEqual((1, 1), (minute_count, daily_count))

    def test_market_index_history_rolls_back_minutes_when_daily_save_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            minute = datetime(2026, 9, 8, 9, 1)

            with self.assertRaises(sqlite3.Error):
                repository.replace_market_index_history(
                    {("kospi", minute): (2600.0, 2601.0, 2599.0, 2600.5, None)},
                    {"kospi": (("invalid-row",),)},
                )

            with closing(sqlite3.connect(path)) as connection:
                minute_count = connection.execute("SELECT count(*) FROM market_index_minute_bars").fetchone()[0]
                daily_count = connection.execute("SELECT count(*) FROM market_index_daily_bars").fetchone()[0]
            self.assertEqual((0, 0), (minute_count, daily_count))

    def test_top20_trade_value_index_is_saved_with_its_fixed_members(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"; Database(path).initialize()
            repository = MinuteBarRepository(path); minute = datetime(2026, 9, 2, 10, 31)
            repository.upsert_top20_trade_value_index(
                minute, 925.5, ("005930", "000660"), "realtime_complete",
                kospi_trade_value_eok=900.0, kosdaq_trade_value_eok=20.0,
                unknown_trade_value_eok=5.5, kospi_stock_count=1,
                kosdaq_stock_count=1,
                cohort_segments=(("2026-09-02T10:31:00", ("005930",)),
                                 ("2026-09-02T10:31:30", ("000660",))),
            )

            loaded = repository.load_recent_top20_trade_value_index(10)

            self.assertEqual(
                ((minute, 925.5, ("005930", "000660"), "realtime_complete", 900.0, 20.0, 5.5),),
                loaded,
            )
            connection = sqlite3.connect(path)
            try:
                segments = json.loads(connection.execute(
                    "SELECT cohort_segments FROM top20_trade_value_index WHERE minute=?",
                    (minute.isoformat(timespec="minutes"),),
                ).fetchone()[0])
            finally:
                connection.close()
            self.assertEqual(
                [
                    ["2026-09-02T10:31:00", ["005930"]],
                    ["2026-09-02T10:31:30", ["000660"]],
                ],
                segments,
            )

    def test_old_top20_record_without_market_split_is_shown_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"; Database(path).initialize()
            repository = MinuteBarRepository(path); minute = datetime(2026, 9, 2, 9, 1)
            repository.upsert_top20_trade_value_index(minute, 12.5, ("005930",))

            loaded = repository.load_recent_top20_trade_value_index(10)

            self.assertEqual(loaded[0][4:7], (0.0, 0.0, 12.5))

    def test_top20_market_split_is_repaired_from_korean_krx_market_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"; Database(path).initialize()
            repository = MinuteBarRepository(path); minute = datetime(2026, 9, 2, 9, 1)
            connection = sqlite3.connect(path)
            try:
                connection.executemany(
                    "INSERT INTO stocks(code,name,market) VALUES(?,?,?)",
                    (("005930", "삼성전자", "유가"), ("196170", "알테오젠", "코스닥")),
                )
                connection.executemany(
                    "INSERT INTO minute_bars(trade_date,stock_code,minute,open_price,high_price,low_price,close_price,volume,trade_value_eok) VALUES(?,?,?,?,?,?,?,?,?)",
                    (("2026-09-02", "005930", minute.isoformat(timespec="minutes"), 1, 1, 1, 1, 1, 10.0),
                     ("2026-09-02", "196170", minute.isoformat(timespec="minutes"), 1, 1, 1, 1, 1, 5.0)),
                )
                connection.commit()
            finally:
                connection.close()
            repository.upsert_top20_trade_value_index(minute, 15.0, ("005930", "196170"))

            self.assertEqual(repository.repair_top20_market_splits(), 1)
            loaded = repository.load_recent_top20_trade_value_index(10)

            self.assertEqual(loaded[0][4:7], (10.0, 5.0, 0.0))

    def test_top20_history_can_be_loaded_by_trade_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"; Database(path).initialize()
            repository = MinuteBarRepository(path)
            first = datetime(2026, 9, 1, 9, 1); second = datetime(2026, 9, 2, 9, 1)
            nxt_after_hours = datetime(2026, 9, 2, 16, 1)
            weekend = datetime(2026, 9, 5, 9, 1)
            repository.upsert_top20_trade_value_index(first, 10.0, ("005930",))
            repository.upsert_top20_trade_value_index(second, 20.0, ("005930",))
            repository.upsert_top20_trade_value_index(nxt_after_hours, 40.0, ("005930",))
            repository.upsert_top20_trade_value_index(weekend, 30.0, ("005930",))

            loaded = repository.load_top20_trade_value_index_for_date(first.date())

            self.assertEqual(tuple(row[0] for row in loaded), (first,))
            daily = repository.load_top20_daily_trade_values()
            self.assertEqual(tuple((row[0], row[3]) for row in daily), ((first.date(), 10.0), (second.date(), 20.0)))

    def test_top20_statistics_compare_regular_session_with_market_cumulative_totals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"; Database(path).initialize()
            repository = MinuteBarRepository(path)
            minute = datetime.now().replace(hour=10, minute=5, second=0, microsecond=0)
            while minute.weekday() >= 5:
                minute -= timedelta(days=1)
            repository.upsert_top20_trade_value_index(minute, 30.0, ("005930",), "realtime_complete")
            repository.upsert_market_index_minutes({
                ("kospi", minute): (2600.0, 2600.0, 2600.0, 2600.0, 1_000.0),
                ("kosdaq", minute): (800.0, 800.0, 800.0, 800.0, 500.0),
            })

            hourly, comparisons = repository.load_top20_statistics(7)

            self.assertEqual("10:00", hourly[0][0])
            self.assertEqual(30.0, hourly[0][1])
            self.assertEqual((30.0, 1_000.0, 500.0), comparisons[-1][1:])

    def test_top20_statistics_and_history_hide_rows_outside_collection_hours(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"; Database(path).initialize()
            repository = MinuteBarRepository(path)
            valid = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
            while valid.weekday() >= 5:
                valid -= timedelta(days=1)
            invalid = valid.replace(hour=7)
            repository.upsert_top20_trade_value_index(valid, 10.0, ("005930",))
            repository.upsert_top20_trade_value_index(invalid, 99.0, ("005930",))

            rows = repository.load_top20_trade_value_index_for_date(valid.date())
            hourly, _comparisons = repository.load_top20_statistics(7)

            self.assertEqual((valid,), tuple(row[0] for row in rows))
            self.assertEqual(("08:00",), tuple(row[0] for row in hourly))


if __name__ == "__main__":
    unittest.main()
