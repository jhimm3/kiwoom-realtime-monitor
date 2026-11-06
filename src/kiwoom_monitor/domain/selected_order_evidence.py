"""Versioned source facts for offline recovery inspection."""
from __future__ import annotations

from datetime import datetime
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any

from .order_contract import OrderPlanRef, OrderSide, OrderState, OrderType, PlannedOrderIntent, TERMINAL_ORDER_STATES

VERSION = "selected-order-evidence/v1"
AMENDED_VERSION = "selected-order-evidence/v2"
MEMBER_VERSION = "selected-order-evidence/v3"
MANAGEMENT_VERSION = "selected-order-evidence/v4"
PLAN_MANAGEMENT_VERSION = "selected-order-evidence/v5"
COLLECTION = "offline_selected_order_evidence"
OWNER = "offline"
RECOVERY_REQUIRED = "OFFLINE_RECOVERY_REQUIRED"


@dataclass(frozen=True)
class SelectedOrderManagementContext:
    """Explicit source coordinates and current target references, not authority."""
    management_id: str
    operation_id: str
    account_ref: str
    canonical_account_ref: str
    execution_run_id: str
    spec_id: str
    admission_id: str
    intent_id: str
    member_spec_id: str | None
    credential_profile_id: str
    credential_revision: int
    binding_revision: int
    binding_verified_at: str
    packet_sha256: str
    policy_closure_sha256: str
    source_admission_version: str
    source_account_selection_id: str | None
    version: str = 'selected-order-management-context/v1'

    def __post_init__(self):
        from uuid import UUID
        if self.version != 'selected-order-management-context/v1':
            raise ValueError('unsupported selected management context')
        for name in ('account_ref', 'canonical_account_ref'):
            if str(UUID(getattr(self, name))) != getattr(self, name):
                raise ValueError('selected management requires explicit account coordinates')
        for name in ('management_id', 'operation_id', 'execution_run_id', 'spec_id', 'admission_id',
                     'intent_id', 'credential_profile_id'):
            if type(getattr(self, name)) is not str or not getattr(self, name).strip():
                raise ValueError('selected management context reference is missing')
        if (self.member_spec_id is not None and self.member_spec_id != self.spec_id
                or type(self.credential_revision) is not int or self.credential_revision < 1
                or type(self.binding_revision) is not int or self.binding_revision < 1):
            raise ValueError('selected management target epoch or original member conflicts')
        if ((self.source_admission_version == 'mock_automation_admission/v1' and self.source_account_selection_id is not None)
                or (self.source_admission_version == 'mock_automation_account_admission/v1'
                    and not self.source_account_selection_id)
                or self.source_admission_version not in {'mock_automation_admission/v1', 'mock_automation_account_admission/v1'}):
            raise ValueError('selected management original admission coordinates conflict')
        at = datetime.fromisoformat(self.binding_verified_at)
        if at.utcoffset() is None:
            raise ValueError('selected management binding time must be aware')
        for name in ('packet_sha256', 'policy_closure_sha256'):
            value = getattr(self, name)
            if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('selected management requires its immutable closure digest')

    @property
    def checkpoint_key(self):
        return 'member:' + self.spec_id if self.source_account_selection_id is not None else 'current'

    def to_dict(self):
        return asdict(self)


