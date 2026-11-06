"""Shared immutable strategy configuration and accepted decision contracts.

No evaluator, worker, store, account authority, or current clock is owned here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from functools import cached_property
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from kiwoom_monitor.domain.time_basis import as_kst

BREAKOUT_STRATEGY_ID = "krx_bar_close_breakout"
BREAKOUT_STRATEGY_VERSION = "v1"
PULLBACK_STRATEGY_ID = "krx_pullback_reacceleration"
PULLBACK_STRATEGY_VERSION = "v1"
PULLBACK_FAMILY_ID = f"{PULLBACK_STRATEGY_ID}/{PULLBACK_STRATEGY_VERSION}"
BREAKOUT_FAMILY_ID = f"{BREAKOUT_STRATEGY_ID}/{BREAKOUT_STRATEGY_VERSION}"
POLICY_ID = "leader_breakout/v1"
LEADER_BREAKOUT_FAMILY_ID = POLICY_ID
MINUTE_SHADOW_VERSION = "section12-minute-shadow/v1"
FACTOR_VERSION = "v1"
ROLLING_HIGH_BREAKOUT_ID = "rolling_high_breakout"
PULLBACK_REACCELERATION_ID = "pullback_reacceleration"
RANK_PERSISTENCE_ID = "rank_persistence"
REGULAR_SESSION_EXIT_POLICY = "krx_regular_before_auction/v1"
STRATEGY_FACTOR_VERSIONS = frozenset({
    (ROLLING_HIGH_BREAKOUT_ID, FACTOR_VERSION),
    (PULLBACK_REACCELERATION_ID, FACTOR_VERSION),
    (RANK_PERSISTENCE_ID, FACTOR_VERSION),
})


def shadow_candidate_config_for_spec(settings, spec_id=None):
    """Select the explicit member policy, preserving singleton documents verbatim."""
    if not isinstance(settings, Mapping):
        return None
    if "shadow_candidate_configs" in settings:
        configs = settings["shadow_candidate_configs"]
        return configs.get(spec_id) if isinstance(configs, Mapping) else None
    return settings.get("shadow_candidate_config")


def _require_factor_version(factor_id: str, version: str) -> None:
    key = (str(factor_id), str(version))
    if key not in STRATEGY_FACTOR_VERSIONS:
        raise ValueError(f"unregistered factor: {factor_id}/{version}") from KeyError(key)



def _time(value):
    at = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("leader breakout requires aware timestamps")
    return as_kst(at)


def _id(prefix, value):
    return prefix + ":" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


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


@dataclass(frozen=True)
class RollingHighBreakoutParameters:
    lookback_bars: int
    buffer_bps: int

    def __post_init__(self) -> None:
        if self.lookback_bars <= 0:
            raise ValueError("lookback_bars must be positive")
        if self.buffer_bps < 0:
            raise ValueError("buffer_bps must not be negative")


@dataclass(frozen=True)
class RankPersistenceParameters:
    top_k: int
    window_seconds: int
    max_gap_seconds: int

    def __post_init__(self) -> None:
        if self.top_k <= 0 or self.top_k > 20:
            raise ValueError("top_k must be between 1 and 20")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if self.max_gap_seconds <= 0:
            raise ValueError("max_gap_seconds must be positive")


@dataclass(frozen=True)
class PullbackReaccelerationParameters:
    lookback_bars: int
    minimum_pullback_bps: int
    minimum_reacceleration_bps: int

    def __post_init__(self) -> None:
        if self.lookback_bars < 3:
            raise ValueError("pullback lookback_bars must be at least 3")
        if self.minimum_pullback_bps <= 0:
            raise ValueError("minimum_pullback_bps must be positive")
        if self.minimum_reacceleration_bps < 0:
            raise ValueError("minimum_reacceleration_bps must not be negative")


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
        if self.strategy_version != BREAKOUT_STRATEGY_VERSION:
            raise ValueError(f"unregistered strategy: {BREAKOUT_STRATEGY_ID}/{self.strategy_version}")
        _require_factor_version(ROLLING_HIGH_BREAKOUT_ID, self.rolling_factor_version)
        _require_factor_version(RANK_PERSISTENCE_ID, self.rank_factor_version)
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
class LeaderBreakoutContext:
    symbol: str
    evidence_ref: str
    available_at: str
    valid_until: str
    leader_eligible: bool | None = None
    leader_rule_ref: str | None = None
    reference_price: int | None = None
    structure_rule_ref: str | None = None
    invalidation_price: int | None = None
    hard_stop_price: int | None = None

    def __post_init__(self):
        if len(self.symbol) != 6 or not self.symbol.isdigit() or not self.evidence_ref:
            raise ValueError("symbol and original leader/structure evidence reference required")
        if _time(self.valid_until) <= _time(self.available_at):
            raise ValueError("context validity must follow its availability")
        if self.leader_eligible is not None and type(self.leader_eligible) is not bool:
            raise ValueError("leader eligibility must be explicit boolean or null")
        for name in ("leader_rule_ref", "structure_rule_ref"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(name + " requires an explicit nonempty reference or null")
        for name in ("reference_price", "invalidation_price", "hard_stop_price"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(name + " must be a positive integer or null")


@dataclass(frozen=True)
class LeaderBreakoutPolicy:
    policy_id: str = POLICY_ID
    live_confirmation: str | None = None
    research_confirmation: str | None = None
    minimum_confirmation_observations: int | None = None
    persistence_seconds: int | None = None
    breakout_buffer_bps: int | None = None
    max_data_age_seconds: int | None = None
    signal_valid_seconds: int | None = None
    minimum_turnover_won: int | None = None
    live_turnover_basis: str | None = None
    research_turnover_basis: str | None = None
    research_turnover_lookback_bars: int | None = None
    structural_exit_rule: str | None = None
    hard_risk_profile_ref: str | None = None
    contexts: tuple[LeaderBreakoutContext, ...] = ()
    reference_level: Mapping[str, Any] | None = None

    def __post_init__(self):
        if self.policy_id != POLICY_ID:
            raise ValueError("unknown leader breakout policy")
        if self.reference_level is not None:
            level = dict(self.reference_level)
            if (set(level) != {'type', 'window_seconds', 'exclude_current_interval'}
                    or level['type'] != 'prior_window_high' or level['exclude_current_interval'] is not True
                    or level['window_seconds'] is not None and
                    (type(level['window_seconds']) is not int or not 0 < level['window_seconds'] <= 86400)):
                raise ValueError('explicit prior_window_high reference contract required')
            object.__setattr__(self, 'reference_level', level)
        if self.hard_risk_profile_ref is not None and (not isinstance(self.hard_risk_profile_ref, str)
                or not self.hard_risk_profile_ref.strip()):
            raise ValueError("hard risk profile reference must be nonempty or null")
        for name, allowed in (("live_confirmation", "observed_price_cross"),
                ("research_confirmation", "completed_minute_close_cross"),
                ("live_turnover_basis", "0B_session_cumulative"),
                ("research_turnover_basis", "completed_minute_window"),
                ("structural_exit_rule", "price_below_invalidation")):
            if getattr(self, name) not in (None, allowed):
                raise ValueError("unsupported explicit rule: " + name)
        for name in ("minimum_confirmation_observations", "max_data_age_seconds", "signal_valid_seconds", "minimum_turnover_won",
                     "research_turnover_lookback_bars"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(name + " must be positive or null")
        if self.persistence_seconds is not None and (type(self.persistence_seconds) is not int or self.persistence_seconds < 0):
            raise ValueError("persistence_seconds must be an explicit nonnegative integer or null")
        if self.breakout_buffer_bps is not None and (type(self.breakout_buffer_bps) is not int or self.breakout_buffer_bps < 0):
            raise ValueError("breakout_buffer_bps must be explicit nonnegative integer or null")
        if not isinstance(self.contexts, tuple) or any(not isinstance(c, LeaderBreakoutContext) for c in self.contexts):
            raise ValueError("typed immutable contexts required")
        if len(self.contexts) > 1000 or len({c.symbol for c in self.contexts}) > 200:
            raise ValueError("leader context scope exceeds input budget")
        if len({c.evidence_ref for c in self.contexts}) != len(self.contexts):
            raise ValueError("duplicate evidence references")
        if len({(c.symbol, _time(c.available_at)) for c in self.contexts}) != len(self.contexts):
            raise ValueError("ambiguous leader contexts at the same cut")

    @classmethod
    def from_dict(cls, value):
        raw = dict(value)
        raw["contexts"] = tuple(LeaderBreakoutContext(**dict(c)) for c in raw.get("contexts", ()))
        return cls(**raw)

    def to_dict(self):
        value = asdict(self)
        if self.reference_level is None:
            value.pop('reference_level')
        return value

    @cached_property
    def fingerprint(self):
        return _id("leader-breakout-policy", self.to_dict())

    def missing(self, mode):
        if mode not in {"0B", "1m"}:
            raise ValueError("expected 0B or 1m input mode")
        names = ["max_data_age_seconds", "signal_valid_seconds", "minimum_turnover_won",
                 "structural_exit_rule", "hard_risk_profile_ref", "minimum_confirmation_observations", "persistence_seconds", "breakout_buffer_bps"]
        names += (["live_confirmation", "live_turnover_basis"] if mode == "0B" else
                  ["research_confirmation", "research_turnover_basis", "research_turnover_lookback_bars"])
        missing = [name for name in names if getattr(self, name) in (None, "")]
        if not self.contexts:
            missing.append("leader_structure_context")
        if self.reference_level is not None and self.reference_level['window_seconds'] is None:
            missing.append('reference_level.window_seconds')
        return tuple(missing)


def document_leader_breakout_policy():
    """00 section 10's observation draft, with no invented numeric trading values."""
    return LeaderBreakoutPolicy(live_confirmation='observed_price_cross',
        research_confirmation='completed_minute_close_cross', reference_level={
            'type':'prior_window_high', 'window_seconds':None, 'exclude_current_interval':True})


