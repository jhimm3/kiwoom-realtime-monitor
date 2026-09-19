from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from kiwoom_monitor.central_server.credential_runtime import CredentialRuntime
from kiwoom_monitor.central_server.credential_store import CredentialStore
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.real_runtime import RealCredentialOwner
from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomSettings
from test_mock_credential_owner import FakeClient


class RealFakeClient(FakeClient):
    query_entered = query_release = None

    def request_with_continuation(self, api_id, path, body, **kwargs):
        with self._request_lock:
            self._ensure_credential_accepting()
            if self.query_entered is not None:
                self.query_entered.set()
                self.query_release.wait(timeout=5)
            return {"credential_marker": self._settings.app_key}, bool(body.get("paged")), "cursor"


class RealCredentialOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SQLiteQueryStore(self.root / "central.sqlite")
        self.store.initialize()
        self.vault = CredentialStore(self.root / "secrets", self.store)
        self.client_patch = patch("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", RealFakeClient)
        self.client_patch.start()
        self.realtime_patch = patch("kiwoom_monitor.central_server.mock_account_monitor.RealAccountRealtimeCollector",
                                    side_effect=lambda **kwargs: AsyncMock(ready=False, error_code=None))
        self.realtime_patch.start()
        self.market_client = RealFakeClient(KiwoomSettings("", "", "real"))
        self.market_broker = CentralRestBroker(self.market_client)
        self.collector = AsyncMock()
        self.owner = RealCredentialOwner(self.store, self.vault, hmac_key=b"x" * 32,
            market_client=self.market_client, market_broker=self.market_broker, market_collector=self.collector)
        self.runtime = CredentialRuntime(self.vault, self.store)
        self.runtime.register("kiwoom_real", self.owner.hooks())
        self.profile = (await self.runtime.create_profile("kiwoom_real", str(uuid.uuid4()), "one"))["profile_id"]

    async def asyncTearDown(self):
        if RealFakeClient.query_release is not None:
            RealFakeClient.query_release.set()
        await self.runtime.close()
        await self.owner.close()
        await self.market_broker.close()
        self.vault.close()
        self.store.close()
        self.client_patch.stop()
        self.realtime_patch.stop()
        self.temp.cleanup()
        RealFakeClient.query_entered = RealFakeClient.query_release = None

    async def ready(self, key="a1", *, profile=None, revision=0, disabled=False):
        document = await self.runtime.prepare("kiwoom_real", profile or self.profile,
            str(uuid.uuid4()), revision, {} if disabled else {"app_key": key, "secret_key": uuid.uuid4().hex},
            disabled=disabled)
        operation = self.runtime._operations[document["operation_id"]]
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        return operation

    async def apply(self, operation):
        self.assertEqual("READY", operation.state, operation.error_code)
        await self.runtime.apply(operation.operation_id, operation.expected_revision, operation.candidate.account_ref)
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        return operation

    async def active(self, **kwargs):
        operation = await self.apply(await self.ready(**kwargs))
        self.assertEqual("ACTIVE", operation.state, operation.error_code)
        return self.owner.bundle(kwargs.get("profile", self.profile))

    async def query(self, context, **kwargs):
        return await context.account_queries.query(api_id="kt00007", path="/api/dostk/acnt",
            body={"paged": True}, **kwargs)

    async def test_market_rotation_reuses_transport_and_old_cursors_are_rejected(self):
        context = await self.active(profile="nas-real-default")
        old = await self.query(context)
        run_id, binding = context.run_id, context.binding
        manager = context.account_queries
        rotated = await self.active(key="a2", profile="nas-real-default", revision=1)
        self.assertIs(context, rotated)
        self.assertIs(self.market_client, rotated.client)
        self.assertIs(self.market_broker, rotated.broker)
        self.assertIs(manager, rotated.account_queries)
        self.assertEqual(run_id, rotated.run_id)
        self.assertEqual(binding.scope, rotated.binding.scope)
        self.assertEqual(binding.binding_revision + 1, rotated.binding.binding_revision)
        self.assertEqual("token:a2", rotated.client._token)
        with self.assertRaises(ValueError):
            await self.query(context, batch_id=old["batch_id"], page_index=1, next_key=old["next_key"])
        self.assertEqual(2, self.collector.begin_credential_change.await_count)
        self.assertEqual(2, self.collector.end_credential_change.await_count)

    async def test_additional_real_accounts_have_separate_queries_and_never_rotate_market(self):
        market = await self.active(profile="nas-real-default")
        other = await self.active(key="b1")
        result = await self.query(other)
        self.assertEqual("b1", result["payload"]["credential_marker"])
        self.assertIsNot(other.client._request_lock, market.client._request_lock)
        self.assertEqual("real", other.client.environment)
        self.assertEqual(2, len(self.owner.account_bindings()))
        self.assertEqual(1, self.collector.begin_credential_change.await_count)
        self.assertEqual(1, self.collector.end_credential_change.await_count)
        self.assertEqual("a1", (await self.query(market))["payload"]["credential_marker"])
        self.assertFalse(self.store.load_account_settings(other.binding.scope.to_dict())["mock_order_enabled"])

    async def test_changed_account_and_duplicate_profile_do_not_replace_old_key(self):
        context = await self.active()
        changed = await self.ready(key="b1", revision=1)
        self.assertEqual("ACCOUNT_CHANGED", changed.error_code)
        other = (await self.runtime.create_profile("kiwoom_real", str(uuid.uuid4()), "duplicate"))["profile_id"]
        duplicate = await self.ready(key="a2", profile=other)
        self.assertEqual("ACCOUNT_PROFILE_CONFLICT", duplicate.error_code)
        self.assertIs(context, self.owner.bundle(self.profile))
        self.assertEqual("token:a1", context.client._token)
        self.assertEqual(1, self.vault.load("kiwoom_real", self.profile).revision)
        self.assertIsNone(self.vault.load("kiwoom_real", other))

    async def test_bad_candidate_does_not_pause_market_or_expose_sensitive_error(self):
        context = await self.active(profile="nas-real-default")
        bad = await self.ready(key="bad", profile="nas-real-default", revision=1)
        self.assertEqual("CREDENTIAL_VALIDATION_FAILED", bad.error_code)
        self.assertNotIn("fake-sensitive-error", str(bad.document()))
        self.assertIs(context, self.owner.bundle("nas-real-default"))
        self.assertEqual(1, self.collector.begin_credential_change.await_count)

    async def test_precommit_settings_conflict_restores_old_query_cursor(self):
        context = await self.active()
        first = await self.query(context)
        operation = await self.ready(key="a2", revision=1)
        self.store.save_account_settings({"scope": context.binding.scope.to_dict(),
            "active_profile_id": self.profile, "monitor_enabled": False, "mock_order_enabled": False}, expected_revision=1)
        await self.apply(operation)
        self.assertEqual("FAILED", operation.state)
        self.assertFalse(operation.committed)
        self.assertEqual("token:a1", context.client._token)
        result = await self.query(context, batch_id=first["batch_id"], page_index=1, next_key=first["next_key"])
        self.assertEqual("a1", result["payload"]["credential_marker"])

    async def test_postcommit_database_failure_fences_old_transport_until_explicit_recovery(self):
        context = await self.active(profile="nas-real-default")
        operation = await self.ready(key="a2", profile="nas-real-default", revision=1)
        with patch.object(self.store, "finalize_credential_activation", side_effect=OSError("fake-db-failure")):
            await self.apply(operation)
        self.assertEqual("RECOVERY_REQUIRED", operation.state)
        self.assertTrue(operation.committed)
        self.assertIsNone(self.owner.bundle("nas-real-default"))
        self.assertTrue(context.broker._credential_paused)
        with self.assertRaises(RuntimeError):
            await context.broker.request("kt00007", "/api/dostk/acnt", {})
        # Candidate verification on the same drained client must never reopen old reads.
        recover = await self.ready(key="a3", profile="nas-real-default", revision=2)
        self.assertEqual("READY", recover.state, recover.error_code)
        self.assertTrue(context.broker._credential_paused)
        self.assertEqual("token:a1", context.client._token)
        await self.apply(recover)
        self.assertEqual("ACTIVE", recover.state)
        self.assertEqual("token:a3", context.client._token)
        self.assertEqual(3, self.owner.active_revision("nas-real-default"))

    async def test_disable_additional_account_preserves_history_and_market_cannot_be_disabled(self):
        market = await self.active(profile="nas-real-default")
        other = await self.active(key="b1")
        bindings = self.store.load_account_bindings()
        disabled = await self.apply(await self.ready(revision=1, disabled=True))
        self.assertEqual("ACTIVE", disabled.state)
        self.assertTrue(self.vault.load("kiwoom_real", self.profile).disabled)
        self.assertEqual(bindings, self.store.load_account_bindings())
        self.assertIsNone(self.owner.bundle(self.profile))
        self.assertIsNone(other.client._token)
        self.assertIs(market, self.owner.bundle("nas-real-default"))
        rejected = await self.ready(profile="nas-real-default", revision=1, disabled=True)
        self.assertEqual("MARKET_PROFILE_REQUIRED", rejected.error_code)
        self.assertEqual(1, self.vault.load("kiwoom_real", "nas-real-default").revision)

    async def test_cancelled_apply_waiter_does_not_commit_before_physical_read_finishes(self):
        context = await self.active()
        operation = await self.ready(key="a2", revision=1)
        RealFakeClient.query_entered, RealFakeClient.query_release = threading.Event(), threading.Event()
        query = asyncio.create_task(context.account_queries.query(api_id="kt00015", path="/api/dostk/acnt",
            body={"physical": True}))
        await asyncio.to_thread(RealFakeClient.query_entered.wait, 3)
        query.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await query
        await self.runtime.apply(operation.operation_id, 1, operation.candidate.account_ref)
        await asyncio.sleep(0.03)
        self.assertEqual(1, self.vault.load("kiwoom_real", self.profile).revision)
        self.assertFalse(operation.task.done())
        RealFakeClient.query_release.set()
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        self.assertEqual("ACTIVE", operation.state)

    async def test_keyless_start_has_no_token_request_and_later_activation_reuses_market_broker(self):
        await self.owner.start()
        self.assertTrue(self.market_broker._credential_paused)
        self.assertIsNone(self.market_client._token)
        self.assertEqual((), self.owner.account_bindings())
        context = await self.active(profile="nas-real-default")
        self.assertIs(self.market_broker, context.broker)
        self.assertFalse(context.broker._credential_paused)

    async def test_failed_client_activation_can_be_replaced_without_opening_old_reads(self):
        context = await self.active()
        operation = await self.ready(key="a2", revision=1)
        with patch.object(context.client, "activate_prepared_credentials", side_effect=RuntimeError("fake-activate")):
            await self.apply(operation)
        self.assertEqual("RECOVERY_REQUIRED", operation.state)
        self.assertTrue(context.broker._credential_paused)
        recovery = await self.ready(key="a3", revision=2)
        self.assertEqual("READY", recovery.state, recovery.error_code)
        self.assertTrue(context.broker._credential_paused)
        await self.apply(recovery)
        self.assertEqual("ACTIVE", recovery.state)
        self.assertEqual("token:a3", context.client._token)

    async def test_aborted_reactivation_restores_disabled_revision_without_restoring_keys(self):
        context = await self.active()
        await self.apply(await self.ready(revision=1, disabled=True))
        operation = await self.ready(key="a2", revision=2)
        with patch.object(self.store, "load_account_settings", side_effect=OSError("fake-read")):
            await self.apply(operation)
        self.assertEqual("FAILED", operation.state)
        self.assertEqual(2, self.owner.active_revision(self.profile))
        self.assertTrue(context.broker._credential_paused)
        self.assertIsNone(context.client._token)
        self.assertIsNone(self.owner.bundle(self.profile))

    async def test_bootstrap_legacy_market_credentials_starts_only_verified_account(self):
        self.vault.import_initial("kiwoom_real", "nas-real-default",
            {"app_key": "a1", "secret_key": uuid.uuid4().hex})
        await self.owner.start()
        context = self.owner.bundle("nas-real-default")
        self.assertIsNotNone(context)
        self.assertIs(context.broker, self.market_broker)
        self.assertEqual(1, self.owner.active_revision("nas-real-default"))
        self.assertEqual("token:a1", context.client._token)

    async def test_drained_validation_rejects_live_client_and_keeps_active_generation_and_rate_history(self):
        context = await self.active()
        with self.assertRaises(RuntimeError):
            await context.broker.prepare_drained_credentials(KiwoomSettings("a2", uuid.uuid4().hex, "real"))
        await context.broker.begin_credential_change()
        generation, token = context.client._credential_generation, context.client._token
        lock, previous_time = context.client._request_lock, context.client._last_request_at
        prepared = await context.broker.prepare_drained_credentials(KiwoomSettings("a2", uuid.uuid4().hex, "real"))
        self.assertEqual(generation, context.client._credential_generation)
        self.assertEqual(token, context.client._token)
        self.assertIs(lock, context.client._request_lock)
        self.assertGreaterEqual(context.client._last_request_at, previous_time)
        self.assertTrue(prepared.account_query_completed)
        with self.assertRaises(RuntimeError):
            await context.broker.request("kt00007", "/api/dostk/acnt", {})

    async def test_https_app_real_prepare_apply_and_scoped_query_use_selected_account(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        settings = CentralServerSettings(f"sqlite:///{self.root / 'app.sqlite'}", "private-token",
            credential_directory=str(self.root / "app-secrets"),
            account_identity_registry_enabled=True, account_identity_hmac_key="x" * 32,
            autonomous_top20_enabled=False, market_event_collection_enabled=False)
        with patch("kiwoom_monitor.central_server.app.CentralRealtimeCollector.start", new=AsyncMock()):
            app = create_app(settings)
            with TestClient(app, base_url="https://nas.test") as client:
                headers = {"Authorization": "Bearer private-token"}
                metadata = client.get("/api/v1/settings/credentials", headers=headers).json()
                self.assertTrue(next(p["supported"] for p in metadata["providers"] if p["provider"] == "kiwoom_real"))
                for profile, key in (("nas-real-default", "a1"), (None, "b1")):
                    if profile is None:
                        response = client.post("/api/v1/settings/credentials/kiwoom_real/profiles", headers=headers,
                            json={"request_id": str(uuid.uuid4()), "label": "second"})
                        self.assertEqual(200, response.status_code)
                        profile = response.json()["profile_id"]
                    body = {"request_id": str(uuid.uuid4()), "expected_revision": 0,
                        "replacement": {"app_key": key, "secret_key": uuid.uuid4().hex}}
                    url = f"/api/v1/settings/credentials/kiwoom_real/profiles/{profile}/prepare"
                    self.assertEqual(401, client.post(url, json=body).status_code)
                    self.assertEqual(426, client.post("http://nas.test" + url, headers=headers, json=body).status_code)
                    response = client.post(url, headers=headers, json=body)
                    self.assertEqual(202, response.status_code, response.text)
                    operation_id = response.json()["operation_id"]
                    async def wait_operation():
                        await asyncio.shield(app.state.credential_runtime._operations[operation_id].task)
                    client.portal.call(wait_operation)
                    status_url = f"/api/v1/settings/credential-operations/{operation_id}"
                    status = client.get(status_url, headers=headers).json()
                    self.assertEqual("READY", status["state"], status)
                    client.post(status_url + "/apply", headers=headers,
                        json={"expected_revision": 0, "target_account_ref": status["target_account_ref"]})
                    client.portal.call(wait_operation)
                    self.assertEqual("ACTIVE", client.get(status_url, headers=headers).json()["state"])
                accounts = client.get("/api/v3/kiwoom/accounts", headers=headers).json()["accounts"]
                self.assertEqual(2, len(accounts))
                for account in accounts:
                    setting = client.get(f"/api/v1/settings/accounts/{account['account_ref']}?environment=real",
                                         headers=headers).json()
                    self.assertTrue(setting["settings"]["monitor_enabled"])
                    self.assertEqual(1, setting["applied_revision"])
                    self.assertEqual("rest_poll", setting["monitor_status"]["mode"])
                    self.assertIsNone(setting["monitor_status"]["last_success_at"])
                    response = client.post("/api/v3/kiwoom/account-query", headers=headers, json={
                        "api_id": "kt00007", "path": "/api/dostk/acnt", "body": {},
                        "account_scope": {k: account[k] for k in ("broker", "environment", "account_ref")},
                        "credential_profile_id": account["credential_profile_id"],
                        "expected_binding_revision": account["binding_revision"]})
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertEqual("a1" if account["credential_profile_id"] == "nas-real-default" else "b1",
                                     response.json()["payload"]["credential_marker"])
                    self.assertEqual(account["account_ref"], response.json()["context"]["account_ref"])
                role_url = "/api/v1/settings/market-profile"
                target = next(account for account in accounts if account["credential_profile_id"] != "nas-real-default")
                role_body = {"market_profile_id": target["credential_profile_id"], "expected_revision": 0,
                             "expected_binding_revision": target["binding_revision"]}
                self.assertEqual(401, client.put(role_url, json=role_body).status_code)
                response = client.put(role_url, headers=headers, json=role_body)
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(1, response.json()["applied_revision"])
                self.assertEqual("nas-real-default", response.json()["settings"]["legacy_real_profile_id"])
                self.assertEqual(409, client.put(role_url, headers=headers, json=role_body).status_code)
                collector = app.state.real_credential_owner.collector
                selected_scope = collector._account_scope_resolver("2345678901")
                self.assertEqual(target["account_ref"], selected_scope.account_ref)
                self.assertIsNone(collector._account_scope_resolver("1234567890"))
                response = client.post("/api/v2/kiwoom/account-query", headers=headers, json={
                    "api_id": "kt00007", "path": "/api/dostk/acnt", "body": {}})
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual("a1", response.json()["payload"]["credential_marker"])
