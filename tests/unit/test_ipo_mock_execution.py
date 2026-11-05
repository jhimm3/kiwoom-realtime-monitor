"""IPO READY publication and original native plan/owned Runner contracts."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, time, timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def prepare_ipo_execution(case, store, family, phase, *, runner=False):
    from tests.unit import test_mock_automation_specification as fixtures
    from tests.unit.test_close_and_ipo import CloseAndIpoTests
    from kiwoom_monitor.application.market_session_schedule import KST, session_window_at
    from kiwoom_monitor.domain.execution_activation import CLOSE_NATIVE_PLAN_MODE, CloseMockExecutionPolicy
    from kiwoom_monitor.domain.market_data_contract import ListingPhase
    from kiwoom_monitor.domain.research_contract import CLOSE_FINAL_CANDIDATE_VERSION
    from kiwoom_monitor.domain.strategy_contract import MINUTE_SHADOW_VERSION

    inputs = fixtures.mock_specification_inputs(store, publish_candidate=False)
    example = CloseAndIpoTests()
    example.symbol = '005930'
    example.friday = fixtures.NOW.astimezone(KST).date() + timedelta(days=4)
    example.tuesday = example.friday + timedelta(days=4)
    example.published = datetime.combine(example.friday, time(8), KST)
    example.setUp()
    if runner:
        example.prior = (example.bar(example.entry, time(15, 13), price=100, revision='early-prior-1'),
                         example.bar(example.entry, time(15, 14), price=101, revision='early-prior-2'))
        example.signal = example.bar(example.entry, time(15, 15), price=104, revision='early-signal')
    policy = replace(example.ipo_close if family == 'ipo_close/v1' else example.ipo_breakout,
                     listing_phases=(ListingPhase(phase),))
    listing = replace(example.listing, listing_date=example.friday - timedelta(days=phase == 'after_listing_day'))
    example.listing = listing
    clock = [datetime.fromisoformat(example.signal.available_at)]
    example.entry = replace(example.entry, schedule_version=session_window_at(clock[0], venue='KRX').schedule_version)
    example.calendar = (replace(example.calendar[0], sessions=(example.entry, example.auction)), *example.calendar[1:])
    case.enterContext(patch.object(store, '_execution_wall_now', lambda: clock[0], create=True))
    parameters = {'capital_won': 100_000, 'policy': policy.to_dict()}
    config = json.loads(json.dumps({
        'version': MINUTE_SHADOW_VERSION, 'family': family, 'signal_valid_seconds': 30,
        'symbols': [example.symbol], 'strategy': parameters,
        'references': {'version': 'strategy-reference/v1', 'calendar': [asdict(day) for day in example.calendar],
                       'listings': [asdict(listing)]},
    }, default=lambda value: value.isoformat()))
    settings = {'revision': 1, 'shadow_candidate_enabled': True, 'shadow_candidate_config': config,
                'shadow_candidate_universe_max_age_seconds': 180}
    store.upsert_documents('server_operational_settings', [{'owner': 'global', 'key': 'current', 'document': settings}])
    monitor = fixtures.CandidateMonitor.from_json(store, json.dumps(config), poll_seconds=2,
                                                universe_max_age_seconds=180, now_provider=lambda: clock[0])
    monitor.run_once()
    for index, frame in enumerate((*example.prior, example.signal)):
        start = datetime.fromisoformat(frame.bar_start)
        key = {'trading_date': start.date().isoformat(), 'minute': start.strftime('%H:%M'),
               'code': frame.code, 'market': 'KRX'}
        store.save_minute_bars([{**key, 'open': frame.open, 'high': frame.high, 'low': frame.low,
            'close': frame.close, 'volume': frame.volume, 'trade_value_million_won': frame.trade_value_million_won,
            'updated_at': start.timestamp(), 'operation_id': f'ipo-write-{index}'}])
        store.finalize_minute_bars([{**key, 'available_at': datetime.fromisoformat(frame.available_at).timestamp(),
            'capture_quality': 'complete', 'finalization_source': 'timer', 'operation_id': f'ipo-finalize-{index}'}])
    monitor.run_once()
    event, = (row for row in store.load_shadow_candidates()['events'] if row['strategy_id'] == family.split('/')[0])
    candidate = {**inputs.package.candidate_spec, 'version': CLOSE_FINAL_CANDIDATE_VERSION,
        'family': family, 'parameters': parameters,
        'strategy_references': {'version': 'strategy-reference/v1', 'sha256': fixtures._hash(config['references'])},
        'execution_model': {**inputs.package.candidate_spec['execution_model'],
            'version': 'completed_close_then_next_open/v1', 'same_bar_path_version': 'no_intrabar_fill/v1'}}
    package = replace(inputs.package, candidate_spec=candidate, candidate_spec_hash=fixtures._hash(candidate))
    eligibility = replace(inputs.policy, candidate_spec_hash=package.candidate_spec_hash)
    receipt = fixtures.evaluate_candidate_eligibility(package, eligibility, account_ref=fixtures.ACCOUNT_REF)
    repository = inputs.repository
    repository.publish_mock_automation_candidate(package, eligibility, receipt, credential_profile_id=fixtures.PROFILE_ID,
                                                 expected_binding_revision=inputs.binding['binding_revision'])
    terms = CloseMockExecutionPolicy(100, 200, 'acknowledged_0B_bounded_by_signal/v1',
        'cancel_at_entry_deadline/v1', 'acknowledged_0B_bounded_slippage/v1')
    documents = fixtures.build_mock_automation_publication_documents(
        candidate_publication={'package': package.to_dict(), 'eligibility_policy': eligibility.to_dict(),
                               'eligibility_receipt': receipt.to_dict()},
        binding=inputs.binding, credential_profile_id=fixtures.PROFILE_ID, shadow_event=event,
        evaluation_start=clock[0], evaluation_end=example.following.ends_at, frozen_at=clock[0],
        criteria=inputs.profile.criteria, limits={name: getattr(inputs.spec, name) for name in (
            'maximum_concurrent_strategies', 'maximum_concurrent_positions', 'maximum_capital_won',
            'maximum_daily_loss_won', 'maximum_data_gap_seconds', 'maximum_submission_unknown_count',
            'maximum_reconnect_count', 'maximum_balance_mismatch_count')},
        execution_plan_mode=CLOSE_NATIVE_PLAN_MODE, close_execution_policy=terms)
    from kiwoom_monitor.domain.execution_activation import (
        forward_spec_from_dict, stage_revision_from_dict, mock_automation_spec_from_dict,
    )
    profile = forward_spec_from_dict(documents['forward_profile'])
    stages = tuple(stage_revision_from_dict(value) for value in documents['stage_revisions'])
    spec = mock_automation_spec_from_dict(documents['operating_spec'])
    case.assertEqual(stages[0].decided_at, stages[1].decided_at)  # Same source time is preserved, never fabricated.
    for expected_change in (True, False):
        readiness, changed = fixtures.publish_ready_mock_automation_spec(repository, profile=profile,
            stage_revisions=stages, spec=spec, shadow_event=event, current_binding=inputs.binding)
        case.assertEqual(('READY', expected_change), (readiness.status.value, changed))
        case.assertEqual(stages, repository.load_stage_revisions(spec.strategy_ref))
    execution = fixtures.ExecutionRepository(store)
    transport = fixtures._FakeBrokerTransport(lambda: clock[0])
    run_id = fixtures.execution_run_id_for_spec(spec.spec_id)
    runtime = fixtures.ExecutionRuntime(fixtures.OrderLifecycle(execution, transport, now_provider=lambda: clock[0]),
        execution, account_ref=fixtures.ACCOUNT_REF, run_id=run_id, owner_token='ipo-native-test', now_provider=lambda: clock[0])
    case.addCleanup(runtime.stop)
    fixtures.admit_mock_automation(repository, repository, runtime, account_ref=fixtures.ACCOUNT_REF,
                                  spec_id=spec.spec_id, requested_at=clock[0])
    context = SimpleNamespace(store=store, example=example, spec=spec, parameters=parameters, event=event,
        repository=repository, runtime=runtime, execution=execution, transport=transport, clock=clock,
        settings=settings, family=family, revision=0, monitor=monitor)
    refresh_ipo_cut(context, {})
    context.quote = {'price_won': 104, 'available_at': clock[0], 'input_ref': 'collector-trade:ipo-fixture:1:005930:1',
                     'durable': False, 'market_status': 'TRADABLE'}
    return context


def refresh_ipo_cut(context, positions):
    from tests.unit import test_mock_automation_specification as fixtures
    context.revision += 1
    context.recovery = fixtures.MockAccountRecovery(
        fixtures.AccountSnapshot(fixtures.ACCOUNT_REF, 100_000, 0, positions, context.clock[0]), ())
    context.risk = fixtures.build_mock_automation_risk_snapshot(context.recovery,
        context.execution.account_events('mock', fixtures.ACCOUNT_REF).events, (),
        context.execution.active_intents('mock', fixtures.ACCOUNT_REF, context.runtime.run_id),
        execution_run_id=context.runtime.run_id, binding_revision=context.spec.binding_revision,
        reconciliation_revision=context.revision, observed_at=context.clock[0],
        broker_query_started_at=context.clock[0], broker_query_completed_at=context.clock[0])
    context.repository.save_mock_automation_risk_snapshot(context.risk)
    fixtures.record_mock_automation_recovery_from_risk(context.repository, context.runtime,
        context.recovery, context.risk, account_ref=fixtures.ACCOUNT_REF, spec_id=context.spec.spec_id)


def enter_ipo(case, context, *, quantity=4, tamper=False):
    from kiwoom_monitor.application.close_mock_execution import CLOSE_ENTRY_COLLECTION, dispatch_close_entry
    from kiwoom_monitor.domain.order_contract import BrokerFill, BrokerOrderSnapshot, OrderState
    call = dict(parameters=context.parameters, event=context.event, quote=context.quote,
                recovery=context.recovery, risk=context.risk)
    if tamper:
        submit = context.runtime.submit_automation_intent
        original = []
        def change_listing(intent, account, **kwargs):
            row = context.repository.load_close_entry_evidence(context.runtime.account_ref, intent.plan.plan_id)
            original.append(row)
            changed = deepcopy(row)
            changed['snapshot']['candidate']['close_ipo_entry_setup']['listing_refs'] = ['rewritten-listing']
            context.store.upsert_documents(CLOSE_ENTRY_COLLECTION, [{'owner': context.runtime.account_ref,
                'key': intent.plan.plan_id, 'document': changed}])
            return submit(intent, account, **kwargs)
        with patch.object(context.runtime, 'submit_automation_intent', side_effect=change_listing):
            with case.assertRaisesRegex(RuntimeError, 'CLOSE_ENTRY_PROOF_OR_ORDER_CHANGED'):
                dispatch_close_entry(context.repository, context.runtime, context.spec, **call)
        case.assertEqual([], context.transport.calls)
        from kiwoom_monitor.application.close_mock_execution import close_entry_plan_id
        context.store.upsert_documents(CLOSE_ENTRY_COLLECTION, [{'owner': context.runtime.account_ref,
            'key': close_entry_plan_id(context.spec.spec_id, context.runtime.run_id, context.event['event_id']),
            'document': original[0]}])
    gate, buy, _ = dispatch_close_entry(context.repository, context.runtime, context.spec, **call)
    case.assertIsNotNone(buy, gate)
    case.assertEqual((4, 104), (buy.intent.quantity, buy.intent.limit_price))
    repeated = dispatch_close_entry(context.repository, context.runtime, context.spec, **call)
    case.assertEqual(buy.intent, repeated[1].intent)
    context.clock[0] += timedelta(seconds=1)
    # The cancelled remainder is confirmed by the broker; only detailed fills become a position.
    buy = context.runtime.reconcile(buy.intent.intent_id, BrokerOrderSnapshot(buy.broker_order_id,
        context.runtime.account_ref, buy.intent.symbol, OrderState.FILLED if quantity == 4 else OrderState.CANCELLED,
        quantity, 0, context.clock[0], (BrokerFill('ipo-buy-fill', quantity, 104, context.clock[0]),)))
    context.buy = buy
    refresh_ipo_cut(context, {buy.intent.symbol: quantity})
    return buy


def ipo_head(context):
    return context.execution.load_plan_position(context.runtime.account_ref, context.runtime.run_id,
                                                context.buy.intent.symbol, context.buy.intent.plan)


def sell_ipo(context, price, *, gap=None):
    from kiwoom_monitor.application.close_mock_execution import dispatch_close_exit
    quote = {**context.quote, 'price_won': price, 'available_at': context.clock[0]}
    return dispatch_close_exit(context.repository, context.runtime, context.execution, context.spec, ipo_head(context).plan,
        parameters=context.parameters, event=context.event, trigger_quote=quote, quote=quote,
        recovery=context.recovery, risk=context.risk, gap_observations=gap)


def fill_ipo_sell(context, sell, *, quantity=None, terminal=True):
    from kiwoom_monitor.domain.order_contract import BrokerFill, BrokerOrderSnapshot, OrderState
    quantity = sell.intent.quantity if quantity is None else quantity
    context.clock[0] += timedelta(seconds=1)
    result = context.runtime.reconcile(sell.intent.intent_id, BrokerOrderSnapshot(sell.broker_order_id,
        context.runtime.account_ref, sell.intent.symbol, OrderState.FILLED if quantity == sell.intent.quantity else
        OrderState.CANCELLED if terminal else OrderState.PARTIALLY_FILLED, quantity,
        0 if terminal else sell.intent.quantity - quantity, context.clock[0],
        (BrokerFill('ipo-sell-' + sell.broker_order_id, quantity, sell.intent.limit_price, context.clock[0]),)))
    head = ipo_head(context)
    refresh_ipo_cut(context, {sell.intent.symbol: head.quantity} if head.quantity else {})
    return result


def verify_ipo_close_native(case, store_factory, *, phase, partial_buy=False):
    from tests.unit.test_close_mock_execution import publish_gap_observations
    from kiwoom_monitor.application.close_mock_execution import close_exit_key
    store = store_factory()
    case.addCleanup(store.close)
    store.initialize()
    context = prepare_ipo_execution(case, store, 'ipo_close/v1', phase)
    enter_ipo(case, context, quantity=2 if partial_buy else 4, tamper=True)
    original_setup = deepcopy(context.event['close_ipo_entry_setup'])
    # Current source changes do not turn an admitted IPO position into a new strategy.
    settings = deepcopy(context.settings)
    settings['shadow_candidate_enabled'] = False
    settings['shadow_candidate_config']['references']['listings'][0]['listing_type'] = 'SPAC'
    store.upsert_documents('server_operational_settings', [{'owner': 'global', 'key': 'current', 'document': settings}])
    context.clock[0] = datetime.fromisoformat(context.example.opening.available_at)
    refresh_ipo_cut(context, {context.buy.intent.symbol: ipo_head(context).quantity})
    gap = publish_gap_observations(store, context.example, context.clock)
    gate, sell, receipt = sell_ipo(context, 103, gap=gap)
    if partial_buy:
        case.assertEqual((None, None, None), (gate, sell, receipt))  # The tranche must preserve an actual residual.
    else:
        case.assertIsNotNone(sell, gate)
        case.assertEqual(2, sell.intent.quantity)
        proof = context.repository.load_close_exit_evidence(context.runtime.account_ref, close_exit_key(sell.intent.plan))
        case.assertEqual(('ipo_adverse_gap_partial_reduction', 4, 0),
            (proof['snapshot']['reason'], proof['snapshot']['position_quantity'], proof['snapshot']['reduced_quantity']))
        fill_ipo_sell(context, sell, quantity=1)
        case.assertEqual(3, ipo_head(context).quantity)
        gate, remainder, _ = sell_ipo(context, 103, gap=gap)
        case.assertIsNotNone(remainder, gate)
        case.assertEqual(1, remainder.intent.quantity)
        fill_ipo_sell(context, remainder)
        case.assertEqual(2, ipo_head(context).quantity)
        case.assertEqual((None, None, None), sell_ipo(context, 103, gap=gap))
    context.runtime.stop()
    store.close()
    reopened = store_factory()
    case.addCleanup(reopened.close)
    reopened.initialize()
    case.enterContext(patch.object(reopened, '_execution_wall_now', lambda: context.clock[0], create=True))
    from tests.unit import test_mock_automation_specification as fixtures
    context.store = reopened
    context.execution = fixtures.ExecutionRepository(reopened)
    context.repository = fixtures.ForwardEvaluationRepository(reopened)
    context.runtime = fixtures.ExecutionRuntime(fixtures.OrderLifecycle(context.execution, context.transport,
        now_provider=lambda: context.clock[0]), context.execution, account_ref=fixtures.ACCOUNT_REF,
        run_id=context.runtime.run_id, owner_token='ipo-native-restored', now_provider=lambda: context.clock[0])
    case.addCleanup(context.runtime.stop)
    context.runtime.start(new_orders_enabled=False)
    context.clock[0] = datetime.fromisoformat(original_setup['next_open_at']) + timedelta(
        minutes=context.parameters['policy']['exit_minutes_after_open'])
    refresh_ipo_cut(context, {context.buy.intent.symbol: 2})
    gate, final, _ = sell_ipo(context, 103)
    case.assertIsNotNone(final, gate)
    case.assertEqual(2, final.intent.quantity)
    fill_ipo_sell(context, final)
    case.assertEqual(0, ipo_head(context).quantity)
    proof = context.repository.load_close_entry_evidence(fixtures.ACCOUNT_REF, context.buy.intent.plan.plan_id)
    case.assertEqual(original_setup, proof['snapshot']['candidate']['close_ipo_entry_setup'])
    case.assertEqual(phase, original_setup['listing_phase'])
    case.assertEqual(['BUY', 'SELL'] if partial_buy else ['BUY', 'SELL', 'SELL', 'SELL'],
                     [row[1] for row in context.transport.calls])


def verify_ipo_breakout_native(case, store_factory, *, phase, deadline):
    from kiwoom_monitor.application.close_mock_execution import close_exit_key
    store = store_factory()
    case.addCleanup(store.close)
    store.initialize()
    context = prepare_ipo_execution(case, store, 'ipo_breakout/v1', phase)
    enter_ipo(case, context, quantity=3, tamper=True)
    setup = context.event['close_ipo_entry_setup']
    level = setup['breakout_level']
    case.assertEqual((None, None, None), sell_ipo(context, level))
    if deadline:
        context.clock[0] = datetime.fromisoformat(setup['entry_deadline'])
        refresh_ipo_cut(context, {context.buy.intent.symbol: 3})
    gate, sell, _ = sell_ipo(context, 104 if deadline else level - 1)
    case.assertIsNotNone(sell, gate)
    case.assertEqual(3, sell.intent.quantity)
    proof = context.repository.load_close_exit_evidence(context.runtime.account_ref, close_exit_key(sell.intent.plan))
    case.assertEqual('ipo_intraday_exit_deadline_0B' if deadline else 'ipo_breakout_structure_failed_0B',
                     proof['snapshot']['reason'])
    case.assertEqual((phase, None), (setup['listing_phase'], setup['next_open_at']))
    fill_ipo_sell(context, sell)
    case.assertEqual(0, ipo_head(context).quantity)
    case.assertEqual(['BUY', 'SELL'], [row[1] for row in context.transport.calls])


class IpoMockExecutionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def factory(self, name):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        return lambda: SQLiteQueryStore(self.root / (name + '.sqlite3'))

    def test_ipo_close_native_partial_tranche_and_reopened_residual_keep_original_listing_phase(self):
        for phase in ('listing_day', 'after_listing_day'):
            with self.subTest(phase=phase):
                verify_ipo_close_native(self, self.factory(phase), phase=phase)

    def test_ipo_close_does_not_reduce_away_actual_partial_buy_residual(self):
        verify_ipo_close_native(self, self.factory('partial-buy'), phase='listing_day', partial_buy=True)

    def test_ipo_breakout_native_strict_structure_exit_keeps_both_listing_phases(self):
        for phase in ('listing_day', 'after_listing_day'):
            with self.subTest(phase=phase):
                verify_ipo_breakout_native(self, self.factory(phase), phase=phase, deadline=False)

    def test_ipo_breakout_native_original_deadline_exits_actual_partial_buy_same_day(self):
        for phase in ('listing_day', 'after_listing_day'):
            with self.subTest(phase=phase):
                verify_ipo_breakout_native(self, self.factory(phase), phase=phase, deadline=True)

async def verify_ipo_runner(case, store_factory, *, family, phase):
    import asyncio
    from tests.unit import test_mock_automation_specification as fixtures
    from kiwoom_monitor.application.close_mock_execution import close_exit_key
    from kiwoom_monitor.central_server.execution_runtime import ManualMockOrderGateway
    from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
    from kiwoom_monitor.central_server.mock_runtime import (
        MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION, MockAccountRuntimeMode, MockAutomationRuntimeContext,
    )
    from tests.unit.test_close_mock_execution import publish_gap_observations
    store = store_factory()
    case.addCleanup(store.close)
    store.initialize()
    context = prepare_ipo_execution(case, store, family, phase, runner=True)
    clock = context.clock
    quotes = {context.event['symbol']: context.quote}
    orders, positions = {}, {}

    class Monitor:
        listener = None
        enabled = False
        cut = None

        async def refresh_recovery(self):
            current_orders = []
            for order in orders.values():
                current = replace(order, as_of=clock[0])
                record = context.execution.find_by_broker_order_id('mock', fixtures.ACCOUNT_REF,
                    context.runtime.run_id, current.broker_order_id)
                if record is not None:
                    context.runtime.reconcile(record.intent.intent_id, current)
                current_orders.append(current)
            account = fixtures.AccountSnapshot(fixtures.ACCOUNT_REF, 100_000,
                sum(row.remaining_quantity * 107 for row in current_orders
                    if row.state in {fixtures.OrderState.ACCEPTED, fixtures.OrderState.PARTIALLY_FILLED}),
                dict(positions), clock[0])
            recovery = fixtures.MockAccountRecovery(account, tuple(current_orders))
            previous = context.repository.load_latest_mock_automation_risk(fixtures.ACCOUNT_REF)
            risk = fixtures.build_mock_automation_risk_snapshot(recovery,
                context.execution.account_events('mock', fixtures.ACCOUNT_REF).events, (),
                context.execution.active_intents('mock', fixtures.ACCOUNT_REF, context.runtime.run_id),
                execution_run_id=context.runtime.run_id, binding_revision=context.spec.binding_revision,
                reconciliation_revision=previous.reconciliation_revision + 1, observed_at=clock[0],
                broker_query_started_at=clock[0], broker_query_completed_at=clock[0])
            context.repository.save_mock_automation_risk_snapshot(risk)
            observation = SimpleNamespace(account=account, risk_snapshot=risk, open_orders_complete=True,
                reconciliation_complete=risk.reconciliation_complete, unmapped_broker_order_ids=(),
                open_orders=context.execution.active_intents('mock', fixtures.ACCOUNT_REF, context.runtime.run_id))
            self.cut = dict(account=account, risk=risk, recovery=recovery, observation=observation)
            if self.listener is not None:
                self.listener(self.cut)
            return recovery

        def set_automation_risk_listener(self, listener):
            self.listener = listener

        def clear_automation_risk_listener(self, listener):
            if self.listener == listener:
                self.listener = None

        def set_automation_execution_inputs_enabled(self, enabled):
            self.enabled = enabled

        @property
        def latest_automation_execution_cut(self):
            return self.cut if self.enabled else None

    async def assemble():
        monitor = Monitor()
        async def refresh_account():
            return (await monitor.refresh_recovery()).account
        gateway = ManualMockOrderGateway(context.runtime, context.execution, refresh_account,
            now_provider=lambda: clock[0], automation_execution_cut=lambda: monitor.latest_automation_execution_cut)
        gateway.set_manual_submit_enabled(False)
        control = context.repository.load_mock_automation_control(fixtures.ACCOUNT_REF)
        admission = context.repository.load_mock_automation_admission_for_spec(fixtures.ACCOUNT_REF, context.spec.spec_id)
        bundle = SimpleNamespace(account_ref=fixtures.ACCOUNT_REF, credential_profile_id=fixtures.PROFILE_ID,
            run_id=context.runtime.run_id, runtime=context.runtime, repository=context.execution, monitor=monitor,
            gateway=gateway, runtime_mode=MockAccountRuntimeMode.AUTOMATIC,
            automation_context=MockAutomationRuntimeContext(MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION,
                fixtures.ACCOUNT_REF, context.runtime.run_id, context.spec.spec_id, admission.admission_id,
                control.control_revision, 1))
        async def require_settings(profile_id, *, expected_settings_revision, credential_revision):
            case.assertEqual((fixtures.PROFILE_ID, 1, 1), (profile_id, expected_settings_revision, credential_revision))
        supervisor = MockAutomationSupervisor(context.store, SimpleNamespace(bundle=lambda _: bundle,
            require_execution_settings=require_settings), poll_seconds=3600,
            price_source=lambda symbol: quotes.get(symbol), now_provider=lambda: clock[0])
        case.addAsyncCleanup(supervisor.close)
        case.addAsyncCleanup(gateway.close)
        await supervisor.activate(credential_profile_id=fixtures.PROFILE_ID, account_ref=fixtures.ACCOUNT_REF,
            spec_id=context.spec.spec_id, expected_settings_revision=1, credential_revision=1)
        runner = supervisor._runners[fixtures.ACCOUNT_REF]
        await asyncio.sleep(0)
        async def idle():
            while runner.episode.execution_busy:
                await asyncio.sleep(.01)
        await asyncio.wait_for(idle(), 10)
        case.assertFalse(runner._task.done())
        return supervisor, runner, monitor

    supervisor, runner, monitor = await assemble()
    case.assertEqual([], context.transport.calls)
    start = datetime.fromisoformat(context.example.signal.bar_end)
    clock[0] = start + timedelta(minutes=1, seconds=2)
    key = dict(trading_date=start.date().isoformat(), minute=start.strftime('%H:%M'),
               code=context.event['symbol'], market='KRX')
    context.store.save_minute_bars([{**key, 'open': 107, 'high': 108, 'low': 107, 'close': 107,
        'volume': 100, 'trade_value_million_won': 1, 'updated_at': clock[0].timestamp(), 'operation_id': 'ipo-runner-new'}])
    context.store.finalize_minute_bars([{**key, 'available_at': clock[0].timestamp(), 'capture_quality': 'complete',
        'finalization_source': 'timer', 'operation_id': 'ipo-runner-new-final'}])
    context.monitor.run_once()
    quotes[key['code']] = {**context.quote, 'price_won': 107, 'available_at': clock[0]}
    case.assertEqual(1, await runner.episode.advance_once(), runner.episode.status)
    buy, = context.execution.active_intents('mock', fixtures.ACCOUNT_REF, context.runtime.run_id)
    context.buy = buy
    proof = context.repository.load_close_entry_evidence(fixtures.ACCOUNT_REF, buy.intent.plan.plan_id)
    candidate = proof['snapshot']['candidate']
    original_setup = deepcopy(candidate['close_ipo_entry_setup'])
    case.assertEqual((4, 107, phase), (buy.intent.quantity, buy.intent.limit_price, original_setup['listing_phase']))
    fills = (fixtures.BrokerFill('ipo-runner-first', 2, 107, clock[0]),)
    positions[buy.intent.symbol] = 2
    orders[buy.broker_order_id] = fixtures.BrokerOrderSnapshot(buy.broker_order_id, fixtures.ACCOUNT_REF,
        buy.intent.symbol, fixtures.OrderState.PARTIALLY_FILLED, 2, 2, clock[0], fills)
    clock[0] += timedelta(seconds=1)
    await monitor.refresh_recovery()
    deadline = datetime.fromisoformat(original_setup['entry_deadline'])
    cancellations = []
    first_notice = None
    def cancel(*args, **kwargs):
        cancellations.append((args, kwargs))
        if first_notice is not None:
            saved = context.store.load_documents('execution_mock_automation_runner_current', fixtures.ACCOUNT_REF, 1)[0]['document']
            case.assertEqual(first_notice, saved['protection_review'])
        return fixtures.BrokerSubmission(buy.broker_order_id, clock[0])
    with patch.object(context.transport, 'cancel', side_effect=cancel):
        if family == 'ipo_breakout/v1':
            level = original_setup['breakout_level']
            case.assertEqual(104, level)
            first_price = {**quotes[buy.intent.symbol], 'price_won': level - 1, 'available_at': clock[0],
                           'input_ref': 'collector-trade:ipo-first-structure'}
            with patch.object(context.store, 'load_documents', side_effect=AssertionError('no callback DB read')), \
                    patch.object(context.store, 'upsert_documents', side_effect=AssertionError('no callback DB write')):
                supervisor.observe_automation_price(buy.intent.symbol,
                    {**first_price, 'available_at': clock[0] - timedelta(seconds=1)})
                case.assertIsNone(runner.episode._protection_snapshot())
                supervisor.observe_automation_price(buy.intent.symbol, first_price)
                first_notice = runner.episode._protection_snapshot()
                case.assertEqual(('ipo_structure_stop/v1', 103),
                    (first_notice['risk_trigger']['stop_loss']['version'], first_notice['risk_trigger']['stop_loss']['price_won']))
                quotes[buy.intent.symbol] = {**first_price, 'price_won': 106, 'input_ref': 'collector-trade:ipo-rebound'}
                supervisor.observe_automation_price(buy.intent.symbol, quotes[buy.intent.symbol])
                case.assertEqual(first_notice, runner.episode._protection_snapshot())
            case.assertLess(clock[0], deadline)
            from kiwoom_monitor.application.mock_automation_risk import validate_mock_stop_loss_proof
            for key, changed in (('breakout_level', 105), ('candidate_event_id', 'another-entry'), ('quantity', 3)):
                invalid = {**first_notice['risk_trigger']['stop_loss'], key: changed}
                with case.assertRaisesRegex(ValueError, 'ipo_structure_stop_(original_setup|native_basis)_changed'):
                    validate_mock_stop_loss_proof(invalid, context.spec, monitor.cut['risk'],
                                                  **runner.episode._stop_validation_terms())
            async def cancelled():
                while not cancellations or runner.episode.execution_busy:
                    await asyncio.sleep(.01)
            await asyncio.wait_for(cancelled(), 10)
        else:
            clock[0] = deadline
            await runner.episode.advance_once()
    case.assertEqual(1, len(cancellations), runner.episode.status)
    case.assertEqual(fixtures.OrderState.CANCEL_PENDING, context.execution.load(buy.intent.intent_id).state)
    clock[0] += timedelta(seconds=1)
    fills = (*fills, fixtures.BrokerFill('ipo-runner-late', 1, 107, clock[0]))
    positions[buy.intent.symbol] = 3
    orders[buy.broker_order_id] = replace(orders[buy.broker_order_id], state=fixtures.OrderState.CANCELLED,
        filled_quantity=3, remaining_quantity=0, fills=fills, as_of=clock[0])
    await monitor.refresh_recovery()
    await supervisor.close()
    case.assertFalse(monitor.enabled)
    context.runtime.stop()
    context.store.close()
    context.store = store_factory()
    case.addCleanup(context.store.close)
    context.store.initialize()
    case.enterContext(patch.object(context.store, '_execution_wall_now', lambda: clock[0], create=True))
    context.execution = fixtures.ExecutionRepository(context.store)
    context.repository = fixtures.ForwardEvaluationRepository(context.store)
    context.runtime = fixtures.ExecutionRuntime(fixtures.OrderLifecycle(context.execution, context.transport,
        now_provider=lambda: clock[0]), context.execution, account_ref=fixtures.ACCOUNT_REF, run_id=context.runtime.run_id,
        owner_token='ipo-runner-restored', now_provider=lambda: clock[0])
    case.addCleanup(context.runtime.stop)
    context.runtime.start(new_orders_enabled=False)
    context.store.upsert_documents('server_operational_settings', [{'owner': 'global', 'key': 'current',
        'document': {**context.settings, 'shadow_candidate_enabled': False, 'shadow_candidate_config': {}}}])
    quotes.pop(buy.intent.symbol)
    with patch.object(context.store, 'load_shadow_candidates', side_effect=AssertionError('no original candidate rescan')):
        supervisor, runner, monitor = await assemble()
    if first_notice is not None:
        case.assertEqual(first_notice, runner.episode._protection_snapshot())
        clock[0] += timedelta(seconds=30)
    else:
        clock[0] = datetime.fromisoformat(context.example.opening.available_at)
        publish_gap_observations(context.store, context.example, clock)
    quotes[buy.intent.symbol] = {**context.quote, 'price_won': 106, 'available_at': clock[0]}
    await monitor.refresh_recovery()
    await runner.episode.advance_once()
    sell, = (row for row in context.execution.active_intents('mock', fixtures.ACCOUNT_REF, context.runtime.run_id)
             if row.intent.side.value == 'SELL')
    case.assertEqual(3 if family == 'ipo_breakout/v1' else 2, sell.intent.quantity)
    if first_notice is not None:
        saved = context.repository.load_close_exit_evidence(fixtures.ACCOUNT_REF, close_exit_key(sell.intent.plan))
        case.assertEqual(first_notice, saved['snapshot']['protection_notice'])
        case.assertEqual('ipo_breakout_structure_failed_0B', saved['snapshot']['reason'])
    clock[0] += timedelta(seconds=1)
    positions[buy.intent.symbol] -= sell.intent.quantity
    orders[sell.broker_order_id] = fixtures.BrokerOrderSnapshot(sell.broker_order_id, fixtures.ACCOUNT_REF,
        sell.intent.symbol, fixtures.OrderState.FILLED, sell.intent.quantity, 0, clock[0],
        (fixtures.BrokerFill('ipo-runner-sell', sell.intent.quantity, 106, clock[0]),))
    if not positions[buy.intent.symbol]:
        positions.clear()
    await monitor.refresh_recovery()
    await runner.episode.advance_once()
    if family == 'ipo_close/v1':
        case.assertEqual(1, ipo_head(context).quantity)
        case.assertEqual(['BUY', 'SELL'], [row[1] for row in context.transport.calls])
        clock[0] = datetime.fromisoformat(original_setup['next_open_at']) + timedelta(
            minutes=context.parameters['policy']['exit_minutes_after_open'])
        quotes[buy.intent.symbol] = {**quotes[buy.intent.symbol], 'available_at': clock[0]}
        await monitor.refresh_recovery()
        await runner.episode.advance_once()
        residual, = context.execution.active_intents('mock', fixtures.ACCOUNT_REF, context.runtime.run_id)
        case.assertEqual(1, residual.intent.quantity)
        clock[0] += timedelta(seconds=1)
        orders[residual.broker_order_id] = fixtures.BrokerOrderSnapshot(residual.broker_order_id, fixtures.ACCOUNT_REF,
            residual.intent.symbol, fixtures.OrderState.FILLED, 1, 0, clock[0],
            (fixtures.BrokerFill('ipo-runner-residual', 1, 106, clock[0]),))
        positions.clear()
        await monitor.refresh_recovery()
        await runner.episode.advance_once()
    case.assertEqual(0, ipo_head(context).quantity)
    restored = context.repository.load_close_entry_evidence(fixtures.ACCOUNT_REF, buy.intent.plan.plan_id)
    case.assertEqual(original_setup, restored['snapshot']['candidate']['close_ipo_entry_setup'])
    expected = ['BUY', 'SELL'] if family == 'ipo_breakout/v1' else ['BUY', 'SELL', 'SELL']
    case.assertEqual(expected, [row[1] for row in context.transport.calls])
    from kiwoom_monitor.application.journal_enrichment import sync_journal_execution_projection
    from kiwoom_monitor.application.trade_history_query_service import TradeHistoryQueryService
    from kiwoom_monitor.application.trade_history_service import TradeFill
    from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
    from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository

    scope = AccountScope('kiwoom', AccountEnvironment.MOCK, fixtures.ACCOUNT_REF)
    journal_path = Path(case.enterContext(tempfile.TemporaryDirectory())) / 'ipo-journal.sqlite3'
    journal = JournalRepository(journal_path)
    source = fixtures._ExecutionPageSource(context.execution, fixtures.ACCOUNT_REF)
    projected = sync_journal_execution_projection(source, journal, scope, page_limit=2, max_pages=100, now=clock[0])
    case.assertEqual(3 if family == 'ipo_breakout/v1' else 4, projected.detailed_fill_count)
    rows = JournalRepository(journal_path).load_execution_fill_projections(scope)
    expected_ids = {'ipo-runner-first', 'ipo-runner-late', 'ipo-runner-sell'}
    if family == 'ipo_close/v1':
        expected_ids.add('ipo-runner-residual')
    case.assertEqual(expected_ids, {row['broker_execution_id'] for row in rows})
    case.assertEqual({'BUY': 3, 'SELL': 3},
        {side: sum(row['quantity'] for row in rows if row['side'] == side) for side in ('BUY', 'SELL')})
    case.assertEqual({buy.intent.plan.plan_id}, {row['plan']['plan_id'] for row in rows})
    summaries = tuple(TradeFill(order.broker_order_id, buy.intent.symbol, family,
        '매수' if order.broker_order_id == buy.broker_order_id else '매도',
        fill.occurred_at.replace(tzinfo=None), fill.quantity, fill.price, origin_scope=scope)
        for order in orders.values() for fill in order.fills)
    journal.upsert_fills(summaries, account_scope=scope)
    visible = TradeHistoryQueryService(JournalRepository(journal_path)).load(
        datetime.combine(context.example.friday, datetime.min.time()),
        datetime.combine(context.example.tuesday + timedelta(days=1), datetime.min.time()), scope)
    case.assertEqual(set(summaries), set(visible.fills))
    case.assertEqual(set(summaries), set(visible.execution_plans))
    case.assertEqual({(buy.intent.plan.plan_id, buy.intent.plan.policy_ref)},
        {(plan.plan_id, plan.policy_ref) for plan in visible.execution_plans.values()})
    repeated = sync_journal_execution_projection(source, JournalRepository(journal_path), scope, now=clock[0])
    case.assertEqual(0, repeated.inserted_event_count)
    case.assertEqual(rows, JournalRepository(journal_path).load_execution_fill_projections(scope))
    case.assertEqual({}, positions)
    await supervisor.close()


class IpoRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def factory(self, name):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        return lambda: SQLiteQueryStore(self.root / (name + '.sqlite3'))

    async def test_ipo_breakout_first_structure_cross_survives_rebound_cancel_late_fill_and_reopen(self):
        for phase in ('listing_day', 'after_listing_day'):
            with self.subTest(phase=phase):
                await verify_ipo_runner(self, self.factory(phase), family='ipo_breakout/v1', phase=phase)

    async def test_ipo_close_runner_reopens_original_plan_and_manages_partial_gap_and_residual(self):
        for phase in ('listing_day', 'after_listing_day'):
            with self.subTest(phase=phase):
                await verify_ipo_runner(self, self.factory(phase), family='ipo_close/v1', phase=phase)
