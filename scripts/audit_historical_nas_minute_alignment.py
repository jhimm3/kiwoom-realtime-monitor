"""Compare CREON raw regular-session minutes with stored NAS Kiwoom minutes."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from kiwoom_monitor.application.market_session_schedule import KST, krx_regular_session_hours
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig
from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient


FIELDS = ("open", "high", "low", "close", "volume")


def compare_day(code: str, day: str, creon_rows: list[dict], nas_rows: list[dict],
                nas_complete: bool) -> dict:
    """Align ordinary ends to NAS starts; preserve the date-specific close print."""
    regular_open, regular_close = krx_regular_session_hours(date.fromisoformat(day))
    open_clock, close_clock = regular_open.strftime("%H:%M"), regular_close.strftime("%H:%M")
    local: dict[str, tuple[int, ...]] = {}
    for row in creon_rows:
        end = datetime.fromisoformat(str(row["bar_time"]))
        if end.tzinfo is None or end.astimezone(KST).date().isoformat() != day:
            raise ValueError("CREON bar_time must be a timezone-aware selected-day interval end")
        local_end = end.astimezone(KST)
        minute = (local_end if local_end.time() == regular_close
                  else local_end - timedelta(minutes=1)).strftime("%H:%M")
        if minute in local:
            raise ValueError("duplicate CREON regular minute")
        local[minute] = tuple(int(row[field]) for field in FIELDS)

    remote: dict[str, tuple[int, ...]] = {}
    outside_regular = 0
    duplicate_nas = 0
    for row in nas_rows:
        minute = str(row["minute"])[:5]
        if not open_clock <= minute <= close_clock:
            outside_regular += 1
            continue
        if minute in remote:
            duplicate_nas += 1
            continue
        remote[minute] = tuple(int(row[field]) for field in FIELDS)
    shared = local.keys() & remote.keys()
    mismatch = sorted(minute for minute in shared if local[minute] != remote[minute])
    return {
        "code": code, "trading_date": day, "creon_raw_regular_minutes": len(local),
        "nas_krx_regular_minutes": len(remote), "nas_outside_regular_minutes": outside_regular,
        "nas_duplicate_regular_minutes": duplicate_nas, "nas_coverage_complete": nas_complete,
        "shared_minutes": len(shared), "matching_ohlcv": len(shared) - len(mismatch),
        "mismatching_ohlcv": len(mismatch), "creon_only_minutes": sorted(local.keys() - remote.keys()),
        "nas_only_regular_minutes": sorted(remote.keys() - local.keys()),
        "mismatching_minutes": mismatch,
        "price_basis": "CREON raw vs NAS stored Kiwoom; NAS adjustment basis must be verified per event",
        "clock_alignment": f"ordinary CREON interval end minus 60s; {close_clock} close print retains its clock",
        "five_minute_comparison": "not_performed",
    }


def load_creon_rows(connection: sqlite3.Connection, code: str, day: str) -> list[dict]:
    next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT bar_time,open,high,low,close,volume FROM market_bars "
        "WHERE provider='daishin_creon' AND code=? AND venue IN ('K','KRX') "
        "AND session_scope='regular' AND adjustment_mode='raw' AND interval_seconds=60 "
        "AND bar_time>=? AND bar_time<? ORDER BY bar_time",
        (code, f"{day}T00:00", f"{next_day}T00:00"),
    ).fetchall()
    return [dict(row) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/historical_intelligence.sqlite3"))
    parser.add_argument("--config", type=Path, default=Path("data/data_source.json"))
    parser.add_argument("--date", required=True)
    parser.add_argument("--code", action="append", required=True, help="Repeat for each six-digit stock code")
    args = parser.parse_args()
    date.fromisoformat(args.date)
    codes = list(dict.fromkeys(args.code))
    if len(codes) > 20 or any(len(code) != 6 or not code.isdigit() for code in codes):
        parser.error("provide 1-20 six-digit stock codes")
    config = DataSourceConfig(args.config).load()
    if config.mode not in ("personal_server", "local_server"):
        parser.error("NAS connection setting is required")
    client = RemoteKiwoomRestClient(config.server_url, config.access_token, timeout_seconds=10)
    path = args.database.resolve(strict=True)
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10) as connection:
        results = []
        for code in codes:
            local = load_creon_rows(connection, code, args.date)
            remote, complete = client.load_stored_minute_bars_with_coverage(code, args.date, "KRX")
            results.append(compare_day(code, args.date, local, list(remote), complete))
    print(json.dumps({"version": "historical_nas_minute_alignment/v1", "read_only": True,
                      "results": results}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
