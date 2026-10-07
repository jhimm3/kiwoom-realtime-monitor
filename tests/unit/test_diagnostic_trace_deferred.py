from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.diagnostic_replay_contract import thaw_payload
from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool, _set_trace, diagnostic_run_lock


@contextmanager
def deferred_capture():
    with tempfile.TemporaryDirectory() as root:
        control = Path(root) / 'control.json'
        with patch.object(trace, 'control_path', return_value=control), patch(
                'kiwoom_monitor.central_server.diagnostic_workloads.control_path', return_value=control), \
                patch.object(trace, '_deferred_memory_check', return_value={'test_headroom': True}):
            master = _set_tool(control, True, 300)['diagnostic_tool']['session_id']
            _set_trace(control, True, 120, expected_session=master)
            active = trace.start(seconds=60, store_inputs=True, collector_inputs=True,
                                 persist_at=time.time() + 3600)
            try:
                yield active['trace_id'], control
            finally:
                trace.stop('server_shutdown', timeout=15)
                _set_tool(control, False)


def wait_state(state, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if trace.status()['state'] == state:
            return trace.status()
        threading.Event().wait(0.01)
    raise AssertionError(f'Expected {state}, got {trace.status()["state"]}')


class DeferredTraceTests(unittest.TestCase):
    def test_more_than_old_event_capacity_stays_in_ram_then_persists_in_order(self):
        with deferred_capture() as (identifier, control):
            self.assertTrue(trace.emit_payload(identifier, 'collector_input',
                                               {'input_id': 'cause', 'workload_id': 'realtime'}, {'price': 123}))
            for index in range(33_000):
                trace.emit(identifier, 'call_end', {'call_id': str(index)})
                if index % 256 == 0:
                    threading.Event().wait(.001)
            trace.stop(timeout=0.01)
            held = wait_state('awaiting_persistence')
            self.assertEqual((33_001, 0, 0), (held['accepted'], held['written'], held['known_dropped']))
            self.assertEqual((8 * 1024**3, 5_000_000), (held['memory_limit_bytes'], held['event_capacity']))
            self.assertIsNone(trace.token())
            self.assertEqual(8 * 1024**3, held['storage_limit_bytes'])
            self.assertIsNone(trace.input_token('collector_inputs'))
            trace.emit(identifier, 'call_end', {'call_id': 'after-capture'})
            self.assertEqual(held['accepted'], trace.status()['accepted'])
            directory = trace._directory() / identifier
            self.assertEqual(['manifest.json'], sorted(path.name for path in directory.iterdir()))
            with self.assertRaisesRegex(RuntimeError, 'diagnostic_run_busy'):
                with diagnostic_run_lock(control):
                    pass
            with self.assertRaisesRegex(ValueError, 'recorded_capture_incomplete_or_old_schema'):
                trace.recorded_events(identifier)
            _set_tool(control, False)  # Does not discard RAM or cause an early flush.
            self.assertEqual(0, trace.status()['written'])
            self.assertGreater(held['packed_events'], 0)
            with patch.object(trace.time, 'sleep'):
                with trace._LOCK:
                    trace._SESSION['persist_at'] = time.time() - 1
                trace._WAKE.set()
                final = wait_state('complete', 15)
            self.assertEqual((33_001, 33_001, 0), (final['accepted'], final['written'], final['queued']))
            self.assertEqual(final['captured_mono_ns'], final['finished_mono_ns'])
            self.assertGreater(final['persisted_at'], final['captured_at'])
            rows = [json.loads(line) for chunk in final['chunks']
                    for line in trace.chunk_bytes(identifier, chunk['name']).splitlines()]
            self.assertEqual(list(range(1, 33_002)), [row['seq'] for row in rows])
            payload = json.loads(trace.payload_bytes(identifier, rows[0]['payload_ref']))
            self.assertEqual({'price': 123}, thaw_payload(payload))
            disk = json.loads((directory / 'manifest.json').read_text())
            self.assertEqual('complete', disk['state'])
            self.assertEqual(final['written'], disk['written'])

    def test_shutdown_before_deadline_does_not_force_payload_io(self):
        with deferred_capture() as (identifier, _):
            self.assertTrue(trace.emit_payload(identifier, 'collector_input',
                                               {'workload_id': 'realtime'}, {'value': 123}))
            final = trace.stop('server_shutdown', timeout=15)
            self.assertEqual('interrupted', final['state'])
            self.assertEqual(0, final['written'])
            self.assertTrue(final['input_capture_censored'])
            self.assertEqual(0, final['charged_bytes'])
            self.assertEqual(['manifest.json'], sorted(path.name for path in
                             (trace._directory() / identifier).iterdir()))

    def test_expiry_seals_capture_without_waiting_for_master_to_remain_enabled(self):
        with deferred_capture() as (identifier, control):
            trace.emit(identifier, 'call_end', {'call_id': 'before-expiry'})
            with trace._LOCK:
                trace._SESSION['expires_at'] = time.time() - 1
            trace._WAKE.set()
            held = wait_state('awaiting_persistence')
            self.assertEqual('expired', held['reason'])
            _set_tool(control, False)
            trace.emit(identifier, 'call_end', {'call_id': 'after-expiry'})
            self.assertEqual(1, trace.status()['accepted'])
            self.assertEqual(0, trace.status()['written'])

    def test_event_capacity_and_memory_budget_are_independent(self):
        for reason in ('event_capacity', 'memory_budget'):
            with deferred_capture() as (identifier, _), patch.object(trace, '_CAPACITY',
                    0 if reason == 'event_capacity' else 1_000_000):
                if reason == 'memory_budget':
                    with trace._LOCK:
                        trace._SESSION['memory_limit_bytes'] = trace._PACK_WORKER_RESERVE + 1
                trace.emit(identifier, 'call_end', {'call_id': 'refused'})
                failed = trace.status()
                self.assertEqual((1, 0, 0), (failed['known_dropped'], failed['accepted'], failed['charged_bytes']))
                self.assertEqual({reason: 1}, failed['drop_reasons'])
                self.assertIsNone(trace.token())
                trace.emit(identifier, 'call_end', {'call_id': 'closed'})
                self.assertEqual(1, trace.status()['known_dropped'])

    def test_writes_are_paced_in_small_blocks_without_fsync_stall_credit(self):
        session = {'state': 'persisting', 'write_bytes_per_second': 1024 * 1024,
                   'persistence_throttle_seconds': 0.0}
        output = io.BytesIO()
        content = b'x' * (2 * 64 * 1024 + 123)
        with patch.object(trace.time, 'sleep') as sleep:
            trace._write(output, content, session)
            self.assertEqual([64 / 1024, 64 / 1024, 123 / 1024**2],
                             [call.args[0] for call in sleep.call_args_list])
            trace._write(output, b'z' * 64 * 1024, session)
            self.assertEqual(64 / 1024, sleep.call_args.args[0])
        self.assertEqual(content + b'z' * 64 * 1024, output.getvalue())

    def test_invalid_deadline_is_rejected_before_memory_or_control_access(self):
        with patch.object(trace, '_deferred_memory_check') as check:
            for deadline in (True, float('inf'), float('nan'), time.time() + 1, time.time() + 90000):
                with self.assertRaisesRegex(ValueError, 'trace_persistence_time_out_of_bounds'):
                    trace.start(seconds=60, persist_at=deadline)
            check.assert_not_called()

    def test_host_and_container_headroom_both_gate_eight_gib_capture(self):
        def read(path):
            values = {'/proc/meminfo': 'MemAvailable: 12582912 kB\n',
                      '/sys/fs/cgroup/memory.max': str(9 * 1024**3),
                      '/sys/fs/cgroup/memory.current': str(1024**3)}
            return values[str(path).replace('\\', '/')]
        with patch.object(Path, 'read_text', read):
            with self.assertRaisesRegex(ValueError, 'trace_memory_headroom_insufficient'):
                trace._deferred_memory_check()

    def test_unavailable_container_limit_fails_closed(self):
        def read(path):
            if str(path).replace('\\', '/') == '/proc/meminfo':
                return 'MemAvailable: 16777216 kB\\n'
            raise OSError('cgroup memory files unavailable')

        with patch.object(Path, 'read_text', read):
            with self.assertRaisesRegex(ValueError, 'trace_container_memory_headroom_unavailable'):
                trace._deferred_memory_check()

    def test_eight_gib_budget_requires_one_gib_extra_on_host_and_container(self):
        required = 9 * 1024**3
        for host, container, passes in ((required, required, True),
                                        (required - 1024, required, False),
                                        (required, required - 1, False)):
            with self.subTest(host=host, container=container):
                def read(path):
                    values = {'/proc/meminfo': f'MemAvailable: {host // 1024} kB\n',
                              '/sys/fs/cgroup/memory.max': str(container + 1024**3),
                              '/sys/fs/cgroup/memory.current': str(1024**3)}
                    return values[str(path).replace('\\', '/')]
                with patch.object(Path, 'read_text', read):
                    if passes:
                        trace._deferred_memory_check()
                    else:
                        with self.assertRaisesRegex(ValueError, 'trace_memory_headroom_insufficient'):
                            trace._deferred_memory_check()


if __name__ == '__main__':
    unittest.main()
