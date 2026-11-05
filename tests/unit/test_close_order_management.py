"""Current conditions -> existing account owner -> one native amendment/cancellation."""
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_monitor.application.close_mock_execution import dispatch_close_entry
from kiwoom_monitor.central_server.minute_plan_management import MinutePlanManagement
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.execution_runtime import ManualMockOrderGateway
from kiwoom_monitor.central_server.mock_account_monitor import MockAccountMonitor
from kiwoom_monitor.domain.execution_activation import CloseMockExecutionPolicy
from kiwoom_monitor.domain.order_contract import AccountSnapshot, BrokerAmendmentSubmission, BrokerFill, BrokerOrderSnapshot, BrokerSubmission, OrderSide, OrderState
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import BrokerOrderDetails, MockAccountRecovery
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown


def management_terms(stopped=True):
    return CloseMockExecutionPolicy(100, 500, 'acknowledged_0B_bounded_by_signal/v1', 'cancel_at_entry_deadline/v1',
        'acknowledged_0B_bounded_slippage/v1', version=f'close-mock-execution-policy/v{4 if stopped else 3}',
        stopped_management='continue_original_plan/v1' if stopped else None,
        accepted_order_management='reassess_then_amend_price/v1')


class BrokerBook:
    """Independent physical-order book; no execution-repository inference of broker fills."""
    def __init__(self, context, *, empty=False):
        self.context = context
        self.rows = {}
        self.mutations = []
        self.reads = 0
        self.lost_ack = False
        if not empty: self.add(context.buy.broker_order_id, context.buy.intent)

    def add(self, number, intent, *, parent=None, quantity=None, price=None):
        self.rows[number] = dict(number=number, side=intent.side, ordered=quantity or intent.quantity,
            remaining=quantity or intent.quantity, price=price or intent.limit_price, fills=[], parent=parent, state=OrderState.ACCEPTED)

    def fill(self, number, quantity, identity='shared'):
        row = self.rows[number]
        assert 0 < quantity <= row['remaining']
        row['remaining'] -= quantity
        row['fills'].append(BrokerFill(identity, quantity, row['price'], self.context.clock[0]))
        row['state'] = OrderState.FILLED if not row['remaining'] else OrderState.PARTIALLY_FILLED

    def amend(self, intent, number, quantity, price):
        stored = self.context.execution.load(intent.intent_id)
        assert stored.amendment.status == 'PENDING' and stored.amendment.assessment['action'] == 'AMEND'
        assert self.context.execution._automation_control is None
        assert self.context.execution.events(intent.intent_id)[-1]['event_type'] == 'AMENDMENT_STARTED'
        accepted = min(quantity, 3) if intent.side is OrderSide.BUY else quantity
        row = self.rows[number]
        assert accepted <= row['remaining']
        row['remaining'] -= accepted
        child = f'amended-{len(self.mutations) + 1}'
        self.add(child, intent, parent=number, quantity=accepted, price=price)
        self.mutations.append(('AMEND', number, child, accepted, price))
        if self.lost_ack:
            raise SubmissionUnknown('fake broker accepted amendment; response lost')
        return BrokerAmendmentSubmission(child, self.context.clock[0], number, accepted)

    def cancel(self, intent, number, quantity=0):
        event = self.context.execution.events(intent.intent_id)[-1]
        assert event['event_type'] == 'CANCEL_STARTED' and event['close_order_management_claim']['action'] == 'CANCEL'
        self.mutations.append(('CANCEL', number, quantity))
        self.rows[number]['remaining'] = 0
        self.rows[number]['state'] = OrderState.CANCELLED
        return BrokerSubmission('cancelled', self.context.clock[0])

    async def read(self):
        self.reads += 1
        ctx = self.context
        ctx.clock[0] += timedelta(milliseconds=1)
        positions, cash, reserved, orders, details = 0, 100_000, 0, [], []
        for row in self.rows.values():
            filled = sum(fill.quantity for fill in row['fills'])
            gross = sum(fill.quantity * fill.price for fill in row['fills'])
            buy = row['side'] is OrderSide.BUY
            positions += filled if buy else -filled
            cash += -gross if buy else gross
            reserved += row['remaining'] * row['price'] if buy else 0
            if row['remaining'] or filled or row['state'] is OrderState.CANCELLED:
                orders.append(BrokerOrderSnapshot(row['number'], ctx.runtime.account_ref, ctx.buy.intent.symbol,
                    row['state'], filled, row['remaining'], ctx.clock[0], tuple(row['fills'])))
                details.append(BrokerOrderDetails(row['number'], row['ordered'], row['side'], row['price'], 'KRX',
                    original_broker_order_id=row['parent']))
        return MockAccountRecovery(AccountSnapshot(ctx.runtime.account_ref, cash, reserved,
            {ctx.buy.intent.symbol: positions} if positions else {}, ctx.clock[0]), tuple(orders), tuple(details))

    async def read_trade_cost_pages(self, start, end):
        grouped = {}
        for row in self.rows.values():
            for fill in row['fills']:
                key = (fill.occurred_at.date(), row['side'])
                grouped[key] = grouped.get(key, 0) + fill.quantity * fill.price
        return ({'trst_ovrl_trde_prps_array': [dict(cntr_dt=day.strftime('%Y%m%d'), trde_dt=day.strftime('%Y%m%d'),
            stk_cd=self.context.buy.intent.symbol, io_tp_nm='매수' if side is OrderSide.BUY else '매도',
            trde_amt=str(gross), exct_amt=str(gross), cmsn='0', tax_sum_cmsn='0') for (day, side), gross in grouped.items()]},)


