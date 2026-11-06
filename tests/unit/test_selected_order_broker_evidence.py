"""Official-shaped fake REST facts reach the existing read-only/native owners."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from kiwoom_monitor.application.selected_order_recovery import DatedBrokerEvidence, DatedBrokerOrderEvidence, broker_evidence_from_dict
from kiwoom_monitor.central_server.selected_order_broker_evidence import read_kiwoom_selected_order_evidence
from kiwoom_monitor.central_server.selected_order_recovery_routes import create_selected_order_recovery_router
from kiwoom_monitor.domain.order_contract import BrokerFill
from tests.unit import test_selected_order_recovery as recovery_fixture


KST, NUMBER = recovery_fixture.KST, recovery_fixture.NUMBER


NOW = datetime(2026, 9, 21, 15, 5, tzinfo=KST)


class FakeDatedREST:
    """No socket or order method; the external broker owns these explicit facts."""
    def __init__(self, orders, *, book=None, clock=None):
        self.orders, self.book, self.clock, self.calls = orders, book, clock, []
        self.overrides = {}

    def facts(self):
        if self.book is None:
            return self.orders
        return tuple(replace(order, filled_quantity=sum(fill.quantity for fill in self.book.rows[order.broker_order_id]['fills']),
            remaining_quantity=self.book.rows[order.broker_order_id]['remaining'],
            state=self.book.rows[order.broker_order_id]['state'].value,
            fills=tuple(self.book.rows[order.broker_order_id]['fills'])) for order in self.orders)

    async def request(self, api_id, path, body, *, cont_yn='N', next_key=''):
        assert path == '/api/dostk/acnt'
        self.calls.append((api_id, deepcopy(body), cont_yn, next_key))
        if api_id == 'ka00001':
            return SimpleNamespace(payload={'acctNo': NUMBER})
        override = self.overrides.get(api_id)
        if override is not None:
            return override(body, cont_yn, next_key)
        rows = []
        for order in self.facts():
            if api_id in {'kt00007', 'kt00009'} and body['ord_dt'] != order.trading_date.strftime('%Y%m%d'):
                continue
            common = dict(ord_no=order.broker_order_id, stk_cd=order.symbol,
                io_tp_nm='매수' if order.side == 'BUY' else '매도', ord_qty=str(order.ordered_quantity))
            if api_id in {'ka10075', 'ka10076'}:
                if api_id == 'ka10075' and not order.remaining_quantity:
                    continue
                rows.append({**common, 'orig_ord_no': order.original_broker_order_id or '0000000',
                    'ord_pric': str(order.limit_price), 'cntr_qty': str(order.filled_quantity),
                    'oso_qty': str(order.remaining_quantity), 'stex_tp': '1',
                    'ord_stt': '취소' if order.state == 'CANCELLED' else '체결' if order.filled_quantity else '접수'})
            elif api_id == 'kt00007':
                rows.append({**common, 'ori_ord': order.original_broker_order_id or '0000000',
                    'ord_uv': str(order.limit_price), 'cntr_qty': str(order.filled_quantity),
                    'ord_remnq': str(order.remaining_quantity), 'dmst_stex_tp': 'KRX',
                    'mdfy_cncl': '취소' if order.state == 'CANCELLED' else '', 'acpt_tp': '접수'})
            elif api_id == 'kt00009':
                for fill in order.fills:
                    rows.append({**common, 'orig_ord_no': order.original_broker_order_id or '0000000',
                        'ord_uv': str(order.limit_price), 'dmst_stex_tp': 'KRX', 'cntr_no': fill.execution_id,
                        'cntr_qty': str(fill.quantity), 'cntr_uv': str(fill.price),
                        'cntr_tm': fill.occurred_at.astimezone(KST).strftime('%H%M%S')})
            else:
                raise AssertionError('fake REST has no order/unknown query: ' + api_id)
        table = {'ka10075': 'oso', 'ka10076': 'cntr', 'kt00007': 'acnt_ord_cntr_prps_dtl',
                 'kt00009': 'acnt_ord_cntr_prst_array'}[api_id]
        return self.page({table: rows, 'return_code': 0})

    @staticmethod
    def page(payload, has_next=False, next_key=''):
        return SimpleNamespace(payload=payload, has_next=has_next, next_key=next_key)


class SelectedOrderBrokerEvidenceTests(unittest.IsolatedAsyncioTestCase):
    source, target = recovery_fixture.SelectedOrderRecoveryTests.source, recovery_fixture.SelectedOrderRecoveryTests.target

    def fixture(self, *, amended=False):
        fill = BrokerFill('actual-execution-01', 1, 1000, NOW - timedelta(minutes=2))
        orders = (DatedBrokerOrderEvidence('original-01', NOW.date(), None, '005930', 'KRX', 'BUY',
            4, 1000, 1, 3, 'PARTIALLY_FILLED', (fill,)),)
        if amended:
            orders = (replace(orders[0], remaining_quantity=0, state='CANCELLED'),
                DatedBrokerOrderEvidence('amended-02', NOW.date(), 'original-01', '005930', 'KRX', 'BUY',
                    3, 1100, 1, 2, 'PARTIALLY_FILLED', (replace(fill, price=1100),)))
        source = DatedBrokerEvidence('origin', 'source-profile', 1, 1, NOW - timedelta(minutes=1),
            NOW - timedelta(minutes=1), (NOW.date(),), 'FULL', 'signed-source-dated-facts', orders)
        packet = dict(account_ref='origin', order=dict(symbol='005930', broker_legs=[dict(
            broker_order_id=order.broker_order_id, transferred_quantity=3 if amended and i == 0 else 0)
            for i, order in enumerate(orders)]))
        identity = SimpleNamespace(target_account_ref='target', credential_profile_id='target-profile',
            credential_revision=3, binding_revision=2)
        broker = FakeDatedREST(orders)
        bundle = SimpleNamespace(broker=broker, runtime=SimpleNamespace(server_now=lambda: NOW))
        return SimpleNamespace(source=source, packet=packet, identity=identity, broker=broker, bundle=bundle)

    async def read(self, data):
        return await read_kiwoom_selected_order_evidence(bundle=data.bundle, identity=data.identity,
            packet=data.packet, source_evidence=data.source)

    async def test_explicit_dated_details_preserve_physical_parent_and_same_raw_fill_id_on_distinct_legs(self):
        data = self.fixture(amended=True)
        evidence = await self.read(data)
        self.assertEqual('FULL', evidence.coverage, evidence.source_ref)
        self.assertEqual(data.source.orders, evidence.orders)
        self.assertEqual(('target', 'target-profile', 3, 2), (evidence.account_ref,
            evidence.credential_profile_id, evidence.credential_revision, evidence.binding_revision))
        self.assertEqual(['ka10075', 'ka10076', 'kt00007', 'kt00009'], [call[0] for call in data.broker.calls])
        for api, body, _, _ in data.broker.calls[2:]:
            self.assertEqual(('20260921', '005930', '', 'KRX'),
                (body['ord_dt'], body['stk_cd'], body['fr_ord_no'], body['dmst_stex_tp']))
            self.assertEqual('1' if api == 'kt00007' else '0', body['qry_tp'])

    async def test_missing_signed_source_dates_do_not_infer_created_or_current_day_or_query_broker(self):
        data = self.fixture()
        data.source = replace(data.source, covered_dates=(), orders=tuple(replace(order, trading_date=None)
            for order in data.source.orders))
        data.packet['order']['created_at'] = NOW.isoformat()
        evidence = await self.read(data)
        self.assertEqual(('UNAVAILABLE', (), ()), (evidence.coverage, evidence.covered_dates, evidence.orders))
        self.assertEqual([], data.broker.calls)

    async def test_aggregate_without_actual_execution_id_or_time_never_synthesizes_fill(self):
        for missing in ('cntr_no', 'cntr_tm'):
            with self.subTest(missing=missing):
                data = self.fixture()
                original = await data.broker.request('kt00009', '/api/dostk/acnt', {'ord_dt': '20260921'})
                original.payload['acnt_ord_cntr_prst_array'][0].pop(missing)
                data.broker.overrides['kt00009'] = lambda *_: original
                evidence = await self.read(data)
                self.assertNotEqual('FULL', evidence.coverage)
                self.assertFalse(any(order.fills for order in evidence.orders))

    async def test_repeated_execution_detail_remains_duplicate_and_missing_page_or_unsupported_mock_is_unknown(self):
        data = self.fixture()
        original = await data.broker.request('kt00009', '/api/dostk/acnt', {'ord_dt': '20260921'})
        original.payload['acnt_ord_cntr_prst_array'] *= 2
        data.broker.overrides['kt00009'] = lambda *_: original
        evidence = await self.read(data)
        self.assertEqual('PARTIAL', evidence.coverage)
        self.assertEqual(['actual-execution-01'] * 2, [fill.execution_id for fill in evidence.orders[0].fills])
        for result in (FakeDatedREST.page({}, False), FakeDatedREST.page({'return_code': 10, 'return_msg': 'secret fake account'}),
                FakeDatedREST.page({'acnt_ord_cntr_prst_array': []}, True, ''),
                FakeDatedREST.page({'acnt_ord_cntr_prst_array': []}, True, 'repeated-key')):
            data = self.fixture()
            data.broker.overrides['kt00009'] = lambda *_, value=result: value
            evidence = await self.read(data)
            self.assertEqual('UNAVAILABLE', evidence.coverage)
            self.assertNotIn('secret fake account', evidence.source_ref)

    async def test_complete_continuation_reuses_same_explicit_day_and_preserves_both_execution_ids(self):
        data = self.fixture()
        second = BrokerFill('actual-execution-02', 1, 1000, NOW - timedelta(minutes=1))
        data.broker.orders = (replace(data.broker.orders[0], filled_quantity=2, remaining_quantity=2,
            fills=data.broker.orders[0].fills + (second,)),)
        original = await data.broker.request('kt00009', '/api/dostk/acnt', {'ord_dt': '20260921'})
        rows = original.payload['acnt_ord_cntr_prst_array']
        data.broker.calls.clear()
        data.broker.overrides['kt00009'] = lambda _, cont, key: FakeDatedREST.page(
            {'acnt_ord_cntr_prst_array': rows[:1] if cont == 'N' else rows[1:]}, cont == 'N', 'page-2' if cont == 'N' else '')
        evidence = await self.read(data)
        self.assertEqual('FULL', evidence.coverage, evidence.source_ref)
        self.assertEqual(('actual-execution-01', 'actual-execution-02'), tuple(fill.execution_id for fill in evidence.orders[0].fills))
        self.assertEqual([('N', ''), ('Y', 'page-2')], [(call[2], call[3]) for call in data.broker.calls if call[0] == 'kt00009'])

    async def test_live_order_id_reused_on_another_day_and_metadata_conflict_cannot_look_verified(self):
        for changed in ('day', 'parent', 'price'):
            with self.subTest(changed=changed):
                data = self.fixture()
                if changed == 'day':
                    day = NOW.date() - timedelta(days=1)
                    data.source = replace(data.source, covered_dates=(day,), orders=(replace(data.source.orders[0], trading_date=day),))
                    data.broker.orders = data.source.orders
                else:
                    original = await data.broker.request('kt00007', '/api/dostk/acnt', {'ord_dt': '20260921'})
                    original.payload['acnt_ord_cntr_prps_dtl'][0]['ori_ord' if changed == 'parent' else 'ord_uv'] = '99999'
                    data.broker.overrides['kt00007'] = lambda *_: original
                evidence = await self.read(data)
                self.assertEqual('PARTIAL', evidence.coverage)
                self.assertTrue(not evidence.orders or evidence.orders[0].state == 'UNVERIFIED')

    async def test_unselected_amendment_visible_only_in_detail_cannot_hide_behind_earlier_summary(self):
        data = self.fixture()
        original = await data.broker.request('kt00009', '/api/dostk/acnt', {'ord_dt': '20260921'})
        child = {**original.payload['acnt_ord_cntr_prst_array'][0], 'ord_no': 'new-amended-child',
            'orig_ord_no': 'original-01', 'cntr_no': 'actual-child-execution'}
        original.payload['acnt_ord_cntr_prst_array'].append(child)
        data.broker.overrides['kt00009'] = lambda *_: original
        evidence = await self.read(data)
        self.assertEqual('PARTIAL', evidence.coverage)
        self.assertIn('UNSELECTED_CHILD_ORDER_PRESENT', evidence.source_ref)

    async def test_complete_old_closed_order_does_not_require_a_fake_current_incarnation(self):
        data = self.fixture()
        day = NOW.date() - timedelta(days=1)
        order = replace(data.source.orders[0], trading_date=day, filled_quantity=4, remaining_quantity=0,
            state='FILLED', fills=(BrokerFill('old-actual-id', 4, 1000, NOW - timedelta(days=1, minutes=2)),))
        data.source = replace(data.source, covered_dates=(day,), orders=(order,))
        data.broker.orders = (order,)
        data.broker.overrides['ka10075'] = lambda *_: FakeDatedREST.page({'oso': []})
        data.broker.overrides['ka10076'] = lambda *_: FakeDatedREST.page({'cntr': []})
        evidence = await self.read(data)
        self.assertEqual('FULL', evidence.coverage, evidence.source_ref)
        self.assertEqual((order,), evidence.orders)

    async def test_fake_rest_reaches_real_identity_assessment_and_closed_native_install(self):
        from tests.unit.test_selected_order_bootstrap import SelectedOrderBootstrapTests
        source = await SelectedOrderBootstrapTests.native_member_source(self)
        target = await self.target(source)
        rest = FakeDatedREST(broker_evidence_from_dict(source.proof['broker_evidence']).orders)
        target.owner.bundle('target-profile').broker = rest
        app = FastAPI()
        app.include_router(create_selected_order_recovery_router(target.store, target.owner, lambda: None,
            broker_evidence_provider=read_kiwoom_selected_order_evidence))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://fake-rest.test') as client:
            assessed = await client.post(target.url, json=target.request)
            self.assertEqual(200, assessed.status_code, assessed.text)
            self.assertEqual('MATCH', assessed.json()['status'], assessed.text)
            installed = await client.post(target.url.replace('/assessment', '/bootstrap'), json=target.request)
            self.assertEqual(200, installed.status_code, installed.text)
        receipt = installed.json()['receipt']
        self.assertEqual((False, False, False), (receipt['order_authority'], receipt['runtime_started'], receipt['new_orders_enabled']))
        self.assertEqual(source.packet['member_spec_id'], target.store.load_execution_intent(source.packet['order']['intent_id'])['mock_account_member_spec_id'])
        self.assertEqual(2, sum(call[0] == 'ka00001' for call in rest.calls))
        self.assertTrue(all(call[0] in {'ka00001', 'ka10075', 'ka10076', 'kt00007', 'kt00009'} for call in rest.calls))

    async def test_fake_rest_reaches_current_canonical_owner_and_original_policy_actor_with_entry_off(self):
        from tests.unit.test_selected_order_bootstrap import SelectedOrderBootstrapTests
        from tests.unit.test_selected_order_management import actual_management_target
        async def source(case):
            return await SelectedOrderBootstrapTests.native_member_source(case, arm=True, whole_second_fill=True)
        captured = {}
        def provider(book, target, clock):
            original = broker_evidence_from_dict(target.request['source_identity_proof']['broker_evidence'])
            rest = FakeDatedREST(original.orders, book=book, clock=clock)
            captured['rest'] = rest
            target.broker.request = rest.request
            return read_kiwoom_selected_order_evidence
        data = await actual_management_target(self, source_factory=source, broker_evidence_provider_factory=provider)
        original_bytes = data.source.selection['source'].read_bytes()
        prepared = await data.target.client.post(data.target.url.replace('/assessment', '/prepare-management'), json=data.request)
        self.assertEqual(200, prepared.status_code, prepared.text)
        current = data.target.owner.bundle('target-profile')
        data.book_context.runtime, data.book_context.execution = current.runtime, current.repository
        self.assertFalse(data.old.runtime._active)
        self.assertEqual(data.source.packet['account_ref'], current.account_ref)
        self.assertEqual(data.target.binding.scope.account_ref, current.canonical_account_ref)
        data.clock[0] += timedelta(milliseconds=1)
        symbol = data.source.packet['order']['symbol']
        data.quotes[symbol] = dict(price_won=1200, available_at=data.clock[0],
            input_ref='collector-trade:provider-current-management-price', durable=False, market_status='TRADABLE')
        accepted = await data.target.client.post(data.target.url.replace('/assessment', '/accept-management'),
            json={**data.request, 'expected_control_revision': 1})
        self.assertEqual(200, accepted.status_code, accepted.text)
        self.assertTrue(accepted.json()['receipt']['management_ready'])
        self.assertFalse(current.runtime._new_orders_enabled)
        runner = data.supervisor._runners[current.account_ref]
        self.assertEqual(data.source.packet['source_spec_id'], runner.episode.status['spec_id'])
        self.assertEqual(data.source.packet['order']['plan']['policy_ref'],
            current.repository.load(data.source.packet['order']['intent_id']).intent.plan.policy_ref)
        self.assertEqual(original_bytes, data.source.selection['source'].read_bytes())
        self.assertTrue(any(call[0] == 'kt00009' for call in captured['rest'].calls))
        self.assertFalse(any(call[0] == 'kt00018' for call in captured['rest'].calls))

    async def test_actual_app_injects_the_provider_while_query_boot_remains_owner_free(self):
        from pathlib import Path
        import tempfile
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        import kiwoom_monitor.central_server.selected_order_recovery_routes as routes
        original = routes.create_selected_order_recovery_router
        calls = []
        def record(*args, **kwargs):
            calls.append(kwargs)
            return original(*args, **kwargs)
        with tempfile.TemporaryDirectory() as root, patch.object(routes, 'create_selected_order_recovery_router', side_effect=record):
            app = create_app(CentralServerSettings('sqlite:///' + str(Path(root) / 'query.sqlite3'), 'admin-fixture',
                autonomous_top20_enabled=False, market_event_collection_enabled=False, news_history_jobs_enabled=False))
            async with app.router.lifespan_context(app):
                self.assertIsNone(app.state.mock_credential_owner)
                self.assertIsNone(app.state.mock_automation_supervisor)
                self.assertIs(calls[0]['broker_evidence_provider'], read_kiwoom_selected_order_evidence)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://query.test') as client:
                    path = '/api/v1/mock-automation/selected-order-evidence/missing/assessment'
                    body = dict(credential_profile_id='none', credential_revision=1,
                        account_ref='11111111-1111-4111-8111-111111111111', binding_revision=1, packet_sha256='a' * 64)
                    self.assertEqual(401, (await client.post(path, json=body)).status_code)
                    result = await client.post(path, json=body, headers={'Authorization': 'Bearer admin-fixture'})
                    self.assertEqual(503, result.status_code, result.text)
            self.assertEqual('completed', app.state.shutdown_status['state'])

