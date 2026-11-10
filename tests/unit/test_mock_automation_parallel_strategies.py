"""Real final/READY leader and CLOSE members share one native account owner.

The quantities, capital limits, execution order and market/calendar observations
below are explicit synthetic test inputs. No fixture manufactures eligibility,
an execution approval, a native FILL, or a second account runtime.
"""
from dataclasses import asdict, replace
from datetime import date, datetime, time, timedelta
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


def close_final_inputs(case):
    """Run the existing frozen minute engine before publishing its actual final."""
    from tests.unit.test_research_final_preparation import FinalPreparationTests
    from tests.unit.test_research_bundle_execution import child, rows_for, PROFILE, KST
    from tests.unit.test_close_and_ipo import CloseAndIpoTests
    from kiwoom_monitor import research_process as rp
    from kiwoom_monitor.application.mock_automation_candidate import MockAutomationEligibilityPolicy
    from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
    from kiwoom_monitor.infrastructure.research_data_source import load_research_input, write_frozen_research_bundle
    from tests.unit.test_mock_automation_specification import ACCOUNT_REF

    fixture = FinalPreparationTests()
    fixture.setUp()
    case.addCleanup(fixture.doCleanups)
    root = fixture.root / 'parallel-close-final'
    root.mkdir()
    source = root / 'source'
    example = CloseAndIpoTests()
    example.symbol, example.friday, example.tuesday = '005930', date(2026, 9, 18), date(2026, 9, 21)
    example.published = datetime.combine(example.friday, time(8), KST)
    example.setUp()
    example.calendar = tuple(day for day in example.calendar[:-1]
                             if day.trading_date < example.tuesday) + (example.calendar[-1],)
    example.close = replace(example.close, entry_window_minutes=10, adverse_gap_bps=500,
                            protection_loss_bps=1000)
    paths = []
    for day, values in ((example.friday, (('15:14', 100), ('15:15', 100), ('15:16', 105),
                                        ('15:17', 105), ('15:18', 106), ('15:19', 106), ('15:29', 110))),
                        (example.tuesday, (('10:00', 103), ('10:01', 102), ('10:02', 89), ('10:03', 89)))):
        rows = rows_for(day, len(values))
        first = datetime.combine(day, time(8, 58), example.published.tzinfo).isoformat()
        rows[0].update(available_at=first, observation_key=first)
        for row, (clock, price) in zip(rows[1:], values, strict=True):
            start = datetime.fromisoformat(f'{day}T{clock}:00+09:00')
            end = start + timedelta(minutes=1)
            row.update(observation_key=start.isoformat(), available_at=end.isoformat())
            row['payload'].update(bar_start=start.isoformat(), bar_end=end.isoformat(),
                                  open=price, high=price, low=price, close=price)
        paths.append(child(source, day, rows))
    write_frozen_research_bundle(source, paths)
    manifest = load_research_input(source, session_profile=PROFILE).manifest
    references = root / 'calendar.json'
    document = {'version': 'strategy-reference/v1',
                'calendar': [asdict(day) for day in example.calendar], 'listings': []}
    # The frozen source has an actual closing-auction minute at 15:29.
    document['calendar'][0]['sessions'][1]['starts_at'] = datetime.combine(
        example.friday, time(15, 29), example.published.tzinfo)
    encoded = json.dumps(document, default=lambda value: value.isoformat()).encode()
    references.write_bytes(encoded)
    request_doc = json.loads((fixture.root / 'request.json').read_text(encoding='utf-8'))
    request_doc.pop('development_partition')
    request_doc.update(family='close_betting/v1', dataset=str(source), database=str(root / 'final.sqlite3'),
        runs_dir=str(root / 'runs'), strategy_references={'path': str(references), 'sha256': hashlib.sha256(encoded).hexdigest()},
        strategy={'capital_won': 100_000, 'policy': example.close.to_dict()})
    request_doc['execution'].update(version='completed_close_then_next_open/v1', same_bar_path_version='no_intrabar_fill/v1')
    request_doc['execution']['cost_model'].update(commission_bps=0, sell_tax_bps=0, slippage_bps=0,
        valid_from='2026-09-18T08:00:00+09:00', valid_to='2026-09-21T16:00:00+09:00')
    request_doc['evaluation']['folds'] = [dict(name='final', role='OOS',
        start='2026-09-18T09:00:00+09:00', end='2026-09-21T16:00:00+09:00')]
    request_doc['evaluation'].update(minimum_closed_trades=1, minimum_active_days=1)
    request = rp.research_process_request_from_document(request_doc, root)
    implementation = rp.research_implementation_hash(PROFILE)
    candidate_hash = rp.final_candidate_spec_hash(request, implementation)
    # Freeze the explicit software-test criteria before evaluating the source.
    policy = MockAutomationEligibilityPolicy('parallel-close-producer', candidate_hash,
        example.published, 1, 1, -100, 1_000_000)
    batch = rp.FinalHoldoutBatchSpec('final_holdout_batch/v1', manifest['dataset_id'],
        manifest['revision_ids_hash'], PROFILE, (candidate_hash,), request.evaluation)
    frozen = rp.prepare_final_holdout_evaluation(batch, (request,), request_id='parallel-close-final',
        accessed_at='2026-09-21T17:00:00+09:00')
    result, = rp.execute_final_holdout_evaluation(frozen, owner_token='parallel-close-final-owner')['candidates']
    case.assertEqual('COMPLETED', result['state'], result)
    repository = ResearchRepository(request.database)
    fills = [row for row in repository.load_execution_events(result['run_id']) if row['event_type'] == 'FILL']
    case.assertEqual([('BUY', 4, 105), ('SELL', 4, 102)],
                     [(row['side'], row['quantity'], row['price']) for row in fills])
    publication = rp.prepare_mock_automation_candidate_publication(repository, request,
        implementation_hash=implementation, strategy_ref=policy.strategy_ref, account_ref=ACCOUNT_REF,
        final_batch_id=batch.batch_id, final_run_id=result['run_id'], eligibility_policy=policy)
    case.assertEqual('ELIGIBLE', publication['eligibility_receipt']['status'])
    case.assertEqual(result['logical_result_hash'], publication['package']['source_final']['result_hash'])
    case.assertEqual(encoded, references.read_bytes())
    return SimpleNamespace(request=request, publication=publication, result=result)


def save_bar(store, frame, identity):
    start = datetime.fromisoformat(frame.bar_start)
    key = dict(trading_date=start.date().isoformat(), minute=start.strftime('%H:%M'), code=frame.code, market='KRX')
    store.save_minute_bars([{**key, 'open': frame.open, 'high': frame.high, 'low': frame.low,
        'close': frame.close, 'volume': frame.volume, 'trade_value_million_won': frame.trade_value_million_won,
        'updated_at': start.timestamp(), 'operation_id': identity}])
    store.finalize_minute_bars([{**key, 'available_at': datetime.fromisoformat(frame.available_at).timestamp(),
        'capture_quality': 'complete', 'finalization_source': 'timer', 'operation_id': identity + '-final'}])


def own_async_close(case, close):
    done = False
    async def finish():
        nonlocal done
        if not done:
            await close()
            done = True
    case.addAsyncCleanup(finish)
    return finish


def reentry_config():
    from tests.unit.test_leader_position_steps import staged_config
    from kiwoom_monitor.domain.strategy_contract import LeaderBreakoutConfig, LEADER_REENTRY_RULE
    raw = staged_config(reduce_quantity=2).to_dict()
    raw['execution_contract'].update(version='leader_breakout_execution/v3', reentry_rule=LEADER_REENTRY_RULE)
    return LeaderBreakoutConfig.from_dict(raw)


