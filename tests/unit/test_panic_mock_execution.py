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


def prepare_panic_native(case, variant='P0', native_factory=None, *, stopped='continue_original_plan/v1'):
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
    admit_mock_automation(ctx.repository, ctx.repository, ctx.runtime, account_ref=ctx.runtime.account_ref,
        spec_id=ctx.spec.spec_id, requested_at=ctx.clock[0])
    ctx.revision = 0
    ctx.quote = dict(price_won=ctx.event['signal_reference_price'], available_at=ctx.clock[0],
        input_ref='collector-trade:panic-forward-entry', durable=False, market_status='TRADABLE')
    refresh_panic_cut(ctx, {})
    return ctx


def refresh_panic_cut(ctx, positions):
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
        account_ref=ctx.runtime.account_ref, spec_id=ctx.spec.spec_id)


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
    def test_both_variants_use_current_news_prices_and_only_detailed_owned_quantity(self):
        for variant in ('P0', 'P1'):
            with self.subTest(variant=variant): verify_panic_native_cycle(self, variant)

    def test_native_claim_rechecks_current_settings_original_setup_and_actual_owned_exit(self):
        verify_panic_native_claim_fences(self)

    def test_stopped_original_plan_survives_closed_session_and_next_day_reopen(self):
        verify_panic_stopped_management(self)

    def test_explicit_reconcile_only_stop_does_not_send_protective_orders(self):
        verify_panic_stopped_management(self, stopped='reconcile_only/v1')
