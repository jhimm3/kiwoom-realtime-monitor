"""A real admitted plan's terminal ENTER/ADD/REDUCE ledger stays one plan."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.preserve_selected_order_evidence import capture_selected_plan_management_bundle
from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation
from kiwoom_monitor.application.selected_order_recovery import DatedBrokerEvidence, DatedBrokerOrderEvidence
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.order_contract import AccountEnvironment, OrderState
from kiwoom_monitor.domain.selected_order_evidence import PLAN_MANAGEMENT_VERSION, encode_selected_order_evidence, selected_plan_orders
from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import KiwoomAccountIdentityReader
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
from tests.unit import test_selected_order_recovery as recovery_fixture


async def actual_staged_plan_source(case, *, arm=True, pending_add=False):
    from tests.unit.test_mock_automation_parallel_strategies import first_parallel_buys, fill_order, deliver_price, member_records
    root = Path(case.enterContext(tempfile.TemporaryDirectory()))
    path = root / 'staged-source.sqlite3'
    identity = await KiwoomAccountIdentityReader(recovery_fixture.FakeReadOnlyBroker(), environment=AccountEnvironment.MOCK,
        hmac_key=recovery_fixture.KEY, now_provider=lambda: recovery_fixture.NOW).verify()
    register = SQLiteQueryStore.register_account_identity
    def register_identity(store, value):
        return register(store, {**value, 'identity_fingerprint': identity.identity_fingerprint})
    with patch.object(SQLiteQueryStore, 'register_account_identity', register_identity):
        data, (leader, close) = await first_parallel_buys(case, lambda: SQLiteQueryStore(path))
    original_fill = data.book.fill
    def whole_second_fill(*args):
        # The external fake REST service reports HHMMSS; choose its clock before
        # the actual fill, preserving every native event and raw timestamp.
        data.clock[0] = data.clock[0].replace(microsecond=0) + timedelta(seconds=1)
        return original_fill(*args)
    data.book.fill = whole_second_fill
    await fill_order(data, leader, 2, 'original-entry-fill')
    await deliver_price(data, 1049)
    await deliver_price(data, 1051)
    additional, = member_records(data, 0, side='BUY')
    records = [leader, additional]
    if not pending_add:
        await fill_order(data, additional, 1, 'original-add-fill')
        await deliver_price(data, 1101)
        reduction, = member_records(data, 0, side='SELL')
        await fill_order(data, reduction, 2, 'original-reduce-fill')
        records.append(reduction)
    head = data.execution.load_plan_position(data.runtime.account_ref, data.run_id, leader.intent.symbol, leader.intent.plan)
    case.assertEqual(2 if pending_add else 1, head.quantity)
    case.assertEqual([2, 0] if pending_add else [2, 1, 2], [record.detailed_filled_quantity for record in (
        data.execution.load(row.intent.intent_id) for row in records)])
    # The peer has no fill; its external broker cancellation is reconciled before
    # the source cut. No peer/manual holding is fabricated or migrated.
    # This is an external broker fact, not a runtime cancellation command.
    # BrokerBook.cancel deliberately asserts a native CANCEL_STARTED claim and
    # is therefore inappropriate for this independent account observation.
    data.book.rows[close.broker_order_id].update(state=OrderState.CANCELLED, remaining=0)
    await data.bundle.monitor.refresh_recovery()
    if arm:
        # LEADER accepts the actual approved 0B callback, not the generic
        # minute-strategy price seam. Do not advance the runner before export.
        await deliver_price(data, 940, advance=False, source='staged-first-original-stop')
        notice = data.episodes[0]._protection_snapshot()
        case.assertEqual('PENDING', notice['state'])
        case.assertEqual(head.quantity, notice['planned_leader_stop']['stop_loss']['quantity'])
        data.episodes[0]._save_checkpoint(force=True)
    emergency_stop_mock_automation(data.repository, data.runtime, account_ref=data.runtime.account_ref,
        spec_id=data.specs[0].spec_id, stopped_at=data.runtime.server_now(), reason='test-owned staged plan export')
    await data.close_runner()
    await data.close_shadow()
    await data.close_gateway()
    await data.close_monitor()
    data.runtime.stop()
    selection = dict(source=path, source_instance='actual-staged-plan', operation_id='actual-staged-plan-closure',
        account_ref=data.runtime.account_ref, run_id=data.run_id, intent_id=leader.intent.intent_id)
    native = tuple(data.store.load_execution_intent(row.intent.intent_id) for row in records)
    orders = []
    for row in native:
        physical = data.book.rows[row['broker_order_id']]
        orders.append(DatedBrokerOrderEvidence(row['broker_order_id'], data.clock[0].date(), None, row['symbol'], row['venue'],
            row['side'], row['quantity'], row['limit_price'], sum(fill.quantity for fill in physical['fills']),
            physical['remaining'], physical['state'].value, tuple(physical['fills'])))
    profile = data.selection.credential_profile_id
    evidence = DatedBrokerEvidence(data.runtime.account_ref, profile, 1, data.binding['binding_revision'],
        data.runtime.server_now(), data.runtime.server_now(), (data.clock[0].date(),), 'FULL', 'actual-fake-broker-staged-cut', tuple(orders))
    owner = recovery_fixture.identity_owner(data.store, recovery_fixture.KEY, profile=profile)
    packet, digest, proof = capture_selected_plan_management_bundle(**selection,
        identity_owner=owner, credential_profile_id=profile, broker_evidence=evidence)
    return SimpleNamespace(root=root, data=data, store=data.store, owner=owner, packet=packet, digest=digest,
        proof=proof, selection=selection, evidence=evidence, native=native)


class SelectedPlanBootstrapTests(unittest.IsolatedAsyncioTestCase):
    target = recovery_fixture.SelectedOrderRecoveryTests.target

    async def test_actual_amended_cancel_claim_is_preserved_and_cannot_be_deleted_or_coerced_in_source_proof(self):
        from tests.unit.test_close_order_management import verify_current_buy
        from kiwoom_monitor.domain.selected_order_evidence import NATIVE_EVENT_REFERENCES
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / 'actual-cancel-claim.sqlite3'
        identity = await KiwoomAccountIdentityReader(recovery_fixture.FakeReadOnlyBroker(), environment=AccountEnvironment.MOCK,
            hmac_key=recovery_fixture.KEY, now_provider=lambda: recovery_fixture.NOW).verify()
        register = SQLiteQueryStore.register_account_identity
        def native_identity(store, value):
            return register(store, {**value, 'identity_fingerprint': identity.identity_fingerprint})
        with patch.object(SQLiteQueryStore, 'register_account_identity', native_identity):
            ctx = await verify_current_buy(self, lambda: SQLiteQueryStore(path), cancel=True)
        emergency_stop_mock_automation(ctx.repository, ctx.runtime, account_ref=ctx.runtime.account_ref,
            spec_id=ctx.spec.spec_id, stopped_at=ctx.clock[0], reason='actual native amended/cancelled plan export')
        await ctx.bundle.gateway.close()
        await ctx.bundle.monitor.close()
        ctx.runtime.stop()
        native = ctx.store.load_execution_intent(ctx.buy.intent.intent_id)
        events = ctx.execution.events(ctx.buy.intent.intent_id)
        originals = [(row, name) for row in events for name in NATIVE_EVENT_REFERENCES & row.keys()]
        self.assertTrue(originals, 'the real native command must have committed its actual claim')
        facts = []
        for leg in native['broker_legs']:
            row = ctx.book.rows[leg['broker_order_id']]
            facts.append(DatedBrokerOrderEvidence(leg['broker_order_id'], ctx.clock[0].date(), row['parent'] or None,
                native['symbol'], native['venue'], native['side'], row['ordered'], row['price'],
                sum(fill.quantity for fill in row['fills']), row['remaining'], row['state'].value, tuple(row['fills'])))
        profile = ctx.spec.credential_profile_id
        binding = ctx.store.load_latest_account_binding(profile, 'kiwoom', 'mock')
        evidence = DatedBrokerEvidence(native['account_ref'], profile, 1, binding['binding_revision'],
            ctx.clock[0], ctx.clock[0], (ctx.clock[0].date(),), 'FULL', 'actual-fake-native-cancel-cut', tuple(facts))
        owner = recovery_fixture.identity_owner(ctx.store, recovery_fixture.KEY, profile=profile)
        selection = dict(source=path, source_instance='actual-native-cancel', operation_id='actual-native-cancel-claim',
            account_ref=native['account_ref'], run_id=native['run_id'], intent_id=native['intent_id'])
        before = path.read_bytes()
        packet, digest, proof = capture_selected_plan_management_bundle(**selection,
            identity_owner=owner, credential_profile_id=profile, broker_evidence=evidence)
        self.assertEqual(list(events), [{key: value for key, value in event.items() if key != 'accepted_sequence'}
            for event in packet['events']])
        risk_refs = {row['document']['snapshot_id'] for row in packet['management_closure']['documents']
            if row['collection'] == 'execution_mock_automation_risk_snapshots'}
        self.assertTrue({value['risk_snapshot_id'] for value in [native['amendment']['assessment']]
            + [event[name] for event, name in originals] if value.get('risk_snapshot_id')} <= risk_refs)
        event, name = originals[0]
        for mutation in ('delete', 'numeric_type'):
            changed = deepcopy(packet)
            selected = next(row for row in changed['events'] if row['event_id'] == event['event_id'])
            if mutation == 'delete': selected.pop(name)
            else:
                key = next(key for key, value in selected[name].items() if type(value) is int)
                selected[name][key] = float(selected[name][key])
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ValueError, 'native event facts'):
                owner.issue_selected_order_identity_proof(changed, encode_selected_order_evidence(changed)[1],
                    credential_profile_id=profile, broker_evidence=evidence)
        source = SimpleNamespace(root=root, store=ctx.store, packet=packet, digest=digest, proof=proof,
            owner=owner, selection=selection, evidence=evidence)
        target = await self.target(source)
        target.broker.orders = evidence.orders
        installed = await target.client.post(target.url.replace('/assessment', '/bootstrap'), json=target.request)
        self.assertEqual(200, installed.status_code, installed.text)
        self.assertEqual(list(events), target.store.load_execution_events(native['intent_id']))
        self.assertEqual(digest, proof['packet_sha256'])
        self.assertEqual(before, path.read_bytes())

    async def prepare(self):
        source = await actual_staged_plan_source(self)
        target = await self.target(source)
        target.broker.orders = source.evidence.orders
        return source, target, target.url.replace('/assessment', '/bootstrap')

    async def test_terminal_entry_add_reduce_install_exact_native_ledger_and_one_share_with_separate_cursors(self):
        source, target, url = await self.prepare()
        original_bytes = source.selection['source'].read_bytes()
        encoded = encode_selected_order_evidence(source.packet)
        self.assertEqual(PLAN_MANAGEMENT_VERSION, source.packet['version'])
        self.assertEqual(['BUY', 'BUY', 'SELL'], [row['side'] for row in selected_plan_orders(source.packet)])
        self.assertEqual(['FILLED'] * 3, [row['state'] for row in selected_plan_orders(source.packet)])
        if isinstance(target.store, SQLiteQueryStore):
            with target.store._connection() as connection:
                connection.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('central_execution_events',100)")
        else:
            from kiwoom_monitor.central_server.postgres_access import DBWriterContext, owned_connection_scope
            context = DBWriterContext(writer_family='infrastructure.connection', writer_kind='test.selected_plan_sequence',
                operation='test_target_sequence', access_mode='connection')
            with owned_connection_scope(target.store._connect, context) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT setval(pg_get_serial_sequence('central_execution_events','accepted_sequence'),100,true)")
        response = await target.client.post(url, json=target.request)
        self.assertEqual(200, response.status_code, response.text)
        self.assertFalse(response.json()['receipt']['order_authority'])
        repository = ExecutionRepository(target.store)
        for row in source.native:
            self.assertEqual(row, target.store.load_execution_intent(row['intent_id']))
            self.assertEqual(source.store.load_execution_events(row['intent_id']), target.store.load_execution_events(row['intent_id']))
            self.assertEqual(source.packet['member_spec_id'], repository.load(row['intent_id']).member_spec_id)
        actual = repository.account_events('mock', source.packet['account_ref']).events
        self.assertEqual([row['event_id'] for row in source.packet['events']], [event.source_event_id for event in actual])
        self.assertTrue(all(event.member_spec_id == source.packet['member_spec_id'] for event in actual))
        head = target.store.load_document('execution_mock_plan_positions', source.packet['account_ref'], source.packet['order']['plan']['plan_id'])['document']
        self.assertEqual((1, [[1, source.native[1]['limit_price']]]), (head['quantity'], head['capital_lots']))
        self.assertEqual(response.json()['receipt']['target_plan_fill_cursor'], head['fill_cursor'])
        self.assertEqual(encoded, encode_selected_order_evidence(source.packet))
        self.assertEqual(original_bytes, source.selection['source'].read_bytes())
        for item in source.packet['management_closure']['documents']:
            self.assertIsNone(target.store.load_document(item['collection'], item['owner'], item['key']))
        replay = await target.client.post(url, json=target.request)
        self.assertEqual(response.json()['receipt'], replay.json()['receipt'])

    async def test_missing_terminal_order_policy_action_claim_or_first_cue_fails_closed(self):
        source, target, url = await self.prepare()
        packet = source.packet
        for collection in ('execution_mock_automation_approved_gates', 'execution_leader_position_actions',
                           'execution_mock_automation_risk_snapshots', 'execution_mock_automation_runner_current'):
            changed = deepcopy(packet)
            changed['management_closure']['documents'] = [item for item in changed['management_closure']['documents']
                if item['collection'] != collection]
            with self.subTest(missing=collection), self.assertRaises(ValueError):
                source.owner.issue_selected_order_identity_proof(changed, encode_selected_order_evidence(changed)[1],
                    credential_profile_id=source.evidence.credential_profile_id, broker_evidence=source.evidence)
        changed = deepcopy(packet)
        changed['plan_orders'].pop()
        with self.assertRaises(ValueError): encode_selected_order_evidence(changed)
        changed = deepcopy(packet)
        changed['plan_position']['quantity'] = True
        changed['management_closure']['source_plan_position']['quantity'] = True
        with self.assertRaises(ValueError): encode_selected_order_evidence(changed)
        changed = deepcopy(packet)
        changed['events'][0].pop('reason')
        # Valid codec shape is insufficient: the source owner must compare the
        # complete raw event, including exact optional-field presence/absence.
        with self.assertRaisesRegex(ValueError, 'native event facts'):
            source.owner.issue_selected_order_identity_proof(changed, encode_selected_order_evidence(changed)[1],
                credential_profile_id=source.evidence.credential_profile_id, broker_evidence=source.evidence)
        with target.store.mock_automation_read_scope() as reader:
            reader.execute('SELECT COUNT(*) FROM central_execution_intents')
            self.assertEqual(0, reader.fetchone()[0])

    async def test_epoch_race_rolls_back_all_three_original_intents_and_event_coordinates(self):
        source, target, url = await self.prepare()
        original = target.store.install_selected_order_management
        def raced(*args, **kwargs):
            current, checks = kwargs['current_owner'], []
            def changed():
                checks.append(1)
                if len(checks) == 2: target.owner._revisions['target-profile'] += 1
                return current()
            return original(*args, **{**kwargs, 'current_owner': changed})
        with patch.object(target.store, 'install_selected_order_management', side_effect=raced):
            response = await target.client.post(url, json=target.request)
        self.assertEqual(409, response.status_code, response.text)
        with target.store.mock_automation_read_scope() as reader:
            for table in ('central_execution_intents', 'central_execution_events'):
                reader.execute('SELECT COUNT(*) FROM ' + table)
                self.assertEqual(0, reader.fetchone()[0])
        self.assertIsNone(target.store.load_mock_automation_control(source.packet['account_ref']))

    async def test_lost_commit_ack_replays_the_same_whole_plan_receipt_without_partial_reinstallation(self):
        source, target, url = await self.prepare()
        kind, committed = type(target.store), []
        original = kind.install_selected_order_management
        def lost(store, *args, **kwargs):
            committed.append(original(store, *args, **kwargs)['receipt'])
            raise ConnectionError('fake whole-plan COMMIT response was lost')
        with patch.object(kind, 'install_selected_order_management', lost):
            response = await target.client.post(url, json=target.request)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({'receipt': committed[0], 'replayed': True}, response.json())
        before = {row['intent_id']: (target.store.load_execution_intent(row['intent_id']),
            target.store.load_execution_events(row['intent_id'])) for row in source.native}
        replay = await target.client.post(url, json=target.request)
        self.assertEqual(response.json(), replay.json())
        self.assertEqual(before, {row['intent_id']: (target.store.load_execution_intent(row['intent_id']),
            target.store.load_execution_events(row['intent_id'])) for row in source.native})

    async def test_fake_rest_current_management_protects_one_remaining_share_after_price_recovery(self):
        import asyncio
        from kiwoom_monitor.domain.order_contract import OrderSide, OrderState
        from kiwoom_monitor.central_server.selected_order_broker_evidence import read_kiwoom_selected_order_evidence
        from tests.unit.test_selected_order_broker_evidence import FakeDatedREST
        from tests.unit.test_selected_order_management import actual_management_target
        from kiwoom_monitor.application.selected_order_recovery import broker_evidence_from_dict
        captured = {}
        def provider(book, target, clock):
            original = broker_evidence_from_dict(target.request['source_identity_proof']['broker_evidence'])
            # Seed this independent fake broker with all dated physical facts,
            # before B or management acceptance. Original native rows stay intact.
            book.rows.clear()
            for order in original.orders:
                book.rows[order.broker_order_id] = dict(number=order.broker_order_id, side=OrderSide(order.side),
                    ordered=order.ordered_quantity, remaining=order.remaining_quantity, price=order.limit_price,
                    fills=list(order.fills), parent=order.original_broker_order_id, state=OrderState(order.state))
            rest = FakeDatedREST(original.orders, book=book, clock=clock)
            captured['rest'] = rest
            target.broker.request = rest.request
            return read_kiwoom_selected_order_evidence
        data = await actual_management_target(self, source_factory=actual_staged_plan_source,
            broker_evidence_provider_factory=provider)
        original_bytes = data.source.selection['source'].read_bytes()
        original_packet = encode_selected_order_evidence(data.source.packet)
        url = data.target.url.replace('/assessment', '/prepare-management')
        prepared = await data.target.client.post(url, json=data.request)
        self.assertEqual(200, prepared.status_code, prepared.text)
        current = data.target.owner.bundle('target-profile')
        data.book_context.runtime, data.book_context.execution = current.runtime, current.repository
        data.clock[0] += timedelta(milliseconds=1)
        symbol = data.source.packet['order']['symbol']
        data.quotes[symbol] = dict(price_won=1200, available_at=data.clock[0],
            input_ref='collector-trade:target-staged-price-recovered', durable=False, market_status='TRADABLE')
        accepted = await data.target.client.post(url.replace('/prepare-management', '/accept-management'),
            json={**data.request, 'expected_control_revision': 1})
        self.assertEqual(200, accepted.status_code, accepted.text)
        self.assertTrue(accepted.json()['receipt']['management_ready'])
        self.assertFalse(current.runtime._new_orders_enabled)
        source_ids = {row['intent_id'] for row in data.source.native}
        async def protection():
            for index in range(200):
                records = current.repository.active_intents('mock', current.account_ref, current.run_id)
                sells = [record for record in records if record.intent.side is OrderSide.SELL
                    and record.intent.intent_id not in source_ids and record.broker_order_id
                    and record.state in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}]
                if sells: return sells
                data.clock[0] += timedelta(milliseconds=10)
                data.quotes[symbol] = {**data.quotes[symbol], 'available_at': data.clock[0],
                    'input_ref': 'collector-trade:target-staged-hold-' + str(index)}
                data.supervisor.observe_leader_trade(dict(code=symbol, market='KRX', current_price=1200,
                    trade_time=data.clock[0].strftime('%H%M%S'), cumulative_trade_value=100),
                    data.clock[0], source='target-staged-approved-0B', approved=True)
                data.supervisor.observe_automation_price(symbol, data.quotes[symbol])
                await asyncio.sleep(.05)
            self.fail(str((data.transport.calls, data.supervisor._runners[current.account_ref].status)))
        sells = await asyncio.wait_for(protection(), timeout=30)
        self.assertEqual([1], [record.intent.quantity for record in sells])
        self.assertEqual(['SELL'], [row[1] for row in data.transport.calls])
        sell, = sells
        data.clock[0] += timedelta(milliseconds=1)
        data.book.fill(sell.broker_order_id, 1, 'current-staged-original-protection-fill')
        await current.monitor.refresh_recovery()
        from kiwoom_monitor.domain.selected_order_evidence import INTENT_FIELDS, NATIVE_ORDER_REFERENCES
        for row in data.source.native:
            actual = data.target.store.load_execution_intent(row['intent_id'])
            immutable = INTENT_FIELDS | (NATIVE_ORDER_REFERENCES & row.keys())
            self.assertEqual({key: row[key] for key in immutable}, {key: actual[key] for key in immutable})
            self.assertEqual(row['fill_ids'], actual['fill_ids'])
        self.assertEqual(0, current.repository.load_plan_position(current.account_ref, current.run_id, symbol, sell.intent.plan).quantity)
        self.assertEqual(original_packet, encode_selected_order_evidence(data.source.packet))
        self.assertEqual(original_bytes, data.source.selection['source'].read_bytes())
