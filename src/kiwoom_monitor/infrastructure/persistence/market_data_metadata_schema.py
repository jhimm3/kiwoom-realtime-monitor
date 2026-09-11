"""시장 관측 메타데이터의 로컬 SQLite 스키마."""

from __future__ import annotations

import sqlite3


MARKET_DATA_METADATA_TABLE = "market_data_observation_meta"


def create_market_data_metadata_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {MARKET_DATA_METADATA_TABLE} (
            dataset_kind TEXT NOT NULL,
            subject TEXT NOT NULL,
            observation_key TEXT NOT NULL,
            effective_at TEXT,
            available_at TEXT,
            venue TEXT NOT NULL,
            unit TEXT NOT NULL,
            value_kind TEXT NOT NULL,
            completeness TEXT NOT NULL,
            origin TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            candidate_universe TEXT NOT NULL,
            PRIMARY KEY(dataset_kind, subject, observation_key)
        )
        """
    )
