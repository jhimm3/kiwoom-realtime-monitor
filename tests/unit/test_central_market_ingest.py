from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_ingest import (
    MarketDataIngestor, fundamentals_document_is_current, nxt_eligibility_document_is_current,
)
from kiwoom_monitor.central_server.market_observations import (
    bar_observation_key,
    minute_bar_observation, daily_bar_observation,
)
from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse,
    DataCompleteness,
    DataValueKind,
    MarketDatasetKind,
    ObservationOrigin,
    TradingVenue,
)


class MarketDataIngestorTests(unittest.TestCase):
    def test_bar_observation_clock_fallback_is_lazy_and_preserves_original_epochs(self) -> None:
        from kiwoom_monitor.domain.time_basis import as_kst
        base = {"trading_date": "2026-09-14", "minute": "10:00", "code": "005930", "market": "KRX"}
        for source_now in (datetime(2030, 1, 2, 3, 4, 5),
                           datetime(2030, 1, 1, 18, 4, 5, tzinfo=timezone.utc)):
            for updated in ("missing", None, "invalid", float("nan"), float("inf"), 1e300, 0, "946684800"):
                valid = updated in (0, "946684800")
                expected = datetime.fromtimestamp(float(updated), timezone.utc).astimezone(as_kst(source_now).tzinfo) if valid else as_kst(source_now)
                for kind in ("minute", "daily"):
                    with self.subTest(clock=source_now.isoformat(), updated=str(updated), kind=kind):
                        value = dict(base)
                        if updated != "missing":
                            value["updated_at"] = updated
                        clock = Mock(return_value=source_now)
                        with patch("kiwoom_monitor.central_server.market_observations.kst_now",
                                   side_effect=AssertionError("injected_clock_must_be_used")):
                            if kind == "minute":
                                result = minute_bar_observation(value, origin=ObservationOrigin.QUERY,
                                    completeness=DataCompleteness.COMPLETE, source="fixture",
                                    value_kind=DataValueKind.ESTIMATED, now_provider=clock)
                            else:
                                result = daily_bar_observation(value, completeness=DataCompleteness.COMPLETE,
                                                               now_provider=clock)
                        self.assertEqual(expected, result.metadata.available_at)
                        self.assertEqual(0 if valid else 1, clock.call_count)
                        self.assertIs(value, result.value)
                        self.assertEqual(as_kst(datetime(2026, 9, 14, 10) if kind == "minute"
                                                else datetime(2026, 9, 14)), result.metadata.effective_at)
        invalid_key = {**base, "trading_date": "invalid", "minute": "invalid"}
        clock = Mock(return_value=datetime(2030, 1, 2, 3, 4, 5))
        result = minute_bar_observation(invalid_key, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE, source="fixture", value_kind=DataValueKind.ESTIMATED,
            now_provider=clock)
        self.assertEqual(1, clock.call_count)
        self.assertEqual(result.metadata.effective_at, result.metadata.available_at)
        # Existing standalone/offline calls retain the common KST fallback.
        with patch("kiwoom_monitor.central_server.market_observations.kst_now", clock):
            standalone = daily_bar_observation(base, completeness=DataCompleteness.COMPLETE)
        self.assertEqual(as_kst(clock.return_value), standalone.metadata.available_at)
        self.assertEqual(2, clock.call_count)

    def test_basic_refresh_cutoff_uses_kst_and_does_not_expire_nxt_eligibility(self) -> None:
        before = datetime.fromisoformat("2026-10-01T06:59:59+09:00")
        after = datetime.fromisoformat("2026-10-01T07:00:00+09:00")
        # Both timestamps represent the same pre-07:00 KST observation.
        for observed_at in (before.isoformat(), before.astimezone(timezone.utc).isoformat(),
                            "2026-10-01T06:59:59"):
            document = {"observed_at": observed_at}
            self.assertTrue(fundamentals_document_is_current(document, before.date(), checked_at=before))
            self.assertFalse(fundamentals_document_is_current(document, after.date(), checked_at=after))
            self.assertTrue(nxt_eligibility_document_is_current(document, after.date()))
        self.assertTrue(fundamentals_document_is_current(
            {"observed_at": after.isoformat()}, after.date(), checked_at=after,
        ))
        for document in ({}, {"observed_at": "invalid"}, {"observed_at": "2026-09-30T07:00:00+09:00"}):
            self.assertFalse(fundamentals_document_is_current(document, after.date(), checked_at=after))

    def test_records_sor_vs_query_trade_value_trend_after_minute_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.save_minute_bars([{
                "trading_date": "2026-09-14", "minute": "10:01", "code": "005930",
                "market": "SOR", "open": 10000, "high": 10000, "low": 10000,
                "close": 10000, "volume": 2500, "trade_value_million_won": 25,
                "updated_at": 1.0,
            }])
            ingestor = MarketDataIngestor(
                store, now_provider=lambda: datetime(2026, 9, 14, 20, 5),
            )
            for suffix, volume in (("", 1000), ("_NX", 500)):
                ingestor.ingest("ka10080", {
                    "stk_cd": f"005930{suffix}", "base_dt": "20260914",
                }, {"stk_min_pole_chart_qry": [{
                    "cntr_tm": "20260914100100", "open_pric": "10000",
                    "high_pric": "10000", "low_pric": "10000",
                    "cur_prc": "10000", "trde_qty": str(volume),
                }]})
            saved = store.load_documents(
                "minute_trade_value_comparisons", "2026-09-14:005930", 10,
            )
            store.close()

        document = saved[0]["document"]
        self.assertEqual({"KRX": 10, "NXT": 5}, document["query_components_million_won"])
        self.assertEqual(25, document["realtime_trade_value_million_won"])
        self.assertEqual(15, document["query_trade_value_million_won"])
        self.assertEqual(10, document["difference_million_won"])
        self.assertEqual("KRX+NXT", document["query_scope"])
    def test_ingests_krx_and_nxt_minute_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            ingestor = MarketDataIngestor(
                store, now_provider=lambda: datetime(2026, 9, 8, 10, 1, 30)
            )
            payload = {"stk_min_pole_chart_qry": [{
                "cntr_tm": "20260908100100", "open_pric": "+70000", "high_pric": "+70100",
                "low_pric": "+69900", "cur_prc": "+70050", "trde_qty": "1000",
            }]}
            ingestor.ingest("ka10080", {"stk_cd": "005930_NX", "base_dt": "20260908"}, payload)
            bars = store.load_minute_bars("005930", "2026-09-08", "NXT")
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.MINUTE_BAR,
                "005930:NXT",
                "2026-09-08T10:01",
            )
        self.assertEqual(1, len(bars))
        self.assertEqual(70050, bars[0]["close"])
        self.assertEqual(70, bars[0]["trade_value_million_won"])
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(DataCompleteness.IN_PROGRESS, metadata.completeness)
        self.assertEqual(ObservationOrigin.QUERY, metadata.origin)
        self.assertEqual(DataValueKind.ESTIMATED, metadata.value_kind)
        self.assertIn("trade_value=ohlcv_estimate", metadata.source)

    def test_time_only_minute_uses_injected_source_date_when_base_date_is_missing(self) -> None:
        class Store:
            def __init__(self) -> None:
                self.saved: list[dict[str, object]] = []

            def load_market_data_metadata_range(self, *args, **kwargs):
                return ()

            def replace_minute_bars(self, values, *, observations=()):
                self.saved.extend(values)

            def load_minute_bars(self, *args, **kwargs):
                return ()

        store = Store()
        source_now = datetime.fromisoformat("2001-04-03T09:00:02+09:00")
        MarketDataIngestor(store, now_provider=lambda: source_now)._ingest_minutes(
            {"stk_cd": "005930"}, {"stk_min_pole_chart_qry": [{
                "cntr_tm": "085900", "open_pric": "100", "high_pric": "101",
                "low_pric": "99", "cur_prc": "100", "trde_qty": "10",
            }]},
        )

        self.assertEqual(1, len(store.saved))
        self.assertEqual("2001-04-03", store.saved[0]["trading_date"])
        self.assertEqual("08:59", store.saved[0]["minute"])

    def test_closed_query_replaces_realtime_minute_and_late_delta_cannot_change_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            actual = {
                "trading_date": "2026-09-08", "minute": "10:01", "code": "005930", "market": "KRX",
                "open": 70000, "high": 70100, "low": 69900, "close": 70050, "volume": 1000,
                "trade_value_million_won": 777, "updated_at": 1.0,
            }
            observation = minute_bar_observation(
                actual, origin=ObservationOrigin.REALTIME,
                completeness=DataCompleteness.IN_PROGRESS,
                source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL,
            )
            store.save_minute_bars(
                [actual], observations=[(bar_observation_key(observation), observation)],
            )

            MarketDataIngestor(
                store, now_provider=lambda: datetime(2026, 9, 8, 10, 2)
            ).ingest("ka10080", {"stk_cd": "005930", "base_dt": "20260908"}, {
                "stk_min_pole_chart_qry": [{
                    "cntr_tm": "20260908100100", "open_pric": "70000", "high_pric": "70100",
                    "low_pric": "69900", "cur_prc": "70050", "trde_qty": "1000",
                }],
            })
            late = {**actual, "volume": 10, "trade_value_million_won": 8,
                    "updated_at": 2.0, "operation_id": "late-0b"}
            late_observation = minute_bar_observation(
                late, origin=ObservationOrigin.REALTIME,
                completeness=DataCompleteness.IN_PROGRESS,
                source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL,
            )
            store.save_minute_bars(
                [late], observations=[(bar_observation_key(late_observation), late_observation)],
            )
            store.finalize_minute_bars([{
                "trading_date": "2026-09-08", "minute": "10:01", "code": "005930", "market": "KRX",
                "available_at": datetime(2026, 9, 8, 10, 2, tzinfo=timezone(timedelta(hours=9))).timestamp(),
                "capture_quality": "complete", "finalization_source": "timer",
                "operation_id": "late-0b-close",
            }])
            bars = store.load_minute_bars("005930", "2026-09-08", "KRX")
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.MINUTE_BAR, "005930:KRX", "2026-09-08T10:01",
            )
            store.close()

        self.assertEqual(70, bars[0]["trade_value_million_won"])
        self.assertEqual(1000, bars[0]["volume"])
        self.assertEqual(DataValueKind.ESTIMATED, metadata.value_kind)
        self.assertEqual(ObservationOrigin.QUERY, metadata.origin)
        self.assertEqual(DataCompleteness.COMPLETE, metadata.completeness)

    def test_realtime_replaces_provisional_query_minute_without_adding_its_volume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            MarketDataIngestor(
                store, now_provider=lambda: datetime(2026, 9, 8, 10, 1, 30)
            ).ingest("ka10080", {"stk_cd": "005930", "base_dt": "20260908"}, {
                "stk_min_pole_chart_qry": [{
                    "cntr_tm": "20260908100100", "open_pric": "70000", "high_pric": "70100",
                    "low_pric": "69900", "cur_prc": "70050", "trde_qty": "1000",
                }],
            })
            realtime = {
                "trading_date": "2026-09-08", "minute": "10:01", "code": "005930", "market": "KRX",
                "open": 70000, "high": 70150, "low": 69900, "close": 70100, "volume": 15,
                "trade_value_million_won": 1, "updated_at": 2.0, "operation_id": "in-progress-0b",
            }
            observation = minute_bar_observation(
                realtime, origin=ObservationOrigin.REALTIME,
                completeness=DataCompleteness.IN_PROGRESS,
                source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL,
            )
            store.save_minute_bars(
                [realtime], observations=[(bar_observation_key(observation), observation)],
            )
            bars = store.load_minute_bars("005930", "2026-09-08", "KRX")
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.MINUTE_BAR, "005930:KRX", "2026-09-08T10:01",
            )
            store.close()
        self.assertEqual(15, bars[0]["volume"])
        self.assertEqual(1, bars[0]["trade_value_million_won"])
        self.assertEqual(ObservationOrigin.REALTIME, metadata.origin)

    def test_ingests_daily_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            MarketDataIngestor(
                store, now_provider=lambda: datetime(2026, 9, 8, 10, 0)
            ).ingest("ka10081", {"stk_cd": "005930"}, {
                "stk_dt_pole_chart_qry": [{
                    "dt": "20260908", "open_pric": "70000", "high_pric": "71000",
                    "low_pric": "69000", "cur_prc": "70500", "trde_qty": "1234", "trde_prica": "9876",
                }],
            })
            bars = store.load_daily_bars("005930", "KRX")
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.DAILY_BAR, "005930:KRX", "2026-09-08"
            )
        self.assertEqual(1, len(bars))
        self.assertEqual(9876, bars[0]["trade_value_million_won"])
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(DataCompleteness.IN_PROGRESS, metadata.completeness)
        self.assertEqual(DataValueKind.ACTUAL, metadata.value_kind)

    def test_effective_date_krx_chart_data_is_not_finalized_at_regular_close(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            payload = {"stk_dt_pole_chart_qry": [{
                "dt": "20260914", "open_pric": "70000", "high_pric": "71000",
                "low_pric": "69000", "cur_prc": "70500", "trde_qty": "1234",
            }]}
            MarketDataIngestor(
                store, now_provider=lambda: datetime(2026, 9, 14, 15, 35)
            ).ingest("ka10081", {"stk_cd": "005930"}, payload)
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.DAILY_BAR, "005930:KRX", "2026-09-14"
            )
            assert metadata is not None
            self.assertEqual(DataCompleteness.IN_PROGRESS, metadata.completeness)

            MarketDataIngestor(
                store, now_provider=lambda: datetime(2026, 9, 14, 20, 5)
            ).ingest("ka10081", {"stk_cd": "005930"}, payload)
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.DAILY_BAR, "005930:KRX", "2026-09-14"
            )
            store.close()

        assert metadata is not None
        self.assertEqual(DataCompleteness.COMPLETE, metadata.completeness)

    def test_pre_effective_krx_chart_keeps_1530_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            MarketDataIngestor(
                store, now_provider=lambda: datetime(2026, 9, 11, 15, 35)
            ).ingest("ka10081", {"stk_cd": "005930"}, {
                "stk_dt_pole_chart_qry": [{
                    "dt": "20260911", "open_pric": "70000", "high_pric": "71000",
                    "low_pric": "69000", "cur_prc": "70500", "trde_qty": "1234",
                }],
            })
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.DAILY_BAR, "005930:KRX", "2026-09-11"
            )
            store.close()

        assert metadata is not None
        self.assertEqual(DataCompleteness.COMPLETE, metadata.completeness)

    def test_effective_date_query_minute_separates_window_and_session_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            payload = {"stk_min_pole_chart_qry": [{
                "cntr_tm": "20260914152900", "open_pric": "70000", "high_pric": "70100",
                "low_pric": "69900", "cur_prc": "70050", "trde_qty": "1000",
            }]}
            MarketDataIngestor(
                store, now_provider=lambda: datetime(2026, 9, 14, 15, 35)
            ).ingest("ka10080", {"stk_cd": "005930", "base_dt": "20260914"}, payload)
            revisions = store.load_observation_revisions("minute_bar", "005930:KRX")
            store.close()

        self.assertTrue(revisions[-1]["payload"]["window_closed"])
        self.assertFalse(revisions[-1]["payload"]["session_finalized"])

    def test_ingests_ranking_and_supply_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            ingestor = MarketDataIngestor(
                store, now_provider=lambda: datetime(2026, 9, 8, 8, 5, 31),
            )
            ingestor.ingest("ka00198", {"qry_tp": "5"}, {
                "item_inq_rank": [{"dt": "20260908", "tm": "080530", "stk_cd": "005930", "bigd_rank": "1"}],
            })
            ingestor.ingest("ka10045", {"stk_cd": "005930_AL", "end_dt": "20260908"}, {
                "stk_orgn_trde_trnsn": [{"dt": "20260908", "for_daly_nettrde_qty": "100"}],
            })
            ingestor.ingest("ka90008", {"stk_cd": "005930_AL", "date": "20260908"}, {
                "stk_tm_prm_trde_trnsn": [{"tm": "101500", "prm_netprps_amt": "200"}],
            })
            ranking = store.load_dataset_snapshots("ranking", "5")
            ranking_metadata = store.load_market_data_metadata(
                MarketDatasetKind.CANDIDATE_SET, "5", "2026-09-08T08:05:30"
            )
            ranking_revisions = store.load_observation_revisions("ranking", "5")
            investor = store.load_dataset_snapshots("investor_flow", "005930")
            program = store.load_dataset_snapshots("program_flow", "005930")
        self.assertEqual("005930", ranking[0]["payload"]["items"][0]["stk_cd"])
        self.assertIsNotNone(ranking_metadata)
        assert ranking_metadata is not None
        self.assertEqual(TradingVenue.COMBINED, ranking_metadata.venue)
        self.assertEqual(DataCompleteness.COMPLETE, ranking_metadata.completeness)
        self.assertEqual(ObservationOrigin.QUERY, ranking_metadata.origin)
        self.assertEqual(CandidateUniverse.RANKING_TOP20, ranking_metadata.candidate_universe)
        self.assertEqual("2026-09-07T23:05:31+00:00", ranking_revisions[0]["available_at"])
        self.assertEqual("SOR", investor[0]["payload"]["market"])
        self.assertEqual("SOR", program[0]["payload"]["market"])

    def test_ranking_ingest_outside_morning_window_does_not_persist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            payload = {"item_inq_rank": [{
                "dt": "20260908", "tm": "090000", "stk_cd": "005930", "bigd_rank": "1",
            }]}
            for query_type in ("1", "5"):
                for hour, minute in ((7, 54), (8, 6), (9, 0)):
                    MarketDataIngestor(
                        store, now_provider=lambda h=hour, m=minute: datetime(2026, 9, 8, h, m),
                    ).ingest("ka00198", {"qry_tp": query_type}, payload)
                self.assertEqual([], store.load_dataset_snapshots("ranking", query_type))
            store.close()

    def test_archives_new_high_fundamentals_and_nxt_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            ingestor = MarketDataIngestor(
                store, now_provider=lambda: datetime(2026, 9, 12, 10, 15, 30),
            )
            ingestor.ingest("ka10016", {"dt": "250"}, {
                "ntl_pric": [{"stk_cd": "005930", "stk_nm": "삼성전자"}],
            })
            ingestor.ingest("ka10001", {"stk_cd": "005930"}, {
                "stk_nm": "삼성전자", "mac": "5000000", "dstr_rt": "75.0",
            })
            ingestor.ingest("ka10100", {"stk_cd": "005930"}, {"nxtEnable": "Y"})

            new_high = store.load_dataset_snapshots("new_high", "250")
            fundamental_history = store.load_dataset_snapshots("stock_fundamentals", "005930")
            fundamentals = store.load_documents("stock_fundamentals", "005930")
            nxt_history = store.load_dataset_snapshots("nxt_eligibility", "005930")
            nxt = store.load_documents("stock_nxt_eligibility", "005930")
            store.close()

        self.assertEqual("005930", new_high[0]["payload"]["items"][0]["stk_cd"])
        self.assertEqual("5000000", fundamentals[0]["document"]["payload"]["mac"])
        self.assertEqual("2026-09-12:KRX", fundamental_history[0]["snapshot_key"])
        self.assertTrue(nxt[0]["document"]["enabled"])
        self.assertEqual("2026-09-12", nxt_history[0]["snapshot_key"])


