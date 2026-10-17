from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
from copy import deepcopy
from dataclasses import replace
from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3
from unittest.mock import Mock, patch
from pathlib import Path

from kiwoom_monitor.application.breakout_strategy import BreakoutStrategyConfig, StrategyState
from kiwoom_monitor.application.pullback_reacceleration_strategy import (
    FAMILY_ID as PULLBACK_FAMILY_ID, PullbackReaccelerationConfig,
)
from kiwoom_monitor.application.research_families import BREAKOUT_FAMILY_ID, family_for_config
from kiwoom_monitor.application.research_replay import replay_candidate_universe, replay_krx_minute_bars
from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor, run_shadow_monitors_once
from kiwoom_monitor.central_server.observation_frame_recovery import drain_thread_call
from kiwoom_monitor.central_server.market_observations import ranking_observation, minute_bar_observation
from kiwoom_monitor.domain.market_data_contract import DataCompleteness, DataValueKind, ObservationOrigin
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.database_observation_readers import (
    OBSERVATION_DELIVERY_PROTOCOL, ObservationRevisionPage,
)
from kiwoom_monitor.central_server.postgres_access import DBWriterContext
from kiwoom_monitor.central_server.market_theme_shadow_monitor import (
    MarketThemeShadowMonitor, MarketThemeShadowRuntime, run_market_theme_shadow_monitors_once,
)
from kiwoom_monitor.application.market_theme_shadow import (
    evaluate_market_theme_shadow, evaluate_stock_thesis_shadow, evaluate_market_theme_shadow_batch,
)
from kiwoom_monitor.application import market_theme_shadow as shadow_context
from tests.unit import test_entry_thesis as thesis_fixtures
from tests.unit.test_market_research_features import shadow_inputs


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
    def test_operational_freshness_expires_without_changing_last_evaluation_or_storage(self):
        from datetime import datetime, timezone
        store = FakeStore([_rank(1), _bar(2, 0, 100, 110)])
        monitor = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300)
        monitor.run_once()
        before = (dict(store.checkpoints), list(store.decisions), list(store.candidates), monitor.quality)
        fresh = monitor.operational_status(now=datetime(2026, 9, 12, 0, 5, tzinfo=timezone.utc))
        stale = monitor.operational_status(now=datetime(2026, 9, 12, 0, 5, 1, tzinfo=timezone.utc))
        self.assertEqual('fresh', fresh['universe']['state'])
        self.assertEqual('stale', stale['universe']['state'])
        self.assertEqual('READY', stale['last_evaluation']['status'])
        self.assertIsNone(stale['current_all_inputs_fresh'])
        self.assertEqual(before, (store.checkpoints, store.decisions, store.candidates, monitor.quality))
        monitor._quality.update(status='ERROR', reason='private-credential-value')
        self.assertNotIn('private-credential-value', str(monitor.operational_status()))
        monitor._universes.clear()
        self.assertEqual('missing', monitor.operational_status()['universe']['state'])

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


class RegisteredFamilyShadowTests(unittest.TestCase):
    def pullback_config(self):
        values = _config().to_dict()
        values.pop("rolling_factor_version")
        values.pop("buffer_bps")
        return PullbackReaccelerationConfig(
            **{**values, "pullback_factor_version": "v1", "lookback_bars": 3},
            minimum_pullback_bps=500, minimum_reacceleration_bps=100,
        )

    def observations(self):
        values = [_rank(1)]
        prices = ((990, 1000, 980, 990), (1000, 1100, 1050, 1080),
                  (1030, 1040, 990, 1000), (1005, 1030, 1000, 1020))
        for minute, (open_, high, low, close) in enumerate(prices):
            row = _bar(minute + 2, minute, close, high)
            row["payload"].update(open=open_, low=low)
            values.append(row)
        return values

    def test_both_registered_families_match_direct_evaluations_using_only_minute_inputs(self):
        class Store(FakeStore):
            def __init__(self, observations):
                super().__init__(observations)
                self.read_kinds = []

            def load_observation_bootstrap(self, kinds, *, per_kind_limit):
                self.read_kinds.append(tuple(kinds))
                return super().load_observation_bootstrap(kinds, per_kind_limit=per_kind_limit)

            def load_observation_revisions_after(self, cursor, kinds, limit):
                self.read_kinds.append(tuple(kinds))
                return super().load_observation_revisions_after(cursor, kinds, limit)

            def load_second_trade_bars(self, *args, **kwargs):
                raise AssertionError("minute strategies must not request second bars")

        rows = self.observations()
        store = Store(rows)
        bars = replay_krx_minute_bars(rows)
        universe = replay_candidate_universe(rows)
        final_actions = []
        for config in (_config(), self.pullback_config()):
            with self.subTest(family=family_for_config(config).family_id):
                monitor = CandidateMonitor(store, config, poll_seconds=1, universe_max_age_seconds=300)
                self.assertEqual(("minute_bar/KRX/closed/complete", "top20_membership/as_available"),
                                 monitor.required_inputs)
                self.assertIs(family_for_config(config).evaluate_bar, monitor._family.evaluate_bar)
                monitor.run_once()
                state = StrategyState()
                expected = []
                for index, bar in enumerate(bars):
                    evaluation = family_for_config(config).evaluate_bar(
                        run_id=monitor.monitor_id, evaluation_bar=bar,
                        bar_history=bars[max(0, index - config.lookback_bars - 1):index + 1],
                        universe_frames=universe, config=config, state=state,
                    )
                    expected.append(evaluation.decision.to_dict())
                    state = evaluation.state
                actual = [row for row in store.decisions if row["run_id"] == monitor.monitor_id]
                self.assertEqual(expected, actual)
                self.assertEqual(state, monitor._state)
                self.assertIsNone(state.position_quantity)
                final_actions.append(actual[-1]["final_action"])
        self.assertEqual(["NO_TRADE", "ENTER"], final_actions)
        self.assertTrue(all(kinds == ("top20_membership", "minute_bar") for kinds in store.read_kinds))
        self.assertEqual(4, len(store.read_kinds))

    def test_shared_native_ledger_restores_each_family_without_orders_or_duplicate_candidates(self):
        rows = self.observations()

        class Store(SQLiteQueryStore):
            def load_observation_bootstrap(self, kinds, *, per_kind_limit):
                return ObservationRevisionPage()

            def load_observation_revisions_after(self, cursor, kinds, limit):
                return [row for row in rows if row["accepted_sequence"] > cursor and row["kind"] in kinds][:limit]

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "shared-shadow.sqlite3")
            store.initialize()
            self.addCleanup(store.close)
            configs = (_config(), self.pullback_config())
            monitors = [CandidateMonitor(store, config, poll_seconds=1, universe_max_age_seconds=300)
                        for config in configs]
            self.assertNotEqual(monitors[0].monitor_id, monitors[1].monitor_id)
            with patch("kiwoom_monitor.central_server.mock_automation_runner."
                       "dispatch_mock_automation_decision_from_risk",
                       side_effect=AssertionError("shadow must not dispatch an order")) as dispatch:
                for monitor in monitors:
                    self.assertEqual(5, monitor.run_once())
                page = store.load_shadow_candidates()
                self.assertEqual(1, len(page["events"]))
                self.assertEqual("pullback_reacceleration", page["events"][0]["setup"])
                checkpoints = {monitor.monitor_id: store.load_shadow_monitor_state(monitor.monitor_id)
                               for monitor in monitors}
                for monitor, config in zip(monitors, configs):
                    checkpoint = checkpoints[monitor.monitor_id]
                    self.assertEqual(config.to_dict(), checkpoint["strategy_config"])
                    self.assertEqual(5, checkpoint["cursor"])
                    restarted = CandidateMonitor(store, config, poll_seconds=1, universe_max_age_seconds=300)
                    self.assertEqual(monitor.monitor_id, restarted.monitor_id)
                    self.assertEqual(monitor._state, restarted._state)
                    self.assertEqual(0, restarted.run_once())
                    self.assertEqual(checkpoint, store.load_shadow_monitor_state(monitor.monitor_id))
                    self.assertIsNone(restarted._state.position_quantity)
                dispatch.assert_not_called()
                self.assertEqual(page, store.load_shadow_candidates())
            self.assertEqual(["flat", "candidate"], [monitor._state.status for monitor in monitors])

    def test_json_default_preserves_breakout_and_explicit_family_selects_pullback(self):
        kwargs = {"poll_seconds": 1, "universe_max_age_seconds": 300}
        legacy = CandidateMonitor.from_json(FakeStore(), json.dumps(_config().to_dict()), **kwargs)
        explicit = CandidateMonitor.from_json(FakeStore(), json.dumps(_config().to_dict()),
                                               family_id=BREAKOUT_FAMILY_ID, **kwargs)
        pullback = CandidateMonitor.from_json(FakeStore(), json.dumps(self.pullback_config().to_dict()),
                                              family_id=PULLBACK_FAMILY_ID, **kwargs)
        self.assertEqual(legacy.monitor_id, explicit.monitor_id)
        self.assertEqual(legacy._config, explicit._config)
        self.assertEqual(PULLBACK_FAMILY_ID, pullback._family.family_id)
        self.assertNotEqual(legacy.monitor_id, pullback.monitor_id)

    def test_unknown_or_mismatched_family_is_rejected_before_store_access(self):
        store = Mock()
        for family, parameters in (("theme-leadership/price-trade-value-v1", _config().to_dict()),
                                   (PULLBACK_FAMILY_ID, _config().to_dict()),
                                   (BREAKOUT_FAMILY_ID, self.pullback_config().to_dict())):
            with self.subTest(family=family), self.assertRaises(ValueError):
                CandidateMonitor.from_json(store, json.dumps(parameters), family_id=family,
                                           poll_seconds=1, universe_max_age_seconds=300)
        self.assertEqual([], store.mock_calls)

    def test_finer_input_contract_cannot_be_silently_admitted_by_minute_reader(self):
        store = Mock()
        config = _config()
        family = replace(family_for_config(config), contract={
            **family_for_config(config).contract,
            "required_inputs": ("second_bar/KRX/complete", "top20_membership/as_available"),
        })
        with patch("kiwoom_monitor.central_server.candidate_monitor.family_for_config", return_value=family):
            with self.assertRaisesRegex(ValueError, "input contract"):
                CandidateMonitor(store, config, poll_seconds=1, universe_max_age_seconds=300)
        self.assertEqual([], store.mock_calls)

    def test_partial_or_open_minute_input_does_not_create_pullback_decisions(self):
        for field, value in (("capture_quality", "partial"), ("window_closed", False)):
            with self.subTest(field=field):
                rows = self.observations()
                for row in rows[1:]:
                    row["payload"][field] = value
                store = FakeStore(rows)
                monitor = CandidateMonitor(store, self.pullback_config(), poll_seconds=1, universe_max_age_seconds=300)
                monitor.run_once()
                self.assertEqual([], store.decisions)
                self.assertEqual([], store.candidates)
                self.assertEqual(5, store.checkpoints[monitor.monitor_id]["cursor"])
                self.assertEqual("flat", monitor._state.status)


class SharedPageStore(FakeStore):
    def __init__(self, observations=()):
        super().__init__(observations)
        self.page_calls = []

    def load_observation_revision_page(self, after_sequence, kinds, limit=1000, *, through_sequence=None):
        self.page_calls.append((after_sequence, tuple(kinds), limit, through_sequence))
        safe = max((row["accepted_sequence"] for row in self.observations), default=0)
        if through_sequence is not None:
            safe = min(safe, through_sequence)
        rows = tuple(row for row in self.observations if after_sequence < row["accepted_sequence"] <= safe
                     and row["kind"] in kinds)
        return ObservationRevisionPage(rows[:limit], safe_through=safe, exhausted=len(rows) <= limit)


class SharedShadowDeliveryTests(unittest.TestCase):
    pullback_config = RegisteredFamilyShadowTests.pullback_config
    observations = RegisteredFamilyShadowTests.observations

    def monitors(self, store):
        return tuple(CandidateMonitor(store, config, poll_seconds=1, universe_max_age_seconds=300)
                     for config in (_config(), self.pullback_config()))

    def test_one_read_fans_out_and_redelivery_does_not_read_or_repeat_decisions(self):
        store = SharedPageStore(self.observations())
        monitors = self.monitors(store)
        result = run_shadow_monitors_once(monitors)
        self.assertEqual(1, result["shared_reads"])
        self.assertEqual(5, result["rows_read"])
        self.assertEqual({}, result["errors"])
        self.assertEqual([5, 5], list(result["processed"].values()))
        before = (list(store.decisions), list(store.candidates), dict(store.checkpoints))
        page = store.load_observation_revision_page(0, ("top20_membership", "minute_bar"))
        with patch.object(store, "load_observation_revision_page", side_effect=AssertionError("extra read")), \
             patch.object(store, "load_observation_revisions_after", side_effect=AssertionError("extra read")):
            for monitor in monitors:
                self.assertEqual(0, monitor.consume_observation_page(page, after_sequence=0, source_store=store))
        self.assertEqual(before, (store.decisions, store.candidates, store.checkpoints))

    def test_shared_conversion_preserves_all_decisions_states_and_checkpoints(self):
        outputs, counts = [], []
        for shared in (False, True):
            store = SharedPageStore(self.observations())
            monitors = self.monitors(store)
            with patch("kiwoom_monitor.central_server.candidate_monitor.replay_krx_minute_bars",
                       wraps=replay_krx_minute_bars) as bars, \
                 patch("kiwoom_monitor.central_server.candidate_monitor.replay_candidate_universe",
                       wraps=replay_candidate_universe) as universes:
                if shared:
                    self.assertEqual({}, run_shadow_monitors_once(monitors)["errors"])
                else:
                    for monitor in monitors:
                        monitor.run_once()
                counts.append((bars.call_count, universes.call_count))
            outputs.append((store.decisions, store.candidates, store.checkpoints))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual([(8, 2), (4, 1)], counts)

    def test_session_profiles_and_native_page_revisions_do_not_share_conversion(self):
        store = SharedPageStore(self.observations())
        regular = self.monitors(store)[0]
        full_day = CandidateMonitor(store, _config(), poll_seconds=1, universe_max_age_seconds=300,
                                    session_profile="krx-full-day/v1")
        with patch("kiwoom_monitor.central_server.candidate_monitor.replay_krx_minute_bars",
                   wraps=replay_krx_minute_bars) as bars:
            self.assertEqual({}, run_shadow_monitors_once((regular, full_day))["errors"])
            self.assertEqual(8, bars.call_count)
            # Same observation identity with a later native revision needs a fresh
            # frame, even if the preceding round parsed it under both profiles.
            revised = self.observations()[-1]
            revised["accepted_sequence"] = 6
            revised["revision_id"] = "revised-bar"
            revised["payload"]["close"] = 1030
            store.observations.append(revised)
            self.assertEqual({}, run_shadow_monitors_once((regular, full_day))["errors"])
            self.assertEqual(10, bars.call_count)
        for monitor in (regular, full_day):
            self.assertEqual("revised-bar", list(monitor._bars.values())[-1].revision_id)
            self.assertEqual(1030, list(monitor._bars.values())[-1].close)

    def test_bounded_pages_catch_up_lagging_cursor_without_replaying_fast_consumer(self):
        store = SharedPageStore(self.observations())
        fast, slow = self.monitors(store)
        fast.run_once(limit=2)
        first = run_shadow_monitors_once((fast, slow), limit=2)
        self.assertEqual({fast.monitor_id: 0, slow.monitor_id: 2}, first["processed"])
        self.assertEqual(0, first["after_sequence"])
        self.assertEqual(2, run_shadow_monitors_once((fast, slow), limit=2)["after_sequence"])
        self.assertEqual(4, run_shadow_monitors_once((fast, slow), limit=2)["after_sequence"])
        self.assertEqual([5, 5], [fast._cursor, slow._cursor])
        self.assertEqual(8, len(store.decisions))
        self.assertEqual(1, len(store.candidates))
        self.assertEqual(3, len(store.page_calls))

    def test_one_checkpoint_failure_is_reported_without_blocking_other_strategy(self):
        store = SharedPageStore(self.observations())
        fast, failing = self.monitors(store)
        original = store.save_shadow_monitor_state
        def save(monitor_id, document):
            if monitor_id == failing.monitor_id:
                raise RuntimeError("checkpoint failed")
            original(monitor_id, document)
        with patch.object(store, "save_shadow_monitor_state", side_effect=save):
            result = run_shadow_monitors_once((failing, fast))
        self.assertIn(failing.monitor_id, result["errors"])
        self.assertEqual(5, result["processed"][failing.monitor_id])
        self.assertTrue(result["checkpoint_pending"][failing.monitor_id])
        self.assertEqual(5, result["cursors"][failing.monitor_id])
        self.assertEqual(5, result["processed"][fast.monitor_id])
        self.assertEqual(5, store.checkpoints[fast.monitor_id]["cursor"])
        self.assertEqual(0, store.checkpoints[failing.monitor_id]["cursor"])
        self.assertTrue(failing._checkpoint_pending)
        before = (list(store.decisions), list(store.candidates), failing._state)
        retried = run_shadow_monitors_once((fast, failing))
        self.assertEqual({}, retried["errors"])
        self.assertEqual(5, store.checkpoints[failing.monitor_id]["cursor"])
        self.assertFalse(failing._checkpoint_pending)
        self.assertEqual(before, (store.decisions, store.candidates, failing._state))

    def test_unsafe_or_invalid_page_never_advances_strategy_state(self):
        store = SharedPageStore(self.observations())
        monitors = self.monitors(store)
        page = store.load_observation_revision_page(0, ("top20_membership", "minute_bar"))
        cases = (replace(page, rows=page.rows[::-1]), replace(page, safe_through=1),
                 replace(page, rows=(*page.rows, page.rows[-1])),
                 replace(page, ready=False), replace(page, rows=(), exhausted=False),
                 replace(page, rows=page.rows * 1001))
        for invalid in cases:
            with self.subTest(page=invalid), self.assertRaises(ValueError):
                monitors[0].consume_observation_page(invalid, after_sequence=0, source_store=store)
        with patch.object(store, "load_observation_revision_page", return_value=ObservationRevisionPage(
            ready=False, exhausted=False, reason="pending_sequence_commit",
        )):
            waiting = run_shadow_monitors_once(monitors)
        self.assertEqual("pending_sequence_commit", waiting["read_state"])
        self.assertEqual({}, waiting["errors"])
        self.assertEqual([0, 0], [monitor._cursor for monitor in monitors])
        self.assertEqual([], store.decisions)

    def test_evaluation_write_failure_retries_same_candidate_after_failure_or_lost_ack(self):
        for lost_ack in (False, True):
            with self.subTest(lost_ack=lost_ack):
                store = SharedPageStore(self.observations())
                healthy, failing = self.monitors(store)
                original = store.save_shadow_evaluation
                fail = True
                def save(monitor_id, decision, candidate, expires_at):
                    nonlocal fail
                    if fail and monitor_id == failing.monitor_id and decision["final_action"] == "ENTER":
                        fail = False
                        if lost_ack:
                            original(monitor_id, decision, candidate, expires_at)
                        raise OSError("evaluation write ACK failed")
                    original(monitor_id, decision, candidate, expires_at)
                with patch.object(store, "save_shadow_evaluation", side_effect=save):
                    failed = run_shadow_monitors_once((failing, healthy))
                    self.assertIn(failing.monitor_id, failed["errors"])
                    self.assertEqual(4, failing._cursor)
                    self.assertEqual(5, healthy._cursor)
                    recovered = run_shadow_monitors_once((healthy, failing))
                self.assertEqual({}, recovered["errors"])
                self.assertEqual(1, len(store.candidates))
                self.assertEqual("ENTER", [row for row in store.decisions
                    if row["run_id"] == failing.monitor_id][-1]["final_action"])
                self.assertEqual(8, len(store.decisions))
                self.assertEqual(5, store.checkpoints[failing.monitor_id]["cursor"])

    def test_source_identity_cursor_gap_duplicate_owner_and_running_worker_are_rejected(self):
        store = SharedPageStore(self.observations())
        monitors = self.monitors(store)
        page = store.load_observation_revision_page(0, ("top20_membership", "minute_bar"))
        with self.assertRaises(ValueError):
            monitors[0].consume_observation_page(page, after_sequence=1, source_store=store)
        with self.assertRaises(ValueError):
            monitors[0].consume_observation_page(page, after_sequence=0, source_store=SharedPageStore())
        other = CandidateMonitor(SharedPageStore(), self.pullback_config(), poll_seconds=1,
                                 universe_max_age_seconds=300)
        with self.assertRaises(ValueError):
            run_shadow_monitors_once((monitors[0], other))
        with self.assertRaises(ValueError):
            run_shadow_monitors_once((monitors[0], monitors[0]))
        with patch.object(monitors[0], "_task", Mock(done=Mock(return_value=False))):
            with self.assertRaisesRegex(RuntimeError, "worker must stop"):
                run_shadow_monitors_once(monitors)
        self.assertEqual(1, len(store.page_calls))

    def test_native_evaluation_commit_failure_and_lost_ack_retry_identical_documents(self):
        for lost_ack in (False, True):
            with self.subTest(lost_ack=lost_ack), tempfile.TemporaryDirectory() as directory:
                source = SharedPageStore(self.observations())
                class Store(SQLiteQueryStore):
                    def load_observation_bootstrap(self, kinds, *, per_kind_limit):
                        return ObservationRevisionPage()
                    def load_observation_revision_page(self, *args, **kwargs):
                        return source.load_observation_revision_page(*args, **kwargs)
                store = Store(Path(directory) / "native-ack.sqlite3")
                store.initialize()
                try:
                    healthy, failing = self.monitors(store)
                    original = store.save_shadow_evaluation
                    attempts = []
                    def save(monitor_id, decision, candidate, expires_at):
                        if monitor_id == failing.monitor_id and decision["final_action"] == "ENTER":
                            attempts.append((decision, candidate, expires_at))
                            if len(attempts) == 1:
                                if lost_ack:
                                    original(monitor_id, decision, candidate, expires_at)
                                raise OSError("native evaluation ACK failed")
                        original(monitor_id, decision, candidate, expires_at)
                    with patch.object(store, "save_shadow_evaluation", side_effect=save):
                        self.assertIn(failing.monitor_id, run_shadow_monitors_once((failing, healthy))["errors"])
                        self.assertEqual("flat", failing._state.status)
                        self.assertEqual({}, run_shadow_monitors_once((healthy, failing))["errors"])
                    self.assertEqual(2, len(attempts))
                    self.assertEqual(attempts[0], attempts[1])
                    self.assertEqual(1, len(store.load_shadow_candidates()["events"]))
                    self.assertEqual(5, store.load_shadow_monitor_state(failing.monitor_id)["cursor"])
                finally:
                    store.close()

    def test_consumer_mutation_does_not_change_native_page_or_other_strategy_inputs(self):
        store = SharedPageStore(self.observations())
        monitors = self.monitors(store)
        original = monitors[0]._consume
        def consume(row, **kwargs):
            original(row, **kwargs)
            row["payload"]["close"] = 999999
        with patch.object(monitors[0], "_consume", side_effect=consume):
            result = run_shadow_monitors_once(monitors)
        self.assertEqual({}, result["errors"])
        self.assertEqual(1020, store.observations[-1]["payload"]["close"])
        self.assertEqual("ENTER", store.decisions[-1]["final_action"])
        self.assertEqual(1020, list(monitors[1]._bars.values())[-1].close)

    def test_legacy_recovery_remains_frames_only_and_does_not_block_ready_strategy(self):
        store = SharedPageStore(self.observations())
        ready, original = self.monitors(store)
        original.run_once(limit=2)
        store.checkpoints[original.monitor_id].pop("delivery_protocol")
        recovering = CandidateMonitor(store, self.pullback_config(), poll_seconds=1,
                                      universe_max_age_seconds=300)
        result = run_shadow_monitors_once((recovering, ready), limit=1)
        self.assertEqual([recovering.monitor_id], result["deferred"])
        self.assertEqual(2, recovering._cursor)
        self.assertEqual(1, result["processed"][ready.monitor_id])
        self.assertIsNotNone(recovering._frame_recovery)
        result = run_shadow_monitors_once((recovering, ready), limit=1)
        self.assertIsNone(recovering._frame_recovery)
        self.assertEqual([recovering.monitor_id], result["deferred"])
        self.assertEqual(2, len(store.decisions))  # One pre-restart bar per strategy, no historical re-evaluation.
        self.assertEqual(OBSERVATION_DELIVERY_PROTOCOL,
                         store.checkpoints[recovering.monitor_id]["delivery_protocol"])

    def test_native_read_failure_is_visible_before_live_cursor_changes(self):
        store = SharedPageStore(self.observations())
        monitors = self.monitors(store)
        with patch.object(store, "load_observation_revision_page", side_effect=OSError("source read failed")):
            with self.assertRaisesRegex(OSError, "source read failed"):
                run_shadow_monitors_once(monitors)
        self.assertEqual([0, 0], [monitor._cursor for monitor in monitors])
        self.assertEqual([], store.decisions)

    def test_native_sqlite_read_reduction_preserves_identical_results_and_write_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed_path = root / "seed.sqlite3"
            seed = SQLiteQueryStore(seed_path)
            seed.initialize()
            self.monitors(seed)  # Both cold checkpoints precede the new input.
            rows = self.observations()
            at = datetime.fromisoformat(rows[0]["available_at"])
            seed.save_dataset_snapshot("top20_membership", "2026-09-12", at.isoformat(), rows[0]["payload"],
                observation=ranking_observation("2026-09-12", at.isoformat(), rows[0]["payload"], at,
                                                source="fixed-shadow-fixture"))
            values, observations = [], []
            for row in rows[1:]:
                start = datetime.fromisoformat(row["payload"]["bar_start"])
                value = {**row["payload"], "trading_date": start.date().isoformat(),
                         "minute": start.strftime("%H:%M"),
                         "updated_at": datetime.fromisoformat(row["available_at"]).timestamp()}
                values.append(value)
                observations.append((f"{start.date().isoformat()}T{value['minute']}", minute_bar_observation(
                    value, origin=ObservationOrigin.QUERY, completeness=DataCompleteness.COMPLETE,
                    source="fixed-shadow-fixture", value_kind=DataValueKind.ACTUAL)))
            seed.replace_minute_bars(values, observations=observations)
            seed.close()
            reports, outputs = [], []
            for shared in (False, True):
                target = root / ("shared.sqlite3" if shared else "separate.sqlite3")
                with closing(sqlite3.connect(seed_path)) as source, closing(sqlite3.connect(target)) as destination:
                    source.backup(destination)
                store = SQLiteQueryStore(target)
                monitors = self.monitors(store)
                statements = []
                original_connect = sqlite3.connect
                def connect(*args, **kwargs):
                    connection = original_connect(*args, **kwargs)
                    connection.set_trace_callback(statements.append)
                    return connection
                with patch("sqlite3.connect", side_effect=connect) as connections, \
                     patch.object(store, "load_observation_revision_page", wraps=store.load_observation_revision_page) as reads, \
                     patch("kiwoom_monitor.central_server.candidate_monitor.replay_krx_minute_bars",
                           wraps=replay_krx_minute_bars) as adapters, \
                     patch.object(store, "save_shadow_evaluation", wraps=store.save_shadow_evaluation) as writes:
                    if shared:
                        result = run_shadow_monitors_once(monitors)
                        self.assertEqual({}, result["errors"])
                    else:
                        for monitor in monitors:
                            monitor.run_once()
                    reports.append({"source_reads": reads.call_count, "connections": connections.call_count,
                        "minute_frame_conversions": adapters.call_count,
                        "write_commits": sum(sql.strip().upper() == "COMMIT" for sql in statements),
                        "observation_selects": sum(sql.lstrip().upper().startswith("SELECT")
                            and "central_observation_revisions" in sql for sql in statements)})
                    outputs.append(([call.args for call in writes.call_args_list],
                                    [monitor._state for monitor in monitors], store.load_shadow_candidates()))
                store.close()
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual([2, 1], [report["source_reads"] for report in reports])
            self.assertEqual([4, 2], [report["observation_selects"] for report in reports])
            self.assertEqual(reports[0]["connections"] - 1, reports[1]["connections"])
            self.assertEqual(reports[0]["write_commits"], reports[1]["write_commits"])
            self.assertEqual([8, 4], [report["minute_frame_conversions"] for report in reports])
            print("SHARED_SHADOW_NATIVE_PROBE=" + json.dumps({"before": reports[0], "after": reports[1]}))


class SharedShadowDrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_batches_serialize_and_do_not_evaluate_the_same_prefix_twice(self):
        helper = SharedShadowDeliveryTests()
        store = SharedPageStore(helper.observations())
        monitors = helper.monitors(store)
        entered, release = threading.Event(), threading.Event()
        original = store.load_observation_revision_page
        calls = []
        def read(*args, **kwargs):
            calls.append(args[0])
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("shared source was not released")
            return original(*args, **kwargs)
        with patch.object(store, "load_observation_revision_page", side_effect=read):
            first = asyncio.create_task(drain_thread_call(run_shadow_monitors_once, monitors))
            second = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                second = asyncio.create_task(drain_thread_call(run_shadow_monitors_once, monitors[::-1]))
                await asyncio.sleep(0.02)
                self.assertEqual([0], calls)
                self.assertFalse(second.done())
                release.set()
                results = await asyncio.gather(first, second)
            finally:
                release.set()
                await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)
        self.assertEqual([0, 5], calls)
        self.assertEqual([10, 0], [sum(result["processed"].values()) for result in results])
        self.assertEqual(8, len(store.decisions))
        self.assertEqual(1, len(store.candidates))

    async def test_cancelled_shared_delivery_drains_native_read_and_both_consumers(self):
        helper = SharedShadowDeliveryTests()
        store = SharedPageStore(helper.observations())
        monitors = helper.monitors(store)
        entered, release = threading.Event(), threading.Event()
        original = store.load_observation_revision_page
        def read(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("shared source was not released")
            return original(*args, **kwargs)
        with patch.object(store, "load_observation_revision_page", side_effect=read):
            task = asyncio.create_task(drain_thread_call(run_shadow_monitors_once, monitors))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(0.02)
                self.assertFalse(task.done())
                task.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual([5, 5], [monitor._cursor for monitor in monitors])
        self.assertEqual(1, len(store.candidates))
        self.assertEqual([5, 5], [store.checkpoints[monitor.monitor_id]["cursor"] for monitor in monitors])


class CandidateMonitorShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_checkpoint_drains_before_repeated_close_and_store_close(self):
        for save_fails in (False, True):
            with self.subTest(save_fails=save_fails), tempfile.TemporaryDirectory() as directory:
                loop = asyncio.get_running_loop()
                started = asyncio.Event()
                release = threading.Event()
                finished = threading.Event()

                class BlockingStore(SQLiteQueryStore):
                    fail_iteration = False

                    def load_observation_revisions_after(self, *args, **kwargs):
                        if self.fail_iteration:
                            raise RuntimeError("injected iteration failure")
                        return super().load_observation_revisions_after(*args, **kwargs)

                    def save_shadow_monitor_state(self, monitor_id, document):
                        if document["quality"]["status"] != "ERROR":
                            return super().save_shadow_monitor_state(monitor_id, document)
                        loop.call_soon_threadsafe(started.set)
                        try:
                            if not release.wait(5):
                                raise TimeoutError("test checkpoint was not released")
                            if save_fails:
                                raise RuntimeError("injected checkpoint failure")
                            return super().save_shadow_monitor_state(monitor_id, document)
                        finally:
                            finished.set()

                store = BlockingStore(Path(directory) / "monitor.db")
                store.initialize()
                monitor = CandidateMonitor(
                    store, _config(), poll_seconds=60, universe_max_age_seconds=300,
                )
                store.fail_iteration = True
                closes = []
                try:
                    with self.assertLogs(level="ERROR") as logs:
                        await monitor.start()
                        await asyncio.wait_for(started.wait(), 2)
                        closes.append(asyncio.create_task(monitor.close()))
                        await asyncio.sleep(0)
                        closes.append(asyncio.create_task(monitor.close()))
                        done, _ = await asyncio.wait(closes, timeout=0.05)
                        self.assertFalse(finished.is_set())
                        self.assertFalse(done, "close returned while the native checkpoint was running")
                        release.set()
                        await asyncio.wait_for(asyncio.gather(*closes), 2)
                        self.assertTrue(finished.is_set())
                        self.assertIsNone(monitor._task)
                        saved = store.load_shadow_monitor_state(monitor.monitor_id)
                        self.assertEqual("WARMUP" if save_fails else "ERROR", saved["quality"]["status"])
                        self.assertEqual(save_fails, monitor._checkpoint_pending)
                        if save_fails:
                            self.assertTrue(any("failed while draining" in message for message in logs.output))
                finally:
                    release.set()
                    if closes:
                        await asyncio.gather(*closes, return_exceptions=True)
                    await monitor.close()
                    await asyncio.to_thread(finished.wait, 2)
                    store.close()


class RamMarketThemeShadowTests(unittest.IsolatedAsyncioTestCase):
    def monitor(self):
        value = MarketThemeShadowMonitor(theme_id="반도체", leadership_venue="KRX", retry_seconds=0.5)
        self.addAsyncCleanup(value.close)
        return value

    async def test_no_database_calls_and_no_evaluation_without_new_inputs(self):
        monitor = self.monitor()
        with patch("sqlite3.connect", side_effect=AssertionError("DB read")) as sqlite_connect, \
             patch("psycopg.connect", side_effect=AssertionError("DB read")) as pg_connect:
            await monitor.start()
            await asyncio.sleep(0)
            self.assertEqual(0, monitor.status["evaluations"])
            inputs = shadow_inputs()
            self.assertTrue(await monitor.offer_inputs(inputs))
            result = await monitor.run_once()
            for _ in range(100):
                self.assertFalse(await monitor.offer_inputs(inputs))
                self.assertEqual(result, await monitor.run_once())
            self.assertEqual(1, monitor.status["evaluations"])
            self.assertEqual(evaluate_market_theme_shadow(inputs), result)
            self.assertEqual(0, sqlite_connect.call_count)
            self.assertEqual(0, pg_connect.call_count)
            self.assertFalse(monitor.status["durable"])

    async def test_queue_retains_one_snapshot_and_reports_coalesced_cuts(self):
        monitor = self.monitor()
        first = shadow_inputs()
        later = replace(first, decision_time="2026-09-14T09:00:21+09:00")
        await monitor.offer_inputs(first)
        await monitor.offer_inputs(later)
        self.assertEqual(1, monitor.status["pending_snapshots"])
        self.assertEqual(1, monitor.status["coalesced_snapshots"])
        self.assertEqual("latest_snapshot", monitor.status["coverage"])
        result = await monitor.run_once()
        self.assertEqual("2026-09-14T00:00:21+00:00", result["decision_time"])
        view = monitor.last_result
        view["order_authorized"] = True
        self.assertFalse(monitor.last_result["order_authorized"])

    async def test_failed_evaluation_preserves_last_result_and_retries_pending_cut(self):
        monitor = self.monitor()
        await monitor.offer_inputs(shadow_inputs())
        first = await monitor.run_once()
        state = monitor._state
        later = replace(shadow_inputs(), decision_time="2026-09-14T09:00:21+09:00")
        await monitor.offer_inputs(later)
        with patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow",
                   side_effect=ValueError("evaluation failed")):
            with self.assertRaisesRegex(ValueError, "failed"):
                await monitor.run_once()
        self.assertEqual(first, monitor.last_result)
        self.assertEqual(state, monitor._state)
        self.assertEqual(1, monitor.status["pending_snapshots"])
        self.assertEqual("ERROR", monitor.status["state"])
        second = await monitor.run_once()
        self.assertNotEqual(first["revision_id"], second["revision_id"])
        self.assertEqual("", monitor.status["last_error"])

    async def test_close_joins_running_calculation_and_drains_last_accepted_snapshot(self):
        monitor = self.monitor()
        entered, release = threading.Event(), threading.Event()
        calls = []
        def evaluate(inputs, **parameters):
            calls.append(inputs.decision_time)
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("blocked evaluation not released")
            return evaluate_market_theme_shadow(inputs, **parameters)
        with patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow",
                   side_effect=evaluate):
            try:
                await monitor.offer_inputs(shadow_inputs())
                await monitor.start()
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                await monitor.offer_inputs(replace(shadow_inputs(), decision_time="2026-09-14T09:00:21+09:00"))
                closer = asyncio.create_task(monitor.close())
                await asyncio.sleep(0.02)
                self.assertFalse(closer.done())
                with self.assertRaises(RuntimeError):
                    await monitor.offer_inputs(shadow_inputs())
                release.set()
                await closer
            finally:
                release.set()
                await monitor.close()
        self.assertEqual(2, monitor.status["evaluations"])
        self.assertEqual("STOPPED", monitor.status["state"])
        self.assertEqual(0, monitor.status["pending_snapshots"])

    async def test_cancelled_close_still_joins_owned_work(self):
        monitor = self.monitor()
        entered, release = threading.Event(), threading.Event()
        original = monitor._evaluate_ram
        def evaluate(inputs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("blocked evaluation not released")
            original(inputs)
        with patch.object(monitor, "_evaluate_ram", side_effect=evaluate):
            try:
                await monitor.offer_inputs(shadow_inputs())
                await monitor.start()
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                closer = asyncio.create_task(monitor.close())
                await asyncio.sleep(0.02)
                closer.cancel()
                await asyncio.sleep(0.02)
                self.assertFalse(closer.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await closer
            finally:
                release.set()
                await monitor.close()
        self.assertEqual("STOPPED", monitor.status["state"])
        self.assertIsNotNone(monitor.last_result)
        with self.assertRaises(RuntimeError):
            await monitor.start()

    async def test_budget_wrong_scope_and_backwards_cut_are_rejected(self):
        monitor = self.monitor()
        inputs = shadow_inputs()
        await monitor.offer_inputs(inputs)
        for bad in (replace(inputs, leadership_venue="NXT"),
                    replace(inputs, decision_time="2026-09-14T09:00:19+09:00"),
                    replace(inputs, market_frames=inputs.market_frames * 334)):
            with self.subTest(inputs=bad), self.assertRaises(ValueError):
                await monitor.offer_inputs(bad)


class SharedRamShadowCalculationTests(unittest.IsolatedAsyncioTestCase):
    def monitor(self, **kwargs):
        monitor = MarketThemeShadowMonitor(theme_id="반도체", leadership_venue="KRX", **kwargs)
        self.addAsyncCleanup(monitor.close)
        return monitor

    async def test_shared_context_and_four_policies_keep_exact_independent_results(self):
        fixture = thesis_fixtures.StockThesisShadowTests()
        fixture.setUp()
        inputs = fixture.weak_inputs()
        requests = (None, *(fixture.request(policy) for policy in
                           ("PRICE_ONLY", "IMMEDIATE_EXIT", "REDUCE_ON_RISK", "CONFIRM_THEN_EXIT")))
        monitors = tuple(self.monitor(stock_thesis=request, leadership_parameters=thesis_fixtures.PARAMETERS)
                         for request in requests)
        expected = tuple(evaluate_stock_thesis_shadow(inputs, request, leadership_parameters=thesis_fixtures.PARAMETERS)
                         if request else evaluate_market_theme_shadow(inputs, leadership_parameters=thesis_fixtures.PARAMETERS)
                         for request in requests)
        for monitor in monitors:
            await monitor.offer_inputs(inputs)
        with patch.object(shadow_context, "compute_market_regime", wraps=shadow_context.compute_market_regime) as h1, \
             patch.object(shadow_context, "evaluate_theme_leadership", wraps=shadow_context.evaluate_theme_leadership) as h2, \
             patch("sqlite3.connect", side_effect=AssertionError("shared calculation read DB")), \
             patch("psycopg.connect", side_effect=AssertionError("shared calculation read DB")):
            report = await run_market_theme_shadow_monitors_once(monitors)
            self.assertEqual({}, report["errors"])
            self.assertEqual(expected, report["results"])
            self.assertEqual((1, 1), (h1.call_count, h2.call_count))
            for _ in range(3):
                for monitor in monitors:
                    self.assertFalse(await monitor.offer_inputs(inputs))
                self.assertEqual([], (await run_market_theme_shadow_monitors_once(monitors))["evaluated"])
            self.assertEqual((1, 1), (h1.call_count, h2.call_count))
        self.assertEqual(["HOLD", "EXIT", "REDUCE", "WAIT"], [r["action"] for r in report["results"][1:]])
        self.assertEqual([1] * 5, [m.status["evaluations"] for m in monitors])
        self.assertEqual(4, len({r["decision_id"] for r in report["results"][1:]}))
        monitors[1]._result["context"]["order_authorized"] = True
        self.assertFalse(monitors[0].last_result["order_authorized"])
        self.assertFalse(monitors[2].last_result["context"]["order_authorized"])

    async def test_input_cut_refs_parameters_and_previous_state_partition_calculations(self):
        inputs = shadow_inputs()
        initial = evaluate_market_theme_shadow(inputs)
        from kiwoom_monitor.application.theme_leadership import ThemeLeadershipState
        state = ThemeLeadershipState(**initial["next_leadership_state"])
        variants = (
            (inputs, {}), (inputs, {}),
            (replace(inputs, decision_time="2026-09-14T09:00:21+09:00"), {}),
            (replace(inputs, theme_membership=replace(inputs.theme_membership, revision_id="different-ref")), {}),
            (replace(inputs, previous_leadership_state=state), {}),
            (inputs, {"market_parameters": replace(shadow_context.MarketFeatureParameters(), max_gap_seconds=7)}),
            (inputs, {"leadership_parameters": replace(shadow_context.ThemeLeadershipParameters(), price_exit_bps=600)}),
            (replace(inputs, continuity_status="PARTIAL"), {}),
            (replace(inputs, market_venue="NXT"), {}),
            (replace(inputs, market_window="another-window"), {}),
        )
        monitors = tuple(self.monitor(**params) for _, params in variants)
        expected = tuple(evaluate_market_theme_shadow(value, **params) for value, params in variants)
        for monitor, (value, _) in zip(monitors, variants):
            await monitor.offer_inputs(value)
        with patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow_batch",
                   wraps=evaluate_market_theme_shadow_batch) as batches:
            report = await run_market_theme_shadow_monitors_once(monitors)
        self.assertEqual({}, report["errors"])
        self.assertEqual(expected, report["results"])
        self.assertEqual(9, batches.call_count)

    async def test_request_failure_preserves_healthy_slots_and_retries_without_new_offer(self):
        fixture = thesis_fixtures.StockThesisShadowTests()
        fixture.setUp()
        future_thesis = thesis_fixtures.build_entry_thesis(
            run_id=fixture.thesis.run_id, symbol=fixture.thesis.symbol, leadership=fixture.first,
            factor_refs=fixture.thesis.factor_refs, hypothesis_refs=fixture.thesis.hypothesis_refs,
            created_at="2026-09-14T09:00:31+09:00", available_at="2026-09-14T09:00:31+09:00",
        )
        bad_request = replace(fixture.request(), entry_thesis=future_thesis)
        healthy, failing = self.monitor(), self.monitor(stock_thesis=bad_request)
        inputs = fixture.weak_inputs()
        for monitor in (healthy, failing):
            await monitor.offer_inputs(inputs)
        report = await run_market_theme_shadow_monitors_once((healthy, failing))
        self.assertEqual([0], report["evaluated"])
        self.assertIn(1, report["errors"])
        self.assertIsNone(failing.last_result)
        self.assertEqual(1, failing.status["pending_snapshots"])
        failing._stock_thesis = fixture.request()
        recovered = await run_market_theme_shadow_monitors_once((healthy, failing))
        self.assertEqual({}, recovered["errors"])
        self.assertEqual([1], recovered["evaluated"])
        self.assertEqual(evaluate_stock_thesis_shadow(inputs, fixture.request()), failing.last_result)
        self.assertEqual(1, healthy.status["evaluations"])

    async def test_common_failure_preserves_last_state_and_other_scope_keeps_progressing(self):
        same = (self.monitor(), self.monitor())
        different = self.monitor(leadership_parameters=replace(shadow_context.ThemeLeadershipParameters(), price_exit_bps=600))
        monitors = (*same, different)
        for monitor in monitors:
            await monitor.offer_inputs(shadow_inputs())
        await run_market_theme_shadow_monitors_once(monitors)
        before = tuple((m.last_result, m._state) for m in same)
        later = replace(shadow_inputs(), decision_time="2026-09-14T09:00:21+09:00")
        for monitor in monitors:
            await monitor.offer_inputs(later)
        def evaluate(inputs, requests, **params):
            if params["leadership_parameters"].price_exit_bps != 600:
                raise OSError("common calculation failed")
            return evaluate_market_theme_shadow_batch(inputs, requests, **params)
        with patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow_batch",
                   side_effect=evaluate):
            report = await run_market_theme_shadow_monitors_once(monitors)
        self.assertEqual([2], report["evaluated"])
        self.assertEqual({0, 1}, set(report["errors"]))
        self.assertEqual(before, tuple((m.last_result, m._state) for m in same))
        self.assertEqual([1, 1], [m.status["pending_snapshots"] for m in same])
        recovered = await run_market_theme_shadow_monitors_once(monitors)
        self.assertEqual([0, 1], recovered["evaluated"])
        self.assertEqual({}, recovered["errors"])

    async def test_cancelled_shared_call_joins_and_close_drains_latest_pending_cut(self):
        monitors = (self.monitor(), self.monitor())
        entered, release = threading.Event(), threading.Event()
        def evaluate(inputs, requests, **params):
            entered.set()
            if not release.wait(5):
                raise AssertionError("shared calculation was not released")
            return evaluate_market_theme_shadow_batch(inputs, requests, **params)
        for monitor in monitors:
            await monitor.offer_inputs(shadow_inputs())
        with patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow_batch",
                   side_effect=evaluate):
            task = asyncio.create_task(run_market_theme_shadow_monitors_once(monitors))
            closer = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                with self.assertRaises(RuntimeError):
                    await monitors[1].start()
                await monitors[0].offer_inputs(replace(shadow_inputs(), decision_time="2026-09-14T09:00:21+09:00"))
                await monitors[0].offer_inputs(replace(shadow_inputs(), decision_time="2026-09-14T09:00:22+09:00"))
                task.cancel()
                closer = asyncio.create_task(monitors[0].close())
                await asyncio.sleep(0.02)
                task.cancel()
                await asyncio.sleep(0.02)
                self.assertFalse(task.done())
                self.assertFalse(closer.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await closer
            finally:
                release.set()
                await asyncio.gather(task, *(tuple([closer]) if closer else ()), return_exceptions=True)
        self.assertEqual("STOPPED", monitors[0].status["state"])
        self.assertEqual(2, monitors[0].status["evaluations"])
        self.assertEqual("2026-09-14T00:00:22+00:00", monitors[0].last_result["decision_time"])
        self.assertEqual(1, monitors[0].status["coalesced_snapshots"])
        self.assertEqual(1, monitors[1].status["evaluations"])
        self.assertEqual([], (await run_market_theme_shadow_monitors_once((monitors[1],)))["evaluated"])

    async def test_active_closed_duplicate_and_over_budget_monitors_are_rejected(self):
        monitor = self.monitor()
        for bad in ((), (monitor, monitor), tuple(self.monitor() for _ in range(33))):
            with self.assertRaises(ValueError):
                await run_market_theme_shadow_monitors_once(bad)
        await monitor.start()
        with self.assertRaises(RuntimeError):
            await run_market_theme_shadow_monitors_once((monitor,))
        await monitor.close()
        with self.assertRaises(RuntimeError):
            await run_market_theme_shadow_monitors_once((monitor,))

    async def test_concurrent_shared_calls_in_opposite_order_do_not_repeat_calculation(self):
        monitors = (self.monitor(), self.monitor())
        for monitor in monitors:
            await monitor.offer_inputs(shadow_inputs())
        with patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow_batch",
                   wraps=evaluate_market_theme_shadow_batch) as batches:
            reports = await asyncio.gather(run_market_theme_shadow_monitors_once(monitors),
                                           run_market_theme_shadow_monitors_once(monitors[::-1]))
        self.assertEqual(1, batches.call_count)
        self.assertEqual(2, sum(len(r["evaluated"]) for r in reports))
        self.assertEqual([1, 1], [m.status["evaluations"] for m in monitors])
        self.assertEqual(monitors[0].last_result, monitors[1].last_result)
        self.assertEqual([{}, {}], [r["errors"] for r in reports])


class CollectorRamShadowRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, count=2, store=None, *, cleanup=True):
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
        now = [datetime(2026, 9, 14, 9, tzinfo=timezone(timedelta(hours=9)))]
        hub = RealtimeHub()
        collector = CentralRealtimeCollector(lambda: "unused-offline-token", "real", hub, lambda: now[0], store)
        collector._apply_trade_source_approval({("005930", "KRX"), ("000660", "KRX")}, now[0], reset_all=True)
        monitors = tuple(MarketThemeShadowMonitor(theme_id="반도체", leadership_venue="KRX") for _ in range(count))
        runtime = MarketThemeShadowRuntime(hub, collector, tuple((m, shadow_inputs()) for m in monitors)) if count else None
        if runtime and cleanup:
            self.addAsyncCleanup(runtime.close)
        if runtime:
            collector.set_ram_observation_retention(runtime._retention)
        return now, hub, collector, monitors, runtime

    def trades(self, collector, now, seconds=40):
        for second in range(seconds):
            now[0] = now[0].replace(second=second)
            collector._publish_parsed({"trnm": "REAL", "data": [{"type": "0B", "item": code, "values": {
                "10": str(price + second), "15": "2", "13": str(100 + second * 2), "14": str(100+second*(2 if code=="005930" else 1)),
                "20": now[0].strftime("%H%M%S"),
            }} for code, price in (("005930", 1000), ("000660", 900))]})

    async def wait_for(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.005)
        await asyncio.wait_for(wait(), 3)

    async def test_one_canonical_read_conversion_and_calculation_for_eight_consumers(self):
        now, hub, collector, monitors, runtime = self.fixture(8)
        from kiwoom_monitor.central_server import market_theme_shadow_inputs as inputs_module
        with patch.object(collector, "ram_observation_snapshot", wraps=collector.ram_observation_snapshot) as snapshots, \
             patch.object(inputs_module, "market_theme_shadow_inputs_from_ram",
                          wraps=inputs_module.market_theme_shadow_inputs_from_ram) as conversions, \
             patch.object(shadow_context, "compute_market_regime", wraps=shadow_context.compute_market_regime) as h1, \
             patch.object(shadow_context, "evaluate_theme_leadership", wraps=shadow_context.evaluate_theme_leadership) as h2, \
             patch("sqlite3.connect", side_effect=AssertionError("RAM path read DB")), \
             patch("psycopg.connect", side_effect=AssertionError("RAM path read DB")):
            requested = hub.requested_codes()
            await runtime.start()
            self.assertEqual(requested, hub.requested_codes())
            self.trades(collector, now)
            before = (tuple(r["input_ref"] for r in collector._ram_trades), set(collector._minute_bars._dirty))
            await self.wait_for(lambda: all(m.status["evaluations"] == 1 for m in monitors))
            self.assertEqual((1, 1, 1, 1), (snapshots.call_count, conversions.call_count, h1.call_count, h2.call_count))
            self.assertEqual(before, (tuple(r["input_ref"] for r in collector._ram_trades), set(collector._minute_bars._dirty)))
            self.assertEqual(1, len({m.last_result["revision_id"] for m in monitors}))
            self.assertEqual("COMPLETE", monitors[0].last_result["leadership"]["quality"]["status"])
            self.assertTrue(all(point.input_ref.startswith("ram-0B:") for point in monitors[0]._last_offered.observation_points))
            await asyncio.sleep(0.03)
            self.assertEqual(1, snapshots.call_count)
            self.assertFalse(runtime.status["durable"])
            await runtime.close()
            self.assertEqual(1, snapshots.call_count)
            self.assertEqual(0, hub.client_count)
            self.assertEqual("STOPPED", runtime.status["state"])
            self.assertEqual(["STOPPED"] * 8, [m.status["state"] for m in monitors])

    async def test_gap_reset_source_removal_and_queue_drop_never_become_complete(self):
        now, hub, collector, monitors, runtime = self.fixture()
        await runtime.start()
        self.trades(collector, now)
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 1)
        generation = collector.ram_observation_snapshot(("005930", "000660"), seconds=20)["generation"]
        now[0] = now[0].replace(second=40)
        collector._mark_capture_gap()
        hub.publish({"type": "error"})
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 2)
        self.assertNotEqual(generation, collector.ram_observation_snapshot(("005930", "000660"), seconds=20)["generation"])
        self.assertNotEqual("COMPLETE", monitors[0].last_result["leadership"]["quality"]["status"])
        now[0] = now[0].replace(second=41)
        collector._apply_trade_source_approval({("005930", "KRX")}, now[0], reset_all=False)
        hub.publish({"type": "codes_added"})
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 3)
        self.assertNotEqual("COMPLETE", monitors[0]._last_offered.continuity_status)
        now[0] = now[0].replace(second=42)
        for _ in range(1001):
            hub.publish({"type": "trade", "payload": {"do_not_use_this_payload": True}})
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 4)
        self.assertEqual(1, runtime.status["dropped_events"])
        self.assertNotEqual("COMPLETE", monitors[0]._last_offered.continuity_status)

    async def test_failure_retries_owned_pending_without_extra_snapshot_or_database(self):
        now, hub, collector, monitors, runtime = self.fixture()
        calls = 0
        def evaluate(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("one calculation failure")
            return evaluate_market_theme_shadow_batch(*args, **kwargs)
        with patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow_batch", side_effect=evaluate), \
             patch("sqlite3.connect", side_effect=AssertionError("RAM retry read DB")):
            await runtime.start()
            self.trades(collector, now)
            await self.wait_for(lambda: runtime.status["last_error"] != "")
            self.assertEqual([1, 1], [m.status["pending_snapshots"] for m in monitors])
            await self.wait_for(lambda: all(m.status["evaluations"] == 1 for m in monitors))
            self.assertEqual(2, calls)
            self.assertEqual(1, runtime.status["snapshot_reads"])
            self.assertEqual("", runtime.status["last_error"])

    async def test_close_freezes_queued_cut_and_repeated_cancellation_joins_all_work(self):
        now, hub, collector, monitors, runtime = self.fixture()
        entered, release = threading.Event(), threading.Event()
        calls = []
        def evaluate(inputs, requests, **params):
            calls.append(inputs.decision_time)
            if len(calls) == 1:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("runtime calculation not released")
            return evaluate_market_theme_shadow_batch(inputs, requests, **params)
        with patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow_batch", side_effect=evaluate):
            closer = None
            try:
                await runtime.start()
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                self.trades(collector, now, 4)
                closer = asyncio.create_task(runtime.close())
                await asyncio.sleep(0.02)
                closer.cancel()
                await asyncio.sleep(0.02)
                closer.cancel()
                self.assertFalse(closer.done())
                self.assertEqual(0, hub.client_count)
                with self.assertRaises(RuntimeError):
                    await runtime.offer_context(shadow_inputs())
                # This later canonical update must not move the frozen close cut.
                now[0] = now[0].replace(second=4)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await closer
            finally:
                release.set()
                if closer:
                    await asyncio.gather(closer, return_exceptions=True)
        self.assertEqual(2, len(calls))
        self.assertTrue(calls[-1].endswith("09:00:03+09:00"))
        self.assertEqual([2, 2], [m.status["evaluations"] for m in monitors])
        self.assertEqual("STOPPED", runtime.status["state"])

    async def test_snapshot_close_failure_still_disconnects_and_closes_all_consumers(self):
        now, hub, collector, monitors, runtime = self.fixture(cleanup=False)
        await runtime.start()
        # Before the worker can consume the initial queued cut, freeze fails.
        with patch.object(collector, "ram_observation_snapshot", side_effect=ValueError("snapshot budget failed")):
            with self.assertRaisesRegex(ValueError, "budget"):
                await runtime.close()
        self.assertEqual(0, hub.client_count)
        self.assertEqual(["STOPPED", "STOPPED"], [m.status["state"] for m in monitors])
        self.assertEqual("STOPPED", runtime.status["state"])
        # Failed owned close is deliberately observable on repeated close too.
        with self.assertRaisesRegex(ValueError, "budget"):
            await runtime.close()

    async def test_context_update_uses_existing_refs_and_rejects_other_scope(self):
        now, hub, collector, monitors, runtime = self.fixture()
        await runtime.start()
        self.trades(collector, now)
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 1)
        context = replace(shadow_inputs(), theme_membership=replace(shadow_inputs().theme_membership, revision_id="accepted-theme-2"))
        for _ in range(1000):
            hub.publish({"type": "diagnostics"})
        await runtime.offer_context(context)
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 2)
        self.assertEqual("accepted-theme-2", monitors[0]._last_offered.theme_membership.revision_id)
        self.assertTrue(all(p.input_ref.startswith("ram-0B:") for p in monitors[0]._last_offered.observation_points))
        with self.assertRaises(ValueError):
            await runtime.offer_context(replace(context, leadership_venue="NXT"))
        self.assertEqual(2, runtime.status["snapshot_reads"])

    async def test_native_writer_counts_commits_and_payload_do_not_scale_with_consumers(self):
        reports, payloads = [], []
        with tempfile.TemporaryDirectory() as directory:
            for count in (0, 2, 8):
                store = SQLiteQueryStore(Path(directory) / f"collector-{count}.sqlite3")
                store.initialize()
                now, hub, collector, monitors, runtime = self.fixture(count, store)
                try:
                    with patch.object(collector._minute_bars, "add", wraps=collector._minute_bars.add) as minutes, \
                         patch.object(collector, "_observe_ram_trade", wraps=collector._observe_ram_trade) as seconds:
                        if runtime:
                            await runtime.start()
                        self.trades(collector, now)
                        if runtime:
                            await self.wait_for(lambda: all(m.status["evaluations"] == 1 for m in monitors))
                        self.assertEqual((80, 80), (minutes.call_count, seconds.call_count))
                    now[0] = now[0].replace(minute=1, second=2)
                    statements = []
                    connect = sqlite3.connect
                    def traced(*args, **kwargs):
                        connection = connect(*args, **kwargs)
                        connection.set_trace_callback(statements.append)
                        return connection
                    from contextlib import ExitStack
                    with ExitStack() as hooks:
                        connections = hooks.enter_context(patch("sqlite3.connect", side_effect=traced))
                        writers = {name: hooks.enter_context(patch.object(store, name, wraps=getattr(store, name)))
                                   for name in ("save_realtime_snapshots", "save_minute_bars", "finalize_minute_bars", "save_second_trade_bars")}
                        await collector._flush_snapshots()
                        reports.append({"consumers": count, "connections": connections.call_count,
                            "commits": sum(sql.strip().upper() == "COMMIT" for sql in statements),
                            "writer_calls": {name: writer.call_count for name, writer in writers.items()},
                            "minute_adds": minutes.call_count, "second_adds": seconds.call_count,
                            "ram_reads": runtime.status["snapshot_reads"] if runtime else 0})
                    with closing(connect(store._path)) as connection:
                        payloads.append((connection.execute("SELECT * FROM central_minute_bars ORDER BY code,minute").fetchall(),
                                         connection.execute("SELECT * FROM central_second_trade_bars ORDER BY code,trade_second").fetchall()))
                    if runtime:
                        # The canonical clean RAM window survives native write drain.
                        snapshot = tuple(collector._ram_trades)
                        self.assertEqual(42, len(snapshot))
                        await runtime.close()
                finally:
                    if runtime:
                        await runtime.close()
                    store.close()
        self.assertEqual(payloads[0], payloads[1])
        self.assertEqual(payloads[0], payloads[2])
        self.assertEqual([3, 3, 3], [r["commits"] for r in reports])
        self.assertEqual([3, 3, 3], [r["connections"] for r in reports])
        self.assertEqual([0, 1, 1], [r["ram_reads"] for r in reports])
        self.assertTrue(all(value == (0 if name=="save_second_trade_bars" else 1) for r in reports for name,value in r["writer_calls"].items()))
        print("RAM_SHADOW_NATIVE_PROBE=" + json.dumps(reports))

    async def test_actual_app_lifespan_owns_explicit_bindings_and_cleans_up_start_failure(self):
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        from unittest.mock import AsyncMock
        for failed_start in (False, True):
            with self.subTest(failed_start=failed_start), tempfile.TemporaryDirectory() as directory:
                monitors = tuple(MarketThemeShadowMonitor(theme_id="반도체", leadership_venue="KRX") for _ in range(2))
                settings = CentralServerSettings(database_url=f"sqlite:///{Path(directory) / 'app.sqlite3'}",
                    access_token="offline-test-only", kiwoom_app_key="offline-unused", kiwoom_secret_key="offline-unused",
                    autonomous_top20_enabled=False, market_event_collection_enabled=False, news_history_jobs_enabled=False,
                    news_query_set_enabled=False, news_naver_api_enabled=False)
                app = create_app(settings, market_theme_shadow_bindings=tuple((m, shadow_inputs()) for m in monitors))
                collector, runtime = app.state.realtime_collector, app.state.market_theme_shadow_runtime
                now = [datetime(2026, 9, 14, 9, tzinfo=timezone(timedelta(hours=9)))]
                collector._now_provider = lambda: now[0]
                collector._apply_trade_source_approval({("005930", "KRX"), ("000660", "KRX")}, now[0], reset_all=True)
                async def start(instance):
                    self.assertEqual("RUNNING", runtime.status["state"])
                    self.assertEqual(1, app.state.realtime_hub.client_count)
                    if failed_start:
                        raise OSError("upstream start failed")
                with patch.object(CentralRealtimeCollector, "start", start), \
                     patch.object(CentralRealtimeCollector, "close", AsyncMock()) as collector_close, \
                     patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.start", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.close", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.diagnostic_trace.recover_interrupted"), \
                     patch("kiwoom_monitor.central_server.diagnostic_trace.stop"):
                    async def run():
                        async with app.router.lifespan_context(app):
                            self.trades(collector, now)
                            await self.wait_for(lambda: all(m.status["evaluations"] == 1 for m in monitors))
                    if failed_start:
                        with self.assertRaisesRegex(OSError, "start failed"):
                            await run()
                    else:
                        await run()
                    self.assertEqual(1, collector_close.await_count)
                self.assertEqual("STOPPED", runtime.status["state"])
                self.assertEqual(["STOPPED", "STOPPED"], [m.status["state"] for m in monitors])
                self.assertEqual(0, app.state.realtime_hub.client_count)

    async def test_runtime_requires_explicit_bounded_scopes_and_no_individual_worker(self):
        now, hub, collector, monitors, runtime = self.fixture()
        for bindings in ((), ((monitors[0], shadow_inputs()),) * 2,
                         ((monitors[0], replace(shadow_inputs(), theme_membership=None)),)):
            with self.assertRaises(ValueError):
                MarketThemeShadowRuntime(hub, collector, bindings)
        await monitors[0].start()
        with self.assertRaises(RuntimeError):
            await runtime.start()
        self.assertEqual(0, hub.client_count)
        await monitors[0].close()

    async def test_same_collector_cannot_have_two_runtime_owners_or_lose_retention_on_failed_start(self):
        now, hub, collector, monitors, runtime = self.fixture()
        other_monitor = MarketThemeShadowMonitor(theme_id="반도체", leadership_venue="KRX")
        other = MarketThemeShadowRuntime(hub, collector, ((other_monitor, shadow_inputs()),))
        self.addAsyncCleanup(other.close)
        await runtime.start()
        with self.assertRaisesRegex(RuntimeError, "already has an owner"):
            await other.start()
        with self.assertRaisesRegex(RuntimeError, "already has an owner"):
            collector.set_ram_observation_retention(0)
        await other.close()
        self.assertEqual(1, hub.client_count)
        self.assertEqual(20, collector._ram_trade_retention_seconds)
        self.trades(collector, now)
        await self.wait_for(lambda: all(m.status["evaluations"] == 1 for m in monitors))
        await runtime.close()
        self.assertIsNone(collector._ram_observation_owner)
        self.assertEqual(0, collector._ram_trade_retention_seconds)


