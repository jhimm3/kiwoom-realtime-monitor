from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import StringIO
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch

import test_research_partition_search as search_fixture
from test_research_campaign_execution import write_campaign_request
from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
from kiwoom_monitor.application.research_search import ExperimentSpec
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository, _MIGRATIONS
from kiwoom_monitor.infrastructure.persistence.schema_migrations import SQLiteMigrationRunner
from kiwoom_monitor.research_process import (
    discover_campaign_inputs, execute_campaign_cycle, execute_process_request,
    register_campaign_request, main,
)
from scripts.run_research import ResearchRunCancelled


class PartitionCampaignTests(unittest.TestCase):
    def setUp(self):
        self.fixture = search_fixture.PartitionSearchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.request, self.dataset = self.fixture.request()
        self.root = self.fixture.fixture.root
        self.repo = ResearchRepository(self.request.database)
        self.repo.create_campaign('c', 'partition', ResearchCampaignPolicy())

    def register(self, request=None):
        return register_campaign_request(self.repo, 'c', request or self.request)

    def execute(self, **kwargs):
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        return execute_campaign_cycle(self.repo, 'c', self.request.runs_dir, **kwargs)

    def test_stored_source_reconstructs_after_request_file_is_removed(self):
        registration = self.register()
        work = self.repo.load_campaign_jobs('c')[0]
        self.assertEqual(self.request.search.to_dict(), work['source_request'])
        self.assertNotEqual(work['request']['dataset_id'], work['source_request']['dataset_id'])
        self.assertEqual(registration['job_id'], work['job_id'])
        self.assertEqual('train', work['request']['research_context']['development_partition']['fold_name'])
        (self.root / 'request.json').unlink()
        result = self.execute()
        self.assertEqual('COMPLETED', result['cycle_outcome'])
        self.assertEqual(registration['experiment_id'], result['last_result']['experiment_id'])
        self.assertEqual(4, result['last_result']['attempted_now'])
        self.assertNotIn('FAILED', [card['status'] for card in result['last_result']['candidate_cards']])
        self.assertEqual('NEEDS_ATTENTION', result['campaign']['operational_state'])
        self.assertEqual('trial_budget_exhausted', result['campaign']['reason'])
        self.assertNotIn('cycle_sequence', self.execute())

    def test_finite_completion_is_reused_without_second_execution(self):
        finite = execute_process_request(self.request)
        registration = self.register()
        self.assertEqual(finite['experiment_id'], registration['experiment_id'])
        self.assertEqual('COMPLETED', self.repo.load_campaign_jobs('c')[0]['state'])
        self.assertNotIn('cycle_sequence', self.execute())

    def test_pause_retry_preserves_source_and_runtime_identity(self):
        registration = self.register()
        source_json = self.repo.load_campaign_jobs('c')[0]['source_request_json']
        def pause(*args, **kwargs):
            self.repo.set_campaign_desired_state('c', 'PAUSED')
            raise ResearchRunCancelled('fixture pause')
        with patch('kiwoom_monitor.research_process.execute_research', side_effect=pause):
            interrupted = self.execute()
        self.assertEqual('INTERRUPTED', interrupted['cycle_outcome'])
        self.assertEqual((), self.repo.load_search_trials(registration['experiment_id']))
        finished = self.execute()
        self.assertEqual('COMPLETED', finished['cycle_outcome'])
        work = self.repo.load_campaign_jobs('c')[0]
        self.assertEqual(source_json, work['source_request_json'])
        self.assertEqual(registration['job_id'], work['job_id'])
        self.assertEqual(4, len(self.repo.load_search_trials(registration['experiment_id'])))

    def test_budget_revision_merges_into_both_views_without_changing_frozen_source(self):
        small = replace(self.request, search=replace(self.request.search, max_trials=2))
        registration = self.register(small)
        self.assertEqual('COMPLETED', self.execute()['cycle_outcome'])
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        before = self.repo.load_campaign_jobs('c')[0]
        effective = replace(ExperimentSpec.from_dict(before['request']), max_trials=4)
        self.assertEqual(2, self.repo.revise_campaign_job_budget('c', registration['job_id'], effective, expected_revision=1))
        updated = self.repo.load_campaign_jobs('c')[0]
        self.assertEqual(before['source_request_json'], updated['source_request_json'])
        self.assertEqual(before['request_json'], updated['request_json'])
        self.assertEqual(4, updated['source_request']['max_trials'])
        self.assertEqual(4, updated['request']['max_trials'])
        finished = self.execute()
        self.assertEqual('COMPLETED', finished['cycle_outcome'])
        self.assertEqual(2, finished['last_result']['attempted_now'])
        self.assertEqual(registration['experiment_id'], finished['last_result']['experiment_id'])

    def test_active_input_change_is_rejected_before_any_trial_or_run_write(self):
        registration = self.register()
        changed = deepcopy(self.dataset)
        row = next(row for row in changed.observations if row['kind'] == 'minute_bar' and row['payload']['bar_start'] == self.fixture.fixture.at(5))
        row['payload']['close'] += 1
        self.fixture.write_changed_source(self.request, changed)
        result = self.execute()
        self.assertEqual('FAILED', result['cycle_outcome'])
        self.assertIn('stored campaign identity', result['cycle_reason'])
        self.assertEqual((), self.repo.load_search_trials(registration['experiment_id']))
        with closing(self.repo._connect()) as connection:
            self.assertEqual(0, connection.execute('SELECT COUNT(*) FROM research_runs').fetchone()[0])

    def source(self):
        registration = self.register()
        watch = self.root / 'watch'
        watch.mkdir()
        source_id = self.repo.save_campaign_input_source('c', registration['job_id'], watch)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        claim = self.repo.claim_campaign_worker('c', owner_token='worker', lease_seconds=3600)
        return registration, watch, source_id, claim

    def candidate(self, watch, name, *, active=False):
        path = watch / name
        shutil.copytree(self.request.dataset, path)
        changed = deepcopy(self.dataset)
        changed.manifest['dataset_id'] = name
        for row in changed.observations:
            if row['kind'] != 'minute_bar':
                continue
            if (active and row['payload']['bar_start'] == self.fixture.fixture.at(5)) or (not active and datetime.fromisoformat(row['available_at']) >= datetime.fromisoformat(self.fixture.fixture.at(22))):
                row['payload']['close'] += 5
                row['revision_id'] += '-changed'
        self.fixture.write_changed_source(replace(self.request, dataset=path), changed)
        return path

    def test_source_final_only_change_is_accepted_without_new_job_or_overwriting_source(self):
        registration, watch, source_id, claim = self.source()
        before = self.repo.load_campaign_jobs('c')[0]
        path = self.candidate(watch, 'final-only')
        result = discover_campaign_inputs(self.repo, 'c', claim)
        self.assertEqual([], result['errors'])
        self.assertEqual(0, result['registered'])
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))
        self.assertEqual(before['source_request_json'], self.repo.load_campaign_jobs('c')[0]['source_request_json'])
        with closing(self.repo._connect()) as connection:
            acceptance = connection.execute('SELECT job_id FROM research_campaign_input_acceptances WHERE input_path=?', (str(path.resolve()),)).fetchone()
        self.assertEqual(registration['job_id'], acceptance[0])
        again = discover_campaign_inputs(self.repo, 'c', claim, now=datetime.now(UTC) + timedelta(seconds=61))
        self.assertEqual([], again['errors'])
        self.assertEqual(0, again['registered'])

    def test_source_active_change_registers_new_runtime_and_original_pair_and_executes(self):
        registration, watch, _, claim = self.source()
        path = self.candidate(watch, 'new-active', active=True)
        result = discover_campaign_inputs(self.repo, 'c', claim)
        self.assertEqual([], result['errors'])
        self.assertEqual(1, result['registered'])
        jobs = self.repo.load_campaign_jobs('c')
        self.assertEqual(2, len(jobs))
        self.assertNotEqual(registration['job_id'], jobs[1]['job_id'])
        self.assertEqual('new-active', jobs[1]['source_request']['dataset_id'])
        self.assertEqual(str(path.resolve()), jobs[1]['input_path'])
        self.assertEqual('COMPLETED', self.execute()['cycle_outcome'])
        next_result = self.execute()
        self.assertEqual('COMPLETED', next_result['cycle_outcome'])
        self.assertEqual(jobs[1]['job_id'], next_result['last_result']['job_id'])

    def test_equivalent_source_after_budget_revision_uses_current_budget_view(self):
        _, watch, _, claim = self.source()
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        job = self.repo.load_campaign_jobs('c')[0]
        expanded = replace(ExperimentSpec.from_dict(job['request']), max_trials=8)
        self.repo.revise_campaign_job_budget('c', job['job_id'], expanded, expected_revision=1)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.candidate(watch, 'equivalent-after-budget')
        result = discover_campaign_inputs(self.repo, 'c', claim)
        self.assertEqual([], result['errors'])
        self.assertEqual(0, result['registered'])
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))

    def test_registration_cancel_and_source_identity_error_enqueue_nothing(self):
        with self.assertRaises(ResearchRunCancelled):
            register_campaign_request(self.repo, 'c', self.request, cancel_requested=lambda: True)
        bad = replace(self.request, search=replace(self.request.search, dataset_id='wrong'))
        with self.assertRaisesRegex(ValueError, 'source dataset identity'):
            self.register(bad)
        self.assertEqual((), self.repo.load_campaign_jobs('c'))

    def test_repository_rejects_wrong_source_runtime_contract(self):
        registration = self.register()
        effective = ExperimentSpec.from_dict(self.repo.load_campaign_jobs('c')[0]['request'])
        with self.assertRaisesRegex(ValueError, 'does not match source request'):
            self.repo.enqueue_campaign_experiment('c', replace(effective, seed=99), self.request.dataset, source_spec=self.request.search)
        self.assertEqual(registration['job_id'], self.repo.load_campaign_jobs('c')[0]['job_id'])

    def test_registration_rejects_wrong_split_contract_before_enqueue(self):
        bad = replace(self.request, search=replace(self.request.search, split_version='unregistered'))
        with self.assertRaisesRegex(ValueError, 'split version'):
            self.register(bad)
        self.assertEqual((), self.repo.load_campaign_jobs('c'))

    def test_cli_registers_without_running_trials(self):
        output = self.root / 'registration.json'
        with patch('sys.stdout', new=StringIO()):
            code = main(['--request', str(self.root / 'request.json'), '--register-campaign', 'c', '--result', str(output)])
        self.assertEqual(0, code)
        result = json.loads(output.read_text())
        self.assertEqual('campaign_registration', result['kind'])
        self.assertEqual((), self.repo.load_search_trials(result['experiment_id']))
        self.assertEqual('PAUSED', self.repo.load_campaign('c')['desired_state'])

    def test_v16_upgrade_preserves_legacy_job_and_reconstructs_same_source(self):
        legacy_root = self.root / 'legacy'
        legacy_root.mkdir()
        _, request = write_campaign_request(legacy_root)
        current = ResearchRepository(request.database)
        current.create_campaign('legacy', 'legacy', ResearchCampaignPolicy())
        current.enqueue_campaign_experiment('legacy', request.search, request.dataset)
        old_path = legacy_root / 'v16.sqlite3'
        tables = ('research_campaigns', 'research_campaign_revisions', 'research_search_experiments',
                  'research_search_jobs', 'research_campaign_jobs', 'research_campaign_job_budgets')
        with closing(sqlite3.connect(old_path)) as old, old, closing(current._connect()) as source:
            SQLiteMigrationRunner(old, table='research_schema_migrations').apply(_MIGRATIONS[:16])
            for table in tables:
                columns = ','.join(row[1] for row in old.execute(f'PRAGMA table_info({table})'))
                rows = source.execute(f'SELECT {columns} FROM {table}').fetchall()
                old.executemany(f'INSERT INTO {table}({columns}) VALUES({",".join("?" for _ in rows[0])})', rows)
            before = old.execute('SELECT request_json,input_path,state FROM research_campaign_jobs').fetchall()
            migration_rows = old.execute('SELECT * FROM research_schema_migrations').fetchall()
        migrated = ResearchRepository(old_path)
        self.assertEqual(23, migrated.schema_version())
        with closing(sqlite3.connect(old_path)) as connection:
            self.assertEqual(before, connection.execute('SELECT request_json,input_path,state FROM research_campaign_jobs').fetchall())
            self.assertEqual(migration_rows, connection.execute('SELECT * FROM research_schema_migrations WHERE version<=16').fetchall())
        job = migrated.load_campaign_jobs('legacy')[0]
        self.assertEqual('', job['source_request_json'])
        self.assertEqual(job['request'], job['source_request'])
        migrated.set_campaign_desired_state('legacy', 'RUNNING')
        result = execute_campaign_cycle(migrated, 'legacy', request.runs_dir)
        self.assertEqual('COMPLETED', result['cycle_outcome'])
