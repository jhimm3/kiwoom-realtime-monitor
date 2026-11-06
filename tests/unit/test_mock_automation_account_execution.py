"""One native account lease, exact member admissions and shared planned cash."""
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


def grouped_inputs(case, store, *, capital=150_000):
    from tests.unit import test_mock_automation_specification as support
    from kiwoom_monitor.application.mock_automation_candidate import evaluate_candidate_eligibility
    from kiwoom_monitor.application.mock_automation_specification import publish_ready_mock_automation_spec
    from kiwoom_monitor.domain.execution_activation import (
        MockAutomationAccountLimits, MockAutomationAccountSelection, strategy_stage_revision,
    )
    base = support.mock_specification_inputs(store, publish_candidate=False)
    specs = []
    for name in ('alpha', 'beta'):
        package = replace(base.package, strategy_ref=name, source_final_batch_id='group-batch-'+name,
                          source_final_run_id='group-final-'+name)
        policy = replace(base.policy, strategy_ref=name)
        receipt = evaluate_candidate_eligibility(package, policy, account_ref=support.ACCOUNT_REF)
        base.repository.publish_mock_automation_candidate(package, policy, receipt,
            credential_profile_id=support.PROFILE_ID, expected_binding_revision=base.binding['binding_revision'])
        profile = replace(base.profile, strategy_ref=name, policy_version=policy.policy_id)
        refs = (package.package_hash, receipt.receipt_id, base.shadow_event['event_id'])
        stages = tuple(strategy_stage_revision(strategy_ref=name, previous_stage=old.previous_stage,
            stage=old.stage, evidence_refs=(ref,), reason='explicit synthetic account member',
            decided_at=old.decided_at) for old, ref in zip(base.stages, refs, strict=True))
        spec = replace(base.spec, strategy_ref=name, candidate_package_hash=package.package_hash,
            final_batch_id=package.source_final_batch_id, final_run_id=package.source_final_run_id,
            forward_profile_id=profile.profile_id, shadow_evidence_ref=stages[-1].revision_id,
            maximum_capital_won=80_000)
        ready, _ = publish_ready_mock_automation_spec(base.repository, profile=profile, stage_revisions=stages,
            spec=spec, shadow_event=base.shadow_event, current_binding=base.binding)
        case.assertEqual('READY', ready.status.value)
        specs.append(spec)
    limits = MockAutomationAccountLimits(2, 2, capital, 300_000, 5, 0, 2, 0)
    selection = MockAutomationAccountSelection(specs[0].account_scope, support.PROFILE_ID,
        base.binding['binding_revision'], datetime.fromisoformat(base.binding['verified_at']),
        base.spec.frozen_at, tuple(spec.spec_id for spec in specs), limits)
    base.repository.publish_mock_automation_account_selection(selection,
        expected_implementation_hash=lambda _: base.package.scientific_implementation_hash)
    return SimpleNamespace(repository=base.repository, selection=selection, specs=tuple(specs),
        binding=base.binding, implementation_hash=base.package.scientific_implementation_hash)


def group_runtime(case, store, data):
    from tests.unit import test_mock_automation_specification as support
    from kiwoom_monitor.application.mock_automation_admission import execution_run_id_for_spec
    from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
    from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
    clock = [support.NOW + timedelta(hours=2)]
    case.enterContext(patch.object(store, '_execution_wall_now', lambda: clock[0], create=True))
    execution = ExecutionRepository(store)
    transport = support._FakeBrokerTransport(lambda: clock[0])
    run_id = execution_run_id_for_spec(data.selection.primary_spec_id)
    runtime = ExecutionRuntime(OrderLifecycle(execution, transport, now_provider=lambda: clock[0]), execution,
        account_ref=support.ACCOUNT_REF, run_id=run_id, owner_token='one-explicit-account-owner',
        now_provider=lambda: clock[0])
    case.addCleanup(runtime.stop)
    return SimpleNamespace(runtime=runtime, execution=execution, transport=transport, clock=clock, run_id=run_id)


