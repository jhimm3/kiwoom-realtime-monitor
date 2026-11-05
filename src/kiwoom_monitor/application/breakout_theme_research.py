"""Completed-minute EX-BR and point-in-time EX-TH hypotheses."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Any, Literal

from kiwoom_monitor.application.market_session_schedule import MarketPhase, MarketSession
from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
from kiwoom_monitor.domain.time_basis import KST, aware_iso_kst


_MINUTE = timedelta(minutes=1)
BREAKOUT_PRIOR_BARS = 10  # EX-BR B0 protocol.


class ThemeRole(StrEnum):
    OWN_LEADER = "own_leader"
    DEPENDENT_FOLLOWER = "dependent_follower"
    TRANSITION = "transition"


@dataclass(frozen=True)
class SymbolBreakoutFilters:
    minimum_price_progress_bps: int
    minimum_symbol_relative_turnover_bps: int

    def __post_init__(self) -> None:
        _require_nonnegative(self.minimum_price_progress_bps, self.minimum_symbol_relative_turnover_bps)


@dataclass(frozen=True)
class MarketBreakoutFilters(SymbolBreakoutFilters):
    market_scope: str
    minimum_market_relative_turnover_bps: int

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.market_scope, str) or not self.market_scope.strip():
            raise ValueError("market turnover filter requires its actual observation scope")
        _require_nonnegative(self.minimum_market_relative_turnover_bps)


@dataclass(frozen=True)
class ContextBreakoutFilters(MarketBreakoutFilters):
    minimum_daily_headroom_bps: int | None
    require_catalyst: bool

    def __post_init__(self) -> None:
        super().__post_init__()
        if (type(self.require_catalyst) is not bool
                or self.minimum_daily_headroom_bps is None and not self.require_catalyst):
            raise ValueError("B3 requires an explicit daily headroom or catalyst condition")
        if self.minimum_daily_headroom_bps is not None:
            _require_nonnegative(self.minimum_daily_headroom_bps)


_FILTERS_BY_VARIANT = {"B1": SymbolBreakoutFilters, "B2": MarketBreakoutFilters, "B3": ContextBreakoutFilters}


@dataclass(frozen=True)
class BreakoutThemePolicy:
    variant: Literal["B0", "B1", "B2", "B3", "TH"]
    quantity: int
    exit_at_kst: str
    theme_id: str | None
    theme_role: ThemeRole | None
    exit_on_dependency_loss: bool

    def __post_init__(self) -> None:
        if self.variant not in {"B0", "TH", *_FILTERS_BY_VARIANT} or type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("breakout research requires a known variant and an explicit positive quantity")
        if self.variant in _FILTERS_BY_VARIANT and not isinstance(self, FilteredBreakoutThemePolicy):
            raise ValueError("filtered breakout variants require explicit entry filters")
        if time.fromisoformat(self.exit_at_kst).tzinfo is not None or type(self.exit_on_dependency_loss) is not bool:
            raise ValueError("breakout research requires a local KST cutoff and a boolean dependency policy")
        if self.theme_role is not None:
            object.__setattr__(self, "theme_role", ThemeRole(self.theme_role))
        if self.variant != "TH" and (self.theme_id is not None or self.theme_role is not None or self.exit_on_dependency_loss):
            raise ValueError("EX-BR cannot include a theme condition")
        if self.variant == "TH" and (not isinstance(self.theme_id, str) or not self.theme_id.strip()):
            raise ValueError("TH requires the theme scope")

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> BreakoutThemePolicy:
        fields = {"variant", "quantity", "exit_at_kst", "theme_id", "theme_role", "exit_on_dependency_loss"}
        variant = document.get("variant")
        filter_type = _FILTERS_BY_VARIANT.get(variant) if isinstance(variant, str) else None
        if set(document) != (fields | {"entry_filters"} if filter_type is not None else fields):
            raise ValueError("invalid breakout research policy fields")
        if filter_type is not None:
            filters = document["entry_filters"]
            if not isinstance(filters, Mapping):
                raise ValueError("breakout entry filters must be an object")
            try:
                parsed = filter_type(**filters)
            except TypeError as error:
                raise ValueError("invalid breakout entry filter fields") from error
            return FilteredBreakoutThemePolicy(**{**document, "entry_filters": parsed})
        return cls(**document)


@dataclass(frozen=True)
class FilteredBreakoutThemePolicy(BreakoutThemePolicy):
    entry_filters: SymbolBreakoutFilters

    def __post_init__(self) -> None:
        super().__post_init__()
        if type(self.entry_filters) is not _FILTERS_BY_VARIANT.get(self.variant):
            raise ValueError("entry filters must match the EX-BR variant")


@dataclass(frozen=True)
class MarketTurnoverValues:
    observed_turnover_won: int
    baseline_median_turnover_won: int

    def __post_init__(self) -> None:
        _require_nonnegative(self.observed_turnover_won, self.baseline_median_turnover_won)


@dataclass(frozen=True)
class BreakoutEntryProof:
    kind: Literal["market_relative_turnover", "daily_headroom", "catalyst"]
    scope: str
    effective_at: datetime | None
    available_at: datetime | None
    source: str
    revision: str
    evidence_refs: tuple[str, ...]
    basis: Literal["point_in_time", "retrospective"]
    value: MarketTurnoverValues | int | Literal["supported", "unsupported"] | None

    def __post_init__(self) -> None:
        if not all(isinstance(text, str) and text.strip() for text in (self.scope, self.source, self.revision)):
            raise ValueError("breakout entry proof requires scope and source revision")
        if self.basis not in {"point_in_time", "retrospective"}:
            raise ValueError("invalid breakout proof basis")
        for instant in (self.effective_at, self.available_at):
            if instant is not None and instant.utcoffset() is None:
                raise ValueError("breakout proof times must include their timezone")
        refs = tuple(self.evidence_refs)
        if not refs or not all(isinstance(ref, str) and ref.strip() for ref in refs):
            raise ValueError("breakout proof requires evidence references")
        object.__setattr__(self, "evidence_refs", refs)
        if self.kind == "market_relative_turnover":
            valid = isinstance(self.value, MarketTurnoverValues)
        elif self.kind == "daily_headroom":
            valid = type(self.value) is int and self.value > 0
        elif self.kind == "catalyst":
            valid = self.value in ("supported", "unsupported")
        else:
            raise ValueError("unknown breakout entry proof kind")
        if self.value is not None and not valid:
            raise ValueError("breakout proof value does not match its kind")

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> BreakoutEntryProof:
        fields = {"kind", "scope", "effective_at", "available_at", "source", "revision", "evidence_refs", "basis", "value"}
        if set(document) != fields or not isinstance(document["evidence_refs"], list):
            raise ValueError("invalid breakout entry proof fields")
        value = document["value"]
        if document["kind"] == "market_relative_turnover" and value is not None:
            if not isinstance(value, Mapping) or set(value) != {"observed_turnover_won", "baseline_median_turnover_won"}:
                raise ValueError("market turnover proof requires an observed value and baseline median")
            value = MarketTurnoverValues(**value)
        return cls(**{**document, "value": value, "effective_at": _optional_instant(document["effective_at"]),
                      "available_at": _optional_instant(document["available_at"]),
                      "evidence_refs": tuple(document["evidence_refs"])})


@dataclass(frozen=True)
class ThemeRoleProof:
    """The leader is the named dependency, or the former leader for a transition."""

    symbol: str
    theme_id: str
    role: ThemeRole
    leader_symbol: str
    effective_at: datetime | None
    available_at: datetime | None
    source: str
    revision: str
    evidence_refs: tuple[str, ...]
    basis: Literal["point_in_time", "retrospective"]
    dependency_valid: bool | None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.symbol, self.theme_id, self.leader_symbol, self.source, self.revision)):
            raise ValueError("theme role requires symbol, leader, theme and source revision")
        object.__setattr__(self, "role", ThemeRole(self.role))
        if (self.role is ThemeRole.OWN_LEADER) != (self.leader_symbol == self.symbol):
            raise ValueError("theme proof leader does not agree with the role")
        if (self.basis not in {"point_in_time", "retrospective"}
                or self.dependency_valid is not None and type(self.dependency_valid) is not bool
                or self.role is not ThemeRole.DEPENDENT_FOLLOWER and self.dependency_valid is not None):
            raise ValueError("invalid theme proof basis or dependency")
        for instant in (self.effective_at, self.available_at):
            if instant is not None and instant.utcoffset() is None:
                raise ValueError("theme proof times must include their timezone")
        refs = tuple(self.evidence_refs)
        if not refs or not all(ref.strip() for ref in refs):
            raise ValueError("theme proof requires evidence references")
        object.__setattr__(self, "evidence_refs", refs)

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> ThemeRoleProof:
        fields = {"symbol", "theme_id", "role", "leader_symbol", "effective_at", "available_at", "source",
                  "revision", "evidence_refs", "basis", "dependency_valid"}
        if set(document) != fields or not isinstance(document["evidence_refs"], list):
            raise ValueError("invalid theme role proof fields")
        return cls(**{**document, "role": ThemeRole(document["role"]),
                      "effective_at": _optional_instant(document["effective_at"]),
                      "available_at": _optional_instant(document["available_at"]),
                      "evidence_refs": tuple(document["evidence_refs"])})


@dataclass(frozen=True)
class BreakoutThemeSetup:
    policy: BreakoutThemePolicy
    symbol: str
    research_session: str
    schedule_version: str
    signal_bar_start: datetime
    signal_available_at: datetime
    exit_at: datetime
    breakout_level: int
    failure_price: int
    theme_proof: ThemeRoleProof | None
    evidence_refs: tuple[str, ...]
    transition_proof: ThemeRoleProof | None = None


@dataclass(frozen=True, kw_only=True)
class FilteredBreakoutThemeSetup(BreakoutThemeSetup):
    previous_close_won: int
    signal_turnover_million_won: int
    prior_turnover_sum_million_won: int
    entry_proofs: tuple[BreakoutEntryProof, ...]


@dataclass(frozen=True)
class BreakoutThemePosition:
    quantity: int
    entry_price: int
    opened_at: datetime

    def __post_init__(self) -> None:
        if (type(self.quantity) is not int or self.quantity <= 0 or type(self.entry_price) is not int
                or self.entry_price <= 0 or self.opened_at.utcoffset() is None):
            raise ValueError("breakout position requires actual fill quantity, price and aware time")


@dataclass(frozen=True)
class BreakoutThemeDecision:
    action: Literal["WAIT", "ENTER", "HOLD", "EXIT"]
    quantity: int
    reason: str
    setup: BreakoutThemeSetup | None = None
    evidence_refs: tuple[str, ...] = ()


def evaluate_breakout_theme(
    policy: BreakoutThemePolicy,
    *,
    bar: KrxMinuteBarFrame,
    history: Iterable[KrxMinuteBarFrame],
    proofs: Iterable[ThemeRoleProof],
    position: BreakoutThemePosition | None,
    setup: BreakoutThemeSetup | None,
    entry_proofs: Iterable[BreakoutEntryProof] = (),
) -> BreakoutThemeDecision:
    """Return a decision at receipt. The next eligible open belongs to execution."""
    start, end, cutoff = _bar_times(bar)
    if not _complete_regular(bar, start, end, cutoff):
        return BreakoutThemeDecision("WAIT", 0, "breakout_complete_regular_minute_required", setup)
    if position is not None:
        return _manage(bar, start, cutoff, proofs, position, setup)

    exit_at = datetime.combine(start.date(), time.fromisoformat(policy.exit_at_kst), KST)
    if cutoff >= exit_at:
        return BreakoutThemeDecision("WAIT", 0, "breakout_entry_window_ended")
    rows = _scoped_rows(bar, start, cutoff, history)
    if rows is None:
        return BreakoutThemeDecision("WAIT", 0, "newer_minute_already_observed")
    prior = _window(rows, start, cutoff)
    if not prior:
        return BreakoutThemeDecision("WAIT", 0, "breakout_prior_ten_minutes_unavailable")
    level = max(row.high for row in prior)
    refs = tuple(row.revision_id for row in prior) + (bar.revision_id,)
    if bar.close <= level:
        return BreakoutThemeDecision("WAIT", 0, "breakout_prior_high_not_crossed", evidence_refs=refs)

    previous_cutoff = aware_iso_kst(prior[-1].available_at)
    preceding = _window(rows, start - _MINUTE, previous_cutoff)
    if preceding and prior[-1].close > max(row.high for row in preceding):
        preceding_refs = tuple(dict.fromkeys(tuple(row.revision_id for row in preceding) + refs))
        return BreakoutThemeDecision("WAIT", 0, "breakout_crossing_already_observed", evidence_refs=preceding_refs)

    proof = None
    filtered_setup = {}
    setup_type = BreakoutThemeSetup
    if isinstance(policy, FilteredBreakoutThemePolicy):
        reason, selected, refs = _filter_entry(policy.entry_filters, bar, prior, end, cutoff, entry_proofs, refs)
        if reason:
            return BreakoutThemeDecision("WAIT", 0, reason, evidence_refs=refs)
        turnover, observations = selected
        setup_type = FilteredBreakoutThemeSetup
        filtered_setup = {
            "previous_close_won": prior[-1].close,
            "signal_turnover_million_won": bar.trade_value_million_won,
            "prior_turnover_sum_million_won": turnover,
            "entry_proofs": observations,
        }
    if policy.variant == "TH":
        proof = _theme_at(proofs, bar.code, policy.theme_id, cutoff)
        if proof is None:
            return BreakoutThemeDecision("WAIT", 0, "theme_role_not_known_at_signal", evidence_refs=refs)
        refs = tuple(dict.fromkeys((*refs, proof.revision, *proof.evidence_refs)))
        if policy.theme_role is not None and proof.role is not policy.theme_role:
            return BreakoutThemeDecision("WAIT", 0, "theme_role_outside_stratum", evidence_refs=refs)
        if proof.role is ThemeRole.DEPENDENT_FOLLOWER and proof.dependency_valid is not True:
            return BreakoutThemeDecision("WAIT", 0, "theme_dependency_not_supported_at_signal", evidence_refs=refs)

    setup = setup_type(
        policy, bar.code, bar.research_session, bar.schedule_version, start, cutoff,
        exit_at, level, bar.open, proof, refs, **filtered_setup,
    )
    return BreakoutThemeDecision("ENTER", policy.quantity, "breakout_first_completed_crossing", setup, refs)


def _filter_entry(filters, bar, prior, end, cutoff, evidence, refs):
    # Completed history and native receipts both carry bar turnover. The run
    # records separately whether the decision clock was reconstructed.
    if any(type(row.trade_value_million_won) is not int or row.trade_value_million_won < 0
           for row in (*prior, bar)):
        return "breakout_symbol_turnover_unavailable", None, refs
    previous = prior[-1].close
    if (bar.close - previous) * 10_000 < previous * filters.minimum_price_progress_bps:
        return "breakout_price_progress_below_threshold", None, refs
    turnover = sum(row.trade_value_million_won for row in prior)
    if turnover <= 0:
        return "breakout_symbol_turnover_baseline_unavailable", None, refs
    if bar.trade_value_million_won * BREAKOUT_PRIOR_BARS * 10_000 < turnover * filters.minimum_symbol_relative_turnover_bps:
        return "breakout_symbol_relative_turnover_below_threshold", None, refs
    if not isinstance(filters, MarketBreakoutFilters):
        return "", (turnover, ()), refs

    evidence = tuple(evidence)
    market = _entry_proof_at(evidence, "market_relative_turnover", filters.market_scope, cutoff, effective_at=end)
    if market is None:
        return "breakout_market_turnover_unavailable", None, refs
    refs = _proof_refs(refs, market)
    values = market.value
    if not isinstance(values, MarketTurnoverValues) or values.baseline_median_turnover_won <= 0:
        return "breakout_market_turnover_unavailable", None, refs
    if values.observed_turnover_won * 10_000 < values.baseline_median_turnover_won * filters.minimum_market_relative_turnover_bps:
        return "breakout_market_relative_turnover_below_threshold", None, refs

    selected = [market]
    if isinstance(filters, ContextBreakoutFilters):
        if filters.minimum_daily_headroom_bps is not None:
            daily = _entry_proof_at(evidence, "daily_headroom", bar.code, cutoff)
            if daily is None:
                return "breakout_daily_headroom_unavailable", None, refs
            refs = _proof_refs(refs, daily)
            if daily.value is None:
                return "breakout_daily_headroom_unavailable", None, refs
            if (daily.value - bar.close) * 10_000 < bar.close * filters.minimum_daily_headroom_bps:
                return "breakout_daily_headroom_below_threshold", None, refs
            selected.append(daily)
        if filters.require_catalyst:
            catalyst = _entry_proof_at(evidence, "catalyst", bar.code, cutoff)
            if catalyst is None:
                return "breakout_catalyst_unavailable", None, refs
            refs = _proof_refs(refs, catalyst)
            if catalyst.value is None:
                return "breakout_catalyst_unavailable", None, refs
            if catalyst.value != "supported":
                return "breakout_catalyst_not_supported", None, refs
            selected.append(catalyst)
    return "", (turnover, tuple(selected)), refs


def _entry_proof_at(evidence, kind, scope, cutoff, *, effective_at=None):
    rows = []
    for proof in evidence:
        if (proof.kind, proof.scope, proof.basis) != (kind, scope, "point_in_time"):
            continue
        if (effective_at is not None and proof.effective_at is not None and proof.effective_at != effective_at
                or proof.effective_at is not None and proof.effective_at > cutoff):
            continue
        if proof.available_at is None:
            return None
        if proof.available_at <= cutoff:
            rows.append(proof)
    if not rows:
        return None
    selected = max(rows, key=lambda proof: proof.available_at)
    if (selected.effective_at is None
            or any(row.available_at == selected.available_at and row != selected for row in rows)):
        return None
    return selected


def _proof_refs(refs, proof):
    return tuple(dict.fromkeys((*refs, proof.revision, *proof.evidence_refs)))


def _manage(bar, start, cutoff, proofs, position, setup):
    refs = (bar.revision_id,)
    if setup is None or setup.symbol != bar.code:
        return BreakoutThemeDecision("WAIT", 0, "breakout_original_setup_unavailable", setup, refs)
    if (start <= setup.signal_bar_start or position.opened_at < setup.signal_available_at
            or position.opened_at > cutoff):
        return BreakoutThemeDecision("WAIT", 0, "breakout_fill_outside_observable_execution_cut", setup, refs)
    if position.entry_price <= setup.failure_price:
        return BreakoutThemeDecision("WAIT", 0, "breakout_initial_risk_distance_unavailable", setup, refs)
    if cutoff >= setup.exit_at:
        return BreakoutThemeDecision("EXIT", position.quantity, "breakout_declared_session_exit", setup, refs)
    if bar.research_session != setup.research_session or bar.schedule_version != setup.schedule_version:
        return BreakoutThemeDecision("WAIT", 0, "breakout_session_evidence_changed", setup, refs)
    if bar.close <= setup.failure_price:
        return BreakoutThemeDecision("EXIT", position.quantity, "breakout_pinned_signal_open_failed", setup, refs)

    original = setup.theme_proof
    if (setup.policy.exit_on_dependency_loss and original is not None
            and original.role is ThemeRole.DEPENDENT_FOLLOWER and setup.transition_proof is None):
        proof = _theme_at(proofs, bar.code, original.theme_id, cutoff)
        if proof is not None and proof.leader_symbol == original.leader_symbol:
            refs = tuple(dict.fromkeys((*refs, proof.revision, *proof.evidence_refs)))
            if proof.role is ThemeRole.TRANSITION:
                updated = replace(setup, transition_proof=proof)
                return BreakoutThemeDecision("HOLD", 0, "theme_dependency_released_by_observed_transition", updated, refs)
            if proof.role is ThemeRole.DEPENDENT_FOLLOWER and proof.dependency_valid is False:
                return BreakoutThemeDecision("EXIT", position.quantity, "theme_pinned_dependency_lost", setup, refs)
    return BreakoutThemeDecision("HOLD", 0, "breakout_structure_held", setup, refs)


def _theme_at(proofs, symbol, theme_id, cutoff):
    latest_at = None
    latest = set()
    for proof in proofs:
        if proof.symbol != symbol or proof.theme_id != theme_id or proof.basis != "point_in_time":
            continue
        if proof.available_at is None:
            return None
        if proof.available_at > cutoff or proof.effective_at is not None and proof.effective_at > cutoff:
            continue
        if latest_at is None or proof.available_at > latest_at:
            latest_at, latest = proof.available_at, {proof}
        elif proof.available_at == latest_at:
            latest.add(proof)
    if len(latest) != 1:
        return None
    selected = next(iter(latest))
    return selected if selected.effective_at is not None else None


def _scoped_rows(bar, start, cutoff, history):
    first = start - (BREAKOUT_PRIOR_BARS + 1) * _MINUTE
    slots = {}
    for row in history:
        if (row.code, row.research_session, row.schedule_version) != (bar.code, bar.research_session, bar.schedule_version):
            continue
        row_start, _, available = _bar_times(row)
        if available > cutoff:
            continue
        if row_start > start:
            return None
        if first <= row_start < start:
            slots.setdefault(row_start, []).append((available, row))
    return slots


def _window(slots, start, cutoff):
    first = start - BREAKOUT_PRIOR_BARS * _MINUTE
    result = []
    for offset in range(BREAKOUT_PRIOR_BARS):
        expected = first + offset * _MINUTE
        rows = [(available, row) for available, row in slots.get(expected, ()) if available <= cutoff]
        if not rows:
            return ()
        available, row = max(rows, key=lambda pair: pair[0])
        _, end, _ = _bar_times(row)
        if (any(at == available and competing != row for at, competing in rows)
                or not _complete_regular(row, expected, end, available)):
            return ()
        result.append(row)
    return tuple(result)


def _complete_regular(row, start, end, cutoff):
    return (end - start == _MINUTE and end <= cutoff and start.date() == cutoff.date()
            and row.capture_quality == "complete" and bool(row.revision_id)
            and row.research_session == f"{start.date().isoformat()}:KRX_REGULAR" and bool(row.schedule_version)
            and row.market_session == MarketSession.KRX_REGULAR.value
            and row.market_phase == MarketPhase.CONTINUOUS.value
            and 0 < row.low <= min(row.open, row.close) and max(row.open, row.close) <= row.high)


def _bar_times(row):
    return tuple(aware_iso_kst(value) for value in (row.bar_start, row.bar_end, row.available_at))


def _optional_instant(value):
    return aware_iso_kst(value) if value is not None else None


def _require_nonnegative(*values):
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("breakout filter ratios and turnover values must be non-negative integers")
