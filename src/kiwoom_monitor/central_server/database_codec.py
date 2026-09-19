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
