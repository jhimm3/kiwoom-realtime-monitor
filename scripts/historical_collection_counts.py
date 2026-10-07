"""Durable monitor counts maintained in the collector's existing transactions.

Initialization scans each source once in a WAL read snapshot. Concurrent writes
are recorded by triggers; the final correction preserves those deltas.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Callable

TABLES = {"news_backfill_jobs", "news_article_pipeline", "market_news_days", "prepared_news"}


@contextmanager
def _initialization_lock(path: Path):
    # OS lock is released on process exit, including an interrupted bootstrap.
    with path.with_name(path.name + ".monitor-counts.lock").open("a+b") as lock:
        lock.seek(0)
        lock.write(b"0")
        lock.flush()
        lock.seek(0)
        if __import__("os").name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _body(alias: str) -> str:
    return (f"CASE WHEN {alias}.state='ready' AND json_valid({alias}.body_json) "
            f"THEN COALESCE(CAST(json_extract({alias}.body_json,'$.body_status') AS TEXT),'') "
            "ELSE '' END")


def _install(connection: sqlite3.Connection, table: str) -> None:
    connection.execute("CREATE TABLE IF NOT EXISTS monitor_count_meta "
                       "(source TEXT PRIMARY KEY, ready INTEGER NOT NULL, updated_at)")
    connection.execute("CREATE TABLE IF NOT EXISTS monitor_counts "
                       "(source TEXT NOT NULL, dimension TEXT NOT NULL, bucket TEXT NOT NULL, "
                       "n INTEGER NOT NULL, PRIMARY KEY(source,dimension,bucket)) WITHOUT ROWID")
    connection.execute("INSERT INTO monitor_count_meta VALUES(?,0,NULL) "
                       "ON CONFLICT(source) DO NOTHING", (table,))
    dimensions = [("state", "COALESCE(NEW.state,'')", "COALESCE(OLD.state,'')")]
    if table == "prepared_news":
        dimensions.append(("body", _body("NEW"), _body("OLD")))
    for event in ("INSERT", "DELETE", "UPDATE"):
        statements = []
        for dimension, new, old in dimensions:
            pairs = [(new, 1)] if event == "INSERT" else [(old, -1)]
            if event == "UPDATE":
                pairs.append((new, 1))
            for expression, delta in pairs:
                condition = f" WHERE {new} IS NOT {old}" if event == "UPDATE" else " WHERE 1"
                statements.append(
                    f"INSERT INTO monitor_counts SELECT '{table}','{dimension}',{expression},{delta}"
                    f"{condition} ON CONFLICT(source,dimension,bucket) DO UPDATE SET n=n+excluded.n;")
        if event != "DELETE":
            statements.append(f"UPDATE monitor_count_meta SET updated_at="
                              f"CASE WHEN updated_at IS NULL OR NEW.updated_at>updated_at "
                              f"THEN NEW.updated_at ELSE updated_at END WHERE source='{table}';")
        connection.execute(f"CREATE TRIGGER IF NOT EXISTS monitor_count_{table}_{event.lower()} "
                           f"AFTER {event} ON {table} BEGIN {' '.join(statements)} END")


def initialize_counts(path: Path, tables: list[str], *,
                      snapshot_hook: Callable[[], None] | None = None) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if not tables or any(table not in TABLES for table in tables):
        raise ValueError("unsupported count source")
    with _initialization_lock(path), closing(sqlite3.connect(path, timeout=5)) as writer:
        if (writer.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal"
                and set(tables) != {"market_news_days"}):
            raise ValueError("initialization requires existing WAL mode; database mode is not changed")
        result = {}
        for table in dict.fromkeys(tables):
            if not writer.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                raise ValueError(f"missing source table: {table}")
            with writer:
                _install(writer, table)
            if writer.execute("SELECT ready FROM monitor_count_meta WHERE source=?", (table,)).fetchone()[0]:
                result[table] = "already_initialized"
                continue
            with closing(sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=5)) as reader:
                reader.execute("BEGIN")
                before = {(d, b): n for d, b, n in reader.execute(
                    "SELECT dimension,bucket,n FROM monitor_counts WHERE source=?", (table,))}
                if snapshot_hook:
                    snapshot_hook()
                # One source scan, including the body dimension and timestamp.
                body = _body("source") if table == "prepared_news" else "''"
                baseline: dict[tuple[str, str], int] = {}
                latest = None
                for state, status, count, updated in reader.execute(
                    f"SELECT COALESCE(state,''),{body},COUNT(*),MAX(updated_at) "
                    f"FROM {table} AS source GROUP BY 1,2"):
                    baseline[("state", state)] = baseline.get(("state", state), 0) + count
                    if table == "prepared_news":
                        baseline[("body", status)] = baseline.get(("body", status), 0) + count
                    if updated is not None and (latest is None or updated > latest):
                        latest = updated
            # Add baseline - snapshot delta to the current delta; never reset it.
            with writer:
                for dimension, bucket in baseline.keys() | before.keys():
                    correction = baseline.get((dimension, bucket), 0) - before.get((dimension, bucket), 0)
                    writer.execute("INSERT INTO monitor_counts VALUES(?,?,?,?) "
                                   "ON CONFLICT(source,dimension,bucket) DO UPDATE SET n=n+excluded.n",
                                   (table, dimension, bucket, correction))
                writer.execute("UPDATE monitor_count_meta SET ready=1, updated_at="
                               "CASE WHEN updated_at IS NULL OR ? > updated_at THEN ? ELSE updated_at END "
                               "WHERE source=?", (latest, latest, table))
            result[table] = "initialized"
        return result


def read_counts(connection: sqlite3.Connection, table: str) -> dict[str, object]:
    # No full-table fallback: an interrupted bootstrap must not display false zeros.
    meta = connection.execute("SELECT ready,updated_at FROM monitor_count_meta WHERE source=?", (table,)).fetchone()
    if meta is None or not meta[0]:
        raise sqlite3.OperationalError(f"incremental counts not initialized: {table}")
    rows = connection.execute("SELECT dimension,bucket,n FROM monitor_counts WHERE source=? AND n<>0", (table,)).fetchall()
    return {"counts": {b: n for d, b, n in rows if d == "state"},
            "body_counts": {b: n for d, b, n in rows if d == "body" and b},
            "updated_at": meta[1]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize durable incremental collector counts once")
    parser.add_argument("database", type=Path)
    parser.add_argument("tables", nargs="+", choices=sorted(TABLES))
    args = parser.parse_args()
    print(json.dumps(initialize_counts(args.database, args.tables), ensure_ascii=False))


if __name__ == "__main__":
    main()
