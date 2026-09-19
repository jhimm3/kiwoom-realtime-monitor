from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from kiwoom_monitor.domain.order_contract import OrderIntent, OrderSide, OrderType
from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import (
    KiwoomMockExecutionTransport,
    SubmissionUnknown,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings


NOW = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)


class _Client:
    def __init__(self, environment: str = "mock", response: dict[str, object] | None = None) -> None:
        self.environment = environment
        self.response = response or {"return_code": 0, "ord_no": "0000123"}
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request_once(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]:
        self.calls.append((api_id, path, body))
        return self.response


class _Response:
    headers: dict[str, str] = {}

    def __init__(self, body: dict[str, object]) -> None:
        self.body = body

    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def read(self) -> bytes: return json.dumps(self.body).encode()


def _intent(side: OrderSide = OrderSide.BUY) -> OrderIntent:
    return OrderIntent(
        "intent-1", "run-1", "decision-1", "mock-account", "mock", "005930", "KRX",
        side, 2, OrderType.LIMIT, 70_000, NOW, NOW + timedelta(minutes=1), "policy-v1",
    )


class MockExecutionTests(unittest.TestCase):
    def test_real_credentials_are_rejected_before_any_order(self) -> None:
        with self.assertRaisesRegex(ValueError, "mock credentials"):
            KiwoomMockExecutionTransport(_Client("real"))

    def test_buy_and_cancel_use_official_order_contract(self) -> None:
        client = _Client()
        transport = KiwoomMockExecutionTransport(client)
        submission = transport.submit(_intent())
        transport.cancel(_intent(), submission.broker_order_id)

        self.assertEqual("kt10000", client.calls[0][0])
        self.assertEqual("/api/dostk/ordr", client.calls[0][1])
        self.assertEqual("KRX", client.calls[0][2]["dmst_stex_tp"])
        self.assertEqual("2", client.calls[0][2]["ord_qty"])
        self.assertEqual("kt10003", client.calls[1][0])
        self.assertEqual("0000123", client.calls[1][2]["orig_ord_no"])

    def test_network_loss_is_not_retried(self) -> None:
        calls = []

        def opener(_request, timeout):
            del timeout
            calls.append(1)
            if len(calls) == 1:
                return _Response({"token": "token", "expires_dt": "20260915000000"})
            raise TimeoutError("lost response")

        client = KiwoomRestClient(
            KiwoomSettings("key", "secret", "mock"), opener=opener,
            clock=lambda: NOW, request_interval_seconds=0,
        )
        with self.assertRaises(SubmissionUnknown):
            KiwoomMockExecutionTransport(client).submit(_intent())
        self.assertEqual(2, len(calls))


if __name__ == "__main__":
    unittest.main()
