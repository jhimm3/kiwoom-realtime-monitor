from copy import deepcopy
from io import StringIO
import json
import unittest
from unittest.mock import patch

import test_research_final_preparation as fixtures
from kiwoom_monitor import research_process as rp
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository


class FinalHoldoutCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FinalPreparationTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.request = self.fixture.request
        self.candidate_hash = self.fixture.batch.candidate_spec_hashes[0]
        candidate = {'mode': self.request.mode, 'family': self.request.family,
            'dataset': str(self.request.dataset), 'database': str(self.request.database),
            'runs_dir': str(self.request.runs_dir), 'session_profile': self.request.session_profile,
            'strategy': self.request.strategy.to_dict(), 'execution': self.request.execution.to_dict(),
            'evaluation': self.request.evaluation.to_dict(),
            'resource_budget': {'memory_mb': self.request.resource_limits.memory_mb,
                                'cpu_duty_percent': self.request.resource_limits.cpu_duty_percent}}
        self.document = {'version': 'independent_final_holdout_request/v1',
            'batch': self.fixture.batch.to_dict(), 'candidates': [candidate],
            'access': {'request_id': 'final-cli-access', 'accessed_at': self.fixture.at},
            'owner_token': 'final-cli-owner', 'recoveries': {}}
        self.path = self.fixture.root / 'final-request.json'
        self.result = self.fixture.root / 'final-result.json'
        self.cancel = self.fixture.root / 'final.cancel'
        self.write()

    def write(self, document=None, path=None):
        target = path or self.path
        target.write_text(json.dumps(document or self.document), encoding='utf-8')
        return target

    def cli(self, *, result=None, cancel=None):
        arguments = ['--evaluate-final', str(self.path), '--result', str(result or self.result)]
        if cancel is not False:
            arguments.extend(['--cancel', str(cancel or self.cancel)])
        with patch('sys.stdout', new=StringIO()):
            code = rp.main(arguments)
        return code, json.loads((result or self.result).read_text(encoding='utf-8'))

    def test_parser_roundtrip_and_cli_execute_locked_final_request(self):
        parsed = rp.load_final_holdout_execution_request(self.path)
        self.assertEqual(self.fixture.batch.to_dict(), parsed.batch.to_dict())
        self.assertEqual(self.fixture.batch.batch_id, parsed.batch.batch_id)
        self.assertEqual(self.document['access']['request_id'], parsed.access_request_id)
        self.assertEqual({}, parsed.recovery_mapping())
        snapshot = parsed.to_dict()
        self.assertEqual(str(self.request.dataset.resolve()), snapshot['candidates'][0]['dataset'])
        normalized = self.fixture.root / 'normalized-final-request.json'
        self.write(snapshot, normalized)
        self.assertEqual(snapshot, rp.load_final_holdout_execution_request(normalized).to_dict())
        code, result = self.cli()
        self.assertEqual((0, 'ok', 'independent_final_holdout', 'COMPLETED', 'COMPLETED'),
            (code, result['status'], result['kind'], result['batch_status'], result['candidates'][0]['state']))
        repository = ResearchRepository(self.request.database)
        self.assertEqual(1, len(repository.load_final_holdout_events(self.fixture.batch.window_id)))
        self.assertEqual('COMPLETED', repository.load_final_holdout_executions(self.fixture.batch.batch_id)[0]['state'])

    def test_failed_cli_is_terminal_until_explicit_recovery_request(self):
        with patch.object(rp, 'execute_research', side_effect=ValueError('final CLI fixture')) as execute:
            first_code, first = self.cli()
        self.assertEqual((0, 'FAILED', 1), (first_code, first['candidates'][0]['state'], execute.call_count))
        with patch.object(rp, 'execute_research', side_effect=AssertionError('terminal candidate cannot rerun')) as retry:
            second_code, second = self.cli()
        self.assertEqual((0, 'FAILED', 0), (second_code, second['candidates'][0]['state'], retry.call_count))
        document = deepcopy(self.document)
        document['owner_token'] = 'final-cli-recovery-owner'
        document['recoveries'] = {self.candidate_hash: {
            'request_id': 'final-cli-recovery-1', 'reason': 'reviewed CLI fixture failure'}}
        self.write(document)
        recovery_code, recovered = self.cli()
        self.assertEqual((0, 'COMPLETED', 'final-cli-recovery-1'),
            (recovery_code, recovered['candidates'][0]['state'], recovered['candidates'][0]['recovery_request_id']))
        repository = ResearchRepository(self.request.database)
        self.assertEqual(2, repository.load_final_holdout_executions(self.fixture.batch.batch_id)[0]['generation'])
        self.assertEqual('CLAIMED', repository.load_final_holdout_recoveries(self.fixture.batch.batch_id)[0]['state'])

    def test_cancel_before_preparation_writes_bounded_result_without_ledger(self):
        self.cancel.write_text('cancel', encoding='utf-8')
        code, result = self.cli()
        self.assertEqual((2, 'cancelled'), (code, result['status']))
        self.assertFalse(self.request.database.exists())

    def test_resource_blocked_execution_uses_distinct_exit_status(self):
        with patch.object(rp, 'execute_research', side_effect=rp.ResearchResourceBlocked('memory fixture')):
            code, result = self.cli()
        self.assertEqual((3, 'resource_blocked', 'CANCELLED'),
                         (code, result['status'], result['candidates'][0]['state']))

    def test_parser_rejects_unknown_fields_candidate_set_recovery_and_identity_before_ledger(self):
        cases = []
        extra = deepcopy(self.document); extra['extra'] = True; cases.append(extra)
        candidate_extra = deepcopy(self.document); candidate_extra['candidates'][0]['search'] = {}; cases.append(candidate_extra)
        duplicate = deepcopy(self.document); duplicate['candidates'].append(deepcopy(duplicate['candidates'][0])); cases.append(duplicate)
        changed_hash = deepcopy(self.document); changed_hash['batch']['candidate_spec_hashes'] = ['f' * 64]; cases.append(changed_hash)
        unknown_recovery = deepcopy(self.document); unknown_recovery['recoveries'] = {
            'f' * 64: {'request_id': 'unknown', 'reason': 'not locked'}}; cases.append(unknown_recovery)
        naive = deepcopy(self.document); naive['access']['accessed_at'] = '2026-09-15T00:00:00'; cases.append(naive)
        empty_owner = deepcopy(self.document); empty_owner['owner_token'] = ''; cases.append(empty_owner)
        for document in cases:
            with self.subTest(keys=document.keys()):
                self.write(document)
                with self.assertRaises(ValueError):
                    rp.load_final_holdout_execution_request(self.path)
        self.assertFalse(self.request.database.exists())

    def test_large_request_and_request_inside_dataset_are_rejected(self):
        self.path.write_bytes(b' ' * (4 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, '4 MiB'):
            rp.load_final_holdout_execution_request(self.path)
        inside = self.request.dataset / 'final-request.json'
        self.write(self.document, inside)
        with self.assertRaisesRegex(ValueError, 'outside the frozen dataset'):
            rp.load_final_holdout_execution_request(inside)

    def test_cli_rejects_result_or_cancel_collision_before_source_or_database_changes(self):
        source_manifest = (self.request.dataset / 'manifest.json').read_bytes()
        for result, cancel in ((self.path, self.cancel),
                               (self.request.database, self.cancel),
                               (self.request.dataset / 'bad-result.json', self.cancel),
                               (self.request.runs_dir / 'bad-result.json', self.cancel),
                               (self.result, self.result)):
            with self.subTest(result=result, cancel=cancel), patch('sys.stderr', new=StringIO()), self.assertRaises(SystemExit):
                rp.main(['--evaluate-final', str(self.path), '--result', str(result), '--cancel', str(cancel)])
        self.assertEqual(source_manifest, (self.request.dataset / 'manifest.json').read_bytes())
        self.assertFalse(self.request.database.exists())

    def test_cli_source_binding_failure_is_reported_without_access_or_execution(self):
        document = deepcopy(self.document)
        document['batch']['dataset_hash'] = 'f' * 64
        self.write(document)
        code, result = self.cli()
        self.assertEqual((1, 'failed'), (code, result['status']))
        self.assertFalse(self.request.database.exists())


if __name__ == '__main__':
    unittest.main()