def admit_group(data, owner, *, expected_revision=0):
    from kiwoom_monitor.application.mock_automation_admission import admit_mock_automation_account_selection
    return admit_mock_automation_account_selection(data.repository, owner.runtime,
        account_ref=data.selection.account_scope.account_ref, selection_id=data.selection.selection_id,
        requested_at=owner.clock[0], expected_control_revision=expected_revision,
        expected_implementation_hash=lambda _: data.implementation_hash)


def verify_group_admission(case, store):
    from kiwoom_monitor.application.mock_automation_admission import (
        MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION, mock_automation_admission_from_dict,
        mock_automation_control_from_dict,
    )
    data = grouped_inputs(case, store)
    owner = group_runtime(case, store, data)
    claim = owner.execution.claim_runtime
    with patch.object(owner.execution, 'claim_runtime', wraps=claim) as acquire:
        admissions, receipts, control = admit_group(data, owner)
    case.assertEqual(1, acquire.call_count, 'members must share the existing account authority')
    case.assertEqual(2, len(admissions))
    case.assertEqual({owner.run_id}, {value.execution_run_id for value in admissions})
    case.assertEqual({owner.run_id}, {value.execution_run_id for value in receipts})
    case.assertEqual(MOCK_AUTOMATION_ACCOUNT_CONTROL_VERSION, control.version)
    case.assertEqual(data.selection, control.account_selection)
    for admission, spec in zip(admissions, data.specs, strict=True):
        case.assertEqual(spec.spec_id, admission.spec_id)
        case.assertEqual(admission, mock_automation_admission_from_dict(admission.to_dict()))
        case.assertTrue(control.includes_spec(spec.spec_id))
    case.assertFalse(control.includes_spec('unselected'))
    case.assertEqual(control, mock_automation_control_from_dict(control.to_dict()))
    case.assertEqual((admissions, receipts, control), admit_group(data, owner, expected_revision=1))
    case.assertEqual((), owner.runtime.load_active_intents())
    case.assertEqual([], owner.transport.calls)
    case.assertFalse(owner.runtime._new_orders_enabled)
    from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
    from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
    peer_repository=ExecutionRepository(store)
    peer=ExecutionRuntime(OrderLifecycle(peer_repository,owner.transport,now_provider=lambda:owner.clock[0]),
        peer_repository,account_ref=owner.runtime.account_ref,run_id=owner.run_id,owner_token='different-account-owner',
        now_provider=lambda:owner.clock[0])
    case.addCleanup(peer.stop)
    with case.assertRaisesRegex(RuntimeError,'another mock execution runtime owns'):
        peer.start(new_orders_enabled=False)
    owner.runtime.heartbeat()
    return data, owner, admissions, receipts, control


