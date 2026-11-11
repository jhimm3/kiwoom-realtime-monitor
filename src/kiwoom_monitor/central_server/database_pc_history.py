"""Read-only history inventory and acknowledged source cleanup on the execution PC."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from kiwoom_monitor.domain.observation_archive import archive_json, archive_sha256
from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES
from kiwoom_monitor.domain.pc_history_archive import (
    CENTRAL_TABLES, DESKTOP_MARKET_TABLES, DESKTOP_NEWS_TABLES, JOURNAL_TABLES, MARKET_DOCUMENTS, MARKET_SNAPSHOTS,
    RESEARCH_FILE_CATALOG, RESEARCH_FILE_SOURCE, RESEARCH_TABLES,
)


@dataclass(frozen=True)
class HistorySource:
    name: str
    table: str
    condition: str = "1=1"
    parameters: tuple = ()
    file_role: str = ""


_TERMINAL = tuple(sorted(state.value for state in TERMINAL_ORDER_STATES))
_STATE_FILTER = "state IN (" + ",".join("?" for _ in _TERMINAL) + ")"
_INTENT = "central_execution_intents"
_EVENT = "central_execution_events"


def history_sources(file_tables: dict[str, set[str]] | None = None,
                    research_run_ids: dict[str, set[str]] | None = None) -> list[HistorySource]:
    sources = [HistorySource(table, table) for table in sorted(CENTRAL_TABLES - {_INTENT, _EVENT})]
    sources.extend((
        HistorySource(_INTENT, _INTENT, _STATE_FILTER, _TERMINAL),
        HistorySource(_EVENT, _EVENT, f"intent_id IN (SELECT intent_id FROM {_INTENT} WHERE {_STATE_FILTER})",
                      _TERMINAL),
    ))
    sources.extend(HistorySource(f"snapshot/{kind}", "central_dataset_snapshots", "kind=?", (kind,))
                   for kind in sorted(MARKET_SNAPSHOTS))
    sources.extend(HistorySource(f"document/{kind}", "central_documents", "collection=?", (kind,))
                   for kind in sorted(MARKET_DOCUMENTS))
    sources.extend(HistorySource(f"journal/{table}", table, file_role="journal") for table in sorted(JOURNAL_TABLES))
    sources.extend(HistorySource(f"desktop_news/{table}", table, file_role="desktop_news")
                   for table in sorted(DESKTOP_NEWS_TABLES))
    for role, tables in sorted((file_tables or {}).items()):
        owned = (research_run_ids or {}).get(role)
        if owned and "research_runs" in tables:
            sources.extend(HistorySource(f"research/{table}", table,
                           "run_id IN (SELECT run_id FROM research_runs WHERE status='completed' "
                           "AND run_id IN (SELECT value FROM json_each(?)))", (json.dumps(sorted(owned)),),
                           file_role=role) for table in sorted(RESEARCH_TABLES & tables))
        if role.startswith("desktop_market:"):
            sources.extend(HistorySource(f"desktop_market/{table}", table, file_role=role)
                           for table in sorted(DESKTOP_MARKET_TABLES & tables))
    sources.append(HistorySource(RESEARCH_FILE_SOURCE, "", file_role="artifact"))
    sources.append(HistorySource(RESEARCH_FILE_CATALOG, "", file_role="artifact"))
    return sources


def _key(data: dict, columns: tuple[str, ...]) -> str:
    return archive_json([data[column] for column in columns]).decode()


def _record(source: HistorySource, data: dict, columns: tuple[str, ...]) -> dict:
    if source.table == "central_external_bars":
        subject = f"{data['instrument']}:{data['timeframe']}"
    else:
        subject = next((str(data[key]) for key in ("subject", "code", "stock_code", "owner", "intent_id", "run_id")
                        if data.get(key) is not None), "")
    if "trading_date" in data:
        sort = str(data["trading_date"]) + ("T" + data["minute"] if data.get("minute") else "")
    else:
        sort = next((str(data[key]) for key in ("slot_order_key", "snapshot_key", "bar_time", "effective_at",
                                                "occurred_at", "decided_at", "available_at", "updated_at")
                     if data.get(key) is not None), "")
    return {"key": _key(data, columns), "subject": subject, "sort": sort, "data": data}


def _primary_key(db, table: str) -> tuple[str, ...]:
    fields = db.execute(f"PRAGMA table_info({table})").fetchall()
    return tuple(row[1] for row in sorted((row for row in fields if row[5]), key=lambda row: row[5]))


def _read_source_page(db, source: HistorySource, rowid: int, limit: int):
    columns = _primary_key(db, source.table)
    if not columns:
        return [], rowid
    cursor = db.execute(
        f"SELECT rowid AS _source_rowid,* FROM {source.table} WHERE rowid>? AND ({source.condition}) ORDER BY rowid LIMIT ?",
        (rowid, *source.parameters, limit),
    )
    names = tuple(field[0] for field in cursor.description)
    records = []
    for values in cursor:
        data = dict(zip(names, values, strict=True))
        rowid = data.pop("_source_rowid")
        if source.name in {"snapshot/ranking", "snapshot/top20_membership"} and data.get("revision_id"):
            revision = db.execute("SELECT payload_json FROM central_observation_revisions WHERE revision_id=?",
                                  (data["revision_id"],)).fetchone()
            if revision is None:
                raise ValueError("rank archive source revision is missing")
            data["resolved_payload_json"] = revision[0]
        records.append((_record(source, data, columns), rowid))
    return records, rowid


_RECEIPTS = "pc_history_archive_receipts"


def read_pc_history_file_tables(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


class SQLitePCHistoryStoreMixin:
    _pc_history_reader = None
    _pc_history_ready = False

    def prepare_pc_history_archive(self) -> None:
        with self._lock, self._connection() as db:
            db.execute(f"""CREATE TABLE IF NOT EXISTS {_RECEIPTS} (
                source TEXT NOT NULL, item_key TEXT NOT NULL, hash TEXT NOT NULL,
                target TEXT NOT NULL, batch_id TEXT NOT NULL, compacted INTEGER NOT NULL,
                subject TEXT NOT NULL, sort_key TEXT NOT NULL,
                PRIMARY KEY(source,item_key))""")
            db.execute(f"CREATE INDEX IF NOT EXISTS pc_history_archive_scope ON {_RECEIPTS}"
                       "(source,compacted,subject,sort_key)")
        self._pc_history_ready = True

    def discover_pc_history_archive(self) -> None:
        with self._connection() as db:
            self._pc_history_ready = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_RECEIPTS,)).fetchone() is not None

    def set_pc_history_reader(self, reader) -> None:
        self._pc_history_reader = reader

    def pc_history_record_verified(self, source: str, row: dict, target: str) -> bool:
        with self._connection() as db:
            receipt = db.execute(f"SELECT hash FROM {_RECEIPTS} WHERE source=? AND item_key=? AND target=?",
                                 (source, row["key"], target)).fetchone()
        return receipt == (archive_sha256(archive_json(row)),)

    def require_pc_history_target(self, target: str) -> None:
        with self._connection() as db:
            targets = {row[0] for row in db.execute(f"SELECT DISTINCT target FROM {_RECEIPTS} WHERE compacted=1")}
        if targets and targets != {target}:
            raise ValueError("the prior PC archive must remain available at its pinned target")

    def read_pc_history(self, source: str, **filters) -> list[dict]:
        if not self._pc_history_ready:
            return []
        query = f"SELECT DISTINCT target FROM {_RECEIPTS} WHERE source=? AND compacted=1"
        parameters = [source]
        for name, column, operation in (("subject", "subject", "="), ("first", "sort_key", ">="),
                                         ("last", "sort_key", "<")):
            if filters.get(name):
                query += f" AND {column}{operation}?"
                parameters.append(filters[name])
        with self._connection() as db:
            targets = {row[0] for row in db.execute(query, parameters)}
        if targets and self._pc_history_reader is None:
            raise ValueError("the PC history archive destination must be configured to read compacted records")
        return self._pc_history_reader(source, expected_targets=targets, **filters) if targets else []

    def scan_pc_history_page(self, source: HistorySource, source_paths: dict[str, Path], rowid: int,
                             *, compact_before: str, target: str, limit: int = 128):
        path = source_paths.get(source.file_role)
        if source.file_role and (path is None or not path.is_file()):
            return []
        with self._connection() as db:
            db.execute("BEGIN")
            if path is not None:
                with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source_db:
                    source_db.execute("BEGIN")
                    rows, _ = _read_source_page(source_db, source, rowid, limit)
                if ":" in source.file_role:
                    namespace = source.file_role.split(":", 1)[1]
                    for row, _ in rows:
                        row["key"] = namespace + ":" + row["key"]
            else:
                rows, _ = _read_source_page(db, source, rowid, limit)
            receipts = {}
            if rows:
                marks = ",".join("?" for _ in rows)
                receipts = {row[0]: (row[1], row[2]) for row in db.execute(
                    f"SELECT item_key,hash,compacted FROM {_RECEIPTS} WHERE source=? AND target=? AND item_key IN ({marks})",
                    (source.name, target, *(row["key"] for row, _ in rows)),
                )}
            return [
                (row, key, receipts.get(row["key"], ("", False))[0] != archive_sha256(archive_json(row))
                 or (compact_before and not receipts.get(row["key"], ("", False))[1]
                     and _can_compact(db, source.name, row["data"], compact_before)))
                for row, key in rows
            ]

    def acknowledge_pc_history(self, source: HistorySource, rows: list[dict], *, target: str, batch_id: str,
                               compact_before: str) -> int:
        removed = 0
        with self._lock, self._connection() as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            columns = _primary_key(db, source.table) if compact_before and not source.file_role else ()
            receipts = {}
            for row in rows:
                data = row["data"]
                compacted = 0
                if columns and _can_compact(db, source.name, data, compact_before):
                    original = {key: value for key, value in data.items() if key != "resolved_payload_json"}
                    conditions = " AND ".join(f"{column} IS ?" for column in original)
                    compacted = db.execute(f"DELETE FROM {source.table} WHERE {conditions}", tuple(original.values())).rowcount
                    removed += compacted
                if compacted:
                    db.execute(f"DELETE FROM {_RECEIPTS} WHERE source=? AND item_key=?", (source.name, row["key"]))
                    day = row["sort"][:8] if source.name == "snapshot/program_flow" else row["sort"][:10]
                    key = "#" + archive_json([row["subject"], day]).decode()
                    receipts[key] = (source.name, key, "", target, batch_id, 1, row["subject"], day)
                else:
                    receipts[row["key"]] = (source.name, row["key"], archive_sha256(archive_json(row)),
                                            target, batch_id, 0, row["subject"], row["sort"])
            db.executemany(f"""
                    INSERT INTO {_RECEIPTS} VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(source,item_key) DO UPDATE SET
                        hash=excluded.hash,target=excluded.target,batch_id=excluded.batch_id,
                        compacted=MAX(compacted,excluded.compacted),subject=excluded.subject,sort_key=excluded.sort_key
                    """, receipts.values())
        return removed

    def pc_history_inventory(self) -> tuple[set[str], set[str], set[str]]:
        with self._connection() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            snapshots = {row[0] for row in db.execute("SELECT DISTINCT kind FROM central_dataset_snapshots")}
            documents = {row[0] for row in db.execute("SELECT DISTINCT collection FROM central_documents")}
        return tables, snapshots, documents


def _can_compact(db, name: str, row: dict, before: str) -> bool:
    if name == "central_minute_bars":
        return row["trading_date"] < before
    if name == "central_daily_bars" and row["trading_date"] < before:
        # Historical daily coverage reads up to 5,000 bars from the native PC store.
        return db.execute(
            "SELECT 1 FROM central_daily_bars WHERE code=? AND market=? AND trading_date>? "
            "ORDER BY trading_date DESC LIMIT 1 OFFSET 4999",
            (row["code"], row["market"], row["trading_date"]),
        ).fetchone() is not None
    if name == "snapshot/program_flow":
        return row["snapshot_key"][:8].isdigit() and row["snapshot_key"][:8] < before.replace("-", "")
    if name in {"snapshot/ranking", "snapshot/top20_membership"}:
        slot = row.get("slot_order_key") or row["snapshot_key"]
        return len(slot) >= 10 and slot[4] == "-" and slot[:10] < before
    return False
