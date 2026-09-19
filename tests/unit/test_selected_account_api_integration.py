import unittest

from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.account_query import AccountQueryCapabilityError
from kiwoom_monitor.presentation.journal_workers import HistoryWorker
from kiwoom_monitor.application.trade_history_service import TradeHistoryService
from kiwoom_monitor.application.trade_cost_service import TradeCostService
from datetime import date
from urllib.error import HTTPError
from test_remote_kiwoom_rest_client import Response
import test_scoped_account_api as scoped_fixture
from test_scoped_account_api import ScopedClient, Transport


class SelectedAccountAPIIntegrationTests(unittest.TestCase):
    setUp = scoped_fixture.ScopedAccountAPITests.setUp
    tearDown = scoped_fixture.ScopedAccountAPITests.tearDown
    activate = scoped_fixture.ScopedAccountAPITests.activate
    enable_orders = scoped_fixture.ScopedAccountAPITests.enable_orders

    def remote(self):
        def opener(request, **kwargs):
            response = self.client.request(request.method, request.full_url, content=request.data,
                headers=dict(request.header_items()))
            if response.status_code >= 400: raise HTTPError(request.full_url, response.status_code, "fake error", {}, None)
            return Response(response.json())
        return RemoteKiwoomRestClient("https://nas.test", "private-token", opener=opener)

    def test_discovery_auth_and_selected_two_page_query(self):
        self.assertEqual(401, self.client.get("/api/v3/kiwoom/accounts").status_code)
        remote = self.remote(); contexts = remote.load_account_contexts()
        self.assertEqual({self.a["credential_profile_id"], self.b["credential_profile_id"]}, {c.credential_profile_id for c in contexts})
        context = next(c for c in contexts if c.credential_profile_id == self.b["credential_profile_id"])
        selected = remote.for_account_scope(context.scope)
        batch = selected.query_account_pages("kt00007", "/api/dostk/acnt", {"ord_dt": "20260915"})
        self.assertEqual(batch.page_count, 2); self.assertEqual(batch.context, context)
        self.assertEqual([c[0] for c in ScopedClient.calls], ["b1", "b1"])

    def test_selected_mock_command_reuses_id_and_returns_same_account(self):
        self.enable_orders(self.b)
        remote = self.remote(); context = next(c for c in remote.load_account_contexts() if c.credential_profile_id == self.b["credential_profile_id"])
        selected = remote.for_account_scope(context.scope)
        command = dict(request_id="same", symbol="005930", side="BUY", quantity=1, limit_price=1000)
        first = selected.submit_mock_order(**command); second = selected.submit_mock_order(**command)
        self.assertEqual(first["intent_id"], second["intent_id"])
        self.assertEqual(len(Transport.submissions), 1)
        self.assertEqual(Transport.submissions[0].account_ref, context.scope.account_ref)
        self.assertEqual(selected.get_mock_order(first["intent_id"])["context"]["account_ref"], context.scope.account_ref)
        selected.cancel_mock_order(first["intent_id"])
        self.assertEqual(Transport.cancellations[0].account_ref, context.scope.account_ref)

    def test_empty_history_worker_uses_selected_real_routes_context(self):
        remote = self.remote(); context = next(c for c in remote.load_account_contexts() if c.credential_profile_id == self.b["credential_profile_id"])
        worker = HistoryWorker(TradeHistoryService(remote), TradeCostService(remote), date(2026, 9, 15), date(2026, 9, 15),
            account_client=remote, account_scope=context.scope)
        results, failed = [], []; worker.completed.connect(lambda *args: results.append(args)); worker.failed.connect(failed.append)
        worker.run()
        self.assertEqual(failed, []); self.assertEqual(results[0][0][3], context)
        self.assertEqual({c[0] for c in ScopedClient.calls}, {"b1"})

    def test_disabled_profile_drops_out_of_query_discovery_without_tr(self):
        profile = self.a["credential_profile_id"]
        contexts = self.remote().load_account_contexts()
        context = next(c for c in contexts if c.credential_profile_id == profile)
        result = self.client.post(f"/api/v1/settings/credentials/kiwoom_mock/profiles/{profile}/prepare", headers=self.headers,
            json={"request_id": "f0541a0c-8486-42fb-bdb3-446b9856cbe7", "expected_revision": 1, "disable": True})
        op = result.json()["operation_id"]
        async def wait(): await self.app.state.credential_runtime._operations[op].task
        self.client.portal.call(wait)
        self.client.post(f"/api/v1/settings/credential-operations/{op}/apply", headers=self.headers,
            json={"expected_revision": 1, "target_account_ref": context.scope.account_ref})
        self.client.portal.call(wait)
        with self.assertRaises(AccountQueryCapabilityError): self.remote().for_account_scope(context.scope)
        self.assertEqual(ScopedClient.calls, [])
