from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from kiwoom_monitor.central_server.credential_runtime import CredentialRuntime, CredentialOperationError
from kiwoom_monitor.central_server.credential_store import CredentialStore, compose_credential_settings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.news_credentials import NaverCredentialOwner
from kiwoom_monitor.central_server.news_service import CentralNewsService
from kiwoom_monitor.infrastructure.naver_news import NaverNewsClient, NaverNewsCredentials, NaverNewsPage
import test_news_source_collection as news_fixture


class FakeNaver(NaverNewsClient):
    def __init__(self, credentials):
        super().__init__(credentials)
        self.marker = credentials.client_id
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.fail = False
        self.pages = 1

    def search_page(self, query, *, display=100, start=1, request_claim=None):
        if request_claim is not None and not request_claim():
            raise RuntimeError("budget exhausted")
        self.calls.append((query, start, display))
        if display != 1 and self.block:
            self.entered.set()
            if not self.release.wait(3):
                raise RuntimeError("fake gate timed out")
        if self.fail or self.marker == "invalid":
            raise RuntimeError("fake-sensitive-secret")
        item = news_fixture._item(f"{self.marker}-{query}-{start}")
        return NaverNewsPage((item,), 101 if self.pages == 2 else 1, start,
                             100 if self.pages == 2 and start == 1 else 1)

    def search(self, name, *, since=None, request_claim=None):
        return self.search_page(name, request_claim=request_claim).items


class NaverCredentialOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SQLiteQueryStore(self.root / "central.sqlite")
        self.store.initialize()
        self.store.replace_documents("stock_catalog", [{"owner": "krx", "key": "005930",
            "document": {"code": "005930", "name": "삼성전자", "market": "KOSPI"}}])
        self.vault = CredentialStore(self.root / "secrets", self.store)
        self.profile = NaverCredentialOwner.PROFILE
        self.vault.import_initial("naver", self.profile, {"client_id": "old", "client_secret": "fake-old"})
        self.old = FakeNaver(NaverNewsCredentials("old", "fake-old"))
        self.service = CentralNewsService(self.old, self.store, jobs_enabled=False,
            query_set_enabled=True, query_set=("증권",), query_set_refresh_seconds=120)
        self.owner = NaverCredentialOwner(self.service, self.vault)
        self.runtime = CredentialRuntime(self.vault, self.store)
        self.runtime.register("naver", self.owner.hooks())
        self.fake_patch = patch("kiwoom_monitor.central_server.news_credentials.NaverNewsClient", FakeNaver)
        self.fake_patch.start()
        self.date = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()

    async def asyncTearDown(self):
        self.old.release.set()
        await self.runtime.close()
        await self.service.close()
        self.fake_patch.stop()
        self.vault.close()
        self.store.close()
        self.temp.cleanup()

    async def ready(self, *, revision=1, marker="new", disabled=False):
        document = await self.runtime.prepare("naver", self.profile, str(uuid.uuid4()), revision,
            {} if disabled else {"client_id": marker, "client_secret": "fake-new"}, disabled=disabled)
        operation = self.runtime._operations[document["operation_id"]]
        await operation.task
        return operation

    async def apply(self, operation, revision=1):
        await self.runtime.apply(operation.operation_id, revision, None)
        await operation.task

    async def until(self, predicate):
        for _ in range(300):
            if predicate():
                return
            await asyncio.sleep(0.005)
        self.fail("fake work did not reach expected boundary")

    async def test_two_paths_change_together_without_resetting_cursor_articles_or_budget(self):
        collector = self.service._query_collector
        await collector.run_once()
        cursor = self.store.load_news_source_diagnostics(limit=5)["sources"]
        history = self.store.load_news_history("article", target="GLOBAL")
        with closing(sqlite3.connect(self.root / "central.sqlite")) as connection:
            jobs = connection.execute("SELECT * FROM central_news_jobs ORDER BY job_key").fetchall()
        before = self.store.news_request_count(self.date)
        operation = await self.ready()
        await self.apply(operation)
        self.assertEqual("ACTIVE", operation.state)
        self.assertEqual(2, self.owner.active_revision(self.profile))
        self.assertIs(collector, self.service._query_collector)
        self.assertIs(self.service._client, collector._client)
        self.assertEqual(cursor, self.store.load_news_source_diagnostics(limit=5)["sources"])
        self.assertEqual(history, self.store.load_news_history("article", target="GLOBAL"))
        with closing(sqlite3.connect(self.root / "central.sqlite")) as connection:
            self.assertEqual(jobs, connection.execute("SELECT * FROM central_news_jobs ORDER BY job_key").fetchall())
        self.assertEqual(before + 1, self.store.news_request_count(self.date))
        await self.service.search("005930", "삼성전자", None)
        self.assertEqual(before + 2, self.store.news_request_count(self.date))
        self.assertEqual("new", self.service._client.marker)
        self.assertEqual(0, await collector.run_once())  # Original next schedule remains effective.
        self.assertIsNone(operation.candidate.prepared)

    async def test_cancelled_watchlist_waiter_still_drains_actual_thread_before_commit(self):
        self.old.block = True
        waiter = asyncio.create_task(self.service.search("005930", "삼성전자", None))
        await self.until(self.old.entered.is_set)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        operation = await self.ready()
        await self.runtime.apply(operation.operation_id, 1, None)
        await self.until(lambda: self.service._naver_paused)
        self.assertEqual(1, self.vault.load("naver", self.profile).revision)
        self.assertFalse(operation.task.done())
        await self.service.search("000660", "다른종목", None)
        self.assertEqual(1, len(self.old.calls))
        self.old.release.set()
        await operation.task
        self.assertEqual("ACTIVE", operation.state)
        self.assertTrue(self.store.load_documents("news_sync", "005930", 1))
        self.assertNotIn(("005930", "삼성전자".casefold()), self.service._running)
        await self.service.search("000660", "다른종목", None)
        self.assertEqual("new", self.service._client.marker)
        self.assertEqual(1, len(self.service._client.calls) - 1)  # Probe plus fresh request.

    async def test_cancelled_query_waiter_finishes_all_old_pages_and_persistence(self):
        self.old.block = True
        self.old.pages = 2
        collector = self.service._query_collector
        waiter = asyncio.create_task(collector.run_once())
        await self.until(self.old.entered.is_set)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        operation = await self.ready()
        await self.runtime.apply(operation.operation_id, 1, None)
        await self.until(lambda: collector._credential_paused)
        self.assertFalse(operation.task.done())
        self.old.release.set()
        await operation.task
        self.assertEqual("ACTIVE", operation.state)
        self.assertEqual([1, 101], [call[1] for call in self.old.calls])
        self.assertEqual(2, len(self.store.load_news_history("article", target="GLOBAL")))
        self.assertEqual(3, self.store.news_request_count(self.date))
        self.assertEqual(0, await collector.run_once())
        collector.update(enabled=True, queries=("실적",), poll_seconds=120)
        await collector.run_once()
        self.assertEqual("실적", self.service._client.calls[-1][0])

    async def test_late_old_error_is_saved_before_new_revision_becomes_active(self):
        self.old.block = self.old.fail = True
        collector = self.service._query_collector
        waiter = asyncio.create_task(collector.run_once())
        await self.until(self.old.entered.is_set)
        entered, release = threading.Event(), threading.Event()
        original = self.store.save_news_source_page

        def save(value):
            if value.get("error"):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("fake persistence gate timed out")
            return original(value)

        try:
            with patch.object(self.store, "save_news_source_page", side_effect=save):
                operation = await self.ready()
                await self.runtime.apply(operation.operation_id, 1, None)
                await self.until(lambda: collector._credential_paused)
                self.old.release.set()
                await self.until(entered.is_set)
                self.assertEqual(1, self.owner.active_revision(self.profile))
                self.assertFalse(operation.task.done())
                release.set()
                await operation.task
                await waiter
        finally:
            release.set()
        self.assertEqual("ACTIVE", operation.state)
        self.assertEqual("query_set_error", self.store.load_news_source_diagnostics(limit=5)["sources"][0]["coverage"])

    async def test_drain_deadline_waits_for_real_end_then_keeps_old_revision(self):
        self.old.block = True
        waiter = asyncio.create_task(self.service.search("005930", "삼성전자", None))
        await self.until(self.old.entered.is_set)
        operation = await self.ready()
        self.runtime._deadline = 0.01
        await self.runtime.apply(operation.operation_id, 1, None)
        await self.until(lambda: operation.state == "BUSY")
        self.assertFalse(operation.task.done())
        self.old.release.set()
        await operation.task
        await waiter
        self.assertEqual("FAILED", operation.state)
        self.assertIs(self.old, self.service._client)
        self.assertEqual(1, self.owner.active_revision(self.profile))
        self.assertFalse(self.service._naver_paused)

    async def test_keyless_enable_keeps_existing_collector_configuration(self):
        await self.service.pause_naver_credentials()
        self.service.replace_naver_client(None)
        self.service.resume_naver_credentials()
        collector = self.service._query_collector
        collector.update(enabled=True, queries=("실적",), poll_seconds=180)
        operation = await self.ready()
        await self.apply(operation)
        self.assertIs(collector, self.service._query_collector)
        self.assertEqual(("실적",), collector._queries)
        self.assertEqual(180, collector._poll_seconds)
        await collector.run_once()
        self.assertEqual("실적", self.service._client.calls[-1][0])

    async def test_disable_does_not_probe_or_resurrect_environment_keys(self):
        from kiwoom_monitor.central_server.config import CentralServerSettings
        operation = await self.ready(disabled=True)
        await self.apply(operation)
        self.assertEqual("ACTIVE", operation.state)
        self.assertIsNone(self.service._client)
        self.assertIsNone(self.service._query_collector._client)
        self.assertEqual(0, self.store.news_request_count(self.date))
        self.assertTrue(self.vault.load("naver", self.profile).disabled)
        settings, _ = compose_credential_settings(CentralServerSettings("sqlite:///unused", "token",
            naver_news_client_id="environment-old", naver_news_client_secret="environment-old"), self.vault, self.store)
        self.assertEqual("", settings.naver_news_client_id)
        self.assertEqual(2, NaverCredentialOwner(self.service, self.vault).active_revision(self.profile))

    async def test_invalid_validation_is_secret_safe_and_keeps_old_client_and_usage(self):
        operation = await self.ready(marker="invalid")
        self.assertEqual("FAILED", operation.state)
        self.assertIs(self.old, self.service._client)
        self.assertEqual(1, self.store.news_request_count(self.date))
        self.assertEqual(1, self.vault.load("naver", self.profile).revision)
        self.assertNotIn("fake-sensitive-secret", json.dumps(await self.runtime.status(operation.operation_id)))

    async def test_stale_revision_and_exhausted_budget_do_not_replace_active_keys(self):
        with self.assertRaises(CredentialOperationError):
            await self.ready(revision=0)
        self.assertEqual(0, self.store.news_request_count(self.date))
        self.service._watchlist_request_limit = 0
        operation = await self.ready()
        self.assertEqual("FAILED", operation.state)
        self.assertEqual(0, self.store.news_request_count(self.date))
        self.assertIs(self.old, self.service._client)

    async def test_commit_failure_fences_old_keys_but_dart_remains_available(self):
        import test_central_news_service as service_fixture
        self.service._dart_client = service_fixture._DartClient()
        self.service._dart_enabled = True
        operation = await self.ready()
        with patch.object(self.vault, "save", side_effect=OSError("fake-sensitive-secret")):
            await self.apply(operation)
        self.assertEqual("RECOVERY_REQUIRED", operation.state)
        self.assertIsNone(self.owner.active_revision(self.profile))
        self.assertIsNone(self.service._client)
        self.assertIsNone(self.service._query_collector._client)
        self.assertFalse(self.service._naver_paused)
        await self.service.search("005930", "삼성전자", None)
        self.assertEqual(1, self.service._dart_client.calls)
        self.assertEqual([], self.old.calls)

    async def test_publish_failure_keeps_committed_receipt_and_recovers_with_new_apply(self):
        operation = await self.ready()
        original = self.service.replace_naver_client

        def replace(client):
            if client is not None:
                raise RuntimeError("fake-sensitive-secret")
            return original(client)

        with patch.object(self.service, "replace_naver_client", side_effect=replace):
            await self.apply(operation)
        self.assertEqual("RECOVERY_REQUIRED", operation.state)
        self.assertTrue(operation.committed)
        self.assertIsNone(self.service._client)
        self.assertEqual(2, self.vault.load("naver", self.profile).revision)
        recovered = await self.ready(revision=2, marker="recovered")
        await self.apply(recovered, revision=2)
        self.assertEqual("ACTIVE", recovered.state)
        self.assertEqual(3, self.owner.active_revision(self.profile))

    async def test_cancelled_candidate_leaves_active_collection_untouched(self):
        operation = await self.ready()
        await self.runtime.cancel(operation.operation_id)
        self.assertEqual("CANCELLED", operation.state)
        self.assertIsNone(operation.candidate.prepared)
        self.assertIs(self.old, self.service._client)
        self.assertEqual(1, self.vault.load("naver", self.profile).revision)

    async def test_shutdown_waits_for_cancelled_watchlist_thread_and_its_db_write(self):
        self.old.block = True
        waiter = asyncio.create_task(self.service.search("005930", "삼성전자", None))
        await self.until(self.old.entered.is_set)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        closing = asyncio.create_task(self.service.close())
        await self.until(lambda: self.service._closing.is_set())
        self.assertFalse(closing.done())
        self.old.release.set()
        await closing
        self.assertTrue(self.store.load_documents("news_sync", "005930", 1))
        self.assertEqual(1, len(self.old.calls))

    async def test_query_shutdown_waits_for_all_pages_after_outer_loop_cancellation(self):
        self.old.block = True
        self.old.pages = 2
        collector = self.service._query_collector
        await collector.start()
        await self.until(self.old.entered.is_set)
        closing = asyncio.create_task(collector.close())
        await self.until(lambda: collector._closing.is_set())
        self.assertFalse(closing.done())
        self.old.release.set()
        await closing
        self.assertEqual([1, 101], [call[1] for call in self.old.calls])
        self.assertEqual(2, len(self.store.load_news_history("article", target="GLOBAL")))


