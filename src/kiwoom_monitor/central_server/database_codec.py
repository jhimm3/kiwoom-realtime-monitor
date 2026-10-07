from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


MINUTE_BAR_COLUMNS = (
    "trading_date", "minute", "code", "market", "open", "high", "low", "close",
    "volume", "trade_value_million_won", "updated_at",
)
DAILY_BAR_COLUMNS = (
    "trading_date", "code", "market", "open", "high", "low", "close", "volume",
    "trade_value_million_won", "updated_at",
)
FIVE_MINUTE_BAR_COLUMNS = (
    "trading_date", "minute", "code", "market", "provider", "adjustment_mode",
    "bar_time_semantics", "open", "high", "low", "close", "volume",
    "trading_value_raw", "observed_at",
)
SECOND_TRADE_BAR_COLUMNS = (
    "trading_date", "trade_second", "code", "market", "open", "high", "low", "close",
    "volume", "trade_value_won", "trade_count", "available_at",
)
BAR_KEY_COLUMNS = frozenset({"trading_date", "minute", "code", "market"})


def bar_columns(*, minute: bool) -> tuple[str, ...]:
    return MINUTE_BAR_COLUMNS if minute else DAILY_BAR_COLUMNS


def bar_value_rows(values: Iterable[Mapping[str, Any]], *, minute: bool) -> list[tuple[Any, ...]]:
    columns = bar_columns(minute=minute)
    return [tuple(value[column] for column in columns) for value in values]


def bar_result_rows(rows: Iterable[Sequence[Any]], *, minute: bool) -> list[dict[str, Any]]:
    columns = bar_columns(minute=minute)
    return [dict(zip(columns, row, strict=True)) for row in rows]


def five_minute_bar_value_rows(values: Iterable[Mapping[str, Any]]) -> list[tuple[Any, ...]]:
    return [tuple(value[column] for column in FIVE_MINUTE_BAR_COLUMNS) for value in values]


def five_minute_bar_result_rows(rows: Iterable[Sequence[Any]]) -> list[dict[str, Any]]:
    result = [dict(zip(FIVE_MINUTE_BAR_COLUMNS, row, strict=True)) for row in rows]
    for value in result:
        for key in ("trading_date", "minute", "observed_at"):
            if not isinstance(value[key], str):
                value[key] = value[key].isoformat()
    return result


def second_trade_bar_value_rows(
    values: Iterable[Mapping[str, Any]],
) -> list[tuple[Any, ...]]:
    return [tuple(value[column] for column in SECOND_TRADE_BAR_COLUMNS) for value in values]


def bounded_limit(value: int, maximum: int) -> int:
    return max(1, min(maximum, int(value)))


def bounded_offset(value: int) -> int:
    return max(0, int(value))


def document_value_rows(
    collection: str,
    values: Iterable[Mapping[str, Any]],
    updated_at: float,
) -> list[tuple[object, ...]]:
    return [
        (
            collection,
            str(value["owner"]),
            str(value["key"]),
            updated_at,
            json.dumps(value["document"], ensure_ascii=False, separators=(",", ":")),
        )
        for value in values
    ]


def document_result_rows(rows: Iterable[Sequence[Any]]) -> list[dict[str, Any]]:
    return [
        {
            "owner": row[0],
            "key": row[1],
            "updated_at": row[2],
            "document": json_mapping(row[3]),
        }
        for row in rows
    ]


def dataset_snapshot_result_rows(rows: Iterable[Sequence[Any]]) -> list[dict[str, Any]]:
    return [
        {
            "subject": row[0],
            "snapshot_key": row[1],
            "saved_at": row[2],
            "payload": json_mapping(row[3]),
        }
        for row in rows
    ]


def observation_revision_result_rows(
    rows: Iterable[Sequence[Any]],
) -> list[dict[str, Any]]:
    keys = (
        "accepted_sequence", "revision_id", "observation_key", "schema_version",
        "source_id", "source_session_id", "source_sequence", "kind", "subject",
        "venue", "effective_at", "received_at", "available_at", "revision_of",
        "payload_hash", "unit", "value_kind", "completeness", "origin",
        "candidate_universe", "clock_quality",
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        value = dict(zip(keys, row[:21], strict=True))
        for name in ("effective_at", "received_at", "available_at"):
            if value[name] is not None and not isinstance(value[name], str):
                value[name] = value[name].isoformat()
        value["quality_flags"] = json.loads(str(row[21])) if not isinstance(row[21], list) else row[21]
        value["source_ref"] = json_mapping(row[22])
        value["payload"] = json_mapping(row[23])
        result.append(value)
    return result


def document_select_query(
    collection: str,
    owner: str,
    limit: int,
    offset: int,
    updated_after: float,
    *,
    placeholder: str,
) -> tuple[str, list[object]]:
    sql = (
        "SELECT owner,document_key,updated_at,document_json "
        f"FROM central_documents WHERE collection={placeholder}"
    )
    parameters: list[object] = [collection]
    if owner:
        sql += f" AND owner={placeholder}"
        parameters.append(owner)
    if updated_after > 0:
        sql += f" AND updated_at>{placeholder}"
        parameters.append(float(updated_after))
    sql += (
        " ORDER BY updated_at DESC,owner,document_key "
        f"LIMIT {placeholder} OFFSET {placeholder}"
    )
    parameters.extend((bounded_limit(limit, 10_000), bounded_offset(offset)))
    return sql, parameters


def json_mapping(value: object) -> dict[str, Any]:
    decoded = dict(value) if isinstance(value, Mapping) else json.loads(str(value))
    if not isinstance(decoded, dict):
        raise ValueError("central JSON value must be an object")
    return decoded


def _json_document(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    decoded = json.loads(str(value))
    if not isinstance(decoded, dict):
        raise ValueError("stored execution document must be an object")
    return decoded


def _event_document(value: dict[str, Any]) -> str:
    document = dict(value.get("document", {})) if isinstance(value.get("document"), dict) else {}
    document.update({key: item for key, item in value.items() if key != "document"})
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def _observation_revision_columns(*, prefix: str = "", postgres: bool = False) -> str:
    column = lambda name: f"{prefix}{name}"
    effective_at = f"{column('effective_at')}::text" if postgres else column("effective_at")
    received_at = f"{column('received_at')}::text" if postgres else column("received_at")
    available_at = f"{column('available_at')}::text" if postgres else column("available_at")
    return ",".join((
        column("accepted_sequence"), column("revision_id"), column("observation_key"),
        column("schema_version"), column("source_id"), column("source_session_id"),
        column("source_sequence"), column("kind"), column("subject"), column("venue"),
        effective_at, received_at, available_at, column("revision_of"), column("payload_hash"),
        column("unit"), column("value_kind"), column("completeness"), column("origin"),
        column("candidate_universe"), column("clock_quality"), column("quality_flags_json"),
        column("source_ref_json"), column("payload_json"),
    ))
