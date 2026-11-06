"""Native additional/reduction actions, not cancellation-replacement aliases."""
from datetime import timedelta
from unittest.mock import patch
import unittest


async def staged_leader(case, factory=None, *, additional_quantity=3):
    from tests.unit import test_mock_automation_specification as support
    from tests.unit.test_leader_order_management import current_leader
    from kiwoom_monitor.domain.strategy_contract import LeaderBreakoutConfig
    build = support.mock_specification_inputs

    def declared(*args, **kwargs):
        config = kwargs['leader_config'].to_dict()
        config['execution_contract'].update(version='leader_breakout_execution/v2', position_steps=[
            dict(step_id='new-lot', action='ADD', trigger='observed_cross_above', reference_price=1005,
                 quantity=additional_quantity, reason_ref='synthetic:new-reconfirmation-lot'),
            dict(step_id='partial-profit', action='REDUCE', trigger='price_at_or_above', reference_price=1010,
                 quantity=2, reason_ref='synthetic:partial-profit')])
        kwargs['leader_config'] = LeaderBreakoutConfig.from_dict(config)
        return build(*args, **kwargs)

    with patch.object(support, 'mock_specification_inputs', side_effect=declared):
        buy = await current_leader(case, factory)
    case.live['enabled'] = True
    case.native_outcomes = []
    dispatch = case.runner.episode._dispatch_original_and_register
    def observed_dispatch(*args, **kwargs):
        result = dispatch(*args, **kwargs)
        case.native_outcomes.append((args[0].final_action, tuple(result[0].reasons),
            result[1].intent.intent_id if result[1] else None))
        return result
    case.enterContext(patch.object(case.runner.episode, '_dispatch_original_and_register', side_effect=observed_dispatch))
    return buy


async def fill(case, record, quantity, identity, *, broker_order_id=None):
    case.book.context.clock[0] = case.clock
    case.book.fill(broker_order_id or record.broker_order_id, quantity, identity)
    await case.bundle.monitor.refresh_recovery()
    await case.runner.episode.advance_once()


async def add_lot(case, first):
    await fill(case, first, 5, 'initial-five')
    case.assertEqual(5, case.runner.episode._episode_position.state.position_quantity)
    await case.deliver(1004)
    await case.deliver(1006)
    record = case.execution.load(case.runner.episode.status.get('pending_intent_id', ''))
    case.assertIsNotNone(record, case.runner.episode.status)
    case.assertNotEqual(first.intent.intent_id, record.intent.intent_id, case.runner.episode.status)
    case.assertEqual(('BUY', 3, first.intent.plan.plan_id),
        (record.intent.side.value, record.intent.quantity, record.intent.plan.plan_id))
    return record


