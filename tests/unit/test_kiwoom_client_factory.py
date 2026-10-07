from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.client_factory import create_query_client
from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.failover_client import FailoverKiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.validation_client import ParallelValidationClient
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig
from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings


class KiwoomClientFactoryTests(unittest.TestCase):
    def test_personal_server_does_not_load_local_api_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "data_source.json"
            DataSourceConfig(source_path).save(DataSourceSettings(
                "personal_server", "https://nas.example.test", "server-token",
            ))
            with patch(
                "kiwoom_monitor.infrastructure.kiwoom_rest.client_factory.LocalApiConfig.load",
                side_effect=AssertionError("로컬 키를 읽으면 안 됨"),
            ):
                client = create_query_client(root / "missing-api.env", source_path)
        self.assertIsInstance(client, RemoteKiwoomRestClient)

    def test_local_server_also_uses_remote_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "data_source.json"
            DataSourceConfig(source_path).save(DataSourceSettings(
                "local_server", "http://127.0.0.1:8787", "server-token",
            ))
            client = create_query_client(root / "missing-api.env", source_path)
        self.assertIsInstance(client, RemoteKiwoomRestClient)

    def test_personal_server_can_opt_in_to_local_query_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = DataSourceSettings(
                "personal_server", "https://nas.example.test", "server-token", True,
            )
            with patch.object(LocalApiConfig, "load", return_value=KiwoomSettings("key", "secret", "real")):
                client = create_query_client(root / "api.env", source_settings=source)
        self.assertIsInstance(client, FailoverKiwoomRestClient)

    def test_personal_server_can_opt_in_to_parallel_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = DataSourceSettings(
                "personal_server", "https://nas.example.test", "server-token", True, True,
            )
            with patch.object(LocalApiConfig, "load", return_value=KiwoomSettings("key", "secret", "real")):
                client = create_query_client(root / "api.env", source_settings=source)
        self.assertIsInstance(client, ParallelValidationClient)


if __name__ == "__main__":
    unittest.main()
