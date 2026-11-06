"""Explicit replacement retains immutable old admissions and the account owner."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


def replacement_for(data, runtime, at, *, limits=None):
    from kiwoom_monitor.domain.execution_activation import (
        MockAutomationAccountSelectionV2, MockAutomationRetainedAdmissionRef)
    previous = data.repository.load_mock_automation_control(runtime.account_ref)
    source = data.specs[-1]
    new_spec = replace(source, maximum_capital_won=source.maximum_capital_won - 1)
    data.repository.save_mock_automation_spec(new_spec)
    refs = tuple(MockAutomationRetainedAdmissionRef(value.spec_id, value.admission_id,
        value.account_selection_id, value.execution_run_id) for value in (
            data.repository.load_mock_automation_admission_for_spec(runtime.account_ref, identity)
            for identity in previous.account_selection.member_spec_ids))
    desired = MockAutomationAccountSelectionV2(account_scope=previous.account_selection.account_scope,
        credential_profile_id=previous.account_selection.credential_profile_id,
        binding_revision=previous.account_selection.binding_revision,
        binding_verified_at=previous.account_selection.binding_verified_at, frozen_at=at,
        member_spec_ids=(*previous.account_selection.member_spec_ids, new_spec.spec_id),
        limits=previous.account_selection.limits if limits is None else limits,
        execution_order_spec_ids=(*previous.account_selection.member_spec_ids, new_spec.spec_id),
        entry_spec_ids=(new_spec.spec_id,), retained_admission_refs=refs)
    data.repository.publish_mock_automation_account_selection(desired,
        expected_implementation_hash=lambda _: data.implementation_hash)
    return previous, desired, new_spec


def prepare_replacement(data, previous, desired):
    from kiwoom_monitor.application.mock_automation_admission import prepare_mock_automation_account_replacement
    return prepare_mock_automation_account_replacement(data.repository, data.owner.runtime,
        account_ref=data.owner.runtime.account_ref, selection_id=desired.selection_id,
        requested_at=data.owner.clock[0], expected_control_revision=previous.control_revision,
        expected_implementation_hash=lambda _: data.implementation_hash)


def verify_native_replacement(case, store, *, stopped=False):
    from tests.unit.test_mock_automation_account_recovery import account_recovery_inputs
    from kiwoom_monitor.application.mock_automation_execution import commit_mock_automation_account_replacement
    from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_account_recovery_from_cut
    from kiwoom_monitor.domain.execution_activation import mock_automation_account_selection_from_dict
    data = account_recovery_inputs(case, store)
    if stopped:
        from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation
        emergency_stop_mock_automation(data.repository, data.owner.runtime, account_ref=data.owner.runtime.account_ref,
            spec_id=data.selection.primary_spec_id, stopped_at=data.owner.clock[0], reason='explicit synthetic pause')
    previous, desired, _ = replacement_for(data, data.owner.runtime, data.owner.clock[0])
    before = tuple(value.to_dict() for value in data.admissions)
    case.assertEqual(desired, mock_automation_account_selection_from_dict(desired.to_dict()))
    case.assertFalse(desired.allows_entry(previous.active_spec_id))
    prepare_replacement(data, previous, desired)
    candidate = replace(previous, account_selection=desired, control_revision=previous.control_revision + 1,
                        changed_at=data.owner.clock[0], reason='explicit synthetic replacement')
    with case.assertRaisesRegex(RuntimeError, 'EXPLICIT_HANDOFF_REQUIRED'):
        data.repository.save_mock_automation_control(candidate, expected_revision=previous.control_revision)
    cut = data.cut()
    values = record_mock_automation_account_recovery_from_cut(data.repository, data.owner.runtime, cut,
        account_ref=data.owner.runtime.account_ref, selection_id=desired.selection_id,
        prospective_selection=desired, expected_control_revision=previous.control_revision)
    case.assertEqual(3, len(values))
    case.assertEqual({desired.selection_id}, {value.account_selection_id for value in values})
    save = data.repository.replace_mock_automation_control
    with patch.object(data.repository, 'replace_mock_automation_control', wraps=save) as cas:
        result = commit_mock_automation_account_replacement(data.repository, data.owner.runtime,
            account_ref=data.owner.runtime.account_ref, selection_id=desired.selection_id,
            requested_at=data.owner.clock[0], expected_control_revision=previous.control_revision,
            reason='explicit synthetic replacement')
        retry = commit_mock_automation_account_replacement(data.repository, data.owner.runtime,
            account_ref=data.owner.runtime.account_ref, selection_id=desired.selection_id,
            requested_at=data.owner.clock[0], expected_control_revision=previous.control_revision,
            reason='retry after response loss')
    case.assertEqual(1, cas.call_count)
    case.assertEqual(result, retry)
    case.assertEqual(previous.control_revision + 1, result.control_revision)
    case.assertEqual(previous.desired_state, result.desired_state)
    case.assertEqual(previous.entry_resume_required, result.entry_resume_required)
    case.assertEqual(before, tuple(data.repository.load_mock_automation_admission_for_spec(
        data.owner.runtime.account_ref, value.spec_id).to_dict() for value in data.admissions))
    case.assertFalse(data.owner.runtime._new_orders_enabled)
    case.assertEqual([], data.owner.transport.calls)
    return data, previous, desired, result


class AccountReplacementTests(unittest.TestCase):
    def setUp(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store = SQLiteQueryStore(root / 'replacement.sqlite3')
        self.store.initialize()
        self.addCleanup(self.store.close)

    def test_explicit_native_cas_preserves_original_admissions_and_is_idempotent(self):
        verify_native_replacement(self, self.store)

    def test_replacing_a_stopped_group_preserves_stop_and_never_resumes_entries(self):
        data, _, _, result = verify_native_replacement(self, self.store, stopped=True)
        self.assertEqual('STOPPED', result.desired_state.value)
        self.assertFalse(data.owner.runtime._new_orders_enabled)

    def test_native_cas_rejects_partial_prospective_recovery_and_pause_state_change(self):
        from tests.unit.test_mock_automation_account_recovery import account_recovery_inputs
        from kiwoom_monitor.application.mock_automation_admission import MockAutomationDesiredState
        from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_account_recovery_from_cut
        data = account_recovery_inputs(self, self.store)
        previous, desired, _ = replacement_for(data, data.owner.runtime, data.owner.clock[0])
        prepare_replacement(data, previous, desired)
        value = replace(previous, account_selection=desired, control_revision=previous.control_revision + 1,
                        changed_at=data.owner.clock[0], reason='explicit synthetic replacement')
        args = dict(expected_revision=previous.control_revision,
            expected_selection_id=previous.account_selection.selection_id, owner_token=data.owner.runtime.owner_token)
        cut = data.cut()
        save_recovery = data.repository.save_mock_automation_recovery_decision
        def interrupted_save(recovered):
            if recovered.spec_id == desired.member_spec_ids[-1]:
                raise OSError('synthetic last member recovery persistence failure')
            return save_recovery(recovered)
        with patch.object(data.repository, 'save_mock_automation_recovery_decision', side_effect=interrupted_save):
            with self.assertRaisesRegex(OSError, 'last member recovery'):
                record_mock_automation_account_recovery_from_cut(data.repository, data.owner.runtime, cut,
                    account_ref=data.owner.runtime.account_ref, prospective_selection=desired,
                    expected_control_revision=previous.control_revision)
        with self.assertRaisesRegex(RuntimeError, 'RESUME_PROOF_MISSING'):
            data.repository.replace_mock_automation_control(value, **args)
        record_mock_automation_account_recovery_from_cut(data.repository, data.owner.runtime, data.cut(),
            account_ref=data.owner.runtime.account_ref, prospective_selection=desired,
            expected_control_revision=previous.control_revision)
        with self.assertRaisesRegex(RuntimeError, 'REPLACEMENT_CONTROL_CHANGED'):
            data.repository.replace_mock_automation_control(
                replace(value, desired_state=MockAutomationDesiredState.STOPPED), **args)
        self.assertEqual(previous, data.repository.load_mock_automation_control(data.owner.runtime.account_ref))


async def verify_actual_group_replacement(case, factory=None, *, lose_commit_response=False, fail_prepare=False):
    from tests.unit.test_mock_automation_parallel_strategies import (
        first_parallel_buys, advance_members, own_async_close, reopen_parallel_runtime, save_bar, deliver_price,
        member_records, fill_order)
    from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
    from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
    from kiwoom_monitor.central_server.mock_runtime import MockAccountRuntimeMode
    data, (leader_buy, close_buy) = await first_parallel_buys(case, factory)
    data.clock[0] += timedelta(milliseconds=1)
    data.book.fill(leader_buy.broker_order_id, 2, 'replacement-original-leader-two')
    await advance_members(data)
    await data.bundle.monitor.refresh_recovery()
    data.runner._detach_inputs()
    original = data.runner
    data.runner = MockAutomationRunner(data.episodes[0], member_episodes=data.episodes[1:],
        monitor=data.bundle.monitor, poll_seconds=3600, execution_order=data.selection.execution_order_spec_ids,
        account_selection=data.selection, borrowed_episodes=original.episodes)
    data.runner.adopt_episodes_from(original)
    data.runner.attach_inputs()
    data.close_runner = own_async_close(case, data.runner.close)
    previous_runner = data.runner
    old_episodes = data.episodes
    old_admissions = tuple(data.repository.load_mock_automation_admission_for_spec(data.runtime.account_ref,
        spec.spec_id).to_dict() for spec in data.specs)
    original_intents = (asdict(leader_buy.intent), asdict(close_buy.intent))
    # Explicit synthetic shared limits cover two retained owners plus the new
    # entry owner. The product never fills these figures on the user's behalf.
    previous, desired, new_spec = replacement_for(data, data.runtime, data.clock[0],
        limits=replace(data.selection.limits, maximum_concurrent_strategies=3, maximum_concurrent_positions=3))
    data.bundle.runtime_mode = MockAccountRuntimeMode.AUTOMATIC
    data.bundle.refresh_automation_control = lambda control: setattr(data.bundle, 'automation_context',
        replace(data.bundle.automation_context, control_revision=control.control_revision))
    owner = SimpleNamespace(bundle=lambda profile: data.bundle if profile == data.selection.credential_profile_id else None,
                            require_execution_settings=AsyncMock())
    supervisor = MockAutomationSupervisor(data.store, owner, poll_seconds=3600,
        now_provider=lambda: data.clock[0], price_source=lambda symbol: data.quotes.get(symbol),
        leader_context_source=lambda spec_id=None: data.live if spec_id == data.specs[0].spec_id else None,
        expected_implementation_hash=lambda _: data.implementation_hash)
    case.addAsyncCleanup(supervisor.close)
    supervisor._runners[data.runtime.account_ref] = previous_runner
    args = dict(credential_profile_id=data.selection.credential_profile_id, account_ref=data.runtime.account_ref,
        spec_id=data.selection.primary_spec_id, account_selection_id=desired.selection_id,
        expected_control_revision=previous.control_revision, expected_settings_revision=1, credential_revision=1,
        reason='explicit synthetic new spec; retain original plans')
    if fail_prepare:
        from kiwoom_monitor.central_server.mock_automation_episode import MockAutomationEpisode
        restore = MockAutomationEpisode.prepare_restore
        def reject_new(episode):
            if episode.spec_id == new_spec.spec_id:
                raise RuntimeError('synthetic new member prepare failure')
            return restore(episode)
        with patch.object(MockAutomationEpisode, 'prepare_restore', reject_new):
            with case.assertRaisesRegex(RuntimeError, 'new member prepare failure'):
                await supervisor.replace_selection(**args)
        case.assertIs(previous_runner, supervisor._runners[data.runtime.account_ref])
        case.assertTrue(all(not value.closing for value in old_episodes))
        case.assertEqual(previous, data.repository.load_mock_automation_control(data.runtime.account_ref))
        case.assertEqual({data.selection.selection_id}, {data.repository.load_latest_mock_automation_recovery(
            data.runtime.account_ref, row['admission_id']).account_selection_id for row in old_admissions})
        return data
    save = data.store.replace_mock_automation_control
    def commit_then_lose(*values, **kwargs):
        result = save(*values, **kwargs)
        if lose_commit_response:
            raise OSError('synthetic durable replacement acknowledgement loss')
        return result
    with patch.object(data.store, 'replace_mock_automation_control', side_effect=commit_then_lose) as cas:
        result = await supervisor.replace_selection(**args)
        case.assertEqual(1, cas.call_count)
        retry = await supervisor.replace_selection(**args)
        case.assertEqual(1, cas.call_count)
    case.assertEqual(result['control'], retry['control'])
    runner = supervisor._runners[data.runtime.account_ref]
    await runner.pause_for_replacement()
    case.assertIsNot(previous_runner, runner)
    case.assertEqual(tuple(old_episodes), runner.episodes[:2])
    case.assertTrue(all(not value.closing for value in old_episodes))
    case.assertIs(data.runtime, runner.episode._bundle.runtime)
    case.assertEqual(desired.selection_id, result['runner']['account_selection_id'])
    case.assertEqual(list(desired.entry_spec_ids), result['runner']['entry_spec_ids'])
    case.assertEqual(old_admissions, tuple(data.repository.load_mock_automation_admission_for_spec(
        data.runtime.account_ref, spec.spec_id).to_dict() for spec in data.specs))
    case.assertEqual(original_intents, tuple(asdict(data.execution.load(value.intent_id).intent)
                                           for value in (leader_buy.intent, close_buy.intent)))
    data.runner, data.episodes = runner, runner.episodes
    # A late detailed FILL is attached to the original accepted physical order,
    # after entry ownership was switched; no row or event is relabelled.
    data.clock[0] += timedelta(milliseconds=1)
    data.book.fill(close_buy.broker_order_id, 1, 'replacement-old-close-late-one')
    await advance_members(data)
    case.assertEqual(1, old_episodes[1]._episode_position.state.position_quantity)
    case.assertIsNone(runner.episodes[2]._episode_position.state.position_quantity)
    case.assertEqual(2, old_episodes[0]._episode_position.state.position_quantity)
    case.assertEqual(2, len(data.transport.calls), 'no default policy is created for the new spec')
    for episode in runner.episodes:
        episode._save_checkpoint(force=True)
    await supervisor.close()
    data.selection, data.specs = desired, (*data.specs, new_spec)
    await reopen_parallel_runtime(case, data)
    case.assertEqual([2, 1, None], [episode._episode_position.state.position_quantity for episode in data.episodes])
    case.assertEqual(desired, data.repository.load_mock_automation_control(data.runtime.account_ref).account_selection)
    case.assertEqual(2, len(data.transport.calls))
    # The replacement itself created no current policy. Now explicitly save
    # the existing, independently verified CLOSE policy for the new spec and
    # supply a later genuine minute/0B observation through the same producers.
    from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
    await data.close_shadow()
    data.settings = {**data.settings, 'revision': data.settings['revision'] + 1,
        'shadow_candidate_configs': {**data.settings['shadow_candidate_configs'], new_spec.spec_id: data.minute_config}}
    data.store.upsert_documents('server_operational_settings',
        [dict(owner='global', key='current', document=data.settings)])
    data.shadow = CandidateMonitor.from_settings(data.store, data.settings, poll_seconds=3600,
        universe_max_age_seconds=180, now_provider=lambda: data.clock[0])
    data.close_shadow = own_async_close(case, data.shadow.close)
    data.shadow.run_once()  # Restore the existing checkpoint before supplying the new live source.
    from datetime import datetime
    data.clock[0] += timedelta(minutes=1)
    start = datetime.fromisoformat(data.example.signal.bar_start) + timedelta(minutes=2)
    frame = replace(data.example.signal, revision_id='replacement-later-close-minute',
        bar_start=start.isoformat(), bar_end=(start + timedelta(minutes=1)).isoformat(),
        available_at=data.clock[0].isoformat(), open=1030, high=1030, low=1030, close=1030)
    save_bar(data.store, frame, 'replacement-later-close-minute')
    await deliver_price(data, 1030, source='replacement-explicit-new-policy')
    buys = member_records(data, 2, side='BUY')
    case.assertEqual(1, len(buys), {'members': [episode.status for episode in data.episodes],
                                 'source': data.shadow.quality})
    case.assertEqual((new_spec.spec_id, data.run_id, 4),
                     (buys[0].member_spec_id, buys[0].intent.run_id, buys[0].intent.quantity))
    await fill_order(data, buys[0], 4, 'replacement-explicit-new-four')
    case.assertEqual([2, 1, 4], [episode._episode_position.state.position_quantity for episode in data.episodes])
    case.assertEqual(3, len(data.transport.calls), 'only the explicitly configured new member submits')
    await deliver_price(data, 940, source='replacement-original-leader-stop')
    await deliver_price(data, 960, source='replacement-original-leader-stop-later-quote')
    await advance_members(data)
    exits = member_records(data, 0, side='SELL')
    case.assertEqual(1, len(exits), [episode.status for episode in data.episodes])
    case.assertEqual((2, leader_buy.intent.plan.plan_id), (exits[0].intent.quantity, exits[0].intent.plan.plan_id))
    await fill_order(data, exits[0], 2, 'replacement-original-leader-protection-two')
    case.assertIsNone(data.episodes[0]._episode_position.state.position_quantity)
    case.assertEqual([1, 4], [episode._episode_position.state.position_quantity for episode in data.episodes[1:]])
    case.assertEqual(0, data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
        leader_buy.intent.symbol, leader_buy.intent.plan).quantity)
    case.assertEqual(4, len(data.transport.calls))
    return data


class ActualAccountReplacementTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_old_plans_and_late_fill_survive_explicit_replacement_response_loss_and_db_reopen(self):
        await verify_actual_group_replacement(self, lose_commit_response=True)

    async def test_failed_new_member_prepare_keeps_original_episode_and_restores_old_recovery(self):
        await verify_actual_group_replacement(self, fail_prepare=True)


class ReplacementPresentationTests(unittest.TestCase):
    def test_standalone_editor_retains_exact_null_original_selection_and_requires_explicit_limits(self):
        from PySide6.QtWidgets import QApplication
        from tests.unit.test_mock_automation_dialog import account_selection_documents, PROFILE, FakeContent
        from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationAccountSelectionDialog
        from kiwoom_monitor.domain.execution_activation import mock_automation_account_selection_from_dict
        self.app = QApplication.instance() or QApplication([])
        saved, specs = account_selection_documents()
        binding = FakeContent().load_mock_automation_candidates(PROFILE['account_ref'],
            credential_profile_id=PROFILE['profile_id'])['binding']
        original, new = saved['member_spec_ids']
        ref = dict(spec_id=original, admission_id='mock_automation_admission_' + 'a' * 64,
                   original_selection_id=None, execution_run_id='mock_auto_run_' + 'd' * 64)
        from datetime import datetime
        editor = MockAutomationAccountSelectionDialog(specs, binding, PROFILE['profile_id'],
            now_provider=lambda: datetime.fromisoformat(saved['frozen_at']) + timedelta(seconds=1),
            original_anchor=original, admission_refs=[ref])
        self.addCleanup(editor.deleteLater)
        self.assertTrue(all(not value.text() for value in editor._limit_inputs.values()))
        self.assertTrue(all(not check.isChecked() for check, _ in editor._member_inputs.values()))
        editor._member_inputs[original][1].setText('1')
        editor._member_inputs[new][1].setText('2')
        editor._member_inputs[new][0].setChecked(True)
        with self.assertRaisesRegex(ValueError, '공통 한도'):
            editor.publication_values()
        for name, field in editor._limit_inputs.items():
            field.setText(str(saved['limits'][name]))
        desired = mock_automation_account_selection_from_dict(editor.publication_values()['selection'])
        self.assertEqual((new,), desired.entry_spec_ids)
        self.assertEqual(original, desired.primary_spec_id)
        self.assertEqual(ref, desired.retained_admission_refs[0].to_dict())

    def test_editor_keeps_original_anchor_and_off_members_without_inventing_a_new_policy(self):
        from PySide6.QtWidgets import QApplication
        from tests.unit.test_mock_automation_dialog import account_selection_documents, PROFILE, FakeContent
        from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationAccountSelectionDialog
        from kiwoom_monitor.domain.execution_activation import mock_automation_account_selection_from_dict
        self.app = QApplication.instance() or QApplication([])
        selection, specs = account_selection_documents()
        binding = FakeContent().load_mock_automation_candidates(PROFILE['account_ref'],
            credential_profile_id=PROFILE['profile_id'])['binding']
        new_id = 'mock_automation_spec_' + 'c' * 64
        specs = deepcopy(specs)
        specs.append({'spec': {**specs[-1]['spec'], 'spec_id': new_id}, 'readiness': 'READY'})
        specs[1]['readiness'] = 'BLOCKED'
        refs = [dict(spec_id=identity, admission_id='mock_automation_admission_' + letter * 64,
            original_selection_id=selection['selection_id'], execution_run_id='mock_auto_run_' + 'd' * 64)
            for identity, letter in zip(selection['member_spec_ids'], ('a', 'b'), strict=True)]
        from datetime import datetime
        editor = MockAutomationAccountSelectionDialog(specs, binding, PROFILE['profile_id'],
            now_provider=lambda: datetime.fromisoformat(selection['frozen_at']) + timedelta(seconds=1),
            selection=selection, current_selection=selection, admission_refs=refs)
        self.addCleanup(editor.deleteLater)
        self.assertFalse(editor._anchor.isEnabled())
        self.assertEqual(selection['member_spec_ids'][0], editor._anchor.currentData())
        editor._member_inputs[selection['member_spec_ids'][0]][0].setChecked(False)
        editor._member_inputs[new_id][0].setChecked(True)
        editor._member_inputs[new_id][1].setText('3')
        values = editor.publication_values()
        desired = mock_automation_account_selection_from_dict(values['selection'])
        self.assertEqual((new_id,), desired.entry_spec_ids)
        self.assertEqual(tuple(selection['member_spec_ids']), tuple(ref.spec_id for ref in desired.retained_admission_refs))
        self.assertEqual(selection['limits'], desired.limits.to_dict())
        self.assertEqual('mock_automation_account_selection/v2', desired.version)
        self.assertEqual({'account_ref', 'credential_profile_id', 'expected_binding_revision', 'selection'}, set(values))

    def test_client_replacement_has_explicit_current_revision_and_does_not_reuse_start(self):
        from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
        client = CentralContentClient('http://example.invalid', 'synthetic-token')
        values = dict(account_ref='synthetic-account', credential_profile_id='synthetic-profile', spec_id='original-anchor',
            account_selection_id='explicit-selection', expected_control_revision=4, expected_settings_revision=7,
            credential_revision=2, reason='explicit replacement')
        with patch.object(client, '_request', return_value={'saved': True}) as request:
            self.assertEqual({'saved': True}, client.replace_selection_mock_automation(**values))
        request.assert_called_once_with('POST', '/api/v1/mock-automation/replace-selection', values)
        with patch.object(client, '_request', return_value={'specs': []}) as request:
            client.load_mock_automation_specs('account/ref')
            client.load_mock_automation_specs('account/ref', include_admission_refs=True)
        self.assertEqual([('GET', '/api/v1/research/mock-automation-specs/account%2Fref'),
                          ('GET', '/api/v1/research/mock-automation-specs/account%2Fref?include_admission_refs=true')],
                         [call.args for call in request.call_args_list])


class ReplacementRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_route_delivers_exact_replacement_and_rejects_extra_or_missing_revision(self):
        import httpx
        from fastapi import FastAPI, HTTPException, Request
        from kiwoom_monitor.central_server.mock_automation_routes import create_mock_automation_router
        operation = AsyncMock(return_value={'control': {'control_revision': 5}})
        supervisor = SimpleNamespace(replace_selection=operation)
        async def authorize(request: Request):
            if request.headers.get('Authorization') != 'Bearer synthetic-route-token':
                raise HTTPException(401, 'unauthorized')
        app = FastAPI()
        app.include_router(create_mock_automation_router(supervisor, authorize))
        values = dict(account_ref='22222222-2222-2222-2222-222222222222', credential_profile_id='synthetic-profile',
            spec_id='original-anchor', account_selection_id='explicit-selection', expected_control_revision=4,
            expected_settings_revision=7, credential_revision=2, reason='explicit replacement')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/v1/mock-automation/replace-selection', json=values)
            self.assertEqual(401, response.status_code)
            operation.assert_not_awaited()
            headers = {'Authorization': 'Bearer synthetic-route-token'}
            for malformed in ({**values, 'extra': True}, {key: item for key, item in values.items()
                                                       if key != 'expected_control_revision'}):
                response = await client.post('/api/v1/mock-automation/replace-selection', json=malformed, headers=headers)
                self.assertEqual(422, response.status_code)
                operation.assert_not_awaited()
            response = await client.post('/api/v1/mock-automation/replace-selection', json=values, headers=headers)
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual({'control': {'control_revision': 5}}, response.json())
        operation.assert_awaited_once_with(**values)
