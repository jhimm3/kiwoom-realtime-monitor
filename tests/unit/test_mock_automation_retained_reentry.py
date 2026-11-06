"""Genuine v3 first entry beside a verified original standalone admission."""
from dataclasses import replace
from datetime import datetime, time, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock


async def verify_genuine_v3_entry_with_standalone_peer(case, factory=None):
    from tests.unit.test_mock_automation_retained_projection import standalone_retained_inputs
    from tests.unit.test_mock_automation_parallel_strategies import reentry_config
    from tests.unit.test_leader_staged_research import staged_final
    from tests.unit.test_mock_automation_specification import MockUnfilledReplacementDispatchTests
    from kiwoom_monitor.application.market_session_schedule import KST
    from kiwoom_monitor.application.mock_automation_candidate import (
        candidate_package_from_dict, eligibility_policy_from_dict, eligibility_receipt_from_dict)
    from kiwoom_monitor.application.mock_automation_specification import (
        build_mock_automation_publication_documents, publish_ready_mock_automation_spec)
    from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
    from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
    from kiwoom_monitor.central_server.mock_runtime import MockAccountRuntimeMode
    from kiwoom_monitor.domain.execution_activation import (ForwardCriteria, LEADER_NATIVE_PLAN_MODE,
        forward_spec_from_dict, mock_automation_spec_from_dict, stage_revision_from_dict)
    from kiwoom_monitor.domain.order_contract import OrderSide

    original = await standalone_retained_inputs(case, factory)
    original_raw = case.store.load_execution_intent(original.buy.intent.intent_id)
    binding = case.store.load_latest_account_binding(case.spec.credential_profile_id, 'kiwoom', 'mock')
    bindings = case.store.load_account_bindings()
    _, request, _, publication, _ = staged_final(case, config=reentry_config())
    day = datetime.fromisoformat(publication['package']['source_final']['finished_at']).astimezone(KST).date()+timedelta(days=1)
    while day.weekday() >= 5: day += timedelta(days=1)
    case.clock = datetime.combine(day, time(15, 17, 2), KST)
    case.runtime.heartbeat()
    config = replace(request.strategy, contexts=(replace(request.strategy.contexts[0],
        available_at=(case.clock-timedelta(minutes=5)).isoformat(),
        valid_until=(case.clock+timedelta(days=1)).isoformat()),))
    monitor = CandidateMonitor(case.store, config.policy, poll_seconds=3600,
        universe_max_age_seconds=180, now_provider=lambda: case.clock)
    case.addAsyncCleanup(monitor.close)
    monitor._leader_accepting = True
    for offset, price in ((-0.001, 999), (0, 1001)):
        at = case.clock+timedelta(seconds=offset)
        monitor.observe_trade(dict(code='005930', market='KRX', trade_time=at.strftime('%H%M%S'),
            current_price=price, cumulative_trade_value=100), at, source='retained-v3-ready:1', approved=True)
    monitor.run_once()
    event, = [row for row in case.store.load_shadow_candidates(0, 1000)['events']
               if row.get('run_id') == monitor.monitor_id]
    case.repository.publish_mock_automation_candidate(candidate_package_from_dict(publication['package']),
        eligibility_policy_from_dict(publication['eligibility_policy']),
        eligibility_receipt_from_dict(publication['eligibility_receipt']),
        credential_profile_id=case.spec.credential_profile_id, expected_binding_revision=binding['binding_revision'])
    names = ('maximum_concurrent_strategies', 'maximum_concurrent_positions', 'maximum_capital_won',
        'maximum_daily_loss_won', 'maximum_data_gap_seconds', 'maximum_submission_unknown_count',
        'maximum_reconnect_count', 'maximum_balance_mismatch_count')
    documents = build_mock_automation_publication_documents(candidate_publication=publication, binding=binding,
        credential_profile_id=case.spec.credential_profile_id, shadow_event=event, evaluation_start=case.clock,
        evaluation_end=case.clock+timedelta(days=7), frozen_at=case.clock,
        criteria=ForwardCriteria(100, 990000, 2000, 1, 0, 1, 0, 2, 0, 5, 10, 0, 100000, 500000),
        limits={name: (10000 if name == 'maximum_capital_won' else getattr(case.spec, name)) for name in names},
        execution_plan_mode=LEADER_NATIVE_PLAN_MODE,
        unfilled_execution_policy=replace(MockUnfilledReplacementDispatchTests.unfilled_execution_policy,
                                          version='mock-unfilled-execution-policy/v2'))
    new_spec = mock_automation_spec_from_dict(documents['operating_spec'])
    ready, _ = publish_ready_mock_automation_spec(case.repository,
        profile=forward_spec_from_dict(documents['forward_profile']),
        stage_revisions=tuple(stage_revision_from_dict(row) for row in documents['stage_revisions']),
        spec=new_spec, shadow_event=event, current_binding=binding)
    case.assertEqual('READY', ready.status.value)
    selection = replace(original.selection, frozen_at=case.clock,
        member_spec_ids=(case.spec.spec_id, new_spec.spec_id),
        execution_order_spec_ids=(case.spec.spec_id, new_spec.spec_id), entry_spec_ids=(new_spec.spec_id,))
    implementation = publication['package']['scientific_implementation_hash']
    case.repository.publish_mock_automation_account_selection(selection,
        expected_implementation_hash=lambda _: implementation)
    live = dict(revision=2, enabled=True, policy=config.policy.to_dict())
    settings = dict(revision=2, shadow_candidate_enabled=True, shadow_candidate_universe_max_age_seconds=180,
        shadow_candidate_configs={case.spec.spec_id: case.live['policy'], new_spec.spec_id: live['policy']})
    case.store.upsert_documents('server_operational_settings', [dict(owner='global', key='current', document=settings)])
    source = lambda spec_id=None: live if spec_id == new_spec.spec_id else case.live
    case.runner.episode._leader_context_source = source
    case.bundle.runtime_mode = MockAccountRuntimeMode.AUTOMATIC
    case.bundle.refresh_automation_control = lambda control: setattr(case.bundle, 'automation_context',
        SimpleNamespace(spec_id=control.active_spec_id, execution_run_id=control.execution_run_id,
                        control_revision=control.control_revision))
    supervisor = MockAutomationSupervisor(case.store,
        SimpleNamespace(bundle=lambda _: case.bundle, require_execution_settings=AsyncMock()),
        poll_seconds=3600, now_provider=lambda: case.clock,
        price_source=case.runner.episode._price_source, leader_context_source=source,
        expected_implementation_hash=lambda _: implementation)
    case.addAsyncCleanup(supervisor.close)
    supervisor._runners[case.runtime.account_ref] = case.runner
    await supervisor.replace_selection(credential_profile_id=case.spec.credential_profile_id,
        account_ref=case.runtime.account_ref, spec_id=case.spec.spec_id, account_selection_id=selection.selection_id,
        expected_control_revision=original.previous.control_revision, expected_settings_revision=1,
        credential_revision=1, reason='explicit synthetic genuine v3 beside retained standalone')
    active = supervisor._runners[case.runtime.account_ref]
    await active.pause_for_replacement()
    for price in (990, 1001):
        case.clock += timedelta(milliseconds=100)
        payload = dict(code='005930', market='KRX', current_price=price,
            trade_time=case.clock.strftime('%H%M%S'), cumulative_trade_value=100)
        for episode in active.episodes:
            episode.observe_leader_trade(payload, case.clock, source='retained-new-v3-0B:1', approved=True)
        await case.bundle.monitor.refresh_recovery()
        for episode in active.episodes:
            await episode.advance_once()
    buys = [record for record in case.execution.active_intents('mock', case.runtime.account_ref, case.runtime.run_id)
            if getattr(record, 'member_spec_id', None) == new_spec.spec_id and record.intent.side is OrderSide.BUY]
    case.assertEqual(1, len(buys), {'new': active.episode_for_spec(new_spec.spec_id).status,
        'calls': case.transport.calls})
    case.assertEqual(2, buys[0].intent.quantity)
    case.assertEqual(original.old_admission, case.repository.load_mock_automation_admission_for_spec(
        case.runtime.account_ref, case.spec.spec_id))
    case.assertEqual(bindings, case.store.load_account_bindings())
    # The raw old source remains standalone; only the verified native view is a peer.
    after = case.store.load_execution_intent(original.buy.intent.intent_id)
    case.assertNotIn('mock_account_member_spec_id', after)
    case.assertEqual(original_raw['plan'], after['plan'])
    case.assertEqual(original_raw['intent_id'], after['intent_id'])
    case.assertEqual(2, case.execution.load_plan_position(case.runtime.account_ref, case.runtime.run_id,
        original.buy.intent.symbol, original.buy.intent.plan).quantity)


class RetainedReentryTests(unittest.IsolatedAsyncioTestCase):
    async def test_genuine_v3_first_entry_treats_only_verified_standalone_admission_as_other_member(self):
        await verify_genuine_v3_entry_with_standalone_peer(self)
