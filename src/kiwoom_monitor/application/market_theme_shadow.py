"""Order-free H1/H2 evaluation at an explicit input cut; no store or runtime owner."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.market_research_features import (
    MARKET_RULE_VERSION, MarketFeatureParameters, MarketResearchFrame, MatureBreakoutSummary,
)
from kiwoom_monitor.application.research_factors import compute_market_regime
from kiwoom_monitor.application.theme_leadership import (
    LEADERSHIP_VERSION, THESIS_POLICIES, EntryThesis, SecondTradePoint,
    ThemeLeadershipEvaluation, ThemeLeadershipParameters, ThemeLeadershipState,
    ThemeMembership, UpperLimitReference, evaluate_entry_thesis, evaluate_theme_leadership,
)
from kiwoom_monitor.domain.ranking import normalize_stock_code


SHADOW_CONTEXT_VERSION = "market-theme-shadow/v1"
STOCK_THESIS_SHADOW_VERSION = "stock-thesis-shadow/v1"


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("shadow input times must be timezone-aware")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class MatureBreakoutEvidence:
    input_ref: str
    available_at: str
    summary: MatureBreakoutSummary

    def __post_init__(self) -> None:
        if not self.input_ref:
            raise ValueError("mature breakout evidence requires an input ref")
        _time(self.available_at)


@dataclass(frozen=True)
class MarketThemeShadowInputs:
    decision_time: str
    market_venue: str
    market_window: str
    leadership_venue: str
    market_frames: tuple[MarketResearchFrame, ...]
    theme_membership: ThemeMembership | None
    second_points: tuple[SecondTradePoint, ...]
    continuity_status: str
    breakout_evidence: MatureBreakoutEvidence | None = None
    previous_leadership_state: ThemeLeadershipState | None = None
    upper_limits: tuple[UpperLimitReference, ...] = ()

    def __post_init__(self) -> None:
        _time(self.decision_time)
        if self.market_venue not in {"KRX", "NXT", "COMBINED"} or not self.market_window:
            raise ValueError("shadow market scope must be explicit")
        if self.leadership_venue not in {"KRX", "NXT"}:
            raise ValueError("leadership venue must be KRX or NXT")
        if self.continuity_status not in {"COMPLETE", "PARTIAL", "STALE", "UNAVAILABLE"}:
            raise ValueError("second-trade continuity must be explicit")


@dataclass(frozen=True)
class StockThesisShadowRequest:
    entry_thesis: EntryThesis
    thesis_venue: str
    thesis_policy_version: str

    def __post_init__(self) -> None:
        if self.thesis_venue not in {"KRX", "NXT"}:
            raise ValueError("entry thesis venue must be explicit")
        if self.thesis_policy_version not in THESIS_POLICIES:
            raise ValueError("unsupported entry thesis policy")


def stock_thesis_shadow_request_from_dict(value: dict[str, Any]) -> StockThesisShadowRequest:
    request = dict(value)
    thesis = dict(request["entry_thesis"])
    thesis["factor_refs"] = tuple(thesis["factor_refs"])
    thesis["hypothesis_refs"] = tuple(thesis["hypothesis_refs"])
    request["entry_thesis"] = EntryThesis(**thesis)
    return StockThesisShadowRequest(**request)


def evaluate_market_theme_shadow(
    inputs: MarketThemeShadowInputs,
    *,
    market_parameters: MarketFeatureParameters = MarketFeatureParameters(),
    leadership_parameters: ThemeLeadershipParameters = ThemeLeadershipParameters(),
) -> dict[str, Any]:
    """Combine existing experimental factors, without promoting their output to a trade."""
    result, _ = _evaluate_market_theme_shadow(
        inputs, market_parameters=market_parameters, leadership_parameters=leadership_parameters,
    )
    return result


def _evaluate_market_theme_shadow(
    inputs: MarketThemeShadowInputs,
    *,
    market_parameters: MarketFeatureParameters,
    leadership_parameters: ThemeLeadershipParameters,
    _market_cache: list | None = None,
) -> tuple[dict[str, Any], ThemeLeadershipEvaluation | None]:
    cutoff = _time(inputs.decision_time)
    decision_time = cutoff.isoformat()
    state = inputs.previous_leadership_state
    if state is not None:
        for value in (state.last_observed_at, state.candidate_since):
            if value and _time(value) > cutoff:
                raise ValueError("previous leadership state is ahead of the input cut")

    if _market_cache is None:
        market = _evaluate_shadow_market(inputs, market_parameters, cutoff)
    else:
        # Compare complete immutable values, including content and lineage. A
        # revision ID alone cannot validate reuse of a frame with changed payload.
        scope = (cutoff, inputs.market_venue, inputs.market_window,
                 inputs.market_frames, inputs.breakout_evidence, market_parameters)
        entry = next((entry for entry in _market_cache if _same_shadow_scope(entry[0], scope)), None)
        if entry is None:
            try:
                value = _evaluate_shadow_market(inputs, market_parameters, cutoff)
            except Exception as error:
                value = error
            entry = (scope, value)
            _market_cache.append(entry)
        if isinstance(entry[1], Exception):
            raise entry[1]
        # Each context/policy owns its output; cached facts are never published.
        market = deepcopy(entry[1])

    membership = inputs.theme_membership
    leadership = _missing("theme_membership_unavailable", decision_time, LEADERSHIP_VERSION)
    leadership["inferences"] = {"raw_leader_id": "", "displayed_leader_id": "",
                                "trend_state": "UNKNOWN", "expansion_state": "UNKNOWN", "limit_state": "UNKNOWN"}
    next_state = None
    evaluation = None
    if membership is not None and _time(membership.available_at) <= cutoff:
        members = {normalize_stock_code(code) for code in membership.member_codes}
        points = _unique(tuple(point for point in inputs.second_points
                               if point.market == inputs.leadership_venue
                               and normalize_stock_code(point.code) in members
                               and cutoff - timedelta(seconds=2 * leadership_parameters.window_seconds)
                               < _time(point.observed_at) <= cutoff
                               and _time(point.available_at) <= cutoff), "input_ref")
        limits = _unique(tuple(limit for limit in inputs.upper_limits
                               if _time(limit.available_at) <= cutoff
                               and normalize_stock_code(limit.code) in members), "input_ref")
        evaluation = evaluate_theme_leadership(
            membership, points, as_of=decision_time,
            venue=inputs.leadership_venue, continuity_status=inputs.continuity_status,
            parameters=leadership_parameters, previous_state=state, upper_limits=limits,
        )
        latest = [max((_time(point.available_at) for point in points
                       if point.market == inputs.leadership_venue
                       and normalize_stock_code(point.code) == normalize_stock_code(code)
                       and _time(point.observed_at) <= cutoff and _time(point.available_at) <= cutoff),
                      default=cutoff) for code in membership.member_codes]
        expires = min(latest) + timedelta(seconds=leadership_parameters.max_data_age_seconds)
        refs = tuple(dict.fromkeys((*evaluation.input_refs,
                    *(limit.input_ref for limit in limits))))
        leadership = {
            "facts": {"theme_id": evaluation.theme_id, "theme_revision_id": evaluation.theme_revision_id,
                      "members": tuple(asdict(member) for member in evaluation.members)},
            "inferences": {key: getattr(evaluation, key) for key in (
                "raw_leader_id", "displayed_leader_id", "trend_state", "expansion_state", "limit_state")},
            "events": tuple(asdict(event) for event in evaluation.events),
            "evidence_refs": refs, "quality": dict(evaluation.quality),
            "available_at": decision_time,
            "expires_at": expires.isoformat() if evaluation.quality["status"] == "COMPLETE" else decision_time,
            "policy_version": evaluation.policy_version,
        }
        next_state = asdict(evaluation.state)

    components = (market, leadership)
    complete = all(component["quality"]["status"] == "COMPLETE" for component in components)
    body = {
        "policy_version": SHADOW_CONTEXT_VERSION, "decision_time": decision_time,
        "scope": {"market_universe": "TOP20", "market_venue": inputs.market_venue,
                  "market_window": inputs.market_window, "leadership_venue": inputs.leadership_venue,
                  "source_durability": "not_verified"},
        "parameters": {"market": asdict(market_parameters), "leadership": asdict(leadership_parameters)},
        "market": market, "leadership": leadership, "next_leadership_state": next_state,
        "quality": {"status": "COMPLETE" if complete else "PARTIAL"},
        "evidence_refs": tuple(dict.fromkeys(ref for component in components for ref in component["evidence_refs"])),
        "available_at": decision_time,
        "expires_at": min(_time(component["expires_at"]) for component in components).isoformat(),
        "order_authorized": False,
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"revision_id": f"market-theme-shadow:{digest}", **body}, evaluation


def _same_shadow_scope(left, right):
    """Preserve encoded types as well as values; 100 and 100.0 hash differently."""
    if left is right:
        return True
    if type(left) is not type(right):
        return False
    if isinstance(left, (tuple, list)):
        return len(left) == len(right) and all(_same_shadow_scope(a, b) for a, b in zip(left, right))
    if is_dataclass(left):
        return all(_same_shadow_scope(getattr(left, field.name), getattr(right, field.name)) for field in fields(left))
    return left == right


def _evaluate_shadow_market(inputs, market_parameters, cutoff):
    """Existing H1 result at this cut; independent of H2 membership and state."""
    decision_time = cutoff.isoformat()
    frames = _unique(tuple(frame for frame in inputs.market_frames
                   if frame.venue == inputs.market_venue and frame.window == inputs.market_window
                   and _time(frame.available_at) <= cutoff), "revision_id")
    evidence = inputs.breakout_evidence
    if evidence is not None and _time(evidence.available_at) > cutoff:
        evidence = None
    summary = evidence.summary if evidence else MatureBreakoutSummary(0, 0, 0)
    market = _missing("market_frames_unavailable", decision_time, MARKET_RULE_VERSION)
    market["inferences"] = {"market_type": "UNKNOWN", "candidate_types": ()}
    if frames:
        factor = compute_market_regime(frames, summary, market_parameters, as_of=cutoff)
        value = factor.value
        assert value is not None
        reasons = list(value["quality"].get("missing_inputs", ()))
        if evidence is None:
            reasons.append("mature_breakout_evidence_unavailable")
        expires = _time(factor.available_at) + timedelta(seconds=market_parameters.max_gap_seconds)
        current = max(frames, key=lambda frame: (_time(frame.available_at), frame.revision_id))
        same_day = current.trading_date == cutoff.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
        stale = cutoff >= expires or not same_day
        if stale:
            reasons.append("market_frame_stale")
        market = {
            "facts": value["features"],
            "inferences": {"market_type": "UNKNOWN" if stale else value["market_type"],
                           "candidate_types": () if stale else value["candidate_types"]},
            "evidence_refs": (*factor.input_refs, *((evidence.input_ref,) if evidence else ())),
            "quality": {"status": "STALE" if stale else "PARTIAL" if reasons else "COMPLETE",
                        "reasons": tuple(dict.fromkeys(reasons))},
            "available_at": max(_time(factor.available_at), _time(evidence.available_at)
                                if evidence else _time(factor.available_at)).isoformat(),
            "expires_at": expires.isoformat(), "policy_version": value["rule_version"],
        }
    return market


def evaluate_stock_thesis_shadow(
    inputs: MarketThemeShadowInputs,
    request: StockThesisShadowRequest,
    *,
    market_parameters: MarketFeatureParameters = MarketFeatureParameters(),
    leadership_parameters: ThemeLeadershipParameters = ThemeLeadershipParameters(),
) -> dict[str, Any]:
    """Apply an explicitly selected existing thesis policy; never dispatch an order."""
    _validate_stock_thesis_request(inputs, request)
    context, leadership = _evaluate_market_theme_shadow(
        inputs, market_parameters=market_parameters, leadership_parameters=leadership_parameters,
    )
    return _stock_thesis_result(request, context, leadership, leadership_parameters)


def evaluate_market_theme_shadow_batch(
    inputs: MarketThemeShadowInputs,
    requests: tuple[StockThesisShadowRequest | None, ...],
    *,
    market_parameters: MarketFeatureParameters = MarketFeatureParameters(),
    leadership_parameters: ThemeLeadershipParameters = ThemeLeadershipParameters(),
    _market_cache: list | None = None,
) -> tuple[dict[str, Any] | Exception, ...]:
    """Compute one context, then independent policies; per-request errors stay isolated.

    None requests return the context itself. Common input failures raise; request
    failures occupy their own result slot. The reserved _market_cache is supplied
    only by the owned multi-scope round and cleared when that round finishes.
    """
    if not 1 <= len(requests) <= 32:
        raise ValueError("shared shadow calculation requires 1..32 requests")
    context, leadership = _evaluate_market_theme_shadow(
        inputs, market_parameters=market_parameters, leadership_parameters=leadership_parameters,
        _market_cache=_market_cache,
    )
    results = []
    for request in requests:
        try:
            if request is None:
                result = deepcopy(context)
            else:
                _validate_stock_thesis_request(inputs, request)
                result = _stock_thesis_result(request, deepcopy(context), leadership, leadership_parameters)
            results.append(result)
        except Exception as error:
            results.append(error)
    return tuple(results)


def _validate_stock_thesis_request(inputs, request):
    thesis = request.entry_thesis
    if request.thesis_venue != inputs.leadership_venue:
        raise ValueError("entry thesis and leadership venue differ")
    if _time(thesis.available_at) > _time(inputs.decision_time):
        raise ValueError("entry thesis was not available at the input cut")
    membership = inputs.theme_membership
    if membership is not None and _time(membership.available_at) <= _time(inputs.decision_time):
        if membership.theme_id != thesis.theme_id:
            raise ValueError("current membership belongs to another thesis theme")


def _stock_thesis_result(request, context, leadership, leadership_parameters):
    thesis = request.entry_thesis
    source_decision = None
    reasons = ("leadership_data_unavailable",)
    thesis_state, proposal, action = "UNKNOWN", "NO_TRADE", "HOLD"
    if leadership is not None:
        source_decision = evaluate_entry_thesis(
            thesis, leadership, policy_version=request.thesis_policy_version,
        ).to_dict()
        thesis_state = source_decision["thesis_state"]
        proposal = source_decision["proposal"]
        action = source_decision["final_action"]
        reasons = source_decision["reasons"]
    body = {
        "policy_version": STOCK_THESIS_SHADOW_VERSION,
        "thesis_policy_version": request.thesis_policy_version,
        "thesis_venue": request.thesis_venue,
        "run_id": thesis.run_id, "symbol": normalize_stock_code(thesis.symbol),
        "thesis_ref": thesis.thesis_id, "context_ref": context["revision_id"],
        "decision_time": context["decision_time"],
        "thesis_state": thesis_state, "proposal": proposal, "action": action,
        "reasons": reasons,
        "invalidation_conditions": (
            {"condition": "entry_leader_replaced", "entry_leader_id": thesis.leader_id,
             "effect": "INVALIDATED"},
            {"condition": "entry_leader_price_exit", "price_exit_bps": leadership_parameters.price_exit_bps,
             "effect": "AT_RISK"},
        ),
        "input_refs": tuple(dict.fromkeys((thesis.thesis_id, *thesis.factor_refs,
                                           *thesis.hypothesis_refs, context["revision_id"],
                                           *((leadership.revision_id,) if leadership else ()),
                                           *context["evidence_refs"]))),
        "quality": dict(context["leadership"]["quality"]),
        "available_at": context["decision_time"],
        # H1 is reported as context, not a veto on the existing H2 protection policy.
        "valid_until": context["leadership"]["expires_at"],
        "source_decision": source_decision, "context": context,
        "order_authorized": False,
    }
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"decision_id": f"stock-thesis-shadow:{digest}", **body}


def _missing(reason: str, at: str, version: str) -> dict[str, Any]:
    return {"facts": {}, "inferences": {}, "evidence_refs": (),
            "quality": {"status": "PARTIAL", "reasons": (reason,)},
            "available_at": at, "expires_at": at, "policy_version": version}


def _unique(values: tuple[Any, ...], ref_field: str) -> tuple[Any, ...]:
    by_ref: dict[str, Any] = {}
    for value in values:
        ref = getattr(value, ref_field)
        if ref in by_ref and by_ref[ref] != value:
            raise ValueError(f"conflicting immutable shadow input: {ref}")
        by_ref[ref] = value
    return tuple(by_ref.values())
