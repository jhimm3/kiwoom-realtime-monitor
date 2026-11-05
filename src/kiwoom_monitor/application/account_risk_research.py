"""EX-RISK cash allocation over an explicit synthetic order and fill stream."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import accumulate

from kiwoom_monitor.domain.order_contract import OrderSide, OrderState, TERMINAL_ORDER_STATES
from kiwoom_monitor.domain.time_basis import aware_iso_kst


@dataclass
class _Order:
    plan_id: str
    symbol: str
    side: OrderSide
    tranche: int | None
    quantity: int
    limit_price_won: int
    fee_budget_won: int
    state: OrderState = field(default=OrderState.ACCEPTED, compare=False)
    filled: int = field(default=0, compare=False)
    fees: int = field(default=0, compare=False)
    cancel_reported_filled: int | None = field(default=None, compare=False)

    @property
    def remaining(self) -> int:
        return self.quantity - self.filled

    @property
    def reserve_won(self) -> int:
        if self.side is not OrderSide.BUY or self.state in TERMINAL_ORDER_STATES:
            return 0
        return self.remaining * self.limit_price_won + self.fee_budget_won - self.fees


@dataclass
class _Lot:
    quantity: int
    cost_won: int


def _integer(source: Mapping, key: str, *, minimum: int = 0) -> int:
    value = source.get(key)
    if type(value) is not int or value < minimum:
        raise ValueError(f"{key} must be an integer at least {minimum}")
    return value


def _identity(source: Mapping, key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value


@dataclass(frozen=True)
class _Mark:
    mark_id: str
    observed_at: datetime
    price_won: int


class _Valuation:
    """Explicit as-of marks and drawdown on complete supplied account cuts."""

    def __init__(self, initial_cash: int, mark_max_age_seconds: int | None) -> None:
        self.cut: datetime | None = None
        self.marks: dict[str, _Mark] = {}
        self.mark_facts: dict[str, tuple[str, datetime, int]] = {}
        self.conflicting: set[str] = set()
        self.max_age = None if mark_max_age_seconds is None else timedelta(seconds=mark_max_age_seconds)
        self.max_age_seconds = mark_max_age_seconds
        self.equity: int | None = initial_cash
        self.peak = initial_cash
        self.drawdown = 0
        self.drawdown_bps = 0 if initial_cash else None
        self.known_cuts = 0
        self.unknown_cuts = 0

    def advance(self, event: Mapping) -> datetime:
        cut = aware_iso_kst(_identity(event, "available_at"))
        if self.cut is not None and cut < self.cut:
            raise ValueError("event available_at must be nondecreasing in sequence order")
        self.cut = cut
        return cut

    def mark(self, event: Mapping, cut: datetime) -> tuple[str, str]:
        symbol = _identity(event, "symbol")
        mark = _Mark(
            _identity(event, "mark_id"), aware_iso_kst(_identity(event, "observed_at")),
            _integer(event, "price_won", minimum=1),
        )
        if mark.observed_at > cut:
            raise ValueError("mark observed_at cannot be later than available_at")
        fact = symbol, mark.observed_at, mark.price_won
        if mark.mark_id in self.mark_facts:
            if self.mark_facts[mark.mark_id] != fact:
                raise ValueError("conflicting evidence for the same mark_id")
            return "NOOP", "duplicate_mark"
        self.mark_facts[mark.mark_id] = fact
        previous = self.marks.get(symbol)
        if previous is not None:
            if mark.observed_at < previous.observed_at:
                return "NOT_APPLIED", "older_mark_observation"
            if mark.observed_at == previous.observed_at:
                if mark.price_won == previous.price_won:
                    return "NOOP", "duplicate_mark_observation"
                self.conflicting.add(symbol)
                return "UNRESOLVED", "conflicting_mark_observation"
        self.marks[symbol] = mark
        self.conflicting.discard(symbol)
        return "APPLIED", ""

    def point(self, cut: datetime, cash: int, held: Mapping[str, int], unresolved: set[str]) -> dict:
        marked_won = 0
        used = {}
        missing, stale, conflicting = [], [], []
        for symbol in sorted(held):
            mark = self.marks.get(symbol)
            if mark is None:
                missing.append(symbol)
            elif symbol in self.conflicting:
                conflicting.append(symbol)
            elif self.max_age is not None and cut - mark.observed_at > self.max_age:
                stale.append(symbol)
            else:
                marked_won += held[symbol] * mark.price_won
                used[symbol] = mark.mark_id
        if missing or stale or conflicting or unresolved:
            self.equity = None
            self.unknown_cuts += 1
        else:
            self.equity = cash + marked_won
            self.known_cuts += 1
            self.peak = max(self.peak, self.equity)
            drawdown = self.peak - self.equity
            self.drawdown = max(self.drawdown, drawdown)
            if self.peak:
                self.drawdown_bps = max(self.drawdown_bps or 0, drawdown * 10_000 // self.peak)
        return {
            "as_of": cut.isoformat(), "equity_won": self.equity, "mark_ids_by_symbol": used,
            "missing_symbols": missing, "stale_symbols": stale, "conflicting_symbols": conflicting,
            "unresolved_execution_order_ids": sorted(unresolved),
        }

    def summary(self) -> dict:
        complete = not self.unknown_cuts
        return {
            "scope": "supplied_event_cuts",
            "mark_basis": "latest_explicit_observation_available_at_cut",
            "status": "COMPLETE" if complete else "PARTIAL",
            "mark_max_age_seconds": self.max_age_seconds,
            "equity_won": self.equity,
            "maximum_drawdown_won": self.drawdown if complete else None,
            "maximum_drawdown_bps": self.drawdown_bps if complete else None,
            "known_drawdown_lower_bound_won": self.drawdown,
            "known_drawdown_lower_bound_bps": self.drawdown_bps,
            "known_cut_count": self.known_cuts,
            "unknown_cut_count": self.unknown_cuts,
        }


def _proposal(event: Mapping) -> _Order:
    side = OrderSide(event.get("side"))
    tranche = _integer(event, "tranche") if side is OrderSide.BUY else None
    if side is OrderSide.SELL and event.get("tranche") is not None:
        raise ValueError("a SELL proposal has no BUY tranche")
    return _Order(
        _identity(event, "plan_id"), _identity(event, "symbol"), side, tranche,
        _integer(event, "quantity", minimum=1), _integer(event, "limit_price_won", minimum=1),
        _integer(event, "fee_budget_won"),
    )


def _close_lots(lots: deque[_Lot], quantity: int) -> int:
    basis = 0
    while quantity:
        lot = lots[0]
        consumed = min(quantity, lot.quantity)
        cost = lot.cost_won * consumed // lot.quantity
        basis += cost
        lot.quantity -= consumed
        lot.cost_won -= cost
        quantity -= consumed
        if not lot.quantity:
            lots.popleft()
    return basis


def replay_account_risk_experiment(
    scenario: Mapping, policy: Mapping, *, checkpoint: Callable[[], None] = lambda: None,
) -> dict:
    """Replay one fixed or explicitly split policy with the same account cash.

    Sequence is the supplied evidence order. A proposal consumes capacity
    without producing any execution. Only the supplied detailed FILL events
    move cash or holdings. A cancel request, or a confirmation with missing
    detail, leaves its reservation active.
    """
    if scenario.get("version") != "account-risk-scenario/v1":
        raise ValueError("unsupported account risk scenario")
    if policy.get("version") != "account-risk-allocation/v1":
        raise ValueError("unsupported account risk allocation")
    weights = policy.get("tranche_bps")
    if (not isinstance(weights, (list, tuple)) or not weights
            or any(type(weight) is not int or weight <= 0 for weight in weights) or sum(weights) != 10_000):
        raise ValueError("tranche_bps must contain positive integers totaling 10000")
    initial_cash = _integer(scenario, "initial_cash_won")
    budget = _integer(scenario, "plan_budget_won", minimum=1)
    boundaries = [0, *(budget * cumulative // 10_000 for cumulative in accumulate(weights))]
    limits = [end - start for start, end in zip(boundaries, boundaries[1:])]
    events = scenario.get("events")
    if not isinstance(events, (list, tuple)):
        raise ValueError("events must be a sequence of evidence objects")
    age = policy.get("mark_max_age_seconds")
    if age is not None:
        age = _integer(policy, "mark_max_age_seconds")
    valued = "mark_max_age_seconds" in policy or any(
        isinstance(event, Mapping) and event.get("kind") == "MARK" for event in events
    )
    valuation = _Valuation(initial_cash, age) if valued else None

    cash = initial_cash
    orders: dict[str, _Order] = {}
    active: dict[str, _Order] = {}
    lots: dict[tuple[str, str], deque[_Lot]] = defaultdict(deque)
    spent: dict[tuple[str, int], int] = defaultdict(int)
    realized: dict[str, int] = defaultdict(int)
    fills: dict[tuple[str, str], tuple[int, int, int]] = {}
    rejections: dict[str, str] = {}
    unresolved_fills: set[str] = set()
    results = []
    sequence = peak_reserved = peak_committed = 0

    def quantity(key):
        return sum(lot.quantity for lot in lots.get(key, ()))

    def reserved_cash():
        return sum(order.reserve_won for order in active.values())

    def record_fill(order_id, order, event):
        nonlocal cash
        execution_id = _identity(event, "execution_id")
        size = _integer(event, "quantity", minimum=1)
        price = _integer(event, "price_won", minimum=1)
        fee = _integer(event, "fee_won")
        fill_key = order_id, execution_id
        detail = size, price, fee
        if fill_key in fills:
            if fills[fill_key] != detail:
                raise ValueError("conflicting detail for the same execution")
            return "NOOP", "duplicate_fill"
        fills[fill_key] = detail
        if order is None:
            return "UNRESOLVED", "order_not_observed"
        if order.state is OrderState.REJECTED:
            return "NOT_APPLIED", "proposal_rejected"
        if order.state in TERMINAL_ORDER_STATES:
            return "UNRESOLVED", "fill_after_confirmed_terminal_order"
        if size > order.remaining or (
            order.cancel_reported_filled is not None and order.filled + size > order.cancel_reported_filled
        ):
            return "UNRESOLVED", "fill_quantity_conflicts_with_order"
        if fee + order.fees > order.fee_budget_won:
            return "UNRESOLVED", "fill_fee_exceeds_budget"

        key = order.plan_id, order.symbol
        gross = price * size
        if order.side is OrderSide.BUY:
            if price > order.limit_price_won:
                return "UNRESOLVED", "fill_outside_limit"
            cost = gross + fee
            lots[key].append(_Lot(size, cost))
            spent[order.plan_id, order.tranche] += cost
            cash -= cost
        else:
            if price < order.limit_price_won or fee > gross:
                return "UNRESOLVED", "fill_outside_limit_or_proceeds"
            if size > quantity(key):
                return "UNRESOLVED", "sell_fill_exceeds_plan_position"
            proceeds = gross - fee
            realized[order.plan_id] += proceeds - _close_lots(lots[key], size)
            cash += proceeds
        order.filled += size
        order.fees += fee
        if not order.remaining:
            order.state = OrderState.FILLED
        elif order.cancel_reported_filled == order.filled:
            order.state = OrderState.CANCELLED
        elif order.state is not OrderState.CANCEL_PENDING:
            order.state = OrderState.PARTIALLY_FILLED
        return "APPLIED", ""

    def cancel_order(order, kind, event):
        if kind == "CANCEL_REQUESTED":
            if order.state in TERMINAL_ORDER_STATES or order.state is OrderState.CANCEL_PENDING:
                return "NOOP", "order_already_terminal_or_pending"
            order.state = OrderState.CANCEL_PENDING
            return "APPLIED", ""
        confirmed_filled = _integer(event, "filled_quantity")
        if order.state is OrderState.REJECTED:
            return "NOT_APPLIED", "proposal_rejected"
        prior = order.cancel_reported_filled
        if (confirmed_filled < order.filled or confirmed_filled > order.quantity
                or (prior is not None and confirmed_filled < prior)):
            return "UNRESOLVED", "cancel_quantity_conflicts_with_order"
        if order.state in TERMINAL_ORDER_STATES:
            if confirmed_filled == order.filled:
                return "NOOP", "order_already_terminal"
            return "UNRESOLVED", "cancel_quantity_conflicts_with_order"
        order.cancel_reported_filled = confirmed_filled
        if confirmed_filled > order.filled:
            order.state = OrderState.CANCEL_PENDING
            return "UNRESOLVED", "cancel_missing_detailed_fills"
        order.state = OrderState.CANCELLED
        return "APPLIED", ""

    checkpoint()
    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError("each event must be an evidence object")
        current = _integer(event, "sequence", minimum=1)
        if current <= sequence:
            raise ValueError("event sequence must be strictly increasing")
        sequence = current
        kind = _identity(event, "kind")
        cut = valuation.advance(event) if valuation is not None else None
        order_id = _identity(event, "order_id") if kind != "MARK" else ""
        reason = ""
        if kind == "MARK":
            status, reason = valuation.mark(event, cut)
        elif kind == "PROPOSE":
            order = _proposal(event)
            prior = orders.get(order_id)
            if prior is not None:
                if order != prior:
                    raise ValueError("conflicting proposal for the same order")
                status, reason = "NOOP", "duplicate_proposal"
            else:
                if order.side is OrderSide.BUY:
                    slot = order.plan_id, order.tranche
                    slot_reserved = sum(
                        item.reserve_won for item in active.values() if (item.plan_id, item.tranche) == slot
                    )
                    if order.tranche >= len(limits):
                        reason = "tranche_disabled"
                    elif spent[slot] + slot_reserved + order.reserve_won > limits[order.tranche]:
                        reason = "plan_tranche_budget"
                    elif order.reserve_won > cash - reserved_cash():
                        reason = "account_cash"
                else:
                    key = order.plan_id, order.symbol
                    pending_sell = sum(
                        item.remaining for item in active.values()
                        if item.side is OrderSide.SELL and (item.plan_id, item.symbol) == key
                    )
                    if order.quantity > quantity(key) - pending_sell:
                        reason = "plan_sell_quantity"
                orders[order_id] = order
                if reason:
                    order.state = OrderState.REJECTED
                    rejections[order_id] = reason
                    status = "REJECTED"
                else:
                    active[order_id] = order
                    status = "ACCEPTED"
        elif kind in {"FILL", "CANCEL_REQUESTED", "CANCEL_CONFIRMED"}:
            order = orders.get(order_id)
            if kind == "FILL":
                status, reason = record_fill(order_id, order, event)
                if status == "UNRESOLVED":
                    unresolved_fills.add(order_id)
            elif order is None:
                status, reason = "UNRESOLVED", "order_not_observed"
            else:
                status, reason = cancel_order(order, kind, event)
            if order is not None and order.state in TERMINAL_ORDER_STATES:
                active.pop(order_id, None)
        else:
            raise ValueError(f"unsupported event kind: {kind}")
        reserved = reserved_cash()
        open_basis = sum(lot.cost_won for book in lots.values() for lot in book)
        peak_reserved = max(peak_reserved, reserved)
        peak_committed = max(peak_committed, open_basis + reserved)
        result = {
            "event": deepcopy(dict(event)), "status": status, "reason": reason,
            "cash_won": cash, "reserved_cash_won": reserved,
        }
        if valuation is not None:
            held: dict[str, int] = defaultdict(int)
            for (_, symbol), book in lots.items():
                if book:
                    held[symbol] += sum(lot.quantity for lot in book)
            unresolved_cancel = {
                key for key, order in active.items()
                if order.cancel_reported_filled is not None and order.cancel_reported_filled > order.filled
            }
            result["valuation"] = valuation.point(cut, cash, held, unresolved_fills | unresolved_cancel)
        results.append(result)
        checkpoint()

    reserved = reserved_cash()
    return {
        "version": "account-risk-result/v1", "scope": "synthetic_detailed_fills",
        "initial_cash_won": initial_cash, "plan_budget_won": budget, "tranche_bps": list(weights),
        "tranche_limits_won": limits, "cash_won": cash, "reserved_cash_won": reserved,
        "available_cash_won": cash - reserved, "peak_reserved_cash_won": peak_reserved,
        "peak_committed_won": peak_committed, "realized_pnl_won": sum(realized.values()),
        "realized_pnl_by_plan_won": dict(sorted(realized.items())),
        "positions": [
            {"plan_id": plan, "symbol": symbol, "quantity": quantity((plan, symbol)),
             "cost_basis_won": sum(lot.cost_won for lot in book)}
            for (plan, symbol), book in sorted(lots.items()) if book
        ],
        "orders": [
            {"order_id": key, "plan_id": order.plan_id, "symbol": order.symbol, "side": order.side.value,
             "state": order.state.value, "requested_quantity": order.quantity, "filled_quantity": order.filled,
             "unfilled_quantity": order.remaining, "reserved_cash_won": order.reserve_won,
             "cancel_reported_filled_quantity": order.cancel_reported_filled,
             "rejection_reason": rejections.get(key, "")}
            for key, order in orders.items()
        ],
        "events": results,
        **({"valuation": valuation.summary()} if valuation is not None else {}),
    }
