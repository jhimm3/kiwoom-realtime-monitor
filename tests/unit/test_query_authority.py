"""Query bearer boundaries exercised through actual loopback HTTP/WebSocket."""
from __future__ import annotations

import asyncio
import inspect
import json
import socket
import shutil
import subprocess
import sys
import threading
import time
import unittest
from contextlib import asynccontextmanager, contextmanager, ExitStack
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

import httpx
import uvicorn
from fastapi.testclient import TestClient
from fastapi import FastAPI, Header, HTTPException
from websockets.sync.client import connect
from websockets.exceptions import InvalidStatus

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.market_observations import KST
from kiwoom_monitor.central_server.market_query_routes import QUERY_MARKET_ENDPOINTS
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.realtime_routes import QUERY_REALTIME_EVENT_TYPES
from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient, CentralContentHttpError
from kiwoom_monitor.infrastructure.central_news_client import CentralNewsClient
from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient, CentralServerUnavailable
from kiwoom_monitor.application.ranking_service import RankingService
from kiwoom_monitor.application.minute_chart_service import MinuteChartService
from kiwoom_monitor.application.stock_fundamentals_service import StockFundamentalsService


NOW = datetime(2026, 10, 24, 10, 0, 0, tzinfo=KST)
ADMIN, QUERY = "authority-admin-fixture", "authority-query-fixture"


def settings(database="sqlite:///:memory:", query=QUERY):
    return CentralServerSettings(database, ADMIN, query_access_token=query,
        news_naver_api_enabled=False, news_naver_market_enabled=False,
        news_query_set_enabled=False, autonomous_top20_enabled=False)


def query_app(selected):
    from kiwoom_monitor.central_server.database import create_query_store
    store = create_query_store(selected.database_url)
    with patch("kiwoom_monitor.central_server.app.create_query_store", return_value=store):
        app = create_app(selected)
    app.state.query_authority_store = store
    return app