@dataclass(frozen=True)
class LeaderBreakoutExecutionContract:
    """Operator-supplied execution terms; absent terms never acquire defaults."""
    version: str = "leader_breakout_execution/v1"
    sizing_mode: str | None = None
    quantity: int | None = None
    capital_won: int | None = None
    order_type: str | None = None
    session_exit_policy: str | None = None
    session_exit_buffer_seconds: int | None = None
    exit_quantity_rule: str | None = None
    research_exit_observation: str | None = None
    leader_rule_ref: str | None = None
    structure_rule_ref: str | None = None
    hard_risk_profile: Mapping[str, Any] | None = None

    def __post_init__(self):
        if self.version != "leader_breakout_execution/v1" and not (
                self.version == "leader_breakout_execution/v2"
                and isinstance(self, LeaderBreakoutStagedExecutionContract)) and not (
                self.version == 'leader_breakout_execution/v3'
                and isinstance(self, LeaderBreakoutReentryExecutionContract)):
            raise ValueError("unsupported leader execution contract")
        for name, allowed in (("sizing_mode", "fixed_quantity"), ("order_type", "LIMIT"),
                ("exit_quantity_rule", "all_owned"), ("research_exit_observation", "completed_minute_close")):
            if getattr(self, name) not in (None, allowed):
                raise ValueError("unsupported explicit execution term: " + name)
        for name in ("quantity", "capital_won"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(name + " must be positive or null")
        if self.session_exit_policy not in (None, "hold_across_sessions", REGULAR_SESSION_EXIT_POLICY):
            raise ValueError("unsupported explicit leader session exit policy")
        if self.session_exit_buffer_seconds is not None:
            if self.session_exit_policy == REGULAR_SESSION_EXIT_POLICY:
                validate_session_exit_policy(self.session_exit_policy, self.session_exit_buffer_seconds)
            elif type(self.session_exit_buffer_seconds) is not int or self.session_exit_buffer_seconds != 0:
                raise ValueError("hold-across-sessions requires an explicit zero exit buffer")
        for name in ("leader_rule_ref", "structure_rule_ref"):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or not value.strip()):
                raise ValueError("explicit rule reference or null required")
        if self.hard_risk_profile is not None:
            raw = dict(self.hard_risk_profile)
            if set(raw) != {"version", "profile_ref", "rule"} or raw["version"] != "leader_hard_risk/v1" or raw["rule"] != "pinned_absolute_price" or not raw["profile_ref"]:
                raise ValueError("explicit pinned absolute-price hard risk document required")
            object.__setattr__(self, "hard_risk_profile", raw)

    def to_dict(self):
        return asdict(self)

    @property
    def fingerprint(self):
        return _id("leader-execution/" + self.version.rsplit("/", 1)[1], self.to_dict())

    def missing(self, mode):
        names = [f.name for f in fields(self) if f.name != "version" and
                 (mode == "1m" or f.name != "research_exit_observation")]
        return tuple("execution." + name for name in names if getattr(self, name) is None)


