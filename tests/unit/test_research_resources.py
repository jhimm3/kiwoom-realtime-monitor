from __future__ import annotations
import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.research_resources import (
    ResearchResourceGuard, ResearchResourceLimits, ResearchResourceBlocked, current_rss_bytes,
)
from kiwoom_monitor.application.research_replay import ResearchReplayCursor, replay_krx_minute_bars, replay_candidate_universe
from kiwoom_monitor.infrastructure.research_data_source import research_observation_order, research_input_encoded_bytes
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.research_process import execute_process_request, load_research_process_request
from test_research_bundle_execution import rows_for, PROFILE
from test_research_process import _request_document, _write_empty_dataset


class ResearchResourcesTests(unittest.TestCase):
    def test_real_platform_rss_is_positive(self):
        self.assertGreater(current_rss_bytes(), 0)

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
