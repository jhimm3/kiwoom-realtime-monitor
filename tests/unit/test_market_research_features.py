from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from kiwoom_monitor.application.market_research_features import (
    MarketFeatureParameters,
    MarketMember,
    MarketResearchFrame,
    MatureBreakoutSummary,
    assess_market_for_strategy,
    classify_market,
    compute_market_research_features,
    market_frames_from_observations,
)
from kiwoom_monitor.application.research_factors import (
    compute_market_regime,
    get_factor_definition,
)


UTC = timezone.utc
PARAMETERS = MarketFeatureParameters(
    top_k=3, history_days=5, max_gap_seconds=40,
    min_history_observations=3, min_mature_breakouts=3,
)


def _frame(
    second: int,
    codes: tuple[str, ...],
    values: tuple[int | None, ...],
    *,
    changes: tuple[int | None, ...] | None = None,
    themes: tuple[str, ...] | None = None,
    quality: str = "complete",
    index_return_bps: int | None = None,
    day: str = "2026-09-13",
    minute: str = "09:20",
) -> MarketResearchFrame:
    changes = changes or tuple(100 for _ in codes)
    themes = themes or tuple(f"theme-{code}" for code in codes)
    at = datetime(2026, 9, 13, 0, 20, tzinfo=UTC) + timedelta(seconds=second)
    if day != "2026-09-13":
        at -= timedelta(days=1)
    return MarketResearchFrame(
        revision_id=f"rank-{day}-{second}", available_at=at.isoformat(),
        trading_date=day, session_minute=minute, venue="COMBINED",
        window="cumulative_session",
        members=tuple(
            MarketMember(code, value, change, (theme,) if theme else ())
            for code, value, change, theme in zip(codes, values, changes, themes, strict=True)
        ),
        capture_quality=quality, index_return_bps=index_return_bps,
        theme_revision_id="theme-v1",
    )


