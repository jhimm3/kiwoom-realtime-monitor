from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime

from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class KiwoomRestClientTests(unittest.TestCase):
    def test_reuses_an_unexpired_token(self) -> None:
        requests = []

        def opener(request: object, timeout: int) -> FakeResponse:
            requests.append(request)
            if len(requests) == 1:
                return FakeResponse({"token": "test-token", "expires_dt": "20260815090000"})
            return FakeResponse({"items": []})

        client = KiwoomRestClient(
            KiwoomSettings("key", "secret", "mock"),
            opener=opener,
            clock=lambda: datetime(2026, 8, 14, tzinfo=UTC),
            request_interval_seconds=0,
        )
        client.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "1"})
        client.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "1"})

        self.assertEqual(3, len(requests))
        self.assertEqual("Bearer test-token", requests[1].get_header("Authorization"))
        self.assertEqual("ka00198", requests[1].get_header("Api-id"))

    def test_rejects_api_error_response(self) -> None:
        responses = iter(
            [
                {"token": "test-token", "expires_dt": "20260815090000"},
                {"return_code": "-1", "return_msg": "failed"},
            ]
        )
        client = KiwoomRestClient(
            KiwoomSettings("key", "secret", "mock"),
            opener=lambda request, timeout: FakeResponse(next(responses)),
            clock=lambda: datetime(2026, 8, 14, tzinfo=UTC),
            request_interval_seconds=0,
        )

        with self.assertRaisesRegex(RuntimeError, "ka00198"):
            client.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "1"})
