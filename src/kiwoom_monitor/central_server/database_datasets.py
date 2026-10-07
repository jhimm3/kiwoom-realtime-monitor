"""Dataset snapshot persistence for SQLite and PostgreSQL stores."""
from __future__ import annotations

import json
import logging
from threading import Event, Thread
from time import monotonic, time
from typing import Any

from kiwoom_monitor.domain.market_data_contract import MarketDataObservation
from kiwoom_monitor.domain.research_contract import RESEARCH_OBSERVATION_KINDS
from kiwoom_monitor.infrastructure.market_data_metadata_codec import market_metadata_storage_values
from kiwoom_monitor.central_server.database_codec import bounded_limit, dataset_snapshot_result_rows
from kiwoom_monitor.central_server.database_observation_writes import (
    _append_postgres_observation_revision, _append_sqlite_observation_revision,
    _market_metadata_upsert_sql,
)
from kiwoom_monitor.central_server.database_top20_statistics import (
    _top20_statistics_cache_day, _top20_today,
)
from kiwoom_monitor.central_server.postgres_access import (
    DBWriterContext, _postgres_wait_summary, _sample_postgres_backend_waits,
    open_observed_connection,
)

logger = logging.getLogger("kiwoom_monitor.central_server.database")

DatasetSnapshotWrite = tuple[
    str, str, str, dict[str, Any], MarketDataObservation[object] | None,
]

ASYNC_COMMIT_DATASET_KINDS = frozenset({
    "market_state", "new_high", "program_flow", "ranking", "top20_membership",
})

COMMON_OBSERVED_DATASET_KINDS = frozenset({
    "market_state", "new_high", "program_flow", "ranking", "top20_membership",
    "top20_index", "market_index_chart",
    "investor_flow", "stock_fundamentals", "nxt_eligibility",
})

def _uses_async_dataset_commit(values: list[DatasetSnapshotWrite]) -> bool:
    return bool(values) and all(value[0] in ASYNC_COMMIT_DATASET_KINDS for value in values)