class MarketResearchFeatureTests(unittest.TestCase):
    def _features(
        self,
        frames: tuple[MarketResearchFrame, ...],
        outcomes: MatureBreakoutSummary = MatureBreakoutSummary(4, 3, 1),
    ):
        return compute_market_research_features(frames, outcomes, PARAMETERS)

    def test_concentration_turnover_relative_value_and_denominators(self) -> None:
        old = _frame(-20, ("1", "2", "3", "4", "5"), (10, 10, 10, 10, 10), day="2026-09-12")
        first = _frame(0, ("1", "2", "3", "4", "5"), (20, 20, 20, 20, 20))
        second = _frame(20, ("1", "2", "6", "4", "5"), (25, 20, 20, 20, 15))
        current = _frame(40, ("1", "2", "6", "7", "5"), (30, 25, 20, 15, 10))

        features = self._features((old, first, second, current))

        self.assertEqual(100, features.trade_value_denominator_million_won)
        self.assertEqual(300_000, features.top1_concentration_ppm)
        self.assertEqual(1_000_000, features.top5_concentration_ppm)
        self.assertEqual(0, features.fixed_k_turnover_ppm)
        self.assertEqual(333_333, features.jaccard_change_ppm)
        self.assertEqual(1_000_000, features.current_leader_top_k_residency_ppm)
        self.assertEqual(2_000_000, features.relative_trade_value_ppm)
        self.assertEqual(50, features.historical_median_trade_value_million_won)
        self.assertIn(old.revision_id, features.input_refs)
        self.assertEqual(5, features.top20_breadth_denominator)

    def test_healthy_theme_spread_is_theme_led(self) -> None:
        codes = ("1", "2", "3", "4", "5")
        themes = ("alpha", "alpha", "alpha", "beta", "gamma")
        frames = (
            _frame(0, codes, (28, 24, 18, 16, 14), themes=themes),
            _frame(20, codes, (29, 24, 18, 15, 14), themes=themes),
            _frame(40, codes, (30, 25, 15, 15, 15), themes=themes),
        )
        classification = classify_market(self._features(frames))
        self.assertEqual("THEME_LED", classification.market_type)
        self.assertIn("theme_concentration_with_active_spread", classification.reasons)

    def test_single_leader_and_strategy_policy_are_separate(self) -> None:
        codes = ("1", "2", "3", "4", "5")
        changes = (500, -100, -100, 100, -50)
        frames = tuple(
            _frame(second, codes, (50, 15, 15, 10, 10), changes=changes)
            for second in (0, 20, 40)
        )
        classification = classify_market(self._features(frames))
        assessment = assess_market_for_strategy(
            classification, strategy_id="leader_breakout/v1",
            preferred_types=("SINGLE_LEADER",), difficult_types=("DISPERSED_ROTATION",),
        )
        self.assertEqual("SINGLE_LEADER", classification.market_type)
        self.assertEqual("SUPPORTED", assessment.tradability)
        self.assertNotIn("tradability", classification.to_dict())

    def test_fast_failure_rotation_needs_more_than_rank_turnover(self) -> None:
        first = _frame(0, ("1", "2", "3", "4", "5"), (22, 21, 20, 19, 18))
        second = _frame(20, ("6", "7", "3", "4", "5"), (22, 21, 20, 19, 18))
        current = _frame(40, ("8", "9", "10", "4", "5"), (22, 21, 20, 19, 18))
        classification = classify_market(self._features(
            (first, second, current), MatureBreakoutSummary(4, 1, 3),
        ))
        self.assertEqual("DISPERSED_ROTATION", classification.market_type)

        stable_leader = _frame(40, ("3", "8", "9", "4", "5"), (22, 21, 20, 19, 18))
        not_rotation = classify_market(self._features(
            (first, second, stable_leader), MatureBreakoutSummary(4, 1, 3),
        ))
        self.assertNotEqual("DISPERSED_ROTATION", not_rotation.market_type)

    def test_index_only_strength_immature_outcomes_and_data_gap_are_unknown(self) -> None:
        codes = ("1", "2", "3", "4", "5")
        changes = (100, -100, -100, -100, -100)
        frames = tuple(
            _frame(second, codes, (25, 20, 20, 20, 15), changes=changes, index_return_bps=150)
            for second in (0, 20, 40)
        )
        index_only = classify_market(self._features(frames))
        self.assertEqual("UNKNOWN", index_only.market_type)
        self.assertIn("index_strength_without_top20_confirmation", index_only.reasons)

        immature = classify_market(self._features(
            frames, MatureBreakoutSummary(0, 0, 0, pending_count=4),
        ))
        self.assertIn("mature_breakout_sample_insufficient", immature.reasons)

        interrupted = (
            frames[0],
            _frame(20, codes, (25, 20, 20, 20, 15)),
            _frame(100, codes, (25, 20, 20, 20, 15), quality="partial"),
        )
        unavailable = classify_market(self._features(interrupted))
        self.assertEqual("UNKNOWN", unavailable.market_type)
        self.assertIn("current_capture_incomplete", unavailable.reasons)
        self.assertIn("rank_observation_gap", unavailable.reasons)

    def test_zero_denominator_is_missing_and_factor_keeps_reasons(self) -> None:
        codes = ("1", "2", "3", "4", "5")
        frames = tuple(_frame(second, codes, (0, 0, 0, 0, 0)) for second in (0, 20, 40))
        factor = compute_market_regime(frames, MatureBreakoutSummary(3, 2, 1), PARAMETERS)
        self.assertEqual("partial", factor.status)
        self.assertIsNone(factor.value["features"]["top1_concentration_ppm"])
        self.assertIn("trade_value_denominator_unavailable", factor.reason)
        self.assertEqual("market_regime", get_factor_definition("market_regime", "v1").factor_id)

    def test_ranking_adapter_uses_theme_revision_available_at_that_time(self) -> None:
        observation = {
            "kind": "ranking", "revision_id": "rank-1", "venue": "COMBINED",
            "effective_at": "2026-09-13T09:20:00+09:00",
            "available_at": "2026-09-13T00:20:01+00:00", "completeness": "complete",
            "payload": {"query_type": "5", "items": [
                {"stk_cd": "A005930", "trde_prica": "1,234", "flu_rt": "+2.50"},
            ]},
        }
        snapshots = ({
            "snapshot_id": "theme-old", "available_at": 1789258800.0,
            "document": {"active_profile": "기본", "profiles": [{
                "name": "기본", "stock_themes": [
                    {"code": "005930", "theme": "반도체"},
                    {"code": "005930", "theme": "AI"},
                ],
            }]},
        },)
        frames = market_frames_from_observations((observation,), snapshots)
        self.assertEqual(1, len(frames))
        self.assertEqual(1234, frames[0].members[0].trade_value_million_won)
        self.assertEqual(250, frames[0].members[0].change_bps)
        self.assertEqual(("반도체", "AI"), frames[0].members[0].themes)


if __name__ == "__main__":
    unittest.main()
