"""Independent native registries and fake dated broker facts grant no order authority."""
import asyncio
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Header, HTTPException

from scripts.preserve_selected_order_evidence import capture_selected_order_evidence, capture_selected_order_recovery_bundle, capture_selected_order_management_bundle
from kiwoom_monitor.application.account_identity import verify_and_bind_account, link_local_scope_to_verified_binding
from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation
from kiwoom_monitor.application.selected_order_recovery import DatedBrokerEvidence, DatedBrokerOrderEvidence
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.mock_runtime import MockCredentialOwner
from kiwoom_monitor.central_server.selected_order_recovery_routes import create_selected_order_recovery_router
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope, BrokerFill
from kiwoom_monitor.domain.selected_order_evidence import MEMBER_VERSION, encode_selected_order_evidence
from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import KiwoomAccountIdentityReader


KEY = b'fixture-identity-namespace-key-32-bytes'
NUMBER = '12345678-01'
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 9, 21, 15, tzinfo=KST)


class FakeReadOnlyBroker:
    def __init__(self, number=NUMBER):
        self.number, self.calls, self.identity_callback = number, [], None
        self.orders, self.coverage = (), 'FULL'

    async def request(self, api_id, path, body, **kwargs):
        self.calls.append(api_id)
        if api_id != 'ka00001':
            raise AssertionError('this fixture never submits or mutates a broker order')
        if self.identity_callback is not None:
            self.identity_callback()
        return SimpleNamespace(payload={'acctNo': self.number})

    async def dated_provider(self, *, bundle, identity, packet, source_evidence=None):
        self.calls.append('dated-detail-fixture')
        started = bundle.clock[0]
        bundle.clock[0] += timedelta(milliseconds=100)
        return DatedBrokerEvidence(bundle.account_ref, identity.credential_profile_id, identity.credential_revision,
            identity.binding_revision, started, bundle.clock[0], tuple(sorted({order.trading_date for order in self.orders
                if order.trading_date is not None})), self.coverage, 'fake-broker-dated-cut', self.orders)


def identity_owner(store, key, bundle=None, profile='source-profile'):
    owner = MockCredentialOwner.__new__(MockCredentialOwner)
    owner.store, owner.hmac_key, owner._closing = store, key, False
    owner._account_locks, owner._pending_profiles = {}, set()
    owner._bundles = {profile: bundle} if bundle is not None else {}
    owner._revisions = {profile: 1}
    return owner


def dated_orders(packet, *, late=False):
    physical = packet['order'].get('broker_legs') or [dict(broker_order_id=packet['order']['broker_order_id'],
        original_broker_order_id='', ordered_quantity=packet['order']['quantity'], limit_price=packet['order']['limit_price'],
        detailed_filled_quantity=packet['order']['detailed_filled_quantity'], transferred_quantity=0)]
    result = []
    for index, leg in enumerate(physical):
        fills = tuple(BrokerFill(event['broker_execution_id'], event['quantity'], event['price'],
            datetime.fromisoformat(event['occurred_at'])) for event in packet['events']
            if event['event_type'] == 'FILL' and event['broker_order_id'] == leg['broker_order_id'])
        if late:
            fills += (BrokerFill('same-raw-late-id', 1, leg['limit_price'], datetime.fromisoformat(packet['stopped_at']) + timedelta(seconds=30 + index)),)
        filled = sum(fill.quantity for fill in fills)
        # This synthetic book issues its orders in the native fixture's original
        # session. Production providers must supply their authoritative date.
        result.append(DatedBrokerOrderEvidence(leg['broker_order_id'], datetime.fromisoformat(packet['order']['created_at']).astimezone(KST).date(),
            leg['original_broker_order_id'] or None, packet['order']['symbol'], packet['order']['venue'],
            packet['order']['side'], leg['ordered_quantity'], leg['limit_price'], filled,
            max(0, leg['ordered_quantity'] - leg['transferred_quantity'] - filled), 'PARTIALLY_FILLED' if filled else 'ACCEPTED', fills))
    return tuple(result)


class SelectedOrderRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def source(self, amended=False, amendment_mode='accepted', management=False):
        source_broker = FakeReadOnlyBroker()
        identity = await KiwoomAccountIdentityReader(source_broker, environment=AccountEnvironment.MOCK,
            hmac_key=KEY, now_provider=lambda: NOW - timedelta(days=1)).verify()
        register = SQLiteQueryStore.register_account_identity
        def verified_fixture(store, value):
            return register(store, {**value, 'identity_fingerprint': identity.identity_fingerprint})
        with patch.object(SQLiteQueryStore, 'register_account_identity', verified_fixture):
            if amended:
                from tests.unit.test_selected_order_amendment_evidence import stopped_source
                root, ctx, selection = await stopped_source(self, amendment_mode)
                store, profile = ctx.store, ctx.spec.credential_profile_id
                member = None
            else:
                from tests.unit.test_mock_automation_account_execution import group_planned_inputs
                root = Path(self.enterContext(tempfile.TemporaryDirectory()))
                store = self.enterContext(closing(SQLiteQueryStore(root / 'source.sqlite3')))
                store.initialize()
                data, native, recovery, risk, plans = group_planned_inputs(self, store)
                spec, gate, intent = plans[1]
                native.runtime.submit_automation_intent(intent, recovery.account, spec_id=spec.spec_id,
                    control_revision=gate.control_revision, plan_recovery=recovery)
                emergency_stop_mock_automation(data.repository, native.runtime, account_ref=native.runtime.account_ref,
                    spec_id=data.specs[0].spec_id, stopped_at=native.clock[0], reason='test-owned stopped source')
                native.runtime.stop()
                selection = dict(source=root / 'source.sqlite3', source_instance='native-source', operation_id='selected-native-member',
                    account_ref=native.runtime.account_ref, run_id=native.run_id, intent_id=intent.intent_id)
                profile, member = data.selection.credential_profile_id, spec.spec_id
        packet, digest = capture_selected_order_evidence(**selection)
        source_orders = dated_orders(packet)
        source_cut = datetime.fromisoformat(packet['stopped_at']) + timedelta(seconds=1)
        evidence = DatedBrokerEvidence(packet['account_ref'], profile, 1, 1, source_cut, source_cut,
            tuple(sorted({order.trading_date for order in source_orders})),
            'FULL', 'fake-source-dated-cut', source_orders)
        owner = identity_owner(store, KEY, profile=profile)
        capture = capture_selected_order_management_bundle if management else capture_selected_order_recovery_bundle
        repeated, repeated_digest, proof = capture(**selection,
            identity_owner=owner, credential_profile_id=profile, broker_evidence=evidence)
        if management:
            self.assertEqual((packet, digest), capture_selected_order_evidence(**selection))
            packet, digest = repeated, repeated_digest
        else:
            self.assertEqual((packet, digest), (repeated, repeated_digest))
        return SimpleNamespace(root=root, store=store, packet=packet, digest=digest, proof=proof,
            owner=owner, selection=selection, member=member, source_broker=source_broker)

    async def target(self, source, *, number=NUMBER, key=KEY, provider=True, verification_at=None):
        path = source.root / ('target-' + str(len(list(source.root.glob('target-*.sqlite3')))) + '.sqlite3')
        factory = getattr(self, 'target_store_factory', None)
        store = self.enterContext(closing(factory() if factory is not None else SQLiteQueryStore(path)))
        store.initialize()
        store.accept_selected_order_evidence(source.packet, expected_sha256=source.digest,
            account_ref=source.packet['account_ref'], run_id=source.packet['run_id'])
        broker, clock, profile = FakeReadOnlyBroker(number), [verification_at if verification_at is not None else
            datetime.fromisoformat(source.proof['broker_evidence']['query_completed_at']) + timedelta(minutes=1)], 'target-profile'
        binding = await verify_and_bind_account(KiwoomAccountIdentityReader(broker, environment=AccountEnvironment.MOCK,
            hmac_key=key, now_provider=lambda: clock[0]), store, credential_profile_id=profile)
        bundle = SimpleNamespace(account_ref=binding.scope.account_ref, binding=binding, broker=broker, clock=clock,
            runtime=SimpleNamespace(server_now=lambda: clock[0]))
        owner = identity_owner(store, key, bundle, profile)
        broker.orders = dated_orders(source.packet, late=True)
        def authorize(authorization: str | None = Header(default=None)):
            if authorization != 'Bearer fixture': raise HTTPException(401)
        app = FastAPI()
        app.include_router(create_selected_order_recovery_router(store, owner, authorize,
            broker_evidence_provider=broker.dated_provider if provider else None))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://readonly.test',
            headers={'Authorization': 'Bearer fixture'})
        self.addAsyncCleanup(client.aclose)
        request = dict(credential_profile_id=profile, credential_revision=1, account_ref=binding.scope.account_ref,
            binding_revision=binding.binding_revision, packet_sha256=source.digest, source_identity_proof=source.proof)
        return SimpleNamespace(path=path, store=store, broker=broker, owner=owner, binding=binding, client=client,
            request=request, url=f"/api/v1/mock-automation/selected-order-evidence/{source.packet['operation_id']}/assessment")

    async def test_native_non_anchor_capture_and_independent_verified_account_match_are_read_only(self):
        source = await self.source()
        self.assertEqual(MEMBER_VERSION, source.packet['version'])
        self.assertEqual(source.member, source.packet['source_spec_id'])
        self.assertEqual(source.member, source.packet['member_spec_id'])
        self.assertNotEqual(source.member, source.packet['anchor_spec_id'])
        target = await self.target(source)
        self.assertNotEqual(source.packet['account_ref'], target.binding.scope.account_ref)
        original = (source.selection['source'].read_bytes(), target.path.read_bytes())
        response = await target.client.post(target.url, json=target.request)
        self.assertEqual(200, response.status_code, response.text)
        result = response.json()
        self.assertEqual('MATCH', result['status'], result['reasons'])
        self.assertEqual((False, False), (result['order_authority'], result['runtime_imported']))
        self.assertEqual('same-raw-late-id', result['target_broker_evidence']['orders'][0]['fills'][0]['execution_id'])
        self.assertEqual(source.packet['source_cursor'], result['source_cursor'])
        self.assertEqual(original, (source.selection['source'].read_bytes(), target.path.read_bytes()))
        self.assertNotIn('account_token', result)
        self.assertNotIn('identity_fingerprint', str(result))
        for table in ('central_execution_intents', 'central_execution_events', 'central_execution_runtime_leases'):
            with target.store.mock_automation_read_scope() as reader:
                self.assertEqual(0, reader.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0])

    async def test_target_alias_cannot_make_a_different_broker_account_match(self):
        source = await self.source()
        target = await self.target(source, number='12345678-02')
        link_local_scope_to_verified_binding(target.store, origin_scope=AccountScope(
            'kiwoom', AccountEnvironment.MOCK, source.packet['account_ref']), binding=target.binding, verified_at=NOW)
        response = await target.client.post(target.url, json=target.request)
        self.assertEqual('CONFLICT', response.json()['status'])
        self.assertIn('VERIFIED_BROKER_ACCOUNT_DIFFERS', response.json()['reasons'])
        self.assertNotIn('dated-detail-fixture', target.broker.calls)

    async def test_unrelated_namespace_and_unavailable_real_detail_provider_remain_unknown(self):
        source = await self.source()
        other = await self.target(source, key=b'other-protected-namespace-key-32-byte')
        result = (await other.client.post(other.url, json=other.request)).json()
        self.assertEqual('UNKNOWN', result['status'])
        self.assertIn('PROTECTED_NAMESPACE_INCOMPARABLE', result['reasons'])
        unavailable = await self.target(source, provider=False)
        result = (await unavailable.client.post(unavailable.url, json=unavailable.request)).json()
        self.assertEqual('UNKNOWN', result['status'])
        self.assertIn('TARGET_DATED_DETAIL_PROVIDER_UNAVAILABLE', result['reasons'])
        missing = source.owner.issue_selected_order_identity_proof(source.packet, source.digest,
            credential_profile_id=source.proof['credential_profile_id'])
        comparable = await self.target(source)
        result = (await comparable.client.post(comparable.url,
            json={**comparable.request, 'source_identity_proof': missing})).json()
        self.assertEqual('UNKNOWN', result['status'])
        self.assertIn('SOURCE_DATED_BROKER_EVIDENCE_UNAVAILABLE', result['reasons'])

    async def test_target_cut_cannot_precede_closed_source_and_original_order_date_need_not_be_query_date(self):
        source = await self.source(amended=True)
        stopped = datetime.fromisoformat(source.packet['stopped_at']).astimezone(timezone.utc)
        target = await self.target(source, verification_at=stopped - timedelta(seconds=1))
        result = (await target.client.post(target.url, json=target.request)).json()
        self.assertEqual('UNKNOWN', result['status'], result['reasons'])
        self.assertIn('TARGET_BROKER_CUT_PRECEDES_SOURCE_STOP', result['reasons'])
        clock = target.owner.bundle('target-profile').clock
        clock[0] = stopped + timedelta(milliseconds=500)
        result = (await target.client.post(target.url, json=target.request)).json()
        self.assertEqual('UNKNOWN', result['status'], result['reasons'])
        self.assertIn('TARGET_BROKER_CUT_PRECEDES_SOURCE_BROKER_CUT', result['reasons'])
        clock[0] = datetime.fromisoformat(source.proof['broker_evidence']['query_completed_at']).astimezone(timezone.utc) + timedelta(days=1)
        result = (await target.client.post(target.url, json=target.request)).json()
        self.assertEqual('MATCH', result['status'], result['reasons'])
        self.assertNotEqual(target.broker.orders[0].trading_date, clock[0].astimezone(KST).date())

    async def test_request_flags_forged_proof_and_changed_credential_epoch_never_verify(self):
        source = await self.source()
        target = await self.target(source)
        self.assertEqual(422, (await target.client.post(target.url, json={**target.request, 'verified': True})).status_code)
        secret = deepcopy(target.request)
        secret['source_identity_proof']['identity_key'] = 'forbidden-secret-input'
        self.assertEqual(422, (await target.client.post(target.url, json=secret)).status_code)
        tampered = deepcopy(target.request)
        tampered['source_identity_proof']['binding_revision'] += 1
        self.assertEqual('CONFLICT', (await target.client.post(target.url, json=tampered)).json()['status'])
        target.broker.identity_callback = lambda: target.owner._revisions.update({'target-profile': 2})
        response = await target.client.post(target.url, json=target.request)
        self.assertEqual(409, response.status_code, response.text)

    async def test_dated_order_incarnation_and_detail_fill_coverage_are_not_aggregate_guesses(self):
        source = await self.source()
        target = await self.target(source)
        [order] = target.broker.orders
        target.broker.orders = (replace(order, trading_date=order.trading_date + timedelta(days=1)),)
        result = (await target.client.post(target.url, json=target.request)).json()
        self.assertEqual('CONFLICT', result['status'])
        self.assertIn('BROKER_ORDER_INCARNATION_DATE_CONFLICT', result['reasons'])
        target.broker.orders = (replace(order, fills=()),)
        result = (await target.client.post(target.url, json=target.request)).json()
        self.assertEqual('UNKNOWN', result['status'])
        self.assertIn('TARGET_DETAILED_FILL_COVERAGE_INCOMPLETE', result['reasons'])
        target.broker.orders = (replace(order, fills=order.fills * 2, filled_quantity=2),)
        result = (await target.client.post(target.url, json=target.request)).json()
        self.assertEqual('CONFLICT', result['status'])
        self.assertIn('TARGET_DETAILED_FILL_ID_DUPLICATED', result['reasons'])

    async def test_query_without_an_identity_owner_exposes_unavailable_without_loading_execution(self):
        store = SimpleNamespace(load_document=lambda *_args, **_kwargs: self.fail('unavailable route read native state'))
        app = FastAPI()
        app.include_router(create_selected_order_recovery_router(store, None, lambda: None))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://query.test') as client:
            response = await client.post('/api/v1/mock-automation/selected-order-evidence/missing/assessment', json={
                'credential_profile_id': 'target-profile', 'credential_revision': 1,
                'account_ref': '11111111-1111-4111-8111-111111111111', 'binding_revision': 1,
                'packet_sha256': 'a' * 64})
        self.assertEqual(503, response.status_code)

    async def test_actual_query_app_wires_assessment_auth_without_starting_an_execution_owner(self):
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        app = create_app(CentralServerSettings(f"sqlite:///{root / 'query.sqlite3'}", 'admin-fixture',
            query_access_token='query-fixture', autonomous_top20_enabled=False,
            market_event_collection_enabled=False, news_history_jobs_enabled=False))
        async with app.router.lifespan_context(app):
            self.assertIsNone(app.state.mock_credential_owner)
            self.assertIsNone(app.state.mock_automation_supervisor)
            self.assertIsNone(app.state.mock_account_bundle)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://query.test') as client:
                self.assertEqual(200, (await client.get('/health')).status_code)
                path = '/api/v1/mock-automation/selected-order-evidence/missing/assessment'
                body = dict(credential_profile_id='absent-profile', credential_revision=1,
                    account_ref='11111111-1111-4111-8111-111111111111', binding_revision=1, packet_sha256='a' * 64)
                self.assertEqual(401, (await client.post(path, json=body)).status_code)
                self.assertEqual(403, (await client.post(path, json=body,
                    headers={'Authorization': 'Bearer query-fixture'})).status_code)
                response = await client.post(path, json=body, headers={'Authorization': 'Bearer admin-fixture'})
                self.assertEqual(503, response.status_code, response.text)
                self.assertEqual('SELECTED_RECOVERY_IDENTITY_OWNER_UNAVAILABLE', response.json()['detail'])
        self.assertEqual('completed', app.state.shutdown_status['state'])

    async def test_native_issuer_refuses_cursor_renaming_and_member_downgrade(self):
        source = await self.source()
        changed = deepcopy(source.packet)
        changed['source_cursor'] += 1
        with self.assertRaisesRegex(ValueError, 'native source cut'):
            source.owner.issue_selected_order_identity_proof(changed, encode_selected_order_evidence(changed)[1],
                credential_profile_id=source.proof['credential_profile_id'])
        changed = {key: value for key, value in source.packet.items()
            if key not in {'member_spec_id', 'member_admission_id', 'account_selection_id', 'anchor_spec_id'}}
        changed['version'] = 'selected-order-evidence/v1'
        with self.assertRaisesRegex(ValueError, 'discard native member'):
            source.owner.issue_selected_order_identity_proof(changed, encode_selected_order_evidence(changed)[1],
                credential_profile_id=source.proof['credential_profile_id'])

    async def test_amended_v2_original_and_child_share_raw_fill_id_without_losing_physical_identity(self):
        source = await self.source(amended=True)
        self.assertEqual('selected-order-evidence/v2', source.packet['version'])
        target = await self.target(source)
        response = await target.client.post(target.url, json=target.request)
        self.assertEqual(200, response.status_code, response.text)
        result = response.json()
        self.assertEqual('MATCH', result['status'], result['reasons'])
        self.assertEqual(2, len(result['target_broker_evidence']['orders']))
        self.assertEqual(['same-raw-late-id'] * 2, [order['fills'][0]['execution_id'] for order in result['target_broker_evidence']['orders']])
        [root, child] = target.broker.orders
        target.broker.orders = (root, replace(child, original_broker_order_id='unverified-parent'))
        result = (await target.client.post(target.url, json=target.request)).json()
        self.assertEqual('CONFLICT', result['status'])

    async def test_unresolved_native_amendment_and_aggregate_only_source_cannot_become_complete(self):
        unresolved = await self.source(amended=True, amendment_mode='unknown')
        target = await self.target(unresolved)
        before = deepcopy(unresolved.packet)
        result = (await target.client.post(target.url, json=target.request)).json()
        self.assertEqual('UNKNOWN', result['status'], result['reasons'])
        self.assertIn('NATIVE_AMENDMENT_LINEAGE_UNRESOLVED', result['reasons'])
        self.assertEqual(before, unresolved.packet)
        self.assertEqual(('UNKNOWN', 0),
            (unresolved.packet['order']['amendment']['status'], unresolved.packet['plan_position']['quantity']))

        detailed = await self.source(amended=True, amendment_mode='detailed')
        evidence = deepcopy(detailed.proof['broker_evidence'])
        for order in evidence['orders']:
            order['fills'], order['filled_quantity'] = [], 0
        from kiwoom_monitor.application.selected_order_recovery import broker_evidence_from_dict
        proof = detailed.owner.issue_selected_order_identity_proof(detailed.packet, detailed.digest,
            credential_profile_id=detailed.proof['credential_profile_id'],
            broker_evidence=broker_evidence_from_dict(evidence))
        target = await self.target(detailed)
        target.broker.orders = dated_orders(detailed.packet)
        result = (await target.client.post(target.url,
            json={**target.request, 'source_identity_proof': proof})).json()
        self.assertEqual('UNKNOWN', result['status'], result['reasons'])
        self.assertIn('SOURCE_NATIVE_AGGREGATE_NOT_COVERED', result['reasons'])
        self.assertIn('SOURCE_KNOWN_FILL_NOT_COVERED', result['reasons'])
