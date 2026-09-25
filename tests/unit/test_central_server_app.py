from __future__ import annotations

import asyncio
import io
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.error import HTTPError
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from kiwoom_monitor.central_server.app import (
    _archived_chart_response,
    _stored_market_response,
    create_app,
)
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.market_observations import KST, ranking_observation
from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness,
    MarketDataMetadata,
    MarketDataObservation,
    MarketDatasetKind,
)
from kiwoom_monitor.infrastructure.news_ai import NewsAIProviderError
from kiwoom_monitor.infrastructure.central_news_client import CentralNewsClient
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.central_journal_sync import CentralJournalSyncService
from kiwoom_monitor.infrastructure.central_content_sync import _journal_news_link_key
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository


class CentralServerAppTests(unittest.TestCase):
    def test_latest_market_caps_returns_old_0b_reference_without_old_price(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.save_realtime_snapshots([{
                "event_type": "trade", "item_key": "005930", "received_at": 1.0,
                "event": {"type": "trade", "payload": {
                    "code": "005930", "current_price": 70_000,
                    "change_rate": 3.5, "market_cap_eok": 4_321_000,
                }},
            }])
            store.close()
            settings = CentralServerSettings(f"sqlite:///{path}", "private-token")
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/latest-market-caps",
                    params=[("codes", "005930")],
                    headers={"Authorization": "Bearer private-token"},
                )

        self.assertEqual(200, response.status_code)
        self.assertEqual(4_321_000, response.json()["market_caps"][0]["market_cap_eok"])
        self.assertNotIn("current_price", response.text)
        self.assertNotIn("change_rate", response.text)

    def test_combined_minute_bars_prefer_sor_and_never_add_all_three_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            base = {
                "trading_date": "2026-09-14", "minute": "10:00", "code": "005930",
                "open": 100, "high": 110, "low": 90, "close": 105,
                "volume": 10, "trade_value_million_won": 20, "updated_at": 1.0,
            }
            store.save_minute_bars([
                {**base, "market": "KRX"},
                {**base, "market": "NXT", "trade_value_million_won": 5},
                {**base, "market": "SOR", "trade_value_million_won": 22},
            ])
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-14:005930:KRX", "key": "complete", "document": {
                    "kind": "minute", "window_closed": True, "session_finalized": True,
                    "as_of": "2026-09-14",
                },
            }])
            store.close()
            settings = CentralServerSettings(f"sqlite:///{path}", "private-token")
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/minute-bars",
                    params={"code": "005930", "trading_date": "2026-09-14", "market": "COMBINED"},
                    headers={"Authorization": "Bearer private-token"},
                )

        self.assertEqual(200, response.status_code)
        self.assertEqual(22, response.json()["bars"][0]["trade_value_million_won"])
        self.assertEqual("SOR", response.json()["bars"][0]["source_market"])
        self.assertTrue(response.json()["coverage"]["complete"])

    def test_trade_value_comparison_summary_excludes_partial_venue_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            owner = "2026-09-14:005930"
            store.upsert_documents("minute_trade_value_comparisons", [
                {
                    "owner": owner, "key": "10:00", "document": {
                        "query_scope": "KRX+NXT", "compared_at": "2026-09-14T10:01:00+09:00",
                        "realtime_trade_value_million_won": 110,
                        "query_trade_value_million_won": 100,
                        "difference_percent": 10.0,
                    },
                },
                {
                    "owner": owner, "key": "10:01", "document": {
                        "query_scope": "KRX+NXT", "compared_at": "2026-09-14T10:02:00+09:00",
                        "realtime_trade_value_million_won": 90,
                        "query_trade_value_million_won": 100,
                        "difference_percent": -10.0,
                    },
                },
                {
                    "owner": owner, "key": "10:02", "document": {
                        "query_scope": "KRX", "compared_at": "2026-09-14T10:03:00+09:00",
                        "realtime_trade_value_million_won": 50,
                        "query_trade_value_million_won": 40,
                        "difference_percent": 25.0,
                    },
                },
            ])
            store.close()
            settings = CentralServerSettings(f"sqlite:///{path}", "private-token")
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/trade-value-comparisons",
                    params={"code": "005930", "trading_date": "2026-09-14"},
                    headers={"Authorization": "Bearer private-token"},
                )

        self.assertEqual(200, response.status_code)
        summary = response.json()["summary"]
        self.assertEqual(2, summary["complete_count"])
        self.assertEqual(1, summary["partial_count"])
        self.assertEqual({"KRX": 1, "KRX+NXT": 2}, summary["scope_counts"])
        self.assertEqual(200, summary["total_realtime_trade_value_million_won"])
        self.assertEqual(200, summary["total_query_trade_value_million_won"])
        self.assertEqual(0.0, summary["total_difference_percent"])
        self.assertEqual(0.0, summary["average_difference_percent"])
        self.assertEqual(10.0, summary["mean_absolute_difference_percent"])
        self.assertEqual(10.0, summary["max_absolute_difference_percent"])

    def test_recent_minute_bars_reads_two_stored_trading_days_without_broker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            for day, minute in (("2026-09-11", "15:20"), ("2026-09-14", "10:00")):
                store.save_minute_bars([{
                    "trading_date": day, "minute": minute, "code": "005930",
                    "market": "KRX", "open": 100, "high": 110, "low": 90,
                    "close": 105, "volume": 10, "trade_value_million_won": 20,
                    "updated_at": 1.0,
                }])
            store.close()
            settings = CentralServerSettings(
                f"sqlite:///{path}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/recent-minute-bars",
                    params={
                        "code": "005930", "end_date": "2026-09-14",
                        "market": "KRX", "trading_days": 2,
                    },
                    headers={"Authorization": "Bearer private-token"},
                )

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            ["2026-09-11", "2026-09-14"], response.json()["trading_days"],
        )
        self.assertEqual(2, len(response.json()["bars"]))

    def test_authenticated_news_search_returns_confirmed_n3_article_to_central_client(self) -> None:
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.save_news_source_page({
                "source_id": "source", "query_text": "증권", "run_id": "run",
                "items": [{
                    "identity": "https://o/n3",
                    "document": {
                        "title": "삼성전자 공급계약 체결", "description": "500억원 수주",
                        "link": "https://n/n3", "original_link": "https://o/n3",
                        "published_at": "2026-09-12T01:00:00+00:00",
                    },
                    "targets": [{
                        "stock_code": "005930", "stock_name": "삼성전자",
                        "relation_status": "confirmed", "rule_version": "exact-v1",
                    }],
                }],
            })
            store.close()
            with closing(sqlite3.connect(path)) as connection:
                jobs_before = int(connection.execute("SELECT COUNT(*) FROM central_news_jobs").fetchone()[0])
            settings = CentralServerSettings(
                f"sqlite:///{path}", "private-token",
                naver_news_client_id="configured", naver_news_client_secret="configured",
                news_query_set_enabled=False, news_history_jobs_enabled=False,
            )
            with patch(
                "kiwoom_monitor.infrastructure.naver_news.NaverNewsClient.search", return_value=(),
            ), TestClient(create_app(settings)) as api_client:
                def opener(request, **_kwargs):
                    path_and_query = urlsplit(request.full_url)
                    response = api_client.request(
                        request.get_method(), path_and_query.path + (f"?{path_and_query.query}" if path_and_query.query else ""),
                        headers=dict(request.header_items()), content=request.data,
                    )
                    return Response(response.content)

                items = CentralNewsClient(
                    "http://testserver", "private-token", opener=opener,
                ).search("005930", "삼성전자")
                stored_items, next_offset = CentralNewsClient(
                    "http://testserver", "private-token", opener=opener,
                ).stored_page("005930", "삼성전자")
            with closing(sqlite3.connect(path)) as connection:
                jobs_after = int(connection.execute("SELECT COUNT(*) FROM central_news_jobs").fetchone()[0])

        self.assertEqual("https://o/n3", items[0].original_link)
        self.assertEqual("수주·계약", items[0].assessment.category)
        self.assertEqual("https://o/n3", stored_items[0].original_link)
        self.assertIsNone(next_offset)
        self.assertEqual(jobs_before, jobs_after)

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

    def test_live_top20_endpoint_does_not_wait_for_database_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_app_key="app-key", kiwoom_secret_key="secret-key",
                autonomous_top20_enabled=True, market_event_collection_enabled=False,
                top20_outbox_path=str(Path(directory) / "top20-outbox.json"),
            )
            app = create_app(settings)
            service = app.state.autonomous_top20_service
            self.assertIsNotNone(service)
            service._latest_membership_snapshot = {
                "subject": "2026-09-22",
                "snapshot_key": "2026-09-22T18:00:00",
                "saved_at": 1.0,
                "payload": {"observed_at": "2026-09-22T18:00:00", "codes": ["005930"],
                            "items": [{"stk_cd": "005930", "stk_nm": "삼성전자"}] * 20},
                "persistence_state": "pending",
            }
            route = next(
                value for value in app.routes
                if getattr(value, "path", "") == "/api/v1/market/snapshots/{kind}"
            )
            live = asyncio.run(route.endpoint(
                kind="top20_membership", subject="2026-09-22", limit=1,
                prefer_live=True,
            ))
            persisted = asyncio.run(route.endpoint(
                kind="top20_membership", subject="2026-09-22", limit=1,
                prefer_live=False,
            ))

        self.assertEqual("pending", live["snapshots"][0]["persistence_state"])
        self.assertEqual([], persisted["snapshots"])

    def test_mock_account_monitor_is_wired_only_with_explicit_mock_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_environment="real", kiwoom_app_key="real-key", kiwoom_secret_key="real-secret",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_account_ref="opaque-account",
                mock_execution_run_id="forward-run",
            )
            app = create_app(settings)
        self.assertIsNotNone(app.state.mock_account_monitor)
        self.assertIsNotNone(app.state.mock_account_realtime)
        account_client = app.state.mock_account_monitor._reader._broker._client
        self.assertEqual("mock", account_client.environment)
        self.assertEqual(1.0, account_client._base_request_interval_seconds)

    def test_mock_identity_token_failure_disables_only_mock_features(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_order_transport_enabled=True,
                mock_account_ref="opaque-account", mock_execution_run_id="forward-run",
                account_identity_registry_enabled=True,
                account_identity_hmac_key="x" * 32,
            )
            with patch(
                "kiwoom_monitor.infrastructure.kiwoom_rest.account_identity."
                "KiwoomAccountIdentityReader.verify",
                new=AsyncMock(side_effect=RuntimeError("mock token unavailable")),
            ), TestClient(create_app(settings)) as client:
                health = client.get("/health").json()
                order = client.post(
                    "/api/v1/mock/orders",
                    headers={"Authorization": "Bearer private-token"},
                    json={
                        "request_id": "req-1", "symbol": "005930", "side": "BUY",
                        "quantity": 1, "limit_price": 1000,
                    },
                )

        self.assertEqual("ok", health["status"])
        self.assertFalse(health["mock_account_available"])
        self.assertEqual("MOCK_ACCOUNT_STARTUP_FAILED", health["mock_account_error"])
        self.assertEqual(503, order.status_code)

    def test_mock_identity_mismatch_keeps_server_alive_without_publishing_binding(self) -> None:
        from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import VerifiedAccountIdentity
        from kiwoom_monitor.domain.order_contract import AccountEnvironment

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "monitor.sqlite3"
            settings = CentralServerSettings(
                f"sqlite:///{database}", "private-token",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_order_transport_enabled=True,
                mock_account_ref="different-account", mock_execution_run_id="forward-run",
                account_identity_registry_enabled=True, account_identity_hmac_key="x" * 32,
            )
            identity = VerifiedAccountIdentity(
                "kiwoom", AccountEnvironment.MOCK, "a" * 64, datetime.now(timezone.utc),
            )
            with patch(
                "kiwoom_monitor.infrastructure.kiwoom_rest.account_identity."
                "KiwoomAccountIdentityReader.verify", new=AsyncMock(return_value=identity),
            ), TestClient(create_app(settings)) as client:
                health = client.get("/health").json()
                self.assertEqual("ok", health["status"])
                self.assertFalse(health["mock_account_available"])
                self.assertEqual("ACCOUNT_CONTEXT_MISMATCH", health["mock_account_error"])
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(0, connection.execute(
                    "SELECT COUNT(*) FROM central_account_binding_revisions"
                ).fetchone()[0])

    def test_mock_monitor_start_failure_is_isolated_and_error_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_account_ref="account-1",
                mock_execution_run_id="run-1",
            )
            with patch(
                "kiwoom_monitor.central_server.mock_account_monitor.MockAccountMonitor.start",
                new=AsyncMock(side_effect=RuntimeError("fake-sensitive-upstream-response")),
            ), self.assertLogs("kiwoom_monitor.central_server.app", level="ERROR") as logged:
                with TestClient(create_app(settings)) as client:
                    health = client.get("/health").json()
                    self.assertEqual("ok", health["status"])
                    self.assertFalse(health["mock_account_available"])
                    self.assertEqual("MOCK_ACCOUNT_STARTUP_FAILED", health["mock_account_error"])
                    self.assertIsNone(client.app.state.mock_account_bundle)
            self.assertNotIn("fake-sensitive-upstream-response", str(logged.output))

    def test_mock_order_response_keeps_original_gateway_after_runtime_publication(self) -> None:
        from types import SimpleNamespace
        from kiwoom_monitor.domain.order_contract import OrderSide, OrderType, OrderState

        with tempfile.TemporaryDirectory() as directory:
            app = create_app(CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            ))
            endpoint = next(route.endpoint for route in app.routes
                            if route.path == "/api/v1/mock/orders")
            gateway_cell = dict(zip(endpoint.__code__.co_freevars, endpoint.__closure__))[
                "mock_order_gateway"
            ]
            now = datetime.now(timezone.utc)
            record = SimpleNamespace(
                intent=SimpleNamespace(
                    intent_id="intent-1", decision_id="manual:request-1", run_id="run-1",
                    environment="mock", symbol="005930", venue="KRX", side=OrderSide.BUY,
                    quantity=1, order_type=OrderType.LIMIT, limit_price=1000,
                    policy_version="test/v1", created_at=now, expires_at=now,
                ), state=OrderState.SUBMISSION_UNKNOWN, broker_order_id="", filled_quantity=0,
                updated_at=now,
            )
            class Gateway:
                async def submit_limit(self, **kwargs):
                    gateway_cell.cell_contents = None  # Publish a disabled/new epoch mid-request.
                    return record
                def events(self, intent_id):
                    return ({"source": "original-account"},)
            gateway_cell.cell_contents = Gateway()
            with TestClient(app) as client:
                response = client.post("/api/v1/mock/orders",
                    headers={"Authorization": "Bearer private-token"},
                    json={"request_id": "request-1", "symbol": "005930", "side": "BUY",
                          "quantity": 1, "limit_price": 1000})
            self.assertEqual(200, response.status_code)
            self.assertEqual([{"source": "original-account"}], response.json()["events"])

    def test_main_identity_start_failure_closes_mock_bundle_before_database(self) -> None:
        settings = CentralServerSettings(
            "sqlite:///:memory:", "private-token", kiwoom_app_key="real-key",
            kiwoom_secret_key="real-secret", kiwoom_environment="real",
            kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
            autonomous_top20_enabled=False, market_event_collection_enabled=False,
            mock_account_monitor_enabled=True, mock_account_ref="account-1",
            mock_execution_run_id="run-1", account_identity_registry_enabled=True,
            account_identity_hmac_key="x" * 32,
        )
        app = create_app(settings)
        with patch(
            "kiwoom_monitor.infrastructure.kiwoom_rest.account_identity."
            "KiwoomAccountIdentityReader.verify",
            new=AsyncMock(side_effect=RuntimeError("main-identity-failed")),
        ), self.assertRaisesRegex(RuntimeError, "main-identity-failed"):
            with TestClient(app): pass
        self.assertTrue(app.state.mock_account_bundle._close_task.done())

    def test_mock_account_monitor_refuses_missing_kiwoom_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_environment="real", mock_account_monitor_enabled=True,
                mock_account_ref="opaque-account", mock_execution_run_id="forward-run",
            )
            with self.assertRaisesRegex(ValueError, "별도 모의투자 앱 키"):
                create_app(settings)

    def test_mock_order_transport_refuses_missing_account_monitor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                mock_order_transport_enabled=True,
            )
            with self.assertRaisesRegex(ValueError, "모의계좌 모니터"):
                create_app(settings)

    def test_mock_account_monitor_does_not_require_real_market_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_environment="real",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_account_ref="opaque-account",
                mock_execution_run_id="forward-run",
            )
            app = create_app(settings)
        self.assertIsNotNone(app.state.mock_account_monitor)
        self.assertIsNotNone(app.state.mock_account_realtime)

    def test_mock_order_gateway_uses_the_mock_account_client_only_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_order_transport_enabled=True,
                mock_account_ref="opaque-account", mock_execution_run_id="forward-run",
            )
            app = create_app(settings)
        gateway = app.state.mock_order_gateway
        self.assertIsNotNone(gateway)
        account_client = app.state.mock_account_monitor._reader._broker._client
        transport_client = gateway._runtime._lifecycle._transport._client
        self.assertIs(account_client, transport_client)
        self.assertEqual("mock", transport_client.environment)

    def test_mock_order_endpoint_is_closed_when_transport_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(CentralServerSettings(
            f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
        ))) as client:
            response = client.post(
                "/api/v1/mock/orders",
                headers={"Authorization": "Bearer private-token"},
                json={
                    "request_id": "manual-1", "symbol": "005930", "side": "BUY",
                    "quantity": 1, "limit_price": 70000,
                },
            )
        self.assertEqual(503, response.status_code)

    def test_mock_order_api_submits_once_reads_and_cancels_with_fresh_account(self) -> None:
        class FakeClient:
            instances = []

            def __init__(self, settings):
                self.environment = settings.environment
                self.order_calls = []
                self.query_calls = []
                self.now = datetime(2026, 9, 14, 9, 0, 0)
                self.__class__.instances.append(self)

            def server_now(self):
                return self.now

            def get_access_token(self):
                return "mock-token"

            def request_with_continuation(self, api_id, _path, _body, *, cont_yn="N", next_key=""):
                self.query_calls.append((api_id, cont_yn, next_key))
                payload = {
                    "ka10075": {"oso": []},
                    "ka10076": {"cntr": []},
                    "kt00018": {"acnt_evlt_remn_indv_tot": []},
                    "kt00001": {"ord_alow_amt": "1000000"},
                }[api_id]
                return payload, False, ""

            def request_once(self, api_id, path, body):
                self.order_calls.append((api_id, path, body))
                return {"return_code": 0, "ord_no": "mock-order-1"}

        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                kiwoom_mock_app_key="mock-key", kiwoom_mock_secret_key="mock-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False,
                mock_account_monitor_enabled=True, mock_order_transport_enabled=True,
                mock_account_ref="opaque-account", mock_execution_run_id="forward-run",
            )
            headers = {"Authorization": "Bearer private-token"}
            body = {
                "request_id": "manual-api-1", "symbol": "005930", "side": "BUY",
                "quantity": 1, "limit_price": 70000, "expires_seconds": 120,
            }
            with patch(
                "kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", FakeClient,
            ), patch(
                "kiwoom_monitor.central_server.mock_account_monitor."
                "MockAccountRealtimeCollector.start", new=AsyncMock(),
            ), TestClient(create_app(settings)) as client:
                first = client.post("/api/v1/mock/orders", headers=headers, json=body)
                duplicate = client.post("/api/v1/mock/orders", headers=headers, json=body)
                intent_id = first.json()["intent_id"]
                loaded = client.get(f"/api/v1/mock/orders/{intent_id}", headers=headers)
                FakeClient.instances[0].now = datetime(2026, 9, 14, 16, 0, 0)
                after_probe = client.post(
                    "/api/v1/mock/orders", headers=headers,
                    json={**body, "request_id": "manual-api-after-1"},
                )
                cancelled = client.post(
                    f"/api/v1/mock/orders/{intent_id}/cancel", headers=headers, json={"quantity": 0},
                )

        self.assertEqual(200, first.status_code)
        self.assertEqual("ACCEPTED", first.json()["state"])
        self.assertEqual(intent_id, duplicate.json()["intent_id"])
        self.assertEqual("ACCEPTED", loaded.json()["state"])
        self.assertEqual("ACCEPTED", after_probe.json()["state"])
        self.assertEqual("ORDER_ACCEPTED", after_probe.json()["events"][-1]["event_type"])
        self.assertIn(
            "manual-mock-krx-after-limit-probe/v1",
            after_probe.json()["policy_version"],
        )
        self.assertEqual("CANCEL_PENDING", cancelled.json()["state"])
        self.assertEqual(3, len(FakeClient.instances[0].order_calls))
        self.assertEqual(
            ["kt10000", "kt10000", "kt10003"],
            [call[0] for call in FakeClient.instances[0].order_calls],
        )

    def test_market_feed_returns_only_requested_stored_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            today = datetime.now().date().isoformat()
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            store.save_news_source_page({
                "source_id": f"naver-stock:flash:{today}", "query_text": "flash",
                "run_id": "flash-run", "items": [{
                    "identity": "https://example.com/flash-1",
                    "document": {"title": "속보", "description": "저장된 요약",
                                 "link": "https://example.com/flash-1",
                                 "published_at": f"{today}T10:00:00+09:00"},
                    "targets": [],
                }],
            })
            store.close()
            settings = CentralServerSettings(f"sqlite:///{path}", "private-token",
                                             news_query_set_enabled=False)
            with TestClient(create_app(settings)) as client:
                headers = {"Authorization": "Bearer private-token"}
                flash = client.get("/api/v1/news/market-feed?source=flash", headers=headers)
                world = client.get("/api/v1/news/market-feed?source=world", headers=headers)
                denied = client.get("/api/v1/news/market-feed?source=flash")
            self.assertEqual(200, flash.status_code)
            self.assertEqual("저장된 요약", flash.json()["items"][0]["description"])
            self.assertEqual([], world.json()["items"])
            self.assertEqual(401, denied.status_code)

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
            ("POST", "/api/v1/mock/orders"),
            ("GET", "/api/v1/mock/orders/{intent_id}"),
            ("POST", "/api/v1/mock/orders/{intent_id}/cancel"),
            ("GET", "/api/v1/settings/operations"),
            ("GET", "/api/v1/settings/market-profile"),
            ("PUT", "/api/v1/settings/market-profile"),
            ("PUT", "/api/v1/settings/operations"),
            ("GET", "/api/v1/settings/accounts/{account_ref}"),
            ("GET", "/api/v1/settings/credentials"),
            ("POST", "/api/v1/settings/credentials/{provider}/profiles"),
            ("PUT", "/api/v1/settings/credentials/{provider}/profiles/{profile_id}"),
            ("DELETE", "/api/v1/settings/credentials/{provider}/profiles/{profile_id}"),
            ("POST", "/api/v1/settings/credentials/{provider}/profiles/{profile_id}/prepare"),
            ("GET", "/api/v1/settings/credential-operations/{operation_id}"),
            ("POST", "/api/v1/settings/credential-operations/{operation_id}/apply"),
            ("DELETE", "/api/v1/settings/credential-operations/{operation_id}"),
            ("GET", "/api/v1/diagnostics/resources"),
            ("POST", "/api/v1/kiwoom/query"),
            ("POST", "/api/v1/news/search"),
            ("POST", "/api/v1/news/stored-page"),
            ("POST", "/api/v1/news/analyze"),
            ("POST", "/api/v1/news/historical-jobs/claim"),
            ("POST", "/api/v1/news/historical-jobs/complete"),
            ("POST", "/api/v1/news/historical-market-articles"),
            ("GET", "/api/v1/news/history/{kind}"),
            ("GET", "/api/v1/news/sources"),
            ("GET", "/api/v1/news/market-feed"),
            ("GET", "/api/v1/market/minute-bars"),
            ("GET", "/api/v1/market/recent-minute-bars"),
            ("GET", "/api/v1/market/latest-market-caps"),
            ("GET", "/api/v1/market/trade-value-comparisons"),
            ("GET", "/api/v1/market/events"),
            ("GET", "/api/v1/market/daily-bars"),
            ("GET", "/api/v1/market/coverage"),
            ("GET", "/api/v1/market/external-bars"),
            ("GET", "/api/v1/research/observations"),
            ("GET", "/api/v1/research/candidates"),
            ("POST", "/api/v1/research/mock-automation-candidates"),
            ("GET", "/api/v1/research/mock-automation-candidates/{account_ref}"),
            ("POST", "/api/v1/research/mock-automation-specs"),
            ("GET", "/api/v1/research/mock-automation-specs/{account_ref}"),
            ("GET", "/api/v1/mock-automation/accounts/{account_ref}"),
            ("POST", "/api/v1/mock-automation/start"),
            ("POST", "/api/v1/mock-automation/stop"),
            ("POST", "/api/v1/mock-automation/resume"),
            ("GET", "/api/v1/market/top20-statistics"),
            ("GET", "/api/v1/market/snapshots/{kind}"),
            ("GET", "/api/v1/content/{collection}"),
            ("POST", "/api/v1/content/{collection}"),
            ("PUT", "/api/v1/content/{collection}"),
            ("GET", "/api/v1/themes/history"),
            ("WEBSOCKET", "/api/v1/realtime"),
        }
        self.assertEqual(expected, actual)

    def test_research_observation_export_is_authenticated_and_page_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            for second, code in ((0, "005930"), (30, "000660")):
                key = f"2026-09-12T09:00:{second:02d}"
                payload = {"query_type": "5", "items": [{"stk_cd": code}]}
                store.save_dataset_snapshot(
                    "ranking", "5", key, payload,
                    observation=ranking_observation(
                        "5", key, payload,
                        datetime(2026, 9, 12, 0, 0, second, tzinfo=timezone.utc),
                        source="fixture",
                    ),
                )
            store.close()

            settings = CentralServerSettings(f"sqlite:///{path}", "private-token")
            parameters = {
                "start": "2026-09-12T00:00:00+00:00",
                "end": "2026-09-13T00:00:00+00:00",
                "kinds": "ranking", "subject": "5", "limit": 1,
            }
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get(
                    "/api/v1/research/observations", params=parameters,
                ).status_code)
                first = client.get(
                    "/api/v1/research/observations", params=parameters, headers=headers,
                )
                self.assertEqual(200, first.status_code)
                first_document = first.json()
                second = client.get(
                    "/api/v1/research/observations",
                    params={**parameters, "watermark": first_document["watermark"],
                            "cursor": first_document["next_cursor"]},
                    headers=headers,
                )
                mismatch = client.get(
                    "/api/v1/research/observations",
                    params={**parameters, "subject": "1", "watermark": first_document["watermark"]},
                    headers=headers,
                )

        self.assertEqual(2, first_document["manifest"]["revision_count"])
        self.assertEqual(1, len(first_document["observations"]))
        self.assertEqual(1, len(second.json()["observations"]))
        self.assertIsNone(second.json()["next_cursor"])
        self.assertEqual(409, mismatch.status_code)

    def test_shadow_candidate_api_is_authenticated_and_reports_disabled_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(CentralServerSettings(
            f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
        ))) as client:
            unauthorized = client.get("/api/v1/research/candidates")
            response = client.get(
                "/api/v1/research/candidates?after_sequence=0&limit=10",
                headers={"Authorization": "Bearer private-token"},
            )

        self.assertEqual(401, unauthorized.status_code)
        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["events"])
        self.assertEqual("DISABLED", response.json()["quality"]["status"])

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
                "news_query_set_enabled": False, "news_query_set": ["증권", "코스피"],
                "news_query_set_refresh_seconds": 900,
                "news_processing_excluded_providers": ["thebell.co.kr", "연합인포맥스"],
            }
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get("/api/v1/settings/operations").status_code)
                self.assertEqual(200, client.put("/api/v1/settings/operations", headers=headers, json=body).status_code)
            with TestClient(create_app(settings)) as client:
                loaded = client.get("/api/v1/settings/operations", headers=headers).json()
            for key, value in body.items():
                self.assertEqual(value, loaded[key])
            self.assertFalse(loaded["shadow_candidate_enabled"])
            self.assertEqual(
                default_shadow_breakout_config().to_dict(),
                loaded["shadow_candidate_config"],
            )

    def test_operational_settings_conflict_and_failed_write_preserve_latest_state(self):
        from unittest.mock import patch
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                original = client.get("/api/v1/settings/operations", headers=headers).json()
                saved = client.put("/api/v1/settings/operations", headers=headers, json={
                    "expected_revision": original["revision"], "ai_daily_limit": 123,
                })
                self.assertEqual(200, saved.status_code)
                self.assertEqual(original["revision"] + 1, saved.json()["revision"])
                stale = client.put("/api/v1/settings/operations", headers=headers, json={
                    "expected_revision": original["revision"], "dart_enabled": True,
                })
                self.assertEqual(409, stale.status_code)
                with patch.object(SQLiteQueryStore, "upsert_documents", side_effect=OSError("write failed")):
                    failed = client.put("/api/v1/settings/operations", headers=headers, json={
                        "ai_daily_limit": 321,
                    })
                self.assertEqual(503, failed.status_code)
                latest = client.get("/api/v1/settings/operations", headers=headers).json()
                self.assertEqual(saved.json(), latest)
                self.assertEqual(latest["revision"], latest["applied_revision"])
            with TestClient(create_app(settings)) as client:
                self.assertEqual(latest, client.get("/api/v1/settings/operations", headers=headers).json())

    def test_operational_apply_failure_can_retry_persisted_revision(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                with patch("kiwoom_monitor.central_server.app.CandidateMonitor.start",
                           side_effect=RuntimeError("runtime failure")):
                    failed = client.put("/api/v1/settings/operations", headers=headers, json={
                        "shadow_candidate_enabled": True,
                    })
                self.assertEqual(503, failed.status_code)
                pending = client.get("/api/v1/settings/operations", headers=headers).json()
                self.assertEqual("RECOVERY_REQUIRED", pending["apply_status"])
                retried = client.put("/api/v1/settings/operations", headers=headers, json={
                    "expected_revision": pending["revision"],
                })
                self.assertEqual(200, retried.status_code)
                self.assertEqual(pending["revision"], retried.json()["revision"])
                self.assertEqual("ACTIVE", retried.json()["apply_status"])

    def test_shadow_candidate_can_start_and_stop_without_server_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                enabled = client.put("/api/v1/settings/operations", headers=headers, json={
                    "shadow_candidate_enabled": True,
                    "shadow_candidate_config": default_shadow_breakout_config().to_dict(),
                    "shadow_candidate_poll_seconds": 2,
                    "shadow_candidate_universe_max_age_seconds": 90,
                })
                active = client.get("/api/v1/research/candidates", headers=headers)
                monitor_before_news_change = client.app.state.candidate_monitor
                news_only = client.put(
                    "/api/v1/settings/operations", headers=headers,
                    json={"news_refresh_seconds": 601},
                )
                monitor_after_news_change = client.app.state.candidate_monitor
                invalid = client.put("/api/v1/settings/operations", headers=headers, json={
                    "shadow_candidate_config": {"strategy_version": "v999"},
                })
                active_after_invalid = client.get(
                    "/api/v1/research/candidates", headers=headers,
                )
                disabled = client.put("/api/v1/settings/operations", headers=headers, json={
                    "shadow_candidate_enabled": False,
                })
                inactive = client.get("/api/v1/research/candidates", headers=headers)

            self.assertEqual(200, enabled.status_code)
            self.assertTrue(enabled.json()["shadow_candidate_enabled"])
            self.assertNotEqual("DISABLED", active.json()["quality"]["status"])
            self.assertEqual(200, news_only.status_code)
            self.assertIs(monitor_before_news_change, monitor_after_news_change)
            self.assertEqual(422, invalid.status_code)
            self.assertNotEqual("DISABLED", active_after_invalid.json()["quality"]["status"])
            self.assertEqual(200, disabled.status_code)
            self.assertFalse(disabled.json()["shadow_candidate_enabled"])
            self.assertEqual("DISABLED", inactive.json()["quality"]["status"])

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
        self.assertTrue(response.json()["capabilities"]["journal_v2_sync"])
        self.assertTrue(response.json()["capabilities"]["journal_news_links_v2"])
        self.assertTrue(response.json()["capabilities"]["combined_minute_bars"])
        self.assertTrue(response.json()["capabilities"]["trade_value_comparisons"])
        self.assertTrue(response.json()["capabilities"]["mock_automation_candidate_publish_v1"])
        self.assertTrue(response.json()["capabilities"]["mock_automation_candidate_read_v1"])
        self.assertTrue(response.json()["capabilities"]["mock_automation_spec_publish_v1"])
        self.assertFalse(response.json()["capabilities"]["mock_automation_runtime_v1"])

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
        self.assertEqual(
            ["news", "market", "research", "account", "other"],
            [item["category"] for item in response.json()["storage_categories"]],
        )
        self.assertFalse(response.json()["retention_policy"]["automatic_deletion_enabled"])

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

    def test_market_event_diagnostics_are_authenticated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get("/api/v1/market/events?kind=cohort").status_code)
                response = client.get(
                    "/api/v1/market/events?kind=cohort",
                    headers={"Authorization": "Bearer private-token"},
                )
        self.assertEqual(200, response.status_code)
        self.assertEqual("NOT_OBSERVED", response.json()["condition"]["status"])

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
                "owner": "2026-09-10:005930:KRX", "key": "complete", "document": {
                    "kind": "minute", "as_of": "2026-09-10",
                    "window_closed": True, "session_finalized": True,
                },
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

    def test_existing_bars_with_unfinalized_coverage_do_not_bypass_kiwoom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.replace_minute_bars([{
                "trading_date": "2026-09-10", "minute": "09:01", "code": "005930", "market": "KRX",
                "open": 70000, "high": 70100, "low": 69900, "close": 70050, "volume": 10,
                "trade_value_million_won": 1, "updated_at": 1.0,
            }])
            store.upsert_documents("market_data_coverage", [{
                "owner": "2026-09-10:005930:KRX", "key": "complete",
                "document": {"kind": "minute", "as_of": "2026-09-10",
                             "window_closed": False, "session_finalized": False},
            }])
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
                "document": {"kind": "daily", "rows": 2, "as_of": "2026-09-10",
                             "window_closed": True, "session_finalized": True},
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

    def test_top20_statistics_endpoint_returns_nas_aggregate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            settings = CentralServerSettings(f"sqlite:///{path}", "private-token")
            store = SQLiteQueryStore(path)
            store.initialize()
            store.save_dataset_snapshot("top20_index", "2026-09-08", "2026-09-08T15:30", {
                "minute": "2026-09-08T15:30", "market_values": [3.0, 2.0, 0.0],
                "capture_state": "realtime_complete",
            })
            store.save_dataset_snapshot("market_index_chart", "20260908:kospi", "20260908", {
                "daily": [{"dt": "20260908", "trde_prica": "26187833"}],
            })
            store.close()

            with TestClient(create_app(settings)) as client:
                response = client.get(
                    "/api/v1/market/top20-statistics?start_date=2026-09-08&end_date=2026-09-08",
                    headers={"Authorization": "Bearer private-token"},
                )

        self.assertEqual(200, response.status_code)
        self.assertEqual(5.0, response.json()["comparisons"][0]["top20_eok"])
        self.assertEqual(261878.33, response.json()["comparisons"][0]["kospi_eok"])

    def test_stored_fundamentals_and_nxt_documents_bypass_broker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("stock_fundamentals", [{
                "owner": "005930", "key": "latest",
                "document": {
                    "observed_at": datetime.now(KST).isoformat(),
                    "payload": {"mac": "1000", "dstr_rt": "40"},
                },
            }])
            store.upsert_documents("stock_nxt_eligibility", [{
                "owner": "005930", "key": "latest",
                "document": {
                    "observed_at": datetime.now(KST).isoformat(),
                    "payload": {"nxtEnable": "Y"},
                },
            }])
            store.close()
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                fundamentals = client.post("/api/v1/kiwoom/query", headers=headers, json={
                    "api_id": "ka10001", "path": "/api/dostk/stkinfo",
                    "body": {"stk_cd": "005930"},
                })
                nxt = client.post("/api/v1/kiwoom/query", headers=headers, json={
                    "api_id": "ka10100", "path": "/api/dostk/stkinfo",
                    "body": {"stk_cd": "005930"},
                })

        self.assertEqual(200, fundamentals.status_code)
        self.assertEqual("1000", fundamentals.json()["payload"]["mac"])
        self.assertTrue(fundamentals.json()["archive_hit"])
        self.assertEqual("Y", nxt.json()["payload"]["nxtEnable"])

    def test_stale_fundamentals_document_is_not_reused_as_a_fresh_query(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            store.upsert_documents("stock_fundamentals", [{
                "owner": "005930", "key": "latest",
                "document": {
                    "observed_at": "2020-01-01T09:00:00+09:00",
                    "payload": {"mac": "1000"},
                },
            }])
            result = _stored_market_response(store, "ka10001", {"stk_cd": "005930"})
            store.close()

        self.assertIsNone(result)

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

    def test_news_history_api_is_authenticated_and_reads_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
                news_history_jobs_enabled=False,
            )
            headers = {"Authorization": "Bearer private-token"}
            with TestClient(create_app(settings)) as client:
                self.assertEqual(401, client.get("/api/v1/news/history/article").status_code)
                client.post(
                    "/api/v1/content/news_article", headers=headers,
                    json={"documents": [{"owner": "005930", "key": "article-1",
                                         "document": {"title": "뉴스"},
                                         "collector_id": "local-test"}]},
                ).raise_for_status()
                result = client.get(
                    "/api/v1/news/history/article?target=005930&identity=article-1",
                    headers=headers,
                ).json()

        self.assertTrue(result["known"])
        self.assertEqual("local-test", result["revisions"][0]["collector_id"])
        self.assertEqual("뉴스", result["revisions"][0]["document"]["title"])

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
                    json={"documents": [{"owner": "group-1", "key": "005930|article-1", "document": {
                        "group_id": "group-1", "stock_code": "005930", "identity": "article-1",
                    }}]},
                )
                scope = "11111111-1111-4111-8111-111111111111"
                scoped_document = {
                    "group_id": "group-1", "stock_code": "005930", "identity": "article-1",
                    "origin_broker": "kiwoom", "origin_environment": "real",
                    "origin_account_ref": scope, "canonical_account_ref": scope,
                }
                scoped_links = client.post(
                    "/api/v1/content/journal_v2_news_links", headers=headers,
                    json={"documents": [{
                        "owner": scope, "key": _journal_news_link_key(scoped_document),
                        "document": scoped_document,
                    }]},
                )
                sync_states = client.post(
                    "/api/v1/content/journal_sync_states", headers=headers,
                    json={"documents": [{
                        "owner": "legacy", "key": "fill-1",
                        "document": {
                            "collection": "journal_group_overrides", "owner": "legacy",
                            "document_key": "fill-1", "origin_broker": "legacy",
                            "origin_environment": "unknown", "origin_account_ref": "legacy-unassigned",
                            "canonical_account_ref": "legacy-unassigned",
                            "is_deleted": False, "updated_at": "2026-09-13T10:00:00",
                        },
                    }]},
                )
        self.assertEqual(1, columns.json()["saved"])
        self.assertEqual(1, links.json()["saved"])
        self.assertEqual(1, scoped_links.json()["saved"])
        self.assertEqual(1, sync_states.json()["saved"])

    def test_current_fastapi_and_content_client_complete_journal_round_trip(self) -> None:
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = CentralServerSettings(
                f"sqlite:///{root / 'server.sqlite3'}", "private-token",
            )
            source_path, target_path = root / "source.sqlite3", root / "target.sqlite3"
            source = JournalRepository(source_path)
            target = JournalRepository(target_path)
            source.assign_group(("fill-1",), "manual:round-trip")

            with TestClient(create_app(settings)) as api:
                def opener(request, **_kwargs):
                    parsed = urlsplit(request.full_url)
                    response = api.request(
                        request.method, parsed.path + (f"?{parsed.query}" if parsed.query else ""),
                        headers=dict(request.header_items()), content=request.data,
                    )
                    if response.status_code >= 400:
                        raise HTTPError(
                            request.full_url, response.status_code, response.reason_phrase,
                            dict(response.headers), io.BytesIO(response.content),
                        )
                    return Response(response.content)

                client = CentralContentClient(
                    "https://in-process.test", "private-token", opener=opener,
                )
                self.assertGreater(CentralJournalSyncService(client).sync(source_path), 0)
                self.assertGreater(CentralJournalSyncService(client).sync(target_path), 0)

            self.assertEqual(
                {"fill-1": "manual:round-trip"}, target.load_group_overrides(),
            )

    def test_v2_sync_state_rejects_mismatch_and_round_trips_valid_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = CentralServerSettings(
                f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token",
            )
            headers = {"Authorization": "Bearer private-token"}
            scope = "11111111-1111-4111-8111-111111111111"
            document = {
                "collection": "journal_v2_group_overrides", "owner": scope,
                "document_key": "fill:v2:key", "origin_broker": "kiwoom",
                "origin_environment": "real", "origin_account_ref": scope,
                "canonical_account_ref": scope, "is_deleted": True,
                "updated_at": "2026-09-13T10:00:00",
            }
            with TestClient(create_app(settings)) as client:
                malformed = client.post(
                    "/api/v1/content/journal_v2_sync_states", headers=headers,
                    json={"documents": [{"owner": scope, "key": "wrong", "document": document}]},
                )
                valid = client.post(
                    "/api/v1/content/journal_v2_sync_states", headers=headers,
                    json={"documents": [{
                        "owner": scope, "key": "fill:v2:key", "document": document,
                    }]},
                )
                loaded = client.get(
                    "/api/v1/content/journal_v2_sync_states", headers=headers,
                )
            self.assertEqual(422, malformed.status_code)
            self.assertEqual(1, valid.json()["saved"])
            self.assertEqual(document, loaded.json()["documents"][0]["document"])


if __name__ == "__main__":
    unittest.main()
