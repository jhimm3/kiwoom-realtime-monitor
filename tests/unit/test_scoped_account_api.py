from __future__ import annotations

import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.domain.order_contract import BrokerSubmission, AccountEnvironment
from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import VerifiedAccountIdentity
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
from test_mock_credential_owner import FakeClient

NOW = datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc)


class ScopedClient(FakeClient):
    calls = []
    accounts = {}

    def server_now(self): return NOW

    def request_with_continuation(self, api_id, path, body, *, cont_yn="N", next_key=""):
        if api_id == "ka10075":
            account_ref = self.accounts.get(self._settings.app_key)
            orders = [] if Transport.unknown else [i for i in Transport.submissions if i.account_ref == account_ref]
            return {"oso": [{"ord_no": "broker-" + i.account_ref, "stk_cd": i.symbol,
                "ord_qty": str(i.quantity), "oso_qty": str(i.quantity), "ord_pric": str(i.limit_price),
                "ord_stt": "접수"} for i in orders]}, False, ""
        if api_id in {"kt00007", "kt00015"}:
            self.calls.append((self._settings.app_key, api_id, dict(body), cont_yn, next_key))
            return {"account_marker": self._settings.app_key, "page": cont_yn}, cont_yn == "N", "next" if cont_yn == "N" else ""
        return super().request_with_continuation(api_id, path, body, cont_yn=cont_yn, next_key=next_key)


class Transport:
    submissions = []
    cancellations = []
    unknown = False

    def submit(self, intent):
        self.submissions.append(intent)
        if self.unknown: raise SubmissionUnknown("fake response lost")
        return BrokerSubmission("broker-" + intent.account_ref, NOW)

    def cancel(self, intent, broker_order_id, quantity=0):
        self.cancellations.append(intent)
        return BrokerSubmission("cancel-" + intent.account_ref, NOW)


class ScopedAccountAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        ScopedClient.calls = []; ScopedClient.accounts = {}; Transport.submissions = []; Transport.cancellations = []; Transport.unknown = False
        self.patches = [patch("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", ScopedClient),
            patch("kiwoom_monitor.central_server.mock_account_monitor.MockAccountRealtimeCollector.start", new=AsyncMock()),
            patch("kiwoom_monitor.central_server.mock_runtime.make_mock_transport", return_value=Transport())]
        for item in self.patches: item.start()
        self.app = create_app(CentralServerSettings(f"sqlite:///{root / 'central.sqlite'}", "private-token",
            credential_directory=str(root / "secrets"), account_identity_registry_enabled=True,
            account_identity_hmac_key="x" * 32, autonomous_top20_enabled=False, market_event_collection_enabled=False))
        self.client = TestClient(self.app, base_url="https://nas.test"); self.client.__enter__()
        self.headers = {"Authorization": "Bearer private-token"}
        self.a = self.activate("a1"); self.b = self.activate("b1")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def activate(self, key, profile=None, revision=0):
        if profile is None:
            profile = self.client.post("/api/v1/settings/credentials/kiwoom_mock/profiles", headers=self.headers,
                json={"request_id": str(uuid.uuid4()), "label": key}).json()["profile_id"]
        response = self.client.post(f"/api/v1/settings/credentials/kiwoom_mock/profiles/{profile}/prepare",
            headers=self.headers, json={"request_id": str(uuid.uuid4()), "expected_revision": revision,
            "replacement": {"app_key": key, "secret_key": "fake-secret"}})
        self.assertEqual(202, response.status_code)
        operation_id = response.json()["operation_id"]
        async def wait(): await self.app.state.credential_runtime._operations[operation_id].task
        self.client.portal.call(wait)
        status = self.client.get(f"/api/v1/settings/credential-operations/{operation_id}", headers=self.headers).json()
        self.assertEqual("READY", status["state"])
        self.client.post(f"/api/v1/settings/credential-operations/{operation_id}/apply", headers=self.headers,
            json={"expected_revision": revision, "target_account_ref": status["target_account_ref"]})
        self.client.portal.call(wait)
        status = self.client.get(f"/api/v1/settings/credential-operations/{operation_id}", headers=self.headers).json()
        self.assertEqual("ACTIVE", status["state"])
        ScopedClient.accounts[key] = status["target_account_ref"]
        return {"account_scope": {"broker": "kiwoom", "environment": "mock", "account_ref": status["target_account_ref"]},
            "credential_profile_id": profile, "expected_binding_revision": status["binding_revision"]}

    def query(self, target, **page):
        return self.client.post("/api/v3/kiwoom/account-query", headers=self.headers,
            json={**target, "api_id": "kt00007", "path": "/api/dostk/acnt", "body": {"ord_dt": "20260915"}, **page})

    def order_url(self, target): return f"/api/v2/mock/accounts/{target['account_scope']['account_ref']}/orders"

    def enable_orders(self, target, enabled=True, revision=1):
        response = self.client.put(f"/api/v1/settings/accounts/{target['account_scope']['account_ref']}?environment=mock",
            headers=self.headers, json={"expected_revision": revision, "active_profile_id": target["credential_profile_id"],
            "monitor_enabled": True, "mock_order_enabled": enabled})
        self.assertEqual(200, response.status_code)

    def submit(self, target, request_id="same"):
        return self.client.post(self.order_url(target), headers=self.headers, json={**target, "request_id": request_id,
            "symbol": "005930", "side": "BUY", "quantity": 1, "limit_price": 1000})

    def read(self, target, intent_id):
        return self.client.get(self.order_url(target) + "/" + intent_id, headers=self.headers,
            params={"credential_profile_id": target["credential_profile_id"],
                "expected_binding_revision": target["expected_binding_revision"], "environment": "mock"})

    def events(self, target, **extra):
        return self.client.get(
            f"/api/v2/mock/accounts/{target['account_scope']['account_ref']}/execution-events",
            headers=self.headers,
            params={
                "credential_profile_id": target["credential_profile_id"],
                "expected_binding_revision": target["expected_binding_revision"],
                "environment": "mock",
                **extra,
            },
        )

    def test_two_accounts_use_their_own_broker_cursor_and_context(self):
        first = self.query(self.a).json()
        second = self.query(self.b).json()
        self.assertEqual("a1", first["payload"]["account_marker"])
        self.assertEqual("b1", second["payload"]["account_marker"])
        before = len(ScopedClient.calls)
        self.assertEqual(409, self.query(self.b, batch_id=first["batch_id"], page_index=1, next_key="next").status_code)
        self.assertEqual(before, len(ScopedClient.calls))
        completed = self.query(self.a, batch_id=first["batch_id"], page_index=1, next_key="next").json()
        self.assertTrue(completed["complete"])
        self.assertEqual(self.a["account_scope"]["account_ref"], completed["context"]["account_ref"])

    def test_mismatched_target_stale_binding_and_old_cursor_never_send(self):
        first = self.query(self.a).json()
        wrong = {**self.a, "account_scope": self.b["account_scope"]}
        before = len(ScopedClient.calls)
        self.assertEqual(409, self.query(wrong).status_code)
        current = self.activate("a2", self.a["credential_profile_id"], revision=1)
        self.assertEqual(409, self.query(self.a).status_code)
        self.assertEqual(409, self.query(current, batch_id=first["batch_id"], page_index=1, next_key="next").status_code)
        self.assertEqual(before, len(ScopedClient.calls))

    def test_order_ids_and_duplicates_are_separate_and_foreign_intents_rejected(self):
        self.enable_orders(self.a); self.enable_orders(self.b)
        a = self.submit(self.a).json(); b = self.submit(self.b).json()
        self.assertNotEqual(a["intent_id"], b["intent_id"])
        self.assertEqual(2, len(Transport.submissions))
        duplicate = self.submit(self.a).json()
        self.assertEqual(a["intent_id"], duplicate["intent_id"]); self.assertEqual(2, len(Transport.submissions))
        self.assertEqual(404, self.read(self.b, a["intent_id"]).status_code)
        response = self.client.post(self.order_url(self.b) + "/" + a["intent_id"] + "/cancel",
            headers=self.headers, json=self.b)
        self.assertEqual(404, response.status_code); self.assertEqual([], Transport.cancellations)
        self.assertEqual(200, self.read(self.a, a["intent_id"]).status_code)
        own_cancel = self.client.post(self.order_url(self.a) + "/" + a["intent_id"] + "/cancel",
            headers=self.headers, json=self.a)
        self.assertEqual(200, own_cancel.status_code, own_cancel.text)
        self.assertEqual("CANCEL_PENDING", own_cancel.json()["state"])
        self.assertEqual([self.a["account_scope"]["account_ref"]], [i.account_ref for i in Transport.cancellations])

    def test_orders_off_allows_owned_record_read_but_blocks_submit_and_cancel(self):
        self.assertEqual(503, self.submit(self.a).status_code)
        self.enable_orders(self.a)
        record = self.submit(self.a).json()
        self.enable_orders(self.a, enabled=False, revision=2)
        self.assertEqual(200, self.read(self.a, record["intent_id"]).status_code)
        self.assertEqual(503, self.submit(self.a, "new").status_code)
        self.assertEqual(503, self.client.post(self.order_url(self.a) + "/" + record["intent_id"] + "/cancel",
            headers=self.headers, json=self.a).status_code)
        self.assertEqual(1, len(Transport.submissions))

    def test_execution_event_pages_are_account_scoped_and_cursor_resumable(self):
        self.enable_orders(self.a); self.enable_orders(self.b)
        self.submit(self.a, "a-first")
        self.submit(self.b, "b-first")
        self.submit(self.a, "a-second")

        first_response = self.events(self.a, limit=1)
        self.assertEqual(200, first_response.status_code, first_response.text)
        first = first_response.json()
        self.assertTrue(first["has_more"])
        self.assertEqual(1, len(first["events"]))
        self.assertEqual(self.a["account_scope"]["account_ref"], first["events"][0]["account_ref"])
        second = self.events(self.a, after_sequence=first["next_cursor"], limit=10).json()
        self.assertFalse(second["has_more"])
        self.assertTrue(second["events"])
        self.assertTrue(all(
            row["account_ref"] == self.a["account_scope"]["account_ref"]
            for row in second["events"]
        ))
        self.assertTrue(all(
            row["accepted_sequence"] > first["next_cursor"] for row in second["events"]
        ))
        replay = self.events(self.a, after_sequence=first["next_cursor"], limit=10).json()
        self.assertEqual(second["events"], replay["events"])
        self.assertEqual(second["next_cursor"], replay["next_cursor"])

        wrong = self.client.get(
            f"/api/v2/mock/accounts/{self.b['account_scope']['account_ref']}/execution-events",
            headers=self.headers,
            params={
                "credential_profile_id": self.a["credential_profile_id"],
                "expected_binding_revision": self.a["expected_binding_revision"],
                "environment": "mock",
            },
        )
        self.assertEqual(409, wrong.status_code)

    def test_unknown_submission_duplicate_is_not_retransmitted(self):
        self.enable_orders(self.a); Transport.unknown = True
        first = self.submit(self.a).json()
        self.assertEqual("SUBMISSION_UNKNOWN", first["state"])
        second = self.submit(self.a).json()
        self.assertEqual(first["intent_id"], second["intent_id"])
        self.assertEqual(1, len(Transport.submissions))

    def test_capabilities_auth_required_context_and_legacy_default_are_fixed(self):
        caps = self.client.get("/api/v1/capabilities", headers=self.headers).json()["capabilities"]
        self.assertTrue(caps["multi_account_query_v3"]); self.assertTrue(caps["scoped_mock_orders_v2"])
        self.assertTrue(caps["execution_event_read_v1"])
        self.assertEqual(401, self.client.post("/api/v3/kiwoom/account-query", json={}).status_code)
        self.assertEqual(422, self.client.post(self.order_url(self.a), headers=self.headers,
            json={"request_id": "missing-context"}).status_code)
        self.enable_orders(self.a)
        self.assertEqual(503, self.client.post("/api/v1/mock/orders", headers=self.headers,
            json={"request_id": "legacy", "symbol": "005930", "side": "BUY", "quantity": 1, "limit_price": 1000}).status_code)
        self.assertEqual(503, self.client.post("/api/v2/kiwoom/account-query", headers=self.headers,
            json={"api_id": "kt00007", "path": "/api/dostk/acnt"}).status_code)

    def test_legacy_real_query_and_explicit_main_real_query_keep_default_binding(self):
        root = Path(self.temp.name)
        with patch("kiwoom_monitor.infrastructure.kiwoom_rest.account_identity.KiwoomAccountIdentityReader.verify",
            new=AsyncMock(return_value=VerifiedAccountIdentity("kiwoom", AccountEnvironment.REAL, "f" * 64, NOW))), patch(
            "kiwoom_monitor.central_server.app.CentralRealtimeCollector.start", new=AsyncMock()):
            app = create_app(CentralServerSettings(f"sqlite:///{root / 'main.sqlite'}", "private-token",
                credential_directory=str(root / "main-secrets"), account_identity_registry_enabled=True,
                account_identity_hmac_key="x" * 32, kiwoom_app_key="fake-real-key", kiwoom_secret_key="fake-real-secret",
                autonomous_top20_enabled=False, market_event_collection_enabled=False))
            with TestClient(app, base_url="https://nas.test") as client:
                binding = app.state.verified_account_bindings[0]
                body = {"api_id": "kt00007", "path": "/api/dostk/acnt", "body": {"ord_dt": "20260915"}}
                legacy = client.post("/api/v2/kiwoom/account-query", headers=self.headers, json=body)
                self.assertEqual(200, legacy.status_code)
                explicit = client.post("/api/v3/kiwoom/account-query", headers=self.headers,
                    json={**body, "account_scope": binding.scope.to_dict(),
                        "credential_profile_id": binding.credential_profile_id,
                        "expected_binding_revision": binding.binding_revision})
                self.assertEqual(200, explicit.status_code)
                self.assertEqual(legacy.json()["context"], explicit.json()["context"])
                self.assertEqual("fake-real-key", explicit.json()["payload"]["account_marker"])