@dataclass(frozen=True)
class LeaderPositionStep:
    """A declared research/operator hypothesis, with no implicit size or level."""
    step_id: str
    action: str
    trigger: str
    reference_price: int
    quantity: int
    reason_ref: str

    def __post_init__(self):
        if any(type(getattr(self, name)) is not str or not getattr(self, name).strip()
               for name in ('step_id', 'reason_ref')):
            raise ValueError('leader position step requires its own identity and reason')
        allowed = {'ADD': {'observed_cross_above'},
                   'REDUCE': {'price_at_or_above', 'price_at_or_below'}}
        if (type(self.action) is not str or type(self.trigger) is not str
                or self.action not in allowed or self.trigger not in allowed[self.action]):
            raise ValueError('unsupported explicit leader position step')
        if any(type(getattr(self, name)) is not int or getattr(self, name) <= 0
               for name in ('reference_price', 'quantity')):
            raise ValueError('leader position step requires positive explicit price and quantity')


@dataclass(frozen=True)
class LeaderBreakoutStagedExecutionContract(LeaderBreakoutExecutionContract):
    """Opt-in actions within cumulative actual BUY notional; reductions do not refill it.

    V1 serialization and behavior stay frozen. Levels, quantities and reasons
    are supplied by a declared hypothesis, never populated as trading defaults.
    """
    version: str = 'leader_breakout_execution/v2'
    position_steps: tuple[LeaderPositionStep, ...] | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.position_steps is not None and (
                type(self.position_steps) is not tuple or not self.position_steps
                or any(type(step) is not LeaderPositionStep for step in self.position_steps)
                or len({step.step_id for step in self.position_steps}) != len(self.position_steps)):
            raise ValueError('explicit unique ordered leader position steps required')