def group_planned_inputs(case, store, *, capital=150_000, limits=None):
    from tests.unit import test_mock_automation_specification as support
    from kiwoom_monitor.application.mock_automation_execution import (
        MockAutomationGateStatus, assess_mock_automation_decision_from_risk,
        mock_automation_order_policy_version,
    )
    from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
    from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
    from kiwoom_monitor.domain.order_contract import AccountSnapshot, OrderPlanRef, OrderSide, OrderType, PlannedOrderIntent
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
    data = grouped_inputs(case, store, capital=capital)
    if limits is not None:
        data.selection = replace(data.selection, limits=limits)
        data.repository.publish_mock_automation_account_selection(data.selection,
            expected_implementation_hash=lambda _: data.implementation_hash)
    owner = group_runtime(case, store, data)
    admissions, receipts, control = admit_group(data, owner)
    account = AccountSnapshot(owner.runtime.account_ref, 250_000, 0, {}, owner.clock[0])
    recovery = MockAccountRecovery(account, ())
    risk = build_mock_automation_risk_snapshot(recovery, (), (), (), execution_run_id=owner.run_id,
        binding_revision=data.selection.binding_revision, reconciliation_revision=1, observed_at=owner.clock[0],
        broker_query_started_at=owner.clock[0], broker_query_completed_at=owner.clock[0])
    data.repository.save_mock_automation_risk_snapshot(risk)
    planned = []
    for number, spec in enumerate(data.specs):
        record_mock_automation_recovery_from_risk(data.repository, owner.runtime, recovery, risk,
            account_ref=account.account_ref, spec_id=spec.spec_id)
        decision = support._automation_decision(owner.run_id, 'ENTER', owner.clock[0]-timedelta(milliseconds=number))
        plan = OrderPlanRef('account-plan-'+str(number), 'explicit-synthetic-plan/v1', 0)
        gate = assess_mock_automation_decision_from_risk(data.repository, owner.runtime, decision, recovery, risk,
            account_ref=account.account_ref, spec_id=spec.spec_id, strategy_ref=spec.strategy_ref, plan=plan)
        case.assertEqual(MockAutomationGateStatus.APPROVED_FOR_SINGLE_SUBMISSION, gate.status, gate.reasons)
        data.repository.save_mock_automation_decision_gate(gate)
        profile = data.repository.load_profile(spec.strategy_ref, spec.forward_profile_id)
        intent = PlannedOrderIntent(gate.intent_id, owner.run_id, decision.decision_id, account.account_ref, 'mock',
            gate.symbol, 'KRX', OrderSide.BUY, gate.quantity, OrderType.LIMIT, gate.limit_price, gate.decided_at,
            gate.decided_at+timedelta(seconds=spec.maximum_data_gap_seconds),
            mock_automation_order_policy_version(profile.family_version, spec.session_profile), plan=plan)
        planned.append((spec, gate, intent))
    return data, owner, recovery, risk, tuple(planned)


def verify_group_native_reservation(case, store, *, capital=150_000):
    from kiwoom_monitor.domain.order_contract import OrderState
    data, owner, recovery, risk, planned = group_planned_inputs(case, store, capital=capital)
    def submit(item):
        spec, gate, intent = item
        return owner.runtime.submit_automation_intent(intent, recovery.account, spec_id=spec.spec_id,
            control_revision=gate.control_revision, plan_recovery=recovery)
    first = submit(planned[0])
    case.assertEqual(OrderState.ACCEPTED, first.state)
    if capital < 140_000:
        with case.assertRaisesRegex(RuntimeError, 'MOCK_ACCOUNT_CAPITAL_UNAVAILABLE'):
            submit(planned[1])
        case.assertIsNone(owner.execution.load(planned[1][2].intent_id))
        case.assertIsNone(store.load_document('execution_mock_plan_positions', owner.runtime.account_ref,
            planned[1][2].plan.plan_id))
        case.assertEqual(1, len(owner.transport.calls))
        return
    second = submit(planned[1])
    case.assertEqual(OrderState.ACCEPTED, second.state)
    case.assertEqual(2, len(owner.transport.calls))
    case.assertEqual({owner.run_id}, {first.intent.run_id, second.intent.run_id})
    case.assertNotEqual(first.intent.plan.plan_id, second.intent.plan.plan_id)
    for (spec, _, intent), record in zip(planned, (first, second), strict=True):
        head = store.load_document('execution_mock_plan_positions', owner.runtime.account_ref, intent.plan.plan_id)['document']
        case.assertEqual((spec.spec_id, 0, []), (head['spec_id'], head['quantity'], head['capital_lots']))
        case.assertEqual(record, submit((spec, planned[data.specs.index(spec)][1], intent)))
    case.assertEqual(2, len(owner.transport.calls))
    return data, owner, recovery, risk, planned, (first, second)


