from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.execution_activation import (
    ForwardCriteria,
    ForwardEvaluationSpec,
    MOCK_AUTOMATION_SPEC_VERSION,
    MOCK_CANDIDATE_TRANSITION_POLICY,
    MOCK_RECOVERY_POLICY,
    MOCK_STOP_POLICY,
    MockAutomationOperatingSpec,
    MockAutomationReadinessStatus,
    StrategyLifecycleStage,
    assess_mock_automation_readiness,
    strategy_stage_revision,
)
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)


NOW = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
MOCK_ACCOUNT_REF = "af64a3fa-197f-49df-8e8b-65b71bee02d9"


def _criteria(**changes: int | None) -> ForwardCriteria:
    values = {
        "minimum_comparable_observations": 100,
        "minimum_coverage_ppm": 990_000,
        "maximum_p95_delay_ms": 2_000,
        "maximum_gap_count": 1,
        "maximum_submission_unknown_count": 0,
        "maximum_rejected_order_count": 1,
        "maximum_cancel_failure_count": 0,
        "maximum_reconnect_count": 2,
        "maximum_balance_mismatch_count": 0,
        "minimum_active_day_count": 5,
        "minimum_closed_trade_count": 10,
        "minimum_adjusted_net_pnl_won": 0,
        "maximum_drawdown_ppm": 100_000,
        "maximum_exposure_ppm": 500_000,
    }
    values.update(changes)
    return ForwardCriteria(**values)


def _spec(
    criteria: ForwardCriteria | None = None, *, environment: str = "mock",
    session_profile: str | None = None, account_ref: str = "mock-account",
) -> ForwardEvaluationSpec:
    return ForwardEvaluationSpec(
        strategy_ref="strategy-1", family_version="family/v1",
        factor_versions=("factor-a/v1", "factor-b/v1"), policy_version="policy/v1",
        data_path="nas", account_ref=account_ref, environment=environment,
        evaluation_start=NOW + timedelta(days=1), evaluation_end=NOW + timedelta(days=8),
        frozen_at=NOW, criteria=criteria or _criteria(),
        session_profile=session_profile,
    )


def _automation_spec(
    profile: ForwardEvaluationSpec,
    *,
    complete: bool = True,
    maximum_concurrent_strategies: int | None = 1,
    binding_revision: int = 1,
    frozen_at: datetime = NOW,
) -> MockAutomationOperatingSpec:
    values = {
        "shadow_evidence_ref": "shadow-stage-revision" if complete else None,
        "maximum_concurrent_strategies": maximum_concurrent_strategies if complete else None,
        "maximum_concurrent_positions": 1 if complete else None,
        "maximum_capital_won": 10_000_000 if complete else None,
        "maximum_daily_loss_won": 300_000 if complete else None,
        "maximum_data_gap_seconds": 5 if complete else None,
        "maximum_submission_unknown_count": 0 if complete else None,
        "maximum_reconnect_count": 2 if complete else None,
        "maximum_balance_mismatch_count": 0 if complete else None,
        "supported_venue": "KRX" if complete else None,
        "session_profile": profile.session_profile if complete else None,
        "data_path": profile.data_path if complete else None,
        "candidate_transition_policy": MOCK_CANDIDATE_TRANSITION_POLICY if complete else None,
        "stop_policy": MOCK_STOP_POLICY if complete else None,
        "recovery_policy": MOCK_RECOVERY_POLICY if complete else None,
    }
    return MockAutomationOperatingSpec(
        strategy_ref=profile.strategy_ref,
        candidate_package_hash="a" * 64,
        final_result_hash="b" * 64,
        final_batch_id="final-batch-1",
        final_run_id="final-run-1",
        forward_profile_id=profile.profile_id,
        account_scope=AccountScope(
            broker="kiwoom",
            environment=AccountEnvironment.MOCK,
            account_ref=profile.account_ref,
        ),
        credential_profile_id="nas-mock-default",
        binding_revision=binding_revision,
        binding_verified_at=NOW,
        frozen_at=frozen_at,
        **values,
    )


