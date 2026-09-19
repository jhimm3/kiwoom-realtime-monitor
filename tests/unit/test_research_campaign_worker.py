from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy, campaign_worker_retry_delay_ms
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository, _MIGRATIONS
from kiwoom_monitor.infrastructure.persistence.schema_migrations import SQLiteMigrationRunner
from kiwoom_monitor.research_process import execute_campaign_cycle, execute_research, run_campaign_worker
from test_research_campaign_execution import write_campaign_request


class CampaignWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _, self.request = write_campaign_request(self.root)
        self.repo = ResearchRepository(self.request.database)
        self.repo.create_campaign('c', '연구', ResearchCampaignPolicy())
        self.repo.enqueue_campaign_experiment('c', self.request.search, self.request.dataset)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.base = datetime.now(UTC)

    def claim(self, seconds=0, owner='worker'):
        return self.repo.claim_campaign_worker('c', owner_token=owner, now=self.base + timedelta(seconds=seconds))

    def finish(self, claim, seconds=1, outcome='FAILED', **kwargs):
        return self.repo.finish_campaign_worker('c', owner_token=claim['owner_token'], generation=claim['generation'],
                                               outcome=outcome, now=self.base + timedelta(seconds=seconds), **kwargs)

    def test_two_launchers_claim_only_one_worker(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda owner: self.claim(owner=owner), ('one', 'two')))
        self.assertEqual(1, sum(value is not None for value in results))
        self.assertEqual(1, len(self.repo.load_campaign_worker_attempts('c')))

    def test_failure_backoff_is_persistent_and_not_reset_by_running_intent(self):
        worker = self.claim()
        self.assertTrue(self.finish(worker, reason='native exit', exit_code=1))
        self.repo = ResearchRepository(self.request.database)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.assertIsNone(self.claim(seconds=30))
        self.assertIsNotNone(self.claim(seconds=31, owner='next'))
        self.assertEqual(1, self.repo.load_campaign_worker('c')['failure_count'])

    def test_three_short_failures_are_quarantined_until_explicit_retry(self):
        first = self.claim()
        self.finish(first, seconds=1)
        second = self.claim(seconds=31, owner='two')
        self.finish(second, seconds=32)
        third = self.claim(seconds=92, owner='three')
        self.finish(third, seconds=93)
        self.assertEqual('NEEDS_ATTENTION', self.repo.load_campaign_worker('c')['state'])
        self.assertEqual('worker_retry_limit_reached', self.repo.load_campaign('c')['reason'])
        self.assertIsNone(self.claim(seconds=10000))
        self.repo.retry_campaign_worker('c')
        self.assertEqual(0, self.repo.load_campaign_worker('c')['failure_count'])
        self.assertEqual(4, self.claim(seconds=10000)['generation'])
        self.assertEqual(3, len([row for row in self.repo.load_campaign_worker_attempts('c') if row['state'] == 'FAILED']))

    def test_duplicate_exit_ack_does_not_charge_failure_twice(self):
        worker = self.claim()
        self.finish(worker)
        self.assertTrue(self.finish(worker, seconds=2))
        self.assertEqual(1, self.repo.load_campaign_worker('c')['failure_count'])
        self.assertFalse(self.finish(worker, seconds=2, outcome='EXPECTED_EXIT'))

    def test_stale_exit_cannot_change_new_worker(self):
        old = self.claim()
        self.finish(old)
        new = self.claim(seconds=31, owner='new')
        before = self.repo.load_campaign_worker('c')
        self.assertTrue(self.finish(old, seconds=32))
        self.assertEqual(before, self.repo.load_campaign_worker('c'))
        self.assertFalse(self.repo.renew_campaign_worker('c', owner_token=old['owner_token'], generation=old['generation'], now=self.base + timedelta(seconds=32)))
        self.assertTrue(self.repo.renew_campaign_worker('c', owner_token=new['owner_token'], generation=new['generation'], now=self.base + timedelta(seconds=32)))

    def test_unacknowledged_expiry_is_counted_once_then_waits(self):
        self.claim()
        self.assertIsNone(self.claim(seconds=30, owner='recovery'))
        self.assertEqual(1, self.repo.load_campaign_worker('c')['failure_count'])
        self.assertIsNone(self.claim(seconds=31, owner='recovery'))
        self.assertIsNotNone(self.claim(seconds=60, owner='recovery'))
        self.assertEqual('worker_lease_expired', self.repo.load_campaign_worker_attempts('c')[0]['reason'])

    def test_pause_and_expected_exit_preserve_intent_without_failure(self):
        worker = self.claim()
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.assertFalse(self.repo.renew_campaign_worker('c', owner_token=worker['owner_token'], generation=worker['generation']))
        self.assertTrue(self.finish(worker, outcome='EXPECTED_EXIT'))
        self.assertEqual(0, self.repo.load_campaign_worker('c')['failure_count'])
        self.assertEqual('PAUSED', self.repo.load_campaign('c')['desired_state'])
        self.assertIsNone(self.claim(seconds=60))

    def test_running_cancel_is_expected_and_does_not_reset_prior_failure(self):
        old = self.claim()
        self.finish(old)
        worker = self.claim(seconds=31, owner='next')
        self.finish(worker, seconds=32, outcome='EXPECTED_EXIT', reason='window_hidden')
        self.assertEqual('RUNNING', self.repo.load_campaign('c')['desired_state'])
        self.assertEqual(1, self.repo.load_campaign_worker('c')['failure_count'])

    def test_sixty_seconds_of_healthy_heartbeats_reset_consecutive_failures(self):
        self.finish(self.claim())
        worker = self.claim(seconds=31, owner='stable')
        for seconds in (51, 71, 91):
            self.assertTrue(self.repo.renew_campaign_worker('c', owner_token=worker['owner_token'], generation=worker['generation'], now=self.base + timedelta(seconds=seconds)))
        self.assertEqual(0, self.repo.load_campaign_worker('c')['failure_count'])
        self.finish(worker, seconds=92)
        self.assertEqual(1, self.repo.load_campaign_worker('c')['failure_count'])

    def test_live_worker_cannot_be_manually_replaced(self):
        self.claim()
        with self.assertRaisesRegex(ValueError, 'active worker'):
            self.repo.retry_campaign_worker('c')

    def test_retry_timer_does_not_spin_or_retry_quarantined_worker(self):
        claim = self.claim()
        self.finish(claim)
        self.assertEqual(30100, campaign_worker_retry_delay_ms(self.repo.load_campaign_worker('c'), self.base + timedelta(seconds=1)))
        self.assertIsNone(campaign_worker_retry_delay_ms({'state': 'NEEDS_ATTENTION'}))
        with self.assertRaisesRegex(ValueError, 'timezone-aware'):
            campaign_worker_retry_delay_ms(self.repo.load_campaign_worker('c'), datetime(2026, 9, 16))

    def test_attempt_insert_failure_rolls_back_worker_claim(self):
        before = self.repo.load_campaign_worker('c')
        with closing(sqlite3.connect(self.request.database)) as connection, connection:
            connection.execute("CREATE TRIGGER fail_worker BEFORE INSERT ON research_campaign_worker_attempts BEGIN SELECT RAISE(ABORT,'worker insert failed'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'worker insert failed'):
            self.claim()
        self.assertEqual(before, self.repo.load_campaign_worker('c'))
        self.assertEqual((), self.repo.load_campaign_worker_attempts('c'))

    def test_worker_runtime_failure_and_parent_ack_charge_only_once(self):
        claim = self.repo.claim_campaign_worker('c', owner_token='child')
        with patch('kiwoom_monitor.research_process._write_result', side_effect=OSError('output failed')):
            with self.assertRaisesRegex(OSError, 'output failed'):
                run_campaign_worker(self.request.database, 'c', self.request.runs_dir, self.root / 'result', worker_claim=claim)
        self.repo.finish_campaign_worker('c', owner_token='child', generation=claim['generation'], outcome='FAILED', reason='parent exit')
        self.assertEqual(1, self.repo.load_campaign_worker('c')['failure_count'])

    def test_cancel_marker_finishes_preclaimed_worker_without_running_trials(self):
        claim = self.repo.claim_campaign_worker('c', owner_token='child')
        marker = self.root / 'cancel'
        marker.touch()
        result = run_campaign_worker(self.request.database, 'c', self.request.runs_dir, self.root / 'result', cancel_path=marker, worker_claim=claim)
        self.assertEqual('RUNNING', result['campaign']['desired_state'])
        self.assertEqual('IDLE', self.repo.load_campaign_worker('c')['state'])
        self.assertEqual(0, self.repo.load_campaign_worker('c')['failure_count'])
        self.assertEqual((), self.repo.load_search_trials(self.request.search.experiment_id))

    def test_worker_ownership_loss_rejects_result_before_next_heartbeat(self):
        claim = self.repo.claim_campaign_worker('c', owner_token='child')
        def lose_owner_after_simulation(*args, **kwargs):
            result = execute_research(*args, **kwargs)
            self.repo.finish_campaign_worker('c', owner_token='child', generation=claim['generation'], outcome='FAILED', reason='owner lost')
            return result
        with patch('kiwoom_monitor.research_process.execute_research', side_effect=lose_owner_after_simulation):
            result = execute_campaign_cycle(self.repo, 'c', self.request.runs_dir, worker_claim=claim)
        self.assertEqual('INTERRUPTED', result['cycle_outcome'])
        self.assertEqual((), self.repo.load_search_trials(self.request.search.experiment_id))

    def test_shutdown_output_error_is_not_counted_as_worker_failure(self):
        claim = self.repo.claim_campaign_worker('c', owner_token='child')
        marker = self.root / 'cancel'
        def fail_after_cancel(*args):
            marker.touch()
            raise OSError('shutdown output failed')
        with patch('kiwoom_monitor.research_process._write_result', side_effect=fail_after_cancel):
            with self.assertRaisesRegex(OSError, 'shutdown output failed'):
                run_campaign_worker(self.request.database, 'c', self.request.runs_dir, self.root / 'result', cancel_path=marker, worker_claim=claim)
        worker = self.repo.load_campaign_worker('c')
        self.assertEqual('IDLE', worker['state'])
        self.assertEqual(0, worker['failure_count'])

    def test_v11_budget_and_job_history_survive_v12_migration(self):
        path = self.root / 'v11.sqlite3'
        now = self.base.isoformat()
        spec = self.request.search
        with closing(sqlite3.connect(path)) as connection, connection:
            SQLiteMigrationRunner(connection, table='research_schema_migrations').apply(_MIGRATIONS[:11])
            connection.execute('INSERT INTO research_search_experiments VALUES(?,?,?)', (spec.experiment_id, now, json.dumps(spec.evidence_dict())))
            connection.execute("INSERT INTO research_search_jobs(job_id,experiment_id,dataset_id,dataset_hash,status,created_at,updated_at) VALUES('job',?,?,?,'queued',?,?)", (spec.experiment_id, spec.dataset_id, spec.dataset_hash, now, now))
            connection.execute("INSERT INTO research_campaigns(campaign_id,name,revision,desired_state,operational_state,created_at,updated_at) VALUES('old','old',1,'RUNNING','RUNNING',?,?)", (now, now))
            connection.execute('INSERT INTO research_campaign_revisions VALUES(?,1,?,?)', ('old', json.dumps(ResearchCampaignPolicy().to_dict()), now))
            connection.execute("INSERT INTO research_campaign_jobs(campaign_id,job_id,source_kind,input_path,request_json,state,accepted_sequence,budget_revision) VALUES('old','job','hypothesis',?,?,'PENDING',1,2)", (str(self.request.dataset), json.dumps(spec.to_dict())))
            budget = {key: spec.to_dict()[key] for key in ('max_trials', 'max_seconds', 'resource_budget')}
            for revision in (1, 2):
                connection.execute('INSERT INTO research_campaign_job_budgets VALUES(?,?,?,?,?)', ('old', 'job', revision, json.dumps(budget), now))
            before = {table: connection.execute('SELECT * FROM ' + table).fetchall() for table in ('research_campaigns', 'research_campaign_jobs', 'research_campaign_job_budgets')}
            previous_columns = {table: ','.join(row[1] for row in connection.execute(f'PRAGMA table_info({table})')) for table in before}
        migrated = ResearchRepository(path)
        with closing(sqlite3.connect(path)) as connection:
            for table, rows in before.items():
                self.assertEqual(rows, connection.execute(f'SELECT {previous_columns[table]} FROM {table}').fetchall())
        self.assertEqual(23, migrated.schema_version())
        self.assertEqual('IDLE', migrated.load_campaign_worker('old')['state'])


if __name__ == '__main__':
    unittest.main()
