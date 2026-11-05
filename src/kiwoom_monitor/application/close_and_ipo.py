"""Explicit research policies for KRX close and IPO plans."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any

from .market_session_schedule import (
    KST,
    KRX_REGULAR_RESEARCH_PROFILE,
    MarketPhase,
    MarketSession,
    SessionSupport,
    SessionWindow,
    TradingDayEvidence,
    calendar_research_bar_window,
    next_trading_day,
    trading_day_as_of,
)
from .research_replay import KrxMinuteBarFrame
from kiwoom_monitor.domain.strategy_contract import MINUTE_SHADOW_VERSION
from kiwoom_monitor.domain.market_data_contract import (
    ListingEvidence,
    ListingEvidenceStatus,
    ListingPhase,
    ListingType,
    select_listing_evidence,
)


@dataclass(frozen=True)
class ClosePolicy:
    quantity: int
    entry_window_minutes: int
    lookback_bars: int
    minimum_close_return_bps: int
    adverse_gap_bps: int
    protection_loss_bps: int
    exit_minutes_after_open: int
    ipo_reduction_quantity: int | None
    listing_phases: tuple[ListingPhase, ...]

    def __post_init__(self) -> None:
        _positive(self.quantity, self.entry_window_minutes, self.lookback_bars, self.adverse_gap_bps,
                  self.protection_loss_bps, self.exit_minutes_after_open)
        _nonnegative(self.minimum_close_return_bps)
        if self.adverse_gap_bps >= 10_000 or self.protection_loss_bps >= 10_000:
            raise ValueError("loss thresholds must be below 100 percent")
        phases = _listing_phases(self.listing_phases)
        if self.ipo_reduction_quantity is not None:
            _positive(self.ipo_reduction_quantity)
            if not phases or self.ipo_reduction_quantity >= self.quantity:
                raise ValueError("IPO close requires listing phases and a strictly partial tranche")
        elif phases:
            raise ValueError("general close does not require a listing phase")
        object.__setattr__(self, "listing_phases", phases)

    @property
    def variant(self) -> str:
        return "CLOSE" if self.ipo_reduction_quantity is None else "IPO_CLOSE"

    @classmethod
    def from_document(cls, document: Mapping) -> ClosePolicy:
        return cls(**{**document, "listing_phases": tuple(ListingPhase(value) for value in document["listing_phases"])})

    def to_dict(self) -> dict:
        return {**asdict(self), "listing_phases": [value.value for value in self.listing_phases]}


@dataclass(frozen=True)
class IpoBreakoutPolicy:
    quantity: int
    lookback_bars: int
    breakout_buffer_bps: int
    protection_loss_bps: int
    exit_minutes_before_close: int
    listing_phases: tuple[ListingPhase, ...]

    def __post_init__(self) -> None:
        _positive(self.quantity, self.lookback_bars, self.protection_loss_bps, self.exit_minutes_before_close)
        _nonnegative(self.breakout_buffer_bps)
        if self.protection_loss_bps >= 10_000:
            raise ValueError("loss threshold must be below 100 percent")
        phases = _listing_phases(self.listing_phases)
        if not phases:
            raise ValueError("IPO breakout requires listing phases")
        object.__setattr__(self, "listing_phases", phases)

    @property
    def variant(self) -> str:
        return "IPO_BREAKOUT"


@dataclass(frozen=True)
class CloseIpoSetup:
    policy: ClosePolicy | IpoBreakoutPolicy
    symbol: str
    entry_date: date
    entry_deadline: datetime
    entry_calendar_ref: str
    entry_bar_refs: tuple[str, ...]
    listing_phase: ListingPhase | None
    listing_refs: tuple[str, ...]
    breakout_level: int | None
    close_auction: SessionWindow | None
    next_date: date | None
    next_open_at: datetime | None
    next_calendar_ref: str | None
    next_session: SessionWindow | None = None

    @classmethod
    def from_document(cls, document: Mapping) -> CloseIpoSetup:
        values = dict(document)
        policy = values["policy"]
        values["policy"] = (IpoBreakoutPolicy(**{
            **policy, "listing_phases": tuple(ListingPhase(value) for value in policy["listing_phases"]),
        }) if "breakout_buffer_bps" in policy else ClosePolicy.from_document(policy))
        values["entry_date"] = date.fromisoformat(values["entry_date"])
        values["entry_deadline"] = datetime.fromisoformat(values["entry_deadline"])
        values["entry_bar_refs"] = tuple(values["entry_bar_refs"])
        values["listing_refs"] = tuple(values["listing_refs"])
        values["listing_phase"] = ListingPhase(values["listing_phase"]) if values["listing_phase"] is not None else None
        for key in ("close_auction", "next_session"):
            window = values.get(key)
            if window is not None:
                values[key] = SessionWindow(**{
                    **window, "trading_date": date.fromisoformat(window["trading_date"]),
                    "session": MarketSession(window["session"]), "phase": MarketPhase(window["phase"]),
                    "support": SessionSupport(window["support"]),
                    "starts_at": datetime.fromisoformat(window["starts_at"]), "ends_at": datetime.fromisoformat(window["ends_at"]),
                })
        values["next_date"] = date.fromisoformat(values["next_date"]) if values["next_date"] is not None else None
        values["next_open_at"] = datetime.fromisoformat(values["next_open_at"]) if values["next_open_at"] is not None else None
        return cls(**values)


def matches_close_shadow(parameters: Mapping[str, Any], event: Mapping[str, Any]) -> bool:
    """Compare the frozen rule, while each stored Shadow setup keeps its own calendar."""
    setup = event.get("close_ipo_entry_setup")
    if (event.get("strategy_id") != "close_betting" or event.get("strategy_version") != "v1"
            or event.get("strategy_variant") != "CLOSE" or event.get("shadow_scope") != MINUTE_SHADOW_VERSION
            or event.get("transition") != "flat_to_candidate"
            or event.get("decision_reasons") != ["preclose_return_setup"]
            or not str(event.get("run_id", "")).startswith("section12-minute-shadow:")
            or any(not isinstance(event.get(key), str) or not event[key]
                   for key in ("event_id", "snapshot_id", "decision_id"))
            or not isinstance(setup, Mapping) or setup.get("policy") != parameters["policy"]
            or setup.get("symbol") != event.get("symbol") or type(event.get("quantity")) is not int
            or event["quantity"] != parameters["policy"]["quantity"]):
        return False
    refs, bars = event.get("input_revision_refs"), setup.get("entry_bar_refs")
    auction = setup.get("close_auction")
    if (not isinstance(refs, list) or not isinstance(bars, list) or not bars
            or bars[-1] != event.get("reference_revision_id")
            or any(not isinstance(ref, str) or not ref or ref not in refs
                   for ref in (*bars, setup.get("entry_calendar_ref"), setup.get("next_calendar_ref")))
            or not isinstance(auction, Mapping) or auction.get("venue") != "KRX"
            or auction.get("session") != "KRX_CLOSING_AUCTION" or auction.get("phase") != "AUCTION_ORDER_ENTRY"
            or auction.get("support") != "SUPPORTED" or auction.get("trading_date") != setup.get("entry_date")):
        return False
    try:
        available, cutoff, expiry, deadline, start, end, next_open = (
            datetime.fromisoformat(value) for value in (
                event["available_at"], event["input_cutoff"], event["expires_at"], setup["entry_deadline"],
                auction["starts_at"], auction["ends_at"], setup["next_open_at"],
            )
        )
    except (KeyError, TypeError, ValueError):
        return False
    return (all(value.utcoffset() is not None for value in (available, cutoff, expiry, deadline, start, end, next_open))
            and available == cutoff and available < expiry <= deadline <= start < end < next_open
            and available.astimezone(KST).date().isoformat() == setup.get("entry_date")
            and next_open.astimezone(KST).date().isoformat() == setup.get("next_date")
            and setup["entry_date"] < setup["next_date"])


@dataclass(frozen=True)
class CloseIpoPosition:
    setup: CloseIpoSetup
    quantity: int
    entry_price: int
    reduced_quantity: int

    def __post_init__(self) -> None:
        _positive(self.quantity, self.entry_price)
        _nonnegative(self.reduced_quantity)
        if self.quantity + self.reduced_quantity > self.setup.policy.quantity:
            raise ValueError("position quantities must come from the entered plan")


@dataclass(frozen=True)
class CloseIpoDecision:
    action: str
    quantity: int
    reason: str
    setup: CloseIpoSetup | None = None
    evidence_refs: tuple[str, ...] = ()


def evaluate_close_and_ipo(
    policy: ClosePolicy | IpoBreakoutPolicy,
    *,
    bar: KrxMinuteBarFrame,
    history: Iterable[KrxMinuteBarFrame],
    calendar: Iterable[TradingDayEvidence],
    listings: Iterable[ListingEvidence],
    position: CloseIpoPosition | None,
) -> CloseIpoDecision:
    """Use completed minute evidence and supplied FILL facts without simulating fills."""
    start, end, cut = _times(bar)
    setup = position.setup if position is not None else None
    if position is not None and bar.code != setup.symbol:
        return CloseIpoDecision("HOLD", 0, "position_symbol_not_observed", setup)
    if (end - start != timedelta(minutes=1) or not _complete(bar, start, end, cut)
            or cut.date() != start.date()):
        return CloseIpoDecision("WAIT", 0, "completed_current_bar_unavailable", setup)
    calendar = tuple(calendar)
    day = trading_day_as_of(start.date(), venue="KRX", as_of=cut, evidence=calendar)
    session = calendar_research_bar_window(
        start, end, trading_day=day, decision_at=cut, session_profile=KRX_REGULAR_RESEARCH_PROFILE,
        declared_session=bar.market_session, declared_phase=bar.market_phase,
        declared_schedule_version=bar.schedule_version,
    )
    if (session is None or session.session is not MarketSession.KRX_REGULAR
            or session.phase is not MarketPhase.CONTINUOUS or cut >= session.ends_at):
        return CloseIpoDecision("WAIT", 0, "continuous_session_unavailable", setup)
    if position is not None:
        return _manage_position(position, bar, history, day, session, cut)
    return _entry(policy, bar, history, calendar, listings, day, session, start, cut)


def _entry(policy, bar, history, calendar, listings, day, session, start, cut) -> CloseIpoDecision:
    if isinstance(policy, ClosePolicy):
        if cut < session.ends_at - timedelta(minutes=policy.entry_window_minutes):
            return CloseIpoDecision("WAIT", 0, "outside_declared_close_entry_window")
        deadline = session.ends_at
    else:
        deadline = session.ends_at - timedelta(minutes=policy.exit_minutes_before_close)
        if cut >= deadline:
            return CloseIpoDecision("WAIT", 0, "ipo_breakout_entry_window_ended")

    listing_phase = None
    listing_refs = ()
    if policy.listing_phases:
        selected = select_listing_evidence(listings, symbol=bar.code, trading_date=start.date(), as_of=cut)
        listing_refs = tuple(row.evidence_id for row in selected.evidence)
        if selected.status is not ListingEvidenceStatus.AVAILABLE:
            return CloseIpoDecision("WAIT", 0, f"listing_{selected.status.value}", evidence_refs=listing_refs)
        if selected.evidence[0].listing_type is not ListingType.IPO or selected.phase not in policy.listing_phases:
            return CloseIpoDecision("WAIT", 0, "listing_type_or_phase_unsupported", evidence_refs=listing_refs)
        listing_phase = selected.phase

    prior = _prior_minutes(history, bar.code, start, cut, session, policy.lookback_bars)
    if not prior:
        return CloseIpoDecision("WAIT", 0, "prior_complete_minutes_unavailable", evidence_refs=listing_refs)
    bar_refs = tuple(row.revision_id for row in prior) + (bar.revision_id,)
    setup = CloseIpoSetup(
        policy=policy, symbol=bar.code, entry_date=start.date(), entry_deadline=deadline,
        entry_calendar_ref=day.revision_id, entry_bar_refs=bar_refs,
        listing_phase=listing_phase, listing_refs=listing_refs, breakout_level=None,
        close_auction=None, next_date=None, next_open_at=None, next_calendar_ref=None,
    )
    if isinstance(policy, IpoBreakoutPolicy):
        level = max(row.high for row in prior)
        if bar.close * 10_000 <= level * (10_000 + policy.breakout_buffer_bps):
            return CloseIpoDecision("WAIT", 0, "prior_high_not_broken", evidence_refs=bar_refs)
        return CloseIpoDecision("ENTER", policy.quantity, "ipo_prior_high_breakout",
                                replace(setup, breakout_level=level), (*bar_refs, *listing_refs))

    if bar.close * 10_000 < prior[0].close * (10_000 + policy.minimum_close_return_bps):
        return CloseIpoDecision("WAIT", 0, "close_setup_return_below_threshold", evidence_refs=bar_refs)
    auction = _supported_session(day, MarketSession.KRX_CLOSING_AUCTION, MarketPhase.AUCTION_ORDER_ENTRY)
    following = next_trading_day(start.date(), venue="KRX", as_of=cut, evidence=calendar)
    next_open = _supported_session(following, MarketSession.KRX_REGULAR, MarketPhase.CONTINUOUS)
    if auction is None or next_open is None:
        return CloseIpoDecision("WAIT", 0, "close_or_next_open_evidence_unavailable", evidence_refs=bar_refs)
    setup = replace(setup, close_auction=auction, next_date=following.trading_date,
                    next_open_at=next_open.starts_at, next_calendar_ref=following.revision_id, next_session=next_open)
    return CloseIpoDecision("ENTER", policy.quantity, "preclose_return_setup", setup,
                            (*bar_refs, day.revision_id, following.revision_id, *listing_refs))


def _manage_position(position, bar, history, day, session, cut) -> CloseIpoDecision:
    setup = position.setup
    policy = setup.policy
    refs = (bar.revision_id,)
    if bar.close * 10_000 <= position.entry_price * (10_000 - policy.protection_loss_bps):
        return CloseIpoDecision("EXIT", position.quantity, "filled_position_protection", setup, refs)
    if isinstance(policy, IpoBreakoutPolicy):
        if cut >= setup.entry_deadline:
            return CloseIpoDecision("EXIT", position.quantity, "ipo_breakout_deadline", setup, refs)
        if bar.close < setup.breakout_level:
            return CloseIpoDecision("EXIT", position.quantity, "pinned_breakout_level_failed", setup, refs)
        return CloseIpoDecision("HOLD", 0, "ipo_breakout_structure_held", setup, refs)

    if cut.date() <= setup.entry_date:
        return CloseIpoDecision("HOLD", 0, "planned_overnight_hold", setup, refs)
    if cut.date() > setup.next_date:
        return CloseIpoDecision("EXIT", position.quantity, "next_session_management_overdue", setup, refs)
    if day.revision_id != setup.next_calendar_ref or session.starts_at != setup.next_open_at:
        return CloseIpoDecision("HOLD", 0, "pinned_next_session_changed", setup, refs)
    if cut >= setup.next_open_at + timedelta(minutes=policy.exit_minutes_after_open):
        return CloseIpoDecision("EXIT", position.quantity, "next_session_exit_deadline", setup, refs)

    return evaluate_close_gap(position, (*history, bar), session=session, cut=cut, refs=refs)


def evaluate_close_gap(position, frames, *, session, cut, refs=()):
    """Share the completed-bar gap rule with native management, without inferring intrabar paths."""
    setup, policy = position.setup, position.setup.policy
    frames = tuple(frames)
    # Only the actual first completed continuous minute and exact prior closing
    # auction are supported gap proxies. A later first observation is not an open.
    auction = setup.close_auction
    closing = _window_bar(frames, setup.symbol, auction, auction.starts_at, auction.ends_at, setup.next_open_at)
    opening = _window_bar(frames, setup.symbol, session, setup.next_open_at,
                          setup.next_open_at + timedelta(minutes=1), cut)
    if closing is None or opening is None:
        return CloseIpoDecision("HOLD", 0, "completed_close_or_open_proxy_unavailable", setup, refs)
    refs = tuple(dict.fromkeys((closing.revision_id, opening.revision_id, *refs)))
    return _close_gap_prices(position, closing.close, opening.open, refs)


def evaluate_close_gap_at_price(position, closing_price, frames, *, session, cut, refs=()):
    """Use a separately verified actual closing price and the first completed opening minute."""
    setup = position.setup
    opening = _window_bar(frames, setup.symbol, session, setup.next_open_at,
                          setup.next_open_at + timedelta(minutes=1), cut)
    if opening is None:
        return CloseIpoDecision('HOLD', 0, 'completed_close_or_open_proxy_unavailable', setup, refs)
    return _close_gap_prices(position, closing_price, opening.open,
                            tuple(dict.fromkeys((*refs, opening.revision_id))))


def _close_gap_prices(position, closing_price, opening_price, refs):
    setup, policy = position.setup, position.setup.policy
    _positive(closing_price, opening_price)
    if opening_price * 10_000 > closing_price * (10_000 - policy.adverse_gap_bps):
        return CloseIpoDecision("HOLD", 0, "no_adverse_opening_gap", setup, refs)
    if policy.ipo_reduction_quantity is None:
        return CloseIpoDecision("EXIT", position.quantity, "adverse_gap_full_exit", setup, refs)
    remaining_tranche = policy.ipo_reduction_quantity - position.reduced_quantity
    if remaining_tranche <= 0:
        return CloseIpoDecision("HOLD", 0, "ipo_gap_tranche_filled_manage_remaining", setup, refs)
    if remaining_tranche >= position.quantity:
        return CloseIpoDecision("HOLD", 0, "ipo_gap_tranche_cannot_preserve_residual", setup, refs)
    return CloseIpoDecision("REDUCE", remaining_tranche, "ipo_adverse_gap_partial_reduction", setup, refs)


def _supported_session(day, session, phase):
    if day is None:
        return None
    return next((window for window in day.sessions or ()
                 if window.session is session and window.phase is phase
                 and window.support is SessionSupport.SUPPORTED), None)


def _prior_minutes(history, symbol, start, cutoff, session, count):
    beginning = start - timedelta(minutes=count)
    eligible = {}
    for row in history:
        if row.code == symbol:
            row_start, _, available = _times(row)
            if beginning <= row_start < start and available <= cutoff:
                eligible.setdefault(row_start, []).append(row)
    previous = []
    for offset in range(count):
        expected = beginning + timedelta(minutes=offset)
        row = _window_bar(eligible.get(expected, ()), symbol, session, expected,
                          expected + timedelta(minutes=1), cutoff)
        if row is None:
            return ()
        previous.append(row)
    return tuple(previous)


def _window_bar(rows, symbol, window, start, end, cutoff):
    if not window.starts_at <= start < end <= window.ends_at:
        return None
    latest = None
    latest_at = None
    conflicting = False
    for row in rows:
        if row.code != symbol:
            continue
        row_start, row_end, available = _times(row)
        if row_start != start or row_end != end or available > cutoff:
            continue
        if latest_at is None or available > latest_at:
            latest, latest_at, conflicting = row, available, False
        elif available == latest_at and row != latest:
            conflicting = True
    if (latest is None or conflicting or latest.market_session != window.session.value
            or latest.market_phase != window.phase.value or latest.schedule_version != window.schedule_version
            or not _complete(latest, start, end, latest_at)):
        return None
    return latest


def _times(row):
    result = tuple(datetime.fromisoformat(value) for value in (row.bar_start, row.bar_end, row.available_at))
    if any(value.utcoffset() is None for value in result):
        raise ValueError("close and IPO observation times must include their timezone")
    return tuple(value.astimezone(KST) for value in result)


def _complete(row, start, end, available):
    return (row.capture_quality == "complete" and bool(row.revision_id)
            and start < end <= available and 0 < row.low <= min(row.open, row.close)
            and max(row.open, row.close) <= row.high)


def _listing_phases(values):
    phases = tuple(ListingPhase(value) for value in values)
    if not set(phases) <= {ListingPhase.LISTING_DAY, ListingPhase.AFTER_LISTING_DAY}:
        raise ValueError("unsupported listing phase")
    return phases


def _positive(*values):
    if any(type(value) is not int or value <= 0 for value in values):
        raise ValueError("policy quantities, windows and loss thresholds must be positive integers")


def _nonnegative(value):
    if type(value) is not int or value < 0:
        raise ValueError("expected a non-negative integer")
