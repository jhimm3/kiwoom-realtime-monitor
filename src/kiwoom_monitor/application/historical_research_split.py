"""Bind historical reconstruction cases to a chronological evaluation policy."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from kiwoom_monitor.application.research_splits import (
    ResearchEvaluationSpec,
    ResearchFoldSpec,
)
from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset


PLAN_VERSION = "historical_chronological_split_plan/v1"
ASSIGNMENT_POLICY = "contiguous_historical_cases/v1"


def build_historical_evaluation_plan(
    dataset: FrozenResearchDataset,
    *,
    train_cases: int,
    validation_cases: int,
    minimum_closed_trades: int = 0,
    minimum_active_days: int = 1,
) -> dict[str, Any]:
    """Assign whole cases in time order and leave every remaining case sealed OOS."""
    manifest = dataset.manifest
    if manifest.get("runtime_input_version") != "historical_reconstruction_strategy/v1":
        raise ValueError("historical split plan requires a reconstruction strategy input")
    if type(train_cases) is not int or type(validation_cases) is not int:
        raise ValueError("historical split case counts must be integers")
    if train_cases < 1 or validation_cases < 1:
        raise ValueError("historical split requires TRAIN and VALIDATION cases")
    if min(minimum_closed_trades, minimum_active_days) < 0:
        raise ValueError("historical split eligibility limits must not be negative")

    included = manifest.get("included_cases")
    if not isinstance(included, list):
        raise ValueError("historical strategy input is missing included cases")
    case_dates = tuple(sorted({
        str(row.get("selection_date", ""))
        for row in included if isinstance(row, Mapping) and row.get("selection_date")
    }))
    if len(case_dates) < train_cases + validation_cases + 1:
        raise ValueError("historical split requires at least one sealed OOS case")

    roles = (
        ("historical_train", "TRAIN", case_dates[:train_cases]),
        ("historical_validation", "VALIDATION",
         case_dates[train_cases:train_cases + validation_cases]),
        ("historical_oos", "OOS", case_dates[train_cases + validation_cases:]),
    )
    assignments: list[dict[str, Any]] = []
    folds: list[ResearchFoldSpec] = []
    for name, role, dates in roles:
        case_rows = tuple(
            row for row in dataset.observations
            if _observation_case_date(row) in dates
        )
        minute_rows = tuple(row for row in case_rows if row.get("kind") == "minute_bar")
        if not minute_rows:
            raise ValueError(f"historical {role} fold has no minute bars")
        start = min(_aware_datetime(row["available_at"]) for row in case_rows)
        end = max(_aware_datetime(row["available_at"]) for row in case_rows) + timedelta(microseconds=1)
        folds.append(ResearchFoldSpec(name, role, start.isoformat(), end.isoformat()))
        assignments.append({
            "fold_name": name,
            "role": role,
            "selection_dates": list(dates),
            "start": start.isoformat(),
            "end_exclusive": end.isoformat(),
            "minute_bar_count": len(minute_rows),
        })

    evaluation = ResearchEvaluationSpec(
        "chronological_holdout/v1", tuple(folds),
        warmup_seconds=0, gap_seconds=0, purge_seconds=0,
        minimum_closed_trades=minimum_closed_trades,
        minimum_active_days=minimum_active_days,
    )
    binding = {
        "version": PLAN_VERSION,
        "source_dataset_id": str(manifest.get("dataset_id", "")),
        "source_revision_ids_hash": str(manifest.get("revision_ids_hash", "")),
        "assignment_policy": ASSIGNMENT_POLICY,
        "case_assignments": assignments,
        "evaluation": evaluation.to_dict(),
        "oos_status": "SEALED",
        "oos_results_included": False,
    }
    if not binding["source_dataset_id"] or len(binding["source_revision_ids_hash"]) != 64:
        raise ValueError("historical strategy input identity is incomplete")
    return {**binding, "plan_id": "historical-split-" + _hash_document(binding)}


def write_historical_evaluation_plan(plan: Mapping[str, Any], output: Path) -> None:
    """Write one immutable split plan after validating its content identity."""
    _validate_plan(plan)
    output = Path(output)
    if output.exists():
        raise ValueError("historical split plan is immutable and cannot be overwritten")
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(plan), ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=f".{output.name}-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
    try:
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def load_historical_evaluation_plan(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("historical split plan cannot be read") from exc
    if not isinstance(value, dict):
        raise ValueError("historical split plan must be an object")
    _validate_plan(value)
    return value


def _validate_plan(value: Mapping[str, Any]) -> None:
    fields = {
        "version", "source_dataset_id", "source_revision_ids_hash", "assignment_policy",
        "case_assignments", "evaluation", "oos_status", "oos_results_included", "plan_id",
    }
    if set(value) != fields or value.get("version") != PLAN_VERSION:
        raise ValueError("historical split plan fields are invalid")
    if value.get("assignment_policy") != ASSIGNMENT_POLICY:
        raise ValueError("historical split assignment policy is invalid")
    if not str(value.get("source_dataset_id", "")).strip():
        raise ValueError("historical split source dataset is missing")
    if re.fullmatch(r"[0-9a-f]{64}", str(value.get("source_revision_ids_hash", ""))) is None:
        raise ValueError("historical split source revision hash is invalid")
    if value.get("oos_status") != "SEALED" or value.get("oos_results_included") is not False:
        raise ValueError("historical split OOS must remain sealed")
    assignments = value.get("case_assignments")
    if not isinstance(assignments, list) or len(assignments) != 3:
        raise ValueError("historical split requires three chronological assignments")
    assignment_fields = {
        "fold_name", "role", "selection_dates", "start", "end_exclusive", "minute_bar_count",
    }
    if any(
        not isinstance(row, Mapping) or set(row) != assignment_fields
        or not isinstance(row.get("selection_dates"), list) or not row["selection_dates"]
        or type(row.get("minute_bar_count")) is not int or row["minute_bar_count"] < 1
        for row in assignments
    ):
        raise ValueError("historical split case assignment is invalid")
    assigned_dates = [str(day) for row in assignments for day in row["selection_dates"]]
    if assigned_dates != sorted(set(assigned_dates)):
        raise ValueError("historical split cases must be unique and chronological")
    evaluation_value = value.get("evaluation")
    if not isinstance(evaluation_value, Mapping):
        raise ValueError("historical split evaluation is invalid")
    evaluation = ResearchEvaluationSpec.from_dict(evaluation_value)
    if evaluation.final_holdout_accessed_at or evaluation.final_holdout_access_reason:
        raise ValueError("historical split OOS access must remain empty")
    if [row.get("role") for row in assignments if isinstance(row, Mapping)] != [
        "TRAIN", "VALIDATION", "OOS",
    ]:
        raise ValueError("historical split roles are invalid")
    if [fold.to_dict() for fold in evaluation.folds] != [
        {"name": row.get("fold_name"), "role": row.get("role"),
         "start": row.get("start"), "end": row.get("end_exclusive")}
        for row in assignments if isinstance(row, Mapping)
    ]:
        raise ValueError("historical split assignments do not match evaluation folds")
    binding = {key: value[key] for key in fields if key != "plan_id"}
    expected = "historical-split-" + _hash_document(binding)
    if value.get("plan_id") != expected:
        raise ValueError("historical split plan identity does not match content")


def _observation_case_date(row: Mapping[str, Any]) -> str:
    payload = row.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    return str(payload.get("selection_date") or payload.get("historical_case_date") or "")


def _aware_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("historical split timestamps must be timezone-aware")
    return parsed


def _hash_document(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
