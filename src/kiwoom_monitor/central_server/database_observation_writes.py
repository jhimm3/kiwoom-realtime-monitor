"""Shared observation metadata and revision SQL using caller-owned connections."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from kiwoom_monitor.domain.market_data_contract import MarketDataObservation
from kiwoom_monitor.domain.research_contract import (
    OBSERVATION_REVISION_SCHEMA_VERSION, ObservationRevisionSource,
)

def _market_metadata_upsert_sql(placeholder: str, excluded: str) -> str:
    placeholders = ",".join((placeholder,) * 12)
    return (
        f"INSERT INTO central_market_data_observation_meta VALUES({placeholders}) "
        + _market_metadata_upsert_suffix(excluded)
    )

def _market_metadata_upsert_suffix(
    excluded: str, *, distinct_operator: str = "",
) -> str:
    sql = (
        "ON CONFLICT(dataset_kind,subject,observation_key) DO UPDATE SET "
        f"effective_at={excluded}.effective_at,available_at={excluded}.available_at,"
        f"venue={excluded}.venue,unit={excluded}.unit,value_kind={excluded}.value_kind,"
        f"completeness={excluded}.completeness,origin={excluded}.origin,source={excluded}.source,"
        f"candidate_universe={excluded}.candidate_universe"
    )
    if distinct_operator:
        semantic_columns = (
            "effective_at", "venue", "unit", "value_kind", "completeness",
            "origin", "source", "candidate_universe",
        )
        sql += " WHERE " + " OR ".join(
            f"central_market_data_observation_meta.{column} {distinct_operator} {excluded}.{column}"
            for column in semantic_columns
        )
    return sql

def _append_sqlite_observation_revision(
    connection: sqlite3.Connection,
    kind: str,
    subject: str,
    observation_key: str,
    payload: dict[str, Any],
    observation: MarketDataObservation[object],
) -> None:
    source = ObservationRevisionSource.from_observation(
        kind, subject, observation_key, payload, observation,
    )
    latest = connection.execute(
        "SELECT revision_id,payload_hash FROM central_observation_revisions "
        "WHERE kind=? AND subject=? AND observation_key=? AND source_id=? "
        "ORDER BY accepted_sequence DESC LIMIT 1",
        (source.kind, source.subject, source.observation_key, source.source_id),
    ).fetchone()
    if latest is not None and str(latest[1]) == source.payload_hash:
        return
    connection.execute(
        "INSERT INTO central_observation_revisions("
        "revision_id,observation_key,schema_version,source_id,source_session_id,source_sequence,"
        "kind,subject,venue,effective_at,received_at,available_at,revision_of,payload_hash,unit,"
        "value_kind,completeness,origin,candidate_universe,quality_flags_json,clock_quality,"
        "source_ref_json,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        _observation_revision_values(source, latest, sqlite=True),
    )

def _append_postgres_observation_revision(
    cursor: Any,
    kind: str,
    subject: str,
    observation_key: str,
    payload: dict[str, Any],
    observation: MarketDataObservation[object],
) -> bool:
    source = ObservationRevisionSource.from_observation(
        kind, subject, observation_key, payload, observation,
    )
    cursor.execute(
        "SELECT revision_id,payload_hash FROM central_observation_revisions "
        "WHERE kind=%s AND subject=%s AND observation_key=%s AND source_id=%s "
        "ORDER BY accepted_sequence DESC LIMIT 1",
        (source.kind, source.subject, source.observation_key, source.source_id),
    )
    latest = cursor.fetchone()
    return _insert_postgres_observation_revision(cursor, source, latest) is not None

def _insert_postgres_observation_revision(
    cursor: Any, source: ObservationRevisionSource,
    latest: tuple[object, object] | None,
    *, execute_seconds: list[float] | None = None,
) -> str | None:
    if latest is not None and str(latest[1]) == source.payload_hash:
        return None
    values = _observation_revision_values(source, latest, sqlite=False)
    execute_started = monotonic() if execute_seconds is not None else 0.0
    cursor.execute(
        "INSERT INTO central_observation_revisions("
        "revision_id,observation_key,schema_version,source_id,source_session_id,source_sequence,"
        "kind,subject,venue,effective_at,received_at,available_at,revision_of,payload_hash,unit,"
        "value_kind,completeness,origin,candidate_universe,quality_flags_json,clock_quality,"
        "source_ref_json,payload_json) VALUES(" + ",".join(("%s",) * 23) + ")",
        values,
    )
    if execute_seconds is not None:
        execute_seconds[0] += monotonic() - execute_started
    return str(values[0])

def _observation_revision_values(
    source: ObservationRevisionSource,
    latest: tuple[object, ...] | None,
    *,
    sqlite: bool,
) -> tuple[object, ...]:
    effective_at: object = source.effective_at
    available_at: object = source.available_at
    received_at: object = datetime.now(timezone.utc)
    if sqlite:
        effective_at = source.effective_at.isoformat() if source.effective_at else None
        available_at = source.available_at.isoformat() if source.available_at else None
        received_at = received_at.isoformat()
    return (
        str(uuid.uuid4()),
        source.observation_key,
        OBSERVATION_REVISION_SCHEMA_VERSION,
        source.source_id,
        "",
        "",
        source.kind,
        source.subject,
        source.venue,
        effective_at,
        received_at,
        available_at,
        str(latest[0]) if latest is not None else None,
        source.payload_hash,
        source.unit,
        source.value_kind,
        source.completeness,
        source.origin,
        source.candidate_universe,
        "[]",
        "source_timezone_confirmed",
        source.source_ref_json,
        source.payload_json,
    )