async def verify_staged_round_trip(case, factory=None):
    from kiwoom_monitor.domain.order_contract import OrderSide
    first = await staged_leader(case, factory)
    additional = await add_lot(case, first)
    case.assertEqual(['BUY', 'BUY'], [row[1] for row in case.transport.calls])
    gate = case.repository.load_mock_automation_approved_gate(case.bundle.account_ref, additional.intent.intent_id)
    case.assertEqual(('ADD', 'mock_automation_plan_action_gate/v1'), (gate.action, gate.version))
    case.assertIsNone(gate.replacement_binding)
    case.assertTrue(gate.position_action_ref.startswith('leader-plan-action/v1:'))
    await fill(case, additional, 3, 'actual-new-three')
    case.assertEqual(8, case.runner.episode._episode_position.state.position_quantity)
    case.live['enabled'] = False
    case.settings['shadow_candidate_enabled'] = False
    case.store.upsert_documents('server_operational_settings', [dict(owner='global', key='current', document=case.settings)])
    await case.deliver(1011)
    reduced = case.execution.load(case.runner.episode.status.get('pending_intent_id', ''))
    case.assertIsNotNone(reduced, case.runner.episode.status)
    case.assertEqual((OrderSide.SELL, 2, first.intent.plan.plan_id),
        (reduced.intent.side, reduced.intent.quantity, reduced.intent.plan.plan_id), case.runner.episode.status)
    gate = case.repository.load_mock_automation_approved_gate(case.bundle.account_ref, reduced.intent.intent_id)
    case.assertEqual('REDUCE', gate.action)
    await fill(case, reduced, 2, 'actual-reduced-two')
    case.assertEqual(6, case.runner.episode._episode_position.state.position_quantity)
    before = len(case.transport.calls)
    await case.deliver(940)
    case.assertEqual(before, len(case.transport.calls))
    original = case.runner.episode._protection_snapshot()
    case.assertEqual(6, original['planned_leader_stop']['stop_loss']['quantity'])
    # A later execution quote may rebound; it does not erase the first stop.
    await case.deliver(960)
    await case.bundle.monitor.refresh_recovery()
    await case.runner.episode.advance_once()
    protective = case.execution.load(case.runner.episode.status.get('pending_intent_id', ''))
    case.assertIsNotNone(protective, case.runner.episode.status)
    case.assertEqual((OrderSide.SELL, 6, 'mock-protection-exit/v1'),
        (protective.intent.side, protective.intent.quantity, protective.intent.policy_version),
        (case.native_outcomes, case.runner.episode.status.get('planned_protection_reason')))
    case.assertEqual(original['planned_leader_stop'], case.runner.episode._protection_snapshot()['planned_leader_stop'])
    await fill(case, protective, 6, 'remaining-six')
    head = case.execution.load_plan_position(case.bundle.account_ref, case.bundle.run_id, first.intent.symbol, first.intent.plan)
    case.assertEqual((0, 0), (head.quantity, head.unreconciled_sell_quantity))
    case.assertEqual(['BUY', 'BUY', 'SELL', 'SELL'], [row[1] for row in case.transport.calls])
    from pathlib import Path
    import tempfile
    from kiwoom_monitor.application.journal_enrichment import sync_journal_execution_projection
    from kiwoom_monitor.application.trade_history_query_service import TradeHistoryQueryService
    from kiwoom_monitor.application.trade_history_service import TradeFill
    from kiwoom_monitor.domain.time_basis import as_kst
    from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
    from tests.unit.test_mock_automation_specification import _ExecutionPageSource
    scope = case.spec.account_scope
    with tempfile.TemporaryDirectory() as directory:
        journal = JournalRepository(Path(directory) / 'staged-journal.sqlite3')
        projected = sync_journal_execution_projection(_ExecutionPageSource(case.execution, scope.account_ref),
            journal, scope, page_limit=1000, now=case.clock)
        rows = journal.load_execution_fill_projections(scope)
        case.assertEqual(4, projected.detailed_fill_count)
        case.assertEqual([('BUY', 5), ('BUY', 3), ('SELL', 2), ('SELL', 6)],
            [(row['side'], row['quantity']) for row in rows])
        case.assertEqual({first.intent.plan.plan_id}, {row['plan']['plan_id'] for row in rows})
        # Independent fake-broker summaries join the actual central detail page;
        # the same existing query used by the journal view must keep one copy.
        summaries = tuple(TradeFill(number, first.intent.symbol, '합성 종목',
            '매수' if row['side'] is OrderSide.BUY else '매도', as_kst(item.occurred_at).replace(tzinfo=None),
            item.quantity, item.price, origin_scope=scope)
            for number, row in case.book.rows.items() for item in row['fills'])
        journal.upsert_fills(summaries, account_scope=scope)
        at = as_kst(case.clock).replace(tzinfo=None)
        view = TradeHistoryQueryService(journal).load(at - timedelta(hours=1), at + timedelta(hours=1), scope)
        case.assertEqual(4, len(view.fills))
        case.assertEqual(4, len(view.execution_plans))
        case.assertEqual({first.intent.plan.plan_id}, {plan.plan_id for plan in view.execution_plans.values()})


