"""Real admitted members borrow one runtime and retain separate Episode state."""
import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


class _Monitor:
    def __init__(self):
        self.listener = None
        self.listener_sets = self.listener_clears = 0
        self.input_changes = []

    def set_automation_risk_listener(self, listener):
        assert self.listener is None
        self.listener, self.listener_sets = listener, self.listener_sets + 1

    def clear_automation_risk_listener(self, listener):
        assert self.listener == listener
        self.listener = None
        self.listener_clears += 1

    def set_automation_execution_inputs_enabled(self, enabled):
        self.input_changes.append(enabled)


def member_episode_inputs(case, store):
    from tests.unit.test_mock_automation_member_risk import member_fill_inputs
    from kiwoom_monitor.central_server.mock_runtime import MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION, MockAutomationRuntimeContext
    from kiwoom_monitor.central_server.mock_automation_episode import MockAutomationEpisode
    data, owner, planned, buys, events, recovery, risk, build = member_fill_inputs(case, store)
    admissions = tuple(data.repository.load_mock_automation_admission_for_spec(owner.runtime.account_ref, spec.spec_id)
                       for spec in data.specs)
    context = MockAutomationRuntimeContext(MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION, owner.runtime.account_ref,
        owner.run_id, data.specs[0].spec_id, admissions[0].admission_id, 1, 1)
    monitor = _Monitor()
    bundle = SimpleNamespace(account_ref=owner.runtime.account_ref, run_id=owner.run_id,
        credential_profile_id=data.selection.credential_profile_id, automation_context=context,
        repository=owner.execution, runtime=owner.runtime, monitor=monitor)
    episodes = tuple(MockAutomationEpisode(store, bundle, spec, member_admission=admission,
                         now_provider=lambda: owner.clock[0]) for spec, admission in zip(data.specs, admissions, strict=True))
    for episode in episodes:
        episode.prepare_restore()
    return data, owner, bundle, episodes, recovery, risk


def verify_native_member_episode_checkpoint(case, store):
    from kiwoom_monitor.central_server.mock_automation_episode import MockAutomationEpisode
    data, owner, bundle, episodes, recovery, risk = member_episode_inputs(case, store)
    for episode, price in zip(episodes, (69_000, 70_000), strict=True):
        state = episode._episode_position.state
        case.assertEqual((1, price), (state.position_quantity, state.entry_price))
        case.assertEqual(risk.execution_event_high_watermark, episode.status['fill_cursor'])
        case.assertTrue(episode._episode_position.matches_risk(risk))
        episode._save_checkpoint(force=True)
    case.assertIsNone(store.load_document('execution_mock_automation_runner_current', bundle.account_ref, 'current'))
    before = [store.load_document('execution_mock_automation_runner_current', bundle.account_ref,
        'member:' + spec.spec_id)['document'] for spec in data.specs]
    case.assertEqual([spec.spec_id for spec in data.specs], [row['spec_id'] for row in before])
    episodes[0]._status = {'state': 'BLOCKED', 'reason': 'first_member_only', 'orders_enabled': False}
    episodes[0]._save_checkpoint(force=True)
    case.assertEqual(before[1], store.load_document('execution_mock_automation_runner_current', bundle.account_ref,
        'member:' + data.specs[1].spec_id)['document'])
    restored = tuple(MockAutomationEpisode(store, bundle, spec,
        member_admission=data.repository.load_mock_automation_admission_for_spec(bundle.account_ref, spec.spec_id),
        now_provider=lambda: owner.clock[0]) for spec in data.specs)
    for episode, price in zip(restored, (69_000, 70_000), strict=True):
        episode.prepare_restore()
        case.assertEqual((1, price), (episode._episode_position.state.position_quantity, episode._episode_position.state.entry_price))
        case.assertEqual(risk.execution_event_high_watermark, episode.status['fill_cursor'])
    case.assertEqual('first_member_only', restored[0].status['reason'])
    case.assertNotEqual('first_member_only', restored[1].status['reason'])
    case.assertEqual(2, len(owner.transport.calls), 'restore never resubmits either accepted BUY')
    return data, owner, bundle, episodes, recovery, risk


