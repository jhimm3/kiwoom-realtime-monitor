"""Shared observation metadata and revision SQL using caller-owned connections."""
from __future__ import annotations

import sqlite3
import json
import uuid
from datetime import datetime
from time import monotonic
from typing import Any, Callable

from .market_observations import KST, as_kst

from kiwoom_monitor.domain.market_data_contract import MarketDataObservation
from kiwoom_monitor.infrastructure.market_data_metadata_codec import market_metadata_storage_values
from kiwoom_monitor.domain.research_contract import (
    OBSERVATION_REVISION_SCHEMA_VERSION, ObservationRevisionSource,
)

MARKET_METADATA_COLUMNS = (
    "dataset_kind,subject,observation_key,effective_at,available_at,venue,unit,"
    "value_kind,completeness,origin,source,candidate_universe"
)
RANK_HEAD_KINDS = frozenset({"ranking", "top20_membership"})


def _market_metadata_upsert_sql(placeholder: str, excluded: str) -> str:
    placeholders = ",".join((placeholder,) * 12)
    return (
        f"INSERT INTO central_market_data_observation_meta({MARKET_METADATA_COLUMNS}) VALUES({placeholders}) "
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
        f"candidate_universe={excluded}.candidate_universe,revision_id=NULL"
    )
    if distinct_operator:
        semantic_columns = (
            "effective_at", "venue", "unit", "value_kind", "completeness",
            "origin", "source", "candidate_universe",
        )
        sql += " WHERE central_market_data_observation_meta.revision_id IS NOT NULL OR " + " OR ".join(
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
    *, now_provider: Callable[[], datetime] | None = None,
) -> None:
    source = ObservationRevisionSource.from_observation(kind, subject, observation_key, payload, observation)
    _resolve_observation_revision(connection, source, postgres=False, now_provider=now_provider)


def _append_postgres_observation_revision(
    cursor: Any,
    kind: str,
    subject: str,
    observation_key: str,
    payload: dict[str, Any],
    observation: MarketDataObservation[object],
    *, now_provider: Callable[[], datetime] | None = None,
) -> bool:
    source = ObservationRevisionSource.from_observation(kind, subject, observation_key, payload, observation)
    _, inserted = _resolve_observation_revision(cursor, source, postgres=True, now_provider=now_provider)
    return inserted


def _resolve_observation_revision(cursor, source, *, postgres, now_provider=None):
    """Reuse only the last SAME-SOURCE revision; never global hash lookup."""
    p = "%s" if postgres else "?"
    result = cursor.execute(
        "SELECT revision_id,payload_hash FROM central_observation_revisions "
        f"WHERE kind={p} AND subject={p} AND observation_key={p} AND source_id={p} "
        "ORDER BY accepted_sequence DESC LIMIT 1",
        (source.kind, source.subject, source.observation_key, source.source_id),
    )
    latest = cursor.fetchone() if postgres else result.fetchone()
    if latest is not None and str(latest[1]) == source.payload_hash:
        return str(latest[0]), False
    if postgres:
        revision_id = _insert_postgres_observation_revision(cursor, source, latest, now_provider=now_provider)
    else:
        values = _observation_revision_values(source, latest, sqlite=True, now_provider=now_provider)
        cursor.execute(
            "INSERT INTO central_observation_revisions("
            "revision_id,observation_key,schema_version,source_id,source_session_id,source_sequence,"
            "kind,subject,venue,effective_at,received_at,available_at,revision_of,payload_hash,unit,"
            "value_kind,completeness,origin,candidate_universe,quality_flags_json,clock_quality,"
            "source_ref_json,payload_json) VALUES(" + ",".join(("?",) * 23) + ")", values,
        )
        revision_id = str(values[0])
    return revision_id, True


def _rank_head_lock_keys(values):
    """Raw head, metadata collision and normalized source dedup domains."""
    keys = set()
    for kind, subject, key, _, observation in values:
        if kind not in RANK_HEAD_KINDS:
            continue
        keys.add(("head", str(kind), str(subject), str(key)))
        if observation is not None:
            keys.add(("metadata", str(observation.kind.value), str(observation.subject), str(key).strip()))
            keys.add(("revision", str(kind).strip(), str(subject).strip(), str(key).strip(),
                      str(observation.metadata.source or "unknown")))
    return sorted(keys)


def _lock_rank_head_writes(cursor, values):
    for key in _rank_head_lock_keys(values):
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                       ("rank-current/v1:" + json.dumps(key, ensure_ascii=False, separators=(",", ":")),))


