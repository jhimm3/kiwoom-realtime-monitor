"""Materialize a verified NAS news seed into a PC-only, unfinished archive DB.

This step preserves existing IDs and table-local accepted_sequence values.  It
does not process prepared news or seal a serving archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from scripts.export_historical_news_seed import TABLE_KEYS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _sqlite_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if isinstance(value, bool):
        return int(value)
    return value


def materialize(seed_path: Path, output: Path) -> dict[str, Any]:
    seed_path = seed_path.resolve(strict=True)
    output = output.resolve()
    partial = output.with_name(output.name + ".partial")
    if output.exists() or partial.exists():
        raise FileExistsError("output or partial archive already exists")
    if not output.parent.is_dir():
        raise FileNotFoundError("archive parent directory does not exist")
    seed_hash = _sha256(seed_path)
    source = sqlite3.connect(f"file:{seed_path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        if source.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("seed SQLite integrity check failed")
        manifest_row = source.execute(
            "SELECT value_json FROM seed_manifest WHERE key='report'").fetchone()
        if manifest_row is None:
            raise ValueError("seed report is missing")
        seed_report = json.loads(manifest_row[0])
        if seed_report.get("schema") != "historical-news-seed/v1":
            raise ValueError("unsupported seed schema")
        if set(seed_report.get("tables", {})) - set(TABLE_KEYS):
            raise ValueError("seed contains an unknown table")
        SQLiteQueryStore(partial).initialize()
        destination = sqlite3.connect(partial)
        try:
            destination.execute("CREATE TABLE archive_seed_roles ("
                                "table_name TEXT NOT NULL,row_key TEXT NOT NULL,"
                                "payload_hash TEXT NOT NULL,role TEXT NOT NULL,"
                                "PRIMARY KEY(table_name,row_key))")
            destination.execute("CREATE TABLE archive_build_manifest ("
                                "key TEXT PRIMARY KEY,value_json TEXT NOT NULL)")
            destination.commit()
            destination.execute("BEGIN IMMEDIATE")
            table_counts: dict[str, int] = {}
            roles: dict[str, int] = {}
            for table in TABLE_KEYS:
                columns = {str(row[1]) for row in destination.execute(f"PRAGMA table_info({table})")}
                if not columns:
                    raise ValueError(f"missing SQLite news table: {table}")
                reader = source.execute(
                    "SELECT row_key,payload_json,payload_hash,role FROM seed_rows "
                    "WHERE table_name=? ORDER BY row_key", (table,))
                count = 0
                while batch := reader.fetchmany(500):
                    for row_key, payload_json, payload_hash, role in batch:
                        if hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != payload_hash:
                            raise ValueError(f"seed row hash mismatch: {table}:{row_key}")
                        payload = json.loads(payload_json)
                        expected_key = json.dumps([payload[key] for key in TABLE_KEYS[table]],
                                                  ensure_ascii=False, separators=(",", ":"))
                        if expected_key != row_key:
                            raise ValueError(f"seed key mismatch: {table}:{row_key}")
                        if set(payload) != columns:
                            raise ValueError(f"SQLite/PG schema mismatch: {table}")
                        ordered = sorted(payload)
                        names = ",".join(ordered)
                        placeholders = ",".join("?" for _ in ordered)
                        destination.execute(
                            f"INSERT INTO {table}({names}) VALUES({placeholders})",
                            tuple(_sqlite_value(payload[name]) for name in ordered),
                        )
                        destination.execute(
                            "INSERT INTO archive_seed_roles VALUES(?,?,?,?)",
                            (table, row_key, payload_hash, role),
                        )
                        roles[role] = roles.get(role, 0) + 1
                        count += 1
                if count:
                    table_counts[table] = count
                if count != seed_report["tables"].get(table, 0):
                    raise ValueError(f"seed table count mismatch: {table}")
            if table_counts != seed_report["tables"] or roles != seed_report["roles"]:
                raise ValueError("seed manifest counts do not match materialized rows")
            report = {"build_state": "building", "seed_sha256": seed_hash,
                      "seed_snapshot_started_at": seed_report["snapshot_started_at"],
                      "seed_rows": sum(table_counts.values()), "tables": table_counts,
                      "roles": roles}
            destination.execute("INSERT INTO archive_build_manifest VALUES(?,?)",
                                ("report", json.dumps(report, ensure_ascii=False, sort_keys=True)))
            destination.commit()
            if destination.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("materialized SQLite integrity check failed")
        finally:
            destination.close()
        partial.replace(output)
        return report
    except BaseException:
        if partial.exists():
            partial.unlink()
        raise
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(materialize(args.seed, args.output), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
