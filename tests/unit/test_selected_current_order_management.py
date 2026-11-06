"""Selected current-order scope uses source facts and the current target epoch."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from kiwoom_monitor.application.minute_order_management import (
    assess_current_minute_order, verified_current_order_peers,
)
from kiwoom_monitor.application.mock_automation_risk import (
    build_mock_automation_risk_snapshot, mock_automation_risk_snapshot_from_dict,
)
from kiwoom_monitor.domain.order_contract import AccountSnapshot, BrokerOrderSnapshot, OrderState, TERMINAL_ORDER_STATES
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository, execution_broker_legs
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository


def pure_scope(record, cut, control, context, policy):
    return verified_current_order_peers(record, cut['recovery'], cut['risk'], cut['observation'].open_orders, control,
        selected_management_context=context, selected_management_policy=policy)


def common_assessment(spec, runtime, execution, record, cut, peers):
    from kiwoom_monitor.domain.execution_activation import CLOSE_ORDER_MANAGEMENT_VERSION
    return assess_current_minute_order(spec, run_id=runtime.run_id, record=record,
        head=execution.load_plan_position(runtime.account_ref, runtime.run_id, record.intent.symbol, record.intent.plan),
        order_events=execution.events(record.intent.intent_id), recovery=cut['recovery'], risk=cut['risk'],
        quote=None, decided_at=runtime.server_now(), now=runtime.server_now(), entry_enabled=False,
        terms=spec.close_execution_policy, management_version=CLOSE_ORDER_MANAGEMENT_VERSION,
        entry_proof_id=record.intent.plan.policy_ref, signal_reference_price=record.intent.limit_price,
        entry_deadline=runtime.server_now() + timedelta(seconds=1), capital_limit=spec.maximum_capital_won,
        verified_peer_orders=peers)


class SelectedCurrentOrderScopeTests(unittest.IsolatedAsyncioTestCase):
    from tests.unit.test_selected_order_recovery import SelectedOrderRecoveryTests
    source, target = SelectedOrderRecoveryTests.source, SelectedOrderRecoveryTests.target

    async def test_actual_original_add_cancels_once_then_protects_two_source_fills_after_quote_recovery(self):
        import asyncio
        from tests.unit.test_selected_order_management import actual_management_target
        from tests.unit.test_selected_plan_bootstrap import actual_staged_plan_source
        from kiwoom_monitor.application.market_session_schedule import KST
        async def pending_source(case):
            return await actual_staged_plan_source(case, pending_add=True)
        data = await actual_management_target(self, source_factory=pending_source)
        original_bytes = data.source.selection['source'].read_bytes()
        original_notice = data.source.data.episodes[0]._protection_snapshot()
        self.assertEqual(('PENDING', 2), (original_notice['state'],
            original_notice['planned_leader_stop']['stop_loss']['quantity']))
        original_add = next(row for row in data.source.native if row['intent_id'] != data.source.packet['order']['intent_id'])
        self.assertEqual(('BUY', 'ACCEPTED', 1, 0), (original_add['side'], original_add['state'],
            original_add['quantity'], original_add['detailed_filled_quantity']))
        url = data.target.url.replace('/assessment', '/prepare-management')
        prepared = await data.target.client.post(url, json=data.request)
        self.assertEqual(200, prepared.status_code, prepared.text)
        bundle = data.target.owner.bundle('target-profile')
        data.book_context.runtime, data.book_context.execution = bundle.runtime, bundle.repository
        data.book_context.management_field = 'leader_order_management_claim'
        accepted = await data.target.client.post(url.replace('/prepare-management', '/accept-management'),
            json={**data.request, 'expected_control_revision': 1})
        self.assertEqual(200, accepted.status_code, accepted.text)
        runner = data.supervisor._runners[bundle.account_ref]
        self.assertEqual(1, len(runner.episodes))
        symbol = original_add['symbol']
        async def recovered_quote():
            data.clock[0] += timedelta(milliseconds=10)
            data.supervisor.observe_leader_trade(dict(code=symbol, market='KRX',
                current_price=original_add['limit_price'], trade_time=data.clock[0].astimezone(KST).strftime('%H%M%S'),
                cumulative_trade_value=100), data.clock[0], source='selected-current-leader:1', approved=True)
            await asyncio.sleep(.05)
        async def protect_source_position():
            cancellation_polled = False
            for _ in range(200):
                await recovered_quote()
                if data.book.mutations and not cancellation_polled and not bundle.runtime.observation_in_progress:
                    cancellation_polled = True
                    await bundle.monitor.refresh_recovery()
                sells = tuple(row for row in bundle.repository.active_intents('mock', bundle.account_ref, bundle.run_id)
                    if row.intent.side.value == 'SELL' and row.state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED})
                if sells:
                    return sells
            self.fail(str((data.book.mutations, runner.status)))
        try:
            sells = await asyncio.wait_for(protect_source_position(), timeout=30)
        except TimeoutError:
            self.fail(str((data.book.mutations, runner.status)))
        self.assertEqual([2], [row.intent.quantity for row in sells])
        sell = sells[0]
        self.assertEqual(('mock-protection-exit/v1', original_add['plan']['plan_id']),
            (sell.intent.policy_version, sell.intent.plan.plan_id))
        self.assertEqual(original_notice['risk_trigger'], runner.episode._protection_snapshot()['risk_trigger'])
        self.assertEqual([('CANCEL', original_add['broker_order_id'], 0)],
            [row for row in data.book.mutations if row[0] == 'CANCEL'])
        self.assertEqual('CANCELLED', bundle.repository.load(original_add['intent_id']).state.value)
        working = tuple(leg for leg in execution_broker_legs(bundle.repository.load(sell.intent.intent_id))
                        if leg.remaining_quantity)
        self.assertEqual(1, len(working))
        data.clock[0] += timedelta(milliseconds=1)
        data.book.fill(working[0].broker_order_id, 2, 'current-target-original-add-protection-two')
        await bundle.monitor.refresh_recovery()
        async def native_flat():
            for _ in range(200):
                head = bundle.repository.load_plan_position(bundle.account_ref, bundle.run_id, symbol, sell.intent.plan)
                if head.quantity == 0 and runner.episode._episode_position.state.position_quantity is None:
                    return head
                await asyncio.sleep(.05)
            self.fail(str((head, runner.status)))
        await asyncio.wait_for(native_flat(), timeout=30)
        self.assertEqual(['SELL'], [row[1] for row in data.transport.calls])
        self.assertEqual(2, bundle.repository.load(sell.intent.intent_id).detailed_filled_quantity)
        self.assertFalse(bundle.runtime._new_orders_enabled)
        control = data.target.store.load_mock_automation_control(bundle.account_ref)
        self.assertTrue(control['entry_resume_required'])
        self.assertEqual(original_bytes, data.source.selection['source'].read_bytes())

    async def test_original_native_add_buy_uses_its_own_source_gate_and_is_cancel_only(self):
        from tests.unit.test_selected_order_management import actual_management_target
        from tests.unit.test_selected_plan_bootstrap import actual_staged_plan_source
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        from kiwoom_monitor.central_server.database_execution import _verified_native_order_management_peers
        from kiwoom_monitor.central_server.selected_order_bootstrap import require_selected_management_owner
        from kiwoom_monitor.domain.execution_activation import LEADER_ORDER_MANAGEMENT_VERSION
        async def pending_source(case):
            return await actual_staged_plan_source(case, pending_add=True)
        data = await actual_management_target(self, source_factory=pending_source)
        original_bytes = data.source.selection['source'].read_bytes()
        url = data.target.url.replace('/assessment', '/prepare-management')
        prepared = await data.target.client.post(url, json=data.request)
        self.assertEqual(200, prepared.status_code, prepared.text)
        bundle = data.target.owner.bundle('target-profile')
        data.book_context.runtime, data.book_context.execution = bundle.runtime, bundle.repository
        with patch.object(MockAutomationRunner, 'start', new_callable=AsyncMock):
            accepted = await data.target.client.post(url.replace('/prepare-management', '/accept-management'),
                json={**data.request, 'expected_control_revision': 1})
        self.assertEqual(200, accepted.status_code, accepted.text)
        execution = bundle.repository
        context, policy = execution.selected_management_context, execution.selected_management_policy
        forward = ForwardEvaluationRepository(data.target.store, selected_management_context=context)
        spec = forward.load_mock_automation_spec(context.account_ref, context.spec_id)
        cut = bundle.monitor.latest_automation_execution_cut
        record, = cut['observation'].open_orders
        self.assertNotEqual(context.intent_id, record.intent.intent_id)
        self.assertEqual(('BUY', 1, 0), (record.intent.side.value, record.intent.quantity, record.detailed_filled_quantity))
        self.assertEqual(2, len(policy['source_intent_ids']))
        source_order = next(row for row in policy['source_orders'] if row['intent_id'] == record.intent.intent_id)
        from kiwoom_monitor.infrastructure.persistence.execution_repository import _record_from_document
        self.assertEqual(_record_from_document(source_order).intent, record.intent)
        self.assertEqual(source_order['mock_plan_approval'],
            data.target.store.load_execution_intent(record.intent.intent_id)['mock_plan_approval'])
        control = execution.current_automation_control(context.account_ref)
        peers = pure_scope(record, cut, control, context, policy)
        with data.target.store.mock_automation_read_scope() as reader:
            current = require_selected_management_owner(reader, '?', context,
                bundle.runtime.run_id + ':' + bundle.runtime.owner_token, data.clock[0],
                current_owner=execution._selected_management_owner)
            self.assertEqual(peers, _verified_native_order_management_peers(reader, '?', record,
                cut['recovery'], cut['risk'], {**control, '_native_selected_management_context': current}, 'LEADER'))
        self.assertEqual(peers, bundle.gateway._verified_order_management_cut(record, cut, spec.spec_id,
            control['control_revision'])['verified_peer_orders'])
        assessment = assess_current_minute_order(spec, run_id=bundle.run_id, record=record,
            head=execution.load_plan_position(bundle.account_ref, bundle.run_id, record.intent.symbol, record.intent.plan),
            order_events=execution.events(record.intent.intent_id), recovery=cut['recovery'], risk=cut['risk'], quote=None,
            decided_at=cut['risk'].observed_at, now=bundle.runtime.server_now(), entry_enabled=True,
            terms=spec.unfilled_execution_policy, management_version=LEADER_ORDER_MANAGEMENT_VERSION,
            entry_proof_id=record.intent.plan.policy_ref, signal_reference_price=record.intent.limit_price,
            entry_deadline=record.intent.expires_at, capital_limit=spec.maximum_capital_won, verified_peer_orders=peers)
        self.assertEqual(('CANCEL', 'current_entry_context_disabled_or_changed', False),
            (assessment['action'], assessment['reason'], assessment['entry_enabled']))
        bad_policy = deepcopy(policy)
        bad_policy.pop('source_intent_ids')
        with self.assertRaisesRegex(ValueError, 'SELECTED_SOURCE_CHANGED'):
            pure_scope(record, cut, control, context, bad_policy)
        bad_gate = deepcopy(policy)
        bad_gate['documents'] = [row for row in bad_gate['documents'] if not (
            row['collection'] == 'execution_mock_automation_approved_gates' and row['key'] == record.intent.intent_id)]
        with self.assertRaisesRegex(ValueError, 'SELECTED_SOURCE_CHANGED'):
            pure_scope(record, cut, control, context, bad_gate)
        wrong_marker = deepcopy(policy)
        next(row for row in wrong_marker['source_orders'] if row['intent_id'] == record.intent.intent_id)[
            'mock_plan_approval'] = 'another-original-approval'
        with self.assertRaisesRegex(ValueError, 'SELECTED_SCOPE_CHANGED'):
            pure_scope(record, cut, control, context, wrong_marker)
        for changed in (replace(record, intent=replace(record.intent, quantity=record.intent.quantity + 1)),
                replace(record, intent=replace(record.intent, intent_id='unassigned-same-plan-buy'))):
            with self.assertRaisesRegex(ValueError, 'SELECTED_SCOPE_CHANGED'):
                verified_current_order_peers(changed, cut['recovery'], cut['risk'], (changed,), control,
                    selected_management_context=context, selected_management_policy=policy)
        self.assertEqual([], data.book.mutations)
        self.assertEqual(original_bytes, data.source.selection['source'].read_bytes())

    async def test_native_member_preflight_reproduces_selected_scope_without_restoring_the_original_group(self):
        from tests.unit.test_selected_order_management import actual_management_target
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        from kiwoom_monitor.central_server.database_execution import _verified_native_order_management_peers
        from kiwoom_monitor.central_server.selected_order_bootstrap import require_selected_management_owner
        data = await actual_management_target(self)
        source_bytes = data.source.selection['source'].read_bytes()
        url = data.target.url.replace('/assessment', '/prepare-management')
        prepared = await data.target.client.post(url, json=data.request)
        self.assertEqual(200, prepared.status_code, prepared.text)
        bundle = data.target.owner.bundle('target-profile')
        data.book_context.runtime, data.book_context.execution = bundle.runtime, bundle.repository
        # Isolate the scope readers: native acceptance/control/lease are real;
        # the separate actor test owns actual scheduled cancellation and SELL.
        with patch.object(MockAutomationRunner, 'start', new_callable=AsyncMock):
            accepted = await data.target.client.post(url.replace('/prepare-management', '/accept-management'),
                json={**data.request, 'expected_control_revision': 1})
        self.assertEqual(200, accepted.status_code, accepted.text)
        execution = bundle.repository
        context, policy = execution.selected_management_context, execution.selected_management_policy
        forward = ForwardEvaluationRepository(data.target.store, selected_management_context=context)
        spec = forward.load_mock_automation_spec(context.account_ref, context.spec_id)
        cut = bundle.monitor.latest_automation_execution_cut
        record, = cut['observation'].open_orders
        control = execution.current_automation_control(context.account_ref)
        self.assertIsNone(control.get('account_selection'))
        self.assertEqual(context.spec_id, record.member_spec_id)
        self.assertNotEqual(spec.binding_revision, cut['risk'].binding_revision)
        peers = pure_scope(record, cut, control, context, policy)
        self.assertEqual((None, [], False), (peers['account_selection_id'], peers['other_member_open_order_ids'],
                                           peers['legacy_evidence_scope']))
        source_risk = next(mock_automation_risk_snapshot_from_dict(item['document']) for item in policy['documents']
            if item['collection'] == 'execution_mock_automation_risk_snapshots')
        with data.target.store.mock_automation_read_scope() as reader:
            verified = require_selected_management_owner(reader, '?', context,
                bundle.runtime.run_id + ':' + bundle.runtime.owner_token, data.clock[0],
                current_owner=execution._selected_management_owner)
            native = _verified_native_order_management_peers(reader, '?', record, cut['recovery'], cut['risk'],
                {**control, '_native_selected_management_context': verified}, 'CLOSE')
            with self.assertRaisesRegex(RuntimeError, 'CURRENT_ORDER_ACCOUNT_COVERAGE_CHANGED'):
                _verified_native_order_management_peers(reader, '?', record, cut['recovery'], source_risk,
                    {**control, '_native_selected_management_context': verified}, 'CLOSE')
        self.assertEqual(peers, native)
        current = bundle.gateway._verified_order_management_cut(record, cut, spec.spec_id, control['control_revision'])
        self.assertEqual(peers, current['verified_peer_orders'])
        assessment = common_assessment(spec, bundle.runtime, execution, record, cut, peers)
        self.assertEqual(('CANCEL', 'current_entry_context_disabled_or_changed'),
                         (assessment['action'], assessment['reason']))
        with self.assertRaisesRegex(ValueError, 'PEER_COVERAGE_CHANGED'):
            verified_current_order_peers(record, cut['recovery'], cut['risk'], (record,), control)
        for changed in (replace(context, binding_revision=context.binding_revision + 1),
                        replace(context, execution_run_id='different-source-run')):
            with self.assertRaises(ValueError):
                pure_scope(record, cut, control, changed, policy)
        with self.assertRaises(ValueError):
            pure_scope(record, cut, {**control, 'desired_state': 'STOPPED'}, context, policy)
        bad_policy = deepcopy(policy)
        bad_policy['source_plan_position']['plan_id'] = 'another-source-plan'
        with self.assertRaisesRegex(ValueError, 'SELECTED_SCOPE_CHANGED'):
            pure_scope(record, cut, control, context, bad_policy)
        extra = replace(record, intent=replace(record.intent, intent_id='unassigned-native-or-manual'))
        with self.assertRaisesRegex(ValueError, 'SELECTED_SCOPE_CHANGED'):
            verified_current_order_peers(record, cut['recovery'], cut['risk'], (record, extra), control,
                selected_management_context=context, selected_management_policy=policy)
        with self.assertRaisesRegex(ValueError, 'SELECTED_RISK_CHANGED'):
            pure_scope(record, {**cut, 'risk': source_risk}, control, context, policy)
        incomplete = build_mock_automation_risk_snapshot(cut['recovery'], (), (), (record,),
            execution_run_id=context.execution_run_id, binding_revision=context.binding_revision,
            reconciliation_revision=cut['risk'].reconciliation_revision + 1, observed_at=data.clock[0],
            broker_query_started_at=data.clock[0], broker_query_completed_at=data.clock[0])
        self.assertFalse(incomplete.reconciliation_complete)
        with self.assertRaises(ValueError):
            common_assessment(spec, bundle.runtime, execution, record, {**cut, 'risk': incomplete}, peers)
        self.assertEqual([], data.book.mutations)
        self.assertEqual(source_bytes, data.source.selection['source'].read_bytes())

    async def test_legacy_none_member_keeps_original_admission_scope_and_uses_its_proved_plan(self):
        from tests.unit.test_selected_order_management import prepared_target, prepare_current_management
        from kiwoom_monitor.domain.selected_order_evidence import selected_order_management_context_from_dict
        from kiwoom_monitor.application.mock_automation_admission import mock_automation_control_from_dict
        data = await prepared_target(self)
        prepared, _ = await prepare_current_management(data)
        context = selected_order_management_context_from_dict(prepared['receipt']['context'])
        self.assertIsNone(context.member_spec_id)
        self.assertIsNone(context.source_account_selection_id)
        execution = ExecutionRepository(data.target.store)
        forward = ForwardEvaluationRepository(data.target.store, selected_management_context=context)
        spec = forward.load_mock_automation_spec(context.account_ref, context.spec_id)
        policy = forward.selected_management_policy
        record = execution.load(context.intent_id)
        self.assertIsNone(getattr(record, 'member_spec_id', None))
        now = data.bundle.clock[0]
        legs = tuple(leg for leg in execution_broker_legs(record)
                     if leg.state not in TERMINAL_ORDER_STATES and leg.remaining_quantity)
        orders = tuple(BrokerOrderSnapshot(leg.broker_order_id, context.account_ref, record.intent.symbol, leg.state,
            leg.filled_quantity, leg.remaining_quantity, now) for leg in legs)
        recovery = MockAccountRecovery(AccountSnapshot(context.account_ref, 250_000,
            sum(leg.remaining_quantity * leg.limit_price for leg in legs),
            {record.intent.symbol: policy['source_plan_position']['quantity']}, now), orders)
        events = execution.account_events('mock', context.account_ref).events
        risk = build_mock_automation_risk_snapshot(recovery, events, (), (record,), execution_run_id=context.execution_run_id,
            binding_revision=context.binding_revision, reconciliation_revision=1, observed_at=now,
            broker_query_started_at=now, broker_query_completed_at=now)
        self.assertTrue(risk.reconciliation_complete)
        self.assertIsNone(risk.member_attribution)
        # Explicit pure current-control input; no native management authority is
        # inferred from this model or written to the target store.
        control = mock_automation_control_from_dict({**execution.current_automation_control(context.account_ref),
            'desired_state': 'RUNNING'})
        cut = dict(recovery=recovery, risk=risk, observation=SimpleNamespace(open_orders=(record,)))
        peers = pure_scope(record, cut, control, context, policy)
        self.assertTrue(peers['legacy_evidence_scope'])
        runtime = SimpleNamespace(account_ref=context.account_ref, run_id=context.execution_run_id, server_now=lambda: now)
        assessment = common_assessment(spec, runtime, execution, record, cut, peers)
        self.assertEqual('CANCEL', assessment['action'])
        self.assertIsNone(getattr(execution.load(context.intent_id), 'member_spec_id', None))
        self.assertIsNone(verified_current_order_peers(record, recovery, risk, (record,), None))
