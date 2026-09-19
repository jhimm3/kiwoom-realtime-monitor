from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.ai_credentials import AICredentialOwner
from kiwoom_monitor.central_server.ai_service import CentralAIService
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.credential_runtime import CredentialRuntime
from kiwoom_monitor.central_server.credential_store import CredentialStore, compose_credential_settings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.infrastructure.news_ai import AINewsAnalysis, AIRequestUsage, NewsAIProviderError


class FakeAI:
    def __init__(self):
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_first = False
        self.failure = None

    def __call__(self, settings, stock_name, articles):
        self.calls.append((settings.provider, settings.api_key, settings.model, articles))
        if len(self.calls) == 1 and self.block_first:
            self.entered.set()
            if not self.release.wait(5):
                raise RuntimeError("fake gate timed out")
        if self.failure is not None:
            error, self.failure = self.failure, None
            raise error
        return tuple(AINewsAnalysis("가짜 요약", "긍정", 80, "가짜 근거") for _ in articles), AIRequestUsage(3, 2, 5)


class AICredentialOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = SQLiteQueryStore(root / "central.sqlite")
        self.store.initialize()
        self.vault = CredentialStore(root / "secrets", self.store)
        self.settings = CentralServerSettings("sqlite:///unused", "token", ai_provider="none", ai_model="",
            gemini_api_key="gemini-old", openai_api_key="openai-old", anthropic_api_key="claude-old")
        for provider in ("openai", "gemini", "claude"):
            self.vault.import_initial(provider, f"nas-{provider}-default", {"api_key": provider + "-old"})
        self.service = CentralAIService(self.settings, self.store)
        self.runtime = CredentialRuntime(self.vault, self.store)
        self.owners = {provider: AICredentialOwner(self.service, self.vault, provider)
                      for provider in ("openai", "gemini", "claude")}
        for provider, owner in self.owners.items():
            self.runtime.register(provider, owner.hooks())
        self.fake = FakeAI()
        self.fake_patch = patch("kiwoom_monitor.central_server.ai_service.analyze_articles", self.fake)
        self.fake_patch.start()

    async def asyncTearDown(self):
        self.fake.release.set()
        await self.runtime.close()
        await self.service.close()
        self.fake_patch.stop()
        self.vault.close()
        self.store.close()
        self.temp.cleanup()

    async def until(self, predicate):
        for _ in range(300):
            if predicate():
                return
            await asyncio.sleep(0.005)
        self.fail("fake work did not reach boundary")

    async def analyze(self, identity="article", body="본문", provider="gemini"):
        return await self.service.analyze("005930", "삼성전자", provider, "model-old",
            [{"identity": identity, "title": "공급계약", "body": body}], 1)

    async def ready(self, provider="gemini", *, revision=1, disabled=False):
        document = await self.runtime.prepare(provider, f"nas-{provider}-default", str(uuid.uuid4()), revision,
            {} if disabled else {"api_key": provider + "-new"}, disabled=disabled)
        operation = self.runtime._operations[document["operation_id"]]
        await operation.task
        self.assertEqual("READY", operation.state)
        return operation

    async def apply(self, operation, revision=1):
        await self.runtime.apply(operation.operation_id, revision, None)
        await operation.task

    async def test_all_three_providers_apply_unverified_without_paid_validation(self):
        for provider in self.owners:
            operation = await self.ready(provider)
            self.assertEqual("UNVERIFIED", operation.candidate.validation)
            self.assertNotIn(provider + "-new", repr(operation.candidate))
            await self.apply(operation)
            self.assertEqual("ACTIVE", operation.state)
            self.assertEqual(2, self.owners[provider].active_revision(f"nas-{provider}-default"))
        self.assertEqual([], self.fake.calls)
        self.assertEqual([], self.store.load_documents("news_request_usage", limit=10))
        for provider in self.owners:
            await self.analyze(provider=provider, identity=provider)
        self.assertEqual([p + "-new" for p in self.owners], [call[1] for call in self.fake.calls])

    async def test_key_rotation_reuses_success_cache_and_preserves_usage(self):
        first = await self.analyze()
        cache = self.store.load_documents("news_ai", "005930", 10)
        usage = self.store.load_documents("news_request_usage", limit=10)
        operation = await self.ready()
        await self.apply(operation)
        second = await self.analyze()
        self.assertTrue(second["cache_hit"])
        self.assertEqual(1, len(self.fake.calls))
        self.assertEqual(cache, self.store.load_documents("news_ai", "005930", 10))
        self.assertEqual(usage, self.store.load_documents("news_request_usage", limit=10))
        self.assertEqual(1, first["results"][0]["credential_revision"])
        self.assertEqual(1, second["results"][0]["credential_revision"])
        self.assertEqual(2, second["credential_revision"])
        self.assertEqual("UNVERIFIED", self.service._credential_validation["gemini"])
        await self.analyze(identity="new-article")
        self.assertEqual("gemini-new", self.fake.calls[-1][1])
        self.assertEqual("VERIFIED", self.service._credential_validation["gemini"])

    async def test_received_request_pins_provider_model_key_before_body_preparation(self):
        from kiwoom_monitor.central_server.ai_service import _prepare_events
        entered, release = threading.Event(), threading.Event()

        def prepare(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("fake gate timed out")
            return _prepare_events(*args)

        try:
            with patch("kiwoom_monitor.central_server.ai_service._prepare_events", prepare):
                waiter = asyncio.create_task(self.analyze())
                await self.until(entered.is_set)
                operation = await self.ready()
                await self.runtime.apply(operation.operation_id, 1, None)
                await self.until(lambda: "gemini" in self.service._credential_paused)
                self.assertFalse(operation.task.done())
                self.service.update_operational_settings(provider="claude", model="model-new", daily_limit=30)
                release.set()
                result = await waiter
                await operation.task
        finally:
            release.set()
        self.assertEqual(("gemini", "gemini-old", "model-old"), self.fake.calls[0][:3])
        self.assertEqual(1, result["credential_revision"])
        await self.analyze(identity="next")
        self.assertEqual(("claude", "claude-old", "model-new"), self.fake.calls[-1][:3])

    async def test_execution_queue_finishes_all_old_requests_before_key_publication(self):
        self.fake.block_first = True
        first = asyncio.create_task(self.analyze(identity="one"))
        await self.until(self.fake.entered.is_set)
        second = asyncio.create_task(self.analyze(identity="two"))
        await self.until(lambda: len(self.service._running) == 2)
        operation = await self.ready()
        await self.runtime.apply(operation.operation_id, 1, None)
        await self.until(lambda: "gemini" in self.service._credential_paused)
        self.assertEqual(1, self.vault.load("gemini", "nas-gemini-default").revision)
        self.fake.release.set()
        await asyncio.gather(first, second)
        await operation.task
        self.assertEqual(["gemini-old", "gemini-old"], [call[1] for call in self.fake.calls])
        self.assertEqual("ACTIVE", operation.state)
        await self.analyze(identity="three")
        self.assertEqual("gemini-new", self.fake.calls[-1][1])

    async def test_identical_requests_coalesce_but_different_bodies_do_not(self):
        self.fake.block_first = True
        first = asyncio.create_task(self.analyze())
        await self.until(self.fake.entered.is_set)
        same = asyncio.create_task(self.analyze())
        different = asyncio.create_task(self.analyze(body="다른 본문"))
        await self.until(lambda: len(self.service._running) == 2 and len(self.service._accepted["gemini"]) == 3)
        self.fake.release.set()
        await asyncio.gather(first, same, different)
        self.assertEqual(2, len(self.fake.calls))
        self.assertEqual({"본문", "다른 본문"}, {call[3][0][1] for call in self.fake.calls})

    async def test_cancelled_http_waiter_keeps_actual_work_owned_until_saved(self):
        self.fake.block_first = True
        waiter = asyncio.create_task(self.analyze())
        await self.until(self.fake.entered.is_set)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        operation = await self.ready()
        await self.runtime.apply(operation.operation_id, 1, None)
        await self.until(lambda: "gemini" in self.service._credential_paused)
        with self.assertRaisesRegex(ValueError, "갱신"):
            await self.analyze(identity="paused")
        self.assertFalse(operation.task.done())
        self.fake.release.set()
        await operation.task
        self.assertEqual("ACTIVE", operation.state)
        self.assertTrue(self.store.load_documents("news_ai", "005930", 10))
        self.assertEqual(1, len(self.store.load_documents("news_request_usage", limit=10)))
        self.assertFalse(self.service._accepted["gemini"])
        self.assertFalse(self.service._running)

    async def test_other_provider_inflight_does_not_block_unused_provider_rotation(self):
        self.fake.block_first = True
        waiter = asyncio.create_task(self.analyze(provider="openai"))
        await self.until(self.fake.entered.is_set)
        operation = await self.ready("gemini")
        await self.apply(operation)
        self.assertEqual("ACTIVE", operation.state)
        self.assertFalse(waiter.done())
        self.fake.release.set()
        await waiter
        self.assertEqual("openai-old", self.fake.calls[0][1])

    async def test_drain_deadline_waits_then_resumes_old_without_commit(self):
        self.fake.block_first = True
        waiter = asyncio.create_task(self.analyze())
        await self.until(self.fake.entered.is_set)
        operation = await self.ready()
        self.runtime._deadline = 0.01
        await self.runtime.apply(operation.operation_id, 1, None)
        await self.until(lambda: operation.state == "BUSY")
        self.assertFalse(operation.task.done())
        self.fake.release.set()
        await waiter
        await operation.task
        self.assertEqual("FAILED", operation.state)
        self.assertEqual(1, self.owners["gemini"].active_revision("nas-gemini-default"))
        self.assertEqual("gemini-old", self.service._keys["gemini"])
        self.assertNotIn("gemini", self.service._credential_paused)

    async def test_late_old_auth_failure_cannot_mark_new_key_invalid(self):
        self.fake.block_first = True
        self.fake.failure = NewsAIProviderError(401)
        waiter = asyncio.create_task(self.analyze())
        await self.until(self.fake.entered.is_set)
        operation = await self.ready()
        await self.runtime.apply(operation.operation_id, 1, None)
        await self.until(lambda: "gemini" in self.service._credential_paused)
        self.fake.release.set()
        with self.assertRaises(NewsAIProviderError):
            await waiter
        await operation.task
        self.assertEqual("ACTIVE", operation.state)
        self.assertEqual("UNVERIFIED", self.service._credential_validation["gemini"])
        await self.analyze(identity="new-key-request")
        self.assertEqual("VERIFIED", self.service._credential_validation["gemini"])

    async def test_disable_preserves_cache_and_does_not_resurrect_environment_key(self):
        await self.analyze()
        cache = self.store.load_documents("news_ai", "005930", 10)
        operation = await self.ready(disabled=True)
        await self.apply(operation)
        self.assertEqual("ACTIVE", operation.state)
        self.assertEqual("DISABLED", self.service._credential_validation["gemini"])
        with self.assertRaisesRegex(ValueError, "API 키"):
            await self.analyze(identity="disabled")
        settings, _ = compose_credential_settings(self.settings, self.vault, self.store)
        self.assertEqual("", settings.gemini_api_key)
        self.assertEqual(cache, self.store.load_documents("news_ai", "005930", 10))
        self.assertEqual(1, len(self.fake.calls))

    async def test_daily_limit_is_not_reset_by_key_rotation(self):
        self.service.update_operational_settings(provider="none", model="", daily_limit=1)
        await self.analyze()
        operation = await self.ready()
        await self.apply(operation)
        with self.assertRaisesRegex(ValueError, "일일 상한"):
            await self.analyze(identity="second")
        self.assertEqual(1, len(self.fake.calls))
        self.assertEqual(1, len(self.store.load_documents("news_request_usage", limit=10)))

    async def test_commit_failure_blocks_old_key_but_other_provider_remains_usable(self):
        operation = await self.ready()
        with patch.object(self.vault, "save", side_effect=OSError("fake-secret")):
            await self.apply(operation)
        self.assertEqual("RECOVERY_REQUIRED", operation.state)
        self.assertIsNone(self.service._credential_revisions["gemini"])
        self.assertEqual("", self.service._keys["gemini"])
        with self.assertRaises(ValueError):
            await self.analyze()
        await self.analyze(provider="claude")
        self.assertEqual("claude-old", self.fake.calls[-1][1])

    async def test_publication_failure_retains_receipt_and_explicit_apply_recovers(self):
        operation = await self.ready()
        original = self.service.replace_credentials

        def replace(provider, key, revision, validation):
            if key:
                raise RuntimeError("fake-secret")
            original(provider, key, revision, validation)

        with patch.object(self.service, "replace_credentials", side_effect=replace):
            await self.apply(operation)
        self.assertEqual("RECOVERY_REQUIRED", operation.state)
        self.assertTrue(operation.committed)
        self.assertEqual(2, self.vault.load("gemini", "nas-gemini-default").revision)
        next_operation = await self.ready(revision=2)
        await self.apply(next_operation, revision=2)
        self.assertEqual("ACTIVE", next_operation.state)
        self.assertEqual(3, self.service._credential_revisions["gemini"])
        self.assertEqual([], self.fake.calls)

    async def test_shutdown_waits_for_cancelled_request_thread_and_usage_save(self):
        self.fake.block_first = True
        waiter = asyncio.create_task(self.analyze())
        await self.until(self.fake.entered.is_set)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        closing = asyncio.create_task(self.service.close())
        await self.until(lambda: self.service._closing)
        self.assertFalse(closing.done())
        self.fake.release.set()
        await closing
        self.assertEqual(1, len(self.store.load_documents("news_request_usage", limit=10)))
        self.assertFalse(self.service._running)

    async def test_runtime_validation_does_not_change_immutable_vault_validation(self):
        operation = await self.ready()
        await self.apply(operation)
        await self.analyze()
        profiles = await self.runtime.profiles()
        profile = next(p for p in profiles["profiles"] if p["profile_id"] == "nas-gemini-default")
        self.assertEqual("UNVERIFIED", profile["validation"])
        self.assertEqual("VERIFIED", profile["runtime_validation"])
        self.assertEqual(2, profile["revision"])
        self.assertNotIn("gemini-new", json.dumps(profiles))

    async def test_authentication_access_and_temporary_errors_are_distinct(self):
        for status, expected in ((401, "INVALID_CREDENTIAL"), (403, "ACCESS_DENIED"),
                                 (429, "RETRYABLE"), (503, "RETRYABLE"), (400, "REQUEST_FAILED")):
            with self.subTest(status=status):
                self.fake.failure = NewsAIProviderError(status)
                with self.assertRaises(NewsAIProviderError):
                    await self.analyze(identity=f"error-{status}")
                self.assertEqual(expected, self.service._credential_validation["gemini"])
                self.assertEqual(1, self.service._credential_revisions["gemini"])
        await self.analyze(identity="recovered")
        self.assertEqual("VERIFIED", self.service._credential_validation["gemini"])


class AICredentialAPITests(unittest.TestCase):
    def test_keyless_server_applies_three_fixed_profiles_without_paid_calls(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        with tempfile.TemporaryDirectory() as directory, patch(
            "kiwoom_monitor.central_server.ai_service.analyze_articles", FakeAI()) as fake:
            root = Path(directory)
            app = create_app(CentralServerSettings(f"sqlite:///{root / 'central.sqlite'}", "private-token",
                credential_directory=str(root / "secrets"), news_query_set_enabled=False))
            with TestClient(app, base_url="https://nas.test") as client:
                headers = {"Authorization": "Bearer private-token"}
                runtime = app.state.credential_runtime
                service = runtime._hooks["gemini"].prepare.__self__.service
                news = runtime._hooks["naver"].prepare.__self__.service
                self.assertIs(service, news._ai_service)
                self.assertIs(service, news._job_runner._ai_service)
                for provider in ("openai", "gemini", "claude"):
                    path = f"/api/v1/settings/credentials/{provider}/profiles/nas-{provider}-default/prepare"
                    payload = {"request_id": str(uuid.uuid4()), "expected_revision": 0,
                               "replacement": {"api_key": provider + "-fake-key"}}
                    self.assertEqual(401, client.post(path, json=payload).status_code)
                    response = client.post(path, headers=headers, json=payload)
                    self.assertEqual(202, response.status_code)
                    operation_id = response.json()["operation_id"]

                    async def wait():
                        await runtime._operations[operation_id].task

                    client.portal.call(wait)
                    op_path = f"/api/v1/settings/credential-operations/{operation_id}"
                    status = client.get(op_path, headers=headers).json()
                    self.assertEqual("UNVERIFIED", status["validation"])
                    self.assertIsNone(status["target_account_ref"])
                    self.assertEqual(202, client.post(op_path + "/apply", headers=headers,
                        json={"expected_revision": 0, "target_account_ref": None}).status_code)
                    client.portal.call(wait)
                    self.assertEqual("ACTIVE", client.get(op_path, headers=headers).json()["state"])
                self.assertEqual([], fake.calls)
                self.assertEqual([], service._store.load_documents("news_request_usage", limit=10))
