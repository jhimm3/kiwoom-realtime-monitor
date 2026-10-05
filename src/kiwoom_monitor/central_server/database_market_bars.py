"""SQLite and PostgreSQL persistence for domestic market bars."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from time import monotonic, time
from typing import Any
from zoneinfo import ZoneInfo

from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness, DataValueKind, MarketDataObservation, ObservationOrigin,
)
from kiwoom_monitor.domain.research_contract import ObservationRevisionSource
from kiwoom_monitor.infrastructure.market_data_metadata_codec import market_metadata_storage_values
from kiwoom_monitor.central_server.database_codec import (
    BAR_KEY_COLUMNS, FIVE_MINUTE_BAR_COLUMNS, bar_columns, bar_result_rows,
    bar_value_rows, bounded_limit, five_minute_bar_result_rows,
    five_minute_bar_value_rows, second_trade_bar_value_rows,
)
from kiwoom_monitor.central_server.database_observation_writes import (
    _append_postgres_observation_revision, _append_sqlite_observation_revision,
    _insert_postgres_observation_revision, _market_metadata_upsert_sql,
    _market_metadata_upsert_suffix, _observation_revision_values,
)
from kiwoom_monitor.central_server.market_observations import (
    bar_observation_key, minute_bar_observation, minute_bar_revision_payload,
)
from kiwoom_monitor.central_server.postgres_access import (
    DBWriterContext, _PostgresObservedCursor, _sample_postgres_commit_waits,
    open_observed_connection,
)

logger = logging.getLogger("kiwoom_monitor.central_server.database")

SQLITE_MULTIROW_UPSERT_ROWS = 80
POSTGRES_MULTIROW_UPSERT_ROWS = 1_000
SQLITE_REVISION_BATCH_ROWS = 40
SQLITE_REVISION_LOOKUP_ROWS = 80



def _minute_key(value: dict[str, Any]) -> str:
    return f"{value['trading_date']}T{value['minute']}"

def _minute_observation_key(value: dict[str, Any]) -> tuple[str, str]:
    return f"{value['code']}:{value.get('market', '') or 'UNKNOWN'}", _minute_key(value)

def _minute_operation(
    value: dict[str, Any], *, finalization: bool = False,
) -> tuple[str, str]:
    operation_id = str(value.get("operation_id") or uuid.uuid4()).strip()
    if not operation_id:
        raise ValueError("minute bar operation_id is required")
    names = (
        ("trading_date", "minute", "code", "market", "available_at", "capture_quality", "finalization_source")
        if finalization else bar_columns(minute=True)
    )
    canonical = {name: value[name] for name in names}
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return operation_id, hashlib.sha256(encoded).hexdigest()

def _load_sqlite_minute_bar(
    connection: sqlite3.Connection, value: dict[str, Any],
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT trading_date,minute,code,market,open,high,low,close,volume,"
        "trade_value_million_won,updated_at FROM central_minute_bars "
        "WHERE trading_date=? AND minute=? AND code=? AND market=?",
        tuple(value[name] for name in ("trading_date", "minute", "code", "market")),
    ).fetchone()
    return bar_result_rows((row,), minute=True)[0] if row is not None else None

def _load_postgres_minute_bar(cursor: Any, value: dict[str, Any]) -> dict[str, Any] | None:
    cursor.execute(
        "SELECT trading_date::text,to_char(minute,'HH24:MI'),code,market,open,high,low,close,"
        "volume,trade_value_million_won,updated_at FROM central_minute_bars "
        "WHERE trading_date=%s AND minute=%s AND code=%s AND market=%s",
        tuple(value[name] for name in ("trading_date", "minute", "code", "market")),
    )
    row = cursor.fetchone()
    return bar_result_rows((row,), minute=True)[0] if row is not None else None

def _final_minute_observation(
    merged: dict[str, Any], closure: dict[str, Any],
) -> MarketDataObservation[dict[str, Any]]:
    capture_quality = str(closure.get("capture_quality", ""))
    if capture_quality not in {"complete", "partial"}:
        raise ValueError("minute bar capture_quality must be complete or partial")
    bar_start = datetime.fromisoformat(
        f"{merged['trading_date']}T{merged['minute']}"
    ).replace(tzinfo=ZoneInfo("Asia/Seoul"))
    available_at = datetime.fromtimestamp(
        float(closure["available_at"]), tz=ZoneInfo("Asia/Seoul")
    )
    if available_at < bar_start.replace(second=0, microsecond=0) + timedelta(minutes=1):
        raise ValueError("minute bar cannot be finalized before bar_end")
    available_value = dict(merged)
    available_value["updated_at"] = float(closure["available_at"])
    complete = capture_quality == "complete"
    return minute_bar_observation(
        available_value,
        origin=ObservationOrigin.REALTIME,
        completeness=DataCompleteness.COMPLETE if complete else DataCompleteness.PARTIAL,
        source="kiwoom-websocket-0B",
        value_kind=DataValueKind.ACTUAL,
    )

def _save_final_minute_revision_sqlite(
    connection: sqlite3.Connection, merged: dict[str, Any], closure: dict[str, Any],
    history_enabled: bool,
) -> None:
    observation = _final_minute_observation(merged, closure)
    key = bar_observation_key(observation)
    connection.execute(
        _market_metadata_upsert_sql("?", "excluded"),
        market_metadata_storage_values(key, observation),
    )
    if history_enabled:
        _append_sqlite_observation_revision(
            connection, "minute_bar", observation.subject, key,
            minute_bar_revision_payload(
                merged, window_closed=True,
                capture_quality=str(closure["capture_quality"]),
                finalization_source=str(closure["finalization_source"]),
                operation_id=str(closure["operation_id"]),
            ),
            observation,
        )

def _save_final_minute_revision_postgres(
    cursor: Any, merged: dict[str, Any], closure: dict[str, Any], history_enabled: bool,
) -> None:
    observation = _final_minute_observation(merged, closure)
    key = bar_observation_key(observation)
    cursor.execute(
        _market_metadata_upsert_sql("%s", "EXCLUDED"),
        market_metadata_storage_values(key, observation),
    )
    if history_enabled:
        _append_postgres_observation_revision(
            cursor, "minute_bar", observation.subject, key,
            minute_bar_revision_payload(
                merged, window_closed=True,
                capture_quality=str(closure["capture_quality"]),
                finalization_source=str(closure["finalization_source"]),
                operation_id=str(closure["operation_id"]),
            ),
            observation,
        )

def _load_sqlite_latest_revisions(
    connection: sqlite3.Connection,
    sources: list[ObservationRevisionSource],
) -> dict[tuple[str, str, str], tuple[object, object] | None]:
    """Load the current revision for each minute key using bounded queries."""
    keys = sorted({(source.kind, source.subject, source.observation_key, source.source_id)
                   for source in sources})
    latest_by_key: dict[tuple[str, str, str], tuple[object, object] | None] = {
        (kind, subject, observation_key, source_id)[1:]: None
        for kind, subject, observation_key, source_id in keys
    }
    for offset in range(0, len(keys), SQLITE_REVISION_LOOKUP_ROWS):
        batch = keys[offset:offset + SQLITE_REVISION_LOOKUP_ROWS]
        value_group = "(" + ",".join("?" for _ in range(4)) + ")"
        requested = ",".join(value_group for _ in batch)
        parameters = tuple(value for key in batch for value in key)
        rows = connection.execute(
            "WITH requested(kind,subject,observation_key,source_id) AS (VALUES "
            + requested + ") "
            "SELECT q.kind,q.subject,q.observation_key,q.source_id,r.revision_id,r.payload_hash "
            "FROM requested q LEFT JOIN central_observation_revisions r "
            "ON r.kind=q.kind AND r.subject=q.subject AND r.observation_key=q.observation_key "
            "AND r.source_id=q.source_id AND r.accepted_sequence=("
            "SELECT MAX(latest.accepted_sequence) FROM central_observation_revisions latest "
            "WHERE latest.kind=q.kind AND latest.subject=q.subject "
            "AND latest.observation_key=q.observation_key AND latest.source_id=q.source_id)",
            parameters,
        ).fetchall()
        for kind, subject, observation_key, source_id, revision_id, payload_hash in rows:
            latest_by_key[(str(subject), str(observation_key), str(source_id))] = (
                (revision_id, payload_hash) if revision_id is not None else None
            )
    return latest_by_key

def _insert_sqlite_observation_revisions_batch(
    connection: sqlite3.Connection,
    sources: list[ObservationRevisionSource],
    latest_by_key: dict[tuple[str, str, str], tuple[object, object] | None],
) -> tuple[int, int]:
    """Insert changed revisions in bounded statements, preserving input chains."""
    columns = (
        "accepted_sequence,revision_id,observation_key,schema_version,source_id,source_session_id,"
        "source_sequence,kind,subject,venue,effective_at,received_at,available_at,revision_of,"
        "payload_hash,unit,value_kind,completeness,origin,candidate_universe,quality_flags_json,"
        "clock_quality,source_ref_json,payload_json"
    )
    column_count = 24
    pending: list[tuple[object, ...]] = []
    statements = inserted_rows = 0

    def flush() -> None:
        nonlocal statements, inserted_rows
        if not pending:
            return
        sequence_row = connection.execute(
            "SELECT COALESCE((SELECT seq FROM sqlite_sequence "
            "WHERE name='central_observation_revisions'),0),"
            "COALESCE((SELECT MAX(accepted_sequence) FROM central_observation_revisions),0)"
        ).fetchone()
        next_sequence = max(int(sequence_row[0]), int(sequence_row[1])) + 1
        rows = [tuple((next_sequence + index, *row))
                for index, row in enumerate(pending)]
        placeholders = "(" + ",".join("?" for _ in range(column_count)) + ")"
        sql = (
            "INSERT INTO central_observation_revisions(" + columns + ") VALUES "
            + ",".join(placeholders for _ in rows)
        )
        connection.execute(sql, tuple(value for row in rows for value in row))
        statements += 1
        inserted_rows += len(rows)
        pending.clear()

    for source in sources:
        key = (source.subject, source.observation_key, source.source_id)
        latest = latest_by_key[key]
        if latest is not None and str(latest[1]) == source.payload_hash:
            continue
        pending.append(_observation_revision_values(source, latest, sqlite=True))
        latest_by_key[key] = (str(pending[-1][0]), source.payload_hash)
        if len(pending) >= SQLITE_REVISION_BATCH_ROWS:
            flush()
    flush()
    return statements, inserted_rows

def _minute_query_authority(cursor: Any, value: dict[str, Any], *, postgres: bool) -> str:
    """Return the ka10080 ownership state for this minute's canonical bar."""
    placeholder = "%s" if postgres else "?"
    result = cursor.execute(
        "SELECT origin,source,completeness FROM central_market_data_observation_meta "
        f"WHERE dataset_kind='minute_bar' AND subject={placeholder} "
        f"AND observation_key={placeholder}",
        (f"{value['code']}:{value['market']}", _minute_key(value)),
    )
    row = result.fetchone()
    if row and str(row[0]) == ObservationOrigin.QUERY.value and str(row[1]).startswith("kiwoom-ka10080"):
        return str(row[2])
    return ""

