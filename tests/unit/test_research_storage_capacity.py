from __future__ import annotations

import sqlite3
import json
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from unittest.mock import patch

from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository, _MIGRATIONS
from kiwoom_monitor.infrastructure.persistence.schema_migrations import SQLiteMigrationRunner
from scripts.export_research_dataset import export_daily_dataset
import test_research_campaign_nas as nas_fixture_module


class ResearchStorageCapacityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = nas_fixture_module.CampaignNasTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.base = self.fixture.repo, self.fixture.fixture
        self.source_id = self.base.source_id

    def cap(self, count):
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.repo.finish_campaign_worker('c', owner_token='worker', generation=self.base.claim['generation'], outcome='EXPECTED_EXIT')
        self.repo.save_campaign_input_source('c', self.base.job, self.fixture.watch, nas_auto_prepare=True, nas_config_path=self.fixture.config_path, storage_cap_bytes=count)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.base.claim = self.repo.claim_campaign_worker('c', owner_token='worker', lease_seconds=3600)

    def ledger(self):
        return self.repo.load_campaign_storage_operations(self.source_id)

    def test_unlimited_publishes_and_unchanged_cache_does_not_grow_ledger(self):
        self.assertEqual(0, self.repo.load_campaign_input_sources('c')[0]['storage_cap_bytes'])
        self.assertEqual(1, self.fixture.scan()['registered'])
        self.assertEqual('PUBLISHED', self.ledger()[0]['state'])
        self.assertTrue(self.ledger()[0]['input_path'])
        operation = self.ledger()[0]
        self.assertTrue(operation['staging_path'])
        self.assertFalse(Path(operation['staging_path']).exists())
        marker = json.loads((Path(operation['input_path']) / '.research-storage.json').read_text())
        self.assertEqual(operation['operation_id'], marker['operation_id'])
        self.fixture.scan(61)
        self.assertEqual(1, len(self.ledger()))
        self.assertEqual(self.ledger(), ResearchRepository(self.repo.path).load_campaign_storage_operations(self.source_id))

    def test_repeated_capacity_wait_does_not_quarantine_or_delete(self):
        self.cap(1)
        for seconds in (0, 61, 122, 183):
            result = self.fixture.scan(seconds)
            self.assertEqual([], result['errors'])
            self.assertEqual(1, result['waiting_storage'])
            source = self.repo.load_campaign_input_sources('c')[0]
            self.assertEqual('WAITING_STORAGE', source['state'])
            self.assertEqual(0, source['failure_count'])
            self.assertEqual('', source['remote_signature'])
        self.assertEqual([], list(self.fixture.watch.iterdir()))
        self.assertTrue(all(row['state'] == 'BLOCKED' for row in self.ledger()))

    def test_existing_manual_bytes_count_before_download_and_are_preserved(self):
        self.cap(1024)
        manual = self.fixture.watch / 'manual.txt'
        manual.write_bytes(b'x' * 1024)
        with patch('scripts.export_research_dataset.export_daily_dataset') as export:
            result = self.fixture.scan()
        export.assert_not_called()
        self.assertEqual(1, result['waiting_storage'])
        self.assertEqual(b'x' * 1024, manual.read_bytes())

    def test_markers_and_manifests_count_and_partial_stage_is_cleaned(self):
        self.cap(1024)
        self.assertEqual(1, self.fixture.scan()['waiting_storage'])
        self.assertEqual([], list(self.fixture.watch.iterdir()))
        self.assertEqual('BLOCKED', self.ledger()[0]['state'])

    def test_incomplete_capacity_inventory_fails_closed_without_publication(self):
        self.cap(128 * 1024)
        with patch('kiwoom_monitor.infrastructure.research_storage.inventory_research_storage', return_value={'complete': False, 'total_bytes': 0}):
            result = self.fixture.scan()
        self.assertIn('inventory_incomplete', result['errors'][0])
        self.assertEqual('BLOCKED', self.ledger()[0]['state'])
        self.assertEqual([], list(self.fixture.watch.iterdir()))

    def test_redirected_root_is_rejected_even_when_unlimited(self):
        with patch('scripts.export_research_dataset.validate_research_storage_root', side_effect=ValueError('root was redirected')):
            result = self.fixture.scan()
        self.assertIn('redirected', result['errors'][0])
        self.assertEqual([], list(self.fixture.watch.iterdir()))

    def test_raise_capacity_resumes_preparation_within_budget(self):
        self.cap(1)
        self.fixture.scan()
        self.cap(128 * 1024)
        result = self.fixture.scan(61)
        self.assertEqual([], result['errors'])
        self.assertEqual(1, result['registered'])
        self.assertLessEqual(self.repo.inspect_campaign_input_storage(self.source_id)['total_bytes'], 128 * 1024)

    def test_preparation_is_serialized_and_finish_is_idempotent(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            operations = list(executor.map(lambda _: self.repo.begin_campaign_storage_preparation(self.source_id, self.base.claim), range(2)))
        self.assertEqual(1, sum(value is not None for value in operations))
        operation = next(value for value in operations if value)
        self.assertTrue(self.repo.finish_campaign_storage_preparation(operation, self.base.claim, outcome='UNCHANGED'))
        self.assertFalse(self.repo.finish_campaign_storage_preparation(operation, self.base.claim, outcome='FAILED'))
        self.assertEqual('UNCHANGED', self.ledger()[0]['state'])

    def test_abandoned_worker_cannot_publish_or_change_new_operation(self):
        old_claim = self.base.claim.copy()
        operation = self.repo.begin_campaign_storage_preparation(self.source_id, old_claim)
        sentinel = self.fixture.watch / 'old-file'
        sentinel.write_text('keep')
        self.repo.finish_campaign_worker('c', owner_token='worker', generation=old_claim['generation'], outcome='EXPECTED_EXIT')
        self.base.claim = self.repo.claim_campaign_worker('c', owner_token='new', lease_seconds=3600)
        self.assertIsNotNone(self.repo.begin_campaign_storage_preparation(self.source_id, self.base.claim))
        self.assertEqual('ABANDONED', next(row for row in self.ledger() if row['operation_id'] == operation)['state'])
        self.assertFalse(self.repo.finish_campaign_storage_preparation(operation, old_claim, outcome='PUBLISHED'))
        with self.assertRaisesRegex(ValueError, 'no longer active'):
            with self.repo.campaign_storage_publication(operation, old_claim):
                self.fail('stale publication')
        self.assertEqual('keep', sentinel.read_text())

    def test_pause_during_download_prevents_publication(self):
        def pause_after_export(*args, **kwargs):
            result = export_daily_dataset(*args, **kwargs)
            self.repo.set_campaign_desired_state('c', 'PAUSED')
            return result
        with patch('scripts.export_research_dataset.export_daily_dataset', side_effect=pause_after_export):
            result = self.fixture.scan()
        self.assertIn('no longer active', result['errors'][0])
        self.assertEqual('FAILED', self.ledger()[0]['state'])
        self.assertEqual([], list(self.fixture.watch.iterdir()))

    def test_busy_preparation_waits_without_source_failure(self):
        self.repo.begin_campaign_storage_preparation(self.source_id, self.base.claim)
        result = self.fixture.scan()
        self.assertEqual([], result['errors'])
        self.assertEqual(1, result['waiting_storage'])
        self.assertEqual(0, self.repo.load_campaign_input_sources('c')[0]['failure_count'])

    def test_lost_finish_record_expires_even_when_worker_remains_healthy(self):
        operation = self.repo.begin_campaign_storage_preparation(self.source_id, self.base.claim)
        with closing(self.repo._connect()) as connection, connection:
            connection.execute("UPDATE research_campaign_storage_operations SET started_at='2000-01-01T00:00:00+00:00' WHERE operation_id=?", (operation,))
        self.assertIsNotNone(self.repo.begin_campaign_storage_preparation(self.source_id, self.base.claim))
        self.assertEqual('ABANDONED', next(row for row in self.ledger() if row['operation_id'] == operation)['state'])
        with self.assertRaisesRegex(ValueError, 'no longer active'):
            with self.repo.campaign_storage_publication(operation, self.base.claim):
                self.fail('expired publication')

    def test_invalid_capacity_and_overlapping_policy_are_rejected(self):
        self.cap(65536)
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.repo.finish_campaign_worker('c', owner_token='worker', generation=self.base.claim['generation'], outcome='EXPECTED_EXIT')
        for value in (-1, True, 1.5):
            with self.assertRaisesRegex(ValueError, 'capacity'):
                self.repo.save_campaign_input_source('c', self.base.job, self.fixture.watch, storage_cap_bytes=value)
        self.repo.create_campaign('other', 'other', ResearchCampaignPolicy())
        self.repo.enqueue_campaign_experiment('other', self.base.request.search, self.base.request.dataset)
        for root, cap in ((self.fixture.watch, 131072), (self.fixture.watch / 'child', 65536)):
            with self.assertRaisesRegex(ValueError, 'overlapping'):
                self.repo.save_campaign_input_source('other', self.base.job, root, nas_auto_prepare=True, nas_config_path=self.fixture.config_path, storage_cap_bytes=cap)
        self.repo.save_campaign_input_source('other', self.base.job, self.fixture.watch, nas_auto_prepare=True, nas_config_path=self.fixture.config_path, storage_cap_bytes=65536)

    def test_v14_migration_preserves_source_and_defaults_unlimited(self):
        path = self.fixture.root / 'v14.sqlite3'
        with closing(sqlite3.connect(path)) as old, old, closing(self.repo._connect()) as current:
            SQLiteMigrationRunner(old, table='research_schema_migrations').apply(_MIGRATIONS[:14])
            for table in ('research_campaigns', 'research_campaign_revisions', 'research_search_experiments', 'research_search_jobs', 'research_campaign_jobs', 'research_campaign_input_sources'):
                columns = ','.join(row[1] for row in old.execute(f'PRAGMA table_info({table})'))
                rows = current.execute(f'SELECT {columns} FROM {table}').fetchall()
                old.executemany(f'INSERT INTO {table}({columns}) VALUES({",".join("?" for _ in rows[0])})', rows)
            before = old.execute('SELECT * FROM research_campaign_input_sources').fetchone()
        migrated = ResearchRepository(path)
        with closing(sqlite3.connect(path)) as connection:
            after = connection.execute('SELECT * FROM research_campaign_input_sources').fetchone()
        self.assertEqual(before, after[:-3])
        self.assertEqual(0, after[-3])
        self.assertEqual(0, after[-2])
        self.assertEqual('', after[-1])
        self.assertEqual(27, migrated.schema_version())
        self.assertEqual((), migrated.load_campaign_storage_operations(self.source_id))
