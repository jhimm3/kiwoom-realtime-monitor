"""EX-PANIC frozen final -> immutable candidate -> original native Shadow lineage."""
from contextlib import closing
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


def prepare_panic_final(case, variant='P0', *, unresolved=False, native_factory=None):
    from tests.unit.test_research_final_execution import FinalExecutionTests, fixtures
    from tests.unit.test_mock_automation_specification import ACCOUNT_REF, PROFILE_ID, mock_specification_inputs
    from kiwoom_monitor import research_process as rp
    from kiwoom_monitor.application.mock_automation_candidate import (
        MockAutomationEligibilityPolicy, candidate_package_from_dict, eligibility_policy_from_dict, eligibility_receipt_from_dict,
    )
    from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
    from kiwoom_monitor.central_server.database import SQLiteQueryStore
    from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
    from kiwoom_monitor.infrastructure.research_data_source import load_research_input, write_frozen_research_bundle

    fixture = FinalExecutionTests(); fixture.setUp(); case.addCleanup(fixture.doCleanups)
    root = fixture.fixture.root / ('panic-' + variant); root.mkdir()
    day = date(2026, 9, 25)
    stamp = lambda clock: f'{day}T{clock}:00+09:00'
    prices = (1000, 990, 980, 900, 910, 920, 925, 950, 960, 960)
    rows = fixtures.rows_for(day, len(prices))
    for row, price in zip(rows[1:], prices):
        row['payload'].update(open=price, high=price + 5, low=price - 5, close=price)
    source = root / 'source'
    relative = fixtures.child(source, day, rows)
    write_frozen_research_bundle(source, (relative,))
    references = {'version': 'strategy-reference/v1', 'listings': [], 'calendar': [{
        'venue': 'KRX', 'trading_date': day.isoformat(), 'available_at': stamp('08:00'),
        'revision_id': 'panic-calendar', 'evidence_ref': 'explicit-test-calendar', 'sessions': [{
            'starts_at': stamp('09:00'), 'ends_at': stamp('15:20'), 'session': 'KRX_REGULAR',
            'phase': 'CONTINUOUS', 'support': 'SUPPORTED', 'schedule_version': 'fixture/v1'}]}]}
    reference = root / 'references.json'; raw = json.dumps(references).encode(); reference.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    base, _, _ = fixture.candidate_cli_paths()
    document = json.loads(base.read_text(encoding='utf-8'))
    parameters = {'capital_won': 4000, 'quantity': 4, 'policy': {
        'variant': variant, 'lookback_minutes': 3, 'decline_bps': 1000, 'minimum_turnover_won': 3_000_000,
        'max_input_age_seconds': 10, 'rebound_target_bps': 500, 'max_hold_minutes': 5},
        'news_proofs': [] if unresolved else [{
            'symbol': '005930', 'source': 'explicit-test-news', 'assessment_id': 'panic-news', 'revision': 'news-r1',
            'effective_at': stamp('08:50'), 'available_at': stamp('08:55'),
            'disposition': 'supported_nonfundamental', 'evidence_refs': ['article-r1', 'body-r1', 'rule-r1']}]}
    if unresolved: parameters['panic_allow_unresolved_news'] = True
    document.update(family='panic_rebound/v1', dataset=str(source), database=str(root / 'research.sqlite3'),
        runs_dir=str(root / 'runs'), strategy=parameters, strategy_references={'path': str(reference), 'sha256': digest})
    document['execution'].update(version='completed_close_then_next_open/v1', same_bar_path_version='no_intrabar_fill/v1')
    document['execution']['cost_model'].update(commission_bps=0, sell_tax_bps=0, slippage_bps=0,
        valid_from=stamp('08:00'), valid_to=stamp('16:00'))
    document['evaluation']['folds'][0].update(start=stamp('09:02'), end=stamp('16:00'))
    document['evaluation'].update(minimum_closed_trades=1, minimum_active_days=1)
    request = rp.research_process_request_from_document(document, root)
    implementation = rp.research_implementation_hash(fixtures.PROFILE)
    spec_hash = rp.final_candidate_spec_hash(request, implementation)
    manifest = load_research_input(source, session_profile=fixtures.PROFILE).manifest
    batch = rp.FinalHoldoutBatchSpec('final_holdout_batch/v1', manifest['dataset_id'], manifest['revision_ids_hash'],
                                    fixtures.PROFILE, (spec_hash,), request.evaluation)
    final = rp.FinalHoldoutExecutionRequest(batch, (request,), 'panic-final-access', stamp('17:00'), fixture.owner)
    result = rp.execute_final_holdout_request(final)['candidates'][0]
    case.assertEqual('COMPLETED', result['state'], result['reason'])
    repo = ResearchRepository(request.database)
    fills = [event for event in repo.load_execution_events(result['run_id']) if event['event_type'] == 'FILL']
    # These bars arrive two seconds after close: that minute's open predates
    # the decision, so the first available subsequent open is index5/index6.
    case.assertEqual([('BUY', 4, 920 if variant == 'P0' else 925), ('SELL', 4, 960)],
        [(event['side'], event['quantity'], event['price']) for event in fills])
    enter, = [event for event in repo.load_evaluations(result['run_id']) if event['final_action'] == 'ENTER']
    case.assertLess(datetime.fromisoformat(enter['decided_at']), datetime.fromisoformat(fills[0]['occurred_at']))
    repeated = rp.execute_final_holdout_request(final)['candidates'][0]
    case.assertEqual(('CACHED', result['run_id'], result['logical_result_hash']),
        (repeated['state'], repeated['run_id'], repeated['logical_result_hash']))
    policy = MockAutomationEligibilityPolicy('panic-test', spec_hash, datetime.fromisoformat(stamp('08:00')), 1, 1, -100, 1_000_000)
    publication = rp.prepare_mock_automation_candidate_publication(repo, request, implementation_hash=implementation,
        strategy_ref=policy.strategy_ref, account_ref=ACCOUNT_REF, final_batch_id=batch.batch_id,
        final_run_id=result['run_id'], eligibility_policy=policy)
    report = repo.load_research_report(result['run_id'])
    case.assertEqual(('ELIGIBLE', parameters), (publication['eligibility_receipt']['status'], publication['package']['candidate_spec']['parameters']),
        {key: report.get(key) for key in ('status', 'reasons', 'input_readiness', 'folds')})
    clock = [datetime.fromisoformat(stamp('09:00'))]
    store = native_factory(root / 'native.sqlite3', lambda: clock[0]) if native_factory else SQLiteQueryStore(root / 'native.sqlite3', now_provider=lambda: clock[0])
    store.initialize(); case.addCleanup(store.close)
    inputs = mock_specification_inputs(store, publish_candidate=False)
    inputs.repository.publish_mock_automation_candidate(candidate_package_from_dict(publication['package']),
        eligibility_policy_from_dict(publication['eligibility_policy']), eligibility_receipt_from_dict(publication['eligibility_receipt']),
        credential_profile_id=PROFILE_ID, expected_binding_revision=inputs.binding['binding_revision'])
    events = []
    if not unresolved:
        config = {'version': 'section12-minute-shadow/v1', 'family': request.family, 'signal_valid_seconds': 30,
                  'symbols': ['005930'], 'strategy': parameters}
        monitor = CandidateMonitor.from_json(store, json.dumps(config), poll_seconds=2,
            universe_max_age_seconds=180, now_provider=lambda: clock[0])
        monitor.run_once()
        for index, row in enumerate(rows[1:5 if variant == 'P0' else 6]):
            clock[0] = datetime.fromisoformat(row['available_at'])
            payload = row['payload']; at = datetime.fromisoformat(payload['bar_start'])
            key = dict(trading_date=at.date().isoformat(), minute=at.strftime('%H:%M'), code='005930', market='KRX')
            store.save_minute_bars([{**key, **{name: payload[name] for name in
                ('open', 'high', 'low', 'close', 'volume', 'trade_value_million_won')},
                'updated_at': clock[0].timestamp(), 'operation_id': f'panic-write-{index}'}])
            store.finalize_minute_bars([{**key, 'available_at': clock[0].timestamp(), 'capture_quality': 'complete',
                'finalization_source': 'timer', 'operation_id': f'panic-final-{index}'}])
            monitor.run_once()
        events = store.load_shadow_candidates()['events']
        case.assertEqual(1, len(events))
    with closing(store._connect()) as connection:
        for table in ('central_execution_intents', 'central_execution_events', 'central_execution_runtime_leases'):
            reader = connection.cursor(); reader.execute('SELECT COUNT(*) FROM ' + table)
            case.assertEqual(0, reader.fetchone()[0]); reader.close()
    return SimpleNamespace(root=root, repository=repo, run=result, request=request, publication=publication,
        parameters=parameters, events=events, store=store, inputs=inputs, clock=clock, final=final,
        implementation=implementation, batch=batch)


