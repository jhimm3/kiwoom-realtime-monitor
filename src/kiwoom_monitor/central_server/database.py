from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from time import time
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from kiwoom_monitor.domain.market_data_contract import (
    MarketDataMetadata,
    MarketDataObservation,
    MarketDatasetKind,
)
from kiwoom_monitor.application.market_data_coverage import CoverageObservation
from kiwoom_monitor.infrastructure.market_data_metadata_codec import (
    market_metadata_from_storage_row,
    market_metadata_storage_values,
)

from kiwoom_monitor.central_server.database_codec import (
    BAR_KEY_COLUMNS,
    bar_columns,
    bar_result_rows,
    bar_value_rows,
    bounded_limit,
    dataset_snapshot_result_rows,
    document_result_rows,
    document_select_query,
    document_value_rows,
)
from kiwoom_monitor.central_server.central_schema import (
    central_schema_migrations,
)
from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigrationRunner


@dataclass(frozen=True)
class StoredQuery:
    payload: dict[str, Any]
    has_next: bool
    next_key: str


class QueryStore(Protocol):
    def initialize(self) -> None: ...
    def load_query(self, cache_key: str) -> StoredQuery | None: ...
    def save_query(self, cache_key: str, api_id: str, expires_at: float, value: StoredQuery) -> None: ...
    def save_realtime_snapshots(self, values: list[dict[str, Any]]) -> None: ...
    def load_realtime_snapshots(self, codes: list[str]) -> list[dict[str, Any]]: ...
    def save_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None: ...
    def replace_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None: ...
    def load_minute_bars(self, code: str, trading_date: str, market: str = "") -> list[dict[str, Any]]: ...
    def replace_daily_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None: ...
    def load_daily_bars(self, code: str, market: str = "", limit: int = 250) -> list[dict[str, Any]]: ...
    def save_dataset_snapshot(
        self, kind: str, subject: str, snapshot_key: str, payload: dict[str, Any],
        *, observation: MarketDataObservation[object] | None = None,
    ) -> None: ...
    def load_dataset_snapshots(self, kind: str, subject: str = "", limit: int = 100) -> list[dict[str, Any]]: ...
    def save_market_data_metadata(self, observation_key: str, observation: MarketDataObservation[object]) -> None: ...
    def load_market_data_metadata(self, kind: MarketDatasetKind, subject: str, observation_key: str) -> MarketDataMetadata | None: ...
    def load_market_data_metadata_range(
        self, kind: MarketDatasetKind, subject: str, start: datetime, end: datetime,
    ) -> list[CoverageObservation]: ...
    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...
    def replace_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...
    def load_documents(self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
                       updated_after: float = 0.0) -> list[dict[str, Any]]: ...
    def save_external_bars(self, values: list[dict[str, Any]]) -> None: ...
    def load_external_bars(self, instrument: str, timeframe: str, limit: int = 1000) -> list[dict[str, Any]]: ...
    def close(self) -> None: ...
    def storage_size_bytes(self) -> int | None: ...


