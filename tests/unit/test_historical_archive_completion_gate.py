from contextlib import closing
import json
import sqlite3
import stat
import unittest
from unittest.mock import patch

from tests.unit import test_prepared_historical_archive_v2 as archive_fixtures
from scripts import prepared_historical_archive_v2 as api
from scripts.capture_historical_archive_sources_v2 import capture, finish_coverage
from scripts.historical_archive_completion_gate import audit_and_seal, verified_publication
from scripts.audit_prepared_historical_archive_v2 import audit


class CompletionGateTests(unittest.TestCase):
    def setUp(self):
        fixture = archive_fixtures.ArchiveV2Tests('test_real_source_coverage_seals_readonly_and_recovers_receipt')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.seed = fixture.empty_seed()
        fixture.prepare_both()
        api.finalize_events(fixture.archive); api.build_projection(fixture.archive)
        for scope in api.SCOPES:
            capture(fixture.archive, fixture.raw_fixture(scope), scope)
        self.assertEqual('complete', finish_coverage(fixture.archive)['state'])
        self.path = fixture.archive
        self.receipt = fixture.root/'single-seal.json'
        self.digest = api.code_digest()
        self.addCleanup(lambda: self.path.chmod(stat.S_IWRITE) if self.path.exists() else None)

    def complete(self):
        return audit_and_seal(self.path, self.seed, self.receipt, 'full-fixture-v2', self.digest)

    def test_one_real_full_audit_and_checksum_reuse_without_second_audit(self):
        with patch('scripts.seal_prepared_historical_archive_v2.audit', wraps=audit) as called:
            result = self.complete()
            publication, gate = verified_publication(self.path, self.receipt, 'full-fixture-v2', self.digest)
            replay = self.complete()
        self.assertEqual(1, called.call_count)
        self.assertTrue(called.call_args.kwargs['full_integrity_check'])
        self.assertTrue(result['ready_for_seal'])
        self.assertEqual('ok', gate['integrity_check'])
        self.assertEqual(result['publication_receipt'], publication)
        self.assertEqual(result, replay)

    def test_real_audit_blocker_prevents_seal_and_receipt(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM archive_targets_v2 WHERE rowid=(SELECT MIN(rowid) FROM archive_targets_v2)')
        with self.assertRaisesRegex(ValueError, 'publication blockers'):
            self.complete()
        self.assertFalse(self.receipt.exists())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual('building', api.report(db)['build_state'])

    def test_changed_archive_bytes_and_forged_receipt_are_rejected(self):
        self.complete()
        value = json.loads(self.receipt.read_text(encoding='utf-8'))
        value['dataset_id'] = 'other'
        self.receipt.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'gate mismatch'):
            self.complete()
        value['dataset_id'] = 'full-fixture-v2'
        self.receipt.write_text(json.dumps(value), encoding='utf-8')
        self.path.chmod(stat.S_IWRITE)
        # Change an ordinary payload without corrupting the manifest/schema.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE central_news_body_revisions SET body_text='X'||substr(body_text,2)")
        self.assertEqual(value['bytes'], self.path.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            verified_publication(self.path, self.receipt, 'full-fixture-v2', self.digest)

    def test_missing_receipt_after_seal_recovers_through_full_gate(self):
        self.complete(); self.receipt.unlink()
        with patch('scripts.seal_prepared_historical_archive_v2.audit', wraps=audit) as called:
            result = self.complete()
        self.assertEqual(1, called.call_count)
        self.assertTrue(result['ready_for_seal'])
        self.assertTrue(self.receipt.exists())


if __name__ == '__main__':
    unittest.main()
