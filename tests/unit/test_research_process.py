from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.research_process import (
    execute_process_request,
    load_research_process_request,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from scripts.run_research import ResearchRunCancelled
from scripts.run_research import LEGACY_RESEARCH_IMPLEMENTATION_HASH, research_implementation_hash


def _request_document() -> dict:
    return {
        "mode": "rank_comparison",
        "dataset": "dataset",
        "database": "output/research.sqlite3",
        "runs_dir": "output/runs",
        "strategy": {
            "strategy_version": "v1", "rolling_factor_version": "v1",
            "rank_factor_version": "v1", "lookback_bars": 2, "buffer_bps": 0,
            "rank_persistence_enabled": True, "rank_persistence_required": True,
            "rank_top_k": 20, "rank_window_seconds": 60,
            "rank_max_gap_seconds": 30, "rank_min_residency_seconds": 10,
            "stop_loss_bps": 300, "target_bps": 500, "max_hold_minutes": 10,
            "quantity": 1, "capital_won": 1_000_000,
            "signal_valid_seconds": 60, "cooldown_seconds": 30,
        },
        "execution": {
            "version": "next_tradable_bar_open/v1",
            "same_bar_path_version": "conservative_with_optimistic_bound/v1",
            "initial_cash_won": 1_000_000,
            "cost_model": {
                "version": "fixed_bps/v1", "commission_bps": 1,
                "sell_tax_bps": 18, "slippage_bps": 5,
                "rate_basis": "model_estimate", "source": "locked fixture",
                "valid_from": "2026-09-11T00:00:00+00:00",
                "valid_to": "2026-09-13T00:00:00+00:00",
            },
        },
        "evaluation": {
            "version": "chronological_holdout/v1",
            "folds": [{
                "name": "train", "role": "TRAIN",
                "start": "2026-09-12T00:00:00+00:00",
                "end": "2026-09-12T01:00:00+00:00",
            }],
            "warmup_seconds": 0, "gap_seconds": 0, "purge_seconds": 0,
            "minimum_closed_trades": 0, "minimum_active_days": 0,
        },
    }


def _write_empty_dataset(root: Path) -> None:
    dataset = root / "dataset"
    dataset.mkdir()
    observations = dataset / "observations.jsonl"
    observations.write_bytes(b"")
    (dataset / "manifest.json").write_text(json.dumps({
        "dataset_id": "empty", "revision_count": 0,
        "revision_ids_hash": hashlib.sha256(b"").hexdigest(),
        "observations_file": observations.name,
        "observations_file_hash": hashlib.sha256(b"").hexdigest(),
    }), encoding="utf-8")


class ResearchProcessTests(unittest.TestCase):
    def test_relative_paths_and_all_locked_settings_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            path = root / "request.json"
            path.write_text(json.dumps(_request_document()), encoding="utf-8")
            request = load_research_process_request(path)
        self.assertEqual("rank_comparison", request.mode)
        self.assertTrue(request.strategy.rank_persistence_required)
        self.assertEqual("locked fixture", request.execution.cost_model.source)
        self.assertEqual("train", request.evaluation.folds[0].name)

    def test_empty_request_executes_in_process_contract_and_writes_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            path = root / "request.json"
            path.write_text(json.dumps(_request_document()), encoding="utf-8")
            result = execute_process_request(load_research_process_request(path))
            manifest = Path(result["manifest"])
        self.assertEqual("ok", result["status"])
        self.assertEqual("rank_comparison", result["kind"])
        self.assertEqual("INELIGIBLE", result["comparison_status"])
        self.assertTrue(manifest.name == "manifest.json")

    def test_output_inside_frozen_dataset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            document = _request_document()
            document["database"] = "dataset/research.sqlite3"
            path = root / "request.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must not overwrite"):
                load_research_process_request(path)

    def test_cancel_marker_stops_before_empty_dataset_is_finalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            path = root / "request.json"
            path.write_text(json.dumps(_request_document()), encoding="utf-8")
            cancel = root / "cancel.request"
            cancel.write_text("cancel\n", encoding="ascii")
            with self.assertRaises(ResearchRunCancelled):
                execute_process_request(
                    load_research_process_request(path), cancel_path=cancel,
                )

    def test_limited_search_runs_to_budget_and_resume_does_not_duplicate_trials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            document = _request_document()
            document["mode"] = "limited_search"
            document["search"] = {
                "version": "limited_search/v2",
                "hypothesis_refs": ["hypothesis-1"],
                "dataset_id": "empty",
                "dataset_hash": hashlib.sha256(b"").hexdigest(),
                "family_allowlist": ["krx_bar_close_breakout/v1"],
                "factor_allowlist": ["rolling_high_breakout/v1", "rank_persistence/v1"],
                "parameter_space": {"target_bps": [400, 500]},
                "objective": {"net_pnl_won": "maximize", "max_drawdown_won": "minimize"},
                "constraints": {"minimum_closed_trades": 1},
                "split_version": "chronological_holdout/v1",
                "max_trials": 4, "max_seconds": 60, "seed": 11,
                "ablations": ["rank_persistence"],
                "cost_stress_multipliers_ppm": [2_000_000],
            }
            path = root / "request.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            request = load_research_process_request(path)
            first = execute_process_request(request)
            second = execute_process_request(request)
        self.assertEqual("limited_search", first["kind"])
        self.assertEqual(4, first["budget"]["used_trials"])
        self.assertEqual(
            ["baseline", "no_trade_baseline", "ablation", "cost_stress"],
            [card["variant"] for card in first["candidate_cards"]],
        )
        self.assertEqual(0, second["attempted_now"])
        self.assertEqual(first["candidate_cards"], second["candidate_cards"])

    def test_interrupted_trial_is_retried_without_cancelled_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            document = _request_document()
            document["mode"] = "limited_search"
            document["search"] = {
                "version": "limited_search/v2", "hypothesis_refs": ["h1"],
                "dataset_id": "empty", "dataset_hash": hashlib.sha256(b"").hexdigest(),
                "family_allowlist": ["krx_bar_close_breakout/v1"],
                "factor_allowlist": ["rolling_high_breakout/v1"],
                "parameter_space": {}, "objective": {"net_pnl_won": "maximize"},
                "constraints": {}, "split_version": "chronological_holdout/v1",
                "max_trials": 1, "max_seconds": 60, "seed": 1,
                "include_no_trade_baseline": False,
            }
            path = root / "request.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            request = load_research_process_request(path)
            with patch(
                "kiwoom_monitor.research_process.execute_research",
                side_effect=ResearchRunCancelled("fixture interruption"),
            ):
                interrupted = execute_process_request(request)
            repository = ResearchRepository(request.database)
            self.assertEqual("cancelled", interrupted["job_status"])
            self.assertEqual((), repository.load_search_trials(request.search.experiment_id))
            completed = execute_process_request(request)
            attempts = repository.load_trial_attempts(request.search.experiment_id)
            results = repository.load_search_trials(request.search.experiment_id)
        self.assertEqual("completed", completed["job_status"])
        self.assertEqual(["INTERRUPTED", "INELIGIBLE"], [row["status"] for row in attempts])
        self.assertEqual(1, len(results))

    def test_search_identity_includes_strategy_execution_and_actual_fold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)

            def load(document: dict, name: str):
                document["mode"] = "limited_search"
                document["search"] = {
                    "version": "limited_search/v2", "hypothesis_refs": ["h1"],
                    "dataset_id": "empty", "dataset_hash": hashlib.sha256(b"").hexdigest(),
                    "family_allowlist": ["krx_bar_close_breakout/v1"],
                    "factor_allowlist": ["rolling_high_breakout/v1"],
                    "parameter_space": {"target_bps": [400]},
                    "objective": {"net_pnl_won": "maximize"}, "constraints": {},
                    "split_version": "chronological_holdout/v1",
                    "max_trials": 1, "max_seconds": 60, "seed": 1,
                }
                path = root / f"{name}.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                return load_research_process_request(path).search

            baseline = load(_request_document(), "baseline")
            strategy_document = _request_document()
            strategy_document["strategy"]["target_bps"] = 900
            strategy = load(strategy_document, "strategy")
            execution_document = _request_document()
            execution_document["execution"]["initial_cash_won"] = 2_000_000
            execution = load(execution_document, "execution")
            fold_document = _request_document()
            fold_document["evaluation"]["folds"][0]["end"] = "2026-09-12T02:00:00+00:00"
            fold = load(fold_document, "fold")
        assert baseline and strategy and execution and fold
        self.assertNotEqual(baseline.experiment_id, strategy.experiment_id)
        self.assertNotEqual(baseline.experiment_id, execution.experiment_id)
        self.assertNotEqual(baseline.experiment_id, fold.experiment_id)

    def test_second_registered_family_is_parsed_and_runs_in_same_process_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            document = _request_document()
            document["mode"] = "single_run"
            document["family"] = "krx_pullback_reacceleration/v1"
            document["strategy"] = {
                "strategy_version": "v1", "pullback_factor_version": "v1",
                "rank_factor_version": "v1", "lookback_bars": 3,
                "minimum_pullback_bps": 500, "minimum_reacceleration_bps": 100,
                "rank_persistence_enabled": False, "rank_persistence_required": False,
                "rank_top_k": 20, "rank_window_seconds": 60,
                "rank_max_gap_seconds": 30, "rank_min_residency_seconds": 10,
                "stop_loss_bps": 300, "target_bps": 500, "max_hold_minutes": 10,
                "quantity": 1, "capital_won": 1_000_000,
                "signal_valid_seconds": 60, "cooldown_seconds": 30,
            }
            path = root / "request.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            request = load_research_process_request(path)
            result = execute_process_request(request)
        self.assertEqual("krx_pullback_reacceleration/v1", request.family)
        self.assertEqual("single_run", result["kind"])

    def test_explicit_session_profile_separates_run_identity_and_missing_request_stays_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            legacy_document = _request_document()
            legacy_document["mode"] = "single_run"
            explicit_document = _request_document()
            explicit_document["mode"] = "single_run"
            explicit_document["session_profile"] = "krx-regular/v1"
            legacy_path = root / "legacy.json"
            explicit_path = root / "explicit.json"
            legacy_path.write_text(json.dumps(legacy_document), encoding="utf-8")
            explicit_path.write_text(json.dumps(explicit_document), encoding="utf-8")
            legacy_request = load_research_process_request(legacy_path)
            explicit_request = load_research_process_request(explicit_path)
            legacy = execute_process_request(legacy_request)
            explicit = execute_process_request(explicit_request)
            explicit_manifest = json.loads(Path(explicit["manifest"]).read_text(encoding="utf-8"))
        self.assertIsNone(legacy_request.session_profile)
        self.assertEqual(LEGACY_RESEARCH_IMPLEMENTATION_HASH, research_implementation_hash())
        self.assertNotEqual(legacy["run_id"], explicit["run_id"])
        self.assertEqual("krx-regular/v1", explicit_manifest["spec"]["session_profile"]["profile"])

    def test_unknown_session_profile_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            document = _request_document()
            document["session_profile"] = "nxt-main/v1"
            path = root / "request.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported research session profile"):
                load_research_process_request(path)


if __name__ == "__main__":
    unittest.main()