def parallel_ready_inputs(case, factory=None, *, shared_capital=20_000, reentry=False,
                          venues=('KRX', 'KRX'), close_symbol='005930'):
    from tests.unit.test_leader_staged_research import staged_final
    from tests.unit.test_close_and_ipo import CloseAndIpoTests
    from tests.unit.test_mock_automation_specification import ACCOUNT_REF, PROFILE_ID, MockUnfilledReplacementDispatchTests
    from kiwoom_monitor.application.market_session_schedule import KST, session_window_at
    from kiwoom_monitor.application.mock_automation_candidate import candidate_package_from_dict, eligibility_policy_from_dict, eligibility_receipt_from_dict
    from kiwoom_monitor.application.mock_automation_specification import build_mock_automation_publication_documents, publish_ready_mock_automation_spec
    from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
    from kiwoom_monitor.central_server.database import SQLiteQueryStore
    from kiwoom_monitor.domain.execution_activation import (ForwardCriteria, LEADER_NATIVE_PLAN_MODE, CLOSE_NATIVE_PLAN_MODE,
        MockAutomationAccountLimits, MockAutomationAccountSelection, forward_spec_from_dict,
        mock_automation_spec_from_dict, stage_revision_from_dict)
    from kiwoom_monitor.domain.strategy_contract import MINUTE_SHADOW_VERSION
    from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
    from tests.unit.test_close_order_management import management_terms

    _, leader_request, leader_result, leader_publication, _ = staged_final(case,
        config=reentry_config() if reentry else None)
    close = close_final_inputs(case)
    publications = (leader_publication, close.publication)
    finished = max(datetime.fromisoformat(row['package']['source_final']['finished_at']).astimezone(KST)
                   for row in publications)
    day = finished.date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    cut = datetime.combine(day, time(15, 17, 2), KST)
    clock = [cut]
    if factory is None:
        root = Path(case.enterContext(tempfile.TemporaryDirectory()))
        factory = lambda: SQLiteQueryStore(root / 'parallel.sqlite3')
    store = factory()
    store.initialize()
    case.addCleanup(store.close)
    case.enterContext(patch.object(store, '_execution_wall_now', lambda: clock[0], create=True))
    store.register_account_identity(dict(broker='kiwoom', environment='mock', identity_fingerprint='c' * 64,
        account_ref=ACCOUNT_REF, created_at=(cut - timedelta(days=1)).isoformat()))
    binding = store.append_account_binding(dict(credential_profile_id=PROFILE_ID, broker='kiwoom', environment='mock',
        account_ref=ACCOUNT_REF, verified_at=(cut - timedelta(hours=1)).isoformat(), verification_method='ka00001'))
    repository = ForwardEvaluationRepository(store)
    leader = replace(leader_request.strategy, contexts=(replace(leader_request.strategy.contexts[0],
        available_at=(cut - timedelta(minutes=5)).isoformat(), valid_until=(cut + timedelta(days=1)).isoformat()),))
    leader_monitor = CandidateMonitor(store, leader.policy, poll_seconds=1, universe_max_age_seconds=180,
                                     now_provider=lambda: clock[0])
    leader_monitor._leader_accepting = True
    for offset, price in ((-0.001, 999), (0, 1001)):
        at = cut + timedelta(seconds=offset)
        leader_monitor.observe_trade(dict(code='005930', market='KRX', trade_time=at.strftime('%H%M%S'),
            current_price=price, cumulative_trade_value=100), at, source='parallel-ready-0B', approved=True)
    leader_monitor.run_once()
    leader_event, = [event for event in store.load_shadow_candidates(0, 100)['events']
                     if event['strategy_id'] == 'leader_breakout']
    example = CloseAndIpoTests()
    example.symbol, example.friday, example.tuesday = close_symbol, day, day + timedelta(days=1)
    example.published = cut - timedelta(hours=7)
    example.setUp()
    example.calendar = (example.calendar[0], example.calendar[-1])
    example.close = close.request.strategy.policy
    example.entry = session_window_at(cut, venue='KRX')
    example.calendar = (replace(example.calendar[0], sessions=(example.entry, example.auction)), *example.calendar[1:])
    example.prior = tuple(replace(frame, open=price, high=price, low=price, close=price,
        schedule_version=example.entry.schedule_version) for frame, price in zip(example.prior, (950, 960), strict=True))
    example.signal = replace(example.signal, open=1001, high=1005, low=1001, close=1001,
                             schedule_version=example.entry.schedule_version)
    minute_config = json.loads(json.dumps(dict(version=MINUTE_SHADOW_VERSION, family='close_betting/v1',
        signal_valid_seconds=30, symbols=[close_symbol], strategy=close.request.strategy.to_dict(),
        references=dict(version='strategy-reference/v1', calendar=[asdict(day) for day in example.calendar], listings=[])),
        default=lambda value: value.isoformat()))
    minute_monitor = CandidateMonitor.from_json(store, json.dumps(minute_config), poll_seconds=1,
        universe_max_age_seconds=180, now_provider=lambda: clock[0])
    minute_monitor.run_once()
    for index, frame in enumerate((*example.prior, example.signal)):
        save_bar(store, frame, f'parallel-ready-{index}')
    minute_monitor.run_once()
    close_event, = [event for event in store.load_shadow_candidates(0, 100)['events']
                    if event['strategy_id'] == 'close_betting']
    limits = dict(maximum_concurrent_strategies=2, maximum_concurrent_positions=2,
        maximum_capital_won=100_000, maximum_daily_loss_won=3000, maximum_data_gap_seconds=5,
        maximum_submission_unknown_count=0, maximum_reconnect_count=2, maximum_balance_mismatch_count=0)
    criteria = ForwardCriteria(100, 990_000, 2000, 1, 0, 1, 0, 2, 0, 5, 10, 0, 100_000, 500_000)
    specs = []
    for index, (publication, event) in enumerate(zip(publications, (leader_event, close_event), strict=True)):
        package = candidate_package_from_dict(publication['package'])
        repository.publish_mock_automation_candidate(package, eligibility_policy_from_dict(publication['eligibility_policy']),
            eligibility_receipt_from_dict(publication['eligibility_receipt']), credential_profile_id=PROFILE_ID,
            expected_binding_revision=binding['binding_revision'])
        terms = (dict(execution_plan_mode=LEADER_NATIVE_PLAN_MODE,
            unfilled_execution_policy=replace(MockUnfilledReplacementDispatchTests.unfilled_execution_policy,
                                              version='mock-unfilled-execution-policy/v2')) if index == 0 else
            dict(execution_plan_mode=CLOSE_NATIVE_PLAN_MODE, close_execution_policy=management_terms()))
        documents = build_mock_automation_publication_documents(candidate_publication=publication, binding=binding,
            credential_profile_id=PROFILE_ID, shadow_event=event, evaluation_start=cut, evaluation_end=cut + timedelta(days=7),
            frozen_at=cut, criteria=criteria, supported_venue=venues[index], limits={**limits, 'maximum_concurrent_strategies': 1,
                'maximum_concurrent_positions': 1, 'maximum_capital_won': 10_000 if index == 0 else 100_000}, **terms)
        spec = mock_automation_spec_from_dict(documents['operating_spec'])
        ready, _ = publish_ready_mock_automation_spec(repository, profile=forward_spec_from_dict(documents['forward_profile']),
            stage_revisions=tuple(stage_revision_from_dict(value) for value in documents['stage_revisions']),
            spec=spec, shadow_event=event, current_binding=binding)
        case.assertEqual('READY', ready.status.value)
        specs.append(spec)
    selection = MockAutomationAccountSelection(specs[0].account_scope, PROFILE_ID, binding['binding_revision'],
        datetime.fromisoformat(binding['verified_at']), cut, tuple(spec.spec_id for spec in specs),
        MockAutomationAccountLimits(**{**limits, 'maximum_capital_won': shared_capital}),
        execution_order_spec_ids=tuple(spec.spec_id for spec in specs))
    implementation = leader_publication['package']['scientific_implementation_hash']
    case.assertEqual(implementation, close.publication['package']['scientific_implementation_hash'])
    repository.publish_mock_automation_account_selection(selection, expected_implementation_hash=lambda _: implementation)
    return SimpleNamespace(store=store, factory=factory, repository=repository, binding=binding, clock=clock, specs=tuple(specs),
        selection=selection, implementation_hash=implementation, leader_config=leader, minute_config=minute_config,
        leader_monitor=leader_monitor, minute_monitor=minute_monitor, example=example, publications=publications,
        finals=(leader_result, close.result), venues=venues)


async def parallel_runtime(case, factory=None, *, shared_capital=20_000, reentry=False):
    data = parallel_ready_inputs(case, factory, shared_capital=shared_capital, reentry=reentry)
    # Historical READY seeds stay behind each restored member's cursor.
    data.clock[0] += timedelta(minutes=1)
    return await assemble_parallel_runtime(case, data, admit=True)


