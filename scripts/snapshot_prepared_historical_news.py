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
import sys
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from scripts.stage_prepared_historical_news_archive import _input_hash


SUPPORTED_SCOPES = frozenset({"historical_backfill", "historical_market_backfill"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def snapshot(source_path: Path, output_path: Path, scope: str, *,
             scope_aliases: tuple[str,...]=()) -> dict[str, Any]:
    if scope not in SUPPORTED_SCOPES:
        raise ValueError(f"unsupported prepared news scope: {scope}")
    aliases=set(scope_aliases)
    if aliases and (scope!='historical_market_backfill' or aliases!={'historical_market'}):
        raise ValueError('only the verified historical_market preparation alias is supported')
    accepted={scope}|aliases
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
            "SELECT DISTINCT scope FROM prepared_news LIMIT ?",(len(accepted)+1,))}
        if scopes - accepted:
            raise ValueError("prepared snapshot contains another scope")
        with closing(sqlite3.connect(partial, timeout=30)) as target:
            source.backup(target, pages=4096, sleep=0.05)
            target.execute("PRAGMA journal_mode=DELETE")
            target.commit()
            normalized={}
            if aliases:
                if target.execute("SELECT 1 FROM sqlite_master WHERE name='prepared_scope_origins'").fetchone():
                    raise ValueError('alias origin ledger already exists; use the original source')
                for alias in sorted(aliases):
                    if target.execute('SELECT 1 FROM prepared_news a JOIN prepared_news c ON c.scope=? AND c.stock_code=a.stock_code AND c.identity=a.identity WHERE a.scope=? LIMIT 1',(scope,alias)).fetchone():
                        raise ValueError('alias key overlaps canonical input; no row may be discarded')
                target.create_function('original_input_hash',8,lambda *values:_input_hash(values))
                target.execute('BEGIN IMMEDIATE')
                try:
                    target.execute('CREATE TABLE prepared_scope_origins(normalized_scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,original_scope TEXT NOT NULL,input_hash TEXT NOT NULL,PRIMARY KEY(normalized_scope,stock_code,identity))')
                    for alias in sorted(aliases):
                        target.execute('INSERT INTO prepared_scope_origins SELECT ?,stock_code,identity,scope,original_input_hash(stock_code,identity,state,article_json,body_json,rules_json,error,updated_at) FROM prepared_news WHERE scope=?',(scope,alias))
                        normalized[alias]=target.execute('UPDATE prepared_news SET scope=? WHERE scope=?',(scope,alias)).rowcount
                    target.commit()
                except BaseException:
                    target.rollback(); raise
    with closing(sqlite3.connect(f"file:{partial.as_posix()}?mode=ro&immutable=1",
                                 uri=True)) as frozen:
        if frozen.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("prepared news snapshot integrity check failed")
        columns = {str(row[1]) for row in frozen.execute("PRAGMA table_info(prepared_news)")}
        expected = {"scope", "stock_code", "identity", "state", "article_json",
                    "body_json", "rules_json", "error", "updated_at"}
        if columns != expected:
            raise ValueError("unexpected prepared_news schema")
        metadata=list(frozen.execute('SELECT scope,state,COUNT(*),MAX(updated_at) FROM prepared_news GROUP BY scope,state'))
        scope_counts = [(str(name), str(state), int(count)) for name,state,count,_ in metadata]
        if any(name != scope for name, _, _ in scope_counts):
            raise ValueError("prepared snapshot contains another scope")
        maximum_updated_at=max((updated for *_,updated in metadata if updated is not None),default=None)
    report: dict[str, Any] = {
        "schema": "prepared-historical-news-snapshot/v1",
        "scope": scope,
        "source_path": str(source_path),
        "snapshot_started_at_utc": started,
        "snapshot_completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows_by_state": {state: count for _, state, count in scope_counts},
        "rows_total": sum(count for _, _, count in scope_counts),
        "max_source_updated_at": maximum_updated_at,
        "normalized_scope_aliases":normalized,
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
    parser.add_argument('--scope-alias',action='append',default=[])
    args = parser.parse_args()
    print(json.dumps(snapshot(args.source, args.output, args.scope,scope_aliases=tuple(args.scope_alias)),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
