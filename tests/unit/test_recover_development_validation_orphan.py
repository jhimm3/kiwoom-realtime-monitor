import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_research_development_validation as fixtures
from kiwoom_monitor import research_process as rp
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.presentation.research_dialog import _record_research_operation_owner
from scripts.recover_development_validation_orphan import recover_receipt


class SequentialOrphanRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DevelopmentValidationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.selected, self.run_id, self.spec = self.fixture.prepared_identity()
        self.repo = ResearchRepository(self.fixture.batch.request.database)
        self.operation_id = 'a' * 32
        self.owner = 'development-ui-owner-' + self.operation_id
        self.state = self.fixture.root / 'state'
        self.state.mkdir()
        stem = 'development_validation_' + self.operation_id
        self.files = {key: self.state / (stem + '.' + suffix) for key, suffix in (
            ('request', 'json'), ('result', 'result.json'),
            ('cancel', 'cancel'), ('owner', 'owner.json'))}
        self.files['request'].write_text(json.dumps(self.fixture.batch.to_dict()), encoding='utf-8')
        with patch('kiwoom_monitor.presentation.research_dialog.process_identity_document',
                   return_value={'pid': 12345, 'start_token': 'start'}):
            self.assertTrue(_record_research_operation_owner(
                self.files, SimpleNamespace(pid=12345), 'development_validation'))

    def claim(self):
        return self.repo.start_run(self.run_id, self.spec, self.selected.manifest,
                                   claim_independent=True, owner_token=self.owner)

    def recovery(self, *, execute=False, owner_state='exited'):
        with patch('scripts.recover_development_validation_orphan.process_identity_state',
                   return_value=owner_state):
            return recover_receipt(self.files['owner'], execute=execute)

    def test_owned_run_preview_cancel_and_manual_resume_generation(self):
        self.assertEqual('claimed', self.claim())
        self.assertEqual('eligible', self.recovery()['runs'][0]['state'])
        self.assertEqual('running', self.repo.load_run(self.run_id)['status'])
        self.assertEqual('cancelled', self.recovery(execute=True)['runs'][0]['state'])
        self.assertEqual('cancelled', self.repo.load_run(self.run_id)['status'])
        self.assertEqual([], self.recovery(execute=True)['runs'])
        with self.assertRaisesRegex(ValueError, 'different owner token'):
            self.claim()
        next_owner = 'development-ui-owner-' + 'b' * 32
        self.assertEqual('claimed', self.repo.start_run(
            self.run_id, self.spec, self.selected.manifest,
            claim_independent=True, owner_token=next_owner))
        self.assertEqual([], self.recovery(execute=True)['runs'])
        self.assertEqual(2, self.repo.load_independent_run_owners(next_owner)[0]['generation'])
        with self.assertRaisesRegex(ValueError, 'owner or generation changed'):
            self.repo.cancel_exited_independent_run(
                self.run_id, owner_token=self.owner, generation=1, reason='stale')
        self.assertTrue(self.repo.cancel_exited_independent_run(
            self.run_id, owner_token=next_owner, generation=2, reason='next owner exited'))
        with self.assertRaisesRegex(ValueError, 'already used'):
            self.claim()

    def test_live_unknown_missing_owner_and_manifest_do_not_cancel(self):
        self.claim()
        for state in ('running', 'unknown'):
            with self.assertRaisesRegex(ValueError, 'exit is not confirmed'):
                self.recovery(execute=True, owner_state=state)
        manifest = self.fixture.batch.request.runs_dir / self.run_id / 'manifest.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{}', encoding='utf-8')
        self.assertEqual('needs_review', self.recovery(execute=True)['runs'][0]['state'])
        self.assertEqual('running', self.repo.load_run(self.run_id)['status'])
        manifest.unlink()
        self.files['request'].write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            self.recovery(execute=True)

    def test_legacy_unowned_run_is_not_recovered(self):
        with self.assertRaisesRegex(ValueError, 'owned claim'):
            self.repo.start_run(self.run_id, self.spec, self.selected.manifest)
        self.assertEqual('claimed', self.repo.start_run(
            self.run_id, self.spec, self.selected.manifest, claim_independent=True))
        self.assertEqual([], self.recovery(execute=True)['runs'])
        self.assertEqual('running', self.repo.load_run(self.run_id)['status'])

    def test_cancelled_owned_run_cannot_be_revived_without_claim(self):
        self.claim()
        self.repo.cancel_run(self.run_id)
        with self.assertRaisesRegex(ValueError, 'owned claim'):
            self.repo.start_run(self.run_id, self.spec, self.selected.manifest)
        self.assertEqual('cancelled', self.repo.load_run(self.run_id)['status'])

    def test_cli_passes_ui_owner_into_claimed_runs(self):
        with patch('sys.stdout', new=StringIO()):
            code = rp.main(['--validate-partitions', str(self.files['request']),
                            '--result', str(self.files['result']), '--validation-owner-token', self.owner])
        self.assertEqual(0, code)
        owned = self.repo.load_independent_run_owners(self.owner)
        self.assertEqual(2, len(owned))
        self.assertTrue(all(row['status'] == 'completed' and row['generation'] == 1 for row in owned))


if __name__ == '__main__':
    unittest.main()