class SQLiteQueryStore:
    """로컬 중앙 서버가 기존 monitor.sqlite3 안에서 사용하는 조회 캐시."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()
        self._memory_uri = f"file:central-query-store-{id(self)}?mode=memory&cache=shared" if str(path) == ":memory:" else ""
        self._keeper: sqlite3.Connection | None = None

    def initialize(self) -> None:
        if self._memory_uri:
            self._keeper = sqlite3.connect(self._memory_uri, uri=True, check_same_thread=False)
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            with connection as transaction:
                cursor = transaction.cursor()
                CentralSchemaMigrationRunner(cursor, "sqlite").apply(
                    central_schema_migrations()
                )

    def storage_size_bytes(self) -> int | None:
        if self._memory_uri:
            return None
        try:
            return self._path.stat().st_size
        except OSError:
            return None

    def load_query(self, cache_key: str) -> StoredQuery | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json,has_next,next_key FROM central_api_query_cache "
                "WHERE cache_key=? AND expires_at>?", (cache_key, time()),
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
            connection.execute("DELETE FROM central_api_query_cache WHERE expires_at<=?", (time(),))

    def close(self) -> None:
        keeper, self._keeper = self._keeper, None
        if keeper is not None:
            keeper.close()

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

    def save_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        if not values:
            return
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_minute_bars VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(trading_date,minute,code,market) DO UPDATE SET "
                "high=MAX(central_minute_bars.high,excluded.high),"
                "low=MIN(central_minute_bars.low,excluded.low),close=excluded.close,"
                "volume=central_minute_bars.volume+excluded.volume,"
                "trade_value_million_won=central_minute_bars.trade_value_million_won+excluded.trade_value_million_won,"
                "updated_at=excluded.updated_at",
                bar_value_rows(values, minute=True),
            )
            _save_sqlite_metadata(connection, observations)

    def load_minute_bars(self, code: str, trading_date: str, market: str = "") -> list[dict[str, Any]]:
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
            rows = connection.execute(sql, parameters).fetchall()
        return bar_result_rows(rows, minute=True)

    def replace_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        self._replace_bars(
            "central_minute_bars", values, minute=True, observations=observations
        )

    def replace_daily_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        self._replace_bars(
            "central_daily_bars", values, minute=False, observations=observations
        )

    def _replace_bars(
        self, table: str, values: list[dict[str, Any]], *, minute: bool,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        if not values:
            return
        columns = bar_columns(minute=minute)
        placeholders = ",".join("?" for _ in columns)
        updates = ",".join(f"{column}=excluded.{column}" for column in columns if column not in BAR_KEY_COLUMNS)
        conflict = "trading_date,minute,code,market" if minute else "trading_date,code,market"
        with self._lock, self._connection() as connection:
            connection.executemany(
                f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders}) "
                f"ON CONFLICT({conflict}) DO UPDATE SET {updates}",
                bar_value_rows(values, minute=minute),
            )
            _save_sqlite_metadata(connection, observations)

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

    def save_dataset_snapshot(
        self, kind: str, subject: str, snapshot_key: str, payload: dict[str, Any],
        *, observation: MarketDataObservation[object] | None = None,
    ) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(kind,subject,snapshot_key) DO UPDATE SET "
                "saved_at=excluded.saved_at,payload_json=excluded.payload_json",
                (kind, subject, snapshot_key, time(), json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            )
            if observation is not None:
                connection.execute(
                    _market_metadata_upsert_sql("?", "excluded"),
                    market_metadata_storage_values(snapshot_key, observation),
                )

    def load_dataset_snapshots(self, kind: str, subject: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT subject,snapshot_key,saved_at,payload_json FROM central_dataset_snapshots WHERE kind=?"
        parameters: list[object] = [kind]
        if subject:
            sql += " AND subject=?"
            parameters.append(subject)
        sql += " ORDER BY snapshot_key DESC LIMIT ?"
        parameters.append(bounded_limit(limit, 5000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return dataset_snapshot_result_rows(rows)

    def save_market_data_metadata(
        self, observation_key: str, observation: MarketDataObservation[object]
    ) -> None:
        values = market_metadata_storage_values(observation_key, observation)
        with self._lock, self._connection() as connection:
            connection.execute(
                _market_metadata_upsert_sql("?", "excluded"),
                values,
            )

    def load_market_data_metadata(
        self, kind: MarketDatasetKind, subject: str, observation_key: str
    ) -> MarketDataMetadata | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT effective_at,available_at,venue,unit,value_kind,completeness,origin,source,"
                "candidate_universe FROM central_market_data_observation_meta "
                "WHERE dataset_kind=? AND subject=? AND observation_key=?",
                (kind.value, subject, observation_key),
            ).fetchone()
        return market_metadata_from_storage_row(row)

    def load_market_data_metadata_range(
        self, kind: MarketDatasetKind, subject: str, start: datetime, end: datetime,
    ) -> list[CoverageObservation]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT observation_key,effective_at,available_at,venue,unit,value_kind,"
                "completeness,origin,source,candidate_universe "
                "FROM central_market_data_observation_meta WHERE dataset_kind=? AND subject=? "
                "AND effective_at>=? AND effective_at<? ORDER BY effective_at",
                (kind.value, subject, start.isoformat(), end.isoformat()),
            ).fetchall()
        return [
            CoverageObservation(str(row[0]), _metadata_from_range_row(row))
            for row in rows
        ]

    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        now = time()
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=excluded.updated_at,document_json=excluded.document_json",
                document_value_rows(collection, values, now),
            )

    def replace_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        """컬렉션 전체를 한 트랜잭션에서 현재 스냅샷으로 교체한다."""
        now = time()
        rows = document_value_rows(collection, values, now)
        with self._lock, self._connection() as connection:
            connection.execute("DELETE FROM central_documents WHERE collection=?", (collection,))
            if rows:
                connection.executemany(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(?,?,?,?,?)", rows,
                )

    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]:
        sql, parameters = document_select_query(
            collection, owner, limit, offset, updated_after, placeholder="?",
        )
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return document_result_rows(rows)

    def save_external_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._lock, self._connection() as connection:
            connection.executemany(
                "INSERT INTO central_external_bars VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(provider,instrument,contract,timeframe,bar_time) DO UPDATE SET "
                "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
                "volume=excluded.volume,updated_at=excluded.updated_at",
                [_external_bar_values(value) for value in values],
            )

    def load_external_bars(self, instrument: str, timeframe: str, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT provider,instrument,contract,timeframe,bar_time,open,high,low,close,volume,updated_at "
                "FROM central_external_bars WHERE instrument=? AND timeframe=? "
                "ORDER BY bar_time DESC LIMIT ?", (instrument, timeframe, bounded_limit(limit, 10000)),
            ).fetchall()
        return [_external_bar_result(row) for row in reversed(rows)]


    def _connect(self) -> sqlite3.Connection:
        connection = (
            sqlite3.connect(self._memory_uri, timeout=10, uri=True, check_same_thread=False)
            if self._memory_uri else sqlite3.connect(self._path, timeout=10)
        )
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


class PostgresQueryStore:
    """시놀로지 PostgreSQL에서 사용하는 동일 규격의 조회 캐시."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def initialize(self) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            CentralSchemaMigrationRunner(cursor, "postgres").apply(
                central_schema_migrations()
            )

    def load_query(self, cache_key: str) -> StoredQuery | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT payload_json,has_next,next_key FROM central_api_query_cache "
                "WHERE cache_key=%s AND expires_at>%s", (cache_key, time()),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return StoredQuery(payload, bool(row[1]), str(row[2]))

    def save_query(self, cache_key: str, api_id: str, expires_at: float, value: StoredQuery) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_api_query_cache(cache_key,api_id,expires_at,payload_json,has_next,next_key) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(cache_key) DO UPDATE SET "
                "api_id=EXCLUDED.api_id,expires_at=EXCLUDED.expires_at,payload_json=EXCLUDED.payload_json,"
                "has_next=EXCLUDED.has_next,next_key=EXCLUDED.next_key",
                (cache_key, api_id, expires_at, json.dumps(value.payload, ensure_ascii=False), value.has_next, value.next_key),
            )
            cursor.execute("DELETE FROM central_api_query_cache WHERE expires_at<=%s", (time(),))

    def close(self) -> None:
        return

    def storage_size_bytes(self) -> int | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_database_size(current_database())")
            row = cursor.fetchone()
        return int(row[0]) if row else None

    def save_realtime_snapshots(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_realtime_latest(event_type,item_key,received_at,event_json) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(event_type,item_key) DO UPDATE SET "
                "received_at=EXCLUDED.received_at,event_json=EXCLUDED.event_json",
                [(str(v["event_type"]), str(v["item_key"]), float(v["received_at"]),
                  json.dumps(v["event"], ensure_ascii=False)) for v in values],
            )

    def load_realtime_snapshots(self, codes: list[str]) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT event_json FROM central_realtime_latest "
                "WHERE (item_key=ANY(%s) OR event_type='market_state') AND received_at>%s "
                "ORDER BY received_at", (codes, time() - 300),
            )
            rows = cursor.fetchall()
        return [row[0] if isinstance(row[0], dict) else json.loads(row[0]) for row in rows]

    def save_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        if not values:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_minute_bars VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(trading_date,minute,code,market) DO UPDATE SET "
                "high=GREATEST(central_minute_bars.high,EXCLUDED.high),"
                "low=LEAST(central_minute_bars.low,EXCLUDED.low),close=EXCLUDED.close,"
                "volume=central_minute_bars.volume+EXCLUDED.volume,"
                "trade_value_million_won=central_minute_bars.trade_value_million_won+EXCLUDED.trade_value_million_won,"
                "updated_at=EXCLUDED.updated_at",
                bar_value_rows(values, minute=True),
            )
            _save_postgres_metadata(cursor, observations)

    def load_minute_bars(self, code: str, trading_date: str, market: str = "") -> list[dict[str, Any]]:
        sql = (
            "SELECT trading_date::text,to_char(minute,'HH24:MI'),code,market,open,high,low,close,volume,"
            "trade_value_million_won,updated_at FROM central_minute_bars WHERE code=%s AND trading_date=%s"
        )
        parameters: list[object] = [code, trading_date]
        if market:
            sql += " AND market=%s"
            parameters.append(market)
        sql += " ORDER BY minute"
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return bar_result_rows(rows, minute=True)

    def replace_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        self._replace_bars(
            "central_minute_bars", values, minute=True, observations=observations
        )

    def replace_daily_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        self._replace_bars(
            "central_daily_bars", values, minute=False, observations=observations
        )

    def _replace_bars(
        self, table: str, values: list[dict[str, Any]], *, minute: bool,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None:
        if not values:
            return
        columns = bar_columns(minute=minute)
        placeholders = ",".join("%s" for _ in columns)
        updates = ",".join(f"{column}=EXCLUDED.{column}" for column in columns if column not in BAR_KEY_COLUMNS)
        conflict = "trading_date,minute,code,market" if minute else "trading_date,code,market"
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders}) "
                f"ON CONFLICT({conflict}) DO UPDATE SET {updates}",
                bar_value_rows(values, minute=minute),
            )
            _save_postgres_metadata(cursor, observations)

    def load_daily_bars(self, code: str, market: str = "", limit: int = 250) -> list[dict[str, Any]]:
        sql = ("SELECT trading_date::text,code,market,open,high,low,close,volume,trade_value_million_won,updated_at "
               "FROM central_daily_bars WHERE code=%s")
        parameters: list[object] = [code]
        if market:
            sql += " AND market=%s"
            parameters.append(market)
        sql += " ORDER BY trading_date DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 5000))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return bar_result_rows(rows, minute=False)

    def save_dataset_snapshot(
        self, kind: str, subject: str, snapshot_key: str, payload: dict[str, Any],
        *, observation: MarketDataObservation[object] | None = None,
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(kind,subject,snapshot_key) DO UPDATE SET "
                "saved_at=EXCLUDED.saved_at,payload_json=EXCLUDED.payload_json",
                (kind, subject, snapshot_key, time(), json.dumps(payload, ensure_ascii=False)),
            )
            if observation is not None:
                cursor.execute(
                    _market_metadata_upsert_sql("%s", "EXCLUDED"),
                    market_metadata_storage_values(snapshot_key, observation),
                )

    def load_dataset_snapshots(self, kind: str, subject: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT subject,snapshot_key,saved_at,payload_json FROM central_dataset_snapshots WHERE kind=%s"
        parameters: list[object] = [kind]
        if subject:
            sql += " AND subject=%s"
            parameters.append(subject)
        sql += " ORDER BY snapshot_key DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 5000))
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return dataset_snapshot_result_rows(rows)

    def save_market_data_metadata(
        self, observation_key: str, observation: MarketDataObservation[object]
    ) -> None:
        values = market_metadata_storage_values(observation_key, observation)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                _market_metadata_upsert_sql("%s", "EXCLUDED"),
                values,
            )

    def load_market_data_metadata(
        self, kind: MarketDatasetKind, subject: str, observation_key: str
    ) -> MarketDataMetadata | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT effective_at,available_at,venue,unit,value_kind,completeness,origin,source,"
                "candidate_universe FROM central_market_data_observation_meta "
                "WHERE dataset_kind=%s AND subject=%s AND observation_key=%s",
                (kind.value, subject, observation_key),
            )
            row = cursor.fetchone()
        return market_metadata_from_storage_row(row)

    def load_market_data_metadata_range(
        self, kind: MarketDatasetKind, subject: str, start: datetime, end: datetime,
    ) -> list[CoverageObservation]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT observation_key,effective_at,available_at,venue,unit,value_kind,"
                "completeness,origin,source,candidate_universe "
                "FROM central_market_data_observation_meta WHERE dataset_kind=%s AND subject=%s "
                "AND effective_at>=%s AND effective_at<%s ORDER BY effective_at",
                (kind.value, subject, start, end),
            )
            rows = cursor.fetchall()
        return [
            CoverageObservation(str(row[0]), _metadata_from_range_row(row))
            for row in rows
        ]

    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        now = time()
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json",
                document_value_rows(collection, values, now),
            )

    def replace_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        """컬렉션 전체를 한 트랜잭션에서 현재 스냅샷으로 교체한다."""
        now = time()
        rows = document_value_rows(collection, values, now)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM central_documents WHERE collection=%s", (collection,))
            if rows:
                cursor.executemany(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(%s,%s,%s,%s,%s)", rows,
                )

    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]:
        sql, parameters = document_select_query(
            collection, owner, limit, offset, updated_after, placeholder="%s",
        )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return document_result_rows(rows)

    def save_external_bars(self, values: list[dict[str, Any]]) -> None:
        if not values:
            return
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO central_external_bars VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(provider,instrument,contract,timeframe,bar_time) DO UPDATE SET "
                "open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,"
                "volume=EXCLUDED.volume,updated_at=EXCLUDED.updated_at",
                [_external_bar_values(value) for value in values],
            )

    def load_external_bars(self, instrument: str, timeframe: str, limit: int = 1000) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT provider,instrument,contract,timeframe,bar_time::text,open,high,low,close,volume,updated_at "
                "FROM central_external_bars WHERE instrument=%s AND timeframe=%s "
                "ORDER BY bar_time DESC LIMIT %s", (instrument, timeframe, bounded_limit(limit, 10000)),
            )
            rows = cursor.fetchall()
        return [_external_bar_result(row) for row in reversed(rows)]


    def _connect(self):
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError("PostgreSQL 서버 의존성을 설치하세요: pip install -e .[server]") from error
        return psycopg.connect(self._database_url)


