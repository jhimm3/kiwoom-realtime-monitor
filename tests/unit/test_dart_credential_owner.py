from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from kiwoom_monitor.central_server.credential_runtime import CredentialRuntime, CredentialOperationError
from kiwoom_monitor.central_server.credential_store import CredentialStore, compose_credential_settings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.news_credentials import DartCredentialOwner, NaverCredentialOwner
from kiwoom_monitor.central_server.news_service import CentralNewsService
from kiwoom_monitor.infrastructure.dart_disclosures import DartDisclosureClient, DartCredentialValidationError
from kiwoom_monitor.infrastructure.naver_news import NaverNewsCredentials
from test_naver_credential_owner import FakeNaver


class FakeDart(DartDisclosureClient):
    def __init__(self, key, cache_path):
        super().__init__(key, cache_path)
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False

    def _json(self, url):
        query = parse_qs(urlsplit(url).query)
        self.calls.append(query)
        if "corp_code" in query and self.block:
            self.entered.set()
            if not self.release.wait(3):
                raise RuntimeError("fake gate timeout")
        if self._api_key in {"invalid", "quota"}:
            return {"status": "010" if self._api_key == "invalid" else "020", "message": self._api_key}
        return {"status": "000", "list": [{"rcept_no": "2026091500" + self._api_key,
            "report_nm": "삼성전자 공급계약 체결", "flr_nm": "삼성전자", "rcept_dt": "20260915"}]}


class DartCredentialOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = self.root / "corp-codes.json"
        self.cache.write_text('{"005930": "00126380"}', encoding="utf-8")
        self.cache_before = (self.cache.read_bytes(), self.cache.stat().st_mtime_ns)
        self.store = SQLiteQueryStore(self.root / "central.sqlite")
        self.store.initialize()
        self.store.replace_documents("stock_catalog", [{"owner": "krx", "key": "005930",
            "document": {"code": "005930", "name": "삼성전자", "market": "KOSPI"}}])
        self.vault = CredentialStore(self.root / "secrets", self.store)
        self.profile = DartCredentialOwner.PROFILE
        self.vault.import_initial("dart", self.profile, {"api_key": "old"})
        self.vault.import_initial("naver", NaverCredentialOwner.PROFILE,
            {"client_id": "old-naver", "client_secret": "fake-secret"})
        self.old = FakeDart("old", self.cache)
        self.naver = FakeNaver(NaverNewsCredentials("old-naver", "fake-secret"))
        self.service = CentralNewsService(self.naver, self.store, self.old,
            jobs_enabled=False, query_set=("증권",))
        self.owner = DartCredentialOwner(self.service, self.vault, self.cache)
        self.naver_owner = NaverCredentialOwner(self.service, self.vault)
        self.runtime = CredentialRuntime(self.vault, self.store)
        self.runtime.register("dart", self.owner.hooks())
        self.runtime.register("naver", self.naver_owner.hooks())
        self.patches = [patch("kiwoom_monitor.central_server.news_credentials.DartDisclosureClient", FakeDart),
            patch("kiwoom_monitor.central_server.news_credentials.NaverNewsClient", FakeNaver),
            patch("kiwoom_monitor.infrastructure.dart_disclosures.urlopen", side_effect=AssertionError("no real API"))]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        self.old.release.set()
        self.naver.release.set()
        await self.runtime.close()
        await self.service.close()
        for item in reversed(self.patches):
            item.stop()
        self.vault.close()
        self.store.close()
        self.temp.cleanup()

    async def until(self, predicate):
        for _ in range(300):
            if predicate():
                return
            await asyncio.sleep(0.005)
        self.fail("fake work did not reach boundary")

    async def ready(self, *, revision=1, key="new", disabled=False):
        document = await self.runtime.prepare("dart", self.profile, str(uuid.uuid4()), revision,
            {} if disabled else {"api_key": key}, disabled=disabled)
        operation = self.runtime._operations[document["operation_id"]]
        await operation.task
        return operation

    async def apply(self, operation, revision=1):
        await self.runtime.apply(operation.operation_id, revision, None)
        await operation.task

    def assert_cache_unchanged(self):
        self.assertEqual(self.cache_before, (self.cache.read_bytes(), self.cache.stat().st_mtime_ns))

    async def test_rotation_preserves_cache_news_cursor_and_naver_usage(self):
        await self.service.search("005930", "삼성전자", None)
        await self.service._query_collector.run_once()
        before = self.store.load_news_source_diagnostics()["sources"]
        articles = self.store.load_documents("news_article", "005930", 20)
        sync = self.store.load_documents("news_sync", "005930", 1)
        remaining = self.service._query_collector._budget_remaining()
        operation = await self.ready()
        candidate = operation.candidate.prepared.client
        self.assertEqual("READY", operation.state)
        self.assertNotIn("corp_code", candidate.calls[0])
        await self.apply(operation)
        self.assertEqual("ACTIVE", operation.state)
        self.assertEqual(2, self.owner.active_revision(self.profile))
        self.assertIs(candidate, self.service._dart_client)
        self.assertIs(self.naver, self.service._client)
        self.assertTrue(self.service._dart_enabled)
        self.assertEqual(before, self.store.load_news_source_diagnostics()["sources"])
        self.assertEqual(articles, self.store.load_documents("news_article", "005930", 20))
        self.assertEqual(sync, self.store.load_documents("news_sync", "005930", 1))
        self.assertEqual(remaining, self.service._query_collector._budget_remaining())
        self.assert_cache_unchanged()
        self.store.replace_documents("news_sync", [])
        await self.service.search("005930", "삼성전자", None)
        self.assertEqual("new", candidate.calls[-1]["crtfc_key"][0])
        self.assertEqual("00126380", candidate.calls[-1]["corp_code"][0])

    async def test_cancelled_old_request_drains_but_naver_common_search_continues(self):
        self.old.block = True
        waiter = asyncio.create_task(self.service.search("005930", "삼성전자", None))
        await self.until(self.old.entered.is_set)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        operation = await self.ready()
        await self.runtime.apply(operation.operation_id, 1, None)
        await self.until(lambda: self.service._dart_paused)
        self.assertFalse(operation.task.done())
        self.assertEqual(1, self.vault.load("dart", self.profile).revision)
        await self.service.search("000660", "다른종목", None)
        self.assertEqual(1, len(self.old.calls))
        self.assertEqual(1, await self.service._query_collector.run_once())
        self.old.release.set()
        await operation.task
        self.assertEqual("ACTIVE", operation.state)
        self.assertTrue(self.store.load_documents("news_sync", "005930", 1))
        self.assertFalse(self.service._running)
        self.assert_cache_unchanged()

    async def test_dart_snapshot_is_fixed_before_slow_naver_and_operational_change(self):
        self.naver.block = True
        waiter = asyncio.create_task(self.service.search("005930", "삼성전자", None))
        await self.until(self.naver.entered.is_set)
        self.service.update_operational_settings(refresh_seconds=300, dart_enabled=False)
        self.naver.release.set()
        await waiter
        self.assertEqual(1, len(self.old.calls))
        self.store.replace_documents("news_sync", [])
        await self.service.search("005930", "삼성전자", None)
        self.assertEqual(1, len(self.old.calls))  # OFF applies to the next accepted collection.

    async def test_two_provider_rotations_do_not_resume_each_others_pause(self):
        self.old.block = True
        waiter = asyncio.create_task(self.service.search("005930", "삼성전자", None))
        await self.until(self.old.entered.is_set)
        dart = await self.ready()
        document = await self.runtime.prepare("naver", NaverCredentialOwner.PROFILE, str(uuid.uuid4()), 1,
            {"client_id": "new-naver", "client_secret": "fake-secret"})
        naver = self.runtime._operations[document["operation_id"]]
        await naver.task
        published, release = asyncio.Event(), asyncio.Event()
        original = self.runtime._hooks["dart"].publish

        async def publish(*args):
            published.set()
            await release.wait()
            await original(*args)

        self.runtime._hooks["dart"] = replace(self.runtime._hooks["dart"], publish=publish)
        try:
            await self.runtime.apply(dart.operation_id, 1, None)
            await self.runtime.apply(naver.operation_id, 1, None)
            await self.until(lambda: self.service._dart_paused and self.service._naver_paused)
            self.old.release.set()
            await published.wait()
            await naver.task
            self.assertEqual("ACTIVE", naver.state)
            self.assertTrue(self.service._dart_paused)
            self.assertFalse(self.service._naver_paused)
            await self.service.search("000660", "다른종목", None)
            self.assertFalse(self.service._running)
            self.assertEqual(1, await self.service._query_collector.run_once())
            self.assertEqual("new-naver", self.service._query_collector._client.marker)
            release.set()
            await dart.task
            await waiter
        finally:
            release.set()
        self.assertEqual("ACTIVE", dart.state)
        self.assertFalse(self.service._dart_paused)

    async def test_validation_failure_distinguishes_invalid_key_and_retryable_quota(self):
        for key, code in [("invalid", "INVALID_CREDENTIAL"), ("quota", "CREDENTIAL_VALIDATION_RETRYABLE")]:
            operation = await self.ready(key=key)
            self.assertEqual("FAILED", operation.state)
            self.assertEqual(code, operation.error_code)
            self.assertIs(self.old, self.service._dart_client)
            self.assertEqual(1, self.vault.load("dart", self.profile).revision)
            self.assert_cache_unchanged()
            self.assertNotIn('"api_key"', json.dumps(await self.runtime.status(operation.operation_id)))

    async def test_stale_revision_does_not_probe_and_cancel_does_not_mutate_cache(self):
        with self.assertRaises(CredentialOperationError):
            await self.ready(revision=0)
        operation = await self.ready()
        await self.runtime.cancel(operation.operation_id)
        self.assertEqual("CANCELLED", operation.state)
        self.assertIsNone(operation.candidate.prepared)
        self.assertIs(self.old, self.service._dart_client)
        self.assert_cache_unchanged()

    async def test_disable_retains_operational_preference_and_blocks_environment_resurrection(self):
        from kiwoom_monitor.central_server.config import CentralServerSettings
        operation = await self.ready(disabled=True)
        await self.apply(operation)
        self.assertEqual("ACTIVE", operation.state)
        self.assertIsNone(self.service._dart_client)
        self.assertTrue(self.service._dart_enabled)
        self.assertEqual([], self.old.calls)
        settings, _ = compose_credential_settings(CentralServerSettings("sqlite:///unused", "token",
            dart_enabled=True, dart_api_key="environment-old"), self.vault, self.store)
        self.assertEqual("", settings.dart_api_key)
        self.assertEqual(2, DartCredentialOwner(self.service, self.vault, self.cache).active_revision(self.profile))
        await self.service.search("005930", "삼성전자", None)
        self.assertTrue(self.naver.calls)
        self.assert_cache_unchanged()

    async def test_deadline_waits_for_old_thread_then_resumes_old_without_commit(self):
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
        self.assertIs(self.old, self.service._dart_client)
        self.assertEqual(1, self.owner.active_revision(self.profile))
        self.assertFalse(self.service._dart_paused)

    async def test_commit_failure_fences_dart_and_keeps_naver_available(self):
        operation = await self.ready()
        with patch.object(self.vault, "save", side_effect=OSError("fake-secret")):
            await self.apply(operation)
        self.assertEqual("RECOVERY_REQUIRED", operation.state)
        self.assertIsNone(self.service._dart_client)
        self.assertIsNone(self.owner.active_revision(self.profile))
        self.assertFalse(self.service._dart_paused)
        await self.service.search("005930", "삼성전자", None)
        self.assertEqual([], self.old.calls)
        self.assertTrue(self.naver.calls)
        self.assert_cache_unchanged()

    async def test_publish_failure_retains_receipt_and_explicit_next_apply_recovers(self):
        operation = await self.ready()
        original = self.service.replace_dart_client

        def publish(client):
            if client is not None:
                raise RuntimeError("fake-secret")
            original(client)

        with patch.object(self.service, "replace_dart_client", side_effect=publish):
            await self.apply(operation)
        self.assertEqual("RECOVERY_REQUIRED", operation.state)
        self.assertTrue(operation.committed)
        self.assertIsNone(self.service._dart_client)
        self.assertEqual(2, self.vault.load("dart", self.profile).revision)
        next_operation = await self.ready(revision=2, key="recovered")
        await self.apply(next_operation, revision=2)
        self.assertEqual("ACTIVE", next_operation.state)
        self.assertEqual(3, self.owner.active_revision(self.profile))
        self.assert_cache_unchanged()

    async def test_cancelled_validation_waits_for_probe_end_without_late_activation(self):
        entered, release = threading.Event(), threading.Event()
        original = FakeDart.validate_credentials

        def probe(client):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("fake gate timeout")
            original(client)

        try:
            with patch.object(FakeDart, "validate_credentials", probe):
                document = await self.runtime.prepare("dart", self.profile, str(uuid.uuid4()), 1,
                    {"api_key": "new"})
                operation = self.runtime._operations[document["operation_id"]]
                await self.until(entered.is_set)
                await self.runtime.cancel(operation.operation_id)
                self.assertFalse(operation.task.done())
                release.set()
                await operation.task
        finally:
            release.set()
        self.assertEqual("CANCELLED", operation.state)
        self.assertIs(self.old, self.service._dart_client)
        self.assertEqual(1, self.vault.load("dart", self.profile).revision)
        self.assert_cache_unchanged()

    async def test_shutdown_waits_for_dart_thread_and_final_news_sync_write(self):
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
        self.assert_cache_unchanged()