class MinutePageReadBoundaryTests(unittest.TestCase):
    @staticmethod
    def _bar(day, minute, market="KRX", value=30):
        return {"trading_date": day, "minute": minute, "code": "005930", "market": market,
                "open": 10000, "high": 10000, "low": 10000, "close": 10000,
                "volume": value * 100, "trade_value_million_won": value, "updated_at": 1.0}

    @staticmethod
    def _payload(*timestamps):
        return {"stk_min_pole_chart_qry": [
            {"cntr_tm": stamp, "open_pric": "10000", "high_pric": "10000",
             "low_pric": "10000", "cur_prc": "10000", "trde_qty": "1000"}
            for stamp in timestamps]}

    def test_mixed_minute_page_limits_native_reads_and_keeps_actual_current_bar_and_components(self):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime.fromisoformat("2026-09-14T10:00:30+09:00")
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3", now_provider=lambda: now)
            store.initialize()
            try:
                values = [self._bar("2026-09-14", (datetime(2026, 9, 14, 9) + timedelta(minutes=i)).strftime("%H:%M"))
                          for i in range(120)]
                observations = [minute_bar_observation(value, origin=ObservationOrigin.REALTIME,
                    completeness=DataCompleteness.IN_PROGRESS, source="actual-fixture",
                    value_kind=DataValueKind.ACTUAL, now_provider=lambda: now) for value in values]
                store.save_minute_bars(values, observations=[(bar_observation_key(o), o) for o in observations])
                store.save_minute_bars([dict(value, market="SOR", trade_value_million_won=25) for value in values])
                before_current = store.load_market_data_metadata(MarketDatasetKind.MINUTE_BAR, "005930:KRX", "2026-09-14T10:00")
                store.upsert_documents("minute_trade_value_comparisons", [
                    {"owner": "2026-09-14:005930", "key": minute,
                     "document": {"query_components_million_won": {"NXT": 5}}} for minute in ("09:59", "10:00")
                ] + [{"owner": "2026-09-14:005930", "key": "09:15", "document": {"preserve": "outside-page"}}])
                native_meta, native_bars = store.load_market_data_metadata_range, store.load_minute_bars
                received = {"metadata": [], "sor": []}
                def metadata(*args, **kwargs):
                    rows = native_meta(*args, **kwargs)
                    received["metadata"].append(len(rows))
                    return rows
                def bars(*args, **kwargs):
                    rows = native_bars(*args, **kwargs)
                    if args[2] == "SOR": received["sor"].append(len(rows))
                    return rows
                with patch.object(store, "load_market_data_metadata_range", side_effect=metadata), \
                        patch.object(store, "load_minute_bars", side_effect=bars):
                    MarketDataIngestor(store, now_provider=lambda: now).ingest("ka10080", {"stk_cd": "005930"},
                        self._payload("20260914095900", "20260914100000"))
                rows = {row["minute"]: row for row in native_bars("005930", "2026-09-14", "KRX")}
                self.assertEqual((1000, 10), (rows["09:59"]["volume"], rows["09:59"]["trade_value_million_won"]))
                self.assertEqual((3000, 30), (rows["10:00"]["volume"], rows["10:00"]["trade_value_million_won"]))
                self.assertEqual(before_current, store.load_market_data_metadata(MarketDatasetKind.MINUTE_BAR, "005930:KRX", "2026-09-14T10:00"))
                closed = store.load_market_data_metadata(MarketDatasetKind.MINUTE_BAR, "005930:KRX", "2026-09-14T09:59")
                self.assertEqual((DataCompleteness.COMPLETE, DataValueKind.ESTIMATED, ObservationOrigin.QUERY),
                                 (closed.completeness, closed.value_kind, closed.origin))
                comparisons = {row["key"]: row["document"] for row in store.load_documents("minute_trade_value_comparisons", "2026-09-14:005930", 20)}
                for minute in ("09:59", "10:00"):
                    self.assertEqual({"NXT": 5, "KRX": 10}, comparisons[minute]["query_components_million_won"])
                    self.assertEqual((25, 15, 10, now.isoformat()), tuple(comparisons[minute][key] for key in (
                        "realtime_trade_value_million_won", "query_trade_value_million_won", "difference_million_won", "compared_at")))
                self.assertEqual({"preserve": "outside-page"}, comparisons["09:15"])
                self.assertEqual([1], received["metadata"])
                self.assertEqual([2], received["sor"])
            finally:
                store.close()

    def test_completed_cross_midnight_pages_skip_prefilter_and_late_sor_keeps_component_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime.fromisoformat("2026-09-15T10:00:00+09:00")
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3", now_provider=lambda: now)
            store.initialize()
            try:
                store.save_minute_bars([self._bar(day, minute, "SOR", 25) for day, minute in (
                    ("2026-09-14", "23:58"), ("2026-09-14", "23:59"), ("2026-09-15", "00:01"))])
                store.upsert_documents("minute_trade_value_comparisons", [
                    {"owner": "2026-09-15:005930", "key": "00:00", "document": {"query_components_million_won": {"NXT": 5}}}])
                native_bars = store.load_minute_bars
                received = []
                def bars(*args, **kwargs):
                    rows = native_bars(*args, **kwargs)
                    if args[2] == "SOR": received.append((args[1], len(rows)))
                    return rows
                ingestor = MarketDataIngestor(store, now_provider=lambda: now)
                with patch.object(store, "load_market_data_metadata_range", wraps=store.load_market_data_metadata_range) as metadata, \
                        patch.object(store, "load_minute_bars", side_effect=bars):
                    ingestor.ingest("ka10080", {"stk_cd": "005930"}, self._payload("20260914235900", "20260915000000"))
                    prior = store.load_documents("minute_trade_value_comparisons", "2026-09-15:005930", 10)[0]["document"]
                    self.assertEqual({"query_components_million_won": {"NXT": 5}}, prior)
                    store.save_minute_bars([self._bar("2026-09-15", "00:00", "SOR", 25)])
                    ingestor.ingest("ka10080", {"stk_cd": "005930"}, self._payload("20260915000000"))
                    self.assertEqual(0, metadata.call_count)
                self.assertEqual([("2026-09-14", 1), ("2026-09-15", 0), ("2026-09-15", 1)], received)
                for day, minute in (("2026-09-14", "23:59"), ("2026-09-15", "00:00")):
                    row = next(row for row in native_bars("005930", day, "KRX") if row["minute"] == minute)
                    self.assertEqual((1000, 10), (row["volume"], row["trade_value_million_won"]))
                comparison = store.load_documents("minute_trade_value_comparisons", "2026-09-15:005930", 10)[0]["document"]
                self.assertEqual({"NXT": 5, "KRX": 10}, comparison["query_components_million_won"])
                self.assertEqual((15, 10), (comparison["query_trade_value_million_won"], comparison["difference_million_won"]))
            finally:
                store.close()

    def test_required_prefilter_failure_stops_write_but_sor_failure_keeps_committed_original(self):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime.fromisoformat("2026-09-14T10:00:30+09:00")
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3", now_provider=lambda: now)
            store.initialize()
            try:
                ingestor = MarketDataIngestor(store, now_provider=lambda: now)
                with patch.object(store, "load_market_data_metadata_range", side_effect=RuntimeError("required metadata unavailable")), \
                        patch.object(store, "replace_minute_bars", wraps=store.replace_minute_bars) as save:
                    with self.assertRaisesRegex(RuntimeError, "required metadata unavailable"):
                        ingestor.ingest("ka10080", {"stk_cd": "005930"}, self._payload("20260914100000"))
                    save.assert_not_called()
                with patch.object(store, "load_minute_bars", side_effect=RuntimeError("diagnostic SOR unavailable")), \
                        self.assertLogs("kiwoom_monitor.central_server.market_ingest", level="ERROR"):
                    ingestor.ingest("ka10080", {"stk_cd": "005930"}, self._payload("20260914095900"))
                self.assertEqual(["09:59"], [row["minute"] for row in store.load_minute_bars("005930", "2026-09-14", "KRX")])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
