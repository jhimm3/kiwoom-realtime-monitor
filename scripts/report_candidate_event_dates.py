"""Select corporate-action dates that intersect candidate research windows.

Waits for the NAS Kiwoom daily refresh to finish before publishing a report.
The CREON/DART event source is evidence, not a complete Kiwoom event feed.
"""

from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


KST = timezone(timedelta(hours=9))
DEFAULT_CANDIDATES = Path(r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3")
DEFAULT_EVENTS = Path("data/historical_market_context.sqlite3")
DEFAULT_STATUS = Path("data/historical_collection/kiwoom_candidate_daily_7y_status.json")


def report(candidate_path: Path, event_path: Path, status_path: Path,
           *, require_complete: bool = True) -> dict:
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if require_complete and status.get("phase") != "complete":
        raise ValueError("NAS daily refresh is not complete")
    cutoff, as_of = str(status["cutoff"]), str(status["as_of"])
    candidates = sqlite3.connect(f"file:{candidate_path.as_posix()}?mode=ro", uri=True)
    events = sqlite3.connect(f"file:{event_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        codes = {str(row[0]) for row in candidates.execute(
            "SELECT DISTINCT c.code FROM candidate_days c JOIN stocks s ON s.code=c.code "
            "WHERE s.market_code IN ('0','10')"
        )}
        by_code: dict[str, list[tuple]] = defaultdict(list)
        for row in events.execute(
            "SELECT provider,code,adjustment_date,adjustment_rate,previous_factor,new_factor "
            "FROM historical_stock_adjustments WHERE adjustment_date BETWEEN ? AND ? "
            "ORDER BY code,adjustment_date", (cutoff, as_of)
        ):
            if row[1] in codes:
                by_code[str(row[1])].append(row)
        classifications = {
            (str(code), str(day), float(rate)): (str(kind), str(state))
            for code, day, rate, kind, state in events.execute(
                "SELECT code,adjustment_date,adjustment_rate,event_type,classification_status "
                "FROM historical_stock_adjustment_classifications"
            )
        }
        selected = []
        for code, event_rows in by_code.items():
            daily = [str(row[0]) for row in candidates.execute(
                "SELECT dt FROM daily_bars WHERE code=? AND dt<=? ORDER BY dt", (code, as_of)
            )]
            case_days = [str(row[0]) for row in candidates.execute(
                "SELECT dt FROM candidate_days WHERE code=? AND dt<=? ORDER BY dt", (code, as_of)
            )]
            windows = []
            for case_day in case_days:
                position = bisect.bisect_right(daily, case_day) - 1
                if position < 0:
                    continue
                start = daily[max(0, position - 249)]
                outcome_end = daily[position + 1] if position + 1 < len(daily) else case_day
                windows.append((start, outcome_end, case_day))
            for provider, _, day, rate, previous, current in event_rows:
                affected = [case_day for start, end, case_day in windows if start <= day <= end]
                if not affected:
                    continue
                kind, state = classifications.get((code, day, float(rate)), ("", "unclassified"))
                selected.append({
                    "code": code, "event_date": day, "event_provider": provider,
                    "adjustment_rate": rate, "previous_factor": previous,
                    "new_factor": current, "event_type": kind,
                    "classification_status": state,
                    "affected_candidate_cases": len(affected),
                    "first_affected_candidate_day": min(affected),
                    "last_affected_candidate_day": max(affected),
                })
        counts = Counter(row["classification_status"] for row in selected)
        return {
            "schema": "candidate-corporate-action-windows/v1",
            "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
            "daily_refresh_as_of": as_of, "cutoff": cutoff,
            "daily_refresh_phase": status.get("phase"),
            "population": "candidate_days joined to stocks.market_code 0/10",
            "window": "candidate day and prior 249 trading days through next available daily bar",
            "event_source_limitation": (
                "CREON adjustment boundaries with DART classifications; "
                "missing Kiwoom-only events are not inferred by this report"
            ),
            "event_count": len(selected),
            "stock_count": len({row["code"] for row in selected}),
            "classification_status_counts": dict(counts),
            "events": selected,
        }
    finally:
        events.close()
        candidates.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--output", type=Path,
                        default=Path("data/research/needed_candidate_events_20260924.json"))
    args = parser.parse_args()
    value = report(args.candidates, args.events, args.status)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"events={value['event_count']} stocks={value['stock_count']} "
          f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