class DartValidationTests(unittest.TestCase):
    def test_success_and_no_data_do_not_touch_company_code_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "absent-cache.json"
            client = DartDisclosureClient("fake-secret", path)
            for payload in [{"status": "000", "list": []}, {"status": "013"}]:
                with patch.object(client, "_json", return_value=payload) as query:
                    client.validate_credentials()
                    params = parse_qs(urlsplit(query.call_args.args[0]).query)
                    self.assertEqual(params["bgn_de"], params["end_de"])
                    self.assertEqual(["1"], params["page_count"])
                    self.assertNotIn("corp_code", params)
                    self.assertFalse(path.exists())

    def test_invalid_key_quota_maintenance_and_malformed_payload_are_secret_safe(self):
        client = DartDisclosureClient("fake-secret", Path("not-used.json"))
        for payload, expected in [({"status": code, "message": "fake-secret"}, "INVALID_CREDENTIAL")
                for code in ("010", "011", "901")] + [
                ({"status": "020"}, "CREDENTIAL_VALIDATION_RETRYABLE"),
                ({"status": "800"}, "CREDENTIAL_VALIDATION_RETRYABLE"),
                ({"status": "012"}, "CREDENTIAL_VALIDATION_FAILED"),
                ({}, "CREDENTIAL_VALIDATION_FAILED"),
                ({"status": "000"}, "CREDENTIAL_VALIDATION_FAILED"),
                ([], "CREDENTIAL_VALIDATION_FAILED")]:
            with patch.object(client, "_json", return_value=payload):
                with self.assertRaises(DartCredentialValidationError) as raised:
                    client.validate_credentials()
                self.assertEqual(expected, raised.exception.code)
                self.assertNotIn("fake-secret", str(raised.exception))

    def test_timeout_is_retryable_without_exposing_request_url(self):
        client = DartDisclosureClient("fake-secret", Path("not-used.json"))
        with patch.object(client, "_json", side_effect=TimeoutError("fake-secret")):
            with self.assertRaises(DartCredentialValidationError) as raised:
                client.validate_credentials()
        self.assertEqual("CREDENTIAL_VALIDATION_RETRYABLE", raised.exception.code)
        self.assertNotIn("fake-secret", str(raised.exception))

    def test_http_and_json_failures_are_classified_without_url_or_body(self):
        from urllib.error import HTTPError
        client = DartDisclosureClient("fake-secret", Path("not-used.json"))
        for error, code in [(HTTPError("https://fake/?key=fake-secret", 429, "fake-secret", {}, None),
                "CREDENTIAL_VALIDATION_RETRYABLE"),
                (HTTPError("https://fake/?key=fake-secret", 503, "fake-secret", {}, None),
                "CREDENTIAL_VALIDATION_RETRYABLE"),
                (HTTPError("https://fake/?key=fake-secret", 403, "fake-secret", {}, None),
                "CREDENTIAL_VALIDATION_FAILED"),
                (ValueError("fake-secret"), "CREDENTIAL_VALIDATION_FAILED")]:
            with patch.object(client, "_json", side_effect=error):
                with self.assertRaises(DartCredentialValidationError) as raised:
                    client.validate_credentials()
                self.assertEqual(code, raised.exception.code)
                self.assertNotIn("fake-secret", str(raised.exception))