async def assemble_parallel_runtime(case, data, *, admit):
    from tests.unit.test_mock_automation_specification import _FakeBrokerTransport
    from tests.unit.test_close_order_management import BrokerBook
    from kiwoom_monitor.application.mock_automation_admission import admit_mock_automation_account_selection, execution_run_id_for_spec
    from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
    from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
    from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime, ManualMockOrderGateway
    from kiwoom_monitor.central_server.mock_account_monitor import MockAccountMonitor
    from kiwoom_monitor.central_server.mock_automation_episode import MockAutomationEpisode
    from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
    from kiwoom_monitor.central_server.mock_runtime import MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION, MockAutomationRuntimeContext
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository

    data.execution = ExecutionRepository(data.store)
    from kiwoom_monitor.domain.order_contract import OrderIntent, OrderSide, OrderType
    past = data.clock[0] - timedelta(minutes=10)
    # Prepare independent old manual evidence. The scenario restores these
    # facts after initial entries, preserving the original flat-entry contract.
    if not hasattr(data, 'manual'):
        data.manual = OrderIntent('parallel-archived-manual', 'parallel-independent-manual-run', 'manual-original-decision',
            data.selection.account_scope.account_ref, 'mock', '005930', 'KRX', OrderSide.BUY, 4, OrderType.LIMIT,
            1000, past, past + timedelta(minutes=1), 'archived-manual-fact/v1')
        data.transport = _FakeBrokerTransport(lambda: data.clock[0])
        data.original_submit = data.transport.submit
    data.run_id = execution_run_id_for_spec(data.selection.primary_spec_id)
    data.owner_generation = getattr(data, 'owner_generation', 0) + 1
    local_venue = any(venue in {'NXT', 'SOR'} for venue in data.venues)
    transport = data.transport
    if local_venue:
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import LocalAutomationOrderTransport
        transport = LocalAutomationOrderTransport(SimpleNamespace(request_once=lambda *args: data.request_once(*args)),
                                                 now_provider=lambda: data.clock[0])
    data.runtime = ExecutionRuntime(OrderLifecycle(data.execution, transport, now_provider=lambda: data.clock[0],
        local_automation_order_rejection=(lambda intent, now: data.route_rejection(intent, now)) if local_venue else None),
        data.execution, account_ref=data.selection.account_scope.account_ref, run_id=data.run_id,
        owner_token='one-leader-close-native-owner:' + str(data.owner_generation), now_provider=lambda: data.clock[0])
    case.addCleanup(data.runtime.stop)
    if admit:
        admissions, _, control = admit_mock_automation_account_selection(data.repository, data.runtime,
            account_ref=data.runtime.account_ref, selection_id=data.selection.selection_id, requested_at=data.clock[0],
            expected_control_revision=0, expected_implementation_hash=lambda _: data.implementation_hash)
    else:
        data.runtime.start(new_orders_enabled=False)
        admissions = tuple(data.repository.load_mock_automation_admission_for_spec(data.runtime.account_ref, spec.spec_id)
                           for spec in data.specs)
        control = data.repository.load_mock_automation_control(data.runtime.account_ref)
    context = SimpleNamespace(clock=data.clock, runtime=data.runtime, execution=data.execution,
                              buy=SimpleNamespace(intent=SimpleNamespace(symbol='005930')))

    class AccountBook(BrokerBook):
        async def read(self):
            recovery = await super().read()
            # The current open-order window omits the earlier terminal manual
            # order; its detailed native FILL, daily costs and balance remain.
            return replace(recovery,
                orders=tuple(row for row in recovery.orders if row.broker_order_id != 'parallel-archived-manual-order'),
                order_details=tuple(row for row in recovery.order_details if row.broker_order_id != 'parallel-archived-manual-order'))

        def cancel(self, intent, number, quantity=0):
            event = data.execution.events(intent.intent_id)[-1]
            self.context.management_field, = [key for key in ('leader_order_management_claim', 'close_order_management_claim')
                                        if key in event]
            return super().cancel(intent, number, quantity)

        def amend(self, intent, number, quantity, price):
            from kiwoom_monitor.domain.order_contract import OrderState
            try:
                return super().amend(intent, number, quantity, price)
            finally:
                # The broker's accepted transfer closes this physical parent's
                # residual; its original detailed fills stay on that parent.
                if self.rows[number]['remaining'] == 0:
                    self.rows[number]['state'] = OrderState.CANCELLED

    if not hasattr(data, 'book'):
        data.book = AccountBook(context, empty=True)
    else:
        data.book.context = context
    def broker_submit(intent):
        result = data.original_submit(intent)
        data.book.add(result.broker_order_id, intent)
        return result
    case.enterContext(patch.object(data.transport, 'submit', side_effect=broker_submit))
    case.enterContext(patch.object(data.transport, 'cancel', side_effect=data.book.cancel))
    case.enterContext(patch.object(data.transport, 'amend', side_effect=data.book.amend, create=True))
    if local_venue:
        from kiwoom_monitor.domain.order_contract import OrderSide, OrderState
        from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_broker_venue
        data.wire = getattr(data, 'wire', [])

        def request_once(api_id, path, body):
            case.assertEqual('/api/dostk/ordr', path)
            data.wire.append((api_id, dict(body)))
            venue, symbol = body['dmst_stex_tp'], body['stk_cd']
            if api_id in {'kt10000', 'kt10001'}:
                side = OrderSide.BUY if api_id == 'kt10000' else OrderSide.SELL
                candidates = data.execution.active_intents('mock', data.runtime.account_ref, data.run_id)
                record, = (item for item in candidates if item.state is OrderState.SUBMISSION_UNKNOWN
                           and not item.broker_order_id and (item.intent.symbol, item.intent.venue, item.intent.side,
                            item.intent.quantity, item.intent.limit_price) == (symbol, venue, side,
                                int(body['ord_qty']), int(body['ord_uv'])))
                actual = data.sor_destinations[symbol] if venue == 'SOR' else venue
                result = broker_submit(replace(record.intent, venue=actual))
                return {'ord_no': result.broker_order_id, 'dmst_stex_tp': actual}
            original = body['orig_ord_no']
            record = data.execution.find_by_broker_order_id('mock', data.runtime.account_ref, data.run_id, original)
            case.assertEqual((symbol, venue), (record.intent.symbol, execution_broker_venue(record)))
            actual_intent = replace(record.intent, venue=venue)
            if api_id == 'kt10002':
                result = data.book.amend(actual_intent, original, int(body['mdfy_qty']), int(body['mdfy_uv']))
                return {'ord_no': result.broker_order_id, 'base_orig_ord_no': original,
                        'mdfy_qty': str(result.quantity), 'dmst_stex_tp': venue}
            case.assertEqual('kt10003', api_id)
            result = data.book.cancel(actual_intent, original, int(body['cncl_qty']))
            return {'ord_no': result.broker_order_id, 'dmst_stex_tp': venue}

        data.request_once = request_once
        data.route_rejection = getattr(data, 'route_rejection', lambda _intent, _now: None)
    monitor = MockAccountMonitor(data.book, data.runtime, data.execution, now_provider=lambda: data.clock[0],
        automation_risk_repository=data.repository, automation_binding_revision=data.selection.binding_revision,
        automation_account_scope=data.selection.account_scope, new_orders_enabled_on_start=False,
        lease_seconds=3600)
    data.close_monitor = own_async_close(case, monitor.close)
    await monitor.start()
    monitor.set_automation_execution_inputs_enabled(True)
    await monitor._queue.join()
    gateway = ManualMockOrderGateway(data.runtime, data.execution, monitor.refresh_account,
        now_provider=lambda: data.clock[0], automation_execution_cut=lambda: monitor.latest_automation_execution_cut)
    gateway.set_manual_submit_enabled(False)
    data.close_gateway = own_async_close(case, gateway.close)
    if admit:
        data.quotes = {}
        data.nxt_quotes = {}
        data.live = dict(revision=1, enabled=True, policy=data.leader_config.policy.to_dict())
        data.settings = dict(revision=1, shadow_candidate_enabled=True,
            shadow_candidate_configs={data.specs[0].spec_id: data.leader_config.policy.to_dict(),
                                      data.specs[1].spec_id: data.minute_config},
            shadow_candidate_universe_max_age_seconds=180)
    data.store.upsert_documents('server_operational_settings',
        [dict(owner='global', key='current', document=data.settings)])
    data.bundle = SimpleNamespace(account_ref=data.runtime.account_ref, run_id=data.run_id,
        credential_profile_id=data.selection.credential_profile_id, runtime=data.runtime,
        repository=data.execution, monitor=monitor, gateway=gateway,
        automation_context=MockAutomationRuntimeContext(MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION, data.runtime.account_ref,
            data.run_id, data.specs[0].spec_id, admissions[0].admission_id, control.control_revision, 1))
    from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
    data.shadow = CandidateMonitor.from_settings(data.store, data.settings, poll_seconds=3600,
        universe_max_age_seconds=180, now_provider=lambda: data.clock[0])
    data.shadow._members[data.specs[0].spec_id]._leader_accepting = True
    data.shadow.run_once()
    data.close_shadow = own_async_close(case, data.shadow.close)
    cut = monitor.latest_automation_execution_cut
    for spec in data.specs:
        record_mock_automation_recovery_from_risk(data.repository, data.runtime, cut['recovery'], cut['risk'],
            account_ref=data.runtime.account_ref, spec_id=spec.spec_id, observation=cut['observation'])
    episodes = tuple(MockAutomationEpisode(data.store, data.bundle, spec, member_admission=admission,
        price_source=lambda symbol: data.quotes.get(symbol),
        nxt_price_source=lambda symbol: data.nxt_quotes.get(symbol),
        leader_context_source=lambda spec_id=None: data.live if spec_id in (None, data.specs[0].spec_id) else None,
        market_comparison=getattr(data, 'market_comparison', None),
        now_provider=lambda: data.clock[0]) for spec, admission in zip(data.specs, admissions, strict=True))
    for episode in episodes:
        episode.prepare_restore()
    data.runner = MockAutomationRunner(episodes[0], member_episodes=episodes[1:], monitor=monitor,
        poll_seconds=1, execution_order=data.selection.execution_order_spec_ids)
    data.runner.attach_inputs()
    data.close_runner = own_async_close(case, data.runner.close)
    data.episodes = episodes
    return data


