"""Native canonical lease races use the same selected-management cases on PostgreSQL."""
import unittest
from tests.unit import test_selected_management_native_lease as native_fixture


class SelectedManagementNativeLeasePostgresTests(unittest.IsolatedAsyncioTestCase):
    source = native_fixture.SelectedManagementNativeLeaseTests.source
    target = native_fixture.SelectedManagementNativeLeaseTests.target

    def setUp(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        self.target_store_factory = LeaderCurrentOrderManagementPostgresTests.factory(self)

    async def test_pending_installation_and_general_alias_cannot_grant_an_origin_lease(self):
        await native_fixture.SelectedManagementNativeLeaseTests.test_pending_installation_and_general_alias_cannot_grant_an_origin_lease(self)

    async def test_canonical_and_selected_contenders_share_one_lease_and_old_cleanup_cannot_delete_new_owner(self):
        await native_fixture.SelectedManagementNativeLeaseTests.test_canonical_and_selected_contenders_share_one_lease_and_old_cleanup_cannot_delete_new_owner(self)

    async def test_epoch_change_during_renewal_rolls_back_and_withdrawn_owner_can_only_release_its_exact_token(self):
        await native_fixture.SelectedManagementNativeLeaseTests.test_epoch_change_during_renewal_rolls_back_and_withdrawn_owner_can_only_release_its_exact_token(self)

    async def test_actual_terminal_zero_fill_unblocks_only_the_normal_target_and_late_fill_blocks_it_again(self):
        await native_fixture.SelectedManagementNativeLeaseTests.test_actual_terminal_zero_fill_unblocks_only_the_normal_target_and_late_fill_blocks_it_again(self)

    async def test_real_ready_acceptance_requires_current_running_for_commands_while_stopped_facts_continue(self):
        await native_fixture.verify_selected_ready_native_stop_authority(self)

    async def test_actual_cancel_response_after_new_current_stop_remains_durable_without_a_new_command(self):
        await native_fixture.verify_selected_cancel_response_after_stop(self)

    async def test_selected_leader_historical_fill_cut_keeps_source_coordinates_after_late_native_fill_and_stop(self):
        await native_fixture.SelectedManagementNativeLeaseTests.test_selected_leader_historical_fill_cut_keeps_source_coordinates_after_late_native_fill_and_stop(self)

    async def test_stale_target_command_waits_for_selected_current_then_rejects_while_actual_fill_commits(self):
        from contextlib import closing
        from concurrent.futures import ThreadPoolExecutor
        from datetime import timedelta
        from threading import Event, local
        from time import monotonic, sleep
        from unittest.mock import patch
        from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
        from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
        from kiwoom_monitor.central_server.postgres_access import ObservedDBCursor
        from kiwoom_monitor.domain.order_contract import BrokerFill, BrokerOrderSnapshot, OrderIntent, OrderSide, OrderState, OrderType
        from kiwoom_monitor.domain.selected_order_evidence import selected_order_management_context_from_dict
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository, execution_broker_legs
        from tests.unit.test_selected_order_management import prepared_target, prepare_current_management

        data = await prepared_target(self)
        prepared, current_owner = await prepare_current_management(data)
        context = selected_order_management_context_from_dict(prepared['receipt']['context'])
        repository = ExecutionRepository(data.target.store)
        repository.bind_selected_management_context(context, current_owner=current_owner)
        active = ExecutionRuntime(OrderLifecycle(repository, data.transport, now_provider=lambda: data.bundle.clock[0]),
            repository, account_ref=context.account_ref, run_id=context.execution_run_id,
            owner_token='fresh-original-management-owner', now_provider=lambda: data.bundle.clock[0])
        active.start(new_orders_enabled=False)
        self.addCleanup(active.stop)
        original = repository.load(context.intent_id)
        leg = next(leg for leg in execution_broker_legs(original) if leg.remaining_quantity > 0)
        data.bundle.clock[0] += timedelta(milliseconds=1)
        now = data.bundle.clock[0]
        detail = BrokerFill('selected-current-lock-actual-fill', 1, leg.limit_price, now)
        snapshot = BrokerOrderSnapshot(leg.broker_order_id, context.account_ref, original.intent.symbol,
            OrderState.FILLED, leg.broker_reported_filled_quantity + 1, 0, now, (detail,))
        stale_intent = OrderIntent('stale-target-command-must-not-enter', data.runtime.run_id, 'explicit-native-check',
            context.canonical_account_ref, 'mock', original.intent.symbol, 'KRX', OrderSide.BUY, 1,
            OrderType.LIMIT, leg.limit_price, now, now+timedelta(seconds=60), 'explicit-native-test/v1')
        selected_locked, stale_started, release = Event(), Event(), Event()
        roles, pids, statements = local(), {}, {'selected': [], 'stale': []}
        original_run = ObservedDBCursor._run

        def observe(cursor, method, operation, args, kwargs):
            role = getattr(roles, 'role', None)
            statement = ' '.join(str(args[0]).split())
            current_read = (statement.startswith('SELECT document_json FROM central_documents')
                and statement.endswith('FOR UPDATE') and len(args) > 1
                and args[1][0] == 'execution_selected_order_management_current')
            if role is not None:
                statements[role].append(statement)
            if role == 'stale' and current_read:
                pids['stale'] = cursor._owner.backend_pid
                stale_started.set()
            result = original_run(cursor, method, operation, args, kwargs)
            if role == 'selected' and current_read and not selected_locked.is_set():
                pids['selected'] = cursor._owner.backend_pid
                selected_locked.set()
                if not release.wait(10):
                    raise TimeoutError('selected CURRENT native lock gate timed out')
            return result

        def selected_fill():
            roles.role = 'selected'
            return active.reconcile(context.intent_id, snapshot)

        def stale_command():
            roles.role = 'stale'
            try:
                data.runtime._lifecycle.queue(stale_intent)
            except RuntimeError as error:
                return str(error)
            self.fail('an old canonical token queued a new command')

        with patch.object(ObservedDBCursor, '_run', new=observe), ThreadPoolExecutor(max_workers=2) as workers:
            fill = workers.submit(selected_fill)
            stale = None
            try:
                self.assertTrue(selected_locked.wait(10))
                stale = workers.submit(stale_command)
                self.assertTrue(stale_started.wait(10))
                wait = None
                with closing(data.target.store._connect()) as probe:
                    probe.autocommit = True
                    deadline = monotonic() + 10
                    while wait is None and monotonic() < deadline:
                        if stale.done():
                            self.fail('stale command did not reach the controlled native CURRENT row wait')
                        wait = probe.execute('SELECT wait_event_type,query FROM pg_stat_activity '
                            'WHERE pid=%s AND %s=ANY(pg_blocking_pids(pid))',
                            (pids['stale'], pids['selected'])).fetchone()
                        if wait is None: sleep(.01)
                self.assertIsNotNone(wait)
                self.assertEqual('Lock', wait[0])
                self.assertIn('central_documents', wait[1])
                self.assertIn('FOR UPDATE', wait[1])
            finally:
                release.set()
            fill.result(20)
            self.assertIsNotNone(stale)
            self.assertRegex(stale.result(20), 'SELECTED_MANAGEMENT_CONTEXT_REQUIRED|EXECUTION_OWNERSHIP_LOST')
        restored = repository.load(context.intent_id)
        self.assertEqual(original.detailed_filled_quantity + 1, restored.detailed_filled_quantity)
        self.assertIn(detail.execution_id, restored.fill_ids)
        self.assertIsNone(data.repository.load(stale_intent.intent_id))
        self.assertEqual([], data.transport.calls)
