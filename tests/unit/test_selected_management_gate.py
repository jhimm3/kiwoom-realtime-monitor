"""Independent application preflight: current target lease, original policy, no READY copy."""
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest

from kiwoom_monitor.application.mock_automation_execution import (
    MockAutomationDesiredState, MockAutomationGateStatus, MockAutomationLiveMetrics,
    _content_id, _selected_management_gate_policy, assess_mock_automation_decision,
    assess_mock_automation_decision_from_risk, dispatch_mock_automation_decision,
)
from kiwoom_monitor.domain.order_contract import AccountSnapshot, OrderPlanRef, OrderState
from kiwoom_monitor.domain.selected_order_evidence import selected_order_management_context_from_dict
from kiwoom_monitor.domain.strategy_contract import StrategyDecision
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery


class SelectedManagementGateTests(unittest.IsolatedAsyncioTestCase):
    from tests.unit.test_selected_order_recovery import SelectedOrderRecoveryTests
    source, target = SelectedOrderRecoveryTests.source, SelectedOrderRecoveryTests.target

    async def asyncSetUp(self):
        from tests.unit.test_selected_order_management import prepared_target, prepare_current_management
        from kiwoom_monitor.application.mock_automation_recovery import (
            MOCK_AUTOMATION_RECOVERY_VERSION, MockAutomationRecoveryDecision, MockAutomationRecoveryStatus,
            mock_account_recovery_fingerprint,
        )
        from kiwoom_monitor.application.mock_automation_admission import MockAutomationControl, MOCK_AUTOMATION_CONTROL_VERSION
        from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository, ExecutionRecord
        from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
        data = await prepared_target(self)
        result, _ = await prepare_current_management(data)
        self.context = selected_order_management_context_from_dict(result['receipt']['context'])
        original = ForwardEvaluationRepository(data.target.store, selected_management_context=self.context)
        self.spec = original.load_mock_automation_spec(self.context.account_ref, self.context.spec_id)
        self.admission = original.load_mock_automation_admission_for_spec(self.context.account_ref, self.context.spec_id)
        self.lease = original.load_mock_automation_lease_for_admission(self.context.account_ref, self.context.admission_id)
        self.policy = original.selected_management_policy
        self.now = data.bundle.clock[0]
        head = self.policy['source_plan_position']
        self.symbol, self.quantity = head['symbol'], head['quantity']
        self.plan = OrderPlanRef(head['plan_id'], head['policy_ref'], head['revision'])
        # Explicit pure-gate input after broker-confirmed cancellation of the old BUY remainder.
        account = AccountSnapshot(self.context.account_ref, 250_000, 0, {self.symbol: self.quantity}, self.now)
        self.recovery = MockAccountRecovery(account, ())
        events = ExecutionRepository(data.target.store).account_events('mock', self.context.account_ref).events
        self.risk = build_mock_automation_risk_snapshot(self.recovery, events, (), (),
            execution_run_id=self.context.execution_run_id, binding_revision=self.context.binding_revision,
            reconciliation_revision=1, observed_at=self.now, broker_query_started_at=self.now,
            broker_query_completed_at=self.now)
        self.risk_events = events
        self.recovered = MockAutomationRecoveryDecision('pure-current-recovery', MOCK_AUTOMATION_RECOVERY_VERSION,
            self.admission.admission_id, self.lease.receipt_id, self.spec.spec_id, self.context.account_ref,
            self.context.execution_run_id, self.now, self.now, mock_account_recovery_fingerprint(self.recovery),
            0, 1, 0, 250_000, None, 0, 0, 0, 0, MockAutomationRecoveryStatus.MANAGE_ONLY_ORDERS_DISABLED, (),
            self.risk.snapshot_id, self.risk.reconciliation_revision)
        self.control = MockAutomationControl(MOCK_AUTOMATION_CONTROL_VERSION, self.context.account_ref,
            MockAutomationDesiredState.RUNNING, 2, self.spec.spec_id, self.context.execution_run_id,
            self.now, 'explicit current synthetic manage-only control', entry_resume_required=True)
        self.metrics = MockAutomationLiveMetrics(self.now, True, None, None, 0, 0, 0, 0,
            self.spec.data_path, owned_position_quantities=((self.symbol, self.quantity),))
        self.gates, self.submissions = [], []
        def forbidden(*args, **kwargs):
            raise AssertionError('selected management cannot load a profile or re-publish READY')
        self.repository = SimpleNamespace(selected_management_context=self.context,
            selected_management_policy=deepcopy(self.policy),
            load_mock_automation_spec=lambda *args, **kwargs: self.spec,
            load_profile=forbidden, save_mock_automation_spec=forbidden,
            revalidate_stored_mock_automation_spec=forbidden,
            load_mock_automation_admission_for_spec=lambda *args, **kwargs: self.admission,
            load_mock_automation_lease_for_admission=lambda *args, **kwargs: self.lease,
            load_latest_mock_automation_recovery=lambda *args, **kwargs: self.recovered,
            load_latest_mock_automation_stop=lambda *args, **kwargs: None,
            load_mock_automation_control=lambda *args, **kwargs: self.control,
            load_latest_mock_automation_risk=lambda *args, **kwargs: self.risk,
            load_mock_automation_approved_gate=lambda *args, **kwargs: None,
            save_mock_automation_decision_gate=self.gates.append,
            save_mock_automation_dispatch_receipt=lambda receipt: None)
        def submit(intent, account, **kwargs):
            self.submissions.append(intent)
            return ExecutionRecord(intent, OrderState.ACCEPTED, 'pure-current-sell', 0, (), None, self.now)
        self.runtime = SimpleNamespace(account_ref=self.context.account_ref, run_id=self.context.execution_run_id,
            set_new_orders_enabled=lambda enabled: self.assertFalse(enabled), automation_decision_guard=nullcontext,
            server_now=lambda: self.now, load_active_intents=lambda: (), load_intent=lambda identity: None,
            submit_automation_intent=submit)
        self.family = _selected_management_gate_policy(self.repository, self.spec, self.admission, self.lease, self.plan)[1]

    def decision(self, action='EXIT', quantity=None):
        from tests.unit.test_mock_automation_specification import _automation_decision
        quantity = self.quantity if quantity is None else quantity
        body = _automation_decision(self.context.execution_run_id, action, self.now).to_dict()
        body.pop('decision_id')
        body.update(symbol=self.symbol, quantity=quantity,
                    required_capital_won=quantity * body['signal_reference_price'] if action in {'ENTER', 'ADD'} else 0)
        return StrategyDecision(decision_id=_content_id('decision', body), **body)

    def assess(self, decision=None, **changes):
        values = dict(strategy_ref=self.spec.strategy_ref, profile=None, control=self.control, server_now=self.now,
            plan=self.plan, selected_management_context=self.context, policy_family=self.family,
            risk_snapshot_id=self.risk.snapshot_id, risk_reconciliation_revision=self.risk.reconciliation_revision)
        values.update(changes)
        return assess_mock_automation_decision(self.spec, self.admission, self.lease, self.recovered,
            decision or self.decision(), self.recovery, self.metrics, **values)

    def test_current_selected_policy_allows_exact_owned_exit_without_profile_or_source_group_restore(self):
        gate = self.assess()
        self.assertEqual(MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION, gate.status, gate.reasons)
        self.assertEqual((), gate.reasons)
        self.assertEqual(self.lease.receipt_id, gate.lease_receipt_id)
        self.assertNotEqual(self.spec.binding_revision, self.context.binding_revision)
        self.assertNotEqual(self.spec.credential_profile_id, self.context.credential_profile_id)
        self.assertIsNone(self.control.account_selection)
        ordinary = self.assess(selected_management_context=None, policy_family=None)
        self.assertEqual(MockAutomationGateStatus.BLOCKED, ordinary.status)
        self.assertIn('FORWARD_PROFILE_MISSING', ordinary.reasons)

    def test_selected_new_entries_are_blocked_before_existing_intent_lookup_or_submission(self):
        def forbidden_lookup(identity):
            raise AssertionError('selected entry reached existing-intent dispatch')
        self.runtime.load_intent = forbidden_lookup
        for action in ('ENTER', 'ADD', 'REENTER'):
            gate, record, receipt = dispatch_mock_automation_decision(self.repository, self.runtime,
                self.decision(action), self.recovery, self.metrics, account_ref=self.context.account_ref,
                spec_id=self.spec.spec_id, strategy_ref=self.spec.strategy_ref, plan=self.plan,
                risk_snapshot_id=self.risk.snapshot_id, risk_reconciliation_revision=self.risk.reconciliation_revision)
            self.assertEqual(MockAutomationGateStatus.BLOCKED, gate.status)
            self.assertIn('SELECTED_MANAGEMENT_ENTRY_DISABLED', gate.reasons)
            self.assertEqual((None, None), (record, receipt))
        self.assertEqual([], self.submissions)

    def test_selected_scope_plan_and_current_lease_cannot_be_substituted(self):
        with self.assertRaisesRegex(ValueError, 'original plan'):
            _selected_management_gate_policy(self.repository, self.spec, self.admission, self.lease,
                replace(self.plan, plan_id='different-native-plan'))
        with self.assertRaisesRegex(ValueError, 'current target lease'):
            _selected_management_gate_policy(self.repository, self.spec, self.admission,
                replace(self.lease, receipt_id='not-the-current-target-lease'), self.plan)
        for context in (replace(self.context, spec_id='different-source-spec'),
                replace(self.context, account_ref='00000000-0000-4000-8000-000000000001'),
                replace(self.context, execution_run_id='different-source-run')):
            gate = self.assess(selected_management_context=context)
            self.assertEqual(MockAutomationGateStatus.BLOCKED, gate.status)
            self.assertIn('SELECTED_MANAGEMENT_CONTEXT_CHANGED', gate.reasons)
        for control in (replace(self.control, desired_state=MockAutomationDesiredState.STOPPED),
                replace(self.control, account_ref='00000000-0000-4000-8000-000000000001'),
                replace(self.control, entry_resume_required=False)):
            self.assertIn('SELECTED_MANAGEMENT_CONTEXT_CHANGED', self.assess(control=control).reasons)
        self.assertIn('FORWARD_PROFILE_MISSING', self.assess(policy_family='unverified-family').reasons)
        self.assertIn('SELECTED_MANAGEMENT_PLAN_REQUIRED', self.assess(plan=None).reasons)

    def test_unknown_unassigned_or_pending_quantity_cannot_become_selected_owned_exit(self):
        for metrics, reason in (
                (replace(self.metrics, owned_position_quantities=()), 'RUN_POSITION_UNVERIFIED'),
                (replace(self.metrics, pending_sell_quantities=((self.symbol, self.quantity),)), 'INSUFFICIENT_POSITION'),
                (replace(self.metrics, reconciliation_pending=True), 'RECONCILIATION_PENDING'),
                (replace(self.metrics, submission_unknown_count=self.spec.maximum_submission_unknown_count + 1),
                 'SUBMISSION_UNKNOWN_LIMIT_EXCEEDED')):
            saved = self.metrics
            self.metrics = metrics
            gate = self.assess()
            self.metrics = saved
            self.assertEqual(MockAutomationGateStatus.BLOCKED, gate.status)
            self.assertIn(reason, gate.reasons)
        broker = self.recovery
        self.recovery = replace(broker, account=replace(broker.account, positions={self.symbol: self.quantity - 1}))
        self.assertIn('INSUFFICIENT_POSITION', self.assess().reasons)

    def test_dispatch_uses_current_sell_family_and_risk_rejects_the_old_binding_epoch(self):
        gate, record, _ = dispatch_mock_automation_decision(self.repository, self.runtime, self.decision(),
            self.recovery, self.metrics, account_ref=self.context.account_ref, spec_id=self.spec.spec_id,
            strategy_ref=self.spec.strategy_ref, plan=self.plan,
            risk_snapshot_id=self.risk.snapshot_id, risk_reconciliation_revision=self.risk.reconciliation_revision)
        self.assertEqual(MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION, gate.status, gate.reasons)
        self.assertEqual(('SELL', self.quantity, 'close_execution_adapter/v1', self.plan),
            (record.intent.side.value, record.intent.quantity, record.intent.policy_version, record.intent.plan))
        from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
        wrong_epoch = build_mock_automation_risk_snapshot(self.recovery, self.risk_events, (), (),
            execution_run_id=self.context.execution_run_id, binding_revision=self.spec.binding_revision,
            reconciliation_revision=2, observed_at=self.now, broker_query_started_at=self.now,
            broker_query_completed_at=self.now)
        self.risk = wrong_epoch
        with self.assertRaisesRegex(ValueError, 'target epoch changed'):
            assess_mock_automation_decision_from_risk(self.repository, self.runtime, self.decision(),
                self.recovery, wrong_epoch, account_ref=self.context.account_ref, spec_id=self.spec.spec_id,
                strategy_ref=self.spec.strategy_ref, plan=self.plan)

    def test_pure_leader_reduce_requires_original_native_plan_and_preserves_remaining_owned_position(self):
        from kiwoom_monitor.application.mock_automation_admission import (
            build_mock_automation_admission, build_mock_automation_lease_receipt,
        )
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.domain.execution_activation import LEADER_NATIVE_PLAN_MODE
        from tests.unit.test_leader_breakout import execution_config
        from tests.unit.test_mock_automation_specification import (
            NOW, MockUnfilledReplacementDispatchTests, mock_specification_inputs,
        )
        from datetime import timedelta
        store = SQLiteQueryStore(':memory:')
        store.initialize()
        self.addCleanup(store.close)
        config = execution_config()
        config = replace(config, contexts=(replace(config.contexts[0],
            available_at=(NOW - timedelta(hours=1)).isoformat(),
            valid_until=(NOW + timedelta(days=1)).isoformat()),))
        source = mock_specification_inputs(store, leader_config=config,
            unfilled_execution_policy=MockUnfilledReplacementDispatchTests.unfilled_execution_policy)
        spec = replace(source.spec, execution_plan_mode=LEADER_NATIVE_PLAN_MODE)
        admission = build_mock_automation_admission(spec, requested_at=self.now)
        lease = build_mock_automation_lease_receipt(admission, admitted_at=self.now)
        context = replace(self.context, account_ref=admission.account_ref, execution_run_id=admission.execution_run_id,
            spec_id=spec.spec_id, admission_id=admission.admission_id, source_admission_version=admission.version,
            source_account_selection_id=None, member_spec_id=None)
        recovered = replace(self.recovered, admission_id=admission.admission_id, lease_receipt_id=lease.receipt_id,
            spec_id=spec.spec_id, account_ref=admission.account_ref, execution_run_id=admission.execution_run_id)
        control = replace(self.control, account_ref=admission.account_ref, active_spec_id=spec.spec_id,
            execution_run_id=admission.execution_run_id)
        recovery = replace(self.recovery, account=replace(self.recovery.account, account_ref=admission.account_ref,
            positions={self.symbol: 3}))
        body = self.decision('REDUCE', quantity=1).to_dict()
        body.pop('decision_id')
        body['run_id'] = admission.execution_run_id
        decision = StrategyDecision(decision_id=_content_id('decision', body), **body)
        options = dict(strategy_ref=spec.strategy_ref, profile=None, control=control, server_now=self.now,
            risk_snapshot_id=self.risk.snapshot_id, risk_reconciliation_revision=self.risk.reconciliation_revision,
            plan=self.plan, position_action_ref='leader-plan-action/v1:' + 'a' * 64,
            selected_management_context=context, policy_family=source.package.candidate_spec['family'])
        metrics = replace(self.metrics, data_path=spec.data_path, owned_position_quantities=((self.symbol, 3),))
        gate = assess_mock_automation_decision(spec, admission, lease, recovered, decision, recovery, metrics, **options)
        self.assertEqual(MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION, gate.status, gate.reasons)
        self.assertEqual(('REDUCE', 1, self.plan), (gate.action, gate.quantity, gate.plan))
        blocked = assess_mock_automation_decision(spec, admission, lease, recovered, decision, recovery,
            replace(metrics, owned_position_quantities=((self.symbol, 1),)), **options)
        self.assertIn('PARTIAL_REDUCTION_REQUIRES_REMAINING_POSITION', blocked.reasons)
        with self.assertRaisesRegex(ValueError, 'native leader plan'):
            assess_mock_automation_decision(spec, admission, lease, recovered, decision, recovery, metrics,
                **{**options, 'plan': None})

    def test_source_submission_approval_cannot_be_reused_for_selected_current_dispatch(self):
        from dataclasses import asdict
        from kiwoom_monitor.application.mock_automation_execution import _intent_for_decision
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRecord
        from kiwoom_monitor.domain.order_contract import PlannedOrderIntent
        decision = self.decision()
        intent = _intent_for_decision(self.spec, self.admission, decision, family_id=self.family)
        intent = PlannedOrderIntent(**asdict(intent), plan=self.plan)
        old_approval = replace(self.assess(decision), lease_receipt_id='historical-source-lease')
        self.runtime.load_intent = lambda identity: ExecutionRecord(intent, OrderState.QUEUED,
            None, 0, (), None, self.now)
        self.repository.load_mock_automation_approved_gate = lambda *args, **kwargs: old_approval
        with self.assertRaisesRegex(RuntimeError, 'source submission approval'):
            dispatch_mock_automation_decision(self.repository, self.runtime, decision,
                self.recovery, self.metrics, account_ref=self.context.account_ref, spec_id=self.spec.spec_id,
                strategy_ref=self.spec.strategy_ref, plan=self.plan,
                risk_snapshot_id=self.risk.snapshot_id, risk_reconciliation_revision=self.risk.reconciliation_revision)
        self.assertEqual([], self.submissions)
