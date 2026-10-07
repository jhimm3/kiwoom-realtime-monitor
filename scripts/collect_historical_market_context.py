"""Collect index bars and candidate-day fundamentals without changing live NAS data."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.infrastructure.historical_backfill import candidate_non_stock_codes


DEFAULT_REFERENCE = Path("data/nas_reference_inspect_20260922/historical_reference.sqlite3")
DEFAULT_DATABASE = Path("data/historical_market_context.sqlite3")
DEFAULT_RAW_ROOT = Path("data/historical_collection/context")
DEFAULT_STOP = Path("data/historical_collection/STOP_CONTEXT")
POWERSHELL32 = Path(r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe")
INDEX_CODES = ("U001", "U201")
INDEX_KINDS = ("index_1m", "index_1m_latest", "index_5m", "index_daily")
KINDS = (*INDEX_KINDS, "stock_daily", "stock_adjustment")
PROVIDER_CODE_REJECTION = "Obj:StockChart\nFun:SetInputValue\nType:0"


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=60)
    connection.execute("PRAGMA busy_timeout=60000")
    return connection


def mark_provider_rejected_codes(connection: sqlite3.Connection) -> int:
    before = connection.total_changes
    connection.execute(
        "UPDATE context_jobs SET state='unavailable',updated_at=? "
        "WHERE state='failed' AND attempts>=3 AND instr(error,?)>0",
        (datetime.now(UTC).isoformat(), PROVIDER_CODE_REJECTION),
    )
    return connection.total_changes - before


def initialize(database: Path, reference: Path) -> int:
    database.parent.mkdir(parents=True, exist_ok=True)
    non_stock_codes = candidate_non_stock_codes(reference)
    with closing(connect(database)) as connection:
        with connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS context_jobs (
                    kind TEXT NOT NULL, code TEXT NOT NULL, state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, raw_file TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
                    PRIMARY KEY(kind,code)
                );
                CREATE TABLE IF NOT EXISTS historical_index_bars (
                    provider TEXT NOT NULL, code TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL, bar_time TEXT NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume INTEGER NOT NULL,
                    trading_value_raw INTEGER NOT NULL, raw_file TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(provider,code,interval_seconds,bar_time)
                );
                CREATE TABLE IF NOT EXISTS historical_stock_fundamentals (
                    provider TEXT NOT NULL, code TEXT NOT NULL, trade_date TEXT NOT NULL,
                    market_cap_raw INTEGER NOT NULL, float_shares INTEGER NOT NULL,
                    close REAL NOT NULL, volume INTEGER NOT NULL,
                    trading_value_raw INTEGER NOT NULL, raw_file TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY(provider,code,trade_date)
                );
                CREATE TABLE IF NOT EXISTS historical_stock_adjustments (
                    provider TEXT NOT NULL, code TEXT NOT NULL,
                    adjustment_date TEXT NOT NULL, adjustment_rate REAL NOT NULL,
                    previous_factor REAL NOT NULL, new_factor REAL NOT NULL,
                    previous_bar_date TEXT NOT NULL, source_bar_date TEXT NOT NULL,
                    raw_file TEXT NOT NULL, observed_at TEXT NOT NULL,
                    PRIMARY KEY(provider,code,adjustment_date,previous_factor,new_factor)
                );
            """)
    with closing(sqlite3.connect(f"file:{reference.resolve().as_posix()}?mode=ro", uri=True)) as source:
        codes = sorted({str(row[0]) for row in source.execute(
            "SELECT DISTINCT code FROM candidate_days WHERE code GLOB '[0-9A-Z]*'"
        ) if len(str(row[0])) == 6 and str(row[0]).upper() not in non_stock_codes})
    now = datetime.now(UTC).isoformat()
    with closing(connect(database)) as connection:
        with connection:
            before = connection.total_changes
            connection.executemany(
                "INSERT OR IGNORE INTO context_jobs(kind,code,state,updated_at) VALUES(?,?,'pending',?)",
                [(kind, code, now) for code in INDEX_CODES for kind in INDEX_KINDS]
                + [(kind, code, now) for code in codes
                   for kind in ("stock_daily", "stock_adjustment")],
            )
            connection.executemany(
                "UPDATE context_jobs SET state='excluded',updated_at=? "
                "WHERE code=? AND kind IN ('stock_daily','stock_adjustment') "
                "AND state IN ('pending','failed','unavailable','complete')",
                [(now, code) for code in sorted(non_stock_codes)],
            )
            # A provider-rejected historical ticker is a coverage gap, not a
            # transient request failure that should block every other ticker.
            mark_provider_rejected_codes(connection)
            return connection.total_changes - before


def target_dates(reference: Path, code: str) -> set[str]:
    with closing(sqlite3.connect(f"file:{reference.resolve().as_posix()}?mode=ro", uri=True)) as source:
        return {str(row[0])[:10] for row in source.execute(
            "SELECT DISTINCT dt FROM candidate_days WHERE code=?", (code,)
        )}


