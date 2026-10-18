from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

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
    def test_real_and_mock_default_limiters_remain_independent(self) -> None:
        real = KiwoomRestClient(KiwoomSettings("real-key", "real-secret", "real"))
        mock = KiwoomRestClient(KiwoomSettings("mock-key", "mock-secret", "mock"))

        self.assertEqual(0.2, real._base_request_interval_seconds)
        self.assertEqual(1.0, mock._base_request_interval_seconds)
        self.assertIsNot(real._request_lock, mock._request_lock)
        self.assertIsNone(real._last_request_at)
        self.assertIsNone(mock._last_request_at)

        with patch("kiwoom_monitor.infrastructure.kiwoom_rest.client.time.monotonic", return_value=100.), patch("kiwoom_monitor.infrastructure.kiwoom_rest.client.time.sleep") as sleep:
            real._wait_for_request_slot()
            mock._wait_for_request_slot()
            sleep.assert_not_called()
            real._wait_for_request_slot()
            self.assertAlmostEqual(.2, sleep.call_args.args[0])
            mock._wait_for_request_slot()
            self.assertAlmostEqual(1., sleep.call_args.args[0])
            self.assertEqual(2, sleep.call_count)

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

    def test_token_failure_preserves_provider_code_and_message(self) -> None:
        client = KiwoomRestClient(
            KiwoomSettings("key", "secret", "mock"),
            opener=lambda request, timeout: FakeResponse({
                "return_code": 9001, "return_msg": "등록 IP를 확인하세요",
            }),
            request_interval_seconds=0,
        )

        with self.assertRaisesRegex(
            RuntimeError, r"CODE=9001, MESSAGE=등록 IP를 확인하세요",
        ):
            client.get_access_token()

    def test_waits_for_configured_request_interval(self) -> None:
        clock = [100.0]
        sent = []

        def opener(request, timeout):
            sent.append((clock[0], request.get_header("Api-id")))
            return FakeResponse({"token": "test-token", "expires_dt": "20260815090000", "items": []})

        def advance(seconds):
            clock[0] += seconds

        client = KiwoomRestClient(
            KiwoomSettings("key", "secret", "mock"), opener=opener,
            clock=lambda: datetime(2026, 8, 14, tzinfo=UTC), request_interval_seconds=2.0,
        )
        with patch("kiwoom_monitor.infrastructure.kiwoom_rest.client.time.monotonic", side_effect=lambda: clock[0]), \
                patch("kiwoom_monitor.infrastructure.kiwoom_rest.client.time.sleep", side_effect=advance):
            client.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "1"})
            clock[0] += 0.75
            client.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "1"})

        self.assertEqual([(100.0, None), (102.0, "ka00198"), (104.0, "ka00198")], sent)

    def test_retries_when_the_remote_host_resets_the_connection(self) -> None:
        responses = iter(
            [
                FakeResponse({"token": "test-token", "expires_dt": "20260815090000"}),
                ConnectionResetError(10054, "connection reset"),
                FakeResponse({"items": []}),
            ]
        )

        def opener(_request: object, timeout: int) -> FakeResponse:
            response = next(responses)
            if isinstance(response, BaseException):
                raise response
            return response

        client = KiwoomRestClient(
            KiwoomSettings("key", "secret", "mock"),
            opener=opener,
            clock=lambda: datetime(2026, 8, 14, tzinfo=UTC),
            request_interval_seconds=0,
        )
        with patch("kiwoom_monitor.infrastructure.kiwoom_rest.client.time.sleep") as sleep:
            self.assertEqual({"items": []}, client.request("ka00198", "/api/dostk/stkinfo", {"qry_tp": "5"}))
        sleep.assert_called_once_with(1)
