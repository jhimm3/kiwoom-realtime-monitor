"""Compare D03 minute gaps with archived CREON pages and saved minute rows.

An identical archived response and database proves the importer did not omit
those bars. It does not prove whether a provider-side gap means no trades.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path


DEFAULT_GAPS = Path("data/research/historical-monthly-selected-gap-vi-check-20260924.json")
DEFAULT_MARKET = Path("data/historical_intelligence.sqlite3")


def _read_day_raw(path: Path, day: str) -> tuple[dict[str, int], int]:
    bars: dict[str, int] = {}
    conflicts = 0
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            page = json.loads(line)
            if page.get("record_type") != "page" or page.get("interval_seconds") != 60:
                continue
            for bar in page.get("bars", []):
                bar_time = str(bar["bar_time"])
                if not bar_time.startswith(day + "T"):
                    continue
                volume = int(bar["volume"])
                if bar_time in bars and bars[bar_time] != volume:
                    conflicts += 1
                bars[bar_time] = volume
    return bars, conflicts


def audit(gap_file: Path, market_path: Path) -> dict[str, object]:
    gap_bytes = gap_file.read_bytes()
    gaps = json.loads(gap_bytes)["gaps"]
    source_root = market_path.resolve(strict=True).parent / "historical_collection" / "daishin"
    cases: dict[tuple[str, str], dict[str, object]] = {}
    checked_gaps = []
    with closing(sqlite3.connect(market_path.resolve().as_uri() + "?mode=ro", uri=True)) as market:
        market.execute("PRAGMA query_only=ON")
        for gap in gaps:
            code, day = str(gap["code"]), str(gap["outcome_date"])
            key = (code, day)
            if key not in cases:
                row = market.execute(
                    "SELECT raw_directory FROM market_backfill_jobs WHERE code=? AND state='complete'",
                    (code,),
                ).fetchone()
                if not row:
                    raise ValueError(f"completed CREON source is unavailable for {code}")
                source = (Path(row[0]) / "1m.ndjson").resolve(strict=True)
                if not source.is_relative_to(source_root.resolve(strict=True)):
                    raise ValueError(f"CREON source escaped archive root for {code}")
                raw_bars, conflicts = _read_day_raw(source, day)
                saved_bars = {
                    str(ts): int(volume) for ts, volume in market.execute(
                        "SELECT bar_time,volume FROM market_bars "
                        "WHERE provider='daishin_creon' AND code=? "
                        "AND venue IN ('K','KRX') AND session_scope='regular' "
                        "AND interval_seconds=60 AND adjustment_mode='raw' "
                        "AND bar_time>=? AND bar_time<?",
                        (code, day, day + "T23:59:59"),
                    )
                }
                raw_keys, saved_keys = set(raw_bars), set(saved_bars)
                cases[key] = {
                    "code": code, "outcome_date": day,
                    "raw_file": str(source),
                    "raw_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "raw_day_bar_count": len(raw_bars),
                    "saved_day_bar_count": len(saved_bars),
                    "raw_duplicate_volume_conflicts": conflicts,
                    "raw_bars_missing_in_saved": len(raw_keys - saved_keys),
                    "saved_bars_absent_from_raw": len(saved_keys - raw_keys),
                    "volume_mismatches": sum(
                        raw_bars[ts] != saved_bars[ts] for ts in raw_keys & saved_keys
                    ),
                    "_raw_times": raw_keys,
                }
            case = cases[key]
            left, right = str(gap["from_bar"]), str(gap["to_bar"])
            inside = sorted(ts for ts in case["_raw_times"] if left < ts < right)
            checked_gaps.append({
                "code": code, "outcome_date": day,
                "from_bar": left, "to_bar": right,
                "gap_seconds": int(gap["gap_seconds"]),
                "raw_bars_strictly_inside_gap": len(inside),
            })
    rows = sorted(cases.values(), key=lambda row: (row["outcome_date"], row["code"]))
    for row in rows:
        del row["_raw_times"]
    counts = Counter({
        "stock_days": len(rows),
        "gaps_over_10m": len(checked_gaps),
        "stock_days_raw_equals_saved": sum(
            row["raw_duplicate_volume_conflicts"] == 0
            and row["raw_bars_missing_in_saved"] == 0
            and row["saved_bars_absent_from_raw"] == 0
            and row["volume_mismatches"] == 0 for row in rows
        ),
        "gaps_with_raw_bar_inside": sum(
            row["raw_bars_strictly_inside_gap"] > 0 for row in checked_gaps
        ),
    })
    return {
        "version": "historical_monthly_selected_gap_raw_provenance/v1",
        "gap_report_sha256": hashlib.sha256(gap_bytes).hexdigest(),
        "counts": dict(counts), "stock_days": rows, "gaps": checked_gaps,
        "limitations": [
            "Raw response equality rules out an importer omission for the archived attempt only.",
            "A provider response without a minute bar does not prove the market had no trade.",
            "No frozen case, source response, or price bar was modified.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gaps", type=Path, default=DEFAULT_GAPS)
    parser.add_argument("--market", type=Path, default=DEFAULT_MARKET)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("raw gap provenance report is immutable")
    report = audit(args.gaps, args.market)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
