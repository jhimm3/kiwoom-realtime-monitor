from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.application.breakout_strategy import BreakoutStrategyConfig
from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
from kiwoom_monitor.central_server.database import SQLiteQueryStore


def _config() -> BreakoutStrategyConfig:
    return BreakoutStrategyConfig(
        strategy_version="v1", rolling_factor_version="v1", rank_factor_version="v1",
        lookback_bars=2, buffer_bps=0,
        rank_persistence_enabled=False, rank_persistence_required=False,
        rank_top_k=None, rank_window_seconds=None, rank_max_gap_seconds=None,
        rank_min_residency_seconds=None,
        stop_loss_bps=300, target_bps=500, max_hold_minutes=10,
        quantity=1, capital_won=1_000_000, signal_valid_seconds=60,
        cooldown_seconds=30,
    )


def _rank(sequence: int, at: str = "2026-09-12T00:00:00+00:00") -> dict:
    return {
        "accepted_sequence": sequence, "revision_id": f"rank-{sequence}",
        "kind": "top20_membership", "observation_key": f"rank-{sequence}",
        "available_at": at, "payload": {"codes": ["005930"]},
    }


def _bar(sequence: int, minute: int, close: int, high: int) -> dict:
    start = f"2026-09-12T00:{minute:02d}:00+00:00"
    end = f"2026-09-12T00:{minute + 1:02d}:00+00:00"
    available = f"2026-09-12T00:{minute + 1:02d}:02+00:00"
    return {
        "accepted_sequence": sequence, "revision_id": f"bar-{sequence}",
        "kind": "minute_bar", "subject": "005930:KRX", "venue": "KRX",
        "observation_key": start, "available_at": available,
        "completeness": "complete", "value_kind": "actual",
        "payload": {
            "market": "KRX", "code": "005930", "bar_start": start, "bar_end": end,
            "open": close - 5, "high": high, "low": close - 10, "close": close,
            "volume": 100, "trade_value_million_won": 1, "window_closed": True,
            "capture_quality": "complete", "finalization_source": "timer",
        },
    }


class FakeStore:
    def __init__(self, observations=()):
        self.observations = list(observations)
        self.checkpoints = {}
        self.decisions = []
        self.candidates = []

    def load_observation_revisions(self, *_args, **_kwargs):
        return []

    def load_observation_revisions_after(self, cursor, kinds, limit):
        return [row for row in self.observations if row["accepted_sequence"] > cursor and row["kind"] in kinds][:limit]

    def load_shadow_monitor_state(self, monitor_id):
        return self.checkpoints.get(monitor_id)

    def save_shadow_monitor_state(self, monitor_id, document):
        self.checkpoints[monitor_id] = document

    def save_shadow_evaluation(self, _monitor_id, decision, candidate, _expires_at):
        if decision["decision_id"] not in {row["decision_id"] for row in self.decisions}:
            self.decisions.append(decision)
        if candidate and candidate["event_id"] not in {row["event_id"] for row in self.candidates}:
            self.candidates.append(candidate)


class CandidateMonitorTests(unittest.TestCase):
    def test_default_session_preserves_existing_monitor_identity_format(self) -> None:
        monitor = CandidateMonitor(
            FakeStore(), _config(), poll_seconds=1, universe_max_age_seconds=300,
        )

        self.assertRegex(
            monitor.monitor_id,
            r"^shadow:krx_bar_close_breakout:v1:[0-9a-f]{16}$",
        )

    def test_incremental_monitor_records_one_candidate_and_restart_does_not_duplicate(self) -> None:
        store = FakeStore((_rank(1), _bar(2, 0, 1000, 1010), _bar(3, 1, 1010, 1020), _bar(4, 2, 1030, 1040)))
        monitor = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)

        self.assertEqual(4, monitor.run_once())
        self.assertEqual(1, len(store.candidates))
        self.assertEqual("ENTER", store.decisions[-1]["final_action"])
        self.assertNotIn("entry_price", store.candidates[0])

        restarted = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        self.assertEqual(0, restarted.run_once())
        self.assertEqual(1, len(store.candidates))

    def test_stale_universe_blocks_candidate_and_reports_quality(self) -> None:
        store = FakeStore((_rank(1), _bar(2, 10, 1000, 1010), _bar(3, 11, 1010, 1020), _bar(4, 12, 1030, 1040)))
        monitor = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=30)

        monitor.run_once()

        self.assertEqual([], store.candidates)
        self.assertEqual("STALE", monitor.quality["status"])
        self.assertIn("candidate_universe_stale", monitor.quality["reason"])

    def test_after_market_bar_advances_cursor_without_shadow_evaluation(self) -> None:
        after_bar = _bar(2, 0, 1030, 1040)
        after_bar["observation_key"] = "2026-09-14T07:00:00+00:00"
        after_bar["available_at"] = "2026-09-14T07:01:02+00:00"
        after_bar["payload"]["bar_start"] = "2026-09-14T07:00:00+00:00"
        after_bar["payload"]["bar_end"] = "2026-09-14T07:01:00+00:00"
        store = FakeStore((after_bar,))
        monitor = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)

        self.assertEqual(1, monitor.run_once())
        self.assertEqual([], store.decisions)
        self.assertEqual("outside_krx_regular_strategy_session", monitor.quality["reason"])
        self.assertEqual(2, store.checkpoints[monitor.monitor_id]["cursor"])
        self.assertEqual("krx-regular/v1", store.checkpoints[monitor.monitor_id]["session_profile"])

    def test_sqlite_candidate_page_is_cursor_based_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            decision = {"decision_id": "d1", "decided_at": "2026-09-12T00:00:00+00:00"}
            candidate = {
                "event_id": "e1", "symbol": "005930", "available_at": "2026-09-12T00:00:00+00:00",
            }
            store.save_shadow_evaluation("monitor", decision, candidate, "2026-09-12T00:01:00+00:00")
            store.save_shadow_evaluation("monitor", decision, candidate, "2026-09-12T00:01:00+00:00")
            page = store.load_shadow_candidates(0, 100)
            empty = store.load_shadow_candidates(page["next_cursor"], 100)
            with self.assertRaisesRegex(ValueError, "immutable"):
                store.save_shadow_evaluation("monitor", {**decision, "decided_at": "changed"}, None)
            store.close()

        self.assertEqual(1, page["high_watermark"])
        self.assertEqual("e1", page["events"][0]["event_id"])
        self.assertEqual([], empty["events"])

    def test_new_profile_monitor_bootstraps_cursor_without_republishing_history(self) -> None:
        history = (_rank(1), _bar(2, 0, 1000, 1010), _bar(3, 1, 1010, 1020),
                   _bar(4, 2, 1030, 1040))

        class HistoricalStore(FakeStore):
            def load_observation_revisions(self, kind, limit=5000):
                return [row for row in self.observations if row["kind"] == kind][-limit:]

        store = HistoricalStore(history)
        monitor = CandidateMonitor(
            store, _config(), poll_seconds=1, universe_max_age_seconds=300,
            session_profile="krx-full-day/v1",
        )
        self.assertEqual(4, store.checkpoints[monitor.monitor_id]["cursor"])
        self.assertEqual(0, monitor.run_once())
        self.assertEqual([], store.candidates)
        self.assertEqual("bootstrapped_without_historical_alerts", monitor.quality["reason"])


if __name__ == "__main__":
    unittest.main()
