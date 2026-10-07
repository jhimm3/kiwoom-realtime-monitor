from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.research_factors import (
    FACTOR_VERSION,
    THEME_LEADERSHIP_ID,
    compute_theme_leadership,
    get_factor_definition,
)
from kiwoom_monitor.application.theme_leadership import (
    SecondTradePoint,
    ThemeLeadershipParameters,
    ThemeMembership,
    UpperLimitReference,
    evaluate_theme_leadership,
    second_trade_points_from_rows,
    theme_memberships_from_snapshot,
)


DAY = "2026-09-14"
PARAMETERS = ThemeLeadershipParameters(display_confirmation_seconds=3)


def point(code: str, second: int, price: int, value: int, *, high: int | None = None):
    observed = f"{DAY}T09:00:{second:02d}+09:00"
    available = f"{DAY}T09:00:{second:02d}.100000+09:00"
    return SecondTradePoint(
        input_ref=f"{code}:{second}:{price}:{value}", code=code, market="KRX",
        observed_at=observed, available_at=available, close=price,
        high=high or price, volume=max(1, value // max(1, price)),
        trade_value_won=value, trade_count=1,
    )


def membership() -> ThemeMembership:
    return ThemeMembership(
        revision_id="theme-r1", theme_id="반도체",
        member_codes=("005930", "000660"), available_at=f"{DAY}T08:50:00+09:00",
    )


def initial_points():
    return (
        point("005930", 1, 100, 400), point("000660", 1, 100, 500),
        point("005930", 9, 100, 600), point("000660", 9, 100, 500),
        point("005930", 12, 101, 900), point("000660", 12, 100, 400),
        point("005930", 19, 105, 1100), point("000660", 19, 101, 400),
    )


class ThemeLeadershipTests(unittest.TestCase):
    def test_price_and_trade_value_rank_selects_leader_and_only_reports_touched(self) -> None:
        evaluation = evaluate_theme_leadership(
            membership(), initial_points(), as_of=f"{DAY}T09:00:20+09:00",
            venue="KRX", continuity_status="COMPLETE", parameters=PARAMETERS,
            upper_limits=(UpperLimitReference(
                "limit-r1", "005930", 105, f"{DAY}T08:55:00+09:00",
            ),),
        )
        self.assertEqual("005930", evaluation.raw_leader_id)
        self.assertEqual("005930", evaluation.displayed_leader_id)
        self.assertEqual("TOUCHED", evaluation.limit_state)
        self.assertNotIn("LOCKED", evaluation.to_dict().values())
        self.assertEqual("COMPLETE", evaluation.quality["status"])

    def test_price_exit_then_reclaim_are_separate_events(self) -> None:
        first = evaluate_theme_leadership(
            membership(), initial_points(), as_of=f"{DAY}T09:00:20+09:00",
            venue="KRX", continuity_status="COMPLETE", parameters=PARAMETERS,
        )
        exit_points = (*initial_points(),
            point("005930", 22, 104, 700), point("000660", 22, 101, 100),
            point("005930", 29, 102, 900), point("000660", 29, 101, 100),
        )
        weakened = evaluate_theme_leadership(
            membership(), exit_points, as_of=f"{DAY}T09:00:30+09:00",
            venue="KRX", continuity_status="COMPLETE", parameters=PARAMETERS,
            previous_state=first.state,
        )
        self.assertEqual("WEAKENING", weakened.trend_state)
        self.assertIn("PRICE_EXIT", {event.event_type for event in weakened.events})
        recovered = evaluate_theme_leadership(
            membership(), (*exit_points,
                point("005930", 32, 103, 1200), point("000660", 32, 101, 100),
                point("005930", 39, 105, 1800), point("000660", 39, 101, 100),
            ), as_of=f"{DAY}T09:00:40+09:00", venue="KRX",
            continuity_status="COMPLETE", parameters=PARAMETERS,
            previous_state=weakened.state,
        )
        self.assertEqual("RECOVERED", recovered.trend_state)
        self.assertIn("PRICE_RECLAIM", {event.event_type for event in recovered.events})

    def test_trade_value_slowdown_then_reacceleration(self) -> None:
        first = evaluate_theme_leadership(
            membership(), initial_points(), as_of=f"{DAY}T09:00:20+09:00",
            venue="KRX", continuity_status="COMPLETE", parameters=PARAMETERS,
        )
        slow_points = (*initial_points(),
            point("005930", 22, 105, 150), point("000660", 22, 101, 50),
            point("005930", 29, 105, 150), point("000660", 29, 101, 50),
        )
        slowed = evaluate_theme_leadership(
            membership(), slow_points, as_of=f"{DAY}T09:00:30+09:00",
            venue="KRX", continuity_status="COMPLETE", parameters=PARAMETERS,
            previous_state=first.state,
        )
        self.assertEqual("SLOWING", slowed.expansion_state)
        accelerated = evaluate_theme_leadership(
            membership(), (*slow_points,
                point("005930", 32, 105, 300), point("000660", 32, 101, 50),
                point("005930", 39, 106, 600), point("000660", 39, 101, 50),
            ), as_of=f"{DAY}T09:00:40+09:00", venue="KRX",
            continuity_status="COMPLETE", parameters=PARAMETERS,
            previous_state=slowed.state,
        )
        self.assertEqual("REACCELERATING", accelerated.expansion_state)
        self.assertIn(
            "TRADE_VALUE_REACCELERATION",
            {event.event_type for event in accelerated.events},
        )

    def test_leader_change_requires_stable_display_confirmation(self) -> None:
        first = evaluate_theme_leadership(
            membership(), initial_points(), as_of=f"{DAY}T09:00:20+09:00",
            venue="KRX", continuity_status="COMPLETE", parameters=PARAMETERS,
        )
        switched_points = (*initial_points(),
            point("005930", 22, 105, 100), point("000660", 22, 102, 1800),
            point("005930", 29, 105, 100), point("000660", 29, 108, 2200),
        )
        candidate = evaluate_theme_leadership(
            membership(), switched_points, as_of=f"{DAY}T09:00:30+09:00",
            venue="KRX", continuity_status="COMPLETE", parameters=PARAMETERS,
            previous_state=first.state,
        )
        self.assertEqual("000660", candidate.raw_leader_id)
        self.assertEqual("005930", candidate.displayed_leader_id)
        confirmed = evaluate_theme_leadership(
            membership(), (*switched_points,
                point("005930", 33, 105, 100), point("000660", 33, 109, 2300),
            ), as_of=f"{DAY}T09:00:34+09:00", venue="KRX",
            continuity_status="COMPLETE", parameters=PARAMETERS,
            previous_state=candidate.state,
        )
        self.assertEqual("000660", confirmed.displayed_leader_id)
        self.assertIn("LEADER_CHANGED", {event.event_type for event in confirmed.events})

    def test_data_interruption_preserves_displayed_leader_but_returns_missing_factor(self) -> None:
        first = evaluate_theme_leadership(
            membership(), initial_points(), as_of=f"{DAY}T09:00:20+09:00",
            venue="KRX", continuity_status="COMPLETE", parameters=PARAMETERS,
        )
        factor = compute_theme_leadership(
            membership(), initial_points(), as_of=f"{DAY}T09:00:20+09:00",
            venue="KRX", continuity_status="GAP", parameters=PARAMETERS,
            previous_state=first.state,
        )
        self.assertEqual("missing", factor.status)
        self.assertEqual("005930", factor.value["displayed_leader_id"])
        self.assertEqual(THEME_LEADERSHIP_ID, get_factor_definition(
            THEME_LEADERSHIP_ID, FACTOR_VERSION,
        ).factor_id)

    def test_removed_leader_is_reselected_from_new_theme_revision(self) -> None:
        first = evaluate_theme_leadership(
            membership(), initial_points(), as_of=f"{DAY}T09:00:20+09:00",
            venue="KRX", continuity_status="COMPLETE", parameters=PARAMETERS,
        )
        changed_membership = ThemeMembership(
            revision_id="theme-r2", theme_id="반도체",
            member_codes=("000660", "035420"),
            available_at=f"{DAY}T09:00:21+09:00",
        )
        changed_points = (
            point("000660", 19, 101, 400), point("035420", 19, 100, 300),
            point("000660", 22, 102, 700), point("035420", 22, 100, 200),
            point("000660", 29, 108, 900), point("035420", 29, 101, 200),
        )
        changed = evaluate_theme_leadership(
            changed_membership, changed_points,
            as_of=f"{DAY}T09:00:30+09:00", venue="KRX",
            continuity_status="COMPLETE", parameters=PARAMETERS,
            previous_state=first.state,
        )
        self.assertEqual("000660", changed.displayed_leader_id)
        self.assertIn(
            "LEADER_REMOVED_FROM_THEME",
            {event.event_type for event in changed.events},
        )

    def test_existing_h1_and_d5_rows_have_explicit_adapters(self) -> None:
        seoul = ZoneInfo("Asia/Seoul")
        available = datetime(2026, 9, 14, 9, 0, 2, tzinfo=seoul).timestamp()
        points = second_trade_points_from_rows(({
            "trading_date": DAY, "trade_second": "09:00:01", "code": "A005930",
            "market": "KRX", "open": 100, "high": 101, "low": 99, "close": 100,
            "volume": 10, "trade_value_won": 1000, "trade_count": 2,
            "available_at": available,
        },))
        memberships = theme_memberships_from_snapshot({
            "snapshot_id": "theme-r1", "available_at": available,
            "document": {"active_profile": "기본", "profiles": [{
                "name": "기본", "stock_themes": [
                    {"code": "005930", "theme": "반도체"},
                    {"code": "000660", "theme": "반도체"},
                ],
            }]},
        })
        self.assertEqual("005930", points[0].code)
        self.assertEqual(("005930", "000660"), memberships[0].member_codes)


if __name__ == "__main__":
    unittest.main()
