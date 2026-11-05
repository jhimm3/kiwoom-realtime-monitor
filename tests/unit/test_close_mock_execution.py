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
