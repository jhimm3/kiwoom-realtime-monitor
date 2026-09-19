import json
import unittest
import uuid
from urllib.error import HTTPError
from unittest.mock import Mock

from kiwoom_monitor.domain.order_contract import AccountScope, AccountEnvironment
from kiwoom_monitor.infrastructure.kiwoom_rest.account_query import AccountQueryCapabilityError, AccountScopeMismatchError
from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient, CentralServerUnavailable
from kiwoom_monitor.infrastructure.kiwoom_rest.failover_client import FailoverKiwoomRestClient
from test_remote_kiwoom_rest_client import Response


class SelectedAccountClientTests(unittest.TestCase):
    def setUp(self):
        self.a = self.context("profile-a"); self.b = self.context("profile-b")
        self.responses = []; self.requests = []
        self.remote = RemoteKiwoomRestClient("https://nas.test", "private-token", opener=self.open)

    def context(self, profile):
        return {"broker": "kiwoom", "environment": "mock", "account_ref": str(uuid.uuid4()),
            "credential_profile_id": profile, "binding_revision": 1}

    def scope(self, value):
        return AccountScope(value["broker"], AccountEnvironment(value["environment"]), value["account_ref"])

    def open(self, request, **kwargs):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception): raise response
        return Response(response)

    def selected(self, value=None):
        self.responses.append({"accounts": [self.a, self.b]})
        return self.remote.for_account_scope(self.scope(value or self.b))

    def page(self, context, index=0, complete=True):
        return {"context": context, "batch_id": "batch", "page_index": index,
            "complete": complete, "payload": {}, "next_key": "next" if not complete else ""}

    def test_selected_account_is_fixed_on_every_page(self):
        selected = self.selected()
        self.responses += [self.page(self.b, complete=False), self.page(self.b, index=1)]
        batch = selected.query_account_pages("kt00007", "/api/dostk/acnt", {}, max_pages=2)
        self.assertEqual(batch.context.scope, self.scope(self.b))
        self.assertEqual(batch.page_count, 2)
        for request in self.requests[1:]:
            self.assertTrue(request.full_url.endswith("/api/v3/kiwoom/account-query"))
            body = json.loads(request.data)
            self.assertEqual(body["account_scope"], self.scope(self.b).to_dict())
            self.assertEqual(body["credential_profile_id"], "profile-b")
            self.assertEqual(body["expected_binding_revision"], 1)

    def test_wrong_account_or_binding_cannot_complete_batch(self):
        selected = self.selected()
        for context in [self.a, {**self.b, "binding_revision": 2}]:
            self.responses = [self.page(context)]
            with self.assertRaises(AccountScopeMismatchError): selected.query_account_pages("kt00007", "/api/dostk/acnt", {})

    def test_unavailable_account_never_queries_or_uses_default(self):
        self.responses = [{"accounts": [self.a]}]
        with self.assertRaises(AccountQueryCapabilityError): self.remote.for_account_scope(self.scope(self.b))
        self.assertEqual([r.method for r in self.requests], ["GET"])

    def test_duplicate_or_boolean_binding_list_is_rejected(self):
        for values in [[self.a, self.a], [{**self.a, "binding_revision": True}]]:
            self.responses = [{"accounts": values}]
            with self.assertRaises((AccountScopeMismatchError, RuntimeError)): self.remote.load_account_contexts()

    def test_selected_failover_does_not_call_single_pc_profile(self):
        fallback = Mock()
        failover = FailoverKiwoomRestClient(self.remote, fallback)
        self.responses = [{"accounts": [self.b]}]
        selected = failover.for_account_scope(self.scope(self.b))
        self.responses = [TimeoutError()]
        with self.assertRaises(RuntimeError): selected.query_account_pages("kt00007", "/api/dostk/acnt", {})
        self.assertEqual(fallback.mock_calls, [])

    def test_order_timeout_has_no_automatic_retry_and_explicit_retry_keeps_id(self):
        selected = self.selected()
        self.responses = [TimeoutError(), {"context": self.b, "intent_id": "owned-order"}]
        command = {"request_id": "same-request", "symbol": "005930", "side": "BUY", "quantity": 1, "limit_price": 1000}
        with self.assertRaises(CentralServerUnavailable): selected.submit_mock_order(**command)
        self.assertEqual(len(self.requests), 2)
        with self.assertRaises(ValueError): selected.submit_mock_order(**{**command, "request_id": "new-request"})
        self.assertEqual(len(self.requests), 2)
        selected.submit_mock_order(**command)
        first, second = [json.loads(r.data) for r in self.requests[1:]]
        self.assertEqual(first, second)
        self.assertIn("/api/v2/mock/accounts/" + self.b["account_ref"], self.requests[-1].full_url)

    def test_order_get_cancel_and_wrong_response_context(self):
        selected = self.selected()
        self.responses = [{"context": self.b}, {"context": self.b}, {"context": self.a}]
        selected.get_mock_order("owned-order"); selected.cancel_mock_order("owned-order")
        self.assertIn("environment=mock", self.requests[1].full_url)
        self.assertNotIn("account_ref=", self.requests[1].full_url)
        self.assertTrue(self.requests[2].full_url.endswith("/owned-order/cancel"))
        with self.assertRaises(AccountScopeMismatchError): selected.get_mock_order("owned-order")

    def test_real_account_cannot_submit_mock_order(self):
        real = {**self.b, "environment": "real"}
        self.responses = [{"accounts": [real]}]
        selected = self.remote.for_account_scope(self.scope(real))
        with self.assertRaises(ValueError): selected.submit_mock_order(request_id="x", symbol="005930", side="BUY", quantity=1, limit_price=1000)
        self.assertEqual(len(self.requests), 1)

    def test_selected_mock_account_reads_validated_incremental_execution_events(self):
        selected = self.selected()
        self.responses = [{
            "context": self.b,
            "events": [{"accepted_sequence": 4, "source_event_id": "event-4"}],
            "next_cursor": 4,
            "has_more": False,
        }]

        page = selected.load_mock_execution_events(after_sequence=3, limit=20)

        self.assertEqual(4, page["next_cursor"])
        self.assertIn("/execution-events?", self.requests[-1].full_url)
        self.assertIn("after_sequence=3", self.requests[-1].full_url)
        self.assertIn("limit=20", self.requests[-1].full_url)

    def test_execution_event_client_rejects_wrong_context_or_regressing_cursor(self):
        selected = self.selected()
        self.responses = [{
            "context": self.a, "events": [], "next_cursor": 3, "has_more": False,
        }]
        with self.assertRaises(AccountScopeMismatchError):
            selected.load_mock_execution_events(after_sequence=3)

        self.responses = [{
            "context": self.b,
            "events": [{"accepted_sequence": 3}],
            "next_cursor": 3,
            "has_more": False,
        }]
        with self.assertRaises(RuntimeError):
            selected.load_mock_execution_events(after_sequence=3)

    def test_new_query_rechecks_binding_but_old_snapshot_stays_fixed(self):
        old = self.selected()
        newer = {**self.b, "binding_revision": 2}
        self.responses = [{"accounts": [newer]}, self.page(newer), self.page(newer)]
        fresh = self.remote.for_account_scope(self.scope(self.b))
        self.assertEqual(fresh.query_account_pages("kt00007", "/api/dostk/acnt", {}).context.binding_revision, 2)
        with self.assertRaises(AccountScopeMismatchError): old.query_account_pages("kt00007", "/api/dostk/acnt", {})
