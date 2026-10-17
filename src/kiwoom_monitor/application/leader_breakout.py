"""Explicit leader/structure evidence and one breakout rule, with 0B/1m adapters.

This module does not select a leader from TOP20 rank, invent a reference level,
size an order, or simulate fills. Missing policy/evidence blocks new candidates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timedelta
import hashlib
import json
from functools import cached_property
from typing import Any, Mapping

from kiwoom_monitor.domain.time_basis import as_kst

POLICY_ID = "leader_breakout/v1"


def _time(value):
    at = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("leader breakout requires aware timestamps")
    return as_kst(at)


def _id(prefix, value):
    return prefix + ":" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


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


def document_baseline_missing(policy,mode):
    """00's unresolved checks, separate from the full v1 execution contract."""
    names=['max_data_age_seconds','minimum_confirmation_observations','persistence_seconds',
        'minimum_turnover_won','structural_exit_rule','hard_risk_profile_ref']
    names += ['live_turnover_basis'] if mode=='0B' else ['research_turnover_basis','research_turnover_lookback_bars']
    missing=[name for name in names if getattr(policy,name) is None]
    if policy.reference_level is None or policy.reference_level['window_seconds'] is None:
        missing.append('reference_level.window_seconds')
    if not any(c.leader_eligible is not None and c.leader_rule_ref for c in policy.contexts):
        missing.append('leader_selection_evidence')
    return tuple(missing)


