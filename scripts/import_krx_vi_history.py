"""Import user-downloaded KRX VI CSVs, keeping immutable source copies."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path


DEFAULT_DATABASE = Path("data/historical_market_context.sqlite3")
DEFAULT_RAW_ROOT = Path("data/historical_collection/krx_vi")
KST = timezone(timedelta(hours=9))
HEADERS = (
    "번호", "발동일", "종목코드", "종목명", "시장구분", "발동시각", "해제시각",
    "발동가격_가격", "정적_참조가격", "정적_발동가격_괴리율",
    "동적_참조가격", "동적_발동가격_괴리율", "유형",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS historical_vi_events (
            event_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
            trade_date TEXT NOT NULL, code TEXT NOT NULL, stock_name TEXT NOT NULL,
            market TEXT NOT NULL, triggered_at TEXT NOT NULL, released_at TEXT NOT NULL,
            trigger_price INTEGER NOT NULL, static_reference_price INTEGER NOT NULL,
            static_deviation_pct REAL NOT NULL, dynamic_reference_price INTEGER NOT NULL,
            dynamic_deviation_pct REAL NOT NULL, vi_type TEXT NOT NULL,
            source_sha256 TEXT NOT NULL, source_file TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_historical_vi_events_code_date
            ON historical_vi_events(code,trade_date,triggered_at);
        CREATE TABLE IF NOT EXISTS krx_vi_imports (
            source_sha256 TEXT PRIMARY KEY, original_name TEXT NOT NULL,
            source_file TEXT NOT NULL, first_date TEXT NOT NULL, last_date TEXT NOT NULL,
            source_rows INTEGER NOT NULL, inserted_rows INTEGER NOT NULL,
            imported_at TEXT NOT NULL
        );
    """)


def parse_row(row: dict[str, str], source_sha: str, source_file: Path) -> tuple:
    code = row["종목코드"].strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{6}", code):
        raise ValueError(f"Invalid KRX code: {code!r}")
    day = datetime.strptime(row["발동일"], "%Y/%m/%d").date().isoformat()
    trigger = datetime.strptime(f"{day} {row['발동시각']}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    release = datetime.strptime(f"{day} {row['해제시각']}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    if release < trigger:
        release += timedelta(days=1)
    numeric = (
        int(row["발동가격_가격"].replace(",", "")),
        int(row["정적_참조가격"].replace(",", "")),
        float(row["정적_발동가격_괴리율"].replace(",", "")),
        int(row["동적_참조가격"].replace(",", "")),
        float(row["동적_발동가격_괴리율"].replace(",", "")),
    )
    vi_type = row["유형"].strip()
    market = row["시장구분"].strip()
    identity = (day, code, market, trigger.isoformat(), release.isoformat(), *numeric, vi_type)
    event_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return (event_id, "krx_data_marketplace", day, code, row["종목명"].strip(), market,
            trigger.isoformat(), release.isoformat(), *numeric, vi_type, source_sha, str(source_file))


def import_file(connection: sqlite3.Connection, source: Path, raw_root: Path) -> dict[str, object]:
    source = source.resolve(strict=True)
    digest = sha256(source)
    existing = connection.execute(
        "SELECT source_rows,inserted_rows FROM krx_vi_imports WHERE source_sha256=?", (digest,)
    ).fetchone()
    if existing:
        return {"file": source.name, "status": "already_imported", "rows": existing[0],
                "inserted": existing[1]}
    raw_root.mkdir(parents=True, exist_ok=True)
    raw_file = raw_root / f"{digest}.csv"
    if raw_file.exists():
        if sha256(raw_file) != digest:
            raise ValueError(f"Raw copy hash mismatch: {raw_file}")
    else:
        temp = raw_root / f".{digest}.tmp"
        shutil.copy2(source, temp)
        if sha256(temp) != digest:
            temp.unlink()
            raise ValueError(f"Raw source changed during copy: {source}")
        temp.replace(raw_file)
    source_rows = inserted = 0
    first_date = last_date = ""
    with raw_file.open(encoding="cp949", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != HEADERS:
            raise ValueError(f"Unexpected KRX VI headers: {source.name}")
        batch: list[tuple] = []
        for row in reader:
            parsed = parse_row(row, digest, raw_file)
            source_rows += 1
            day = parsed[2]
            first_date = min(first_date, day) if first_date else day
            last_date = max(last_date, day)
            batch.append(parsed)
            if len(batch) >= 500:
                with connection:
                    before = connection.total_changes
                    connection.executemany(
                        "INSERT OR IGNORE INTO historical_vi_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch
                    )
                    inserted += connection.total_changes - before
                batch.clear()
        if batch:
            with connection:
                before = connection.total_changes
                connection.executemany(
                    "INSERT OR IGNORE INTO historical_vi_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch
                )
                inserted += connection.total_changes - before
    if not source_rows:
        raise ValueError(f"KRX VI source is empty: {source}")
    with connection:
        connection.execute(
            "INSERT INTO krx_vi_imports VALUES(?,?,?,?,?,?,?,?)",
            (digest, source.name, str(raw_file), first_date, last_date, source_rows,
             inserted, datetime.now(timezone.utc).isoformat()),
        )
    return {"file": source.name, "status": "imported", "first_date": first_date,
            "last_date": last_date, "rows": source_rows, "inserted": inserted,
            "sha256": digest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    args = parser.parse_args()
    database = args.database.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database, timeout=60)) as connection:
        connection.execute("PRAGMA busy_timeout=60000")
        initialize(connection)
        for file in args.files:
            print(json.dumps(import_file(connection, file, args.raw_root.resolve()),
                             ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