def reference_date_range(reference: Path) -> tuple[str, str]:
    with closing(sqlite3.connect(f"file:{reference.resolve().as_posix()}?mode=ro", uri=True)) as source:
        first, last = source.execute(
            "SELECT MIN(substr(dt,1,10)),MAX(substr(dt,1,10)) FROM candidate_days"
        ).fetchone()
    if not first or not last:
        raise ValueError("candidate_days has no date range")
    return str(first), str(last)


def claim(database: Path, kind: str) -> tuple[str, int] | None:
    now = datetime.now(UTC).isoformat()
    with closing(connect(database)) as connection:
        with connection:
            connection.execute(
                "UPDATE context_jobs SET state='failed',error='interrupted',updated_at=? "
                "WHERE kind=? AND state='running'", (now, kind),
            )
            row = connection.execute(
                "SELECT code,attempts FROM context_jobs WHERE kind=? "
                "AND state IN ('pending','failed') AND attempts<3 ORDER BY code LIMIT 1",
                (kind,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE context_jobs SET state='running',attempts=attempts+1,error='',updated_at=? "
                "WHERE kind=? AND code=?", (now, kind, row[0]),
            )
            return str(row[0]), int(row[1]) + 1


def finish(database: Path, kind: str, code: str, state: str, raw_file: Path, error: str = "") -> None:
    with closing(connect(database)) as connection:
        with connection:
            connection.execute(
                "UPDATE context_jobs SET state=?,raw_file=?,error=?,updated_at=? "
                "WHERE kind=? AND code=?",
                (state, str(raw_file), error[:2000], datetime.now(UTC).isoformat(), kind, code),
            )


def run_bridge(code: str, kind: str, output: Path, *, from_day: str, to_day: str) -> None:
    bridge = Path(__file__).with_name("daishin_market_context_backfill.ps1")
    command = [str(POWERSHELL32), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
               str(bridge), "-Code", code, "-Kind", kind, "-OutputPath", str(output),
               "-FromDate", from_day, "-ToDate", to_day]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
    if completed.returncode:
        error = completed.stderr.strip()
        if output.exists():
            for line in reversed(output.read_text(encoding="utf-8-sig").splitlines()):
                record = json.loads(line)
                if record.get("record_type") == "error":
                    error = str(record.get("error") or error)
                    break
        raise RuntimeError(error or f"CREON bridge exited {completed.returncode}")


def import_raw(database: Path, path: Path, *, kind: str, code: str,
               target: set[str] | None = None, before_date: str = "",
               on_or_after_date: str = "") -> tuple[int, str]:
    interval = {"index_1m": 60, "index_1m_latest": 60,
                "index_5m": 300, "index_daily": 86400}.get(kind)
    inserted = 0
    received_rows = 0
    page_count = 0
    summary: dict[str, object] | None = None
    page_hash = hashlib.sha256()
    adjustment_bars: list[tuple[str, float, int, str]] = []
    with closing(connect(database)) as connection, path.open(encoding="utf-8-sig") as stream:
      with connection:
        for line in stream:
            page_hash.update(line.encode("utf-8"))
            record = json.loads(line)
            if record.get("code") != code or record.get("kind") != kind:
                raise ValueError("Raw response identity mismatch")
            if record.get("record_type") == "summary":
                summary = record
                continue
            if record.get("record_type") != "page":
                raise ValueError(str(record.get("error") or "Unexpected raw record"))
            page_count += 1
            received_rows += len(record.get("bars", []))
            rows = []
            for bar in record.get("bars", []):
                if kind == "stock_daily":
                    day = str(bar["date"])
                    if target is not None and day not in target:
                        continue
                    rows.append(("daishin_creon", code, day, int(bar["market_cap"]),
                                 int(bar["shares"]), float(bar["close"]), int(bar["volume"]),
                                 int(bar["trading_value"]), str(path), str(record["observed_at"])))
                elif kind == "stock_adjustment":
                    raw_day = int(bar.get("raw_adjustment_date") or 0)
                    factor = float(bar.get("adjustment_rate") or 0)
                    if raw_day <= 0 or factor <= 0:
                        continue
                    adjustment_bars.append((
                        str(bar["date"]), factor, raw_day, str(record["observed_at"])
                    ))
                else:
                    moment = str(bar["bar_time"])
                    if before_date and moment[:10] >= before_date:
                        continue
                    rows.append(("daishin_creon", code, interval, moment,
                                 float(bar["open"]), float(bar["high"]), float(bar["low"]),
                                 float(bar["close"]), int(bar["volume"]),
                                 int(bar["trading_value"]), str(path), str(record["observed_at"])))
            before = connection.total_changes
            if kind == "stock_daily":
                connection.executemany(
                    "INSERT OR IGNORE INTO historical_stock_fundamentals VALUES(?,?,?,?,?,?,?,?,?,?)", rows
                )
            elif kind == "stock_adjustment":
                pass
            else:
                connection.executemany(
                    "INSERT OR IGNORE INTO historical_index_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows
                )
            inserted += connection.total_changes - before
        if summary is None or summary.get("provider_has_more") or not summary.get("total_bars"):
            raise ValueError("Incomplete or empty CREON continuation")
        if int(summary["total_bars"]) != received_rows:
            raise ValueError("CREON page row count does not match summary")
        if "pages" in summary and int(summary["pages"]) != page_count:
            raise ValueError("CREON page count does not match summary")
        if kind == "stock_adjustment":
            events = []
            ordered = sorted(adjustment_bars, key=lambda item: item[0])
            for previous, current in zip(ordered, ordered[1:]):
                previous_day, previous_factor, _previous_raw_day, _ = previous
                current_day, new_factor, _current_raw_day, observed_at = current
                if on_or_after_date and current_day < on_or_after_date:
                    continue
                if math.isclose(previous_factor, new_factor, rel_tol=0, abs_tol=1e-4):
                    continue
                rate = new_factor / previous_factor * 100.0
                events.append((
                    "daishin_creon", code, current_day, rate,
                    previous_factor, new_factor, previous_day, current_day,
                    str(path), observed_at,
                ))
            before = connection.total_changes
            connection.executemany(
                "INSERT OR IGNORE INTO historical_stock_adjustments VALUES(?,?,?,?,?,?,?,?,?,?)",
                events,
            )
            inserted += connection.total_changes - before
    return inserted, page_hash.hexdigest()


def oldest_index_day(database: Path, code: str) -> str:
    with closing(connect(database)) as connection:
        row = connection.execute(
            "SELECT MIN(bar_time) FROM historical_index_bars WHERE provider='daishin_creon' "
            "AND code=? AND interval_seconds=60", (code,),
        ).fetchone()
    return str(row[0] or "")[:10]


def collect(database: Path, reference: Path, raw_root: Path, kind: str, jobs: int,
            stop_file: Path) -> dict[str, object]:
    finished = failed = 0
    for _ in range(jobs):
        if stop_file.exists():
            break
        claimed = claim(database, kind)
        if claimed is None:
            break
        code, attempt = claimed
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output = raw_root / kind / code / f"{stamp}-attempt{attempt}.ndjson"
        output.parent.mkdir(parents=True, exist_ok=True)
        dates = target_dates(reference, code) if kind == "stock_daily" else None
        if kind == "stock_adjustment":
            first, last = reference_date_range(reference)
            from_day = (date.fromisoformat(first) - timedelta(days=31)).strftime("%Y%m%d")
            to_day = last.replace("-", "")
        else:
            from_day = min(dates).replace("-", "") if dates else "19000101"
            to_day = max(dates).replace("-", "") if dates else datetime.now().strftime("%Y%m%d")
        before = ""
        if kind == "index_5m":
            before = oldest_index_day(database, code)
            if not before:
                finish(database, kind, code, "pending", output, "index_1m must complete first")
                break
            to_day = (date.fromisoformat(before) - timedelta(days=1)).strftime("%Y%m%d")
        try:
            run_bridge(code, kind, output, from_day=from_day, to_day=to_day)
            count, digest = import_raw(database, output, kind=kind, code=code,
                                       target=dates, before_date=before,
                                       on_or_after_date=first if kind == "stock_adjustment" else "")
            finish(database, kind, code, "complete", output)
            finished += 1
            print(json.dumps({"event": "complete", "kind": kind, "code": code,
                              "inserted": count, "raw_sha256": digest}, ensure_ascii=False), flush=True)
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            if "CREON Plus is not connected" in detail:
                with closing(connect(database)) as connection:
                    with connection:
                        connection.execute(
                            "UPDATE context_jobs SET state='pending',attempts=MAX(0,attempts-1),"
                            "error=?,updated_at=? WHERE kind=? AND code=?",
                            (detail, datetime.now(UTC).isoformat(), kind, code),
                        )
                print(json.dumps({"event": "environment_unavailable", "error": detail}), flush=True)
                break
            if PROVIDER_CODE_REJECTION in detail:
                finish(database, kind, code, "unavailable", output, detail)
                print(json.dumps({"event": "provider_code_unavailable", "kind": kind,
                                  "code": code, "error": detail}, ensure_ascii=False), flush=True)
                continue
            finish(database, kind, code, "failed", output, detail)
            failed += 1
            print(json.dumps({"event": "failed", "kind": kind, "code": code,
                              "error": detail}, ensure_ascii=False), flush=True)
    return {"kind": kind, "finished": finished, "failed": failed, "database": str(database)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=KINDS)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--seed-only", action="store_true")
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--stop-file", type=Path, default=DEFAULT_STOP)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 10000:
        parser.error("--jobs must be between 1 and 10000")
    reference = args.reference.resolve(strict=True)
    database = args.database.resolve()
    seeded = initialize(database, reference)
    if args.seed_only:
        print(json.dumps({"seeded": seeded, "database": str(database)}))
        return 0
    result = collect(database, reference, args.raw_root.resolve(), args.kind,
                     args.jobs, args.stop_file.resolve())
    result["seeded"] = seeded
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 2 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
