"""Frozen forward-evaluation and strategy-stage contracts for broker execution."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from kiwoom_monitor.application.market_session_schedule import (
    SUPPORTED_RESEARCH_SESSION_PROFILES,
    research_session_profile_document,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.domain.strategy_contract import LEADER_BREAKOUT_FAMILY_ID


MOCK_AUTOMATION_SPEC_VERSION = "mock_automation_operating_spec/v1"
MOCK_CANDIDATE_TRANSITION_POLICY = "wait_until_flat"
MOCK_STOP_POLICY = "disable_new_orders_and_reconcile"
MOCK_RECOVERY_POLICY = "broker_reconcile_before_resume"
LEADER_NATIVE_PLAN_MODE = "leader-0b-native-plan/v1"
CLOSE_NATIVE_PLAN_MODE = "close-minute-native-plan/v1"
PANIC_NATIVE_PLAN_MODE = "panic-minute-native-plan/v1"
CLOSE_INPUT_PROTOCOL = "close-shadow-native-plan/v1"
PANIC_INPUT_PROTOCOL = "panic-shadow-native-plan/v1"
CLOSE_ORDER_MANAGEMENT_VERSION = "close-current-order-management/v1"
PANIC_ORDER_MANAGEMENT_VERSION = "panic-current-order-management/v1"
LEADER_ORDER_MANAGEMENT_VERSION = "leader-current-order-management/v1"
CLOSE_ENTRY_COLLECTION = "execution_close_entry_evidence"
CLOSE_EXIT_COLLECTION = "execution_close_exit_evidence"
PANIC_ENTRY_COLLECTION = "execution_panic_entry_evidence"
PANIC_EXIT_COLLECTION = "execution_panic_exit_evidence"


class CloseExitCalendarRecheck(RuntimeError):
    """A scheduled CLOSE exit must be rebuilt before broker submission."""


@dataclass(frozen=True)
class CloseMockExecutionPolicy:
    maximum_adverse_slippage_bps: int
    protective_slippage_bps: int
    entry_price_basis: str
    entry_remainder_policy: str
    exit_price_basis: str
    version: str = "close-mock-execution-policy/v1"
    stopped_management: str | None = None
    accepted_order_management: str | None = None

    def __post_init__(self) -> None:
        if (self.version not in {f"close-mock-execution-policy/v{number}" for number in (1, 2, 3, 4)}
                or self.stopped_management != ('continue_original_plan/v1' if self.version in {
                    'close-mock-execution-policy/v2', 'close-mock-execution-policy/v4'} else None)
                or self.accepted_order_management != ('reassess_then_amend_price/v1' if self.version in {
                    'close-mock-execution-policy/v3', 'close-mock-execution-policy/v4'} else None)
                or self.entry_price_basis != "acknowledged_0B_bounded_by_signal/v1"
                or self.entry_remainder_policy != "cancel_at_entry_deadline/v1"
                or self.exit_price_basis != "acknowledged_0B_bounded_slippage/v1"):
            raise ValueError("unsupported CLOSE execution policy")
        if any(type(value) is not int or not 0 <= value < 10_000 for value in (
            self.maximum_adverse_slippage_bps, self.protective_slippage_bps,
        )):
            raise ValueError("CLOSE slippage bounds must be explicit integer basis points")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.stopped_management is None:
            value.pop('stopped_management')
        if self.accepted_order_management is None:
            value.pop('accepted_order_management')
        return value


def allows_stopped_close_management(spec) -> bool:
    return (spec.execution_plan_mode == CLOSE_NATIVE_PLAN_MODE and spec.close_execution_policy is not None
            and spec.close_execution_policy.stopped_management == 'continue_original_plan/v1')


@dataclass(frozen=True)
class PanicMockExecutionPolicy:
    maximum_adverse_slippage_bps: int
    protective_slippage_bps: int
    stopped_management: str
    version: str = 'panic-mock-execution-policy/v1'

    def __post_init__(self):
        if (self.version != 'panic-mock-execution-policy/v1'
                or self.stopped_management not in {'continue_original_plan/v1', 'reconcile_only/v1'}
                or any(type(value) is not int or not 0 <= value < 10_000 for value in
                       (self.maximum_adverse_slippage_bps, self.protective_slippage_bps))):
            raise ValueError('PANIC requires explicit price bounds and stopped management')

    @property
    def accepted_order_management(self):
        return 'reassess_then_amend_price/v1'

    def to_dict(self):
        return asdict(self)


def allows_stopped_panic_management(spec) -> bool:
    return (spec.execution_plan_mode == PANIC_NATIVE_PLAN_MODE and spec.panic_execution_policy is not None
            and spec.panic_execution_policy.stopped_management == 'continue_original_plan/v1')


class StrategyLifecycleStage(StrEnum):
    DRAFT = "draft"
    EVALUATED = "evaluated"
    VALIDATED = "validated"
    SHADOW = "shadow"
    BROKER_MOCK_VALIDATED = "broker_mock_validated"
    APPROVED_FOR_LIVE = "approved_for_live"
    RETIRED = "retired"


class ForwardReportStatus(StrEnum):
    BLOCKED = "BLOCKED"
    PENDING = "PENDING"
    FAILED = "FAILED"
    PASSED = "PASSED"


class MockAutomationReadinessStatus(StrEnum):
    BLOCKED = "BLOCKED"
    READY = "READY"


@dataclass(frozen=True)
class ForwardCriteria:
    minimum_comparable_observations: int | None = None
    minimum_coverage_ppm: int | None = None
    maximum_p95_delay_ms: int | None = None
    maximum_gap_count: int | None = None
    maximum_submission_unknown_count: int | None = None
    maximum_rejected_order_count: int | None = None
    maximum_cancel_failure_count: int | None = None
    maximum_reconnect_count: int | None = None
    maximum_balance_mismatch_count: int | None = None
    minimum_active_day_count: int | None = None
    minimum_closed_trade_count: int | None = None
    minimum_adjusted_net_pnl_won: int | None = None
    maximum_drawdown_ppm: int | None = None
    maximum_exposure_ppm: int | None = None

    def __post_init__(self) -> None:
        values = asdict(self)
        for name, value in values.items():
            if value is not None and not isinstance(value, int):
                raise ValueError(f"{name} must be an integer or TBD")
            if value is not None and value < 0 and name != "minimum_adjusted_net_pnl_won":
                raise ValueError(f"{name} must not be negative")
        for name in ("minimum_coverage_ppm", "maximum_drawdown_ppm", "maximum_exposure_ppm"):
            value = values[name]
            if value is not None and value > 1_000_000:
                raise ValueError(f"{name} must be between 0 and 1,000,000 ppm")

    @property
    def tbd_fields(self) -> tuple[str, ...]:
        return tuple(name for name, value in asdict(self).items() if value is None)

    def to_dict(self) -> dict[str, int | None]:
        return asdict(self)


@dataclass(frozen=True)
class ForwardEvaluationSpec:
    strategy_ref: str
    family_version: str
    factor_versions: tuple[str, ...]
    policy_version: str
    data_path: str
    account_ref: str
    environment: str
    evaluation_start: datetime
    evaluation_end: datetime
    frozen_at: datetime
    criteria: ForwardCriteria
    session_profile: str | None = None

    def __post_init__(self) -> None:
        for name in ("strategy_ref", "family_version", "policy_version", "account_ref"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if not self.factor_versions or any(not value.strip() for value in self.factor_versions):
            raise ValueError("factor_versions must contain at least one version")
        if len(set(self.factor_versions)) != len(self.factor_versions):
            raise ValueError("factor_versions must not contain duplicates")
        if self.data_path not in {"nas", "direct"}:
            raise ValueError("data_path must be nas or direct")
        if self.environment != "mock":
            raise ValueError("O2a forward evaluation supports only the mock environment")
        if (
            self.session_profile is not None
            and self.session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES
        ):
            raise ValueError(f"unsupported research session profile: {self.session_profile}")
        for name in ("evaluation_start", "evaluation_end", "frozen_at"):
            _require_aware(getattr(self, name), name)
        if self.evaluation_end <= self.evaluation_start:
            raise ValueError("evaluation_end must be later than evaluation_start")
        if self.frozen_at > self.evaluation_start:
            raise ValueError("the forward profile must be frozen before evaluation starts")

    @property
    def profile_id(self) -> str:
        return _content_id("forward_profile", self._identity_document())

    def to_dict(self) -> dict[str, Any]:
        return {"profile_id": self.profile_id, **self._identity_document()}

    def _identity_document(self) -> dict[str, Any]:
        document = {
            "strategy_ref": self.strategy_ref,
            "family_version": self.family_version,
            "factor_versions": list(self.factor_versions),
            "policy_version": self.policy_version,
            "data_path": self.data_path,
            "account_ref": self.account_ref,
            "environment": self.environment,
            "evaluation_start": self.evaluation_start.isoformat(),
            "evaluation_end": self.evaluation_end.isoformat(),
            "frozen_at": self.frozen_at.isoformat(),
            "criteria": self.criteria.to_dict(),
        }
        if self.session_profile is not None:
            document["session_profile"] = research_session_profile_document(self.session_profile)
        return document


@dataclass(frozen=True)
class MockUnfilledExecutionPolicy:
    """Explicit frozen mock-only replacement limits; absent means execution OFF."""
    maximum_reprices: int
    maximum_adverse_slippage_bps: int
    protective_slippage_bps: int
    maximum_management_seconds: int
    max_snapshot_age_seconds: int
    minimum_net_advantage_won: int
    uncertainty_multiplier: float
    minimum_paired_samples: int
    reprice_horizon_seconds: int
    allow_supplied_paired_mock_evidence: bool = False
    version: str = "mock-unfilled-execution-policy/v1"
    maximum_protective_cancel_retries: int = 0
    protective_cancel_retry_delay_seconds: int = 0

    def __post_init__(self):
        if self.version not in {"mock-unfilled-execution-policy/v1", "mock-unfilled-execution-policy/v2"}:
            raise ValueError("unsupported mock unfilled execution policy")
        for name, minimum, maximum in (
            ("maximum_reprices", 0, 64), ("maximum_adverse_slippage_bps", 0, 10000),
            ("protective_slippage_bps", 0, 10000), ("maximum_management_seconds", 1, 3600),
            ("max_snapshot_age_seconds", 1, 3600), ("minimum_net_advantage_won", 0, 10**12),
            ("minimum_paired_samples", 2, 10000), ("reprice_horizon_seconds", 1, 3600),
        ):
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"invalid mock unfilled policy {name}")
        if (type(self.uncertainty_multiplier) not in (int, float)
                or not math.isfinite(self.uncertainty_multiplier) or self.uncertainty_multiplier < 0
                or type(self.allow_supplied_paired_mock_evidence) is not bool):
            raise ValueError("invalid mock unfilled evidence policy")
        if (type(self.maximum_protective_cancel_retries) is not int
                or not 0 <= self.maximum_protective_cancel_retries <= 3
                or type(self.protective_cancel_retry_delay_seconds) is not int
                or (self.maximum_protective_cancel_retries == 0 and self.protective_cancel_retry_delay_seconds != 0)
                or (self.maximum_protective_cancel_retries > 0
                    and not 1 <= self.protective_cancel_retry_delay_seconds <= self.maximum_management_seconds)):
            raise ValueError('invalid protective cancel retry policy')

    @property
    def accepted_order_management(self):
        return 'reassess_then_amend_price/v1' if self.version == 'mock-unfilled-execution-policy/v2' else None

    @property
    def policy_id(self):
        return _content_id("mock_unfilled_policy", self.to_dict())

    def to_dict(self):
        value=asdict(self)
        if self.maximum_protective_cancel_retries == 0:
            value.pop('maximum_protective_cancel_retries');value.pop('protective_cancel_retry_delay_seconds')
        return value


@dataclass(frozen=True)
class MockAutomationOperatingSpec:
    """Frozen O2-M limits. A complete spec still does not enable order transport."""

    strategy_ref: str
    candidate_package_hash: str
    final_result_hash: str
    final_batch_id: str
    final_run_id: str
    forward_profile_id: str
    account_scope: AccountScope
    credential_profile_id: str
    binding_revision: int
    binding_verified_at: datetime
    frozen_at: datetime
    shadow_evidence_ref: str | None = None
    maximum_concurrent_strategies: int | None = None
    maximum_concurrent_positions: int | None = None
    maximum_capital_won: int | None = None
    maximum_daily_loss_won: int | None = None
    maximum_data_gap_seconds: int | None = None
    maximum_submission_unknown_count: int | None = None
    maximum_reconnect_count: int | None = None
    maximum_balance_mismatch_count: int | None = None
    supported_venue: str | None = None
    session_profile: str | None = None
    data_path: str | None = None
    candidate_transition_policy: str | None = None
    stop_policy: str | None = None
    recovery_policy: str | None = None
    version: str = MOCK_AUTOMATION_SPEC_VERSION
    unfilled_execution_policy: MockUnfilledExecutionPolicy | None = None
    execution_plan_mode: str | None = None
    close_execution_policy: CloseMockExecutionPolicy | None = None
    panic_execution_policy: PanicMockExecutionPolicy | None = None

    def __post_init__(self) -> None:
        if self.execution_plan_mode == PANIC_NATIVE_PLAN_MODE:
            if (not isinstance(self.panic_execution_policy, PanicMockExecutionPolicy) or self.supported_venue not in {'KRX', 'NXT', 'SOR'}
                    or self.unfilled_execution_policy is not None or self.close_execution_policy is not None):
                raise ValueError('PANIC native plan requires its explicit KRX execution policy or an NXT plan')
        elif self.execution_plan_mode == CLOSE_NATIVE_PLAN_MODE:
            if (not isinstance(self.close_execution_policy, CloseMockExecutionPolicy)
                    or self.supported_venue not in {'KRX', 'NXT', 'SOR'} or self.unfilled_execution_policy is not None):
                raise ValueError("CLOSE native plan requires its explicit KRX execution policy or an NXT plan")
        elif self.execution_plan_mode is not None and (
                self.execution_plan_mode != LEADER_NATIVE_PLAN_MODE or self.unfilled_execution_policy is None
                or self.supported_venue not in {'KRX', 'NXT', 'SOR'}):
            raise ValueError("leader native plan mode requires the explicit KRX mock unfilled policy or an NXT plan")
        if self.close_execution_policy is not None and self.execution_plan_mode != CLOSE_NATIVE_PLAN_MODE:
            raise ValueError("CLOSE execution policy requires its native plan mode")
        if self.panic_execution_policy is not None and self.execution_plan_mode != PANIC_NATIVE_PLAN_MODE:
            raise ValueError('PANIC execution policy requires its native plan mode')
        if self.unfilled_execution_policy is not None:
            if not isinstance(self.unfilled_execution_policy, MockUnfilledExecutionPolicy):
                raise ValueError("mock unfilled execution policy must be typed")
            if (self.unfilled_execution_policy.accepted_order_management is not None
                    and self.execution_plan_mode != LEADER_NATIVE_PLAN_MODE):
                raise ValueError("current leader order management requires native plan mode")
            if (self.maximum_data_gap_seconds is not None
                    and self.unfilled_execution_policy.max_snapshot_age_seconds > self.maximum_data_gap_seconds):
                raise ValueError("mock unfilled freshness exceeds operating limit")
        for name in (
            "strategy_ref", "final_batch_id", "final_run_id", "forward_profile_id",
            "credential_profile_id",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        _require_sha256(self.candidate_package_hash, "candidate_package_hash")
        _require_sha256(self.final_result_hash, "final_result_hash")
        if self.version != MOCK_AUTOMATION_SPEC_VERSION:
            raise ValueError("unsupported mock automation operating spec version")
        if self.account_scope.environment is not AccountEnvironment.MOCK:
            raise ValueError("mock automation requires a verified mock account scope")
        if self.binding_revision <= 0:
            raise ValueError("binding_revision must be positive")
        _require_aware(self.binding_verified_at, "binding_verified_at")
        _require_aware(self.frozen_at, "frozen_at")
        if self.frozen_at < self.binding_verified_at:
            raise ValueError("operating spec cannot predate account verification")

        positive = (
            "maximum_concurrent_strategies", "maximum_concurrent_positions",
            "maximum_capital_won", "maximum_daily_loss_won",
        )
        nonnegative = (
            "maximum_data_gap_seconds", "maximum_submission_unknown_count",
            "maximum_reconnect_count", "maximum_balance_mismatch_count",
        )
        for name in positive:
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value <= 0):
                raise ValueError(f"{name} must be a positive integer or TBD")
        for name in nonnegative:
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or TBD")
        if self.supported_venue not in {None, 'KRX', 'NXT', 'SOR'}:
            raise ValueError("unsupported automation execution venue")
        if self.supported_venue in {'NXT', 'SOR'}:
            terms = self.unfilled_execution_policy or self.close_execution_policy or self.panic_execution_policy
            if getattr(terms, 'accepted_order_management', None) != 'reassess_then_amend_price/v1':
                raise ValueError('NXT/SOR automation requires a native reassessment and amendment policy')
        if (
            self.session_profile is not None
            and self.session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES
        ):
            raise ValueError(f"unsupported research session profile: {self.session_profile}")
        if self.data_path is not None and self.data_path not in {"nas", "direct"}:
            raise ValueError("data_path must be nas, direct or TBD")
        allowed_policies = {
            "candidate_transition_policy": MOCK_CANDIDATE_TRANSITION_POLICY,
            "stop_policy": MOCK_STOP_POLICY,
            "recovery_policy": MOCK_RECOVERY_POLICY,
        }
        for name, expected in allowed_policies.items():
            value = getattr(self, name)
            if value is not None and value != expected:
                raise ValueError(f"unsupported {name}: {value}")

    @property
    def tbd_fields(self) -> tuple[str, ...]:
        names = (
            "shadow_evidence_ref", "maximum_concurrent_strategies",
            "maximum_concurrent_positions", "maximum_capital_won",
            "maximum_daily_loss_won", "maximum_data_gap_seconds",
            "maximum_submission_unknown_count", "maximum_reconnect_count",
            "maximum_balance_mismatch_count", "supported_venue", "session_profile",
            "data_path", "candidate_transition_policy", "stop_policy", "recovery_policy",
        )
        return tuple(name for name in names if getattr(self, name) in {None, ""})

    @property
    def spec_id(self) -> str:
        return _content_id("mock_automation_spec", self._identity_document())

    def to_dict(self) -> dict[str, Any]:
        return {"spec_id": self.spec_id, **self._identity_document()}

    def _identity_document(self) -> dict[str, Any]:
        document = {
            "version": self.version,
            "strategy_ref": self.strategy_ref,
            "candidate_package_hash": self.candidate_package_hash,
            "final_result_hash": self.final_result_hash,
            "final_batch_id": self.final_batch_id,
            "final_run_id": self.final_run_id,
            "forward_profile_id": self.forward_profile_id,
            "account_scope": self.account_scope.to_dict(),
            "credential_profile_id": self.credential_profile_id,
            "binding_revision": self.binding_revision,
            "binding_verified_at": self.binding_verified_at.isoformat(),
            "frozen_at": self.frozen_at.isoformat(),
            "shadow_evidence_ref": self.shadow_evidence_ref,
            "maximum_concurrent_strategies": self.maximum_concurrent_strategies,
            "maximum_concurrent_positions": self.maximum_concurrent_positions,
            "maximum_capital_won": self.maximum_capital_won,
            "maximum_daily_loss_won": self.maximum_daily_loss_won,
            "maximum_data_gap_seconds": self.maximum_data_gap_seconds,
            "maximum_submission_unknown_count": self.maximum_submission_unknown_count,
            "maximum_reconnect_count": self.maximum_reconnect_count,
            "maximum_balance_mismatch_count": self.maximum_balance_mismatch_count,
            "supported_venue": self.supported_venue,
            "session_profile": self.session_profile,
            "data_path": self.data_path,
            "candidate_transition_policy": self.candidate_transition_policy,
            "stop_policy": self.stop_policy,
            "recovery_policy": self.recovery_policy,
        }
        if self.unfilled_execution_policy is not None:
            document["unfilled_execution_policy"] = self.unfilled_execution_policy.to_dict()
        if self.execution_plan_mode is not None:
            document["execution_plan_mode"] = self.execution_plan_mode
        if self.close_execution_policy is not None:
            document["close_execution_policy"] = self.close_execution_policy.to_dict()
        if self.panic_execution_policy is not None:
            document['panic_execution_policy'] = self.panic_execution_policy.to_dict()
        return document


@dataclass(frozen=True)
class MockAutomationAccountLimits:
    """Explicit shared-account bounds; no trading values are inferred."""
    maximum_concurrent_strategies: int
    maximum_concurrent_positions: int
    maximum_capital_won: int
    maximum_daily_loss_won: int
    maximum_data_gap_seconds: int
    maximum_submission_unknown_count: int
    maximum_reconnect_count: int
    maximum_balance_mismatch_count: int
    version: str = 'mock_automation_account_limits/v1'

    def __post_init__(self):
        if self.version != 'mock_automation_account_limits/v1':
            raise ValueError('unsupported mock account limits version')
        positive = {'maximum_concurrent_strategies', 'maximum_concurrent_positions',
                    'maximum_capital_won', 'maximum_daily_loss_won'}
        for name, value in self.to_dict().items():
            if name != 'version' and (type(value) is not int or value < (1 if name in positive else 0)):
                raise ValueError('explicit integer account limit required: ' + name)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class MockAutomationAccountSelection:
    """Frozen composition for one account, separate from execution authority.

    Members retain their original candidate/strategy policies. Shared bounds
    and ordered selection are explicit; publishing this does not admit a run.
    The first member is the declared execution anchor, not a ranking default.
    """
    account_scope: AccountScope
    credential_profile_id: str
    binding_revision: int
    binding_verified_at: datetime
    frozen_at: datetime
    member_spec_ids: tuple[str, ...]
    limits: MockAutomationAccountLimits
    version: str = 'mock_automation_account_selection/v1'
    execution_order_spec_ids: tuple[str, ...] | None = None

    def __post_init__(self):
        if self.version != 'mock_automation_account_selection/v1' and not (
                self.version == 'mock_automation_account_selection/v2'
                and isinstance(self, MockAutomationAccountSelectionV2)):
            raise ValueError('unsupported mock account selection version')
        if not isinstance(self.account_scope, AccountScope) or self.account_scope.environment is not AccountEnvironment.MOCK:
            raise ValueError('account selection requires a verified mock scope')
        if type(self.credential_profile_id) is not str or not self.credential_profile_id.strip():
            raise ValueError('account selection requires its credential profile')
        if type(self.binding_revision) is not int or self.binding_revision <= 0:
            raise ValueError('account selection requires a positive binding revision')
        if not isinstance(self.binding_verified_at, datetime) or not isinstance(self.frozen_at, datetime):
            raise ValueError('account selection requires explicit aware times')
        _require_aware(self.binding_verified_at, 'binding_verified_at')
        _require_aware(self.frozen_at, 'frozen_at')
        if self.frozen_at < self.binding_verified_at:
            raise ValueError('account selection predates verified binding')
        if type(self.member_spec_ids) is not tuple or not self.member_spec_ids:
            raise ValueError('account selection requires explicit ordered members')
        for identity in self.member_spec_ids:
            prefix = 'mock_automation_spec_'
            if type(identity) is not str or not identity.startswith(prefix):
                raise ValueError('account selection requires a frozen operating spec ID')
            _require_sha256(identity[len(prefix):], 'member_spec_id')
        if len(set(self.member_spec_ids)) != len(self.member_spec_ids):
            raise ValueError('account selection contains duplicate members')
        if not isinstance(self.limits, MockAutomationAccountLimits):
            raise ValueError('account selection requires explicit typed account limits')
        if self.execution_order_spec_ids is not None and (
                type(self.execution_order_spec_ids) is not tuple
                or len(self.execution_order_spec_ids) != len(self.member_spec_ids)
                or set(self.execution_order_spec_ids) != set(self.member_spec_ids)):
            raise ValueError('account execution order must explicitly name every member once')

    @property
    def primary_spec_id(self):
        return self.member_spec_ids[0]

    def allows_entry(self, spec_id: str) -> bool:
        return spec_id in self.member_spec_ids

    def matches_admission(self, *, spec_id: str, admission_id: str,
                          original_selection_id: str | None, execution_run_id: str) -> bool:
        """Match references only; the caller still verifies the native document and run."""
        return (spec_id in self.member_spec_ids and bool(admission_id) and bool(execution_run_id)
                and original_selection_id == self.selection_id)

    @property
    def selection_id(self):
        return _content_id('mock_automation_account_selection', self._identity_document())

    def _identity_document(self):
        document = dict(version=self.version, account_scope=self.account_scope.to_dict(),
            credential_profile_id=self.credential_profile_id, binding_revision=self.binding_revision,
            binding_verified_at=self.binding_verified_at.isoformat(), frozen_at=self.frozen_at.isoformat(),
            member_spec_ids=list(self.member_spec_ids), limits=self.limits.to_dict())
        if self.execution_order_spec_ids is not None:
            document['execution_order_spec_ids'] = list(self.execution_order_spec_ids)
        return document

    def to_dict(self):
        return dict(selection_id=self.selection_id, **self._identity_document())


@dataclass(frozen=True)
class MockAutomationRetainedAdmissionRef:
    spec_id: str
    admission_id: str
    original_selection_id: str | None
    execution_run_id: str

    def __post_init__(self):
        for name, prefix in (('spec_id', 'mock_automation_spec_'),
                             ('admission_id', 'mock_automation_admission_'),
                             ('execution_run_id', 'mock_auto_run_')):
            value = getattr(self, name)
            if type(value) is not str or not value.startswith(prefix):
                raise ValueError('invalid retained admission reference: ' + name)
            _require_sha256(value[len(prefix):], name)
        if self.original_selection_id is not None:
            prefix = 'mock_automation_account_selection_'
            if type(self.original_selection_id) is not str or not self.original_selection_id.startswith(prefix):
                raise ValueError('invalid retained admission original selection')
            _require_sha256(self.original_selection_id[len(prefix):], 'original_selection_id')

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True, kw_only=True)
class MockAutomationAccountSelectionV2(MockAutomationAccountSelection):
    entry_spec_ids: tuple[str, ...]
    retained_admission_refs: tuple[MockAutomationRetainedAdmissionRef, ...]
    version: str = 'mock_automation_account_selection/v2'

    def __post_init__(self):
        super().__post_init__()
        if (self.execution_order_spec_ids is None or type(self.entry_spec_ids) is not tuple
                or len(set(self.entry_spec_ids)) != len(self.entry_spec_ids)
                or not set(self.entry_spec_ids) <= set(self.member_spec_ids)
                or type(self.retained_admission_refs) is not tuple or not self.retained_admission_refs
                or any(not isinstance(value, MockAutomationRetainedAdmissionRef) for value in self.retained_admission_refs)):
            raise ValueError('replacement requires explicit entries, order and retained admissions')
        identities = tuple(value.spec_id for value in self.retained_admission_refs)
        if (len(set(identities)) != len(identities) or self.primary_spec_id not in identities
                or set(self.member_spec_ids) != set(identities) | set(self.entry_spec_ids)
                or len({value.execution_run_id for value in self.retained_admission_refs}) != 1):
            raise ValueError('replacement retained membership or original run conflicts')

    def allows_entry(self, spec_id: str) -> bool:
        return spec_id in self.entry_spec_ids

    def retained_admission_ref(self, spec_id: str) -> MockAutomationRetainedAdmissionRef | None:
        return next((value for value in self.retained_admission_refs if value.spec_id == spec_id), None)

    def matches_admission(self, *, spec_id: str, admission_id: str,
                          original_selection_id: str | None, execution_run_id: str) -> bool:
        retained = self.retained_admission_ref(spec_id)
        if retained is not None:
            return (admission_id, original_selection_id, execution_run_id) == (
                retained.admission_id, retained.original_selection_id, retained.execution_run_id)
        return (spec_id in self.entry_spec_ids and bool(admission_id)
                and original_selection_id == self.selection_id
                and execution_run_id == self.retained_admission_refs[0].execution_run_id)

    def _identity_document(self):
        return {**super()._identity_document(), 'entry_spec_ids': list(self.entry_spec_ids),
                'retained_admission_refs': [value.to_dict() for value in self.retained_admission_refs]}


def mock_automation_account_selection_from_dict(value):
    raw = dict(value)
    identity = raw.pop('selection_id')
    scope = dict(raw['account_scope'])
    scope['environment'] = AccountEnvironment(scope['environment'])
    raw.update(account_scope=AccountScope(**scope),
        binding_verified_at=datetime.fromisoformat(raw['binding_verified_at']),
        frozen_at=datetime.fromisoformat(raw['frozen_at']),
        limits=MockAutomationAccountLimits(**raw['limits']))
    if type(raw['member_spec_ids']) is not list:
        raise ValueError('account selection members must be a JSON array')
    raw['member_spec_ids'] = tuple(raw['member_spec_ids'])
    if 'execution_order_spec_ids' in raw:
        if type(raw['execution_order_spec_ids']) is not list:
            raise ValueError('account execution order must be a JSON array')
        raw['execution_order_spec_ids'] = tuple(raw['execution_order_spec_ids'])
    constructor = MockAutomationAccountSelection
    if raw.get('version') == 'mock_automation_account_selection/v2':
        if type(raw.get('entry_spec_ids')) is not list or type(raw.get('retained_admission_refs')) is not list:
            raise ValueError('replacement entry and retained references must be JSON arrays')
        raw['entry_spec_ids'] = tuple(raw['entry_spec_ids'])
        raw['retained_admission_refs'] = tuple(MockAutomationRetainedAdmissionRef(**row)
                                                for row in raw['retained_admission_refs'])
        constructor = MockAutomationAccountSelectionV2
    result = constructor(**raw)
    if identity != result.selection_id or result.to_dict() != dict(value):
        raise ValueError('mock account selection canonical content conflict')
    return result


@dataclass(frozen=True)
class MockAutomationReadiness:
    spec_id: str
    status: MockAutomationReadinessStatus
    reasons: tuple[str, ...]


def assess_mock_automation_readiness(
    spec: MockAutomationOperatingSpec,
    profile: ForwardEvaluationSpec,
    *,
    strategy_stage: StrategyLifecycleStage,
) -> MockAutomationReadiness:
    reasons = [f"TBD:{name}" for name in spec.tbd_fields]
    if profile.profile_id != spec.forward_profile_id:
        reasons.append("FORWARD_PROFILE_MISMATCH")
    if profile.strategy_ref != spec.strategy_ref:
        reasons.append("STRATEGY_MISMATCH")
    if profile.account_ref != spec.account_scope.account_ref:
        reasons.append("ACCOUNT_SCOPE_MISMATCH")
    if profile.environment != "mock":
        reasons.append("FORWARD_PROFILE_NOT_MOCK")
    if spec.execution_plan_mode == LEADER_NATIVE_PLAN_MODE and profile.family_version != LEADER_BREAKOUT_FAMILY_ID:
        reasons.append("NATIVE_PLAN_MODE_REQUIRES_LEADER_0B_FAMILY")
    if spec.execution_plan_mode == PANIC_NATIVE_PLAN_MODE and profile.family_version != 'panic_rebound/v1':
        reasons.append('NATIVE_PLAN_MODE_REQUIRES_PANIC_FAMILY')
    if spec.execution_plan_mode == CLOSE_NATIVE_PLAN_MODE and profile.family_version not in {
        'close_betting/v1', 'ipo_close/v1', 'ipo_breakout/v1',
    }:
        reasons.append("NATIVE_PLAN_MODE_REQUIRES_CLOSE_FAMILY")
    if profile.criteria.tbd_fields:
        reasons.extend(f"FORWARD_CRITERIA_TBD:{name}" for name in profile.criteria.tbd_fields)
    if profile.session_profile != spec.session_profile:
        reasons.append("SESSION_PROFILE_MISMATCH")
    if profile.data_path != spec.data_path:
        reasons.append("DATA_PATH_MISMATCH")
    if spec.frozen_at > profile.evaluation_start:
        reasons.append("SPEC_FROZEN_AFTER_EVALUATION_START")
    if strategy_stage is not StrategyLifecycleStage.SHADOW:
        reasons.append("STRATEGY_NOT_IN_SHADOW")
    if (
        spec.maximum_concurrent_strategies is not None
        and spec.maximum_concurrent_strategies != 1
    ):
        reasons.append("UNSUPPORTED_CONCURRENT_STRATEGY_COUNT")
    if (
        spec.maximum_concurrent_positions is not None
        and spec.maximum_concurrent_positions != 1
    ):
        reasons.append("UNSUPPORTED_CONCURRENT_POSITION_COUNT")
    if spec.session_profile is not None and spec.session_profile != "krx-regular/v1":
        reasons.append("UNSUPPORTED_MOCK_SESSION_PROFILE")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return MockAutomationReadiness(
        spec_id=spec.spec_id,
        status=(
            MockAutomationReadinessStatus.BLOCKED
            if unique_reasons
            else MockAutomationReadinessStatus.READY
        ),
        reasons=unique_reasons,
    )


@dataclass(frozen=True)
class ForwardEvidence:
    profile_id: str
    as_of: datetime
    finalized: bool
    comparable_observation_count: int
    coverage_ppm: int | None
    p95_delay_ms: int | None
    gap_count: int
    submission_unknown_count: int
    rejected_order_count: int
    cancel_failure_count: int
    reconnect_count: int
    balance_mismatch_count: int
    no_trade_count: int
    active_day_count: int
    closed_trade_count: int
    broker_net_pnl_won: int | None
    broker_reported_cost_won: int | None
    additional_unmodeled_cost_won: int | None
    max_drawdown_ppm: int | None
    exposure_ppm: int | None

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("profile_id is required")
        _require_aware(self.as_of, "as_of")
        nonnegative = (
            "comparable_observation_count", "gap_count", "submission_unknown_count",
            "rejected_order_count", "cancel_failure_count", "reconnect_count",
            "balance_mismatch_count", "no_trade_count", "active_day_count",
            "closed_trade_count", "broker_reported_cost_won",
            "additional_unmodeled_cost_won", "max_drawdown_ppm", "exposure_ppm",
        )
        for name in nonnegative:
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer when known")
        if self.coverage_ppm is not None and (
            not isinstance(self.coverage_ppm, int) or not 0 <= self.coverage_ppm <= 1_000_000
        ):
            raise ValueError("coverage_ppm must be an integer between 0 and 1,000,000")
        if self.p95_delay_ms is not None and (
            not isinstance(self.p95_delay_ms, int) or self.p95_delay_ms < 0
        ):
            raise ValueError("p95_delay_ms must be a non-negative integer")
        for name in ("max_drawdown_ppm", "exposure_ppm"):
            value = getattr(self, name)
            if value is not None and value > 1_000_000:
                raise ValueError(f"{name} must not exceed 1,000,000 ppm")

    @property
    def adjusted_net_pnl_won(self) -> int | None:
        if self.broker_net_pnl_won is None or self.additional_unmodeled_cost_won is None:
            return None
        return self.broker_net_pnl_won - self.additional_unmodeled_cost_won

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        document["as_of"] = self.as_of.isoformat()
        document["adjusted_net_pnl_won"] = self.adjusted_net_pnl_won
        return document


@dataclass(frozen=True)
class StrategyStageRevision:
    revision_id: str
    strategy_ref: str
    previous_stage: StrategyLifecycleStage
    stage: StrategyLifecycleStage
    evidence_refs: tuple[str, ...]
    reason: str
    decided_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "strategy_ref": self.strategy_ref,
            "previous_stage": self.previous_stage.value,
            "stage": self.stage.value,
            "evidence_refs": list(self.evidence_refs),
            "reason": self.reason,
            "decided_at": self.decided_at.isoformat(),
        }


def strategy_stage_revision_sort_key(revision: StrategyStageRevision):
    """Equal-time stage evidence follows its lifecycle, without rewriting event times or IDs."""
    return revision.decided_at, tuple(StrategyLifecycleStage).index(revision.stage), revision.revision_id


_NEXT_STAGE = {
    StrategyLifecycleStage.DRAFT: StrategyLifecycleStage.EVALUATED,
    StrategyLifecycleStage.EVALUATED: StrategyLifecycleStage.VALIDATED,
    StrategyLifecycleStage.VALIDATED: StrategyLifecycleStage.SHADOW,
    StrategyLifecycleStage.SHADOW: StrategyLifecycleStage.BROKER_MOCK_VALIDATED,
}


def strategy_stage_revision(
    *, strategy_ref: str, previous_stage: StrategyLifecycleStage,
    stage: StrategyLifecycleStage, evidence_refs: tuple[str, ...],
    reason: str, decided_at: datetime,
) -> StrategyStageRevision:
    if not strategy_ref.strip() or not reason.strip():
        raise ValueError("strategy_ref and reason are required")
    _require_aware(decided_at, "decided_at")
    if stage is StrategyLifecycleStage.APPROVED_FOR_LIVE:
        raise ValueError("approved_for_live requires the separate O2b live approval boundary")
    if stage is not StrategyLifecycleStage.RETIRED and _NEXT_STAGE.get(previous_stage) is not stage:
        raise ValueError(f"invalid strategy stage transition: {previous_stage.value} -> {stage.value}")
    refs = tuple(dict.fromkeys(value.strip() for value in evidence_refs if value.strip()))
    if stage is not StrategyLifecycleStage.RETIRED and not refs:
        raise ValueError("stage promotion requires immutable evidence references")
    body: Mapping[str, Any] = {
        "strategy_ref": strategy_ref,
        "previous_stage": previous_stage.value,
        "stage": stage.value,
        "evidence_refs": list(refs),
        "reason": reason,
        "decided_at": decided_at.isoformat(),
    }
    return StrategyStageRevision(
        revision_id=_content_id("strategy_stage", body), strategy_ref=strategy_ref,
        previous_stage=previous_stage, stage=stage, evidence_refs=refs,
        reason=reason, decided_at=decided_at,
    )


def forward_spec_from_dict(value: Mapping[str, Any]) -> ForwardEvaluationSpec:
    criteria = value.get("criteria")
    if not isinstance(criteria, Mapping):
        raise ValueError("forward profile criteria are missing")
    spec = ForwardEvaluationSpec(
        strategy_ref=str(value["strategy_ref"]),
        family_version=str(value["family_version"]),
        factor_versions=tuple(str(item) for item in value["factor_versions"]),
        policy_version=str(value["policy_version"]),
        data_path=str(value["data_path"]), account_ref=str(value["account_ref"]),
        environment=str(value["environment"]),
        evaluation_start=datetime.fromisoformat(str(value["evaluation_start"])),
        evaluation_end=datetime.fromisoformat(str(value["evaluation_end"])),
        frozen_at=datetime.fromisoformat(str(value["frozen_at"])),
        criteria=ForwardCriteria(**{name: criteria.get(name) for name in ForwardCriteria.__dataclass_fields__}),
        session_profile=(
            str(value["session_profile"].get("profile", ""))
            if isinstance(value.get("session_profile"), Mapping)
            else str(value["session_profile"])
            if value.get("session_profile") is not None
            else None
        ),
    )
    expected = str(value.get("profile_id", spec.profile_id))
    if expected != spec.profile_id:
        raise ValueError("forward profile content does not match profile_id")
    return spec


def mock_automation_spec_from_dict(value: Mapping[str, Any]) -> MockAutomationOperatingSpec:
    scope = value.get("account_scope")
    if not isinstance(scope, Mapping):
        raise ValueError("mock automation account_scope is missing")
    spec = MockAutomationOperatingSpec(
        version=str(value.get("version", "")),
        strategy_ref=str(value["strategy_ref"]),
        candidate_package_hash=str(value["candidate_package_hash"]),
        final_result_hash=str(value["final_result_hash"]),
        final_batch_id=str(value["final_batch_id"]),
        final_run_id=str(value["final_run_id"]),
        forward_profile_id=str(value["forward_profile_id"]),
        account_scope=AccountScope(
            broker=str(scope["broker"]),
            environment=AccountEnvironment(str(scope["environment"])),
            account_ref=str(scope["account_ref"]),
        ),
        credential_profile_id=str(value["credential_profile_id"]),
        binding_revision=int(value["binding_revision"]),
        binding_verified_at=datetime.fromisoformat(str(value["binding_verified_at"])),
        frozen_at=datetime.fromisoformat(str(value["frozen_at"])),
        shadow_evidence_ref=_optional_text(value.get("shadow_evidence_ref")),
        maximum_concurrent_strategies=_optional_int(value.get("maximum_concurrent_strategies")),
        maximum_concurrent_positions=_optional_int(value.get("maximum_concurrent_positions")),
        maximum_capital_won=_optional_int(value.get("maximum_capital_won")),
        maximum_daily_loss_won=_optional_int(value.get("maximum_daily_loss_won")),
        maximum_data_gap_seconds=_optional_int(value.get("maximum_data_gap_seconds")),
        maximum_submission_unknown_count=_optional_int(
            value.get("maximum_submission_unknown_count")
        ),
        maximum_reconnect_count=_optional_int(value.get("maximum_reconnect_count")),
        maximum_balance_mismatch_count=_optional_int(
            value.get("maximum_balance_mismatch_count")
        ),
        supported_venue=_optional_text(value.get("supported_venue")),
        session_profile=_optional_text(value.get("session_profile")),
        data_path=_optional_text(value.get("data_path")),
        candidate_transition_policy=_optional_text(value.get("candidate_transition_policy")),
        stop_policy=_optional_text(value.get("stop_policy")),
        recovery_policy=_optional_text(value.get("recovery_policy")),
        unfilled_execution_policy=(MockUnfilledExecutionPolicy(**value["unfilled_execution_policy"])
                                   if value.get("unfilled_execution_policy") is not None else None),
        execution_plan_mode=value.get("execution_plan_mode"),
        close_execution_policy=(CloseMockExecutionPolicy(**value["close_execution_policy"])
                                if value.get("close_execution_policy") is not None else None),
        panic_execution_policy=(PanicMockExecutionPolicy(**value['panic_execution_policy'])
                                if value.get('panic_execution_policy') is not None else None),
    )
    expected = str(value.get("spec_id", spec.spec_id))
    if expected != spec.spec_id:
        raise ValueError("mock automation operating spec content does not match spec_id")
    return spec


def stage_revision_from_dict(value: Mapping[str, Any]) -> StrategyStageRevision:
    revision = StrategyStageRevision(
        revision_id=str(value["revision_id"]), strategy_ref=str(value["strategy_ref"]),
        previous_stage=StrategyLifecycleStage(str(value["previous_stage"])),
        stage=StrategyLifecycleStage(str(value["stage"])),
        evidence_refs=tuple(str(item) for item in value.get("evidence_refs", ())),
        reason=str(value["reason"]), decided_at=datetime.fromisoformat(str(value["decided_at"])),
    )
    expected = strategy_stage_revision(
        strategy_ref=revision.strategy_ref, previous_stage=revision.previous_stage,
        stage=revision.stage, evidence_refs=revision.evidence_refs,
        reason=revision.reason, decided_at=revision.decided_at,
    )
    if expected.revision_id != revision.revision_id:
        raise ValueError("strategy stage revision content does not match revision_id")
    return revision


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _require_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
