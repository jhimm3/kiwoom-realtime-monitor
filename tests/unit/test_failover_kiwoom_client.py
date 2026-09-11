from __future__ import annotations

import unittest

from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomApiError
from kiwoom_monitor.infrastructure.kiwoom_rest.failover_client import FailoverKiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import CentralServerUnavailable


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

    def test_central_api_error_does_not_duplicate_request_locally(self) -> None:
        primary = _Client(error=KiwoomApiError("bad request"))
        fallback = _Client(result=({}, False, ""))
        client = FailoverKiwoomRestClient(primary, fallback)  # type: ignore[arg-type]

        with self.assertRaises(KiwoomApiError):
            client.request("ka00198", "/rank", {})

        self.assertEqual(0, fallback.calls)


if __name__ == "__main__":
    unittest.main()