def _load_postgres_minute_operation_hashes(
    cursor: Any, operation_ids: list[str],
) -> dict[str, str]:
    """Read the existing idempotency records for a realtime flush in one query."""
    if not operation_ids:
        return {}
    cursor.execute(
        "SELECT operation_id,operation_hash FROM central_minute_bar_operations "
        "WHERE operation_id=ANY(%s)",
        (operation_ids,),
    )
    return {str(operation_id): str(operation_hash)
            for operation_id, operation_hash in cursor.fetchall()}

def _load_postgres_minute_query_authorities(
    cursor: Any, keys: list[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    """Read finalized/query-owned minute keys for a realtime flush in one query."""
    if not keys:
        return {}
    subjects, observation_keys = zip(*keys)
    cursor.execute(
        "SELECT meta.subject,meta.observation_key,meta.origin,meta.source,meta.completeness "
        "FROM central_market_data_observation_meta AS meta "
        "JOIN unnest(%s::text[],%s::text[]) AS requested(subject,observation_key) "
        "ON requested.subject=meta.subject AND requested.observation_key=meta.observation_key "
        "WHERE meta.dataset_kind='minute_bar'",
        (list(subjects), list(observation_keys)),
    )
    authorities: dict[tuple[str, str], str] = {}
    for subject, observation_key, origin, source, completeness in cursor.fetchall():
        if (str(origin) == ObservationOrigin.QUERY.value
                and str(source).startswith("kiwoom-ka10080")):
            authorities[(str(subject), str(observation_key))] = str(completeness)
    return authorities

def _lock_postgres_minute_day_scopes(cursor: Any, values: list[dict[str, Any]]) -> None:
    """Serialize query replacement and late 0B writes for the same stock/day."""
    scopes = sorted({(str(value["trading_date"]), str(value["code"]), str(value.get("market", "KRX")))
                     for value in values})
    for scope in scopes:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,1))",
            (json.dumps(scope, ensure_ascii=False, separators=(",", ":")),),
        )


def _read_realtime_minute_overlay(
    cursor: Any, bars: list[dict[str, Any]], deltas: list[dict[str, Any]], *, postgres: bool,
) -> list[dict[str, Any]]:
    """Use the caller's read snapshot to exclude committed or superseded deltas.

    An open RAM segment already has its future operation ID. If that segment
    commits while this read starts, its marker and canonical row are either
    both visible or both absent, including a lost COMMIT acknowledgement.
    """
    operation_ids = list(dict.fromkeys(str(value["operation_id"]) for value in deltas))
    keys = list(dict.fromkeys((f"{value['code']}:{value['market']}", _minute_key(value)) for value in deltas))
    if postgres:
        applied = set(_load_postgres_minute_operation_hashes(cursor, operation_ids))
        authorities = _load_postgres_minute_query_authorities(cursor, keys)
    else:
        marks = ','.join('?' for _ in operation_ids)
        applied = {str(row[0]) for row in cursor.execute(
            f"SELECT operation_id FROM central_minute_bar_operations WHERE operation_id IN ({marks})",
            operation_ids,
        ).fetchall()}
        subjects = list(dict.fromkeys(key[0] for key in keys))
        observation_keys = list(dict.fromkeys(key[1] for key in keys))
        subject_marks = ','.join('?' for _ in subjects)
        key_marks = ','.join('?' for _ in observation_keys)
        metadata = cursor.execute(
            "SELECT subject,observation_key,origin,source,completeness "
            "FROM central_market_data_observation_meta WHERE dataset_kind='minute_bar' "
            f"AND subject IN ({subject_marks}) AND observation_key IN ({key_marks})",
            [*subjects, *observation_keys],
        ).fetchall()
        authorities = {
            (str(subject), str(key)): str(completeness)
            for subject, key, origin, source, completeness in metadata
            if str(origin) == ObservationOrigin.QUERY.value and str(source).startswith('kiwoom-ka10080')
        }
    indexed = {(str(row['minute']), str(row['market'])): dict(row) for row in bars}
    seen = set(applied)
    for delta in deltas:
        operation_id = str(delta['operation_id'])
        if operation_id in seen:
            continue
        seen.add(operation_id)
        authority_key = (f"{delta['code']}:{delta['market']}", _minute_key(delta))
        authority = authorities.get(authority_key, '')
        if authority == DataCompleteness.COMPLETE.value:
            continue
        key = (str(delta['minute']), str(delta['market']))
        previous = indexed.get(key)
        value = {column: delta[column] for column in bar_columns(minute=True)}
        if previous is not None and authority != DataCompleteness.IN_PROGRESS.value:
            value.update(
                open=previous['open'], high=max(int(previous['high']), int(delta['high'])),
                low=min(int(previous['low']), int(delta['low'])),
                volume=int(previous['volume']) + int(delta['volume']),
                trade_value_million_won=int(previous['trade_value_million_won']) + int(delta['trade_value_million_won']),
            )
        indexed[key] = value
        # The first pending realtime operation replaces an in-progress query;
        # later operations add to that replacement, just as the writer does.
        authorities.pop(authority_key, None)
    return [indexed[key] for key in sorted(indexed)]

