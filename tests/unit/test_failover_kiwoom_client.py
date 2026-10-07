from __future__ import annotations

import unittest
from datetime import UTC, datetime

from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomApiError
from kiwoom_monitor.infrastructure.kiwoom_rest.failover_client import FailoverKiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import CentralServerUnavailable
from kiwoom_monitor.infrastructure.kiwoom_rest.account_query import (
    AccountQueryBatch,
    AccountQueryContext,
    AccountScopeMismatchError,
    InterruptedAccountQueryError,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope


class _Client:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result, self.error, self.calls = result, error, 0

    def request_with_continuation(self, *_args, **_kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    def server_now(self): return "now"
    def get_access_token(self): return "token"
class FailoverKiwoomRestClientTests(unittest.TestCase):

    def test_connection_failure_uses_local_and_temporarily_skips_primary(self) -> None:
        primary = _Client(error=CentralServerUnavailable("down"))
        fallback = _Client(result=({"ok": True}, False, ""))
        client = FailoverKiwoomRestClient(primary, fallback, retry_primary_seconds=60)  # type: ignore[arg-type]

        self.assertEqual({"ok": True}, client.request("ka00198", "/rank", {}))
        self.assertEqual({"ok": True}, client.request("ka00198", "/rank", {}))

        self.assertEqual(1, primary.calls)
        self.assertEqual(2, fallback.calls)
        self.assertTrue(client.using_fallback)

    def test_successful_primary_retry_clears_exposed_fallback_state(self) -> None:
        primary = _Client(error=CentralServerUnavailable("down"))
        fallback = _Client(result=({"ok": True}, False, ""))
        client = FailoverKiwoomRestClient(primary, fallback, retry_primary_seconds=1)  # type: ignore[arg-type]

        client.request("ka00198", "/rank", {})
        self.assertTrue(client.using_fallback)
        primary.error = None
        primary.result = ({"ok": True}, False, "")
        client._primary_retry_at = 0.0

        client.request("ka00198", "/rank", {})

        self.assertFalse(client.using_fallback)

    def test_stored_ranking_gateway_failure_then_uses_local_query(self) -> None:
        class Primary(_Client):
            def load_stored_ranking(self, _query_type="5"):
                raise CentralServerUnavailable("gateway down")

        primary = Primary()
        fallback = _Client(result=({"item_inq_rank": [{"stk_cd": "005930"}]}, False, ""))
        client = FailoverKiwoomRestClient(primary, fallback, retry_primary_seconds=60)  # type: ignore[arg-type]

        self.assertIsNone(client.load_stored_ranking("5"))
        self.assertEqual(
            {"item_inq_rank": [{"stk_cd": "005930"}]},
            client.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"}),
        )
        self.assertEqual(1, fallback.calls)
        self.assertTrue(client.using_fallback)

    def test_stored_ranking_success_clears_fallback_state(self) -> None:
        class Primary(_Client):
            def load_stored_ranking(self, _query_type="5"):
                self.calls += 1
                if self.error is not None:
                    raise self.error
                return {"item_inq_rank": [{"stk_cd": "005930"}]}

        primary = Primary(error=CentralServerUnavailable("gateway down"))
        fallback = _Client(result=({"item_inq_rank": []}, False, ""))
        client = FailoverKiwoomRestClient(primary, fallback, retry_primary_seconds=1)  # type: ignore[arg-type]
        self.assertIsNone(client.load_stored_ranking("5"))
        self.assertTrue(client.using_fallback)

        primary.error = None
        client._primary_retry_at = 0.0
        self.assertIsNotNone(client.load_stored_ranking("5"))
        self.assertFalse(client.using_fallback)

    def test_central_api_error_does_not_duplicate_request_locally(self) -> None:
        primary = _Client(error=KiwoomApiError("bad request"))
        fallback = _Client(result=({}, False, ""))
        client = FailoverKiwoomRestClient(primary, fallback)  # type: ignore[arg-type]

        with self.assertRaises(KiwoomApiError):
            client.request("ka00198", "/rank", {})

        self.assertEqual(0, fallback.calls)

    def test_order_api_never_reaches_primary_or_fallback(self) -> None:
        primary = _Client(result=({}, False, ""))
        fallback = _Client(result=({}, False, ""))
        client = FailoverKiwoomRestClient(primary, fallback)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "order APIs"):
            client.request("kt10000", "/api/dostk/ordr", {})
        self.assertEqual(0, primary.calls)
        self.assertEqual(0, fallback.calls)

    def test_account_batch_restarts_direct_and_rejects_different_account(self) -> None:
        scope_a = AccountScope("kiwoom", AccountEnvironment.REAL, "11111111-1111-1111-1111-111111111111")
        scope_b = AccountScope("kiwoom", AccountEnvironment.REAL, "22222222-2222-2222-2222-222222222222")
        nas_context = AccountQueryContext(scope_a, "nas-real-default", 1, "nas")
        direct_context = AccountQueryContext(scope_b, "local-real-default", 1, "direct")

        class Primary(_Client):
            def query_account_pages(self, *args, **kwargs):
                raise InterruptedAccountQueryError("down after first page", nas_context)
        class Direct(_Client):
            def query_account_pages(self, *args, **kwargs):
                self.calls += 1
                return AccountQueryBatch(({"page": 1},), direct_context, 1, True)

        direct = Direct()
        client = FailoverKiwoomRestClient(Primary(), direct)
        with self.assertRaises(AccountScopeMismatchError):
            client.query_account_pages("kt00007", "/api/dostk/acnt", {})
        self.assertEqual(1, direct.calls)

    def test_account_batch_restarts_at_first_page_for_same_account(self) -> None:
        scope = AccountScope("kiwoom", AccountEnvironment.REAL, "11111111-1111-1111-1111-111111111111")
        nas_context = AccountQueryContext(scope, "nas-real-default", 1, "nas")
        direct_context = AccountQueryContext(scope, "local-real-default", 4, "direct")

        class Primary(_Client):
            def query_account_pages(self, *args, **kwargs):
                raise InterruptedAccountQueryError("down after first page", nas_context)
        class Direct(_Client):
            def query_account_pages(self, *args, **kwargs):
                self.calls += 1
                return AccountQueryBatch(({"fresh_page": 1},), direct_context, 1, True)

        direct = Direct()
        result = FailoverKiwoomRestClient(Primary(), direct).query_account_pages(
            "kt00007", "/api/dostk/acnt", {},
        )
        self.assertEqual(({"fresh_page": 1},), result.pages)
        self.assertEqual(1, direct.calls)


if __name__ == "__main__":
    unittest.main()
