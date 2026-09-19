"""D3c 전용 단일 포지션 모의 주문·체결 엔진."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from kiwoom_monitor.application.breakout_strategy import (
    StrategyRuntimeConfig,
    StrategyDecision,
    StrategyState,
)
from kiwoom_monitor.application.market_session_schedule import (
    KRX_REGULAR_RESEARCH_PROFILE,
    MarketPhase,
    SUPPORTED_RESEARCH_SESSION_PROFILES,
    research_session_key,
)
from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
from kiwoom_monitor.domain.ranking import normalize_stock_code


COST_MODEL_VERSION = "fixed_bps/v1"
EXECUTION_MODEL_VERSION = "next_tradable_bar_open/v1"
SAME_BAR_PATH_VERSION = "conservative_with_optimistic_bound/v1"


@dataclass(frozen=True)
class SimulationCostModel:
    version: str
    commission_bps: int
    sell_tax_bps: int
    slippage_bps: int
    rate_basis: str = "unspecified"
    source: str = ""
    valid_from: str = ""
    valid_to: str = ""

    def __post_init__(self) -> None:
        if self.version != COST_MODEL_VERSION:
            raise ValueError(f"unregistered cost model: {self.version}")
        if min(self.commission_bps, self.sell_tax_bps, self.slippage_bps) < 0:
            raise ValueError("simulation cost rates must not be negative")
        if self.slippage_bps >= 10_000:
            raise ValueError("slippage_bps must be below 10000")
        if self.rate_basis not in {
            "unspecified", "official_period_rule", "account_actual", "model_estimate",
        }:
            raise ValueError(f"unsupported simulation cost rate basis: {self.rate_basis}")
        if bool(self.valid_from) != bool(self.valid_to):
            raise ValueError("simulation cost validity requires both valid_from and valid_to")
        if self.valid_from:
            start = _aware_datetime(self.valid_from)
            end = _aware_datetime(self.valid_to)
            if end <= start:
                raise ValueError("simulation cost valid_to must be after valid_from")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SimulationExecutionConfig:
    version: str
    same_bar_path_version: str
    initial_cash_won: int
    cost_model: SimulationCostModel | None

    def __post_init__(self) -> None:
        if self.version != EXECUTION_MODEL_VERSION:
            raise ValueError(f"unregistered execution model: {self.version}")
        if self.same_bar_path_version != SAME_BAR_PATH_VERSION:
            raise ValueError(f"unregistered same-bar path model: {self.same_bar_path_version}")
        if self.initial_cash_won <= 0:
            raise ValueError("initial_cash_won must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "same_bar_path_version": self.same_bar_path_version,
            "initial_cash_won": self.initial_cash_won,
            "cost_model": self.cost_model.to_dict() if self.cost_model else None,
        }


@dataclass(frozen=True)
class SimulationPosition:
    symbol: str
    quantity: int
    entry_price: int
    entry_notional: int
    entry_commission: int
    opened_at: str
    entry_intent_id: str


@dataclass(frozen=True)
class PendingOrder:
    intent_id: str
    decision_id: str
    symbol: str
    side: str
    quantity: int
    submitted_at: str
    eligible_after: str
    reserved_cash_won: int
    session_profile: str = ""
    research_session: str = ""


@dataclass(frozen=True)
class SimulationPortfolio:
    cash_won: int
    reserved_cash_won: int
    position: SimulationPosition | None
    pending_order: PendingOrder | None
    realized_pnl_won: int
    total_cost_won: int


@dataclass(frozen=True)
class ExecutionEvent:
    event_id: str
    run_id: str
    execution_environment: str
    account_ref: str
    intent_id: str
    decision_id: str
    event_type: str
    state: str
    symbol: str
    side: str
    occurred_at: str
    received_at: str
    quantity: int
    price: int
    notional_won: int
    commission_won: int
    tax_won: int
    total_cost_won: int
    realized_pnl_won: int
    cash_after_won: int
    reserved_cash_after_won: int
    position_quantity_after: int
    source_revision_id: str
    optimistic_exit_price: int | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PaperExecutionEngine:
    """실계좌·TradeCostService와 연결되지 않는 결정론적 단일 슬롯 실행기."""

    def __init__(
        self,
        run_id: str,
        execution: SimulationExecutionConfig,
        strategy: StrategyRuntimeConfig,
        session_profile: str | None = None,
    ) -> None:
        if not str(run_id).strip():
            raise ValueError("run_id is required")
        self.run_id = run_id
        if session_profile is not None and session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES:
            raise ValueError(f"unsupported research session profile: {session_profile}")
        self.execution = execution
        self.strategy = strategy
        self.session_profile = session_profile
        self.strategy_state = StrategyState()
        self.portfolio = SimulationPortfolio(
            cash_won=execution.initial_cash_won,
            reserved_cash_won=0,
            position=None,
            pending_order=None,
            realized_pnl_won=0,
            total_cost_won=0,
        )

    def process_bar(self, bar: KrxMinuteBarFrame) -> tuple[ExecutionEvent, ...]:
        events: list[ExecutionEvent] = []
        pending = self.portfolio.pending_order
        if pending is not None and pending.symbol == normalize_stock_code(bar.code):
            bar_start = _aware_datetime(bar.bar_start).astimezone(timezone.utc)
            eligible_after = _aware_datetime(pending.eligible_after).astimezone(timezone.utc)
            if bar_start >= eligible_after:
                reason = self._pending_bar_rejection(pending, bar)
                event = (
                    self._discard_pending(pending, bar, reason)
                    if reason else self._fill_pending(pending, bar)
                )
                events.append(event)
        if self.portfolio.position is not None and (
            self.portfolio.position.symbol == normalize_stock_code(bar.code)
        ):
            if self.session_profile is not None and bar.market_phase != MarketPhase.CONTINUOUS.value:
                events.append(self._event(
                    intent_id=self.portfolio.position.entry_intent_id, decision_id="",
                    event_type="RISK_UNSUPPORTED", state="OPEN",
                    symbol=self.portfolio.position.symbol, side="", occurred_at=bar.bar_end,
                    received_at=bar.available_at, quantity=self.portfolio.position.quantity,
                    price=0, notional=0, commission=0, tax=0, realized=0,
                    source_revision_id=bar.revision_id, optimistic_exit_price=None,
                    reason="minute_bar_phase_cannot_prove_intrabar_execution_order",
                ))
                return tuple(events)
            risk_event = self._apply_intrabar_risk(bar)
            if risk_event is not None:
                events.append(risk_event)
            elif self.portfolio.position is not None:
                events.append(self._mark(bar))
        return tuple(events)

    def process_decision(
        self, decision: StrategyDecision, resulting_state: StrategyState,
        *, source_bar: KrxMinuteBarFrame | None = None,
    ) -> tuple[ExecutionEvent, ...]:
        self.strategy_state = resulting_state
        if decision.final_action not in {"ENTER", "EXIT"}:
            return ()
        side = "BUY" if decision.final_action == "ENTER" else "SELL"
        if self.portfolio.pending_order is not None:
            return (self._notice(decision, side, "ORDER_REJECTED", "pending_order_exists"),)
        if side == "BUY" and self.portfolio.position is not None:
            return (self._notice(decision, side, "ORDER_REJECTED", "position_already_open"),)
        if side == "SELL" and self.portfolio.position is None:
            return (self._notice(decision, side, "ORDER_REJECTED", "position_missing"),)
        quantity = (
            decision.quantity if side == "BUY" else self.portfolio.position.quantity
        )
        if quantity <= 0:
            return (self._notice(decision, side, "ORDER_REJECTED", "invalid_quantity"),)
        reserved = self._estimated_buy_cash(decision.signal_reference_price, quantity) if side == "BUY" else 0
        available_cash = self.portfolio.cash_won - self.portfolio.reserved_cash_won
        if side == "BUY" and reserved > available_cash:
            self.strategy_state = StrategyState(
                emitted_candidate_keys=resulting_state.emitted_candidate_keys,
            )
            return (self._notice(decision, side, "ORDER_REJECTED", "cash_reservation_exceeded"),)
        intent_id = _content_id("intent", {
            "run_id": self.run_id, "decision_id": decision.decision_id,
            "side": side, "quantity": quantity,
        })
        pending = PendingOrder(
            intent_id=intent_id,
            decision_id=decision.decision_id,
            symbol=decision.symbol,
            side=side,
            quantity=quantity,
            submitted_at=decision.decided_at,
            eligible_after=decision.decided_at,
            reserved_cash_won=reserved,
            session_profile=self.session_profile or "",
            research_session=(
                source_bar.research_session
                if source_bar is not None and source_bar.research_session
                else research_session_key(
                    _aware_datetime(decision.decided_at),
                    session_profile=self.session_profile or KRX_REGULAR_RESEARCH_PROFILE,
                ) or ""
            ),
        )
        self.portfolio = SimulationPortfolio(
            cash_won=self.portfolio.cash_won,
            reserved_cash_won=reserved,
            position=self.portfolio.position,
            pending_order=pending,
            realized_pnl_won=self.portfolio.realized_pnl_won,
            total_cost_won=self.portfolio.total_cost_won,
        )
        return (self._event(
            intent_id=intent_id, decision_id=decision.decision_id,
            event_type="ORDER_SUBMITTED", state="PENDING", symbol=decision.symbol,
            side=side, occurred_at=decision.decided_at, received_at=decision.decided_at,
            quantity=quantity, price=0, notional=0, commission=0, tax=0,
            realized=0, source_revision_id="", optimistic_exit_price=None, reason="",
        ),)

    def _pending_bar_rejection(self, pending: PendingOrder, bar: KrxMinuteBarFrame) -> str:
        if self.session_profile is None:
            return ""
        submitted = _aware_datetime(pending.submitted_at).astimezone(timezone.utc)
        expected_start = submitted.replace(second=0, microsecond=0) + timedelta(minutes=1)
        bar_start = _aware_datetime(bar.bar_start).astimezone(timezone.utc)
        if pending.research_session and bar.research_session != pending.research_session:
            return "session_boundary_before_fill"
        if bar_start != expected_start:
            return "next_tradable_bar_gap"
        if bar.market_phase != MarketPhase.CONTINUOUS.value:
            return "minute_bar_phase_cannot_prove_next_open_fill"
        return ""

    def _discard_pending(
        self, pending: PendingOrder, bar: KrxMinuteBarFrame, reason: str,
    ) -> ExecutionEvent:
        unsupported = reason == "minute_bar_phase_cannot_prove_next_open_fill"
        self.portfolio = SimulationPortfolio(
            cash_won=self.portfolio.cash_won, reserved_cash_won=0,
            position=self.portfolio.position, pending_order=None,
            realized_pnl_won=self.portfolio.realized_pnl_won,
            total_cost_won=self.portfolio.total_cost_won,
        )
        if pending.side == "BUY":
            self.strategy_state = StrategyState(
                emitted_candidate_keys=self.strategy_state.emitted_candidate_keys,
            )
        return self._event(
            intent_id=pending.intent_id, decision_id=pending.decision_id,
            event_type="ORDER_UNSUPPORTED" if unsupported else "ORDER_CENSORED",
            state="UNSUPPORTED" if unsupported else "CENSORED",
            symbol=pending.symbol, side=pending.side, occurred_at=bar.bar_start,
            received_at=bar.available_at, quantity=pending.quantity, price=0,
            notional=0, commission=0, tax=0, realized=0,
            source_revision_id=bar.revision_id, optimistic_exit_price=None, reason=reason,
        )

    def finalize(self, ended_at: str) -> tuple[ExecutionEvent, ...]:
        events: list[ExecutionEvent] = []
        pending = self.portfolio.pending_order
        if pending is not None:
            self.portfolio = SimulationPortfolio(
                cash_won=self.portfolio.cash_won,
                reserved_cash_won=0,
                position=self.portfolio.position,
                pending_order=None,
                realized_pnl_won=self.portfolio.realized_pnl_won,
                total_cost_won=self.portfolio.total_cost_won,
            )
            if pending.side == "BUY":
                self.strategy_state = StrategyState(
                    emitted_candidate_keys=self.strategy_state.emitted_candidate_keys,
                )
            events.append(self._event(
                intent_id=pending.intent_id, decision_id=pending.decision_id,
                event_type="ORDER_CENSORED", state="CENSORED", symbol=pending.symbol,
                side=pending.side, occurred_at=ended_at, received_at=ended_at,
                quantity=pending.quantity, price=0, notional=0, commission=0, tax=0,
                realized=0, source_revision_id="", optimistic_exit_price=None,
                reason="research_interval_ended_before_fill",
            ))
        if self.portfolio.position is not None:
            position = self.portfolio.position
            events.append(self._event(
                intent_id=position.entry_intent_id, decision_id="",
                event_type="POSITION_CENSORED", state="OPEN", symbol=position.symbol,
                side="", occurred_at=ended_at, received_at=ended_at,
                quantity=position.quantity, price=0, notional=0, commission=0, tax=0,
                realized=0, source_revision_id="", optimistic_exit_price=None,
                reason="research_interval_ended_with_open_position",
            ))
        return tuple(events)

    def _fill_pending(self, pending: PendingOrder, bar: KrxMinuteBarFrame) -> ExecutionEvent:
        cost = self.execution.cost_model
        if cost is None:
            self.portfolio = SimulationPortfolio(
                cash_won=self.portfolio.cash_won, reserved_cash_won=0,
                position=self.portfolio.position, pending_order=None,
                realized_pnl_won=self.portfolio.realized_pnl_won,
                total_cost_won=self.portfolio.total_cost_won,
            )
            return self._event(
                intent_id=pending.intent_id, decision_id=pending.decision_id,
                event_type="ORDER_UNSUPPORTED", state="UNSUPPORTED", symbol=pending.symbol,
                side=pending.side, occurred_at=bar.bar_start, received_at=bar.available_at,
                quantity=pending.quantity, price=0, notional=0, commission=0, tax=0,
                realized=0, source_revision_id=bar.revision_id, optimistic_exit_price=None,
                reason="cost_model_missing",
            )
        price = _slipped_price(bar.open, pending.side, cost.slippage_bps)
        notional = price * pending.quantity
        commission = notional * cost.commission_bps // 10_000
        tax = notional * cost.sell_tax_bps // 10_000 if pending.side == "SELL" else 0
        if pending.side == "BUY":
            required = notional + commission
            if required > self.portfolio.cash_won:
                self.portfolio = SimulationPortfolio(
                    cash_won=self.portfolio.cash_won, reserved_cash_won=0,
                    position=None, pending_order=None,
                    realized_pnl_won=self.portfolio.realized_pnl_won,
                    total_cost_won=self.portfolio.total_cost_won,
                )
                self.strategy_state = StrategyState(
                    emitted_candidate_keys=self.strategy_state.emitted_candidate_keys,
                )
                return self._event(
                    intent_id=pending.intent_id, decision_id=pending.decision_id,
                    event_type="ORDER_CANCELLED", state="CANCELLED", symbol=pending.symbol,
                    side="BUY", occurred_at=bar.bar_start, received_at=bar.available_at,
                    quantity=pending.quantity, price=price, notional=notional,
                    commission=commission, tax=0, realized=0,
                    source_revision_id=bar.revision_id, optimistic_exit_price=None,
                    reason="fill_cash_exceeded",
                )
            position = SimulationPosition(
                symbol=pending.symbol, quantity=pending.quantity, entry_price=price,
                entry_notional=notional, entry_commission=commission,
                opened_at=bar.bar_start, entry_intent_id=pending.intent_id,
            )
            self.portfolio = SimulationPortfolio(
                cash_won=self.portfolio.cash_won - required, reserved_cash_won=0,
                position=position, pending_order=None,
                realized_pnl_won=self.portfolio.realized_pnl_won,
                total_cost_won=self.portfolio.total_cost_won + commission,
            )
            self.strategy_state = StrategyState(
                status="open", symbol=pending.symbol, entry_price=price,
                position_quantity=pending.quantity, opened_at=bar.bar_start,
                emitted_candidate_keys=self.strategy_state.emitted_candidate_keys,
            )
            return self._event(
                intent_id=pending.intent_id, decision_id=pending.decision_id,
                event_type="FILL", state="FILLED", symbol=pending.symbol, side="BUY",
                occurred_at=bar.bar_start, received_at=bar.available_at,
                quantity=pending.quantity, price=price, notional=notional,
                commission=commission, tax=0, realized=0,
                source_revision_id=bar.revision_id, optimistic_exit_price=None, reason="",
            )
        return self._sell_fill(
            pending.intent_id, pending.decision_id, bar, price, pending.quantity,
            event_type="FILL", reason="",
        )

    def _apply_intrabar_risk(self, bar: KrxMinuteBarFrame) -> ExecutionEvent | None:
        position = self.portfolio.position
        if position is None:
            return None
        stop_price = position.entry_price * (10_000 - self.strategy.stop_loss_bps) // 10_000
        target_price = (
            position.entry_price * (10_000 + self.strategy.target_bps) + 9_999
        ) // 10_000
        stop_hit = bar.low <= stop_price
        target_hit = bar.high >= target_price
        if not stop_hit and not target_hit:
            return None
        base_price = stop_price if stop_hit else target_price
        reason = "stop_and_target_same_bar_conservative_stop" if stop_hit and target_hit else (
            "stop_triggered" if stop_hit else "target_triggered"
        )
        optimistic = target_price if stop_hit and target_hit else None
        cost = self.execution.cost_model
        if cost is None:
            return None
        price = _slipped_price(base_price, "SELL", cost.slippage_bps)
        event = self._sell_fill(
            _content_id("risk_intent", {
                "run_id": self.run_id, "entry_intent_id": position.entry_intent_id,
                "bar_revision_id": bar.revision_id, "reason": reason,
            }),
            "", bar, price, position.quantity, event_type="RISK_FILL", reason=reason,
            optimistic_exit_price=optimistic,
        )
        return event

    def _sell_fill(
        self,
        intent_id: str,
        decision_id: str,
        bar: KrxMinuteBarFrame,
        price: int,
        quantity: int,
        *,
        event_type: str,
        reason: str,
        optimistic_exit_price: int | None = None,
    ) -> ExecutionEvent:
        position = self.portfolio.position
        cost = self.execution.cost_model
        assert position is not None and cost is not None
        quantity = min(quantity, position.quantity)
        notional = price * quantity
        commission = notional * cost.commission_bps // 10_000
        tax = notional * cost.sell_tax_bps // 10_000
        proceeds = notional - commission - tax
        entry_basis = position.entry_price * quantity + (
            position.entry_commission * quantity // position.quantity
        )
        realized = proceeds - entry_basis
        remaining = position.quantity - quantity
        remaining_position = None if remaining == 0 else SimulationPosition(
            symbol=position.symbol, quantity=remaining, entry_price=position.entry_price,
            entry_notional=position.entry_price * remaining,
            entry_commission=position.entry_commission - (
                position.entry_commission * quantity // position.quantity
            ),
            opened_at=position.opened_at, entry_intent_id=position.entry_intent_id,
        )
        self.portfolio = SimulationPortfolio(
            cash_won=self.portfolio.cash_won + proceeds, reserved_cash_won=0,
            position=remaining_position, pending_order=None,
            realized_pnl_won=self.portfolio.realized_pnl_won + realized,
            total_cost_won=self.portfolio.total_cost_won + commission + tax,
        )
        if remaining == 0:
            fill_time = bar.bar_end if event_type == "RISK_FILL" else bar.bar_start
            ended = _aware_datetime(fill_time).astimezone(timezone.utc)
            self.strategy_state = StrategyState(
                status="cooldown", cooldown_until=(
                    ended + timedelta(seconds=self.strategy.cooldown_seconds)
                ).isoformat(),
                emitted_candidate_keys=self.strategy_state.emitted_candidate_keys,
            )
        return self._event(
            intent_id=intent_id, decision_id=decision_id, event_type=event_type,
            state="FILLED", symbol=position.symbol, side="SELL",
            occurred_at=bar.bar_end if event_type == "RISK_FILL" else bar.bar_start,
            received_at=bar.available_at, quantity=quantity, price=price,
            notional=notional, commission=commission, tax=tax, realized=realized,
            source_revision_id=bar.revision_id,
            optimistic_exit_price=optimistic_exit_price, reason=reason,
        )

    def _mark(self, bar: KrxMinuteBarFrame) -> ExecutionEvent:
        position = self.portfolio.position
        assert position is not None
        return self._event(
            intent_id=position.entry_intent_id, decision_id="", event_type="MARK",
            state="OPEN", symbol=position.symbol, side="", occurred_at=bar.bar_end,
            received_at=bar.available_at, quantity=position.quantity, price=bar.close,
            notional=position.quantity * bar.close, commission=0, tax=0, realized=0,
            source_revision_id=bar.revision_id, optimistic_exit_price=None, reason="",
        )

    def _notice(
        self, decision: StrategyDecision, side: str, event_type: str, reason: str,
    ) -> ExecutionEvent:
        return self._event(
            intent_id=_content_id("intent", {
                "run_id": self.run_id, "decision_id": decision.decision_id, "side": side,
            }),
            decision_id=decision.decision_id, event_type=event_type, state="REJECTED",
            symbol=decision.symbol, side=side, occurred_at=decision.decided_at,
            received_at=decision.decided_at, quantity=decision.quantity,
            price=0, notional=0, commission=0, tax=0, realized=0,
            source_revision_id="", optimistic_exit_price=None, reason=reason,
        )

    def _estimated_buy_cash(self, reference_price: int, quantity: int) -> int:
        cost = self.execution.cost_model
        if cost is None:
            return reference_price * quantity
        price = _slipped_price(reference_price, "BUY", cost.slippage_bps)
        notional = price * quantity
        return notional + notional * cost.commission_bps // 10_000

    def _event(
        self,
        *,
        intent_id: str,
        decision_id: str,
        event_type: str,
        state: str,
        symbol: str,
        side: str,
        occurred_at: str,
        received_at: str,
        quantity: int,
        price: int,
        notional: int,
        commission: int,
        tax: int,
        realized: int,
        source_revision_id: str,
        optimistic_exit_price: int | None,
        reason: str,
    ) -> ExecutionEvent:
        body = {
            "run_id": self.run_id, "execution_environment": "simulation",
            "account_ref": "research-single-position", "intent_id": intent_id,
            "decision_id": decision_id, "event_type": event_type, "state": state,
            "symbol": normalize_stock_code(symbol), "side": side,
            "occurred_at": occurred_at, "received_at": received_at,
            "quantity": quantity, "price": price, "notional_won": notional,
            "commission_won": commission, "tax_won": tax,
            "total_cost_won": commission + tax, "realized_pnl_won": realized,
            "cash_after_won": self.portfolio.cash_won,
            "reserved_cash_after_won": self.portfolio.reserved_cash_won,
            "position_quantity_after": (
                self.portfolio.position.quantity if self.portfolio.position else 0
            ),
            "source_revision_id": source_revision_id,
            "optimistic_exit_price": optimistic_exit_price, "reason": reason,
        }
        return ExecutionEvent(event_id=_content_id("execution", body), **body)


def _slipped_price(price: int, side: str, slippage_bps: int) -> int:
    if side == "BUY":
        return (price * (10_000 + slippage_bps) + 9_999) // 10_000
    return price * (10_000 - slippage_bps) // 10_000


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def _aware_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("execution timestamps must be timezone-aware")
    return parsed