async def reopen_parallel_runtime(case, data):
    """Retire the sole account owner, reopen its native DB and restore members."""
    from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
    previous = data.runtime
    await data.close_runner()
    await data.close_shadow()
    await data.close_gateway()
    await data.close_monitor()
    case.assertFalse(previous._active)
    data.store.close()
    data.store = data.factory()
    data.store.initialize()
    case.addCleanup(data.store.close)
    case.enterContext(patch.object(data.store, '_execution_wall_now', lambda: data.clock[0], create=True))
    data.repository = ForwardEvaluationRepository(data.store)
    return await assemble_parallel_runtime(case, data, admit=False)


async def verify_parallel_preparation(case, factory=None):
    data = await parallel_runtime(case, factory)
    case.assertEqual({'leader_breakout/v1', 'close_betting/v1'},
        {row['package']['candidate_spec']['family'] for row in data.publications})
    case.assertEqual(1, data.binding['binding_revision'])
    case.assertEqual({data.run_id}, {episode._bundle.run_id for episode in data.episodes})
    case.assertTrue(all(episode._bundle is data.bundle for episode in data.episodes))
    case.assertEqual(tuple(spec.spec_id for spec in data.specs), data.runner.episode.admitted_member_spec_ids)
    case.assertTrue(all(episode.prepared for episode in data.episodes))
    case.assertEqual([], data.transport.calls, 'final/READY/historical Shadow and restoration submit no orders')
    case.assertEqual(((), True), data.execution.account_active_intents(data.runtime.account_ref))
    cut = data.bundle.monitor.latest_automation_execution_cut
    case.assertEqual({}, cut['observation'].account.positions)
    case.assertEqual((), cut['risk'].owned_position_quantities)
    events = data.execution.account_events('mock', data.runtime.account_ref).events
    case.assertEqual([], [event for event in events if event.run_id == data.run_id])
    case.assertEqual((), events)
    return data


async def advance_members(data):
    """Run the Runner's declared order without giving either member a task."""
    await data.bundle.monitor.refresh_recovery()
    for episode in data.runner._execution_episodes:
        await episode.advance_once()


async def deliver_price(data, price, *, advance=True, source='parallel-approved-0B:1', nxt_price=None):
    data.clock[0] += timedelta(milliseconds=100)
    data.runtime.heartbeat()
    quote = dict(price_won=price, available_at=data.clock[0],
        input_ref='collector-trade:' + source + ':' + data.clock[0].isoformat(),
        durable=False, market_status='TRADABLE')
    for symbol in {'005930', data.example.symbol}:
        data.quotes[symbol] = quote
        data.nxt_quotes[symbol] = {**quote, 'venue': 'NXT', 'price_won': price if nxt_price is None else nxt_price,
                                  'input_ref': quote['input_ref'] + ':NXT:' + symbol}
    payload = dict(code='005930', market='KRX', current_price=price,
                   trade_time=data.clock[0].strftime('%H%M%S'), cumulative_trade_value=100)
    data.shadow.observe_trade(payload, data.clock[0], source=source, approved=True)
    for episode in data.episodes:
        episode.observe_leader_trade(payload, data.clock[0], source=source, approved=True)
        for symbol in {'005930', data.example.symbol}:
            episode.observe_automation_price(symbol, quote)
            episode.observe_automation_price(symbol, data.nxt_quotes[symbol])
    data.shadow.run_once()
    if advance:
        await advance_members(data)


def publish_current_close_minute(data, *, price=1002):
    start = datetime.fromisoformat(data.example.signal.bar_start) + timedelta(minutes=1)
    frame = replace(data.example.signal, revision_id='parallel-current-close-minute',
        bar_start=start.isoformat(), bar_end=(start + timedelta(minutes=1)).isoformat(),
        available_at=data.clock[0].isoformat(), open=price, high=price, low=price, close=price)
    save_bar(data.store, frame, 'parallel-current-close-minute')
    data.shadow.run_once()


async def fill_order(data, record, quantity, identity='same-raw-fill-id', *, broker_order_id=None):
    data.clock[0] += timedelta(milliseconds=1)
    data.book.fill(broker_order_id or record.broker_order_id, quantity, identity)
    await advance_members(data)


async def fill_initial_buys(data, leader, close, *, close_quantity=4, advance=True):
    """Two independent broker FILLs precede the next current-order assessment."""
    data.clock[0] += timedelta(milliseconds=1)
    data.book.fill(leader.broker_order_id, 2, 'same-raw-fill-id')
    data.book.fill(close.broker_order_id, close_quantity, 'same-raw-fill-id')
    if advance:
        await advance_members(data)


async def restore_manual_history(data):
    """Reconcile separately evidenced old manual lots after the initial entries."""
    from kiwoom_monitor.domain.order_contract import OrderState, order_event
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
    ledger = ExecutionRepository(data.store)
    past = data.manual.created_at
    manual = ledger.create(data.manual)
    for kind, state, changes, values in (
        ('QUEUED', OrderState.QUEUED, {}, {}),
        ('SUBMISSION_STARTED', OrderState.SUBMISSION_UNKNOWN, {}, {}),
        ('ORDER_ACCEPTED', OrderState.ACCEPTED, {'broker_order_id': 'parallel-archived-manual-order'},
         {'broker_order_id': 'parallel-archived-manual-order'}),
        ('FILL', OrderState.FILLED, {'filled_quantity': 4, 'detailed_filled_quantity': 4,
          'broker_reported_filled_quantity': 4, 'fill_ids': ('manual-original-fill',), 'last_broker_as_of': past},
         {'broker_order_id': 'parallel-archived-manual-order', 'broker_execution_id': 'manual-original-fill',
          'quantity': 4, 'price': 1000, 'broker_as_of': past}),
        ('BROKER_RECONCILED', OrderState.FILLED, {},
         {'broker_order_id': 'parallel-archived-manual-order', 'quantity': 4, 'broker_as_of': past}),
    ):
        manual = ledger.apply(manual,
            order_event(data.manual.intent_id, kind, state, past, data.clock[0], **values), **changes)
    data.book.add('parallel-archived-manual-order', data.manual)
    current = data.clock[0]
    data.clock[0] = past
    data.book.fill('parallel-archived-manual-order', 4, 'manual-original-fill')
    data.clock[0] = current
    await advance_members(data)


def member_records(data, index, *, side=None):
    records = data.execution.active_intents('mock', data.runtime.account_ref, data.run_id)
    return tuple(row for row in records if row.member_spec_id == data.specs[index].spec_id
                 and (side is None or row.intent.side.value == side))


async def first_parallel_buys(case, factory=None, *, shared_capital=20_000, reentry=False):
    data = await parallel_runtime(case, factory, shared_capital=shared_capital, reentry=reentry)
    await deliver_price(data, 990)
    publish_current_close_minute(data)
    await deliver_price(data, 1001)
    buys = tuple(next(iter(member_records(data, index, side='BUY')), None) for index in (0, 1))
    case.assertTrue(all(buys), ([episode.status for episode in data.episodes], data.transport.calls))
    case.assertEqual([(data.specs[0].spec_id, 2), (data.specs[1].spec_id, 4)],
                     [(row.member_spec_id, row.intent.quantity) for row in buys])
    case.assertEqual({'005930'}, {row.intent.symbol for row in buys})
    case.assertEqual({data.run_id}, {row.intent.run_id for row in buys})
    return data, buys