class OwnerContextShadowTests(unittest.IsolatedAsyncioTestCase):
    fixture = CollectorRamShadowRuntimeTests.fixture
    wait_for = CollectorRamShadowRuntimeTests.wait_for
    trades = CollectorRamShadowRuntimeTests.trades

    def theme(self, at, ref="native-theme-1", codes=("005930", "000660")):
        return {"snapshot_id": ref, "available_at": at.timestamp(), "accepted_sequence": 1,
            "document": {"active_profile": "default", "profiles": [{"name": "default", "stock_themes": [
                {"code": code, "theme": "반도체"} for code in codes]}]}}

    def ranking(self, at):
        codes = ("005930", "000660", *(f"{100000 + i:06d}" for i in range(3, 21)))
        return {"subject": at.date().isoformat(), "snapshot_key": at.replace(tzinfo=None).isoformat(),
            "saved_at": at.timestamp(), "persistence_state": "pending", "payload": {
                "codes": list(codes), "items": [{"stk_cd": code, "stk_nm": code, "trde_prica": "100", "flu_rt": "2.5"}
                                                for code in codes]}}

    def source_fixture(self, count=8, theme_source=None):
        now, hub, collector, monitors, _ = self.fixture(count, cleanup=False)
        source = {"theme": [self.theme(now[0].replace(second=19))], "ranking": self.ranking(now[0].replace(second=39))}
        ranking = Mock(side_effect=lambda: source["ranking"])
        themes = Mock(side_effect=lambda cutoff: source["theme"])
        runtime = MarketThemeShadowRuntime(hub, collector, tuple((m, replace(shadow_inputs(), market_venue="COMBINED")) for m in monitors),
                                          ranking_source=ranking, theme_source=theme_source or themes)
        self.addAsyncCleanup(runtime.close)
        if runtime:
            collector.set_ram_observation_retention(runtime._retention)
        return now, hub, collector, monitors, runtime, source, ranking, themes

    async def test_eight_monitors_share_owner_reads_and_unchanged_receipt_does_not_recalculate(self):
        from kiwoom_monitor.central_server import market_theme_shadow_inputs as adapters
        now, hub, collector, monitors, runtime, source, ranking, themes = self.source_fixture()
        with patch.object(adapters, "market_frame_from_live_membership", wraps=adapters.market_frame_from_live_membership) as frames:
            await runtime.start()
            self.trades(collector, now)
            await self.wait_for(lambda: all(m.status["evaluations"] == 1 for m in monitors))
            self.assertEqual((1, 1, 1), (ranking.call_count, themes.call_count, frames.call_count))
            self.assertTrue(monitors[0]._last_offered.market_frames[-1].revision_id.startswith("ram:top20:"))
            self.assertEqual("native-theme-1", monitors[0]._last_offered.theme_membership.revision_id)
            self.assertEqual("native-theme-1", monitors[0]._last_offered.market_frames[-1].theme_revision_id)
            source["ranking"]["persistence_state"] = "persisted"
            source["ranking"]["saved_at"] += 1
            now[0] = now[0].replace(second=40)
            runtime.context_changed("ranking")
            await self.wait_for(lambda: ranking.call_count == 2)
            await asyncio.sleep(0.02)
            self.assertEqual(1, frames.call_count)
            self.assertEqual([1] * 8, [m.status["evaluations"] for m in monitors])
            self.assertEqual(1, runtime.status["snapshot_reads"])
            source["theme"] = [self.theme(now[0], "native-theme-2", codes=())]
            runtime.context_changed("theme")
            await self.wait_for(lambda: all(m.status["evaluations"] == 2 for m in monitors))
            self.assertEqual(2, themes.call_count)
            self.assertTrue(all(m._last_offered.theme_membership is None for m in monitors))
            self.assertNotEqual("COMPLETE", monitors[0].last_result["leadership"]["quality"]["status"])
            self.assertEqual(((), ()), hub.requested_codes())
        print("OWNER_CONTEXT_PROBE=" + json.dumps({"consumers": 8, "initial_ranking_ram_reads": 1,
            "initial_native_theme_reads": 1, "initial_frame_conversions": 1,
            "unchanged_receipt_extra_evaluations": 0, "theme_mutation_native_reads": 1}))

    async def test_theme_revision_does_not_relabel_past_ranking_frames(self):
        now, hub, collector, monitors, runtime, source, ranking, themes = self.source_fixture(2)
        source["ranking"] = self.ranking(now[0].replace(second=20))
        await runtime.start()
        self.trades(collector, now)
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 1)
        old = monitors[0]._last_offered.market_frames[-1]
        source["theme"] = [self.theme(now[0].replace(second=30), "native-theme-2")]
        now[0] = now[0].replace(second=40)
        runtime.context_changed("theme")
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 2)
        self.assertEqual(old, monitors[0]._last_offered.market_frames[-1])
        source["ranking"] = self.ranking(now[0].replace(second=25))
        runtime.context_changed("ranking")
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 3)
        self.assertEqual("native-theme-1", monitors[0]._last_offered.market_frames[-1].theme_revision_id)
        source["ranking"] = self.ranking(now[0].replace(second=40))
        runtime.context_changed("ranking")
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 4)
        self.assertEqual("native-theme-2", monitors[0]._last_offered.market_frames[-1].theme_revision_id)

    async def test_failed_theme_read_retries_once_for_all_consumers_and_keeps_state(self):
        now, hub, collector, monitors, runtime, source, ranking, themes = self.source_fixture()
        await runtime.start()
        self.trades(collector, now)
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 1)
        before = tuple((m.last_result, m._state) for m in monitors)
        next_theme = self.theme(now[0], "native-theme-2")
        themes.side_effect = [OSError("native theme read failed"), [next_theme]]
        runtime.context_changed("theme")
        await self.wait_for(lambda: "native theme read failed" in runtime.status["last_error"])
        self.assertEqual(before, tuple((m.last_result, m._state) for m in monitors))
        self.assertTrue(runtime.status["context_pending"])
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 2)
        self.assertEqual(3, themes.call_count)
        self.assertEqual(2, runtime.status["snapshot_reads"])

    async def test_close_joins_native_context_and_freezes_new_members_before_delivery(self):
        entered, release = threading.Event(), threading.Event()
        now, hub, collector, monitors, _, = self.fixture(cleanup=False)
        snapshot = self.theme(now[0].replace(second=39), "native-theme-new", ("005930", "100003"))
        calls = []
        def read(cutoff):
            calls.append(cutoff)
            if len(calls) == 2:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("native context read not released")
            return [self.theme(now[0].replace(second=19))] if len(calls) == 1 else [snapshot]
        collector._apply_trade_source_approval({("005930", "KRX"), ("000660", "KRX"), ("100003", "KRX")}, now[0], reset_all=False)
        runtime = MarketThemeShadowRuntime(hub, collector, tuple((m, shadow_inputs()) for m in monitors), theme_source=read)
        self.addAsyncCleanup(runtime.close)
        await runtime.start()
        self.trades(collector, now)
        collector._publish_parsed({"trnm": "REAL", "data": [{"type": "0B", "item": "100003", "values": {
            "10": "1000", "15": "2", "13": "100", "20": "090039"}}]})
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 1)
        runtime.context_changed("theme")
        closer = None
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            closer = asyncio.create_task(runtime.close())
            await asyncio.sleep(0.02)
            closer.cancel()
            self.assertFalse(closer.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await closer
        finally:
            release.set()
            if closer:
                await asyncio.gather(closer, return_exceptions=True)
        self.assertEqual(3, len(calls))
        self.assertEqual(now[0].timestamp(), calls[-1])
        self.assertTrue(any(p.code == "100003" for p in monitors[0]._last_offered.observation_points))
        self.assertEqual("native-theme-new", monitors[0]._last_offered.theme_membership.revision_id)
        self.assertEqual(0, hub.client_count)

    async def test_future_projection_is_deferred_without_rereading_until_the_cut_catches_up(self):
        now, hub, collector, monitors, runtime, source, ranking, themes = self.source_fixture(2)
        await runtime.start()
        self.trades(collector, now)
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 1)
        old = monitors[0]._last_offered.market_frames[-1]
        source["ranking"] = self.ranking(now[0].replace(second=42))
        runtime.context_changed("ranking")
        await self.wait_for(lambda: ranking.call_count == 2)
        self.assertEqual(old, monitors[0]._last_offered.market_frames[-1])
        self.assertTrue(runtime.status["context_pending"])
        now[0] = now[0].replace(second=43)
        hub.publish({"type": "trade", "payload": {}})
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 2)
        self.assertEqual(2, ranking.call_count)
        self.assertNotEqual(old.revision_id, monitors[0]._last_offered.market_frames[-1].revision_id)
        self.assertFalse(runtime.status["context_pending"])

    async def test_actual_app_theme_ack_failed_write_and_cancelled_commit_notification(self):
        import httpx
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from kiwoom_monitor.domain.research_contract import THEME_BACKUP_FORMAT, THEME_BACKUP_VERSION
        for mode in ("success", "failure", "cancelled", "lost_ack"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                store = SQLiteQueryStore(Path(directory) / "content.sqlite3")
                monitors = tuple(MarketThemeShadowMonitor(theme_id="반도체", leadership_venue="KRX") for _ in range(2))
                settings = CentralServerSettings(database_url="sqlite:///:memory:", access_token="offline-token",
                    kiwoom_app_key="offline-unused", kiwoom_secret_key="offline-unused", autonomous_top20_enabled=False,
                    market_event_collection_enabled=False, news_history_jobs_enabled=False,
                    news_query_set_enabled=False, news_naver_api_enabled=False)
                with patch("kiwoom_monitor.central_server.app.create_query_store", return_value=store):
                    app = create_app(settings, market_theme_shadow_bindings=tuple((m, shadow_inputs()) for m in monitors))
                collector, runtime = app.state.realtime_collector, app.state.market_theme_shadow_runtime
                now = [datetime(2026, 9, 14, 9, tzinfo=timezone(timedelta(hours=9)))]
                collector._now_provider = lambda: now[0]
                collector._apply_trade_source_approval({("005930", "KRX"), ("000660", "KRX")}, now[0], reset_all=True)
                document = {"format": THEME_BACKUP_FORMAT, "version": THEME_BACKUP_VERSION,
                            **self.theme(now[0])["document"]}
                value = {"owner": "default", "key": "full", "document": document}
                with patch("kiwoom_monitor.central_server.database_documents.time", return_value=now[0].replace(second=19).timestamp()):
                    store.upsert_documents("theme_metadata", [value])
                entered, release = threading.Event(), threading.Event()
                original = store.replace_documents
                request = None
                try:
                    await runtime.start()
                    self.trades(collector, now)
                    await self.wait_for(lambda: monitors[0].status["evaluations"] == 1)
                    before = monitors[0]._last_offered.theme_membership.revision_id
                    changed = {**document, "profiles": [{"name": "default", "stock_themes": []}]}
                    def write(*args):
                        if mode == "failure":
                            raise OSError("theme commit failed")
                        if mode == "cancelled":
                            entered.set()
                            if not release.wait(5):
                                raise AssertionError("theme write not released")
                        original(*args)
                        if mode == "lost_ack":
                            raise OSError("theme commit ACK lost")
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://offline") as client:
                        self.assertEqual(401, (await client.put("/api/v1/content/theme_metadata", json={"documents": [value]})).status_code)
                        with patch.object(store, "replace_documents", side_effect=write), \
                             patch("kiwoom_monitor.central_server.database_documents.time", return_value=now[0].timestamp()):
                            request = asyncio.create_task(client.put("/api/v1/content/theme_metadata",
                                headers={"Authorization": "Bearer offline-token"}, json={"documents": [{**value, "document": changed}]}))
                            if mode == "cancelled":
                                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                                request.cancel()
                                await asyncio.sleep(0.02)
                                self.assertFalse(request.done())
                                release.set()
                                with self.assertRaises(asyncio.CancelledError):
                                    await request
                            else:
                                response = await request
                                self.assertEqual(500 if mode in {"failure", "lost_ack"} else 200, response.status_code)
                                if mode == "success":
                                    self.assertEqual({"collection": "theme_metadata", "saved": 1}, response.json())
                        if mode == "failure":
                            await asyncio.sleep(0.03)
                            self.assertEqual(2, runtime.status["theme_context_reads"])
                            self.assertEqual(before, monitors[0]._last_offered.theme_membership.revision_id)
                        else:
                            await self.wait_for(lambda: monitors[0].status["evaluations"] == 2)
                            self.assertEqual(2, runtime.status["theme_context_reads"])
                            self.assertIsNone(monitors[0]._last_offered.theme_membership)
                finally:
                    release.set()
                    if request:
                        await asyncio.gather(request, return_exceptions=True)
                    await runtime.close()
                    store.close()


class RamShadowSourceCoverageTests(unittest.IsolatedAsyncioTestCase):
    fixture = CollectorRamShadowRuntimeTests.fixture
    trades = CollectorRamShadowRuntimeTests.trades
    wait_for = CollectorRamShadowRuntimeTests.wait_for

    def coverage(self, page, binding=0):
        slot = next(row for row in page["results"] if row["binding"] == binding)
        provenance = page["input_provenance"][slot["context_ref"]]
        return page["source_coverage"][provenance["coverage_ref"]], provenance

    async def test_eight_consumers_share_one_copied_receipt_without_extra_snapshot_or_database(self):
        now, hub, collector, monitors, runtime = self.fixture(8)
        with patch.object(collector, "ram_observation_snapshot", wraps=collector.ram_observation_snapshot) as reads, \
             patch("sqlite3.connect", side_effect=AssertionError("source receipt opened DB")), \
             patch("psycopg.connect", side_effect=AssertionError("source receipt opened PG")):
            await runtime.start()
            self.trades(collector, now)
            await self.wait_for(lambda: all(m.status["evaluations"] == 1 for m in monitors))
            page = runtime.latest_results
            self.assertEqual((1, 1, 1), (reads.call_count, len(page["source_coverage"]), len(page["input_provenance"])))
            source, provenance = self.coverage(page)
            self.assertEqual(collector._ram_source_id, source["source_id"])
            self.assertEqual("COMPLETE", provenance["continuity_status"])
            self.assertEqual(10, provenance["window_seconds"])
            self.assertEqual(["000660", "005930"], [v["code"] for v in source["sources"]])
            self.assertTrue(all(v["approved"] for v in source["sources"]))
            self.assertTrue(all(v["continuous_from"] for v in source["sources"]))
            self.assertFalse(source["raw_values_recorded"])
            self.assertEqual("unverified", source["capture_lineage"])
            self.assertFalse(source["replay_eligible"])
            self.assertFalse(source["order_authorized"])
            self.assertEqual("not_verified", page["source_durability"])
            source["sources"][0]["approved"] = False
            provenance["member_codes"].clear()
            fresh = runtime.latest_results
            self.assertTrue(self.coverage(fresh)[0]["sources"][0]["approved"])
            self.assertEqual(2, len(self.coverage(fresh)[1]["member_codes"]))
            self.assertEqual(1, reads.call_count)
            await runtime.close()

    async def test_collector_gap_delivery_gap_and_removed_source_stay_distinct_and_partial(self):
        now, hub, collector, monitors, runtime = self.fixture(1)
        await runtime.start(); self.trades(collector, now)
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 1)
        now[0] = now[0].replace(second=40)
        collector._mark_capture_gap()
        await runtime._dispatch(runtime._snapshot())
        source, provenance = self.coverage(runtime.latest_results)
        self.assertEqual(now[0].isoformat(), source["collector_gap_at"])
        self.assertIsNone(source["delivery_gap_at"])
        self.assertEqual("UNAVAILABLE", provenance["continuity_status"])
        now[0] = now[0].replace(second=41)
        runtime._subscriber.dropped_events += 1
        await runtime._dispatch(runtime._snapshot())
        source, provenance = self.coverage(runtime.latest_results)
        self.assertEqual(now[0].isoformat(), source["delivery_gap_at"])
        self.assertEqual(now[0].isoformat(), source["effective_gap_at"])
        self.assertNotEqual(source["collector_gap_at"], source["delivery_gap_at"])
        self.assertEqual(1, source["dropped_events"])
        self.assertIn("delivery-1", str(source["source_generation"]))
        self.assertNotEqual("COMPLETE", provenance["continuity_status"])
        now[0] = now[0].replace(second=42)
        collector._apply_trade_source_approval({("005930", "KRX")}, now[0], reset_all=False)
        await runtime._dispatch(runtime._snapshot())
        source, provenance = self.coverage(runtime.latest_results)
        removed = next(row for row in source["sources"] if row["code"] == "000660")
        self.assertFalse(removed["approved"])
        self.assertIsNone(removed["continuous_from"])
        self.assertNotEqual("COMPLETE", provenance["continuity_status"])

    async def test_failed_slot_keeps_older_source_cut_and_prunes_receipts_after_recovery(self):
        now, hub, collector, monitors, runtime = self.fixture(2)
        await runtime.start(); self.trades(collector, now)
        await self.wait_for(lambda: all(m.status["evaluations"] == 1 for m in monitors))
        old_source, _ = self.coverage(runtime.latest_results, 0)
        now[0] = now[0].replace(second=40)
        original = evaluate_market_theme_shadow_batch
        def fail_one(*args, **kwargs):
            results = list(original(*args, **kwargs))
            results[0] = ValueError("injected source consumer failure")
            return tuple(results)
        with patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow_batch", side_effect=fail_one):
            report = await runtime._dispatch(runtime._snapshot())
        self.assertEqual([1], report["evaluated"])
        page = runtime.latest_results
        self.assertEqual(2, len(page["source_coverage"]))
        self.assertEqual(old_source, self.coverage(page, 0)[0])
        self.assertNotEqual(old_source["coverage_ref"], self.coverage(page, 1)[0]["coverage_ref"])
        self.assertEqual(now[0].isoformat(), self.coverage(page, 1)[0]["decision_time"])
        now[0] = now[0].replace(second=41)
        await runtime._dispatch(runtime._snapshot())
        page = runtime.latest_results
        self.assertEqual(1, len(page["source_coverage"]))
        self.assertEqual(2, len(runtime._source_provenance))
        self.assertNotIn(old_source["coverage_ref"], page["source_coverage"])

    async def test_later_delivery_state_cannot_relabel_an_already_captured_input_cut(self):
        now, hub, collector, monitors, runtime = self.fixture(1)
        await runtime.start(); self.trades(collector, now)
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 1)
        now[0] = now[0].replace(second=40)
        snapshot = runtime._snapshot()
        now[0] = now[0].replace(second=41)
        runtime._consumer_gap_at = now[0]
        runtime._dropped = runtime._subscriber.dropped_events = 3
        await runtime._dispatch(snapshot)
        source, provenance = self.coverage(runtime.latest_results)
        self.assertEqual(snapshot["decision_time"], source["decision_time"])
        self.assertIsNone(source["delivery_gap_at"])
        self.assertEqual(0, source["dropped_events"])
        self.assertNotIn("delivery-3", str(source["source_generation"]))

    async def test_cancelled_calculation_drains_and_binds_published_results_to_their_actual_cut(self):
        now, hub, collector, monitors, runtime = self.fixture(2)
        await runtime.start(); self.trades(collector, now)
        await self.wait_for(lambda: all(m.status["evaluations"] == 1 for m in monitors))
        now[0] = now[0].replace(second=40)
        entered, release = threading.Event(), threading.Event()
        original = evaluate_market_theme_shadow_batch
        def slow(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("source calculation not released")
            return original(*args, **kwargs)
        task = None
        with patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow_batch", side_effect=slow):
            try:
                task = asyncio.create_task(runtime._dispatch(runtime._snapshot()))
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel(); await asyncio.sleep(0.01); task.cancel()
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                page = runtime.latest_results
                self.assertEqual(1, len(page["source_coverage"]))
                self.assertEqual(now[0].isoformat(), self.coverage(page)[0]["decision_time"])
                self.assertEqual(2, monitors[0].status["evaluations"])
            finally:
                release.set()
                if task:
                    await asyncio.gather(task, return_exceptions=True)

    async def test_selected_record_budget_omits_optional_coverage_preserving_existing_decision(self):
        import json
        from unittest.mock import Mock, PropertyMock
        now, hub, collector, monitors, runtime = self.fixture(1)
        await runtime.start(); self.trades(collector, now)
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 1)
        snapshot = runtime.latest_results
        snapshot.update(coverage="selected_latest_transition", selection="first_result_or_categorical_state_change",
                        selected_at=max(c["decision_time"] for c in snapshot["contexts"].values()), diagnostic_padding="")
        legacy = deepcopy(snapshot)
        for key in ("source_coverage", "input_provenance", "market_observed_changes"):
            legacy.pop(key, None)
        for slot in legacy["results"]:
            slot.pop("market_observed_changes_ref", None)
        snapshot["diagnostic_padding"] = "x" * (1024 * 1024 - 10 - len(json.dumps(legacy, ensure_ascii=False).encode("utf-8")))
        store = Mock()
        runtime._record_store = store
        with patch.object(MarketThemeShadowRuntime, "latest_results", new_callable=PropertyMock, return_value=snapshot):
            runtime._select_record()
        selected = runtime._record_pending[1]
        self.assertNotIn("source_coverage", selected)
        self.assertNotIn("input_provenance", selected)
        self.assertEqual(monitors[0].last_result["revision_id"], next(iter(selected["contexts"])))
        self.assertLessEqual(len(json.dumps(selected, ensure_ascii=False).encode("utf-8")), 1024 * 1024)
        await runtime.close()
        store.upsert_documents.assert_called_once()


class RamShadowCaptureAnchorTests(unittest.IsolatedAsyncioTestCase):
    fixture = CollectorRamShadowRuntimeTests.fixture
    trades = CollectorRamShadowRuntimeTests.trades
    wait_for = CollectorRamShadowRuntimeTests.wait_for

    async def test_anchor_requires_active_matching_epoch_and_never_exposes_replay_or_stale_ids(self):
        now, hub, collector, _, _ = self.fixture(0)
        path = "kiwoom_monitor.central_server.diagnostic_trace.input_token"
        with patch(path, return_value="trace-a"):
            self.assertIsNone(collector.ram_observation_snapshot(("005930",), seconds=20)["capture_anchor"])
            collector._recorded_capture_epoch = "trace-a"
            collector._recorded_input_high_water = ("trace-a", "accepted-a")
            anchor = collector.ram_observation_snapshot(("005930",), seconds=20)["capture_anchor"]
            self.assertEqual(("trace-a", "accepted-a", False), (anchor["trace_id"], anchor["input_id"], anchor["verified"]))
            self.assertEqual("last_accepted_collector_input", anchor["role"])
            collector._input_replay_only = True
            self.assertIsNone(collector.ram_observation_snapshot(("005930",), seconds=20)["capture_anchor"])
            collector._input_replay_only = False
            for high_water in (("trace-b", "wrong-session"), ("trace-a", ""), None, ("trace-a", 7)):
                collector._recorded_input_high_water = high_water
                self.assertIsNone(collector.ram_observation_snapshot(("005930",), seconds=20)["capture_anchor"])
            collector._recorded_input_high_water = ("trace-a", "accepted-a")
            collector._recorded_capture_epoch = "trace-b"
            self.assertIsNone(collector.ram_observation_snapshot(("005930",), seconds=20)["capture_anchor"])
        collector._recorded_capture_epoch = "trace-a"
        with patch(path, return_value=None):
            self.assertIsNone(collector.ram_observation_snapshot(("005930",), seconds=20)["capture_anchor"])

    async def test_rejected_capture_does_not_invent_new_anchor_or_verification(self):
        now, hub, collector, _, _ = self.fixture(0)
        collector._recorded_capture_epoch = "trace-a"
        with patch("kiwoom_monitor.central_server.diagnostic_trace.input_token", return_value="trace-a"), \
             patch("kiwoom_monitor.central_server.diagnostic_trace.emit_payload", return_value=True):
            accepted = collector._record_upstream("capture_gap", {"clear_continuous": False})
        with patch("kiwoom_monitor.central_server.diagnostic_trace.input_token", return_value="trace-a"), \
             patch("kiwoom_monitor.central_server.diagnostic_trace.emit_payload", return_value=False):
            self.assertIsNone(collector._record_upstream("capture_gap", {"clear_continuous": False}))
            anchor = collector.ram_observation_snapshot(("005930",), seconds=20)["capture_anchor"]
        self.assertEqual(accepted, anchor["input_id"])
        self.assertFalse(anchor["verified"])

    async def test_snapshot_anchor_is_frozen_before_trace_replacement_and_returned_as_copy(self):
        now, hub, collector, monitors, runtime = self.fixture(1)
        await runtime.start(); self.trades(collector, now)
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 1)
        now[0] = now[0].replace(second=40)
        collector._recorded_capture_epoch = "trace-a"
        collector._recorded_input_high_water = ("trace-a", "accepted-a")
        with patch("kiwoom_monitor.central_server.diagnostic_trace.input_token", return_value="trace-a"):
            snapshot = runtime._snapshot()
        collector._recorded_capture_epoch = "trace-b"
        collector._recorded_input_high_water = ("trace-b", "accepted-b")
        with patch("kiwoom_monitor.central_server.diagnostic_trace.input_token", return_value="trace-b"):
            await runtime._dispatch(snapshot)
        snapshot["capture_anchor"]["input_id"] = "caller-mutation"
        page = runtime.latest_results
        source = next(iter(page["source_coverage"].values()))
        self.assertEqual("accepted-a", source["capture_anchor"]["input_id"])
        self.assertEqual("trace-a", source["capture_anchor"]["trace_id"])
        self.assertEqual("unverified", source["capture_lineage"])
        source["capture_anchor"]["input_id"] = "API-mutation"
        self.assertEqual("accepted-a", next(iter(runtime.latest_results["source_coverage"].values()))["capture_anchor"]["input_id"])

    async def test_eight_consumers_read_one_existing_token_without_new_capture_or_database(self):
        now, hub, collector, monitors, runtime = self.fixture(8)
        with patch("kiwoom_monitor.central_server.diagnostic_trace.input_token", return_value=None) as token, \
             patch("kiwoom_monitor.central_server.diagnostic_trace.emit_payload", side_effect=AssertionError("snapshot recorded new payload")), \
             patch("sqlite3.connect", side_effect=AssertionError("anchor DB read")), \
             patch("psycopg.connect", side_effect=AssertionError("anchor PG read")):
            await runtime.start()
            self.trades(collector, now)
            before = token.call_count
            await self.wait_for(lambda: all(m.status["evaluations"] == 1 for m in monitors))
            self.assertEqual(1, token.call_count - before)
            page = runtime.latest_results
            self.assertEqual(1, len(page["source_coverage"]))
            self.assertIsNone(next(iter(page["source_coverage"].values()))["capture_anchor"])
            self.assertFalse(page["replay_eligible"])
            await runtime.close()

    async def test_real_isolated_recorder_anchor_reaches_selected_row_without_extra_payloads(self):
        from tests.unit.test_recorded_workload_capture import capture
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        from kiwoom_monitor.central_server.diagnostic_replay_contract import thaw_payload
        helper = SelectedRamShadowRecordingTests()
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "anchor.sqlite3")
            store.initialize()
            now, hub, collector, monitors, runtime = helper.recorded_runtime(store)
            try:
                with capture(store_inputs=False) as session:
                    await runtime.start(); self.trades(collector, now)
                    await self.wait_for(lambda: runtime.status["recording"]["writes"] == 1)
                    source = next(iter(runtime.latest_results["source_coverage"].values()))
                    anchor = source["capture_anchor"]
                    self.assertEqual(collector._recorded_input_high_water, (anchor["trace_id"], anchor["input_id"]))
                    self.assertEqual(session["trace_id"], anchor["trace_id"])
                    accepted = trace.status()["payload_accepted"]
                    for _ in range(3):
                        collector.ram_observation_snapshot(("005930", "000660"), seconds=20)
                    self.assertEqual(accepted, trace.status()["payload_accepted"])
                    self.assertFalse(anchor["verified"])
                    trace.stop()
                    self.assertIsNone(collector.ram_observation_snapshot(("005930",), seconds=20)["capture_anchor"])
                    manifest, rows = trace.recorded_events(session["trace_id"])
                    actual = next(row for row in rows if row.get("input_id") == anchor["input_id"])
                    self.assertEqual(anchor["producer_component"], actual["producer_component"])
                    self.assertEqual("message", actual["input_kind"])
                    self.assertEqual(now[0], thaw_payload(actual["payload"])["source_time"])
                    self.assertEqual("complete", manifest["state"])
                    saved = store.load_document("ram_shadow_diagnostics", "market_theme", "latest")["document"]
                    self.assertEqual(anchor, next(iter(saved["source_coverage"].values()))["capture_anchor"])
                    self.assertFalse(saved["replay_eligible"])
                    self.assertEqual(1, runtime.status["recording"]["writes"])
            finally:
                await runtime.close()
                store.close()


class SelectedRamShadowRecordingTests(unittest.IsolatedAsyncioTestCase):
    fixture = CollectorRamShadowRuntimeTests.fixture
    wait_for = CollectorRamShadowRuntimeTests.wait_for
    trades = CollectorRamShadowRuntimeTests.trades

    def recorded_runtime(self, store, count=8, interval=30):
        now, hub, collector, monitors, _ = self.fixture(count, cleanup=False)
        runtime = MarketThemeShadowRuntime(hub, collector, tuple((m, shadow_inputs()) for m in monitors),
            record_store=store, record_interval_seconds=interval)
        if runtime:
            collector.set_ram_observation_retention(runtime._retention)
        return now, hub, collector, monitors, runtime

    async def test_eight_consumers_one_context_one_commit_and_unchanged_state_no_write(self):
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import SHADOW_RESULT_COLLECTION, SHADOW_RESULT_OWNER
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "selected.sqlite3")
            store.initialize()
            now, hub, collector, monitors, runtime = self.recorded_runtime(store)
            sql = []
            connect = store._connect
            def traced():
                connection = connect()
                connection.set_trace_callback(sql.append)
                return connection
            with patch.object(store, "_connect", side_effect=traced), \
                 patch.object(store, "upsert_documents", wraps=store.upsert_documents) as writes:
                try:
                    await runtime.start()
                    self.trades(collector, now)
                    await self.wait_for(lambda: runtime.status["recording"]["writes"] == 1)
                    self.assertEqual([1] * 8, [m.status["evaluations"] for m in monitors])
                    self.assertEqual(1, writes.call_count)
                    self.assertEqual(1, sum(statement == "COMMIT" for statement in sql))
                    selected = writes.call_args.args[1][0]["document"]
                    self.assertEqual((1, 8), (len(selected["contexts"]), len(selected["results"])))
                    self.assertEqual((1, 1), (len(selected["source_coverage"]), len(selected["input_provenance"])))
                    selected_source = next(iter(selected["source_coverage"].values()))
                    self.assertEqual("ram-source-coverage/v1", selected_source["version"])
                    self.assertEqual("unverified", selected_source["capture_lineage"])
                    first_ref = monitors[0].last_result["revision_id"]
                    now[0] = now[0].replace(second=40)
                    hub.publish({"type": "trade"})
                    await self.wait_for(lambda: monitors[0].status["evaluations"] == 2)
                    self.assertNotEqual(first_ref, monitors[0].last_result["revision_id"])
                    self.assertEqual(1, writes.call_count)
                    current_source = next(iter(runtime.latest_results["source_coverage"].values()))
                    self.assertNotEqual(selected_source["decision_time"], current_source["decision_time"])
                    self.assertNotEqual(selected_source["coverage_ref"], current_source["coverage_ref"])
                    self.assertFalse(runtime.status["recording"]["pending"])
                    self.assertFalse(selected["replay_eligible"])
                    self.assertEqual("not_verified", selected["source_durability"])
                    now[0] = now[0].replace(second=41)
                    collector._mark_capture_gap()
                    hub.publish({"type": "error"})
                    await self.wait_for(lambda: monitors[0].status["evaluations"] == 3)
                    self.assertTrue(runtime.status["recording"]["pending"])
                    self.assertEqual(1, writes.call_count)  # interval bounds normal writes
                    await runtime.close()  # final selected transition bypasses delay
                    self.assertEqual(2, writes.call_count)
                    self.assertEqual(2, sum(statement == "COMMIT" for statement in sql))
                    document = store.load_document(SHADOW_RESULT_COLLECTION, SHADOW_RESULT_OWNER, "latest")["document"]
                    self.assertEqual("PARTIAL", next(iter(document["contexts"].values()))["leadership"]["quality"]["status"])
                    saved_source = next(iter(document["source_coverage"].values()))
                    self.assertEqual(now[0].isoformat(), saved_source["collector_gap_at"])
                    self.assertFalse(saved_source["replay_eligible"])
                    self.assertEqual(1, len(store.load_documents(SHADOW_RESULT_COLLECTION)))
                finally:
                    await runtime.close()
                    store.close()

    async def test_slow_writer_does_not_block_judgment_and_cancelled_close_drains_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "blocked.sqlite3")
            store.initialize()
            now, hub, collector, monitors, runtime = self.recorded_runtime(store)
            entered, release = threading.Event(), threading.Event()
            original = store.upsert_documents
            calls = []
            def write(*args):
                calls.append(args)
                if len(calls) == 1:
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("record writer not released")
                original(*args)
            closer = None
            with patch.object(store, "upsert_documents", side_effect=write):
                try:
                    await runtime.start()
                    self.trades(collector, now)
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    now[0] = now[0].replace(second=40)
                    collector._mark_capture_gap()
                    hub.publish({"type": "error"})
                    await self.wait_for(lambda: monitors[0].status["evaluations"] == 2)
                    self.assertEqual([2] * 8, [m.status["evaluations"] for m in monitors])
                    self.assertEqual(1, runtime.status["recording"]["coalesced"])
                    closer = asyncio.create_task(runtime.close())
                    await asyncio.sleep(0.02)
                    closer.cancel()
                    await asyncio.sleep(0.02)
                    closer.cancel()
                    self.assertFalse(closer.done())
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await closer
                    self.assertEqual(2, len(calls))
                    self.assertEqual(2, runtime.status["recording"]["writes"])
                    self.assertFalse(runtime.status["recording"]["pending"])
                    self.assertEqual(0, hub.client_count)
                    self.assertEqual(["STOPPED"] * 8, [m.status["state"] for m in monitors])
                finally:
                    release.set()
                    if closer:
                        await asyncio.gather(closer, return_exceptions=True)
                    await runtime.close()
                    store.close()

    async def test_actual_commit_then_lost_ack_retries_same_document_without_rejudgment(self):
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import SHADOW_RESULT_COLLECTION
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "ack.sqlite3")
            store.initialize()
            now, hub, collector, monitors, runtime = self.recorded_runtime(store, interval=0.5)
            original = store.upsert_documents
            calls = []
            def write(*args):
                calls.append(args)
                original(*args)
                if len(calls) == 1:
                    raise OSError("COMMIT ACK lost")
            with patch.object(store, "upsert_documents", side_effect=write):
                try:
                    await runtime.start()
                    self.trades(collector, now)
                    await self.wait_for(lambda: runtime.status["recording"]["last_error"] != "")
                    self.assertEqual(1, len(store.load_documents(SHADOW_RESULT_COLLECTION)))
                    self.assertEqual([1] * 8, [m.status["evaluations"] for m in monitors])
                    await self.wait_for(lambda: runtime.status["recording"]["writes"] == 1)
                    self.assertEqual(2, len(calls))
                    self.assertEqual(calls[0], calls[1])
                    self.assertEqual(1, len(store.load_documents(SHADOW_RESULT_COLLECTION)))
                    self.assertEqual([1] * 8, [m.status["evaluations"] for m in monitors])
                    self.assertEqual("", runtime.status["recording"]["last_error"])
                finally:
                    await runtime.close()
                    store.close()

    async def test_persistent_write_failure_close_is_observable_and_dependencies_released(self):
        store = Mock()
        store.upsert_documents.side_effect = OSError("selected write failed")
        now, hub, collector, monitors, runtime = self.recorded_runtime(store)
        await runtime.start()
        self.trades(collector, now)
        await self.wait_for(lambda: runtime.status["recording"]["last_error"] != "")
        with self.assertRaisesRegex(OSError, "selected write failed"):
            await runtime.close()
        self.assertEqual("STOPPED", runtime.status["state"])
        self.assertEqual(0, hub.client_count)
        self.assertIsNone(collector._ram_observation_owner)
        self.assertTrue(runtime.status["recording"]["pending"])
        self.assertEqual(["STOPPED"] * 8, [m.status["state"] for m in monitors])
        with self.assertRaisesRegex(OSError, "selected write failed"):
            await runtime.close()

    async def test_four_policies_record_shared_context_once_and_independent_actions(self):
        fixture = thesis_fixtures.StockThesisShadowTests()
        fixture.setUp()
        inputs = fixture.weak_inputs()
        requests = (None, *(fixture.request(policy) for policy in
                           ("PRICE_ONLY", "IMMEDIATE_EXIT", "REDUCE_ON_RISK", "CONFIRM_THEN_EXIT")))
        monitors = tuple(MarketThemeShadowMonitor(theme_id="반도체", leadership_venue="KRX",
            stock_thesis=request, leadership_parameters=thesis_fixtures.PARAMETERS) for request in requests)
        _, hub, collector, _, _ = self.fixture(0, cleanup=False)
        store = Mock()
        runtime = MarketThemeShadowRuntime(hub, collector, tuple((m, inputs) for m in monitors), record_store=store)
        try:
            for monitor in monitors:
                await monitor.offer_inputs(inputs)
            await run_market_theme_shadow_monitors_once(monitors)
            runtime._select_record()
            await self.wait_for(lambda: runtime.status["recording"]["writes"] == 1)
            document = store.upsert_documents.call_args.args[1][0]["document"]
            self.assertEqual((1, 5), (len(document["contexts"]), len(document["results"])))
            self.assertEqual(["HOLD", "EXIT", "REDUCE", "WAIT"], [r["decision"]["action"] for r in document["results"][1:]])
            self.assertTrue(all("context" not in r["decision"] for r in document["results"][1:]))
            copied = runtime.latest_results
            next(iter(copied["contexts"].values()))["order_authorized"] = True
            self.assertFalse(monitors[0].last_result["order_authorized"])
        finally:
            await runtime.close()

    async def test_authenticated_api_memory_default_and_recorded_restart_remain_diagnostics(self):
        import httpx
        from fastapi import FastAPI, Header, HTTPException
        from kiwoom_monitor.central_server.research_read_routes import create_research_read_router
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "http.sqlite3")
            store.initialize()
            now, hub, collector, monitors, runtime = self.recorded_runtime(store)
            holder = [runtime]
            app = FastAPI()
            def authorize(authorization: str = Header(default="")):
                if authorization != "Bearer offline-token":
                    raise HTTPException(401)
            app.include_router(create_research_read_router(store, authorize, lambda: None,
                get_market_theme_shadow_runtime=lambda: holder[0]))
            try:
                await runtime.start()
                self.trades(collector, now)
                await self.wait_for(lambda: runtime.status["recording"]["writes"] == 1)
                with patch.object(store, "load_document", wraps=store.load_document) as reads:
                  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                    self.assertEqual(401, (await client.get("/api/v1/research/ram-shadow?include_recorded=true")).status_code)
                    self.assertEqual(0, reads.call_count)
                    response = await client.get("/api/v1/research/ram-shadow", headers={"Authorization": "Bearer offline-token"})
                    self.assertEqual(200, response.status_code)
                    body = response.json()
                    self.assertEqual((1, 8), (len(body["live"]["contexts"]), len(body["live"]["results"])))
                    self.assertTrue(all(r["display_state"] == "EXPIRED" for r in body["live"]["results"]))
                    self.assertEqual(0, reads.call_count)
                    self.assertIsNone(body["recorded"])
                    await runtime.close()
                    holder[0] = None
                    response = await client.get("/api/v1/research/ram-shadow?include_recorded=true", headers={"Authorization": "Bearer offline-token"})
                    body = response.json()
                    self.assertEqual(1, reads.call_count)
                    self.assertIsNone(body["live"])
                    self.assertEqual("DISABLED", body["runtime"]["state"])
                    self.assertEqual(runtime._session_id, body["recorded"]["document"]["session_id"])
                    self.assertFalse(body["recorded_is_live"])
                    self.assertFalse(body["replay_eligible"])
                    self.assertFalse(body["order_authorized"])
            finally:
                await runtime.close()
                store.close()

    async def test_actual_app_default_recording_off_and_explicit_selection_is_read_only(self):
        import httpx
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                settings = CentralServerSettings(database_url=f"sqlite:///{Path(directory) / 'app.sqlite3'}",
                    access_token="offline-token", kiwoom_app_key="offline-unused", kiwoom_secret_key="offline-unused",
                    autonomous_top20_enabled=False, market_event_collection_enabled=False, news_history_jobs_enabled=False,
                    news_query_set_enabled=False, news_naver_api_enabled=False)
                monitor = MarketThemeShadowMonitor(theme_id="반도체", leadership_venue="KRX")
                kwargs = {"market_theme_shadow_recording": True} if enabled else {}
                store = SQLiteQueryStore(Path(directory) / "app.sqlite3")
                with patch("kiwoom_monitor.central_server.app.create_query_store", return_value=store):
                    app = create_app(settings, market_theme_shadow_bindings=((monitor, shadow_inputs()),), **kwargs)
                runtime = app.state.market_theme_shadow_runtime
                try:
                    self.assertEqual(enabled, runtime.status["recording"]["enabled"])
                    with patch.object(store, "load_document", wraps=store.load_document) as reads, \
                         patch.object(store, "upsert_documents", wraps=store.upsert_documents) as writes:
                        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                            response = await client.get("/api/v1/research/ram-shadow", headers={"Authorization": "Bearer offline-token"})
                            self.assertEqual(200, response.status_code)
                            self.assertEqual([], response.json()["live"]["results"])
                            response = await client.post("/api/v1/content/ram_shadow_diagnostics",
                                headers={"Authorization": "Bearer offline-token"},
                                json={"documents": [{"owner": "market_theme", "key": "latest", "document": {}}]})
                            self.assertEqual(404, response.status_code)
                        self.assertEqual(0, reads.call_count)
                        self.assertEqual(0, writes.call_count)
                    with self.assertRaisesRegex(ValueError, "explicit monitor bindings"):
                        create_app(settings, market_theme_shadow_recording=True)
                finally:
                    await runtime.close()
                    store.close()


