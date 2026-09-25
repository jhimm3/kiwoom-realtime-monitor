"""Freeze exchange trading-state evidence for monthly development cases.

This sidecar accompanies D03 inputs.  A receipt date is not an effective date,
and a historical filing's intraday publication time is not reconstructed here.
No event is converted into a strategy signal or a tradability decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path


DEFAULT_SELECTION = Path("data/research/historical-monthly-case-selection-2024-09-to-2026-01.json")
DEFAULT_CANDIDATES = Path(r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3")
DEFAULT_CONTEXT = Path("data/historical_market_context.sqlite3")


def build_context(selection: Path, candidates: Path, context: Path) -> dict[str, object]:
    selection_bytes = selection.read_bytes()
    plan = json.loads(selection_bytes)
    assignments = [case for case in plan["cases"] if case["role"] in {"TRAIN", "VALIDATION"}]
    if not assignments or not any(case["role"] == "VALIDATION" for case in assignments):
        raise ValueError("development case selection is incomplete")
    with closing(sqlite3.connect(candidates.resolve().as_uri() + "?mode=ro", uri=True)) as daily, closing(
        sqlite3.connect(context.resolve().as_uri() + "?mode=ro", uri=True)
    ) as events:
        daily.execute("PRAGMA query_only=ON")
        events.execute("PRAGMA query_only=ON")
        if not events.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='historical_exchange_effective_daily_checks'"
        ).fetchone():
            raise ValueError("effective-date daily reconciliation must finish first")
        cases: list[dict[str, object]] = []
        counts: Counter[str] = Counter()
        for case in assignments:
            selected = str(case["selection_date"])
            outcome = str(case["outcome_date"])
            excluded = set(case["excluded_codes"])
            codes = {
                str(row[0]) for row in daily.execute(
                    "SELECT c.code FROM candidate_days c JOIN stocks s ON s.code=c.code "
                    "WHERE c.dt=? AND s.market_code IN ('0','10')", (selected,)
                )
            } - excluded
            if len(codes) != int(case["candidate_codes_after_exclusion"]):
                raise ValueError(f"candidate population changed for {selected}")
            rows = events.execute(
                "SELECT e.code,e.receipt_no,e.receipt_date,e.kind,e.effective_date,"
                "e.effective_time,e.precision,e.source_label,e.raw_sha256,"
                "d.receipt_to_effective_days,d.daily_row_state,d.daily_volume,"
                "d.last_positive_before,d.first_positive_on_or_after,d.review_status "
                "FROM historical_exchange_effective_events e "
                "LEFT JOIN historical_exchange_effective_daily_checks d "
                "ON d.code=e.code AND d.receipt_no=e.receipt_no AND d.kind=e.kind "
                "AND d.effective_date=e.effective_date AND d.effective_time=e.effective_time "
                "WHERE e.effective_date>=? AND e.effective_date<=? "
                "ORDER BY e.effective_date,e.effective_time,e.code,e.receipt_no,e.kind",
                (selected, outcome),
            ).fetchall()
            matched: list[dict[str, object]] = []
            for row in rows:
                if row[0] not in codes:
                    continue
                if row[9] is None:
                    raise ValueError("effective-date daily reconciliation is incomplete")
                record = dict(zip((
                    "code", "receipt_no", "receipt_date", "kind", "effective_date",
                    "effective_time", "precision", "source_label", "raw_sha256",
                    "receipt_to_effective_days", "daily_row_state", "daily_volume",
                    "last_positive_before", "first_positive_on_or_after", "review_status",
                ), row, strict=True))
                matched.append(record)
                counts[str(record["kind"])] += 1
            cases.append({
                "selection_date": selected, "outcome_date": outcome,
                "role": case["role"], "candidate_count": len(codes),
                "candidate_codes_sha256": hashlib.sha256(
                    "\n".join(sorted(codes)).encode("ascii")
                ).hexdigest(),
                "exchange_events": matched,
            })
    return {
        "version": "historical_exchange_case_context/v1",
        "selection_sha256": hashlib.sha256(selection_bytes).hexdigest(),
        "development_case_count": len(cases),
        "event_kind_counts": dict(sorted(counts.items())),
        "cases": cases,
        "policy": {
            "oos_included": False,
            "receipt_date_is_not_effective_date": True,
            "historical_intraday_receipt_time_verified": False,
            "daily_trade_rows_are_cross_checks_not_overrides": True,
            "strategy_signal_or_tradability_gate": False,
            "corrected_filings_require_revision_review": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("exchange case context is immutable")
    report = build_context(args.selection, args.candidates, args.context)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"case_count": report["development_case_count"],
                      "event_kind_counts": report["event_kind_counts"],
                      "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
