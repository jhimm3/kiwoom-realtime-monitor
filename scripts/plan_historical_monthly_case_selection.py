"""Freeze calendar-first D03 development dates without inspecting outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from kiwoom_monitor.application.historical_research_split import load_historical_evaluation_plan


VERSION = "historical_monthly_case_selection/v1"


def plan(projection: dict, projection_sha256: str, prior_split: dict,
         prior_split_sha256: str, *, train_months: int) -> dict:
    if projection.get("version") != "historical_minute_exclusion_projection/v1":
        raise ValueError("unsupported minute exclusion projection")
    if (prior_split.get("oos_status"), prior_split.get("oos_results_included")) != ("SEALED", False):
        raise ValueError("the prior OOS boundary is not sealed")
    assignments = prior_split.get("case_assignments", [])
    oos = [row for row in assignments if row.get("role") == "OOS"]
    if len(oos) != 1 or len(oos[0].get("selection_dates", [])) != 1:
        raise ValueError("expected one precommitted sealed OOS date")
    oos_date = str(oos[0]["selection_dates"][0])

    first_by_month: dict[str, dict] = {}
    dates: list[str] = []
    for case in projection.get("cases", []):
        day = str(case["selection_date"])
        dates.append(day)
        first_by_month.setdefault(day[:7], case)
    if dates != sorted(set(dates)):
        raise ValueError("projection dates must be unique and chronological")
    months = list(first_by_month)
    if months != sorted(months) or not 0 < train_months < len(months):
        raise ValueError("invalid monthly TRAIN/VALIDATION boundary")
    selected = list(first_by_month.values())
    if any(case.get("readiness_after_exclusion") != "READY" for case in selected):
        raise ValueError("a calendar-first case is not ready after exclusions")
    if selected[-1]["outcome_date"] >= oos_date:
        raise ValueError("development cases overlap the sealed OOS date")
    cases = [{
        "role": "TRAIN" if index < train_months else "VALIDATION",
        "selection_date": case["selection_date"],
        "outcome_date": case["outcome_date"],
        "candidate_codes_original": case["candidate_codes_original"],
        "candidate_codes_excluded": case["candidate_codes_excluded"],
        "candidate_codes_after_exclusion": case["candidate_codes_after_exclusion"],
        "excluded_codes": case["excluded_codes"],
    } for index, case in enumerate(selected)]
    binding = {
        "version": VERSION,
        "selection_policy": "first_candidate_date_per_calendar_month_before_outcomes",
        "source_projection_sha256": projection_sha256,
        "prior_split_sha256": prior_split_sha256,
        "train_months": train_months,
        "validation_months": len(months) - train_months,
        "cases": cases,
        "oos": {"selection_date": oos_date, "status": "SEALED", "results_included": False},
        "limitations": [
            "Readiness is a minimum one-minute-pair gate, not full-session completeness.",
            "The date policy does not inspect returns, news labels, or OOS outcomes.",
            "The prior OOS case remains a structural holdout, not sufficient final evaluation.",
        ],
    }
    digest = hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()
    return {**binding, "plan_id": "historical-monthly-selection-" + digest,
            "generated_at": datetime.now(UTC).isoformat()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projection", required=True, type=Path)
    parser.add_argument("--prior-split", required=True, type=Path)
    parser.add_argument("--train-months", type=int, default=12)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("monthly case selection is immutable")
    projection_bytes = args.projection.read_bytes()
    split_bytes = args.prior_split.read_bytes()
    result = plan(json.loads(projection_bytes), hashlib.sha256(projection_bytes).hexdigest(),
                  load_historical_evaluation_plan(args.prior_split),
                  hashlib.sha256(split_bytes).hexdigest(),
                  train_months=args.train_months)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"output": str(args.output), "plan_id": result["plan_id"],
                      "train_months": result["train_months"],
                      "validation_months": result["validation_months"],
                      "oos_status": result["oos"]["status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
