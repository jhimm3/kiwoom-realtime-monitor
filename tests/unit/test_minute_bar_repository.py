from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

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
from kiwoom_monitor.infrastructure.persistence.local_bar_observations import (
    local_daily_bar_observation, local_minute_bar_observation,
)


class MinuteBarRepositoryTests(unittest.TestCase):
    def test_local_observation_preserves_explicit_times_and_uses_shared_offline_fallback(self) -> None:
        fixed = datetime.fromisoformat("2030-01-02T09:01:30+09:00")
        minute = datetime(2030, 1, 2, 9)
        fallback_path = "kiwoom_monitor.infrastructure.persistence.local_bar_observations.kst_now"
        for supplied in (fixed, fixed.replace(tzinfo=None), fixed.astimezone(timezone.utc)):
            with self.subTest(supplied=supplied), patch(fallback_path) as fallback:
                observation = local_minute_bar_observation("A", minute, None,
                    available_at=supplied, source="explicit", actual_trade_value=False)
                daily = local_daily_bar_observation("A", minute.date(), None,
                    available_at=supplied, source="explicit")
                fallback.assert_not_called()
                self.assertEqual(fixed, observation.metadata.available_at)
                self.assertEqual(fixed, daily.metadata.available_at)
                self.assertEqual(DataCompleteness.COMPLETE, observation.metadata.completeness)
                self.assertEqual(DataCompleteness.IN_PROGRESS, daily.metadata.completeness)
        with patch(fallback_path, return_value=fixed) as fallback:
            observation = local_minute_bar_observation("A", minute, None,
                source="offline", actual_trade_value=False)
            daily = local_daily_bar_observation("A", minute.date(), None, source="offline")
            self.assertEqual(2, fallback.call_count)
            self.assertEqual(fixed, observation.metadata.available_at)
            self.assertEqual(fixed, daily.metadata.available_at)

    def test_gap_empty_and_market_index_metadata_use_one_injected_clock_per_batch(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        Database(path).initialize()
        fixed = datetime.fromisoformat("2030-01-02T09:01:30+09:00")
        minute = datetime(2030, 1, 2, 9)
        clock = Mock(return_value=fixed.astimezone(timezone.utc))
        repository = MinuteBarRepository(path, now_provider=clock)
        with patch("kiwoom_monitor.infrastructure.persistence.local_bar_observations.kst_now",
                   side_effect=AssertionError("app path used local fallback")):
            repository.mark_gap_minutes({"A": (minute,), "B": (minute,)})
            clock.assert_called_once_with()
            clock.reset_mock()
            self.assertEqual((minute,), repository.confirm_empty_minutes("A", (minute,)))
            clock.assert_called_once_with()
            clock.reset_mock()
            repository.upsert_market_index_minutes({
                (market, minute): (100., 101., 99., 100., 1.) for market in ("kospi", "kosdaq")})
            clock.assert_called_once_with()
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual([
                ("A", "2030-01-02T09:00", fixed.isoformat(), "complete"),
                ("B", "2030-01-02T09:00", fixed.isoformat(), "missing"),
                ("market:kosdaq", "2030-01-02T09:00", fixed.isoformat(), "in_progress"),
                ("market:kospi", "2030-01-02T09:00", fixed.isoformat(), "in_progress"),
            ], connection.execute("SELECT subject,observation_key,available_at,completeness "
                                  "FROM market_data_observation_meta ORDER BY subject").fetchall())
            self.assertEqual(0, connection.execute("SELECT count(*) FROM minute_bars").fetchone()[0])
            self.assertEqual(2, connection.execute("SELECT count(*) FROM market_index_minute_bars").fetchone()[0])

    def test_aware_injected_clock_compares_legacy_kst_minutes_without_changing_keys(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        Database(path).initialize()
        now = datetime(2026, 10, 16, 1, 1, 30, tzinfo=timezone.utc)
        repository = MinuteBarRepository(path, now_provider=lambda: now)
        bars = tuple(MinuteOhlcv(datetime(2026, 10, 16, 10, minute),
                                 100, 105, 99, 104, 20, 1.25) for minute in (0, 1))
        for with_metadata in (False, True):
            code = "metadata" if with_metadata else "plain"
            values = tuple(replace(bar, metadata=local_minute_bar_observation(
                code, bar.minute, bar, available_at=now,
                source="kiwoom-websocket-0B", actual_trade_value=True,
                origin=ObservationOrigin.REALTIME,
                completeness=DataCompleteness.IN_PROGRESS).metadata)
                if with_metadata else bar for bar in bars)
            repository.upsert_many({code: values})
            metadata = MarketDataMetadataRepository(path)
            for bar, expected in zip(bars, (DataCompleteness.COMPLETE, DataCompleteness.IN_PROGRESS)):
                self.assertEqual(expected, metadata.load(
                    MarketDatasetKind.MINUTE_BAR, code,
                    bar.minute.isoformat(timespec="minutes")).completeness)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual([
                ("2026-10-16", code, bar.minute.isoformat(timespec="minutes"), 104, 20)
                for code in ("metadata", "plain") for bar in bars
            ], connection.execute("SELECT trade_date,stock_code,minute,close_price,volume "
                                  "FROM minute_bars ORDER BY stock_code,minute").fetchall())

    def test_live_batch_reads_each_metadata_row_once_inside_the_writer_transaction(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        Database(path).initialize()
        now = datetime(2026, 10, 16, 10, 0, 30)
        minute = now.replace(second=0)
        bars = {f"{index:06d}": (MinuteOhlcv(minute, 100, 105, 99, 104, 20, 1.25),)
                for index in range(1, 21)}
        statements: list[str] = []
        real_connect = sqlite3.connect

        def traced_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        with patch("kiwoom_monitor.infrastructure.persistence.minute_bar_repository.sqlite3.connect",
                   side_effect=traced_connect), patch(
                       "kiwoom_monitor.infrastructure.persistence.minute_bar_repository.kst_wall_now",
                       return_value=now):
            MinuteBarRepository(path).upsert_many(bars)
        self.assertEqual("BEGIN IMMEDIATE", statements[0])
        self.assertEqual("COMMIT", statements[-1])
        selects = [sql for sql in statements if sql.startswith("SELECT ")]
        self.assertEqual(20, len(selects))
        with closing(real_connect(path)) as connection:
            self.assertEqual([(104, 20, 1.25)] * 20, connection.execute(
                "SELECT close_price,volume,trade_value_eok FROM minute_bars ORDER BY stock_code"
            ).fetchall())
            self.assertEqual(20, connection.execute(
                "SELECT COUNT(*) FROM market_data_observation_meta "
                "WHERE completeness='in_progress' AND origin='realtime' AND source='kiwoom-websocket-0B'"
            ).fetchone()[0])

    def test_live_write_reuses_authority_without_losing_partial_missing_or_same_batch_updates(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        Database(path).initialize()
        now = datetime(2026, 10, 16, 10, 1, 30)
        minute = now.replace(minute=0, second=0)
        key = minute.isoformat(timespec="minutes")
        repository = MinuteBarRepository(path)
        original = MinuteOhlcv(minute, 100, 105, 99, 104, 20, 1.25)
        cases = [("query", DataCompleteness.COMPLETE, ObservationOrigin.QUERY, "kiwoom-ka10080", True),
                 ("backfill", DataCompleteness.COMPLETE, ObservationOrigin.BACKFILLED, "backfill", True),
                 ("nas", DataCompleteness.COMPLETE, ObservationOrigin.REALTIME, "NAS:kiwoom-websocket-0B", True),
                 ("local", DataCompleteness.COMPLETE, ObservationOrigin.REALTIME, "kiwoom-websocket-0B", False),
                 ("partial", DataCompleteness.PARTIAL, ObservationOrigin.QUERY, "kiwoom-ka10080", False),
                 ("missing", DataCompleteness.MISSING, ObservationOrigin.REALTIME, "kiwoom-websocket-0B", False)]
        metadata = MarketDataMetadataRepository(path)
        with patch("kiwoom_monitor.infrastructure.persistence.minute_bar_repository.kst_wall_now", return_value=now):
            repository.upsert_many({code: (original,) for code, *_ in cases})
            for code, state, origin, source, _protected in cases:
                metadata.save(key, local_minute_bar_observation(
                    code, minute, original, available_at=now, source=source,
                    actual_trade_value=True, completeness=state, origin=origin))
            replacement = replace(original, close_price=102, volume=30)
            repository.upsert_many({code: (replacement,) for code, *_ in cases})
            # A trusted first entry changes metadata before the duplicate key is processed.
            trusted = local_minute_bar_observation(
                "same", minute, original, available_at=now, source="kiwoom-ka10080",
                actual_trade_value=False, completeness=DataCompleteness.COMPLETE,
                origin=ObservationOrigin.QUERY)
            repository.upsert_many({"same": (replace(original, metadata=trusted.metadata), replacement)})
        with closing(sqlite3.connect(path)) as connection:
            values = dict(connection.execute("SELECT stock_code,close_price FROM minute_bars"))
        for code, state, origin, source, protected in cases:
            with self.subTest(code=code):
                saved = metadata.load(MarketDatasetKind.MINUTE_BAR, code, key)
                self.assertEqual(104 if protected else 102, values[code])
                self.assertEqual(state if protected else DataCompleteness.PARTIAL
                                 if state in {DataCompleteness.PARTIAL, DataCompleteness.MISSING}
                                 else DataCompleteness.COMPLETE, saved.completeness)
                self.assertEqual(origin if protected else ObservationOrigin.REALTIME, saved.origin)
        self.assertEqual(104, values["same"])
        self.assertEqual(ObservationOrigin.QUERY, metadata.load(MarketDatasetKind.MINUTE_BAR, "same", key).origin)

    def test_live_batch_failure_rolls_back_previous_metadata_and_retry_keeps_one_row_per_key(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        Database(path).initialize()
        repository = MinuteBarRepository(path)
        now = datetime(2026, 10, 16, 10, 0, 30)
        bar = MinuteOhlcv(now.replace(second=0), 100, 105, 99, 104, 20, 1.25)
        from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import upsert_market_data_metadata
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("second metadata failed")
            return upsert_market_data_metadata(*args, **kwargs)

        with patch("kiwoom_monitor.infrastructure.persistence.minute_bar_repository.kst_wall_now", return_value=now):
            with patch("kiwoom_monitor.infrastructure.persistence.minute_bar_repository.upsert_market_data_metadata",
                       side_effect=fail_second), self.assertRaisesRegex(RuntimeError, "second metadata failed"):
                repository.upsert_many({"A": (bar,), "B": (bar,)})
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual((0, 0), (connection.execute("SELECT COUNT(*) FROM minute_bars").fetchone()[0],
                                         connection.execute("SELECT COUNT(*) FROM market_data_observation_meta").fetchone()[0]))
            repository.upsert_many({"A": (bar,), "B": (bar,)})
            repository.upsert_many({"A": (bar,), "B": (bar,)})
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual([(104, 20)] * 2, connection.execute(
                "SELECT close_price,volume FROM minute_bars ORDER BY stock_code").fetchall())
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM market_data_observation_meta").fetchone()[0])

    @patch("kiwoom_monitor.infrastructure.persistence.minute_bar_repository.kst_wall_now",
           return_value=datetime(2026, 9, 10, 10, 15, 30))
    def test_open_0b_snapshot_replaces_provisional_bar_without_double_accumulating(self, clock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            minute = clock.return_value.replace(second=0, microsecond=0)

            repository.upsert_many({
                "005930": (MinuteOhlcv(minute, 100, 105, 99, 104, 20, 1.25),),
            })
            repository.upsert_many({
                "005930": (MinuteOhlcv(minute, 100, 110, 98, 108, 35, 2.75),),
            })

            with closing(sqlite3.connect(path)) as connection:
                bar = connection.execute(
                    "SELECT open_price,high_price,low_price,close_price,volume,trade_value_eok "
                    "FROM minute_bars WHERE stock_code=? AND minute=?",
                    ("005930", minute.isoformat(timespec="minutes")),
                ).fetchone()
            metadata = MarketDataMetadataRepository(path).load(
                MarketDatasetKind.MINUTE_BAR, "005930", minute.isoformat(timespec="minutes"),
            )

        self.assertEqual((100, 110, 98, 108, 35, 2.75), bar)
        assert metadata is not None
        self.assertEqual(ObservationOrigin.REALTIME, metadata.origin)
        self.assertEqual(DataCompleteness.IN_PROGRESS, metadata.completeness)
        self.assertEqual("kiwoom-websocket-0B", metadata.source)

    def test_completed_ka10080_bar_is_not_changed_by_late_0b_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            minute = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=5)
            key = minute.isoformat(timespec="minutes")

            repository.upsert_many({
                "005930": (MinuteOhlcv(minute, 100, 105, 99, 104, 20, 1.25),),
            })
            query_bar = MinuteOhlcv(minute, 101, 120, 95, 118, 80)
            repository.upsert_bars("005930", (query_bar,))
            expected_trade_value = query_bar.trade_value_eok
            repository.upsert_many({
                "005930": (MinuteOhlcv(minute, 100, 999, 1, 2, 50_000, 900_000),),
            })

            with closing(sqlite3.connect(path)) as connection:
                bar = connection.execute(
                    "SELECT open_price,high_price,low_price,close_price,volume,trade_value_eok "
                    "FROM minute_bars WHERE stock_code=? AND minute=?",
                    ("005930", key),
                ).fetchone()
            metadata = MarketDataMetadataRepository(path).load(
                MarketDatasetKind.MINUTE_BAR, "005930", key,
            )

        self.assertEqual((101, 120, 95, 118, 80, expected_trade_value), bar)
        assert metadata is not None
        self.assertEqual(ObservationOrigin.QUERY, metadata.origin)
        self.assertEqual(DataCompleteness.COMPLETE, metadata.completeness)
        self.assertEqual("kiwoom-ka10080", metadata.source)
        self.assertEqual(DataValueKind.ESTIMATED, metadata.value_kind)

    def test_combined_query_value_remains_classified_as_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            minute = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=5)
            repository.upsert_bars("005930", (
                MinuteOhlcv(minute, 100, 110, 90, 105, 20, 0.002),
            ))
            metadata = MarketDataMetadataRepository(path).load(
                MarketDatasetKind.MINUTE_BAR, "005930", minute.isoformat(timespec="minutes"),
            )

        assert metadata is not None
        self.assertEqual(ObservationOrigin.QUERY, metadata.origin)
        self.assertEqual(DataValueKind.ESTIMATED, metadata.value_kind)

    @patch("kiwoom_monitor.infrastructure.persistence.minute_bar_repository.kst_wall_now",
           return_value=datetime(2026, 9, 10, 10, 15, 30))
    def test_realtime_and_query_minute_bars_store_distinct_observation_meaning(self, clock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            live_minute = clock.return_value.replace(second=0, microsecond=0)
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
            targets = replace(
                targets,
                collection_verified=True,
                scope="provisional",
                query_basis_date=observed_at.date().isoformat(),
            )
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

    def test_top20_statistics_include_1530_and_prefer_ka20006_market_total(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            for minute, amount in ((datetime(2026, 9, 8, 15, 29), 10.0), (datetime(2026, 9, 8, 15, 30), 5.0)):
                repository.upsert_top20_trade_value_index(
                    minute, amount, ("005930",), "realtime_complete",
                    kospi_trade_value_eok=amount, kospi_stock_count=1,
                )
            repository.upsert_market_index_minutes({
                ("kospi", datetime(2026, 9, 8, 15, 29)): (1.0, 1.0, 1.0, 1.0, 100.0),
            })
            repository.replace_market_index_daily(
                "kospi", (("2026-09-08T00:00", 1.0, 1.0, 1.0, 1.0, 1, 200.0),),
            )

            repository.set_now_provider(lambda: datetime(2026,9,9))
            _hourly, comparisons = repository.load_top20_statistics(30)

        row = next(value for value in comparisons if value[0].isoformat() == "2026-09-08")
        self.assertEqual((15.0, 200.0), (row[1], row[2]))

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
            repository = MinuteBarRepository(path, now_provider=lambda: datetime(2026,9,3,10,0)); minute = datetime(2026, 9, 2, 9, 1)
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

            repository.load_top20_statistics(30)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM top20_statistics_daily_cache WHERE trade_date='2026-09-02'").fetchone()[0])
            self.assertEqual(repository.repair_top20_market_splits(), 1)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM top20_statistics_daily_cache WHERE trade_date='2026-09-02'").fetchone()[0])
                self.assertIn('+09:00', connection.execute('SELECT updated_at FROM top20_trade_value_index').fetchone()[0])
            repository.load_top20_statistics(30)
            loaded = repository.load_recent_top20_trade_value_index(10)

            self.assertEqual(loaded[0][4:7], (10.0, 5.0, 0.0))

    def test_top20_market_repair_stops_between_rows_when_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"; Database(path).initialize()
            repository = MinuteBarRepository(path)
            repository.upsert_top20_trade_value_index(datetime(2026, 9, 2, 9, 1), 10.0, ("005930",))
            repository.upsert_top20_trade_value_index(datetime(2026, 9, 2, 9, 2), 20.0, ("005930",))
            checks = 0

            def cancelled() -> bool:
                nonlocal checks
                checks += 1
                return checks > 1

            self.assertEqual(repository.repair_top20_market_splits(cancelled=cancelled), 1)
            self.assertEqual(checks, 2)

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
            repository.upsert_top20_trade_value_index(
                minute.replace(hour=16), 900.0, ("005930",), "realtime_complete"
            )
            repository.upsert_market_index_minutes({
                ("kospi", minute): (2600.0, 2600.0, 2600.0, 2600.0, 1_000.0),
                ("kosdaq", minute): (800.0, 800.0, 800.0, 800.0, 500.0),
            })

            hourly, comparisons = repository.load_top20_statistics(7)

            self.assertEqual(("16:00", 900.0), hourly[0][:2])
            self.assertIn(("10:00", 30.0, 1), hourly)
            self.assertEqual((30.0, 1_000.0, 500.0), comparisons[-1][1:])

    def test_top20_statistics_reuses_past_day_and_invalidates_when_minute_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"; Database(path).initialize()
            repository = MinuteBarRepository(path)
            minute = datetime.now().replace(hour=10, minute=5, second=0, microsecond=0) - timedelta(days=1)
            while minute.weekday() >= 5:
                minute -= timedelta(days=1)
            repository.upsert_top20_trade_value_index(minute, 10.0, ("005930",))
            repository.upsert_top20_trade_value_index(minute.replace(hour=9), 0.0, ("005930",))
            first = repository.load_top20_statistics(7)
            with closing(sqlite3.connect(path)) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM top20_statistics_daily_cache WHERE trade_date=?",
                    (minute.date().isoformat(),),
                ).fetchone()[0]
            second = repository.load_top20_statistics(7)
            repository.upsert_top20_trade_value_index(minute, 20.0, ("005930",))
            with closing(sqlite3.connect(path)) as connection:
                invalidated = connection.execute(
                    "SELECT COUNT(*) FROM top20_statistics_daily_cache WHERE trade_date=?",
                    (minute.date().isoformat(),),
                ).fetchone()[0]
            refreshed = repository.load_top20_statistics(7)
            self.assertEqual(1, count)
            self.assertEqual(first, second)
            self.assertEqual(0, invalidated)
            self.assertEqual(20.0, refreshed[1][-1][1])
            self.assertNotIn("09:00", {row[0] for row in refreshed[0]})

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
