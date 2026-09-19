from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from unittest.mock import AsyncMock, patch

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.news_sources import QuerySetNewsCollector, _source_id
from kiwoom_monitor.central_server.news_service import CentralNewsService
from kiwoom_monitor.infrastructure.central_operational_settings import CentralOperationalSettingsClient
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.infrastructure.naver_news import LocalNaverNewsConfig, NaverNewsCredentials, NaverNewsPage
from kiwoom_monitor.presentation.api_settings_dialog import ApiSettingsDialog
from qt_settings_test_support import dispose_dialogs, wait_until
from test_central_operational_settings import _Response
from test_news_source_collection import _PageClient, _item


QUERY_FIELDS = {"news_query_set_enabled": True, "news_query_set": ["증권", "수주 계약"],
                "news_query_set_refresh_seconds": 300}


class QueryOperationsBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteQueryStore(Path(self.temp.name) / "central.sqlite")
        self.store.initialize()
        self.store.upsert_documents("stock_catalog", [{"owner": "krx", "key": "005930",
            "document": {"stock_code": "005930", "stock_name": "삼성전자", "market": "KOSPI"}}])
        self.collector = QuerySetNewsCollector(_PageClient({}), self.store,
            queries=("증권", "코스피"), catalog_loader=lambda: ())

    async def asyncTearDown(self):
        await self.collector.close()
        self.store.close()
        self.temp.cleanup()

    async def test_off_during_accepted_query_finishes_it_but_stops_remaining_queries(self):
        entered, release = threading.Event(), threading.Event()

        class Client(_PageClient):
            def search_page(self, query, **kwargs):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("fake gate timeout")
                return super().search_page(query, **kwargs)

        self.collector._client = Client({})
        running = asyncio.create_task(self.collector.run_once())
        try:
            for _ in range(200):
                if entered.is_set(): break
                await asyncio.sleep(.005)
            self.assertTrue(entered.is_set())
            self.collector.update(enabled=False, queries=("증권", "코스피"), poll_seconds=60)
            release.set()
            await running
            self.assertEqual([("증권", 1)], self.collector._client.calls)
            self.assertIsNotNone(self.store.load_news_source_cursor(_source_id("증권")))
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)

    async def test_shorter_interval_uses_last_success_without_erasing_cursor(self):
        with patch("kiwoom_monitor.central_server.news_sources.time", return_value=1000):
            await self.collector.run_once()
        cursor = self.store.load_news_source_cursor(_source_id("증권"))
        self.assertEqual(1300, cursor["next_schedule_at"])
        self.collector.update(enabled=True, queries=("증권",), poll_seconds=60)
        with patch("kiwoom_monitor.central_server.news_sources.time", return_value=1061):
            completed = await self.collector.run_once()
        self.assertEqual(1, completed)
        self.assertEqual(cursor["cursor_identity"], self.store.load_news_source_cursor(_source_id("증권"))["cursor_identity"])

    async def test_longer_interval_delays_next_query_without_new_requests(self):
        with patch("kiwoom_monitor.central_server.news_sources.time", return_value=1000):
            await self.collector.run_once()
        self.collector.update(enabled=True, queries=("증권",), poll_seconds=600)
        with patch("kiwoom_monitor.central_server.news_sources.time", return_value=1301):
            self.assertEqual(0, await self.collector.run_once())
        self.assertEqual(2, len(self.collector._client.calls))

    async def test_failed_query_backoff_is_not_shortened_by_success_schedule(self):
        self.collector._client = _PageClient({("증권", 1): RuntimeError("fake failure")})
        with patch("kiwoom_monitor.central_server.news_sources.time", return_value=1000):
            await self.collector.run_once()
        self.collector.update(enabled=True, queries=("증권",), poll_seconds=60)
        with patch("kiwoom_monitor.central_server.news_sources.time", return_value=1061):
            self.assertEqual(0, await self.collector.run_once())

    async def test_changed_queries_stop_old_batch_and_resume_old_cursor_when_readded(self):
        client = _PageClient({})
        self.collector._client = client
        original = self.store.load_news_source_cursor
        changed = False

        def load(source_id):
            nonlocal changed
            result = original(source_id)
            if not changed:
                changed = True
                self.collector.update(enabled=True, queries=("상장사",), poll_seconds=60)
            return result

        with patch.object(self.store, "load_news_source_cursor", side_effect=load):
            self.assertEqual(0, await self.collector.run_once())
        self.assertEqual([], client.calls)
        await self.collector.run_once()
        cursor = original(_source_id("상장사"))
        self.collector.update(enabled=False, queries=(), poll_seconds=60)
        self.assertEqual(0, await self.collector.run_once())
        self.collector.update(enabled=True, queries=("상장사",), poll_seconds=60)
        self.assertEqual(0, await self.collector.run_once())
        self.assertEqual(cursor, original(_source_id("상장사")))


