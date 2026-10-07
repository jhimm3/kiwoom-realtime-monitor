"""Collect DART filings around dated events for the 2,637 stock candidates.

The candidate daily database is read only. Existing adjustment-event DART snapshots
are linked, not requested again. Zero-volume runs are *leads* for disclosure search,
not assertions that an exchange officially suspended the stock.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient
from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig


DEFAULT_CANDIDATES = Path(r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3")
DEFAULT_CONTEXT = Path("data/historical_market_context.sqlite3")
DEFAULT_CONFIG = Path("data/naver_news.dat")
DEFAULT_CACHE = Path("data/dart_corp_codes.json")
DEFAULT_RAW = Path("data/historical_collection/context/dart_candidate_events")
START = "2019-09-24"


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _event_window(kind: str, event_date: str, as_of: str) -> tuple[str, str]:
    day = date.fromisoformat(event_date)
    before, after = {
        "price_adjustment": (180, 14),
        "listing": (90, 14),
        "zero_volume_start": (30, 14),
        "zero_volume_resume": (30, 14),
        "delisting_review": (30, 30),
    }[kind]
    return ((day - timedelta(days=before)).isoformat(),
            min(day + timedelta(days=after), date.fromisoformat(as_of)).isoformat())


def candidate_events(candidate_db: Path, context_db: Path, as_of: str) -> tuple[int, list[tuple[str, str, str, str, str]]]:
    """Return (population count, event leads with source/provenance)."""
    with closing(_read_only(candidate_db)) as candidates, closing(_read_only(context_db)) as context:
        stocks = {str(code): str(reg_day or "") for code, reg_day in candidates.execute(
            "SELECT s.code,s.reg_day FROM stocks s WHERE s.market_code IN ('0','10') "
            "AND EXISTS(SELECT 1 FROM candidate_days c WHERE c.code=s.code)"
        )}
        events: set[tuple[str, str, str, str, str]] = set()
        for code, day in stocks.items():
            if START <= day <= as_of:
                events.add((code, day, "listing", "stocks.reg_day", "recorded_listing_date"))
            previous_active: bool | None = None
            zero_start = ""
            zero_count = 0
            for trade_day, volume in candidates.execute(
                "SELECT dt,volume FROM daily_bars WHERE code=? AND dt BETWEEN ? AND ? ORDER BY dt",
                (code, START, as_of),
            ):
                active = (volume or 0) > 0
                if not active:
                    if previous_active is True:
                        zero_start, zero_count = str(trade_day), 1
                    elif previous_active is False:
                        zero_count += 1
                elif previous_active is False and zero_count >= 5:
                    if zero_start:
                        events.add((code, zero_start, "zero_volume_start", "daily_bars.volume", "inferred_not_exchange_confirmed"))
                    events.add((code, str(trade_day), "zero_volume_resume", "daily_bars.volume", "inferred_not_exchange_confirmed"))
                if active:
                    zero_start, zero_count = "", 0
                previous_active = active
            if previous_active is False and zero_count >= 5 and zero_start:
                events.add((code, zero_start, "zero_volume_start", "daily_bars.volume", "inferred_not_exchange_confirmed"))
        for code, day in context.execute(
            "SELECT DISTINCT code,adjustment_date FROM historical_stock_adjustments"
        ):
            if code in stocks and START <= day <= as_of:
                events.add((str(code), str(day), "price_adjustment", "historical_stock_adjustments", "creon_adjustment_boundary"))
        # Current delisting classifications supplied by the user. The last traded
        # date is a search anchor, *not* asserted to be the legal delisting date.
        for code in ("008290", "046070", "082660"):
            if code not in stocks:
                continue
            row = candidates.execute(
                "SELECT MAX(dt) FROM daily_bars WHERE code=? AND volume>0 AND dt<=?",
                (code, as_of),
            ).fetchone()
            if row and row[0]:
                events.add((code, str(row[0]), "delisting_review", "user_status_and_daily_last_trade", "delisting_date_unverified"))
        return len(stocks), sorted(events)


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS historical_candidate_event_jobs (
            code TEXT NOT NULL, event_date TEXT NOT NULL, event_kind TEXT NOT NULL,
            event_source TEXT NOT NULL, event_quality TEXT NOT NULL,
            query_begin TEXT NOT NULL, query_end TEXT NOT NULL,
            state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            disclosure_count INTEGER NOT NULL DEFAULT 0,
            raw_file TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(code,event_date,event_kind)
        );
        CREATE TABLE IF NOT EXISTS historical_candidate_event_disclosures (
            code TEXT NOT NULL, event_date TEXT NOT NULL, event_kind TEXT NOT NULL,
            receipt_no TEXT NOT NULL, receipt_date TEXT NOT NULL,
            report_name TEXT NOT NULL, filer_name TEXT NOT NULL,
            filing_url TEXT NOT NULL, raw_file TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            PRIMARY KEY(code,event_date,event_kind,receipt_no)
        );
        CREATE INDEX IF NOT EXISTS idx_historical_candidate_event_jobs_state
        ON historical_candidate_event_jobs(state,code,event_date);
        CREATE INDEX IF NOT EXISTS idx_historical_candidate_event_disclosures_receipt
        ON historical_candidate_event_disclosures(code,receipt_date,receipt_no);
    """)