def _load_postgres_latest_revisions(
    cursor: Any, sources: list[ObservationRevisionSource], *,
    timings: dict[str, float] | None = None,
) -> dict[tuple[str, str, str], tuple[object, object] | None]:
    """Read one latest revision per logical key, holding each source/subject writer scope.

    Query-response pages for a stock use one scope. Sorting the scopes keeps
    overlapping multi-stock batches from taking transaction locks out of order.
    The realtime writer has a different source ID and remains independent.
    """
    keys = sorted({(source.subject, source.observation_key, source.source_id)
                   for source in sources})
    lock_started = monotonic()
    for kind, subject, source_id in sorted({(source.kind, source.subject, source.source_id)
                                           for source in sources}):
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (json.dumps((kind, subject, source_id), ensure_ascii=False, separators=(",", ":")),),
        )
    if timings is not None:
        timings["lock_seconds"] = monotonic() - lock_started
    lookup_started = monotonic()
    cursor.execute(
        "SELECT k.subject,k.observation_key,k.source_id,r.revision_id,r.payload_hash "
        "FROM unnest(%s::text[],%s::text[],%s::text[]) "
        "AS k(subject,observation_key,source_id) "
        "LEFT JOIN LATERAL ("
        "SELECT revision_id,payload_hash FROM central_observation_revisions "
        "WHERE kind='minute_bar' AND subject=k.subject "
        "AND observation_key=k.observation_key AND source_id=k.source_id "
        "ORDER BY accepted_sequence DESC LIMIT 1"
        ") AS r ON true",
        ([key[0] for key in keys], [key[1] for key in keys], [key[2] for key in keys]),
    )
    latest_by_key = {key: None for key in keys}
    for subject, observation_key, source_id, revision_id, payload_hash in cursor.fetchall():
        latest_by_key[(subject, observation_key, source_id)] = (
            (revision_id, payload_hash) if revision_id is not None else None
        )
    if timings is not None:
        timings["lookup_seconds"] = monotonic() - lookup_started
    return latest_by_key

def _insert_postgres_observation_revisions_batch(
    cursor: Any,
    sources: list[ObservationRevisionSource],
    latest_by_key: dict[tuple[str, str, str], tuple[object, object] | None],
    *,
    execute_seconds: list[float] | None = None,
) -> tuple[int, int]:
    """Insert changed revisions in bounded multi-row statements, preserving input order.

    Revision IDs are allocated before each batch is sent so repeated logical keys
    can link to the immediately preceding input observation. Sequence values are
    also assigned in input order; a multi-row VALUES statement alone does not
    establish the accepted_sequence ordering used by latest-revision queries.
    Advisory locks are acquired by the caller, and canonical bars, metadata,
    and history remain in the same transaction.
    """
    columns = (
        "revision_id,observation_key,schema_version,source_id,source_session_id,source_sequence,"
        "kind,subject,venue,effective_at,received_at,available_at,revision_of,payload_hash,unit,"
        "value_kind,completeness,origin,candidate_universe,quality_flags_json,clock_quality,"
        "source_ref_json,payload_json"
    )
    column_count = 24
    batch_size = 1000  # 24,000 bind parameters, below PostgreSQL's 65,535 limit.
    pending: list[tuple[object, ...]] = []
    statements = inserted_rows = 0
    execute_elapsed = 0.0

    def flush() -> None:
        nonlocal statements, inserted_rows, execute_elapsed
        if not pending:
            return
        cursor.execute(
            "SELECT nextval(pg_get_serial_sequence("
            "'central_observation_revisions','accepted_sequence')) "
            "FROM generate_series(1,%s) ORDER BY 1",
            (len(pending),),
        )
        sequences = [int(row[0]) for row in cursor.fetchall()]
        if len(sequences) != len(pending) or any(
            current <= previous for previous, current in zip(sequences, sequences[1:])
        ):
            raise RuntimeError("observation revision sequence allocation was incomplete or unordered")
        row_placeholders = "(" + ",".join(("%s",) * column_count) + ")"
        sql = (
            "INSERT INTO central_observation_revisions(accepted_sequence," + columns + ") VALUES "
            + ",".join(row_placeholders for _ in pending)
        )
        parameters = tuple(value for sequence, row in zip(sequences, pending)
                           for value in (sequence, *row))
        started = monotonic()
        cursor.execute(sql, parameters)
        execute_elapsed += monotonic() - started
        statements += 1
        inserted_rows += len(pending)
        pending.clear()

    for source in sources:
        key = (source.subject, source.observation_key, source.source_id)
        latest = latest_by_key[key]
        if latest is not None and str(latest[1]) == source.payload_hash:
            continue
        values = _observation_revision_values(source, latest, sqlite=False)
        pending.append(values)
        latest_by_key[key] = (str(values[0]), source.payload_hash)
        if len(pending) >= batch_size:
            flush()
    flush()
    if execute_seconds is not None:
        execute_seconds[0] += execute_elapsed
    return statements, inserted_rows

def _partition_rows_by_last_key(
    rows: list[tuple[Any, ...]], key_indexes: tuple[int, ...],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """Separate earlier duplicate rows from each key's final occurrence."""
    seen: set[tuple[Any, ...]] = set()
    earlier: list[tuple[Any, ...]] = []
    latest: list[tuple[Any, ...]] = []
    for row in reversed(rows):
        key = tuple(row[index] for index in key_indexes)
        if key in seen:
            earlier.append(row)
            continue
        seen.add(key)
        latest.append(row)
    earlier.reverse()
    latest.reverse()
    return earlier, latest

def _last_rows_by_key(
    rows: list[tuple[Any, ...]], key_indexes: tuple[int, ...],
) -> list[tuple[Any, ...]]:
    """Keep the final metadata observation for each key."""
    return _partition_rows_by_last_key(rows, key_indexes)[1]

def _execute_multirow_upsert(
    executor: Any,
    insert_prefix: str,
    rows: list[tuple[Any, ...]],
    upsert_suffix: str,
    *,
    placeholder: str,
    batch_size: int,
    returning_columns: str = "",
    returned_rows: list[tuple[Any, ...]] | None = None,
) -> int:
    """Execute a bounded multi-row UPSERT and return its statement count."""
    if not rows:
        return 0
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    width = len(rows[0])
    if width <= 0 or any(len(row) != width for row in rows):
        raise ValueError("multi-row UPSERT rows must have one non-empty width")
    value_group = "(" + ",".join((placeholder,) * width) + ")"
    statements = 0
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset:offset + batch_size]
        sql = f"{insert_prefix}{','.join(value_group for _ in batch)} {upsert_suffix}"
        if returning_columns:
            sql += f" RETURNING {returning_columns}"
        parameters = tuple(value for row in batch for value in row)
        result = executor.execute(sql, parameters)
        if returned_rows is not None:
            returned_rows.extend(result.fetchall())
        statements += 1
    return statements

