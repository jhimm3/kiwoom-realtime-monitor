"""Actual capture -> bounded reader -> owned native replay, on the new replay DB.

Inputs are recorded from controlled native calls, not a real intraday capture.
Never provision/reseal implicitly; each run restores the operator's fixed baseline.
"""
from __future__ import annotations

from contextlib import contextmanager
import asyncio
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from kiwoom_monitor.central_server import diagnostic_recorded_execution as execution
from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline
from kiwoom_monitor.central_server import diagnostic_replay_database_cli as cli
from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.diagnostic_metrics import refresh_capture_state
from kiwoom_monitor.central_server.diagnostic_replay_contract import capture_owner
from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool, _set_trace
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub


class RecordedExecutionPostgresTests(unittest.TestCase):
    @contextmanager
    def _candidate_flow_fixture(self):
        from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
        from kiwoom_monitor.central_server.market_ingest import MarketDataIngestor
        from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
        from kiwoom_monitor.central_server.diagnostic_top20_flow_input import VERSION

        class Client:
            def request_with_continuation(self, api_id, path, body, **kwargs):
                return {'stk_orgn_trde_trnsn': [{'dt': '20261007', 'frgnr_netprps': '10', 'orgn_netprps': '20'}]}, False, ''

        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / 'control.json'
            with patch.dict(os.environ, {'KIWOOM_DIAGNOSTIC_WORKLOAD_PATH': str(control)}):
                master = _set_tool(control, True, 300)['diagnostic_tool']['session_id']
                _set_trace(control, True, 120, expected_session=master)
                session = trace.start(seconds=60, store_inputs=True)
                try:
                    with baseline.ReplayDatabaseLease(self.url, self.token) as lease:
                        lease.restore(self.baseline_id)
                        store = lease.store()
                        self.assertFalse(store.load_documents('candidate_flow_capture', '2026-10-07:099997', 1))
                        broker = CentralRestBroker(Client(), store, MarketDataIngestor(store).ingest)
                        async def collect():
                            try:
                                await AutonomousTop20Service(broker, None, store,
                                    now_provider=lambda: datetime.fromisoformat('2026-10-07T09:01:02+09:00')
                                )._capture_candidate_investor_flow('099997', '2026-10-07')
                            finally:
                                await broker.close()
                        asyncio.run(collect())
                        self.assertEqual(0, lease.status()['owned_connections'])
                        lease.restore(self.baseline_id)
                    manifest = trace.stop()
                    self.assertEqual('complete', manifest['state'])
                    self.assertEqual(0, manifest['known_dropped'])
                    self.assertEqual(0, manifest['input_rejected'])
                    _, events = trace.recorded_events(session['trace_id'])
                    input_id = next(row['input_id'] for row in events if row.get('market_input_version') == VERSION)
                    yield events, input_id
                finally:
                    trace.stop()
                    _set_tool(control, False)
                    refresh_capture_state(force=True)

    def test_candidate_flow_native_ingest_repeats_and_restores_sealed_baseline(self):
        from kiwoom_monitor.central_server import diagnostic_top20_flow_input as flow
        original = flow._execute_candidate_flow
        verified = []
        async def inspect(store, events, input_id):
            result = await original(store, events, input_id)
            rows = await asyncio.to_thread(store.load_dataset_snapshots, 'investor_flow', '099997', 1)
            marker = await asyncio.to_thread(store.load_documents, 'candidate_flow_capture', '2026-10-07:099997', 1)
            self.assertEqual('20261007:SOR', rows[0]['snapshot_key'])
            self.assertEqual('10', rows[0]['payload']['rows'][0]['frgnr_netprps'])
            self.assertEqual('candidate_first_seen', marker[0]['document']['scope'])
            verified.append(result['replay_request_id'])
            return result
        with self._candidate_flow_fixture() as (events, input_id), patch.object(flow, '_execute_candidate_flow', side_effect=inspect):
            for _ in range(3):
                result = flow.run_owned_candidate_flow_experiment(self.url, self.token, self.baseline_id, events, input_id)
                self.assertTrue(result['cleanup']['baseline_restored'])
                self.assertFalse(result['source_state_equivalent'])
                self.assertFalse(result['whole_top20_replay'])
                self.assertEqual(input_id, result['source_input_id'])
                self.assertTrue(result['historical_descendant_operations_not_executed'])
                self._assert_clean()
        self.assertEqual(3, len(set(verified)))

    def test_candidate_flow_lost_ingest_ack_fails_and_restores_baseline(self):
        from kiwoom_monitor.central_server import diagnostic_top20_flow_input as flow
        from kiwoom_monitor.central_server.market_ingest import MarketDataIngestor
        original = MarketDataIngestor.ingest
        committed = []
        def lost_ack(ingestor, api_id, body, payload):
            original(ingestor, api_id, body, payload)
            committed.append(api_id)
            raise OSError('injected flow replay COMMIT acknowledgement loss')
        with self._candidate_flow_fixture() as (events, input_id), patch.object(MarketDataIngestor, 'ingest', lost_ack):
            with self.assertRaisesRegex(OSError, 'flow replay COMMIT acknowledgement loss'):
                flow.run_owned_candidate_flow_experiment(self.url, self.token, self.baseline_id, events, input_id)
        self.assertEqual(['ka10045'], committed)
        self._assert_clean()

    def test_captured_mixed_collector_replays_market_history_once_and_joins_native_db_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / 'control.json'
            with patch.dict(os.environ, {'KIWOOM_DIAGNOSTIC_WORKLOAD_PATH': str(control)}):
                master = _set_tool(control, True, 300)['diagnostic_tool']['session_id']
                _set_trace(control, True, 120, expected_session=master)
                session = trace.start(seconds=60, store_inputs=True, collector_inputs=True)
                try:
                    with baseline.ReplayDatabaseLease(self.url, self.token) as lease:
                        lease.restore(self.baseline_id)
                        store = lease.store()
                        now = datetime(2026, 10, 7, 1, tzinfo=timezone.utc)
                        collector = CentralRealtimeCollector(lambda: '', 'real', RealtimeHub(), lambda: now, store)
                        component = f'realtime_collector:{id(collector):x}'
                        collector._publish_parsed({'trnm': 'REAL', 'data': [
                            {'type': '0w', 'item': '005930_NX', 'values': {'20': '100000', '210': '-5', '212': '7'}},
                            {'type': '0J', 'item': '001', 'values': {'20': '100000', '10': '2500.1', '14': '99'}},
                            {'type': '0U', 'item': '101', 'values': {'20': '100000', '10': '800.5', '14': '42'}},
                        ]})
                        asyncio.run(collector.close())
                        with capture_owner('realtime', 'peer:source', 'peer:actor'):
                            store.save_dataset_snapshots([
                                ('market_state', self.owner, 'peer', {'peer': True}, None)])
                        self.assertEqual(0, lease.status()['owned_connections'])
                        lease.restore(self.baseline_id)
                    manifest = trace.stop()
                    self.assertEqual('complete', manifest['state'])
                    self.assertEqual(0, manifest['known_dropped'])
                    self.assertEqual(0, manifest['input_rejected'])
                    duration = (manifest['finished_mono_ns'] - manifest['started_mono_ns']) / 1e9
                    code, result = self._run((session['trace_id'], duration),
                                             '--mode', 'collector_with_background',
                                             '--collector-component', component)
                    self.assertEqual(0, code)
                    self.assertEqual(1, len(result['calls']))  # The peer is retained.
                    self.assertEqual('peer:source', result['calls'][0]['producer_component'])
                    self.assertEqual({'save_realtime_snapshots', 'save_dataset_snapshots'},
                                     {row['method'] for row in result['replaced_operation_details']})
                    report = result['collector_reports'][0]
                    self.assertEqual({'0w': 1, '0J': 1, '0U': 1}, report['event_type_counts'])
                    self.assertEqual(0, report['pending_records_after_drain'])
                    self.assertEqual(1, sum(call['kind'] == 'market_state' for call in report['calls']))
                    self.assertTrue(all(call['replay_call_ids'] for call in report['calls']))
                    self.assertEqual(self.baseline_counts['central_dataset_snapshots'] + 3,
                                     result['final_tables']['central_dataset_snapshots']['rows'])
                    self.assertFalse(result['cross_component_causal_replay'])
                finally:
                    trace.stop()
                    _set_tool(control, False)
                    refresh_capture_state(force=True)

    def setUp(self):
        self.url = os.environ.get('KIWOOM_REPLAY_DATABASE_URL', '')
        self.token = os.environ.get('KIWOOM_REPLAY_OWNER_TOKEN', '')
        if not self.url or not self.token:
            self.skipTest('provisioned/sealed dedicated replay DB is required')
        self.owner = 'recorded-execution-' + uuid4().hex
        with baseline.ReplayDatabaseLease(self.url, self.token) as lease:
            self.baseline_id = lease.status()['baseline_id']
            if not self.baseline_id:
                self.skipTest('sealed controlled baseline is required')
            self.baseline_counts = lease.restore(self.baseline_id)['table_counts']
        self.addCleanup(self._restore)

    def _restore(self):
        with baseline.ReplayDatabaseLease(self.url, self.token) as lease:
            lease.restore(self.baseline_id)

    def _assert_clean(self):
        with baseline.ReplayDatabaseLease(self.url, self.token) as lease:
            with lease.connection.cursor() as cursor:
                tables = lease._tables_digest(cursor, 'public')
                self.assertEqual(self.baseline_counts,
                                 {name: value['rows'] for name, value in tables.items()})
                cursor.execute('SELECT count(*) FROM central_documents WHERE owner=%s', (self.owner,))
                self.assertEqual(0, cursor.fetchone()[0])
                _, manifest = lease._baseline(cursor)
                self.assertEqual(manifest['sequences'], lease._sequences(cursor))

    @contextmanager
    def _recorded_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            control = Path(directory) / 'control.json'
            with patch.dict(os.environ, {'KIWOOM_DIAGNOSTIC_WORKLOAD_PATH': str(control)}):
                master = _set_tool(control, True, 300)['diagnostic_tool']['session_id']
                _set_trace(control, True, 120, expected_session=master)
                session = trace.start(seconds=60, store_inputs=True)
                try:
                    with baseline.ReplayDatabaseLease(self.url, self.token) as lease:
                        lease.restore(self.baseline_id)
                        store = lease.store()
                        with capture_owner('top20', 'top20:controlled', 'top20:actor'):
                            for value in (10, 10, 11):
                                store.upsert_documents('top20_daily_entrants', [{
                                    'owner': self.owner, 'key': '005930', 'document': {'value': value}}])
                            self.assertEqual({'value': 11}, store.load_document(
                                'top20_daily_entrants', self.owner, '005930')['document'])
                        with capture_owner('rest_market', 'rest:controlled', 'rest:actor'):
                            store.upsert_documents('stock_fundamentals', [{
                                'owner': self.owner, 'key': '005930', 'document': {'value': 20}}])
                            self.assertEqual({'value': 20}, store.load_document(
                                'stock_fundamentals', self.owner, '005930')['document'])
                        self.assertEqual(0, lease.status()['owned_connections'])
                        lease.restore(self.baseline_id)
                    manifest = trace.stop()
                    self.assertEqual('complete', manifest['state'])
                    self.assertEqual(0, manifest['known_dropped'])
                    # A genuine captured interval; do not manufacture a future
                    # finish timestamp to make an out-of-bounds request pass.
                    duration = (manifest['finished_mono_ns'] - manifest['started_mono_ns']) / 1e9
                    yield session['trace_id'], duration
                finally:
                    trace.stop()
                    _set_tool(control, False)
                    refresh_capture_state(force=True)

    def _run(self, capture, *selection):
        output = io.StringIO()
        code = cli.main(['run', '--trace-id', capture[0], '--baseline-id', self.baseline_id,
                         '--window-start', '0', '--window-end', str(capture[1]),
                         *selection], output=output)
        value = json.loads(output.getvalue())
        self.assertEqual('ok', value['state'], value)
        result = value['result']
        self.assertTrue(result['cleanup']['baseline_restored'])
        self.assertTrue(result['baseline_managed'])
        self.assertFalse(result['source_state_equivalent'])
        self.assertFalse(result['public_execution_ready'])
        self.assertEqual([], result['unobserved_replay_operations'])
        self.assertTrue(result['db_observation_complete'])
        self._assert_clean()
        return code, result

    def test_same_captured_inputs_repeat_three_times_and_workload_masks_preserve_native_calls(self):
        fingerprints, operation_ids, native_ids, content_signatures = [], [], set(), []
        with self._recorded_fixture() as capture:
            for arguments, expected in [([], {11, 20})] * 3 + [
                    (['--include-workload', 'top20'], {11}),
                    (['--exclude-workload', 'top20'], {20})]:
                with patch.object(execution, '_result_digest', wraps=execution._result_digest) as digests:
                    code, result = self._run(capture, *arguments)
                self.assertEqual(0, code)
                self.assertEqual('complete', result['state'])
                actual = {call.args[0]['document']['value'] for call in digests.call_args_list
                          if type(call.args[0]) is dict and 'document' in call.args[0]}
                self.assertEqual(expected, actual)
                count = 6 if len(expected) == 2 else (4 if 11 in expected else 2)
                self.assertEqual(count, len(result['calls']))
                self.assertEqual(count, len(result['db_calls']['calls']))
                for operation in result['calls']:
                    self.assertEqual(1, len(operation['source_call_ids']))
                    self.assertEqual(1, len(operation['replay_call_ids']))
                    identifier = operation['replay_call_ids'][0]
                    self.assertNotIn(identifier, native_ids)
                    native_ids.add(identifier)
                fingerprints.append(result['input_sha256'])
                comparison = result['final_content_comparison']
                self.assertFalse(comparison['functional_equivalence_verified'])
                self.assertFalse(comparison['timing_equivalence_verified'])
                self.assertTrue(all(table['content_projection_valid']
                                    for table in comparison['tables'].values()))
                content_signatures.append({name: (table['rows'], table['sha256'])
                                           for name, table in comparison['tables'].items()})
                operation_ids.append({call['source_operation_id'] for call in result['calls']})
            self.assertEqual(1, len(set(fingerprints)))
            self.assertEqual(operation_ids[0], operation_ids[1])
            self.assertEqual(operation_ids[0], operation_ids[2])
            self.assertEqual(content_signatures[0], content_signatures[1])
            self.assertEqual(content_signatures[0], content_signatures[2])
            self.assertNotEqual(content_signatures[0], content_signatures[3])
            self.assertNotEqual(content_signatures[0], content_signatures[4])
            self.assertEqual(operation_ids[0], operation_ids[3] | operation_ids[4])
            self.assertFalse(operation_ids[3] & operation_ids[4])

    def test_lost_native_commit_ack_is_incomplete_and_restores_after_actual_drain(self):
        original = baseline._OwnedConnection.commit
        committed = []

        def lost_ack(connection):
            original(connection)
            committed.append(connection.info.backend_pid)
            raise OSError('injected recorded replay COMMIT acknowledgement loss')

        with self._recorded_fixture() as capture:
            with patch.object(baseline._OwnedConnection, 'commit', lost_ack):
                code, result = self._run(capture, '--include-workload', 'top20')
            self.assertEqual(1, code)
            self.assertEqual('incomplete', result['state'])
            self.assertTrue(committed)
            self.assertTrue(any(call['state'] == 'failed' for call in result['calls']))
            self.assertTrue(any(error['stage'] == 'commit' and error['exception_type'] == 'OSError'
                                for call in result['db_calls']['calls'] for error in call['errors']))


if __name__ == '__main__':
    unittest.main()
