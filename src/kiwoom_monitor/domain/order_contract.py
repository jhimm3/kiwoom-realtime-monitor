"""Broker-backed mock order contracts kept separate from research paper fills."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping


class AccountEnvironment(StrEnum):
    REAL = "real"
    MOCK = "mock"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AccountScope:
    """원문 계좌번호·자격증명과 분리된 지속 계좌 신원."""

    broker: str
    environment: AccountEnvironment
    account_ref: str

    def __post_init__(self) -> None:
        if self.broker == "legacy":
            if self.environment is not AccountEnvironment.UNKNOWN or self.account_ref != "legacy-unassigned":
                raise ValueError("legacy account scope must be legacy/unknown/legacy-unassigned")
            return
        if self.broker != "kiwoom" or self.environment not in {
            AccountEnvironment.REAL, AccountEnvironment.MOCK,
        }:
            raise ValueError("only a registered kiwoom account scope is supported")
        try:
            parsed = uuid.UUID(str(self.account_ref))
        except (ValueError, AttributeError) as exc:
            raise ValueError("account_ref must be a UUID") from exc
        if parsed.int == 0 or str(parsed) != str(self.account_ref).lower():
            raise ValueError("account_ref must be a canonical non-zero UUID")

    def to_dict(self) -> dict[str, str]:
        return {
            "broker": self.broker,
            "environment": self.environment.value,
            "account_ref": self.account_ref,
        }


LEGACY_ACCOUNT_SCOPE = AccountScope(
    broker="legacy",
    environment=AccountEnvironment.UNKNOWN,
    account_ref="legacy-unassigned",
)


@dataclass(frozen=True)
class AccountBinding:
    credential_profile_id: str
    scope: AccountScope
    binding_revision: int
    verified_at: datetime
    verification_method: str = "ka00001"

    def __post_init__(self) -> None:
        if not self.credential_profile_id.strip():
            raise ValueError("credential_profile_id is required")
        if self.binding_revision <= 0:
            raise ValueError("binding_revision must be positive")
        if self.verification_method != "ka00001":
            raise ValueError("unregistered account verification method")
        _require_aware(self.verified_at, "verified_at")


@dataclass(frozen=True)
class AccountScopeAlias:
    """오프라인에서 만든 scope를 검증된 중앙 scope에 연결한 불변 기록."""

    origin_scope: AccountScope
    canonical_scope: AccountScope
    credential_profile_id: str
    binding_revision: int
    verified_at: datetime
    verification_method: str = "ka00001"

    def __post_init__(self) -> None:
        if self.origin_scope == self.canonical_scope:
            raise ValueError("account scope alias must change the account_ref")
        if (
            self.origin_scope.broker != self.canonical_scope.broker
            or self.origin_scope.environment != self.canonical_scope.environment
        ):
            raise ValueError("account scope alias cannot cross broker or environment")
        if not self.credential_profile_id.strip() or self.binding_revision <= 0:
            raise ValueError("account scope alias requires a verified binding")
        if self.verification_method != "ka00001":
            raise ValueError("unregistered account verification method")
        _require_aware(self.verified_at, "verified_at")


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderState(StrEnum):
    QUEUED = "QUEUED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


TERMINAL_ORDER_STATES = frozenset({OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED})


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    run_id: str
    decision_id: str
    account_ref: str
    environment: str
    symbol: str
    venue: str
    side: OrderSide
    quantity: int
    order_type: OrderType
    limit_price: int | None
    created_at: datetime
    expires_at: datetime
    policy_version: str

    def __post_init__(self) -> None:
        for field_name in ("intent_id", "run_id", "decision_id", "account_ref", "symbol", "policy_version"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        if self.environment != "mock":
            raise ValueError("broker execution supports only the mock environment")
        if self.venue != "KRX":
            raise ValueError("Kiwoom mock investment supports KRX orders only")
        if not self.symbol.isdigit() or len(self.symbol) != 6:
            raise ValueError("symbol must be a six-digit domestic stock code")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.order_type is OrderType.LIMIT and (self.limit_price is None or self.limit_price <= 0):
            raise ValueError("limit orders require a positive limit_price")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise ValueError("market orders must not include limit_price")
        _require_aware(self.created_at, "created_at")
        _require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")


@dataclass(frozen=True)
class BrokerSubmission:
    broker_order_id: str
    accepted_at: datetime

    def __post_init__(self) -> None:
        if not self.broker_order_id.strip():
            raise ValueError("broker_order_id is required")
        _require_aware(self.accepted_at, "accepted_at")


@dataclass(frozen=True)
class BrokerFill:
    execution_id: str
    quantity: int
    price: int
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not self.execution_id.strip():
            raise ValueError("execution_id is required")
        if self.quantity <= 0 or self.price <= 0:
            raise ValueError("fill quantity and price must be positive")
        _require_aware(self.occurred_at, "occurred_at")


@dataclass(frozen=True)
class BrokerOrderSnapshot:
    broker_order_id: str
    account_ref: str
    symbol: str
    state: OrderState
    filled_quantity: int
    remaining_quantity: int
    as_of: datetime
    fills: tuple[BrokerFill, ...] = ()

    def __post_init__(self) -> None:
        if not self.broker_order_id.strip() or not self.account_ref.strip():
            raise ValueError("broker_order_id and account_ref are required")
        if self.filled_quantity < 0 or self.remaining_quantity < 0:
            raise ValueError("snapshot quantities must not be negative")
        _require_aware(self.as_of, "as_of")


@dataclass(frozen=True)
class AccountSnapshot:
    account_ref: str
    available_cash_won: int
    reserved_open_buy_won: int
    positions: Mapping[str, int]
    as_of: datetime

    def __post_init__(self) -> None:
        if not self.account_ref.strip():
            raise ValueError("account_ref is required")
        if self.available_cash_won < 0 or self.reserved_open_buy_won < 0:
            raise ValueError("cash values must not be negative")
        if any(int(quantity) < 0 for quantity in self.positions.values()):
            raise ValueError("position quantities must not be negative")
        _require_aware(self.as_of, "as_of")


@dataclass(frozen=True)
class OrderEvent:
    event_id: str
    intent_id: str
    event_type: str
    state: OrderState
    occurred_at: datetime
    received_at: datetime
    broker_order_id: str = ""
    broker_execution_id: str = ""
    quantity: int = 0
    price: int = 0
    reason: str = ""
    broker_as_of: datetime | None = None


def order_event(
    intent_id: str,
    event_type: str,
    state: OrderState,
    occurred_at: datetime,
    received_at: datetime,
    **values: object,
) -> OrderEvent:
    _require_aware(occurred_at, "occurred_at")
    _require_aware(received_at, "received_at")
    broker_as_of = values.get("broker_as_of")
    if broker_as_of is not None:
        _require_aware(broker_as_of, "broker_as_of")
    body = {
        "intent_id": intent_id,
        "event_type": event_type,
        "state": state.value,
        "occurred_at": occurred_at.isoformat(),
        "received_at": received_at.isoformat(),
        **{key: value.isoformat() if isinstance(value, datetime) else value for key, value in values.items()},
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return OrderEvent(
        event_id=f"order_event_{hashlib.sha256(encoded).hexdigest()}",
        intent_id=intent_id,
        event_type=event_type,
        state=state,
        occurred_at=occurred_at,
        received_at=received_at,
        **values,
    )


def contract_document(value: object) -> dict[str, object]:
    document = asdict(value)
    for key, item in tuple(document.items()):
        if isinstance(item, datetime):
            document[key] = item.isoformat()
        elif isinstance(item, StrEnum):
            document[key] = item.value
    return document


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
