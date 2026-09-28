from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.diagnostic_workloads import instance_id
from tests.unit import test_historical_news_archive_reader as reader_fixture


class HistoricalNewsArchiveApiTests(unittest.TestCase):
    def test_unconfigured_or_unsealed_archive_does_not_change_existing_capability(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            archive = reader_fixture.HistoricalNewsArchiveReaderTests()._archive(directory)
            for configured in (False, True):
                settings = CentralServerSettings(
                    f"sqlite:///{directory / 'monitor.sqlite3'}", "test-token",
                    historical_news_archive_path=str(archive) if configured else "",
                )
                with TestClient(create_app(settings)) as client:
                    headers = {"Authorization": "Bearer test-token"}
                    health = client.get("/health").json()
                    self.assertEqual("unavailable" if configured else "unconfigured",
                                     health["historical_news_archive"]["state"])
                    capabilities = client.get("/api/v1/capabilities", headers=headers).json()
                    self.assertTrue(capabilities["capabilities"]["news_archive"])
                    self.assertFalse(capabilities["capabilities"]["historical_news_archive_v1"])
                    response = client.get("/api/v1/news/historical-archive/search",
                                          params={"stock_code": "005930"}, headers=headers)
                    self.assertEqual(503, response.status_code)

    def test_sealed_archive_api_is_authenticated_read_only_and_diagnostic_gated(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            archive = reader_fixture.HistoricalNewsArchiveReaderTests()._archive(directory)
            reader_fixture.HistoricalNewsArchiveReaderTests._seal_fixture(archive)
            before = hashlib.sha256(archive.read_bytes()).hexdigest()
            settings = CentralServerSettings(
                f"sqlite:///{directory / 'monitor.sqlite3'}", "test-token",
                historical_news_archive_path=str(archive),
            )
            with TestClient(create_app(settings)) as client:
                endpoint = "/api/v1/news/historical-archive/search"
                headers = {"Authorization": "Bearer test-token"}
                self.assertEqual(401, client.get(endpoint, params={"stock_code": "005930"}).status_code)
                capabilities = client.get("/api/v1/capabilities", headers=headers).json()
                self.assertTrue(capabilities["capabilities"]["historical_news_archive_v1"])
                self.assertEqual("fixture-1", capabilities["historical_news_archive_dataset_id"])
                first = client.get(endpoint, params={"stock_code": "005930", "limit": 1},
                                   headers=headers)
                self.assertEqual(200, first.status_code)
                data = first.json()
                article_id = data["items"][0]["article_revision_id"]
                second = client.get(endpoint, params={"stock_code": "005930", "limit": 1,
                                                      "cursor": data["next_cursor"]}, headers=headers)
                self.assertEqual(200, second.status_code)
                self.assertEqual(400, client.get(endpoint, params={"stock_code": "005930",
                                                               "cursor": "invalid"}, headers=headers).status_code)
                detail = client.get(f"/api/v1/news/historical-archive/articles/{article_id}",
                                    params={"dataset_id": data["dataset_id"]}, headers=headers)
                self.assertEqual(200, detail.status_code)
                self.assertEqual(article_id, detail.json()["article_revision_id"])
                self.assertEqual(409, client.get(
                    f"/api/v1/news/historical-archive/articles/{article_id}",
                    params={"dataset_id": "older-generation"}, headers=headers).status_code)
                self.assertEqual(404, client.get(
                    "/api/v1/news/historical-archive/articles/missing",
                    params={"dataset_id": data["dataset_id"]}, headers=headers).status_code)
                control = directory / "diagnostic-control.json"
                now = time.time()
                payload = {"schema": 1, "instance_id": instance_id(),
                           "diagnostic_tool": {"expires_at": now + 60,
                                               "owner": "test", "session_id": "test-session"},
                           "leases": {"historical_news_archive": now + 60}}
                control.write_text(json.dumps(payload), encoding="utf-8")
                with patch.dict("os.environ", {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(control)}):
                    paused = client.get(endpoint, params={"stock_code": "005930"}, headers=headers)
                    self.assertEqual(503, paused.status_code)
                    self.assertEqual("HISTORICAL_NEWS_ARCHIVE_PAUSED", paused.json()["detail"])
                    status = client.get("/api/v1/diagnostics/workloads", headers=headers).json()
                    self.assertTrue(status["workloads"]["historical_news_archive"]["paused_by_diagnostic"])
                    payload["diagnostic_tool"]["expires_at"] = now - 1
                    control.write_text(json.dumps(payload), encoding="utf-8")
                    resumed = client.get(endpoint, params={"stock_code": "005930"}, headers=headers)
                    self.assertEqual(200, resumed.status_code)
            self.assertEqual(before, hashlib.sha256(archive.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
