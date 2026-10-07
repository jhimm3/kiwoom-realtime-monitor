from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from kiwoom_monitor.application.account_identity import bind_verified_account_identity
from kiwoom_monitor.central_server.credential_runtime import CredentialRuntime
from kiwoom_monitor.central_server.credential_store import CredentialStore
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.mock_runtime import MockCredentialOwner
from kiwoom_monitor.domain.order_contract import AccountEnvironment
from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import VerifiedAccountIdentity
from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient, PreparedKiwoomCredentials


class FakeClient(KiwoomRestClient):
    fail_read = False
    read_entered = None
    read_release = None
    validate_entered = None
    validate_release = None
    counter_lock = threading.Lock()
    active_reads = 0
    max_reads = 0

    def prepare_credential_token(self, settings):
        with self._request_lock:
            self._ensure_credential_accepting()
            if settings.app_key == "bad": raise RuntimeError("fake-sensitive-error")
            self._last_request_at = time.monotonic()
            return PreparedKiwoomCredentials(settings, "token:" + settings.app_key,
                datetime.now(timezone.utc) + timedelta(hours=1), {}, self._credential_generation)

    def verify_prepared_credentials(self, prepared):
        with self._request_lock:
            if self.validate_entered is not None:
                self.validate_entered.set(); self.validate_release.wait(timeout=5)
            self._last_request_at = time.monotonic()
            return PreparedKiwoomCredentials(prepared.settings, prepared.token, prepared.expires_at,
                {"acctNo": ("1234567890" if prepared.settings.app_key.startswith("a") else
                            "3456789012" if prepared.settings.app_key.startswith("c") else "2345678901")},
                self._credential_generation, True)

    def request_with_continuation(self, api_id, path, body, **kwargs):
        with self._request_lock:
            self._ensure_credential_accepting()
            if api_id == "ka10075" and self.read_entered is not None:
                with FakeClient.counter_lock:
                    FakeClient.active_reads += 1
                    FakeClient.max_reads = max(FakeClient.max_reads, FakeClient.active_reads)
                try:
                    self.read_entered.set(); self.read_release.wait(timeout=5)
                finally:
                    with FakeClient.counter_lock: FakeClient.active_reads -= 1
            if self.fail_read: raise RuntimeError("fake-sensitive-read-error")
            return {"ka10075": {"oso": []}, "ka10076": {"cntr": []},
                "kt00018": {"acnt_evlt_remn_indv_tot": []}, "kt00001": {"ord_alow_amt": "1000000"},
            }[api_id], False, ""


class MockCredentialOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        FakeClient.active_reads = FakeClient.max_reads = 0
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SQLiteQueryStore(self.root / "central.sqlite"); self.store.initialize()
        self.vault = CredentialStore(self.root / "secrets", self.store)
        self.client_patch = patch("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", FakeClient)
        self.client_patch.start()
        self.ws_patch = patch("kiwoom_monitor.central_server.mock_account_monitor.MockAccountRealtimeCollector.start", new=AsyncMock())
        self.ws_patch.start()
        self.owner = MockCredentialOwner(self.store, self.vault, hmac_key=b"x" * 32)
        self.runtime = CredentialRuntime(self.vault, self.store)
        self.runtime.register("kiwoom_mock", self.owner.hooks())
        self.profile = (await self.runtime.create_profile("kiwoom_mock", str(uuid.uuid4()), "one"))["profile_id"]

    async def asyncTearDown(self):
        for event in (FakeClient.read_release, FakeClient.validate_release):
            if event is not None: event.set()
        FakeClient.fail_read = False
        await self.runtime.close(); await self.owner.close()
        self.ws_patch.stop(); self.client_patch.stop(); self.vault.close(); self.store.close(); self.temp.cleanup()
        FakeClient.read_entered = FakeClient.read_release = None
        FakeClient.validate_entered = FakeClient.validate_release = None

    async def ready(self, key="a1", profile=None, revision=0):
        document = await self.runtime.prepare("kiwoom_mock", profile or self.profile, str(uuid.uuid4()), revision,
                                               {"app_key": key, "secret_key": "fake-secret"})
        operation = self.runtime._operations[document["operation_id"]]
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        return operation

    async def apply(self, operation):
        await self.runtime.apply(operation.operation_id, operation.expected_revision, operation.candidate.account_ref)
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        return operation

    async def active(self):
        operation = await self.apply(await self.ready())
        self.assertEqual("ACTIVE", operation.state)
        return self.owner.bundle(self.profile)

    async def test_new_profile_is_admitted_only_after_account_read_with_orders_off(self):
        bundle = await self.active()
        self.assertTrue(bundle.runtime._active)
        self.assertIsNone(bundle.gateway)
        self.assertEqual("token:a1", bundle.client._token)
        self.assertEqual(1, self.owner.active_revision(self.profile))
        self.assertEqual(1, self.owner.applied_settings_revision(bundle.binding.scope.to_dict()))
        self.assertIsNot(bundle.client._request_lock, self.owner._probe._client._request_lock)

    async def test_disable_preserves_binding_and_history_and_replacement_reuses_run(self):
        old = await self.active()
        before_bindings = self.store.load_account_bindings()
        prepared = await self.runtime.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), 1, {}, disabled=True)
        operation = self.runtime._operations[prepared["operation_id"]]
        await asyncio.shield(operation.task)
        self.assertEqual("READY", operation.state)
        self.assertIs(old, self.owner.bundle(self.profile))
        await self.apply(operation)
        self.assertEqual("ACTIVE", operation.state)
        self.assertIsNone(self.owner.bundle(self.profile))
        self.assertEqual(before_bindings, self.store.load_account_bindings())
        self.assertIsNone(old.client._token)
        self.assertEqual("", old.client._settings.app_key)
        record = self.vault.load("kiwoom_mock", self.profile)
        self.assertTrue(record.disabled); self.assertEqual({}, record.payload["credentials"])
        settings = self.store.load_account_settings(old.binding.scope.to_dict())
        self.assertIsNone(settings["active_profile_id"])
        self.assertFalse(settings["monitor_enabled"]); self.assertFalse(settings["mock_order_enabled"])
        self.assertEqual(settings["revision"], self.owner.applied_settings_revision(settings["scope"]))
        await self.apply(await self.ready("a2", revision=2))
        new = self.owner.bundle(self.profile)
        self.assertEqual(old.run_id, new.run_id); self.assertIs(old.broker, new.broker)
        self.assertIsNone(new.monitor._task)

    async def test_disable_normalizes_legacy_non_uuid_runtime_run_id(self):
        old = await self.active()
        old._run_id = "legacy-mock-run"

        prepared = await self.runtime.prepare(
            "kiwoom_mock", self.profile, str(uuid.uuid4()), 1, {}, disabled=True,
        )
        operation = self.runtime._operations[prepared["operation_id"]]
        await asyncio.wait_for(asyncio.shield(operation.task), 5)

        self.assertEqual("READY", operation.state)
        self.assertEqual(old.account_ref, operation.candidate.account_ref)
        self.assertNotEqual(old.run_id, operation.candidate.run_id)
        uuid.UUID(operation.candidate.run_id)
        await self.apply(operation)
        self.assertEqual("ACTIVE", operation.state)
        self.assertTrue(self.vault.load("kiwoom_mock", self.profile).disabled)

    async def test_legacy_import_without_activation_can_be_disabled_from_durable_binding(self):
        legacy = "nas-mock-default"
        self.store.register_credential_profile(
            "kiwoom_mock", legacy, datetime.now(timezone.utc).isoformat(),
        )
        binding = bind_verified_account_identity(
            VerifiedAccountIdentity(
                "kiwoom", AccountEnvironment.MOCK, "f" * 64, datetime.now(timezone.utc),
            ),
            self.store,
            credential_profile_id=legacy,
        )
        # Early account registration could retain only account_ref in the vault
        # before the full activation receipt contract existed.
        self.vault.save(
            "kiwoom_mock", legacy,
            {"app_key": "expired", "secret_key": "fake-secret"},
            expected_revision=0,
            activation={"account_ref": binding.scope.account_ref},
        )

        prepared = await self.runtime.prepare(
            "kiwoom_mock", legacy, str(uuid.uuid4()), 1, {}, disabled=True,
        )
        operation = self.runtime._operations[prepared["operation_id"]]
        await asyncio.wait_for(asyncio.shield(operation.task), 5)

        self.assertEqual("READY", operation.state)
        self.assertEqual(binding.scope.account_ref, operation.candidate.account_ref)
        uuid.UUID(operation.candidate.run_id)
        await self.apply(operation)
        self.assertEqual("ACTIVE", operation.state)
        self.assertTrue(self.vault.load("kiwoom_mock", legacy).disabled)

    async def test_disabled_bootstrap_does_not_verify_or_revive_credentials(self):
        old = await self.active()
        prepared = await self.runtime.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), 1, {}, disabled=True)
        operation = self.runtime._operations[prepared["operation_id"]]
        await asyncio.shield(operation.task); await self.apply(operation)
        bindings = self.store.load_account_bindings()
        await self.owner.close()
        self.owner = MockCredentialOwner(self.store, self.vault, hmac_key=b"x" * 32)
        await self.owner.start()
        self.assertEqual([], self.owner._boot_tasks)
        self.assertEqual(2, self.owner.active_revision(self.profile))
        self.assertEqual(bindings, self.store.load_account_bindings())
        record = self.vault.load("kiwoom_mock", self.profile)
        self.store.finalize_credential_activation({**record.payload["activation"],
            "provider": "kiwoom_mock", "profile_id": self.profile, "credential_revision": record.revision})
        self.assertIsNone(self.store.load_account_settings(old.binding.scope.to_dict())["active_profile_id"])

    async def test_failed_disable_after_file_commit_never_resumes_old_connection(self):
        old = await self.active()
        prepared = await self.runtime.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), 1, {}, disabled=True)
        operation = self.runtime._operations[prepared["operation_id"]]
        await asyncio.shield(operation.task)
        with patch.object(self.store, "finalize_credential_activation", side_effect=OSError("fake commit failure")):
            await self.apply(operation)
        self.assertEqual("RECOVERY_REQUIRED", operation.state)
        self.assertTrue(self.vault.load("kiwoom_mock", self.profile).disabled)
        self.assertIsNone(self.owner.bundle(self.profile)); self.assertFalse(old.runtime._active)
        self.assertTrue(old.broker._credential_paused)
        secondary = MockCredentialOwner(self.store, self.vault, hmac_key=b"x" * 32)
        try:
            await secondary.start()
            self.assertIsNone(secondary.active_revision(self.profile))
            self.assertEqual("ACTIVATION_RECOVERY_REQUIRED", secondary.errors[self.profile])
        finally:
            await secondary.close()
        prepared = await self.runtime.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), 2, {}, disabled=True)
        recovery = self.runtime._operations[prepared["operation_id"]]
        await asyncio.shield(recovery.task); await self.apply(recovery)
        self.assertEqual("ACTIVE", recovery.state)
        self.assertIsNone(old.client._token)
        self.assertEqual(1, len(self.store.load_account_bindings()))

    async def test_disable_validation_excludes_settings_before_receipt_read_finishes(self):
        from kiwoom_monitor.central_server.credential_runtime import CredentialOperationError
        old = await self.active()
        entered, release = threading.Event(), threading.Event()
        finalize = self.store.finalize_credential_activation
        def blocked(value):
            entered.set()
            if not release.wait(5): raise RuntimeError("test gate timeout")
            return finalize(value)
        try:
            with patch.object(self.store, "finalize_credential_activation", side_effect=blocked):
                prepared = await self.runtime.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), 1, {}, disabled=True)
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                with self.assertRaises(CredentialOperationError) as raised:
                    await self.owner.update_settings(old.binding.scope.to_dict(), {"active_profile_id": self.profile,
                        "monitor_enabled": False, "mock_order_enabled": False}, 1)
                self.assertEqual("PROFILE_BUSY", raised.exception.code)
                self.assertIs(old, self.owner.bundle(self.profile)); self.assertTrue(old.runtime._active)
                release.set()
                operation = self.runtime._operations[prepared["operation_id"]]
                await asyncio.wait_for(asyncio.shield(operation.task), 5)
                self.assertEqual("READY", operation.state)
                await self.runtime.cancel(operation.operation_id)
        finally:
            release.set()

    async def test_pre_commit_deadline_keeps_previously_disabled_profile_disabled(self):
        old = await self.active()
        prepared = await self.runtime.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), 1, {}, disabled=True)
        disabled = self.runtime._operations[prepared["operation_id"]]
        await asyncio.shield(disabled.task); await self.apply(disabled)
        replacement = await self.ready("a2", revision=2)
        self.runtime._deadline = 0.000001
        await self.apply(replacement)
        self.assertEqual("FAILED", replacement.state)
        self.assertIsNone(self.owner.bundle(self.profile))
        self.assertEqual(2, self.owner.active_revision(self.profile))
        self.assertIsNone(old.client._token); self.assertEqual("", old.client._settings.app_key)
        self.assertTrue(self.vault.load("kiwoom_mock", self.profile).disabled)

    async def test_settings_off_on_keeps_credentials_scope_run_and_binding(self):
        old = await self.active(); scope = old.binding.scope.to_dict()
        bindings = self.store.load_account_bindings()
        result = await self.owner.update_settings(scope, {"active_profile_id": self.profile,
            "monitor_enabled": False, "mock_order_enabled": False}, 1)
        off = self.owner.bundle(self.profile)
        self.assertIsNone(off.monitor._task); self.assertFalse(old.runtime._active)
        self.assertEqual(2, result["applied_revision"])
        result = await self.owner.update_settings(scope, {"active_profile_id": self.profile,
            "monitor_enabled": True, "mock_order_enabled": True}, 2)
        new = self.owner.bundle(self.profile)
        self.assertTrue(new.runtime._active); self.assertIsNotNone(new.gateway)
        self.assertIs(old.client, new.client); self.assertIs(old.broker, new.broker)
        self.assertEqual(old.run_id, new.run_id)
        self.assertEqual(bindings, self.store.load_account_bindings())
        self.assertEqual(1, self.vault.load("kiwoom_mock", self.profile).revision)
        self.assertEqual(3, result["applied_revision"])

    async def test_settings_validation_conflict_and_ready_lock_leave_connection_unchanged(self):
        from kiwoom_monitor.central_server.credential_runtime import CredentialOperationError
        old = await self.active(); scope = old.binding.scope.to_dict()
        for value, revision in (({"active_profile_id": self.profile, "monitor_enabled": False,
                "mock_order_enabled": True}, 1), ({"active_profile_id": self.profile,
                "monitor_enabled": True, "mock_order_enabled": False}, 0)):
            with self.assertRaises(CredentialOperationError):
                await self.owner.update_settings(scope, value, revision)
            self.assertIs(old, self.owner.bundle(self.profile))
        candidate = await self.ready("a2", revision=1)
        with self.assertRaises(CredentialOperationError) as raised:
            await self.owner.update_settings(scope, {"active_profile_id": self.profile,
                "monitor_enabled": False, "mock_order_enabled": False}, 1)
        self.assertEqual("PROFILE_BUSY", raised.exception.code)
        await self.runtime.cancel(candidate.operation_id)

    async def test_settings_post_commit_read_failure_fences_and_same_settings_can_recover(self):
        from kiwoom_monitor.central_server.credential_runtime import CredentialOperationError
        old = await self.active(); scope = old.binding.scope.to_dict()
        value = {"active_profile_id": self.profile, "monitor_enabled": True, "mock_order_enabled": True}
        FakeClient.fail_read = True
        with self.assertRaises(CredentialOperationError) as raised:
            await self.owner.update_settings(scope, value, 1)
        self.assertEqual("ACCOUNT_SETTINGS_RECOVERY_REQUIRED", raised.exception.code)
        self.assertIsNone(self.owner.bundle(self.profile)); self.assertIsNone(self.owner.active_revision(self.profile))
        self.assertEqual(2, self.store.load_account_settings(scope)["revision"])
        self.assertIsNone(self.owner.applied_settings_revision(scope))
        FakeClient.fail_read = False
        result = await self.owner.update_settings(scope, value, 2)
        self.assertEqual(2, result["applied_revision"])
        self.assertTrue(self.owner.bundle(self.profile).runtime._active)

    async def test_settings_cannot_resume_old_key_after_new_key_file_commit(self):
        from kiwoom_monitor.central_server.credential_runtime import CredentialOperationError
        old = await self.active(); scope = old.binding.scope.to_dict()
        operation = await self.ready("a2", revision=1)
        with patch.object(self.store, "finalize_credential_activation", side_effect=OSError("fake database failure")):
            await self.apply(operation)
        self.assertEqual("RECOVERY_REQUIRED", operation.state)
        self.assertEqual("token:a1", old.client._token)
        with self.assertRaises(CredentialOperationError) as raised:
            await self.owner.update_settings(scope, {"active_profile_id": self.profile,
                "monitor_enabled": True, "mock_order_enabled": False}, 1)
        self.assertEqual("PROFILE_RUNTIME_NOT_READY", raised.exception.code)
        self.assertIsNone(self.owner.bundle(self.profile))
        self.assertTrue(old.broker._credential_paused)

    async def test_settings_pre_commit_storage_failure_restores_old_settings_and_connection(self):
        from kiwoom_monitor.central_server.credential_runtime import CredentialOperationError
        old = await self.active(); scope = old.binding.scope.to_dict()
        with patch.object(self.store, "save_account_settings", side_effect=OSError("fake save failure")):
            with self.assertRaises(CredentialOperationError) as raised:
                await self.owner.update_settings(scope, {"active_profile_id": self.profile,
                    "monitor_enabled": False, "mock_order_enabled": False}, 1)
        self.assertEqual("ACCOUNT_SETTINGS_APPLY_FAILED", raised.exception.code)
        restored = self.owner.bundle(self.profile)
        self.assertTrue(restored.runtime._active)
        self.assertEqual(1, self.owner.applied_settings_revision(scope))
        self.assertIs(old.client, restored.client)

    async def test_settings_waiter_cancellation_does_not_abandon_actual_read(self):
        old = await self.active(); scope = old.binding.scope.to_dict()
        FakeClient.read_entered = threading.Event(); FakeClient.read_release = threading.Event()
        waiter = asyncio.create_task(self.owner.update_settings(scope, {"active_profile_id": self.profile,
            "monitor_enabled": True, "mock_order_enabled": True}, 1))
        self.assertTrue(await asyncio.to_thread(FakeClient.read_entered.wait, 3))
        actual = next(iter(self.owner._settings_tasks))
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError): await waiter
        self.assertIsNone(self.owner.bundle(self.profile))
        self.assertTrue(self.owner._account_locks[old.account_ref].locked())
        FakeClient.read_release.set()
        await asyncio.wait_for(asyncio.shield(actual), 5)
        self.assertEqual(2, self.owner.applied_settings_revision(scope))

    async def test_same_account_rotation_keeps_client_broker_rate_history_run_and_scope(self):
        old = await self.active(); old_run = old.run_id
        operation = await self.ready("a2", revision=1)
        self.assertEqual("token:a1", old.client._token)
        await self.apply(operation)
        new = self.owner.bundle(self.profile)
        self.assertEqual("ACTIVE", operation.state)
        self.assertIs(old.client, new.client); self.assertIs(old.broker, new.broker)
        self.assertEqual(old_run, new.run_id); self.assertEqual(old.account_ref, new.account_ref)
        self.assertEqual(1.0, new.client._base_request_interval_seconds)
        self.assertIsNotNone(new.client._last_request_at)
        self.assertEqual("token:a2", new.client._token)
        self.assertEqual(2, new.binding.binding_revision)
        self.assertIsNone(old._resolve_scope("1234567890"))
        self.assertIsNotNone(new._resolve_scope("1234567890"))

    async def test_second_account_has_independent_runtime_and_keeps_first_alive(self):
        first = await self.active()
        profile = (await self.runtime.create_profile("kiwoom_mock", str(uuid.uuid4()), "two"))["profile_id"]
        await self.apply(await self.ready("b1", profile))
        second = self.owner.bundle(profile)
        self.assertIs(first, self.owner.bundle(self.profile)); self.assertTrue(first.runtime._active)
        self.assertNotEqual(first.account_ref, second.account_ref); self.assertNotEqual(first.run_id, second.run_id)
        self.assertIsNot(first.client, second.client); self.assertIsNot(first.broker, second.broker)
        self.assertIsNone(second.gateway)

    async def test_duplicate_account_profile_is_rejected_before_vault_commit(self):
        first = await self.active()
        profile = (await self.runtime.create_profile("kiwoom_mock", str(uuid.uuid4()), "duplicate"))["profile_id"]
        operation = await self.ready("a3", profile)
        self.assertEqual("FAILED", operation.state); self.assertEqual("ACCOUNT_PROFILE_CONFLICT", operation.error_code)
        self.assertIsNone(self.vault.load("kiwoom_mock", profile))
        self.assertIs(first, self.owner.bundle(self.profile))

    async def test_account_change_and_invalid_token_keep_active_credentials(self):
        first = await self.active()
        for key, code in (("b1", "ACCOUNT_CHANGED"), ("bad", "CREDENTIAL_VALIDATION_FAILED")):
            operation = await self.ready(key, revision=1)
            self.assertEqual("FAILED", operation.state); self.assertEqual(code, operation.error_code)
            self.assertIs(first, self.owner.bundle(self.profile)); self.assertEqual("token:a1", first.client._token)
        self.assertEqual(1, self.vault.load("kiwoom_mock", self.profile).revision)

    async def test_cancel_ready_releases_account_lock_and_candidate_references(self):
        operation = await self.ready(); plan = operation.candidate.prepared
        self.assertTrue(plan.lock.locked())
        await self.runtime.cancel(operation.operation_id)
        self.assertIsNone(plan.lock); self.assertIsNone(plan.prepared); self.assertIsNone(plan.identity)
        self.assertEqual({}, self.owner._account_locks)
        self.assertIsNone(self.vault.load("kiwoom_mock", self.profile))

    async def test_cancel_while_validation_runs_releases_late_candidate(self):
        FakeClient.validate_entered = threading.Event(); FakeClient.validate_release = threading.Event()
        document = await self.runtime.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), 0,
                                               {"app_key": "a1", "secret_key": "fake-secret"})
        operation = self.runtime._operations[document["operation_id"]]
        self.assertTrue(await asyncio.to_thread(FakeClient.validate_entered.wait, 2))
        await self.runtime.cancel(operation.operation_id)
        FakeClient.validate_release.set(); await asyncio.wait_for(operation.task, 5)
        self.assertEqual("CANCELLED", operation.state); self.assertEqual({}, self.owner._account_locks)
        self.assertIsNone(operation.candidate)

    async def test_initial_read_blocks_active_publication_and_survives_waiter_cancel(self):
        operation = await self.ready()
        FakeClient.read_entered = threading.Event(); FakeClient.read_release = threading.Event()
        await self.runtime.apply(operation.operation_id, 0, operation.candidate.account_ref)
        self.assertTrue(await asyncio.to_thread(FakeClient.read_entered.wait, 2))
        self.assertTrue(operation.committed); self.assertIsNone(self.owner.active_revision(self.profile))
        self.assertIsNone(self.owner.bundle(self.profile))
        async def wait(): await asyncio.shield(operation.task)
        waiter = asyncio.create_task(wait()); await asyncio.sleep(0); waiter.cancel()
        with self.assertRaises(asyncio.CancelledError): await waiter
        FakeClient.read_release.set(); await asyncio.wait_for(operation.task, 5)
        self.assertEqual("ACTIVE", operation.state)

    async def test_post_commit_read_failure_is_fenced_and_can_be_recovered_with_next_key(self):
        operation = await self.ready(); FakeClient.fail_read = True
        await self.apply(operation)
        self.assertEqual("RECOVERY_REQUIRED", operation.state); self.assertTrue(operation.committed)
        self.assertIsNone(self.owner.bundle(self.profile)); self.assertIsNone(self.owner.active_revision(self.profile))
        self.assertEqual(1, self.vault.load("kiwoom_mock", self.profile).revision)
        self.assertFalse(self.owner._contexts[self.profile].runtime._active)
        FakeClient.fail_read = False
        recovered = await self.apply(await self.ready("a2", revision=1))
        self.assertEqual("ACTIVE", recovered.state)
        self.assertEqual(operation.candidate.run_id, self.owner.bundle(self.profile).run_id)

    async def test_pre_commit_settings_conflict_restores_old_connection_and_vault(self):
        old = await self.active(); operation = await self.ready("a2", revision=1)
        settings = self.store.load_account_settings(old.binding.scope.to_dict())
        self.store.save_account_settings({key: (False if key == "monitor_enabled" else value)
            for key, value in settings.items() if key != "revision"}, expected_revision=1)
        await self.apply(operation)
        self.assertEqual("FAILED", operation.state); self.assertFalse(operation.committed)
        restored = self.owner.bundle(self.profile)
        self.assertEqual("token:a1", restored.client._token); self.assertTrue(restored.runtime._active)
        self.assertEqual(old.run_id, restored.run_id); self.assertIs(old.client, restored.client)
        self.assertEqual(1, self.vault.load("kiwoom_mock", self.profile).revision)
        self.assertEqual(1, self.owner.applied_settings_revision(restored.binding.scope.to_dict()))

    async def test_owner_bootstrap_restores_multiple_committed_profiles_without_new_binding_revisions(self):
        first = await self.active()
        profile = (await self.runtime.create_profile("kiwoom_mock", str(uuid.uuid4()), "two"))["profile_id"]
        await self.apply(await self.ready("b1", profile))
        await self.owner.close()
        self.owner = MockCredentialOwner(self.store, self.vault, hmac_key=b"x" * 32)
        await self.owner.start()
        await asyncio.wait_for(asyncio.gather(*self.owner._boot_tasks), 5)
        self.assertEqual(2, len(self.owner._bundles)); self.assertEqual(2, len(self.store.load_account_bindings()))
        self.assertEqual(first.run_id, self.owner.bundle(self.profile).run_id)

    async def test_pending_file_commit_receipt_is_recovered_before_next_activation(self):
        operation = await self.ready()
        with patch.object(self.store, "finalize_credential_activation", side_effect=RuntimeError("fake-db-failure")):
            await self.apply(operation)
        self.assertTrue(operation.committed); self.assertEqual("RECOVERY_REQUIRED", operation.state)
        self.assertEqual("ACTIVATION_RECOVERY_REQUIRED", self.owner.errors[self.profile])
        self.assertIsNone(self.store.find_credential_activation(operation_id=operation.operation_id))
        next_operation = await self.ready("a2", revision=1)
        self.assertEqual("READY", next_operation.state)
        self.assertIsNotNone(self.store.find_credential_activation(operation_id=operation.operation_id))
        await self.apply(next_operation)
        self.assertEqual("ACTIVE", next_operation.state)
        self.assertEqual(2, len(self.store.load_credential_activations(self.profile)))

    async def test_https_api_activates_scoped_profile_and_leaves_real_market_and_legacy_orders_intact(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from kiwoom_monitor.domain.order_contract import AccountEnvironment
        from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import VerifiedAccountIdentity
        settings = CentralServerSettings(
            f"sqlite:///{self.root / 'app.sqlite'}", "private-token",
            credential_directory=str(self.root / "app-secrets"),
            account_identity_registry_enabled=True, account_identity_hmac_key="x" * 32,
            kiwoom_app_key="fake-real-key", kiwoom_secret_key="fake-real-secret", kiwoom_environment="real",
            autonomous_top20_enabled=False, market_event_collection_enabled=False,
        )
        with patch("kiwoom_monitor.infrastructure.kiwoom_rest.account_identity.KiwoomAccountIdentityReader.verify",
                   new=AsyncMock(return_value=VerifiedAccountIdentity("kiwoom", AccountEnvironment.REAL,
                        "f" * 64, datetime.now(timezone.utc)))), patch(
            "kiwoom_monitor.central_server.app.CentralRealtimeCollector.start", new=AsyncMock(),
        ):
            app = create_app(settings)
            with TestClient(app, base_url="https://nas.test") as client:
                headers = {"Authorization": "Bearer private-token"}
                metadata = client.get("/api/v1/settings/credentials", headers=headers).json()
                self.assertTrue(next(p["supported"] for p in metadata["providers"] if p["provider"] == "kiwoom_mock"))
                response = client.post("/api/v1/settings/credentials/kiwoom_mock/profiles", headers=headers,
                    json={"request_id": str(uuid.uuid4()), "label": "test-account"})
                self.assertEqual(200, response.status_code)
                profile_id = response.json()["profile_id"]
                response = client.post(f"/api/v1/settings/credentials/kiwoom_mock/profiles/{profile_id}/prepare",
                    headers=headers, json={"request_id": str(uuid.uuid4()), "expected_revision": 0,
                    "replacement": {"app_key": "a1", "secret_key": "fake-secret"}})
                self.assertEqual(202, response.status_code)
                operation_id = response.json()["operation_id"]
                async def wait_operation():
                    await asyncio.shield(app.state.credential_runtime._operations[operation_id].task)
                client.portal.call(wait_operation)
                status = client.get(f"/api/v1/settings/credential-operations/{operation_id}", headers=headers).json()
                self.assertEqual("READY", status["state"])
                response = client.post(f"/api/v1/settings/credential-operations/{operation_id}/apply", headers=headers,
                    json={"expected_revision": 0, "target_account_ref": status["target_account_ref"]})
                self.assertEqual(202, response.status_code)
                client.portal.call(wait_operation)
                status = client.get(f"/api/v1/settings/credential-operations/{operation_id}", headers=headers).json()
                self.assertEqual("ACTIVE", status["state"])
                self.assertTrue(client.get("/api/v1/capabilities", headers=headers).json()["capabilities"]["kiwoom_rest"])
                self.assertTrue(client.get("/health").json()["mock_account_available"])
                setting = client.get(f"/api/v1/settings/accounts/{status['target_account_ref']}?environment=mock",
                                     headers=headers).json()
                self.assertEqual(setting["settings"]["revision"], setting["applied_revision"])
                self.assertFalse(setting["settings"]["mock_order_enabled"])
                settings_url = f"/api/v1/settings/accounts/{status['target_account_ref']}?environment=mock"
                settings_value = {"expected_revision": 1, "active_profile_id": profile_id,
                                  "monitor_enabled": False, "mock_order_enabled": False}
                self.assertEqual(401, client.put(settings_url, json=settings_value).status_code)
                insecure = client.put("http://testserver" + settings_url, headers=headers, json=settings_value)
                self.assertEqual(426, insecure.status_code)
                malformed = client.put(settings_url, headers=headers,
                    content='{"expected_revision":1,"expected_revision":2}')
                self.assertEqual(422, malformed.status_code)
                response = client.put(settings_url, headers=headers, json=settings_value)
                self.assertEqual(200, response.status_code)
                self.assertEqual(2, response.json()["applied_revision"])
                self.assertFalse(client.get("/health").json()["mock_account_available"])
                self.assertTrue(client.get("/api/v1/capabilities", headers=headers).json()["capabilities"]["kiwoom_rest"])
                self.assertEqual(503, client.post("/api/v1/mock/orders", headers=headers,
                    json={"request_id": "legacy", "symbol": "005930", "side": "BUY",
                          "quantity": 1, "limit_price": 1000}).status_code)

    async def test_reserved_market_profile_is_not_advertised_or_prepared_as_account_runtime(self):
        from kiwoom_monitor.central_server.credential_runtime import CredentialOperationError
        self.owner.reserved_profiles.add(self.profile)
        metadata = await self.runtime.profiles()
        self.assertFalse(next(p["supported"] for p in metadata["profiles"] if p["profile_id"] == self.profile))
        with self.assertRaises(CredentialOperationError) as raised:
            await self.runtime.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), 0,
                                       {"app_key": "a1", "secret_key": "fake-secret"})
        self.assertEqual("PROFILE_RUNTIME_NOT_READY", raised.exception.code)

    async def test_off_account_stays_off_when_pre_commit_conflict_restores_prior_connection(self):
        bundle = await self.active()
        settings = self.store.load_account_settings(bundle.binding.scope.to_dict())
        self.store.save_account_settings({key: (False if key == "monitor_enabled" else value)
            for key, value in settings.items() if key != "revision"}, expected_revision=1)
        await self.apply(await self.ready("a2", revision=1))
        off = self.owner.bundle(self.profile); self.assertIsNone(off.monitor._task)
        operation = await self.ready("a3", revision=2)
        settings = self.store.load_account_settings(bundle.binding.scope.to_dict())
        self.store.save_account_settings({key: (True if key == "monitor_enabled" else value)
            for key, value in settings.items() if key != "revision"}, expected_revision=2)
        await self.apply(operation)
        self.assertEqual("FAILED", operation.state)
        self.assertIsNone(self.owner.bundle(self.profile).monitor._task)
        self.assertFalse(self.owner.bundle(self.profile).runtime._active)
        self.assertEqual(2, self.owner.active_revision(self.profile))

    async def test_first_env_import_preserves_disabled_legacy_monitor(self):
        self.vault.save("kiwoom_mock", "nas-mock-default", {"app_key": "a1", "secret_key": "fake-secret"},
                        expected_revision=0)
        self.store.register_credential_profile("kiwoom_mock", "nas-mock-default", datetime.now(timezone.utc).isoformat())
        await self.owner.start()
        await asyncio.wait_for(asyncio.gather(*self.owner._boot_tasks), 5)
        bundle = self.owner.bundle("nas-mock-default")
        self.assertIsNotNone(bundle)
        self.assertIsNone(bundle.monitor._task); self.assertIsNone(bundle.gateway)
        settings = self.store.load_account_settings(bundle.binding.scope.to_dict())
        self.assertFalse(settings["monitor_enabled"]); self.assertFalse(settings["mock_order_enabled"])

    async def test_main_mock_market_profile_uses_separate_identity_and_is_excluded_from_account_bootstrap(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from kiwoom_monitor.domain.order_contract import AccountEnvironment
        from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import VerifiedAccountIdentity
        settings = CentralServerSettings(
            f"sqlite:///{self.root / 'main-mock.sqlite'}", "private-token",
            credential_directory=str(self.root / "main-mock-secrets"),
            account_identity_registry_enabled=True, account_identity_hmac_key="x" * 32,
            kiwoom_app_key="fake-main-mock", kiwoom_secret_key="fake-secret", kiwoom_environment="mock",
            autonomous_top20_enabled=False, market_event_collection_enabled=False,
        )
        with patch("kiwoom_monitor.infrastructure.kiwoom_rest.account_identity.KiwoomAccountIdentityReader.verify",
            new=AsyncMock(return_value=VerifiedAccountIdentity("kiwoom", AccountEnvironment.MOCK,
                "f" * 64, datetime.now(timezone.utc)))), patch(
            "kiwoom_monitor.central_server.app.CentralRealtimeCollector.start", new=AsyncMock(),
        ):
            app = create_app(settings)
            with TestClient(app, base_url="https://nas.test") as client:
                self.assertEqual("nas-main-mock-default", app.state.verified_account_bindings[0].credential_profile_id)
                metadata = client.get("/api/v1/settings/credentials", headers={"Authorization": "Bearer private-token"}).json()
                market = next(p for p in metadata["profiles"] if p["profile_id"] == "nas-main-mock-default")
                self.assertFalse(market["supported"]); self.assertIsNotNone(market["account_ref"])
                self.assertFalse(app.state.mock_credential_owner._boot_tasks)

    async def test_three_account_refreshes_and_token_refresh_share_two_slots(self):
        await self.active()
        for label, key in (("two", "b1"), ("three", "c1")):
            profile = (await self.runtime.create_profile("kiwoom_mock", str(uuid.uuid4()), label))["profile_id"]
            await self.apply(await self.ready(key, profile))
        FakeClient.read_entered = threading.Event(); FakeClient.read_release = threading.Event()
        tasks = [asyncio.create_task(bundle.monitor.refresh_account()) for bundle in self.owner._bundles.values()]
        try:
            for _ in range(200):
                if FakeClient.active_reads == 2: break
                await asyncio.sleep(0.01)
            self.assertEqual(2, FakeClient.active_reads)
            token = asyncio.create_task(self.owner.bundle(self.profile).realtime._async_token_provider())
            tasks.append(token)
            await asyncio.sleep(0)
            self.assertFalse(token.done())
            self.assertEqual(2, FakeClient.max_reads)
        finally:
            FakeClient.read_release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
        self.assertLessEqual(FakeClient.max_reads, 2)

    async def test_legacy_order_route_is_closed_until_bootstrap_admits_verified_bundle(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from kiwoom_monitor.domain.order_contract import AccountEnvironment
        from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import account_identity_fingerprint
        path = self.root / "legacy-app.sqlite"
        registry = SQLiteQueryStore(path); registry.initialize()
        account_ref = registry.register_account_identity({
            "broker": "kiwoom", "environment": "mock", "created_at": datetime.now(timezone.utc).isoformat(),
            "identity_fingerprint": account_identity_fingerprint("1234567890", AccountEnvironment.MOCK, b"x" * 32),
        })
        registry.close()
        app = create_app(CentralServerSettings(
            f"sqlite:///{path}", "private-token", credential_directory=str(self.root / "legacy-app-secrets"),
            account_identity_registry_enabled=True, account_identity_hmac_key="x" * 32,
            kiwoom_environment="real", kiwoom_mock_app_key="a1", kiwoom_mock_secret_key="fake-secret",
            mock_account_monitor_enabled=True, mock_order_transport_enabled=True,
            mock_account_ref=account_ref, mock_execution_run_id=str(uuid.uuid4()),
            autonomous_top20_enabled=False, market_event_collection_enabled=False,
        ))
        FakeClient.validate_entered = threading.Event(); FakeClient.validate_release = threading.Event()
        with TestClient(app, base_url="https://nas.test") as client:
            try:
                self.assertTrue(await asyncio.to_thread(FakeClient.validate_entered.wait, 2))
                self.assertFalse(client.get("/health").json()["mock_account_available"])
                response = client.post("/api/v1/mock/orders", headers={"Authorization": "Bearer private-token"},
                    json={"request_id": "before-verification", "symbol": "005930", "side": "BUY",
                          "quantity": 1, "limit_price": 1000})
                self.assertEqual(503, response.status_code)
            finally:
                FakeClient.validate_release.set()
