"""Persistence of latest realtime snapshots; connection ownership stays with the store."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from time import monotonic, time
from typing import Any

from .database_codec import _json_document


class SQLiteRealtimeSnapshotStoreMixin:
    def save_realtime_snapshots(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        rows = [(
            str(value["event_type"]), str(value["item_key"]), float(value["received_at"]),
            json.dumps(value["event"], ensure_ascii=False, separators=(",", ":")),
        ) for value in values]
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_realtime_latest(event_type,item_key,received_at,event_json) "
                "VALUES(?,?,?,?) ON CONFLICT(event_type,item_key) DO UPDATE SET "
                "received_at=excluded.received_at,event_json=excluded.event_json",
                rows,
            )

    def load_realtime_snapshots(self, codes: list[str]) -> list[dict[str, Any]]:
        placeholders = ",".join("?" for _ in codes)
        condition = f"item_key IN ({placeholders}) OR event_type='market_state'" if codes else "event_type='market_state'"
        parameters: list[object] = [*codes, time() - 300]
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"SELECT event_json FROM central_realtime_latest WHERE ({condition}) AND received_at>? "
                "ORDER BY received_at", parameters,
            ).fetchall()
        return [value for row in rows if isinstance((value := json.loads(str(row[0]))), dict)]

    def load_latest_market_caps(self, codes: list[str]) -> list[dict[str, Any]]:
        """Return the last persisted 0B market cap without reviving stale prices."""
        if not codes:
            return []
        placeholders = ",".join("?" for _ in codes)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT item_key,received_at,event_json FROM central_realtime_latest "
                f"WHERE event_type='trade' AND item_key IN ({placeholders})",
                codes,
            ).fetchall()
        return _latest_market_cap_rows(rows)


class PostgresRealtimeSnapshotStoreMixin:
    def save_realtime_snapshots(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        from .postgres_access import DBWriterContext, open_observed_connection

        started_at = monotonic()
        writer = DBWriterContext(
            writer_family="realtime.latest", writer_kind="realtime_latest",
            operation="save_realtime_snapshots", rows_attempted=len(values),
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_realtime_latest(event_type,item_key,received_at,event_json) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(event_type,item_key) DO UPDATE SET "
                "received_at=EXCLUDED.received_at,event_json=EXCLUDED.event_json",
                [(str(v["event_type"]), str(v["item_key"]), float(v["received_at"]),
                  json.dumps(v["event"], ensure_ascii=False)) for v in values],
            )
        from .diagnostic_metrics import record_writer_transaction
        record_writer_transaction("realtime_latest", len(values),
                                  round((monotonic() - started_at) * 1000),
                                  db_call_id=writer.call_id)

    def load_realtime_snapshots(self, codes: list[str]) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.realtime_market_state", writer_kind="realtime_snapshots",
            operation="load_realtime_snapshots", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT event_json FROM central_realtime_latest "
                "WHERE (item_key=ANY(%s) OR event_type='market_state') AND received_at>%s "
                "ORDER BY received_at", (codes, time() - 300),
            )
            rows = cursor.fetchall()
        return [row[0] if isinstance(row[0], dict) else json.loads(row[0]) for row in rows]

    def load_latest_market_caps(self, codes: list[str]) -> list[dict[str, Any]]:
        """Return the last persisted 0B market cap without a freshness cutoff."""
        if not codes:
            return []
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.realtime_market_state", writer_kind="latest_market_caps",
            operation="load_latest_market_caps", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT item_key,received_at,event_json FROM central_realtime_latest "
                "WHERE event_type='trade' AND item_key=ANY(%s)",
                (codes,),
            )
            rows = cursor.fetchall()
        return _latest_market_cap_rows(rows)


def _latest_market_cap_rows(
    rows: list[tuple[object, object, object]],
) -> list[dict[str, Any]]:
    """Decode only the durable market-cap field from latest 0B snapshots."""
    result: list[dict[str, Any]] = []
    for raw_code, raw_received_at, raw_event in rows:
        try:
            event = _json_document(raw_event)
            payload = event.get("payload")
            raw_market_cap = payload.get("market_cap_eok") if isinstance(payload, dict) else None
            market_cap = float(raw_market_cap)
            received_at = float(raw_received_at)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        code = str(raw_code).strip()
        if not code or market_cap <= 0:
            continue
        result.append({
            "code": code,
            "market_cap_eok": market_cap,
            "observed_at": datetime.fromtimestamp(
                received_at, timezone.utc,
            ).isoformat(),
        })
    return result
