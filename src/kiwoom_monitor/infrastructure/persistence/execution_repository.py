"""Persistent broker execution ledger over the central SQLite/PostgreSQL store."""

from __future__ import annotations

import hashlib
import json
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot,
    OrderEvent,
    OrderIntent,
    OrderPlanRef,
    OrderSide,
    OrderState,
    OrderType,
    PlannedOrderIntent,
    TERMINAL_ORDER_STATES,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery



class ExecutionStore(Protocol):
    def create_execution_intent(self, value: dict[str, Any], *, ownership: dict[str, Any] | None = None) -> bool: ...
    def append_execution_event(self, intent: dict[str, Any], event: dict[str, Any], *, ownership: dict[str, Any] | None = None) -> bool: ...
    def load_execution_intent(self, intent_id: str, *, _reader: Any = None) -> dict[str, Any] | None: ...
    def load_document(self, collection: str, owner: str, key: str, *, _reader: Any = None) -> dict[str, Any] | None: ...
    def load_active_execution_intents(
        self, environment: str, account_ref: str, run_id: str, *, _reader: Any = None,
    ) -> list[dict[str, Any]]: ...
    def load_account_active_execution_intents(
        self, environment: str, account_ref: str, limit: int, *, _reader: Any = None,
    ) -> list[dict[str, Any]]: ...
    def find_execution_intent_by_broker_order_id(
        self, environment: str, account_ref: str, run_id: str, broker_order_id: str,
    ) -> dict[str, Any] | None: ...
    def load_execution_events(self, intent_id: str, *, _reader: Any = None) -> list[dict[str, Any]]: ...
    def load_account_execution_events(
        self, environment: str, account_ref: str, after_sequence: int, limit: int, *, _reader: Any = None,
    ) -> list[dict[str, Any]]: ...
    def save_execution_account_snapshot(self, value: dict[str, Any], *, ownership: dict[str, Any] | None = None) -> bool: ...
    def acquire_execution_runtime(
        self, owner_key: str, owner_token: str, now: str, lease_expires_at: str,
    ) -> bool: ...
    def release_execution_runtime(self, owner_key: str, owner_token: str) -> bool: ...


@dataclass(frozen=True)
class ExecutionBrokerLeg:
    """One physical order; transfers do not alter its original broker quantity."""

    broker_order_id: str
    original_broker_order_id: str
    ordered_quantity: int
    limit_price: int
    state: OrderState
    transferred_quantity: int = 0
    detailed_filled_quantity: int = 0
    broker_reported_filled_quantity: int = 0
    fill_ids: tuple[str, ...] = ()
    last_broker_as_of: datetime | None = None

    @property
    def filled_quantity(self) -> int:
        return max(self.detailed_filled_quantity, self.broker_reported_filled_quantity)

    @property
    def remaining_quantity(self) -> int:
        return max(0, self.ordered_quantity - self.transferred_quantity - self.filled_quantity)


@dataclass(frozen=True)
class ExecutionAmendment:
    broker_order_id: str
    quantity: int
    limit_price: int
    started_at: datetime
    status: str = "PENDING"
    reported_broker_order_id: str = ""
    accepted_quantity: int = 0
    observed_fills: tuple[str, ...] = ()
    assessment: dict[str, Any] | None = None

    @property
    def unresolved(self) -> bool:
        return self.status in {"PENDING", "UNKNOWN"}

    @property
    def reconciliation_pending(self) -> bool:
        return self.unresolved or bool(self.observed_fills)


@dataclass(frozen=True)
class ExecutionRecord:
    intent: OrderIntent
    state: OrderState
    broker_order_id: str
    filled_quantity: int
    fill_ids: tuple[str, ...]
    last_broker_as_of: datetime | None
    updated_at: datetime
    detailed_filled_quantity: int = 0
    broker_reported_filled_quantity: int = 0
    broker_legs: tuple[ExecutionBrokerLeg, ...] = ()
    amendment: ExecutionAmendment | None = None
    broker_revision: int = 0


def broker_legs_state(legs, requested, amendment=None):
    if amendment is not None and amendment.reconciliation_pending:
        return OrderState.SUBMISSION_UNKNOWN
    filled = sum(leg.filled_quantity for leg in legs)
    active = [leg for leg in legs if leg.state not in TERMINAL_ORDER_STATES and leg.remaining_quantity]
    if any(leg.state is OrderState.CANCEL_PENDING for leg in active):
        return OrderState.CANCEL_PENDING
    if active:
        return OrderState.PARTIALLY_FILLED if filled else OrderState.ACCEPTED
    if filled >= requested:
        return OrderState.FILLED
    return OrderState.CANCELLED


def execution_broker_legs(record):
    if record.broker_legs:
        return record.broker_legs
    if not record.broker_order_id or record.intent.limit_price is None:
        return ()
    return (ExecutionBrokerLeg(record.broker_order_id, "", record.intent.quantity, record.intent.limit_price, record.state,
        detailed_filled_quantity=record.detailed_filled_quantity,
        broker_reported_filled_quantity=record.broker_reported_filled_quantity,
        fill_ids=record.fill_ids, last_broker_as_of=record.last_broker_as_of),)


