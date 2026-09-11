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