def _bar_metadata_key(row: tuple[Any, ...], *, minute: bool) -> tuple[str, str, str]:
    if minute:
        day, clock, code, market = row
        observation_key = f"{day}T{clock}"
        kind = "minute_bar"
    else:
        day, code, market = row
        observation_key = str(day)
        kind = "daily_bar"
    return kind, f"{code}:{market or 'UNKNOWN'}", observation_key

def _save_sqlite_metadata(
    connection, observations, *, multirow: bool = False,
    changed_bar_keys: set[tuple[str, str, str]] | None = None,
) -> None:
    if not observations:
        return
    rows = [market_metadata_storage_values(key, observation) for key, observation in observations]
    if multirow:
        final_rows = _last_rows_by_key(rows, (0, 1, 2))
        groups = (
            [(final_rows, "")]
            if changed_bar_keys is None else [
                ([row for row in final_rows if (row[0], row[1], row[2]) in changed_bar_keys], ""),
                ([row for row in final_rows if (row[0], row[1], row[2]) not in changed_bar_keys], "IS NOT"),
            ]
        )
        for selected, distinct_operator in groups:
            if not selected:
                continue
            _execute_multirow_upsert(
                connection,
                "INSERT INTO central_market_data_observation_meta VALUES",
                selected,
                _market_metadata_upsert_suffix(
                    "excluded", distinct_operator=distinct_operator,
                ),
                placeholder="?", batch_size=SQLITE_MULTIROW_UPSERT_ROWS,
            )
    else:
        connection.executemany(_market_metadata_upsert_sql("?", "excluded"), rows)

def _save_postgres_metadata(
    cursor, observations, *, multirow: bool = False,
    changed_bar_keys: set[tuple[str, str, str]] | None = None,
) -> None:
    if not observations:
        return
    rows = [market_metadata_storage_values(key, observation) for key, observation in observations]
    if multirow:
        final_rows = _last_rows_by_key(rows, (0, 1, 2))
        groups = (
            [(final_rows, "")]
            if changed_bar_keys is None else [
                ([row for row in final_rows if (row[0], row[1], row[2]) in changed_bar_keys], ""),
                ([row for row in final_rows if (row[0], row[1], row[2]) not in changed_bar_keys], "IS DISTINCT FROM"),
            ]
        )
        for selected, distinct_operator in groups:
            if not selected:
                continue
            _execute_multirow_upsert(
                cursor,
                "INSERT INTO central_market_data_observation_meta VALUES",
                selected,
                _market_metadata_upsert_suffix(
                    "EXCLUDED", distinct_operator=distinct_operator,
                ),
                placeholder="%s", batch_size=POSTGRES_MULTIROW_UPSERT_ROWS,
            )
    else:
        cursor.executemany(_market_metadata_upsert_sql("%s", "EXCLUDED"), rows)