@contextmanager
def wire_server(app):
    """Keep one actual bound socket/thread and join lifespan before returning."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    original = app.router.lifespan_context
    loop_holder = []

    @asynccontextmanager
    async def lifespan(selected):
        loop_holder.append(asyncio.get_running_loop())
        async with original(selected):
            yield

    app.router.lifespan_context = lifespan
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False,
                                          ws="websockets-sansio", lifespan="on"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, name="query-authority-loopback")
    thread.start()
    deadline = time.monotonic() + 5
    try:
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not server.started:
            raise AssertionError("isolated loopback did not start")
        yield f"http://127.0.0.1:{port}", loop_holder[0]
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        if thread.is_alive():
            raise AssertionError("query-authority loopback server did not drain")


class QueryAuthorityTests(unittest.TestCase):
    def test_optional_token_preserves_admin_and_rejects_ambiguous_configuration(self):
        with self.assertRaises(ValueError):
            CentralServerSettings("sqlite:///:memory:", ADMIN, query_access_token=ADMIN)
        with patch.dict("os.environ", {"MONITOR_SERVER_ACCESS_TOKEN": ADMIN,
                                      "MONITOR_SERVER_QUERY_ACCESS_TOKEN": QUERY}, clear=True):
            self.assertEqual(QUERY, CentralServerSettings.from_environment().query_access_token)
        with TestClient(query_app(settings(query=""))) as client:
            admin = client.get("/api/v1/capabilities", headers={"Authorization": "Bearer " + ADMIN})
            self.assertEqual(200, admin.status_code)
            self.assertNotIn("access_scope", admin.json())
            self.assertTrue(admin.json()["capabilities"]["mock_automation_spec_publish_v1"])
            self.assertEqual(401, client.get("/api/v1/capabilities", headers={"Authorization": "Bearer " + QUERY}).status_code)

    def test_wire_read_whitelist_and_private_get_command_handlers_never_run(self):
        app = query_app(settings())
        private = [
            ("GET", "/api/v1/settings/credentials", None),
            ("GET", "/api/v1/settings/credential-operations/00000000-0000-0000-0000-000000000001", None),
            ("GET", "/api/v3/kiwoom/accounts", None),
            ("GET", "/api/v1/mock/orders/intent", None),
            ("GET", "/api/v1/settings/operations", None),
            ("GET", "/api/v1/news/chatgpt/models", None),
            ("GET", "/api/v1/news/sources", None),
            ("GET", "/api/v1/diagnostics/resources", None),
            ("GET", "/api/v1/research/ram-shadow", None),
            ("POST", "/api/v1/news/search", {"stock_code": "005930", "stock_name": "fixture"}),
            ("POST", "/api/v1/mock-automation/start", {"credential_profile_id": "fixture", "account_ref": "a" * 36,
                "spec_id": "fixture", "expected_settings_revision": 1, "credential_revision": 1}),
            ("POST", "/api/v1/mock-automation/stop", {"credential_profile_id": "fixture", "account_ref": "a" * 36,
                "spec_id": "fixture", "expected_control_revision": 1, "reason": "fixture"}),
            ("POST", "/api/v1/content/theme_profile", {"documents": [{"owner": "market", "key": "fixture", "document": {}}]}),
            ("PUT", "/api/v1/content/theme_profile", {"documents": []}),
        ]
        with wire_server(app) as (base, _loop), httpx.Client(base_url=base, timeout=5) as client, ExitStack() as stack:
            spies = []
            private_paths = {path for _, path, _ in private}
            for route in app.routes:
                if getattr(route, "path", "") in private_paths or getattr(route, "path", "") in {
                    "/api/v1/settings/credential-operations/{operation_id}", "/api/v1/mock/orders/{intent_id}",
                    "/api/v1/content/{collection}"}:
                    original = route.dependant.call
                    spy = (AsyncMock if inspect.iscoroutinefunction(original) else Mock)(wraps=original)
                    stack.enter_context(patch.object(route.dependant, "call", spy))
                    spies.append(spy)
            for method, path, body in private:
                response = client.request(method, path, json=body, headers={"Authorization": "Bearer " + QUERY})
                self.assertEqual(403, response.status_code, (method, path, response.text))
            for collection in ("journal_v2_fills", "app_settings", "news_request_usage", "news_sync", "not_registered"):
                self.assertEqual(403, client.get("/api/v1/content/" + collection,
                    headers={"Authorization": "Bearer " + QUERY}).status_code)
            for spy in spies:
                spy.assert_not_called()
            stack.close()
            for collection in ("theme_profile", "theme_stock", "theme_metadata", "stock_fundamentals",
                               "stock_nxt_eligibility", "stock_price_references", "historical_highs"):
                self.assertEqual(200, client.get("/api/v1/content/" + collection,
                    headers={"Authorization": "Bearer " + QUERY}).status_code)
            for path in ("/api/v1/themes/current-snapshot", "/api/v1/market/minute-bars?code=005930&trading_date=2026-10-24",
                         "/api/v1/market/daily-bars?code=005930", "/api/v1/market/snapshots/ranking",
                         "/api/v1/news/history/article", "/api/v1/news/market-feed?source=common"):
                self.assertEqual(200, client.get(path, headers={"Authorization": "Bearer " + QUERY}).status_code, path)
            self.assertEqual(401, client.get("/api/v1/capabilities").status_code)
            self.assertEqual(401, client.get("/api/v1/capabilities", headers={"Authorization": "Bearer invalid"}).status_code)
            caps = client.get("/api/v1/capabilities", headers={"Authorization": "Bearer " + QUERY}).json()
            self.assertEqual("query", caps["access_scope"])
            for key in ("runtime_credentials_v1", "account_contexts_v3", "execution_event_read_v1", "shared_settings",
                        "trade_journal", "mock_automation_spec_publish_v1", "mock_automation_runtime_v1"):
                self.assertFalse(caps["capabilities"][key])
            self.assertEqual(503, client.get("/api/v1/settings/credentials", headers={"Authorization": "Bearer " + ADMIN}).status_code)

    def test_raw_public_market_exact_paths_reuse_broker_and_reject_account_before_native_call(self):
        class Transport:
            calls = []
            def request_with_continuation(self, api_id, path, body, **continuation):
                self.calls.append((api_id, path, body, continuation))
                return {"api_id": api_id, "body": body}, False, ""
        transport = Transport()
        broker = CentralRestBroker(transport)
        from kiwoom_monitor.central_server.market_query_routes import create_market_query_router
        original_factory = create_market_query_router
        def factory(store, authorize, _broker, collector, **kwargs):
            return original_factory(store, authorize, broker, collector, **kwargs)
        with patch("kiwoom_monitor.central_server.market_query_routes.create_market_query_router", factory):
            app = query_app(settings())
        original_lifespan = app.router.lifespan_context
        @asynccontextmanager
        async def lifespan(selected):
            async with original_lifespan(selected):
                try:
                    yield
                finally:
                    await broker.close()
        app.router.lifespan_context = lifespan
        with wire_server(app) as (base, _loop), httpx.Client(base_url=base, timeout=5) as http:
            remote = RemoteKiwoomRestClient(base, QUERY)
            content = CentralContentClient(base, QUERY)
            self.assertEqual([], list(RankingService(remote).load_top_stocks()))
            self.assertEqual((), MinuteChartService(remote).load_today("005930", NOW))
            self.assertEqual([], transport.calls, "empty accepted store must not trigger TR")
            app.state.query_authority_store.upsert_documents("stock_fundamentals", [{
                "owner": "005930", "key": "latest", "document": {
                    "observed_at": remote.server_now().isoformat(), "payload": {"mac": "1000", "dstr_rt": "40"}}}])
            self.assertEqual(1000, StockFundamentalsService(remote).load("005930").market_cap_eok)
            for index, (api_id, path) in enumerate(QUERY_MARKET_ENDPOINTS.items()):
                body = {"stk_cd": f"{index + 1:06d}", "qry_tp": "5", "inds_cd": "001", "base_dt": "20261024"}
                self.assertEqual(api_id, remote.request(api_id, path, body)["api_id"])
            before = len(transport.calls)
            body = {"api_id": "ka00198", "path": "/api/dostk/stkinfo", "body": {"qry_tp": "5", "duplicate": True}}
            first = http.post("/api/v1/kiwoom/query", json=body, headers={"Authorization": "Bearer " + QUERY})
            second = http.post("/api/v1/kiwoom/query", json=body, headers={"Authorization": "Bearer " + QUERY})
            self.assertEqual(first.json()["payload"], second.json()["payload"])
            self.assertEqual(before + 1, len(transport.calls), "same public query retains broker RAM dedup")
            before = len(transport.calls)
            for api_id, path in (("ka00001", "/api/dostk/acnt"), ("kt00007", "/api/dostk/acnt"),
                                 ("ka10075", "/api/dostk/acnt"), ("kt10000", "/api/dostk/ordr"),
                                 ("ka00198", "/api/dostk/acnt"), ("unknown", "/api/dostk/stkinfo")):
                response = http.post("/api/v1/kiwoom/query", json={"api_id": api_id, "path": path, "body": {}},
                                     headers={"Authorization": "Bearer " + QUERY})
                self.assertEqual(403, response.status_code)
            self.assertEqual(before, len(transport.calls))
            with self.assertRaises(CentralContentHttpError) as denied:
                content.load("journal_v2_fills")
            self.assertEqual(403, denied.exception.status_code)
            with self.assertRaises(Exception) as permission:
                remote.request("kt00007", "/api/dostk/acnt", {})
            self.assertNotIsInstance(permission.exception, CentralServerUnavailable)
        self.assertIsNone(broker._worker)

    def test_query_hub_rejects_account_and_unknown_before_queue_budget(self):
        hub = RealtimeHub()
        query = hub.connect(allowed_event_types=QUERY_REALTIME_EVENT_TYPES)
        admin = hub.connect()
        for index in range(1001):
            hub.publish({"type": "account_balance", "payload": {"code": "005930", "private": index}}, "005930")
            hub.publish({"type": "future_private", "payload": {"private": index}})
        self.assertEqual(0, query.queue.qsize())
        self.assertEqual(0, query.dropped_events)
        self.assertGreater(admin.dropped_events, 0)
        for index in range(1001):
            hub.publish({"type": "trade", "payload": {"code": "005930", "current_price": index}}, "005930")
        self.assertEqual(1, query.dropped_events)
        self.assertEqual(1000, query.queue.qsize())

    def test_processor_off_news_history_reads_native_acceptance_without_work_mutation(self):
        app = query_app(settings())
        store = app.state.query_authority_store
        sql = []
        with wire_server(app) as (base, _loop), httpx.Client(base_url=base, timeout=5) as client:
            seeded = client.post("/api/v1/content/news_article", headers={"Authorization": "Bearer " + ADMIN},
                json={"documents": [{"owner": "005930", "key": "accepted-news-fixture",
                    "collector_id": "offline-fixture", "document": {"title": "native accepted result"}}]})
            self.assertEqual(200, seeded.status_code)
            accepted = store.load_news_history("article", target="005930", identity="accepted-news-fixture")
            before_settings = store.load_documents("news_automation_settings")
            with store._connection() as connection:
                before_jobs = connection.execute("SELECT * FROM central_news_jobs ORDER BY job_key").fetchall()
            original = store._connection
            @contextmanager
            def traced():
                with original() as connection:
                    connection.set_trace_callback(sql.append)
                    try:
                        yield connection
                    finally:
                        connection.set_trace_callback(None)
            with patch.object(store, "_connection", traced):
                result = client.get("/api/v1/news/history/article?target=005930&identity=accepted-news-fixture",
                    headers={"Authorization": "Bearer " + QUERY})
                self.assertEqual(200, result.status_code)
                self.assertEqual(accepted, result.json()["revisions"])
                self.assertEqual("native accepted result", result.json()["revisions"][0]["document"]["title"])
                news = CentralNewsClient(base, QUERY)
                page, next_offset = news.stored_page("005930", "fixture", ai=None)
                self.assertEqual(["native accepted result"], [item.title for item in page])
                self.assertIsNone(next_offset)
                self.assertEqual(((), None), news.stored_page("000000", "unknown", ai=None))
                self.assertEqual(((), None), news.stored_page("005930", "fixture", offset=200, ai=None))
            self.assertTrue(any("central_news_article_revisions" in statement for statement in sql))
            self.assertFalse([statement for statement in sql if statement.lstrip().split()[0].upper() in {
                "INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}])
            self.assertEqual(before_settings, store.load_documents("news_automation_settings"))
            with store._connection() as connection:
                self.assertEqual(before_jobs, connection.execute("SELECT * FROM central_news_jobs ORDER BY job_key").fetchall())
            body = {"stock_code": "005930", "stock_name": "fixture", "ai_auto_analyze": True}
            self.assertEqual(403, client.post("/api/v1/news/stored-page", json=body,
                headers={"Authorization": "Bearer " + QUERY}).status_code)
            self.assertEqual(503, client.post("/api/v1/news/stored-page", json=body,
                headers={"Authorization": "Bearer " + ADMIN}).status_code, "processor is physically unconstructed")

    def test_stored_page_keeps_admin_priority_automation_and_query_read_only(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.central_server.news_service import CentralNewsService
        from kiwoom_monitor.central_server.news_service_routes import create_news_search_router
        store = SQLiteQueryStore(Path(":memory:"), now_provider=lambda: NOW)
        store.initialize()
        provider = Mock()
        provider.search.side_effect = AssertionError("stored read must not call provider")
        priority = Mock()
        service = CentralNewsService(provider, store, read_only_search=True,
                                     now_provider=lambda: NOW, prioritize_stock=priority)
        def read_scope(authorization: str | None = Header(default=None)):
            if authorization == "Bearer " + QUERY: return "query"
            if authorization == "Bearer " + ADMIN: return "admin"
            raise HTTPException(status_code=401)
        def admin_scope(authorization: str | None = Header(default=None)):
            if read_scope(authorization) != "admin": raise HTTPException(status_code=403)
        app = FastAPI()
        app.include_router(create_news_search_router(service, admin_scope, read_authorize=read_scope, store=store))
        try:
            with wire_server(app) as (base, _loop), httpx.Client(base_url=base, timeout=5) as client:
                pure = {"stock_code": "005930", "stock_name": "fixture", "offset": 0}
                query = client.post("/api/v1/news/stored-page", json=pure, headers={"Authorization": "Bearer " + QUERY})
                self.assertEqual(200, query.status_code)
                priority.assert_not_called()
                self.assertEqual([], store.load_documents("news_automation_settings"))
                command = {**pure, "ai_auto_analyze": True, "ai_provider": "openai", "ai_model": "fixture-model"}
                self.assertEqual(403, client.post("/api/v1/news/stored-page", json=command,
                    headers={"Authorization": "Bearer " + QUERY}).status_code)
                admin = client.post("/api/v1/news/stored-page", json=command,
                    headers={"Authorization": "Bearer " + ADMIN})
                self.assertEqual(200, admin.status_code)
                self.assertEqual(query.json(), admin.json())
                priority.assert_called_once_with("005930")
                self.assertEqual({"auto_analyze": True, "auto_recent_limit": 10,
                    "provider": "openai", "model": "fixture-model"},
                    store.load_documents("news_automation_settings")[0]["document"])
                client.post("/api/v1/news/stored-page", json={**command, "offset": 200},
                    headers={"Authorization": "Bearer " + ADMIN}).raise_for_status()
                priority.assert_called_once_with("005930")
                provider.search.assert_not_called()
        finally:
            store.close()

    def test_read_router_imports_with_processor_files_physically_absent(self):
        source = Path(__file__).resolve().parents[2] / "src"
        excluded = {"central_server/news_service.py", "central_server/news_jobs.py",
            "central_server/news_sources.py", "central_server/market_news_sources.py",
            "central_server/ai_service.py", "infrastructure/news_ai.py",
            "infrastructure/naver_stock_news.py", "infrastructure/dart_disclosures.py"}
        with TemporaryDirectory(prefix="query-authority-no-processor-") as directory:
            target = Path(directory) / "src"
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for relative in excluded:
                (target / "kiwoom_monitor" / relative).unlink()
            script = '''
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.news_service_routes import create_news_search_router
from kiwoom_monitor.central_server.news_read_routes import load_stored_news_page
store = SQLiteQueryStore(Path(":memory:"))
store.initialize()
app = FastAPI()
app.include_router(create_news_search_router(None, lambda: None, read_authorize=lambda: "query", store=store))
with TestClient(app) as client:
    response = client.post("/api/v1/news/stored-page", json={"stock_code":"005930","stock_name":"fixture"})
    assert response.status_code == 200, response.text
    assert response.json() == {"stock_code":"005930","items":[],"next_offset":None}
assert "kiwoom_monitor.infrastructure.news_ai" not in sys.modules
assert "kiwoom_monitor.central_server.news_service" not in sys.modules
store.close()
print("processor-files-absent-read-pass")
'''
            import os
            env = dict(os.environ, PYTHONPATH=str(target), PYTHONDONTWRITEBYTECODE="1")
            result = subprocess.run([sys.executable, "-c", script], cwd=directory, env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("processor-files-absent-read-pass", result.stdout)

    def test_wire_collector_arrays_snapshots_account_exclusion_and_existing_worker_decode(self):
        app = query_app(settings())
        hub = app.state.realtime_hub
        collector = CentralRealtimeCollector(lambda: "offline", "real", hub, lambda: NOW)
        store = app.state.query_authority_store
        with patch.object(store, "load_realtime_snapshots", return_value=[
            {"type": "account_balance", "payload": {"code": "005930", "private": "initial"}},
            {"type": "future_private", "payload": {"private": "initial"}},
            {"type": "stock_reference", "payload": {"code": "005930", "market": "KRX", "high_price": 123}},
        ]), wire_server(app) as (base, loop):
            ws_url = base.replace("http://", "ws://") + "/api/v1/realtime"
            with connect(ws_url, additional_headers={"Authorization": "Bearer " + QUERY}, open_timeout=5) as query, \
                 connect(ws_url + "?token=" + ADMIN, open_timeout=5) as admin:
                self.assertEqual("ready", json.loads(query.recv(timeout=5))["type"])
                self.assertEqual("ready", json.loads(admin.recv(timeout=5))["type"])
                query.send(json.dumps({"type": "subscribe", "codes": ["005930"], "nxt_codes": [], "role": "admin"}))
                initial = []
                while True:
                    event = json.loads(query.recv(timeout=5)); initial.append(event)
                    if event["type"] == "central_ready": break
                self.assertEqual(["subscribed", "stock_reference", "central_ready"], [item["type"] for item in initial])
                async def publish():
                    collector._publish_parsed({"trnm": "REAL", "data": [
                        {"type": "0B", "item": "005930", "values": {"10": "+60700", "20": "100000", "15": "2"}},
                        {"type": "00", "values": {"9203": "18", "909": "7", "9001": "A005930", "913": "체결",
                            "905": "+매수", "908": "100000", "910": "60700", "911": "2"}},
                        {"type": "04", "item": "A005930", "values": {"930": "2", "933": "2", "10": "60700"}},
                        {"type": "0s", "item": "", "values": {"215": "3", "20": "100000"}},
                    ]})
                    hub.publish({"type": "account_balance", "payload": {"private": "empty-code"}})
                    hub.publish({"type": "future_private", "payload": {"private": "future"}})
                    hub.publish({"type": "codes_added", "codes": ["005930"], "source_resets": [{"code": "005930", "market": "KRX"}]})
                    hub.publish({"type": "subscription_ready", "scope": "upstream", "codes": ["005930"],
                                 "connection_status": {"generation": 7}})
                asyncio.run_coroutine_threadsafe(publish(), loop).result(5)
                received = []
                while True:
                    event = json.loads(query.recv(timeout=5)); received.append(event)
                    if event["type"] == "subscription_ready": break
                self.assertIn("market_operation", [event["type"] for event in received])
                tick = next(event for event in received if event["type"] == "trade")
                self.assertEqual(60700, tick["payload"]["current_price"])
                self.assertFalse({"account_balance", "order_execution", "future_private"} & {event["type"] for event in received})
                self.assertEqual([{"code": "005930", "market": "KRX"}], next(event for event in received if event["type"] == "codes_added")["source_resets"])
                from kiwoom_monitor.infrastructure.kiwoom_rest.central_realtime_worker import CentralRealtimeWorker
                from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
                worker = CentralRealtimeWorker(DataSourceSettings("personal_server", base, QUERY), ("005930",))
                values = []; worker.trade_received.connect(values.append)
                worker._dispatch(tick)
                self.assertEqual(60700, values[0].current_price)
                seen_admin = []
                while True:
                    event = json.loads(admin.recv(timeout=5)); seen_admin.append(event["type"])
                    if event["type"] == "subscription_ready": break
                self.assertIn("order_execution", seen_admin)
                self.assertIn("account_balance", seen_admin)
                query.send(json.dumps({"type": "ping"}))
                self.assertEqual("pong", json.loads(query.recv(timeout=5))["type"])
            for token in (QUERY, "invalid"):
                with self.assertRaises(InvalidStatus):
                    with connect(ws_url + "?token=" + token, open_timeout=5): pass
            self.assertEqual(0, hub.client_count)

    def test_changed_query_token_applies_at_new_startup_and_handshake_only(self):
        with wire_server(query_app(settings(query="replacement-query-fixture"))) as (base, _loop), httpx.Client(base_url=base, timeout=5) as client:
            self.assertEqual(401, client.get("/api/v1/capabilities", headers={"Authorization": "Bearer " + QUERY}).status_code)
            self.assertEqual(200, client.get("/api/v1/capabilities", headers={"Authorization": "Bearer replacement-query-fixture"}).status_code)
            self.assertEqual(200, client.get("/api/v1/capabilities", headers={"Authorization": "Bearer " + ADMIN}).status_code)
            with self.assertRaises(InvalidStatus):
                with connect(base.replace("http://", "ws://") + "/api/v1/realtime",
                             additional_headers={"Authorization": "Bearer " + QUERY}, open_timeout=5): pass


if __name__ == "__main__":
    unittest.main()
