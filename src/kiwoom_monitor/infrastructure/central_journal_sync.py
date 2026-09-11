from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.central_sync_utils import (
    central_document,
    normalize_batch_size,
    table_names,
    upload_documents,
)


@dataclass(frozen=True)
class _TableSpec:
    collection: str
    table: str
    keys: tuple[str, ...]
    version: str


_SPECS = (
    _TableSpec("journal_settings", "journal_settings", ("setting_key",), "updated_at"),
    _TableSpec("journal_fills", "trade_fills", ("order_no", "stock_code", "filled_at", "side"), "filled_at"),
    _TableSpec("journal_reviews", "trade_reviews", ("group_id",), "updated_at"),
    _TableSpec("journal_setups", "trade_setup_classifications", ("group_id",), "updated_at"),
    _TableSpec("journal_cycle_overrides", "trade_setup_cycle_overrides", ("group_id", "cycle_index"), "updated_at"),
    _TableSpec("journal_group_overrides", "trade_group_overrides", ("fill_key",), "updated_at"),
    _TableSpec("journal_entry_snapshots", "trade_entry_snapshots", ("execution_key",), "captured_at"),
    _TableSpec("journal_costs", "daily_trade_costs", ("fill_date", "stock_code", "side"), "confirmed_at"),
    _TableSpec("journal_stocks", "journal_stocks", ("trade_date", "stock_code"), "last_opened_at"),
    _TableSpec("journal_backfill", "journal_bar_backfill", ("trade_date", "stock_code"), "updated_at"),
)
_SYNC_STATE_COLLECTION = "journal_sync_states"


