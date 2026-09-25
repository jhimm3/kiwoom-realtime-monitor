from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository, _MIGRATIONS
from kiwoom_monitor.infrastructure.persistence.schema_migrations import SQLiteMigrationRunner
from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
from kiwoom_monitor.infrastructure.research_storage import (
    MARKER_NAME, ResearchStagingCleanupYield, write_research_storage_marker,
    inspect_incomplete_research_staging, delete_incomplete_research_staging,
)
import test_research_campaign_nas as nas_fixture_module


class ResearchStagingCleanupTests(unittest.TestCase):
    def setUp(self):
        self.fixture = nas_fixture_module.CampaignNasTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.base = self.fixture.repo, self.fixture.fixture
        self.source_id = self.base.source_id
        self.count = 0

    def partial(self, *, old=True):
        self.count += 1
        operation = self.repo.begin_campaign_storage_preparation(self.source_id, self.base.claim)
        path = self.fixture.watch / f'.nas-preparing-old{self.count}'
        path.mkdir()
        self.repo.record_campaign_storage_staging(operation, self.base.claim, path)
        write_research_storage_marker(path, self.source_id, kind='preparing', operation_id=operation)
        (path / 'payload').mkdir()
        (path / 'payload' / 'observations.jsonl').write_text('partial')
        self.repo.finish_campaign_storage_preparation(operation, self.base.claim, outcome='FAILED')
        if old:
            when = (datetime.now(UTC) - timedelta(days=2)).isoformat()
            marker = json.loads((path / MARKER_NAME).read_text())
            marker['created_at'] = when
            (path / MARKER_NAME).write_text(json.dumps(marker))
            with closing(self.repo._connect()) as connection, connection:
                connection.execute('UPDATE research_campaign_storage_operations SET started_at=?,finished_at=? WHERE operation_id=?', (when, when, operation))
        return operation, path

    def clean(self, **kwargs):
        return self.repo.cleanup_campaign_incomplete_staging(self.source_id, self.base.claim, **kwargs)

    def ledger(self):
        return self.repo.load_campaign_staging_cleanups(self.source_id)

    def test_old_known_partial_is_removed_and_ledger_is_idempotent(self):
        operation, path = self.partial()
        before = self.base.request.dataset.joinpath('manifest.json').read_bytes()
        result = self.clean()
        self.assertEqual(1, result['deleted'])
        self.assertFalse(path.exists())
        self.assertEqual('DELETED', self.ledger()[0]['state'])
        self.assertEqual(operation, self.ledger()[0]['operation_id'])
        self.assertEqual(0, self.clean()['deleted'])
        self.assertEqual(before, self.base.request.dataset.joinpath('manifest.json').read_bytes())
        self.assertEqual(self.ledger(), ResearchRepository(self.repo.path).load_campaign_staging_cleanups(self.source_id))

    def test_young_or_live_preparing_stages_are_preserved(self):
        _, young = self.partial(old=False)
        operation, active = self.partial()
        with closing(self.repo._connect()) as connection, connection:
            connection.execute("UPDATE research_campaign_storage_operations SET state='PREPARING',finished_at='' WHERE operation_id=?", (operation,))
        self.assertEqual(0, self.clean()['deleted'])
        self.assertTrue(young.exists())
        self.assertTrue(active.exists())

    def test_any_daily_or_bundle_manifest_is_preserved(self):
        _, path = self.partial()
        (path / 'payload' / 'days').mkdir()
        day = path / 'payload' / 'days' / '2026-09-12'
        day.mkdir()
        (day / 'manifest.json').write_text('{}')
        self.assertEqual(1, self.clean()['protected'])
        self.assertTrue((day / 'manifest.json').exists())
        self.assertEqual('completed_input_preserved', self.ledger()[0]['reason'])

    def test_unknown_manual_file_and_legacy_marker_are_preserved(self):
        _, path = self.partial()
        (path / 'notes.txt').write_text('user notes')
        self.clean()
        self.assertEqual('user notes', (path / 'notes.txt').read_text())
        _, legacy = self.partial()
        marker = json.loads((legacy / MARKER_NAME).read_text())
        marker.pop('operation_id')
        (legacy / MARKER_NAME).write_text(json.dumps(marker))
        self.clean()
        self.assertTrue(legacy.exists())

    def test_completed_job_reference_to_stage_or_parent_preserves_it(self):
        _, path = self.partial()
        with closing(self.repo._connect()) as connection, connection:
            connection.execute("UPDATE research_campaign_jobs SET input_path=?,state='COMPLETED' WHERE campaign_id='c'", (str(path / 'payload'),))
        self.assertEqual(1, self.clean()['protected'])
        self.assertEqual('research_reference_preserved', self.ledger()[0]['reason'])
        self.assertTrue(path.exists())

    def test_reference_added_after_initial_inspection_is_rechecked(self):
        _, path = self.partial()
        def add_reference(*args, **kwargs):
            result = inspect_incomplete_research_staging(*args, **kwargs)
            with closing(self.repo._connect()) as connection, connection:
                connection.execute('UPDATE research_campaign_jobs SET input_path=?', (str(path),))
            return result
        with patch('kiwoom_monitor.infrastructure.research_storage.inspect_incomplete_research_staging', side_effect=add_reference):
            result = self.clean()
        self.assertEqual(1, result['protected'])
        self.assertTrue(path.exists())

    def test_other_campaign_reference_is_also_preserved(self):
        _, path = self.partial()
        self.repo.create_campaign('other', 'other', ResearchCampaignPolicy())
        self.repo.enqueue_campaign_experiment('other', self.base.request.search, path / 'payload')
        self.assertEqual(1, self.clean()['protected'])
        self.assertTrue(path.exists())

    def test_pause_after_preflight_prevents_deletion(self):
        _, path = self.partial()
        def pause(*args, **kwargs):
            result = inspect_incomplete_research_staging(*args, **kwargs)
            self.repo.set_campaign_desired_state('c', 'PAUSED')
            return result
        with patch('kiwoom_monitor.infrastructure.research_storage.inspect_incomplete_research_staging', side_effect=pause):
            self.assertEqual(0, self.clean()['deleted'])
        self.assertTrue((path / 'payload' / 'observations.jsonl').exists())

    def test_marker_changed_after_preflight_is_not_adopted(self):
        _, path = self.partial()
        calls = 0
        def change_marker(*args, **kwargs):
            nonlocal calls
            result = inspect_incomplete_research_staging(*args, **kwargs)
            calls += 1
            if calls == 1:
                marker = json.loads((path / MARKER_NAME).read_text())
                marker['changed'] = True
                (path / MARKER_NAME).write_text(json.dumps(marker))
            return result
        with patch('kiwoom_monitor.infrastructure.research_storage.inspect_incomplete_research_staging', side_effect=change_marker):
            self.assertEqual(1, self.clean()['protected'])
        self.assertEqual('staging_marker_changed', self.ledger()[0]['reason'])
        self.assertTrue((path / 'payload' / 'observations.jsonl').exists())

    def test_outside_root_path_from_corrupt_record_is_preserved(self):
        operation, _ = self.partial()
        outside = self.fixture.root / '.nas-preparing-outside'
        outside.mkdir()
        (outside / 'user.txt').write_text('keep')
        with closing(self.repo._connect()) as connection, connection:
            connection.execute('UPDATE research_campaign_storage_operations SET staging_path=? WHERE operation_id=?', (str(outside), operation))
        self.assertEqual(1, self.clean()['protected'])
        self.assertEqual('keep', (outside / 'user.txt').read_text())

    def test_large_partial_tree_is_protected_before_any_unlink(self):
        _, path = self.partial()
        days = path / 'payload' / 'days'
        days.mkdir()
        for count in range(40):
            child = days / (datetime(2026, 1, 1) + timedelta(days=count)).date().isoformat()
            child.mkdir()
            (child / 'observations.jsonl').write_text('partial')
        self.assertEqual(1, self.clean()['protected'])
        self.assertEqual('staging_inventory_limit', self.ledger()[0]['reason'])
        self.assertEqual(40, len(tuple(days.iterdir())))

    def test_redirected_child_is_preserved_without_traversal(self):
        _, path = self.partial()
        child = path / 'payload'
        import kiwoom_monitor.infrastructure.research_storage as storage
        original = storage._redirected
        with patch.object(storage, '_redirected', side_effect=lambda value: value == child or original(value)):
            self.assertEqual(1, self.clean()['protected'])
        self.assertTrue(path.exists())

    def test_cancel_and_caller_heartbeat_never_run_under_write_fence(self):
        _, path = self.partial()
        with self.assertRaises(InterruptedError):
            self.clean(checkpoint=lambda: (_ for _ in ()).throw(InterruptedError()))
        self.assertTrue(path.exists())
        def heartbeat():
            with closing(self.repo._connect()) as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.rollback()
        self.assertEqual(1, self.clean(checkpoint=heartbeat)['deleted'])

    def test_io_failure_retries_with_backoff_without_source_failure(self):
        _, path = self.partial()
        now = datetime.now(UTC)
        with patch.object(Path, 'unlink', side_effect=PermissionError('locked')):
            self.assertEqual(1, len(self.clean(now=now)['errors']))
        self.assertEqual('FAILED', self.ledger()[0]['state'])
        self.assertEqual(1, self.ledger()[0]['failure_count'])
        self.assertEqual(0, self.clean(now=now + timedelta(seconds=30))['deleted'])
        self.assertEqual(1, self.clean(now=now + timedelta(seconds=61))['deleted'])
        self.assertFalse(path.exists())
        self.assertEqual(0, self.repo.load_campaign_input_sources('c')[0]['failure_count'])

    def test_partial_delete_yield_keeps_identity_and_resumes(self):
        _, path = self.partial()
        def partial_delete(root, stage, inspection, **kwargs):
            inspection['files'][0].unlink()
            raise ResearchStagingCleanupYield()
        with patch('kiwoom_monitor.infrastructure.research_storage.delete_incomplete_research_staging', side_effect=partial_delete):
            self.assertEqual(1, self.clean()['yielded'])
        self.assertTrue((path / MARKER_NAME).exists())
        self.assertEqual('READY', self.ledger()[0]['state'])
        self.assertEqual(1, self.clean()['deleted'])

    def test_crash_after_marker_removal_resumes_empty_directory(self):
        _, path = self.partial()
        def remove_without_final_rmdir(root, stage, inspection, **kwargs):
            for file in inspection['files']:
                file.unlink()
            for directory in reversed(inspection['directories']):
                directory.rmdir()
            (Path(stage) / MARKER_NAME).unlink()
            raise ResearchStagingCleanupYield()
        with patch('kiwoom_monitor.infrastructure.research_storage.delete_incomplete_research_staging', side_effect=remove_without_final_rmdir):
            self.clean()
        self.assertTrue(path.exists())
        self.assertEqual(1, self.clean()['deleted'])

    def test_source_root_change_does_not_clean_old_folder(self):
        _, path = self.partial()
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.repo.finish_campaign_worker('c', owner_token='worker', generation=self.base.claim['generation'], outcome='EXPECTED_EXIT')
        new = self.fixture.root / 'new-root'
        self.repo.save_campaign_input_source('c', self.base.job, new, nas_auto_prepare=True, nas_config_path=self.fixture.config_path)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.base.claim = self.repo.claim_campaign_worker('c', owner_token='new', lease_seconds=3600)
        self.assertEqual(0, self.clean()['deleted'])
        self.assertTrue(path.exists())

    def test_worker_discovery_cleans_before_new_preparation(self):
        _, path = self.partial()
        result = self.fixture.scan()
        self.assertEqual([], result['errors'])
        self.assertEqual(1, result['staging_cleanup'][0]['deleted'])
        self.assertEqual(1, result['registered'])
        self.assertFalse(path.exists())

    def test_v15_migration_preserves_operation_and_adds_empty_cleanup_ledger(self):
        self.partial()
        path = self.fixture.root / 'v15.sqlite3'
        with closing(sqlite3.connect(path)) as old, old, closing(self.repo._connect()) as current:
            SQLiteMigrationRunner(old, table='research_schema_migrations').apply(_MIGRATIONS[:15])
            for table in ('research_campaigns', 'research_campaign_revisions', 'research_search_experiments', 'research_search_jobs', 'research_campaign_jobs', 'research_campaign_input_sources', 'research_campaign_storage_operations'):
                columns = ','.join(row[1] for row in old.execute(f'PRAGMA table_info({table})'))
                rows = current.execute(f'SELECT {columns} FROM {table}').fetchall()
                old.executemany(f'INSERT INTO {table}({columns}) VALUES({",".join("?" for _ in rows[0])})', rows)
            before = old.execute('SELECT * FROM research_campaign_storage_operations').fetchall()
        migrated = ResearchRepository(path)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(before, connection.execute('SELECT * FROM research_campaign_storage_operations').fetchall())
        self.assertEqual(27, migrated.schema_version())
        self.assertEqual((), migrated.load_campaign_staging_cleanups(self.source_id))
