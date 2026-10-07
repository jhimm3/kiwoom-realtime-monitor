"""Audit candidate-day minute coverage before freezing D03 development cases.

Only candidate codes and raw CREON bar timestamps are read.  Strategy outcomes,
returns, and sealed OOS inputs are not inspected.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


VERSION = "historical_minute_readiness_audit/v1"


def _open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _bar_times(connection: sqlite3.Connection, code: str, day: str) -> list[datetime]:
    rows = connection.execute(
        "SELECT bar_time FROM market_bars WHERE provider='daishin_creon' "
        "AND code=? AND venue IN ('K','KRX') AND session_scope='regular' "
        "AND interval_seconds=60 AND adjustment_mode='raw' "
        "AND bar_time>=? AND bar_time<? ORDER BY bar_time",
        (code, day, day + "T23:59:59"),
    )
    return sorted({datetime.fromisoformat(value) for (value,) in rows})


def audit_case(
    candidates: sqlite3.Connection, bars: sqlite3.Connection,
    selected_day: str, outcome_day: str,
) -> dict[str, object]:
    codes = [str(row[0]) for row in candidates.execute(
        "SELECT c.code FROM candidate_days c JOIN stocks s ON s.code=c.code "
        "WHERE c.dt=? AND s.market_code IN ('0','10') ORDER BY c.code",
        (selected_day,),
    )]
    missing: list[str] = []
    no_pair: list[str] = []
    thirty_minute_grid: list[str] = []
    total_bars = 0
    for code in codes:
        times = _bar_times(bars, code, outcome_day)
        total_bars += len(times)
        if not times:
            missing.append(code)
            continue
        gaps = [int((current - previous).total_seconds()) for previous, current
                in zip(times, times[1:])]
        if 60 not in gaps:
            no_pair.append(code)
            if len(times) >= 10 and gaps and all(gap in {1740, 1800} for gap in gaps):
                thirty_minute_grid.append(code)
    return {
        "selection_date": selected_day,
        "outcome_date": outcome_day,
        "candidate_codes": len(codes),
        "one_minute_labelled_bars": total_bars,
        "codes_without_bars": missing,
        "codes_without_continuous_minute_pair": no_pair,
        "codes_on_thirty_minute_grid": thirty_minute_grid,
        "readiness": "READY" if codes and not missing and not no_pair else "BLOCKED",
    }


def audit_range(
    candidate_database: Path, intelligence_database: Path,
    start_date: str, end_date: str,
) -> dict[str, object]:
    if start_date > end_date:
        raise ValueError("start_date must not exceed end_date")
    with _open_read_only(candidate_database) as candidates, _open_read_only(
        intelligence_database
    ) as bars:
        all_days = [str(row[0]) for row in candidates.execute(
            "SELECT DISTINCT dt FROM candidate_days ORDER BY dt"
        )]
        cases = [audit_case(candidates, bars, day, all_days[index + 1])
                 for index, day in enumerate(all_days[:-1])
                 if start_date <= day <= end_date]
    first_ready_by_month: dict[str, str] = {}
    for case in cases:
        if case["readiness"] == "READY":
            day = str(case["selection_date"])
            first_ready_by_month.setdefault(day[:7], day)
    return {
        "version": VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "selection_policy": "first_ready_candidate_date_per_calendar_month_by_data_quality_only",
        "source_databases": [
            {"path": str(path.resolve()), "size_bytes": path.stat().st_size,
             "modified_ns": path.stat().st_mtime_ns}
            for path in (candidate_database, intelligence_database)
        ],
        "start_date": start_date,
        "end_date": end_date,
        "case_count": len(cases),
        "ready_case_count": sum(case["readiness"] == "READY" for case in cases),
        "first_ready_by_month": first_ready_by_month,
        "cases": cases,
        "limitations": [
            "The stock market_code is the current candidate database classification, not an as-of listing record.",
            "A continuous one-minute pair is a minimum input check, not full-day completeness.",
            "A thirty-minute timestamp grid is suspicious source cadence, not proof of the provider's actual interval.",
            "No strategy outcomes or sealed OOS cases were inspected.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-database", required=True, type=Path)
    parser.add_argument("--intelligence-database", required=True, type=Path)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("readiness audit output is immutable")
    report = audit_range(args.candidate_database, args.intelligence_database,
                         args.start_date, args.end_date)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                      indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "case_count": report["case_count"],
                      "ready_case_count": report["ready_case_count"],
                      "first_ready_by_month": report["first_ready_by_month"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