async def management_context(case, factory=None, *, stopped=True, family='close_betting/v1', phase='listing_day'):
    from tests.unit.test_mock_automation_specification import MockAutomationSpecificationTests
    if factory is None:
        directory = case.enterContext(tempfile.TemporaryDirectory())
        factory = lambda: SQLiteQueryStore(Path(directory) / 'management.sqlite3')
    store = factory(); store.initialize(); case.addCleanup(store.close)
    if family == 'close_betting/v1':
        ctx = MockAutomationSpecificationTests._verify_close_entry_claim(case, store, close_terms=management_terms(stopped))
    else:
        from tests.unit.test_ipo_mock_execution import prepare_ipo_execution
        ctx = prepare_ipo_execution(case, store, family, phase, close_terms=management_terms(stopped))
        gate, ctx.buy, _ = dispatch_close_entry(ctx.repository, ctx.runtime, ctx.spec, parameters=ctx.parameters,
            event=ctx.event, quote=ctx.quote, recovery=ctx.recovery, risk=ctx.risk)
        case.assertIsNotNone(ctx.buy, gate)
        ctx.entry_proof = ctx.repository.load_close_entry_evidence(ctx.runtime.account_ref, ctx.buy.intent.plan.plan_id)
    ctx.clock[0] = datetime.fromisoformat(ctx.entry_proof['decision']['decided_at']) + timedelta(seconds=10)
    ctx.base = datetime.fromisoformat(ctx.entry_proof['decision']['decided_at'])
    ctx.book = BrokerBook(ctx)
    case.enterContext(patch.object(ctx.transport, 'amend', ctx.book.amend, create=True))
    case.enterContext(patch.object(ctx.transport, 'cancel', ctx.book.cancel))
    submit = ctx.transport.submit
    def broker_submit(intent):
        response = submit(intent); ctx.book.add(response.broker_order_id, intent); return response
    case.enterContext(patch.object(ctx.transport, 'submit', broker_submit))
    monitor = MockAccountMonitor(ctx.book, ctx.runtime, ctx.execution, now_provider=lambda: ctx.clock[0],
        automation_risk_repository=ctx.repository, automation_binding_revision=ctx.spec.binding_revision,
        automation_account_scope=ctx.spec.account_scope, new_orders_enabled_on_start=False)
    await monitor.start()
    monitor.set_automation_execution_inputs_enabled(True)
    await monitor._queue.join()
    case.addAsyncCleanup(monitor.close)
    gateway = ManualMockOrderGateway(ctx.runtime, ctx.execution, monitor.refresh_account,
        now_provider=lambda: ctx.clock[0], automation_execution_cut=lambda: monitor.latest_automation_execution_cut)
    gateway.set_manual_submit_enabled(False); case.addAsyncCleanup(gateway.close)
    ctx.quotes = {ctx.buy.intent.symbol: {**ctx.quote, 'price_won': 105, 'available_at': ctx.clock[0],
        'input_ref': 'collector-trade:current-management:1'}}
    ctx.bundle = SimpleNamespace(account_ref=ctx.runtime.account_ref, run_id=ctx.runtime.run_id,
        runtime=ctx.runtime, repository=ctx.execution, monitor=monitor, gateway=gateway)
    ctx.manager = MinutePlanManagement(store, ctx.bundle, ctx.spec, ctx.repository, parameters=ctx.parameters,
        price_source=lambda symbol: ctx.quotes.get(symbol), now_provider=lambda: ctx.clock[0], family=family)
    ctx.manager.prepare(None)
    ctx.control = ctx.repository.load_mock_automation_control(ctx.runtime.account_ref)
    return ctx