def execution_remaining_quantity(record):
    if record.state in TERMINAL_ORDER_STATES:
        return 0
    if record.broker_legs:
        return sum(leg.remaining_quantity for leg in record.broker_legs if leg.state not in TERMINAL_ORDER_STATES)
    return max(0, record.intent.quantity - record.filled_quantity)


def broker_fill_key(broker_order_id: str, execution_id: str) -> str:
    return json.dumps([broker_order_id, execution_id], separators=(",", ":"))


def amendment_from_recovery(record, recovery: MockAccountRecovery, now):
    """An unambiguous physical order fact, not a fabricated broker ACK or fill."""
    command = record.amendment
    if command is None or not command.unresolved:
        return None
    if recovery.account.account_ref != record.intent.account_ref:
        raise ValueError("amendment recovery account mismatch")
    if not command.started_at <= recovery.account.as_of <= now:
        return None
    known = {leg.broker_order_id for leg in record.broker_legs}
    details = [item for item in recovery.order_details
               if item.original_broker_order_id == command.broker_order_id and item.broker_order_id not in known]
    ids = {item.broker_order_id for item in details}
    if len(ids) != 1:
        return None
    number = next(iter(ids))
    facts = {(item.ordered_quantity, item.side, item.limit_price, item.venue) for item in details}
    if len(facts) != 1 or any(item.reasons for item in details):
        return None
    quantity, side, price, venue = next(iter(facts))
    if (type(quantity) is not int or not 0 < quantity <= command.quantity
            or (side, price, venue) != (record.intent.side, command.limit_price, record.intent.venue)):
        return None
    orders = [order for order in recovery.orders if order.broker_order_id == number]
    if (not orders or any(order.account_ref != record.intent.account_ref or order.symbol != record.intent.symbol
            or not command.started_at <= order.as_of <= recovery.account.as_of
            or order.state is OrderState.REJECTED for order in orders)):
        return None
    return number, quantity, max(order.as_of for order in orders)


@dataclass(frozen=True)
class ExecutionPlanPosition:
    plan: OrderPlanRef
    symbol: str
    quantity: int
    fill_cursor: int
    unreconciled_sell_quantity: int


@dataclass(frozen=True)
class AccountExecutionEvent:
    """One immutable broker-ledger event with its account-scoped intent lineage."""

    accepted_sequence: int
    source_event_id: str
    intent_id: str
    run_id: str
    decision_id: str
    account_ref: str
    environment: str
    symbol: str
    venue: str
    side: str
    event_type: str
    state: str
    occurred_at: datetime
    received_at: datetime
    broker_order_id: str = ""
    broker_execution_id: str = ""
    quantity: int = 0
    price: int = 0
    reason: str = ""
    broker_as_of: datetime | None = None
    plan: OrderPlanRef | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted_sequence": self.accepted_sequence,
            "source_event_id": self.source_event_id,
            "intent_id": self.intent_id,
            "run_id": self.run_id,
            "decision_id": self.decision_id,
            "account_ref": self.account_ref,
            "environment": self.environment,
            "symbol": self.symbol,
            "venue": self.venue,
            "side": self.side,
            "event_type": self.event_type,
            "state": self.state,
            "occurred_at": self.occurred_at.isoformat(),
            "received_at": self.received_at.isoformat(),
            "broker_order_id": self.broker_order_id,
            "broker_execution_id": self.broker_execution_id,
            "quantity": self.quantity,
            "price": self.price,
            "reason": self.reason,
            "broker_as_of": self.broker_as_of.isoformat() if self.broker_as_of else None,
            **({"plan": asdict(self.plan)} if self.plan is not None else {}),
        }


@dataclass(frozen=True)
class AccountExecutionEventPage:
    events: tuple[AccountExecutionEvent, ...]
    next_cursor: int
    has_more: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [event.to_dict() for event in self.events],
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
        }