class NaverCredentialAPITests(unittest.TestCase):
    def test_keyless_server_registers_naver_without_ai_and_applies_only_default_profile(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        with tempfile.TemporaryDirectory() as directory, patch(
            "kiwoom_monitor.central_server.news_credentials.NaverNewsClient", FakeNaver):
            root = Path(directory)
            app = create_app(CentralServerSettings(f"sqlite:///{root / 'central.sqlite'}", "private-token",
                credential_directory=str(root / "secrets"), news_query_set_enabled=False,
                news_history_jobs_enabled=False))
            with TestClient(app, base_url="https://nas.test") as client:
                headers = {"Authorization": "Bearer private-token"}
                runtime = app.state.credential_runtime
                service = runtime._hooks["naver"].prepare.__self__.service
                self.assertIsNone(service._client)
                self.assertIsNotNone(service._query_collector)
                path = f"/api/v1/settings/credentials/naver/profiles/{NaverCredentialOwner.PROFILE}/prepare"
                payload = {"request_id": str(uuid.uuid4()), "expected_revision": 0,
                           "replacement": {"client_id": "new", "client_secret": "fake-secret"}}
                self.assertEqual(401, client.post(path, json=payload).status_code)
                response = client.post(path, headers=headers, json=payload)
                self.assertEqual(202, response.status_code)
                operation_id = response.json()["operation_id"]

                async def wait():
                    await runtime._operations[operation_id].task

                client.portal.call(wait)
                operation_path = f"/api/v1/settings/credential-operations/{operation_id}"
                status = client.get(operation_path, headers=headers).json()
                self.assertEqual("READY", status["state"])
                self.assertIsNone(status["target_account_ref"])
                response = client.post(operation_path + "/apply", headers=headers,
                    json={"expected_revision": 0, "target_account_ref": None})
                self.assertEqual(202, response.status_code)
                client.portal.call(wait)
                status = client.get(operation_path, headers=headers).json()
                self.assertEqual("ACTIVE", status["state"])
                self.assertNotIn("fake-secret", json.dumps(status))
                other = client.post("/api/v1/settings/credentials/naver/profiles", headers=headers,
                    json={"request_id": str(uuid.uuid4()), "label": "unsupported"}).json()["profile_id"]
                rejected = client.post(f"/api/v1/settings/credentials/naver/profiles/{other}/prepare",
                    headers=headers, json={**payload, "request_id": str(uuid.uuid4())})
                self.assertEqual("PROFILE_RUNTIME_NOT_READY", rejected.json()["detail"]["code"])
