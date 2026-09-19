from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.contracts import API_VERSION, SCHEMA_VERSION, ServerCapabilities, health_document
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings


class CentralServerSettingsTests(unittest.TestCase):
    def test_synology_compose_passes_all_query_set_controls_with_defaults(self) -> None:
        compose = (
            Path(__file__).resolve().parents[2] / "deploy" / "synology" / "docker-compose.yml"
        ).read_text(encoding="utf-8")
        expected = {
            "NEWS_QUERY_SET_ENABLED": "true",
            "NEWS_QUERY_SET": "증권,코스피,코스닥,상장사,수주 계약,유상증자,인수합병,실적 전망,최대주주",
            "NEWS_QUERY_SET_REFRESH_SECONDS": "300",
            "NEWS_REQUEST_HARD_LIMIT": "24000",
            "NEWS_WATCHLIST_REQUEST_LIMIT": "8000",
            "NEWS_QUERY_SET_REQUEST_LIMIT": "16000",
            "MARKET_EVENT_COLLECTION_ENABLED": "true",
            "HOT_COHORT_CONDITION_NAME": "",
            "HOT_COHORT_CONDITION_SUBSTRING": "15%",
            "SHADOW_CANDIDATE_ENABLED": "0",
            "SHADOW_CANDIDATE_POLL_SECONDS": "2",
            "SHADOW_CANDIDATE_UNIVERSE_MAX_AGE_SECONDS": "0",
            "MOCK_ACCOUNT_MONITOR_ENABLED": "0",
            "MOCK_ORDER_TRANSPORT_ENABLED": "0",
            "KIWOOM_MOCK_APP_KEY": "",
            "KIWOOM_MOCK_SECRET_KEY": "",
            "MOCK_ACCOUNT_REF": "",
            "ACCOUNT_IDENTITY_HMAC_KEY": "",
            "ACCOUNT_IDENTITY_REGISTRY_ENABLED": "0",
            "MOCK_EXECUTION_RUN_ID": "",
        }
        for key, default in expected.items():
            self.assertIn(f"${{{key}:-{default}}}", compose)

    def test_loads_required_environment(self) -> None:
        with patch.dict(os.environ, {
            "KIWOOM_SERVER_DATABASE_URL": "postgresql://monitor:test@db/monitor",
            "MONITOR_SERVER_ACCESS_TOKEN": "private-token",
            "KIWOOM_SERVER_PORT": "9000",
            "KIWOOM_ENVIRONMENT": "mock",
            "NAVER_NEWS_CLIENT_ID": "news-id",
            "NAVER_NEWS_CLIENT_SECRET": "news-secret",
        }, clear=True):
            settings = CentralServerSettings.from_environment()
        self.assertEqual(9000, settings.port)
        self.assertEqual("mock", settings.kiwoom_environment)
        self.assertTrue(settings.news_configured)
        self.assertTrue(settings.news_query_set_enabled)
        self.assertEqual(9, len(settings.parsed_news_query_set()))
        self.assertEqual(24_000, settings.news_request_hard_limit)
        self.assertEqual((8_000, 16_000), (settings.news_watchlist_request_limit,
                                          settings.news_query_set_request_limit))
        self.assertTrue(settings.market_event_collection_enabled)
        self.assertEqual("15%", settings.hot_cohort_condition_substring)

    def test_rejects_missing_server_secret(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "ACCESS_TOKEN"):
                CentralServerSettings.from_environment()

    def test_local_database_defaults_to_existing_monitor_database(self) -> None:
        with patch.dict(os.environ, {"MONITOR_SERVER_ACCESS_TOKEN": "token"}, clear=True):
            settings = CentralServerSettings.from_environment()
        self.assertEqual("sqlite:///data/monitor.sqlite3", settings.database_url)

    def test_legacy_server_token_name_remains_compatible(self) -> None:
        with patch.dict(os.environ, {"KIWOOM_SERVER_ACCESS_TOKEN": "legacy-token"}, clear=True):
            settings = CentralServerSettings.from_environment()
        self.assertEqual("legacy-token", settings.access_token)

    def test_new_server_token_name_takes_priority(self) -> None:
        with patch.dict(os.environ, {
            "MONITOR_SERVER_ACCESS_TOKEN": "new-token",
            "KIWOOM_SERVER_ACCESS_TOKEN": "legacy-token",
        }, clear=True):
            settings = CentralServerSettings.from_environment()
        self.assertEqual("new-token", settings.access_token)

    def test_news_history_worker_can_be_disabled(self) -> None:
        with patch.dict(os.environ, {
            "MONITOR_SERVER_ACCESS_TOKEN": "token", "NEWS_HISTORY_JOBS_ENABLED": "false",
        }, clear=True):
            settings = CentralServerSettings.from_environment()
        self.assertFalse(settings.news_history_jobs_enabled)

    def test_market_event_collection_and_condition_name_are_configurable(self) -> None:
        with patch.dict(os.environ, {
            "MONITOR_SERVER_ACCESS_TOKEN": "token", "MARKET_EVENT_COLLECTION_ENABLED": "false",
            "HOT_COHORT_CONDITION_NAME": "정확한 15% 조건", "HOT_COHORT_CONDITION_SUBSTRING": "급등",
        }, clear=True):
            settings = CentralServerSettings.from_environment()
        self.assertFalse(settings.market_event_collection_enabled)
        self.assertEqual("정확한 15% 조건", settings.hot_cohort_condition_name)
        self.assertEqual("급등", settings.hot_cohort_condition_substring)

    def test_research_observation_history_can_be_disabled(self) -> None:
        with patch.dict(os.environ, {
            "MONITOR_SERVER_ACCESS_TOKEN": "token",
            "RESEARCH_OBSERVATION_HISTORY_ENABLED": "false",
        }, clear=True):
            settings = CentralServerSettings.from_environment()
        self.assertFalse(settings.research_observation_history_enabled)

    def test_shadow_candidate_requires_explicit_strategy_and_freshness(self) -> None:
        with patch.dict(os.environ, {
            "MONITOR_SERVER_ACCESS_TOKEN": "token", "SHADOW_CANDIDATE_ENABLED": "1",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "SHADOW_CANDIDATE_CONFIG_JSON"):
                CentralServerSettings.from_environment()

    def test_shadow_candidate_explicit_configuration_is_loaded(self) -> None:
        with patch.dict(os.environ, {
            "MONITOR_SERVER_ACCESS_TOKEN": "token", "SHADOW_CANDIDATE_ENABLED": "1",
            "SHADOW_CANDIDATE_CONFIG_JSON": "{\"strategy_version\":\"v1\"}",
            "SHADOW_CANDIDATE_UNIVERSE_MAX_AGE_SECONDS": "90",
            "SHADOW_CANDIDATE_POLL_SECONDS": "1.5",
        }, clear=True):
            settings = CentralServerSettings.from_environment()
        self.assertTrue(settings.shadow_candidate_enabled)
        self.assertEqual(90, settings.shadow_candidate_universe_max_age_seconds)
        self.assertEqual(1.5, settings.shadow_candidate_poll_seconds)

    def test_mock_account_monitor_requires_dedicated_credentials_and_identifiers(self) -> None:
        with patch.dict(os.environ, {
            "MONITOR_SERVER_ACCESS_TOKEN": "token", "MOCK_ACCOUNT_MONITOR_ENABLED": "1",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "KIWOOM_MOCK_APP_KEY"):
                CentralServerSettings.from_environment()

        with patch.dict(os.environ, {
            "MONITOR_SERVER_ACCESS_TOKEN": "token", "KIWOOM_ENVIRONMENT": "real",
            "MOCK_ACCOUNT_MONITOR_ENABLED": "1",
            "KIWOOM_MOCK_APP_KEY": "mock-key", "KIWOOM_MOCK_SECRET_KEY": "mock-secret",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "MOCK_ACCOUNT_REF"):
                CentralServerSettings.from_environment()

    def test_mock_account_monitor_configuration_is_explicit(self) -> None:
        with patch.dict(os.environ, {
            "MONITOR_SERVER_ACCESS_TOKEN": "token", "KIWOOM_ENVIRONMENT": "real",
            "MOCK_ACCOUNT_MONITOR_ENABLED": "1", "MOCK_ACCOUNT_REF": "opaque-ref",
            "MOCK_EXECUTION_RUN_ID": "forward-run",
            "KIWOOM_MOCK_APP_KEY": "mock-key", "KIWOOM_MOCK_SECRET_KEY": "mock-secret",
        }, clear=True):
            settings = CentralServerSettings.from_environment()
        self.assertTrue(settings.mock_account_monitor_enabled)
        self.assertTrue(settings.kiwoom_mock_configured)
        self.assertFalse(settings.kiwoom_configured)
        self.assertEqual("real", settings.kiwoom_environment)
        self.assertEqual(("opaque-ref", "forward-run"), (
            settings.mock_account_ref, settings.mock_execution_run_id,
        ))

    def test_enabled_account_registry_requires_a_protected_hmac_key(self) -> None:
        base = {
            "MONITOR_SERVER_ACCESS_TOKEN": "token", "MOCK_ACCOUNT_MONITOR_ENABLED": "1",
            "MOCK_ACCOUNT_REF": "opaque-ref", "MOCK_EXECUTION_RUN_ID": "forward-run",
            "KIWOOM_MOCK_APP_KEY": "mock-key", "KIWOOM_MOCK_SECRET_KEY": "mock-secret",
            "ACCOUNT_IDENTITY_REGISTRY_ENABLED": "1",
        }
        with patch.dict(os.environ, base, clear=True):
            with self.assertRaisesRegex(ValueError, "ACCOUNT_IDENTITY_HMAC_KEY"):
                CentralServerSettings.from_environment()
        with patch.dict(os.environ, {**base, "ACCOUNT_IDENTITY_HMAC_KEY": "k" * 32}, clear=True):
            settings = CentralServerSettings.from_environment()
        self.assertTrue(settings.account_identity_registry_enabled)
        self.assertEqual(b"k" * 32, settings.account_identity_key())

    def test_mock_order_transport_requires_and_reuses_mock_account_boundary(self) -> None:
        with patch.dict(os.environ, {
            "MONITOR_SERVER_ACCESS_TOKEN": "token",
            "MOCK_ORDER_TRANSPORT_ENABLED": "1",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "MOCK_ACCOUNT_MONITOR_ENABLED"):
                CentralServerSettings.from_environment()

        with patch.dict(os.environ, {
            "MONITOR_SERVER_ACCESS_TOKEN": "token",
            "MOCK_ACCOUNT_MONITOR_ENABLED": "1", "MOCK_ORDER_TRANSPORT_ENABLED": "1",
            "KIWOOM_MOCK_APP_KEY": "mock-key", "KIWOOM_MOCK_SECRET_KEY": "mock-secret",
            "MOCK_ACCOUNT_REF": "opaque-ref", "MOCK_EXECUTION_RUN_ID": "forward-run",
        }, clear=True):
            settings = CentralServerSettings.from_environment()
        self.assertTrue(settings.mock_account_monitor_enabled)
        self.assertTrue(settings.mock_order_transport_enabled)

    def test_query_set_can_be_disabled_and_scope_budgets_are_clamped_to_hard_limit(self) -> None:
        with patch.dict(os.environ, {
            "MONITOR_SERVER_ACCESS_TOKEN": "token", "NEWS_QUERY_SET_ENABLED": "false",
            "NEWS_QUERY_SET": "증권,수주 계약,증권", "NEWS_REQUEST_HARD_LIMIT": "100",
            "NEWS_WATCHLIST_REQUEST_LIMIT": "80", "NEWS_QUERY_SET_REQUEST_LIMIT": "80",
            "NEWS_PROCESSING_EXCLUDED_PROVIDERS": "thebell.co.kr,연합인포맥스,thebell.co.kr",
        }, clear=True):
            settings = CentralServerSettings.from_environment()
        self.assertFalse(settings.news_query_set_enabled)
        self.assertEqual(("증권", "수주 계약"), settings.parsed_news_query_set())
        self.assertEqual(
            ("thebell.co.kr", "연합인포맥스"),
            settings.parsed_news_processing_excluded_providers(),
        )
        self.assertEqual((100, 80, 20), (settings.news_request_hard_limit,
                                       settings.news_watchlist_request_limit,
                                       settings.news_query_set_request_limit))


class DataSourceConfigTests(unittest.TestCase):
    def test_round_trip_personal_server(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = DataSourceConfig(Path(directory) / "server.json")
            expected = DataSourceSettings("personal_server", "https://nas.example.test/", "token", True)
            config.save(expected)
            actual = config.load()
        self.assertEqual("https://nas.example.test", actual.server_url)
        self.assertEqual("personal_server", actual.mode)
        self.assertTrue(actual.local_fallback_enabled)

    def test_round_trip_local_server(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = DataSourceConfig(Path(directory) / "server.json")
            expected = DataSourceSettings("local_server", "http://127.0.0.1:8787", "token")
            config.save(expected)
            self.assertEqual(expected, config.load())

    def test_local_is_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(DataSourceSettings(), DataSourceConfig(Path(directory) / "missing.json").load())


class ServerContractTests(unittest.TestCase):
    def test_contract_is_versioned(self) -> None:
        document = ServerCapabilities().as_document()
        self.assertEqual(API_VERSION, document["api_version"])
        self.assertEqual(SCHEMA_VERSION, document["schema_version"])
        self.assertEqual("ok", health_document()["status"])


if __name__ == "__main__":
    unittest.main()
