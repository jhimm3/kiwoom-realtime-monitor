from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from kiwoom_monitor.application.research_factors import (
    RankPersistenceParameters,
    RollingHighBreakoutParameters,
    compute_rank_persistence,
    compute_rolling_high_breakout,
    get_factor_definition,
)
from kiwoom_monitor.application.research_replay import (
    CandidateUniverseFrame,
    KrxMinuteBarFrame,
)


UTC = timezone.utc


def _bar(index: int, *, close: int, high: int, available_offset: int = 2) -> KrxMinuteBarFrame:
    start = datetime(2026, 9, 12, 0, index, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    return KrxMinuteBarFrame(
        revision_id=f"bar-{index}", observation_key=start.isoformat(), code="005930",
        bar_start=start.isoformat(), bar_end=end.isoformat(),
        available_at=(end + timedelta(seconds=available_offset)).isoformat(),
        open=close - 10, high=high, low=close - 20, close=close, volume=100,
        trade_value_million_won=1, session_finalized=False,
        capture_quality="complete", finalization_source="timer",
    )


def _rank(second: int, codes: tuple[str, ...]) -> CandidateUniverseFrame:
    at = datetime(2026, 9, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=second)
    return CandidateUniverseFrame(
        revision_id=f"rank-{second}", observation_key=at.isoformat(),
        available_at=at.isoformat(), codes=codes,
    )


class ResearchFactorTests(unittest.TestCase):
    def test_rolling_high_uses_only_preceding_available_bars(self) -> None:
        history = [_bar(0, close=1000, high=1010), _bar(1, close=1010, high=1020)]
        evaluation = _bar(2, close=1031, high=1035)
        future_revision = _bar(1, close=2000, high=2000, available_offset=200)

        value = compute_rolling_high_breakout(
            [evaluation, future_revision, *history], evaluation,
            RollingHighBreakoutParameters(lookback_bars=2, buffer_bps=100),
        )

        self.assertEqual("valid", value.status)
        self.assertTrue(value.value["breakout"])
        self.assertEqual(1020, value.value["reference_high"])
        self.assertEqual("bar-1", value.value["reference_revision_id"])
        self.assertEqual(("bar-0", "bar-1", "bar-2"), value.input_refs)

    def test_rolling_high_reports_missing_for_history_or_coverage_gap(self) -> None:
        evaluation = _bar(3, close=1030, high=1040)
        insufficient = compute_rolling_high_breakout(
            [_bar(2, close=1020, high=1025)], evaluation,
            RollingHighBreakoutParameters(2, 0),
        )
        gap = compute_rolling_high_breakout(
            [_bar(0, close=1000, high=1010), _bar(2, close=1020, high=1025)], evaluation,
            RollingHighBreakoutParameters(2, 0),
        )
        self.assertEqual("insufficient_preceding_bars", insufficient.reason)
        self.assertEqual("minute_coverage_gap", gap.reason)

    def test_rolling_factor_does_not_carry_history_between_profile_windows(self) -> None:
        after_history = replace(
            _bar(0, close=1000, high=1010),
            research_session="2026-09-14:KRX_AFTER",
        )
        regular_history = replace(
            _bar(1, close=1010, high=1020),
            research_session="2026-09-14:KRX_REGULAR",
        )
        evaluation = replace(
            _bar(2, close=1030, high=1040),
            research_session="2026-09-14:KRX_REGULAR",
        )
        value = compute_rolling_high_breakout(
            (after_history, regular_history), evaluation,
            RollingHighBreakoutParameters(2, 0),
        )
        self.assertEqual("insufficient_preceding_bars", value.reason)

    def test_rank_persistence_measures_residency_without_bridging_gaps(self) -> None:
        frames = (
            _rank(0, ("005930", "000660")),
            _rank(20, ("000660", "005930")),
            _rank(40, ("035420", "000660")),
            _rank(60, ("005930", "000660")),
        )
        value = compute_rank_persistence(
            frames, "A005930", datetime(2026, 9, 12, 0, 1, tzinfo=UTC),
            RankPersistenceParameters(top_k=1, window_seconds=60, max_gap_seconds=20),
        )
        self.assertEqual("valid", value.status)
        self.assertEqual(20, value.value["residency_seconds"])
        self.assertEqual(2, value.value["present_observation_count"])

        missing = compute_rank_persistence(
            (frames[0], frames[2]), "005930", datetime(2026, 9, 12, 0, 1, tzinfo=UTC),
            RankPersistenceParameters(top_k=1, window_seconds=60, max_gap_seconds=20),
        )
        self.assertEqual("rank_observation_gap", missing.reason)

    def test_partial_rank_list_and_unknown_version_are_rejected(self) -> None:
        value = compute_rank_persistence(
            (_rank(0, ("005930",)), _rank(30, ("005930",)), _rank(60, ("005930",))),
            "005930", datetime(2026, 9, 12, 0, 1, tzinfo=UTC),
            RankPersistenceParameters(top_k=2, window_seconds=60, max_gap_seconds=30),
        )
        self.assertEqual("partial_rank_list", value.reason)
        with self.assertRaisesRegex(ValueError, "unregistered factor"):
            get_factor_definition("rolling_high_breakout", "v999")


if __name__ == "__main__":
    unittest.main()
