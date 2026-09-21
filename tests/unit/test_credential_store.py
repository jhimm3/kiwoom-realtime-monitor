from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.credential_store import (
    CredentialStore, CredentialStoreError, compose_credential_settings,
)
from kiwoom_monitor.central_server.database import (
    SQLiteQueryStore, _credential_activation_row, _finalize_credential_activation,
)
from kiwoom_monitor.central_server.central_schema import central_schema_migrations
from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigrationRunner


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = SQLiteQueryStore(self.root / "central.sqlite")
        self.database.initialize()
        self.vault = CredentialStore(self.root / "secrets", self.database)
        self.credentials = {"app_key": uuid.uuid4().hex, "secret_key": uuid.uuid4().hex}

    def tearDown(self):
        self.vault.close()
        self.database.close()
        self.temporary.cleanup()

    def test_roundtrip_revision_previous_nonce_and_no_plaintext(self):
        one = self.vault.save("kiwoom_mock", "nas-mock-default", self.credentials, expected_revision=0)
        first = json.loads((self.root / "secrets/kiwoom_mock--nas-mock-default.json").read_bytes())
        two = self.vault.save("kiwoom_mock", "nas-mock-default", self.credentials, expected_revision=1)
        self.assertEqual((1, 2), (one.revision, two.revision))
        self.assertEqual(self.credentials, self.vault.load("kiwoom_mock", "nas-mock-default").payload["credentials"])
        raw = (self.root / "secrets/kiwoom_mock--nas-mock-default.json").read_text()
        second = json.loads(raw)
        self.assertNotEqual(first["active"]["nonce"], second["active"]["nonce"])
        self.assertEqual(1, second["previous"]["revision"])
        for secret in self.credentials.values():
            self.assertNotIn(secret, raw)
            self.assertNotIn(secret, repr(two))
            self.assertNotIn(secret, str(self.database.load_documents("credential_vault_state")))
        self.vault.close()
        self.vault = CredentialStore(self.root / "secrets", self.database)
        self.assertEqual(2, self.vault.load("kiwoom_mock", "nas-mock-default").revision)

    def test_deleted_file_does_not_import_env(self):
        self.vault.import_initial("kiwoom_mock", "nas-mock-default", self.credentials)
        (self.root / "secrets/kiwoom_mock--nas-mock-default.json").unlink()
        with self.assertRaisesRegex(CredentialStoreError, "RECOVERY_REQUIRED"):
            self.vault.import_initial("kiwoom_mock", "nas-mock-default", self.credentials)

    def test_disabled_overrides_env_and_blank_pair_rejected(self):
        self.vault.save("kiwoom_mock", "nas-mock-default", {}, expected_revision=0, disabled=True)
        self.assertTrue(self.vault.import_initial("kiwoom_mock", "nas-mock-default", self.credentials).disabled)
        with self.assertRaisesRegex(CredentialStoreError, "INCOMPLETE_CREDENTIALS"):
            self.vault.save("kiwoom_mock", "other", {"app_key": "", "secret_key": ""}, expected_revision=0)

    def test_tampering_copy_and_master_loss_fail_closed(self):
        self.vault.save("kiwoom_mock", "first", self.credentials, expected_revision=0)
        path = self.root / "secrets/kiwoom_mock--first.json"
        original = path.read_bytes()
        envelope = json.loads(original)
        envelope["profile_id"] = "second"
        target = self.root / "secrets/kiwoom_mock--second.json"
        target.write_text(json.dumps(envelope))
        target.chmod(0o600)
        with self.assertRaisesRegex(CredentialStoreError, "RECOVERY_REQUIRED"):
            self.vault.load("kiwoom_mock", "second")
        envelope = json.loads(original)
        envelope["active"]["ciphertext"] = envelope["active"]["ciphertext"][::-1]
        path.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(CredentialStoreError, "RECOVERY_REQUIRED"):
            self.vault.load("kiwoom_mock", "first")
        path.write_bytes(original)
        (self.root / "secrets/master.key").unlink()
        with self.assertRaisesRegex(CredentialStoreError, "RECOVERY_REQUIRED"):
            self.vault.load("kiwoom_mock", "first")
        with self.assertRaisesRegex(CredentialStoreError, "RECOVERY_REQUIRED"):
            self.vault.save("dart", "new", {"api_key": uuid.uuid4().hex}, expected_revision=0)
        self.assertFalse((self.root / "secrets/master.key").exists())

    def test_replace_failure_preserves_previous_and_first_import_marker(self):
        self.vault.save("kiwoom_mock", "first", self.credentials, expected_revision=0)
        with patch("kiwoom_monitor.central_server.credential_store.os.replace", side_effect=OSError):
            with self.assertRaises(OSError):
                self.vault.save("kiwoom_mock", "first", self.credentials, expected_revision=1)
            with self.assertRaises(OSError):
                self.vault.save("kiwoom_mock", "new", self.credentials, expected_revision=0)
        self.assertEqual(1, self.vault.load("kiwoom_mock", "first").revision)
        with self.assertRaisesRegex(CredentialStoreError, "RECOVERY_REQUIRED"):
            self.vault.load("kiwoom_mock", "new")
        self.assertFalse(list((self.root / "secrets").glob(".pending-*")))

    def test_db_fence_failure_after_file_commit_reconciles(self):
        self.vault.save("kiwoom_mock", "first", self.credentials, expected_revision=0)
        with patch.object(self.database, "upsert_documents", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.vault.save("kiwoom_mock", "first", self.credentials, expected_revision=1)
        self.assertEqual(2, self.vault.load("kiwoom_mock", "first").revision)
        self.assertEqual(2, self.database.load_documents("credential_vault_state", "kiwoom_mock--first")[0]["document"]["revision"])

    def test_stale_file_and_concurrent_writes(self):
        self.vault.save("kiwoom_mock", "first", self.credentials, expected_revision=0)
        path = self.root / "secrets/kiwoom_mock--first.json"
        old = path.read_bytes()
        def save():
            try:
                self.vault.save("kiwoom_mock", "first", self.credentials, expected_revision=1)
                return "saved"
            except CredentialStoreError as error:
                return str(error)
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(["saved", "CREDENTIAL_REVISION_CONFLICT"], list(pool.map(lambda _: save(), range(2))))
        path.write_bytes(old)
        with self.assertRaisesRegex(CredentialStoreError, "RECOVERY_REQUIRED"):
            self.vault.load("kiwoom_mock", "first")

    def test_second_owner_and_unsafe_profile_rejected(self):
        with self.assertRaisesRegex(CredentialStoreError, "VAULT_ALREADY_OWNED"):
            CredentialStore(self.root / "secrets", self.database)
        with self.assertRaisesRegex(CredentialStoreError, "INVALID_CREDENTIAL_PROFILE"):
            self.vault.load("dart", "../escape")

    @unittest.skipIf(os.name == "nt", "POSIX permissions checked on Linux")
    def test_permissions_and_symlink_rejected(self):
        self.vault.save("kiwoom_mock", "first", self.credentials, expected_revision=0)
        self.assertEqual(0o700, (self.root / "secrets").stat().st_mode & 0o777)
        master = self.root / "secrets/master.key"
        self.assertEqual(0o600, master.stat().st_mode & 0o777)
        master.chmod(0o644)
        with self.assertRaises(CredentialStoreError):
            self.vault.load("kiwoom_mock", "first")
        master.chmod(0o600)
        master.unlink()
        master.symlink_to(self.root / "other")
        with self.assertRaises(CredentialStoreError):
            self.vault.load("kiwoom_mock", "first")

    def test_composition_uses_vault_and_isolates_corrupted_provider(self):
        self.vault.save("kiwoom_mock", "nas-mock-default", self.credentials, expected_revision=0)
        self.vault.save("naver", "nas-naver-default", {"client_id": uuid.uuid4().hex, "client_secret": uuid.uuid4().hex}, expected_revision=0)
        (self.root / "secrets/naver--nas-naver-default.json").write_text("corrupt")
        settings = CentralServerSettings("sqlite://", uuid.uuid4().hex)
        active, statuses = compose_credential_settings(settings, self.vault, self.database)
        self.assertEqual(self.credentials["app_key"], active.kiwoom_mock_app_key)
        self.assertEqual("ACTIVE", statuses["kiwoom_mock"])
        self.assertEqual("RECOVERY_REQUIRED", statuses["naver"])
        self.assertEqual("", active.naver_news_client_id)

    def _activation(self, profile="first"):
        account_ref = self.database.register_account_identity({
            "broker": "kiwoom", "environment": "mock", "identity_fingerprint": uuid.uuid4().hex + uuid.uuid4().hex,
            "created_at": "2026-09-15T12:00:00+09:00",
        })
        self.vault.save("kiwoom_mock", "digest", self.credentials, expected_revision=0)
        return {"operation_id": uuid.uuid4().hex, "provider": "kiwoom_mock", "credential_revision": 1,
                "request_id": uuid.uuid4().hex, "request_digest": self.vault.request_digest(self.credentials),
                "profile_id": profile, "environment": "mock", "account_ref": account_ref,
                "run_id": uuid.uuid4().hex, "committed_at": "2026-09-15T12:00:00+09:00"}

    def test_activation_idempotency_account_change_and_no_secrets(self):
        value = self._activation()
        one = self.database.finalize_credential_activation(value)
        two = self.database.finalize_credential_activation(value)
        self.assertEqual(one, two)
        self.assertEqual(1, len(self.database.load_account_bindings()))
        new_account = self.database.register_account_identity({
            "broker": "kiwoom", "environment": "mock", "identity_fingerprint": uuid.uuid4().hex + uuid.uuid4().hex,
            "created_at": "2026-09-15T12:00:00+09:00",
        })
        changed = {**value, "operation_id": uuid.uuid4().hex, "request_id": uuid.uuid4().hex,
                   "credential_revision": 2, "account_ref": new_account}
        with self.assertRaisesRegex(ValueError, "ACCOUNT_CHANGED"):
            self.database.finalize_credential_activation(changed)
        self.assertEqual(1, len(self.database.load_account_bindings()))
        with self.assertRaises(ValueError):
            self.database.finalize_credential_activation({**value, "secret_key": self.credentials["secret_key"]})
        for secret in self.credentials.values():
            self.assertNotIn(secret, str(one))

    def test_postgres_activation_timestamp_is_normalized_to_iso_text(self):
        committed_at = datetime.fromisoformat("2026-09-15T12:00:00+09:00")
        row = (
            "operation", "kiwoom_mock", 1, "request", "a" * 64,
            "profile", None, None, None, committed_at,
        )
        self.assertEqual(
            "2026-09-15T12:00:00+09:00",
            _credential_activation_row(row)["committed_at"],
        )

    def test_activation_failure_rolls_back_binding_and_profile(self):
        value = self._activation()
        with self.database._connection() as connection:
            connection.execute("CREATE TRIGGER fail_activation BEFORE INSERT ON central_credential_activations "
                               "BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.finalize_credential_activation(value)
        self.assertEqual([], self.database.load_account_bindings())
        self.assertEqual([], self.database.load_credential_activations(value["profile_id"]))
        with self.database._connection() as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM central_credential_profiles").fetchone()[0])
            connection.execute("DROP TRIGGER fail_activation")
        self.assertEqual(1, self.database.finalize_credential_activation(value)["binding_revision"])

    def test_prepared_identity_does_not_publish_binding(self):
        from types import SimpleNamespace
        from datetime import datetime
        from kiwoom_monitor.application.account_identity import prepare_verified_account_identity
        from kiwoom_monitor.domain.order_contract import AccountEnvironment
        identity = SimpleNamespace(broker="kiwoom", environment=AccountEnvironment.MOCK,
                                   identity_fingerprint=uuid.uuid4().hex + uuid.uuid4().hex,
                                   verified_at=datetime.fromisoformat("2026-09-15T12:00:00+09:00"),
                                   verification_method="ka00001")
        prepared = prepare_verified_account_identity(identity, self.database, credential_profile_id="candidate")
        self.assertEqual([], self.database.load_account_bindings())
        self.assertEqual(prepared, prepare_verified_account_identity(identity, self.database, credential_profile_id="candidate"))

    def test_legacy_main_mock_keys_remain_separate(self):
        main_keys = {"app_key": uuid.uuid4().hex, "secret_key": uuid.uuid4().hex}
        settings = CentralServerSettings("sqlite://", uuid.uuid4().hex, kiwoom_environment="mock",
                                        kiwoom_app_key=main_keys["app_key"], kiwoom_secret_key=main_keys["secret_key"],
                                        kiwoom_mock_app_key=self.credentials["app_key"],
                                        kiwoom_mock_secret_key=self.credentials["secret_key"])
        active, _ = compose_credential_settings(settings, self.vault, self.database)
        self.assertEqual(main_keys["app_key"], active.kiwoom_app_key)
        self.assertEqual(self.credentials["app_key"], active.kiwoom_mock_app_key)
        empty = replace(settings, kiwoom_app_key="", kiwoom_secret_key="", kiwoom_mock_app_key="", kiwoom_mock_secret_key="")
        active, _ = compose_credential_settings(empty, self.vault, self.database)
        self.assertEqual(main_keys["app_key"], active.kiwoom_app_key)
        self.assertEqual(self.credentials["app_key"], active.kiwoom_mock_app_key)

    def test_environment_validation_defers_mock_credentials_to_vault(self):
        with patch.dict(os.environ, {"MONITOR_SERVER_ACCESS_TOKEN": uuid.uuid4().hex,
                                     "KIWOOM_SERVER_SECRET_DIR": str(self.root / "secrets"),
                                     "MOCK_ACCOUNT_MONITOR_ENABLED": "1"}, clear=True):
            settings = CentralServerSettings.from_environment()
        self.assertEqual(str(self.root / "secrets"), settings.credential_directory)
        active, _ = compose_credential_settings(settings, self.vault, self.database)
        self.assertFalse(active.mock_account_monitor_enabled)  # No verified activation yet.

    def test_server_lifespan_releases_vault_and_second_worker_rejected(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        directory = self.root / "app-secrets"
        settings = CentralServerSettings(f"sqlite:///{self.root / 'app.sqlite'}", uuid.uuid4().hex,
                                        credential_directory=str(directory), autonomous_top20_enabled=False,
                                        market_event_collection_enabled=False)
        app = create_app(settings)
        with self.assertRaisesRegex(RuntimeError, "VAULT_ALREADY_OWNED"):
            create_app(settings)
        with TestClient(app) as client:
            self.assertEqual(200, client.get("/health").status_code)
            self.assertEqual("UNCONFIGURED", app.state.credential_statuses["naver"])
        vault = CredentialStore(directory, self.database)
        vault.close()

    def test_server_startup_failure_releases_vault(self):
        from fastapi.testclient import TestClient
        from unittest.mock import AsyncMock
        from kiwoom_monitor.central_server.app import create_app
        directory = self.root / "startup-secrets"
        settings = CentralServerSettings(f"sqlite:///{self.root / 'startup.sqlite'}", uuid.uuid4().hex,
                                        credential_directory=str(directory), autonomous_top20_enabled=False,
                                        market_event_collection_enabled=False,
                                        kiwoom_app_key=self.credentials["app_key"], kiwoom_secret_key=self.credentials["secret_key"])
        app = create_app(settings)
        with patch("kiwoom_monitor.central_server.app.CentralRestBroker.start", new=AsyncMock(side_effect=RuntimeError("injected"))):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                with TestClient(app):
                    pass
        vault = CredentialStore(directory, self.database)
        vault.close()

    def test_v19_migration_failure_preserves_v18(self):
        from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigration
        with closing(sqlite3.connect(":memory:")) as connection:
            migrations = central_schema_migrations()
            with connection:
                CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply(migrations[:18])
            broken = CentralSchemaMigration(19, migrations[-1].name,
                                            (*migrations[-1].sqlite_statements, "INVALID SQL"), ())
            with self.assertRaises(sqlite3.OperationalError):
                with connection:
                    CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply((*migrations[:18], broken))
            self.assertEqual(18, connection.execute("SELECT MAX(version) FROM central_schema_migrations").fetchone()[0])
            self.assertEqual([], connection.execute("SELECT name FROM sqlite_master WHERE name='central_credential_profiles'").fetchall())

    def test_pending_activation_restart_and_empty_env_scope(self):
        value = self._activation("nas-mock-default")
        metadata = {key: item for key, item in value.items() if key not in {"provider", "profile_id", "credential_revision"}}
        self.vault.save("kiwoom_mock", "nas-mock-default", self.credentials, expected_revision=0, activation=metadata)
        settings = CentralServerSettings("sqlite://", uuid.uuid4().hex, mock_account_monitor_enabled=True)
        active, statuses = compose_credential_settings(settings, self.vault, self.database)
        self.assertEqual(value["account_ref"], active.mock_account_ref)
        self.assertEqual(value["run_id"], active.mock_execution_run_id)
        self.assertTrue(active.mock_account_monitor_enabled)
        compose_credential_settings(settings, self.vault, self.database)
        self.assertEqual(1, len(self.database.load_account_bindings()))

    def test_v18_fixture_upgrade_preserves_rows(self):
        path = self.root / "old.sqlite"
        with closing(sqlite3.connect(path)) as connection, connection:
            CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply(central_schema_migrations()[:18])
            connection.execute("INSERT INTO central_documents VALUES(?,?,?,?,?)", ("old", "owner", "key", 1, '{"kept":true}'))
        old = SQLiteQueryStore(path)
        old.initialize()
        self.assertEqual({"kept": True}, old.load_documents("old")[0]["document"])
        self.assertEqual([], old.load_credential_activations("missing"))
        old.close()

    def test_nonaccount_activation_and_sql_dialect_parameter_parity(self):
        value = {"operation_id": uuid.uuid4().hex, "provider": "dart", "credential_revision": 1,
                 "request_id": uuid.uuid4().hex, "request_digest": uuid.uuid4().hex + uuid.uuid4().hex,
                 "profile_id": "nas-dart-default", "committed_at": "2026-09-15T12:00:00+09:00"}
        class PostgresParameterCursor:
            def __init__(self, cursor): self.cursor = cursor
            def execute(self, sql, parameters): self.cursor.execute(sql.replace("%s", "?"), parameters)
            def fetchone(self): return self.cursor.fetchone()
            def fetchall(self): return self.cursor.fetchall()
        with self.database._lock, self.database._connection() as connection:
            result = _finalize_credential_activation(PostgresParameterCursor(connection.cursor()), value, "%s")
        self.assertEqual(result, self.database.finalize_credential_activation(value))
        self.assertIsNone(result["account_ref"])
        self.assertIsNone(result["binding_revision"])


if __name__ == "__main__":
    unittest.main()
