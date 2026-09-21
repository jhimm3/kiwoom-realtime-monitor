"""Account-scoped risk evidence for one automatic mock execution runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from kiwoom_monitor.infrastructure.persistence.execution_repository import (
    AccountExecutionEvent,
    ExecutionRecord,
)


MOCK_AUTOMATION_RISK_VERSION = "mock_automation_risk_snapshot/v1"
VERIFIED_DAILY_PNL_SOURCE = "account_scoped_fifo_broker_cost/v1"
KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class MockAutomationRiskSnapshot:
    snapshot_id: str
    version: str
    account_ref: str
    execution_run_id: str
    binding_revision: int
    trading_date: date
    observed_at: datetime
    broker_query_started_at: datetime
    broker_query_completed_at: datetime
    account_as_of: datetime
    account_revision: str
    reconciliation_revision: int
    execution_event_high_watermark: int
    cost_evidence_revision: str
    daily_net_pnl_won: int | None
    daily_net_pnl_source: str | None
    cost_complete: bool
    missing_cost_reasons: tuple[str, ...]
    reconciliation_complete: bool
    data_path: str
    data_gap_seconds: int | None
    submission_unknown_count: int
    reconnect_count: int
    balance_mismatch_count: int
    owned_position_quantities: tuple[tuple[str, int], ...]
    pending_sell_quantities: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if self.version != MOCK_AUTOMATION_RISK_VERSION:
            raise ValueError("unsupported mock automation risk snapshot")
        for name in ("snapshot_id", "account_ref", "execution_run_id", "account_revision"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        _require_sha256(self.snapshot_id, "snapshot_id")
        _require_sha256(self.account_revision, "account_revision")
        _require_sha256(self.cost_evidence_revision, "cost_evidence_revision")
        for name in ("observed_at", "broker_query_started_at", "broker_query_completed_at", "account_as_of"):
            _require_aware(getattr(self, name), name)
        if self.broker_query_started_at > self.broker_query_completed_at:
            raise ValueError("broker query interval is invalid")
        if self.observed_at < self.broker_query_completed_at:
            raise ValueError("risk observation predates its broker query")
        if self.trading_date != self.observed_at.astimezone(KST).date():
            raise ValueError("risk trading_date must use the KST observation date")
        for name in (
            "binding_revision", "reconciliation_revision", "execution_event_high_watermark",
            "submission_unknown_count", "reconnect_count", "balance_mismatch_count",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.binding_revision <= 0 or self.reconciliation_revision <= 0:
            raise ValueError("binding and reconciliation revisions must be positive")
        if self.data_path not in {"nas", "direct"}:
            raise ValueError("data_path must be nas or direct")
        if self.data_gap_seconds is not None and self.data_gap_seconds < 0:
            raise ValueError("data_gap_seconds must be non-negative or unknown")
        if self.daily_net_pnl_won is not None and type(self.daily_net_pnl_won) is not int:
            raise ValueError("daily_net_pnl_won must be an integer or unknown")
        if self.cost_complete != (not self.missing_cost_reasons):
            raise ValueError("cost completeness and missing reasons disagree")
        if self.daily_net_pnl_won is not None and self.daily_net_pnl_source != VERIFIED_DAILY_PNL_SOURCE:
            raise ValueError("known daily PnL requires the verified source")
        for values in (self.owned_position_quantities, self.pending_sell_quantities):
            if any(not symbol or quantity < 0 for symbol, quantity in values):
                raise ValueError("position quantities must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "version": self.version,
            "account_ref": self.account_ref,
            "execution_run_id": self.execution_run_id,
            "binding_revision": self.binding_revision,
            "trading_date": self.trading_date.isoformat(),
            "observed_at": self.observed_at.isoformat(),
            "broker_query_started_at": self.broker_query_started_at.isoformat(),
            "broker_query_completed_at": self.broker_query_completed_at.isoformat(),
            "account_as_of": self.account_as_of.isoformat(),
            "account_revision": self.account_revision,
            "reconciliation_revision": self.reconciliation_revision,
            "execution_event_high_watermark": self.execution_event_high_watermark,
            "cost_evidence_revision": self.cost_evidence_revision,
            "daily_net_pnl_won": self.daily_net_pnl_won,
            "daily_net_pnl_source": self.daily_net_pnl_source,
            "cost_complete": self.cost_complete,
            "missing_cost_reasons": list(self.missing_cost_reasons),
            "reconciliation_complete": self.reconciliation_complete,
            "data_path": self.data_path,
            "data_gap_seconds": self.data_gap_seconds,
            "submission_unknown_count": self.submission_unknown_count,
            "reconnect_count": self.reconnect_count,
            "balance_mismatch_count": self.balance_mismatch_count,
            "owned_position_quantities": [list(value) for value in self.owned_position_quantities],
            "pending_sell_quantities": [list(value) for value in self.pending_sell_quantities],
        }


def build_mock_automation_risk_snapshot(
    recovery: MockAccountRecovery,
    events: Sequence[AccountExecutionEvent],
    costs: Sequence[DailyTradeCost],
    active_intents: Sequence[ExecutionRecord],
    *,
    execution_run_id: str,
    binding_revision: int,
    reconciliation_revision: int,
    observed_at: datetime,
    broker_query_started_at: datetime,
    broker_query_completed_at: datetime,
    reconnect_count: int = 0,
    balance_mismatch_count: int = 0,
    data_path: str = "nas",
) -> MockAutomationRiskSnapshot:
    """Project immutable risk evidence without estimating missing broker costs."""
    account = recovery.account
    for value in (observed_at, broker_query_started_at, broker_query_completed_at, account.as_of):
        _require_aware(value, "risk timestamp")
    if any(event.account_ref != account.account_ref or event.environment != "mock" for event in events):
        raise ValueError("execution events crossed the risk account scope")
    if any(record.intent.account_ref != account.account_ref for record in active_intents):
        raise ValueError("active intents crossed the risk account scope")
    trading_date = observed_at.astimezone(KST).date()
    exact_fills = _exact_fills(events)
    lots: dict[str, list[list[Any]]] = {}
    realized = Fraction(0)
    missing_costs: list[str] = []
    costs_by_key = {(cost.fill_date, cost.stock_code, cost.side): cost for cost in costs}
    fills_by_key: dict[tuple[date, str, str], list[AccountExecutionEvent]] = {}
    for event in exact_fills:
        side = "매수" if event.side == "BUY" else "매도"
        key = (event.occurred_at.astimezone(KST).date(), event.symbol, side)
        fills_by_key.setdefault(key, []).append(event)
    allocated_cost: dict[str, Fraction] = {}
    for key, values in fills_by_key.items():
        evidence = costs_by_key.get(key)
        gross = sum(value.quantity * value.price for value in values)
        if evidence is None:
            missing_costs.append(f"COST_MISSING:{key[0].isoformat()}:{key[1]}:{key[2]}")
            continue
        if evidence.gross_amount != gross:
            missing_costs.append(f"COST_GROSS_MISMATCH:{key[0].isoformat()}:{key[1]}:{key[2]}")
            continue
        for value in values:
            allocated_cost[value.source_event_id] = (
                Fraction(evidence.total_cost * value.quantity * value.price, gross)
                if gross else Fraction(0)
            )
    for key in costs_by_key.keys() - fills_by_key.keys():
        missing_costs.append(f"COST_WITHOUT_EXECUTION_FILL:{key[0].isoformat()}:{key[1]}:{key[2]}")

    unmatched_sell = False
    for event in sorted(exact_fills, key=lambda item: (item.occurred_at, item.accepted_sequence)):
        fill_day = event.occurred_at.astimezone(KST).date()
        if event.side == "BUY":
            lots.setdefault(event.symbol, []).append([
                event.quantity,
                Fraction(event.quantity * event.price) + allocated_cost.get(event.source_event_id, Fraction(0)),
                event.run_id,
            ])
            continue
        remaining = event.quantity
        sale_cost = allocated_cost.get(event.source_event_id)
        if sale_cost is None:
            sale_cost = Fraction(0)
        matched_proceeds = Fraction(0)
        matched_basis = Fraction(0)
        symbol_lots = lots.setdefault(event.symbol, [])
        while remaining and symbol_lots:
            lot_quantity, lot_basis, lot_run = symbol_lots[0]
            matched = min(remaining, lot_quantity)
            matched_basis += lot_basis * matched / lot_quantity
            matched_proceeds += matched * event.price
            remaining -= matched
            if matched == lot_quantity:
                symbol_lots.pop(0)
            else:
                symbol_lots[0] = [lot_quantity - matched, lot_basis * (lot_quantity - matched) / lot_quantity, lot_run]
        if remaining:
            unmatched_sell = True
            missing_costs.append(f"FIFO_BUY_MISSING:{event.symbol}")
        if fill_day == trading_date:
            realized += matched_proceeds - matched_basis - sale_cost

    net_positions = {
        symbol: sum(int(lot[0]) for lot in values)
        for symbol, values in lots.items() if sum(int(lot[0]) for lot in values) > 0
    }
    broker_positions = {
        str(symbol): int(quantity) for symbol, quantity in account.positions.items() if int(quantity) > 0
    }
    balance_matches = net_positions == broker_positions
    unresolved_aggregate = _has_unresolved_aggregate(events)
    open_orders = any(order.state not in TERMINAL_ORDER_STATES for order in recovery.orders)
    submission_unknown = sum(record.state.value == "SUBMISSION_UNKNOWN" for record in active_intents)
    owned = _run_positions(exact_fills, execution_run_id)
    pending_sells: dict[str, int] = {}
    for record in active_intents:
        if record.intent.run_id == execution_run_id and record.intent.side.value == "SELL":
            pending_sells[record.intent.symbol] = pending_sells.get(record.intent.symbol, 0) + max(
                0, record.intent.quantity - record.filled_quantity,
            )
    missing_reasons = tuple(dict.fromkeys(missing_costs))
    reconciliation_complete = not unresolved_aggregate and not unmatched_sell and balance_matches
    # Zero is evidence only after a complete account recovery and an empty confirmed ledger.
    known = not missing_reasons and reconciliation_complete
    if not exact_fills and (broker_positions or open_orders):
        known = False
        reconciliation_complete = False
    daily_pnl = round(realized) if known else None
    cost_document = [
        {
            "fill_date": value.fill_date.isoformat(), "stock_code": value.stock_code,
            "side": value.side, "gross_amount": value.gross_amount,
            "total_cost": value.total_cost,
        }
        for value in sorted(costs, key=lambda item: (item.fill_date, item.stock_code, item.side))
    ]
    cost_revision = _sha256(cost_document)
    account_revision = _sha256({
        "account_ref": account.account_ref,
        "available_cash_won": account.available_cash_won,
        "reserved_open_buy_won": account.reserved_open_buy_won,
        "positions": sorted(broker_positions.items()),
        "as_of": account.as_of.isoformat(),
        "orders": sorted((order.broker_order_id, order.state.value, order.filled_quantity,
                          order.remaining_quantity, order.as_of.isoformat()) for order in recovery.orders),
    })
    body = {
        "version": MOCK_AUTOMATION_RISK_VERSION,
        "account_ref": account.account_ref,
        "execution_run_id": execution_run_id,
        "binding_revision": binding_revision,
        "trading_date": trading_date.isoformat(),
        "observed_at": observed_at.isoformat(),
        "broker_query_started_at": broker_query_started_at.isoformat(),
        "broker_query_completed_at": broker_query_completed_at.isoformat(),
        "account_as_of": account.as_of.isoformat(),
        "account_revision": account_revision,
        "reconciliation_revision": reconciliation_revision,
        "execution_event_high_watermark": max((event.accepted_sequence for event in events), default=0),
        "cost_evidence_revision": cost_revision,
        "daily_net_pnl_won": daily_pnl,
        "daily_net_pnl_source": VERIFIED_DAILY_PNL_SOURCE if daily_pnl is not None else None,
        "cost_complete": not missing_reasons,
        "missing_cost_reasons": list(missing_reasons),
        "reconciliation_complete": reconciliation_complete,
        "data_path": data_path,
        "data_gap_seconds": max(0, int((observed_at - account.as_of).total_seconds())) if account.as_of <= observed_at else None,
        "submission_unknown_count": submission_unknown,
        "reconnect_count": reconnect_count,
        "balance_mismatch_count": balance_mismatch_count + (0 if balance_matches else 1),
        "owned_position_quantities": [list(value) for value in sorted(owned.items()) if value[1] > 0],
        "pending_sell_quantities": [list(value) for value in sorted(pending_sells.items()) if value[1] > 0],
    }
    return MockAutomationRiskSnapshot(snapshot_id=_sha256(body), **{
        **body,
        "trading_date": trading_date,
        "observed_at": observed_at,
        "broker_query_started_at": broker_query_started_at,
        "broker_query_completed_at": broker_query_completed_at,
        "account_as_of": account.as_of,
        "missing_cost_reasons": missing_reasons,
        "owned_position_quantities": tuple(tuple(value) for value in body["owned_position_quantities"]),
        "pending_sell_quantities": tuple(tuple(value) for value in body["pending_sell_quantities"]),
    })


def mock_automation_risk_snapshot_from_dict(value: Mapping[str, Any]) -> MockAutomationRiskSnapshot:
    snapshot = MockAutomationRiskSnapshot(
        snapshot_id=str(value["snapshot_id"]), version=str(value["version"]),
        account_ref=str(value["account_ref"]), execution_run_id=str(value["execution_run_id"]),
        binding_revision=int(value["binding_revision"]),
        trading_date=date.fromisoformat(str(value["trading_date"])),
        observed_at=datetime.fromisoformat(str(value["observed_at"])),
        broker_query_started_at=datetime.fromisoformat(str(value["broker_query_started_at"])),
        broker_query_completed_at=datetime.fromisoformat(str(value["broker_query_completed_at"])),
        account_as_of=datetime.fromisoformat(str(value["account_as_of"])),
        account_revision=str(value["account_revision"]),
        reconciliation_revision=int(value["reconciliation_revision"]),
        execution_event_high_watermark=int(value["execution_event_high_watermark"]),
        cost_evidence_revision=str(value["cost_evidence_revision"]),
        daily_net_pnl_won=(int(value["daily_net_pnl_won"]) if value.get("daily_net_pnl_won") is not None else None),
        daily_net_pnl_source=(str(value["daily_net_pnl_source"]) if value.get("daily_net_pnl_source") else None),
        cost_complete=bool(value["cost_complete"]),
        missing_cost_reasons=tuple(str(item) for item in value.get("missing_cost_reasons", ())),
        reconciliation_complete=bool(value["reconciliation_complete"]),
        data_path=str(value["data_path"]),
        data_gap_seconds=(int(value["data_gap_seconds"]) if value.get("data_gap_seconds") is not None else None),
        submission_unknown_count=int(value["submission_unknown_count"]),
        reconnect_count=int(value["reconnect_count"]),
        balance_mismatch_count=int(value["balance_mismatch_count"]),
        owned_position_quantities=tuple((str(item[0]), int(item[1])) for item in value.get("owned_position_quantities", ())),
        pending_sell_quantities=tuple((str(item[0]), int(item[1])) for item in value.get("pending_sell_quantities", ())),
    )
    expected = _sha256({key: item for key, item in snapshot.to_dict().items() if key != "snapshot_id"})
    if snapshot.snapshot_id != expected:
        raise ValueError("mock automation risk content does not match snapshot_id")
    return snapshot


def _exact_fills(events: Sequence[AccountExecutionEvent]) -> tuple[AccountExecutionEvent, ...]:
    found: dict[str, AccountExecutionEvent] = {}
    for event in events:
        if event.event_type != "FILL" or not event.broker_execution_id or event.quantity <= 0 or event.price <= 0:
            continue
        previous = found.get(event.broker_execution_id)
        if previous is not None and previous != event:
            raise ValueError("broker execution id has conflicting account events")
        found[event.broker_execution_id] = event
    return tuple(found.values())


def _has_unresolved_aggregate(events: Sequence[AccountExecutionEvent]) -> bool:
    by_intent: dict[str, list[AccountExecutionEvent]] = {}
    for event in events:
        by_intent.setdefault(event.intent_id, []).append(event)
    for values in by_intent.values():
        unresolved = 0
        seen_fills: set[str] = set()
        for value in sorted(values, key=lambda item: item.accepted_sequence):
            if value.event_type == "BROKER_FILL_AGGREGATE":
                unresolved += max(0, value.quantity)
            elif value.event_type == "FILL" and value.broker_execution_id not in seen_fills:
                seen_fills.add(value.broker_execution_id)
                unresolved = max(0, unresolved - max(0, value.quantity))
        if unresolved:
            return True
    return False


def _run_positions(events: Sequence[AccountExecutionEvent], run_id: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for event in sorted(events, key=lambda item: (item.occurred_at, item.accepted_sequence)):
        if event.run_id != run_id:
            continue
        change = event.quantity if event.side == "BUY" else -event.quantity
        values[event.symbol] = values.get(event.symbol, 0) + change
    return values


def _sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase sha256")
