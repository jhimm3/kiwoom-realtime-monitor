from __future__ import annotations

import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
from kiwoom_monitor.infrastructure.central_content_client import CentralContentUnavailableError
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import (
    campaign_input_fingerprint, campaign_input_scope, load_research_input,
)
from kiwoom_monitor.research_process import execute_campaign_cycle
from scripts.export_research_dataset import prepare_campaign_nas_input
import test_research_campaign_nas as nas_fixture_module


class CompletedInputRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = nas_fixture_module.CampaignNasTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo, self.base = self.fixture.repo, self.fixture.fixture
        self.source_id = self.base.source_id

    def publish_without_registration(self):
        baseline = self.base.request.dataset
        dataset = load_research_input(baseline)
        fingerprint = campaign_input_fingerprint(dataset)
        scope = campaign_input_scope(dataset)
        self.repo.initialize_campaign_input_source(
            self.source_id, scope, fingerprint, baseline, self.base.job,
            hashlib.sha256((baseline / 'manifest.json').read_bytes()).hexdigest(), self.base.claim)
        source = self.repo.load_campaign_input_sources('c')[0]
        operation = self.repo.begin_campaign_storage_preparation(self.source_id, self.base.claim)
        prepared = prepare_campaign_nas_input(
            self.fixture.client, source, baseline, scope, {fingerprint},
            begin_preparation=lambda: operation,
            publication_context=lambda: self.repo.campaign_storage_publication(operation, self.base.claim),
            record_staging=lambda path: self.repo.record_campaign_storage_staging(operation, self.base.claim, path))
        self.assertEqual('prepared', prepared['status'])
        self.repo.finish_campaign_storage_preparation(
            operation, self.base.claim, outcome='PUBLISHED', input_path=prepared['path'])
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))
        self.assertEqual('', self.repo.load_campaign_input_sources('c')[0]['remote_signature'])
        self.fixture.client.calls.clear()
        return Path(prepared['path'])

    def configure(self, *, cap=0, backlog=None):
        self.repo.set_campaign_desired_state('c', 'PAUSED')
        self.repo.finish_campaign_worker('c', owner_token='worker', generation=self.base.claim['generation'], outcome='EXPECTED_EXIT')
        if backlog is not None:
            self.repo.revise_campaign_policy('c', ResearchCampaignPolicy(max_active_jobs=backlog), expected_revision=1)
        self.repo.save_campaign_input_source(
            'c', self.base.job, self.fixture.watch, nas_auto_prepare=True,
            nas_config_path=self.fixture.config_path, storage_cap_bytes=cap)
        self.repo.set_campaign_desired_state('c', 'RUNNING')
        self.base.claim = self.repo.claim_campaign_worker('c', owner_token='worker', lease_seconds=3600)

    def test_published_orphan_registers_even_when_capacity_is_full(self):
        path = self.publish_without_registration()
        before = {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()}
        self.configure(cap=1)
        result = self.fixture.scan()
        self.assertEqual(1, result['registered'])
        self.assertEqual(1, result['waiting_storage'])
        self.assertEqual([], result['errors'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in path.iterdir() if p.is_file()})
        self.assertEqual('', self.repo.load_campaign_input_sources('c')[0]['remote_signature'])
        self.assertEqual(2, len(self.repo.load_campaign_input_acceptances(self.source_id)))
        self.assertEqual(0, self.fixture.scan(61)['registered'])
        self.assertEqual(2, len(self.repo.load_campaign_jobs('c')))

    def test_orphan_is_registered_before_offline_probe_and_survives_restart(self):
        path = self.publish_without_registration()
        with patch.object(self.fixture.client, 'load_research_observations_page', side_effect=CentralContentUnavailableError('offline')):
            result = self.fixture.scan()
        self.assertEqual(1, result['registered'])
        self.assertIn('unavailable', result['errors'][0])
        self.base.repo = ResearchRepository(self.repo.path)
        recovered = self.fixture.scan(61)
        self.assertEqual(0, recovered['registered'])
        self.assertEqual([], recovered['errors'])
        self.assertTrue(self.base.repo.load_campaign_input_sources('c')[0]['remote_signature'])
        self.assertTrue((path / 'manifest.json').is_file())

    def test_recovered_input_fills_backlog_without_loading_connection_settings(self):
        path = self.publish_without_registration()
        self.configure(cap=1, backlog=2)
        with patch('kiwoom_monitor.research_process.DataSourceConfig.load') as settings:
            result = self.fixture.scan()
        settings.assert_not_called()
        self.assertEqual([], self.fixture.client.calls)
        self.assertEqual(1, result['registered'])
        self.assertEqual(1, result['waiting_backlog'])
        self.assertEqual([], result['errors'])
        self.assertTrue(path.is_dir())

    def test_recovery_never_bypasses_frozen_baseline_manifest(self):
        path = self.publish_without_registration()
        manifest = self.base.request.dataset / 'manifest.json'
        manifest.write_bytes(manifest.read_bytes() + b'\n')
        result = self.fixture.scan()
        self.assertEqual(0, result['registered'])
        self.assertIn('source template manifest was modified', result['errors'][0])
        self.assertEqual([], self.fixture.client.calls)
        self.assertTrue(path.is_dir())

    def test_pause_between_local_load_and_enqueue_prevents_recovery(self):
        path = self.publish_without_registration()
        original = load_research_input
        def pause_after_load(input_path, **kwargs):
            dataset = original(input_path, **kwargs)
            self.repo.set_campaign_desired_state('c', 'PAUSED')
            return dataset
        with patch('kiwoom_monitor.research_process.load_research_input', side_effect=pause_after_load):
            result = self.fixture.scan()
        self.assertEqual(0, result['registered'])
        self.assertTrue(result['errors'])
        self.assertEqual(1, len(self.repo.load_campaign_jobs('c')))
        self.assertEqual([], self.fixture.client.calls)
        self.assertTrue(path.is_dir())

    def test_completed_research_reference_stays_protected(self):
        path = self.publish_without_registration()
        self.configure(cap=1)
        self.fixture.scan()
        execute_campaign_cycle(self.repo, 'c', self.base.request.runs_dir, worker_claim=self.base.claim)
        execute_campaign_cycle(self.repo, 'c', self.base.request.runs_dir, worker_claim=self.base.claim)
        self.assertTrue(all(row['state'] == 'COMPLETED' for row in self.repo.load_campaign_jobs('c')))
        inventory = self.repo.inspect_campaign_input_storage(self.source_id)
        entry = next(row for row in inventory['entries'] if row['path'] == str(path.resolve()))
        self.assertIn('research_reference', entry['protected_reasons'])
        self.assertFalse(inventory['deletion_authorized'])
        self.repo.cleanup_campaign_incomplete_staging(self.source_id, self.base.claim)
        self.assertTrue((path / 'manifest.json').is_file())

    def test_out_of_scope_complete_input_is_preserved_without_registration(self):
        self.base.candidate('other', scope={'subject': 'different'})
        self.configure(cap=1)
        result = self.fixture.scan()
        self.assertEqual(1, result['out_of_scope'])
        self.assertEqual(0, result['registered'])
        self.assertEqual(1, result['waiting_storage'])
        self.assertTrue((self.fixture.watch / 'other' / 'manifest.json').is_file())

    def test_accepted_manifest_change_is_checked_before_network(self):
        path = self.publish_without_registration()
        self.configure(backlog=2)
        self.fixture.scan()
        # Make room for discovery while keeping the acceptance reference frozen.
        execute_campaign_cycle(self.repo, 'c', self.base.request.runs_dir, worker_claim=self.base.claim)
        manifest = path / 'manifest.json'
        manifest.write_bytes(manifest.read_bytes() + b'\n')
        result = self.fixture.scan(61)
        self.assertIn('accepted frozen input manifest was modified', result['errors'][0])
        self.assertEqual([], self.fixture.client.calls)
        self.assertTrue(path.is_dir())
