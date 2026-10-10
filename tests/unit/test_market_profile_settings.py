from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import unittest
import uuid
from contextlib import contextmanager
from contextlib import ExitStack, closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import hashlib
import sqlite3

import httpx

from kiwoom_monitor.central_server.database import SQLiteQueryStore, _load_market_profile_settings, _save_market_profile_settings


ACCOUNT_SETTINGS_BASELINE = Path(__file__).resolve().parents[1] / "fixtures/api_contract_baselines/account_settings_http_v1.json"


@contextmanager
def account_settings_contract_app(app_factory, database_path, mode):
    """Native settings storage; owner doubles cover HTTP delegation, not live admission."""
    from kiwoom_monitor.central_server.config import CentralServerSettings
    from kiwoom_monitor.central_server.mock_runtime import MockCredentialOwner
    from kiwoom_monitor.central_server.real_runtime import RealCredentialOwner

    class FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value

    owners = [MagicMock(spec=RealCredentialOwner), MagicMock(spec=MockCredentialOwner)]
    real, mock = owners
    for owner in owners:
        owner.start, owner.close = AsyncMock(), AsyncMock()
        owner.account_bindings.return_value = ()
        owner.errors = {}
    real.applied_market_role_revision.return_value = 7
    real.applied_settings_revision.return_value = 13
    real.account_monitor_status.return_value = {"status": "RECOVERING", "reason": "fixture-admission"}
    real.change_market_role = AsyncMock()
    mock.applied_settings_revision.return_value = 17
    store = SQLiteQueryStore(database_path)
    app_stores = []
    original_factory = app_factory.__globals__["create_query_store"]

    def owned_store(*args, **kwargs):
        result = original_factory(*args, **kwargs)
        app_stores.append(result)
        return result

    try:
        with ExitStack() as stack:
            stack.enter_context(patch("kiwoom_monitor.central_server.schema_migrations.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_credentials.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.news_credentials.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.ai_credentials.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_account_settings.time", return_value=2000.0))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_documents.time", return_value=2000.0))
            stack.enter_context(patch.dict(app_factory.__globals__, {"create_query_store": owned_store}))
            stack.enter_context(patch("kiwoom_monitor.central_server.real_runtime.RealCredentialOwner", return_value=real))
            stack.enter_context(patch("kiwoom_monitor.central_server.mock_runtime.MockCredentialOwner", return_value=mock))
            for target in ("app.CentralRestBroker", "app.CentralRealtimeCollector", "news_service.CentralNewsService",
                           "mock_automation_supervisor.MockAutomationSupervisor"):
                stack.enter_context(patch(f"kiwoom_monitor.central_server.{target}.start", new_callable=AsyncMock))
            store.initialize()
            refs = {}
            for index, environment in enumerate(("real", "mock"), 1):
                refs[environment] = store.register_account_identity({"broker": "kiwoom", "environment": environment,
                    "account_ref": str(uuid.UUID(int=index)), "identity_fingerprint": str(index) * 64,
                    "created_at": "2026-10-10T03:00:00+00:00"})
                store.finalize_credential_activation({"provider": f"kiwoom_{environment}", "environment": environment,
                    "profile_id": f"contract-{environment}", "account_ref": refs[environment],
                    "run_id": str(uuid.UUID(int=10 + index)), "operation_id": f"operation-{environment}",
                    "request_id": f"request-{environment}", "request_digest": str(index) * 64,
                    "credential_revision": 1, "committed_at": "2026-10-10T03:00:00+00:00"})
            store.save_market_profile_settings({"market_profile_id": "contract-real", "expected_binding_revision": 1},
                                              expected_revision=0)
            app = app_factory(CentralServerSettings(f"sqlite:///{database_path}", "private-token",
                credential_directory=str(database_path.parent / "vault") if mode != "absent" else "",
                kiwoom_environment="mock" if mode == "mock" else "real",
                account_identity_registry_enabled=mode != "absent", account_identity_hmac_key="contract-key" * 4,
                autonomous_top20_enabled=False, market_event_collection_enabled=False, news_history_jobs_enabled=False,
                news_naver_api_enabled=False, news_naver_stock_enabled=False, news_naver_market_enabled=False,
                news_query_set_enabled=False))
            if len(app_stores) != 1:
                raise AssertionError("expected one app-owned native store")
            yield app, store, app_stores[0], real if mode == "real" else None, mock if mode != "absent" else None, refs
        for owner in ([real, mock] if mode == "real" else [mock] if mode == "mock" else []):
            owner.start.assert_awaited_once()
            if owner.close.await_count != 1:
                raise AssertionError("app must close each owner exactly once after lifecycle cleanup repair")
    finally:
        store.close()


