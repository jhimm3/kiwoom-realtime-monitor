from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from tests.unit import test_prepared_historical_archive_v2 as fixtures
from scripts import prepared_historical_archive_v2 as api
from scripts import prepared_historical_archive_inputs as inputs
from scripts.capture_historical_archive_sources_v2 import _tables, finish_coverage
from scripts.stage_prepared_historical_news_archive import _sha256


class InputGenerationTests(unittest.TestCase):
    setUp = fixtures.ArchiveV2Tests.setUp
    add = fixtures.ArchiveV2Tests.add
    raw_fixture = fixtures.ArchiveV2Tests.raw_fixture
    empty_seed = fixtures.ArchiveV2Tests.empty_seed

    def base(self, *, failed=False):
        self.add(api.SEARCH, 'base')
        self.add(api.MARKET, 'market')
        if failed:
            with closing(sqlite3.connect(self.prepared)) as db, db:
                db.execute("UPDATE prepared_news SET state='failed',error='OperationalError: database is locked' WHERE identity='base'")
        self.base_hash = _sha256(self.prepared)
        for scope in api.SCOPES:
            api.stage(self.archive, self.prepared, scope, self.base_hash)
        with closing(sqlite3.connect(self.archive)) as db, db:
            _tables(db)
            for scope, owner, identity, hashed in db.execute('SELECT scope,stock_code,identity,input_hash FROM archive_input_progress').fetchall():
                db.execute('INSERT INTO archive_source_coverage_v2 VALUES(?,?,?,?,?)', (scope, owner, identity, hashed, '{}'))
            for scope in api.SCOPES:
                db.execute('INSERT INTO archive_build_manifest VALUES(?,?)', ('source_capture_v2_' + scope, json.dumps({'state': 'complete', 'raw_fingerprint': {}})))
        self.parent_digest = api.code_digest()

    def component(self, identity, *, failed=False):
        filename = self.root / (identity + '.sqlite3')
        self.add(api.SEARCH, identity)
        with closing(sqlite3.connect(self.prepared)) as source, closing(sqlite3.connect(filename)) as output, source, output:
            output.execute('CREATE TABLE prepared_news(scope TEXT,stock_code TEXT,identity TEXT,state TEXT,article_json TEXT,body_json TEXT,rules_json TEXT,error TEXT,updated_at REAL,PRIMARY KEY(scope,stock_code,identity))')
            row = source.execute('SELECT * FROM prepared_news WHERE identity=?', (identity,)).fetchone()
            if failed:
                row = (*row[:3], 'failed', *row[4:7], 'body unavailable', row[8])
            output.execute('INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)', row)
            source.execute('DELETE FROM prepared_news WHERE identity=?', (identity,))
        # add/delete can change file bytes: fixtures refreeze the actual base file.
        self.base_hash = _sha256(self.prepared)
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute('UPDATE archive_sources_v2 SET source_sha256=?', (self.base_hash,))
            metadata = dict(training_eligible=1, article_fetch_status='published_at_found', training_exclusion_reason='')
            db.execute('INSERT INTO archive_source_coverage_v2 VALUES(?,?,?,?,?)', (api.SEARCH, '005930', identity, None, json.dumps(metadata)))
        return filename, _sha256(filename)

    def adopt(self):
        return inputs.adopt(self.archive, parent_dataset='test-v2', parent_digest=self.parent_digest, dataset_id='test-components')

    def stage(self, filename, digest, kind='supplement', **kwargs):
        return inputs.stage_component(self.archive, filename, api.SEARCH, digest, component_id='added', kind=kind, **kwargs)

    def test_supplement_resume_sorted_finalize_and_original_manifest(self):
        self.base()
        filename, digest = self.component('aaa')
        self.adopt()
        self.assertEqual(1, self.stage(filename, digest, max_rows=1)['staged_now'])
        self.assertEqual(0, self.stage(filename, digest)['staged_now'])
        inputs.stage_exclusions(self.archive)
        inputs.complete_adoption(self.archive)
        with closing(sqlite3.connect(self.archive)) as db, db:
            self.assertEqual([], inputs.contract_blockers(db, check_files=True))
            self.assertEqual((2, 2), inputs.input_counts(db, api.SEARCH))
            self.assertEqual((1, 1), db.execute('SELECT expected_rows,staged_rows FROM archive_sources_v2 WHERE scope=?', (api.SEARCH,)).fetchone())
        self.assertEqual(2, api.finalize_inputs(self.archive, self.prepared, api.SEARCH, self.base_hash)['inputs_finalized_now'])
        with closing(sqlite3.connect(self.archive)) as db, db:
            ordered = [json.loads(r[0])['identity'] for r in db.execute('SELECT document_json FROM central_news_article_revisions ORDER BY accepted_sequence')]
            self.assertEqual(['aaa', 'base'], ordered)
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM central_news_jobs').fetchone()[0])
        with self.assertRaisesRegex(ValueError, 'finalized'):
            inputs.rollback_adoption(self.archive)

    def test_repair_preserves_failure_and_rollback_restores_parent(self):
        self.base(failed=True)
        with closing(sqlite3.connect(self.archive)) as db, db:
            original = db.execute('SELECT * FROM archive_input_progress ORDER BY scope,stock_code,identity').fetchall()
            failure = db.execute('SELECT * FROM archive_failures_v2').fetchall()
            manifests = db.execute('SELECT * FROM archive_build_manifest ORDER BY key').fetchall()
        repair = self.root / 'repair.sqlite3'
        with closing(sqlite3.connect(self.prepared)) as source, closing(sqlite3.connect(repair)) as output, output:
            output.execute('CREATE TABLE prepared_news AS SELECT * FROM (SELECT 1) WHERE 0')
            output.execute('DROP TABLE prepared_news')
            output.execute(source.execute("SELECT sql FROM sqlite_master WHERE name='prepared_news'").fetchone()[0])
            row = list(source.execute("SELECT * FROM prepared_news WHERE identity='base'").fetchone())
            row[3] = 'ready'; row[7] = ''
            output.execute('INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)', row)
        self.adopt()
        self.stage(repair, _sha256(repair), 'repair')
        with closing(sqlite3.connect(self.archive)) as db, db:
            self.assertEqual(failure, db.execute('SELECT * FROM archive_failures_v2').fetchall())
            self.assertEqual((1, 1), inputs.input_counts(db, api.SEARCH))
        inputs.rollback_adoption(self.archive)
        with closing(sqlite3.connect(self.archive)) as db, db:
            self.assertEqual(original, db.execute('SELECT * FROM archive_input_progress ORDER BY scope,stock_code,identity').fetchall())
            self.assertEqual(manifests, db.execute('SELECT * FROM archive_build_manifest ORDER BY key').fetchall())
            self.assertEqual(failure, db.execute('SELECT * FROM archive_failures_v2').fetchall())

    def test_exclusions_known_only_unknown_missing_blocks(self):
        self.base()
        with closing(sqlite3.connect(self.archive)) as db, db:
            for identity, status in [('excluded', 'blocked'), ('unknown', 'other')]:
                metadata = dict(training_eligible=0, article_fetch_status=status, training_exclusion_reason=status)
                db.execute('INSERT INTO archive_source_coverage_v2 VALUES(?,?,?,?,?)', (api.SEARCH, '005930', identity, None, json.dumps(metadata)))
                db.execute('INSERT INTO archive_source_history_v2 VALUES(?,?,?,?,?,?)', (api.SEARCH, 'news_articles', identity, 'verified', api._json(metadata), inputs._hash(metadata)))
        self.adopt()
        inputs.stage_exclusions(self.archive, max_rows=1, batch_size=1)
        inputs.stage_exclusions(self.archive)
        with closing(sqlite3.connect(self.archive)) as db, db:
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM archive_source_exclusions_v3').fetchone()[0])
            self.assertEqual(1, inputs.missing_coverage(db))
        with self.assertRaisesRegex(ValueError, 'unexplained_missing'):
            inputs.complete_adoption(self.archive)
        with self.assertRaises(ValueError):
            with api._writer(self.archive):
                pass

    def test_bad_parent_and_competing_writer_rejected(self):
        self.base()
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            inputs.adopt(self.archive, parent_dataset='wrong', parent_digest=self.parent_digest, dataset_id='next')
        with api._writer(self.archive), self.assertRaisesRegex(ValueError, 'another archive builder'):
            self.adopt()
        self.adopt()
        with self.assertRaisesRegex(ValueError, 'different adoption'):
            inputs.adopt(self.archive, parent_dataset='test-v2', parent_digest=self.parent_digest, dataset_id='another')

    def test_component_changed_or_bad_scope_rolls_back_batch(self):
        self.base()
        filename, digest = self.component('new')
        self.adopt()
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute("CREATE TRIGGER break_binding BEFORE INSERT ON archive_input_bindings_v3 BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'injected'):
            self.stage(filename, digest)
        with closing(sqlite3.connect(self.archive)) as db, db:
            self.assertEqual(0, db.execute('SELECT staged FROM archive_input_components_v3').fetchone()[0])
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM archive_input_bindings_v3').fetchone()[0])
            self.assertIsNone(db.execute("SELECT 1 FROM archive_input_progress WHERE identity='new'").fetchone())
            db.execute('DROP TRIGGER break_binding')
        self.stage(filename, digest)
        with closing(sqlite3.connect(filename)) as db, db:
            db.execute("UPDATE prepared_news SET error='changed'")
        with closing(sqlite3.connect(self.archive)) as db, db:
            self.assertIn('input_component_changed_or_unavailable', inputs.contract_blockers(db, check_files=True))

    def test_full_audit_detects_binding_and_exclusion_tamper(self):
        self.base()
        filename, digest = self.component('new')
        self.adopt(); self.stage(filename, digest)
        inputs.stage_exclusions(self.archive); inputs.complete_adoption(self.archive)
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute("UPDATE archive_input_bindings_v3 SET coverage_hash='bad'")
        from scripts.audit_prepared_historical_archive_v2 import audit
        self.assertIn('input_binding_invalid', audit(self.archive)['blockers'])

    def test_failed_supplement_repair_keeps_both_input_versions(self):
        self.base()
        filename, digest = self.component('new', failed=True)
        repaired = self.root / 'network-repair.sqlite3'
        with closing(sqlite3.connect(filename)) as source, closing(sqlite3.connect(repaired)) as target, target:
            target.execute(source.execute("SELECT sql FROM sqlite_master WHERE name='prepared_news'").fetchone()[0])
            row = list(source.execute('SELECT * FROM prepared_news').fetchone())
            row[3] = 'ready'; row[7] = ''
            target.execute('INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)', row)
        self.adopt(); self.stage(filename, digest)
        inputs.stage_component(self.archive, repaired, api.SEARCH, _sha256(repaired), component_id='network-retry', kind='repair')
        inputs.stage_exclusions(self.archive); inputs.complete_adoption(self.archive)
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual([], inputs.contract_blockers(db, check_files=True))
            self.assertEqual(2, db.execute('SELECT COUNT(*) FROM archive_component_members_v3').fetchone()[0])
            self.assertEqual(1, db.execute('SELECT COUNT(*) FROM archive_binding_history_v3').fetchone()[0])
            self.assertEqual((2, 2), inputs.input_counts(db, api.SEARCH))
        self.assertEqual(2, api.finalize_inputs(self.archive, self.prepared, api.SEARCH, self.base_hash)['inputs_finalized_now'])

    def test_parent_raw_evidence_change_blocks_completion_and_rollback(self):
        self.base(); self.adopt(); inputs.stage_exclusions(self.archive)
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute("UPDATE archive_source_coverage_v2 SET metadata_json='changed'")
        with self.assertRaisesRegex(ValueError, 'parent_evidence'):
            inputs.complete_adoption(self.archive)
        with self.assertRaisesRegex(ValueError, 'parent evidence changed'):
            inputs.rollback_adoption(self.archive)

    def test_failure_review_is_evidence_bound_and_rolled_back(self):
        self.base()
        filename, _ = self.component('missing', failed=True)
        with closing(sqlite3.connect(filename)) as db, db:
            db.execute("UPDATE prepared_news SET body_json='{}',rules_json='[]',error=?", ('ValueError: 기사 본문과 목록 요약을 가져오지 못했습니다.',))
        self.adopt(); self.stage(filename, _sha256(filename))
        self.assertEqual(1, inputs.review_local_failures(self.archive)['reviewed_now'])
        inputs.stage_exclusions(self.archive); inputs.complete_adoption(self.archive)
        with closing(sqlite3.connect(self.archive)) as db, db:
            self.assertEqual([], inputs.contract_blockers(db))
            db.execute("UPDATE archive_failure_review_v2 SET reason='manual approval'")
            self.assertIn('source_failure_evidence_invalid', inputs.contract_blockers(db))
        inputs.rollback_adoption(self.archive)
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM archive_failure_review_v2').fetchone()[0])
            self.assertEqual(2, db.execute('SELECT COUNT(*) FROM archive_input_progress').fetchone()[0])

    def test_failure_with_available_body_cannot_be_waived(self):
        self.base(failed=True); self.adopt()
        with self.assertRaisesRegex(ValueError, 'available prepared BODY'):
            inputs.review_local_failures(self.archive)
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM archive_failure_review_v2').fetchone()[0])

    def test_adopted_generation_full_gate_seal_and_existing_reader(self):
        from scripts.capture_historical_archive_sources_v2 import capture
        from scripts.audit_prepared_historical_archive_v2 import audit
        from scripts.seal_prepared_historical_archive_v2 import seal
        from kiwoom_monitor.central_server.historical_news_archive import HistoricalNewsArchiveReader
        import stat
        seed = self.empty_seed()
        self.add(api.SEARCH); self.add(api.MARKET)
        digest = _sha256(self.prepared)
        for scope in api.SCOPES:
            api.stage(self.archive, self.prepared, scope, digest)
            capture(self.archive, self.raw_fixture(scope), scope)
        inputs.adopt(self.archive, parent_dataset='full-fixture-v2', parent_digest=api.code_digest(), dataset_id='test-components')
        inputs.stage_exclusions(self.archive); inputs.complete_adoption(self.archive)
        self.assertEqual('complete', finish_coverage(self.archive)['state'])
        for scope in api.SCOPES:
            api.finalize_inputs(self.archive, self.prepared, scope, digest)
        api.finalize_events(self.archive); api.build_projection(self.archive)
        self.assertEqual([], audit(self.archive, seed=seed, full_integrity_check=True)['blockers'])
        published = seal(self.archive, seed, self.root/'publication.json')
        self.addCleanup(lambda: self.archive.chmod(stat.S_IWRITE) if self.archive.exists() else None)
        self.assertEqual(_sha256(self.archive), published['sha256'])
        reader = HistoricalNewsArchiveReader(self.archive, cursor_key=b'test')
        self.assertEqual(1, len(reader.page_v2('market')['items']))

    def test_pc_master_pause_prevents_batch_writes_and_allows_resume(self):
        self.base(); filename, digest = self.component('new'); self.adopt()
        control = Path(str(self.archive) + '.input-controls.json')
        value = {'schema': 'historical-archive-pc-controls/v1', 'archive': str(self.archive), 'master': False, 'active_phase': 'component', 'phases': {'component': True}, 'expires_at': None}
        control.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(inputs.ArchiveBuildPaused):
            self.stage(filename, digest)
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM archive_input_components_v3').fetchone()[0])
        value['master'] = True; control.write_text(json.dumps(value), encoding='utf-8')
        self.assertEqual(1, self.stage(filename, digest)['staged_now'])

    def test_exclusion_range_reuses_rowid_index_without_repeated_sort(self):
        self.base()
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute('CREATE INDEX archive_source_coverage_missing_v2 ON archive_source_coverage_v2(scope) WHERE input_hash IS NULL')
        self.adopt()
        traced = []
        original = inputs._adoption_writer
        from contextlib import contextmanager
        @contextmanager
        def trace_writer(path):
            with original(path) as db:
                db.set_trace_callback(traced.append)
                yield db
        with patch.object(inputs, '_adoption_writer', trace_writer):
            inputs.stage_exclusions(self.archive, max_rows=1)
        sql = next(q for q in traced if q.startswith('SELECT c.rowid'))
        with closing(sqlite3.connect(self.archive)) as db:
            plan = ' '.join(r[3] for r in db.execute('EXPLAIN QUERY PLAN '+sql))
            self.assertIn('archive_source_coverage_missing_v2', plan)
            self.assertIn('rowid>?', plan)
            self.assertNotIn('TEMP B-TREE', plan)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='archive_source_coverage_missing_v3'").fetchone())
        inputs.rollback_adoption(self.archive)
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='archive_source_coverage_missing_v2'").fetchone())


if __name__ == '__main__':
    unittest.main()
