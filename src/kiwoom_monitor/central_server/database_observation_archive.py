"""Opt-in archive storage; archive commands never touch the live observation tables."""
from __future__ import annotations

import json
from typing import Any

from kiwoom_monitor.domain.observation_archive import (
    ObservationArchiveBatch, ObservationArchiveConflict, POSTGRES_ARCHIVE_DURABILITY,
    SQLITE_ARCHIVE_DURABILITY, archive_batch_id, archive_json, archive_sha256,
)
from kiwoom_monitor.domain.time_basis import as_kst
from kiwoom_monitor.domain.pc_history_archive import PC_HISTORY_ARCHIVE_KIND, RESEARCH_FILE_SOURCE

from .postgres_access import DBWriterContext, owned_connection_scope


_TABLE = "central_observation_archive_batches"
_HISTORY = "central_pc_archive_history"


def _history_schema() -> tuple[str, str]:
    return (
        f"CREATE TABLE IF NOT EXISTS {_HISTORY} ("
        "source_stream TEXT NOT NULL,source TEXT NOT NULL,item_key TEXT NOT NULL,subject TEXT NOT NULL,"
        "sort_key TEXT NOT NULL,record_json TEXT NOT NULL,batch_id TEXT NOT NULL,"
        "PRIMARY KEY(source_stream,source,item_key))",
        f"CREATE INDEX IF NOT EXISTS idx_pc_archive_history_read ON {_HISTORY}"
        "(source_stream,source,subject,sort_key DESC,item_key)",
    )


def _index_pc_history(cursor, placeholder: str, value: ObservationArchiveBatch) -> None:
    document = value.document()
    source = json.loads(document["manifest"])["subject"]
    records = [json.loads(line) for line in document["observations"].split("\n") if line]
    cursor.executemany(
        f"INSERT INTO {_HISTORY} VALUES({','.join((placeholder,) * 7)}) "
        "ON CONFLICT(source_stream,source,item_key) DO UPDATE SET subject=excluded.subject,"
        "sort_key=excluded.sort_key,record_json=excluded.record_json,batch_id=excluded.batch_id",
        [(value.source_stream, source, row["key"], row["subject"], row["sort"],
          "" if source == RESEARCH_FILE_SOURCE else archive_json(row).decode(), value.batch_id) for row in records],
    )


def _read_pc_history(cursor, p: str, source_stream: str, source: str, subject: str,
                     first: str, last: str, limit: int, batch_id: str = "") -> list[dict]:
    sql = f"SELECT record_json,batch_id,item_key FROM {_HISTORY} WHERE source_stream={p} AND source={p}"
    values: list = [source_stream, source]
    for column, operator, value in (("subject", "=", subject), ("sort_key", ">=", first),
                                    ("sort_key", "<", last), ("batch_id", "=", batch_id)):
        if value:
            sql += f" AND {column}{operator}{p}"
            values.append(value)
    cursor.execute(sql + f" ORDER BY sort_key DESC,item_key LIMIT {p}", (*values, limit))
    rows = cursor.fetchall()
    if source != RESEARCH_FILE_SOURCE:
        return [{"record": json.loads(row[0]), "batch_id": row[1]} for row in rows]
    restored = {}
    for _, identity, _ in rows:
        if identity not in restored:
            stored = _load_batch(cursor, p, source_stream, identity, True)
            restored[identity] = {row["key"]: row for row in map(
                json.loads, stored["batch"]["observations"].splitlines())}
    return [{"record": restored[identity][key], "batch_id": identity} for _, identity, key in rows]


def _archive_schema(*, postgres: bool) -> str:
    binary = "BYTEA" if postgres else "BLOB"
    sequence = "BIGINT" if postgres else "INTEGER"
    return (
        f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
        f"source_stream TEXT NOT NULL,batch_id TEXT NOT NULL,receipt_sequence {sequence} NOT NULL,"
        f"content_sha256 TEXT NOT NULL,receipt_json TEXT NOT NULL,batch_body {binary} NOT NULL,"
        "PRIMARY KEY(source_stream,batch_id),UNIQUE(source_stream,receipt_sequence))"
    )


def _accept_batch(cursor, placeholder, value, now_provider, durability) -> dict[str, Any]:
    cursor.execute(
        f"SELECT content_sha256,receipt_json FROM {_TABLE} "
        f"WHERE source_stream={placeholder} AND batch_id={placeholder}",
        (value.source_stream, value.batch_id),
    )
    stored = cursor.fetchone()
    if stored is not None:
        if stored[0] != value.content_sha256:
            raise ObservationArchiveConflict("immutable observation archive batch conflict")
        try:
            return value.verify_receipt(json.loads(stored[1]))
        except ValueError as error:
            raise RuntimeError("stored observation archive receipt is invalid") from error
    cursor.execute(
        f"SELECT receipt_sequence FROM {_TABLE} WHERE source_stream={placeholder} "
        "ORDER BY receipt_sequence DESC LIMIT 1", (value.source_stream,),
    )
    last = cursor.fetchone()
    receipt = {
        **value.identity(), "status": "stored", "storage_only": True,
        "receipt_sequence": 1 if last is None else int(last[0]) + 1,
        "accepted_at": as_kst(now_provider()).isoformat(), "durability": durability,
    }
    cursor.execute(
        f"INSERT INTO {_TABLE}(source_stream,batch_id,receipt_sequence,content_sha256,receipt_json,batch_body) "
        f"VALUES({','.join((placeholder,) * 6)})",
        (value.source_stream, value.batch_id, receipt["receipt_sequence"], value.content_sha256,
         archive_json(receipt).decode("utf-8"), value.raw),
    )
    if value.kind == PC_HISTORY_ARCHIVE_KIND:
        _index_pc_history(cursor, placeholder, value)
    return receipt