def capture_account_settings_http_contract(client, store, request_store, real, mock, refs, database_path):
    from kiwoom_monitor.central_server.credential_runtime import CredentialOperationError
    from kiwoom_monitor.central_server.credential_store import CredentialStoreError
    cases = []

    def stored_hash():
        with closing(sqlite3.connect(database_path)) as connection:
            return hashlib.sha256("\n".join(connection.iterdump()).encode()).hexdigest()

    def request(name, method, path, *, status=200, authenticated=True, **kwargs):
        before = stored_hash()
        for owner in (real, mock):
            if owner is not None:
                owner.method_calls.clear()
        with patch.object(request_store, "load_market_profile_settings", wraps=request_store.load_market_profile_settings) as market, \
                patch.object(request_store, "load_account_settings", wraps=request_store.load_account_settings) as account:
            response = client.request(method, path,
                headers={"Authorization": "Bearer private-token"} if authenticated else {}, **kwargs)
        if response.status_code != status:
            raise AssertionError((name, response.status_code, response.text))
        after = stored_hash()
        if before != after:
            raise AssertionError((name, "settings HTTP request changed native storage"))
        cases.append({"name": name, "status": response.status_code, "headers": dict(response.headers),
            "body": response.json(), "storage_hash": after,
            "market_reads": [(call.args, call.kwargs) for call in market.call_args_list],
            "account_reads": [(call.args, call.kwargs) for call in account.call_args_list],
            "real_calls": [(call[0], call.args, call.kwargs) for call in real.method_calls] if real is not None else [],
            "mock_calls": [(call[0], call.args, call.kwargs) for call in mock.method_calls] if mock is not None else []})

    market = "/api/v1/settings/market-profile"
    for method in ("GET", "PUT"):
        request(f"unauthorized-market-{method}", method, market, authenticated=False, status=401,
                **({"json": {}} if method == "PUT" else {}))
    request("market-persisted-and-applied", "GET", market)
    valid = {"market_profile_id": "contract-real", "expected_revision": 1, "expected_binding_revision": 1}
    for name, body in (("empty", {}), ("missing-binding", {k: v for k, v in valid.items() if k != "expected_binding_revision"}),
                       ("extra-key", {**valid, "extra": True}), ("non-object", [])):
        request(f"market-invalid-{name}", "PUT", market, status=422, json=body)
    request("market-owner-delegation", "PUT", market, status=200 if real is not None else 503, json=valid)
    if real is not None:
        for error, status in ((CredentialOperationError("MARKET_PROFILE_SETTINGS_INVALID", 409), 422),
                (CredentialOperationError("MARKET_PROFILE_SETTINGS_REVISION_CONFLICT"), 409),
                (CredentialOperationError("MARKET_ROLE_BUSY", 423), 423), (CredentialStoreError("RECOVERY_REQUIRED"), 503),
                *((ValueError(code), 409) for code in ("ACCOUNT_CONTEXT_MISMATCH", "ACCOUNT_IDENTITY_UNVERIFIED",
                    "ACCOUNT_SETTINGS_RECOVERY_REQUIRED", "MARKET_PROFILE_SETTINGS_RECOVERY_REQUIRED", "unknown-detail"))):
            real.change_market_role.side_effect = error
            request(f"market-owner-error-{str(error)}", "PUT", market, status=status, json=valid)
        real.change_market_role.side_effect = None
    for environment in ("real", "mock"):
        url = f"/api/v1/settings/accounts/{refs[environment]}"
        request(f"account-{environment}-unauthorized", "GET", url, authenticated=False, status=401,
                params={"environment": environment})
        request(f"account-{environment}-applied", "GET", url, params={"environment": environment})
        request(f"account-{environment}-explicit-broker", "GET", url,
                params={"environment": environment, "broker": "kiwoom"})
        request(f"account-{environment}-wrong-environment", "GET", url, status=404,
                params={"environment": "mock" if environment == "real" else "real"})
    url = f"/api/v1/settings/accounts/{refs['real']}"
    for name, params in (("missing-environment", {}), ("invalid-environment", {"environment": "other"}),
                          ("invalid-broker", {"environment": "real", "broker": "other"})):
        request(f"account-{name}", "GET", url, status=422, params=params)
    request("account-invalid-identity", "GET", "/api/v1/settings/accounts/not-a-uuid", status=400,
            params={"environment": "real"})
    request("account-unknown-identity", "GET", f"/api/v1/settings/accounts/{uuid.UUID(int=999)}", status=404,
            params={"environment": "real"})
    # Corrupt native documents exercise the actual reader's recovery branch.
    with store._connection() as connection:
        connection.execute("UPDATE central_documents SET document_json=? WHERE collection='server_account_settings'",
                           (json.dumps({"revision": 0}),))
    request("account-recovery-required", "GET", url, status=409, params={"environment": "real"})
    with store._connection() as connection:
        connection.execute("UPDATE central_documents SET document_json=? WHERE collection='server_market_profile_settings'",
                           (json.dumps({"revision": 0}),))
    request("market-recovery-required", "GET", market, status=409)
    return json.loads(json.dumps(cases, ensure_ascii=False))


class MarketProfileSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "central.sqlite"
        self.store = SQLiteQueryStore(self.path)
        self.store.initialize()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_account_settings_http_matches_pre_extraction_baseline(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        expected = json.loads(ACCOUNT_SETTINGS_BASELINE.read_text(encoding="utf-8"))["results"]
        for mode, baseline in zip(("absent", "mock", "real"), expected, strict=True):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "settings.sqlite3"
                with account_settings_contract_app(create_app, path, mode) as (app, store, request_store, real, mock, refs):
                    with TestClient(app) as client:
                        actual = capture_account_settings_http_contract(client, store, request_store, real, mock, refs, path)
            self.assertEqual([case["name"] for case in baseline], [case["name"] for case in actual])
            for original, observed in zip(baseline, actual, strict=True):
                with self.subTest(mode=mode, case=original["name"]):
                    self.assertEqual(original, observed)

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
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        account = self.account()
        selected = self.choose(account["profile_id"])
        app = create_app(CentralServerSettings(f"sqlite:///{self.path}", "private-token",
            autonomous_top20_enabled=False, market_event_collection_enabled=False))

        async def verify_requests():
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://market-profile.test",
                    timeout=5,
                ) as client:
                    url = "/api/v1/settings/market-profile"
                    headers = {"Authorization": "Bearer private-token"}
                    self.assertEqual(401, (await client.get(url)).status_code)
                    response = await client.get(url, headers=headers)
                    self.assertEqual(200, response.status_code)
                    self.assertEqual(selected, response.json()["settings"])
                    self.assertIsNone(response.json()["applied_revision"])
                    self.assertEqual(422, (await client.put(url, headers=headers, json={})).status_code)
                    self.assertEqual(503, (await client.put(url, headers=headers, json={
                        "market_profile_id": "nas-real-default",
                        "expected_revision": 0,
                        "expected_binding_revision": 1,
                    })).status_code)
                    self.assertEqual(404, (await client.post(
                        "/api/v1/content/server_market_profile_settings", headers=headers,
                        json={"documents": [{"owner": "global", "key": "settings",
                                              "document": {"revision": 99}}]},
                    )).status_code)
                    self.assertEqual(selected, self.store.load_market_profile_settings())
                    with self.store._connection() as connection:
                        connection.execute(
                            "UPDATE central_documents SET document_json=? "
                            "WHERE collection='server_market_profile_settings'",
                            (json.dumps({"revision": 0}),),
                        )
                    error = await client.get(url, headers=headers)
                    self.assertEqual(409, error.status_code)
                    self.assertEqual(
                        "MARKET_PROFILE_SETTINGS_RECOVERY_REQUIRED", error.json()["detail"],
                    )

        asyncio.run(verify_requests())
