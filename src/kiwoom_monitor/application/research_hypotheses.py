"""등록된 전략 안에서만 자동 연구 가설을 만드는 결정론적 계약."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from kiwoom_monitor.application.research_evaluation import DevelopmentEvidence
from kiwoom_monitor.application.research_families import (
    get_research_family,
    parse_strategy_config,
)


HYPOTHESIS_VERSION = "research_hypothesis/v1"
GENERATOR_VERSION = "registered_single_parameter_neighbors/v1"
DEVELOPMENT_EVIDENCE_VERSION = "development_evidence_snapshot/v1"
HYPOTHESIS_STATUSES = frozenset({"READY"})


def _reference(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or "\x00" in value:
        raise ValueError(f"{name} must be 1 to 256 characters")
    return value


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("hypothesis document must be canonical JSON data") from exc


def _content_id(document: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(_canonical_json(document).encode("utf-8")).hexdigest()
    return f"research_hypothesis_{digest}"


@dataclass(frozen=True)
class DevelopmentEvidenceRef:
    """Automatic selection may reference development evidence only."""

    reference: str
    scope: str = "DEVELOPMENT"

    def __post_init__(self) -> None:
        _reference(self.reference, "development evidence reference")
        if self.scope != "DEVELOPMENT":
            raise ValueError("automatic hypothesis evidence must be DEVELOPMENT scoped")


@dataclass(frozen=True)
class DevelopmentEvidenceSnapshot:
    """Content-addressed development-only result used by one automatic expansion."""

    version: str
    evidence_id: str
    source_run_id: str
    evidence: DevelopmentEvidence

    def __post_init__(self) -> None:
        if self.version != DEVELOPMENT_EVIDENCE_VERSION:
            raise ValueError("unregistered development evidence snapshot version")
        _reference(self.source_run_id, "development evidence source run")
        if not isinstance(self.evidence, DevelopmentEvidence):
            raise ValueError("development evidence snapshot requires typed evidence")
        if self.evidence.status not in {"ELIGIBLE", "INELIGIBLE", "NOT_APPLICABLE"}:
            raise ValueError("development evidence status is invalid")
        if (
            len(self.evidence.reasons) > 200
            or any(
                not isinstance(reason, str) or not reason or len(reason) > 500 or "\x00" in reason
                for reason in self.evidence.reasons
            )
        ):
            raise ValueError("development evidence reasons are invalid")
        if (
            len(self.evidence.fold_refs) > 200
            or any(
                len(item) != 2
                or any(not isinstance(value, str) or not value or len(value) > 256 for value in item)
                for item in self.evidence.fold_refs
            )
        ):
            raise ValueError("development evidence fold references are invalid")
        for value in (
            self.evidence.net_pnl_won,
            self.evidence.max_drawdown_won,
            self.evidence.closed_trade_count,
            self.evidence.active_day_count,
        ):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError("development evidence metrics must be integers or null")
        if self.evidence.closed_trade_count < 0 or self.evidence.active_day_count < 0:
            raise ValueError("development evidence counts cannot be negative")
        expected = "development_evidence_" + hashlib.sha256(
            _canonical_json(self.identity_dict()).encode("utf-8")
        ).hexdigest()
        if self.evidence_id != expected:
            raise ValueError("development evidence ID does not match its immutable content")

    def identity_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_run_id": self.source_run_id,
            "evidence": asdict(self.evidence),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"evidence_id": self.evidence_id, **self.identity_dict()}


def build_development_evidence_snapshot(
    source_run_id: str,
    evidence: DevelopmentEvidence,
) -> DevelopmentEvidenceSnapshot:
    identity = {
        "version": DEVELOPMENT_EVIDENCE_VERSION,
        "source_run_id": source_run_id,
        "evidence": asdict(evidence),
    }
    evidence_id = "development_evidence_" + hashlib.sha256(
        _canonical_json(identity).encode("utf-8")
    ).hexdigest()
    return DevelopmentEvidenceSnapshot(
        version=DEVELOPMENT_EVIDENCE_VERSION,
        evidence_id=evidence_id,
        source_run_id=source_run_id,
        evidence=evidence,
    )


@dataclass(frozen=True)
class ResearchHypothesis:
    """한 전략 설정과 그 설정에 도달한 근거·부모를 묶은 불변 가설."""

    version: str
    hypothesis_id: str
    generator_version: str
    research_scope_id: str
    family_id: str
    factor_allowlist: tuple[str, ...]
    status: str
    kind: str
    parent_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    baseline_parameters: Mapping[str, Any]
    parameters: Mapping[str, Any]
    changed_parameter: str
    changed_from: int | None
    changed_to: int | None
    reason: str

    def __post_init__(self) -> None:
        if self.version != HYPOTHESIS_VERSION:
            raise ValueError("unregistered research hypothesis version")
        if self.generator_version != GENERATOR_VERSION:
            raise ValueError("unregistered hypothesis generator")
        _reference(self.research_scope_id, "research hypothesis scope")
        definition = get_research_family(self.family_id)
        if (
            not self.factor_allowlist
            or len(set(self.factor_allowlist)) != len(self.factor_allowlist)
            or not set(self.factor_allowlist) <= set(definition.factor_ids)
        ):
            raise ValueError("hypothesis factor allowlist contains an unregistered factor")
        if self.status not in HYPOTHESIS_STATUSES:
            raise ValueError("unregistered research hypothesis status")
        if self.kind not in {"BASELINE", "ONE_PARAMETER_VARIANT"}:
            raise ValueError("unregistered research hypothesis kind")
        if len(self.parent_ids) > 32 or len(set(self.parent_ids)) != len(self.parent_ids):
            raise ValueError("hypothesis parents must be unique and bounded")
        if len(self.evidence_refs) < 1 or len(self.evidence_refs) > 200:
            raise ValueError("hypothesis requires 1 to 200 development evidence references")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("development evidence references must be unique")
        for value in self.parent_ids:
            _reference(value, "parent hypothesis reference")
        for value in self.evidence_refs:
            _reference(value, "development evidence reference")
        if self.hypothesis_id in self.parent_ids:
            raise ValueError("hypothesis cannot be its own parent")
        if not isinstance(self.reason, str) or not self.reason.strip() or len(self.reason) > 500 or "\x00" in self.reason:
            raise ValueError("hypothesis reason must be 1 to 500 characters")

        baseline = parse_strategy_config(self.family_id, self.baseline_parameters).to_dict()
        parameters = parse_strategy_config(self.family_id, self.parameters).to_dict()
        if _canonical_json(baseline) != _canonical_json(self.baseline_parameters):
            raise ValueError("baseline parameters must be the normalized registered strategy document")
        if _canonical_json(parameters) != _canonical_json(self.parameters):
            raise ValueError("hypothesis parameters must be the normalized registered strategy document")

        differences = tuple(
            key for key in sorted(set(baseline) | set(parameters))
            if baseline.get(key) != parameters.get(key)
        )
        if self.kind == "BASELINE":
            if self.parent_ids or differences or self.changed_parameter or self.changed_from is not None or self.changed_to is not None:
                raise ValueError("baseline hypothesis cannot contain a parent or parameter change")
        else:
            if len(self.parent_ids) != 1 or len(differences) != 1:
                raise ValueError("one-parameter hypothesis requires one parent and one changed field")
            if self.changed_parameter != differences[0] or self.changed_parameter not in definition.searchable_parameters:
                raise ValueError("hypothesis changed an unregistered parameter")
            before, after = baseline[self.changed_parameter], parameters[self.changed_parameter]
            if isinstance(before, bool) or not isinstance(before, int) or isinstance(after, bool) or not isinstance(after, int):
                raise ValueError("searchable hypothesis values must be integers")
            if self.changed_from != before or self.changed_to != after or before == after:
                raise ValueError("hypothesis change metadata does not match its strategy parameters")

        expected_id = _content_id(self.identity_dict())
        if self.hypothesis_id != expected_id:
            raise ValueError("hypothesis ID does not match its immutable content")

    def identity_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generator_version": self.generator_version,
            "research_scope_id": self.research_scope_id,
            "family_id": self.family_id,
            "factor_allowlist": list(self.factor_allowlist),
            "status": self.status,
            "kind": self.kind,
            "parent_ids": list(self.parent_ids),
            "evidence_refs": list(self.evidence_refs),
            "baseline_parameters": dict(self.baseline_parameters),
            "parameters": dict(self.parameters),
            "changed_parameter": self.changed_parameter,
            "changed_from": self.changed_from,
            "changed_to": self.changed_to,
            "reason": self.reason,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"hypothesis_id": self.hypothesis_id, **self.identity_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ResearchHypothesis":
        if not isinstance(value, Mapping):
            raise ValueError("research hypothesis must be an object")
        expected = {
            "version", "hypothesis_id", "generator_version", "research_scope_id", "family_id", "factor_allowlist", "status", "kind",
            "parent_ids", "evidence_refs", "baseline_parameters", "parameters",
            "changed_parameter", "changed_from", "changed_to", "reason",
        }
        if set(value) != expected:
            raise ValueError("research hypothesis fields do not match the registered contract")
        baseline = value["baseline_parameters"]
        parameters = value["parameters"]
        if not isinstance(baseline, Mapping) or not isinstance(parameters, Mapping):
            raise ValueError("hypothesis strategy parameters must be objects")
        if (
            not isinstance(value["factor_allowlist"], (list, tuple))
            or not isinstance(value["parent_ids"], (list, tuple))
            or not isinstance(value["evidence_refs"], (list, tuple))
        ):
            raise ValueError("hypothesis factor, parent, and evidence references must be arrays")
        changed_from = value["changed_from"]
        changed_to = value["changed_to"]
        if changed_from is not None and (isinstance(changed_from, bool) or not isinstance(changed_from, int)):
            raise ValueError("changed_from must be an integer or null")
        if changed_to is not None and (isinstance(changed_to, bool) or not isinstance(changed_to, int)):
            raise ValueError("changed_to must be an integer or null")
        return cls(
            version=str(value["version"]),
            hypothesis_id=str(value["hypothesis_id"]),
            generator_version=str(value["generator_version"]),
            research_scope_id=str(value["research_scope_id"]),
            family_id=str(value["family_id"]),
            factor_allowlist=tuple(value["factor_allowlist"]),
            status=str(value["status"]),
            kind=str(value["kind"]),
            parent_ids=tuple(value["parent_ids"]),
            evidence_refs=tuple(value["evidence_refs"]),
            baseline_parameters=dict(baseline),
            parameters=dict(parameters),
            changed_parameter=str(value["changed_parameter"]),
            changed_from=changed_from,
            changed_to=changed_to,
            reason=str(value["reason"]),
        )


@dataclass(frozen=True)
class HypothesisGenerationRequest:
    """호출자가 허용값을 명시하는 단일 파라미터 가설 생성 요청."""

    research_scope_id: str
    family_id: str
    factor_allowlist: tuple[str, ...]
    baseline_parameters: Mapping[str, Any]
    allowed_parameter_values: Mapping[str, tuple[int, ...]]
    development_evidence_refs: tuple[DevelopmentEvidenceRef, ...]
    seed: int
    max_variants: int

    def __post_init__(self) -> None:
        _reference(self.research_scope_id, "research hypothesis scope")
        definition = get_research_family(self.family_id)
        if (
            not self.factor_allowlist
            or len(set(self.factor_allowlist)) != len(self.factor_allowlist)
            or not set(self.factor_allowlist) <= set(definition.factor_ids)
        ):
            raise ValueError("generation factor allowlist contains an unregistered factor")
        baseline = parse_strategy_config(self.family_id, self.baseline_parameters).to_dict()
        if _canonical_json(baseline) != _canonical_json(self.baseline_parameters):
            raise ValueError("baseline parameters must be the normalized registered strategy document")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("hypothesis seed must be an integer")
        if isinstance(self.max_variants, bool) or not isinstance(self.max_variants, int) or not 0 <= self.max_variants <= 1_000:
            raise ValueError("max_variants must be between 0 and 1000")
        if (
            len(self.development_evidence_refs) < 1
            or len(self.development_evidence_refs) > 200
            or any(not isinstance(item, DevelopmentEvidenceRef) for item in self.development_evidence_refs)
        ):
            raise ValueError("generation requires 1 to 200 unique development evidence references")
        evidence_refs = tuple(item.reference for item in self.development_evidence_refs)
        if len(set(evidence_refs)) != len(evidence_refs):
            raise ValueError("generation requires 1 to 200 unique development evidence references")
        if not set(self.allowed_parameter_values) <= definition.searchable_parameters:
            raise ValueError("allowed values contain an unregistered parameter")
        for key, values in self.allowed_parameter_values.items():
            if not isinstance(values, tuple) or not values:
                raise ValueError("each allowed parameter requires a nonempty tuple")
            for value in values:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError("allowed hypothesis values must be integers")
                candidate = dict(baseline)
                candidate[key] = value
                normalized = parse_strategy_config(self.family_id, candidate).to_dict()
                if normalized.get(key) != value:
                    raise ValueError(f"allowed value cannot affect the normalized parameter: {key}")


@dataclass(frozen=True)
class FollowupHypothesisGenerationRequest:
    """Generate bounded single-field neighbors from one completed parent."""

    parent: ResearchHypothesis
    allowed_parameter_values: Mapping[str, tuple[int, ...]]
    development_evidence_refs: tuple[DevelopmentEvidenceRef, ...]
    seed: int
    max_variants: int

    def __post_init__(self) -> None:
        if not isinstance(self.parent, ResearchHypothesis) or self.parent.status != "READY":
            raise ValueError("follow-up generation requires a READY parent hypothesis")
        definition = get_research_family(self.parent.family_id)
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("hypothesis seed must be an integer")
        if (
            isinstance(self.max_variants, bool)
            or not isinstance(self.max_variants, int)
            or not 0 <= self.max_variants <= 1_000
        ):
            raise ValueError("max_variants must be between 0 and 1000")
        if (
            len(self.development_evidence_refs) < 1
            or len(self.development_evidence_refs) > 200
            or any(not isinstance(item, DevelopmentEvidenceRef) for item in self.development_evidence_refs)
        ):
            raise ValueError("follow-up generation requires development evidence")
        references = tuple(item.reference for item in self.development_evidence_refs)
        if len(set(references)) != len(references):
            raise ValueError("follow-up development evidence references must be unique")
        if not set(self.allowed_parameter_values) <= definition.searchable_parameters:
            raise ValueError("allowed values contain an unregistered parameter")
        baseline = dict(self.parent.parameters)
        for key, values in self.allowed_parameter_values.items():
            if not isinstance(values, tuple) or not values:
                raise ValueError("each allowed parameter requires a nonempty tuple")
            for value in values:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError("allowed hypothesis values must be integers")
                candidate = dict(baseline)
                candidate[key] = value
                normalized = parse_strategy_config(self.parent.family_id, candidate).to_dict()
                if normalized.get(key) != value:
                    raise ValueError(f"allowed value cannot affect the normalized parameter: {key}")


def _make_hypothesis(**identity: Any) -> ResearchHypothesis:
    document = {"version": HYPOTHESIS_VERSION, "generator_version": GENERATOR_VERSION, **identity}
    return ResearchHypothesis(hypothesis_id=_content_id(document), **document)


def generate_research_hypotheses(request: HypothesisGenerationRequest) -> tuple[ResearchHypothesis, ...]:
    """기준 가설과 검증 가능한 단일 파라미터 이웃을 고정 순서로 만든다."""
    baseline = parse_strategy_config(request.family_id, request.baseline_parameters).to_dict()
    evidence_refs = tuple(item.reference for item in request.development_evidence_refs)
    root = _make_hypothesis(
        family_id=request.family_id,
        research_scope_id=request.research_scope_id,
        factor_allowlist=request.factor_allowlist,
        status="READY",
        kind="BASELINE",
        parent_ids=(),
        evidence_refs=evidence_refs,
        baseline_parameters=baseline,
        parameters=baseline,
        changed_parameter="",
        changed_from=None,
        changed_to=None,
        reason="registered_strategy_baseline",
    )
    candidates: list[tuple[str, int]] = []
    for key in sorted(request.allowed_parameter_values):
        for value in dict.fromkeys(request.allowed_parameter_values[key]):
            if baseline[key] != value:
                candidates.append((key, value))
    candidates.sort(key=lambda item: (
        hashlib.sha256(f"{request.seed}:{item[0]}:{item[1]}".encode("utf-8")).hexdigest(),
        item[0], item[1],
    ))
    hypotheses = [root]
    for key, value in candidates[:request.max_variants]:
        parameters = dict(baseline)
        parameters[key] = value
        parameters = parse_strategy_config(request.family_id, parameters).to_dict()
        hypotheses.append(_make_hypothesis(
            family_id=request.family_id,
            research_scope_id=request.research_scope_id,
            factor_allowlist=request.factor_allowlist,
            status="READY",
            kind="ONE_PARAMETER_VARIANT",
            parent_ids=(root.hypothesis_id,),
            evidence_refs=evidence_refs,
            baseline_parameters=baseline,
            parameters=parameters,
            changed_parameter=key,
            changed_from=baseline[key],
            changed_to=value,
            reason="registered_single_parameter_counterfactual",
        ))
    return tuple(hypotheses)


def generate_followup_hypotheses(
    request: FollowupHypothesisGenerationRequest,
) -> tuple[ResearchHypothesis, ...]:
    """Create deterministic children only; the completed parent remains the baseline."""
    parent = request.parent
    baseline = dict(parent.parameters)
    evidence_refs = tuple(item.reference for item in request.development_evidence_refs)
    candidates: list[tuple[str, int]] = []
    for key in sorted(request.allowed_parameter_values):
        for value in dict.fromkeys(request.allowed_parameter_values[key]):
            if baseline[key] != value:
                candidates.append((key, value))
    candidates.sort(key=lambda item: (
        hashlib.sha256(
            f"{request.seed}:{parent.hypothesis_id}:{item[0]}:{item[1]}".encode("utf-8")
        ).hexdigest(),
        item[0], item[1],
    ))
    hypotheses = []
    for key, value in candidates[:request.max_variants]:
        parameters = dict(baseline)
        parameters[key] = value
        parameters = parse_strategy_config(parent.family_id, parameters).to_dict()
        hypotheses.append(_make_hypothesis(
            family_id=parent.family_id,
            research_scope_id=parent.research_scope_id,
            factor_allowlist=parent.factor_allowlist,
            status="READY",
            kind="ONE_PARAMETER_VARIANT",
            parent_ids=(parent.hypothesis_id,),
            evidence_refs=evidence_refs,
            baseline_parameters=baseline,
            parameters=parameters,
            changed_parameter=key,
            changed_from=baseline[key],
            changed_to=value,
            reason="completed_development_single_parameter_followup",
        ))
    return tuple(hypotheses)
