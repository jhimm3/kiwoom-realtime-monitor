from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_ingest import MarketDataIngestor
from kiwoom_monitor.central_server.market_observations import (
    bar_observation_key,
    minute_bar_observation,
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

    def test_query_estimate_does_not_replace_existing_realtime_minute(self) -> None:
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
            bars = store.load_minute_bars("005930", "2026-09-08", "KRX")
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.MINUTE_BAR, "005930:KRX", "2026-09-08T10:01",
            )
            store.close()

        self.assertEqual(777, bars[0]["trade_value_million_won"])
        self.assertEqual(DataValueKind.ACTUAL, metadata.value_kind)

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

    def test_ingests_ranking_and_supply_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            ingestor = MarketDataIngestor(store)
            ingestor.ingest("ka00198", {"qry_tp": "5"}, {
                "item_inq_rank": [{"dt": "20260908", "tm": "101530", "stk_cd": "005930", "bigd_rank": "1"}],
            })
            ingestor.ingest("ka10045", {"stk_cd": "005930_AL", "end_dt": "20260908"}, {
                "stk_orgn_trde_trnsn": [{"dt": "20260908", "for_daly_nettrde_qty": "100"}],
            })
            ingestor.ingest("ka90008", {"stk_cd": "005930_AL", "date": "20260908"}, {
                "stk_tm_prm_trde_trnsn": [{"tm": "101500", "prm_netprps_amt": "200"}],
            })
            ranking = store.load_dataset_snapshots("ranking", "5")
            ranking_metadata = store.load_market_data_metadata(
                MarketDatasetKind.CANDIDATE_SET, "5", "2026-09-08T10:15:30"
            )
            investor = store.load_dataset_snapshots("investor_flow", "005930")
            program = store.load_dataset_snapshots("program_flow", "005930")
        self.assertEqual("005930", ranking[0]["payload"]["items"][0]["stk_cd"])
        self.assertIsNotNone(ranking_metadata)
        assert ranking_metadata is not None
        self.assertEqual(TradingVenue.COMBINED, ranking_metadata.venue)
        self.assertEqual(DataCompleteness.COMPLETE, ranking_metadata.completeness)
        self.assertEqual(ObservationOrigin.QUERY, ranking_metadata.origin)
        self.assertEqual(CandidateUniverse.RANKING_TOP20, ranking_metadata.candidate_universe)
        self.assertEqual("SOR", investor[0]["payload"]["market"])
        self.assertEqual("SOR", program[0]["payload"]["market"])

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


if __name__ == "__main__":
    unittest.main()
