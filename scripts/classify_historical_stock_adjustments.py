"""Enrich candidate-stock CREON adjustment signals with historical DART filings."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient
from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig


DEFAULT_DATABASE = Path("data/historical_market_context.sqlite3")
DEFAULT_CONFIG = Path("data/naver_news.dat")
DEFAULT_CACHE = Path("data/dart_corp_codes.json")
DEFAULT_RAW_ROOT = Path("data/historical_collection/context/dart_adjustment")

EVENT_PATTERNS = (
    ("stock_split", re.compile(r"주식\s*분할")),
    ("reverse_split", re.compile(r"주식\s*병합")),
    ("capital_reduction", re.compile(r"감자")),
    ("bonus_issue", re.compile(r"무상\s*증자")),
    ("rights_offering", re.compile(r"유상\s*증자")),
    ("spin_off", re.compile(r"분할\s*합병|회사\s*분할")),
    ("merger", re.compile(r"합병")),
    ("dividend", re.compile(r"배당")),
)


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=60)
    connection.execute("PRAGMA busy_timeout=60000")
    return connection


def initialize(database: Path) -> int:
    now = datetime.now(UTC).isoformat()
    with closing(connect(database)) as connection:
        with connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS adjustment_disclosure_jobs (
                    code TEXT NOT NULL, adjustment_date TEXT NOT NULL,
                    adjustment_rate REAL NOT NULL, state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, raw_file TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
                    PRIMARY KEY(code,adjustment_date,adjustment_rate)
                );
                CREATE TABLE IF NOT EXISTS historical_stock_adjustment_disclosures (
                    provider TEXT NOT NULL, code TEXT NOT NULL,
                    adjustment_date TEXT NOT NULL, receipt_no TEXT NOT NULL,
                    receipt_date TEXT NOT NULL, report_name TEXT NOT NULL,
                    filer_name TEXT NOT NULL, filing_url TEXT NOT NULL,
                    raw_file TEXT NOT NULL, observed_at TEXT NOT NULL,
                    PRIMARY KEY(provider,code,adjustment_date,receipt_no)
                );
                CREATE TABLE IF NOT EXISTS historical_stock_adjustment_classifications (
                    provider TEXT NOT NULL, code TEXT NOT NULL,
                    adjustment_date TEXT NOT NULL, adjustment_rate REAL NOT NULL,
                    event_type TEXT NOT NULL, classification_status TEXT NOT NULL,
                    evidence_json TEXT NOT NULL, classified_at TEXT NOT NULL,
                    PRIMARY KEY(provider,code,adjustment_date,adjustment_rate)
                );
            """)
            before = connection.total_changes
            connection.execute(
                "DELETE FROM adjustment_disclosure_jobs WHERE NOT EXISTS ("
                "SELECT 1 FROM historical_stock_adjustments a WHERE "
                "a.code=adjustment_disclosure_jobs.code AND "
                "a.adjustment_date=adjustment_disclosure_jobs.adjustment_date AND "
                "a.adjustment_rate=adjustment_disclosure_jobs.adjustment_rate)"
            )
            connection.execute(
                "DELETE FROM historical_stock_adjustment_classifications WHERE NOT EXISTS ("
                "SELECT 1 FROM historical_stock_adjustments a WHERE "
                "a.code=historical_stock_adjustment_classifications.code AND "
                "a.adjustment_date=historical_stock_adjustment_classifications.adjustment_date AND "
                "a.adjustment_rate=historical_stock_adjustment_classifications.adjustment_rate)"
            )
            connection.execute(
                "DELETE FROM historical_stock_adjustment_disclosures WHERE NOT EXISTS ("
                "SELECT 1 FROM historical_stock_adjustments a WHERE "
                "a.code=historical_stock_adjustment_disclosures.code AND "
                "a.adjustment_date=historical_stock_adjustment_disclosures.adjustment_date)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO adjustment_disclosure_jobs"
                "(code,adjustment_date,adjustment_rate,state,updated_at) "
                "SELECT code,adjustment_date,adjustment_rate,'pending',? "
                "FROM historical_stock_adjustments",
                (now,),
            )
            return connection.total_changes - before


def classify(rows: tuple[dict[str, object], ...]) -> tuple[str, str, list[dict[str, str]]]:
    evidence: list[dict[str, str]] = []
    kinds: set[str] = set()
    for row in rows:
        title = str(row.get("report_nm") or "")
        for event_type, pattern in EVENT_PATTERNS:
            if pattern.search(title):
                kinds.add(event_type)
                evidence.append({
                    "event_type": event_type,
                    "receipt_no": str(row.get("rcept_no") or ""),
                    "receipt_date": str(row.get("rcept_dt") or ""),
                    "report_name": title,
                })
                break
    if not kinds:
        return "unknown", "unmatched", evidence
    if len(kinds) > 1:
        return "ambiguous", "manual_review", evidence
    return next(iter(kinds)), "classified", evidence


