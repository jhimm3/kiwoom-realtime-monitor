"""Real scheduler -> authenticated ASGI -> native recorder; private SQLite, no lifespan.

Postgres-configured API advertises its native capability, but its injected store is
SQLite. This covers start wiring and durable arguments, not NAS/PG/resource acceptance.
"""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scripts import nas_scheduled_trace as scheduler
from scripts.compare_recorded_capture_overhead import AccountLargeFixture
from tests.unit.test_diagnostic_trace_api import request_api, HEADERS
from tests.unit.test_diagnostic_trace_deferred import recorder_storage_headroom, wait_state
from kiwoom_monitor.central_server import app as app_module, diagnostic_trace as trace
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.diagnostic_replay_contract import thaw_operation_arguments
from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub


class ScheduledTraceApiTests(unittest.TestCase):
    def test_five_opt_ins_from_scheduler_persist_native_account_vi_and_one_large_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = root / 'control.json'
            (root / 'source-runtime').mkdir()
            # Recorder truthfully reports image_or_local for this source location.
            (root / 'source-runtime/active.json').write_text(json.dumps({'release_id': 'image_or_local'}))
            store = SQLiteQueryStore(root / 'fixture.sqlite3')
            store.initialize()
            fixture = AccountLargeFixture(store, datetime.now(timezone.utc), distinct_large_inputs=True)
            settings = CentralServerSettings('postgresql://private-fixture/no-connection', 'private-token')
            calls = []
            with patch.dict(os.environ, {'KIWOOM_DIAGNOSTIC_WORKLOAD_PATH': str(control)}), \
                    patch.object(app_module, 'create_query_store', return_value=store), \
                    recorder_storage_headroom(), patch.object(trace, '_deferred_memory_check',
                        return_value={'bounded_start_wiring_fixture': True}):
                app = app_module.create_app(settings)
                _set_tool(control, False)
                class AsgiApi:
                    def request(self, path, body=None, method='GET'):
                        calls.append((path, method, body))
                        response = request_api(app, method, path, headers=HEADERS,
                                               **({'json': body} if body is not None else {}))
                        if response.status_code != 200:
                            raise AssertionError((method, path, response.status_code, response.text))
                        return response.json()
                target = time.time() + .15
                plan = dict(start_at=datetime.fromtimestamp(target, timezone.utc).isoformat(),
                    persist_at=datetime.fromtimestamp(target + 3600, timezone.utc).isoformat(),
                    seconds=60, build=app_module.SERVER_BUILD, release='image_or_local',
                    memory_limit_bytes=8 * 1024**3, event_capacity=5_000_000,
                    write_bytes_per_second=1024**2, maximum_lateness_seconds=30,
                    capture_flags=list(scheduler.FLAGS + scheduler.EXTRA_FLAGS))
                try:
                    started = scheduler.start(AsgiApi(), root, plan, scheduler.wait_until)
                    self.assertEqual(4, started['trace']['schema_version'])
                    self.assertTrue(all(started['trace']['payload_capture'][x] for x in plan['capture_flags']))
                    self.assertEqual(['PUT', 'POST'], [m for _, m, _ in calls if m != 'GET'])
                    self.assertTrue(all(trace.input_token(x) == started['trace_id'] for x in plan['capture_flags']))
                    fixture.batch(0)
                    collector = CentralRealtimeCollector(lambda: '', 'real', RealtimeHub(),
                                                          lambda: fixture.origin, store)
                    collector._publish_parsed({'trnm': 'REAL', 'data': [
                        {'type': '0B', 'item': '005930_AL', 'values': {'10': '70000', '15': '1', '20': '090000'}}]})
                    response = request_api(app, 'POST', '/api/v1/diagnostics/trace/stop', headers=HEADERS)
                    self.assertEqual(200, response.status_code)
                    held = wait_state('awaiting_persistence')
                    self.assertEqual((0, 0, 0), (held['written'], held['known_dropped'], held['input_rejected']))
                    with trace._LOCK:
                        trace._SESSION['persist_at'] = time.time() - 1
                    trace._WAKE.set()
                    final = wait_state('complete', 45)
                    self.assertEqual(final['accepted'], final['written'])
                    self.assertEqual(0, final['charged_bytes'])
                    manifest, rows = trace.recorded_events(started['trace_id'])
                    starts = [r for r in rows if r['event_type'] == 'operation_start']
                    ends = [r for r in rows if r['event_type'] == 'operation_end']
                    self.assertEqual(sum(fixture.counts.values()), len(starts))
                    self.assertEqual({r['operation_id'] for r in starts}, {r['operation_id'] for r in ends})
                    for method, count in fixture.counts.items():
                        self.assertEqual(count, sum(r['method'] == method for r in starts))
                    large = next(r for r in starts if r['method'] == 'save_shadow_monitor_state')
                    arguments = thaw_operation_arguments(large)
                    self.assertEqual(fixture.last_large, arguments['document'])
                    self.assertEqual(1, sum(r['method'] == 'save_shadow_monitor_state' for r in starts))
                    self.assertGreater(manifest['blobs'][large['payload'].digest]['bytes'], 8 * 1024**2)
                    self.assertTrue(any(r.get('input_kind') == 'message' for r in rows))
                    self.assertNotIn('private-authority', json.dumps(rows, default=str))
                    from kiwoom_monitor.central_server.diagnostic_account_context import read_recorded_account_context
                    self.assertEqual('account-context/v2', read_recorded_account_context(started['trace_id'])['version'])
                    fixture.signature()  # Content/revisions and released lease are checked by the native fixture.
                finally:
                    trace.stop('server_shutdown', timeout=15)
                    _set_tool(control, False)
                    store.close()


if __name__ == '__main__':
    unittest.main()