def create_query_store(database_url: str) -> QueryStore:
    if database_url.startswith("sqlite:///"):
        raw = unquote(database_url.removeprefix("sqlite:///"))
        # sqlite:///C:/...와 sqlite:///relative/path를 모두 지원한다.
        return SQLiteQueryStore(Path(raw))
    parsed = urlsplit(database_url)
    if parsed.scheme in {"postgres", "postgresql"}:
        return PostgresQueryStore(database_url)
    raise ValueError("중앙 DB 주소는 sqlite:/// 또는 postgresql:// 형식이어야 합니다.")


def _external_bar_values(value: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(value["provider"]), str(value["instrument"]), str(value["contract"]),
        str(value["timeframe"]), str(value["bar_time"]), value.get("open"), value.get("high"),
        value.get("low"), value.get("close"), value.get("volume"), float(value["updated_at"]),
    )


def _external_bar_result(row: tuple[object, ...]) -> dict[str, Any]:
    keys = ("provider", "instrument", "contract", "timeframe", "bar_time", "open", "high", "low", "close", "volume", "updated_at")
    return dict(zip(keys, row, strict=True))


def _market_metadata_upsert_sql(placeholder: str, excluded: str) -> str:
    placeholders = ",".join((placeholder,) * 12)
    return (
        f"INSERT INTO central_market_data_observation_meta VALUES({placeholders}) "
        "ON CONFLICT(dataset_kind,subject,observation_key) DO UPDATE SET "
        f"effective_at={excluded}.effective_at,available_at={excluded}.available_at,"
        f"venue={excluded}.venue,unit={excluded}.unit,value_kind={excluded}.value_kind,"
        f"completeness={excluded}.completeness,origin={excluded}.origin,source={excluded}.source,"
        f"candidate_universe={excluded}.candidate_universe"
    )


def _metadata_from_range_row(row: tuple[object, ...]) -> MarketDataMetadata:
    metadata = market_metadata_from_storage_row(tuple(row[1:]))
    if metadata is None:
        raise ValueError("market metadata row is missing")
    return metadata


def _save_sqlite_metadata(connection, observations) -> None:
    if not observations:
        return
    connection.executemany(
        _market_metadata_upsert_sql("?", "excluded"),
        [market_metadata_storage_values(key, observation) for key, observation in observations],
    )


def _save_postgres_metadata(cursor, observations) -> None:
    if not observations:
        return
    cursor.executemany(
        _market_metadata_upsert_sql("%s", "EXCLUDED"),
        [market_metadata_storage_values(key, observation) for key, observation in observations],
    )
