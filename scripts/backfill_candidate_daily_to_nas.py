"""Fill missing seven-year candidate daily bars through the NAS Kiwoom broker.

This client only orchestrates requests. Kiwoom calls and bar writes happen on
the NAS through /api/v1/kiwoom/query; the candidate SQLite is read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


KST = timezone(timedelta(hours=9))
DEFAULT_CANDIDATES = Path(r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3")
DEFAULT_ENV = Path(r"X:\kiwoom-monitor\deploy\synology\.env")
DEFAULT_STATUS = Path("data/historical_collection/kiwoom_candidate_daily_7y_status.json")


def candidate_codes(connection: sqlite3.Connection) -> list[str]:
    """Only KOSPI/KOSDAQ individual stocks that actually entered the candidate set."""
    return [str(row[0]) for row in connection.execute(
        "SELECT DISTINCT c.code FROM candidate_days c "
        "JOIN stocks s ON s.code=c.code "
        "WHERE s.market_code IN ('0','10') AND length(c.code)=6 "
        "ORDER BY c.code"
    )]


def _token(path: Path) -> str:
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("MONITOR_SERVER_ACCESS_TOKEN="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise ValueError("NAS access token variable missing")


def _request(url: str, token: str, *, body: dict | None = None, timeout: float = 60.0) -> dict:
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    request = Request(url, data=data, headers={
        "Authorization": f"Bearer {token}",
        **({"Content-Type": "application/json"} if data is not None else {}),
    })
    with urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("NAS response must be an object")
    return value


def _save_status(path: Path, status: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    status["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                return
            except PermissionError as error:
                if getattr(error, "winerror", None) not in {5, 32} or attempt == 9:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def _cutoff(as_of: datetime) -> str:
    try:
        return as_of.replace(year=as_of.year - 7).date().isoformat()
    except ValueError:  # February 29
        return as_of.replace(year=as_of.year - 7, day=28).date().isoformat()


def _pause_for_market(status: dict, path: Path) -> None:
    while True:
        now = datetime.now(KST)
        if now.weekday() >= 5 or not (9 <= now.hour < 16):
            if status.get("phase") == "market_hours_pause":
                status["phase"] = "running"
                _save_status(path, status)
            return
        if status.get("phase") != "market_hours_pause":
            status["phase"] = "market_hours_pause"
            _save_status(path, status)
        time.sleep(30)


def _local_dates(connection: sqlite3.Connection, code: str, cutoff: str) -> set[str]:
    return {str(row[0]) for row in connection.execute(
        "SELECT dt FROM daily_bars WHERE code=? AND dt>=?", (code, cutoff)
    )}


def _nas_dates(base_url: str, token: str, code: str) -> set[str]:
    query = urlencode({"code": code, "market": "KRX", "limit": 5000})
    result = _request(f"{base_url}/api/v1/market/daily-bars?{query}", token)
    bars = result.get("bars")
    if not isinstance(bars, list):
        raise ValueError(f"{code}: NAS daily-bars list missing")
    return {str(bar["trading_date"]) for bar in bars if isinstance(bar, dict)}


def _collect_code(base_url: str, token: str, code: str, as_of: str, cutoff: str,
                  status: dict, path: Path, request_delay: float,
                  pause_market_hours: bool,
                  fallback_base_dt: str = "") -> tuple[int, str, str]:
    body = {"stk_cd": code, "base_dt": as_of.replace("-", ""), "upd_stkpc_tp": "1"}
    query_base_dt = as_of
    continuation, next_key = "N", ""
    pages, oldest = 0, "9999-99-99"
    seen_keys: set[str] = set()
    while True:
        if pause_market_hours:
            _pause_for_market(status, path)
        response = _request(f"{base_url}/api/v1/kiwoom/query", token, body={
            "api_id": "ka10081", "path": "/api/dostk/chart", "body": body,
            "cont_yn": continuation, "next_key": next_key,
        })
        if response.get("archive_hit"):
            raise RuntimeError(f"{code}: archived 250-row response blocks full-history continuation")
        rows = response.get("payload", {}).get("stk_dt_pole_chart_qry")
        if not isinstance(rows, list) or not rows:
            if pages == 0:
                return 0, "", query_base_dt  # delisted or unavailable: report separately
            if response.get("has_next"):
                raise RuntimeError(f"{code}: empty continuing page")
            break
        dates = [str(row.get("dt", row.get("date", ""))) for row in rows if isinstance(row, dict)]
        dates = [f"{value[:4]}-{value[4:6]}-{value[6:8]}" for value in dates if len(value) == 8 and value.isdigit()]
        if not dates:
            # A date after the last trading day can return a successful empty
            # placeholder even though the same code has historical chart pages.
            if pages == 0 and not response.get("has_next") and all(
                isinstance(row, dict) and not any(str(value or "").strip() for value in row.values())
                for row in rows
            ):
                if fallback_base_dt and fallback_base_dt < as_of and query_base_dt == as_of:
                    query_base_dt = fallback_base_dt
                    body["base_dt"] = fallback_base_dt.replace("-", "")
                    status.update(current_code=code, current_query_base_dt=query_base_dt,
                                  current_pages=0, phase="running")
                    _save_status(path, status)
                    time.sleep(request_delay)
                    continue
                return 0, "", query_base_dt
            raise RuntimeError(f"{code}: page without valid daily dates")
        oldest = min(oldest, *dates)
        pages += 1
        status.update(current_code=code, current_query_base_dt=query_base_dt,
                      current_pages=pages, current_oldest=oldest,
                      current_rows=len(rows), phase="running")
        _save_status(path, status)
        if oldest <= cutoff or not response.get("has_next"):
            break
        next_key = str(response.get("next_key") or "")
        if not next_key or next_key in seen_keys:
            raise RuntimeError(f"{code}: missing or repeated continuation key")
        seen_keys.add(next_key)
        continuation = "Y"
        time.sleep(request_delay)
    return pages, oldest, query_base_dt


def run(args: argparse.Namespace) -> int:
    as_of = datetime.fromisoformat(args.as_of).replace(tzinfo=KST) if args.as_of else datetime.now(KST)
    cutoff = _cutoff(as_of)
    as_of_day = as_of.date().isoformat()
    token = _token(args.env_file)
    base_url = args.url.rstrip("/")
    health = _request(f"{base_url}/health", token)
    if health.get("status") != "ok":
        raise RuntimeError("NAS health is not ok")
    connection = sqlite3.connect(f"file:{args.candidates.as_posix()}?mode=ro", uri=True)
    try:
        codes = candidate_codes(connection)
        if args.codes:
            selected = set(args.codes.split(","))
            codes = [code for code in codes if code in selected]
        if args.max_codes:
            codes = codes[:args.max_codes]
        status = json.loads(args.status.read_text(encoding="utf-8")) if args.status.exists() else {}
        if status and (status.get("cutoff") != cutoff or status.get("as_of") != as_of_day):
            raise ValueError("status belongs to another as-of date; use another --status path")
        status.setdefault("codes", {})
        status.update(schema="kiwoom-candidate-daily-7y/v1", as_of=as_of_day,
                      cutoff=cutoff, total_selected=len(codes), server_build=health.get("server_build"),
                      phase="running")
        _save_status(args.status, status)
        for code in codes:
            if status["codes"].get(code, {}).get("state") == "complete":
                continue
            try:
                if args.pause_market_hours:
                    _pause_for_market(status, args.status)
                expected = _local_dates(connection, code, cutoff)
                existing = _nas_dates(base_url, token, code)
                missing_before = len(expected - existing)
                # Query the entire span even if NAS already has the dates: an older
                # adjusted series may have used a different Kiwoom base_dt.
                pages, oldest, query_base_dt = _collect_code(
                    base_url, token, code, as_of_day, cutoff,
                    status, args.status, args.request_delay, args.pause_market_hours,
                    max(expected) if expected else "",
                )
                after = _nas_dates(base_url, token, code)
                missing_after = len(expected - after)
                result = {"state": ("complete" if missing_after == 0 and pages
                                    else "unavailable" if not pages else "incomplete"),
                          "reason": "queried" if pages else "no_rows", "pages": pages,
                          "oldest_fetched": oldest, "query_base_dt": query_base_dt,
                          "missing_before": missing_before,
                          "missing_after": missing_after}
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as error:
                try:
                    health = _request(f"{base_url}/health", token, timeout=5.0)
                    nas_available = health.get("status") == "ok"
                except (HTTPError, URLError, TimeoutError, OSError, ValueError):
                    nas_available = False
                if not nas_available:
                    status["phase"] = "nas_unavailable"
                    status["last_error"] = f"{type(error).__name__}: {error}"[:300]
                    status["current_code"] = code
                    _save_status(args.status, status)
                    print(f"NAS unavailable at {code}; stopping without marking further codes failed", flush=True)
                    return 2
                result = {"state": "failed", "error": f"{type(error).__name__}: {error}"[:300]}
            status["codes"][code] = result
            status["current_code"] = code
            status["phase"] = "running"
            _save_status(args.status, status)
            print(f"{code} {result['state']} pages={result.get('pages', 0)} "
                  f"missing={result.get('missing_after', '?')}", flush=True)
            time.sleep(args.request_delay)
        states = [value["state"] for value in status["codes"].values()]
        status["phase"] = "complete" if all(value == "complete" for value in states) else "attention_required"
        _save_status(args.status, status)
        return 0 if status["phase"] == "complete" else 1
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--url", default="http://192.168.0.5:8787")
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--as-of", help="fixed KST reference date, YYYY-MM-DD")
    parser.add_argument("--codes", help="comma-separated individual-stock codes for a probe")
    parser.add_argument("--max-codes", type=int, default=0)
    parser.add_argument("--request-delay", type=float, default=0.5)
    parser.add_argument("--pause-market-hours", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    try:
        return run(args)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"backfill stopped: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