class SQLiteDatasetStoreMixin:
    def save_dataset_snapshot(
        self, kind: str, subject: str, snapshot_key: str, payload: dict[str, Any],
        *, observation: MarketDataObservation[object] | None = None,
    ) -> None:
        self.save_dataset_snapshots([(kind, subject, snapshot_key, payload, observation)])

    def save_dataset_snapshots(self, values: list[DatasetSnapshotWrite]) -> None:
        if not values:
            return
        saved_at = time()
        with self._lock, self._connection() as connection:
            for kind, subject, snapshot_key, payload, observation in values:
                connection.execute(
                    "INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(kind,subject,snapshot_key) DO UPDATE SET "
                    "saved_at=excluded.saved_at,payload_json=excluded.payload_json",
                    (kind, subject, snapshot_key, saved_at, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
                )
                cache_day = _top20_statistics_cache_day(kind, subject)
                if cache_day:
                    connection.execute(
                        "DELETE FROM central_dataset_snapshots WHERE kind='top20_statistics_day' "
                        "AND subject=?", (cache_day,),
                    )
                if observation is not None:
                    connection.execute(
                        _market_metadata_upsert_sql("?", "excluded"),
                        market_metadata_storage_values(snapshot_key, observation),
                    )
                    if self._observation_history_enabled and kind in RESEARCH_OBSERVATION_KINDS:
                        _append_sqlite_observation_revision(
                            connection, kind, subject, snapshot_key, payload, observation,
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

class PostgresDatasetStoreMixin:
    def save_dataset_snapshot(
        self, kind: str, subject: str, snapshot_key: str, payload: dict[str, Any],
        *, observation: MarketDataObservation[object] | None = None,
    ) -> None:
        self.save_dataset_snapshots([(kind, subject, snapshot_key, payload, observation)])

    def save_dataset_snapshots(self, values: list[DatasetSnapshotWrite]) -> None:
        if not values:
            return
        snapshot_kinds = {value[0] for value in values}
        writer = None
        if len(snapshot_kinds) == 1 and snapshot_kinds <= COMMON_OBSERVED_DATASET_KINDS:
            from .postgres_access import DBWriterContext, open_observed_connection

            snapshot_kind = next(iter(snapshot_kinds))
            writer = DBWriterContext(
                writer_family="dataset.snapshot",
                writer_kind=f"dataset:{snapshot_kind}",
                operation="save_dataset_snapshots",
                rows_attempted=len(values),
            )
        saved_at = time()
        started_at = monotonic()
        serialized = [
            (kind, subject, snapshot_key, json.dumps(payload, ensure_ascii=False), payload, observation)
            for kind, subject, snapshot_key, payload, observation in values
        ]
        serialized_at = monotonic()
        connection = (
            open_observed_connection(self._connect, writer)
            if writer is not None else self._connect()
        )
        backend_pid = int(getattr(getattr(connection, "info", None), "backend_pid", 0) or 0)
        trace_top20 = backend_pid > 0 and any(value[0] == "top20_membership" for value in values)
        wait_stop = Event()
        wait_samples: list[tuple[str, str, tuple[int, ...]]] = []
        wait_thread = (
            Thread(
                target=_sample_postgres_backend_waits,
                args=(self._database_url, backend_pid, wait_stop, wait_samples),
                name="top20-postgres-wait-probe", daemon=True,
            )
            if trace_top20 else None
        )
        asynchronous_commit = _uses_async_dataset_commit(values)
        connected_at = monotonic()
        commit_ms = 0
        snapshot_at = connected_at
        metadata_at = connected_at
        revision_at = connected_at
        if wait_thread is not None:
            wait_thread.start()
        try:
            with connection.cursor() as cursor:
                if asynchronous_commit:
                    cursor.execute("SET LOCAL synchronous_commit TO OFF")
                historical_cache_days = sorted({
                    day for kind, subject, *_ in serialized
                    if (day := _top20_statistics_cache_day(kind, subject))
                    and day < _top20_today().isoformat()
                })
                for day in historical_cache_days:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(%s,%s)",
                        (902025, int(day.replace("-", ""))),
                    )
                for kind, subject, snapshot_key, payload_json, payload, observation in serialized:
                    cache_day = _top20_statistics_cache_day(kind, subject)
                    cursor.execute(
                        "INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) "
                        "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(kind,subject,snapshot_key) DO UPDATE SET "
                        "saved_at=EXCLUDED.saved_at,payload_json=EXCLUDED.payload_json",
                        (kind, subject, snapshot_key, saved_at, payload_json),
                    )
                    if cache_day:
                        cursor.execute(
                            "DELETE FROM central_dataset_snapshots WHERE kind='top20_statistics_day' "
                            "AND subject=%s", (cache_day,),
                        )
                    if observation is not None:
                        cursor.execute(
                            _market_metadata_upsert_sql("%s", "EXCLUDED"),
                            market_metadata_storage_values(snapshot_key, observation),
                        )
                        if self._observation_history_enabled and kind in RESEARCH_OBSERVATION_KINDS:
                            _append_postgres_observation_revision(
                                cursor, kind, subject, snapshot_key, payload, observation,
                            )
                snapshot_at = monotonic()
                metadata_at = snapshot_at
                revision_at = snapshot_at
            phase_started = monotonic()
            connection.commit()
            commit_ms = round((monotonic() - phase_started) * 1000)
        except BaseException:
            connection.rollback()
            raise
        finally:
            wait_stop.set()
            if wait_thread is not None and wait_thread.is_alive():
                wait_thread.join(timeout=1.0)
            connection.close()
        completed_at = monotonic()
        elapsed_ms = round((completed_at - started_at) * 1000)
        from .diagnostic_metrics import record_writer_transaction
        snapshot_kind = next(iter(snapshot_kinds)) if len(snapshot_kinds) == 1 else "mixed"
        record_writer_transaction(
            f"dataset:{snapshot_kind}", len(values), elapsed_ms,
            commit_ms=commit_ms,
            connect_ms=round((connected_at - serialized_at) * 1000),
            execute_ms=round((revision_at - connected_at) * 1000),
            db_call_id=writer.call_id if writer is not None else None,
        )
        if elapsed_ms >= 1000:
            kinds = ",".join(sorted({value[0] for value in values}))
            logger.warning(
                "slow PostgreSQL dataset snapshot batch save kinds=%s count=%d total_ms=%d "
                "serialize_ms=%d connect_ms=%d snapshot_ms=%d metadata_ms=%d revision_ms=%d commit_close_ms=%d",
                kinds, len(values), elapsed_ms,
                round((serialized_at - started_at) * 1000),
                round((connected_at - serialized_at) * 1000),
                round((snapshot_at - connected_at) * 1000),
                round((metadata_at - snapshot_at) * 1000),
                round((revision_at - metadata_at) * 1000),
                round((completed_at - revision_at) * 1000),
            )
            if trace_top20:
                logger.warning(
                    "PostgreSQL TOP20 blocked commit wait trace backend_pid=%d %s",
                    backend_pid, _postgres_wait_summary(wait_samples),
                )

    def load_dataset_snapshots(self, kind: str, subject: str = "", limit: int = 100) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        started_at = monotonic()
        sql = "SELECT subject,snapshot_key,saved_at,payload_json FROM central_dataset_snapshots WHERE kind=%s"
        parameters: list[object] = [kind]
        if subject:
            sql += " AND subject=%s"
            parameters.append(subject)
        sql += " ORDER BY snapshot_key DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 5000))
        connection_started_at = monotonic()
        context = DBWriterContext(
            writer_family="read.dataset_snapshots",
            writer_kind=f"dataset:{kind}",
            operation="load_dataset_snapshots",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            connected_at = monotonic()
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
            fetched_at = monotonic()
        completed_at = monotonic()
        elapsed_ms = round((completed_at - started_at) * 1000)
        if elapsed_ms >= 1000:
            logger.warning(
                "slow PostgreSQL dataset snapshot load kind=%s subject=%s limit=%d total_ms=%d "
                "prepare_ms=%d connect_ms=%d query_ms=%d commit_close_ms=%d",
                kind, subject, limit, elapsed_ms,
                round((connection_started_at - started_at) * 1000),
                round((connected_at - connection_started_at) * 1000),
                round((fetched_at - connected_at) * 1000),
                round((completed_at - fetched_at) * 1000),
            )
        return dataset_snapshot_result_rows(rows)
