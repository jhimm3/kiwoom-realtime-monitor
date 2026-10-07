"""One-shot Kiwoom mock-order transport; never retries or fails over orders."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from kiwoom_monitor.domain.order_contract import BrokerSubmission, OrderIntent, OrderSide, OrderType

from .client import KiwoomApiError, KiwoomRestClient, KiwoomSubmissionUnknownError


ORDER_PATH = "/api/dostk/ordr"
BUY_API_ID = "kt10000"
SELL_API_ID = "kt10001"
MODIFY_API_ID = "kt10002"
CANCEL_API_ID = "kt10003"
ORDER_API_IDS = frozenset({
    BUY_API_ID, SELL_API_ID, MODIFY_API_ID, CANCEL_API_ID,
    "kt10006", "kt10007", "kt10008", "kt10009",  # domestic credit orders
    "kt50000", "kt50001", "kt50002", "kt50003",  # gold spot orders
})


class SubmissionUnknown(RuntimeError):
    """The request may have reached the broker and must be reconciled before any retry."""


class SubmissionRejected(RuntimeError):
    pass


class OneShotOrderClient(Protocol):
    @property
    def environment(self) -> str: ...

    def request_once(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]: ...


class KiwoomMockExecutionTransport:
    def __init__(self, client: OneShotOrderClient) -> None:
        if client.environment != "mock":
            raise ValueError("mock order transport requires mock credentials")
        self._client = client

    def submit(self, intent: OrderIntent) -> BrokerSubmission:
        if intent.environment != "mock" or intent.venue != "KRX":
            raise ValueError("only KRX mock orders are supported")
        api_id = BUY_API_ID if intent.side is OrderSide.BUY else SELL_API_ID
        body: dict[str, object] = {
            "dmst_stex_tp": "KRX",
            "stk_cd": intent.symbol,
            "ord_qty": str(intent.quantity),
            "ord_uv": str(intent.limit_price or ""),
            "trde_tp": "0" if intent.order_type is OrderType.LIMIT else "3",
            "cond_uv": "",
        }
        response = self._send(api_id, body)
        return BrokerSubmission(str(response.get("ord_no") or ""), datetime.now(timezone.utc))

    def cancel(self, intent: OrderIntent, broker_order_id: str, quantity: int = 0) -> BrokerSubmission:
        if not broker_order_id.strip():
            raise ValueError("broker_order_id is required before cancellation")
        response = self._send(CANCEL_API_ID, {
            "dmst_stex_tp": "KRX",
            "orig_ord_no": broker_order_id,
            "stk_cd": intent.symbol,
            "cncl_qty": str(max(0, quantity)),
        })
        return BrokerSubmission(str(response.get("ord_no") or ""), datetime.now(timezone.utc))

    def _send(self, api_id: str, body: dict[str, object]) -> dict[str, object]:
        try:
            response = self._client.request_once(api_id, ORDER_PATH, body)
        except KiwoomSubmissionUnknownError as error:
            raise SubmissionUnknown(str(error)) from error
        except KiwoomApiError as error:
            raise SubmissionRejected(str(error)) from error
        if response.get("return_code") not in (None, 0, "0"):
            raise SubmissionRejected(str(response.get("return_msg") or "broker rejected order"))
        if not str(response.get("ord_no") or "").strip():
            raise SubmissionUnknown("broker response did not include an order number")
        return response


def make_mock_transport(client: KiwoomRestClient) -> KiwoomMockExecutionTransport:
    return KiwoomMockExecutionTransport(client)
