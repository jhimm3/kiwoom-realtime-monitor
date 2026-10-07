from __future__ import annotations

import sqlite3
import threading
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from kiwoom_monitor.infrastructure.central_content_client import (
    CentralContentClient,
    is_missing_collection_error,
)
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


_V1_SPECS = (
    _TableSpec("journal_settings", "journal_settings", ("setting_key",), "updated_at"),
    _TableSpec("journal_fills", "trade_fills", ("fill_key",), "filled_at"),
    _TableSpec("journal_reviews", "trade_reviews", ("group_id",), "updated_at"),
    _TableSpec("journal_setups", "trade_setup_classifications", ("group_id",), "updated_at"),
    _TableSpec("journal_cycle_overrides", "trade_setup_cycle_overrides", ("group_id", "cycle_index"), "updated_at"),
    _TableSpec("journal_group_overrides", "trade_group_overrides", ("fill_key",), "updated_at"),
    _TableSpec("journal_entry_snapshots", "trade_entry_snapshots", ("execution_key",), "captured_at"),
    _TableSpec(
        "journal_costs", "daily_trade_costs",
        ("origin_broker", "origin_environment", "origin_account_ref", "fill_date", "stock_code", "side"),
        "confirmed_at",
    ),
    _TableSpec("journal_stocks", "journal_stocks", ("trade_date", "stock_code"), "last_opened_at"),
    _TableSpec("journal_backfill", "journal_bar_backfill", ("trade_date", "stock_code"), "updated_at"),
)
_SYNC_STATE_COLLECTION = "journal_sync_states"
_V2_SPECS = (
    _TableSpec("journal_v2_fills", "trade_fills", ("fill_key",), "filled_at"),
    _TableSpec("journal_v2_reviews", "trade_reviews", ("group_id",), "updated_at"),
    _TableSpec("journal_v2_setups", "trade_setup_classifications", ("group_id",), "updated_at"),
    _TableSpec("journal_v2_cycle_overrides", "trade_setup_cycle_overrides", ("group_id", "cycle_index"), "updated_at"),
    _TableSpec("journal_v2_group_overrides", "trade_group_overrides", ("fill_key",), "updated_at"),
    _TableSpec("journal_v2_entry_snapshots", "trade_entry_snapshots", ("execution_key",), "captured_at"),
    _TableSpec(
        "journal_v2_costs", "daily_trade_costs",
        ("origin_broker", "origin_environment", "origin_account_ref", "fill_date", "stock_code", "side"),
        "confirmed_at",
    ),
    _TableSpec("journal_v2_enrichment_tasks", "journal_enrichment_tasks", ("task_id",), "updated_at"),
    _TableSpec("journal_v2_analysis_revisions", "journal_analysis_revisions", ("revision_id",), "created_at"),
    _TableSpec("journal_v2_research_links", "journal_research_links", ("link_id",), "created_at"),
)
_ACCOUNT_SCOPED_TABLES = frozenset(spec.table for spec in _V2_SPECS)