def claim(database: Path) -> tuple[str, str, float, int] | None:
    now = datetime.now(UTC).isoformat()
    with closing(connect(database)) as connection:
        with connection:
            connection.execute(
                "UPDATE adjustment_disclosure_jobs SET state='failed',error='interrupted',updated_at=? "
                "WHERE state='running'", (now,),
            )
            row = connection.execute(
                "SELECT code,adjustment_date,adjustment_rate,attempts "
                "FROM adjustment_disclosure_jobs WHERE state IN ('pending','failed') "
                "AND attempts<3 ORDER BY adjustment_date,code LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE adjustment_disclosure_jobs SET state='running',attempts=attempts+1,"
                "error='',updated_at=? WHERE code=? AND adjustment_date=? AND adjustment_rate=?",
                (now, row[0], row[1], row[2]),
            )
            return str(row[0]), str(row[1]), float(row[2]), int(row[3]) + 1


def collect_one(
    database: Path, client: DartDisclosureClient, raw_root: Path,
    code: str, adjustment_date: str, rate: float, attempt: int,
) -> int:
    event_day = date.fromisoformat(adjustment_date)
    begin, end = event_day - timedelta(days=180), event_day + timedelta(days=14)
    rows = client.list_disclosures(code, begin, end)
    observed = datetime.now(UTC).isoformat()
    target = raw_root / code / f"{adjustment_date}-{rate:g}-attempt{attempt}.ndjson"
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": "historical-dart-adjustment/v1", "provider": "opendart",
        "code": code, "adjustment_date": adjustment_date, "adjustment_rate": rate,
        "query_begin": begin.isoformat(), "query_end": end.isoformat(),
        "observed_at": observed, "disclosures": rows,
    }
    target.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    event_type, status, evidence = classify(rows)
    disclosure_rows = []
    for row in rows:
        receipt = str(row.get("rcept_no") or "")
        if not receipt:
            continue
        raw_date = str(row.get("rcept_dt") or "")
        receipt_date = (
            f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            if len(raw_date) == 8 else raw_date
        )
        disclosure_rows.append((
            "opendart", code, adjustment_date, receipt, receipt_date,
            str(row.get("report_nm") or ""), str(row.get("flr_nm") or ""),
            f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
            str(target), observed,
        ))
    with closing(connect(database)) as connection:
        with connection:
            connection.executemany(
                "INSERT OR IGNORE INTO historical_stock_adjustment_disclosures "
                "VALUES(?,?,?,?,?,?,?,?,?,?)", disclosure_rows,
            )
            connection.execute(
                "INSERT OR REPLACE INTO historical_stock_adjustment_classifications "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("opendart", code, adjustment_date, rate, event_type, status,
                 json.dumps(evidence, ensure_ascii=False), observed),
            )
            connection.execute(
                "UPDATE adjustment_disclosure_jobs SET state='complete',raw_file=?,error='',updated_at=? "
                "WHERE code=? AND adjustment_date=? AND adjustment_rate=?",
                (str(target), observed, code, adjustment_date, rate),
            )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--jobs", type=int, default=10000)
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()
    database = args.database.resolve(strict=True)
    seeded = initialize(database)
    if args.seed_only:
        print(json.dumps({"seeded": seeded, "database": str(database)}))
        return 0
    official = LocalNaverNewsConfig(args.config.resolve(strict=True)).load_official()
    if not official.dart_enabled or not official.dart_api_key:
        raise SystemExit("DART API is not enabled in the app news settings")
    client = DartDisclosureClient(official.dart_api_key, args.cache.resolve())
    finished = failed = 0
    for _ in range(max(1, args.jobs)):
        job = claim(database)
        if job is None:
            break
        code, adjustment_date, rate, attempt = job
        try:
            count = collect_one(
                database, client, args.raw_root.resolve(), code, adjustment_date, rate, attempt,
            )
            finished += 1
            print(json.dumps({"event": "complete", "code": code,
                              "adjustment_date": adjustment_date,
                              "disclosures": count}, ensure_ascii=False), flush=True)
        except Exception as error:
            failed += 1
            detail = f"{type(error).__name__}: {error}"
            with closing(connect(database)) as connection:
                with connection:
                    connection.execute(
                        "UPDATE adjustment_disclosure_jobs SET state='failed',error=?,updated_at=? "
                        "WHERE code=? AND adjustment_date=? AND adjustment_rate=?",
                        (detail[:2000], datetime.now(UTC).isoformat(), code, adjustment_date, rate),
                    )
            print(json.dumps({"event": "failed", "code": code,
                              "adjustment_date": adjustment_date,
                              "error": detail}, ensure_ascii=False), flush=True)
    print(json.dumps({"seeded": seeded, "finished": finished, "failed": failed},
                     ensure_ascii=False), flush=True)
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