LEADER_REENTRY_RULE = 'new_independent_setup_after_confirmed_flat/v1'


@dataclass(frozen=True)
class LeaderBreakoutReentryExecutionContract(LeaderBreakoutStagedExecutionContract):
    """Opt in to a fresh independently evidenced setup after actual full exit."""
    version: str = 'leader_breakout_execution/v3'
    reentry_rule: str | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.reentry_rule != LEADER_REENTRY_RULE:
            raise ValueError('v3 requires the explicit independent-setup reentry rule')


def leader_execution_contract_from_dict(document):
    raw = dict(document)
    if raw.get('version') in {'leader_breakout_execution/v2', 'leader_breakout_execution/v3'}:
        steps = raw.get('position_steps')
        if steps is not None:
            if not isinstance(steps, (tuple, list)) or any(not isinstance(step, Mapping) for step in steps):
                raise ValueError('leader position steps must be explicit objects')
            raw['position_steps'] = tuple(LeaderPositionStep(**step) for step in steps)
        cls = (LeaderBreakoutReentryExecutionContract if raw['version'] == 'leader_breakout_execution/v3'
               else LeaderBreakoutStagedExecutionContract)
        return cls(**raw)
    return LeaderBreakoutExecutionContract(**raw)


@dataclass(frozen=True)
class LeaderBreakoutConfig(LeaderBreakoutPolicy):
    """Registered Family config. Original orderless policy serialization is unchanged."""
    execution_contract: LeaderBreakoutExecutionContract | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.execution_contract is not None and not isinstance(self.execution_contract, LeaderBreakoutExecutionContract):
            raise ValueError("typed leader execution contract required")

    @classmethod
    def from_dict(cls, value):
        raw = dict(value)
        contract = raw.pop("execution_contract", None)
        policy = LeaderBreakoutPolicy.from_dict(raw)
        return cls(**{f.name: getattr(policy, f.name) for f in fields(LeaderBreakoutPolicy)},
                   execution_contract=leader_execution_contract_from_dict(contract) if contract is not None else None)

    def to_dict(self):
        # Canonical JSON arrays, including nested contexts, roundtrip through files/API.
        value = asdict(self)
        if self.reference_level is None:
            value.pop('reference_level')
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))

    @property
    def policy(self):
        return LeaderBreakoutPolicy(**{f.name: getattr(self, f.name) for f in fields(LeaderBreakoutPolicy)})

    @property
    def rules_document(self):
        return {k: v for k, v in self.policy.to_dict().items() if k != "contexts"}

    @property
    def rules_hash(self):
        return _id("leader-rules/v1", self.rules_document)

    def execution_missing(self, mode, *, require_contexts=True):
        reasons = list(self.policy.missing(mode))
        if self.reference_level is not None:
            reasons.append('document_baseline_observation_only')
        if not require_contexts:
            reasons = [r for r in reasons if r != "leader_structure_context"]
        contract = self.execution_contract
        if contract is None:
            return tuple(reasons + ["execution_contract"])
        reasons += list(contract.missing(mode))
        risk = contract.hard_risk_profile
        if risk is not None and risk["profile_ref"] != self.hard_risk_profile_ref:
            reasons.append("execution.hard_risk_profile_mismatch")
        for context in self.contexts:
            if context.leader_rule_ref != contract.leader_rule_ref or context.structure_rule_ref != contract.structure_rule_ref:
                reasons.append("execution.context_rule_mismatch")
        return tuple(dict.fromkeys(reasons))

    @property
    def strategy_version(self):
        return "v1"

    @property
    def session_exit_policy(self):
        value = self.execution_contract.session_exit_policy if self.execution_contract else None
        return "" if value in (None, "hold_across_sessions") else value

    @property
    def session_exit_buffer_seconds(self):
        return self.execution_contract.session_exit_buffer_seconds if self.execution_contract else None

    @property
    def quantity(self):
        return self.execution_contract.quantity if self.execution_contract else None

    @property
    def capital_won(self):
        return self.execution_contract.capital_won if self.execution_contract else None


