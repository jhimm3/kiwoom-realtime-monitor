from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.application.breakout_strategy import BreakoutStrategyConfig
from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.database_observation_readers import (
    OBSERVATION_DELIVERY_PROTOCOL, ObservationRevisionPage,
)
from kiwoom_monitor.central_server.postgres_access import DBWriterContext


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

    def load_observation_bootstrap(self, kinds, *, per_kind_limit):
        return ObservationRevisionPage()

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
    def test_pending_bootstrap_retries_without_checkpoint_or_historical_decisions(self):
        class PendingStore(FakeStore):
            page = ObservationRevisionPage(ready=False, reason="pending_sequence_commit")

            def load_observation_bootstrap(self, kinds, *, per_kind_limit):
                self.asserted_kinds = tuple(kinds)
                return self.page

            def load_observation_revisions(self, *_args, **_kwargs):
                raise AssertionError("separate raw history must not seed bootstrap")

        store = PendingStore()
        monitor = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        self.assertEqual("WARMUP", monitor.quality["status"])
        self.assertFalse(store.checkpoints)
        monitor._save_checkpoint(reason="error")
        self.assertEqual(0, monitor.run_once())
        self.assertFalse(store.checkpoints)
        # Restart while pending must retry the same initial seed, not restore cursor zero as ready.
        monitor = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        store.page = ObservationRevisionPage(rows=(_rank(1), _bar(2, 0, 1000, 1010)), safe_through=99)
        self.assertEqual(0, monitor.run_once())
        saved = store.checkpoints[monitor.monitor_id]
        self.assertEqual(2, saved["cursor"])
        self.assertEqual(1, len(saved["bars"]))
        self.assertEqual(OBSERVATION_DELIVERY_PROTOCOL, saved["delivery_protocol"])
        self.assertEqual([], store.decisions)
        restarted = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        self.assertEqual(0, restarted.run_once())
        self.assertEqual(saved, store.checkpoints[monitor.monitor_id])

    def test_failed_bootstrap_checkpoint_cannot_begin_incremental_processing(self):
        class Store(FakeStore):
            ready = False
            fail = True
            def load_observation_bootstrap(self, kinds, *, per_kind_limit):
                return ObservationRevisionPage(rows=(_rank(1),), ready=self.ready)
            def save_shadow_monitor_state(self, monitor_id, document):
                if self.fail:
                    raise OSError("seed COMMIT failed")
                super().save_shadow_monitor_state(monitor_id, document)

        store = Store()
        monitor = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        store.ready = True
        with self.assertRaisesRegex(OSError, "seed COMMIT failed"):
            monitor.run_once()
        self.assertTrue(monitor._bootstrap_pending)
        self.assertFalse(store.checkpoints)
        store.fail = False
        self.assertEqual(0, monitor.run_once())
        self.assertFalse(monitor._bootstrap_pending)
        self.assertEqual(1, store.checkpoints[monitor.monitor_id]["cursor"])

    def test_legacy_checkpoint_is_not_relabelled_as_safe_delivery(self):
        store = FakeStore()
        monitor = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        store.checkpoints[monitor.monitor_id].pop("delivery_protocol")
        restarted = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        restarted._save_checkpoint()
        self.assertNotIn("delivery_protocol", store.checkpoints[monitor.monitor_id])

    def test_checkpoint_sources_distinguish_bootstrap_and_processed_batches(self) -> None:
        class SourceStore(FakeStore):
            def __init__(self, observations=()):
                super().__init__(observations)
                self.sources = []

            def save_shadow_monitor_state(self, monitor_id, document):
                self.sources.append(DBWriterContext("test", "test", "test").source)
                super().save_shadow_monitor_state(monitor_id, document)

        store = SourceStore((_rank(1),))
        monitor = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        self.assertEqual(["candidate_monitor.bootstrap"], store.sources)
        self.assertEqual(1, monitor.run_once())
        self.assertEqual(["candidate_monitor.bootstrap", "candidate_monitor.processed"], store.sources)
        self.assertEqual(0, monitor.run_once())
        self.assertEqual(2, len(store.sources))

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

    def test_checkpoint_failure_after_candidate_commit_replays_without_duplicate_event(self) -> None:
        class CheckpointFailureStore(FakeStore):
            fail_checkpoint = False

            def save_shadow_monitor_state(self, monitor_id, document):
                if self.fail_checkpoint:
                    raise RuntimeError("checkpoint failed")
                super().save_shadow_monitor_state(monitor_id, document)

        store = CheckpointFailureStore((
            _rank(1), _bar(2, 0, 1000, 1010),
            _bar(3, 1, 1010, 1020), _bar(4, 2, 1030, 1040),
        ))
        monitor = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        store.fail_checkpoint = True
        with self.assertRaisesRegex(RuntimeError, "checkpoint failed"):
            monitor.run_once()
        self.assertEqual(1, len(store.candidates))
        self.assertEqual(0, store.checkpoints[monitor.monitor_id]["cursor"])

        store.fail_checkpoint = False
        restarted = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        self.assertEqual(4, restarted.run_once())
        self.assertEqual(1, len(store.candidates))
        self.assertEqual(4, store.checkpoints[monitor.monitor_id]["cursor"])

    def test_failed_final_checkpoint_retries_while_queue_is_empty_then_stops_writing(self) -> None:
        class RetryStore(FakeStore):
            fail_checkpoint = False
            attempts = 0
            sources = []

            def save_shadow_monitor_state(self, monitor_id, document):
                self.attempts += 1
                self.sources.append(DBWriterContext("test", "test", "test").source)
                if self.fail_checkpoint:
                    raise RuntimeError("checkpoint unavailable")
                super().save_shadow_monitor_state(monitor_id, document)

        store = RetryStore((
            _rank(1), _bar(2, 0, 1000, 1010),
            _bar(3, 1, 1010, 1020), _bar(4, 2, 1030, 1040),
        ))
        monitor = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        store.fail_checkpoint = True
        with self.assertRaisesRegex(RuntimeError, "checkpoint unavailable"):
            monitor.run_once()
        # A second failure with no new input must also remain retryable.
        with self.assertRaisesRegex(RuntimeError, "checkpoint unavailable"):
            monitor.run_once()
        self.assertEqual(0, store.checkpoints[monitor.monitor_id]["cursor"])
        self.assertEqual(3, len(store.decisions))
        self.assertEqual(1, len(store.candidates))

        store.fail_checkpoint = False
        self.assertEqual(0, monitor.run_once())
        saved = store.checkpoints[monitor.monitor_id]
        self.assertEqual(4, saved["cursor"])
        self.assertEqual(monitor._state.to_dict(), saved["strategy_state"])
        self.assertEqual(3, len(saved["bars"]))
        attempts = store.attempts
        self.assertEqual(0, monitor.run_once())
        restarted = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        self.assertEqual(0, restarted.run_once())
        self.assertEqual(attempts, store.attempts)
        self.assertEqual(3, len(store.decisions))
        self.assertEqual(1, len(store.candidates))
        self.assertEqual([
            "candidate_monitor.bootstrap", "candidate_monitor.processed",
            "candidate_monitor.retry", "candidate_monitor.retry",
        ], store.sources)

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
            def load_observation_bootstrap(self, kinds, *, per_kind_limit):
                return ObservationRevisionPage(rows=tuple(self.observations), safe_through=100)

        store = HistoricalStore(history)
        monitor = CandidateMonitor(
            store, _config(), poll_seconds=1, universe_max_age_seconds=300,
            session_profile="krx-full-day/v1",
        )
        self.assertEqual(4, store.checkpoints[monitor.monitor_id]["cursor"])
        self.assertEqual(0, monitor.run_once())
        self.assertEqual([], store.candidates)
        self.assertEqual("bootstrapped_without_historical_alerts", monitor.quality["reason"])
        self.assertEqual(OBSERVATION_DELIVERY_PROTOCOL,
                         store.checkpoints[monitor.monitor_id]["delivery_protocol"])


if __name__ == "__main__":
    unittest.main()