def verify_group_native_fill_isolation(case, store):
    from tests.unit import test_mock_automation_specification as support
    from kiwoom_monitor.application.mock_automation_execution import dispatch_mock_automation_decision_from_risk
    from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
    from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
    from kiwoom_monitor.domain.order_contract import AccountSnapshot, BrokerFill, BrokerOrderSnapshot, OrderState
    from kiwoom_monitor.domain.strategy_contract import StrategyDecision
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
    data, owner, recovery, _, planned, buys = verify_group_native_reservation(case, store)
    for number, buy in enumerate(buys):
        owner.clock[0] += timedelta(seconds=1)
        owner.runtime.reconcile(buy.intent.intent_id, BrokerOrderSnapshot(buy.broker_order_id,
            owner.runtime.account_ref, buy.intent.symbol, OrderState.FILLED, 1, 0, owner.clock[0],
            (BrokerFill('actual-member-buy-'+str(number), 1, 70_000, owner.clock[0]),)))
    owner.clock[0] += timedelta(seconds=1)
    recovery = MockAccountRecovery(AccountSnapshot(owner.runtime.account_ref, 110_000, 0,
        {'005930': 2}, owner.clock[0]), ())
    events = owner.execution.account_events('mock', owner.runtime.account_ref).events
    risk = build_mock_automation_risk_snapshot(recovery, events, (), (), execution_run_id=owner.run_id,
        binding_revision=data.selection.binding_revision, reconciliation_revision=2, observed_at=owner.clock[0],
        broker_query_started_at=owner.clock[0], broker_query_completed_at=owner.clock[0])
    case.assertTrue(risk.reconciliation_complete)
    case.assertEqual({'005930': 2}, dict(risk.owned_position_quantities))
    data.repository.save_mock_automation_risk_snapshot(risk)
    for spec in data.specs:
        record_mock_automation_recovery_from_risk(data.repository, owner.runtime, recovery, risk,
            account_ref=owner.runtime.account_ref, spec_id=spec.spec_id)
    def head(plan):
        return store.load_document('execution_mock_plan_positions', owner.runtime.account_ref, plan.plan_id)['document']
    plans = [replace(item[2].plan, expected_revision=head(item[2].plan)['revision']) for item in planned]
    before = [head(plan) for plan in plans]
    case.assertEqual([[[1,70_000]], [[1,70_000]]], [row['capital_lots'] for row in before])
    def exit(spec, decision, plan):
        return dispatch_mock_automation_decision_from_risk(data.repository, owner.runtime, decision, recovery, risk,
            account_ref=owner.runtime.account_ref, spec_id=spec.spec_id, strategy_ref=spec.strategy_ref, plan=plan)
    wrong_member = support._automation_decision(owner.run_id, 'EXIT', owner.clock[0]-timedelta(milliseconds=1))
    with case.assertRaisesRegex(RuntimeError, 'EXECUTION_PLAN_MEMBER_MISMATCH'):
        exit(data.specs[1], wrong_member, plans[0])
    original = support._automation_decision(owner.run_id, 'EXIT', owner.clock[0])
    oversized = original.to_dict()
    oversized.pop('decision_id')
    oversized['quantity'] = 2
    oversized = StrategyDecision(decision_id='decision_'+support._hash(oversized), **oversized)
    with case.assertRaisesRegex(RuntimeError, 'EXECUTION_PLAN_QUANTITY_UNAVAILABLE'):
        exit(data.specs[0], oversized, plans[0])
    case.assertEqual(before, [head(plan) for plan in plans])
    case.assertEqual(2, len(owner.transport.calls))
    _, sell, receipt = exit(data.specs[0], original, plans[0])
    case.assertEqual((1, plans[0]), (sell.intent.quantity, sell.intent.plan))
    owner.clock[0] += timedelta(seconds=1)
    owner.runtime.reconcile(sell.intent.intent_id, BrokerOrderSnapshot(sell.broker_order_id,
        owner.runtime.account_ref, sell.intent.symbol, OrderState.FILLED, 1, 0, owner.clock[0],
        (BrokerFill('actual-alpha-sell', 1, 70_500, owner.clock[0]),)))
    case.assertEqual((0, []), (head(plans[0])['quantity'], head(plans[0])['capital_lots']))
    case.assertEqual(before[1], head(plans[1]), 'alpha exit must preserve the exact beta plan/head/cost')
    repeated = exit(data.specs[0], original, plans[0])
    case.assertEqual((sell.intent, receipt), (repeated[1].intent, repeated[2]))
    case.assertEqual(3, len(owner.transport.calls))
    return data, owner, plans, buys


