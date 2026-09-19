from __future__ import annotations


CENTRAL_TABLES = (
    "central_api_query_cache",
    "central_realtime_latest",
    "central_minute_bars",
    "central_second_trade_bars",
    "central_daily_bars",
    "central_dataset_snapshots",
    "central_market_data_observation_meta",
    "central_observation_revisions",
    "central_research_exports",
    "central_research_export_members",
    "central_minute_bar_operations",
    "central_shadow_monitor_state",
    "central_shadow_decisions",
    "central_shadow_candidate_events",
    "central_theme_snapshots",
    "central_news_article_revisions",
    "central_news_body_revisions",
    "central_news_ai_revisions",
    "central_news_event_revisions",
    "central_news_event_membership_revisions",
    "central_news_source_cursors",
    "central_news_source_runs",
    "central_news_source_observations",
    "central_news_article_target_revisions",
    "central_news_request_budget",
    "central_news_jobs",
    "central_vi_event_revisions",
    "central_hot_cohort_current",
    "central_hot_cohort_revisions",
    "central_upper_limit_fact_revisions",
    "central_documents",
    "central_external_bars",
    "central_execution_intents",
    "central_execution_events",
    "central_execution_account_snapshots",
    "central_execution_runtime_leases",
    "central_account_registry",
    "central_account_binding_revisions",
    "central_account_scope_aliases",
    "central_credential_profiles",
    "central_credential_activations",
)
CENTRAL_INDEXES = (
    "idx_central_api_query_expiry",
    "idx_central_minute_bars_lookup",
    "idx_central_second_trade_bars_lookup",
    "idx_central_dataset_lookup",
    "idx_central_observation_lookup",
    "idx_central_observation_available",
    "idx_central_shadow_candidate_available",
    "idx_central_theme_snapshot_available",
    "idx_central_news_article_lookup",
    "idx_central_news_body_lookup",
    "idx_central_news_ai_lookup",
    "idx_central_news_event_lookup",
    "idx_central_news_membership_lookup",
    "idx_central_news_source_runs_lookup",
    "idx_central_news_source_observations_lookup",
    "idx_central_news_source_identity_lookup",
    "idx_central_news_article_targets_lookup",
    "idx_central_news_targets_stock_lookup",
    "idx_central_news_jobs_ready",
    "idx_central_vi_event_lookup",
    "idx_central_hot_cohort_lookup",
    "idx_central_upper_limit_lookup",
    "idx_central_documents_lookup",
    "idx_central_external_bars_lookup",
    "idx_central_execution_intents_scope",
    "idx_central_execution_events_intent",
    "idx_central_execution_account_snapshots",
    "idx_central_account_binding_latest",
    "idx_central_account_scope_alias_target",
)

CENTRAL_SCHEMA_VERSION = 19
CENTRAL_SCHEMA_BASELINE_NAME = "current_central_storage_baseline"
CENTRAL_MARKET_METADATA_MIGRATION_NAME = "market_data_observation_metadata"
CENTRAL_MARKET_STATE_TIME_REPAIR_MIGRATION_NAME = "repair_market_state_special_trade_time"
CENTRAL_SECOND_TRADE_BARS_MIGRATION_NAME = "second_trade_bars"
CENTRAL_THEME_HISTORY_MIGRATION_NAME = "theme_snapshot_history"
CENTRAL_NEWS_HISTORY_MIGRATION_NAME = "news_observation_jobs"
CENTRAL_NEWS_EVENT_MIGRATION_NAME = "supply_contract_event_history"
CENTRAL_NEWS_SOURCE_MIGRATION_NAME = "query_set_news_sources"
CENTRAL_MARKET_EVENT_MIGRATION_NAME = "vi_condition_hot_cohort"
CENTRAL_NEWS_REUSE_MIGRATION_NAME = "news_source_revision_reuse"
CENTRAL_N3_STOCK_NEWS_MIGRATION_NAME = "n3_confirmed_stock_news_lookup"
CENTRAL_OBSERVATION_HISTORY_MIGRATION_NAME = "ranking_observation_history"
CENTRAL_RESEARCH_EXPORT_MIGRATION_NAME = "fixed_research_observation_exports"
CENTRAL_MINUTE_BAR_REVISION_MIGRATION_NAME = "minute_bar_revision_history"
CENTRAL_SHADOW_CANDIDATE_MIGRATION_NAME = "shadow_candidate_ledger"
CENTRAL_MOCK_EXECUTION_MIGRATION_NAME = "mock_execution_ledger"
CENTRAL_ACCOUNT_IDENTITY_MIGRATION_NAME = "verified_account_identity_registry"
CENTRAL_ACCOUNT_SCOPE_ALIAS_MIGRATION_NAME = "verified_account_scope_aliases"
CENTRAL_CREDENTIAL_ACTIVATION_MIGRATION_NAME = "encrypted_credential_activation_ledger"


def sqlite_schema_statements() -> tuple[str, ...]:
    return (
        "CREATE TABLE IF NOT EXISTS central_api_query_cache ("
        "cache_key TEXT PRIMARY KEY, api_id TEXT NOT NULL, expires_at REAL NOT NULL, "
        "payload_json TEXT NOT NULL, has_next INTEGER NOT NULL, next_key TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_central_api_query_expiry "
        "ON central_api_query_cache(expires_at)",
        "CREATE TABLE IF NOT EXISTS central_realtime_latest ("
        "event_type TEXT NOT NULL, item_key TEXT NOT NULL, received_at REAL NOT NULL, "
        "event_json TEXT NOT NULL, PRIMARY KEY(event_type,item_key))",
        "CREATE TABLE IF NOT EXISTS central_minute_bars ("
        "trading_date TEXT NOT NULL, minute TEXT NOT NULL, code TEXT NOT NULL, market TEXT NOT NULL, "
        "open INTEGER NOT NULL, high INTEGER NOT NULL, low INTEGER NOT NULL, close INTEGER NOT NULL, "
        "volume INTEGER NOT NULL, trade_value_million_won INTEGER NOT NULL, updated_at REAL NOT NULL, "
        "PRIMARY KEY(trading_date,minute,code,market))",
        "CREATE INDEX IF NOT EXISTS idx_central_minute_bars_lookup "
        "ON central_minute_bars(code,trading_date,minute)",
        "CREATE TABLE IF NOT EXISTS central_daily_bars ("
        "trading_date TEXT NOT NULL, code TEXT NOT NULL, market TEXT NOT NULL, "
        "open INTEGER NOT NULL, high INTEGER NOT NULL, low INTEGER NOT NULL, close INTEGER NOT NULL, "
        "volume INTEGER NOT NULL, trade_value_million_won INTEGER, updated_at REAL NOT NULL, "
        "PRIMARY KEY(trading_date,code,market))",
        "CREATE TABLE IF NOT EXISTS central_dataset_snapshots ("
        "kind TEXT NOT NULL, subject TEXT NOT NULL, snapshot_key TEXT NOT NULL, "
        "saved_at REAL NOT NULL, payload_json TEXT NOT NULL, "
        "PRIMARY KEY(kind,subject,snapshot_key))",
        "CREATE INDEX IF NOT EXISTS idx_central_dataset_lookup "
        "ON central_dataset_snapshots(kind,subject,snapshot_key DESC)",
        "CREATE TABLE IF NOT EXISTS central_documents ("
        "collection TEXT NOT NULL, owner TEXT NOT NULL, document_key TEXT NOT NULL, "
        "updated_at REAL NOT NULL, document_json TEXT NOT NULL, "
        "PRIMARY KEY(collection,owner,document_key))",
        "CREATE INDEX IF NOT EXISTS idx_central_documents_lookup "
        "ON central_documents(collection,owner,updated_at DESC)",
        "CREATE TABLE IF NOT EXISTS central_external_bars ("
        "provider TEXT NOT NULL, instrument TEXT NOT NULL, contract TEXT NOT NULL, "
        "timeframe TEXT NOT NULL, bar_time TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL, "
        "volume REAL, updated_at REAL NOT NULL, "
        "PRIMARY KEY(provider,instrument,contract,timeframe,bar_time))",
        "CREATE INDEX IF NOT EXISTS idx_central_external_bars_lookup "
        "ON central_external_bars(instrument,timeframe,bar_time DESC)",
    )