class CentralJournalSyncService:
    """매매일지 고유 자료를 로컬 우선으로 중앙 서버와 병합한다."""

    def __init__(self, client: CentralContentClient, *, batch_size: int = 500) -> None:
        self._client = client
        self._batch_size = normalize_batch_size(batch_size)
        self._pending_collections: tuple[str, ...] = ()

    @property
    def pending_collections(self) -> tuple[str, ...]:
        return self._pending_collections

    def sync(self, database_path: Path) -> int:
        self._pending_collections = ()
        if not database_path.is_file():
            return 0
        connection = sqlite3.connect(database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        saved = 0
        pending: list[str] = []
        try:
            tables = table_names(connection)
            capability_reader = getattr(self._client, "capabilities", None)
            v2_enabled = bool(
                callable(capability_reader)
                and capability_reader().get("journal_v2_sync", False)
            )
            sync_states_enabled = "journal_sync_states" in tables
            if sync_states_enabled:
                try:
                    remote_states = self._client.load_all(_SYNC_STATE_COLLECTION)
                except RuntimeError as error:
                    if not is_missing_collection_error(error):
                        raise
                    self._pending_collections = (_SYNC_STATE_COLLECTION,)
                    return 0
                if v2_enabled:
                    try:
                        remote_states.extend(self._client.load_all("journal_v2_sync_states"))
                    except RuntimeError as error:
                        if not is_missing_collection_error(error):
                            raise
                        pending.append("journal_v2_sync_states")
                        v2_enabled = False
                with connection:
                    self._merge_sync_states(connection, remote_states)
                    self._apply_deleted_states(connection)
            specs = (*_V1_SPECS, *(_V2_SPECS if v2_enabled else ()))
            for spec in specs:
                if spec.table not in tables:
                    continue
                remote = self._client.load_all(spec.collection)
                with connection:
                    self._merge_remote(
                        connection, spec, remote,
                        sync_states_enabled=sync_states_enabled,
                    )
                documents = [
                    self._document(spec, dict(row))
                    for row in self._rows_for_spec(connection, spec)
                ]
                saved += upload_documents(self._client, spec.collection, documents, self._batch_size)
            if sync_states_enabled:
                state_rows = list(connection.execute(
                    "SELECT collection,owner,document_key,origin_broker,origin_environment,"
                    "origin_account_ref,canonical_account_ref,is_deleted,updated_at FROM journal_sync_states"
                ))
                legacy_states = [
                    central_document(
                        str(row[1]), str(row[2]),
                        {
                            "collection": str(row[0]), "owner": str(row[1]),
                            "document_key": str(row[2]), "origin_broker": str(row[3]),
                            "origin_environment": str(row[4]), "origin_account_ref": str(row[5]),
                            "canonical_account_ref": str(row[6]), "is_deleted": bool(row[7]),
                            "updated_at": str(row[8]),
                        },
                    )
                    for row in state_rows
                    if not str(row[0]).startswith("journal_v2_") and str(row[1]) == "legacy"
                ]
                saved += upload_documents(
                    self._client, _SYNC_STATE_COLLECTION, legacy_states, self._batch_size,
                )
                if v2_enabled:
                    v2_states = [
                        central_document(
                            str(row[1]), str(row[2]),
                            {
                                "collection": str(row[0]), "owner": str(row[1]),
                                "document_key": str(row[2]), "origin_broker": str(row[3]),
                                "origin_environment": str(row[4]), "origin_account_ref": str(row[5]),
                                "canonical_account_ref": str(row[6]), "is_deleted": bool(row[7]),
                                "updated_at": str(row[8]),
                            },
                        )
                        for row in state_rows
                        if str(row[0]).startswith("journal_v2_")
                        and str(row[1]) == str(row[5]) and str(row[3]) == "kiwoom"
                    ]
                    saved += upload_documents(
                        self._client, "journal_v2_sync_states", v2_states, self._batch_size,
                    )
            self._pending_collections = tuple(pending)
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
            if not isinstance(document, dict):
                continue
            if spec.collection.startswith("journal_v2_"):
                expected_owner = str(document.get("origin_account_ref", ""))
                if str(value.get("owner", "")) != expected_owner:
                    continue
            document = CentralJournalSyncService._account_scoped_document(spec, document)
            if document is None:
                continue
            if any(key not in document for key in spec.keys):
                continue
            incoming = {column: document[column] for column in columns if column in document}
            if spec.version not in incoming or any(key not in incoming for key in spec.keys):
                continue
            document_key = "|".join(str(incoming[key]) for key in spec.keys)
            if str(value.get("key", "")) != document_key:
                continue
            legacy_source = CentralJournalSyncService._legacy_source_identity(
                connection, spec, value, str(incoming[spec.version]), document_key,
            )
            if legacy_source is not None and CentralJournalSyncService._legacy_source_seen(
                connection, legacy_source,
            ):
                continue
            if sync_states_enabled and CentralJournalSyncService._remote_value_is_deleted(
                    connection, spec, document_key, document, str(incoming[spec.version])):
                CentralJournalSyncService._record_legacy_source(connection, legacy_source)
                continue
            where = " AND ".join(f"{key}=?" for key in spec.keys)
            local = connection.execute(
                f"SELECT * FROM {spec.table} WHERE {where}",
                tuple(incoming[key] for key in spec.keys),
            ).fetchone()
            if local is not None and spec.table in _ACCOUNT_SCOPED_TABLES:
                local_is_v2 = str(dict(local).get("origin_broker", "legacy")) != "legacy"
                incoming_is_v2 = spec.collection.startswith("journal_v2_")
                if local_is_v2 != incoming_is_v2:
                    if legacy_source is not None:
                        legacy_source = (*legacy_source[:-1], "CONFLICT")
                    CentralJournalSyncService._record_legacy_source(connection, legacy_source)
                    continue
            if local is not None and str(dict(local).get(spec.version, "")) >= str(incoming[spec.version]):
                if (
                    legacy_source is not None
                    and str(dict(local).get(spec.version, "")) == str(incoming[spec.version])
                    and CentralJournalSyncService._normalized_hash(dict(local))
                    != CentralJournalSyncService._normalized_hash(incoming)
                ):
                    legacy_source = (*legacy_source[:-1], "CONFLICT")
                CentralJournalSyncService._record_legacy_source(connection, legacy_source)
                continue
            names = tuple(incoming)
            updates = tuple(name for name in names if name not in spec.keys)
            sql = (
                f"INSERT INTO {spec.table}({','.join(names)}) VALUES({','.join('?' for _ in names)}) "
                f"ON CONFLICT({','.join(spec.keys)}) DO UPDATE SET "
                + ",".join(f"{name}=excluded.{name}" for name in updates)
            )
            connection.execute(sql, tuple(incoming[name] for name in names))
            CentralJournalSyncService._record_legacy_source(connection, legacy_source)
            if sync_states_enabled:
                CentralJournalSyncService._record_sync_state(
                    connection, spec.collection, document_key, document, False,
                    str(incoming[spec.version]),
                )

    @staticmethod
    def _account_scoped_document(
        spec: _TableSpec, value: dict[str, Any],
    ) -> dict[str, Any] | None:
        document = dict(value)
        if spec.table not in _ACCOUNT_SCOPED_TABLES:
            return document
        if spec.collection.startswith("journal_v2_"):
            required_scope = (
                "origin_broker", "origin_environment", "origin_account_ref", "canonical_account_ref",
            )
            if any(not str(document.get(key, "")).strip() for key in required_scope):
                return None
            if (
                document["origin_broker"] != "kiwoom"
                or document["origin_environment"] not in {"real", "mock"}
            ):
                return None
            try:
                uuid.UUID(str(document["origin_account_ref"]))
                uuid.UUID(str(document["canonical_account_ref"]))
            except (ValueError, AttributeError):
                return None
            if document["origin_account_ref"] != document["canonical_account_ref"]:
                return None
        else:
            supplied_broker = str(document.get("origin_broker", "legacy"))
            if supplied_broker not in {"", "legacy"}:
                return None
            document.update({
                "origin_broker": "legacy", "origin_environment": "unknown",
                "origin_account_ref": "legacy-unassigned",
                "canonical_account_ref": "legacy-unassigned",
            })
        if spec.table == "trade_fills" and "fill_key" not in document:
            required = ("order_no", "stock_code", "filled_at", "side")
            if all(key in document for key in required):
                document["fill_key"] = "|".join(str(document[key]) for key in required)
        return document

    @staticmethod
    def _merge_sync_states(connection: sqlite3.Connection, values: list[dict[str, Any]]) -> None:
        for value in values:
            document = value.get("document")
            if not isinstance(document, dict):
                continue
            collection = str(document.get("collection", ""))
            owner = str(document.get("owner", value.get("owner", "")))
            document_key = str(document.get("document_key", ""))
            updated_at = str(document.get("updated_at", ""))
            if not collection or not owner or not document_key or not updated_at:
                continue
            if str(value.get("owner", "")) != owner or str(value.get("key", "")) != document_key:
                continue
            is_v2 = collection.startswith("journal_v2_")
            if is_v2:
                probe = CentralJournalSyncService._account_scoped_document(
                    _TableSpec(collection, "trade_fills", ("fill_key",), "filled_at"), document,
                )
                if probe is None or owner != str(document.get("origin_account_ref", "")):
                    continue
            elif str(document.get("origin_broker", "legacy")) not in {"legacy", "unknown"}:
                continue
            local = connection.execute(
                "SELECT updated_at FROM journal_sync_states WHERE collection=? AND owner=? AND document_key=?",
                (collection, owner, document_key),
            ).fetchone()
            if local is not None and str(local[0]) >= updated_at:
                continue
            CentralJournalSyncService._record_sync_state(
                connection, collection, document_key, document,
                bool(document.get("is_deleted")), updated_at,
            )

    @staticmethod
    def _apply_deleted_states(connection: sqlite3.Connection) -> None:
        specs = {spec.collection: spec for spec in (*_V1_SPECS, *_V2_SPECS)}
        rows = connection.execute(
            "SELECT collection,owner,document_key,origin_broker,origin_environment,"
            "origin_account_ref,canonical_account_ref,updated_at FROM journal_sync_states WHERE is_deleted=1"
        ).fetchall()
        for collection, owner, document_key, broker, environment, origin_ref, canonical_ref, deleted_at in rows:
            spec = specs.get(str(collection))
            if spec is None:
                continue
            key_values = CentralJournalSyncService._key_values(str(document_key), len(spec.keys))
            if key_values is None:
                continue
            where = " AND ".join(f"{key}=?" for key in spec.keys)
            scope_clause = ""
            if spec.table in _ACCOUNT_SCOPED_TABLES:
                scope_clause = " AND origin_broker=? AND origin_environment=? AND origin_account_ref=?"
                key_values = (*key_values, broker, environment, origin_ref)
            local = connection.execute(
                f"SELECT {spec.version} FROM {spec.table} WHERE {where}{scope_clause}", key_values,
            ).fetchone()
            if local is not None and str(local[0]) > str(deleted_at):
                CentralJournalSyncService._record_sync_state(
                    connection, str(collection), str(document_key), {
                        "origin_broker": str(broker), "origin_environment": str(environment),
                        "origin_account_ref": str(origin_ref),
                        "canonical_account_ref": str(canonical_ref),
                    }, False, str(local[0]),
                )
                continue
            connection.execute(f"DELETE FROM {spec.table} WHERE {where}{scope_clause}", key_values)

    @staticmethod
    def _remote_value_is_deleted(
        connection: sqlite3.Connection,
        spec: _TableSpec,
        document_key: str,
        document: dict[str, Any],
        value_updated_at: str,
    ) -> bool:
        owner = str(document.get("origin_account_ref", "")) if spec.collection.startswith("journal_v2_") else "legacy"
        row = connection.execute(
            "SELECT is_deleted, updated_at FROM journal_sync_states "
            "WHERE collection=? AND owner=? AND document_key=?",
            (spec.collection, owner, document_key),
        ).fetchone()
        return bool(row and bool(row[0]) and str(row[1]) >= value_updated_at)

    @staticmethod
    def _record_sync_state(
        connection: sqlite3.Connection,
        collection: str,
        document_key: str,
        document: dict[str, Any],
        is_deleted: bool,
        updated_at: str,
    ) -> None:
        is_v2 = collection.startswith("journal_v2_")
        owner = str(document.get("origin_account_ref", "")) if is_v2 else "legacy"
        broker = str(document.get("origin_broker", "legacy"))
        environment = str(document.get("origin_environment", "unknown"))
        origin_ref = str(document.get("origin_account_ref", "legacy-unassigned"))
        canonical_ref = str(document.get("canonical_account_ref", origin_ref))
        connection.execute(
            "INSERT INTO journal_sync_states VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
            "is_deleted=excluded.is_deleted, updated_at=excluded.updated_at",
            (collection,owner,document_key,broker,environment,origin_ref,canonical_ref,int(is_deleted),updated_at),
        )

    @staticmethod
    def _legacy_source_identity(
        connection: sqlite3.Connection,
        spec: _TableSpec,
        envelope: dict[str, Any],
        source_revision: str,
        target_key: str,
    ) -> tuple[str, str, str, str, str, str, str, str, str] | None:
        if (
            spec.collection.startswith("journal_v2_")
            or spec.table not in _ACCOUNT_SCOPED_TABLES
            or "journal_legacy_imports" not in table_names(connection)
        ):
            return None
        source_key = str(envelope.get("key", "")).strip()
        if not source_key or not source_revision:
            return None
        document = envelope.get("document")
        if not isinstance(document, dict):
            return None
        source_owner = str(envelope.get("owner", "")).strip() or "unknown"
        modified_at = source_revision if spec.version == "updated_at" else ""
        return (
            spec.collection, source_owner, source_key,
            CentralJournalSyncService._normalized_hash(document),
            str(envelope.get("updated_at", "")), modified_at,
            spec.collection, target_key, "IMPORTED",
        )

    @staticmethod
    def _legacy_source_seen(
        connection: sqlite3.Connection,
        source: tuple[str, str, str, str, str, str, str, str, str],
    ) -> bool:
        return connection.execute(
            "SELECT 1 FROM journal_legacy_imports WHERE source_collection=? "
            "AND source_content_hash=? AND target_collection=? AND target_key=?",
            (source[0], source[3], source[6], source[7]),
        ).fetchone() is not None

    @staticmethod
    def _record_legacy_source(
        connection: sqlite3.Connection,
        source: tuple[str, str, str, str, str, str, str, str, str] | None,
    ) -> None:
        if source is None:
            return
        connection.execute(
            "INSERT OR IGNORE INTO journal_legacy_imports("
            "source_collection,source_owner,source_key,source_content_hash,source_observed_at,"
            "source_modified_at,target_collection,target_key,status,imported_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (*source, datetime.now(UTC).isoformat()),
        )

    @staticmethod
    def _normalized_hash(value: dict[str, Any]) -> str:
        legacy_scope = str(value.get("origin_broker", "legacy")) in {"", "legacy"}
        payload = {
            key: item for key, item in value.items()
            if key not in {"updated_at", "filled_at", "captured_at"}
            and not (
                legacy_scope and key in {
                    "origin_broker", "origin_environment", "origin_account_ref",
                    "canonical_account_ref", "fill_key",
                }
            )
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _key_values(document_key: str, count: int) -> tuple[str, ...] | None:
        if count == 1:
            return (document_key,)
        values = tuple(document_key.split("|", count - 1))
        return values if len(values) == count else None

    @staticmethod
    def _document(spec: _TableSpec, value: dict[str, Any]) -> dict[str, Any]:
        scoped = CentralJournalSyncService._account_scoped_document(spec, value)
        if scoped is None:
            raise ValueError("account-scoped journal row is missing a verified scope")
        value = scoped
        key = "|".join(str(value[name]) for name in spec.keys)
        owner = (
            str(value["origin_account_ref"])
            if spec.collection.startswith("journal_v2_")
            else str(value.get("stock_code") or value.get("group_id") or "default")
        )
        return central_document(owner, key, value)

    @staticmethod
    def _rows_for_spec(connection: sqlite3.Connection, spec: _TableSpec) -> list[sqlite3.Row]:
        if spec.table not in _ACCOUNT_SCOPED_TABLES:
            return connection.execute(f"SELECT * FROM {spec.table}").fetchall()
        columns = {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({spec.table})")
        }
        if "origin_broker" not in columns:
            return [] if spec.collection.startswith("journal_v2_") else connection.execute(
                f"SELECT * FROM {spec.table}"
            ).fetchall()
        if spec.collection.startswith("journal_v2_"):
            return connection.execute(
                f"SELECT * FROM {spec.table} WHERE origin_broker<>'legacy'"
            ).fetchall()
        return connection.execute(
            f"SELECT * FROM {spec.table} WHERE origin_broker='legacy'"
        ).fetchall()


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