async def verify_parallel_staged_ownership(case, factory=None):
    from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
    data, (leader, close) = await first_parallel_buys(case, factory)
    # Two accepted physical BUY reservations coexist before either creates a
    # strategy position. The broker supplies both fills independently.
    await data.bundle.monitor.refresh_recovery()
    cut = data.bundle.monitor.latest_automation_execution_cut
    case.assertEqual(2, len(cut['observation'].open_orders))
    case.assertEqual(6, sum(row.intent.quantity for row in cut['observation'].open_orders))
    await fill_initial_buys(data, leader, close)
    await restore_manual_history(data)
    case.assertEqual([2, 4], [episode._episode_position.state.position_quantity for episode in data.episodes])
    case.assertEqual({'005930': 10}, data.bundle.monitor.latest_automation_execution_cut['observation'].account.positions)
    await deliver_price(data, 1049)
    await deliver_price(data, 1051)
    additions = member_records(data, 0, side='BUY')
    case.assertEqual(1, len(additions), ([episode.status for episode in data.episodes], data.transport.calls))
    additional, = additions
    case.assertEqual((1, leader.intent.plan.plan_id), (additional.intent.quantity, additional.intent.plan.plan_id))
    await fill_order(data, additional, 1, 'additional-only')
    await deliver_price(data, 1101)
    reductions = member_records(data, 0, side='SELL')
    case.assertEqual(1, len(reductions), ([episode.status for episode in data.episodes], data.transport.calls))
    reduction, = reductions
    case.assertEqual((2, leader.intent.plan.plan_id), (reduction.intent.quantity, reduction.intent.plan.plan_id))
    await fill_order(data, reduction, 2, 'reduction-only')
    case.assertEqual([1, 4], [episode._episode_position.state.position_quantity for episode in data.episodes])
    head = data.execution.load_plan_position(data.runtime.account_ref, data.run_id, close.intent.symbol, close.intent.plan)
    case.assertEqual(4, head.quantity)
    case.assertEqual({'005930': 9}, data.bundle.monitor.latest_automation_execution_cut['observation'].account.positions)
    case.assertEqual([('BUY', 2), ('BUY', 4), ('BUY', 1), ('SELL', 2)],
                     [(row[1], row[3]) for row in data.transport.calls])
    # Remove only current leader inputs. The original plan's absolute hard
    # stop still owns exactly its remaining one share.
    data.live = None
    data.settings = {**data.settings, 'revision': 2,
        'shadow_candidate_configs': {data.specs[1].spec_id: data.minute_config}}
    data.store.upsert_documents('server_operational_settings',
        [dict(owner='global', key='current', document=data.settings)])
    await data.close_shadow()
    data.shadow = CandidateMonitor.from_settings(data.store, data.settings, poll_seconds=3600,
        universe_max_age_seconds=180, now_provider=lambda: data.clock[0])
    data.close_shadow = own_async_close(case, data.shadow.close)
    data.shadow.run_once()
    await deliver_price(data, 940)
    notice = data.episodes[0]._protection_snapshot()
    case.assertEqual(1, notice['planned_leader_stop']['stop_loss']['quantity'])
    await deliver_price(data, 960)
    await advance_members(data)
    sells = member_records(data, 0, side='SELL')
    case.assertEqual(1, len(sells), ([episode.status for episode in data.episodes], data.transport.calls))
    protective, = sells
    case.assertEqual((1, leader.intent.plan.plan_id), (protective.intent.quantity, protective.intent.plan.plan_id))
    await fill_order(data, protective, 1, 'leader-original-one-only')
    case.assertEqual(0, data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
                                                        leader.intent.symbol, leader.intent.plan).quantity)
    case.assertEqual(4, data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
                                                        close.intent.symbol, close.intent.plan).quantity)
    case.assertEqual(4, data.execution.load(data.manual.intent_id).detailed_filled_quantity)
    case.assertEqual({'005930': 8}, data.bundle.monitor.latest_automation_execution_cut['observation'].account.positions)
    case.assertEqual([('BUY', 2), ('BUY', 4), ('BUY', 1), ('SELL', 2), ('SELL', 1)],
                     [(row[1], row[3]) for row in data.transport.calls])
    return data, (leader, close, additional, reduction, protective)


async def verify_parallel_one_task(case, factory=None):
    data, (leader, close) = await first_parallel_buys(case, factory)
    await fill_initial_buys(data, leader, close, advance=False)
    await data.runner.start()
    task = data.runner._task
    await data.bundle.monitor.refresh_recovery()
    async def both_fills_consumed():
        while [episode._episode_position.state.position_quantity for episode in data.episodes] != [2, 4]:
            await asyncio.sleep(0.01)
    try:
        await asyncio.wait_for(both_fills_consumed(), 10)
    except TimeoutError as error:
        raise AssertionError(([episode.status for episode in data.episodes], data.transport.calls)) from error
    case.assertIs(task, data.runner._task)
    case.assertFalse(task.done())
    case.assertEqual(1, sum(value.get_name() == 'mock-automation-runner' and not value.done()
                            for value in asyncio.all_tasks()))
    case.assertEqual(2, len(data.transport.calls))
    await data.close_runner()
    case.assertTrue(task.done())
    case.assertTrue(all(episode.closing for episode in data.episodes))
    case.assertIsNone(data.bundle.monitor._automation_risk_listener)
    case.assertFalse(data.bundle.monitor.automation_execution_inputs_enabled)


async def verify_parallel_shared_capital(case, factory=None):
    data = await parallel_runtime(case, factory, shared_capital=5000)
    await deliver_price(data, 990)
    publish_current_close_minute(data)
    try:
        await deliver_price(data, 1001)
    except RuntimeError as error:
        case.assertIn('MOCK_ACCOUNT_CAPITAL_UNAVAILABLE', str(error))
    first, = member_records(data, 0, side='BUY')
    case.assertEqual((2, 1001), (first.intent.quantity, first.intent.limit_price))
    case.assertEqual((), member_records(data, 1))
    case.assertEqual([('BUY', 2)], [(row[1], row[3]) for row in data.transport.calls])
    rows = data.store.load_documents('execution_mock_plan_positions', data.runtime.account_ref, 1000)
    case.assertEqual([data.specs[0].spec_id], [row['document']['spec_id'] for row in rows])


async def verify_parallel_new_setup_reentry(case, factory=None):
    from kiwoom_monitor.application.breakout_strategy import StrategyState
    data, (leader, close) = await first_parallel_buys(case, factory, reentry=True)
    await fill_initial_buys(data, leader, close)
    await deliver_price(data, 1049)
    await deliver_price(data, 1051)
    additional, = member_records(data, 0, side='BUY')
    case.assertEqual((1, leader.intent.plan.plan_id), (additional.intent.quantity, additional.intent.plan.plan_id))
    await fill_order(data, additional, 1, 'old-plan-additional-fill')
    await deliver_price(data, 1101)
    reduction, = member_records(data, 0, side='SELL')
    case.assertEqual((2, leader.intent.plan.plan_id), (reduction.intent.quantity, reduction.intent.plan.plan_id))
    await fill_order(data, reduction, 2, 'old-plan-reduction-fill')
    await deliver_price(data, 970)  # Actual structure failure, above the pinned hard stop.
    exits = member_records(data, 0, side='SELL')
    case.assertEqual(1, len(exits), [episode.status for episode in data.episodes])
    case.assertEqual(1, exits[0].intent.quantity)
    await fill_order(data, exits[0], 1, 'original-structure-exit')
    case.assertEqual('flat', data.episodes[0]._episode_position.state.status)
    old = data.execution.load_plan_position(data.runtime.account_ref, data.run_id, leader.intent.symbol, leader.intent.plan)
    case.assertEqual(0, old.quantity)
    before = tuple(data.transport.calls)
    await deliver_price(data, 990, source='parallel-repackaged-same-setup:2')
    await deliver_price(data, 1001, source='parallel-repackaged-same-setup:2')
    case.assertEqual(before, tuple(data.transport.calls))
    case.assertIn('reentry_setup_already_used', data.episodes[0].status['reason'])
    # An invalid caller/checkpoint cannot turn the original setup into a new
    # first entry by erasing RAM history. The real native writer must refuse.
    episode = data.episodes[0]
    saved_state, saved_pin = episode._episode_position.state, episode._leader_position_proof
    episode._episode_position.replace_strategy_state(StrategyState(), position_time=lambda: data.clock[0])
    await deliver_price(data, 990, source='invalid-erased-history:3', advance=False)
    await deliver_price(data, 1001, source='invalid-erased-history:3', advance=False)
    control = data.repository.load_mock_automation_control(data.runtime.account_ref)
    with case.assertRaisesRegex(RuntimeError, 'LEADER_REENTRY_'):
        # Exercise the actual page/decision/native boundary directly. A normal
        # advance repairs erased RAM steps from native claims before this page.
        await episode._run_leader_page(control, 1000)
    case.assertEqual(before, tuple(data.transport.calls))
    episode._episode_position.replace_strategy_state(saved_state, position_time=lambda: data.clock[0])
    episode._leader_position_proof = saved_pin
    episode._save_checkpoint(force=True)
    await install_new_leader_setup(case, data)
    await deliver_price(data, 990, source='parallel-new-setup:2')
    await deliver_price(data, 1001, source='parallel-new-setup:2')
    buys = member_records(data, 0, side='BUY')
    case.assertEqual(1, len(buys), ([episode.status for episode in data.episodes], data.transport.calls))
    case.assertNotEqual(leader.intent.plan.plan_id, buys[0].intent.plan.plan_id)
    case.assertEqual(2, buys[0].intent.quantity)
    case.assertEqual(4, data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
                                                        close.intent.symbol, close.intent.plan).quantity)
    case.assertEqual(2, data.execution.load(leader.intent.intent_id).detailed_filled_quantity)
    case.assertEqual([('BUY', 2), ('BUY', 4), ('BUY', 1), ('SELL', 2), ('SELL', 1), ('BUY', 2)],
                     [(row[1], row[3]) for row in data.transport.calls])
    await fill_order(data, buys[0], 2, 'actual-new-independent-setup-fill')
    await deliver_price(data, 1049, source='parallel-new-setup:2')
    await deliver_price(data, 1051, source='parallel-new-setup:2')
    next_add, = member_records(data, 0, side='BUY')
    case.assertEqual((1, buys[0].intent.plan.plan_id), (next_add.intent.quantity, next_add.intent.plan.plan_id))
    await fill_order(data, next_add, 1, 'new-plan-additional-fill')
    history = data.episodes[0]._episode_position.state.emitted_candidate_keys
    case.assertEqual(saved_state.emitted_candidate_keys, history[:len(saved_state.emitted_candidate_keys)])
    case.assertEqual(3, len([key for key in history if key.startswith('leader-position-step/v1:')]))
    for episode in data.episodes:
        episode._save_checkpoint(force=True)
    before = tuple(data.transport.calls)
    await reopen_parallel_runtime(case, data)
    case.assertEqual(before, tuple(data.transport.calls))
    case.assertEqual(history, data.episodes[0]._episode_position.state.emitted_candidate_keys)
    case.assertEqual([3, 4], [episode._episode_position.state.position_quantity for episode in data.episodes])
    case.assertEqual(0, data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
        leader.intent.symbol, leader.intent.plan).quantity)
    case.assertEqual(3, data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
        buys[0].intent.symbol, buys[0].intent.plan).quantity)


