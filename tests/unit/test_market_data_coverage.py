from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from kiwoom_monitor.application.market_data_coverage import (
    CoverageObservation,
    CoverageState,
    evaluate_coverage,
)
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    MarketDataMetadata,
)


KST = timezone(timedelta(hours=9))


def observation(
    minute: int,
    *,
    available_minute: int | None = None,
    completeness: DataCompleteness = DataCompleteness.COMPLETE,
) -> CoverageObservation:
    effective = datetime(2026, 9, 10, 9, minute, tzinfo=KST)
    available = datetime(
        2026, 9, 10, 9, available_minute if available_minute is not None else minute,
        tzinfo=KST,
    )
    return CoverageObservation(
        effective.isoformat(),
        MarketDataMetadata(effective, available, completeness=completeness),
    )


class MarketDataCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 9, 10, 9, 0, tzinfo=KST)
        self.end = datetime(2026, 9, 10, 9, 3, tzinfo=KST)

    def test_bar_absence_without_explicit_evidence_is_not_called_a_gap(self) -> None:
        report = evaluate_coverage(
            (), start=self.start, end=self.end, available_by=self.end,
        )
        self.assertEqual(CoverageState.MISSING, report.state)
        self.assertFalse(report.gap_inference_supported)
        self.assertEqual((), report.missing_intervals)
        self.assertEqual("no_trade_or_no_observation", report.absence_meaning)

    def test_explicit_complete_archive_can_prove_zero_trade_bars(self) -> None:
        report = evaluate_coverage(
            (), start=self.start, end=self.end, available_by=self.end,
            explicit_complete=True,
        )
        self.assertEqual(CoverageState.COMPLETE, report.state)
        self.assertEqual("no_trade_with_complete_coverage", report.absence_meaning)

    def test_observation_available_after_cutoff_is_excluded(self) -> None:
        report = evaluate_coverage(
            (observation(1, available_minute=5),),
            start=self.start,
            end=self.end,
            available_by=self.end,
        )
        self.assertEqual(CoverageState.MISSING, report.state)
        self.assertEqual(1, report.unavailable_by_cutoff_count)

    def test_fixed_cadence_data_reports_grouped_missing_intervals(self) -> None:
        report = evaluate_coverage(
            (observation(0), observation(2)),
            start=self.start,
            end=self.end,
            available_by=self.end,
            expected_seconds=60,
        )
        self.assertEqual(CoverageState.PARTIAL, report.state)
        self.assertEqual(
            ((datetime(2026, 9, 10, 9, 1, tzinfo=KST),
              datetime(2026, 9, 10, 9, 2, tzinfo=KST)),),
            report.missing_intervals,
        )

    def test_fixed_cadence_is_complete_when_all_slots_exist(self) -> None:
        report = evaluate_coverage(
            (observation(0), observation(1), observation(2)),
            start=self.start,
            end=self.end,
            available_by=self.end,
            expected_seconds=60,
        )
        self.assertEqual(CoverageState.COMPLETE, report.state)


if __name__ == "__main__":
    unittest.main()