class PanicFinalPublicationTests(unittest.TestCase):
    def test_p0_final_publishes_original_setup_and_matches_native_shadow_without_orders(self):
        self.verify('P0')

    def test_p1_final_preserves_trigger_and_confirmation_and_matches_native_shadow_without_orders(self):
        self.verify('P1')

    def verify(self, variant, native_factory=None):
        from kiwoom_monitor.application.mock_automation_specification import matching_shadow_events
        from kiwoom_monitor.application.panic_rebound import matches_panic_shadow
        ctx = prepare_panic_final(self, variant, native_factory=native_factory)
        event, = matching_shadow_events(ctx.publication, tuple(ctx.events))
        self.assertEqual((900, 895, 945), tuple(event['panic_entry_setup'][name] for name in ('trigger_close', 'failure_price', 'target_price')))
        self.assertEqual(900 if variant == 'P0' else 910, event['signal_reference_price'])
        for change in ('target', 'quantity', 'variant', 'missing_news'):
            changed = deepcopy(event)
            if change == 'target': changed['panic_entry_setup']['target_price'] += 1
            elif change == 'quantity': changed['quantity'] += 1
            elif change == 'variant': changed['strategy_variant'] = 'P1' if variant == 'P0' else 'P0'
            else: changed['input_revision_refs'].remove('article-r1')
            with self.subTest(change=change): self.assertFalse(matches_panic_shadow(ctx.parameters, changed))
        later = deepcopy(ctx.parameters)
        adverse = {**later['news_proofs'][0], 'revision': 'adverse-after-cut', 'disposition': 'fundamental_adverse',
            'available_at': (datetime.fromisoformat(event['available_at']) + timedelta(seconds=1)).isoformat()}
        later['news_proofs'].append(adverse)
        self.assertTrue(matches_panic_shadow(later, event))
        adverse['available_at'] = event['available_at']
        self.assertTrue(matches_panic_shadow(later, event))  # Historical sample news is not a live-input contract.
        current = deepcopy(event)
        adverse['available_at'] = (datetime.fromisoformat(event['available_at']) + timedelta(seconds=1)).isoformat()
        current['panic_entry_parameters']['news_proofs'].append(adverse)
        self.assertTrue(matches_panic_shadow(ctx.parameters, current))
        adverse['available_at'] = event['available_at']
        self.assertFalse(matches_panic_shadow(ctx.parameters, current))
        from unittest.mock import patch
        from kiwoom_monitor import research_process as rp
        from kiwoom_monitor.application.mock_automation_candidate import eligibility_policy_from_dict
        wrong = deepcopy(ctx.repository.load_run(ctx.run['run_id']))
        wrong['spec']['parameters']['quantity'] += 1
        with patch.object(ctx.repository, 'load_run', return_value=wrong), self.assertRaisesRegex(ValueError, 'source run differs'):
            rp.prepare_mock_automation_candidate_publication(ctx.repository, ctx.request, implementation_hash=ctx.implementation,
                strategy_ref='panic-test', account_ref=ctx.publication['eligibility_receipt']['account_ref'],
                final_batch_id=ctx.batch.batch_id, final_run_id=ctx.run['run_id'],
                eligibility_policy=eligibility_policy_from_dict(ctx.publication['eligibility_policy']))
        return ctx

    def test_unresolved_news_final_remains_researchable_without_becoming_a_native_shadow_authorization(self):
        from kiwoom_monitor.application.mock_automation_specification import matching_shadow_events
        ctx = prepare_panic_final(self, unresolved=True)
        reasons = [reason for event in ctx.repository.load_evaluations(ctx.run['run_id']) for reason in event['reasons']]
        self.assertIn('panic_price_setup_news_unresolved', reasons)
        from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
        config = {'version': 'section12-minute-shadow/v1', 'family': ctx.request.family,
            'signal_valid_seconds': 30, 'symbols': ['005930'], 'strategy': ctx.parameters}
        with self.assertRaises(ValueError):
            CandidateMonitor.from_json(ctx.store, json.dumps(config), poll_seconds=2,
                universe_max_age_seconds=180, now_provider=lambda: ctx.clock[0])
        self.assertEqual((), matching_shadow_events(ctx.publication,
            tuple(ctx.repository.load_candidate_events(ctx.run['run_id']))))


def prepare_panic_native(case, variant='P0', native_factory=None, *, stopped='continue_original_plan/v1', ready_http=False,
                         defer_admission=False):
    from unittest.mock import patch
    from kiwoom_monitor.application.market_session_schedule import KST
    from kiwoom_monitor.application.mock_automation_specification import build_mock_automation_publication_documents, publish_ready_mock_automation_spec
    from kiwoom_monitor.application.mock_automation_admission import admit_mock_automation, execution_run_id_for_spec
    from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
    from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
    from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
    from kiwoom_monitor.domain.execution_activation import (
        PANIC_NATIVE_PLAN_MODE, PanicMockExecutionPolicy, forward_spec_from_dict,
        mock_automation_spec_from_dict, stage_revision_from_dict,
    )
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
    from tests.unit.test_mock_automation_specification import _FakeBrokerTransport, PROFILE_ID
    ctx = prepare_panic_final(case, variant, native_factory=native_factory)
    ctx.research_parameters = deepcopy(ctx.parameters)
    ctx.research_event = deepcopy(ctx.events[0])
    day = datetime.fromisoformat(ctx.publication['package']['source_final']['finished_at']).astimezone(KST).date() + timedelta(days=1)
    while day.weekday() >= 5: day += timedelta(days=1)
    stamp = lambda clock: f'{day}T{clock}:00+09:00'
    current = deepcopy(ctx.parameters)
    current['news_proofs'] = [{'symbol': '005930', 'source': 'synthetic-current-news',
        'assessment_id': f'forward-assessment-{day}', 'revision': f'forward-news-{day}',
        'effective_at': stamp('08:50'), 'available_at': stamp('08:55'), 'disposition': 'supported_nonfundamental',
        'evidence_refs': [f'forward-article-{day}', f'forward-body-{day}', f'forward-rule-{day}']}]
    config = {'version': 'section12-minute-shadow/v1', 'family': 'panic_rebound/v1',
        'signal_valid_seconds': 30, 'symbols': ['005930'], 'strategy': current}
    ctx.settings = {'revision': 1, 'shadow_candidate_enabled': True, 'shadow_candidate_config': config,
                    'shadow_candidate_universe_max_age_seconds': 180}
    ctx.store.upsert_documents('server_operational_settings', [{'owner': 'global', 'key': 'current', 'document': ctx.settings}])
    ctx.clock[0] = datetime.fromisoformat(stamp('09:00'))
    monitor = CandidateMonitor.from_json(ctx.store, json.dumps(config), poll_seconds=2,
        universe_max_age_seconds=180, now_provider=lambda: ctx.clock[0])
    ctx.shadow_monitor = monitor
    monitor.run_once()
    prices = (1050, 1040, 1030, 945, 950)
    for index, price in enumerate(prices[:4 if variant == 'P0' else 5]):
        start = datetime.fromisoformat(stamp('09:00')) + timedelta(minutes=index)
        ctx.clock[0] = start + timedelta(minutes=1, seconds=2)
        key = dict(trading_date=day.isoformat(), minute=start.strftime('%H:%M'), code='005930', market='KRX')
        ctx.store.save_minute_bars([{**key, 'open': price, 'high': price + 5, 'low': price - 5,
            'close': price, 'volume': 100, 'trade_value_million_won': 1, 'updated_at': ctx.clock[0].timestamp(),
            'operation_id': f'forward-write-{day}-{index}'}])
        ctx.store.finalize_minute_bars([{**key, 'available_at': ctx.clock[0].timestamp(), 'capture_quality': 'complete',
            'finalization_source': 'timer', 'operation_id': f'forward-final-{day}-{index}'}])
        monitor.run_once()
    ctx.event, = [event for event in ctx.store.load_shadow_candidates()['events'] if event['run_id'] == monitor.monitor_id]
    case.assertNotEqual(ctx.research_event['signal_reference_price'], ctx.event['signal_reference_price'])
    case.assertNotEqual(ctx.research_parameters['news_proofs'], ctx.event['panic_entry_parameters']['news_proofs'])
    documents = build_mock_automation_publication_documents(candidate_publication=ctx.publication,
        binding=ctx.inputs.binding, credential_profile_id=PROFILE_ID, shadow_event=ctx.event,
        evaluation_start=ctx.clock[0], evaluation_end=ctx.clock[0] + timedelta(hours=5), frozen_at=ctx.clock[0],
        criteria=ctx.inputs.profile.criteria, limits={name: getattr(ctx.inputs.spec, name) for name in (
            'maximum_concurrent_strategies', 'maximum_concurrent_positions', 'maximum_capital_won',
            'maximum_daily_loss_won', 'maximum_data_gap_seconds', 'maximum_submission_unknown_count',
            'maximum_reconnect_count', 'maximum_balance_mismatch_count')},
        execution_plan_mode=PANIC_NATIVE_PLAN_MODE, panic_execution_policy=PanicMockExecutionPolicy(100, 500, stopped))
    ctx.spec = mock_automation_spec_from_dict(documents['operating_spec'])
    case.assertEqual(ctx.spec, mock_automation_spec_from_dict(ctx.spec.to_dict()))
    case.assertNotIn('panic_execution_policy', ctx.inputs.spec.to_dict())
    ctx.repository = ctx.inputs.repository
    if ready_http:
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        from tests.unit.test_mock_automation_specification import mock_publication_contract_app
        # Reuse the actual already-verified binding. A second fixture seed would
        # append a new revision immediately before posting the frozen document.
        with patch('tests.unit.test_mock_automation_specification.mock_specification_inputs', return_value=ctx.inputs), \
                mock_publication_contract_app(create_app, ctx.root/'native.sqlite3') as (app, _, _):
            with TestClient(app) as client:
                headers = {'Authorization': 'Bearer private-token'}
                for status in ('saved', 'unchanged'):
                    reply = client.post('/api/v1/research/mock-automation-specs', headers=headers, json=documents)
                    case.assertEqual(200, reply.status_code, reply.text)
                    case.assertEqual((status, 'READY', False),
                        (reply.json()['status'], reply.json()['readiness'], reply.json()['orders_started']))
                reply = client.get('/api/v1/research/mock-automation-specs/' + ctx.spec.account_scope.account_ref, headers=headers)
                case.assertEqual(200, reply.status_code, reply.text)
                row, = reply.json()['specs']
                case.assertEqual(('READY', ctx.spec.to_dict()), (row['readiness'], row['spec']))
                changed = deepcopy(documents); changed['expected_binding_revision'] += 1
                case.assertEqual(409, client.post('/api/v1/research/mock-automation-specs', headers=headers, json=changed).status_code)
    else:
        for changed in (True, False):
            readiness, actual = publish_ready_mock_automation_spec(ctx.repository, profile=forward_spec_from_dict(documents['forward_profile']),
                stage_revisions=tuple(stage_revision_from_dict(row) for row in documents['stage_revisions']), spec=ctx.spec,
                shadow_event=ctx.event, current_binding=ctx.inputs.binding)
            case.assertEqual(('READY', changed), (readiness.status.value, actual))
    case.enterContext(patch.object(ctx.store, '_execution_wall_now', lambda: ctx.clock[0], create=True))
    ctx.execution = ExecutionRepository(ctx.store)
    ctx.transport = _FakeBrokerTransport(lambda: ctx.clock[0])
    ctx.runtime = ExecutionRuntime(OrderLifecycle(ctx.execution, ctx.transport, now_provider=lambda: ctx.clock[0]), ctx.execution,
        account_ref=ctx.spec.account_scope.account_ref, run_id=execution_run_id_for_spec(ctx.spec.spec_id),
        owner_token='panic-native-test', now_provider=lambda: ctx.clock[0])
    case.addCleanup(ctx.runtime.stop)
    if not defer_admission:
        admit_mock_automation(ctx.repository, ctx.repository, ctx.runtime, account_ref=ctx.runtime.account_ref,
            spec_id=ctx.spec.spec_id, requested_at=ctx.clock[0])
    ctx.revision = 0
    ctx.quote = dict(price_won=ctx.event['signal_reference_price'], available_at=ctx.clock[0],
        input_ref='collector-trade:panic-forward-entry', durable=False, market_status='TRADABLE')
    if not defer_admission:
        refresh_panic_cut(ctx, {})
    return ctx


