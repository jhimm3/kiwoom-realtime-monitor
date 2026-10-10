from __future__ import annotations

import io
import json
import os
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from kiwoom_monitor.central_server import diagnostic_replay_database_cli as cli


URL = 'postgresql://kiwoom_monitor_replay:supersecret@localhost/kiwoom_monitor_replay_test'
TOKEN = 'abcdef0123456789abcdef0123456789'
ENV = {cli._URL_ENV: URL, cli._TOKEN_ENV: TOKEN}


class RecordedReplayDatabaseCliTests(unittest.TestCase):
    def test_durable_v3_selection_uses_actual_capture_end_before_database(self):
        from tests.unit.test_diagnostic_account_context import AccountContextTests, REF
        from tests.unit.test_diagnostic_trace_deferred import deferred_capture
        from tests.unit.test_diagnostic_trace_blocks import persist_now
        from kiwoom_monitor.central_server.diagnostic_replay_contract import capture_owner
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline
        case = AccountContextTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        with deferred_capture(account_inputs=True, account_context_store=case.source) as (identifier, _):
            with capture_owner('account', 'account:fixture', 'account-actor'):
                self.assertTrue(case.source.release_execution_runtime('mock:' + REF,
                    'run:with:colons:original-authority'))
            manifest = persist_now()
            duration = (manifest['finished_mono_ns'] - manifest['started_mono_ns']) / 1e9
            origin = datetime.fromtimestamp(manifest['started_at']).astimezone().isoformat()
            args = cli._parser().parse_args(['seal', '--baseline-version', '3', '--source-origin', origin,
                '--trace-id', identifier, '--window-start', '0', '--window-end', str(duration)])
            options = cli._baseline_options(args)
            with patch.object(baseline, '_maintenance_connection') as connect:
                _, rows, selection = cli._read_selection(args, options)
                capsule, owners = cli._v3_context(URL, TOKEN, args, options, rows, selection)
                self.assertEqual(identifier, capsule['trace_id'])
                self.assertEqual(1, len(owners))
                connect.assert_not_called()
            args.window_end += .15
            with self.assertRaisesRegex(ValueError, 'window'):
                cli._read_selection(args, options)

    def account_fixture(self):
        from tests.unit.test_diagnostic_account_context import AccountContextTests
        case = AccountContextTests()
        case.setUp()
        self.addCleanup(case.tearDown)
        self.addCleanup(case.doCleanups)
        capsule, rows, _ = case.scheduler_fixture()
        started = max(capsule['snapshot_finished_mono_ns'] + 1, rows[0]['entered_mono_ns'])
        delta = started - rows[0]['entered_mono_ns']
        for row in rows:
            for key in ('mono_ns', 'entered_mono_ns', 'started_mono_ns', 'finished_mono_ns'):
                if key in row:
                    row[key] += delta
        origin = capsule['snapshot_finished_at'] + (started - capsule['snapshot_finished_mono_ns']) / 1e9
        manifest = dict(started_mono_ns=started, started_at=origin, window_read={}, source_release='fixture')
        arguments = ['--baseline-version', '3', '--source-origin', datetime.fromtimestamp(origin).astimezone().isoformat(),
                     '--trace-id', capsule['trace_id'], '--window-start', '0', '--window-end', '.15']
        return capsule, rows, manifest, arguments

    def test_v3_seal_requires_explicit_selection_before_lease(self):
        for extras in ([], ['--trace-id', 'capture'], ['--window-start', '0', '--window-end', '1']):
            with patch.object(cli, 'ReplayDatabaseLease') as factory:
                code, value = self.invoke(['seal', '--baseline-version', '3', '--source-origin',
                    '2026-10-12T09:00:00+09:00', *extras], ENV)
                self.assertEqual(1, code)
                self.assertEqual('replay_v3_seal_requires_trace_selection', value['reason'])
                factory.assert_not_called()

    def test_v3_seal_passes_selected_native_owner_frontier(self):
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        from kiwoom_monitor.central_server import diagnostic_account_context as context
        from kiwoom_monitor.central_server import diagnostic_recorded_execution as execution
        capsule, rows, manifest, arguments = self.account_fixture()
        with patch.object(trace, 'recorded_window_events', return_value=(manifest, rows)), \
             patch.object(context, 'read_recorded_account_context', return_value=capsule), \
             patch.object(execution, '_prepare', return_value={}) as prepare, \
             patch('kiwoom_monitor.central_server.diagnostic_replay_baseline._ReplayStore'), \
             patch.object(cli, 'ReplayDatabaseLease') as factory:
            lease = factory.return_value.__enter__.return_value
            lease.seal.return_value = {'baseline_id': 'a' * 64}
            code, value = self.invoke(['seal', *arguments], ENV)
        self.assertEqual(0, code, value)
        prepare.assert_called_once()
        self.assertEqual(capsule, lease.seal.call_args.kwargs['account_context'])
        self.assertEqual(['snapshot', 'release', 'revision'],
                         [row['operation_id'] for row in lease.seal.call_args.kwargs['owner_rows']])

    def test_v3_run_passes_verified_context_and_clock_without_serializing_capsule(self):
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        from kiwoom_monitor.central_server import diagnostic_account_context as context
        from kiwoom_monitor.central_server import diagnostic_recorded_execution as execution
        from kiwoom_monitor.central_server import diagnostic_metrics as metrics
        capsule, rows, manifest, arguments = self.account_fixture()
        with patch.object(trace, 'recorded_window_events', return_value=(manifest, rows)), \
             patch.object(context, 'read_recorded_account_context', return_value=capsule), \
             patch.object(execution, '_prepare', return_value={}), \
             patch.object(execution, 'run_owned_recorded_experiment', return_value={
                 'state': 'complete', 'calls': [], 'collector_reports': []}) as run, \
             patch.object(metrics, 'summarize_db_calls', return_value={'calls': []}):
            code, value = self.invoke(['run', '--baseline-id', 'c' * 64, *arguments], ENV)
        self.assertEqual(0, code, value)
        self.assertEqual(capsule, run.call_args.kwargs['account_context'])
        self.assertEqual(3, run.call_args.kwargs['baseline_version'])
        self.assertFalse(run.call_args.kwargs['cache_clock'].armed)
        self.assertNotIn('account_context', value['result']['selection'])

    def test_v3_corrupt_capsule_or_clock_fails_before_observer_and_lease_acquisition(self):
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        from kiwoom_monitor.central_server import diagnostic_account_context as context
        from kiwoom_monitor.central_server import diagnostic_workloads as controls
        capsule, rows, manifest, arguments = self.account_fixture()
        for reason in ('checksum', 'clock'):
            with patch.object(trace, 'recorded_window_events', return_value=(manifest, rows)), \
                 patch.object(context, 'read_recorded_account_context',
                    side_effect=ValueError('capsule_checksum_mismatch') if reason == 'checksum' else None,
                    return_value={**capsule, 'snapshot_finished_mono_ns': manifest['started_mono_ns'] + 1}), \
                 patch.object(cli, 'ReplayDatabaseLease') as lease, \
                 patch.object(controls, '_set_tool') as control:
                code, _ = self.invoke(['run', '--baseline-id', 'c' * 64, *arguments], ENV)
                self.assertEqual(1, code)
                lease.assert_not_called()
                control.assert_not_called()

    def test_large_reference_hash_does_not_hydrate_and_pins_descriptor(self):
        from kiwoom_monitor.central_server.diagnostic_trace_payload import BlockPayloadReference
        reference = BlockPayloadReference('fixture', 'a' * 64, b'{"format":"blocks/v1","bytes":12}')
        with patch.object(BlockPayloadReference, 'load', side_effect=AssertionError('no hydration')):
            digest = cli._input_hash([{'payload': reference}])
            self.assertEqual(digest, cli._input_hash([{'payload': reference}]))
            self.assertNotEqual(digest, cli._input_hash([{'payload': BlockPayloadReference(
                'fixture', 'a' * 64, b'{"format":"blocks/v1","bytes":13}')}]))
        self.assertEqual(64, len(digest))

    def test_v3_unsupported_native_method_rejects_before_database_or_observer(self):
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        from kiwoom_monitor.central_server import diagnostic_account_context as context
        from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline
        from kiwoom_monitor.central_server import diagnostic_workloads as controls
        capsule, rows, manifest, arguments = self.account_fixture()
        rows[0]['method'] = rows[1]['method'] = 'not_a_native_method'
        with patch.object(trace, 'recorded_window_events', return_value=(manifest, rows)), \
             patch.object(context, 'read_recorded_account_context', return_value=capsule), \
             patch.object(baseline, '_maintenance_connection') as connect, \
             patch.object(controls, '_set_tool') as control:
            code, value = self.invoke(['run', '--baseline-id', 'c' * 64, *arguments], ENV)
            self.assertEqual(1, code, value)
            connect.assert_not_called()
            control.assert_not_called()

    def test_empty_profile_checks_inputs_and_baseline_before_any_recorded_execution(self):
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        from kiwoom_monitor.central_server import diagnostic_recorded_execution as execution
        from kiwoom_monitor.central_server.diagnostic_replay_contract import compile_recorded_plan
        from tests.unit.test_diagnostic_scoped_window import TRACE, SELECTION, write_fixture
        from pathlib import Path
        import tempfile
        arguments = ['run', '--trace-id', TRACE, '--baseline-profile', 'empty-v1',
            '--window-start', '0', '--window-end', '.05', '--include-workload', 'realtime',
            '--capture-policy', 'scoped-operations']
        with tempfile.TemporaryDirectory() as root, patch.object(trace, '_directory', return_value=Path(root)):
            write_fixture(root)
            with patch.object(cli, 'provision_existing_empty_database') as provision, \
                 patch.object(cli, 'ReplayDatabaseLease') as factory, \
                 patch.object(execution, 'run_owned_recorded_experiment') as run:
                lease = factory.return_value.__enter__.return_value
                lease.seal.return_value = {'baseline_id': 'b' * 64}
                lease.restore.return_value = {'baseline_id': 'b' * 64}
                code, result = self.invoke(arguments + ['--preflight-only'], ENV)
                self.assertEqual(0, code)
                self.assertEqual('preflight_passed', result['result']['state'])
                self.assertEqual(0, result['result']['recorded_operations_executed'])
                self.assertFalse(result['result']['source_state_equivalent'])
                provision.assert_called_once_with(URL, TOKEN)
                run.assert_not_called()
                provision.reset_mock()
                code, _ = self.invoke(arguments, ENV)
                self.assertEqual(1, code)
                provision.assert_not_called()
                code, result = self.invoke(arguments + ['--expected-baseline-id', 'a' * 64], ENV)
                self.assertEqual(1, code)
                self.assertEqual('replay_expected_baseline_mismatch', result['reason'])
                run.assert_not_called()
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_empty_profile_preflight_rejects_selected_rejection_before_provision(self):
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        from tests.unit.test_diagnostic_scoped_window import TRACE, write_fixture, fixture_rows
        from pathlib import Path
        import tempfile
        arguments = ['run', '--trace-id', TRACE, '--baseline-profile', 'empty-v1', '--preflight-only',
            '--window-start', '0', '--window-end', '.05', '--include-workload', 'realtime',
            '--capture-policy', 'scoped-operations']
        with tempfile.TemporaryDirectory() as root, patch.object(trace, '_directory', return_value=Path(root)):
            rows = fixture_rows()
            rows[3]['workload_id'] = 'realtime'
            write_fixture(root, rows)
            with patch.object(cli, 'provision_existing_empty_database') as provision:
                code, result = self.invoke(arguments, ENV)
                self.assertEqual(1, code)
                self.assertEqual('recorded_selected_input_unsupported', result['reason'])
                provision.assert_not_called()

    def test_v2_run_origin_errors_fail_before_observer_or_database(self):
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        from kiwoom_monitor.central_server import diagnostic_workloads as controls
        arguments = ['run', '--trace-id', 'capture', '--baseline-id', 'c' * 64,
                     '--window-start', '10', '--window-end', '11']
        variants = [arguments + ['--baseline-version', '2'],
                    arguments + ['--source-origin', '2026-10-06T08:55:00+09:00'],
                    arguments + ['--baseline-version', '2', '--source-origin', '2026-10-06T08:55:00'],
                    arguments + ['--baseline-version', '2', '--source-origin', '2026-10-06T08:55:00+09:00']]
        for args in variants:
            with self.subTest(args=args), patch.object(trace, 'recorded_window_events', return_value=(
                    {'started_at': datetime.fromisoformat('2026-10-07T08:55:00+09:00').timestamp()}, [])), \
                 patch.object(controls, '_set_tool') as control:
                code, _ = self.invoke(args, ENV)
                self.assertEqual(1, code)
                control.assert_not_called()

    def test_v2_run_passes_one_frozen_clock_without_serializing_it_in_selection(self):
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        from kiwoom_monitor.central_server import diagnostic_metrics as metrics
        from kiwoom_monitor.central_server import diagnostic_recorded_execution as execution
        origin = '2026-10-06T08:55:00+09:00'
        manifest = {'started_at': datetime.fromisoformat(origin).timestamp(),
                    'started_mono_ns': 1, 'source_release': 'source', 'window_read': {}}
        arguments = ['run', '--trace-id', 'capture', '--baseline-id', 'c' * 64,
                     '--window-start', '10', '--window-end', '11',
                     '--baseline-version', '2', '--source-origin', origin]
        with patch.object(trace, 'recorded_window_events', return_value=(manifest, [])), \
             patch.object(execution, 'run_owned_recorded_experiment', return_value={
                 'state': 'complete', 'calls': [], 'collector_reports': []}) as run, \
             patch.object(metrics, 'summarize_db_calls', return_value={'calls': []}):
            code, result = self.invoke(arguments, ENV)
        self.assertEqual(0, code)
        self.assertEqual(2, run.call_args.kwargs['baseline_version'])
        self.assertFalse(run.call_args.kwargs['cache_clock'].armed)
        self.assertNotIn('clock', result['result']['selection'])
        self.assertNotIn('cache_clock', result['result']['selection'])

    def test_v2_management_requires_explicit_aware_source_origin_and_stays_opt_in(self):
        for args in (['status', '--baseline-version', '2'],
                     ['seal', '--baseline-version', '2', '--source-origin', '2026-10-06T08:55:00'],
                     ['status', '--source-origin', '2026-10-06T08:55:00+09:00']):
            with self.subTest(args=args), patch.object(cli, 'ReplayDatabaseLease') as factory:
                code, _ = self.invoke(args, ENV)
                self.assertEqual(1, code)
                factory.assert_not_called()
        with patch.object(cli, 'ReplayDatabaseLease') as factory:
            factory.return_value.__enter__.return_value.status.return_value = {'baseline_sealed': False}
            code, _ = self.invoke(['status', '--baseline-version', '2', '--source-origin',
                                   '2026-10-06T08:55:00+09:00'], ENV)
            self.assertEqual(0, code)
            self.assertEqual(2, factory.call_args.kwargs['baseline_version'])
            self.assertFalse(factory.call_args.kwargs['cache_clock'].armed)

    def invoke(self, args, environ=None):
        output = io.StringIO()
        code = cli.main(args, environ=environ, output=output)
        return code, json.loads(output.getvalue())

    def test_missing_or_invalid_environment_fails_without_connecting_or_echoing_secret(self):
        with patch.object(cli, 'provision_existing_empty_database') as provision:
            code, result = self.invoke(['provision'], {})
            self.assertEqual(1, code)
            self.assertIn(cli._URL_ENV, result['reason'])
            self.assertNotIn('supersecret', json.dumps(result))
            code, result = self.invoke(['provision'], {**ENV, cli._TOKEN_ENV: 'bad'})
            self.assertEqual(1, code)
            self.assertNotIn(TOKEN, json.dumps(result))
            provision.assert_not_called()

    def test_provision_uses_only_fixed_environment_target_and_hides_credentials(self):
        with patch.object(cli, 'provision_existing_empty_database', return_value={
                'database': 'kiwoom_monitor_replay_test', 'baseline_sealed': False}) as provision:
            code, result = self.invoke(['provision'], ENV)
        self.assertEqual(0, code)
        provision.assert_called_once_with(URL, TOKEN)
        self.assertNotIn('supersecret', json.dumps(result))
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_invalid_restore_id_fails_before_database_lease(self):
        with patch.object(cli, 'ReplayDatabaseLease') as lease:
            code, result = self.invoke(['restore', '--baseline-id', 'not-a-baseline'], ENV)
        self.assertEqual(1, code)
        self.assertEqual('baseline_id_must_be_64_lowercase_hex_characters', result['reason'])
        lease.assert_not_called()

    def test_run_checks_capture_before_connect_and_hides_database_errors(self):
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        arguments = ['run', '--trace-id', 'old', '--baseline-id', 'c' * 64,
                     '--window-start', '0', '--window-end', '60']
        with patch.object(trace, 'recorded_window_events', side_effect=ValueError('old_schema')), \
             patch.object(cli, 'ReplayDatabaseLease') as lease:
            code, result = self.invoke(arguments, ENV)
            self.assertEqual(1, code)
            self.assertEqual('old_schema', result['reason'])
            lease.assert_not_called()
        with patch.object(cli, '_run_trace', side_effect=cli.psycopg.OperationalError(URL + TOKEN)):
            code, result = self.invoke(arguments, ENV)
            self.assertEqual(1, code)
            self.assertNotIn('supersecret', json.dumps(result))
            self.assertNotIn(TOKEN, json.dumps(result))

    def test_offline_observation_links_calls_and_preserves_original_control(self):
        from kiwoom_monitor.central_server import diagnostic_metrics as metrics
        from kiwoom_monitor.central_server import diagnostic_recorded_execution as execution
        from kiwoom_monitor.central_server import diagnostic_trace as trace
        manifest = {'started_mono_ns': 1, 'source_release': 'captured-source', 'window_read': {}}
        native = {'state': 'complete', 'calls': [{'source_operation_id': 'source',
                  'replay_operation_id': 'replayed'}], 'collector_reports': [],
                  'cleanup': {'baseline_restored': True}}
        arguments = ['run', '--trace-id', 'capture', '--baseline-id', 'c' * 64,
                     '--window-start', '10', '--window-end', '60',
                     '--include-workload', 'top20', '--concurrency', '2']
        with patch.dict(os.environ, {'KIWOOM_DIAGNOSTIC_WORKLOAD_PATH': 'original-control'}), \
             patch.object(trace, 'recorded_window_events', return_value=(manifest, [])), \
             patch.object(execution, 'run_owned_recorded_experiment', return_value=native) as run, \
             patch.object(metrics, 'summarize_db_calls', return_value={
                 'calls': [{'call_id': 'native-span', 'input_operation_id': 'replayed'}],
                 'raw_truncated': False, 'dropped': 0}):
            code, value = self.invoke(arguments, ENV)
            self.assertEqual('original-control', os.environ['KIWOOM_DIAGNOSTIC_WORKLOAD_PATH'])
        self.assertEqual(0, code)
        result = value['result']
        self.assertEqual(['native-span'], result['calls'][0]['replay_call_ids'])
        self.assertEqual([], result['unobserved_replay_operations'])
        self.assertTrue(result['db_observation_complete'])
        self.assertFalse(result['wal_attribution_available'])
        self.assertEqual(('top20',), run.call_args.kwargs['include_workloads'])
        self.assertEqual(2, run.call_args.kwargs['concurrency'])
        self.assertTrue(run.call_args.kwargs['collect_activity'])

    def test_status_seal_and_restore_use_exact_cli_operation(self):
        for command in ('status', 'seal', 'restore'):
            with self.subTest(command=command):
                lease = MagicMock()
                lease.__enter__.return_value = lease
                lease.status.return_value = {'ownership_verified': True}
                lease.seal.return_value = {'baseline_id': 'b' * 64}
                lease.restore.return_value = {'baseline_managed': True}
                args = [command]
                if command == 'restore':
                    args += ['--baseline-id', 'c' * 64]
                with patch.object(cli, 'ReplayDatabaseLease', return_value=lease) as factory:
                    code, result = self.invoke(args, ENV)
                self.assertEqual(0, code)
                factory.assert_called_once_with(URL, TOKEN)
                getattr(lease, command).assert_called_once_with(*(['c' * 64] if command == 'restore' else []))
                self.assertEqual(command, result['command'])


if __name__ == '__main__':
    unittest.main()