def initialize_store(case):
    from kiwoom_monitor.central_server.database import SQLiteQueryStore
    temporary = case.enterContext(tempfile.TemporaryDirectory())
    case.store = SQLiteQueryStore(Path(temporary) / 'members.sqlite3')
    case.store.initialize()
    case.addCleanup(case.store.close)


def verify_native_panic_member_checkpoint(case, native_factory=None):
    """A real PANIC non-anchor plan uses its own ACKed trigger in native claims."""
    from tests.unit.test_panic_mock_execution import prepare_panic_native, panic_entry, fill_panic_entry, refresh_panic_cut
    from kiwoom_monitor.application.mock_automation_admission import admit_mock_automation_account_selection
    from kiwoom_monitor.application.mock_automation_specification import publish_ready_mock_automation_spec
    from kiwoom_monitor.application.panic_mock_execution import dispatch_panic_exit
    from kiwoom_monitor.central_server.mock_automation_episode import MockAutomationEpisode
    from kiwoom_monitor.central_server.mock_runtime import MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION, MockAutomationRuntimeContext
    from kiwoom_monitor.domain.execution_activation import MockAutomationAccountLimits, MockAutomationAccountSelection
    # Defer only the fixture's standalone admission; the actual group admission,
    # Runtime, lease, recovery, gates and order transactions below are not mocked.
    ctx = prepare_panic_native(case, native_factory=native_factory, defer_admission=True)
    anchor = ctx.spec
    member = replace(anchor, maximum_capital_won=anchor.maximum_capital_won - 1)
    profile = ctx.repository.load_profile(member.strategy_ref, member.forward_profile_id)
    ready, _ = publish_ready_mock_automation_spec(ctx.repository, profile=profile,
        stage_revisions=ctx.repository.load_stage_revisions(member.strategy_ref), spec=member,
        shadow_event=ctx.event, current_binding=ctx.inputs.binding)
    case.assertEqual('READY', ready.status.value)
    limits = MockAutomationAccountLimits(2, 2, anchor.maximum_capital_won * 2, anchor.maximum_daily_loss_won,
        anchor.maximum_data_gap_seconds, anchor.maximum_submission_unknown_count, anchor.maximum_reconnect_count,
        anchor.maximum_balance_mismatch_count)
    selection = MockAutomationAccountSelection(anchor.account_scope, anchor.credential_profile_id, anchor.binding_revision,
        anchor.binding_verified_at, ctx.clock[0], (anchor.spec_id, member.spec_id), limits)
    package = ctx.repository.load_mock_automation_candidate_package(anchor.strategy_ref, anchor.candidate_package_hash)
    implementation = lambda _: package.scientific_implementation_hash
    ctx.repository.publish_mock_automation_account_selection(selection, expected_implementation_hash=implementation)
    admissions, _, control = admit_mock_automation_account_selection(ctx.repository, ctx.runtime,
        account_ref=ctx.runtime.account_ref, selection_id=selection.selection_id, requested_at=ctx.clock[0],
        expected_control_revision=0, expected_implementation_hash=implementation)
    ctx.spec = member
    refresh_panic_cut(ctx, {})
    gate, buy, receipt = panic_entry(ctx)
    case.assertIsNotNone(buy, gate)
    case.assertEqual(member.spec_id, ctx.execution.load(buy.intent.intent_id).member_spec_id)
    head = fill_panic_entry(case, ctx, buy)
    context = MockAutomationRuntimeContext(MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION, ctx.runtime.account_ref,
        ctx.runtime.run_id, anchor.spec_id, admissions[0].admission_id, control.control_revision, 1)
    bundle = SimpleNamespace(account_ref=ctx.runtime.account_ref, run_id=ctx.runtime.run_id,
        credential_profile_id=anchor.credential_profile_id, automation_context=context,
        repository=ctx.execution, runtime=ctx.runtime, monitor=_Monitor())
    episodes = tuple(MockAutomationEpisode(ctx.store, bundle, spec, member_admission=admission,
        now_provider=lambda: ctx.clock[0]) for spec, admission in zip((anchor, member), admissions, strict=True))
    for episode in episodes:
        episode.prepare_restore()
    case.assertEqual('flat', episodes[0]._episode_position.state.status)
    case.assertIsNone(episodes[0]._episode_position.state.position_quantity)
    case.assertEqual(3, episodes[1]._episode_position.state.position_quantity)
    quote = {**ctx.quote, 'price_won': ctx.event['panic_entry_setup']['failure_price'],
        'available_at': ctx.clock[0], 'input_ref': 'collector-trade:native-group-panic-first-failure'}
    episodes[1].observe_automation_price(ctx.event['symbol'], quote)
    episodes[1]._save_checkpoint(force=True)
    key = 'member:' + member.spec_id
    saved = ctx.store.load_document('execution_mock_automation_runner_current', bundle.account_ref, key)['document']
    trigger = saved['minute_management']['triggers'][head.plan.plan_id]
    other = ctx.store.load_document('execution_mock_automation_runner_current', bundle.account_ref,
        'member:' + anchor.spec_id)['document']
    case.assertEqual({}, other['minute_management']['triggers'])
    # A real saved trigger is necessary. Corrupt only this owned isolated DB
    # checkpoint and prove the original native writer refuses before transport.
    changed = {**saved, 'minute_management': {**saved['minute_management'], 'triggers': {}}}
    ctx.store.upsert_documents('execution_mock_automation_runner_current',
        [{'owner': bundle.account_ref, 'key': key, 'document': changed}])
    before_calls = list(ctx.transport.calls)
    kwargs = dict(parameters=ctx.parameters, event=ctx.event, trigger_quote=quote, quote=quote,
        recovery=ctx.recovery, risk=ctx.risk, panic_trigger=trigger)
    with case.assertRaisesRegex(RuntimeError, 'PANIC_FIRST_TRIGGER_CHECKPOINT_CHANGED'):
        dispatch_panic_exit(ctx.repository, ctx.runtime, ctx.execution, member, head.plan, **kwargs)
    case.assertEqual(before_calls, ctx.transport.calls)
    ctx.store.upsert_documents('execution_mock_automation_runner_current',
        [{'owner': bundle.account_ref, 'key': key, 'document': saved}])
    gate, sell, receipt = dispatch_panic_exit(ctx.repository, ctx.runtime, ctx.execution, member, head.plan, **kwargs)
    case.assertIsNotNone(sell, gate)
    case.assertEqual(3, sell.intent.quantity)
    case.assertEqual(member.spec_id, sell.member_spec_id)
    case.assertEqual(other, ctx.store.load_document('execution_mock_automation_runner_current', bundle.account_ref,
        'member:' + anchor.spec_id)['document'])
    case.assertIsNone(ctx.store.load_document('execution_mock_automation_runner_current', bundle.account_ref, 'current'))


