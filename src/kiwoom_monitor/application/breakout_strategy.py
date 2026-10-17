"""KRX 완료봉 Family와 별도 0B RAM 돌파 후보의 순수 판단 계약."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
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
from kiwoom_monitor.application.theme_leadership import second_trade_points_from_rows
from kiwoom_monitor.domain.ranking import normalize_stock_code
from kiwoom_monitor.application.market_session_schedule import (
    KST, krx_regular_session_hours, krx_closing_auction_hours,
)


STRATEGY_ID = "krx_bar_close_breakout"
STRATEGY_VERSION = "v1"
STATE_NAMES = frozenset({"flat", "candidate", "open", "cooldown"})
REGULAR_SESSION_EXIT_POLICY = "krx_regular_before_auction/v1"


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
    session_exit_policy: str
    session_exit_buffer_seconds: int


def validate_session_exit_policy(policy: str, buffer_seconds: int) -> None:
    if type(buffer_seconds) is not int:
        raise ValueError("session exit buffer must be integer seconds")
    if policy == "" and buffer_seconds == 0:
        return
    if policy != REGULAR_SESSION_EXIT_POLICY or not 60 <= buffer_seconds <= 3600:
        raise ValueError("registered regular session exit policy requires a 60-3600 second buffer")


def regular_session_fill_deadline(at: datetime) -> datetime:
    """정규장 연속 체결의 제외 경계. 이미 열린 포지션에는 진입 거래일을 전달한다."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("session deadline requires a timezone-aware instant")
    local = at.astimezone(KST)
    auction_start, _ = krx_closing_auction_hours(local.date())
    return datetime.combine(local.date(), auction_start, KST)


def regular_session_time_rejection(at: datetime) -> str:
    deadline = regular_session_fill_deadline(at)
    local = at.astimezone(KST)
    opened, _ = krx_regular_session_hours(local.date())
    if local.weekday() >= 5 or local < datetime.combine(local.date(), opened, KST) or local >= deadline:
        return "outside_regular_continuous_session"
    return ""


def session_entry_time_rejection(config: StrategyRuntimeConfig, at: datetime, *,
                                 include_signal_validity: bool = True) -> str:
    from .leader_breakout import LeaderBreakoutConfig, leader_session_entry_rejection
    if isinstance(config, LeaderBreakoutConfig):
        return leader_session_entry_rejection(config, at)
    if not config.session_exit_policy:
        return ""
    reason = regular_session_time_rejection(at)
    if reason:
        return reason
    deadline = regular_session_fill_deadline(at)
    local = at.astimezone(KST)
    validity = config.signal_valid_seconds if include_signal_validity else 0
    required = timedelta(minutes=config.max_hold_minutes, seconds=validity + config.session_exit_buffer_seconds)
    if local + required >= deadline:
        return "session_entry_budget_exhausted"
    return ""


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
    session_exit_policy: str = ""
    session_exit_buffer_seconds: int = 0

    def __post_init__(self) -> None:
        validate_session_exit_policy(self.session_exit_policy, self.session_exit_buffer_seconds)
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
        if not self.session_exit_policy:
            document.pop("session_exit_policy")
            document.pop("session_exit_buffer_seconds")
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
        "policy_version": config.session_exit_policy or "single_position_buy_then_sell/v1",
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

    session_reason = session_entry_time_rejection(config, decision_time)
    if session_reason:
        return "NO_TRADE", "NO_TRADE", (session_reason,), constraints, _flat_preserving_seen(state), None

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
    if (config.session_exit_policy and decision_time >= regular_session_fill_deadline(opened_at)
            - timedelta(seconds=config.session_exit_buffer_seconds)):
        reasons.append("session_exit_due")
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


REALTIME_BREAKOUT_POLICY = "observed_window_high_breakout/v1"
REALTIME_BREAKOUT_CAPABILITIES = (
    "approved_krx_0b", "ram_second_price_trade_value", "ram_window_continuity",
)


@dataclass(frozen=True)
class RealtimeBreakoutPolicy:
    """Explicit research settings, independent of the completed-minute Family."""

    window_seconds: int
    buffer_bps: int
    minimum_trade_value_won: int
    max_data_age_seconds: int

    def __post_init__(self) -> None:
        if any(type(value) is not int for value in asdict(self).values()):
            raise ValueError("realtime breakout settings must be integers")
        if not 1 <= self.window_seconds <= 30 or not 0 <= self.buffer_bps < 10_000:
            raise ValueError("realtime window/buffer is outside the supported budget")
        if self.minimum_trade_value_won <= 0 or self.max_data_age_seconds <= 0:
            raise ValueError("explicit positive turnover and freshness limits are required")


