"""Versioned source facts for offline recovery inspection."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from typing import Any

from .order_contract import OrderPlanRef, OrderSide, OrderState, OrderType, PlannedOrderIntent, TERMINAL_ORDER_STATES

VERSION = "selected-order-evidence/v1"
AMENDED_VERSION = "selected-order-evidence/v2"
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
AMENDED_ORDER_FIELDS = ORDER_FIELDS | {"broker_legs", "amendment", "broker_revision"}
LEG_FIELDS = frozenset({
    "broker_order_id", "original_broker_order_id", "ordered_quantity", "limit_price", "state",
    "transferred_quantity", "detailed_filled_quantity", "broker_reported_filled_quantity", "fill_ids", "last_broker_as_of",
})
AMENDMENT_FIELDS = frozenset({
    "broker_order_id", "quantity", "limit_price", "started_at", "status", "reported_broker_order_id",
    "accepted_quantity", "observed_fills",
})
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


def _validate_physical_facts(order, intent, events):
    """Check retained source facts, without treating them as target execution approval."""
    if not isinstance(order['broker_legs'], list) or not order['broker_legs'] or not _count(order['broker_revision']):
        raise ValueError('amended selected-order physical facts are missing')
    legs, transfers, fills = {}, {}, {}
    for event in events:
        if event['event_type'] == 'FILL':
            fills.setdefault(event['broker_order_id'], []).append(event)
    for raw in order['broker_legs']:
        leg = _fields(raw, LEG_FIELDS)
        number, parent = _text(leg['broker_order_id']), _text(leg['original_broker_order_id'], optional=True)
        if number in legs or parent and parent not in legs or not legs and parent or legs and not parent:
            raise ValueError('amended selected-order parent lineage disagrees')
        ordered, price = _count(leg['ordered_quantity']), _count(leg['limit_price'])
        detail, reported, moved = (_count(leg[name]) for name in
            ('detailed_filled_quantity', 'broker_reported_filled_quantity', 'transferred_quantity'))
        if not ordered or not price or moved + max(detail, reported) > ordered:
            raise ValueError('amended selected-order physical allocation disagrees')
        OrderState(leg['state'])
        if leg['last_broker_as_of'] is not None: _time(leg['last_broker_as_of'])
        facts = fills.get(number, [])
        if (leg['fill_ids'] != [event['broker_execution_id'] for event in facts]
                or detail != sum(event['quantity'] for event in facts)):
            raise ValueError('amended selected-order physical fills disagree')
        if not legs and (number, ordered, price) != (order['broker_order_id'], intent.quantity, intent.limit_price):
            raise ValueError('amended selected-order original allocation changed')
        legs[number] = leg
        if parent: transfers[parent] = transfers.get(parent, 0) + ordered
    if (set(fills) - legs.keys() or any(leg['transferred_quantity'] != transfers.get(number, 0) for number, leg in legs.items())
            or order['detailed_filled_quantity'] != sum(leg['detailed_filled_quantity'] for leg in legs.values())
            or order['broker_reported_filled_quantity'] != sum(leg['broker_reported_filled_quantity'] for leg in legs.values())
            or order['filled_quantity'] != sum(max(leg['detailed_filled_quantity'], leg['broker_reported_filled_quantity'])
                                              for leg in legs.values()) or order['filled_quantity'] > intent.quantity):
        raise ValueError('amended selected-order totals or transfers disagree')
    command = order['amendment']
    if not isinstance(command, dict): raise ValueError('amended selected-order command is missing')
    _fields(command, AMENDMENT_FIELDS | ({'assessment'} if 'assessment' in command else set()))
    if 'assessment' in command and not isinstance(command['assessment'], dict):
        raise ValueError('invalid retained amendment assessment')
    started = _time(command['started_at'])
    quantity, accepted, price = (_count(command[name]) for name in ('quantity', 'accepted_quantity', 'limit_price'))
    parent, child = _text(command['broker_order_id']), _text(command['reported_broker_order_id'], optional=True)
    if (parent not in legs or not quantity or quantity > intent.quantity or not price or accepted > quantity
            or _text(command['status']) not in {'PENDING', 'UNKNOWN', 'REJECTED', 'ACCEPTED'}
            or (command['status'] == 'ACCEPTED' and (not accepted or child not in legs
                or (legs[child]['original_broker_order_id'], legs[child]['ordered_quantity']) != (parent, accepted)))
            or (command['status'] != 'ACCEPTED' and accepted)):
        raise ValueError('amended selected-order command facts disagree')
    current_parent, latest_start, observations, pending = '', None, {}, {}
    applied = {(event['broker_order_id'], event['broker_execution_id']): event for event in events if event['event_type'] == 'FILL'}
    for event in events:
        if event['event_type'] == 'AMENDMENT_STARTED':
            current_parent, latest_start, pending = event['broker_order_id'], event, {}
        elif event['event_type'] == 'AMENDMENT_FILL_OBSERVED':
            key = (event['broker_order_id'], event['broker_execution_id'])
            prefix, _, _ = _text(event['reason']).partition(';')
            revision = int(prefix.removeprefix('broker_revision='))
            if (not 0 < revision <= order['broker_revision'] or not current_parent
                    or event['reason'] != f'broker_revision={revision};amendment_parent={current_parent}'
                    or current_parent not in legs or not key[0] or not key[1] or not event['quantity'] or not event['price']
                    or key in observations):
                raise ValueError('amended selected-order unbound observation disagrees')
            observations[key] = event
            if key in applied:
                fill = applied[key]
                if any(fill[name] != event[name] for name in ('quantity', 'price', 'occurred_at')):
                    raise ValueError('amended selected-order observed and attributed fills disagree')
            else:
                pending[key] = event
    if (latest_start is None or (latest_start['broker_order_id'], latest_start['quantity'], latest_start['price'],
            _time(latest_start['received_at'])) != (parent, quantity, price, started)):
        raise ValueError('amended selected-order command start disagrees')
    expected = [json.dumps(key, separators=(',', ':')) for key in pending]
    if command['observed_fills'] != expected:
        raise ValueError('amended selected-order pending observations disagree')
    active = [leg for leg in legs.values() if OrderState(leg['state']) not in TERMINAL_ORDER_STATES
              and leg['ordered_quantity'] - leg['transferred_quantity']
              - max(leg['detailed_filled_quantity'], leg['broker_reported_filled_quantity']) > 0]
    state = (OrderState.SUBMISSION_UNKNOWN if command['status'] in {'PENDING', 'UNKNOWN'} or pending else
        OrderState.CANCEL_PENDING if any(leg['state'] == 'CANCEL_PENDING' for leg in active) else
        OrderState.PARTIALLY_FILLED if active and order['filled_quantity'] else
        OrderState.ACCEPTED if active else OrderState.FILLED if order['filled_quantity'] >= intent.quantity else OrderState.CANCELLED)
    if order['state'] != state.value:
        raise ValueError('amended selected-order state disagrees with retained physical facts')


def encode_selected_order_evidence(packet: dict[str, Any]) -> tuple[bytes, str]:
    """Validate a supported selection and return its canonical bytes and digest."""
    _fields(packet, PACKET_FIELDS)
    if _text(packet["version"]) not in {VERSION, AMENDED_VERSION}:
        raise ValueError("unsupported selected-order evidence version")
    for key in ("source_instance", "operation_id", "account_ref", "run_id", "source_spec_id"):
        _text(packet[key])
    _time(packet["stopped_at"])
    physical = packet['version'] == AMENDED_VERSION
    order = _fields(packet["order"], AMENDED_ORDER_FIELDS if physical else ORDER_FIELDS)
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
    if (not physical and _count(order["filled_quantity"]) != max(detailed, reported)
            or max(detailed, reported, _count(order['filled_quantity'])) > intent.quantity):
        raise ValueError("selected order fill quantities disagree")

    if not isinstance(packet["events"], list):
        raise ValueError("invalid selected-order event list")
    event_ids, fill_ids, fill_keys = set(), [], set()
    previous_sequence = fill_cursor = fill_quantity = 0
    latest_state = OrderState.QUEUED
    for event in packet["events"]:
        observed = physical and event.get('event_type') == 'AMENDMENT_FILL_OBSERVED'
        _fields(event, EVENT_FIELDS | {"accepted_sequence"} | ({'reason'} if observed else set()))
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
            key = (event['broker_order_id'], execution_id) if physical else execution_id
            if not execution_id or not quantity or not price or key in fill_keys:
                raise ValueError("invalid selected-order detailed fill")
            fill_keys.add(key)
            fill_ids.append(execution_id)
            fill_quantity += quantity
            fill_cursor = sequence
        if not physical and event['event_type'].startswith('AMENDMENT_'):
            raise ValueError('amended selected orders require v2 physical facts')

    if physical:
        _validate_physical_facts(order, intent, packet['events'])

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
