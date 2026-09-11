from __future__ import annotations

import unittest
from datetime import datetime, timezone

from kiwoom_monitor.application.top20_trade_value_collector import Top20MinuteRecord
from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse,
    DataCompleteness,
    DataUnit,
    DataValueKind,
    MarketDatasetKind,
    ObservationOrigin,
    TradingVenue,
    metadata_from_legacy_capture_state,
    trading_venue,
)
from kiwoom_monitor.domain.snapshot_provenance import SnapshotOrigin


class MarketDataContractTests(unittest.TestCase):
    def test_complete_realtime_value_is_available_only_after_received_time(self) -> None:
        effective = datetime(2026, 9, 10, 9, 1)
        available = datetime(2026, 9, 10, 9, 2)
        metadata = metadata_from_legacy_capture_state(
            "realtime_complete", effective_at=effective, available_at=available,
        )
        self.assertFalse(metadata.was_available_by(datetime(2026, 9, 10, 9, 1, 59)))
        self.assertTrue(metadata.was_available_by(available))
        self.assertTrue(metadata.is_complete)

    def test_legacy_rows_without_available_time_are_not_assumed_point_in_time_safe(self) -> None:
        metadata = metadata_from_legacy_capture_state(
            "realtime_complete", effective_at=datetime(2026, 9, 10, 9, 1),
        )
        self.assertFalse(metadata.was_available_by(datetime(2026, 9, 10, 15)))

    def test_partial_and_unconfirmed_are_not_promoted_to_complete(self) -> None:
        partial = metadata_from_legacy_capture_state("partial", effective_at=None)
        unknown = metadata_from_legacy_capture_state("old_custom_value", effective_at=None)
        self.assertEqual(DataCompleteness.PARTIAL, partial.completeness)
        self.assertEqual(ObservationOrigin.REALTIME, partial.origin)
        self.assertEqual(DataCompleteness.UNCONFIRMED, unknown.completeness)
        self.assertEqual(ObservationOrigin.UNKNOWN, unknown.origin)

    def test_timezone_mismatch_is_not_compared_implicitly(self) -> None:
        metadata = metadata_from_legacy_capture_state(
            "realtime_complete",
            effective_at=datetime(2026, 9, 10, 9),
            available_at=datetime(2026, 9, 10, 9, 1),
        )
        self.assertFalse(metadata.was_available_by(datetime(2026, 9, 10, 9, 2, tzinfo=timezone.utc)))

    def test_venue_adapter_keeps_krx_nxt_sor_and_unknown_distinct(self) -> None:
        self.assertEqual(TradingVenue.KRX, trading_venue("KRX"))
        self.assertEqual(TradingVenue.NXT, trading_venue("_NX"))
        self.assertEqual(TradingVenue.SOR, trading_venue("_AL"))
        self.assertEqual(TradingVenue.COMBINED, trading_venue("통합"))
        self.assertEqual(TradingVenue.UNKNOWN, trading_venue("KOSPI"))

    def test_top20_record_exposes_combined_eok_derived_contract(self) -> None:
        minute = datetime(2026, 9, 10, 9, 1)
        available = datetime(2026, 9, 10, 9, 2)
        record = Top20MinuteRecord(
            minute, (10.0, 5.0, 0.0), ("A", "B"), (1, 1, 0), (),
            "realtime_complete",
        )
        metadata = record.metadata(available_at=available)
        self.assertEqual(TradingVenue.COMBINED, metadata.venue)
        self.assertEqual(DataUnit.EOK_WON, metadata.unit)
        self.assertEqual(DataValueKind.DERIVED_FROM_ACTUAL, metadata.value_kind)
        self.assertEqual(CandidateUniverse.RANKING_TOP20, metadata.candidate_universe)
        self.assertTrue(metadata.was_available_by(available))
        observation = record.observation(available_at=available)
        self.assertEqual(MarketDatasetKind.TOP20_INDEX, observation.kind)
        self.assertEqual("KOSPI+KOSDAQ", observation.subject)
        self.assertIs(record, observation.value)

    def test_snapshot_origin_import_remains_compatible(self) -> None:
        self.assertIs(SnapshotOrigin, ObservationOrigin)
        self.assertEqual("backfilled", SnapshotOrigin.BACKFILLED)


if __name__ == "__main__":
    unittest.main()
