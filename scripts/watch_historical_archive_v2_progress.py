"""Read-only live progress watcher for the one-off historical archive v2 build."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASK = PROJECT_ROOT / "data" / "historical_collection" / "archive-v2-20261013"


def snapshot(task: Path) -> tuple[dict, tuple]:
    state_path = task / "run-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    phase = state.get("phase", "unknown")
    result = {
        "phase": phase,
        "state": state.get("state", "unknown"),
        "updated_at": state.get("at"),
        "child_pid": state.get("child_pid"),
        "free_bytes": state.get("free_bytes"),
    }

    archive_value = state.get("archive")
    archive = Path(archive_value) if archive_value else task / "archive-rowid-range.building.sqlite3"
    try:
        result["archive_bytes"] = archive.stat().st_size
    except OSError:
        result["archive_bytes"] = None

    # The event manifest is advanced in the same transaction as each batch,
    # so this reports committed work. Keep this lookup indexed and lightweight.
    if phase == "events" and archive.exists():
        try:
            uri = archive.resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=1.0) as db:
                db.execute("PRAGMA query_only=ON")
                db.execute("PRAGMA busy_timeout=1000")
                row = db.execute(
                    "SELECT value_json FROM archive_build_manifest WHERE key='events_v2'"
                ).fetchone()
            if row:
                manifest = json.loads(row[0])
                total = manifest.get("target_count")
                resolved = manifest.get("next_ordinal", 1) - 1
                result["event_state"] = manifest.get("state")
                result["event_resolved"] = resolved
                result["event_total"] = total
                result["event_remaining"] = max(0, total - resolved) if total is not None else None
                result["event_percent"] = (100.0 * resolved / total) if total else None
        except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
            result["progress_read_error"] = f"{type(exc).__name__}: {exc}"

    signature = tuple(
        result.get(key)
        for key in (
            "phase", "state", "event_state", "event_resolved", "event_total",
            "event_remaining", "progress_read_error",
        )
    )
    return result, signature


def format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    return f"{value / (1024 ** 3):,.2f} GiB"


def render(result: dict) -> str:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    parts = [f"[{stamp}] phase={result['phase']} state={result['state']}"]
    if result.get("event_total"):
        parts.append(
            "events committed={:,}/{:,} ({:.2f}%), remaining={:,}".format(
                result["event_resolved"],
                result["event_total"],
                result["event_percent"],
                result["event_remaining"],
            )
        )
    elif result["phase"] in {"projection", "audit", "seal"}:
        parts.append("phase detail=not exposed by the active builder")
    if result.get("progress_read_error"):
        parts.append(f"progress_read_error={result['progress_read_error']}")
    parts.append(f"archive={format_bytes(result.get('archive_bytes'))}")
    parts.append(f"free={format_bytes(result.get('free_bytes'))}")
    if result.get("child_pid"):
        parts.append(f"child_pid={result['child_pid']}")
    return " | ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", type=Path, default=DEFAULT_TASK)
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    parser.add_argument("--once", action="store_true", help="print one snapshot and exit")
    args = parser.parse_args()
    if args.interval_seconds < 5:
        parser.error("--interval-seconds must be at least 5")

    task = args.task_dir.resolve()
    previous = None
    try:
        while True:
            result, signature = snapshot(task)
            if signature != previous:
                print(render(result), flush=True)
                previous = signature
            if args.once or result.get("phase") in {"local-sealed", "failed", "blocked"}:
                return 0
            time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        print("Progress watcher stopped; archive builder was not touched.", flush=True)
        return 0
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Unable to read archive status: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
