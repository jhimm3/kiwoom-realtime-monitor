"""Point-in-time EX-DD research decisions and externally confirmed fill state."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Iterable, Literal, Mapping

from kiwoom_monitor.application.market_session_schedule import (
    MarketPhase, MarketSession, SessionSupport, SessionWindow, TradingDayEvidence,
    decode_strategy_references, trading_day_as_of,
)
from kiwoom_monitor.domain.time_basis import KST


BPS = 10_000


@dataclass(frozen=True)
class DailyDeclineSpec:
    lookback_sessions: int
    minimum_decline_bps: int
    minimum_relative_decline_bps: int
    minimum_volatility_multiple_bps: int
    budget_won: int
    tranche_weights_bps: tuple[int, ...]
    tranche_drop_units_bps: tuple[int, ...]
    max_hold_sessions: int
    require_intact_catalyst: bool

    def __post_init__(self) -> None:
        weights, offsets = self.tranche_weights_bps, self.tranche_drop_units_bps
        positive = (self.lookback_sessions, self.minimum_decline_bps,
                    self.minimum_volatility_multiple_bps, self.budget_won, self.max_hold_sessions)
        if (any(type(value) is not int or value <= 0 for value in positive)
                or type(self.minimum_relative_decline_bps) is not int
                or self.minimum_relative_decline_bps < 0
                or type(self.require_intact_catalyst) is not bool):
            raise ValueError("invalid DD research limits")
        if (not weights or len(weights) != len(offsets)
                or any(type(value) is not int or value <= 0 for value in weights)
                or sum(weights) != BPS or offsets[0] != 0
                or any(type(value) is not int for value in offsets)
                or any(current <= prior for prior, current in zip(offsets, offsets[1:]))):
            raise ValueError("invalid DD tranche weights or offsets")


@dataclass(frozen=True)
class DailyDeclineBasis:
    symbol: str
    venue: str
    first_day: date
    last_day: date
    available_at: datetime
    evidence_id: str
    adjustment_mode: str


@dataclass(frozen=True)
class DailyDeclineBenchmark:
    """Same trading day's benchmark close-to-close return, in basis points."""

    venue: str
    trading_date: date
    available_at: datetime
    evidence_id: str
    return_bps: int


@dataclass(frozen=True)
class DailyDeclineCatalyst:
    state: Literal["INTACT", "UNKNOWN", "DAMAGED"]
    available_at: datetime
    evidence_id: str


@dataclass(frozen=True)
class DailyDeclineSetup:
    plan_id: str
    spec: DailyDeclineSpec
    symbol: str
    venue: str
    signal_day: date
    decided_at: datetime
    drawdown_bps: int
    relative_decline_bps: int
    volatility_numerator: int
    volatility_denominator: int
    catalyst_state: Literal["INTACT", "UNKNOWN", "DAMAGED"]
    evidence_ids: tuple[str, ...]

    @property
    def volatility_bps(self) -> int:
        return self.volatility_numerator * BPS // self.volatility_denominator


@dataclass(frozen=True)
class DailyDeclineSetupResult:
    status: Literal["QUALIFIED", "NOT_QUALIFIED", "UNKNOWN"]
    reason: str
    setup: DailyDeclineSetup | None = None

    def to_document(self) -> dict[str, object]:
        document = {"status": self.status, "reason": self.reason}
        if self.setup is not None:
            document.update(
                setup_id=self.setup.plan_id, drawdown_bps=self.setup.drawdown_bps,
                relative_decline_bps=self.setup.relative_decline_bps, volatility_bps=self.setup.volatility_bps,
                catalyst_state=self.setup.catalyst_state, evidence_ids=list(self.setup.evidence_ids),
            )
        return document


def decode_daily_decline_setup_inputs(document: Mapping[str, object], policy: Mapping[str, object]) -> dict:
    """Read the same explicit setup inputs for frozen research and native Shadow."""
    def proof(name, proof_type, *dates):
        raw = document.get(name)
        if raw is None:
            return None
        values = {**raw, "available_at": datetime.fromisoformat(raw["available_at"])}
        for field in dates:
            values[field] = date.fromisoformat(raw[field])
        return proof_type(**values)

    try:
        symbol, venue = document["symbol"], document["venue"]
        if (not isinstance(symbol, str) or len(symbol) != 6 or not symbol.isascii()
                or not symbol.isalnum() or venue != "KRX"):
            raise ValueError("DD native setup requires a scoped KRX stock code")
        policy = dict(policy)
        for name in ("tranche_weights_bps", "tranche_drop_units_bps"):
            policy[name] = tuple(policy[name])
        calendar, _ = decode_strategy_references(document["strategy_references"])
        return dict(
            spec=DailyDeclineSpec(**policy), symbol=symbol, venue=venue,
            signal_day=date.fromisoformat(document["signal_day"]), calendar=calendar,
            basis=proof("basis", DailyDeclineBasis, "first_day", "last_day"),
            benchmark=proof("benchmark", DailyDeclineBenchmark, "trading_date"),
            catalyst=proof("catalyst", DailyDeclineCatalyst),
        )
    except (KeyError, TypeError) as error:
        raise ValueError("invalid DD native setup proof or policy") from error