async def verify_current_entry_off(case, factory=None):
    from kiwoom_monitor.application.leader_breakout import leader_next_position_step, _id
    first = await staged_leader(case, factory)
    await fill(case, first, 5, 'initial-five')
    episode = case.runner.episode
    head = case.execution.load_plan_position(case.bundle.account_ref, case.bundle.run_id, first.intent.symbol, first.intent.plan)
    key = leader_next_position_step(episode._config, episode._episode_position.state)[2]
    queue = case.runtime._lifecycle.queue_planned
    def disabled(intent):
        case.settings['shadow_candidate_enabled'] = False
        case.store.upsert_documents('server_operational_settings', [dict(owner='global', key='current', document=case.settings)])
        return queue(intent)
    await case.deliver(1004)
    with patch.object(case.runtime._lifecycle, 'queue_planned', side_effect=disabled):
        with case.assertRaisesRegex(RuntimeError, 'LEADER_CURRENT_ENTRY_DISABLED'):
            await case.deliver(1006)
    case.assertEqual(['BUY'], [row[1] for row in case.transport.calls])
    case.assertEqual(head, case.execution.load_plan_position(case.bundle.account_ref, case.bundle.run_id,
        first.intent.symbol, first.intent.plan))
    case.assertIsNone(case.store.load_document('execution_leader_position_actions', case.bundle.account_ref,
        _id('leader-plan-step-claim/v1', [first.intent.plan.plan_id, key])))


async def verify_cumulative_entry_budget(case, factory=None):
    first = await staged_leader(case, factory, additional_quantity=6)
    await fill(case, first, 5, 'initial-five')
    head = case.execution.load_plan_position(case.bundle.account_ref, case.bundle.run_id, first.intent.symbol, first.intent.plan)
    await case.deliver(1004)
    # Incremental order alone fits 10000; actual first 5005 + new 6036 does not.
    with case.assertRaisesRegex(RuntimeError, 'LEADER_POSITION_ENTRY_BUDGET_EXCEEDED'):
        await case.deliver(1006)
    case.assertEqual(['BUY'], [row[1] for row in case.transport.calls])
    case.assertEqual(5, case.runner.episode._episode_position.state.position_quantity)
    case.assertEqual(head, case.execution.load_plan_position(case.bundle.account_ref, case.bundle.run_id,
        first.intent.symbol, first.intent.plan))


async def verify_additional_current_management(case, factory=None):
    first = await staged_leader(case, factory)
    additional = await add_lot(case, first)
    case.clock = additional.intent.expires_at + timedelta(milliseconds=100)
    await case.deliver(1007)
    case.assertEqual([('AMEND', additional.broker_order_id, 'amended-1', 3, 1007)], case.book.mutations,
        case.runner.episode.status)
    case.assertEqual(additional.intent, case.execution.load(additional.intent.intent_id).intent)
    await case.deliver(1004)  # Still above the initial 1000, below this new lot's 1005.
    # Existing broker API quantity=0 cancels all remaining shares of this
    # physical order. The native claim still binds the exact three shares.
    case.assertEqual(('CANCEL', 'amended-1', 0), case.book.mutations[-1], case.runner.episode.status)
    event = next(row for row in reversed(case.execution.events(additional.intent.intent_id))
                 if row['event_type'] == 'CANCEL_STARTED')
    claim = event['leader_order_management_claim']
    case.assertEqual(('amended-1', 3, 'CANCEL'), (claim['broker_order_id'], claim['quantity'], claim['action']))
    case.assertEqual(['BUY', 'BUY'], [row[1] for row in case.transport.calls])
    await case.bundle.monitor.refresh_recovery()
    head = case.execution.load_plan_position(case.bundle.account_ref, case.bundle.run_id, first.intent.symbol, first.intent.plan)
    case.assertEqual(5, head.quantity)


async def verify_staged_restart(case, factory=None):
    from tests.unit.test_mock_automation_runner import _prepared_runner
    first = await staged_leader(case, factory)
    await fill(case, first, 5, 'initial-five')
    case.runner.episode._save_checkpoint(force=True)
    old = case.store.load_document('execution_mock_automation_runner_current', case.bundle.account_ref, 'current')['document']
    await case.deliver(1004)
    await case.deliver(1006)
    additional = case.execution.load(case.runner.episode.status['pending_intent_id'])
    case.assertEqual(3, additional.intent.quantity)
    await case.runner.close()
    # Exact earlier persisted checkpoint models loss after the native order COMMIT.
    case.store.upsert_documents('execution_mock_automation_runner_current', [
        dict(owner=case.bundle.account_ref, key='current', document=old)])
    case.runner = _prepared_runner(case.store, case.bundle, case.spec, now_provider=lambda: case.clock,
        leader_context_source=lambda: case.live)
    case.addAsyncCleanup(case.runner.close)
    await case.bundle.monitor.refresh_recovery()
    await case.runner.episode.advance_once()
    case.assertEqual(1, len(case.runner.episode._leader_staged_facts['claims']))
    await case.deliver(1007)
    case.assertEqual(['BUY', 'BUY'], [row[1] for row in case.transport.calls])
    case.assertEqual(additional.intent, case.execution.load(additional.intent.intent_id).intent)
    number = next(number for number, row in case.book.rows.items() if row['remaining'] == 3)
    await fill(case, case.execution.load(additional.intent.intent_id), 3, 'restored-new-three', broker_order_id=number)
    case.assertEqual(8, case.runner.episode._episode_position.state.position_quantity)


