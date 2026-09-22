"""Prepare fixed TRAIN/VALIDATION requests for historical strategy baselines.

The generated requests never include an OOS partition.  They are intended to
confirm that the existing registered families can consume the independently
frozen historical development inputs before any parameter search is attempted.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


BREAKOUT_FAMILY = "krx_bar_close_breakout/v1"
PULLBACK_FAMILY = "krx_pullback_reacceleration/v1"


def prepare_requests(package: Path, output: Path) -> tuple[Path, ...]:
    package = package.resolve()
    output = output.resolve()
    manifest = _read_mapping(package / "manifest.json", "development package manifest")
    if manifest.get("version") != "historical_development_inputs/v1":
        raise ValueError("unsupported historical development package")
    if manifest.get("oos_included") is not False:
        raise ValueError("historical baseline requests require an OOS-free package")

    partitions = manifest.get("partitions")
    if not isinstance(partitions, list) or not partitions:
        raise ValueError("historical development package has no partitions")
    roles = {str(item.get("role", "")) for item in partitions if isinstance(item, Mapping)}
    if roles != {"TRAIN", "VALIDATION"}:
        raise ValueError("historical baseline requests allow TRAIN and VALIDATION only")

    requests_dir = output / "requests"
    requests_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for partition in partitions:
        if not isinstance(partition, Mapping):
            raise ValueError("historical development partition must be an object")
        role = str(partition["role"]).lower()
        dataset = package / str(partition["path"])
        evaluation = partition.get("evaluation")
        if not dataset.is_dir() or not isinstance(evaluation, Mapping):
            raise ValueError(f"historical {role} partition is incomplete")
        for family, name, strategy in _strategies():
            request = {
                "mode": "single_run",
                "family": family,
                "dataset": str(dataset),
                "database": str(output / "research.sqlite3"),
                "runs_dir": str(output / "runs"),
                "session_profile": "krx-regular/v1",
                "strategy": strategy,
                "execution": _execution_model(),
                "evaluation": dict(evaluation),
                "resource_budget": {"memory_mb": 512, "cpu_duty_percent": 50},
                "historical_baseline_context": {
                    "version": "historical_baseline_context/v1",
                    "purpose": "structural_train_validation_comparison",
                    "partition_role": str(partition["role"]),
                    "source_package_id": str(manifest["package_id"]),
                    "split_plan_id": str(manifest["split_plan_id"]),
                    "oos_included": False,
                    "parameter_status": "provisional_not_user_approved",
                    "cost_status": "development_model_not_broker_verified",
                },
            }
            path = requests_dir / f"{role}-{name}.json"
            _write_immutable_json(path, request)
            written.append(path)
    return tuple(written)


def _strategies() -> tuple[tuple[str, str, dict[str, Any]], ...]:
    common = {
        "strategy_version": "v1",
        "rank_factor_version": "v1",
        "rank_persistence_enabled": False,
        "rank_persistence_required": False,
        "rank_top_k": None,
        "rank_window_seconds": None,
        "rank_max_gap_seconds": None,
        "rank_min_residency_seconds": None,
        "stop_loss_bps": 300,
        "target_bps": 500,
        "max_hold_minutes": 10,
        "quantity": 1,
        "capital_won": 1_000_000,
        "signal_valid_seconds": 60,
        "cooldown_seconds": 30,
    }
    return (
        (BREAKOUT_FAMILY, "breakout", {
            **common,
            "rolling_factor_version": "v1",
            "lookback_bars": 5,
            "buffer_bps": 0,
        }),
        (PULLBACK_FAMILY, "pullback", {
            **common,
            "pullback_factor_version": "v1",
            "lookback_bars": 3,
            "minimum_pullback_bps": 500,
            "minimum_reacceleration_bps": 100,
        }),
    )


def _execution_model() -> dict[str, Any]:
    return {
        "version": "next_tradable_bar_open/v1",
        "same_bar_path_version": "conservative_with_optimistic_bound/v1",
        "initial_cash_won": 1_000_000,
        "cost_model": {
            "version": "fixed_bps/v1",
            "commission_bps": 1,
            "sell_tax_bps": 18,
            "slippage_bps": 5,
            "rate_basis": "model_estimate",
            "source": "historical development structural baseline; not broker verified",
            "valid_from": "2024-01-01T00:00:00+09:00",
            "valid_to": "2027-01-01T00:00:00+09:00",
        },
    }


def _read_mapping(path: Path, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} cannot be read") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _write_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError(f"request already exists with different content: {path}")
        return
    path.write_text(encoded, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare fixed baseline requests from an OOS-free historical package."
    )
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    paths = prepare_requests(args.package, args.output)
    print(json.dumps({"status": "ok", "requests": [str(path) for path in paths]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
