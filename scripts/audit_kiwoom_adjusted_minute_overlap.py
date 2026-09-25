"""Compare stored Kiwoom candidate-day minutes with raw CREON minutes.

Stored Kiwoom minutes were requested with base_dt=candidate day. Consequently,
a mismatch against a later-reference daily factor is not proof that the factor
is wrong. A full match is direct evidence only for the recorded day and bars.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from audit_kiwoom_adjustment_candidates import DEFAULT_BARS, DEFAULT_HISTORY, _read_only_uri


DEFAULT_CANDIDATES = Path("data/historical_collection/kiwoom-adjustment-candidate-audit-20260924.json")


def round_half_up_scaled(price: int, factor_scaled: int) -> int:
    if price < 0 or factor_scaled < 0:
        raise ValueError("Stock prices and adjustment factors must be nonnegative")
    return (price * factor_scaled + 5_000) // 10_000


def round_half_even_scaled(price: int, factor_scaled: int) -> int:
    if price < 0 or factor_scaled < 0:
        raise ValueError("Stock prices and adjustment factors must be nonnegative")
    whole, remainder = divmod(price * factor_scaled, 10_000)
    return whole + (remainder > 5_000 or (remainder == 5_000 and whole % 2 == 1))


def compare_day(kiwoom: dict[str, tuple[int, ...]], creon: dict[str, tuple[int, ...]], factor: int) -> dict:
    common = kiwoom.keys() & creon.keys()
    adjusted_same = sum(
        all(round_half_up_scaled(raw, factor) == actual for actual, raw in zip(kiwoom[minute][:4], creon[minute][:4]))
        for minute in common
    )
    half_even_same = sum(
        all(round_half_even_scaled(raw, factor) == actual for actual, raw in zip(kiwoom[minute][:4], creon[minute][:4]))
        for minute in common
    )
    raw_same = sum(kiwoom[minute][:4] == creon[minute][:4] for minute in common)
    volume_same = sum(kiwoom[minute][4] == creon[minute][4] for minute in common)
    if not common:
        evidence = "no_common_minutes"
    elif adjusted_same == len(common) and half_even_same == len(common):
        evidence = "all_common_ohlc_match_both_rounding_models"
    elif adjusted_same == len(common):
        evidence = "all_common_ohlc_match_half_up_candidate"
    elif half_even_same == len(common):
        evidence = "all_common_ohlc_match_half_even_candidate"
    else:
        evidence = "not_matched_or_insufficient"
    return {
        "kiwoom_minutes": len(kiwoom),
        "creon_minutes": len(creon),
        "common_minutes": len(common),
        "adjusted_ohlc_match_minutes": adjusted_same,
        "half_even_ohlc_match_minutes": half_even_same,
        "raw_ohlc_match_minutes": raw_same,
        "volume_match_minutes": volume_same,
        "direct_evidence": evidence,
    }


def _eligible_epoch(record: dict, day: str) -> bool:
    return (record["status"] == "candidate_unique_4dp"
            and (record["epoch_start_inclusive"] is None or record["epoch_start_inclusive"] <= day)
            and (record["epoch_end_exclusive"] is None or day < record["epoch_end_exclusive"]))


def audit(history: Path, bars: Path, candidates: Path, *, include_unit_factor: bool = False) -> dict:
    source = json.loads(candidates.read_text(encoding="utf-8"))
    epochs = defaultdict(list)
    for record in source["code_epochs"]:
        epochs[record["code"]].append(record)
    results = []
    with sqlite3.connect(_read_only_uri(history), uri=True) as ki, sqlite3.connect(_read_only_uri(bars), uri=True) as cr:
        for code, day, count in ki.execute("SELECT code,dt,COUNT(*) FROM minute_bars GROUP BY code,dt ORDER BY code,dt"):
            matches = [record for record in epochs.get(code, ()) if _eligible_epoch(record, day)]
            if len(matches) != 1:
                continue
            record = matches[0]
            factor_text = record["four_decimal_factor"]
            if factor_text == "1.0000" and not include_unit_factor:
                continue
            factor = int(factor_text.replace(".", ""))
            next_day = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
            ki_bars = {
                row[0]: tuple(int(value) for value in row[1:])
                for row in ki.execute("""
                    SELECT ts,open,high,low,close,volume FROM minute_bars
                    WHERE code=? AND ts>=? AND ts<?
                """, (code, day + " ", next_day + " "))
                if all(value is not None for value in row[1:])
            }
            cr_bars = {}
            for bar_time, *values in cr.execute("""
                SELECT bar_time,open,high,low,close,volume FROM market_bars
                WHERE provider='daishin_creon' AND code=? AND venue='K'
                AND session_scope='regular' AND interval_seconds=60
                AND adjustment_mode='raw' AND bar_time>=? AND bar_time<?
            """, (code, day + "T", next_day + "T")):
                if any(value is None for value in values):
                    continue
                start = datetime.fromisoformat(bar_time) - timedelta(minutes=1)
                cr_bars[start.strftime("%Y-%m-%d %H:%M:%S")] = tuple(int(value) for value in values)
            comparison = compare_day(ki_bars, cr_bars, factor)
            results.append({
                "code": code, "day": day, "candidate_factor": factor_text,
                "epoch_start_inclusive": record["epoch_start_inclusive"],
                "epoch_end_exclusive": record["epoch_end_exclusive"],
                "kiwoom_base_dt_at_collection": day,
                "kiwoom_job_minute_count": count,
                **comparison,
            })
    category = Counter()
    for row in results:
        common = row["common_minutes"]
        if not common:
            category["no_common_minutes"] += 1
        elif row["half_even_ohlc_match_minutes"] == common or row["adjusted_ohlc_match_minutes"] == common:
            category["candidate_factor_matches_all_common"] += 1
        elif row["raw_ohlc_match_minutes"] == common:
            category["stored_kiwoom_matches_raw_all_common"] += 1
        else:
            category["mixed_or_different"] += 1
    return {
        "schema": "kiwoom_adjusted_minute_overlap_audit/v1",
        "source_candidates": str(candidates.resolve()),
        "interpretation": "Kiwoom stored 1m uses base_dt=day; CREON raw 1m uses interval-end timestamp shifted back 1m. Full common-bar OHLC match supports this day only. Nonmatch may be base-date mismatch. Volume is reported separately and adjustment of volume is not assumed.",
        "summary": {
            "compared_code_days": len(results),
            "categories": dict(sorted(category.items())),
            "common_minutes": sum(row["common_minutes"] for row in results),
            "candidate_ohlc_match_minutes": sum(row["adjusted_ohlc_match_minutes"] for row in results),
            "half_even_ohlc_match_minutes": sum(row["half_even_ohlc_match_minutes"] for row in results),
            "raw_ohlc_match_minutes": sum(row["raw_ohlc_match_minutes"] for row in results),
        },
        "code_days": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-db", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--bars-db", type=Path, default=DEFAULT_BARS)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--include-unit-factor", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.history_db, args.bars_db, args.candidates, include_unit_factor=args.include_unit_factor)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as destination:
            json.dump(report, destination, ensure_ascii=False, indent=2)
            destination.write("\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