class NativeAccountEpisodeTests(unittest.TestCase):
    def setUp(self):
        initialize_store(self)

    def test_actual_member_fills_restore_from_independent_native_checkpoints_without_resubmission(self):
        verify_native_member_episode_checkpoint(self, self.store)

    def test_non_anchor_panic_plan_requires_its_own_acknowledged_trigger_at_the_native_order_claim(self):
        verify_native_panic_member_checkpoint(self)

    def test_member_admission_must_match_the_actual_stored_selection_and_same_runtime(self):
        from kiwoom_monitor.central_server.mock_automation_episode import MockAutomationEpisode
        data, owner, bundle, episodes, recovery, risk = member_episode_inputs(self, self.store)
        admission = episodes[1]._member_admission
        with self.assertRaisesRegex(ValueError, 'MEMBER_CONTEXT_MISMATCH'):
            MockAutomationEpisode(self.store, bundle, data.specs[0], member_admission=admission)
        bad = replace(admission, admission_id='forged-member-admission')
        candidate = MockAutomationEpisode(self.store, bundle, data.specs[1], member_admission=bad)
        with self.assertRaisesRegex(ValueError, 'MEMBER_ADMISSION_CHANGED'):
            candidate.prepare_restore()
        self.assertFalse(candidate.prepared)
        self.assertEqual(2, len(owner.transport.calls))

    def test_member_stop_uses_its_actual_basis_and_quantity_from_the_same_full_account_risk(self):
        from kiwoom_monitor.application.mock_automation_risk import mock_automation_stop_loss_trigger, validate_mock_stop_loss_proof
        data, owner, bundle, episodes, recovery, risk = member_episode_inputs(self, self.store)
        before = risk.to_dict()
        for spec, basis in zip(data.specs, (69_000, 70_000), strict=True):
            trigger = mock_automation_stop_loss_trigger(spec, risk, execution_run_id=owner.run_id,
                now=owner.clock[0], price={'symbol': '005930', 'price_won': 65_000,
                    'available_at': owner.clock[0], 'input_ref': 'collector-trade:actual-member-stop',
                    'durable': False, 'market_status': 'TRADABLE'}, account_positions=recovery.account.positions, stop_loss_bps=500)
            self.assertEqual((1, basis), (trigger['stop_loss']['quantity'], trigger['stop_loss']['entry_price_won']))
            validate_mock_stop_loss_proof(trigger['stop_loss'], spec, risk, stop_loss_bps=500)
            with self.assertRaisesRegex(ValueError, 'native_basis_changed'):
                validate_mock_stop_loss_proof({**trigger['stop_loss'], 'quantity': 2}, spec, risk, stop_loss_bps=500)
        self.assertEqual(before, risk.to_dict(), 'a member view never replaces or relabels canonical Risk')
        self.assertEqual((('005930', 2),), risk.owned_position_quantities)


class NativeAccountRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        initialize_store(self)

    async def test_one_member_delivery_failure_keeps_other_member_risk_and_original_trade_delivery(self):
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
        data, owner, bundle, episodes, recovery, risk = member_episode_inputs(self, self.store)
        runner = MockAutomationRunner(episodes[0], member_episodes=(episodes[1],), monitor=bundle.monitor,
            execution_order=tuple(episode.spec_id for episode in episodes))
        runner.attach_inputs()
        with patch.object(episodes[0], 'observe_native_risk', side_effect=ValueError('member input failure')):
            with self.assertRaisesRegex(ValueError, 'member input failure'):
                bundle.monitor.listener({'risk': risk, 'account': recovery.account})
        self.assertIs(risk, episodes[1]._stop_loss_cut[0])
        # Exercise the existing source delivery methods; this is not a claim
        # that these generic bar-family members evaluate the leader strategy.
        supervisor = MockAutomationSupervisor.__new__(MockAutomationSupervisor)
        supervisor._closing, supervisor._runners = False, {bundle.account_ref: runner}
        payload, price = {'kind': '0B', 'stock_code': '005930'}, {'price_won': 65_000, 'available_at': owner.clock[0]}
        for method, callback, args, kwargs in (
            ('observe_leader_trade', 'observe_leader_trade', (payload, owner.clock[0]), {'source': 'actual-fixture', 'approved': True}),
            ('observe_automation_price', 'observe_automation_price', ('005930', price), {}),
        ):
            with patch.object(episodes[0], callback, side_effect=ValueError('member input failure')), \
                    patch.object(episodes[1], callback, wraps=getattr(episodes[1], callback)) as delivered:
                with self.assertRaisesRegex(ValueError, 'member input failure'):
                    getattr(supervisor, method)(*args, **kwargs)
                delivered.assert_called_once_with(*args, **kwargs)
        await runner.close()
        self.assertEqual(2, len(owner.transport.calls))

    async def test_one_task_listener_and_close_advance_both_real_member_episodes_after_shared_stop(self):
        from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        data, owner, bundle, episodes, recovery, risk = member_episode_inputs(self, self.store)
        emergency_stop_mock_automation(data.repository, owner.runtime, account_ref=bundle.account_ref,
            spec_id=data.specs[1].spec_id, stopped_at=owner.clock[0], reason='explicit whole-account stop')
        observed = []
        reached = asyncio.Event()
        for episode in episodes:
            original = episode.advance_once
            async def advance(*, _original=original, _episode=episode, **kwargs):
                value = await _original(**kwargs)
                observed.append(_episode.spec_id)
                if set(observed) == {spec.spec_id for spec in data.specs}:
                    reached.set()
                return value
            self.enterContext(patch.object(episode, 'advance_once', advance))
        # This fixture explicitly selects its order; the selection anchor itself
        # never supplies a priority or an account-entry authorization.
        runner = MockAutomationRunner(episodes[0], member_episodes=(episodes[1],), monitor=bundle.monitor,
            execution_order=(episodes[1].spec_id, episodes[0].spec_id))
        runner.attach_inputs()
        self.assertEqual(1, bundle.monitor.listener_sets)
        bundle.monitor.listener({'risk': risk, 'account': recovery.account})
        self.assertTrue(all(episode._stop_loss_cut[0] is risk for episode in episodes))
        await runner.start()
        task = runner._task
        await asyncio.wait_for(reached.wait(), 10)
        self.assertEqual([data.specs[1].spec_id, data.specs[0].spec_id], observed[:2])
        self.assertTrue(all(episode.status['state'] == 'STOPPED' for episode in episodes))
        self.assertIs(episodes[0], runner.episode_for_spec(data.specs[0].spec_id))
        await runner.close()
        self.assertTrue(task.done())
        self.assertIsNone(runner._task)
        self.assertEqual(1, bundle.monitor.listener_clears)
        self.assertTrue(all(episode.closing for episode in episodes))
        self.assertEqual(2, len(owner.transport.calls), 'STOP/FILL/close adds no order transport')

    async def test_failed_member_checkpoint_close_still_drains_other_member_checkpoint(self):
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        data, owner, bundle, episodes, recovery, risk = member_episode_inputs(self, self.store)
        for episode in episodes:
            episode._save_checkpoint(force=True)
        key = 'member:' + episodes[1].spec_id
        before = self.store.load_document('execution_mock_automation_runner_current', bundle.account_ref, key)['document']['checkpoint_revision']
        runner = MockAutomationRunner(episodes[0], member_episodes=(episodes[1],), monitor=bundle.monitor,
            execution_order=tuple(episode.spec_id for episode in episodes))
        with patch.object(episodes[0], '_save_checkpoint', side_effect=RuntimeError('first checkpoint failure')):
            # This OFF cue makes an unscheduled close persist its original state.
            for episode in episodes:
                episode.request_protection_review(reason='explicit OFF review', input_ref='test-close', requested_at=owner.clock[0])
            with self.assertRaisesRegex(RuntimeError, 'first checkpoint failure'):
                await runner.close()
        after = self.store.load_document('execution_mock_automation_runner_current', bundle.account_ref, key)['document']['checkpoint_revision']
        self.assertGreater(after, before)
        self.assertTrue(all(episode.closing for episode in episodes))
        self.assertEqual(2, len(owner.transport.calls))

    async def test_duplicate_members_and_distinct_runtime_bundles_cannot_share_one_runner(self):
        from kiwoom_monitor.central_server.mock_automation_episode import MockAutomationEpisode
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        data, owner, bundle, episodes, recovery, risk = member_episode_inputs(self, self.store)
        with self.assertRaisesRegex(ValueError, 'EXPLICIT_MEMBER_EXECUTION_ORDER_REQUIRED'):
            MockAutomationRunner(episodes[0], member_episodes=(episodes[1],))
        incomplete = MockAutomationRunner(episodes[0], monitor=bundle.monitor)
        with self.assertRaisesRegex(ValueError, 'RUNNER_MEMBERS_MISMATCH'):
            incomplete.attach_inputs()
        self.assertEqual(0, bundle.monitor.listener_sets)
        with self.assertRaisesRegex(ValueError, 'RUNNER_MEMBERS_MISMATCH'):
            MockAutomationRunner(episodes[0], member_episodes=(episodes[0],))
        other_bundle = SimpleNamespace(**vars(bundle))
        other = MockAutomationEpisode(self.store, other_bundle, data.specs[1], member_admission=episodes[1]._member_admission)
        with self.assertRaisesRegex(ValueError, 'RUNNER_MEMBERS_MISMATCH'):
            MockAutomationRunner(episodes[0], member_episodes=(other,))