async def install_new_leader_setup(case, data):
    from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
    data.clock[0] += timedelta(milliseconds=1)  # A new source delivery after the exit acknowledgement.
    context = replace(data.leader_config.contexts[0], evidence_ref='synthetic:new-independent-setup',
        available_at=data.clock[0].isoformat(), valid_until=(data.clock[0] + timedelta(hours=1)).isoformat())
    data.leader_config = replace(data.leader_config, contexts=(context,))
    data.live = dict(revision=2, enabled=True, policy=data.leader_config.policy.to_dict())
    data.settings = {**data.settings, 'revision': 2, 'shadow_candidate_configs': {
        **data.settings['shadow_candidate_configs'], data.specs[0].spec_id: data.leader_config.policy.to_dict()}}
    data.store.upsert_documents('server_operational_settings',
        [dict(owner='global', key='current', document=data.settings)])
    await data.close_shadow()
    data.shadow = CandidateMonitor.from_settings(data.store, data.settings, poll_seconds=3600,
        universe_max_age_seconds=180, now_provider=lambda: data.clock[0])
    data.shadow._members[data.specs[0].spec_id]._leader_accepting = True
    data.shadow.run_once()
    data.close_shadow = own_async_close(case, data.shadow.close)


async def verify_parallel_late_old_fill_invalidates_reentry(case, factory=None):
    from kiwoom_monitor.domain.order_contract import BrokerFill, BrokerOrderSnapshot, OrderState
    data, (leader, close) = await first_parallel_buys(case, factory, reentry=True)
    data.book.fill(leader.broker_order_id, 1, 'first-partial-leader-fill')
    data.book.fill(close.broker_order_id, 4, 'complete-close-fill')
    await advance_members(data)
    late_occurred_at = data.clock[0] + timedelta(microseconds=1)
    await deliver_price(data, 1000)  # The confirmed breakout failed: cancel only the original residual.
    await advance_members(data)  # Observe the broker's terminal cancellation acknowledgement.
    case.assertEqual(OrderState.CANCELLED, data.execution.load(leader.intent.intent_id).state)
    await deliver_price(data, 970)
    exit_order, = member_records(data, 0, side='SELL')
    case.assertEqual(1, exit_order.intent.quantity)
    await fill_order(data, exit_order, 1, 'confirmed-one-share-exit')
    old_plan = lambda: data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
        leader.intent.symbol, leader.intent.plan)
    case.assertEqual(0, old_plan().quantity)
    await install_new_leader_setup(case, data)
    await deliver_price(data, 990, source='parallel-new-setup-before-late-fill:2')
    before, queued = tuple(data.transport.calls), []
    submit = data.runtime._lifecycle.submit
    def late_fill_before_submission(intent_id, account, **kwargs):
        record = data.execution.load(intent_id)
        case.assertEqual(OrderState.QUEUED, record.state)
        case.assertNotEqual(leader.intent.plan.plan_id, record.intent.plan.plan_id)
        queued.append(record)
        # The broker delivers an earlier execution only now, after confirming
        # the old residual cancelled and the next plan's QUEUED claim accepted.
        # It is a broker detail, never a fabricated native event or Risk ID.
        data.clock[0] += timedelta(milliseconds=1)
        row = data.book.rows[leader.broker_order_id]
        row['fills'].append(BrokerFill('late-old-original-buy-fill', 1, row['price'], late_occurred_at))
        row.update(remaining=0, state=OrderState.FILLED)
        data.runtime.reconcile(leader.intent.intent_id, BrokerOrderSnapshot(leader.broker_order_id,
            data.runtime.account_ref, leader.intent.symbol, OrderState.FILLED, 2, 0,
            data.clock[0], tuple(row['fills'])))
        return submit(intent_id, account, **kwargs)
    with patch.object(data.runtime._lifecycle, 'submit', side_effect=late_fill_before_submission):
        with case.assertRaisesRegex(RuntimeError,
                'PLANNED_EXECUTION_RISK_OR_RECOVERY_CHANGED|LEADER_REENTRY_[A-Z_]+'):
            await deliver_price(data, 1001, source='parallel-new-setup-before-late-fill:2')
    new_order, = queued
    case.assertEqual(before, tuple(data.transport.calls))
    case.assertEqual(1, old_plan().quantity)
    case.assertEqual(4, data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
        close.intent.symbol, close.intent.plan).quantity)
    case.assertFalse(any(event['event_type'] == 'SUBMISSION_STARTED'
        for event in data.execution.events(new_order.intent.intent_id)))
    case.assertEqual(0, data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
        new_order.intent.symbol, new_order.intent.plan).quantity)
    late_fill, = [event for event in data.execution.events(leader.intent.intent_id)
                 if event.get('broker_execution_id') == 'late-old-original-buy-fill']
    case.assertEqual((leader.intent.intent_id, 1), (late_fill['intent_id'], late_fill['quantity']))


async def verify_parallel_late_fill_restart_resume(case, factory=None):
    from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation, resume_mock_automation
    from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
    from kiwoom_monitor.domain.order_contract import OrderState
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
    data, (leader, close) = await first_parallel_buys(case, factory)
    await fill_initial_buys(data, leader, close, close_quantity=2)
    partial = data.execution.load(close.intent.intent_id)
    case.assertEqual(OrderState.PARTIALLY_FILLED, partial.state,
        (data.book.mutations, data.book.rows, data.episodes[1].status))
    data.clock[0] += timedelta(milliseconds=100)
    control = data.repository.load_mock_automation_control(data.runtime.account_ref)
    emergency_stop_mock_automation(data.repository, data.runtime, account_ref=data.runtime.account_ref,
        spec_id=data.specs[1].spec_id, stopped_at=data.clock[0], reason='explicit synthetic whole-account stop',
        expected_control_revision=control.control_revision)
    await data.bundle.monitor.refresh_recovery()
    cut = data.bundle.monitor.latest_automation_execution_cut
    # Refreshing one member cannot authorize the stopped common account.
    record_mock_automation_recovery_from_risk(data.repository, data.runtime, cut['recovery'], cut['risk'],
        account_ref=data.runtime.account_ref, spec_id=data.specs[0].spec_id, observation=cut['observation'])
    with case.assertRaisesRegex(RuntimeError, 'fresh recovery for every selected member'):
        resume_mock_automation(data.repository, data.runtime, account_ref=data.runtime.account_ref,
            spec_id=data.specs[0].spec_id, resumed_at=data.clock[0], reason='insufficient one-member recovery')
    proof = data.repository.load_close_entry_evidence(data.runtime.account_ref, close.intent.plan.plan_id)
    deadline = datetime.fromisoformat(proof['snapshot']['candidate']['close_ipo_entry_setup']['entry_deadline'])
    data.clock[0] = deadline + timedelta(milliseconds=100)
    await deliver_price(data, 1001, advance=False)
    uncertain_numbers = []
    def uncertain_cancel(intent, number, quantity=0):
        case.assertEqual(close.intent.intent_id, intent.intent_id)
        case.assertEqual('CANCEL_STARTED', data.execution.events(intent.intent_id)[-1]['event_type'])
        data.book.mutations.append(('CANCEL_UNKNOWN', number, quantity))
        uncertain_numbers.append(number)
        raise SubmissionUnknown('broker accepted cancellation; response lost')
    with patch.object(data.transport, 'cancel', side_effect=uncertain_cancel):
        await advance_members(data)
        await advance_members(data)
        current = data.execution.load(close.intent.intent_id)
        case.assertEqual(OrderState.CANCEL_PENDING, current.state,
            {'uncertain_numbers': uncertain_numbers, 'broker_mutations': data.book.mutations,
             'broker_orders': data.book.rows, 'native_legs': current.broker_legs,
             'episode': data.episodes[1].status})
        case.assertEqual(1, len([row for row in data.book.mutations if row[0] == 'CANCEL_UNKNOWN']))
    # Independent broker detail reports one later FILL and a confirmed cancel
    # of the residual one share. No new logical order fills that gap.
    data.clock[0] += timedelta(milliseconds=100)
    uncertain_number, = uncertain_numbers
    data.book.fill(uncertain_number, 1, 'close-late-original-fill')
    data.book.rows[uncertain_number].update(remaining=0, state=OrderState.CANCELLED)
    await advance_members(data)
    original = data.execution.load(close.intent.intent_id)
    case.assertEqual((OrderState.CANCELLED, 3), (original.state, original.detailed_filled_quantity))
    case.assertEqual([2, 3], [episode._episode_position.state.position_quantity for episode in data.episodes])
    old_runtime = data.runtime
    old_calls = tuple(data.transport.calls)
    for episode in data.episodes:
        episode._save_checkpoint(force=True)
    data.clock[0] += timedelta(seconds=1)
    await reopen_parallel_runtime(case, data)
    case.assertIsNot(old_runtime, data.runtime)
    case.assertFalse(old_runtime._active)
    case.assertNotEqual(old_runtime.owner_token, data.runtime.owner_token)
    case.assertEqual([2, 3], [episode._episode_position.state.position_quantity for episode in data.episodes])
    case.assertEqual('STOPPED', data.repository.load_mock_automation_control(data.runtime.account_ref).desired_state.value)
    case.assertEqual(old_calls, tuple(data.transport.calls), 'reopening a stopped account does not retry either BUY')
    resumed = resume_mock_automation(data.repository, data.runtime, account_ref=data.runtime.account_ref,
        spec_id=data.specs[0].spec_id, resumed_at=data.clock[0], reason='explicit all-member current recovery resume')
    case.assertEqual('RUNNING', resumed.desired_state.value)
    await advance_members(data)
    case.assertEqual(old_calls, tuple(data.transport.calls))
    case.assertEqual((2, 3), tuple(data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
        row.intent.symbol, row.intent.plan).quantity for row in (leader, close)))