def _aware(value: datetime) -> bool:
    return value.utcoffset() is not None


def _visible(available_at: datetime, evidence_id: str, cut: datetime) -> bool:
    return bool(evidence_id and _aware(available_at) and available_at <= cut)


def _identity(prefix: str, values: object) -> str:
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()}"


def _catalyst_state(catalyst: DailyDeclineCatalyst | None, cut: datetime):
    if catalyst is None or not _visible(catalyst.available_at, catalyst.evidence_id, cut):
        return "UNKNOWN", ()
    if catalyst.state not in {"INTACT", "UNKNOWN", "DAMAGED"}:
        raise ValueError("invalid DD catalyst state")
    return catalyst.state, (catalyst.evidence_id,)


def _daily_window(observations: Iterable[Mapping[str, object]], symbol: str, venue: str,
                  days: tuple[date, ...], cut: datetime, adjustment_mode: str,
                  session_closed_at: Mapping[date, datetime] | None):
    keys = {day.isoformat() for day in days}
    latest = {}
    subject = f"{symbol}:{venue}"
    for row in observations:
        if row.get("kind") != "daily_bar" or row.get("subject") != subject:
            continue
        key = row.get("observation_key")
        if not isinstance(key, str) or key not in keys:
            continue
        try:
            available = datetime.fromisoformat(str(row.get("available_at")))
        except ValueError:
            continue
        if not _aware(available) or available > cut:
            continue
        sequence = row.get("accepted_sequence")
        order = (available.astimezone(UTC), sequence if type(sequence) is int else -1,
                 str(row.get("revision_id", "")))
        if key not in latest or order > latest[key][0]:
            latest[key] = (order, row)

    rows, revisions = [], []
    for day in days:
        key = day.isoformat()
        if key not in latest:
            return None
        (available, sequence, revision), row = latest[key]
        payload = row.get("payload")
        close_at = session_closed_at.get(day) if session_closed_at is not None else None
        # The most recent visible revision can invalidate an earlier complete bar.
        if (not revision or sequence < 0 or available.astimezone(KST).date() < day
                or (session_closed_at is not None and (close_at is None or available < close_at))
                or row.get("venue") != venue or row.get("source_id") != "kiwoom-ka10081"
                or row.get("completeness") != "complete" or row.get("value_kind") != "actual"
                or not isinstance(payload, Mapping) or payload.get("version") != "daily_bar/v1"
                or (payload.get("code"), payload.get("market"), payload.get("trading_date")) != (symbol, venue, key)
                or payload.get("adjustment_mode") != adjustment_mode):
            return None
        prices = tuple(payload.get(field) for field in ("open", "high", "low", "close"))
        if any(type(price) is not int or price <= 0 for price in prices):
            return None
        opening, high, low, close = prices
        if not low <= min(opening, close) <= max(opening, close) <= high:
            return None
        rows.append((high, low, close))
        revisions.append(revision)
    return rows, tuple(revisions)


