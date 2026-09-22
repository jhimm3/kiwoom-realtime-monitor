from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import traceback
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.infrastructure.historical_backfill import (
    import_daishin_backfill_ndjson,
    initialize_probe_database,
    store_daishin_probe_payload,
)


DEFAULT_REFERENCE = Path("data/nas_reference_inspect_20260922/historical_reference.sqlite3")
DEFAULT_DATABASE = Path("data/historical_intelligence.sqlite3")
DEFAULT_HEARTBEAT = Path("data/historical_collection/daishin-job-heartbeat.json")
DEFAULT_STOP_FILE = Path("data/historical_collection/STOP_DAISHIN")
POWERSHELL32 = Path(r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe")
CREON_CONNECTION_ERROR = "CREON Plus is not connected in this Windows privilege context."
DATABASE_TIMEOUT_SECONDS = 60


class DaishinEnvironmentUnavailable(RuntimeError):
    """The collector host cannot currently use the logged-in CREON session."""


def _connect_database(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, timeout=DATABASE_TIMEOUT_SECONDS)


def _write_heartbeat(
    path: Path, phase: str, *, code: str = "", attempt: int = 0,
    directory: Path | None = None, error: str = "",
) -> None:
    document = {
        "schema": "daishin-job-heartbeat/v1",
        "pid": os.getpid(),
        "phase": phase,
        "code": code,
        "attempt": attempt,
        "raw_directory": str(directory or ""),
        "error": error[:2000],
        "updated_at": datetime.now(UTC).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)


def _initialize_jobs(reference: Path, database: Path) -> int:
    initialize_probe_database(database)
    with closing(sqlite3.connect(reference)) as source:
        codes = sorted({
            code for row in source.execute("SELECT DISTINCT code FROM candidate_days")
            if (code := str(row[0] or "").strip().upper())
            and re.fullmatch(r"[0-9A-Z]{6}", code)
        })
    now = datetime.now(UTC).isoformat()
    with closing(_connect_database(database)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS market_backfill_jobs (
                    code TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    one_minute_bars INTEGER NOT NULL DEFAULT 0,
                    five_minute_bars INTEGER NOT NULL DEFAULT 0,
                    one_minute_oldest TEXT NOT NULL DEFAULT '',
                    one_minute_newest TEXT NOT NULL DEFAULT '',
                    five_minute_oldest TEXT NOT NULL DEFAULT '',
                    five_minute_newest TEXT NOT NULL DEFAULT '',
                    raw_directory TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
                """
            )
            existing_job_codes = {
                str(row[0]) for row in connection.execute(
                    "SELECT code FROM market_backfill_jobs"
                )
            }
            new_codes = [code for code in codes if code not in existing_job_codes]
            before = connection.total_changes
            connection.executemany(
                "INSERT OR IGNORE INTO market_backfill_jobs(code,state,updated_at) VALUES(?,'pending',?)",
                [(code, now) for code in new_codes],
            )
            inserted = connection.total_changes - before
        if not new_codes:
            return inserted
        # Do not hold the writer transaction while scanning tens of millions of
        # bars. News collection shares this database and must remain writable.
        existing = {
            (str(row[0]), int(row[1])): (int(row[2]), str(row[3] or ""), str(row[4] or ""))
            for row in connection.execute(
                "SELECT code,interval_seconds,COUNT(*),MIN(bar_time),MAX(bar_time) "
                f"FROM market_bars WHERE provider=? AND code IN ({','.join('?' for _ in new_codes)}) "
                "AND interval_seconds IN (60,300) GROUP BY code,interval_seconds",
                ("daishin_creon", *new_codes),
            )
        }
        completed_rows = []
        for code in new_codes:
            one = existing.get((code, 60))
            five = existing.get((code, 300))
            # A partial import left by an interrupted run is not complete.
            # Legacy inference is safe only when both requested intervals
            # are already present; normal runs retain their explicit ledger state.
            if one is None or five is None:
                continue
            completed_rows.append((*one, *five, now, code))
        with connection:
            connection.executemany(
                """
                UPDATE market_backfill_jobs SET state='complete',one_minute_bars=?,
                    one_minute_oldest=?,one_minute_newest=?,five_minute_bars=?,
                    five_minute_oldest=?,five_minute_newest=?,updated_at=?
                WHERE code=? AND state='pending'
                """,
                completed_rows,
            )
    return inserted


def _claim(database: Path) -> tuple[str, int] | None:
    now = datetime.now(UTC).isoformat()
    with closing(_connect_database(database)) as connection:
        connection.row_factory = sqlite3.Row
        with connection:
            # A terminated collector may leave one owned job in running state.
            connection.execute(
                "UPDATE market_backfill_jobs SET state='failed',last_error='collector_restarted',updated_at=? "
                "WHERE state='running'", (now,),
            )
            row = connection.execute(
                "SELECT code,attempts FROM market_backfill_jobs "
                "WHERE state IN ('pending','failed') AND attempts<3 ORDER BY code LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE market_backfill_jobs SET state='running',attempts=attempts+1,last_error='',updated_at=? "
                "WHERE code=?", (now, row["code"]),
            )
            return str(row["code"]), int(row["attempts"]) + 1


def _run_backfill(
    code: str, interval: int, output: Path, *, to_date: str = "",
) -> None:
    bridge = Path(__file__).with_name("daishin_stockchart_backfill.ps1")
    command = [
        str(POWERSHELL32), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(bridge),
        "-Code", code, "-Interval", str(interval), "-OutputPath", str(output),
    ]
    if to_date:
        command.extend(["-ToDate", to_date])
    completed = subprocess.run(
        command,
        check=False, capture_output=True, text=True, encoding="utf-8",
    )
    if completed.returncode:
        detail = completed.stderr.strip()
        if output.exists():
            for line in reversed(output.read_text(encoding="utf-8-sig").splitlines()):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("record_type") == "error":
                    detail = str(record.get("error") or detail)
                    break
        if CREON_CONNECTION_ERROR in detail:
            raise DaishinEnvironmentUnavailable(detail)
        raise RuntimeError(detail or f"{interval}m backfill failed")


def _five_minute_to_date(one_minute_oldest: object) -> str:
    oldest_day = date.fromisoformat(str(one_minute_oldest)[:10])
    return (oldest_day - timedelta(days=1)).strftime("%Y%m%d")


def _recent_overlay(code: str, output: Path) -> dict[str, object]:
    bridge = Path(__file__).with_name("daishin_stockchart_probe.ps1")
    completed = subprocess.run(
        [str(POWERSHELL32), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(bridge),
         "-Code", code, "-Interval", "1", "-Count", "1000", "-Venue", "K",
         "-Session", "regular", "-Adjustment", "raw"],
        check=False, capture_output=True, text=True, encoding="utf-8",
    )
    lines = completed.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError(completed.stderr.strip() or "recent overlay returned no output")
    payload = json.loads(lines[-1].lstrip("\ufeff"))
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if completed.returncode or payload.get("error"):
        raise RuntimeError(str(payload.get("error") or completed.stderr.strip()))
    return payload


def _ranges(database: Path, code: str) -> tuple[object, ...]:
    with closing(_connect_database(database)) as connection:
        one = connection.execute(
            "SELECT COUNT(*),MIN(bar_time),MAX(bar_time) FROM market_bars WHERE code=? AND interval_seconds=60",
            (code,),
        ).fetchone()
        five = connection.execute(
            "SELECT COUNT(*),MIN(bar_time),MAX(bar_time) FROM market_bars WHERE code=? AND interval_seconds=300",
            (code,),
        ).fetchone()
    return (*one, *five)


def _finish(database: Path, code: str, state: str, raw_directory: Path, error: str = "") -> None:
    values = _ranges(database, code)
    with closing(_connect_database(database)) as connection:
        with connection:
            connection.execute(
                """
                UPDATE market_backfill_jobs SET state=?,one_minute_bars=?,one_minute_oldest=?,
                    one_minute_newest=?,five_minute_bars=?,five_minute_oldest=?,five_minute_newest=?,
                    raw_directory=?,last_error=?,updated_at=? WHERE code=?
                """,
                (state, int(values[0]), str(values[1] or ""), str(values[2] or ""),
                 int(values[3]), str(values[4] or ""), str(values[5] or ""),
                 str(raw_directory), error[:2000], datetime.now(UTC).isoformat(), code),
            )


def _defer_for_environment(database: Path, code: str, raw_directory: Path, error: str) -> None:
    """Return a claimed job without charging an attempt for a host-wide outage."""
    with closing(_connect_database(database)) as connection:
        with connection:
            connection.execute(
                """
                UPDATE market_backfill_jobs
                SET state='pending', attempts=MAX(attempts-1, 0), raw_directory=?,
                    last_error=?, updated_at=?
                WHERE code=? AND state='running'
                """,
                (str(raw_directory), error[:2000], datetime.now(UTC).isoformat(), code),
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="후보 종목의 대신 1분/이전 5분봉을 재개 가능하게 수집합니다.")
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--raw-root", type=Path, default=Path("data/historical_collection/daishin"))
    parser.add_argument("--heartbeat-file", type=Path, default=DEFAULT_HEARTBEAT)
    parser.add_argument("--stop-file", type=Path, default=DEFAULT_STOP_FILE)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()
    if args.jobs < 1 or args.jobs > 10000:
        parser.error("--jobs must be between 1 and 10000")
    reference, database = args.reference.resolve(strict=True), args.database.resolve()
    heartbeat = args.heartbeat_file.resolve()
    stop_file = args.stop_file.resolve()
    stop_file.unlink(missing_ok=True)
    _write_heartbeat(heartbeat, "initializing")
    inserted = _initialize_jobs(reference, database)
    if args.seed_only:
        print(json.dumps({"seeded": inserted, "database": str(database)}, ensure_ascii=False, indent=2))
        return 0
    raw_root = args.raw_root.resolve()
    raw_root.mkdir(parents=True, exist_ok=True)
    finished = failed = 0
    environment_error = ""
    for _ in range(args.jobs):
        if stop_file.exists():
            _write_heartbeat(heartbeat, "stopped")
            break
        job = _claim(database)
        if job is None:
            break
        code, attempt = job
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        directory = raw_root / code / f"{stamp}-attempt{attempt}"
        directory.mkdir(parents=True)
        one_path, five_path = directory / "1m.ndjson", directory / "5m.ndjson"
        recent_path = directory / "1m-recent-1000.json"
        _write_heartbeat(heartbeat, "one_minute_download", code=code, attempt=attempt, directory=directory)
        try:
            _run_backfill(code, 1, one_path)
            _write_heartbeat(heartbeat, "one_minute_import", code=code, attempt=attempt, directory=directory)
            import_daishin_backfill_ndjson(one_path, database)
            _write_heartbeat(heartbeat, "recent_overlay", code=code, attempt=attempt, directory=directory)
            overlay = _recent_overlay(code, recent_path)
            store_daishin_probe_payload(database, overlay)
            one_oldest = _ranges(database, code)[1]
            if not one_oldest:
                raise RuntimeError("one-minute response contained no bars")
            five_to_date = _five_minute_to_date(one_oldest)
            _write_heartbeat(heartbeat, "five_minute_download", code=code, attempt=attempt, directory=directory)
            _run_backfill(code, 5, five_path, to_date=five_to_date)
            _write_heartbeat(heartbeat, "five_minute_import", code=code, attempt=attempt, directory=directory)
            import_daishin_backfill_ndjson(five_path, database, before_date=str(one_oldest)[:10])
            _finish(database, code, "complete", directory)
            _write_heartbeat(heartbeat, "complete", code=code, attempt=attempt, directory=directory)
            finished += 1
            print(json.dumps({"event": "market_job_finished", "code": code,
                              "ranges": _ranges(database, code)}, ensure_ascii=False), flush=True)
        except DaishinEnvironmentUnavailable as error:
            environment_error = f"{type(error).__name__}: {error}"
            _defer_for_environment(database, code, directory, environment_error)
            _write_heartbeat(heartbeat, "collector_unavailable", code=code, attempt=attempt, directory=directory, error=environment_error)
            print(json.dumps({"event": "collector_environment_unavailable", "code": code,
                              "error": environment_error}, ensure_ascii=False), flush=True)
            break
        except Exception as error:
            detail = f"{type(error).__name__}: {error}"
            _finish(database, code, "failed", directory, detail)
            _write_heartbeat(heartbeat, "failed", code=code, attempt=attempt, directory=directory, error=detail)
            failed += 1
            print(json.dumps({"event": "market_job_failed", "code": code,
                              "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False), flush=True)
    print(json.dumps({"finished": finished, "failed": failed, "seeded": inserted,
                      "environment_error": environment_error,
                      "database": str(database)}, ensure_ascii=False, indent=2))
    if environment_error:
        return 3
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception:
        # PowerShell 5.1 can truncate redirected native stderr after its first
        # ErrorRecord. Keep the complete traceback on stdout for the run log.
        traceback.print_exc(file=sys.stdout)
        exit_code = 1
    raise SystemExit(exit_code)
