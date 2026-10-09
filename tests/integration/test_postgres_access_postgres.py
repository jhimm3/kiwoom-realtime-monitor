"""Opt-in DB access pilot checks; refuses every DB except the dedicated test DB."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import psycopg
import sqlite3
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from kiwoom_monitor.central_server.database import (
    PostgresQueryStore, StoredQuery, _NEWS_JOB_CLAIM_SELECT_SQL,
    _NEWS_JOB_CLAIM_DIAGNOSTIC_CANDIDATE_SQL,
)
from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigration
from kiwoom_monitor.central_server.central_schema import central_schema_migrations
from kiwoom_monitor.central_server.credential_store import CredentialStore, CredentialStoreError
from kiwoom_monitor.central_server.diagnostic_metrics import (
    refresh_capture_state, summarize_db_calls, summarize_market_bar_saves,
)
from kiwoom_monitor.central_server.diagnostic_workloads import instance_id
from kiwoom_monitor.central_server.app import (
    _stored_market_response, _trade_value_comparison_summary,
)
from kiwoom_monitor.central_server.market_observations import (
    KST, bar_observation_key, daily_bar_observation, market_state_observation,
    minute_bar_observation,
    ranking_observation, top20_index_observation,
)
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness, DataValueKind, MarketDatasetKind, ObservationOrigin,
)
from kiwoom_monitor.central_server.postgres_access import (
    DBWriterContext, ObservedDBCursor, open_observed_connection,
)
from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
from kiwoom_monitor.central_server.market_events import MarketEventService
from kiwoom_monitor.central_server.news_jobs import NewsJobRunner
from kiwoom_monitor.central_server.external_market_collector import YahooDelayedMarketCollector
from kiwoom_monitor.central_server.market_ingest import MarketDataIngestor
from kiwoom_monitor.central_server.market_news_sources import MarketFeedNewsCollector
from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
from kiwoom_monitor.application.breakout_strategy import StrategyState, default_shadow_breakout_config
from kiwoom_monitor.application.breakout_strategy import StrategyDecision
from kiwoom_monitor.application.research_families import BREAKOUT_FAMILY_ID
from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
from kiwoom_monitor.application.mock_automation_admission import (
    MOCK_AUTOMATION_CONTROL_VERSION, MockAutomationControl,
    MockAutomationDesiredState, build_mock_automation_admission,
    build_mock_automation_lease_receipt,
)
from kiwoom_monitor.application.mock_automation_recovery import (
    record_mock_automation_recovery_from_risk,
)
from kiwoom_monitor.application.mock_automation_execution import (
    MOCK_AUTOMATION_DECISION_GATE_VERSION, MOCK_AUTOMATION_DISPATCH_RECEIPT_VERSION,
    MockAutomationGateStatus, MockAutomationLiveMetrics,
    assess_mock_automation_decision, emergency_stop_mock_automation,
    mock_automation_decision_gate_from_dict,
    mock_automation_dispatch_receipt_from_dict,
)
from kiwoom_monitor.domain.execution_activation import MockAutomationOperatingSpec
from kiwoom_monitor.domain.order_contract import (
    AccountBinding, AccountEnvironment, AccountScope, AccountSnapshot,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import AccountRecovery, MockAccountRecovery
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import OrderExecution
from kiwoom_monitor.infrastructure.central_content_sync import (
    CentralContentSyncService, _journal_news_link_key,
)
from kiwoom_monitor.infrastructure.central_journal_sync import (
    CentralJournalSyncService, _V1_SPECS, _V2_SPECS,
)
from kiwoom_monitor.infrastructure.central_settings_sync import CentralSettingsSyncService
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)
from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
from kiwoom_monitor.application.news_rules import classify_supply_contract
from kiwoom_monitor.domain.news_observation import (
    ARTICLE_BODY_EXTRACTOR_VERSION, NEWS_ANALYSIS_SCHEMA_VERSION,
)


class PostgresAccessIntegrationTests(unittest.TestCase):
    def test_daily_history_short_source_reuse_and_failed_storage_keep_separate_observed_commits(self):
        from kiwoom_monitor.application.daily_bar_coverage import COLLECTION, assess_daily_coverage
        from kiwoom_monitor.central_server.rest_broker import BrokerResult
        from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
        code = f"DIAG{uuid.uuid4().hex[:16]}"
        self.query_bar_codes.append(code)
        self.document_keys.append((COLLECTION, f"{code}:KRX", "initial"))
        now = datetime.now(KST)
        day = now.date().isoformat()
        store = self.store

        class Broker:
            calls = 0
            fail_save = False

            async def request(self, api_id, path, body, **kwargs):
                self.calls += 1
                rows = [{"dt": (now.date() - timedelta(days=i + 1)).strftime("%Y%m%d"),
                         "open_pric": "100", "high_pric": str(110 + i), "low_pric": "90",
                         "cur_prc": "105", "trde_qty": "10", "trde_prica": "200"} for i in range(3)]
                payload = {"stk_dt_pole_chart_qry": rows}
                if not self.fail_save:
                    MarketDataIngestor(store).ingest(api_id, body, payload)
                return BrokerResult(payload, False, "", recording_succeeded=True)

        broker = Broker()
        service = AutonomousTop20Service(broker, RealtimeHub(), store, now_provider=lambda: now)
        started = time.time()

        async def run():
            first = await service._ensure_daily_history(code, day, "KRX", scope="initial")
            second = await service._ensure_daily_history(code, day, "KRX", scope="initial")
            self.assertEqual(first, second)
            self.assertEqual("ready", first["periods"]["250"]["status"])
            self.assertEqual(3, first["periods"]["250"]["available_count"])
            self.assertEqual(1, broker.calls)
            with store._connect() as connection, connection.cursor() as cursor:
                cursor.execute("DELETE FROM central_daily_bars WHERE code=%s AND trading_date=%s",
                               (code, (now.date() - timedelta(days=1)).isoformat()))
            broker.fail_save = True
            with self.assertRaisesRegex(RuntimeError, "저장값"):
                await service._ensure_daily_history(code, day, "KRX", scope="initial")

        asyncio.run(run())
        documents = store.load_documents(COLLECTION, f"{code}:KRX", 2)
        self.assertEqual(1, len(documents))
        self.assertFalse(assess_daily_coverage(store.load_daily_bars(code, "KRX", 250), documents[0]["document"],
                         code=code, market="KRX", query_basis_date=day)["collection_verified"])
        calls = summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
        writes = [call for call in calls if call["writer_kind"] in {"query_daily", f"document:{COLLECTION}"}
                  and call.get("access_mode") == "write"]
        self.assertEqual(2, len(writes))
        self.assertEqual(2, len({call["call_id"] for call in writes}))
        self.assertTrue(all(call["transactions"] == 1 and call["commits"] == 1 for call in writes))

    @classmethod
    def setUpClass(cls) -> None:
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            raise unittest.SkipTest("dedicated PostgreSQL test database not configured")
        if urlsplit(url).path.lstrip("/") != "kiwoom_monitor_diagnostic_test":
            raise RuntimeError("refusing DB access test outside dedicated diagnostic DB")
        cls.store = PostgresQueryStore(url)
        with cls.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != "kiwoom_monitor_diagnostic_test":
                raise RuntimeError("connected database is not the dedicated test database")
            if os.environ.get("KIWOOM_DIAGNOSTIC_REQUIRE_EXISTING_CACHE_SCHEMA") == "1":
                cursor.execute("SELECT to_regclass('central_api_query_cache')")
                if cursor.fetchone()[0] is None:
                    raise RuntimeError("diagnostic cache table is unavailable")
                cursor.execute("SELECT to_regclass('central_news_jobs')")
                if cursor.fetchone()[0] is None:
                    raise RuntimeError("diagnostic news jobs table is unavailable")
                cursor.execute("SELECT to_regclass('central_documents')")
                if cursor.fetchone()[0] is None:
                    raise RuntimeError("diagnostic documents table is unavailable")
                return
        cls.store.initialize()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.control = Path(self.tmp.name) / "controls.json"
        self.control.write_text(json.dumps({
            "schema": 1, "instance_id": instance_id(),
            "diagnostic_tool": {"expires_at": time.time() + 60, "session_id": uuid.uuid4().hex},
            "capture": {"expires_at": time.time() + 60},
        }), encoding="utf-8")
        self.environment = patch.dict(os.environ, {
            "KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(self.control),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        refresh_capture_state(force=True)
        self.keys: list[str] = []
        self.news_keys: list[str] = []
        self.article_revision_ids: list[str] = []
        self.article_revision_identities: list[tuple[str, str]] = []
        self.theme_snapshot_profile_ids: list[str] = []
        self.source_ids: list[str] = []
        self.document_keys: list[tuple[str, str, str]] = []
        self.real_account_document_owners: list[str] = []
        self.news_budget_keys: list[tuple[str, str]] = []
        self.market_event_codes: list[str] = []
        self.realtime_codes: list[str] = []
        self.query_bar_codes: list[str] = []
        self.realtime_operation_ids: list[str] = []
        self.external_bar_instruments: list[str] = []
        self.dataset_snapshot_keys: list[tuple[str, str, str]] = []
        self.shadow_monitor_ids: list[str] = []
        self.execution_intent_ids: list[str] = []
        self.execution_account_refs: list[str] = []
        self.execution_owner_keys: list[str] = []
        self.account_registry_refs: list[str] = []
        self.account_scope_alias_refs: list[str] = []
        self.account_binding_profile_ids: list[str] = []
        self.credential_activation_profile_ids: list[str] = []
        self.credential_profile_ids: list[str] = []
        self.credential_profile_request_keys: list[tuple[str, str]] = []
        self.research_export_ids: list[str] = []

    def tearDown(self) -> None:
        try:
            if self.research_export_ids:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM central_research_export_members WHERE dataset_id=ANY(%s)",
                        (self.research_export_ids,),
                    )
                    cursor.execute(
                        "DELETE FROM central_research_exports WHERE dataset_id=ANY(%s)",
                        (self.research_export_ids,),
                    )
                    for table in ("central_research_export_members", "central_research_exports"):
                        cursor.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE dataset_id=ANY(%s)",
                            (self.research_export_ids,),
                        )
                        self.assertEqual(0, cursor.fetchone()[0], table)
            if self.credential_profile_request_keys:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    for provider, request_id in self.credential_profile_request_keys:
                        cursor.execute(
                            "SELECT document_json FROM central_documents "
                            "WHERE collection='credential_profile_requests' "
                            "AND owner=%s AND document_key=%s", (provider, request_id),
                        )
                        row = cursor.fetchone()
                        if row:
                            document = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                            self.credential_profile_ids.append(document["profile_id"])
            if self.article_revision_identities:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    for stock, identity in self.article_revision_identities:
                        cursor.execute(
                            "SELECT article_revision_id FROM central_news_article_revisions "
                            "WHERE stock_code=%s AND identity=%s", (stock, identity),
                        )
                        self.article_revision_ids.extend(str(row[0]) for row in cursor.fetchall())
            if self.theme_snapshot_profile_ids:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM central_theme_snapshots WHERE profile_id=ANY(%s)",
                        (self.theme_snapshot_profile_ids,),
                    )
                    cursor.execute(
                        "SELECT COUNT(*) FROM central_theme_snapshots WHERE profile_id=ANY(%s)",
                        (self.theme_snapshot_profile_ids,),
                    )
                    self.assertEqual(0, cursor.fetchone()[0])
            if self.keys:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute("DELETE FROM central_api_query_cache WHERE cache_key=ANY(%s)",
                                   (self.keys,))
            if self.dataset_snapshot_keys:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    for kind, subject, key in self.dataset_snapshot_keys:
                        revision_kind = kind if kind in {"ranking", "top20_membership"} else None
                        metadata_kind = (
                            MarketDatasetKind.CANDIDATE_SET.value
                            if revision_kind else kind
                        )
                        if revision_kind:
                            cursor.execute(
                                "DELETE FROM central_observation_revisions "
                                "WHERE kind=%s AND subject=%s AND observation_key=%s",
                                (revision_kind, subject, key),
                            )
                        cursor.execute(
                            "DELETE FROM central_dataset_snapshots "
                            "WHERE kind=%s AND subject=%s AND snapshot_key=%s",
                            (kind, subject, key),
                        )
                        cursor.execute(
                            "DELETE FROM central_market_data_observation_meta "
                            "WHERE dataset_kind=%s AND subject=%s AND observation_key=%s",
                            (metadata_kind, subject, key),
                        )
                        cursor.execute(
                            "SELECT (SELECT COUNT(*) FROM central_dataset_snapshots "
                            "WHERE kind=%s AND subject=%s AND snapshot_key=%s) + "
                            "(SELECT COUNT(*) FROM central_market_data_observation_meta "
                            "WHERE dataset_kind=%s AND subject=%s AND observation_key=%s) + "
                            "(SELECT COUNT(*) FROM central_observation_revisions "
                            "WHERE kind=%s AND subject=%s AND observation_key=%s)",
                            (kind, subject, key, metadata_kind, subject, key,
                             revision_kind or "", subject, key),
                        )
                        self.assertEqual(0, cursor.fetchone()[0], (kind, subject, key))
            if self.news_keys:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute("DELETE FROM central_news_jobs WHERE job_key=ANY(%s)",
                                   (self.news_keys,))
                    cursor.execute("SELECT COUNT(*) FROM central_news_jobs WHERE job_key=ANY(%s)",
                                   (self.news_keys,))
                    self.assertEqual(0, cursor.fetchone()[0])
            if self.source_ids:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT DISTINCT article_revision_id FROM central_news_source_observations "
                        "WHERE source_id=ANY(%s)", (self.source_ids,),
                    )
                    self.article_revision_ids.extend(str(row[0]) for row in cursor.fetchall())
                    for table in ("central_news_source_observations", "central_news_source_runs",
                                  "central_news_source_cursors"):
                        cursor.execute(f"DELETE FROM {table} WHERE source_id=ANY(%s)",
                                       (self.source_ids,))
                        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE source_id=ANY(%s)",
                                       (self.source_ids,))
                        self.assertEqual(0, cursor.fetchone()[0], table)
            if self.article_revision_ids:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM central_news_event_membership_revisions "
                        "WHERE article_revision_id=ANY(%s)", (self.article_revision_ids,),
                    )
                    cursor.execute(
                        "DELETE FROM central_news_event_revisions "
                        "WHERE article_revision_id=ANY(%s)", (self.article_revision_ids,),
                    )
                    cursor.execute(
                        "DELETE FROM central_news_ai_revisions WHERE article_revision_id=ANY(%s)",
                        (self.article_revision_ids,),
                    )
                    cursor.execute(
                        "DELETE FROM central_news_article_target_revisions "
                        "WHERE article_revision_id=ANY(%s)", (self.article_revision_ids,),
                    )
                    cursor.execute(
                        "DELETE FROM central_news_jobs WHERE article_revision_id=ANY(%s)",
                        (self.article_revision_ids,),
                    )
                    cursor.execute(
                        "DELETE FROM central_news_body_revisions WHERE article_revision_id=ANY(%s)",
                        (self.article_revision_ids,),
                    )
                    cursor.execute(
                        "DELETE FROM central_news_article_revisions "
                        "WHERE article_revision_id=ANY(%s)", (self.article_revision_ids,),
                    )
                    for table in ("central_news_event_membership_revisions",
                                  "central_news_event_revisions", "central_news_ai_revisions",
                                  "central_news_article_target_revisions",
                                  "central_news_jobs",
                                  "central_news_body_revisions",
                                  "central_news_article_revisions"):
                        cursor.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE article_revision_id=ANY(%s)",
                            (self.article_revision_ids,),
                        )
                        self.assertEqual(0, cursor.fetchone()[0], table)
            if self.document_keys:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    for collection, owner, key in self.document_keys:
                        cursor.execute(
                            "DELETE FROM central_documents WHERE collection=%s AND owner=%s AND document_key=%s",
                            (collection, owner, key),
                        )
                        cursor.execute(
                            "SELECT COUNT(*) FROM central_documents WHERE collection=%s "
                            "AND owner=%s AND document_key=%s", (collection, owner, key),
                        )
                        self.assertEqual(0, cursor.fetchone()[0])
            if self.real_account_document_owners:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM central_documents WHERE collection IN "
                        "('real_account_event','real_account_recovery') AND owner=ANY(%s)",
                        (self.real_account_document_owners,),
                    )
                    cursor.execute(
                        "SELECT COUNT(*) FROM central_documents WHERE collection IN "
                        "('real_account_event','real_account_recovery') AND owner=ANY(%s)",
                        (self.real_account_document_owners,),
                    )
                    self.assertEqual(0, cursor.fetchone()[0])
            if self.news_budget_keys:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    for budget_date, scope in self.news_budget_keys:
                        cursor.execute(
                            "DELETE FROM central_news_request_budget WHERE budget_date=%s AND scope=%s",
                            (budget_date, scope),
                        )
                        cursor.execute(
                            "SELECT COUNT(*) FROM central_news_request_budget "
                            "WHERE budget_date=%s AND scope=%s", (budget_date, scope),
                        )
                        self.assertEqual(0, cursor.fetchone()[0])
            if self.market_event_codes:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    for table in ("central_vi_event_revisions", "central_hot_cohort_revisions",
                                  "central_hot_cohort_current", "central_upper_limit_fact_revisions"):
                        cursor.execute(f"DELETE FROM {table} WHERE stock_code=ANY(%s)",
                                       (self.market_event_codes,))
                        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE stock_code=ANY(%s)",
                                       (self.market_event_codes,))
                        self.assertEqual(0, cursor.fetchone()[0], table)
            if self.realtime_codes:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    subjects = [f"{code}:KRX" for code in self.realtime_codes]
                    for table, column, keys in (
                        ("central_realtime_latest", "item_key", self.realtime_codes),
                        ("central_minute_bars", "code", self.realtime_codes),
                        ("central_second_trade_bars", "code", self.realtime_codes),
                        ("central_market_data_observation_meta", "subject", subjects),
                        ("central_observation_revisions", "subject", subjects),
                        ("central_minute_bar_operations", "operation_id", self.realtime_operation_ids),
                    ):
                        cursor.execute(f"DELETE FROM {table} WHERE {column}=ANY(%s)", (keys,))
                        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=ANY(%s)", (keys,))
                        self.assertEqual(0, cursor.fetchone()[0], table)
            if self.query_bar_codes:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    subjects = [f"{code}:KRX" for code in self.query_bar_codes]
                    for table, column, keys in (
                        ("central_minute_bars", "code", self.query_bar_codes),
                        ("central_daily_bars", "code", self.query_bar_codes),
                        ("central_market_data_observation_meta", "subject", subjects),
                        ("central_observation_revisions", "subject", subjects),
                    ):
                        cursor.execute(f"DELETE FROM {table} WHERE {column}=ANY(%s)", (keys,))
                        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=ANY(%s)", (keys,))
                        self.assertEqual(0, cursor.fetchone()[0], table)
            if self.external_bar_instruments:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM central_external_bars WHERE instrument=ANY(%s)",
                        (self.external_bar_instruments,),
                    )
                    cursor.execute(
                        "SELECT COUNT(*) FROM central_external_bars WHERE instrument=ANY(%s)",
                        (self.external_bar_instruments,),
                    )
                    self.assertEqual(0, cursor.fetchone()[0])
            if self.shadow_monitor_ids:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    for table in ("central_shadow_candidate_events", "central_shadow_decisions",
                                  "central_shadow_monitor_state"):
                        cursor.execute(f"DELETE FROM {table} WHERE monitor_id=ANY(%s)",
                                       (self.shadow_monitor_ids,))
                        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE monitor_id=ANY(%s)",
                                       (self.shadow_monitor_ids,))
                        self.assertEqual(0, cursor.fetchone()[0], table)
            if self.execution_intent_ids:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    for table, column, keys in (
                        ("central_execution_events", "intent_id", self.execution_intent_ids),
                        ("central_execution_intents", "intent_id", self.execution_intent_ids),
                        ("central_execution_account_snapshots", "account_ref", self.execution_account_refs),
                        ("central_execution_runtime_leases", "owner_key", self.execution_owner_keys),
                    ):
                        cursor.execute(f"DELETE FROM {table} WHERE {column}=ANY(%s)", (keys,))
                        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=ANY(%s)", (keys,))
                        self.assertEqual(0, cursor.fetchone()[0], table)
            if self.account_scope_alias_refs:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM central_account_scope_aliases WHERE origin_account_ref=ANY(%s)",
                        (self.account_scope_alias_refs,),
                    )
                    cursor.execute(
                        "SELECT COUNT(*) FROM central_account_scope_aliases WHERE origin_account_ref=ANY(%s)",
                        (self.account_scope_alias_refs,),
                    )
                    self.assertEqual(0, cursor.fetchone()[0])
            if self.account_registry_refs:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    for table, column, keys in (
                        ("central_account_binding_revisions", "credential_profile_id",
                         self.account_binding_profile_ids),
                        ("central_credential_activations", "profile_id",
                         self.credential_activation_profile_ids),
                        ("central_credential_profiles", "profile_id",
                         self.credential_activation_profile_ids),
                        ("central_account_registry", "account_ref", self.account_registry_refs),
                    ):
                        cursor.execute(f"DELETE FROM {table} WHERE {column}=ANY(%s)", (keys,))
                        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=ANY(%s)", (keys,))
                        self.assertEqual(0, cursor.fetchone()[0], table)
            if self.credential_profile_ids:
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM central_credential_profiles WHERE profile_id=ANY(%s)",
                        (self.credential_profile_ids,),
                    )
                    cursor.execute(
                        "SELECT COUNT(*) FROM central_credential_profiles WHERE profile_id=ANY(%s)",
                        (self.credential_profile_ids,),
                    )
                    self.assertEqual(0, cursor.fetchone()[0])
        finally:
            self.control.unlink(missing_ok=True)
            refresh_capture_state(force=True)

    def _key(self) -> str:
        key = f"diagnostic-common-access-{uuid.uuid4().hex}"
        self.keys.append(key)
        return key

    def test_research_export_keeps_fixed_membership_and_rolls_back_partial_failure(self) -> None:
        subject = f"DIAG-{uuid.uuid4().hex}"
        start = datetime(2026, 9, 28, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        keys = [f"2026-09-28T09:{minute:02d}:00+09:00" for minute in (30, 31, 32)]

        def save_revision(index: int) -> None:
            key = keys[index]
            payload = {"query_type": subject, "items": [{"stk_cd": f"DIAG{index}"}]}
            available_at = datetime(2026, 9, 28, 9, 30 + index, tzinfo=KST)
            self.dataset_snapshot_keys.append(("ranking", subject, key))
            self.store.save_dataset_snapshot(
                "ranking", subject, key, payload,
                observation=ranking_observation(
                    subject, key, payload, available_at, source="diagnostic-export",
                ),
            )

        save_revision(0)
        save_revision(1)
        started = time.time() - 1
        first = self.store.create_observation_export(start, end, ("ranking",), subject)
        self.research_export_ids.append(first["dataset_id"])
        page_one = self.store.load_observation_export_page(first["fixed_watermark"], 0, 1)
        page_two = self.store.load_observation_export_page(
            first["fixed_watermark"], page_one["next_cursor"], 1,
        )
        first_rows = [*page_one["observations"], *page_two["observations"]]
        self.assertEqual(keys[:2], [row["observation_key"] for row in first_rows])
        self.assertEqual([1, 2], [row["ordinal"] for row in first_rows])
        self.assertEqual(2, first["revision_count"])
        self.assertEqual(hashlib.sha256("\n".join(
            row["revision_id"] for row in first_rows
        ).encode("utf-8")).hexdigest(), first["revision_ids_hash"])

        save_revision(2)
        self.assertEqual(keys[:2], [
            row["observation_key"] for row in
            self.store.load_observation_export_page(first["fixed_watermark"], 0, 10)["observations"]
        ])
        second = self.store.create_observation_export(start, end, ("ranking",), subject)
        self.research_export_ids.append(second["dataset_id"])
        self.assertEqual(keys, [
            row["observation_key"] for row in
            self.store.load_observation_export_page(second["fixed_watermark"], 0, 10)["observations"]
        ])

        from kiwoom_monitor.central_server import database_research_export as export_module

        original_manifest = export_module._observation_export_manifest
        failed_ids: list[str] = []

        def capture_manifest(*args):
            manifest = original_manifest(*args)
            failed_ids.append(manifest["dataset_id"])
            self.research_export_ids.append(manifest["dataset_id"])
            return manifest

        def fail_after_first_member(cursor, sql, values):
            cursor._raw.execute(sql, values[0])
            raise RuntimeError("injected member write failure")

        with patch.object(export_module, "_observation_export_manifest",
                          side_effect=capture_manifest), patch.object(
                              ObservedDBCursor, "executemany", autospec=True,
                              side_effect=fail_after_first_member,
                          ):
            with self.assertRaisesRegex(RuntimeError, "injected member write failure"):
                self.store.create_observation_export(start, end, ("ranking",), subject)

        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT (SELECT COUNT(*) FROM central_research_exports WHERE dataset_id=%s) + "
                "(SELECT COUNT(*) FROM central_research_export_members WHERE dataset_id=%s)",
                (failed_ids[0], failed_ids[0]),
            )
            self.assertEqual(0, cursor.fetchone()[0])
        self.assertEqual(2, len(self.store.load_observation_export_page(
            first["fixed_watermark"], 0, 10,
        )["observations"]))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "research_observation_export_create"
        ]
        self.assertEqual(3, len(calls))
        self.assertEqual(["committed", "committed", "rolled_back"],
                         [call["outcome"] for call in calls])
        self.assertEqual([1, 1, 0], [call["commits"] for call in calls])
        self.assertEqual([0, 0, 1], [call["rollbacks"] for call in calls])
        self.assertEqual([3, 3], [call["sql_calls"] for call in calls[:2]])
        self.assertEqual(3, len({call["call_id"] for call in calls}))
        self.assertTrue(all(
            call["writer_family"] == "research.observation_export"
            and call["operation"] == "create_observation_export"
            and call["backend_pid"] is not None
            and call["rows_attempted"] is None
            for call in calls
        ))
        reader_calls = [
            call for call in summarize_db_calls(
                started + 1, time.time() + 1, mode="raw",
            )["calls"] if call["writer_family"] == "read.research_export"
            and call["writer_kind"] == "export_page"
        ]
        self.assertEqual(5, len(reader_calls))
        self.assertEqual(5, len({call["call_id"] for call in reader_calls}))
        self.assertTrue(all(call["access_mode"] == "read"
                            and call["transactions"] == 1
                            and call["commits"] == 1
                            and call["sql_calls"] == 2
                            and call["backend_pid"] is not None for call in reader_calls))

    def test_storage_diagnostics_keep_separate_native_reader_transactions(self) -> None:
        started = time.time() - 1
        size = self.store.storage_size_bytes()
        breakdown = self.store.storage_breakdown()
        self.assertIsNotNone(size)
        self.assertGreater(int(size), 0)
        self.assertIsInstance(breakdown, list)

        reader_calls = [
            call for call in summarize_db_calls(
                started + 1, time.time() + 1, mode="raw",
            )["calls"] if call["writer_family"] == "read.diagnostics"
        ]
        self.assertEqual({"database_size": 1, "storage_breakdown": 1}, {
            kind: sum(call["writer_kind"] == kind for call in reader_calls)
            for kind in {call["writer_kind"] for call in reader_calls}
        })
        self.assertEqual([1, 2], sorted(call["sql_calls"] for call in reader_calls))
        self.assertEqual(2, len({call["call_id"] for call in reader_calls}))
        self.assertTrue(all(call["access_mode"] == "read"
                            and call["transactions"] == 1
                            and call["commits"] == 1
                            and call["backend_pid"] is not None for call in reader_calls))

    def test_account_identity_and_binding_keep_separate_commits_and_revision_lock(self) -> None:
        canonical_ref, replay_candidate = str(uuid.uuid4()), str(uuid.uuid4())
        profile_id = str(uuid.uuid4())
        self.account_registry_refs.extend((canonical_ref, replay_candidate))
        self.account_binding_profile_ids.append(profile_id)
        observed_at = datetime.now(timezone.utc).isoformat()
        identity = {
            "broker": "kiwoom", "environment": "mock",
            "identity_fingerprint": uuid.uuid4().hex * 2,
            "created_at": observed_at, "account_ref": canonical_ref,
        }
        started = time.time() - 1
        self.assertEqual(canonical_ref, self.store.register_account_identity(identity))
        self.assertEqual(canonical_ref, self.store.register_account_identity(
            {**identity, "account_ref": replay_candidate},
        ))
        self.assertEqual({"verified": True, "canonical_account_ref": canonical_ref}, {
            key: self.store.resolve_account_scope("kiwoom", "mock", canonical_ref)[key]
            for key in ("verified", "canonical_account_ref")
        })
        binding = {
            "credential_profile_id": profile_id, "broker": "kiwoom",
            "environment": "mock", "account_ref": canonical_ref,
            "verified_at": observed_at, "verification_method": "ka00001",
        }
        with self.assertRaisesRegex(ValueError, "verified active account"):
            self.store.append_account_binding(
                {**binding, "account_ref": str(uuid.uuid4())},
            )
        self.assertEqual([], [row for row in self.store.load_account_bindings()
                              if row["credential_profile_id"] == profile_id])
        self.assertTrue(self.store.resolve_account_scope("kiwoom", "mock", canonical_ref)["verified"])

        barrier = Barrier(2)

        def append() -> dict[str, object]:
            barrier.wait(timeout=30)
            return self.store.append_account_binding(binding)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(append) for _ in range(2)]
            saved = [future.result(timeout=60) for future in futures]
        self.assertEqual([1, 2], sorted(row["binding_revision"] for row in saved))
        stored = [row for row in self.store.load_account_bindings()
                  if row["credential_profile_id"] == profile_id]
        self.assertEqual([1, 2], [row["binding_revision"] for row in stored])
        self.assertEqual({canonical_ref}, {row["account_ref"] for row in stored})
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM central_account_registry WHERE account_ref=ANY(%s)",
                (self.account_registry_refs,),
            )
            self.assertEqual(1, cursor.fetchone()[0])

        kinds = {"account_identity_register", "account_binding_append"}
        calls = [call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
                 if call["writer_kind"] in kinds]
        self.assertEqual(5, len(calls))
        self.assertEqual(5, len({call["call_id"] for call in calls}))
        self.assertTrue(all(call["backend_pid"] is not None for call in calls))
        self.assertEqual(4, sum(call["commits"] for call in calls))
        self.assertEqual(["account_binding_append"], [
            call["writer_kind"] for call in calls if call["rollbacks"] == 1
        ])
        reader_calls = [call for call in summarize_db_calls(
            started + 1, time.time() + 1, mode="raw",
        )["calls"] if call["writer_family"] == "read.account"]
        self.assertEqual({"account_scope": 3, "account_bindings": 2}, {
            kind: sum(call["writer_kind"] == kind for call in reader_calls)
            for kind in {call["writer_kind"] for call in reader_calls}
        })
        self.assertEqual([2, 2, 2], sorted(call["sql_calls"] for call in reader_calls
                                        if call["writer_kind"] == "account_scope"))
        self.assertTrue(all(call["access_mode"] == "read"
                            and call["transactions"] == 1
                            and call["commits"] == 1
                            and call["backend_pid"] is not None for call in reader_calls))

    def test_account_scope_alias_preserves_binding_validation_rollback_and_peer(self) -> None:
        from kiwoom_monitor.central_server import database_account_identity as account_identity_module

        account_ref, origin, failed_origin, profile_id = (str(uuid.uuid4()) for _ in range(4))
        self.account_registry_refs.append(account_ref)
        self.account_binding_profile_ids.append(profile_id)
        self.account_scope_alias_refs.extend((origin, failed_origin))
        observed_at = datetime.now(timezone.utc).isoformat()
        self.store.register_account_identity({
            "broker": "kiwoom", "environment": "mock", "account_ref": account_ref,
            "identity_fingerprint": uuid.uuid4().hex * 2, "created_at": observed_at,
        })
        binding = self.store.append_account_binding({
            "credential_profile_id": profile_id, "broker": "kiwoom", "environment": "mock",
            "account_ref": account_ref, "verified_at": observed_at, "verification_method": "ka00001",
        })
        expected_bindings = [row for row in self.store.load_account_bindings()
                             if row["credential_profile_id"] == profile_id]
        alias = {
            "origin_account_ref": origin, "canonical_account_ref": account_ref,
            "broker": "kiwoom", "environment": "mock", "credential_profile_id": profile_id,
            "binding_revision": binding["binding_revision"], "verified_at": observed_at,
            "verification_method": "ka00001",
        }
        with self.assertRaisesRegex(ValueError, "recorded verified binding"):
            self.store.register_account_scope_alias({**alias, "binding_revision": 999})
        original_values = account_identity_module._account_scope_alias_values

        def invalid_insert_values(document):
            values = list(original_values(document))
            values[5] = "not-a-binding-revision"
            return tuple(values)

        with patch.object(account_identity_module, "_account_scope_alias_values",
                          side_effect=invalid_insert_values):
            with self.assertRaises(psycopg.errors.InvalidTextRepresentation):
                self.store.register_account_scope_alias({**alias, "origin_account_ref": failed_origin})
        self.assertFalse(self.store.resolve_account_scope("kiwoom", "mock", failed_origin)["verified"])
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM central_account_scope_aliases WHERE origin_account_ref=ANY(%s)",
                (self.account_scope_alias_refs,),
            )
            self.assertEqual(0, cursor.fetchone()[0])

        barrier = Barrier(2)

        def register():
            barrier.wait(timeout=30)
            return self.store.register_account_scope_alias(alias)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(register) for _ in range(2)]
            saved = [future.result(timeout=60) for future in futures]
        self.assertEqual([alias, alias], saved)
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.store.register_account_scope_alias({**alias, "binding_revision": 999})
        with self.assertRaisesRegex(ValueError, "chains"):
            self.store.register_account_scope_alias({
                **alias, "origin_account_ref": failed_origin, "canonical_account_ref": origin,
            })
        with self.assertRaisesRegex(ValueError, "canonical account"):
            self.store.register_account_scope_alias({
                **alias, "origin_account_ref": account_ref, "canonical_account_ref": failed_origin,
            })
        resolved = self.store.resolve_account_scope("kiwoom", "mock", origin)
        self.assertEqual((origin, origin, account_ref, True), (
            resolved["account_ref"], resolved["origin_account_ref"],
            resolved["canonical_account_ref"], resolved["verified"],
        ))
        self.assertFalse(self.store.resolve_account_scope("kiwoom", "real", origin)["verified"])
        self.assertEqual(expected_bindings, [row for row in self.store.load_account_bindings()
                                            if row["credential_profile_id"] == profile_id])

    def test_real_account_event_and_recovery_keep_replay_fence_and_separate_commits(self) -> None:
        account_ref, profile_id = str(uuid.uuid4()), str(uuid.uuid4())
        scope = AccountScope("kiwoom", AccountEnvironment.REAL, account_ref)
        owner = f"kiwoom:real:{account_ref}"
        now = datetime.now(timezone.utc)
        self.account_registry_refs.append(account_ref)
        self.account_binding_profile_ids.append(profile_id)
        self.credential_profile_ids.append(profile_id)
        self.document_keys.append(("server_account_settings", owner, "settings"))
        self.real_account_document_owners.append(owner)
        self.store.register_account_identity({
            **scope.to_dict(), "identity_fingerprint": uuid.uuid4().hex * 2,
            "created_at": now.isoformat(),
        })
        self.store.register_credential_profile("kiwoom_real", profile_id, now.isoformat())
        saved_binding = self.store.append_account_binding({
            "credential_profile_id": profile_id, **scope.to_dict(),
            "verified_at": now.isoformat(), "verification_method": "ka00001",
        })
        self.assertEqual(1, saved_binding["binding_revision"])
        binding = AccountBinding(profile_id, scope, 1, now)
        settings = self.store.save_account_settings({
            "scope": scope.to_dict(), "active_profile_id": profile_id,
            "monitor_enabled": True, "mock_order_enabled": False,
        }, expected_revision=0)
        self.assertEqual(1, settings["revision"])

        event = OrderExecution("order-1", "fill-1", "005930", "test", "매수", 1000, 1,
                               "170000", origin_scope=scope)
        recovery = AccountRecovery(AccountSnapshot(account_ref, 500000, 0,
                                                    {"005930": 1}, now), ())
        barrier = Barrier(2)

        def save_event() -> dict[str, object]:
            barrier.wait(timeout=30)
            return self.store.save_real_account_event(binding, "order_execution", event, now,
                                                      settings_revision=1)

        def save_recovery() -> dict[str, object]:
            barrier.wait(timeout=30)
            return self.store.save_real_account_recovery(binding, recovery, now,
                                                         settings_revision=1)

        started = time.time() - 1
        with ThreadPoolExecutor(max_workers=2) as executor:
            event_future = executor.submit(save_event)
            recovery_future = executor.submit(save_recovery)
            event_result = event_future.result(timeout=60)
            recovery_result = recovery_future.result(timeout=60)
        self.assertEqual(event_result, self.store.save_real_account_event(
            binding, "order_execution", event, now, settings_revision=1))
        self.assertEqual(recovery_result, self.store.save_real_account_recovery(
            binding, recovery, now, settings_revision=1))
        self.assertEqual(1, len(self.store.load_documents("real_account_event", owner)))
        self.assertEqual(1, len(self.store.load_documents("real_account_recovery", owner)))
        self.assertEqual(event_result, self.store.load_documents("real_account_event", owner)[0]["document"])
        self.assertEqual(recovery_result, self.store.load_documents("real_account_recovery", owner)[0]["document"])

        with self.assertRaisesRegex(ValueError, "ACCOUNT_CONTEXT_MISMATCH"):
            self.store.save_real_account_event(binding, "order_execution", event, now,
                                               settings_revision=2)
        with self.assertRaisesRegex(ValueError, "REAL_ACCOUNT_RECOVERY_INVALID"):
            invalid = AccountRecovery(AccountSnapshot(str(uuid.uuid4()), 500000, 0, {}, now), ())
            self.store.save_real_account_recovery(binding, invalid, now, settings_revision=1)
        self.assertEqual(1, len(self.store.load_documents("real_account_event", owner)))
        self.assertEqual(1, len(self.store.load_documents("real_account_recovery", owner)))

        kinds = {"real_account_event", "real_account_recovery"}
        calls = [call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
                 if call["writer_kind"] in kinds]
        self.assertEqual(6, len(calls))
        self.assertEqual(6, len({call["call_id"] for call in calls}))
        self.assertEqual(4, sum(call["commits"] for call in calls))
        self.assertEqual(2, sum(call["rollbacks"] for call in calls))
        for kind in kinds:
            kind_calls = [call for call in calls if call["writer_kind"] == kind]
            self.assertEqual((3, 2, 1), (len(kind_calls), sum(call["commits"] for call in kind_calls),
                                          sum(call["rollbacks"] for call in kind_calls)))
            self.assertTrue(all(call["writer_family"] == "account.real_monitor"
                                and call["backend_pid"] is not None
                                and call["rows_attempted"] == 1 for call in kind_calls))

    def test_account_and_market_profile_settings_keep_cas_fence_and_independent_commits(self) -> None:
        accounts = {}
        now = datetime.now(timezone.utc).isoformat()
        for environment in ("real", "mock"):
            account_ref, profile_id = str(uuid.uuid4()), str(uuid.uuid4())
            scope = {"broker": "kiwoom", "environment": environment, "account_ref": account_ref}
            accounts[environment] = (scope, profile_id)
            self.account_registry_refs.append(account_ref)
            self.account_binding_profile_ids.append(profile_id)
            self.credential_profile_ids.append(profile_id)
            self.document_keys.append(("server_account_settings", f"kiwoom:{environment}:{account_ref}",
                                       "settings"))
            self.store.register_account_identity({
                **scope, "identity_fingerprint": uuid.uuid4().hex * 2, "created_at": now,
            })
            self.store.register_credential_profile(f"kiwoom_{environment}", profile_id, now)
            self.assertEqual(1, self.store.append_account_binding({
                "credential_profile_id": profile_id, **scope,
                "verified_at": now, "verification_method": "ka00001",
            })["binding_revision"])

        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT updated_at, document_json FROM central_documents "
                           "WHERE collection='server_market_profile_settings' "
                           "AND owner='global' AND document_key='settings'")
            original_market_row = cursor.fetchone()
        initial_market = self.store.load_market_profile_settings()
        started = time.time() - 1
        barrier = Barrier(2)

        def save_account(environment: str) -> dict[str, object]:
            scope, profile_id = accounts[environment]
            barrier.wait(timeout=30)
            return self.store.save_account_settings({
                "scope": scope, "active_profile_id": profile_id,
                "monitor_enabled": True, "mock_order_enabled": False,
            }, expected_revision=0)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {environment: executor.submit(save_account, environment)
                       for environment in ("real", "mock")}
            settings = {environment: future.result(timeout=60)
                        for environment, future in futures.items()}
        for environment in ("real", "mock"):
            self.assertEqual(1, settings[environment]["revision"])
            self.assertEqual(settings[environment], self.store.load_account_settings(
                accounts[environment][0]))
        self.assertEqual(settings["real"], self.store.save_account_settings(
            {key: settings["real"][key] for key in
             ("scope", "active_profile_id", "monitor_enabled", "mock_order_enabled")},
            expected_revision=1))
        with self.assertRaisesRegex(ValueError, "ACCOUNT_SETTINGS_REVISION_CONFLICT"):
            self.store.save_account_settings({
                key: settings["mock"][key] for key in
                ("scope", "active_profile_id", "monitor_enabled", "mock_order_enabled")
            }, expected_revision=0)

        real_scope, real_profile = accounts["real"]
        market_value = {"market_profile_id": real_profile, "expected_binding_revision": 1}
        try:
            selected = self.store.save_market_profile_settings(
                market_value, expected_revision=initial_market["revision"])
            self.assertEqual(initial_market["revision"] + 1, selected["revision"])
            self.assertEqual(selected, self.store.load_market_profile_settings())
            self.assertEqual(selected, self.store.save_market_profile_settings(
                market_value, expected_revision=selected["revision"]))
            with self.assertRaisesRegex(ValueError, "MARKET_PROFILE_SETTINGS_REVISION_CONFLICT"):
                self.store.save_market_profile_settings(market_value,
                                                        expected_revision=initial_market["revision"])
            with self.assertRaisesRegex(ValueError, "MARKET_PROFILE_REQUIRED"):
                self.store.save_account_settings({
                    "scope": real_scope, "active_profile_id": None,
                    "monitor_enabled": False, "mock_order_enabled": False,
                }, expected_revision=1)
            self.assertEqual(settings["real"], self.store.load_account_settings(real_scope))

            calls = [call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
                     if call["writer_kind"] in
                     {"account_settings_save", "market_profile_settings_save"}]
            self.assertEqual(8, len(calls))
            self.assertEqual(8, len({call["call_id"] for call in calls}))
            self.assertEqual((5, 3),
                             (sum(call["commits"] for call in calls),
                              sum(call["rollbacks"] for call in calls)))
            self.assertEqual({"account_settings_save": (5, 3, 2),
                              "market_profile_settings_save": (3, 2, 1)}, {
                kind: (len(group), sum(call["commits"] for call in group),
                       sum(call["rollbacks"] for call in group))
                for kind in ("account_settings_save", "market_profile_settings_save")
                for group in [[call for call in calls if call["writer_kind"] == kind]]
            })
            self.assertTrue(all(call["writer_family"] == "account.settings"
                                and call["backend_pid"] is not None
                                and call["rows_attempted"] == 1 for call in calls))
            reader_calls = [call for call in summarize_db_calls(
                started + 1, time.time() + 1, mode="raw",
            )["calls"] if call["writer_family"] == "read.account"]
            self.assertEqual({"account_settings": 3, "market_profile_settings": 1}, {
                kind: sum(call["writer_kind"] == kind for call in reader_calls)
                for kind in {call["writer_kind"] for call in reader_calls}
            })
            self.assertTrue(all(call["access_mode"] == "read"
                                and call["transactions"] == 1
                                and call["commits"] == 1
                                and call["backend_pid"] is not None for call in reader_calls))
        finally:
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("credential-activation",))
                cursor.execute("DELETE FROM central_documents "
                               "WHERE collection='server_market_profile_settings' "
                               "AND owner='global' AND document_key='settings'")
                if original_market_row is not None:
                    updated_at, document = original_market_row
                    cursor.execute("INSERT INTO central_documents "
                                   "(collection,owner,document_key,updated_at,document_json) "
                                   "VALUES(%s,%s,%s,%s,%s)",
                                   ("server_market_profile_settings", "global", "settings", updated_at,
                                    json.dumps(document) if isinstance(document, dict) else document))
            self.assertEqual(initial_market, self.store.load_market_profile_settings())

    def test_execution_ledger_and_lease_keep_native_boundaries_and_metrics(self) -> None:
        marker = uuid.uuid4().hex
        account_ref, run_id = f"diag-{marker}", f"run-{marker}"
        owner_key, owner_token = f"mock:{account_ref}", f"{run_id}:{marker}"
        intent_id = f"intent-{marker}"
        self.execution_intent_ids.append(intent_id)
        self.execution_account_refs.append(account_ref)
        self.execution_owner_keys.append(owner_key)
        now = datetime.now(timezone.utc)
        observed_at = now.isoformat()
        expires_at = (now + timedelta(minutes=5)).isoformat()
        ownership = {"owner_key": owner_key, "owner_token": owner_token, "run_id": run_id}
        intent = {
            "intent_id": intent_id, "run_id": run_id, "decision_id": marker,
            "environment": "mock", "account_ref": account_ref, "symbol": "005930",
            "venue": "KRX", "side": "BUY", "quantity": 1, "order_type": "LIMIT",
            "limit_price": 1, "created_at": observed_at, "expires_at": expires_at,
            "policy_version": "diagnostic-v1", "state": "QUEUED",
            "broker_order_id": "", "filled_quantity": 0, "fill_ids": [],
            "last_broker_as_of": None, "updated_at": observed_at,
        }
        accepted = {**intent, "state": "ACCEPTED", "broker_order_id": marker}
        event = {
            "event_id": f"event-{marker}", "intent_id": intent_id,
            "event_type": "ORDER_ACCEPTED", "state": "ACCEPTED",
            "occurred_at": observed_at, "received_at": observed_at,
            "broker_order_id": marker, "broker_execution_id": "", "quantity": 0,
            "price": 0, "reason": "", "broker_as_of": None,
        }
        snapshot = {
            "snapshot_id": f"account-{marker}", "environment": "mock",
            "account_ref": account_ref, "as_of": observed_at,
            "received_at": observed_at, "available_cash_won": 1,
            "reserved_open_buy_won": 0, "positions": {},
        }
        started = time.time() - 1
        self.assertTrue(self.store.acquire_execution_runtime(owner_key, owner_token, observed_at, expires_at))
        self.assertFalse(self.store.acquire_execution_runtime(
            owner_key, f"{run_id}:peer", observed_at, expires_at,
        ))
        self.assertTrue(self.store.create_execution_intent(intent, ownership=ownership))
        self.assertFalse(self.store.create_execution_intent(intent, ownership=ownership))
        self.assertTrue(self.store.append_execution_event(accepted, event, ownership=ownership))
        self.assertFalse(self.store.append_execution_event(accepted, event, ownership=ownership))
        self.assertEqual(accepted, self.store.load_execution_intent(intent_id))
        self.assertEqual([accepted], self.store.load_active_execution_intents(
            "mock", account_ref, run_id,
        ))
        self.assertEqual(accepted, self.store.find_execution_intent_by_broker_order_id(
            "mock", account_ref, run_id, marker,
        ))
        self.assertIsNone(self.store.find_execution_intent_by_broker_order_id(
            "mock", f"other-{account_ref}", run_id, marker,
        ))
        self.assertEqual([event], self.store.load_execution_events(intent_id))
        account_events = self.store.load_account_execution_events("mock", account_ref, 0, 10)
        self.assertEqual([(accepted, event)], [
            (row["intent"], row["event"]) for row in account_events
        ])
        self.assertEqual([], self.store.load_account_execution_events(
            "mock", account_ref, account_events[0]["accepted_sequence"], 10,
        ))

        failed_event = {**event, "event_id": f"failed-{marker}"}
        with self.assertRaises(KeyError):
            self.store.append_execution_event(
                {**accepted, "intent_id": f"missing-{marker}"}, failed_event,
            )
        self.assertEqual([event], self.store.load_execution_events(intent_id))
        self.assertEqual(accepted, self.store.load_execution_intent(intent_id))
        self.assertTrue(self.store.save_execution_account_snapshot(snapshot, ownership=ownership))
        self.assertFalse(self.store.save_execution_account_snapshot(snapshot, ownership=ownership))
        self.assertFalse(self.store.release_execution_runtime(owner_key, f"{run_id}:peer"))
        self.assertTrue(self.store.release_execution_runtime(owner_key, owner_token))
        with self.assertRaisesRegex(RuntimeError, "EXECUTION_OWNERSHIP_LOST"):
            self.store.save_execution_account_snapshot(
                {**snapshot, "snapshot_id": f"late-{marker}"}, ownership=ownership,
            )

        kinds = {
            "execution_runtime_acquire", "execution_intent", "execution_event",
            "execution_account_snapshot", "execution_runtime_release",
        }
        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] in kinds
        ]
        self.assertEqual(12, len(calls))
        self.assertEqual(12, len({call["call_id"] for call in calls}))
        self.assertTrue(all(call["backend_pid"] is not None for call in calls))
        self.assertEqual(2, sum(call["rollbacks"] for call in calls))
        self.assertEqual(10, sum(call["commits"] for call in calls))
        self.assertEqual({"execution_event", "execution_account_snapshot"}, {
            call["writer_kind"] for call in calls if call["rollbacks"] == 1
        })
        reader_calls = [call for call in summarize_db_calls(
            started + 1, time.time() + 1, mode="raw",
        )["calls"] if call["writer_family"] == "read.execution"]
        expected_reads = {
            "intent_by_id": 2, "active_intents": 1,
            "intent_by_broker_order": 2, "intent_events": 2, "account_events": 2,
        }
        self.assertEqual(expected_reads, {
            kind: sum(call["writer_kind"] == kind for call in reader_calls)
            for kind in {call["writer_kind"] for call in reader_calls}
        })
        self.assertEqual(len(reader_calls), len({call["call_id"] for call in reader_calls}))
        self.assertTrue(all(call["access_mode"] == "read"
                            and call["transactions"] == 1
                            and call["commits"] == 1
                            and call["sql_calls"] == 1
                            and call["backend_pid"] is not None for call in reader_calls))

    def test_execution_ownership_control_fences_rollback_and_keep_peer(self) -> None:
        marker = uuid.uuid4().hex
        account_ref, run_id = f"diag-{marker}", f"run-{marker}"
        peer_account = f"peer-{marker}"
        owner_key, owner_token = f"mock:{account_ref}", f"{run_id}:{marker}"
        self.execution_owner_keys.append(owner_key)
        self.execution_account_refs.extend((account_ref, peer_account))
        ids = [f"{name}-{marker}" for name in ("owned", "peer", "blocked", "resumed")]
        self.execution_intent_ids.extend(ids)
        self.document_keys.append(("execution_mock_automation_control", account_ref, account_ref))
        now = datetime.now(timezone.utc)
        observed_at = now.isoformat()
        expires_at = (now + timedelta(minutes=5)).isoformat()
        intent = {
            "intent_id": ids[0], "run_id": run_id, "environment": "mock",
            "account_ref": account_ref, "state": "QUEUED", "broker_order_id": "",
            "last_broker_as_of": None, "created_at": observed_at, "updated_at": observed_at,
        }
        peer_intent = {**intent, "intent_id": ids[1], "account_ref": peer_account}
        peer = PostgresQueryStore(self.store._database_url)
        self.assertTrue(peer.create_execution_intent(peer_intent))
        self.assertTrue(self.store.acquire_execution_runtime(owner_key, owner_token, observed_at, expires_at))
        control = {
            "account_ref": account_ref, "control_revision": 1, "desired_state": "RUNNING",
            "changed_at": observed_at, "active_spec_id": marker, "execution_run_id": run_id,
        }
        self.assertTrue(self.store.save_mock_automation_control(control, expected_revision=0))
        ownership = {
            "owner_key": owner_key, "owner_token": owner_token, "run_id": run_id,
            "control_revision": 1, "active_spec_id": marker,
        }
        self.assertTrue(self.store.create_execution_intent(intent, ownership=ownership))
        stopped = {**control, "control_revision": 2, "desired_state": "STOPPED"}
        self.assertTrue(self.store.save_mock_automation_control(stopped, expected_revision=1))
        started = time.time()
        blocked = {**intent, "intent_id": ids[2]}
        with self.assertRaisesRegex(RuntimeError, "MOCK_AUTOMATION_CONTROL_CHANGED"):
            self.store.create_execution_intent(blocked, ownership=ownership)
        event = {
            "event_id": f"blocked-event-{marker}", "intent_id": ids[0], "state": "ACCEPTED",
            "occurred_at": observed_at, "received_at": observed_at,
        }
        with self.assertRaisesRegex(RuntimeError, "MOCK_AUTOMATION_CONTROL_CHANGED"):
            self.store.append_execution_event({**intent, "state": "ACCEPTED"}, event, ownership=ownership)
        snapshot_id = f"blocked-snapshot-{marker}"
        snapshot = {
            "snapshot_id": snapshot_id, "environment": "mock", "account_ref": account_ref,
            "as_of": observed_at, "received_at": observed_at,
        }
        with self.assertRaisesRegex(RuntimeError, "MOCK_AUTOMATION_CONTROL_CHANGED"):
            self.store.save_execution_account_snapshot(snapshot, ownership=ownership)
        resumed = {**control, "control_revision": 3}
        self.assertTrue(self.store.save_mock_automation_control(resumed, expected_revision=2))
        with self.assertRaisesRegex(RuntimeError, "MOCK_AUTOMATION_CONTROL_CHANGED"):
            self.store.create_execution_intent(blocked, ownership=ownership)
        current_ownership = {**ownership, "control_revision": 3}
        current_intent = {**intent, "intent_id": ids[3]}
        self.assertTrue(self.store.create_execution_intent(current_intent, ownership=current_ownership))
        self.assertFalse(self.store.release_execution_runtime(owner_key, f"{run_id}:wrong"))
        self.assertTrue(self.store.release_execution_runtime(owner_key, owner_token))
        self.assertTrue(peer.acquire_execution_runtime(owner_key, f"{run_id}:new-owner", observed_at, expires_at))
        with self.assertRaisesRegex(RuntimeError, "EXECUTION_OWNERSHIP_LOST"):
            self.store.create_execution_intent(blocked, ownership=current_ownership)
        self.assertIsNone(self.store.load_execution_intent(ids[2]))
        self.assertEqual([], self.store.load_execution_events(ids[0]))
        self.assertEqual(intent, self.store.load_execution_intent(ids[0]))
        self.assertEqual(current_intent, self.store.load_execution_intent(ids[3]))
        self.assertEqual(peer_intent, peer.load_execution_intent(ids[1]))
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM central_execution_account_snapshots WHERE snapshot_id=%s", (snapshot_id,))
            self.assertEqual(0, cursor.fetchone()[0])
        calls = summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
        failed = [call for call in calls if call["writer_kind"] in {
            "execution_intent", "execution_event", "execution_account_snapshot",
        } and call["outcome"] == "rolled_back"]
        self.assertEqual(5, len(failed))
        self.assertEqual(5, len({call["call_id"] for call in failed}))
        self.assertTrue(all(call["commits"] == 0 and call["rollbacks"] == 1
                            and call["backend_pid"] is not None for call in failed))

    def test_query_market_bars_keep_native_history_and_correlate_both_metrics(self) -> None:
        code = f"DIAG{uuid.uuid4().hex[:16]}"
        self.query_bar_codes.append(code)
        day = "2099-01-09"
        minute = {
            "trading_date": day, "minute": "10:00", "code": code, "market": "KRX",
            "open": 100, "high": 110, "low": 90, "close": 105,
            "volume": 10, "trade_value_million_won": 1, "updated_at": 1_790_000_000.0,
        }
        daily = {key: value for key, value in minute.items() if key != "minute"}
        minute_observation = minute_bar_observation(
            minute, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE,
            source="kiwoom-ka10080;trade_value=ohlcv_estimate",
            value_kind=DataValueKind.ESTIMATED,
        )
        daily_observation = daily_bar_observation(
            daily, completeness=DataCompleteness.COMPLETE,
        )
        minute_pair = (bar_observation_key(minute_observation), minute_observation)
        daily_pair = (bar_observation_key(daily_observation), daily_observation)

        started = time.time() - 1
        self.store.replace_minute_bars([minute], observations=[minute_pair])
        self.store.replace_daily_bars([daily], observations=[daily_pair])
        self.store.replace_minute_bars([minute], observations=[minute_pair])
        self.store.replace_daily_bars([daily], observations=[daily_pair])

        def save_revision(close: int) -> None:
            revised = {**minute, "close": close}
            observation = minute_bar_observation(
                revised, origin=ObservationOrigin.QUERY,
                completeness=DataCompleteness.COMPLETE,
                source="kiwoom-ka10080;trade_value=ohlcv_estimate",
                value_kind=DataValueKind.ESTIMATED,
            )
            self.store.replace_minute_bars(
                [revised], observations=[(bar_observation_key(observation), observation)],
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(save_revision, close) for close in (106, 107)]
            for future in futures:
                future.result(timeout=30)

        invalid = {**minute, "minute": "10:01"}
        invalid_observation = minute_bar_observation(
            invalid, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE,
            source="kiwoom-ka10080;trade_value=ohlcv_estimate",
            value_kind=DataValueKind.ESTIMATED,
        )
        with self.assertRaisesRegex(ValueError, "observation_key"):
            self.store.replace_minute_bars(
                [invalid], observations=[(" ", invalid_observation)],
            )

        [saved_minute] = self.store.load_minute_bars(code, day, "KRX")
        self.assertIn(saved_minute["close"], (106, 107))
        self.assertEqual((minute["volume"], minute["updated_at"]), (
            saved_minute["volume"], saved_minute["updated_at"],
        ))
        self.assertEqual([daily], [
            {key: row[key] for key in daily}
            for row in self.store.load_daily_bars(code, "KRX", 10)
        ])
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT revision_id,revision_of FROM central_observation_revisions "
                "WHERE kind='minute_bar' AND subject=%s ORDER BY accepted_sequence",
                (f"{code}:KRX",),
            )
            revisions = cursor.fetchall()
            self.assertEqual(3, len(revisions))
            self.assertIsNone(revisions[0][1])
            self.assertEqual(revisions[0][0], revisions[1][1])
            self.assertEqual(revisions[1][0], revisions[2][1])
            cursor.execute(
                "SELECT dataset_kind,count(*) FROM central_market_data_observation_meta "
                "WHERE subject=%s GROUP BY dataset_kind", (f"{code}:KRX",),
            )
            self.assertEqual({"daily_bar": 1, "minute_bar": 1}, dict(cursor.fetchall()))

        calls = [call for call in summarize_db_calls(
            started, time.time() + 1, mode="raw",
        )["calls"] if call["writer_family"].startswith("rest.market_bars.")]
        self.assertEqual(7, len(calls))
        self.assertEqual(7, len({call["call_id"] for call in calls}))
        self.assertEqual((6, 1), (
            sum(call["commits"] for call in calls),
            sum(call["rollbacks"] for call in calls),
        ))
        self.assertTrue(all(call["backend_pid"] is not None
                            and call["rows_attempted"] == 1
                            and call["transactions"] == 1 for call in calls))
        failed = next(call for call in calls if call["outcome"] == "rolled_back")
        self.assertEqual(("query_minute", 0, 1), (
            failed["writer_kind"], failed["commits"], failed["rollbacks"],
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        for kind, family in (("minute", "rest.market_bars.minute"),
                             ("daily", "rest.market_bars.daily")):
            committed = [call for call in calls
                         if call["writer_family"] == family and call["outcome"] == "committed"]
            self.assertEqual(4 if kind == "minute" else 2, len(committed))
            ids = {call["call_id"] for call in committed}
            bar_samples = legacy["kinds"][kind]["call_samples"]
            writer_samples = legacy["writer_transactions"][f"query_{kind}"]["call_samples"]
            self.assertEqual(ids, {sample["db_call_id"] for sample in bar_samples})
            self.assertEqual(ids, {sample["db_call_id"] for sample in writer_samples})
            for call in committed:
                sample = next(row for row in bar_samples if row["db_call_id"] == call["call_id"])
                self.assertEqual(call["backend_pid"], sample["commit_diagnostics"]["backend_pid"])
                self.assertLessEqual(abs(call["commit_ms"] - sample["commit_ms"]), 10)

    def test_query_bar_metadata_available_at_changes_only_with_content_or_state(self) -> None:
        code = f"DIAG{uuid.uuid4().hex[:16]}"
        self.query_bar_codes.append(code)
        minute = {
            "trading_date": "2099-01-09", "minute": "10:00", "code": code,
            "market": "KRX", "open": 100, "high": 110, "low": 90, "close": 105,
            "volume": 10, "trade_value_million_won": 1,
            "updated_at": 1_790_000_000.0,
        }
        daily = {key: value for key, value in minute.items() if key != "minute"}

        def save_minute(value, completeness, source):
            observation = minute_bar_observation(
                value, origin=ObservationOrigin.QUERY,
                completeness=completeness, source=source,
                value_kind=DataValueKind.ESTIMATED,
            )
            self.store.replace_minute_bars(
                [value], observations=[(bar_observation_key(observation), observation)],
            )
            return self.store.load_market_data_metadata(
                MarketDatasetKind.MINUTE_BAR, f"{code}:KRX", "2099-01-09T10:00",
            )

        def save_daily(value):
            observation = daily_bar_observation(value, completeness=DataCompleteness.COMPLETE)
            self.store.replace_daily_bars(
                [value], observations=[(bar_observation_key(observation), observation)],
            )
            return self.store.load_market_data_metadata(
                MarketDatasetKind.DAILY_BAR, f"{code}:KRX", "2099-01-09",
            )

        first_minute = save_minute(minute, DataCompleteness.IN_PROGRESS, "source-a")
        first_daily = save_daily(daily)
        replay_minute = {**minute, "updated_at": minute["updated_at"] + 60}
        replay_daily = {**daily, "updated_at": daily["updated_at"] + 60}
        self.assertEqual(first_minute.available_at,
                         save_minute(replay_minute, DataCompleteness.IN_PROGRESS, "source-a").available_at)
        self.assertEqual(first_daily.available_at, save_daily(replay_daily).available_at)
        completed = save_minute(replay_minute, DataCompleteness.COMPLETE, "source-a")
        self.assertEqual(replay_minute["updated_at"], completed.available_at.timestamp())
        sourced = save_minute({**replay_minute, "updated_at": replay_minute["updated_at"] + 60},
                              DataCompleteness.COMPLETE, "source-b")
        self.assertEqual(replay_minute["updated_at"] + 60, sourced.available_at.timestamp())
        corrected = save_daily({**replay_daily, "close": 106})
        self.assertEqual(replay_daily["updated_at"], corrected.available_at.timestamp())

    def test_direct_market_metadata_writer_rolls_back_and_range_reader_keeps_native_context(self) -> None:
        from kiwoom_monitor.central_server.database_observation_writes import (
            _market_metadata_upsert_sql as market_metadata_upsert_sql,
        )
        from kiwoom_monitor.domain.market_data_contract import (
            DataCompleteness, DataUnit, DataValueKind, MarketDataMetadata,
            MarketDataObservation, MarketDatasetKind, ObservationOrigin, TradingVenue,
        )

        code = f"DIAG{uuid.uuid4().hex[:16]}"
        self.query_bar_codes.append(code)
        subject = f"{code}:KRX"
        effective_at = datetime(2099, 1, 9, 10, 0, tzinfo=KST)
        available_at = effective_at + timedelta(seconds=2)
        observation = MarketDataObservation(
            kind=MarketDatasetKind.CANDIDATE_SET,
            subject=subject,
            value={"items": []},
            metadata=MarketDataMetadata(
                effective_at=effective_at,
                available_at=available_at,
                venue=TradingVenue.COMBINED,
                unit=DataUnit.COUNT,
                value_kind=DataValueKind.ACTUAL,
                completeness=DataCompleteness.COMPLETE,
                origin=ObservationOrigin.QUERY,
                source="diagnostic-market-metadata-writer",
            ),
        )
        key = effective_at.isoformat()
        self.store.save_market_data_metadata(key, observation)

        with patch(
            "kiwoom_monitor.central_server.database_market_metadata._market_metadata_upsert_sql",
            side_effect=lambda placeholder, excluded: (
                market_metadata_upsert_sql(placeholder, excluded) + " INVALID"
            ),
        ), self.assertRaises(psycopg.errors.SyntaxError):
            self.store.save_market_data_metadata(key, replace(
                observation,
                metadata=replace(observation.metadata, source="failed-correction"),
            ))

        started = time.time()
        loaded = self.store.load_market_data_metadata(
            MarketDatasetKind.CANDIDATE_SET, subject, key,
        )
        rows = self.store.load_market_data_metadata_range(
            MarketDatasetKind.CANDIDATE_SET, subject,
            effective_at - timedelta(seconds=1), effective_at + timedelta(seconds=1),
        )
        self.assertEqual(observation.metadata, loaded)
        self.assertEqual([(key, observation.metadata)], [
            (row.observation_key, row.metadata) for row in rows
        ])

        calls = summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
        range_reads = [call for call in calls
                       if call["operation"] == "load_market_data_metadata_range"]
        self.assertEqual(1, len(range_reads))
        self.assertEqual("read", range_reads[0]["access_mode"])
        self.assertEqual("committed", range_reads[0]["outcome"])
        self.assertEqual((1, 1, 1), (
            range_reads[0]["transactions"], range_reads[0]["commits"],
            range_reads[0]["sql_calls"],
        ))
        self.assertIsNotNone(range_reads[0]["backend_pid"])

    def test_shadow_evaluation_and_checkpoint_keep_replay_and_independent_commits(self) -> None:
        monitor_id = f"diagnostic-shadow-{uuid.uuid4().hex}"
        self.shadow_monitor_ids.append(monitor_id)
        decision = {
            "decision_id": f"diagnostic-decision-{uuid.uuid4().hex}",
            "decided_at": "2099-01-09T00:00:00+00:00", "final_action": "ENTER",
        }
        candidate = {
            "event_id": f"diagnostic-event-{uuid.uuid4().hex}",
            "available_at": "2099-01-09T00:00:00+00:00", "symbol": "005930",
        }
        expires_at = "2099-01-09T00:01:00+00:00"
        started = time.time() - 1

        self.store.save_shadow_monitor_state(monitor_id, {"cursor": 10})
        self.store.save_shadow_evaluation(monitor_id, decision, candidate, expires_at)
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT accepted_sequence FROM central_shadow_candidate_events WHERE event_id=%s",
                           (candidate["event_id"],))
            event_sequence = int(cursor.fetchone()[0])
        first = self.store.load_shadow_candidates(event_sequence - 1)
        first_event = first["events"][0]
        self.assertEqual(candidate["symbol"], first_event["symbol"])
        self.assertEqual({"cursor": 10}, self.store.load_shadow_monitor_state(monitor_id))

        # A committed event survives a subsequent checkpoint failure and can be replayed.
        with self.assertRaises(TypeError):
            self.store.save_shadow_monitor_state(monitor_id, {"cursor": 11, "invalid": object()})
        self.assertEqual({"cursor": 10}, self.store.load_shadow_monitor_state(monitor_id))
        self.store.save_shadow_evaluation(monitor_id, decision, candidate, expires_at)
        replay = self.store.load_shadow_candidates(first_event["accepted_sequence"] - 1)
        self.assertEqual(first_event, next(
            row for row in replay["events"] if row["event_id"] == candidate["event_id"]
        ))
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.store.save_shadow_evaluation(
                monitor_id, {**decision, "final_action": "EXIT"}, None,
            )
        self.store.save_shadow_monitor_state(monitor_id, {"cursor": 11})
        self.assertEqual({"cursor": 11}, self.store.load_shadow_monitor_state(monitor_id))

        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM central_shadow_decisions WHERE monitor_id=%s", (monitor_id,))
            self.assertEqual(1, cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM central_shadow_candidate_events WHERE monitor_id=%s", (monitor_id,))
            self.assertEqual(1, cursor.fetchone()[0])

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] in {"candidate.shadow_evaluation", "candidate.shadow_checkpoint"}
        ]
        self.assertEqual(6, len(calls))
        self.assertEqual(6, len({call["call_id"] for call in calls}))
        self.assertEqual(["shadow_monitor_state", "shadow_evaluation", "shadow_monitor_state",
                          "shadow_evaluation", "shadow_evaluation", "shadow_monitor_state"],
                         [call["writer_kind"] for call in calls])
        self.assertEqual(["committed", "committed", "rolled_back", "committed",
                          "rolled_back", "committed"], [call["outcome"] for call in calls])
        self.assertEqual([1, 2, 1, 2, 1, 1], [call["rows_attempted"] for call in calls])
        self.assertEqual(4, sum(call["commits"] for call in calls))
        self.assertEqual(2, sum(call["rollbacks"] for call in calls))
        self.assertTrue(all(call["backend_pid"] is not None for call in calls))

    def test_news_job_finish_updates_only_its_job_and_correlates_metrics(self) -> None:
        key = f"diagnostic-common-access-news-{uuid.uuid4().hex}"
        self.news_keys.append(key)
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_news_jobs"
                "(job_key,article_revision_id,stock_code,target_id,stage,input_hash,"
                "processing_version,attempts,next_retry_at,state,output_ref,error,payload_json,updated_at) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,1,0,'RUNNING','','','{}'::jsonb,%s)",
                (key, "diagnostic-article", "000000", "diagnostic-target", "BODY",
                 "diagnostic-hash", "diagnostic-version", time.time()),
            )
        started = time.time() - 1
        self.store.finish_news_job(key, "diagnostic-output")
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT state,output_ref,error FROM central_news_jobs WHERE job_key=%s",
                           (key,))
            self.assertEqual(("COMPLETED", "diagnostic-output", ""), cursor.fetchone())
        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "news.job_finish"
            and call["operation"] == "finish_news_job"
        ]
        self.assertEqual(1, len(calls))
        self.assertEqual(("news.job_finish", "news_job_finish", "committed", 1, 1),
                         (calls[0]["writer_family"], calls[0]["writer_kind"],
                          calls[0]["outcome"], calls[0]["commits"], calls[0]["sql_calls"]))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["news_job_finish"]["call_samples"]
        self.assertTrue(any(sample["db_call_id"] == calls[0]["call_id"] for sample in samples))

    def test_news_job_retry_preserves_attempt_threshold_and_correlates_metrics(self) -> None:
        token = uuid.uuid4().hex
        keys = [f"diagnostic-retry-{token}-{attempts}" for attempts in (2, 3)]
        self.news_keys.extend(keys)
        with self.store._connect() as connection, connection.cursor() as cursor:
            for key, attempts in zip(keys, (2, 3)):
                cursor.execute(
                    "INSERT INTO central_news_jobs"
                    "(job_key,article_revision_id,stock_code,target_id,stage,input_hash,"
                    "processing_version,attempts,next_retry_at,state,output_ref,error,payload_json,updated_at) "
                    "VALUES(%s,%s,%s,%s,'BODY',%s,%s,%s,0,'RUNNING','','','{}'::jsonb,%s)",
                    (key, "diagnostic-article", "000000", "diagnostic-target",
                     "diagnostic-hash", "diagnostic-version", attempts, time.time()),
                )

        started = time.time() - 1
        for key in keys:
            self.store.retry_news_job(key, "diagnostic retry", 123.0, "diagnostic-output")

        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT job_key,state,attempts,next_retry_at,output_ref,error "
                "FROM central_news_jobs WHERE job_key=ANY(%s)", (keys,),
            )
            rows = {row[0]: row[1:] for row in cursor.fetchall()}
        self.assertEqual({
            keys[0]: ("PENDING", 2, 123.0, "diagnostic-output", "diagnostic retry"),
            keys[1]: ("FAILED", 3, 123.0, "diagnostic-output", "diagnostic retry"),
        }, rows)
        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "news.job_retry"
            and call["operation"] == "retry_news_job"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_kind"] == "news_job_retry"
            and call["outcome"] == "committed"
            and call["sql_calls"] == 2
            and call["commits"] == 1
            and call["rows_attempted"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))

    def test_news_job_claim_recovers_stale_and_prevents_parallel_duplicate_claim(self) -> None:
        claimed_at = 1.0
        stale_cutoff = claimed_at - 120.0
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM central_news_jobs "
                "WHERE state='RUNNING' AND updated_at<%s", (stale_cutoff,),
            )
            self.assertEqual(0, cursor.fetchone()[0],
                             "dedicated test DB contains an unrelated expired lease")

        token = uuid.uuid4().hex
        stale_key, first_key, second_key = (f"diagnostic-claim-{token}-{index}"
                                            for index in range(3))
        self.news_keys.extend((stale_key, first_key, second_key))
        stale_code = f"STALE{token[:16]}"
        ready_code = f"READY{token[:16]}"
        with self.store._connect() as connection, connection.cursor() as cursor:
            for key, code, state, updated_at in (
                (stale_key, stale_code, "RUNNING", -200.0),
                (first_key, ready_code, "PENDING", 0.0),
                (second_key, ready_code, "PENDING", 0.0),
            ):
                cursor.execute(
                    "INSERT INTO central_news_jobs"
                    "(job_key,article_revision_id,stock_code,target_id,stage,input_hash,"
                    "processing_version,attempts,next_retry_at,state,output_ref,error,"
                    "payload_json,updated_at) "
                    "VALUES(%s,%s,%s,%s,'BODY',%s,%s,0,0,%s,'','','{}'::jsonb,%s)",
                    (key, f"diagnostic-revision-{token}", code, code,
                     "diagnostic-hash", "diagnostic-version", state, updated_at),
                )

        started = time.time() - 1
        recovered = self.store.claim_news_jobs(now=claimed_at,
                                               priority_stock_code=stale_code)
        self.assertEqual([stale_key], [job["job_key"] for job in recovered])
        self.assertEqual(1, recovered[0]["attempts"])

        barrier = Barrier(2)

        def claim_ready() -> list[dict[str, object]]:
            barrier.wait(timeout=10)
            return self.store.claim_news_jobs(now=claimed_at,
                                              priority_stock_code=ready_code)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: claim_ready(), range(2)))
        self.assertEqual({first_key, second_key},
                         {job["job_key"] for batch in results for job in batch})
        self.assertTrue(all(len(batch) == 1 for batch in results))

        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT job_key,state,attempts FROM central_news_jobs "
                "WHERE job_key=ANY(%s)", (self.news_keys,),
            )
            rows = {str(key): (state, attempts)
                    for key, state, attempts in cursor.fetchall()}
        self.assertEqual({key: ("RUNNING", 1) for key in self.news_keys}, rows)

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "news.job_claim"
            and call["operation"] == "claim_news_jobs"
        ]
        self.assertEqual(3, len(calls))
        self.assertTrue(all(
            call["writer_kind"] == "news_job_claim"
            and call["outcome"] == "committed"
            and call["sql_calls"] == 3
            and call["commits"] == 1
            and call["backend_pid"] is not None
            and call["rows_attempted"] is None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["news_job_claim"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

        for call in calls:
            phases = call["phase_diagnostics"]
            self.assertEqual(["recover_stale", "select_candidates", "mark_running"],
                             [phase["phase"] for phase in phases])
            self.assertTrue(all(phase["backend_pid"] == call["backend_pid"]
                                and not phase["exception_type"] for phase in phases))
            self.assertEqual([1, 1], [phase["rowcount"] for phase in phases[1:]])
            sample = next(sample for sample in samples if sample["db_call_id"] == call["call_id"])
            self.assertEqual({phase["phase"]: phase["duration_ms"] for phase in phases},
                             sample["domain_phase_ms"])

    def test_news_job_claim_candidate_preserves_postgres_selection_and_skip_locked(self) -> None:
        token = uuid.uuid4().hex
        code = f"DIAG{token[:16]}"
        jobs = []
        for index, (stage, scope) in enumerate((
            ("BODY", "news_article"), ("RULE", "news_article"),
            ("BODY", "historical_news_pc_backfill"),
            ("RULE", "historical_market_pc_backfill"),
            ("AI", "historical_news_pc_backfill"),
        )):
            article_id = f"diagnostic-claim-article-{token}-{index}"
            job_key = f"diagnostic-claim-candidate-{token}-{index}"
            self.article_revision_ids.append(article_id)
            self.news_keys.append(job_key)
            jobs.append((job_key, article_id, stage, scope))
        with self.store._connect() as connection, connection.cursor() as cursor:
            for job_key, article_id, stage, scope in jobs:
                cursor.execute(
                    "INSERT INTO central_news_article_revisions("
                    "article_revision_id,stock_code,identity,content_hash,collector_id,"
                    "published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                    "VALUES(%s,%s,%s,%s,'diagnostic',NULL,0,0,%s,NULL,'{}'::jsonb)",
                    (article_id, code, article_id, article_id, scope),
                )
                cursor.execute(
                    "INSERT INTO central_news_jobs("
                    "job_key,article_revision_id,stock_code,target_id,stage,input_hash,"
                    "processing_version,attempts,next_retry_at,state,output_ref,error,"
                    "payload_json,updated_at) "
                    "VALUES(%s,%s,%s,%s,%s,%s,'diagnostic',0,0,'PENDING','','',"
                    "'{}'::jsonb,0)",
                    (job_key, article_id, code, code, stage, article_id),
                )

        parameters = (1.0, "DIAG", "DIAG", code, code, code, code, code, code, 3)
        expected = {jobs[index][0] for index in (0, 1, 4)}
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(_NEWS_JOB_CLAIM_SELECT_SQL, parameters)
            original = [row[0] for row in cursor.fetchall()]
            cursor.execute(_NEWS_JOB_CLAIM_DIAGNOSTIC_CANDIDATE_SQL, parameters)
            candidate = [row[0] for row in cursor.fetchall()]
            self.assertEqual(original, candidate)
            self.assertEqual(expected, set(candidate))

        first = self.store._connect()
        second = self.store._connect()
        try:
            with first.cursor() as first_cursor, second.cursor() as second_cursor:
                one_row = (*parameters[:-1], 1)
                first_cursor.execute(_NEWS_JOB_CLAIM_DIAGNOSTIC_CANDIDATE_SQL, one_row)
                first_key = first_cursor.fetchone()[0]
                second_cursor.execute(_NEWS_JOB_CLAIM_DIAGNOSTIC_CANDIDATE_SQL, one_row)
                second_key = second_cursor.fetchone()[0]
                self.assertNotEqual(first_key, second_key)
                self.assertIn(first_key, expected)
                self.assertIn(second_key, expected)
        finally:
            first.rollback()
            second.rollback()
            first.close()
            second.close()

    def test_news_request_budget_serializes_parallel_claims_and_observes_limits(self) -> None:
        budget_date = "2099-12-31"
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(SUM(request_count),0) FROM central_news_request_budget "
                "WHERE budget_date=%s", (budget_date,),
            )
            baseline = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM central_news_request_budget "
                "WHERE budget_date=%s AND scope=ANY(%s)",
                (budget_date, ["query_set", "watchlist"]),
            )
            self.assertEqual(0, cursor.fetchone()[0], "dedicated test budget scopes are occupied")
        self.news_budget_keys.extend(((budget_date, "query_set"), (budget_date, "watchlist")))

        started = time.time() - 1
        barrier = Barrier(2)

        def claim() -> bool:
            barrier.wait(timeout=10)
            return self.store.claim_news_request(
                "query_set", scope_limit=1, hard_limit=baseline + 10,
                budget_date=budget_date,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            accepted = list(executor.map(lambda _: claim(), range(2)))
        self.assertEqual([False, True], sorted(accepted))
        self.assertFalse(self.store.claim_news_request(
            "watchlist", scope_limit=1, hard_limit=baseline + 1,
            budget_date=budget_date,
        ))
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT request_count FROM central_news_request_budget "
                "WHERE budget_date=%s AND scope='query_set'", (budget_date,),
            )
            self.assertEqual((1,), cursor.fetchone())
            cursor.execute(
                "SELECT COUNT(*) FROM central_news_request_budget "
                "WHERE budget_date=%s AND scope='watchlist'", (budget_date,),
            )
            self.assertEqual(0, cursor.fetchone()[0])
        self.assertEqual(baseline + 1, self.store.news_request_count(budget_date))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "news.request_budget"
            and call["operation"] == "claim_news_request"
        ]
        self.assertEqual(3, len(calls))
        self.assertEqual(2, sum(call["writer_kind"] == "news_request:query_set" for call in calls))
        self.assertEqual(1, sum(call["writer_kind"] == "news_request:watchlist" for call in calls))
        self.assertEqual([3, 3, 4], sorted(call["sql_calls"] for call in calls))
        self.assertTrue(all(call["outcome"] == "committed" and call["commits"] == 1
                            and call["rows_attempted"] is None
                            and call["backend_pid"] is not None for call in calls))
        reader_calls = [call for call in summarize_db_calls(
            started, time.time() + 1, mode="raw",
        )["calls"] if call["writer_family"] == "read.news_request_budget"]
        self.assertEqual(1, len(reader_calls))
        self.assertEqual(("request_count", "news_request_count", "read", 1, 1), (
            reader_calls[0]["writer_kind"], reader_calls[0]["operation"],
            reader_calls[0]["access_mode"], reader_calls[0]["transactions"],
            reader_calls[0]["commits"],
        ))
        self.assertIsNotNone(reader_calls[0]["backend_pid"])

    def test_historical_market_batch_replay_and_failure_preserve_history_and_progress(self) -> None:
        token = uuid.uuid4().hex
        target_date = "2099-12-31"
        source_id = f"naver-stock:flash:historical:{target_date}"
        batch_id = f"diagnostic-historical-market-{token}"
        identity = f"https://diagnostic.example/historical-market/{token}"
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT (SELECT COUNT(*) FROM central_news_source_runs WHERE source_id=%s) + "
                "(SELECT COUNT(*) FROM central_news_source_cursors WHERE source_id=%s) + "
                "(SELECT COUNT(*) FROM central_news_source_observations WHERE source_id=%s)",
                (source_id, source_id, source_id),
            )
            self.assertEqual(0, cursor.fetchone()[0], "dedicated historical source key is occupied")
        self.source_ids.append(source_id)
        item = {
            "identity": identity,
            "document": {
                "title": f"diagnostic historical market {token}",
                "description": "prepared market page replay probe",
                "link": identity, "original_link": identity,
                "published_at": "2099-12-31T10:00:00+09:00",
            },
            "targets": [], "processing_excluded": True,
        }

        started = time.time() - 1
        barrier = Barrier(2)

        def save_same_batch() -> dict[str, object]:
            barrier.wait(timeout=10)
            return self.store.save_historical_market_news_batch(
                "flash", target_date, batch_id, [item], processing_owner="pc",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            replay_results = list(executor.map(lambda _: save_same_batch(), range(2)))
        self.assertCountEqual(["imported", "already_imported"],
                              [result["state"] for result in replay_results])
        self.assertTrue(all(result["raw_count"] == 1 for result in replay_results))

        article_history = self.store.load_news_history("article", target="GLOBAL", identity=identity)
        self.assertEqual(1, len(article_history))
        article_id = str(article_history[0]["article_revision_id"])
        self.article_revision_ids.append(article_id)
        self.assertEqual("historical_market_pc_backfill", article_history[0]["collection_scope"])
        diagnostics = self.store.load_news_source_diagnostics(source_id=source_id)
        self.assertEqual((1, 1), (len(diagnostics["runs"]), len(diagnostics["observations"])))
        self.assertEqual((1, 0), (diagnostics["runs"][0]["unique_count"],
                                 diagnostics["runs"][0]["duplicate_count"]))

        failed_batch = f"diagnostic-historical-market-failure-{token}"
        revised = {**item, "document": {**item["document"], "description": "must roll back"}}
        with self.assertRaisesRegex(ValueError, "소스 관측에는 identity와 기사 문서가 필요합니다"):
            self.store.save_historical_market_news_batch(
                "flash", target_date, failed_batch, [revised, {"identity": "missing-document"}],
                processing_owner="pc",
            )
        article_history_after_failure = self.store.load_news_history(
            "article", target="GLOBAL", identity=identity,
        )
        self.assertEqual([article_id], [row["article_revision_id"] for row in article_history_after_failure])
        diagnostics_after_failure = self.store.load_news_source_diagnostics(source_id=source_id)
        self.assertEqual((1, 1), (len(diagnostics_after_failure["runs"]),
                                 len(diagnostics_after_failure["observations"])))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "news.historical_market_batch"
            and call["operation"] == "save_historical_market_news_batch"
        ]
        self.assertEqual(3, len(calls))
        self.assertEqual({"committed": 2, "rolled_back": 1}, {
            outcome: sum(call["outcome"] == outcome for call in calls)
            for outcome in ("committed", "rolled_back")
        })
        self.assertEqual({"news_historical_market:flash"}, {call["writer_kind"] for call in calls})
        self.assertEqual([1, 1, 2], sorted(call["rows_attempted"] for call in calls))
        self.assertEqual(3, len({call["call_id"] for call in calls}))
        self.assertTrue(all(call["backend_pid"] is not None for call in calls))

    def test_news_ai_job_enqueue_replay_and_batch_failure_preserve_queue(self) -> None:
        token = uuid.uuid4().hex
        article_id = f"diagnostic-ai-queue-article-{token}"
        body_id = f"diagnostic-ai-queue-body-{token}"
        identity = f"diagnostic-ai-queue-identity-{token}"
        stock_code = f"AIQ{token[:16]}"
        body_hash = f"diagnostic-ai-queue-body-hash-{token}"
        self.article_revision_ids.append(article_id)
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_news_article_revisions("
                "article_revision_id,stock_code,identity,content_hash,collector_id,"
                "published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                "VALUES(%s,%s,%s,%s,'diagnostic',NULL,%s,%s,'news_article',NULL,'{}'::jsonb)",
                (article_id, stock_code, identity, f"diagnostic-article-hash-{token}",
                 time.time(), time.time()),
            )
            cursor.execute(
                "INSERT INTO central_news_body_revisions("
                "body_revision_id,article_revision_id,content_hash,extractor_version,"
                "fetched_at,available_at,status,body_text,error) "
                "VALUES(%s,%s,%s,'diagnostic',%s,%s,'fulltext','diagnostic body','')",
                (body_id, article_id, body_hash, time.time(), time.time()),
            )

        value = {
            "stock_code": stock_code, "identity": identity, "target_id": stock_code,
            "processing_version": f"diagnostic-ai-v1-{token}",
            "payload": {"event": {"identity": identity}, "article_count": 1},
        }
        started = time.time() - 1
        barrier = Barrier(2)

        def enqueue() -> int:
            barrier.wait(timeout=10)
            return self.store.enqueue_news_ai_jobs([value])

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: enqueue(), range(2)))
        self.assertEqual([0, 1], sorted(results))

        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT job_key,article_revision_id,input_hash,processing_version,state,payload_json "
                "FROM central_news_jobs WHERE article_revision_id=%s AND stage='AI'",
                (article_id,),
            )
            rows = cursor.fetchall()
        self.assertEqual(1, len(rows))
        job_key, stored_article, input_hash, version, state, payload = rows[0]
        self.news_keys.append(str(job_key))
        self.assertEqual((article_id, body_hash, value["processing_version"], "PENDING"),
                         (stored_article, input_hash, version, state))
        self.assertEqual(body_id, payload["body_revision_id"])

        revised = {**value, "processing_version": f"diagnostic-ai-v2-{token}"}
        with self.assertRaises(KeyError):
            self.store.enqueue_news_ai_jobs([revised, {"stock_code": stock_code}])
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT job_key,processing_version FROM central_news_jobs "
                "WHERE article_revision_id=%s AND stage='AI'", (article_id,),
            )
            self.assertEqual([(job_key, value["processing_version"])], cursor.fetchall())

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "news.job_enqueue"
            and call["operation"] == "enqueue_news_ai_jobs"
        ]
        self.assertEqual(3, len(calls))
        self.assertEqual(["committed", "committed", "rolled_back"],
                         sorted(call["outcome"] for call in calls))
        self.assertTrue(all(call["writer_kind"] == "news_ai_job_enqueue"
                            and call["sql_calls"] == 3
                            and call["backend_pid"] is not None for call in calls))
        self.assertEqual([(1, 0, 2)], [
            (call["rollbacks"], call["commits"], call["rows_attempted"])
            for call in calls if call["outcome"] == "rolled_back"
        ])

    def test_news_source_page_replay_and_failure_preserve_article_and_progress(self) -> None:
        token = uuid.uuid4().hex
        identity = f"https://diagnostic.example/{token}"
        title = f"diagnostic source page {token}"
        query_source = f"naver-query:diagnostic-{token}"
        market_source = f"naver-stock:flash:diagnostic-{token}"
        self.source_ids.extend((query_source, market_source))
        item = {
            "identity": identity,
            "document": {
                "title": title, "description": "source page diagnostic",
                "link": identity, "original_link": identity,
                "published_at": "2026-09-27T10:00:00+09:00",
            },
            "targets": [{"stock_code": "005930", "stock_name": "삼성전자",
                         "relation_status": "confirmed", "rule_version": "diagnostic-v1"}],
        }
        page = {
            "source_id": query_source, "scope": "query_set", "query_text": "증권",
            "run_id": f"diagnostic-first-{token}", "page_start": 1,
            "checked_at": time.time(), "cursor_identity": identity,
            "next_start": 2, "coverage": "query_set", "items": [item],
        }
        started = time.time() - 1
        self.assertEqual({"raw_count": 1, "unique_count": 1, "duplicate_count": 0},
                         self.store.save_news_source_page(page))
        self.assertEqual({"raw_count": 1, "unique_count": 0, "duplicate_count": 1},
                         self.store.save_news_source_page({
                             **page, "run_id": f"diagnostic-replay-{token}",
                             "page_start": 2, "next_start": 3,
                         }))
        self.assertEqual({"raw_count": 1, "unique_count": 0, "duplicate_count": 1},
                         self.store.save_news_source_page({
                             **page, "source_id": market_source,
                             "scope": "naver_stock_market", "query_text": "flash",
                             "run_id": f"diagnostic-market-{token}",
                         }))

        # The stock-article reader is intentionally scoped to watchlist rows;
        # source-page observations create GLOBAL articles. Seed the reader's
        # own supported scope rather than changing the source-page contract.
        watchlist_article_id = f"diagnostic-watchlist-article-{token}"
        watchlist_title = f"diagnostic watchlist article {token}"
        self.article_revision_ids.append(watchlist_article_id)
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_news_article_revisions("
                "article_revision_id,stock_code,identity,content_hash,collector_id,"
                "published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                "VALUES(%s,'005930',%s,%s,'diagnostic',NULL,%s,%s,'watchlist',NULL,%s::jsonb)",
                (watchlist_article_id, f"diagnostic-watchlist-identity-{token}",
                 f"diagnostic-watchlist-hash-{token}", time.time(), time.time(),
                 json.dumps({"title": watchlist_title, "stock_code": "005930"})),
            )

        article_history = self.store.load_news_history("article", target="GLOBAL",
                                                       identity=identity)
        self.assertEqual(1, len(article_history))
        article_id = article_history[0]["article_revision_id"]
        article_revision = self.store.load_news_article_revision(article_id)
        self.assertEqual((article_id, title), (
            article_revision["article_revision_id"], article_revision["document"]["title"],
        ))
        body_id = self.store.save_news_body_revision({
            "article_revision_id": article_id, "status": "fulltext",
            "body_text": "diagnostic news body", "fetched_at": time.time(),
        })
        body_revision = self.store.load_news_body_revision(body_id)
        self.assertEqual((body_id, "diagnostic news body"), (
            body_revision["body_revision_id"], body_revision["body_text"],
        ))
        self.assertEqual(body_id, self.store.load_latest_news_body(article_id)["body_revision_id"])
        self.assertEqual(3, self.store.load_news_source_cursor(query_source)["next_start"])
        diagnostic = self.store.load_news_source_diagnostics(source_id=query_source)
        self.assertEqual((2, 2), (len(diagnostic["runs"]), len(diagnostic["observations"])))
        self.assertEqual([title], [row["title"] for row in
                                   self.store.load_market_news_feed("flash")
                                   if row["title"] == title])
        self.assertEqual([watchlist_title], [row["title"] for row in
                                   self.store.load_stock_news_articles("005930")
                                   if row["title"] == watchlist_title])
        self.assertEqual([title], [row["title"] for row in
                                   self.store.load_confirmed_news_articles("005930")
                                   if row["title"] == title])
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM central_news_jobs WHERE article_revision_id=%s AND stage='BODY'",
                (article_id,),
            )
            self.assertEqual(1, cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM central_news_article_target_revisions "
                "WHERE article_revision_id=%s", (article_id,),
            )
            self.assertEqual(1, cursor.fetchone()[0])

        revised = {**item, "document": {**item["document"], "description": "revised"}}
        with self.assertRaisesRegex(ValueError, "identity와 기사 문서"):
            self.store.save_news_source_page({
                **page, "run_id": f"diagnostic-failed-{token}",
                "next_start": 99, "items": [revised, {"identity": "missing-document"}],
            })
        self.assertEqual([article_id], [row["article_revision_id"] for row in
                                        self.store.load_news_history("article", target="GLOBAL",
                                                                     identity=identity)])
        self.assertEqual(3, self.store.load_news_source_cursor(query_source)["next_start"])
        diagnostic = self.store.load_news_source_diagnostics(source_id=query_source)
        self.assertEqual((2, 2), (len(diagnostic["runs"]), len(diagnostic["observations"])))

        all_calls = summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
        reader_calls = [call for call in all_calls if call["access_mode"] == "read"]
        self.assertEqual(12, len(reader_calls))
        expected_readers = {
            ("read.news_history", "history:article"): 2,
            ("read.news_history", "history:body"): 1,
            ("read.news_revision", "article_revision"): 1,
            ("read.news_revision", "body_revision"): 1,
            ("read.news_source", "source_cursor"): 2,
            ("read.news_source", "source_diagnostics"): 2,
            ("read.news_publications", "market_feed"): 1,
            ("read.news_publications", "stock_articles"): 1,
            ("read.news_publications", "confirmed_articles"): 1,
        }
        self.assertEqual(expected_readers, {
            (family, kind): sum(
                call["writer_family"] == family and call["writer_kind"] == kind
                for call in reader_calls
            )
            for family, kind in expected_readers
        })
        self.assertTrue(all(
            call["outcome"] == "committed"
            and call["transactions"] == 1
            and call["commits"] == 1
            and call["sql_calls"] >= 1
            and call["backend_pid"] is not None
            for call in reader_calls
        ))
        grouped = summarize_db_calls(started, time.time() + 1)["readers"]
        self.assertEqual(3, grouped["read.news_history/history:article"]["calls"]
                         + grouped["read.news_history/history:body"]["calls"])
        self.assertEqual(4, grouped["read.news_source/source_cursor"]["calls"]
                         + grouped["read.news_source/source_diagnostics"]["calls"])
        self.assertEqual(3, sum(grouped[f"read.news_publications/{kind}"]["calls"]
                                for kind in ("market_feed", "stock_articles", "confirmed_articles")))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "news.source_page"
            and call["operation"] == "save_news_source_page"
        ]
        self.assertEqual(4, len(calls))
        self.assertEqual((3, 1), (
            sum(call["outcome"] == "committed" for call in calls),
            sum(call["outcome"] == "rolled_back" for call in calls),
        ))
        self.assertEqual({"news_source:query_set", "news_source:naver_stock_market"},
                         {call["writer_kind"] for call in calls})
        self.assertTrue(all(call["backend_pid"] is not None for call in calls))
        self.assertEqual([(2, 0, 1)], [
            (call["rows_attempted"], call["commits"], call["rollbacks"])
            for call in calls if call["outcome"] == "rolled_back"
        ])

    def test_news_event_parallel_replay_and_membership_failure_preserve_history(self) -> None:
        from psycopg.errors import UniqueViolation

        token = uuid.uuid4().hex
        article_id = f"diagnostic-event-article-{token}"
        body_id = f"diagnostic-event-body-{token}"
        identity = f"diagnostic-event-identity-{token}"
        self.article_revision_ids.append(article_id)
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_news_article_revisions("
                "article_revision_id,stock_code,identity,content_hash,collector_id,"
                "published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                "VALUES(%s,'005930',%s,%s,'diagnostic',NULL,%s,%s,'news_article',NULL,%s)",
                (article_id, identity, f"diagnostic-event-hash-{token}",
                 time.time(), time.time(), json.dumps({
                     "title": "테스트기업, 삼성전자와 500억원 공급계약 체결",
                     "stock_name": "테스트기업",
                 })),
            )
            cursor.execute(
                "INSERT INTO central_news_body_revisions("
                "body_revision_id,article_revision_id,content_hash,extractor_version,"
                "fetched_at,available_at,status,body_text,error) "
                "VALUES(%s,%s,%s,'diagnostic',%s,%s,'fulltext','공급계약 체결','')",
                (body_id, article_id, f"diagnostic-event-body-hash-{token}",
                 time.time(), time.time()),
            )
        rule = classify_supply_contract({
            "title": "테스트기업, 삼성전자와 500억원 공급계약 체결",
            "stock_name": "테스트기업", "stock_code": "005930",
        }, "공급계약 체결")
        self.assertIsNotNone(rule)
        result = rule.as_document()
        result["event_key"] = f"diagnostic-event-key-{token}"
        value = {
            "stock_code": "005930", "article_revision_id": article_id,
            "body_revision_id": body_id, "rule_version": rule.rule_version,
            "input_hash": f"diagnostic-event-input-{token}",
            "candidate_identities": (), "result": result,
        }
        started = time.time() - 1
        barrier = Barrier(2)

        def save() -> str:
            barrier.wait(timeout=10)
            return self.store.save_news_event_revision(value)

        with ThreadPoolExecutor(max_workers=2) as executor:
            revisions = list(executor.map(lambda _: save(), range(2)))
        self.assertEqual(revisions[0], revisions[1])
        history = [row for row in self.store.load_news_history("event", target="005930")
                   if row["article_revision_id"] == article_id]
        self.assertEqual(1, len(history))
        event = history[0]
        self.assertEqual((revisions[0], article_id, body_id),
                         (event["event_revision_id"], event["article_revision_id"],
                          event["body_revision_id"]))
        memberships = self.store.load_news_history("membership", target=event["event_id"])
        self.assertEqual(1, len(memberships))
        self.assertEqual((revisions[0], article_id, body_id),
                         (memberships[0]["event_revision_id"],
                          memberships[0]["article_revision_id"],
                          memberships[0]["body_revision_id"]))

        with patch("kiwoom_monitor.central_server.database_news_revisions.uuid") as event_uuid:
            event_uuid.uuid4.side_effect = [
                uuid.uuid4(), uuid.UUID(hex=memberships[0]["membership_revision_id"]),
            ]
            with self.assertRaises(UniqueViolation):
                self.store.save_news_event_revision({
                    **value, "input_hash": f"diagnostic-event-revised-{token}",
                })
        self.assertEqual([revisions[0]], [
            row["event_revision_id"] for row in
            self.store.load_news_history("event", identity=event["event_id"])
        ])
        self.assertEqual([memberships[0]["membership_revision_id"]], [
            row["membership_revision_id"] for row in
            self.store.load_news_history("membership", target=event["event_id"])
        ])

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "news.event"
            and call["operation"] == "save_news_event_revision"
        ]
        self.assertEqual(3, len(calls))
        self.assertEqual([2, 10], sorted(call["sql_calls"] for call in calls
                                         if call["outcome"] == "committed"))
        self.assertEqual([(9, 0, 1)], [
            (call["sql_calls"], call["commits"], call["rollbacks"])
            for call in calls if call["outcome"] == "rolled_back"
        ])
        self.assertTrue(all(call["writer_kind"] == "news_event"
                            and call["rows_attempted"] == 1
                            and call["backend_pid"] is not None for call in calls))

    def test_news_ai_results_keep_projection_revision_usage_atomic_and_observed(self) -> None:
        from psycopg.errors import UniqueViolation

        token = uuid.uuid4().hex
        target_id = f"diagnostic-ai-target-{token}"
        article_id = f"diagnostic-ai-article-{token}"
        body_id = f"diagnostic-ai-body-{token}"
        analysis_id = f"diagnostic-ai-revision-{token}"
        document_key = f"diagnostic-ai-document-{token}"
        usage_owner = f"diagnostic-ai-usage-{token}"
        usage_keys = (f"diagnostic-ai-request-{token}",
                      f"diagnostic-ai-retry-{token}")
        self.article_revision_ids.append(article_id)
        self.document_keys.extend((
            ("news_ai", target_id, document_key),
            ("news_request_usage", usage_owner, usage_keys[0]),
            ("news_request_usage", usage_owner, usage_keys[1]),
        ))
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_news_article_revisions("
                "article_revision_id,stock_code,identity,content_hash,collector_id,"
                "published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                "VALUES(%s,%s,%s,%s,'diagnostic',NULL,%s,%s,'news_article',NULL,%s)",
                (article_id, target_id, document_key, f"diagnostic-hash-{token}",
                 time.time(), time.time(), json.dumps({"title": "diagnostic"})),
            )
            cursor.execute(
                "INSERT INTO central_news_body_revisions("
                "body_revision_id,article_revision_id,content_hash,extractor_version,"
                "fetched_at,available_at,status,body_text,error) "
                "VALUES(%s,%s,%s,'diagnostic',%s,%s,'fulltext','diagnostic body','')",
                (body_id, article_id, f"diagnostic-body-hash-{token}",
                 time.time(), time.time()),
            )

        document = {"owner": target_id, "key": document_key,
                    "document": {"summary": "first"}}
        revision = {"analysis_revision_id": analysis_id, "target_id": target_id,
                    "article_revision_id": article_id, "body_revision_id": body_id,
                    "provider": "diagnostic", "model": "diagnostic-v1",
                    "prompt_version": "diagnostic-v1", "input_hash": f"diagnostic-body-hash-{token}",
                    "computed_at": time.time(), "output": {"summary": "first"},
                    "usage": {"total_tokens": 5}}
        usage = {"owner": usage_owner, "key": usage_keys[0],
                 "document": {"total_tokens": 5}}
        started = time.time() - 1
        self.store.save_news_ai_results([document], [revision], [usage])
        self.assertEqual(analysis_id, self.store.find_news_ai_revision(
            target_id=target_id, article_revision_id=article_id,
            body_revision_id=body_id, provider="diagnostic", model="diagnostic-v1",
            prompt_version="diagnostic-v1", schema_version=NEWS_ANALYSIS_SCHEMA_VERSION,
            input_hash=revision["input_hash"],
        ))
        self.assertEqual("first", self.store.load_documents("news_ai", target_id, 10)[0]
                         ["document"]["summary"])
        ai_history = self.store.load_news_history("ai", target=target_id)
        self.assertEqual([(analysis_id, article_id, body_id, "first")], [
            (row["analysis_revision_id"], row["article_revision_id"],
             row["body_revision_id"], row["output"]["summary"])
            for row in ai_history
        ])
        self.assertEqual([usage_keys[0]], [row["key"] for row in
                                        self.store.load_documents("news_request_usage", usage_owner, 10)])

        with self.assertRaises(UniqueViolation):
            self.store.save_news_ai_results(
                [{**document, "document": {"summary": "must roll back"}}],
                [revision],
                [{"owner": usage_owner, "key": usage_keys[1],
                  "document": {"total_tokens": 99}}],
            )
        self.assertEqual("first", self.store.load_documents("news_ai", target_id, 10)[0]
                         ["document"]["summary"])
        self.assertEqual([analysis_id], [row["analysis_revision_id"] for row in
                                         self.store.load_news_history("ai", target=target_id)])
        self.assertEqual([usage_keys[0]], [row["key"] for row in
                                        self.store.load_documents("news_request_usage", usage_owner, 10)])

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "news.ai_results"
            and call["operation"] == "save_news_ai_results"
        ]
        self.assertEqual(2, len(calls))
        self.assertEqual({"committed": (3, 1, 0), "rolled_back": (2, 0, 1)}, {
            call["outcome"]: (call["sql_calls"], call["commits"], call["rollbacks"])
            for call in calls
        })
        self.assertTrue(all(call["writer_kind"] == "news_ai_results"
                            and call["rows_attempted"] == 3
                            and call["backend_pid"] is not None for call in calls))
        revision_reads = [call for call in summarize_db_calls(
            started, time.time() + 1, mode="raw",
        )["calls"] if call["writer_family"] == "read.news_ai_revisions"]
        self.assertEqual(1, len(revision_reads))
        self.assertEqual(("analysis_revision", "find_news_ai_revision", "read", 1, 1), (
            revision_reads[0]["writer_kind"], revision_reads[0]["operation"],
            revision_reads[0]["access_mode"], revision_reads[0]["transactions"],
            revision_reads[0]["commits"],
        ))
        self.assertIsNotNone(revision_reads[0]["backend_pid"])

    def test_news_body_revision_links_rule_job_and_rolls_back_missing_article(self) -> None:
        token = uuid.uuid4().hex
        article_id = f"diagnostic-body-article-{token}"
        missing_id = f"diagnostic-body-missing-{token}"
        self.article_revision_ids.extend((article_id, missing_id))
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_news_article_revisions("
                "article_revision_id,stock_code,identity,content_hash,collector_id,"
                "published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                "VALUES(%s,'005930',%s,%s,'diagnostic',NULL,%s,%s,'news_article',NULL,%s)",
                (article_id, f"diagnostic-body-identity-{token}",
                 f"diagnostic-body-hash-{token}", time.time(), time.time(),
                 json.dumps({"title": "diagnostic", "stock_name": "삼성전자"})),
            )

        started = time.time() - 1
        body_id = self.store.save_news_body_revision({
            "article_revision_id": article_id, "status": "fulltext",
            "body_text": "diagnostic body", "fetched_at": time.time(),
        })
        body = self.store.load_news_body_revision(body_id)
        self.assertEqual((body_id, article_id, "fulltext", "diagnostic body"),
                         (body["body_revision_id"], body["article_revision_id"],
                          body["status"], body["body_text"]))
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT stage,state,attempts,target_id,payload_json FROM central_news_jobs "
                "WHERE article_revision_id=%s", (article_id,),
            )
            jobs = cursor.fetchall()
        self.assertEqual(1, len(jobs))
        stage, state, attempts, target_id, payload = jobs[0]
        self.assertEqual(("RULE", "PENDING", 0, "005930"),
                         (stage, state, attempts, target_id))
        if isinstance(payload, str):
            payload = json.loads(payload)
        self.assertEqual(body_id, payload["body_revision_id"])

        with self.assertRaisesRegex(ValueError, "규칙 작업을 예약할 기사 리비전이 없습니다"):
            self.store.save_news_body_revision({
                "article_revision_id": missing_id, "status": "fulltext",
                "body_text": "must roll back", "fetched_at": time.time(),
            })
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM central_news_body_revisions "
                           "WHERE article_revision_id=%s", (missing_id,))
            self.assertEqual(0, cursor.fetchone()[0])

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "news.body"
            and call["operation"] == "save_news_body_revision"
        ]
        self.assertEqual(2, len(calls))
        committed = next(call for call in calls if call["outcome"] == "committed")
        rolled_back = next(call for call in calls if call["outcome"] == "rolled_back")
        self.assertEqual(("news_body", 1, 3, 1, 0),
                         (committed["writer_kind"], committed["rows_attempted"],
                          committed["sql_calls"], committed["commits"],
                          committed["rollbacks"]))
        self.assertEqual((2, 0, 1),
                         (rolled_back["sql_calls"], rolled_back["commits"],
                          rolled_back["rollbacks"]))
        self.assertTrue(all(call["backend_pid"] is not None for call in calls))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["news_body"]["call_samples"]
        self.assertIn(committed["call_id"], {sample["db_call_id"] for sample in samples})
        self.assertNotIn(rolled_back["call_id"],
                         {sample["db_call_id"] for sample in samples})

    def test_external_news_claim_preserves_parallel_ownership_and_reader(self) -> None:
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('central_news_article_revisions')")
            self.assertIsNotNone(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM central_news_jobs j "
                "JOIN central_news_article_revisions a "
                "ON a.article_revision_id=j.article_revision_id "
                "WHERE j.state='PENDING' AND j.stage='BODY' "
                "AND j.processing_version=%s AND j.next_retry_at<=%s "
                "AND a.collection_scope='historical_news_pc_backfill'",
                (ARTICLE_BODY_EXTRACTOR_VERSION, time.time()),
            )
            self.assertEqual(0, cursor.fetchone()[0],
                             "dedicated test DB contains an unrelated ready PC search job")

        token = uuid.uuid4().hex
        keys = [f"diagnostic-external-claim-{token}-{index}" for index in range(2)]
        revisions = [f"diagnostic-external-article-{token}-{index}" for index in range(2)]
        self.news_keys.extend(keys)
        self.article_revision_ids.extend(revisions)
        with self.store._connect() as connection, connection.cursor() as cursor:
            for index, (key, revision) in enumerate(zip(keys, revisions, strict=True)):
                cursor.execute(
                    "INSERT INTO central_news_article_revisions("
                    "article_revision_id,stock_code,identity,content_hash,collector_id,"
                    "published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                    "VALUES(%s,'005930',%s,%s,'diagnostic',NULL,%s,%s,"
                    "'historical_news_pc_backfill',NULL,%s)",
                    (revision, f"diagnostic-identity-{token}-{index}",
                     f"diagnostic-hash-{token}-{index}", time.time(), time.time(),
                     json.dumps({"title": f"diagnostic {index}"})),
                )
                cursor.execute(
                    "INSERT INTO central_news_jobs("
                    "job_key,article_revision_id,stock_code,target_id,stage,input_hash,"
                    "processing_version,attempts,next_retry_at,state,output_ref,error,"
                    "payload_json,updated_at) "
                    "VALUES(%s,%s,'005930','005930','BODY',%s,%s,0,0,'PENDING','','',"
                    "'{}'::jsonb,0)",
                    (key, revision, f"diagnostic-hash-{token}-{index}",
                     ARTICLE_BODY_EXTRACTOR_VERSION),
                )

        started = time.time() - 1
        barrier = Barrier(2)

        def claim() -> dict[str, object] | None:
            barrier.wait(timeout=10)
            return self.store.claim_external_historical_news_job("BODY", scope="pc_search")

        with ThreadPoolExecutor(max_workers=2) as executor:
            claimed = list(executor.map(lambda _: claim(), range(2)))
        self.assertEqual(set(keys), {job["job_key"] for job in claimed if job})
        self.assertTrue(all(job and job["attempts"] == 1 for job in claimed))
        self.assertIsNone(self.store.claim_external_historical_news_job("BODY", scope="pc_search"))

        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT job_key,state,attempts FROM central_news_jobs "
                "WHERE job_key=ANY(%s)", (keys,),
            )
            self.assertEqual({key: ("RUNNING", 1) for key in keys},
                             {key: (state, attempts)
                              for key, state, attempts in cursor.fetchall()})
        for revision in revisions:
            article = self.store.load_news_article_revision(revision)
            self.assertEqual((revision, "historical_news_pc_backfill"),
                             (article["article_revision_id"], article["collection_scope"]))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "news.external_claim"
            and call["operation"] == "claim_external_historical_news_job"
        ]
        self.assertEqual(3, len(calls))
        self.assertEqual([1, 2, 2], sorted(call["sql_calls"] for call in calls))
        self.assertTrue(all(
            call["writer_kind"] == "news_external_claim"
            and call["source"] == "pc_search"
            and call["outcome"] == "committed"
            and call["commits"] == 1
            and call["rows_attempted"] is None
            and call["backend_pid"] is not None
            for call in calls
        ))

    def test_external_news_finish_preserves_body_rule_replay_and_atomic_rollback(self) -> None:
        from kiwoom_monitor.central_server import database_historical_news as historical_news_module
        from kiwoom_monitor.domain.news_observation import ARTICLE_BODY_EXTRACTOR_VERSION
        from kiwoom_monitor.application.news_rules import SUPPLY_CONTRACT_RULE_VERSION

        token = uuid.uuid4().hex
        article_id = f"diagnostic-external-finish-article-{token}"
        identity = f"diagnostic-external-finish-identity-{token}"
        body_job_key = hashlib.sha256(f"body-{token}".encode()).hexdigest()
        article_hash = f"diagnostic-external-finish-hash-{token}"
        self.article_revision_ids.append(article_id)
        self.document_keys.append(("news_assessment", "005930", article_id))
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_news_article_revisions("
                "article_revision_id,stock_code,identity,content_hash,collector_id,"
                "published_at,received_at,available_at,collection_scope,revision_of,document_json) "
                "VALUES(%s,'005930',%s,%s,'diagnostic',NULL,%s,%s,"
                "'historical_news_pc_backfill',NULL,%s)",
                (article_id, identity, article_hash, time.time(), time.time(),
                 json.dumps({"title": "테스트기업, 삼성전자와 500억원 공급계약 체결",
                             "stock_name": "테스트기업"})),
            )
            cursor.execute(
                "INSERT INTO central_news_jobs("
                "job_key,article_revision_id,stock_code,target_id,stage,input_hash,"
                "processing_version,attempts,next_retry_at,state,output_ref,error,"
                "payload_json,updated_at) "
                "VALUES(%s,%s,'005930','005930','BODY',%s,%s,1,0,'RUNNING','','',"
                "'{}'::jsonb,0)",
                (body_job_key, article_id, article_hash, ARTICLE_BODY_EXTRACTOR_VERSION),
            )

        started = time.time() - 1
        body_result = {"job_key": body_job_key, "attempts": 1, "stage": "BODY",
                       "body_text": "삼성전자가 500억원 공급계약을 체결했다.",
                       "body_status": "fulltext", "fetched_at": time.time()}
        body_saved = self.store.complete_external_historical_news_job(body_result)
        self.assertEqual("completed", body_saved["state"])
        body_id = body_saved["output_ref"]
        self.assertEqual(body_id, self.store.load_news_body_revision(body_id)["body_revision_id"])
        self.assertEqual("already_completed",
                         self.store.complete_external_historical_news_job(body_result)["state"])

        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT job_key,payload_json FROM central_news_jobs "
                "WHERE article_revision_id=%s AND stage='RULE'", (article_id,),
            )
            rule_job_key, rule_payload = cursor.fetchone()
            self.assertEqual(body_id, rule_payload["body_revision_id"])
            cursor.execute(
                "UPDATE central_news_jobs SET state='RUNNING',attempts=1 WHERE job_key=%s",
                (rule_job_key,),
            )

        rule = classify_supply_contract(
            {"title": "테스트기업, 삼성전자와 500억원 공급계약 체결",
             "stock_name": "테스트기업", "stock_code": "005930"},
            "삼성전자가 500억원 공급계약을 체결했다.",
        )
        self.assertIsNotNone(rule)
        rule_document = rule.as_document()
        self.assertEqual(SUPPLY_CONTRACT_RULE_VERSION, rule_document["rule_version"])
        rule_document["event_key"] = f"diagnostic-external-finish-event-{token}"
        rule_result = {"job_key": rule_job_key, "attempts": 1, "stage": "RULE",
                       "assessment": {"status": "diagnostic"}, "core_sentences": [],
                       "rule_result": rule_document}
        save_event = historical_news_module._save_postgres_news_event

        def fail_after_event(cursor: object, value: dict[str, object]) -> str:
            save_event(cursor, value)
            raise RuntimeError("diagnostic failure after event membership")

        with patch.object(historical_news_module, "_save_postgres_news_event",
                          side_effect=fail_after_event):
            with self.assertRaisesRegex(RuntimeError, "after event membership"):
                self.store.complete_external_historical_news_job(rule_result)
        self.assertIsNone(self.store.load_document("news_assessment", "005930", article_id))
        self.assertEqual([], [row for row in self.store.load_news_history("event", target="005930")
                              if row["article_revision_id"] == article_id])
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT state,output_ref FROM central_news_jobs WHERE job_key=%s",
                           (rule_job_key,))
            self.assertEqual(("RUNNING", ""), cursor.fetchone())

        rule_saved = self.store.complete_external_historical_news_job(rule_result)
        self.assertEqual("completed", rule_saved["state"])
        self.assertEqual("already_completed",
                         self.store.complete_external_historical_news_job(rule_result)["state"])
        self.assertIsNotNone(self.store.load_document("news_assessment", "005930", article_id))
        events = [row for row in self.store.load_news_history("event", target="005930")
                  if row["article_revision_id"] == article_id]
        self.assertEqual([rule_saved["output_ref"]], [row["event_revision_id"] for row in events])
        memberships = self.store.load_news_history("membership", target=events[0]["event_id"])
        self.assertEqual([rule_saved["output_ref"]],
                         [row["event_revision_id"] for row in memberships
                          if row["article_revision_id"] == article_id])

        calls = [call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
                 if call["operation"] == "complete_external_historical_news_job"]
        self.assertEqual((2, 3),
                         (sum(call["writer_kind"] == "news_external_finish:BODY" for call in calls),
                          sum(call["writer_kind"] == "news_external_finish:RULE" for call in calls)))
        self.assertEqual((4, 1),
                         (sum(call["outcome"] == "committed" for call in calls),
                          sum(call["outcome"] == "rolled_back" for call in calls)))
        self.assertTrue(all(call["writer_family"] == "news.external_finish"
                            and call["rows_attempted"] == 1
                            and call["backend_pid"] is not None for call in calls))

    def test_news_sync_document_replay_preserves_row_and_correlates_metrics(self) -> None:
        owner = f"diagnostic-common-access-sync-{uuid.uuid4().hex}"
        self.document_keys.append(("news_sync", owner, "latest"))
        document = {"owner": owner, "key": "latest", "document": {"checked_at": "2026-09-27T00:00:00+00:00"}}
        started = time.time() - 1
        self.store.upsert_documents("news_sync", [document])
        first = self.store.load_document("news_sync", owner, "latest")
        self.store.upsert_documents("news_sync", [document])
        replay = self.store.load_document("news_sync", owner, "latest")
        self.assertEqual(first, replay)
        self.assertEqual(document["document"], replay["document"])
        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:news_sync"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["outcome"] == "committed"
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:news_sync"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_news_article_projection_revision_and_job_share_observed_commit(self) -> None:
        stock = f"DIAG{uuid.uuid4().hex[:12]}"
        identity = f"article-{uuid.uuid4().hex}"
        self.article_revision_identities.append((stock, identity))
        self.document_keys.append(("news_article", stock, identity))
        value = {
            "owner": stock, "key": identity, "collector_id": "diagnostic",
            "document": {"stock_code": stock, "identity": identity, "title": "first"},
        }
        started = time.time() - 1
        self.store.upsert_documents("news_article", [value])
        first = self.store.load_document("news_article", stock, identity)
        self.store.upsert_documents("news_article", [value])
        replay = self.store.load_document("news_article", stock, identity)
        self.assertEqual(first, replay)
        revised = {**value, "document": {**value["document"], "title": "revised"}}
        self.store.upsert_documents("news_article", [revised])
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT article_revision_id,revision_of FROM central_news_article_revisions "
                "WHERE stock_code=%s AND identity=%s ORDER BY accepted_sequence",
                (stock, identity),
            )
            revisions = cursor.fetchall()
            cursor.execute(
                "SELECT COUNT(*) FROM central_news_jobs WHERE article_revision_id=ANY(%s)",
                ([row[0] for row in revisions],),
            )
            job_count = cursor.fetchone()[0]
        self.assertEqual(2, len(revisions))
        self.assertEqual(str(revisions[0][0]), str(revisions[1][1]))
        self.assertEqual(2, job_count)
        self.assertEqual("revised", self.store.load_news_article_revision(
            str(revisions[1][0]),
        )["document"]["title"])
        self.assertEqual("revised", self.store.load_document(
            "news_article", stock, identity,
        )["document"]["title"])
        with self.assertRaises(ValueError):
            self.store.upsert_documents("news_article", [
                revised, {"owner": stock, "key": "invalid", "document": "invalid"},
            ])
        self.assertIsNone(self.store.load_document("news_article", stock, "invalid"))
        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:news_article"
        ]
        self.assertEqual(4, len(calls))
        self.assertEqual(3, sum(call["commits"] for call in calls))
        self.assertEqual(1, sum(call["rollbacks"] for call in calls))
        self.assertTrue(all(call["backend_pid"] is not None for call in calls))
        samples = summarize_market_bar_saves(started, time.time() + 1)[
            "writer_transactions"]["document:news_article"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls if call["commits"]}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_news_sync_projection_collections_replay_and_rollback_are_observed(self) -> None:
        suffix = uuid.uuid4().hex
        values = {
            "news_ai": {"owner": f"DIAG{suffix[:12]}", "key": f"ai-{suffix}",
                        "document": {"title": "analysis", "score": 0.7}},
            "news_ai_shared": {"owner": "shared", "key": f"shared-{suffix}",
                               "document": {"summary": "shared summary"}},
            "news_request_usage": {"owner": "usage", "key": f"usage-{suffix}",
                                   "document": {"provider": "diagnostic", "requests": 1}},
        }
        for collection, value in values.items():
            self.document_keys.append((collection, value["owner"], value["key"]))
        failed_key = f"failed-{suffix}"
        self.document_keys.append(("news_ai", values["news_ai"]["owner"], failed_key))
        started = time.time() - 1
        for collection, value in values.items():
            self.store.upsert_documents(collection, [value])
            first = self.store.load_document(collection, value["owner"], value["key"])
            self.store.upsert_documents(collection, [value])
            self.assertEqual(first, self.store.load_document(
                collection, value["owner"], value["key"],
            ))
        with self.assertRaises(KeyError):
            self.store.upsert_documents("news_ai", [
                {"owner": values["news_ai"]["owner"], "key": failed_key,
                 "document": {"title": "must rollback"}},
                {"owner": values["news_ai"]["owner"], "key": f"invalid-{suffix}"},
            ])
        self.assertIsNone(self.store.load_document(
            "news_ai", values["news_ai"]["owner"], failed_key,
        ))
        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] in {
                "document:news_ai", "document:news_ai_shared", "document:news_request_usage",
            }
        ]
        self.assertEqual(7, len(calls))
        self.assertEqual(6, sum(call["commits"] for call in calls))
        self.assertEqual(1, sum(call["rollbacks"] for call in calls))
        expected_calls = {
            "document:news_ai": 3,
            "document:news_ai_shared": 2,
            "document:news_request_usage": 2,
        }
        for kind, expected_count in expected_calls.items():
            kind_calls = [call for call in calls if call["writer_kind"] == kind]
            self.assertEqual(expected_count, len(kind_calls), kind)
            self.assertTrue(all(
                call["writer_family"] == "document.collection"
                and call["backend_pid"] is not None
                for call in kind_calls
            ))

    def test_journal_sync_collections_keep_scopes_and_independent_observed_writes(self) -> None:
        control = json.loads(self.control.read_text(encoding="utf-8"))
        control["diagnostic_tool"]["expires_at"] = time.time() + 300
        control["capture"]["expires_at"] = control["diagnostic_tool"]["expires_at"]
        self.control.write_text(json.dumps(control), encoding="utf-8")
        refresh_capture_state(force=True)
        suffix = uuid.uuid4().hex
        path = Path(self.tmp.name) / "journal-sync.sqlite3"
        repository = JournalRepository(path)
        real = AccountScope("kiwoom", AccountEnvironment.REAL, str(uuid.uuid4()))
        mock = AccountScope("kiwoom", AccountEnvironment.MOCK, str(uuid.uuid4()))
        for index, scope in enumerate((real, mock), start=1):
            repository.upsert_history_sync((TradeFill(
                f"diag-{suffix}-{index}", "005930", "삼성전자", "매수",
                datetime(2026, 9, 28, 9, index), 1, 70000,
                origin_scope=scope,
            ),), (), account_scope=scope)
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO journal_settings VALUES(?,?,?)",
                    (f"diagnostic-{suffix}", '["first"]', "2026-09-28T10:00:00"),
                )
            real_fill_key = str(connection.execute(
                "SELECT fill_key FROM trade_fills WHERE origin_account_ref=?",
                (real.account_ref,),
            ).fetchone()[0])
        repository.remember_stock(f"D{suffix[:8]}", "diagnostic", datetime(2026, 9, 28, 9))
        legacy_key = f"legacy-{suffix}"
        repository.assign_group((legacy_key,), "diagnostic")
        repository.clear_group_assignments((legacy_key,))
        repository.assign_group((real_fill_key,), "diagnostic", account_scope=real)
        repository.clear_group_assignments((real_fill_key,), account_scope=real)

        case = self

        class StoreClient:
            def __init__(self) -> None:
                self.keys: set[tuple[str, str, str]] = set()
                self.posted: list[str] = []
                self.fail_v2_fills = False

            def capabilities(self) -> dict[str, bool]:
                return {"journal_v2_sync": True}

            def load_all(self, collection: str) -> list[dict[str, object]]:
                return [
                    row for kind, owner, key in sorted(self.keys)
                    if kind == collection
                    if (row := case.store.load_document(kind, owner, key)) is not None
                ]

            def upsert(self, collection: str, documents: list[dict[str, object]]) -> int:
                self.posted.append(collection)
                for document in documents:
                    identity = (collection, str(document["owner"]), str(document["key"]))
                    self.keys.add(identity)
                    case.document_keys.append(identity)
                if self.fail_v2_fills and collection == "journal_v2_fills":
                    documents = [*documents, {"owner": real.account_ref, "key": f"bad-{suffix}"}]
                case.store.upsert_documents(collection, documents)
                return len(documents)

        client = StoreClient()
        service = CentralJournalSyncService(client)  # type: ignore[arg-type]
        started = time.time() - 1
        self.assertEqual(6, service.sync(path))
        self.assertEqual(set(), set(service.pending_collections))
        self.assertEqual(2, len([
            key for kind, owner, key in client.keys if kind == "journal_v2_fills"
        ]))
        self.assertEqual({real.account_ref, mock.account_ref}, {
            owner for kind, owner, _ in client.keys if kind == "journal_v2_fills"
        })
        self.assertNotIn("journal_fills", client.posted)
        self.assertEqual({"legacy"}, {
            owner for kind, owner, _ in client.keys if kind == "journal_sync_states"
        })
        self.assertEqual({real.account_ref}, {
            owner for kind, owner, _ in client.keys if kind == "journal_v2_sync_states"
        })
        first_rows = {
            identity: self.store.load_document(*identity) for identity in client.keys
        }
        self.assertTrue(all(value is not None for value in first_rows.values()))
        self.assertEqual(6, service.sync(path))
        self.assertEqual(first_rows, {
            identity: self.store.load_document(*identity) for identity in client.keys
        })

        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute(
                    "UPDATE journal_settings SET value_json=?,updated_at=? WHERE setting_key=?",
                    ('["newer"]', "2026-09-28T11:00:00", f"diagnostic-{suffix}"),
                )
        client.fail_v2_fills = True
        with self.assertRaises(KeyError):
            service.sync(path)
        setting = next(identity for identity in client.keys if identity[0] == "journal_settings")
        self.assertEqual('["newer"]', self.store.load_document(*setting)["document"]["value_json"])
        for identity, first in first_rows.items():
            if identity[0] == "journal_v2_fills":
                self.assertEqual(first, self.store.load_document(*identity))

        # Every remaining journal kind uses this same PostgreSQL UPSERT path.
        # Exercise each explicit registration once without fabricating local journal rows.
        collections = tuple(spec.collection for spec in (*_V1_SPECS, *_V2_SPECS)) + (
            "journal_sync_states", "journal_v2_sync_states",
        )
        direct = set(collections) - set(client.posted)
        for collection in sorted(direct):
            owner = real.account_ref if collection.startswith("journal_v2_") else f"diagnostic-{suffix}"
            key = f"diagnostic-{suffix}"
            document = {"diagnostic_key": key, "origin_broker": "legacy"}
            if collection.startswith("journal_v2_"):
                document.update({
                    "origin_broker": "kiwoom", "origin_environment": "real",
                    "origin_account_ref": real.account_ref,
                    "canonical_account_ref": real.account_ref,
                })
            identity = (collection, owner, key)
            self.document_keys.append(identity)
            self.store.upsert_documents(collection, [{
                "owner": owner, "key": key, "document": document,
            }])
            self.assertEqual(document, self.store.load_document(*identity)["document"])

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] in {f"document:{kind}" for kind in collections}
        ]
        self.assertEqual(set(collections), {
            str(call["writer_kind"]).removeprefix("document:") for call in calls
        })
        for collection in collections:
            self.assertEqual(
                client.posted.count(collection) + int(collection in direct),
                sum(call["writer_kind"] == f"document:{collection}" for call in calls),
                collection,
            )
        self.assertEqual(len(calls), len({call["call_id"] for call in calls}))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["backend_pid"] is not None for call in calls
        ))
        failed = [call for call in calls if call["writer_kind"] == "document:journal_v2_fills"
                  and call["rollbacks"] == 1]
        self.assertEqual(1, len(failed))
        self.assertEqual(3, failed[0]["rows_attempted"])
        self.assertEqual(1, sum(call["rollbacks"] for call in calls))
        self.assertEqual(len(calls) - 1, sum(call["commits"] for call in calls))
        samples = summarize_market_bar_saves(started, time.time() + 1)["writer_transactions"]
        for collection in collections:
            kind = f"document:{collection}"
            success_ids = {call["call_id"] for call in calls
                           if call["writer_kind"] == kind and call["commits"] == 1}
            self.assertTrue(success_ids.issubset(
                {sample["db_call_id"] for sample in samples[kind]["call_samples"]}
            ), kind)

    def test_central_settings_collections_replay_and_keep_independent_transactions(self) -> None:
        suffix = uuid.uuid4().hex
        setting_key = f"diagnostic_setting_{suffix}"
        column_name = f"diagnostic_column_{suffix}"
        local_path = Path(self.tmp.name) / "app-settings.sqlite3"
        with closing(sqlite3.connect(local_path)) as connection:
            connection.executescript("""
                CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE central_setting_versions(setting_key TEXT PRIMARY KEY,updated_at TEXT NOT NULL);
                CREATE TABLE column_settings(column_name TEXT PRIMARY KEY,visible INTEGER,position INTEGER,width INTEGER);
                CREATE TABLE central_column_setting_versions(column_name TEXT PRIMARY KEY,updated_at TEXT NOT NULL);
            """)
            with connection:
                connection.executemany(
                    "INSERT INTO settings VALUES(?,?)", [(setting_key, "initial")],
                )
                connection.executemany(
                    "INSERT INTO central_setting_versions VALUES(?,?)",
                    [(setting_key, "2026-09-28T09:00:00+00:00")],
                )
                connection.execute(
                    "INSERT INTO column_settings VALUES(?,?,?,?)", (column_name, 1, 3, 280),
                )
                connection.execute(
                    "INSERT INTO central_column_setting_versions VALUES(?,?)",
                    (column_name, "2026-09-28T09:00:00+00:00"),
                )

        case = self

        class StoreClient:
            def __init__(self) -> None:
                self.keys: set[tuple[str, str, str]] = set()
                self.posted: list[str] = []
                self.fail_column_settings = False

            def load_all(self, collection: str) -> list[dict[str, object]]:
                return [
                    row for kind, owner, key in sorted(self.keys)
                    if kind == collection
                    if (row := case.store.load_document(kind, owner, key)) is not None
                ]

            def upsert(self, collection: str, documents: list[dict[str, object]]) -> int:
                self.posted.append(collection)
                for document in documents:
                    identity = (collection, str(document["owner"]), str(document["key"]))
                    self.keys.add(identity)
                    case.document_keys.append(identity)
                if self.fail_column_settings and collection == "app_column_settings":
                    documents = [*documents, {"owner": "main_table", "key": f"bad-{suffix}"}]
                case.store.upsert_documents(collection, documents)
                return len(documents)

        client = StoreClient()
        service = CentralSettingsSyncService(client)  # type: ignore[arg-type]
        started = time.time() - 1
        self.assertEqual(2, service.sync(local_path))
        self.assertEqual(["app_settings", "app_column_settings"], client.posted)
        first_rows = {identity: self.store.load_document(*identity) for identity in client.keys}
        self.assertEqual(2, len(first_rows))
        self.assertTrue(all(value is not None for value in first_rows.values()))
        self.assertNotIn("width", next(
            row["document"] for identity, row in first_rows.items()
            if identity[0] == "app_column_settings"
        ))

        client.posted.clear()
        self.assertEqual(0, service.sync(local_path))
        self.assertEqual([], client.posted)
        self.assertEqual(first_rows, {
            identity: self.store.load_document(*identity) for identity in client.keys
        })

        with closing(sqlite3.connect(local_path)) as connection:
            with connection:
                connection.execute("UPDATE settings SET value='newer' WHERE key=?", (setting_key,))
                connection.execute(
                    "UPDATE central_setting_versions SET updated_at=? WHERE setting_key=?",
                    ("2026-09-28T10:00:00+00:00", setting_key),
                )
                connection.execute(
                    "UPDATE column_settings SET visible=0,position=8 WHERE column_name=?",
                    (column_name,),
                )
                connection.execute(
                    "UPDATE central_column_setting_versions SET updated_at=? WHERE column_name=?",
                    ("2026-09-28T10:00:00+00:00", column_name),
                )
        client.fail_column_settings = True
        with self.assertRaises(KeyError):
            service.sync(local_path)
        self.assertEqual(["app_settings", "app_column_settings"], client.posted[-2:])
        setting_identity = next(identity for identity in client.keys if identity[0] == "app_settings")
        column_identity = next(identity for identity in client.keys if identity[0] == "app_column_settings")
        self.assertEqual("newer", self.store.load_document(*setting_identity)["document"]["value"])
        self.assertEqual(first_rows[column_identity], self.store.load_document(*column_identity))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] in {"document:app_settings", "document:app_column_settings"}
        ]
        self.assertEqual(4, len(calls))
        for kind in ("document:app_settings", "document:app_column_settings"):
            kind_calls = [call for call in calls if call["writer_kind"] == kind]
            self.assertEqual(2, len(kind_calls), kind)
            self.assertTrue(all(
                call["writer_family"] == "document.collection"
                and call["backend_pid"] is not None for call in kind_calls
            ))
        rolled_back = [call for call in calls if call["rollbacks"] == 1]
        self.assertEqual(["document:app_column_settings"], [call["writer_kind"] for call in rolled_back])
        self.assertEqual(2, rolled_back[0]["rows_attempted"])
        self.assertEqual(3, sum(call["commits"] for call in calls))
        samples = summarize_market_bar_saves(started, time.time() + 1)["writer_transactions"]
        for kind in ("document:app_settings", "document:app_column_settings"):
            success_ids = {call["call_id"] for call in calls
                           if call["writer_kind"] == kind and call["commits"] == 1}
            self.assertTrue(success_ids.issubset(
                {sample["db_call_id"] for sample in samples[kind]["call_samples"]}
            ), kind)

    def test_journal_link_scopes_replay_tombstone_and_metrics_are_preserved(self) -> None:
        suffix = uuid.uuid4().hex
        group, identity, stock = f"diagnostic-{suffix}", f"article-{suffix}", "005930"
        legacy = {
            "owner": group, "key": f"{stock}|{identity}",
            "document": {
                "group_id": group, "stock_code": stock, "identity": identity,
                "origin_broker": "legacy", "is_deleted": False,
                "updated_at": "2026-09-28T10:00:00+09:00",
            },
        }
        scoped = []
        for environment in ("real", "mock"):
            account_ref = str(uuid.uuid4())
            document = {
                "group_id": group, "stock_code": stock, "identity": identity,
                "origin_broker": "kiwoom", "origin_environment": environment,
                "origin_account_ref": account_ref, "canonical_account_ref": account_ref,
                "is_deleted": False, "updated_at": "2026-09-28T10:00:00+09:00",
            }
            scoped.append({
                "owner": account_ref, "key": _journal_news_link_key(document),
                "document": document,
            })
        self.document_keys.append(("journal_news_link", legacy["owner"], legacy["key"]))
        self.document_keys.extend(
            ("journal_v2_news_links", value["owner"], value["key"]) for value in scoped
        )
        failed_key = f"failed-{suffix}"
        self.document_keys.append(("journal_v2_news_links", scoped[0]["owner"], failed_key))
        started = time.time() - 1
        self.store.upsert_documents("journal_news_link", [legacy])
        self.store.upsert_documents("journal_v2_news_links", scoped)
        legacy_first = self.store.load_document(
            "journal_news_link", legacy["owner"], legacy["key"],
        )
        self.store.upsert_documents("journal_news_link", [legacy])
        self.assertEqual(legacy_first, self.store.load_document(
            "journal_news_link", legacy["owner"], legacy["key"],
        ))
        first = self.store.load_document(
            "journal_v2_news_links", scoped[0]["owner"], scoped[0]["key"],
        )
        self.store.upsert_documents("journal_v2_news_links", [scoped[0]])
        self.assertEqual(first, self.store.load_document(
            "journal_v2_news_links", scoped[0]["owner"], scoped[0]["key"],
        ))
        tombstone = {**scoped[0], "document": {
            **scoped[0]["document"], "is_deleted": True,
            "updated_at": "2026-09-28T11:00:00+09:00",
        }}
        self.store.upsert_documents("journal_v2_news_links", [tombstone])
        self.assertTrue(self.store.load_document(
            "journal_v2_news_links", scoped[0]["owner"], scoped[0]["key"],
        )["document"]["is_deleted"])
        self.assertEqual(legacy["document"], self.store.load_document(
            "journal_news_link", legacy["owner"], legacy["key"],
        )["document"])
        self.assertEqual(scoped[1]["document"], self.store.load_document(
            "journal_v2_news_links", scoped[1]["owner"], scoped[1]["key"],
        )["document"])
        local_path = Path(self.tmp.name) / "journal-links.sqlite3"
        local_store = StockNewsRepository(local_path)
        pulled = {
            "journal_news_link": [self.store.load_document(
                "journal_news_link", legacy["owner"], legacy["key"],
            )],
            "journal_v2_news_links": [self.store.load_document(
                "journal_v2_news_links", value["owner"], value["key"],
            ) for value in scoped],
        }
        merged = CentralContentSyncService._write_news(local_path, pulled)
        self.assertEqual((1, 2), (merged["journal_news_link"], merged["journal_v2_news_links"]))
        real = AccountScope("kiwoom", AccountEnvironment.REAL, scoped[0]["owner"])
        mock = AccountScope("kiwoom", AccountEnvironment.MOCK, scoped[1]["owner"])
        self.assertEqual({identity}, local_store.journal_linked_identities(group, stock))
        self.assertEqual(set(), local_store.journal_linked_identities(group, stock, real))
        self.assertEqual({identity}, local_store.journal_linked_identities(group, stock, mock))
        CentralContentSyncService._write_news(local_path, {
            "journal_v2_news_links": [scoped[0]],
        })
        self.assertEqual(set(), local_store.journal_linked_identities(group, stock, real))
        with self.assertRaises(KeyError):
            self.store.upsert_documents("journal_v2_news_links", [
                {"owner": scoped[0]["owner"], "key": failed_key,
                 "document": {"is_deleted": False}},
                {"owner": scoped[0]["owner"], "key": f"invalid-{suffix}"},
            ])
        self.assertIsNone(self.store.load_document(
            "journal_v2_news_links", scoped[0]["owner"], failed_key,
        ))
        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] in {
                "document:journal_news_link", "document:journal_v2_news_links",
            }
        ]
        self.assertEqual(6, len(calls))
        self.assertEqual(5, sum(call["commits"] for call in calls))
        self.assertEqual(1, sum(call["rollbacks"] for call in calls))
        self.assertEqual(2, sum(call["writer_kind"] == "document:journal_news_link"
                                for call in calls))
        self.assertEqual(4, sum(call["writer_kind"] == "document:journal_v2_news_links"
                                for call in calls))
        self.assertEqual([1, 1, 2], sorted(
            call["rows_attempted"] for call in calls
            if call["writer_kind"] == "document:journal_v2_news_links"
            and call["commits"] == 1
        ))
        self.assertTrue(all(call["writer_family"] == "document.collection"
                            and call["backend_pid"] is not None for call in calls))
        samples = summarize_market_bar_saves(started, time.time() + 1)["writer_transactions"]
        for kind in ("document:journal_news_link", "document:journal_v2_news_links"):
            success_ids = {call["call_id"] for call in calls
                           if call["writer_kind"] == kind and call["commits"] == 1}
            self.assertTrue(success_ids.issubset(
                {sample["db_call_id"] for sample in samples[kind]["call_samples"]}
            ))

    def test_theme_replacement_keeps_native_transactions_and_snapshot_history(self) -> None:
        collections = ("theme_profile", "theme_stock", "theme_metadata")
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT collection,COUNT(*) FROM central_documents "
                "WHERE collection=ANY(%s) GROUP BY collection", (list(collections),),
            )
            occupied = cursor.fetchall()
        if occupied:
            self.skipTest("dedicated DB theme collections are occupied; refusing full replacement")
        profile_id = f"diagnostic-{uuid.uuid4().hex}"
        self.theme_snapshot_profile_ids.append(profile_id)
        values = {
            "theme_profile": [{"owner": profile_id, "key": "theme", "document": {"name": "theme"}}],
            "theme_stock": [{"owner": profile_id, "key": "005930|theme", "document": {"stock_code": "005930"}}],
            "theme_metadata": [{
                "owner": "default", "key": "full",
                "document": {
                    "format": "kiwoom-realtime-monitor-theme-db", "version": 1,
                    "active_profile": profile_id, "profiles": [],
                },
            }],
        }
        for collection, entries in values.items():
            self.document_keys.append((collection, entries[0]["owner"], entries[0]["key"]))
        started = time.time() - 1
        for collection in ("theme_profile", "theme_stock"):
            self.store.upsert_documents(collection, values[collection])
        self.store.upsert_documents("theme_metadata", values["theme_metadata"])
        before = self.store.load_theme_snapshots()
        for collection in collections:
            self.store.replace_documents(collection, values[collection])
        after = self.store.load_theme_snapshots()
        self.assertEqual(1, sum(row["profile_id"] == profile_id for row in before))
        self.assertEqual(1, sum(row["profile_id"] == profile_id for row in after))
        with self.assertRaises(ValueError):
            self.store.replace_documents("theme_metadata", [
                {"owner": "default", "key": "full", "document": "invalid"},
            ])
        self.assertEqual(after, self.store.load_theme_snapshots())
        for collection, entries in values.items():
            self.assertEqual(entries[0]["document"], self.store.load_document(
                collection, entries[0]["owner"], entries[0]["key"],
            )["document"])
        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] in {f"document:{name}" for name in collections}
        ]
        self.assertEqual(7, len(calls))
        self.assertEqual(4, sum(call["operation"] == "replace_documents" for call in calls))
        self.assertEqual(3, sum(call["operation"] == "upsert_documents" for call in calls))
        self.assertEqual(6, sum(call["commits"] for call in calls))
        self.assertEqual(1, sum(call["rollbacks"] for call in calls))
        self.assertTrue(all(call["writer_family"] == "document.collection"
                            and call["backend_pid"] is not None for call in calls))

    def test_krx_trading_day_document_replay_preserves_reader_and_correlates_metrics(self) -> None:
        day = "2026-09-28"
        key = "latest"
        self.document_keys.append(("krx_trading_day_observations", day, key))
        document = {
            "owner": day,
            "key": key,
            "document": {
                "trading_date": day,
                "source": "kiwoom_websocket_0s",
                "status_code": "3",
                "trade_time": "090000",
                "remaining_time": "000000",
                "observed_at": "2026-09-28T09:00:00+09:00",
            },
        }
        started = time.time() - 1
        self.store.upsert_documents("krx_trading_day_observations", [document])
        first = self.store.load_document("krx_trading_day_observations", day, key)
        self.store.upsert_documents("krx_trading_day_observations", [document])
        replay = self.store.load_document("krx_trading_day_observations", day, key)
        self.assertEqual(first, replay)
        self.assertEqual(document["document"], replay["document"])

        service = object.__new__(AutonomousTop20Service)
        service._store = self.store
        service._krx_trading_day_cache = {}
        self.assertTrue(asyncio.run(service._is_observed_krx_trading_day(day)))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:krx_trading_day_observations" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:krx_trading_day_observations"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_external_market_roll_state_replay_preserves_reader_and_correlates_metrics(self) -> None:
        instrument = f"diagnostic-roll-{uuid.uuid4().hex}"
        key = "current"
        self.document_keys.append(("external_market_roll_state", instrument, key))
        state = {
            "provider": "yahoo_delayed",
            "instrument": instrument,
            "active_contract": "CLV26.NYM",
            "next_contract": "CLX26.NYM",
            "confirmation_count": 1,
            "required_confirmations": 2,
            "previous_contract": "CLV25.NYM",
            "change_basis": "previous_daily_close",
        }
        document = {"owner": instrument, "key": key, "document": state}
        started = time.time() - 1
        self.store.upsert_documents("external_market_roll_state", [document])
        first = self.store.load_document("external_market_roll_state", instrument, key)
        self.store.upsert_documents("external_market_roll_state", [document])
        replay = self.store.load_document("external_market_roll_state", instrument, key)
        self.assertEqual(first, replay)
        self.assertEqual(state, replay["document"])

        collector = object.__new__(YahooDelayedMarketCollector)
        collector._store = self.store
        restored = collector._load_roll_state(instrument, "CLV26.NYM")
        self.assertEqual(state, restored)

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:external_market_roll_state" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:external_market_roll_state"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_stock_catalog_replay_preserves_market_news_reader_and_correlates_metrics(self) -> None:
        code = f"DIAG{uuid.uuid4().hex[:16]}"
        self.document_keys.append(("stock_catalog", "krx", code))
        document = {"owner": "krx", "key": code, "document": {
            "code": code, "name": "Diagnostic catalog row", "market": "TEST",
        }}
        started = time.time() - 1
        self.store.upsert_documents("stock_catalog", [document])
        first = self.store.load_document("stock_catalog", "krx", code)
        self.store.upsert_documents("stock_catalog", [document])
        replay = self.store.load_document("stock_catalog", "krx", code)
        self.assertEqual(first, replay)
        self.assertEqual(document["document"], replay["document"])

        reader = object.__new__(MarketFeedNewsCollector)
        reader._store = self.store
        self.assertIn((code, "Diagnostic catalog row"), reader._catalog())

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:stock_catalog" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:stock_catalog"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_top20_daily_entrants_replay_preserves_reader_and_correlates_metrics(self) -> None:
        day = f"diagnostic-day-{uuid.uuid4().hex}"
        code = f"DIAG{uuid.uuid4().hex[:16]}"
        self.document_keys.append(("top20_daily_entrants", day, code))
        document = {"owner": day, "key": code, "document": {
            "code": code, "first_seen_at": "snapshot-1", "last_seen_at": "snapshot-2",
        }}
        started = time.time() - 1
        self.store.upsert_documents("top20_daily_entrants", [document])
        first = self.store.load_documents("top20_daily_entrants", day, 5000)
        self.store.upsert_documents("top20_daily_entrants", [document])
        replay = self.store.load_documents("top20_daily_entrants", day, 5000)
        self.assertEqual(first, replay)
        self.assertEqual(1, len(replay))
        self.assertEqual((day, code, document["document"]),
                         (replay[0]["owner"], replay[0]["key"], replay[0]["document"]))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:top20_daily_entrants" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:top20_daily_entrants"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_historical_highs_replay_preserves_reader_and_correlates_metrics(self) -> None:
        code = f"DIAG{uuid.uuid4().hex[:16]}"
        self.document_keys.append(("historical_highs", code, "latest"))
        document = {"owner": code, "key": "latest", "document": {
            "checked_on": "2099-12-31",
            "target": {"high_250": 123456, "source": "diagnostic"},
        }}
        started = time.time() - 1
        self.store.upsert_documents("historical_highs", [document])
        first = self.store.load_documents("historical_highs", code, 1)
        self.store.upsert_documents("historical_highs", [document])
        replay = self.store.load_documents("historical_highs", code, 1)
        self.assertEqual(first, replay)
        self.assertEqual(document["document"], replay[0]["document"])

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:historical_highs" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:historical_highs"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_market_state_dataset_replay_and_failure_preserve_reader_and_correlate_metrics(self) -> None:
        subject = f"DIAG-{uuid.uuid4().hex}:KRX"
        snapshot_key = "2026-09-28T09:30:00+09:00"
        failed_key = "2026-09-28T09:31:00+09:00"
        broken_key = None
        self.dataset_snapshot_keys.extend((
            ("market_state", subject, snapshot_key),
            ("market_state", subject, failed_key),
        ))
        payload = {"subject": subject, "value": {"price": 12345}}
        observation = market_state_observation(
            subject, snapshot_key, payload, datetime(2026, 9, 28, 9, 30, tzinfo=KST),
        )
        started = time.time() - 1
        write = ("market_state", subject, snapshot_key, payload, observation)
        self.store.save_dataset_snapshots([write])
        before_replay = self.store.load_dataset_snapshots("market_state", subject, 10)
        self.store.save_dataset_snapshots([write])
        after_replay = self.store.load_dataset_snapshots("market_state", subject, 10)
        self.assertEqual(
            [(row["subject"], row["snapshot_key"], row["payload"]) for row in before_replay],
            [(row["subject"], row["snapshot_key"], row["payload"]) for row in after_replay],
        )
        self.assertEqual(1, len(after_replay))
        self.assertEqual(payload, after_replay[0]["payload"])
        self.assertGreater(after_replay[0]["saved_at"], before_replay[0]["saved_at"])

        failed_observation = market_state_observation(
            subject, failed_key, payload, datetime(2026, 9, 28, 9, 31, tzinfo=KST),
        )
        with self.assertRaises(Exception) as raised:
            self.store.save_dataset_snapshots([
                ("market_state", subject, failed_key, payload, failed_observation),
                ("market_state", subject, broken_key, payload, None),  # type: ignore[arg-type]
            ])
        self.assertEqual("NotNullViolation", type(raised.exception).__name__)
        self.assertEqual(1, len(self.store.load_dataset_snapshots("market_state", subject, 10)))
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM central_market_data_observation_meta "
                "WHERE dataset_kind=%s AND subject=%s AND observation_key=ANY(%s)",
                ("market_state", subject, [snapshot_key, failed_key]),
            )
            self.assertEqual(1, cursor.fetchone()[0])

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "dataset.snapshot"
            and call["writer_kind"] == "dataset:market_state"
        ]
        self.assertEqual(3, len(calls))
        self.assertEqual(["committed", "committed", "rolled_back"],
                         [call["outcome"] for call in calls])
        self.assertEqual([1, 1, 0], [call["commits"] for call in calls])
        self.assertEqual([0, 0, 1], [call["rollbacks"] for call in calls])
        self.assertEqual([3, 3, 4], [call["sql_calls"] for call in calls])
        self.assertTrue(all(
            call["operation"] == "save_dataset_snapshots"
            and call["rows_attempted"] in (1, 2)
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["dataset:market_state"]["call_samples"]
        self.assertEqual(2, len(samples))
        self.assertTrue({call["call_id"] for call in calls if call["outcome"] == "committed"}
                        .issubset({sample["db_call_id"] for sample in samples}))

    def test_new_high_and_program_flow_dataset_replay_preserve_readers_and_correlate_metrics(self) -> None:
        started = time.time() - 1
        for kind in ("new_high", "program_flow"):
            with self.subTest(kind=kind):
                subject = f"DIAG-{uuid.uuid4().hex}"
                snapshot_key = "2026-09-28T09:30:00+09:00"
                failed_key = "2026-09-28T09:31:00+09:00"
                self.dataset_snapshot_keys.extend((
                    (kind, subject, snapshot_key), (kind, subject, failed_key),
                ))
                payload = {"kind": kind, "subject": subject, "values": [1, 2, 3]}
                write = (kind, subject, snapshot_key, payload, None)
                self.store.save_dataset_snapshots([write])
                before_replay = self.store.load_dataset_snapshots(kind, subject, 10)
                self.store.save_dataset_snapshots([write])
                after_replay = self.store.load_dataset_snapshots(kind, subject, 10)
                self.assertEqual(1, len(after_replay))
                self.assertEqual(
                    [(row["subject"], row["snapshot_key"], row["payload"]) for row in before_replay],
                    [(row["subject"], row["snapshot_key"], row["payload"]) for row in after_replay],
                )
                self.assertGreater(after_replay[0]["saved_at"], before_replay[0]["saved_at"])

                with self.assertRaises(Exception) as raised:
                    self.store.save_dataset_snapshots([
                        (kind, subject, failed_key, payload, None),
                        (kind, subject, None, payload, None),  # type: ignore[arg-type]
                    ])
                self.assertEqual("NotNullViolation", type(raised.exception).__name__)
                self.assertEqual(1, len(self.store.load_dataset_snapshots(kind, subject, 10)))
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT COUNT(*) FROM central_dataset_snapshots "
                        "WHERE kind=%s AND subject=%s AND snapshot_key=%s",
                        (kind, subject, failed_key),
                    )
                    self.assertEqual(0, cursor.fetchone()[0])

                calls = [
                    call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
                    if call["writer_family"] == "dataset.snapshot"
                    and call["writer_kind"] == f"dataset:{kind}"
                ]
                self.assertEqual(3, len(calls))
                self.assertEqual(["committed", "committed", "rolled_back"],
                                 [call["outcome"] for call in calls])
                self.assertEqual([2, 2, 3], [call["sql_calls"] for call in calls])
                self.assertEqual([1, 1, 0], [call["commits"] for call in calls])
                self.assertEqual([0, 0, 1], [call["rollbacks"] for call in calls])
                self.assertTrue(all(call["backend_pid"] is not None for call in calls))
                legacy = summarize_market_bar_saves(started, time.time() + 1)
                samples = legacy["writer_transactions"][f"dataset:{kind}"]["call_samples"]
                self.assertEqual(2, len(samples))
                self.assertTrue(
                    {call["call_id"] for call in calls if call["outcome"] == "committed"}
                    .issubset({sample["db_call_id"] for sample in samples})
                )

    def _assert_research_dataset_observed(
        self, kind: str, subject: str, snapshot_key: str, source: str,
        first_payload: dict[str, object], revised_payload: dict[str, object],
        failed_payload: dict[str, object],
    ) -> None:
        self.dataset_snapshot_keys.append((kind, subject, snapshot_key))
        self.dataset_snapshot_keys.append((kind, subject, f"failed-{snapshot_key}"))
        available_at = datetime.fromisoformat(snapshot_key)

        def observation(payload: dict[str, object], offset: int):
            return ranking_observation(
                subject, snapshot_key, payload,
                available_at + timedelta(seconds=offset), source=source,
            )

        def snapshot() -> list[dict[str, object]]:
            return [
                row for row in self.store.load_dataset_snapshots(kind, subject, 5000)
                if row["snapshot_key"] == snapshot_key
            ]

        def revisions() -> list[dict[str, object]]:
            return [
                row for row in self.store.load_observation_revisions(kind, subject, 5000)
                if row["observation_key"] == snapshot_key
            ]

        self.store.save_dataset_snapshot(
            kind, subject, snapshot_key, first_payload,
            observation=observation(first_payload, 0),
        )
        self.assertEqual(first_payload, snapshot()[0]["payload"])
        first_history = revisions()
        self.assertEqual(1, len(first_history))

        # A replay updates latest metadata availability but must not append a
        # duplicate immutable revision for the same payload/source.
        self.store.save_dataset_snapshot(
            kind, subject, snapshot_key, first_payload,
            observation=observation(first_payload, 1),
        )
        self.assertEqual(first_payload, snapshot()[0]["payload"])
        self.assertEqual(1, len(revisions()))

        self.store.save_dataset_snapshot(
            kind, subject, snapshot_key, revised_payload,
            observation=observation(revised_payload, 2),
        )
        self.assertEqual(revised_payload, snapshot()[0]["payload"])
        revised_history = revisions()
        self.assertEqual(2, len(revised_history))
        self.assertEqual(revised_payload, revised_history[0]["payload"])
        self.assertEqual(first_payload, revised_history[1]["payload"])
        self.assertEqual(revised_history[1]["revision_id"], revised_history[0]["revision_of"])

        metadata = self.store.load_market_data_metadata(
            MarketDatasetKind.CANDIDATE_SET, subject, snapshot_key,
        )
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(source, metadata.source)
        self.assertEqual(available_at + timedelta(seconds=2), metadata.available_at)

        with self.assertRaises(Exception) as raised:
            self.store.save_dataset_snapshots([
                (kind, subject, snapshot_key, failed_payload,
                 observation(failed_payload, 3)),
                (kind, subject, None, failed_payload, None),  # type: ignore[arg-type]
            ])
        self.assertEqual("NotNullViolation", type(raised.exception).__name__)
        self.assertEqual(revised_payload, snapshot()[0]["payload"])
        self.assertEqual(2, len(revisions()))
        metadata = self.store.load_market_data_metadata(
            MarketDatasetKind.CANDIDATE_SET, subject, snapshot_key,
        )
        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(available_at + timedelta(seconds=2), metadata.available_at)

    def test_ranking_and_top20_membership_revisions_and_metrics_are_preserved(self) -> None:
        snapshot_at = datetime(2099, 9, 28, 9, 30, tzinfo=KST) + timedelta(
            microseconds=int(uuid.uuid4().hex[:6], 16),
        )
        snapshot_key = snapshot_at.isoformat(timespec="microseconds")
        ranking_subject = "5"
        membership_subject = snapshot_key[:10]
        rank_a = {"query_type": "5", "items": [{"stk_cd": "005930", "rank": "1"}]}
        rank_b = {"query_type": "5", "items": [{"stk_cd": "000660", "rank": "1"}]}
        rank_c = {"query_type": "5", "items": [{"stk_cd": "035420", "rank": "1"}]}
        membership_a = {
            "observed_at": snapshot_key, "codes": ["005930"],
            "items": [{"stk_cd": "005930"}],
        }
        membership_b = {
            "observed_at": snapshot_key, "codes": ["000660"],
            "items": [{"stk_cd": "000660"}],
        }
        membership_c = {
            "observed_at": snapshot_key, "codes": ["035420"],
            "items": [{"stk_cd": "035420"}],
        }
        started = time.time() - 1
        self._assert_research_dataset_observed(
            "ranking", ranking_subject, snapshot_key, "kiwoom-ka00198",
            rank_a, rank_b, rank_c,
        )
        self._assert_research_dataset_observed(
            "top20_membership", membership_subject, snapshot_key,
            "nas-autonomous-ka00198", membership_a, membership_b, membership_c,
        )

        membership_history = [
            row for row in self.store.load_observation_revisions(
                "top20_membership", membership_subject, 5000,
            ) if row["observation_key"] == snapshot_key
        ]
        self.assertEqual(
            {"kind": "ranking", "subject": "5", "observation_key": snapshot_key},
            membership_history[0]["source_ref"],
        )
        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "dataset.snapshot"
            and call["writer_kind"] in {"dataset:ranking", "dataset:top20_membership"}
        ]
        self.assertEqual(8, len(calls))
        for kind in ("ranking", "top20_membership"):
            selected = [call for call in calls if call["writer_kind"] == f"dataset:{kind}"]
            self.assertEqual(4, len(selected))
            self.assertEqual(["committed"] * 3 + ["rolled_back"],
                             [call["outcome"] for call in selected])
            self.assertEqual([5, 4, 5, 6], [call["sql_calls"] for call in selected])
            self.assertEqual([1, 1, 1, 0], [call["commits"] for call in selected])
            self.assertEqual([0, 0, 0, 1], [call["rollbacks"] for call in selected])
            self.assertTrue(all(call["backend_pid"] is not None for call in selected))
            legacy = summarize_market_bar_saves(started, time.time() + 1)
            samples = legacy["writer_transactions"][f"dataset:{kind}"]["call_samples"]
            self.assertEqual(3, len(samples))
            self.assertTrue(
                {call["call_id"] for call in selected if call["outcome"] == "committed"}
                .issubset({sample["db_call_id"] for sample in samples})
            )

    def test_top20_and_market_index_writes_invalidate_statistics_atomically(self) -> None:
        token = uuid.uuid4().hex
        day = f"{1700 + int(token[:4], 16) % 200:04d}-{1 + int(token[4:6], 16) % 12:02d}-{1 + int(token[6:8], 16) % 28:02d}"
        raw_day = day.replace("-", "")
        minute = f"{day}T15:30:00+09:00"
        market_subject = f"{raw_day}:kospi"
        top20_payload = {
            "minute": minute, "market_values": [10.0, 5.0, 0.0],
            "capture_state": "realtime_complete",
        }
        market_payload = {"daily": [{"dt": raw_day, "trde_prica": "123400"}]}
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM central_dataset_snapshots WHERE "
                "(kind='top20_index' AND subject=%s) OR "
                "(kind='market_index_chart' AND subject=%s) OR "
                "(kind='top20_statistics_day' AND subject=%s)",
                (day, market_subject, day),
            )
            self.assertEqual(0, cursor.fetchone()[0], "diagnostic day already occupied")
        self.dataset_snapshot_keys.extend((
            ("top20_index", day, minute),
            ("market_index_chart", market_subject, raw_day),
            ("top20_statistics_day", day, day),
        ))
        observation = top20_index_observation(
            day, minute, top20_payload, datetime.now(KST), "realtime_complete",
        )
        started = time.time() - 1
        self.store.save_dataset_snapshot(
            "top20_index", day, minute, top20_payload, observation=observation,
        )
        initial = self.store.load_top20_statistics(day, day)
        self.assertEqual(15.0, initial["comparisons"][0]["top20_eok"])
        self.assertEqual(1, len(self.store.load_dataset_snapshots("top20_statistics_day", day)))

        self.store.save_dataset_snapshot(
            "top20_index", day, minute, top20_payload, observation=observation,
        )
        self.assertEqual([], self.store.load_dataset_snapshots("top20_statistics_day", day))
        self.assertEqual(initial, self.store.load_top20_statistics(day, day))
        metadata_before_failure = self.store.load_market_data_metadata(
            MarketDatasetKind.TOP20_INDEX, day, minute,
        )
        self.assertIsNotNone(metadata_before_failure)

        self.store.save_dataset_snapshot(
            "market_index_chart", market_subject, raw_day, market_payload,
        )
        self.assertEqual([], self.store.load_dataset_snapshots("top20_statistics_day", day))
        with_market = self.store.load_top20_statistics(day, day)
        self.assertEqual(1234.0, with_market["comparisons"][0]["kospi_eok"])
        self.store.save_dataset_snapshot(
            "market_index_chart", market_subject, raw_day, market_payload,
        )
        self.assertEqual([], self.store.load_dataset_snapshots("top20_statistics_day", day))
        self.assertEqual(with_market, self.store.load_top20_statistics(day, day))
        cached = self.store.load_dataset_snapshots("top20_statistics_day", day)
        self.assertEqual(1, len(cached))

        changed_top20 = {**top20_payload, "market_values": [99.0, 0.0, 0.0]}
        with self.assertRaises(Exception) as top20_error:
            self.store.save_dataset_snapshots([
                ("top20_index", day, minute, changed_top20,
                 top20_index_observation(
                     day, minute, changed_top20, datetime.now(KST), "realtime_complete",
                 )),
                ("top20_index", day, None, changed_top20, None),  # type: ignore[arg-type]
            ])
        self.assertEqual("NotNullViolation", type(top20_error.exception).__name__)
        self.assertEqual(top20_payload, self.store.load_dataset_snapshots("top20_index", day)[0]["payload"])
        self.assertEqual(
            metadata_before_failure,
            self.store.load_market_data_metadata(MarketDatasetKind.TOP20_INDEX, day, minute),
        )
        self.assertEqual(cached, self.store.load_dataset_snapshots("top20_statistics_day", day))

        changed_market = {"daily": [{"dt": raw_day, "trde_prica": "999900"}]}
        with self.assertRaises(Exception) as market_error:
            self.store.save_dataset_snapshots([
                ("market_index_chart", market_subject, raw_day, changed_market, None),
                ("market_index_chart", market_subject, None, changed_market, None),  # type: ignore[arg-type]
            ])
        self.assertEqual("NotNullViolation", type(market_error.exception).__name__)
        self.assertEqual(
            market_payload,
            self.store.load_dataset_snapshots("market_index_chart", market_subject)[0]["payload"],
        )
        self.assertEqual(cached, self.store.load_dataset_snapshots("top20_statistics_day", day))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "dataset.snapshot"
            and call["writer_kind"] in {"dataset:top20_index", "dataset:market_index_chart"}
        ]
        self.assertEqual(6, len(calls))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        for kind, expected_sql in (("top20_index", [4, 4, 5]),
                                   ("market_index_chart", [3, 3, 4])):
            selected = [call for call in calls if call["writer_kind"] == f"dataset:{kind}"]
            self.assertEqual(["committed", "committed", "rolled_back"],
                             [call["outcome"] for call in selected])
            self.assertEqual(expected_sql, [call["sql_calls"] for call in selected])
            self.assertEqual([1, 1, 0], [call["commits"] for call in selected])
            self.assertEqual([0, 0, 1], [call["rollbacks"] for call in selected])
            self.assertEqual([1, 1, 2], [call["rows_attempted"] for call in selected])
            self.assertTrue(all(call["backend_pid"] is not None for call in selected))
            samples = legacy["writer_transactions"][f"dataset:{kind}"]["call_samples"]
            self.assertEqual(2, len(samples))
            self.assertTrue(
                {call["call_id"] for call in selected if call["outcome"] == "committed"}
                .issubset({sample["db_call_id"] for sample in samples})
            )

    def test_investor_fundamental_and_nxt_dataset_writes_preserve_reader_boundaries(self) -> None:
        token = uuid.uuid4().hex
        code = f"DIAG{token[:12]}"
        now = datetime(2026, 9, 28, 10, 15, tzinfo=KST)
        day = now.date().isoformat()
        ingestor = MarketDataIngestor(self.store, now_provider=lambda: now)
        payloads = {
            "investor_flow": {"stk_orgn_trde_trnsn": [{"dt": "20260928", "qty": "17"}]},
            "stock_fundamentals": {"stk_nm": "diagnostic", "mac": "123456"},
            "nxt_eligibility": {"nxtEnable": "Y"},
        }
        sources = {
            "investor_flow": ("ka10045", {"stk_cd": f"{code}_AL", "end_dt": "20260928"}),
            "stock_fundamentals": ("ka10001", {"stk_cd": f"{code}_AL"}),
            "nxt_eligibility": ("ka10100", {"stk_cd": f"{code}_AL"}),
        }
        for collection in ("stock_fundamentals", "stock_nxt_eligibility"):
            self.document_keys.append((collection, code, "latest"))
        dataset_keys = {
            "investor_flow": (code, "20260928:SOR"),
            "stock_fundamentals": (code, f"{day}:SOR"),
            "nxt_eligibility": (code, day),
        }
        self.dataset_snapshot_keys.extend(
            (kind, subject, key)
            for kind, (subject, key) in dataset_keys.items()
        )
        self.dataset_snapshot_keys.extend(
            (kind, subject, f"failed-{key}")
            for kind, (subject, key) in dataset_keys.items()
        )
        started = time.time() - 1
        for kind, (api_id, body) in sources.items():
            ingestor.ingest(api_id, body, payloads[kind])
            first = self.store.load_dataset_snapshots(kind, code, 10)
            ingestor.ingest(api_id, body, payloads[kind])
            replay = self.store.load_dataset_snapshots(kind, code, 10)
            expected_key = dataset_keys[kind][1]
            self.assertEqual(1, len(replay))
            self.assertEqual(expected_key, replay[0]["snapshot_key"])
            self.assertEqual(first[0]["payload"], replay[0]["payload"])
            self.assertGreater(replay[0]["saved_at"], first[0]["saved_at"])

            with self.assertRaises(Exception) as raised:
                self.store.save_dataset_snapshots([
                    (kind, code, f"failed-{expected_key}", {"temporary": True}, None),
                    (kind, code, None, {"invalid": True}, None),  # type: ignore[arg-type]
                ])
            self.assertEqual("NotNullViolation", type(raised.exception).__name__)
            after_failure = self.store.load_dataset_snapshots(kind, code, 10)
            self.assertEqual(1, len(after_failure))
            self.assertEqual(replay[0]["payload"], after_failure[0]["payload"])

        fundamentals = self.store.load_documents("stock_fundamentals", code, 1)
        nxt = self.store.load_documents("stock_nxt_eligibility", code, 1)
        self.assertEqual("123456", fundamentals[0]["document"]["payload"]["mac"])
        self.assertTrue(nxt[0]["document"]["enabled"])

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "dataset.snapshot"
            and call["writer_kind"] in {
                "dataset:investor_flow", "dataset:stock_fundamentals", "dataset:nxt_eligibility",
            }
        ]
        self.assertEqual(9, len(calls))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        for kind in dataset_keys:
            selected = [call for call in calls if call["writer_kind"] == f"dataset:{kind}"]
            self.assertEqual(["committed", "committed", "rolled_back"],
                             [call["outcome"] for call in selected])
            self.assertEqual([1, 1, 2], [call["sql_calls"] for call in selected])
            self.assertEqual([1, 1, 0], [call["commits"] for call in selected])
            self.assertEqual([0, 0, 1], [call["rollbacks"] for call in selected])
            self.assertEqual([1, 1, 2], [call["rows_attempted"] for call in selected])
            self.assertTrue(all(call["backend_pid"] is not None for call in selected))
            samples = legacy["writer_transactions"][f"dataset:{kind}"]["call_samples"]
            self.assertEqual(2, len(samples))
            self.assertTrue(
                {call["call_id"] for call in selected if call["outcome"] == "committed"}
                .issubset({sample["db_call_id"] for sample in samples})
            )

        document_calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "document.collection"
            and call["writer_kind"] in {
                "document:stock_fundamentals", "document:stock_nxt_eligibility",
            }
        ]
        self.assertEqual(4, len(document_calls))
        self.assertTrue(all(call["outcome"] == "committed" and call["commits"] == 1
                            for call in document_calls))

    def test_top20_statistics_cache_preserves_warm_cold_and_concurrent_reads(self) -> None:
        token = uuid.uuid4().hex
        day = f"{1700 + int(token[:4], 16) % 200:04d}-{1 + int(token[4:6], 16) % 12:02d}-{1 + int(token[6:8], 16) % 28:02d}"
        raw_day = day.replace("-", "")
        minute = f"{day}T15:30:00+09:00"
        market_subject = f"{raw_day}:kospi"
        self.dataset_snapshot_keys.extend((
            ("top20_index", day, minute),
            ("market_index_chart", market_subject, raw_day),
            ("top20_statistics_day", day, day),
        ))
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM central_dataset_snapshots WHERE "
                "(kind='top20_index' AND subject=%s) OR "
                "(kind='market_index_chart' AND subject=%s) OR "
                "(kind='top20_statistics_day' AND subject=%s)",
                (day, market_subject, day),
            )
            self.assertEqual(0, cursor.fetchone()[0], "diagnostic day already occupied")
        self.store.save_dataset_snapshot("top20_index", day, minute, {
            "minute": minute, "market_values": [10.0, 5.0, 0.0],
            "capture_state": "realtime_complete",
        })
        self.store.save_dataset_snapshot(
            "market_index_chart", market_subject, raw_day,
            {"daily": [{"dt": raw_day, "trde_prica": "123400"}]},
        )

        started = time.time() - 1
        cold = self.store.load_top20_statistics(day, day)
        warm = self.store.load_top20_statistics(day, day)
        self.assertEqual(cold, warm)
        self.assertEqual(15.0, cold["comparisons"][0]["top20_eok"])
        self.assertEqual(1234.0, cold["comparisons"][0]["kospi_eok"])
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM central_dataset_snapshots "
                "WHERE kind='top20_statistics_day' AND subject=%s",
                (day,),
            )

        barrier = Barrier(3)

        def load_after_barrier() -> dict[str, object]:
            barrier.wait(timeout=5)
            return self.store.load_top20_statistics(day, day)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(load_after_barrier) for _ in range(2)]
            barrier.wait(timeout=5)
            concurrent = [future.result(timeout=10) for future in futures]
        self.assertEqual([cold, cold], concurrent)
        cached = self.store.load_dataset_snapshots("top20_statistics_day", day, 10)
        self.assertEqual(1, len(cached))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "dataset.statistics_cache"
            and call["writer_kind"] == "dataset:top20_statistics_day"
        ]
        self.assertEqual(4, len(calls))
        self.assertTrue(all(
            call["operation"] == "load_top20_statistics"
            and call["outcome"] == "committed"
            and call["commits"] == 1
            and call["rollbacks"] == 0
            and call["rows_attempted"] is None
            and call["backend_pid"] is not None
            for call in calls
        ))
        sql_counts = [call["sql_calls"] for call in calls]
        self.assertGreaterEqual(sql_counts.count(5), 2)
        self.assertGreaterEqual(sql_counts.count(1), 1)
        self.assertTrue(all(count in {1, 5} for count in sql_counts))

    def test_market_index_chart_coverage_replay_preserves_reader_and_correlates_metrics(self) -> None:
        day = f"diagnostic-day-{uuid.uuid4().hex}"
        self.document_keys.append(("market_index_chart_coverage", day, "complete"))
        document = {"owner": day, "key": "complete", "document": {
            "as_of": day, "markets": ["kospi", "kosdaq"],
            "completed_at": datetime.now(KST).isoformat(),
        }}
        started = time.time() - 1
        self.store.upsert_documents("market_index_chart_coverage", [document])
        first = self.store.load_documents("market_index_chart_coverage", day, 1)
        self.store.upsert_documents("market_index_chart_coverage", [document])
        replay = self.store.load_documents("market_index_chart_coverage", day, 1)
        self.assertEqual(first, replay)
        self.assertEqual(document["document"], replay[0]["document"])
        service = object.__new__(AutonomousTop20Service)
        service._store = self.store
        asyncio.run(service._backfill_market_indexes(day))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:market_index_chart_coverage" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:market_index_chart_coverage"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_daily_market_data_coverage_replay_preserves_reader_and_correlates_metrics(self) -> None:
        day = f"diagnostic-day-{uuid.uuid4().hex}"
        owner = f"DIAG{uuid.uuid4().hex[:16]}:KRX"
        self.document_keys.append(("market_data_coverage_daily", owner, "complete"))
        document = {"owner": owner, "key": "complete", "document": {
            "kind": "daily", "scope": "full_day", "as_of": day,
            "window_closed": True, "session_finalized": True,
            "rows": 1, "completed_at": datetime.now(KST).isoformat(),
        }}
        started = time.time() - 1
        self.store.upsert_documents("market_data_coverage_daily", [document])
        first = self.store.load_documents("market_data_coverage_daily", owner, 1)
        self.store.upsert_documents("market_data_coverage_daily", [document])
        replay = self.store.load_documents("market_data_coverage_daily", owner, 1)
        self.assertEqual(first, replay)
        self.assertEqual(document["document"], replay[0]["document"])
        service = object.__new__(AutonomousTop20Service)
        service._store = self.store
        service._ensure_daily_history = AsyncMock(return_value={"expected_count": 1})
        asyncio.run(service._backfill_daily(owner.split(":", 1)[0], day, "KRX"))
        service._ensure_daily_history.assert_awaited_once_with(
            owner.split(":", 1)[0], day, "KRX", scope="final",
        )

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:market_data_coverage_daily" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:market_data_coverage_daily"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_minute_market_data_coverage_replay_preserves_reader_and_correlates_metrics(self) -> None:
        day = "2026-09-28"
        code = f"DIAG{uuid.uuid4().hex[:16]}"
        market = "KRX"
        owner = f"{day}:{code}:{market}"
        self.document_keys.append(("market_data_coverage", owner, "complete"))
        document = {"owner": owner, "key": "complete", "document": {
            "kind": "minute", "pages": 2, "scope": "full_day",
            "window_closed": True, "session_finalized": True,
            "as_of": day, "completed_at": datetime.now(KST).isoformat(),
        }}
        started = time.time() - 1
        self.store.upsert_documents("market_data_coverage", [document])
        first = self.store.load_documents("market_data_coverage", owner, 1)
        self.store.upsert_documents("market_data_coverage", [document])
        replay = self.store.load_documents("market_data_coverage", owner, 1)
        self.assertEqual(first, replay)
        self.assertEqual(document["document"], replay[0]["document"])

        service = object.__new__(AutonomousTop20Service)
        service._store = self.store
        service._minute_backfill_enabled = True
        with patch.object(self.store, "load_minute_bars", return_value=[{"minute": "09:00"}]) as load_bars:
            asyncio.run(service._backfill_minutes(code, day, market))
        load_bars.assert_called_once_with(code, day, market)

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:market_data_coverage" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:market_data_coverage"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_intraday_market_data_coverage_replay_preserves_reader_and_correlates_metrics(self) -> None:
        day = "2026-09-28"
        code = f"DIAG{uuid.uuid4().hex[:16]}"
        owner = f"{day}:{code}:KRX"
        self.document_keys.append(("market_data_coverage_intraday", owner, "entry_backfill"))
        document = {"owner": owner, "key": "entry_backfill", "document": {
            "kind": "minute", "scope": "through_entry", "pages": 1,
            "as_of": day, "completed_at": datetime.now(KST).isoformat(),
        }}
        started = time.time() - 1
        self.store.upsert_documents("market_data_coverage_intraday", [document])
        first = self.store.load_documents("market_data_coverage_intraday", owner, 1)
        self.store.upsert_documents("market_data_coverage_intraday", [document])
        replay = self.store.load_documents("market_data_coverage_intraday", owner, 1)
        self.assertEqual(first, replay)
        self.assertEqual(document["document"], replay[0]["document"])

        service = object.__new__(AutonomousTop20Service)
        service._store = self.store
        service._minute_backfill_enabled = True
        service._now = lambda: datetime(2026, 9, 28, 9, 0, tzinfo=KST)

        async def nxt_disabled(_code: str) -> bool:
            return False

        service._nxt_enabled = nxt_disabled
        asyncio.run(service._backfill_entry_minutes(code, day))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:market_data_coverage_intraday" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:market_data_coverage_intraday"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_candidate_flow_capture_replay_preserves_reader_and_correlates_metrics(self) -> None:
        day = "2026-09-28"
        code = f"DIAG{uuid.uuid4().hex[:16]}"
        owner = f"{day}:{code}"
        self.document_keys.append(("candidate_flow_capture", owner, "initial"))
        document = {"owner": owner, "key": "initial", "document": {
            "as_of": day, "captured_at": datetime.now(KST).isoformat(),
            "scope": "candidate_first_seen",
        }}
        started = time.time() - 1
        self.store.upsert_documents("candidate_flow_capture", [document])
        first = self.store.load_documents("candidate_flow_capture", owner, 1)
        self.store.upsert_documents("candidate_flow_capture", [document])
        replay = self.store.load_documents("candidate_flow_capture", owner, 1)
        self.assertEqual(first, replay)
        self.assertEqual(document["document"], replay[0]["document"])

        service = object.__new__(AutonomousTop20Service)
        service._store = self.store
        asyncio.run(service._capture_candidate_investor_flow(code, day))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:candidate_flow_capture" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:candidate_flow_capture"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_candidate_flow_finalization_replay_preserves_reader_and_correlates_metrics(self) -> None:
        day = "2026-09-28"
        code = f"DIAG{uuid.uuid4().hex[:16]}"
        owner = f"{day}:{code}"
        self.document_keys.append(("candidate_flow_finalization", owner, "complete"))
        document = {"owner": owner, "key": "complete", "document": {
            "as_of": day, "completed_at": datetime.now(KST).isoformat(),
            "scope": "candidate_after_close",
        }}
        started = time.time() - 1
        self.store.upsert_documents("candidate_flow_finalization", [document])
        first = self.store.load_documents("candidate_flow_finalization", owner, 1)
        self.store.upsert_documents("candidate_flow_finalization", [document])
        replay = self.store.load_documents("candidate_flow_finalization", owner, 1)
        self.assertEqual(first, replay)
        self.assertEqual(document["document"], replay[0]["document"])

        service = object.__new__(AutonomousTop20Service)
        service._store = self.store
        asyncio.run(service._backfill_candidate_flows(code, day))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:candidate_flow_finalization" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:candidate_flow_finalization"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_condition_search_status_replay_preserves_reader_and_correlates_metrics(self) -> None:
        class WebSocket:
            def __init__(self) -> None:
                self.messages: list[str] = []

            async def send(self, message: str) -> None:
                self.messages.append(message)

        started = time.time() - 1
        service = MarketEventService(
            None, None, self.store, condition_substring="15%",
            now_provider=lambda: datetime(2026, 9, 28, 10, 0, tzinfo=KST),
        )
        service._list_policy = (0, "", "15%")
        websocket = WebSocket()
        asyncio.run(service._condition_list({
            "return_code": 0,
            "data": [["17", "TOP 15% value"]],
        }, websocket))
        first = self.store.load_documents("condition_search_status", "hot_cohort", 1)
        self.assertEqual("UNIQUE_SUBSTRING", first[0]["document"]["status"])
        self.assertEqual(["17", "TOP 15% value"], first[0]["document"]["selected"])
        self.assertEqual(1, len(websocket.messages))

        value = {"owner": "hot_cohort", "key": "current", "document": first[0]["document"]}
        self.store.upsert_documents("condition_search_status", [value])
        replay = self.store.load_documents("condition_search_status", "hot_cohort", 1)
        self.assertEqual(first, replay)
        self.assertEqual(value["document"], replay[0]["document"])

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:condition_search_status" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:condition_search_status"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_market_event_session_markers_preserve_writer_boundaries_and_correlate_metrics(self) -> None:
        session_id = f"diagnostic-{uuid.uuid4().hex}"
        self.document_keys.append(("market_event_sessions", "krx", session_id))
        service = MarketEventService(
            None, None, self.store,
            now_provider=lambda: datetime(2026, 9, 28, 15, 30, tzinfo=KST),
        )
        started = time.time() - 1

        async def close_session_phases() -> None:
            service.mark_krx_session_observed(session_id)
            pending = tuple(service._background)
            if pending:
                await asyncio.gather(*pending)
            await service.close_krx_regular_session(session_id)
            await service.close_observation_day(session_id)

        asyncio.run(close_session_phases())
        first = self.store.load_documents("market_event_sessions", "krx", 1)
        self.assertEqual(session_id, first[0]["document"]["session_id"])
        self.assertTrue(first[0]["document"]["observed"])
        self.assertIn("full_day_closed_at", first[0]["document"])

        value = {"owner": "krx", "key": session_id, "document": first[0]["document"]}
        self.store.upsert_documents("market_event_sessions", [value])
        replay = self.store.load_documents("market_event_sessions", "krx", 1)
        self.assertEqual(first, replay)

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:market_event_sessions" and call["access_mode"] == "write"
        ]
        self.assertEqual(4, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:market_event_sessions"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_market_event_revision_batch_preserves_replay_projection_and_rollback(self) -> None:
        token = uuid.uuid4().hex
        code = f"diagnostic-{token}"
        self.market_event_codes.append(code)
        now = time.time()
        vi = {
            "event_id": f"vi-{token}", "event_key": f"vi-key-{token}",
            "stock_code": code, "event_kind": "trigger", "vi_type": "dynamic",
            "effective_at": "2026-09-28T09:30:00+09:00", "received_at": now,
            "available_at": now, "price": 1000,
        }
        cohort = {
            "revision_id": f"cohort-{token}", "revision_key": f"cohort-key-{token}",
            "stock_code": code, "event_type": "entry", "session_id": "2026-09-28",
            "effective_at": now, "available_at": now,
        }
        current = {
            "stock_code": code, "stock_name": "diagnostic", "condition_name": "test",
            "first_seen_at": now, "entry_session": "2026-09-28",
            "last_signal": "entry", "last_signal_at": now, "active": True,
        }
        upper = {
            "fact_id": f"upper-{token}", "fact_key": f"upper-key-{token}",
            "stock_code": code, "session_id": "2026-09-28", "status": "confirmed",
            "upper_limit_price": 1300, "current_price": 1300, "high_price": 1300,
            "effective_at": now, "available_at": now,
        }
        started = time.time() - 1
        self.assertEqual(1, self.store.append_vi_events([vi]))
        self.assertEqual(0, self.store.append_vi_events([vi]))
        self.assertTrue(self.store.record_hot_cohort_revision(cohort, current))
        self.assertFalse(self.store.record_hot_cohort_revision(cohort, current))
        self.assertEqual(1, self.store.append_upper_limit_facts([upper]))
        self.assertEqual(0, self.store.append_upper_limit_facts([upper]))

        failed = dict(cohort, revision_id=f"failed-{token}",
                      revision_key=f"failed-key-{token}")
        broken_current = dict(current)
        del broken_current["first_seen_at"]
        with self.assertRaises(KeyError):
            self.store.record_hot_cohort_revision(failed, broken_current)

        self.assertEqual([vi["event_id"]], [row["event_id"] for row in
                         self.store.load_market_event_history("vi", code=code)])
        self.assertEqual([cohort["revision_id"]], [row["revision_id"] for row in
                         self.store.load_market_event_history("cohort", code=code)])
        self.assertEqual([upper["fact_id"]], [row["fact_id"] for row in
                         self.store.load_market_event_history("upper_limit", code=code)])
        self.assertEqual([code], [row["stock_code"] for row in
                         self.store.load_hot_cohort(active_only=True)
                         if row["stock_code"] == code])

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "market_event.revision"
        ]
        self.assertEqual(7, len(calls))
        self.assertEqual(7, len({call["call_id"] for call in calls}))
        self.assertEqual({"market_event:vi": 2, "market_event:hot_cohort": 3,
                          "market_event:upper_limit": 2},
                         {kind: sum(call["writer_kind"] == kind for call in calls)
                          for kind in {call["writer_kind"] for call in calls}})
        self.assertTrue(all(call["backend_pid"] is not None
                            and call["transactions"] == 1 for call in calls))
        self.assertEqual(6, sum(call["commits"] for call in calls))
        self.assertEqual(1, sum(call["rollbacks"] for call in calls))
        self.assertEqual(10, sum(call["rows_attempted"] for call in calls))
        self.assertEqual(9, sum(call["sql_calls"] for call in calls))
        failed_call = next(call for call in calls if call["outcome"] == "rolled_back")
        self.assertEqual(("market_event:hot_cohort", 2, 1, 0, 1), (
            failed_call["writer_kind"], failed_call["rows_attempted"],
            failed_call["sql_calls"], failed_call["commits"], failed_call["rollbacks"],
        ))
        self.assertIn({"stage": "body", "exception_type": "KeyError"}, failed_call["errors"])
        reader_calls = [call for call in summarize_db_calls(
            started, time.time() + 1, mode="raw",
        )["calls"] if call["access_mode"] == "read"]
        self.assertEqual({"history:vi": 1, "history:cohort": 1,
                          "history:upper_limit": 1, "hot_cohort": 1}, {
            kind: sum(call["writer_kind"] == kind for call in reader_calls)
            for kind in {call["writer_kind"] for call in reader_calls}
        })
        self.assertTrue(all(call["writer_family"] == "read.market_events"
                            and call["backend_pid"] is not None
                            and call["transactions"] == 1
                            and call["commits"] == 1 for call in reader_calls))

    def test_realtime_snapshot_batch_failure_rolls_back_and_keeps_peer(self) -> None:
        token = uuid.uuid4().hex
        code, invalid_code, peer_code = [f"diagnostic-{token}-{suffix}"
                                          for suffix in ("latest", "invalid", "peer")]
        self.realtime_codes.extend((code, invalid_code, peer_code))
        received_at = time.time()

        def snapshot(item_key: str, market_cap: float) -> dict[str, object]:
            return {
                "event_type": "trade", "item_key": item_key, "received_at": received_at,
                "event": {"type": "trade", "payload": {
                    "code": item_key, "current_price": 100, "market_cap_eok": market_cap,
                }},
            }

        original = snapshot(code, 1000)
        peer = snapshot(peer_code, 2000)
        self.store.save_realtime_snapshots([original])
        self.store.save_realtime_snapshots([peer])
        revised = snapshot(code, 1500)
        # json.dumps emits NaN, which PostgreSQL JSONB rejects during batch DML.
        invalid = snapshot(invalid_code, float("nan"))
        started = time.time()
        with self.assertRaises(psycopg.errors.InvalidTextRepresentation):
            self.store.save_realtime_snapshots([revised, invalid])

        persisted = [event for event in self.store.load_realtime_snapshots([code, peer_code])
                     if event.get("payload", {}).get("code") in (code, peer_code)]
        self.assertEqual({code: original["event"], peer_code: peer["event"]},
                         {event["payload"]["code"]: event for event in persisted})
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM central_realtime_latest WHERE item_key=%s",
                           (invalid_code,))
            self.assertEqual(0, cursor.fetchone()[0])
        failed_calls = [call for call in summarize_db_calls(
            started, time.time(), mode="raw",
        )["calls"] if call["writer_family"] == "realtime.latest"
                       and call["outcome"] == "rolled_back"]
        self.assertEqual(1, len(failed_calls))
        failed = failed_calls[0]
        # The invalid JSON fails on the first SQL statement, so the observer
        # deliberately reports transaction count as unavailable. The rollback
        # count still confirms that the caller-owned transaction was rolled back.
        self.assertEqual((2, None, 0, 1), (
            failed["rows_attempted"], failed["transactions"],
            failed["commits"], failed["rollbacks"],
        ))

        self.store.save_realtime_snapshots([revised, snapshot(invalid_code, 3000)])
        caps = self.store.load_latest_market_caps([code, invalid_code, peer_code])
        self.assertEqual({code: 1500, invalid_code: 3000, peer_code: 2000},
                         {row["code"]: row["market_cap_eok"] for row in caps})

    def test_realtime_writer_batch_preserves_replay_lineage_and_independent_commits(self) -> None:
        token = uuid.uuid4().hex
        code = f"diagnostic-{token}"
        self.realtime_codes.append(code)
        first_operation = f"realtime-first-{token}"
        second_operation = f"realtime-second-{token}"
        invalid_operation = f"realtime-invalid-{token}"
        close_operation = f"realtime-close-{token}"
        self.realtime_operation_ids.extend((first_operation, second_operation,
                                            invalid_operation, close_operation))
        observed_at = datetime(2026, 9, 28, 10, 0, 10, tzinfo=KST)
        first = {
            "trading_date": "2026-09-28", "minute": "10:00", "code": code,
            "market": "KRX", "open": 100, "high": 100, "low": 100,
            "close": 100, "volume": 3, "trade_value_million_won": 3,
            "updated_at": observed_at.timestamp(), "operation_id": first_operation,
        }
        second = dict(first, high=105, low=105, close=105, open=105,
                      volume=5, trade_value_million_won=5,
                      updated_at=observed_at.timestamp() + 40,
                      operation_id=second_operation)

        def observation(value: dict[str, object]) -> list[tuple[str, object]]:
            item = minute_bar_observation(
                value, origin=ObservationOrigin.REALTIME,
                completeness=DataCompleteness.IN_PROGRESS,
                source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL,
            )
            return [(bar_observation_key(item), item)]

        latest = {
            "event_type": "trade", "item_key": code, "received_at": time.time(),
            "event": {"type": "trade", "payload": {
                "code": code, "price": 100, "market_cap_eok": 765432,
            }},
        }
        second_bar = {
            "trading_date": "2026-09-28", "trade_second": "10:00:01",
            "code": code, "market": "KRX", "open": 100, "high": 110,
            "low": 90, "close": 105, "volume": 10,
            "trade_value_won": 1020, "trade_count": 3,
            "available_at": observed_at.timestamp(),
        }
        started = time.time() - 1
        self.store.save_realtime_snapshots([latest])
        self.store.save_realtime_snapshots([latest])
        self.store.save_minute_bars([first], observations=observation(first))
        self.store.save_minute_bars([first], observations=observation(first))
        invalid = dict(second, operation_id=invalid_operation)
        del invalid["close"]
        with self.assertRaises(KeyError):
            self.store.save_minute_bars([second, invalid],
                                        observations=observation(second))
        self.assertEqual(3, self.store.load_minute_bars(code, "2026-09-28", "KRX")[0]["volume"])
        self.store.save_minute_bars([second], observations=observation(second))
        closure = {
            "trading_date": "2026-09-28", "minute": "10:00", "code": code,
            "market": "KRX", "operation_id": close_operation,
            "capture_quality": "complete", "finalization_source": "timer",
            "available_at": datetime(2026, 9, 28, 10, 1, 2, tzinfo=KST).timestamp(),
        }
        self.store.finalize_minute_bars([closure])
        self.store.finalize_minute_bars([closure])
        self.store.save_second_trade_bars([second_bar])
        self.store.save_second_trade_bars([second_bar])
        newer_second_bar = dict(second_bar, close=120, volume=12,
                                trade_count=4, available_at=second_bar["available_at"] + 1)
        self.store.save_second_trade_bars([newer_second_bar])
        self.store.save_second_trade_bars([second_bar])

        snapshots = [row for row in self.store.load_realtime_snapshots([code])
                     if row.get("payload", {}).get("code") == code]
        self.assertEqual([latest["event"]], snapshots)
        self.assertEqual(765432, self.store.load_latest_market_caps([code])[0]["market_cap_eok"])
        self.assertEqual(8, self.store.load_minute_bars(code, "2026-09-28", "KRX")[0]["volume"])
        revisions = list(reversed(self.store.load_observation_revisions("minute_bar", f"{code}:KRX")))
        self.assertEqual([3, 8, 8], [row["payload"]["volume"] for row in revisions])
        self.assertTrue(revisions[-1]["payload"]["window_closed"])
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT operation_id FROM central_minute_bar_operations "
                           "WHERE operation_id=ANY(%s)", (self.realtime_operation_ids,))
            self.assertEqual({first_operation, second_operation, close_operation},
                             {str(row[0]) for row in cursor.fetchall()})
            cursor.execute("SELECT close,volume,trade_count,available_at "
                           "FROM central_second_trade_bars WHERE code=%s", (code,))
            self.assertEqual((120, 12, 4, newer_second_bar["available_at"]), cursor.fetchone())

        calls = [call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
                 if call["writer_family"] in {"realtime.latest", "realtime.minute",
                                                "realtime.minute_finalize", "realtime.second_bar"}]
        self.assertEqual(12, len(calls))
        self.assertEqual(12, len({call["call_id"] for call in calls}))
        self.assertEqual({"realtime_latest": 2, "realtime_minute": 4,
                          "realtime_minute_finalize": 2, "realtime_second_bar": 4},
                         {kind: sum(call["writer_kind"] == kind for call in calls)
                          for kind in {call["writer_kind"] for call in calls}})
        self.assertTrue(all(call["backend_pid"] is not None
                            and call["transactions"] == 1 for call in calls))
        self.assertEqual((11, 1), (sum(call["commits"] for call in calls),
                                   sum(call["rollbacks"] for call in calls)))
        failed = next(call for call in calls if call["outcome"] == "rolled_back")
        self.assertEqual(("realtime_minute", 2, 0, 1), (
            failed["writer_kind"], failed["rows_attempted"],
            failed["commits"], failed["rollbacks"],
        ))
        self.assertIn({"stage": "body", "exception_type": "KeyError"}, failed["errors"])
        reader_calls = [call for call in summarize_db_calls(
            started, time.time() + 1, mode="raw",
        )["calls"] if call["access_mode"] == "read"
                       and call["writer_family"] == "read.realtime_market_state"]
        self.assertEqual({"realtime_snapshots": 1, "latest_market_caps": 1}, {
            kind: sum(call["writer_kind"] == kind for call in reader_calls)
            for kind in {call["writer_kind"] for call in reader_calls}
        })
        self.assertTrue(all(call["backend_pid"] is not None
                            and call["transactions"] == 1
                            and call["commits"] == 1 for call in reader_calls))
        legacy = summarize_market_bar_saves(started, time.time() + 1)["writer_transactions"]
        for kind in ("realtime_latest", "realtime_minute", "realtime_minute_finalize",
                     "realtime_second_bar"):
            committed_ids = {call["call_id"] for call in calls
                             if call["writer_kind"] == kind and call["outcome"] == "committed"}
            self.assertEqual(committed_ids, {sample["db_call_id"] for sample in
                                             legacy[kind]["call_samples"]})

    def test_realtime_document_batch_preserves_readers_and_correlates_metrics(self) -> None:
        token = uuid.uuid4().hex
        day = f"diagnostic-day-{token}"
        code = f"DIAG{token[:16]}"
        self.document_keys.extend((
            ("account_entry_symbols_daily", day, code),
            ("stock_price_references", code, "latest"),
        ))
        entry = {"owner": day, "key": code, "document": {
            "code": code, "environment": "diagnostic", "source": "kiwoom-realtime-00",
        }}
        reference = {"owner": code, "key": "latest", "document": {
            "upper_limit_price": 12345, "source": "kiwoom-websocket-0g",
        }}
        started = time.time() - 1
        self.store.upsert_documents("account_entry_symbols_daily", [entry])
        self.store.upsert_documents("account_entry_symbols_daily", [entry])
        self.store.upsert_documents("stock_price_references", [reference])
        self.store.upsert_documents("stock_price_references", [reference])

        # Exercise the same document readers used by TOP20 enrichment and market events.
        entries = self.store.load_documents("account_entry_symbols_daily", day, 5000)
        stored_reference = self.store.load_documents("stock_price_references", code, 1)
        self.assertEqual([code], [row["key"] for row in entries])
        self.assertEqual(entry["document"], entries[0]["document"])
        self.assertEqual(reference["document"], stored_reference[0]["document"])

        calls = [call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
                 if call["writer_kind"] in {
                     "document:account_entry_symbols_daily",
                     "document:stock_price_references",
                 } and call["access_mode"] == "write"]
        self.assertEqual(4, len(calls))
        self.assertEqual(4, len({call["call_id"] for call in calls}))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        for kind in ("document:account_entry_symbols_daily", "document:stock_price_references"):
            metric = legacy["writer_transactions"][kind]
            self.assertEqual(2, metric["calls"])
            self.assertTrue({call["call_id"] for call in calls if call["writer_kind"] == kind}
                            .issubset({sample["db_call_id"] for sample in metric["call_samples"]}))

    def test_external_market_bar_batch_preserves_reader_and_independent_commits(self) -> None:
        token = uuid.uuid4().hex
        instrument = f"DIAG-{token}"
        contract = f"DIAG-{token}.CME"
        self.external_bar_instruments.append(instrument)
        base = {
            "provider": "yahoo_delayed", "instrument": instrument,
            "contract": contract, "open": 100.0, "high": 105.0, "low": 99.0,
            "close": 104.0, "volume": 12.0, "updated_at": time.time(),
        }
        five_minute = {**base, "timeframe": "5m", "bar_time": "2026-09-28T00:00:00Z"}
        daily = {**base, "timeframe": "1d", "bar_time": "2026-09-28T00:00:00Z"}
        started = time.time() - 1
        self.store.save_external_bars([five_minute])
        self.store.save_external_bars([{**five_minute, "updated_at": base["updated_at"] + 60}])
        self.store.save_external_bars([daily])
        self.store.save_external_bars([{**daily, "updated_at": base["updated_at"] + 60}])

        five_minute_rows = self.store.load_external_bars(instrument, "5m")
        daily_rows = self.store.load_external_bars(instrument, "1d")
        self.assertEqual(1, len(five_minute_rows))
        self.assertEqual(1, len(daily_rows))
        self.assertEqual(104.0, five_minute_rows[0]["close"])
        self.assertEqual(104.0, daily_rows[0]["close"])
        self.assertEqual(base["updated_at"], five_minute_rows[0]["updated_at"])
        self.assertEqual(base["updated_at"], daily_rows[0]["updated_at"])

        corrected = {**five_minute, "close": 106.0, "updated_at": base["updated_at"] + 120}
        self.store.save_external_bars([corrected])
        corrected_rows = self.store.load_external_bars(instrument, "5m")
        self.assertEqual(106.0, corrected_rows[0]["close"])
        self.assertEqual(corrected["updated_at"], corrected_rows[0]["updated_at"])

        calls = [call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
                 if call["writer_kind"] == "external_market:bars"]
        self.assertEqual(5, len(calls))
        self.assertEqual(5, len({call["call_id"] for call in calls}))
        self.assertEqual({"external_market.bars"}, {call["writer_family"] for call in calls})
        self.assertTrue(all(
            call["operation"] == "save_external_bars"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        reader_calls = [call for call in summarize_db_calls(
            started, time.time() + 1, mode="raw",
        )["calls"] if call["writer_family"] == "read.external_market_bars"]
        self.assertEqual({"bars:5m": 2, "bars:1d": 1}, {
            kind: sum(call["writer_kind"] == kind for call in reader_calls)
            for kind in {call["writer_kind"] for call in reader_calls}
        })
        self.assertTrue(all(call["access_mode"] == "read"
                            and call["operation"] == "load_external_bars"
                            and call["backend_pid"] is not None
                            and call["transactions"] == 1
                            and call["commits"] == 1 for call in reader_calls))

    def test_external_market_bar_batch_failure_rolls_back_and_keeps_peer(self) -> None:
        instrument = f"DIAG-{uuid.uuid4().hex}"
        self.external_bar_instruments.append(instrument)
        base = {
            "provider": "yahoo_delayed", "instrument": instrument,
            "contract": f"{instrument}.CME", "timeframe": "5m",
            "bar_time": "2026-10-04T00:00:00Z", "open": 100.0,
            "high": 101.0, "low": 99.0, "close": None, "volume": None,
            "updated_at": time.time(),
        }
        self.store.save_external_bars([base])
        original = self.store.load_external_bars(instrument, "5m")
        self.assertEqual(1, len(original))
        self.assertIsNone(original[0]["close"])
        self.assertIsNone(original[0]["volume"])

        corrected = {**base, "close": 100.5, "volume": 12.0,
                     "updated_at": base["updated_at"] + 1}
        second = {**base, "bar_time": "2026-10-04T00:05:00Z"}
        self.store.save_external_bars([corrected, second])
        committed = self.store.load_external_bars(instrument, "5m")
        self.assertEqual([100.5, None], [row["close"] for row in committed])
        self.assertEqual([12.0, None], [row["volume"] for row in committed])

        peer = {**base, "timeframe": "1d"}
        self.store.save_external_bars([peer])
        peer_committed = self.store.load_external_bars(instrument, "1d")
        failed_first = {**corrected, "close": 102.0,
                        "updated_at": corrected["updated_at"] + 1}
        failed_second = {**base, "bar_time": "invalid-timestamp"}
        started = time.time() - 1
        with self.assertRaises(Exception):
            self.store.save_external_bars([failed_first, failed_second])
        self.assertEqual(committed, self.store.load_external_bars(instrument, "5m"))
        self.assertEqual(peer_committed, self.store.load_external_bars(instrument, "1d"))
        calls = [call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
                 if call["writer_kind"] == "external_market:bars" and call["rollbacks"] == 1]
        self.assertEqual(1, len(calls))
        self.assertEqual(2, calls[0]["rows_attempted"])
        self.assertEqual("rolled_back", calls[0]["outcome"])
        self.assertEqual(0, calls[0]["commits"])

    def test_news_original_publication_replay_preserves_reader_and_correlates_metrics(self) -> None:
        article_revision_id = f"diagnostic-{uuid.uuid4().hex}"
        self.document_keys.append(("news_original_publication", article_revision_id, "published_at"))
        original_published_at = "2026-09-28T09:30:00+09:00"
        runner = NewsJobRunner(
            self.store,
            fetcher=lambda url, *, timeout_seconds: ("Article body", original_published_at),
        )
        job = {
            "article_revision_id": article_revision_id,
            "payload": {"link": "https://example.test/article", "description": "summary"},
        }
        started = time.time() - 1
        with patch.object(self.store, "load_latest_news_body", return_value=None), \
                patch.object(self.store, "save_news_body_revision", return_value="diagnostic-body-revision"):
            body_revision_id = asyncio.run(runner._run_body(job))
        self.assertEqual("diagnostic-body-revision", body_revision_id)

        first = self.store.load_documents("news_original_publication", article_revision_id, 1)
        self.assertEqual(original_published_at, first[0]["document"]["published_at"])
        self.assertEqual("https://example.test/article", first[0]["document"]["source_url"])
        self.store.upsert_documents("news_original_publication", [{
            "owner": article_revision_id, "key": "published_at", "document": first[0]["document"],
        }])
        replay = self.store.load_documents("news_original_publication", article_revision_id, 1)
        self.assertEqual(first, replay)

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:news_original_publication" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:news_original_publication"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_external_market_collection_status_replay_correlates_metrics(self) -> None:
        instrument = f"diagnostic-{uuid.uuid4().hex}"
        self.document_keys.append(("external_market_collection_status", instrument, "current"))
        collector = YahooDelayedMarketCollector(self.store, {instrument: "CLV26.NYM"})
        started = time.time() - 1
        collector._save_status(instrument, "CLV26.NYM", "failed", "diagnostic failure", 0)
        first = self.store.load_document("external_market_collection_status", instrument, "current")
        self.assertEqual("failed", first["document"]["status"])
        self.assertEqual("diagnostic failure", first["document"]["error"])
        self.assertEqual(0, first["document"]["saved_rows"])

        self.store.upsert_documents("external_market_collection_status", [{
            "owner": instrument, "key": "current", "document": first["document"],
        }])
        replay = self.store.load_document("external_market_collection_status", instrument, "current")
        self.assertEqual(first, replay)

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:external_market_collection_status"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:external_market_collection_status"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_news_rule_assessment_replay_preserves_reader_and_correlates_metrics(self) -> None:
        article_revision_id = f"diagnostic-{uuid.uuid4().hex}"
        body_revision_id = f"diagnostic-body-{uuid.uuid4().hex}"
        self.document_keys.append(("news_assessment", "GLOBAL", article_revision_id))
        article = {
            "article_revision_id": article_revision_id, "stock_code": "GLOBAL",
            "identity": f"https://example.test/{article_revision_id}",
            "document": {
                "title": "Global market event", "description": "Market summary",
                "published_at": "2026-09-28T09:30:00+09:00", "stock_name": "GLOBAL",
            },
        }
        body = {
            "body_revision_id": body_revision_id,
            "body_text": "Global market conditions changed during the session.",
            "status": "fulltext",
        }
        runner = NewsJobRunner(self.store)
        job = {
            "article_revision_id": article_revision_id, "target_id": "GLOBAL",
            "payload": {"body_revision_id": body_revision_id, "stock_code": "GLOBAL"},
        }
        started = time.time() - 1
        with patch.object(self.store, "load_news_article_revision", return_value=article), \
                patch.object(self.store, "load_news_body_revision", return_value=body):
            result = asyncio.run(runner._run_rule(job))
        self.assertEqual(f"ignored:{body_revision_id}", result)

        first = self.store.load_document("news_assessment", "GLOBAL", article_revision_id)
        self.assertIsNotNone(first)
        self.assertEqual(body_revision_id, first["document"]["body_revision_id"])
        self.assertEqual("2026-09-28T09:30:00+09:00", first["document"]["published_at"])
        self.store.upsert_documents("news_assessment", [{
            "owner": "GLOBAL", "key": article_revision_id, "document": first["document"],
        }])
        replay = self.store.load_document("news_assessment", "GLOBAL", article_revision_id)
        self.assertEqual(first, replay)

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:news_assessment"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:news_assessment"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_credential_profile_writers_preserve_replay_lifecycle_and_independent_metrics(self) -> None:
        provider = "openai"
        registered_id = f"diag-{uuid.uuid4().hex}"
        request_id = str(uuid.uuid4())
        digest = uuid.uuid4().hex * 2
        created_at = datetime.now(timezone.utc).isoformat()
        self.credential_profile_ids.append(registered_id)
        self.credential_profile_request_keys.append((provider, request_id))
        self.document_keys.append(("credential_profile_requests", provider, request_id))
        started = time.time() - 1

        self.store.register_credential_profile(provider, registered_id, created_at)
        self.store.register_credential_profile(provider, registered_id, created_at)
        registered = [row for row in self.store.list_credential_profiles()
                      if row["profile_id"] == registered_id]
        self.assertEqual(1, len(registered))
        self.assertEqual("active", registered[0]["lifecycle_state"])

        barrier = Barrier(2)

        def create() -> dict[str, object]:
            barrier.wait(timeout=30)
            return self.store.create_credential_profile(provider, request_id, "first", digest)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(create) for _ in range(2)]
            created = [future.result(timeout=60) for future in futures]
        self.assertEqual(created[0], created[1])
        profile_id = created[0]["profile_id"]
        self.assertNotEqual(registered_id, profile_id)
        self.assertEqual({"provider": provider, "profile_id": profile_id, "label": "first"},
                         created[0])
        request = self.store.load_document("credential_profile_requests", provider, request_id)
        self.assertEqual(profile_id, request["document"]["profile_id"])
        self.assertEqual(digest, request["document"]["request_digest"])
        draft = [row for row in self.store.list_credential_profiles()
                 if row["profile_id"] == profile_id]
        self.assertEqual(1, len(draft))
        self.assertEqual("draft", draft[0]["lifecycle_state"])

        with self.assertRaisesRegex(ValueError, "PROFILE_REQUEST_CONFLICT"):
            self.store.create_credential_profile(provider, request_id, "other", uuid.uuid4().hex * 2)
        self.assertEqual("first", self.store.load_document(
            "credential_profile_requests", provider, request_id,
        )["document"]["label"])

        renamed = self.store.rename_credential_profile(provider, profile_id, "  renamed  ")
        self.assertEqual({"provider": provider, "profile_id": profile_id, "label": "renamed"},
                         renamed)
        self.assertEqual(renamed, self.store.rename_credential_profile(provider, profile_id, "renamed"))
        self.assertEqual("renamed", next(row for row in self.store.list_credential_profiles()
                                         if row["profile_id"] == profile_id)["label"])

        archived = self.store.archive_credential_profile(provider, profile_id)
        self.assertEqual(archived, self.store.archive_credential_profile(provider, profile_id))
        self.assertEqual("archived", archived["lifecycle_state"])
        with self.assertRaisesRegex(ValueError, "PROFILE_NOT_FOUND"):
            self.store.rename_credential_profile(provider, profile_id, "after archive")
        self.store.register_credential_profile(provider, profile_id, created_at)
        stored = next(row for row in self.store.list_credential_profiles()
                      if row["profile_id"] == profile_id)
        self.assertEqual(("archived", "renamed"),
                         (stored["lifecycle_state"], stored["label"]))
        self.assertEqual(profile_id, self.store.load_document(
            "credential_profile_requests", provider, request_id,
        )["document"]["profile_id"])

        expected = {
            "credential_profile_register": (3, 3, 0),
            "credential_profile_create": (3, 2, 1),
            "credential_profile_rename": (3, 2, 1),
            "credential_profile_archive": (2, 2, 0),
        }
        calls = [call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
                 if call["writer_kind"] in expected]
        self.assertEqual(11, len(calls))
        self.assertEqual(11, len({call["call_id"] for call in calls}))
        self.assertTrue(all(call["writer_family"] == "credential.profile"
                            and call["backend_pid"] is not None
                            and call["rows_attempted"] == 1 for call in calls))
        for kind, counts in expected.items():
            kind_calls = [call for call in calls if call["writer_kind"] == kind]
            self.assertEqual(counts, (len(kind_calls), sum(call["commits"] for call in kind_calls),
                                      sum(call["rollbacks"] for call in kind_calls)), kind)
        self.assertNotIn(digest, json.dumps(calls))
        profile_reads = [call for call in summarize_db_calls(
            started + 1, time.time() + 1, mode="raw",
        )["calls"] if call["writer_family"] == "read.credential"
                   and call["writer_kind"] == "profile_list"]
        self.assertEqual(4, len(profile_reads))
        self.assertTrue(all(call["access_mode"] == "read"
                            and call["transactions"] == 1
                            and call["commits"] == 1
                            and call["backend_pid"] is not None for call in profile_reads))
        self.assertNotIn(digest, json.dumps(profile_reads))

    def test_credential_activation_finalize_replays_and_rolls_back_after_file_commit(self) -> None:
        class IsolatedVaultMetadata:
            def __init__(self, delegate: PostgresQueryStore) -> None:
                self.delegate = delegate
                self.markers: dict[str, dict[str, object]] = {}

            def load_documents(self, collection: str, owner: str = "", limit: int = 1000,
                               offset: int = 0) -> list[dict[str, object]]:
                self.assert_collection(collection)
                rows = [{"owner": key, "key": "state", "document": value}
                        for key, value in self.markers.items() if not owner or key == owner]
                return rows[offset:offset + limit]

            def upsert_documents(self, collection: str, values: list[dict[str, object]]) -> None:
                self.assert_collection(collection)
                for value in values:
                    self.markers[str(value["owner"])] = dict(value["document"])

            def load_credential_activations(self, profile_id: str) -> list[dict[str, object]]:
                return self.delegate.load_credential_activations(profile_id)

            @staticmethod
            def assert_collection(collection: str) -> None:
                if collection != "credential_vault_state":
                    raise AssertionError(collection)

        account_ref, profile_id, conflicting_profile = (str(uuid.uuid4()) for _ in range(3))
        self.account_registry_refs.append(account_ref)
        self.account_binding_profile_ids.extend((profile_id, conflicting_profile))
        self.credential_activation_profile_ids.extend((profile_id, conflicting_profile))
        self.document_keys.append(("server_account_settings", f"kiwoom:mock:{account_ref}", "settings"))
        scope = {"broker": "kiwoom", "environment": "mock", "account_ref": account_ref}
        observed_at = datetime.now(timezone.utc).isoformat()
        self.assertEqual(account_ref, self.store.register_account_identity({
            **scope, "identity_fingerprint": uuid.uuid4().hex * 2,
            "created_at": observed_at,
        }))
        vault = CredentialStore(Path(self.tmp.name) / "activation-vault",
                                IsolatedVaultMetadata(self.store))
        self.addCleanup(vault.close)
        credentials = {"app_key": uuid.uuid4().hex, "secret_key": uuid.uuid4().hex}
        first = vault.save("kiwoom_mock", profile_id, credentials, expected_revision=0)
        self.assertEqual(1, first.revision)
        self.assertEqual(credentials, vault.load("kiwoom_mock", profile_id).payload["credentials"])
        self.assertEqual([], self.store.load_credential_activations(profile_id))
        self.assertEqual([], [row for row in self.store.load_account_bindings()
                              if row["credential_profile_id"] == profile_id])
        activation = {
            "operation_id": str(uuid.uuid4()), "provider": "kiwoom_mock",
            "profile_id": profile_id, "credential_revision": first.revision,
            "request_id": str(uuid.uuid4()), "request_digest": vault.request_digest(credentials),
            "environment": "mock", "account_ref": account_ref,
            "run_id": str(uuid.uuid4()), "committed_at": observed_at,
        }
        started = time.time() - 1
        barrier = Barrier(2)

        def finalize() -> dict[str, object]:
            barrier.wait(timeout=30)
            return self.store.finalize_credential_activation(activation)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(finalize) for _ in range(2)]
            committed = [future.result(timeout=60) for future in futures]
        self.assertEqual(committed[0], committed[1])
        self.assertEqual(1, committed[0]["binding_revision"])
        self.assertEqual(committed[0], self.store.find_credential_activation(
            operation_id=activation["operation_id"],
        ))
        self.assertEqual(committed[0], self.store.find_credential_activation(
            provider=activation["provider"], profile_id=profile_id,
            request_id=activation["request_id"],
        ))
        self.assertEqual([committed[0]], self.store.load_credential_activations(profile_id))
        self.assertEqual([1], [row["binding_revision"] for row in self.store.load_account_bindings()
                               if row["credential_profile_id"] == profile_id])
        settings = self.store.load_account_settings(scope)
        self.assertEqual((profile_id, 1, True, False), (
            settings["active_profile_id"], settings["revision"],
            settings["monitor_enabled"], settings["mock_order_enabled"],
        ))

        conflicting_credentials = {"app_key": uuid.uuid4().hex, "secret_key": uuid.uuid4().hex}
        second = vault.save("kiwoom_mock", conflicting_profile, conflicting_credentials,
                            expected_revision=0)
        conflict = {**activation, "operation_id": str(uuid.uuid4()),
                    "request_id": str(uuid.uuid4()), "profile_id": conflicting_profile,
                    "credential_revision": second.revision,
                    "request_digest": vault.request_digest(conflicting_credentials)}
        for _ in range(2):  # Recovery retries the same committed vault file.
            with self.assertRaisesRegex(ValueError, "ACCOUNT_PROFILE_CONFLICT"):
                self.store.finalize_credential_activation(conflict)
        self.assertIsNone(self.store.find_credential_activation(
            operation_id=conflict["operation_id"],
        ))
        self.assertEqual(conflicting_credentials, vault.load(
            "kiwoom_mock", conflicting_profile,
        ).payload["credentials"])
        self.assertEqual([], self.store.load_credential_activations(conflicting_profile))
        self.assertEqual([], [row for row in self.store.load_account_bindings()
                              if row["credential_profile_id"] == conflicting_profile])
        self.assertFalse(any(row["profile_id"] == conflicting_profile
                             for row in self.store.list_credential_profiles()))
        self.assertEqual(settings, self.store.load_account_settings(scope))

        calls = [call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
                 if call["writer_kind"] == "credential_activation_finalize"]
        self.assertEqual(4, len(calls))
        self.assertEqual(4, len({call["call_id"] for call in calls}))
        self.assertTrue(all(call["writer_family"] == "credential.activation"
                            and call["operation"] == "finalize_credential_activation"
                            and call["backend_pid"] is not None for call in calls))
        self.assertEqual(2, sum(call["commits"] for call in calls))
        self.assertEqual(2, sum(call["rollbacks"] for call in calls))
        for secret in (*credentials.values(), *conflicting_credentials.values()):
            self.assertNotIn(secret, json.dumps(calls))
        reader_calls = [call for call in summarize_db_calls(
            started + 1, time.time() + 1, mode="raw",
        )["calls"] if call["writer_family"] in {"read.credential", "read.account"}]
        expected_minimum = {
            "activation_lookup": 3, "profile_activations": 2,
            "profile_list": 1, "account_bindings": 2, "account_settings": 2,
        }
        for kind, minimum in expected_minimum.items():
            self.assertGreaterEqual(sum(call["writer_kind"] == kind for call in reader_calls),
                                    minimum, kind)
        self.assertTrue(all(call["access_mode"] == "read"
                            and call["transactions"] == 1
                            and call["commits"] == 1
                            and call["backend_pid"] is not None for call in reader_calls))
        for secret in (*credentials.values(), *conflicting_credentials.values()):
            self.assertNotIn(secret, json.dumps(reader_calls))

    def test_credential_vault_state_preserves_file_commit_and_recovers_fence(self) -> None:
        self.assertEqual([], self.store.load_documents("credential_vault_state", "", 1))
        vault = CredentialStore(Path(self.tmp.name) / "vault", self.store)
        self.addCleanup(vault.close)
        profile = f"diagnostic_{uuid.uuid4().hex}"
        identity = f"dart--{profile}"
        self.document_keys.append(("credential_vault_state", identity, "state"))
        first_secret, second_secret, recovered_secret = (uuid.uuid4().hex for _ in range(3))
        started = time.time() - 1

        first = vault.save("dart", profile, {"api_key": first_secret}, expected_revision=0)
        self.assertEqual(1, first.revision)
        marker = self.store.load_document("credential_vault_state", identity, "state")
        self.assertEqual({"initialized": True, "revision": 1}, marker["document"])
        second = vault.save("dart", profile, {"api_key": second_secret}, expected_revision=1)
        self.assertEqual(2, second.revision)

        with patch.object(self.store, "upsert_documents", side_effect=RuntimeError("injected fence failure")):
            with self.assertRaises(RuntimeError):
                vault.save("dart", profile, {"api_key": recovered_secret}, expected_revision=2)
        self.assertEqual(2, self.store.load_document(
            "credential_vault_state", identity, "state",
        )["document"]["revision"])
        vault.close()
        vault = CredentialStore(Path(self.tmp.name) / "vault", self.store)
        self.addCleanup(vault.close)
        restored = vault.load("dart", profile)
        self.assertEqual(3, restored.revision)
        self.assertTrue(restored.payload["credentials"]["api_key"] == recovered_secret)
        self.assertEqual(3, self.store.load_document(
            "credential_vault_state", identity, "state",
        )["document"]["revision"])

        missing_profile = f"diagnostic_{uuid.uuid4().hex}"
        missing_identity = f"dart--{missing_profile}"
        self.document_keys.append(("credential_vault_state", missing_identity, "state"))
        with patch.object(vault, "_atomic_write", side_effect=OSError("injected file failure")):
            with self.assertRaises(OSError):
                vault.save("dart", missing_profile, {"api_key": uuid.uuid4().hex},
                           expected_revision=0)
        self.assertEqual(0, self.store.load_document(
            "credential_vault_state", missing_identity, "state",
        )["document"]["revision"])
        with self.assertRaisesRegex(CredentialStoreError, "RECOVERY_REQUIRED"):
            vault.load("dart", missing_profile)

        encrypted = (Path(self.tmp.name) / "vault" / f"{identity}.json").read_text()
        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:credential_vault_state" and call["access_mode"] == "write"
        ]
        self.assertEqual(5, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:credential_vault_state"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))
        for secret in (first_secret, second_secret, recovered_secret):
            self.assertNotIn(secret, encrypted)
            self.assertNotIn(secret, json.dumps(marker))
            self.assertNotIn(secret, json.dumps(calls))

    def test_mock_automation_risk_history_and_current_recover_separate_writes(self) -> None:
        account_ref = f"diagnostic-{uuid.uuid4().hex}"
        observed = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
        recovery = MockAccountRecovery(AccountSnapshot(
            account_ref, 1_000_000, 0, {}, observed,
        ), ())
        repository = ForwardEvaluationRepository(self.store)

        def snapshot(revision: int, at: datetime):
            return build_mock_automation_risk_snapshot(
                recovery, (), (), (), execution_run_id="diagnostic-run",
                binding_revision=1, reconciliation_revision=revision,
                observed_at=at, broker_query_started_at=at,
                broker_query_completed_at=at,
            )

        first = snapshot(1, observed)
        second = snapshot(2, observed + timedelta(seconds=1))
        self.document_keys.extend([
            (repository.MOCK_AUTOMATION_RISK_COLLECTION, account_ref, first.snapshot_id),
            (repository.MOCK_AUTOMATION_RISK_COLLECTION, account_ref, second.snapshot_id),
            (repository.MOCK_AUTOMATION_CURRENT_RISK_COLLECTION, account_ref, account_ref),
        ])
        started = time.time() - 1
        self.assertTrue(repository.save_mock_automation_risk_snapshot(first))
        self.assertFalse(repository.save_mock_automation_risk_snapshot(first))
        self.assertEqual(first, repository.load_latest_mock_automation_risk(account_ref))
        self.assertEqual((first,), repository.load_mock_automation_risk_snapshots(account_ref))

        original_upsert = self.store.upsert_documents

        def fail_current(collection, values):
            if collection == repository.MOCK_AUTOMATION_CURRENT_RISK_COLLECTION:
                raise RuntimeError("injected current risk write failure")
            return original_upsert(collection, values)

        with patch.object(self.store, "upsert_documents", side_effect=fail_current):
            with self.assertRaises(RuntimeError):
                repository.save_mock_automation_risk_snapshot(second)
        self.assertEqual(first, repository.load_latest_mock_automation_risk(account_ref))
        self.assertEqual((first, second), repository.load_mock_automation_risk_snapshots(account_ref))
        self.assertFalse(repository.save_mock_automation_risk_snapshot(second))
        self.assertEqual(second, repository.load_latest_mock_automation_risk(account_ref))
        self.assertEqual((first, second), repository.load_mock_automation_risk_snapshots(account_ref))
        with self.assertRaisesRegex(ValueError, "monotonically"):
            repository.save_mock_automation_risk_snapshot(first)

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] in {
                "document:execution_mock_automation_risk_snapshots",
                "document:execution_mock_automation_current_risk",
            } and call["access_mode"] == "write"
        ]
        self.assertEqual(4, len(calls))
        for kind in (
            "document:execution_mock_automation_risk_snapshots",
            "document:execution_mock_automation_current_risk",
        ):
            selected = [call for call in calls if call["writer_kind"] == kind]
            self.assertEqual(2, len(selected))
            self.assertTrue(all(
                call["writer_family"] == "document.collection"
                and call["operation"] == "upsert_documents"
                and call["outcome"] == "committed"
                and call["rows_attempted"] == 1
                and call["sql_calls"] == 1
                and call["commits"] == 1
                and call["backend_pid"] is not None
                for call in selected
            ))
            samples = summarize_market_bar_saves(started, time.time() + 1)[
                "writer_transactions"
            ][kind]["call_samples"]
            self.assertTrue({call["call_id"] for call in selected}.issubset(
                {sample["db_call_id"] for sample in samples}
            ))

    def test_mock_automation_recovery_preserves_lineage_and_separate_projection(self) -> None:
        account_ref = str(uuid.uuid4())
        observed = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
        repository = ForwardEvaluationRepository(self.store)
        recovery_collections = (
            repository.MOCK_AUTOMATION_RECOVERY_COLLECTION,
            repository.MOCK_AUTOMATION_CURRENT_RECOVERY_COLLECTION,
        )

        def cleanup_recovery_documents() -> None:
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM central_documents WHERE collection=ANY(%s) AND owner=%s",
                    (list(recovery_collections), account_ref),
                )
                cursor.execute(
                    "SELECT COUNT(*) FROM central_documents WHERE collection=ANY(%s) AND owner=%s",
                    (list(recovery_collections), account_ref),
                )
                self.assertEqual(0, cursor.fetchone()[0])

        self.addCleanup(cleanup_recovery_documents)
        spec = MockAutomationOperatingSpec(
            strategy_ref=f"diagnostic-strategy-{uuid.uuid4().hex}",
            candidate_package_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            final_result_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            final_batch_id="diagnostic-final-batch", final_run_id="diagnostic-final-run",
            forward_profile_id="diagnostic-profile",
            account_scope=AccountScope("kiwoom", AccountEnvironment.MOCK, account_ref),
            credential_profile_id="diagnostic-credential", binding_revision=1,
            binding_verified_at=observed, frozen_at=observed,
            maximum_daily_loss_won=100_000, maximum_data_gap_seconds=5,
            maximum_submission_unknown_count=0, maximum_reconnect_count=2,
            maximum_balance_mismatch_count=0,
        )
        admission = build_mock_automation_admission(spec, requested_at=observed)
        lease = build_mock_automation_lease_receipt(admission, admitted_at=observed)
        self.document_keys.append((
            repository.MOCK_AUTOMATION_SPEC_COLLECTION, account_ref, spec.spec_id,
        ))
        self.store.upsert_documents(repository.MOCK_AUTOMATION_SPEC_COLLECTION, [{
            "owner": account_ref, "key": spec.spec_id, "document": spec.to_dict(),
        }])
        self.document_keys.extend((
            (repository.MOCK_AUTOMATION_ADMISSION_COLLECTION,
             account_ref, admission.admission_id),
            (repository.MOCK_AUTOMATION_ADMISSION_BY_SPEC_COLLECTION,
             account_ref, spec.spec_id),
            (repository.MOCK_AUTOMATION_LEASE_COLLECTION,
             account_ref, lease.receipt_id),
            (repository.MOCK_AUTOMATION_LEASE_BY_ADMISSION_COLLECTION,
             account_ref, admission.admission_id),
        ))
        repository.save_mock_automation_admission(admission)
        self.assertEqual(
            admission,
            repository.load_mock_automation_admission_for_spec(account_ref, spec.spec_id),
        )
        repository.save_mock_automation_lease_receipt(lease)
        self.assertEqual(
            lease,
            repository.load_mock_automation_lease_for_admission(
                account_ref, admission.admission_id,
            ),
        )
        recovery = MockAccountRecovery(AccountSnapshot(
            account_ref, 1_000_000, 0, {}, observed,
        ), ())
        runtime = SimpleNamespace(
            account_ref=account_ref, run_id=admission.execution_run_id,
            set_new_orders_enabled=lambda enabled: None,
            automation_decision_guard=nullcontext,
        )

        def risk(revision: int, at: datetime):
            return build_mock_automation_risk_snapshot(
                recovery, (), (), (), execution_run_id=runtime.run_id,
                binding_revision=1, reconciliation_revision=revision,
                observed_at=at, broker_query_started_at=at,
                broker_query_completed_at=at,
            )

        first_risk = risk(1, observed)
        second_risk = risk(2, observed + timedelta(seconds=1))
        self.document_keys.extend([
            (repository.MOCK_AUTOMATION_RISK_COLLECTION, account_ref, first_risk.snapshot_id),
            (repository.MOCK_AUTOMATION_RISK_COLLECTION, account_ref, second_risk.snapshot_id),
            (repository.MOCK_AUTOMATION_CURRENT_RISK_COLLECTION, account_ref, account_ref),
        ])
        started = time.time() - 1
        repository.save_mock_automation_risk_snapshot(first_risk)
        first = record_mock_automation_recovery_from_risk(
            repository, runtime, recovery, first_risk,
            account_ref=account_ref, spec_id=spec.spec_id,
        )
        replay = record_mock_automation_recovery_from_risk(
            repository, runtime, recovery, first_risk,
            account_ref=account_ref, spec_id=spec.spec_id,
        )
        self.assertEqual(first, replay)
        self.assertEqual(first, repository.load_latest_mock_automation_recovery(
            account_ref, admission.admission_id,
        ))

        repository.save_mock_automation_risk_snapshot(second_risk)
        original_upsert = self.store.upsert_documents

        def fail_current(collection, values):
            if collection == repository.MOCK_AUTOMATION_CURRENT_RECOVERY_COLLECTION:
                raise RuntimeError("injected current recovery write failure")
            return original_upsert(collection, values)

        with patch.object(self.store, "upsert_documents", side_effect=fail_current):
            with self.assertRaises(RuntimeError):
                record_mock_automation_recovery_from_risk(
                    repository, runtime, recovery, second_risk,
                    account_ref=account_ref, spec_id=spec.spec_id,
                )
        decisions = repository.load_mock_automation_recovery_decisions(account_ref)
        self.assertEqual(2, len(decisions))
        second = decisions[-1]
        self.assertEqual(first, repository.load_latest_mock_automation_recovery(
            account_ref, admission.admission_id,
        ))
        retried = record_mock_automation_recovery_from_risk(
            repository, runtime, recovery, second_risk,
            account_ref=account_ref, spec_id=spec.spec_id,
        )
        self.assertEqual(second, retried)
        self.assertEqual(second, repository.load_latest_mock_automation_recovery(
            account_ref, admission.admission_id,
        ))
        with self.assertRaisesRegex(RuntimeError, "latest mock automation risk snapshot changed"):
            record_mock_automation_recovery_from_risk(
                repository, runtime, recovery, first_risk,
                account_ref=account_ref, spec_id=spec.spec_id,
            )

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "document.collection"
            and call["writer_kind"] in {
                "document:execution_mock_automation_recovery_decisions",
                "document:execution_mock_automation_current_recovery",
            }
        ]
        self.assertEqual(5, len(calls))
        self.assertEqual(5, len({call["call_id"] for call in calls}))
        self.assertEqual([
            "document:execution_mock_automation_recovery_decisions",
            "document:execution_mock_automation_current_recovery",
            "document:execution_mock_automation_current_recovery",
            "document:execution_mock_automation_recovery_decisions",
            "document:execution_mock_automation_current_recovery",
        ], [call["writer_kind"] for call in calls])
        for kind, count in (
            ("document:execution_mock_automation_recovery_decisions", 2),
            ("document:execution_mock_automation_current_recovery", 3),
        ):
            selected = [call for call in calls if call["writer_kind"] == kind]
            self.assertEqual(count, len(selected))
            self.assertTrue(all(
                call["writer_family"] == "document.collection"
                and call["operation"] == "upsert_documents"
                and call["outcome"] == "committed"
                and call["rows_attempted"] == 1
                and call["sql_calls"] == 1
                and call["commits"] == 1
                and call["backend_pid"] is not None
                for call in selected
            ))
            samples = summarize_market_bar_saves(started, time.time() + 1)[
                "writer_transactions"
            ][kind]["call_samples"]
            self.assertTrue({call["call_id"] for call in selected}.issubset(
                {sample["db_call_id"] for sample in samples}
            ))

    def test_mock_automation_decision_gate_preserves_approval_boundary(self) -> None:
        account_ref = str(uuid.uuid4())
        observed = datetime(2026, 9, 16, 0, 0, tzinfo=timezone.utc)
        repository = ForwardEvaluationRepository(self.store)
        collections = (
            repository.MOCK_AUTOMATION_SPEC_COLLECTION,
            repository.MOCK_AUTOMATION_ADMISSION_COLLECTION,
            repository.MOCK_AUTOMATION_ADMISSION_BY_SPEC_COLLECTION,
            repository.MOCK_AUTOMATION_LEASE_COLLECTION,
            repository.MOCK_AUTOMATION_LEASE_BY_ADMISSION_COLLECTION,
            repository.MOCK_AUTOMATION_RISK_COLLECTION,
            repository.MOCK_AUTOMATION_CURRENT_RISK_COLLECTION,
            repository.MOCK_AUTOMATION_RECOVERY_COLLECTION,
            repository.MOCK_AUTOMATION_CURRENT_RECOVERY_COLLECTION,
            repository.MOCK_AUTOMATION_GATE_COLLECTION,
            repository.MOCK_AUTOMATION_APPROVED_GATE_COLLECTION,
        )

        def cleanup_documents() -> None:
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM central_documents WHERE collection=ANY(%s) AND owner=%s",
                    (list(collections), account_ref),
                )
                cursor.execute(
                    "SELECT COUNT(*) FROM central_documents "
                    "WHERE collection=ANY(%s) AND owner=%s",
                    (list(collections), account_ref),
                )
                self.assertEqual(0, cursor.fetchone()[0])

        self.addCleanup(cleanup_documents)
        spec = MockAutomationOperatingSpec(
            strategy_ref=f"diagnostic-strategy-{uuid.uuid4().hex}",
            candidate_package_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            final_result_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            final_batch_id="diagnostic-final-batch", final_run_id="diagnostic-final-run",
            forward_profile_id="diagnostic-profile",
            account_scope=AccountScope("kiwoom", AccountEnvironment.MOCK, account_ref),
            credential_profile_id="diagnostic-credential", binding_revision=1,
            binding_verified_at=observed, frozen_at=observed,
            maximum_concurrent_positions=1, maximum_capital_won=10_000_000,
            maximum_daily_loss_won=100_000, maximum_data_gap_seconds=5,
            maximum_submission_unknown_count=0, maximum_reconnect_count=2,
            maximum_balance_mismatch_count=0, supported_venue="KRX",
            session_profile="krx-regular/v1", data_path="nas",
        )
        admission = build_mock_automation_admission(spec, requested_at=observed)
        lease = build_mock_automation_lease_receipt(admission, admitted_at=observed)
        self.store.upsert_documents(repository.MOCK_AUTOMATION_SPEC_COLLECTION, [{
            "owner": account_ref, "key": spec.spec_id, "document": spec.to_dict(),
        }])
        repository.save_mock_automation_admission(admission)
        repository.save_mock_automation_lease_receipt(lease)
        self.assertEqual(admission, repository.load_mock_automation_admission_for_spec(
            account_ref, spec.spec_id,
        ))
        self.assertEqual(lease, repository.load_mock_automation_lease_for_admission(
            account_ref, admission.admission_id,
        ))
        recovery = MockAccountRecovery(AccountSnapshot(
            account_ref, 1_000_000, 0, {}, observed,
        ), ())
        risk = build_mock_automation_risk_snapshot(
            recovery, (), (), (), execution_run_id=admission.execution_run_id,
            binding_revision=1, reconciliation_revision=1,
            observed_at=observed, broker_query_started_at=observed,
            broker_query_completed_at=observed,
        )
        repository.save_mock_automation_risk_snapshot(risk)
        runtime = SimpleNamespace(
            account_ref=account_ref, run_id=admission.execution_run_id,
            set_new_orders_enabled=lambda enabled: None,
            automation_decision_guard=nullcontext,
        )
        recovered = record_mock_automation_recovery_from_risk(
            repository, runtime, recovery, risk,
            account_ref=account_ref, spec_id=spec.spec_id,
        )
        metrics = MockAutomationLiveMetrics(
            observed_at=risk.observed_at,
            recovery_complete=risk.reconciliation_complete,
            daily_net_pnl_won=risk.daily_net_pnl_won,
            daily_net_pnl_source=risk.daily_net_pnl_source,
            data_gap_seconds=risk.data_gap_seconds,
            submission_unknown_count=risk.submission_unknown_count,
            reconnect_count=risk.reconnect_count,
            balance_mismatch_count=risk.balance_mismatch_count,
            data_path=risk.data_path,
        )
        profile = SimpleNamespace(
            evaluation_start=observed, evaluation_end=observed + timedelta(days=1),
        )
        control = SimpleNamespace(
            desired_state=MockAutomationDesiredState.RUNNING,
            active_spec_id=spec.spec_id,
            execution_run_id=admission.execution_run_id, control_revision=1,
        )

        def decision(snapshot_id: str) -> StrategyDecision:
            body = {
                "run_id": admission.execution_run_id,
                "snapshot_id": snapshot_id,
                "symbol": "005930", "proposal": "ENTER", "final_action": "ENTER",
                "quantity": 1, "signal_reference_price": 70_000,
                "required_capital_won": 70_000, "reasons": (), "constraints": ("KRX",),
                "state_before": {}, "state_after": {},
                "decided_at": observed.isoformat(),
            }
            encoded = json.dumps(
                body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            return StrategyDecision(
                decision_id=f"decision_{hashlib.sha256(encoded).hexdigest()}", **body,
            )

        def gate(snapshot_id: str, live_metrics: MockAutomationLiveMetrics):
            return assess_mock_automation_decision(
                spec, admission, lease, recovered, decision(snapshot_id), recovery,
                live_metrics, strategy_ref=spec.strategy_ref,
                control=control, profile=profile,
                server_now=observed + timedelta(seconds=1),
                risk_snapshot_id=risk.snapshot_id,
                risk_reconciliation_revision=risk.reconciliation_revision,
            )

        approved = gate("diagnostic-approved", metrics)
        blocked = gate("diagnostic-blocked", replace(
            metrics, daily_net_pnl_source="unverified",
        ))
        retried = gate("diagnostic-retried", metrics)
        self.assertEqual(MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION,
                         approved.status)
        self.assertEqual(MockAutomationGateStatus.BLOCKED, blocked.status)
        self.assertEqual(MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION,
                         retried.status)
        self.assertIn("DAILY_NET_PNL_SOURCE_UNVERIFIED", blocked.reasons)

        started = time.time() - 1
        self.assertTrue(repository.save_mock_automation_decision_gate(approved))
        self.assertFalse(repository.save_mock_automation_decision_gate(approved))
        self.assertEqual(approved, repository.load_mock_automation_approved_gate(
            account_ref, approved.intent_id,
        ))
        self.assertTrue(repository.save_mock_automation_decision_gate(blocked))
        self.assertIsNone(repository.load_mock_automation_approved_gate(
            account_ref, blocked.intent_id,
        ))
        original_upsert = self.store.upsert_documents

        def fail_approved_projection(collection, values):
            if collection == repository.MOCK_AUTOMATION_APPROVED_GATE_COLLECTION:
                raise RuntimeError("injected approved gate projection failure")
            return original_upsert(collection, values)

        with patch.object(self.store, "upsert_documents",
                          side_effect=fail_approved_projection):
            with self.assertRaisesRegex(RuntimeError, "approved gate projection failure"):
                repository.save_mock_automation_decision_gate(retried)
        history = repository.load_mock_automation_decision_gates(account_ref)
        self.assertEqual({approved.gate_id, blocked.gate_id, retried.gate_id},
                         {item.gate_id for item in history})
        self.assertIsNone(repository.load_mock_automation_approved_gate(
            account_ref, retried.intent_id,
        ))
        self.assertFalse(repository.save_mock_automation_decision_gate(retried))
        self.assertEqual(retried, repository.load_mock_automation_approved_gate(
            account_ref, retried.intent_id,
        ))

        newer_risk = build_mock_automation_risk_snapshot(
            recovery, (), (), (), execution_run_id=admission.execution_run_id,
            binding_revision=1, reconciliation_revision=2,
            observed_at=observed + timedelta(seconds=2),
            broker_query_started_at=observed + timedelta(seconds=2),
            broker_query_completed_at=observed + timedelta(seconds=2),
        )
        repository.save_mock_automation_risk_snapshot(newer_risk)
        with self.assertRaisesRegex(ValueError, "does not match current risk evidence"):
            repository.save_mock_automation_decision_gate(approved)

        kinds = (
            "document:execution_mock_automation_decision_gates",
            "document:execution_mock_automation_approved_gates",
        )
        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "document.collection"
            and call["writer_kind"] in kinds
        ]
        self.assertEqual([kinds[0], kinds[1], kinds[0], kinds[0], kinds[1]],
                         [call["writer_kind"] for call in calls])
        self.assertEqual(5, len({call["call_id"] for call in calls}))
        for kind, count in ((kinds[0], 3), (kinds[1], 2)):
            selected = [call for call in calls if call["writer_kind"] == kind]
            self.assertEqual(count, len(selected))
            self.assertTrue(all(
                call["writer_family"] == "document.collection"
                and call["operation"] == "upsert_documents"
                and call["outcome"] == "committed"
                and call["rows_attempted"] == 1
                and call["sql_calls"] == 1
                and call["commits"] == 1
                and call["backend_pid"] is not None
                for call in selected
            ))
            samples = summarize_market_bar_saves(started, time.time() + 1)[
                "writer_transactions"
            ][kind]["call_samples"]
            self.assertTrue({call["call_id"] for call in selected}.issubset(
                {sample["db_call_id"] for sample in samples}
            ))

    def test_mock_automation_dispatch_receipt_recovers_separate_intent_projection(self) -> None:
        account_ref = str(uuid.uuid4())
        observed = datetime(2026, 9, 16, 0, 0, tzinfo=timezone.utc)
        repository = ForwardEvaluationRepository(self.store)
        intent_id = f"diagnostic-intent-{uuid.uuid4().hex}"
        decision_id = f"diagnostic-decision-{uuid.uuid4().hex}"
        gate_body = {
            "version": MOCK_AUTOMATION_DECISION_GATE_VERSION,
            "admission_id": f"diagnostic-admission-{uuid.uuid4().hex}",
            "lease_receipt_id": f"diagnostic-lease-{uuid.uuid4().hex}",
            "recovery_decision_id": f"diagnostic-recovery-{uuid.uuid4().hex}",
            "spec_id": f"diagnostic-spec-{uuid.uuid4().hex}",
            "strategy_ref": "diagnostic-strategy", "account_ref": account_ref,
            "execution_run_id": f"diagnostic-run-{uuid.uuid4().hex}",
            "strategy_decision_id": decision_id, "intent_id": intent_id,
            "symbol": "005930", "action": "ENTER", "quantity": 1, "limit_price": 70_000,
            "decided_at": observed.isoformat(), "observed_at": observed.isoformat(),
            "account_as_of": observed.isoformat(), "server_now": observed.isoformat(),
            "recovery_fingerprint": "diagnostic-recovery-fingerprint",
            "session_evidence": "diagnostic-session", "daily_net_pnl_won": 0,
            "daily_net_pnl_source": "account_scoped_fifo_broker_cost/v1",
            "data_gap_seconds": 0, "submission_unknown_count": 0,
            "reconnect_count": 0, "balance_mismatch_count": 0,
            "in_flight_order_count": 0, "control_revision": 1,
            "status": MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION.value,
            "reasons": [], "risk_snapshot_id": "", "risk_reconciliation_revision": 0,
        }
        gate_body["gate_id"] = "mock_automation_gate_" + hashlib.sha256(json.dumps(
            gate_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        gate = mock_automation_decision_gate_from_dict(gate_body)
        receipt_body = {
            "version": MOCK_AUTOMATION_DISPATCH_RECEIPT_VERSION,
            "gate_id": gate.gate_id, "intent_id": intent_id,
            "account_ref": account_ref, "execution_run_id": gate.execution_run_id,
            "strategy_decision_id": decision_id, "order_state": "SUBMITTED",
            "broker_order_id": "diagnostic-broker-order",
            "dispatched_at": observed.isoformat(),
        }
        receipt_body["receipt_id"] = "mock_automation_dispatch_" + hashlib.sha256(
            json.dumps(receipt_body, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        receipt = mock_automation_dispatch_receipt_from_dict(receipt_body)
        self.document_keys.extend((
            (repository.MOCK_AUTOMATION_GATE_COLLECTION, account_ref, gate.gate_id),
            (repository.MOCK_AUTOMATION_DISPATCH_COLLECTION, account_ref, receipt.receipt_id),
            (repository.MOCK_AUTOMATION_DISPATCH_BY_INTENT_COLLECTION, account_ref, intent_id),
        ))
        self.store.upsert_documents(repository.MOCK_AUTOMATION_GATE_COLLECTION, [{
            "owner": account_ref, "key": gate.gate_id, "document": gate.to_dict(),
        }])
        original_upsert = self.store.upsert_documents

        def fail_intent_projection(collection, values):
            if collection == repository.MOCK_AUTOMATION_DISPATCH_BY_INTENT_COLLECTION:
                raise RuntimeError("injected dispatch intent projection failure")
            return original_upsert(collection, values)

        started = time.time() - 1
        with patch.object(self.store, "upsert_documents", side_effect=fail_intent_projection):
            with self.assertRaisesRegex(RuntimeError, "intent projection failure"):
                repository.save_mock_automation_dispatch_receipt(receipt)
        self.assertEqual((receipt,), repository.load_mock_automation_dispatch_receipts(
            account_ref,
        ))
        self.assertIsNone(repository.load_mock_automation_dispatch_receipt(
            account_ref, intent_id,
        ))
        self.assertFalse(repository.save_mock_automation_dispatch_receipt(receipt))
        self.assertEqual(receipt, repository.load_mock_automation_dispatch_receipt(
            account_ref, intent_id,
        ))
        self.assertFalse(repository.save_mock_automation_dispatch_receipt(receipt))
        self.assertEqual((receipt,), repository.load_mock_automation_dispatch_receipts(
            account_ref,
        ))

        kinds = (
            "document:execution_mock_automation_dispatch_receipts",
            "document:execution_mock_automation_dispatch_by_intent",
        )
        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "document.collection"
            and call["writer_kind"] in kinds
        ]
        self.assertEqual(list(kinds), [call["writer_kind"] for call in calls])
        self.assertEqual(2, len({call["call_id"] for call in calls}))
        for kind, call in zip(kinds, calls):
            self.assertEqual(("document.collection", "upsert_documents", "committed", 1, 1, 1), (
                call["writer_family"], call["operation"], call["outcome"],
                call["rows_attempted"], call["sql_calls"], call["commits"],
            ))
            self.assertIsNotNone(call["backend_pid"])
            samples = summarize_market_bar_saves(started, time.time() + 1)[
                "writer_transactions"
            ][kind]["call_samples"]
            self.assertIn(call["call_id"], {sample["db_call_id"] for sample in samples})

    def test_mock_automation_control_cas_preserves_native_transactions(self) -> None:
        account_ref = str(uuid.uuid4())
        collection = "execution_mock_automation_control"
        self.document_keys.append((collection, account_ref, account_ref))
        observed = datetime(2026, 9, 16, tzinfo=timezone.utc)

        def control(revision: int, reason: object) -> dict[str, object]:
            return {
                "account_ref": account_ref, "control_revision": revision,
                "desired_state": "STOPPED" if revision == 2 else "RUNNING",
                "changed_at": observed.isoformat(), "reason": reason,
            }

        started = time.time() - 1
        self.assertTrue(self.store.save_mock_automation_control(
            control(1, "initial"), expected_revision=0,
        ))
        self.assertFalse(self.store.save_mock_automation_control(
            control(1, "stale"), expected_revision=0,
        ))
        self.assertEqual(1, self.store.load_mock_automation_control(
            account_ref,
        )["control_revision"])

        ready = Barrier(2, timeout=10)

        def replace_control(reason: str) -> bool:
            ready.wait()
            return self.store.save_mock_automation_control(
                control(2, reason), expected_revision=1,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(replace_control, ("first", "second")))
        self.assertEqual([False, True], sorted(results))
        stored = self.store.load_mock_automation_control(account_ref)
        self.assertEqual((2, "STOPPED"), (
            stored["control_revision"], stored["desired_state"],
        ))
        self.assertIn(stored["reason"], ("first", "second"))

        with self.assertRaises(TypeError):
            self.store.save_mock_automation_control(
                control(3, {"not JSON serializable"}), expected_revision=2,
            )
        self.assertEqual(stored, self.store.load_mock_automation_control(account_ref))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "mock_automation_control"
        ]
        self.assertEqual(5, len(calls))
        self.assertEqual(5, len({call["call_id"] for call in calls}))
        self.assertEqual([2, 2, 2, 3, 3], sorted(call["sql_calls"] for call in calls))
        self.assertEqual((4, 1), (
            sum(call["commits"] for call in calls),
            sum(call["rollbacks"] for call in calls),
        ))
        self.assertEqual("committed", calls[1]["outcome"])
        self.assertEqual(2, calls[1]["sql_calls"])
        self.assertEqual("rolled_back", calls[-1]["outcome"])
        self.assertIn({"stage": "body", "exception_type": "TypeError"},
                      calls[-1]["errors"])
        reader_calls = [call for call in summarize_db_calls(
            started + 1, time.time() + 1, mode="raw",
        )["calls"] if call["writer_family"] == "read.mock_automation"
                   and call["writer_kind"] == "control_state"]
        self.assertEqual(3, len(reader_calls))
        self.assertEqual(3, len({call["call_id"] for call in reader_calls}))
        self.assertTrue(all(call["access_mode"] == "read"
                            and call["transactions"] == 1
                            and call["commits"] == 1
                            and call["sql_calls"] == 1
                            and call["backend_pid"] is not None for call in reader_calls))
        for call in calls:
            self.assertEqual(("mock_automation.control", "save_mock_automation_control", 1), (
                call["writer_family"], call["operation"], call["transactions"],
            ))
            self.assertIsNone(call["rows_attempted"])
            self.assertIsNotNone(call["backend_pid"])

    def test_mock_automation_stop_preserves_closed_control_and_recovers_projection(self) -> None:
        account_ref = str(uuid.uuid4())
        observed = datetime(2026, 9, 16, 0, 0, tzinfo=timezone.utc)
        repository = ForwardEvaluationRepository(self.store)
        collections = (
            repository.MOCK_AUTOMATION_SPEC_COLLECTION,
            repository.MOCK_AUTOMATION_ADMISSION_COLLECTION,
            repository.MOCK_AUTOMATION_ADMISSION_BY_SPEC_COLLECTION,
            "execution_mock_automation_control",
            repository.MOCK_AUTOMATION_STOP_COLLECTION,
            repository.MOCK_AUTOMATION_CURRENT_STOP_COLLECTION,
        )

        def cleanup_documents() -> None:
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM central_documents WHERE collection=ANY(%s) AND owner=%s",
                    (list(collections), account_ref),
                )
                cursor.execute(
                    "SELECT COUNT(*) FROM central_documents "
                    "WHERE collection=ANY(%s) AND owner=%s",
                    (list(collections), account_ref),
                )
                self.assertEqual(0, cursor.fetchone()[0])

        self.addCleanup(cleanup_documents)
        spec = MockAutomationOperatingSpec(
            strategy_ref=f"diagnostic-strategy-{uuid.uuid4().hex}",
            candidate_package_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            final_result_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            final_batch_id="diagnostic-final-batch", final_run_id="diagnostic-final-run",
            forward_profile_id="diagnostic-profile",
            account_scope=AccountScope("kiwoom", AccountEnvironment.MOCK, account_ref),
            credential_profile_id="diagnostic-credential", binding_revision=1,
            binding_verified_at=observed, frozen_at=observed,
            maximum_concurrent_positions=1, maximum_capital_won=10_000_000,
            maximum_daily_loss_won=100_000, maximum_data_gap_seconds=5,
            maximum_submission_unknown_count=0, maximum_reconnect_count=2,
            maximum_balance_mismatch_count=0, supported_venue="KRX",
            session_profile="krx-regular/v1", data_path="nas",
        )
        admission = build_mock_automation_admission(spec, requested_at=observed)
        self.store.upsert_documents(repository.MOCK_AUTOMATION_SPEC_COLLECTION, [{
            "owner": account_ref, "key": spec.spec_id, "document": spec.to_dict(),
        }])
        repository.save_mock_automation_admission(admission)
        running = MockAutomationControl(
            version=MOCK_AUTOMATION_CONTROL_VERSION,
            account_ref=account_ref, desired_state=MockAutomationDesiredState.RUNNING,
            control_revision=1, active_spec_id=spec.spec_id,
            execution_run_id=admission.execution_run_id,
            changed_at=observed, reason="diagnostic running",
        )
        self.assertTrue(repository.save_mock_automation_control(
            running, expected_revision=0,
        ))
        enabled: list[bool] = []
        runtime = SimpleNamespace(
            account_ref=account_ref, run_id=admission.execution_run_id,
            set_new_orders_enabled=enabled.append,
        )
        original_upsert = self.store.upsert_documents

        def fail_current(collection, values):
            if collection == repository.MOCK_AUTOMATION_CURRENT_STOP_COLLECTION:
                raise RuntimeError("injected current stop projection failure")
            return original_upsert(collection, values)

        started = time.time() - 1
        with patch.object(self.store, "upsert_documents", side_effect=fail_current):
            with self.assertRaisesRegex(RuntimeError, "current stop projection failure"):
                emergency_stop_mock_automation(
                    repository, runtime, account_ref=account_ref, spec_id=spec.spec_id,
                    stopped_at=observed + timedelta(seconds=1), reason="diagnostic stop",
                )
        stopped = repository.load_mock_automation_control(account_ref)
        self.assertEqual(MockAutomationDesiredState.STOPPED, stopped.desired_state)
        self.assertEqual(running.control_revision + 1, stopped.control_revision)
        self.assertEqual([False], enabled)
        history = repository.load_mock_automation_stop_revisions(account_ref)
        self.assertEqual(1, len(history))
        self.assertIsNone(repository.load_latest_mock_automation_stop(
            account_ref, admission.admission_id,
        ))
        self.assertFalse(repository.save_mock_automation_stop_revision(history[0]))
        self.assertEqual(history[0], repository.load_latest_mock_automation_stop(
            account_ref, admission.admission_id,
        ))
        self.assertEqual(history, repository.load_mock_automation_stop_revisions(account_ref))
        self.assertEqual(MockAutomationDesiredState.STOPPED,
                         repository.load_mock_automation_control(account_ref).desired_state)

        kinds = (
            "document:execution_mock_automation_stop_revisions",
            "document:execution_mock_automation_current_stop",
        )
        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "document.collection"
            and call["writer_kind"] in kinds
        ]
        self.assertEqual(list(kinds), [call["writer_kind"] for call in calls])
        self.assertEqual(2, len({call["call_id"] for call in calls}))
        for kind, call in zip(kinds, calls):
            self.assertEqual(("document.collection", "upsert_documents", "committed", 1, 1, 1), (
                call["writer_family"], call["operation"], call["outcome"],
                call["rows_attempted"], call["sql_calls"], call["commits"],
            ))
            self.assertIsNotNone(call["backend_pid"])
            samples = summarize_market_bar_saves(started, time.time() + 1)[
                "writer_transactions"
            ][kind]["call_samples"]
            self.assertIn(call["call_id"], {sample["db_call_id"] for sample in samples})

    def test_mock_automation_checkpoint_restores_state_without_extra_write(self) -> None:
        account_ref = f"diagnostic-{uuid.uuid4().hex}"
        run_id = f"diagnostic-run-{uuid.uuid4().hex}"
        spec_id = f"diagnostic-spec-{uuid.uuid4().hex}"
        package_hash = uuid.uuid4().hex + uuid.uuid4().hex
        self.document_keys.append((
            "execution_mock_automation_runner_current", account_ref, "current",
        ))
        bundle = SimpleNamespace(
            account_ref=account_ref,
            run_id=run_id,
            automation_context=SimpleNamespace(execution_run_id=run_id, spec_id=spec_id),
        )
        spec = SimpleNamespace(
            account_scope=SimpleNamespace(account_ref=account_ref),
            spec_id=spec_id,
            strategy_ref=f"diagnostic-strategy-{uuid.uuid4().hex}",
            candidate_package_hash=package_hash,
            session_profile="krx-regular/v1",
        )
        candidate_package = SimpleNamespace(candidate_spec={
            "family": BREAKOUT_FAMILY_ID,
            "parameters": default_shadow_breakout_config().to_dict(),
            "session_profile": {"profile": "krx-regular/v1"},
        })
        started = time.time() - 1
        with patch.object(
            ForwardEvaluationRepository, "load_mock_automation_candidate_package",
            return_value=candidate_package,
        ), patch.object(PostgresQueryStore, "load_observation_revisions", return_value=[]), \
                patch.object(MockAutomationRunner, "_apply_new_fills"):
            runner = MockAutomationRunner(self.store, bundle, spec)
            self.assertEqual("WARMUP", runner.status["state"])
            runner._cursor = 42
            runner._fill_cursor = 7
            runner._seen_fill_ids = {"fill-diagnostic-1"}
            runner._state = StrategyState(
                status="candidate", symbol="005930", candidate_key="candidate-diagnostic",
                emitted_candidate_keys=("candidate-diagnostic",),
            )
            runner._status = {
                "state": "STOPPED", "reason": "user_stopped", "orders_enabled": False,
                "pending_intent_id": "intent-diagnostic-1",
            }
            runner._save_checkpoint()

            stored = self.store.load_document(
                "execution_mock_automation_runner_current", account_ref, "current",
            )
            self.assertEqual(42, stored["document"]["input_cursor"])
            self.assertEqual(7, stored["document"]["fill_cursor"])
            self.assertEqual("intent-diagnostic-1", stored["document"]["pending_intent_id"])

            restarted = MockAutomationRunner(self.store, bundle, spec)

        self.assertEqual(42, restarted.status["input_cursor"])
        self.assertEqual(7, restarted._fill_cursor)
        self.assertEqual("candidate", restarted._state.status)
        self.assertEqual(("candidate-diagnostic",), restarted._state.emitted_candidate_keys)
        self.assertEqual({"fill-diagnostic-1"}, restarted._seen_fill_ids)
        self.assertEqual("intent-diagnostic-1", restarted._status["pending_intent_id"])
        self.assertEqual("STOPPED", restarted.status["state"])

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:execution_mock_automation_runner_current" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"][
            "document:execution_mock_automation_runner_current"
        ]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_stock_fundamentals_replay_preserves_stored_response_and_correlates_metrics(self) -> None:
        code = f"DIAG{uuid.uuid4().hex[:16]}"
        self.document_keys.append(("stock_fundamentals", code, "latest"))
        payload = {"mac": "123456", "dstr_rt": "42.5"}
        document = {"owner": code, "key": "latest", "document": {
            "code": code, "market": "KRX",
            "observed_at": datetime.now(KST).isoformat(), "payload": payload,
        }}
        started = time.time() - 1
        self.store.upsert_documents("stock_fundamentals", [document])
        first = self.store.load_document("stock_fundamentals", code, "latest")
        self.store.upsert_documents("stock_fundamentals", [document])
        replay = self.store.load_document("stock_fundamentals", code, "latest")
        self.assertEqual(first, replay)
        self.assertEqual(payload, _stored_market_response(
            self.store, "ka10001", {"stk_cd": code},
        ))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:stock_fundamentals" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:stock_fundamentals"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_nxt_eligibility_replay_preserves_stored_response_and_correlates_metrics(self) -> None:
        code = f"DIAG{uuid.uuid4().hex[:16]}"
        self.document_keys.append(("stock_nxt_eligibility", code, "latest"))
        payload = {"nxtEnable": "Y"}
        document = {"owner": code, "key": "latest", "document": {
            "code": code, "enabled": True,
            "observed_at": datetime.now(KST).isoformat(), "payload": payload,
        }}
        started = time.time() - 1
        self.store.upsert_documents("stock_nxt_eligibility", [document])
        first = self.store.load_document("stock_nxt_eligibility", code, "latest")
        self.store.upsert_documents("stock_nxt_eligibility", [document])
        replay = self.store.load_document("stock_nxt_eligibility", code, "latest")
        self.assertEqual(first, replay)
        self.assertEqual(payload, _stored_market_response(
            self.store, "ka10100", {"stk_cd": code},
        ))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:stock_nxt_eligibility" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"]["document:stock_nxt_eligibility"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_trade_value_comparison_replay_preserves_summary_reader_and_correlates_metrics(self) -> None:
        code = f"D{uuid.uuid4().hex[:11]}"
        owner = f"2026-09-27:{code}"
        self.document_keys.extend(("minute_trade_value_comparisons", owner, minute)
                                  for minute in ("10:01", "10:02"))
        documents = [
            {"owner": owner, "key": "10:01", "document": {
                "trading_date": "2026-09-27", "minute": "10:01", "code": code,
                "realtime_source": "SOR", "realtime_trade_value_million_won": 25,
                "query_components_million_won": {"KRX": 10, "NXT": 5},
                "query_trade_value_million_won": 15, "difference_million_won": 10,
                "difference_percent": 66.666667, "query_scope": "KRX+NXT",
                "compared_at": "2026-09-27T10:02:00+09:00",
            }},
            {"owner": owner, "key": "10:02", "document": {
                "trading_date": "2026-09-27", "minute": "10:02", "code": code,
                "realtime_source": "SOR", "realtime_trade_value_million_won": 12,
                "query_components_million_won": {"KRX": 12},
                "query_trade_value_million_won": 12, "difference_million_won": 0,
                "difference_percent": 0, "query_scope": "KRX",
                "compared_at": "2026-09-27T10:03:00+09:00",
            }},
        ]
        started = time.time() - 1
        self.store.upsert_documents("minute_trade_value_comparisons", documents)
        first = self.store.load_documents("minute_trade_value_comparisons", owner, 10)
        self.store.upsert_documents("minute_trade_value_comparisons", documents)
        replay = self.store.load_documents("minute_trade_value_comparisons", owner, 10)
        self.assertEqual(first, replay)
        self.assertEqual(2, len(replay))
        summary = _trade_value_comparison_summary(replay)
        self.assertEqual((1, 1, 25, 15, 10), (
            summary["complete_count"], summary["partial_count"],
            summary["total_realtime_trade_value_million_won"],
            summary["total_query_trade_value_million_won"],
            summary["total_difference_million_won"],
        ))

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_kind"] == "document:minute_trade_value_comparisons" and call["access_mode"] == "write"
        ]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["sql_calls"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        samples = legacy["writer_transactions"][
            "document:minute_trade_value_comparisons"]["call_samples"]
        self.assertTrue({call["call_id"] for call in calls}.issubset(
            {sample["db_call_id"] for sample in samples}
        ))

    def test_schema_initialize_observes_native_commit_and_migration_rollback(self) -> None:
        from kiwoom_monitor.central_server import database as database_module

        started = time.time() - 1
        self.store.initialize()

        migrations = central_schema_migrations()
        migration_version = migrations[-1].version + 1
        migration_name = f"diagnostic_failure_{uuid.uuid4().hex}"
        table_name = f"diagnostic_migration_{uuid.uuid4().hex}"
        failing_migration = CentralSchemaMigration(
            migration_version, migration_name, (),
            (f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY)", "SELECT 1/0"),
        )

        def cleanup_failed_migration_table() -> None:
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {table_name}")

        self.addCleanup(cleanup_failed_migration_table)
        with patch.object(
            database_module, "central_schema_migrations",
            return_value=(*migrations, failing_migration),
        ):
            with self.assertRaisesRegex(Exception, "division by zero"):
                self.store.initialize()

        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s)", (table_name,))
            self.assertIsNone(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM central_schema_migrations WHERE version=%s AND name=%s",
                (migration_version, migration_name),
            )
            self.assertEqual(0, cursor.fetchone()[0])

        calls = [
            call for call in summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
            if call["writer_family"] == "schema.migration"
            and call["writer_kind"] == "central_schema"
        ]
        self.assertEqual(2, len(calls))
        self.assertEqual(2, len({call["call_id"] for call in calls}))
        self.assertEqual(["committed", "rolled_back"], [call["outcome"] for call in calls])
        self.assertEqual([1, 0], [call["commits"] for call in calls])
        self.assertEqual([0, 1], [call["rollbacks"] for call in calls])
        self.assertTrue(all(call["backend_pid"] is not None for call in calls))
        self.assertEqual([], [
            error for call in calls for error in call["errors"]
            if error["stage"] == "observer"
        ])

    def test_native_context_commit_and_body_error_preserve_driver_boundary(self) -> None:
        committed_key, rolled_back_key = self._key(), self._key()

        def insert_row(cursor, key: str) -> None:
            cursor.execute(
                "INSERT INTO central_api_query_cache"
                "(cache_key,api_id,expires_at,payload_json,has_next,next_key) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                (key, "ka10081", time.time() + 60, "{}", False, ""),
            )

        success = open_observed_connection(
            self.store._connect,
            DBWriterContext("test.native_context", "cache_row", "commit", 1),
        )
        with success as connection, connection.cursor() as cursor:
            insert_row(cursor, committed_key)
        self.assertTrue(success._raw.closed)

        failure = open_observed_connection(
            self.store._connect,
            DBWriterContext("test.native_context", "cache_row", "rollback", 1),
        )
        with self.assertRaisesRegex(ValueError, "body failed"):
            with failure as connection, connection.cursor() as cursor:
                insert_row(cursor, rolled_back_key)
                raise ValueError("body failed")
        self.assertTrue(failure._raw.closed)
        self.assertIsNotNone(self.store.load_query(committed_key))
        self.assertIsNone(self.store.load_query(rolled_back_key))

        calls = summarize_db_calls(time.time() - 30, time.time() + 1, mode="raw")["calls"]
        observed = {call["call_id"]: call for call in calls}
        for connection, outcome, commits, rollbacks in (
            (success, "committed", 1, 0),
            (failure, "rolled_back", 0, 1),
        ):
            call = observed[connection.call_id]
            self.assertEqual((outcome, commits, rollbacks),
                             (call["outcome"], call["commits"], call["rollbacks"]))
            self.assertIsNotNone(call["backend_pid"])
            self.assertIsNotNone(call["close_ms"])

    def test_native_context_deferred_commit_failure_preserves_open_connection(self) -> None:
        connection = open_observed_connection(
            self.store._connect,
            DBWriterContext("test.native_context", "temp_constraint", "commit_failure", 2),
        )
        try:
            with self.assertRaises(Exception):
                with connection as observed, observed.cursor() as cursor:
                    cursor.execute(
                        "CREATE TEMP TABLE diagnostic_context_commit_failure "
                        "(value INTEGER UNIQUE DEFERRABLE INITIALLY DEFERRED) ON COMMIT DROP"
                    )
                    cursor.execute(
                        "INSERT INTO diagnostic_context_commit_failure(value) VALUES(1),(1)"
                    )
            self.assertFalse(connection._raw.closed)
            calls = summarize_db_calls(time.time() - 30, time.time() + 1, mode="raw")["calls"]
            call = next(call for call in calls if call["call_id"] == connection.call_id)
            self.assertEqual(("unknown", 0, 0),
                             (call["outcome"], call["commits"], call["rollbacks"]))
            self.assertIsNone(call["close_ms"])
        finally:
            connection.close()

    def test_cache_replay_and_failure_preserve_final_rows(self) -> None:
        key = self._key()
        started = time.time() - 1
        value = StoredQuery({"message": "stable"}, False, "")
        self.store.save_query(key, "ka10081", time.time() + 60, value)
        self.store.save_query(key, "ka10081", time.time() + 60, value)
        self.assertEqual(value, self.store.load_query(key))

        failed_key = self._key()
        context = DBWriterContext("rest.query_cache", "query_cache", "failure_probe", 1)
        connection = open_observed_connection(self.store._connect, context)
        try:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO central_api_query_cache"
                               "(cache_key,api_id,expires_at,payload_json,has_next,next_key) "
                               "VALUES(%s,%s,%s,%s,%s,%s)",
                               (failed_key, "ka10081", time.time() + 60, "{}", False, ""))
                with self.assertRaises(Exception):
                    cursor.execute("SELECT * FROM central_diagnostic_missing_table")
        finally:
            connection.close()
        self.assertIsNone(self.store.load_query(failed_key))
        calls = summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
        writer_calls = [call for call in calls if call.get("access_mode", "write") == "write"]
        self.assertEqual(["committed", "committed", "closed_uncommitted"],
                         [call["outcome"] for call in writer_calls])
        self.assertTrue(all(call["backend_pid"] for call in writer_calls))
        self.assertEqual(0, writer_calls[-1]["rollbacks"])
        legacy = summarize_market_bar_saves(started, time.time() + 1)
        legacy_calls = {
            sample["db_call_id"]: sample
            for sample in legacy["writer_transactions"]["query_cache"]["call_samples"]
        }
        for call in writer_calls[:2]:
            sample = legacy_calls[call["call_id"]]
            # The old outer timers contain the new inner timers. Allow their
            # integer rounding (twice for the two SQL execute phases).
            for old_field, new_field, rounding_ms in (
                ("connect_ms", "connection_acquire_ms", 1.0),
                ("execute_ms", "execute_ms", 2.0),
                ("commit_ms", "commit_ms", 1.0),
                ("elapsed_ms", "total_ms", 1.0),
            ):
                self.assertIsNotNone(sample[old_field])
                self.assertIsNotNone(call[new_field])
                self.assertGreaterEqual(
                    float(sample[old_field]) + rounding_ms, float(call[new_field]),
                    (call["call_id"], old_field, new_field),
                )
            print(
                "paired_db_call",
                {"call_id": call["call_id"],
                 "legacy_commit_ms": sample["commit_ms"],
                 "common_commit_ms": call["commit_ms"],
                 "legacy_total_ms": sample["elapsed_ms"],
                 "common_total_ms": call["total_ms"]},
                flush=True,
            )

    def test_query_cache_reader_keeps_native_context_and_separates_metrics(self) -> None:
        hit_key, expired_key, missing_key = self._key(), self._key(), self._key()
        value = StoredQuery({"rows": ["reader"]}, True, "next")
        self.store.save_query(hit_key, "ka10081", time.time() + 60, value)
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO central_api_query_cache"
                "(cache_key,api_id,expires_at,payload_json,has_next,next_key) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                (expired_key, "ka10081", time.time() - 60, "{}", False, ""),
            )

        started = time.time()
        self.assertEqual(value, self.store.load_query(hit_key))
        self.assertIsNone(self.store.load_query(expired_key))
        self.assertIsNone(self.store.load_query(missing_key))
        calls = summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
        self.assertEqual(3, len(calls))
        self.assertTrue(all(
            call["access_mode"] == "read"
            and call["writer_family"] == "read.query_cache"
            and call["writer_kind"] == "query_cache"
            and call["operation"] == "load_query"
            and call["outcome"] == "committed"
            and call["transactions"] == 1
            and call["commits"] == 1
            and call["sql_calls"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        summary = summarize_db_calls(started, time.time() + 1)
        self.assertEqual({}, summary["writers"])
        reader = summary["readers"]["read.query_cache/query_cache"]
        self.assertEqual((3, 3, 3), (reader["calls"], reader["transactions"], reader["commits"]))

    def test_document_collection_reader_keeps_native_context_and_kind_metrics(self) -> None:
        collection = "diagnostic_reader_documents"
        owner, key = f"DIAG-{uuid.uuid4().hex}", "reader-row"
        self.document_keys.append((collection, owner, key))
        document = {"value": 17, "label": "reader-pilot"}
        self.store.upsert_documents(collection, [{
            "owner": owner, "key": key, "document": document,
        }])

        started = time.time()
        self.assertEqual([{
            "owner": owner, "key": key, "document": document,
        }], [
            {name: value for name, value in row.items() if name != "updated_at"}
            for row in self.store.load_documents(collection, owner, 10)
        ])
        self.assertEqual([], self.store.load_documents(collection, f"{owner}-missing", 10))
        calls = summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["access_mode"] == "read"
            and call["writer_family"] == "read.document_collection"
            and call["writer_kind"] == f"document:{collection}"
            and call["operation"] == "load_documents"
            and call["outcome"] == "committed"
            and call["transactions"] == 1
            and call["commits"] == 1
            and call["sql_calls"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        summary = summarize_db_calls(started, time.time() + 1)
        self.assertEqual({}, summary["writers"])
        reader = summary["readers"][f"read.document_collection/document:{collection}"]
        self.assertEqual((2, 2, 2), (reader["calls"], reader["transactions"], reader["commits"]))

    def test_document_collection_single_reader_keeps_native_context_and_kind_metrics(self) -> None:
        collection = "diagnostic_single_reader_documents"
        owner, key = f"DIAG-{uuid.uuid4().hex}", "single-reader-row"
        self.document_keys.append((collection, owner, key))
        document = {"value": 23, "label": "single-reader-pilot"}
        self.store.upsert_documents(collection, [{
            "owner": owner, "key": key, "document": document,
        }])

        started = time.time()
        self.assertEqual(document, self.store.load_document(collection, owner, key)["document"])
        self.assertIsNone(self.store.load_document(collection, f"{owner}-missing", key))
        calls = summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
        self.assertEqual(2, len(calls))
        self.assertTrue(all(
            call["access_mode"] == "read"
            and call["writer_family"] == "read.document_collection"
            and call["writer_kind"] == f"document:{collection}:single"
            and call["operation"] == "load_document"
            and call["outcome"] == "committed"
            and call["transactions"] == 1
            and call["commits"] == 1
            and call["sql_calls"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        summary = summarize_db_calls(started, time.time() + 1)
        self.assertEqual({}, summary["writers"])
        reader = summary["readers"][f"read.document_collection/document:{collection}:single"]
        self.assertEqual((2, 2, 2), (reader["calls"], reader["transactions"], reader["commits"]))

    def test_forward_and_active_settings_document_writers_keep_independent_observed_transactions(self) -> None:
        collections = (
            "news_watchlist", "news_automation_settings", "server_operational_settings",
            "execution_forward_profiles", "execution_forward_reports",
            "execution_strategy_stage_revisions", "execution_feedback_evidence",
            "execution_feedback_reviews", "execution_feedback_improvement_proposals",
            "execution_feedback_strategy_versions", "execution_feedback_revalidation_requests",
            "execution_feedback_revalidation_receipts", "execution_mock_automation_specs",
            "execution_mock_automation_admissions", "execution_mock_automation_admission_by_spec",
            "execution_mock_automation_lease_receipts",
            "execution_mock_automation_lease_by_admission",
            "execution_mock_automation_candidate_packages",
            "execution_mock_automation_eligibility_policies",
            "execution_mock_automation_eligibility_receipts",
        )
        entries = []
        for collection in collections:
            owner, key = f"DIAG-{uuid.uuid4().hex}", "writer-batch"
            self.document_keys.append((collection, owner, key))
            document = {"collection": collection, "value": uuid.uuid4().hex}
            entries.append((collection, owner, key, document))

        started = time.time()
        for collection, owner, key, document in entries:
            self.store.upsert_documents(collection, [{
                "owner": owner, "key": key, "document": document,
            }])
        for collection, owner, key, document in entries:
            stored = self.store.load_document(collection, owner, key)
            self.assertEqual(document, stored["document"])

        calls = summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
        self.assertEqual(2 * len(collections), len(calls))
        writer_calls = [call for call in calls if call["access_mode"] == "write"]
        reader_calls = [call for call in calls if call["access_mode"] == "read"]
        self.assertEqual(len(collections), len(writer_calls))
        self.assertEqual(len(collections), len(reader_calls))
        self.assertEqual(len(calls), len({call["call_id"] for call in calls}))
        self.assertEqual({f"document:{collection}" for collection in collections}, {
            call["writer_kind"] for call in writer_calls
        })
        self.assertEqual({f"document:{collection}:single" for collection in collections}, {
            call["writer_kind"] for call in reader_calls
        })
        self.assertTrue(all(
            call["writer_family"] == "document.collection"
            and call["operation"] == "upsert_documents"
            and call["outcome"] == "committed"
            and call["rows_attempted"] == 1
            and call["transactions"] == 1
            and call["commits"] == 1
            and call["sql_calls"] == 1
            and call["backend_pid"] is not None
            for call in writer_calls
        ))
        self.assertTrue(all(
            call["writer_family"] == "read.document_collection"
            and call["operation"] == "load_document"
            and call["outcome"] == "committed"
            and call["transactions"] == 1
            and call["commits"] == 1
            and call["sql_calls"] == 1
            and call["backend_pid"] is not None
            for call in reader_calls
        ))

    def test_market_bar_readers_keep_native_context_and_separate_kinds(self) -> None:
        code, missing_code = f"DIAG{uuid.uuid4().hex[:16]}", f"DIAG{uuid.uuid4().hex[:16]}"
        self.query_bar_codes.extend((code, missing_code))
        day = "2099-01-09"
        minute = {
            "trading_date": day, "minute": "10:00", "code": code, "market": "KRX",
            "open": 100, "high": 110, "low": 90, "close": 105,
            "volume": 10, "trade_value_million_won": 1, "updated_at": 1_790_000_000.0,
        }
        daily = {key: value for key, value in minute.items() if key != "minute"}
        self.store.replace_minute_bars([minute])
        self.store.replace_daily_bars([daily])

        started = time.time()
        [saved_minute] = self.store.load_minute_bars(code, day, "KRX")
        [saved_daily] = self.store.load_daily_bars(code, "KRX", 10)
        self.assertEqual((105, 10), (saved_minute["close"], saved_minute["volume"]))
        self.assertEqual((105, 10), (saved_daily["close"], saved_daily["volume"]))
        self.assertEqual([], self.store.load_minute_bars(missing_code, day, "KRX"))
        self.assertEqual([], self.store.load_daily_bars(missing_code, "KRX", 10))

        calls = summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
        self.assertEqual(4, len(calls))
        self.assertTrue(all(
            call["access_mode"] == "read"
            and call["writer_family"] == "read.market_bars"
            and call["operation"] in {"load_minute_bars", "load_daily_bars"}
            and call["outcome"] == "committed"
            and call["transactions"] == 1
            and call["commits"] == 1
            and call["sql_calls"] == 1
            and call["backend_pid"] is not None
            for call in calls
        ))
        self.assertEqual({"minute_bar", "daily_bar"}, {call["writer_kind"] for call in calls})
        summary = summarize_db_calls(started, time.time() + 1)
        self.assertEqual({}, summary["writers"])
        self.assertEqual(2, summary["readers"]["read.market_bars/minute_bar"]["calls"])
        self.assertEqual(2, summary["readers"]["read.market_bars/daily_bar"]["calls"])

    def test_observation_revision_readers_keep_native_context_and_separate_kinds(self) -> None:
        code = f"DIAG{uuid.uuid4().hex[:16]}"
        self.query_bar_codes.append(code)
        minute = {
            "trading_date": "2099-01-09", "minute": "10:00", "code": code,
            "market": "KRX", "open": 100, "high": 110, "low": 90,
            "close": 105, "volume": 10, "trade_value_million_won": 1,
            "updated_at": 1_790_000_000.0,
        }
        observation = minute_bar_observation(
            minute, origin=ObservationOrigin.QUERY,
            completeness=DataCompleteness.COMPLETE,
            source="diagnostic-observation-revision-reader",
            value_kind=DataValueKind.ESTIMATED,
        )
        self.store.replace_minute_bars(
            [minute], observations=[(bar_observation_key(observation), observation)],
        )

        started = time.time()
        [revision] = self.store.load_observation_revisions(
            "minute_bar", f"{code}:KRX", limit=10,
        )
        incremental = self.store.load_observation_revisions_after(
            int(revision["accepted_sequence"]) - 1, ("minute_bar",), limit=10,
        )
        self.assertEqual(code, revision["payload"]["code"])
        self.assertEqual([revision["revision_id"]], [
            row["revision_id"] for row in incremental
            if row["subject"] == f"{code}:KRX"
        ])

        calls = summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
        reader_calls = [call for call in calls
                        if call["writer_family"] == "read.observation_revisions"]
        self.assertEqual(2, len(reader_calls))
        self.assertEqual({"observation_revision", "observation_revisions_after"}, {
            call["writer_kind"] for call in reader_calls
        })
        self.assertTrue(all(
            call["access_mode"] == "read"
            and call["outcome"] == "committed"
            and call["transactions"] == 1
            and call["commits"] == 1
            and call["sql_calls"] == (4 if call["writer_kind"] == "observation_revisions_after" else 1)
            and call["backend_pid"] is not None
            for call in reader_calls
        ))
        summary = summarize_db_calls(started, time.time() + 1)
        self.assertEqual({}, summary["writers"])
        self.assertEqual(1, summary["readers"][
            "read.observation_revisions/observation_revision"]["calls"])
        self.assertEqual(1, summary["readers"][
            "read.observation_revisions/observation_revisions_after"]["calls"])
        frontier = summary["readers"][
            "read.observation_revisions/observation_revisions_after"]["phase_diagnostics"]["observation_delivery"]
        self.assertEqual(1, frontier["executions"])
        self.assertEqual({"not_requested": 1}, frontier["sampling_status"])
        self.assertEqual({}, frontier["wait_samples"])

    def test_active_readers_batch_preserve_native_context_and_separate_metrics(self) -> None:
        snapshot_subject = f"DIAG-{uuid.uuid4().hex}"
        snapshot_key = "2099-01-09T10:00:00+09:00"
        snapshot_payload = {"query_type": snapshot_subject, "items": [{"stk_cd": "005930"}]}
        available_at = datetime.fromisoformat(snapshot_key)
        self.dataset_snapshot_keys.append(("ranking", snapshot_subject, snapshot_key))
        self.store.save_dataset_snapshot(
            "ranking", snapshot_subject, snapshot_key, snapshot_payload,
            observation=ranking_observation(
                snapshot_subject, snapshot_key, snapshot_payload, available_at,
                source="diagnostic-active-reader-batch",
            ),
        )

        monitor_id = f"diagnostic-shadow-read-{uuid.uuid4().hex}"
        self.shadow_monitor_ids.append(monitor_id)
        decision = {
            "decision_id": f"diagnostic-decision-{uuid.uuid4().hex}",
            "decided_at": "2099-01-09T00:00:00+00:00", "final_action": "ENTER",
        }
        candidate = {
            "event_id": f"diagnostic-event-{uuid.uuid4().hex}",
            "available_at": "2099-01-09T00:00:00+00:00", "symbol": "005930",
        }
        self.store.save_shadow_monitor_state(monitor_id, {"cursor": 23})
        self.store.save_shadow_evaluation(
            monitor_id, decision, candidate, "2099-01-09T00:01:00+00:00",
        )
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT accepted_sequence FROM central_shadow_candidate_events WHERE event_id=%s",
                (candidate["event_id"],),
            )
            [event_sequence] = cursor.fetchone()

        started = time.time()
        [snapshot] = self.store.load_dataset_snapshots("ranking", snapshot_subject, 10)
        self.assertEqual(snapshot_payload, snapshot["payload"])
        self.assertEqual([], self.store.load_dataset_snapshots("ranking", f"{snapshot_subject}-missing", 10))
        metadata_rows = self.store.load_market_data_metadata_range(
            MarketDatasetKind.CANDIDATE_SET, snapshot_subject,
            available_at - timedelta(seconds=1), available_at + timedelta(seconds=1),
        )
        self.assertEqual([snapshot_key], [row.observation_key for row in metadata_rows])
        self.assertEqual([], self.store.load_market_data_metadata_range(
            MarketDatasetKind.CANDIDATE_SET, f"{snapshot_subject}-missing",
            available_at - timedelta(seconds=1), available_at + timedelta(seconds=1),
        ))
        self.assertEqual({"cursor": 23}, self.store.load_shadow_monitor_state(monitor_id))
        self.assertIsNone(self.store.load_shadow_monitor_state(f"{monitor_id}-missing"))
        page = self.store.load_shadow_candidates(int(event_sequence) - 1, 10)
        self.assertEqual([candidate["event_id"]], [row["event_id"] for row in page["events"]])
        empty = self.store.load_shadow_candidates(int(event_sequence), 10)
        self.assertEqual([], empty["events"])

        calls = summarize_db_calls(started, time.time() + 1, mode="raw")["calls"]
        reader_families = {
            "read.dataset_snapshots", "read.market_data_metadata", "read.shadow_monitor",
        }
        reader_calls = [call for call in calls if call["writer_family"] in reader_families]
        self.assertEqual(8, len(reader_calls))
        self.assertEqual(10, sum(call["sql_calls"] for call in reader_calls))
        self.assertTrue(all(
            call["access_mode"] == "read"
            and call["outcome"] == "committed"
            and call["transactions"] == 1
            and call["commits"] == 1
            and call["backend_pid"] is not None
            for call in reader_calls
        ))
        summary = summarize_db_calls(started, time.time() + 1)
        self.assertEqual({}, summary["writers"])
        self.assertEqual(2, summary["readers"]["read.dataset_snapshots/dataset:ranking"]["calls"])
        self.assertEqual(2, summary["readers"]["read.market_data_metadata/metadata_range"]["calls"])
        self.assertEqual(2, summary["readers"]["read.shadow_monitor/monitor_state"]["calls"])
        self.assertEqual(2, summary["readers"]["read.shadow_monitor/candidate_events"]["calls"])

    def test_two_connections_commit_independently(self) -> None:
        first_key, second_key = self._key(), self._key()
        ready = Barrier(2, timeout=10)

        def write(key: str) -> int:
            context = DBWriterContext("rest.query_cache", "query_cache", "parallel_probe", 1)
            connection = open_observed_connection(self.store._connect, context)
            try:
                with connection.cursor() as cursor:
                    ready.wait()
                    cursor.execute("INSERT INTO central_api_query_cache"
                                   "(cache_key,api_id,expires_at,payload_json,has_next,next_key) "
                                   "VALUES(%s,%s,%s,%s,%s,%s)",
                                   (key, "ka10081", time.time() + 60, "{}", False, ""))
                connection.commit()
                return int(connection.backend_pid)
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(write, key) for key in (first_key, second_key)]
            pids = [future.result(timeout=20) for future in futures]
        self.assertEqual(2, len(set(pids)))
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM central_api_query_cache WHERE cache_key=ANY(%s)",
                           ([first_key, second_key],))
            self.assertEqual(2, cursor.fetchone()[0])

    def test_failed_connection_discard_does_not_rollback_peer_commit(self) -> None:
        committed_key, discarded_key = self._key(), self._key()
        ready = Barrier(2, timeout=10)

        def write(key: str, fail: bool) -> None:
            connection = open_observed_connection(
                self.store._connect,
                DBWriterContext("rest.query_cache", "query_cache", "independent_probe", 1),
            )
            try:
                with connection.cursor() as cursor:
                    ready.wait()
                    cursor.execute("INSERT INTO central_api_query_cache"
                                   "(cache_key,api_id,expires_at,payload_json,has_next,next_key) "
                                   "VALUES(%s,%s,%s,%s,%s,%s)",
                                   (key, "ka10081", time.time() + 60, "{}", False, ""))
                    if fail:
                        with self.assertRaises(Exception):
                            cursor.execute("SELECT * FROM central_diagnostic_missing_table")
                    else:
                        connection.commit()
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(write, committed_key, False),
                       executor.submit(write, discarded_key, True)]
            for future in futures:
                future.result(timeout=20)
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT cache_key FROM central_api_query_cache WHERE cache_key=ANY(%s)",
                           ([committed_key, discarded_key],))
            self.assertEqual([(committed_key,)], cursor.fetchall())


if __name__ == "__main__":
    unittest.main()