@dataclass(frozen=True)
class PullbackReaccelerationConfig:
    strategy_version: str
    pullback_factor_version: str
    rank_factor_version: str
    lookback_bars: int
    minimum_pullback_bps: int
    minimum_reacceleration_bps: int
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
        if self.strategy_version != PULLBACK_STRATEGY_VERSION:
            raise ValueError(f"unregistered strategy: {PULLBACK_STRATEGY_ID}/{self.strategy_version}")
        _require_factor_version(PULLBACK_REACCELERATION_ID, self.pullback_factor_version)
        _require_factor_version(RANK_PERSISTENCE_ID, self.rank_factor_version)
        PullbackReaccelerationParameters(
            self.lookback_bars, self.minimum_pullback_bps,
            self.minimum_reacceleration_bps,
        )
        if self.rank_persistence_enabled:
            if None in (
                self.rank_top_k, self.rank_window_seconds, self.rank_max_gap_seconds,
                self.rank_min_residency_seconds,
            ):
                raise ValueError("enabled rank persistence requires all rank parameters")
            RankPersistenceParameters(
                int(self.rank_top_k), int(self.rank_window_seconds),
                int(self.rank_max_gap_seconds),
            )
            if int(self.rank_min_residency_seconds) < 0:
                raise ValueError("rank_min_residency_seconds must not be negative")
        elif self.rank_persistence_required:
            raise ValueError("disabled rank persistence cannot be required")
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


PULLBACK_CONTRACT: Mapping[str, Any] = {'required_inputs': ('minute_bar/KRX/closed/complete', 'top20_membership/as_available'), 'state_contract': 'flat/candidate/open/cooldown', 'entry_contract': 'pullback_reacceleration_then_optional_rank_filter', 'exit_contract': 'stop_loss_or_target_or_max_hold', 'size_contract': 'fixed_quantity_with_capital_limit', 'execution_environments': ('replay', 'simulation')}


RESEARCH_FAMILY_LABELS: Mapping[str, str] = MappingProxyType({LEADER_BREAKOUT_FAMILY_ID: '주도주 구조 돌파 · 실시간 0B / 연구 분봉', BREAKOUT_FAMILY_ID: '완료봉 고가 돌파 v1 · 기존 비교 실험', PULLBACK_FAMILY_ID: '눌림 재가속 v1 · 기존 비교 실험'})


RESEARCH_DIRECTION_NOTICE = '연구 방향: 사용자 주도주 돌파 구상(00 AI 인텔리전스, 미완성 초안). 기존 완료봉 돌파·눌림 실험은 이 전략의 구현이나 검증 결과가 아닙니다. 주도주 구조 돌파는 실시간 0B·분봉 연구 경로를 사용하며, 미정 필수 조건은 진입을 차단합니다.'


