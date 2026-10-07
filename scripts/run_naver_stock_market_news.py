"""Resumable historical FLASH/WORLD collection with a visible progress ledger."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.infrastructure.naver_stock_market_news import SOURCES, collect_day, day_state
from scripts.import_historical_market_news_to_nas import _catalog_matcher
from scripts.preprocess_historical_news_locally import ConcurrentArticlePreparation
from scripts.historical_news_timing_log import NewsTimingLog


def write_status(path: Path, **fields: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": "naver-stock-market-news-collector/v1",
               "pid": os.getpid(), "updated_at": datetime.now(UTC).isoformat(), **fields}
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
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


def publish_snapshot(database: Path, nas_project: Path,
                     diagnostic: NewsTimingLog | None = None) -> Path:
    """Publish a consistent, immutable SQLite copy without touching NAS operational DBs."""
    snapshot_started = time.monotonic()
    root = nas_project.resolve(strict=True)
    if not (root / "AGENTS.md").is_file():
        raise ValueError("NAS project sentinel is missing")
    destination = root / "deploy" / "synology" / "server-data" / "historical-intelligence" / "market-news-v1"
    runs = destination / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    staging = runs / (".staging-" + run_id)
    final = runs / run_id
    staging.mkdir()
    target = staging / "naver_stock_market_news.sqlite3"

    def run_stage(stage: str, operation):
        started = time.monotonic()
        if diagnostic is not None:
            diagnostic.write("market_snapshot_stage", stage=stage, state="started", run_id=run_id)
        try:
            result = operation()
        except Exception as error:
            if diagnostic is not None:
                diagnostic.write("market_snapshot_stage", stage=stage, state="failed", run_id=run_id,
                                 elapsed_ms=round((time.monotonic() - started) * 1000),
                                 error=f"{type(error).__name__}: {error}"[:400])
            raise
        if diagnostic is not None:
            diagnostic.write("market_snapshot_stage", stage=stage, state="complete", run_id=run_id,
                             elapsed_ms=round((time.monotonic() - started) * 1000))
        return result

    def backup_database() -> None:
        with closing(sqlite3.connect(database, timeout=60)) as source:
            with closing(sqlite3.connect(target)) as copy:
                source.backup(copy)

    run_stage("backup_to_nas", backup_database)

    def inspect_snapshot():
        with closing(sqlite3.connect(target)) as check:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            summary = [list(row) for row in check.execute(
                "SELECT source,state,COUNT(*),SUM(articles) FROM market_news_days "
                "GROUP BY source,state ORDER BY source,state"
            )]
        return integrity, summary

    integrity, summary = run_stage("integrity_check", inspect_snapshot)
    if integrity != "ok":
        raise RuntimeError(f"NAS snapshot failed integrity_check: {integrity}")

    def publish_manifest() -> None:
        manifest = {"schema": "naver-stock-market-news-snapshot/v1", "run": run_id,
                    "published_at": datetime.now(UTC).isoformat(), "database": target.name,
                    "bytes": target.stat().st_size, "integrity_check": integrity, "summary": summary}
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(staging, final)
        pointer = destination / "latest.json"
        temporary = destination / (".latest-" + run_id + ".json")
        temporary.write_text(json.dumps({"run": run_id, "manifest": f"runs/{run_id}/manifest.json"},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, pointer)

    run_stage("publish_pointer", publish_manifest)
    if diagnostic is not None:
        diagnostic.write("market_snapshot", state="complete", run_id=run_id,
                         bytes=(final / target.name).stat().st_size,
                         elapsed_ms=round((time.monotonic() - snapshot_started) * 1000))
    return final


def should_publish_periodic_snapshot(completed_days: int, interval_days: int) -> bool:
    return interval_days > 0 and (completed_days == 1 or completed_days % interval_days == 0)


def publish_combined_status(database: Path, nas_project: Path) -> None:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "report_historical_collection_status.py"),
        "--database", str(PROJECT_ROOT / "data" / "historical_intelligence.sqlite3"),
        "--market-news-database", str(database),
        "--nas-project", str(nas_project),
        "--fast",
    ]
    result = subprocess.run(
        command, cwd=PROJECT_ROOT, check=True, capture_output=True, text=True, timeout=120,
    )
    if result.stdout.strip():
        print(result.stdout.strip(), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2019, 1, 1))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today() - timedelta(days=1))
    parser.add_argument("--database", type=Path, default=Path("data/naver_stock_market_news.sqlite3"))
    parser.add_argument("--state-file", type=Path, default=Path("data/historical_collection/market-news-state.json"))
    parser.add_argument("--stop-file", type=Path, default=Path("data/historical_collection/STOP_MARKET_NEWS"))
    parser.add_argument("--nas-project", type=Path, default=Path(r"X:\kiwoom-monitor"))
    parser.add_argument("--delay", type=float, default=0.7)
    parser.add_argument("--max-pages", type=int, default=300)
    parser.add_argument("--publish-every-days", type=int, default=30,
                        help="publish interim NAS snapshots this often; 0 publishes only at completion")
    parser.add_argument("--status-every-days", type=int, default=1)
    parser.add_argument("--prepared-output", type=Path,
                        default=Path("data/historical_collection/prepared-market-news.sqlite3"))
    parser.add_argument("--candidates", type=Path,
                        default=Path(r"C:\Users\pc-1\Desktop\kiwoom_history_backfill\data\kiwoom_history.sqlite3"))
    parser.add_argument("--prepare-workers", type=int, default=8)
    parser.add_argument("--diagnostic-log", type=Path,
                        help="Append-only JSONL request and processing timings")
    args = parser.parse_args()
    if (args.start_date > args.end_date or args.delay < 0 or args.max_pages < 1
            or args.publish_every_days < 0 or args.status_every_days < 1
            or not 1 <= args.prepare_workers <= 8):
        parser.error("invalid date range or collection limit")
    database = args.database.resolve()
    state_file = args.state_file.resolve()
    stop_file = args.stop_file.resolve()
    diagnostic_path = (args.diagnostic_log.resolve() if args.diagnostic_log else
                       state_file.parent / "logs" / "market-news-timing.jsonl")
    diagnostic = NewsTimingLog(diagnostic_path)
    nas_project = args.nas_project.resolve(strict=True)
    print(f"Market news diagnostic log: {diagnostic_path}", flush=True)
    if stop_file.exists():
        stop_file.unlink()
    completed_days = 0
    failures = 0
    last_snapshot = ""
    current = args.start_date
    def body_request(url: str, status: str, elapsed_ms: int, error: str) -> None:
        diagnostic.write("market_body_request", host=urlsplit(url).hostname or "unknown",
                         url_role="market_article", status=status,
                         elapsed_ms=elapsed_ms, error=error)

    def preparation_error(scope: str, code: str, identity: str, detail: str) -> None:
        diagnostic.write("market_preparation_error", scope=scope, stock_code=code,
                         identity=identity, error=detail)

    def day_diagnostic(event: str, fields: dict) -> None:
        if event == "market_page_request":
            fields = {**fields, "host": "stock.naver.com", "url_role": "market_page"}
        diagnostic.write(event, **fields)

    preparation = ConcurrentArticlePreparation(
        output=args.prepared_output.resolve(),
        search_database=PROJECT_ROOT / "data" / "historical_intelligence.sqlite3",
        market_database=database,
        matcher=_catalog_matcher(args.candidates.resolve(strict=True)),
        workers=args.prepare_workers,
        body_timeout_seconds=2.0,
        body_request_observer=body_request,
        error_observer=preparation_error,
        timing_observer=lambda event, fields: diagnostic.write(event, **fields),
    )
    preparation.__enter__()

    def on_page(page) -> None:
        for article in page.articles:
            if article.url:
                preparation.submit("historical_market_backfill", "GLOBAL", article.url)
        preparation.drain()

    try:
        while current <= args.end_date:
            collected_this_day = False
            for source in SOURCES:
                if stop_file.exists():
                    write_status(state_file, status="stopped", date=current.isoformat(), source=source,
                                 completed_days=completed_days, failures=failures, last_snapshot=last_snapshot)
                    return 0
                retries = 0
                while True:
                    write_status(state_file, status="running", date=current.isoformat(), source=source,
                                 completed_days=completed_days, failures=failures, last_snapshot=last_snapshot)
                    try:
                        prior = day_state(database, source, current.isoformat())
                        if not prior or prior.get("state") not in {"complete", "complete_boundary", "empty"}:
                            collected_this_day = True
                        result = collect_day(database, source, current.isoformat(),
                                             max_pages=args.max_pages, delay_seconds=args.delay,
                                             on_page=on_page, on_diagnostic=day_diagnostic)
                        preparation.drain(all_pending=True)
                        if result["state"] == "running":
                            continue  # max-pages is a batch bound, not a complete-day claim
                        print(json.dumps({"date": current.isoformat(), "source": source,
                                          "state": result["state"], "pages": result["pages"],
                                          "articles": result["articles"]}, ensure_ascii=False), flush=True)
                        break
                    except HTTPError as error:
                        retries += 1
                        diagnostic.write("market_day_http_error", source=source,
                                         target_date=current.isoformat(), http_status=error.code,
                                         attempt=retries, error=f"HTTP {error.code}")
                        if error.code not in {403, 429, 500, 502, 503, 504} or retries > 12:
                            raise
                        wait = min(300, 30 * retries)
                        diagnostic.write("market_day_retry_wait", source=source,
                                         target_date=current.isoformat(), http_status=error.code,
                                         wait_seconds=wait)
                        write_status(state_file, status="throttled", date=current.isoformat(), source=source,
                                     retry_in_seconds=wait, error=f"HTTP {error.code}",
                                     completed_days=completed_days, failures=failures, last_snapshot=last_snapshot)
                        print(f"{current} {source} HTTP {error.code}; retry in {wait}s", flush=True)
                        time.sleep(wait)
                    except Exception as error:
                        failures += 1
                        diagnostic.write("market_day_error", source=source,
                                         target_date=current.isoformat(),
                                         error=f"{type(error).__name__}: {error}"[:400])
                        print(f"{current} {source} failed: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
                        break
            if collected_this_day:
                completed_days += 1
            if collected_this_day and completed_days % args.status_every_days == 0:
                try:
                    publish_combined_status(database, nas_project)
                except Exception as error:
                    print(f"combined STATUS.md publish failed: {type(error).__name__}: {error}",
                          file=sys.stderr, flush=True)
            if collected_this_day and should_publish_periodic_snapshot(
                completed_days, args.publish_every_days
            ):
                last_snapshot = str(publish_snapshot(database, nas_project, diagnostic))
                print(f"NAS snapshot: {last_snapshot}", flush=True)
            write_status(state_file, status="running", date=current.isoformat(), source="all",
                         completed_days=completed_days, failures=failures, last_snapshot=last_snapshot)
            current += timedelta(days=1)
        last_snapshot = str(publish_snapshot(database, nas_project, diagnostic))
        write_status(state_file, status="complete" if not failures else "complete_with_errors",
                     date=args.end_date.isoformat(), source="all", completed_days=completed_days,
                     failures=failures, last_snapshot=last_snapshot)
        return 0 if not failures else 1
    except Exception as error:
        write_status(state_file, status="failed", date=current.isoformat(), source="all",
                     completed_days=completed_days, failures=failures,
                     error=f"{type(error).__name__}: {error}", last_snapshot=last_snapshot)
        raise
    finally:
        preparation.close()


if __name__ == "__main__":
    raise SystemExit(main())