class CentralJournalSyncService:
    """매매일지 고유 자료를 로컬 우선으로 중앙 서버와 병합한다."""

    def __init__(self, client: CentralContentClient, *, batch_size: int = 500) -> None:
        self._client = client
        self._batch_size = normalize_batch_size(batch_size)

    def sync(self, database_path: Path) -> int:
        if not database_path.is_file():
            return 0
        connection = sqlite3.connect(database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        saved = 0
        try:
            tables = table_names(connection)
            sync_states_enabled = "journal_sync_states" in tables
            if sync_states_enabled:
                remote_states = self._client.load_all(_SYNC_STATE_COLLECTION)
                with connection:
                    self._merge_sync_states(connection, remote_states)
                    self._apply_deleted_states(connection)
            for spec in _SPECS:
                if spec.table not in tables:
                    continue
                remote = self._client.load_all(spec.collection)
                with connection:
                    self._merge_remote(
                        connection, spec, remote,
                        sync_states_enabled=sync_states_enabled,
                    )
                documents = [self._document(spec, dict(row)) for row in connection.execute(
                    f"SELECT * FROM {spec.table}"
                )]
                saved += upload_documents(self._client, spec.collection, documents, self._batch_size)
            if sync_states_enabled:
                states = [
                    central_document(
                        str(row[0]), str(row[1]),
                        {
                            "collection": str(row[0]), "document_key": str(row[1]),
                            "is_deleted": bool(row[2]), "updated_at": str(row[3]),
                        },
                    )
                    for row in connection.execute(
                        "SELECT collection, document_key, is_deleted, updated_at FROM journal_sync_states"
                    )
                ]
                saved += upload_documents(
                    self._client, _SYNC_STATE_COLLECTION, states, self._batch_size,
                )
        finally:
            connection.close()
        return saved

    @staticmethod
    def _merge_remote(connection: sqlite3.Connection, spec: _TableSpec,
                      values: list[dict[str, Any]], *,
                      sync_states_enabled: bool = False) -> None:
        columns = tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({spec.table})"))
        for value in values:
            document = value.get("document")
            if not isinstance(document, dict) or any(key not in document for key in spec.keys):
                continue
            incoming = {column: document[column] for column in columns if column in document}
            if spec.version not in incoming or any(key not in incoming for key in spec.keys):
                continue
            document_key = "|".join(str(incoming[key]) for key in spec.keys)
            if sync_states_enabled and CentralJournalSyncService._remote_value_is_deleted(
                    connection, spec.collection, document_key, str(incoming[spec.version])):
                continue
            where = " AND ".join(f"{key}=?" for key in spec.keys)
            local = connection.execute(
                f"SELECT {spec.version} FROM {spec.table} WHERE {where}",
                tuple(incoming[key] for key in spec.keys),
            ).fetchone()
            if local is not None and str(local[0]) >= str(incoming[spec.version]):
                continue
            names = tuple(incoming)
            updates = tuple(name for name in names if name not in spec.keys)
            sql = (
                f"INSERT INTO {spec.table}({','.join(names)}) VALUES({','.join('?' for _ in names)}) "
                f"ON CONFLICT({','.join(spec.keys)}) DO UPDATE SET "
                + ",".join(f"{name}=excluded.{name}" for name in updates)
            )
            connection.execute(sql, tuple(incoming[name] for name in names))
            if sync_states_enabled:
                CentralJournalSyncService._record_sync_state(
                    connection, spec.collection, document_key, False, str(incoming[spec.version]),
                )

    @staticmethod
    def _merge_sync_states(connection: sqlite3.Connection, values: list[dict[str, Any]]) -> None:
        for value in values:
            document = value.get("document")
            if not isinstance(document, dict):
                continue
            collection = str(document.get("collection", ""))
            document_key = str(document.get("document_key", ""))
            updated_at = str(document.get("updated_at", ""))
            if not collection or not document_key or not updated_at:
                continue
            local = connection.execute(
                "SELECT updated_at FROM journal_sync_states WHERE collection=? AND document_key=?",
                (collection, document_key),
            ).fetchone()
            if local is not None and str(local[0]) >= updated_at:
                continue
            CentralJournalSyncService._record_sync_state(
                connection, collection, document_key, bool(document.get("is_deleted")), updated_at,
            )

    @staticmethod
    def _apply_deleted_states(connection: sqlite3.Connection) -> None:
        specs = {spec.collection: spec for spec in _SPECS}
        rows = connection.execute(
            "SELECT collection, document_key, updated_at FROM journal_sync_states WHERE is_deleted=1"
        ).fetchall()
        for collection, document_key, deleted_at in rows:
            spec = specs.get(str(collection))
            if spec is None:
                continue
            key_values = CentralJournalSyncService._key_values(str(document_key), len(spec.keys))
            if key_values is None:
                continue
            where = " AND ".join(f"{key}=?" for key in spec.keys)
            local = connection.execute(
                f"SELECT {spec.version} FROM {spec.table} WHERE {where}", key_values,
            ).fetchone()
            if local is not None and str(local[0]) > str(deleted_at):
                CentralJournalSyncService._record_sync_state(
                    connection, str(collection), str(document_key), False, str(local[0]),
                )
                continue
            connection.execute(f"DELETE FROM {spec.table} WHERE {where}", key_values)

    @staticmethod
    def _remote_value_is_deleted(
        connection: sqlite3.Connection,
        collection: str,
        document_key: str,
        value_updated_at: str,
    ) -> bool:
        row = connection.execute(
            "SELECT is_deleted, updated_at FROM journal_sync_states "
            "WHERE collection=? AND document_key=?",
            (collection, document_key),
        ).fetchone()
        return bool(row and bool(row[0]) and str(row[1]) >= value_updated_at)

    @staticmethod
    def _record_sync_state(
        connection: sqlite3.Connection,
        collection: str,
        document_key: str,
        is_deleted: bool,
        updated_at: str,
    ) -> None:
        connection.execute(
            "INSERT INTO journal_sync_states VALUES (?, ?, ?, ?) "
            "ON CONFLICT(collection, document_key) DO UPDATE SET "
            "is_deleted=excluded.is_deleted, updated_at=excluded.updated_at",
            (collection, document_key, int(is_deleted), updated_at),
        )

    @staticmethod
    def _key_values(document_key: str, count: int) -> tuple[str, ...] | None:
        if count == 1:
            return (document_key,)
        values = tuple(document_key.split("|", count - 1))
        return values if len(values) == count else None

    @staticmethod
    def _document(spec: _TableSpec, value: dict[str, Any]) -> dict[str, Any]:
        key = "|".join(str(value[name]) for name in spec.keys)
        owner = str(value.get("stock_code") or value.get("group_id") or "default")
        return central_document(owner, key, value)


class CentralJournalSyncRunner:
    """중앙 매매일지 동기화의 단일 백그라운드 실행 상태를 소유한다."""

    def __init__(
        self,
        service: CentralJournalSyncService | None,
        database_path: Path,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._service = service
        self._database_path = database_path
        self._on_error = on_error
        self._running = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def schedule(self) -> bool:
        if self._service is None or self._running.is_set():
            return False
        self._running.set()
        threading.Thread(
            target=self._run,
            name="central-journal-settings-sync",
            daemon=True,
        ).start()
        return True

    def _run(self) -> None:
        try:
            assert self._service is not None
            self._service.sync(self._database_path)
        except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
            if self._on_error is not None:
                self._on_error(error)
        finally:
            self._running.clear()
