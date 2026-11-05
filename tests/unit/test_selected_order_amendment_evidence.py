"""Stopped-source amendment facts survive offline preservation without granting authority."""
from contextlib import closing, redirect_stdout
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import io
import json

from scripts.preserve_selected_order_evidence import (
    accept_selected_order_evidence, capture_selected_order_evidence, inspect_selected_order_evidence,
    main,
)
from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.order_contract import BrokerOrderSnapshot, OrderSide, OrderState
from kiwoom_monitor.domain.selected_order_evidence import ORDER_FIELDS, RECOVERY_REQUIRED, encode_selected_order_evidence
from tests.unit.test_close_order_management import advance, management_context, verify_current_buy


async def stopped_source(case, mode):
    root = Path(case.enterContext(tempfile.TemporaryDirectory()))
    source = root / 'source.sqlite3'
    factory = lambda: SQLiteQueryStore(source)
    if mode == 'detailed':
        ctx = await verify_current_buy(case, factory)
        ctx.book.fill('amended-1', 1)
        await ctx.bundle.monitor.refresh_recovery()
    else:
        ctx = await management_context(case, factory)
        ctx.book.lost_ack = mode in {'unknown', 'recovered'}
        await advance(ctx)
        if mode in {'unknown', 'recovered'}:
            ctx.book.fill('amended-1', 1)
            actual = ctx.book.rows['amended-1']
            snapshot = BrokerOrderSnapshot('amended-1', ctx.runtime.account_ref, ctx.buy.intent.symbol,
                OrderState.PARTIALLY_FILLED, 1, 2, ctx.clock[0], tuple(actual['fills']))
            ctx.runtime.observe_amendment_fill(snapshot, original_broker_order_id=ctx.buy.broker_order_id, side=OrderSide.BUY)
            if mode == 'recovered':
                await ctx.bundle.monitor.refresh_recovery()
    emergency_stop_mock_automation(ctx.repository, ctx.runtime, account_ref=ctx.runtime.account_ref,
        spec_id=ctx.spec.spec_id, stopped_at=ctx.clock[0], reason='test-owned source drain')
    await ctx.bundle.gateway.close()
    await ctx.bundle.monitor.close()
    ctx.runtime.stop()
    selection = dict(source=source, source_instance='test-owned-source', operation_id='selected-amendment',
        account_ref=ctx.runtime.account_ref, run_id=ctx.runtime.run_id, intent_id=ctx.buy.intent.intent_id)
    return root, ctx, selection


class SelectedOrderAmendmentEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovered_observation_keeps_its_raw_fact_and_single_attributed_fill(self):
        _, _, selection = await stopped_source(self, 'recovered')
        packet, _ = capture_selected_order_evidence(**selection)
        self.assertEqual(('ACCEPTED', []),
            (packet['order']['amendment']['status'], packet['order']['amendment']['observed_fills']))
        observations = [event for event in packet['events'] if event['event_type'] == 'AMENDMENT_FILL_OBSERVED']
        fills = [event for event in packet['events'] if event['event_type'] == 'FILL']
        self.assertEqual((1, 1, 1), (len(observations), len(fills), packet['plan_position']['quantity']))
        self.assertEqual([(event['broker_order_id'], event['broker_execution_id'], event['quantity']) for event in observations],
            [(event['broker_order_id'], event['broker_execution_id'], event['quantity']) for event in fills])

    async def test_unfilled_amendment_preserves_both_physical_orders_and_offline_first_receipt(self):
        root, ctx, selection = await stopped_source(self, 'accepted')
        before = selection['source'].read_bytes()
        packet, digest = capture_selected_order_evidence(**selection)
        self.assertEqual('selected-order-evidence/v2', packet['version'])
        self.assertEqual([(ctx.buy.broker_order_id, 4, 3), ('amended-1', 3, 0)],
            [(leg['broker_order_id'], leg['ordered_quantity'], leg['transferred_quantity']) for leg in packet['order']['broker_legs']])
        self.assertEqual('reassess_then_amend_price/v1', ctx.spec.close_execution_policy.accepted_order_management)
        self.assertEqual('AMEND', packet['order']['amendment']['assessment']['action'])
        self.assertEqual((packet, digest), capture_selected_order_evidence(**selection))
        downgraded = {**packet, 'version': 'selected-order-evidence/v1',
                      'order': {key: packet['order'][key] for key in ORDER_FIELDS}}
        with self.assertRaisesRegex(ValueError, 'require v2'):
            encode_selected_order_evidence(downgraded)
        packet_path = root / 'selected-order.json'
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, main(['capture', '--source', str(selection['source']),
                '--source-instance', selection['source_instance'], '--operation-id', selection['operation_id'],
                '--intent-id', ctx.buy.intent.intent_id, '--account-ref', ctx.runtime.account_ref,
                '--run-id', ctx.runtime.run_id, '--packet', str(packet_path)]))
        self.assertEqual({'operation_id': packet['operation_id'], 'sha256': digest}, json.loads(output.getvalue()))
        self.assertEqual(packet, json.loads(packet_path.read_bytes()))
        target = root / 'offline.sqlite3'
        with closing(SQLiteQueryStore(target)) as store:
            store.initialize()
        request = dict(target=target, packet=packet, expected_sha256=digest,
                       account_ref=ctx.runtime.account_ref, run_id=ctx.runtime.run_id)
        first = accept_selected_order_evidence(**request, now_provider=lambda: ctx.clock[0])
        repeated = accept_selected_order_evidence(**request, now_provider=lambda: self.fail('receipt time renewed'))
        self.assertEqual(first['receipt'], repeated['receipt'])
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, main(['accept', '--target', str(target), '--sha256', digest,
                '--account-ref', ctx.runtime.account_ref, '--run-id', ctx.runtime.run_id, '--packet', str(packet_path)]))
        self.assertEqual(repeated, json.loads(output.getvalue()))
        self.assertEqual((False, True, RECOVERY_REQUIRED, False),
            (first['replayed'], repeated['replayed'], first['receipt']['status'], first['receipt']['order_authority']))
        self.assertEqual(packet, inspect_selected_order_evidence(target=target, operation_id=packet['operation_id'])['packet'])
        with closing(SQLiteQueryStore(target)) as store:
            self.assertIsNone(store.load_execution_intent(ctx.buy.intent.intent_id))
            self.assertEqual([], store.load_execution_events(ctx.buy.intent.intent_id))
            self.assertIsNone(store.load_mock_automation_control(ctx.runtime.account_ref))
        self.assertEqual(before, selection['source'].read_bytes())

    async def test_same_raw_fill_id_on_distinct_orders_is_retained_and_inconsistent_legs_are_rejected(self):
        _, ctx, selection = await stopped_source(self, 'detailed')
        packet, _ = capture_selected_order_evidence(**selection)
        fills = [event for event in packet['events'] if event['event_type'] == 'FILL']
        self.assertEqual([(ctx.buy.broker_order_id, 'shared', 1), ('amended-1', 'shared', 1)],
            [(event['broker_order_id'], event['broker_execution_id'], event['quantity']) for event in fills])
        self.assertEqual((2, ['shared', 'shared']), (packet['plan_position']['quantity'], packet['order']['fill_ids']))
        for change in ('missing_child', 'wrong_transfer', 'wrong_leg_detail', 'missing_fill_identity', 'wrong_total'):
            with self.subTest(change=change):
                changed = deepcopy(packet)
                order = changed['order']
                if change == 'missing_child': order['broker_legs'].pop()
                elif change == 'wrong_transfer': order['broker_legs'][0]['transferred_quantity'] -= 1
                elif change == 'wrong_leg_detail': order['broker_legs'][1]['detailed_filled_quantity'] += 1
                elif change == 'missing_fill_identity': order['broker_legs'][1]['fill_ids'] = []
                else: order['filled_quantity'] += 1
                with self.assertRaises(ValueError): encode_selected_order_evidence(changed)

    async def test_unbound_observation_and_pending_marker_remain_separate_from_position_fills(self):
        _, ctx, selection = await stopped_source(self, 'unknown')
        packet, _ = capture_selected_order_evidence(**selection)
        command = packet['order']['amendment']
        self.assertEqual(('UNKNOWN', ['["amended-1","shared"]']), (command['status'], command['observed_fills']))
        observed, = [row for row in packet['events'] if row['event_type'] == 'AMENDMENT_FILL_OBSERVED']
        self.assertEqual(('amended-1', 'shared', 1),
            (observed['broker_order_id'], observed['broker_execution_id'], observed['quantity']))
        self.assertTrue(observed['reason'].startswith('broker_revision='))
        self.assertTrue(observed['reason'].endswith(';amendment_parent=' + ctx.buy.broker_order_id))
        self.assertEqual((0, []), (packet['plan_position']['quantity'], packet['order']['fill_ids']))
        self.assertFalse(any(row['event_type'] == 'FILL' for row in packet['events']))
        changed = deepcopy(packet); changed['order']['amendment']['observed_fills'] = []
        with self.assertRaises(ValueError): encode_selected_order_evidence(changed)
        changed = deepcopy(packet); changed['events'][-1]['reason'] = 'amendment_parent=other'
        with self.assertRaises(ValueError): encode_selected_order_evidence(changed)
