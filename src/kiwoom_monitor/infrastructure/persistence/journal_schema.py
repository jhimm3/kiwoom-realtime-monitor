"""매매일지 SQLite 스키마 생성과 호환성 복구."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from kiwoom_monitor.infrastructure.persistence.market_data_metadata_schema import (
    create_market_data_metadata_table,
)
from kiwoom_monitor.infrastructure.persistence.schema_migrations import (
    SQLiteMigration,
    SQLiteMigrationRunner,
)


JOURNAL_SCHEMA_VERSION = 3


def _create_journal_sync_state_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS journal_sync_states ("
        "collection TEXT NOT NULL, document_key TEXT NOT NULL, is_deleted INTEGER NOT NULL, "
        "updated_at TEXT NOT NULL, PRIMARY KEY(collection, document_key))"
    )

def initialize_journal_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            migrations = SQLiteMigrationRunner(connection, "journal_schema_migrations")
            migrations.prepare()
            migrations.ensure_compatible(JOURNAL_SCHEMA_VERSION)
            connection.executescript("""
        CREATE TABLE IF NOT EXISTS journal_minute_bars (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL, minute TEXT NOT NULL,
            open_price INTEGER NOT NULL, high_price INTEGER NOT NULL,
            low_price INTEGER NOT NULL, close_price INTEGER NOT NULL,
            volume INTEGER NOT NULL, trade_value_eok REAL NOT NULL,
            source TEXT NOT NULL, confirmed_at TEXT,
            PRIMARY KEY(trade_date, stock_code, minute)
        );
        CREATE INDEX IF NOT EXISTS idx_journal_bars_code_date
            ON journal_minute_bars(stock_code, trade_date, minute);
        CREATE TABLE IF NOT EXISTS journal_stocks (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT 'opened', last_opened_at TEXT NOT NULL,
            PRIMARY KEY(trade_date, stock_code)
        );
        CREATE TABLE IF NOT EXISTS trade_fills (
            order_no TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT NOT NULL,
            side TEXT NOT NULL, filled_at TEXT NOT NULL, quantity INTEGER NOT NULL,
            price INTEGER NOT NULL, order_type TEXT NOT NULL DEFAULT '', market TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(order_no, stock_code, filled_at, side)
        );
        CREATE INDEX IF NOT EXISTS idx_trade_fills_time ON trade_fills(filled_at DESC);
        CREATE TABLE IF NOT EXISTS trade_group_overrides (
            fill_key TEXT PRIMARY KEY, group_id TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trade_reviews (
            group_id TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '',
            review TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '',
            rating TEXT NOT NULL DEFAULT '보통', status TEXT NOT NULL DEFAULT '미작성',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_trade_costs (
            fill_date TEXT NOT NULL, settlement_date TEXT NOT NULL,
            stock_code TEXT NOT NULL, side TEXT NOT NULL,
            gross_amount INTEGER NOT NULL, settlement_amount INTEGER NOT NULL,
            commission INTEGER NOT NULL, tax INTEGER NOT NULL, total_cost INTEGER NOT NULL,
            confirmed_at TEXT NOT NULL,
            PRIMARY KEY(fill_date, stock_code, side)
        );
        CREATE TABLE IF NOT EXISTS journal_bar_backfill (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL,
            state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
            PRIMARY KEY(trade_date, stock_code)
        );
        CREATE TABLE IF NOT EXISTS journal_daily_bars (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL,
            open_price INTEGER NOT NULL, high_price INTEGER NOT NULL,
            low_price INTEGER NOT NULL, close_price INTEGER NOT NULL,
            volume INTEGER NOT NULL, trade_value_eok REAL NOT NULL,
            confirmed_at TEXT NOT NULL,
            PRIMARY KEY(trade_date, stock_code)
        );
        CREATE TABLE IF NOT EXISTS journal_settings (
            setting_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trade_setup_classifications (
            group_id TEXT PRIMARY KEY, automatic_type TEXT NOT NULL, confidence INTEGER NOT NULL,
            evidence_json TEXT NOT NULL, manual_type TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trade_setup_cycle_overrides (
            group_id TEXT NOT NULL, cycle_index INTEGER NOT NULL,
            manual_type TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
            PRIMARY KEY(group_id, cycle_index)
        );
        CREATE TABLE IF NOT EXISTS trade_entry_snapshots (
            execution_key TEXT PRIMARY KEY, order_no TEXT NOT NULL,
            stock_code TEXT NOT NULL, stock_name TEXT NOT NULL, side TEXT NOT NULL,
            executed_at TEXT NOT NULL, price INTEGER NOT NULL, quantity INTEGER NOT NULL,
            market TEXT NOT NULL DEFAULT '', rank INTEGER,
            trade_value_1m_eok REAL, trade_value_5m_eok REAL,
            themes_json TEXT NOT NULL DEFAULT '[]', theme_ranks_json TEXT NOT NULL DEFAULT '{}',
            high_distance_percent REAL, news_json TEXT NOT NULL DEFAULT '[]',
            investor_flow_json TEXT NOT NULL DEFAULT '{}', orderbook_json TEXT NOT NULL DEFAULT '{}',
            market_state_json TEXT NOT NULL DEFAULT '{}', capture_state TEXT NOT NULL,
            captured_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_trade_entry_snapshots_time
            ON trade_entry_snapshots(stock_code, executed_at);
            """)
            _repair_legacy_alpha_cost_codes(connection)
            migrations.record_applied(1, "current_journal_schema_baseline")
            migrations.apply(
                (
                    SQLiteMigration(1, "current_journal_schema_baseline", lambda _: None),
                    SQLiteMigration(
                        2,
                        "market_data_observation_metadata",
                        create_market_data_metadata_table,
                    ),
                    SQLiteMigration(
                        3,
                        "journal_sync_deletion_states",
                        _create_journal_sync_state_table,
                    ),
                )
            )


def _repair_legacy_alpha_cost_codes(connection: sqlite3.Connection) -> None:
    """과거 버전이 내부 영문자를 제거해 저장한 비용 종목코드를 복구한다."""
    mappings = connection.execute(
        "SELECT DISTINCT substr(filled_at,1,10), stock_code FROM trade_fills "
        "WHERE stock_code GLOB '*[A-Z]*'"
    ).fetchall()
    for fill_date, stock_code in mappings:
        legacy_code = "".join(character for character in str(stock_code) if character.isdigit())
        if not legacy_code or legacy_code == stock_code:
            continue
        connection.execute(
            "UPDATE OR REPLACE daily_trade_costs SET stock_code=? WHERE fill_date=? AND stock_code=?",
            (stock_code, fill_date, legacy_code),
        )
