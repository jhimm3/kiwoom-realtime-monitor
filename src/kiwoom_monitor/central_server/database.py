from __future__ import annotations

import json
import hashlib
import logging
import sqlite3
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import Enum
from functools import partial
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import BoundedSemaphore, Event, RLock, Thread
from time import monotonic, time
from typing import Any, Callable, Protocol
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from kiwoom_monitor.domain.market_data_contract import (
    CoverageObservation,
    DataCompleteness,
    DataValueKind,
    MarketDataMetadata,
    MarketDataObservation,
    MarketDatasetKind,
    ObservationOrigin,
)
from kiwoom_monitor.domain.research_contract import (
    OBSERVATION_REVISION_SCHEMA_VERSION,
    RESEARCH_OBSERVATION_KINDS,
    ObservationRevisionSource,
    ThemeSnapshotSource,
)
from kiwoom_monitor.domain.news_observation import (
    ARTICLE_BODY_EXTRACTOR_VERSION,
    NEWS_ANALYSIS_SCHEMA_VERSION,
    SUPPLY_CONTRACT_RULE_VERSION,
    news_job_key,
    stable_document_hash,
)
from kiwoom_monitor.application.news_rules import grouped_candidate_identities
from kiwoom_monitor.infrastructure.market_data_metadata_codec import (
    market_metadata_from_storage_row,
    market_metadata_storage_values,
)