class SQLiteMarketBarStoreMixin:


    def save_second_trade_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_second_trade_bars VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(trading_date,trade_second,code,market) DO UPDATE SET "
                "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
                "volume=excluded.volume,trade_value_won=excluded.trade_value_won,"
                "trade_count=excluded.trade_count,available_at=excluded.available_at "
                "WHERE excluded.available_at>central_second_trade_bars.available_at OR "
                "(excluded.available_at=central_second_trade_bars.available_at AND "
                "excluded.trade_count>=central_second_trade_bars.trade_count)",
                second_trade_bar_value_rows(values),
            )


    def load_minute_bars(
        self, code: str, trading_date: str, market: str = "", *,
        realtime_deltas: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        realtime_deltas = [value for value in (realtime_deltas or [])
                           if value['code'] == code and value['trading_date'] == trading_date
                           and (not market or value['market'] == market)]
        sql = (
            "SELECT trading_date,minute,code,market,open,high,low,close,volume,trade_value_million_won,updated_at "
            "FROM central_minute_bars WHERE code=? AND trading_date=?"
        )
        parameters: list[object] = [code, trading_date]
        if market:
            sql += " AND market=?"
            parameters.append(market)
        sql += " ORDER BY minute"
        with self._lock, self._connection() as connection:
            if realtime_deltas:
                connection.execute("BEGIN")
            rows = connection.execute(sql, parameters).fetchall()
            if realtime_deltas:
                return _read_realtime_minute_overlay(
                    connection, bar_result_rows(rows, minute=True), realtime_deltas, postgres=False,
                )
        return bar_result_rows(rows, minute=True)


    def replace_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        self._replace_bars(
            "central_minute_bars", values, minute=True, observations=observations
        )


    def load_five_minute_bars(self, code: str, trading_date: str, adjustment_mode: str = "adjusted") -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT " + ",".join(FIVE_MINUTE_BAR_COLUMNS) + " FROM central_five_minute_bars "
                "WHERE code=? AND trading_date=? AND adjustment_mode=? ORDER BY minute,provider",
                (code, trading_date, adjustment_mode),
            ).fetchall()
        return five_minute_bar_result_rows(rows)


    def save_five_minute_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_five_minute_bars("
                + ",".join(FIVE_MINUTE_BAR_COLUMNS) + ") VALUES(" + ",".join("?" for _ in FIVE_MINUTE_BAR_COLUMNS) + ") "
                "ON CONFLICT(trading_date,minute,code,market,provider,adjustment_mode) DO UPDATE SET "
                "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
                "volume=excluded.volume,trading_value_raw=excluded.trading_value_raw,"
                "observed_at=excluded.observed_at",
                five_minute_bar_value_rows(values),
            )


    def finalize_minute_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for closure in values:
                operation_id, operation_hash = _minute_operation(closure, finalization=True)
                processed = connection.execute(
                    "SELECT operation_hash FROM central_minute_bar_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if processed is not None:
                    if str(processed[0]) != operation_hash:
                        raise ValueError("minute bar operation_id payload changed")
                    continue
                merged = _load_sqlite_minute_bar(connection, closure)
                if merged is not None and _minute_query_authority(connection, closure, postgres=False) != DataCompleteness.COMPLETE.value:
                    _save_final_minute_revision_sqlite(
                        connection, merged, closure, self._observation_history_enabled,
                    )
                connection.execute(
                    "INSERT INTO central_minute_bar_operations(operation_id,operation_hash,processed_at) "
                    "VALUES(?,?,?)",
                    (operation_id, operation_hash, datetime.now(timezone.utc).isoformat()),
                )


    def replace_daily_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        self._replace_bars(
            "central_daily_bars", values, minute=False, observations=observations
        )


    def load_daily_bars(self, code: str, market: str = "", limit: int = 250) -> list[dict[str, Any]]:
        sql = ("SELECT trading_date,code,market,open,high,low,close,volume,trade_value_million_won,updated_at "
               "FROM central_daily_bars WHERE code=?")
        parameters: list[object] = [code]
        if market:
            sql += " AND market=?"
            parameters.append(market)
        sql += " ORDER BY trading_date DESC LIMIT ?"
        parameters.append(bounded_limit(limit, 5000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return bar_result_rows(rows, minute=False)


    def save_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        if not values:
            return
        observation_by_key = {
            (observation.subject, key): observation for key, observation in observations or ()
        }
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for value in values:
                operation_id, operation_hash = _minute_operation(value)
                processed = connection.execute(
                    "SELECT operation_hash FROM central_minute_bar_operations WHERE operation_id=?",
                    (operation_id,),
                ).fetchone()
                if processed is not None:
                    if str(processed[0]) != operation_hash:
                        raise ValueError("minute bar operation_id payload changed")
                    continue
                query_authority = _minute_query_authority(connection, value, postgres=False)
                if query_authority != DataCompleteness.COMPLETE.value:
                    if query_authority == DataCompleteness.IN_PROGRESS.value:
                        updates = ",".join(
                            f"{column}=excluded.{column}" for column in bar_columns(minute=True)
                            if column not in BAR_KEY_COLUMNS
                        )
                    else:
                        updates = (
                            "high=MAX(central_minute_bars.high,excluded.high),"
                            "low=MIN(central_minute_bars.low,excluded.low),close=excluded.close,"
                            "volume=central_minute_bars.volume+excluded.volume,"
                            "trade_value_million_won=central_minute_bars.trade_value_million_won+excluded.trade_value_million_won,"
                            "updated_at=excluded.updated_at"
                        )
                    connection.execute(
                        "INSERT INTO central_minute_bars VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(trading_date,minute,code,market) DO UPDATE SET " + updates,
                        bar_value_rows((value,), minute=True)[0],
                    )
                key = _minute_key(value)
                observation = observation_by_key.get(_minute_observation_key(value))
                if observation is not None and query_authority != DataCompleteness.COMPLETE.value:
                    merged = _load_sqlite_minute_bar(connection, value)
                    merged_observation = MarketDataObservation(
                        observation.kind, observation.subject, merged, observation.metadata,
                    )
                    connection.execute(
                        _market_metadata_upsert_sql("?", "excluded"),
                        market_metadata_storage_values(key, merged_observation),
                    )
                    if self._observation_history_enabled:
                        _append_sqlite_observation_revision(
                            connection, "minute_bar", observation.subject, key,
                            minute_bar_revision_payload(
                                merged, window_closed=False, capture_quality="in_progress",
                                finalization_source="realtime_flush", operation_id=operation_id,
                            ),
                            merged_observation,
                        )
                connection.execute(
                    "INSERT INTO central_minute_bar_operations(operation_id,operation_hash,processed_at) "
                    "VALUES(?,?,?)",
                    (operation_id, operation_hash, datetime.now(timezone.utc).isoformat()),
                )


    def _replace_bars(
        self, table: str, values: list[dict[str, Any]], *, minute: bool,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        if not values:
            return
        columns = bar_columns(minute=minute)
        updates = ",".join(f"{column}=excluded.{column}" for column in columns if column not in BAR_KEY_COLUMNS)
        conflict = "trading_date,minute,code,market" if minute else "trading_date,code,market"
        changed_columns = tuple(
            column for column in columns
            if column not in BAR_KEY_COLUMNS and column != "updated_at"
        )
        # Identical OHLCV payloads do not rewrite the canonical row.
        changed_guard = (
            " WHERE " + " OR ".join(
                f"{table}.{column} IS NOT excluded.{column}"
                for column in changed_columns
            )
            if not minute else ""
        )
        with self._lock, self._connection() as connection:
            bar_rows = bar_value_rows(values, minute=minute)
            changed_bar_rows: list[tuple[Any, ...]] = []
            returning = (
                "trading_date,minute,code,market" if minute else "trading_date,code,market"
            ) if observations else ""
            if minute:
                changed_guard = (
                    " WHERE " + " OR ".join(
                        f"{table}.{column} IS NOT excluded.{column}"
                        for column in changed_columns
                    )
                )
            earlier_rows, latest_rows = _partition_rows_by_last_key(
                bar_rows, (0, 1, 2, 3) if minute else (0, 1, 2),
            )
            for row in earlier_rows:
                _execute_multirow_upsert(
                    connection,
                    f"INSERT INTO {table}({','.join(columns)}) VALUES",
                    [row], f"ON CONFLICT({conflict}) DO UPDATE SET {updates}{changed_guard}",
                    placeholder="?", batch_size=1,
                    returning_columns=returning,
                    returned_rows=changed_bar_rows if returning else None,
                )
            _execute_multirow_upsert(
                connection,
                f"INSERT INTO {table}({','.join(columns)}) VALUES",
                latest_rows,
                f"ON CONFLICT({conflict}) DO UPDATE SET {updates}{changed_guard}",
                placeholder="?", batch_size=SQLITE_MULTIROW_UPSERT_ROWS,
                returning_columns=returning,
                returned_rows=changed_bar_rows if returning else None,
            )
            _save_sqlite_metadata(
                connection, observations, multirow=True,
                changed_bar_keys={_bar_metadata_key(row, minute=minute) for row in changed_bar_rows},
            )
            if minute and observations and self._observation_history_enabled:
                revision_sources = [
                    ObservationRevisionSource.from_observation(
                        "minute_bar", observation.subject, key,
                        minute_bar_revision_payload(
                            value,
                            window_closed=observation.metadata.completeness in {
                                DataCompleteness.COMPLETE, DataCompleteness.PARTIAL,
                            },
                            capture_quality=(
                                "complete" if observation.metadata.completeness == DataCompleteness.COMPLETE
                                else observation.metadata.completeness.value
                            ),
                            finalization_source="query_response",
                        ),
                        observation,
                    )
                    for value, (key, observation) in zip(values, observations, strict=True)
                ]
                latest_by_key = _load_sqlite_latest_revisions(connection, revision_sources)
                _insert_sqlite_observation_revisions_batch(
                    connection, revision_sources, latest_by_key,
                )




class PostgresMarketBarStoreMixin:


    def save_second_trade_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        from .postgres_access import DBWriterContext, open_observed_connection

        started_at = monotonic()
        writer = DBWriterContext(
            writer_family="realtime.second_bar", writer_kind="realtime_second_bar",
            operation="save_second_trade_bars", rows_attempted=len(values),
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_second_trade_bars VALUES("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(trading_date,trade_second,code,market) DO UPDATE SET "
                "open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,"
                "volume=EXCLUDED.volume,trade_value_won=EXCLUDED.trade_value_won,"
                "trade_count=EXCLUDED.trade_count,available_at=EXCLUDED.available_at "
                "WHERE EXCLUDED.available_at>central_second_trade_bars.available_at OR "
                "(EXCLUDED.available_at=central_second_trade_bars.available_at AND "
                "EXCLUDED.trade_count>=central_second_trade_bars.trade_count)",
                second_trade_bar_value_rows(values),
            )
        from .diagnostic_metrics import record_writer_transaction
        record_writer_transaction("realtime_second_bar", len(values),
                                  round((monotonic() - started_at) * 1000),
                                  db_call_id=writer.call_id)


    def load_minute_bars(
        self, code: str, trading_date: str, market: str = "", *,
        realtime_deltas: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        realtime_deltas = [value for value in (realtime_deltas or [])
                           if value['code'] == code and value['trading_date'] == trading_date
                           and (not market or value['market'] == market)]
        from .postgres_access import DBWriterContext, open_observed_connection

        sql = (
            "SELECT trading_date::text,to_char(minute,'HH24:MI'),code,market,open,high,low,close,volume,"
            "trade_value_million_won,updated_at FROM central_minute_bars WHERE code=%s AND trading_date=%s"
        )
        parameters: list[object] = [code, trading_date]
        if market:
            sql += " AND market=%s"
            parameters.append(market)
        sql += " ORDER BY minute"
        reader = DBWriterContext(
            writer_family="read.market_bars", writer_kind="minute_bar",
            operation="load_minute_bars", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            if realtime_deltas:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
            if realtime_deltas:
                return _read_realtime_minute_overlay(
                    cursor, bar_result_rows(rows, minute=True), realtime_deltas, postgres=True,
                )
        return bar_result_rows(rows, minute=True)


    def replace_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        self._replace_bars(
            "central_minute_bars", values, minute=True, observations=observations
        )


    def load_five_minute_bars(self, code: str, trading_date: str, adjustment_mode: str = "adjusted") -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT " + ",".join(FIVE_MINUTE_BAR_COLUMNS) + " FROM central_five_minute_bars "
                "WHERE code=%s AND trading_date=%s AND adjustment_mode=%s ORDER BY minute,provider",
                (code, trading_date, adjustment_mode),
            )
            rows = cursor.fetchall()
        return five_minute_bar_result_rows(rows)


    def save_five_minute_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_five_minute_bars("
                + ",".join(FIVE_MINUTE_BAR_COLUMNS) + ") VALUES(" + ",".join("%s" for _ in FIVE_MINUTE_BAR_COLUMNS) + ") "
                "ON CONFLICT(trading_date,minute,code,market,provider,adjustment_mode) DO UPDATE SET "
                "open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,"
                "volume=EXCLUDED.volume,trading_value_raw=EXCLUDED.trading_value_raw,"
                "observed_at=EXCLUDED.observed_at",
                five_minute_bar_value_rows(values),
            )


    def finalize_minute_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        from .postgres_access import DBWriterContext, open_observed_connection

        started_at = monotonic()
        writer = DBWriterContext(
            writer_family="realtime.minute_finalize", writer_kind="realtime_minute_finalize",
            operation="finalize_minute_bars", rows_attempted=len(values),
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            _lock_postgres_minute_day_scopes(cursor, values)
            for closure in values:
                operation_id, operation_hash = _minute_operation(closure, finalization=True)
                cursor.execute(
                    "SELECT operation_hash FROM central_minute_bar_operations WHERE operation_id=%s",
                    (operation_id,),
                )
                processed = cursor.fetchone()
                if processed is not None:
                    if str(processed[0]) != operation_hash:
                        raise ValueError("minute bar operation_id payload changed")
                    continue
                merged = _load_postgres_minute_bar(cursor, closure)
                if merged is not None and _minute_query_authority(cursor, closure, postgres=True) != DataCompleteness.COMPLETE.value:
                    _save_final_minute_revision_postgres(
                        cursor, merged, closure, self._observation_history_enabled,
                    )
                cursor.execute(
                    "INSERT INTO central_minute_bar_operations(operation_id,operation_hash,processed_at) "
                    "VALUES(%s,%s,%s)",
                    (operation_id, operation_hash, datetime.now(timezone.utc)),
                )
        from .diagnostic_metrics import record_writer_transaction
        record_writer_transaction("realtime_minute_finalize", len(values),
                                  round((monotonic() - started_at) * 1000),
                                  db_call_id=writer.call_id)


    def replace_daily_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        self._replace_bars(
            "central_daily_bars", values, minute=False, observations=observations
        )


    def load_daily_bars(self, code: str, market: str = "", limit: int = 250) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        sql = ("SELECT trading_date::text,code,market,open,high,low,close,volume,trade_value_million_won,updated_at "
               "FROM central_daily_bars WHERE code=%s")
        parameters: list[object] = [code]
        if market:
            sql += " AND market=%s"
            parameters.append(market)
        # Avoid resolving the SELECT alias for trading_date::text here;
        # PostgreSQL would sort the cast expression and skip the date index.
        sql += " ORDER BY central_daily_bars.trading_date DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 5000))
        reader = DBWriterContext(
            writer_family="read.market_bars", writer_kind="daily_bar",
            operation="load_daily_bars", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return bar_result_rows(rows, minute=False)


    def save_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        if not values:
            return
        from .postgres_access import DBWriterContext, open_observed_connection

        started_at = monotonic()
        observation_by_key = {
            (observation.subject, key): observation for key, observation in observations or ()
        }
        domain_phase_ms: dict[str, float] = {}
        domain_counts = {"replayed": 0, "query_complete": 0,
                         "bar_upserts": 0, "metadata_upserts": 0,
                         "revision_inserts": 0, "operation_inserts": 0,
                         "operation_lookup_statements": 0,
                         "operation_lookup_keys": 0,
                         "authority_lookup_statements": 0,
                         "authority_lookup_keys": 0}
        writer = DBWriterContext(
            writer_family="realtime.minute", writer_kind="realtime_minute",
            operation="save_minute_bars", rows_attempted=len(values),
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            phase_started = monotonic()
            _lock_postgres_minute_day_scopes(cursor, values)
            domain_phase_ms["day_locks"] = round((monotonic() - phase_started) * 1000, 3)
            operations = [(value, *_minute_operation(value)) for value in values]
            operation_ids = list(dict.fromkeys(
                operation_id for _, operation_id, _ in operations
            ))
            phase_started = monotonic()
            operation_hashes = _load_postgres_minute_operation_hashes(
                cursor, operation_ids,
            )
            domain_phase_ms["operation_lookup"] = round((monotonic() - phase_started) * 1000, 3)
            domain_counts["operation_lookup_statements"] = 1
            domain_counts["operation_lookup_keys"] = len(operation_ids)

            # The realtime accumulator emits one operation per minute key. Keep
            # sequential authority reads for duplicate keys so unusual callers
            # retain the previous read-after-write behavior within this batch.
            key_counts: dict[tuple[str, str], int] = {}
            pending_operation_ids: set[str] = set()
            for value, operation_id, _ in operations:
                if operation_id in operation_hashes or operation_id in pending_operation_ids:
                    continue
                pending_operation_ids.add(operation_id)
                key = (f"{value['code']}:{value['market']}", _minute_key(value))
                key_counts[key] = key_counts.get(key, 0) + 1
            authority_keys = [key for key, count in key_counts.items() if count == 1]
            phase_started = monotonic()
            authorities = _load_postgres_minute_query_authorities(cursor, authority_keys)
            domain_phase_ms["query_authority"] = round((monotonic() - phase_started) * 1000, 3)
            domain_counts["authority_lookup_statements"] = int(bool(authority_keys))
            domain_counts["authority_lookup_keys"] = len(authority_keys)
            seen_operations: dict[str, str] = {}
            for value, operation_id, operation_hash in operations:
                processed_hash = operation_hashes.get(operation_id)
                if processed_hash is not None:
                    if str(processed_hash) != operation_hash:
                        raise ValueError("minute bar operation_id payload changed")
                    domain_counts["replayed"] += 1
                    continue
                earlier_hash = seen_operations.get(operation_id)
                if earlier_hash is not None:
                    if earlier_hash != operation_hash:
                        raise ValueError("minute bar operation_id payload changed")
                    domain_counts["replayed"] += 1
                    continue
                seen_operations[operation_id] = operation_hash
                key = (f"{value['code']}:{value['market']}", _minute_key(value))
                if key_counts.get(key, 0) > 1:
                    phase_started = monotonic()
                    query_authority = _minute_query_authority(cursor, value, postgres=True)
                    domain_phase_ms["query_authority"] += (monotonic() - phase_started) * 1000
                    domain_counts["authority_lookup_statements"] += 1
                    domain_counts["authority_lookup_keys"] += 1
                else:
                    query_authority = authorities.get(key, "")
                merged_bar: dict[str, Any] | None = None
                if query_authority != DataCompleteness.COMPLETE.value:
                    phase_started = monotonic()
                    if query_authority == DataCompleteness.IN_PROGRESS.value:
                        updates = ",".join(
                            f"{column}=EXCLUDED.{column}" for column in bar_columns(minute=True)
                            if column not in BAR_KEY_COLUMNS
                        )
                    else:
                        updates = (
                            "high=GREATEST(central_minute_bars.high,EXCLUDED.high),"
                            "low=LEAST(central_minute_bars.low,EXCLUDED.low),close=EXCLUDED.close,"
                            "volume=central_minute_bars.volume+EXCLUDED.volume,"
                            "trade_value_million_won=central_minute_bars.trade_value_million_won+EXCLUDED.trade_value_million_won,"
                            "updated_at=EXCLUDED.updated_at"
                        )
                    cursor.execute(
                        "INSERT INTO central_minute_bars VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT(trading_date,minute,code,market) DO UPDATE SET " + updates + " "
                        "RETURNING trading_date::text,to_char(minute,'HH24:MI'),code,market,open,high,low,close,"
                        "volume,trade_value_million_won,updated_at",
                        bar_value_rows((value,), minute=True)[0],
                    )
                    merged_bar = bar_result_rows((cursor.fetchone(),), minute=True)[0]
                    domain_phase_ms["bar_upsert"] = domain_phase_ms.get("bar_upsert", 0) + (monotonic() - phase_started) * 1000
                    domain_counts["bar_upserts"] += 1
                else:
                    domain_counts["query_complete"] += 1
                key = _minute_key(value)
                observation = observation_by_key.get(_minute_observation_key(value))
                if observation is not None and query_authority != DataCompleteness.COMPLETE.value:
                    if merged_bar is None:
                        raise RuntimeError("minute bar upsert did not return its saved row")
                    phase_started = monotonic()
                    merged_observation = MarketDataObservation(
                        observation.kind, observation.subject, merged_bar, observation.metadata,
                    )
                    cursor.execute(
                        _market_metadata_upsert_sql("%s", "EXCLUDED"),
                        market_metadata_storage_values(key, merged_observation),
                    )
                    domain_counts["metadata_upserts"] += 1
                    if self._observation_history_enabled:
                        if _append_postgres_observation_revision(
                            cursor, "minute_bar", observation.subject, key,
                            minute_bar_revision_payload(
                                merged_bar, window_closed=False, capture_quality="in_progress",
                                finalization_source="realtime_flush", operation_id=operation_id,
                            ),
                            merged_observation,
                        ):
                            domain_counts["revision_inserts"] += 1
                    domain_phase_ms["metadata_revision"] = domain_phase_ms.get("metadata_revision", 0) + (monotonic() - phase_started) * 1000
                phase_started = monotonic()
                cursor.execute(
                    "INSERT INTO central_minute_bar_operations(operation_id,operation_hash,processed_at) "
                    "VALUES(%s,%s,%s)",
                    (operation_id, operation_hash, datetime.now(timezone.utc)),
                )
                domain_phase_ms["operation_insert"] = domain_phase_ms.get("operation_insert", 0) + (monotonic() - phase_started) * 1000
                domain_counts["operation_inserts"] += 1
        from .diagnostic_metrics import record_writer_transaction
        record_writer_transaction("realtime_minute", len(values),
                                  round((monotonic() - started_at) * 1000),
                                  domain_phase_ms={key: round(value, 3)
                                                   for key, value in domain_phase_ms.items()},
                                  domain_counts=domain_counts,
                                  db_call_id=writer.call_id)


    def _replace_bars(
        self, table: str, values: list[dict[str, Any]], *, minute: bool,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        if not values:
            return
        columns = bar_columns(minute=minute)
        updates = ",".join(f"{column}=EXCLUDED.{column}" for column in columns if column not in BAR_KEY_COLUMNS)
        conflict = "trading_date,minute,code,market" if minute else "trading_date,code,market"
        # A repeated page must not rewrite an unchanged canonical bar. Metadata
        # and minute observation revisions still run below, including finalization.
        changed_columns = tuple(column for column in columns if column not in BAR_KEY_COLUMNS and column != "updated_at")
        changed_guard = (
            " WHERE (" + ",".join(f"{table}.{column}" for column in changed_columns) + ")"
            " IS DISTINCT FROM (" + ",".join(f"EXCLUDED.{column}" for column in changed_columns) + ")"
            if changed_columns else ""
        )
        from .postgres_access import DBWriterContext, open_observed_connection
        from .diagnostic_metrics import CURRENT_API_ID, refresh_capture_state

        writer_kind = "query_minute" if minute else "query_daily"
        writer = DBWriterContext(
            writer_family="rest.market_bars.minute" if minute else "rest.market_bars.daily",
            writer_kind=writer_kind,
            operation="replace_minute_bars" if minute else "replace_daily_bars",
            rows_attempted=len(values), api_id=CURRENT_API_ID.get(),
        )
        total_started = monotonic()
        connect_started = monotonic()
        connection = open_observed_connection(self._connect, writer)
        connect_ms = round((monotonic() - connect_started) * 1000)
        bar_write_ms = metadata_ms = revision_ms = commit_ms = close_ms = 0
        revision_lookup_statements = revision_lookup_keys = revision_insert_statements = 0
        revision_sources_ms = revision_locks_ms = revision_lookup_ms = 0
        revision_rows_ms = revision_insert_execute_ms = 0
        revision_insert_rows = 0
        commit_wait_samples: list[dict[str, object]] = []
        commit_probe_errors: list[str] = []
        commit_probe_stop: Event | None = None
        commit_probe_thread: Thread | None = None
        commit_backend_pid = 0
        commit_started_at: float | None = None
        commit_ended_at: float | None = None
        commit_probe_incomplete = False
        bar_statement_diagnostics: list[dict[str, object]] = []
        metadata_statement_diagnostics: list[dict[str, object]] = []
        capture_enabled = bool(refresh_capture_state().get("enabled"))
        # The diagnostic lease is sampled once per call. Expiry/restart restores
        # the normal write for the next call without changing this transaction.
        from .diagnostic_workloads import is_paused
        metadata_suppressed_rows = (
            len(observations or ()) if minute and is_paused("minute_query_metadata") else 0
        )
        if capture_enabled:
            commit_probe_stop = Event()
        bar_backend_pid = int(getattr(getattr(connection, "info", None), "backend_pid", 0) or 0)
        wal_timing_for_commit: bool | None = None
        wal_timing_error = ""
        try:
            with connection.cursor() as cursor:
                if capture_enabled:
                    try:
                        cursor.execute("SAVEPOINT diagnostic_wal_timing")
                        try:
                            cursor.execute("SET LOCAL track_wal_io_timing TO on")
                            cursor.execute("SHOW track_wal_io_timing")
                            wal_timing_for_commit = str(cursor.fetchone()[0]).lower() == "on"
                        except Exception as error:
                            wal_timing_error = type(error).__name__
                            cursor.execute("ROLLBACK TO SAVEPOINT diagnostic_wal_timing")
                        finally:
                            cursor.execute("RELEASE SAVEPOINT diagnostic_wal_timing")
                    except Exception as error:
                        # No bar write has happened yet. If savepoint recovery
                        # itself failed, clear the aborted diagnostic transaction.
                        wal_timing_error = type(error).__name__
                        connection.rollback()
                observed_cursor = (
                    _PostgresObservedCursor(
                        cursor, self._database_url, bar_backend_pid,
                        True, bar_statement_diagnostics,
                    ) if capture_enabled else cursor
                )
                if minute:
                    _lock_postgres_minute_day_scopes(cursor, values)
                phase_started = monotonic()
                bar_rows = bar_value_rows(values, minute=minute)
                changed_bar_rows: list[tuple[Any, ...]] = []
                returning = (
                    "trading_date,minute,code,market" if minute else "trading_date,code,market"
                ) if observations and not metadata_suppressed_rows else ""
                earlier_rows, latest_rows = _partition_rows_by_last_key(
                    bar_rows, (0, 1, 2, 3) if minute else (0, 1, 2),
                )
                for row in earlier_rows:
                    _execute_multirow_upsert(
                        observed_cursor,
                        f"INSERT INTO {table}({','.join(columns)}) VALUES",
                        [row], f"ON CONFLICT({conflict}) DO UPDATE SET {updates}{changed_guard}",
                        placeholder="%s", batch_size=1,
                        returning_columns=returning,
                        returned_rows=changed_bar_rows if returning else None,
                    )
                _execute_multirow_upsert(
                    observed_cursor,
                    f"INSERT INTO {table}({','.join(columns)}) VALUES",
                    latest_rows,
                    f"ON CONFLICT({conflict}) DO UPDATE SET {updates}{changed_guard}",
                    placeholder="%s", batch_size=POSTGRES_MULTIROW_UPSERT_ROWS,
                    returning_columns=returning,
                    returned_rows=changed_bar_rows if returning else None,
                )
                bar_write_ms = round((monotonic() - phase_started) * 1000)

                phase_started = monotonic()
                metadata_cursor = (
                    _PostgresObservedCursor(
                        cursor, self._database_url, bar_backend_pid,
                        True, metadata_statement_diagnostics,
                    ) if capture_enabled else cursor
                )
                if not metadata_suppressed_rows:
                    _save_postgres_metadata(
                        metadata_cursor, observations, multirow=True,
                        changed_bar_keys={_bar_metadata_key(row, minute=minute) for row in changed_bar_rows},
                    )
                metadata_ms = round((monotonic() - phase_started) * 1000)

                phase_started = monotonic()
                if minute and observations and self._observation_history_enabled:
                    phase_started = monotonic()
                    revision_sources = [
                        ObservationRevisionSource.from_observation(
                            "minute_bar", observation.subject, key,
                            minute_bar_revision_payload(
                                value,
                                window_closed=observation.metadata.completeness in {
                                    DataCompleteness.COMPLETE, DataCompleteness.PARTIAL,
                                },
                                capture_quality=(
                                    "complete" if observation.metadata.completeness == DataCompleteness.COMPLETE
                                    else observation.metadata.completeness.value
                                ),
                                finalization_source="query_response",
                            ),
                            observation,
                        )
                        for value, (key, observation) in zip(values, observations, strict=True)
                    ]
                    revision_sources_ms = round((monotonic() - phase_started) * 1000)
                    revision_timings: dict[str, float] = {}
                    latest_by_key = _load_postgres_latest_revisions(
                        cursor, revision_sources, timings=revision_timings,
                    )
                    revision_locks_ms = round(revision_timings.get("lock_seconds", 0.0) * 1000)
                    revision_lookup_ms = round(revision_timings.get("lookup_seconds", 0.0) * 1000)
                    revision_lookup_statements = 1
                    revision_lookup_keys = len(latest_by_key)
                    row_phase_started = monotonic()
                    insert_execute_seconds = [0.0]
                    (revision_insert_statements,
                     revision_insert_rows) = _insert_postgres_observation_revisions_batch(
                        cursor, revision_sources, latest_by_key,
                        execute_seconds=insert_execute_seconds,
                    )
                    revision_rows_ms = round((monotonic() - row_phase_started) * 1000)
                    revision_insert_execute_ms = round(insert_execute_seconds[0] * 1000)
                else:
                    revision_insert_rows = 0
                revision_ms = round((monotonic() - phase_started) * 1000)

            # During an explicitly enabled diagnostic capture, sample this exact
            # backend while COMMIT is in progress. WAL timing was enabled locally
            # before the bar UPSERT, never through cluster configuration.
            if capture_enabled:
                commit_backend_pid = bar_backend_pid
                if bar_backend_pid > 0:
                    try:
                        commit_probe_thread = Thread(
                            target=_sample_postgres_commit_waits,
                            args=(self._database_url, bar_backend_pid, commit_probe_stop,
                                  commit_wait_samples, commit_probe_errors),
                            name="market-commit-wait-probe", daemon=True,
                        )
                        commit_probe_thread.start()
                    except Exception as error:
                        commit_probe_errors.append(type(error).__name__)
                        commit_probe_thread = None
            phase_started = monotonic()
            commit_started_at = time()
            try:
                connection.commit()
            finally:
                commit_ended_at = time()
                commit_ms = round((monotonic() - phase_started) * 1000)
                if commit_probe_stop is not None:
                    commit_probe_stop.set()
                if commit_probe_thread is not None:
                    try:
                        # A slow probe connection must not become part of the
                        # measured save latency after COMMIT has finished.
                        commit_probe_incomplete = commit_probe_thread.is_alive()
                    except Exception as error:
                        commit_probe_errors.append(type(error).__name__)
                        commit_probe_incomplete = True
                commit_wait_samples = list(commit_wait_samples)
                commit_probe_errors = list(commit_probe_errors)
        except BaseException:
            connection.rollback()
            raise
        finally:
            phase_started = monotonic()
            connection.close()
            close_ms = round((monotonic() - phase_started) * 1000)

        total_ms = round((monotonic() - total_started) * 1000)
        from .diagnostic_metrics import record_market_bar_save
        record_market_bar_save(
            kind="minute" if minute else "daily", rows=len(values),
            db_call_id=writer.call_id,
            observations=len(observations or ()), connect_ms=connect_ms,
            metadata_suppressed_rows=metadata_suppressed_rows,
            bars_ms=bar_write_ms, metadata_ms=metadata_ms,
            revisions_ms=revision_ms, commit_ms=commit_ms,
            close_ms=close_ms, total_ms=total_ms,
            revision_lookup_statements=revision_lookup_statements,
            revision_lookup_keys=revision_lookup_keys,
            revision_insert_statements=revision_insert_statements,
            revision_insert_rows=revision_insert_rows,
            revision_sources_ms=revision_sources_ms,
            revision_locks_ms=revision_locks_ms,
            revision_lookup_ms=revision_lookup_ms,
            revision_rows_ms=revision_rows_ms,
            revision_insert_execute_ms=revision_insert_execute_ms,
            commit_wait_samples=commit_wait_samples,
            commit_probe_errors=commit_probe_errors,
            commit_backend_pid=commit_backend_pid,
            commit_started_at=commit_started_at,
            commit_ended_at=commit_ended_at,
            commit_probe_incomplete=commit_probe_incomplete,
            wal_timing_for_commit=wal_timing_for_commit,
            wal_timing_error=wal_timing_error,
            bar_statement_diagnostics=bar_statement_diagnostics,
            metadata_statement_diagnostics=metadata_statement_diagnostics,
        )
        from .diagnostic_metrics import record_writer_transaction
        record_writer_transaction(writer_kind,
                                  len(values), total_ms, commit_ms=commit_ms,
                                  connect_ms=connect_ms,
                                  execute_ms=bar_write_ms + metadata_ms + revision_ms,
                                  db_call_id=writer.call_id,
                                  domain_counts=({
                                      "bar_shape_version": 1,
                                      "bar_changed_rows": len(changed_bar_rows) if returning else -1,
                                      "observations": len(observations or ()),
                                      "metadata_suppressed_rows": metadata_suppressed_rows,
                                      "revision_insert_rows": revision_insert_rows,
                                      "revision_history_enabled": int(self._observation_history_enabled),
                                      "duplicate_input_keys": len(earlier_rows),
                                  } if minute else None))
        if total_ms >= 1000:
            logger.warning(
                "slow PostgreSQL market bar save kind=%s rows=%d observations=%d "
                "connect_ms=%d bars_ms=%d metadata_ms=%d revisions_ms=%d "
                "revision_sources_ms=%d revision_locks_ms=%d revision_lookup_ms=%d "
                "revision_rows_ms=%d revision_insert_execute_ms=%d "
                "revision_lookup_statements=%d revision_lookup_keys=%d "
                "revision_insert_statements=%d revision_insert_rows=%d "
                "commit_ms=%d close_ms=%d total_ms=%d",
                "minute" if minute else "daily", len(values), len(observations or ()),
                connect_ms, bar_write_ms, metadata_ms, revision_ms,
                revision_sources_ms, revision_locks_ms, revision_lookup_ms,
                revision_rows_ms, revision_insert_execute_ms,
                revision_lookup_statements, revision_lookup_keys,
                revision_insert_statements, revision_insert_rows,
                commit_ms, close_ms, total_ms,
            )
