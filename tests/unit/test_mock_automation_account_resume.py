"""Explicit account resume uses every member of one actual native Risk cut."""
from dataclasses import asdict, fields, replace
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def stopped_account(case, store, *, peer_order=False):
    from tests.unit.test_mock_automation_account_recovery import (
        account_recovery_inputs, verify_account_recovery_peer_order)
    from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation
    if peer_order:
        data, previous, buy = verify_account_recovery_peer_order(case, store)
        orders, reserved = previous['recovery'].orders, buy.intent.limit_price
    else:
        data = account_recovery_inputs(case, store)
        orders, reserved = (), 0
    owner = data.owner
    emergency_stop_mock_automation(data.repository, owner.runtime, account_ref=owner.runtime.account_ref,
        spec_id=data.selection.primary_spec_id, stopped_at=owner.clock[0], reason='explicit synthetic account pause')
    control = data.repository.load_mock_automation_control(owner.runtime.account_ref)
    cut = data.cut(orders=orders, reserved=reserved)
    return data, control, cut


def resume_request(data):
    return dict(account_ref=data.owner.runtime.account_ref, spec_id=data.selection.primary_spec_id,
                resumed_at=data.owner.clock[0], reason='explicit synthetic account entry resume')


def running_control(control, at):
    from kiwoom_monitor.application.mock_automation_admission import MockAutomationDesiredState
    return replace(control, desired_state=MockAutomationDesiredState.RUNNING,
                   control_revision=control.control_revision + 1, changed_at=at, reason='direct native resume')


def verify_account_resume_all_members(case, store, *, peer_order=False):
    from tests.unit.test_mock_automation_account_recovery import record_members
    from kiwoom_monitor.application.mock_automation_execution import resume_mock_automation
    data, previous, cut = stopped_account(case, store, peer_order=peer_order)
    recoveries = record_members(data, cut)
    case.assertEqual(2, len(recoveries))
    save = data.repository.save_mock_automation_control
    calls_before = tuple(data.owner.transport.calls)
    with patch.object(data.repository, 'save_mock_automation_control', wraps=save) as cas:
        resumed = resume_mock_automation(data.repository, data.owner.runtime, **resume_request(data))
    case.assertEqual(1, cas.call_count)
    case.assertEqual(previous.control_revision + 1, resumed.control_revision)
    case.assertEqual('RUNNING', resumed.desired_state.value)
    case.assertEqual(data.selection, resumed.account_selection)
    case.assertEqual(resumed, data.repository.load_mock_automation_control(data.owner.runtime.account_ref))
    case.assertFalse(resumed.entry_resume_required)
    case.assertFalse(data.owner.runtime._new_orders_enabled)
    case.assertEqual(calls_before, tuple(data.owner.transport.calls))
    if peer_order:
        case.assertEqual((1, 1), tuple(value.open_order_count for value in recoveries))
        case.assertEqual(1, len(data.owner.execution.account_active_intents(data.owner.runtime.account_ref)[0]))
    return data, resumed, cut


