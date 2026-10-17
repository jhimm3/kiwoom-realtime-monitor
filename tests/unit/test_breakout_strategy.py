from __future__ import annotations

import tempfile
import unittest
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch

from kiwoom_monitor.application.breakout_strategy import (
    BreakoutStrategyConfig,
    StrategyState,
    evaluate_breakout_bar,
    RealtimeBreakoutPolicy,
    evaluate_realtime_breakout,
)
from kiwoom_monitor.application.research_replay import (
    CandidateUniverseFrame,
    KrxMinuteBarFrame,
)
from kiwoom_monitor.application.research_execution import (
    SimulationCostModel,
    SimulationExecutionConfig,
)
from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec, ResearchFoldSpec
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset
from scripts.run_research import execute_rank_comparison, execute_research


UTC = timezone.utc


def _bar(index: int, close: int, high: int) -> KrxMinuteBarFrame:
    start = datetime(2026, 9, 12, 0, index, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    return KrxMinuteBarFrame(
        revision_id=f"bar-{index}", observation_key=start.isoformat(), code="005930",
        bar_start=start.isoformat(), bar_end=end.isoformat(),
        available_at=(end + timedelta(seconds=2)).isoformat(), open=close - 5,
        high=high, low=close - 10, close=close, volume=100,
        trade_value_million_won=1, session_finalized=False,
        capture_quality="complete", finalization_source="timer",
    )


def _universe(at: datetime, codes: tuple[str, ...], revision: str = "rank") -> CandidateUniverseFrame:
    return CandidateUniverseFrame(revision, at.isoformat(), at.isoformat(), codes)


def _config(**changes) -> BreakoutStrategyConfig:
    values = dict(
        strategy_version="v1", rolling_factor_version="v1", rank_factor_version="v1",
        lookback_bars=2, buffer_bps=0,
        rank_persistence_enabled=False, rank_persistence_required=False,
        rank_top_k=1, rank_window_seconds=60, rank_max_gap_seconds=30,
        rank_min_residency_seconds=30,
        stop_loss_bps=300, target_bps=500, max_hold_minutes=10,
        quantity=1, capital_won=1_000_000, signal_valid_seconds=60,
        cooldown_seconds=30,
    )
    values.update(changes)
    return BreakoutStrategyConfig(**values)


def _execution_config() -> SimulationExecutionConfig:
    return SimulationExecutionConfig(
        version="next_tradable_bar_open/v1",
        same_bar_path_version="conservative_with_optimistic_bound/v1",
        initial_cash_won=1_000_000,
        cost_model=SimulationCostModel(
            "fixed_bps/v1", 0, 0, 0, "model_estimate", "fixture",
            "2026-09-11T00:00:00+00:00", "2026-09-13T00:00:00+00:00",
        ),
    )


def _evaluation_spec() -> ResearchEvaluationSpec:
    return ResearchEvaluationSpec(
        "chronological_holdout/v1",
        (ResearchFoldSpec(
            "train", "TRAIN", "2026-09-12T00:00:00+00:00",
            "2026-09-12T01:00:00+00:00",
        ),),
        0, 0, 0, 0, 0,
    )


class BreakoutStrategyTests(unittest.TestCase):
    def test_breakout_creates_one_candidate_and_unchanged_condition_does_not_reissue(self) -> None:
        history = (_bar(0, 1000, 1010), _bar(1, 1010, 1020))
        evaluation_bar = _bar(2, 1030, 1040)
        universe = (_universe(datetime(2026, 9, 12, 0, 2, tzinfo=UTC), ("005930",)),)
        first = evaluate_breakout_bar(
            run_id="run-a", evaluation_bar=evaluation_bar, bar_history=history,
            universe_frames=universe, config=_config(), state=StrategyState(),
        )
        second = evaluate_breakout_bar(
            run_id="run-a", evaluation_bar=evaluation_bar, bar_history=history,
            universe_frames=universe, config=_config(), state=first.state,
        )
        self.assertEqual("ENTER", first.decision.final_action)
        self.assertEqual(1, first.decision.quantity)
        self.assertEqual(1030, first.decision.required_capital_won)
        self.assertIsNotNone(first.candidate_event)
        self.assertEqual("candidate", first.state.status)
        self.assertEqual("HOLD", second.decision.final_action)
        self.assertIsNone(second.candidate_event)

    def test_required_and_optional_rank_missing_policies_are_distinct(self) -> None:
        history = (_bar(0, 1000, 1010), _bar(1, 1010, 1020))
        bar = _bar(2, 1030, 1040)
        universe = (_universe(datetime(2026, 9, 12, 0, 2, tzinfo=UTC), ("005930",)),)
        required = evaluate_breakout_bar(
            run_id="run-required", evaluation_bar=bar, bar_history=history,
            universe_frames=universe,
            config=_config(rank_persistence_enabled=True, rank_persistence_required=True),
            state=StrategyState(),
        )
        optional = evaluate_breakout_bar(
            run_id="run-optional", evaluation_bar=bar, bar_history=history,
            universe_frames=universe,
            config=_config(rank_persistence_enabled=True, rank_persistence_required=False),
            state=StrategyState(),
        )
        self.assertEqual("NO_TRADE", required.decision.final_action)
        self.assertEqual("ENTER", optional.decision.final_action)
        self.assertIn("optional_rank_factor_missing", optional.decision.reasons[1])

    def test_disabled_rank_parameters_do_not_change_judgment(self) -> None:
        history = (_bar(0, 1000, 1010), _bar(1, 1010, 1020))
        bar = _bar(2, 1030, 1040)
        universe = (_universe(datetime(2026, 9, 12, 0, 2, tzinfo=UTC), ("005930",)),)
        first = evaluate_breakout_bar(
            run_id="same", evaluation_bar=bar, bar_history=history,
            universe_frames=universe, config=_config(rank_top_k=1), state=StrategyState(),
        )
        second = evaluate_breakout_bar(
            run_id="same", evaluation_bar=bar, bar_history=history,
            universe_frames=universe, config=_config(rank_top_k=20), state=StrategyState(),
        )
        self.assertEqual(first, second)

    def test_future_universe_does_not_admit_past_entry_and_open_exit_ignores_rank(self) -> None:
        history = (_bar(0, 1000, 1010), _bar(1, 1010, 1020))
        bar = _bar(2, 1030, 1040)
        future = _universe(datetime(2026, 9, 12, 0, 4, tzinfo=UTC), ("005930",), "future")
        rejected = evaluate_breakout_bar(
            run_id="past", evaluation_bar=bar, bar_history=history,
            universe_frames=(future,), config=_config(), state=StrategyState(),
        )
        opened = StrategyState(
            status="open", symbol="005930", entry_price=1100,
            position_quantity=1,
            opened_at=datetime(2026, 9, 12, 0, 0, tzinfo=UTC).isoformat(),
        )
        exit_decision = evaluate_breakout_bar(
            run_id="open", evaluation_bar=bar, bar_history=history,
            universe_frames=(), config=_config(), state=opened,
        )
        self.assertEqual("candidate_universe_missing", rejected.decision.reasons[0])
        self.assertEqual("EXIT", exit_decision.decision.final_action)
        self.assertIn("stop_loss_reached", exit_decision.decision.reasons)

    def test_unknown_strategy_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unregistered strategy"):
            _config(strategy_version="v999")
        with self.assertRaisesRegex(ValueError, "unregistered factor"):
            _config(rank_factor_version="v999")

    def test_configured_capital_limits_entry_size(self) -> None:
        history = (_bar(0, 1000, 1010), _bar(1, 1010, 1020))
        bar = _bar(2, 1030, 1040)
        universe = (_universe(datetime(2026, 9, 12, 0, 2, tzinfo=UTC), ("005930",)),)
        result = evaluate_breakout_bar(
            run_id="capital", evaluation_bar=bar, bar_history=history,
            universe_frames=universe,
            config=_config(quantity=100, capital_won=1000), state=StrategyState(),
        )
        self.assertEqual("NO_TRADE", result.decision.final_action)
        self.assertEqual("configured_capital_insufficient", result.decision.reasons[0])

    def test_empty_dataset_has_same_logical_result_in_independent_runs(self) -> None:
        manifest = {"dataset_id": "empty", "revision_ids_hash": "zero", "revision_count": 0}
        dataset = FrozenResearchDataset(manifest, ())
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = execute_research(
                dataset, ResearchRepository(Path(first_dir) / "research.sqlite3"),
                Path(first_dir) / "runs", _config(), _execution_config(),
            )
            second = execute_research(
                dataset, ResearchRepository(Path(second_dir) / "research.sqlite3"),
                Path(second_dir) / "runs", _config(), _execution_config(),
            )
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(first.logical_result_hash, second.logical_result_hash)
        self.assertEqual(0, first.decision_count)

    def test_historical_reconstruction_population_runs_without_rank_semantics(self) -> None:
        observations = [{
            "ordinal": 1, "accepted_sequence": 1, "revision_id": "population",
            "kind": "historical_candidate_population", "subject": "2026-09-11",
            "observation_key": "2026-09-11", "available_at": "2026-09-12T00:00:00+00:00",
            "payload": {"codes": ["005930"], "not_contemporaneous_top20": True},
        }]
        for index, (close, high) in enumerate(((1000, 1010), (1010, 1020), (1030, 1040))):
            bar = _bar(index, close, high)
            observations.append({
                "ordinal": index + 2, "accepted_sequence": index + 2,
                "revision_id": bar.revision_id, "kind": "minute_bar",
                "subject": "005930:KRX", "venue": "KRX",
                "observation_key": bar.observation_key, "available_at": bar.bar_end,
                "completeness": "complete", "value_kind": "actual", "payload": {
                    "market": "KRX", "code": "005930", "bar_start": bar.bar_start,
                    "bar_end": bar.bar_end, "open": bar.open, "high": bar.high,
                    "low": bar.low, "close": bar.close, "volume": bar.volume,
                    "trade_value_million_won": bar.trade_value_million_won,
                    "window_closed": True, "capture_quality": "complete",
                    "finalization_source": "daishin_completed_history_job",
                    "source_available_at": "2026-09-22T00:00:00+00:00",
                },
            })
        revision_ids = [row["revision_id"] for row in observations]
        dataset = FrozenResearchDataset({
            "runtime_input_version": "historical_reconstruction_strategy/v1",
            "dataset_id": "historical-fixture", "revision_count": len(observations),
            "revision_ids_hash": hashlib.sha256("\n".join(revision_ids).encode()).hexdigest(),
            "not_contemporaneous_top20": True,
        }, tuple(observations))
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            result = execute_research(
                dataset, repository, Path(directory) / "runs", _config(), _execution_config(),
                _evaluation_spec(),
            )
            report = repository.load_research_report(result.run_id)
            with self.assertRaisesRegex(ValueError, "does not support.*rank"):
                execute_research(
                    dataset, repository, Path(directory) / "rank-runs",
                    _config(rank_persistence_enabled=True), _execution_config(),
                )
        self.assertEqual(3, result.decision_count)
        self.assertEqual(1, result.candidate_count)
        self.assertNotIn("candidate_universe_missing", report["data_quality"]["reasons"])
        self.assertIn(
            "posthoc_candidate_population_not_contemporaneous_top20",
            report["limitations"],
        )

    def test_sparse_historical_days_keep_unfilled_exit_and_do_not_trade_new_cohort(self) -> None:
        from datetime import date
        from tests.unit.test_research_bundle_execution import rows_for
        from scripts.summarize_historical_baseline_results import position_observation_summary
        observations = []
        for day, code, late in ((date(2026, 9, 14), "005930", True), (date(2026, 9, 15), "000660", False)):
            rows = rows_for(day, 11 if late else 8)
            for row in rows:
                if row["kind"] == "top20_membership":
                    row["kind"] = "historical_candidate_population"
                    row["payload"]["codes"] = [code]
                else:
                    row["subject"] = code + ":KRX"
                    row["payload"]["code"] = code
                if late:
                    offset = timedelta(minutes=370)
                    for key in ("available_at", "observation_key"):
                        row[key] = (datetime.fromisoformat(row[key]) + offset).isoformat()
                    if row["kind"] == "minute_bar":
                        for key in ("bar_start", "bar_end"):
                            row["payload"][key] = (datetime.fromisoformat(row["payload"][key]) + offset).isoformat()
                        if row["payload"]["bar_start"][11:16] == "15:20":
                            row["payload"]["bar_end"] = "2026-09-14T15:30:00+09:00"
                            row["available_at"] = "2026-09-14T15:30:02+09:00"
                observations.append(row)
        dataset = FrozenResearchDataset({
            "runtime_input_version": "historical_reconstruction_strategy/v1",
            "dataset_id": "sparse-cohort-fixture", "revision_count": len(observations),
            "revision_ids_hash": "fixture-revisions", "schema_version": 3,
            "not_contemporaneous_top20": True,
        }, tuple(observations), ())
        execution = _execution_config()
        execution = replace(execution, cost_model=replace(execution.cost_model,
                            valid_from="2026-09-14T00:00:00+09:00", valid_to="2026-09-16T00:00:00+09:00"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = ResearchRepository(root / "research.sqlite3")
            result = execute_research(dataset, repository, root / "runs", _config(), execution,
                                      session_profile="krx-regular/v1")
            events = repository.load_execution_events(result.run_id)
            decisions = repository.load_evaluations(result.run_id)
            followup = [row for row in decisions if row["symbol"] == "000660"]
            self.assertTrue(followup)
            self.assertTrue(all(row["final_action"] == "HOLD" for row in followup))
            self.assertTrue(all("different_symbol_position_managed_elsewhere" in row["reasons"] for row in followup))
            summary = position_observation_summary(events)
            self.assertEqual("005930", summary["symbol"])
            self.assertEqual(1, summary["exit_request_count"])
            self.assertEqual({"session_boundary_before_fill": 1}, summary["exit_censored_reasons"])
            final = json.loads(result.output_manifest.read_text(encoding="utf-8"))
            self.assertEqual("005930", final["final_portfolio"]["position"]["symbol"])
            self.assertFalse(any(event["side"] == "SELL" and event["state"] == "FILLED" for event in events))

    def test_disabled_rank_parameters_do_not_change_run_identity(self) -> None:
        manifest = {"dataset_id": "empty", "revision_ids_hash": "zero", "revision_count": 0}
        dataset = FrozenResearchDataset(manifest, ())
        with tempfile.TemporaryDirectory() as first_dir, tempfile.TemporaryDirectory() as second_dir:
            first = execute_research(
                dataset, ResearchRepository(Path(first_dir) / "research.sqlite3"),
                Path(first_dir) / "runs", _config(rank_top_k=1), _execution_config(),
            )
            second = execute_research(
                dataset, ResearchRepository(Path(second_dir) / "research.sqlite3"),
                Path(second_dir) / "runs", _config(rank_top_k=20), _execution_config(),
            )
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(first.logical_result_hash, second.logical_result_hash)

    def test_rank_comparison_runs_and_persists_both_locked_variants(self) -> None:
        digest = hashlib.sha256(b"").hexdigest()
        dataset = FrozenResearchDataset({
            "dataset_id": "empty", "revision_ids_hash": digest, "revision_count": 0,
        }, ())
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            result = execute_rank_comparison(
                dataset, repository, Path(directory) / "runs",
                _config(rank_persistence_enabled=True, rank_persistence_required=True),
                _execution_config(), _evaluation_spec(),
            )
            stored = repository.load_research_comparison(result.comparison_id)
            self.assertTrue(result.output_manifest.is_file())
        self.assertNotEqual(result.baseline_run_id, result.variant_run_id)
        self.assertEqual("INELIGIBLE", result.status)
        self.assertEqual(result.comparison_id, stored["comparison_id"])

    def test_runner_replays_available_revisions_and_emits_one_candidate(self) -> None:
        observations = [{
            "ordinal": 1, "accepted_sequence": 1, "revision_id": "rank-0",
            "kind": "top20_membership", "subject": "2026-09-12",
            "observation_key": "rank-0", "available_at": "2026-09-12T00:00:00+00:00",
            "payload": {"codes": ["005930"]},
        }]
        for index, (close, high) in enumerate(((1000, 1010), (1010, 1020), (1030, 1040))):
            bar = _bar(index, close, high)
            observations.append({
                "ordinal": index + 2, "accepted_sequence": index + 2,
                "revision_id": bar.revision_id, "kind": "minute_bar", "subject": "005930",
                "observation_key": bar.observation_key, "venue": "KRX",
                "available_at": bar.available_at, "completeness": "complete",
                "value_kind": "actual", "payload": {
                    "market": "KRX", "code": "005930", "bar_start": bar.bar_start,
                    "bar_end": bar.bar_end, "open": bar.open, "high": bar.high,
                    "low": bar.low, "close": bar.close, "volume": bar.volume,
                    "trade_value_million_won": bar.trade_value_million_won,
                    "window_closed": True, "capture_quality": "complete",
                    "finalization_source": "timer",
                },
            })
        revision_ids = [row["revision_id"] for row in observations]
        manifest = {
            "dataset_id": "fixture", "revision_count": len(observations),
            "revision_ids_hash": hashlib.sha256("\n".join(revision_ids).encode()).hexdigest(),
        }
        dataset = FrozenResearchDataset(manifest, tuple(observations))
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            result = execute_research(
                dataset, repository, Path(directory) / "runs", _config(), _execution_config(),
                _evaluation_spec(),
            )
            repeated = execute_research(
                dataset, repository, Path(directory) / "runs", _config(), _execution_config(),
                _evaluation_spec(),
            )
            decisions = repository.load_evaluations(result.run_id)
            execution_events = repository.load_execution_events(result.run_id)
            outcomes = repository.load_outcome_labels(result.run_id)
            performance = repository.load_run_evaluation(result.run_id)
            report = repository.load_research_report(result.run_id)
        self.assertEqual(3, result.decision_count)
        self.assertEqual(result, repeated)
        self.assertEqual(1, result.candidate_count)
        self.assertEqual(2, result.execution_event_count)
        self.assertEqual(5, result.outcome_label_count)
        self.assertEqual("NOT_APPLICABLE", result.performance_status)
        self.assertEqual("ENTER", decisions[-1]["final_action"])
        self.assertEqual(2, len(execution_events))
        self.assertEqual(5, len(outcomes))
        self.assertEqual("NOT_APPLICABLE", performance["status"])
        self.assertEqual("NOT_APPLICABLE", result.report_status)
        self.assertEqual("NOT_APPLICABLE", report["status"])


class RealtimeBreakoutTests(unittest.TestCase):
    """Observed-second eligibility only; no historical performance assertions."""

    def setUp(self) -> None:
        self.base = datetime(2026, 10, 14, 1, 0, tzinfo=UTC)
        self.policy = RealtimeBreakoutPolicy(10, 0, 200, 3)
        self.snapshot = {
            "decision_time": (self.base + timedelta(seconds=20)).isoformat(),
            "source_id": "fixture-collector", "generation": 1, "durable": False,
            "approved_sources": (("005930", "KRX"),),
            "continuous_from": {("005930", "KRX"): self.base},
            "retention_since": self.base, "gap_at": None,
            "rows": tuple(self.row(index, 100 if index <= 10 else 110) for index in range(1, 21)),
        }

    def row(self, second, price, turnover=20, **changes):
        at = self.base + timedelta(seconds=second)
        value = dict(
            trading_date="2026-10-14", trade_second=f"10:00:{second:02d}",
            code="005930", market="KRX", open=price, high=price, low=price, close=price,
            volume=1, trade_value_won=turnover, trade_count=1, available_at=at.timestamp(),
        )
        value.update(changes)
        return value

    def evaluate(self, snapshot=None, state=None, policy=None):
        return evaluate_realtime_breakout(
            self.snapshot if snapshot is None else snapshot, symbol="005930",
            policy=policy or self.policy, state=state,
        )

    def test_first_candidate_is_theme_free_research_and_unchanged_episode_is_not_reissued(self):
        first = self.evaluate()
        self.assertEqual(("CANDIDATE", "ENTER", True), (first.status, first.proposal, first.new_candidate))
        self.assertFalse(first.order_authorized)
        self.assertEqual((100, 110, 200), (first.reference_high, first.observed_price, first.current_trade_value_won))
        self.assertEqual((), first.missing_data_capabilities)
        self.assertTrue(all(ref.startswith("ram:fixture-collector:1:") for ref in first.input_refs))
        self.assertNotIn("theme", " ".join(first.required_data_capabilities))
        repeat = self.evaluate(state=first.state)
        self.assertFalse(repeat.new_candidate)
        self.assertEqual(first.state.episode_id, repeat.state.episode_id)
        self.assertEqual("2026-10-14T01:00:23+00:00", first.valid_until)
        self.assertEqual(first, self.evaluate())

    def test_window_boundaries_exclude_current_price_from_reference(self):
        snapshot = dict(self.snapshot, rows=(self.row(0, 9999), self.row(10, 100), self.row(20, 110, 200)))
        result = self.evaluate(snapshot)
        self.assertTrue(result.new_candidate)
        self.assertEqual((100, 200), (result.reference_high, result.current_trade_value_won))

    def test_strict_price_buffer_and_inclusive_turnover_threshold(self):
        exact = self.evaluate(policy=replace(self.policy, buffer_bps=1000))
        self.assertEqual(("WATCH", "observed_high_not_broken"), (exact.status, exact.reasons[0]))
        short = self.evaluate(policy=replace(self.policy, minimum_trade_value_won=201))
        self.assertEqual("current_turnover_below_threshold", short.reasons[0])

    def test_fixed_structure_survives_rolling_high_change_then_invalidates(self):
        first = self.evaluate()
        snapshot = dict(self.snapshot, decision_time=(self.base + timedelta(seconds=21)).isoformat(),
                        rows=self.snapshot["rows"] + (self.row(21, 109),))
        kept = self.evaluate(snapshot, state=first.state)
        self.assertEqual(100, kept.reference_high)  # rolling previous window now includes price 110
        self.assertEqual("CANDIDATE", kept.status)
        self.assertEqual(first.state.reference_input_refs, kept.state.reference_input_refs)
        self.assertTrue(set(first.state.reference_input_refs).issubset(kept.input_refs))
        failed = dict(snapshot, rows=self.snapshot["rows"] + (self.row(21, 100),))
        invalid = self.evaluate(failed, state=kept.state)
        self.assertEqual(("INVALIDATED", "WAIT"), (invalid.status, invalid.proposal))
        self.assertEqual("fixed_breakout_structure_lost", invalid.reasons[0])
        self.assertFalse(invalid.order_authorized)

    def test_turnover_slowdown_waits_without_exit_or_episode_reissue(self):
        first = self.evaluate()
        slow = dict(self.snapshot, decision_time=(self.base + timedelta(seconds=21)).isoformat(),
                    rows=tuple({**row, "trade_value_won": 1} for row in self.snapshot["rows"])
                    + (self.row(21, 110, 1),))
        waiting = self.evaluate(slow, state=first.state)
        self.assertEqual(("WATCH", "WAIT"), (waiting.status, waiting.proposal))
        self.assertEqual("CANDIDATE", waiting.state.status)
        self.assertEqual(slow["decision_time"], waiting.state.last_cut)
        resumed = self.evaluate(dict(slow, rows=self.snapshot["rows"] + (self.row(21, 110),)), state=waiting.state)
        self.assertFalse(resumed.new_candidate)
        self.assertEqual(first.state.episode_id, resumed.state.episode_id)

    def test_gap_reconnect_and_unapproved_source_block_without_claiming_price_failure(self):
        first = self.evaluate()
        cases = (
            dict(self.snapshot, gap_at=self.base + timedelta(seconds=1)),
            dict(self.snapshot, retention_since=None),
            dict(self.snapshot, continuous_from={}),
            dict(self.snapshot, approved_sources=()),
            dict(self.snapshot, source_id="reconnect", generation=2,
                 continuous_from={("005930", "KRX"): self.base + timedelta(seconds=19)}),
        )
        for snapshot in cases:
            with self.subTest(snapshot=snapshot["source_id"], bounds=snapshot.get("gap_at")):
                result = self.evaluate(snapshot, state=first.state)
                self.assertEqual("DATA_BLOCKED", result.status)
                self.assertEqual("", result.state.episode_id)
                self.assertTrue(result.missing_data_capabilities)
                self.assertNotIn("fixed_breakout_structure_lost", result.reasons)

    def test_future_prices_and_future_availability_do_not_change_past_cut(self):
        baseline = self.evaluate()
        snapshot = dict(self.snapshot, rows=self.snapshot["rows"] + (
            self.row(21, 9999), self.row(19, 9999, available_at=(self.base + timedelta(seconds=21)).timestamp()),
        ))
        self.assertEqual(baseline, self.evaluate(snapshot))

    def test_unavailable_malformed_future_rows_do_not_change_past_decision(self):
        baseline = self.evaluate()
        future = dict(self.snapshot, rows=self.snapshot["rows"] + (
            self.row(21, 9999, high=1),
            self.row(19, 9999, high=1, available_at=(self.base + timedelta(seconds=21)).timestamp()),
        ))
        self.assertEqual(baseline, self.evaluate(future))

    def test_fixed_candidate_can_invalidate_when_previous_window_has_no_prints(self):
        first = self.evaluate()
        sparse = dict(self.snapshot, decision_time=(self.base + timedelta(seconds=40)).isoformat(),
                      rows=(self.row(40, 100, 1),))
        result = self.evaluate(sparse, state=first.state)
        self.assertEqual("INVALIDATED", result.status)
        self.assertEqual("fixed_breakout_structure_lost", result.reasons[0])
        self.assertEqual(first.state.reference_input_refs, result.state.reference_input_refs)
        continued = self.evaluate(dict(sparse, rows=(self.row(40, 110, 200),)), state=first.state)
        self.assertEqual("CANDIDATE", continued.status)
        self.assertFalse(continued.new_candidate)
        self.assertEqual(first.state.episode_id, continued.state.episode_id)
        interrupted = self.evaluate(dict(sparse, gap_at=self.base + timedelta(seconds=39)), state=first.state)
        self.assertEqual("DATA_BLOCKED", interrupted.status)

    def test_fresh_receipt_of_delayed_price_is_still_stale(self):
        snapshot = dict(self.snapshot, decision_time=(self.base + timedelta(seconds=24)).isoformat(),
                        rows=tuple({**row, "available_at": (self.base + timedelta(seconds=24)).timestamp()}
                                   for row in self.snapshot["rows"]))
        result = self.evaluate(snapshot)
        self.assertEqual("observed_price_stale_or_delayed", result.reasons[0])
        self.assertEqual("DATA_BLOCKED", result.status)
        self.assertEqual("2026-10-14T01:00:23+00:00", result.valid_until)

    def test_no_print_seconds_are_not_synthetic_trades_or_broker_loss_evidence(self):
        sparse = dict(self.snapshot, rows=(self.row(10, 100), self.row(20, 110, 200)))
        self.assertTrue(self.evaluate(sparse).new_candidate)
        empty = dict(self.snapshot, rows=(self.row(20, 110, 200),))
        self.assertEqual("observed_window_price_missing", self.evaluate(empty).reasons[0])

    def test_malformed_duplicate_and_oversized_rows_fail_closed(self):
        for rows, reason in (
            (self.snapshot["rows"] + (self.row(19, 110),), "duplicate_second_trade_row"),
            ((self.row(10, 100), self.row(20, 110, close=0)), "invalid_second_trade_row"),
            ((self.row(10, 100), self.row(20, 110, high=100)), "invalid_second_trade_row"),
            (self.snapshot["rows"] * 4, "ram_second_rows_exceed_budget"),
        ):
            with self.subTest(reason=reason):
                self.assertEqual(reason, self.evaluate(dict(self.snapshot, rows=rows)).reasons[0])

    def test_old_cut_does_not_rewind_a_new_episode_or_source(self):
        first = self.evaluate()
        older = dict(self.snapshot, decision_time=(self.base + timedelta(seconds=19)).isoformat(), generation=0)
        result = self.evaluate(older, state=first.state)
        self.assertEqual("out_of_order_cut", result.reasons[0])
        self.assertEqual(first.state, result.state)

    def test_existing_runtime_delivery_gap_generation_is_preserved(self):
        first = self.evaluate()
        gap = dict(self.snapshot, generation="1:delivery-2", gap_at=self.base + timedelta(seconds=19))
        blocked = self.evaluate(gap, state=first.state)
        self.assertEqual("DATA_BLOCKED", blocked.status)
        self.assertEqual("1:delivery-2", blocked.state.generation)
        self.assertEqual("", blocked.state.episode_id)
        warmed = dict(gap, decision_time=(self.base + timedelta(seconds=40)).isoformat(),
                      rows=(self.row(30, 100), self.row(40, 110, 200)))
        fresh = self.evaluate(warmed, state=blocked.state)
        self.assertTrue(fresh.new_candidate)
        self.assertNotEqual(first.state.episode_id, fresh.state.episode_id)
        self.assertTrue(all(ref.startswith("ram:fixture-collector:1:delivery-2:") for ref in fresh.input_refs))

    def test_policy_inputs_are_explicit_and_state_cannot_cross_symbol_or_settings(self):
        for changes in ({"minimum_trade_value_won": 0}, {"window_seconds": 31}, {"buffer_bps": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.policy, **changes)
        first = self.evaluate()
        with self.assertRaises(ValueError):
            self.evaluate(state=first.state, policy=replace(self.policy, buffer_bps=1))
        with self.assertRaises(ValueError):
            self.evaluate(state=replace(first.state, symbol="000660"))
        with self.assertRaises(ValueError):
            replace(first, order_authorized=True)

    def test_other_venues_symbols_and_out_of_session_observations_do_not_admit_entry(self):
        others = tuple({**row, "market": "NXT"} for row in self.snapshot["rows"])
        self.assertEqual(self.evaluate(), self.evaluate(dict(self.snapshot, rows=self.snapshot["rows"] + others)))
        outside = dict(self.snapshot, decision_time="2026-10-14T08:00:20+09:00",
                       retention_since="2026-10-14T07:00:00+09:00",
                       continuous_from={("005930", "KRX"): "2026-10-14T07:00:00+09:00"})
        self.assertEqual("outside_regular_continuous_session", self.evaluate(outside).reasons[0])

    def test_repeated_pure_evaluation_keeps_input_unchanged_and_does_not_use_io_or_h2(self):
        original = deepcopy(self.snapshot)
        first = self.evaluate()
        with patch("sqlite3.connect", side_effect=AssertionError("unexpected DB")), \
                patch("builtins.open", side_effect=AssertionError("unexpected file IO")), \
                patch("kiwoom_monitor.application.theme_leadership.evaluate_theme_leadership",
                      side_effect=AssertionError("unexpected H2 recomputation")):
            for _ in range(100):
                self.assertFalse(self.evaluate(state=first.state).new_candidate)
        self.assertEqual(original, self.snapshot)

    def test_existing_0b_ram_accumulator_rows_are_consumed_without_draining(self):
        from kiwoom_monitor.central_server.minute_bars import SecondTradeAccumulator
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick

        accumulator = SecondTradeAccumulator()
        accumulator.set_ram_retention(20)
        kst = timezone(timedelta(hours=9))
        for second in range(1, 21):
            now = (self.base + timedelta(seconds=second)).astimezone(kst)
            accumulator.add(TradeTick(
                code="005930", current_price=100 if second <= 10 else 110,
                cumulative_volume=second, cumulative_trade_value=None, trade_volume=1,
                high_price=110, trade_time=now.strftime("%H%M%S"), market="KRX",
            ), now=now, received_at=now.timestamp())
        rows = accumulator.observation_snapshot(("005930",), at=now, seconds=20)
        result = self.evaluate(dict(self.snapshot, rows=rows))
        self.assertEqual((100, 110, 1100), (result.reference_high, result.observed_price, result.current_trade_value_won))
        self.assertTrue(result.new_candidate)
        self.assertEqual(rows, accumulator.observation_snapshot(("005930",), at=now, seconds=20))


if __name__ == "__main__":
    unittest.main()
