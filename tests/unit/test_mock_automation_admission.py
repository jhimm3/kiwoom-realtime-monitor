from __future__ import annotations

import unittest
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.mock_automation_admission import (
    admit_mock_automation,
    execution_run_id_for_spec,
)
from kiwoom_monitor.application.mock_automation_recovery import (
    MockAutomationRecoveryMetrics,
    MockAutomationRecoveryStatus,
    record_mock_automation_recovery,
)
from kiwoom_monitor.application.mock_automation_execution import (
    MockAutomationGateStatus,
    MockAutomationLiveMetrics,
    VERIFIED_DAILY_PNL_SOURCE,
    dispatch_mock_automation_decision,
    emergency_stop_mock_automation,
)
from kiwoom_monitor.application.breakout_strategy import StrategyDecision
from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
from kiwoom_monitor.domain.execution_activation import (
    ForwardCriteria,
    ForwardEvaluationSpec,
    MOCK_CANDIDATE_TRANSITION_POLICY,
    MOCK_RECOVERY_POLICY,
    MOCK_STOP_POLICY,
    MockAutomationOperatingSpec,
    StrategyLifecycleStage,
    strategy_stage_revision,
)
from kiwoom_monitor.domain.order_contract import (
    AccountEnvironment,
    AccountScope,
    AccountSnapshot,
    BrokerOrderSnapshot,
    BrokerSubmission,
    OrderIntent,
    OrderSide,
    OrderState,
    OrderType,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import (
    ForwardEvaluationRepository,
)


NOW = datetime(2026, 9, 16, 0, 0, tzinfo=timezone.utc)
ACCOUNT_REF = "af64a3fa-197f-49df-8e8b-65b71bee02d9"
CANDIDATE_HASH = "a" * 64
RESULT_HASH = "b" * 64


class _ResearchRepository:
    def __init__(self, *, state: str = "COMPLETED", result_hash: str = RESULT_HASH) -> None:
        self.execution = {
            "candidate_spec_hash": CANDIDATE_HASH,
            "state": state,
            "run_id": "final-run-1",
            "logical_result_hash": result_hash,
        }
        self.run = {
            "status": "completed",
            "logical_result_hash": result_hash,
        }

    def load_final_holdout_executions(self, batch_id: str):
        return (self.execution,) if batch_id == "final-batch-1" else ()

    def load_run(self, run_id: str):
        return self.run if run_id == "final-run-1" else None


class _Transport:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, intent):
        self.calls += 1
        raise AssertionError(f"disabled admission sent order: {intent.intent_id}")


class _AcceptTransport:
    def __init__(self) -> None:
        self.calls = 0

    def submit(self, intent):
        self.calls += 1
        return BrokerSubmission("broker-order-1", NOW)

    def cancel(self, intent, broker_order_id, quantity=0):
        raise AssertionError("cancel was not requested")


