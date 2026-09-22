from __future__ import annotations

import unittest
from datetime import datetime, timezone

from kiwoom_monitor.application.research_replay import (
    normalize_candidate_codes,
    replay_candidate_universe,
    replay_krx_minute_bars,
)


class ResearchReplayTests(unittest.TestCase):
    def test_ranking_and_membership_use_same_code_normalization(self) -> None:
        ranking = {"kind": "ranking", "payload": {"items": [
            {"stk_cd": "A005930"}, {"stk_cd": "000660_AL"}, {"stk_cd": "005930_NX"},
        ]}}
        membership = {"kind": "top20_membership", "payload": {
            "codes": ["005930", "A000660", "005930_NX"],
        }}
        self.assertEqual(("005930", "000660"), normalize_candidate_codes(ranking))
        self.assertEqual(("005930", "000660"), normalize_candidate_codes(membership))

    def test_virtual_clock_replay_is_deterministic_and_ignores_future_revision(self) -> None:
        values = [
            {"accepted_sequence": 2, "revision_id": "b", "observation_key": "09:00:30",
             "kind": "top20_membership", "available_at": "2026-09-12T00:00:31+00:00",
             "payload": {"codes": ["000660"]}},
            {"accepted_sequence": 1, "revision_id": "a", "observation_key": "09:00:00",
             "kind": "top20_membership", "available_at": "2026-09-12T00:00:01+00:00",
             "payload": {"codes": ["A005930"]}},
        ]
        cutoff = datetime(2026, 9, 12, 0, 0, 10, tzinfo=timezone.utc)
        first = replay_candidate_universe(values, as_of=cutoff)
        second = replay_candidate_universe(reversed(values), as_of=cutoff)
        self.assertEqual(first, second)
        self.assertEqual(("005930",), first[0].codes)
        self.assertEqual(1, len(first))

    def test_naive_virtual_clock_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            replay_candidate_universe((), as_of=datetime(2026, 9, 12))

    def test_historical_population_requires_explicit_kind_and_is_not_capped_at_twenty(self) -> None:
        observation = {
            "kind": "historical_candidate_population",
            "revision_id": "historical",
            "observation_key": "2024-01-02",
            "available_at": "2024-01-03T00:00:00+00:00",
            "payload": {"codes": [f"{index:06d}" for index in range(25)]},
        }
        self.assertEqual((), replay_candidate_universe((observation,)))
        replayed = replay_candidate_universe(
            (observation,), kinds=("historical_candidate_population",),
        )
        self.assertEqual(25, len(replayed[0].codes))

    def test_default_regular_profile_does_not_consume_new_after_market_bar(self) -> None:
        def observation(sequence: int, start: str, end: str) -> dict:
            return {
                "accepted_sequence": sequence, "revision_id": f"bar-{sequence}",
                "kind": "minute_bar", "subject": "005930:KRX", "venue": "KRX",
                "observation_key": start, "available_at": end,
                "completeness": "complete", "value_kind": "actual",
                "payload": {
                    "market": "KRX", "code": "005930", "bar_start": start, "bar_end": end,
                    "open": 1000, "high": 1010, "low": 990, "close": 1005,
                    "volume": 100, "trade_value_million_won": 1,
                    "window_closed": True, "capture_quality": "complete",
                    "finalization_source": "timer",
                },
            }

        regular = observation(1, "2026-09-14T06:29:00+00:00", "2026-09-14T06:30:00+00:00")
        after = observation(2, "2026-09-14T07:00:00+00:00", "2026-09-14T07:01:00+00:00")

        frames = replay_krx_minute_bars((regular, after))

        self.assertEqual((regular["observation_key"],), tuple(frame.observation_key for frame in frames))


if __name__ == "__main__":
    unittest.main()
