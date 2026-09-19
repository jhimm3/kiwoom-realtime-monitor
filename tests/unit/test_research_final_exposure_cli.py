from copy import deepcopy
from io import StringIO
import json
import unittest
from unittest.mock import patch

import test_research_final_cli as fixtures
from kiwoom_monitor import research_process as rp
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository


class FinalHoldoutExposureCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FinalHoldoutCliTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        with patch('sys.stdout', new=StringIO()):
            self.assertEqual(0, rp.main(['--evaluate-final', str(self.fixture.path),
                '--result', str(self.fixture.result), '--cancel', str(self.fixture.cancel)]))
        self.path = self.fixture.fixture.root / 'exposure-request.json'
        self.result = self.fixture.fixture.root / 'exposure-result.json'
        self.cancel = self.fixture.fixture.root / 'exposure.cancel'
        self.document = {'version': 'final_holdout_exposure_request/v1',
            'database': str(self.fixture.request.database),
            'batch': self.fixture.fixture.batch.to_dict(),
            'exposure': {'request_id': 'exposure-cli-1',
                         'exposed_at': '2026-09-16T00:00:00+00:00',
                         'reason': 'used final evidence to design the next hypothesis'}}
        self.write()

    def write(self, document=None):
        self.path.write_text(json.dumps(document or self.document), encoding='utf-8')

    def cli(self):
        with patch('sys.stdout', new=StringIO()):
            code = rp.main(['--expose-final', str(self.path), '--result', str(self.result),
                            '--cancel', str(self.cancel)])
        return code, json.loads(self.result.read_text(encoding='utf-8'))

    def test_parser_roundtrip_and_irreversible_exposure(self):
        parsed = rp.load_final_holdout_exposure_request(self.path)
        self.assertEqual(self.document, parsed.to_dict())
        code, result = self.cli()
        self.assertEqual((0, 'ok', 'EXPOSED_DEVELOPMENT', True),
                         (code, result['status'], result['state'], result['recorded']))
        repository = ResearchRepository(self.fixture.request.database)
        self.assertEqual('EXPOSED_DEVELOPMENT',
                         repository.load_final_holdout_window(self.fixture.fixture.batch.window_id)['state'])
        self.assertEqual('EXPOSED_DEVELOPMENT',
                         repository.load_final_holdout_events(self.fixture.fixture.batch.window_id)[-1]['event_type'])

    def test_same_request_is_idempotent(self):
        first_code, first = self.cli()
        second_code, second = self.cli()
        self.assertEqual((0, True, 0, False),
                         (first_code, first['recorded'], second_code, second['recorded']))
        events = ResearchRepository(self.fixture.request.database).load_final_holdout_events(
            self.fixture.fixture.batch.window_id)
        self.assertEqual(2, len(events))

    def test_cancel_before_mutation_keeps_window_reserved(self):
        self.cancel.write_text('cancel', encoding='ascii')
        code, result = self.cli()
        self.assertEqual((2, 'cancelled'), (code, result['status']))
        window = ResearchRepository(self.fixture.request.database).load_final_holdout_window(
            self.fixture.fixture.batch.window_id)
        self.assertEqual('FINAL_RESERVED', window['state'])

    def test_changed_batch_is_rejected_without_exposure(self):
        changed = deepcopy(self.document)
        changed['batch']['candidate_spec_hashes'] = ['f' * 64]
        self.write(changed)
        code, result = self.cli()
        self.assertEqual((1, 'failed'), (code, result['status']))
        window = ResearchRepository(self.fixture.request.database).load_final_holdout_window(
            self.fixture.fixture.batch.window_id)
        self.assertEqual('FINAL_RESERVED', window['state'])

    def test_contract_size_fields_time_reason_and_existing_database(self):
        cases = []
        extra = deepcopy(self.document); extra['extra'] = True; cases.append(extra)
        missing = deepcopy(self.document); missing['database'] = str(self.path.parent / 'missing.db'); cases.append(missing)
        naive = deepcopy(self.document); naive['exposure']['exposed_at'] = '2026-09-16T00:00:00'; cases.append(naive)
        empty = deepcopy(self.document); empty['exposure']['reason'] = ''; cases.append(empty)
        unknown = deepcopy(self.document); unknown['exposure']['other'] = True; cases.append(unknown)
        for document in cases:
            with self.subTest(document=document):
                self.write(document)
                with self.assertRaises(ValueError):
                    rp.load_final_holdout_exposure_request(self.path)
        self.path.write_bytes(b' ' * (1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, '1 MiB'):
            rp.load_final_holdout_exposure_request(self.path)

    def test_result_cancel_request_and_database_collisions_are_rejected(self):
        for result, cancel in ((self.path, self.cancel),
                               (self.fixture.request.database, self.cancel),
                               (self.result, self.result)):
            with self.subTest(result=result, cancel=cancel), patch('sys.stderr', new=StringIO()), self.assertRaises(SystemExit):
                rp.main(['--expose-final', str(self.path), '--result', str(result), '--cancel', str(cancel)])
        self.assertEqual('FINAL_RESERVED', ResearchRepository(self.fixture.request.database)
            .load_final_holdout_window(self.fixture.fixture.batch.window_id)['state'])


if __name__ == '__main__':
    unittest.main()
