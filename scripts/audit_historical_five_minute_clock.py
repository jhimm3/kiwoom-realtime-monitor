"""Read-only CREON five-minute clock and per-day resolution audit."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

from kiwoom_monitor.application.market_session_schedule import KST, krx_regular_session_hours


def expected_clock_bins(trading_date: date | None = None) -> set[str]:
    trading_date = trading_date or date(2000, 1, 1)
    regular_open, regular_close = krx_regular_session_hours(trading_date)
    current = datetime.combine(trading_date, regular_open) + timedelta(minutes=5)
    last_continuous = datetime.combine(trading_date, regular_close) - timedelta(minutes=10)
    bins = set()
    while current <= last_continuous:
        bins.add(current.strftime("%H:%M"))
        current += timedelta(minutes=5)
    bins.add(regular_close.strftime("%H:%M"))
    return bins


def summarize_day(code: str, day: str, rows: list[dict]) -> dict:
    trading_date = date.fromisoformat(day)
    clocks = []
    invalid_semantics = 0
    invalid_ohlcv = 0
    one_minute_count = 0
    for row in rows:
        interval = int(row["interval_seconds"])
        if interval == 60:
            one_minute_count += 1
            continue
        if interval != 300:
            raise ValueError("unsupported bar interval")
        end = datetime.fromisoformat(str(row["bar_time"]))
        if end.tzinfo is None or end.astimezone(KST).date().isoformat() != day:
            raise ValueError("five-minute end must be timezone-aware and on the selected KST day")
        clock = end.astimezone(KST).strftime("%H:%M")
        clocks.append(clock)
        if row["bar_time_semantics"] != "interval_end":
            invalid_semantics += 1
        open_, high, low, close, volume = (int(row[key]) for key in ("open", "high", "low", "close", "volume"))
        if not (low <= open_ <= high and low <= close <= high and volume >= 0):
            invalid_ohlcv += 1
    observed = set(clocks)
    expected = expected_clock_bins(trading_date)
    regular_open, regular_close = krx_regular_session_hours(trading_date)
    auction_start = (datetime.combine(trading_date, regular_close) - timedelta(minutes=10)).time()
    close_clock = regular_close.strftime("%H:%M")
    return {
        "code": code, "trading_date": day, "five_minute_rows": len(clocks),
        "one_minute_rows": one_minute_count,
        "mixed_resolution_day": bool(clocks and one_minute_count),
        "expected_clock_bin_count": len(expected),
        "observed_clock_bin_count": len(observed),
        "duplicate_clock_rows": len(clocks) - len(observed),
        "unobserved_expected_clock_bins": sorted(expected - observed) if clocks else [],
        "unexpected_clock_bins": sorted(observed - expected),
        "close_auction_print_present": close_clock in observed,
        "five_minute_source_present": bool(clocks),
        "invalid_interval_end_semantics": invalid_semantics,
        "invalid_ohlcv_rows": invalid_ohlcv,
        "clock_policy": f"{(datetime.combine(trading_date, regular_open) + timedelta(minutes=5)):%H:%M}..{auction_start:%H:%M} five-minute ends; {close_clock} separate closing auction print",
        "unobserved_bin_meaning": "unobserved, not automatically a provider gap or zero trading",
    }


def load_rows(connection: sqlite3.Connection, code: str, day: str) -> list[dict]:
    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT interval_seconds,bar_time,bar_time_semantics,open,high,low,close,volume "
        "FROM market_bars WHERE provider='daishin_creon' AND code=? "
        "AND venue IN ('K','KRX') AND session_scope='regular' AND adjustment_mode='raw' "
        "AND interval_seconds IN (60,300) AND bar_time>=? AND bar_time<? ORDER BY bar_time",
        (code, f"{day}T00:00", f"{next_day}T00:00"),
    ).fetchall()
    return [dict(row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/historical_intelligence.sqlite3"))
    parser.add_argument("--date", required=True)
    parser.add_argument("--code", action="append", required=True)
    args = parser.parse_args()
    date.fromisoformat(args.date)
    codes = list(dict.fromkeys(args.code))
    if len(codes) > 20 or any(len(code) != 6 or not code.isdigit() for code in codes):
        parser.error("provide 1-20 six-digit stock codes")
    path = args.database.resolve(strict=True)
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10) as connection:
        results = [summarize_day(code, args.date, load_rows(connection, code, args.date))
                   for code in codes]
    print(json.dumps({"version": "historical_five_minute_clock/v1", "read_only": True,
                      "results": results}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
