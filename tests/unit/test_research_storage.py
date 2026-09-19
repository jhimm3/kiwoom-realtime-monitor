from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.research_storage import (
    MARKER_NAME, inventory_research_storage, write_research_storage_marker,
)
from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy
import test_research_campaign_inputs as fixture_module


class ResearchStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = 'a' * 64

    def published(self):
        path = self.root / ('nas-' + self.source[:12] + '-' + 'b' * 64)
        path.mkdir()
        (path / 'manifest.json').write_text('{}')
        (path / 'observations.jsonl').write_text('data')
        write_research_storage_marker(path, self.source, kind='published', fingerprint='b' * 64)
        return path

    def inspect(self, references=(), **kwargs):
        return inventory_research_storage(self.root, self.source, references, **kwargs)

    def test_owned_orphan_is_identified_but_never_deleted_or_authorized(self):
        path = self.published()
        before = {p.name: p.read_bytes() for p in path.iterdir()}
        result = self.inspect()
        self.assertTrue(result['complete'])
        self.assertEqual('published', result['entries'][0]['kind'])
        self.assertEqual([], result['entries'][0]['protected_reasons'])
        self.assertFalse(result['deletion_authorized'])
        self.assertFalse(result['entries'][0]['deletion_authorized'])
        self.assertEqual(sum(map(len, before.values())), result['total_bytes'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in path.iterdir()})

    def test_input_file_parent_and_descendant_references_protect_directory(self):
        path = self.published()
        for ref in (path, path / 'observations.jsonl', self.root):
            self.assertIn('research_reference', self.inspect([ref])['entries'][0]['protected_reasons'])

    def test_manual_and_old_unmarked_prefix_are_not_adopted(self):
        for name in ('manual', '.nas-preparing-old', 'nas-' + self.source[:12] + '-' + 'b' * 64):
            (self.root / name).mkdir()
        result = self.inspect()
        self.assertTrue(all('unverified_ownership' in row['protected_reasons'] for row in result['entries']))

    def test_corrupt_marker_or_manifest_is_protected(self):
        path = self.published()
        (path / 'manifest.json').write_text('{"changed":true}')
        self.assertIsNone(self.inspect()['entries'][0]['kind'])
        (path / MARKER_NAME).write_text('[]')
        self.assertIsNone(self.inspect()['entries'][0]['kind'])

    def test_wrong_source_or_renamed_directory_is_protected(self):
        path = self.published()
        result = inventory_research_storage(self.root, 'c' * 64, ())
        self.assertIsNone(result['entries'][0]['kind'])
        path.rename(self.root / 'manual')
        self.assertIsNone(self.inspect()['entries'][0]['kind'])

    def test_staging_requires_live_lease_check(self):
        stage = self.root / '.nas-preparing-test123'
        stage.mkdir()
        write_research_storage_marker(stage, self.source, kind='preparing')
        entry = self.inspect()['entries'][0]
        self.assertEqual('preparing', entry['kind'])
        self.assertIn('staging_requires_lease_check', entry['protected_reasons'])

    def test_marker_creation_is_exclusive_and_evidence_unchanged(self):
        path = self.published()
        self.assertEqual(hashlib.sha256(b'{}').hexdigest(), json.loads((path / MARKER_NAME).read_text())['manifest_hash'])
        with self.assertRaises(FileExistsError):
            write_research_storage_marker(path, self.source, kind='published', fingerprint='b' * 64)
        self.assertEqual(b'{}', (path / 'manifest.json').read_bytes())

    def test_entry_limit_and_io_error_report_incomplete_without_mutation(self):
        self.published()
        result = self.inspect(max_entries=1)
        self.assertFalse(result['complete'])
        with patch.object(Path, 'iterdir', side_effect=PermissionError('denied')):
            self.assertFalse(self.inspect()['complete'])

    def test_redirected_directory_is_not_traversed(self):
        path = self.published()
        with patch('kiwoom_monitor.infrastructure.research_storage._redirected', side_effect=lambda p: p == path):
            result = self.inspect()
        self.assertFalse(result['complete'])
        self.assertEqual(0, result['total_bytes'])
        self.assertIn('redirected_path', result['entries'][0]['protected_reasons'])

    def test_cancel_is_propagated_without_deleting_any_files(self):
        path = self.published()
        with self.assertRaises(InterruptedError):
            self.inspect(checkpoint=lambda: (_ for _ in ()).throw(InterruptedError('cancel')))
        self.assertTrue((path / 'manifest.json').exists())

    def test_completed_jobs_and_cross_campaign_references_are_retained(self):
        fixture = fixture_module.CampaignInputTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        path = fixture.candidate()
        fixture.scan()
        other = fixture.watch / 'other-campaign'
        shutil.copytree(fixture.request.dataset, other)
        fixture.repo.create_campaign('other', 'other', ResearchCampaignPolicy())
        fixture.repo.enqueue_campaign_experiment('other', fixture.request.search, other)
        with closing(fixture.repo._connect()) as connection, connection:
            connection.execute("UPDATE research_campaign_jobs SET state='COMPLETED'")
        # All DB jobs/acceptances are queried, not just active jobs of one campaign.
        inventory = fixture.repo.inspect_campaign_input_storage(fixture.repo.load_campaign_input_sources('c')[0]['source_id'])
        entry = next(row for row in inventory['entries'] if Path(row['path']) == path)
        self.assertIn('research_reference', entry['protected_reasons'])
        self.assertFalse(entry['deletion_authorized'])
        other_entry = next(row for row in inventory['entries'] if Path(row['path']) == other)
        self.assertIn('research_reference', other_entry['protected_reasons'])