async def advance(ctx):
    return await ctx.manager.advance(ctx.control, limit=10, allow_entry=lambda: False)


async def verify_current_buy(case, factory=None, *, lost_ack=False, cancel=False, family='close_betting/v1', phase='listing_day'):
    ctx = await management_context(case, factory, family=family, phase=phase)
    case.assertGreater(ctx.clock[0], ctx.buy.intent.expires_at)
    case.assertTrue(ctx.bundle.monitor.latest_automation_execution_cut['risk'].reconciliation_complete)
    ctx.book.lost_ack = lost_ack
    await advance(ctx)
    case.assertEqual([('AMEND', ctx.buy.broker_order_id, 'amended-1', 3, 105)], ctx.book.mutations)
    current = ctx.execution.load(ctx.buy.intent.intent_id)
    case.assertEqual(ctx.buy.intent, current.intent)
    case.assertEqual(104, current.intent.limit_price)
    if lost_ack:
        case.assertEqual(OrderState.SUBMISSION_UNKNOWN, current.state)
        await advance(ctx); await ctx.bundle.monitor._queue.join()
        case.assertEqual(1, len(ctx.book.mutations))
        case.assertEqual('ACCEPTED', ctx.execution.load(current.intent.intent_id).amendment.status)
    ctx.clock[0] += timedelta(seconds=1)
    ctx.book.fill(ctx.buy.broker_order_id, 1)
    await ctx.bundle.monitor.refresh_recovery()
    await advance(ctx)
    reads = ctx.book.reads
    await advance(ctx); await advance(ctx)
    case.assertEqual(reads, ctx.book.reads)
    case.assertEqual(1, len(ctx.book.mutations))
    if cancel:
        ctx.store.upsert_documents('server_operational_settings', [{'owner': 'global', 'key': 'current',
            'document': {**ctx.settings, 'shadow_candidate_enabled': False}}])
        await advance(ctx)
        case.assertEqual(('CANCEL', 'amended-1', 0), ctx.book.mutations[-1])
        await ctx.bundle.monitor.refresh_recovery()
        final = ctx.execution.load(ctx.buy.intent.intent_id)
        case.assertEqual((OrderState.CANCELLED, 1), (final.state, final.detailed_filled_quantity))
        case.assertEqual({ctx.buy.intent.symbol: 1}, ctx.bundle.monitor.latest_automation_execution_cut['recovery'].account.positions)
    return ctx


