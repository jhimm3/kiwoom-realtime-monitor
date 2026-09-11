from __future__ import annotations


CENTRAL_TABLES = (
    "central_api_query_cache",
    "central_realtime_latest",
    "central_minute_bars",
    "central_daily_bars",
    "central_dataset_snapshots",
    "central_market_data_observation_meta",
    "central_documents",
    "central_external_bars",
)
CENTRAL_INDEXES = (
    "idx_central_api_query_expiry",
    "idx_central_minute_bars_lookup",
    "idx_central_dataset_lookup",
    "idx_central_documents_lookup",
    "idx_central_external_bars_lookup",
)

CENTRAL_SCHEMA_VERSION = 3
CENTRAL_SCHEMA_BASELINE_NAME = "current_central_storage_baseline"
CENTRAL_MARKET_METADATA_MIGRATION_NAME = "market_data_observation_metadata"
CENTRAL_MARKET_STATE_TIME_REPAIR_MIGRATION_NAME = "repair_market_state_special_trade_time"


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
    )