def refresh_panic_cut(ctx, positions):
    from tests.unit.test_mock_automation_account_execution import native_account_observation
    from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
    from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
    from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
    from kiwoom_monitor.domain.order_contract import AccountSnapshot
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
    events = ctx.execution.account_events('mock', ctx.runtime.account_ref).events
    gross = {}; cash = 100_000
    for event in events:
        if event.event_type != 'FILL': continue
        key = (event.occurred_at.date(), event.symbol, event.side)
        value = event.quantity * event.price
        gross[key] = gross.get(key, 0) + value
        cash += -value if event.side == 'BUY' else value
    costs = tuple(DailyTradeCost(day, day, symbol, '매수' if side == 'BUY' else '매도', value, value, 0, 0, 0,
        origin_scope=ctx.spec.account_scope) for (day, symbol, side), value in gross.items())
    ctx.recovery = MockAccountRecovery(AccountSnapshot(ctx.runtime.account_ref, cash, 0, positions, ctx.clock[0]), ())
    ctx.revision += 1
    ctx.risk = build_mock_automation_risk_snapshot(ctx.recovery, events, costs,
        ctx.execution.active_intents('mock', ctx.runtime.account_ref, ctx.runtime.run_id),
        execution_run_id=ctx.runtime.run_id, binding_revision=ctx.spec.binding_revision,
        reconciliation_revision=ctx.revision, observed_at=ctx.clock[0], broker_query_started_at=ctx.clock[0],
        broker_query_completed_at=ctx.clock[0])
    ctx.repository.save_mock_automation_risk_snapshot(ctx.risk)
    record_mock_automation_recovery_from_risk(ctx.repository, ctx.runtime, ctx.recovery, ctx.risk,
        account_ref=ctx.runtime.account_ref, spec_id=ctx.spec.spec_id,
        observation=native_account_observation(ctx, ctx.recovery, ctx.risk))


def panic_entry(ctx):
    from kiwoom_monitor.application.panic_mock_execution import dispatch_panic_entry
    return dispatch_panic_entry(ctx.repository, ctx.runtime, ctx.spec, parameters=ctx.parameters, event=ctx.event,
        quote=ctx.quote, recovery=ctx.recovery, risk=ctx.risk)


def fill_panic_entry(case, ctx, buy):
    from unittest.mock import patch
    from kiwoom_monitor.domain.order_contract import BrokerFill, BrokerOrderSnapshot, OrderState
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
    ctx.clock[0] += timedelta(seconds=1)
    first = BrokerFill('panic-partial', 2, buy.intent.limit_price, ctx.clock[0])
    ctx.runtime.reconcile(buy.intent.intent_id, BrokerOrderSnapshot(buy.broker_order_id, ctx.runtime.account_ref,
        buy.intent.symbol, OrderState.PARTIALLY_FILLED, 2, 2, ctx.clock[0], (first,)))
    with patch.object(ctx.transport, 'cancel', side_effect=SubmissionUnknown('test cancellation response lost')):
        case.assertEqual(OrderState.CANCEL_PENDING, ctx.runtime.cancel(buy.intent.intent_id).state)
    ctx.clock[0] += timedelta(seconds=1)
    late = BrokerFill('panic-late', 1, buy.intent.limit_price, ctx.clock[0])
    current = ctx.runtime.reconcile(buy.intent.intent_id, BrokerOrderSnapshot(buy.broker_order_id, ctx.runtime.account_ref,
        buy.intent.symbol, OrderState.CANCELLED, 3, 0, ctx.clock[0], (first, late)))
    case.assertEqual((OrderState.CANCELLED, 3), (current.state, current.detailed_filled_quantity))
    ctx.buy = current
    refresh_panic_cut(ctx, {buy.intent.symbol: 3})
    return ctx.execution.load_plan_position(ctx.runtime.account_ref, ctx.runtime.run_id, buy.intent.symbol, buy.intent.plan)


def panic_exit(ctx, plan, price):
    from kiwoom_monitor.application.panic_mock_execution import dispatch_panic_exit
    quote = {**ctx.quote, 'price_won': price, 'available_at': ctx.clock[0], 'input_ref': 'collector-trade:panic-current-exit'}
    return dispatch_panic_exit(ctx.repository, ctx.runtime, ctx.execution, ctx.spec, plan, parameters=ctx.parameters,
        event=ctx.event, trigger_quote=quote, quote=quote, recovery=ctx.recovery, risk=ctx.risk)


