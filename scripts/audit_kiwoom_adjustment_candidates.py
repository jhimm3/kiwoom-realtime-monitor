"""Read-only daily-price gate for candidate Kiwoom-adjusted minute bars.

This is an audit, not a price converter: a daily close match cannot establish
that every intraday OHLC value matches Kiwoom's adjusted minute response.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from bisect import bisect_right
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path


DEFAULT_HISTORY = Path("C:/Users/pc-1/Desktop/kiwoom_history_backfill/data/kiwoom_history.sqlite3")
DEFAULT_CONTEXT = Path("data/historical_market_context.sqlite3")
DEFAULT_BARS = Path("data/historical_intelligence.sqlite3")


def _read_only_uri(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    return f"{path.resolve().as_uri()}?mode=ro"


def _ceil(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def assess_closes(pairs: list[tuple[int, int]], *, rounding: str = "half_up") -> dict:
    """Intersect factors where rounded(raw * factor) equals adjusted.

    Half-even includes a tie only when the adjusted integer is even. Four-
    decimal candidates are an audit heuristic, not a supplier contract.
    """
    if not pairs or any(raw <= 0 or adjusted < 0 for raw, adjusted in pairs):
        return {"status": "invalid_or_missing_close", "four_decimal_factor": None}
    if rounding not in ("half_up", "half_even"):
        raise ValueError(rounding)
    lower_bounds = [(Fraction(2 * adjusted - 1, 2 * raw),
                     rounding == "half_up" or adjusted % 2 == 0) for raw, adjusted in pairs]
    upper_bounds = [(Fraction(2 * adjusted + 1, 2 * raw),
                     rounding == "half_even" and adjusted % 2 == 0) for raw, adjusted in pairs]
    lower = max(value for value, _ in lower_bounds)
    upper = min(value for value, _ in upper_bounds)
    lower_included = all(included for value, included in lower_bounds if value == lower)
    upper_included = all(included for value, included in upper_bounds if value == upper)
    if lower > upper or (lower == upper and not (lower_included and upper_included)):
        return {"status": "exclude_conflict", "four_decimal_factor": None}
    first = _ceil(lower * 10_000) if lower_included else lower.numerator * 10_000 // lower.denominator + 1
    last = upper.numerator * 10_000 // upper.denominator if upper_included else _ceil(upper * 10_000) - 1
    count = max(0, last - first + 1)
    if len(pairs) == 1:
        status = "insufficient_single_day"
    elif count == 0:
        status = "unresolved_precision"
    elif count > 1:
        status = "ambiguous_factor"
    else:
        status = "candidate_unique_4dp"
    return {
        "status": status,
        "four_decimal_factor": f"{first / 10_000:.4f}" if count == 1 else None,
        "four_decimal_candidate_count": count,
        "factor_lower": f"{lower.numerator}/{lower.denominator}",
        "factor_lower_included": lower_included,
        "factor_upper": f"{upper.numerator}/{upper.denominator}",
        "factor_upper_included": upper_included,
    }


def build_report(history: Path, context: Path, bars: Path, last_date_exclusive: str | None = None,
                 rounding: str = "half_up") -> dict:
    with sqlite3.connect(_read_only_uri(context), uri=True) as db:
        db.execute("ATTACH DATABASE ? AS hist", (_read_only_uri(history),))
        db.execute("ATTACH DATABASE ? AS market", (_read_only_uri(bars),))
        jobs = {
            str(code): (int(one), int(five))
            for code, one, five in db.execute("""
                SELECT j.code,j.one_minute_bars,j.five_minute_bars
                FROM market.market_backfill_jobs j
                JOIN hist.stocks s ON s.code=j.code
                WHERE s.market_code IN ('0','10')
            """)
        }
        events: dict[str, list[str]] = defaultdict(list)
        for code, event_date in db.execute("""
            SELECT code,adjustment_date FROM historical_stock_adjustments
            WHERE provider='daishin_creon' ORDER BY code,adjustment_date
        """):
            if code in jobs and (not events[code] or events[code][-1] != event_date):
                events[code].append(event_date)
        groups: dict[tuple[str, int], list[tuple[str, int, int]]] = defaultdict(list)
        pair_count = 0
        for code, date, raw, adjusted in db.execute("""
            SELECT f.code,f.trade_date,f.close,d.close
            FROM historical_stock_fundamentals f
            JOIN hist.daily_bars d ON d.code=f.code AND d.dt=f.trade_date
            JOIN market.market_backfill_jobs j ON j.code=f.code
            JOIN hist.stocks s ON s.code=f.code
            WHERE f.provider='daishin_creon' AND s.market_code IN ('0','10')
            ORDER BY f.code,f.trade_date
        """):
            if last_date_exclusive and date >= last_date_exclusive:
                continue
            if raw is None or adjusted is None:
                continue
            if int(raw) != raw:
                raise ValueError(f"Noninteger raw close: {code} {date} {raw}")
            groups[(code, bisect_right(events[code], date))].append((date, int(raw), int(adjusted)))
            pair_count += 1
        records = []
        for (code, epoch), observations in sorted(groups.items()):
            result = assess_closes([(raw, adjusted) for _, raw, adjusted in observations], rounding=rounding)
            event_dates = events[code]
            records.append({
                "code": code,
                "epoch_start_inclusive": event_dates[epoch - 1] if epoch else None,
                "epoch_end_exclusive": event_dates[epoch] if epoch < len(event_dates) else None,
                "first_observed_date": observations[0][0],
                "last_observed_date": observations[-1][0],
                "daily_close_pairs": len(observations),
                "has_one_minute_bars": jobs[code][0] > 0,
                "has_five_minute_bars": jobs[code][1] > 0,
                "verification_level": "candidate_daily_close_only",
                **result,
            })
        counts = Counter(item["status"] for item in records)
        return {
            "schema": "kiwoom_adjustment_candidate_audit/v2",
            "rounding_model": rounding,
            "last_date_exclusive": last_date_exclusive,
            "source_databases": {"history": str(history.resolve()), "context": str(context.resolve()), "bars": str(bars.resolve())},
            "assumption": "Within each CREON adjustment-date epoch, Kiwoom adjusted daily close = rounded(CREON raw daily close * one positive factor) using the named rounding model. Four-decimal grid is exploratory; intraday OHLC and volume are not verified by this report.",
            "summary": {
                "target_stock_codes": len(jobs),
                "target_codes_without_creon_bars": sorted(code for code, (one, five) in jobs.items() if not one and not five),
                "matched_daily_close_pairs": pair_count,
                "observed_code_epochs": len(records),
                "status_counts": dict(sorted(counts.items())),
                "excluded_conflict_codes": len({r["code"] for r in records if r["status"] == "exclude_conflict"}),
                "minute_verified_epochs": 0,
            },
            "code_epochs": records,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-db", type=Path, default=DEFAULT_HISTORY)
    parser.add_argument("--context-db", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--bars-db", type=Path, default=DEFAULT_BARS)
    parser.add_argument("--last-date-exclusive", help="Optional YYYY-MM-DD daily observation cutoff.")
    parser.add_argument("--rounding", choices=("half_up", "half_even"), default="half_up")
    parser.add_argument("--output", type=Path, help="Write a new audit JSON file (never overwrite).")
    args = parser.parse_args()
    report = build_report(args.history_db, args.context_db, args.bars_db, args.last_date_exclusive, args.rounding)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as target:
            json.dump(report, target, ensure_ascii=False, indent=2)
            target.write("\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
