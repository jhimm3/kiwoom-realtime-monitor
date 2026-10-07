from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.research_process import discover_campaign_inputs, execute_campaign_cycle
from test_research_campaign_execution import write_campaign_request


class CampaignInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _, self.request = write_campaign_request(self.root)
        self.scope = {'captured_range': {'start': '2026-09-12T00:00:00+00:00', 'end': '2026-09-12T01:00:00+00:00'}, 'kinds': ['top20_membership'], 'subject': ''}
        path = self.request.dataset / 'manifest.json'
        manifest = json.loads(path.read_text())
        path.write_text(json.dumps({**manifest, **self.scope}), encoding='utf-8')
        self.watch = self.root / 'prepared'
        self.watch.mkdir()
        self.repo = ResearchRepository(self.request.database)
        self.repo.create_campaign('c', 'research', ResearchCampaignPolicy())
        self.repo.enqueue_campaign_experiment('c', self.request.search, self.request.dataset)
        self.job = self.repo.load_campaign_jobs('c')[0]['job_id']
        self.source_id = self.repo.save_campaign_input_source('c', self.job, self.watch)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.claim = self.repo.claim_campaign_worker('c', owner_token='worker', lease_seconds=3600)
        self.now = datetime.now(UTC)

    def candidate(self, name='new', *, rows=True, scope=None):
        path = self.watch / name
        shutil.copytree(self.request.dataset, path)
        values = [{'ordinal': 1, 'revision_id': 'rank-' + name, 'kind': 'top20_membership', 'available_at': '2026-09-12T00:00:00+00:00', 'accepted_sequence': 1, 'observation_key': '2026-09-12T00:00:00+00:00', 'payload': {'codes': ['005930']}}] if rows else []
        encoded = ''.join(json.dumps(row) + '\n' for row in values).encode()
        (path / 'observations.jsonl').write_bytes(encoded)
        manifest = json.loads((path / 'manifest.json').read_text())
        manifest.update(scope or {})
        manifest.update(dataset_id=name, fixed_watermark=name, revision_count=len(values), revision_ids_hash=hashlib.sha256('\n'.join(row['revision_id'] for row in values).encode()).hexdigest(), observations_file_hash=hashlib.sha256(encoded).hexdigest())
        (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        return path

    def scan(self, seconds=0, **kwargs):
        return discover_campaign_inputs(self.repo, 'c', self.claim, now=self.now + timedelta(seconds=seconds), **kwargs)

    def test_related_input_registers_once_and_executes_same_hypothesis(self):
        self.candidate()
        self.assertEqual(1, self.scan()['registered'])
        new = self.repo.load_campaign_jobs('c')[1]
        self.assertEqual('new_data', new['source_kind'])
        self.assertEqual(self.request.search.research_context, new['request']['research_context'])
        self.assertEqual('COMPLETED', execute_campaign_cycle(self.repo, 'c', self.request.runs_dir, worker_claim=self.claim)['cycle_outcome'])
        self.assertEqual('COMPLETED', execute_campaign_cycle(self.repo, 'c', self.request.runs_dir, worker_claim=self.claim)['cycle_outcome'])
        self.assertEqual(1, self.scan(61)['unchanged'])
        self.assertEqual(2, len(self.repo.load_campaign_jobs('c')))

    def test_watermark_only_change_does_not_create_new_evidence(self):
        self.candidate(rows=False)
        result = self.scan()
        self.assertEqual(0, result['registered'])
        self.assertEqual(1, result['unchanged'])
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))

    def test_duplicate_evidence_in_different_folders_is_not_registered_twice(self):
        path = self.candidate()
        shutil.copytree(path, self.watch / 'copy')
        result = self.scan()
        self.assertEqual(1, result['registered'])
        self.assertEqual(1, result['unchanged'])

    def test_new_day_and_unrelated_subject_or_kinds_are_out_of_scope(self):
        self.candidate('day', scope={'captured_range': {'start': '2026-09-13T00:00:00+00:00', 'end': '2026-09-13T01:00:00+00:00'}})
        self.candidate('stock', scope={'subject': '000660'})
        self.candidate('kind', scope={'kinds': ['program_trade']})
        self.assertEqual(3, self.scan()['out_of_scope'])
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))

    def test_scan_is_throttled_and_restart_preserves_acceptances(self):
        self.candidate()
        self.scan()
        self.repo = ResearchRepository(self.request.database)
        with patch('kiwoom_monitor.research_process.load_research_input') as load:
            self.assertEqual(0, self.scan(59)['registered'])
            load.assert_not_called()
        with patch('kiwoom_monitor.research_process.load_research_input') as load:
            self.assertEqual(1, self.scan(61)['unchanged'])
            load.assert_not_called()

    def test_changed_accepted_manifest_is_rejected(self):
        path = self.candidate()
        self.scan()
        manifest = json.loads((path / 'manifest.json').read_text())
        manifest['fixed_watermark'] = 'changed'
        (path / 'manifest.json').write_text(json.dumps(manifest))
        result = self.scan(61)
        self.assertIn('modified', result['errors'][0])
        self.assertEqual(2, len(self.repo.load_campaign_jobs('c')))

    def test_corrupt_input_is_isolated_and_retried_until_manual_reset(self):
        path = self.candidate()
        (path / 'observations.jsonl').write_bytes(b'corrupt')
        self.assertEqual(1, len(self.scan()['errors']))
        self.assertEqual(1, len(self.scan(29)['errors']))
        self.scan(31)
        self.scan(92)
        source = self.repo.load_campaign_input_sources('c')[0]
        self.assertEqual('NEEDS_ATTENTION', source['state'])
        self.assertEqual(3, source['failure_count'])
        self.assertEqual('RUNNING', self.repo.load_campaign('c')['desired_state'])
        self.assertEqual('COMPLETED', execute_campaign_cycle(self.repo, 'c', self.request.runs_dir, worker_claim=self.claim)['cycle_outcome'])
        self.assertEqual(0, self.repo.load_campaign_worker('c')['failure_count'])

    def test_disabling_source_requires_pause_and_survives_restart(self):
        with self.assertRaisesRegex(ValueError, 'pause'):
            self.repo.save_campaign_input_source('c', self.job, self.watch, enabled=False)
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.repo.finish_campaign_worker('c', owner_token='worker', generation=self.claim['generation'], outcome='EXPECTED_EXIT')
        self.repo.save_campaign_input_source('c', self.job, self.watch, enabled=False)
        self.repo = ResearchRepository(self.request.database)
        self.assertFalse(self.repo.load_campaign_input_sources('c')[0]['enabled'])
        self.assertEqual(0, self.scan()['registered'])

    def test_cancel_and_owner_loss_prevent_registration(self):
        self.candidate()
        self.assertEqual(0, self.scan(cancel_requested=lambda: True)['registered'])
        self.repo.finish_campaign_worker('c', owner_token='worker', generation=self.claim['generation'], outcome='FAILED')
        result = self.scan(61)
        self.assertEqual(0, result['registered'])
        self.assertIn('no longer active', result['errors'][0])

    def test_acceptance_insert_failure_rolls_back_job_and_budget(self):
        self.scan()
        self.candidate()
        with closing(sqlite3.connect(self.request.database)) as connection, connection:
            connection.execute("CREATE TRIGGER fail_acceptance BEFORE INSERT ON research_campaign_input_acceptances BEGIN SELECT RAISE(ABORT,'acceptance failed'); END")
        self.assertIn('acceptance failed', self.scan(61)['errors'][0])
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))
        self.assertEqual(1, len(self.repo.load_campaign_input_acceptances(self.source_id)))

    def test_missing_watch_folder_does_not_fail_existing_research(self):
        self.watch.rmdir()
        self.assertEqual(1, len(self.scan()['errors']))
        self.assertEqual('COMPLETED', execute_campaign_cycle(self.repo, 'c', self.request.runs_dir, worker_claim=self.claim)['cycle_outcome'])

    def test_unregistered_sources_do_not_load_job_history_or_inputs(self):
        with patch.object(self.repo, 'load_campaign_input_sources', return_value=()), patch.object(self.repo, 'load_campaign_jobs') as jobs:
            self.scan()
        jobs.assert_not_called()

    def test_irrelevant_program_revision_does_not_trigger_research(self):
        path = self.candidate()
        data = json.loads((path / 'observations.jsonl').read_text())
        data['kind'] = 'program_trade'
        encoded = (json.dumps(data) + '\n').encode()
        (path / 'observations.jsonl').write_bytes(encoded)
        manifest = json.loads((path / 'manifest.json').read_text())
        manifest['observations_file_hash'] = hashlib.sha256(encoded).hexdigest()
        (path / 'manifest.json').write_text(json.dumps(manifest))
        self.assertEqual(1, self.scan()['unchanged'])
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))

    def test_active_backlog_limit_preserves_unaccepted_input_for_retry(self):
        self.candidate()
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.repo.finish_campaign_worker('c', owner_token='worker', generation=self.claim['generation'], outcome='EXPECTED_EXIT')
        self.repo.revise_campaign_policy('c', ResearchCampaignPolicy(max_active_jobs=1), expected_revision=1)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.claim = self.repo.claim_campaign_worker('c', owner_token='worker', lease_seconds=3600)
        self.assertEqual(1, self.scan()['waiting_backlog'])
        self.assertEqual(0, self.repo.load_campaign_input_sources('c')[0]['failure_count'])
        self.assertEqual(1, len(self.repo.load_campaign_input_acceptances(self.source_id)))
        execute_campaign_cycle(self.repo, 'c', self.request.runs_dir, worker_claim=self.claim)
        self.assertEqual(1, self.scan(61)['registered'])

    def test_pause_between_load_and_registration_rejects_candidate(self):
        self.candidate()
        original = self.repo.enqueue_campaign_experiment
        def pause_before_enqueue(*args, **kwargs):
            self.repo.set_campaign_desired_state('c', 'PAUSED')
            return original(*args, **kwargs)
        with patch.object(self.repo, 'enqueue_campaign_experiment', side_effect=pause_before_enqueue):
            self.assertEqual(0, self.scan()['registered'])
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))

    def test_source_configuration_can_change_folder_without_creating_second_source(self):
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.repo.finish_campaign_worker('c', owner_token='worker', generation=self.claim['generation'], outcome='EXPECTED_EXIT')
        other = self.root / 'other'
        self.assertEqual(self.source_id, self.repo.save_campaign_input_source('c', self.job, other))
        sources = self.repo.load_campaign_input_sources('c')
        self.assertEqual(1, len(sources))
        self.assertEqual(str(other.resolve()), sources[0]['root'])


class RollingDailyInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        from test_research_process import _request_document
        original = _request_document()
        original['execution']['cost_model']['valid_to'] = '2026-09-20T00:00:00+00:00'
        with patch('test_research_campaign_execution._request_document', return_value=original):
            _, self.request = write_campaign_request(self.root)
        manifest_path = self.request.dataset / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest.update(captured_range={'start': '2026-09-12T00:00:00+00:00', 'end': '2026-09-12T01:00:00+00:00'},
                        kinds=['top20_membership', 'minute_bar'], subject='')
        manifest_path.write_text(json.dumps(manifest))
        self.watch = self.root / 'rolling'
        self.watch.mkdir()
        self.repo = ResearchRepository(self.request.database)
        self.repo.create_campaign('c', 'rolling', ResearchCampaignPolicy())
        self.repo.enqueue_campaign_experiment('c', self.request.search, self.request.dataset)
        self.job = self.repo.load_campaign_jobs('c')[0]['job_id']
        self.source_id = self.repo.save_campaign_input_source('c', self.job, self.watch, rolling_daily=True)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.claim = self.repo.claim_campaign_worker('c', owner_token='rolling-worker', lease_seconds=3600)
        self.now = datetime.now(UTC)

    def candidate(self, name, day, *, include_bar=True, subject=''):
        path = self.watch / name
        shutil.copytree(self.request.dataset, path)
        start = f'2026-09-{day:02d}T00:00:00+00:00'
        end = f'2026-09-{day:02d}T01:00:00+00:00'
        rows = [{'ordinal': 1, 'revision_id': f'{name}-membership', 'kind': 'top20_membership',
                 'venue': 'KRX', 'available_at': start, 'payload': {'codes': ['005930']}}]
        if include_bar:
            rows.append({'ordinal': 2, 'revision_id': f'{name}-bar', 'kind': 'minute_bar',
                         'venue': 'KRX', 'available_at': f'2026-09-{day:02d}T00:02:00+00:00',
                         'payload': {'code': '005930', 'bar_start': f'2026-09-{day:02d}T00:01:00+00:00',
                                     'bar_end': f'2026-09-{day:02d}T00:02:00+00:00'}})
        encoded = ''.join(json.dumps(row) + '\n' for row in rows).encode()
        (path / 'observations.jsonl').write_bytes(encoded)
        manifest_path = path / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest.update(dataset_id=name, captured_range={'start': start, 'end': end}, subject=subject,
                        revision_count=len(rows), revision_ids_hash=hashlib.sha256('\n'.join(row['revision_id'] for row in rows).encode()).hexdigest(),
                        observations_file_hash=hashlib.sha256(encoded).hexdigest())
        manifest_path.write_text(json.dumps(manifest))
        return path

    def test_closed_new_day_registers_distinct_evaluation_without_mutating_template(self):
        self.candidate('next-day', 13)
        result = discover_campaign_inputs(self.repo, 'c', self.claim, now=self.now)
        self.assertEqual(1, result['registered'])
        new = self.repo.load_campaign_jobs('c')[1]
        self.assertEqual('2026-09-13T00:00:00+00:00', new['request']['research_context']['evaluation']['folds'][0]['start'])
        self.assertEqual('2026-09-12T00:00:00+00:00', self.repo.load_campaign_jobs('c')[0]['request']['research_context']['evaluation']['folds'][0]['start'])
        self.assertEqual(0, discover_campaign_inputs(self.repo, 'c', self.claim, now=self.now + timedelta(seconds=61))['registered'])
        self.assertEqual(2, len(self.repo.load_campaign_jobs('c')))

    def test_empty_day_and_other_subject_are_excluded(self):
        self.candidate('no-bar', 13, include_bar=False)
        self.candidate('other-stock', 14, subject='000660')
        result = discover_campaign_inputs(self.repo, 'c', self.claim, now=self.now)
        self.assertEqual(0, result['registered'])
        self.assertEqual(2, result['out_of_scope'])
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))

    def test_expired_cost_period_does_not_register_new_day(self):
        self.candidate('after-cost-validity', 21)
        result = discover_campaign_inputs(self.repo, 'c', self.claim, now=self.now)
        self.assertEqual(0, result['registered'])
        self.assertEqual('cost_validity_does_not_cover_new_day', result['rolling_rejections'][0]['reason'])
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))

    def test_new_manifest_cannot_relabel_old_day_bar_as_new_day_evidence(self):
        path = self.candidate('relabeled-bar', 13)
        rows = [json.loads(line) for line in (path / 'observations.jsonl').read_text().splitlines()]
        rows[1]['payload']['bar_start'] = '2026-09-12T00:01:00+00:00'
        rows[1]['payload']['bar_end'] = '2026-09-12T00:02:00+00:00'
        encoded = ''.join(json.dumps(row) + '\n' for row in rows).encode()
        (path / 'observations.jsonl').write_bytes(encoded)
        manifest_path = path / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['observations_file_hash'] = hashlib.sha256(encoded).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        result = discover_campaign_inputs(self.repo, 'c', self.claim, now=self.now)
        self.assertEqual(0, result['registered'])
        self.assertEqual('trading_day_evidence_missing', result['rolling_rejections'][0]['reason'])

    def test_independent_final_input_cannot_be_registered_as_a_new_development_day(self):
        path = self.candidate('final-input', 13)
        manifest_path = path / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['runtime_input_version'] = 'independent_final_holdout_input/v1'
        manifest_path.write_text(json.dumps(manifest))
        result = discover_campaign_inputs(self.repo, 'c', self.claim, now=self.now)
        self.assertEqual(0, result['registered'])
        self.assertEqual('independent_or_final_input_cannot_roll', result['rolling_rejections'][0]['reason'])

    def test_same_day_cannot_be_mislabeled_as_a_new_rolling_window(self):
        self.candidate('same-day', 12)
        result = discover_campaign_inputs(self.repo, 'c', self.claim, now=self.now)
        self.assertEqual(1, result['registered'])
        new = self.repo.load_campaign_jobs('c')[1]
        self.assertEqual('2026-09-12T00:00:00+00:00', new['request']['research_context']['evaluation']['folds'][0]['start'])

    def test_rolling_accepts_nas_auto_prepare_for_development_template(self):
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.repo.finish_campaign_worker('c', owner_token='rolling-worker', generation=self.claim['generation'], outcome='EXPECTED_EXIT')
        self.repo.save_campaign_input_source('c', self.job, self.watch, rolling_daily=True,
                                             nas_auto_prepare=True, nas_config_path=self.root / 'source.json')
        source = self.repo.load_campaign_input_sources('c')[0]
        self.assertEqual(1, source['nas_auto_prepare'])
        self.assertEqual('', source['rolling_next_start'])


if __name__ == '__main__':
    unittest.main()
