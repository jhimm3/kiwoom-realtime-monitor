"""Read archived records only for existing user-facing history queries."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind
from kiwoom_monitor.domain.time_basis import KST
from kiwoom_monitor.infrastructure.market_data_metadata_codec import market_metadata_document

from .database_codec import dataset_snapshot_result_rows


class PCHistoryReadUnavailable(RuntimeError):
    """A local slice was archived and the configured archive cannot serve it."""


def _history(store, source: str, **filters) -> list[dict]:
    read = getattr(store, "read_pc_history", None)
    if read is None:
        return []
    try:
        return read(source, **filters)
    except (OSError, ValueError, RuntimeError) as error:
        raise PCHistoryReadUnavailable("NAS 보관 이력을 읽을 수 없습니다.") from error


def display_minute_history(store, values: list[dict], code: str, first: str, last: str, market: str = "") -> list[dict]:
    archived = _history(store, "central_minute_bars", subject=code, first=first,
                        last=last + "T24", limit=10000)
    if not archived:
        return values
    current = {(row["trading_date"], row["minute"], row["market"]): row for row in values}
    metadata = {}
    for entry in archived:
        row = entry["data"]
        day, minute, venue = row["trading_date"], row["minute"], row["market"]
        if (market and market != venue) or (day, minute, venue) in current:
            continue
        group = day, venue
        if group not in metadata:
            start = datetime.combine(datetime.fromisoformat(day).date(), time(), KST)
            observations = store.load_market_data_metadata_range(
                MarketDatasetKind.MINUTE_BAR, f"{code}:{venue}", start, start + timedelta(days=1))
            metadata[group] = {item.observation_key: market_metadata_document(item.metadata) for item in observations}
        current[day, minute, venue] = {
            **row, "metadata": metadata[group].get(day + "T" + minute, {"completeness": "unconfirmed"}),
        }
    return [row for _, row in sorted(current.items())]


def display_daily_history(store, values: list[dict], code: str, market: str, limit: int,
                          *, last: str = "") -> list[dict]:
    if not last and len(values) >= limit:
        return values
    archived = _history(store, "central_daily_bars", subject=code, last=last, limit=min(limit * 3, 10000))
    if not archived:
        return values
    merged = {}
    for row in [*(entry["data"] for entry in archived), *values]:
        day = row["trading_date"]
        venue = row.get("market", market)
        if (not market or venue == market) and (not last or day < last):
            merged[day, venue] = row
    return [row for _, row in sorted(merged.items(), reverse=True)][:limit]


def display_snapshot_history(store, values: list[dict], kind: str, subject: str, limit: int) -> list[dict]:
    if limit == 1 and values:
        return values
    archived = _history(store, f"snapshot/{kind}", subject=subject, limit=limit)
    if not archived:
        return values
    merged = {}
    for entry in archived:
        data = entry["data"]
        row, = dataset_snapshot_result_rows(((data["subject"], data["snapshot_key"], data["saved_at"],
                                             data.get("resolved_payload_json") or data["payload_json"]),))
        merged[row["subject"], row["snapshot_key"]] = (entry["sort"], row)
    for row in values:
        key = row["subject"], row["snapshot_key"]
        sort_key = merged[key][0] if key in merged else row["snapshot_key"]
        merged[key] = sort_key, row
    return [row for _, row in sorted(merged.values(), key=lambda pair: pair[0], reverse=True)][:limit]