def seed(candidate_db: Path, context_db: Path, as_of: str) -> dict[str, object]:
    population, events = candidate_events(candidate_db, context_db, as_of)
    now = datetime.now(UTC).isoformat()
    with closing(sqlite3.connect(context_db, timeout=60)) as connection:
        initialize(connection)
        existing = {
            (str(code), str(day)): (str(raw_file), str(state))
            for code, day, raw_file, state in connection.execute(
                "SELECT code,adjustment_date,raw_file,state FROM adjustment_disclosure_jobs "
                "WHERE state='complete'"
            )
        }
        records = []
        for code, day, kind, source, quality in events:
            begin, end = _event_window(kind, day, as_of)
            prior = existing.get((code, day)) if kind == "price_adjustment" else None
            records.append((code, day, kind, source, quality, begin, end,
                            "reused_existing" if prior else "pending",
                            prior[0] if prior else "", now))
        with connection:
            connection.executemany(
                "INSERT OR IGNORE INTO historical_candidate_event_jobs"
                "(code,event_date,event_kind,event_source,event_quality,query_begin,query_end,"
                "state,raw_file,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", records,
            )
            connection.execute(
                "INSERT OR IGNORE INTO historical_candidate_event_disclosures "
                "(code,event_date,event_kind,receipt_no,receipt_date,report_name,filer_name,"
                "filing_url,raw_file,observed_at) "
                "SELECT d.code,d.adjustment_date,'price_adjustment',d.receipt_no,"
                "d.receipt_date,d.report_name,d.filer_name,d.filing_url,d.raw_file,d.observed_at "
                "FROM historical_stock_adjustment_disclosures d "
                "JOIN historical_candidate_event_jobs j ON j.code=d.code "
                "AND j.event_date=d.adjustment_date AND j.event_kind='price_adjustment'"
            )
        states = dict(connection.execute(
            "SELECT state,COUNT(*) FROM historical_candidate_event_jobs GROUP BY state"
        ))
    return {"candidate_stocks": population, "event_leads": len(events), "job_states": states}


def _dart_query_code(code: str, corp_codes: dict[str, str], names: dict[str, str]) -> str:
    if code in corp_codes:
        return code
    common = code[:5] + "0" if len(code) == 6 else ""
    if common not in corp_codes:
        return ""
    common_name, share_name = names.get(common, ""), names.get(code, "")
    suffix = share_name[len(common_name):] if common_name and share_name.startswith(common_name) else ""
    if suffix and re.fullmatch(r"[0-9]*우[A-Z]?", suffix):
        return common
    # Some preferred names shorten the issuer name before the 우 suffix.
    # Require the matching common-share code and a substantial exact prefix.
    if share_name.endswith("우") and len(share_name[:-1]) >= 3 \
            and common_name.startswith(share_name[:-1]):
        return common
    return ""