def selected_order_management_context_from_dict(value):
    result = SelectedOrderManagementContext(**value)
    if result.to_dict() != value:
        raise ValueError('selected management context must be canonical')
    return result

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
MEMBER_FIELDS = frozenset({"member_spec_id", "member_admission_id", "account_selection_id", "anchor_spec_id"})


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
    if packet.get('version') == PLAN_MANAGEMENT_VERSION:
        return _encode_selected_plan_evidence(packet)
    management = packet.get('version') == MANAGEMENT_VERSION
    member = packet.get('version') == MEMBER_VERSION or management and 'member_spec_id' in packet
    _fields(packet, PACKET_FIELDS | (MEMBER_FIELDS if member else set()) | ({'management_closure'} if management else set()))
    if _text(packet["version"]) not in {VERSION, AMENDED_VERSION, MEMBER_VERSION, MANAGEMENT_VERSION}:
        raise ValueError("unsupported selected-order evidence version")
    for key in ("source_instance", "operation_id", "account_ref", "run_id", "source_spec_id"):
        _text(packet[key])
    _time(packet["stopped_at"])
    if member:
        for name in MEMBER_FIELDS:
            _text(packet[name])
        if packet['source_spec_id'] != packet['member_spec_id']:
            raise ValueError('selected-order native member attribution disagrees')
    physical = packet['version'] == AMENDED_VERSION or (member or management) and 'broker_legs' in packet['order']
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

    if management:
        closure = _fields(packet['management_closure'], frozenset({'version', 'ledger_scope', 'documents', 'source_plan_position', 'origin_approval_id', 'shadow_candidate'}))
        if (closure['version'] != 'selected-order-management-closure/v1' or closure['ledger_scope'] != 'single-original-intent/v1'
                or not isinstance(closure['documents'], list)):
            raise ValueError('invalid selected-order management closure')
        _text(closure['origin_approval_id'])
        head = closure['source_plan_position']
        if not isinstance(head, dict) or not PLAN_FIELDS <= head.keys() or any(head[name] != position[name] for name in PLAN_FIELDS):
            raise ValueError('selected-order full source position changed')
        keys = set()
        for document in closure['documents']:
            _fields(document, frozenset({'collection', 'owner', 'key', 'document'}))
            for name in ('collection', 'owner', 'key'): _text(document[name])
            key = (document['collection'], document['owner'], document['key'])
            if key in keys or not isinstance(document['document'], dict):
                raise ValueError('duplicate or invalid selected-order policy document')
            keys.add(key)
        if closure['shadow_candidate'] is not None and not isinstance(closure['shadow_candidate'], dict):
            raise ValueError('invalid selected-order original candidate')

    encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return encoded, sha256(encoded).hexdigest()


NATIVE_ORDER_REFERENCES = frozenset({'mock_plan_approval', 'mock_account_member_spec_id',
    'mock_replacement_claim', 'mock_protection_claim'})
NATIVE_EVENT_REFERENCES = frozenset({'mock_cancel_retry_claim', 'planned_leader_cancel_claim',
    'close_entry_cancel_claim', 'close_order_management_claim', 'leader_order_management_claim',
    'panic_order_management_claim'})


def selected_plan_orders(packet):
    """Original immutable native records, never a request-provided authority set."""
    if packet.get('version') == PLAN_MANAGEMENT_VERSION:
        return tuple({**item['order'], **item['native_refs']} for item in packet['plan_orders'])
    order = dict(packet['order'])
    if 'management_closure' in packet:
        order['mock_plan_approval'] = packet['management_closure']['origin_approval_id']
    if 'member_spec_id' in packet:
        order['mock_account_member_spec_id'] = packet['member_spec_id']
    return (order,)


def selected_physical_order_facts(packet):
    result = []
    for order in selected_plan_orders(packet):
        legs = order.get('broker_legs') or [dict(broker_order_id=order['broker_order_id'],
            original_broker_order_id='', ordered_quantity=order['quantity'], limit_price=order['limit_price'],
            detailed_filled_quantity=order['detailed_filled_quantity'],
            broker_reported_filled_quantity=order['broker_reported_filled_quantity'], transferred_quantity=0)]
        result.extend({**leg, 'intent_id': order['intent_id'], 'symbol': order['symbol'],
            'venue': order['venue'], 'side': order['side']} for leg in legs)
    return tuple(result)