class ExecutionRepository:
    def __init__(self, store: ExecutionStore) -> None:
        self._store = store
        self._ownership: dict[str, str] | None = None
        self._automation_control: dict[str, Any] | None = None

    def uses_store(self, store: ExecutionStore) -> bool:
        return self._store is store

    def mock_automation_read_scope(self) -> AbstractContextManager[Any]:
        factory = getattr(self._store, "mock_automation_read_scope", None)
        return factory() if callable(factory) else nullcontext()

    def broker_reconciliation_scope(self) -> AbstractContextManager[Any]:
        factory = getattr(type(self._store), "_broker_reconciliation_scope", None)
        return factory(self._store) if factory is not None else nullcontext()

    def bind_runtime_owner(self, account_ref: str, run_id: str, owner_token: str) -> None:
        ownership = {"owner_key": f"mock:{account_ref}", "owner_token": f"{run_id}:{owner_token}", "run_id": run_id}
        if self._ownership is not None and self._ownership != ownership:
            raise RuntimeError("EXECUTION_REPOSITORY_OWNER_IS_IMMUTABLE")
        self._ownership = ownership

    def _write_options(self) -> dict[str, Any]:
        if self._ownership is None:
            return {}
        ownership: dict[str, Any] = dict(self._ownership)
        if self._automation_control is not None:
            ownership.update(self._automation_control)
        return {"ownership": ownership}

    def bind_automation_control(self, *, spec_id: str, control_revision: int,
                                replacement_claim: dict | None = None, protection_claim: dict | None = None,
                                cancel_retry_claim: dict | None = None,
                                planned_cancel_claim: dict | None = None,
                                plan_recovery: MockAccountRecovery | None = None,
                                amendment_claim: dict | None = None) -> None:
        if self._ownership is None:
            raise RuntimeError("EXECUTION_REPOSITORY_OWNER_REQUIRED")
        if not spec_id.strip() or type(control_revision) is not int or control_revision <= 0:
            raise ValueError("valid automation spec and control revision are required")
        self._automation_control = {
            "active_spec_id": spec_id,
            "control_revision": control_revision,
        }
        if replacement_claim is not None:
            self._automation_control['replacement_claim'] = dict(replacement_claim)
        if protection_claim is not None:
            if replacement_claim is not None:
                raise ValueError('execution_claim_purposes_conflict')
            self._automation_control['protection_claim'] = dict(protection_claim)
        if cancel_retry_claim is not None:
            if replacement_claim is not None or protection_claim is not None:
                raise ValueError('execution_claim_purposes_conflict')
            self._automation_control['cancel_retry_claim']=dict(cancel_retry_claim)
        if planned_cancel_claim is not None:
            requires_recovery = planned_cancel_claim.get("version") in {
                "planned-leader-expired-entry-cancel/v1", "close-entry-deadline-cancel/v1",
                'close-entry-protective-cancel/v1', 'close-current-order-management/v1',
                'panic-current-order-management/v1',
            }
            if (any(value is not None for value in (replacement_claim, protection_claim, cancel_retry_claim))
                    or (plan_recovery is not None) != requires_recovery):
                raise ValueError("execution_claim_purposes_conflict")
            self._automation_control["planned_cancel_claim"] = dict(planned_cancel_claim)
        if amendment_claim is not None:
            if any(value is not None for value in (replacement_claim, protection_claim, cancel_retry_claim, planned_cancel_claim)) or plan_recovery is None:
                raise ValueError("execution_claim_purposes_conflict")
            self._automation_control["amendment_claim"] = dict(amendment_claim)
        if plan_recovery is not None:
            self._automation_control["plan_recovery"] = plan_recovery

    def clear_automation_control(self) -> None:
        self._automation_control = None

    def create(self, intent: OrderIntent) -> ExecutionRecord:
        return self.claim(intent)[0]

    def claim(self, intent: OrderIntent) -> tuple[ExecutionRecord, bool]:
        record = ExecutionRecord(intent, OrderState.QUEUED, "", 0, (), None, intent.created_at)
        if self._store.create_execution_intent(_record_document(record), **self._write_options()):
            return record, True
        existing = self.load(intent.intent_id)
        if existing is None or existing.intent != intent:
            raise ValueError("intent_id already belongs to a different order")
        return existing, False

    def apply(
        self, record: ExecutionRecord, event: OrderEvent, *, _connection: Any = None,
        _amendment_recovery: MockAccountRecovery | None = None, **changes: object,
    ) -> ExecutionRecord:
        updated = ExecutionRecord(
            intent=record.intent,
            state=event.state,
            broker_order_id=str(changes.get("broker_order_id", record.broker_order_id)),
            filled_quantity=int(changes.get("filled_quantity", record.filled_quantity)),
            fill_ids=tuple(changes.get("fill_ids", record.fill_ids)),
            last_broker_as_of=changes.get("last_broker_as_of", record.last_broker_as_of),
            updated_at=event.received_at,
            detailed_filled_quantity=int(changes.get(
                "detailed_filled_quantity", record.detailed_filled_quantity,
            )),
            broker_reported_filled_quantity=int(changes.get(
                "broker_reported_filled_quantity", record.broker_reported_filled_quantity,
            )),
            broker_legs=tuple(changes.get("broker_legs", record.broker_legs)),
            amendment=changes.get("amendment", record.amendment),
            broker_revision=record.broker_revision + int(bool(record.broker_legs or changes.get("broker_legs"))),
        )
        connection_args = {} if _connection is None else {"_connection": _connection}
        options = self._write_options()
        if _amendment_recovery is not None:
            if self._ownership is None:
                raise RuntimeError("EXECUTION_REPOSITORY_OWNER_REQUIRED")
            options = {"ownership": {**options["ownership"], "amendment_recovery": _amendment_recovery}}
        if not self._store.append_execution_event(
            _record_document(updated), _event_document(event), **options, **connection_args,
        ):
            return self.load(record.intent.intent_id, **connection_args) or record
        return updated

    def load(self, intent_id: str, *, _reader: Any = None, _connection: Any = None) -> ExecutionRecord | None:
        reader_args = {} if _reader is None else {'_reader': _reader}
        connection_args = {} if _connection is None else {"_connection": _connection}
        value = self._store.load_execution_intent(intent_id, **reader_args, **connection_args)
        return _record_from_document(value) if value else None

    def load_plan_position(self, account_ref, run_id, symbol, plan: OrderPlanRef, *, _reader=None) -> ExecutionPlanPosition:
        reader_args = {} if _reader is None else {"_reader": _reader}
        row = self._store.load_document("execution_mock_plan_positions", account_ref, plan.plan_id, **reader_args)
        value = row["document"] if row else {}
        return plan_position_from_document(value, run_id, symbol, plan)

    def load_protective_execution(self, intent_id, *, _reader=None):
        reader_args = {} if _reader is None else {"_reader": _reader}
        value = self._store.load_execution_intent(intent_id, **reader_args)
        return (_record_from_document(value), _protection_claim(value)) if value else (None, None)

    def active_intents(
        self, environment: str, account_ref: str, run_id: str, *, _reader: Any = None,
    ) -> tuple[ExecutionRecord, ...]:
        if environment != "mock" or not account_ref.strip() or not run_id.strip():
            raise ValueError("mock environment, account_ref and run_id are required")
        reader_args = {} if _reader is None else {"_reader": _reader}
        return tuple(
            _record_from_document(value)
            for value in self._store.load_active_execution_intents(
                environment, account_ref, run_id, **reader_args,
            )
        )

    def find_by_broker_order_id(
        self, environment: str, account_ref: str, run_id: str, broker_order_id: str, *, _connection: Any = None,
    ) -> ExecutionRecord | None:
        if environment != "mock" or not account_ref.strip() or not run_id.strip() or not broker_order_id.strip():
            raise ValueError("mock environment, account_ref, run_id and broker_order_id are required")
        connection_args = {} if _connection is None else {"_connection": _connection}
        value = self._store.find_execution_intent_by_broker_order_id(
            environment, account_ref, run_id, broker_order_id, **connection_args,
        )
        return _record_from_document(value) if value else None

    def account_active_intents(self, account_ref: str, *, _reader: Any = None) -> tuple[tuple[ExecutionRecord, ...], bool]:
        """One bounded account-wide read, including QUEUED records with no events."""
        reader_args = {} if _reader is None else {"_reader": _reader}
        values = self._store.load_account_active_execution_intents("mock", account_ref, 401, **reader_args)
        records = tuple(_record_from_document(value) for value in values[:400])
        if any(r.intent.account_ref != account_ref or r.intent.environment != "mock" for r in records):
            raise ValueError("account-wide ledger crossed the requested scope")
        return records, len(values) <= 400

    def events(self, intent_id: str, *, _reader: Any = None) -> tuple[dict[str, Any], ...]:
        reader_args = {} if _reader is None else {'_reader': _reader}
        return tuple(self._store.load_execution_events(intent_id, **reader_args))

    def claim_approval(self, intent_id: str) -> dict[str, Any] | None:
        value = self._store.load_execution_intent(intent_id)
        claim = value.get('mock_replacement_claim') if value else None
        if claim is not None and (not isinstance(claim, dict)
                or set(claim) != {'gate_id', 'risk_snapshot_id', 'risk_reconciliation_revision'}
                or type(claim['risk_reconciliation_revision']) is not int
                or claim['risk_reconciliation_revision'] <= 0):
            raise ValueError('invalid native replacement claim proof')
        return dict(claim) if claim is not None else None

    def protection_claim(self, intent_id: str) -> dict[str, Any] | None:
        return _protection_claim(self._store.load_execution_intent(intent_id))

    def account_events(
        self, environment: str, account_ref: str, *, after_sequence: int = 0, limit: int = 500, _reader: Any = None,
    ) -> AccountExecutionEventPage:
        """Read one stable account page across run changes without mutating the ledger."""
        if environment != "mock" or not account_ref.strip():
            raise ValueError("mock environment and account_ref are required")
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        reader_args = {} if _reader is None else {"_reader": _reader}
        rows = self._store.load_account_execution_events(
            environment, account_ref, after_sequence, limit + 1, **reader_args,
        )
        has_more = len(rows) > limit
        selected = rows[:limit]
        events = tuple(
            _account_event_from_document(row, environment=environment, account_ref=account_ref)
            for row in selected
        )
        next_cursor = events[-1].accepted_sequence if events else after_sequence
        return AccountExecutionEventPage(events, next_cursor, has_more)

    def save_account_snapshot(
        self, environment: str, snapshot: AccountSnapshot, received_at: datetime,
    ) -> bool:
        if environment != "mock":
            raise ValueError("only mock account snapshots may enter the execution ledger")
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise ValueError("account snapshot receipt must be timezone-aware")
        body = {
            "environment": environment,
            "account_ref": snapshot.account_ref,
            "available_cash_won": snapshot.available_cash_won,
            "reserved_open_buy_won": snapshot.reserved_open_buy_won,
            "positions": dict(snapshot.positions),
            "as_of": snapshot.as_of.isoformat(),
            "received_at": received_at.isoformat(),
        }
        # Runtime receipts were historically UTC. Keep that canonical ID boundary
        # while the stored receipt uses the application's KST representation.
        canonical = {**body, "received_at": received_at.astimezone(timezone.utc).isoformat()}
        fingerprint = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        body["snapshot_id"] = f"account_snapshot_{hashlib.sha256(fingerprint).hexdigest()}"
        return self._store.save_execution_account_snapshot(body, **self._write_options())

    def claim_runtime(
        self, environment: str, account_ref: str, run_id: str, owner_token: str,
        now: datetime, lease_seconds: int = 60, *, _connection: Any = None,
    ) -> bool:
        if environment != "mock":
            raise ValueError("execution runtime supports only mock")
        owner_key = f"{environment}:{account_ref}"
        lease_owner = f"{run_id}:{owner_token}"
        from kiwoom_monitor.central_server.postgres_access import database_observer
        observer = database_observer()
        connection_args = {} if _connection is None else {"_connection": _connection}
        with observer.account.source_execution_owner(owner_key, run_id) if observer is not None else nullcontext():
            return self._store.acquire_execution_runtime(
                owner_key, lease_owner, now.isoformat(),
                (now + timedelta(seconds=max(1, lease_seconds))).isoformat(), **connection_args,
            )


    def release_runtime(self, environment: str, account_ref: str, run_id: str, owner_token: str) -> bool:
        if environment != "mock" or not all(value.strip() for value in (account_ref, run_id, owner_token)):
            raise ValueError("mock environment, account_ref, run_id and owner_token are required")
        from kiwoom_monitor.central_server.postgres_access import database_observer
        observer = database_observer()
        owner_key = f"{environment}:{account_ref}"
        with observer.account.source_execution_owner(owner_key, run_id) if observer is not None else nullcontext():
            return self._store.release_execution_runtime(owner_key, f"{run_id}:{owner_token}")