class ParallelStrategyTests(unittest.IsolatedAsyncioTestCase):
    async def test_market_comparison_is_visible_on_both_actual_entry_paths_without_changing_orders(self):
        data = parallel_ready_inputs(self, close_symbol='000660')
        data.market_comparison = lambda cut: {
            'status': 'CONTRADICTED', 'as_of': cut.isoformat(), 'advisory_only': True}
        await assemble_parallel_runtime(self, data, admit=True)
        data.clock[0] = max(data.clock[0], datetime.fromisoformat(data.example.signal.bar_end) + timedelta(minutes=1))
        await deliver_price(data, 990)
        publish_current_close_minute(data)
        await deliver_price(data, 1001)
        for index, (symbol, quantity) in enumerate((('005930', 2), ('000660', 4))):
            entry, = member_records(data, index, side='BUY')
            self.assertEqual((symbol, quantity), (entry.intent.symbol, entry.intent.quantity))
            evidence = data.episodes[index].status['entry_market_comparison']
            self.assertEqual((symbol, 'CONTRADICTED'), (evidence['symbol'], evidence['status']))

    async def test_sor_amends_the_physical_nxt_and_krx_orders_after_reopening(self):
        from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_broker_legs, execution_broker_venue

        data = parallel_ready_inputs(self, venues=('SOR', 'SOR'), close_symbol='000660')
        data.sor_destinations = {'005930': 'NXT', '000660': 'KRX'}
        await assemble_parallel_runtime(self, data, admit=True)
        data.clock[0] = max(data.clock[0], datetime.fromisoformat(data.example.signal.bar_end) + timedelta(minutes=1))
        await deliver_price(data, 990)
        publish_current_close_minute(data)
        await deliver_price(data, 1001)
        entries = tuple(member_records(data, index, side='BUY')[0] for index in (0, 1))
        self.assertEqual([('SOR', 'NXT'), ('SOR', 'KRX')],
                         [(row.intent.venue, execution_broker_venue(row)) for row in entries])
        self.assertEqual(['SOR', 'SOR'], [body['dmst_stex_tp'] for api, body in data.wire if api == 'kt10000'])
        for entry in entries:
            data.book.fill(entry.broker_order_id, 1, 'same-source-fill')
        # Refresh the broker ledger while holding the original price, then restart.
        await advance_members(data)
        sent = list(data.wire)
        await reopen_parallel_runtime(self, data)
        self.assertEqual(sent, data.wire)
        await deliver_price(data, 1002, nxt_price=1003)
        mutations = {body['stk_cd']: body for api, body in data.wire if api == 'kt10002'}
        self.assertEqual({'005930', '000660'}, set(mutations), ([e.status for e in data.episodes], data.wire))
        self.assertEqual([('NXT', entries[0].broker_order_id, '1', '1003'),
                          ('KRX', entries[1].broker_order_id, '3', '1002')],
            [tuple(mutations[entry.intent.symbol][key] for key in ('dmst_stex_tp', 'orig_ord_no', 'mdfy_qty', 'mdfy_uv'))
             for entry in entries])
        sent = list(data.wire)
        await reopen_parallel_runtime(self, data)
        self.assertEqual(sent, data.wire)
        for entry in entries:
            current = data.execution.load(entry.intent.intent_id)
            self.assertEqual(execution_broker_venue(entry), execution_broker_venue(current))
            self.assertEqual(1, current.detailed_filled_quantity)
            active, = (leg for leg in execution_broker_legs(current) if leg.remaining_quantity)
            self.assertEqual(entry.broker_order_id, active.original_broker_order_id)
        fills = data.execution.account_events('mock', data.runtime.account_ref).events
        self.assertEqual({('005930', 'NXT', 1), ('000660', 'KRX', 1)},
                         {(row.symbol, row.venue, row.quantity) for row in fills if row.event_type == 'FILL'})

    async def test_native_nxt_and_krx_keep_two_stocks_and_original_orders_separate_on_reopen(self):
        from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_broker_legs

        data = parallel_ready_inputs(self, venues=('NXT', 'KRX'), close_symbol='000660')
        await assemble_parallel_runtime(self, data, admit=True)
        data.clock[0] = max(data.clock[0], datetime.fromisoformat(data.example.signal.bar_end) + timedelta(minutes=1))
        await deliver_price(data, 990)
        publish_current_close_minute(data)
        await deliver_price(data, 1001)
        leader, = member_records(data, 0, side='BUY')
        close, = member_records(data, 1, side='BUY')
        self.assertEqual([('005930', 'NXT', 2), ('000660', 'KRX', 4)],
            [(row.intent.symbol, row.intent.venue, row.intent.quantity) for row in (leader, close)])
        data.book.fill(leader.broker_order_id, 1, 'same-broker-fill-token')
        data.book.fill(close.broker_order_id, 4, 'same-broker-fill-token')
        await advance_members(data)
        await deliver_price(data, 1002)
        amendments = [(api, body) for api, body in data.wire if api == 'kt10002' and body['stk_cd'] == '005930']
        self.assertEqual(1, len(amendments), ([episode.status for episode in data.episodes], data.wire,
            data.execution.load(leader.intent.intent_id).state))
        amendment, = amendments
        self.assertEqual(('005930', 'NXT', leader.broker_order_id, '1'), tuple(amendment[1][key] for key in
            ('stk_cd', 'dmst_stex_tp', 'orig_ord_no', 'mdfy_qty')))
        submitted = list(data.wire)
        await reopen_parallel_runtime(self, data)
        self.assertEqual(submitted, data.wire)
        self.assertEqual([1, 4], [data.execution.load_plan_position(data.runtime.account_ref, data.run_id,
            row.intent.symbol, row.intent.plan).quantity for row in (leader, close)])
        working, = [leg for leg in execution_broker_legs(data.execution.load(leader.intent.intent_id))
                    if leg.remaining_quantity]
        await fill_order(data, leader, 1, 'nxt-amended-lot', broker_order_id=working.broker_order_id)
        await deliver_price(data, 1049)
        await deliver_price(data, 1051)
        addition, = member_records(data, 0, side='BUY')
        self.assertEqual((leader.intent.plan.plan_id, 'NXT', 1),
                         (addition.intent.plan.plan_id, addition.intent.venue, addition.intent.quantity))
        await fill_order(data, addition, 1, 'nxt-addition')
        await deliver_price(data, 1101)
        reduction, = member_records(data, 0, side='SELL')
        self.assertEqual((leader.intent.plan.plan_id, 'NXT', 2),
                         (reduction.intent.plan.plan_id, reduction.intent.venue, reduction.intent.quantity))
        await fill_order(data, reduction, 2, 'nxt-reduction')
        await deliver_price(data, 890)
        await deliver_price(data, 900)
        await advance_members(data)
        exits = tuple(member_records(data, index, side='SELL') for index in (0, 1))
        self.assertEqual([1, 1], list(map(len, exits)), [episode.status for episode in data.episodes])
        for (sell,), entry, residual in zip(exits, (leader, close), (1, 4), strict=True):
            self.assertEqual((entry.intent.symbol, entry.intent.venue, residual),
                             (sell.intent.symbol, sell.intent.venue, sell.intent.quantity))
            legs = [leg for leg in execution_broker_legs(data.execution.load(sell.intent.intent_id))
                    if leg.remaining_quantity]
            self.assertEqual(sell.intent.quantity, sum(leg.remaining_quantity for leg in legs))
            for leg in legs:
                data.book.fill(leg.broker_order_id, leg.remaining_quantity, 'same-exit-fill-token')
        await advance_members(data)
        self.assertEqual({}, data.bundle.monitor.latest_automation_execution_cut['observation'].account.positions)

    async def test_genuine_final_ready_and_one_binding_restore_two_members_without_orders(self):
        await verify_parallel_preparation(self)

    async def test_actual_same_symbol_orders_and_staged_reduction_keep_other_plan_and_manual_quantity(self):
        await verify_parallel_staged_ownership(self)

    async def test_one_account_runner_task_consumes_both_actual_member_fills_and_closes_once(self):
        await verify_parallel_one_task(self)

    async def test_actual_strategy_orders_share_the_frozen_account_capital_limit(self):
        await verify_parallel_shared_capital(self)

    async def test_recorded_structure_failure_then_new_setup_reenters_without_touching_close_plan(self):
        await verify_parallel_new_setup_reentry(self)

    async def test_late_old_fill_after_new_queued_claim_stays_on_old_plan_and_prevents_submission(self):
        await verify_parallel_late_old_fill_invalidates_reentry(self)

    async def test_partial_unknown_cancel_and_late_fill_reopen_one_owner_then_require_all_member_resume(self):
        await verify_parallel_late_fill_restart_resume(self)


