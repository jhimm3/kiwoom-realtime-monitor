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
    rows = {row['observation_key']: row for row in sorted(
        store.load_observation_revisions('minute_bar', example.symbol),
        key=lambda row: (datetime.fromisoformat(row['available_at']), row['accepted_sequence']))}
    return [rows[key] for key in keys]


async def publish_collector_gap_observations(case, store, example):
    import asyncio
    from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
    from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
    from kiwoom_monitor.central_server.diagnostic_replay_contract import validate_collector_message
    from kiwoom_monitor.central_server.diagnostic_collector_replay import _MeasuredStore
    from types import SimpleNamespace

    at = [example.auction.ends_at + timedelta(seconds=7, milliseconds=100)]
    async def held_timer(_):
        await asyncio.Event().wait()
    measured = _MeasuredStore(store, SimpleNamespace(now=lambda: at[0]))
    collector = CentralRealtimeCollector(lambda: 'unused-offline-token', 'mock', RealtimeHub(), lambda: at[0], measured,
                                         snapshot_sleep=held_timer)
    await collector.start_input_replay(message_validator=validate_collector_message)
    case.addAsyncCleanup(collector.close)
    source = (example.symbol, 'KRX')
    collector.restore_replay_source_state({source}, {source: example.auction.starts_at})
    def trade(raw_time, price):
        collector.accept_replay_message({'trnm': 'REAL', 'data': [{'type': '0B', 'item': example.symbol,
            'values': {'20': raw_time, '10': str(price), '13': '100', '14': '1', '15': '1', '290': '2'}}]})
    trade((example.auction.ends_at + timedelta(seconds=7)).strftime('%H%M%S'), example.closing.close)
    case.assertEqual(1, len(collector._pending_closing_auction_prices))
    native_save = store.save_dataset_snapshots
    failed = [False]
    def lose_ack(values):
        native_save(values)
        if not failed[0] and any(value[0] == 'closing_auction_price' for value in values):
            failed[0] = True
            raise OSError('synthetic closing price COMMIT response lost')
    with patch.object(store, 'save_dataset_snapshots', side_effect=lose_ack):
        with case.assertRaisesRegex(OSError, 'COMMIT response lost'):
            await collector._flush_snapshots(flush_latest=False)
        original, = store.load_observation_revisions('closing_auction_price', example.symbol + ':KRX')
        case.assertEqual(1, len(collector._pending_closing_auction_prices))
        await collector._flush_snapshots(flush_latest=False)
    case.assertEqual([original], store.load_observation_revisions('closing_auction_price', example.symbol + ':KRX'))
    case.assertFalse(collector._pending_closing_auction_prices)
    case.assertEqual(['failed', 'returned'], [row['state'] for row in measured.calls if row['kind'] == 'realtime_market_history'])
    case.assertFalse(set(original['payload']) & {'open', 'high', 'low', 'close', 'bar_start', 'bar_end'})
    # A later trade is a new minute, never a replacement of the retained close.
    at[0] += timedelta(minutes=1)
    trade(at[0].strftime('%H%M%S'), 1)
    at[0] = example.following.starts_at - timedelta(minutes=1)
    collector.accept_replay_sources({source}, reset_all=True, subscribed_at=at[0])
    at[0] = example.following.starts_at + timedelta(milliseconds=100)
    trade(example.following.starts_at.strftime('%H%M%S'), example.opening.open)
    at[0] = example.following.starts_at + timedelta(seconds=59)
    trade(at[0].strftime('%H%M%S'), example.opening.close)
    at[0] = example.following.starts_at + timedelta(minutes=1, seconds=2)
    await collector._flush_snapshots(flush_latest=False)
    selected = store.load_close_gap_observations(example.symbol, example.auction.starts_at,
        example.auction.ends_at, example.following.starts_at, at[0])
    case.assertEqual(['closing_auction_price', 'minute_bar'], [row['kind'] for row in selected],
        [(row['kind'], row['effective_at'], row['available_at'], row['completeness'],
          row['payload'].get('bar_start'), row['payload'].get('bar_end'), row['payload'].get('capture_quality'))
         for kind in ('closing_auction_price', 'minute_bar') for row in store.load_observation_revisions(kind, example.symbol + ':KRX')[:2]])
    case.assertEqual(original, selected[0])
    case.assertEqual(example.opening.open, selected[1]['payload']['open'])
    case.assertEqual((), store.load_close_gap_observations(example.symbol, example.auction.starts_at,
        example.auction.ends_at, example.following.starts_at, at[0] - timedelta(microseconds=1)))
    await collector.close()
    case.assertEqual(0, collector.input_replay_status()['pending_records'])
    return list(selected)


