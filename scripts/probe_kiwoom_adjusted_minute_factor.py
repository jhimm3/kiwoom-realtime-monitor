"""Bounded live ka10080 adjusted-minute probe against stored CREON raw bars.

The request uses the project's KiwoomRestClient rate limit. It writes only a
new diagnostic JSON file; neither source DB nor the application DB is edited.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from audit_kiwoom_adjusted_minute_overlap import compare_day, round_half_even_scaled
from audit_kiwoom_adjustment_candidates import DEFAULT_BARS, _read_only_uri, assess_closes
from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig


def probe(code: str, day: str, base_day: str, factor_text: str, bars_db: Path,
          api_config: Path, max_pages: int) -> dict:
    if not day < base_day:
        raise ValueError("The reference date must be after the target day")
    factor = int(factor_text.replace(".", ""))
    client = KiwoomRestClient(LocalApiConfig(api_config).load())
    body = {"stk_cd": code, "tic_scope": "1", "upd_stkpc_tp": "1", "base_dt": base_day.replace("-", "")}
    live = {}
    pages = 0
    cont_yn, next_key = "N", ""
    reached_target = False
    while pages < max_pages:
        payload, has_next, response_next_key = client.request_with_continuation(
            "ka10080", "/api/dostk/chart", body, cont_yn=cont_yn, next_key=next_key
        )
        pages += 1
        records = payload.get("stk_min_pole_chart_qry", [])
        if not isinstance(records, list):
            raise ValueError("Unexpected ka10080 minute record format")
        for record in records:
            if not isinstance(record, dict):
                continue
            stamp = str(record.get("cntr_tm", ""))
            if len(stamp) != 14 or not stamp.isdigit():
                continue
            date = stamp[:8]
            if date < day.replace("-", ""):
                reached_target = True
            if date != day.replace("-", ""):
                continue
            try:
                values = tuple(abs(int(str(record[key]).replace(",", ""))) for key in
                               ("open_pric", "high_pric", "low_pric", "cur_prc", "trde_qty"))
            except (KeyError, TypeError, ValueError):
                continue
            live[f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[8:10]}:{stamp[10:12]}:00"] = values
        if reached_target or not has_next or not response_next_key:
            break
        cont_yn, next_key = "Y", response_next_key
    next_day = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
    raw = {}
    with sqlite3.connect(_read_only_uri(bars_db), uri=True) as db:
        for bar_time, *values in db.execute("""
            SELECT bar_time,open,high,low,close,volume FROM market_bars
            WHERE provider='daishin_creon' AND code=? AND venue='K'
            AND session_scope='regular' AND interval_seconds=60
            AND adjustment_mode='raw' AND bar_time>=? AND bar_time<?
        """, (code, day + "T", next_day + "T")):
            if any(value is None for value in values):
                continue
            minute = datetime.fromisoformat(bar_time) - timedelta(minutes=1)
            raw[minute.strftime("%Y-%m-%d %H:%M:%S")] = tuple(int(value) for value in values)
    price_pairs = [
        (raw_price, adjusted_price)
        for minute in sorted(live.keys() & raw.keys())
        for adjusted_price, raw_price in zip(live[minute][:4], raw[minute][:4])
    ]
    return {
        "code": code, "target_day": day, "kiwoom_base_dt": base_day,
        "candidate_factor": factor_text, "pages": pages,
        "target_day_reached": reached_target,
        "comparison": compare_day(live, raw, factor),
        "factor_interval_half_up": assess_closes(price_pairs, rounding="half_up"),
        "factor_interval_half_even": assess_closes(price_pairs, rounding="half_even"),
        "live_target_bars": len(live),
        "live_first_minute": min(live) if live else None,
        "live_last_minute": max(live) if live else None,
        "mismatches": [
            {"minute": minute, "kiwoom": live[minute], "creon_raw": raw[minute]}
            for minute in sorted(live.keys() & raw.keys())
            if any(round_half_even_scaled(raw_price, factor) != actual
                   for actual, raw_price in zip(live[minute][:4], raw[minute][:4]))
        ][:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", required=True)
    parser.add_argument("--day", required=True)
    parser.add_argument("--base-day", required=True)
    parser.add_argument("--factor", required=True)
    parser.add_argument("--bars-db", type=Path, default=DEFAULT_BARS)
    parser.add_argument("--api-config", type=Path, default=Path("data/api.env"))
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe(args.code, args.day, args.base_day, args.factor, args.bars_db,
                   args.api_config, args.max_pages)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as destination:
        json.dump(result, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
    print(json.dumps({key: result[key] for key in ("code", "target_day", "kiwoom_base_dt", "pages", "target_day_reached", "comparison")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
