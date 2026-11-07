from __future__ import annotations
import ctypes
import errno
import hashlib
import json
import struct
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace
from kiwoom_monitor.application import research_resources as resources

from kiwoom_monitor.application.research_resources import (
    ResearchResourceGuard, ResearchResourceLimits, ResearchResourceBlocked, current_rss_bytes,
    _windows_rss_api,
)
from kiwoom_monitor.application.research_replay import ResearchReplayCursor, replay_krx_minute_bars, replay_candidate_universe
from kiwoom_monitor.infrastructure.research_data_source import research_observation_order, research_input_encoded_bytes
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.research_process import execute_process_request, load_research_process_request
from test_research_bundle_execution import rows_for, PROFILE
from research_test_support import (
    research_request_document as _request_document,
    write_empty_dataset as _write_empty_dataset,
)


class ResearchResourcesTests(unittest.TestCase):
    def test_real_platform_rss_is_positive(self):
        self.assertGreater(current_rss_bytes(), 0)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows process memory API')
    def test_windows_rss_reuses_native_api_bindings(self):
        first = _windows_rss_api()
        for _ in range(1000):
            self.assertGreater(current_rss_bytes(), 0)
        self.assertIs(first, _windows_rss_api())
        self.assertEqual(_windows_rss_api.cache_info().misses, 1)

    def test_darwin_rss_reads_resident_bytes_with_cached_fixed_width_binding(self):
        # Darwin proc_taskinfo: six uint64 values followed by twelve int32s.
        raw = struct.pack('=6Q12i', 1 << 40, 123_456_789, *([0] * 16))

        def query(pid, flavor, argument, buffer, size):
            self.assertEqual((resources.os.getpid(), 4, 0, 96), (pid, flavor, argument, size))
            ctypes.memmove(buffer, raw, len(raw))
            return len(raw)

        native = Mock(side_effect=query)
        resources._darwin_rss_api.cache_clear()
        try:
            with patch.object(resources.sys, 'platform', 'darwin'), patch.object(
                    resources.ctypes, 'CDLL', return_value=SimpleNamespace(proc_pidinfo=native)) as load:
                self.assertEqual(123_456_789, current_rss_bytes())
                self.assertEqual(123_456_789, current_rss_bytes())
            load.assert_called_once_with('/usr/lib/libproc.dylib', use_errno=True)
            self.assertEqual(ctypes.c_int, native.restype)
            self.assertEqual([ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int], native.argtypes)
        finally:
            resources._darwin_rss_api.cache_clear()

    def test_darwin_rss_incomplete_and_denied_reads_keep_resource_blocked(self):
        def query(*_arguments):
            ctypes.set_errno(errno.EPERM)
            return returned[0]

        resources._darwin_rss_api.cache_clear()
        try:
            with patch.object(resources.sys, 'platform', 'darwin'), patch.object(
                    resources.ctypes, 'CDLL', return_value=SimpleNamespace(proc_pidinfo=Mock(side_effect=query))):
                for count in (0, 95):
                    returned = [count]
                    with self.subTest(returned=count), self.assertRaisesRegex(ResearchResourceBlocked, 'unavailable'):
                        ResearchResourceGuard(ResearchResourceLimits()).check()
        finally:
            resources._darwin_rss_api.cache_clear()

    def test_limits_and_missing_rss_fail_closed(self):
        for kwargs in ({'memory_mb': 127}, {'cpu_duty_percent': 0}, {'batch_seconds': 1}):
            with self.assertRaises(ValueError):
                ResearchResourceLimits(**kwargs)
        guard = ResearchResourceGuard(ResearchResourceLimits(), rss=lambda: (_ for _ in ()).throw(OSError()))
        with self.assertRaisesRegex(ResearchResourceBlocked, 'unavailable'):
            guard.check()

    def test_preflight_and_actual_rss_are_both_enforced(self):
        used = [40 * 1048576]
        guard = ResearchResourceGuard(ResearchResourceLimits(memory_mb=128), rss=lambda: used[0])
        self.assertEqual(1024, guard.preflight(1024))
        with self.assertRaisesRegex(ResearchResourceBlocked, 'input_memory_estimate'):
            guard.preflight(12 * 1048576)
        used[0] = 129 * 1048576
        with self.assertRaisesRegex(ResearchResourceBlocked, 'memory_rss'):
            guard.checkpoint()

    def test_short_batches_throttle_cpu_time_and_not_io_wait(self):
        wall, cpu, pauses = [0.0], [0.0], []
        guard = ResearchResourceGuard(ResearchResourceLimits(cpu_duty_percent=50), rss=lambda: 1024,
                                      monotonic=lambda: wall[0], process_clock=lambda: cpu[0], sleeper=pauses.append)
        wall[0], cpu[0] = 0.06, 0.05
        guard.checkpoint()
        self.assertAlmostEqual(0.04, pauses[-1])
        wall[0] = 3
        guard.checkpoint()
        self.assertEqual(0, pauses[-1])
        wall[0], cpu[0] = 3.1, 4
        guard.checkpoint()
        self.assertEqual(0.2, pauses[-1])

    def test_cursor_matches_full_prefix_including_invalid_and_late_corrections(self):
        rows = rows_for(date(2026, 9, 14), count=5) + rows_for(date(2026, 9, 15), count=2)
        valid = rows[3]
        rows.append(dict(valid, revision_id='invalid-correction', accepted_sequence=100,
                         available_at='2026-09-15T09:01:30+09:00', completeness='partial'))
        rows.append(dict(valid, revision_id='valid-again', accepted_sequence=101,
                         available_at='2026-09-15T09:02:30+09:00'))
        rows = tuple(sorted(rows, key=research_observation_order))
        cursor = ResearchReplayCursor(rows, session_profile=PROFILE)
        for index, row in enumerate(rows):
            bars, universe = cursor.advance(research_observation_order(row))
            prefix = rows[:index + 1]
            cutoff = datetime.fromisoformat(row['available_at'])
            self.assertEqual(replay_krx_minute_bars(prefix, as_of=cutoff, session_profile=PROFILE), bars)
            self.assertEqual(replay_candidate_universe(prefix, as_of=cutoff, chronological=True), universe)

    def test_target_cursor_matches_same_code_day_full_replay_after_correction(self):
        rows = rows_for(date(2026, 9, 14), count=3)
        other = dict(rows[2], revision_id='other-code', subject='000660:KRX', accepted_sequence=20,
                     payload=dict(rows[2]['payload'], code='000660'))
        invalid = dict(rows[2], revision_id='invalid-correction', accepted_sequence=21,
                       available_at='2026-09-14T09:04:00+09:00', completeness='partial')
        rows.extend((other, invalid))
        rows.extend(rows_for(date(2026, 9, 15), count=1))
        rows = tuple(sorted(rows, key=research_observation_order))
        cursor = ResearchReplayCursor(rows, session_profile=PROFILE)
        for index, row in enumerate(rows):
            if row.get('kind') != 'minute_bar':
                continue
            current, history, _ = cursor.advance_for_observation(row)
            prefix = rows[:index + 1]
            cutoff = datetime.fromisoformat(row['available_at'])
            full = replay_krx_minute_bars(prefix, as_of=cutoff, session_profile=PROFILE)
            expected_current = next((bar for bar in full if bar.revision_id == row['revision_id']), None)
            self.assertEqual(expected_current, current)
            if current is not None:
                expected_history = tuple(bar for bar in full if bar.code == current.code
                                         and datetime.fromisoformat(bar.bar_start).date()
                                         == datetime.fromisoformat(current.bar_start).date())
                self.assertEqual(expected_history, history)

    def test_checkpoint_due_probe_reuses_batch_cadence_without_rss_io(self):
        wall, reads = [0.0], []
        limits = ResearchResourceLimits()
        guard = ResearchResourceGuard(limits, rss=lambda: reads.append("rss") or 1024,
            monotonic=lambda: wall[0], process_clock=lambda: 0.0, sleeper=lambda _: None)
        for _ in range(100):
            self.assertFalse(guard.checkpoint_due())
        self.assertEqual([], reads)
        wall[0] = limits.batch_seconds * 2
        self.assertTrue(guard.checkpoint_due())
        self.assertEqual([], reads)
        guard.checkpoint()
        self.assertEqual(["rss"], reads)
        self.assertFalse(guard.checkpoint_due())

    def test_invalid_order_stays_before_native_run_start_and_leaves_no_footprint(self):
        from contextlib import closing
        from kiwoom_monitor import research_run as runner
        from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset
        from tests.unit.test_research_execution import _strategy, _execution
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = rows_for(date(2026, 9, 14))
            rows[0]['available_at'] = '2026-09-14T09:00:00'
            raw = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
            dataset = FrozenResearchDataset({'dataset_id': 'invalid-order-boundary',
                'revision_ids_hash': hashlib.sha256(raw).hexdigest()}, tuple(rows))
            repository = ResearchRepository(root / 'research.sqlite3')
            with patch.object(repository, 'start_run', wraps=repository.start_run) as start:
                with self.assertRaisesRegex(ValueError, 'availability/order contract is invalid'):
                    runner.execute_research(dataset, repository, root / 'runs', _strategy(), _execution(),
                        session_profile=PROFILE)
            self.assertEqual(0, start.call_count)
            with closing(repository._connect()) as connection:
                self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM research_runs').fetchone()[0])
            self.assertEqual([], list((root / 'runs').rglob('manifest.json')))
            self.assertEqual(raw, json.dumps(dataset.observations, sort_keys=True, separators=(",", ":")).encode())

    def test_operational_hook_error_keeps_original_exception_and_failed_run_footprint(self):
        import sqlite3
        from contextlib import closing
        from kiwoom_monitor import research_run as runner
        from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset
        from tests.unit.test_research_execution import _strategy, _execution
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = tuple(rows_for(date(2026, 9, 14)))
            raw = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
            dataset = FrozenResearchDataset({'dataset_id': 'operational-hook-boundary',
                'revision_ids_hash': hashlib.sha256(raw).hexdigest()}, rows)
            repository = ResearchRepository(root / 'research.sqlite3')
            failures = []
            def cancelled():
                with closing(sqlite3.connect(root / 'control.sqlite3')) as control:
                    try:
                        control.execute('SELECT generation FROM checkpoint_lease').fetchone()
                    except sqlite3.OperationalError as exc:
                        failures.append(exc)
                        raise
            with self.assertRaises(sqlite3.OperationalError) as caught:
                runner.execute_research(dataset, repository, root / 'runs', _strategy(), _execution(),
                    session_profile=PROFILE, cancel_requested=cancelled)
            self.assertEqual(1, len(failures))
            self.assertIs(failures[0], caught.exception)
            with closing(repository._connect()) as connection:
                connection.row_factory = sqlite3.Row
                saved = connection.execute('SELECT status,finished_at,error FROM research_runs').fetchall()
            self.assertEqual(1, len(saved))
            self.assertEqual('failed', saved[0]['status'])
            self.assertTrue(saved[0]['finished_at'])
            self.assertEqual('OperationalError: ' + str(caught.exception), saved[0]['error'])
            self.assertEqual([], list((root / 'runs').rglob('manifest.json')))
            self.assertEqual(raw, json.dumps(dataset.observations, sort_keys=True, separators=(",", ":")).encode())

    def test_preflight_failure_never_creates_output_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            path = root / 'request.json'
            path.write_text(json.dumps(_request_document()), encoding='utf-8')
            self.assertGreater(research_input_encoded_bytes(root / 'dataset'), 0)
            request = load_research_process_request(path)
            with patch.object(ResearchResourceGuard, 'check', side_effect=ResearchResourceBlocked('fixture_limit')):
                with self.assertRaises(ResearchResourceBlocked):
                    execute_process_request(request)
            self.assertFalse(request.database.exists())

    def test_search_resource_block_is_an_interrupted_attempt_and_can_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_empty_dataset(root)
            document = _request_document()
            document['mode'] = 'limited_search'
            document['search'] = {
                'version': 'limited_search/v2', 'hypothesis_refs': ['fixture'], 'dataset_id': 'empty',
                'dataset_hash': hashlib.sha256(b'').hexdigest(),
                'family_allowlist': ['krx_bar_close_breakout/v1'],
                'factor_allowlist': ['rolling_high_breakout/v1', 'rank_persistence/v1'],
                'parameter_space': {'target_bps': [400]}, 'objective': {'net_pnl_won': 'maximize'},
                'constraints': {}, 'split_version': 'chronological_holdout/v1',
                'max_trials': 1, 'max_seconds': 60, 'seed': 11,
            }
            path = root / 'request.json'
            path.write_text(json.dumps(document), encoding='utf-8')
            request = load_research_process_request(path)
            with patch('kiwoom_monitor.research_process.execute_research', side_effect=ResearchResourceBlocked('fixture_limit')):
                result = execute_process_request(request)
            self.assertEqual('resource_blocked', result['job_status'])
            repository = ResearchRepository(request.database)
            self.assertEqual((), repository.load_search_trials(request.search.experiment_id))
            attempts = repository.load_trial_attempts(request.search.experiment_id)
            self.assertEqual('INTERRUPTED', attempts[0]['status'])
            resumed = execute_process_request(request)
            self.assertEqual('completed', resumed['job_status'])
            self.assertEqual(1, len(repository.load_search_trials(request.search.experiment_id)))


if __name__ == '__main__':
    unittest.main()
