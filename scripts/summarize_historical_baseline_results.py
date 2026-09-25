"""Summarize four fixed historical TRAIN/VALIDATION baseline runs."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository


EXPECTED = (
    ("TRAIN", "breakout", "train-breakout.json"),
    ("TRAIN", "pullback", "train-pullback.json"),
    ("VALIDATION", "breakout", "validation-breakout.json"),
    ("VALIDATION", "pullback", "validation-pullback.json"),
)


def summarize(root: Path) -> Mapping[str, Any]:
    root = root.resolve()
    repository = ResearchRepository(root / "research.sqlite3")
    runs: list[dict[str, Any]] = []
    for role, family, filename in EXPECTED:
        result = _read_mapping(root / "results" / filename)
        if result.get("status") != "ok" or result.get("kind") != "single_run":
            raise ValueError(f"baseline result is incomplete: {filename}")
        report = result.get("report")
        if not isinstance(report, Mapping):
            raise ValueError(f"baseline report is missing: {filename}")
        folds = report.get("fold_reports")
        if not isinstance(folds, list) or len(folds) != 1 or folds[0].get("role") != role:
            raise ValueError(f"baseline result role mismatch: {filename}")
        fold = folds[0]
        run_id = str(result["run_id"])
        manifest = _read_mapping(Path(str(result["manifest"])))
        censored = Counter(
            str(event.get("reason", ""))
            for event in repository.load_execution_events(run_id)
            if event.get("state") == "CENSORED"
        )
        runs.append({
            "partition_role": role,
            "family": family,
            "run_id": run_id,
            "report_status": str(result["report_status"]),
            "data_quality": report.get("data_quality"),
            "decision_count": int(manifest.get("decision_count", 0)),
            "candidate_count": int(manifest.get("candidate_count", 0)),
            "submitted_order_count": int(fold.get("submitted_order_count", 0)),
            "filled_order_count": int(fold.get("filled_order_count", 0)),
            "censored_order_count": int(fold.get("censored_order_count", 0)),
            "run_censored_event_count": sum(censored.values()),
            "censored_reasons": dict(sorted(censored.items())),
            "closed_trade_count": int(fold.get("closed_trade_count", 0)),
            "active_day_count": int(fold.get("active_day_count", 0)),
            "net_realized_pnl_won": fold.get("net_realized_pnl_won"),
            "event_outcomes": fold.get("event_outcomes", []),
            "limitations": report.get("limitations", []),
        })
    return {
        "version": "historical_baseline_comparison/v1",
        "scope": "provisional structural TRAIN/VALIDATION baseline",
        "oos_included": False,
        "rank_persistence_enabled": False,
        "parameter_status": "provisional_not_user_approved",
        "cost_status": "development_model_not_broker_verified",
        "conclusion_policy": (
            "Do not select or reject a strategy from this sparse collection-in-progress sample."
        ),
        "runs": runs,
    }


def _read_mapping(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON cannot be read: {path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON must be an object: {path}")
    return value


def _markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Historical development baseline comparison",
        "",
        "This is a structural TRAIN/VALIDATION check on a sparse collection-in-progress sample. "
        "Parameters are provisional, costs are not broker verified, and OOS remains sealed.",
        "",
        "| Partition | Family | Status | Decisions | Candidates | Submitted | Filled | In-fold censored | Closed | Net PnL |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in summary["runs"]:
        lines.append(
            f"| {run['partition_role']} | {run['family']} | {run['report_status']} | "
            f"{run['decision_count']} | {run['candidate_count']} | "
            f"{run['submitted_order_count']} | {run['filled_order_count']} | "
            f"{run['censored_order_count']} | {run['closed_trade_count']} | "
            f"{run['net_realized_pnl_won']} |"
        )
    lines.extend([
        "", "The in-fold count uses the evaluation window. The reason counts below cover the whole run,",
        "including orders censored when the research interval ended.",
        "", "## Run-wide censored event reasons", "",
    ])
    for run in summary["runs"]:
        reasons = ", ".join(
            f"{reason or '(empty)'}={count}"
            for reason, count in run["censored_reasons"].items()
        ) or "none"
        lines.append(f"- {run['partition_role']} {run['family']}: {reasons}")
    lines.extend([
        "",
        "Do not use this sample to select or reject either strategy. Rebuild the split after "
        "historical collection coverage is representative, then rerun TRAIN and VALIDATION.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    summary = summarize(args.root)
    args.root.mkdir(parents=True, exist_ok=True)
    (args.root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.root / "SUMMARY.md").write_text(_markdown(summary), encoding="utf-8")
    print(json.dumps({"status": "ok", "summary": str((args.root / 'summary.json').resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