def _strategy_decision(
    run_id: str, *, action: str = "ENTER", snapshot_id: str = "snapshot-1",
) -> StrategyDecision:
    quantity = 1 if action in {"ENTER", "EXIT"} else 0
    body = {
        "run_id": run_id,
        "snapshot_id": snapshot_id,
        "symbol": "005930",
        "proposal": action,
        "final_action": action,
        "quantity": quantity,
        "signal_reference_price": 70_000,
        "required_capital_won": 70_000 if action == "ENTER" else 0,
        "reasons": (),
        "constraints": ("KRX",),
        "state_before": {},
        "state_after": {},
        "decided_at": NOW.isoformat(),
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return StrategyDecision(
        decision_id=f"decision_{hashlib.sha256(encoded).hexdigest()}",
        **body,
    )


def _criteria() -> ForwardCriteria:
    return ForwardCriteria(
        minimum_comparable_observations=100,
        minimum_coverage_ppm=990_000,
        maximum_p95_delay_ms=2_000,
        maximum_gap_count=1,
        maximum_submission_unknown_count=0,
        maximum_rejected_order_count=1,
        maximum_cancel_failure_count=0,
        maximum_reconnect_count=2,
        maximum_balance_mismatch_count=0,
        minimum_active_day_count=5,
        minimum_closed_trade_count=10,
        minimum_adjusted_net_pnl_won=0,
        maximum_drawdown_ppm=100_000,
        maximum_exposure_ppm=500_000,
    )


class MockAutomationAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SQLiteQueryStore(Path(":memory:"))
        self.store.initialize()
        self.addCleanup(self.store.close)
        self.store.register_account_identity({
            "broker": "kiwoom", "environment": "mock",
            "identity_fingerprint": "c" * 64, "account_ref": ACCOUNT_REF,
            "created_at": NOW.isoformat(),
        })
        self.binding = self.store.append_account_binding({
            "credential_profile_id": "nas-mock-default", "broker": "kiwoom",
            "environment": "mock", "account_ref": ACCOUNT_REF,
            "verified_at": NOW.isoformat(), "verification_method": "ka00001",
        })
        self.forward = ForwardEvaluationRepository(self.store)
        self.profile = ForwardEvaluationSpec(
            strategy_ref="strategy-1", family_version="family/v1",
            factor_versions=("factor/v1",), policy_version="policy/v1",
            data_path="nas", account_ref=ACCOUNT_REF, environment="mock",
            evaluation_start=NOW + timedelta(days=1),
            evaluation_end=NOW + timedelta(days=8), frozen_at=NOW,
            criteria=_criteria(), session_profile="krx-regular/v1",
        )
        self.forward.save_profile(self.profile)
        previous = StrategyLifecycleStage.DRAFT
        self.shadow_revision_id = ""
        for index, stage in enumerate((
            StrategyLifecycleStage.EVALUATED,
            StrategyLifecycleStage.VALIDATED,
            StrategyLifecycleStage.SHADOW,
        )):
            revision = strategy_stage_revision(
                strategy_ref="strategy-1", previous_stage=previous, stage=stage,
                evidence_refs=(f"evidence-{index}",), reason=f"stage {stage.value}",
                decided_at=NOW + timedelta(seconds=index),
            )
            self.forward.save_stage_revision(revision)
            if stage is StrategyLifecycleStage.SHADOW:
                self.shadow_revision_id = revision.revision_id
            previous = stage
        self.spec = MockAutomationOperatingSpec(
            strategy_ref="strategy-1", candidate_package_hash=CANDIDATE_HASH,
            final_result_hash=RESULT_HASH, final_batch_id="final-batch-1",
            final_run_id="final-run-1", forward_profile_id=self.profile.profile_id,
            account_scope=AccountScope("kiwoom", AccountEnvironment.MOCK, ACCOUNT_REF),
            credential_profile_id="nas-mock-default",
            binding_revision=self.binding["binding_revision"],
            binding_verified_at=NOW, frozen_at=NOW,
            shadow_evidence_ref=self.shadow_revision_id,
            maximum_concurrent_strategies=1, maximum_concurrent_positions=1,
            maximum_capital_won=10_000_000, maximum_daily_loss_won=300_000,
            maximum_data_gap_seconds=5, maximum_submission_unknown_count=0,
            maximum_reconnect_count=2, maximum_balance_mismatch_count=0,
            supported_venue="KRX", session_profile="krx-regular/v1", data_path="nas",
            candidate_transition_policy=MOCK_CANDIDATE_TRANSITION_POLICY,
            stop_policy=MOCK_STOP_POLICY, recovery_policy=MOCK_RECOVERY_POLICY,
        )
        self.forward.save_mock_automation_spec(self.spec)

    def _runtime(
        self,
        transport: _Transport | None = None,
        *,
        spec: MockAutomationOperatingSpec | None = None,
        owner_token: str = "automation-owner",
    ) -> tuple[ExecutionRuntime, ExecutionRepository]:
        repository = ExecutionRepository(self.store)
        selected = spec or self.spec
        runtime = ExecutionRuntime(
            OrderLifecycle(repository, transport or _Transport(), now_provider=lambda: NOW),
            repository,
            account_ref=ACCOUNT_REF,
            run_id=execution_run_id_for_spec(selected.spec_id),
            owner_token=owner_token,
        )
        return runtime, repository

    def _admitted_runtime(self) -> tuple[ExecutionRuntime, ExecutionRepository, _Transport]:
        transport = _Transport()
        runtime, execution = self._runtime(transport)
        admit_mock_automation(
            self.forward, _ResearchRepository(), runtime,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id, requested_at=NOW,
        )
        self.addCleanup(runtime.stop)
        return runtime, execution, transport

    def test_ready_candidate_is_admitted_once_with_orders_disabled(self) -> None:
        transport = _Transport()
        runtime, execution = self._runtime(transport)
        self.addCleanup(runtime.stop)

        first = admit_mock_automation(
            self.forward, _ResearchRepository(), runtime,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id, requested_at=NOW,
        )
        second = admit_mock_automation(
            self.forward, _ResearchRepository(), runtime,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
            requested_at=NOW + timedelta(seconds=10),
        )

        self.assertEqual(first, second)
        self.assertEqual(1, len(self.forward.load_mock_automation_admissions(ACCOUNT_REF)))
        self.assertEqual(1, len(self.forward.load_mock_automation_lease_receipts(ACCOUNT_REF)))
        intent = OrderIntent(
            "intent-1", runtime.run_id, "decision-1", ACCOUNT_REF, "mock", "005930",
            "KRX", OrderSide.BUY, 1, OrderType.LIMIT, 70_000,
            NOW, NOW + timedelta(minutes=1), "policy-v1",
        )
        account = AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW)
        with self.assertRaisesRegex(RuntimeError, "NEW_ORDERS_DISABLED"):
            runtime.submit(intent, account)
        self.assertIsNone(execution.load("intent-1"))
        self.assertEqual(0, transport.calls)

    def test_existing_manual_lease_blocks_automatic_owner_before_receipt(self) -> None:
        manual = ExecutionRepository(self.store)
        self.assertTrue(manual.claim_runtime(
            "mock", ACCOUNT_REF, "manual-run", "manual-owner",
            datetime.now(timezone.utc), lease_seconds=60,
        ))
        runtime, _ = self._runtime()
        with self.assertRaisesRegex(RuntimeError, "another mock execution runtime"):
            admit_mock_automation(
                self.forward, _ResearchRepository(), runtime,
                account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id, requested_at=NOW,
            )
        self.assertEqual(1, len(self.forward.load_mock_automation_admissions(ACCOUNT_REF)))
        self.assertEqual((), self.forward.load_mock_automation_lease_receipts(ACCOUNT_REF))

    def test_unfinished_or_changed_final_result_is_rejected_before_admission(self) -> None:
        runtime, _ = self._runtime()
        for research in (
            _ResearchRepository(state="FAILED"),
            _ResearchRepository(result_hash="d" * 64),
        ):
            with self.subTest(research=research.execution), self.assertRaisesRegex(
                ValueError, "completed final result",
            ):
                admit_mock_automation(
                    self.forward, research, runtime,
                    account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id, requested_at=NOW,
                )
        self.assertEqual((), self.forward.load_mock_automation_admissions(ACCOUNT_REF))

    def test_changed_binding_or_unrelated_shadow_evidence_blocks_admission(self) -> None:
        stale_shadow = replace(self.spec, shadow_evidence_ref="another-shadow-revision")
        self.forward.save_mock_automation_spec(stale_shadow)
        runtime, _ = self._runtime(spec=stale_shadow, owner_token="stale-shadow-owner")
        with self.assertRaisesRegex(ValueError, "shadow evidence"):
            admit_mock_automation(
                self.forward, _ResearchRepository(), runtime,
                account_ref=ACCOUNT_REF, spec_id=stale_shadow.spec_id, requested_at=NOW,
            )

        self.store.append_account_binding({
            "credential_profile_id": "nas-mock-default", "broker": "kiwoom",
            "environment": "mock", "account_ref": ACCOUNT_REF,
            "verified_at": (NOW + timedelta(minutes=1)).isoformat(),
            "verification_method": "ka00001",
        })
        current_runtime, _ = self._runtime()
        with self.assertRaisesRegex(ValueError, "current verified mock binding"):
            admit_mock_automation(
                self.forward, _ResearchRepository(), current_runtime,
                account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id, requested_at=NOW,
            )
        self.assertEqual((), self.forward.load_mock_automation_admissions(ACCOUNT_REF))

    def test_flat_fresh_recovery_is_cleared_but_orders_remain_disabled(self) -> None:
        runtime, execution, transport = self._admitted_runtime()
        recovery = MockAccountRecovery(
            AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), (),
        )
        decision = record_mock_automation_recovery(
            self.forward,
            runtime,
            recovery,
            MockAutomationRecoveryMetrics(
                observed_at=NOW, recovery_complete=True, daily_net_pnl_won=0,
                data_gap_seconds=0, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0,
            ),
            account_ref=ACCOUNT_REF,
            spec_id=self.spec.spec_id,
        )

        self.assertEqual(MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED, decision.status)
        self.assertEqual((), decision.reasons)
        self.assertEqual(
            (decision,), self.forward.load_mock_automation_recovery_decisions(ACCOUNT_REF),
        )
        intent = OrderIntent(
            "intent-after-recovery", runtime.run_id, "decision-1", ACCOUNT_REF,
            "mock", "005930", "KRX", OrderSide.BUY, 1, OrderType.LIMIT, 70_000,
            NOW, NOW + timedelta(minutes=1), "policy-v1",
        )
        with self.assertRaisesRegex(RuntimeError, "NEW_ORDERS_DISABLED"):
            runtime.submit(intent, recovery.account)
        self.assertIsNone(execution.load(intent.intent_id))
        self.assertEqual(0, transport.calls)

    def test_recovery_blocks_open_state_loss_and_fault_limits(self) -> None:
        runtime, execution, _ = self._admitted_runtime()
        recovery = MockAccountRecovery(
            AccountSnapshot(
                ACCOUNT_REF, 1_100_000, 100_000, {"005930": 1},
                NOW - timedelta(seconds=10),
            ),
            (BrokerOrderSnapshot(
                "broker-1", ACCOUNT_REF, "005930", OrderState.ACCEPTED,
                0, 1, NOW - timedelta(seconds=10),
            ),),
        )
        decision = record_mock_automation_recovery(
            self.forward,
            runtime,
            recovery,
            MockAutomationRecoveryMetrics(
                observed_at=NOW, recovery_complete=True, daily_net_pnl_won=-300_001,
                data_gap_seconds=6, submission_unknown_count=1,
                reconnect_count=3, balance_mismatch_count=1,
            ),
            account_ref=ACCOUNT_REF,
            spec_id=self.spec.spec_id,
        )

        self.assertEqual(MockAutomationRecoveryStatus.BLOCKED, decision.status)
        self.assertTrue({
            "DAILY_LOSS_LIMIT_REACHED", "DATA_GAP_LIMIT_EXCEEDED",
            "SUBMISSION_UNKNOWN_LIMIT_EXCEEDED", "RECONNECT_LIMIT_EXCEEDED",
            "BALANCE_MISMATCH_LIMIT_EXCEEDED", "BROKER_OPEN_ORDER_PRESENT",
            "BROKER_POSITION_PRESENT", "BROKER_RESERVED_BUY_PRESENT",
            "BROKER_ACCOUNT_SNAPSHOT_STALE", "BROKER_ORDER_SNAPSHOT_STALE",
        }.issubset(set(decision.reasons)))

    def test_unknown_daily_result_or_lost_lease_cannot_clear_recovery(self) -> None:
        runtime, execution, _ = self._admitted_runtime()
        recovery = MockAccountRecovery(
            AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), (),
        )
        blocked = record_mock_automation_recovery(
            self.forward,
            runtime,
            recovery,
            MockAutomationRecoveryMetrics(
                observed_at=NOW, recovery_complete=False, daily_net_pnl_won=None,
                data_gap_seconds=None, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0,
            ),
            account_ref=ACCOUNT_REF,
            spec_id=self.spec.spec_id,
        )
        self.assertEqual(MockAutomationRecoveryStatus.BLOCKED, blocked.status)
        self.assertIn("DAILY_NET_PNL_UNKNOWN", blocked.reasons)
        self.assertIn("BROKER_RECOVERY_INCOMPLETE", blocked.reasons)

        self.assertTrue(execution.release_runtime(
            "mock", ACCOUNT_REF, runtime.run_id, "automation-owner",
        ))
        self.assertTrue(execution.claim_runtime(
            "mock", ACCOUNT_REF, "replacement-run", "replacement-owner",
            datetime.now(timezone.utc), lease_seconds=60,
        ))
        with self.assertRaisesRegex(RuntimeError, "ownership was lost"):
            record_mock_automation_recovery(
                self.forward,
                runtime,
                recovery,
                MockAutomationRecoveryMetrics(
                    observed_at=NOW + timedelta(seconds=1), recovery_complete=True,
                    daily_net_pnl_won=0, data_gap_seconds=0,
                    submission_unknown_count=0, reconnect_count=0,
                    balance_mismatch_count=0,
                ),
                account_ref=ACCOUNT_REF,
                spec_id=self.spec.spec_id,
            )
        self.assertEqual(1, len(
            self.forward.load_mock_automation_recovery_decisions(ACCOUNT_REF)
        ))

    def test_each_decision_rechecks_limits_and_submits_same_intent_once(self) -> None:
        transport = _AcceptTransport()
        runtime, execution = self._runtime(transport)
        admit_mock_automation(
            self.forward, _ResearchRepository(), runtime,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id, requested_at=NOW,
        )
        self.addCleanup(runtime.stop)
        initial = MockAccountRecovery(
            AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), (),
        )
        record_mock_automation_recovery(
            self.forward, runtime, initial,
            MockAutomationRecoveryMetrics(
                observed_at=NOW, recovery_complete=True, daily_net_pnl_won=0,
                data_gap_seconds=0, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0,
            ),
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        current = MockAccountRecovery(
            AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), (),
        )
        metrics = MockAutomationLiveMetrics(
            observed_at=NOW + timedelta(seconds=1), recovery_complete=True,
            daily_net_pnl_won=0, daily_net_pnl_source=VERIFIED_DAILY_PNL_SOURCE,
            data_gap_seconds=0, submission_unknown_count=0,
            reconnect_count=0, balance_mismatch_count=0, data_path="nas",
        )
        decision = _strategy_decision(runtime.run_id)

        first = dispatch_mock_automation_decision(
            self.forward, runtime, decision, current, metrics,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
            strategy_ref=self.spec.strategy_ref,
        )
        second = dispatch_mock_automation_decision(
            self.forward, runtime, decision, current, metrics,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
            strategy_ref=self.spec.strategy_ref,
        )

        self.assertEqual(MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION, first[0].status)
        self.assertEqual(first[1], second[1])
        self.assertEqual(1, transport.calls)
        self.assertIsNotNone(execution.load(first[0].intent_id))
        self.assertEqual(1, len(self.forward.load_mock_automation_decision_gates(ACCOUNT_REF)))
        self.assertEqual(1, len(self.forward.load_mock_automation_dispatch_receipts(ACCOUNT_REF)))
        with self.assertRaisesRegex(RuntimeError, "NEW_ORDERS_DISABLED"):
            runtime.submit(first[1].intent, current.account)

        blocked, next_record, next_receipt = dispatch_mock_automation_decision(
            self.forward, runtime,
            _strategy_decision(runtime.run_id, snapshot_id="snapshot-2"),
            current, metrics,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
            strategy_ref=self.spec.strategy_ref,
        )
        self.assertEqual(MockAutomationGateStatus.BLOCKED, blocked.status)
        self.assertIn("EXECUTION_ORDER_IN_FLIGHT", blocked.reasons)
        self.assertIsNone(next_record)
        self.assertIsNone(next_receipt)
        self.assertEqual(1, transport.calls)

    def test_live_gate_blocks_unverified_pnl_stale_data_and_emergency_stop(self) -> None:
        runtime, _, transport = self._admitted_runtime()
        initial = MockAccountRecovery(
            AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), (),
        )
        record_mock_automation_recovery(
            self.forward, runtime, initial,
            MockAutomationRecoveryMetrics(
                observed_at=NOW, recovery_complete=True, daily_net_pnl_won=0,
                data_gap_seconds=0, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0,
            ),
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        emergency_stop_mock_automation(
            self.forward, runtime, account_ref=ACCOUNT_REF,
            spec_id=self.spec.spec_id, stopped_at=NOW + timedelta(milliseconds=500),
            reason="operator stop",
        )
        current = MockAccountRecovery(
            AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), (),
        )
        blocked, record, receipt = dispatch_mock_automation_decision(
            self.forward, runtime, _strategy_decision(runtime.run_id), current,
            MockAutomationLiveMetrics(
                observed_at=NOW + timedelta(seconds=6), recovery_complete=True,
                daily_net_pnl_won=0, daily_net_pnl_source="estimated/v1",
                data_gap_seconds=6, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0, data_path="nas",
            ),
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
            strategy_ref=self.spec.strategy_ref,
        )

        self.assertEqual(MockAutomationGateStatus.BLOCKED, blocked.status)
        self.assertTrue({
            "DAILY_NET_PNL_SOURCE_UNVERIFIED", "DATA_GAP_LIMIT_EXCEEDED",
            "STRATEGY_DECISION_STALE", "BROKER_ACCOUNT_SNAPSHOT_STALE",
            "EMERGENCY_STOP_REQUIRES_NEW_RECOVERY",
        }.issubset(set(blocked.reasons)))
        self.assertIsNone(record)
        self.assertIsNone(receipt)
        self.assertEqual(0, transport.calls)
        self.assertEqual(1, len(self.forward.load_mock_automation_stop_revisions(ACCOUNT_REF)))


if __name__ == "__main__":
    unittest.main()
