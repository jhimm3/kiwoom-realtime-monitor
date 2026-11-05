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
_PRIOR_BARS = 10  # EX-BR B0 protocol.


class ThemeRole(StrEnum):
    OWN_LEADER = "own_leader"
    DEPENDENT_FOLLOWER = "dependent_follower"
    TRANSITION = "transition"


@dataclass(frozen=True)
class BreakoutThemePolicy:
    variant: Literal["B0", "TH"]
    quantity: int
    exit_at_kst: str
    theme_id: str | None
    theme_role: ThemeRole | None
    exit_on_dependency_loss: bool

    def __post_init__(self) -> None:
        if self.variant not in {"B0", "TH"} or type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("breakout research requires B0 or TH and an explicit positive quantity")
        if time.fromisoformat(self.exit_at_kst).tzinfo is not None or type(self.exit_on_dependency_loss) is not bool:
            raise ValueError("breakout research requires a local KST cutoff and a boolean dependency policy")
        if self.theme_role is not None:
            object.__setattr__(self, "theme_role", ThemeRole(self.theme_role))
        if self.variant == "B0" and (self.theme_id is not None or self.theme_role is not None or self.exit_on_dependency_loss):
            raise ValueError("B0 cannot include a theme condition")
        if self.variant == "TH" and (not isinstance(self.theme_id, str) or not self.theme_id.strip()):
            raise ValueError("TH requires the theme scope")

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> BreakoutThemePolicy:
        if set(document) != {"variant", "quantity", "exit_at_kst", "theme_id", "theme_role", "exit_on_dependency_loss"}:
            raise ValueError("invalid breakout research policy fields")
        return cls(**document)


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
    if policy.variant == "TH":
        proof = _theme_at(proofs, bar.code, policy.theme_id, cutoff)
        if proof is None:
            return BreakoutThemeDecision("WAIT", 0, "theme_role_not_known_at_signal", evidence_refs=refs)
        refs = tuple(dict.fromkeys((*refs, proof.revision, *proof.evidence_refs)))
        if policy.theme_role is not None and proof.role is not policy.theme_role:
            return BreakoutThemeDecision("WAIT", 0, "theme_role_outside_stratum", evidence_refs=refs)
        if proof.role is ThemeRole.DEPENDENT_FOLLOWER and proof.dependency_valid is not True:
            return BreakoutThemeDecision("WAIT", 0, "theme_dependency_not_supported_at_signal", evidence_refs=refs)

    setup = BreakoutThemeSetup(
        policy, bar.code, bar.research_session, bar.schedule_version, start, cutoff,
        exit_at, level, bar.open, proof, refs,
    )
    return BreakoutThemeDecision("ENTER", policy.quantity, "breakout_first_completed_crossing", setup, refs)


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
    first = start - (_PRIOR_BARS + 1) * _MINUTE
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
    first = start - _PRIOR_BARS * _MINUTE
    result = []
    for offset in range(_PRIOR_BARS):
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
