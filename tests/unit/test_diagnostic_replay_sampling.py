import unittest
from threading import Event, Thread
from unittest.mock import MagicMock, patch

from kiwoom_monitor.central_server import diagnostic_replay_sampling as sampling


class ReplaySamplingTests(unittest.TestCase):
    def test_missing_activity_identity_does_not_stop_later_probes_or_invent_a_backend(self):
        connection = MagicMock()
        rows = connection.cursor.return_value.__enter__.return_value.fetchall
        rows.side_effect = [
            [(8, None, 'active', '', '', [], True),
             (9, 1, 'active', '', '', None, True),
             (10, 1, 'active', '', '', [None], True)],
            [(11, 1, 'active', 'IO', 'WALSync', [], True)]]
        observer = sampling.ReplayActivitySampler(connection)
        observer._read()
        observer._read()
        report = observer.report()
        self.assertEqual(2, report['query_count'])
        self.assertEqual([11], [row['pid'] for row in report['samples']])
        self.assertEqual({'backend_start': 1, 'blocking_pids': 1,
                          'invalid_backend_identity': 1}, report['unidentifiable_row_fields'])
        self.assertEqual('incomplete', report['state'])
        self.assertEqual([], report['error_types'])

    def test_process_usage_reports_bytes_and_does_not_invent_missing_rss(self):
        with patch.object(sampling.Path, 'read_text', return_value='VmRSS:\t123 kB\nVmHWM:\t234 kB\n'):
            values = sampling.replay_process_usage()
        self.assertEqual(123*1024, values['rss_bytes'])
        self.assertEqual(234*1024, values['process_lifetime_peak_rss_bytes'])
        with patch.object(sampling.Path, 'read_text', side_effect=OSError()):
            self.assertIsNone(sampling.replay_process_usage()['rss_bytes'])

    def test_fixed_read_only_query_bounds_rows_and_hides_statement_text(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
            (8, 1, 'active', 'IO', 'WALSync', [9], True)] * 65
        observer = sampling.ReplayActivitySampler(connection, max_samples=2)
        observer._read()
        self.assertEqual(2, len(observer.samples))
        self.assertEqual(62, observer.dropped)
        self.assertTrue(observer.rows_truncated)
        cursor = connection.cursor.return_value.__enter__.return_value
        queries = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(["SET LOCAL transaction_read_only=on", "SET LOCAL statement_timeout='500ms'"], queries[:2])
        self.assertIn('pid<>pg_backend_pid()', queries[2])
        self.assertIn('datname=current_database()', queries[2])
        self.assertEqual('COMMIT', observer.samples[0]['statement_type'])
        self.assertNotIn('query', observer.samples[0])
        self.assertEqual('incomplete', observer.report()['state'])

    def test_probe_errors_are_safe_and_do_not_replace_body_failure(self):
        connection = MagicMock()
        connection.transaction.side_effect = OSError('secret URL and SQL')
        observer = sampling.ReplayActivitySampler(connection)
        with self.assertRaisesRegex(ValueError, 'native'):
            with observer:
                raise ValueError('native')
        result = observer.report()
        self.assertEqual(['OSError'], result['error_types'])
        self.assertNotIn('secret', repr(result))
        self.assertTrue(result['observer_drained'])

    def test_close_joins_inflight_probe_before_management_connection_reuse(self):
        entered, release, closed = Event(), Event(), Event()
        observer = sampling.ReplayActivitySampler(None)
        def read():
            entered.set()
            release.wait(2)
        observer._read = read
        observer._ready.set()
        observer.__enter__()
        self.assertTrue(entered.wait(1))
        def close():
            observer.__exit__()
            closed.set()
        closer = Thread(target=close)
        closer.start()
        try:
            self.assertFalse(closed.wait(.05))
        finally:
            release.set()
            closer.join(2)
        self.assertTrue(closed.is_set())
        self.assertTrue(observer.report()['observer_drained'])

    def test_interrupted_join_still_drains_before_propagating(self):
        observer = sampling.ReplayActivitySampler(None)
        observer._thread = MagicMock()
        observer._thread.is_alive.side_effect = [True, True, False, False]
        observer._thread.join.side_effect = [KeyboardInterrupt(), None]
        with self.assertRaises(KeyboardInterrupt):
            observer.__exit__()
        self.assertEqual(2, observer._thread.join.call_count)
        self.assertTrue(observer.report()['observer_drained'])

    def test_correlation_rejects_pid_reuse_other_statement_and_edge_intervals(self):
        call = dict(call_id='a', backend_pid=8, started_at=3,
                    commit_started_at=4, commit_finished_at=5, commit_ms=1000,
                    writer_family='family', writer_kind='kind')
        row = dict(pid=8, backend_started_at=1, started_at=4.1, finished_at=4.2,
                   statement_type='COMMIT', state='active', wait_type='IO',
                   wait_event='WALSync', blocking_pids=[9])
        activity = dict(state='complete', target_interval_ms=25, samples=[row,
            {**row, 'backend_started_at': 4.01}, {**row, 'statement_type': 'other'},
            {**row, 'started_at': 3.99}, {**row, 'finished_at': 5.01},
            {**row, 'pid': 7}, {**row, 'started_at': 4.3, 'finished_at': 4.2}])
        result = sampling.correlate_replay_commits({'calls': [call]}, activity)
        self.assertEqual(1, result['calls'][0]['sample_count'])
        self.assertEqual({'IO:WALSync': 1}, result['calls'][0]['wait_samples'])
        self.assertEqual([9], result['calls'][0]['blocking_pids'])
        self.assertEqual('incomplete', sampling.correlate_replay_commits(
            {'calls': [call], 'raw_truncated': True}, activity)['state'])
        activity['samples'] = [{**row, 'backend_started_at': 3.1}]
        self.assertEqual(1, sampling.correlate_replay_commits(
            {'calls': [call]}, activity)['calls'][0]['sample_count'])

    def test_null_wait_is_not_cpu_proof_and_idle_commit_state_is_preserved(self):
        call = dict(call_id='a', backend_pid=8, started_at=3,
                    commit_started_at=4, commit_finished_at=5, commit_ms=1000,
                    writer_family='family', writer_kind='kind')
        activity = dict(state='complete', target_interval_ms=25, samples=[dict(
            pid=8, backend_started_at=1, started_at=4.1, finished_at=4.2,
            statement_type='COMMIT', state='idle', wait_type='', wait_event='', blocking_pids=[])])
        result = sampling.correlate_replay_commits({'calls': [call]}, activity)
        self.assertEqual({'NONE:NONE': 1}, result['calls'][0]['wait_samples'])
        self.assertEqual({'idle': 1}, result['calls'][0]['backend_states'])
        activity['samples'] = []
        self.assertEqual('no_sample', sampling.correlate_replay_commits(
            {'calls': [call]}, activity)['calls'][0]['sampling_status'])

    def test_owned_execution_drains_observer_before_status_digest_and_restore(self):
        from kiwoom_monitor.central_server import diagnostic_recorded_execution as execution
        from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline
        from kiwoom_monitor.central_server import diagnostic_replay_comparison as comparison
        order = []
        lease = MagicMock()
        lease.__enter__.return_value = lease
        lease.status.side_effect = lambda: order.append('status') or {'owned_connections': 0}
        lease.restore.side_effect = lambda unused: order.append('restore') or {}
        class Observer:
            def __init__(self, connection):
                self.connection = connection
            def __enter__(self):
                order.append('observer_start')
            def __exit__(self, *unused):
                order.append('observer_stop')
            def report(self):
                return {'observer_drained': True}
        async def native(*unused, **options):
            order.append('native')
            return {}
        with patch.object(baseline, 'ReplayDatabaseLease', return_value=lease), \
             patch.object(baseline, '_ReplayStore'), \
             patch.object(execution, 'compile_recorded_plan'), \
             patch.object(execution, '_prepare'), patch.object(execution, '_collector_inputs', return_value={}), \
             patch.object(execution, '_execute_recorded_operations', autospec=True, side_effect=native), \
             patch.object(sampling, 'ReplayActivitySampler', Observer), \
             patch.object(comparison, 'collect_final_content_comparison', return_value={}):
            result = execution.run_owned_recorded_experiment('url', 'token', 'base', [],
                collect_activity=True, started_mono_ns=1, window_start_seconds=0,
                window_end_seconds=1)
        self.assertEqual(['restore', 'observer_start', 'native', 'observer_stop', 'status', 'restore'], order)
        self.assertTrue(result['postgres_activity']['observer_drained'])


if __name__ == '__main__':
    unittest.main()
