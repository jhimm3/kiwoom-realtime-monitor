"""Wait for historical context collection and publish its NAS snapshot once complete."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from collect_historical_market_context import mark_provider_rejected_codes


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "historical_market_context.sqlite3"
STATE_ROOT = ROOT / "data" / "historical_collection"
WATCH_STATE = STATE_ROOT / "context-publish-watch.json"
STOCK_DAILY_STATE = STATE_ROOT / "context-stock_daily-state.json"
ADJUSTMENT_STATE = STATE_ROOT / "stock-adjustment-state.json"
PUBLISHER = Path(__file__).with_name("publish_historical_market_context_to_nas.py")


def write_state(status: str, **details: object) -> None:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    document = {"status": status, "updated_at": datetime.now(UTC).isoformat(), **details}
    temporary = WATCH_STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(WATCH_STATE)


def inspect() -> tuple[dict[str, int], dict[str, int], int, int, int]:
    with closing(sqlite3.connect(DATABASE, timeout=60)) as connection:
        with connection:
            mark_provider_rejected_codes(connection)
        jobs = dict(connection.execute("SELECT state,COUNT(*) FROM context_jobs GROUP BY state"))
        vi_files = connection.execute("SELECT COUNT(*) FROM krx_vi_imports").fetchone()[0]
        adjustments = connection.execute(
            "SELECT COUNT(*) FROM historical_stock_adjustments"
        ).fetchone()[0] if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='historical_stock_adjustments'"
        ).fetchone() else 0
        classified = connection.execute(
            "SELECT COUNT(*) FROM historical_stock_adjustment_classifications"
        ).fetchone()[0] if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='historical_stock_adjustment_classifications'"
        ).fetchone() else 0
        disclosure_jobs = dict(connection.execute(
            "SELECT state,COUNT(*) FROM adjustment_disclosure_jobs GROUP BY state"
        ).fetchall()) if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='adjustment_disclosure_jobs'"
        ).fetchone() else {}
    return jobs, disclosure_jobs, vi_files, adjustments, classified


def main() -> int:
    deadline = datetime.now(UTC) + timedelta(hours=12)
    while datetime.now(UTC) < deadline:
        jobs, disclosure_jobs, vi_files, adjustments, classified = inspect()
        state_path = ADJUSTMENT_STATE if jobs.get("pending") or jobs.get("running") or disclosure_jobs else STOCK_DAILY_STATE
        collector = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        status = collector.get("status")
        dart_done = (
            adjustments == classified
            and not any(disclosure_jobs.get(key, 0) for key in ("pending", "running", "failed"))
        )
        if status == "running":
            updated = datetime.fromisoformat(str(collector.get("updated_at")))
            if datetime.now(UTC) - updated.astimezone(UTC) > timedelta(minutes=20):
                write_state("attention_required", jobs=jobs, disclosure_jobs=disclosure_jobs,
                            vi_files=vi_files, adjustments=adjustments, classified=classified,
                            collector_status=status, collector_error="No completed job for 20 minutes")
                return 2
        if (not jobs.get("pending") and not jobs.get("running") and not jobs.get("failed")
                and vi_files == 8 and dart_done):
            write_state("publishing", jobs=jobs, disclosure_jobs=disclosure_jobs,
                        vi_files=vi_files, adjustments=adjustments, classified=classified)
            result = subprocess.run(
                [sys.executable, str(PUBLISHER), "--database", str(DATABASE)],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
            )
            if result.returncode:
                write_state("publish_failed", error=(result.stderr or result.stdout)[-4000:],
                            exit_code=result.returncode)
                return result.returncode
            write_state("published", result=result.stdout.strip(), jobs=jobs,
                        disclosure_jobs=disclosure_jobs, vi_files=vi_files,
                        adjustments=adjustments, classified=classified)
            return 0
        if status in ("failed", "environment_unavailable"):
            write_state("attention_required", jobs=jobs, disclosure_jobs=disclosure_jobs,
                        vi_files=vi_files, adjustments=adjustments, classified=classified,
                        collector_status=status, collector_error=collector.get("error"))
            return 2
        if status == "complete" and (
            jobs.get("failed") or jobs.get("pending") or jobs.get("running") or not dart_done
        ):
            write_state("attention_required", jobs=jobs, vi_files=vi_files,
                        collector_status=status, collector_error="Jobs remain after collector exited")
            return 2
        write_state("waiting", jobs=jobs, disclosure_jobs=disclosure_jobs,
                    vi_files=vi_files, adjustments=adjustments, classified=classified,
                    collector_status=status, completed_jobs=collector.get("completed_jobs"))
        time.sleep(60)
    write_state("attention_required", error="Collection did not finish within 12 hours")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
