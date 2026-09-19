from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy, build_research_job_identity
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository, RESEARCH_SCHEMA_VERSION
from kiwoom_monitor.infrastructure.persistence.research_repository import _MIGRATIONS
from kiwoom_monitor.infrastructure.persistence.schema_migrations import SQLiteMigrationRunner
from test_research_queue import _spec

BASE = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


class ResearchCampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'research.sqlite3'
        self.repo = ResearchRepository(self.path)
        self.repo.create_campaign('c', '연구', ResearchCampaignPolicy())

    def enqueue(self, name='one', **kwargs):
        spec = _spec(name)
        self.repo.enqueue_campaign_experiment('c', spec, self.path.parent / 'dataset', **kwargs)
        return build_research_job_identity(spec).job_id

    def claim(self, owner='a', seconds=0, **kwargs):
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        return self.repo.claim_campaign_cycle('c', owner_token=owner, now=BASE + timedelta(seconds=seconds), **kwargs)

    def finish(self, cycle, outcome, seconds=1, **kwargs):
        return self.repo.finish_campaign_cycle('c', cycle['sequence'], owner_token=cycle['owner_token'],
                                              generation=cycle['generation'], outcome=outcome,
                                              now=BASE + timedelta(seconds=seconds), **kwargs)

    def completed_search(self, job_id):
        generation = self.repo.start_search_job(job_id, owner_token='search', lease_seconds=60)
        self.assertTrue(self.repo.finish_search_job(job_id, 'completed', {}, owner_token='search', generation=generation))

    def test_policy_allows_owned_hypotheses_but_disallows_automatic_final(self):
        self.assertTrue(ResearchCampaignPolicy(auto_hypotheses=True).auto_hypotheses)
        for kwargs in ({'auto_final_evaluation': True}, {'auto_hypotheses': 1}, {'max_active_jobs': 0},
                       {'max_attempts': 0}, {'retry_initial_seconds': 901}, {'retry_max_seconds': 0}):
            with self.assertRaises(ValueError):
                ResearchCampaignPolicy(**kwargs)
        self.assertEqual(900, ResearchCampaignPolicy().retry_seconds(100000))

    def test_schema_default_state_and_idempotent_creation_survive_restart(self):
        self.assertEqual(23, RESEARCH_SCHEMA_VERSION)
        self.assertFalse(self.repo.create_campaign('c', '연구', ResearchCampaignPolicy()))
        restored = ResearchRepository(self.path).load_campaign('c')
        self.assertEqual('PAUSED', restored['desired_state'])
        self.assertEqual(1, restored['revision'])
        with self.assertRaises(ValueError):
            self.repo.create_campaign('c', '다른 연구', ResearchCampaignPolicy())

    def test_policy_revisions_are_immutable_and_require_pause_and_expected_revision(self):
        changed = ResearchCampaignPolicy(max_attempts=2)
        self.assertEqual(2, self.repo.revise_campaign_policy('c', changed, expected_revision=1))
        with self.assertRaises(ValueError):
            self.repo.revise_campaign_policy('c', ResearchCampaignPolicy(), expected_revision=1)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        with self.assertRaises(ValueError):
            self.repo.revise_campaign_policy('c', ResearchCampaignPolicy(), expected_revision=2)
        with closing(sqlite3.connect(self.path)) as connection:
            revisions = connection.execute('SELECT policy_json FROM research_campaign_revisions ORDER BY revision').fetchall()
        self.assertEqual([3, 2], [json.loads(row[0])['max_attempts'] for row in revisions])

    def test_request_snapshot_is_detached_from_mutable_spec_and_enqueue_is_idempotent(self):
        spec = _spec()
        input_path = self.path.parent / 'dataset'
        self.assertTrue(self.repo.enqueue_campaign_experiment('c', spec, input_path))
        self.assertFalse(self.repo.enqueue_campaign_experiment('c', spec, input_path))
        spec.research_context['locked_request'] = 'externally changed'
        restored = ResearchRepository(self.path).load_campaign_jobs('c')
        self.assertEqual('context-1', restored[0]['request']['research_context']['locked_request'])
        self.assertEqual(1, len(restored))
        with self.assertRaises(ValueError):
            self.repo.enqueue_campaign_experiment('c', replace(_spec(), max_seconds=30), input_path)

    def test_paused_and_stopped_campaigns_cannot_be_claimed(self):
        self.enqueue()
        for state in ('PAUSED', 'STOPPED'):
            self.repo.set_campaign_desired_state('c', state)
            self.assertIsNone(self.repo.claim_campaign_cycle('c', owner_token='a', now=BASE))
            self.assertEqual(state, ResearchRepository(self.path).load_campaign('c')['desired_state'])
        self.assertEqual((), self.repo.load_campaign_cycles('c'))

    def test_two_launchers_claim_one_atomic_cycle(self):
        self.enqueue('one')
        self.enqueue('two')
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        second = ResearchRepository(self.path)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda pair: pair[0].claim_campaign_cycle('c', owner_token=pair[1], now=BASE),
                                   [(self.repo, 'a'), (second, 'b')]))
        self.assertEqual(1, sum(claim is not None for claim in claims))
        self.assertEqual(1, self.repo.load_campaign('c')['cycle_sequence'])
        self.assertEqual(1, len(self.repo.load_campaign_cycles('c')))

    def test_expired_lease_creates_new_attempt_and_old_owner_cannot_renew_or_finish(self):
        self.enqueue()
        old = self.claim(lease_seconds=5)
        new = self.claim('b', seconds=6)
        self.assertEqual((2, 2), (new['generation'], new['sequence']))
        self.assertFalse(self.repo.renew_campaign_cycle('c', old['sequence'], owner_token='a', generation=1, now=BASE + timedelta(seconds=7)))
        self.assertFalse(self.finish(old, 'INTERRUPTED', seconds=7))
        self.assertEqual('INTERRUPTED', self.repo.load_campaign_cycles('c')[0]['state'])
        self.assertTrue(self.finish(new, 'INTERRUPTED', seconds=7))

    def test_renewal_extends_lease_but_pause_stops_renewal_and_preserves_desired_state(self):
        self.enqueue()
        cycle = self.claim(lease_seconds=5)
        self.assertTrue(self.repo.renew_campaign_cycle('c', 1, owner_token='a', generation=1,
                                                     lease_seconds=10, now=BASE + timedelta(seconds=4)))
        self.assertIsNone(self.repo.claim_campaign_cycle('c', owner_token='b', now=BASE + timedelta(seconds=6)))
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.assertFalse(self.repo.renew_campaign_cycle('c', 1, owner_token='a', generation=1, now=BASE + timedelta(seconds=7)))
        self.assertTrue(self.finish(cycle, 'INTERRUPTED', seconds=7))
        self.assertEqual('PAUSED', self.repo.load_campaign('c')['operational_state'])

    def test_retry_then_unexecuted_hypothesis_then_new_data_priority(self):
        self.enqueue('new', source_kind='new_data')
        hypothesis = self.enqueue('hypothesis')
        self.enqueue('other')
        first = self.claim()
        self.assertEqual(hypothesis, first['job_id'])
        self.assertTrue(self.finish(first, 'INTERRUPTED'))
        retry = self.claim(seconds=2)
        self.assertEqual(hypothesis, retry['job_id'])
        self.assertEqual(0, self.repo.load_campaign_jobs('c')[1]['failure_count'])

    def test_failure_backoff_and_limit_are_separate_from_user_interruptions(self):
        self.repo.revise_campaign_policy('c', ResearchCampaignPolicy(max_attempts=2), expected_revision=1)
        job_id = self.enqueue()
        first = self.claim()
        self.assertTrue(self.finish(first, 'INTERRUPTED'))
        retry = self.claim(seconds=2)
        self.assertTrue(self.finish(retry, 'FAILED', seconds=3, reason='fixture failure'))
        self.assertIsNone(self.repo.claim_campaign_cycle('c', owner_token='a', now=BASE + timedelta(seconds=32)))
        last = self.claim(seconds=33)
        self.assertTrue(self.finish(last, 'FAILED', seconds=34))
        self.assertEqual('NEEDS_ATTENTION', self.repo.load_campaign('c')['operational_state'])
        self.assertIsNone(self.repo.claim_campaign_cycle('c', owner_token='a', now=BASE + timedelta(seconds=1000)))
        self.repo.retry_campaign_job('c', job_id)
        self.assertIsNotNone(self.claim(seconds=1001))

    def test_resource_block_is_not_auto_retried_and_explicit_retry_is_allowed(self):
        job_id = self.enqueue()
        cycle = self.claim()
        self.assertTrue(self.finish(cycle, 'RESOURCE_BLOCKED', reason='memory_rss_limit_exceeded'))
        self.assertEqual('RESOURCE_BLOCKED', self.repo.load_campaign('c')['operational_state'])
        self.assertIsNone(self.repo.claim_campaign_cycle('c', owner_token='a', now=BASE + timedelta(days=1)))
        self.repo.retry_campaign_job('c', job_id)
        self.assertIsNotNone(self.claim(seconds=2))

    def test_completion_requires_actual_completed_job_and_cannot_be_committed_twice(self):
        job_id = self.enqueue()
        cycle = self.claim()
        with self.assertRaisesRegex(ValueError, 'completed search job'):
            self.finish(cycle, 'COMPLETED')
        self.completed_search(job_id)
        self.assertTrue(self.finish(cycle, 'COMPLETED'))
        self.assertFalse(self.finish(cycle, 'COMPLETED', seconds=2))
        self.assertEqual('SEARCH_SPACE_EXHAUSTED', self.repo.load_campaign('c')['operational_state'])
        self.assertEqual(1, len(self.repo.load_campaign_cycles('c')))

    def test_committed_search_result_is_recovered_after_worker_dies_without_cycle_ack(self):
        job_id = self.enqueue()
        self.claim(lease_seconds=5)
        self.completed_search(job_id)
        self.assertIsNone(self.repo.claim_campaign_cycle('c', owner_token='b', now=BASE + timedelta(seconds=6)))
        cycles = self.repo.load_campaign_cycles('c')
        self.assertEqual(('COMPLETED', 'completed_search_job_recovered'), (cycles[0]['state'], cycles[0]['reason']))
        self.assertEqual(1, self.repo.load_campaign('c')['cycle_sequence'])

    def test_new_hypothesis_is_accepted_without_new_dataset_after_registered_jobs_complete(self):
        job_id = self.enqueue()
        cycle = self.claim()
        self.completed_search(job_id)
        self.finish(cycle, 'COMPLETED')
        changed = replace(_spec('one'), hypothesis_refs=('new hypothesis',))
        self.assertTrue(self.repo.enqueue_campaign_experiment('c', changed, self.path.parent / 'dataset'))
        self.assertIsNotNone(self.claim(seconds=2))

    def test_backlog_failure_rolls_back_experiment_and_job_creation(self):
        self.repo.revise_campaign_policy('c', ResearchCampaignPolicy(max_active_jobs=1), expected_revision=1)
        self.enqueue()
        spec = _spec('two')
        with self.assertRaisesRegex(ValueError, 'backlog'):
            self.repo.enqueue_campaign_experiment('c', spec, self.path.parent / 'dataset')
        self.assertIsNone(self.repo.load_search_job(build_research_job_identity(spec).job_id))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM research_search_experiments').fetchone()[0])

    def test_100_completed_jobs_do_not_block_101st_active_job(self):
        for index in range(100):
            self.enqueue(str(index))
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE research_search_jobs SET status='completed'")
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.assertIsNone(self.repo.claim_campaign_cycle('c', owner_token='a', now=BASE))
        self.enqueue('101')
        self.assertIsNotNone(self.claim(seconds=1))
        self.assertEqual(101, len(self.repo.load_campaign_jobs('c')))

    def test_naive_clock_and_invalid_lease_or_state_are_rejected(self):
        for kwargs in ({'now': datetime(2026, 9, 16), 'owner_token': 'a'}, {'lease_seconds': 0, 'owner_token': 'a'}, {'owner_token': ''}):
            with self.assertRaises(ValueError):
                self.repo.claim_campaign_cycle('c', **kwargs)
        with self.assertRaises(ValueError):
            self.repo.set_campaign_desired_state('c', 'UNKNOWN')

    def test_live_search_owner_is_not_dispatched_by_campaign(self):
        job_id = self.enqueue()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE research_search_jobs SET status='running',lease_expires_at=? WHERE job_id=?",
                               ((BASE + timedelta(seconds=60)).isoformat(), job_id))
        self.assertIsNone(self.claim())
        self.assertIsNotNone(self.claim(seconds=61))

    def test_existing_cycle_keeps_its_frozen_policy_when_paused_policy_is_revised(self):
        self.enqueue()
        cycle = self.claim()
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.repo.revise_campaign_policy('c', ResearchCampaignPolicy(retry_initial_seconds=90), expected_revision=1)
        self.assertTrue(self.finish(cycle, 'FAILED', seconds=1))
        job = self.repo.load_campaign_jobs('c')[0]
        self.assertEqual((BASE + timedelta(seconds=31)).isoformat(), job['next_attempt_at'])
        self.assertEqual(1, self.repo.load_campaign_cycles('c')[0]['campaign_revision'])

    def test_cycle_write_failure_rolls_back_sequence_generation_and_claim(self):
        self.enqueue()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TRIGGER reject_cycle BEFORE INSERT ON research_campaign_cycles "
                               "BEGIN SELECT RAISE(ABORT,'simulated_write_failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'simulated_write_failure'):
            self.claim()
        self.assertEqual(0, self.repo.load_campaign('c')['cycle_sequence'])
        self.assertEqual((), self.repo.load_campaign_cycles('c'))
        job = self.repo.load_campaign_jobs('c')[0]
        self.assertEqual(('PENDING', 0, 0), (job['state'], job['generation'], job['attempt_count']))

    def test_global_backlog_failure_does_not_leave_orphan_experiment(self):
        self.enqueue()
        self.repo.create_campaign('other', '다른 연구', ResearchCampaignPolicy(max_active_jobs=1))
        spec = _spec('other')
        with self.assertRaisesRegex(ValueError, 'retention'):
            self.repo.enqueue_campaign_experiment('other', spec, self.path.parent / 'dataset')
        self.assertEqual((), self.repo.load_campaign_jobs('other'))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(1, connection.execute('SELECT COUNT(*) FROM research_search_experiments').fetchone()[0])

    def test_v9_search_owner_and_attempt_data_are_preserved_by_v10_migration(self):
        path = self.path.parent / 'old-v9.sqlite3'
        with closing(sqlite3.connect(path)) as connection, connection:
            SQLiteMigrationRunner(connection, table='research_schema_migrations').apply(_MIGRATIONS[:9])
            connection.execute("INSERT INTO research_search_experiments VALUES('old','now','{}')")
            connection.execute("INSERT INTO research_search_jobs(job_id,experiment_id,dataset_id,dataset_hash,status,created_at,updated_at,owner_token,generation) "
                               "VALUES('old-job','old','dataset','hash','running','now','now','old-owner',7)")
            connection.execute("INSERT INTO research_trial_attempts(attempt_id,job_id,experiment_id,trial_id,owner_token,generation,status,started_at,heartbeat_at) "
                               "VALUES('old-attempt','old-job','old','old-trial','old-owner',7,'RUNNING','now','now')")
            before_job = connection.execute('SELECT * FROM research_search_jobs').fetchone()
            before_attempt = connection.execute('SELECT * FROM research_trial_attempts').fetchone()
        migrated = ResearchRepository(path)
        self.assertEqual(23, migrated.schema_version())
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(before_job, connection.execute('SELECT * FROM research_search_jobs').fetchone())
            self.assertEqual(before_attempt, connection.execute('SELECT * FROM research_trial_attempts').fetchone())


if __name__ == '__main__':
    unittest.main()
