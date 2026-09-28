"""Stage a frozen PC prepared-news input in an unfinished archive.

This creates only an input/progress ledger.  It never alters imported NAS news
rows or marks the archive publishable.  A later PC finalizer must consume each
ready row and update its progress record in the same article transaction.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


SCOPE = "historical_backfill"
MANIFEST_KEY = "prepared_search_inputs"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _input_hash(row: tuple[Any, ...]) -> str:
    digest = hashlib.sha256()
    for value in row:
        encoded = str(value).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def stage(prepared_path: Path, archive_path: Path, expected_sha256: str,
          *, max_rows: int = 0) -> dict[str, Any]:
    if max_rows < 0:
        raise ValueError("max_rows must be non-negative")
    prepared_path = prepared_path.resolve(strict=True)
    archive_path = archive_path.resolve(strict=True)
    if prepared_path == archive_path:
        raise ValueError("prepared input and archive must differ")
    wal_path = prepared_path.with_name(prepared_path.name + "-wal")
    if wal_path.exists() and wal_path.stat().st_size:
        raise ValueError("prepared input has uncheckpointed WAL content; freeze it first")
    actual_sha256 = _sha256(prepared_path)
    if actual_sha256 != expected_sha256:
        raise ValueError("frozen prepared input hash differs from expected SHA-256")

    with closing(sqlite3.connect(
        f"file:{prepared_path.as_posix()}?mode=ro", uri=True
    )) as prepared:
        states = {str(state): int(count) for state, count in prepared.execute(
            "SELECT state,COUNT(*) FROM prepared_news WHERE scope=? GROUP BY state",
            (SCOPE,),
        )}
        if not states or set(states) - {"ready", "failed"}:
            raise ValueError("frozen search input has missing or unsupported states")
        if states.get("failed"):
            missing = prepared.execute(
                "SELECT COUNT(*) FROM prepared_news WHERE scope=? AND state='failed' "
                "AND TRIM(error)=''", (SCOPE,),
            ).fetchone()[0]
            if missing:
                raise ValueError("failed prepared input lacks an error reason")
        expected_rows = sum(states.values())

        with closing(sqlite3.connect(archive_path, timeout=30)) as archive:
            archive.execute("PRAGMA busy_timeout=30000")
            row = archive.execute(
                "SELECT value_json FROM archive_build_manifest WHERE key='report'"
            ).fetchone()
            if row is None or json.loads(row[0]).get("build_state") != "building":
                raise ValueError("archive is not an unfinished seed build")
            archive.execute("BEGIN IMMEDIATE")
            try:
                archive.execute(
                    "CREATE TABLE IF NOT EXISTS archive_input_progress ("
                    "scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,"
                    "input_hash TEXT NOT NULL,source_state TEXT NOT NULL,"
                    "progress_state TEXT NOT NULL,error TEXT NOT NULL,"
                    "article_revision_id TEXT,body_revision_id TEXT,"
                    "PRIMARY KEY(scope,stock_code,identity))"
                )
                archive.execute(
                    "CREATE INDEX IF NOT EXISTS archive_input_progress_state "
                    "ON archive_input_progress(scope,progress_state,stock_code,identity)"
                )
                saved = archive.execute(
                    "SELECT value_json FROM archive_build_manifest WHERE key=?",
                    (MANIFEST_KEY,),
                ).fetchone()
                if saved is None:
                    if archive.execute(
                        "SELECT 1 FROM archive_input_progress LIMIT 1"
                    ).fetchone() is not None:
                        raise ValueError("progress rows exist without an input manifest")
                    manifest: dict[str, Any] = {
                        "schema": "prepared-search-archive-input/v1",
                        "source_path": str(prepared_path),
                        "source_sha256": actual_sha256,
                        "states": states,
                        "expected_rows": expected_rows,
                        "staged_rows": 0,
                        "last_key": None,
                        "state": "building",
                    }
                    archive.execute(
                        "INSERT INTO archive_build_manifest(key,value_json) VALUES(?,?)",
                        (MANIFEST_KEY, json.dumps(manifest, sort_keys=True)),
                    )
                else:
                    manifest = json.loads(saved[0])
                    if (manifest.get("schema") != "prepared-search-archive-input/v1" or
                        manifest.get("source_path") != str(prepared_path) or
                        manifest.get("source_sha256") != actual_sha256 or
                        manifest.get("states") != states or
                        manifest.get("expected_rows") != expected_rows):
                        raise ValueError("staged input manifest does not match frozen source")
                    staged = archive.execute(
                        "SELECT COUNT(*) FROM archive_input_progress WHERE scope=?",
                        (SCOPE,),
                    ).fetchone()[0]
                    if staged != manifest["staged_rows"]:
                        raise ValueError("staged row count differs from progress manifest")
                archive.commit()
            except BaseException:
                archive.rollback()
                raise

            inserted_now = 0
            last = manifest["last_key"]
            while manifest["state"] != "complete" and (not max_rows or inserted_now < max_rows):
                take = min(500, max_rows - inserted_now) if max_rows else 500
                if last is None:
                    rows = prepared.execute(
                        "SELECT stock_code,identity,state,article_json,body_json,"
                        "rules_json,error,updated_at FROM prepared_news WHERE scope=? "
                        "ORDER BY stock_code,identity LIMIT ?", (SCOPE, take),
                    ).fetchall()
                else:
                    rows = prepared.execute(
                        "SELECT stock_code,identity,state,article_json,body_json,"
                        "rules_json,error,updated_at FROM prepared_news WHERE scope=? "
                        "AND (stock_code,identity)>(?,?) "
                        "ORDER BY stock_code,identity LIMIT ?",
                        (SCOPE, last[0], last[1], take),
                    ).fetchall()
                if not rows:
                    if manifest["staged_rows"] != expected_rows:
                        raise ValueError("prepared input ended before all rows were staged")
                    manifest["state"] = "complete"
                    archive.execute("BEGIN IMMEDIATE")
                    try:
                        archive.execute(
                            "UPDATE archive_build_manifest SET value_json=? WHERE key=?",
                            (json.dumps(manifest, sort_keys=True), MANIFEST_KEY),
                        )
                        archive.commit()
                    except BaseException:
                        archive.rollback()
                        raise
                    break
                entries = [
                    (SCOPE, str(stock), str(identity), _input_hash(tuple(row)),
                     str(state), "pending" if state == "ready" else "source_failed",
                     str(error), None, None)
                    for row in rows for stock, identity, state, _, _, _, error, _ in (row,)
                ]
                next_last = [str(rows[-1][0]), str(rows[-1][1])]
                next_staged = manifest["staged_rows"] + len(entries)
                if next_staged > expected_rows:
                    raise ValueError("staged rows exceed frozen source count")
                updated = {**manifest, "last_key": next_last,
                           "staged_rows": next_staged,
                           "state": "complete" if next_staged == expected_rows else "building"}
                archive.execute("BEGIN IMMEDIATE")
                try:
                    archive.executemany(
                        "INSERT INTO archive_input_progress VALUES(?,?,?,?,?,?,?,?,?)",
                        entries,
                    )
                    archive.execute(
                        "UPDATE archive_build_manifest SET value_json=? WHERE key=?",
                        (json.dumps(updated, sort_keys=True), MANIFEST_KEY),
                    )
                    archive.commit()
                except BaseException:
                    archive.rollback()
                    raise
                manifest = updated
                last = next_last
                inserted_now += len(entries)
            if ((wal_path.exists() and wal_path.stat().st_size) or
                _sha256(prepared_path) != actual_sha256):
                raise ValueError("prepared input changed while staging")
            return {"state": manifest["state"], "expected_rows": expected_rows,
                    "staged_rows": manifest["staged_rows"], "inserted_now": inserted_now,
                    "source_states": states, "source_sha256": actual_sha256}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--max-rows", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(stage(args.prepared, args.archive, args.expected_sha256,
                           max_rows=args.max_rows), sort_keys=True))


if __name__ == "__main__":
    main()
