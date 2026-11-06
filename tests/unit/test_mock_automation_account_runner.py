"""Real admitted members borrow one runtime and retain separate Episode state."""
import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


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


def public_group_inputs(case):
    """Native READY/admission/lease/Risk; the broker recovery cut is explicit."""
    from tests.unit.test_mock_automation_account_execution import grouped_inputs, group_runtime
    from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
    from kiwoom_monitor.application.position_target import reconcile_position_account_observation
    from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
    from kiwoom_monitor.central_server.mock_runtime import MockAccountRuntimeMode
    from kiwoom_monitor.domain.order_contract import AccountBinding, AccountSnapshot
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
    from datetime import timedelta
    data = grouped_inputs(case, case.store)
    data.selection = replace(data.selection, execution_order_spec_ids=tuple(reversed(data.selection.member_spec_ids)))
    data.repository.publish_mock_automation_account_selection(data.selection,
        expected_implementation_hash=lambda _: data.implementation_hash)
    native = group_runtime(case, case.store, data)

    class Monitor(_Monitor):
        def __init__(self):
            super().__init__()
            self.enabled = False
            self.refreshes = 0
            self.latest_automation_execution_cut = None
            self.recovery_override = None

        def set_automation_execution_inputs_enabled(self, enabled, *, request_recovery=True):
            self.enabled = enabled
            self.input_changes.append((enabled, request_recovery))

        @property
        def automation_execution_inputs_enabled(self):
            return self.enabled

        async def refresh_recovery(self):
            self.refreshes += 1
            native.clock[0] += timedelta(seconds=1)
            account = AccountSnapshot(native.runtime.account_ref, 250_000, 0, {}, native.clock[0])
            recovery = MockAccountRecovery(account, ()) if self.recovery_override is None else replace(
                self.recovery_override, account=replace(self.recovery_override.account, as_of=native.clock[0]))
            account = recovery.account
            records, complete = native.execution.account_active_intents(account.account_ref)
            events = native.execution.account_events('mock', account.account_ref).events
            risk = build_mock_automation_risk_snapshot(recovery, events, (), records,
                execution_run_id=native.run_id, binding_revision=data.selection.binding_revision,
                reconciliation_revision=self.refreshes, observed_at=native.clock[0],
                broker_query_started_at=native.clock[0], broker_query_completed_at=native.clock[0])
            data.repository.save_mock_automation_risk_snapshot(risk)
            observation = reconcile_position_account_observation(recovery, records, records,
                input_ref='mock-account-ram:public-group:' + str(self.refreshes), observed_at=native.clock[0],
                ledger_complete=complete, runtime_stable=True, ledger_read_failed=False, reconciled_records=records)
            observation = replace(observation, risk_snapshot=risk)
            self.latest_automation_execution_cut = dict(risk=risk, observation=observation, recovery=recovery)
            return recovery

    monitor = Monitor()
    bundle = SimpleNamespace(account_ref=native.runtime.account_ref, run_id=native.run_id,
        credential_profile_id=data.selection.credential_profile_id, runtime=native.runtime,
        repository=native.execution, monitor=monitor, runtime_mode=MockAccountRuntimeMode.MANUAL,
        automation_context=None)
    bundle.refresh_automation_control = lambda control: setattr(bundle, 'automation_context',
        replace(bundle.automation_context, control_revision=control.control_revision))

    class Owner:
        def __init__(self):
            self.current = bundle
            self.switches = []
            self.require_execution_settings = AsyncMock()

        def bundle(self, profile):
            return self.current if profile == data.selection.credential_profile_id else None

        async def switch_execution_mode(self, profile, mode, **kwargs):
            self.switches.append((profile, mode, kwargs))
            bundle.runtime_mode = mode
            bundle.automation_context = kwargs['automation_context']
            return bundle

        def account_bindings(self):
            return (AccountBinding(data.selection.credential_profile_id, data.selection.account_scope,
                data.selection.binding_revision, data.selection.binding_verified_at),)

        def active_revision(self, profile):
            return 1

    owner = Owner()
    supervisor = MockAutomationSupervisor(case.store, owner, poll_seconds=60,
        now_provider=lambda: native.clock[0], expected_implementation_hash=lambda _: data.implementation_hash)
    case.addAsyncCleanup(supervisor.close)
    arguments = dict(credential_profile_id=data.selection.credential_profile_id,
        account_ref=bundle.account_ref, spec_id=data.selection.primary_spec_id,
        expected_settings_revision=1, credential_revision=1, account_selection_id=data.selection.selection_id)
    return data, native, owner, bundle, supervisor, arguments


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

    async def test_listener_detach_failure_still_joins_the_one_task_and_every_member(self):
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        data, owner, bundle, episodes, recovery, risk = member_episode_inputs(self, self.store)
        runner = MockAutomationRunner(episodes[0], member_episodes=episodes[1:], monitor=bundle.monitor,
            execution_order=tuple(episode.spec_id for episode in episodes))
        runner.attach_inputs()
        await runner.start()
        task = runner._task
        cleared = bundle.monitor.clear_automation_risk_listener
        def fail_after_clear(listener):
            cleared(listener)
            raise RuntimeError('listener close acknowledgement lost')
        with patch.object(bundle.monitor, 'clear_automation_risk_listener', side_effect=fail_after_clear):
            with self.assertRaisesRegex(RuntimeError, 'close acknowledgement lost'):
                await runner.close()
        self.assertTrue(task.done())
        self.assertIsNone(runner._task)
        self.assertIsNone(bundle.monitor.listener)
        self.assertTrue(all(episode.closing for episode in episodes))
        self.assertTrue(all(not episode._checkpoint.pending for episode in episodes))
        self.assertEqual(2, len(owner.transport.calls))


class PublicAccountSupervisorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        initialize_store(self)

    async def test_authenticated_group_start_stop_resume_and_restore_keep_every_native_member(self):
        import httpx
        from fastapi import FastAPI, Header, HTTPException
        from kiwoom_monitor.central_server.mock_automation_routes import create_mock_automation_router
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
        data, native, owner, bundle, supervisor, arguments = public_group_inputs(self)
        def authorize(authorization: str | None = Header(default=None)):
            if authorization != 'Bearer group-test':
                raise HTTPException(401)
        app = FastAPI()
        app.include_router(create_mock_automation_router(supervisor, authorize))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://group.test') as client:
            self.assertEqual(401, (await client.post('/api/v1/mock-automation/start', json=arguments)).status_code)
            response = await client.post('/api/v1/mock-automation/start', json=arguments,
                headers={'Authorization': 'Bearer group-test'})
            self.assertEqual(200, response.status_code, response.text)
            status = response.json()
        self.assertEqual('mock_automation_account_runner_status/v1', status['runner']['version'])
        self.assertEqual(data.selection.selection_id, status['control']['account_selection']['selection_id'])
        self.assertEqual(list(data.selection.execution_order_spec_ids), status['runner']['execution_order_spec_ids'])
        self.assertEqual(list(data.selection.member_spec_ids), [value['spec_id'] for value in status['runner']['members']])
        runner = supervisor._runners[bundle.account_ref]
        self.assertEqual(2, len(runner.episodes))
        self.assertTrue(all(episode.prepared for episode in runner.episodes))
        self.assertEqual((1, 1, 1), (len(owner.switches), bundle.monitor.refreshes, bundle.monitor.listener_sets))
        self.assertFalse(native.runtime._new_orders_enabled)
        self.assertEqual([], native.transport.calls)
        again = await supervisor.activate(**arguments)
        self.assertEqual(status['runner']['account_selection_id'], again['runner']['account_selection_id'])
        self.assertEqual(1, bundle.monitor.refreshes)
        with self.assertRaisesRegex(RuntimeError, 'ALREADY_ACTIVE'):
            await supervisor.activate(**{key: value for key, value in arguments.items() if key != 'account_selection_id'})
        stopped = await supervisor.stop(credential_profile_id=arguments['credential_profile_id'],
            account_ref=bundle.account_ref, spec_id=arguments['spec_id'], expected_control_revision=1, reason='whole account stop')
        self.assertEqual('STOPPED', stopped['control']['desired_state'])
        self.assertEqual(data.selection.selection_id, stopped['control']['account_selection']['selection_id'])
        resumed = await supervisor.resume(credential_profile_id=arguments['credential_profile_id'],
            account_ref=bundle.account_ref, spec_id=arguments['spec_id'], expected_control_revision=2,
            expected_settings_revision=1, credential_revision=1, reason='explicit whole account resume')
        self.assertTrue(runner._task is None and all(episode.closing for episode in runner.episodes))
        self.assertEqual('RUNNING', resumed['control']['desired_state'])
        self.assertEqual(2, len(resumed['runner']['members']))
        self.assertEqual(2, bundle.monitor.refreshes, 'one new recovery for the whole resume')
        await supervisor.close()
        restored = MockAutomationSupervisor(self.store, owner, poll_seconds=60, now_provider=lambda: native.clock[0],
            expected_implementation_hash=lambda _: data.implementation_hash)
        self.addAsyncCleanup(restored.close)
        await restored._restore_once()
        self.assertIn(bundle.account_ref, restored._runners, restored._restore_errors)
        final = restored.status(bundle.account_ref, credential_profile_id=arguments['credential_profile_id'])
        self.assertEqual(data.selection.selection_id, final['runner']['account_selection_id'])
        self.assertEqual(2, len(final['runner']['members']))
        self.assertEqual(3, bundle.monitor.refreshes)
        self.assertIsNone(self.store.load_document('execution_mock_automation_runner_current', bundle.account_ref, 'current'))
        self.assertEqual([], native.transport.calls)

    async def test_missing_explicit_order_rejects_before_switch_or_native_admission(self):
        data, native, owner, bundle, supervisor, arguments = public_group_inputs(self)
        selection = replace(data.selection, execution_order_spec_ids=None)
        data.repository.publish_mock_automation_account_selection(selection,
            expected_implementation_hash=lambda _: data.implementation_hash)
        with self.assertRaisesRegex(RuntimeError, 'EXPLICIT_MEMBER_EXECUTION_ORDER_REQUIRED'):
            await supervisor.activate(**{**arguments, 'account_selection_id': selection.selection_id})
        self.assertEqual([], owner.switches)
        self.assertEqual(0, bundle.monitor.refreshes)
        self.assertIsNone(data.repository.load_mock_automation_control(bundle.account_ref))
        self.assertEqual([], native.transport.calls)

    async def test_unselected_or_mismatched_research_artifact_never_issues_group_admission(self):
        data, native, owner, bundle, supervisor, arguments = public_group_inputs(self)
        for implementation in (None, '0' * 64):
            with self.subTest(implementation=implementation):
                supervisor._expected_implementation_hash = lambda _: implementation
                with self.assertRaisesRegex(ValueError, 'selected implementation changed'):
                    await supervisor.activate(**arguments)
                self.assertIsNone(data.repository.load_mock_automation_control(bundle.account_ref))
                self.assertTrue(all(data.repository.load_mock_automation_admission_for_spec(bundle.account_ref, spec.spec_id) is None
                    for spec in data.specs))
                self.assertFalse(native.runtime._new_orders_enabled)
        self.assertEqual([], native.transport.calls)

    async def test_group_activation_uses_the_accepted_monitor_cut_instead_of_raw_broker_account(self):
        data, native, owner, bundle, supervisor, arguments = public_group_inputs(self)
        refresh = bundle.monitor.refresh_recovery
        async def return_raw_account():
            recovery = await refresh()
            return replace(recovery, account=replace(recovery.account, available_cash_won=250_001))
        bundle.monitor.refresh_recovery = return_raw_account

        status = await supervisor.activate(**arguments)

        cut = bundle.monitor.latest_automation_execution_cut
        self.assertEqual(2, len(status['runner']['members']))
        for spec in data.specs:
            admission = data.repository.load_mock_automation_admission_for_spec(bundle.account_ref, spec.spec_id)
            recovered = data.repository.load_latest_mock_automation_recovery(bundle.account_ref, admission.admission_id)
            self.assertEqual((cut['risk'].snapshot_id, 250_000), (recovered.risk_snapshot_id, recovered.orderable_cash_won))
        self.assertEqual(1, bundle.monitor.refreshes)
        self.assertEqual([], native.transport.calls)

    async def test_stopped_group_restore_keeps_all_original_admissions_and_never_resumes_entries(self):
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
        data, native, owner, bundle, supervisor, arguments = public_group_inputs(self)
        await supervisor.activate(**arguments)
        admissions = tuple(data.repository.load_mock_automation_admission_for_spec(bundle.account_ref, spec.spec_id) for spec in data.specs)
        stopped = await supervisor.stop(credential_profile_id=arguments['credential_profile_id'], account_ref=bundle.account_ref,
            spec_id=arguments['spec_id'], expected_control_revision=1, reason='preserve stopped member management')
        await supervisor.close()
        restored = MockAutomationSupervisor(self.store, owner, poll_seconds=60, now_provider=lambda: native.clock[0],
            expected_implementation_hash=lambda _: data.implementation_hash)
        self.addAsyncCleanup(restored.close)
        await restored._restore_once()
        self.assertIn(bundle.account_ref, restored._runners, restored._restore_errors)
        status = restored.status(bundle.account_ref, credential_profile_id=arguments['credential_profile_id'])
        self.assertEqual(stopped['control'], status['control'])
        self.assertEqual(2, len(status['runner']['members']))
        self.assertEqual(admissions, tuple(data.repository.load_mock_automation_admission_for_spec(bundle.account_ref, spec.spec_id)
            for spec in data.specs))
        async def stopped_members():
            while not all(episode.status['state'] == 'STOPPED' for episode in restored._runners[bundle.account_ref].episodes):
                await asyncio.sleep(0.01)
        await asyncio.wait_for(stopped_members(), 10)
        self.assertFalse(native.runtime._new_orders_enabled)
        self.assertEqual([], native.transport.calls)

    async def test_explicit_group_resume_resolves_each_flat_generic_protection_review(self):
        data, native, owner, bundle, supervisor, arguments = public_group_inputs(self)
        await supervisor.activate(**arguments)
        for episode in supervisor._runners[bundle.account_ref].episodes:
            episode.request_protection_review(reason='explicit OFF review', input_ref='public-group-review',
                requested_at=native.clock[0])
        await supervisor.stop(credential_profile_id=arguments['credential_profile_id'], account_ref=bundle.account_ref,
            spec_id=arguments['spec_id'], expected_control_revision=1, reason='whole group review')

        status = await supervisor.resume(credential_profile_id=arguments['credential_profile_id'], account_ref=bundle.account_ref,
            spec_id=arguments['spec_id'], expected_control_revision=2, expected_settings_revision=1,
            credential_revision=1, reason='explicit resume after native flat verification')

        self.assertEqual('RUNNING', status['control']['desired_state'])
        self.assertTrue(all(member['protection_review']['state'] == 'RESOLVED_FLAT' for member in status['runner']['members']))
        self.assertFalse(native.runtime._new_orders_enabled)
        self.assertEqual(2, bundle.monitor.refreshes)
        self.assertEqual([], native.transport.calls)

    async def test_original_stopped_plan_and_late_fill_survive_a_changed_selected_artifact(self):
        from datetime import timedelta
        from tests.unit import test_mock_automation_specification as support
        from kiwoom_monitor.application.mock_automation_execution import dispatch_mock_automation_decision_from_risk
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
        from kiwoom_monitor.domain.order_contract import AccountSnapshot, BrokerFill, BrokerOrderSnapshot, OrderPlanRef, OrderState
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
        data, native, owner, bundle, supervisor, arguments = public_group_inputs(self)
        await supervisor.activate(**arguments)
        cut = bundle.monitor.latest_automation_execution_cut
        original = data.specs[0]
        decision = support._automation_decision(native.run_id, 'ENTER', native.clock[0])
        gate, buy, receipt = dispatch_mock_automation_decision_from_risk(data.repository, native.runtime,
            decision, cut['recovery'], cut['risk'], account_ref=bundle.account_ref, spec_id=original.spec_id,
            strategy_ref=original.strategy_ref, plan=OrderPlanRef('preserved-off-plan', 'explicit-synthetic-plan/v1', 0))
        self.assertIsNotNone(buy, gate.reasons)
        admission = data.repository.load_mock_automation_admission_for_spec(bundle.account_ref, original.spec_id)
        await supervisor.stop(credential_profile_id=arguments['credential_profile_id'], account_ref=bundle.account_ref,
            spec_id=arguments['spec_id'], expected_control_revision=1, reason='OFF preserves original account management')
        await supervisor.close()
        native.clock[0] += timedelta(seconds=1)
        actual = BrokerOrderSnapshot(buy.broker_order_id, bundle.account_ref, buy.intent.symbol, OrderState.FILLED,
            1, 0, native.clock[0], (BrokerFill('late-after-off', 1, buy.intent.limit_price, native.clock[0]),))
        native.runtime.reconcile(buy.intent.intent_id, actual)
        native.runtime.reconcile(buy.intent.intent_id, actual)
        bundle.monitor.recovery_override = MockAccountRecovery(AccountSnapshot(bundle.account_ref,
            250_000 - buy.intent.limit_price, 0, {buy.intent.symbol: 1}, native.clock[0]), ())
        restored = MockAutomationSupervisor(self.store, owner, poll_seconds=60, now_provider=lambda: native.clock[0],
            leader_context_source=lambda *_: {'enabled': False},
            expected_implementation_hash=lambda _: None)
        self.addAsyncCleanup(restored.close)
        await restored._restore_once()

        self.assertIn(bundle.account_ref, restored._runners, restored._restore_errors)
        episode = restored._runners[bundle.account_ref].episode_for_spec(original.spec_id)
        async def late_fill_projected():
            while episode._episode_position.state.position_quantity != 1:
                await asyncio.sleep(0.01)
        await asyncio.wait_for(late_fill_projected(), 10)
        self.assertEqual(1, episode._episode_position.state.position_quantity)
        self.assertEqual(admission, data.repository.load_mock_automation_admission_for_spec(bundle.account_ref, original.spec_id))
        control = data.repository.load_mock_automation_control(bundle.account_ref)
        self.assertEqual('STOPPED', control.desired_state.value)
        head = self.store.load_document('execution_mock_plan_positions', bundle.account_ref, buy.intent.plan.plan_id)['document']
        self.assertEqual((original.spec_id, 1), (head['spec_id'], head['quantity']))
        fills = [event for event in native.execution.account_events('mock', bundle.account_ref).events if event.event_type == 'FILL']
        self.assertEqual([('late-after-off', original.spec_id)], [(event.broker_execution_id, event.member_spec_id) for event in fills])
        self.assertFalse(native.runtime._new_orders_enabled)
        self.assertEqual(1, len(native.transport.calls))

    async def test_failed_member_preparation_closes_all_prepared_members_and_can_retry_the_same_admission(self):
        from kiwoom_monitor.central_server.mock_automation_episode import MockAutomationEpisode
        data, native, owner, bundle, supervisor, arguments = public_group_inputs(self)
        prepare = MockAutomationEpisode.prepare_restore
        finish = MockAutomationEpisode.finish_close
        finished = []
        def fail_member(episode):
            if episode.spec_id == data.specs[1].spec_id:
                raise RuntimeError('second member native prepare failed')
            return prepare(episode)
        async def close_member(episode, **kwargs):
            finished.append(episode.spec_id)
            return await finish(episode, **kwargs)
        with patch.object(MockAutomationEpisode, 'prepare_restore', fail_member), \
                patch.object(MockAutomationEpisode, 'finish_close', close_member):
            with self.assertRaisesRegex(RuntimeError, 'second member native prepare failed'):
                await supervisor.activate(**arguments)
        self.assertEqual(set(data.selection.member_spec_ids), set(finished))
        self.assertFalse(supervisor._preparing_runners)
        self.assertNotIn(bundle.account_ref, supervisor._runners)
        self.assertFalse(bundle.monitor.enabled)
        self.assertFalse(native.runtime._new_orders_enabled)
        status = await supervisor.activate(**arguments)
        self.assertEqual(2, len(status['runner']['members']))
        self.assertEqual(1, len(owner.switches))
        self.assertEqual([], native.transport.calls)

    async def test_group_attachment_failure_withdraws_the_listener_and_drains_every_prepared_member(self):
        from kiwoom_monitor.central_server.mock_automation_episode import MockAutomationEpisode
        data, native, owner, bundle, supervisor, arguments = public_group_inputs(self)
        original = bundle.monitor.set_automation_risk_listener
        def fail_after_attach(listener):
            original(listener)
            raise RuntimeError('group listener acknowledgement lost')
        finished = []
        finish = MockAutomationEpisode.finish_close
        async def close_member(episode, **kwargs):
            finished.append(episode.spec_id)
            return await finish(episode, **kwargs)
        with patch.object(bundle.monitor, 'set_automation_risk_listener', side_effect=fail_after_attach), \
                patch.object(MockAutomationEpisode, 'finish_close', close_member):
            with self.assertRaisesRegex(RuntimeError, 'group listener acknowledgement lost'):
                await supervisor.activate(**arguments)
        self.assertEqual(set(data.selection.member_spec_ids), set(finished))
        self.assertIsNone(bundle.monitor.listener)
        self.assertFalse(bundle.monitor.enabled)
        self.assertFalse(supervisor._preparing_runners)
        self.assertNotIn(bundle.account_ref, supervisor._runners)
        self.assertFalse(native.runtime._new_orders_enabled)
        self.assertEqual([], native.transport.calls)