async def verify_ipo_current(case, factory=None, *, family, phase):
    ctx = await verify_current_buy(case, factory, family=family, phase=phase, cancel=family == 'ipo_close/v1')
    if family == 'ipo_breakout/v1':
        ctx.clock[0] += timedelta(seconds=1)
        setup = ctx.event['close_ipo_entry_setup']
        ctx.quotes[ctx.buy.intent.symbol] = {**ctx.quote, 'price_won': setup['breakout_level'],
            'available_at': ctx.clock[0], 'input_ref': 'collector-trade:ipo-breakout-lost'}
        await advance(ctx)
        case.assertEqual(('CANCEL', 'amended-1', 0), ctx.book.mutations[-1])
        claim = next(row['close_order_management_claim'] for row in ctx.execution.events(ctx.buy.intent.intent_id)
                     if row['event_type'] == 'CANCEL_STARTED')
        case.assertEqual('current_ipo_breakout_not_confirmed', claim['reason'])
        await ctx.bundle.monitor.refresh_recovery()
        final = ctx.execution.load(ctx.buy.intent.intent_id)
        case.assertEqual((OrderState.CANCELLED, 1), (final.state, final.detailed_filled_quantity))


async def verify_current_sell(case, factory=None):
    from kiwoom_monitor.application.mock_automation_admission import MockAutomationDesiredState
    ctx = await verify_current_buy(case, factory)
    ctx.book.fill('amended-1', 3)
    await ctx.bundle.monitor.refresh_recovery()
    risk = ctx.bundle.monitor.latest_automation_execution_cut['risk']
    case.assertEqual(((ctx.buy.intent.symbol, 4),), risk.owned_position_quantities)
    basis = dict(risk.owned_entry_prices)[ctx.buy.intent.symbol]
    trigger = basis * (10_000 - ctx.parameters['policy']['protection_loss_bps']) // 10_000
    ctx.clock[0] += timedelta(seconds=1)
    ctx.quotes[ctx.buy.intent.symbol] = {**ctx.quote, 'price_won': trigger, 'available_at': ctx.clock[0],
        'input_ref': 'collector-trade:current-management:stop'}
    await advance(ctx)
    sells = [row for row in ctx.execution.active_intents('mock', ctx.runtime.account_ref, ctx.runtime.run_id)
             if row.intent.side is OrderSide.SELL]
    case.assertEqual(1, len(sells)); sell = sells[0]
    ctx.clock[0] += timedelta(seconds=6)
    ctx.quotes[ctx.buy.intent.symbol] = {**ctx.quote, 'price_won': trigger - 1, 'available_at': ctx.clock[0],
        'input_ref': 'collector-trade:current-management:sell'}
    ctx.store.upsert_documents('server_operational_settings', [{'owner': 'global', 'key': 'current',
        'document': {**ctx.settings, 'shadow_candidate_enabled': False}}])
    current = ctx.repository.load_mock_automation_control(ctx.runtime.account_ref)
    ctx.control = replace(current, desired_state=MockAutomationDesiredState.STOPPED,
                          control_revision=current.control_revision + 1, changed_at=ctx.clock[0])
    case.assertTrue(ctx.repository.save_mock_automation_control(ctx.control, expected_revision=current.control_revision))
    await advance(ctx)
    case.assertGreater(ctx.clock[0], sell.intent.expires_at)
    case.assertEqual(('AMEND', sell.broker_order_id, 'amended-2', 4, trigger - 1), ctx.book.mutations[-1])
    case.assertEqual(['BUY', 'SELL'], [row[1] for row in ctx.transport.calls])
    ctx.book.fill('amended-2', 4)
    await ctx.bundle.monitor.refresh_recovery()
    final = ctx.execution.load(sell.intent.intent_id)
    case.assertEqual((OrderState.FILLED, 4), (final.state, final.detailed_filled_quantity))
    head = ctx.execution.load_plan_position(ctx.runtime.account_ref, ctx.runtime.run_id, sell.intent.symbol, sell.intent.plan)
    case.assertEqual(0, head.quantity)
    case.assertEqual({}, ctx.bundle.monitor.latest_automation_execution_cut['recovery'].account.positions)
    from tests.unit.test_mock_automation_specification import _ExecutionPageSource
    from kiwoom_monitor.application.journal_enrichment import sync_journal_execution_projection
    from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
    journal_path = Path(case.enterContext(tempfile.TemporaryDirectory())) / 'amendment-journal.sqlite3'
    scope = ctx.spec.account_scope
    source = _ExecutionPageSource(ctx.execution, ctx.runtime.account_ref)
    projection = sync_journal_execution_projection(source, JournalRepository(journal_path), scope,
        page_limit=2, max_pages=100, now=ctx.clock[0])
    case.assertEqual(3, projection.detailed_fill_count)
    rows = JournalRepository(journal_path).load_execution_fill_projections(scope)
    case.assertEqual([(ctx.buy.broker_order_id, 'shared', 1), ('amended-1', 'shared', 3), ('amended-2', 'shared', 4)],
        [(row['broker_order_id'], row['broker_execution_id'], row['quantity']) for row in rows])
    case.assertEqual({ctx.buy.intent.plan.plan_id}, {row['plan']['plan_id'] for row in rows})
    repeated = sync_journal_execution_projection(source, JournalRepository(journal_path), scope, now=ctx.clock[0])
    case.assertEqual(0, repeated.inserted_event_count)