from kiwoom_monitor.central_server.database_codec import (
    BAR_KEY_COLUMNS,
    _event_document,
    _json_document,
    bar_columns,
    bar_result_rows,
    bar_value_rows,
    bounded_limit,
    dataset_snapshot_result_rows,
    document_result_rows,
    document_select_query,
    document_value_rows,
    FIVE_MINUTE_BAR_COLUMNS,
    five_minute_bar_result_rows,
    five_minute_bar_value_rows,
    json_mapping,
    observation_revision_result_rows,
    _observation_revision_columns,
    second_trade_bar_value_rows,
)
from kiwoom_monitor.central_server.central_schema import (
    central_schema_migrations,
)
from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigrationRunner
from kiwoom_monitor.central_server.database_datasets import (
    DatasetSnapshotWrite, ASYNC_COMMIT_DATASET_KINDS, COMMON_OBSERVED_DATASET_KINDS,
    _uses_async_dataset_commit, SQLiteDatasetStoreMixin, PostgresDatasetStoreMixin,
)
from kiwoom_monitor.central_server.database_observation_writes import (
    _market_metadata_upsert_sql, _market_metadata_upsert_suffix,
    _append_sqlite_observation_revision, _append_postgres_observation_revision,
    _insert_postgres_observation_revision, _observation_revision_values,
)
from kiwoom_monitor.central_server.postgres_access import (
    _sample_postgres_backend_waits, _postgres_wait_summary,
)
from kiwoom_monitor.central_server.database_market_bars import (
    SQLiteMarketBarStoreMixin, PostgresMarketBarStoreMixin,
    SQLITE_MULTIROW_UPSERT_ROWS, POSTGRES_MULTIROW_UPSERT_ROWS,
    SQLITE_REVISION_BATCH_ROWS, SQLITE_REVISION_LOOKUP_ROWS,
    _minute_key, _minute_operation, _load_sqlite_minute_bar, _load_postgres_minute_bar,
    _final_minute_observation, _save_final_minute_revision_sqlite,
    _save_final_minute_revision_postgres, _load_sqlite_latest_revisions,
    _insert_sqlite_observation_revisions_batch, _minute_query_authority,
    _load_postgres_minute_operation_hashes, _load_postgres_minute_query_authorities,
    _lock_postgres_minute_day_scopes, _load_postgres_latest_revisions,
    _insert_postgres_observation_revisions_batch, _partition_rows_by_last_key,
    _last_rows_by_key, _execute_multirow_upsert, _bar_metadata_key,
    _save_sqlite_metadata, _save_postgres_metadata,
)
from kiwoom_monitor.central_server.postgres_access import (
    _NEWS_CLAIM_WAIT_PROBE_SLOTS, _PostgresObservedCursor,
    _execute_with_postgres_wait_probe, _sample_postgres_commit_waits,
)
from kiwoom_monitor.central_server.database_external_market import (
    PostgresExternalMarketStoreMixin,
    SQLiteExternalMarketStoreMixin,
)
from kiwoom_monitor.central_server.database_query_cache import (
    QueryCacheStore,
    PostgresQueryCacheStoreMixin,
    SQLiteQueryCacheStoreMixin,
    StoredQuery,
)
from kiwoom_monitor.central_server.database_market_events import (
    PostgresMarketEventStoreMixin,
    SQLiteMarketEventStoreMixin,
)
from kiwoom_monitor.central_server.database_research_export import (
    PostgresResearchExportStoreMixin,
    SQLiteResearchExportStoreMixin,
)
from kiwoom_monitor.central_server.database_historical_news import (
    SQLiteHistoricalNewsStoreMixin, PostgresHistoricalNewsStoreMixin,
    _historical_market_source_page,
    _complete_external_news_job as _complete_external_news_job_with_policy,
)
from kiwoom_monitor.central_server.database_news_sources import (
    SQLiteNewsSourceStoreMixin, PostgresNewsSourceStoreMixin,
    _NEWS_SOURCE_CURSOR_KEYS,
    _news_source_cursor,
    _source_item,
    _news_article_content_hash,
    _save_sqlite_news_source_page,
    _insert_sqlite_article_target,
    _enqueue_sqlite_existing_body_rule,
    _upsert_sqlite_source_cursor,
    _save_postgres_news_source_page,
    _insert_postgres_article_target,
    _enqueue_postgres_existing_body_rule,
    _upsert_postgres_source_cursor,
    _source_diagnostics,
    _load_sqlite_news_source_diagnostics,
    _load_postgres_news_source_diagnostics,
    _market_news_source_prefix,
    _market_news_feed_sql,
    _decode_market_news_feed,
)
from kiwoom_monitor.central_server.database_news_revisions import (
    SQLiteNewsRevisionStoreMixin, PostgresNewsRevisionStoreMixin,
    _save_sqlite_news_body,
    _save_postgres_news_body,
    _save_sqlite_news_ai,
    _save_postgres_news_ai,
    _save_sqlite_news_event,
    _save_postgres_news_event,
    _sqlite_possible_related,
    _postgres_possible_related,
    _news_history_query,
    _decode_news_history,
    _load_sqlite_news_history,
    _load_postgres_news_history,
    _confirmed_news_articles_query,
    _stock_news_articles_query,
    _decode_confirmed_news_articles,
    _load_sqlite_stock_news_articles,
    _load_postgres_stock_news_articles,
    _load_sqlite_confirmed_news_articles,
    _load_postgres_confirmed_news_articles,
)
from kiwoom_monitor.central_server.database_news_jobs import (
    SQLiteNewsJobStoreMixin, PostgresNewsJobStoreMixin,
    _NEWS_JOB_CLAIM_READY_SQL,
    _NEWS_JOB_CLAIM_HISTORICAL_SCOPES_SQL,
    _NEWS_JOB_CLAIM_ORDER_AND_LOCK_SQL,
    _NEWS_JOB_CLAIM_SELECT_SQL,
    _NEWS_JOB_CLAIM_DIAGNOSTIC_CANDIDATE_SQL,
    _NEWS_JOB_CLAIM_READ_ONLY_SQL,
    _enqueue_sqlite_news_ai_jobs,
    _enqueue_postgres_news_ai_jobs,
    _news_job_rows,
)
from kiwoom_monitor.central_server.database_news_job_writes import (
    _insert_sqlite_news_job, _insert_postgres_news_job, _notify_news_job_wakeup,
)
from kiwoom_monitor.central_server.database_documents import (
    PostgresDocumentStoreMixin,
    SQLiteDocumentStoreMixin,
)
from kiwoom_monitor.central_server.database_realtime_snapshot import (
    PostgresRealtimeSnapshotStoreMixin,
    SQLiteRealtimeSnapshotStoreMixin,
)
from kiwoom_monitor.central_server.database_market_metadata import (
    PostgresMarketMetadataStoreMixin, SQLiteMarketMetadataStoreMixin,
    _metadata_from_range_row,
)
from kiwoom_monitor.central_server.database_observation_readers import (
    ObservationDeliveryState, ObservationRevisionPage,
    PostgresObservationReaderStoreMixin,
    SQLiteObservationReaderStoreMixin,
)
from kiwoom_monitor.central_server.database_top20_statistics import (
    PostgresTop20StatisticsStoreMixin,
    SQLiteTop20StatisticsStoreMixin,
    _top20_statistics_cache_day,
    _top20_today,
)
from kiwoom_monitor.central_server.database_storage_diagnostics import (
    PostgresStorageDiagnosticsStoreMixin,
    SQLiteStorageDiagnosticsStoreMixin,
)
from kiwoom_monitor.central_server.database_account_identity import (
    PostgresAccountIdentityStoreMixin,
    SQLiteAccountIdentityStoreMixin,
    _account_binding_document,
    _account_binding_values,
    _canonical_account_ref,
    _stable_id,
)
from kiwoom_monitor.central_server.database_account_settings import (
    PostgresAccountSettingsStoreMixin,
    SQLiteAccountSettingsStoreMixin,
    _load_account_settings,
    _load_market_profile_settings,
    _save_account_settings,
    _save_market_profile_settings,
    _save_real_account_event,
    _save_real_account_recovery,
)
from kiwoom_monitor.central_server.database_shadow_state import (
    PostgresShadowStateStoreMixin, SQLiteShadowStateStoreMixin,
    _canonical_document, _save_sqlite_shadow_evaluation,
    _save_postgres_shadow_evaluation, _shadow_candidate_page,
)
from kiwoom_monitor.central_server.database_execution import (
    PostgresExecutionStoreMixin, SQLiteExecutionStoreMixin,
    _require_execution_ownership,
    _execution_intent_values,
    _execution_event_values,
)
from kiwoom_monitor.central_server.database_credentials import (
    PostgresCredentialStoreMixin, SQLiteCredentialStoreMixin,
    _CREDENTIAL_ACTIVATION_COLUMNS,
    _credential_activation_row,
    _register_credential_profile,
    _load_credential_activations,
    _list_credential_profiles,
    _archive_credential_profile,
    _rename_credential_profile,
    _find_credential_activation,
    _create_credential_profile,
    _claim_account_settings,
    _finalize_credential_activation,
)
from kiwoom_monitor.central_server.market_observations import (
    bar_observation_key,
    minute_bar_observation,
    minute_bar_revision_payload,
)





