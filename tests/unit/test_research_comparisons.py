from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.application.research_evaluation import build_research_comparison
from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec, ResearchFoldSpec
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository


def _spec() -> ResearchEvaluationSpec:
    return ResearchEvaluationSpec(
        "chronological_holdout/v1",
        (ResearchFoldSpec(
            "train", "TRAIN", "2026-09-12T00:00:00+00:00",
            "2026-09-12T01:00:00+00:00",
        ),),
        0, 0, 0, 0, 0,
    )


def _report(run_id: str, net: int) -> dict:
    return {
        "run_id": run_id,
        "fold_reports": [{
            "name": "train", "role": "TRAIN", "status": "ELIGIBLE", "reasons": [],
            "net_realized_pnl_won": net,
            "event_outcomes": [{
                "horizon_seconds": 60, "sample_count": 2,
                "average_return_bps": 50 if run_id == "base" else 80,
            }],
        }],
    }


def _trade_events(prefix: str, decision: str, minute: int, realized: int) -> tuple[dict, dict]:
    return ({
        "event_id": f"{prefix}-buy", "event_type": "FILL", "state": "FILLED",
        "intent_id": f"{prefix}-intent", "decision_id": decision,
        "symbol": "005930", "side": "BUY", "quantity": 1,
        "occurred_at": f"2026-09-12T00:{minute:02d}:00+00:00",
        "notional_won": 1000, "total_cost_won": 0,
    }, {
        "event_id": f"{prefix}-sell", "event_type": "FILL", "state": "FILLED",
        "intent_id": f"{prefix}-exit", "decision_id": "",
        "symbol": "005930", "side": "SELL", "quantity": 1,
        "occurred_at": f"2026-09-12T00:{minute + 1:02d}:00+00:00",
        "notional_won": 1000 + realized, "total_cost_won": 0,
        "realized_pnl_won": realized,
    })


class ResearchComparisonTests(unittest.TestCase):
    def test_rank_filter_reports_avoided_loss_and_same_candidate_results(self) -> None:
        baseline_events = (
            *_trade_events("loss", "decision-loss", 5, -100),
            *_trade_events("win", "decision-win", 10, 200),
        )
        variant_events = _trade_events("kept", "decision-win-variant", 10, 200)
        comparison = build_research_comparison(
            baseline_run_id="base", variant_run_id="variant",
            changed_condition="rank_persistence_filter",
            input_dataset_id="dataset", input_revision_ids_hash="hash",
            baseline_report=_report("base", 100), variant_report=_report("variant", 200),
            baseline_candidates=(
                {"decision_id": "decision-loss", "dedup_key": "setup-loss"},
                {"decision_id": "decision-win", "dedup_key": "setup-win"},
            ),
            variant_candidates=(
                {"decision_id": "decision-win-variant", "dedup_key": "setup-win"},
            ),
            baseline_events=baseline_events, variant_events=variant_events, spec=_spec(),
        )
        fold = comparison.fold_comparisons[0]
        self.assertEqual("ELIGIBLE", comparison.status)
        self.assertEqual(1, fold.shared_candidate_count)
        self.assertEqual(1, fold.baseline_only_candidate_count)
        self.assertEqual(100, fold.avoided_loss_won)
        self.assertEqual(0, fold.missed_profit_won)
        self.assertEqual(100, fold.net_pnl_delta_won)
        self.assertEqual(30, fold.event_outcome_deltas[0]["average_return_bps_delta"])

    def test_comparison_is_immutable_after_both_runs_complete(self) -> None:
        comparison = build_research_comparison(
            baseline_run_id="base", variant_run_id="variant",
            changed_condition="rank_persistence_filter",
            input_dataset_id="dataset", input_revision_ids_hash="hash",
            baseline_report=_report("base", 0), variant_report=_report("variant", 0),
            baseline_candidates=(), variant_candidates=(),
            baseline_events=(), variant_events=(), spec=_spec(),
        )
        with tempfile.TemporaryDirectory() as directory:
            repository = ResearchRepository(Path(directory) / "research.sqlite3")
            for run_id in ("base", "variant"):
                repository.start_run(run_id, {}, {})
                repository.finish_run(run_id, f"hash-{run_id}")
            self.assertTrue(repository.save_research_comparison(comparison))
            self.assertFalse(repository.save_research_comparison(comparison))
            loaded = repository.load_research_comparison(comparison.comparison_id)
        self.assertEqual(comparison.comparison_id, loaded["comparison_id"])

    def test_market_type_and_raw_feature_ablation_keep_sample_counts(self) -> None:
        for changed_condition in ("market_type_filter", "market_raw_feature_filter"):
            comparison = build_research_comparison(
                baseline_run_id=f"base-{changed_condition}",
                variant_run_id=f"variant-{changed_condition}",
                changed_condition=changed_condition,
                input_dataset_id="dataset", input_revision_ids_hash="hash",
                baseline_report=_report(f"base-{changed_condition}", 0),
                variant_report=_report(f"variant-{changed_condition}", 0),
                baseline_candidates=(), variant_candidates=(),
                baseline_events=(), variant_events=(), spec=_spec(),
            )
            self.assertEqual(changed_condition, comparison.changed_condition)
            self.assertEqual(0, comparison.fold_comparisons[0].shared_candidate_count)


if __name__ == "__main__":
    unittest.main()