def document_breakout_reference(policy, rows, *, symbol, decision_time, mode):
    """Prior completed native minute highs; source receipts remain live cut fences.

    This is price evidence, not leader eligibility, invalidation, risk or an order.
    Explicit subminute windows cannot be reconstructed from minute history.
    """
    cut = _time(decision_time)
    end = cut.replace(second=0, microsecond=0) if mode == '0B' else cut-timedelta(minutes=1)
    level = policy.reference_level
    result = {'version':'document-breakout-reference/v1', 'symbol':symbol,
        'reference_type':'prior_window_high', 'reference_resolution':'1m',
        'exclude_current_interval':True, 'window_end':end.isoformat(),
        'reference_price':None, 'input_refs':[], 'input_count':0, 'input_refs_hash':None,
        'source_durability':'not_verified', 'reasons':[], 'order_authorized':False}
    seconds = level['window_seconds'] if level else None
    if seconds is None:
        result['reasons'].append('unconfigured:reference_level.window_seconds')
    elif seconds % 60:
        result['reasons'].append('subminute_reference_unavailable_from_minutes')
    else:
        start = end-timedelta(seconds=seconds)
        result['window_start'] = start.isoformat()
        selected = {}
        for row in rows:
            if row.get('code') != symbol or row.get('market') != 'KRX':
                continue
            try:
                at = _time(row['trading_date']+'T'+row['minute']+'+09:00')
            except (ValueError,TypeError,KeyError):
                continue
            if at.second or at.microsecond:
                continue
            if not start <= at < end or row.get('completeness') != 'complete':
                continue
            query = str(row.get('source','')).startswith('kiwoom-ka10080')
            if row.get('value_kind') != 'actual' and not (query and row.get('value_kind') == 'estimated'):
                continue
            available = row.get('available_at')
            if mode == '0B' and (type(available) not in (int,float) or available > cut.timestamp()):
                continue
            if type(row.get('high')) is not int or row['high'] <= 0:
                continue
            rank = (query, float(available or 0))
            if at not in selected or selected[at][0] < rank:
                selected[at] = (rank, row)
        if len(selected) != seconds//60:
            result['reasons'].append('prior_completed_minute_window_incomplete')
        else:
            ordered = [selected[start+timedelta(minutes=i)][1] for i in range(seconds//60)]
            result['reference_price'] = max(row['high'] for row in ordered)
            refs = [_id('document-reference-minute', dict(row)) for row in ordered]
            peak = next(i for i,row in enumerate(ordered) if row['high'] == result['reference_price'])
            result['input_refs'] = list(dict.fromkeys((refs[0],refs[-1],refs[peak])))
            result['input_count'] = len(refs)
            result['input_refs_hash'] = _id('document-reference-inputs', refs)
    result['quality'] = 'COMPLETE' if not result['reasons'] else 'UNAVAILABLE'
    result['reference_ref'] = _id('document-breakout-reference', result)
    return result


def document_breakout_observation(policy, reference, *, price, observed_at, available_at, input_ref,
                                  previous=None, source, mode, turnover_won=None):
    """Report independently observed checks while execution terms remain unknown."""
    observed, available = _time(observed_at), _time(available_at)
    reasons = list(reference['reasons'])
    valid_price = type(price) is int and price > 0 and observed <= available
    if not valid_price:
        reasons.append('price_unavailable_or_from_future')
    comparable = (previous is not None and previous['source'] == source
        and _time(previous['observed_at']) <= observed and _time(previous['available_at']) < available)
    if previous is not None and not comparable:
        reasons.append('source_or_delivery_discontinuity')
    cross = None
    leader = next((c for c in policy.contexts if c.symbol==reference['symbol'] and c.leader_rule_ref
        and _time(c.available_at)<=available<_time(c.valid_until)),None)
    missing = list(document_baseline_missing(policy,mode))
    if leader is None and 'leader_selection_evidence' not in missing:
        missing.append('leader_selection_evidence')
    level = reference['reference_price']
    if valid_price and level is not None and comparable:
        cross = previous['price'] <= level < price
    if policy.max_data_age_seconds is None:
        freshness = 'UNCONFIGURED'
    else:
        freshness = 'FRESH' if valid_price and (available-observed).total_seconds() <= policy.max_data_age_seconds else 'STALE'
        if freshness == 'STALE':
            reasons.append('input_stale')
            cross = None
    facts = {'version':'document-breakout-observation/v1', 'symbol':reference['symbol'],
        'input_mode':mode, 'source':source, 'observed_at':observed.isoformat(),
        'available_at':available.isoformat(), 'input_ref':input_ref, 'price':price,
        'reference':reference, 'observed_price_cross':cross, 'freshness':freshness,
        'turnover_won':turnover_won, 'turnover_basis':'0B_session_cumulative' if mode == '0B' else policy.research_turnover_basis,
        'configured_turnover_basis':policy.live_turnover_basis if mode == '0B' else policy.research_turnover_basis,
        'leader_eligible':leader.leader_eligible if leader else None, 'confirmation_verified':False,
        'configuration_missing':missing, 'reasons':reasons,
        'state':('DATA_BLOCKED' if reasons else 'CROSSED_UNCONFIRMED' if cross else
                 'ABOVE_REFERENCE' if valid_price and level is not None and price>level else 'WATCH'),
        'proposal':'WAIT', 'order_authorized':False}
    facts['observation_ref'] = _id('document-breakout-observation', facts)
    return facts


@dataclass(frozen=True)
class LeaderBreakoutState:
    source: str = ""
    context_ref: str = ""
    last_observed_at: str = ""
    last_available_at: str = ""
    last_price: int | None = None
    episode_id: str = ""
    crossed_at: str = ""
    confirmation_count: int = 0


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
        if self.version != "leader_breakout_execution/v1":
            raise ValueError("unsupported leader execution contract")
        for name, allowed in (("sizing_mode", "fixed_quantity"), ("order_type", "LIMIT"),
                ("exit_quantity_rule", "all_owned"), ("research_exit_observation", "completed_minute_close")):
            if getattr(self, name) not in (None, allowed):
                raise ValueError("unsupported explicit execution term: " + name)
        for name in ("quantity", "capital_won"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(name + " must be positive or null")
        from .breakout_strategy import REGULAR_SESSION_EXIT_POLICY, validate_session_exit_policy
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
        return _id("leader-execution/v1", self.to_dict())

    def missing(self, mode):
        names = [f.name for f in fields(self) if f.name != "version" and
                 (mode == "1m" or f.name != "research_exit_observation")]
        return tuple("execution." + name for name in names if getattr(self, name) is None)


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
                   execution_contract=LeaderBreakoutExecutionContract(**contract) if contract is not None else None)

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


def leader_session_entry_rejection(config, at):
    """Explicit session fence without importing the old maximum-hold strategy rule."""
    if not config.session_exit_policy:
        return ""
    from .breakout_strategy import regular_session_time_rejection, regular_session_fill_deadline
    reason = regular_session_time_rejection(at)
    if reason:
        return reason
    required = config.signal_valid_seconds + config.session_exit_buffer_seconds
    return "session_entry_budget_exhausted" if at + timedelta(seconds=required) >= regular_session_fill_deadline(at) else ""


def evaluate_leader_breakout(*, policy, symbol, price, turnover_won, observed_at,
        available_at, decision_time, input_ref, mode, source, state=None, position_open=False,
        position_context=None):
    """Cross a supplied structure with supplied leader evidence; never authorize orders."""
    if mode not in {"0B", "1m"} or not input_ref or not source:
        raise ValueError("explicit source, input reference and 0B/1m mode required")
    before = state or LeaderBreakoutState()
    observed, available, cut = map(_time, (observed_at, available_at, decision_time))
    missing = list(policy.missing(mode))
    contexts = [c for c in policy.contexts if c.symbol == symbol and _time(c.available_at) <= cut < _time(c.valid_until)]
    context = max(contexts, key=lambda c: _time(c.available_at), default=None)
    if position_open and position_context is not None:
        if not isinstance(position_context, LeaderBreakoutContext) or position_context.symbol != symbol:
            raise ValueError("position context must be the pinned entry evidence")
        context = position_context
    if context is None:
        missing.append("current_leader_structure_context")
    else:
        missing += [name for name in ("leader_eligible", "leader_rule_ref", "reference_price",
                    "structure_rule_ref", "invalidation_price", "hard_stop_price")
                    if getattr(context, name) in (None, "")]
    reasons = ["unconfigured:" + name for name in dict.fromkeys(missing)]
    if policy.reference_level is not None:
        reasons.append('document_baseline_observation_only')
    if type(price) is not int or price <= 0:
        reasons.append("price_unavailable")
    if type(turnover_won) is not int or turnover_won < 0:
        reasons.append("turnover_unavailable")
    if observed > available or available > cut or observed > cut:
        reasons.append("input_from_future")
    if policy.max_data_age_seconds is not None and max((cut-observed).total_seconds(),
            (cut-available).total_seconds()) > policy.max_data_age_seconds:
        reasons.append("input_stale")
    if before.last_observed_at and observed < _time(before.last_observed_at):
        reasons.append("out_of_order_observation")
    if before.last_available_at and available <= _time(before.last_available_at):
        reasons.append("duplicate_or_old_delivery")
    action, status, new_candidate = "WAIT", "WATCH", False
    after = before
    # A configured hard price boundary protects an existing position even when
    # entry policy fields are incomplete. Future/stale prices never trigger it.
    invalid_price = any(r in reasons for r in ("price_unavailable", "input_from_future", "input_stale",
                                             "out_of_order_observation", "duplicate_or_old_delivery"))
    if (position_open and context and context.hard_stop_price is not None and policy.max_data_age_seconds is not None
            and not invalid_price and price <= context.hard_stop_price):
        action, status = "EXIT", "HARD_EXIT"
        reasons.append("explicit_hard_stop_reached")
    elif (position_open and context and context.invalidation_price is not None
            and policy.structural_exit_rule == "price_below_invalidation"
            and policy.max_data_age_seconds is not None and not invalid_price):
        action, status = ("EXIT", "INVALIDATED") if price < context.invalidation_price else ("HOLD", "HOLD")
        reasons.append("explicit_structure_invalidated" if action == "EXIT" else "explicit_structure_intact")
    elif reasons:
        status = "BLOCKED"
        # A discontinuity must not leave a crossing bridge to an older source.
        if (any(reason in reasons for reason in ("input_stale", "input_from_future", "out_of_order_observation"))
                or "out_of_order_observation" not in reasons and "duplicate_or_old_delivery" not in reasons):
            after = LeaderBreakoutState()
    else:
        same = before.source == source and before.context_ref == context.evidence_ref
        episode = before.episode_id if same else ""
        crossed = before.crossed_at if same else ""
        count = before.confirmation_count if same else 0
        if price < context.invalidation_price:
            action, status = ("EXIT" if position_open else "WAIT"), "INVALIDATED"
            episode = ""
            crossed, count = "", 0
            reasons.append("explicit_structure_invalidated")
        elif position_open:
            action, status = "HOLD", "HOLD"
            reasons.append("explicit_structure_intact")
        elif not context.leader_eligible:
            reasons.append("leader_not_eligible")
            episode = ""
            crossed, count = "", 0
        elif turnover_won < policy.minimum_turnover_won:
            reasons.append("turnover_below_explicit_threshold")
            crossed, count = "", 0
        elif episode:
            status = "CANDIDATE"
            reasons.append("existing_episode")
        else:
            trigger = context.reference_price*(10_000+policy.breakout_buffer_bps)
            if price*10_000 <= trigger:
                crossed, count = "", 0
            elif same and before.last_price is not None and before.last_price*10_000 <= trigger:
                crossed, count = observed.isoformat(), 1
            elif crossed:
                count += 1
            if crossed and count >= policy.minimum_confirmation_observations and (observed-_time(crossed)).total_seconds() >= policy.persistence_seconds:
                episode = _id("leader-breakout-episode", [policy.fingerprint, symbol, context.evidence_ref, source, input_ref])
                action, status, new_candidate = "ENTER", "CANDIDATE", True
                reasons.append("leader_structure_cross_confirmed")
                crossed, count = "", 0
            else:
                status = "CONFIRMING" if crossed else "WATCH"
                reasons.append("awaiting_explicit_confirmation" if crossed else "awaiting_observed_cross")
        after = LeaderBreakoutState(source, context.evidence_ref, observed.isoformat(), available.isoformat(), price, episode, crossed, count)
    expiry = cut
    if context and policy.signal_valid_seconds and policy.max_data_age_seconds:
        expiry = min(_time(context.valid_until), available+timedelta(seconds=policy.signal_valid_seconds),
                     observed+timedelta(seconds=policy.max_data_age_seconds))
    body = {"policy_id": POLICY_ID, "input_mode": mode, "symbol": symbol, "status": status,
        "proposal": action, "decided_at": cut.isoformat(), "observed_at": observed.isoformat(),
        "available_at": available.isoformat(), "valid_until": expiry.isoformat(), "input_ref": input_ref,
        "context_ref": context.evidence_ref if context else None, "reference_price": context.reference_price if context else None,
        "signal_reference_price": price, "turnover_won": turnover_won, "reasons": reasons,
        "new_candidate": new_candidate, "episode_id": after.episode_id, "order_authorized": False,
        "state_before": asdict(before), "state_after": asdict(after)}
    return {"decision_id": _id("leader-breakout-decision", [policy.fingerprint, body]), **body}, after


def evaluate_leader_breakout_trade(policy, payload, *, received_at, source, state=None, position_open=False,
                                 decision_time=None, position_context=None):
    """Consume the actual 0B payload, not a second bar or a newer cached quote."""
    cut = _time(received_at)
    source = source + ":" + cut.date().isoformat()
    if payload.get("market", "KRX") != "KRX":
        raise ValueError("leader breakout currently requires explicit KRX 0B")
    clock = str(payload.get("trade_time", ""))
    if len(clock) != 6 or not clock.isdigit():
        raise ValueError("0B exchange clock required")
    observed = cut.replace(hour=int(clock[:2]), minute=int(clock[2:4]), second=int(clock[4:]), microsecond=0)
    # 0B FID14 is million KRW, as in the existing minute_trade_value consumer.
    raw_value = payload.get("cumulative_trade_value")
    turnover = abs(raw_value)*1_000_000 if type(raw_value) is int else None
    raw_price = payload.get("current_price")
    price = abs(raw_price) if type(raw_price) is int else None
    ref = _id("0B", [source, cut.isoformat(), dict(payload)])
    return evaluate_leader_breakout(policy=policy, symbol=str(payload.get("code", "")), price=price,
        turnover_won=turnover, observed_at=observed, available_at=cut, decision_time=decision_time or cut,
        input_ref=ref, mode="0B", source=source, state=state, position_open=position_open,
        position_context=position_context)


def final_leader_minute_observations(observations, *, session_profile, checkpoint=lambda: None):
    """Select final corrected bars once; preserve original source/receipt evidence."""
    from kiwoom_monitor.application.research_replay import research_bar_allowed
    latest = {}
    for row in observations:
        checkpoint()
        if row.get("kind") != "minute_bar" or row.get("venue") != "KRX":
            continue
        p = row.get("payload", {})
        if not isinstance(p, Mapping):
            raise ValueError("minute payload must be an object")
        if (not p.get("window_closed") or p.get("capture_quality") != "complete"
                or row.get("completeness") != "complete" or p.get("market") != "KRX"):
            continue
        query = str(row.get("source", "")).startswith("kiwoom-ka10080")
        if row.get("value_kind") != "actual" and not (query and row.get("value_kind") == "estimated"):
            continue
        if not {"bar_start", "bar_end", "code", "close"}.issubset(p) or not row.get("revision_id"):
            raise ValueError("closed minute is missing original identity/price/time")
        start, end = _time(p["bar_start"]), _time(p["bar_end"])
        if end-start != timedelta(minutes=1) or not research_bar_allowed(start, end, venue="KRX", session_profile=session_profile,
                declared_session=p.get("session"), declared_phase=p.get("phase"), declared_schedule_version=p.get("schedule_version")):
            continue
        key = (str(p["code"]), start.isoformat())
        order = (query, int(row.get("accepted_sequence", 0)))
        if key not in latest or order > latest[key][0]:
            latest[key] = (order, row)
    return tuple(row for _, row in sorted(latest.values(),
        key=lambda item: (_time(item[1]["payload"]["bar_end"]), item[1]["payload"]["code"])))


def leader_research_rows(observations, *, session_profile, checkpoint=lambda: None):
    """Explicit final-bar analysis clock projection, never a persisted receipt rewrite."""
    selected = final_leader_minute_observations(observations, session_profile=session_profile, checkpoint=checkpoint)
    return tuple({**row, "available_at": row["payload"]["bar_end"],
        "subject": row.get("subject") or row["payload"]["code"] + ":KRX",
        "observation_key": row.get("observation_key") or row["payload"]["bar_start"],
        "leader_original_receipt": row.get("available_at"),
        "leader_availability_basis": "final_completed_minute"} for row in selected)


def validate_leader_shadow_event(config, event):
    """Check original actual-0B evidence, independently of an execution quantity."""
    if (event.get("evidence_version") != "leader_shadow_evidence/v1" or event.get("rules_hash") != config.rules_hash
            or event.get("strategy_id") != "leader_breakout" or event.get("strategy_version") != "v1"
            or event.get("transition") != "observed_cross" or event.get("quantity") is not None
            or event.get("order_authorized") is not False):
        raise ValueError("leader Shadow identity mismatch")
    policy = LeaderBreakoutPolicy.from_dict(event["policy"])
    if event.get("run_id") != _id("leader-breakout-monitor", policy.to_dict()):
        raise ValueError("leader Shadow monitor identity mismatch")
    actual = LeaderBreakoutConfig.from_dict(policy.to_dict())
    if actual.rules_hash != config.rules_hash:
        raise ValueError("leader Shadow rules differ from final candidate")
    observation, decision = event["observation"], event["decision"]
    if observation.get("approved") is not True or not observation.get("source"):
        raise ValueError("leader Shadow requires acknowledged actual 0B")
    recomputed, _ = evaluate_leader_breakout_trade(policy, observation["payload"],
        received_at=_time(observation["received_at"]), source=observation["source"],
        state=LeaderBreakoutState(**decision["state_before"]))
    canonical = lambda v: json.dumps(v, sort_keys=True, separators=(",", ":"), allow_nan=False)
    context = next((c for c in policy.contexts if c.evidence_ref == decision.get("context_ref")), None)
    contract = config.execution_contract
    if (canonical(recomputed) != canonical(decision) or not decision["new_candidate"]
            or decision["input_mode"] != "0B" or event["event_id"] != decision["episode_id"]
            or event["decision_id"] != decision["decision_id"] or event["available_at"] != decision["available_at"]
            or event["signal_reference_price"] != decision["signal_reference_price"] or context is None
            or event["context_hash"] != _id("leader-context/v1", asdict(context))
            or context.leader_rule_ref != contract.leader_rule_ref or context.structure_rule_ref != contract.structure_rule_ref):
        raise ValueError("leader Shadow observation/context proof mismatch")
    if _time(decision["valid_until"]) <= _time(decision["decided_at"]):
        raise ValueError("leader Shadow signal was not valid when decided")


def leader_position_proof(config, *, spec_id, package_hash, account_ref, run_id, decision, context):
    from .mock_automation_execution import mock_automation_intent_id
    body = {"version": "leader_position_context/v1", "spec_id": spec_id, "package_hash": package_hash,
        "account_ref": account_ref, "run_id": run_id, "decision_id": decision.decision_id,
        "entry_intent_id": mock_automation_intent_id(spec_id, decision.decision_id),
        "rules_hash": config.rules_hash, "execution_contract_hash": config.execution_contract.fingerprint,
        "context": asdict(context), "context_hash": _id("leader-context/v1", asdict(context)),
        "entry_decided_at": decision.decided_at}
    return {**body, "proof_id": _id("leader-position-proof/v1", body)}


def validate_leader_position_proof(proof, config, spec, run_id):
    from .mock_automation_execution import mock_automation_intent_id
    body = {k: v for k, v in proof.items() if k != "proof_id"}
    context = LeaderBreakoutContext(**proof["context"])
    contract = config.execution_contract
    if (proof.get("version") != "leader_position_context/v1" or proof.get("proof_id") != _id("leader-position-proof/v1", body)
            or proof.get("spec_id") != spec.spec_id or proof.get("package_hash") != spec.candidate_package_hash
            or proof.get("account_ref") != spec.account_scope.account_ref or proof.get("run_id") != run_id
            or proof.get("entry_intent_id") != mock_automation_intent_id(spec.spec_id, proof["decision_id"])
            or proof.get("rules_hash") != config.rules_hash or contract is None
            or proof.get("execution_contract_hash") != contract.fingerprint
            or proof.get("context_hash") != _id("leader-context/v1", asdict(context))
            or context.hard_stop_price is None or context.invalidation_price is None
            or context.leader_rule_ref != contract.leader_rule_ref or context.structure_rule_ref != contract.structure_rule_ref
            or not _time(context.available_at) <= _time(proof["entry_decided_at"]) < _time(context.valid_until)):
        raise ValueError("leader pinned position context proof mismatch")
    return context


def leader_evaluation(*, run_id, config, raw, state, context=None):
    """Translate a pure proposal to the existing sized execution DTO only when ready."""
    from .breakout_strategy import FeatureSnapshot, StrategyDecision, StrategyEvaluation, CandidateEvent
    missing = list(config.execution_missing(raw["input_mode"]))
    action = raw["proposal"]
    reasons = list(raw["reasons"])
    if action == "ENTER":
        if state.status == "open":
            missing.append("position_already_open")
        elif state.emitted_candidate_keys:
            missing.append("reentry_policy_unconfigured")
        reason = leader_session_entry_rejection(config, _time(raw["decided_at"])) if not missing else ""
        if reason:
            missing.append(reason)
    elif action == "EXIT":
        # Entry data/turnover expiry must not disarm a pinned, configured position exit.
        missing = [] if state.status == "open" and context is not None else ["owned_position_context_unavailable"]
    if action not in {"ENTER", "EXIT"} or missing:
        final = "NO_TRADE" if action != "HOLD" else "HOLD"
    else:
        final = action
    reasons += ["unconfigured:" + name for name in missing]
    quantity = (config.quantity if action == "ENTER" else state.position_quantity) if final in {"ENTER", "EXIT"} else 0
    price = raw.get("signal_reference_price") or 0
    if final == "ENTER" and quantity * price > config.capital_won:
        final, quantity = "NO_TRADE", 0
        reasons.append("explicit_capital_limit")
    after = state
    if final == "ENTER":
        after = replace(state, status="candidate", symbol=raw["symbol"], candidate_key=raw["episode_id"],
            candidate_expires_at=raw["valid_until"], emitted_candidate_keys=(*state.emitted_candidate_keys, raw["episode_id"]))
    feature = {"factor_id": "leader_structure/v1", "version": "v1", "value": raw,
               "rules_hash": config.rules_hash, "context": asdict(context) if context else None,
               "context_hash": _id("leader-context/v1", asdict(context)) if context else None,
               "execution_contract_hash": config.execution_contract.fingerprint if config.execution_contract else None}
    snapshot = FeatureSnapshot(_id("leader-snapshot/v1", [run_id, feature, state.to_dict()]), run_id,
        raw["decided_at"], raw["decided_at"], raw["symbol"], "", (feature,), (raw["input_ref"],),
        "leader_breakout", "v1", "leader_execution_adapter/v1", state.to_dict())
    decision = StrategyDecision("pending", run_id, snapshot.snapshot_id,
        raw["symbol"], action, final, quantity, price, quantity*price if final == "ENTER" else 0,
        tuple(reasons), tuple(missing), state.to_dict(), after.to_dict(), raw["decided_at"])
    from .breakout_strategy import _content_id
    body = decision.to_dict()
    body.pop("decision_id")
    decision = replace(decision, decision_id=_content_id("decision", body))
    event = CandidateEvent(raw["episode_id"], run_id, raw["episode_id"], raw["symbol"], "leader_breakout", "v1",
        "explicit_leader_structure", raw["input_ref"], "flat->candidate", None, price, raw["available_at"],
        snapshot.snapshot_id, decision.decision_id) if raw["new_candidate"] else None
    return StrategyEvaluation(snapshot, decision, event, after)


def evaluate_leader_breakout_bar(*, run_id, evaluation_bar, bar_history, universe_frames, config, state,
                               position_context=None, original_observation=None):
    """Pure Family adapter. History supplies observed closes, never fabricated ticks."""
    from .breakout_strategy import StrategyState
    bars = sorted((b for b in bar_history if b.code == evaluation_bar.code
        and _time(b.bar_end) <= _time(evaluation_bar.bar_end)), key=lambda b: _time(b.bar_end))
    minute_state = None
    value = None
    for bar in bars:
        value, minute_state = evaluate_leader_breakout_bar_step(run_id=run_id, evaluation_bar=bar,
            config=config, state=state if bar == evaluation_bar else StrategyState(), minute_state=minute_state,
            position_context=position_context if bar == evaluation_bar else None,
            original_observation=original_observation if bar == evaluation_bar else None)
    if value is None:
        raise ValueError("leader Family requires its current completed minute in history")
    return value


def evaluate_leader_breakout_bar_step(*, run_id, evaluation_bar, config, state, minute_state=None,
                                      position_context=None, original_observation=None):
    """One completed close and an explicit bounded observation window, independent of fills."""
    observed, window, previous_cut = minute_state if minute_state is not None else (None, (), None)
    cut = _time(evaluation_bar.bar_end)
    gap = previous_cut is not None and cut-_time(previous_cut) != timedelta(minutes=1)
    if gap:
        observed, window = None, ()
    value = evaluation_bar.trade_value_million_won
    if original_observation is not None:
        payload = original_observation["payload"]
        value = payload.get("trade_value_million_won")
        if value is None:
            value = payload.get("trading_value_raw")
    count = config.research_turnover_lookback_bars
    window = (*window, value)[-count:] if count else ()
    turnover = sum(window)*1_000_000 if count and len(window) == count and all(type(v) is int and v >= 0 for v in window) else None
    pinned = position_context if state.status == "open" else None
    if state.status == "open" and pinned is None:
        # Only a context proven at entry can own the position boundary. Execution
        # owners also retain the exact signal context, including across restart.
        candidates = [c for c in config.contexts if c.symbol == state.symbol
                      and _time(c.available_at) <= _time(state.opened_at)]
        pinned = max(candidates, key=lambda c: _time(c.available_at), default=None)
    raw, observed = evaluate_leader_breakout(policy=config.policy, symbol=evaluation_bar.code, price=evaluation_bar.close,
        turnover_won=turnover, observed_at=cut, available_at=cut, decision_time=cut,
        input_ref=evaluation_bar.revision_id, mode="1m", source="final_completed_minute:"+cut.date().isoformat(),
        state=observed, position_open=state.status == "open", position_context=pinned)
    if gap:
        raw["reasons"].append("minute_observation_gap_no_cross_bridge")
    from .mock_automation_session_clock import session_clock_assessment
    assessment = session_clock_assessment(config, state, _time(evaluation_bar.bar_end))
    if assessment["exit_required"] and raw["status"] in {"HOLD", "HARD_EXIT", "INVALIDATED"}:
        raw = {**raw, "proposal": "EXIT", "reasons": [*raw["reasons"], *assessment["reasons"]]}
    context = pinned or next((c for c in config.contexts if c.evidence_ref == raw["context_ref"]), None)
    raw.update(price=evaluation_bar.close, availability_basis="final_completed_minute")
    if original_observation is not None:
        raw.update(original_received_at=original_observation.get("leader_original_receipt"),
                   original_source=original_observation.get("source"), trade_value_kind=original_observation.get("value_kind"))
    return leader_evaluation(run_id=run_id, config=config, raw=raw, state=state, context=context), (observed, window, cut.isoformat())


def evaluate_leader_breakout_minutes(policy, observations, *, session_profile, checkpoint=lambda: None):
    """Final completed-minute analysis; never synthesize intraminute ticks/fills.

    Final query bars take precedence. Their historical decision cut is bar_end,
    per the user's completed-bar contract; original receipt time remains evidence.
    """
    selected = final_leader_minute_observations(observations, session_profile=session_profile, checkpoint=checkpoint)
    states, history, decisions = {}, {}, []
    document_history, document_previous = {}, {}
    for row in selected:
        checkpoint()
        p = row["payload"]; symbol = str(p["code"]); cut = _time(p["bar_end"])
        bars = history.setdefault((symbol, cut.date()), [])
        raw = p.get("trade_value_million_won")
        if raw is None:
            raw = p.get("trading_value_raw")
        value = int(raw)*1_000_000 if raw is not None else None
        bars.append((cut, value))
        count = policy.research_turnover_lookback_bars
        window = bars[-count:] if count else []
        complete = bool(count and len(window) == count and all(v is not None for _, v in window)
                        and all(b[0]-a[0] == timedelta(minutes=1) for a, b in zip(window, window[1:])))
        turnover = sum(v for _, v in window) if complete else None
        previous = states.get(symbol)
        gap = previous is not None and previous.last_observed_at and cut-_time(previous.last_observed_at) != timedelta(minutes=1)
        if gap:
            previous = None
        decision, states[symbol] = evaluate_leader_breakout(policy=policy, symbol=symbol,
            price=p["close"], turnover_won=turnover, observed_at=cut, available_at=cut, decision_time=cut,
            input_ref=str(row["revision_id"]), mode="1m", source="final_completed_minute:"+cut.date().isoformat(), state=previous)
        if gap:
            decision["reasons"].append("minute_observation_gap_no_cross_bridge")
        decision["original_received_at"] = row.get("available_at")
        decision["original_source"] = row.get("source")
        decision["trade_value_kind"] = row.get("value_kind")
        decision["availability_basis"] = "final_completed_minute"
        if policy.reference_level is not None:
            key = (symbol,cut.date())
            reference_rows = document_history.setdefault(key,[])
            reference = document_breakout_reference(policy,reference_rows,symbol=symbol,decision_time=cut,mode='1m')
            prior = document_previous.get(symbol)
            if prior and cut-_time(prior['observed_at']) != timedelta(minutes=1):
                prior = None
            document_source = 'final_completed_minute:'+cut.date().isoformat()
            decision['baseline_observation'] = document_breakout_observation(policy,reference,price=p['close'],
                observed_at=cut,available_at=cut,input_ref=row['revision_id'],previous=prior,
                source=document_source,mode='1m',turnover_won=turnover)
            document_previous[symbol] = {'price':p['close'],'observed_at':cut.isoformat(),
                'available_at':cut.isoformat(),'source':document_source}
            reference_rows.append({'trading_date':cut.date().isoformat(),
                'minute':_time(p['bar_start']).strftime('%H:%M:%S'),'code':symbol,'market':'KRX',
                'high':p['high'],'completeness':'complete','value_kind':row.get('value_kind'),
                'source':row.get('source'),'available_at':_time(row['available_at']).timestamp(),
                'original_received_at':row.get('leader_original_receipt',row.get('available_at')),
                'revision_id':row['revision_id']})
            seconds = policy.reference_level['window_seconds']
            if seconds:
                reference_rows[:] = [r for r in reference_rows
                    if _time(r['trading_date']+'T'+r['minute']+'+09:00') >= cut-timedelta(seconds=seconds)]
            else:
                reference_rows.clear()
        decision["decision_id"] = _id("leader-breakout-minute-decision", [policy.fingerprint,
            {key: value for key, value in decision.items() if key != "decision_id"}])
        decisions.append(decision)
    return {"status": "ok", "kind": "leader_breakout", "policy_id": POLICY_ID, "input_mode": "1m",
        "configuration_missing": list(policy.missing("1m")), "decisions": decisions,
        "candidate_count": sum(d["new_candidate"] for d in decisions), "bar_count": len(decisions),
        "blocked_count": sum(d["status"] == "BLOCKED" for d in decisions),
        "fills_simulated": False, "performance_verified": False, "order_authorized": False}
