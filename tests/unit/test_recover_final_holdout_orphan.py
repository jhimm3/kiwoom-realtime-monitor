import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_research_final_preparation as fixtures
from kiwoom_monitor import research_process as rp
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.presentation.research_dialog import _record_research_operation_owner
from scripts import run_research as runner
from scripts.recover_final_holdout_orphan import recover_receipt


class FinalOrphanRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FinalPreparationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.prepared = self.fixture.prepare()
        self.candidate = self.prepared.candidates[0]
        self.repo = ResearchRepository(self.candidate.database)
        self.operation = 'final_holdout_' + 'a' * 32
        self.owner = 'final-ui-owner-' + 'a' * 32
        self.state = self.fixture.root / 'state'
        self.state.mkdir()
        self.files = {key: self.state / (self.operation + '.' + suffix) for key, suffix in (
            ('request', 'json'), ('result', 'result.json'),
            ('cancel', 'cancel'), ('owner', 'owner.json'))}
        self.request = rp.FinalHoldoutExecutionRequest(
            self.prepared.batch, (self.candidate,), 'access', self.fixture.at, self.owner)
        self.files['request'].write_text(json.dumps(self.request.to_dict()), encoding='utf-8')
        with patch('kiwoom_monitor.presentation.research_dialog.process_identity_document',
                   return_value={'pid': 12345, 'start_token': 'start'}):
            self.assertTrue(_record_research_operation_owner(
                self.files, SimpleNamespace(pid=12345), 'final_holdout'))
        self.candidate_hash = rp.final_candidate_spec_hash(
            self.candidate, self.prepared.implementation_hash)
        self.run_id, spec = runner.research_run_identity(
            self.prepared.dataset, self.candidate.strategy, self.candidate.execution,
            self.candidate.evaluation, session_profile=self.candidate.session_profile,
            execution_scope='independent_final_holdout/v1')
        self.assertEqual('claimed', self.repo.claim_final_holdout_execution(
            self.prepared.batch, candidate_spec_hash=self.candidate_hash,
            run_id=self.run_id, spec=spec, input_manifest=self.prepared.dataset.manifest,
            owner_token=self.owner))

    def run_recovery(self, *, execute=False, owner_state='exited'):
        with patch('scripts.recover_final_holdout_orphan.process_identity_state', return_value=owner_state):
            return recover_receipt(self.files['owner'], execute=execute)

    def test_preview_does_not_mutate_and_exited_owner_can_be_cancelled_once(self):
        self.assertEqual('eligible', self.run_recovery()['candidates'][0]['state'])
        self.assertEqual('running', self.repo.load_run(self.run_id)['status'])
        self.assertEqual('cancelled', self.run_recovery(execute=True)['candidates'][0]['state'])
        self.assertEqual('cancelled', self.repo.load_run(self.run_id)['status'])
        row = self.repo.load_final_holdout_executions(self.prepared.batch.batch_id)[0]
        self.assertEqual(('CANCELLED', 1), (row['state'], row['generation']))
        self.assertEqual([], self.run_recovery(execute=True)['candidates'])

    def test_running_owner_hash_change_and_completed_artifact_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exit is not confirmed'):
            self.run_recovery(execute=True, owner_state='running')
        manifest = self.candidate.runs_dir / self.run_id / 'manifest.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{}', encoding='utf-8')
        self.assertEqual('needs_review', self.run_recovery(execute=True)['candidates'][0]['state'])
        self.assertEqual('running', self.repo.load_run(self.run_id)['status'])
        manifest.unlink()
        self.files['request'].write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            self.run_recovery(execute=True)

    def test_newer_owner_or_generation_cannot_be_cancelled_by_old_receipt(self):
        with self.assertRaisesRegex(ValueError, 'owner or generation changed'):
            self.repo.cancel_exited_final_holdout_execution(
                self.prepared.batch, candidate_spec_hash=self.candidate_hash,
                run_id=self.run_id, owner_token=self.owner, generation=2,
                reason='stale')
        with self.repo._connect() as connection:
            connection.execute('UPDATE research_final_holdout_executions SET owner_token=? '
                               'WHERE batch_id=? AND candidate_spec_hash=?',
                               ('different-owner', self.prepared.batch.batch_id, self.candidate_hash))
        self.assertEqual([], self.run_recovery(execute=True)['candidates'])
        self.assertEqual('running', self.repo.load_run(self.run_id)['status'])

    def test_terminal_result_and_unknown_process_are_not_recovered(self):
        with self.assertRaisesRegex(ValueError, 'exit is not confirmed'):
            self.run_recovery(execute=True, owner_state='unknown')
        self.files['result'].write_text(json.dumps({'status': 'ok'}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'terminal or invalid result'):
            self.run_recovery(execute=True)
        self.assertEqual('running', self.repo.load_run(self.run_id)['status'])


if __name__ == '__main__':
    unittest.main()
