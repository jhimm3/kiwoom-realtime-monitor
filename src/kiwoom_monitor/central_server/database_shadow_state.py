"""Shadow monitor state and evaluation DB implementations.

Store methods retain their native connection and transaction ownership. The
checkpoint frame codec stays in shadow_checkpoint and receives the caller-owned
PostgreSQL cursor.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from kiwoom_monitor.central_server.database_codec import bounded_limit, json_mapping

def _canonical_document(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _save_sqlite_shadow_evaluation(
    connection: sqlite3.Connection,
    monitor_id: str,
    decision: dict[str, Any],
    candidate: dict[str, Any] | None,
    expires_at: str,
) -> None:
    encoded_decision = _canonical_document(decision)
    existing = connection.execute(
        "SELECT document_json FROM central_shadow_decisions WHERE decision_id=?",
        (str(decision["decision_id"]),),
    ).fetchone()
    if existing is not None and str(existing[0]) != encoded_decision:
        raise ValueError("shadow decision id already has different immutable content")
    connection.execute(
        "INSERT OR IGNORE INTO central_shadow_decisions VALUES(?,?,?,?)",
        (str(decision["decision_id"]), monitor_id, str(decision["decided_at"]), encoded_decision),
    )
    if candidate is None:
        return
    encoded_candidate = _canonical_document(candidate)
    existing = connection.execute(
        "SELECT document_json FROM central_shadow_candidate_events WHERE event_id=?",
        (str(candidate["event_id"]),),
    ).fetchone()
    if existing is not None and str(existing[0]) != encoded_candidate:
        raise ValueError("shadow candidate id already has different immutable content")
    connection.execute(
        "INSERT OR IGNORE INTO central_shadow_candidate_events("
        "event_id,monitor_id,available_at,expires_at,document_json) VALUES(?,?,?,?,?)",
        (str(candidate["event_id"]), monitor_id, str(candidate["available_at"]), expires_at, encoded_candidate),
    )


def _save_postgres_shadow_evaluation(
    cursor: Any,
    monitor_id: str,
    decision: dict[str, Any],
    candidate: dict[str, Any] | None,
    expires_at: str,
) -> None:
    cursor.execute(
        "SELECT document_json FROM central_shadow_decisions WHERE decision_id=%s",
        (str(decision["decision_id"]),),
    )
    existing = cursor.fetchone()
    if existing is not None and _canonical_document(json_mapping(existing[0])) != _canonical_document(decision):
        raise ValueError("shadow decision id already has different immutable content")
    cursor.execute(
        "INSERT INTO central_shadow_decisions VALUES(%s,%s,%s,%s) ON CONFLICT(decision_id) DO NOTHING",
        (str(decision["decision_id"]), monitor_id, str(decision["decided_at"]),
         json.dumps(decision, ensure_ascii=False)),
    )
    if candidate is None:
        return
    cursor.execute(
        "SELECT document_json FROM central_shadow_candidate_events WHERE event_id=%s",
        (str(candidate["event_id"]),),
    )
    existing = cursor.fetchone()
    if existing is not None and _canonical_document(json_mapping(existing[0])) != _canonical_document(candidate):
        raise ValueError("shadow candidate id already has different immutable content")
    cursor.execute(
        "INSERT INTO central_shadow_candidate_events("
        "event_id,monitor_id,available_at,expires_at,document_json) VALUES(%s,%s,%s,%s,%s) "
        "ON CONFLICT(event_id) DO NOTHING",
        (str(candidate["event_id"]), monitor_id, str(candidate["available_at"]), expires_at,
         json.dumps(candidate, ensure_ascii=False)),
    )


def _shadow_candidate_page(rows: list[tuple[object, ...]], high_watermark: int) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for sequence, raw_document, expires_at in rows:
        document = json_mapping(raw_document)
        document["accepted_sequence"] = int(sequence)
        document["expires_at"] = expires_at.isoformat() if hasattr(expires_at, "isoformat") else str(expires_at)
        events.append(document)
    next_cursor = int(events[-1]["accepted_sequence"]) if events else None
    return {
        "high_watermark": max(0, int(high_watermark)),
        "events": events,
        "next_cursor": next_cursor,
        "has_more": bool(next_cursor is not None and next_cursor < high_watermark),
    }


class SQLiteShadowStateStoreMixin:
    def load_shadow_monitor_state(self, monitor_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT document_json FROM central_shadow_monitor_state WHERE monitor_id=?",
                (monitor_id,),
            ).fetchone()
        return json_mapping(row[0]) if row else None

    def save_shadow_monitor_state(self, monitor_id: str, document: dict[str, Any]) -> None:
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO central_shadow_monitor_state VALUES(?,?,?) "
                "ON CONFLICT(monitor_id) DO UPDATE SET updated_at=excluded.updated_at,document_json=excluded.document_json",
                (monitor_id, datetime.now(timezone.utc).isoformat(), encoded),
            )

    def save_shadow_evaluation(
        self, monitor_id: str, decision: dict[str, Any],
        candidate: dict[str, Any] | None, expires_at: str = "",
    ) -> None:
        with self._lock, self._connection() as connection:
            _save_sqlite_shadow_evaluation(connection, monitor_id, decision, candidate, expires_at)

    def load_shadow_candidates(self, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]:
        page_limit = bounded_limit(limit, 1000)
        with self._lock, self._connection() as connection:
            watermark_row = connection.execute(
                "SELECT COALESCE(MAX(accepted_sequence),0) FROM central_shadow_candidate_events"
            ).fetchone()
            rows = connection.execute(
                "SELECT accepted_sequence,document_json,expires_at FROM central_shadow_candidate_events "
                "WHERE accepted_sequence>? ORDER BY accepted_sequence LIMIT ?",
                (max(0, int(after_sequence)), page_limit),
            ).fetchall()
        return _shadow_candidate_page(rows, int(watermark_row[0]) if watermark_row else 0)

class PostgresShadowStateStoreMixin:
    def load_shadow_monitor_state(self, monitor_id: str) -> dict[str, Any] | None:
        from .postgres_access import DBWriterContext, open_observed_connection
        from .shadow_checkpoint import LOAD_SQL, restore_checkpoint

        context = DBWriterContext(
            writer_family="read.shadow_monitor",
            writer_kind="monitor_state",
            operation="load_shadow_monitor_state",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            cursor.execute(LOAD_SQL, (monitor_id,))
            row = cursor.fetchone()
        return restore_checkpoint(row)

    def save_shadow_monitor_state(self, monitor_id: str, document: dict[str, Any]) -> None:
        from .postgres_access import DBWriterContext, open_observed_connection
        from .diagnostic_metrics import record_writer_transaction
        from .shadow_checkpoint import clear_frames, save_frames, split_checkpoint

        started_at = monotonic()
        writer = DBWriterContext(
            writer_family="candidate.shadow_checkpoint",
            writer_kind="shadow_monitor_state",
            operation="save_shadow_monitor_state", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            updated_at = datetime.now(timezone.utc)
            encode_started = monotonic()
            encoded = json.dumps(document, ensure_ascii=False)
            payload_bytes = len(encoded.encode("utf-8"))
            parts = split_checkpoint(document) if self._shadow_checkpoint_frames_enabled else None
            encode_ms = round((monotonic() - encode_started) * 1000, 3)
            counts = {"checkpoint_storage_version": 1}
            if parts is not None:
                counts = save_frames(cursor, monitor_id, encoded, parts, updated_at)
            else:
                # The UPSERT locks the same stable parent used by normalized
                # writers. Retire any v2 authority in this same transaction.
                cursor.execute(
                    "INSERT INTO central_shadow_monitor_state VALUES(%s,%s,%s) "
                    "ON CONFLICT(monitor_id) DO UPDATE SET updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json",
                    (monitor_id, updated_at, encoded),
                )
                clear_frames(cursor, monitor_id)
        record_writer_transaction(
            "shadow_monitor_state", 1, round((monotonic() - started_at) * 1000),
            encode_ms=encode_ms, bytes_payload_estimate=payload_bytes,
            domain_counts={
                **counts,
                "checkpoint_bar_frames": len(document.get("bars", ())),
                "checkpoint_universe_frames": len(document.get("universes", ())),
                "checkpoint_emitted_candidate_keys": len(
                    document.get("strategy_state", {}).get("emitted_candidate_keys", ()),
                ),
            },
            db_call_id=writer.call_id,
        )

    def save_shadow_evaluation(
        self, monitor_id: str, decision: dict[str, Any],
        candidate: dict[str, Any] | None, expires_at: str = "",
    ) -> None:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="candidate.shadow_evaluation",
            writer_kind="shadow_evaluation",
            operation="save_shadow_evaluation",
            rows_attempted=1 + int(candidate is not None),
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            _save_postgres_shadow_evaluation(cursor, monitor_id, decision, candidate, expires_at)

    def load_shadow_candidates(self, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        page_limit = bounded_limit(limit, 1000)
        context = DBWriterContext(
            writer_family="read.shadow_monitor",
            writer_kind="candidate_events",
            operation="load_shadow_candidates",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COALESCE(MAX(accepted_sequence),0) FROM central_shadow_candidate_events")
            watermark_row = cursor.fetchone()
            cursor.execute(
                "SELECT accepted_sequence,document_json,expires_at::text "
                "FROM central_shadow_candidate_events WHERE accepted_sequence>%s "
                "ORDER BY accepted_sequence LIMIT %s",
                (max(0, int(after_sequence)), page_limit),
            )
            rows = cursor.fetchall()
        return _shadow_candidate_page(rows, int(watermark_row[0]) if watermark_row else 0)