def pack_selected_plan_order(native):
    fields = AMENDED_ORDER_FIELDS if native.get('broker_legs') else ORDER_FIELDS
    if not fields <= native.keys() or native.keys() - fields - NATIVE_ORDER_REFERENCES:
        raise ValueError('unsupported native selected-plan order facts')
    return dict(order={name: native[name] for name in fields},
        native_refs={name: native[name] for name in NATIVE_ORDER_REFERENCES if name in native},
        source_order_sha256=sha256(json.dumps(native, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest())


def _encode_selected_plan_evidence(packet):
    member = 'member_spec_id' in packet
    _fields(packet, PACKET_FIELDS | (MEMBER_FIELDS if member else set()) | {'management_closure', 'plan_orders'})
    for name in ('source_instance', 'operation_id', 'account_ref', 'run_id', 'source_spec_id'):
        _text(packet[name])
    _time(packet['stopped_at'])
    if member:
        for name in MEMBER_FIELDS: _text(packet[name])
        if packet['member_spec_id'] != packet['source_spec_id']:
            raise ValueError('selected-plan member scope changed')
    if not isinstance(packet['plan_orders'], list) or not packet['plan_orders']:
        raise ValueError('selected-plan native order closure is empty')
    orders = {}
    for item in packet['plan_orders']:
        _fields(item, {'order', 'native_refs', 'source_order_sha256'})
        if not isinstance(item['native_refs'], dict) or item['native_refs'].keys() - NATIVE_ORDER_REFERENCES:
            raise ValueError('unsupported selected-plan native references')
        order = _fields(item['order'], AMENDED_ORDER_FIELDS if 'broker_legs' in item['order'] else ORDER_FIELDS)
        intent = _intent(order)
        if (intent.intent_id in orders or (intent.account_ref, intent.run_id, intent.symbol,
                intent.plan.plan_id, intent.plan.policy_ref) != (packet['account_ref'], packet['run_id'],
                packet['order']['symbol'], packet['order']['plan']['plan_id'], packet['order']['plan']['policy_ref'])
                or item['native_refs'].get('mock_account_member_spec_id') != packet.get('member_spec_id')
                or pack_selected_plan_order({**order, **item['native_refs']}) != item):
            raise ValueError('selected-plan order set or original hash changed')
        if not item['native_refs'].get('mock_plan_approval'):
            raise ValueError('selected-plan original approval reference is missing')
        OrderState(order['state'])
        for name in ('filled_quantity', 'detailed_filled_quantity', 'broker_reported_filled_quantity'):
            _count(order[name])
        for name in ('updated_at', 'created_at', 'expires_at'): _time(order[name])
        if order['last_broker_as_of'] is not None: _time(order['last_broker_as_of'])
        orders[intent.intent_id] = order
    root = orders.get(packet['order']['intent_id'])
    if root != packet['order'] or root['side'] != 'BUY' or root['plan']['expected_revision'] != 0:
        raise ValueError('selected-plan original root changed')
    if not isinstance(packet['events'], list): raise ValueError('selected-plan event closure is missing')
    event_ids, histories, previous = set(), {identity: [] for identity in orders}, 0
    quantity, fill_cursor, revision, lots = 0, 0, 0, []
    claimed, first_order = set(), []
    for event in packet['events']:
        optional = ({'reason'} | NATIVE_EVENT_REFERENCES) & event.keys()
        _fields(event, EVENT_FIELDS | {'accepted_sequence'} | optional)
        if any(not isinstance(event[name], dict) for name in NATIVE_EVENT_REFERENCES & event.keys()):
            raise ValueError('selected-plan native command claim is incomplete')
        sequence, identity = _count(event['accepted_sequence']), event['intent_id']
        if identity not in orders or sequence <= previous or event['event_id'] in event_ids:
            raise ValueError('selected-plan event identity or source sequence changed')
        order = orders[identity]
        if identity not in claimed:
            if order['plan']['expected_revision'] != revision:
                raise ValueError('selected-plan reservation revision closure is incomplete')
            revision += 1
            claimed.add(identity)
            first_order.append(identity)
        previous = sequence
        event_ids.add(_text(event['event_id']))
        for name in ('occurred_at', 'received_at'): _time(event[name])
        if event['broker_as_of'] is not None: _time(event['broker_as_of'])
        OrderState(event['state'])
        _text(event['event_type'])
        amount, price = _count(event['quantity']), _count(event['price'])
        if event['event_type'] == 'FILL':
            if not amount or not price or not _text(event['broker_execution_id']):
                raise ValueError('selected-plan detailed fill is incomplete')
            if order['side'] == 'BUY':
                quantity += amount
                lots.append([amount, price])
            else:
                quantity -= amount
                remaining = amount
                while remaining and lots:
                    taken = min(remaining, lots[0][0])
                    remaining -= taken
                    lots[0][0] -= taken
                    if not lots[0][0]: lots.pop(0)
            if quantity < 0: raise ValueError('selected-plan sell attribution is unreconciled')
            fill_cursor = sequence
        if event['event_type'] == 'FILL' or event['state'] in {'CANCELLED', 'REJECTED'}: revision += 1
        histories[identity].append(event)
    if first_order != list(orders): raise ValueError('selected-plan order closure is not in native first-event order')
    physical_ids = set()
    for identity, order in orders.items():
        history = histories[identity]
        fills = [event for event in history if event['event_type'] == 'FILL']
        if (not history or history[-1]['state'] != order['state']
                or order['fill_ids'] != [event['broker_execution_id'] for event in fills]
                or order['detailed_filled_quantity'] != sum(event['quantity'] for event in fills)
                or 'broker_legs' not in order and order['filled_quantity'] != max(order['detailed_filled_quantity'], order['broker_reported_filled_quantity'])
                or order['filled_quantity'] > order['quantity']):
            raise ValueError('selected-plan native order and event history changed')
        keys = [(event['broker_order_id'], event['broker_execution_id']) for event in fills]
        if len(keys) != len(set(keys)): raise ValueError('selected-plan physical fill is duplicated')
        if 'broker_legs' in order: _validate_physical_facts(order, _intent(order), history)
        for leg in order.get('broker_legs') or [order]:
            number = _text(leg['broker_order_id'], optional=True)
            if number and number in physical_ids: raise ValueError('selected-plan physical order has multiple owners')
            if number: physical_ids.add(number)
    position = _fields(packet['plan_position'], PLAN_FIELDS)
    for name in ('quantity', 'fill_cursor', 'revision'): _count(position[name])
    if ((position['version'], position['plan_id'], position['policy_ref'], position['run_id'], position['symbol'])
            != ('mock_plan_position/v1', root['plan']['plan_id'], root['plan']['policy_ref'], root['run_id'], root['symbol'])
            or (position['quantity'], position['fill_cursor'], position['revision']) != (quantity, fill_cursor, revision)
            or _count(packet['source_cursor']) < previous):
        raise ValueError('selected-plan signed replay disagrees with native head')
    closure = _fields(packet['management_closure'], {'version', 'ledger_scope', 'documents', 'source_plan_position',
        'origin_approval_id', 'shadow_candidate'})
    if (closure['version'], closure['ledger_scope']) != ('selected-order-management-closure/v2', 'single-plan-ledger/v1'):
        raise ValueError('unsupported selected-plan policy closure')
    head = closure['source_plan_position']
    if isinstance(head, dict):
        for name in ('quantity', 'fill_cursor', 'revision'): _count(head[name])
        if 'capital_lots' in head:
            for quantity_and_price in head['capital_lots']:
                if (not isinstance(quantity_and_price, list) or len(quantity_and_price) != 2
                        or not all(_count(value) > 0 for value in quantity_and_price)):
                    raise ValueError('selected-plan capital lot is incomplete')
    if (not isinstance(head, dict) or any(head.get(name) != value for name, value in position.items())
            or head.get('unreconciled_sell_quantity', 0) or 'capital_lots' in head and head['capital_lots'] != lots
            or member and head.get('spec_id') != packet['member_spec_id']):
        raise ValueError('selected-plan original quantity/capital/member head changed')
    seen = set()
    if not isinstance(closure['documents'], list): raise ValueError('selected-plan policies are missing')
    for item in closure['documents']:
        _fields(item, {'collection', 'owner', 'key', 'document'})
        key = tuple(_text(item[name]) for name in ('collection', 'owner', 'key'))
        if key in seen or not isinstance(item['document'], dict): raise ValueError('selected-plan policy identity is duplicated')
        seen.add(key)
    if closure['origin_approval_id'] != packet['plan_orders'][0]['native_refs']['mock_plan_approval']:
        raise ValueError('selected-plan root approval changed')
    encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return encoded, sha256(encoded).hexdigest()
