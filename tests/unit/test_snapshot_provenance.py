from __future__ import annotations

import unittest
from types import SimpleNamespace
from datetime import datetime

from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse, DataCompleteness, DataValueKind, MarketDatasetKind,
    ObservationOrigin, TradingVenue,
)
from kiwoom_monitor.domain.snapshot_provenance import (
    SnapshotOrigin, mark_news_backfilled, snapshot_field_origins,
)
from kiwoom_monitor.domain.trade_snapshot_observations import entry_context_observations


class SnapshotProvenanceTests(unittest.TestCase):
    def test_entry_context_is_split_into_independent_field_observations(self) -> None:
        executed_at = datetime(2026, 9, 10, 9, 1)
        snapshot = SimpleNamespace(
            execution_key="execution-1", stock_code="005930", executed_at=executed_at,
            market="KRX", rank=3, trade_value_1m_eok=12.5, trade_value_5m_eok=44.2,
            themes=("반도체",), high_distance_percent=0.7, news=(),
            investor_flow={"available": True, "backfilled": True},
            orderbook={"strength": 101.2}, market_state={},
        )

        observations = entry_context_observations(
            snapshot, datetime(2026, 9, 10, 20, 5)
        )

        self.assertEqual(MarketDatasetKind.ENTRY_CONTEXT, observations["rank"].kind)
        self.assertEqual("005930:rank", observations["rank"].subject)
        self.assertEqual(TradingVenue.KRX, observations["rank"].metadata.venue)
        self.assertEqual(CandidateUniverse.TRADE_ENTRIES, observations["rank"].metadata.candidate_universe)
        self.assertEqual(DataValueKind.UNKNOWN, observations["trade_value_1m"].metadata.value_kind)
        self.assertEqual(DataCompleteness.MISSING, observations["news"].metadata.completeness)
        self.assertEqual(ObservationOrigin.BACKFILLED, observations["investor_flow"].metadata.origin)
        self.assertEqual(ObservationOrigin.REALTIME, observations["orderbook"].metadata.origin)

    def test_realtime_and_backfilled_values_are_distinguished(self) -> None:
        snapshot = SimpleNamespace(
            rank=3,
            news=mark_news_backfilled(({"title": "기사"},)),
            themes=("반도체",),
            investor_flow={"available": True, "backfilled": True},
            orderbook={"strength": 101.2},
            market_state={},
        )

        origins = snapshot_field_origins(snapshot)

        self.assertEqual(SnapshotOrigin.REALTIME, origins["rank"])
        self.assertEqual(SnapshotOrigin.BACKFILLED, origins["news"])
        self.assertEqual(SnapshotOrigin.REALTIME, origins["themes"])
        self.assertEqual(SnapshotOrigin.BACKFILLED, origins["investor_flow"])
        self.assertEqual(SnapshotOrigin.REALTIME, origins["orderbook"])
        self.assertEqual(SnapshotOrigin.MISSING, origins["market_state"])

    def test_unavailable_investor_response_is_not_treated_as_observed(self) -> None:
        snapshot = SimpleNamespace(
            rank=None, news=(), themes=(),
            investor_flow={"available": False, "error": "timeout"},
            orderbook={}, market_state={},
        )

        self.assertEqual(
            SnapshotOrigin.MISSING,
            snapshot_field_origins(snapshot)["investor_flow"],
        )


if __name__ == "__main__":
    unittest.main()
