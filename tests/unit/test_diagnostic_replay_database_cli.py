from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from kiwoom_monitor.central_server import diagnostic_replay_database_cli as cli


URL = 'postgresql://kiwoom_monitor_replay:supersecret@localhost/kiwoom_monitor_replay_test'
TOKEN = 'abcdef0123456789abcdef0123456789'
ENV = {cli._URL_ENV: URL, cli._TOKEN_ENV: TOKEN}


class RecordedReplayDatabaseCliTests(unittest.TestCase):
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