class QueryOperationsAPITests(unittest.TestCase):
    def test_api_updates_same_collector_and_preserves_articles_usage_cursor_and_restart(self):
        from fastapi.testclient import TestClient
        original = CentralNewsService.update_operational_settings
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(CentralNewsService, "start", new=AsyncMock()), \
                patch.object(CentralNewsService, "update_operational_settings", autospec=True, side_effect=original) as updater:
            settings = CentralServerSettings(f"sqlite:///{Path(directory) / 'central.sqlite'}", "token",
                naver_news_client_id="fake-id", naver_news_client_secret="fake-secret",
                news_history_jobs_enabled=False, news_query_set="증권")
            app = create_app(settings)
            service = updater.call_args.args[0]
            collector = service._query_collector
            collector._client = _PageClient({("증권", 1): NaverNewsPage((_item("retained"),), 1, 1, 1)})
            collector._catalog_loader = lambda: (("005930", "삼성전자", "KOSPI"),)
            headers = {"Authorization": "Bearer token"}
            with TestClient(app) as client:
                client.portal.call(collector.run_once)
                cursor = service._store.load_news_source_cursor(_source_id("증권"))
                history = service._store.load_news_history("article", target="GLOBAL")
                day = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
                used = service._store.news_request_count(day)
                saved = client.put("/api/v1/settings/operations", headers=headers, json={
                    "expected_revision": 0, "news_query_set_enabled": False,
                    "news_query_set": ["수주 계약"], "news_query_set_refresh_seconds": 60})
                self.assertEqual(200, saved.status_code)
                self.assertIs(collector, service._query_collector)
                self.assertFalse(collector._enabled)
                self.assertEqual(("수주 계약",), collector._queries)
                self.assertEqual(cursor, service._store.load_news_source_cursor(_source_id("증권")))
                self.assertEqual(history, service._store.load_news_history("article", target="GLOBAL"))
                self.assertEqual(used, service._store.news_request_count(day))
                self.assertEqual(0, client.portal.call(collector.run_once))
                self.assertEqual(409, client.put("/api/v1/settings/operations", headers=headers,
                    json={"expected_revision": 0, "news_query_set_enabled": True}).status_code)
                self.assertEqual(422, client.put("/api/v1/settings/operations", headers=headers,
                    json={"news_query_set": [str(n) for n in range(51)]}).status_code)
                self.assertEqual(422, client.put("/api/v1/settings/operations", headers=headers,
                    json={"news_query_set_refresh_seconds": 59}).status_code)
                self.assertEqual(401, client.put("/api/v1/settings/operations", json=QUERY_FIELDS).status_code)
            restarted = create_app(settings)
            restored_service = updater.call_args.args[0]
            with TestClient(restarted) as client:
                restored = client.get("/api/v1/settings/operations", headers=headers).json()
                self.assertFalse(restored["news_query_set_enabled"])
                self.assertEqual(["수주 계약"], restored["news_query_set"])
                self.assertEqual(60, restored_service._query_collector._poll_seconds)


class QueryOperationsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def tearDown(self):
        dispose_dialogs()

    def test_old_server_excludes_fields_and_supported_values_normalize_by_line(self):
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            with patch("kiwoom_monitor.presentation.api_settings_dialog.apply_to_local_news"):
                dialog._operations_loaded({"ai_provider": "none"})
                self.assertFalse(dialog._nas_query_text.isEnabled())
                self.assertNotIn("news_query_set", dialog._nas_operation_values())
                dialog._operations_loaded({"ai_provider": "none", **QUERY_FIELDS})
                self.assertTrue(dialog._nas_query_text.isEnabled())
                dialog._nas_query_text.setPlainText("  증권 \n\n 수주 계약 \n증권\n")
                self.assertEqual(["증권", "수주 계약"], dialog._nas_operation_values()["news_query_set"])
                dialog._set_nas_operations_enabled(False)
                self.assertFalse(dialog._nas_query_refresh.isEnabled())
            dialog.reject()

    def test_invalid_queries_do_not_start_io_and_off_allows_empty_list(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            with patch("kiwoom_monitor.presentation.api_settings_dialog.apply_to_local_news"):
                dialog._operations_loaded({"ai_provider": "none", **QUERY_FIELDS})
            dialog._nas_client = Mock()
            with patch("kiwoom_monitor.presentation.api_settings_dialog.QMessageBox.warning") as warning:
                for value in ("", "\n".join(str(n) for n in range(51))):
                    dialog._nas_query_text.setPlainText(value)
                    dialog._save_nas_operational_settings(dialog.data_source_values)
                self.assertEqual(2, warning.call_count)
                dialog._nas_client.save.assert_not_called()
            dialog._nas_query_enabled.setChecked(False)
            dialog._nas_query_text.clear()
            self.assertEqual([], dialog._nas_operation_values()["news_query_set"])
            dialog.reject()

    def test_small_window_scrolls_form_while_save_buttons_stay_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            dialog = ApiSettingsDialog(Path(directory) / "api.env", section="nas")
            with patch.object(dialog, "_load_nas_operational_settings"):
                dialog.show()
                dialog.resize(560, 450)
                self.qt.processEvents()
            self.assertGreater(dialog._nas_scroll.verticalScrollBar().maximum(), 0)
            self.assertTrue(dialog._buttons.isVisible())
            self.assertLess(dialog._buttons.geometry().bottom(), dialog.height())
            dialog.reject()

    def test_worker_sends_only_changed_query_fields_with_cas_and_preserves_pc_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = DataSourceSettings("personal_server", "https://nas.example.test", "token")
            DataSourceConfig(root / "data_source.json").save(source)
            local = LocalNaverNewsConfig(root / "naver_news.dat")
            local.save(NaverNewsCredentials("pc-id", "pc-secret"))
            dialog = ApiSettingsDialog(root / "api.env", section="nas")
            client = CentralOperationalSettingsClient(source)
            calls = []

            def opened(request, **kwargs):
                calls.append(request)
                return _Response({"revision": 4 if request.method == "GET" else 5,
                    "ai_provider": "none", **QUERY_FIELDS,
                    "news_query_set": ["증권"] if request.method == "PUT" else QUERY_FIELDS["news_query_set"]})

            with patch("kiwoom_monitor.presentation.api_settings_dialog.CentralOperationalSettingsClient", return_value=client), \
                    patch("kiwoom_monitor.infrastructure.central_operational_settings.urlopen", side_effect=opened):
                dialog._load_nas_operational_settings()
                wait_until(lambda: dialog._nas_operations_available)
                dialog._nas_query_text.setPlainText("증권")
                dialog._save_nas_operational_settings(source)
                wait_until(lambda: dialog._closed)
            import json
            self.assertEqual(["GET", "PUT"], [call.method for call in calls])
            self.assertEqual({"news_query_set": ["증권"], "expected_revision": 4}, json.loads(calls[1].data))
            self.assertEqual(NaverNewsCredentials("pc-id", "pc-secret"), local.load())
