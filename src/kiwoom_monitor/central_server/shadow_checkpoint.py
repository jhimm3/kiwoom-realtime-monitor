"""Versioned PostgreSQL checkpoint layout; the caller owns the transaction.

Logical checkpoint documents keep their existing shape. Only retained working
frames are normalized here; observation and decision ledgers are never edited.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


STORAGE_VERSION = 2
MIGRATION_VERSION = 21
MIGRATION_NAME = "shadow_checkpoint_frames"
HEADER_TABLE = "central_shadow_checkpoint_state"
FRAME_TABLE = "central_shadow_checkpoint_frames"


@dataclass(frozen=True)
class CheckpointParts:
    header: str
    order: str
    frames: str


def split_checkpoint(document: dict[str, Any]) -> CheckpointParts | None:
    """Unsupported/ambiguous documents keep the lossless inline representation."""
    if document.get("schema_version") != 1 or not isinstance(document.get("bars"), list):
        return None
    keys = []
    frames = []
    seen = set()
    for frame in document["bars"]:
        if not isinstance(frame, dict):
            return None
        code, observation_key = frame.get("code"), frame.get("observation_key")
        if not isinstance(code, str) or not code or not isinstance(observation_key, str) or not observation_key:
            return None
        key = (code, observation_key)
        if key in seen:
            return None
        seen.add(key)
        keys.append(key)
        frames.append({"code": code, "observation_key": observation_key, "frame_json": frame})
    encode = lambda value: json.dumps(value, ensure_ascii=False)
    return CheckpointParts(
        encode({name: value for name, value in document.items() if name != "bars"}),
        encode(keys), encode(frames),
    )


def postgres_schema_statements() -> tuple[str, ...]:
    return (
        f"CREATE TABLE IF NOT EXISTS {HEADER_TABLE} ("
        "monitor_id TEXT PRIMARY KEY REFERENCES central_shadow_monitor_state(monitor_id) ON DELETE CASCADE, "
        "storage_version INTEGER NOT NULL, updated_at TIMESTAMPTZ NOT NULL, "
        "document_json JSONB NOT NULL, bar_order JSONB NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS {FRAME_TABLE} ("
        f"monitor_id TEXT NOT NULL REFERENCES {HEADER_TABLE}(monitor_id) ON DELETE CASCADE, "
        "code TEXT NOT NULL, observation_key TEXT NOT NULL, frame_json JSONB NOT NULL, "
        "PRIMARY KEY(monitor_id,code,observation_key))",
    )


LOAD_SQL = f"""
SELECT legacy.document_json, header.storage_version, header.document_json,
       header.bar_order,
       (SELECT COALESCE(jsonb_agg(jsonb_build_array(item.value, frame.frame_json)
                                 ORDER BY item.ordinality), '[]'::jsonb)
        FROM jsonb_array_elements(COALESCE(header.bar_order, '[]'::jsonb))
             WITH ORDINALITY AS item(value, ordinality)
        LEFT JOIN {FRAME_TABLE} frame ON frame.monitor_id=header.monitor_id
             AND frame.code=item.value->>0 AND frame.observation_key=item.value->>1)