async def verify_staged_claim_ack_loss(case, factory=None):
    from tests.unit.test_mock_automation_runner import _prepared_runner
    first = await staged_leader(case, factory)
    await fill(case, first, 5, 'initial-five')
    case.runner.episode._save_checkpoint(force=True)
    old = case.store.load_document('execution_mock_automation_runner_current', case.bundle.account_ref, 'current')['document']
    await case.deliver(1004)
    create = case.store.create_execution_intent
    committed = []
    def lost_ack(value, **kwargs):
        result = create(value, **kwargs)
        committed.append(value['intent_id'])
        case.assertTrue(result)
        raise OSError('additional native QUEUED commit ACK lost')
    with patch.object(case.store, 'create_execution_intent', side_effect=lost_ack):
        with case.assertRaisesRegex(OSError, 'ACK lost'):
            await case.deliver(1006)
    additional, = [case.execution.load(identity) for identity in committed]
    case.assertEqual(('QUEUED', 'BUY', 3),
        (additional.state.value, additional.intent.side.value, additional.intent.quantity))
    case.assertEqual(['BUY'], [row[1] for row in case.transport.calls])
    await case.runner.close()
    case.store.upsert_documents('execution_mock_automation_runner_current', [
        dict(owner=case.bundle.account_ref, key='current', document=old)])
    case.runner = _prepared_runner(case.store, case.bundle, case.spec, now_provider=lambda: case.clock,
        leader_context_source=lambda: case.live)
    case.addAsyncCleanup(case.runner.close)
    await case.bundle.monitor.refresh_recovery()
    await case.runner.episode.advance_once()
    case.assertEqual(1, len(case.runner.episode._leader_staged_facts['claims']))
    for price in (1007, 1004, 1006):
        await case.deliver(price)
    case.assertEqual(['BUY'], [row[1] for row in case.transport.calls])
    case.assertEqual(5, case.runner.episode._episode_position.state.position_quantity)
    case.assertEqual('QUEUED', case.execution.load(additional.intent.intent_id).state.value)
    case.clock = max(case.clock, additional.intent.expires_at)+timedelta(seconds=1)
    await case.deliver(1007)
    # Episode calls the existing native cancel after unsent TTL. The native
    # release is REJECTED, with no broker cancellation or network retry.
    case.assertEqual(('REJECTED', ['BUY']),
        (case.execution.load(additional.intent.intent_id).state.value, [row[1] for row in case.transport.calls]))
    case.assertEqual(['PLANNED_UNSENT_CANCELLED'],
        [event['event_type'] for event in case.execution.events(additional.intent.intent_id)])