class LeaderReentryContractTests(unittest.TestCase):
    def entry(self, config, state):
        from tests.unit.test_leader_breakout import trade
        from kiwoom_monitor.application.leader_breakout import (
            LeaderBreakoutState, evaluate_leader_breakout_trade, leader_evaluation)
        at = datetime.fromisoformat(config.contexts[0].available_at) + timedelta(seconds=1)
        first, _ = evaluate_leader_breakout_trade(config.policy, trade(990, at=at), received_at=at,
            source='explicit-reentry-test', decision_time=at)
        at += timedelta(milliseconds=100)
        raw, _ = evaluate_leader_breakout_trade(config.policy, trade(1001, at=at), received_at=at,
            source='explicit-reentry-test', decision_time=at, state=LeaderBreakoutState(**first['state_after']))
        return leader_evaluation(run_id='explicit-reentry-run', config=config, raw=raw, state=state,
                                 context=config.contexts[0])

    def test_v1_v2_wire_and_no_reentry_stay_frozen_while_v3_requires_explicit_rule(self):
        from tests.unit.test_leader_breakout import execution_config, AT
        from tests.unit.test_leader_position_steps import staged_config
        from kiwoom_monitor.application.breakout_strategy import StrategyState
        from kiwoom_monitor.domain.strategy_contract import LeaderBreakoutConfig
        for config in (execution_config(), staged_config()):
            self.assertNotIn('reentry_rule', config.execution_contract.to_dict())
            state = StrategyState(cooldown_until=(AT-timedelta(minutes=20)).isoformat(), emitted_candidate_keys=('old',))
            value = self.entry(config, state)
            self.assertIn('unconfigured:reentry_policy_unconfigured', value.decision.reasons)
            self.assertEqual('NO_TRADE', value.decision.final_action)
        raw = reentry_config().to_dict()
        self.assertEqual(raw, LeaderBreakoutConfig.from_dict(raw).to_dict())
        raw['execution_contract'].pop('reentry_rule')
        with self.assertRaisesRegex(ValueError, 'explicit independent-setup'):
            LeaderBreakoutConfig.from_dict(raw)

    def test_v3_preserves_history_and_requires_fresh_independent_evidence_after_confirmed_flat(self):
        from tests.unit.test_leader_breakout import AT
        from kiwoom_monitor.application.breakout_strategy import StrategyState
        from kiwoom_monitor.application.leader_breakout import leader_setup_key
        config = reentry_config()
        old = config.contexts[0]
        state = StrategyState(cooldown_until=AT.isoformat(),
            emitted_candidate_keys=('old-episode', leader_setup_key(old)))
        repackaged = replace(old, available_at=(AT+timedelta(seconds=1)).isoformat(),
                             valid_until=(AT+timedelta(hours=1)).isoformat())
        same = self.entry(replace(config, contexts=(repackaged,)), state)
        self.assertEqual('NO_TRADE', same.decision.final_action)
        self.assertIn('unconfigured:reentry_setup_already_used', same.decision.reasons)
        new = replace(repackaged, evidence_ref='independent-new-source-evidence')
        allowed = self.entry(replace(config, contexts=(new,)), state)
        self.assertEqual('ENTER', allowed.decision.final_action)
        self.assertEqual(state.emitted_candidate_keys, allowed.state.emitted_candidate_keys[:2])
        self.assertEqual(leader_setup_key(new), allowed.state.emitted_candidate_keys[-1])
        for invalid in (replace(state, cooldown_until=''), replace(state, cooldown_until=new.available_at)):
            self.assertEqual('NO_TRADE', self.entry(replace(config, contexts=(new,)), invalid).decision.final_action)

    def test_new_setup_steps_have_new_keys_and_keep_all_previous_plan_step_history(self):
        from tests.unit.test_leader_breakout import AT
        from kiwoom_monitor.application.breakout_strategy import StrategyState
        from kiwoom_monitor.application.leader_breakout import leader_setup_key, leader_next_position_step, leader_position_step_key
        config = reentry_config()
        old = config.contexts[0]
        old_step = leader_position_step_key(config, 0, config.execution_contract.position_steps[0], context=old)
        new = replace(old, evidence_ref='another-independent-setup')
        keys = ('old-episode', leader_setup_key(old), old_step, 'new-episode', leader_setup_key(new))
        state = StrategyState(status='open', symbol=old.symbol, entry_price=1001, position_quantity=2,
                              opened_at=AT.isoformat(), emitted_candidate_keys=keys)
        index, _, current = leader_next_position_step(config, state)
        self.assertEqual(0, index)
        self.assertNotEqual(old_step, current)
        self.assertEqual(keys, state.emitted_candidate_keys)

    def test_minute_paper_uses_two_real_exit_fills_and_separate_plan_budgets_without_erasing_pnl(self):
        from tests.unit.test_leader_breakout import AT
        from tests.unit.test_research_execution import _execution, _bar
        from kiwoom_monitor.application.research_execution import PaperExecutionEngine
        from kiwoom_monitor.application.leader_breakout import evaluate_leader_breakout_bar_step
        config = reentry_config()
        first = replace(config.contexts[0], valid_until=(AT+timedelta(minutes=6)).isoformat())
        second = replace(first, evidence_ref='second-completed-minute-setup',
            available_at=(AT+timedelta(minutes=6)).isoformat(), valid_until=(AT+timedelta(hours=1)).isoformat())
        config = replace(config, contexts=(first, second),
                         execution_contract=replace(config.execution_contract, capital_won=3000))
        engine = PaperExecutionEngine('v3-minute-two-plans', _execution(commission=10, tax=20), config)
        cursor, events = None, []
        for index, price in enumerate((990, 1001, 1040, 940, 930, 990, 990, 1001, 1040, 940, 930)):
            start = AT+timedelta(minutes=index)
            frame = replace(_bar(index, open_=price), revision_id=f'v3-two-plans:{index}', observation_key=start.isoformat(),
                bar_start=start.isoformat(), bar_end=(start+timedelta(minutes=1)).isoformat(),
                available_at=(start+timedelta(minutes=1, seconds=2)).isoformat(),
                open=price, high=price, low=price, close=price, trade_value_million_won=100)
            events.extend(engine.process_bar(frame))
            evaluation, cursor = evaluate_leader_breakout_bar_step(run_id=engine.run_id, evaluation_bar=frame,
                config=config, state=engine.strategy_state, minute_state=cursor)
            events.extend(engine.process_decision(evaluation.decision, evaluation.state, source_bar=frame))
        fills = [event for event in events if event.event_type == 'FILL']
        self.assertEqual([('BUY', 2), ('SELL', 2), ('BUY', 2), ('SELL', 2)],
                         [(event.side, event.quantity) for event in fills])
        self.assertIsNone(engine.portfolio.position)
        self.assertLess(engine.portfolio.realized_pnl_won, 0)
        self.assertGreater(engine.portfolio.total_cost_won, 0)
        self.assertEqual(2080, engine._leader_entry_notional)
        self.assertEqual(datetime.fromisoformat(fills[-1].received_at),
                         datetime.fromisoformat(engine.strategy_state.cooldown_until))


if __name__ == '__main__':
    unittest.main()