def verify_group_queued_ack_loss(case, store):
    from kiwoom_monitor.domain.order_contract import OrderState
    data, owner, recovery, risk, planned = group_planned_inputs(case, store)
    spec, gate, intent = planned[0]
    create = store.create_execution_intent
    def lost_ack(*args, **kwargs):
        case.assertTrue(create(*args, **kwargs))
        raise OSError('shared account QUEUED COMMIT response lost')
    def submit(item):
        member, approved, request = item
        return owner.runtime.submit_automation_intent(request, recovery.account, spec_id=member.spec_id,
            control_revision=approved.control_revision, plan_recovery=recovery)
    with patch.object(store, 'create_execution_intent', side_effect=lost_ack):
        with case.assertRaisesRegex(OSError, 'COMMIT response lost'):
            submit(planned[0])
    queued = owner.execution.load(intent.intent_id)
    case.assertEqual(OrderState.QUEUED, queued.state)
    first_head = store.load_document('execution_mock_plan_positions', owner.runtime.account_ref,
                                    intent.plan.plan_id)['document']
    for _ in range(2):
        case.assertEqual(queued, submit(planned[0]))
    case.assertEqual([], owner.transport.calls)
    case.assertEqual(OrderState.ACCEPTED, submit(planned[1]).state)
    case.assertEqual(1, len(owner.transport.calls))
    case.assertEqual(first_head, store.load_document('execution_mock_plan_positions', owner.runtime.account_ref,
                                                  intent.plan.plan_id)['document'])
    case.assertFalse(owner.runtime._new_orders_enabled)


def verify_group_stop(case, store):
    from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation
    from kiwoom_monitor.domain.order_contract import BrokerFill, BrokerOrderSnapshot, OrderState
    data, owner, recovery, risk, planned = group_planned_inputs(case, store)
    spec, gate, intent = planned[0]
    first = owner.runtime.submit_automation_intent(intent, recovery.account, spec_id=spec.spec_id,
        control_revision=gate.control_revision, plan_recovery=recovery)
    owner.clock[0] += timedelta(seconds=1)
    # Any selected member can stop the shared account, preserving its anchor.
    emergency_stop_mock_automation(data.repository, owner.runtime, account_ref=owner.runtime.account_ref,
        spec_id=data.specs[1].spec_id, stopped_at=owner.clock[0], reason='explicit shared account stop',
        expected_control_revision=1)
    stopped = data.repository.load_mock_automation_control(owner.runtime.account_ref)
    case.assertEqual(('STOPPED', 2, data.selection.primary_spec_id, data.selection),
        (stopped.desired_state.value, stopped.control_revision, stopped.active_spec_id, stopped.account_selection))
    other, approved, request = planned[1]
    with case.assertRaisesRegex(RuntimeError, 'MOCK_AUTOMATION_CONTROL_CHANGED'):
        owner.runtime.submit_automation_intent(request, recovery.account, spec_id=other.spec_id,
            control_revision=approved.control_revision, plan_recovery=recovery)
    case.assertIsNone(owner.execution.load(request.intent_id))
    case.assertEqual(1, len(owner.transport.calls))
    # A STOP never erases an already accepted broker FILL.
    owner.runtime.reconcile(intent.intent_id, BrokerOrderSnapshot(first.broker_order_id,
        owner.runtime.account_ref, intent.symbol, OrderState.FILLED, 1, 0, owner.clock[0],
        (BrokerFill('accepted-before-group-stop',1,70_000,owner.clock[0]),)))
    head = store.load_document('execution_mock_plan_positions', owner.runtime.account_ref,intent.plan.plan_id)['document']
    case.assertEqual((1,[[1,70_000]]),(head['quantity'],head['capital_lots']))
    case.assertFalse(owner.runtime._new_orders_enabled)


