"""Conservative mock-broker order lifecycle and reconciliation policy."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Protocol

from kiwoom_monitor.application.market_session_schedule import mock_order_entry_decision
from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot,
    BrokerOrderSnapshot,
    BrokerSubmission,
    OrderIntent,
    OrderSide,
    OrderState,
    TERMINAL_ORDER_STATES,
    order_event,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionRejected, SubmissionUnknown
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRecord, ExecutionRepository


class MockOrderTransport(Protocol):
    def submit(self, intent: OrderIntent) -> BrokerSubmission: ...
    def cancel(self, intent: OrderIntent, broker_order_id: str, quantity: int = 0) -> BrokerSubmission: ...


class OrderLifecycle:
    def __init__(
        self, repository: ExecutionRepository, transport: MockOrderTransport | None,
        *, now_provider: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._repository = repository
        self._transport = transport
        self._now = now_provider

    def queue(self, intent: OrderIntent) -> ExecutionRecord:
        record = self._repository.create(intent)
        if self._repository.events(intent.intent_id):
            return record
        event = order_event(
            intent.intent_id, "QUEUED", OrderState.QUEUED,
            intent.created_at, self._now(),
        )
        return self._repository.apply(record, event)

    def submit(
        self, intent_id: str, account: AccountSnapshot, *, reference_price: int | None = None,
    ) -> ExecutionRecord:
        record = self._required(intent_id)
        if record.state is not OrderState.QUEUED:
            return record
        now = self._now()
        rejection = self._preflight_error(record.intent, account, now, reference_price)
        if rejection:
            return self._apply(record, "PRECHECK_REJECTED", OrderState.REJECTED, reason=rejection)
        transport = self._required_transport()

        # Persist unknown before crossing the network boundary. A crash can then cause a
        # missed submission, but never an automatic duplicate order.
        record = self._apply(record, "SUBMISSION_STARTED", OrderState.SUBMISSION_UNKNOWN)
        try:
            submission = transport.submit(record.intent)
        except SubmissionUnknown as error:
            return self._apply(record, "SUBMISSION_RESPONSE_UNKNOWN", OrderState.SUBMISSION_UNKNOWN, reason=str(error))
        except SubmissionRejected as error:
            return self._apply(record, "SUBMISSION_REJECTED", OrderState.REJECTED, reason=str(error))
        return self._apply(
            record, "ORDER_ACCEPTED", OrderState.ACCEPTED,
            broker_order_id=submission.broker_order_id, occurred_at=submission.accepted_at,
        )

    def cancel(self, intent_id: str, quantity: int = 0) -> ExecutionRecord:
        record = self._required(intent_id)
        if record.state in TERMINAL_ORDER_STATES:
            return record
        if record.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED} or not record.broker_order_id:
            raise ValueError("order must be reconciled and accepted before cancellation")
        transport = self._required_transport()
        record = self._apply(record, "CANCEL_STARTED", OrderState.CANCEL_PENDING)
        try:
            transport.cancel(record.intent, record.broker_order_id, quantity)
        except SubmissionUnknown as error:
            return self._apply(record, "CANCEL_RESPONSE_UNKNOWN", OrderState.CANCEL_PENDING, reason=str(error))
        except SubmissionRejected as error:
            fallback = OrderState.PARTIALLY_FILLED if record.filled_quantity else OrderState.ACCEPTED
            return self._apply(record, "CANCEL_REJECTED", fallback, reason=str(error))
        return self._apply(record, "CANCEL_ACCEPTED", OrderState.CANCEL_PENDING)

    def reconcile(self, intent_id: str, snapshot: BrokerOrderSnapshot) -> ExecutionRecord:
        record = self._required(intent_id)
        intent = record.intent
        if snapshot.account_ref != intent.account_ref or snapshot.symbol != intent.symbol:
            raise ValueError("broker snapshot does not match the order account and symbol")
        if record.broker_order_id and snapshot.broker_order_id != record.broker_order_id:
            raise ValueError("broker snapshot order id does not match")
        if record.last_broker_as_of is not None and snapshot.as_of <= record.last_broker_as_of:
            return record

        broker_order_id = record.broker_order_id or snapshot.broker_order_id
        fill_ids = list(record.fill_ids)
        detailed_filled_quantity = record.detailed_filled_quantity
        broker_reported_filled_quantity = record.broker_reported_filled_quantity
        filled_quantity = max(detailed_filled_quantity, broker_reported_filled_quantity)
        for fill in sorted(snapshot.fills, key=lambda value: (value.occurred_at, value.execution_id)):
            if fill.execution_id in fill_ids:
                continue
            if detailed_filled_quantity + fill.quantity > intent.quantity:
                raise ValueError("broker fills exceed the requested quantity")
            detailed_filled_quantity += fill.quantity
            filled_quantity = max(detailed_filled_quantity, broker_reported_filled_quantity)
            fill_ids.append(fill.execution_id)
            state = OrderState.FILLED if filled_quantity == intent.quantity else OrderState.PARTIALLY_FILLED
            record = self._apply(
                record, "FILL", state, broker_order_id=broker_order_id,
                broker_execution_id=fill.execution_id, quantity=fill.quantity, price=fill.price,
                occurred_at=fill.occurred_at, broker_as_of=snapshot.as_of,
                filled_quantity=filled_quantity, fill_ids=tuple(fill_ids),
                detailed_filled_quantity=detailed_filled_quantity,
                broker_reported_filled_quantity=broker_reported_filled_quantity,
                last_broker_as_of=snapshot.as_of,
            )

        if snapshot.filled_quantity > intent.quantity:
            raise ValueError("broker cumulative fill exceeds the requested quantity")
        if snapshot.filled_quantity > broker_reported_filled_quantity:
            before = filled_quantity
            broker_reported_filled_quantity = snapshot.filled_quantity
            filled_quantity = max(detailed_filled_quantity, broker_reported_filled_quantity)
            record = self._apply(
                record, "BROKER_FILL_AGGREGATE",
                OrderState.FILLED if filled_quantity == intent.quantity else OrderState.PARTIALLY_FILLED,
                broker_order_id=broker_order_id, quantity=filled_quantity - before,
                reason="broker cumulative fill; exact execution identity unavailable",
                occurred_at=snapshot.as_of, broker_as_of=snapshot.as_of,
                filled_quantity=filled_quantity, fill_ids=tuple(fill_ids),
                detailed_filled_quantity=detailed_filled_quantity,
                broker_reported_filled_quantity=broker_reported_filled_quantity,
                last_broker_as_of=snapshot.as_of,
            )

        final_state = self._snapshot_state(snapshot, filled_quantity, intent.quantity)
        if record.state is not final_state or record.last_broker_as_of != snapshot.as_of:
            record = self._apply(
                record, "BROKER_RECONCILED", final_state,
                broker_order_id=broker_order_id, broker_as_of=snapshot.as_of,
                filled_quantity=filled_quantity, fill_ids=tuple(fill_ids),
                detailed_filled_quantity=detailed_filled_quantity,
                broker_reported_filled_quantity=broker_reported_filled_quantity,
                last_broker_as_of=snapshot.as_of,
            )
        return record

    @staticmethod
    def _snapshot_state(snapshot: BrokerOrderSnapshot, filled: int, requested: int) -> OrderState:
        if filled >= requested:
            return OrderState.FILLED
        if snapshot.state is OrderState.CANCELLED:
            return OrderState.CANCELLED
        if snapshot.state is OrderState.REJECTED:
            return OrderState.REJECTED
        if snapshot.state is OrderState.CANCEL_PENDING:
            return OrderState.CANCEL_PENDING
        return OrderState.PARTIALLY_FILLED if filled else OrderState.ACCEPTED

    @staticmethod
    def _preflight_error(
        intent: OrderIntent, account: AccountSnapshot, now: datetime, reference_price: int | None,
    ) -> str:
        session_decision = mock_order_entry_decision(
            now,
            environment=intent.environment,
            venue=intent.venue,
            order_type=intent.order_type.value,
        )
        if not session_decision.allowed:
            return session_decision.evidence
        if account.account_ref != intent.account_ref:
            return "account mismatch"
        if account.as_of > now:
            return "account snapshot is from the future"
        if now >= intent.expires_at:
            return "intent expired before transmission"
        if intent.side is OrderSide.SELL:
            if int(account.positions.get(intent.symbol, 0)) < intent.quantity:
                return "insufficient position"
            return ""
        price = intent.limit_price or reference_price
        if price is None or price <= 0:
            return "market order requires a positive reference price for cash reservation"
        required = price * intent.quantity
        if required > account.available_cash_won - account.reserved_open_buy_won:
            return "insufficient available cash after open-order reservation"
        return ""

    def _required(self, intent_id: str) -> ExecutionRecord:
        record = self._repository.load(intent_id)
        if record is None:
            raise KeyError(f"unknown order intent: {intent_id}")
        return record

    def _required_transport(self) -> MockOrderTransport:
        if self._transport is None:
            raise RuntimeError("order transport is not configured")
        return self._transport

    def _apply(self, record: ExecutionRecord, event_type: str, state: OrderState, **values: object) -> ExecutionRecord:
        received_at = self._now()
        occurred_at = values.pop("occurred_at", received_at)
        event_fields = {
            key: values[key] for key in (
                "broker_order_id", "broker_execution_id", "quantity", "price", "reason", "broker_as_of",
            ) if key in values
        }
        event = order_event(
            record.intent.intent_id, event_type, state, occurred_at, received_at, **event_fields,
        )
        return self._repository.apply(record, event, **values)
