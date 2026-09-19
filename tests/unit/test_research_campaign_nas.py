from __future__ import annotations

import json
import hashlib
import sqlite3
import secrets
from contextlib import closing
from datetime import date, datetime, UTC
from pathlib import Path
from unittest.mock import patch
import unittest

from kiwoom_monitor.infrastructure.central_content_client import CentralContentHttpError, CentralContentUnavailableError
from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
from kiwoom_monitor.infrastructure.research_data_source import load_research_input, campaign_input_scope, write_frozen_research_bundle
from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository, _MIGRATIONS
from kiwoom_monitor.infrastructure.persistence.schema_migrations import SQLiteMigrationRunner
from kiwoom_monitor.research_process import execute_campaign_cycle
from scripts.export_research_dataset import prepare_campaign_nas_input
import test_research_campaign_inputs as fixture_module
from test_research_bundle import write_child


class SnapshotClient:
    def __init__(self, datasets):
        self.datasets = datasets
        self.calls = []
        self.fail_page = False
        self.after_page = lambda: None

    def load_research_observations_page(self, start, end, kinds, *, subject='', watermark='', cursor=0, limit=1000):
        self.calls.append((watermark, cursor, limit))
        path = self.datasets[start.isoformat()]
        manifest = json.loads((path / 'manifest.json').read_text())
        rows = [json.loads(line) for line in (path / 'observations.jsonl').read_text().splitlines()]
        values = rows[cursor:cursor + limit]
        next_cursor = cursor + len(values) if cursor + len(values) < len(rows) else None
        self.after_page()
        return {'manifest': manifest, 'watermark': 'changed' if watermark and self.fail_page else 'fixed', 'observations': values, 'next_cursor': next_cursor}

    def load_theme_history(self, **kwargs):
        return {'snapshots': []}


class CampaignNasTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.CampaignInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.root, self.watch = self.fixture.repo, self.fixture.root, self.fixture.watch
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.repo.finish_campaign_worker('c', owner_token='worker', generation=self.fixture.claim['generation'], outcome='EXPECTED_EXIT')
        self.config_path = self.root / 'data_source.json'
        self.repo.save_campaign_input_source('c', self.fixture.job, self.watch, nas_auto_prepare=True, nas_config_path=self.config_path)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.fixture.claim = self.repo.claim_campaign_worker('c', owner_token='worker', lease_seconds=3600)
        remote = self.fixture.candidate('remote')
        self.remote = self.root / 'remote'
        remote.rename(self.remote)
        self.client = SnapshotClient({self.fixture.scope['captured_range']['start']: self.remote})
        self.secret = secrets.token_urlsafe(24)
        self.settings = DataSourceSettings(mode='personal_server', server_url='https://nas.invalid', access_token=self.secret)
        self.patches = [patch('kiwoom_monitor.research_process.DataSourceConfig.load', return_value=self.settings), patch('kiwoom_monitor.research_process.CentralContentClient', return_value=self.client)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def scan(self, seconds=0, **kwargs):
        return self.fixture.scan(seconds, **kwargs)

    def test_download_register_and_cached_probe_do_not_redownload(self):
        first = self.scan()
        self.assertEqual([], first['errors'])
        self.assertEqual(1, first['nas_prepared'])
        self.assertEqual(1, first['registered'])
        storage = first['storage_inventory'][0]
        self.assertTrue(storage['complete'])
        self.assertEqual('published', storage['entries'][0]['kind'])
        self.assertIn('research_reference', storage['entries'][0]['protected_reasons'])
        self.assertFalse(storage['deletion_authorized'])
        self.assertTrue(list(self.watch.glob('nas-*/manifest.json')))
        with patch('scripts.export_research_dataset.export_daily_dataset') as export:
            second = self.scan(61)
        self.assertEqual([], second['errors'])
        export.assert_not_called()
        self.assertEqual(2, len(self.repo.load_campaign_jobs('c')))
        self.assertEqual([], list(self.watch.glob('.nas-preparing-*')))

    def test_http_failure_does_not_store_server_detail_or_token(self):
        with patch.object(self.client, 'load_research_observations_page', side_effect=CentralContentHttpError(401, self.secret)):
            result = self.scan()
        self.assertIn('HTTP 401', result['errors'][0])
        self.assertNotIn(self.secret, self.fixture.request.database.read_bytes().decode('latin-1'))
        self.assertEqual(0, self.repo.load_campaign_worker('c')['failure_count'])
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))

    def test_nas_offline_is_source_backoff_without_direct_kiwoom(self):
        with patch.object(self.client, 'load_research_observations_page', side_effect=CentralContentUnavailableError('offline')):
            result = self.scan()
        self.assertIn('unavailable', result['errors'][0])
        self.assertEqual('BACKOFF', self.repo.load_campaign_input_sources('c')[0]['state'])
        self.assertEqual('RUNNING', self.repo.load_campaign('c')['desired_state'])

    def test_direct_mode_does_not_attempt_nas_or_kiwoom(self):
        with patch('kiwoom_monitor.research_process.DataSourceConfig.load', return_value=DataSourceSettings()):
            result = self.scan()
        self.assertIn('direct Kiwoom fallback is not used', result['errors'][0])
        self.assertEqual([], self.client.calls)

    def test_cancel_during_download_does_not_publish_or_register(self):
        self.two_rows()
        cancelled = [False]
        self.client.after_page = lambda: cancelled.__setitem__(0, bool(self.client.calls[-1][0]))
        result = self.scan(cancel_requested=lambda: cancelled[0])
        self.assertEqual([], result['errors'])
        self.assertEqual([], list(self.watch.glob('nas-*')))
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))

    def two_rows(self):
        row = json.loads((self.remote / 'observations.jsonl').read_text())
        second = {**row, 'ordinal': 2, 'revision_id': 'second', 'accepted_sequence': 2}
        encoded = (json.dumps(row) + '\n' + json.dumps(second) + '\n').encode()
        (self.remote / 'observations.jsonl').write_bytes(encoded)
        path = self.remote / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest.update(revision_count=2, revision_ids_hash=hashlib.sha256((row['revision_id'] + '\nsecond').encode()).hexdigest(), observations_file_hash=hashlib.sha256(encoded).hexdigest())
        path.write_text(json.dumps(manifest))

    def test_snapshot_change_during_pagination_is_rejected(self):
        self.two_rows()
        self.client.fail_page = True
        result = self.scan()
        self.assertIn('watermark changed', result['errors'][0])
        self.assertEqual([], list(self.watch.glob('nas-*')))
        self.assertEqual([], list(self.watch.glob('.nas-preparing-*')))

    def test_full_campaign_skips_download_then_prepares_when_capacity_returns(self):
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.repo.finish_campaign_worker('c', owner_token='worker', generation=self.fixture.claim['generation'], outcome='EXPECTED_EXIT')
        self.repo.revise_campaign_policy('c', ResearchCampaignPolicy(max_active_jobs=1), expected_revision=1)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.fixture.claim = self.repo.claim_campaign_worker('c', owner_token='worker', lease_seconds=3600)
        self.assertEqual(1, self.scan()['waiting_backlog'])
        self.assertEqual([], self.client.calls)
        self.assertEqual('', self.repo.load_campaign_input_sources('c')[0]['remote_signature'])
        execute_campaign_cycle(self.repo, 'c', self.fixture.request.runs_dir, worker_claim=self.fixture.claim)
        self.assertEqual(1, self.scan(61)['registered'])

    def test_v13_source_fields_are_preserved_and_nas_defaults_off(self):
        path = self.root / 'v13.sqlite3'
        now = datetime.now(UTC).isoformat()
        spec = self.fixture.request.search
        with closing(sqlite3.connect(path)) as connection, connection:
            SQLiteMigrationRunner(connection, table='research_schema_migrations').apply(_MIGRATIONS[:13])
            connection.execute('INSERT INTO research_search_experiments VALUES(?,?,?)', (spec.experiment_id, now, json.dumps(spec.evidence_dict())))
            connection.execute("INSERT INTO research_search_jobs(job_id,experiment_id,dataset_id,dataset_hash,status,created_at,updated_at) VALUES('job',?,?,?,'queued',?,?)", (spec.experiment_id, spec.dataset_id, spec.dataset_hash, now, now))
            connection.execute("INSERT INTO research_campaigns(campaign_id,name,revision,desired_state,operational_state,created_at,updated_at) VALUES('old','old',1,'PAUSED','PAUSED',?,?)", (now, now))
            connection.execute('INSERT INTO research_campaign_revisions VALUES(?,1,?,?)', ('old', json.dumps(ResearchCampaignPolicy().to_dict()), now))
            connection.execute('INSERT INTO research_campaign_workers(campaign_id,updated_at) VALUES(?,?)', ('old', now))
            connection.execute("INSERT INTO research_campaign_jobs(campaign_id,job_id,source_kind,input_path,request_json,state,accepted_sequence) VALUES('old','job','hypothesis',?,?,'PENDING',1)", (str(self.fixture.request.dataset), json.dumps(spec.to_dict())))
            connection.execute("INSERT INTO research_campaign_input_sources VALUES('source','old','job',?,0,'','BACKOFF',2,?,'kept')", (str(self.watch), now))
            before = connection.execute('SELECT * FROM research_campaign_input_sources').fetchone()
        migrated = ResearchRepository(path)
        row = migrated.load_campaign_input_sources('old')[0]
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(before, connection.execute('SELECT * FROM research_campaign_input_sources').fetchone()[:len(before)])
        self.assertEqual(23, migrated.schema_version())
        self.assertEqual(0, row['nas_auto_prepare'])
        self.assertEqual('', row['nas_config_path'])
        self.assertEqual('', row['remote_signature'])

    def test_cached_signature_survives_repository_restart(self):
        self.scan()
        self.fixture.repo = type(self.repo)(self.fixture.request.database)
        with patch('scripts.export_research_dataset.export_daily_dataset') as export:
            self.scan(61)
        export.assert_not_called()
        source = self.fixture.repo.load_campaign_input_sources('c')[0]
        self.assertTrue(source['remote_signature'])
        self.assertEqual(str(self.config_path.resolve()), source['nas_config_path'])

    def test_signature_ack_is_fenced_after_pause(self):
        self.scan()
        source = self.repo.load_campaign_input_sources('c')[0]
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        with self.assertRaisesRegex(ValueError, 'no longer active'):
            self.repo.record_campaign_prepared_input(source['source_id'], 'wrong', self.fixture.claim)
        self.assertEqual(source['remote_signature'], self.repo.load_campaign_input_sources('c')[0]['remote_signature'])

    def test_export_quota_failure_leaves_no_published_or_staging_files(self):
        source = self.repo.load_campaign_input_sources('c')[0]
        with self.assertRaisesRegex(ValueError, 'encoded_byte_limit'):
            prepare_campaign_nas_input(self.client, source, self.fixture.request.dataset, campaign_input_scope(load_research_input(self.fixture.request.dataset)), set(), max_encoded_bytes=1)
        self.assertEqual([], list(self.watch.iterdir()))

    def test_probe_scope_mismatch_does_not_download(self):
        path = self.remote / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['subject'] = '000660'
        path.write_text(json.dumps(manifest))
        with patch('scripts.export_research_dataset.export_daily_dataset') as export:
            result = self.scan()
        self.assertIn('scope', result['errors'][0])
        export.assert_not_called()

    def test_incomplete_probe_is_rejected_before_cache_or_publication(self):
        original = self.client.load_research_observations_page
        def incomplete(*args, **kwargs):
            page = original(*args, **kwargs)
            page['observations'] = []
            return page
        with patch.object(self.client, 'load_research_observations_page', side_effect=incomplete):
            self.assertIn('incomplete', self.scan()['errors'][0])
        self.assertEqual('', self.repo.load_campaign_input_sources('c')[0]['remote_signature'])
        self.assertEqual([], list(self.watch.glob('nas-*')))

    def test_daily_bundle_is_published_with_original_daily_boundaries(self):
        template = self.root / 'bundle'
        remote_root = self.root / 'bundle-remote'
        days = (date(2026, 9, 12), date(2026, 9, 13))
        paths = tuple(write_child(template, day) for day in days)
        write_frozen_research_bundle(template, paths)
        remote_paths = tuple(write_child(remote_root, day) for day in days)
        client = SnapshotClient({json.loads((remote_root / path / 'manifest.json').read_text())['captured_range']['start']: remote_root / path for path in remote_paths})
        source = self.repo.load_campaign_input_sources('c')[0]
        scope = campaign_input_scope(load_research_input(template, session_profile='krx-regular/v1'))
        result = prepare_campaign_nas_input(client, source, template, scope, set())
        self.assertEqual('prepared', result['status'])
        published = Path(result['path'])
        dataset = load_research_input(published, session_profile='krx-regular/v1')
        self.assertEqual(scope, campaign_input_scope(dataset))
        self.assertEqual([day.isoformat() for day in days], [entry['selected_date'] for entry in json.loads((published / 'manifest.json').read_text())['children']])


if __name__ == '__main__':
    unittest.main()