class DartCredentialAPITests(unittest.TestCase):
    def test_keyless_off_server_applies_default_profile_without_enabling_disclosure_collection(self):
        self._keyless_case(False)

    def test_keyless_on_server_collects_disclosures_after_default_profile_activation(self):
        self._keyless_case(True)

    def _keyless_case(self, enabled):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        with tempfile.TemporaryDirectory() as directory, patch(
            "kiwoom_monitor.central_server.news_credentials.DartDisclosureClient", FakeDart):
            root = Path(directory)
            app = create_app(CentralServerSettings(f"sqlite:///{root / 'central.sqlite'}", "private-token",
                credential_directory=str(root / "secrets"), dart_cache_path=str(root / "corp.json"),
                dart_enabled=enabled, news_query_set_enabled=False, news_history_jobs_enabled=False))
            with TestClient(app, base_url="https://nas.test") as client:
                headers = {"Authorization": "Bearer private-token"}
                runtime = app.state.credential_runtime
                service = runtime._hooks["dart"].prepare.__self__.service
                self.assertIsNone(service._dart_client)
                metadata = client.get("/api/v1/settings/credentials", headers=headers).json()
                profile = next(p for p in metadata["profiles"] if p["profile_id"] == DartCredentialOwner.PROFILE)
                self.assertTrue(profile["supported"])
                self.assertEqual(0, profile["revision"])
                path = f"/api/v1/settings/credentials/dart/profiles/{DartCredentialOwner.PROFILE}/prepare"
                payload = {"request_id": str(uuid.uuid4()), "expected_revision": 0,
                           "replacement": {"api_key": "new"}}
                self.assertEqual(401, client.post(path, json=payload).status_code)
                response = client.post(path, headers=headers, json=payload)
                self.assertEqual(202, response.status_code)
                operation_id = response.json()["operation_id"]

                async def wait():
                    await runtime._operations[operation_id].task

                client.portal.call(wait)
                operation_path = f"/api/v1/settings/credential-operations/{operation_id}"
                ready = client.get(operation_path, headers=headers).json()
                self.assertEqual("READY", ready["state"])
                self.assertIsNone(ready["target_account_ref"])
                self.assertEqual(202, client.post(operation_path + "/apply", headers=headers,
                    json={"expected_revision": 0, "target_account_ref": None}).status_code)
                client.portal.call(wait)
                self.assertEqual("ACTIVE", client.get(operation_path, headers=headers).json()["state"])
                self.assertEqual(enabled, service._dart_enabled)
                self.assertEqual(1, len(service._dart_client.calls))
                self.assertFalse((root / "corp.json").exists())
                (root / "corp.json").write_text('{"005930": "00126380"}', encoding="utf-8")
                result = client.portal.call(service.search, "005930", "삼성전자", None)
                self.assertEqual(enabled, bool(result))
                self.assertEqual(2 if enabled else 1, len(service._dart_client.calls))