def _lock_rank_metadata_write(cursor, observation_key, observation):
    # Independent metadata writes use the same metadata domain, then native row lock.
    if observation.kind.value == "candidate_set":
        key = ("metadata", str(observation.kind.value), str(observation.subject), str(observation_key).strip())
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                       ("rank-current/v1:" + json.dumps(key, ensure_ascii=False, separators=(",", ":")),))


def _rank_slot_order_key(raw_key):
    """Pure projection; naive historical keys use the existing explicit KST boundary."""
    try:
        return as_kst(datetime.fromisoformat(str(raw_key).replace("Z", "+00:00"))).isoformat(timespec="microseconds")
    except ValueError:
        return None


def _save_rank_current(cursor, *, postgres, kind, subject, key, payload, observation, saved_at, now_provider):
    metadata = market_metadata_storage_values(key, observation)
    source = ObservationRevisionSource.from_observation(kind, subject, key, payload, observation)
    revision_id, _ = _resolve_observation_revision(cursor, source, postgres=postgres, now_provider=now_provider)
    p = "%s" if postgres else "?"
    cursor.execute(
        "INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json,revision_id,slot_order_key) "
        f"VALUES({p},{p},{p},{p},NULL,{p},{p}) ON CONFLICT(kind,subject,snapshot_key) DO UPDATE SET "
        "saved_at=excluded.saved_at,payload_json=NULL,revision_id=excluded.revision_id,slot_order_key=excluded.slot_order_key",
        (kind, subject, key, saved_at, revision_id, _rank_slot_order_key(key)),
    )
    # A blank/raw non-string source cannot be reconstructed from normalized source_id.
    # Keep ONE inline metadata value in that case; payload may still reference its fact.
    if (observation.kind.value != "candidate_set" or not isinstance(observation.metadata.source, str)
            or not observation.metadata.source):
        cursor.execute(_market_metadata_upsert_sql(p, "excluded"), metadata)
        return
    values = (*metadata[:5], *(None,) * 7, revision_id)
    cursor.execute(
        f"INSERT INTO central_market_data_observation_meta({MARKET_METADATA_COLUMNS},revision_id) "
        "VALUES(" + ",".join((p,) * 13) + ") "
        "ON CONFLICT(dataset_kind,subject,observation_key) DO UPDATE SET "
        "effective_at=excluded.effective_at,available_at=excluded.available_at,"
        "venue=NULL,unit=NULL,value_kind=NULL,completeness=NULL,origin=NULL,source=NULL,"
        "candidate_universe=NULL,revision_id=excluded.revision_id", values,
    )


def _validate_rank_head_reference(head, revision):
    # Raw head keys remain raw; normalization belongs only to immutable source identity.
    if revision[0] is None or tuple(str(part).strip() for part in head) != tuple(revision[1:4]):
        raise ValueError("rank current reference does not identify its source fact")


def _metadata_projection(alias="m", revision="r"):
    fields = [f"{alias}.effective_at", f"{alias}.available_at"]
    for name in ("venue", "unit", "value_kind", "completeness", "origin"):
        fields.append(f"CASE WHEN {alias}.revision_id IS NULL THEN {alias}.{name} ELSE {revision}.{name} END")
    fields.append(f"CASE WHEN {alias}.revision_id IS NULL THEN {alias}.source ELSE {revision}.source_id END")
    fields.append(f"CASE WHEN {alias}.revision_id IS NULL THEN {alias}.candidate_universe ELSE {revision}.candidate_universe END")
    return ",".join(fields)


def _validate_metadata_reference(row):
    if row[9] is not None:
        if (row[10] is None or row[11] not in RANK_HEAD_KINDS
                or str(row[12]) != str(row[14]).strip() or str(row[13]) != str(row[15]).strip()):
            raise ValueError("rank metadata reference does not identify its source fact")
    return tuple(row[:9])


def _insert_postgres_observation_revision(
    cursor: Any, source: ObservationRevisionSource,
    latest: tuple[object, object] | None,
    *, execute_seconds: list[float] | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> str | None:
    if latest is not None and str(latest[1]) == source.payload_hash:
        return None
    values = _observation_revision_values(source, latest, sqlite=False, now_provider=now_provider)
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
    now_provider: Callable[[], datetime] | None = None,
) -> tuple[object, ...]:
    effective_at: object = source.effective_at
    available_at: object = source.available_at
    received_at: object = as_kst(now_provider()) if now_provider else datetime.now(KST)
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
