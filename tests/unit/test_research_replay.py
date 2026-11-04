from __future__ import annotations

import unittest
from datetime import datetime, timezone

from kiwoom_monitor.application.research_replay import (
    MinuteStrategyInputs,
    ResearchReplayCursor,
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
        mixed = [dict(values[0], available_at="2026-09-12T00:00:03+00:00"),
                 dict(values[1], available_at="2026-09-12T09:00:01+09:00")]
        ordered = replay_candidate_universe(mixed, as_of=cutoff, chronological=True)
        self.assertEqual(["a", "b"], [frame.revision_id for frame in ordered])
        self.assertEqual(ordered, replay_candidate_universe(reversed(mixed), as_of=cutoff, chronological=True))
        self.assertEqual(ordered, replay_candidate_universe(mixed, as_of=cutoff))
        self.assertEqual(
            ["2026-09-12T09:00:01+09:00", "2026-09-12T00:00:03+00:00"],
            [frame.available_at for frame in ordered],
        )


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

    def test_cursor_can_replay_projected_historical_candidate_population(self) -> None:
        observation = {
            "kind": "historical_candidate_population",
            "revision_id": "historical",
            "observation_key": "2024-01-02",
            "available_at": "2024-01-03T00:00:00+00:00",
            "accepted_sequence": 1,
            "payload": {"codes": [f"{index:06d}" for index in range(25)]},
        }
        cursor = ResearchReplayCursor(
            (observation,), session_profile="krx-regular/v1",
            universe_kinds=("historical_candidate_population",),
        )

        _, universe = cursor.advance((
            datetime.fromisoformat(observation["available_at"]), 1, "historical",
        ))

        self.assertEqual(25, len(universe[0].codes))

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


class MinuteStrategyInputsTests(unittest.TestCase):
    @staticmethod
    def observation(index, *, code="005930", close=1005):
        from datetime import timedelta
        start = datetime.fromisoformat("2026-10-16T09:00:00+09:00") + timedelta(minutes=index)
        end = start + timedelta(minutes=1)
        return {
            "accepted_sequence": index + 1, "revision_id": f"{code}:{index}:{close}",
            "kind": "minute_bar", "subject": f"{code}:KRX", "venue": "KRX",
            "observation_key": start.isoformat(), "available_at": end.isoformat(),
            "completeness": "complete", "value_kind": "actual",
            "payload": {"market": "KRX", "code": code, "bar_start": start.isoformat(),
                "bar_end": end.isoformat(), "open": 1000, "high": 1010, "low": 990,
                "close": close, "volume": 100, "trade_value_million_won": 1,
                "window_closed": True, "capture_quality": "complete", "finalization_source": "timer"},
        }

    @staticmethod
    def inputs():
        return MinuteStrategyInputs(session_profile="krx-regular/v1", keep_bars=3, universe_horizon=60)

    def test_invalid_live_correction_retains_last_accepted_bar(self):
        inputs = self.inputs()
        original = self.observation(0)
        accepted = inputs.accept_observation(original)
        invalid = {**original, "revision_id": "invalid-correction", "completeness": "partial"}
        self.assertIsNone(inputs.accept_observation(invalid))
        self.assertEqual([accepted], list(inputs.frames_for_checkpoint()[0]))
        self.assertEqual("005930:0:1005", accepted.revision_id)

    def test_seed_keeps_bounded_window_per_symbol_and_live_universe_uses_arrival_time(self):
        inputs = self.inputs()
        inputs.seed([self.observation(index, code=code)
                     for code in ("005930", "000660") for index in range(5)])
        bars = tuple(inputs.frames_for_checkpoint()[0])
        self.assertEqual(6, len(bars))
        self.assertEqual({2, 3, 4}, {int(bar.revision_id.split(":")[1]) for bar in bars})
        for revision, at in (("late", "09:10:00"), ("out-of-order", "09:00:00")):
            inputs.accept_observation({"kind": "top20_membership", "revision_id": revision,
                "available_at": f"2026-10-16T{at}+09:00", "payload": {"codes": ["005930"]}})
        self.assertEqual(["late", "out-of-order"],
                         [frame.revision_id for frame in inputs.frames_for_checkpoint()[1]])

        seeded = self.inputs()
        seeded.seed([
            {"kind": "top20_membership", "revision_id": "early", "accepted_sequence": 1,
             "available_at": "2026-10-16T09:00:00+09:00", "payload": {"codes": ["005930"]}},
            {"kind": "top20_membership", "revision_id": "late", "accepted_sequence": 2,
             "available_at": "2026-10-16T00:02:00+00:00", "payload": {"codes": ["005930"]}},
        ])
        self.assertEqual(["late"],
                         [frame.revision_id for frame in seeded.frames_for_checkpoint()[1]])

    def test_clock_selection_is_read_only_and_filters_future_available_frame(self):
        inputs = self.inputs()
        inputs.seed([self.observation(0)])
        before = tuple(inputs.frames_for_checkpoint()[0])
        bar = inputs.clock_price_frame("005930", datetime.fromisoformat("2026-10-16T09:02:00+09:00"),
                                       (self.observation(1), self.observation(2)))
        self.assertEqual("2026-10-16T09:02:00+09:00", bar.bar_end)
        self.assertEqual(before, tuple(inputs.frames_for_checkpoint()[0]))

    def test_family_receives_owned_window_and_same_fill_state_without_mutation(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        inputs = self.inputs()
        bar = inputs.accept_observation(self.observation(0))
        state, config, result = object(), object(), object()
        kernel = Mock(return_value=result)
        self.assertIs(result, inputs.evaluate_bar(family=SimpleNamespace(evaluate_bar=kernel),
            run_id="test-run", evaluation_bar=bar, config=config, state=state))
        kernel.assert_called_once_with(run_id="test-run", evaluation_bar=bar,
            bar_history=(bar,), universe_frames=(), config=config, state=state)


if __name__ == "__main__":
    unittest.main()
