"""값을 바꾸지 않고 시장 관측의 시각·출처 의미를 별도로 보존한다."""

from __future__ import annotations

from pathlib import Path

from kiwoom_monitor.domain.market_data_contract import (
    MarketDataMetadata,
    MarketDataObservation,
    MarketDatasetKind,
)
from kiwoom_monitor.infrastructure.market_data_metadata_codec import (
    market_metadata_from_storage_row,
    market_metadata_storage_values,
)
from kiwoom_monitor.infrastructure.persistence.market_data_metadata_schema import (
    MARKET_DATA_METADATA_TABLE,
)
from kiwoom_monitor.infrastructure.persistence.sqlite_connections import (
    sqlite_read_connection,
    sqlite_transaction,
)


def upsert_market_data_metadata(
    connection: object,
    observation_key: str,
    observation: MarketDataObservation[object],
    *,
    preserve_complete: bool = False,
    preserve_backfilled: bool = False,
) -> None:
    """열려 있는 저장 트랜잭션에 관측 의미를 함께 기록한다."""
    key = str(observation_key).strip()
    if not key:
        raise ValueError("observation_key is required")
    guards: list[str] = []
    if preserve_complete:
        guards.append(
            f"({MARKET_DATA_METADATA_TABLE}.completeness!='complete' "
            "OR excluded.completeness='complete')"
        )
    if preserve_backfilled:
        guards.append(
            f"({MARKET_DATA_METADATA_TABLE}.origin!='backfilled' "
            "OR excluded.origin='backfilled')"
        )
    guard = f" WHERE {' AND '.join(guards)}" if guards else ""
    connection.execute(
        f"""
        INSERT INTO {MARKET_DATA_METADATA_TABLE} (
            dataset_kind, subject, observation_key, effective_at, available_at,
            venue, unit, value_kind, completeness, origin, source, candidate_universe
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dataset_kind, subject, observation_key) DO UPDATE SET
            effective_at=excluded.effective_at,
            available_at=excluded.available_at,
            venue=excluded.venue,
            unit=excluded.unit,
            value_kind=excluded.value_kind,
            completeness=excluded.completeness,
            origin=excluded.origin,
            source=excluded.source,
            candidate_universe=excluded.candidate_universe
        {guard}
        """,
        market_metadata_storage_values(key, observation),
    )


class MarketDataMetadataRepository:
    """메인/매매일지 DB가 공유하는 관측 메타데이터 저장소."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path

    def save(self, observation_key: str, observation: MarketDataObservation[object]) -> None:
        with sqlite_transaction(self._database_path) as connection:
            upsert_market_data_metadata(connection, observation_key, observation)

    def load(
        self,
        kind: MarketDatasetKind,
        subject: str,
        observation_key: str,
    ) -> MarketDataMetadata | None:
        with sqlite_read_connection(self._database_path) as connection:
            row = connection.execute(
                f"""
                SELECT effective_at, available_at, venue, unit, value_kind,
                       completeness, origin, source, candidate_universe
                FROM {MARKET_DATA_METADATA_TABLE}
                WHERE dataset_kind=? AND subject=? AND observation_key=?
                """,
                (kind.value, subject, observation_key),
            ).fetchone()
        return market_metadata_from_storage_row(row)