class CloseDeadlineExecutionTests(unittest.TestCase):
    def test_v1_stopped_policy_does_not_gain_protective_cancel_authority(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-v1-stopped.sqlite3'
        verify_close_stop_native_fences(self, lambda: SQLiteQueryStore(db), stopped=True)

    def test_stopped_management_requires_explicit_v2_policy_and_preserves_v1_identity(self):
        from dataclasses import replace
        from kiwoom_monitor.domain.execution_activation import CloseMockExecutionPolicy

        original = CloseMockExecutionPolicy(100, 200, 'acknowledged_0B_bounded_by_signal/v1',
            'cancel_at_entry_deadline/v1', 'acknowledged_0B_bounded_slippage/v1')
        self.assertNotIn('stopped_management', original.to_dict())
        self.assertEqual(original, CloseMockExecutionPolicy(**original.to_dict()))
        with self.assertRaisesRegex(ValueError, 'unsupported CLOSE execution policy'):
            replace(original, stopped_management='continue_original_plan/v1')
        with self.assertRaisesRegex(ValueError, 'unsupported CLOSE execution policy'):
            replace(original, version='close-mock-execution-policy/v2')
        chosen = replace(original, version='close-mock-execution-policy/v2', stopped_management='continue_original_plan/v1')
        self.assertEqual(chosen, CloseMockExecutionPolicy(**chosen.to_dict()))

    def test_native_stop_rejects_changed_checkpoint_and_later_fill_cut_before_cancel(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-stop-fences.sqlite3'
        verify_close_stop_native_fences(self, lambda: SQLiteQueryStore(db))

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


def verify_close_stop_native_fences(case, store_factory, *, stopped=False):
    from tests.unit.test_mock_automation_specification import MockAutomationSpecificationTests, ACCOUNT_REF
    from kiwoom_monitor.application.close_mock_execution import prepare_close_entry_deadline_claim
    from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot, mock_automation_stop_loss_trigger
    from kiwoom_monitor.domain.order_contract import AccountSnapshot, BrokerFill, BrokerOrderSnapshot, OrderState
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery

    store = store_factory()
    case.addCleanup(store.close)
    store.initialize()
    context = MockAutomationSpecificationTests._verify_close_entry_claim(case, store)
    clock, buy, runtime, execution, repository = context.clock, context.buy, context.runtime, context.execution, context.repository
    clock[0] = datetime.fromisoformat(context.event['close_ipo_entry_setup']['entry_deadline']) - timedelta(seconds=1)
    order = BrokerOrderSnapshot(buy.broker_order_id, ACCOUNT_REF, buy.intent.symbol, OrderState.PARTIALLY_FILLED,
        2, 2, clock[0], (BrokerFill('native-stop-prefix', 2, 104, buy.intent.created_at + timedelta(seconds=1)),))
    current = runtime.reconcile(buy.intent.intent_id, order)
    recovery = MockAccountRecovery(AccountSnapshot(ACCOUNT_REF, 100_000, 208, {buy.intent.symbol: 2}, clock[0]), (order,))
    risk = build_mock_automation_risk_snapshot(recovery, execution.account_events('mock', ACCOUNT_REF).events,
        (), execution.active_intents('mock', ACCOUNT_REF, runtime.run_id), execution_run_id=runtime.run_id,
        binding_revision=context.spec.binding_revision, reconciliation_revision=2,
        observed_at=clock[0], broker_query_started_at=clock[0], broker_query_completed_at=clock[0])
    repository.save_mock_automation_risk_snapshot(risk)
    trigger = mock_automation_stop_loss_trigger(context.spec, risk, execution_run_id=runtime.run_id, now=clock[0],
        price={**context.quote, 'symbol': buy.intent.symbol, 'price_won': 94, 'available_at': clock[0]},
        account_positions=recovery.account.positions, stop_loss_bps=context.parameters['policy']['protection_loss_bps'])
    notice = dict(version='mock-protection-review/v1', state='PENDING', account_ref=ACCOUNT_REF,
        spec_id=context.spec.spec_id, execution_run_id=runtime.run_id, revision=1, order_authorized=False,
        transport_state='OFF', requested_at=clock[0].isoformat(), input_ref=trigger['stop_loss']['input_ref'], risk_trigger=trigger)
    checkpoint = dict(account_ref=ACCOUNT_REF, spec_id=context.spec.spec_id, execution_run_id=runtime.run_id,
                      candidate_package_hash=context.spec.candidate_package_hash, protection_review=notice)
    def save_checkpoint(value):
        store.upsert_documents('execution_mock_automation_runner_current', [{'owner': ACCOUNT_REF, 'key': 'current', 'document': value}])
    save_checkpoint(checkpoint)
    claim = prepare_close_entry_deadline_claim(repository, runtime, execution, context.spec, current,
        parameters=context.parameters, event=context.event, recovery=recovery, risk=risk, protection_notice=notice)
    revision = repository.load_mock_automation_control(ACCOUNT_REF).control_revision
    before = execution.events(buy.intent.intent_id)
    if stopped:
        from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation

        emergency_stop_mock_automation(repository, runtime, account_ref=ACCOUNT_REF, spec_id=context.spec.spec_id,
            stopped_at=clock[0], reason='synthetic v1 reconciliation-only stop', expected_control_revision=revision)
        revision += 1
        with patch.object(context.transport, 'cancel', side_effect=AssertionError('v1 stop reached transport')):
            with case.assertRaisesRegex(RuntimeError, 'MOCK_AUTOMATION_CONTROL_CHANGED'):
                runtime.cancel_automation_intent(current, spec_id=context.spec.spec_id, control_revision=revision, planned_cancel_claim=claim)
        case.assertEqual(before, execution.events(buy.intent.intent_id))
        return
    with patch.object(context.transport, 'cancel', side_effect=AssertionError('invalid stop reached transport')):
        changed = deepcopy(checkpoint)
        changed['protection_review']['revision'] += 1
        save_checkpoint(changed)
        with case.assertRaisesRegex(RuntimeError, 'CLOSE_DURABLE_STOP_CHANGED'):
            runtime.cancel_automation_intent(current, spec_id=context.spec.spec_id, control_revision=revision, planned_cancel_claim=claim)
        save_checkpoint(checkpoint)
        late_risk = risk.to_dict()
        late_risk['execution_event_high_watermark'] = 0
        store.upsert_documents(repository.MOCK_AUTOMATION_RISK_COLLECTION,
            [{'owner': ACCOUNT_REF, 'key': risk.snapshot_id, 'document': late_risk}])
        with case.assertRaisesRegex((RuntimeError, ValueError), 'CLOSE_STOP_NATIVE_FILL_CUT_CHANGED|snapshot_id'):
            runtime.cancel_automation_intent(current, spec_id=context.spec.spec_id, control_revision=revision, planned_cancel_claim=claim)
    case.assertEqual(before, execution.events(buy.intent.intent_id))
    case.assertEqual(OrderState.PARTIALLY_FILLED, execution.load(buy.intent.intent_id).state)


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


async def verify_close_runner(case, store_factory, *, protective=False, stopped=False, collector_gap=False,
                              tamper_collector_gap=False, corrected_gap=False, acceptance=False):
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
    from kiwoom_monitor.domain.execution_activation import CloseMockExecutionPolicy
    terms = (CloseMockExecutionPolicy(100, 200, 'acknowledged_0B_bounded_by_signal/v1', 'cancel_at_entry_deadline/v1',
        'acknowledged_0B_bounded_slippage/v1', version='close-mock-execution-policy/v2',
        stopped_management='continue_original_plan/v1') if stopped else None)
    context = MockAutomationSpecificationTests._verify_close_entry_claim(case, store, prepare_only=True,
        close_terms=terms, native_windows=collector_gap)
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
        from kiwoom_monitor.central_server.mock_runtime import (
            MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION, MockAccountRuntimeMode, MockAutomationRuntimeContext,
        )
        monitor = Monitor()
        async def refresh_account():
            return (await monitor.refresh_recovery()).account
        gateway = ManualMockOrderGateway(runtime, execution, refresh_account, now_provider=lambda: clock[0],
                                        automation_execution_cut=lambda: monitor.latest_automation_execution_cut)
        gateway.set_manual_submit_enabled(False)
        control = repository.load_mock_automation_control(ACCOUNT_REF)
        admission = repository.load_mock_automation_admission_for_spec(ACCOUNT_REF, context.spec.spec_id)
        bundle = SimpleNamespace(account_ref=ACCOUNT_REF, credential_profile_id=PROFILE_ID, run_id=runtime.run_id,
            runtime=runtime, repository=execution, monitor=monitor, gateway=gateway,
            runtime_mode=MockAccountRuntimeMode.AUTOMATIC,
            automation_context=MockAutomationRuntimeContext(MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION, ACCOUNT_REF,
                runtime.run_id, context.spec.spec_id, admission.admission_id, control.control_revision, 1))
        async def require_settings(profile_id, *, expected_settings_revision, credential_revision):
            case.assertEqual((PROFILE_ID, 1, 1), (profile_id, expected_settings_revision, credential_revision))
        supervisor = MockAutomationSupervisor(store, SimpleNamespace(bundle=lambda _: bundle,
            require_execution_settings=require_settings), poll_seconds=3600,
            price_source=lambda symbol: quotes.get(symbol), now_provider=lambda: clock[0])
        case.addAsyncCleanup(supervisor.close)
        case.addAsyncCleanup(gateway.close)
        if control.desired_state.value == 'RUNNING':
            status = await supervisor.activate(credential_profile_id=PROFILE_ID, account_ref=ACCOUNT_REF,
                spec_id=context.spec.spec_id, expected_settings_revision=1, credential_revision=1)
            case.assertEqual(('AUTOMATIC', context.spec.spec_id),
                             (status['runtime_mode'], status['control']['active_spec_id']))
            runner = supervisor._runners[ACCOUNT_REF]
        else:
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
    if stopped:
        from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation

        control = repository.load_mock_automation_control(ACCOUNT_REF)
        emergency_stop_mock_automation(repository, runtime, account_ref=ACCOUNT_REF, spec_id=context.spec.spec_id,
            stopped_at=clock[0], reason='synthetic explicit original-plan management',
            expected_control_revision=control.control_revision)
        stopped_control = repository.load_mock_automation_control(ACCOUNT_REF)
        case.assertEqual('STOPPED', stopped_control.desired_state.value)
        clock[0] += timedelta(seconds=1)
        await monitor.refresh_recovery()
        execution.bind_automation_control(spec_id=context.spec.spec_id, control_revision=stopped_control.control_revision)
        try:
            with case.assertRaisesRegex(RuntimeError, 'MOCK_AUTOMATION_CONTROL_CHANGED'):
                execution.create(replace(buy.intent, intent_id=buy.intent.intent_id + '-forbidden-new-entry'))
        finally:
            execution.clear_automation_control()
    if not protective:
        clock[0] = deadline
    cancellations = []
    def cancel(*args, **kwargs):
        cancellations.append((args, kwargs))
        if protective:
            saved = store.load_documents('execution_mock_automation_runner_current', ACCOUNT_REF, 1)[0]['document']
            case.assertEqual(first_notice, saved['protection_review'])
        return BrokerSubmission(buy.broker_order_id, clock[0])
    with patch.object(context.transport, 'cancel', side_effect=cancel):
        if protective:
            stopped_quote = {**quotes[buy.intent.symbol], 'price_won': 94, 'available_at': clock[0],
                             'input_ref': 'collector-trade:close-first-stop'}
            previous_notice = runner.episode._protection_snapshot()
            with patch.object(store, 'load_documents', side_effect=AssertionError('0B callback must not read DB')), \
                    patch.object(store, 'upsert_documents', side_effect=AssertionError('0B callback must not write DB')):
                supervisor.observe_automation_price(buy.intent.symbol,
                    {**stopped_quote, 'available_at': clock[0] - timedelta(seconds=1)})
                case.assertEqual(previous_notice, runner.episode._protection_snapshot())
                supervisor.observe_automation_price(buy.intent.symbol, stopped_quote)
                first_notice = runner.episode._protection_snapshot()
                case.assertEqual(94, first_notice['risk_trigger']['stop_loss']['price_won'])
                quotes[buy.intent.symbol] = {**stopped_quote, 'price_won': 103, 'input_ref': 'collector-trade:close-rebound'}
                supervisor.observe_automation_price(buy.intent.symbol, quotes[buy.intent.symbol])
            case.assertEqual(first_notice, runner.episode._protection_snapshot())
            case.assertLess(clock[0], deadline)
            async def cancelled():
                for _ in range(1000):
                    if cancellations and not runner.episode.execution_busy:
                        return
                    await asyncio.sleep(0.01)
            await cancelled()
        else:
            await runner.episode.advance_once()
    case.assertEqual(1, len(cancellations), runner.episode.status)
    case.assertEqual(OrderState.CANCEL_PENDING, execution.load(buy.intent.intent_id).state)
    if protective:
        cancel_event, = [row for row in execution.events(buy.intent.intent_id) if row['event_type'] == 'CANCEL_STARTED']
        case.assertEqual('close-entry-protective-cancel/v1', cancel_event['close_entry_cancel_claim']['version'])
        case.assertEqual(first_notice, cancel_event['close_entry_cancel_claim']['protection_notice'])
    clock[0] += timedelta(seconds=1)
    fills = (*fills, BrokerFill('runner-late-fill', 1, 105, clock[0]))
    broker_positions[buy.intent.symbol] = 3
    broker_orders[buy.broker_order_id] = replace(broker_orders[buy.broker_order_id], state=OrderState.CANCELLED,
                                               filled_quantity=3, remaining_quantity=0, as_of=clock[0], fills=fills)
    await monitor.refresh_recovery()
    await supervisor.close()
    case.assertFalse(monitor.enabled)
    if protective:
        quotes.pop(buy.intent.symbol)
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
    if acceptance:
        from datetime import time

        before = execution.load_plan_position(ACCOUNT_REF, runtime.run_id, buy.intent.symbol, buy.intent.plan)
        # The admitted calendar deliberately contains a weekend and a synthetic
        # Monday holiday. No completed bar or forced day-close may create an exit.
        for cut in (context.example.auction.ends_at + timedelta(seconds=1),
                    datetime.combine(context.example.friday + timedelta(days=1), time(12), context.clock[0].tzinfo),
                    datetime.combine(context.example.friday + timedelta(days=3), time(12), context.clock[0].tzinfo)):
            clock[0] = cut
            quotes[buy.intent.symbol] = {**context.quote, 'price_won': 103, 'available_at': cut}
            await monitor.refresh_recovery()
            await runner.episode.advance_once()
            held = execution.load_plan_position(ACCOUNT_REF, runtime.run_id, buy.intent.symbol, buy.intent.plan)
            case.assertEqual((before.plan.plan_id, 3, 0), (held.plan.plan_id, held.quantity, held.unreconciled_sell_quantity))
            case.assertEqual(['BUY'], [row[1] for row in context.transport.calls])
        clock[0] = datetime.fromisoformat(context.example.opening.available_at)
        for unavailable in (None, {**context.quote, 'price_won': 103, 'available_at': clock[0], 'market_status': 'HALTED'},
                             {**context.quote, 'price_won': 103}):
            if unavailable is None:
                quotes.pop(buy.intent.symbol, None)
            else:
                quotes[buy.intent.symbol] = unavailable
            await monitor.refresh_recovery()
            await runner.episode.advance_once()
            case.assertEqual(['BUY'], [row[1] for row in context.transport.calls])
            case.assertEqual((), execution.active_intents('mock', ACCOUNT_REF, runtime.run_id))
    clock[0] = datetime.fromisoformat(context.example.opening.available_at)
    quotes[buy.intent.symbol] = {**context.quote, 'price_won': 103, 'available_at': clock[0]}
    if protective:
        case.assertEqual(first_notice, runner.episode._protection_snapshot())
        case.assertGreater((clock[0] - datetime.fromisoformat(first_notice['requested_at'])).total_seconds(),
                           context.spec.maximum_data_gap_seconds)
    await monitor.refresh_recovery()
    await runner.episode.advance_once()
    if not protective:
        case.assertEqual(['BUY'], [row[1] for row in context.transport.calls])
    if corrected_gap:
        # A complete initially known opening price says HOLD. A later complete
        # revision of that same minute must replace it before any exit is fixed.
        original = context.example.opening
        context.example.opening = replace(original, open=105, high=105)
        publish_gap_observations(store, context.example, clock)
        await runner.episode.advance_once()
        case.assertEqual(['BUY'], [row[1] for row in context.transport.calls])
        case.assertEqual('planned_overnight_hold', runner.episode._minute_management.status['reason'])
        old_cut = clock[0]
        clock[0] += timedelta(seconds=1)
        context.example.opening = replace(original, available_at=clock[0].isoformat())
        quotes[buy.intent.symbol] = {**quotes[buy.intent.symbol], 'available_at': clock[0]}
    expected_gap = (await publish_collector_gap_observations(case, store, context.example) if collector_gap else
                    publish_gap_observations(store, context.example, clock))
    if corrected_gap:
        before = store.load_close_gap_observations(buy.intent.symbol, context.example.auction.starts_at,
            context.example.auction.ends_at, context.example.following.starts_at, old_cut)
        case.assertEqual(105, before[1]['payload']['open'])
    selected = store.load_close_gap_observations(buy.intent.symbol, context.example.auction.starts_at,
        context.example.auction.ends_at, context.example.following.starts_at, clock[0])
    case.assertEqual(expected_gap, list(selected))
    if not corrected_gap:
        case.assertEqual((), store.load_close_gap_observations(buy.intent.symbol, context.example.auction.starts_at,
            context.example.auction.ends_at, context.example.following.starts_at, clock[0] - timedelta(microseconds=1)))
    from datetime import timezone
    case.assertEqual(selected, store.load_close_gap_observations(buy.intent.symbol,
        context.example.auction.starts_at.astimezone(timezone.utc), context.example.auction.ends_at.astimezone(timezone.utc),
        context.example.following.starts_at.astimezone(timezone.utc), clock[0].astimezone(timezone.utc)))
    if tamper_collector_gap:
        from kiwoom_monitor.application.close_mock_execution import CLOSE_EXIT_COLLECTION, close_exit_key

        original_submit = runtime.submit_automation_intent
        def change_frozen_closing_price(intent, account, **kwargs):
            proof = repository.load_close_exit_evidence(ACCOUNT_REF, close_exit_key(intent.plan))
            changed = deepcopy(proof)
            changed['snapshot']['gap_observations'][0]['payload']['price_won'] += 1
            store.upsert_documents(CLOSE_EXIT_COLLECTION,
                [{'owner': ACCOUNT_REF, 'key': close_exit_key(intent.plan), 'document': changed}])
            return original_submit(intent, account, **kwargs)
        with patch.object(runtime, 'submit_automation_intent', side_effect=change_frozen_closing_price):
            with case.assertRaisesRegex(RuntimeError, 'CLOSE_EXIT_PROOF_OR_ORDER_CHANGED'):
                await runner.episode.advance_once()
        case.assertEqual(['BUY'], [row[1] for row in context.transport.calls])
        case.assertEqual((), execution.active_intents('mock', ACCOUNT_REF, runtime.run_id))
        head = execution.load_plan_position(ACCOUNT_REF, runtime.run_id, buy.intent.symbol, buy.intent.plan)
        case.assertEqual((3, 0), (head.quantity, head.unreconciled_sell_quantity))
        await supervisor.close()
        return
    await runner.episode.advance_once()
    pending = execution.active_intents('mock', ACCOUNT_REF, runtime.run_id)
    case.assertEqual(1, len(pending), runner.episode.status)
    sell, = pending
    case.assertEqual((OrderState.ACCEPTED, 3, 103, buy.intent.plan.plan_id),
                     (sell.state, sell.intent.quantity, sell.intent.limit_price, sell.intent.plan.plan_id))
    if protective:
        from kiwoom_monitor.application.close_mock_execution import close_exit_key
        evidence = repository.load_close_exit_evidence(ACCOUNT_REF, close_exit_key(sell.intent.plan))
        case.assertEqual(('close_exit_snapshot/v2', 94, 103, 'filled_position_protection_0B'),
            (evidence['snapshot']['version'], evidence['snapshot']['trigger_quote']['price_won'],
             evidence['snapshot']['quote']['price_won'], evidence['snapshot']['reason']))
    if collector_gap:
        from kiwoom_monitor.application.close_mock_execution import close_exit_key
        evidence = repository.load_close_exit_evidence(ACCOUNT_REF, close_exit_key(sell.intent.plan))
        case.assertEqual(('close_exit_snapshot/v3', 'adverse_gap_full_exit_closing_trade_and_completed_open'),
                         (evidence['snapshot']['version'], evidence['snapshot']['reason']))
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
    if stopped:
        case.assertEqual(stopped_control, repository.load_mock_automation_control(ACCOUNT_REF))
        case.assertEqual('STOPPED', runner.episode._minute_management.status['state'])
    if acceptance:
        from tests.unit.test_mock_automation_specification import _ExecutionPageSource
        from kiwoom_monitor.application.journal_enrichment import sync_journal_execution_projection
        from kiwoom_monitor.application.trade_history_query_service import TradeHistoryQueryService
        from kiwoom_monitor.application.trade_history_service import TradeFill
        from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository

        scope = AccountScope('kiwoom', AccountEnvironment.MOCK, ACCOUNT_REF)
        directory = case.enterContext(tempfile.TemporaryDirectory())
        journal_path = Path(directory) / 'close-journal.sqlite3'
        journal = JournalRepository(journal_path)
        source = _ExecutionPageSource(execution, ACCOUNT_REF)
        projected = sync_journal_execution_projection(source, journal, scope, page_limit=2, max_pages=100, now=clock[0])
        case.assertEqual(3, projected.detailed_fill_count)
        rows = JournalRepository(journal_path).load_execution_fill_projections(scope)
        case.assertEqual({'runner-first-fill', 'runner-late-fill', 'runner-exit'},
                         {row['broker_execution_id'] for row in rows})
        case.assertEqual({buy.intent.plan.plan_id}, {row['plan']['plan_id'] for row in rows})
        case.assertEqual({'BUY': 3, 'SELL': 3},
                         {side: sum(row['quantity'] for row in rows if row['side'] == side) for side in ('BUY', 'SELL')})
        # Broker summaries stay separate from native details. The screen query
        # reconciles the two evidence sets and carries the original plan refs.
        summaries = tuple(TradeFill(order.broker_order_id, buy.intent.symbol, 'synthetic CLOSE',
            '매수' if order.broker_order_id == buy.broker_order_id else '매도',
            fill.occurred_at.replace(tzinfo=None), fill.quantity, fill.price, origin_scope=scope)
            for order in broker_orders.values() for fill in order.fills)
        journal.upsert_fills(summaries, account_scope=scope)
        visible = TradeHistoryQueryService(JournalRepository(journal_path)).load(
            datetime.combine(context.example.friday, datetime.min.time()),
            datetime.combine(context.example.tuesday + timedelta(days=1), datetime.min.time()), scope)
        case.assertEqual(set(summaries), set(visible.fills))
        case.assertEqual(set(summaries), set(visible.execution_plans))
        case.assertEqual({buy.intent.plan.plan_id}, {plan.plan_id for plan in visible.execution_plans.values()})
        repeated = sync_journal_execution_projection(source, JournalRepository(journal_path), scope, now=clock[0])
        case.assertEqual(0, repeated.inserted_event_count)
        case.assertEqual(rows, JournalRepository(journal_path).load_execution_fill_projections(scope))
        case.assertEqual({}, broker_positions)
    await supervisor.close()


class CloseRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_acceptance_preserves_holidays_halt_and_final_journal_plan_identity(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-acceptance.sqlite3'
        await verify_close_runner(self, lambda: SQLiteQueryStore(db), collector_gap=True, acceptance=True)

    async def test_later_available_opening_revision_replaces_hold_before_gap_exit(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-gap-correction.sqlite3'
        await verify_close_runner(self, lambda: SQLiteQueryStore(db), corrected_gap=True)

    async def test_collector_gap_rechecks_original_closing_price_before_native_order_claim(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-collector-gap-tamper.sqlite3'
        await verify_close_runner(self, lambda: SQLiteQueryStore(db), collector_gap=True, tamper_collector_gap=True)

    async def test_actual_collector_closing_price_and_first_minute_drive_original_native_gap_exit(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-collector-gap.sqlite3'
        await verify_close_runner(self, lambda: SQLiteQueryStore(db), collector_gap=True)

    async def test_explicit_stopped_plan_management_keeps_deadline_and_gap_exit_without_price_stop(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-stopped-gap.sqlite3'
        await verify_close_runner(self, lambda: SQLiteQueryStore(db), stopped=True)

    async def test_explicit_stopped_plan_management_cancels_and_exits_without_resuming_entries(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-stopped.sqlite3'
        await verify_close_runner(self, lambda: SQLiteQueryStore(db), protective=True, stopped=True)

    async def test_first_0B_stop_cancels_partial_buy_and_survives_rebound_late_fill_and_restart(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-stop.sqlite3'
        await verify_close_runner(self, lambda: SQLiteQueryStore(db), protective=True)

    async def test_flat_runner_clears_previous_pending_order_without_new_shadow(self):
        from types import SimpleNamespace
        from kiwoom_monitor.central_server.minute_plan_management import MinutePlanManagement

        store = SimpleNamespace(load_shadow_candidates=lambda *_: {'events': [], 'high_watermark': 7})
        management = MinutePlanManagement(store, None, None, None, parameters={}, price_source=None, now_provider=None)
        management.status = {'state': 'ORDER_SUBMITTED', 'pending_intent_id': 'already-filled', 'orders_enabled': False}
        control = SimpleNamespace(desired_state=SimpleNamespace(value='RUNNING'))
        self.assertEqual(0, await management.advance(control, limit=10, allow_entry=lambda: True))
        self.assertEqual('RUNNING', management.status['state'])
        self.assertNotIn('pending_intent_id', management.status)

    async def test_runner_enters_cancels_restores_retained_plan_and_exits_native_gap(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore

        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'close-runner.sqlite3'
        await verify_close_runner(self, lambda: SQLiteQueryStore(db))
