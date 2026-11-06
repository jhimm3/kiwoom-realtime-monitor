"""Closed native installation is separate from canonical ownership and management."""
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from scripts.preserve_selected_order_evidence import capture_selected_order_management_bundle
from kiwoom_monitor.application.selected_order_recovery import validate_management_closure
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.selected_order_bootstrap import ASSESSMENTS, INSTALLATIONS
from kiwoom_monitor.domain.selected_order_evidence import MANAGEMENT_VERSION, encode_selected_order_evidence
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
from tests.unit import test_selected_order_recovery as recovery_fixture
from tests.unit.test_selected_order_recovery import (
    dated_orders, FakeReadOnlyBroker, KEY, NOW, identity_owner,
)


class SelectedOrderBootstrapTests(unittest.IsolatedAsyncioTestCase):
    source, target = recovery_fixture.SelectedOrderRecoveryTests.source, recovery_fixture.SelectedOrderRecoveryTests.target

    def assert_empty_execution(self, store):
        with store.mock_automation_read_scope() as reader:
            for table in ('central_execution_intents', 'central_execution_events'):
                reader.execute('SELECT COUNT(*) FROM ' + table)
                self.assertEqual(0, reader.fetchone()[0])
        self.assertIsNone(store.load_mock_automation_control(self.origin))
        self.assertIsNone(store.load_document(INSTALLATIONS, self.origin, self.intent_id))

    async def prepare(self, *, detailed=False):
        source = await self.source(amended=True, amendment_mode='detailed' if detailed else 'accepted', management=True)
        target = await self.target(source)
        target.broker.orders = dated_orders(source.packet)
        self.origin, self.intent_id = source.packet['account_ref'], source.packet['order']['intent_id']
        return source, target, target.url.removesuffix('/assessment') + '/bootstrap'

    def current_read_runtime(self, target):
        from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
        from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime, ManualMockOrderGateway
        from kiwoom_monitor.domain.order_contract import AccountSnapshot
        from types import SimpleNamespace
        bundle = target.owner.bundle('target-profile')
        target.store._now_provider = lambda: bundle.clock[0]
        target.store._execution_wall_now = lambda: bundle.clock[0]
        execution = ExecutionRepository(target.store)
        transport = SimpleNamespace(submit=lambda *_args: self.fail('read runtime submitted'),
            cancel=lambda *_args: self.fail('read runtime cancelled'))
        runtime = ExecutionRuntime(OrderLifecycle(execution, transport, now_provider=lambda: bundle.clock[0]), execution,
            account_ref=bundle.account_ref, run_id='current-target-read-run', owner_token='test-owned-reader',
            now_provider=lambda: bundle.clock[0])
        runtime.start(new_orders_enabled=False)
        self.addCleanup(runtime.stop)
        execution.save_account_snapshot('mock', AccountSnapshot(bundle.account_ref, 100_000, 0, {}, bundle.clock[0]), bundle.clock[0])
        async def read_account():
            return AccountSnapshot(bundle.account_ref, 100_000, 0, {}, bundle.clock[0])
        bundle.runtime = runtime
        bundle.gateway = ManualMockOrderGateway(runtime, execution, read_account, now_provider=lambda: bundle.clock[0])
        self.addAsyncCleanup(bundle.gateway.close)
        context = dict(account_ref=runtime.account_ref, run_id=runtime.run_id, owner_token=runtime.owner_token)
        return runtime, context

    async def install_from_current_owner(self, source, target, context):
        from kiwoom_monitor.central_server.observation_frame_recovery import drain_thread_call
        async def accept(identity, evidence, current):
            return await drain_thread_call(target.store.install_selected_order_management, source.packet,
                identity=identity, source_proof=source.proof, broker_evidence=evidence, current_owner=current,
                current_target_runtime=context)
        request = target.request
        return await target.owner.verify_selected_order_identity(source.packet, source.digest, source.proof,
            credential_profile_id=request['credential_profile_id'], credential_revision=request['credential_revision'],
            account_ref=request['account_ref'], binding_revision=request['binding_revision'],
            broker_evidence_provider=target.broker.dated_provider, on_verified=accept)

    async def test_full_policy_export_and_native_install_preserve_ids_with_independent_target_cursor(self):
        source, target, url = await self.prepare(detailed=True)
        self.assertEqual(MANAGEMENT_VERSION, source.packet['version'])
        source_bytes = source.selection['source'].read_bytes()
        if isinstance(target.store, SQLiteQueryStore):
            with target.store._connection() as connection:
                connection.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('central_execution_events',100)")
        else:
            from kiwoom_monitor.central_server.postgres_access import DBWriterContext, owned_connection_scope
            context = DBWriterContext(writer_family='infrastructure.connection', writer_kind='test.selected_order_sequence',
                operation='test_target_sequence', access_mode='connection')
            with owned_connection_scope(target.store._connect, context) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT setval(pg_get_serial_sequence('central_execution_events','accepted_sequence'),100,true)")
        response = await target.client.post(url, json=target.request)
        self.assertEqual(200, response.status_code, response.text)
        receipt = response.json()['receipt']
        self.assertEqual((False, False, False, False), (receipt['order_authority'], receipt['management_ready'],
            receipt['runtime_started'], receipt['new_orders_enabled']))
        assessment = target.store.load_document(ASSESSMENTS, 'offline', receipt['assessment_id'])['document']
        self.assertEqual('MATCH', assessment['assessment']['status'])
        self.assertEqual(source.proof, assessment['source_identity_proof'])
        self.assertNotIn('account_token', str(response.json()))
        self.assertNotEqual(receipt['source_plan_fill_cursor'], receipt['target_plan_fill_cursor'])
        self.assertTrue(all(item['target_accepted_sequence'] > 100 for item in receipt['event_coordinates']))
        self.assertEqual({**source.packet['order'],
            'mock_plan_approval': source.packet['management_closure']['origin_approval_id']},
            target.store.load_execution_intent(self.intent_id))
        self.assertEqual([{key: value for key, value in event.items() if key != 'accepted_sequence'}
            for event in source.packet['events']], target.store.load_execution_events(self.intent_id))
        original = source.packet['management_closure']['source_plan_position']
        row = target.store.load_document('execution_mock_plan_positions', self.origin, original['plan_id'])['document']
        self.assertEqual({**original, 'fill_cursor': receipt['target_plan_fill_cursor']}, row)
        control = target.store.load_mock_automation_control(self.origin)
        self.assertEqual(('STOPPED', True), (control['desired_state'], control['entry_resume_required']))
        self.assertEqual(source_bytes, source.selection['source'].read_bytes())
        for item in source.packet['management_closure']['documents']:
            self.assertIsNone(target.store.load_document(item['collection'], item['owner'], item['key']))
        with target.store.mock_automation_read_scope() as reader:
            reader.execute('SELECT COUNT(*) FROM central_execution_runtime_leases')
            self.assertEqual(0, reader.fetchone()[0])
        record = ExecutionRepository(target.store).load(self.intent_id)
        self.assertEqual(source.packet['order']['plan']['policy_ref'], record.intent.plan.policy_ref)

    async def test_lost_commit_ack_replays_first_receipt_without_reinstalling_or_renewing_it(self):
        source, target, url = await self.prepare()
        store_type = type(target.store)
        native = store_type.install_selected_order_management
        committed = []
        def lose_ack(store, *args, **kwargs):
            committed.append(native(store, *args, **kwargs)['receipt'])
            raise ConnectionError('test lost native COMMIT ACK')
        with patch.object(store_type, 'install_selected_order_management', lose_ack):
            recovered = await target.client.post(url, json=target.request)
            self.assertEqual(200, recovered.status_code, recovered.text)
            self.assertEqual(1, len(committed))
            self.assertEqual({'receipt': committed[0], 'replayed': True}, recovered.json())
        first = target.store.load_document(INSTALLATIONS, self.origin, self.intent_id)['document']
        events = target.store.load_execution_events(self.intent_id)
        response = await target.client.post(url, json=target.request)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({'receipt': first, 'replayed': True}, response.json())
        self.assertEqual(events, target.store.load_execution_events(self.intent_id))
        self.assertEqual(source.digest, first['packet_sha256'])

    async def test_target_epoch_change_before_commit_rolls_back_every_native_installation_write(self):
        _, target, url = await self.prepare()
        store_type = type(target.store)
        native = store_type.install_selected_order_management
        checks = []
        def race(store, packet, **kwargs):
            owner_current = kwargs['current_owner']
            def changed():
                checks.append(1)
                if len(checks) == 2: target.owner._revisions['target-profile'] = 2
                return owner_current()
            return native(store, packet, **{**kwargs, 'current_owner': changed})
        with patch.object(store_type, 'install_selected_order_management', race):
            response = await target.client.post(url, json=target.request)
        self.assertEqual(409, response.status_code, response.text)
        self.assertIn('TARGET_EPOCH_CHANGED', response.json()['detail'])
        self.assertEqual(2, len(checks))
        self.assert_empty_execution(target.store)

    async def test_an_already_active_canonical_target_cannot_install_under_an_origin_alias(self):
        _, target, url = await self.prepare()
        binding = target.binding
        now = target.owner.bundle('target-profile').clock[0]
        self.assertTrue(target.store.acquire_execution_runtime('mock:' + binding.scope.account_ref, 'test-run:existing-owner',
            now.isoformat(), (now + timedelta(minutes=1)).isoformat()))
        response = await target.client.post(url, json=target.request)
        self.assertEqual(409, response.status_code, response.text)
        self.assertIn('EMPTY_INACTIVE_TARGET', response.json()['detail'])
        self.assert_empty_execution(target.store)

    async def test_explicit_server_owned_read_runtime_keeps_its_single_canonical_lease_and_no_new_authority(self):
        source, target, url = await self.prepare()
        runtime, context = self.current_read_runtime(target)
        with target.store.mock_automation_read_scope() as reader:
            reader.execute('SELECT owner_key,owner_token FROM central_execution_runtime_leases')
            original = reader.fetchall()
        response = await target.client.post(url, json=target.request)
        self.assertEqual(200, response.status_code, response.text)
        result = response.json()
        self.assertEqual((False, False), (result['receipt']['management_ready'], result['receipt']['order_authority']))
        with target.store.mock_automation_read_scope() as reader:
            reader.execute('SELECT owner_key,owner_token FROM central_execution_runtime_leases')
            self.assertEqual(original, reader.fetchall())
        self.assertNotIn(runtime.owner_token, str(result))
        self.assertFalse(runtime._new_orders_enabled)
        self.assertFalse(target.owner.bundle('target-profile').gateway._accepting)

    async def test_server_runtime_context_rejects_foreign_token_account_expired_lease_and_snapshot(self):
        source, target, _ = await self.prepare()
        runtime, context = self.current_read_runtime(target)
        for changed in ({**context, 'owner_token': 'foreign-owner'}, {**context, 'account_ref': source.packet['account_ref']}):
            with self.assertRaisesRegex(RuntimeError, 'TARGET_RUNTIME_CHANGED'):
                await self.install_from_current_owner(source, target, changed)
            self.assert_empty_execution(target.store)
        target.owner.bundle('target-profile').clock[0] += timedelta(minutes=2)
        with self.assertRaisesRegex(RuntimeError, 'TARGET_RUNTIME_CHANGED'):
            await self.install_from_current_owner(source, target, context)
        runtime.heartbeat()
        now = runtime.server_now()
        self.assertTrue(target.store.acquire_execution_runtime('mock:another-account', 'foreign-run:foreign-owner',
            now.isoformat(), (now + timedelta(minutes=1)).isoformat()))
        with self.assertRaisesRegex(RuntimeError, 'TARGET_RUNTIME_CHANGED'):
            await self.install_from_current_owner(source, target, context)
        self.assertTrue(target.store.release_execution_runtime('mock:another-account', 'foreign-run:foreign-owner'))
        target.store.save_execution_account_snapshot(dict(snapshot_id='foreign-read-snapshot', environment='mock',
            account_ref=source.packet['account_ref'], available_cash_won=100_000, reserved_open_buy_won=0,
            positions={}, as_of=now.isoformat(), received_at=now.isoformat()))
        with self.assertRaisesRegex(RuntimeError, 'EMPTY_INACTIVE_TARGET'):
            await self.install_from_current_owner(source, target, context)
        self.assert_empty_execution(target.store)

    async def test_reference_only_packet_and_incomplete_native_policy_closure_fail_closed(self):
        source = await self.source(amended=True)
        target = await self.target(source)
        self.origin, self.intent_id = source.packet['account_ref'], source.packet['order']['intent_id']
        url = target.url.removesuffix('/assessment') + '/bootstrap'
        response = await target.client.post(url, json=target.request)
        self.assertEqual(400, response.status_code, response.text)
        self.assertIn('policy closure', response.json()['detail'])
        self.assert_empty_execution(target.store)
        full = await self.source(amended=True, management=True)
        changed = deepcopy(full.packet)
        changed['management_closure']['documents'] = [item for item in changed['management_closure']['documents']
            if item['collection'] != 'execution_mock_automation_candidate_packages']
        encode_selected_order_evidence(changed)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            validate_management_closure(changed)
        with self.assertRaises(ValueError):
            full.owner.issue_selected_order_identity_proof(changed, encode_selected_order_evidence(changed)[1],
                credential_profile_id=full.proof['credential_profile_id'])

    async def test_source_owner_rechecks_closure_against_native_immutable_policy_documents(self):
        full, target, _ = await self.prepare()
        changed = deepcopy(full.packet)
        changed['management_closure']['source_plan_position']['source_test_injection'] = True
        with self.assertRaisesRegex(ValueError, 'closure changed'):
            full.owner.issue_selected_order_identity_proof(changed, encode_selected_order_evidence(changed)[1],
                credential_profile_id=full.proof['credential_profile_id'])
        self.assert_empty_execution(target.store)

    async def test_hidden_add_or_sell_intent_is_not_mistaken_for_a_complete_single_order_plan(self):
        full, target, _ = await self.prepare()
        extra = {**full.packet['order'], 'intent_id': 'unselected-plan-intent', 'state': 'FILLED'}
        with full.store._connection() as connection:
            connection.execute('INSERT INTO central_execution_intents(intent_id,run_id,environment,account_ref,state,'
                'broker_order_id,last_broker_as_of,created_at,updated_at,document_json) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (extra['intent_id'], extra['run_id'], extra['environment'], extra['account_ref'], extra['state'],
                    extra['broker_order_id'], extra['last_broker_as_of'], extra['created_at'], extra['updated_at'], json.dumps(extra)))
        with self.assertRaisesRegex(ValueError, 'complete plan ledger'):
            full.owner.issue_selected_order_identity_proof(full.packet, full.digest,
                credential_profile_id=full.proof['credential_profile_id'])
        self.assert_empty_execution(target.store)

    async def native_member_source(self, *, arm=False, whole_second_fill=False):
        from kiwoom_monitor.application.mock_automation_execution import emergency_stop_mock_automation
        from kiwoom_monitor.application.selected_order_recovery import DatedBrokerEvidence
        from kiwoom_monitor.domain.order_contract import AccountEnvironment
        from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import KiwoomAccountIdentityReader
        from tests.unit.test_mock_automation_parallel_strategies import first_parallel_buys
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / 'native-member.sqlite3'
        identity = await KiwoomAccountIdentityReader(FakeReadOnlyBroker(), environment=AccountEnvironment.MOCK,
            hmac_key=KEY, now_provider=lambda: NOW).verify()
        register = SQLiteQueryStore.register_account_identity
        def native_identity(store, value):
            return register(store, {**value, 'identity_fingerprint': identity.identity_fingerprint})
        with patch.object(SQLiteQueryStore, 'register_account_identity', native_identity):
            data, (_, close) = await first_parallel_buys(self, lambda: SQLiteQueryStore(path))
        if arm:
            data.clock[0] += timedelta(milliseconds=1)
            if whole_second_fill:
                # This external fake broker can report HHMMSS only. Choose the
                # clock before the actual fill; never rewrite a native event.
                data.clock[0] = data.clock[0].replace(microsecond=0) + timedelta(seconds=1)
            data.book.fill(close.broker_order_id, 1, 'first-close-owned-fill')
            await data.bundle.monitor.refresh_recovery()
            quote = dict(price_won=500, available_at=data.clock[0], input_ref='collector-trade:first-close-stop',
                durable=False, market_status='TRADABLE')
            data.episodes[1].observe_automation_price(close.intent.symbol, quote)
            notice = deepcopy(data.episodes[1]._protection_snapshot())
            self.assertEqual('PENDING', notice['state'])
            self.assertIn('stop_loss', notice['risk_trigger'])
            data.episodes[1]._save_checkpoint(force=True)
            data.clock[0] += timedelta(milliseconds=1)
            data.episodes[1].observe_automation_price(close.intent.symbol, {**quote,
                'price_won': 1200, 'available_at': data.clock[0], 'input_ref': 'collector-trade:close-recovered-price'})
            self.assertEqual(notice, data.episodes[1]._protection_snapshot())
            data.episodes[1]._save_checkpoint(force=True)
        emergency_stop_mock_automation(data.repository, data.runtime, account_ref=data.runtime.account_ref,
            spec_id=data.specs[0].spec_id, stopped_at=data.runtime.server_now(), reason='test-owned stopped export')
        await data.close_runner()
        await data.close_shadow()
        await data.close_gateway()
        await data.close_monitor()
        data.runtime.stop()
        from scripts.preserve_selected_order_evidence import capture_selected_order_evidence
        options = dict(source=path, source_instance='native-member-source', operation_id='native-close-policy',
            account_ref=data.runtime.account_ref, run_id=data.runtime.run_id, intent_id=close.intent.intent_id)
        original, _ = capture_selected_order_evidence(**options)
        profile = data.selection.credential_profile_id
        orders = dated_orders(original)
        evidence = DatedBrokerEvidence(data.runtime.account_ref, profile, 1, data.binding['binding_revision'],
            data.runtime.server_now(), data.runtime.server_now(), tuple(sorted({order.trading_date for order in orders})),
            'FULL', 'fake-source-cut', orders)
        owner = identity_owner(data.store, KEY, profile=profile)
        packet, digest, proof = capture_selected_order_management_bundle(**options,
            identity_owner=owner, credential_profile_id=profile, broker_evidence=evidence)
        spec, package, admission = validate_management_closure(packet)
        self.assertEqual((data.specs[1].spec_id, close.intent.plan.policy_ref), (spec.spec_id, packet['order']['plan']['policy_ref']))
        self.assertEqual('close_betting/v1', package.candidate_spec['family'])
        self.assertEqual(admission.admission_id, packet['member_admission_id'])
        self.assertNotEqual(packet['member_spec_id'], packet['anchor_spec_id'])
        self.assertEqual(digest, proof['packet_sha256'])
        from types import SimpleNamespace
        return SimpleNamespace(root=root, data=data, store=data.store, owner=owner, packet=packet, digest=digest,
            proof=proof, selection=options, evidence=evidence)

    async def test_actual_non_anchor_close_export_retains_its_own_policy_and_admission(self):
        full = await self.native_member_source()
        original_bytes = full.selection['source'].read_bytes()
        encoded, digest = encode_selected_order_evidence(full.packet)
        target = await self.target(full)
        target.broker.orders = dated_orders(full.packet)
        response = await target.client.post(target.url.removesuffix('/assessment') + '/bootstrap', json=target.request)
        self.assertEqual(200, response.status_code, response.text)
        original_ref = full.store.load_execution_intent(full.packet['order']['intent_id'])['mock_plan_approval']
        native = target.store.load_execution_intent(full.packet['order']['intent_id'])
        self.assertEqual(original_ref, full.packet['management_closure']['origin_approval_id'])
        self.assertEqual({**full.packet['order'], 'mock_plan_approval': original_ref,
            'mock_account_member_spec_id': full.packet['member_spec_id']}, native)
        repository = ExecutionRepository(target.store)
        record = repository.load(native['intent_id'])
        self.assertEqual(full.packet['member_spec_id'], record.member_spec_id)
        self.assertEqual(full.packet['order']['plan']['policy_ref'], record.intent.plan.policy_ref)
        events = repository.account_events('mock', full.packet['account_ref']).events
        self.assertEqual([event['event_id'] for event in full.packet['events']], [event.source_event_id for event in events])
        self.assertTrue(all(event.member_spec_id == full.packet['member_spec_id'] for event in events))
        self.assertEqual([{key: value for key, value in event.items() if key != 'accepted_sequence'}
            for event in full.packet['events']], list(repository.events(native['intent_id'])))
        self.assertEqual((encoded, digest), encode_selected_order_evidence(full.packet))
        self.assertEqual(original_bytes, full.selection['source'].read_bytes())
        for item in full.packet['management_closure']['documents']:
            self.assertIsNone(target.store.load_document(item['collection'], item['owner'], item['key']))
        self.assertFalse(response.json()['receipt']['order_authority'])

    async def test_first_pending_stop_checkpoint_and_exact_historical_risk_survive_export_and_cannot_be_omitted(self):
        full = await self.native_member_source(arm=True)
        packet = full.packet
        member, account = packet['member_spec_id'], packet['account_ref']
        values = {(item['collection'], item['owner'], item['key']): item['document']
            for item in packet['management_closure']['documents']}
        checkpoint_key = ('execution_mock_automation_runner_current', account, 'member:' + member)
        original = full.store.load_document(*checkpoint_key)['document']
        self.assertEqual(original, values[checkpoint_key])
        notice = original['protection_review']
        self.assertEqual('collector-trade:first-close-stop', notice['input_ref'])
        risk_key = ('execution_mock_automation_risk_snapshots', account, notice['risk_trigger']['snapshot_id'])
        self.assertEqual(full.store.load_document(*risk_key)['document'], values[risk_key])
        for missing in (checkpoint_key, risk_key):
            changed = deepcopy(packet)
            changed['management_closure']['documents'] = [item for item in changed['management_closure']['documents']
                if (item['collection'], item['owner'], item['key']) != missing]
            with self.assertRaises(ValueError):
                full.owner.issue_selected_order_identity_proof(changed, encode_selected_order_evidence(changed)[1],
                    credential_profile_id=full.proof['credential_profile_id'], broker_evidence=full.evidence)
        erased = deepcopy(packet)
        erased['management_closure']['documents'] = [item for item in erased['management_closure']['documents']
            if (item['collection'], item['owner'], item['key']) not in {checkpoint_key, risk_key}]
        with self.assertRaisesRegex(ValueError, 'checkpoint evidence'):
            full.owner.issue_selected_order_identity_proof(erased, encode_selected_order_evidence(erased)[1],
                credential_profile_id=full.proof['credential_profile_id'], broker_evidence=full.evidence)
        changed = deepcopy(packet)
        next(item['document'] for item in changed['management_closure']['documents']
            if (item['collection'], item['owner'], item['key']) == checkpoint_key)['protection_review']['input_ref'] = 'replaced-first-cue'
        with self.assertRaisesRegex(ValueError, 'native policy facts'):
            full.owner.issue_selected_order_identity_proof(changed, encode_selected_order_evidence(changed)[1],
                credential_profile_id=full.proof['credential_profile_id'], broker_evidence=full.evidence)
        # Native absence is retained as absence; it cannot become a complete
        # management closure by substituting the latest healthy Risk.
        with full.store._connection() as connection:
            connection.execute('DELETE FROM central_documents WHERE collection=? AND owner=? AND document_key=?', risk_key)
        missing_packet, missing_digest, missing_proof = capture_selected_order_management_bundle(**full.selection,
            identity_owner=full.owner, credential_profile_id=full.proof['credential_profile_id'], broker_evidence=full.evidence)
        self.assertEqual(notice, next(item['document']['protection_review'] for item in missing_packet['management_closure']['documents']
            if (item['collection'], item['owner'], item['key']) == checkpoint_key))
        self.assertEqual(missing_digest, missing_proof['packet_sha256'])
        with self.assertRaisesRegex(ValueError, 'checkpoint dependency is missing'):
            validate_management_closure(missing_packet)
