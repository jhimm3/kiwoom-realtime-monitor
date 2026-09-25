"""Immutable PC-to-NAS candidate publication and final eligibility contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from kiwoom_monitor.application.research_execution import (
    SimulationCostModel,
    SimulationExecutionConfig,
)
from kiwoom_monitor.application.market_session_schedule import research_session_profile_contract_matches
from kiwoom_monitor.application.research_families import parse_strategy_config
from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec


CANDIDATE_PACKAGE_VERSION = "mock_automation_candidate_package/v1"
ELIGIBILITY_POLICY_VERSION = "mock_automation_eligibility_policy/v1"
ELIGIBILITY_RECEIPT_VERSION = "mock_automation_eligibility_receipt/v1"
MAX_CANDIDATE_PUBLICATION_BYTES = 256 * 1024


class CandidateEligibilityStatus(StrEnum):
    BLOCKED = "BLOCKED"
    ELIGIBLE = "ELIGIBLE"


@dataclass(frozen=True)
class MockAutomationEligibilityPolicy:
    strategy_ref: str
    candidate_spec_hash: str
    frozen_at: datetime
    minimum_closed_trades: int | None
    minimum_active_days: int | None
    minimum_net_realized_pnl_won: int | None
    maximum_drawdown_ppm: int | None
    version: str = ELIGIBILITY_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.version != ELIGIBILITY_POLICY_VERSION:
            raise ValueError("unsupported mock automation eligibility policy version")
        if not self.strategy_ref.strip():
            raise ValueError("strategy_ref is required")
        _require_sha256(self.candidate_spec_hash, "candidate_spec_hash")
        _require_aware(self.frozen_at, "frozen_at")
        for name in ("minimum_closed_trades", "minimum_active_days", "maximum_drawdown_ppm"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or TBD")
        value = self.minimum_net_realized_pnl_won
        if value is not None and type(value) is not int:
            raise ValueError("minimum_net_realized_pnl_won must be an integer or TBD")
        if self.maximum_drawdown_ppm is not None and self.maximum_drawdown_ppm > 1_000_000:
            raise ValueError("maximum_drawdown_ppm must not exceed 1,000,000 ppm")

    @property
    def tbd_fields(self) -> tuple[str, ...]:
        return tuple(name for name in (
            "minimum_closed_trades", "minimum_active_days",
            "minimum_net_realized_pnl_won", "maximum_drawdown_ppm",
        ) if getattr(self, name) is None)

    @property
    def policy_id(self) -> str:
        return _content_id("mock_eligibility_policy", self._identity_document())

    def _identity_document(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "strategy_ref": self.strategy_ref,
            "candidate_spec_hash": self.candidate_spec_hash,
            "frozen_at": self.frozen_at.isoformat(),
            "minimum_closed_trades": self.minimum_closed_trades,
            "minimum_active_days": self.minimum_active_days,
            "minimum_net_realized_pnl_won": self.minimum_net_realized_pnl_won,
            "maximum_drawdown_ppm": self.maximum_drawdown_ppm,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"policy_id": self.policy_id, **self._identity_document()}


@dataclass(frozen=True)
class MockAutomationCandidatePackage:
    strategy_ref: str
    candidate_spec: Mapping[str, Any]
    candidate_spec_hash: str
    scientific_implementation_hash: str
    source_final_batch_id: str
    source_final_run_id: str
    source_final_result_hash: str
    source_run_started_at: datetime
    source_run_finished_at: datetime
    evaluation_evidence: Mapping[str, Any]
    version: str = CANDIDATE_PACKAGE_VERSION

    def __post_init__(self) -> None:
        if self.version != CANDIDATE_PACKAGE_VERSION:
            raise ValueError("unsupported mock automation candidate package version")
        for name in ("strategy_ref", "source_final_batch_id", "source_final_run_id"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        for name in (
            "candidate_spec_hash", "scientific_implementation_hash", "source_final_result_hash",
        ):
            _require_sha256(str(getattr(self, name)), name)
        _require_aware(self.source_run_started_at, "source_run_started_at")
        _require_aware(self.source_run_finished_at, "source_run_finished_at")
        if self.source_run_finished_at < self.source_run_started_at:
            raise ValueError("source final run finished before it started")
        _validate_candidate_spec(self.candidate_spec, self.candidate_spec_hash)
        if self.candidate_spec.get("implementation_hash") != self.scientific_implementation_hash:
            raise ValueError("candidate scientific implementation hash is inconsistent")
        _validate_evaluation_evidence(self.evaluation_evidence, self.source_final_run_id)
        _reject_unsafe_values(self.to_dict())

    @property
    def package_hash(self) -> str:
        return _sha256(self._identity_document())

    def _identity_document(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "strategy_ref": self.strategy_ref,
            "candidate_spec": dict(self.candidate_spec),
            "candidate_spec_hash": self.candidate_spec_hash,
            "scientific_implementation_hash": self.scientific_implementation_hash,
            "source_final": {
                "batch_id": self.source_final_batch_id,
                "run_id": self.source_final_run_id,
                "result_hash": self.source_final_result_hash,
                "started_at": self.source_run_started_at.isoformat(),
                "finished_at": self.source_run_finished_at.isoformat(),
            },
            "evaluation_evidence": dict(self.evaluation_evidence),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"package_hash": self.package_hash, **self._identity_document()}


@dataclass(frozen=True)
class MockAutomationEligibilityReceipt:
    account_ref: str
    package_hash: str
    candidate_spec_hash: str
    policy_id: str
    evaluated_at: datetime
    status: CandidateEligibilityStatus
    reasons: tuple[str, ...]
    observed_metrics: Mapping[str, int | None]
    version: str = ELIGIBILITY_RECEIPT_VERSION

    def __post_init__(self) -> None:
        if self.version != ELIGIBILITY_RECEIPT_VERSION:
            raise ValueError("unsupported mock automation eligibility receipt version")
        if not self.account_ref.strip() or not self.policy_id.strip():
            raise ValueError("eligibility account_ref and policy_id are required")
        _require_sha256(self.package_hash, "package_hash")
        _require_sha256(self.candidate_spec_hash, "candidate_spec_hash")
        _require_aware(self.evaluated_at, "evaluated_at")
        if self.status is CandidateEligibilityStatus.ELIGIBLE and self.reasons:
            raise ValueError("eligible receipt must not contain blocking reasons")

    @property
    def receipt_id(self) -> str:
        return _content_id("mock_eligibility", self._identity_document())

    def _identity_document(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "account_ref": self.account_ref,
            "package_hash": self.package_hash,
            "candidate_spec_hash": self.candidate_spec_hash,
            "policy_id": self.policy_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "status": self.status.value,
            "reasons": list(self.reasons),
            "observed_metrics": dict(self.observed_metrics),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"receipt_id": self.receipt_id, **self._identity_document()}


def build_candidate_package(
    *, strategy_ref: str, candidate_spec: Mapping[str, Any], batch_id: str,
    run: Mapping[str, Any], execution: Mapping[str, Any], report: Mapping[str, Any],
) -> MockAutomationCandidatePackage:
    candidate_hash = _sha256(candidate_spec)
    if (execution.get("candidate_spec_hash") != candidate_hash
            or execution.get("state") != "COMPLETED"
            or execution.get("run_id") != run.get("run_id")
            or execution.get("logical_result_hash") != run.get("logical_result_hash")
            or run.get("status") != "completed"):
        raise ValueError("completed final candidate lineage is missing or inconsistent")
    if report.get("run_id") != run.get("run_id"):
        raise ValueError("final research report does not match the source run")
    evidence = {
        "report_id": str(report.get("report_id", "")),
        "report_status": str(report.get("status", "")),
        "evaluation_spec": run.get("spec", {}).get("evaluation_spec"),
        "oos_fold": _single_oos_fold(report),
    }
    return MockAutomationCandidatePackage(
        strategy_ref=strategy_ref, candidate_spec=dict(candidate_spec),
        candidate_spec_hash=candidate_hash,
        scientific_implementation_hash=str(candidate_spec.get("implementation_hash", "")),
        source_final_batch_id=batch_id, source_final_run_id=str(run.get("run_id", "")),
        source_final_result_hash=str(run.get("logical_result_hash", "")),
        source_run_started_at=datetime.fromisoformat(str(run.get("started_at", ""))),
        source_run_finished_at=datetime.fromisoformat(str(run.get("finished_at", ""))),
        evaluation_evidence=evidence,
    )


def evaluate_candidate_eligibility(
    package: MockAutomationCandidatePackage,
    policy: MockAutomationEligibilityPolicy,
    *, account_ref: str,
) -> MockAutomationEligibilityReceipt:
    reasons = [f"TBD:{name}" for name in policy.tbd_fields]
    if policy.strategy_ref != package.strategy_ref:
        reasons.append("STRATEGY_MISMATCH")
    if policy.candidate_spec_hash != package.candidate_spec_hash:
        reasons.append("CANDIDATE_SPEC_MISMATCH")
    if policy.frozen_at > package.source_run_started_at:
        reasons.append("POLICY_FROZEN_AFTER_FINAL_STARTED")
    fold = package.evaluation_evidence.get("oos_fold", {})
    metrics = {
        "closed_trade_count": _optional_int(fold.get("closed_trade_count")),
        "active_day_count": _optional_int(fold.get("active_day_count")),
        "net_realized_pnl_won": _optional_int(fold.get("net_realized_pnl_won")),
        "max_drawdown_ppm": _optional_int(fold.get("max_drawdown_ppm")),
    }
    if package.evaluation_evidence.get("report_status") != "ELIGIBLE" or fold.get("status") != "ELIGIBLE":
        reasons.append("FINAL_REPORT_NOT_ELIGIBLE")
    comparisons = (
        ("closed_trade_count", policy.minimum_closed_trades, lambda actual, limit: actual >= limit,
         "MINIMUM_CLOSED_TRADES_NOT_MET"),
        ("active_day_count", policy.minimum_active_days, lambda actual, limit: actual >= limit,
         "MINIMUM_ACTIVE_DAYS_NOT_MET"),
        ("net_realized_pnl_won", policy.minimum_net_realized_pnl_won, lambda actual, limit: actual >= limit,
         "MINIMUM_NET_PNL_NOT_MET"),
        ("max_drawdown_ppm", policy.maximum_drawdown_ppm, lambda actual, limit: actual <= limit,
         "MAXIMUM_DRAWDOWN_EXCEEDED"),
    )
    for name, limit, accepted, reason in comparisons:
        actual = metrics[name]
        if limit is not None and (actual is None or not accepted(actual, limit)):
            reasons.append(reason if actual is not None else f"MISSING_METRIC:{name}")
    unique = tuple(dict.fromkeys(reasons))
    return MockAutomationEligibilityReceipt(
        account_ref=account_ref, package_hash=package.package_hash,
        candidate_spec_hash=package.candidate_spec_hash, policy_id=policy.policy_id,
        evaluated_at=package.source_run_finished_at,
        status=(CandidateEligibilityStatus.BLOCKED if unique else CandidateEligibilityStatus.ELIGIBLE),
        reasons=unique, observed_metrics=metrics,
    )


def candidate_package_from_dict(value: Mapping[str, Any]) -> MockAutomationCandidatePackage:
    source = _mapping(value.get("source_final"), "source_final")
    package = MockAutomationCandidatePackage(
        version=str(value.get("version", "")), strategy_ref=str(value.get("strategy_ref", "")),
        candidate_spec=_mapping(value.get("candidate_spec"), "candidate_spec"),
        candidate_spec_hash=str(value.get("candidate_spec_hash", "")),
        scientific_implementation_hash=str(value.get("scientific_implementation_hash", "")),
        source_final_batch_id=str(source.get("batch_id", "")),
        source_final_run_id=str(source.get("run_id", "")),
        source_final_result_hash=str(source.get("result_hash", "")),
        source_run_started_at=datetime.fromisoformat(str(source.get("started_at", ""))),
        source_run_finished_at=datetime.fromisoformat(str(source.get("finished_at", ""))),
        evaluation_evidence=_mapping(value.get("evaluation_evidence"), "evaluation_evidence"),
    )
    if value.get("package_hash") != package.package_hash:
        raise ValueError("candidate package content does not match package_hash")
    return package


def eligibility_policy_from_dict(value: Mapping[str, Any]) -> MockAutomationEligibilityPolicy:
    policy = MockAutomationEligibilityPolicy(
        version=str(value.get("version", "")), strategy_ref=str(value.get("strategy_ref", "")),
        candidate_spec_hash=str(value.get("candidate_spec_hash", "")),
        frozen_at=datetime.fromisoformat(str(value.get("frozen_at", ""))),
        minimum_closed_trades=_optional_int(value.get("minimum_closed_trades")),
        minimum_active_days=_optional_int(value.get("minimum_active_days")),
        minimum_net_realized_pnl_won=_optional_int(value.get("minimum_net_realized_pnl_won")),
        maximum_drawdown_ppm=_optional_int(value.get("maximum_drawdown_ppm")),
    )
    if value.get("policy_id") != policy.policy_id:
        raise ValueError("eligibility policy content does not match policy_id")
    return policy


def eligibility_receipt_from_dict(value: Mapping[str, Any]) -> MockAutomationEligibilityReceipt:
    metrics = _mapping(value.get("observed_metrics"), "observed_metrics")
    receipt = MockAutomationEligibilityReceipt(
        version=str(value.get("version", "")), account_ref=str(value.get("account_ref", "")),
        package_hash=str(value.get("package_hash", "")),
        candidate_spec_hash=str(value.get("candidate_spec_hash", "")),
        policy_id=str(value.get("policy_id", "")),
        evaluated_at=datetime.fromisoformat(str(value.get("evaluated_at", ""))),
        status=CandidateEligibilityStatus(str(value.get("status", ""))),
        reasons=tuple(str(item) for item in value.get("reasons", ())),
        observed_metrics={str(key): _optional_int(item) for key, item in metrics.items()},
    )
    if value.get("receipt_id") != receipt.receipt_id:
        raise ValueError("eligibility receipt content does not match receipt_id")
    return receipt


def validate_publication_size(package: Mapping[str, Any], policy: Mapping[str, Any], receipt: Mapping[str, Any]) -> None:
    encoded = _canonical({"package": package, "policy": policy, "receipt": receipt})
    if len(encoded) > MAX_CANDIDATE_PUBLICATION_BYTES:
        raise ValueError("candidate publication payload is too large")


def _validate_candidate_spec(value: Mapping[str, Any], expected_hash: str) -> None:
    required = {"version", "family", "parameters", "execution_model", "session_profile", "implementation_hash"}
    if set(value) != required or value.get("version") != "final_candidate/v1":
        raise ValueError("candidate_spec must be the canonical final_candidate/v1 document")
    family = str(value.get("family", ""))
    parameters = _mapping(value.get("parameters"), "candidate parameters")
    if parse_strategy_config(family, parameters).to_dict() != dict(parameters):
        raise ValueError("candidate parameters are not canonical")
    execution = _mapping(value.get("execution_model"), "execution_model")
    cost = _mapping(execution.get("cost_model"), "cost_model")
    parsed_cost = SimulationCostModel(**dict(cost))
    parsed_execution = SimulationExecutionConfig(
        version=str(execution.get("version", "")),
        same_bar_path_version=str(execution.get("same_bar_path_version", "")),
        initial_cash_won=execution.get("initial_cash_won"), cost_model=parsed_cost,
    )
    if parsed_execution.to_dict() != dict(execution):
        raise ValueError("candidate execution model is not canonical")
    session = _mapping(value.get("session_profile"), "session_profile")
    profile = str(session.get("profile", ""))
    if not profile.strip():
        raise ValueError("candidate session profile is missing")
    if not research_session_profile_contract_matches(session, profile):
        raise ValueError("candidate session profile is not canonical")
    if _sha256(value) != expected_hash:
        raise ValueError("candidate_spec content does not match candidate_spec_hash")


def _validate_evaluation_evidence(value: Mapping[str, Any], run_id: str) -> None:
    if not str(value.get("report_id", "")).strip():
        raise ValueError("evaluation evidence report_id is required")
    evaluation = _mapping(value.get("evaluation_spec"), "evaluation_spec")
    ResearchEvaluationSpec.from_dict(evaluation)
    fold = _mapping(value.get("oos_fold"), "oos_fold")
    if fold.get("role") != "OOS":
        raise ValueError("evaluation evidence requires exactly one OOS fold")
    if not run_id.strip():
        raise ValueError("evaluation evidence source run is required")


def _single_oos_fold(report: Mapping[str, Any]) -> dict[str, Any]:
    rows = [row for row in report.get("fold_reports", ())
            if isinstance(row, Mapping) and row.get("role") == "OOS"]
    if len(rows) != 1:
        raise ValueError("final research report requires exactly one OOS fold")
    return dict(rows[0])


def _reject_unsafe_values(value: Any, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).casefold()
            if any(token in name for token in ("secret", "token", "password", "pickle", "code_blob", "source_path")):
                raise ValueError(f"candidate package contains forbidden field: {path}{key}")
            _reject_unsafe_values(item, f"{path}{key}.")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_unsafe_values(item, f"{path}{index}.")
    elif isinstance(value, str) and (
        re.match(r"^[A-Za-z]:[\\/]", value) is not None
        or value.startswith("\\\\")
        or value.casefold().startswith("file://")
    ):
        raise ValueError(f"candidate package contains a local path: {path.rstrip('.')}")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError("eligibility metrics and thresholds must be integers or TBD")
    return value


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    return f"{prefix}_{_sha256(value)}"


def _require_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
