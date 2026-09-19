from __future__ import annotations

import tempfile
import unittest
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.breakout_strategy import (
    BreakoutStrategyConfig,
    StrategyState,
    evaluate_breakout_bar,
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


if __name__ == "__main__":
    unittest.main()
