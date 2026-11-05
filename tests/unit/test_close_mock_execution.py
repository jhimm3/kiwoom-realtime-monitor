"""Native CLOSE deadline cancellation, late detailed fills and management restoration."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def verify_close_deadline(case, store_factory, *, partial, gap=False):
    from tests.unit.test_mock_automation_specification import MockAutomationSpecificationTests, ACCOUNT_REF
    from kiwoom_monitor.application.close_mock_execution import (
        CLOSE_ENTRY_COLLECTION, cancel_close_entry_deadline, dispatch_close_exit,
    )
    from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
    from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
    from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
    from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
    from kiwoom_monitor.domain.order_contract import (
        AccountSnapshot, BrokerFill, BrokerOrderSnapshot, BrokerSubmission, OrderState,
    )
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
    from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository

    store = store_factory()
    case.addCleanup(store.close)
    store.initialize()
    context = MockAutomationSpecificationTests._verify_close_entry_claim(case, store)
    runtime, execution, repository = context.runtime, context.execution, context.repository
    clock, buy = context.clock, context.buy
    deadline = datetime.fromisoformat(context.event['close_ipo_entry_setup']['entry_deadline'])
    quantity = 2 if partial else 0
    fills = (BrokerFill('close-before-cancel', quantity, 104, buy.intent.created_at + timedelta(seconds=1)),) if quantity else ()
    control_revision = repository.load_mock_automation_control(ACCOUNT_REF).control_revision

    def refresh():
        order = BrokerOrderSnapshot(buy.broker_order_id, ACCOUNT_REF, buy.intent.symbol,
            OrderState.PARTIALLY_FILLED if quantity else OrderState.ACCEPTED, quantity, 4 - quantity, clock[0], fills)
        current = runtime.reconcile(buy.intent.intent_id, order)
        account = AccountSnapshot(ACCOUNT_REF, 100_000, (4 - quantity) * 104,
                                  {buy.intent.symbol: quantity} if quantity else {}, clock[0])
        recovery = MockAccountRecovery(account, (order,))
        risk = build_mock_automation_risk_snapshot(recovery, execution.account_events('mock', ACCOUNT_REF).events,
            (), execution.active_intents('mock', ACCOUNT_REF, runtime.run_id), execution_run_id=runtime.run_id,
            binding_revision=context.spec.binding_revision, reconciliation_revision=2 if clock[0] < deadline else 3,
            observed_at=clock[0], broker_query_started_at=clock[0], broker_query_completed_at=clock[0])
        repository.save_mock_automation_risk_snapshot(risk)
        return current, recovery, risk

    def cancel(expected, recovery, risk):
        return cancel_close_entry_deadline(repository, runtime, execution, context.spec, expected,
            parameters=context.parameters, event=context.event, recovery=recovery, risk=risk,
            control_revision=control_revision)

    clock[0] = deadline - timedelta(seconds=1)
    current, recovery, risk = refresh()
    with case.assertRaisesRegex(ValueError, 'deadline has not arrived'):
        cancel(current, recovery, risk)
    clock[0] = deadline
    current, recovery, risk = refresh()
    head = execution.load_plan_position(ACCOUNT_REF, runtime.run_id, buy.intent.symbol, buy.intent.plan)
    original_cancel = runtime._lifecycle.cancel

    def mutate_proof(*args, **kwargs):
        invalid = deepcopy(context.entry_proof)
        invalid['snapshot']['candidate_snapshot_id'] = 'changed-after-cancel-assessment'
        store.upsert_documents(CLOSE_ENTRY_COLLECTION,
            [{'owner': ACCOUNT_REF, 'key': head.plan.plan_id, 'document': invalid}])
        return original_cancel(*args, **kwargs)

    cancellations = []

    def transport_cancel(*args, **kwargs):
        cancellations.append((args, kwargs))
        if partial:
            raise SubmissionUnknown('synthetic cancellation response lost')
        return BrokerSubmission(buy.broker_order_id, clock[0])

    with patch.object(context.transport, 'cancel', side_effect=transport_cancel):
        with patch.object(runtime._lifecycle, 'cancel', side_effect=mutate_proof):
            with case.assertRaisesRegex(ValueError, 'confirmed original BUY remainder'):
                cancel(current, recovery, risk)
        store.upsert_documents(CLOSE_ENTRY_COLLECTION,
            [{'owner': ACCOUNT_REF, 'key': head.plan.plan_id, 'document': context.entry_proof}])
        case.assertEqual([], cancellations)
        case.assertFalse(any(row['event_type'] == 'CANCEL_STARTED' for row in execution.events(buy.intent.intent_id)))
        case.assertEqual(head, execution.load_plan_position(ACCOUNT_REF, runtime.run_id, buy.intent.symbol, head.plan))
        cancelled = cancel(current, recovery, risk)
        case.assertEqual(OrderState.CANCEL_PENDING, cancelled.state)
        case.assertEqual(1, len(cancellations))
        case.assertTrue(runtime.stop())
        store.close()
        clock[0] += timedelta(seconds=1)
        store = store_factory()
        case.addCleanup(store.close)
        store.initialize()
        case.enterContext(patch.object(store, '_execution_wall_now', lambda: clock[0], create=True))
        execution = ExecutionRepository(store)
        repository = ForwardEvaluationRepository(store)
        runtime = ExecutionRuntime(OrderLifecycle(execution, context.transport, now_provider=lambda: clock[0]), execution,
            account_ref=ACCOUNT_REF, run_id=context.runtime.run_id, owner_token='close-deadline-restored',
            now_provider=lambda: clock[0])
        case.addCleanup(runtime.stop)
        runtime.start(new_orders_enabled=False)
        case.assertEqual(OrderState.CANCEL_PENDING, cancel(current, recovery, risk).state)
        case.assertEqual(1, len(cancellations))

    late = BrokerFill('close-during-cancel', 1, 104, clock[0])
    terminal = BrokerOrderSnapshot(buy.broker_order_id, ACCOUNT_REF, buy.intent.symbol,
        OrderState.CANCELLED, quantity + 1, 0, clock[0], (*fills, late))
    for _ in range(2):
        case.assertEqual(OrderState.CANCELLED, runtime.reconcile(buy.intent.intent_id, terminal).state)
    head = execution.load_plan_position(ACCOUNT_REF, runtime.run_id, buy.intent.symbol, buy.intent.plan)
    case.assertEqual(quantity + 1, head.quantity)
    history = execution.events(buy.intent.intent_id)
    claims = [row for row in history if row['event_type'] == 'CANCEL_STARTED']
    case.assertEqual(1, len(claims))
    case.assertEqual('close-entry-deadline-cancel/v1', claims[0]['close_entry_cancel_claim']['version'])
    case.assertEqual(1, sum(row.get('broker_execution_id') == late.execution_id for row in history))
    clock[0] = (datetime.fromisoformat(context.example.opening.available_at) if gap
                else context.example.following.starts_at)
    account = AccountSnapshot(ACCOUNT_REF, 100_000, 0, {buy.intent.symbol: head.quantity}, clock[0])
    recovery = MockAccountRecovery(account, ())
    risk = build_mock_automation_risk_snapshot(recovery, execution.account_events('mock', ACCOUNT_REF).events,
        (), (), execution_run_id=runtime.run_id, binding_revision=context.spec.binding_revision,
        reconciliation_revision=4, observed_at=clock[0], broker_query_started_at=clock[0], broker_query_completed_at=clock[0])
    repository.save_mock_automation_risk_snapshot(risk)
    record_mock_automation_recovery_from_risk(repository, runtime, recovery, risk,
        account_ref=ACCOUNT_REF, spec_id=context.spec.spec_id)
    quote = {**context.quote, 'price_won': 103 if gap else 94, 'available_at': clock[0]}
    observations = None
    if gap:
        from kiwoom_monitor.application.close_mock_execution import CLOSE_EXIT_COLLECTION, close_exit_key

        observations = publish_gap_observations(store, context.example, clock)
        call = dict(parameters=context.parameters, event=context.event, trigger_quote=quote, quote=quote,
                    recovery=recovery, risk=risk)
        case.assertEqual((None, None, None), dispatch_close_exit(repository, runtime, execution,
            context.spec, head.plan, **call))
        future = deepcopy(observations)
        future[1]['available_at'] = (clock[0] + timedelta(seconds=1)).isoformat()
        case.assertEqual((None, None, None), dispatch_close_exit(repository, runtime, execution,
            context.spec, head.plan, gap_observations=future, **call))
        original_submit = runtime.submit_automation_intent
        for mutation, error in (('payload', 'CLOSE_EXIT_PROOF_OR_ORDER_CHANGED'),
                                ('missing', 'CLOSE_GAP_NATIVE_REVISIONS_MISSING')):
            proofs = []

            def mutate_gap(intent, account, **kwargs):
                proof = repository.load_close_exit_evidence(ACCOUNT_REF, close_exit_key(head.plan))
                proofs.append(proof)
                invalid = deepcopy(proof)
                row = invalid['snapshot']['gap_observations'][0]
                if mutation == 'payload':
                    row['payload']['close'] += 1
                else:
                    row['revision_id'] = 'missing-native-gap-revision'
                store.upsert_documents(CLOSE_EXIT_COLLECTION,
                    [{'owner': ACCOUNT_REF, 'key': close_exit_key(head.plan), 'document': invalid}])
                return original_submit(intent, account, **kwargs)

            with patch.object(runtime, 'submit_automation_intent', side_effect=mutate_gap):
                with case.assertRaisesRegex(RuntimeError, error):
                    dispatch_close_exit(repository, runtime, execution, context.spec, head.plan,
                                        gap_observations=observations, **call)
            store.upsert_documents(CLOSE_EXIT_COLLECTION,
                [{'owner': ACCOUNT_REF, 'key': close_exit_key(head.plan), 'document': proofs[0]}])
            case.assertEqual(head, execution.load_plan_position(ACCOUNT_REF, runtime.run_id, buy.intent.symbol, head.plan))
            case.assertEqual(['BUY'], [row[1] for row in context.transport.calls])
    gate, sell, _ = dispatch_close_exit(repository, runtime, execution, context.spec, head.plan,
        parameters=context.parameters, event=context.event, trigger_quote=quote, quote=quote, recovery=recovery, risk=risk,
        gap_observations=observations)
    case.assertIsNotNone(sell, gate)
    case.assertEqual((OrderState.ACCEPTED, quantity + 1, head.plan), (sell.state, sell.intent.quantity, sell.intent.plan))
    case.assertEqual(['BUY', 'SELL'], [row[1] for row in context.transport.calls])
    if gap:
        case.assertEqual(103, sell.intent.limit_price)
        proof = repository.load_close_exit_evidence(ACCOUNT_REF, close_exit_key(head.plan))
        case.assertEqual('adverse_gap_full_exit_completed_bars', proof['snapshot']['reason'])
        case.assertEqual(observations, proof['snapshot']['gap_observations'])


def publish_gap_observations(store, example, clock):
    from kiwoom_monitor.central_server.database import SQLiteQueryStore
    from kiwoom_monitor.central_server.database_observation_writes import (
        _append_sqlite_observation_revision, _append_postgres_observation_revision,
    )
    from kiwoom_monitor.domain.market_data_contract import (
        MarketDataObservation, MarketDataMetadata, MarketDatasetKind, TradingVenue, DataUnit,
        DataValueKind, DataCompleteness, ObservationOrigin,
    )

    keys = []
    for bar in (example.closing, example.opening):
        payload = {key: getattr(bar, key) for key in ('code', 'bar_start', 'bar_end', 'open', 'high', 'low',
            'close', 'volume', 'trade_value_million_won', 'capture_quality', 'finalization_source', 'session_finalized')}
        payload.update(market='KRX', price_unit='won', window_closed=True, session=bar.market_session,
                       phase=bar.market_phase, schedule_version=bar.schedule_version)
        observation = MarketDataObservation(MarketDatasetKind.MINUTE_BAR, bar.code, payload,
            MarketDataMetadata(datetime.fromisoformat(bar.bar_start), datetime.fromisoformat(bar.available_at),
                TradingVenue.KRX, DataUnit.WON, DataValueKind.ACTUAL, DataCompleteness.COMPLETE,
                ObservationOrigin.REALTIME, 'synthetic:confirmed-auction-and-open'))
        if isinstance(store, SQLiteQueryStore):
            with store._lock, store._connection() as connection:
                _append_sqlite_observation_revision(connection, 'minute_bar', bar.code, bar.bar_start, payload,
                                                   observation, now_provider=lambda: clock[0])
        else:
            with store._connect() as connection, connection.cursor() as cursor:
                _append_postgres_observation_revision(cursor, 'minute_bar', bar.code, bar.bar_start, payload,
                                                     observation, now_provider=lambda: clock[0])
        keys.append(bar.bar_start)
    rows = {row['observation_key']: row for row in store.load_observation_revisions('minute_bar', example.symbol)}
    return [rows[key] for key in keys]


class CloseDeadlineExecutionTests(unittest.TestCase):
    def test_legacy_v1_entry_keeps_original_plan_and_receipt(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-legacy.sqlite3'
        verify_close_legacy_entry(self, lambda: SQLiteQueryStore(db))

    def _verify(self, partial):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-deadline.sqlite3'
        verify_close_deadline(self, lambda: SQLiteQueryStore(db), partial=partial)

    def test_zero_fill_deadline_cancel_keeps_late_fill_and_restores_management(self):
        self._verify(False)

    def test_partial_deadline_cancel_with_unknown_response_preserves_late_fill_after_restart(self):
        self._verify(True)

    def test_completed_adverse_gap_uses_native_revisions_and_current_quote_after_restart(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-gap.sqlite3'
        verify_close_deadline(self, lambda: SQLiteQueryStore(db), partial=True, gap=True)


def verify_close_legacy_entry(case, store_factory):
    from tests.unit.test_mock_automation_specification import MockAutomationSpecificationTests, ACCOUNT_REF
    from kiwoom_monitor.application.close_mock_execution import build_close_entry_evidence, dispatch_close_entry

    store = store_factory()
    case.addCleanup(store.close)
    store.initialize()
    context = MockAutomationSpecificationTests._verify_close_entry_claim(case, store, prepare_only=True)
    at = context.clock[0]
    proof, _, plan = build_close_entry_evidence(context.spec, run_id=context.runtime.run_id,
        parameters=context.parameters, event=context.event, quote=context.quote, decided_at=at, now=at,
        retain_candidate=False)
    context.repository.save_close_entry_evidence(ACCOUNT_REF, plan.plan_id, proof)
    risk = context.repository.load_latest_mock_automation_risk(ACCOUNT_REF)
    from kiwoom_monitor.domain.order_contract import AccountSnapshot
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
    recovery = MockAccountRecovery(AccountSnapshot(ACCOUNT_REF, 100_000, 0, {}, at), ())
    args = dict(parameters=context.parameters, event=context.event, quote=context.quote, recovery=recovery, risk=risk)
    _, buy, receipt = dispatch_close_entry(context.repository, context.runtime, context.spec, **args)
    case.assertEqual(plan, buy.intent.plan)
    context.clock[0] = datetime.fromisoformat(context.event['expires_at']) + timedelta(seconds=1)
    _, repeated, repeated_receipt = dispatch_close_entry(context.repository, context.runtime, context.spec, **args)
    case.assertEqual((buy.intent, receipt), (repeated.intent, repeated_receipt))
    case.assertEqual(proof, context.repository.load_close_entry_evidence(ACCOUNT_REF, plan.plan_id))
    case.assertEqual(['BUY'], [row[1] for row in context.transport.calls])


async def verify_close_runner(case, store_factory):
    import asyncio
    from dataclasses import replace
    from types import SimpleNamespace
    from tests.unit.test_mock_automation_specification import MockAutomationSpecificationTests, ACCOUNT_REF, PROFILE_ID
    from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
    from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
    from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime, ManualMockOrderGateway
    from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
    from kiwoom_monitor.domain.order_contract import AccountSnapshot, BrokerFill, BrokerOrderSnapshot, BrokerSubmission, OrderState
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
    from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository

    store = store_factory()
    case.addCleanup(store.close)
    store.initialize()
    context = MockAutomationSpecificationTests._verify_close_entry_claim(case, store, prepare_only=True)
    clock, quotes = context.clock, {context.event['symbol']: context.quote}
    broker_orders, broker_positions = {}, {}
    execution, repository, runtime = context.execution, context.repository, context.runtime

    class Monitor:
        listener = None
        enabled = False
        cut = None

        async def refresh_recovery(self):
            orders = []
            for order in broker_orders.values():
                current = replace(order, as_of=clock[0])
                record = execution.find_by_broker_order_id('mock', ACCOUNT_REF, runtime.run_id, current.broker_order_id)
                if record is not None:
                    runtime.reconcile(record.intent.intent_id, current)
                orders.append(current)
            reserved = sum(row.remaining_quantity * 105 for row in orders if row.state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED})
            account = AccountSnapshot(ACCOUNT_REF, 100_000, reserved, dict(broker_positions), clock[0])
            recovery = MockAccountRecovery(account, tuple(orders))
            previous = repository.load_latest_mock_automation_risk(ACCOUNT_REF)
            risk = build_mock_automation_risk_snapshot(recovery, execution.account_events('mock', ACCOUNT_REF).events,
                (), execution.active_intents('mock', ACCOUNT_REF, runtime.run_id), execution_run_id=runtime.run_id,
                binding_revision=context.spec.binding_revision, reconciliation_revision=previous.reconciliation_revision + 1,
                observed_at=clock[0], broker_query_started_at=clock[0], broker_query_completed_at=clock[0])
            repository.save_mock_automation_risk_snapshot(risk)
            observation = SimpleNamespace(account=account, risk_snapshot=risk, open_orders_complete=True,
                reconciliation_complete=risk.reconciliation_complete, unmapped_broker_order_ids=(),
                open_orders=execution.active_intents('mock', ACCOUNT_REF, runtime.run_id))
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
        gateway = ManualMockOrderGateway(runtime, execution, refresh_account, now_provider=lambda: clock[0],
                                        automation_execution_cut=lambda: monitor.latest_automation_execution_cut)
        gateway.set_manual_submit_enabled(False)
        bundle = SimpleNamespace(account_ref=ACCOUNT_REF, credential_profile_id=PROFILE_ID, run_id=runtime.run_id,
            runtime=runtime, repository=execution, monitor=monitor, gateway=gateway,
            automation_context=SimpleNamespace(spec_id=context.spec.spec_id, execution_run_id=runtime.run_id))
        supervisor = MockAutomationSupervisor(store, SimpleNamespace(bundle=lambda _: bundle), poll_seconds=3600,
            price_source=lambda symbol: quotes.get(symbol), now_provider=lambda: clock[0])
        case.addAsyncCleanup(supervisor.close)
        case.addAsyncCleanup(gateway.close)
        runner = await supervisor._assemble_runner(bundle, context.spec, PROFILE_ID)
        supervisor._runners[ACCOUNT_REF] = runner
        await asyncio.sleep(0)
        async def idle():
            while runner.episode.execution_busy:
                await asyncio.sleep(0.01)
        await asyncio.wait_for(idle(), 10)
        case.assertFalse(runner._task.done())
        case.assertNotEqual('ERROR', runner.episode.status['state'])
        case.assertTrue(monitor.enabled)
        return supervisor, runner, monitor

    supervisor, runner, monitor = await assemble()
    case.assertEqual([], context.transport.calls)  # Existing Shadow seeds never become historical orders.
    start = datetime.fromisoformat(context.example.signal.bar_start) + timedelta(minutes=1)
    clock[0] = start + timedelta(minutes=1, seconds=2)
    key = dict(trading_date=start.date().isoformat(), minute=start.strftime('%H:%M'), code=context.event['symbol'], market='KRX')
    store.save_minute_bars([{**key, 'open': 104, 'high': 105, 'low': 104, 'close': 105, 'volume': 100,
        'trade_value_million_won': 1, 'updated_at': clock[0].timestamp(), 'operation_id': 'close-runner-entry'}])
    store.finalize_minute_bars([{**key, 'available_at': clock[0].timestamp(), 'capture_quality': 'complete',
        'finalization_source': 'timer', 'operation_id': 'close-runner-entry-final'}])
    context.monitor.run_once()
    quotes[key['code']] = {**context.quote, 'price_won': 105, 'available_at': clock[0]}
    case.assertEqual(1, await runner.episode.advance_once())
    buy, = execution.active_intents('mock', ACCOUNT_REF, runtime.run_id)
    case.assertEqual((OrderState.ACCEPTED, 4, 105), (buy.state, buy.intent.quantity, buy.intent.limit_price))
    proof = repository.load_close_entry_evidence(ACCOUNT_REF, buy.intent.plan.plan_id)
    case.assertEqual('close_entry_snapshot/v2', proof['snapshot']['version'])
    case.assertEqual(buy.intent.symbol, proof['snapshot']['candidate']['symbol'])
    fills = (BrokerFill('runner-first-fill', 2, 105, clock[0]),)
    broker_positions[buy.intent.symbol] = 2
    broker_orders[buy.broker_order_id] = BrokerOrderSnapshot(buy.broker_order_id, ACCOUNT_REF, buy.intent.symbol,
        OrderState.PARTIALLY_FILLED, 2, 2, clock[0], fills)
    clock[0] += timedelta(seconds=1)
    await monitor.refresh_recovery()
    deadline = datetime.fromisoformat(proof['snapshot']['candidate']['close_ipo_entry_setup']['entry_deadline'])
    clock[0] = deadline
    cancellations = []
    def cancel(*args, **kwargs):
        cancellations.append((args, kwargs))
        return BrokerSubmission(buy.broker_order_id, clock[0])
    with patch.object(context.transport, 'cancel', side_effect=cancel):
        await runner.episode.advance_once()
    case.assertEqual(1, len(cancellations))
    case.assertEqual(OrderState.CANCEL_PENDING, execution.load(buy.intent.intent_id).state)
    clock[0] += timedelta(seconds=1)
    fills = (*fills, BrokerFill('runner-late-fill', 1, 105, clock[0]))
    broker_positions[buy.intent.symbol] = 3
    broker_orders[buy.broker_order_id] = replace(broker_orders[buy.broker_order_id], state=OrderState.CANCELLED,
                                               filled_quantity=3, remaining_quantity=0, as_of=clock[0], fills=fills)
    await monitor.refresh_recovery()
    await supervisor.close()
    case.assertFalse(monitor.enabled)
    case.assertTrue(runtime.stop())
    store.close()
    store = store_factory()
    case.addCleanup(store.close)
    store.initialize()
    case.enterContext(patch.object(store, '_execution_wall_now', lambda: clock[0], create=True))
    repository, execution = ForwardEvaluationRepository(store), ExecutionRepository(store)
    runtime = ExecutionRuntime(OrderLifecycle(execution, context.transport, now_provider=lambda: clock[0]), execution,
        account_ref=ACCOUNT_REF, run_id=context.runtime.run_id, owner_token='close-runner-restored', now_provider=lambda: clock[0])
    case.addCleanup(runtime.stop)
    runtime.start(new_orders_enabled=False)
    store.upsert_documents('server_operational_settings', [{'owner': 'global', 'key': 'current',
        'document': {**context.settings, 'shadow_candidate_enabled': False, 'shadow_candidate_config': {}}}])
    with patch.object(store, 'load_shadow_candidates', side_effect=AssertionError('retained CLOSE must not rescan Shadow history')):
        supervisor, runner, monitor = await assemble()
    clock[0] = datetime.fromisoformat(context.example.opening.available_at)
    quotes[buy.intent.symbol] = {**context.quote, 'price_won': 103, 'available_at': clock[0]}
    await monitor.refresh_recovery()
    await runner.episode.advance_once()
    case.assertEqual(['BUY'], [row[1] for row in context.transport.calls])
    expected_gap = publish_gap_observations(store, context.example, clock)
    selected = store.load_close_gap_observations(buy.intent.symbol, context.example.auction.starts_at,
        context.example.auction.ends_at, context.example.following.starts_at, clock[0])
    case.assertEqual(expected_gap, list(selected))
    case.assertEqual((), store.load_close_gap_observations(buy.intent.symbol, context.example.auction.starts_at,
        context.example.auction.ends_at, context.example.following.starts_at, clock[0] - timedelta(microseconds=1)))
    from datetime import timezone
    case.assertEqual(selected, store.load_close_gap_observations(buy.intent.symbol,
        context.example.auction.starts_at.astimezone(timezone.utc), context.example.auction.ends_at.astimezone(timezone.utc),
        context.example.following.starts_at.astimezone(timezone.utc), clock[0].astimezone(timezone.utc)))
    await runner.episode.advance_once()
    sell, = execution.active_intents('mock', ACCOUNT_REF, runtime.run_id)
    case.assertEqual((OrderState.ACCEPTED, 3, 103, buy.intent.plan.plan_id),
                     (sell.state, sell.intent.quantity, sell.intent.limit_price, sell.intent.plan.plan_id))
    clock[0] += timedelta(seconds=1)
    broker_positions.clear()
    broker_orders[sell.broker_order_id] = BrokerOrderSnapshot(sell.broker_order_id, ACCOUNT_REF, buy.intent.symbol,
        OrderState.FILLED, 3, 0, clock[0], (BrokerFill('runner-exit', 3, 103, clock[0]),))
    await monitor.refresh_recovery()
    await runner.episode.advance_once()
    head = execution.load_plan_position(ACCOUNT_REF, runtime.run_id, buy.intent.symbol, buy.intent.plan)
    case.assertEqual((0, 0), (head.quantity, head.unreconciled_sell_quantity))
    case.assertEqual(['BUY', 'SELL'], [row[1] for row in context.transport.calls])
    case.assertEqual(((), True), runtime.load_account_active_intents())
    await supervisor.close()


class CloseRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_flat_runner_clears_previous_pending_order_without_new_shadow(self):
        from types import SimpleNamespace
        from kiwoom_monitor.central_server.close_plan_management import ClosePlanManagement

        store = SimpleNamespace(load_shadow_candidates=lambda *_: {'events': [], 'high_watermark': 7})
        management = ClosePlanManagement(store, None, None, None, parameters={}, price_source=None, now_provider=None)
        management.status = {'state': 'ORDER_SUBMITTED', 'pending_intent_id': 'already-filled', 'orders_enabled': False}
        control = SimpleNamespace(desired_state=SimpleNamespace(value='RUNNING'))
        self.assertEqual(0, await management.advance(control, limit=10, allow_entry=lambda: True))
        self.assertEqual('RUNNING', management.status['state'])
        self.assertNotIn('pending_intent_id', management.status)

    async def test_runner_enters_cancels_restores_retained_plan_and_exits_native_gap(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-runner.sqlite3'
        await verify_close_runner(self, lambda: SQLiteQueryStore(db))