def postgres_schema_statements() -> tuple[str, ...]:
    return (
        "CREATE TABLE IF NOT EXISTS central_api_query_cache ("
        "cache_key TEXT PRIMARY KEY, api_id TEXT NOT NULL, expires_at DOUBLE PRECISION NOT NULL, "
        "payload_json JSONB NOT NULL, has_next BOOLEAN NOT NULL, next_key TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_central_api_query_expiry "
        "ON central_api_query_cache(expires_at)",
        "CREATE TABLE IF NOT EXISTS central_realtime_latest ("
        "event_type TEXT NOT NULL, item_key TEXT NOT NULL, received_at DOUBLE PRECISION NOT NULL, "
        "event_json JSONB NOT NULL, PRIMARY KEY(event_type,item_key))",
        "CREATE TABLE IF NOT EXISTS central_minute_bars ("
        "trading_date DATE NOT NULL, minute TIME NOT NULL, code TEXT NOT NULL, market TEXT NOT NULL, "
        "open BIGINT NOT NULL, high BIGINT NOT NULL, low BIGINT NOT NULL, close BIGINT NOT NULL, "
        "volume BIGINT NOT NULL, trade_value_million_won BIGINT NOT NULL, updated_at DOUBLE PRECISION NOT NULL, "
        "PRIMARY KEY(trading_date,minute,code,market))",
        "CREATE INDEX IF NOT EXISTS idx_central_minute_bars_lookup "
        "ON central_minute_bars(code,trading_date,minute)",
        "CREATE TABLE IF NOT EXISTS central_daily_bars ("
        "trading_date DATE NOT NULL, code TEXT NOT NULL, market TEXT NOT NULL, "
        "open BIGINT NOT NULL, high BIGINT NOT NULL, low BIGINT NOT NULL, close BIGINT NOT NULL, "
        "volume BIGINT NOT NULL, trade_value_million_won BIGINT, updated_at DOUBLE PRECISION NOT NULL, "
        "PRIMARY KEY(trading_date,code,market))",
        "CREATE TABLE IF NOT EXISTS central_dataset_snapshots ("
        "kind TEXT NOT NULL, subject TEXT NOT NULL, snapshot_key TEXT NOT NULL, "
        "saved_at DOUBLE PRECISION NOT NULL, payload_json JSONB NOT NULL, "
        "PRIMARY KEY(kind,subject,snapshot_key))",
        "CREATE INDEX IF NOT EXISTS idx_central_dataset_lookup "
        "ON central_dataset_snapshots(kind,subject,snapshot_key DESC)",
        "CREATE TABLE IF NOT EXISTS central_documents ("
        "collection TEXT NOT NULL, owner TEXT NOT NULL, document_key TEXT NOT NULL, "
        "updated_at DOUBLE PRECISION NOT NULL, document_json JSONB NOT NULL, "
        "PRIMARY KEY(collection,owner,document_key))",
        "CREATE INDEX IF NOT EXISTS idx_central_documents_lookup "
        "ON central_documents(collection,owner,updated_at DESC)",
        "CREATE TABLE IF NOT EXISTS central_external_bars ("
        "provider TEXT NOT NULL, instrument TEXT NOT NULL, contract TEXT NOT NULL, "
        "timeframe TEXT NOT NULL, bar_time TIMESTAMPTZ NOT NULL, open DOUBLE PRECISION, "
        "high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION, volume DOUBLE PRECISION, "
        "updated_at DOUBLE PRECISION NOT NULL, "
        "PRIMARY KEY(provider,instrument,contract,timeframe,bar_time))",
        "CREATE INDEX IF NOT EXISTS idx_central_external_bars_lookup "
        "ON central_external_bars(instrument,timeframe,bar_time DESC)",
    )