def _load_batch(cursor, placeholder, source_stream, batch_id, include_body) -> dict[str, Any] | None:
    columns = "receipt_json,batch_body" if include_body else "receipt_json"
    cursor.execute(
        f"SELECT {columns} FROM {_TABLE} WHERE source_stream={placeholder} AND batch_id={placeholder}",
        (source_stream, archive_batch_id(batch_id)),
    )
    stored = cursor.fetchone()
    if stored is None:
        return None
    receipt = json.loads(stored[0])
    if not include_body:
        return receipt
    raw = bytes(stored[1])
    if archive_sha256(raw) != receipt["content_sha256"]:
        raise RuntimeError("stored observation archive bytes do not match their first receipt")
    return {"receipt": receipt, "batch": json.loads(raw)}


def _authenticated_batch(source_stream: str, raw: bytes, expected_sha256: str) -> ObservationArchiveBatch:
    value = ObservationArchiveBatch.parse(raw, expected_sha256=expected_sha256)
    if value.source_stream != source_stream:
        raise ObservationArchiveConflict("observation archive batch does not match the authorized source stream")
    return value


def _begin_sqlite_archive(connection) -> None:
    connection.execute("PRAGMA synchronous=FULL")
    if (connection.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
            or connection.execute("PRAGMA synchronous").fetchone()[0] != 2):
        raise RuntimeError("observation archive requires file-backed SQLite WAL and synchronous FULL")
    connection.execute("BEGIN IMMEDIATE")


def _begin_postgres_archive(cursor, lock_scope: str) -> None:
    cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
    cursor.execute("SET LOCAL synchronous_commit TO ON")
    cursor.execute("SHOW fsync")
    if cursor.fetchone()[0] != "on":
        raise RuntimeError("observation archive requires PostgreSQL fsync ON")
    cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("observation-archive/v1:" + lock_scope,))


class SQLiteObservationArchiveStoreMixin:
    def prepare_observation_archive(self) -> None:
        if self._memory_uri or not self._path.is_file():
            raise ValueError("initialize a durable SQLite receiver before enabling observation archive")
        with self._lock, self._connection() as connection:
            _begin_sqlite_archive(connection)
            connection.execute(_archive_schema(postgres=False))
            for statement in _history_schema():
                connection.execute(statement)

    def load_pc_archive_history(self, source_stream: str, source: str, subject: str,
                                first: str, last: str, limit: int, batch_id: str = "") -> list[dict]:
        with self._lock, self._connection() as connection:
            return _read_pc_history(connection.cursor(), "?", source_stream, source, subject, first, last, limit, batch_id)

    def accept_observation_archive(self, source_stream: str, raw: bytes, expected_sha256: str) -> dict[str, Any]:
        value = _authenticated_batch(source_stream, raw, expected_sha256)
        with self._lock, self._connection() as connection:
            _begin_sqlite_archive(connection)
            receipt = _accept_batch(connection.cursor(), "?", value, self._now_provider, SQLITE_ARCHIVE_DURABILITY)
        return receipt

    def load_observation_archive(
        self, source_stream: str, batch_id: str, *, include_body: bool = False,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            return _load_batch(connection.cursor(), "?", source_stream, batch_id, include_body)


class PostgresObservationArchiveStoreMixin:
    def prepare_observation_archive(self) -> None:
        writer = DBWriterContext(writer_family="archive.observation", writer_kind="archive_schema_prepare",
                                 operation="prepare_observation_archive")
        with owned_connection_scope(self._connect, writer) as connection, connection.cursor() as cursor:
            _begin_postgres_archive(cursor, "schema")
            cursor.execute(_archive_schema(postgres=True))
            for statement in _history_schema():
                cursor.execute(statement)

    def load_pc_archive_history(self, source_stream: str, source: str, subject: str,
                                first: str, last: str, limit: int, batch_id: str = "") -> list[dict]:
        reader = DBWriterContext(writer_family="read.archive.observation", writer_kind="pc_history",
                                 operation="load_pc_archive_history", access_mode="read")
        with owned_connection_scope(self._connect, reader) as connection, connection.cursor() as cursor:
            return _read_pc_history(cursor, "%s", source_stream, source, subject, first, last, limit, batch_id)

    def accept_observation_archive(self, source_stream: str, raw: bytes, expected_sha256: str) -> dict[str, Any]:
        value = _authenticated_batch(source_stream, raw, expected_sha256)
        writer = DBWriterContext(writer_family="archive.observation", writer_kind="archive_batch_accept",
                                 operation="accept_observation_archive", rows_attempted=1)
        with owned_connection_scope(self._connect, writer) as connection, connection.cursor() as cursor:
            _begin_postgres_archive(cursor, "stream:" + source_stream)
            receipt = _accept_batch(cursor, "%s", value, self._now_provider, POSTGRES_ARCHIVE_DURABILITY)
        return receipt

    def load_observation_archive(
        self, source_stream: str, batch_id: str, *, include_body: bool = False,
    ) -> dict[str, Any] | None:
        reader = DBWriterContext(writer_family="read.archive.observation", writer_kind="archive_batch_load",
                                 operation="load_observation_archive", access_mode="read")
        with owned_connection_scope(self._connect, reader) as connection, connection.cursor() as cursor:
            return _load_batch(cursor, "%s", source_stream, batch_id, include_body)