@dataclass(frozen=True)
class ResearchFamilyContract:
    family_id: str
    config_type: type
    factor_ids: tuple[str, ...]
    searchable_parameters: frozenset[str]
    contract: Mapping[str, Any]


FAMILY_CONTRACTS: Mapping[str, ResearchFamilyContract] = MappingProxyType({LEADER_BREAKOUT_FAMILY_ID: ResearchFamilyContract(family_id=LEADER_BREAKOUT_FAMILY_ID, config_type=LeaderBreakoutConfig, factor_ids=('leader_structure/v1',), searchable_parameters=frozenset({'minimum_confirmation_observations', 'persistence_seconds', 'breakout_buffer_bps', 'max_data_age_seconds', 'signal_valid_seconds', 'minimum_turnover_won', 'research_turnover_lookback_bars'}), contract={'required_inputs': ('minute_bar/KRX/final_completed', 'explicit_leader_structure_evidence'), 'live_inputs': ('kiwoom-websocket-0B/KRX', 'explicit_current_leader_structure_evidence'), 'state_contract': 'observation_separate_from_fill_owned_position', 'entry_contract': 'explicit_leader_structure_cross', 'exit_contract': 'pinned_absolute_hard_stop_or_structure_invalidation', 'size_contract': 'explicit_execution_contract_only', 'execution_environments': ('replay', 'simulation', 'mock_0B')}), BREAKOUT_FAMILY_ID: ResearchFamilyContract(family_id=BREAKOUT_FAMILY_ID, config_type=BreakoutStrategyConfig, factor_ids=('rolling_high_breakout/v1', 'rank_persistence/v1'), searchable_parameters=frozenset({'lookback_bars', 'buffer_bps', 'rank_top_k', 'rank_window_seconds', 'rank_max_gap_seconds', 'rank_min_residency_seconds', 'stop_loss_bps', 'target_bps', 'max_hold_minutes', 'signal_valid_seconds', 'cooldown_seconds'}), contract={'required_inputs': ('minute_bar/KRX/closed/complete', 'top20_membership/as_available'), 'state_contract': 'flat/candidate/open/cooldown', 'entry_contract': 'rolling_high_breakout_then_optional_rank_filter', 'exit_contract': 'stop_loss_or_target_or_max_hold', 'size_contract': 'fixed_quantity_with_capital_limit', 'execution_environments': ('replay', 'simulation')}), PULLBACK_FAMILY_ID: ResearchFamilyContract(family_id=PULLBACK_FAMILY_ID, config_type=PullbackReaccelerationConfig, factor_ids=('pullback_reacceleration/v1', 'rank_persistence/v1'), searchable_parameters=frozenset({'lookback_bars', 'minimum_pullback_bps', 'minimum_reacceleration_bps', 'rank_top_k', 'rank_window_seconds', 'rank_max_gap_seconds', 'rank_min_residency_seconds', 'stop_loss_bps', 'target_bps', 'max_hold_minutes', 'signal_valid_seconds', 'cooldown_seconds'}), contract=PULLBACK_CONTRACT)})


def get_research_family(family_id: str) -> ResearchFamilyContract:
    try:
        return FAMILY_CONTRACTS[str(family_id)]
    except KeyError as exc:
        raise ValueError(f"unregistered research family: {family_id}") from exc


def parse_strategy_config(family_id: str, value: Mapping[str, Any]) -> Any:
    definition = get_research_family(family_id)
    if definition.config_type is LeaderBreakoutConfig:
        return LeaderBreakoutConfig.from_dict(value)
    return definition.config_type(**dict(value))


def shadow_monitor_id_for_config(
    family_id: str, config: object, session_profile: str,
) -> str:
    """Return the stable NAS shadow owner for one registered family configuration."""
    definition = get_research_family(family_id)
    if not isinstance(config, definition.config_type) or not hasattr(config, "to_dict"):
        raise ValueError("shadow monitor config does not match its registered family")
    config_document = config.to_dict()
    identity_document = (
        config_document
        if session_profile == "krx-regular/v1"
        else {"strategy": config_document, "session_profile": session_profile}
    )
    encoded = json.dumps(
        identity_document, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    strategy_id, strategy_version = family_id.rsplit("/", 1)
    return (
        f"shadow:{strategy_id}:{strategy_version}:"
        f"{hashlib.sha256(encoded).hexdigest()[:16]}"
    )