def plan_position_from_document(value, run_id, symbol, plan: OrderPlanRef) -> ExecutionPlanPosition:
    """Decode the same native plan head for public reads and transactional claims."""
    if ((value.get("version"), value.get("plan_id"), value.get("policy_ref"), value.get("run_id"), value.get("symbol"))
            != ("mock_plan_position/v1", plan.plan_id, plan.policy_ref, run_id, symbol)
            or type(value.get("revision")) is not int or value["revision"] < plan.expected_revision):
        raise ValueError("execution plan position scope changed")
    quantity, cursor, unassigned = value.get("quantity"), value.get("fill_cursor"), value.get("unreconciled_sell_quantity", 0)
    if any(type(item) is not int or item < 0 for item in (quantity, cursor, unassigned)):
        raise ValueError("invalid execution plan position")
    return ExecutionPlanPosition(OrderPlanRef(plan.plan_id, plan.policy_ref, value["revision"]),
                                 symbol, quantity, cursor, unassigned)


def _record_document(record: ExecutionRecord) -> dict[str, Any]:
    intent = record.intent
    return {
        "intent_id": intent.intent_id,
        "run_id": intent.run_id,
        "decision_id": intent.decision_id,
        "account_ref": intent.account_ref,
        "environment": intent.environment,
        "symbol": intent.symbol,
        "venue": intent.venue,
        "side": intent.side.value,
        "quantity": intent.quantity,
        "order_type": intent.order_type.value,
        "limit_price": intent.limit_price,
        "created_at": intent.created_at.isoformat(),
        "expires_at": intent.expires_at.isoformat(),
        "policy_version": intent.policy_version,
        **({"plan": asdict(intent.plan)} if isinstance(intent, PlannedOrderIntent) else {}),
        "state": record.state.value,
        "broker_order_id": record.broker_order_id,
        "filled_quantity": record.filled_quantity,
        "fill_ids": list(record.fill_ids),
        "last_broker_as_of": record.last_broker_as_of.isoformat() if record.last_broker_as_of else None,
        "updated_at": record.updated_at.isoformat(),
        "detailed_filled_quantity": record.detailed_filled_quantity,
        "broker_reported_filled_quantity": record.broker_reported_filled_quantity,
        **({
            "broker_legs": [broker_leg_document(leg) for leg in record.broker_legs],
            "amendment": amendment_document(record.amendment),
            "broker_revision": record.broker_revision,
        } if record.broker_legs else {}),
    }


