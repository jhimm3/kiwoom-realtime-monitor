from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy, build_research_job_identity
from kiwoom_monitor.application.research_resources import ResearchResourceBlocked
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository, _MIGRATIONS
from kiwoom_monitor.infrastructure.persistence.schema_migrations import SQLiteMigrationRunner
from kiwoom_monitor.research_process import execute_campaign_cycle, execute_process_request
from test_research_campaign_execution import write_campaign_request


class CampaignBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path, self.request = write_campaign_request(self.root, max_trials=2)
        self.spec = self.request.search
        self.job_id = build_research_job_identity(self.spec).job_id
        self.repo = ResearchRepository(self.request.database)
        self.repo.create_campaign('c', '연구', ResearchCampaignPolicy())
        self.repo.enqueue_campaign_experiment('c', self.spec, self.request.dataset)

    def work(self):
        return self.repo.load_campaign_jobs('c')[0]

    def revise(self, **changes):
        return self.repo.revise_campaign_job_budget('c', self.job_id, replace(self.spec, **changes), expected_revision=1)

    def execute(self):
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        result = execute_campaign_cycle(self.repo, 'c', self.request.runs_dir)
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        return result

    def test_initial_revision_matches_immutable_spec(self):
        revisions = self.repo.load_campaign_job_budget_revisions('c', self.job_id)
        self.assertEqual(23, self.repo.schema_version())
        self.assertEqual(1, len(revisions))
        self.assertEqual(2, revisions[0]['budget']['max_trials'])
        self.assertEqual(1, self.work()['budget_revision'])

    def test_expansion_reuses_first_results_and_preserves_finite_completed_cache(self):
        first = self.execute()
        cards = self.repo.load_candidate_cards(self.spec.experiment_id)
        trials = self.repo.load_search_trials(self.spec.experiment_id)
        reports = {card['run_id']: self.repo.load_research_report(card['run_id']) for card in cards}
        original_json = self.work()['request_json']
        self.assertEqual(2, self.revise(max_trials=4))
        finite = execute_process_request(replace(self.request, search=replace(self.spec, max_trials=4)))
        self.assertEqual(0, finite['attempted_now'])
        self.assertEqual(2, finite['budget']['used_trials'])
        second = self.execute()
        self.assertEqual('COMPLETED', second['cycle_outcome'])
        self.assertEqual(2, second['last_result']['attempted_now'])
        self.assertEqual(4, second['last_result']['budget']['used_trials'])
        self.assertEqual('SEARCH_SPACE_EXHAUSTED', second['campaign']['operational_state'])
        self.assertEqual(cards, self.repo.load_candidate_cards(self.spec.experiment_id)[:2])
        self.assertEqual(trials, self.repo.load_search_trials(self.spec.experiment_id)[:2])
        self.assertEqual(original_json, self.work()['request_json'])
        self.assertEqual(reports, {key: self.repo.load_research_report(key) for key in reports})
        cycles = self.repo.load_campaign_cycles('c')
        self.assertEqual([1, 2], [row['budget_revision'] for row in cycles])
        self.assertEqual(self.spec.experiment_id, second['last_result']['experiment_id'])
        self.assertEqual('trial_budget_exhausted', first['campaign']['reason'])

    def test_identical_edit_is_idempotent(self):
        self.assertEqual(1, self.revise())
        self.assertEqual(1, len(self.repo.load_campaign_job_budget_revisions('c', self.job_id)))

    def test_explicit_save_adopts_existing_target_after_other_smaller_job_completed(self):
        full = replace(self.spec, max_trials=4)
        self.repo.create_campaign('full', '전체', ResearchCampaignPolicy())
        self.repo.enqueue_campaign_experiment('full', full, self.request.dataset)
        self.execute()
        self.repo.set_campaign_desired_state('full', 'RUNNING')
        self.assertIsNone(self.repo.claim_campaign_cycle('full', owner_token='check'))
        self.assertEqual('budget_expansion_required', self.repo.load_campaign_jobs('full')[0]['reason'])
        self.repo.set_campaign_desired_state('full', 'PAUSED')
        self.assertEqual(2, self.repo.revise_campaign_job_budget('full', self.job_id, full, expected_revision=1))
        self.repo.set_campaign_desired_state('full', 'RUNNING')
        result = execute_campaign_cycle(self.repo, 'full', self.request.runs_dir)
        self.assertEqual(2, result['last_result']['attempted_now'])
        self.assertEqual(4, result['last_result']['budget']['used_trials'])

    def test_scientific_change_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'scientific evidence'):
            self.revise(parameter_space={'target_bps': (600, 700)})
        self.assertEqual(1, self.work()['budget_revision'])

    def test_generated_candidate_limit_cannot_be_changed_by_budget_edit(self):
        with self.assertRaisesRegex(ValueError, 'preserve generated candidates'):
            self.revise(resource_budget={**self.spec.resource_budget, 'max_generated_candidates': 2000})

    def test_trial_limit_cannot_be_reduced(self):
        with self.assertRaisesRegex(ValueError, 'not reduce trial limit'):
            self.revise(max_trials=1)

    def test_running_desired_state_rejects_edit(self):
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        with self.assertRaisesRegex(ValueError, 'pause campaign'):
            self.revise(max_trials=4)

    def test_paused_but_live_campaign_owner_rejects_edit(self):
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.repo.claim_campaign_cycle('c', owner_token='live')
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        with self.assertRaisesRegex(ValueError, 'active worker'):
            self.revise(max_trials=4)

    def test_other_live_search_owner_rejects_edit(self):
        self.repo.start_search_job(self.job_id, owner_token='other', lease_seconds=60)
        with self.assertRaisesRegex(ValueError, 'active worker'):
            self.revise(max_trials=4)

    def test_expired_cycle_is_closed_and_old_worker_cannot_start(self):
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        old = self.repo.claim_campaign_cycle('c', owner_token='old', now=datetime.now(UTC) - timedelta(days=1))
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.revise(max_trials=4)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.assertIsNone(self.repo.start_search_job(self.job_id, owner_token='old-search', lease_seconds=60, campaign_cycle=old))
        self.assertEqual('queued', self.repo.load_search_job(self.job_id)['status'])
        self.assertEqual('INTERRUPTED', self.repo.load_campaign_cycles('c')[0]['state'])
        self.assertFalse(self.repo.finish_campaign_cycle('c', old['sequence'], owner_token=old['owner_token'],
                                                       generation=old['generation'], outcome='INTERRUPTED'))

    def test_campaign_backlog_failure_rolls_back_budget_history(self):
        self.execute()
        self.repo.revise_campaign_policy('c', ResearchCampaignPolicy(max_active_jobs=1), expected_revision=1)
        other = replace(self.spec, hypothesis_refs=('other',))
        self.repo.enqueue_campaign_experiment('c', other, self.request.dataset)
        before = self.work()
        with self.assertRaisesRegex(ValueError, 'campaign active backlog'):
            self.revise(max_trials=4)
        self.assertEqual(before, self.work())
        self.assertEqual(1, len(self.repo.load_campaign_job_budget_revisions('c', self.job_id)))

    def test_global_backlog_failure_rolls_back_budget_history(self):
        self.execute()
        self.repo.create_campaign('other', '별도', ResearchCampaignPolicy())
        self.repo.enqueue_campaign_experiment('other', replace(self.spec, hypothesis_refs=('other',)), self.request.dataset)
        with self.assertRaisesRegex(ValueError, 'research active backlog'):
            self.revise(max_trials=4, resource_budget={**self.spec.resource_budget, 'max_retained_jobs': 1})
        self.assertEqual(1, self.work()['budget_revision'])

    def test_budget_insert_failure_rolls_back_job_and_revision(self):
        before = self.work()
        with closing(sqlite3.connect(self.request.database)) as connection, connection:
            connection.execute("CREATE TRIGGER fail_budget BEFORE INSERT ON research_campaign_job_budgets WHEN NEW.revision=2 BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'disk failure'):
            self.revise(max_trials=4)
        self.assertEqual(before, self.work())
        self.assertEqual(1, len(self.repo.load_campaign_job_budget_revisions('c', self.job_id)))

    def test_reopen_and_search_claim_are_one_transaction(self):
        self.execute()
        self.revise(max_trials=4)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        cycle = self.repo.claim_campaign_cycle('c', owner_token='cycle')
        before = self.repo.load_search_job(self.job_id)
        events = self.repo.load_search_job_events(self.job_id)
        with closing(sqlite3.connect(self.request.database)) as connection, connection:
            connection.execute("CREATE TRIGGER fail_start BEFORE UPDATE ON research_search_jobs WHEN NEW.status='running' BEGIN SELECT RAISE(ABORT,'claim failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'claim failure'):
            self.repo.start_search_job(self.job_id, owner_token='search', lease_seconds=60, campaign_cycle=cycle)
        self.assertEqual(before, self.repo.load_search_job(self.job_id))
        self.assertEqual(events, self.repo.load_search_job_events(self.job_id))

    def test_cancelled_retry_does_not_overfill_global_backlog(self):
        generation = self.repo.start_search_job(self.job_id, owner_token='previous', lease_seconds=60)
        self.repo.finish_search_job(self.job_id, 'cancelled', {}, owner_token='previous', generation=generation)
        self.repo.create_campaign('other', '별도', ResearchCampaignPolicy())
        self.repo.enqueue_campaign_experiment('other', replace(self.spec, hypothesis_refs=('other',)), self.request.dataset)
        self.revise(resource_budget={**self.spec.resource_budget, 'max_retained_jobs': 1})
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        cycle = self.repo.claim_campaign_cycle('c', owner_token='cycle')
        with self.assertRaisesRegex(ValueError, 'research active backlog'):
            self.repo.start_search_job(self.job_id, owner_token='retry', lease_seconds=60, campaign_cycle=cycle)
        self.assertEqual('cancelled', self.repo.load_search_job(self.job_id)['status'])

    def test_two_edits_accept_only_one_expected_revision(self):
        def edit(limit):
            try:
                return self.repo.revise_campaign_job_budget('c', self.job_id, replace(self.spec, max_trials=limit), expected_revision=1)
            except ValueError:
                return 'stale'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, (3, 4)))
        self.assertCountEqual((2, 'stale'), results)
        self.assertEqual(2, len(self.repo.load_campaign_job_budget_revisions('c', self.job_id)))

    def test_two_search_workers_open_expanded_job_only_once(self):
        self.execute()
        self.revise(max_trials=4)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        cycle = self.repo.claim_campaign_cycle('c', owner_token='cycle')
        def start(owner):
            return self.repo.start_search_job(self.job_id, owner_token=owner, lease_seconds=60, campaign_cycle=cycle)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(start, ('one', 'two')))
        self.assertEqual(1, sum(value is not None for value in results))
        self.assertEqual(2, self.repo.load_search_job(self.job_id)['attempt_count'])

    def test_forged_captured_budget_cannot_reopen_completed_job(self):
        self.execute()
        self.revise(max_trials=4)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        cycle = self.repo.claim_campaign_cycle('c', owner_token='cycle')
        self.assertIsNone(self.repo.start_search_job(self.job_id, owner_token='stale', lease_seconds=60,
                                                   campaign_cycle={**cycle, 'budget_revision': 1}))
        forged = {**cycle, 'request': {**cycle['request'], 'max_trials': 6}}
        with self.assertRaisesRegex(ValueError, 'captured request'):
            self.repo.start_search_job(self.job_id, owner_token='forged', lease_seconds=60, campaign_cycle=forged)
        self.assertEqual('completed', self.repo.load_search_job(self.job_id)['status'])

    def test_edit_and_running_claim_capture_one_consistent_revision(self):
        def edit():
            try:
                return self.revise(max_trials=4)
            except ValueError:
                return 'running'
        def claim():
            self.repo.set_campaign_desired_state('c', 'RUNNING')
            return self.repo.claim_campaign_cycle('c', owner_token='racer')
        with ThreadPoolExecutor(max_workers=2) as pool:
            edit_future = pool.submit(edit)
            claim_future = pool.submit(claim)
            revision, cycle = edit_future.result(), claim_future.result()
        self.assertEqual(2 if revision == 2 else 1, cycle['budget_revision'])
        self.assertEqual(4 if revision == 2 else 2, cycle['request']['max_trials'])

    def test_resource_edit_releases_resource_blocked_work(self):
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        with patch('kiwoom_monitor.research_process.ResearchResourceGuard.preflight', side_effect=ResearchResourceBlocked('memory')):
            execute_campaign_cycle(self.repo, 'c', self.request.runs_dir)
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.revise(resource_budget={**self.spec.resource_budget, 'memory_mb': 1024})
        self.assertEqual('PENDING', self.work()['state'])
        result = self.execute()
        self.assertEqual(1024, result['last_result']['budget']['resource_budget']['memory_mb'])

    def test_restart_uses_latest_budget_without_original_request(self):
        self.execute()
        self.revise(max_trials=4)
        self.path.unlink()
        self.repo = ResearchRepository(self.request.database)
        self.assertEqual(4, self.work()['request']['max_trials'])
        self.assertEqual(4, self.execute()['last_result']['budget']['used_trials'])

    def test_explicit_expansion_of_legacy_completion_checks_committed_trial_rows(self):
        generation = self.repo.start_search_job(self.job_id, owner_token='legacy', lease_seconds=60)
        self.repo.finish_search_job(self.job_id, 'completed', {}, owner_token='legacy', generation=generation)
        self.assertEqual(0, execute_process_request(self.request)['attempted_now'])
        self.revise(max_trials=4)
        result = self.execute()
        self.assertEqual(4, result['last_result']['attempted_now'])
        self.assertEqual(4, result['last_result']['budget']['used_trials'])

    def test_v10_campaign_and_cycle_rows_survive_v11_migration(self):
        path = self.root / 'v10.sqlite3'
        now = datetime.now(UTC).isoformat()
        with closing(sqlite3.connect(path)) as connection, connection:
            SQLiteMigrationRunner(connection, table='research_schema_migrations').apply(_MIGRATIONS[:10])
            connection.execute('INSERT INTO research_search_experiments VALUES(?,?,?)', (self.spec.experiment_id, now, json.dumps(self.spec.evidence_dict())))
            connection.execute("INSERT INTO research_search_jobs(job_id,experiment_id,dataset_id,dataset_hash,status,created_at,updated_at) VALUES(?,?,?,?,'queued',?,?)",
                               (self.job_id, self.spec.experiment_id, self.spec.dataset_id, self.spec.dataset_hash, now, now))
            connection.execute("INSERT INTO research_campaigns(campaign_id,name,revision,desired_state,operational_state,created_at,updated_at) VALUES('old','이전',1,'PAUSED','PAUSED',?,?)", (now, now))
            connection.execute('INSERT INTO research_campaign_revisions VALUES(?,1,?,?)', ('old', json.dumps(ResearchCampaignPolicy().to_dict()), now))
            connection.execute("INSERT INTO research_campaign_jobs(campaign_id,job_id,source_kind,input_path,request_json,state,accepted_sequence,generation) VALUES('old',?,'hypothesis',?,?,'PENDING',1,7)",
                               (self.job_id, str(self.request.dataset), json.dumps(self.spec.to_dict())))
            connection.execute("INSERT INTO research_campaign_cycles(campaign_id,sequence,campaign_revision,job_id,generation,owner_token,state,started_at) VALUES('old',1,1,?,7,'previous','INTERRUPTED',?)", (self.job_id, now))
            jobs = connection.execute('SELECT * FROM research_campaign_jobs').fetchall()
            cycles = connection.execute('SELECT * FROM research_campaign_cycles').fetchall()
        migrated = ResearchRepository(path)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(jobs, [row[:len(jobs[0])] for row in connection.execute('SELECT * FROM research_campaign_jobs')])
            self.assertEqual(cycles, [row[:-1] for row in connection.execute('SELECT * FROM research_campaign_cycles')])
        self.assertEqual(1, migrated.load_campaign_jobs('old')[0]['budget_revision'])
        self.assertEqual(2, migrated.load_campaign_job_budget_revisions('old', self.job_id)[0]['budget']['max_trials'])


if __name__ == '__main__':
    unittest.main()