def select_daily_decline_setup(
    observations: Iterable[Mapping[str, object]], *, spec: DailyDeclineSpec,
    symbol: str, venue: str, signal_day: date, prior_days: Iterable[date], cut: datetime,
    basis: DailyDeclineBasis | None, benchmark: DailyDeclineBenchmark | None,
    catalyst: DailyDeclineCatalyst | None = None,
    session_evidence_ids: tuple[str, ...] = (),
    session_closed_at: Mapping[date, datetime] | None = None,
) -> DailyDeclineSetupResult:
    """Read only the declared verified session window, including its final signal bar."""
    if not _aware(cut):
        raise ValueError("DD cutoff must be timezone-aware")
    history = tuple(prior_days)
    if (len(history) != spec.lookback_sessions or history != tuple(sorted(set(history)))
            or history[-1] >= signal_day):
        return DailyDeclineSetupResult("UNKNOWN", "session_window_unverified")
    if (basis is None or (basis.symbol, basis.venue) != (symbol, venue)
            or basis.adjustment_mode != "1" or basis.first_day > history[0] or basis.last_day < signal_day
            or not _visible(basis.available_at, basis.evidence_id, cut)):
        return DailyDeclineSetupResult("UNKNOWN", "adjusted_price_basis_unverified")
    if (benchmark is None or (benchmark.venue, benchmark.trading_date) != (venue, signal_day)
            or not _visible(benchmark.available_at, benchmark.evidence_id, cut)
            or type(benchmark.return_bps) is not int):
        return DailyDeclineSetupResult("UNKNOWN", "benchmark_unavailable")
    window = _daily_window(observations, symbol, venue, (*history, signal_day), cut,
                           basis.adjustment_mode, session_closed_at)
    if window is None:
        return DailyDeclineSetupResult("UNKNOWN", "daily_window_unavailable")
    rows, revisions = window
    prior, signal_close = rows[:-1], rows[-1][2]
    previous_close = prior[-1][2]
    prior_high = max(high for high, _, _ in prior)
    volatility = sum(high - low for high, low, _ in prior)
    volatility_base = len(prior) * previous_close
    if volatility == 0:
        return DailyDeclineSetupResult("UNKNOWN", "prior_volatility_unavailable")
    drawdown = prior_high - signal_close
    drawdown_bps = drawdown * BPS // prior_high
    relative_decline_bps = (benchmark.return_bps * previous_close
                            - (signal_close - previous_close) * BPS) // previous_close
    if (drawdown_bps < spec.minimum_decline_bps
            or relative_decline_bps < spec.minimum_relative_decline_bps
            or drawdown * volatility_base * BPS < spec.minimum_volatility_multiple_bps * prior_high * volatility):
        return DailyDeclineSetupResult("NOT_QUALIFIED", "decline_threshold_not_met")
    catalyst_state, catalyst_refs = _catalyst_state(catalyst, cut)
    if spec.require_intact_catalyst and catalyst_state != "INTACT":
        status = "UNKNOWN" if catalyst_state == "UNKNOWN" else "NOT_QUALIFIED"
        return DailyDeclineSetupResult(status, "intact_catalyst_unconfirmed")
    evidence_ids = (*revisions, basis.evidence_id, benchmark.evidence_id, *catalyst_refs, *session_evidence_ids)
    plan_id = _identity("dd-v1", (symbol, venue, signal_day.isoformat(), evidence_ids, asdict(spec)))
    return DailyDeclineSetupResult("QUALIFIED", "decline_threshold_met", DailyDeclineSetup(
        plan_id, spec, symbol, venue, signal_day, cut, drawdown_bps, relative_decline_bps,
        volatility, volatility_base, catalyst_state, evidence_ids,
    ))


def evaluate_daily_decline_shadow(
    observations: Iterable[Mapping[str, object]], *, spec: DailyDeclineSpec, symbol: str,
    venue: str, signal_day: date, cut: datetime, calendar: Iterable[TradingDayEvidence],
    basis: DailyDeclineBasis | None, benchmark: DailyDeclineBenchmark | None,
    catalyst: DailyDeclineCatalyst | None = None,
) -> DailyDeclineSetupResult:
    """Evaluate a setup using native daily revisions and an admitted calendar window."""
    if not _aware(cut):
        raise ValueError("DD cutoff must be timezone-aware")
    calendar_by_day: dict[date, list[TradingDayEvidence]] = {}
    for revision in calendar:
        if revision.venue == venue and revision.trading_date <= signal_day:
            calendar_by_day.setdefault(revision.trading_date, []).append(revision)

    current = signal_day
    witnessed: list[TradingDayEvidence] = []
    closes: dict[date, datetime] = {}
    while len(closes) <= spec.lookback_sessions:
        day = trading_day_as_of(current, venue=venue, as_of=cut, evidence=calendar_by_day.get(current, ()))
        if day is None or day.sessions is None or (current == signal_day and not day.sessions):
            return DailyDeclineSetupResult("UNKNOWN", "session_window_unverified")
        if current == signal_day and cut < day.sessions[-1].ends_at:
            return DailyDeclineSetupResult("UNKNOWN", "signal_session_still_open")
        if day.sessions:
            closes[current] = day.sessions[-1].ends_at
        witnessed.append(day)
        if len(closes) > spec.lookback_sessions:
            break
        if current == date.min:
            return DailyDeclineSetupResult("UNKNOWN", "session_window_unverified")
        current -= timedelta(days=1)
    prior_days = tuple(sorted(day for day in closes if day < signal_day))
    return select_daily_decline_setup(
        observations, spec=spec, symbol=symbol, venue=venue, signal_day=signal_day,
        prior_days=prior_days, cut=cut, basis=basis, benchmark=benchmark, catalyst=catalyst,
        session_evidence_ids=tuple(day.revision_id for day in reversed(witnessed)), session_closed_at=closes,
    )


@dataclass(frozen=True)
class DailyDeclineQuote:
    symbol: str
    venue: str
    observed_at: datetime
    available_at: datetime
    price_won: int
    buy_unit_cost_won: int | None
    session_index: int
    session_end: bool
    tradable: bool | None
    evidence_id: str


@dataclass(frozen=True)
class DailyDeclineProtection:
    symbol: str
    venue: str
    available_at: datetime
    evidence_id: str
    failure_price_won: int
    rebound_price_won: int | None