def central_schema_migrations():
    # 순환 import를 피하면서 실행 명세를 이 파일의 단일 원본에서 구성한다.
    from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigration

    return (
        CentralSchemaMigration(
            1,
            CENTRAL_SCHEMA_BASELINE_NAME,
            sqlite_schema_statements(),
            postgres_schema_statements(),
        ),
        CentralSchemaMigration(
            2,
            CENTRAL_MARKET_METADATA_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_market_data_observation_meta ("
                "dataset_kind TEXT NOT NULL, subject TEXT NOT NULL, observation_key TEXT NOT NULL, "
                "effective_at TEXT, available_at TEXT, venue TEXT NOT NULL, unit TEXT NOT NULL, "
                "value_kind TEXT NOT NULL, completeness TEXT NOT NULL, origin TEXT NOT NULL, "
                "source TEXT NOT NULL DEFAULT '', candidate_universe TEXT NOT NULL, "
                "PRIMARY KEY(dataset_kind,subject,observation_key))",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_market_data_observation_meta ("
                "dataset_kind TEXT NOT NULL, subject TEXT NOT NULL, observation_key TEXT NOT NULL, "
                "effective_at TIMESTAMPTZ, available_at TIMESTAMPTZ, venue TEXT NOT NULL, "
                "unit TEXT NOT NULL, value_kind TEXT NOT NULL, completeness TEXT NOT NULL, "
                "origin TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', candidate_universe TEXT NOT NULL, "
                "PRIMARY KEY(dataset_kind,subject,observation_key))",
            ),
        ),
        CentralSchemaMigration(
            3,
            CENTRAL_MARKET_STATE_TIME_REPAIR_MIGRATION_NAME,
            (
                "INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) "
                "SELECT kind,subject,strftime('%Y-%m-%dT%H:%M',saved_at,'unixepoch','+9 hours'),"
                "saved_at,payload_json FROM central_dataset_snapshots "
                "WHERE kind='market_state' AND snapshot_key LIKE '%T88:88' "
                "ON CONFLICT(kind,subject,snapshot_key) DO UPDATE SET "
                "saved_at=excluded.saved_at,payload_json=excluded.payload_json",
                "INSERT INTO central_market_data_observation_meta("
                "dataset_kind,subject,observation_key,effective_at,available_at,venue,unit,"
                "value_kind,completeness,origin,source,candidate_universe) "
                "SELECT m.dataset_kind,m.subject,"
                "strftime('%Y-%m-%dT%H:%M',s.saved_at,'unixepoch','+9 hours'),"
                "strftime('%Y-%m-%dT%H:%M:00+09:00',s.saved_at,'unixepoch','+9 hours'),"
                "m.available_at,m.venue,m.unit,m.value_kind,m.completeness,m.origin,m.source,"
                "m.candidate_universe FROM central_market_data_observation_meta m "
                "JOIN central_dataset_snapshots s ON s.kind=m.dataset_kind AND s.subject=m.subject "
                "AND s.snapshot_key=m.observation_key "
                "WHERE m.dataset_kind='market_state' AND m.observation_key LIKE '%T88:88' "
                "ON CONFLICT(dataset_kind,subject,observation_key) DO UPDATE SET "
                "effective_at=excluded.effective_at,available_at=excluded.available_at,"
                "venue=excluded.venue,unit=excluded.unit,value_kind=excluded.value_kind,"
                "completeness=excluded.completeness,origin=excluded.origin,source=excluded.source,"
                "candidate_universe=excluded.candidate_universe",
                "DELETE FROM central_market_data_observation_meta "
                "WHERE dataset_kind='market_state' AND observation_key LIKE '%T88:88'",
                "DELETE FROM central_dataset_snapshots "
                "WHERE kind='market_state' AND snapshot_key LIKE '%T88:88'",
            ),
            (
                "INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) "
                "SELECT kind,subject,to_char(to_timestamp(saved_at) AT TIME ZONE 'Asia/Seoul',"
                "'YYYY-MM-DD\"T\"HH24:MI'),saved_at,payload_json FROM central_dataset_snapshots "
                "WHERE kind='market_state' AND snapshot_key LIKE '%T88:88' "
                "ON CONFLICT(kind,subject,snapshot_key) DO UPDATE SET "
                "saved_at=EXCLUDED.saved_at,payload_json=EXCLUDED.payload_json",
                "INSERT INTO central_market_data_observation_meta("
                "dataset_kind,subject,observation_key,effective_at,available_at,venue,unit,"
                "value_kind,completeness,origin,source,candidate_universe) "
                "SELECT m.dataset_kind,m.subject,"
                "to_char(to_timestamp(s.saved_at) AT TIME ZONE 'Asia/Seoul','YYYY-MM-DD\"T\"HH24:MI'),"
                "to_timestamp(s.saved_at),to_timestamp(s.saved_at),m.venue,m.unit,m.value_kind,m.completeness,"
                "m.origin,m.source,m.candidate_universe FROM central_market_data_observation_meta m "
                "JOIN central_dataset_snapshots s ON s.kind=m.dataset_kind AND s.subject=m.subject "
                "AND s.snapshot_key=m.observation_key "
                "WHERE m.dataset_kind='market_state' AND m.observation_key LIKE '%T88:88' "
                "ON CONFLICT(dataset_kind,subject,observation_key) DO UPDATE SET "
                "effective_at=EXCLUDED.effective_at,available_at=EXCLUDED.available_at,"
                "venue=EXCLUDED.venue,unit=EXCLUDED.unit,value_kind=EXCLUDED.value_kind,"
                "completeness=EXCLUDED.completeness,origin=EXCLUDED.origin,source=EXCLUDED.source,"
                "candidate_universe=EXCLUDED.candidate_universe",
                "DELETE FROM central_market_data_observation_meta "
                "WHERE dataset_kind='market_state' AND observation_key LIKE '%T88:88'",
                "DELETE FROM central_dataset_snapshots "
                "WHERE kind='market_state' AND snapshot_key LIKE '%T88:88'",
            ),
        ),
        CentralSchemaMigration(
            4,
            CENTRAL_SECOND_TRADE_BARS_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_second_trade_bars ("
                "trading_date TEXT NOT NULL, trade_second TEXT NOT NULL, code TEXT NOT NULL, "
                "market TEXT NOT NULL, open INTEGER NOT NULL, high INTEGER NOT NULL, "
                "low INTEGER NOT NULL, close INTEGER NOT NULL, volume INTEGER NOT NULL, "
                "trade_value_won INTEGER NOT NULL, trade_count INTEGER NOT NULL, "
                "available_at REAL NOT NULL, "
                "PRIMARY KEY(trading_date,trade_second,code,market))",
                "CREATE INDEX IF NOT EXISTS idx_central_second_trade_bars_lookup "
                "ON central_second_trade_bars(code,trading_date,trade_second)",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_second_trade_bars ("
                "trading_date DATE NOT NULL, trade_second TIME NOT NULL, code TEXT NOT NULL, "
                "market TEXT NOT NULL, open BIGINT NOT NULL, high BIGINT NOT NULL, "
                "low BIGINT NOT NULL, close BIGINT NOT NULL, volume BIGINT NOT NULL, "
                "trade_value_won BIGINT NOT NULL, trade_count BIGINT NOT NULL, "
                "available_at DOUBLE PRECISION NOT NULL, "
                "PRIMARY KEY(trading_date,trade_second,code,market))",
                "CREATE INDEX IF NOT EXISTS idx_central_second_trade_bars_lookup "
                "ON central_second_trade_bars(code,trading_date,trade_second)",
            ),
        ),
        CentralSchemaMigration(
            5,
            CENTRAL_THEME_HISTORY_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_theme_snapshots ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT NOT NULL UNIQUE, "
                "profile_id TEXT NOT NULL, content_hash TEXT NOT NULL, effective_at TEXT, "
                "received_at REAL NOT NULL, available_at REAL NOT NULL, origin_device TEXT NOT NULL, "
                "revision_of TEXT, document_json TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_theme_snapshot_available "
                "ON central_theme_snapshots(available_at DESC,accepted_sequence DESC)",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_theme_snapshots ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, snapshot_id TEXT NOT NULL UNIQUE, "
                "profile_id TEXT NOT NULL, content_hash TEXT NOT NULL, effective_at TEXT, "
                "received_at DOUBLE PRECISION NOT NULL, available_at DOUBLE PRECISION NOT NULL, "
                "origin_device TEXT NOT NULL, revision_of TEXT, document_json JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_theme_snapshot_available "
                "ON central_theme_snapshots(available_at DESC,accepted_sequence DESC)",
            ),
        ),
        CentralSchemaMigration(
            6,
            CENTRAL_NEWS_HISTORY_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_news_article_revisions ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, article_revision_id TEXT NOT NULL UNIQUE, "
                "stock_code TEXT NOT NULL, identity TEXT NOT NULL, content_hash TEXT NOT NULL, "
                "collector_id TEXT NOT NULL, published_at TEXT, received_at REAL NOT NULL, "
                "available_at REAL NOT NULL, collection_scope TEXT NOT NULL, revision_of TEXT, "
                "document_json TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_news_article_lookup ON "
                "central_news_article_revisions(stock_code,identity,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_body_revisions ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, body_revision_id TEXT NOT NULL UNIQUE, "
                "article_revision_id TEXT NOT NULL, content_hash TEXT NOT NULL, extractor_version TEXT NOT NULL, "
                "fetched_at REAL NOT NULL, available_at REAL NOT NULL, status TEXT NOT NULL, "
                "body_text TEXT NOT NULL, error TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_news_body_lookup ON "
                "central_news_body_revisions(article_revision_id,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_ai_revisions ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, analysis_revision_id TEXT NOT NULL UNIQUE, "
                "target_id TEXT NOT NULL, article_revision_id TEXT NOT NULL, body_revision_id TEXT, "
                "provider TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL, "
                "schema_version TEXT NOT NULL, input_hash TEXT NOT NULL, computed_at REAL NOT NULL, "
                "available_at REAL NOT NULL, output_json TEXT NOT NULL, usage_json TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_news_ai_lookup ON "
                "central_news_ai_revisions(target_id,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_jobs ("
                "job_key TEXT PRIMARY KEY, article_revision_id TEXT NOT NULL, stock_code TEXT NOT NULL, "
                "target_id TEXT NOT NULL, stage TEXT NOT NULL, input_hash TEXT NOT NULL, "
                "processing_version TEXT NOT NULL, attempts INTEGER NOT NULL, next_retry_at REAL NOT NULL, "
                "state TEXT NOT NULL, output_ref TEXT NOT NULL, error TEXT NOT NULL, "
                "payload_json TEXT NOT NULL, updated_at REAL NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_news_jobs_ready ON "
                "central_news_jobs(state,next_retry_at,updated_at)",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_news_article_revisions ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, article_revision_id TEXT NOT NULL UNIQUE, "
                "stock_code TEXT NOT NULL, identity TEXT NOT NULL, content_hash TEXT NOT NULL, "
                "collector_id TEXT NOT NULL, published_at TEXT, received_at DOUBLE PRECISION NOT NULL, "
                "available_at DOUBLE PRECISION NOT NULL, collection_scope TEXT NOT NULL, revision_of TEXT, "
                "document_json JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_news_article_lookup ON "
                "central_news_article_revisions(stock_code,identity,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_body_revisions ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, body_revision_id TEXT NOT NULL UNIQUE, "
                "article_revision_id TEXT NOT NULL, content_hash TEXT NOT NULL, extractor_version TEXT NOT NULL, "
                "fetched_at DOUBLE PRECISION NOT NULL, available_at DOUBLE PRECISION NOT NULL, status TEXT NOT NULL, "
                "body_text TEXT NOT NULL, error TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_news_body_lookup ON "
                "central_news_body_revisions(article_revision_id,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_ai_revisions ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, analysis_revision_id TEXT NOT NULL UNIQUE, "
                "target_id TEXT NOT NULL, article_revision_id TEXT NOT NULL, body_revision_id TEXT, "
                "provider TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL, "
                "schema_version TEXT NOT NULL, input_hash TEXT NOT NULL, computed_at DOUBLE PRECISION NOT NULL, "
                "available_at DOUBLE PRECISION NOT NULL, output_json JSONB NOT NULL, usage_json JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_news_ai_lookup ON "
                "central_news_ai_revisions(target_id,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_jobs ("
                "job_key TEXT PRIMARY KEY, article_revision_id TEXT NOT NULL, stock_code TEXT NOT NULL, "
                "target_id TEXT NOT NULL, stage TEXT NOT NULL, input_hash TEXT NOT NULL, "
                "processing_version TEXT NOT NULL, attempts INTEGER NOT NULL, next_retry_at DOUBLE PRECISION NOT NULL, "
                "state TEXT NOT NULL, output_ref TEXT NOT NULL, error TEXT NOT NULL, "
                "payload_json JSONB NOT NULL, updated_at DOUBLE PRECISION NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_news_jobs_ready ON "
                "central_news_jobs(state,next_retry_at,updated_at)",
            ),
        ),
        CentralSchemaMigration(
            7,
            CENTRAL_NEWS_EVENT_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_news_event_revisions ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_revision_id TEXT NOT NULL UNIQUE, "
                "event_id TEXT NOT NULL, event_key TEXT, stock_code TEXT NOT NULL, event_type TEXT NOT NULL, "
                "article_revision_id TEXT NOT NULL, body_revision_id TEXT NOT NULL, rule_version TEXT NOT NULL, "
                "input_hash TEXT NOT NULL, role TEXT NOT NULL, scope TEXT NOT NULL, certainty TEXT NOT NULL, "
                "novelty TEXT NOT NULL, amount_won INTEGER, counterparty TEXT, importance_score INTEGER NOT NULL, "
                "confidence_score INTEGER NOT NULL, novelty_score INTEGER NOT NULL, ai_required INTEGER NOT NULL, "
                "available_at REAL NOT NULL, revision_of TEXT, result_json TEXT NOT NULL, "
                "UNIQUE(article_revision_id,body_revision_id,rule_version,input_hash))",
                "CREATE INDEX IF NOT EXISTS idx_central_news_event_lookup ON "
                "central_news_event_revisions(event_id,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_event_membership_revisions ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, membership_revision_id TEXT NOT NULL UNIQUE, "
                "event_id TEXT NOT NULL, event_revision_id TEXT NOT NULL, article_revision_id TEXT NOT NULL, "
                "body_revision_id TEXT NOT NULL, relation TEXT NOT NULL, available_at REAL NOT NULL, "
                "revision_of TEXT, document_json TEXT NOT NULL, "
                "UNIQUE(event_revision_id,article_revision_id,body_revision_id))",
                "CREATE INDEX IF NOT EXISTS idx_central_news_membership_lookup ON "
                "central_news_event_membership_revisions(event_id,available_at DESC,accepted_sequence DESC)",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_news_event_revisions ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, event_revision_id TEXT NOT NULL UNIQUE, "
                "event_id TEXT NOT NULL, event_key TEXT, stock_code TEXT NOT NULL, event_type TEXT NOT NULL, "
                "article_revision_id TEXT NOT NULL, body_revision_id TEXT NOT NULL, rule_version TEXT NOT NULL, "
                "input_hash TEXT NOT NULL, role TEXT NOT NULL, scope TEXT NOT NULL, certainty TEXT NOT NULL, "
                "novelty TEXT NOT NULL, amount_won BIGINT, counterparty TEXT, importance_score INTEGER NOT NULL, "
                "confidence_score INTEGER NOT NULL, novelty_score INTEGER NOT NULL, ai_required BOOLEAN NOT NULL, "
                "available_at DOUBLE PRECISION NOT NULL, revision_of TEXT, result_json JSONB NOT NULL, "
                "UNIQUE(article_revision_id,body_revision_id,rule_version,input_hash))",
                "CREATE INDEX IF NOT EXISTS idx_central_news_event_lookup ON "
                "central_news_event_revisions(event_id,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_event_membership_revisions ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, membership_revision_id TEXT NOT NULL UNIQUE, "
                "event_id TEXT NOT NULL, event_revision_id TEXT NOT NULL, article_revision_id TEXT NOT NULL, "
                "body_revision_id TEXT NOT NULL, relation TEXT NOT NULL, available_at DOUBLE PRECISION NOT NULL, "
                "revision_of TEXT, document_json JSONB NOT NULL, "
                "UNIQUE(event_revision_id,article_revision_id,body_revision_id))",
                "CREATE INDEX IF NOT EXISTS idx_central_news_membership_lookup ON "
                "central_news_event_membership_revisions(event_id,available_at DESC,accepted_sequence DESC)",
            ),
        ),
        CentralSchemaMigration(
            8,
            CENTRAL_NEWS_SOURCE_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_news_source_cursors ("
                "source_id TEXT PRIMARY KEY, scope TEXT NOT NULL, query_text TEXT NOT NULL, "
                "cursor_published_at TEXT, cursor_identity TEXT NOT NULL, pending_published_at TEXT, "
                "pending_identity TEXT NOT NULL, next_start INTEGER NOT NULL, next_schedule_at REAL NOT NULL, "
                "checked_at REAL, last_success REAL, coverage TEXT NOT NULL, truncated INTEGER NOT NULL, "
                "error TEXT NOT NULL, updated_at REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS central_news_source_runs ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_revision_id TEXT NOT NULL UNIQUE, "
                "run_id TEXT NOT NULL, source_id TEXT NOT NULL, scope TEXT NOT NULL, page_start INTEGER NOT NULL, "
                "checked_at REAL NOT NULL, completed_at REAL NOT NULL, raw_count INTEGER NOT NULL, "
                "unique_count INTEGER NOT NULL, duplicate_count INTEGER NOT NULL, request_count INTEGER NOT NULL, "
                "budget_remaining INTEGER NOT NULL, truncated INTEGER NOT NULL, coverage TEXT NOT NULL, "
                "error TEXT NOT NULL, document_json TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_news_source_runs_lookup ON "
                "central_news_source_runs(source_id,checked_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_source_observations ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, observation_id TEXT NOT NULL UNIQUE, "
                "run_id TEXT NOT NULL, source_id TEXT NOT NULL, query_text TEXT NOT NULL, page_start INTEGER NOT NULL, "
                "article_revision_id TEXT NOT NULL, identity TEXT NOT NULL, published_at TEXT, received_at REAL NOT NULL, "
                "available_at REAL NOT NULL, content_hash TEXT NOT NULL, duplicate INTEGER NOT NULL, document_json TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_news_source_observations_lookup ON "
                "central_news_source_observations(source_id,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_article_target_revisions ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, target_revision_id TEXT NOT NULL UNIQUE, "
                "article_revision_id TEXT NOT NULL, identity TEXT NOT NULL, stock_code TEXT, stock_name TEXT, "
                "relation_status TEXT NOT NULL, evidence_text TEXT NOT NULL, rule_version TEXT NOT NULL, "
                "available_at REAL NOT NULL, revision_of TEXT, document_json TEXT NOT NULL, "
                "UNIQUE(article_revision_id,stock_code,stock_name,relation_status,rule_version))",
                "CREATE INDEX IF NOT EXISTS idx_central_news_article_targets_lookup ON "
                "central_news_article_target_revisions(article_revision_id,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_request_budget ("
                "budget_date TEXT NOT NULL, scope TEXT NOT NULL, request_count INTEGER NOT NULL, updated_at REAL NOT NULL, "
                "PRIMARY KEY(budget_date,scope))",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_news_source_cursors ("
                "source_id TEXT PRIMARY KEY, scope TEXT NOT NULL, query_text TEXT NOT NULL, "
                "cursor_published_at TEXT, cursor_identity TEXT NOT NULL, pending_published_at TEXT, "
                "pending_identity TEXT NOT NULL, next_start INTEGER NOT NULL, next_schedule_at DOUBLE PRECISION NOT NULL, "
                "checked_at DOUBLE PRECISION, last_success DOUBLE PRECISION, coverage TEXT NOT NULL, "
                "truncated BOOLEAN NOT NULL, error TEXT NOT NULL, updated_at DOUBLE PRECISION NOT NULL)",
                "CREATE TABLE IF NOT EXISTS central_news_source_runs ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, run_revision_id TEXT NOT NULL UNIQUE, run_id TEXT NOT NULL, "
                "source_id TEXT NOT NULL, scope TEXT NOT NULL, page_start INTEGER NOT NULL, checked_at DOUBLE PRECISION NOT NULL, "
                "completed_at DOUBLE PRECISION NOT NULL, raw_count INTEGER NOT NULL, unique_count INTEGER NOT NULL, "
                "duplicate_count INTEGER NOT NULL, request_count INTEGER NOT NULL, budget_remaining INTEGER NOT NULL, "
                "truncated BOOLEAN NOT NULL, coverage TEXT NOT NULL, error TEXT NOT NULL, document_json JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_news_source_runs_lookup ON "
                "central_news_source_runs(source_id,checked_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_source_observations ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, observation_id TEXT NOT NULL UNIQUE, run_id TEXT NOT NULL, "
                "source_id TEXT NOT NULL, query_text TEXT NOT NULL, page_start INTEGER NOT NULL, article_revision_id TEXT NOT NULL, "
                "identity TEXT NOT NULL, published_at TEXT, received_at DOUBLE PRECISION NOT NULL, available_at DOUBLE PRECISION NOT NULL, "
                "content_hash TEXT NOT NULL, duplicate BOOLEAN NOT NULL, document_json JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_news_source_observations_lookup ON "
                "central_news_source_observations(source_id,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_article_target_revisions ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, target_revision_id TEXT NOT NULL UNIQUE, "
                "article_revision_id TEXT NOT NULL, identity TEXT NOT NULL, stock_code TEXT, stock_name TEXT, "
                "relation_status TEXT NOT NULL, evidence_text TEXT NOT NULL, rule_version TEXT NOT NULL, "
                "available_at DOUBLE PRECISION NOT NULL, revision_of TEXT, document_json JSONB NOT NULL, "
                "UNIQUE(article_revision_id,stock_code,stock_name,relation_status,rule_version))",
                "CREATE INDEX IF NOT EXISTS idx_central_news_article_targets_lookup ON "
                "central_news_article_target_revisions(article_revision_id,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_news_request_budget ("
                "budget_date TEXT NOT NULL, scope TEXT NOT NULL, request_count INTEGER NOT NULL, "
                "updated_at DOUBLE PRECISION NOT NULL, PRIMARY KEY(budget_date,scope))",
            ),
        ),
        CentralSchemaMigration(
            9,
            CENTRAL_MARKET_EVENT_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_vi_event_revisions ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, "
                "event_key TEXT NOT NULL UNIQUE, stock_code TEXT NOT NULL, event_kind TEXT NOT NULL, "
                "vi_type TEXT NOT NULL, effective_at TEXT, received_at REAL NOT NULL, available_at REAL NOT NULL, "
                "price INTEGER, direction TEXT NOT NULL, trigger_count INTEGER, exchange TEXT NOT NULL, "
                "source TEXT NOT NULL, document_json TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_vi_event_lookup ON "
                "central_vi_event_revisions(stock_code,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_hot_cohort_current ("
                "stock_code TEXT PRIMARY KEY, stock_name TEXT NOT NULL, condition_name TEXT NOT NULL, "
                "first_seen_at REAL NOT NULL, entry_session TEXT NOT NULL, last_signal TEXT NOT NULL, "
                "last_signal_at REAL NOT NULL, active INTEGER NOT NULL, nxt_eligible INTEGER, "
                "expired_at REAL, document_json TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS central_hot_cohort_revisions ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL UNIQUE, "
                "revision_key TEXT NOT NULL UNIQUE, stock_code TEXT NOT NULL, event_type TEXT NOT NULL, "
                "condition_name TEXT NOT NULL, condition_seq TEXT NOT NULL, session_id TEXT NOT NULL, "
                "effective_at REAL NOT NULL, available_at REAL NOT NULL, document_json TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_hot_cohort_lookup ON "
                "central_hot_cohort_revisions(stock_code,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_upper_limit_fact_revisions ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, fact_id TEXT NOT NULL UNIQUE, "
                "fact_key TEXT NOT NULL UNIQUE, stock_code TEXT NOT NULL, session_id TEXT NOT NULL, "
                "status TEXT NOT NULL, upper_limit_price INTEGER, current_price INTEGER, high_price INTEGER, "
                "effective_at REAL NOT NULL, available_at REAL NOT NULL, source TEXT NOT NULL, "
                "evidence TEXT NOT NULL, document_json TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_upper_limit_lookup ON "
                "central_upper_limit_fact_revisions(stock_code,available_at DESC,accepted_sequence DESC)",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_vi_event_revisions ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, event_key TEXT NOT NULL UNIQUE, "
                "stock_code TEXT NOT NULL, event_kind TEXT NOT NULL, vi_type TEXT NOT NULL, effective_at TEXT, "
                "received_at DOUBLE PRECISION NOT NULL, available_at DOUBLE PRECISION NOT NULL, price BIGINT, "
                "direction TEXT NOT NULL, trigger_count INTEGER, exchange TEXT NOT NULL, source TEXT NOT NULL, "
                "document_json JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_vi_event_lookup ON "
                "central_vi_event_revisions(stock_code,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_hot_cohort_current ("
                "stock_code TEXT PRIMARY KEY, stock_name TEXT NOT NULL, condition_name TEXT NOT NULL, "
                "first_seen_at DOUBLE PRECISION NOT NULL, entry_session TEXT NOT NULL, last_signal TEXT NOT NULL, "
                "last_signal_at DOUBLE PRECISION NOT NULL, active BOOLEAN NOT NULL, nxt_eligible BOOLEAN, "
                "expired_at DOUBLE PRECISION, document_json JSONB NOT NULL)",
                "CREATE TABLE IF NOT EXISTS central_hot_cohort_revisions ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, revision_id TEXT NOT NULL UNIQUE, revision_key TEXT NOT NULL UNIQUE, "
                "stock_code TEXT NOT NULL, event_type TEXT NOT NULL, condition_name TEXT NOT NULL, "
                "condition_seq TEXT NOT NULL, session_id TEXT NOT NULL, effective_at DOUBLE PRECISION NOT NULL, "
                "available_at DOUBLE PRECISION NOT NULL, document_json JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_hot_cohort_lookup ON "
                "central_hot_cohort_revisions(stock_code,available_at DESC,accepted_sequence DESC)",
                "CREATE TABLE IF NOT EXISTS central_upper_limit_fact_revisions ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, fact_id TEXT NOT NULL UNIQUE, fact_key TEXT NOT NULL UNIQUE, "
                "stock_code TEXT NOT NULL, session_id TEXT NOT NULL, status TEXT NOT NULL, upper_limit_price BIGINT, "
                "current_price BIGINT, high_price BIGINT, effective_at DOUBLE PRECISION NOT NULL, "
                "available_at DOUBLE PRECISION NOT NULL, source TEXT NOT NULL, evidence TEXT NOT NULL, "
                "document_json JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_upper_limit_lookup ON "
                "central_upper_limit_fact_revisions(stock_code,available_at DESC,accepted_sequence DESC)",
            ),
        ),
        CentralSchemaMigration(
            10,
            CENTRAL_NEWS_REUSE_MIGRATION_NAME,
            (
                "CREATE INDEX IF NOT EXISTS idx_central_news_source_identity_lookup ON "
                "central_news_source_observations(source_id,identity,accepted_sequence DESC)",
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_central_news_source_identity_lookup ON "
                "central_news_source_observations(source_id,identity,accepted_sequence DESC)",
            ),
        ),
        CentralSchemaMigration(
            11,
            CENTRAL_N3_STOCK_NEWS_MIGRATION_NAME,
            (
                "CREATE INDEX IF NOT EXISTS idx_central_news_targets_stock_lookup ON "
                "central_news_article_target_revisions(stock_code,relation_status,article_revision_id,accepted_sequence DESC)",
            ),
            (
                "CREATE INDEX IF NOT EXISTS idx_central_news_targets_stock_lookup ON "
                "central_news_article_target_revisions(stock_code,relation_status,article_revision_id,accepted_sequence DESC)",
            ),
        ),
        CentralSchemaMigration(
            12,
            CENTRAL_OBSERVATION_HISTORY_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_observation_revisions ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL UNIQUE, "
                "observation_key TEXT NOT NULL, schema_version INTEGER NOT NULL, source_id TEXT NOT NULL, "
                "source_session_id TEXT NOT NULL, source_sequence TEXT NOT NULL, kind TEXT NOT NULL, "
                "subject TEXT NOT NULL, venue TEXT NOT NULL, effective_at TEXT, received_at TEXT NOT NULL, "
                "available_at TEXT, revision_of TEXT, payload_hash TEXT NOT NULL, unit TEXT NOT NULL, "
                "value_kind TEXT NOT NULL, completeness TEXT NOT NULL, origin TEXT NOT NULL, "
                "candidate_universe TEXT NOT NULL, quality_flags_json TEXT NOT NULL, "
                "clock_quality TEXT NOT NULL, source_ref_json TEXT NOT NULL, payload_json TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_observation_lookup ON "
                "central_observation_revisions(kind,subject,observation_key,source_id,accepted_sequence DESC)",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_observation_revisions ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, revision_id TEXT NOT NULL UNIQUE, "
                "observation_key TEXT NOT NULL, schema_version INTEGER NOT NULL, source_id TEXT NOT NULL, "
                "source_session_id TEXT NOT NULL, source_sequence TEXT NOT NULL, kind TEXT NOT NULL, "
                "subject TEXT NOT NULL, venue TEXT NOT NULL, effective_at TIMESTAMPTZ, "
                "received_at TIMESTAMPTZ NOT NULL, available_at TIMESTAMPTZ, revision_of TEXT, "
                "payload_hash TEXT NOT NULL, unit TEXT NOT NULL, value_kind TEXT NOT NULL, "
                "completeness TEXT NOT NULL, origin TEXT NOT NULL, candidate_universe TEXT NOT NULL, "
                "quality_flags_json JSONB NOT NULL, clock_quality TEXT NOT NULL, "
                "source_ref_json JSONB NOT NULL, payload_json JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_observation_lookup ON "
                "central_observation_revisions(kind,subject,observation_key,source_id,accepted_sequence DESC)",
            ),
        ),
        CentralSchemaMigration(
            13,
            CENTRAL_RESEARCH_EXPORT_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_research_exports ("
                "dataset_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, start_at TEXT NOT NULL, "
                "end_at TEXT NOT NULL, kinds_json TEXT NOT NULL, subject TEXT NOT NULL, "
                "revision_count INTEGER NOT NULL, revision_ids_hash TEXT NOT NULL, "
                "manifest_json TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS central_research_export_members ("
                "dataset_id TEXT NOT NULL, ordinal INTEGER NOT NULL, revision_id TEXT NOT NULL, "
                "PRIMARY KEY(dataset_id,ordinal), UNIQUE(dataset_id,revision_id))",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_research_exports ("
                "dataset_id TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL, "
                "start_at TIMESTAMPTZ NOT NULL, end_at TIMESTAMPTZ NOT NULL, "
                "kinds_json JSONB NOT NULL, subject TEXT NOT NULL, revision_count INTEGER NOT NULL, "
                "revision_ids_hash TEXT NOT NULL, manifest_json JSONB NOT NULL)",
                "CREATE TABLE IF NOT EXISTS central_research_export_members ("
                "dataset_id TEXT NOT NULL, ordinal INTEGER NOT NULL, revision_id TEXT NOT NULL, "
                "PRIMARY KEY(dataset_id,ordinal), UNIQUE(dataset_id,revision_id))",
            ),
        ),
        CentralSchemaMigration(
            14,
            CENTRAL_MINUTE_BAR_REVISION_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_minute_bar_operations ("
                "operation_id TEXT PRIMARY KEY, operation_hash TEXT NOT NULL, "
                "processed_at TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_observation_available ON "
                "central_observation_revisions(kind,available_at,accepted_sequence)",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_minute_bar_operations ("
                "operation_id TEXT PRIMARY KEY, operation_hash TEXT NOT NULL, "
                "processed_at TIMESTAMPTZ NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_observation_available ON "
                "central_observation_revisions(kind,available_at,accepted_sequence)",
            ),
        ),
        CentralSchemaMigration(
            15,
            CENTRAL_SHADOW_CANDIDATE_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_shadow_monitor_state ("
                "monitor_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, document_json TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS central_shadow_decisions ("
                "decision_id TEXT PRIMARY KEY, monitor_id TEXT NOT NULL, decided_at TEXT NOT NULL, "
                "document_json TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS central_shadow_candidate_events ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, "
                "monitor_id TEXT NOT NULL, available_at TEXT NOT NULL, expires_at TEXT NOT NULL, "
                "document_json TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_shadow_candidate_available ON "
                "central_shadow_candidate_events(available_at,accepted_sequence)",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_shadow_monitor_state ("
                "monitor_id TEXT PRIMARY KEY, updated_at TIMESTAMPTZ NOT NULL, document_json JSONB NOT NULL)",
                "CREATE TABLE IF NOT EXISTS central_shadow_decisions ("
                "decision_id TEXT PRIMARY KEY, monitor_id TEXT NOT NULL, decided_at TIMESTAMPTZ NOT NULL, "
                "document_json JSONB NOT NULL)",
                "CREATE TABLE IF NOT EXISTS central_shadow_candidate_events ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, "
                "monitor_id TEXT NOT NULL, available_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL, "
                "document_json JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_shadow_candidate_available ON "
                "central_shadow_candidate_events(available_at,accepted_sequence)",
            ),
        ),
        CentralSchemaMigration(
            16,
            CENTRAL_MOCK_EXECUTION_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_execution_intents ("
                "intent_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, environment TEXT NOT NULL, "
                "account_ref TEXT NOT NULL, state TEXT NOT NULL, broker_order_id TEXT NOT NULL, "
                "last_broker_as_of TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
                "document_json TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_execution_intents_scope ON "
                "central_execution_intents(environment,account_ref,run_id,state)",
                "CREATE TABLE IF NOT EXISTS central_execution_events ("
                "accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, "
                "intent_id TEXT NOT NULL, state TEXT NOT NULL, occurred_at TEXT NOT NULL, "
                "received_at TEXT NOT NULL, broker_execution_id TEXT NOT NULL, document_json TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_execution_events_intent ON "
                "central_execution_events(intent_id,accepted_sequence)",
                "CREATE TABLE IF NOT EXISTS central_execution_account_snapshots ("
                "snapshot_id TEXT PRIMARY KEY, environment TEXT NOT NULL, account_ref TEXT NOT NULL, "
                "as_of TEXT NOT NULL, received_at TEXT NOT NULL, document_json TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_execution_account_snapshots ON "
                "central_execution_account_snapshots(environment,account_ref,as_of DESC)",
                "CREATE TABLE IF NOT EXISTS central_execution_runtime_leases ("
                "owner_key TEXT PRIMARY KEY, owner_token TEXT NOT NULL, lease_expires_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_execution_intents ("
                "intent_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, environment TEXT NOT NULL, "
                "account_ref TEXT NOT NULL, state TEXT NOT NULL, broker_order_id TEXT NOT NULL, "
                "last_broker_as_of TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL, "
                "document_json JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_execution_intents_scope ON "
                "central_execution_intents(environment,account_ref,run_id,state)",
                "CREATE TABLE IF NOT EXISTS central_execution_events ("
                "accepted_sequence BIGSERIAL PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, "
                "intent_id TEXT NOT NULL, state TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL, "
                "received_at TIMESTAMPTZ NOT NULL, broker_execution_id TEXT NOT NULL, document_json JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_execution_events_intent ON "
                "central_execution_events(intent_id,accepted_sequence)",
                "CREATE TABLE IF NOT EXISTS central_execution_account_snapshots ("
                "snapshot_id TEXT PRIMARY KEY, environment TEXT NOT NULL, account_ref TEXT NOT NULL, "
                "as_of TIMESTAMPTZ NOT NULL, received_at TIMESTAMPTZ NOT NULL, document_json JSONB NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_execution_account_snapshots ON "
                "central_execution_account_snapshots(environment,account_ref,as_of DESC)",
                "CREATE TABLE IF NOT EXISTS central_execution_runtime_leases ("
                "owner_key TEXT PRIMARY KEY, owner_token TEXT NOT NULL, lease_expires_at TIMESTAMPTZ NOT NULL, "
                "updated_at TIMESTAMPTZ NOT NULL)",
            ),
        ),
        CentralSchemaMigration(
            17,
            CENTRAL_ACCOUNT_IDENTITY_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_account_registry ("
                "account_ref TEXT PRIMARY KEY, broker TEXT NOT NULL, environment TEXT NOT NULL, "
                "identity_fingerprint TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL, "
                "UNIQUE(broker,environment,identity_fingerprint))",
                "CREATE TABLE IF NOT EXISTS central_account_binding_revisions ("
                "binding_id TEXT PRIMARY KEY, credential_profile_id TEXT NOT NULL, broker TEXT NOT NULL, "
                "environment TEXT NOT NULL, account_ref TEXT NOT NULL, binding_revision INTEGER NOT NULL, "
                "verified_at TEXT NOT NULL, verification_method TEXT NOT NULL, "
                "UNIQUE(credential_profile_id,broker,environment,binding_revision))",
                "CREATE INDEX IF NOT EXISTS idx_central_account_binding_latest ON "
                "central_account_binding_revisions(credential_profile_id,broker,environment,binding_revision DESC)",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_account_registry ("
                "account_ref TEXT PRIMARY KEY, broker TEXT NOT NULL, environment TEXT NOT NULL, "
                "identity_fingerprint TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL, "
                "UNIQUE(broker,environment,identity_fingerprint))",
                "CREATE TABLE IF NOT EXISTS central_account_binding_revisions ("
                "binding_id TEXT PRIMARY KEY, credential_profile_id TEXT NOT NULL, broker TEXT NOT NULL, "
                "environment TEXT NOT NULL, account_ref TEXT NOT NULL, binding_revision BIGINT NOT NULL, "
                "verified_at TIMESTAMPTZ NOT NULL, verification_method TEXT NOT NULL, "
                "UNIQUE(credential_profile_id,broker,environment,binding_revision))",
                "CREATE INDEX IF NOT EXISTS idx_central_account_binding_latest ON "
                "central_account_binding_revisions(credential_profile_id,broker,environment,binding_revision DESC)",
            ),
        ),
        CentralSchemaMigration(
            18,
            CENTRAL_ACCOUNT_SCOPE_ALIAS_MIGRATION_NAME,
            (
                "CREATE TABLE IF NOT EXISTS central_account_scope_aliases ("
                "origin_account_ref TEXT PRIMARY KEY, canonical_account_ref TEXT NOT NULL, "
                "broker TEXT NOT NULL, environment TEXT NOT NULL, credential_profile_id TEXT NOT NULL, "
                "binding_revision INTEGER NOT NULL, verified_at TEXT NOT NULL, verification_method TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_account_scope_alias_target ON "
                "central_account_scope_aliases(canonical_account_ref,broker,environment)",
            ),
            (
                "CREATE TABLE IF NOT EXISTS central_account_scope_aliases ("
                "origin_account_ref TEXT PRIMARY KEY, canonical_account_ref TEXT NOT NULL, "
                "broker TEXT NOT NULL, environment TEXT NOT NULL, credential_profile_id TEXT NOT NULL, "
                "binding_revision BIGINT NOT NULL, verified_at TIMESTAMPTZ NOT NULL, verification_method TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS idx_central_account_scope_alias_target ON "
                "central_account_scope_aliases(canonical_account_ref,broker,environment)",
            ),
        ),
        CentralSchemaMigration(
            19,
            CENTRAL_CREDENTIAL_ACTIVATION_MIGRATION_NAME,
            _credential_schema_statements("sqlite"),
            _credential_schema_statements("postgres"),
        ),
    )


def _credential_schema_statements(dialect: str) -> tuple[str, ...]:
    timestamp = "TEXT" if dialect == "sqlite" else "TIMESTAMPTZ"
    integer = "INTEGER" if dialect == "sqlite" else "BIGINT"
    return (
        "CREATE TABLE IF NOT EXISTS central_credential_profiles ("
        "profile_id TEXT PRIMARY KEY, provider TEXT NOT NULL, environment TEXT, label TEXT NOT NULL, "
        f"lifecycle_state TEXT NOT NULL, created_at {timestamp} NOT NULL, archived_at {timestamp})",
        "CREATE TABLE IF NOT EXISTS central_credential_activations ("
        f"operation_id TEXT PRIMARY KEY, provider TEXT NOT NULL, credential_revision {integer} NOT NULL, "
        "request_id TEXT NOT NULL, request_digest TEXT NOT NULL, profile_id TEXT NOT NULL, "
        f"account_ref TEXT, run_id TEXT, binding_revision {integer}, committed_at {timestamp} NOT NULL, "
        "UNIQUE(provider,profile_id,credential_revision), UNIQUE(provider,profile_id,request_id))",
        "INSERT INTO central_credential_profiles(profile_id,provider,environment,label,lifecycle_state,created_at) "
        "SELECT credential_profile_id,'kiwoom_' || MIN(environment),MIN(environment),'','active',MIN(verified_at) "
        "FROM central_account_binding_revisions GROUP BY credential_profile_id "
        "HAVING COUNT(DISTINCT environment)=1 ON CONFLICT(profile_id) DO NOTHING",
    )
