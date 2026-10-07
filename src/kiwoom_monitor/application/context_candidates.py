"""장전·전일 근거를 장중 반응과 분리해 추적하는 C1 순수 계약."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from kiwoom_monitor.domain.ranking import normalize_stock_code


CONTEXT_POLICY_VERSION = "context-candidates/v1"
CONTEXT_SCHEMA_VERSION = 1
CONTEXT_SOURCES = frozenset({
    "GLOBAL_CONTEXT", "STOCK_NEWS", "CARRYOVER", "INTRADAY_DISCOVERY",
})
HYPOTHESIS_STATUSES = frozenset({
    "UNCONFIRMED", "FLOW_CONFIRMED", "LEADERSHIP_CONFIRMED",
    "NO_RESPONSE", "EXPIRED", "REJECTED",
})
NORMAL_KRX_SESSION = "NORMAL_KRX"


@dataclass(frozen=True)
class TradingSession:
    session_id: str
    opens_at: str
    closes_at: str
    session_kind: str
    previous_session_id: str = ""

    def __post_init__(self) -> None:
        opened = _aware_datetime(self.opens_at)
        closed = _aware_datetime(self.closes_at)
        if not self.session_id or closed <= opened:
            raise ValueError("trading session requires a valid id and time range")


@dataclass(frozen=True)
class ContextEvidence:
    evidence_ref: str
    source_type: str
    target_id: str
    event_group_id: str
    available_at: str
    relation_version: str
    facts: Mapping[str, Any]
    inferred_impact: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.evidence_ref:
            raise ValueError("context evidence ref is required")
        if self.source_type not in CONTEXT_SOURCES:
            raise ValueError(f"unsupported context source: {self.source_type}")
        if not normalize_stock_code(self.target_id):
            raise ValueError("context evidence target is required")
        if not self.event_group_id or not self.relation_version:
            raise ValueError("event group and relation version are required")
        _aware_datetime(self.available_at)


@dataclass(frozen=True)
class CarryoverEvidence:
    evidence_ref: str
    target_id: str
    trading_session_id: str
    finalized_at: str
    available_at: str
    strong_move_confirmed: bool | None
    change_bps: int | None = None
    trade_value_million_won: int | None = None
    close_location_ppm: int | None = None
    new_high_kind: str = "UNKNOWN"
    upper_limit_status: str = "UNKNOWN"
    theme_revision_id: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_ref or not normalize_stock_code(self.target_id):
            raise ValueError("carryover evidence ref and target are required")
        _aware_datetime(self.finalized_at)
        available = _aware_datetime(self.available_at)
        if available < _aware_datetime(self.finalized_at):
            raise ValueError("carryover evidence cannot be available before finalization")
        if self.new_high_kind not in {
            "NONE", "INTRADAY_HIGH", "PREVIOUS_DAY_HIGH", "N_DAY_HIGH",
            "ALL_TIME_HIGH", "UNKNOWN",
        }:
            raise ValueError("unsupported new high kind")
        if self.upper_limit_status not in {
            "NONE", "TOUCHED", "CLOSED_AT_LIMIT", "UNKNOWN",
        }:
            raise ValueError("unsupported upper limit status")


@dataclass(frozen=True)
class IntradayResponse:
    response_ref: str
    target_id: str
    observed_at: str
    available_at: str
    quality: str
    price_response: bool | None
    flow_response: bool | None
    leadership_response: bool | None
    contradiction: bool = False
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.response_ref or not normalize_stock_code(self.target_id):
            raise ValueError("intraday response ref and target are required")
        observed = _aware_datetime(self.observed_at)
        available = _aware_datetime(self.available_at)
        if available < observed:
            raise ValueError("response cannot be available before it was observed")
        if self.quality not in {"COMPLETE", "PARTIAL", "STALE", "UNAVAILABLE"}:
            raise ValueError("unsupported response quality")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextHypothesis:
    hypothesis_id: str
    revision_id: str
    schema_version: int
    policy_version: str
    target_id: str
    event_group_id: str
    source_types: tuple[str, ...]
    source_refs: tuple[str, ...]
    relation_versions: tuple[str, ...]
    facts: tuple[Mapping[str, Any], ...]
    inferred_impacts: tuple[Mapping[str, Any], ...]
    created_at: str
    available_at: str
    expires_at: str
    status: str
    status_reason: str
    response_refs: tuple[str, ...]
    response_observations: tuple[Mapping[str, Any], ...]
    last_evaluated_at: str
    entry_eligible: bool = False

    def __post_init__(self) -> None:
        if self.status not in HYPOTHESIS_STATUSES:
            raise ValueError(f"unsupported hypothesis status: {self.status}")
        if self.schema_version != CONTEXT_SCHEMA_VERSION:
            raise ValueError("unsupported context hypothesis schema version")
        if self.policy_version != CONTEXT_POLICY_VERSION:
            raise ValueError("unsupported context hypothesis policy version")
        if not normalize_stock_code(self.target_id) or not self.hypothesis_id or not self.revision_id:
            raise ValueError("hypothesis identity and target are required")
        expected_hypothesis_id = _content_id("context", {
            "target_id": normalize_stock_code(self.target_id),
            "event_group_id": self.event_group_id,
            "policy_version": self.policy_version,
        })
        if self.hypothesis_id != expected_hypothesis_id:
            raise ValueError("context hypothesis id does not match its immutable identity")
        if _aware_datetime(self.expires_at) <= _aware_datetime(self.available_at):
            raise ValueError("hypothesis expiry must be after availability")
        # C1 후보는 관찰·연구 입력이며 주문 승격을 소유하지 않는다.
        if self.entry_eligible:
            raise ValueError("context hypothesis cannot authorize an entry")
        body = asdict(self)
        body.pop("revision_id")
        if self.revision_id != _content_id("context-revision", body):
            raise ValueError("context revision id does not match its immutable content")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_context_hypothesis(
    evidence: Iterable[ContextEvidence], *, expires_at: str,
) -> ContextHypothesis:
    values = tuple(evidence)
    if not values:
        raise ValueError("at least one context evidence is required")
    target = normalize_stock_code(values[0].target_id)
    event_group = values[0].event_group_id
    if any(
        normalize_stock_code(value.target_id) != target or value.event_group_id != event_group
        for value in values
    ):
        raise ValueError("merged context evidence must share target and event group")
    ordered = tuple(sorted(values, key=lambda value: (value.available_at, value.evidence_ref)))
    available = max(_aware_datetime(value.available_at) for value in ordered)
    expiry = _aware_datetime(expires_at)
    if expiry <= available:
        raise ValueError("context expiry must be after every input is available")
    hypothesis_id = _content_id("context", {
        "target_id": target, "event_group_id": event_group,
        "policy_version": CONTEXT_POLICY_VERSION,
    })
    body = {
        "hypothesis_id": hypothesis_id,
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "policy_version": CONTEXT_POLICY_VERSION,
        "target_id": target,
        "event_group_id": event_group,
        "source_types": tuple(dict.fromkeys(value.source_type for value in ordered)),
        "source_refs": tuple(dict.fromkeys(value.evidence_ref for value in ordered)),
        "relation_versions": tuple(dict.fromkeys(value.relation_version for value in ordered)),
        "facts": _unique_documents(value.facts for value in ordered),
        "inferred_impacts": _unique_documents(value.inferred_impact for value in ordered),
        "created_at": ordered[0].available_at,
        "available_at": available.isoformat(),
        "expires_at": expiry.isoformat(),
        "status": "UNCONFIRMED",
        "status_reason": "awaiting_intraday_confirmation",
        "response_refs": (),
        "response_observations": (),
        "last_evaluated_at": "",
        "entry_eligible": False,
    }
    return ContextHypothesis(
        revision_id=_content_id("context-revision", body), **body,
    )


def build_news_hypotheses(
    rule_result: Mapping[str, Any],
    *,
    article_revision_id: str,
    event_revision_id: str,
    available_at: str,
    expires_at: str,
    source_type: str = "STOCK_NEWS",
) -> tuple[ContextHypothesis, ...]:
    """N2 사건의 확인 사실과 영향 추론을 대상별 C1 가설로 바꾼다."""
    if source_type not in {"STOCK_NEWS", "GLOBAL_CONTEXT"}:
        raise ValueError("news evidence requires a news context source")
    if str(rule_result.get("role", "")) == "REACTION":
        return ()
    targets = rule_result.get("targets")
    if not isinstance(targets, (list, tuple)):
        return ()
    event_group = str(rule_result.get("event_key") or event_revision_id).strip()
    if not event_group or not article_revision_id or not event_revision_id:
        raise ValueError("news hypothesis requires immutable article and event revisions")
    evidence: list[ContextEvidence] = []
    for target in targets:
        if not isinstance(target, Mapping):
            continue
        target_id = normalize_stock_code(target.get("target_id", ""))
        if not target_id or str(target.get("directness", "")) not in {"DIRECT", "CONFIRMED"}:
            continue
        evidence.append(ContextEvidence(
            evidence_ref=f"{article_revision_id}:{event_revision_id}:{target_id}",
            source_type=source_type, target_id=target_id, event_group_id=event_group,
            available_at=available_at,
            relation_version=str(rule_result.get("rule_version") or "unknown"),
            facts={
                "event_type": rule_result.get("event_type"),
                "certainty": rule_result.get("certainty"),
                "role": rule_result.get("role"),
                "amount_won": rule_result.get("amount_won"),
                "counterparty": rule_result.get("counterparty"),
            },
            inferred_impact={
                "direction": target.get("direction", "UNKNOWN"),
                "directness": target.get("directness", "UNKNOWN"),
                "basis": "news_rule_target",
            },
        ))
    return tuple(build_context_hypothesis((value,), expires_at=expires_at) for value in evidence)


def build_delayed_market_context_evidence(
    bar: Mapping[str, Any],
    roll_state: Mapping[str, Any],
    *,
    target_id: str,
    event_group_id: str,
    relation_version: str,
    inferred_direction: str,
    available_at: str,
) -> ContextEvidence:
    """기존 Yahoo 5분/일봉을 지연 자료라는 사실을 보존해 C1 근거로 바꾼다."""
    provider = str(bar.get("provider", ""))
    timeframe = str(bar.get("timeframe", ""))
    instrument = str(bar.get("instrument", ""))
    contract = str(bar.get("contract", ""))
    bar_time = str(bar.get("bar_time", ""))
    if provider != "yahoo_delayed" or timeframe not in {"5m", "1d"}:
        raise ValueError("global context supports existing Yahoo delayed 5m/1d bars only")
    if not instrument or not contract or not bar_time:
        raise ValueError("global context bar identity is incomplete")
    if str(roll_state.get("active_contract", "")) != contract:
        raise ValueError("global context must use the recorded active futures contract")
    if _aware_datetime(bar_time.replace("Z", "+00:00")) > _aware_datetime(available_at):
        raise ValueError("global context bar cannot be available before its bar time")
    return ContextEvidence(
        evidence_ref=f"external:{instrument}:{contract}:{timeframe}:{bar_time}",
        source_type="GLOBAL_CONTEXT",
        target_id=target_id,
        event_group_id=event_group_id,
        available_at=available_at,
        relation_version=relation_version,
        facts={
            "provider": provider,
            "delayed": True,
            "instrument": instrument,
            "contract": contract,
            "timeframe": timeframe,
            "bar_time": bar_time,
            "close": bar.get("close"),
            "volume": bar.get("volume"),
            "change_pct": roll_state.get("change_pct"),
            "change_basis": roll_state.get("change_basis"),
            "roll_state_updated_at": roll_state.get("updated_at"),
        },
        inferred_impact={
            "direction": str(inferred_direction or "UNKNOWN"),
            "basis": "versioned_external_market_relation",
            "simultaneous_second_observation": False,
        },
    )


def build_carryover_hypothesis(
    evidence: CarryoverEvidence,
    *,
    next_session: TradingSession,
) -> ContextHypothesis:
    """명시된 전 거래일 관계만 사용하며 달력 날짜를 역산하지 않는다."""
    if next_session.session_kind != NORMAL_KRX_SESSION:
        raise ValueError("first carryover profile supports normal KRX sessions only")
    if next_session.previous_session_id != evidence.trading_session_id:
        raise ValueError("carryover evidence is not from the session calendar predecessor")
    available = _aware_datetime(evidence.available_at)
    if available >= _aware_datetime(next_session.closes_at):
        raise ValueError("carryover evidence became available after the target session")
    if (
        evidence.strong_move_confirmed is not True
        and evidence.new_high_kind in {"NONE", "UNKNOWN"}
        and evidence.upper_limit_status in {"NONE", "UNKNOWN"}
    ):
        raise ValueError("carryover evidence has no confirmed candidate fact")
    facts = {
        "previous_session_id": evidence.trading_session_id,
        "strong_move_confirmed": evidence.strong_move_confirmed,
        "change_bps": evidence.change_bps,
        "trade_value_million_won": evidence.trade_value_million_won,
        "close_location_ppm": evidence.close_location_ppm,
        "new_high_kind": evidence.new_high_kind,
        "upper_limit_status": evidence.upper_limit_status,
        "theme_revision_id": evidence.theme_revision_id or None,
    }
    context = ContextEvidence(
        evidence_ref=evidence.evidence_ref,
        source_type="CARRYOVER", target_id=evidence.target_id,
        event_group_id=f"carryover:{evidence.trading_session_id}:{normalize_stock_code(evidence.target_id)}",
        available_at=evidence.available_at, relation_version="carryover-facts/v1",
        facts=facts,
        inferred_impact={
            "direction": "POSITIVE_WATCH",
            "basis": "previous_session_momentum_requires_intraday_confirmation",
        },
    )
    return build_context_hypothesis((context,), expires_at=next_session.closes_at)


def build_intraday_discovery_hypothesis(
    candidate_event: Mapping[str, Any], *, expires_at: str,
) -> ContextHypothesis:
    """뉴스·전일 후보가 없어도 D4 관측 후보를 독립 출처로 만든다."""
    event_id = str(candidate_event.get("event_id", "")).strip()
    target = normalize_stock_code(candidate_event.get("symbol", ""))
    available_at = str(candidate_event.get("available_at", ""))
    if not event_id or not target:
        raise ValueError("intraday discovery requires candidate event id and symbol")
    context = ContextEvidence(
        evidence_ref=event_id, source_type="INTRADAY_DISCOVERY", target_id=target,
        event_group_id=str(candidate_event.get("dedup_key") or event_id),
        available_at=available_at,
        relation_version=str(candidate_event.get("strategy_version") or "unknown"),
        facts={
            "setup": candidate_event.get("setup"),
            "reference_revision_id": candidate_event.get("reference_revision_id"),
            "signal_reference_price": candidate_event.get("signal_reference_price"),
        },
        inferred_impact={
            "direction": "POSITIVE_WATCH",
            "basis": "intraday_price_discovery_requires_flow_confirmation",
        },
    )
    return build_context_hypothesis((context,), expires_at=expires_at)


def advance_context_hypothesis(
    hypothesis: ContextHypothesis,
    response: IntradayResponse,
) -> ContextHypothesis:
    """한 관측으로 반응 상태를 전이한다. 기사 사실 자체는 수정하지 않는다."""
    if normalize_stock_code(response.target_id) != hypothesis.target_id:
        raise ValueError("response target does not match hypothesis")
    available = _aware_datetime(response.available_at)
    observed = _aware_datetime(response.observed_at)
    if available < _aware_datetime(hypothesis.available_at):
        raise ValueError("response predates hypothesis availability")
    if observed < _aware_datetime(hypothesis.available_at):
        raise ValueError("response was observed before hypothesis availability")
    if response.response_ref in hypothesis.response_refs:
        return hypothesis
    status = hypothesis.status
    reason = ""
    if status in {"EXPIRED", "REJECTED"}:
        reason = "terminal_status_preserved"
    elif response.quality != "COMPLETE":
        reason = f"response_data_{response.quality.casefold()}"
    elif observed > _aware_datetime(hypothesis.expires_at):
        if status == "NO_RESPONSE":
            status = "EXPIRED"
            reason = "no_response_observation_closed"
        elif status == "UNCONFIRMED":
            status = "NO_RESPONSE"
            reason = "no_intraday_response_by_expiry"
        else:
            reason = "confirmed_status_preserved_at_expiry"
    elif response.contradiction:
        status = "REJECTED"
        reason = "market_response_contradicted_hypothesis"
    elif (
        response.leadership_response is True
        and (response.flow_response is True or status == "FLOW_CONFIRMED")
    ):
        status = "LEADERSHIP_CONFIRMED"
        reason = "flow_and_leadership_confirmed"
    elif response.flow_response is True:
        status = "FLOW_CONFIRMED"
        reason = "flow_confirmed"
    elif available >= _aware_datetime(hypothesis.expires_at):
        if status == "NO_RESPONSE":
            status = "EXPIRED"
            reason = "no_response_observation_closed"
        elif status == "UNCONFIRMED":
            status = "NO_RESPONSE"
            reason = "no_intraday_response_by_expiry"
        else:
            reason = "confirmed_status_preserved_at_expiry"
    elif response.leadership_response is True:
        reason = "leadership_without_flow_confirmation"
    else:
        reason = "awaiting_intraday_confirmation"
    body = {
        **hypothesis.to_dict(),
        "status": status,
        "status_reason": reason,
        "response_refs": (*hypothesis.response_refs, response.response_ref),
        "response_observations": (
            *hypothesis.response_observations, response.to_dict(),
        ),
        "last_evaluated_at": available.isoformat(),
        "entry_eligible": False,
    }
    body.pop("revision_id", None)
    return ContextHypothesis(
        revision_id=_content_id("context-revision", body), **body,
    )


def merge_context_hypotheses(
    hypotheses: Iterable[ContextHypothesis],
) -> ContextHypothesis:
    """같은 사건·종목의 복수 출처를 합치되 독립 호재로 중복 가산하지 않는다."""
    values = tuple(hypotheses)
    if not values:
        raise ValueError("at least one hypothesis is required")
    target, event_group = values[0].target_id, values[0].event_group_id
    if any(value.target_id != target or value.event_group_id != event_group for value in values):
        raise ValueError("only the same target and event group can be merged")
    if any(value.status != "UNCONFIRMED" or value.response_refs for value in values):
        raise ValueError("context sources must be merged before intraday responses are applied")
    strongest = max(values, key=lambda value: _status_rank(value.status))
    body = {
        "hypothesis_id": values[0].hypothesis_id,
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "policy_version": CONTEXT_POLICY_VERSION,
        "target_id": target,
        "event_group_id": event_group,
        "source_types": tuple(dict.fromkeys(
            source for value in values for source in value.source_types
        )),
        "source_refs": tuple(dict.fromkeys(
            ref for value in values for ref in value.source_refs
        )),
        "relation_versions": tuple(dict.fromkeys(
            version for value in values for version in value.relation_versions
        )),
        "facts": _unique_documents(fact for value in values for fact in value.facts),
        "inferred_impacts": _unique_documents(
            impact for value in values for impact in value.inferred_impacts
        ),
        "created_at": min(values, key=lambda value: _aware_datetime(value.created_at)).created_at,
        "available_at": max(values, key=lambda value: _aware_datetime(value.available_at)).available_at,
        "expires_at": max(values, key=lambda value: _aware_datetime(value.expires_at)).expires_at,
        "status": strongest.status,
        "status_reason": strongest.status_reason,
        "response_refs": tuple(dict.fromkeys(
            ref for value in values for ref in value.response_refs
        )),
        "response_observations": _unique_documents(
            observation
            for value in values
            for observation in value.response_observations
        ),
        "last_evaluated_at": strongest.last_evaluated_at,
        "entry_eligible": False,
    }
    return ContextHypothesis(
        revision_id=_content_id("context-revision", body), **body,
    )


def _status_rank(status: str) -> int:
    return {
        "UNCONFIRMED": 0, "NO_RESPONSE": 1, "FLOW_CONFIRMED": 2,
        "LEADERSHIP_CONFIRMED": 3, "EXPIRED": 4, "REJECTED": 5,
    }[status]


def _unique_documents(values: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        encoded = _canonical_json(value)
        if encoded not in seen:
            seen.add(encoded)
            result.append(dict(value))
    return tuple(result)


def _aware_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("context candidate timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"
