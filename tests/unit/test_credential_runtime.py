from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.credential_runtime import (
    CredentialRuntime, CredentialRuntimeHooks, CredentialOperationError, ValidatedCredential,
)
from kiwoom_monitor.central_server.credential_store import CredentialStore
from kiwoom_monitor.central_server.database import SQLiteQueryStore


class CredentialRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = SQLiteQueryStore(self.root / "central.sqlite")
        self.store.initialize()
        self.vault = CredentialStore(self.root / "secrets", self.store)
        self.now = 0
        self.runtime = CredentialRuntime(self.vault, self.store, monotonic=lambda: self.now)
        self.active = {}
        self.calls = []
        self.key = uuid.uuid4().hex
        async def prepare(*_): return ValidatedCredential("UNVERIFIED")
        async def drain(profile): self.calls.append("drain")
        async def publish(profile, record, candidate): self.calls.append("publish"); self.active[profile] = record.revision
        async def resume(profile): self.calls.append("resume")
        self.hooks = CredentialRuntimeHooks(prepare, drain, publish, resume, lambda profile: self.active.get(profile))
        self.runtime.register("openai", self.hooks)
        self.profile = (await self.runtime.create_profile("openai", str(uuid.uuid4()), "연구용"))["profile_id"]

    async def asyncTearDown(self):
        await self.runtime.close()
        self.vault.close()
        self.store.close()
        self.temporary.cleanup()

    async def ready(self, request=None):
        document = await self.runtime.prepare("openai", self.profile, request or str(uuid.uuid4()), 0, {"api_key": self.key})
        op = self.runtime._operations[document["operation_id"]]
        await op.task
        self.assertEqual("READY", op.state)
        return op

    async def test_profile_creation_idempotency_survives_new_runtime(self):
        request_id = str(uuid.uuid4())
        first = await self.runtime.create_profile("openai", request_id, "분리 계좌")
        second = await CredentialRuntime(self.vault, self.store).create_profile("openai", request_id, "분리 계좌")
        self.assertEqual(first, second)
        with self.assertRaises(CredentialOperationError):
            await self.runtime.create_profile("openai", request_id, "다른 내용")
        profile = next(p for p in self.store.list_credential_profiles() if p["profile_id"] == first["profile_id"])
        self.assertEqual("draft", profile["lifecycle_state"])

    async def test_prepare_apply_duplicate_digest_revision_and_secret_free_status(self):
        request_id = str(uuid.uuid4())
        op = await self.ready(request_id)
        duplicate = await self.runtime.prepare("openai", self.profile, request_id, 0, {"api_key": self.key})
        self.assertEqual(op.operation_id, duplicate["operation_id"])
        with self.assertRaises(CredentialOperationError):
            await self.runtime.prepare("openai", self.profile, request_id, 0, {"api_key": uuid.uuid4().hex})
        self.assertIsNone(self.vault.load("openai", self.profile))
        await self.runtime.apply(op.operation_id, 0, None)
        await self.runtime.apply(op.operation_id, 0, None)
        await op.task
        self.assertEqual("ACTIVE", op.state)
        self.assertEqual(["drain", "publish", "resume"], self.calls)
        self.assertEqual(1, len(self.store.load_credential_activations(self.profile)))
        self.assertEqual({}, op.credentials)
        self.assertIsNone(op.candidate.prepared)
        self.assertNotIn(self.key, json.dumps(await self.runtime.status(op.operation_id)))
        self.assertNotIn(self.key, json.dumps(await self.runtime.profiles()))
        with self.assertRaises(CredentialOperationError): await self.runtime.apply(op.operation_id, 1, None)
        restored = CredentialRuntime(self.vault, self.store)
        repeated = await restored.prepare("openai", self.profile, request_id, 0, {"api_key": self.key})
        self.assertTrue(repeated["committed"])
        self.assertEqual("RECOVERY_REQUIRED", repeated["state"])
        await restored.apply(op.operation_id, 0, None)
        with self.assertRaises(CredentialOperationError): await restored.apply(op.operation_id, 2, None)
        self.assertEqual(1, self.vault.load("openai", self.profile).revision)

    async def test_expiry_cancel_and_same_profile_busy(self):
        op = await self.ready()
        with self.assertRaises(CredentialOperationError):
            await self.runtime.prepare("openai", self.profile, str(uuid.uuid4()), 0, {"api_key": self.key})
        self.now = 301
        self.assertEqual("EXPIRED", (await self.runtime.status(op.operation_id))["state"])
        self.assertEqual({}, op.credentials)
        with self.assertRaises(CredentialOperationError): await self.runtime.apply(op.operation_id, 0, None)
        op2 = await self.ready()
        self.assertEqual("CANCELLED", (await self.runtime.cancel(op2.operation_id))["state"])
        self.assertEqual({}, op2.credentials)
        self.assertIsNone(self.vault.load("openai", self.profile))

    async def test_cancelled_validator_waits_for_actual_work_without_late_ready(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def prepare(*_): entered.set(); await release.wait(); return ValidatedCredential("UNVERIFIED")
        self.runtime._hooks["openai"] = CredentialRuntimeHooks(prepare, self.hooks.drain, self.hooks.publish, self.hooks.resume)
        doc = await self.runtime.prepare("openai", self.profile, str(uuid.uuid4()), 0, {"api_key": self.key})
        op = self.runtime._operations[doc["operation_id"]]
        await entered.wait()
        await self.runtime.cancel(op.operation_id)
        self.assertFalse(op.task.done())
        self.assertEqual({}, op.credentials)
        with self.assertRaises(CredentialOperationError):
            await self.runtime.prepare("openai", self.profile, str(uuid.uuid4()), 0, {"api_key": self.key})
        release.set(); await op.task
        self.assertEqual("CANCELLED", op.state)

    async def test_apply_deadline_busy_waits_then_resumes_old_without_commit(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def drain(_): entered.set(); await release.wait()
        self.runtime._hooks["openai"] = CredentialRuntimeHooks(self.hooks.prepare, drain, self.hooks.publish, self.hooks.resume)
        self.runtime._deadline = 0.01
        op = await self.ready()
        await self.runtime.apply(op.operation_id, 0, None)
        await entered.wait(); await asyncio.sleep(0.02)
        self.assertEqual("BUSY", op.state)
        self.assertIsNone(self.vault.load("openai", self.profile))
        with self.assertRaises(CredentialOperationError): await self.runtime.cancel(op.operation_id)
        release.set(); await op.task
        self.assertEqual("FAILED", op.state)
        self.assertEqual(["resume"], self.calls)
        self.assertIsNone(self.vault.load("openai", self.profile))

    async def test_postcommit_db_failure_restarts_finalize_without_double_binding(self):
        op = await self.ready()
        with patch.object(self.store, "finalize_credential_activation", side_effect=RuntimeError(self.key)):
            await self.runtime.apply(op.operation_id, 0, None); await op.task
        self.assertTrue(op.committed)
        self.assertEqual("RECOVERY_REQUIRED", op.state)
        self.assertEqual(["drain"], self.calls)
        self.assertNotIn(self.key, json.dumps(op.document()))
        recovered = CredentialRuntime(self.vault, self.store)
        await recovered.recover_committed(); await recovered.recover_committed()
        self.assertEqual(1, len(self.store.load_credential_activations(self.profile)))
        self.assertEqual("RECOVERY_REQUIRED", (await recovered.status(op.operation_id))["state"])

    async def test_file_replace_failure_never_reports_old_rollback_success(self):
        op = await self.ready()
        with patch("kiwoom_monitor.central_server.credential_store.os.replace", side_effect=OSError(self.key)):
            await self.runtime.apply(op.operation_id, 0, None); await op.task
        self.assertFalse(op.committed)
        self.assertEqual("RECOVERY_REQUIRED", op.state)
        self.assertEqual(["drain"], self.calls)
        self.assertNotIn(self.key, json.dumps(op.document()))

    async def test_unapplied_candidates_are_unknown_after_restart(self):
        op = await self.ready()
        with self.assertRaises(CredentialOperationError) as caught:
            await CredentialRuntime(self.vault, self.store).status(op.operation_id)
        self.assertEqual(410, caught.exception.status)
        self.assertIsNone(self.vault.load("openai", self.profile))

    async def test_restart_retains_committed_status_when_database_still_fails(self):
        op = await self.ready()
        with patch.object(self.store, "finalize_credential_activation", side_effect=RuntimeError(self.key)):
            await self.runtime.apply(op.operation_id, 0, None); await op.task
            restored = CredentialRuntime(self.vault, self.store)
            await restored.recover_committed()
            document = await restored.status(op.operation_id)
            self.assertEqual("RECOVERY_REQUIRED", document["state"])
            self.assertTrue(document["committed"])
            repeated = await restored.prepare("openai", self.profile, op.request_id, 0, {"api_key": self.key})
            self.assertEqual(op.operation_id, repeated["operation_id"])
            self.assertNotIn(self.key, json.dumps(document))
        await restored.recover_committed()
        self.assertEqual(1, len(self.store.load_credential_activations(self.profile)))

    async def test_unknown_account_rejected_and_verified_account_requires_confirmation(self):
        account = str(uuid.uuid4())
        async def prepare(*_): return ValidatedCredential("VERIFIED", account, str(uuid.uuid4()))
        self.runtime.register("kiwoom_mock", CredentialRuntimeHooks(prepare, self.hooks.drain,
            self.hooks.publish, self.hooks.resume, lambda profile: self.active.get(profile)))
        profile = (await self.runtime.create_profile("kiwoom_mock", str(uuid.uuid4()), "모의"))["profile_id"]
        keys = {"app_key": uuid.uuid4().hex, "secret_key": self.key}
        first = await self.runtime.prepare("kiwoom_mock", profile, str(uuid.uuid4()), 0, keys)
        op = self.runtime._operations[first["operation_id"]]; await op.task
        self.assertEqual("FAILED", op.state)
        account = self.store.register_account_identity({"account_ref": account, "broker": "kiwoom",
            "environment": "mock", "identity_fingerprint": uuid.uuid4().hex + uuid.uuid4().hex,
            "created_at": "2026-09-15T12:00:00+09:00", "status": "active"})
        second = await self.runtime.prepare("kiwoom_mock", profile, str(uuid.uuid4()), 0, keys)
        op = self.runtime._operations[second["operation_id"]]; await op.task
        self.assertEqual("READY", op.state)
        self.assertEqual([], self.store.load_account_bindings())
        with self.assertRaises(CredentialOperationError): await self.runtime.apply(op.operation_id, 0, None)
        await self.runtime.apply(op.operation_id, 0, account); await op.task
        self.assertEqual("ACTIVE", op.state)
        self.assertEqual(account, self.store.load_account_bindings()[0]["account_ref"])
        account = self.store.register_account_identity({"account_ref": str(uuid.uuid4()), "broker": "kiwoom",
            "environment": "mock", "identity_fingerprint": uuid.uuid4().hex + uuid.uuid4().hex,
            "created_at": "2026-09-15T12:00:00+09:00", "status": "active"})
        third = await self.runtime.prepare("kiwoom_mock", profile, str(uuid.uuid4()), 1, keys)
        op = self.runtime._operations[third["operation_id"]]; await op.task
        self.assertEqual("ACCOUNT_CHANGED", op.error_code)
        self.assertEqual(1, self.vault.load("kiwoom_mock", profile).revision)

    async def test_publish_failure_does_not_resume_or_claim_active(self):
        async def publish(*_): raise RuntimeError(self.key)
        self.runtime._hooks["openai"] = CredentialRuntimeHooks(self.hooks.prepare, self.hooks.drain,
            publish, self.hooks.resume, lambda profile: self.active.get(profile))
        op = await self.ready()
        await self.runtime.apply(op.operation_id, 0, None); await op.task
        self.assertEqual("RECOVERY_REQUIRED", op.state)
        self.assertTrue(op.committed)
        self.assertEqual(["drain"], self.calls)
        self.assertNotIn(self.key, json.dumps(op.document()))

    async def test_shutdown_waits_for_validator_and_prevents_late_ready(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def prepare(*_): entered.set(); await release.wait(); return ValidatedCredential("UNVERIFIED")
        self.runtime._hooks["openai"] = CredentialRuntimeHooks(prepare, self.hooks.drain,
            self.hooks.publish, self.hooks.resume)
        document = await self.runtime.prepare("openai", self.profile, str(uuid.uuid4()), 0, {"api_key": self.key})
        await entered.wait()
        closing = asyncio.create_task(self.runtime.close()); await asyncio.sleep(0)
        self.assertFalse(closing.done())
        release.set(); await closing
        self.assertEqual("EXPIRED", (await self.runtime.status(document["operation_id"]))["state"])
        self.assertIsNone(self.vault.load("openai", self.profile))


class CredentialAPITests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.token, self.key = uuid.uuid4().hex, uuid.uuid4().hex
        self.app = create_app(CentralServerSettings(f"sqlite:///{root / 'central.sqlite'}", self.token,
            credential_directory=str(root / "secrets"), autonomous_top20_enabled=False, market_event_collection_enabled=False))
        self.runtime = self.app.state.credential_runtime
        self.active = {}
        async def prepare(*_): return ValidatedCredential("UNVERIFIED")
        async def drain(_): pass
        async def publish(profile, record, _): self.active[profile] = record.revision
        async def resume(_): pass
        self.runtime._hooks["openai"] = CredentialRuntimeHooks(prepare, drain, publish, resume, lambda profile: self.active.get(profile))
        self.client = TestClient(self.app, base_url="https://nas.test")
        self.client.__enter__()
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.profile = self.client.post("/api/v1/settings/credentials/openai/profiles", headers=self.headers,
            json={"request_id": str(uuid.uuid4()), "label": "연구용"}).json()["profile_id"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def path(self): return f"/api/v1/settings/credentials/openai/profiles/{self.profile}/prepare"
    def payload(self): return {"request_id": str(uuid.uuid4()), "expected_revision": 0, "replacement": {"api_key": self.key}}
    def poll(self, operation_id, terminal):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            doc = self.client.get(f"/api/v1/settings/credential-operations/{operation_id}", headers=self.headers).json()
            if doc.get("state") in terminal: return doc
            time.sleep(0.005)
        self.fail("operation did not finish")

    def test_https_api_pipeline_capability_and_provider_support(self):
        capabilities = self.client.get("/api/v1/capabilities", headers=self.headers).json()["capabilities"]
        self.assertTrue(capabilities["runtime_credentials_v1"])
        profiles = self.client.get("/api/v1/settings/credentials", headers=self.headers).json()
        self.assertFalse(next(p for p in profiles["providers"] if p["provider"] == "kiwoom_mock")["supported"])
        response = self.client.post(self.path(), headers=self.headers, json=self.payload())
        self.assertEqual(202, response.status_code)
        operation = response.json()["operation_id"]
        self.poll(operation, {"READY"})
        self.assertEqual(202, self.client.post(f"/api/v1/settings/credential-operations/{operation}/apply",
            headers=self.headers, json={"expected_revision": 0}).status_code)
        result = self.poll(operation, {"ACTIVE", "RECOVERY_REQUIRED"})
        self.assertEqual("ACTIVE", result["state"])
        self.assertNotIn(self.key, json.dumps(result))

    def test_http_and_spoofed_forwarded_header_cannot_send_keys(self):
        payload = self.payload()
        for headers in (self.headers, {**self.headers, "X-Forwarded-Proto": "https"}):
            response = self.client.post("http://nas.test" + self.path(), headers=headers, json=payload)
            self.assertEqual(426, response.status_code)
            self.assertNotIn(self.key, response.text)
        response = self.client.post(self.path(), json=payload)
        self.assertEqual(401, response.status_code)

    def test_invalid_bodies_never_echo_supplied_values(self):
        for payload in (
            {**self.payload(), "unknown": self.key},
            {**self.payload(), "expected_revision": self.key},
            {**self.payload(), "request_id": "invalid:" + self.key},
            {**self.payload(), "replacement": {"access_token": self.key}},
            {**self.payload(), "replacement": {"api_key": ""}},
            {**self.payload(), "disable": True},
        ):
            response = self.client.post(self.path(), headers=self.headers, json=payload)
            self.assertEqual(422, response.status_code)
            self.assertNotIn(self.key, response.text)
        response = self.client.post(self.path(), headers=self.headers, content=self.key)
        self.assertEqual(422, response.status_code)
        self.assertNotIn(self.key, response.text)
        response = self.client.post(self.path(), headers=self.headers, content='"' + self.key * 600 + '"')
        self.assertEqual(413, response.status_code)
        self.assertNotIn(self.key, response.text)

    def test_query_removed_from_server_scope_and_safe_error(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.credential_runtime import install_credential_routes
        app, observed = FastAPI(), []
        async def authorize(): pass
        install_credential_routes(app, self.runtime, authorize)
        async def capture(scope, receive, send):
            await app(scope, receive, send)
            if scope["type"] == "http": observed.append(scope["query_string"])
        with TestClient(capture, base_url="https://nas.test") as client:
            response = client.post(self.path() + "?secret=" + self.key, json=self.payload())
            self.assertEqual(400, response.status_code)
            self.assertNotIn(self.key, response.text)
        self.assertEqual([b""], observed)

    def test_only_explicit_proxy_and_single_https_header_allow_writes(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.credential_runtime import install_credential_routes
        app = FastAPI()
        async def authorize(): pass
        install_credential_routes(app, self.runtime, authorize, ("127.0.0.1",))
        async def proxy(scope, receive, send):
            if scope["type"] == "http": scope["client"] = ("127.0.0.1", 5000)
            await app(scope, receive, send)
        with TestClient(proxy, base_url="http://nas.test") as client:
            path = "/api/v1/settings/credentials/openai/profiles"
            payload = {"request_id": str(uuid.uuid4()), "label": "프록시"}
            self.assertEqual(200, client.post(path, json=payload, headers={"X-Forwarded-Proto": "https"}).status_code)
            self.assertEqual(426, client.post(path, json=payload, headers=[("X-Forwarded-Proto", "https"),
                ("X-Forwarded-Proto", "http")]).status_code)
            self.assertEqual(426, client.post(path, json=payload,
                headers={"X-Forwarded-Proto": "https,http"}).status_code)

    def test_unwired_provider_cannot_commit_or_claim_runtime_ready(self):
        profile = self.client.post("/api/v1/settings/credentials/kiwoom_real/profiles", headers=self.headers,
            json={"request_id": str(uuid.uuid4()), "label": "뉴스"}).json()["profile_id"]
        response = self.client.post(f"/api/v1/settings/credentials/kiwoom_real/profiles/{profile}/prepare", headers=self.headers,
            json={"request_id": str(uuid.uuid4()), "expected_revision": 0,
                  "replacement": {"app_key": self.key, "secret_key": self.key}})
        self.assertEqual(503, response.status_code)
        self.assertEqual("PROVIDER_RUNTIME_NOT_READY", response.json()["detail"]["code"])
        self.assertNotIn(self.key, response.text)
