"""Original standalone approvals give v2 an internal view, never rewritten history."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
import unittest


async def standalone_retained_inputs(case, factory=None):
    from tests.unit.test_leader_order_management import current_leader
    from kiwoom_monitor.domain.execution_activation import (
        MockAutomationAccountLimits, MockAutomationAccountSelectionV2, MockAutomationRetainedAdmissionRef,
    )
    buy = await current_leader(case, factory)
    case.clock += timedelta(milliseconds=100)
    case.book.fill(buy.broker_order_id, 2, 'standalone-first-detail')
    await case.bundle.monitor.refresh_recovery()
    control = case.repository.load_mock_automation_control(case.runtime.account_ref)
    admission = case.repository.load_mock_automation_admission_for_spec(case.runtime.account_ref, case.spec.spec_id)
    case.assertIsNone(control.account_selection)
    case.assertIsNone(admission.account_selection_id)
    new_spec = replace(case.spec, maximum_capital_won=case.spec.maximum_capital_won - 1)
    case.repository.save_mock_automation_spec(new_spec)
    limits = MockAutomationAccountLimits(maximum_concurrent_strategies=2, maximum_concurrent_positions=2,
        maximum_capital_won=case.spec.maximum_capital_won, maximum_daily_loss_won=case.spec.maximum_daily_loss_won,
        maximum_data_gap_seconds=case.spec.maximum_data_gap_seconds,
        maximum_submission_unknown_count=case.spec.maximum_submission_unknown_count,
        maximum_reconnect_count=case.spec.maximum_reconnect_count,
        maximum_balance_mismatch_count=case.spec.maximum_balance_mismatch_count)
    selection = MockAutomationAccountSelectionV2(account_scope=case.spec.account_scope,
        credential_profile_id=case.spec.credential_profile_id, binding_revision=case.spec.binding_revision,
        binding_verified_at=case.spec.binding_verified_at, frozen_at=case.clock,
        member_spec_ids=(case.spec.spec_id, new_spec.spec_id), limits=limits,
        execution_order_spec_ids=(case.spec.spec_id, new_spec.spec_id), entry_spec_ids=(new_spec.spec_id,),
        retained_admission_refs=(MockAutomationRetainedAdmissionRef(case.spec.spec_id, admission.admission_id,
            None, case.runtime.run_id),))
    package = case.repository.load_mock_automation_candidate_package(case.spec.strategy_ref, case.spec.candidate_package_hash)
    case.repository.publish_mock_automation_account_selection(selection,
        expected_implementation_hash=lambda _: package.scientific_implementation_hash)
    return SimpleNamespace(buy=buy, selection=selection, previous=control, old_admission=admission, new_spec=new_spec)


class StandaloneRetainedProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_run_manual_queue_is_not_projected_to_the_retained_owner(self):
        from dataclasses import fields
        from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
        from kiwoom_monitor.domain.order_contract import OrderIntent
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
        data = await standalone_retained_inputs(self)
        raw = {field.name: getattr(data.buy.intent, field.name) for field in fields(OrderIntent)}
        manual = OrderIntent(**{**raw, 'intent_id': 'manual-unassigned-same-run', 'decision_id': 'manual-source',
            'quantity': 1, 'policy_version': 'explicit-isolated-manual-fixture'})
        OrderLifecycle(ExecutionRepository(self.store), None, now_provider=lambda: self.clock).queue(manual)
        _, projection = self.execution.retained_member_projection(self.runtime.account_ref,
            selection_id=data.selection.selection_id, expected_control_revision=data.previous.control_revision,
            intent_ids=(data.buy.intent.intent_id, manual.intent_id))
        self.assertEqual({data.buy.intent.intent_id: self.spec.spec_id}, projection)
        self.assertFalse(hasattr(self.execution.load(manual.intent_id, member_projection=projection), 'member_spec_id'))
        await self.bundle.monitor.prepare_account_selection_recovery(data.selection.selection_id,
                                                                     data.previous.control_revision)
        risk = self.bundle.monitor.latest_automation_execution_cut['risk']
        self.assertEqual('INCOMPLETE', risk.member_attribution)
        self.assertEqual((), risk.member_pending_sells)
        self.assertNotIn('mock_account_member_spec_id', self.store.load_execution_intent(manual.intent_id))

    async def test_actual_standalone_handoff_preserves_old_partial_owner_and_new_entry_scope(self):
        from unittest.mock import AsyncMock
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
        from kiwoom_monitor.central_server.mock_runtime import MockAccountRuntimeMode
        data = await standalone_retained_inputs(self, getattr(self, '_retained_factory', None))
        original = self.runner
        old_episode = original.episode
        # The actual app producer accepts this optional spec target; keep the identical policy.
        old_episode._leader_context_source = lambda spec_id=None: self.live
        self.bundle.runtime_mode = MockAccountRuntimeMode.AUTOMATIC
        self.bundle.refresh_automation_control = lambda control: setattr(self.bundle, 'automation_context',
            SimpleNamespace(spec_id=control.active_spec_id, execution_run_id=control.execution_run_id,
                            control_revision=control.control_revision))
        package = self.repository.load_mock_automation_candidate_package(self.spec.strategy_ref, self.spec.candidate_package_hash)
        owner = SimpleNamespace(bundle=lambda profile: self.bundle, require_execution_settings=AsyncMock())
        supervisor = MockAutomationSupervisor(self.store, owner, poll_seconds=3600, now_provider=lambda: self.clock,
            price_source=old_episode._price_source,
            leader_context_source=lambda spec_id=None: self.live,
            expected_implementation_hash=lambda _: package.scientific_implementation_hash)
        self.addAsyncCleanup(supervisor.close)
        supervisor._runners[self.runtime.account_ref] = original
        result = await supervisor.replace_selection(credential_profile_id=self.spec.credential_profile_id,
            account_ref=self.runtime.account_ref, spec_id=self.spec.spec_id,
            account_selection_id=data.selection.selection_id, expected_control_revision=data.previous.control_revision,
            expected_settings_revision=1, credential_revision=1, reason='explicit synthetic standalone retained handoff')
        active = supervisor._runners[self.runtime.account_ref]
        await active.pause_for_replacement()
        self.assertIs(old_episode, active.episode_for_spec(self.spec.spec_id))
        self.assertEqual(data.selection.selection_id, result['runner']['account_selection_id'])
        self.assertEqual([data.new_spec.spec_id], result['runner']['entry_spec_ids'])
        self.assertEqual(data.old_admission, self.repository.load_mock_automation_admission_for_spec(
            self.runtime.account_ref, self.spec.spec_id))
        self.clock += timedelta(milliseconds=100)
        self.book.fill(data.buy.broker_order_id, 1, 'standalone-after-handoff-detail')
        await self.bundle.monitor.refresh_recovery()
        for episode in active.episodes:
            await episode.advance_once()
        self.assertEqual(3, old_episode._episode_position.state.position_quantity)
        self.assertEqual(1, len(self.transport.calls))
        cut = self.bundle.monitor.latest_automation_execution_cut
        if cut is None:
            await self.bundle.monitor.refresh_recovery()
            cut = self.bundle.monitor.latest_automation_execution_cut
        self.assertEqual((('005930', 3),), cut['risk'].owned_for_member(self.spec.spec_id))
        self.assertEqual((), cut['risk'].owned_for_member(data.new_spec.spec_id))
        from kiwoom_monitor.domain.time_basis import KST
        from kiwoom_monitor.domain.order_contract import OrderSide
        async def deliver_all(price):
            self.clock += timedelta(milliseconds=100)
            self.runtime.heartbeat()
            payload = {'code': '005930', 'market': 'KRX', 'current_price': price,
                'trade_time': self.clock.astimezone(KST).strftime('%H%M%S'), 'cumulative_trade_value': 100}
            for episode in active.episodes:
                episode.observe_leader_trade(payload, self.clock, source='actual-0B-fixture:1', approved=True)
            await self.bundle.monitor.refresh_recovery()
            for episode in active.episodes:
                await episode.advance_once()
        await deliver_all(990)
        await deliver_all(1001)
        new_entry_status = active.episode_for_spec(data.new_spec.spec_id).status
        new_buys = [record for record in self.execution.active_intents('mock', self.runtime.account_ref, self.runtime.run_id)
                    if getattr(record, 'member_spec_id', None) == data.new_spec.spec_id and record.intent.side is OrderSide.BUY]
        # The same explicit hard-stop trace as the existing native leader management test.
        await deliver_all(940)
        await deliver_all(945)
        await self.bundle.monitor.refresh_recovery()
        for episode in active.episodes:
            await episode.advance_once()
        sells = [record for record in self.execution.active_intents('mock', self.runtime.account_ref, self.runtime.run_id)
                 if record.intent.side is OrderSide.SELL and getattr(record, 'member_spec_id', None) == self.spec.spec_id]
        self.assertEqual((1, 1), (len(new_buys), len(sells)), {'new_entry': new_entry_status,
            'old_protection': old_episode.status, 'calls': self.transport.calls, 'mutations': self.book.mutations})
        self.assertEqual((3, data.buy.intent.plan.plan_id), (sells[0].intent.quantity, sells[0].intent.plan.plan_id))
        self.book.fill(sells[0].broker_order_id, 3, 'old-owner-only-exit')
        await self.bundle.monitor.refresh_recovery()
        for episode in active.episodes:
            await episode.advance_once()
        self.assertIsNone(old_episode._episode_position.state.position_quantity)
        self.assertEqual('flat', old_episode._episode_position.state.status)
        self.assertEqual((), self.bundle.monitor.latest_automation_execution_cut['risk'].owned_for_member(data.new_spec.spec_id))

    async def test_real_old_plan_partial_and_late_fill_use_proved_member_view_without_rewriting_sources(self):
        from kiwoom_monitor.infrastructure.persistence.execution_repository import MemberExecutionRecord
        data = await standalone_retained_inputs(self, getattr(self, '_retained_factory', None))
        raw = self.store.load_execution_intent(data.buy.intent.intent_id)
        before = self.execution.account_events('mock', self.runtime.account_ref).to_dict()
        old_gate = self.store.load_document('execution_mock_automation_approved_gates', self.runtime.account_ref,
                                            data.buy.intent.intent_id)
        await self.bundle.monitor.prepare_account_selection_recovery(data.selection.selection_id,
                                                                     data.previous.control_revision)
        cut = self.bundle.monitor.latest_automation_execution_cut
        self.assertIsNotNone(cut)
        self.assertEqual('COMPLETE', cut['risk'].member_attribution)
        self.assertEqual((('005930', 2),), cut['risk'].owned_for_member(self.spec.spec_id))
        self.assertEqual((), cut['risk'].owned_for_member(data.new_spec.spec_id))
        projection = self.bundle.monitor.member_projection_for_selection(data.selection.selection_id)
        self.assertEqual({data.buy.intent.intent_id: self.spec.spec_id}, projection)
        viewed = self.execution.load(data.buy.intent.intent_id, member_projection=projection)
        self.assertIsInstance(viewed, MemberExecutionRecord)
        self.assertEqual(data.buy.intent, viewed.intent)
        self.assertFalse(hasattr(self.execution.load(data.buy.intent.intent_id), 'member_spec_id'))
        current_raw = self.store.load_execution_intent(data.buy.intent.intent_id)
        # The real fresh reconciliation advances only its observation/receipt clocks.
        self.assertEqual({key: value for key, value in raw.items() if key not in {'last_broker_as_of', 'updated_at'}},
                         {key: value for key, value in current_raw.items() if key not in {'last_broker_as_of', 'updated_at'}})
        current_events = {event['source_event_id']: event for event in
                          self.execution.account_events('mock', self.runtime.account_ref).to_dict()['events']}
        self.assertEqual(before['events'], [current_events[event['source_event_id']] for event in before['events']])
        self.assertEqual(old_gate, self.store.load_document('execution_mock_automation_approved_gates',
            self.runtime.account_ref, data.buy.intent.intent_id))
        self.assertEqual(data.old_admission, self.repository.load_mock_automation_admission_for_spec(
            self.runtime.account_ref, self.spec.spec_id))
        # A fresh detailed execution invalidates the cut, but cannot erase immutable ownership proof.
        self.clock += timedelta(milliseconds=100)
        self.book.fill(data.buy.broker_order_id, 1, 'standalone-late-detail')
        recovery = await self.book.read()
        snapshot = next(order for order in recovery.orders if order.broker_order_id == data.buy.broker_order_id)
        self.runtime.reconcile(data.buy.intent.intent_id, snapshot)
        self.assertIsNone(self.bundle.monitor.latest_automation_execution_cut)
        self.assertEqual(projection, self.bundle.monitor.member_projection_for_selection(data.selection.selection_id))
        scoped = self.execution.account_events('mock', self.runtime.account_ref, member_projection=projection)
        late = [event for event in scoped.events if event.broker_execution_id == 'standalone-late-detail']
        self.assertEqual([(self.spec.spec_id, 1)], [(event.member_spec_id, event.quantity) for event in late])
        await self.bundle.monitor.prepare_account_selection_recovery(data.selection.selection_id,
                                                                     data.previous.control_revision)
        refreshed = self.bundle.monitor.latest_automation_execution_cut
        self.assertEqual((('005930', 3),), refreshed['risk'].owned_for_member(self.spec.spec_id))
        self.assertEqual((), refreshed['risk'].owned_for_member(data.new_spec.spec_id))
        with self.assertRaisesRegex(RuntimeError, 'PROJECTION_UNAVAILABLE'):
            self.bundle.monitor.member_projection_for_selection('unseen-selection')
        self.assertEqual(1, len(self.transport.calls))

    async def test_missing_original_gate_and_stale_control_never_infer_a_member_from_the_run(self):
        data = await standalone_retained_inputs(self)
        await self.runner.episode.advance_once()
        position = self.runner.episode._episode_position.checkpoint_fields()
        self.assertEqual(2, self.runner.episode._episode_position.state.position_quantity)
        from kiwoom_monitor.domain.time_basis import KST
        self.clock += timedelta(milliseconds=100)
        self.runner.episode.observe_leader_trade({'code': '005930', 'market': 'KRX', 'current_price': 940,
            'trade_time': self.clock.astimezone(KST).strftime('%H%M%S'), 'cumulative_trade_value': 100},
            self.clock, source='actual-0B-fixture:1', approved=True)
        cue = self.runner.episode._protection_snapshot()
        self.assertIsNotNone(cue)
        self.assertEqual('PENDING', cue['state'])
        transmissions = tuple(self.transport.calls)
        with self.assertRaisesRegex(RuntimeError, 'CONTROL_CHANGED'):
            await self.bundle.monitor.prepare_account_selection_recovery(data.selection.selection_id,
                data.previous.control_revision + 1)
        with self.store._connection() as connection:
            connection.execute('DELETE FROM central_documents WHERE collection=? AND owner=? AND document_key=?',
                ('execution_mock_automation_approved_gates', self.runtime.account_ref, data.buy.intent.intent_id))
            connection.commit()
        await self.bundle.monitor.prepare_account_selection_recovery(data.selection.selection_id,
                                                                     data.previous.control_revision)
        risk = self.bundle.monitor.latest_automation_execution_cut['risk']
        self.assertEqual('INCOMPLETE', risk.member_attribution)
        self.assertEqual({}, self.bundle.monitor.member_projection_for_selection(data.selection.selection_id))
        with self.assertRaisesRegex(ValueError, 'attribution is unavailable or incomplete'):
            risk.owned_for_member(self.spec.spec_id)
        self.assertFalse(hasattr(self.execution.load(data.buy.intent.intent_id), 'member_spec_id'))
        self.assertEqual(position, self.runner.episode._episode_position.checkpoint_fields())
        self.assertEqual(cue, self.runner.episode._protection_snapshot())
        self.assertEqual(transmissions, tuple(self.transport.calls))
