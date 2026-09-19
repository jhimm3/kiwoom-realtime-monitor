from __future__ import annotations

import json
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from kiwoom_monitor.central_server.database import (
    SQLiteQueryStore, _load_account_settings, _save_account_settings,
)


class AccountRuntimeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "central.sqlite"
        self.store = SQLiteQueryStore(self.path); self.store.initialize()
        self.scope = self.identity()
        self.profile = str(uuid.uuid4())

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def identity(self, environment="mock"):
        account_ref = self.store.register_account_identity({
            "broker": "kiwoom", "environment": environment,
            "identity_fingerprint": uuid.uuid4().hex + uuid.uuid4().hex,
            "created_at": "2026-09-15T12:00:00+09:00",
        })
        return {"broker": "kiwoom", "environment": environment, "account_ref": account_ref}

    def activation(self, profile=None, scope=None, revision=1):
        scope = scope or self.scope
        return {
            "operation_id": str(uuid.uuid4()), "provider": f"kiwoom_{scope['environment']}",
            "profile_id": profile or self.profile, "credential_revision": revision,
            "request_id": str(uuid.uuid4()), "request_digest": uuid.uuid4().hex + uuid.uuid4().hex,
            "environment": scope["environment"], "account_ref": scope["account_ref"],
            "run_id": str(uuid.uuid4()), "committed_at": "2026-09-15T12:00:00+09:00",
        }

    def setting(self, **changes):
        return {"scope": self.scope, "active_profile_id": self.profile,
                "monitor_enabled": True, "mock_order_enabled": False, **changes}

    def bind_profile(self, profile, scope=None, provider=None):
        scope = scope or self.scope
        self.store.register_credential_profile(
            provider or f"kiwoom_{scope['environment']}", profile, "2026-09-15T12:00:00+09:00",
        )
        self.store.append_account_binding({
            "credential_profile_id": profile, **scope, "verified_at": "2026-09-15T12:00:00+09:00",
            "verification_method": "ka00001",
        })

    def test_verified_account_default_is_off_and_read_does_not_persist(self):
        document = self.store.load_account_settings(self.scope)
        self.assertEqual(0, document["revision"])
        self.assertFalse(document["monitor_enabled"])
        self.assertFalse(document["mock_order_enabled"])
        self.assertIsNone(document["active_profile_id"])
        self.assertEqual([], self.store.load_documents("server_account_settings"))

    def test_mock_activation_claims_profile_with_orders_off_in_same_transaction(self):
        self.store.finalize_credential_activation(self.activation())
        document = self.store.load_account_settings(self.scope)
        self.assertEqual(1, document["revision"])
        self.assertTrue(document["monitor_enabled"])
        self.assertFalse(document["mock_order_enabled"])
        self.assertEqual(self.profile, document["active_profile_id"])

    def test_real_activation_claims_profile_and_rotation_preserves_preferences(self):
        self.scope = self.identity("real")
        first = self.store.finalize_credential_activation(self.activation())
        document = self.store.load_account_settings(self.scope)
        self.assertEqual(self.profile, document["active_profile_id"])
        self.assertEqual(1, document["revision"])
        self.assertTrue(document["monitor_enabled"])
        self.assertFalse(document["mock_order_enabled"])
        saved = self.store.save_account_settings(self.setting(monitor_enabled=False), expected_revision=1)
        before = self.store.load_documents("server_account_settings")
        rotated = self.store.finalize_credential_activation(self.activation(revision=2))
        self.assertEqual(first["binding_revision"] + 1, rotated["binding_revision"])
        self.assertEqual(saved, self.store.load_account_settings(self.scope))
        self.assertEqual(before, self.store.load_documents("server_account_settings"))

    def test_real_duplicate_activation_rolls_back_draft_binding_and_receipt(self):
        self.scope = self.identity("real")
        self.store.finalize_credential_activation(self.activation())
        other = self.store.create_credential_profile("kiwoom_real", str(uuid.uuid4()), "second", "digest")
        bindings = self.store.load_account_bindings()
        settings = self.store.load_account_settings(self.scope)
        with self.assertRaisesRegex(ValueError, "ACCOUNT_PROFILE_CONFLICT"):
            self.store.finalize_credential_activation(self.activation(profile=other["profile_id"]))
        self.assertEqual([], self.store.load_credential_activations(other["profile_id"]))
        self.assertEqual(bindings, self.store.load_account_bindings())
        self.assertEqual(settings, self.store.load_account_settings(self.scope))
        self.assertEqual("draft", next(p for p in self.store.list_credential_profiles()
                                      if p["profile_id"] == other["profile_id"])["lifecycle_state"])

    def test_real_disable_retains_history_and_replays_do_not_replace_new_profile(self):
        self.scope = self.identity("real")
        enabled = self.activation()
        self.store.finalize_credential_activation(enabled)
        bindings = self.store.load_account_bindings()
        disabled = {**self.activation(revision=2), "disabled": True}
        receipt = self.store.finalize_credential_activation(disabled)
        self.assertEqual(bindings, self.store.load_account_bindings())
        configuration = self.store.load_account_settings(self.scope)
        self.assertIsNone(configuration["active_profile_id"])
        self.assertFalse(configuration["monitor_enabled"])
        self.assertFalse(configuration["mock_order_enabled"])
        other = str(uuid.uuid4())
        self.store.finalize_credential_activation(self.activation(profile=other))
        changed = self.store.load_account_settings(self.scope)
        self.assertEqual(other, changed["active_profile_id"])
        self.assertEqual(receipt, self.store.finalize_credential_activation(disabled))
        self.store.finalize_credential_activation(enabled)
        self.assertEqual(changed, self.store.load_account_settings(self.scope))

    def test_real_and_mock_activations_and_second_real_account_are_independent(self):
        self.store.finalize_credential_activation(self.activation())
        mock_settings = self.store.save_account_settings(self.setting(mock_order_enabled=True), expected_revision=1)
        real_scope = self.identity("real")
        real_profile = str(uuid.uuid4())
        self.store.finalize_credential_activation(self.activation(profile=real_profile, scope=real_scope))
        second_scope = self.identity("real")
        second_profile = str(uuid.uuid4())
        self.store.finalize_credential_activation(self.activation(profile=second_profile, scope=second_scope))
        self.store.save_account_settings(self.setting(scope=real_scope, active_profile_id=real_profile,
                                                     monitor_enabled=False), expected_revision=1)
        second = self.store.load_account_settings(second_scope)
        self.assertEqual(second_profile, second["active_profile_id"])
        self.assertTrue(second["monitor_enabled"])
        self.assertFalse(second["mock_order_enabled"])
        self.assertEqual(mock_settings, self.store.load_account_settings(self.scope))

    def test_competing_real_profile_activations_admit_only_one_connection(self):
        self.scope = self.identity("real")
        second = SQLiteQueryStore(self.path); second.initialize()
        barrier = threading.Barrier(2)
        profiles = (self.profile, str(uuid.uuid4()))
        def activate(store, profile):
            barrier.wait(timeout=3)
            try:
                return store.finalize_credential_activation(self.activation(profile=profile))
            except ValueError as error:
                return str(error)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(activate, store, profile)
                           for store, profile in zip((self.store, second), profiles)]
                results = [future.result(timeout=5) for future in futures]
            admitted = [result for result in results if isinstance(result, dict)]
            self.assertEqual(1, len(admitted))
            self.assertIn("ACCOUNT_PROFILE_CONFLICT", results)
            winner = admitted[0]["profile_id"]
            self.assertEqual(winner, self.store.load_account_settings(self.scope)["active_profile_id"])
            self.assertEqual([winner], [b["credential_profile_id"] for b in self.store.load_account_bindings()])
            loser = next(profile for profile in profiles if profile != winner)
            self.assertEqual([], self.store.load_credential_activations(loser))
            self.assertFalse(any(p["profile_id"] == loser for p in self.store.list_credential_profiles()))
        finally:
            second.close()

    def test_real_corrupt_preferences_roll_back_rotation_without_reset(self):
        self.scope = self.identity("real")
        self.store.finalize_credential_activation(self.activation())
        bindings = self.store.load_account_bindings()
        with self.store._connection() as connection:
            connection.execute("UPDATE central_documents SET document_json=? WHERE collection='server_account_settings'",
                               (json.dumps({"revision": 0}),))
        with self.assertRaisesRegex(ValueError, "ACCOUNT_SETTINGS_RECOVERY_REQUIRED"):
            self.store.finalize_credential_activation(self.activation(revision=2))
        self.assertEqual(bindings, self.store.load_account_bindings())
        self.assertEqual(1, len(self.store.load_credential_activations(self.profile)))

    def test_legacy_real_receipt_replay_initializes_missing_preferences_once(self):
        self.scope = self.identity("real")
        activation = self.activation()
        receipt = self.store.finalize_credential_activation(activation)
        # R1/R6a receipts did not create real account settings. Emulate that persisted state.
        with self.store._connection() as connection:
            connection.execute("DELETE FROM central_documents WHERE collection='server_account_settings'")
        bindings = self.store.load_account_bindings()
        self.assertEqual(receipt, self.store.finalize_credential_activation(activation))
        settings = self.store.load_account_settings(self.scope)
        self.assertEqual(self.profile, settings["active_profile_id"])
        self.assertEqual(1, settings["revision"])
        before = self.store.load_documents("server_account_settings")
        self.store.finalize_credential_activation(activation)
        self.assertEqual(before, self.store.load_documents("server_account_settings"))
        self.assertEqual(bindings, self.store.load_account_bindings())

    def test_postgres_dialect_real_activation_conflict_and_disable_share_transaction(self):
        from kiwoom_monitor.central_server.database import _finalize_credential_activation
        self.scope = self.identity("real")
        class Cursor:
            def __init__(self, cursor): self.cursor = cursor
            def execute(self, sql, parameters): self.cursor.execute(sql.replace("%s", "?"), parameters)
            def fetchone(self): return self.cursor.fetchone()
            def fetchall(self): return self.cursor.fetchall()
        def finalize(activation):
            with self.store._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                return _finalize_credential_activation(Cursor(connection.cursor()), activation, "%s")
        activation = self.activation()
        first = finalize(activation)
        self.assertEqual(self.profile, self.store.load_account_settings(self.scope)["active_profile_id"])
        other = str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, "ACCOUNT_PROFILE_CONFLICT"):
            finalize(self.activation(profile=other))
        self.assertEqual([], self.store.load_credential_activations(other))
        self.assertEqual(first, finalize(activation))
        bindings = self.store.load_account_bindings()
        disabled = finalize({**self.activation(revision=2), "disabled": True})
        self.assertEqual(first["binding_revision"], disabled["binding_revision"])
        self.assertEqual(bindings, self.store.load_account_bindings())
        self.assertIsNone(self.store.load_account_settings(self.scope)["active_profile_id"])

    def test_same_profile_rotation_preserves_toggles_and_settings_revision(self):
        self.store.finalize_credential_activation(self.activation())
        saved = self.store.save_account_settings(self.setting(mock_order_enabled=True), expected_revision=1)
        self.store.finalize_credential_activation(self.activation(revision=2))
        self.assertEqual(saved, self.store.load_account_settings(self.scope))

    def test_duplicate_profile_activation_rolls_back_binding_profile_and_ledger(self):
        self.store.finalize_credential_activation(self.activation())
        other = str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, "ACCOUNT_PROFILE_CONFLICT"):
            self.store.finalize_credential_activation(self.activation(profile=other))
        self.assertEqual([], self.store.load_credential_activations(other))
        self.assertFalse(any(p["profile_id"] == other for p in self.store.list_credential_profiles()))
        self.assertEqual([self.profile], [b["credential_profile_id"] for b in self.store.load_account_bindings()])

    def test_replayed_activation_does_not_restore_disabled_configuration(self):
        activation = self.activation(); self.store.finalize_credential_activation(activation)
        disabled = self.store.save_account_settings(
            self.setting(active_profile_id=None, monitor_enabled=False), expected_revision=1,
        )
        self.store.finalize_credential_activation(activation)
        self.assertEqual(disabled, self.store.load_account_settings(self.scope))

    def test_stale_revision_is_rejected_and_unchanged_save_keeps_timestamp(self):
        self.store.finalize_credential_activation(self.activation())
        before = self.store.load_documents("server_account_settings")
        self.store.save_account_settings(self.setting(), expected_revision=1)
        self.assertEqual(before, self.store.load_documents("server_account_settings"))
        with self.assertRaisesRegex(ValueError, "ACCOUNT_SETTINGS_REVISION_CONFLICT"):
            self.store.save_account_settings(self.setting(), expected_revision=0)

    def test_scope_requires_verified_identity_and_environment_match(self):
        for scope in ({**self.scope, "account_ref": str(uuid.uuid4())},
                      {**self.scope, "environment": "real"}):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, "ACCOUNT_IDENTITY_UNVERIFIED"):
                self.store.load_account_settings(scope)
        for scope in ({**self.scope, "environment": []}, {**self.scope, "extra": "secret"},
                      {**self.scope, "account_ref": "1234567890"}):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, "ACCOUNT_SETTINGS_SCOPE_INVALID"):
                self.store.load_account_settings(scope)

    def test_profile_requires_correct_provider_binding_and_active_lifecycle(self):
        other_scope = self.identity(); other = str(uuid.uuid4()); self.bind_profile(other, other_scope)
        with self.assertRaisesRegex(ValueError, "ACCOUNT_PROFILE_SCOPE_MISMATCH"):
            self.store.save_account_settings(self.setting(active_profile_id=other), expected_revision=0)
        for provider in ("openai", "kiwoom_real"):
            profile = str(uuid.uuid4()); self.bind_profile(profile, provider=provider)
            with self.assertRaisesRegex(ValueError, "ACCOUNT_PROFILE_UNAVAILABLE"):
                self.store.save_account_settings(self.setting(active_profile_id=profile), expected_revision=0)
        draft = self.store.create_credential_profile("kiwoom_mock", str(uuid.uuid4()), "draft", "digest")
        with self.assertRaisesRegex(ValueError, "ACCOUNT_PROFILE_UNAVAILABLE"):
            self.store.save_account_settings(self.setting(active_profile_id=draft["profile_id"]), expected_revision=0)

    def test_invalid_toggles_extra_fields_and_bool_revision_are_rejected(self):
        self.bind_profile(self.profile)
        for document, revision in (
            (self.setting(mock_order_enabled=True, monitor_enabled=False), 0),
            (self.setting(active_profile_id=None), 0), (self.setting(monitor_enabled=1), 0),
            (self.setting(secret_key="must-not-save"), 0), (self.setting(), True),
        ):
            with self.subTest(document=document), self.assertRaisesRegex(ValueError, "ACCOUNT_SETTINGS_INVALID"):
                self.store.save_account_settings(document, expected_revision=revision)
        self.assertEqual([], self.store.load_documents("server_account_settings"))

    def test_real_account_never_accepts_mock_order_toggle(self):
        scope = self.identity("real"); self.bind_profile(self.profile, scope)
        with self.assertRaisesRegex(ValueError, "ACCOUNT_SETTINGS_INVALID"):
            self.store.save_account_settings(self.setting(scope=scope, mock_order_enabled=True), expected_revision=0)

    def test_second_account_configuration_is_independent(self):
        self.store.finalize_credential_activation(self.activation())
        scope = self.identity(); profile = str(uuid.uuid4())
        self.store.finalize_credential_activation(self.activation(profile=profile, scope=scope))
        self.store.save_account_settings(self.setting(mock_order_enabled=True), expected_revision=1)
        self.assertFalse(self.store.load_account_settings(scope)["mock_order_enabled"])
        self.assertEqual(profile, self.store.load_account_settings(scope)["active_profile_id"])

    def test_corrupt_saved_document_is_fenced_instead_of_reset(self):
        self.store.finalize_credential_activation(self.activation())
        with self.store._connection() as connection:
            connection.execute("UPDATE central_documents SET document_json=? WHERE collection='server_account_settings'",
                               (json.dumps({"revision": 0}),))
        with self.assertRaisesRegex(ValueError, "ACCOUNT_SETTINGS_RECOVERY_REQUIRED"):
            self.store.load_account_settings(self.scope)
        with self.assertRaisesRegex(ValueError, "ACCOUNT_SETTINGS_RECOVERY_REQUIRED"):
            self.store.finalize_credential_activation(self.activation(revision=2))
        self.assertEqual(1, len(self.store.load_credential_activations(self.profile)))

    def test_two_connections_cas_allows_only_one_competing_update(self):
        self.store.finalize_credential_activation(self.activation())
        second = SQLiteQueryStore(self.path); second.initialize()
        barrier = threading.Barrier(2)
        def update(store, value):
            barrier.wait(timeout=3)
            try: return store.save_account_settings(value, expected_revision=1)
            except ValueError as error: return str(error)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(update, self.store, self.setting(mock_order_enabled=True)),
                           pool.submit(update, second, self.setting(monitor_enabled=False))]
                results = [future.result(timeout=5) for future in futures]
            self.assertEqual(1, sum(isinstance(value, dict) for value in results))
            self.assertIn("ACCOUNT_SETTINGS_REVISION_CONFLICT", results)
            self.assertEqual(2, self.store.load_account_settings(self.scope)["revision"])
        finally:
            second.close()

    def test_two_connections_duplicate_account_activation_has_one_winner(self):
        second = SQLiteQueryStore(self.path); second.initialize()
        barrier = threading.Barrier(2)
        def activate(store, value):
            barrier.wait(timeout=3)
            try: return store.finalize_credential_activation(value)
            except ValueError as error: return str(error)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [future.result(timeout=5) for future in (
                    pool.submit(activate, self.store, self.activation()),
                    pool.submit(activate, second, self.activation(profile=str(uuid.uuid4()))),
                )]
            self.assertEqual(1, sum(isinstance(value, dict) for value in results))
            self.assertIn("ACCOUNT_PROFILE_CONFLICT", results)
            self.assertEqual(1, len(self.store.load_account_bindings()))
        finally:
            second.close()

    def test_postgres_parameter_dialect_uses_same_settings_validation_and_cas(self):
        self.bind_profile(self.profile)
        class Cursor:
            def __init__(self, cursor): self.cursor = cursor
            def execute(self, sql, parameters): self.cursor.execute(sql.replace("%s", "?"), parameters)
            def fetchone(self): return self.cursor.fetchone()
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = Cursor(connection.cursor())
            saved = _save_account_settings(cursor, self.setting(), 0, "%s")
            self.assertEqual(saved, _load_account_settings(cursor, self.scope, "%s"))
        self.assertEqual(saved, self.store.load_account_settings(self.scope))

    def test_disable_receipt_preserves_binding_and_replay_does_not_restore_old_settings(self):
        active = self.store.finalize_credential_activation(self.activation())
        bindings = self.store.load_account_bindings()
        disabled = {**self.activation(revision=2), "disabled": True}
        receipt = self.store.finalize_credential_activation(disabled)
        self.assertEqual(active["binding_revision"], receipt["binding_revision"])
        self.assertEqual(bindings, self.store.load_account_bindings())
        settings = self.store.load_account_settings(self.scope)
        self.assertIsNone(settings["active_profile_id"])
        self.assertFalse(settings["monitor_enabled"])
        self.assertFalse(settings["mock_order_enabled"])
        other = str(uuid.uuid4())
        self.store.finalize_credential_activation(self.activation(profile=other))
        changed = self.store.load_account_settings(self.scope)
        self.assertEqual(other, changed["active_profile_id"])
        self.assertEqual(receipt, self.store.finalize_credential_activation(disabled))
        self.assertEqual(changed, self.store.load_account_settings(self.scope))

    def test_postgres_dialect_disable_uses_existing_binding_and_atomic_settings_off(self):
        from kiwoom_monitor.central_server.database import _finalize_credential_activation
        self.store.finalize_credential_activation(self.activation())
        bindings = self.store.load_account_bindings()
        class Cursor:
            def __init__(self, cursor): self.cursor = cursor
            def execute(self, sql, parameters): self.cursor.execute(sql.replace("%s", "?"), parameters)
            def fetchone(self): return self.cursor.fetchone()
            def fetchall(self): return self.cursor.fetchall()
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipt = _finalize_credential_activation(Cursor(connection.cursor()),
                {**self.activation(revision=2), "disabled": True}, "%s")
            self.assertEqual(1, receipt["binding_revision"])
        self.assertEqual(bindings, self.store.load_account_bindings())
        self.assertIsNone(self.store.load_account_settings(self.scope)["active_profile_id"])

    def test_authenticated_api_returns_preferences_without_claiming_runtime_activation(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        self.store.finalize_credential_activation(self.activation())
        app = create_app(CentralServerSettings(
            f"sqlite:///{self.path}", "private-token", autonomous_top20_enabled=False,
            market_event_collection_enabled=False,
        ))
        url = f"/api/v1/settings/accounts/{self.scope['account_ref']}?environment=mock"
        with TestClient(app) as client:
            self.assertEqual(401, client.get(url).status_code)
            response = client.get(url, headers={"Authorization": "Bearer private-token"})
            self.assertEqual(200, response.status_code)
            self.assertEqual(self.store.load_account_settings(self.scope), response.json()["settings"])
            self.assertIsNone(response.json()["applied_revision"])
            self.assertEqual(404, client.post(
                "/api/v1/content/server_account_settings", json={"documents": [{
                    "owner": "kiwoom:mock:" + self.scope["account_ref"], "key": "settings",
                    "document": {"revision": 99},
                }]},
                headers={"Authorization": "Bearer private-token"},
            ).status_code)
            # Writes remain unimplemented until the actual drain/activation owner exists.
            self.assertEqual(405, client.put(url, json={},
                headers={"Authorization": "Bearer private-token"}).status_code)

    def test_api_rejects_unknown_raw_account_number_and_wrong_environment(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        app = create_app(CentralServerSettings(
            f"sqlite:///{self.path}", "private-token", autonomous_top20_enabled=False,
            market_event_collection_enabled=False,
        ))
        with TestClient(app) as client:
            for account_ref, environment, expected_code in (
                (str(uuid.uuid4()), "mock", 404), ("1234567890", "mock", 400),
                (self.scope["account_ref"], "real", 404),
            ):
                with self.subTest(account_ref=account_ref):
                    response = client.get(
                        f"/api/v1/settings/accounts/{account_ref}?environment={environment}",
                        headers={"Authorization": "Bearer private-token"},
                    )
                    self.assertEqual(expected_code, response.status_code)
