"""Freeze one active PC prepared-news SQLite input for an archive generation.

The SQLite backup API copies a consistent source snapshot while collectors may
continue writing.  This tool does not stop collectors or publish to the NAS.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


SUPPORTED_SCOPES = frozenset({"historical_backfill", "historical_market_backfill"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def snapshot(source_path: Path, output_path: Path, scope: str) -> dict[str, Any]:
    if scope not in SUPPORTED_SCOPES:
        raise ValueError(f"unsupported prepared news scope: {scope}")
    source_path = source_path.resolve(strict=True)
    output_path = output_path.resolve()
    if source_path == output_path:
        raise ValueError("source and snapshot paths must differ")
    if not output_path.parent.is_dir():
        raise FileNotFoundError("snapshot parent directory does not exist")
    partial = output_path.with_name(output_path.name + ".partial")
    manifest_path = output_path.with_name(output_path.name + ".manifest.json")
    manifest_partial = manifest_path.with_name(manifest_path.name + ".partial")
    if any(path.exists() for path in
           (output_path, partial, manifest_path, manifest_partial)):
        raise FileExistsError("snapshot, manifest, or partial file already exists")
    started = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro",
                                 uri=True, timeout=30)) as source:
        scopes = {str(row[0]) for row in source.execute(
            "SELECT DISTINCT scope FROM prepared_news LIMIT 2")}
        if scopes - {scope}:
            raise ValueError("prepared snapshot contains another scope")
        with closing(sqlite3.connect(partial, timeout=30)) as target:
            source.backup(target, pages=4096, sleep=0.05)
            target.execute("PRAGMA journal_mode=DELETE")
            target.commit()
    with closing(sqlite3.connect(f"file:{partial.as_posix()}?mode=ro&immutable=1",
                                 uri=True)) as frozen:
        if frozen.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("prepared news snapshot integrity check failed")
        columns = {str(row[1]) for row in frozen.execute("PRAGMA table_info(prepared_news)")}
        expected = {"scope", "stock_code", "identity", "state", "article_json",
                    "body_json", "rules_json", "error", "updated_at"}
        if columns != expected:
            raise ValueError("unexpected prepared_news schema")
        scope_counts = [(str(name), str(state), int(count))
                        for name, state, count in frozen.execute(
                            "SELECT scope,state,COUNT(*) FROM prepared_news "
                            "GROUP BY scope,state")]
        if any(name != scope for name, _, _ in scope_counts):
            raise ValueError("prepared snapshot contains another scope")
        maximum_updated_at = frozen.execute(
            "SELECT MAX(updated_at) FROM prepared_news").fetchone()[0]
    report: dict[str, Any] = {
        "schema": "prepared-historical-news-snapshot/v1",
        "scope": scope,
        "source_path": str(source_path),
        "snapshot_started_at_utc": started,
        "snapshot_completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows_by_state": {state: count for _, state, count in scope_counts},
        "rows_total": sum(count for _, _, count in scope_counts),
        "max_source_updated_at": maximum_updated_at,
        "size_bytes": partial.stat().st_size,
        "sha256": _sha256(partial),
    }
    manifest_partial.write_text(json.dumps(report, ensure_ascii=False,
                                           sort_keys=True, indent=2) + "\n",
                                encoding="utf-8")
    # The DB is published first; a missing companion manifest leaves an
    # unmistakably incomplete generation rather than a falsely sealed one.
    partial.replace(output_path)
    manifest_partial.replace(manifest_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scope", required=True, choices=sorted(SUPPORTED_SCOPES))
    args = parser.parse_args()
    print(json.dumps(snapshot(args.source, args.output, args.scope),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
