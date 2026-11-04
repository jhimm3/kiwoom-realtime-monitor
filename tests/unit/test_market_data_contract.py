from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from kiwoom_monitor.application.top20_trade_value_collector import Top20MinuteRecord
from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse,
    DataCompleteness,
    DataUnit,
    DataValueKind,
    ListingEvidence,
    ListingEvidenceStatus,
    ListingPhase,
    ListingType,
    MarketDatasetKind,
    ObservationOrigin,
    TradingVenue,
    metadata_from_legacy_capture_state,
    select_listing_evidence,
    trading_venue,
)
from kiwoom_monitor.domain.snapshot_provenance import SnapshotOrigin
from kiwoom_monitor.domain.time_basis import KST


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


class ListingEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.listing_date = date(2026, 9, 10)
        self.initial = ListingEvidence(
            symbol="123456", listing_date=self.listing_date, listing_type=ListingType.IPO,
            available_at=datetime(2026, 9, 9, 17, tzinfo=KST),
            source="exchange-notice", revision="1", evidence_id="notice-123",
        )

    def test_reported_date_phase_uses_only_evidence_available_at_cutoff(self) -> None:
        correction = replace(
            self.initial, listing_date=date(2026, 9, 11), revision="2",
            available_at=datetime(2026, 9, 10, 1, tzinfo=timezone.utc),
        )
        other_symbol = replace(correction, symbol="654321")
        rows = (correction, other_symbol, self.initial)
        for trading_date, cutoff, phase, evidence in (
            (date(2026, 9, 9), self.initial.available_at, ListingPhase.BEFORE_REPORTED_DATE, self.initial),
            (self.listing_date, correction.available_at - timedelta(microseconds=1), ListingPhase.LISTING_DAY, self.initial),
            (self.listing_date, correction.available_at, ListingPhase.BEFORE_REPORTED_DATE, correction),
            (date(2026, 9, 12), datetime(2026, 9, 12, 9, tzinfo=KST), ListingPhase.AFTER_LISTING_DAY, correction),
        ):
            with self.subTest(trading_date=trading_date, cutoff=cutoff):
                selected = select_listing_evidence(rows, symbol="123456", trading_date=trading_date, as_of=cutoff)
                self.assertEqual(ListingEvidenceStatus.AVAILABLE, selected.status)
                self.assertEqual((evidence,), selected.evidence)
                self.assertEqual(phase, selected.phase)

    def test_incomplete_correction_does_not_restore_earlier_complete_revision(self) -> None:
        cutoff = datetime(2026, 9, 10, 9, tzinfo=KST)
        for changes in (
            {"listing_type": ListingType.UNKNOWN},
            {"listing_date": None},
            {"completeness": DataCompleteness.PARTIAL},
        ):
            with self.subTest(changes=changes):
                correction = replace(self.initial, revision="2", available_at=cutoff, **changes)
                selected = select_listing_evidence(
                    (correction, self.initial), symbol="123456", trading_date=self.listing_date, as_of=cutoff,
                )
                self.assertEqual(ListingEvidenceStatus.INCOMPLETE, selected.status)
                self.assertEqual(ListingPhase.UNKNOWN, selected.phase)
                self.assertEqual((correction,), selected.evidence)

    def test_same_instant_keeps_provenance_and_reports_conflicting_facts(self) -> None:
        cutoff = datetime(2026, 9, 10, 9, tzinfo=KST)
        notice = replace(self.initial, available_at=cutoff, revision="2", listing_type=ListingType.SPAC)
        corroborating = replace(
            notice, available_at=cutoff.astimezone(timezone.utc), source="issuer-notice", evidence_id="issuer-13",
        )
        conflict = replace(corroborating, listing_type=ListingType.SPAC_MERGER)
        for peer, status, phase in (
            (corroborating, ListingEvidenceStatus.AVAILABLE, ListingPhase.LISTING_DAY),
            (conflict, ListingEvidenceStatus.CONFLICT, ListingPhase.UNKNOWN),
        ):
            with self.subTest(status=status):
                result = select_listing_evidence(
                    (peer, notice, self.initial, notice), symbol="123456", trading_date=self.listing_date, as_of=cutoff,
                )
                self.assertEqual(status, result.status)
                self.assertEqual(phase, result.phase)
                self.assertEqual((notice, peer), result.evidence)

    def test_missing_or_undated_evidence_cannot_supply_a_phase(self) -> None:
        cutoff = datetime(2026, 9, 10, 9, tzinfo=KST)
        future = replace(self.initial, available_at=cutoff + timedelta(microseconds=1))
        undated = replace(self.initial, available_at=None, revision="2")
        for rows, evidence in (((future,), ()), ((self.initial, undated, future), (undated,))):
            with self.subTest(rows=rows):
                result = select_listing_evidence(rows, symbol="123456", trading_date=self.listing_date, as_of=cutoff)
                self.assertEqual(ListingEvidenceStatus.UNKNOWN, result.status)
                self.assertEqual(ListingPhase.UNKNOWN, result.phase)
                self.assertEqual(evidence, result.evidence)

    def test_listing_rejects_unqualified_time_and_a_cut_after_trading_date(self) -> None:
        with self.assertRaisesRegex(ValueError, "include its timezone"):
            replace(self.initial, available_at=datetime(2026, 9, 9, 17))
        with self.assertRaisesRegex(ValueError, "include its timezone"):
            select_listing_evidence(
                (self.initial,), symbol="123456", trading_date=self.listing_date, as_of=datetime(2026, 9, 10, 9),
            )
        with self.assertRaisesRegex(ValueError, "follows the trading date"):
            select_listing_evidence(
                (self.initial,), symbol="123456", trading_date=self.listing_date,
                as_of=datetime(2026, 9, 10, 15, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
