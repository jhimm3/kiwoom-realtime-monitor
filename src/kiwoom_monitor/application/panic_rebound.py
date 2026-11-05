"""EX-PANIC completed-minute research decisions on an observed KRX prefix."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import chain
from typing import Literal

from kiwoom_monitor.application.market_session_schedule import MarketPhase, MarketSession
from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
from kiwoom_monitor.domain.time_basis import KST, aware_iso_kst


_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class PanicReboundRules:
    variant: Literal["P0", "P1"]
    lookback_minutes: int
    decline_bps: int
    minimum_turnover_won: int
    max_input_age_seconds: int
    rebound_target_bps: int
    max_hold_minutes: int

    def __post_init__(self) -> None:
        if self.variant not in {"P0", "P1"} or self.lookback_minutes not in {3, 5, 10}:
            raise ValueError("EX-PANIC requires P0 or P1 and a 3, 5 or 10 minute decline window")
        settings = (
            self.lookback_minutes, self.decline_bps, self.minimum_turnover_won,
            self.max_input_age_seconds, self.rebound_target_bps, self.max_hold_minutes,
        )
        if (any(type(value) is not int for value in settings)
                or not 0 < self.decline_bps < 10_000
                or min(self.minimum_turnover_won, self.max_input_age_seconds) < 0
                or min(self.rebound_target_bps, self.max_hold_minutes) <= 0):
            raise ValueError("EX-PANIC numeric settings must be explicit integer research parameters")


@dataclass(frozen=True)
class PanicNewsProof:
    """A symbol-specific assessment backed by frozen source/processing references."""

    symbol: str
    source: str
    assessment_id: str
    revision: str
    effective_at: datetime | None
    available_at: datetime | None
    disposition: Literal["supported_nonfundamental", "fundamental_adverse", "unresolved"]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.symbol, self.source, self.assessment_id, self.revision)):
            raise ValueError("PANIC news proof requires symbol and assessment provenance")
        if self.disposition not in {"supported_nonfundamental", "fundamental_adverse", "unresolved"}:
            raise ValueError("unknown PANIC news disposition")
        for value in (self.effective_at, self.available_at):
            if value is not None and value.utcoffset() is None:
                raise ValueError("PANIC news timestamps must include their timezone")
        if any(not ref.strip() for ref in self.evidence_refs):
            raise ValueError("PANIC news evidence references must be nonempty")


@dataclass(frozen=True)
class PanicSetup:
    symbol: str
    research_session: str
    trigger_bar_start: datetime
    trigger_available_at: datetime
    trigger_close: int
    failure_price: int
    target_price: int
    trigger_revision_id: str
    input_refs: tuple[str, ...]


@dataclass(frozen=True)
class PanicReboundDecision:
    action: Literal["NO_TRADE", "ARM", "ENTER", "HOLD", "EXIT"]
    reason: str
    setup: PanicSetup | None = None
    input_refs: tuple[str, ...] = ()


def evaluate_panic_rebound(
    *, evaluation_bar: KrxMinuteBarFrame, bar_history: Iterable[KrxMinuteBarFrame],
    rules: PanicReboundRules, news_proofs: Iterable[PanicNewsProof] = (),
    setup: PanicSetup | None = None, position_opened_at: datetime | None = None,
) -> PanicReboundDecision:
    """Decide on completed closes. The caller owns orders, fills and session closure."""
    bar = evaluation_bar
    start, end, cutoff = _bar_times(bar)
    if position_opened_at is not None:
        return _manage_position(bar, start, end, cutoff, rules, setup, position_opened_at)
    if not _complete_regular_bar(bar, start, end, cutoff):
        return PanicReboundDecision("NO_TRADE", "panic_complete_krx_bar_required")
    if cutoff - end > timedelta(seconds=rules.max_input_age_seconds):
        return PanicReboundDecision("NO_TRADE", "panic_decision_bar_stale")

    history = _known_bars(bar, start, cutoff, bar_history, rules.lookback_minutes)
    if history.get(start) != bar:
        return PanicReboundDecision("NO_TRADE", "panic_decision_bar_revision_unproven")
    if rules.variant == "P1" and setup is not None and _same_scope(setup, bar):
        result = _confirm_setup(bar, start, cutoff, rules, setup, history)
        if result.action != "ENTER":
            return result
        return _with_news(result, bar, cutoff, news_proofs)

    window = _complete_window(history, start, rules.lookback_minutes)
    if not window:
        return PanicReboundDecision("NO_TRADE", "panic_decline_window_unproven")
    previous = _complete_window(history, start - _MINUTE, rules.lookback_minutes)
    refs = _window_refs(window, previous)
    if not _decline_reached(window, rules.decline_bps):
        return PanicReboundDecision("NO_TRADE", "panic_decline_not_reached", input_refs=refs)
    if previous and _decline_reached(previous, rules.decline_bps):
        return PanicReboundDecision("NO_TRADE", "panic_decline_already_observed", input_refs=refs)
    if sum(row.trade_value_million_won for row in window[1:]) * 1_000_000 < rules.minimum_turnover_won:
        return PanicReboundDecision("NO_TRADE", "panic_turnover_below_minimum", input_refs=refs)
    target = (bar.close * (10_000 + rules.rebound_target_bps) + 9_999) // 10_000
    setup = PanicSetup(
        bar.code, bar.research_session, start, cutoff, bar.close, min(row.low for row in window[1:]),
        target, bar.revision_id, refs,
    )
    if rules.variant == "P1":
        return PanicReboundDecision("ARM", "panic_waiting_for_next_completed_close", setup, refs)
    return _with_news(PanicReboundDecision("ENTER", "panic_decline_reached", setup, refs), bar, cutoff, news_proofs)


def _bar_times(bar: KrxMinuteBarFrame) -> tuple[datetime, datetime, datetime]:
    return aware_iso_kst(bar.bar_start), aware_iso_kst(bar.bar_end), aware_iso_kst(bar.available_at)


def _complete_regular_bar(bar: KrxMinuteBarFrame, start: datetime, end: datetime, available: datetime) -> bool:
    return (
        bar.market_session == MarketSession.KRX_REGULAR.value
        and bar.market_phase == MarketPhase.CONTINUOUS.value
        and bar.research_session == f"{start.date().isoformat()}:KRX_REGULAR"
        and bar.capture_quality == "complete" and bool(bar.revision_id)
        and start.second == start.microsecond == 0 and end == start + _MINUTE
        and available.date() == start.date() and available >= end
        and 0 < bar.low <= bar.close <= bar.high and bar.trade_value_million_won >= 0
    )


def _same_scope(setup: PanicSetup, bar: KrxMinuteBarFrame) -> bool:
    return setup.symbol == bar.code and setup.research_session == bar.research_session


def _known_bars(
    current: KrxMinuteBarFrame, start: datetime, cutoff: datetime,
    history: Iterable[KrxMinuteBarFrame], minutes: int,
) -> dict[datetime, KrxMinuteBarFrame | None]:
    latest: dict[datetime, tuple[datetime, datetime, KrxMinuteBarFrame | None]] = {}
    earliest = start - (minutes + 2) * _MINUTE
    for row in chain(history, (current,)):
        if row.code != current.code or row.research_session != current.research_session:
            continue
        row_start, end, available = _bar_times(row)
        if not earliest <= row_start <= start or available > cutoff:
            continue
        previous = latest.get(row_start)
        if previous is None or previous[0] < available:
            latest[row_start] = available, end, row
        elif previous[0] == available and previous[2] != row:
            latest[row_start] = available, end, None
    known: dict[datetime, KrxMinuteBarFrame | None] = {}
    for row_start, (available, end, row) in latest.items():
        if row is not None and _complete_regular_bar(row, row_start, end, available):
            known[row_start] = row
        else:
            known[row_start] = None
    return known


def _complete_window(
    history: Mapping[datetime, KrxMinuteBarFrame | None], endpoint: datetime, minutes: int,
) -> tuple[KrxMinuteBarFrame, ...]:
    rows = tuple(history.get(endpoint - offset * _MINUTE) for offset in range(minutes, -1, -1))
    complete = tuple(row for row in rows if row is not None)
    return complete if len(complete) == len(rows) else ()


def _decline_reached(rows: tuple[KrxMinuteBarFrame, ...], decline_bps: int) -> bool:
    return rows[-1].close * 10_000 <= rows[0].close * (10_000 - decline_bps)


def _window_refs(
    window: tuple[KrxMinuteBarFrame, ...], previous: tuple[KrxMinuteBarFrame, ...],
) -> tuple[str, ...]:
    return tuple(row.revision_id for row in (*previous[:1], *window))


def _confirm_setup(
    bar: KrxMinuteBarFrame, start: datetime, cutoff: datetime, rules: PanicReboundRules,
    setup: PanicSetup, history: Mapping[datetime, KrxMinuteBarFrame | None],
) -> PanicReboundDecision:
    if start <= setup.trigger_bar_start:
        if start == setup.trigger_bar_start and bar.revision_id != setup.trigger_revision_id:
            return PanicReboundDecision("NO_TRADE", "panic_trigger_revised", input_refs=(bar.revision_id,))
        return PanicReboundDecision("ARM", "panic_waiting_for_next_completed_close", setup, setup.input_refs)
    if start != setup.trigger_bar_start + _MINUTE or cutoff <= setup.trigger_available_at:
        return PanicReboundDecision("NO_TRADE", "panic_next_completed_close_unproven")
    trigger_window = _complete_window(history, setup.trigger_bar_start, rules.lookback_minutes)
    previous = _complete_window(history, setup.trigger_bar_start - _MINUTE, rules.lookback_minutes)
    if not trigger_window or _window_refs(trigger_window, previous) != setup.input_refs:
        return PanicReboundDecision("NO_TRADE", "panic_trigger_revised_or_unproven")
    refs = (*setup.input_refs, bar.revision_id)
    if bar.close <= setup.trigger_close:
        return PanicReboundDecision("NO_TRADE", "panic_positive_close_recovery_unconfirmed", input_refs=refs)
    return PanicReboundDecision("ENTER", "panic_next_completed_close_recovered", setup, refs)


def _with_news(
    decision: PanicReboundDecision, bar: KrxMinuteBarFrame, cutoff: datetime, proofs: Iterable[PanicNewsProof],
) -> PanicReboundDecision:
    eligible: list[tuple[datetime, PanicNewsProof]] = []
    for row in proofs:
        if row.symbol != bar.code:
            continue
        if row.effective_at is not None:
            effective = row.effective_at.astimezone(KST)
            if effective.date() != cutoff.date() or effective > cutoff:
                continue
        if row.available_at is None:
            return PanicReboundDecision("NO_TRADE", "panic_news_unresolved", input_refs=decision.input_refs)
        available = row.available_at.astimezone(KST)
        if available <= cutoff:
            eligible.append((available, row))
    if not eligible:
        return PanicReboundDecision("NO_TRADE", "panic_news_unresolved", input_refs=decision.input_refs)
    latest_at = max(available for available, _ in eligible)
    latest = tuple(row for available, row in eligible if available == latest_at)
    refs = tuple(dict.fromkeys((*decision.input_refs, *sorted({
        ref for row in latest for ref in (row.revision, *row.evidence_refs)
    }))))
    if any(row.effective_at is None or not row.evidence_refs for row in latest):
        return PanicReboundDecision("NO_TRADE", "panic_news_unresolved", input_refs=refs)
    dispositions = {row.disposition for row in latest}
    if len(dispositions) > 1:
        return PanicReboundDecision("NO_TRADE", "panic_news_conflict", input_refs=refs)
    if dispositions == {"fundamental_adverse"}:
        return PanicReboundDecision("NO_TRADE", "panic_fundamental_adverse", input_refs=refs)
    if dispositions == {"supported_nonfundamental"}:
        return PanicReboundDecision("ENTER", decision.reason, decision.setup, refs)
    return PanicReboundDecision("NO_TRADE", "panic_news_unresolved", input_refs=refs)


def _manage_position(
    bar: KrxMinuteBarFrame, start: datetime, end: datetime, cutoff: datetime,
    rules: PanicReboundRules, setup: PanicSetup | None, opened_at: datetime,
) -> PanicReboundDecision:
    if opened_at.utcoffset() is None:
        raise ValueError("PANIC opened_at must include its timezone")
    opened_at = opened_at.astimezone(KST)
    if setup is None or not _same_scope(setup, bar) or opened_at < setup.trigger_available_at:
        return PanicReboundDecision("HOLD", "panic_position_context_unproven", setup)
    refs = tuple(dict.fromkeys((*setup.input_refs, bar.revision_id)))
    if not _complete_regular_bar(bar, start, end, cutoff) or start < opened_at:
        return PanicReboundDecision("HOLD", "panic_position_close_unproven", setup, refs)
    if cutoff >= opened_at + timedelta(minutes=rules.max_hold_minutes):
        return PanicReboundDecision("EXIT", "panic_max_hold_reached", setup, refs)
    if bar.close <= setup.failure_price:
        return PanicReboundDecision("EXIT", "panic_failure_close_reached", setup, refs)
    if bar.close >= setup.target_price:
        return PanicReboundDecision("EXIT", "panic_rebound_close_reached", setup, refs)
    return PanicReboundDecision("HOLD", "panic_position_within_pinned_range", setup, refs)
