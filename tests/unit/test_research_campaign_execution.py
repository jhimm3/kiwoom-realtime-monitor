from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
from kiwoom_monitor.application.research_resources import ResearchResourceBlocked
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.research_process import execute_campaign_cycle, load_research_process_request, run_campaign_worker
from scripts.run_research import ResearchRunCancelled, execute_research
from test_research_process import _request_document, _write_empty_dataset


def write_campaign_request(root: Path, *, max_trials=4):
    _write_empty_dataset(root)
    document = _request_document()
    document['mode'] = 'limited_search'
    document['search'] = {
        'version': 'limited_search/v2', 'hypothesis_refs': ['h1'],
        'dataset_id': 'empty', 'dataset_hash': hashlib.sha256(b'').hexdigest(),
        'family_allowlist': ['krx_bar_close_breakout/v1'],
        'factor_allowlist': ['rolling_high_breakout/v1'],
        'parameter_space': {'target_bps': [400, 500]},
        'objective': {'net_pnl_won': 'maximize'}, 'constraints': {},
        'split_version': 'chronological_holdout/v1', 'max_trials': max_trials,
        'max_seconds': 60, 'seed': 11,
    }
    path = root / 'request.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    return path, load_research_process_request(path)


class CampaignExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path, self.request = write_campaign_request(self.root)
        self.repo = ResearchRepository(self.request.database)
        self.repo.create_campaign('c', '연구', ResearchCampaignPolicy())
        self.repo.enqueue_campaign_experiment('c', self.request.search, self.request.dataset)
        self.repo.set_campaign_desired_state('c', 'RUNNING')

    def execute(self, **kwargs):
        return execute_campaign_cycle(self.repo, 'c', self.request.runs_dir, **kwargs)

    def test_stored_request_executes_after_external_request_is_deleted(self):
        self.path.unlink()
        result = self.execute()
        self.assertEqual('COMPLETED', result['cycle_outcome'])
        self.assertTrue(result['cycle_accepted'])
        self.assertEqual('SEARCH_SPACE_EXHAUSTED', result['campaign']['operational_state'])
        self.assertEqual(4, len(self.repo.load_search_trials(self.request.search.experiment_id)))
        self.assertNotIn('cycle_sequence', self.execute())
        self.assertEqual(1, len(self.repo.load_campaign_cycles('c')))

    def test_pause_during_trial_cancels_attempt_without_saving_a_candidate(self):
        def pause(*args, **kwargs):
            self.repo.set_campaign_desired_state('c', 'PAUSED')
            raise ResearchRunCancelled('paused')
        with patch('kiwoom_monitor.research_process.execute_research', side_effect=pause):
            result = self.execute()
        self.assertEqual('INTERRUPTED', result['cycle_outcome'])
        self.assertEqual('PAUSED', result['campaign']['desired_state'])
        self.assertEqual((), self.repo.load_search_trials(self.request.search.experiment_id))
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.assertEqual('COMPLETED', self.execute()['cycle_outcome'])
        self.assertEqual(2, len(self.repo.load_campaign_cycles('c')))

    def test_process_cancel_preserves_running_intent(self):
        cancel = self.root / 'cancel'
        cancel.touch()
        result = self.execute(cancel_path=cancel)
        self.assertEqual('INTERRUPTED', result['cycle_outcome'])
        self.assertEqual('RUNNING', result['campaign']['desired_state'])
        self.assertEqual('PENDING', self.repo.load_campaign_jobs('c')[0]['state'])

    def test_pause_at_result_commit_is_rejected_without_waiting_for_heartbeat(self):
        def pause_after_simulation(*args, **kwargs):
            run = execute_research(*args, **kwargs)
            self.repo.set_campaign_desired_state('c', 'PAUSED')
            return run
        with patch('kiwoom_monitor.research_process.execute_research', side_effect=pause_after_simulation):
            result = self.execute()
        self.assertEqual('INTERRUPTED', result['cycle_outcome'])
        self.assertTrue(result['cycle_accepted'])
        self.assertEqual((), self.repo.load_search_trials(self.request.search.experiment_id))
        self.assertEqual((), self.repo.load_candidate_cards(self.request.search.experiment_id))

    def test_campaign_takeover_at_commit_rejects_old_cycle_and_trial_result(self):
        from datetime import UTC, datetime, timedelta
        def takeover_after_simulation(*args, **kwargs):
            run = execute_research(*args, **kwargs)
            claimed = self.repo.claim_campaign_cycle('c', owner_token='replacement',
                                                      now=datetime.now(UTC) + timedelta(days=1))
            self.assertIsNotNone(claimed)
            return run
        with patch('kiwoom_monitor.research_process.execute_research', side_effect=takeover_after_simulation):
            result = self.execute()
        self.assertEqual('INTERRUPTED', result['cycle_outcome'])
        self.assertFalse(result['cycle_accepted'])
        self.assertEqual((), self.repo.load_search_trials(self.request.search.experiment_id))
        self.assertEqual('replacement', self.repo.load_campaign_jobs('c')[0]['owner_token'])

    def test_resource_preflight_blocks_without_automatic_retry(self):
        with patch('kiwoom_monitor.research_process.ResearchResourceGuard.preflight', side_effect=ResearchResourceBlocked('memory')):
            result = self.execute()
        self.assertEqual('RESOURCE_BLOCKED', result['cycle_outcome'])
        self.assertEqual('RESOURCE_BLOCKED', result['campaign']['operational_state'])
        self.assertNotIn('cycle_sequence', self.execute())

    def test_input_tampering_is_failed_with_persistent_backoff(self):
        (self.request.dataset / 'observations.jsonl').write_text('{}\n', encoding='utf-8')
        result = self.execute()
        self.assertEqual('FAILED', result['cycle_outcome'])
        job = self.repo.load_campaign_jobs('c')[0]
        self.assertEqual(1, job['failure_count'])
        self.assertTrue(job['next_attempt_at'])
        self.assertNotIn('cycle_sequence', self.execute())

    def test_stored_implementation_context_mismatch_is_rejected(self):
        with patch('kiwoom_monitor.research_process.research_implementation_hash', return_value='different'):
            result = self.execute()
        self.assertEqual('FAILED', result['cycle_outcome'])
        self.assertIn('locked request', self.repo.load_campaign_jobs('c')[0]['reason'])

    def test_manual_unsealed_final_request_cannot_enter_automatic_campaign(self):
        from dataclasses import replace
        spec = replace(self.request.search, final_holdout_accessed_at='2026-09-16T00:00:00+00:00',
                       final_holdout_access_reason='manual final')
        with self.assertRaisesRegex(ValueError, 'automatic final evaluation is disabled'):
            self.repo.enqueue_campaign_experiment('c', spec, self.request.dataset)
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))

    def test_worker_waiting_preserves_last_table_and_stops_on_db_intent(self):
        calls = 0
        def wait(_):
            nonlocal calls
            calls += 1
            if calls == 11:
                self.repo.set_campaign_desired_state('c', 'PAUSED')
        result_path = self.root / 'result.json'
        with patch('kiwoom_monitor.research_process.time.sleep', side_effect=wait):
            result = run_campaign_worker(self.request.database, 'c', self.request.runs_dir, result_path)
        self.assertEqual('PAUSED', result['campaign']['desired_state'])
        self.assertEqual(4, len(result['last_result']['candidate_cards']))
        self.assertEqual(1, len(self.repo.load_campaign_cycles('c')))
        self.assertTrue(result_path.is_file())

    def test_cancelled_worker_without_claim_does_not_change_intent(self):
        cancel = self.root / 'cancel'
        cancel.touch()
        result = run_campaign_worker(self.request.database, 'c', self.request.runs_dir, self.root / 'result', cancel_path=cancel)
        self.assertEqual('RUNNING', result['campaign']['desired_state'])
        self.assertEqual((), self.repo.load_campaign_cycles('c'))

    def test_real_worker_process_executes_and_exits_on_persistent_pause(self):
        import os
        import subprocess
        import sys
        import time
        result_path = self.root / 'child_result.json'
        process = subprocess.Popen([
            sys.executable, '-m', 'kiwoom_monitor.research_process', '--campaign', 'c',
            '--database', str(self.request.database), '--runs-dir', str(self.request.runs_dir),
            '--result', str(result_path),
        ], cwd=Path(__file__).resolve().parents[2], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = self.repo.load_campaign_jobs('c')[0]['state']
                if state == 'COMPLETED' or process.poll() is not None:
                    break
                time.sleep(.05)
            self.repo.set_campaign_desired_state('c', 'PAUSED')
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(0, process.returncode, stderr or stdout)
            result = json.loads(result_path.read_text(encoding='utf-8'))
            self.assertEqual('PAUSED', result['campaign']['desired_state'])
            self.assertEqual(4, len(result['last_result']['candidate_cards']))
            self.assertEqual(1, len(self.repo.load_campaign_cycles('c')))
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    def test_limited_trial_budget_is_not_reported_as_full_space_completion(self):
        from dataclasses import replace
        small = replace(self.request.search, max_trials=2)
        self.repo.create_campaign('small', '제한', ResearchCampaignPolicy())
        self.repo.enqueue_campaign_experiment('small', small, self.request.dataset)
        self.repo.set_campaign_desired_state('small', 'RUNNING')
        result = execute_campaign_cycle(self.repo, 'small', self.request.runs_dir)
        self.assertEqual('COMPLETED', result['cycle_outcome'])
        self.assertEqual('NEEDS_ATTENTION', result['campaign']['operational_state'])
        self.assertEqual('trial_budget_exhausted', result['campaign']['reason'])
        waiting = self.execute()
        self.assertEqual('NEEDS_ATTENTION', waiting['campaign']['operational_state'])
        self.assertEqual('budget_expansion_required', waiting['campaign']['reason'])
        self.repo.create_campaign('larger', '확대', ResearchCampaignPolicy())
        with self.assertRaisesRegex(ValueError, 'explicit budget expansion'):
            self.repo.enqueue_campaign_experiment('larger', self.request.search, self.request.dataset)
        self.assertEqual((), self.repo.load_campaign_jobs('larger'))


if __name__ == '__main__':
    unittest.main()
