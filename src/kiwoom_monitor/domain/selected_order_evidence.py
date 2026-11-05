"""Versioned source facts for offline recovery inspection."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from typing import Any

from .order_contract import OrderPlanRef, OrderSide, OrderState, OrderType, PlannedOrderIntent, TERMINAL_ORDER_STATES

VERSION = "selected-order-evidence/v1"
COLLECTION = "offline_selected_order_evidence"
OWNER = "offline"
RECOVERY_REQUIRED = "OFFLINE_RECOVERY_REQUIRED"

INTENT_FIELDS = frozenset({
    "intent_id", "run_id", "decision_id", "account_ref", "environment", "symbol", "venue", "side",
    "quantity", "order_type", "limit_price", "created_at", "expires_at", "policy_version", "plan",
})
ORDER_FIELDS = INTENT_FIELDS | {
    "state", "broker_order_id", "filled_quantity", "fill_ids", "last_broker_as_of", "updated_at",
    "detailed_filled_quantity", "broker_reported_filled_quantity",
}
EVENT_FIELDS = frozenset({
    "event_id", "intent_id", "event_type", "state", "occurred_at", "received_at", "broker_order_id",
    "broker_execution_id", "quantity", "price", "broker_as_of",
})
PLAN_FIELDS = frozenset({"version", "plan_id", "policy_ref", "run_id", "symbol", "revision", "quantity", "fill_cursor"})
PACKET_FIELDS = frozenset({
    "version", "source_instance", "operation_id", "account_ref", "run_id", "source_spec_id",
    "stopped_at", "order", "events", "source_cursor", "plan_position",
})


def _fields(value: Any, names: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.keys() != names:
        raise ValueError("invalid selected-order evidence fields")
    return value


def _text(value: Any, *, optional: bool = False) -> str:
    if not isinstance(value, str) or (not optional and not value.strip()):
        raise ValueError("invalid selected-order evidence identity")
    return value


def _count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("invalid selected-order evidence count")
    return value


def _time(value: Any) -> datetime:
    observed = datetime.fromisoformat(_text(value))
    if observed.utcoffset() is None:
        raise ValueError("selected-order evidence timestamps require an offset")
    return observed


def _intent(order: dict[str, Any]) -> PlannedOrderIntent:
    values = {key: order[key] for key in INTENT_FIELDS}
    for key in ("intent_id", "run_id", "decision_id", "account_ref", "symbol", "policy_version"):
        _text(values[key])
    values.update(
        side=OrderSide(values["side"]), order_type=OrderType(values["order_type"]),
        quantity=_count(values["quantity"]), limit_price=_count(values["limit_price"]),
        created_at=_time(values["created_at"]), expires_at=_time(values["expires_at"]),
        plan=OrderPlanRef(**_fields(values["plan"], frozenset({"plan_id", "policy_ref", "expected_revision"}))),
    )
    return PlannedOrderIntent(**values)


def encode_selected_order_evidence(packet: dict[str, Any]) -> tuple[bytes, str]:
    """Validate the first supported selection and return its canonical bytes and digest."""
    _fields(packet, PACKET_FIELDS)
    if packet["version"] != VERSION:
        raise ValueError("unsupported selected-order evidence version")
    for key in ("source_instance", "operation_id", "account_ref", "run_id", "source_spec_id"):
        _text(packet[key])
    _time(packet["stopped_at"])
    order = _fields(packet["order"], ORDER_FIELDS)
    intent = _intent(order)
    state = OrderState(order["state"])
    if ((intent.account_ref, intent.run_id) != (packet["account_ref"], packet["run_id"])
            or intent.side is not OrderSide.BUY or intent.plan.expected_revision != 0
            or state in TERMINAL_ORDER_STATES):
        raise ValueError("selected order must be an unresolved original buy in the requested account and run")
    _time(order["updated_at"])
    if order["last_broker_as_of"] is not None:
        _time(order["last_broker_as_of"])
    _text(order["broker_order_id"], optional=True)
    detailed = _count(order["detailed_filled_quantity"])
    reported = _count(order["broker_reported_filled_quantity"])
    if _count(order["filled_quantity"]) != max(detailed, reported) or max(detailed, reported) > intent.quantity:
        raise ValueError("selected order fill quantities disagree")

    if not isinstance(packet["events"], list):
        raise ValueError("invalid selected-order event list")
    event_ids, fill_ids = set(), []
    previous_sequence = fill_cursor = fill_quantity = 0
    latest_state = OrderState.QUEUED
    for event in packet["events"]:
        _fields(event, EVENT_FIELDS | {"accepted_sequence"})
        sequence = _count(event["accepted_sequence"])
        event_id = _text(event["event_id"])
        if event["intent_id"] != intent.intent_id or sequence <= previous_sequence or event_id in event_ids:
            raise ValueError("selected-order event identity or sequence disagrees")
        previous_sequence = sequence
        event_ids.add(event_id)
        latest_state = OrderState(event["state"])
        _time(event["occurred_at"])
        _time(event["received_at"])
        if event["broker_as_of"] is not None:
            _time(event["broker_as_of"])
        _text(event["broker_order_id"], optional=True)
        execution_id = _text(event["broker_execution_id"], optional=True)
        quantity = _count(event["quantity"])
        price = _count(event["price"])
        if _text(event["event_type"]) == "FILL":
            if not execution_id or not quantity or not price or execution_id in fill_ids:
                raise ValueError("invalid selected-order detailed fill")
            fill_ids.append(execution_id)
            fill_quantity += quantity
            fill_cursor = sequence

    position = _fields(packet["plan_position"], PLAN_FIELDS)
    if ((position["version"], position["plan_id"], position["policy_ref"], position["run_id"], position["symbol"])
            != ("mock_plan_position/v1", intent.plan.plan_id, intent.plan.policy_ref, intent.run_id, intent.symbol)
            or _count(position["revision"]) < 1
            or (_count(position["quantity"]), _count(position["fill_cursor"])) != (fill_quantity, fill_cursor)
            or (detailed, order["fill_ids"]) != (fill_quantity, fill_ids)
            or latest_state != state or _count(packet["source_cursor"]) < previous_sequence):
        raise ValueError("selected order, events and source plan position disagree")

    encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return encoded, sha256(encoded).hexdigest()
