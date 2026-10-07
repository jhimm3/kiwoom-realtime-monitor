from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from time import monotonic, time
from typing import Any, Protocol


logger = logging.getLogger("kiwoom_monitor.central_server.database")


def _cache_wall_time(store) -> float:
    # Only an owned replay store supplies this clock. Production stays unchanged.
    provider = getattr(store, "_query_cache_wall_time", None)
    return time() if provider is None else provider()


@dataclass(frozen=True)
class StoredQuery:
    payload: dict[str, Any]
    has_next: bool
    next_key: str


class QueryCacheStore(Protocol):
    """Persistent cache operations required by the REST broker."""

    def load_query(self, cache_key: str) -> StoredQuery | None: ...

    def save_query(self, cache_key: str, api_id: str, expires_at: float, value: StoredQuery) -> None: ...


class SQLiteQueryCacheStoreMixin:
    def load_query(self, cache_key: str) -> StoredQuery | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json,has_next,next_key FROM central_api_query_cache "
                "WHERE cache_key=? AND expires_at>?", (cache_key, _cache_wall_time(self)),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row[0]))
        return StoredQuery(payload, bool(row[1]), str(row[2])) if isinstance(payload, dict) else None

    def save_query(self, cache_key: str, api_id: str, expires_at: float, value: StoredQuery) -> None:
        encoded = json.dumps(value.payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO central_api_query_cache(cache_key,api_id,expires_at,payload_json,has_next,next_key) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET "
                "api_id=excluded.api_id,expires_at=excluded.expires_at,payload_json=excluded.payload_json,"
                "has_next=excluded.has_next,next_key=excluded.next_key",
                (cache_key, api_id, expires_at, encoded, int(value.has_next), value.next_key),
            )
            connection.execute("DELETE FROM central_api_query_cache WHERE expires_at<=?", (_cache_wall_time(self),))


class PostgresQueryCacheStoreMixin:
    def load_query(self, cache_key: str) -> StoredQuery | None:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.query_cache", writer_kind="query_cache",
            operation="load_query", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT payload_json,has_next,next_key FROM central_api_query_cache "
                "WHERE cache_key=%s AND expires_at>%s", (cache_key, _cache_wall_time(self)),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return StoredQuery(payload, bool(row[1]), str(row[2]))

    def save_query(self, cache_key: str, api_id: str, expires_at: float, value: StoredQuery) -> None:
        from .postgres_access import DBWriterContext, open_observed_connection

        total_started = monotonic()
        encode_started = monotonic()
        payload_json = json.dumps(value.payload, ensure_ascii=False)
        encode_ms = round((monotonic() - encode_started) * 1000)

        connect_started = monotonic()
        writer = DBWriterContext(
            writer_family="rest.query_cache", writer_kind="query_cache",
            operation="save_query", rows_attempted=1, api_id=api_id,
        )
        connection = open_observed_connection(self._connect, writer)
        connect_ms = round((monotonic() - connect_started) * 1000)
        upsert_ms = cleanup_ms = commit_ms = 0
        try:
            with connection.cursor() as cursor:
                phase_started = monotonic()
                cursor.execute(
                    "INSERT INTO central_api_query_cache(cache_key,api_id,expires_at,payload_json,has_next,next_key) "
                    "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(cache_key) DO UPDATE SET "
                    "api_id=EXCLUDED.api_id,expires_at=EXCLUDED.expires_at,payload_json=EXCLUDED.payload_json,"
                    "has_next=EXCLUDED.has_next,next_key=EXCLUDED.next_key",
                    (cache_key, api_id, expires_at, payload_json, value.has_next, value.next_key),
                )
                upsert_ms = round((monotonic() - phase_started) * 1000)

                phase_started = monotonic()
                cursor.execute("DELETE FROM central_api_query_cache WHERE expires_at<=%s", (_cache_wall_time(self),))
                cleanup_ms = round((monotonic() - phase_started) * 1000)

            phase_started = monotonic()
            connection.commit()
            commit_ms = round((monotonic() - phase_started) * 1000)
        finally:
            connection.close()

        total_ms = round((monotonic() - total_started) * 1000)
        from .diagnostic_metrics import record_writer_transaction
        try:
            record_writer_transaction("query_cache", 1, total_ms,
                                      commit_ms=commit_ms, connect_ms=connect_ms,
                                      execute_ms=upsert_ms + cleanup_ms,
                                      db_call_id=writer.call_id)
        except Exception:
            # The cache write has already committed; a diagnostic sink failure
            # must not turn that successful write into a caller-visible error.
            pass
        if total_ms >= 1000:
            logger.warning(
                "slow postgres query cache save api_id=%s encode_ms=%d connect_ms=%d "
                "upsert_ms=%d cleanup_ms=%d commit_ms=%d total_ms=%d",
                api_id, encode_ms, connect_ms, upsert_ms, cleanup_ms, commit_ms, total_ms,
            )