class MockAccountExecutionTests(unittest.TestCase):
    def setUp(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = SQLiteQueryStore(root / 'account.sqlite3')
        self.store.initialize()
        self.addCleanup(self.store.close)

    def test_two_members_obtain_one_account_authority_and_retry_without_orders(self):
        verify_group_admission(self, self.store)

    def test_current_source_is_rechecked_before_any_member_admission_or_lease(self):
        from kiwoom_monitor.application.mock_automation_admission import admit_mock_automation_account_selection
        data = grouped_inputs(self, self.store)
        owner = group_runtime(self, self.store, data)
        with self.assertRaisesRegex(ValueError, 'selected implementation changed'):
            admit_mock_automation_account_selection(data.repository, owner.runtime,
                account_ref=owner.runtime.account_ref, selection_id=data.selection.selection_id,
                requested_at=owner.clock[0], expected_control_revision=0, expected_implementation_hash=lambda _: 'b'*64)
        self.assertFalse(owner.runtime._active)
        self.assertEqual((), data.repository.load_mock_automation_admissions(owner.runtime.account_ref))
        self.assertIsNone(data.repository.load_mock_automation_control(owner.runtime.account_ref))

    def test_control_commit_ack_loss_reuses_exact_members_without_another_revision_or_order(self):
        data = grouped_inputs(self, self.store)
        owner = group_runtime(self, self.store, data)
        save = self.store.save_mock_automation_control
        def lost_ack(*args, **kwargs):
            self.assertTrue(save(*args, **kwargs))
            raise OSError('account control COMMIT acknowledgement lost')
        with patch.object(self.store, 'save_mock_automation_control', side_effect=lost_ack):
            with self.assertRaisesRegex(OSError, 'acknowledgement lost'):
                admit_group(data, owner)
        first = data.repository.load_mock_automation_control(owner.runtime.account_ref)
        admissions, receipts, recovered = admit_group(data, owner, expected_revision=1)
        self.assertEqual(first, recovered)
        self.assertEqual((2, 2), (len(admissions), len(receipts)))
        self.assertEqual(2, len(data.repository.load_mock_automation_admissions(owner.runtime.account_ref)))
        self.assertEqual([], owner.transport.calls)
        self.assertFalse(owner.runtime._new_orders_enabled)

    def test_actual_native_members_share_reservations_without_a_second_owner_or_broker_retry(self):
        verify_group_native_reservation(self, self.store)

    def test_shared_capital_blocks_second_preapproved_buy_and_rolls_back_its_entire_plan(self):
        verify_group_native_reservation(self, self.store, capital=100_000)

    def test_actual_fill_and_plan_only_exit_preserve_another_member_and_reject_its_ownership(self):
        verify_group_native_fill_isolation(self, self.store)

    def test_frozen_shared_strategy_and_plan_limits_are_enforced_at_native_claim(self):
        from kiwoom_monitor.domain.execution_activation import MockAutomationAccountLimits
        for strategies, positions, reason in ((1,2,'STRATEGY_LIMIT'),(2,1,'POSITION_LIMIT')):
            with self.subTest(strategies=strategies, positions=positions):
                from kiwoom_monitor.central_server.database import SQLiteQueryStore
                root = Path(self.enterContext(tempfile.TemporaryDirectory()))
                store = SQLiteQueryStore(root/'limits.sqlite3')
                store.initialize()
                self.addCleanup(store.close)
                data, owner, recovery, risk, planned = group_planned_inputs(self, store,
                    limits=MockAutomationAccountLimits(strategies,positions,150_000,300_000,5,0,2,0))
                for number, (spec, gate, intent) in enumerate(planned):
                    if number:
                        with self.assertRaisesRegex(RuntimeError, 'MOCK_ACCOUNT_'+reason):
                            owner.runtime.submit_automation_intent(intent, recovery.account, spec_id=spec.spec_id,
                                control_revision=gate.control_revision, plan_recovery=recovery)
                        self.assertIsNone(owner.execution.load(intent.intent_id))
                    else:
                        owner.runtime.submit_automation_intent(intent, recovery.account, spec_id=spec.spec_id,
                            control_revision=gate.control_revision, plan_recovery=recovery)
                self.assertEqual(1,len(owner.transport.calls))

    def test_queued_commit_response_loss_keeps_one_claim_and_never_retries_the_broker(self):
        verify_group_queued_ack_loss(self,self.store)

    def test_any_member_stops_the_whole_account_and_existing_fills_remain_reconcilable(self):
        verify_group_stop(self,self.store)

    def test_standalone_admission_cannot_silently_select_one_member_of_an_account_group(self):
        from kiwoom_monitor.application.mock_automation_admission import (
            admit_mock_automation, mock_automation_control_from_dict, mock_automation_admission_from_dict,
            MOCK_AUTOMATION_ADMISSION_VERSION,
        )
        data,owner,admissions,receipts,control = verify_group_admission(self,self.store)
        with self.assertRaisesRegex(RuntimeError,'account admission path'):
            admit_mock_automation(data.repository,data.repository,owner.runtime,
                account_ref=owner.runtime.account_ref,spec_id=data.selection.primary_spec_id,
                requested_at=owner.clock[0])
        self.assertEqual(control,data.repository.load_mock_automation_control(owner.runtime.account_ref))
        self.assertEqual([],owner.transport.calls)
        for body in ({**control.to_dict(),'control_revision':True},{**control.to_dict(),'unused_permission':True}):
            with self.assertRaises(ValueError):
                mock_automation_control_from_dict(body)
        with self.assertRaisesRegex(ValueError,'standalone admission'):
            mock_automation_admission_from_dict({**admissions[0].to_dict(),'version':MOCK_AUTOMATION_ADMISSION_VERSION})

    def test_missing_member_lease_never_publishes_a_runnable_group_control(self):
        data=grouped_inputs(self,self.store)
        owner=group_runtime(self,self.store,data)
        save=data.repository.save_mock_automation_lease_receipt
        def missing(value):
            return save(value) if value.spec_id == data.selection.primary_spec_id else False
        with patch.object(data.repository,'save_mock_automation_lease_receipt',side_effect=missing):
            with self.assertRaisesRegex(RuntimeError,'MOCK_ACCOUNT_MEMBER_LEASE_MISSING'):
                admit_group(data,owner)
        self.assertIsNone(data.repository.load_mock_automation_control(owner.runtime.account_ref))
        self.assertEqual([],owner.transport.calls)
        self.assertFalse(owner.runtime._new_orders_enabled)

    def test_binding_rotation_after_approval_blocks_native_entry_but_preserves_account_stop(self):
        from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation
        data,owner,recovery,risk,planned=group_planned_inputs(self,self.store)
        changed=self.store.append_account_binding({**data.binding,'verified_at':owner.clock[0].isoformat()})
        self.assertEqual(data.selection.binding_revision+1,changed['binding_revision'])
        spec,gate,intent=planned[1]
        with self.assertRaisesRegex(RuntimeError,'MOCK_ACCOUNT_CONTROL_BINDING_CHANGED'):
            owner.runtime.submit_automation_intent(intent,recovery.account,spec_id=spec.spec_id,
                control_revision=gate.control_revision,plan_recovery=recovery)
        self.assertIsNone(owner.execution.load(intent.intent_id))
        self.assertEqual([],owner.transport.calls)
        emergency_stop_mock_automation(data.repository,owner.runtime,account_ref=owner.runtime.account_ref,
            spec_id=spec.spec_id,stopped_at=owner.clock[0],reason='stop after key rotation',expected_control_revision=1)
        self.assertEqual('STOPPED',data.repository.load_mock_automation_control(owner.runtime.account_ref).desired_state.value)
        self.assertFalse(owner.runtime._new_orders_enabled)
