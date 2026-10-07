"""Cross-check frozen D03 minute gaps against independent daily and VI evidence.

The report records supporting or contradictory evidence. It never fills bars,
infers a halt from zero volume, or changes case eligibility.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path


DEFAULT_GAPS = Path("data/research/historical-monthly-selected-gap-vi-check-20260924.json")
DEFAULT_MARKET = Path("data/historical_intelligence.sqlite3")
DEFAULT_CONTEXT = Path("data/historical_market_context.sqlite3")
DEFAULT_KIWOOM = Path(r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3")


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def audit(gap_file: Path, market_path: Path, context_path: Path,
          kiwoom_path: Path) -> dict[str, object]:
    raw = gap_file.read_bytes()
    gaps = json.loads(raw)["gaps"]
    cases: dict[tuple[str, str], dict[str, object]] = {}
    checked_gaps: list[dict[str, object]] = []
    with closing(_read_only(market_path)) as market, closing(_read_only(context_path)) as context, closing(
        _read_only(kiwoom_path)
    ) as kiwoom:
        for gap in gaps:
            code, day = str(gap["code"]), str(gap["outcome_date"])
            key = (code, day)
            if key not in cases:
                count, minute_volume = market.execute(
                    "SELECT COUNT(*),COALESCE(SUM(volume),0) FROM market_bars "
                    "WHERE provider='daishin_creon' AND code=? AND venue IN ('K','KRX') "
                    "AND session_scope='regular' AND interval_seconds=60 "
                    "AND adjustment_mode='raw' AND bar_time>=? AND bar_time<?",
                    (code, day, day + "T23:59:59"),
                ).fetchone()
                creon_daily = context.execute(
                    "SELECT volume FROM historical_stock_fundamentals "
                    "WHERE provider='daishin_creon' AND code=? AND trade_date=?",
                    (code, day),
                ).fetchone()
                kiwoom_daily = kiwoom.execute(
                    "SELECT volume FROM daily_bars WHERE code=? AND dt=?", key,
                ).fetchone()
                kiwoom_minutes = kiwoom.execute(
                    "SELECT COUNT(*) FROM minute_bars WHERE code=? AND dt=?", key,
                ).fetchone()[0]
                official = context.execute(
                    "SELECT receipt_no,receipt_date,kind,effective_time,precision "
                    "FROM historical_exchange_effective_events "
                    "WHERE code=? AND effective_date=? ORDER BY effective_time,kind",
                    key,
                ).fetchall()
                cases[key] = {
                    "code": code, "outcome_date": day, "creon_minute_count": int(count),
                    "creon_minute_volume_sum": int(minute_volume),
                    "creon_daily_volume": int(creon_daily[0]) if creon_daily else None,
                    "creon_daily_volume_minus_minute_sum": (
                        int(creon_daily[0]) - int(minute_volume) if creon_daily else None
                    ),
                    "kiwoom_daily_volume": int(kiwoom_daily[0]) if kiwoom_daily else None,
                    "kiwoom_minute_count": int(kiwoom_minutes),
                    "official_exchange_events_on_date": [
                        {"receipt_no": receipt, "receipt_date": receipt_date,
                         "kind": kind, "effective_time": clock, "precision": precision}
                        for receipt, receipt_date, kind, clock, precision in official
                    ],
                    "gap_count_over_10m": 0,
                    "vi_overlapping_gap_count": 0,
                }
            case = cases[key]
            case["gap_count_over_10m"] += 1
            if gap["overlapping_vi_count"]:
                case["vi_overlapping_gap_count"] += 1
            kiwoom_inside = None
            if case["kiwoom_minute_count"]:
                left = str(gap["from_bar"])[:19].replace("T", " ")
                right = str(gap["to_bar"])[:19].replace("T", " ")
                kiwoom_inside = kiwoom.execute(
                    "SELECT COUNT(*) FROM minute_bars WHERE code=? AND ts>? AND ts<?",
                    (code, left, right),
                ).fetchone()[0]
            checked_gaps.append({**gap, "kiwoom_minutes_strictly_inside_gap": kiwoom_inside})
    rows = sorted(cases.values(), key=lambda row: (row["outcome_date"], row["code"]))
    counts = Counter({
        "stock_days": len(rows),
        "gaps_over_10m": len(gaps),
        "gaps_with_vi_overlap": sum(int(row["vi_overlapping_gap_count"]) for row in rows),
        "stock_days_with_creon_daily": sum(row["creon_daily_volume"] is not None for row in rows),
        "stock_days_exact_creon_volume": sum(
            row["creon_daily_volume"] is not None
            and row["creon_daily_volume_minus_minute_sum"] == 0 for row in rows
        ),
        "stock_days_exact_kiwoom_volume": sum(
            row["kiwoom_daily_volume"] is not None
            and row["kiwoom_daily_volume"] == row["creon_minute_volume_sum"] for row in rows
        ),
        "stock_days_with_kiwoom_minutes": sum(row["kiwoom_minute_count"] > 0 for row in rows),
        "stock_days_with_official_event_on_date": sum(
            bool(row["official_exchange_events_on_date"]) for row in rows
        ),
        "gaps_with_kiwoom_minute_reference": sum(
            row["kiwoom_minutes_strictly_inside_gap"] is not None for row in checked_gaps
        ),
        "gaps_with_kiwoom_minute_inside": sum(
            (row["kiwoom_minutes_strictly_inside_gap"] or 0) > 0 for row in checked_gaps
        ),
    })
    return {
        "version": "historical_monthly_selected_gap_evidence/v2",
        "gap_report_sha256": hashlib.sha256(raw).hexdigest(),
        "counts": dict(counts), "stock_days": rows, "gaps": checked_gaps,
        "limitations": [
            "Minute volume equality with a daily source supports no missing trade volume, "
            "but does not prove every minute timestamp is correct.",
            "Kiwoom adjusted daily volume can differ from CREON raw minute volume after corporate actions.",
            "CREON regular-session minute sums and daily volume can differ by session or auction scope.",
            "VI overlap does not explain an entire gap without interval-level evidence.",
            "No cross-check changes frozen cases, orders, or price bars.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gaps", type=Path, default=DEFAULT_GAPS)
    parser.add_argument("--market", type=Path, default=DEFAULT_MARKET)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--kiwoom", type=Path, default=DEFAULT_KIWOOM)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("gap evidence report is immutable")
    report = audit(args.gaps, args.market, args.context, args.kiwoom)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