# These live market snapshots are refreshed continuously and can be reconstructed
# from the next poll/realtime frame.  Keeping their commit synchronous makes the
# UI wait behind slow NAS WAL fsyncs; durable documents, account data, orders and
# research exports continue to use PostgreSQL's default synchronous commit.

# SQLite builds can still use the historical 999-variable limit. Daily bars
# use 10 values and observation metadata uses 12, so 80 rows remain below it.
# PostgreSQL's protocol limit is much larger; 1,000 keeps one ka10081 page in
# one statement while bounding statement size for other callers.
















logger = logging.getLogger(__name__)


# Keep the historical importer helper's existing call contract at the composition root.
_complete_external_news_job = partial(
    _complete_external_news_job_with_policy, candidate_grouper=grouped_candidate_identities,
)


class QueryStore(QueryCacheStore, Protocol):
    def find_credential_activation(self, *, operation_id: str = "", provider: str = "", profile_id: str = "", request_id: str = "") -> dict[str, Any] | None: ...
    def list_credential_profiles(self) -> list[dict[str, Any]]: ...
    def create_credential_profile(self, provider: str, request_id: str, label: str, digest: str) -> dict[str, Any]: ...
    def archive_credential_profile(self, provider: str, profile_id: str) -> dict[str, Any]: ...
    def rename_credential_profile(self, provider: str, profile_id: str, label: str) -> dict[str, Any]: ...
    def register_credential_profile(self, provider: str, profile_id: str, created_at: str) -> None: ...
    def finalize_credential_activation(self, value: dict[str, Any]) -> dict[str, Any]: ...
    def load_account_settings(self, scope: dict[str, str]) -> dict[str, Any]: ...
    def save_real_account_recovery(self, binding, recovery, received_at: datetime, *, settings_revision: int) -> dict[str, Any]: ...
    def save_real_account_event(self, binding, event_type, event, received_at: datetime, *, settings_revision: int) -> dict[str, Any]: ...
    def save_account_settings(self, value: dict[str, Any], *, expected_revision: int) -> dict[str, Any]: ...
    def load_market_profile_settings(self) -> dict[str, Any]: ...
    def save_market_profile_settings(self, value: dict[str, Any], *, expected_revision: int) -> dict[str, Any]: ...
    def load_credential_activations(self, profile_id: str) -> list[dict[str, Any]]: ...
    def initialize(self) -> None: ...
    def save_realtime_snapshots(self, values: list[dict[str, Any]]) -> None: ...
    def load_realtime_snapshots(self, codes: list[str]) -> list[dict[str, Any]]: ...
    def load_latest_market_caps(self, codes: list[str]) -> list[dict[str, Any]]: ...
    def save_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None: ...
    def save_second_trade_bars(self, values: list[dict[str, Any]]) -> None: ...
    def finalize_minute_bars(self, values: list[dict[str, Any]]) -> None: ...
    def replace_minute_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> None: ...
    def load_minute_bars(
        self, code: str, trading_date: str, market: str = "", *,
        realtime_deltas: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]: ...
    def save_five_minute_bars(self, values: list[dict[str, Any]]) -> None: ...
    def load_five_minute_bars(self, code: str, trading_date: str, adjustment_mode: str = "adjusted") -> list[dict[str, Any]]: ...
    def replace_daily_bars(
        self, values: list[dict[str, Any]], *,
        observations: list[tuple[str, MarketDataObservation[object]]] | None = None,
    ) -> tuple[tuple[str, str, str], ...]: ...
    def load_daily_bars(self, code: str, market: str = "", limit: int = 250) -> list[dict[str, Any]]: ...
    def save_dataset_snapshot(
        self, kind: str, subject: str, snapshot_key: str, payload: dict[str, Any],
        *, observation: MarketDataObservation[object] | None = None,
    ) -> None: ...
    def save_dataset_snapshots(self, values: list[DatasetSnapshotWrite]) -> None: ...
    def load_dataset_snapshots(self, kind: str, subject: str = "", limit: int = 100) -> list[dict[str, Any]]: ...
    def load_top20_statistics(self, start_date: str, end_date: str) -> dict[str, object]: ...
    def load_observation_revisions(
        self, kind: str, subject: str = "", limit: int = 100,
    ) -> list[dict[str, Any]]: ...
    def load_observation_revisions_after(
        self, after_sequence: int, kinds: tuple[str, ...], limit: int = 1000,
    ) -> list[dict[str, Any]]: ...
    def load_observation_revision_page(
        self, after_sequence: int, kinds: tuple[str, ...], limit: int = 1000,
        *, through_sequence: int | None = None,
    ) -> ObservationRevisionPage: ...
    def load_observation_bootstrap(
        self, kinds: tuple[str, ...], per_kind_limit: int = 5000,
    ) -> ObservationRevisionPage: ...
    def load_shadow_monitor_state(self, monitor_id: str) -> dict[str, Any] | None: ...
    def save_shadow_monitor_state(self, monitor_id: str, document: dict[str, Any]) -> None: ...
    def save_shadow_evaluation(
        self, monitor_id: str, decision: dict[str, Any],
        candidate: dict[str, Any] | None, expires_at: str = "",
    ) -> None: ...
    def load_shadow_candidates(self, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]: ...
    def create_observation_export(
        self, start: datetime, end: datetime, kinds: tuple[str, ...], subject: str = "",
    ) -> dict[str, Any]: ...
    def load_observation_export_page(
        self, watermark: str, cursor: int = 0, limit: int = 1000,
    ) -> dict[str, Any]: ...
    def save_market_data_metadata(self, observation_key: str, observation: MarketDataObservation[object]) -> None: ...
    def load_market_data_metadata(self, kind: MarketDatasetKind, subject: str, observation_key: str) -> MarketDataMetadata | None: ...
    def load_market_data_metadata_range(
        self, kind: MarketDatasetKind, subject: str, start: datetime, end: datetime,
    ) -> list[CoverageObservation]: ...
    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...
    def replace_documents(self, collection: str, values: list[dict[str, Any]]) -> None: ...
    def load_documents(self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
                       updated_after: float = 0.0) -> list[dict[str, Any]]: ...
    def load_document(
        self, collection: str, owner: str, key: str,
    ) -> dict[str, Any] | None: ...
    def load_theme_snapshots(self, *, available_at: float | None = None,
                             limit: int = 100) -> list[dict[str, Any]]: ...
    def enqueue_news_ai_jobs(self, values: list[dict[str, Any]]) -> int: ...
    def claim_news_jobs(self, *, limit: int = 1, now: float | None = None,
                        priority_stock_code: str = "", preferred_stage: str = "") -> list[dict[str, Any]]: ...
    def claim_external_historical_news_job(self, stage: str,
                                           excluded_codes: tuple[str, ...] = (),
                                           scope: str = "all") -> dict[str, Any] | None: ...
    def complete_external_historical_news_job(self, value: dict[str, Any]) -> dict[str, str]: ...
    def save_historical_market_news_batch(self, source: str, target_date: str,
                                          batch_id: str, items: list[dict[str, Any]],
                                          processing_owner: str = "nas") -> dict[str, Any]: ...
    def finish_news_job(self, job_key: str, output_ref: str) -> None: ...
    def retry_news_job(self, job_key: str, error: str, next_retry_at: float,
                       output_ref: str = "") -> None: ...
    def save_news_body_revision(self, value: dict[str, Any]) -> str: ...
    def save_news_ai_results(self, documents: list[dict[str, Any]],
                             revisions: list[dict[str, Any]],
                             usage_documents: list[dict[str, Any]] | None = None) -> None: ...
    def load_news_history(self, kind: str, *, target: str = "", identity: str = "",
                          available_at: float | None = None, limit: int = 100) -> list[dict[str, Any]]: ...
    def load_latest_news_body(self, article_revision_id: str) -> dict[str, Any] | None: ...
    def load_news_body_revision(self, body_revision_id: str) -> dict[str, Any] | None: ...
    def load_news_article_revision(self, article_revision_id: str) -> dict[str, Any] | None: ...
    def load_stock_news_articles(self, stock_code: str, *, limit: int = 1000) -> list[dict[str, Any]]: ...
    def load_confirmed_news_articles(self, stock_code: str, *, limit: int = 1000) -> list[dict[str, Any]]: ...
    def save_news_event_revision(self, value: dict[str, Any]) -> str: ...
    def claim_news_request(self, scope: str, *, scope_limit: int, hard_limit: int,
                           budget_date: str) -> bool: ...
    def news_request_count(self, budget_date: str) -> int: ...
    def load_news_source_cursor(self, source_id: str) -> dict[str, Any] | None: ...
    def save_news_source_page(self, value: dict[str, Any]) -> dict[str, Any]: ...
    def load_news_source_diagnostics(self, *, source_id: str = "", days: int = 7,
                                     limit: int = 100) -> dict[str, Any]: ...
    def load_market_news_feed(self, source: str, *, limit: int = 200) -> list[dict[str, Any]]: ...
    def find_news_ai_revision(
        self, *, target_id: str, article_revision_id: str, body_revision_id: str,
        provider: str, model: str, prompt_version: str, schema_version: str,
        input_hash: str,
    ) -> str | None: ...
    def append_vi_events(self, values: list[dict[str, Any]]) -> int: ...
    def record_hot_cohort_revision(self, value: dict[str, Any],
                                   current: dict[str, Any] | None = None) -> bool: ...
    def load_hot_cohort(self, *, active_only: bool = False) -> list[dict[str, Any]]: ...
    def append_upper_limit_facts(self, values: list[dict[str, Any]]) -> int: ...
    def load_market_event_history(self, kind: str, *, code: str = "",
                                  limit: int = 100) -> list[dict[str, Any]]: ...
    def save_external_bars(self, values: list[dict[str, Any]]) -> None: ...
    def load_external_bars(self, instrument: str, timeframe: str, limit: int = 1000) -> list[dict[str, Any]]: ...
    def create_execution_intent(self, value: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool: ...
    def append_execution_event(self, intent: dict[str, Any], event: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool: ...
    def load_execution_intent(self, intent_id: str) -> dict[str, Any] | None: ...
    def load_active_execution_intents(
        self, environment: str, account_ref: str, run_id: str,
    ) -> list[dict[str, Any]]: ...
    def find_execution_intent_by_broker_order_id(
        self, environment: str, account_ref: str, run_id: str, broker_order_id: str,
    ) -> dict[str, Any] | None: ...
    def load_execution_events(self, intent_id: str) -> list[dict[str, Any]]: ...
    def load_account_execution_events(
        self, environment: str, account_ref: str, after_sequence: int, limit: int,
    ) -> list[dict[str, Any]]: ...
    def save_execution_account_snapshot(self, value: dict[str, Any], *, ownership: dict[str, str] | None = None) -> bool: ...
    def load_mock_automation_control(self, account_ref: str) -> dict[str, Any] | None: ...
    def save_mock_automation_control(
        self, value: dict[str, Any], *, expected_revision: int,
    ) -> bool: ...
    def register_account_identity(self, value: dict[str, Any]) -> str: ...
    def append_account_binding(self, value: dict[str, Any]) -> dict[str, Any]: ...
    def load_account_bindings(self) -> list[dict[str, Any]]: ...
    def register_account_scope_alias(self, value: dict[str, Any]) -> dict[str, Any]: ...
    def resolve_account_scope(self, broker: str, environment: str, account_ref: str) -> dict[str, Any]: ...
    def acquire_execution_runtime(
        self, owner_key: str, owner_token: str, now: str, lease_expires_at: str,
    ) -> bool: ...
    def release_execution_runtime(self, owner_key: str, owner_token: str) -> bool: ...
    def close(self) -> None: ...
    def storage_size_bytes(self) -> int | None: ...
    def storage_breakdown(self) -> list[dict[str, object]]: ...


class SQLiteQueryStore(
    SQLiteHistoricalNewsStoreMixin,
    SQLiteNewsSourceStoreMixin,
    SQLiteNewsRevisionStoreMixin,
    SQLiteNewsJobStoreMixin,
    SQLiteMarketMetadataStoreMixin,
    SQLiteMarketBarStoreMixin,
    SQLiteShadowStateStoreMixin,
    SQLiteExecutionStoreMixin,
    SQLiteCredentialStoreMixin,
    SQLiteAccountIdentityStoreMixin,
    SQLiteAccountSettingsStoreMixin,
    SQLiteExternalMarketStoreMixin,
    SQLiteQueryCacheStoreMixin,
    SQLiteStorageDiagnosticsStoreMixin,
    SQLiteRealtimeSnapshotStoreMixin,
    SQLiteMarketEventStoreMixin,
    SQLiteResearchExportStoreMixin,
    SQLiteDocumentStoreMixin,
    SQLiteTop20StatisticsStoreMixin,
    SQLiteObservationReaderStoreMixin,
    SQLiteDatasetStoreMixin,
):


    """로컬 중앙 서버가 기존 monitor.sqlite3 안에서 사용하는 조회 캐시."""

    _candidate_grouper = staticmethod(grouped_candidate_identities)

    def __init__(self, path: Path, *, observation_history_enabled: bool = True) -> None:
        self._path = path
        self._observation_history_enabled = observation_history_enabled
        self._lock = RLock()
        self._memory_uri = f"file:central-query-store-{id(self)}?mode=memory&cache=shared" if str(path) == ":memory:" else ""
        self._keeper: sqlite3.Connection | None = None
        from .diagnostic_replay_contract import install_store_capture
        install_store_capture(self)

    def initialize(self) -> None:
        if self._memory_uri:
            self._keeper = sqlite3.connect(self._memory_uri, uri=True, check_same_thread=False)
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            with connection as transaction:
                cursor = transaction.cursor()
                CentralSchemaMigrationRunner(cursor, "sqlite").apply(
                    central_schema_migrations("sqlite")
                )

    def close(self) -> None:
        keeper, self._keeper = self._keeper, None
        if keeper is not None:
            keeper.close()












































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


class PostgresQueryStore(
    PostgresHistoricalNewsStoreMixin,
    PostgresNewsSourceStoreMixin,
    PostgresNewsRevisionStoreMixin,
    PostgresNewsJobStoreMixin,
    PostgresMarketMetadataStoreMixin,
    PostgresMarketBarStoreMixin,
    PostgresShadowStateStoreMixin,
    PostgresExecutionStoreMixin,
    PostgresCredentialStoreMixin,
    PostgresAccountIdentityStoreMixin,
    PostgresAccountSettingsStoreMixin,
    PostgresExternalMarketStoreMixin,
    PostgresQueryCacheStoreMixin,
    PostgresStorageDiagnosticsStoreMixin,
    PostgresRealtimeSnapshotStoreMixin,
    PostgresMarketEventStoreMixin,
    PostgresResearchExportStoreMixin,
    PostgresDocumentStoreMixin,
    PostgresTop20StatisticsStoreMixin,
    PostgresObservationReaderStoreMixin,
    PostgresDatasetStoreMixin,
):


    """시놀로지 PostgreSQL에서 사용하는 동일 규격의 조회 캐시."""

    _candidate_grouper = staticmethod(grouped_candidate_identities)

    def __init__(self, database_url: str, *, observation_history_enabled: bool = True,
                 shadow_checkpoint_frames_enabled: bool = False) -> None:
        self._database_url = database_url
        self._observation_delivery = ObservationDeliveryState()
        self._observation_history_enabled = observation_history_enabled
        # Opt in only after storage/rollback and dedicated-PG gates pass.
        self._shadow_checkpoint_frames_enabled = shadow_checkpoint_frames_enabled
        from .diagnostic_replay_contract import install_store_capture
        install_store_capture(self)

    def initialize(self) -> None:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(
            writer_family="schema.migration", writer_kind="central_schema",
            operation="initialize",
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            CentralSchemaMigrationRunner(cursor, "postgres").apply(
                central_schema_migrations()
            )

    def close(self) -> None:
        return














































    def _connect(self):
        try:
            import psycopg
        except ImportError as error:
            raise RuntimeError("PostgreSQL 서버 의존성을 설치하세요: pip install -e .[server]") from error
        return psycopg.connect(self._database_url)


def create_query_store(
    database_url: str, *, observation_history_enabled: bool = True,
) -> QueryStore:
    if database_url.startswith("sqlite:///"):
        raw = unquote(database_url.removeprefix("sqlite:///"))
        # sqlite:///C:/...와 sqlite:///relative/path를 모두 지원한다.
        return SQLiteQueryStore(
            Path(raw), observation_history_enabled=observation_history_enabled,
        )
    parsed = urlsplit(database_url)
    if parsed.scheme in {"postgres", "postgresql"}:
        return PostgresQueryStore(
            database_url, observation_history_enabled=observation_history_enabled,
        )
    raise ValueError("중앙 DB 주소는 sqlite:/// 또는 postgresql:// 형식이어야 합니다.")
