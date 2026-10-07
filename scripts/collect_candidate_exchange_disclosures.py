"""Archive all OpenDART exchange-category filings for candidate stocks.

This complements date-windowed event leads: brief trading suspensions need not
produce a five-day zero-volume run. Filing dates remain separate from the
effective date and from minute-level information availability.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

from collect_candidate_event_disclosures import (
    DEFAULT_CACHE, DEFAULT_CANDIDATES, DEFAULT_CONFIG, DEFAULT_CONTEXT,
    START, _dart_query_code, _read_only,
)
from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient
from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig


DEFAULT_RAW = Path("data/historical_collection/context/dart_exchange")


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS historical_candidate_exchange_jobs (
            code TEXT PRIMARY KEY, query_begin TEXT NOT NULL, query_end TEXT NOT NULL,
            dart_query_code TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, disclosure_count INTEGER NOT NULL DEFAULT 0,
            raw_file TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS historical_candidate_exchange_disclosures (
            code TEXT NOT NULL, receipt_no TEXT NOT NULL, receipt_date TEXT NOT NULL,
            report_name TEXT NOT NULL, filer_name TEXT NOT NULL,
            filing_url TEXT NOT NULL, raw_file TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            PRIMARY KEY(code,receipt_no)
        );
        CREATE INDEX IF NOT EXISTS idx_historical_candidate_exchange_jobs_state
        ON historical_candidate_exchange_jobs(state,code);
        CREATE INDEX IF NOT EXISTS idx_historical_candidate_exchange_disclosures_date
        ON historical_candidate_exchange_disclosures(code,receipt_date);
    """)


def seed(candidate_db: Path, context_db: Path, as_of: str) -> dict[str, object]:
    with closing(_read_only(candidate_db)) as candidates:
        codes = [str(row[0]) for row in candidates.execute(
            "SELECT s.code FROM stocks s WHERE s.market_code IN ('0','10') "
            "AND EXISTS(SELECT 1 FROM candidate_days c WHERE c.code=s.code) ORDER BY s.code"
        )]
    with closing(sqlite3.connect(context_db, timeout=60)) as connection:
        initialize(connection)
        now = datetime.now(UTC).isoformat()
        with connection:
            connection.executemany(
                "INSERT OR IGNORE INTO historical_candidate_exchange_jobs "
                "(code,query_begin,query_end,state,updated_at) VALUES(?,?,?,'pending',?)",
                [(code, START, as_of, now) for code in codes],
            )
        states = dict(connection.execute(
            "SELECT state,COUNT(*) FROM historical_candidate_exchange_jobs GROUP BY state"
        ))
    return {"candidate_stocks": len(codes), "job_states": states}