def verify_panic_native_cycle(case, variant='P0', native_factory=None):
    from kiwoom_monitor.application.panic_mock_execution import build_panic_entry_evidence
    from kiwoom_monitor.domain.order_contract import BrokerFill, BrokerOrderSnapshot, OrderState
    ctx = prepare_panic_native(case, variant, native_factory)
    setup = ctx.event['panic_entry_setup']
    for price in (setup['failure_price'], setup['target_price'], ctx.event['signal_reference_price'] * 101 // 100 + 1):
        with case.assertRaisesRegex(ValueError, 'current entry'):
            build_panic_entry_evidence(ctx.spec, run_id=ctx.runtime.run_id, parameters=ctx.parameters, event=ctx.event,
                quote={**ctx.quote, 'price_won': price}, decided_at=ctx.clock[0], now=ctx.clock[0])
    case.assertEqual([], ctx.transport.calls)
    gate, buy, receipt = panic_entry(ctx); case.assertIsNotNone(buy, gate)
    case.assertEqual((4, ctx.event['signal_reference_price']), (buy.intent.quantity, buy.intent.limit_price))
    case.assertEqual(receipt, panic_entry(ctx)[2])
    before = ctx.execution.events(buy.intent.intent_id)
    with case.assertRaisesRegex(RuntimeError, 'PLANNED_AMENDMENT_ASSESSMENT_REQUIRED'):
        ctx.runtime._lifecycle.amend(buy.intent.intent_id, 4, buy.intent.limit_price + 1)
    case.assertEqual(before, ctx.execution.events(buy.intent.intent_id))
    head = fill_panic_entry(case, ctx, buy)
    case.assertEqual((None, None, None), panic_exit(ctx, head.plan, setup['failure_price'] + 1))
    ctx.store.upsert_documents('server_operational_settings', [{'owner': 'global', 'key': 'current',
        'document': {**ctx.settings, 'shadow_candidate_enabled': False}}])
    gate, sell, sell_receipt = panic_exit(ctx, head.plan, setup['target_price'])
    case.assertIsNotNone(sell, gate)
    case.assertEqual((3, head.plan), (sell.intent.quantity, sell.intent.plan))
    ctx.clock[0] += timedelta(seconds=1)
    # A confirmed broker cancellation after one actual SELL fill leaves two owned shares.
    ctx.runtime.reconcile(sell.intent.intent_id, BrokerOrderSnapshot(sell.broker_order_id, ctx.runtime.account_ref,
        sell.intent.symbol, OrderState.CANCELLED, 1, 0, ctx.clock[0],
        (BrokerFill('panic-exit-partial', 1, sell.intent.limit_price, ctx.clock[0]),)))
    refresh_panic_cut(ctx, {sell.intent.symbol: 2})
    case.assertEqual(sell_receipt, panic_exit(ctx, head.plan, setup['target_price'])[2])
    remainder = ctx.execution.load_plan_position(ctx.runtime.account_ref, ctx.runtime.run_id, sell.intent.symbol, head.plan)
    case.assertEqual(2, remainder.quantity)
    case.assertNotEqual(head.plan, remainder.plan)
    gate, residual, _ = panic_exit(ctx, remainder.plan, setup['failure_price'])
    case.assertIsNotNone(residual, gate)
    case.assertEqual((2, remainder.plan), (residual.intent.quantity, residual.intent.plan))
    ctx.clock[0] += timedelta(seconds=1)
    ctx.runtime.reconcile(residual.intent.intent_id, BrokerOrderSnapshot(residual.broker_order_id, ctx.runtime.account_ref,
        residual.intent.symbol, OrderState.FILLED, 2, 0, ctx.clock[0],
        (BrokerFill('panic-exit-residual', 2, residual.intent.limit_price, ctx.clock[0]),)))
    refresh_panic_cut(ctx, {})
    case.assertEqual(['BUY', 'SELL', 'SELL'], [row[1] for row in ctx.transport.calls])
    final = ctx.execution.load_plan_position(ctx.runtime.account_ref, ctx.runtime.run_id, sell.intent.symbol, remainder.plan)
    case.assertEqual(0, final.quantity)
    return ctx


def verify_panic_native_claim_fences(case, native_factory=None):
    from unittest.mock import patch
    from kiwoom_monitor.application.panic_mock_execution import PANIC_ENTRY_COLLECTION, PANIC_EXIT_COLLECTION, panic_exit_key
    ctx = prepare_panic_native(case, native_factory=native_factory)
    submit = ctx.runtime.submit_automation_intent
    for boundary in ('settings', 'entry-proof'):
        originals = []
        def change_entry(intent, account, **kwargs):
            if boundary == 'settings':
                collection, key, original = 'server_operational_settings', 'current', ctx.settings
                changed = {**original, 'shadow_candidate_enabled': False}
                owner = 'global'
            else:
                collection, key, owner = PANIC_ENTRY_COLLECTION, intent.plan.plan_id, ctx.runtime.account_ref
                original = ctx.repository.load_panic_entry_evidence(owner, key)
                changed = deepcopy(original)
                changed['snapshot']['candidate']['panic_entry_setup']['target_price'] += 1
            originals.append((collection, owner, key, original))
            ctx.store.upsert_documents(collection, [{'owner': owner, 'key': key, 'document': changed}])
            return submit(intent, account, **kwargs)
        with patch.object(ctx.runtime, 'submit_automation_intent', side_effect=change_entry):
            with case.assertRaisesRegex(RuntimeError, 'PANIC_CURRENT_SHADOW_CHANGED|PANIC_ENTRY_PROOF_OR_ORDER_CHANGED'):
                panic_entry(ctx)
        case.assertEqual([], ctx.transport.calls)
        case.assertEqual((), ctx.runtime.load_active_intents())
        collection, owner, key, original = originals[0]
        ctx.store.upsert_documents(collection, [{'owner': owner, 'key': key, 'document': original}])
    _, buy, _ = panic_entry(ctx)
    head = fill_panic_entry(case, ctx, buy)
    original = []
    def change_exit(intent, account, **kwargs):
        row = ctx.repository.load_panic_exit_evidence(ctx.runtime.account_ref, panic_exit_key(head.plan))
        original.append(row)
        changed = deepcopy(row); changed['snapshot']['quantity'] += 1
        ctx.store.upsert_documents(PANIC_EXIT_COLLECTION, [{'owner': ctx.runtime.account_ref,
            'key': panic_exit_key(head.plan), 'document': changed}])
        return submit(intent, account, **kwargs)
    with patch.object(ctx.runtime, 'submit_automation_intent', side_effect=change_exit):
        with case.assertRaisesRegex(RuntimeError, 'PANIC_EXIT_PROOF_OR_ORDER_CHANGED'):
            panic_exit(ctx, head.plan, ctx.event['panic_entry_setup']['target_price'])
    case.assertEqual(['BUY'], [row[1] for row in ctx.transport.calls])
    case.assertEqual(head, ctx.execution.load_plan_position(ctx.runtime.account_ref, ctx.runtime.run_id, buy.intent.symbol, head.plan))
    ctx.store.upsert_documents(PANIC_EXIT_COLLECTION, [{'owner': ctx.runtime.account_ref,
        'key': panic_exit_key(head.plan), 'document': original[0]}])
    gate, sell, _ = panic_exit(ctx, head.plan, ctx.event['panic_entry_setup']['target_price'])
    case.assertIsNotNone(sell, gate)
    case.assertEqual((3, head.plan), (sell.intent.quantity, sell.intent.plan))


def verify_panic_stopped_management(case, native_factory=None, *, stopped='continue_original_plan/v1'):
    from unittest.mock import patch
    from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation
    from kiwoom_monitor.application.panic_mock_execution import panic_exit_key
    from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
    from kiwoom_monitor.central_server.database import SQLiteQueryStore
    from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
    from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
    ctx = prepare_panic_native(case, native_factory=native_factory, stopped=stopped)
    _, buy, _ = panic_entry(ctx)
    head = fill_panic_entry(case, ctx, buy)
    entry = ctx.repository.load_panic_entry_evidence(ctx.runtime.account_ref, head.plan.plan_id)
    control = ctx.repository.load_mock_automation_control(ctx.runtime.account_ref)
    emergency_stop_mock_automation(ctx.repository, ctx.runtime, account_ref=ctx.runtime.account_ref,
        spec_id=ctx.spec.spec_id, stopped_at=ctx.clock[0], reason='isolated PANIC original-plan stop',
        expected_control_revision=control.control_revision)
    stopped_control = ctx.repository.load_mock_automation_control(ctx.runtime.account_ref)
    case.assertEqual('STOPPED', stopped_control.desired_state.value)
    ctx.store.upsert_documents('server_operational_settings', [{'owner': 'global', 'key': 'current',
        'document': {**ctx.settings, 'shadow_candidate_enabled': False}}])
    ctx.clock[0] += timedelta(milliseconds=1)
    refresh_panic_cut(ctx, {buy.intent.symbol: 3})
    if stopped == 'reconcile_only/v1':
        gate, order, _ = panic_exit(ctx, head.plan, ctx.event['panic_entry_setup']['target_price'])
        case.assertIsNone(order)
        case.assertIn('AUTOMATION_CONTROL_NOT_RUNNING', gate.reasons)
        case.assertEqual(['BUY'], [row[1] for row in ctx.transport.calls])
        return

    def reopen(at):
        account, run_id = ctx.runtime.account_ref, ctx.runtime.run_id
        case.assertTrue(ctx.runtime.stop())
        ctx.store.close()
        ctx.clock[0] = at
        ctx.store = native_factory(ctx.root / 'native.sqlite3', lambda: ctx.clock[0]) if native_factory else SQLiteQueryStore(
            ctx.root / 'native.sqlite3', now_provider=lambda: ctx.clock[0])
        ctx.store.initialize(); case.addCleanup(ctx.store.close)
        case.enterContext(patch.object(ctx.store, '_execution_wall_now', lambda: ctx.clock[0], create=True))
        ctx.repository = ForwardEvaluationRepository(ctx.store)
        ctx.execution = ExecutionRepository(ctx.store)
        ctx.runtime = ExecutionRuntime(OrderLifecycle(ctx.execution, ctx.transport, now_provider=lambda: ctx.clock[0]),
            ctx.execution, account_ref=account, run_id=run_id, owner_token=f'panic-restored-{at.date()}',
            now_provider=lambda: ctx.clock[0])
        case.addCleanup(ctx.runtime.stop)
        ctx.runtime.start(new_orders_enabled=False)
        refresh_panic_cut(ctx, {buy.intent.symbol: 3})

    reopen(ctx.clock[0].replace(hour=16, minute=0, second=0, microsecond=0))
    gate, order, _ = panic_exit(ctx, head.plan, ctx.event['panic_entry_setup']['failure_price'] + 1)
    case.assertIsNone(order)
    # 16:00 is an existing KRX after-hours session, outside this regular-only profile.
    case.assertIn('SESSION_PROFILE_MISMATCH', gate.reasons)
    case.assertEqual(['BUY'], [row[1] for row in ctx.transport.calls])
    following = ctx.clock[0] + timedelta(days=1)
    while following.weekday() >= 5: following += timedelta(days=1)
    reopen(following.replace(hour=9, minute=5))
    gate, sell, _ = panic_exit(ctx, head.plan, ctx.event['panic_entry_setup']['failure_price'] + 1)
    case.assertIsNotNone(sell, gate)
    case.assertEqual((3, head.plan), (sell.intent.quantity, sell.intent.plan))
    evidence = ctx.repository.load_panic_exit_evidence(ctx.runtime.account_ref, panic_exit_key(head.plan))
    case.assertEqual('panic_intraday_management_overdue_0B', evidence['snapshot']['reason'])
    case.assertEqual(entry, ctx.repository.load_panic_entry_evidence(ctx.runtime.account_ref, head.plan.plan_id))
    case.assertEqual(stopped_control, ctx.repository.load_mock_automation_control(ctx.runtime.account_ref))
    case.assertEqual(['BUY', 'SELL'], [row[1] for row in ctx.transport.calls])


class PanicNativePlanTests(unittest.TestCase):
    def test_explicit_panic_ready_roundtrips_through_authenticated_publication_without_starting_orders(self):
        ctx = prepare_panic_native(self, ready_http=True)
        self.assertEqual([], ctx.transport.calls)
        self.assertEqual((), ctx.runtime.load_active_intents())

    def test_both_variants_use_current_news_prices_and_only_detailed_owned_quantity(self):
        for variant in ('P0', 'P1'):
            with self.subTest(variant=variant): verify_panic_native_cycle(self, variant)

    def test_native_claim_rechecks_current_settings_original_setup_and_actual_owned_exit(self):
        verify_panic_native_claim_fences(self)

    def test_stopped_original_plan_survives_closed_session_and_next_day_reopen(self):
        verify_panic_stopped_management(self)

    def test_explicit_reconcile_only_stop_does_not_send_protective_orders(self):
        verify_panic_stopped_management(self, stopped='reconcile_only/v1')


async def panic_management_context(case, native_factory=None, variant='P0', *, empty=False):
    from unittest.mock import patch
    from tests.unit.test_close_order_management import BrokerBook
    from kiwoom_monitor.central_server.execution_runtime import ManualMockOrderGateway
    from kiwoom_monitor.central_server.mock_account_monitor import MockAccountMonitor
    from kiwoom_monitor.domain.order_contract import BrokerSubmission, OrderState
    ctx = prepare_panic_native(case, variant, native_factory)
    if not empty:
        _, ctx.buy, _ = panic_entry(ctx)
        ctx.clock[0] += timedelta(seconds=10)
    ctx.book = BrokerBook(ctx, empty=empty)

    def cancel(intent, number, quantity=0):
        event = ctx.execution.events(intent.intent_id)[-1]
        case.assertEqual(('CANCEL_STARTED', 'CANCEL'),
            (event['event_type'], event['panic_order_management_claim']['action']))
        ctx.book.mutations.append(('CANCEL', number, quantity))
        if getattr(ctx, 'cancel_unknown', False):
            from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
            ctx.book.rows[number]['state'] = OrderState.CANCEL_PENDING
            raise SubmissionUnknown('isolated broker cancellation confirmation is delayed')
        ctx.book.rows[number].update(remaining=0, state=OrderState.CANCELLED)
        return BrokerSubmission('panic-cancelled', ctx.clock[0])

    case.enterContext(patch.object(ctx.transport, 'amend', ctx.book.amend, create=True))
    case.enterContext(patch.object(ctx.transport, 'cancel', cancel))
    submit = ctx.transport.submit
    def record_submission(intent):
        response = submit(intent); ctx.book.add(response.broker_order_id, intent)
        if intent.side.value == 'BUY': ctx.buy = ctx.execution.load(intent.intent_id)
        return response
    case.enterContext(patch.object(ctx.transport, 'submit', record_submission))
    await start_panic_bundle(case, ctx)
    ctx.current_quote = {**ctx.quote, 'price_won': ctx.event['signal_reference_price'] + int(not empty),
                         'available_at': ctx.clock[0], 'input_ref': 'collector-trade:panic-current-amend'}
    return ctx


async def start_panic_bundle(case, ctx):
    from kiwoom_monitor.central_server.execution_runtime import ManualMockOrderGateway
    from kiwoom_monitor.central_server.mock_account_monitor import MockAccountMonitor
    monitor = MockAccountMonitor(ctx.book, ctx.runtime, ctx.execution, now_provider=lambda: ctx.clock[0],
        automation_risk_repository=ctx.repository, automation_binding_revision=ctx.spec.binding_revision,
        automation_account_scope=ctx.spec.account_scope, new_orders_enabled_on_start=False)
    await monitor.start(); monitor.set_automation_execution_inputs_enabled(True); await monitor._queue.join()
    case.addAsyncCleanup(monitor.close)
    gateway = ManualMockOrderGateway(ctx.runtime, ctx.execution, monitor.refresh_account, now_provider=lambda: ctx.clock[0],
        automation_execution_cut=lambda: monitor.latest_automation_execution_cut)
    gateway.set_manual_submit_enabled(False); case.addAsyncCleanup(gateway.close)
    ctx.bundle = SimpleNamespace(monitor=monitor, gateway=gateway)


async def manage_panic_current(ctx, record=None):
    from kiwoom_monitor.application.minute_order_management import minute_current_shadow_matches
    from kiwoom_monitor.application.panic_mock_execution import prepare_panic_order_management_claim
    from kiwoom_monitor.domain.order_contract import OrderSide
    record = record or ctx.execution.load(ctx.buy.intent.intent_id)
    control = ctx.repository.load_mock_automation_control(ctx.runtime.account_ref)
    def prepare(current, cut):
        settings = ctx.store.load_document('server_operational_settings', 'global', 'current')
        enabled = (current.intent.side is OrderSide.BUY and control.desired_state.value == 'RUNNING'
            and not control.entry_resume_required and minute_current_shadow_matches(ctx.spec, ctx.event,
                settings['document'] if settings else None))
        return prepare_panic_order_management_claim(ctx.repository, ctx.runtime, ctx.execution, ctx.spec, current,
            parameters=ctx.parameters, event=ctx.event, quote=ctx.current_quote,
            recovery=cut['recovery'], risk=cut['risk'], entry_enabled=enabled)
    return await ctx.bundle.gateway.manage_close_order(record, spec_id=ctx.spec.spec_id,
        control_revision=control.control_revision, prepare_management=prepare)


async def verify_panic_current_buy(case, native_factory=None, *, variant='P0', lost_ack=False):
    from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
    from kiwoom_monitor.domain.order_contract import BrokerFill, OrderState
    ctx = await panic_management_context(case, native_factory, variant)
    case.assertGreater(ctx.clock[0], ctx.buy.intent.expires_at)
    ctx.book.lost_ack = lost_ack
    current = await manage_panic_current(ctx)
    case.assertEqual([('AMEND', ctx.buy.broker_order_id, 'amended-1', 3, ctx.buy.intent.limit_price + 1)], ctx.book.mutations)
    case.assertEqual(ctx.buy.intent, current.intent)
    if lost_ack:
        case.assertEqual(OrderState.SUBMISSION_UNKNOWN, current.state)
        def unexpected_claim(record): raise AssertionError('UNKNOWN amendment was automatically retried')
        again = ctx.runtime.manage_close_order(current, spec_id=ctx.spec.spec_id,
            control_revision=ctx.repository.load_mock_automation_control(ctx.runtime.account_ref).control_revision,
            build_claim=unexpected_claim)
        case.assertEqual(current, again)
        await ctx.bundle.monitor.refresh_recovery()
        case.assertEqual('ACCEPTED', ctx.execution.load(current.intent.intent_id).amendment.status)
        case.assertEqual(1, len(ctx.book.mutations))
    ctx.clock[0] += timedelta(seconds=1)
    ctx.book.fill(ctx.buy.broker_order_id, 1)
    await ctx.bundle.monitor.refresh_recovery()
    ctx.current_quote.update(available_at=ctx.clock[0])
    await manage_panic_current(ctx)
    case.assertEqual(1, len(ctx.book.mutations))
    ctx.current_quote.update(price_won=ctx.event['panic_entry_setup']['failure_price'], available_at=ctx.clock[0],
                             input_ref='collector-trade:panic-current-failure')
    await manage_panic_current(ctx)
    case.assertEqual(('CANCEL', 'amended-1', 0), ctx.book.mutations[-1])
    await ctx.bundle.monitor.refresh_recovery()
    case.assertEqual((OrderState.CANCELLED, 1), (ctx.execution.load(ctx.buy.intent.intent_id).state,
        ctx.execution.load(ctx.buy.intent.intent_id).detailed_filled_quantity))
    # One actual fill preceded cancellation but arrived after its terminal report.
    ctx.clock[0] += timedelta(seconds=1)
    ctx.book.rows['amended-1']['fills'].append(BrokerFill('shared', 1, ctx.book.rows['amended-1']['price'], ctx.clock[0]))
    await ctx.bundle.monitor.refresh_recovery()
    cut = ctx.bundle.monitor.latest_automation_execution_cut
    head = ctx.execution.load_plan_position(ctx.runtime.account_ref, ctx.runtime.run_id, ctx.buy.intent.symbol, ctx.buy.intent.plan)
    case.assertEqual(2, head.quantity)
    case.assertTrue(cut['risk'].reconciliation_complete)
    record_mock_automation_recovery_from_risk(ctx.repository, ctx.runtime, cut['recovery'], cut['risk'],
        account_ref=ctx.runtime.account_ref, spec_id=ctx.spec.spec_id, observation=cut['observation'])
    ctx.recovery, ctx.risk = cut['recovery'], cut['risk']
    gate, sell, _ = panic_exit(ctx, head.plan, ctx.event['panic_entry_setup']['failure_price'])
    case.assertIsNotNone(sell, gate); case.assertEqual(2, sell.intent.quantity)
    ctx.book.fill(sell.broker_order_id, 2)
    await ctx.bundle.monitor.refresh_recovery()
    case.assertEqual({}, ctx.bundle.monitor.latest_automation_execution_cut['recovery'].account.positions)
    case.assertEqual(['BUY', 'SELL'], [row[1] for row in ctx.transport.calls])
    return ctx


async def verify_panic_current_native_recheck(case, native_factory=None):
    from unittest.mock import patch
    ctx = await panic_management_context(case, native_factory)
    amend = ctx.runtime._lifecycle.amend
    before = []
    def changed(*args, **kwargs):
        ctx.store.upsert_documents('server_operational_settings', [{'owner': 'global', 'key': 'current',
            'document': {**ctx.settings, 'shadow_candidate_enabled': False}}])
        before.append(ctx.execution.events(ctx.buy.intent.intent_id))
        return amend(*args, **kwargs)
    with patch.object(ctx.runtime._lifecycle, 'amend', side_effect=changed):
        with case.assertRaisesRegex(RuntimeError, 'PANIC_CURRENT_ORDER_ASSESSMENT_CHANGED'):
            await manage_panic_current(ctx)
    case.assertEqual(before[0], ctx.execution.events(ctx.buy.intent.intent_id))
    case.assertEqual([], ctx.book.mutations)
    await manage_panic_current(ctx)
    case.assertEqual([('CANCEL', ctx.buy.broker_order_id, 0)], ctx.book.mutations)


async def verify_panic_current_sell(case, native_factory=None):
    from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation
    from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
    from kiwoom_monitor.domain.order_contract import OrderState
    ctx = await panic_management_context(case, native_factory)
    await manage_panic_current(ctx)
    ctx.book.fill(ctx.buy.broker_order_id, 1); ctx.book.fill('amended-1', 3)
    await ctx.bundle.monitor.refresh_recovery()
    cut = ctx.bundle.monitor.latest_automation_execution_cut
    record_mock_automation_recovery_from_risk(ctx.repository, ctx.runtime, cut['recovery'], cut['risk'],
        account_ref=ctx.runtime.account_ref, spec_id=ctx.spec.spec_id, observation=cut['observation'])
    ctx.recovery, ctx.risk = cut['recovery'], cut['risk']
    head = ctx.execution.load_plan_position(ctx.runtime.account_ref, ctx.runtime.run_id, ctx.buy.intent.symbol, ctx.buy.intent.plan)
    failure = ctx.event['panic_entry_setup']['failure_price']
    gate, sell, _ = panic_exit(ctx, head.plan, failure)
    case.assertIsNotNone(sell, gate)
    case.assertEqual(4, sell.intent.quantity)
    ctx.clock[0] += timedelta(seconds=6)
    ctx.current_quote.update(price_won=failure - 1, available_at=ctx.clock[0], input_ref='collector-trade:panic-working-sell')
    control = ctx.repository.load_mock_automation_control(ctx.runtime.account_ref)
    emergency_stop_mock_automation(ctx.repository, ctx.runtime, account_ref=ctx.runtime.account_ref,
        spec_id=ctx.spec.spec_id, stopped_at=ctx.clock[0], reason='isolated PANIC current original-plan SELL',
        expected_control_revision=control.control_revision)
    ctx.clock[0] += timedelta(milliseconds=1)
    ctx.store.upsert_documents('server_operational_settings', [{'owner': 'global', 'key': 'current',
        'document': {**ctx.settings, 'shadow_candidate_enabled': False}}])
    await manage_panic_current(ctx, sell)
    case.assertGreater(ctx.clock[0], sell.intent.expires_at)
    case.assertEqual(('AMEND', sell.broker_order_id, 'amended-2', 4, failure - 1), ctx.book.mutations[-1])
    case.assertEqual(['BUY', 'SELL'], [row[1] for row in ctx.transport.calls])
    ctx.book.fill('amended-2', 4)
    await ctx.bundle.monitor.refresh_recovery()
    current = ctx.execution.load(sell.intent.intent_id)
    case.assertEqual((OrderState.FILLED, 4), (current.state, current.detailed_filled_quantity))
    case.assertEqual({}, ctx.bundle.monitor.latest_automation_execution_cut['recovery'].account.positions)
    from kiwoom_monitor.central_server.mock_automation_episode import MockAutomationEpisodePosition
    from kiwoom_monitor.application.section12_minute_research import MinuteExperiment
    position = MockAutomationEpisodePosition(account_ref=ctx.runtime.account_ref, run_id=ctx.runtime.run_id)
    position.apply_new_fills(ctx.execution, leader=False,
        config=MinuteExperiment.from_document('panic_rebound/v1', ctx.parameters), position_time=lambda: ctx.clock[0])
    case.assertEqual('cooldown', position.state.status)
    case.assertFalse(position.state.position_quantity)
    from tests.unit.test_mock_automation_specification import _ExecutionPageSource
    from kiwoom_monitor.application.journal_enrichment import sync_journal_execution_projection
    from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
    journal = JournalRepository(ctx.root / 'panic-current-journal.sqlite3')
    source = _ExecutionPageSource(ctx.execution, ctx.runtime.account_ref)
    projection = sync_journal_execution_projection(source, journal, ctx.spec.account_scope, now=ctx.clock[0])
    case.assertEqual(3, projection.detailed_fill_count)
    rows = journal.load_execution_fill_projections(ctx.spec.account_scope)
    case.assertEqual([(ctx.buy.broker_order_id, 'shared', 1), ('amended-1', 'shared', 3), ('amended-2', 'shared', 4)],
        [(row['broker_order_id'], row['broker_execution_id'], row['quantity']) for row in rows])
    case.assertEqual({ctx.buy.intent.plan.plan_id}, {row['plan']['plan_id'] for row in rows})
    case.assertEqual(0, sync_journal_execution_projection(source, journal, ctx.spec.account_scope,
        now=ctx.clock[0]).inserted_event_count)


class PanicCurrentOrderManagementTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_variants_reassess_then_amend_and_cancel_failed_remainder_with_late_fill(self):
        for variant in ('P0', 'P1'):
            with self.subTest(variant=variant):
                await verify_panic_current_buy(self, variant=variant, lost_ack=variant == 'P1')

    async def test_native_transaction_rechecks_current_context_before_amendment(self):
        await verify_panic_current_native_recheck(self)

    async def test_current_sell_amends_original_owned_remainder_after_stop_and_preserves_journal(self):
        await verify_panic_current_sell(self)


async def assemble_panic_runner(case, ctx):
    import asyncio
    from kiwoom_monitor.central_server.mock_runtime import (
        MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION, MockAccountRuntimeMode, MockAutomationRuntimeContext,
    )
    from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
    from tests.unit.test_mock_automation_specification import PROFILE_ID
    control = ctx.repository.load_mock_automation_control(ctx.runtime.account_ref)
    admission = ctx.repository.load_mock_automation_admission_for_spec(ctx.runtime.account_ref, ctx.spec.spec_id)
    ctx.bundle.account_ref, ctx.bundle.run_id = ctx.runtime.account_ref, ctx.runtime.run_id
    ctx.bundle.credential_profile_id = PROFILE_ID
    ctx.bundle.runtime, ctx.bundle.repository = ctx.runtime, ctx.execution
    ctx.bundle.runtime_mode = MockAccountRuntimeMode.AUTOMATIC
    ctx.bundle.automation_context = MockAutomationRuntimeContext(MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION,
        ctx.runtime.account_ref, ctx.runtime.run_id, ctx.spec.spec_id, admission.admission_id, control.control_revision, 1)
    async def require_settings(profile_id, *, expected_settings_revision, credential_revision):
        case.assertEqual((PROFILE_ID, 1, 1), (profile_id, expected_settings_revision, credential_revision))
    supervisor = MockAutomationSupervisor(ctx.store, SimpleNamespace(bundle=lambda _: ctx.bundle,
        require_execution_settings=require_settings), poll_seconds=3600,
        price_source=lambda symbol: ctx.current_quote if symbol == ctx.event['symbol'] else None,
        now_provider=lambda: ctx.clock[0])
    case.addAsyncCleanup(supervisor.close)
    await supervisor.activate(credential_profile_id=PROFILE_ID, account_ref=ctx.runtime.account_ref,
        spec_id=ctx.spec.spec_id, expected_settings_revision=1, credential_revision=1)
    runner = supervisor._runners[ctx.runtime.account_ref]
    async def ready():
        while runner.episode.execution_busy: await asyncio.sleep(.01)
    await asyncio.sleep(0)
    await asyncio.wait_for(ready(), 10)
    case.assertFalse(runner._task.done(), runner.episode.status)
    case.assertNotEqual('ERROR', runner.episode.status['state'])
    return supervisor, runner


async def verify_panic_runner(case, native_factory=None, *, variant='P0', kind='failure', zero_fill=False,
                              after_partial_fill=None):
    import asyncio
    from unittest.mock import patch
    from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
    from kiwoom_monitor.central_server.database import SQLiteQueryStore
    from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
    from kiwoom_monitor.domain.order_contract import BrokerFill, OrderSide, OrderState
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
    from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
    ctx = await panic_management_context(case, native_factory, variant, empty=True)
    # Explicit long fake lease covers compressed minute arrivals; no production limit changes.
    supervisor, runner = await assemble_panic_runner(case, ctx)
    ctx.runtime.start(lease_seconds=3600, new_orders_enabled=False)
    case.assertEqual([], ctx.transport.calls)
    start = ctx.clock[0].replace(second=0, microsecond=0)
    # A continuing decline is intentionally not a new signal. Reset the declared
    # three-minute window before presenting a distinct 10 percent decline.
    for index, price in enumerate((1000, 1000, 1000, 900) if variant == 'P0' else (1000, 1000, 1000, 900, 910)):
        at = start + timedelta(minutes=index)
        ctx.clock[0] = at + timedelta(minutes=1, seconds=2)
        key = dict(trading_date=at.date().isoformat(), minute=at.strftime('%H:%M'), code=ctx.event['symbol'], market='KRX')
        ctx.store.save_minute_bars([{**key, 'open': price, 'high': price + 5, 'low': price - 5, 'close': price,
            'volume': 100, 'trade_value_million_won': 1, 'updated_at': ctx.clock[0].timestamp(),
            'operation_id': f'panic-runner-write-{index}'}])
        ctx.store.finalize_minute_bars([{**key, 'available_at': ctx.clock[0].timestamp(), 'capture_quality': 'complete',
            'finalization_source': 'timer', 'operation_id': f'panic-runner-final-{index}'}])
        ctx.shadow_monitor.run_once()
    ctx.current_quote.update(price_won=price, available_at=ctx.clock[0], input_ref='collector-trade:panic-runner-entry')
    await runner.episode.advance_once()
    active = ctx.execution.active_intents('mock', ctx.runtime.account_ref, ctx.runtime.run_id)
    case.assertEqual(1, len(active), dict(status=runner.episode.status,
        candidates=[(event['event_id'], event['available_at'], event['signal_reference_price'])
                    for event in ctx.store.load_shadow_candidates()['events']]))
    buy, = active
    ctx.buy = buy
    case.assertEqual((OrderState.ACCEPTED, OrderSide.BUY, 4, price),
        (buy.state, buy.intent.side, buy.intent.quantity, buy.intent.limit_price))
    proof = ctx.repository.load_panic_entry_evidence(ctx.runtime.account_ref, buy.intent.plan.plan_id)
    ctx.event = proof['snapshot']['candidate']
    if not zero_fill: ctx.book.fill(buy.broker_order_id, 2, 'panic-runner-partial')
    await ctx.bundle.monitor.refresh_recovery()
    await runner.episode.advance_once()
    if after_partial_fill is not None:
        await after_partial_fill(ctx, runner)
    setup = ctx.event['panic_entry_setup']
    first = setup['failure_price'] if kind == 'failure' else setup['target_price']
    ctx.clock[0] += timedelta(seconds=1)
    first_quote = {**ctx.current_quote, 'price_won': first, 'available_at': ctx.clock[0],
                    'input_ref': f'collector-trade:panic-first-{kind}'}
    rebound = {**first_quote, 'price_won': setup['failure_price'] + 25,
               'input_ref': 'collector-trade:panic-later-in-range'}
    ctx.cancel_unknown = True
    async def cancelled():
        while not ctx.book.mutations or runner.episode.execution_busy: await asyncio.sleep(.01)
    original_cancel = ctx.runtime._lifecycle.cancel
    rejected = []
    def change_first_checkpoint(*args, **kwargs):
        if kind != 'failure' or zero_fill or rejected: return original_cancel(*args, **kwargs)
        row = ctx.store.load_documents('execution_mock_automation_runner_current', ctx.runtime.account_ref, 1)[0]['document']
        changed = deepcopy(row)
        changed['minute_management']['triggers'][buy.intent.plan.plan_id]['quote']['price_won'] += 1
        before = ctx.execution.events(buy.intent.intent_id)
        ctx.store.upsert_documents('execution_mock_automation_runner_current', [{'owner': ctx.runtime.account_ref,
            'key': 'current', 'document': changed}])
        with case.assertRaisesRegex(RuntimeError, 'PANIC_FIRST_TRIGGER_CHECKPOINT_CHANGED'):
            original_cancel(*args, **kwargs)
        case.assertEqual(before, ctx.execution.events(buy.intent.intent_id))
        case.assertEqual([], ctx.book.mutations)
        rejected.append(True)
        ctx.store.upsert_documents('execution_mock_automation_runner_current', [{'owner': ctx.runtime.account_ref,
            'key': 'current', 'document': row}])
        return original_cancel(*args, **kwargs)
    with patch.object(ctx.runtime._lifecycle, 'cancel', side_effect=change_first_checkpoint):
        with patch.object(ctx.store, 'load_documents', side_effect=AssertionError('0B callback read DB')), \
                patch.object(ctx.store, 'upsert_documents', side_effect=AssertionError('0B callback wrote DB')):
            supervisor.observe_automation_price(ctx.event['symbol'], first_quote)
            supervisor.observe_automation_price(ctx.event['symbol'], rebound)
        ctx.current_quote = rebound
        await asyncio.wait_for(cancelled(), 15)
    if kind == 'failure' and not zero_fill: case.assertEqual([True], rejected)
    await ctx.bundle.monitor.refresh_recovery()
    case.assertEqual([('CANCEL', buy.broker_order_id, 0)], ctx.book.mutations)
    case.assertEqual(OrderState.CANCEL_PENDING, ctx.execution.load(buy.intent.intent_id).state)
    checkpoint = ctx.store.load_documents('execution_mock_automation_runner_current', ctx.runtime.account_ref, 1)[0]['document']
    trigger = checkpoint['minute_management']['triggers'][buy.intent.plan.plan_id]
    case.assertEqual((first_quote['input_ref'], first), (trigger['quote']['input_ref'], trigger['quote']['price_won']))
    ctx.clock[0] += timedelta(seconds=1)
    row = ctx.book.rows[buy.broker_order_id]
    row.update(remaining=0, state=OrderState.CANCELLED)
    await ctx.bundle.monitor.refresh_recovery()
    if zero_fill:
        await runner.episode.advance_once()
        case.assertNotIn(buy.intent.plan.plan_id, runner.episode._minute_management._plans)
    row['fills'].append(BrokerFill('panic-runner-late', 1, buy.intent.limit_price, ctx.clock[0]))
    await ctx.bundle.monitor.refresh_recovery()
    head = ctx.execution.load_plan_position(ctx.runtime.account_ref, ctx.runtime.run_id, buy.intent.symbol, buy.intent.plan)
    case.assertEqual(1 if zero_fill else 3, head.quantity)
    preclose_sell = None
    if zero_fill:
        ctx.current_quote.update(available_at=ctx.clock[0])
        await runner.episode.advance_once()
        restored, = ctx.execution.active_intents('mock', ctx.runtime.account_ref, ctx.runtime.run_id)
        case.assertEqual((OrderSide.SELL, 1, buy.intent.plan.plan_id),
            (restored.intent.side, restored.intent.quantity, restored.intent.plan.plan_id))
        preclose_sell = restored.intent.intent_id
    async def reopen(at):
        nonlocal supervisor, runner
        await supervisor.close(); await ctx.bundle.gateway.close(); await ctx.bundle.monitor.close()
        ctx.runtime.stop()  # Supervisor retirement may already have released the same lease.
        with closing(ctx.store._connect()) as connection:
            reader = connection.cursor(); reader.execute('SELECT COUNT(*) FROM central_execution_runtime_leases')
            case.assertEqual(0, reader.fetchone()[0]); reader.close()
        ctx.store.close()
        ctx.clock[0] = at
        ctx.store = (native_factory(ctx.root/'native.sqlite3', lambda: ctx.clock[0]) if native_factory else
            SQLiteQueryStore(ctx.root/'native.sqlite3', now_provider=lambda: ctx.clock[0]))
        ctx.store.initialize(); case.addCleanup(ctx.store.close)
        case.enterContext(patch.object(ctx.store, '_execution_wall_now', lambda: ctx.clock[0], create=True))
        ctx.execution, ctx.repository = ExecutionRepository(ctx.store), ForwardEvaluationRepository(ctx.store)
        ctx.runtime = ExecutionRuntime(OrderLifecycle(ctx.execution, ctx.transport, now_provider=lambda: ctx.clock[0]),
            ctx.execution, account_ref=buy.intent.account_ref, run_id=buy.intent.run_id, owner_token=f'panic-runner-restored-{at.date()}',
            now_provider=lambda: ctx.clock[0])
        case.addCleanup(ctx.runtime.stop); ctx.runtime.start(new_orders_enabled=False)
        ctx.store.upsert_documents('server_operational_settings', [{'owner': 'global', 'key': 'current',
            'document': {**ctx.settings, 'shadow_candidate_enabled': False}}])
        ctx.current_quote.update(available_at=ctx.clock[0])
        await start_panic_bundle(case, ctx)
        with patch.object(ctx.store, 'load_shadow_candidates', side_effect=AssertionError('restored plan rescanned Shadow history')):
            supervisor, runner = await assemble_panic_runner(case, ctx)

    if kind == 'rebound':
        await reopen(ctx.clock[0].replace(hour=16, minute=0, second=0, microsecond=0))
        await runner.episode.advance_once()
        case.assertEqual([], [record for record in ctx.runtime.load_active_intents() if record.intent.side is OrderSide.SELL])
        case.assertIn('SESSION_PROFILE_MISMATCH', runner.episode.status['reason'])
        case.assertEqual(['BUY'], [item[1] for item in ctx.transport.calls])
        following = ctx.clock[0] + timedelta(days=1)
        while following.weekday() >= 5: following += timedelta(days=1)
        await reopen(following.replace(hour=9, minute=5, second=0, microsecond=0))
    else:
        await reopen(ctx.clock[0] + timedelta(seconds=10))
    await runner.episode.advance_once()
    sells = [record for record in ctx.execution.active_intents('mock', ctx.runtime.account_ref, ctx.runtime.run_id)
             if record.intent.side is OrderSide.SELL]
    case.assertEqual(1, len(sells), runner.episode.status)
    sell, = sells
    if preclose_sell is not None: case.assertEqual(preclose_sell, sell.intent.intent_id)
    case.assertEqual(head.quantity, sell.intent.quantity)
    from kiwoom_monitor.application.panic_mock_execution import panic_exit_key
    evidence = ctx.repository.load_panic_exit_evidence(ctx.runtime.account_ref, panic_exit_key(sell.intent.plan))
    case.assertEqual(trigger, evidence['snapshot']['panic_trigger'])
    case.assertEqual('panic_failure_reached_0B' if kind == 'failure' else 'panic_intraday_management_overdue_0B', evidence['snapshot']['reason'])
    if kind == 'rebound':
        await runner.episode.advance_once()  # Settle the acknowledged working SELL's material-input token.
        reads, settings_reads = ctx.book.reads, []
        load_document = ctx.store.load_document
        def read_document(collection, *args, **kwargs):
            if collection == 'server_operational_settings': settings_reads.append(collection)
            return load_document(collection, *args, **kwargs)
        with patch.object(ctx.store, 'load_document', side_effect=read_document):
            await runner.episode.advance_once(); await runner.episode.advance_once()
        case.assertEqual(reads, ctx.book.reads, 'unchanged working SELL repeated a broker query')
        case.assertEqual(0, len(settings_reads), 'a working SELL does not consume entry Shadow settings')
        ctx.book.fill(sell.broker_order_id, 1, 'panic-runner-partial-exit')
        ctx.book.rows[sell.broker_order_id].update(remaining=0, state=OrderState.CANCELLED)
        ctx.clock[0] += timedelta(seconds=1)
        ctx.current_quote.update(available_at=ctx.clock[0])
        await ctx.bundle.monitor.refresh_recovery(); await runner.episode.advance_once()
        residual, = ctx.runtime.load_active_intents()
        case.assertEqual((OrderSide.SELL, 2, buy.intent.plan.plan_id),
            (residual.intent.side, residual.intent.quantity, residual.intent.plan.plan_id))
        ctx.book.fill(residual.broker_order_id, 2, 'panic-runner-final-residual')
    else:
        ctx.book.fill(sell.broker_order_id, sell.intent.quantity, 'panic-runner-exit')
    await ctx.bundle.monitor.refresh_recovery(); await runner.episode.advance_once()
    final = ctx.execution.load_plan_position(ctx.runtime.account_ref, ctx.runtime.run_id, buy.intent.symbol, buy.intent.plan)
    case.assertEqual(0, final.quantity)
    case.assertEqual(['BUY', 'SELL', 'SELL'] if kind == 'rebound' else ['BUY', 'SELL'], [item[1] for item in ctx.transport.calls])
    return ctx


class PanicRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_p0_first_failure_survives_rebound_unknown_cancel_late_fill_and_reopen(self):
        await verify_panic_runner(self)

    async def test_p1_first_rebound_survives_pullback_closed_session_and_next_day_partial_exit(self):
        await verify_panic_runner(self, variant='P1', kind='rebound')

    async def test_zero_fill_terminal_cancellation_late_fill_restores_original_plan(self):
        await verify_panic_runner(self, zero_fill=True)
