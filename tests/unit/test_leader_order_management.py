"""Actual leader 0B -> current claim -> native physical-order management."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


async def current_leader(case, factory=None):
    from tests.unit import test_mock_automation_specification as support
    from tests.unit.test_close_order_management import BrokerBook
    from kiwoom_monitor.central_server.database import SQLiteQueryStore
    from kiwoom_monitor.central_server.execution_runtime import ManualMockOrderGateway
    from kiwoom_monitor.central_server.mock_account_monitor import MockAccountMonitor
    from kiwoom_monitor.domain.execution_activation import LEADER_NATIVE_PLAN_MODE

    if factory is None:
        root = Path(case.enterContext(tempfile.TemporaryDirectory()))
        factory = lambda: SQLiteQueryStore(root / 'leader-current.sqlite3')
    case._native_fixture_store = factory()
    case.unfilled_execution_policy = support.MockUnfilledReplacementDispatchTests.unfilled_execution_policy
    build = support.mock_specification_inputs
    def frozen(*args, **kwargs):
        config = kwargs['leader_config']
        kwargs['leader_config'] = replace(config, execution_contract=replace(config.execution_contract, quantity=5))
        inputs = build(*args, **kwargs)
        inputs.spec = replace(inputs.spec, execution_plan_mode=LEADER_NATIVE_PLAN_MODE,
            unfilled_execution_policy=replace(case.unfilled_execution_policy, version='mock-unfilled-execution-policy/v2'))
        return inputs
    with patch.object(support, 'mock_specification_inputs', side_effect=frozen):
        support.PlannedLeaderNativeTests.setUp(case)
    case.bundle.gateway = None
    case.settings = {'revision': 1, 'shadow_candidate_enabled': True, 'shadow_candidate_config': case.live['policy']}
    case.store.upsert_documents('server_operational_settings', [dict(owner='global', key='current', document=case.settings)])
    await case.deliver(990)
    await case.deliver(1001)
    buy = case.execution.load(case.runner.episode.status['pending_intent_id'])
    case.assertEqual((5, 'BUY'), (buy.intent.quantity, buy.intent.side.value))
    context = SimpleNamespace(buy=buy, clock=[case.clock], runtime=case.runtime, execution=case.execution,
        management_field='leader_order_management_claim')
    case.book = BrokerBook(context)
    read = case.book.read
    async def read_current():
        context.clock[0] = case.clock
        value = await read()
        case.clock = context.clock[0]
        return value
    case.book.read = read_current
    amend_book = case.book.amend
    def amend_current(intent, number, quantity, price):
        from kiwoom_monitor.domain.order_contract import OrderState
        try:
            return amend_book(intent, number, quantity, price)
        finally:
            if case.book.rows[number]['remaining'] == 0:
                case.book.rows[number]['state'] = OrderState.CANCELLED
    case.enterContext(patch.object(case.transport, 'cancel', side_effect=case.book.cancel))
    case.enterContext(patch.object(case.transport, 'amend', side_effect=amend_current, create=True))
    submit = case.transport.submit
    def submit_current(intent):
        result = submit(intent)
        case.book.add(result.broker_order_id, intent)
        return result
    case.enterContext(patch.object(case.transport, 'submit', side_effect=submit_current))
    monitor = MockAccountMonitor(case.book, case.runtime, case.execution, now_provider=lambda: case.clock,
        automation_risk_repository=case.repository, automation_binding_revision=case.spec.binding_revision,
        automation_account_scope=case.spec.account_scope, new_orders_enabled_on_start=False)
    case.addAsyncCleanup(monitor.close)
    await monitor.start()
    monitor.set_automation_execution_inputs_enabled(True)
    await monitor._queue.join()
    gateway = ManualMockOrderGateway(case.runtime, case.execution, monitor.refresh_account,
        now_provider=lambda: case.clock, automation_execution_cut=lambda: monitor.latest_automation_execution_cut)
    gateway.set_manual_submit_enabled(False)
    case.addAsyncCleanup(gateway.close)
    case.bundle.monitor, case.bundle.gateway = monitor, gateway
    # Restore through the real Runner seam so the real monitor owns native
    # risk delivery as well as account refresh and the command cut.
    from tests.unit.test_mock_automation_runner import _prepared_runner
    await case.runner.close()
    case.runner = _prepared_runner(case.store, case.bundle, case.spec, now_provider=lambda: case.clock,
        leader_context_source=lambda: case.live)
    case.addAsyncCleanup(case.runner.close)
    await monitor.refresh_recovery()
    return buy


async def verify_current_buy(case, factory=None, *, lost_ack=False):
    from kiwoom_monitor.domain.order_contract import OrderState
    buy = await current_leader(case, factory)
    case.clock = buy.intent.expires_at + timedelta(milliseconds=100)
    case.book.lost_ack = lost_ack
    await case.deliver(1002)
    case.assertEqual([('AMEND', buy.broker_order_id, 'amended-1', 3, 1002)], case.book.mutations,
                     case.runner.episode.status)
    current = case.execution.load(buy.intent.intent_id)
    case.assertEqual(buy.intent, current.intent)
    if lost_ack:
        case.assertEqual(OrderState.SUBMISSION_UNKNOWN, current.state)
        await case.runner.episode.advance_once()
        await case.bundle.monitor._queue.join()
        case.assertEqual(1, len(case.book.mutations))
        case.assertEqual('ACCEPTED', case.execution.load(buy.intent.intent_id).amendment.status)
    case.book.lost_ack = False
    case.book.fill(buy.broker_order_id, 1, 'same-raw-id')
    case.book.fill('amended-1', 2, 'same-raw-id')
    await case.bundle.monitor.refresh_recovery()
    before = len(case.book.mutations)
    await case.runner.episode.advance_once()
    case.assertEqual(before + 1, len(case.book.mutations))
    case.assertEqual(('AMEND', buy.broker_order_id, 'amended-2', 1, 1002), case.book.mutations[-1])
    root = case.execution.load(buy.intent.intent_id)
    case.assertEqual(3, root.detailed_filled_quantity)
    head = case.execution.load_plan_position(case.runtime.account_ref, case.run_id, buy.intent.symbol, buy.intent.plan)
    case.assertEqual(3, head.quantity)
    case.assertEqual(2, sum(leg.remaining_quantity for leg in root.broker_legs))
    await case.bundle.monitor._queue.join()
    await case.runner.episode.advance_once()
    reads = case.book.reads
    await case.runner.episode.advance_once()
    await case.runner.episode.advance_once()
    case.assertEqual(reads, case.book.reads, 'unchanged current inputs must reuse the acknowledged account/command cut')
    case.store.upsert_documents('server_operational_settings', [dict(owner='global', key='current',
        document={**case.settings, 'revision': 2, 'shadow_candidate_enabled': False})])
    case.live = {**case.live, 'revision': 2}
    await case.runner.episode.advance_once()
    case.assertEqual('CANCEL', case.book.mutations[-1][0], case.runner.episode.status)
    case.assertEqual(['BUY'], [row[1] for row in case.transport.calls])


async def verify_native_current_recheck(case, factory=None):
    buy = await current_leader(case, factory)
    before = case.execution.events(buy.intent.intent_id)
    claim_cut = []
    amend = case.runtime._lifecycle.amend
    def changed(*args, **kwargs):
        claim_cut.extend(case.execution.events(buy.intent.intent_id))
        case.store.upsert_documents('server_operational_settings', [dict(owner='global', key='current',
            document={**case.settings, 'shadow_candidate_enabled': False})])
        return amend(*args, **kwargs)
    with patch.object(case.runtime._lifecycle, 'amend', side_effect=changed):
        await case.deliver(1002)
    case.assertIn('LEADER_CURRENT_ORDER_ASSESSMENT_CHANGED', case.runner.episode.status['reason'])
    case.assertEqual(before, case.execution.events(buy.intent.intent_id)[:len(before)])
    case.assertEqual(tuple(claim_cut), case.execution.events(buy.intent.intent_id))
    case.assertEqual([], case.book.mutations)
    await case.runner.episode.advance_once()
    case.assertEqual([('CANCEL', buy.broker_order_id, 0)], case.book.mutations, case.runner.episode.status)


async def verify_current_sell(case, factory=None, *, protective=False, amended_buy=False):
    from kiwoom_monitor.domain.order_contract import OrderSide
    buy = await current_leader(case, factory)
    if amended_buy:
        case.clock = buy.intent.expires_at + timedelta(milliseconds=100)
        await case.deliver(1002)
        case.assertEqual([('AMEND', buy.broker_order_id, 'amended-1', 3, 1002)], case.book.mutations)
        case.book.fill(buy.broker_order_id, 2)
        case.book.fill('amended-1', 3)
    else:
        case.book.fill(buy.broker_order_id, 5)
    await case.bundle.monitor.refresh_recovery()
    await case.runner.episode.advance_once()
    if protective:
        await case.deliver(940)
        await case.deliver(945)
        await case.bundle.monitor.refresh_recovery()
        await case.runner.episode.advance_once()
    else:
        await case.deliver(970)
    active = case.execution.active_intents('mock', case.runtime.account_ref, case.run_id)
    case.assertEqual(1, len(active), case.runner.episode.status)
    sell = active[0]
    case.assertIs(OrderSide.SELL, sell.intent.side)
    case.assertEqual('mock-protection-exit/v1' if protective else 'leader_execution_adapter/v1', sell.intent.policy_version)
    case.assertEqual((5, buy.intent.plan.plan_id), (sell.intent.quantity, sell.intent.plan.plan_id))
    entry_mutations = list(case.book.mutations)
    first_child = f'amended-{len(entry_mutations) + 1}'
    second_child = f'amended-{len(entry_mutations) + 2}'
    case.clock = sell.intent.expires_at + timedelta(milliseconds=100)
    case.store.upsert_documents('server_operational_settings', [dict(owner='global', key='current',
        document={**case.settings, 'revision': 2, 'shadow_candidate_enabled': False})])
    case.live = {**case.live, 'revision': 2}
    await case.deliver(1002 if protective else 960)
    price = 1002 if protective else 960
    case.assertEqual(entry_mutations + [('AMEND', sell.broker_order_id, first_child, 5, price)], case.book.mutations,
                     case.runner.episode.status)
    case.assertEqual(sell.intent, case.execution.load(sell.intent.intent_id).intent)
    case.book.fill(first_child, 2)
    await case.bundle.monitor.refresh_recovery()
    await case.deliver(price + 1)
    case.assertEqual(('AMEND', first_child, second_child, 3, price + 1), case.book.mutations[-1],
                     case.runner.episode.status)
    case.book.fill(second_child, 3)
    await case.bundle.monitor.refresh_recovery()
    await case.runner.episode.advance_once()
    head = case.execution.load_plan_position(case.runtime.account_ref, case.run_id, buy.intent.symbol, buy.intent.plan)
    case.assertEqual(0, head.quantity)
    case.assertEqual(['BUY', 'SELL'], [row[1] for row in case.transport.calls])
    case.assertEqual(['AMEND'] * (len(entry_mutations) + 2), [row[0] for row in case.book.mutations])


async def verify_cancel_unknown_late_fill(case, factory=None):
    from kiwoom_monitor.domain.order_contract import OrderState
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
    buy = await current_leader(case, factory)
    case.book.fill(buy.broker_order_id, 2, 'first')
    await case.bundle.monitor.refresh_recovery()
    def unknown_cancel(intent, number, quantity=0):
        case.assertEqual('CANCEL_STARTED', case.execution.events(intent.intent_id)[-1]['event_type'])
        case.book.mutations.append(('CANCEL', number, quantity))
        raise SubmissionUnknown('fake broker cancellation response lost')
    with patch.object(case.transport, 'cancel', side_effect=unknown_cancel):
        await case.deliver(1000)
        case.assertIs(OrderState.CANCEL_PENDING, case.execution.load(buy.intent.intent_id).state)
        await case.runner.episode.advance_once()
        await case.bundle.monitor._queue.join()
        case.assertEqual([('CANCEL', buy.broker_order_id, 0)], case.book.mutations)
    case.clock += timedelta(milliseconds=100)
    case.book.context.clock[0] = case.clock
    case.book.fill(buy.broker_order_id, 1, 'late')
    case.book.rows[buy.broker_order_id].update(remaining=0, state=OrderState.CANCELLED)
    await case.bundle.monitor.refresh_recovery()
    await case.runner.episode.advance_once()
    root = case.execution.load(buy.intent.intent_id)
    case.assertEqual((OrderState.CANCELLED, 3), (root.state, root.detailed_filled_quantity))
    await case.deliver(940)
    await case.deliver(945)
    await case.bundle.monitor.refresh_recovery()
    await case.runner.episode.advance_once()
    active = case.execution.active_intents('mock', case.runtime.account_ref, case.run_id)
    case.assertEqual(1, len(active), case.runner.episode.status)
    case.assertEqual(('SELL', 3, buy.intent.plan.plan_id),
        (active[0].intent.side.value, active[0].intent.quantity, active[0].intent.plan.plan_id))
    case.assertEqual(['BUY', 'SELL'], [row[1] for row in case.transport.calls])


async def verify_current_entry_predicates(case, factory=None):
    buy = await current_leader(case, factory)
    await case.deliver(1000)  # At the reference is no longer a confirmed breakout.
    case.assertEqual([('CANCEL', buy.broker_order_id, 0)], case.book.mutations, case.runner.episode.status)
    case.assertEqual(0, case.execution.load_plan_position(case.runtime.account_ref, case.run_id, buy.intent.symbol, buy.intent.plan).quantity)
    case.assertEqual(['BUY'], [row[1] for row in case.transport.calls])


async def verify_amendment_limit(case, factory=None):
    buy = await current_leader(case, factory)
    await case.deliver(1002)
    await case.deliver(1003)
    await case.runner.episode.advance_once()
    case.assertEqual(3, len(case.book.mutations), case.runner.episode.status)
    await case.deliver(1004)
    case.assertEqual('original_amendment_limit_reached', case.runner.episode.status['reason'])
    case.assertEqual(3, len(case.book.mutations))
    await case.deliver(1000)
    case.assertEqual('CANCEL', case.book.mutations[-1][0])
    case.assertEqual(['BUY'], [row[1] for row in case.transport.calls])


class LeaderCurrentOrderManagementTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_buy_amends_after_original_ttl_and_keeps_physical_fill_scope(self):
        await verify_current_buy(self)

    async def test_unknown_amendment_reconciles_without_resending(self):
        await verify_current_buy(self, lost_ack=True)

    async def test_native_current_condition_change_rejects_claim_before_transport(self):
        await verify_native_current_recheck(self)

    async def test_current_breakout_failure_cancels_instead_of_amending_or_reentering(self):
        await verify_current_entry_predicates(self)

    async def test_ordinary_exit_amends_only_actual_plan_remainder_with_shadow_off(self):
        await verify_current_sell(self)

    async def test_confirmed_protection_exit_keeps_first_stop_and_amends_actual_remainder(self):
        await verify_current_sell(self, protective=True)

    async def test_protection_after_amended_buy_reconciles_all_physical_orders(self):
        await verify_current_sell(self, protective=True, amended_buy=True)

    async def test_cancel_unknown_and_late_fill_keep_original_plan_and_protect_only_actual_three(self):
        await verify_cancel_unknown_late_fill(self)

    async def test_explicit_amendment_limit_does_not_disable_current_failure_cancellation(self):
        await verify_amendment_limit(self)


class LeaderCurrentPolicyTests(unittest.TestCase):
    def test_v1_serialization_is_unchanged_and_v2_requires_the_explicit_native_plan(self):
        from tests.unit.test_mock_automation_specification import MockUnfilledReplacementDispatchTests
        from tests.unit.test_execution_activation import _automation_spec, _spec
        from kiwoom_monitor.domain.execution_activation import LEADER_NATIVE_PLAN_MODE, MockUnfilledExecutionPolicy
        legacy = MockUnfilledReplacementDispatchTests.unfilled_execution_policy
        current = replace(legacy, version='mock-unfilled-execution-policy/v2')
        self.assertIsNone(legacy.accepted_order_management)
        self.assertEqual('reassess_then_amend_price/v1', current.accepted_order_management)
        self.assertNotIn('accepted_order_management', legacy.to_dict())
        self.assertEqual(set(legacy.to_dict()), set(current.to_dict()))
        self.assertNotEqual(legacy.policy_id, current.policy_id)
        self.assertEqual(current, MockUnfilledExecutionPolicy(**current.to_dict()))
        base = _automation_spec(_spec(account_ref='af64a3fa-197f-49df-8e8b-65b71bee02d9', session_profile='krx-regular/v1'))
        with self.assertRaisesRegex(ValueError, 'requires native plan mode'):
            replace(base, unfilled_execution_policy=current)
        self.assertEqual(current, replace(base, unfilled_execution_policy=current,
            execution_plan_mode=LEADER_NATIVE_PLAN_MODE).unfilled_execution_policy)
