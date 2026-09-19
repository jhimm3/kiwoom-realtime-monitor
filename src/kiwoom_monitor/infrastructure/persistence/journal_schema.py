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


JOURNAL_SCHEMA_VERSION = 9


def _create_journal_sync_state_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS journal_sync_states ("
        "collection TEXT NOT NULL, document_key TEXT NOT NULL, is_deleted INTEGER NOT NULL, "
        "updated_at TEXT NOT NULL, PRIMARY KEY(collection, document_key))"
    )


def _create_legacy_import_ledger(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS journal_legacy_imports ("
        "source_collection TEXT NOT NULL, source_key TEXT NOT NULL, source_revision TEXT NOT NULL, "
        "target_collection TEXT NOT NULL, target_key TEXT NOT NULL, "
        "imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "PRIMARY KEY(source_collection,source_key,source_revision))"
    )


def _migrate_legacy_import_provenance_v8(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE journal_legacy_imports RENAME TO journal_legacy_imports_v7")
    connection.execute(
        "CREATE TABLE journal_legacy_imports ("
        "source_collection TEXT NOT NULL,source_owner TEXT NOT NULL,source_key TEXT NOT NULL,"
        "source_content_hash TEXT NOT NULL,source_observed_at TEXT NOT NULL DEFAULT '',"
        "source_modified_at TEXT NOT NULL DEFAULT '',target_collection TEXT NOT NULL,"
        "target_key TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'IMPORTED',"
        "imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY("
        "source_collection,source_owner,source_key,source_content_hash))"
    )
    connection.execute(
        "INSERT INTO journal_legacy_imports("
        "source_collection,source_owner,source_key,source_content_hash,source_modified_at,"
        "target_collection,target_key,status,imported_at) SELECT source_collection,'unknown',"
        "source_key,'unknown',source_revision,target_collection,target_key,'IMPORTED',imported_at "
        "FROM journal_legacy_imports_v7"
    )
    connection.execute("DROP TABLE journal_legacy_imports_v7")
    connection.execute("ALTER TABLE journal_sync_states RENAME TO journal_sync_states_v7")
    connection.execute(
        "CREATE TABLE journal_sync_states ("
        "collection TEXT NOT NULL,owner TEXT NOT NULL,document_key TEXT NOT NULL,"
        "origin_broker TEXT NOT NULL,origin_environment TEXT NOT NULL,"
        "origin_account_ref TEXT NOT NULL,canonical_account_ref TEXT NOT NULL,"
        "is_deleted INTEGER NOT NULL,updated_at TEXT NOT NULL,"
        "PRIMARY KEY(collection,owner,document_key))"
    )
    connection.execute(
        "INSERT INTO journal_sync_states SELECT collection,"
        "CASE WHEN collection NOT LIKE 'journal_v2_%' THEN 'legacy' ELSE 'unknown' END,"
        "document_key,"
        "CASE WHEN collection NOT LIKE 'journal_v2_%' THEN 'legacy' ELSE 'unknown' END,"
        "'unknown',"
        "CASE WHEN collection NOT LIKE 'journal_v2_%' THEN 'legacy-unassigned' ELSE 'unknown' END,"
        "CASE WHEN collection NOT LIKE 'journal_v2_%' THEN 'legacy-unassigned' ELSE 'unknown' END,"
        "is_deleted,updated_at FROM journal_sync_states_v7"
    )
    connection.execute("DROP TABLE journal_sync_states_v7")


def _create_execution_projection_ledger_v9(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE journal_execution_projection_states ("
        "projection_name TEXT NOT NULL,origin_broker TEXT NOT NULL,"
        "origin_environment TEXT NOT NULL,origin_account_ref TEXT NOT NULL,"
        "canonical_account_ref TEXT NOT NULL,cursor INTEGER NOT NULL,updated_at TEXT NOT NULL,"
        "PRIMARY KEY(projection_name,origin_broker,origin_environment,origin_account_ref))"
    )
    connection.execute(
        "CREATE TABLE journal_execution_event_projections ("
        "origin_broker TEXT NOT NULL,origin_environment TEXT NOT NULL,"
        "origin_account_ref TEXT NOT NULL,canonical_account_ref TEXT NOT NULL,"
        "projection_name TEXT NOT NULL,source_event_id TEXT NOT NULL,"
        "accepted_sequence INTEGER NOT NULL,event_type TEXT NOT NULL,trading_date TEXT NOT NULL,"
        "intent_id TEXT NOT NULL,run_id TEXT NOT NULL,decision_id TEXT NOT NULL,"
        "broker_order_id TEXT NOT NULL,broker_execution_id TEXT NOT NULL,"
        "stock_code TEXT NOT NULL,venue TEXT NOT NULL,side TEXT NOT NULL,"
        "occurred_at TEXT NOT NULL,received_at TEXT NOT NULL,quantity INTEGER NOT NULL,"
        "price INTEGER NOT NULL,broker_as_of TEXT,fill_identity TEXT,content_hash TEXT NOT NULL,"
        "projected_at TEXT NOT NULL,PRIMARY KEY("
        "origin_broker,origin_environment,origin_account_ref,source_event_id),UNIQUE("
        "origin_broker,origin_environment,origin_account_ref,fill_identity))"
    )
    connection.execute(
        "CREATE INDEX idx_journal_execution_projection_order ON "
        "journal_execution_event_projections(origin_broker,origin_environment,"
        "origin_account_ref,intent_id,accepted_sequence)"
    )


def _create_journal_enrichment_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS journal_enrichment_tasks ("
        "task_id TEXT PRIMARY KEY,target_type TEXT NOT NULL,target_ref TEXT NOT NULL,"
        "kind TEXT NOT NULL,input_fingerprint TEXT NOT NULL,policy_version TEXT NOT NULL,"
        "state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_retry_at TEXT,"
        "owner TEXT NOT NULL DEFAULT '',result_json TEXT NOT NULL DEFAULT '{}',"
        "last_error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
        "started_at TEXT,completed_at TEXT,"
        "UNIQUE(target_type,target_ref,kind,input_fingerprint,policy_version))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_journal_enrichment_due "
        "ON journal_enrichment_tasks(state,next_retry_at,updated_at)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS journal_analysis_revisions ("
        "revision_id TEXT PRIMARY KEY,group_id TEXT NOT NULL,analysis_kind TEXT NOT NULL,"
        "input_fingerprint TEXT NOT NULL,analysis_version TEXT NOT NULL,content_json TEXT NOT NULL,"
        "created_at TEXT NOT NULL,UNIQUE(group_id,analysis_kind,input_fingerprint,analysis_version))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS journal_research_links ("
        "link_id TEXT PRIMARY KEY,execution_ref TEXT NOT NULL,run_id TEXT,decision_id TEXT,"
        "snapshot_id TEXT,entry_thesis_id TEXT,evidence_timing TEXT NOT NULL,"
        "available_at TEXT,created_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_journal_research_links_execution "
        "ON journal_research_links(execution_ref,created_at)"
    )


def _migrate_account_scoped_trade_ledger(connection: sqlite3.Connection) -> None:
    """기존 체결·비용 키를 보존하며 계좌 범위를 PK에 포함한다."""
    connection.execute("ALTER TABLE trade_fills RENAME TO trade_fills_v4")
    connection.execute("DROP INDEX IF EXISTS idx_trade_fills_time")
    connection.execute(
        "CREATE TABLE trade_fills ("
        "fill_key TEXT PRIMARY KEY,origin_broker TEXT NOT NULL,origin_environment TEXT NOT NULL,"
        "origin_account_ref TEXT NOT NULL,canonical_account_ref TEXT NOT NULL,"
        "order_no TEXT NOT NULL,stock_code TEXT NOT NULL,stock_name TEXT NOT NULL,side TEXT NOT NULL,"
        "filled_at TEXT NOT NULL,quantity INTEGER NOT NULL,price INTEGER NOT NULL,"
        "order_type TEXT NOT NULL DEFAULT '',market TEXT NOT NULL DEFAULT '',"
        "UNIQUE(origin_broker,origin_environment,origin_account_ref,order_no,stock_code,filled_at,side))"
    )
    connection.execute(
        "INSERT INTO trade_fills("
        "fill_key,origin_broker,origin_environment,origin_account_ref,canonical_account_ref,"
        "order_no,stock_code,stock_name,side,filled_at,quantity,price,order_type,market) "
        "SELECT order_no||'|'||stock_code||'|'||filled_at||'|'||side,"
        "'legacy','unknown','legacy-unassigned','legacy-unassigned',"
        "order_no,stock_code,stock_name,side,filled_at,quantity,price,order_type,market "
        "FROM trade_fills_v4"
    )
    connection.execute("DROP TABLE trade_fills_v4")
    connection.execute(
        "CREATE INDEX idx_trade_fills_time ON "
        "trade_fills(canonical_account_ref,filled_at DESC)"
    )

    connection.execute("ALTER TABLE daily_trade_costs RENAME TO daily_trade_costs_v4")
    connection.execute(
        "CREATE TABLE daily_trade_costs ("
        "origin_broker TEXT NOT NULL,origin_environment TEXT NOT NULL,origin_account_ref TEXT NOT NULL,"
        "canonical_account_ref TEXT NOT NULL,fill_date TEXT NOT NULL,settlement_date TEXT NOT NULL,"
        "stock_code TEXT NOT NULL,side TEXT NOT NULL,gross_amount INTEGER NOT NULL,"
        "settlement_amount INTEGER NOT NULL,commission INTEGER NOT NULL,tax INTEGER NOT NULL,"
        "total_cost INTEGER NOT NULL,confirmed_at TEXT NOT NULL,"
        "PRIMARY KEY(origin_broker,origin_environment,origin_account_ref,fill_date,stock_code,side))"
    )
    connection.execute(
        "INSERT INTO daily_trade_costs("
        "origin_broker,origin_environment,origin_account_ref,canonical_account_ref,fill_date,"
        "settlement_date,stock_code,side,gross_amount,settlement_amount,commission,tax,total_cost,confirmed_at) "
        "SELECT 'legacy','unknown','legacy-unassigned','legacy-unassigned',fill_date,settlement_date,"
        "stock_code,side,gross_amount,settlement_amount,commission,tax,total_cost,confirmed_at "
        "FROM daily_trade_costs_v4"
    )
    connection.execute("DROP TABLE daily_trade_costs_v4")
    connection.execute(
        "CREATE INDEX idx_daily_trade_costs_scope_date ON "
        "daily_trade_costs(canonical_account_ref,fill_date)"
    )


def _migrate_account_scoped_journal_artifacts(connection: sqlite3.Connection) -> None:
    """기존 참조 키를 유지하며 스냅샷·복기·분석 산출물에 계좌 범위를 붙인다."""
    tables = (
        "trade_group_overrides",
        "trade_reviews",
        "trade_setup_classifications",
        "trade_setup_cycle_overrides",
        "trade_entry_snapshots",
        "journal_enrichment_tasks",
        "journal_analysis_revisions",
        "journal_research_links",
    )
    for table in tables:
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN origin_broker TEXT NOT NULL DEFAULT 'legacy'"
        )
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN origin_environment TEXT NOT NULL DEFAULT 'unknown'"
        )
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN origin_account_ref TEXT NOT NULL DEFAULT 'legacy-unassigned'"
        )
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN canonical_account_ref TEXT NOT NULL DEFAULT 'legacy-unassigned'"
        )
    connection.execute("ALTER TABLE journal_enrichment_tasks RENAME TO journal_enrichment_tasks_v5")
    connection.execute("DROP INDEX IF EXISTS idx_journal_enrichment_due")
    connection.execute(
        "CREATE TABLE journal_enrichment_tasks ("
        "task_id TEXT PRIMARY KEY,target_type TEXT NOT NULL,target_ref TEXT NOT NULL,"
        "kind TEXT NOT NULL,input_fingerprint TEXT NOT NULL,policy_version TEXT NOT NULL,"
        "state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_retry_at TEXT,"
        "owner TEXT NOT NULL DEFAULT '',result_json TEXT NOT NULL DEFAULT '{}',"
        "last_error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
        "started_at TEXT,completed_at TEXT,origin_broker TEXT NOT NULL,"
        "origin_environment TEXT NOT NULL,origin_account_ref TEXT NOT NULL,"
        "canonical_account_ref TEXT NOT NULL,UNIQUE("
        "origin_broker,origin_environment,origin_account_ref,target_type,target_ref,kind,"
        "input_fingerprint,policy_version))"
    )
    connection.execute(
        "INSERT INTO journal_enrichment_tasks SELECT * FROM journal_enrichment_tasks_v5"
    )
    connection.execute("DROP TABLE journal_enrichment_tasks_v5")
    connection.execute(
        "CREATE INDEX idx_journal_enrichment_due ON "
        "journal_enrichment_tasks(state,next_retry_at,updated_at)"
    )

    connection.execute("ALTER TABLE journal_analysis_revisions RENAME TO journal_analysis_revisions_v5")
    connection.execute(
        "CREATE TABLE journal_analysis_revisions ("
        "revision_id TEXT PRIMARY KEY,group_id TEXT NOT NULL,analysis_kind TEXT NOT NULL,"
        "input_fingerprint TEXT NOT NULL,analysis_version TEXT NOT NULL,content_json TEXT NOT NULL,"
        "created_at TEXT NOT NULL,origin_broker TEXT NOT NULL,origin_environment TEXT NOT NULL,"
        "origin_account_ref TEXT NOT NULL,canonical_account_ref TEXT NOT NULL,UNIQUE("
        "origin_broker,origin_environment,origin_account_ref,group_id,analysis_kind,"
        "input_fingerprint,analysis_version))"
    )
    connection.execute(
        "INSERT INTO journal_analysis_revisions SELECT * FROM journal_analysis_revisions_v5"
    )
    connection.execute("DROP TABLE journal_analysis_revisions_v5")
    connection.execute(
        "CREATE INDEX idx_trade_entry_snapshots_scope_time ON trade_entry_snapshots("
        "canonical_account_ref,origin_broker,origin_environment,stock_code,executed_at)"
    )
    connection.execute(
        "CREATE INDEX idx_trade_reviews_scope ON trade_reviews("
        "canonical_account_ref,origin_broker,origin_environment,group_id)"
    )
    connection.execute(
        "CREATE INDEX idx_journal_enrichment_scope_due ON journal_enrichment_tasks("
        "canonical_account_ref,origin_broker,origin_environment,state,next_retry_at,updated_at)"
    )
    connection.execute(
        "CREATE INDEX idx_journal_analysis_scope_group ON journal_analysis_revisions("
        "canonical_account_ref,origin_broker,origin_environment,group_id,created_at)"
    )
    connection.execute(
        "CREATE INDEX idx_journal_research_scope_execution ON journal_research_links("
        "canonical_account_ref,origin_broker,origin_environment,execution_ref,created_at)"
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
                    SQLiteMigration(
                        4,
                        "journal_enrichment_ledger",
                        _create_journal_enrichment_tables,
                    ),
                    SQLiteMigration(
                        5,
                        "account_scoped_trade_ledger",
                        _migrate_account_scoped_trade_ledger,
                    ),
                    SQLiteMigration(
                        6,
                        "account_scoped_journal_artifacts",
                        _migrate_account_scoped_journal_artifacts,
                    ),
                    SQLiteMigration(
                        7,
                        "legacy_import_provenance",
                        _create_legacy_import_ledger,
                    ),
                    SQLiteMigration(
                        8,
                        "strict_legacy_import_provenance",
                        _migrate_legacy_import_provenance_v8,
                    ),
                    SQLiteMigration(
                        9,
                        "execution_event_projection_ledger",
                        _create_execution_projection_ledger_v9,
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