async def verify_native_current_recheck(case, factory=None):
    ctx = await management_context(case, factory)
    original = ctx.runtime._lifecycle.amend
    before = []
    def changed_before_claim(*args, **kwargs):
        ctx.store.upsert_documents('server_operational_settings', [{'owner': 'global', 'key': 'current',
            'document': {**ctx.settings, 'shadow_candidate_enabled': False}}])
        before.append(ctx.execution.events(ctx.buy.intent.intent_id))
        return original(*args, **kwargs)
    with patch.object(ctx.runtime._lifecycle, 'amend', side_effect=changed_before_claim):
        with case.assertRaisesRegex(RuntimeError, 'CLOSE_CURRENT_ORDER_ASSESSMENT_CHANGED'):
            await advance(ctx)
    case.assertEqual(before[0], ctx.execution.events(ctx.buy.intent.intent_id))
    case.assertEqual([], ctx.book.mutations)
    await advance(ctx)
    case.assertEqual([('CANCEL', ctx.buy.broker_order_id, 0)], ctx.book.mutations)


class CloseCurrentOrderManagementTests(unittest.IsolatedAsyncioTestCase):
    async def test_ipo_breakout_rechecks_its_original_breakout_condition_in_both_listing_phases(self):
        for phase in ('listing_day', 'after_listing_day'):
            with self.subTest(phase=phase):
                await verify_ipo_current(self, family='ipo_breakout/v1', phase=phase)

    async def test_ipo_close_uses_current_amendment_and_context_cancellation_in_both_listing_phases(self):
        for phase in ('listing_day', 'after_listing_day'):
            with self.subTest(phase=phase):
                await verify_ipo_current(self, family='ipo_close/v1', phase=phase)

    async def test_current_buy_reassesses_after_original_intent_expiry_and_cancels_on_context_change(self):
        await verify_current_buy(self, cancel=True)

    async def test_unknown_amendment_is_recovered_without_resending(self):
        await verify_current_buy(self, lost_ack=True)

    async def test_current_sell_amends_owned_remainder_after_stop_without_cancel_and_reentry(self):
        await verify_current_sell(self)

    async def test_native_transaction_rechecks_current_context_before_any_mutation(self):
        await verify_native_current_recheck(self)