async def verify_staged_cancel_unknown_late_fill(case, factory=None):
    from kiwoom_monitor.domain.order_contract import OrderState
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
    first = await staged_leader(case, factory)
    additional = await add_lot(case, first)
    # Reuse a raw execution ID across two actual orders; it is not one fill.
    await fill(case, additional, 2, 'initial-five')
    case.assertEqual(7, case.runner.episode._episode_position.state.position_quantity)
    def unknown_cancel(intent, number, quantity=0):
        event = case.execution.events(intent.intent_id)[-1]
        case.assertEqual('CANCEL_STARTED', event['event_type'])
        claim = event['leader_order_management_claim']
        case.assertEqual((additional.intent.intent_id, number, 1, 'CANCEL'),
            (claim['intent_id'], claim['broker_order_id'], claim['quantity'], claim['action']))
        case.book.mutations.append(('CANCEL', number, quantity))
        raise SubmissionUnknown('isolated broker cancellation response lost')
    with patch.object(case.transport, 'cancel', side_effect=unknown_cancel):
        await case.deliver(940)
        notice = case.runner.episode._protection_snapshot()
        case.assertEqual(7, notice['planned_leader_stop']['stop_loss']['quantity'])
        case.assertIs(OrderState.CANCEL_PENDING, case.execution.load(additional.intent.intent_id).state)
        await case.runner.episode.advance_once()
        await case.bundle.monitor._queue.join()
        case.assertEqual([('CANCEL', additional.broker_order_id, 0)], case.book.mutations)
        case.assertEqual(['BUY', 'BUY'], [row[1] for row in case.transport.calls])
    case.clock += timedelta(milliseconds=100)
    case.book.context.clock[0] = case.clock
    case.book.fill(additional.broker_order_id, 1, 'late-new-lot')
    await case.bundle.monitor.refresh_recovery()
    await case.runner.episode.advance_once()
    current = case.execution.load(additional.intent.intent_id)
    case.assertEqual((OrderState.FILLED, 3), (current.state, current.detailed_filled_quantity))
    case.assertEqual(8, case.runner.episode._episode_position.state.position_quantity)
    case.assertEqual(notice['planned_leader_stop'], case.runner.episode._protection_snapshot()['planned_leader_stop'])
    await case.deliver(960)
    await case.bundle.monitor.refresh_recovery()
    await case.runner.episode.advance_once()
    active = case.execution.active_intents('mock', case.bundle.account_ref, case.bundle.run_id)
    case.assertEqual(1, len(active), case.runner.episode.status)
    protective = active[0]
    case.assertEqual(('SELL', 8, first.intent.plan.plan_id, 'mock-protection-exit/v1'),
        (protective.intent.side.value, protective.intent.quantity, protective.intent.plan.plan_id,
         protective.intent.policy_version), case.runner.episode.status)
    case.assertEqual(notice['planned_leader_stop'], case.runner.episode._protection_snapshot()['planned_leader_stop'])
    await fill(case, protective, 8, 'all-actual-eight')
    head = case.execution.load_plan_position(case.bundle.account_ref, case.bundle.run_id, first.intent.symbol, first.intent.plan)
    case.assertEqual((0, 0), (head.quantity, head.unreconciled_sell_quantity))
    case.assertEqual(['BUY', 'BUY', 'SELL'], [row[1] for row in case.transport.calls])


