"""Project a read-only D03 minute readiness audit after explicit case exclusions."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


VERSION = "historical_minute_exclusion_projection/v1"


def project(source: dict, source_sha256: str) -> dict:
    if source.get("version") != "historical_minute_readiness_audit/v1":
        raise ValueError("unsupported minute-readiness audit")
    cases = []
    seen_dates: set[str] = set()
    for row in source["cases"]:
        missing = set(row["codes_without_bars"])
        no_pair = set(row["codes_without_continuous_minute_pair"])
        excluded = sorted(missing | no_pair)
        original = int(row["candidate_codes"])
        remaining = original - len(excluded)
        day = row["selection_date"]
        if original < 0 or len(excluded) > original:
            raise ValueError("excluded code count exceeds candidate population")
        if day in seen_dates:
            raise ValueError("duplicate selection date in readiness audit")
        seen_dates.add(day)
        cases.append({
            "selection_date": day,
            "outcome_date": row["outcome_date"],
            "candidate_codes_original": original,
            "candidate_codes_excluded": len(excluded),
            "candidate_codes_after_exclusion": remaining,
            "excluded_codes": excluded,
            "excluded_no_bars": sorted(missing),
            "excluded_no_continuous_pair": sorted(no_pair),
            "readiness_after_exclusion": "READY" if remaining else "BLOCKED",
        })
    return {
        "version": VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_audit_sha256": source_sha256,
        "source_audit_selection_policy": source["selection_policy"],
        "projection_policy": "exclude_only_stock_outcome_dates_without_any_continuous_one_minute_pair",
        "start_date": source["start_date"],
        "end_date": source["end_date"],
        "case_count": len(cases),
        "ready_case_count": sum(row["readiness_after_exclusion"] == "READY" for row in cases),
        "candidate_codes_original": sum(row["candidate_codes_original"] for row in cases),
        "candidate_codes_excluded": sum(row["candidate_codes_excluded"] for row in cases),
        "candidate_codes_after_exclusion": sum(row["candidate_codes_after_exclusion"] for row in cases),
        "cases": cases,
        "limitations": [
            "Exclusion is per stock and outcome date, never a permanent current-status stock ban.",
            "A continuous one-minute pair is a minimum runner gate, not full-session completeness.",
            "No price outcomes, strategy results, or sealed OOS inputs are used.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--readiness", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("exclusion projection is immutable")
    encoded = args.readiness.read_bytes()
    result = project(json.loads(encoded), hashlib.sha256(encoded).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"output": str(args.output), "case_count": result["case_count"],
                      "ready_case_count": result["ready_case_count"],
                      "candidate_codes_excluded": result["candidate_codes_excluded"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
