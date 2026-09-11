from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from kiwoom_monitor.central_server.app import _archived_chart_response, create_app
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    MarketDataMetadata,
    MarketDataObservation,
    MarketDatasetKind,
)
from kiwoom_monitor.infrastructure.news_ai import NewsAIProviderError


class CentralServerAppTests(unittest.TestCase):
    def test_app_starts_with_kiwoom_and_autonomous_top20_enabled(self) -> None:
        """Protect the NAS deployment combination that creates the persistent outbox."""
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_app_key="app-key", kiwoom_secret_key="secret-key",
                autonomous_top20_enabled=True,
                top20_outbox_path=str(Path(directory) / "top20-outbox.json"),
            )

            app = create_app(settings)

        self.assertIsNotNone(app)

    def test_public_api_route_contract_is_stable(self) -> None:
        """Protect the paths and methods used by released desktop clients."""
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            app = create_app(settings)
        actual = {
            (method, route.path)
            for route in app.routes
            for method in (getattr(route, "methods", None) or {"WEBSOCKET"})
            if route.path == "/health" or route.path.startswith("/api/v1/")
        }
        expected = {
            ("GET", "/health"),
            ("GET", "/api/v1/capabilities"),
            ("GET", "/api/v1/settings/operations"),
            ("PUT", "/api/v1/settings/operations"),
            ("GET", "/api/v1/diagnostics/resources"),
            ("POST", "/api/v1/kiwoom/query"),
            ("POST", "/api/v1/news/search"),
            ("POST", "/api/v1/news/analyze"),
            ("GET", "/api/v1/market/minute-bars"),
            ("GET", "/api/v1/market/daily-bars"),
            ("GET", "/api/v1/market/coverage"),
            ("GET", "/api/v1/market/external-bars"),
            ("GET", "/api/v1/market/snapshots/{kind}"),
            ("GET", "/api/v1/content/{collection}"),
            ("POST", "/api/v1/content/{collection}"),
            ("PUT", "/api/v1/content/{collection}"),
            ("WEBSOCKET", "/api/v1/realtime"),
        }
        self.assertEqual(expected, actual)

    def test_operational_settings_can_be_changed_and_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                ai_provider="gemini", ai_model="old-model", ai_daily_limit=500,
            )
            headers = {"Authorization": "Bearer private-token"}
            body = {
                "ai_provider": "gemini", "ai_model": "new-model", "ai_daily_limit": 321,
                "news_refresh_seconds": 600, "dart_enabled": True,
            }
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get("/api/v1/settings/operations").status_code)
                self.assertEqual(200, client.put("/api/v1/settings/operations", headers=headers, json=body).status_code)
            with TestClient(create_app(settings)) as client:
                loaded = client.get("/api/v1/settings/operations", headers=headers).json()
            self.assertEqual(body, loaded)

    def test_health_is_public_and_capabilities_require_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                self.assertEqual(200, client.get("/health").status_code)
                self.assertEqual(401, client.get("/api/v1/capabilities").status_code)
                response = client.get(
                    "/api/v1/capabilities", headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.json()["capabilities"]["kiwoom_rest"])
        self.assertTrue(response.json()["capabilities"]["news_archive"])
        self.assertTrue(response.json()["capabilities"]["ai_analysis_archive"])
        self.assertTrue(response.json()["capabilities"]["themes"])
        self.assertTrue(response.json()["capabilities"]["trade_journal"])
        self.assertTrue(response.json()["capabilities"]["shared_settings"])

    def test_query_reports_unconfigured_kiwoom_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                response = client.post(
                    "/api/v1/kiwoom/query",
                    headers={"Authorization": "Bearer private-token"},
                    json={"api_id": "ka10001", "path": "/api/dostk/stkinfo", "body": {"stk_cd": "005930"}},
                )
        self.assertEqual(503, response.status_code)

    def test_resource_diagnostics_are_authenticated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get("/api/v1/diagnostics/resources").status_code)
                response = client.get(
                    "/api/v1/diagnostics/resources",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertIn("process_memory_bytes", response.json())
        self.assertGreater(response.json()["database_size_bytes"], 0)

    def test_ai_endpoint_reports_unconfigured_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                response = client.post(
                    "/api/v1/news/analyze", headers={"Authorization": "Bearer private-token"},
                    json={"stock_code": "005930", "stock_name": "삼성전자", "events": [{
                        "identity": "a", "title": "제목", "body": "본문", "body_hash": "h",
                    }]},
                )
        self.assertEqual(503, response.status_code)

    def test_ai_endpoint_preserves_provider_rate_limit_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                gemini_api_key="key", ai_provider="gemini", ai_model="model",
            )
            with patch(
                "kiwoom_monitor.central_server.ai_service.CentralAIService.analyze",
                new=AsyncMock(side_effect=NewsAIProviderError(429)),
            ), TestClient(create_app(settings)) as client:
                response = client.post(
                    "/api/v1/news/analyze", headers={"Authorization": "Bearer private-token"},
                    json={"stock_code": "005930", "stock_name": "삼성전자", "events": [{
                        "identity": "a", "title": "제목", "body": "본문", "body_hash": "h",
                    }]},
                )
        self.assertEqual(429, response.status_code)
        self.assertIn("호출 한도", response.json()["detail"])

    def test_minute_bars_endpoint_is_authenticated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                path = "/api/v1/market/minute-bars?code=005930&trading_date=2026-09-08"
                self.assertEqual(401, client.get(path).status_code)
                response = client.get(path, headers={"Authorization": "Bearer private-token"})
        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["bars"])

    def test_daily_bars_endpoint_is_authenticated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/daily-bars?code=005930&limit=250",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["bars"])

    def test_market_coverage_requires_authentication_and_stays_conservative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                autonomous_top20_enabled=False,
            )
            path = (
                "/api/v1/market/coverage?kind=minute_bar&subject=005930%3AKRX"
                "&start=2026-09-10T09%3A00%3A00%2B09%3A00"
                "&end=2026-09-10T15%3A30%3A00%2B09%3A00"
            )
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get(path).status_code)
                response = client.get(
                    path, headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertEqual("missing", response.json()["state"])
        self.assertEqual("no_trade_or_no_observation", response.json()["absence_meaning"])

    def test_market_coverage_uses_explicit_completed_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(database_path)
            store.initialize()
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-10:005930:KRX", "key": "complete",
                "document": {"kind": "minute"},
            }])
            store.close()
            settings = CentralServerSettings(
                f"sqlite:///{database_path}", "private-token",
                autonomous_top20_enabled=False,
            )
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/coverage?kind=minute_bar&subject=005930%3AKRX"
                    "&start=2026-09-10T00%3A00%3A00%2B09%3A00"
                    "&end=2026-09-11T00%3A00%3A00%2B09%3A00",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertEqual("complete", response.json()["state"])
        self.assertTrue(response.json()["explicit_complete"])
        self.assertEqual(
            "no_trade_with_complete_coverage", response.json()["absence_meaning"],
        )

    def test_market_coverage_does_not_use_completion_saved_after_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(database_path)
            store.initialize()
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-10:005930:KRX", "key": "complete",
                "document": {"kind": "minute"},
            }])
            store.close()
            settings = CentralServerSettings(
                f"sqlite:///{database_path}", "private-token",
                autonomous_top20_enabled=False,
            )
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/coverage?kind=minute_bar&subject=005930%3AKRX"
                    "&start=2026-09-10T09%3A00%3A00%2B09%3A00"
                    "&end=2026-09-10T15%3A30%3A00%2B09%3A00"
                    "&available_by=2026-09-10T15%3A30%3A00%2B09%3A00",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertEqual("missing", response.json()["state"])
        self.assertFalse(response.json()["explicit_complete"])

    def test_market_coverage_rejects_inferred_bar_cadence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                autonomous_top20_enabled=False,
            )
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/coverage?kind=minute_bar&subject=005930%3AKRX"
                    "&start=2026-09-10T09%3A00%3A00%2B09%3A00"
                    "&end=2026-09-10T09%3A01%3A00%2B09%3A00&expected_seconds=60",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(400, response.status_code)

    def test_completed_minute_archive_is_reused_as_kiwoom_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.replace_minute_bars([{
                "trading_date": "2026-09-10", "minute": "09:01", "code": "005930", "market": "KRX",
                "open": 70000, "high": 70100, "low": 69900, "close": 70050, "volume": 10,
                "trade_value_million_won": 1, "updated_at": 1.0,
            }])
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-10:005930:KRX", "key": "complete", "document": {"kind": "minute"},
            }])
            result = _archived_chart_response(
                store, "ka10080", {"stk_cd": "005930", "base_dt": "20260910"},
            )
            store.close()
        self.assertEqual("20260910090100", result["stk_min_pole_chart_qry"][0]["cntr_tm"])

    def test_incomplete_archive_does_not_bypass_kiwoom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            result = _archived_chart_response(
                store, "ka10080", {"stk_cd": "005930", "base_dt": "20260910"},
            )
            store.close()
        self.assertIsNone(result)

    def test_empty_completed_minute_archive_does_not_bypass_kiwoom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-10:005930:KRX", "key": "complete",
                "document": {"kind": "minute", "rows": 0, "as_of": "2026-09-10"},
            }])
            result = _archived_chart_response(
                store, "ka10080", {"stk_cd": "005930", "base_dt": "20260910"},
            )
            store.close()
        self.assertIsNone(result)

    def test_empty_completed_daily_archive_does_not_bypass_kiwoom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("market_data_coverage_daily", [{
                "owner": "000660:NXT", "key": "complete",
                "document": {"kind": "daily", "rows": 0, "as_of": "2026-09-09"},
            }])

            result = _archived_chart_response(
                store, "ka10081", {"stk_cd": "000660_NX", "base_dt": "20260909"},
            )
            store.close()
        self.assertIsNone(result)

    def test_daily_archive_excludes_bars_after_requested_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.replace_daily_bars([{
                "trading_date": day, "code": "005930", "market": "KRX",
                "open": 70000, "high": 71000, "low": 69000, "close": close,
                "volume": 10, "trade_value_million_won": 1, "updated_at": 1.0,
            } for day, close in (("2026-09-10", 70500), ("2026-09-09", 69500))])
            store.upsert_documents("market_data_coverage_daily", [{
                "owner": "005930:KRX", "key": "complete",
                "document": {"kind": "daily", "rows": 2, "as_of": "2026-09-10"},
            }])
            result = _archived_chart_response(
                store, "ka10081", {"stk_cd": "005930", "base_dt": "20260909"},
            )
            store.close()

        self.assertEqual(1, len(result["stk_dt_pole_chart_qry"]))
        self.assertEqual("20260909", result["stk_dt_pole_chart_qry"][0]["date"])

    def test_daily_archive_newer_than_coverage_does_not_bypass_kiwoom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.replace_daily_bars([{
                "trading_date": "2026-09-10", "code": "005930", "market": "KRX",
                "open": 70000, "high": 71000, "low": 69000, "close": 70500,
                "volume": 10, "trade_value_million_won": 1, "updated_at": 1.0,
            }])
            store.upsert_documents("market_data_coverage_daily", [{
                "owner": "005930:KRX", "key": "complete",
                "document": {"kind": "daily", "rows": 1, "as_of": "2026-09-10"},
            }])
            result = _archived_chart_response(
                store, "ka10081", {"stk_cd": "005930", "base_dt": "20260911"},
            )
            store.close()
        self.assertIsNone(result)

    def test_market_snapshot_endpoint_rejects_unknown_kind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/snapshots/unknown",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(404, response.status_code)

    def test_content_api_upserts_and_loads_news(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                saved = client.post(
                    "/api/v1/content/news_article", headers=headers,
                    json={"documents": [{"owner": "005930", "key": "article-1", "document": {"title": "뉴스"}}]},
                )
                loaded = client.get(
                    "/api/v1/content/news_article?owner=005930", headers=headers,
                )
        self.assertEqual(1, saved.json()["saved"])
        self.assertEqual("뉴스", loaded.json()["documents"][0]["document"]["title"])

    def test_content_api_accepts_shared_columns_and_journal_news_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                columns = client.post(
                    "/api/v1/content/app_column_settings", headers=headers,
                    json={"documents": [{"owner": "main_table", "key": "stock", "document": {"visible": True, "position": 1}}]},
                )
                links = client.post(
                    "/api/v1/content/journal_news_link", headers=headers,
                    json={"documents": [{"owner": "group-1", "key": "005930|article-1", "document": {"group_id": "group-1"}}]},
                )
        self.assertEqual(1, columns.json()["saved"])
        self.assertEqual(1, links.json()["saved"])


if __name__ == "__main__":
    unittest.main()
