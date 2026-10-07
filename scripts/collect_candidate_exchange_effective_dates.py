"""Resume official DART document collection for candidate trading-state dates.

The existing disclosure list is the source ledger. This script adds only new
document/effective-event tables; it never rewrites disclosure or price rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from kiwoom_monitor.infrastructure.exchange_effective_dates import (
    archive_rows, extract_effective_events,
)
from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig
from kiwoom_monitor.infrastructure.system_ssl import system_ssl_context


DEFAULT_CONTEXT = Path("data/historical_market_context.sqlite3")
DEFAULT_CONFIG = Path("data/naver_news.dat")
DEFAULT_RAW = Path("data/historical_collection/context/dart_effective")
EXCHANGE_FILERS = ("시장본부", "코넥스시장")


def selected_filings(connection: sqlite3.Connection) -> dict[str, list[tuple[str, str, str, str]]]:
    selected: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for code, receipt, receipt_date, title, filer in connection.execute(
        "SELECT code,receipt_no,receipt_date,report_name,filer_name "
        "FROM historical_candidate_exchange_disclosures ORDER BY receipt_no,code"
    ):
        if not any(name in filer for name in EXCHANGE_FILERS):
            continue
        core = re.sub(r"^(?:\[(?:기재정정|첨부추가)\])\s*", "", title).strip()
        if not (core.startswith(("주권매매거래정지", "매매거래정지및정지해제"))
                or (core.startswith("기타시장안내") and "상장폐지" in core)):
            continue
        selected[receipt].append((code, receipt_date, title, filer))
    return selected


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS historical_exchange_document_fetch (
            receipt_no TEXT PRIMARY KEY, receipt_date TEXT NOT NULL,
            state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            raw_file TEXT NOT NULL DEFAULT '', raw_sha256 TEXT NOT NULL DEFAULT '',
            parsed_event_count INTEGER NOT NULL DEFAULT 0,
            error_code TEXT NOT NULL DEFAULT '', fetched_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS historical_exchange_effective_events (
            code TEXT NOT NULL, receipt_no TEXT NOT NULL, receipt_date TEXT NOT NULL,
            kind TEXT NOT NULL, effective_date TEXT NOT NULL,
            effective_time TEXT NOT NULL DEFAULT '', precision TEXT NOT NULL,
            source_label TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            PRIMARY KEY(code,receipt_no,kind,effective_date,effective_time)
        );
        CREATE INDEX IF NOT EXISTS idx_historical_exchange_effective_events_code_date
        ON historical_exchange_effective_events(code,effective_date,kind);
    """)


def collect(context: Path, config: Path, raw_root: Path, *, jobs: int,
            delay_seconds: float, retry: bool = False) -> dict[str, object]:
    if jobs < 0 or delay_seconds < 0:
        raise ValueError("jobs and delay must not be negative")
    settings = LocalNaverNewsConfig(config.resolve(strict=True)).load_official()
    if not settings.dart_enabled or not settings.dart_api_key:
        raise RuntimeError("DART is not configured")
    raw_root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(context, timeout=60)) as connection:
        connection.execute("PRAGMA busy_timeout=60000")
        initialize(connection)
        filings = selected_filings(connection)
        states = dict(connection.execute(
            "SELECT receipt_no,state FROM historical_exchange_document_fetch"
        ))
        pending = [receipt for receipt in sorted(filings)
                   if receipt not in states or (retry and states[receipt] == "retryable")]
        processed = 0
        for receipt in pending[:jobs]:
            items = filings[receipt]
            raw_file = raw_root / f"{receipt}.zip"
            state = "complete"
            error_code = ""
            raw_hash = ""
            raw_bytes = b""
            events = ()
            try:
                if raw_file.exists():
                    raw_bytes = raw_file.read_bytes()
                else:
                    query = urlencode({"crtfc_key": settings.dart_api_key,
                                       "rcept_no": receipt})
                    request = Request("https://opendart.fss.or.kr/api/document.xml?" + query,
                                      headers={"User-Agent": "KiwoomRealtimeMonitor/Research"})
                    with urlopen(request, timeout=20, context=system_ssl_context()) as response:
                        raw_bytes = response.read()
                if raw_bytes[:2] != b"PK":
                    status = re.search(rb"<status>\s*(\d+)\s*</status>", raw_bytes)
                    error_code = status.group(1).decode("ascii") if status else "not_zip"
                    state = "api_limit" if error_code == "020" else "unavailable" if error_code == "014" else "retryable"
                else:
                    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
                    if not raw_file.exists():
                        temporary = raw_file.with_suffix(".zip.tmp")
                        temporary.write_bytes(raw_bytes)
                        temporary.replace(raw_file)
                    events = extract_effective_events(archive_rows(raw_bytes), exchange_filing=True)
                    if not events:
                        state = "parsed_no_explicit_transition"
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, UnicodeError) as exc:
                state = "retryable"
                error_code = f"{type(exc).__name__}:{getattr(exc, 'code', '')}"
            now = datetime.now(UTC).isoformat()
            with connection:
                connection.execute(
                    "INSERT INTO historical_exchange_document_fetch "
                    "(receipt_no,receipt_date,state,attempts,raw_file,raw_sha256,"
                    "parsed_event_count,error_code,fetched_at) VALUES(?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(receipt_no) DO UPDATE SET state=excluded.state,"
                    "attempts=historical_exchange_document_fetch.attempts+1,"
                    "raw_file=excluded.raw_file,raw_sha256=excluded.raw_sha256,"
                    "parsed_event_count=excluded.parsed_event_count,"
                    "error_code=excluded.error_code,fetched_at=excluded.fetched_at",
                    (receipt, items[0][1], state, 1,
                     str(raw_file) if raw_hash else "", raw_hash,
                     len(events) * len(items), error_code, now),
                )
                if raw_hash:
                    connection.executemany(
                        "INSERT OR IGNORE INTO historical_exchange_effective_events "
                        "(code,receipt_no,receipt_date,kind,effective_date,effective_time,"
                        "precision,source_label,raw_sha256,observed_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        [(code, receipt, receipt_date, event.kind, event.effective_date,
                          event.effective_time, event.precision, event.source_label,
                          raw_hash, now)
                         for code, receipt_date, _title, _filer in items for event in events],
                    )
            processed += 1
            if state == "api_limit":
                break
            if delay_seconds and processed < jobs:
                time.sleep(delay_seconds)
        counts = dict(connection.execute(
            "SELECT state,COUNT(*) FROM historical_exchange_document_fetch GROUP BY state"
        ))
        total_events = connection.execute(
            "SELECT COUNT(*) FROM historical_exchange_effective_events"
        ).fetchone()[0]
    return {"eligible_documents": len(filings), "processed_this_run": processed,
            "document_states": counts, "effective_events": total_events,
            "remaining_unattempted": len(filings) - len(states) - sum(
                1 for receipt in pending[:processed] if receipt not in states
            )}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--jobs", type=int, default=100)
    parser.add_argument("--delay-seconds", type=float, default=0.3)
    parser.add_argument("--retry", action="store_true")
    args = parser.parse_args()
    result = collect(args.context, args.config, args.raw_root, jobs=args.jobs,
                     delay_seconds=args.delay_seconds, retry=args.retry)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
