from __future__ import annotations

import unittest
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from kiwoom_monitor.application.mock_automation_admission import (
    MockAutomationDesiredState,
    admit_mock_automation,
    execution_run_id_for_spec,
    mock_automation_control_from_dict,
)
from kiwoom_monitor.application.mock_automation_candidate import CandidateEligibilityStatus
from kiwoom_monitor.application.mock_automation_recovery import (
    MockAutomationRecoveryMetrics,
    MockAutomationRecoveryStatus,
    record_mock_automation_recovery,
    record_mock_automation_recovery_from_risk,
)
from kiwoom_monitor.application.mock_automation_execution import (
    MockAutomationGateStatus,
    MockAutomationLiveMetrics,
    VERIFIED_DAILY_PNL_SOURCE,
    dispatch_mock_automation_decision,
    dispatch_mock_automation_decision_from_risk,
    emergency_stop_mock_automation,
    resume_mock_automation,
)
from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
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

    def load_mock_automation_candidate_package(self, strategy_ref: str, package_hash: str):
        if self.execution["state"] != "COMPLETED" or package_hash != CANDIDATE_HASH:
            return None
        return SimpleNamespace(
            package_hash=CANDIDATE_HASH, candidate_spec_hash="c" * 64,
            source_final_batch_id="final-batch-1", source_final_run_id="final-run-1",
            source_final_result_hash=self.execution["logical_result_hash"],
        )

    def load_mock_automation_eligibility_receipt(self, account_ref: str, package_hash: str):
        if account_ref != ACCOUNT_REF or package_hash != CANDIDATE_HASH:
            return None
        return SimpleNamespace(
            status=CandidateEligibilityStatus.ELIGIBLE,
            policy_id="policy-1", candidate_spec_hash="c" * 64,
        )

    def load_mock_automation_eligibility_policy(self, strategy_ref: str, package_hash: str):
        if strategy_ref != "strategy-1" or package_hash != CANDIDATE_HASH:
            return None
        return SimpleNamespace(
            policy_id="policy-1", candidate_spec_hash="c" * 64, tbd_fields=(),
        )

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

    def cancel(self, intent, broker_order_id, quantity=0):
        raise AssertionError("cancel was not requested")


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
            evaluation_start=NOW,
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
        now_provider=None,
    ) -> tuple[ExecutionRuntime, ExecutionRepository]:
        repository = ExecutionRepository(self.store)
        selected = spec or self.spec
        clock = now_provider or (lambda: NOW + timedelta(seconds=1))
        self.store._execution_wall_now = clock
        runtime = ExecutionRuntime(
            OrderLifecycle(repository, transport or _Transport(), now_provider=lambda: NOW),
            repository,
            account_ref=ACCOUNT_REF,
            run_id=execution_run_id_for_spec(selected.spec_id),
            owner_token=owner_token,
            now_provider=clock,
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
        control = self.forward.load_mock_automation_control(ACCOUNT_REF)
        self.assertIsNotNone(control)
        self.assertEqual(MockAutomationDesiredState.RUNNING, control.desired_state)
        self.assertEqual(1, control.control_revision)
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
            NOW, lease_seconds=60,
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
                ValueError, "candidate package|lineage",
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

    def test_operational_recovery_and_dispatch_require_same_stored_risk_revision(self) -> None:
        transport = _AcceptTransport()
        runtime, execution = self._runtime(transport)
        admit_mock_automation(
            self.forward, _ResearchRepository(), runtime,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id, requested_at=NOW,
        )
        self.addCleanup(runtime.stop)
        recovery = MockAccountRecovery(AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), ())
        risk = build_mock_automation_risk_snapshot(
            recovery, (), (), (), execution_run_id=runtime.run_id,
            binding_revision=self.binding["binding_revision"], reconciliation_revision=1,
            observed_at=NOW, broker_query_started_at=NOW, broker_query_completed_at=NOW,
        )
        self.forward.save_mock_automation_risk_snapshot(risk)
        recovered = record_mock_automation_recovery_from_risk(
            self.forward, runtime, recovery, risk,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        self.assertEqual(risk.snapshot_id, recovered.risk_snapshot_id)
        gate, record, receipt = dispatch_mock_automation_decision_from_risk(
            self.forward, runtime, _strategy_decision(runtime.run_id), recovery, risk,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id, strategy_ref="strategy-1",
        )
        self.assertEqual(MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION, gate.status)
        self.assertEqual(risk.snapshot_id, gate.risk_snapshot_id)
        self.assertIsNotNone(record); self.assertIsNotNone(receipt)
        self.assertEqual(1, transport.calls)
        replayed_gate, replayed_record, replayed_receipt = dispatch_mock_automation_decision_from_risk(
            self.forward, runtime, _strategy_decision(runtime.run_id), recovery, risk,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id, strategy_ref="strategy-1",
        )
        self.assertEqual(gate.gate_id, replayed_gate.gate_id)
        self.assertEqual(record.intent.intent_id, replayed_record.intent.intent_id)
        self.assertEqual(receipt.receipt_id, replayed_receipt.receipt_id)
        self.assertEqual(1, transport.calls)

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

    def test_daily_loss_blocks_at_the_exact_frozen_limit(self) -> None:
        runtime, _, _ = self._admitted_runtime()
        recovery = MockAccountRecovery(
            AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), (),
        )
        decision = record_mock_automation_recovery(
            self.forward, runtime, recovery,
            MockAutomationRecoveryMetrics(
                observed_at=NOW, recovery_complete=True, daily_net_pnl_won=-300_000,
                data_gap_seconds=0, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0,
            ),
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        self.assertEqual(MockAutomationRecoveryStatus.BLOCKED, decision.status)
        self.assertIn("DAILY_LOSS_LIMIT_REACHED", decision.reasons)

    def test_terminal_broker_history_is_not_treated_as_an_open_order(self) -> None:
        runtime, _, _ = self._admitted_runtime()
        terminal = BrokerOrderSnapshot(
            "broker-filled", ACCOUNT_REF, "005930", OrderState.FILLED,
            1, 1, NOW,
        )
        decision = record_mock_automation_recovery(
            self.forward, runtime,
            MockAccountRecovery(
                AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), (terminal,),
            ),
            MockAutomationRecoveryMetrics(
                observed_at=NOW, recovery_complete=True, daily_net_pnl_won=0,
                data_gap_seconds=0, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0,
            ),
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        self.assertEqual(MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED, decision.status)
        self.assertEqual(0, decision.open_order_count)

    def test_persisted_stop_rejects_an_older_control_revision_at_intent_claim(self) -> None:
        runtime, execution, _ = self._admitted_runtime()
        running = self.forward.load_mock_automation_control(ACCOUNT_REF)
        self.assertIsNotNone(running)
        emergency_stop_mock_automation(
            self.forward, runtime, account_ref=ACCOUNT_REF,
            spec_id=self.spec.spec_id, stopped_at=NOW + timedelta(seconds=1),
            reason="operator stop",
        )
        stopped = ForwardEvaluationRepository(self.store).load_mock_automation_control(ACCOUNT_REF)
        self.assertEqual(MockAutomationDesiredState.STOPPED, stopped.desired_state)
        self.assertEqual(running.control_revision + 1, stopped.control_revision)

        execution.bind_automation_control(
            spec_id=self.spec.spec_id, control_revision=running.control_revision,
        )
        self.assertTrue(execution.claim_runtime(
            "mock", ACCOUNT_REF, runtime.run_id, runtime.owner_token,
            NOW + timedelta(seconds=1), lease_seconds=60,
        ))
        intent = OrderIntent(
            "intent-after-stop", runtime.run_id, "decision-after-stop", ACCOUNT_REF,
            "mock", "005930", "KRX", OrderSide.BUY, 1, OrderType.LIMIT, 70_000,
            NOW, NOW + timedelta(minutes=1), "policy-v1",
        )
        with self.assertRaisesRegex(RuntimeError, "MOCK_AUTOMATION_CONTROL_CHANGED"):
            execution.create(intent)
        self.assertIsNone(execution.load(intent.intent_id))

    def test_entry_pause_fences_native_buy_creation_and_submission_but_preserves_reconciliation(self) -> None:
        from kiwoom_monitor.domain.order_contract import order_event, BrokerSubmission
        from unittest.mock import patch
        runtime, execution, transport = self._admitted_runtime()
        control = self.forward.load_mock_automation_control(ACCOUNT_REF)
        queued_intent = OrderIntent(
            "queued-before-key-rotation", runtime.run_id, "decision-before-key-rotation", ACCOUNT_REF,
            "mock", "005930", "KRX", OrderSide.BUY, 1, OrderType.LIMIT, 70_000,
            NOW, NOW + timedelta(minutes=1), "policy-v1",
        )
        paused = replace(control, control_revision=control.control_revision + 1, entry_resume_required=True)
        queue = runtime._lifecycle.queue
        def rotate_after_queue(intent):
            queued = queue(intent)
            self.assertTrue(self.forward.save_mock_automation_control(paused, expected_revision=control.control_revision))
            return queued
        with patch.object(runtime._lifecycle, "queue", side_effect=rotate_after_queue):
            with self.assertRaisesRegex(RuntimeError, "EXPLICIT_ENTRY_RESUME"):
                runtime.submit_automation_intent(queued_intent, AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW),
                    spec_id=self.spec.spec_id, control_revision=control.control_revision)
        queued = execution.load(queued_intent.intent_id)
        self.assertEqual(OrderState.QUEUED, queued.state)
        self.assertEqual(["QUEUED"], [event["event_type"] for event in execution.events(queued_intent.intent_id)])
        self.assertEqual(0, transport.calls)
        self.assertIsNone(execution._automation_control)
        self.assertEqual(paused, mock_automation_control_from_dict(paused.to_dict()))
        self.assertTrue(ForwardEvaluationRepository(self.store).load_mock_automation_control(ACCOUNT_REF).entry_resume_required)
        execution.bind_automation_control(spec_id=self.spec.spec_id, control_revision=paused.control_revision)
        try:
            with self.assertRaisesRegex(RuntimeError, "EXPLICIT_ENTRY_RESUME"):
                execution.create(replace(queued_intent, intent_id="new-buy-after-key-rotation"))
            with self.assertRaisesRegex(RuntimeError, "EXPLICIT_ENTRY_RESUME"):
                execution.apply(queued, order_event(queued_intent.intent_id, "SUBMISSION_STARTED",
                    OrderState.SUBMISSION_UNKNOWN, NOW, NOW))
            self.assertEqual(OrderState.QUEUED, execution.load(queued_intent.intent_id).state)
            confirmed = execution.apply(queued, order_event(queued_intent.intent_id, "ORDER_ACCEPTED",
                OrderState.ACCEPTED, NOW, NOW, broker_order_id="confirmed-existing"),
                broker_order_id="confirmed-existing")
            self.assertEqual(OrderState.ACCEPTED, confirmed.state)
            with patch.object(transport, "cancel", return_value=BrokerSubmission("cancel-existing", NOW)) as cancel:
                cancelled = runtime.cancel(queued_intent.intent_id)
            self.assertEqual(OrderState.CANCEL_PENDING, cancelled.state)
            cancel.assert_called_once()
        finally:
            execution.clear_automation_control()

    def test_actual_submission_keeps_stop_after_queue_and_response_after_entry_pause_contracts(self) -> None:
        from unittest.mock import patch
        runtime, execution, transport = self._admitted_runtime()
        control = self.forward.load_mock_automation_control(ACCOUNT_REF)
        stopped = replace(control, desired_state=MockAutomationDesiredState.STOPPED,
            control_revision=control.control_revision + 1)
        intent = OrderIntent("claimed-before-stop", runtime.run_id, "decision-before-stop", ACCOUNT_REF,
            "mock", "005930", "KRX", OrderSide.BUY, 2, OrderType.LIMIT, 70_000,
            NOW, NOW + timedelta(minutes=1), "policy-v1")
        queue = runtime._lifecycle.queue
        def stop_after_queue(value):
            result = queue(value)
            self.assertTrue(self.forward.save_mock_automation_control(stopped, expected_revision=control.control_revision))
            return result
        paused = replace(stopped, entry_resume_required=True, control_revision=stopped.control_revision + 1)
        def response_after_pause(value):
            self.assertEqual(OrderState.SUBMISSION_UNKNOWN, execution.load(value.intent_id).state)
            self.assertTrue(self.forward.save_mock_automation_control(paused, expected_revision=stopped.control_revision))
            return BrokerSubmission("accepted-after-pause", NOW)
        with patch.object(runtime._lifecycle, "queue", side_effect=stop_after_queue), \
             patch.object(transport, "submit", side_effect=response_after_pause) as submit:
            record = runtime.submit_automation_intent(intent, AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW),
                spec_id=self.spec.spec_id, control_revision=control.control_revision)
        submit.assert_called_once_with(intent)
        self.assertEqual(OrderState.ACCEPTED, record.state)
        reconciled = runtime.reconcile(intent.intent_id, BrokerOrderSnapshot(
            "accepted-after-pause", ACCOUNT_REF, "005930", OrderState.FILLED,
            2, 2, NOW + timedelta(seconds=1)))
        self.assertEqual(OrderState.FILLED, reconciled.state)
        self.assertEqual(2, reconciled.filled_quantity)
        self.assertEqual(paused, self.forward.load_mock_automation_control(ACCOUNT_REF))

    def test_failed_stop_persistence_closes_the_in_memory_order_gate(self) -> None:
        runtime, _, _ = self._admitted_runtime()
        runtime.set_new_orders_enabled(True)
        original = self.forward.save_mock_automation_control

        def fail_save(*args, **kwargs):
            raise OSError("simulated database failure")

        self.forward.save_mock_automation_control = fail_save
        self.addCleanup(setattr, self.forward, "save_mock_automation_control", original)
        with self.assertRaisesRegex(OSError, "simulated database failure"):
            emergency_stop_mock_automation(
                self.forward, runtime, account_ref=ACCOUNT_REF,
                spec_id=self.spec.spec_id, stopped_at=NOW + timedelta(seconds=1),
                reason="operator stop",
            )
        intent = OrderIntent(
            "intent-after-failed-stop", runtime.run_id, "decision-after-failed-stop",
            ACCOUNT_REF, "mock", "005930", "KRX", OrderSide.BUY, 1,
            OrderType.LIMIT, 70_000, NOW, NOW + timedelta(minutes=1), "policy-v1",
        )
        with self.assertRaisesRegex(RuntimeError, "NEW_ORDERS_DISABLED"):
            runtime.submit(
                intent, AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW),
            )

    def test_stopped_run_requires_fresh_recovery_before_explicit_resume(self) -> None:
        runtime, _, _ = self._admitted_runtime()
        emergency_stop_mock_automation(
            self.forward, runtime, account_ref=ACCOUNT_REF,
            spec_id=self.spec.spec_id, stopped_at=NOW + timedelta(seconds=1),
            reason="operator stop",
        )
        with self.assertRaisesRegex(RuntimeError, "fresh broker reconciliation"):
            resume_mock_automation(
                self.forward, runtime, account_ref=ACCOUNT_REF,
                spec_id=self.spec.spec_id, resumed_at=NOW + timedelta(seconds=2),
                reason="operator resume",
            )

        reconciled_at = NOW + timedelta(seconds=2)
        record_mock_automation_recovery(
            self.forward, runtime,
            MockAccountRecovery(
                AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, reconciled_at), (),
            ),
            MockAutomationRecoveryMetrics(
                observed_at=reconciled_at, recovery_complete=True, daily_net_pnl_won=0,
                data_gap_seconds=0, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0,
            ),
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        resumed = resume_mock_automation(
            self.forward, runtime, account_ref=ACCOUNT_REF,
            spec_id=self.spec.spec_id, resumed_at=NOW + timedelta(seconds=3),
            reason="operator resume",
        )
        self.assertEqual(MockAutomationDesiredState.RUNNING, resumed.desired_state)
        self.assertEqual(3, resumed.control_revision)

    def test_server_clock_blocks_observation_from_the_future(self) -> None:
        runtime, _ = self._runtime(now_provider=lambda: NOW + timedelta(seconds=1))
        admit_mock_automation(
            self.forward, _ResearchRepository(), runtime,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id, requested_at=NOW,
        )
        self.addCleanup(runtime.stop)
        recovery = MockAccountRecovery(
            AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), (),
        )
        record_mock_automation_recovery(
            self.forward, runtime, recovery,
            MockAutomationRecoveryMetrics(
                observed_at=NOW, recovery_complete=True, daily_net_pnl_won=0,
                data_gap_seconds=0, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0,
            ),
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        gate, record, receipt = dispatch_mock_automation_decision(
            self.forward, runtime, _strategy_decision(runtime.run_id), recovery,
            MockAutomationLiveMetrics(
                observed_at=NOW + timedelta(seconds=2), recovery_complete=True,
                daily_net_pnl_won=0, daily_net_pnl_source=VERIFIED_DAILY_PNL_SOURCE,
                data_gap_seconds=0, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0, data_path="nas",
            ),
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
            strategy_ref=self.spec.strategy_ref,
        )
        self.assertEqual(MockAutomationGateStatus.BLOCKED, gate.status)
        self.assertIn("LIVE_OBSERVATION_FROM_FUTURE", gate.reasons)
        self.assertIsNone(record)
        self.assertIsNone(receipt)

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
            NOW + timedelta(seconds=1), lease_seconds=60,
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
            self.forward, runtime, decision, current,
            replace(metrics, observed_at=NOW + timedelta(seconds=2)),
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
            strategy_ref=self.spec.strategy_ref,
        )

        self.assertEqual(MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION, first[0].status)
        self.assertEqual(first[1], second[1])
        self.assertEqual(first[2], second[2])
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

    def test_manage_only_allows_verified_reduce_only_exit_with_unknown_pnl(self) -> None:
        transport = _AcceptTransport()
        runtime, _ = self._runtime(transport)
        admit_mock_automation(
            self.forward, _ResearchRepository(), runtime,
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id, requested_at=NOW,
        )
        self.addCleanup(runtime.stop)
        control = self.forward.load_mock_automation_control(ACCOUNT_REF)
        self.assertTrue(self.forward.save_mock_automation_control(
            replace(control, control_revision=control.control_revision + 1, entry_resume_required=True),
            expected_revision=control.control_revision,
        ))
        recovery = MockAccountRecovery(
            AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {"005930": 1}, NOW), (),
        )
        recovered = record_mock_automation_recovery(
            self.forward, runtime, recovery,
            MockAutomationRecoveryMetrics(
                observed_at=NOW, recovery_complete=True, daily_net_pnl_won=None,
                data_gap_seconds=10, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0,
            ),
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        self.assertEqual(
            MockAutomationRecoveryStatus.MANAGE_ONLY_ORDERS_DISABLED,
            recovered.status,
        )

        gate, record, _ = dispatch_mock_automation_decision(
            self.forward, runtime,
            _strategy_decision(runtime.run_id, action="EXIT"),
            recovery,
            MockAutomationLiveMetrics(
                observed_at=NOW + timedelta(seconds=1), recovery_complete=True,
                daily_net_pnl_won=None, daily_net_pnl_source=None,
                data_gap_seconds=10, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0, data_path="nas",
                owned_position_quantities=(("005930", 1),),
            ),
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
            strategy_ref=self.spec.strategy_ref,
        )
        self.assertEqual(MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION, gate.status)
        self.assertIsNotNone(record)
        self.assertEqual(1, transport.calls)

    def test_reconciliation_pending_blocks_reduce_only_exit(self) -> None:
        runtime, _, _ = self._admitted_runtime()
        recovery = MockAccountRecovery(
            AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {"005930": 1}, NOW), (),
        )
        record_mock_automation_recovery(
            self.forward, runtime, recovery,
            MockAutomationRecoveryMetrics(
                observed_at=NOW, recovery_complete=True, daily_net_pnl_won=None,
                data_gap_seconds=0, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0,
            ),
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        gate, record, receipt = dispatch_mock_automation_decision(
            self.forward, runtime,
            _strategy_decision(runtime.run_id, action="EXIT"), recovery,
            MockAutomationLiveMetrics(
                observed_at=NOW + timedelta(seconds=1), recovery_complete=True,
                daily_net_pnl_won=None, daily_net_pnl_source=None,
                data_gap_seconds=0, submission_unknown_count=0,
                reconnect_count=0, balance_mismatch_count=0, data_path="nas",
                reconciliation_pending=True,
                owned_position_quantities=(("005930", 1),),
            ),
            account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
            strategy_ref=self.spec.strategy_ref,
        )
        self.assertEqual(MockAutomationGateStatus.BLOCKED, gate.status)
        self.assertIn("RECONCILIATION_PENDING", gate.reasons)
        self.assertIsNone(record)
        self.assertIsNone(receipt)

    def test_native_read_scope_observes_peer_stop_and_recovery_after_completed_statement(self) -> None:
        import threading

        runtime, execution, transport = self._admitted_runtime()
        recovery = MockAccountRecovery(AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), ())
        risk = build_mock_automation_risk_snapshot(
            recovery, (), (), (), execution_run_id=runtime.run_id,
            binding_revision=self.binding["binding_revision"], reconciliation_revision=1,
            observed_at=NOW, broker_query_started_at=NOW, broker_query_completed_at=NOW,
        )
        self.forward.save_mock_automation_risk_snapshot(risk)
        first = record_mock_automation_recovery_from_risk(
            self.forward, runtime, recovery, risk, account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        admission = self.forward.load_mock_automation_admission_for_spec(ACCOUNT_REF, self.spec.spec_id)
        lease = self.forward.load_mock_automation_lease_for_admission(ACCOUNT_REF, admission.admission_id)
        original_control = self.forward.load_mock_automation_control(ACCOUNT_REF)
        done, written, errors = threading.Event(), {}, []

        def write_new_cut_and_stop():
            try:
                later = NOW + timedelta(milliseconds=1)
                next_risk = build_mock_automation_risk_snapshot(
                    recovery, (), (), (), execution_run_id=runtime.run_id,
                    binding_revision=self.binding["binding_revision"], reconciliation_revision=2,
                    observed_at=later, broker_query_started_at=later, broker_query_completed_at=later,
                )
                self.forward.save_mock_automation_risk_snapshot(next_risk)
                written["recovery"] = record_mock_automation_recovery_from_risk(
                    self.forward, runtime, recovery, next_risk,
                    account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
                )
                written["risk"] = next_risk
                written["stop"] = emergency_stop_mock_automation(
                    self.forward, runtime, account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
                    stopped_at=later, reason="separate native writer during completed PK read",
                    expected_control_revision=original_control.control_revision,
                )
                written["control"] = self.forward.load_mock_automation_control(ACCOUNT_REF)
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()

        writer = threading.Thread(target=write_new_cut_and_stop, daemon=True)
        try:
            with self.forward.mock_automation_read_scope() as reader:
                self.assertIsNotNone(reader, "actual SQLite/PG factory must be selected")
                self.assertEqual(self.spec, self.forward.load_mock_automation_spec(
                    ACCOUNT_REF, self.spec.spec_id, _reader=reader))
                # The first PK result has been fully consumed. The writer uses
                # the normal store APIs and their separate native connections.
                writer.start()
                self.assertTrue(done.wait(3), "writer must finish before the read scope closes")
                self.assertFalse(errors, repr(errors))
                self.assertEqual(self.profile, self.forward.load_profile(
                    self.spec.strategy_ref, self.spec.forward_profile_id, _reader=reader))
                self.assertEqual(lease, self.forward.load_mock_automation_lease_for_admission(
                    ACCOUNT_REF, admission.admission_id, _reader=reader))
                observed = self.forward.load_latest_mock_automation_recovery(
                    ACCOUNT_REF, admission.admission_id, _reader=reader)
                self.assertEqual(written["recovery"], observed)
                self.assertNotEqual(first.decision_id, observed.decision_id)
                self.assertEqual(written["risk"].snapshot_id, observed.risk_snapshot_id)
                self.assertEqual(2, observed.risk_reconciliation_revision)
                self.assertEqual(written["stop"], self.forward.load_latest_mock_automation_stop(
                    ACCOUNT_REF, admission.admission_id, _reader=reader))
                control = self.forward.load_mock_automation_control(ACCOUNT_REF, _reader=reader)
                self.assertEqual(written["control"], control)
                self.assertEqual(MockAutomationDesiredState.STOPPED, control.desired_state)
                self.assertEqual(original_control.control_revision + 1, control.control_revision)
                self.assertEqual(admission.execution_run_id, control.execution_run_id)
        finally:
            if writer.ident is not None:
                writer.join(3)
            self.assertFalse(writer.is_alive())
        self.assertEqual(written["recovery"], self.forward.load_latest_mock_automation_recovery(
            ACCOUNT_REF, admission.admission_id))
        self.assertEqual(written["control"], self.forward.load_mock_automation_control(ACCOUNT_REF))
        self.assertEqual(admission, self.forward.load_mock_automation_admission_for_spec(
            ACCOUNT_REF, self.spec.spec_id))
        self.assertEqual(0, transport.calls)

    def test_native_read_scope_closes_and_short_circuits_before_admission_on_stale_risk(self) -> None:
        import sqlite3
        from unittest.mock import patch
        from kiwoom_monitor.application.mock_automation_execution import _native_gate_context

        runtime, execution, transport = self._admitted_runtime()
        recovery = MockAccountRecovery(AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), ())
        first = build_mock_automation_risk_snapshot(
            recovery, (), (), (), execution_run_id=runtime.run_id,
            binding_revision=self.binding["binding_revision"], reconciliation_revision=1,
            observed_at=NOW, broker_query_started_at=NOW, broker_query_completed_at=NOW,
        )
        self.forward.save_mock_automation_risk_snapshot(first)
        record_mock_automation_recovery_from_risk(
            self.forward, runtime, recovery, first, account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )
        later = NOW + timedelta(milliseconds=1)
        current = build_mock_automation_risk_snapshot(
            recovery, (), (), (), execution_run_id=runtime.run_id,
            binding_revision=self.binding["binding_revision"], reconciliation_revision=2,
            observed_at=later, broker_query_started_at=later, broker_query_completed_at=later,
        )
        self.forward.save_mock_automation_risk_snapshot(current)
        connect, handles, statements = self.store._connect, [], []

        class CursorPassthrough:
            def __init__(self, raw): self.raw = raw
            def __getattr__(self, name): return getattr(self.raw, name)
            def __enter__(self): self.raw.__enter__(); return self
            def __exit__(self, *args): return self.raw.__exit__(*args)
            def execute(self, sql, parameters=()):
                statements.append(str(sql))
                self.raw.execute(sql, parameters)
                return self

        class HandlePassthrough:
            def __init__(self, raw): self.raw, self.closes, self._closed = raw, 0, False
            def __getattr__(self, name): return getattr(self.raw, name)
            @property
            def closed(self): return bool(getattr(self.raw, "closed", self._closed))
            def cursor(self, *args, **kwargs): return CursorPassthrough(self.raw.cursor(*args, **kwargs))
            def __enter__(self): self.raw.__enter__(); return self
            def __exit__(self, *args):
                was_open = not self.closed
                try:
                    return self.raw.__exit__(*args)
                finally:
                    if was_open and bool(getattr(self.raw, "closed", False)):
                        self._record_native_exit()
            def _record_native_exit(self):
                self.closes += 1
                self._closed = True
            def close(self):
                self.closes += 1
                try:
                    return self.raw.close()
                finally:
                    self._closed = True

        def tracked_connect():
            handle = HandlePassthrough(connect())
            handles.append(handle)
            return handle

        with patch.object(self.forward, "mock_automation_read_scope",
                          wraps=self.forward.mock_automation_read_scope) as scope, \
                patch.object(self.store, "mock_automation_read_scope",
                             wraps=self.store.mock_automation_read_scope) as native_scope, \
                patch.object(self.store, "_connect", side_effect=tracked_connect), \
                patch.object(self.forward, "load_mock_automation_admission_for_spec",
                             wraps=self.forward.load_mock_automation_admission_for_spec) as admission, \
                patch.object(self.forward, "load_latest_mock_automation_recovery",
                             wraps=self.forward.load_latest_mock_automation_recovery) as recovered:
            with self.assertRaisesRegex(RuntimeError, "^latest mock automation risk snapshot changed$"):
                _native_gate_context(self.forward, runtime, recovery, first, ACCOUNT_REF, self.spec.spec_id)
        scope.assert_called_once_with()
        native_scope.assert_called_once_with()
        admission.assert_not_called()
        recovered.assert_not_called()
        self.assertEqual(1, len(handles), "no default-reader fallback or second context after the failure")
        self.assertEqual(1, len([sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]))
        self.assertEqual(1, handles[0].closes)
        self.assertTrue(handles[0].closed)
        if isinstance(handles[0].raw, sqlite3.Connection):
            with self.assertRaises(sqlite3.ProgrammingError):
                handles[0].raw.execute("SELECT 1")
        else:
            self.assertTrue(handles[0].raw.closed)
        self.assertEqual(0, transport.calls)

    def test_native_gate_rejects_noncanonical_stored_spec_without_republishing(self) -> None:
        from unittest.mock import patch
        from kiwoom_monitor.application.mock_automation_execution import _native_gate_context

        runtime, _, transport = self._admitted_runtime()
        recovery = MockAccountRecovery(AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), ())
        risk = build_mock_automation_risk_snapshot(
            recovery, (), (), (), execution_run_id=runtime.run_id,
            binding_revision=self.binding["binding_revision"], reconciliation_revision=1,
            observed_at=NOW, broker_query_started_at=NOW, broker_query_completed_at=NOW,
        )
        self.forward.save_mock_automation_risk_snapshot(risk)
        record_mock_automation_recovery_from_risk(
            self.forward, runtime, recovery, risk, account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id,
        )

        collection = self.forward.MOCK_AUTOMATION_SPEC_COLLECTION
        key = (collection, ACCOUNT_REF, self.spec.spec_id)
        _native_gate_context(self.forward, runtime, recovery, risk, ACCOUNT_REF, self.spec.spec_id)
        self.store.upsert_documents(collection, [{
            "owner": ACCOUNT_REF, "key": self.spec.spec_id,
            "document": {**self.spec.to_dict(), "unexpected_field": "stored"},
        }])
        self.assertEqual(self.spec, self.forward.load_mock_automation_spec(ACCOUNT_REF, self.spec.spec_id))
        with self.assertRaisesRegex(ValueError, "^immutable document conflict:"):
            self.forward.save_mock_automation_spec(self.spec)

        with patch.object(self.store, "load_document", wraps=self.store.load_document) as load, \
                patch.object(self.store, "insert_immutable_document", wraps=self.store.insert_immutable_document) as insert:
            with self.assertRaisesRegex(ValueError, "^immutable document conflict:"):
                _native_gate_context(self.forward, runtime, recovery, risk, ACCOUNT_REF, self.spec.spec_id)
        self.assertEqual(1, sum(call.args == key for call in load.call_args_list))
        insert.assert_not_called()
        self.assertEqual(0, transport.calls)

    def test_recovery_input_scope_closes_before_runtime_and_preserves_scalar_failures(self) -> None:
        from contextlib import ExitStack, contextmanager, nullcontext
        from unittest.mock import patch

        runtime, execution, transport = self._admitted_runtime()
        recovery = MockAccountRecovery(AccountSnapshot(ACCOUNT_REF, 1_000_000, 0, {}, NOW), ())
        metrics = MockAutomationRecoveryMetrics(
            observed_at=NOW, recovery_complete=True, daily_net_pnl_won=0,
            data_gap_seconds=0, submission_unknown_count=0, reconnect_count=0, balance_mismatch_count=0,
        )
        methods = {"spec": "load_mock_automation_spec", "admission": "load_mock_automation_admission_for_spec",
                   "lease": "load_mock_automation_lease_for_admission"}
        originals = {name: getattr(self.forward, attr) for name, attr in methods.items()}
        original_scope = self.forward.mock_automation_read_scope
        original_orders, original_save = runtime.set_new_orders_enabled, self.forward.save_mock_automation_recovery_decision
        missing = {"spec": "mock automation operating spec is not stored",
                   "admission": "mock automation admission is missing", "lease": "mock automation lease receipt is missing"}
        for native_reader in (True, False):
            for failure in ("", "spec", "admission", "lease", "decode"):
                with self.subTest(native_reader=native_reader, failure=failure):
                    events, active, current_reader = [], False, None
                    malformed = ValueError("controlled recovery spec decode failure")

                    @contextmanager
                    def observed_scope():
                        nonlocal active, current_reader
                        active = True
                        try:
                            with (original_scope() if native_reader else nullcontext()) as current_reader:
                                if native_reader:
                                    self.assertIsNotNone(current_reader)
                                yield current_reader
                        finally:
                            active = False
                            events.append("closed")

                    def observe_loader(name):
                        def load(*args, **kwargs):
                            self.assertTrue(active)
                            self.assertEqual({"_reader": current_reader} if native_reader else {}, kwargs)
                            events.append(name)
                            if failure == "decode" and name == "spec":
                                raise malformed
                            return None if failure == name else originals[name](*args, **kwargs)
                        return load

                    def orders(enabled):
                        self.assertFalse(active)
                        events.append("orders")
                        return original_orders(enabled)

                    def save(value):
                        self.assertFalse(active)
                        events.append("save")
                        return original_save(value)

                    with ExitStack() as patches:
                        patches.enter_context(patch.object(self.forward, "mock_automation_read_scope", observed_scope))
                        for name, attr in methods.items():
                            patches.enter_context(patch.object(self.forward, attr, side_effect=observe_loader(name)))
                        patches.enter_context(patch.object(runtime, "set_new_orders_enabled", side_effect=orders))
                        patches.enter_context(patch.object(self.forward, "save_mock_automation_recovery_decision", side_effect=save))
                        if failure:
                            with self.assertRaisesRegex(ValueError, missing.get(failure, "controlled recovery spec decode failure")) as raised:
                                record_mock_automation_recovery(self.forward, runtime, recovery, metrics,
                                    account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id)
                            if failure == "decode":
                                self.assertIs(malformed, raised.exception)
                        else:
                            decision = record_mock_automation_recovery(self.forward, runtime, recovery, metrics,
                                account_ref=ACCOUNT_REF, spec_id=self.spec.spec_id)
                            self.assertEqual(MockAutomationRecoveryStatus.CLEARED_ORDERS_DISABLED, decision.status)
                    expected = ["spec"] if failure in {"spec", "decode"} else ["spec", "admission"] if failure == "admission" else ["spec", "admission", "lease"]
                    self.assertEqual(expected + ["closed"] + ([] if failure else ["orders", "save"]), events)
                    self.assertFalse(active)
        self.assertEqual(0, transport.calls)


if __name__ == "__main__":
    unittest.main()
