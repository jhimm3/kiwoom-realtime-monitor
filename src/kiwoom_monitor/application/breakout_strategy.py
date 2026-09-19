"""첫 KRX 완료봉 돌파 Family의 결정론적 판단과 상태 계약."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Protocol

from kiwoom_monitor.application.research_factors import (
    FACTOR_VERSION,
    RANK_PERSISTENCE_ID,
    ROLLING_HIGH_BREAKOUT_ID,
    FactorValue,
    RankPersistenceParameters,
    RollingHighBreakoutParameters,
    compute_rank_persistence,
    compute_rolling_high_breakout,
    get_factor_definition,
)
from kiwoom_monitor.application.research_replay import (
    CandidateUniverseFrame,
    KrxMinuteBarFrame,
)
from kiwoom_monitor.domain.ranking import normalize_stock_code


STRATEGY_ID = "krx_bar_close_breakout"
STRATEGY_VERSION = "v1"
STATE_NAMES = frozenset({"flat", "candidate", "open", "cooldown"})


class StrategyRuntimeConfig(Protocol):
    strategy_version: str
    rank_persistence_enabled: bool
    rank_persistence_required: bool
    rank_top_k: int | None
    rank_window_seconds: int | None
    rank_max_gap_seconds: int | None
    rank_min_residency_seconds: int | None
    stop_loss_bps: int
    target_bps: int
    max_hold_minutes: int
    quantity: int
    capital_won: int
    signal_valid_seconds: int


@dataclass(frozen=True)
class BreakoutStrategyConfig:
    strategy_version: str
    rolling_factor_version: str
    rank_factor_version: str
    lookback_bars: int
    buffer_bps: int
    rank_persistence_enabled: bool
    rank_persistence_required: bool
    rank_top_k: int | None
    rank_window_seconds: int | None
    rank_max_gap_seconds: int | None
    rank_min_residency_seconds: int | None
    stop_loss_bps: int
    target_bps: int
    max_hold_minutes: int
    quantity: int
    capital_won: int
    signal_valid_seconds: int
    cooldown_seconds: int

    def __post_init__(self) -> None:
        if self.strategy_version != STRATEGY_VERSION:
            raise ValueError(f"unregistered strategy: {STRATEGY_ID}/{self.strategy_version}")
        get_factor_definition(ROLLING_HIGH_BREAKOUT_ID, self.rolling_factor_version)
        get_factor_definition(RANK_PERSISTENCE_ID, self.rank_factor_version)
        if self.rank_persistence_enabled:
            if None in (
                self.rank_top_k, self.rank_window_seconds, self.rank_max_gap_seconds,
                self.rank_min_residency_seconds,
            ):
                raise ValueError("enabled rank persistence requires all rank parameters")
            RankPersistenceParameters(
                top_k=int(self.rank_top_k),
                window_seconds=int(self.rank_window_seconds),
                max_gap_seconds=int(self.rank_max_gap_seconds),
            )
            if int(self.rank_min_residency_seconds) < 0:
                raise ValueError("rank_min_residency_seconds must not be negative")
        elif self.rank_persistence_required:
            raise ValueError("disabled rank persistence cannot be required")
        RollingHighBreakoutParameters(self.lookback_bars, self.buffer_bps)
        if self.stop_loss_bps <= 0 or self.target_bps <= 0:
            raise ValueError("stop_loss_bps and target_bps must be positive")
        if self.max_hold_minutes <= 0 or self.quantity <= 0 or self.capital_won <= 0:
            raise ValueError("hold duration, quantity, and capital must be positive")
        if self.signal_valid_seconds <= 0 or self.cooldown_seconds < 0:
            raise ValueError("signal validity must be positive and cooldown must not be negative")

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        if not self.rank_persistence_enabled:
            document.update({
                "rank_persistence_required": False,
                "rank_top_k": None,
                "rank_window_seconds": None,
                "rank_max_gap_seconds": None,
                "rank_min_residency_seconds": None,
            })
        return document


def default_shadow_breakout_config() -> BreakoutStrategyConfig:
    """Return the editable initial profile used by the order-free live monitor."""
    return BreakoutStrategyConfig(
        strategy_version="v1",
        rolling_factor_version="v1",
        rank_factor_version="v1",
        lookback_bars=5,
        buffer_bps=0,
        rank_persistence_enabled=True,
        rank_persistence_required=True,
        rank_top_k=20,
        rank_window_seconds=300,
        rank_max_gap_seconds=60,
        rank_min_residency_seconds=60,
        stop_loss_bps=300,
        target_bps=500,
        max_hold_minutes=10,
        quantity=1,
        capital_won=1_000_000,
        signal_valid_seconds=60,
        cooldown_seconds=30,
    )


@dataclass(frozen=True)
class StrategyState:
    status: str = "flat"
    symbol: str = ""
    candidate_key: str = ""
    candidate_expires_at: str = ""
    entry_price: int | None = None
    position_quantity: int | None = None
    opened_at: str = ""
    cooldown_until: str = ""
    emitted_candidate_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in STATE_NAMES:
            raise ValueError(f"unknown strategy state: {self.status}")
        if self.status == "open" and (
            not normalize_stock_code(self.symbol) or self.entry_price is None
            or self.position_quantity is None or self.position_quantity <= 0 or not self.opened_at
        ):
            raise ValueError("open state requires symbol, entry_price, quantity, and opened_at")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureSnapshot:
    snapshot_id: str
    run_id: str
    decision_time: str
    input_cutoff: str
    symbol: str
    universe_ref: str
    feature_values: tuple[Mapping[str, Any], ...]
    input_revision_refs: tuple[str, ...]
    strategy_id: str
    strategy_version: str
    policy_version: str
    state_before: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyDecision:
    decision_id: str
    run_id: str
    snapshot_id: str
    symbol: str
    proposal: str
    final_action: str
    quantity: int
    signal_reference_price: int
    required_capital_won: int
    reasons: tuple[str, ...]
    constraints: tuple[str, ...]
    state_before: Mapping[str, Any]
    state_after: Mapping[str, Any]
    decided_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateEvent:
    event_id: str
    run_id: str
    dedup_key: str
    symbol: str
    strategy_id: str
    strategy_version: str
    setup: str
    reference_revision_id: str
    transition: str
    quantity: int
    signal_reference_price: int
    available_at: str
    snapshot_id: str
    decision_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyEvaluation:
    snapshot: FeatureSnapshot
    decision: StrategyDecision
    candidate_event: CandidateEvent | None
    state: StrategyState


def evaluate_breakout_bar(
    *,
    run_id: str,
    evaluation_bar: KrxMinuteBarFrame,
    bar_history: Iterable[KrxMinuteBarFrame],
    universe_frames: Iterable[CandidateUniverseFrame],
    config: BreakoutStrategyConfig,
    state: StrategyState,
) -> StrategyEvaluation:
    """한 완료봉 시점에 보였던 입력만 사용해 한 번 판단한다."""
    universe_rows = tuple(universe_frames)
    decision_time = _aware_datetime(evaluation_bar.available_at).astimezone(timezone.utc)
    code = normalize_stock_code(evaluation_bar.code)
    rolling = compute_rolling_high_breakout(
        bar_history,
        evaluation_bar,
        RollingHighBreakoutParameters(config.lookback_bars, config.buffer_bps),
    )
    factors: list[FactorValue] = [rolling]
    if config.rank_persistence_enabled:
        assert config.rank_top_k is not None
        assert config.rank_window_seconds is not None
        assert config.rank_max_gap_seconds is not None
        factors.append(compute_rank_persistence(
            universe_rows,
            code,
            decision_time,
            RankPersistenceParameters(
                config.rank_top_k, config.rank_window_seconds, config.rank_max_gap_seconds,
            ),
        ))
    return evaluate_factor_entry_bar(
        run_id=run_id, evaluation_bar=evaluation_bar, universe_frames=universe_rows,
        config=config, state=state, factors=tuple(factors), strategy_id=STRATEGY_ID,
        setup="rolling_high_breakout", signal_key="breakout",
        negative_reason="rolling_high_not_broken", positive_reason="rolling_high_breakout",
    )


def evaluate_factor_entry_bar(
    *,
    run_id: str,
    evaluation_bar: KrxMinuteBarFrame,
    universe_frames: Iterable[CandidateUniverseFrame],
    config: StrategyRuntimeConfig,
    state: StrategyState,
    factors: tuple[FactorValue, ...],
    strategy_id: str,
    setup: str,
    signal_key: str,
    negative_reason: str,
    positive_reason: str,
) -> StrategyEvaluation:
    """동일 체결 정책을 쓰는 등록 Family의 공통 Snapshot·Decision 계약."""
    if not str(run_id).strip():
        raise ValueError("run_id is required")
    if not factors:
        raise ValueError("at least one entry factor is required")
    decision_time = _aware_datetime(evaluation_bar.available_at).astimezone(timezone.utc)
    code = normalize_stock_code(evaluation_bar.code)
    before = _advance_timed_state(state, decision_time)
    universe = _latest_universe(universe_frames, decision_time)
    input_refs = tuple(dict.fromkeys(
        ref for factor in factors for ref in factor.input_refs
    ))
    universe_ref = universe.revision_id if universe is not None else ""
    if universe_ref:
        input_refs = tuple(dict.fromkeys((*input_refs, universe_ref)))
    snapshot_body = {
        "run_id": run_id,
        "decision_time": decision_time.isoformat(),
        "input_cutoff": decision_time.isoformat(),
        "symbol": code,
        "universe_ref": universe_ref,
        "feature_values": [factor.to_dict() for factor in factors],
        "input_revision_refs": input_refs,
        "strategy_id": strategy_id,
        "strategy_version": config.strategy_version,
        "policy_version": "single_position_buy_then_sell/v1",
        "state_before": before.to_dict(),
    }
    snapshot = FeatureSnapshot(
        snapshot_id=_content_id("snapshot", snapshot_body),
        **snapshot_body,
    )
    proposal, final_action, reasons, constraints, after, new_candidate = _decide(
        code=code,
        decision_time=decision_time,
        evaluation_bar=evaluation_bar,
        universe=universe,
        factors=tuple(factors),
        config=config,
        state=before,
        strategy_id=strategy_id,
        setup=setup,
        signal_key=signal_key,
        negative_reason=negative_reason,
        positive_reason=positive_reason,
    )
    decision_body = {
        "run_id": run_id,
        "snapshot_id": snapshot.snapshot_id,
        "symbol": code,
        "proposal": proposal,
        "final_action": final_action,
        "quantity": _decision_quantity(proposal, config, before),
        "signal_reference_price": evaluation_bar.close,
        "required_capital_won": (
            evaluation_bar.close * config.quantity if proposal == "ENTER" else 0
        ),
        "reasons": reasons,
        "constraints": constraints,
        "state_before": before.to_dict(),
        "state_after": after.to_dict(),
        "decided_at": decision_time.isoformat(),
    }
    decision = StrategyDecision(
        decision_id=_content_id("decision", decision_body),
        **decision_body,
    )
    candidate_event = None
    if new_candidate is not None:
        candidate_body = {
            "run_id": run_id,
            "dedup_key": new_candidate[0],
            "symbol": code,
            "strategy_id": strategy_id,
            "strategy_version": config.strategy_version,
            "setup": setup,
            "reference_revision_id": new_candidate[1],
            "transition": "flat_to_candidate",
            "quantity": config.quantity,
            "signal_reference_price": evaluation_bar.close,
            "available_at": decision_time.isoformat(),
            "snapshot_id": snapshot.snapshot_id,
            "decision_id": decision.decision_id,
        }
        candidate_event = CandidateEvent(
            event_id=_content_id("candidate", candidate_body),
            **candidate_body,
        )
    return StrategyEvaluation(snapshot, decision, candidate_event, after)


def _decide(
    *,
    code: str,
    decision_time: datetime,
    evaluation_bar: KrxMinuteBarFrame,
    universe: CandidateUniverseFrame | None,
    factors: tuple[FactorValue, ...],
    config: StrategyRuntimeConfig,
    state: StrategyState,
    strategy_id: str,
    setup: str,
    signal_key: str,
    negative_reason: str,
    positive_reason: str,
) -> tuple[str, str, tuple[str, ...], tuple[str, ...], StrategyState, tuple[str, str] | None]:
    constraints = ("KRX", "single_strategy", "max_one_position", "buy_then_sell", "no_pyramiding")
    if state.status == "open":
        return _decide_open(decision_time, evaluation_bar, config, state, constraints)
    if state.status == "cooldown":
        return "NO_TRADE", "NO_TRADE", ("cooldown_active",), constraints, state, None
    if state.status == "candidate" and state.symbol != code:
        return "NO_TRADE", "NO_TRADE", ("portfolio_slot_reserved",), constraints, state, None

    entry_factor = factors[0]
    if entry_factor.status != "valid" or not isinstance(entry_factor.value, Mapping):
        return "NO_TRADE", "NO_TRADE", (f"required_factor_missing:{entry_factor.reason}",), constraints, state, None
    if not bool(entry_factor.value.get(signal_key)):
        after = _flat_preserving_seen(state)
        return "NO_TRADE", "NO_TRADE", (negative_reason,), constraints, after, None
    if universe is None:
        return "NO_TRADE", "NO_TRADE", ("candidate_universe_missing",), constraints, state, None
    if code not in universe.codes:
        return "NO_TRADE", "NO_TRADE", ("outside_contemporaneous_universe",), constraints, state, None

    reasons = [positive_reason]
    if config.rank_persistence_enabled:
        rank = factors[1]
        if rank.status != "valid" or not isinstance(rank.value, Mapping):
            if config.rank_persistence_required:
                return (
                    "NO_TRADE", "NO_TRADE",
                    (f"required_rank_factor_missing:{rank.reason}",), constraints, state, None,
                )
            reasons.append(f"optional_rank_factor_missing:{rank.reason}")
        else:
            assert config.rank_min_residency_seconds is not None
            if int(rank.value["residency_seconds"]) < config.rank_min_residency_seconds:
                return "NO_TRADE", "NO_TRADE", ("rank_residency_below_threshold",), constraints, state, None
            reasons.append("rank_persistence_satisfied")

    if state.status == "candidate":
        return "HOLD", "HOLD", ("candidate_condition_unchanged",), constraints, state, None
    if evaluation_bar.close * config.quantity > config.capital_won:
        return "NO_TRADE", "NO_TRADE", ("configured_capital_insufficient",), constraints, state, None

    reference_revision = str(entry_factor.value["reference_revision_id"])
    key_material = {
        "symbol": code,
        "setup": setup,
        "reference_revision_id": reference_revision,
        "transition": "flat_to_candidate",
        "strategy_version": config.strategy_version,
    }
    dedup_key = _content_id("setup", key_material)
    if dedup_key in state.emitted_candidate_keys:
        return "HOLD", "HOLD", ("candidate_already_emitted",), constraints, state, None
    expires_at = decision_time + timedelta(seconds=config.signal_valid_seconds)
    emitted = (*state.emitted_candidate_keys, dedup_key)
    after = StrategyState(
        status="candidate",
        symbol=code,
        candidate_key=dedup_key,
        candidate_expires_at=expires_at.isoformat(),
        emitted_candidate_keys=emitted,
    )
    return "ENTER", "ENTER", tuple(reasons), constraints, after, (dedup_key, reference_revision)


def _decide_open(
    decision_time: datetime,
    bar: KrxMinuteBarFrame,
    config: StrategyRuntimeConfig,
    state: StrategyState,
    constraints: tuple[str, ...],
) -> tuple[str, str, tuple[str, ...], tuple[str, ...], StrategyState, None]:
    if state.symbol != normalize_stock_code(bar.code):
        return "HOLD", "HOLD", ("different_symbol_position_managed_elsewhere",), constraints, state, None
    assert state.entry_price is not None
    opened_at = _aware_datetime(state.opened_at).astimezone(timezone.utc)
    reasons: list[str] = []
    if bar.close * 10_000 <= state.entry_price * (10_000 - config.stop_loss_bps):
        reasons.append("stop_loss_reached")
    if bar.close * 10_000 >= state.entry_price * (10_000 + config.target_bps):
        reasons.append("target_reached")
    if decision_time >= opened_at + timedelta(minutes=config.max_hold_minutes):
        reasons.append("max_hold_reached")
    if reasons:
        # 체결 전까지 실제 포지션 상태는 open으로 유지한다. D3c가 체결로 cooldown을 확정한다.
        return "EXIT", "EXIT", tuple(reasons), constraints, state, None
    return "HOLD", "HOLD", ("position_exit_condition_not_met",), constraints, state, None


def _advance_timed_state(state: StrategyState, now: datetime) -> StrategyState:
    if state.status == "candidate" and state.candidate_expires_at:
        if now > _aware_datetime(state.candidate_expires_at).astimezone(timezone.utc):
            return _flat_preserving_seen(state)
    if state.status == "cooldown" and state.cooldown_until:
        if now >= _aware_datetime(state.cooldown_until).astimezone(timezone.utc):
            return _flat_preserving_seen(state)
    return state


def _flat_preserving_seen(state: StrategyState) -> StrategyState:
    return StrategyState(emitted_candidate_keys=state.emitted_candidate_keys)


def _decision_quantity(
    proposal: str, config: StrategyRuntimeConfig, state: StrategyState,
) -> int:
    if proposal == "ENTER":
        return config.quantity
    if proposal == "EXIT" and state.position_quantity is not None:
        return state.position_quantity
    return 0


def _latest_universe(
    frames: Iterable[CandidateUniverseFrame], cutoff: datetime,
) -> CandidateUniverseFrame | None:
    eligible = [
        frame for frame in frames
        if _aware_datetime(frame.available_at).astimezone(timezone.utc) <= cutoff
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda frame: (
        _aware_datetime(frame.available_at).astimezone(timezone.utc),
        frame.accepted_sequence,
        frame.revision_id,
    ))


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def logical_evaluation_document(evaluation: StrategyEvaluation) -> dict[str, Any]:
    """run 저장 시각과 무관한 결과 hash 입력."""
    return {
        "snapshot": evaluation.snapshot.to_dict(),
        "decision": evaluation.decision.to_dict(),
        "candidate_event": (
            evaluation.candidate_event.to_dict() if evaluation.candidate_event else None
        ),
    }


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("strategy timestamps must be timezone-aware")
    return parsed
