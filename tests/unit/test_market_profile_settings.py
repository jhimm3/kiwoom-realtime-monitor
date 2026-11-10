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
def fixed_contract_clock(app_factory, clock):
    """Freeze the shared clock boundaries used by the native HTTP fixtures."""
    from kiwoom_monitor.domain.time_basis import KST
    with ExitStack() as stack:
        stack.enter_context(patch("kiwoom_monitor.central_server.database.datetime", clock))
        for module in ("schema_migrations", "database_execution"):
            stack.enter_context(patch(f"kiwoom_monitor.central_server.{module}.kst_now",
                                     side_effect=lambda: clock.now(KST)))
        stack.enter_context(patch("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient.server_now",
                                 side_effect=lambda: clock.now(KST)))
        stack.enter_context(patch.dict(app_factory.__globals__, {"datetime": clock}))
        yield


def contract_storage_hash(database_path, *, document_receipt_clock=None, observation_receipt_clock=None,
                          project_s12_window_migration=False):
    """Require KST writes and compare all other bytes to the frozen UTC contract.

    Only these audited clock columns change representation. The native fixture
    stays untouched; normalizing its in-memory copy preserves the original
    baseline's checks of every row, identifier, value, and JSON/hash payload.
    An explicit document_receipt_clock additionally asserts every document
    receipt equals the injected clock before projecting only that column to
    the old fixture's epoch clock in the in-memory comparison copy. API oracles
    can opt in to projecting the exact S12 window index and migration receipt.
    """
    clock_columns = (
        ("central_schema_migrations", "applied_at"),
        ("central_observation_revisions", "received_at"),
        ("central_minute_bar_operations", "processed_at"),
        ("central_execution_runtime_leases", "lease_expires_at"),
        ("central_execution_runtime_leases", "updated_at"),
        ("central_execution_account_snapshots", "received_at"),
        ("central_shadow_monitor_state", "updated_at"),
        ("central_credential_profiles", "created_at"),
        ("central_credential_profiles", "archived_at"),
    )
    source_connection = (sqlite3.connect(Path(database_path).resolve().as_uri() + "?mode=ro", uri=True)
                         if project_s12_window_migration else sqlite3.connect(database_path))
    with closing(source_connection) as source, closing(sqlite3.connect(":memory:")) as copy:
        source.backup(copy)
        if project_s12_window_migration:
            fixture = ACCOUNT_SETTINGS_BASELINE.with_name("s12_additive_20261106.json")
            migration = json.loads(fixture.read_text(encoding="utf-8"))["diagnostic_migration"]
            rows = copy.execute("SELECT version,name,applied_at FROM central_schema_migrations WHERE version>=25").fetchall()
            index = copy.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                                 (migration["index_name"],)).fetchall()
            if rows != [tuple(migration["row"])] or index != [(migration["index_ddl"],)]:
                raise AssertionError(("expected exact S12 window index and migration receipt", rows, index))
            copy.execute("DROP INDEX idx_central_observation_window")
            copy.execute("DELETE FROM central_schema_migrations WHERE version=25")
            copy.commit()
        if document_receipt_clock is not None:
            expected_now, historical_now = document_receipt_clock
            rows = copy.execute(
                "SELECT collection,owner,document_key,updated_at FROM central_documents"
            ).fetchall()
            for collection, owner, key, received_at in rows:
                if received_at != expected_now:
                    raise AssertionError((collection, owner, key, "expected injected receipt clock", received_at, expected_now))
            copy.execute("UPDATE central_documents SET updated_at=?", (historical_now,))
        for table, column in clock_columns:
            if column not in {row[1] for row in copy.execute(f"PRAGMA table_info({table})")}:
                continue
            for (value,) in copy.execute(f"SELECT DISTINCT {column} FROM {table}").fetchall():
                if value is None:
                    continue
                at = datetime.fromisoformat(value)
                # Credential activation fixtures also seed caller-supplied UTC
                # event times, which are distinct from the server's clock writes.
                allowed = {0, 32400} if table == "central_credential_profiles" else {32400}
                if at.tzinfo is None or at.utcoffset().total_seconds() not in allowed:
                    raise AssertionError((table, column, "expected aware KST clock write", value))
                comparison_at = at
                if observation_receipt_clock is not None and (table, column) in {
                    ("central_observation_revisions", "received_at"),
                    ("central_minute_bar_operations", "processed_at"),
                }:
                    expected_now, historical_now = observation_receipt_clock
                    if at != expected_now:
                        raise AssertionError((table, column, "expected injected observation clock", value, expected_now))
                    comparison_at = historical_now
                copy.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?",
                             (comparison_at.astimezone(timezone.utc).isoformat(), value))
        for snapshot_id, document in copy.execute(
                "SELECT snapshot_id,document_json FROM central_execution_account_snapshots").fetchall():
            body = json.loads(document)
            received = datetime.fromisoformat(body["received_at"])
            if received.tzinfo is None or received.utcoffset().total_seconds() != 32400:
                raise AssertionError("expected KST snapshot receipt")
            body["received_at"] = received.astimezone(timezone.utc).isoformat()
            copy.execute("UPDATE central_execution_account_snapshots SET document_json=? WHERE snapshot_id=?",
                         (json.dumps(body, ensure_ascii=False, separators=(",", ":")), snapshot_id))
        # Approved BODY authority adds only two internal columns and migration23.
        # Independently check their exact schema/defaults before projecting the
        # memory copy back to the frozen contract. Every original row/value and
        # the native fixture itself remain covered by the original full hash.
        columns = copy.execute("PRAGMA table_info(central_news_jobs)").fetchall()
        expected_names = ["job_key", "article_revision_id", "stock_code", "target_id", "stage",
                          "input_hash", "processing_version", "attempts", "next_retry_at", "state",
                          "output_ref", "error", "payload_json", "updated_at",
                          "body_processor_owner", "body_input_ref"]
        if [row[1] for row in columns] != expected_names or columns[-2:] != [
                (14, "body_processor_owner", "TEXT", 1, "''", 0),
                (15, "body_input_ref", "TEXT", 0, None, 0)]:
            raise AssertionError("expected exact BODY authority schema on existing news job columns")
        if copy.execute("SELECT job_key FROM central_news_jobs WHERE body_processor_owner<>'' "
                        "OR body_input_ref IS NOT NULL").fetchall():
            raise AssertionError("existing contract fixture must not acquire a remote BODY authority")
        migration = copy.execute("SELECT version,name FROM central_schema_migrations WHERE version=23").fetchall()
        if migration != [(23, "news_body_processor_attempt_owner")]:
            raise AssertionError(("expected exact BODY authority migration", migration))
        # Its applied_at has already passed the same aware-KST assertion above.
        copy.execute("ALTER TABLE central_news_jobs DROP COLUMN body_input_ref")
        copy.execute("ALTER TABLE central_news_jobs DROP COLUMN body_processor_owner")
        copy.execute("DELETE FROM central_schema_migrations WHERE version=23 AND name=?",
                     ("news_body_processor_attempt_owner",))
        return hashlib.sha256("\n".join(copy.iterdump()).encode()).hexdigest()


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
            stack.enter_context(fixed_contract_clock(app_factory, FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_credentials.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.news_credentials.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.ai_credentials.datetime", FixedNow))
            stack.enter_context(patch("kiwoom_monitor.central_server.database_account_settings.time", return_value=2000.0))
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
                headers={"X-Request-ID": f"account-contract-{name}",
                         **({"Authorization": "Bearer private-token"} if authenticated else {})}, **kwargs)
        if response.status_code != status:
            raise AssertionError((name, response.status_code, response.text))
        after = stored_hash()
        if before != after:
            raise AssertionError((name, "settings HTTP request changed native storage"))
        cases.append({"name": name, "status": response.status_code, "headers": dict(response.headers),
            "body": response.json(), "storage_hash": contract_storage_hash(database_path, project_s12_window_migration=True),
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
        # Preserve the schema20 golden bytes. The exact hashes were replayed
        # from the verified schema22 dumps with migration24. The shared
        # helper still validates and projects the BODY23 addition.
        schema24_hashes = {
            "178f3d2c824f1657e0dd78d20143c041a2def2636dd010a67801d14d4041431f":
                "3d7c65c53b8696a02cc1b13ce26e22574ad645a13cfd2db2c669221d4c1a3561",
            "e97ed116cc97cc55680bea3afb441f0c525e64feed591dc2e4db48c152cf45fa":
                "e693b057a211772324c8cf5a5e4de677994a562caa21e0fc587e480778b174c5",
            "b8572a4ca9f3015ddc2669a0f0ed59309842e2064fe47f34eacf57668ee78015":
                "9a21414de7558267d4ac545a31fe39fe8405d8ceae738191f00c932f11016808",
            "0ebd9d8973c37e20d4bb72f6fe337c3a654cbc7d473f2d6f3cf393beb82e9995":
                "0102c78fe292149fdf19125153bfabbe98633a9fb34e2b8f53eaa56ebda809cf",
            "2b56a47314c9252a699e709078dababe8d3aa69e12122a78adbe9ce734c10276":
                "bcc56162cdaa41093eb86271d33ada4c28417cdaa737808344006ce9741dbd6e",
            "17e94d1a123f57fc4979294fd818439b153753f72124e543e5f1870940337a98":
                "0f2414bd79dc7cecc5786f37c362ae4437457b4306288d0df4addf443116ee23",
        }
        for mode, baseline in zip(("absent", "mock", "real"), expected, strict=True):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "settings.sqlite3"
                with account_settings_contract_app(create_app, path, mode) as (app, store, request_store, real, mock, refs):
                    with TestClient(app) as client:
                        actual = capture_account_settings_http_contract(client, store, request_store, real, mock, refs, path)
            self.assertEqual([case["name"] for case in baseline], [case["name"] for case in actual])
            for original, observed in zip(baseline, actual, strict=True):
                with self.subTest(mode=mode, case=original["name"]):
                    # Raw headers remain fully compared, including exact ID echo.
                    expected_case = {**original,
                        "headers": {**original["headers"],
                                    "x-request-id": f"account-contract-{original['name']}"},
                        "storage_hash": schema24_hashes[original["storage_hash"]]}
                    self.assertEqual(expected_case, observed)

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

    def test_tr_connections_native_cas_and_binding_leave_existing_owners_unchanged(self):
        def target(account):
            return {"scope": {"broker": "kiwoom", "environment": account["environment"],
                              "account_ref": account["account_ref"]},
                    "credential_profile_id": account["profile_id"], "binding_revision": 1}
        a, b = target(self.account("mock")), target(self.account())
        self.assertEqual({"mode": "separated", "execution_target": None, "data_target": None, "revision": 0},
                         self.store.load_tr_connection_settings())
        self.assertEqual([], self.store.load_documents("server_tr_connection_settings"))
        existing = (self.store.load_market_profile_settings(), self.store.load_account_bindings(),
                    self.store.load_documents("server_account_settings"))
        split = {"mode": "separated", "execution_target": a, "data_target": b}
        integrated = {"mode": "integrated", "execution_target": a, "data_target": a}
        self.assertEqual({**split, "revision": 1}, self.store.save_tr_connection_settings(split, expected_revision=0))
        with self.assertRaisesRegex(ValueError, "REVISION_CONFLICT"):
            self.store.save_tr_connection_settings(integrated, expected_revision=0)
        with self.assertRaisesRegex(ValueError, "ACCOUNT_CONTEXT_MISMATCH"):
            self.store.save_tr_connection_settings({**split, "data_target": {**b, "binding_revision": 2}}, expected_revision=1)
        with self.assertRaisesRegex(ValueError, "SETTINGS_INVALID"):
            self.store.save_tr_connection_settings({**split, "mode": "integrated"}, expected_revision=1)
        self.assertEqual({**integrated, "revision": 2}, self.store.save_tr_connection_settings(integrated, expected_revision=1))
        persisted = self.store.load_documents("server_tr_connection_settings")
        self.assertEqual({**integrated, "revision": 2}, self.store.save_tr_connection_settings(integrated, expected_revision=2))
        self.assertEqual(persisted, self.store.load_documents("server_tr_connection_settings"))
        self.assertEqual(existing, (self.store.load_market_profile_settings(), self.store.load_account_bindings(),
                                    self.store.load_documents("server_account_settings")))

    def test_tr_connections_wire_routing_orders_rotation_ack_loss_and_restart(self):
        from io import BytesIO
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient

        now = datetime(2026, 11, 6, 0, 31, 15, tzinfo=timezone.utc)
        sent, stores, failed, transports = [], [], set(), []

        def wire(request, *, timeout):
            headers = {key.casefold(): value for key, value in request.header_items()}
            body = json.loads(request.data)
            api_id = headers.get("api-id", "oauth")
            marker = headers.get("authorization", "").removeprefix("Bearer token:") or body["appkey"]
            sent.append((marker, api_id, body))
            accounts = {"r": "1111111111", "a": "2222222222", "b": "3333333333", "b2": "3333333333",
                        "d": "4444444444"}
            payload = {
                "oauth": {"token": "token:" + marker}, "ka00001": {"acctNo": accounts[marker]},
                "ka10080": {"transport": marker} if marker not in failed else {"return_code": 99, "return_msg": "data offline"},
                "ka10075": {"oso": []}, "ka10076": {"cntr": []}, "kt00018": {"acnt_evlt_remn_indv_tot": []},
                "kt00001": {"ord_alow_amt": "1000000" if marker == "a" else "2000000"},
                "kt00007": {"acnt_ord_cntr_prps_dtl": [{"ord_no": "fill-" + marker}]},
                "kt10000": {"ord_no": "1001"}, "kt10003": {"ord_no": "1002"},
            }[api_id]
            result = BytesIO(json.dumps(payload).encode())
            result.headers = {}
            return result

        class FakeWireClient(KiwoomRestClient):
            def __init__(self, settings):
                # Only the external transport and test pacing are replaced.
                super().__init__(settings, opener=wire, clock=lambda: now, request_interval_seconds=0)

            def server_now(self):
                return now

        config = CentralServerSettings(f"sqlite:///{self.path}", "private-token", query_access_token="read-token",
            credential_directory=str(self.path.parent / "vault"), kiwoom_app_key="r", kiwoom_secret_key="fake",
            account_identity_registry_enabled=True, account_identity_hmac_key="routing-fixture-key" * 3,
            autonomous_top20_enabled=False, market_event_collection_enabled=False, news_history_jobs_enabled=False,
            news_naver_api_enabled=False, news_naver_stock_enabled=False, news_naver_market_enabled=False,
            news_query_set_enabled=False)
        factory = create_app.__globals__["create_query_store"]

        def capture_store(*args, **kwargs):
            store = factory(*args, **kwargs)
            store._execution_wall_now = lambda: now
            stores.append(store)
            return store

        async def activate(app, key, profile=None, revision=0, environment="mock"):
            runtime = app.state.credential_runtime
            provider = "kiwoom_" + environment
            if profile is None:
                profile = (await runtime.create_profile(provider, str(uuid.uuid4()), key))["profile_id"]
            prepared = await runtime.prepare(provider, profile, str(uuid.uuid4()), revision,
                                             {"app_key": key, "secret_key": "fake"}, disabled=False)
            operation = runtime._operations[prepared["operation_id"]]
            await asyncio.wait_for(asyncio.shield(operation.task), 10)
            self.assertEqual("READY", operation.state, operation.error_code)
            await runtime.apply(operation.operation_id, revision, operation.candidate.account_ref)
            await asyncio.wait_for(asyncio.shield(operation.task), 10)
            self.assertEqual("ACTIVE", operation.state, operation.error_code)
            owner = app.state.mock_credential_owner if environment == "mock" else app.state.real_credential_owner
            return owner.bundle(profile)

        def target(bundle):
            binding = bundle.binding
            return {"scope": binding.scope.to_dict(), "credential_profile_id": binding.credential_profile_id,
                    "binding_revision": binding.binding_revision}

        async def verify():
            route = "/api/v1/settings/tr-connections"
            bars = "/api/v1/kiwoom/query"
            query = {"api_id": "ka10080", "path": "/api/dostk/chart", "body": {"stk_cd": "005930"}}
            headers = {"Authorization": "Bearer private-token"}
            app = create_app(config)
            async with app.router.lifespan_context(app), httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="https://routing.test", headers=headers) as client:
                self.assertEqual(403, (await client.get(route, headers={"Authorization": "Bearer read-token"})).status_code)
                self.assertEqual("legacy", (await client.get(route)).json()["status"])
                self.assertEqual("r", (await client.post(bars, json=query)).json()["payload"]["transport"])
                a, b = await activate(app, "a"), await activate(app, "b")
                self.assertIsNot(a.client._request_lock, b.client._request_lock)
                execution, data = target(a), target(b)
                owner = app.state.mock_credential_owner
                await owner.update_settings(execution["scope"], {"active_profile_id": execution["credential_profile_id"],
                    "monitor_enabled": True, "mock_order_enabled": True}, 1)
                a = owner.bundle(execution["credential_profile_id"])
                self.assertIs(a.client, a.broker._client)
                self.assertEqual(1000000, (await a.monitor.refresh_recovery()).account.available_cash_won)
                self.assertEqual(2000000, (await b.monitor.refresh_recovery()).account.available_cash_won)
                split = {"mode": "separated", "execution_target": execution, "data_target": data}
                integrated = {"mode": "integrated", "execution_target": execution, "data_target": execution}
                real_data = target(await activate(app, "d", environment="real"))
                await app.state.real_credential_owner.update_settings(real_data["scope"], {
                    "active_profile_id": real_data["credential_profile_id"], "monitor_enabled": False,
                    "mock_order_enabled": False}, 1)
                real_split = {**split, "data_target": real_data}
                cut = len(sent)
                response = await client.put(route, json={**real_split, "expected_revision": 0})
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual("d", (await client.post(bars, json=query)).json()["payload"]["transport"])
                self.assertEqual([("d", "ka10080")], [(marker, api) for marker, api, _ in sent[cut:]])
                self.assertFalse(stores[0].load_account_settings(real_data["scope"])["monitor_enabled"])
                rejected = await client.put(route, json={**split, "expected_revision": False})
                self.assertEqual(422, rejected.status_code)
                saved = await client.put(route, json={**split, "expected_revision": 1})
                self.assertEqual(200, saved.status_code, saved.text)
                self.assertEqual(2, saved.json()["applied_revision"])
                self.assertEqual({"environment": "real", "credential_profile_id": "nas-real-default"}, saved.json()["realtime_market"])
                self.assertEqual("token:r", app.state.realtime_collector._token_provider())
                self.assertEqual(409, (await client.put(route, json={**integrated, "expected_revision": 0})).status_code)
                self.assertEqual("b", (await client.post(bars, json=query)).json()["payload"]["transport"])
                self.assertTrue((await client.post(bars, json=query)).json()["cache_hit"])
                b = await activate(app, "b2", data["credential_profile_id"], 1)
                self.assertEqual(data, target(b))
                self.assertEqual("b2", (await client.post(bars, json=query)).json()["payload"]["transport"])

                def scope(target):
                    return {"account_scope": target["scope"], "credential_profile_id": target["credential_profile_id"],
                            "expected_binding_revision": target["binding_revision"]}
                async def fill(target):
                    response = await client.post("/api/v3/kiwoom/account-query", json={**scope(target),
                        "api_id": "kt00007", "path": "/api/dostk/acnt", "body": {}})
                    self.assertEqual(200, response.status_code, response.text)
                    return response.json()["payload"]["acnt_ord_cntr_prps_dtl"][0]["ord_no"]
                failed.add("b2")
                failed_bar = await client.post(bars, json={**query, "body": {"stk_cd": "000660"}})
                self.assertEqual(502, failed_bar.status_code)
                self.assertEqual("fill-a", await fill(execution))
                self.assertEqual("fill-b2", await fill(data))
                orders = "/api/v2/mock/accounts/" + execution["scope"]["account_ref"] + "/orders"
                order = await client.post(orders, json={**scope(execution), "request_id": "route-order",
                    "symbol": "005930", "side": "BUY", "quantity": 1, "limit_price": 70000})
                self.assertEqual(200, order.status_code, order.text)
                self.assertEqual("1001", order.json()["broker_order_id"], order.text)
                normal_save = stores[0].save_tr_connection_settings

                def lost_ack(*args, **kwargs):
                    normal_save(*args, **kwargs)
                    raise OSError("saved response lost")

                with patch.object(stores[0], "save_tr_connection_settings", side_effect=lost_ack):
                    self.assertEqual(503, (await client.put(route, json={**integrated, "expected_revision": 2})).status_code)
                recovery = (await client.get(route)).json()
                self.assertEqual({**integrated, "revision": 3}, recovery["settings"])
                self.assertEqual("recovery_required", recovery["status"])
                self.assertIsNone(recovery["applied_revision"])
                self.assertEqual(502, (await client.post(bars, json=query)).status_code)
                cancelled = await client.post(orders + "/" + order.json()["intent_id"] + "/cancel", json=scope(execution))
                self.assertEqual(200, cancelled.status_code, cancelled.text)
                self.assertEqual([("a", "kt10000"), ("a", "kt10003")],
                                 [(marker, api) for marker, api, _ in sent if api.startswith("kt1000")])
                self.assertEqual("1001", next(body["orig_ord_no"] for _, api, body in sent if api == "kt10003"))

            restarted = create_app(config)
            async with restarted.router.lifespan_context(restarted), httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=restarted), base_url="https://routing.test", headers=headers) as client:
                owner = restarted.state.mock_credential_owner
                await asyncio.wait_for(asyncio.gather(*owner._boot_tasks), 10)
                restored = (await client.get(route)).json()
                self.assertEqual({**integrated, "revision": 3}, restored["settings"])
                self.assertEqual(3, restored["applied_revision"])
                self.assertEqual("a", (await client.post(bars, json=query)).json()["payload"]["transport"])
                a = owner.bundle(execution["credential_profile_id"])
                self.assertIs(a.client, next(client for client, api in reversed(transports) if api == "ka10080"))
                self.assertIs(a.client, a.broker._client)
                self.assertIs(a.client, a.runtime._lifecycle._transport._client)
                same_account_split = {**integrated, "mode": "separated", "expected_revision": 3}
                changed = (await client.put(route, json=same_account_split)).json()
                self.assertEqual("integrated", changed["effective_mode"])
                self.assertEqual("a", (await client.post(bars, json=query)).json()["payload"]["transport"])
                self.assertEqual("token:r", restarted.state.realtime_collector._token_provider())

        with ExitStack() as stack:
            from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
            original_call = CentralRestBroker._call_transport
            def capture_transport(broker, job):
                transports.append((broker._client, job.api_id))
                return original_call(broker, job)
            stack.enter_context(patch.object(CentralRestBroker, "_call_transport", capture_transport))
            stack.enter_context(patch.dict(create_app.__globals__, {"create_query_store": capture_store}))
            stack.enter_context(patch("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", FakeWireClient))
            for target_name in ("realtime_collector.CentralRealtimeCollector", "mock_account_monitor.MockAccountRealtimeCollector",
                                "mock_account_monitor.RealAccountRealtimeCollector"):
                stack.enter_context(patch(f"kiwoom_monitor.central_server.{target_name}.start", new_callable=AsyncMock))
            asyncio.run(verify())

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
        profile = account["profile_id"]
        chosen = self.choose(profile)
        binding = self.store.load_latest_account_binding(profile, "kiwoom", "real")
        rotated = self.store.finalize_credential_activation({**account, "credential_revision": 2,
            "operation_id": str(uuid.uuid4()), "request_id": str(uuid.uuid4()),
            "request_digest": uuid.uuid4().hex * 2})
        self.assertEqual(2, rotated["credential_revision"])
        self.assertEqual(binding["binding_revision"], rotated["binding_revision"])
        self.assertEqual(binding, self.store.load_latest_account_binding(profile, "kiwoom", "real"))

        refreshed = self.store.append_account_binding({**binding, "verified_at": "2026-09-15T12:05:00+09:00"})
        self.assertEqual((1, 2), (binding["binding_revision"], refreshed["binding_revision"]))
        before = self.store.load_documents("server_market_profile_settings")
        with self.assertRaisesRegex(ValueError, "ACCOUNT_CONTEXT_MISMATCH"):
            self.choose(profile, revision=1, binding=binding["binding_revision"])
        self.assertEqual(before, self.store.load_documents("server_market_profile_settings"))
        self.assertEqual(chosen, self.choose(profile, revision=1, binding=refreshed["binding_revision"]))
        self.assertEqual(before, self.store.load_documents("server_market_profile_settings"))

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
            autonomous_top20_enabled=False, market_event_collection_enabled=False,
            news_naver_market_enabled=False, news_naver_api_enabled=False,
            news_naver_stock_enabled=False, news_query_set_enabled=False,
            news_history_jobs_enabled=False))

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