def collect(candidate_db: Path, context_db: Path, config: Path, cache: Path, raw_root: Path,
            *, jobs: int, delay_seconds: float, retry_failed: bool,
            retry_unmapped: bool = False, max_attempts: int = 2) -> dict[str, object]:
    settings = LocalNaverNewsConfig(config.resolve(strict=True)).load_official()
    if not settings.dart_enabled or not settings.dart_api_key:
        raise RuntimeError("DART API is not enabled in the app news settings")
    client = DartDisclosureClient(settings.dart_api_key, cache.resolve(strict=True))
    corp_codes = json.loads(cache.read_text(encoding="utf-8"))
    with closing(_read_only(candidate_db)) as candidate_connection:
        names = {str(code): str(name) for code, name in candidate_connection.execute(
            "SELECT code,name FROM stocks"
        )}
    done = empty = unmapped = failed = 0
    raw_root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(context_db, timeout=60)) as connection:
        connection.execute("PRAGMA busy_timeout=60000")
        for _ in range(max(0, jobs)):
            allowed = ["pending"]
            if retry_failed:
                allowed.append("failed")
            if retry_unmapped:
                allowed.append("unmapped")
            row = connection.execute(
                "SELECT code,event_date,event_kind,query_begin,query_end,attempts FROM "
                "historical_candidate_event_jobs WHERE state IN (" + ",".join("?" for _ in allowed) + ") "
                "AND attempts<? "
                "ORDER BY CASE WHEN event_kind='delisting_review' THEN 0 ELSE 1 END, "
                "event_date DESC,code LIMIT 1", (*allowed, max_attempts),
            ).fetchone()
            if row is None:
                break
            code, event_date, kind, begin, end, attempts = row
            key = (code, event_date, kind)
            now = datetime.now(UTC).isoformat()
            with connection:
                connection.execute(
                    "UPDATE historical_candidate_event_jobs SET state='running',attempts=?,"
                    "updated_at=? WHERE code=? AND event_date=? AND event_kind=?",
                    (attempts + 1, now, *key),
                )
            query_code = _dart_query_code(code, corp_codes, names)
            if not query_code:
                with connection:
                    connection.execute(
                        "UPDATE historical_candidate_event_jobs SET state='unmapped',updated_at=? "
                        "WHERE code=? AND event_date=? AND event_kind=?", (now, *key),
                    )
                unmapped += 1
                continue
            target = raw_root / code / f"{event_date}-{kind}.json"
            try:
                if target.exists():
                    payload = json.loads(target.read_text(encoding="utf-8"))
                    if payload.get("code") != code or payload.get("event_date") != event_date or payload.get("event_kind") != kind:
                        raise ValueError("Existing raw event identity mismatch")
                    rows = payload["disclosures"]
                else:
                    rows = list(client.list_disclosures(
                        query_code, date.fromisoformat(begin), date.fromisoformat(end)
                    ))
                    payload = {"schema": "candidate-dart-event/v1", "provider": "opendart",
                               "code": code, "event_date": event_date, "event_kind": kind,
                               "dart_query_code": query_code,
                               "query_begin": begin, "query_end": end,
                               "observed_at": datetime.now(UTC).isoformat(),
                               "disclosures": rows}
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_suffix(".json.tmp")
                    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    os.replace(temporary, target)
                observed = str(payload["observed_at"])
                disclosure_rows = []
                for item in rows:
                    receipt = str(item.get("rcept_no") or "")
                    if not receipt:
                        continue
                    raw_date = str(item.get("rcept_dt") or "")
                    receipt_date = (f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                                    if len(raw_date) == 8 else raw_date)
                    disclosure_rows.append((code, event_date, kind, receipt, receipt_date,
                                            str(item.get("report_nm") or ""),
                                            str(item.get("flr_nm") or ""),
                                            f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
                                            str(target), observed))
                with connection:
                    connection.executemany(
                        "INSERT OR IGNORE INTO historical_candidate_event_disclosures "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)", disclosure_rows,
                    )
                    connection.execute(
                        "UPDATE historical_candidate_event_jobs SET state=?,disclosure_count=?,"
                        "raw_file=?,error='',updated_at=? WHERE code=? AND event_date=? AND event_kind=?",
                        ("complete" if rows else "empty", len(rows), str(target),
                         datetime.now(UTC).isoformat(), *key),
                    )
                done += 1
                empty += not bool(rows)
                print(json.dumps({"event": "complete", "code": code,
                                  "event_date": event_date, "kind": kind,
                                  "disclosures": len(rows)}, ensure_ascii=False), flush=True)
            except Exception as error:
                detail = f"{type(error).__name__}: {error}"
                rate_limited = "020" in detail or "요청 제한" in detail
                with connection:
                    connection.execute(
                        "UPDATE historical_candidate_event_jobs SET state=?,error=?,updated_at=? "
                        "WHERE code=? AND event_date=? AND event_kind=?",
                        ("rate_limited" if rate_limited else "failed", detail[:500],
                         datetime.now(UTC).isoformat(), *key),
                    )
                failed += 1
                print(json.dumps({"event": "rate_limited" if rate_limited else "failed",
                                  "code": code, "event_date": event_date,
                                  "error": detail}, ensure_ascii=False), flush=True)
                if rate_limited:
                    break
            if delay_seconds:
                time.sleep(delay_seconds)
        states = dict(connection.execute(
            "SELECT state,COUNT(*) FROM historical_candidate_event_jobs GROUP BY state"
        ))
    return {"processed": done, "empty": empty, "unmapped": unmapped,
            "failed": failed, "job_states": states}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-database", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--context-database", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--jobs", type=int, default=100)
    parser.add_argument("--delay-seconds", type=float, default=0.3)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--retry-unmapped", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 0 or args.delay_seconds < 0 or args.max_attempts < 1:
        parser.error("jobs and delay-seconds must be non-negative; max-attempts must be positive")
    if args.status_only:
        with closing(_read_only(args.context_database)) as connection:
            states = dict(connection.execute(
                "SELECT state,COUNT(*) FROM historical_candidate_event_jobs GROUP BY state"
            ))
            disclosures = connection.execute(
                "SELECT COUNT(*) FROM historical_candidate_event_disclosures"
            ).fetchone()[0]
        print(json.dumps({"job_states": states, "linked_disclosures": disclosures},
                         ensure_ascii=False), flush=True)
        return 0
    result = seed(args.candidate_database, args.context_database, args.as_of)
    print(json.dumps({"event": "seeded", **result}, ensure_ascii=False), flush=True)
    if not args.seed_only:
        result = collect(args.candidate_database, args.context_database, args.config, args.cache, args.raw_root,
                         jobs=args.jobs, delay_seconds=args.delay_seconds,
                         retry_failed=args.retry_failed,
                         retry_unmapped=args.retry_unmapped,
                         max_attempts=args.max_attempts)
        print(json.dumps({"event": "finished", **result}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