def collect(candidate_db: Path, context_db: Path, config: Path, cache: Path,
            raw_root: Path, *, jobs: int, delay_seconds: float,
            retry_incomplete: bool = False, max_attempts: int = 2) -> dict[str, object]:
    settings = LocalNaverNewsConfig(config.resolve(strict=True)).load_official()
    if not settings.dart_enabled or not settings.dart_api_key:
        raise RuntimeError("DART API is not enabled in the app news settings")
    client = DartDisclosureClient(settings.dart_api_key, cache.resolve(strict=True))
    corp_codes = json.loads(cache.read_text(encoding="utf-8"))
    with closing(_read_only(candidate_db)) as candidates:
        names = dict(candidates.execute("SELECT code,name FROM stocks"))
    raw_root.mkdir(parents=True, exist_ok=True)
    complete = empty = unmapped = failed = 0
    with closing(sqlite3.connect(context_db, timeout=60)) as connection:
        connection.execute("PRAGMA busy_timeout=60000")
        for _ in range(max(0, jobs)):
            allowed = ["pending"] + (["failed", "unmapped"] if retry_incomplete else [])
            row = connection.execute(
                "SELECT code,query_begin,query_end,attempts FROM historical_candidate_exchange_jobs "
                "WHERE state IN (" + ",".join("?" for _ in allowed) + ") AND attempts<? "
                "ORDER BY code LIMIT 1", (*allowed, max_attempts),
            ).fetchone()
            if row is None:
                break
            code, begin, end, attempts = row
            now = datetime.now(UTC).isoformat()
            query_code = _dart_query_code(code, corp_codes, names)
            with connection:
                connection.execute(
                    "UPDATE historical_candidate_exchange_jobs SET state='running',"
                    "attempts=?,dart_query_code=?,updated_at=? WHERE code=?",
                    (attempts + 1, query_code, now, code),
                )
            if not query_code:
                with connection:
                    connection.execute(
                        "UPDATE historical_candidate_exchange_jobs SET state='unmapped',"
                        "updated_at=? WHERE code=?", (now, code),
                    )
                unmapped += 1
                continue
            target = raw_root / f"{code}.json"
            try:
                if target.exists():
                    payload = json.loads(target.read_text(encoding="utf-8"))
                    if (payload.get("code"), payload.get("query_end")) != (code, end):
                        raise ValueError("Existing exchange snapshot identity mismatch")
                    rows = payload["disclosures"]
                else:
                    rows = list(client.list_disclosures(
                        query_code, date.fromisoformat(begin), date.fromisoformat(end),
                        disclosure_type="I",
                    ))
                    payload = {"schema": "candidate-dart-exchange/v1", "provider": "opendart",
                               "code": code, "dart_query_code": query_code,
                               "query_begin": begin, "query_end": end,
                               "disclosure_type": "I", "observed_at": datetime.now(UTC).isoformat(),
                               "disclosures": rows}
                    temporary = target.with_suffix(".json.tmp")
                    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    os.replace(temporary, target)
                observed = str(payload["observed_at"])
                filings = []
                for item in rows:
                    receipt = str(item.get("rcept_no") or "")
                    if not receipt:
                        continue
                    raw_date = str(item.get("rcept_dt") or "")
                    receipt_date = (f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                                    if len(raw_date) == 8 else raw_date)
                    filings.append((code, receipt, receipt_date,
                                    str(item.get("report_nm") or ""),
                                    str(item.get("flr_nm") or ""),
                                    f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
                                    str(target), observed))
                with connection:
                    connection.executemany(
                        "INSERT OR IGNORE INTO historical_candidate_exchange_disclosures "
                        "VALUES(?,?,?,?,?,?,?,?)", filings,
                    )
                    connection.execute(
                        "UPDATE historical_candidate_exchange_jobs SET state=?,"
                        "disclosure_count=?,raw_file=?,error='',updated_at=? WHERE code=?",
                        ("complete" if rows else "empty", len(rows), str(target),
                         datetime.now(UTC).isoformat(), code),
                    )
                complete += 1
                empty += not bool(rows)
                print(json.dumps({"event": "complete", "code": code,
                                  "disclosures": len(rows)}, ensure_ascii=False), flush=True)
            except Exception as error:
                detail = f"{type(error).__name__}: {error}"
                rate_limited = "020" in detail or "요청 제한" in detail
                with connection:
                    connection.execute(
                        "UPDATE historical_candidate_exchange_jobs SET state=?,error=?,"
                        "updated_at=? WHERE code=?",
                        ("rate_limited" if rate_limited else "failed", detail[:500],
                         datetime.now(UTC).isoformat(), code),
                    )
                failed += 1
                print(json.dumps({"event": "rate_limited" if rate_limited else "failed",
                                  "code": code, "error": detail}, ensure_ascii=False), flush=True)
                if rate_limited:
                    break
            if delay_seconds:
                time.sleep(delay_seconds)
        states = dict(connection.execute(
            "SELECT state,COUNT(*) FROM historical_candidate_exchange_jobs GROUP BY state"
        ))
    return {"processed": complete, "empty": empty,
            "unmapped": unmapped, "failed": failed, "job_states": states}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-database", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--context-database", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--jobs", type=int, default=100)
    parser.add_argument("--delay-seconds", type=float, default=0.3)
    parser.add_argument("--retry-incomplete", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args()
    if args.jobs < 0 or args.delay_seconds < 0 or args.max_attempts < 1:
        parser.error("jobs/delay must be non-negative and max-attempts positive")
    if args.status_only:
        with closing(_read_only(args.context_database)) as connection:
            states = dict(connection.execute(
                "SELECT state,COUNT(*) FROM historical_candidate_exchange_jobs GROUP BY state"
            ))
            filings = connection.execute(
                "SELECT COUNT(*) FROM historical_candidate_exchange_disclosures"
            ).fetchone()[0]
        print(json.dumps({"job_states": states, "exchange_filings": filings},
                         ensure_ascii=False), flush=True)
        return 0
    print(json.dumps({"event": "seeded", **seed(
        args.candidate_database, args.context_database, args.as_of
    )}, ensure_ascii=False), flush=True)
    if not args.seed_only:
        print(json.dumps({"event": "finished", **collect(
            args.candidate_database, args.context_database, args.config, args.cache,
            args.raw_root, jobs=args.jobs, delay_seconds=args.delay_seconds,
            retry_incomplete=args.retry_incomplete, max_attempts=args.max_attempts,
        )}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
