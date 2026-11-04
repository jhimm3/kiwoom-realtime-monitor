"""Observation metadata persistence and queries for the store backends.

Bar and dataset writers keep their caller-owned metadata SQL helpers separately.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from kiwoom_monitor.domain.time_basis import as_kst

from kiwoom_monitor.domain.market_data_contract import (
    CoverageObservation, MarketDataMetadata, MarketDataObservation, MarketDatasetKind,
)
from kiwoom_monitor.infrastructure.market_data_metadata_codec import (
    market_metadata_from_storage_row, market_metadata_storage_values,
)
from kiwoom_monitor.central_server.database_observation_writes import (
    _market_metadata_upsert_sql, _metadata_projection, _validate_metadata_reference, _lock_rank_metadata_write,
)
from .database_codec import _instant_time_key as _metadata_time_key


def _metadata_from_range_row(row: tuple[object, ...]) -> MarketDataMetadata:
    metadata = market_metadata_from_storage_row(_validate_metadata_reference(row[1:]))
    if metadata is None:
        raise ValueError("market metadata row is missing")
    return metadata


def _metadata_query(*, ranged, postgres):
    prefix = "m.observation_key," if ranged else ""
    proof = ",m.revision_id,r.revision_id,r.kind,r.subject,r.observation_key,m.subject,m.observation_key,r.source_id"
    return ("SELECT " + prefix + _metadata_projection() + proof +
            " FROM central_market_data_observation_meta m LEFT JOIN central_observation_revisions r "
            "ON r.revision_id=m.revision_id WHERE m.dataset_kind=" + ("%s" if postgres else "?") +
            " AND m.subject=" + ("%s" if postgres else "?"))


class SQLiteMarketMetadataStoreMixin:
    def save_market_data_metadata(self, observation_key: str, observation: MarketDataObservation[object]) -> None:
        values = market_metadata_storage_values(observation_key, observation)
        with self._lock, self._connection() as connection:
            connection.execute(_market_metadata_upsert_sql("?", "excluded"), values)

    def load_market_data_metadata(self, kind: MarketDatasetKind, subject: str, observation_key: str) -> MarketDataMetadata | None:
        with self._lock, self._connection() as connection:
            row = connection.execute(_metadata_query(ranged=False, postgres=False) +
                                     " AND m.observation_key=?", (kind.value, subject, observation_key)).fetchone()
        return market_metadata_from_storage_row(None if row is None else _validate_metadata_reference(row))

    def load_market_data_metadata_range(self, kind: MarketDatasetKind, subject: str, start: datetime, end: datetime) -> list[CoverageObservation]:
        with self._lock, self._connection() as connection:
            connection.create_function("metadata_time_key", 1, _metadata_time_key, deterministic=True)
            lower = (as_kst(start).astimezone(UTC) - timedelta(days=1)).replace(tzinfo=None).isoformat(timespec="microseconds")
            upper = (as_kst(end).astimezone(UTC) + timedelta(days=1)).replace(tzinfo=None).isoformat(timespec="microseconds")
            rows = connection.execute(
                _metadata_query(ranged=True, postgres=False) +
                " AND m.effective_at>=? AND m.effective_at<? "
                "AND metadata_time_key(m.effective_at)>=? AND metadata_time_key(m.effective_at)<? "
                "ORDER BY metadata_time_key(m.effective_at)",
                (kind.value, subject, lower, upper, _metadata_time_key(start), _metadata_time_key(end)),
            ).fetchall()
        return [CoverageObservation(str(row[0]), _metadata_from_range_row(row)) for row in rows]


class PostgresMarketMetadataStoreMixin:
    def save_market_data_metadata(self, observation_key: str, observation: MarketDataObservation[object]) -> None:
        values = market_metadata_storage_values(observation_key, observation)
        with self._connect() as connection, connection.cursor() as cursor:
            _lock_rank_metadata_write(cursor, observation_key, observation)
            cursor.execute(_market_metadata_upsert_sql("%s", "EXCLUDED"), values)

    def load_market_data_metadata(self, kind: MarketDatasetKind, subject: str, observation_key: str) -> MarketDataMetadata | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(_metadata_query(ranged=False, postgres=True) + " AND m.observation_key=%s",
                           (kind.value, subject, observation_key))
            row = cursor.fetchone()
        return market_metadata_from_storage_row(None if row is None else _validate_metadata_reference(row))

    def load_market_data_metadata_range(self, kind: MarketDatasetKind, subject: str, start: datetime, end: datetime) -> list[CoverageObservation]:
        from .postgres_access import DBWriterContext, open_observed_connection
        context = DBWriterContext(writer_family="read.market_data_metadata", writer_kind="metadata_range",
                                  operation="load_market_data_metadata_range", access_mode="read")
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
            cursor.execute(_metadata_query(ranged=True, postgres=True) +
                           " AND m.effective_at>=%s AND m.effective_at<%s ORDER BY m.effective_at",
                           (kind.value, subject, start, end))
            rows = cursor.fetchall()
        return [CoverageObservation(str(row[0]), _metadata_from_range_row(row)) for row in rows]
