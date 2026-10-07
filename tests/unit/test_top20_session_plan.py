"""Native source closure plus shared peer execution, without PostgreSQL."""
import asyncio
import copy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.diagnostic_replay_baseline import ReplayDatabaseLease
from kiwoom_monitor.central_server.diagnostic_replay_runtime import ReplayRuntimeScope
from kiwoom_monitor.central_server.diagnostic_replay_contract import InputRejected, validate_operation
from kiwoom_monitor.central_server.diagnostic_rest_input import RequestTapeClient, read_request_tape
from kiwoom_monitor.central_server.diagnostic_top20_execution import (
    build_top20_session, execute_top20_session, prepare_top20_source_inputs,
)
from kiwoom_monitor.central_server.diagnostic_top20_experiment import (
    compile_top20_session_plan, execute_owned_top20_fixture,
)
from kiwoom_monitor.central_server.diagnostic_top20_lifecycle_input import SubscriptionTape, read_realtime_tape
from kiwoom_monitor.central_server.diagnostic_top20_seed import Top20FixtureClock
from tests.top20_native_fixture import capture_native_top20_fixture


ORIGIN = datetime.fromisoformat('2026-10-06T09:30:01+09:00')


class Top20SessionPlanTests(unittest.IsolatedAsyncioTestCase):
    def test_account_cohort_boundary_allows_only_date_scoped_read_arguments(self):
        arguments = {'collection': 'account_entry_symbols_daily', 'owner': '2026-10-06', 'limit': 5000}
        self.assertEqual('top20', validate_operation('load_documents', arguments))
        for method in ('load_document', 'upsert_documents', 'replace_documents'):
            with self.assertRaises(InputRejected):
                validate_operation(method, arguments)
        for key, value in (('owner', 'account-id'), ('owner', '2026-99-99'), ('limit', 5001), ('offset', 1)):
            with self.assertRaises(InputRejected):
                validate_operation('load_documents', {**arguments, key: value})

    def options(self, fixture):
        return {'component': fixture['component'], 'source_hub_component': fixture['hub'],
                'source_collector_component': fixture['collector'], 'end_seconds': 20}

    async def test_real_source_closes_descendants_keeps_same_collection_peer_and_masks_without_seeding(self):
        with tempfile.TemporaryDirectory() as directory:
            stores = []
            def store(name):
                value = SQLiteQueryStore(Path(directory) / (name+'.sqlite3'))
                value.initialize()
                stores.append(value)
                return value
            try:
                fixture = await capture_native_top20_fixture(store('source'), ORIGIN,
                    capture_store=True, capture_seconds=20, include_peer=True)
                options = self.options(fixture)
                plan = compile_top20_session_plan(fixture['events'], fixture['manifest'], **options)
                starts = {row['operation_id']: row for row in fixture['events'] if row['event_type'] == 'operation_start'}
                self.assertGreater(len(plan['replaced_top20_operations']), 20)
                self.assertGreater(len(plan['replaced_collector_operations']), 0)
                peer_ids = plan['peer_plan'].operation_ids
                self.assertEqual(1, len(peer_ids))
                self.assertEqual('peer:fixture', starts[peer_ids[0]]['producer_component'])
                self.assertEqual('upsert_documents', starts[peer_ids[0]]['method'])
                off = compile_top20_session_plan(fixture['events'], fixture['manifest'], **options, include_top20=False)
                self.assertEqual(peer_ids, off['peer_plan'].operation_ids)
                self.assertEqual(set(plan['replaced_top20_operations']), set(off['excluded_top20_operations']))
                excluded_peer = compile_top20_session_plan(fixture['events'], fixture['manifest'], **options,
                                                          exclude_peer_workloads=('shadow',))
                self.assertEqual((), excluded_peer['peer_plan'].operation_ids)
                self.assertEqual(peer_ids, excluded_peer['excluded_peer_operations'])
                for enabled in (True, False):
                    selected = plan if enabled else off
                    target, clock = store('on' if enabled else 'off'), Top20FixtureClock(ORIGIN)
                    target._query_cache_wall_time = clock.wall_time
                    client = RequestTapeClient(read_request_tape(fixture['events']), preserve_transport_delay=False)
                    bindings = client.task_bindings(fixture['roles'])
                    service, broker, collector = build_top20_session(target, clock, client, bindings,
                        outbox_path=None, minute_backfill_enabled=False)
                    prepared = prepare_top20_source_inputs(selected['source_inputs'], clock=clock,
                        started_mono_ns=fixture['started'], end_seconds=20)
                    with patch.object(trace, 'input_token', return_value=None), patch(
                            'kiwoom_monitor.central_server.realtime_collector.REALTIME_REG_INTERVAL_SECONDS', 0):
                        report = await execute_top20_session(service, broker, collector, clock=clock,
                            runtime=ReplayRuntimeScope(), bindings=bindings, subscription_tape=SubscriptionTape(
                                read_realtime_tape(fixture['events'])), source_hub_component=fixture['hub'],
                            source_component=fixture['component'], prepared_inputs=prepared,
                            started_mono_ns=fixture['started'], end_seconds=20, include_top20=enabled,
                            peer_events=fixture['events'], peer_operation_ids=peer_ids, drain_timeout=30)
                    self.assertTrue(report['execution_succeeded'], report)
                    self.assertEqual('complete', report['peer_execution']['state'])
                    self.assertEqual(1, len(report['peer_execution']['calls']))
                    self.assertEqual('200001', target.load_document('top20_daily_entrants', 'peer-fixture', 'peer')['document']['code'])
                    membership = target.load_dataset_snapshots('top20_membership', ORIGIN.date().isoformat())
                    self.assertEqual(1 if enabled else 0, len(membership))
                    self.assertFalse(report['historical_outputs_injected'])
                    self.assertEqual(0, report['runtime']['pending_threads'])
                rejected = copy.deepcopy(fixture['manifest'])
                rejected['known_dropped'] = 1
                with self.assertRaisesRegex(ValueError, 'incomplete_or_not_drained'):
                    compile_top20_session_plan(fixture['events'], rejected, **options)
                with self.assertRaisesRegex(ValueError, 'not_observed'):
                    compile_top20_session_plan(fixture['events'], fixture['manifest'], **options,
                                              include_peer_workloads=('not-recorded',))
            finally:
                for value in stores:
                    value.close()

    async def test_clock_mismatch_rejects_before_restore_or_store_access(self):
        lease = ReplayDatabaseLease('postgresql://kiwoom_monitor_replay:x@localhost/kiwoom_monitor_replay_test',
            'a'*32, baseline_version=2, cache_clock=Top20FixtureClock(ORIGIN))
        lease._active, lease.connection = True, object()
        with patch('kiwoom_monitor.central_server.diagnostic_top20_experiment.compile_top20_session_plan',
                   return_value={}), patch.object(lease, 'restore') as restore, \
                patch.object(trace, 'input_token', return_value=None):
            with self.assertRaisesRegex(ValueError, 'source_clock_mismatch'):
                await execute_owned_top20_fixture(lease, 'b'*64, events=[],
                    manifest={'started_at': ORIGIN.timestamp()-1}, task_bindings=[], component='t',
                    source_hub_component='h', source_collector_component='c', outbox_parent='unused', end_seconds=1)
            restore.assert_not_called()

    async def test_capture_on_rejects_before_plan_or_restore(self):
        lease = ReplayDatabaseLease('postgresql://kiwoom_monitor_replay:x@localhost/kiwoom_monitor_replay_test',
            'a'*32, baseline_version=2, cache_clock=Top20FixtureClock(ORIGIN))
        lease._active, lease.connection = True, object()
        with patch.object(trace, 'input_token', return_value='capture-running'), \
                patch.object(lease, 'restore') as restore:
            with self.assertRaisesRegex(ValueError, 'capture_must_be_off'):
                await execute_owned_top20_fixture(lease, 'b'*64, events=[], manifest={}, task_bindings=[],
                    component='t', source_hub_component='h', source_collector_component='c',
                    outbox_parent='unused', end_seconds=1)
            restore.assert_not_called()
