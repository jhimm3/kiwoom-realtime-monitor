from __future__ import annotations

import hashlib
import json
from pathlib import Path

from kiwoom_monitor.application.research_search import ExperimentSpec, SEARCH_VERSION
from kiwoom_monitor.research_process import load_research_process_request


def research_request_document() -> dict:
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


def write_empty_dataset(root: Path) -> None:
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


def write_campaign_request(root: Path, *, max_trials=4):
    write_empty_dataset(root)
    document = research_request_document()
    document["mode"] = "limited_search"
    document["search"] = {
        "version": "limited_search/v2", "hypothesis_refs": ["h1"],
        "dataset_id": "empty", "dataset_hash": hashlib.sha256(b"").hexdigest(),
        "family_allowlist": ["krx_bar_close_breakout/v1"],
        "factor_allowlist": ["rolling_high_breakout/v1"],
        "parameter_space": {"target_bps": [400, 500]},
        "objective": {"net_pnl_won": "maximize"}, "constraints": {},
        "split_version": "chronological_holdout/v1", "max_trials": max_trials,
        "max_seconds": 60, "seed": 11,
    }
    path = root / "request.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, load_research_process_request(path)


def research_search_spec(dataset_id: str = "dataset-1") -> ExperimentSpec:
    return ExperimentSpec(
        version=SEARCH_VERSION, hypothesis_refs=("hypothesis-1",),
        dataset_id=dataset_id, dataset_hash=f"hash-{dataset_id}",
        family_allowlist=("krx_bar_close_breakout/v1",),
        factor_allowlist=("rolling_high_breakout/v1",),
        parameter_space={"lookback_bars": (3,)},
        objective={"net_pnl_won": "maximize"}, constraints={},
        split_version="chronological_holdout/v1", max_trials=2,
        max_seconds=10, seed=1,
        research_context={"locked_request": "context-1"},
    )
