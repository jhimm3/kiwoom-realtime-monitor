"""Compare frozen D03 monthly dates with all development dates without reading outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import median


VERSION = "historical_monthly_selection_coverage_audit/v1"


def _case_map(document: dict, name: str) -> dict[str, dict]:
    rows = document.get("cases")
    if not isinstance(rows, list):
        raise ValueError(f"{name} cases must be a list")
    result: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{name} case must be an object")
        day = str(row.get("selection_date", ""))
        if not day or day in result:
            raise ValueError(f"{name} dates must be present and unique")
        result[day] = row
    if list(result) != sorted(result):
        raise ValueError(f"{name} dates must be chronological")
    return result


def _summary(rows: list[tuple[dict, dict]]) -> dict:
    counts = [int(projection["candidate_codes_after_exclusion"]) for _, projection in rows]
    original = [int(projection["candidate_codes_original"]) for _, projection in rows]
    excluded = [int(projection["candidate_codes_excluded"]) for _, projection in rows]
    if not counts or any(count <= 0 for count in counts):
        raise ValueError("coverage group must contain positive candidate counts")
    if any(before - removed != after for before, removed, after in zip(original, excluded, counts)):
        raise ValueError("projection candidate counts are inconsistent")
    bars_per_candidate = [int(readiness["one_minute_labelled_bars"]) / int(readiness["candidate_codes"])
                          for readiness, _ in rows]
    return {
        "date_count": len(rows),
        "candidate_days_original": sum(original),
        "candidate_days_excluded": sum(excluded),
        "candidate_days_after_exclusion": sum(counts),
        "candidate_count_median": median(counts),
        "candidate_count_min": min(counts),
        "candidate_count_max": max(counts),
        "ready_before_exclusion_dates": sum(readiness["readiness"] == "READY" for readiness, _ in rows),
        "median_one_minute_labelled_bars_per_candidate": round(median(bars_per_candidate), 1),
    }


def audit(readiness: dict, readiness_hash: str, projection: dict, projection_hash: str,
          selection: dict, selection_hash: str) -> dict:
    if readiness.get("version") != "historical_minute_readiness_audit/v1":
        raise ValueError("unsupported readiness audit")
    if projection.get("version") != "historical_minute_exclusion_projection/v1":
        raise ValueError("unsupported exclusion projection")
    if selection.get("version") != "historical_monthly_case_selection/v1":
        raise ValueError("unsupported monthly selection")
    if projection.get("source_audit_sha256") != readiness_hash:
        raise ValueError("projection does not bind to the readiness audit")
    if selection.get("source_projection_sha256") != projection_hash:
        raise ValueError("selection does not bind to the exclusion projection")
    if selection.get("oos", {}).get("status") != "SEALED" or selection["oos"].get("results_included") is not False:
        raise ValueError("OOS must remain sealed")

    ready = _case_map(readiness, "readiness")
    projected = _case_map(projection, "projection")
    selected = _case_map(selection, "selection")
    if set(ready) != set(projected) or not set(selected).issubset(projected):
        raise ValueError("development date sets do not match")
    for day, row in ready.items():
        other = projected[day]
        if (row["outcome_date"] != other["outcome_date"]
                or int(row["candidate_codes"]) != int(other["candidate_codes_original"])):
            raise ValueError(f"source case mismatch: {day}")
    for day, row in selected.items():
        other = projected[day]
        if row.get("role") not in {"TRAIN", "VALIDATION"} or any(
            row.get(field) != other.get(field) for field in (
                "outcome_date", "candidate_codes_original", "candidate_codes_excluded",
                "candidate_codes_after_exclusion", "excluded_codes")
        ):
            raise ValueError(f"selected case mismatch: {day}")

    months: dict[str, list[str]] = {}
    for day in projected:
        months.setdefault(day[:7], []).append(day)
    if len(selected) != len(months) or any(
        sum(day[:7] == month for day in selected) != 1 or next(
            day for day in selected if day[:7] == month) != days[0]
        for month, days in months.items()
    ):
        raise ValueError("selection must use the first development date of every month")

    chosen = [(ready[day], projected[day]) for day in selected]
    other = [(ready[day], projected[day]) for day in projected if day not in selected]
    monthly = []
    for month, days in months.items():
        day = days[0]
        counts = [int(projected[value]["candidate_codes_after_exclusion"]) for value in days]
        chosen_count = counts[0]
        monthly.append({
            "month": month,
            "selection_date": day,
            "development_date_count": len(days),
            "selected_candidate_count": chosen_count,
            "month_candidate_count_median": median(counts),
            "selected_count_percentile_le": round(sum(value <= chosen_count for value in counts) / len(counts), 3),
            "selected_ready_before_exclusion": ready[day]["readiness"] == "READY",
        })
    return {
        "version": VERSION,
        "source_sha256": {"readiness": readiness_hash, "projection": projection_hash,
                          "selection": selection_hash},
        "scope": "TRAIN_and_VALIDATION_development_dates_only",
        "outcomes_read": False,
        "oos_results_read": False,
        "all_development_dates": _summary(chosen + other),
        "selected_dates": _summary(chosen),
        "unselected_dates": _summary(other),
        "monthly_comparison": monthly,
        "limitations": [
            "Calendar-first dates are deterministic, not a random sample of trading days.",
            "Candidate counts and one-minute-labelled bar totals do not establish full-session coverage.",
            "This audit does not compare contemporaneous rank, market regime, news availability, returns, or OOS outcomes.",
            "Current stock market classification is not an as-of listing record.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readiness", required=True, type=Path)
    parser.add_argument("--projection", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    inputs = [path.read_bytes() for path in (args.readiness, args.projection, args.selection)]
    result = audit(*(part for encoded in inputs for part in (
        json.loads(encoded), hashlib.sha256(encoded).hexdigest())))
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output.exists():
        if args.output.read_text(encoding="utf-8") != text:
            raise ValueError("immutable coverage audit already exists with different content")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(json.dumps({"status": "ok", "output": str(args.output.resolve()),
                      "selected_dates": result["selected_dates"]["date_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
