"""Observation metadata persistence and queries for the store backends.

Bar and dataset writers keep their caller-owned metadata SQL helpers separately.
"""
from __future__ import annotations

from datetime import datetime

from kiwoom_monitor.domain.market_data_contract import (
    CoverageObservation, MarketDataMetadata, MarketDataObservation, MarketDatasetKind,
)
from kiwoom_monitor.infrastructure.market_data_metadata_codec import (
    market_metadata_from_storage_row, market_metadata_storage_values,
)
from kiwoom_monitor.central_server.database_observation_writes import _market_metadata_upsert_sql


def _metadata_from_range_row(row: tuple[object, ...]) -> MarketDataMetadata:
    metadata = market_metadata_from_storage_row(tuple(row[1:]))
    if metadata is None:
        raise ValueError("market metadata row is missing")
    return metadata


class SQLiteMarketMetadataStoreMixin:
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


class PostgresMarketMetadataStoreMixin:
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
        from .postgres_access import DBWriterContext, open_observed_connection

        context = DBWriterContext(
            writer_family="read.market_data_metadata",
            writer_kind="metadata_range",
            operation="load_market_data_metadata_range",
            access_mode="read",
        )
        with open_observed_connection(self._connect, context) as connection, connection.cursor() as cursor:
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
