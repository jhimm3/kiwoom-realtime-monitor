"""Freeze calendar-first D03 development dates without inspecting outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

from kiwoom_monitor.application.historical_research_split import load_historical_evaluation_plan


VERSION = "historical_monthly_case_selection/v1"
TRAIN_EXPANSION_VERSION = "historical_train_case_expansion/v1"


def expand_train(projection: dict, projection_sha256: str, selection: dict,
                 selection_sha256: str) -> dict:
    """Fix all dates in existing TRAIN months before reading new price outcomes."""
    from scripts.audit_historical_monthly_selection_coverage import _case_map

    if projection.get("version") != "historical_minute_exclusion_projection/v1":
        raise ValueError("unsupported minute exclusion projection")
    if selection.get("version") != VERSION:
        raise ValueError("unsupported monthly selection")
    if selection.get("source_projection_sha256") != projection_sha256:
        raise ValueError("selection does not bind to the exclusion projection")
    oos = selection.get("oos", {})
    if oos.get("status") != "SEALED" or oos.get("results_included") is not False:
        raise ValueError("the prior OOS boundary is not sealed")
    projected = _case_map(projection, "projection")
    selected = _case_map(selection, "selection")
    if not set(selected).issubset(projected):
        raise ValueError("selected dates are missing from the projection")
    fields = ("outcome_date", "candidate_codes_original", "candidate_codes_excluded",
              "candidate_codes_after_exclusion", "excluded_codes")
    months: dict[str, list[str]] = {}
    for day, row in projected.items():
        for value in (day, row.get("outcome_date")):
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError("case dates must be canonical ISO dates")
        if row["outcome_date"] <= day:
            raise ValueError("outcome dates must follow selection dates")
        counts = [row.get(field) for field in fields[1:4]]
        excluded = row.get("excluded_codes")
        if (any(type(value) is not int or value < 0 for value in counts)
                or counts[2] <= 0 or counts[0] - counts[1] != counts[2]
                or not isinstance(excluded, list)
                or any(not isinstance(code, str) or not code for code in excluded)
                or len(set(excluded)) != len(excluded) or len(excluded) != counts[1]):
            raise ValueError("projection candidate counts or exclusions are inconsistent")
        months.setdefault(day[:7], []).append(day)
    outcomes = [row["outcome_date"] for row in projected.values()]
    if outcomes != sorted(set(outcomes)):
        raise ValueError("projection outcome dates must be unique and chronological")
    if (len(selected) != len(months) or any(
            days[0] not in selected for days in months.values())):
        raise ValueError("selection must use the first development date of every month")
    roles = [row.get("role") for row in selected.values()]
    train_count = roles.count("TRAIN")
    if (not 0 < train_count < len(roles)
            or roles != ["TRAIN"] * train_count + ["VALIDATION"] * (len(roles) - train_count)
            or type(selection.get("train_months")) is not int
            or type(selection.get("validation_months")) is not int
            or selection.get("train_months") != train_count
            or selection.get("validation_months") != len(roles) - train_count):
        raise ValueError("monthly TRAIN/VALIDATION roles must be contiguous")
    for day, row in selected.items():
        if any(row.get(field) != projected[day].get(field) for field in fields):
            raise ValueError(f"selected case mismatch: {day}")
    oos_date = oos.get("selection_date")
    if (not isinstance(oos_date, str) or date.fromisoformat(oos_date).isoformat() != oos_date
            or any(row["outcome_date"] >= oos_date for row in selected.values())):
        raise ValueError("development cases overlap the sealed OOS date")
    prior_binding = {key: value for key, value in selection.items()
                     if key not in {"plan_id", "generated_at"}}
    prior_digest = hashlib.sha256(json.dumps(prior_binding, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
    if selection.get("plan_id") != "historical-monthly-selection-" + prior_digest:
        raise ValueError("monthly selection plan_id does not match its contents")
    train_months = [day[:7] for day, row in selected.items() if row["role"] == "TRAIN"]
    boundary = next(day for day, row in selected.items() if row["role"] == "VALIDATION")
    cases, excluded_boundary = [], []
    for day, row in projected.items():
        if day[:7] not in train_months:
            continue
        case = {"role": "TRAIN", "selection_date": day,
                **{field: row[field] for field in fields}}
        if row["outcome_date"] >= boundary:
            excluded_boundary.append({**case, "reason": "outcome_reaches_validation_boundary"})
            continue
        if row.get("readiness_after_exclusion") != "READY":
            raise ValueError(f"TRAIN case is not ready after exclusions: {day}")
        cases.append(case)
    retained = {row["selection_date"] for row in cases}
    if any(day not in retained for day, row in selected.items() if row["role"] == "TRAIN"):
        raise ValueError("expansion must retain every existing TRAIN case")
    binding = {
        "version": TRAIN_EXPANSION_VERSION,
        "selection_policy": "all_existing_train_month_dates_before_validation_boundary",
        "source_projection_sha256": projection_sha256,
        "prior_selection_sha256": selection_sha256,
        "prior_selection_plan_id": selection.get("plan_id"),
        "train_months": train_months,
        "validation_boundary": boundary,
        "cases": cases,
        "excluded_boundary_cases": excluded_boundary,
        "prior_train_date_count": train_count,
        "train_date_count": len(cases),
        "additional_train_date_count": len(cases) - train_count,
        "candidate_days_after_exclusion": sum(row["candidate_codes_after_exclusion"] for row in cases),
        "oos": dict(oos),
        "validation_results_read": False,
        "oos_results_read": False,
        "strategy_outcomes_read": False,
        "source_readiness_verified": False,
        "runner_requests_created": False,
        "limitations": [
            "기존 TRAIN 월의 확대 개발 표본이며 독립 검증이나 미사용 final 표본이 아닙니다.",
            "과거 최소 연속쌍 제외 원장을 재사용하며 현재 원본·종일 완전성은 별도 대조해야 합니다.",
            "후보군은 사후 주도후보이며 당시 실시간 TOP20 모집단을 재현하지 않습니다.",
        ],
    }
    digest = hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()
    return {**binding, "plan_id": "historical-train-expansion-" + digest,
            "generated_at": datetime.now(UTC).isoformat()}


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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prior-split", type=Path)
    mode.add_argument("--expand-train-selection", type=Path,
                      help="기존 월별 계획의 TRAIN 월 전체를 확대하며 검증·OOS 결과는 읽지 않습니다.")
    parser.add_argument("--train-months", type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("monthly case selection is immutable")
    projection_bytes = args.projection.read_bytes()
    if args.expand_train_selection:
        if args.train_months is not None:
            parser.error("--train-months cannot change an existing TRAIN expansion")
        selection_bytes = args.expand_train_selection.read_bytes()
        result = expand_train(json.loads(projection_bytes), hashlib.sha256(projection_bytes).hexdigest(),
                              json.loads(selection_bytes), hashlib.sha256(selection_bytes).hexdigest())
    else:
        split_bytes = args.prior_split.read_bytes()
        result = plan(json.loads(projection_bytes), hashlib.sha256(projection_bytes).hexdigest(),
                      load_historical_evaluation_plan(args.prior_split),
                      hashlib.sha256(split_bytes).hexdigest(),
                      train_months=12 if args.train_months is None else args.train_months)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    summary = {"output": str(args.output), "plan_id": result["plan_id"],
               "train_months": result["train_months"], "oos_status": result["oos"]["status"]}
    if args.expand_train_selection:
        summary["train_date_count"] = result["train_date_count"]
    else:
        summary["validation_months"] = result["validation_months"]
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