@dataclass(frozen=True)
class DailyDeclineAction:
    kind: Literal["NONE", "BUY", "SELL", "CANCEL"]
    reason: str
    plan_id: str
    action_id: str = ""
    quantity: int = 0
    price_won: int = 0
    reserved_won: int = 0
    budget_won: int = 0
    stage: int | None = None
    session_index: int = 0
    observed_at: datetime | None = None
    evidence_ids: tuple[str, ...] = ()
    target_action_id: str = ""
    protection: DailyDeclineProtection | None = None


@dataclass(frozen=True)
class DailyDeclineFill:
    fill_id: str
    action_id: str
    quantity: int
    price_won: int
    fee_won: int
    session_index: int


def _remaining_quantity(order: DailyDeclineAction, fills: tuple[DailyDeclineFill, ...]) -> int:
    return order.quantity - sum(fill.quantity for fill in fills if fill.action_id == order.action_id)


@dataclass(frozen=True)
class DailyDeclineState:
    plan_id: str = ""
    owned_quantity: int = 0
    spent_won: int = 0
    first_fill_session: int | None = None
    protection: DailyDeclineProtection | None = None
    exit_reason: str = ""
    pending_action_id: str = ""
    orders: tuple[DailyDeclineAction, ...] = ()
    fills: tuple[DailyDeclineFill, ...] = ()

    @property
    def pending_order(self) -> DailyDeclineAction | None:
        return next((order for order in self.orders if order.action_id == self.pending_action_id), None)

    @property
    def reserved_won(self) -> int:
        order = self.pending_order
        if order is None or order.kind != "BUY":
            return 0
        return _remaining_quantity(order, self.fills) * (order.reserved_won // order.quantity)


def propose_daily_decline_action(
    setup: DailyDeclineSetup, state: DailyDeclineState, quote: DailyDeclineQuote, *,
    cut: datetime, protection: DailyDeclineProtection | None,
    catalyst: DailyDeclineCatalyst | None = None,
) -> DailyDeclineAction:
    """Propose an action from one raw observation. Price movement never creates a fill."""
    if state.plan_id and state.plan_id != setup.plan_id:
        raise ValueError("DD state belongs to a different plan")
    if not _aware(cut):
        raise ValueError("DD cutoff must be timezone-aware")

    def none(reason: str) -> DailyDeclineAction:
        return DailyDeclineAction("NONE", reason, setup.plan_id)

    if ((quote.symbol, quote.venue) != (setup.symbol, setup.venue)
            or not _visible(quote.available_at, quote.evidence_id, cut) or not _aware(quote.observed_at)
            or not setup.decided_at < quote.observed_at <= quote.available_at
            or quote.observed_at.astimezone(KST).date() <= setup.signal_day
            or type(quote.session_index) is not int or quote.session_index < 1
            or type(quote.session_end) is not bool or type(quote.price_won) is not int or quote.price_won <= 0):
        return none("raw_observation_unavailable")
    if state.orders:
        latest_order = state.orders[-1]
        if quote.observed_at <= latest_order.observed_at:
            return none("later_observation_required")
        if quote.session_index < latest_order.session_index:
            return none("session_order_unverified")
    if quote.tradable is not True:
        return none("tradeability_unknown" if quote.tradable is None else "trade_unavailable")

    risk = state.protection or protection
    catalyst_state, catalyst_refs = _catalyst_state(catalyst, cut)

    def action(kind, reason, *, quantity=0, reserve=0, stage=None, target="") -> DailyDeclineAction:
        evidence_ids = (*setup.evidence_ids, risk.evidence_id, quote.evidence_id, *catalyst_refs)
        action_id = _identity("dd-action-v1", (
            setup.plan_id, kind, reason, quantity, reserve, stage, target, evidence_ids,
            quote.price_won, quote.observed_at.astimezone(UTC).isoformat(),
        ))
        return DailyDeclineAction(kind, reason, setup.plan_id, action_id, quantity, quote.price_won,
                                  reserve, setup.spec.budget_won, stage, quote.session_index,
                                  quote.observed_at, evidence_ids, target, risk)

    if (risk is None or (risk.symbol, risk.venue) != (setup.symbol, setup.venue)
            or not _visible(risk.available_at, risk.evidence_id, cut)
            or type(risk.failure_price_won) is not int or risk.failure_price_won <= 0
            or (risk.rebound_price_won is not None and (type(risk.rebound_price_won) is not int
                                                       or risk.rebound_price_won <= risk.failure_price_won))):
        return none("raw_protection_unavailable")
    pending = state.pending_order
    exit_reason = state.exit_reason
    if state.owned_quantity or (pending is not None and pending.kind == "BUY"):
        if not exit_reason and quote.price_won <= risk.failure_price_won:
            exit_reason = "raw_failure_level"
        if not exit_reason and setup.spec.require_intact_catalyst and catalyst_state == "DAMAGED":
            exit_reason = "catalyst_damaged"
        if not exit_reason and risk.rebound_price_won is not None and quote.price_won >= risk.rebound_price_won:
            exit_reason = "raw_rebound_level"
        if state.first_fill_session is not None:
            completed_sessions = quote.session_index - state.first_fill_session + int(quote.session_end)
            if not exit_reason and completed_sessions >= setup.spec.max_hold_sessions:
                exit_reason = "holding_sessions_elapsed"
    if pending is not None:
        if state.orders[-1].target_action_id != pending.action_id:
            if pending.kind == "BUY" and exit_reason:
                return action("CANCEL", exit_reason, target=pending.action_id)
            if pending.kind == "SELL" and _remaining_quantity(pending, state.fills) > state.owned_quantity:
                return action("CANCEL", "late_fill_reduced_sell_quantity", target=pending.action_id)
        return none("order_reconciliation_pending")
    if exit_reason:
        return action("SELL", exit_reason, quantity=state.owned_quantity) if state.owned_quantity else none("plan_ended")
    if setup.spec.require_intact_catalyst and catalyst_state == "DAMAGED":
        return none("catalyst_damaged")

    buys = tuple(order for order in state.orders if order.kind == "BUY")
    stage = len(buys)
    weights = setup.spec.tranche_weights_bps
    if stage >= len(weights):
        return none("tranches_exhausted")
    if quote.price_won <= risk.failure_price_won:
        return none("raw_failure_level")
    if buys:
        anchor = buys[0].price_won
        offset = setup.spec.tranche_drop_units_bps[stage]
        if ((anchor - quote.price_won) * setup.volatility_denominator * BPS
                < anchor * setup.volatility_numerator * offset):
            return none("next_tranche_decline_not_met")
    unit_cost = quote.buy_unit_cost_won
    if type(unit_cost) is not int or unit_cost < quote.price_won:
        return none("buy_cost_unavailable")
    previous_weight = sum(weights[:stage])
    budget = setup.spec.budget_won
    tranche_budget = budget * (previous_weight + weights[stage]) // BPS - budget * previous_weight // BPS
    quantity = min(tranche_budget, budget - state.spent_won - state.reserved_won) // unit_cost
    if quantity <= 0:
        return none("tranche_budget_unavailable")
    return action("BUY", "initial_entry" if not buys else "next_tranche_decline_met",
                  quantity=quantity, reserve=quantity * unit_cost, stage=stage)


def apply_daily_decline_order(state: DailyDeclineState, action: DailyDeclineAction) -> DailyDeclineState:
    """Record an externally accepted proposal. Cancel acceptance leaves the reservation intact."""
    if (action.kind not in {"BUY", "SELL", "CANCEL"} or not action.action_id or not action.plan_id
            or action.observed_at is None or not _aware(action.observed_at)):
        raise ValueError("DD order requires an executable proposal")
    if state.plan_id and state.plan_id != action.plan_id:
        raise ValueError("DD order belongs to a different plan")
    previous = next((order for order in state.orders if order.action_id == action.action_id), None)
    if previous is not None:
        if previous != action:
            raise ValueError("DD action identity changed")
        return state
    if action.kind == "CANCEL":
        if not state.pending_action_id or action.target_action_id != state.pending_action_id:
            raise ValueError("DD cancellation requires its working order")
        return replace(state, exit_reason=state.exit_reason or action.reason, orders=(*state.orders, action))
    if state.pending_action_id:
        raise ValueError("DD order reconciliation is pending")
    if action.kind == "BUY":
        if (state.exit_reason or action.stage != sum(order.kind == "BUY" for order in state.orders)
                or action.protection is None or action.quantity <= 0 or action.price_won <= 0
                or action.reserved_won % action.quantity or action.reserved_won < action.quantity * action.price_won
                or action.reserved_won + state.spent_won > action.budget_won):
            raise ValueError("DD buy exceeds the active tranche or budget")
    elif not 0 < action.quantity <= state.owned_quantity:
        raise ValueError("DD sell exceeds confirmed ownership")
    return replace(state, plan_id=action.plan_id, pending_action_id=action.action_id,
                   protection=state.protection or action.protection,
                   exit_reason=action.reason if action.kind == "SELL" else state.exit_reason,
                   orders=(*state.orders, action))


def apply_daily_decline_fill(state: DailyDeclineState, fill: DailyDeclineFill) -> DailyDeclineState:
    """Deduplicate an external FILL and keep its original order attribution after terminal status."""
    previous = next((row for row in state.fills if row.fill_id == fill.fill_id), None)
    if previous is not None:
        if previous != fill:
            raise ValueError("DD fill identity changed")
        return state
    order = next((row for row in state.orders if row.action_id == fill.action_id), None)
    if (order is None or order.kind not in {"BUY", "SELL"} or not fill.fill_id
            or any(type(value) is not int for value in (fill.quantity, fill.price_won, fill.fee_won, fill.session_index))
            or fill.quantity <= 0 or fill.price_won <= 0 or fill.fee_won < 0
            or fill.session_index < order.session_index):
        raise ValueError("DD fill requires valid facts for an accepted order")
    if fill.quantity > _remaining_quantity(order, state.fills) or (order.kind == "SELL" and fill.quantity > state.owned_quantity):
        raise ValueError("DD fill exceeds its order or confirmed ownership")
    if order.kind == "SELL":
        return replace(state, owned_quantity=state.owned_quantity - fill.quantity, fills=(*state.fills, fill))
    first_session = fill.session_index if state.first_fill_session is None else min(state.first_fill_session, fill.session_index)
    return replace(state, owned_quantity=state.owned_quantity + fill.quantity,
                   spent_won=state.spent_won + fill.quantity * fill.price_won + fill.fee_won,
                   first_fill_session=first_session, fills=(*state.fills, fill))


def finish_daily_decline_order(state: DailyDeclineState, action_id: str) -> DailyDeclineState:
    """Release remaining reservation only after external terminal reconciliation."""
    if action_id == state.pending_action_id and action_id:
        return replace(state, pending_action_id="")
    if any(order.action_id == action_id and order.kind in {"BUY", "SELL"} for order in state.orders):
        return state
    raise ValueError("DD terminal result requires an accepted order")


def _scenario_time(value: object) -> datetime:
    instant = datetime.fromisoformat(str(value))
    if not _aware(instant):
        raise ValueError("DD scenario timestamps must be timezone-aware")
    return instant


def _scenario_reference(reference_type, row, *date_fields):
    if row is None:
        return None
    values = {**row, "available_at": _scenario_time(row["available_at"])}
    for field in date_fields:
        values[field] = date.fromisoformat(row[field])
    return reference_type(**values)


def _scenario_calendar(rows, checkpoint):
    calendar = {}
    for row in rows:
        checkpoint()
        venue, day = row["venue"], date.fromisoformat(row["trading_date"])
        raw_windows = row["sessions"]
        windows = None if raw_windows is None else tuple(SessionWindow(
            venue, day, MarketSession(window["session"]), MarketPhase(window["phase"]),
            _scenario_time(window["starts_at"]), _scenario_time(window["ends_at"]),
            window["schedule_version"], SessionSupport(window["support"]), window.get("reason", ""),
        ) for window in raw_windows)
        evidence = TradingDayEvidence(venue, day, _scenario_time(row["available_at"]),
                                      row["revision_id"], row["evidence_ref"], windows)
        calendar.setdefault(day, []).append(evidence)
    return calendar


def _verified_days(calendar, venue, first, last, cut, checkpoint):
    days = []
    while first <= last:
        checkpoint()
        evidence = trading_day_as_of(first, venue=venue, as_of=cut, evidence=calendar.get(first, ()))
        if evidence is None or evidence.sessions is None:
            return days, False
        days.append(evidence)
        first += timedelta(days=1)
    return days, True


def _scenario_action(action):
    return {"kind": action.kind, "reason": action.reason, "action_id": action.action_id,
            "quantity": action.quantity, "price_won": action.price_won, "reserved_won": action.reserved_won,
            "budget_won": action.budget_won, "stage": action.stage, "session_index": action.session_index,
            "evidence_ids": list(action.evidence_ids), "target_action_id": action.target_action_id}


def replay_daily_decline(scenario: Mapping[str, object], policy: Mapping[str, object], *,
                         checkpoint=lambda: None, setup_result=None, include_state=False) -> dict:
    """Run the DD policy against explicitly recorded synthetic orders and FILL receipts."""
    checkpoint()
    if scenario.get("version") != "dd-synthetic-evidence/v1" or scenario.get("venue") != "KRX":
        raise ValueError("unsupported DD synthetic evidence contract")
    try:
        spec = DailyDeclineSpec(**{
            **policy, "tranche_weights_bps": tuple(policy["tranche_weights_bps"]),
            "tranche_drop_units_bps": tuple(policy["tranche_drop_units_bps"]),
        })
        symbol, venue = scenario["symbol"], scenario["venue"]
        signal_day, cut = date.fromisoformat(scenario["signal_day"]), _scenario_time(scenario["cut"])
        prior_days = tuple(date.fromisoformat(value) for value in scenario["prior_days"])
        calendar = _scenario_calendar(scenario["calendar"], checkpoint)
        basis = _scenario_reference(DailyDeclineBasis, scenario.get("basis"), "first_day", "last_day")
        benchmark = _scenario_reference(DailyDeclineBenchmark, scenario.get("benchmark"), "trading_date")
        catalyst = _scenario_reference(DailyDeclineCatalyst, scenario.get("catalyst"))
        protection = _scenario_reference(DailyDeclineProtection, scenario.get("protection"))
        events = scenario["events"]
        daily_revisions = scenario["daily_revisions"]
    except (KeyError, TypeError) as error:
        raise ValueError("invalid DD synthetic evidence inputs") from error

    setup_days, complete = _verified_days(calendar, venue, prior_days[0] if prior_days else signal_day,
                                          signal_day, cut, checkpoint)
    trading_days = [day for day in setup_days if day.sessions]
    if setup_result is not None:
        setup_days = [day for revisions in calendar.values() for day in revisions
                      if setup_result.setup is not None and day.revision_id in setup_result.setup.evidence_ids]
    elif (not complete or tuple(day.trading_date for day in trading_days) != (*prior_days, signal_day)
            or cut < trading_days[-1].sessions[-1].ends_at):
        setup_result = DailyDeclineSetupResult("UNKNOWN", "session_window_unverified")
    else:
        def observations():
            for row in daily_revisions:
                checkpoint()
                yield row

        setup_result = select_daily_decline_setup(
            observations(), spec=spec, symbol=symbol, venue=venue, signal_day=signal_day,
            prior_days=prior_days, cut=cut, basis=basis, benchmark=benchmark, catalyst=catalyst,
            session_evidence_ids=tuple(day.revision_id for day in setup_days),
            session_closed_at={day.trading_date: day.sessions[-1].ends_at for day in trading_days},
        )
    setup = setup_result.setup
    setup_document = {"status": setup_result.status, "reason": setup_result.reason}
    if setup is not None:
        setup_document.update(plan_id=setup.plan_id, drawdown_bps=setup.drawdown_bps,
                              relative_decline_bps=setup.relative_decline_bps, volatility_bps=setup.volatility_bps,
                              catalyst_state=setup.catalyst_state, evidence_ids=list(setup.evidence_ids),
                              calendar_revision_ids=[day.revision_id for day in setup_days])

    state = DailyDeclineState()
    proposals, accepted, decisions = {}, {}, []
    last_receipt = cut
    for row in events:
        checkpoint()
        try:
            kind, available = row["kind"], _scenario_time(row["available_at"])
            if available < last_receipt:
                raise ValueError("DD receipts must be recorded in available order after the setup cut")
            last_receipt = available
            event = {"kind": kind, "available_at": available.isoformat()}
            if kind == "QUOTE":
                observed = _scenario_time(row["observed_at"])
                quote_ref = row["evidence_id"]
                if not quote_ref or quote_ref in proposals or observed > available:
                    raise ValueError("DD quote needs unique evidence and valid occurrence time")
                quote_day = observed.astimezone(KST).date()
                days, known = _verified_days(calendar, venue, signal_day + timedelta(days=1),
                                             quote_day, available, checkpoint)
                sessions = [day for day in days if day.sessions]
                day = sessions[-1] if known and sessions and sessions[-1].trading_date == quote_day else None
                windows = tuple(window for window in day.sessions if window.permits("continuous_trade")) if day else ()
                window = next((window for window in windows if window.starts_at <= observed < window.ends_at), None)
                tradable = row["tradable"] if window is not None else None
                quote = DailyDeclineQuote(symbol, venue, observed, available, row["price_won"],
                                         row["buy_unit_cost_won"], len(sessions) if day else 0,
                                         row["session_end"] and window is not None and window == windows[-1],
                                         tradable, quote_ref)
                action = (propose_daily_decline_action(setup, state, quote, cut=available, protection=protection,
                          catalyst=_scenario_reference(DailyDeclineCatalyst, row.get("catalyst")))
                          if setup is not None else DailyDeclineAction("NONE", "setup_not_qualified", ""))
                proposals[quote_ref] = action
                event.update(quote_ref=quote_ref, observed_at=observed.isoformat(), raw_price_won=quote.price_won,
                             tradable=tradable, action=_scenario_action(action),
                             calendar_revision_ids=[day.revision_id for day in days])
            elif kind == "ACCEPT":
                order_ref, quote_ref = row["order_ref"], row["quote_ref"]
                action = proposals[quote_ref]
                if (not order_ref or accepted.get(order_ref, action) != action
                        or any(ref != order_ref and prior.action_id == action.action_id
                               for ref, prior in accepted.items())):
                    raise ValueError("DD order reference conflicts with its original proposal")
                state = apply_daily_decline_order(state, action)
                accepted[order_ref] = action
                event.update(order_ref=order_ref, action_id=action.action_id, quote_ref=quote_ref)
            elif kind == "FILL":
                order_ref = row["order_ref"]
                order = accepted[order_ref]
                sessions, _ = _verified_days(calendar, venue, signal_day + timedelta(days=1),
                                             available.astimezone(KST).date(), available, checkpoint)
                elapsed = sum(bool(day.sessions and day.sessions[0].starts_at <= available) for day in sessions)
                if type(row["session_index"]) is not int or not 1 <= row["session_index"] <= elapsed:
                    raise ValueError("DD fill requires an observed trading session")
                fill = DailyDeclineFill(row["fill_id"], order.action_id, row["quantity"],
                                        row["price_won"], row["fee_won"], row["session_index"])
                state = apply_daily_decline_fill(state, fill)
                event.update(order_ref=order_ref, fill_id=fill.fill_id, quantity=fill.quantity,
                             price_won=fill.price_won, fee_won=fill.fee_won, session_index=fill.session_index)
            elif kind == "TERMINAL":
                order_ref = row["order_ref"]
                state = finish_daily_decline_order(state, accepted[order_ref].action_id)
                event["order_ref"] = order_ref
            else:
                raise ValueError("unsupported DD receipt kind")
        except (KeyError, TypeError) as error:
            raise ValueError("DD receipt requires its recorded evidence and accepted order reference") from error
        event.update(owned_quantity=state.owned_quantity, spent_won=state.spent_won, reserved_won=state.reserved_won)
        for field in ('provenance', 'cost_provenance'):
            if field in row:
                if not isinstance(row[field], Mapping):
                    raise ValueError('DD receipt provenance must be an object')
                event[field] = dict(row[field])
        decisions.append(event)
    checkpoint()

    sales = {order.action_id for order in state.orders if order.kind == "SELL"}
    sell_proceeds = sum(fill.quantity * fill.price_won - fill.fee_won for fill in state.fills if fill.action_id in sales)
    closed = not state.owned_quantity and not state.pending_action_id and bool(state.fills)
    summary = {
        "owned_quantity": state.owned_quantity, "spent_won": state.spent_won, "reserved_won": state.reserved_won,
        "budget_exceeded": state.spent_won + state.reserved_won > spec.budget_won,
        "pending_action_id": state.pending_action_id, "exit_reason": state.exit_reason,
        "fill_count": len(state.fills), "sell_proceeds_won": sell_proceeds,
        "realized_pnl_won": sell_proceeds - state.spent_won if closed else None,
    }
    return {"version": "dd-research-result/v1", "setup": setup_document, "events": decisions, "summary": summary,
            **({'state': json.loads(json.dumps(asdict(state), default=lambda value: value.isoformat()))}
               if include_state else {})}


def replay_frozen_daily_decline(observations, descriptor, policy, setup_result, *, checkpoint=lambda: None):
    """Join admitted frozen daily setup with explicit research execution receipts."""
    execution = descriptor['execution_evidence']
    if (not isinstance(execution, Mapping) or execution.get('version') != 'dd-execution-evidence/v1'
            or set(execution) - {'version', 'provenance', 'protection', 'protection_provenance', 'events'}
            or not isinstance(execution.get('events'), (list, tuple))):
        raise ValueError('invalid DD execution evidence')
    provenance = execution.get('provenance')
    if (not isinstance(provenance, Mapping)
            or provenance.get('source_kind') not in {'synthetic', 'historical_reconstruction', 'historical_replay', 'hypothesis'}
            or any(not isinstance(provenance.get(key), str) or not provenance[key].strip()
                   for key in ('source_ref', 'source_version'))):
        raise ValueError('DD execution evidence requires its declared research source and version')
    scenario = {key: descriptor[key] for key in ('symbol', 'venue', 'signal_day', 'cut', 'basis', 'benchmark')}
    scenario.update(version='dd-synthetic-evidence/v1', daily_revisions=observations, prior_days=[],
        calendar=descriptor['strategy_references']['calendar'], events=execution['events'],
        protection=execution.get('protection'), catalyst=descriptor.get('catalyst'))
    result = replay_daily_decline(scenario, policy, checkpoint=checkpoint, setup_result=setup_result, include_state=True)
    missing = []
    def has_source(value):
        return (isinstance(value, Mapping) and all(isinstance(value.get(key), str) and value[key].strip()
                for key in ('source_ref', 'source_version')))
    if not has_source(execution.get('protection_provenance')):
        missing.append('raw_protection_source')
    if execution.get('protection') is None:
        missing.append('raw_price_protection')
    for row in execution['events']:
        if row.get('kind') == 'QUOTE' and not has_source(row.get('provenance')):
            missing.append('raw_quote_source:' + row['evidence_id'])
        if row.get('kind') == 'FILL' and not has_source(row.get('cost_provenance')):
            missing.append('fill_cost_source:' + row['fill_id'])
    return {**result, 'source_kind': 'frozen_research_evidence',
        'execution_evidence': dict(execution),
        'input_assessment': {'scope': 'declared_frozen_research', 'missing_inputs': missing,
                             'native_confirmation': False}, 'order_authorized': False,
        'evidence_provenance': {'execution': dict(provenance), 'raw_protection': execution.get('protection'),
            'raw_protection_source': execution.get('protection_provenance'),
            'fill_costs': {row['fill_id']: dict(row.get('cost_provenance', {}))
                          for row in execution['events'] if row.get('kind') == 'FILL'}}}