def broker_leg_document(leg: ExecutionBrokerLeg) -> dict[str, Any]:
    return {**asdict(leg), "state": leg.state.value, "fill_ids": list(leg.fill_ids),
            "last_broker_as_of": leg.last_broker_as_of.isoformat() if leg.last_broker_as_of else None}


def amendment_document(value: ExecutionAmendment | None) -> dict[str, Any] | None:
    if value is None:
        return None
    document = {**asdict(value), "observed_fills": list(value.observed_fills), "started_at": value.started_at.isoformat()}
    if value.assessment is None:
        document.pop("assessment")
    return document


def broker_leg_from_document(value: Mapping[str, Any]) -> ExecutionBrokerLeg:
    return ExecutionBrokerLeg(**{**value, "state": OrderState(value["state"]),
        "fill_ids": tuple(value["fill_ids"]),
        "last_broker_as_of": datetime.fromisoformat(value["last_broker_as_of"]) if value["last_broker_as_of"] else None})


def amendment_from_document(value: Mapping[str, Any] | None) -> ExecutionAmendment | None:
    return ExecutionAmendment(**{**value, "observed_fills": tuple(value.get("observed_fills", ())),
        "started_at": datetime.fromisoformat(value["started_at"])}) if value else None


def validate_broker_ledger_event(stored, document, event, *, recovery=None, now=None, authorized_assessment=None) -> None:
    """Validate the physical-order transition again inside the native writer's CAS."""
    before, after = _record_from_document(stored), _record_from_document(document)
    if (before.intent != after.intent or before.broker_order_id != after.broker_order_id
            or after.broker_revision != before.broker_revision + 1
            or after.state.value != event["state"] or not after.broker_legs
            or after.state is not broker_legs_state(after.broker_legs, after.intent.quantity, after.amendment)):
        raise RuntimeError("EXECUTION_BROKER_REVISION_CHANGED")
    legs = after.broker_legs
    if (len({leg.broker_order_id for leg in legs}) != len(legs)
            or sum(leg.ordered_quantity - leg.transferred_quantity for leg in legs) != after.intent.quantity
            or any(not leg.broker_order_id or not 0 <= leg.transferred_quantity <= leg.ordered_quantity
                   or leg.ordered_quantity <= 0 or leg.limit_price <= 0
                   or min(leg.detailed_filled_quantity, leg.broker_reported_filled_quantity) < 0
                   or len(set(leg.fill_ids)) != len(leg.fill_ids) for leg in legs)
            or after.detailed_filled_quantity != sum(leg.detailed_filled_quantity for leg in legs)
            or after.broker_reported_filled_quantity != sum(leg.broker_reported_filled_quantity for leg in legs)
            or after.filled_quantity != sum(leg.filled_quantity for leg in legs)
            or after.filled_quantity > after.intent.quantity):
        raise RuntimeError("EXECUTION_BROKER_LEGS_CHANGED")
    kind = event["event_type"]
    command = after.amendment
    if kind == "AMENDMENT_STARTED":
        if ("plan" in stored or before.intent.run_id.startswith("mock_auto_run_")) and authorized_assessment is None:
            raise RuntimeError("PLANNED_AMENDMENT_ASSESSMENT_REQUIRED")
        expected = ExecutionAmendment(event["broker_order_id"], event["quantity"], event["price"],
                                      datetime.fromisoformat(event["received_at"]), assessment=authorized_assessment)
        initial = before.broker_legs or (ExecutionBrokerLeg(
            before.broker_order_id, "", before.intent.quantity, before.intent.limit_price, before.state,
            detailed_filled_quantity=before.detailed_filled_quantity,
            broker_reported_filled_quantity=before.broker_reported_filled_quantity,
            fill_ids=before.fill_ids, last_broker_as_of=before.last_broker_as_of),)
        selected = [leg for leg in initial if leg.broker_order_id == expected.broker_order_id]
        if (command != expected or legs != initial or before.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}
                or (before.amendment is not None and before.amendment.unresolved)
                or len(selected) != 1 or not 0 < expected.quantity <= selected[0].remaining_quantity
                or type(expected.quantity) is not int or type(expected.limit_price) is not int or expected.limit_price <= 0
                or before.intent.order_type is not OrderType.LIMIT or before.intent.venue != "KRX"):
            raise RuntimeError("EXECUTION_AMENDMENT_CLAIM_CHANGED")
    elif kind in {"AMENDMENT_ACCEPTED", "AMENDMENT_RECOVERED", "AMENDMENT_REJECTED", "AMENDMENT_RESPONSE_UNKNOWN"}:
        pending = before.amendment
        if pending is None or not pending.unresolved:
            raise RuntimeError("EXECUTION_AMENDMENT_COMMAND_MISSING")
        if kind in {"AMENDMENT_ACCEPTED", "AMENDMENT_RECOVERED"}:
            q = event["quantity"]
            expected = replace(pending, status="ACCEPTED", reported_broker_order_id=event["broker_order_id"], accepted_quantity=q)
            expected_legs = tuple(replace(leg, transferred_quantity=leg.transferred_quantity + q)
                if leg.broker_order_id == pending.broker_order_id else leg for leg in before.broker_legs)
            expected_legs += (ExecutionBrokerLeg(event["broker_order_id"], pending.broker_order_id,
                                                q, pending.limit_price, OrderState.ACCEPTED),)
            valid = type(q) is int and 0 < q <= pending.quantity and event["price"] == pending.limit_price
            if kind == "AMENDMENT_RECOVERED":
                if recovery is None or now is None or amendment_from_recovery(before, recovery, now) != (
                        event["broker_order_id"], q, datetime.fromisoformat(event["broker_as_of"])):
                    raise RuntimeError("EXECUTION_AMENDMENT_RECOVERY_CHANGED")
        else:
            expected = replace(pending, status="REJECTED" if kind == "AMENDMENT_REJECTED" else "UNKNOWN",
                               reported_broker_order_id=command.reported_broker_order_id if command else "")
            expected_legs, valid = before.broker_legs, True
        if not valid or command != expected or legs != expected_legs:
            raise RuntimeError("EXECUTION_AMENDMENT_RESPONSE_CHANGED")
    elif kind == "AMENDMENT_FILL_OBSERVED":
        pending = before.amendment
        key = broker_fill_key(event["broker_order_id"], event["broker_execution_id"])
        if (pending is None or not pending.unresolved or key in pending.observed_fills
                or event["broker_order_id"] in {leg.broker_order_id for leg in before.broker_legs}
                or not event["broker_order_id"] or not event["broker_execution_id"]
                or type(event["quantity"]) is not int or event["quantity"] <= 0
                or type(event["price"]) is not int or event["price"] <= 0
                or event["reason"] != f"broker_revision={after.broker_revision};amendment_parent={pending.broker_order_id}"
                or command != replace(pending, observed_fills=(*pending.observed_fills, key))
                or legs != before.broker_legs):
            raise RuntimeError("EXECUTION_AMENDMENT_OBSERVATION_CHANGED")
    else:
        expected_command = before.amendment
        if kind == "FILL" and expected_command is not None:
            key = broker_fill_key(event["broker_order_id"], event["broker_execution_id"])
            expected_command = replace(expected_command, observed_fills=tuple(
                value for value in expected_command.observed_fills if value != key))
        if not before.broker_legs or command != expected_command:
            raise RuntimeError("EXECUTION_AMENDMENT_COMMAND_CHANGED")
        physical = event["broker_order_id"]
        prior = next((leg for leg in before.broker_legs if leg.broker_order_id == physical), None)
        updated = next((leg for leg in legs if leg.broker_order_id == physical), None)
        if prior is None or updated is None or any(
                leg != new for leg, new in zip(before.broker_legs, legs) if leg.broker_order_id != physical
        ) or len(legs) != len(before.broker_legs):
            raise RuntimeError("EXECUTION_BROKER_LEGS_CHANGED")
        if kind == "FILL":
            q, fill_id = event["quantity"], event["broker_execution_id"]
            expected = replace(prior, detailed_filled_quantity=prior.detailed_filled_quantity + q,
                               fill_ids=(*prior.fill_ids, fill_id))
            valid = (fill_id and fill_id not in prior.fill_ids and type(q) is int and q > 0
                     and type(event["price"]) is int and event["price"] > 0
                     and after.fill_ids == (*before.fill_ids, fill_id))
        elif kind in {"BROKER_FILL_AGGREGATE", "BROKER_RECONCILED"}:
            expected = replace(prior, state=updated.state, last_broker_as_of=updated.last_broker_as_of,
                               broker_reported_filled_quantity=updated.broker_reported_filled_quantity)
            valid = (updated.last_broker_as_of == datetime.fromisoformat(event["broker_as_of"])
                     and (prior.last_broker_as_of is None or updated.last_broker_as_of > prior.last_broker_as_of)
                     and updated.broker_reported_filled_quantity >= prior.broker_reported_filled_quantity)
        elif kind in {"CANCEL_STARTED", "CANCEL_ACCEPTED", "CANCEL_RESPONSE_UNKNOWN", "CANCEL_REJECTED"}:
            expected = replace(prior, state=updated.state)
            valid = not (command and command.reconciliation_pending) and (
                updated.state is OrderState.CANCEL_PENDING if kind != "CANCEL_REJECTED"
                else updated.state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED})
        else:
            raise RuntimeError("EXECUTION_BROKER_EVENT_UNSUPPORTED")
        if not valid or updated != expected:
            raise RuntimeError("EXECUTION_BROKER_EVENT_CHANGED")
    if kind != "FILL" and after.fill_ids != before.fill_ids:
        raise RuntimeError("EXECUTION_BROKER_FILL_IDENTITY_CHANGED")