async def verify_staged_manual_quantity_isolation(case, factory=None):
    from dataclasses import replace
    from kiwoom_monitor.domain.order_contract import OrderIntent, OrderSide, OrderState, OrderType, order_event
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
    first = await staged_leader(case, factory)
    additional = await add_lot(case, first)
    await fill(case, additional, 3, 'new-three')
    past = case.clock - timedelta(days=7)
    manual = OrderIntent('archived-manual-buy', 'independent-manual-run', 'archived-manual-decision',
        case.bundle.account_ref, 'mock', first.intent.symbol, 'KRX', OrderSide.BUY, 4, OrderType.LIMIT,
        1000, past, past + timedelta(minutes=1), 'archived-manual-fact/v1')
    # Import already completed historical facts only. No second runtime, lease,
    # submit permission or broker transport is created for these four shares.
    ledger = ExecutionRepository(case.store)
    record = ledger.create(manual)
    for kind, state, changes, values in (
        ('QUEUED', OrderState.QUEUED, {}, {}),
        ('SUBMISSION_STARTED', OrderState.SUBMISSION_UNKNOWN, {}, {}),
        ('ORDER_ACCEPTED', OrderState.ACCEPTED, {'broker_order_id': 'archived-manual-order'},
         {'broker_order_id': 'archived-manual-order'}),
        ('FILL', OrderState.FILLED, {'filled_quantity': 4, 'detailed_filled_quantity': 4,
          'broker_reported_filled_quantity': 4, 'fill_ids': ('archived-manual-fill',), 'last_broker_as_of': past},
         {'broker_order_id': 'archived-manual-order', 'broker_execution_id': 'archived-manual-fill',
          'quantity': 4, 'price': 1000, 'broker_as_of': past}),
        ('BROKER_RECONCILED', OrderState.FILLED, {},
         {'broker_order_id': 'archived-manual-order', 'quantity': 4, 'broker_as_of': past}),
    ):
        record = ledger.apply(record, order_event(manual.intent_id, kind, state, past, case.clock, **values), **changes)
    case.book.add('archived-manual-order', manual)
    case.book.context.clock[0] = past
    case.book.fill('archived-manual-order', 4, 'archived-manual-fill')
    case.book.context.clock[0] = case.clock
    read = case.book.read
    async def current_window():
        recovery = await read()
        # The current broker query does not return a terminal order from last
        # week; the account balance and actual historical ledger retain it.
        return replace(recovery,
            orders=tuple(row for row in recovery.orders if row.broker_order_id != 'archived-manual-order'),
            order_details=tuple(row for row in recovery.order_details if row.broker_order_id != 'archived-manual-order'))
    case.book.read = current_window
    await case.bundle.monitor.refresh_recovery()
    await case.runner.episode.advance_once()
    risk = case.repository.load_latest_mock_automation_risk(case.bundle.account_ref)
    case.assertTrue(risk.reconciliation_complete, risk.to_dict())
    case.assertEqual(0, risk.balance_mismatch_count)
    case.assertEqual({first.intent.symbol: 8}, dict(risk.owned_position_quantities))
    await case.deliver(1011)
    partial = case.execution.load(case.runner.episode.status['pending_intent_id'])
    case.assertEqual(('SELL', 2), (partial.intent.side.value, partial.intent.quantity), case.runner.episode.status)
    await fill(case, partial, 2, 'only-planned-two')
    await case.deliver(940)
    notice = case.runner.episode._protection_snapshot()
    case.assertIsNotNone(notice, (case.runner.episode.status, risk.to_dict()))
    case.assertEqual(6, notice.get('planned_leader_stop', {}).get('stop_loss', {}).get('quantity'),
        (case.runner.episode.status, risk.to_dict()))
    await case.deliver(960)
    await case.bundle.monitor.refresh_recovery()
    await case.runner.episode.advance_once()
    active = case.execution.active_intents('mock', case.bundle.account_ref, case.bundle.run_id)
    case.assertEqual(1, len(active), case.runner.episode.status)
    protective = active[0]
    case.assertEqual(('SELL', 6, first.intent.plan.plan_id),
        (protective.intent.side.value, protective.intent.quantity, protective.intent.plan.plan_id))
    await fill(case, protective, 6, 'only-planned-six')
    head = case.execution.load_plan_position(case.bundle.account_ref, case.bundle.run_id, first.intent.symbol, first.intent.plan)
    recovery = await case.bundle.monitor.refresh_recovery()
    case.assertEqual(0, head.quantity)
    case.assertEqual({first.intent.symbol: 4}, recovery.account.positions)
    case.assertEqual(4, ledger.load(manual.intent_id).detailed_filled_quantity)
    case.assertEqual(['BUY', 'BUY', 'SELL', 'SELL'], [row[1] for row in case.transport.calls])


class LeaderStagedNativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_add_reduction_and_full_stop_share_one_plan_and_actual_fills(self):
        await verify_staged_round_trip(self)

    async def test_current_entry_off_before_claim_rolls_back_add_without_consuming_its_step(self):
        await verify_current_entry_off(self)

    async def test_native_cumulative_entry_budget_counts_original_fills_and_rolls_back_new_action(self):
        await verify_cumulative_entry_budget(self)

    async def test_additional_remainder_amends_then_cancels_when_its_own_current_breakout_fails(self):
        await verify_additional_current_management(self)

    async def test_restart_with_old_checkpoint_recovers_native_action_without_a_second_buy(self):
        await verify_staged_restart(self)

    async def test_additional_queued_commit_ack_loss_restores_once_without_network_retry(self):
        await verify_staged_claim_ack_loss(self)

    async def test_additional_partial_cancel_unknown_late_fill_protects_actual_eight_under_first_stop_seven(self):
        await verify_staged_cancel_unknown_late_fill(self)

    async def test_partial_and_full_protection_leave_independent_manual_four_untouched(self):
        await verify_staged_manual_quantity_isolation(self)


if __name__ == '__main__':
    unittest.main()
