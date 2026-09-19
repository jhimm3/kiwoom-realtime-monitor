from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from kiwoom_monitor.central_server.database import SQLiteQueryStore, _load_market_profile_settings, _save_market_profile_settings


class MarketProfileSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "central.sqlite"
        self.store = SQLiteQueryStore(self.path)
        self.store.initialize()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def account(self, environment="real", profile=None):
        profile = profile or str(uuid.uuid4())
        account_ref = self.store.register_account_identity({"broker": "kiwoom", "environment": environment,
            "identity_fingerprint": uuid.uuid4().hex * 2, "created_at": "2026-09-15T12:00:00+09:00"})
        activation = {"provider": f"kiwoom_{environment}", "environment": environment, "profile_id": profile,
            "account_ref": account_ref, "run_id": str(uuid.uuid4()), "operation_id": str(uuid.uuid4()),
            "request_id": str(uuid.uuid4()), "request_digest": uuid.uuid4().hex * 2,
            "credential_revision": 1, "committed_at": "2026-09-15T12:00:00+09:00"}
        self.store.finalize_credential_activation(activation)
        return activation

    def choose(self, profile, *, revision=0, binding=1, store=None):
        return (store or self.store).save_market_profile_settings({
            "market_profile_id": profile, "expected_binding_revision": binding}, expected_revision=revision)

    def test_missing_role_returns_legacy_defaults_without_persisting(self):
        self.assertEqual({"market_profile_id": "nas-real-default", "legacy_real_profile_id": "nas-real-default",
                          "revision": 0}, self.store.load_market_profile_settings())
        self.assertEqual([], self.store.load_documents("server_market_profile_settings"))

    def test_role_changes_keep_legacy_account_binding_and_account_preferences(self):
        first, second = self.account(), self.account()
        before = self.store.load_account_bindings(), self.store.load_documents("server_account_settings")
        chosen = self.choose(first["profile_id"])
        changed = self.choose(second["profile_id"], revision=1)
        self.assertEqual(1, chosen["revision"])
        self.assertEqual(2, changed["revision"])
        self.assertEqual(second["profile_id"], changed["market_profile_id"])
        self.assertEqual("nas-real-default", changed["legacy_real_profile_id"])
        self.assertEqual(before, (self.store.load_account_bindings(), self.store.load_documents("server_account_settings")))

    def test_stale_role_revision_rejected_and_unchanged_save_preserves_timestamp(self):
        account = self.account()
        chosen = self.choose(account["profile_id"])
        before = self.store.load_documents("server_market_profile_settings")
        self.assertEqual(chosen, self.choose(account["profile_id"], revision=1))
        self.assertEqual(before, self.store.load_documents("server_market_profile_settings"))
        with self.assertRaisesRegex(ValueError, "MARKET_PROFILE_SETTINGS_REVISION_CONFLICT"):
            self.choose(account["profile_id"])

    def test_unknown_mock_draft_global_and_archived_profiles_are_ineligible(self):
        mock = self.account("mock")["profile_id"]
        global_profile = str(uuid.uuid4())
        self.store.register_credential_profile("openai", global_profile, "2026-09-15T12:00:00+09:00")
        draft = self.store.create_credential_profile("kiwoom_real", str(uuid.uuid4()), "draft", "digest")["profile_id"]
        archived = self.account()["profile_id"]
        with self.store._connection() as connection:
            connection.execute("UPDATE central_credential_profiles SET lifecycle_state='archived' WHERE profile_id=?", (archived,))
        for profile in (str(uuid.uuid4()), mock, draft, global_profile, archived):
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, "MARKET_PROFILE_UNAVAILABLE"):
                self.choose(profile)
        self.assertEqual([], self.store.load_documents("server_market_profile_settings"))

    def test_unverified_or_inactive_identity_cannot_be_selected(self):
        profile = str(uuid.uuid4())
        self.store.register_credential_profile("kiwoom_real", profile, "2026-09-15T12:00:00+09:00")
        with self.assertRaisesRegex(ValueError, "ACCOUNT_IDENTITY_UNVERIFIED"):
            self.choose(profile)
        account = self.account()
        with self.store._connection() as connection:
            connection.execute("UPDATE central_account_registry SET status='inactive' WHERE account_ref=?", (account["account_ref"],))
        with self.assertRaisesRegex(ValueError, "ACCOUNT_IDENTITY_UNVERIFIED"):
            self.choose(account["profile_id"])

    def test_binding_rotation_rejects_stale_selection_without_changing_role_revision(self):
        account = self.account()
        chosen = self.choose(account["profile_id"])
        self.store.finalize_credential_activation({**account, "credential_revision": 2,
            "operation_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4())})
        with self.assertRaisesRegex(ValueError, "ACCOUNT_CONTEXT_MISMATCH"):
            self.choose(account["profile_id"], revision=1)
        self.assertEqual(chosen, self.choose(account["profile_id"], revision=1, binding=2))

    def test_disabled_profile_cannot_be_selected_and_real_role_does_not_affect_mock(self):
        account = self.account()
        self.store.finalize_credential_activation({**account, "disabled": True, "credential_revision": 2,
            "operation_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4())})
        with self.assertRaisesRegex(ValueError, "MARKET_PROFILE_UNAVAILABLE"):
            self.choose(account["profile_id"])
        real = self.account()
        self.choose(real["profile_id"])
        mock = self.account("mock")
        self.store.finalize_credential_activation({**mock, "disabled": True, "credential_revision": 2,
            "operation_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4())})
        self.assertEqual(real["profile_id"], self.store.load_market_profile_settings()["market_profile_id"])

    def test_market_profile_disable_rolls_back_and_completed_replay_is_still_idempotent(self):
        account = self.account()
        self.choose(account["profile_id"])
        before = self.store.load_account_bindings(), self.store.load_credential_activations(account["profile_id"])
        with self.assertRaisesRegex(ValueError, "MARKET_PROFILE_REQUIRED"):
            self.store.finalize_credential_activation({**account, "disabled": True, "credential_revision": 2,
                "operation_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4())})
        self.assertEqual(before, (self.store.load_account_bindings(), self.store.load_credential_activations(account["profile_id"])))
        self.store.finalize_credential_activation(account)
        self.assertEqual(before, (self.store.load_account_bindings(), self.store.load_credential_activations(account["profile_id"])))

    def test_unlink_is_blocked_but_account_monitor_toggle_keeps_role(self):
        account = self.account()
        chosen = self.choose(account["profile_id"])
        scope = {"broker": "kiwoom", "environment": "real", "account_ref": account["account_ref"]}
        with self.assertRaisesRegex(ValueError, "MARKET_PROFILE_REQUIRED"):
            self.store.save_account_settings({"scope": scope, "active_profile_id": None,
                "monitor_enabled": False, "mock_order_enabled": False}, expected_revision=1)
        self.store.save_account_settings({"scope": scope, "active_profile_id": account["profile_id"],
            "monitor_enabled": False, "mock_order_enabled": False}, expected_revision=1)
        self.assertEqual(chosen, self.store.load_market_profile_settings())

    def test_invalid_fields_bool_versions_and_legacy_override_are_rejected(self):
        value = {"market_profile_id": str(uuid.uuid4()), "expected_binding_revision": 1}
        for document, revision in (({**value, "legacy_real_profile_id": "new"}, 0),
                ({**value, "secret_key": "unused"}, 0), ({**value, "market_profile_id": None}, 0),
                ({**value, "market_profile_id": "../file"}, 0), ({**value, "expected_binding_revision": True}, 0),
                (value, True), (value, -1), (value, 2**63)):
            with self.subTest(document=document), self.assertRaisesRegex(ValueError, "MARKET_PROFILE_SETTINGS_INVALID"):
                self.store.save_market_profile_settings(document, expected_revision=revision)

    def test_corrupt_role_fails_closed_without_resetting_document(self):
        account = self.account()
        self.choose(account["profile_id"])
        with self.store._connection() as connection:
            connection.execute("UPDATE central_documents SET document_json=? WHERE collection='server_market_profile_settings'",
                               (json.dumps({"revision": 0}),))
        before = self.store.load_documents("server_market_profile_settings")
        with self.assertRaisesRegex(ValueError, "MARKET_PROFILE_SETTINGS_RECOVERY_REQUIRED"):
            self.store.load_market_profile_settings()
        with self.assertRaisesRegex(ValueError, "MARKET_PROFILE_SETTINGS_RECOVERY_REQUIRED"):
            self.choose(account["profile_id"], revision=1)
        self.assertEqual(before, self.store.load_documents("server_market_profile_settings"))

    def test_two_connections_cas_admit_only_one_role_change(self):
        profiles = [self.account()["profile_id"] for _ in range(2)]
        second = SQLiteQueryStore(self.path)
        second.initialize()
        barrier = threading.Barrier(2)
        def choose(store, profile):
            barrier.wait(timeout=3)
            try:
                return self.choose(profile, store=store)
            except ValueError as error:
                return str(error)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(choose, store, profile) for store, profile in zip((self.store, second), profiles)]
                results = [future.result(timeout=5) for future in futures]
            winner = [result for result in results if isinstance(result, dict)]
            self.assertEqual(1, len(winner))
            self.assertIn("MARKET_PROFILE_SETTINGS_REVISION_CONFLICT", results)
            self.assertEqual(winner[0], self.store.load_market_profile_settings())
        finally:
            second.close()

    def test_postgres_placeholder_uses_same_selection_and_cas_contract(self):
        account = self.account()
        class Cursor:
            def __init__(self, cursor): self.cursor = cursor
            def execute(self, sql, parameters): self.cursor.execute(sql.replace("%s", "?"), parameters)
            def fetchone(self): return self.cursor.fetchone()
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = Cursor(connection.cursor())
            selected = _save_market_profile_settings(cursor, {"market_profile_id": account["profile_id"],
                "expected_binding_revision": 1}, 0, "%s")
            self.assertEqual(selected, _load_market_profile_settings(cursor, "%s"))
        self.assertEqual(selected, self.store.load_market_profile_settings())

    def test_owner_rejects_selected_market_disable_before_vault_changes(self):
        from kiwoom_monitor.central_server.credential_store import CredentialStore
        from kiwoom_monitor.central_server.credential_runtime import CredentialRuntime
        from kiwoom_monitor.central_server.real_runtime import RealCredentialOwner
        vault = CredentialStore(Path(self.temp.name) / "secrets", self.store)
        profile_id = str(uuid.uuid4())
        vault.save("kiwoom_real", profile_id,
                   {"app_key": uuid.uuid4().hex, "secret_key": uuid.uuid4().hex}, expected_revision=0)
        account = self.account(profile=profile_id)
        self.choose(account["profile_id"])
        async def check():
            owner = RealCredentialOwner(self.store, vault, hmac_key=b"x" * 32)
            runtime = CredentialRuntime(vault, self.store)
            runtime.register("kiwoom_real", owner.hooks())
            try:
                document = await runtime.prepare("kiwoom_real", account["profile_id"], str(uuid.uuid4()), 1, {}, disabled=True)
                operation = runtime._operations[document["operation_id"]]
                await asyncio.shield(operation.task)
                self.assertEqual("MARKET_PROFILE_REQUIRED", operation.error_code)
                self.assertFalse(vault.load("kiwoom_real", account["profile_id"]).disabled)
                self.assertEqual(1, vault.load("kiwoom_real", account["profile_id"]).revision)
            finally:
                await runtime.close()
                await owner.close()
        try:
            asyncio.run(check())
        finally:
            vault.close()

    def postgres_checker_store(self):
        store = self.store
        class Cursor:
            def __init__(self, cursor): self.cursor = cursor
            def __enter__(self): return self
            def __exit__(self, *args): self.cursor.close()
            def execute(self, sql, parameters):
                if sql.startswith("SELECT pg_advisory_xact_lock"):
                    return self.cursor.execute("SELECT 1")
                return self.cursor.execute(sql.replace("%s", "?"), parameters)
            def fetchone(self): return self.cursor.fetchone()
        class Connection:
            def __init__(self, connection): self.connection = connection
            def cursor(self): return Cursor(self.connection.cursor())
            def rollback(self): self.connection.rollback()
        class Store:
            def load_market_profile_settings(self): return store.load_market_profile_settings()
            @contextmanager
            def _connect(self):
                with store._connection() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    yield Connection(connection)
        return Store()

    def test_postgres_checker_rolls_back_temporary_role_and_keeps_operational_document(self):
        from scripts.check_postgres_integration import _exercise_market_profile_transaction
        original, candidate = self.account(), self.account()
        self.choose(original["profile_id"])
        before = self.store.load_documents("server_market_profile_settings")
        checks = _exercise_market_profile_transaction(self.postgres_checker_store(), candidate["profile_id"], 1)
        self.assertEqual(4, len(checks))
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual(before, self.store.load_documents("server_market_profile_settings"))

    def test_postgres_checker_failure_also_rolls_back_temporary_role(self):
        from unittest.mock import patch
        from scripts import check_postgres_integration as checker
        original, candidate = self.account(), self.account()
        self.choose(original["profile_id"])
        before = self.store.load_documents("server_market_profile_settings")
        save = checker._save_market_profile_settings
        calls = 0
        def failing_save(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected-after-role-save")
            return save(*args)
        with patch.object(checker, "_save_market_profile_settings", side_effect=failing_save):
            with self.assertRaisesRegex(OSError, "injected-after-role-save"):
                checker._exercise_market_profile_transaction(self.postgres_checker_store(), candidate["profile_id"], 1)
        self.assertEqual(before, self.store.load_documents("server_market_profile_settings"))

    def test_authenticated_read_has_no_runtime_claim_or_content_write_bypass(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        account = self.account()
        selected = self.choose(account["profile_id"])
        app = create_app(CentralServerSettings(f"sqlite:///{self.path}", "private-token",
            autonomous_top20_enabled=False, market_event_collection_enabled=False))
        with TestClient(app) as client:
            url = "/api/v1/settings/market-profile"
            headers = {"Authorization": "Bearer private-token"}
            self.assertEqual(401, client.get(url).status_code)
            response = client.get(url, headers=headers)
            self.assertEqual(200, response.status_code)
            self.assertEqual(selected, response.json()["settings"])
            self.assertIsNone(response.json()["applied_revision"])
            self.assertEqual(422, client.put(url, headers=headers, json={}).status_code)
            self.assertEqual(503, client.put(url, headers=headers, json={"market_profile_id": "nas-real-default",
                "expected_revision": 0, "expected_binding_revision": 1}).status_code)
            self.assertEqual(404, client.post("/api/v1/content/server_market_profile_settings", headers=headers,
                json={"documents": [{"owner": "global", "key": "settings", "document": {"revision": 99}}]}).status_code)
            self.assertEqual(selected, self.store.load_market_profile_settings())
            with self.store._connection() as connection:
                connection.execute("UPDATE central_documents SET document_json=? WHERE collection='server_market_profile_settings'",
                                   (json.dumps({"revision": 0}),))
            error = client.get(url, headers=headers)
            self.assertEqual(409, error.status_code)
            self.assertEqual("MARKET_PROFILE_SETTINGS_RECOVERY_REQUIRED", error.json()["detail"])
