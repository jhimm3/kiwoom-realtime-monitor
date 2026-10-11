from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from contextlib import closing

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv, MinuteTradeValueAggregator
from kiwoom_monitor.application.minute_chart_service import MinuteChartService
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_observations import minute_bar_observation
from kiwoom_monitor.domain.realtime_gap import GapRange, RealtimeGap
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness, DataUnit, DataValueKind, MarketDataMetadata, MarketDatasetKind, ObservationOrigin,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
from kiwoom_monitor.infrastructure.persistence.market_data_metadata_repository import MarketDataMetadataRepository
from kiwoom_monitor.infrastructure.persistence.market_cache_writer import MarketCacheWriter
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.application.trade_chart import aggregate_chart_rows
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication


class RealtimeGapQualityTests(unittest.TestCase):
    def setUp(self):
        self.minute = datetime.now().replace(second=0, microsecond=0) - timedelta(minutes=5)
        self.now = self.minute + timedelta(minutes=5)

    def trade(self, price=100, minute=None):
        minute = minute or self.minute
        return TradeTick("005930", price, None, 1000, 10, None, minute.strftime("%H%M") + "01")

    def gap(self, code="005930", minute=None):
        minute = minute or self.minute
        return RealtimeGap(1, (GapRange("trade", code, "KRX", minute, minute),), False)

    def query(self, minute=None, high=1000):
        minute = minute or self.minute
        return MinuteOhlcv(minute, 100, high, 100, 100, 30, metadata=MarketDataMetadata(
            effective_at=minute, available_at=self.now, completeness=DataCompleteness.COMPLETE,
            origin=ObservationOrigin.QUERY, source="kiwoom-ka10080",
        ))

    def test_native_overflow_partial_commit_query_recovery_and_late_retry(self):
        hub = RealtimeHub()
        subscriber = hub.connect()
        subscriber.queue = asyncio.Queue(maxsize=1)
        aggregator, control = MinuteTradeValueAggregator(), MinuteTradeValueAggregator()
        first, lost, retained = self.trade(), self.trade(1000), self.trade()
        aggregator.ingest(first, self.minute)
        for tick in (first, lost, retained):
            control.ingest(tick, self.minute)
        hub.publish({"type": "trade", "payload": asdict(lost)})
        hub.publish({"type": "trade", "payload": asdict(retained)})
        gap = RealtimeGap.parse(subscriber.take_gap(subscriber.dropped_events).document())
        self.assertIsNotNone(gap)
        affected = aggregator.mark_gap(gap, ("005930",), self.now)
        partial = aggregator.ingest(retained, self.minute)
        self.assertEqual(100, partial.high_price)
        self.assertEqual(1000, control.bars("005930")[0].high_price)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository, reader = MinuteBarRepository(path), MarketDataMetadataRepository(path)
            repository.mark_gap_minutes(affected)
            repository.upsert_many({"005930": (partial,)})
            key = self.minute.isoformat(timespec="minutes")
            self.assertEqual(DataCompleteness.PARTIAL, reader.load(MarketDatasetKind.MINUTE_BAR, "005930", key).completeness)
            # A failed batch's old, apparently complete snapshot cannot clear a persisted gap.
            old = replace(partial, metadata=replace(partial.metadata, completeness=DataCompleteness.COMPLETE))
            repository.upsert_many({"005930": (old,)})
            self.assertEqual(DataCompleteness.PARTIAL, reader.load(MarketDatasetKind.MINUTE_BAR, "005930", key).completeness)
            generation = aggregator.gap_generation("005930")
            query = self.query()
            aggregator.seed("005930", (query,), self.now, recovery_generation=generation, query_authoritative=True)
            self.assertEqual("recovering", aggregator.recovery_state("005930"))
            repository.upsert_bars("005930", (query,))
            aggregator.confirm_history_saved("005930", generation)
            self.assertEqual("recovered", aggregator.recovery_state("005930"))
            repository.upsert_many({"005930": (old,)})
            self.assertEqual(DataCompleteness.COMPLETE, reader.load(MarketDatasetKind.MINUTE_BAR, "005930", key).completeness)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(1000, connection.execute("SELECT high_price FROM minute_bars").fetchone()[0])

    def test_hole_and_first_baseline_minute_remain_partial_until_authoritative_saved(self):
        aggregator = MinuteTradeValueAggregator()
        affected = aggregator.mark_gap(self.gap(), ("005930",), self.now)
        self.assertEqual((self.minute,), affected["005930"])
        later = self.minute + timedelta(minutes=1)
        partial = aggregator.ingest(self.trade(minute=later), later)
        self.assertEqual(DataCompleteness.PARTIAL, partial.metadata.completeness)
        # Empty/partial NAS responses retain the completely absent minute.
        aggregator.seed("005930", (), self.now, include_current_snapshot=True)
        self.assertEqual(DataCompleteness.PARTIAL, aggregator.bucket_quality("005930", 1, self.minute))
        aggregator.seed("005930", (replace(self.query(), metadata=MarketDataMetadata(None, None)),), self.now,
                        recovery_generation=aggregator.gap_generation("005930"))
        self.assertEqual("recovery_required", aggregator.recovery_state("005930"))

    def test_new_gap_rejects_older_request_and_commit_ack_and_save_failure(self):
        aggregator = MinuteTradeValueAggregator()
        aggregator.ingest(self.trade(), self.minute)
        aggregator.mark_gap(self.gap(), (), self.now)
        old_generation = aggregator.gap_generation("005930")
        aggregator.seed("005930", (self.query(),), self.now, recovery_generation=old_generation)
        aggregator.history_save_failed("005930")
        self.assertEqual("recovery_required", aggregator.recovery_state("005930"))
        aggregator.mark_gap(self.gap(minute=self.minute + timedelta(minutes=1)), (), self.now)
        self.assertEqual((), aggregator.history_for_storage("005930", (self.query(),), old_generation))
        aggregator.seed("005930", (self.query(),), self.now, recovery_generation=old_generation)
        aggregator.confirm_history_saved("005930", old_generation)
        self.assertEqual(DataCompleteness.PARTIAL, aggregator.bucket_quality("005930", 1, self.minute))

    def test_count_only_gap_and_bad_ranges_are_conservative_but_other_code_is_untouched(self):
        aggregator = MinuteTradeValueAggregator()
        aggregator.ingest(self.trade(), self.minute)
        aggregator.ingest(replace(self.trade(), code="000660"), self.minute)
        aggregator.mark_gap(self.gap(), (), self.now)
        self.assertEqual(DataCompleteness.COMPLETE, aggregator.bucket_quality("000660", 1, self.minute))
        malformed = {"dropped_events": 2, "ranges": [{"event_type": "trade", "start": "broken"}], "scope_unknown": False}
        gap = RealtimeGap.parse(malformed)
        self.assertTrue(gap.scope_unknown)
        aggregator.mark_gap(gap, ("000660",), self.now)
        self.assertEqual(DataCompleteness.PARTIAL, aggregator.bucket_quality("000660", 1, self.minute))
        self.assertIsNone(RealtimeGap.parse(True))

    def test_missing_rows_are_metadata_only_and_gap_transaction_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            minutes = {"005930": (self.minute, self.minute + timedelta(minutes=1))}
            with patch("kiwoom_monitor.infrastructure.persistence.minute_bar_repository.upsert_market_data_metadata", side_effect=RuntimeError("save")):
                with self.assertRaises(RuntimeError):
                    repository.mark_gap_minutes(minutes)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(0, connection.execute("SELECT count(*) FROM market_data_observation_meta").fetchone()[0])
            repository.mark_gap_minutes(minutes)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(0, connection.execute("SELECT count(*) FROM minute_bars").fetchone()[0])
                self.assertEqual([("missing",), ("missing",)], connection.execute("SELECT completeness FROM market_data_observation_meta").fetchall())

    def test_nas_numeric_row_has_no_query_authority_without_metadata(self):
        rows = [{"minute": self.minute.strftime("%H:%M"), "open": 100, "high": 100, "low": 100,
                 "close": 100, "volume": 10, "trade_value_million_won": 1}]
        bar = MinuteChartService._stored_rows_to_bars(rows, self.now)[0]
        self.assertEqual(DataCompleteness.UNCONFIRMED, bar.metadata.completeness)
        self.assertEqual(ObservationOrigin.UNKNOWN, bar.metadata.origin)

    def test_commit_ack_only_recovers_its_written_minutes_in_same_generation(self):
        aggregator = MinuteTradeValueAggregator()
        later = self.minute + timedelta(minutes=1)
        aggregator.mark_gap(RealtimeGap(2, (GapRange("trade", "005930", "KRX", self.minute, later),), False), (), self.now)
        generation = aggregator.gap_generation("005930")
        aggregator.seed("005930", (self.query(),), self.now, recovery_generation=generation)
        aggregator.seed("005930", (self.query(minute=later),), self.now, recovery_generation=generation)
        aggregator.confirm_history_saved("005930", generation, (self.minute,))
        self.assertEqual(DataCompleteness.PARTIAL, aggregator.bucket_quality("005930", 1, later))
        aggregator.confirm_history_saved("005930", generation, (later,))
        self.assertEqual("recovered", aggregator.recovery_state("005930"))

    def test_nas_closed_complete_capture_is_recovery_authority_and_partial_is_not(self):
        aggregator = MinuteTradeValueAggregator()
        aggregator.mark_gap(self.gap(), (), self.now)
        generation = aggregator.gap_generation("005930")
        complete = replace(self.query(), metadata=replace(self.query().metadata,
            origin=ObservationOrigin.REALTIME, source="NAS:kiwoom-websocket-0B"))
        aggregator.seed("005930", (replace(complete, metadata=replace(complete.metadata, completeness=DataCompleteness.PARTIAL)),),
                        self.now, recovery_generation=generation)
        self.assertEqual("recovery_required", aggregator.recovery_state("005930"))
        aggregator.seed("005930", (complete,), self.now, recovery_generation=generation)
        aggregator.confirm_history_saved("005930", generation, (self.minute,))
        self.assertEqual("recovered", aggregator.recovery_state("005930"))

    def test_empty_coverage_requires_exhausted_valid_query_for_both_venues(self):
        class Client:
            def __init__(self, response, has_next=False, key="", nxt_failure=False):
                self.response, self.has_next, self.key, self.nxt_failure = response, has_next, key, nxt_failure
            def request_with_continuation(self, _api, _path, body, **kwargs):
                if self.nxt_failure and body["stk_cd"].endswith("_NX"):
                    raise RuntimeError("NXT unavailable")
                return self.response, self.has_next, self.key
        cases = [({"stk_min_pole_chart_qry": []}, False, False, False, True),
                 ({}, False, False, False, False),
                 ({"stk_min_pole_chart_qry": []}, True, False, False, False),
                 ({"stk_min_pole_chart_qry": []}, False, True, True, False)]
        for response, has_next, combined, nxt_failure, expected in cases:
            with self.subTest(response=response, has_next=has_next, nxt_failure=nxt_failure):
                service = MinuteChartService(Client(response, has_next, nxt_failure=nxt_failure), include_nxt=combined)
                bars, complete = service.load_today_with_completion("005930", self.now)
                self.assertEqual(expected, complete)
                self.assertEqual((), bars)
        class Nas:
            def load_stored_minute_bars_with_coverage(self, *_): return (), True
        service = MinuteChartService(Nas())
        self.assertTrue(service.load_today_with_completion("005930", self.now)[1])
        self.assertFalse(service.empty_coverage_authoritative)

    def test_failed_required_venue_does_not_label_krx_only_chart_complete(self):
        record = {"cntr_tm": self.minute.strftime("%Y%m%d%H%M%S"), "open_pric": "100",
                  "high_pric": "100", "low_pric": "100", "cur_prc": "100", "trde_qty": "10"}
        class Client:
            def request(self, _api, _path, body):
                if body["stk_cd"].endswith("_NX"): raise RuntimeError("NXT")
                return {"stk_min_pole_chart_qry": [record]}
        service = MinuteChartService(Client(), include_nxt=True)
        for loader in (service.load_today, service.load_recent, service.load_two_trading_days):
            with self.subTest(loader=loader.__name__):
                self.assertEqual(DataCompleteness.PARTIAL, loader("005930", self.now)[0].metadata.completeness)

    def test_empty_recovery_commits_only_absent_minutes_before_ack_and_creates_no_bars(self):
        application = QApplication.instance() or QApplication([])
        aggregator = MinuteTradeValueAggregator()
        aggregator.mark_gap(self.gap(), (), self.now)
        generation = aggregator.gap_generation("005930")
        minutes = aggregator.empty_recovery_candidates("005930", (), self.now, generation)
        self.assertEqual((self.minute,), minutes)
        self.assertEqual("recovering", aggregator.recovery_state("005930"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            repository.mark_gap_minutes({"005930": minutes})
            acknowledgements = []
            writer = MarketCacheWriter(path)
            def acknowledged(code, result):
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(("complete",), connection.execute(
                        "SELECT completeness FROM market_data_observation_meta").fetchone())
                acknowledgements.append(result)
            writer.history_quality_saved.connect(acknowledged, Qt.ConnectionType.DirectConnection)
            writer.start()
            writer.enqueue_empty_coverage("005930", minutes, generation)
            self.assertTrue(writer.stop_and_drain())
            self.assertEqual(1, len(acknowledgements))
            self.assertEqual("recovering", aggregator.recovery_state("005930"))
            result = acknowledgements[0]
            aggregator.confirm_history_saved("005930", result["generation"], result["minutes"], empty=True)
            self.assertEqual("recovered", aggregator.recovery_state("005930"))
            self.assertEqual(DataCompleteness.COMPLETE, aggregator.bucket_quality("005930", 1, self.minute))
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(0, connection.execute("SELECT count(*) FROM minute_bars").fetchone()[0])
        application.processEvents()

    def test_gap_restore_and_day_expiration_never_report_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            repository.mark_gap_minutes({"005930": (self.minute,)})
            restored = repository.load_gap_minutes(("005930",), self.minute.date())
            aggregator = MinuteTradeValueAggregator()
            aggregator.mark_gap(self.gap(minute=restored["005930"][0]), (), self.now)
            aggregator.discard_before(self.minute.date() + timedelta(days=1))
            self.assertEqual("expired_unresolved", aggregator.recovery_state("005930"))
            self.assertEqual(restored, repository.load_gap_minutes(("005930",), self.minute.date()))

    def test_journal_and_multiminute_chart_preserve_partial_quality_and_confirmed_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = JournalRepository(Path(directory) / "journal.sqlite3")
            partial = replace(self.query(), metadata=replace(self.query().metadata, completeness=DataCompleteness.PARTIAL))
            repository.upsert_bars("005930", (partial,), "api_confirmed")
            rows = repository.load_bars("005930", self.now)
            self.assertEqual("realtime_partial", rows[0][7])
            normal = (rows[0][0], *rows[0][1:7], "api_confirmed")
            self.assertEqual("realtime_partial", aggregate_chart_rows((rows[0], normal), "5분")[0][7])
            repository.upsert_bars("005930", (self.query(),), "api_confirmed")
            repository.upsert_bars("005930", (replace(partial, high_price=10),), "api_confirmed")
            self.assertEqual((1000, "api_confirmed"), tuple(repository.load_bars("005930", self.now)[0][i] for i in (2, 7)))

    def test_market_index_gap_is_sticky_until_query_and_late_realtime_cannot_replace_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            rows = {("kospi", self.minute): (100, 100, 100, 100, 1)}
            repository.upsert_market_index_minutes(rows)
            repository.mark_gap_minutes({"market:kospi": (self.minute,)})
            repository.upsert_market_index_minutes(rows)
            reader = MarketDataMetadataRepository(path)
            key = self.minute.isoformat(timespec="minutes")
            self.assertEqual(DataCompleteness.PARTIAL, reader.load(MarketDatasetKind.MINUTE_BAR, "market:kospi", key).completeness)
            repository.replace_market_index_minutes({("kospi", self.minute): (100, 1000, 100, 100, 1)})
            repository.upsert_market_index_minutes(rows)
            self.assertEqual(DataCompleteness.COMPLETE, reader.load(MarketDatasetKind.MINUTE_BAR, "market:kospi", key).completeness)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(1000, connection.execute("SELECT high_value FROM market_index_minute_bars").fetchone()[0])

    def test_native_nas_read_attaches_metadata_to_same_numeric_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "central.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            value = {"trading_date": self.minute.date().isoformat(), "minute": self.minute.strftime("%H:%M"),
                "code": "005930", "market": "KRX", "open": 100, "high": 1000, "low": 100,
                "close": 100, "volume": 10, "trade_value_million_won": 1, "updated_at": self.now.timestamp()}
            observation = minute_bar_observation(value, origin=ObservationOrigin.QUERY,
                completeness=DataCompleteness.COMPLETE, source="kiwoom-ka10080", value_kind=DataValueKind.ACTUAL)
            key = self.minute.isoformat(timespec="minutes")
            try:
                store.save_minute_bars([value], observations=[(key, observation)])
                rows = store.load_minute_bars("005930", value["trading_date"], "KRX")
                self.assertEqual(1000, rows[0]["high"])
                self.assertEqual("complete", rows[0]["metadata"]["completeness"])
                self.assertEqual("query", rows[0]["metadata"]["origin"])
                converted = MinuteChartService._stored_rows_to_bars(rows, self.now)[0]
                self.assertTrue(converted.metadata.is_complete)
                self.assertEqual(DataUnit.EOK_WON, converted.metadata.unit)
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute("DELETE FROM central_market_data_observation_meta")
                rows = store.load_minute_bars("005930", value["trading_date"], "KRX")
                self.assertEqual(1000, rows[0]["high"])
                self.assertEqual("unconfirmed", rows[0]["metadata"]["completeness"])
            finally:
                store.close()

    def test_count_only_market_gap_invalidates_retained_rows_and_preserves_query_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            earlier = self.minute - timedelta(minutes=1)
            repository.upsert_market_index_minutes({("kospi", earlier): (100, 100, 100, 100, 1)})
            repository.replace_market_index_minutes({("kospi", self.minute): (100, 1000, 100, 100, 1)})
            repository.mark_gap_minutes({"market:kospi": ()})
            reader = MarketDataMetadataRepository(path)
            self.assertEqual(DataCompleteness.PARTIAL, reader.load(MarketDatasetKind.MINUTE_BAR,
                "market:kospi", earlier.isoformat(timespec="minutes")).completeness)
            self.assertEqual(DataCompleteness.COMPLETE, reader.load(MarketDatasetKind.MINUTE_BAR,
                "market:kospi", self.minute.isoformat(timespec="minutes")).completeness)