async def verify_typed_parallel_protection_resume(case, factory=None, *, pending_peer=False):
    from tests.unit.test_mock_automation_parallel_strategies import (
        first_parallel_buys, fill_order, deliver_price, advance_members,
        member_records, reopen_parallel_runtime,
    )
    from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation, resume_mock_automation
    from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_account_recovery_from_cut
    from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_broker_legs
    from kiwoom_monitor.domain.order_contract import TERMINAL_ORDER_STATES

    data, (leader_buy, close_buy) = await first_parallel_buys(case, factory)
    # Both accepted physical orders fill in the same broker cut. Refreshing
    # after just one fill could legitimately reprice the other working leg.
    data.clock[0] += timedelta(milliseconds=1)
    data.book.fill(leader_buy.broker_order_id, 2, 'typed-group-leader-two')
    if not pending_peer:
        data.book.fill(close_buy.broker_order_id, 4, 'typed-group-close-four')
    await advance_members(data)
    if pending_peer:
        await data.bundle.monitor.refresh_recovery()  # A management mutation invalidates the previous cut.
    case.assertIsNone(data.execution.load(data.manual.intent_id), 'this case does not restore manual lots')
    case.assertEqual([2, None if pending_peer else 4],
                     [episode._episode_position.state.position_quantity for episode in data.episodes])
    case.assertEqual({'005930': 2 if pending_peer else 6},
                     data.bundle.monitor.latest_automation_execution_cut['observation'].account.positions)
    close_head = data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
                                                  close_buy.intent.symbol, close_buy.intent.plan)
    case.assertEqual(0 if pending_peer else 4, close_head.quantity)
    close_document = deepcopy(data.store.load_document('execution_mock_plan_positions', data.runtime.account_ref,
                                                       close_buy.intent.plan.plan_id)['document'])
    if pending_peer:
        current_close = data.execution.load(close_buy.intent.intent_id)
        case.assertIn(current_close.state.value, {'ACCEPTED', 'PARTIALLY_FILLED'},
                      {'close_status': data.episodes[1].status, 'management': data.book.mutations,
                       'events': data.execution.events(close_buy.intent.intent_id)})
        case.assertEqual(4, sum(leg.remaining_quantity for leg in execution_broker_legs(current_close)
                               if leg.state not in TERMINAL_ORDER_STATES))
    outcomes = []
    dispatch = data.episodes[0]._dispatch_original_and_register
    def observed_dispatch(*args, **kwargs):
        try:
            result = dispatch(*args, **kwargs)
        except Exception as error:
            outcomes.append({'error': type(error).__name__ + ': ' + str(error)})
            raise
        outcomes.append({'gate': result[0].status.value, 'reasons': result[0].reasons,
                         'intent': result[1].intent.intent_id if result[1] is not None else None})
        return result
    case.enterContext(patch.object(data.episodes[0], '_dispatch_original_and_register', side_effect=observed_dispatch))
    await deliver_price(data, 950 if pending_peer else 940)
    first = data.episodes[0]._protection_snapshot()
    case.assertEqual(2, first['planned_leader_stop']['stop_loss']['quantity'])
    await deliver_price(data, 951 if pending_peer else 960)
    await advance_members(data)
    sells = member_records(data, 0, side='SELL')
    case.assertEqual(1, len(sells), {
        'leader_status': data.episodes[0].status, 'close_status': data.episodes[1].status,
        'transport': data.transport.calls, 'notice': data.episodes[0]._protection_snapshot(), 'native_dispatch': outcomes,
    })
    protective, = sells
    case.assertEqual((2, leader_buy.intent.plan.plan_id), (protective.intent.quantity, protective.intent.plan.plan_id))
    case.assertEqual([('BUY', 2), ('BUY', 4), ('SELL', 2)], [(row[1], row[3]) for row in data.transport.calls])
    await fill_order(data, protective, 2, 'typed-group-leader-protective-two')
    leader_head = data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
                                                   leader_buy.intent.symbol, leader_buy.intent.plan)
    case.assertEqual(0, leader_head.quantity)
    case.assertEqual(close_head, data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
                                                                  close_buy.intent.symbol, close_buy.intent.plan))
    case.assertEqual(close_document, data.store.load_document('execution_mock_plan_positions', data.runtime.account_ref,
                                                             close_buy.intent.plan.plan_id)['document'])
    if pending_peer:
        current_close = data.execution.load(close_buy.intent.intent_id)
        legs = tuple(leg for leg in execution_broker_legs(current_close)
                     if leg.state not in TERMINAL_ORDER_STATES and leg.remaining_quantity)
        case.assertEqual(4, sum(leg.remaining_quantity for leg in legs))
        data.clock[0] += timedelta(milliseconds=1)
        for index, leg in enumerate(legs):
            data.book.fill(leg.broker_order_id, leg.remaining_quantity, 'typed-group-peer-later-four:' + str(index))
        await advance_members(data)
        close_head = data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
                                                      close_buy.intent.symbol, close_buy.intent.plan)
        case.assertEqual(4, close_head.quantity)
        close_document = deepcopy(data.store.load_document('execution_mock_plan_positions', data.runtime.account_ref,
                                                           close_buy.intent.plan.plan_id)['document'])
    case.assertEqual({'005930': 4}, data.bundle.monitor.latest_automation_execution_cut['observation'].account.positions)
    case.assertEqual(4, data.episodes[1]._episode_position.state.position_quantity)
    control = data.repository.load_mock_automation_control(data.runtime.account_ref)
    data.clock[0] += timedelta(milliseconds=100)
    emergency_stop_mock_automation(data.repository, data.runtime, account_ref=data.runtime.account_ref,
        spec_id=data.selection.primary_spec_id, stopped_at=data.clock[0], reason='explicit stop after own typed protection',
        expected_control_revision=control.control_revision)
    stopped = data.repository.load_mock_automation_control(data.runtime.account_ref)
    data.clock[0] += timedelta(milliseconds=100)
    await data.bundle.monitor.refresh_recovery()
    cut = data.bundle.monitor.latest_automation_execution_cut
    recoveries = record_mock_automation_account_recovery_from_cut(data.repository, data.runtime, cut,
        account_ref=data.runtime.account_ref, selection_id=data.selection.selection_id)
    case.assertEqual(2, len(recoveries))
    case.assertEqual({cut['risk'].snapshot_id}, {value.risk_snapshot_id for value in recoveries})
    case.assertEqual((), recoveries[0].own_position_quantities)
    case.assertEqual((('005930', 4),), recoveries[1].own_position_quantities)
    leader_episode = data.episodes[0]
    notice = leader_episode._protection_snapshot()
    case.assertEqual('PENDING', notice['state'])
    case.assertIn('planned_leader_protection_exit', notice)
    resolved = leader_episode.resolve_protection_review(expected_revision=notice['revision'],
        recovery=cut['recovery'], risk=cut['risk'], planned_resume_control=stopped)
    case.assertEqual('RESOLVED_FLAT', resolved['state'])
    receipt = resolved['planned_review_resume_id']
    case.assertEqual(notice['planned_leader_protection_exit'], resolved['planned_leader_protection_exit'])
    case.assertEqual(cut['risk'].snapshot_id, resolved['resolved_risk_snapshot_id'])
    reviews = {data.specs[0].spec_id: receipt}
    save = data.repository.save_mock_automation_control
    with patch.object(data.repository, 'save_mock_automation_control', wraps=save) as cas:
        resumed = resume_mock_automation(data.repository, data.runtime, account_ref=data.runtime.account_ref,
            spec_id=data.selection.primary_spec_id, resumed_at=data.clock[0], reason='explicit typed all-member resume',
            planned_review_resume_ids=reviews)
    case.assertEqual(1, cas.call_count)
    case.assertEqual(stopped.control_revision + 1, resumed.control_revision)
    case.assertEqual(reviews, resumed.planned_review_resume_ids)
    case.assertNotIn(data.specs[1].spec_id, resumed.planned_review_resume_ids)
    case.assertFalse(resumed.entry_resume_required)
    case.assertFalse(data.runtime._new_orders_enabled)
    for episode in data.episodes:
        episode._save_checkpoint(force=True)
    old_owner, old_calls = data.runtime, tuple(data.transport.calls)
    data.clock[0] += timedelta(milliseconds=100)
    await reopen_parallel_runtime(case, data)
    case.assertIsNot(old_owner, data.runtime)
    case.assertFalse(old_owner._active)
    persisted = data.repository.load_mock_automation_control(data.runtime.account_ref)
    case.assertEqual(resumed, persisted)
    case.assertEqual(reviews, persisted.planned_review_resume_ids)
    restored_notice = data.episodes[0]._protection_snapshot()
    case.assertEqual('RESOLVED_FLAT', restored_notice['state'])
    case.assertEqual(receipt, restored_notice['planned_review_resume_id'])
    case.assertTrue(data.episodes[0]._restored_planned_resume_matches(restored_notice))
    case.assertFalse(data.episodes[0]._protection_pending())
    case.assertEqual(4, data.episodes[1]._episode_position.state.position_quantity)
    case.assertEqual(close_head, data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
                                                                  close_buy.intent.symbol, close_buy.intent.plan))
    case.assertEqual(close_document, data.store.load_document('execution_mock_plan_positions', data.runtime.account_ref,
                                                             close_buy.intent.plan.plan_id)['document'])
    await advance_members(data)
    case.assertEqual(old_calls, tuple(data.transport.calls))
    case.assertFalse(data.runtime._new_orders_enabled)
    case.assertIsNone(data.execution.load(data.manual.intent_id))
    case.assertEqual({'005930': 4}, data.bundle.monitor.latest_automation_execution_cut['observation'].account.positions)
    return data


class TypedParallelResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_leader_protection_resume_map_survives_native_reopen_and_preserves_close_four(self):
        await verify_typed_parallel_protection_resume(self)

    async def test_actual_leader_protection_preserves_pending_close_then_resumes_only_its_own_review(self):
        await verify_typed_parallel_protection_resume(self, pending_peer=True)


class AccountResumeTests(unittest.TestCase):
    def setUp(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = SQLiteQueryStore(root / 'account-resume.sqlite3')
        self.store.initialize()
        self.addCleanup(self.store.close)

    def test_explicit_resume_commits_once_after_every_member_recovery_and_keeps_runtime_gate_closed(self):
        verify_account_resume_all_members(self, self.store)

    def test_verified_peer_accepted_order_survives_explicit_group_resume_without_resubmission(self):
        verify_account_resume_all_members(self, self.store, peer_order=True)

    def test_partial_member_recovery_cannot_resume_through_app_or_direct_native_cas(self):
        from kiwoom_monitor.application.mock_automation_execution import resume_mock_automation
        from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
        data, previous, cut = stopped_account(self, self.store)
        record_mock_automation_recovery_from_risk(data.repository, data.owner.runtime, cut['recovery'], cut['risk'],
            account_ref=data.owner.runtime.account_ref, spec_id=data.selection.member_spec_ids[0],
            observation=cut['observation'])
        with self.assertRaisesRegex(RuntimeError, 'every selected member'):
            resume_mock_automation(data.repository, data.owner.runtime, **resume_request(data))
        with self.assertRaisesRegex(RuntimeError, 'MOCK_ACCOUNT_RESUME_PROOF_MISSING'):
            data.repository.save_mock_automation_control(running_control(previous, data.owner.clock[0]),
                expected_revision=previous.control_revision, resume_owner_token=data.owner.runtime.owner_token)
        self.assertEqual(previous, data.repository.load_mock_automation_control(data.owner.runtime.account_ref))
        self.assertFalse(data.owner.runtime._new_orders_enabled)

    def test_mixed_risk_cuts_cannot_resume_and_complete_same_cut_can(self):
        from tests.unit.test_mock_automation_account_recovery import record_members
        from kiwoom_monitor.application.mock_automation_execution import resume_mock_automation
        from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
        data, previous, first = stopped_account(self, self.store)
        record_members(data, first)
        latest = data.cut()
        record_mock_automation_recovery_from_risk(data.repository, data.owner.runtime, latest['recovery'], latest['risk'],
            account_ref=data.owner.runtime.account_ref, spec_id=data.selection.member_spec_ids[0], observation=latest['observation'])
        with self.assertRaisesRegex(RuntimeError, 'every selected member'):
            resume_mock_automation(data.repository, data.owner.runtime, **resume_request(data))
        with self.assertRaisesRegex(RuntimeError, 'MOCK_ACCOUNT_RESUME_MEMBER_RECOVERY_CHANGED'):
            data.repository.save_mock_automation_control(running_control(previous, data.owner.clock[0]),
                expected_revision=previous.control_revision, resume_owner_token=data.owner.runtime.owner_token)
        record_members(data, latest)
        result = resume_mock_automation(data.repository, data.owner.runtime, **resume_request(data))
        self.assertEqual(previous.control_revision + 1, result.control_revision)

    def test_new_risk_after_app_validation_is_rejected_in_native_transaction(self):
        from tests.unit.test_mock_automation_account_recovery import record_members
        from kiwoom_monitor.application.mock_automation_execution import resume_mock_automation
        data, previous, cut = stopped_account(self, self.store)
        record_members(data, cut)
        save = data.repository.save_mock_automation_control
        def change_cut(value, **kwargs):
            data.cut()
            return save(value, **kwargs)
        with patch.object(data.repository, 'save_mock_automation_control', side_effect=change_cut):
            with self.assertRaisesRegex(RuntimeError, 'MOCK_ACCOUNT_RESUME_MEMBER_RECOVERY_CHANGED'):
                resume_mock_automation(data.repository, data.owner.runtime, **resume_request(data))
        self.assertEqual(previous, data.repository.load_mock_automation_control(data.owner.runtime.account_ref))
        self.assertFalse(data.owner.runtime._new_orders_enabled)

    def test_generic_resolved_notice_cannot_clear_a_members_actual_working_order(self):
        from tests.unit.test_mock_automation_account_recovery import record_members
        data, previous, cut = stopped_account(self, self.store, peer_order=True)
        record_members(data, cut)
        member_id = data.selection.member_spec_ids[0]
        self.store.upsert_documents('execution_mock_automation_runner_current', [{
            'owner': data.owner.runtime.account_ref, 'key': 'member:' + member_id, 'document': {
                'spec_id': member_id, 'execution_run_id': data.owner.run_id, 'protection_review': {
                    'state': 'RESOLVED_FLAT', 'requested_at': data.owner.clock[0].isoformat()}}}])
        with self.assertRaisesRegex(RuntimeError, 'MOCK_ACCOUNT_RESUME_REVIEW_FLAT_CHANGED'):
            data.repository.save_mock_automation_control(running_control(previous, data.owner.clock[0]),
                expected_revision=previous.control_revision, resume_owner_token=data.owner.runtime.owner_token)
        self.assertEqual(previous, data.repository.load_mock_automation_control(data.owner.runtime.account_ref))
        self.assertEqual(1, len(data.owner.execution.account_active_intents(data.owner.runtime.account_ref)[0]))

    def test_direct_group_resume_requires_current_owner_and_compatible_current_binding(self):
        from tests.unit.test_mock_automation_account_recovery import record_members
        data, previous, cut = stopped_account(self, self.store)
        record_members(data, cut)
        value = running_control(previous, data.owner.clock[0])
        with self.assertRaisesRegex(RuntimeError, 'MOCK_ACCOUNT_RESUME_SCOPE_CHANGED'):
            data.repository.save_mock_automation_control(value, expected_revision=previous.control_revision)
        with self.assertRaises(RuntimeError):
            data.repository.save_mock_automation_control(value, expected_revision=previous.control_revision,
                                                         resume_owner_token='a-different-current-owner')
        self.store.append_account_binding({**data.binding, 'binding_revision': data.binding['binding_revision'] + 1,
            'verified_at': (data.owner.clock[0] + timedelta(milliseconds=1)).isoformat()})
        with self.assertRaisesRegex(RuntimeError, 'MOCK_ACCOUNT_CONTROL_BINDING_CHANGED'):
            data.repository.save_mock_automation_control(value, expected_revision=previous.control_revision,
                                                         resume_owner_token=data.owner.runtime.owner_token)
        self.assertEqual(previous, data.repository.load_mock_automation_control(data.owner.runtime.account_ref))

    def test_group_review_receipts_are_durable_typed_and_never_change_standalone_asdict_fields(self):
        from tests.unit.test_mock_automation_account_recovery import record_members
        from kiwoom_monitor.application.mock_automation_admission import (
            MockAutomationControl, MockAutomationAccountResumeControl, mock_automation_control_from_dict)
        data, previous, cut = stopped_account(self, self.store)
        original_fields = ('version', 'account_ref', 'desired_state', 'control_revision', 'active_spec_id',
            'execution_run_id', 'changed_at', 'reason', 'entry_resume_required', 'planned_review_resume_id', 'account_selection')
        self.assertEqual(original_fields, tuple(field.name for field in fields(MockAutomationControl)))
        self.assertEqual(set(original_fields), set(asdict(previous)))
        receipt = 'planned_protection_resume_' + 'a' * 64
        member_id = data.selection.member_spec_ids[1]
        value = MockAutomationAccountResumeControl(**running_control(previous, data.owner.clock[0]).__dict__,
                                                  planned_review_resumes=((member_id, receipt),))
        self.assertEqual({member_id: receipt}, value.to_dict()['planned_review_resume_ids'])
        self.assertEqual(value, mock_automation_control_from_dict(value.to_dict()))
        value.planned_review_resume_ids.clear()
        self.assertEqual({member_id: receipt}, value.planned_review_resume_ids)
        record_members(data, cut)
        with self.assertRaisesRegex(RuntimeError, 'MOCK_ACCOUNT_RESUME_REVIEW_SCOPE_CHANGED'):
            data.repository.save_mock_automation_control(value, expected_revision=previous.control_revision,
                                                         resume_owner_token=data.owner.runtime.owner_token)
        self.assertEqual(previous, data.repository.load_mock_automation_control(data.owner.runtime.account_ref))
