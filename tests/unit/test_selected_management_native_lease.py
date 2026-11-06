"""Native selected-management canonical collisions and non-authorizing cleanup."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
import unittest

from tests.unit.test_selected_order_management import prepared_target, prepare_current_management
from tests.unit import test_selected_order_recovery as recovery_fixture


def lease_rows(store):
    with store.mock_automation_read_scope() as cursor:
        cursor.execute('SELECT owner_key,owner_token,lease_expires_at FROM central_execution_runtime_leases ORDER BY owner_key')
        return tuple(tuple(row) for row in cursor.fetchall())


async def verify_selected_ready_native_stop_authority(case):
    """Use real D acceptance, then test only the native owner command/fact boundary."""
    from contextlib import closing
    from dataclasses import replace
    from kiwoom_monitor.central_server.database import SQLiteQueryStore
    from kiwoom_monitor.central_server.database_execution import _require_execution_ownership
    from kiwoom_monitor.application.mock_automation_admission import MockAutomationDesiredState
    from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
    from tests.unit.test_selected_order_management import actual_management_target
    data = await actual_management_target(case)
    source_bytes = data.source.selection['source'].read_bytes()
    url = data.target.url.replace('/assessment', '/prepare-management')
    prepared = await data.target.client.post(url, json=data.request)
    case.assertEqual(200, prepared.status_code, prepared.text)
    bundle = data.target.owner.bundle('target-profile')
    data.book_context.runtime, data.book_context.execution = bundle.runtime, bundle.repository
    data.clock[0] += timedelta(milliseconds=1)
    symbol = data.source.packet['order']['symbol']
    data.quotes[symbol] = dict(price_won=1200, available_at=data.clock[0],
        input_ref='collector-trade:selected-native-current-authority', durable=False, market_status='TRADABLE')
    accepted = await data.target.client.post(url.replace('/prepare-management', '/accept-management'),
        json={**data.request, 'expected_control_revision': 1})
    case.assertEqual(200, accepted.status_code, accepted.text)
    case.assertEqual('MANAGEMENT_READY', accepted.json()['receipt']['status'])
    runner = data.supervisor._runners[bundle.account_ref]
    await runner.close()  # Join the real actor before the isolated native boundary probe.
    forward = ForwardEvaluationRepository(data.target.store)
    control = forward.load_mock_automation_control(bundle.account_ref)
    case.assertEqual((MockAutomationDesiredState.RUNNING, True),
        (control.desired_state, control.entry_resume_required))
    raw = data.target.store.load_execution_intent(data.source.packet['order']['intent_id'])
    p = '?' if isinstance(data.target.store, SQLiteQueryStore) else '%s'

    def native_owner(event_kind):
        bundle.repository.bind_automation_control(spec_id=control.active_spec_id, control_revision=control.control_revision)
        options = bundle.repository._write_options()
        try:
            with closing(data.target.store._connect()) as connection:
                try:
                    if p == '?': connection.execute('BEGIN IMMEDIATE')
                    with closing(connection.cursor()) as cursor:
                        return _require_execution_ownership(cursor, options['ownership'], raw, p,
                            now_provider=lambda: data.clock[0], event_kind=event_kind)
                finally:
                    connection.rollback()
        finally:
            bundle.repository.clear_automation_control()

    case.assertEqual(raw['intent_id'], native_owner('CANCEL_STARTED')['intent_id'])
    data.clock[0] += timedelta(milliseconds=1)
    stopped = replace(control, desired_state=MockAutomationDesiredState.STOPPED,
        control_revision=control.control_revision+1, changed_at=data.clock[0],
        reason='explicit current STOP after accepted selected management')
    case.assertTrue(forward.save_mock_automation_control(stopped, expected_revision=control.control_revision))
    control = stopped  # A matching newer revision cannot substitute for RUNNING acceptance.
    with case.assertRaisesRegex(RuntimeError, 'SELECTED_MANAGEMENT_CONTROL_CHANGED'):
        native_owner('CANCEL_STARTED')
    case.assertEqual(raw['intent_id'], native_owner('BROKER_RECONCILED')['intent_id'])
    bundle.runtime.heartbeat()
    before = tuple(data.book.mutations)
    await bundle.monitor.refresh_recovery()
    case.assertEqual(before, tuple(data.book.mutations))
    case.assertEqual(source_bytes, data.source.selection['source'].read_bytes())


async def verify_selected_cancel_response_after_stop(case):
    """An actual managed CANCEL may finish after a newer explicit STOP."""
    import asyncio
    from dataclasses import replace
    from threading import Event
    from unittest.mock import patch
    from kiwoom_monitor.application.mock_automation_admission import MockAutomationDesiredState
    from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
    from tests.unit.test_selected_order_management import actual_management_target

    data = await actual_management_target(case)
    url = data.target.url.replace('/assessment', '/prepare-management')
    prepared = await data.target.client.post(url, json=data.request)
    case.assertEqual(200, prepared.status_code, prepared.text)
    bundle = data.target.owner.bundle('target-profile')
    data.book_context.runtime, data.book_context.execution = bundle.runtime, bundle.repository
    data.clock[0] += timedelta(milliseconds=1)
    symbol = data.source.packet['order']['symbol']
    data.quotes[symbol] = dict(price_won=1200, available_at=data.clock[0],
        input_ref='collector-trade:selected-cancel-stop-response', durable=False, market_status='TRADABLE')
    forward = ForwardEvaluationRepository(data.target.store)
    cancelled, stops = Event(), []
    cancel = data.transport.cancel

    def stop_after_actual_cancel(intent, broker_order_id, quantity=0):
        result = cancel(intent, broker_order_id, quantity)
        current = forward.load_mock_automation_control(bundle.account_ref)
        data.clock[0] += timedelta(milliseconds=1)
        stopped = replace(current, desired_state=MockAutomationDesiredState.STOPPED,
            control_revision=current.control_revision+1, changed_at=data.clock[0],
            reason='explicit STOP while actual selected cancellation response is in flight')
        case.assertTrue(forward.save_mock_automation_control(stopped, expected_revision=current.control_revision))
        stops.append(stopped)
        cancelled.set()
        return result

    with patch.object(data.transport, 'cancel', side_effect=stop_after_actual_cancel):
        accepted = await data.target.client.post(url.replace('/prepare-management', '/accept-management'),
            json={**data.request, 'expected_control_revision': 1})
        case.assertEqual(200, accepted.status_code, accepted.text)
        case.assertEqual('MANAGEMENT_READY', accepted.json()['receipt']['status'])
        runner = data.supervisor._runners[bundle.account_ref]
        try:
            case.assertTrue(await asyncio.wait_for(asyncio.to_thread(cancelled.wait, 10), timeout=15))
        finally:
            await runner.close()
    case.assertEqual(1, len(stops))
    events = bundle.repository.events(data.source.packet['order']['intent_id'])
    case.assertEqual(1, sum(event['event_type'] == 'CANCEL_STARTED' for event in events))
    case.assertEqual(1, sum(event['event_type'] == 'CANCEL_ACCEPTED' for event in events))
    case.assertEqual(1, len(data.book.mutations))
    case.assertEqual('CANCEL', data.book.mutations[0][0])
    case.assertEqual(stops[0], forward.load_mock_automation_control(bundle.account_ref))
    bundle.runtime.heartbeat()
    await bundle.monitor.refresh_recovery()
    case.assertEqual(1, len(data.book.mutations))


class SelectedManagementNativeLeaseTests(unittest.IsolatedAsyncioTestCase):
    source, target = recovery_fixture.SelectedOrderRecoveryTests.source, recovery_fixture.SelectedOrderRecoveryTests.target

    async def test_real_ready_acceptance_requires_current_running_for_commands_while_stopped_facts_continue(self):
        await verify_selected_ready_native_stop_authority(self)

    async def test_actual_cancel_response_after_new_current_stop_remains_durable_without_a_new_command(self):
        await verify_selected_cancel_response_after_stop(self)

    async def test_selected_leader_historical_fill_cut_keeps_source_coordinates_after_late_native_fill_and_stop(self):
        from contextlib import closing
        from dataclasses import replace
        from unittest.mock import patch
        from kiwoom_monitor.application.mock_automation_admission import MockAutomationDesiredState
        from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.central_server.database_execution import (
            _leader_native_risk_high_watermark, _leader_plan_ledger, _native_execution_scope,
            _plan_document, _require_execution_ownership)
        from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
        from tests.unit.test_selected_leader_management import selected_leader_target

        ordinary_target = self.target
        async def independent_event_allocator(source, **kwargs):
            target = await ordinary_target(source, **kwargs)
            # Simulate an independently used target allocator, as the existing
            # B round-trip test does. No event, Risk or source cursor is changed.
            if isinstance(target.store, SQLiteQueryStore):
                with target.store._connection() as connection:
                    connection.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('central_execution_events',100)")
            else:
                from kiwoom_monitor.central_server.postgres_access import DBWriterContext, owned_connection_scope
                writer = DBWriterContext(writer_family='infrastructure.connection', writer_kind='test.selected_leader_sequence',
                    operation='test_target_sequence', access_mode='connection')
                with owned_connection_scope(target.store._connect, writer) as connection, connection.cursor() as cursor:
                    cursor.execute("SELECT setval(pg_get_serial_sequence('central_execution_events','accepted_sequence'),100,true)")
            return target
        with patch.object(self, 'target', side_effect=independent_event_allocator):
            data = await selected_leader_target(self)
        await data.runner.close()
        data.bundle.monitor.set_automation_execution_inputs_enabled(True, request_recovery=False)
        self.assertEqual([], data.book.mutations)
        bundle, context, store = data.bundle, data.management_context, data.target.store
        p = '?' if isinstance(store, SQLiteQueryStore) else '%s'
        original_notice = data.source.source_notice
        risk_id = original_notice['planned_leader_stop']['risk_snapshot_id']
        original_risk_doc = None

        def inspect(event_kind='BROKER_RECONCILED'):
            nonlocal original_risk_doc
            options = bundle.repository._write_options()
            raw = store.load_execution_intent(context.intent_id)
            with closing(store._connect()) as connection:
                try:
                    if p == '?': connection.execute('BEGIN IMMEDIATE')
                    with closing(connection.cursor()) as cursor:
                        facts = {}
                        _require_execution_ownership(cursor, options['ownership'], raw, p,
                            now_provider=lambda: data.clock[0], event_kind=event_kind, control_facts=facts)
                        ownership = _native_execution_scope(options['ownership'], facts)
                        management = ownership['_native_selected_management_context']
                        risk_doc = _plan_document(cursor, p, 'execution_mock_automation_risk_snapshots',
                            context.account_ref, risk_id, selected_management=management)
                        if original_risk_doc is None: original_risk_doc = risk_doc
                        self.assertEqual(original_risk_doc, risk_doc)
                        original_risk = mock_automation_risk_snapshot_from_dict(risk_doc)
                        self.assertEqual(data.source.source_spec.binding_revision, original_risk.binding_revision)
                        self.assertNotEqual(context.binding_revision, original_risk.binding_revision)
                        orders, pages = _leader_plan_ledger(cursor, p, context.account_ref, context.execution_run_id,
                            raw['plan']['plan_id'])
                        events = [row for page in pages.values() for row in page]
                        boundary = _leader_native_risk_high_watermark(ownership, original_risk, events)
                        historical = sum((1 if orders[identity]['side'] == 'BUY' else -1)*event['quantity']
                            for identity, page in pages.items() for sequence, event in page
                            if event['event_type'] == 'FILL' and sequence <= boundary)
                        self.assertGreater(boundary, 100)
                        self.assertNotEqual(original_risk.execution_event_high_watermark, boundary)
                        self.assertEqual(2, historical)
                        return boundary
                finally:
                    connection.rollback()

        boundary = inspect()
        original = bundle.repository.load(context.intent_id)
        data.clock[0] += timedelta(milliseconds=1)
        data.book.fill(original.broker_order_id, 1, 'selected-leader-late-native-detail')
        await bundle.monitor.refresh_recovery()
        self.assertEqual(3, bundle.repository.load(context.intent_id).detailed_filled_quantity)
        self.assertEqual(boundary, inspect())
        self.assertEqual(3, dict(bundle.monitor.latest_automation_execution_cut['risk'].owned_for_spec(context.spec_id))[original.intent.symbol])
        forward = ForwardEvaluationRepository(store)
        control = forward.load_mock_automation_control(context.account_ref)
        data.clock[0] += timedelta(milliseconds=1)
        stopped = replace(control, desired_state=MockAutomationDesiredState.STOPPED,
            control_revision=control.control_revision+1, changed_at=data.clock[0],
            reason='explicit current STOP after selected Leader late detailed fill')
        self.assertTrue(forward.save_mock_automation_control(stopped, expected_revision=control.control_revision))
        bundle.repository.bind_automation_control(spec_id=context.spec_id, control_revision=stopped.control_revision)
        try:
            with self.assertRaisesRegex(RuntimeError, 'SELECTED_MANAGEMENT_CONTROL_CHANGED'):
                inspect('CANCEL_STARTED')
            self.assertEqual(boundary, inspect())
        finally:
            bundle.repository.clear_automation_control()
        self.assertEqual(data.source_bytes, data.source.selection['source'].read_bytes())
        self.assertEqual([], data.book.mutations)

    async def test_pending_installation_and_general_alias_cannot_grant_an_origin_lease(self):
        from kiwoom_monitor.domain.order_contract import OrderIntent, OrderSide, OrderType
        data = await prepared_target(self)
        store, binding = data.target.store, data.target.binding
        now = data.bundle.clock[0]
        origin, canonical = data.source.packet['account_ref'], binding.scope.account_ref
        before = lease_rows(store)
        pending_buy = OrderIntent('bare-canonical-pending-buy', data.runtime.run_id, 'explicit-native-check', canonical,
            'mock', data.source.packet['order']['symbol'], 'KRX', OrderSide.BUY, 1, OrderType.LIMIT,
            100, now, now+timedelta(seconds=60), 'explicit-native-test/v1')
        with self.assertRaisesRegex(RuntimeError, 'SELECTED_MANAGEMENT_CONTEXT_REQUIRED'):
            data.runtime._lifecycle.queue(pending_buy)
        self.assertFalse(store.acquire_execution_runtime('mock:' + origin,
            data.source.packet['run_id']+':bare-pending-origin', now.isoformat(), (now+timedelta(seconds=60)).isoformat()))
        alias = '11111111-2222-4333-8444-555555555555'
        declaration = dict(origin_account_ref=alias, canonical_account_ref=canonical,
            broker='kiwoom', environment='mock', credential_profile_id=binding.credential_profile_id,
            binding_revision=binding.binding_revision, verified_at=binding.verified_at.isoformat(),
            verification_method='ka00001')
        # An unrelated origin's live lease cannot acquire an alias to an
        # already owned target, even if both current account proofs are known.
        token = 'unrelated-origin-run:independent-owner'
        self.assertTrue(store.acquire_execution_runtime('mock:' + alias, token,
            now.isoformat(), (now+timedelta(seconds=60)).isoformat()))
        with self.assertRaisesRegex(RuntimeError, 'ACCOUNT_ALIAS_EXECUTION_OWNER_ACTIVE'):
            store.register_account_scope_alias(declaration)
        self.assertTrue(store.release_execution_runtime('mock:' + alias, token))
        store.register_account_scope_alias(declaration)
        self.assertFalse(store.acquire_execution_runtime('mock:' + alias, token,
            now.isoformat(), (now+timedelta(seconds=60)).isoformat()))
        self.assertEqual(before, lease_rows(store))
        self.assertEqual([], data.transport.calls)

    async def test_actual_terminal_zero_fill_unblocks_only_the_normal_target_and_late_fill_blocks_it_again(self):
        from contextlib import closing
        from types import SimpleNamespace
        from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.central_server.database_execution import _selected_target_management_pending
        from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
        from kiwoom_monitor.domain.order_contract import BrokerFill, BrokerOrderSnapshot, OrderState
        from kiwoom_monitor.domain.selected_order_evidence import selected_order_management_context_from_dict
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository, execution_broker_legs
        from tests.unit.test_selected_order_bootstrap import SelectedOrderBootstrapTests
        from tests.unit.test_mock_automation_specification import _FakeBrokerTransport
        source, target, _ = await SelectedOrderBootstrapTests.prepare(self, detailed=False)
        runtime, runtime_context = SelectedOrderBootstrapTests.current_read_runtime(self, target)
        installed = await SelectedOrderBootstrapTests.install_from_current_owner(self, source, target, runtime_context)
        self.assertFalse(installed['receipt']['order_authority'])
        bundle = target.owner.bundle('target-profile')
        from tests.unit.test_selected_order_management import apply_current_settings
        apply_current_settings(target, bundle.clock)
        transport = _FakeBrokerTransport(lambda: bundle.clock[0])
        data = SimpleNamespace(source=source, target=target, bundle=bundle, runtime=runtime, transport=transport)
        prepared, current_owner = await prepare_current_management(data)
        context = selected_order_management_context_from_dict(prepared['receipt']['context'])
        token = context.execution_run_id + ':fresh-original-management-owner'
        def manager():
            repository = ExecutionRepository(target.store)
            repository.bind_selected_management_context(context, current_owner=current_owner)
            value = ExecutionRuntime(OrderLifecycle(repository, transport, now_provider=lambda: bundle.clock[0]),
                repository, account_ref=context.account_ref, run_id=context.execution_run_id,
                owner_token='fresh-original-management-owner', now_provider=lambda: bundle.clock[0])
            value.start(new_orders_enabled=False)
            self.addCleanup(value.stop)
            return value, repository
        active, repository = manager()
        p = '?' if isinstance(target.store, SQLiteQueryStore) else '%s'
        ordinary_token = 'ordinary-after-flat:explicit-new-owner'
        def pending():
            # This is a claim validator with row locks, so inspect it within
            # the same read/write transaction kind as its actual native caller.
            with closing(target.store._connect()) as connection:
                try:
                    if p == '?': connection.execute('BEGIN IMMEDIATE')
                    with closing(connection.cursor()) as cursor:
                        return _selected_target_management_pending(cursor, p, context.canonical_account_ref, ordinary_token)
                finally:
                    connection.rollback()
        self.assertTrue(pending(), 'zero owned quantity cannot hide working physical BUYs')
        original = repository.load(context.intent_id)
        self.assertEqual(0, original.detailed_filled_quantity)
        for leg in execution_broker_legs(original):
            bundle.clock[0] += timedelta(milliseconds=1)
            active.reconcile(context.intent_id, BrokerOrderSnapshot(leg.broker_order_id, context.account_ref,
                original.intent.symbol, OrderState.CANCELLED, 0, 0, bundle.clock[0], ()))
        self.assertTrue(active.stop())
        self.assertFalse(pending(), 'immutable installation alone cannot retain a flat-account lock')
        self.assertTrue(target.store.acquire_execution_runtime('mock:' + context.canonical_account_ref,
            ordinary_token, bundle.clock[0].isoformat(), (bundle.clock[0]+timedelta(seconds=60)).isoformat()))
        self.assertTrue(target.store.release_execution_runtime('mock:' + context.canonical_account_ref, ordinary_token))
        # A later broker detail is reconciled on the original physical order;
        # it restores pending management without moving facts into a new plan.
        active, repository = manager()
        leg = execution_broker_legs(original)[0]
        bundle.clock[0] += timedelta(milliseconds=1)
        detail = BrokerFill('actual-late-after-cancel', 1, leg.limit_price, bundle.clock[0])
        active.reconcile(context.intent_id, BrokerOrderSnapshot(leg.broker_order_id, context.account_ref,
            original.intent.symbol, OrderState.CANCELLED, 1, 0, bundle.clock[0], (detail,)))
        self.assertTrue(active.stop())
        self.assertTrue(pending())
        self.assertEqual(1, repository.load_plan_position(context.account_ref, context.execution_run_id,
            original.intent.symbol, original.intent.plan).quantity)
        self.assertEqual([], transport.calls)

    async def test_canonical_and_selected_contenders_share_one_lease_and_old_cleanup_cannot_delete_new_owner(self):
        from kiwoom_monitor.domain.selected_order_evidence import selected_order_management_context_from_dict
        data = await prepared_target(self)
        result, current_owner = await prepare_current_management(data)
        context = selected_order_management_context_from_dict(result['receipt']['context'])
        store, now = data.target.store, data.bundle.clock[0]
        selected_token = context.execution_run_id + ':fresh-original-management-owner'
        kwargs = dict(selected_management_context=context.to_dict(), selected_management_owner=current_owner)
        self.assertTrue(store.release_execution_runtime('mock:' + context.account_ref, selected_token, **kwargs))
        barrier = Barrier(2)
        ordinary_token = 'canonical-other-run:new-independent-canonical-owner'
        def selected():
            barrier.wait(10)
            return store.acquire_execution_runtime('mock:' + context.account_ref, selected_token,
                now.isoformat(), (now+timedelta(seconds=60)).isoformat(), **kwargs)
        def ordinary():
            barrier.wait(10)
            return store.acquire_execution_runtime('mock:' + context.canonical_account_ref, ordinary_token,
                now.isoformat(), (now+timedelta(seconds=60)).isoformat())
        with ThreadPoolExecutor(max_workers=2) as workers:
            a, b = workers.submit(selected), workers.submit(ordinary)
            selected_won, ordinary_won = a.result(20), b.result(20)
        self.assertEqual(1, int(selected_won)+int(ordinary_won))
        self.assertEqual(1, len(lease_rows(store)))
        if selected_won:
            self.assertTrue(store.release_execution_runtime('mock:' + context.account_ref, selected_token, **kwargs))
            self.assertTrue(store.acquire_execution_runtime('mock:' + context.canonical_account_ref, ordinary_token,
                now.isoformat(), (now+timedelta(seconds=60)).isoformat()))
        before = lease_rows(store)
        self.assertFalse(store.release_execution_runtime('mock:' + context.account_ref, selected_token, **kwargs))
        self.assertFalse(store.acquire_execution_runtime('mock:' + context.account_ref, selected_token,
            now.isoformat(), (now+timedelta(seconds=60)).isoformat(), **kwargs))
        self.assertEqual(before, lease_rows(store))
        self.assertEqual(('mock:' + context.canonical_account_ref, ordinary_token), before[0][:2])
        self.assertTrue(store.release_execution_runtime('mock:' + context.canonical_account_ref, ordinary_token))
        self.assertEqual([], data.transport.calls)

    async def test_epoch_change_during_renewal_rolls_back_and_withdrawn_owner_can_only_release_its_exact_token(self):
        from kiwoom_monitor.domain.selected_order_evidence import selected_order_management_context_from_dict
        data = await prepared_target(self)
        result, current_owner = await prepare_current_management(data)
        context = selected_order_management_context_from_dict(result['receipt']['context'])
        store, now = data.target.store, data.bundle.clock[0]
        token = context.execution_run_id + ':fresh-original-management-owner'
        before, calls = lease_rows(store), []
        def changed():
            calls.append(1)
            if len(calls) == 2:
                data.target.owner._revisions[context.credential_profile_id] += 1
            return current_owner()
        kwargs = dict(selected_management_context=context.to_dict(), selected_management_owner=changed)
        with self.assertRaisesRegex(RuntimeError, 'SELECTED_MANAGEMENT_OWNER_CHANGED'):
            store.acquire_execution_runtime('mock:' + context.account_ref, token, now.isoformat(),
                (now+timedelta(seconds=120)).isoformat(), **kwargs)
        self.assertEqual(before, lease_rows(store))
        with self.assertRaisesRegex(RuntimeError, 'SELECTED_MANAGEMENT_OWNER_CHANGED'):
            store.acquire_execution_runtime('mock:' + context.account_ref, token, now.isoformat(),
                (now+timedelta(seconds=120)).isoformat(), **kwargs)
        # Cleanup verifies the immutable preparation and exact token. It must
        # still work after withdrawal without granting any operation authority.
        self.assertTrue(store.release_execution_runtime('mock:' + context.account_ref, token,
            selected_management_context=context.to_dict(), selected_management_owner=lambda: False))
        self.assertEqual((), lease_rows(store))
        self.assertEqual([], data.transport.calls)