class RamShadowSelectionTests(unittest.IsolatedAsyncioTestCase):
    wait_for = CollectorRamShadowRuntimeTests.wait_for
    trades = CollectorRamShadowRuntimeTests.trades

    def app_fixture(self, directory, *, path=None, configured=True, explicit=False):
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from kiwoom_monitor.domain.research_contract import THEME_BACKUP_FORMAT, THEME_BACKUP_VERSION
        store = SQLiteQueryStore(path or Path(directory) / "selection.sqlite3")
        settings = CentralServerSettings(database_url="sqlite:///:memory:", access_token="offline-token",
            kiwoom_app_key="offline-unused" if configured else "", kiwoom_secret_key="offline-unused" if configured else "",
            autonomous_top20_enabled=False, market_event_collection_enabled=False, news_history_jobs_enabled=False,
            news_query_set_enabled=False, news_naver_api_enabled=False)
        bindings = ((MarketThemeShadowMonitor(theme_id="반도체", leadership_venue="KRX"), shadow_inputs()),) if explicit else ()
        with patch("kiwoom_monitor.central_server.app.create_query_store", return_value=store):
            app = create_app(settings, market_theme_shadow_bindings=bindings)
        now = [datetime(2026, 9, 14, 9, tzinfo=timezone(timedelta(hours=9)))]
        collector = app.state.realtime_collector
        if collector:
            collector._now_provider = lambda: now[0]
            collector._apply_trade_source_approval({("005930", "KRX"), ("000660", "KRX")}, now[0], reset_all=True)
        theme = OwnerContextShadowTests().theme(now[0])
        document = {"format": THEME_BACKUP_FORMAT, "version": THEME_BACKUP_VERSION, **theme["document"]}
        with patch("kiwoom_monitor.central_server.database_documents.time", return_value=now[0].timestamp()):
            store.upsert_documents("theme_metadata", [{"owner": "default", "key": "full", "document": document}])
        return app, store, now, collector

    async def cleanup_app(self, app, store):
        runtime = app.state.market_theme_shadow_runtime
        if runtime is not None:
            await runtime.close()
        store.close()

    async def test_authenticated_selection_validate_noop_replace_disable_and_owner_updates(self):
        import httpx
        url = "/api/v1/research/ram-shadow/selection"
        headers = {"Authorization": "Bearer offline-token"}
        with tempfile.TemporaryDirectory() as directory:
            app, store, now, collector = self.app_fixture(directory)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                    self.assertEqual(401, (await client.put(url, json={"expected_revision": 0, "enabled": True})).status_code)
                    self.assertFalse((await client.get(url, headers=headers)).json()["enabled"])
                    with patch.object(store, "upsert_documents", wraps=store.upsert_documents) as writes:
                        for change in ({"enabled": True}, {"enabled": "yes"}, {"theme_ids": ["missing"], "enabled": True},
                                       {"theme_ids": ["반도체", "반도체"]}, {"thesis_policy": "EXIT"}, {"leadership_venue": "COMBINED"}):
                            self.assertEqual(422, (await client.put(url, headers=headers, json={"expected_revision": 0, **change})).status_code)
                        self.assertEqual(0, writes.call_count)
                        enabled = (await client.put(url, headers=headers, json={"expected_revision": 0, "enabled": True,
                            "theme_ids": ["반도체"], "recording": True})).json()
                        self.assertEqual((1, 1, "ACTIVE"), (enabled["revision"], enabled["applied_revision"], enabled["apply_status"]))
                        runtime = app.state.market_theme_shadow_runtime
                        self.assertEqual(((), ()), app.state.realtime_hub.requested_codes())
                        self.trades(collector, now)
                        await self.wait_for(lambda: runtime.status["dispatches"] >= 1)
                        await self.wait_for(lambda: runtime.status["recording"]["writes"] >= 1)
                        self.assertEqual(409, (await client.put(url, headers=headers, json={"expected_revision": 0, "enabled": False})).status_code)
                        count = writes.call_count
                        response = await client.put(url, headers=headers, json={"expected_revision": 1, "enabled": True})
                        self.assertEqual(1, response.json()["revision"])
                        self.assertIs(runtime, app.state.market_theme_shadow_runtime)
                        self.assertEqual(count, writes.call_count)
                        # Content notification registered while initially disabled
                        # must now reach the dynamically selected runtime.
                        before = runtime.status["theme_context_reads"]
                        from kiwoom_monitor.domain.research_contract import THEME_BACKUP_FORMAT, THEME_BACKUP_VERSION
                        empty_theme = {"format": THEME_BACKUP_FORMAT, "version": THEME_BACKUP_VERSION,
                            "active_profile": "default", "profiles": [{"name": "default", "stock_themes": []}]}
                        with patch("kiwoom_monitor.central_server.database_documents.time", return_value=now[0].timestamp()):
                            response = await client.put("/api/v1/content/theme_metadata", headers=headers,
                                json={"documents": [{"owner": "default", "key": "full", "document": empty_theme}]})
                        self.assertEqual(200, response.status_code)
                        await self.wait_for(lambda: runtime.status["theme_context_reads"] > before)
                        await self.wait_for(lambda: runtime._monitors[0]._last_offered.theme_membership is None)
                        response = await client.put(url, headers=headers, json={"expected_revision": 1, "leadership_venue": "NXT"})
                        self.assertEqual(422, response.status_code)
                        response = await client.put(url, headers=headers, json={"expected_revision": 1, "enabled": False})
                        self.assertEqual((2, "ACTIVE"), (response.json()["revision"], response.json()["apply_status"]))
                        self.assertIsNone(app.state.market_theme_shadow_runtime)
                        self.assertEqual("STOPPED", runtime.status["state"])
                        self.assertEqual(0, app.state.realtime_hub.client_count)
                        self.assertIsNone(collector._ram_observation_owner)
            finally:
                await self.cleanup_app(app, store)

    async def test_save_failure_and_commit_ack_loss_require_saved_revision_recovery(self):
        import httpx
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import SHADOW_SELECTION_COLLECTION
        for lost_ack in (False, True):
            with self.subTest(lost_ack=lost_ack), tempfile.TemporaryDirectory() as directory:
                app, store, now, collector = self.app_fixture(directory)
                original = store.upsert_documents
                def write(collection, values):
                    if collection == SHADOW_SELECTION_COLLECTION:
                        if lost_ack:
                            original(collection, values)
                        raise OSError("selection native write failure")
                    original(collection, values)
                try:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                        url = "/api/v1/research/ram-shadow/selection"; headers = {"Authorization": "Bearer offline-token"}
                        body = {"expected_revision": 0, "enabled": True, "theme_ids": ["반도체"]}
                        with patch.object(store, "upsert_documents", side_effect=write):
                            self.assertEqual(503, (await client.put(url, headers=headers, json=body)).status_code)
                        current = (await client.get(url, headers=headers)).json()
                        self.assertEqual(int(lost_ack), current["revision"])
                        self.assertIsNone(app.state.market_theme_shadow_runtime)
                        self.assertEqual("RECOVERY_REQUIRED" if lost_ack else "ACTIVE", current["apply_status"])
                        if lost_ack:
                            with patch.object(store, "upsert_documents", wraps=original) as writes:
                                recovered = await client.put(url, headers=headers, json={"expected_revision": 1})
                                self.assertEqual((1, 1, "ACTIVE"), (recovered.json()["revision"], recovered.json()["applied_revision"], recovered.json()["apply_status"]))
                                self.assertEqual(0, writes.call_count)
                finally:
                    await self.cleanup_app(app, store)

    async def test_apply_start_failure_retry_does_not_increment_saved_revision(self):
        import httpx
        with tempfile.TemporaryDirectory() as directory:
            app, store, now, collector = self.app_fixture(directory)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                    headers = {"Authorization": "Bearer offline-token"}; url = "/api/v1/research/ram-shadow/selection"
                    with patch.object(MarketThemeShadowRuntime, "start", side_effect=OSError("start failed")):
                        response = await client.put(url, headers=headers, json={"expected_revision": 0, "enabled": True, "theme_ids": ["반도체"]})
                        self.assertEqual(503, response.status_code)
                    failed = app.state.market_theme_shadow_runtime
                    self.assertEqual("RECOVERY_REQUIRED", (await client.get(url, headers=headers)).json()["apply_status"])
                    with patch.object(store, "upsert_documents", wraps=store.upsert_documents) as writes:
                        response = await client.put(url, headers=headers, json={"expected_revision": 1})
                        self.assertEqual((1, "ACTIVE"), (response.json()["revision"], response.json()["apply_status"]))
                        self.assertEqual(0, writes.call_count)
                        self.assertEqual("STOPPED", failed.status["state"])
                        self.assertIsNot(failed, app.state.market_theme_shadow_runtime)
            finally:
                await self.cleanup_app(app, store)

    async def test_cancelled_selection_joins_native_save_and_serializes_competing_change(self):
        import httpx
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import SHADOW_SELECTION_COLLECTION
        with tempfile.TemporaryDirectory() as directory:
            app, store, now, collector = self.app_fixture(directory)
            entered, release = threading.Event(), threading.Event()
            original = store.upsert_documents
            def write(collection, values):
                if collection == SHADOW_SELECTION_COLLECTION:
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("selection write not released")
                original(collection, values)
            first = second = None
            try:
                with patch.object(store, "upsert_documents", side_effect=write):
                  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                    headers = {"Authorization": "Bearer offline-token"}; url = "/api/v1/research/ram-shadow/selection"
                    first = asyncio.create_task(client.put(url, headers=headers, json={"expected_revision": 0, "enabled": True, "theme_ids": ["반도체"]}))
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    first.cancel(); await asyncio.sleep(0.02); first.cancel()
                    second = asyncio.create_task(client.put(url, headers=headers, json={"expected_revision": 0, "enabled": False}))
                    await asyncio.sleep(0.02)
                    self.assertFalse(first.done()); self.assertFalse(second.done())
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await first
                    self.assertEqual(409, (await second).status_code)
                    selection = (await client.get(url, headers=headers)).json()
                    self.assertEqual((1, 1, "ACTIVE"), (selection["revision"], selection["applied_revision"], selection["apply_status"]))
                    self.assertEqual(1, app.state.realtime_hub.client_count)
            finally:
                release.set()
                await asyncio.gather(*(task for task in (first, second) if task), return_exceptions=True)
                await self.cleanup_app(app, store)

    async def test_saved_selection_restart_and_shutdown_fence_restore_no_thesis_or_orders(self):
        import httpx
        from unittest.mock import AsyncMock
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "restart.sqlite3"
            app, store, now, collector = self.app_fixture(directory, path=path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                response = await client.put("/api/v1/research/ram-shadow/selection", headers={"Authorization": "Bearer offline-token"},
                    json={"expected_revision": 0, "enabled": True, "theme_ids": ["반도체"], "leadership_venue": "NXT"})
                self.assertEqual(200, response.status_code)
            old = app.state.market_theme_shadow_runtime
            await self.cleanup_app(app, store)
            app, store, now, collector = self.app_fixture(directory, path=path)
            try:
                with patch.object(CentralRealtimeCollector, "start", AsyncMock()), \
                     patch.object(CentralRealtimeCollector, "close", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.start", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.close", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.diagnostic_trace.recover_interrupted"), \
                     patch("kiwoom_monitor.central_server.diagnostic_trace.stop"):
                    async with app.router.lifespan_context(app):
                        runtime = app.state.market_theme_shadow_runtime
                        self.assertIsNotNone(runtime)
                        self.assertNotEqual(old._session_id, runtime._session_id)
                        self.assertIsNone(runtime._monitors[0]._stock_thesis)
                        if runtime._monitors[0].last_result is not None:
                            self.assertFalse(runtime._monitors[0].last_result["order_authorized"])
                        self.assertEqual("NXT", runtime._monitors[0]._venue)
                self.assertEqual("STOPPED", runtime.status["state"])
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                    response = await client.put("/api/v1/research/ram-shadow/selection", headers={"Authorization": "Bearer offline-token"}, json={"expected_revision": 1, "enabled": False})
                    self.assertEqual(503, response.status_code)
            finally:
                await self.cleanup_app(app, store)

    async def test_missing_collector_and_explicit_bindings_cannot_be_overridden(self):
        import httpx
        for configured, explicit in ((False, False), (True, True)):
            with self.subTest(configured=configured, explicit=explicit), tempfile.TemporaryDirectory() as directory:
                app, store, now, collector = self.app_fixture(directory, configured=configured, explicit=explicit)
                try:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                        response = await client.put("/api/v1/research/ram-shadow/selection", headers={"Authorization": "Bearer offline-token"}, json={"expected_revision": 0, "enabled": True, "theme_ids": ["반도체"]})
                        self.assertEqual(422, response.status_code)
                finally:
                    await self.cleanup_app(app, store)

    async def test_shutdown_joins_pending_selection_before_native_store_close(self):
        import httpx
        from unittest.mock import AsyncMock
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import SHADOW_SELECTION_COLLECTION
        with tempfile.TemporaryDirectory() as directory:
            app, store, now, collector = self.app_fixture(directory)
            entered, release = threading.Event(), threading.Event()
            original = store.upsert_documents
            def write(collection, values):
                if collection == SHADOW_SELECTION_COLLECTION:
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("selection not released before shutdown")
                original(collection, values)
            first = closer = None
            with patch.object(CentralRealtimeCollector, "start", AsyncMock()), \
                 patch.object(CentralRealtimeCollector, "close", AsyncMock()), \
                 patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.start", AsyncMock()), \
                 patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.close", AsyncMock()), \
                 patch("kiwoom_monitor.central_server.diagnostic_trace.recover_interrupted"), \
                 patch("kiwoom_monitor.central_server.diagnostic_trace.stop"), \
                 patch.object(store, "close", wraps=store.close) as close_store:
                life = app.router.lifespan_context(app)
                await life.__aenter__()
                try:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                        with patch.object(store, "upsert_documents", side_effect=write):
                            first = asyncio.create_task(client.put("/api/v1/research/ram-shadow/selection",
                                headers={"Authorization": "Bearer offline-token"}, json={"expected_revision": 0, "enabled": True, "theme_ids": ["반도체"]}))
                            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                            closer = asyncio.create_task(life.__aexit__(None, None, None))
                            await asyncio.sleep(0.03)
                            self.assertFalse(closer.done()); close_store.assert_not_called()
                            release.set()
                            self.assertEqual(200, (await first).status_code)
                            await closer
                            close_store.assert_called_once()
                            self.assertEqual("STOPPED", app.state.market_theme_shadow_runtime.status["state"])
                            self.assertEqual(0, app.state.realtime_hub.client_count)
                            self.assertIsNone(collector._ram_observation_owner)
                finally:
                    release.set()
                    await asyncio.gather(*(task for task in (first, closer) if task), return_exceptions=True)
                    if closer is None:
                        await life.__aexit__(None, None, None)

    async def test_invalid_saved_selection_requires_explicit_repair_and_disabled_save_needs_no_collector(self):
        import httpx
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import SHADOW_SELECTION_COLLECTION
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.sqlite3"
            store = SQLiteQueryStore(path); store.initialize()
            store.upsert_documents(SHADOW_SELECTION_COLLECTION, [{"owner": "global", "key": "current", "document": {"enabled": "invalid"}}])
            store.close()
            app, store, now, collector = self.app_fixture(directory, path=path, configured=False)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                    url = "/api/v1/research/ram-shadow/selection"; headers = {"Authorization": "Bearer offline-token"}
                    state = (await client.get(url, headers=headers)).json()
                    self.assertEqual("RECOVERY_REQUIRED", state["apply_status"])
                    self.assertIsNone(app.state.market_theme_shadow_runtime)
                    response = await client.put(url, headers=headers, json={"expected_revision": 0, "enabled": False})
                    self.assertEqual((1, "ACTIVE"), (response.json()["revision"], response.json()["apply_status"]))
                    document = store.load_document(SHADOW_SELECTION_COLLECTION, "global", "current")["document"]
                    self.assertIs(document["enabled"], False)
                    self.assertEqual("ram-shadow-selection/v1", document["version"])
            finally:
                await self.cleanup_app(app, store)

    async def test_saved_selection_start_failure_remains_pending_without_automatic_second_start(self):
        import httpx
        from unittest.mock import AsyncMock
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import SHADOW_SELECTION_COLLECTION
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "start-failure.sqlite3"
            store = SQLiteQueryStore(path); store.initialize()
            store.upsert_documents(SHADOW_SELECTION_COLLECTION, [{"owner": "global", "key": "current", "document": {
                "version": "ram-shadow-selection/v1", "revision": 1, "enabled": True, "theme_ids": ["반도체"],
                "leadership_venue": "KRX", "recording": False}}])
            store.close()
            app, store, now, collector = self.app_fixture(directory, path=path)
            try:
                with patch.object(MarketThemeShadowRuntime, "start", AsyncMock(side_effect=OSError("startup failure"))) as start, \
                     patch.object(CentralRealtimeCollector, "start", AsyncMock()), \
                     patch.object(CentralRealtimeCollector, "close", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.start", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.close", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.diagnostic_trace.recover_interrupted"), \
                     patch("kiwoom_monitor.central_server.diagnostic_trace.stop"):
                    async with app.router.lifespan_context(app):
                        start.assert_awaited_once()
                        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                            state = (await client.get("/api/v1/research/ram-shadow/selection", headers={"Authorization": "Bearer offline-token"})).json()
                            self.assertEqual("RECOVERY_REQUIRED", state["apply_status"])
                            self.assertEqual(1, state["revision"])
                            self.assertEqual(0, app.state.realtime_hub.client_count)
            finally:
                await self.cleanup_app(app, store)


def saved_thesis_selection_fixture(directory):
    from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
    repository = ResearchRepository(Path(directory) / "research.sqlite3")
    repository.start_run("selection-research-run", {"mode": "offline-fixture"}, {"dataset_id": "fixture"})
    first = thesis_fixtures.first_evaluation()
    thesis = thesis_fixtures.build_entry_thesis(run_id="selection-research-run", symbol="005930", leadership=first,
        factor_refs=("stored-factor",), hypothesis_refs=(), created_at=first.as_of, available_at=first.as_of)
    repository.append_theme_leadership(first)
    repository.append_entry_thesis(thesis)
    return {"evidence": repository.load_entry_thesis_evidence(thesis.run_id, thesis.thesis_id),
            "thesis_policy_version": "PRICE_ONLY"}


class StockThesisSourceCheckHttpTests(unittest.IsolatedAsyncioTestCase):
    app_fixture = RamShadowSelectionTests.app_fixture
    cleanup_app = RamShadowSelectionTests.cleanup_app
    url = "/api/v1/research/ram-shadow/source-check"
    selection_url = "/api/v1/research/ram-shadow/selection"
    headers = {"Authorization": "Bearer offline-token"}

    async def attach(self, client, directory):
        response = await client.put(self.selection_url, headers=self.headers,
            json={"expected_revision": 0, "theme_ids": ["반도체"], "stock_thesis": saved_thesis_selection_fixture(directory)})
        self.assertEqual(200, response.status_code, response.text)

    async def test_authenticated_explicit_report_never_changes_selection_or_live_poll_cost(self):
        import httpx
        with tempfile.TemporaryDirectory() as directory:
            app, store, now, collector = self.app_fixture(directory)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                    await self.attach(client, directory)
                    with patch.object(store, "load_theme_snapshots", wraps=store.load_theme_snapshots) as themes, \
                         patch.object(store, "load_second_trade_bars", wraps=store.load_second_trade_bars) as seconds, \
                         patch.object(store, "upsert_documents", wraps=store.upsert_documents) as writes:
                        self.assertEqual(401, (await client.get(self.url, params={"expected_revision": 1})).status_code)
                        for _ in range(3):
                            self.assertEqual(200, (await client.get("/api/v1/research/ram-shadow", headers=self.headers)).status_code)
                        self.assertEqual((0, 0, 0), (themes.call_count, seconds.call_count, writes.call_count))
                        report = await client.get(self.url, headers=self.headers, params={"expected_revision": 1})
                        self.assertEqual(200, report.status_code, report.text)
                        self.assertEqual(1, report.json()["selection_revision"])
                        self.assertFalse(report.json()["order_authorized"])
                        self.assertFalse(report.json()["replay_eligible"])
                        self.assertTrue(report.json()["unverified_refs"]["leadership"])
                        self.assertEqual((1, 1, 0), (themes.call_count, seconds.call_count, writes.call_count))
                        selected = (await client.get(self.selection_url, headers=self.headers)).json()
                        self.assertEqual(1, selected["revision"])
                        self.assertFalse(selected["enabled"])
                        self.assertIsNone(app.state.market_theme_shadow_runtime)
            finally:
                await self.cleanup_app(app, store)

    async def test_missing_selection_stale_revision_and_invalid_window_do_not_read_sources(self):
        import httpx
        with tempfile.TemporaryDirectory() as directory:
            app, store, now, collector = self.app_fixture(directory)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                    self.assertEqual(409, (await client.get(self.url, headers=self.headers, params={"expected_revision": 0})).status_code)
                    await self.attach(client, directory)
                    with patch.object(store, "load_theme_snapshots", side_effect=AssertionError("unexpected source read")):
                        for params, status in (({"expected_revision": 0}, 409), ({"expected_revision": 1, "lookback_seconds": 61}, 422), ({}, 422)):
                            self.assertEqual(status, (await client.get(self.url, headers=self.headers, params=params)).status_code)
            finally:
                await self.cleanup_app(app, store)

    async def test_repeated_cancellation_drains_read_and_shutdown_before_store_close(self):
        import httpx
        from unittest.mock import AsyncMock
        from contextlib import ExitStack
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        with tempfile.TemporaryDirectory() as directory:
            app, store, now, collector = self.app_fixture(directory)
            entered, release = threading.Event(), threading.Event()
            original = store.load_theme_snapshots
            def read(**kwargs):
                entered.set()
                if not release.wait(5):
                    raise AssertionError("source read was not released")
                return original(**kwargs)
            task = closer = None
            with ExitStack() as stack:
                stack.enter_context(patch.object(CentralRealtimeCollector, "start", AsyncMock()))
                stack.enter_context(patch.object(CentralRealtimeCollector, "close", AsyncMock()))
                stack.enter_context(patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.start", AsyncMock()))
                stack.enter_context(patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.close", AsyncMock()))
                stack.enter_context(patch("kiwoom_monitor.central_server.diagnostic_trace.recover_interrupted"))
                stack.enter_context(patch("kiwoom_monitor.central_server.diagnostic_trace.stop"))
                close_store = stack.enter_context(patch.object(store, "close", wraps=store.close))
                life = app.router.lifespan_context(app)
                await life.__aenter__()
                try:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                        await self.attach(client, directory)
                        with patch.object(store, "load_theme_snapshots", side_effect=read):
                            task = asyncio.create_task(client.get(self.url, headers=self.headers, params={"expected_revision": 1}))
                            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                            task.cancel()
                            closer = asyncio.create_task(life.__aexit__(None, None, None))
                            await asyncio.sleep(0.03)
                            task.cancel()
                            self.assertFalse(task.done())
                            self.assertFalse(closer.done())
                            close_store.assert_not_called()
                            release.set()
                            with self.assertRaises(asyncio.CancelledError):
                                await task
                            await closer
                            close_store.assert_called_once()
                        self.assertEqual(409, (await client.get(self.url, headers=self.headers, params={"expected_revision": 1})).status_code)
                finally:
                    release.set()
                    await asyncio.gather(*(t for t in (task, closer) if t), return_exceptions=True)
                    if closer is None:
                        await life.__aexit__(None, None, None)


class PositionComparisonSelectionTests(unittest.IsolatedAsyncioTestCase):
    app_fixture = RamShadowSelectionTests.app_fixture
    cleanup_app = RamShadowSelectionTests.cleanup_app
    wait_for = RamShadowSelectionTests.wait_for
    trades = RamShadowSelectionTests.trades
    url = "/api/v1/research/ram-shadow/selection"
    headers = {"Authorization": "Bearer offline-token"}

    def document(self):
        from tests.unit.test_entry_thesis import PositionActionComparisonTests
        fixture = PositionActionComparisonTests(); fixture.setUp()
        return fixture.document()

    async def test_authenticated_attach_single_calculation_read_copy_noop_and_clear(self):
        import httpx
        from kiwoom_monitor.application import position_target
        with tempfile.TemporaryDirectory() as directory:
            app, store, now, collector = self.app_fixture(directory)
            try:
                with patch.object(position_target, "evaluate_position_action_comparison_document",
                                  wraps=position_target.evaluate_position_action_comparison_document) as evaluate:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                        body = {"expected_revision": 0, "enabled": True, "theme_ids": ["반도체"],
                                "position_comparison": self.document()}
                        self.assertEqual(401, (await client.put(self.url, json=body)).status_code)
                        self.assertEqual(0, evaluate.call_count)
                        response = await client.put(self.url, headers=self.headers, json=body)
                        self.assertEqual(200, response.status_code, response.text)
                        self.assertEqual(1, evaluate.call_count)
                        runtime = app.state.market_theme_shadow_runtime
                        self.trades(collector, now)
                        await self.wait_for(lambda: runtime.status["dispatches"] >= 1)
                        expected = json.loads(json.dumps(runtime.latest_results["position_comparison"]["result"]))
                        with patch.object(store, "_connect", side_effect=AssertionError("live read accessed DB")):
                            for _ in range(3):
                                response = await client.get("/api/v1/research/ram-shadow", headers=self.headers)
                                self.assertEqual(200, response.status_code, response.text)
                                value = response.json()["live"]["position_comparison"]
                                self.assertFalse(value["is_live_account"])
                                self.assertEqual(expected, value["result"])
                        value["result"]["targets"]["HOLD"]["actual_quantity"] = 999
                        self.assertEqual(10, runtime.latest_results["position_comparison"]["result"]["targets"]["HOLD"]["actual_quantity"])
                        self.assertEqual(1, evaluate.call_count)
                        response = await client.put(self.url, headers=self.headers,
                            json={"expected_revision": 1, "position_comparison": body["position_comparison"]})
                        self.assertEqual(200, response.status_code)
                        self.assertIs(runtime, app.state.market_theme_shadow_runtime)
                        self.assertEqual(1, evaluate.call_count)
                        response = await client.put(self.url, headers=self.headers,
                            json={"expected_revision": 1, "position_comparison": None})
                        self.assertEqual(200, response.status_code, response.text)
                        self.assertNotIn("position_comparison", response.json())
                        self.assertNotIn("position_comparison", app.state.market_theme_shadow_runtime.latest_results)
                        self.assertEqual(((), ()), app.state.realtime_hub.requested_codes())
            finally:
                await self.cleanup_app(app, store)

    async def test_invalid_comparison_cannot_write_or_replace_current_runtime(self):
        import httpx
        with tempfile.TemporaryDirectory() as directory:
            app, store, now, collector = self.app_fixture(directory)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                    response = await client.put(self.url, headers=self.headers,
                        json={"expected_revision": 0, "enabled": True, "theme_ids": ["반도체"]})
                    self.assertEqual(200, response.status_code)
                    runtime = app.state.market_theme_shadow_runtime
                    bad = self.document(); bad["requests"][1]["account_ref"] = "other-account"
                    missing = self.document(); missing.pop("account")
                    duplicate = self.document(); duplicate["outcomes"].append(deepcopy(duplicate["outcomes"][0]))
                    wrong_symbol = self.document()
                    for request in wrong_symbol["requests"]:
                        request["symbol"] = "035420"
                    oversized = self.document(); oversized["extra"] = "x" * (256 * 1024)
                    with patch.object(store, "upsert_documents", wraps=store.upsert_documents) as writes:
                        for document in (bad, missing, duplicate, wrong_symbol, oversized):
                            response = await client.put(self.url, headers=self.headers,
                                json={"expected_revision": 1, "position_comparison": document})
                            self.assertEqual(422, response.status_code, response.text)
                            self.assertIs(runtime, app.state.market_theme_shadow_runtime)
                        self.assertEqual(0, writes.call_count)
                    response = await client.put(self.url, headers=self.headers,
                        json={"expected_revision": 1, "leadership_venue": "NXT", "position_comparison": self.document()})
                    self.assertEqual(422, response.status_code)
                    self.assertNotIn("position_comparison", store.load_document("ram_shadow_selection", "global", "current")["document"])
            finally:
                await self.cleanup_app(app, store)

    async def test_eight_consumers_share_one_fixed_result_and_existing_record_transaction(self):
        from kiwoom_monitor.application import position_target
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import selected_position_comparison
        now, hub, collector, monitors, _ = CollectorRamShadowRuntimeTests.fixture(self, 8, cleanup=False)
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "comparison.sqlite3"); store.initialize()
            sql = []; connect = store._connect
            def traced():
                connection = connect(); connection.set_trace_callback(sql.append); return connection
            with patch.object(position_target, "evaluate_position_action_comparison_document",
                              wraps=position_target.evaluate_position_action_comparison_document) as evaluate, \
                 patch.object(store, "_connect", side_effect=traced), \
                 patch.object(collector, "ram_observation_snapshot", wraps=collector.ram_observation_snapshot) as reads:
                result = selected_position_comparison({"position_comparison": self.document()})
                runtime = MarketThemeShadowRuntime(hub, collector, tuple((m, shadow_inputs()) for m in monitors),
                    position_comparison=result, record_store=store)
                expected = json.loads(json.dumps(result))
                result["targets"]["HOLD"]["actual_quantity"] = 999
                try:
                    await runtime.start(); self.trades(collector, now)
                    await self.wait_for(lambda: runtime.status["recording"]["writes"] == 1)
                    self.assertEqual(1, reads.call_count)
                    self.assertEqual(1, evaluate.call_count)
                    self.assertEqual(1, sum(statement == "COMMIT" for statement in sql))
                    saved = store.load_document("ram_shadow_diagnostics", "market_theme", "latest")["document"]
                    self.assertEqual(8, len(saved["results"]))
                    self.assertEqual(expected, saved["position_comparison"]["result"])
                    now[0] = now[0].replace(second=40); hub.publish({"type": "trade"})
                    await self.wait_for(lambda: monitors[0].status["evaluations"] == 2)
                    self.assertEqual(1, runtime.status["recording"]["writes"])
                    self.assertEqual(1, evaluate.call_count)
                    self.assertEqual(expected, json.loads(json.dumps(runtime.latest_results["position_comparison"]["result"])))
                    self.assertFalse(saved["order_authorized"])
                finally:
                    await runtime.close(); store.close()

    async def test_saved_fixed_cut_restart_and_lost_ack_recovery_do_not_promote_account(self):
        await self._assert_saved_restart_isolation(self.document())

    async def test_selection_policy_and_order_free_recommendation_survive_saved_restart(self):
        from tests.unit.test_entry_thesis import PositionActionSelectionTests
        fixture = PositionActionSelectionTests(); fixture.setUp()
        fixture.parameters = replace(fixture.parameters,selection_policy=fixture.policy())
        document = fixture.document()
        value = await self._assert_saved_restart_isolation(document)
        self.assertEqual('REDUCE',value['result']['action_selection']['action'])
        self.assertFalse(value['result']['action_selection']['order_authorized'])
        restored = value['result']['parameters']['selection_policy']
        for name, original in document['parameters']['selection_policy'].items():
            if name in {'input_mode','thesis_ref'}:
                self.assertEqual({'input_mode':'supplied_fixed','thesis_ref':''}[name],original)
                self.assertNotIn(name,restored)
            elif name in {'available_at','valid_until'}:
                self.assertEqual(datetime.fromisoformat(original),datetime.fromisoformat(restored[name]))
            else:
                self.assertEqual(original,restored[name])

    async def test_native_selection_restart_restores_policy_but_not_account_risk_or_thesis_approval(self):
        from tests.unit.test_entry_thesis import NativePositionSelectionInputsTests
        fixture = NativePositionSelectionInputsTests(); fixture.setUp()
        document = fixture.document()
        value = await self._assert_saved_restart_isolation(document)
        result = value['result']
        self.assertEqual('owner_ram_risk_and_thesis',result['parameters']['selection_policy']['input_mode'])
        self.assertEqual(fixture.thesis_fixture.thesis.thesis_id,result['parameters']['selection_policy']['thesis_ref'])
        self.assertEqual(('WAIT',False),tuple(result['action_selection'][k] for k in ('action','order_authorized')))
        self.assertIsNone(result['selection_inputs']['risk_snapshot_ref'])
        self.assertIsNone(result['selection_inputs']['thesis_decision_ref'])

    async def test_automatic_policy_and_content_binding_survive_saved_restart(self):
        document = self.document()
        cut = datetime.fromisoformat(document['requests'][1]['decision_time'])
        document['requests'][1].update(target_quantity=None, target_policy={
            'version':'bounded-notional-sizing/v1','target_notional_won':5_000,
            'max_position_notional_won':30_000,'max_position_quantity':30,'cash_buffer_won':1_000,
            'available_at':(cut-timedelta(seconds=1)).isoformat(),
            'valid_until':(cut+timedelta(seconds=20)).isoformat()})
        value = await self._assert_saved_restart_isolation(document)
        target = value['result']['targets']['REDUCE']
        self.assertEqual(5,target['target_quantity'])
        self.assertTrue(target['target_policy_version'].startswith('position-sizing-policy:'))
        for name, original in document['requests'][1]['target_policy'].items():
            restored = target['target_sizing']['parameters'][name]
            if name in {'available_at','valid_until'}:
                self.assertEqual(datetime.fromisoformat(original),datetime.fromisoformat(restored))
            else:
                self.assertEqual(original,restored)

    async def _assert_saved_restart_isolation(self, document):
        import httpx
        from unittest.mock import AsyncMock
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "server.sqlite3"
            app, store, now, collector = self.app_fixture(directory, path=path)
            original = store.upsert_documents
            def lost_ack(*args, **kwargs):
                original(*args, **kwargs); raise OSError("commit ACK lost")
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                    with patch.object(store, "upsert_documents", side_effect=lost_ack):
                        response = await client.put(self.url, headers=self.headers,
                            json={"expected_revision": 0, "enabled": True, "theme_ids": ["반도체"], "position_comparison": document})
                    self.assertEqual(503, response.status_code)
                    response = await client.put(self.url, headers=self.headers, json={"expected_revision": 1})
                    self.assertEqual(200, response.status_code, response.text)
                old = app.state.market_theme_shadow_runtime
                expected = old.latest_results["position_comparison"]["result"]["decision_id"]
            finally:
                await self.cleanup_app(app, store)
            app, store, now, collector = self.app_fixture(directory, path=path)
            try:
                with patch.object(CentralRealtimeCollector, "start", AsyncMock()), \
                     patch.object(CentralRealtimeCollector, "close", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.start", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.close", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.diagnostic_trace.recover_interrupted"), \
                     patch("kiwoom_monitor.central_server.diagnostic_trace.stop"):
                    async with app.router.lifespan_context(app):
                        runtime = app.state.market_theme_shadow_runtime
                        value = runtime.latest_results["position_comparison"]
                        self.assertNotEqual(old._session_id, runtime._session_id)
                        self.assertEqual(expected, value["result"]["decision_id"])
                        self.assertEqual(datetime.fromisoformat(document["requests"][0]["decision_time"]),
                                         datetime.fromisoformat(value["result"]["decision_time"]))
                        self.assertFalse(value["is_live_account"])
                        self.assertFalse(value["result"]["order_authorized"])
                        self.assertIsNone(runtime._monitors[0]._state)
            finally:
                await self.cleanup_app(app, store)
            return value


class PositionAccountRamShadowTests(unittest.IsolatedAsyncioTestCase):
    fixture = CollectorRamShadowRuntimeTests.fixture
    trades = CollectorRamShadowRuntimeTests.trades
    wait_for = CollectorRamShadowRuntimeTests.wait_for

    def policy(self):
        from tests.unit.test_entry_thesis import PositionActionComparisonTests
        value = PositionActionComparisonTests(); value.setUp()
        return value.document(), value.account

    async def test_action_required_context_is_shared_without_blocking_common_account_data(self):
        from tests.unit.test_entry_thesis import NativePositionSelectionInputsTests
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        from kiwoom_monitor.application import position_target
        for count in (2,8):
            fixture=NativePositionSelectionInputsTests();fixture.setUp()
            fixture.parameters=replace(fixture.parameters,selection_policy=fixture.capability_policy(('sampled_price',),
                action_required_data_capabilities=(('REDUCE',('news_context',)),)))
            now,hub,collector,monitors,_=self.fixture(count,cleanup=False)
            self.trades(collector,now)
            account=replace(fixture.account,as_of=now[0]);risk=fixture.risk_snapshot(observed=now[0],account_at=now[0])
            source=Mock(return_value=PositionAccountObservation(account,(),'action-context-account',now[0],True,True,(),(),(),risk))
            runtime=MarketThemeShadowRuntime(hub,collector,tuple((m,shadow_inputs()) for m in monitors),
                position_document=fixture.document(),position_account_source=source)
            try:
                with patch.object(position_target,'evaluate_position_action_comparison_shadow',wraps=position_target.evaluate_position_action_comparison_shadow) as evaluate, \
                     patch('sqlite3.connect',side_effect=AssertionError('action matrix SQLite')), \
                     patch('psycopg.connect',side_effect=AssertionError('action matrix PostgreSQL')):
                    await runtime._dispatch(runtime._snapshot())
                    receipt=runtime.latest_results['position_comparison']
                    self.assertEqual('AVAILABLE',receipt['quality']['status'])
                    self.assertEqual('WAIT',receipt['result']['action_selection']['action'])
                    self.assertEqual([],receipt['result']['selection_inputs']['missing_data_capabilities'])
                    self.assertEqual(['news_context'],receipt['result']['selection_inputs']['action_inputs']['REDUCE']['missing_data_capabilities'])
                    for _ in range(100):runtime.latest_results
                    self.assertEqual((1,1),(source.call_count,evaluate.call_count))
                    self.assertFalse(receipt['result']['order_authorized'])
            finally:await runtime.close()

    async def test_theme_independent_runtime_starts_records_and_closes_without_theme_consumers(self):
        from tests.unit.test_entry_thesis import NativePositionSelectionInputsTests
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        fixture=NativePositionSelectionInputsTests();fixture.setUp()
        fixture.parameters=replace(fixture.parameters,selection_policy=fixture.capability_policy(('sampled_price',)))
        now,hub,collector,_,_=self.fixture(0,cleanup=False)
        account=replace(fixture.account,as_of=now[0].replace(second=39))
        risk=fixture.risk_snapshot(observed=account.as_of,account_at=account.as_of)
        source=Mock(return_value=PositionAccountObservation(account,(),'r4-only-account',account.as_of,True,True,(),(),(),risk))
        record=Mock(); ranking=Mock(side_effect=AssertionError('unused ranking source'))
        theme=Mock(side_effect=AssertionError('unused theme source'))
        runtime=MarketThemeShadowRuntime(hub,collector,(),position_document=fixture.document(),
            position_account_source=source,ranking_source=ranking,theme_source=theme,record_store=record)
        try:
            with patch('sqlite3.connect',side_effect=AssertionError('R4-only SQLite')), \
                 patch('psycopg.connect',side_effect=AssertionError('R4-only PostgreSQL')):
                requested=hub.requested_codes()
                await runtime.start();self.trades(collector,now)
                await self.wait_for(lambda:runtime.status['recording']['writes']==1)
                receipt=runtime.latest_results['position_comparison']
                self.assertEqual('REDUCE',receipt['result']['action_selection']['action'])
                self.assertEqual({},runtime.latest_results['contexts'])
                self.assertEqual([],runtime.latest_results['results'])
                self.assertEqual((0,0,1,1),(runtime.status['theme_context_reads'],runtime.status['ranking_context_reads'],
                    runtime.status['snapshot_reads'],source.call_count))
                self.assertEqual(requested,hub.requested_codes())
                runtime.context_changed('theme');runtime.context_changed('ranking')
                for _ in range(100):runtime.latest_results
                await asyncio.sleep(0.02)
                self.assertEqual(1,record.upsert_documents.call_count)
                saved=record.upsert_documents.call_args.args[1][0]['document']
                self.assertEqual(receipt['decision_time'],saved['selected_at'])
                self.assertEqual('REDUCE',saved['position_comparison']['result']['action_selection']['action'])
                self.assertFalse(saved['order_authorized'])
                await runtime.close()
                self.assertEqual('STOPPED',runtime.status['state'])
                self.assertEqual(0,hub.client_count)
                self.assertEqual(1,source.call_count)
                ranking.assert_not_called();theme.assert_not_called()
        finally:await runtime.close()
        for policy in (fixture.policy(),fixture.capability_policy(('stock_thesis',)),
                       fixture.capability_policy(('theme_context',))):
            fixture.parameters=replace(fixture.parameters,selection_policy=policy)
            with self.assertRaises(ValueError):
                MarketThemeShadowRuntime(hub,collector,(),position_document=fixture.document(),position_account_source=source)
        with self.assertRaises(ValueError):MarketThemeShadowRuntime(hub,collector,())

    async def test_theme_independent_authenticated_selection_persists_without_theme_snapshot_reads(self):
        import httpx
        from tests.unit.test_entry_thesis import NativePositionSelectionInputsTests
        fixture=NativePositionSelectionInputsTests();fixture.setUp()
        legacy=fixture.document()
        fixture.parameters=replace(fixture.parameters,selection_policy=fixture.capability_policy(('sampled_price',)))
        document=fixture.document();helper=RamShadowSelectionTests()
        with tempfile.TemporaryDirectory() as directory:
            app,store,now,collector=helper.app_fixture(directory)
            url='/api/v1/research/ram-shadow/selection';headers={'Authorization':'Bearer offline-token'}
            try:
                store.replace_documents('theme_metadata',[])
                with patch.object(store,'load_theme_snapshots',side_effect=AssertionError('unrequired theme DB read')):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
                        body={'expected_revision':0,'enabled':True,'theme_ids':[],
                            'position_comparison':document,'position_account_live':True}
                        self.assertEqual(401,(await client.put(url,json=body)).status_code)
                        self.assertEqual(422,(await client.put(url,headers=headers,json={**body,'position_comparison':legacy})).status_code)
                        response=await client.put(url,headers=headers,json=body)
                        self.assertEqual(200,response.status_code,response.text)
                        self.assertEqual('position_context',response.json()['mode'])
                        runtime=app.state.market_theme_shadow_runtime
                        self.trades(collector,now)
                        await self.wait_for(lambda:runtime.status['dispatches']==1)
                        with patch.object(store,'_connect',side_effect=AssertionError('R4-only status DB read')):
                            response=await client.get('/api/v1/research/ram-shadow',headers=headers)
                        self.assertEqual(200,response.status_code,response.text)
                        self.assertEqual('UNAVAILABLE',response.json()['live']['position_comparison']['quality']['status'])
                        self.assertFalse(response.json()['live']['order_authorized'])
                        response=await client.put(url,headers=headers,json={**body,'expected_revision':1})
                        self.assertEqual(200,response.status_code,response.text)
                        self.assertIs(runtime,app.state.market_theme_shadow_runtime)
                        stored=store.load_document('ram_shadow_selection','global','current')['document']
                        saved_session=runtime._session_id
                        self.assertEqual([],stored['theme_ids'])
                        self.assertEqual(['sampled_price'],stored['position_comparison']['parameters']['selection_policy']['required_data_capabilities'])
            finally:await helper.cleanup_app(app,store)
            app,store,_,_=helper.app_fixture(directory)
            try:
                from unittest.mock import AsyncMock
                from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
                with patch.object(store,'load_theme_snapshots',side_effect=AssertionError('restart unrequired theme DB read')), \
                     patch.object(CentralRealtimeCollector,'start',AsyncMock()), \
                     patch.object(CentralRealtimeCollector,'close',AsyncMock()), \
                     patch('kiwoom_monitor.central_server.rest_broker.CentralRestBroker.start',AsyncMock()), \
                     patch('kiwoom_monitor.central_server.rest_broker.CentralRestBroker.close',AsyncMock()), \
                     patch('kiwoom_monitor.central_server.diagnostic_trace.recover_interrupted'), \
                     patch('kiwoom_monitor.central_server.diagnostic_trace.stop'):
                    async with app.router.lifespan_context(app):
                        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
                            response=await client.get(url,headers=headers)
                        self.assertEqual('',response.json()['last_error'])
                        self.assertEqual('ACTIVE',response.json()['apply_status'])
                        self.assertEqual('position_context',response.json()['mode'])
                        runtime=app.state.market_theme_shadow_runtime
                        self.assertNotEqual(saved_session,runtime._session_id)
                        self.assertIsNone(runtime.latest_results['position_comparison']['result'])
                        self.assertEqual('UNAVAILABLE',runtime.latest_results['position_comparison']['quality']['status'])
            finally:await helper.cleanup_app(app,store)

    async def test_capability_policy_price_only_shares_native_risk_without_theme_or_news(self):
        from tests.unit.test_entry_thesis import NativePositionSelectionInputsTests
        from kiwoom_monitor.application import position_target
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        for count in (2,8):
            with self.subTest(consumers=count):
                fixture=NativePositionSelectionInputsTests();fixture.setUp()
                fixture.parameters=replace(fixture.parameters,selection_policy=fixture.capability_policy(('sampled_price',)))
                now,hub,collector,monitors,_=self.fixture(count,cleanup=False)
                self.trades(collector,now)
                account=replace(fixture.account,as_of=now[0])
                risk=fixture.risk_snapshot(observed=now[0],account_at=now[0])
                source=Mock(return_value=PositionAccountObservation(account,(),'capability-native-cut',now[0],True,True,(),(),(),risk))
                runtime=MarketThemeShadowRuntime(hub,collector,tuple((m,shadow_inputs()) for m in monitors),
                    position_document=fixture.document(),position_account_source=source)
                try:
                    runtime._apply_theme_snapshot(())
                    with patch.object(collector,'ram_observation_snapshot',wraps=collector.ram_observation_snapshot) as read, \
                         patch.object(position_target,'evaluate_position_action_comparison_shadow',
                            wraps=position_target.evaluate_position_action_comparison_shadow) as calculate, \
                         patch('sqlite3.connect',side_effect=AssertionError('capability RAM SQLite')), \
                         patch('psycopg.connect',side_effect=AssertionError('capability RAM PostgreSQL')):
                        await runtime._dispatch(runtime._snapshot())
                        receipt=runtime.latest_results['position_comparison']
                        result=receipt['result']
                        self.assertEqual('SELECTED',result['action_selection']['status'])
                        self.assertEqual('REDUCE',result['action_selection']['action'])
                        self.assertEqual(['sampled_price'],result['selection_inputs']['required_data_capabilities'])
                        self.assertEqual([],result['selection_inputs']['missing_data_capabilities'])
                        self.assertEqual(risk.snapshot_id,result['selection_inputs']['risk_snapshot_ref'])
                        self.assertIsNone(result['selection_inputs']['thesis_decision_ref'])
                        self.assertFalse(result['order_authorized'])
                        self.assertLessEqual(datetime.fromisoformat(receipt['valid_until']),fixture.parameters.selection_policy.valid_until)
                        for _ in range(100):runtime.latest_results
                        self.assertEqual((1,1,1),(read.call_count,source.call_count,calculate.call_count))
                finally:await runtime.close()

    async def test_native_quote_is_independent_of_missing_themes_and_second_history(self):
        from kiwoom_monitor.application import position_target
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        for count in (2, 8):
            with self.subTest(consumers=count):
                document, account = self.policy()
                now, hub, collector, monitors, _ = self.fixture(count, cleanup=False)
                self.trades(collector, now)
                source = Mock(return_value=PositionAccountObservation(
                    replace(account, as_of=now[0]), (), 'independent-price-account', now[0], True, True, ()))
                runtime = MarketThemeShadowRuntime(hub, collector, tuple((m, shadow_inputs()) for m in monitors),
                    position_document=document, position_account_source=source)
                try:
                    runtime._apply_theme_snapshot(())
                    dirty = (set(collector._minute_bars._dirty), tuple(r["input_ref"] for r in collector._ram_trades))
                    with patch.object(collector, 'ram_observation_snapshot', wraps=collector.ram_observation_snapshot) as read, \
                         patch.object(position_target, 'evaluate_position_action_comparison_shadow',
                                      wraps=position_target.evaluate_position_action_comparison_shadow) as evaluate, \
                         patch('sqlite3.connect', side_effect=AssertionError('R4 quote read SQLite')), \
                         patch('psycopg.connect', side_effect=AssertionError('R4 quote read PostgreSQL')):
                        snapshot = runtime._snapshot()
                        snapshot['rows'] = ()  # A quote does not require a retained seconds window.
                        await runtime._dispatch(snapshot)
                        receipt = runtime.latest_results['position_comparison']
                        self.assertIsNotNone(receipt['result'])
                        self.assertEqual('COMPARABLE', receipt['result']['status'])
                        self.assertEqual('AVAILABLE', receipt['quality']['status'])
                        self.assertEqual(('005930',), runtime._codes())
                        quote = next(r for r in snapshot['trade_references'] if r['code'] == '005930')
                        self.assertEqual(quote['input_ref'], receipt['price_input_ref'])
                        self.assertEqual(quote['price_won'], evaluate.call_args.args[1][0].reference_price_won)
                        self.assertFalse(receipt['result']['order_authorized'])
                        self.assertEqual([{'code': '005930', 'venue': 'KRX', 'approved': True,
                            'continuous_from': snapshot['continuous_from'][('005930', 'KRX')].isoformat()}],
                            runtime._source_coverage(snapshot, {})['sources'])
                        self.assertTrue(all(m._result['leadership']['quality']['status'] != 'COMPLETE' for m in monitors))
                        for _ in range(100):
                            runtime.latest_results
                        self.assertEqual((1, 1, 1), (read.call_count, source.call_count, evaluate.call_count))
                    self.assertEqual(dirty, (set(collector._minute_bars._dirty), tuple(r["input_ref"] for r in collector._ram_trades)))
                finally:
                    await runtime.close()

    async def test_independent_quote_does_not_replace_required_native_risk_or_thesis(self):
        from tests.unit.test_entry_thesis import NativePositionSelectionInputsTests
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        fixture = NativePositionSelectionInputsTests(); fixture.setUp()
        now, hub, collector, monitors, _ = self.fixture(2, cleanup=False)
        self.trades(collector, now)
        account = replace(fixture.account, as_of=now[0])
        risk = fixture.risk_snapshot(observed=now[0], account_at=now[0])
        source = Mock(return_value=PositionAccountObservation(
            account, (), 'independent-price-risk', now[0], True, True, (), (), (), risk))
        runtime = MarketThemeShadowRuntime(hub, collector, tuple((m, shadow_inputs()) for m in monitors),
            position_document=fixture.document(), position_account_source=source)
        try:
            runtime._apply_theme_snapshot(())
            await runtime._refresh_position_inputs(runtime._snapshot())
            result = runtime.latest_results['position_comparison']['result']
            self.assertIsNotNone(result)
            self.assertEqual('BLOCKED', result['action_selection']['status'])
            self.assertIn('native_thesis_unavailable_or_ambiguous', result['selection_inputs']['reasons'])
            self.assertEqual(risk.snapshot_id, result['selection_inputs']['risk_snapshot_ref'])
            self.assertFalse(result['order_authorized'])
        finally:
            await runtime.close()

    async def test_independent_quote_rejects_stale_future_unknown_source_and_delivery_gap(self):
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        document, account = self.policy()
        now, hub, collector, monitors, _ = self.fixture(2, cleanup=False)
        self.trades(collector, now)
        source = Mock(return_value=PositionAccountObservation(
            replace(account, as_of=now[0]), (), 'independent-price-boundaries', now[0], True, True, ()))
        runtime = MarketThemeShadowRuntime(hub, collector, tuple((m, shadow_inputs()) for m in monitors),
            position_document=document, position_account_source=source)
        try:
            runtime._apply_theme_snapshot(())
            original = runtime._snapshot()
            quote = next(r for r in original['trade_references'] if r['code'] == '005930')
            mutations = {
                'unapproved': lambda s: s.update(approved_sources=()),
                'missing': lambda s: s.update(trade_references=()),
                'duplicate': lambda s: s.update(trade_references=(quote, quote)),
                'wrong_venue': lambda s: s.update(trade_references=({**quote, 'market': 'NXT'},)),
                'durable_claim': lambda s: s.update(trade_references=({**quote, 'durable': True},)),
                'wrong_lineage': lambda s: s.update(trade_references=({**quote, 'input_ref': 'unrelated-quote'},)),
                'boolean_price': lambda s: s.update(trade_references=({**quote, 'price_won': True},)),
                'halted': lambda s: s.update(trade_references=({**quote, 'market_status': 'HALTED'},)),
                'future': lambda s: s.update(trade_references=({**quote, 'available_at': now[0] + timedelta(seconds=1)},)),
                'unknown_source': lambda s: s.update(continuous_from={}),
                'after_quote_gap': lambda s: s.update(gap_at=quote['available_at'] + timedelta(microseconds=1)),
            }
            for name, mutate in mutations.items():
                with self.subTest(boundary=name):
                    snapshot = deepcopy(original); mutate(snapshot)
                    runtime._refresh_position_comparison(snapshot)
                    self.assertIsNone(runtime.latest_results['position_comparison']['result'])
            runtime._refresh_position_comparison(original)
            self.assertIsNotNone(runtime.latest_results['position_comparison']['result'])
            price_time = quote['available_at']
            self.assertEqual(price_time + timedelta(seconds=runtime._position_inputs[1][0].max_snapshot_age_seconds),
                             datetime.fromisoformat(runtime.latest_results['position_comparison']['valid_until']))
            now[0] = price_time + timedelta(seconds=runtime._position_inputs[1][0].max_snapshot_age_seconds)
            source.return_value = replace(source.return_value, account=replace(account, as_of=now[0]), observed_at=now[0])
            runtime._refresh_position_comparison(runtime._snapshot())
            self.assertIsNone(runtime.latest_results['position_comparison']['result'])
        finally:
            await runtime.close()

    async def test_native_risk_and_current_thesis_are_shared_once_without_new_database_reads(self):
        from tests.unit.test_entry_thesis import NativePositionSelectionInputsTests
        from kiwoom_monitor.application import position_target
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        fixture = NativePositionSelectionInputsTests(); fixture.setUp()
        document = fixture.document()
        now,hub,collector,monitors,_ = self.fixture(8,cleanup=False)
        cut = now[0]+timedelta(seconds=39)
        # Existing eight R3 consumers share one native thesis but retain their result slots.
        for monitor in monitors:
            monitor._stock_thesis = fixture.thesis_fixture.request('REDUCE_ON_RISK')
        account = replace(fixture.account,as_of=cut)
        risk = fixture.risk_snapshot(observed=cut)
        source = Mock(return_value=PositionAccountObservation(account,(),'native-cut:1',cut,True,True,(),(),(),risk))
        runtime = MarketThemeShadowRuntime(hub,collector,tuple((m,shadow_inputs()) for m in monitors),
            position_document=document,position_account_source=source)
        with patch.object(position_target,'evaluate_position_action_comparison_shadow',
                          wraps=position_target.evaluate_position_action_comparison_shadow) as evaluate, \
             patch('sqlite3.connect',side_effect=AssertionError('native inputs opened SQLite')), \
             patch('psycopg.connect',side_effect=AssertionError('native inputs opened PG')):
            try:
                await runtime.start(); self.trades(collector,now)
                await self.wait_for(lambda:runtime.status['position_evaluations']==1)
                value = runtime.latest_results['position_comparison']
                self.assertEqual(risk.snapshot_id,value['result']['selection_inputs']['risk_snapshot_ref'])
                current = monitors[0]._result
                self.assertEqual(current['decision_id'],value['result']['selection_inputs']['thesis_decision_ref'])
                self.assertEqual({m._result['decision_id'] for m in monitors},{current['decision_id']})
                self.assertEqual(8,len(runtime.latest_results['results']))
                for _ in range(8): runtime.latest_results
                self.assertEqual((1,1),(source.call_count,evaluate.call_count))
                original = deepcopy(source.return_value)
                before = [m.status['evaluations'] for m in monitors]
                self.assertLessEqual(datetime.fromisoformat(value['valid_until']),datetime.fromisoformat(current['valid_until']))
                now[0] += timedelta(seconds=1)
                runtime.context_changed('account')
                await self.wait_for(lambda:runtime.status['position_evaluations']==2)
                updated = runtime.latest_results['position_comparison']['result']
                self.assertEqual(risk.snapshot_id,updated['selection_inputs']['risk_snapshot_ref'])
                self.assertEqual(current['decision_id'],updated['selection_inputs']['thesis_decision_ref'])
                self.assertEqual(before,[m.status['evaluations'] for m in monitors])
                self.assertEqual(original,source.return_value)
                self.assertFalse(updated['action_selection']['order_authorized'])
                monitors[-1]._result = deepcopy(fixture.thesis)
                runtime._refresh_position_comparison(runtime._snapshot())
                ambiguous = runtime.latest_results['position_comparison']['result']
                self.assertEqual('WAIT',ambiguous['action_selection']['action'])
                self.assertIn('native_thesis_unavailable_or_ambiguous',ambiguous['selection_inputs']['reasons'])
                self.assertEqual((3,3),(source.call_count,evaluate.call_count))
            finally: await runtime.close()

    async def test_selection_is_shared_and_risk_cut_expiry_and_record_coalescing_are_preserved(self):
        from dataclasses import asdict
        from tests.unit.test_entry_thesis import PositionActionSelectionTests
        from kiwoom_monitor.application import position_target
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        fixture = PositionActionSelectionTests(); fixture.setUp()
        document = fixture.document()
        now, hub, collector, monitors, _ = self.fixture(8, cleanup=False)
        cut = now[0] + timedelta(seconds=39)
        policy = fixture.policy(risk_constraint='EXIT_REQUIRED', account_input_ref='risk-cut:1',
            orders_input_ref='risk-cut:1', available_at=cut, valid_until=cut+timedelta(seconds=3))
        document['parameters']['selection_policy'] = json.loads(json.dumps(asdict(policy), default=lambda v:v.isoformat()))
        account = replace(fixture.account, as_of=cut)
        source = Mock(return_value=PositionAccountObservation(account,(),'risk-cut:1',cut,True,True,(),(),()))
        runtime = MarketThemeShadowRuntime(hub,collector,tuple((m,shadow_inputs()) for m in monitors),
            position_document=document,position_account_source=source)
        with patch.object(position_target,'evaluate_position_action_comparison_shadow',
                          wraps=position_target.evaluate_position_action_comparison_shadow) as evaluate, \
             patch('sqlite3.connect',side_effect=AssertionError('selection opened SQLite')), \
             patch('psycopg.connect',side_effect=AssertionError('selection opened PG')):
            try:
                await runtime.start(); self.trades(collector,now)
                await self.wait_for(lambda:runtime.status['position_evaluations']==1)
                initial = runtime.latest_results['position_comparison']
                self.assertEqual('EXIT',initial['result']['action_selection']['action'])
                self.assertEqual(policy.valid_until,datetime.fromisoformat(initial['valid_until']))
                for _ in range(8): runtime.latest_results
                self.assertEqual((1,1),(source.call_count,evaluate.call_count))
                store = Mock(); runtime._record_store = store
                runtime._select_record()
                await self.wait_for(lambda:store.upsert_documents.call_count==1)
                now[0] += timedelta(seconds=1)
                runtime._refresh_position_comparison(runtime._snapshot()); runtime._select_record()
                await asyncio.sleep(.03)
                self.assertEqual(1,store.upsert_documents.call_count)
                self.assertEqual((2,2),(source.call_count,evaluate.call_count))
                before = [m.status['evaluations'] for m in monitors]
                now[0] = policy.valid_until
                runtime.context_changed('account')
                await self.wait_for(lambda:runtime.status['position_evaluations']==3)
                selection = runtime.latest_results['position_comparison']['result']['action_selection']
                self.assertEqual('WAIT',selection['action'])
                self.assertIn('selection_risk_input_expired',selection['reasons'])
                self.assertEqual(before,[m.status['evaluations'] for m in monitors])
                self.assertFalse(selection['order_authorized'])
            finally: await runtime.close()

    async def test_automatic_quantity_uses_shared_latest_price_once_for_eight_consumers(self):
        from dataclasses import asdict
        from kiwoom_monitor.application import position_target
        from kiwoom_monitor.application.position_target import PositionAccountObservation, PositionTargetSizingPolicy
        document, account = self.policy()
        now, hub, collector, monitors, _ = self.fixture(8, cleanup=False)
        policy = PositionTargetSizingPolicy('bounded-notional-sizing/v1',5_000,30_000,30,1_000,
            now[0],now[0]+timedelta(seconds=45))
        document['requests'][1].update(target_quantity=None,
            target_policy=json.loads(json.dumps(asdict(policy),default=lambda v:v.isoformat())))
        account = replace(account,as_of=now[0]+timedelta(seconds=39))
        source = Mock(return_value=PositionAccountObservation(account,(),'sizing-cut:1',account.as_of,True,True,(),(),()))
        runtime = MarketThemeShadowRuntime(hub,collector,tuple((m,shadow_inputs()) for m in monitors),
            position_document=document,position_account_source=source)
        with patch.object(position_target,'evaluate_position_action_comparison_shadow',
                          wraps=position_target.evaluate_position_action_comparison_shadow) as evaluate, \
             patch('sqlite3.connect',side_effect=AssertionError('sizing RAM opened DB')), \
             patch('psycopg.connect',side_effect=AssertionError('sizing RAM opened PG')):
            try:
                await runtime.start(); self.trades(collector,now)
                await self.wait_for(lambda: runtime.status['position_evaluations']==1)
                initial = runtime.latest_results['position_comparison']
                self.assertEqual(4,initial['result']['targets']['REDUCE']['target_quantity'])
                self.assertEqual(policy.valid_until,datetime.fromisoformat(initial['valid_until']))
                for _ in range(8): runtime.latest_results
                self.assertEqual((1,1),(source.call_count,evaluate.call_count))
                now[0] += timedelta(seconds=1)
                collector._publish_parsed({'trnm':'REAL','data':[{'type':'0B','item':'005930',
                    'values':{'10':'2000','15':'2','13':'200','20':now[0].strftime('%H%M%S')}}]})
                await self.wait_for(lambda: runtime.status['position_evaluations']==2)
                updated = runtime.latest_results['position_comparison']
                self.assertEqual(2,updated['result']['targets']['REDUCE']['target_quantity'])
                self.assertEqual(initial['result']['targets']['REDUCE']['target_policy_version'],
                                 updated['result']['targets']['REDUCE']['target_policy_version'])
                self.assertEqual((2,2),(source.call_count,evaluate.call_count))
                self.assertEqual(8,len(runtime.latest_results['results']))
                self.assertFalse(updated['result']['order_authorized'])
                self.assertEqual(document['requests'][1]['target_quantity'],None)
                before = [m.status['evaluations'] for m in monitors]
                now[0] = policy.valid_until
                source.return_value = replace(source.return_value,account=replace(account,as_of=now[0]),observed_at=now[0])
                runtime.context_changed('account')
                await self.wait_for(lambda: runtime.status['position_evaluations']==3)
                target = runtime.latest_results['position_comparison']['result']['targets']['REDUCE']
                self.assertIsNone(target['incremental_quantity'])
                self.assertIn('sizing_policy_expired',target['quality']['reasons'])
                self.assertEqual(before,[m.status['evaluations'] for m in monitors])
            finally: await runtime.close()

    async def test_complete_account_cut_is_shared_and_order_and_cost_expiry_bound_result(self):
        from kiwoom_monitor.application import position_target
        from kiwoom_monitor.application.position_target import PositionAccountObservation, PositionBrokerOrderExposure
        from kiwoom_monitor.domain.order_contract import OrderSide, OrderState
        document, account = self.policy()
        now, hub, collector, monitors, _ = self.fixture(8, cleanup=False)
        account = replace(account, as_of=now[0])
        source = Mock(return_value=PositionAccountObservation(account, (), "account-wide:1", now[0], True, True, (), (), ()))
        source.return_value = replace(source.return_value, account=replace(account, as_of=now[0].replace(second=39)),
                                      observed_at=now[0].replace(second=39))
        runtime = MarketThemeShadowRuntime(hub, collector, tuple((m, shadow_inputs()) for m in monitors),
            position_document=document, position_account_source=source)
        with patch.object(position_target, "evaluate_position_action_comparison_shadow",
                          wraps=position_target.evaluate_position_action_comparison_shadow) as evaluate, \
             patch("sqlite3.connect", side_effect=AssertionError("Shadow accessed DB")), \
             patch("psycopg.connect", side_effect=AssertionError("Shadow accessed PG")):
            try:
                await runtime.start(); self.trades(collector, now)
                await self.wait_for(lambda: runtime.status["position_evaluations"] == 1)
                value = runtime.latest_results["position_comparison"]
                self.assertEqual((1, 1), (source.call_count, evaluate.call_count))
                self.assertTrue(value["account_orders_complete"])
                self.assertTrue(value["account_reconciliation_complete"])
                self.assertEqual(5, value["result"]["targets"]["REDUCE"]["incremental_quantity"], value["result"]["targets"]["REDUCE"]["quality"])
                self.assertFalse(value["result"]["order_authorized"])
                self.assertEqual(8, len(runtime.latest_results["results"]))
                for _ in range(8): runtime.latest_results
                self.assertEqual((1, 1), (source.call_count, evaluate.call_count))
                exposure = PositionBrokerOrderExposure("manual", account.account_ref, "005930", OrderSide.SELL,
                    1, 0, 1, OrderState.ACCEPTED, 1000, now[0] - timedelta(seconds=4), None)
                source.return_value = replace(source.return_value, broker_open_orders=(exposure,))
                runtime.context_changed("account")
                await self.wait_for(lambda: runtime.status["position_evaluations"] == 2)
                value = runtime.latest_results["position_comparison"]
                age = runtime._position_inputs[1][0].max_snapshot_age_seconds
                self.assertEqual(exposure.as_of + timedelta(seconds=age), datetime.fromisoformat(value["valid_until"]))
                cost_end = now[0] + timedelta(seconds=1)
                parameters, requests, a, b, outcomes = runtime._position_inputs
                requests = tuple(replace(r, cost_model=replace(r.cost_model, valid_from=(now[0] - timedelta(seconds=1)).isoformat(),
                    valid_to=cost_end.isoformat())) for r in requests)
                runtime._position_inputs = (parameters, requests, a, b, outcomes)
                runtime.context_changed("account")
                await self.wait_for(lambda: runtime.status["position_evaluations"] == 3)
                self.assertEqual(cost_end, datetime.fromisoformat(runtime.latest_results["position_comparison"]["valid_until"]))
            finally:
                await runtime.close()

    async def test_eight_monitors_share_owner_and_price_cut_and_reads_do_not_recalculate(self):
        from kiwoom_monitor.application import position_target
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        document, account = self.policy()
        now, hub, collector, monitors, _ = self.fixture(8, cleanup=False)
        account = replace(account, as_of=now[0])
        observation = PositionAccountObservation(account, (), "account-ram:1", now[0], False, False,
                                                  ("account_wide_intent_coverage_unverified",))
        source = Mock(return_value=observation)
        runtime = MarketThemeShadowRuntime(hub, collector, tuple((m, shadow_inputs()) for m in monitors),
            position_document=document, position_account_source=source)
        with patch.object(collector, "ram_observation_snapshot", wraps=collector.ram_observation_snapshot) as reads, \
             patch.object(position_target, "evaluate_position_action_comparison_shadow",
                          wraps=position_target.evaluate_position_action_comparison_shadow) as evaluate, \
             patch("sqlite3.connect", side_effect=AssertionError("RAM accessed DB")), \
             patch("psycopg.connect", side_effect=AssertionError("RAM accessed PG")):
            try:
                await runtime.start(); self.trades(collector, now)
                await self.wait_for(lambda: runtime.status["position_evaluations"] >= 1)
                value = runtime.latest_results["position_comparison"]
                self.assertEqual((1, 1, 1), (source.call_count, reads.call_count, evaluate.call_count))
                self.assertEqual("owner_ram_snapshot", value["input_mode"])
                self.assertTrue(value["is_live_account"])
                self.assertEqual("account-ram:1", value["account_input_ref"])
                self.assertEqual(8, len(runtime.latest_results["results"]))
                for target in value["result"]["targets"].values():
                    self.assertEqual(10, target["actual_quantity"])
                    self.assertIsNone(target["incremental_quantity"])
                    self.assertIn("account_open_orders_incomplete", target["quality"]["reasons"])
                self.assertTrue(all(row["ev_status"] == "EV_UNAVAILABLE" for row in value["result"]["actions"]))
                for _ in range(8): runtime.latest_results
                self.assertEqual((1, 1), (source.call_count, evaluate.call_count))
                value["result"]["targets"]["HOLD"]["actual_quantity"] = 999
                self.assertEqual(10, runtime.latest_results["position_comparison"]["result"]["targets"]["HOLD"]["actual_quantity"])
                before = [m.status["evaluations"] for m in monitors]
                now[0] += timedelta(seconds=1)
                source.return_value = replace(observation, account=replace(account, positions={"005930": 7}, as_of=now[0]),
                    observed_at=now[0], input_ref="account-ram:2")
                runtime.context_changed("account")
                await self.wait_for(lambda: runtime.status["position_evaluations"] == 2)
                self.assertEqual(before, [m.status["evaluations"] for m in monitors])
                self.assertEqual(7, runtime.latest_results["position_comparison"]["result"]["targets"]["HOLD"]["actual_quantity"])
                source.return_value = None; runtime.context_changed("account")
                await self.wait_for(lambda: runtime.status["position_observation_reads"] == 3)
                self.assertIsNone(runtime.latest_results["position_comparison"]["result"])
                # A full observer queue cannot discard the owner's latest pending cut.
                queue = runtime._subscriber.queue
                self.assertGreater(queue.maxsize, 0)
                while not queue.full(): queue.put_nowait({"type": "ignored"})
                source.return_value = observation
                runtime.context_changed("account")
                await self.wait_for(lambda: runtime.status["position_observation_reads"] == 4)
                self.assertIsNotNone(runtime.latest_results["position_comparison"]["result"])
                self.assertEqual(before, [m.status["evaluations"] for m in monitors])
            finally:
                await runtime.close()

    async def test_missing_wrong_scope_future_or_unapproved_price_never_fall_back_to_fixed_account(self):
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        document, account = self.policy()
        now, hub, collector, monitors, _ = self.fixture(1, cleanup=False)
        source = Mock(return_value=None)
        runtime = MarketThemeShadowRuntime(hub, collector, tuple((m, shadow_inputs()) for m in monitors),
            position_document=document, position_account_source=source)
        try:
            snapshot = runtime._snapshot()
            runtime._refresh_position_comparison(snapshot)
            self.assertIsNone(runtime.latest_results["position_comparison"]["result"])
            source.return_value = PositionAccountObservation(replace(account, account_ref="another-account"), (),
                "account-ram:1", now[0], False, False, ())
            runtime._refresh_position_comparison(snapshot)
            self.assertEqual("ERROR", runtime.latest_results["position_comparison"]["quality"]["status"])
            source.return_value = replace(source.return_value, account=account, observed_at=now[0] + timedelta(seconds=1))
            runtime._refresh_position_comparison(snapshot)
            self.assertIn("future", runtime.latest_results["position_comparison"]["quality"]["reasons"][0])
            source.return_value = replace(source.return_value, observed_at=now[0])
            self.trades(collector, now)
            snapshot = runtime._snapshot(); snapshot["approved_sources"] = ()
            runtime._refresh_position_comparison(snapshot)
            self.assertEqual(["ram_reference_price_unavailable"], runtime.latest_results["position_comparison"]["quality"]["reasons"])
            self.assertIsNone(runtime.latest_results["position_comparison"]["result"])
        finally:
            await runtime.close()

    async def test_explicit_live_selection_without_account_owner_stays_unavailable_and_can_clear(self):
        import httpx
        helper = RamShadowSelectionTests()
        document, _ = self.policy()
        with tempfile.TemporaryDirectory() as directory:
            app, store, now, collector = helper.app_fixture(directory)
            headers = {"Authorization": "Bearer offline-token"}
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                    url = "/api/v1/research/ram-shadow/selection"
                    for change in ({"position_account_live": True}, {"position_account_live": "yes"}):
                        response = await client.put(url, headers=headers, json={"expected_revision": 0,
                            "enabled": True, "theme_ids": ["반도체"], **change})
                        self.assertEqual(422, response.status_code)
                    response = await client.put(url, headers=headers, json={"expected_revision": 0, "enabled": True,
                        "theme_ids": ["반도체"], "position_comparison": document, "position_account_live": True})
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertTrue(response.json()["position_account_live"])
                    runtime = app.state.market_theme_shadow_runtime
                    self.trades(collector, now); await self.wait_for(lambda: runtime.status["position_observation_reads"] >= 1)
                    # Freeze the producer to isolate HTTP reads from queued startup/trade dispatches.
                    runtime._task.cancel()
                    await asyncio.gather(runtime._task, return_exceptions=True)
                    count = runtime.status["position_observation_reads"]
                    with patch.object(store, "_connect", side_effect=AssertionError("RAM API accessed DB")):
                        response = await client.get("/api/v1/research/ram-shadow", headers=headers)
                        self.assertEqual(200, response.status_code)
                        self.assertIsNone(response.json()["live"]["position_comparison"]["result"])
                    self.assertEqual(count, runtime.status["position_observation_reads"])
                    response = await client.put(url, headers=headers,
                        json={"expected_revision": 1, "position_account_live": False})
                    self.assertEqual(200, response.status_code)
                    self.assertNotIn("position_account_live", response.json())
                    self.assertFalse(app.state.market_theme_shadow_runtime.latest_results["position_comparison"]["is_live_account"])
                    response = await client.put(url, headers=headers,
                        json={"expected_revision": 2, "position_account_live": True})
                    self.assertEqual(200, response.status_code)
                    response = await client.put(url, headers=headers,
                        json={"expected_revision": 3, "position_comparison": None})
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertNotIn("position_account_live", response.json())
                    self.assertNotIn("position_comparison", response.json())
            finally:
                await helper.cleanup_app(app, store)

    async def test_live_receipt_ref_changes_reuse_existing_selected_record_without_extra_commits(self):
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        document, account = self.policy()
        now, hub, collector, monitors, _ = self.fixture(8, cleanup=False)
        account = replace(account, as_of=now[0].replace(second=39))
        source = Mock(return_value=PositionAccountObservation(account, (), "account-ram:1", account.as_of,
            False, False, ("account_wide_intent_coverage_unverified",)))
        store = SQLiteQueryStore(Path(":memory:")); store.initialize()
        runtime = MarketThemeShadowRuntime(hub, collector, tuple((m, shadow_inputs()) for m in monitors),
            position_document=document, position_account_source=source, record_store=store)
        sql = []; connect = store._connect
        def traced():
            connection = connect(); connection.set_trace_callback(sql.append); return connection
        with patch.object(store, "_connect", side_effect=traced):
            try:
                await runtime.start(); self.trades(collector, now)
                await self.wait_for(lambda: runtime.status["recording"]["writes"] == 1)
                saved = store.load_document("ram_shadow_diagnostics", "market_theme", "latest")["document"]
                self.assertEqual(8, len(saved["results"]))
                self.assertEqual("owner_ram_snapshot", saved["position_comparison"]["input_mode"])
                self.assertEqual(1, sum(statement == "COMMIT" for statement in sql))
                before = [m.status["evaluations"] for m in monitors]
                now[0] += timedelta(seconds=1)
                source.return_value = replace(source.return_value, input_ref="account-ram:2", observed_at=now[0],
                    account=replace(account, as_of=now[0]))
                runtime.context_changed("account")
                await self.wait_for(lambda: runtime.status["position_evaluations"] == 2)
                self.assertEqual(before, [m.status["evaluations"] for m in monitors])
                self.assertEqual("account-ram:2", runtime.latest_results["position_comparison"]["account_input_ref"])
                self.assertEqual(1, runtime.status["recording"]["writes"])
                self.assertFalse(runtime.status["recording"]["pending"])
                self.assertEqual(1, sum(statement == "COMMIT" for statement in sql))
            finally:
                await runtime.close(); store.close()


class ResearchThesisSelectionTests(unittest.IsolatedAsyncioTestCase):
    app_fixture = RamShadowSelectionTests.app_fixture
    cleanup_app = RamShadowSelectionTests.cleanup_app
    wait_for = RamShadowSelectionTests.wait_for
    trades = RamShadowSelectionTests.trades
    url = "/api/v1/research/ram-shadow/selection"
    headers = {"Authorization": "Bearer offline-token"}

    async def test_attach_noop_live_order_block_and_explicit_clear(self):
        import httpx
        with tempfile.TemporaryDirectory() as directory:
            selected = saved_thesis_selection_fixture(directory)
            app, store, now, collector = self.app_fixture(directory)
            now[0] = now[0].replace(second=20)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                    body = {"expected_revision": 0, "enabled": True, "theme_ids": ["반도체"], "stock_thesis": selected}
                    self.assertEqual(401, (await client.put(self.url, json=body)).status_code)
                    response = await client.put(self.url, headers=self.headers, json=body)
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertEqual("research_thesis", response.json()["mode"])
                    runtime = app.state.market_theme_shadow_runtime
                    self.trades(collector, now)
                    await self.wait_for(lambda: runtime._monitors[0].last_result is not None)
                    decision = runtime._monitors[0].last_result
                    self.assertEqual(selected["evidence"]["thesis"]["thesis_id"], decision["thesis_ref"])
                    self.assertFalse(decision["order_authorized"])
                    self.assertFalse(runtime.status["durable"])
                    self.assertEqual(((), ()), app.state.realtime_hub.requested_codes())
                    with patch.object(store, "upsert_documents", wraps=store.upsert_documents) as writes:
                        self.assertEqual(200, (await client.put(self.url, headers=self.headers,
                            json={"expected_revision": 1, "stock_thesis": selected})).status_code)
                        self.assertIs(runtime, app.state.market_theme_shadow_runtime)
                        self.assertEqual(0, writes.call_count)
                    copied = (await client.get(self.url, headers=self.headers)).json()
                    copied["stock_thesis"]["evidence"]["thesis"]["symbol"] = "000660"
                    self.assertEqual("005930", (await client.get(self.url, headers=self.headers)).json()["stock_thesis"]["evidence"]["thesis"]["symbol"])
                    cleared = await client.put(self.url, headers=self.headers, json={"expected_revision": 1, "stock_thesis": None})
                    self.assertEqual(200, cleared.status_code, cleared.text)
                    self.assertNotIn("stock_thesis", cleared.json())
                    self.assertEqual("theme_context", cleared.json()["mode"])
                    self.assertIsNone(app.state.market_theme_shadow_runtime._monitors[0]._stock_thesis)
                    self.assertNotIn("stock_thesis", store.load_document("ram_shadow_selection", "global", "current")["document"])
            finally:
                await self.cleanup_app(app, store)

    async def test_corrupt_partial_future_cross_scope_path_and_oversized_evidence_are_rejected_without_writes(self):
        import httpx
        from copy import deepcopy
        with tempfile.TemporaryDirectory() as directory:
            selected = saved_thesis_selection_fixture(directory)
            app, store, now, collector = self.app_fixture(directory)
            cases = [{"stock_thesis": {"path": "PC.sqlite3"}}, {"stock_thesis": {**selected, "thesis_policy_version": "AUTO"}},
                     {"stock_thesis": selected, "leadership_venue": "NXT"}]
            corrupted = deepcopy(selected); corrupted["evidence"]["thesis"]["leader_id"] = "000660"
            cases.append({"stock_thesis": corrupted})
            failed = deepcopy(selected); failed["evidence"]["run_status"] = "failed"
            cases.append({"stock_thesis": failed})
            oversized = deepcopy(selected); oversized["evidence"]["leadership"]["input_refs"] = ["x" * (1024 * 1024)]
            cases.append({"stock_thesis": oversized})
            # The fixture server cut is 09:00:00; this thesis is available at 20.
            cases.append({"stock_thesis": selected})
            try:
                with patch.object(store, "upsert_documents", wraps=store.upsert_documents) as writes:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                        for change in cases:
                            response = await client.put(self.url, headers=self.headers,
                                json={"expected_revision": 0, "enabled": True, "theme_ids": ["반도체"], **change})
                            self.assertEqual(422, response.status_code, response.text)
                        self.assertEqual(0, writes.call_count)
                        self.assertIsNone(app.state.market_theme_shadow_runtime)
            finally:
                await self.cleanup_app(app, store)

    async def test_attach_and_clear_commit_ack_loss_retry_preserves_exact_saved_choice(self):
        import httpx
        with tempfile.TemporaryDirectory() as directory:
            selected = saved_thesis_selection_fixture(directory)
            app, store, now, collector = self.app_fixture(directory)
            now[0] = now[0].replace(second=20)
            original = store.upsert_documents
            def lost_ack(*args, **kwargs):
                original(*args, **kwargs)
                raise OSError("commit ACK lost")
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                    for revision, stock in ((0, selected), (1, None)):
                        with patch.object(store, "upsert_documents", side_effect=lost_ack):
                            response = await client.put(self.url, headers=self.headers,
                                json={"expected_revision": revision, "enabled": True, "theme_ids": ["반도체"], "stock_thesis": stock})
                        self.assertEqual(503, response.status_code)
                        saved = (await client.get(self.url, headers=self.headers)).json()
                        self.assertEqual(revision + 1, saved["revision"])
                        self.assertEqual("RECOVERY_REQUIRED", saved["apply_status"])
                        self.assertEqual(stock is not None, "stock_thesis" in saved)
                        with patch.object(store, "upsert_documents", wraps=original) as writes:
                            recovered = await client.put(self.url, headers=self.headers, json={"expected_revision": revision + 1})
                            self.assertEqual(200, recovered.status_code, recovered.text)
                            self.assertEqual(0, writes.call_count)
                            self.assertEqual("ACTIVE", recovered.json()["apply_status"])
            finally:
                await self.cleanup_app(app, store)

    async def test_selected_snapshot_restart_uses_new_h2_state_and_never_reopens_pc_database(self):
        import httpx
        from unittest.mock import AsyncMock
        from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
        with tempfile.TemporaryDirectory() as directory:
            selected = saved_thesis_selection_fixture(directory)
            path = Path(directory) / "server.sqlite3"
            app, store, now, collector = self.app_fixture(directory, path=path)
            now[0] = now[0].replace(second=20)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
                response = await client.put(self.url, headers=self.headers,
                    json={"expected_revision": 0, "enabled": True, "theme_ids": ["반도체"], "stock_thesis": selected})
                self.assertEqual(200, response.status_code, response.text)
            old = app.state.market_theme_shadow_runtime
            await self.cleanup_app(app, store)
            app, store, now, collector = self.app_fixture(directory, path=path)
            now[0] = now[0].replace(second=20)
            try:
                with patch.object(CentralRealtimeCollector, "start", AsyncMock()), \
                     patch.object(CentralRealtimeCollector, "close", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.start", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.rest_broker.CentralRestBroker.close", AsyncMock()), \
                     patch("kiwoom_monitor.central_server.diagnostic_trace.recover_interrupted"), \
                     patch("kiwoom_monitor.central_server.diagnostic_trace.stop"), \
                     patch("kiwoom_monitor.infrastructure.persistence.research_repository.ResearchRepository",
                           side_effect=AssertionError("server tried to open PC research DB")):
                    async with app.router.lifespan_context(app):
                        current = app.state.market_theme_shadow_runtime
                        self.assertNotEqual(old._session_id, current._session_id)
                        self.assertIsNone(current._monitors[0]._state)
                        self.assertEqual(selected["evidence"]["thesis"]["thesis_id"], current._monitors[0]._stock_thesis.entry_thesis.thesis_id)
                        self.trades(collector, now)
                        await self.wait_for(lambda: current._monitors[0].last_result is not None)
                        self.assertFalse(current._monitors[0].last_result["order_authorized"])
            finally:
                await self.cleanup_app(app, store)

    async def test_one_thesis_only_changes_its_theme_and_keeps_other_contexts_independent(self):
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import ram_shadow_bindings_from_snapshot
        with tempfile.TemporaryDirectory() as directory:
            selected = saved_thesis_selection_fixture(directory)
            base = shadow_inputs().theme_membership
            at = selected["evidence"]["thesis"]["available_at"]
            snapshot = OwnerContextShadowTests().theme(datetime.fromisoformat(at))
            # Use the existing membership conversion shape for two themes.
            snapshot["document"]["profiles"][0]["stock_themes"] += [
                {"code": code, "theme": "다른 테마"} for code in base.member_codes]
            selection = {"version": "ram-shadow-selection/v1", "revision": 1, "enabled": True,
                "theme_ids": ["반도체", "다른 테마"], "leadership_venue": "KRX", "recording": False, "stock_thesis": selected}
            bindings = ram_shadow_bindings_from_snapshot(selection, snapshot, at)
            self.assertIsNotNone(bindings[0][0]._stock_thesis)
            self.assertIsNone(bindings[1][0]._stock_thesis)
            await asyncio.gather(*(monitor.close() for monitor, _ in bindings))


class CollectorUpperLimitShadowTests(unittest.IsolatedAsyncioTestCase):
    fixture = CollectorRamShadowRuntimeTests.fixture
    trades = CollectorRamShadowRuntimeTests.trades
    wait_for = CollectorRamShadowRuntimeTests.wait_for

    def reference(self, collector, price="1040", *, code="005930", venue="KRX"):
        collector._publish_parsed({"trnm": "REAL", "data": [{
            "type": "0g", "item": code + ("_NX" if venue == "NXT" else ""),
            "values": {"305": price, "306": "700", "307": "800"},
        }]})

    async def test_current_collector_reference_reaches_live_shadow_without_database(self):
        now, hub, collector, monitors, runtime = self.fixture()
        with patch("sqlite3.connect", side_effect=AssertionError("RAM limit read DB")), \
             patch("psycopg.connect", side_effect=AssertionError("RAM limit read DB")):
            self.reference(collector)
            await runtime.start()
            self.trades(collector, now)
            await self.wait_for(lambda: monitors[0].status["evaluations"] == 1)
            self.assertEqual("NEAR", monitors[0].last_result["leadership"]["inferences"]["limit_state"])
            limit, = monitors[0]._last_offered.upper_limits
            self.assertEqual(1040, limit.price)
            self.assertTrue(limit.input_ref.startswith("ram:"))
            self.assertIn(limit.input_ref, monitors[0].last_result["leadership"]["evidence_refs"])
            await runtime.close()


    async def test_reference_arrival_updates_without_new_trade_or_database_work(self):
        now, hub, collector, monitors, runtime = self.fixture()
        await runtime.start()
        self.trades(collector, now)
        await self.wait_for(lambda: monitors[0].status["evaluations"] == 1)
        counts = (collector._minute_bars._dirty.copy(), tuple(r["input_ref"] for r in collector._ram_trades))
        with patch.object(collector._minute_bars, "add", side_effect=AssertionError("reference reaggregated trade")), \
             patch.object(collector, "_observe_ram_trade", side_effect=AssertionError("reference reaggregated trade")), \
             patch("sqlite3.connect", side_effect=AssertionError("reference read DB")):
            for index, (price, expected) in enumerate((("1040", "NEAR"), ("1030", "TOUCHED"),
                                                     ("1200", "NONE"), ("", "UNKNOWN")), start=2):
                self.reference(collector, price)
                await self.wait_for(lambda: monitors[0].status["evaluations"] == index)
                self.assertEqual(expected, monitors[0].last_result["leadership"]["inferences"]["limit_state"])
            before = runtime.status["snapshot_reads"]
            self.reference(collector, "")
            await self.wait_for(lambda: runtime.status["snapshot_reads"] > before)
            self.assertEqual(5, monitors[0].status["evaluations"])
            self.assertEqual(counts, (collector._minute_bars._dirty, tuple(r["input_ref"] for r in collector._ram_trades)))
            await runtime.close()

    async def test_reference_snapshot_is_scoped_copied_and_available_at_cut(self):
        now, hub, collector, monitors, runtime = self.fixture()
        self.reference(collector)
        snapshot = collector.ram_observation_snapshot(("005930",), seconds=20)
        self.assertEqual(1, len(snapshot["stock_references"]))
        self.assertEqual((), collector.ram_observation_snapshot(("000660",), seconds=20)["stock_references"])
        snapshot["stock_references"][0]["upper_limit_price"] = 1
        self.assertEqual(1040, collector.ram_observation_snapshot(("005930",), seconds=20)["stock_references"][0]["upper_limit_price"])
        now[0] -= timedelta(seconds=1)
        self.assertEqual((), collector.ram_observation_snapshot(("005930",), seconds=20)["stock_references"])
        now[0] += timedelta(days=1, seconds=1)
        self.assertEqual((), collector.ram_observation_snapshot(("005930",), seconds=20)["stock_references"])
        self.assertEqual({}, collector._ram_stock_references)

    async def test_reference_memory_is_bounded_by_approved_sources_and_clears_on_reset_or_gap(self):
        now, hub, collector, monitors, runtime = self.fixture(0)
        self.reference(collector)
        self.reference(collector, code="000660")
        for code in (f"{index:06d}" for index in range(10)):
            self.reference(collector, code=code)
        self.assertEqual(2, len(collector._ram_stock_references))
        collector._apply_trade_source_approval({("005930", "KRX")}, now[0], reset_all=False)
        self.assertEqual({("005930", "KRX")}, set(collector._ram_stock_references))
        collector._apply_trade_source_approval({("005930", "KRX")}, now[0], reset_all=True)
        self.assertEqual({}, collector._ram_stock_references)
        self.reference(collector)
        collector._mark_capture_gap()
        self.assertEqual({}, collector._ram_stock_references)

    async def test_krx_and_nxt_limits_stay_separate(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import market_theme_shadow_inputs_from_ram
        now, hub, collector, monitors, runtime = self.fixture(0)
        collector._apply_trade_source_approval({("005930", "KRX"), ("005930", "NXT")}, now[0], reset_all=False)
        self.reference(collector, "1040")
        self.reference(collector, "2040", venue="NXT")
        snapshot = collector.ram_observation_snapshot(("005930",), seconds=20)
        for venue, expected in (("KRX", 1040), ("NXT", 2040)):
            inputs = market_theme_shadow_inputs_from_ram(snapshot, replace(shadow_inputs(), leadership_venue=venue), window_seconds=10)
            limit, = inputs.upper_limits
            self.assertEqual(expected, limit.price)
        collector._apply_trade_source_approval({("005930", "KRX")}, now[0], reset_all=False)
        self.assertEqual({("005930", "KRX")}, set(collector._ram_stock_references))

    async def test_adapter_rejects_future_previous_day_and_unapproved_references(self):
        from copy import deepcopy
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import market_theme_shadow_inputs_from_ram
        now, hub, collector, monitors, runtime = self.fixture(0)
        self.reference(collector)
        snapshot = collector.ram_observation_snapshot(("005930",), seconds=20)
        cases = (
            {"available_at": now[0].timestamp() + 0.001}, {"trading_date": "2026-09-13"},
            {"market": "NXT"}, {"code": "000001"},
        )
        for changes in cases:
            value = deepcopy(snapshot)
            value["stock_references"][0].update(changes)
            result = market_theme_shadow_inputs_from_ram(value, shadow_inputs(), window_seconds=10)
            self.assertEqual((), result.upper_limits, changes)
        value = deepcopy(snapshot)
        value["approved_sources"] = ()
        self.assertEqual((), market_theme_shadow_inputs_from_ram(value, shadow_inputs(), window_seconds=10).upper_limits)

    async def test_observed_unknown_price_replaces_explicit_old_basis_without_fabricating_durability(self):
        from kiwoom_monitor.application.theme_leadership import UpperLimitReference
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import market_theme_shadow_inputs_from_ram
        now, hub, collector, monitors, runtime = self.fixture(0)
        supplied = UpperLimitReference("explicit-test-reference", "005930", 999, now[0].isoformat())
        context = replace(shadow_inputs(), upper_limits=(supplied,))
        snapshot = collector.ram_observation_snapshot(("005930",), seconds=20)
        self.assertEqual((supplied,), market_theme_shadow_inputs_from_ram(snapshot, context, window_seconds=10).upper_limits)
        for price in ("", "0"):
            self.reference(collector, price)
            snapshot = collector.ram_observation_snapshot(("005930",), seconds=20)
            self.assertEqual((), market_theme_shadow_inputs_from_ram(snapshot, context, window_seconds=10).upper_limits)
        self.reference(collector, "1040")
        result = market_theme_shadow_inputs_from_ram(collector.ram_observation_snapshot(("005930",), seconds=20), context, window_seconds=10)
        self.assertEqual(1040, result.upper_limits[0].price)
        self.assertNotEqual(supplied.input_ref, result.upper_limits[0].input_ref)
        self.assertFalse(snapshot["durable"])
        self.assertNotIn("accepted_sequence", snapshot["stock_references"][0])

    async def test_eight_themes_share_reference_read_and_h1_keep_independent_h2(self):
        now, hub, collector, _, _ = self.fixture(0)
        inputs = tuple(replace(shadow_inputs(), theme_membership=replace(shadow_inputs().theme_membership, theme_id=f"limit-theme-{index}"))
                       for index in range(8))
        monitors = tuple(MarketThemeShadowMonitor(theme_id=value.theme_membership.theme_id, leadership_venue="KRX") for value in inputs)
        runtime = MarketThemeShadowRuntime(hub, collector, tuple(zip(monitors, inputs)))
        self.addAsyncCleanup(runtime.close)
        with patch.object(collector, "ram_observation_snapshot", wraps=collector.ram_observation_snapshot) as reads, \
             patch.object(shadow_context, "compute_market_regime", wraps=shadow_context.compute_market_regime) as h1, \
             patch.object(shadow_context, "evaluate_theme_leadership", wraps=shadow_context.evaluate_theme_leadership) as h2, \
             patch("sqlite3.connect", side_effect=AssertionError("limit themes read DB")):
            self.reference(collector)
            requested = hub.requested_codes()
            await runtime.start()
            self.trades(collector, now)
            await self.wait_for(lambda: all(m.status["evaluations"] == 1 for m in monitors))
            self.assertEqual((1, 1, 8), (reads.call_count, h1.call_count, h2.call_count))
            self.assertEqual(requested, hub.requested_codes())
            self.assertEqual(8, len({m.last_result["revision_id"] for m in monitors}))
            refs = {m._last_offered.upper_limits[0].input_ref for m in monitors}
            self.assertEqual(1, len(refs))
            self.assertTrue(all(m.last_result["leadership"]["inferences"]["limit_state"] == "NEAR" for m in monitors))
            await runtime.close()

    async def test_close_drains_frozen_reference_cut_despite_late_reference_and_repeated_cancel(self):
        now, hub, collector, monitors, runtime = self.fixture(cleanup=False)
        entered, release = threading.Event(), threading.Event()
        def evaluate(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("reference close calculation was not released")
            return evaluate_market_theme_shadow_batch(*args, **kwargs)
        with patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow_batch", side_effect=evaluate):
            closer = None
            try:
                self.reference(collector)
                await runtime.start()
                self.trades(collector, now)
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                self.reference(collector, "1030")
                closer = asyncio.create_task(runtime.close())
                await self.wait_for(lambda: runtime._closing)
                self.reference(collector, "1200")
                closer.cancel()
                await asyncio.sleep(0.01)
                closer.cancel()
                self.assertFalse(closer.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await closer
            finally:
                release.set()
                await runtime.close()
                if closer:
                    await asyncio.gather(closer, return_exceptions=True)
        self.assertEqual("STOPPED", runtime.status["state"])
        self.assertEqual(1030, monitors[0]._last_offered.upper_limits[0].price)
        self.assertEqual("TOUCHED", monitors[0].last_result["leadership"]["inferences"]["limit_state"])
        self.assertEqual(0, hub.client_count)


class CrossThemeH1SharingTests(unittest.IsolatedAsyncioTestCase):
    def minute_inputs(self, value):
        from tests.unit.test_market_research_features import MinuteMarketCandidateTests
        helper=MinuteMarketCandidateTests();raw=helper.inputs()
        value=self.price_inputs(value)
        cut=datetime.fromisoformat(value.decision_time)
        delta=(cut-timedelta(seconds=2)).replace(second=0,microsecond=0)-(helper.cut-timedelta(seconds=2)).replace(second=0,microsecond=0)
        rows=[]
        for row in raw.rows:
            metadata=row['metadata'];at=datetime.fromisoformat(metadata['effective_at'])+delta
            local=at.astimezone(timezone(timedelta(hours=9)))
            rows.append({**row,'trading_date':local.date().isoformat(),'minute':local.strftime('%H:%M'),
                'updated_at':row['updated_at']+delta.total_seconds(),
                'metadata':{**metadata,'effective_at':at.isoformat(),
                    'available_at':(datetime.fromisoformat(metadata['available_at'])+delta).isoformat()}})
        raw=replace(raw,universe_available_at=cut.isoformat(),rows=tuple(rows))
        return replace(value,market_primary_timeframe='1m',market_minute_trade_inputs=raw)

    async def test_minute_primary_shared_and_second_failure_cannot_block_market_candidate(self):
        for count in (2,8):
            values=tuple(self.minute_inputs(self.observed_inputs(i)) for i in range(count))
            monitors=tuple(self.monitor(v) for v in values)
            expected=tuple(evaluate_market_theme_shadow(v) for v in values)
            for m,v in zip(monitors,values):await m.offer_inputs(v)
            with patch.object(shadow_context,'compute_minute_trade_candidates',wraps=shadow_context.compute_minute_trade_candidates) as minutes, \
                 patch.object(shadow_context,'compute_price_trade_candidates',side_effect=OSError('auxiliary seconds unavailable')) as seconds:
                report=await run_market_theme_shadow_monitors_once(monitors,include_market_changes=True)
            self.assertEqual((1,1),(minutes.call_count,seconds.call_count));self.assertEqual(expected,report['results'])
            observed=monitors[0]._market_changes_receipt[1]
            self.assertEqual('RETAINED',observed['price_trade_leadership']['state'])
            self.assertEqual('CONFIRMED',observed['price_trade_leadership']['five_minute_confirmation']['status'])
            self.assertIsNone(observed['second_trade_auxiliary'])

    async def test_minute_failure_keeps_four_thesis_policies_and_protective_exit(self):
        from tests.unit import test_entry_thesis as fixtures
        fixture=fixtures.StockThesisShadowTests();fixture.setUp()
        value=self.minute_inputs(fixture.weak_inputs())
        requests=tuple(fixture.request(p) for p in ('PRICE_ONLY','IMMEDIATE_EXIT','REDUCE_ON_RISK','CONFIRM_THEN_EXIT'))
        monitors=tuple(self.monitor(value,stock_thesis=r,leadership_parameters=fixtures.PARAMETERS) for r in requests)
        expected=tuple(evaluate_stock_thesis_shadow(value,r,leadership_parameters=fixtures.PARAMETERS) for r in requests)
        for m in monitors:await m.offer_inputs(value)
        with patch.object(shadow_context,'compute_minute_trade_candidates',side_effect=OSError('minute failure')):
            report=await run_market_theme_shadow_monitors_once(monitors,include_market_changes=True)
        self.assertEqual(expected,report['results']);self.assertEqual('EXIT',report['results'][1]['action'])

    async def test_native_minutes_loaded_once_for_eight_consumers_and_independent_expiry(self):
        helper=CollectorRamShadowRuntimeTests()
        now,hub,collector,monitors,runtime=helper.fixture(8,cleanup=False);self.addAsyncCleanup(runtime.close)
        value=self.minute_inputs(self.observed_inputs(0))
        value=replace(value,theme_membership=replace(value.theme_membership,theme_id='반도체'))
        runtime._bindings=tuple((m,value) for m in monitors)
        monitors[-1]._leadership_parameters=replace(monitors[-1]._leadership_parameters,window_seconds=20,max_data_age_seconds=1)
        runtime._retention=40
        collector._store=Mock()
        rows=value.market_minute_trade_inputs.rows
        async def load(code,day,venue):return [{**row,'metadata':dict(row['metadata'])} for row in rows if row['code']==code and row['market']==venue]
        with patch.object(collector,'load_live_minute_bars',side_effect=load) as read, \
             patch('kiwoom_monitor.central_server.market_theme_shadow_monitor.compute_minute_trade_candidates',wraps=shadow_context.compute_minute_trade_candidates) as calculate:
            await runtime.start();helper.trades(collector,now)
            await helper.wait_for(lambda:all(m.status['evaluations']==1 and m._market_changes_receipt is not None
                and m._market_changes_receipt[1]['price_trade_leadership']['state']=='RETAINED' for m in monitors))
            self.assertEqual((20,1),(read.await_count,calculate.call_count))
            page=runtime.latest_results;ref,observed=next(iter(page['market_observed_changes'].items()))
            self.assertEqual('RETAINED',observed['price_trade_leadership']['state'])
            now[0]+=timedelta(seconds=4)
            for _ in range(100):page=runtime.latest_results
            self.assertEqual('RETAINED',page['market_observed_changes'][ref]['price_trade_leadership']['state'])
            self.assertEqual((20,1),(read.await_count,calculate.call_count))
            cached,errors=await runtime._market_minutes({'decision_time':now[0].isoformat()})
            sort_rows=lambda values:sorted(values,key=lambda row:(row['code'],row['minute']))
            self.assertEqual(sort_rows(rows),sort_rows(cached));self.assertEqual({},errors)
            self.assertEqual(20,read.await_count)
            collector._save_success_at['minute_finalizations']='native-new-close'
            await runtime._market_minutes({'decision_time':now[0].isoformat()})
            self.assertEqual(40,read.await_count)
            now[0]+=timedelta(seconds=120)
            expired=runtime.latest_results['market_observed_changes'][ref]['price_trade_leadership']
            self.assertEqual('UNKNOWN',expired['state'])
            self.assertTrue(all(not frame['current_candidates'] for frame in expired['timeframes'].values()))
            self.assertEqual('RETAINED',monitors[0]._market_changes_receipt[1]['price_trade_leadership']['state'])
        await runtime.close();self.assertIsNone(runtime._market_minute_cache)

    async def test_minute_primary_uses_existing_sqlite_reader_without_writes_or_consumer_scaling(self):
        from tests.unit.test_market_research_features import MinuteMarketCandidateTests
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.central_server.market_observations import minute_bar_observation
        from kiwoom_monitor.domain.market_data_contract import ObservationOrigin,DataCompleteness,DataValueKind
        raw=MinuteMarketCandidateTests().inputs();cut=MinuteMarketCandidateTests.cut
        core=self.observed_inputs(0);delta=cut-datetime.fromisoformat(core.decision_time)
        core=replace(core,decision_time=cut.isoformat(),market_frames=tuple(replace(f,
            available_at=(datetime.fromisoformat(f.available_at)+delta).isoformat(),trading_date='2026-10-13') for f in core.market_frames))
        value=self.minute_inputs(core);value=replace(value,theme_membership=replace(value.theme_membership,theme_id='반도체'))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'native-minute.sqlite3';store=SQLiteQueryStore(path);store.initialize()
            observations=[]
            for row in raw.rows:
                value_row={key:value for key,value in row.items() if key!='metadata'}
                observation=minute_bar_observation(value_row,origin=ObservationOrigin.REALTIME,
                    completeness=DataCompleteness.COMPLETE,source='kiwoom-websocket-0B',value_kind=DataValueKind.ACTUAL)
                observations.append((row['trading_date']+'T'+row['minute'],observation))
            store.save_minute_bars([{key:value for key,value in row.items() if key!='metadata'} for row in raw.rows],observations=observations)
            before=path.read_bytes()
            helper=CollectorRamShadowRuntimeTests()
            now,hub,collector,monitors,runtime=helper.fixture(8,store=store,cleanup=False);now[0]=cut
            runtime._bindings=tuple((m,value) for m in monitors)
            try:
                with patch.object(store,'load_minute_bars',wraps=store.load_minute_bars) as read, \
                     patch.object(store,'save_minute_bars',side_effect=AssertionError('minute observer wrote bars')):
                    await runtime.start();await helper.wait_for(lambda:all(m.status['evaluations']==1 and m._market_changes_receipt is not None
                        and m._market_changes_receipt[1]['price_trade_leadership']['state']=='RETAINED' for m in monitors))
                    observed=next(iter(runtime.latest_results['market_observed_changes'].values()))
                    self.assertEqual('RETAINED',observed['price_trade_leadership']['state'])
                    self.assertEqual('CONFIRMED',observed['price_trade_leadership']['five_minute_confirmation']['status'])
                    self.assertEqual(20,read.call_count)
                    for _ in range(100):runtime.latest_results
                    self.assertEqual(20,read.call_count);self.assertEqual(before,path.read_bytes())
                    await runtime.close();self.assertEqual(20,read.call_count)
            finally:await runtime.close();store.close()

    async def test_close_during_native_minute_read_drains_worker_before_returning(self):
        helper=CollectorRamShadowRuntimeTests();now,hub,collector,monitors,runtime=helper.fixture(2,cleanup=False)
        self.addAsyncCleanup(runtime.close)
        value=self.minute_inputs(self.observed_inputs(0));value=replace(value,theme_membership=replace(value.theme_membership,theme_id='반도체'))
        runtime._bindings=tuple((m,value) for m in monitors);now[0]=datetime.fromisoformat(value.decision_time)
        rows=value.market_minute_trade_inputs.rows;entered,release=threading.Event(),threading.Event();calls=[]
        class Store:
            def load_minute_bars(self,code,day,venue,**kwargs):
                calls.append(code)
                if len(calls)==1:
                    entered.set()
                    if not release.wait(5):raise TimeoutError('test minute reader not released')
                return [{**r,'metadata':dict(r['metadata'])} for r in rows if r['code']==code]
        collector._store=Store();closer=None
        try:
            await runtime.start();self.assertTrue(await asyncio.to_thread(entered.wait,2))
            # The minute source remains blocked while existing core H1/H2 has
            # already published; this I/O cannot delay a protective proposal.
            await helper.wait_for(lambda:all(m.status['evaluations']==1 for m in monitors))
            self.assertFalse(release.is_set())
            now[0]+=timedelta(seconds=1)
            collector._publish_parsed({'trnm':'REAL','data':[{'type':'0B','item':'005930',
                'values':{'10':'1000','13':'100','15':'2','20':now[0].strftime('%H%M%S')}}]})
            await helper.wait_for(lambda:all(m.status['evaluations']==2 for m in monitors))
            self.assertFalse(release.is_set())
            closer=asyncio.create_task(runtime.close());await asyncio.sleep(.02);closer.cancel()
            self.assertFalse(closer.done());release.set()
            with self.assertRaises(asyncio.CancelledError):await closer
            count=len(calls);await asyncio.sleep(.02)
            self.assertEqual(count,len(calls));self.assertEqual('STOPPED',runtime.status['state'])
            self.assertEqual(0,hub.client_count);self.assertIsNone(runtime._market_minute_cache)
        finally:
            release.set()
            if closer:await asyncio.gather(closer,return_exceptions=True)

    def observed_inputs(self, index):
        from kiwoom_monitor.application.market_research_features import MarketMember
        value = self.inputs(index)
        frames = tuple(replace(frame, theme_revision_id="", members=tuple(
            MarketMember(f"{code:06d}", 1900 if code == 1 else 100, 200, ())
            for code in range(1, 21))) for frame in value.market_frames)
        return replace(value, market_frames=frames, breakout_evidence=None)

    def price_inputs(self, value, *, candidate='000001'):
        from tests.unit.test_market_research_features import PriceTradeCandidateTests
        from kiwoom_monitor.application.market_theme_shadow import MarketPriceTradeShadowInputs
        helper = PriceTradeCandidateTests()
        raw = helper.inputs(current=candidate)
        cut = datetime.fromisoformat(value.decision_time)
        delta = cut - helper.cut
        raw = replace(raw, universe_available_at=(datetime.fromisoformat(raw.universe_available_at)+delta).isoformat(),
            points=tuple(replace(p, observed_at=(datetime.fromisoformat(p.observed_at)+delta).isoformat(),
                available_at=(datetime.fromisoformat(p.available_at)+delta).isoformat()) for p in raw.points))
        return MarketPriceTradeShadowInputs(**{**vars(value), 'market_price_trade_inputs':raw})

    async def test_price_candidates_shared_once_independent_of_lookup_and_core(self):
        from kiwoom_monitor.application import research_factors as factors_module
        for count in (2,8):
            values = tuple(self.price_inputs(self.observed_inputs(i)) for i in range(count))
            monitors = tuple(self.monitor(v) for v in values)
            expected = tuple(evaluate_market_theme_shadow(v) for v in values)
            for monitor,value in zip(monitors,values): await monitor.offer_inputs(value)
            with patch.object(shadow_context,'compute_price_trade_candidates',wraps=shadow_context.compute_price_trade_candidates) as price, \
                 patch.object(factors_module,'compute_market_research_features',wraps=factors_module.compute_market_research_features) as h1:
                report = await run_market_theme_shadow_monitors_once(monitors,include_market_changes=True)
            self.assertEqual((1,1),(price.call_count,h1.call_count))
            self.assertEqual(expected,report['results'])
            observation=monitors[0]._market_changes_receipt[1]
            self.assertEqual('RETAINED',observation['price_trade_leadership']['state'])
            changed=replace(values[0],market_frames=(*values[0].market_frames[:-1],replace(values[0].market_frames[-1],
                members=(values[0].market_frames[-1].members[1],values[0].market_frames[-1].members[0],*values[0].market_frames[-1].members[2:]))))
            other=self.monitor(changed); await other.offer_inputs(changed)
            await run_market_theme_shadow_monitors_once((other,),include_market_changes=True)
            self.assertTrue(other._market_changes_receipt[1]['rank_price_trade_relation']['rank_changed_candidate_retained'])
            replaced=self.price_inputs(self.observed_inputs(0),candidate='000002')
            other=self.monitor(replaced); await other.offer_inputs(replaced)
            await run_market_theme_shadow_monitors_once((other,),include_market_changes=True)
            self.assertTrue(other._market_changes_receipt[1]['rank_price_trade_relation']['candidate_changed_rank_retained'])

    async def test_price_failure_preserves_all_four_protective_policies(self):
        from tests.unit import test_entry_thesis as fixtures
        fixture=fixtures.StockThesisShadowTests(); fixture.setUp()
        inputs=self.price_inputs(fixture.weak_inputs())
        requests=tuple(fixture.request(p) for p in ('PRICE_ONLY','IMMEDIATE_EXIT','REDUCE_ON_RISK','CONFIRM_THEN_EXIT'))
        monitors=tuple(self.monitor(inputs,stock_thesis=r,leadership_parameters=fixtures.PARAMETERS) for r in requests)
        expected=tuple(evaluate_stock_thesis_shadow(inputs,r,leadership_parameters=fixtures.PARAMETERS) for r in requests)
        for m in monitors: await m.offer_inputs(inputs)
        with patch.object(shadow_context,'compute_price_trade_candidates',side_effect=OSError('isolated')) as price:
            report=await run_market_theme_shadow_monitors_once(monitors,include_market_changes=True)
        self.assertEqual(1,price.call_count)
        self.assertEqual(expected,report['results']); self.assertEqual('EXIT',report['results'][1]['action'])
        self.assertEqual('UNAVAILABLE',monitors[0]._market_changes_receipt[1]['price_trade_leadership']['quality']['status'])

    async def test_price_expiry_does_not_expire_lookup_or_mutate_receipt(self):
        helper=CollectorRamShadowRuntimeTests()
        now,hub,collector,monitors,runtime=helper.fixture(2,cleanup=False)
        self.addAsyncCleanup(runtime.close)
        value=self.price_inputs(self.observed_inputs(0))
        value=replace(value,theme_membership=replace(value.theme_membership,theme_id='반도체'))
        for monitor in monitors: await monitor.offer_inputs(value)
        await run_market_theme_shadow_monitors_once(monitors,include_market_changes=True)
        now[0]=datetime.fromisoformat(value.decision_time)+timedelta(seconds=3)
        with patch.object(collector,'ram_observation_snapshot',side_effect=AssertionError('read on projection')):
            for _ in range(100): page=runtime.latest_results
        observed=next(iter(page['market_observed_changes'].values()))
        self.assertEqual('UNKNOWN',observed['price_trade_leadership']['state'])
        self.assertEqual('STALE',observed['price_trade_leadership']['quality']['status'])
        self.assertEqual('RETAINED',observed['lookup_change']['state'])
        self.assertEqual('RETAINED',monitors[0]._market_changes_receipt[1]['price_trade_leadership']['state'])

    async def test_observed_changes_share_current_features_and_preserve_all_v1_results(self):
        from kiwoom_monitor.application import market_research_features as features_module
        from kiwoom_monitor.application import research_factors as factors_module
        for count in (2, 8):
            values = tuple(self.observed_inputs(index) for index in range(count))
            expected = tuple(evaluate_market_theme_shadow(value) for value in values)
            monitors = tuple(self.monitor(value) for value in values)
            for monitor, value in zip(monitors, values):
                await monitor.offer_inputs(value)
            with patch.object(factors_module, "compute_market_research_features", wraps=factors_module.compute_market_research_features) as current, \
                 patch.object(features_module, "compute_market_research_features", wraps=features_module.compute_market_research_features) as previous, \
                 patch.object(shadow_context, "compute_market_context_changes", wraps=shadow_context.compute_market_context_changes) as changes, \
                 patch("sqlite3.connect", side_effect=AssertionError("observation read DB")), \
                 patch("psycopg.connect", side_effect=AssertionError("observation read DB")):
                report = await run_market_theme_shadow_monitors_once(monitors, include_market_changes=True)
            self.assertEqual((1, 1, 1), (current.call_count, previous.call_count, changes.call_count))
            self.assertEqual(expected, report["results"])
            receipts = [monitor._market_changes_receipt[1] for monitor in monitors]
            self.assertTrue(all(receipt == receipts[0] for receipt in receipts))
            self.assertEqual("LEADER_CONCENTRATION", receipts[0]["pattern"])
            self.assertEqual("COMPLETE", receipts[0]["quality"]["status"])
            self.assertEqual("UNKNOWN", receipts[0]["classification"]["market_type"])
            self.assertFalse(receipts[0]["order_authorized"])
            import hashlib
            body = {key: value for key, value in receipts[0].items() if key != "revision_id"}
            digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            self.assertEqual("market-observed-changes:" + digest, receipts[0]["revision_id"])
            receipts[0]["quality"]["status"] = "MUTATED"
            self.assertEqual("COMPLETE", receipts[1]["quality"]["status"])

    async def test_auxiliary_observation_failure_cannot_change_existing_policy_or_pending(self):
        values = tuple(self.observed_inputs(index) for index in range(2))
        monitors = tuple(self.monitor(value) for value in values)
        expected = tuple(evaluate_market_theme_shadow(value) for value in values)
        for monitor, value in zip(monitors, values):
            await monitor.offer_inputs(value)
        with patch.object(shadow_context, "compute_market_context_changes", side_effect=OSError("isolated observation failure")) as changes:
            report = await run_market_theme_shadow_monitors_once(monitors, include_market_changes=True)
        self.assertEqual(1, changes.call_count)
        self.assertEqual(expected, report["results"])
        self.assertEqual({}, report["errors"])
        self.assertTrue(all(monitor.status["pending_snapshots"] == 0 for monitor in monitors))
        self.assertTrue(all(monitor._market_changes_receipt[1]["quality"]["status"] == "UNAVAILABLE" for monitor in monitors))
        # A later round must recompute rather than retaining the failed cache.
        for monitor, value in zip(monitors, values):
            await monitor.offer_inputs(replace(value, decision_time="2026-09-14T09:00:21+09:00"))
        with patch.object(shadow_context, "compute_market_context_changes", wraps=shadow_context.compute_market_context_changes) as changes:
            await run_market_theme_shadow_monitors_once(monitors, include_market_changes=True)
        self.assertEqual(1, changes.call_count)
        self.assertEqual("COMPLETE", monitors[0]._market_changes_receipt[1]["quality"]["status"])

    async def test_observation_reuse_keeps_full_scope_and_clears_disabled_receipts(self):
        base = self.observed_inputs(0)
        values = (base, self.observed_inputs(1),
            replace(self.observed_inputs(2), market_frames=tuple(replace(frame, index_return_bps=100.0) for frame in base.market_frames)))
        monitors = tuple(self.monitor(value) for value in values)
        for monitor, value in zip(monitors, values):
            await monitor.offer_inputs(value)
        with patch.object(shadow_context, "compute_market_context_changes", wraps=shadow_context.compute_market_context_changes) as changes:
            await run_market_theme_shadow_monitors_once(monitors, include_market_changes=True)
        self.assertEqual(2, changes.call_count)
        self.assertNotEqual(monitors[0]._market_changes_receipt[1]["revision_id"], monitors[2]._market_changes_receipt[1]["revision_id"])
        for monitor, value in zip(monitors, values):
            await monitor.offer_inputs(replace(value, decision_time="2026-09-14T09:00:21+09:00"))
        await run_market_theme_shadow_monitors_once(monitors)
        self.assertTrue(all(monitor._market_changes_receipt is None for monitor in monitors))

    async def test_observation_failure_keeps_four_thesis_policies_and_immediate_exit_identical(self):
        from tests.unit import test_entry_thesis as fixtures
        fixture = fixtures.StockThesisShadowTests(); fixture.setUp()
        inputs = fixture.weak_inputs()
        requests = tuple(fixture.request(policy) for policy in
            ("PRICE_ONLY", "IMMEDIATE_EXIT", "REDUCE_ON_RISK", "CONFIRM_THEN_EXIT"))
        monitors = tuple(self.monitor(inputs, stock_thesis=request, leadership_parameters=fixtures.PARAMETERS)
                         for request in requests)
        expected = tuple(evaluate_stock_thesis_shadow(inputs, request, leadership_parameters=fixtures.PARAMETERS)
                         for request in requests)
        for monitor in monitors:
            await monitor.offer_inputs(inputs)
        with patch.object(shadow_context, "compute_market_context_changes", side_effect=OSError("auxiliary failure")):
            report = await run_market_theme_shadow_monitors_once(monitors, include_market_changes=True)
        self.assertEqual(expected, report["results"])
        self.assertEqual("EXIT", report["results"][1]["action"])
        self.assertEqual({}, report["errors"])

    async def test_runtime_publishes_one_observation_and_expires_without_reads_or_recalculation(self):
        from kiwoom_monitor.central_server import market_theme_shadow_inputs as adapters
        helper = CollectorRamShadowRuntimeTests()
        now, hub, collector, monitors, runtime = helper.fixture(8, cleanup=False)
        self.addAsyncCleanup(runtime.close)
        value = self.observed_inputs(0)
        value = replace(value, theme_membership=replace(value.theme_membership, theme_id="반도체"))
        runtime._bindings = tuple((monitor, value) for monitor in monitors)
        with patch.object(shadow_context, "compute_market_context_changes", wraps=shadow_context.compute_market_context_changes) as changes, \
             patch.object(adapters, 'market_price_trade_inputs_from_ram', wraps=adapters.market_price_trade_inputs_from_ram) as price_inputs, \
             patch.object(shadow_context, 'compute_price_trade_candidates', wraps=shadow_context.compute_price_trade_candidates) as price, \
             patch.object(collector, "ram_observation_snapshot", wraps=collector.ram_observation_snapshot) as snapshots, \
             patch("sqlite3.connect", side_effect=AssertionError("observation read DB")), \
             patch("psycopg.connect", side_effect=AssertionError("observation read DB")):
            await runtime.start()
            helper.trades(collector, now)
            await helper.wait_for(lambda: all(monitor.status["evaluations"] == 1 for monitor in monitors))
            envelope = runtime.latest_results
            observations = envelope["market_observed_changes"]
            self.assertEqual(1, len(observations))
            ref, original = next(iter(observations.items()))
            self.assertEqual("LEADER_CONCENTRATION", original["pattern"])
            self.assertTrue(all(slot["market_observed_changes_ref"] == ref for slot in envelope["results"]))
            original_core = deepcopy(envelope["contexts"])
            now[0] += timedelta(seconds=120)
            for _ in range(100):
                latest = runtime.latest_results
            expired = latest["market_observed_changes"][ref]
            self.assertEqual((1, 1), (changes.call_count, snapshots.call_count))
            self.assertEqual((1,1),(price_inputs.call_count,price.call_count))
            self.assertEqual('UNKNOWN',original['price_trade_leadership']['state'])
            self.assertEqual(original_core, latest["contexts"])
            self.assertEqual("STALE", expired["quality"]["status"])
            self.assertEqual("UNKNOWN", expired["pattern"])
            self.assertEqual((), expired["candidates"])
            self.assertEqual(original["expires_at"], expired["expires_at"])
            self.assertEqual("LEADER_CONCENTRATION", monitors[0]._market_changes_receipt[1]["pattern"])
            self.assertFalse(latest["order_authorized"])
            await runtime.close()
            self.assertEqual(0, hub.client_count)

    async def test_large_observation_cannot_displace_existing_selected_record_or_add_writes(self):
        helper = CollectorRamShadowRuntimeTests()
        now, hub, collector, monitors, runtime = helper.fixture(1, cleanup=False)
        self.addAsyncCleanup(runtime.close)
        await monitors[0].offer_inputs(shadow_inputs())
        await run_market_theme_shadow_monitors_once(monitors, include_market_changes=True)
        original = monitors[0].last_result
        monitors[0]._market_changes_receipt[1]["input_refs"] = ("large-optional-" + "x" * (2 * 1024 * 1024),)
        store = Mock()
        runtime._record_store = store
        runtime._select_record()
        selected = runtime._record_pending[1]
        self.assertNotIn("market_observed_changes", selected)
        self.assertNotIn("market_observed_changes_ref", selected["results"][0])
        self.assertEqual(original, selected["contexts"][original["revision_id"]])
        runtime._select_record()
        await runtime.close()
        self.assertEqual(1, store.upsert_documents.call_count)

    def inputs(self, index, **changes):
        inputs = shadow_inputs()
        return replace(inputs, theme_membership=replace(inputs.theme_membership,
            theme_id=f"shared-market-theme-{index}"), **changes)

    def monitor(self, inputs, **parameters):
        monitor = MarketThemeShadowMonitor(theme_id=inputs.theme_membership.theme_id,
            leadership_venue=inputs.leadership_venue, **parameters)
        self.addAsyncCleanup(monitor.close)
        return monitor

    async def test_eight_themes_compute_h1_once_keep_h2_results_and_state_independent(self):
        values = tuple(self.inputs(index) for index in range(8))
        monitors = tuple(self.monitor(value) for value in values)
        expected = tuple(evaluate_market_theme_shadow(value) for value in values)
        for monitor, value in zip(monitors, values):
            await monitor.offer_inputs(value)
        with patch.object(shadow_context, "compute_market_regime", wraps=shadow_context.compute_market_regime) as h1, \
             patch.object(shadow_context, "evaluate_theme_leadership", wraps=shadow_context.evaluate_theme_leadership) as h2, \
             patch("sqlite3.connect", side_effect=AssertionError("H1 share read DB")), \
             patch("psycopg.connect", side_effect=AssertionError("H1 share read DB")):
            report = await run_market_theme_shadow_monitors_once(monitors)
            self.assertEqual({}, report["errors"])
            self.assertEqual(expected, report["results"])
            self.assertEqual(1, h1.call_count)
            self.assertEqual(8, h2.call_count)
        self.assertEqual(8, len({result["revision_id"] for result in report["results"]}))
        self.assertEqual([value.theme_membership.theme_id for value in values], [m._state.theme_id for m in monitors])
        first_market = monitors[0]._result["market"]
        first_market["quality"]["status"] = "MUTATED"
        self.assertTrue(all(m.last_result["market"]["quality"]["status"] != "MUTATED" for m in monitors[1:]))

    async def test_immutable_frames_are_shared_but_root_and_mutable_h2_inputs_are_copied(self):
        first = self.inputs(0)
        points = list(first.second_points)
        first = replace(first, second_points=points)
        second = self.inputs(1)
        # Both source scopes use the same immutable frame tuple.
        second = replace(second, market_frames=first.market_frames)
        monitors = tuple(self.monitor(value) for value in (first, second))
        for monitor, value in zip(monitors, (first, second)):
            await monitor.offer_inputs(value)
            self.assertIsNot(value, monitor._pending)
            self.assertIs(value.market_frames, monitor._pending.market_frames)
        before = len(monitors[0]._pending.second_points)
        points.clear()
        self.assertEqual(before, len(monitors[0]._pending.second_points))
        helper = CollectorRamShadowRuntimeTests()
        _, hub, collector, _, _ = helper.fixture(0, cleanup=False)
        runtime = MarketThemeShadowRuntime(hub, collector, tuple(zip(monitors, (first, second))))
        self.addAsyncCleanup(runtime.close)
        self.assertIs(first.market_frames, runtime._bindings[0][1].market_frames)
        self.assertIs(first.market_frames, runtime._bindings[1][1].market_frames)
        await runtime.offer_context(first)
        self.assertIs(first.market_frames, runtime._bindings[0][1].market_frames)

    async def test_lists_inside_frozen_market_frames_keep_the_original_snapshot_isolation(self):
        first = self.inputs(0)
        members = list(first.market_frames[0].members)
        frame = replace(first.market_frames[0], members=members)
        value = replace(first, market_frames=(frame, *first.market_frames[1:]))
        monitor = self.monitor(value)
        await monitor.offer_inputs(value)
        self.assertIsNot(value.market_frames, monitor._pending.market_frames)
        members.clear()
        self.assertEqual(2, len(monitor._pending.market_frames[0].members))
        other = self.inputs(1)
        themes = list(other.market_frames[0].members[0].themes)
        member = replace(other.market_frames[0].members[0], themes=themes)
        frame = replace(other.market_frames[0], members=(member, *other.market_frames[0].members[1:]))
        value = replace(other, market_frames=(frame, *other.market_frames[1:]))
        monitor = self.monitor(value)
        await monitor.offer_inputs(value)
        themes.clear()
        self.assertTrue(monitor._pending.market_frames[0].members[0].themes)

    async def test_equal_numeric_values_with_different_types_keep_distinct_market_revisions(self):
        base = self.inputs(0)
        integer_frames = tuple(replace(frame, index_return_bps=100) for frame in base.market_frames)
        float_frames = tuple(replace(frame, index_return_bps=100.0) for frame in base.market_frames)
        values = (replace(base, market_frames=integer_frames), self.inputs(1, market_frames=float_frames))
        monitors = tuple(self.monitor(value) for value in values)
        expected = tuple(evaluate_market_theme_shadow(value) for value in values)
        for monitor, value in zip(monitors, values):
            await monitor.offer_inputs(value)
        with patch.object(shadow_context, "compute_market_regime", wraps=shadow_context.compute_market_regime) as h1:
            report = await run_market_theme_shadow_monitors_once(monitors)
        self.assertEqual(expected, report["results"])
        self.assertEqual(2, h1.call_count)
        self.assertIs(float, type(report["results"][1]["market"]["facts"]["index_return_bps"]))

    async def test_full_context_grouping_also_preserves_numeric_encoding_types(self):
        base = self.inputs(0)
        values = (replace(base, market_frames=tuple(replace(frame, index_return_bps=100) for frame in base.market_frames)),
                  replace(base, market_frames=tuple(replace(frame, index_return_bps=100.0) for frame in base.market_frames)))
        monitors = tuple(self.monitor(value) for value in values)
        expected = tuple(evaluate_market_theme_shadow(value) for value in values)
        for monitor, value in zip(monitors, values):
            await monitor.offer_inputs(value)
        report = await run_market_theme_shadow_monitors_once(monitors)
        self.assertEqual(expected, report["results"])

    async def test_market_cut_scope_content_lineage_and_parameters_partition_reuse(self):
        base = self.inputs(0)
        frame = base.market_frames[-1]
        changed_member = replace(frame.members[0], trade_value_million_won=101)
        variants = (
            (base, {}), (self.inputs(1), {}),
            (self.inputs(2, decision_time="2026-09-14T09:00:21+09:00"), {}),
            (self.inputs(3, market_venue="NXT", market_frames=tuple(replace(f, venue="NXT") for f in base.market_frames)), {}),
            (self.inputs(4, market_window="other", market_frames=tuple(replace(f, window="other") for f in base.market_frames)), {}),
            (self.inputs(5, market_frames=(*base.market_frames[:-1], replace(frame, revision_id="changed-lineage"))), {}),
            (self.inputs(6, market_frames=(*base.market_frames[:-1], replace(frame, members=(changed_member, *frame.members[1:])))), {}),
            (self.inputs(7, breakout_evidence=replace(base.breakout_evidence, input_ref="other-outcomes-ref")), {}),
            (self.inputs(8, breakout_evidence=replace(base.breakout_evidence, summary=shadow_context.MatureBreakoutSummary(4, 2, 2))), {}),
            (self.inputs(9), {"market_parameters": replace(shadow_context.MarketFeatureParameters(), top_k=2)}),
        )
        monitors = tuple(self.monitor(value, **params) for value, params in variants)
        expected = tuple(evaluate_market_theme_shadow(value, **params) for value, params in variants)
        for monitor, (value, _) in zip(monitors, variants):
            await monitor.offer_inputs(value)
        with patch.object(shadow_context, "compute_market_regime", wraps=shadow_context.compute_market_regime) as h1:
            report = await run_market_theme_shadow_monitors_once(monitors)
        self.assertEqual(expected, report["results"])
        self.assertEqual({}, report["errors"])
        self.assertEqual(9, h1.call_count)
        self.assertNotEqual(report["results"][0]["market"], report["results"][6]["market"])

    async def test_h2_state_quality_parameters_and_upper_limits_do_not_partition_h1(self):
        from kiwoom_monitor.application.theme_leadership import ThemeLeadershipState, UpperLimitReference
        previous = self.inputs(1)
        state = ThemeLeadershipState(**evaluate_market_theme_shadow(previous)["next_leadership_state"])
        values = (self.inputs(0), replace(previous, previous_leadership_state=state),
            self.inputs(2, continuity_status="PARTIAL"), self.inputs(3),
            self.inputs(4, upper_limits=(UpperLimitReference(code="005930", price=1300,
                available_at="2026-09-14T09:00:00+09:00", input_ref="cap-ref"),)))
        parameters = ({}, {}, {}, {"leadership_parameters": replace(shadow_context.ThemeLeadershipParameters(), price_exit_bps=600)}, {})
        monitors = tuple(self.monitor(value, **params) for value, params in zip(values, parameters))
        expected = tuple(evaluate_market_theme_shadow(value, **params) for value, params in zip(values, parameters))
        for monitor, value in zip(monitors, values):
            await monitor.offer_inputs(value)
        with patch.object(shadow_context, "compute_market_regime", wraps=shadow_context.compute_market_regime) as h1, \
             patch.object(shadow_context, "evaluate_theme_leadership", wraps=shadow_context.evaluate_theme_leadership) as h2:
            report = await run_market_theme_shadow_monitors_once(monitors)
        self.assertEqual(expected, report["results"])
        self.assertEqual((1, 5), (h1.call_count, h2.call_count))

    async def test_h1_failure_shared_once_other_market_progresses_and_retry_recomputes(self):
        values = tuple(self.inputs(index) for index in range(3))
        monitors = tuple(self.monitor(value, **({"market_parameters": replace(shadow_context.MarketFeatureParameters(), top_k=2)} if index == 2 else {}))
                         for index, value in enumerate(values))
        for monitor, value in zip(monitors, values):
            await monitor.offer_inputs(value)
        await run_market_theme_shadow_monitors_once(monitors)
        before = tuple((m.last_result, m._state) for m in monitors[:2])
        for monitor, value in zip(monitors, values):
            await monitor.offer_inputs(replace(value, decision_time="2026-09-14T09:00:21+09:00"))
        original = shadow_context.compute_market_regime
        def calculate(frames, summary, parameters, **kwargs):
            if parameters.top_k == 5:
                raise OSError("one shared H1 failure")
            return original(frames, summary, parameters, **kwargs)
        with patch.object(shadow_context, "compute_market_regime", side_effect=calculate) as h1:
            report = await run_market_theme_shadow_monitors_once(monitors)
            self.assertEqual(2, h1.call_count)
        self.assertEqual([2], report["evaluated"])
        self.assertEqual({0, 1}, set(report["errors"]))
        self.assertEqual(before, tuple((m.last_result, m._state) for m in monitors[:2]))
        self.assertEqual([1, 1, 0], [m.status["pending_snapshots"] for m in monitors])
        with patch.object(shadow_context, "compute_market_regime", wraps=original) as h1:
            report = await run_market_theme_shadow_monitors_once(monitors)
            self.assertEqual(1, h1.call_count)
        self.assertEqual([0, 1], report["evaluated"])
        self.assertEqual({}, report["errors"])

    async def test_h2_failure_keeps_shared_h1_healthy_and_no_cache_survives_round(self):
        values = tuple(self.inputs(index) for index in range(3))
        monitors = tuple(self.monitor(value) for value in values)
        original = shadow_context.evaluate_theme_leadership
        def leadership(membership, *args, **kwargs):
            if membership.theme_id == values[1].theme_membership.theme_id:
                raise OSError("one independent H2 failure")
            return original(membership, *args, **kwargs)
        captured = []
        def batch(*args, **kwargs):
            captured.append(kwargs["_market_cache"])
            return evaluate_market_theme_shadow_batch(*args, **kwargs)
        for monitor, value in zip(monitors, values):
            await monitor.offer_inputs(value)
        with patch.object(shadow_context, "compute_market_regime", wraps=shadow_context.compute_market_regime) as h1, \
             patch.object(shadow_context, "evaluate_theme_leadership", side_effect=leadership), \
             patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow_batch", side_effect=batch):
            report = await run_market_theme_shadow_monitors_once(monitors)
            self.assertEqual(1, h1.call_count)
        self.assertEqual([0, 2], report["evaluated"])
        self.assertEqual({1}, set(report["errors"]))
        self.assertTrue(captured and all(value == [] for value in captured))
        cache = captured[0]
        with patch.object(shadow_context, "compute_market_regime", wraps=shadow_context.compute_market_regime) as h1, \
             patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow_batch", side_effect=batch):
            report = await run_market_theme_shadow_monitors_once(monitors)
            self.assertEqual(1, h1.call_count)
        self.assertEqual([1], report["evaluated"])
        self.assertIsNot(cache, captured[-1])
        self.assertEqual([], captured[-1])

    async def test_cancelled_cross_theme_round_drains_once_and_close_consumes_new_pending(self):
        values = tuple(self.inputs(index) for index in range(3))
        monitors = tuple(self.monitor(value) for value in values)
        entered, release = threading.Event(), threading.Event()
        original = shadow_context.compute_market_regime
        captured = []
        def calculate(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("H1 work not released")
            return original(*args, **kwargs)
        def batch(*args, **kwargs):
            captured.append(kwargs["_market_cache"])
            return evaluate_market_theme_shadow_batch(*args, **kwargs)
        for monitor, value in zip(monitors, values):
            await monitor.offer_inputs(value)
        task = closer = None
        with patch.object(shadow_context, "compute_market_regime", side_effect=calculate) as h1, \
             patch("kiwoom_monitor.central_server.market_theme_shadow_monitor.evaluate_market_theme_shadow_batch", side_effect=batch):
            try:
                task = asyncio.create_task(run_market_theme_shadow_monitors_once(monitors))
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                await monitors[0].offer_inputs(replace(values[0], decision_time="2026-09-14T09:00:22+09:00"))
                task.cancel(); await asyncio.sleep(0.01); task.cancel()
                closer = asyncio.create_task(monitors[0].close())
                await asyncio.sleep(0.01)
                self.assertFalse(task.done()); self.assertFalse(closer.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await closer
                self.assertEqual(2, h1.call_count)  # shared cut once + final standalone cut
                self.assertEqual([2, 1, 1], [m.status["evaluations"] for m in monitors])
                self.assertTrue(all(value == [] for value in captured))
                self.assertTrue(all(not m._shared_running for m in monitors))
            finally:
                release.set()
                await asyncio.gather(*(item for item in (task, closer) if item), return_exceptions=True)

    async def test_existing_four_thesis_policies_and_other_theme_share_only_h1(self):
        fixture = thesis_fixtures.StockThesisShadowTests(); fixture.setUp()
        inputs = fixture.weak_inputs()
        other = replace(inputs, theme_membership=replace(inputs.theme_membership, theme_id="another-theme"), previous_leadership_state=None)
        requests = tuple(fixture.request(policy) for policy in ("PRICE_ONLY", "IMMEDIATE_EXIT", "REDUCE_ON_RISK", "CONFIRM_THEN_EXIT"))
        monitors = tuple(self.monitor(inputs, stock_thesis=request, leadership_parameters=thesis_fixtures.PARAMETERS) for request in requests)
        monitors += (self.monitor(other, leadership_parameters=thesis_fixtures.PARAMETERS),)
        expected = tuple(evaluate_stock_thesis_shadow(inputs, request, leadership_parameters=thesis_fixtures.PARAMETERS) for request in requests)
        expected += (evaluate_market_theme_shadow(other, leadership_parameters=thesis_fixtures.PARAMETERS),)
        for monitor, value in zip(monitors, (*([inputs] * 4), other)):
            await monitor.offer_inputs(value)
        with patch.object(shadow_context, "compute_market_regime", wraps=shadow_context.compute_market_regime) as h1, \
             patch.object(shadow_context, "evaluate_theme_leadership", wraps=shadow_context.evaluate_theme_leadership) as h2:
            report = await run_market_theme_shadow_monitors_once(monitors)
        self.assertEqual(expected, report["results"])
        self.assertEqual((1, 2), (h1.call_count, h2.call_count))
        self.assertEqual(["HOLD", "EXIT", "REDUCE", "WAIT"], [result["action"] for result in report["results"][:4]])

    async def test_cross_theme_collector_snapshot_and_native_writes_are_common(self):
        helper = CollectorRamShadowRuntimeTests()
        reports, payloads = [], []
        with tempfile.TemporaryDirectory() as directory:
            for count, record in ((0, False), (8, False), (8, True)):
                store = SQLiteQueryStore(Path(directory) / f"cross-theme-{count}-{record}.sqlite3")
                store.initialize()
                now, hub, collector, _, _ = helper.fixture(0, store, cleanup=False)
                values = tuple(self.inputs(index) for index in range(count))
                monitors = tuple(MarketThemeShadowMonitor(theme_id=value.theme_membership.theme_id, leadership_venue="KRX") for value in values)
                runtime = MarketThemeShadowRuntime(hub, collector, tuple(zip(monitors, values)), record_store=store if record else None) if count else None
                statements = []
                connect = store._connect
                def traced():
                    connection = connect(); connection.set_trace_callback(statements.append); return connection
                try:
                    from contextlib import ExitStack
                    with ExitStack() as hooks:
                        connections = hooks.enter_context(patch.object(store, "_connect", side_effect=traced))
                        h1 = hooks.enter_context(patch.object(shadow_context, "compute_market_regime", wraps=shadow_context.compute_market_regime))
                        h2 = hooks.enter_context(patch.object(shadow_context, "evaluate_theme_leadership", wraps=shadow_context.evaluate_theme_leadership))
                        minutes = hooks.enter_context(patch.object(collector._minute_bars, "add", wraps=collector._minute_bars.add))
                        seconds = hooks.enter_context(patch.object(collector, "_observe_ram_trade", wraps=collector._observe_ram_trade))
                        writers = {name: hooks.enter_context(patch.object(store, name, wraps=getattr(store, name))) for name in (
                            "save_realtime_snapshots", "save_minute_bars", "finalize_minute_bars", "save_second_trade_bars")}
                        records = hooks.enter_context(patch.object(store, "upsert_documents", wraps=store.upsert_documents))
                        if runtime:
                            await runtime.start()
                        helper.trades(collector, now)
                        if runtime:
                            await helper.wait_for(lambda: all(m.status["evaluations"] == 1 for m in monitors))
                            if record:
                                await helper.wait_for(lambda: runtime.status["recording"]["writes"] == 1)
                            self.assertEqual((1, 8), (h1.call_count, h2.call_count))
                            self.assertEqual(1, runtime.status["snapshot_reads"])
                            self.assertEqual(8, len({m.last_result["revision_id"] for m in monitors}))
                        now[0] = now[0].replace(minute=1, second=2)
                        await collector._flush_snapshots()
                        self.assertEqual((80, 80), (minutes.call_count, seconds.call_count))
                        self.assertEqual({name: (0 if name=="save_second_trade_bars" else 1) for name in writers}, {name: writer.call_count for name, writer in writers.items()})
                        self.assertEqual(int(record), records.call_count)
                        self.assertEqual(3 + int(record), connections.call_count)
                        self.assertEqual(3 + int(record), sum(sql.strip().upper() == "COMMIT" for sql in statements))
                        if record:
                            document = records.call_args.args[1][0]["document"]
                            self.assertEqual((8, 8), (len(document["contexts"]), len(document["results"])))
                        reports.append({"themes": count, "recording": record, "ram_reads": runtime.status["snapshot_reads"] if runtime else 0,
                            "h1": h1.call_count, "h2": h2.call_count, "minute_adds": minutes.call_count, "second_adds": seconds.call_count,
                            "connections": connections.call_count, "commits": sum(sql.strip().upper() == "COMMIT" for sql in statements),
                            "record_calls": records.call_count, "original_writers": {name: writer.call_count for name, writer in writers.items()}})
                    with closing(sqlite3.connect(store._path)) as connection:
                        payloads.append((connection.execute("SELECT * FROM central_minute_bars ORDER BY code,minute").fetchall(),
                                         connection.execute("SELECT * FROM central_second_trade_bars ORDER BY code,trade_second").fetchall()))
                    if runtime:
                        await runtime.close()
                finally:
                    if runtime:
                        await runtime.close()
                    store.close()
        self.assertEqual(payloads[0], payloads[1]); self.assertEqual(payloads[0], payloads[2])
        print("CROSS_THEME_RAM_PROBE=" + json.dumps(reports))


class DynamicAllocationRamTests(unittest.IsolatedAsyncioTestCase):
    fixture=CollectorRamShadowRuntimeTests.fixture
    trades=CollectorRamShadowRuntimeTests.trades
    wait_for=CollectorRamShadowRuntimeTests.wait_for

    async def test_market_only_allocation_uses_market_lease_and_independent_risk_membership(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import dynamic_allocation_inputs_from_ram
        for count in (2,8):
            runtime,collector,now,source,observation,_=self.prepare(count)
            try:
                snapshot=runtime._snapshot();await runtime._dispatch(snapshot)
                context=shadow_context.evaluate_market_theme_shadow(replace(runtime._monitors[0]._last_offered,
                    second_points=(),continuity_status='UNAVAILABLE'))
                self.assertNotEqual('COMPLETE',context['leadership']['quality']['status'])
                self.assertLessEqual(datetime.fromisoformat(context['expires_at']),now[0])
                membership=runtime._bindings[0][1].theme_membership
                selection={**runtime._allocation,'policy':replace(runtime._allocation['policy'],context_weights=(1,0,0))}
                with patch('sqlite3.connect',side_effect=AssertionError('independent context SQLite')), \
                     patch('psycopg.connect',side_effect=AssertionError('independent context PostgreSQL')):
                    result=dynamic_allocation_inputs_from_ram(selection,snapshot,(context,),observation,
                        theme_memberships=(membership,))
                self.assertTrue(all(f['market_strength_bps'] is not None and f['theme_ids']==[membership.theme_id] for f in result['facts']))
                self.assertNotIn('context_not_complete_or_expired',result['quality']['reasons'])
                self.assertFalse(any(r.startswith('native_market_theme_factors_unavailable') for r in result['quality']['reasons']))
                self.assertGreater(datetime.fromisoformat(result['valid_until']),now[0])
                self.assertIn(membership.revision_id,result['input_refs'])
                missing=dynamic_allocation_inputs_from_ram(selection,snapshot,(context,),observation,theme_memberships=())
                self.assertTrue(any(r.startswith('native_theme_risk_membership_unavailable') for r in missing['quality']['reasons']))
                required=dynamic_allocation_inputs_from_ram(runtime._allocation,snapshot,(context,),observation)
                self.assertTrue(any(r.startswith('native_market_theme_factors_unavailable') for r in required['quality']['reasons']))
                self.assertEqual(1,source.call_count)
                self.assertFalse(result['order_authorized'])
            finally:await runtime.close()

    async def test_component_expiry_and_future_risk_membership_remain_blocking(self):
        import hashlib
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import dynamic_allocation_inputs_from_ram
        runtime,_,now,_,observation,_=self.prepare(2)
        try:
            snapshot=runtime._snapshot();await runtime._dispatch(snapshot)
            context=deepcopy(runtime._monitors[0]._result)
            membership=runtime._bindings[0][1].theme_membership
            selection={**runtime._allocation,'policy':replace(runtime._allocation['policy'],context_weights=(1,0,0))}
            context['market']['expires_at']=now[0].isoformat()
            body={k:v for k,v in context.items() if k!='revision_id'}
            context['revision_id']='market-theme-shadow:'+hashlib.sha256(json.dumps(body,
                sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            result=dynamic_allocation_inputs_from_ram(selection,snapshot,(context,),observation,theme_memberships=(membership,))
            self.assertIn('context_not_complete_or_expired',result['quality']['reasons'])
            result=dynamic_allocation_inputs_from_ram(selection,snapshot,(runtime._monitors[0]._result,),observation,
                theme_memberships=(replace(membership,available_at=(now[0]+timedelta(seconds=1)).isoformat()),))
            self.assertIn('native_theme_membership_from_future',result['quality']['reasons'])
            self.assertTrue(all(not f['theme_ids'] for f in result['facts']))
            with self.assertRaises(ValueError):
                dynamic_allocation_inputs_from_ram(selection,snapshot,(context,),observation,theme_memberships=({'theme_id':'fake'},))
            with self.assertRaises(ValueError):
                dynamic_allocation_inputs_from_ram(selection,snapshot,(context,),observation,
                    theme_memberships=(membership,replace(membership,revision_id='other-membership-cut')))
            future=deepcopy(runtime._monitors[0]._result)
            future['available_at']=(now[0]+timedelta(seconds=1)).isoformat()
            body={k:v for k,v in future.items() if k!='revision_id'}
            future['revision_id']='market-theme-shadow:'+hashlib.sha256(json.dumps(body,
                sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            result=dynamic_allocation_inputs_from_ram(selection,snapshot,(future,),observation,theme_memberships=(membership,))
            self.assertIn('context_from_future',result['quality']['reasons'])
        finally:await runtime.close()

    async def test_shared_market_only_runtime_calculates_without_unused_leadership_window(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import (
            compile_dynamic_allocation_ram_selection,prepare_dynamic_allocation_model_publication,
            decode_dynamic_allocation_model_publication)
        from kiwoom_monitor.central_server import market_theme_shadow_inputs as inputs_module
        for count in (2,8):
            runtime,collector,_,source,_,document,legacy=AllocationModelPublicationTests.prepare(self,count)
            try:
                document['policy']['context_weights']=[1,0,0]
                runtime._allocation=compile_dynamic_allocation_ram_selection(document)
                packet=prepare_dynamic_allocation_model_publication(document,legacy['rows'],expected_selection_revision=1)
                runtime.offer_allocation_models(packet,decode_dynamic_allocation_model_publication(packet,runtime._allocation,1))
                snapshot=runtime._snapshot()
                snapshot['rows']=tuple(row for row in snapshot['rows'] if row['code']=='005930')
                with patch.object(inputs_module,'dynamic_allocation_inputs_from_ram',wraps=inputs_module.dynamic_allocation_inputs_from_ram) as calculate, \
                     patch('sqlite3.connect',side_effect=AssertionError('market-only runtime SQLite')), \
                     patch('psycopg.connect',side_effect=AssertionError('market-only runtime PostgreSQL')):
                    await runtime._dispatch(snapshot)
                    receipt=runtime.latest_results['position_allocation']
                    self.assertEqual('CALCULATED',receipt['quality']['status'],receipt['quality']['reasons'])
                    self.assertIsNotNone(receipt['result'])
                    self.assertTrue(all(m._result['leadership']['quality']['status']!='COMPLETE' for m in runtime._monitors))
                    self.assertEqual('CALCULATED',receipt['lower_targets'][0]['quality']['status'])
                    self.assertTrue(calculate.call_args.kwargs['theme_memberships'])
                    before=deepcopy(receipt)
                    for _ in range(100):runtime.latest_results
                    self.assertEqual(before,runtime.latest_results['position_allocation'])
                    self.assertEqual((1,1),(source.call_count,calculate.call_count))
                    self.assertFalse(receipt['order_authorized'])
            finally:await runtime.close()

    async def test_unused_market_score_does_not_block_native_facts_or_fabricate_market_breadth(self):
        import hashlib
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import dynamic_allocation_inputs_from_ram
        runtime,collector,now,source,observation,_=self.prepare()
        try:
            snapshot=runtime._snapshot()
            await runtime._dispatch(snapshot)
            context=deepcopy(runtime._monitors[0]._result)
            context['market']['facts']['top20_breadth_ppm']=None
            body={k:v for k,v in context.items() if k!='revision_id'}
            context['revision_id']='market-theme-shadow:'+hashlib.sha256(json.dumps(body,
                sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            selection={**runtime._allocation,'policy':replace(runtime._allocation['policy'],context_weights=(0,0,1))}
            with patch('sqlite3.connect',side_effect=AssertionError('unused score source SQLite')):
                receipt=dynamic_allocation_inputs_from_ram(selection,snapshot,(context,),observation)
            self.assertTrue(all(f['market_strength_bps'] is None for f in receipt['facts']))
            self.assertTrue(all(f['stock_strength_bps'] is not None and f['theme_ids'] for f in receipt['facts']))
            self.assertFalse(any(r.startswith('native_market_theme_factors_unavailable') for r in receipt['quality']['reasons']))
            self.assertIn('allocation_ev_unavailable',receipt['quality']['reasons'])
            required=dynamic_allocation_inputs_from_ram(runtime._allocation,snapshot,(context,),observation)
            self.assertTrue(any(r.startswith('native_market_theme_factors_unavailable') for r in required['quality']['reasons']))
            self.assertFalse(receipt['order_authorized'])
        finally:await runtime.close()

    def prepare(self,count=8,comparison=False):
        from dataclasses import asdict
        from tests.unit.test_entry_thesis import DynamicPositionAllocationTests,NativePositionSelectionInputsTests
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import compile_dynamic_allocation_ram_selection
        base=DynamicPositionAllocationTests();base.setUp();native=NativePositionSelectionInputsTests();native.setUp()
        now,hub,collector,monitors,_=self.fixture(count,cleanup=False)
        at=now[0]+timedelta(seconds=39)
        account=replace(native.account,as_of=at)
        risk=native.risk_snapshot(observed=at,account_at=at)
        observation=PositionAccountObservation(account,(),'native-account-ram',at,True,True,(),(),(),risk)
        policy=replace(base.policy,account_ref=account.account_ref,capacity_window_seconds=20,
            available_at=now[0]-timedelta(seconds=1),valid_until=now[0]+timedelta(seconds=120))
        document={'schema':'dynamic-position-allocation-ram/v1','policy':asdict(policy),
            'symbols':['005930','000660'],'expected_execution_run_id':'run-a','expected_binding_revision':3,
            'data_path':'nas','maximum_daily_loss_won':1000,'cost_model':base.fixture.cost.to_dict()}
        document=json.loads(json.dumps(document,default=lambda v:v.isoformat()))
        source=Mock(return_value=observation)
        position_document=None
        if comparison:
            position_document=native.document()
            position_document['parameters'].pop('selection_policy',None)
        runtime=MarketThemeShadowRuntime(hub,collector,tuple((m,shadow_inputs()) for m in monitors),
            position_document=position_document,position_account_source=source,
            position_allocation=compile_dynamic_allocation_ram_selection(document))
        collector.set_ram_observation_retention(runtime._retention)
        collector._ram_retention_since=now[0]
        self.trades(collector,now)
        for code in ('005930','000660'):
            collector._latest_snapshots[('trade',code)]['event']['payload']['market_cap_eok']=1234
        self.addAsyncCleanup(runtime.close)
        return runtime,collector,now,source,observation,document

    async def test_native_quote_cap_turnover_and_ack_risk_are_shared_once_for_eight_monitors(self):
        from kiwoom_monitor.application import position_target
        runtime,collector,now,source,observation,_=self.prepare()
        dirty=(set(collector._minute_bars._dirty),tuple(r["input_ref"] for r in collector._ram_trades))
        with patch('sqlite3.connect',side_effect=AssertionError('RAM read DB')),patch('psycopg.connect',side_effect=AssertionError('RAM read PG')), \
             patch.object(position_target,'evaluate_dynamic_position_allocation',side_effect=AssertionError('unknown EV/cap fabricated')):
            await runtime._dispatch(runtime._snapshot())
        receipt=runtime.latest_results['position_allocation']
        self.assertEqual(1,source.call_count);self.assertEqual(observation.risk_snapshot.snapshot_id,receipt['risk_snapshot_ref'])
        facts={f['symbol']:f for f in receipt['facts']}
        self.assertEqual(1039,facts['005930']['reference_price_won']);self.assertEqual(123400000000,facts['005930']['market_cap_won'])
        self.assertIsNotNone(facts['005930']['recent_turnover_won']);self.assertIsNone(facts['005930']['net_ev_bps'])
        self.assertIsNone(receipt['result']);self.assertFalse(receipt['order_authorized'])
        self.assertEqual('ALLOW',receipt['risk_disposition']);self.assertIn('full_exit_capacity_unavailable',receipt['quality']['reasons'])
        self.assertEqual(dirty,(set(collector._minute_bars._dirty),tuple(r["input_ref"] for r in collector._ram_trades)))
        self.assertEqual(1,runtime._allocation_evaluations)

    async def test_r4_and_allocation_reuse_same_account_cut_even_on_source_failure(self):
        runtime,_,_,source,_,_=self.prepare(comparison=True)
        await runtime._dispatch(runtime._snapshot())
        self.assertEqual(1,source.call_count);self.assertEqual(0,runtime._allocation_reads)
        source.reset_mock();source.side_effect=OSError('source unavailable')
        await runtime._refresh_position_inputs(runtime._snapshot())
        self.assertEqual(1,source.call_count);self.assertIsNone(runtime.latest_results['position_allocation']['result'])
        self.assertIn('native_allocation_input_error:OSError',runtime.latest_results['position_allocation']['quality']['reasons'])

    async def test_status_reads_are_inert_and_do_not_recalculate_or_extend_native_time(self):
        runtime,_,now,source,_,_=self.prepare()
        await runtime._dispatch(runtime._snapshot());before=deepcopy(runtime.latest_results['position_allocation'])
        calls=source.call_count;evaluations=runtime._allocation_evaluations
        with patch('sqlite3.connect',side_effect=AssertionError('status DB')):
            for _ in range(100):runtime.latest_results;runtime.status
        self.assertEqual(calls,source.call_count);self.assertEqual(evaluations,runtime._allocation_evaluations)
        self.assertEqual(before,runtime.latest_results['position_allocation'])
        now[0]+=timedelta(seconds=31)
        expired=runtime.latest_results['position_allocation']
        self.assertEqual(before['available_at'],expired['available_at']);self.assertEqual(before['valid_until'],expired['valid_until'])
        self.assertIn('native_allocation_receipt_expired',expired['quality']['reasons'])

    async def test_wrong_scope_unconfirmed_or_missing_risk_never_becomes_native_input(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import dynamic_allocation_inputs_from_ram
        runtime,_,_,_,observation,_=self.prepare()
        snapshot=runtime._snapshot()
        for risk in (None,replace(observation.risk_snapshot,execution_run_id='other-run'),replace(observation.risk_snapshot,binding_revision=4)):
            receipt=dynamic_allocation_inputs_from_ram(runtime._allocation,snapshot,(),replace(observation,risk_snapshot=risk))
            self.assertIsNone(receipt['result']);self.assertEqual('HALT_NEW_ORDERS',receipt['risk_disposition'])
            self.assertNotIn('risk_snapshot_ref',receipt)
        receipt=dynamic_allocation_inputs_from_ram(runtime._allocation,snapshot,(),replace(observation,open_orders_complete=False))
        self.assertIn('native_account_order_projection_unverified',receipt['quality']['reasons'])

    async def test_confirmed_loss_and_cost_unknown_are_classified_without_ev_or_capacity(self):
        from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict,_sha256
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import dynamic_allocation_inputs_from_ram
        runtime,_,_,_,observation,_=self.prepare()
        def risk_with(**updates):
            body=observation.risk_snapshot.to_dict();body.pop('snapshot_id');body.update(updates);body['snapshot_id']=_sha256(body)
            return mock_automation_risk_snapshot_from_dict(body)
        loss=risk_with(daily_net_pnl_won=-1000)
        receipt=dynamic_allocation_inputs_from_ram(runtime._allocation,runtime._snapshot(),(),replace(observation,risk_snapshot=loss))
        self.assertTrue(receipt['protection_required']);self.assertEqual('EXIT_REQUIRED',receipt['risk_disposition']);self.assertIsNone(receipt['result'])
        unknown=risk_with(cost_complete=False,missing_cost_reasons=['COST_UNAVAILABLE'],daily_net_pnl_won=None,daily_net_pnl_source=None)
        receipt=dynamic_allocation_inputs_from_ram(runtime._allocation,runtime._snapshot(),(),replace(observation,risk_snapshot=unknown))
        self.assertEqual('NO_NEW_RISK',receipt['risk_disposition']);self.assertFalse(receipt['protection_required'])

    async def test_stale_native_risk_does_not_issue_new_protection_from_old_loss(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import dynamic_allocation_inputs_from_ram
        runtime,_,_,_,observation,_=self.prepare();snapshot=runtime._snapshot()
        snapshot['decision_time']=(datetime.fromisoformat(snapshot['decision_time'])+timedelta(seconds=31)).isoformat()
        receipt=dynamic_allocation_inputs_from_ram(runtime._allocation,snapshot,(),observation)
        self.assertEqual('HALT_NEW_ORDERS',receipt['risk_disposition']);self.assertFalse(receipt['protection_required'])

    async def test_unapproved_paused_market_cap_unknown_and_retention_gap_are_explicit(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import dynamic_allocation_inputs_from_ram
        runtime,collector,now,_,observation,_=self.prepare()
        collector._latest_snapshots[('trade','005930')]['event']['payload']['market_cap_eok']=None
        collector._ram_observation_gap_at=now[0]
        receipt=dynamic_allocation_inputs_from_ram(runtime._allocation,runtime._snapshot(),(),observation)
        self.assertIn('native_market_cap_unavailable:005930',receipt['quality']['reasons'])
        self.assertIn('native_turnover_window_incomplete:005930',receipt['quality']['reasons'])
        collector._credential_paused=True
        receipt=dynamic_allocation_inputs_from_ram(runtime._allocation,runtime._snapshot(),(),observation)
        self.assertIn('approved_KRX_quote_unavailable:005930',receipt['quality']['reasons'])

    async def test_original_quote_ttl_survives_repeated_binding_and_no_default_metadata(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import dynamic_allocation_inputs_from_ram
        runtime,collector,_,_,observation,_=self.prepare();snapshot=runtime._snapshot()
        a=dynamic_allocation_inputs_from_ram(runtime._allocation,snapshot,(),observation)
        snapshot['decision_time']=(datetime.fromisoformat(snapshot['decision_time'])+timedelta(seconds=1)).isoformat()
        b=dynamic_allocation_inputs_from_ram(runtime._allocation,snapshot,(),observation)
        self.assertEqual(a['available_at'],b['available_at']);self.assertEqual(a['valid_until'],b['valid_until'])
        legacy=collector.ram_observation_snapshot(('005930',),seconds=40)
        self.assertNotIn('trade_references',legacy)

    async def test_account_only_wake_reuses_results_and_close_stops_observation(self):
        runtime,collector,now,source,observation,_=self.prepare()
        await runtime.start()
        await self.wait_for(lambda: runtime._allocation_receipt is not None)
        before=[m.status['evaluations'] for m in runtime._monitors];calls=source.call_count
        source.return_value=replace(observation,input_ref='native-account-ram:2')
        runtime.context_changed('account')
        await self.wait_for(lambda: runtime._allocation_receipt.get('account_input_ref')=='native-account-ram:2')
        self.assertEqual(before,[m.status['evaluations'] for m in runtime._monitors])
        self.assertFalse(runtime._position_pending)
        self.assertEqual('native-account-ram:2',runtime.latest_results['position_allocation']['account_input_ref'])
        await runtime.close();calls=source.call_count;runtime.context_changed('account');self.assertEqual(calls,source.call_count)

    async def test_http_opt_in_read_and_explicit_clear_reuse_existing_selection_lifecycle(self):
        import httpx
        helper=RamShadowSelectionTests()
        _,_,_,_,_,document=self.prepare(1)
        with tempfile.TemporaryDirectory() as directory:
            app,store,now,collector=helper.app_fixture(directory);headers={'Authorization':'Bearer offline-token'}
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
                    url='/api/v1/research/ram-shadow/selection'
                    denied=await client.put(url,json={'expected_revision':0,'position_allocation':document})
                    self.assertEqual(401,denied.status_code)
                    response=await client.put(url,headers=headers,json={'expected_revision':0,'enabled':True,
                        'theme_ids':['반도체'],'position_allocation':document})
                    self.assertEqual(200,response.status_code,response.text)
                    runtime=app.state.market_theme_shadow_runtime;self.trades(collector,now)
                    await self.wait_for(lambda:runtime._allocation_receipt is not None)
                    runtime._task.cancel();await asyncio.gather(runtime._task,return_exceptions=True)
                    with patch.object(store,'_connect',side_effect=AssertionError('live read DB')):
                        for _ in range(20):
                            response=await client.get('/api/v1/research/ram-shadow',headers=headers)
                            self.assertEqual(200,response.status_code)
                            receipt=response.json()['live']['position_allocation']
                            self.assertIsNone(receipt['result']);self.assertIn('native_account_observation_unavailable',receipt['quality']['reasons'])
                    response=await client.put(url,headers=headers,json={'expected_revision':1,'position_allocation':None})
                    self.assertEqual(200,response.status_code,response.text);self.assertNotIn('position_allocation',response.json())
                    self.assertNotIn('position_allocation',app.state.market_theme_shadow_runtime.latest_results)
            finally:await helper.cleanup_app(app,store)

    async def test_tampered_context_cannot_supply_normalized_market_factors(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import dynamic_allocation_inputs_from_ram
        runtime,_,_,_,observation,_=self.prepare()
        await runtime._dispatch(runtime._snapshot())
        context=deepcopy(runtime._monitors[0]._result);context['market']['facts']['top20_breadth_ppm']=1000000
        context['revision_id']='market-theme-shadow:'+'0'*64
        receipt=dynamic_allocation_inputs_from_ram(runtime._allocation,runtime._snapshot(),(context,),observation)
        self.assertIn('context_content_invalid',receipt['quality']['reasons'])
        self.assertTrue(all(f['market_strength_bps'] is None for f in receipt['facts']))

    async def test_blocked_allocation_work_does_not_delay_native_stop_and_close_joins_it(self):
        import threading
        from tests.unit.test_mock_automation_specification import MockImmediateStopLossTests
        from kiwoom_monitor.central_server import market_theme_shadow_inputs as module
        runtime,_,_,_,_,_=self.prepare(1)
        entered=threading.Event();release=threading.Event();native=module.dynamic_allocation_inputs_from_ram
        def held(*args):
            entered.set()
            if not release.wait(5):raise TimeoutError('controlled allocation release')
            return native(*args)
        case=MockImmediateStopLossTests();case.setUp()
        try:
            await case.asyncSetUp();case.connect()
            with patch.object(module,'dynamic_allocation_inputs_from_ram',side_effect=held):
                await runtime.start();self.assertTrue(await asyncio.to_thread(entered.wait,2))
                before=len(case.transport.calls);case.tick(case.stop_price())
                self.assertTrue(case.runner._protection_pending());self.assertEqual(before,len(case.transport.calls))
                close=asyncio.create_task(runtime.close());await asyncio.sleep(.02)
                self.assertFalse(close.done());release.set();await asyncio.wait_for(close,2)
                self.assertTrue(runtime._closed)
        finally:
            release.set();await runtime.close();await case.asyncTearDown()
            while case._cleanups:
                function,args,kwargs=case._cleanups.pop()
                result=function(*args,**kwargs)
                if asyncio.iscoroutine(result):await result

    async def test_turnover_and_fractional_return_rms_reuse_exact_absolute_rows(self):
        runtime,_,_,_,_,_=self.prepare(1);await runtime._dispatch(runtime._snapshot())
        facts={f['symbol']:f for f in runtime.latest_results['position_allocation']['facts']}
        self.assertEqual(41180,facts['005930']['recent_turnover_won'])
        self.assertEqual(10,facts['005930']['observed_close_return_rms_bps'])
        self.assertTrue(facts['005930']['window_input_ref'].startswith('ram-allocation-0B-window:'))
        self.assertIn(facts['005930']['window_input_ref'],facts['005930']['input_refs'])
        self.assertNotEqual(facts['005930']['recent_turnover_won'],facts['000660']['recent_turnover_won'])

    async def test_0b_rms_preserves_observed_second_prices_despite_receipt_frequency_and_excursions(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import dynamic_allocation_inputs_from_ram
        from kiwoom_monitor.central_server.minute_bars import SecondTradeAccumulator
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
        runtime,collector,now,_,observation,_=self.prepare(1)
        snapshot=runtime._snapshot();cut=datetime.fromisoformat(snapshot['decision_time'])
        template=next(row for row in snapshot['rows'] if row['code']=='005930')
        scenarios=(((10000,),(10100,),(10200,)),
            ((10000,)*4,(10100,)*4,(10200,)*4),((9000,10000),(9000,10100),(9000,10200)))
        for seconds in scenarios:
            with self.subTest(receipts=seconds):
                legacy=SecondTradeAccumulator();legacy.set_ram_retention(60)
                rows=[]
                for second,prices in enumerate(seconds):
                    at=(cut-timedelta(seconds=9-second)).replace(microsecond=0)
                    for offset,price in enumerate(prices):
                        seq=len(rows)+1;received=at+timedelta(microseconds=offset*1000)
                        rows.append({**template,'input_ref':'ram-0B:rms-regression:'+str(seq),'sequence':seq,
                            'observed_at':at.isoformat(),'available_at':received.timestamp(),
                            'price_won':price,'volume':1,'trade_value_won':price})
                        legacy.add(TradeTick('005930',price,seq,None,1,None,at.strftime('%H%M%S'),market='KRX'),
                            received,received.timestamp())
                archived=legacy.observation_snapshot(('005930',),at=cut,seconds=20)
                with patch('sqlite3.connect',side_effect=AssertionError('RMS SQLite read')), \
                     patch('psycopg.connect',side_effect=AssertionError('RMS PostgreSQL read')):
                    report=dynamic_allocation_inputs_from_ram(runtime._allocation,{**snapshot,'rows':tuple(rows)},(),observation)
                fact=next(f for f in report['facts'] if f['symbol']=='005930')
                self.assertEqual(100,fact['observed_close_return_rms_bps'])
                self.assertEqual(3,fact['volatility_sample_count'])
                self.assertEqual('last_received_price_per_observed_second/v1',fact['volatility_basis'])
                self.assertEqual([10000,10100,10200],[r['close'] for r in archived])
                self.assertEqual(sum(r['trade_value_won'] for r in archived),fact['recent_turnover_won'])
                self.assertFalse(report['order_authorized'])
        self.assertFalse(hasattr(collector,'_second_trades'))

    async def test_many_receipts_in_one_second_do_not_fabricate_rms_history_or_fill_empty_seconds(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import dynamic_allocation_inputs_from_ram
        runtime,_,_,_,observation,_=self.prepare(1)
        snapshot=runtime._snapshot();cut=datetime.fromisoformat(snapshot['decision_time'])
        template=next(row for row in snapshot['rows'] if row['code']=='005930')
        for offsets,expected_count,expected_rms in (((9,9,9,9),1,None),((9,6,2),3,100)):
            rows=[]
            for index,offset in enumerate(offsets):
                at=(cut-timedelta(seconds=offset)).replace(microsecond=0)
                price=10000+index*100
                rows.append({**template,'input_ref':'ram-0B:rms-history:'+str(index),'sequence':index,
                    'observed_at':at.isoformat(),'available_at':at.timestamp()+index/1000,
                    'price_won':price,'volume':1,'trade_value_won':price})
            result=dynamic_allocation_inputs_from_ram(runtime._allocation,{**snapshot,'rows':tuple(rows)},(),observation)
            fact=next(f for f in result['facts'] if f['symbol']=='005930')
            self.assertEqual(expected_count,fact['volatility_sample_count'])
            self.assertEqual(expected_rms,fact['observed_close_return_rms_bps'])

    async def test_selection_disallows_fabricated_model_facts_and_mismatched_account(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import compile_dynamic_allocation_ram_selection
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import validate_ram_shadow_selection
        _,_,_,_,_,document=self.prepare()
        for mutate in (lambda d:d.update(net_ev_bps=100),lambda d:d.update(expected_binding_revision=True),
                       lambda d:d['policy'].update(capacity_window_seconds=61)):
            d=deepcopy(document);mutate(d)
            with self.assertRaises(ValueError):compile_dynamic_allocation_ram_selection(d)
        selection={'version':'ram-shadow-selection/v1','revision':0,'enabled':True,'theme_ids':['반도체'],
            'leadership_venue':'KRX','recording':False,'position_allocation':document}
        self.assertEqual(document,validate_ram_shadow_selection(selection)['position_allocation'])
        selection['leadership_venue']='NXT'
        with self.assertRaises(ValueError):validate_ram_shadow_selection(selection)


class AllocationModelPublicationTests(unittest.IsolatedAsyncioTestCase):
    fixture=CollectorRamShadowRuntimeTests.fixture
    wait_for=CollectorRamShadowRuntimeTests.wait_for

    def prepare(self,count=8):
        from dataclasses import asdict
        from kiwoom_monitor.application.research_execution import SimulationCostModel
        from tests.unit.test_entry_thesis import NativeFullExitStressTests,NativeDynamicAllocationEvTests,DynamicPositionAllocationTests,NativePositionSelectionInputsTests
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import compile_dynamic_allocation_ram_selection,prepare_dynamic_allocation_model_publication
        exit_case=NativeFullExitStressTests();xr=exit_case.prepare();self.addCleanup(exit_case.doCleanups)
        cost=SimulationCostModel(**xr['replay_inputs']['execution']['cost_model'])
        ev_case=NativeDynamicAllocationEvTests();ev_case.setUp();self.addCleanup(ev_case.doCleanups)
        er=ev_case.prepare(cost_override=cost,reference_price=xr['evidence'].reference_price_won,terminal_price=1060,horizon_minutes=3)
        self.assertIsNotNone(er['evidence'])
        now,hub,collector,monitors,_=self.fixture(count,cleanup=False)
        now[0]=exit_case.fixture.cut.astimezone(timezone(timedelta(hours=9))).replace(second=0,microsecond=0)
        collector._apply_trade_source_approval({('005930','KRX'),('000660','KRX')},now[0],reset_all=True)
        collector.set_ram_observation_retention(20)
        collector._ram_retention_since=now[0]
        native=NativePositionSelectionInputsTests();native.setUp();base=DynamicPositionAllocationTests();base.setUp()
        at=now[0]+timedelta(seconds=39);account=replace(native.account,as_of=at)
        observation=PositionAccountObservation(account,(),'native-allocation-account',at,True,True,(),(),(),native.risk_snapshot(observed=at,account_at=at))
        policy=replace(base.policy,account_ref=account.account_ref,capacity_window_seconds=20,
            available_at=now[0]-timedelta(seconds=1),valid_until=now[0]+timedelta(seconds=120),
            allocation_ev_horizon_seconds=180,allocation_exit_horizon_seconds=120,max_turnover_ratio_bps=10000)
        from kiwoom_monitor.application.position_target import _json_value
        document=json.loads(json.dumps({'schema':'dynamic-position-allocation-ram/v1','policy':asdict(policy),'symbols':['005930'],
            'expected_execution_run_id':'run-a','expected_binding_revision':3,'data_path':'nas',
            'maximum_daily_loss_won':1000,'cost_model':cost.to_dict()},default=_json_value))
        source=Mock(return_value=observation)
        context=shadow_inputs()
        context=replace(context,market_frames=tuple(replace(frame,available_at=(now[0]+timedelta(seconds=i*10)).isoformat(),
            trading_date=now[0].date().isoformat(),session_minute=now[0].strftime('%H:%M')) for i,frame in enumerate(context.market_frames)),
            theme_membership=replace(context.theme_membership,available_at=now[0].isoformat()),
            breakout_evidence=replace(context.breakout_evidence,available_at=now[0].isoformat()))
        runtime=MarketThemeShadowRuntime(hub,collector,tuple((m,context) for m in monitors),
            position_account_source=source,position_allocation=compile_dynamic_allocation_ram_selection(document))
        for second in range(40):
            now[0]=now[0].replace(second=second)
            collector._publish_parsed({'trnm':'REAL','data':[{'type':'0B','item':code,'values':{
                '10':str(price+second),'15':'2','13':str(100+second*2),'14':str(100+second*(2 if code=='005930' else 1)),'20':now[0].strftime('%H%M%S'),
                '311':'1234'}} for code,price in (('005930',xr['evidence'].reference_price_won-39),('000660',900))]})
        packet=prepare_dynamic_allocation_model_publication(document,[{'symbol':'005930','ev_receipt':er,'exit_receipt':xr}],expected_selection_revision=1)
        self.addAsyncCleanup(runtime.close)
        return runtime,collector,now,source,observation,document,packet

    async def test_native_replays_publish_once_and_shared_upper_lower_calculate(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import decode_dynamic_allocation_model_publication
        runtime,collector,_,source,_,_,packet=self.prepare()
        decoded=decode_dynamic_allocation_model_publication(packet,runtime._allocation,1)
        self.assertFalse(runtime.offer_allocation_models(packet,decoded)['duplicate'])
        with patch('sqlite3.connect',side_effect=AssertionError('publication consumer DB')):
            await runtime._dispatch(runtime._snapshot())
        receipt=runtime.latest_results['position_allocation']
        self.assertEqual('CALCULATED',receipt['quality']['status'],receipt)
        self.assertEqual(1,source.call_count);self.assertEqual([],receipt['missing_producers'])
        self.assertGreater(receipt['result']['allocations'][0]['new_buy_quantity'],0)
        self.assertEqual(receipt['result']['allocations'][0]['target_notional_won'],receipt['lower_targets'][0]['target_notional_won'])
        self.assertEqual('EV_UNAVAILABLE',receipt['r4_action_comparison']['status']);self.assertFalse(receipt['order_authorized'])

    async def test_duplicate_is_inert_and_expiry_never_renews_or_exposes_old_goal(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import decode_dynamic_allocation_model_publication
        runtime,_,now,source,_,_,packet=self.prepare(1)
        runtime.offer_allocation_models(packet,decode_dynamic_allocation_model_publication(packet,runtime._allocation,1))
        await runtime._dispatch(runtime._snapshot());before=deepcopy(runtime.latest_results['position_allocation'])
        calls=source.call_count;evaluations=runtime._allocation_evaluations
        for _ in range(100):
            self.assertTrue(runtime.allocation_publication_duplicate(packet)['duplicate']);runtime.latest_results
        self.assertEqual((calls,evaluations,1),(source.call_count,runtime._allocation_evaluations,runtime._allocation_model_generation))
        now[0]=datetime.fromisoformat(before['valid_until'])
        expired=runtime.latest_results['position_allocation'];self.assertIsNone(expired['result'])
        self.assertEqual(before['valid_until'],expired['valid_until'])
        self.assertTrue(runtime.allocation_publication_duplicate(packet)['duplicate'])
        now[0]=runtime._allocation_models['valid_until']
        with self.assertRaises(ValueError):runtime.allocation_publication_duplicate(packet)

    async def test_tampered_hash_scope_statistics_and_cancelled_exit_are_rejected(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import decode_dynamic_allocation_model_publication
        runtime,_,_,_,_,_,packet=self.prepare(1)
        def resign(body):
            import hashlib
            raw={k:v for k,v in body.items() if k!='publication_ref'}
            body['publication_ref']='allocation-model-publication:'+hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            return body
        for mutator in (lambda p:p.update(expected_selection_revision=2),
            lambda p:p['rows'][0]['ev_receipt']['evidence'].update(net_ev_bps=999999),
            lambda p:p['rows'][0]['exit_receipt']['result']['scenarios'][0].update(status='PARTIAL'),
            lambda p:p['rows'][0]['exit_receipt']['replay_inputs']['waiting_bars'].clear()):
            bad=deepcopy(packet);mutator(bad);resign(bad)
            with self.assertRaises(ValueError):decode_dynamic_allocation_model_publication(bad,runtime._allocation,1)
        bad=deepcopy(packet);bad['order_authorized']=True
        with self.assertRaises(ValueError):decode_dynamic_allocation_model_publication(bad,runtime._allocation,1)
        bad=deepcopy(packet);bad['rows']=['unsupported'];resign(bad)
        with self.assertRaises(ValueError):decode_dynamic_allocation_model_publication(bad,runtime._allocation,1)
        runtime.offer_allocation_models(packet,decode_dynamic_allocation_model_publication(packet,runtime._allocation,1))
        bad=deepcopy(packet);bad['expected_selection_revision']=True
        self.assertIsNone(runtime.allocation_publication_duplicate(bad))

    async def test_new_publication_cannot_be_overwritten_by_an_old_inflight_frame(self):
        from kiwoom_monitor.central_server import market_theme_shadow_inputs as module
        from dataclasses import asdict
        runtime,_,_,_,_,_,packet=self.prepare(1)
        decoded=module.decode_dynamic_allocation_model_publication(packet,runtime._allocation,1)
        runtime.offer_allocation_models(packet,decoded)
        entered=threading.Event();release=threading.Event();original=module.dynamic_allocation_inputs_from_ram
        def blocked(*args,**kwargs):
            entered.set();release.wait(10);return original(*args,**kwargs)
        with patch.object(module,'dynamic_allocation_inputs_from_ram',side_effect=blocked):
            task=asyncio.create_task(runtime._dispatch(runtime._snapshot()))
            try:
                await self.wait_for(entered.is_set)
                new=deepcopy(packet);new['publication_ref']='new-fixed-generation'
                changed={**decoded,'publication_ref':'new-fixed-generation'}
                runtime.offer_allocation_models(new,changed)
                release.set();await task
                self.assertIsNone(runtime._allocation_receipt);self.assertEqual(2,runtime._allocation_model_generation)
            finally:release.set();await task
        await runtime._refresh_position_inputs(runtime._snapshot())
        self.assertEqual('new-fixed-generation',runtime.latest_results['position_allocation']['model_publication_ref'])

    async def test_http_auth_revision_duplicate_and_clear_use_existing_configuration_lock(self):
        import httpx
        runtime,_,clock,_,_,document,packet=self.prepare(1)
        helper=RamShadowSelectionTests();headers={'Authorization':'Bearer offline-token'}
        with tempfile.TemporaryDirectory() as directory:
            app,store,now,collector=helper.app_fixture(directory);now[0]=clock[0]
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
                    url='/api/v1/research/ram-shadow/allocation-models'
                    self.assertEqual(401,(await client.post(url,json=packet)).status_code)
                    self.assertEqual(503,(await client.post(url,json=packet,headers=headers)).status_code)
                    selected=await client.put('/api/v1/research/ram-shadow/selection',headers=headers,
                        json={'expected_revision':0,'enabled':True,'theme_ids':['반도체'],'leadership_venue':'KRX','position_allocation':document})
                    self.assertEqual(200,selected.status_code,selected.text)
                    published=await client.post(url,headers=headers,json=packet)
                    self.assertEqual(200,published.status_code,published.text);self.assertFalse(published.json()['duplicate'])
                    active=app.state.market_theme_shadow_runtime
                    with patch('kiwoom_monitor.central_server.market_theme_shadow_inputs.decode_dynamic_allocation_model_publication',side_effect=AssertionError('duplicate replay')):
                        for _ in range(10):
                            response=await client.post(url,headers=headers,json=packet)
                            self.assertEqual(200,response.status_code,response.text);self.assertTrue(response.json()['duplicate'])
                    self.assertEqual(1,active._allocation_model_generation)
                    bad={**packet,'expected_selection_revision':2}
                    self.assertEqual(422,(await client.post(url,headers=headers,json=bad)).status_code)
                    await active.close()
                    self.assertEqual(503,(await client.post(url,headers=headers,json=packet)).status_code)
                    cleared=await client.put('/api/v1/research/ram-shadow/selection',headers=headers,
                        json={'expected_revision':1,'position_allocation':None})
                    self.assertEqual(200,cleared.status_code,cleared.text)
                    self.assertEqual(503,(await client.post(url,headers=headers,json=packet)).status_code)
                    self.assertNotIn('position_allocation_models',app.state.market_theme_shadow_runtime.latest_results)
            finally:await helper.cleanup_app(app,store)

    async def test_configuration_and_close_drain_a_cancelled_publication_replay(self):
        import httpx
        from kiwoom_monitor.central_server import market_theme_shadow_inputs as module
        from tests.unit.test_mock_automation_specification import MockImmediateStopLossTests
        _,_,clock,_,_,document,packet=self.prepare(1)
        entered=threading.Event();release=threading.Event();native=module.decode_dynamic_allocation_model_publication
        def held(*args):entered.set();release.wait(10);return native(*args)
        helper=RamShadowSelectionTests();headers={'Authorization':'Bearer offline-token'}
        case=MockImmediateStopLossTests();case.setUp()
        try:
            await case.asyncSetUp();case.connect()
            with tempfile.TemporaryDirectory() as directory:
                app,store,now,_=helper.app_fixture(directory);now[0]=clock[0]
                try:
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
                        selected=await client.put('/api/v1/research/ram-shadow/selection',headers=headers,
                            json={'expected_revision':0,'enabled':True,'theme_ids':['반도체'],'leadership_venue':'KRX','position_allocation':document})
                        self.assertEqual(200,selected.status_code,selected.text)
                        with patch.object(module,'decode_dynamic_allocation_model_publication',side_effect=held):
                            post=asyncio.create_task(client.post('/api/v1/research/ram-shadow/allocation-models',headers=headers,json=packet))
                            await self.wait_for(entered.is_set)
                            before=len(case.transport.calls);case.tick(case.stop_price())
                            self.assertTrue(case.runner._protection_pending());self.assertEqual(before,len(case.transport.calls))
                            clear=asyncio.create_task(client.put('/api/v1/research/ram-shadow/selection',headers=headers,
                                json={'expected_revision':1,'position_allocation':None}))
                            post.cancel();await asyncio.sleep(.01);self.assertFalse(post.done());self.assertFalse(clear.done())
                            release.set()
                            with self.assertRaises(asyncio.CancelledError):await post
                            self.assertEqual(200,(await clear).status_code)
                            self.assertNotIn('position_allocation_models',app.state.market_theme_shadow_runtime.latest_results)
                finally:release.set();await helper.cleanup_app(app,store)
        finally:
            release.set();await case.asyncTearDown()
            while case._cleanups:
                function,args,kwargs=case._cleanups.pop();result=function(*args,**kwargs)
                if asyncio.iscoroutine(result):await result

    async def test_stale_price_and_changed_native_risk_binding_block_cached_models(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import decode_dynamic_allocation_model_publication
        from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict,_sha256
        runtime,collector,_,source,observation,_,packet=self.prepare(1)
        runtime.offer_allocation_models(packet,decode_dynamic_allocation_model_publication(packet,runtime._allocation,1))
        await runtime._dispatch(runtime._snapshot())
        original=collector._latest_snapshots[('trade','005930')]['event']['payload']['current_price']
        collector._latest_snapshots[('trade','005930')]['event']['payload']['current_price']=original+1
        await runtime._refresh_position_inputs(runtime._snapshot())
        receipt=runtime.latest_results['position_allocation']
        self.assertEqual('BLOCKED',receipt['quality']['status']);self.assertFalse(receipt['order_authorized'])
        collector._latest_snapshots[('trade','005930')]['event']['payload']['current_price']=original
        bad=observation.risk_snapshot.to_dict();bad.pop('snapshot_id');bad['execution_run_id']='other-run';bad['snapshot_id']=_sha256(bad)
        source.return_value=replace(observation,risk_snapshot=mock_automation_risk_snapshot_from_dict(bad))
        await runtime._refresh_position_inputs(runtime._snapshot())
        self.assertIsNone(runtime.latest_results['position_allocation']['result'])
        self.assertEqual('HALT_NEW_ORDERS',runtime.latest_results['position_allocation']['risk_disposition'])

    async def test_current_hold_exit_and_reduce_keep_valid_upper_goals_without_buy_orders(self):
        import json
        from dataclasses import asdict,replace
        from kiwoom_monitor.application.position_target import _json_value
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import (
            compile_dynamic_allocation_ram_selection,prepare_dynamic_allocation_model_publication,
            decode_dynamic_allocation_model_publication)
        for count in (2,8):
            parent,collector,clock,source,observation,document,packet=self.prepare(count)
            held=observation.account.positions['005930'];price=packet['rows'][0]['ev_receipt']['evidence']['reference_price_won']
            cases=[]
            for action,budget in (('HOLD',held*price),('EXIT',0),('REDUCE',6*price)):
                policy=replace(parent._allocation['policy'],operating_limit_won=budget)
                selected={**document,'policy':json.loads(json.dumps(asdict(policy),default=_json_value))}
                rows=prepare_dynamic_allocation_model_publication(selected,packet['rows'],expected_selection_revision=1)
                runtime=MarketThemeShadowRuntime(parent._hub,collector,parent._bindings,
                    position_account_source=source,position_allocation=compile_dynamic_allocation_ram_selection(selected))
                self.addAsyncCleanup(runtime.close)
                runtime.offer_allocation_models(rows,decode_dynamic_allocation_model_publication(rows,runtime._allocation,1))
                cases.append((action,budget,runtime))
            for action,budget,runtime in cases:
                with self.subTest(consumers=count,action=action):
                    source.reset_mock()
                    with patch('sqlite3.connect',side_effect=AssertionError('hold/exit hot DB')):
                        await runtime._dispatch(runtime._snapshot())
                    receipt=runtime.latest_results['position_allocation']
                    self.assertEqual('CALCULATED',receipt['quality']['status'],receipt)
                    upper=receipt['result'];lower=receipt['lower_targets'][0]
                    self.assertEqual(action,lower['action']);self.assertEqual(budget,lower['target_notional_won'])
                    self.assertEqual(budget//price,lower['target_quantity'])
                    self.assertEqual(0,upper['allocations'][0]['new_buy_quantity'])
                    self.assertEqual(0,upper['new_buy_cash_won'])
                    self.assertEqual(upper['decision_id'],lower['source_decision_ref'])
                    self.assertEqual('CALCULATED',lower['quality']['status']);self.assertFalse(lower['order_authorized'])
                    self.assertEqual('EV_UNAVAILABLE',receipt['r4_action_comparison']['status'])
                    self.assertEqual(1,source.call_count)
                    original_expiry=receipt['valid_until'];clock[0]=datetime.fromisoformat(original_expiry)
                    expired=runtime.latest_results['position_allocation']
                    self.assertIsNone(expired['result']);self.assertNotIn('lower_targets',expired)
                    clock[0]=datetime.fromisoformat(receipt['decision_time'])

    async def test_client_prepares_and_transmits_fixed_receipts_without_source_read(self):
        from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
        _,_,_,_,_,selection,packet=self.prepare(1)
        client=CentralContentClient('http://offline','offline-token')
        with patch('sqlite3.connect',side_effect=AssertionError('client source reread')),patch.object(client,'_request',return_value={'accepted':True}) as send:
            prepared=client.prepare_dynamic_allocation_models(selection,packet['rows'],expected_selection_revision=1)
            self.assertEqual(packet,prepared)
            self.assertEqual({'accepted':True},client.publish_dynamic_allocation_models(prepared))
        send.assert_called_once_with('POST','/api/v1/research/ram-shadow/allocation-models',packet)


if __name__ == "__main__":
    unittest.main()
