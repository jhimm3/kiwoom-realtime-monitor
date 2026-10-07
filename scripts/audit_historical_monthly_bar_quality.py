"""Inspect full-session minute coverage for quality-selected D03 monthly cases.

This audit reads candidate identities and raw CREON timestamps only. It does not
read prices, returns, strategy results, or sealed research inputs.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from statistics import median


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def audit(readiness: dict, candidate_db: Path, bar_db: Path, *, all_ready: bool = False,
          selected_cases: bool = False) -> dict:
    if selected_cases:
        case_rows = [row for row in readiness["cases"]
                     if row["role"] in {"TRAIN", "VALIDATION"}]
        selected = [(row["selection_date"][:7], row["selection_date"])
                    for row in case_rows]
    else:
        case_rows = readiness["cases"]
        selected = (
            [(row["selection_date"][:7], row["selection_date"])
             for row in case_rows if row["readiness"] == "READY"]
            if all_ready else list(readiness["first_ready_by_month"].items())
        )
    by_day = {row["selection_date"]: row for row in case_rows}
    result = []
    with closing(_read_only(candidate_db)) as candidates, closing(_read_only(bar_db)) as bars:
        for month, day in sorted(selected):
            outcome = by_day[day]["outcome_date"]
            excluded = set(by_day[day].get("excluded_codes", []))
            codes = [str(row[0]) for row in candidates.execute(
                "SELECT c.code FROM candidate_days c JOIN stocks s ON s.code=c.code "
                "WHERE c.dt=? AND s.market_code IN ('0','10') ORDER BY c.code",
                (day,),
            ) if str(row[0]) not in excluded]
            if selected_cases and len(codes) != int(by_day[day]["candidate_codes_after_exclusion"]):
                raise ValueError(f"candidate population changed for {day}")
            code_rows = []
            for code in codes:
                times = [datetime.fromisoformat(row[0]) for row in bars.execute(
                    "SELECT bar_time FROM market_bars WHERE provider='daishin_creon' "
                    "AND code=? AND venue IN ('K','KRX') AND session_scope='regular' "
                    "AND interval_seconds=60 AND adjustment_mode='raw' "
                    "AND bar_time>=? AND bar_time<? ORDER BY bar_time",
                    (code, outcome, outcome + "T23:59:59"),
                )]
                gaps = [int((b - a).total_seconds()) for a, b in zip(times, times[1:])]
                code_rows.append({
                    "code": code,
                    "bar_count": len(times),
                    "first_bar": times[0].isoformat() if times else None,
                    "last_bar": times[-1].isoformat() if times else None,
                    "max_gap_seconds": max(gaps, default=None),
                    "continuous_pairs": gaps.count(60),
                })
            counts = [row["bar_count"] for row in code_rows]
            result.append({
                "month": month,
                "selection_date": day,
                "outcome_date": outcome,
                "candidate_count": len(codes),
                "minimum_bars": min(counts, default=0),
                "median_bars": median(counts) if counts else 0,
                "codes_below_300_bars": [row["code"] for row in code_rows if row["bar_count"] < 300],
                "codes_with_gap_over_10_minutes": [row["code"] for row in code_rows
                                                    if (row["max_gap_seconds"] or 0) > 600],
                "codes": code_rows,
            })
    return {
        "version": ("historical_monthly_selected_bar_quality/v1" if selected_cases
                    else "historical_all_ready_bar_quality/v1" if all_ready
                    else "historical_monthly_bar_quality/v1"),
        "generated_at": datetime.now(UTC).isoformat(),
        "selection_policy": (
            "frozen_monthly_development_cases_no_oos" if selected_cases
            else "all_ready_candidate_dates_by_data_quality_only"
            if all_ready else readiness["selection_policy"]
        ),
        "source_readiness": readiness.get("version"),
        "source_databases": [
            {"path": str(path.resolve()), "size_bytes": path.stat().st_size,
             "modified_ns": path.stat().st_mtime_ns}
            for path in (candidate_db, bar_db)
        ],
        "cases": result,
        "limitations": [
            "Bar count and gaps describe coverage, not correctness of OHLCV or absence of trading halts.",
            "The 300-bar and 10-minute thresholds are diagnostics, not eligibility rules.",
            "No strategy outcome or sealed OOS case was read.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--readiness", type=Path)
    source.add_argument("--selection", type=Path,
                        help="Audit the frozen monthly TRAIN/VALIDATION cases, excluding OOS")
    parser.add_argument("--candidate-database", required=True, type=Path)
    parser.add_argument("--intelligence-database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--all-ready", action="store_true",
                        help="Audit every READY candidate date rather than one per month.")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("quality audit output is immutable")
    if args.selection and args.all_ready:
        raise ValueError("--all-ready cannot be combined with --selection")
    readiness = json.loads((args.selection or args.readiness).read_text(encoding="utf-8"))
    report = audit(readiness, args.candidate_database, args.intelligence_database,
                   all_ready=args.all_ready, selected_cases=bool(args.selection))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"output": str(args.output), "cases": len(report["cases"]),
                      "minimum_bars": min(x["minimum_bars"] for x in report["cases"]),
                      "codes_below_300_bars": sum(len(x["codes_below_300_bars"]) for x in report["cases"])},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