def _event_document(event: OrderEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "intent_id": event.intent_id,
        "event_type": event.event_type,
        "state": event.state.value,
        "occurred_at": event.occurred_at.isoformat(),
        "received_at": event.received_at.isoformat(),
        "broker_order_id": event.broker_order_id,
        "broker_execution_id": event.broker_execution_id,
        "quantity": event.quantity,
        "price": event.price,
        "reason": event.reason,
        "broker_as_of": event.broker_as_of.isoformat() if event.broker_as_of else None,
    }


def _protection_claim(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    claim = value.get("mock_protection_claim") if value else None
    if claim is not None and (not isinstance(claim, dict)
            or set(claim) != {"workflow_id", "gate_id", "risk_snapshot_id", "risk_reconciliation_revision"}
            or type(claim["risk_reconciliation_revision"]) is not int or claim["risk_reconciliation_revision"] <= 0):
        raise ValueError("invalid native protection claim proof")
    return dict(claim) if claim is not None else None


def _record_from_document(value: Mapping[str, Any]) -> ExecutionRecord:
    intent_type = PlannedOrderIntent if "plan" in value else OrderIntent
    intent = intent_type(
        intent_id=str(value["intent_id"]), run_id=str(value["run_id"]),
        decision_id=str(value["decision_id"]), account_ref=str(value["account_ref"]),
        environment=str(value["environment"]), symbol=str(value["symbol"]), venue=str(value["venue"]),
        side=OrderSide(str(value["side"])), quantity=int(value["quantity"]),
        order_type=OrderType(str(value["order_type"])),
        limit_price=int(value["limit_price"]) if value.get("limit_price") is not None else None,
        created_at=datetime.fromisoformat(str(value["created_at"])),
        expires_at=datetime.fromisoformat(str(value["expires_at"])),
        policy_version=str(value["policy_version"]),
        **({"plan": OrderPlanRef(**value["plan"])} if "plan" in value else {}),
    )
    last_as_of = value.get("last_broker_as_of")
    return ExecutionRecord(
        intent=intent, state=OrderState(str(value["state"])),
        broker_order_id=str(value.get("broker_order_id") or ""),
        filled_quantity=int(value.get("filled_quantity") or 0),
        fill_ids=tuple(str(item) for item in value.get("fill_ids", ())),
        last_broker_as_of=datetime.fromisoformat(str(last_as_of)) if last_as_of else None,
        updated_at=datetime.fromisoformat(str(value["updated_at"])),
        detailed_filled_quantity=int(value.get("detailed_filled_quantity", value.get("filled_quantity", 0)) or 0),
        broker_reported_filled_quantity=int(value.get("broker_reported_filled_quantity") or 0),
        broker_legs=tuple(broker_leg_from_document(leg) for leg in value.get("broker_legs", ())),
        amendment=amendment_from_document(value.get("amendment")),
        broker_revision=int(value.get("broker_revision", 0)),
    )


def _account_event_from_document(
    value: Mapping[str, Any], *, environment: str, account_ref: str,
) -> AccountExecutionEvent:
    intent = value.get("intent")
    event = value.get("event")
    if not isinstance(intent, Mapping) or not isinstance(event, Mapping):
        raise ValueError("execution event row must contain intent and event documents")
    if intent.get("environment") != environment or intent.get("account_ref") != account_ref:
        raise ValueError("execution event row crossed its requested account scope")
    intent_id = str(intent.get("intent_id") or "")
    if not intent_id or str(event.get("intent_id") or "") != intent_id:
        raise ValueError("execution event intent lineage is invalid")
    sequence = value.get("accepted_sequence")
    if type(sequence) is not int or sequence <= 0:
        raise ValueError("execution event sequence is invalid")
    occurred_at = datetime.fromisoformat(str(event["occurred_at"]))
    received_at = datetime.fromisoformat(str(event["received_at"]))
    broker_as_of_value = event.get("broker_as_of")
    broker_as_of = datetime.fromisoformat(str(broker_as_of_value)) if broker_as_of_value else None
    if occurred_at.tzinfo is None or received_at.tzinfo is None or (
        broker_as_of is not None and broker_as_of.tzinfo is None
    ):
        raise ValueError("execution event timestamps must be timezone-aware")
    source_event_id = str(event.get("event_id") or "")
    if not source_event_id:
        raise ValueError("execution event source identity is required")
    return AccountExecutionEvent(
        accepted_sequence=sequence,
        source_event_id=source_event_id,
        intent_id=intent_id,
        run_id=str(intent.get("run_id") or ""),
        decision_id=str(intent.get("decision_id") or ""),
        account_ref=account_ref,
        environment=environment,
        symbol=str(intent.get("symbol") or ""),
        venue=str(intent.get("venue") or ""),
        side=str(intent.get("side") or ""),
        event_type=str(event.get("event_type") or ""),
        state=str(event.get("state") or ""),
        occurred_at=occurred_at,
        received_at=received_at,
        broker_order_id=str(event.get("broker_order_id") or intent.get("broker_order_id") or ""),
        broker_execution_id=str(event.get("broker_execution_id") or ""),
        quantity=int(event.get("quantity") or 0),
        price=int(event.get("price") or 0),
        reason=str(event.get("reason") or ""),
        broker_as_of=broker_as_of,
        plan=OrderPlanRef(**intent["plan"]) if "plan" in intent else None,
    )
