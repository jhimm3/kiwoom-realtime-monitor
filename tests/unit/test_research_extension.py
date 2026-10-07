from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.breakout_strategy import StrategyState
from kiwoom_monitor.application.pullback_reacceleration_strategy import (
    FAMILY_ID,
    FAMILY_CONTRACT,
    PullbackReaccelerationConfig,
    evaluate_pullback_reacceleration_bar,
)
from kiwoom_monitor.application.research_execution import (
    SimulationCostModel,
    SimulationExecutionConfig,
)
from kiwoom_monitor.application.research_factors import (
    PullbackReaccelerationParameters,
    compute_pullback_reacceleration,
)
from kiwoom_monitor.application.research_families import get_research_family
from kiwoom_monitor.application.research_replay import CandidateUniverseFrame, KrxMinuteBarFrame
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset
from scripts.run_research import execute_research


UTC = timezone.utc


def _bar(index: int, *, open_: int, high: int, low: int, close: int) -> KrxMinuteBarFrame:
    start = datetime(2026, 9, 12, 0, index, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    return KrxMinuteBarFrame(
        revision_id=f"bar-{index}", observation_key=start.isoformat(), code="005930",
        bar_start=start.isoformat(), bar_end=end.isoformat(),
        available_at=(end + timedelta(seconds=2)).isoformat(), open=open_, high=high,
        low=low, close=close, volume=100, trade_value_million_won=1,
        session_finalized=False, capture_quality="complete", finalization_source="timer",
    )


def _config(**changes) -> PullbackReaccelerationConfig:
    values = {
        "strategy_version": "v1", "pullback_factor_version": "v1",
        "rank_factor_version": "v1", "lookback_bars": 3,
        "minimum_pullback_bps": 500, "minimum_reacceleration_bps": 100,
        "rank_persistence_enabled": False, "rank_persistence_required": False,
        "rank_top_k": 1, "rank_window_seconds": 60, "rank_max_gap_seconds": 30,
        "rank_min_residency_seconds": 30, "stop_loss_bps": 300,
        "target_bps": 500, "max_hold_minutes": 10, "quantity": 1,
        "capital_won": 1_000_000, "signal_valid_seconds": 60,
        "cooldown_seconds": 30,
    }
    values.update(changes)
    return PullbackReaccelerationConfig(**values)


class ResearchExtensionTests(unittest.TestCase):
    def test_family_declares_compatible_inputs_state_exit_and_size_contract(self) -> None:
        definition = get_research_family(FAMILY_ID)
        self.assertEqual("flat/candidate/open/cooldown", FAMILY_CONTRACT["state_contract"])
        self.assertIn("minute_bar/KRX/closed/complete", FAMILY_CONTRACT["required_inputs"])
        self.assertEqual(PullbackReaccelerationConfig, definition.config_type)
        with self.assertRaisesRegex(ValueError, "unregistered strategy"):
            _config(strategy_version="v2")

    def test_pullback_factor_uses_peak_then_trough_and_current_reacceleration(self) -> None:
        history = (
            _bar(0, open_=990, high=1000, low=980, close=990),
            _bar(1, open_=1000, high=1100, low=1050, close=1080),
            _bar(2, open_=1030, high=1040, low=990, close=1000),
        )
        evaluation = _bar(3, open_=1005, high=1030, low=1000, close=1020)
        result = compute_pullback_reacceleration(
            history, evaluation, PullbackReaccelerationParameters(3, 500, 100),
        )
        self.assertEqual("valid", result.status)
        self.assertTrue(result.value["reaccelerated"])
        self.assertEqual(1000, result.value["pullback_bps"])
        self.assertEqual(200, result.value["reacceleration_bps"])
        self.assertEqual("bar-2", result.value["reference_revision_id"])

    def test_second_family_emits_its_own_setup_and_ignores_disabled_rank_parameters(self) -> None:
        history = (
            _bar(0, open_=990, high=1000, low=980, close=990),
            _bar(1, open_=1000, high=1100, low=1050, close=1080),
            _bar(2, open_=1030, high=1040, low=990, close=1000),
        )
        evaluation = _bar(3, open_=1005, high=1030, low=1000, close=1020)
        universe = (CandidateUniverseFrame(
            "rank-1", "rank-1", evaluation.available_at, ("005930",),
        ),)
        first = evaluate_pullback_reacceleration_bar(
            run_id="same", evaluation_bar=evaluation, bar_history=history,
            universe_frames=universe, config=_config(rank_top_k=1), state=StrategyState(),
        )
        second = evaluate_pullback_reacceleration_bar(
            run_id="same", evaluation_bar=evaluation, bar_history=history,
            universe_frames=universe, config=_config(rank_top_k=20), state=StrategyState(),
        )
        self.assertEqual(first, second)
        self.assertEqual("ENTER", first.decision.final_action)
        self.assertEqual("pullback_reacceleration", first.candidate_event.setup)
        self.assertEqual("krx_pullback_reacceleration", first.candidate_event.strategy_id)

    def test_second_family_runs_through_existing_simulation_without_changing_environment(self) -> None:
        dataset = FrozenResearchDataset(
            {"dataset_id": "empty", "revision_ids_hash": "zero", "revision_count": 0}, (),
        )
        execution = SimulationExecutionConfig(
            "next_tradable_bar_open/v1", "conservative_with_optimistic_bound/v1",
            1_000_000, SimulationCostModel("fixed_bps/v1", 0, 0, 0),
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            result = execute_research(
                dataset, repository, Path(directory) / "runs", _config(), execution,
            )
            stored = repository.load_run(result.run_id)
        self.assertEqual(FAMILY_ID, stored["spec"]["family"])
        self.assertEqual("historical_replay", stored["spec"]["mode"])


if __name__ == "__main__":
    unittest.main()