@dataclass(frozen=True)
class RealtimeBreakoutState:
    symbol: str
    source_id: str
    generation: int | str
    parameters_hash: str
    status: str = "WATCH"
    episode_id: str = ""
    reference_high: int | None = None
    last_cut: str = ""
    reference_input_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"WATCH", "CANDIDATE", "INVALIDATED", "DATA_BLOCKED"}:
            raise ValueError("unsupported realtime breakout state")
        if self.status == "CANDIDATE" and (not self.episode_id or not self.reference_high):
            raise ValueError("candidate state requires fixed episode evidence")


@dataclass(frozen=True)
class RealtimeBreakoutEvaluation:
    decision_id: str
    policy_id: str
    as_of: str
    observed_at: str
    available_at: str
    valid_until: str
    status: str
    proposal: str
    reasons: tuple[str, ...]
    required_data_capabilities: tuple[str, ...]
    missing_data_capabilities: tuple[str, ...]
    input_refs: tuple[str, ...]
    reference_high: int | None
    observed_price: int | None
    current_trade_value_won: int | None
    new_candidate: bool
    state: RealtimeBreakoutState
    order_authorized: bool = False

    def __post_init__(self) -> None:
        if self.order_authorized:
            raise ValueError("realtime entry research cannot authorize orders")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_realtime_breakout(
    snapshot: Mapping[str, Any], *, symbol: str, policy: RealtimeBreakoutPolicy,
    state: RealtimeBreakoutState | None = None,
) -> RealtimeBreakoutEvaluation:
    """Consume one existing Collector RAM cut, with no IO or market/EV calculation.

    Windows are (cut-2W, cut-W] for the high and (cut-W, cut] for
    current turnover/price. Empty seconds are not fabricated as trades.
    A candidate's high remains fixed until invalidation or data discontinuity.
    """
    code = normalize_stock_code(symbol)
    cut = _aware_datetime(snapshot["decision_time"])
    source_id, generation = snapshot["source_id"], snapshot["generation"]
    # The shared Runtime appends a delivery-gap fence to the Collector integer.
    # Keep that identity opaque instead of dropping the consumer discontinuity.
    valid_generation = ((type(generation) is int and generation >= 0)
                        or (type(generation) is str and 0 < len(generation) <= 128))
    if (not code or not isinstance(source_id, str) or not source_id
            or not valid_generation or snapshot["durable"] is not False):
        raise ValueError("realtime breakout requires an identified ephemeral RAM cut")
    parameters_hash = _content_id(REALTIME_BREAKOUT_POLICY, asdict(policy))
    if state is not None and (state.symbol != code or state.parameters_hash != parameters_hash):
        raise ValueError("realtime state belongs to another symbol or policy settings")
    same_source = state is not None and (state.source_id, state.generation) == (source_id, generation)
    before = state if same_source else RealtimeBreakoutState(code, source_id, generation, parameters_hash)
    start = cut - timedelta(seconds=2 * policy.window_seconds)
    middle = cut - timedelta(seconds=policy.window_seconds)
    refs: tuple[str, ...] = ()
    reference_refs: tuple[str, ...] = ()
    observed_at = available_at = valid_until = ""

    def result(status, reason, *, missing=(), high=None, price=None, turnover=None,
               episode="", new=False, preserve=False):
        after = (
            before if reason == "out_of_order_cut" else replace(before, last_cut=cut.isoformat())
        ) if preserve else RealtimeBreakoutState(
            code, source_id, generation, parameters_hash, status, episode, high, cut.isoformat(),
            reference_refs,
        )
        body = dict(
            policy_id=REALTIME_BREAKOUT_POLICY, as_of=cut.isoformat(),
            observed_at=observed_at, available_at=available_at, valid_until=valid_until, status=status,
            proposal="ENTER" if status == "CANDIDATE" else "WAIT", reasons=(reason,),
            required_data_capabilities=REALTIME_BREAKOUT_CAPABILITIES,
            missing_data_capabilities=tuple(missing), input_refs=tuple(dict.fromkeys((*reference_refs, *refs))),
            reference_high=high, observed_price=price, current_trade_value_won=turnover,
            new_candidate=new, state=asdict(after), order_authorized=False,
        )
        return RealtimeBreakoutEvaluation(
            decision_id=_content_id("realtime-breakout", body), **{**body, "state": after},
        )

    if state is not None and state.last_cut and cut < _aware_datetime(state.last_cut):
        # A delayed callback may not rewind a later episode, even across a reconnect.
        before = state
        return result("DATA_BLOCKED", "out_of_order_cut", preserve=True)
    if (code, "KRX") not in snapshot["approved_sources"]:
        return result("DATA_BLOCKED", "krx_source_not_approved", missing=("approved_krx_0b",))
    floors = (snapshot["continuous_from"].get((code, "KRX")), snapshot["retention_since"])
    if any(value is None for value in floors):
        return result("DATA_BLOCKED", "ram_window_not_warmed_up", missing=("ram_window_continuity",))
    bounds = (*floors, snapshot.get("gap_at"))
    if any(value is not None and _aware_datetime(value) > start for value in bounds):
        return result("DATA_BLOCKED", "ram_window_not_continuous", missing=("ram_window_continuity",))
    if regular_session_time_rejection(cut) or regular_session_time_rejection(start):
        return result("WATCH", "outside_regular_continuous_session")
    if start.astimezone(KST).date() != cut.astimezone(KST).date():
        return result("DATA_BLOCKED", "window_crosses_session_date", missing=("ram_window_continuity",))

    # Reuse the H2 row/point contract, but do not require membership or H1/H2 scores.
    rows = []
    for row in snapshot["rows"]:
        if normalize_stock_code(row.get("code", "")) != code or row.get("market") != "KRX":
            continue
        # Apply the information cut before validating payload values: an unavailable
        # future payload must not turn a valid past decision into DATA_BLOCKED.
        try:
            raw_available = row.get("available_at")
            available = (datetime.fromtimestamp(raw_available, timezone.utc)
                         if isinstance(raw_available, (int, float)) else _aware_datetime(raw_available))
            if available > cut:
                continue
            observed = datetime.fromisoformat(
                f"{row.get('trading_date', '')}T{row.get('trade_second', '')}"
            ).replace(tzinfo=KST)
            if not start < observed <= cut:
                continue
        except (TypeError, ValueError, OverflowError, OSError):
            return result("DATA_BLOCKED", "invalid_second_trade_row", missing=("ram_second_price_trade_value",))
        rows.append(row)
    if len(rows) > 61:
        return result("DATA_BLOCKED", "ram_second_rows_exceed_budget", missing=("ram_second_price_trade_value",))
    points = second_trade_points_from_rows(
        {**row, "revision_id": f"ram:{source_id}:{generation}:{_content_id('second-trade', row)}"}
        for row in rows
    )
    if len(points) != len(rows) or any(point.high < point.close for point in points):
        return result("DATA_BLOCKED", "invalid_second_trade_row", missing=("ram_second_price_trade_value",))
    eligible = tuple(point for point in points
                     if start < _aware_datetime(point.observed_at) <= cut
                     and _aware_datetime(point.available_at) <= cut)
    refs = tuple(point.input_ref for point in eligible)
    if len({_aware_datetime(point.observed_at) for point in eligible}) != len(eligible):
        return result("DATA_BLOCKED", "duplicate_second_trade_row", missing=("ram_second_price_trade_value",))
    previous = tuple(point for point in eligible if _aware_datetime(point.observed_at) <= middle)
    current = tuple(point for point in eligible if _aware_datetime(point.observed_at) > middle)
    if not current or (not previous and before.status != "CANDIDATE"):
        return result("DATA_BLOCKED", "observed_window_price_missing", missing=("ram_second_price_trade_value",))
    latest = current[-1]
    observed_at, available_at = latest.observed_at, latest.available_at
    valid_until = (min(_aware_datetime(observed_at), _aware_datetime(available_at))
                   + timedelta(seconds=policy.max_data_age_seconds)).isoformat()
    if any((cut - _aware_datetime(at)).total_seconds() > policy.max_data_age_seconds
           for at in (latest.observed_at, latest.available_at)):
        return result("DATA_BLOCKED", "observed_price_stale_or_delayed", missing=("ram_second_price_trade_value",))
    price = latest.close
    turnover = sum(point.trade_value_won for point in current)
    high = before.reference_high if before.status == "CANDIDATE" else max(point.high for point in previous)
    reference_refs = (before.reference_input_refs if before.status == "CANDIDATE"
                      else tuple(point.input_ref for point in previous))
    assert high is not None
    if before.status == "CANDIDATE" and price <= high:
        return result("INVALIDATED", "fixed_breakout_structure_lost", high=high,
                      price=price, turnover=turnover, episode=before.episode_id)
    above = price * 10_000 > high * (10_000 + policy.buffer_bps)
    reason = "observed_high_not_broken" if not above else "current_turnover_below_threshold"
    if not above or turnover < policy.minimum_trade_value_won:
        # This is entry eligibility only; it never emits an EXIT for held positions.
        return result("WATCH", reason, high=high, price=price, turnover=turnover,
                      preserve=before.status == "CANDIDATE")
    new = before.status != "CANDIDATE"
    episode = before.episode_id if not new else _content_id("realtime-breakout-episode", {
        "symbol": code, "source_id": source_id, "generation": generation,
        "parameters_hash": parameters_hash, "as_of": cut.isoformat(), "input_refs": refs,
        "reference_high": high,
    })
    return result("CANDIDATE", "observed_high_and_turnover_confirmed" if new else "candidate_condition_unchanged",
                  high=high, price=price, turnover=turnover, episode=episode, new=new)