class ExecutionActivationTests(unittest.TestCase):
    def test_profile_id_freezes_versions_path_account_period_and_criteria(self) -> None:
        first = _spec()
        same = _spec()
        changed = _spec(_criteria(maximum_gap_count=2))

        self.assertEqual(first.profile_id, same.profile_id)
        self.assertNotEqual(first.profile_id, changed.profile_id)
        self.assertEqual((), first.criteria.tbd_fields)

    def test_tbd_criteria_are_explicit_and_real_environment_is_rejected(self) -> None:
        self.assertEqual(
            ("minimum_closed_trade_count",),
            _criteria(minimum_closed_trade_count=None).tbd_fields,
        )
        with self.assertRaisesRegex(ValueError, "only the mock"):
            _spec(environment="real")

    def test_explicit_session_profile_is_part_of_forward_profile_identity(self) -> None:
        legacy = _spec()
        regular = _spec(session_profile="krx-regular/v1")
        after = _spec(session_profile="krx-after/v1")
        self.assertNotEqual(legacy.profile_id, regular.profile_id)
        self.assertNotEqual(regular.profile_id, after.profile_id)
        self.assertNotIn("session_profile", legacy.to_dict())
        self.assertEqual("krx-after/v1", after.to_dict()["session_profile"]["profile"])

    def test_stage_transition_requires_evidence_and_live_has_separate_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "evidence"):
            strategy_stage_revision(
                strategy_ref="strategy-1", previous_stage=StrategyLifecycleStage.DRAFT,
                stage=StrategyLifecycleStage.EVALUATED, evidence_refs=(),
                reason="backtest reviewed", decided_at=NOW,
            )
        with self.assertRaisesRegex(ValueError, "O2b"):
            strategy_stage_revision(
                strategy_ref="strategy-1",
                previous_stage=StrategyLifecycleStage.BROKER_MOCK_VALIDATED,
                stage=StrategyLifecycleStage.APPROVED_FOR_LIVE,
                evidence_refs=("forward-report-1",), reason="manual request", decided_at=NOW,
            )

    def test_mock_automation_spec_has_no_silent_defaults_and_stays_blocked(self) -> None:
        profile = _spec(session_profile="krx-regular/v1", account_ref=MOCK_ACCOUNT_REF)
        draft = _automation_spec(profile, complete=False)
        readiness = assess_mock_automation_readiness(
            draft, profile, strategy_stage=StrategyLifecycleStage.SHADOW,
        )

        self.assertEqual(MOCK_AUTOMATION_SPEC_VERSION, draft.version)
        self.assertEqual(MockAutomationReadinessStatus.BLOCKED, readiness.status)
        self.assertIn("TBD:maximum_capital_won", readiness.reasons)
        self.assertIn("TBD:recovery_policy", readiness.reasons)

    def test_complete_mock_policy_is_ready_but_does_not_enable_transport(self) -> None:
        profile = _spec(session_profile="krx-regular/v1", account_ref=MOCK_ACCOUNT_REF)
        operating = _automation_spec(profile)
        readiness = assess_mock_automation_readiness(
            operating, profile, strategy_stage=StrategyLifecycleStage.SHADOW,
        )

        self.assertEqual(MockAutomationReadinessStatus.READY, readiness.status)
        self.assertEqual((), readiness.reasons)
        self.assertNotEqual(
            operating.spec_id,
            _automation_spec(profile, maximum_concurrent_strategies=2).spec_id,
        )

    def test_unsupported_concurrency_and_non_shadow_stage_are_blocked(self) -> None:
        profile = _spec(session_profile="krx-regular/v1", account_ref=MOCK_ACCOUNT_REF)
        readiness = assess_mock_automation_readiness(
            _automation_spec(profile, maximum_concurrent_strategies=2),
            profile,
            strategy_stage=StrategyLifecycleStage.VALIDATED,
        )
        self.assertEqual(MockAutomationReadinessStatus.BLOCKED, readiness.status)
        self.assertIn("UNSUPPORTED_CONCURRENT_STRATEGY_COUNT", readiness.reasons)
        self.assertIn("STRATEGY_NOT_IN_SHADOW", readiness.reasons)

    def test_mock_automation_spec_rejects_real_account_scope(self) -> None:
        profile = _spec(session_profile="krx-regular/v1", account_ref=MOCK_ACCOUNT_REF)
        values = _automation_spec(profile).to_dict()
        values["account_scope"] = {
            "broker": "kiwoom", "environment": "real", "account_ref": str(uuid.uuid4()),
        }
        from kiwoom_monitor.domain.execution_activation import mock_automation_spec_from_dict
        with self.assertRaisesRegex(ValueError, "verified mock"):
            mock_automation_spec_from_dict(values)

    def test_repository_requires_current_binding_and_keeps_operating_spec_immutable(self) -> None:
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        store.register_account_identity({
            "broker": "kiwoom", "environment": "mock",
            "identity_fingerprint": "c" * 64, "account_ref": MOCK_ACCOUNT_REF,
            "created_at": NOW.isoformat(),
        })
        binding = store.append_account_binding({
            "credential_profile_id": "nas-mock-default", "broker": "kiwoom",
            "environment": "mock", "account_ref": MOCK_ACCOUNT_REF,
            "verified_at": NOW.isoformat(), "verification_method": "ka00001",
        })
        repository = ForwardEvaluationRepository(store)
        profile = _spec(session_profile="krx-regular/v1", account_ref=MOCK_ACCOUNT_REF)
        repository.save_profile(profile)
        operating = _automation_spec(profile, binding_revision=binding["binding_revision"])

        self.assertTrue(repository.save_mock_automation_spec(operating))
        self.assertFalse(repository.save_mock_automation_spec(operating))
        self.assertEqual((operating,), repository.load_mock_automation_specs(MOCK_ACCOUNT_REF))

        store.append_account_binding({
            "credential_profile_id": "nas-mock-default", "broker": "kiwoom",
            "environment": "mock", "account_ref": MOCK_ACCOUNT_REF,
            "verified_at": (NOW + timedelta(minutes=1)).isoformat(),
            "verification_method": "ka00001",
        })
        stale = _automation_spec(
            profile, binding_revision=1, frozen_at=NOW + timedelta(minutes=2),
        )
        with self.assertRaisesRegex(ValueError, "current verified mock binding"):
            repository.save_mock_automation_spec(stale)

    def test_repository_keeps_profiles_and_stage_revisions_across_restart(self) -> None:
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        repository = ForwardEvaluationRepository(store)
        spec = _spec()
        self.assertTrue(repository.save_profile(spec))
        self.assertFalse(repository.save_profile(spec))
        first = strategy_stage_revision(
            strategy_ref=spec.strategy_ref, previous_stage=StrategyLifecycleStage.DRAFT,
            stage=StrategyLifecycleStage.EVALUATED, evidence_refs=("research-report-1",),
            reason="manual research report reviewed", decided_at=NOW,
        )
        self.assertTrue(repository.save_stage_revision(first))
        self.assertFalse(repository.save_stage_revision(first))

        restored = ForwardEvaluationRepository(store)
        self.assertEqual(spec, restored.load_profile(spec.strategy_ref, spec.profile_id))
        self.assertEqual(StrategyLifecycleStage.EVALUATED, restored.latest_stage(spec.strategy_ref))
        self.assertEqual((first,), restored.load_stage_revisions(spec.strategy_ref))


if __name__ == "__main__":
    unittest.main()