FROM central_shadow_monitor_state legacy
LEFT JOIN {HEADER_TABLE} header ON header.monitor_id=legacy.monitor_id
WHERE legacy.monitor_id=%s
"""


def _decoded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def restore_checkpoint(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    legacy, version, header, order, entries = row
    if version is None:
        value = _decoded(legacy)
        return dict(value) if isinstance(value, dict) else {}
    if version != STORAGE_VERSION:
        raise RuntimeError("unsupported shadow checkpoint storage version")
    header, order, entries = map(_decoded, (header, order, entries))
    if (not isinstance(header, dict) or header.get("schema_version") != 1 or "bars" in header
            or not isinstance(order, list) or not isinstance(entries, list)):
        raise RuntimeError("invalid shadow checkpoint header")
    if len(order) != len(entries):
        raise RuntimeError("shadow checkpoint frame count mismatch")
    frames = []
    seen = set()
    for key, entry in zip(order, entries):
        if not isinstance(key, list) or len(key) != 2 or not all(isinstance(v, str) and v for v in key):
            raise RuntimeError("invalid shadow checkpoint frame key")
        if tuple(key) in seen:
            raise RuntimeError("duplicate shadow checkpoint frame key")
        seen.add(tuple(key))
        if not isinstance(entry, list) or len(entry) != 2 or entry[0] != key:
            raise RuntimeError("invalid shadow checkpoint frame order")
        frame = entry[1]
        if not isinstance(frame, dict) or [frame.get("code"), frame.get("observation_key")] != key:
            raise RuntimeError("missing or mismatched shadow checkpoint frame")
        frames.append(frame)
    return {**header, "bars": frames}


def save_frames(cursor: Any, monitor_id: str, encoded: str, parts: CheckpointParts,
                updated_at: datetime) -> dict[str, int]:
    # This legacy parent is also the lock used by inline writers. Keep its first
    # complete document for migration/downgrade; v2 is authoritative afterwards.
    cursor.execute(
        "INSERT INTO central_shadow_monitor_state VALUES(%s,%s,'{}'::jsonb) "
        "ON CONFLICT(monitor_id) DO NOTHING RETURNING monitor_id", (monitor_id, updated_at),
    )
    created = cursor.fetchone() is not None
    if created:
        cursor.execute("UPDATE central_shadow_monitor_state SET document_json=%s WHERE monitor_id=%s",
                       (encoded, monitor_id))
    cursor.execute("SELECT monitor_id FROM central_shadow_monitor_state WHERE monitor_id=%s FOR UPDATE",
                   (monitor_id,))
    cursor.execute(
        f"INSERT INTO {HEADER_TABLE} VALUES(%s,%s,%s,%s,%s) "
        "ON CONFLICT(monitor_id) DO UPDATE SET storage_version=EXCLUDED.storage_version, "
        "updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json, "
        f"bar_order=CASE WHEN {HEADER_TABLE}.bar_order IS DISTINCT FROM EXCLUDED.bar_order "
        f"THEN EXCLUDED.bar_order ELSE {HEADER_TABLE}.bar_order END",
        (monitor_id, STORAGE_VERSION, updated_at, parts.header, parts.order),
    )
    cursor.execute(
        f"WITH incoming AS (SELECT * FROM jsonb_to_recordset(%s::jsonb) "
        "AS item(code TEXT,observation_key TEXT,frame_json JSONB)) "
        f"INSERT INTO {FRAME_TABLE}(monitor_id,code,observation_key,frame_json) "
        "SELECT %s,item.code,item.observation_key,item.frame_json FROM incoming item "
        f"LEFT JOIN {FRAME_TABLE} previous ON previous.monitor_id=%s AND previous.code=item.code "
        "AND previous.observation_key=item.observation_key "
        "WHERE previous.monitor_id IS NULL OR previous.frame_json IS DISTINCT FROM item.frame_json "
        "ON CONFLICT(monitor_id,code,observation_key) DO UPDATE SET frame_json=EXCLUDED.frame_json "
        f"WHERE {FRAME_TABLE}.frame_json IS DISTINCT FROM EXCLUDED.frame_json",
        (parts.frames, monitor_id, monitor_id),
    )
    changed = cursor.rowcount
    cursor.execute(
        f"WITH retained AS (SELECT item->>0 AS code,item->>1 AS observation_key "
        f"FROM {HEADER_TABLE},jsonb_array_elements(bar_order) AS item WHERE monitor_id=%s) "
        f"DELETE FROM {FRAME_TABLE} frame WHERE frame.monitor_id=%s AND NOT EXISTS "
        "(SELECT 1 FROM retained WHERE retained.code=frame.code "
        "AND retained.observation_key=frame.observation_key)",
        (monitor_id, monitor_id),
    )
    return {"checkpoint_frames_changed": changed, "checkpoint_frames_deleted": cursor.rowcount,
            "checkpoint_storage_version": STORAGE_VERSION, "checkpoint_legacy_created": int(created)}


def clear_frames(cursor: Any, monitor_id: str) -> None:
    cursor.execute(f"DELETE FROM {HEADER_TABLE} WHERE monitor_id=%s", (monitor_id,))


def downgrade_schema(cursor: Any) -> int:
    """Called only offline, in one caller-owned observed transaction.

    Old schema runners reject version 21 even with no v2 rows. Conversion, new
    table removal, and the matching migration marker therefore commit together.
    """
    cursor.execute("SELECT version,name FROM central_schema_migrations ORDER BY version")
    versions = cursor.fetchall()
    latest = int(versions[-1][0]) if versions else 0
    if latest < MIGRATION_VERSION:
        return 0
    if latest != MIGRATION_VERSION or versions[-1][1] != MIGRATION_NAME:
        raise RuntimeError("shadow checkpoint downgrade requires exactly its known migration")
    cursor.execute(f"LOCK TABLE central_shadow_monitor_state,{HEADER_TABLE},{FRAME_TABLE} "
                   "IN ACCESS EXCLUSIVE MODE")
    cursor.execute(f"SELECT monitor_id FROM {HEADER_TABLE} ORDER BY monitor_id")
    ids = [row[0] for row in cursor.fetchall()]
    for monitor_id in ids:
        cursor.execute(LOAD_SQL, (monitor_id,))
        document = restore_checkpoint(cursor.fetchone())
        if document is None:
            raise RuntimeError("missing shadow checkpoint parent during downgrade")
        cursor.execute("UPDATE central_shadow_monitor_state SET updated_at=%s,document_json=%s "
                       "WHERE monitor_id=%s", (datetime.now(timezone.utc), json.dumps(document, ensure_ascii=False), monitor_id))
    cursor.execute(f"DROP TABLE {FRAME_TABLE}")
    cursor.execute(f"DROP TABLE {HEADER_TABLE}")
    cursor.execute("DELETE FROM central_schema_migrations WHERE version=%s AND name=%s",
                   (MIGRATION_VERSION, MIGRATION_NAME))
    return len(ids)
