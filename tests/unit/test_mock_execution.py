from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from kiwoom_monitor.domain.order_contract import OrderIntent, OrderSide, OrderType
from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import (
    KiwoomMockExecutionTransport,
    SubmissionRejected,
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

    def test_amend_uses_one_request_and_preserves_original_and_new_numbers(self):
        for side in (OrderSide.BUY, OrderSide.SELL):
            self._verify_amend_ack(side, 1)
            self._verify_amend_ack(side, 2)  # Preserve the acknowledged smaller quantity after a possible fill race.

    def _verify_amend_ack(self, side, requested):
        client = _Client(response={'return_code': 0, 'ord_no': '0000140', 'base_orig_ord_no': '0000139',
                                   'mdfy_qty': '+000000000001', 'dmst_stex_tp': 'KRX'})
        result = KiwoomMockExecutionTransport(client, now_provider=lambda: NOW).amend(
            _intent(side), '0000139', requested, 70_100)
        self.assertEqual(('0000140', '0000139', 1, NOW),
            (result.broker_order_id, result.original_broker_order_id, result.quantity, result.accepted_at))
        self.assertEqual([('kt10002', '/api/dostk/ordr', {
            'dmst_stex_tp': 'KRX', 'orig_ord_no': '0000139', 'stk_cd': '005930',
            'mdfy_qty': str(requested), 'mdfy_uv': '70100', 'mdfy_cond_uv': '',
        })], client.calls)

    def test_amend_rejection_keeps_the_original_order_without_fallback_submission(self):
        client = _Client(response={'return_code': 20, 'return_msg': 'synthetic amendment rejection'})
        with self.assertRaises(SubmissionRejected):
            KiwoomMockExecutionTransport(client).amend(_intent(), '0000139', 1, 70_100)
        self.assertEqual(['kt10002'], [row[0] for row in client.calls])

    def test_amend_ambiguous_acknowledgements_never_retry_or_cancel_and_resubmit(self):
        valid = {'return_code': 0, 'ord_no': '0000140', 'base_orig_ord_no': '0000139',
                 'mdfy_qty': '000000000001', 'dmst_stex_tp': 'KRX'}
        for changed in ({'ord_no': ''}, {'ord_no': '0000139'}, {'base_orig_ord_no': '0000999'},
                        {'mdfy_qty': '0'}, {'mdfy_qty': '2'}, {'mdfy_qty': True}, {'mdfy_qty': '-1'},
                        {'mdfy_qty': '1.0'}, {'mdfy_qty': '1_0'}, {'dmst_stex_tp': 'NXT'}):
            with self.subTest(changed=changed):
                client = _Client(response={**valid, **changed})
                with self.assertRaises(SubmissionUnknown) as unknown:
                    KiwoomMockExecutionTransport(client).amend(_intent(), '0000139', 1, 70_100)
                self.assertEqual(['kt10002'], [row[0] for row in client.calls])
                if changed.get('ord_no') != '':
                    self.assertEqual(('0000139', str(changed.get('ord_no', '0000140'))),
                        (unknown.exception.original_broker_order_id, unknown.exception.reported_broker_order_id))

    def test_amend_requires_explicit_supported_order_remaining_quantity_and_price(self):
        client = _Client()
        transport = KiwoomMockExecutionTransport(client)
        for intent, number, quantity, price in (
            (_intent(), '', 1, 70_100), (_intent(), '0000139', 0, 70_100),
            (_intent(), '0000139', True, 70_100), (_intent(), '0000139', 3, 70_100),
            (_intent(), '0000139', 1, 0), (_intent(), '0000139', 1, True),
            (replace(_intent(), order_type=OrderType.MARKET, limit_price=None), '0000139', 1, 70_100),
        ):
            with self.subTest(number=number, quantity=quantity, price=price, intent=intent):
                with self.assertRaises(ValueError):
                    transport.amend(intent, number, quantity, price)
        self.assertEqual([], client.calls)

    def test_amend_network_loss_keeps_one_mutation_without_automatic_retransmission(self):
        calls = []
        def opener(request, timeout):
            del timeout
            calls.append(request)
            if len(calls) == 1:
                return _Response({'token': 'token', 'expires_dt': '20260915000000'})
            raise TimeoutError('amend response lost')
        client = KiwoomRestClient(KiwoomSettings('key', 'secret', 'mock'), opener=opener,
                                 clock=lambda: NOW, request_interval_seconds=0)
        with self.assertRaises(SubmissionUnknown):
            KiwoomMockExecutionTransport(client).amend(_intent(), '0000139', 1, 70_100)
        self.assertEqual(2, len(calls))  # One token call and exactly one order mutation.


if __name__ == "__main__":
    unittest.main()
