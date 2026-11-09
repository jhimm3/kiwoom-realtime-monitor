"""One-shot Kiwoom stock-order transports; never retry or fail over orders."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from kiwoom_monitor.domain.time_basis import kst_now, as_kst

from kiwoom_monitor.domain.order_contract import (
    BrokerAmendmentSubmission, BrokerSubmission, LocalRehearsalOrderIntent, OrderIntent, OrderSide, OrderType,
    SorBrokerSubmission,
)

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


class AmendmentResponseUnknown(SubmissionUnknown):
    """Keep a reported new number for reconciliation without treating it as accepted."""

    def __init__(self, message: str, *, original_broker_order_id: str, reported_broker_order_id: str) -> None:
        super().__init__(message)
        self.original_broker_order_id = original_broker_order_id
        self.reported_broker_order_id = reported_broker_order_id


class SubmissionRejected(RuntimeError):
    pass


class OneShotOrderClient(Protocol):
    @property
    def environment(self) -> str: ...

    def request_once(self, api_id: str, path: str, body: dict[str, object]) -> dict[str, object]: ...


class _StockOrderWire:
    def __init__(self, request_once, *, now_provider=None) -> None:
        self._request_once = request_once
        self._now = (lambda: as_kst(now_provider())) if now_provider else kst_now

    def submit(self, intent: OrderIntent) -> BrokerSubmission:
        self._require_route(intent)
        api_id = BUY_API_ID if intent.side is OrderSide.BUY else SELL_API_ID
        body: dict[str, object] = {
            "dmst_stex_tp": intent.venue,
            "stk_cd": intent.symbol,
            "ord_qty": str(intent.quantity),
            "ord_uv": str(intent.limit_price or ""),
            "trde_tp": "0" if intent.order_type is OrderType.LIMIT else "3",
            "cond_uv": "",
        }
        response = self._send(api_id, body)
        if intent.venue == 'SOR':
            venue = response.get('dmst_stex_tp')
            return SorBrokerSubmission(str(response['ord_no']), self._now(),
                                       broker_venue=venue if venue in ('KRX', 'NXT') else None)
        self._check_response_venue(intent, response)
        return BrokerSubmission(str(response.get("ord_no") or ""), self._now())

    def cancel(self, intent: OrderIntent, broker_order_id: str, quantity: int = 0) -> BrokerSubmission:
        self._require_route(intent)
        if not broker_order_id.strip():
            raise ValueError("broker_order_id is required before cancellation")
        response = self._send(CANCEL_API_ID, {
            "dmst_stex_tp": intent.venue,
            "orig_ord_no": broker_order_id,
            "stk_cd": intent.symbol,
            "cncl_qty": str(max(0, quantity)),
        })
        self._check_response_venue(intent, response)
        return BrokerSubmission(str(response.get("ord_no") or ""), self._now())

    def amend(self, intent: OrderIntent, broker_order_id: str, quantity: int, limit_price: int) -> BrokerAmendmentSubmission:
        """One explicit LIMIT amendment; native authority and reconciliation belong to the caller."""
        self._require_route(intent)
        if (intent.order_type is not OrderType.LIMIT
                or not isinstance(broker_order_id, str) or not broker_order_id.strip()
                or type(quantity) is not int or not 0 < quantity <= intent.quantity
                or type(limit_price) is not int or limit_price <= 0):
            raise ValueError('amendment requires a limit order and explicit remaining quantity/price')
        original = broker_order_id.strip()
        response = self._send(MODIFY_API_ID, {
            'dmst_stex_tp': intent.venue, 'orig_ord_no': original, 'stk_cd': intent.symbol,
            'mdfy_qty': str(quantity), 'mdfy_uv': str(limit_price), 'mdfy_cond_uv': '',
        })
        raw_quantity = response.get('mdfy_qty')
        try:
            text = str(raw_quantity).strip().removeprefix('+')
            acknowledged_quantity = int(text) if type(raw_quantity) in (str, int) and text.isascii() and text.isdecimal() else 0
        except ValueError:
            acknowledged_quantity = 0
        default_venue = 'KRX' if intent.environment == 'mock' else None
        if (not 0 < acknowledged_quantity <= quantity or response.get('base_orig_ord_no') != original
                or response.get('dmst_stex_tp', default_venue) != intent.venue or type(response.get('ord_no')) is not str):
            raise AmendmentResponseUnknown('amendment response cannot be bound to its original order and quantity',
                original_broker_order_id=original, reported_broker_order_id=str(response.get('ord_no') or ''))
        try:
            return BrokerAmendmentSubmission(broker_order_id=str(response['ord_no']).strip(), accepted_at=self._now(),
                original_broker_order_id=original, quantity=acknowledged_quantity)
        except ValueError as error:
            raise AmendmentResponseUnknown('amendment response did not identify its new order',
                original_broker_order_id=original, reported_broker_order_id=str(response.get('ord_no') or '')) from error

    def _send(self, api_id: str, body: dict[str, object]) -> dict[str, object]:
        try:
            response = self._request_once(api_id, ORDER_PATH, body)
        except KiwoomSubmissionUnknownError as error:
            raise SubmissionUnknown(str(error)) from error
        except KiwoomApiError as error:
            raise SubmissionRejected(str(error)) from error
        if response.get("return_code") not in (None, 0, "0"):
            raise SubmissionRejected(str(response.get("return_msg") or "broker rejected order"))
        if not str(response.get("ord_no") or "").strip():
            raise SubmissionUnknown("broker response did not include an order number")
        return response

    @staticmethod
    def _check_response_venue(intent, response):
        if (intent.environment == 'real' or intent.venue == 'NXT') and response.get('dmst_stex_tp') != intent.venue:
            raise SubmissionUnknown('broker response did not confirm the requested venue')


class KiwoomMockExecutionTransport(_StockOrderWire):
    def __init__(self, client: OneShotOrderClient, *, now_provider=None) -> None:
        if client.environment != "mock":
            raise ValueError("mock order transport requires mock credentials")
        self._client = client
        super().__init__(lambda *args: client.request_once(*args), now_provider=now_provider)

    @staticmethod
    def _require_route(intent):
        if intent.environment != "mock" or intent.venue != "KRX":
            raise ValueError("only KRX mock orders are supported")


class KiwoomRealExecutionTransport(_StockOrderWire):
    """Real account wire contract. The runtime must authorize the account before use."""

    def __init__(self, client: OneShotOrderClient, *, account_ref: str, now_provider=None) -> None:
        if client.environment != 'real' or not account_ref.strip():
            raise ValueError('real order transport requires a real account binding')
        self._account_ref = account_ref
        super().__init__(client.request_once, now_provider=now_provider)

    def _require_route(self, intent):
        if ((intent.environment, intent.account_ref) != ('real', self._account_ref)
                or intent.venue not in {'KRX', 'NXT', 'SOR'} or intent.order_type is not OrderType.LIMIT):
            raise ValueError('real order transport requires a matching account and an explicit limit order')

    def cancel(self, intent, broker_order_id, quantity=0):
        self._require_physical_route(intent)
        return super().cancel(intent, broker_order_id, quantity)

    def amend(self, intent, broker_order_id, quantity, limit_price):
        self._require_physical_route(intent)
        return super().amend(intent, broker_order_id, quantity, limit_price)

    @staticmethod
    def _require_physical_route(intent):
        if intent.venue not in {'KRX', 'NXT'}:
            raise ValueError('an original physical order venue is required')


class LocalRehearsalOrderTransport(KiwoomRealExecutionTransport):
    """Use only the supplied in-process fake peer. No client, token or URL is configured."""

    def __init__(self, fake_peer, *, account_ref, now_provider=None):
        if isinstance(fake_peer, KiwoomRestClient) or isinstance(getattr(fake_peer.request_once, '__self__', None), KiwoomRestClient):
            raise ValueError('local rehearsal does not accept a live Kiwoom client')
        super().__init__(fake_peer, account_ref=account_ref, now_provider=now_provider)

    def _require_route(self, intent):
        if not isinstance(intent, LocalRehearsalOrderIntent):
            raise ValueError('local rehearsal requires an isolated order intent')
        super()._require_route(intent)


class LocalAutomationOrderTransport(_StockOrderWire):
    """An in-process fake peer for the ordinary automation runner."""

    def __init__(self, fake_peer, *, now_provider):
        if isinstance(fake_peer, KiwoomRestClient):
            raise ValueError('local automation requires a fake peer')
        super().__init__(fake_peer.request_once, now_provider=now_provider)

    @staticmethod
    def _require_route(intent):
        if intent.environment != 'mock' or intent.venue not in {'KRX', 'NXT', 'SOR'} or intent.order_type is not OrderType.LIMIT:
            raise ValueError('local automation requires a KRX, NXT or SOR limit order')


def make_mock_transport(client: KiwoomRestClient, *, now_provider=None) -> KiwoomMockExecutionTransport:
    return KiwoomMockExecutionTransport(client, now_provider=now_provider)
