"""Persistent broker execution ledger over the central SQLite/PostgreSQL store."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Protocol

from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot,
    OrderEvent,
    OrderIntent,
    OrderSide,
    OrderState,
    OrderType,
)


class ExecutionStore(Protocol):
    def create_execution_intent(self, value: dict[str, Any], *, ownership: dict[str, Any] | None = None) -> bool: ...
    def append_execution_event(self, intent: dict[str, Any], event: dict[str, Any], *, ownership: dict[str, Any] | None = None) -> bool: ...
    def load_execution_intent(self, intent_id: str) -> dict[str, Any] | None: ...
    def load_active_execution_intents(
        self, environment: str, account_ref: str, run_id: str,
    ) -> list[dict[str, Any]]: ...
    def find_execution_intent_by_broker_order_id(
        self, environment: str, account_ref: str, run_id: str, broker_order_id: str,
    ) -> dict[str, Any] | None: ...
    def load_execution_events(self, intent_id: str) -> list[dict[str, Any]]: ...
    def load_account_execution_events(
        self, environment: str, account_ref: str, after_sequence: int, limit: int,
    ) -> list[dict[str, Any]]: ...
    def save_execution_account_snapshot(self, value: dict[str, Any], *, ownership: dict[str, Any] | None = None) -> bool: ...
    def acquire_execution_runtime(
        self, owner_key: str, owner_token: str, now: str, lease_expires_at: str,
    ) -> bool: ...
    def release_execution_runtime(self, owner_key: str, owner_token: str) -> bool: ...


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

    def bind_automation_control(self, *, spec_id: str, control_revision: int) -> None:
        if self._ownership is None:
            raise RuntimeError("EXECUTION_REPOSITORY_OWNER_REQUIRED")
        if not spec_id.strip() or type(control_revision) is not int or control_revision <= 0:
            raise ValueError("valid automation spec and control revision are required")
        self._automation_control = {
            "active_spec_id": spec_id,
            "control_revision": control_revision,
        }

    def clear_automation_control(self) -> None:
        self._automation_control = None

    def create(self, intent: OrderIntent) -> ExecutionRecord:
        record = ExecutionRecord(intent, OrderState.QUEUED, "", 0, (), None, intent.created_at)
        if self._store.create_execution_intent(_record_document(record), **self._write_options()):
            return record
        existing = self.load(intent.intent_id)
        if existing is None or existing.intent != intent:
            raise ValueError("intent_id already belongs to a different order")
        return existing

    def apply(self, record: ExecutionRecord, event: OrderEvent, **changes: object) -> ExecutionRecord:
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
        )
        if not self._store.append_execution_event(_record_document(updated), _event_document(event), **self._write_options()):
            return self.load(record.intent.intent_id) or record
        return updated

    def load(self, intent_id: str) -> ExecutionRecord | None:
        value = self._store.load_execution_intent(intent_id)
        return _record_from_document(value) if value else None

    def active_intents(
        self, environment: str, account_ref: str, run_id: str,
    ) -> tuple[ExecutionRecord, ...]:
        if environment != "mock" or not account_ref.strip() or not run_id.strip():
            raise ValueError("mock environment, account_ref and run_id are required")
        return tuple(
            _record_from_document(value)
            for value in self._store.load_active_execution_intents(
                environment, account_ref, run_id,
            )
        )

    def find_by_broker_order_id(
        self, environment: str, account_ref: str, run_id: str, broker_order_id: str,
    ) -> ExecutionRecord | None:
        if environment != "mock" or not account_ref.strip() or not run_id.strip() or not broker_order_id.strip():
            raise ValueError("mock environment, account_ref, run_id and broker_order_id are required")
        value = self._store.find_execution_intent_by_broker_order_id(
            environment, account_ref, run_id, broker_order_id,
        )
        return _record_from_document(value) if value else None

    def events(self, intent_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(self._store.load_execution_events(intent_id))

    def account_events(
        self, environment: str, account_ref: str, *, after_sequence: int = 0, limit: int = 500,
    ) -> AccountExecutionEventPage:
        """Read one stable account page across run changes without mutating the ledger."""
        if environment != "mock" or not account_ref.strip():
            raise ValueError("mock environment and account_ref are required")
        if type(after_sequence) is not int or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self._store.load_account_execution_events(
            environment, account_ref, after_sequence, limit + 1,
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
        body = {
            "environment": environment,
            "account_ref": snapshot.account_ref,
            "available_cash_won": snapshot.available_cash_won,
            "reserved_open_buy_won": snapshot.reserved_open_buy_won,
            "positions": dict(snapshot.positions),
            "as_of": snapshot.as_of.isoformat(),
            "received_at": received_at.isoformat(),
        }
        fingerprint = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        body["snapshot_id"] = f"account_snapshot_{hashlib.sha256(fingerprint).hexdigest()}"
        return self._store.save_execution_account_snapshot(body, **self._write_options())

    def claim_runtime(
        self, environment: str, account_ref: str, run_id: str, owner_token: str,
        now: datetime, lease_seconds: int = 60,
    ) -> bool:
        if environment != "mock":
            raise ValueError("execution runtime supports only mock")
        owner_key = f"{environment}:{account_ref}"
        lease_owner = f"{run_id}:{owner_token}"
        return self._store.acquire_execution_runtime(
            owner_key, lease_owner, now.isoformat(),
            (now + timedelta(seconds=max(1, lease_seconds))).isoformat(),
        )


    def release_runtime(self, environment: str, account_ref: str, run_id: str, owner_token: str) -> bool:
        if environment != "mock" or not all(value.strip() for value in (account_ref, run_id, owner_token)):
            raise ValueError("mock environment, account_ref, run_id and owner_token are required")
        return self._store.release_execution_runtime(f"{environment}:{account_ref}", f"{run_id}:{owner_token}")


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
        "state": record.state.value,
        "broker_order_id": record.broker_order_id,
        "filled_quantity": record.filled_quantity,
        "fill_ids": list(record.fill_ids),
        "last_broker_as_of": record.last_broker_as_of.isoformat() if record.last_broker_as_of else None,
        "updated_at": record.updated_at.isoformat(),
        "detailed_filled_quantity": record.detailed_filled_quantity,
        "broker_reported_filled_quantity": record.broker_reported_filled_quantity,
    }


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


def _record_from_document(value: Mapping[str, Any]) -> ExecutionRecord:
    intent = OrderIntent(
        intent_id=str(value["intent_id"]), run_id=str(value["run_id"]),
        decision_id=str(value["decision_id"]), account_ref=str(value["account_ref"]),
        environment=str(value["environment"]), symbol=str(value["symbol"]), venue=str(value["venue"]),
        side=OrderSide(str(value["side"])), quantity=int(value["quantity"]),
        order_type=OrderType(str(value["order_type"])),
        limit_price=int(value["limit_price"]) if value.get("limit_price") is not None else None,
        created_at=datetime.fromisoformat(str(value["created_at"])),
        expires_at=datetime.fromisoformat(str(value["expires_at"])),
        policy_version=str(value["policy_version"]),
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
    )
