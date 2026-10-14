from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch

from tests.unit import test_prepared_historical_archive_v2 as fixtures
from tests.unit import test_prepared_historical_archive_inputs as input_fixtures
from scripts import prepared_historical_archive_v2 as api
from scripts.prepared_historical_archive_inputs import prepared_batch
from scripts import prepared_historical_archive_inputs as inputs
from scripts.stage_prepared_historical_news_archive import _sha256


class BatchReadTests(unittest.TestCase):
    setUp = fixtures.ArchiveV2Tests.setUp
    add = fixtures.ArchiveV2Tests.add
    prepare_both = fixtures.ArchiveV2Tests.prepare_both

    def test_251_inputs_read_in_two_statements_and_missing_keys_not_invented(self):
        for n in range(251): self.add(api.SEARCH, f'url-{n:03d}')
        digest = _sha256(self.prepared)
        api.stage(self.archive, self.prepared, api.SEARCH, digest)
        with closing(sqlite3.connect(self.archive)) as db, closing(sqlite3.connect(self.prepared)) as source:
            pending = db.execute('SELECT stock_code,identity,input_hash FROM archive_input_progress ORDER BY stock_code,identity').fetchall()
            queries=[]; source.set_trace_callback(queries.append)
            rows=prepared_batch(db,source,{},api.SEARCH,pending)
            self.assertEqual(251,len(rows))
            self.assertEqual(2,len([q for q in queries if q.startswith('SELECT stock_code,identity,state')]))
            missing=prepared_batch(db,source,{},api.SEARCH,[('005930','absent','hash')])
            self.assertEqual({},missing)

    def test_cached_row_still_hash_verified_and_entire_batch_rolled_back(self):
        self.add(api.SEARCH,'a');self.add(api.SEARCH,'b')
        digest=_sha256(self.prepared);api.stage(self.archive,self.prepared,api.SEARCH,digest)
        original=prepared_batch
        def changed(*args):
            rows=original(*args);key=('005930','b');row=list(rows[key]);row[3]=row[3]+' '
            rows[key]=tuple(row);return rows
        with patch.object(api,'prepared_batch',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'changed or disappeared'):
                api.finalize_inputs(self.archive,self.prepared,api.SEARCH,digest,batch_size=250)
        with closing(sqlite3.connect(self.archive)) as db:
            for table in ('central_news_article_revisions','central_news_body_revisions','archive_expected_targets_v2','archive_targets_v2'):
                self.assertEqual(0,db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0])
        self.assertEqual(2,api.finalize_inputs(self.archive,self.prepared,api.SEARCH,digest,batch_size=250)['inputs_finalized_now'])

    def test_projection_reuses_clean_bodies_and_cleans_only_missing(self):
        self.prepare_both();api.finalize_events(self.archive)
        with closing(sqlite3.connect(self.archive)) as db,db:
            self.assertEqual(2,db.execute('SELECT COUNT(*) FROM archive_clean_bodies_v2').fetchone()[0])
        with patch.object(api,'clean_article_text',side_effect=AssertionError('already cleaned')):
            self.assertEqual(2,api.build_projection(self.archive,batch_size=1000)['projected_now'])

    def test_projection_missing_clean_body_is_repaired_once(self):
        for scope in api.SCOPES: self.add(scope)
        with closing(sqlite3.connect(self.prepared)) as db,db:
            for scope,owner,identity,encoded in db.execute('SELECT scope,stock_code,identity,body_json FROM prepared_news').fetchall():
                body=json.loads(encoded);body.update(body_status='fulltext',body_text='This full article contains an independently verified business development and its details.')
                db.execute('UPDATE prepared_news SET body_json=? WHERE scope=? AND stock_code=? AND identity=?',(json.dumps(body),scope,owner,identity))
        digest=_sha256(self.prepared)
        for scope in api.SCOPES:
            api.stage(self.archive,self.prepared,scope,digest)
            api.finalize_inputs(self.archive,self.prepared,scope,digest)
        api.finalize_events(self.archive)
        with closing(sqlite3.connect(self.archive)) as db,db:
            db.execute('DELETE FROM archive_clean_bodies_v2 WHERE rowid=(SELECT MIN(rowid) FROM archive_clean_bodies_v2)')
        with patch.object(api,'clean_article_text',wraps=api.clean_article_text) as called:
            api.build_projection(self.archive,batch_size=1000)
        self.assertEqual(1,called.call_count)

    def test_existing_clean_row_does_not_hide_body_relation_corruption(self):
        self.prepare_both();api.finalize_events(self.archive)
        with closing(sqlite3.connect(self.archive)) as db,db:
            db.execute("UPDATE central_news_body_revisions SET article_revision_id='wrong'")
        with self.assertRaisesRegex(ValueError,'body relation missing'):
            api.build_projection(self.archive,batch_size=1000)
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM archive_projection_v2').fetchone()[0])

    def test_joined_exclusion_check_still_rejects_missing_or_changed_evidence(self):
        f=input_fixtures.InputGenerationTests('test_exclusions_known_only_unknown_missing_blocks')
        f.setUp();self.addCleanup(f.doCleanups);f.base()
        with closing(sqlite3.connect(f.archive)) as db,db:
            for n in range(20):
                metadata={'training_eligible':0,'article_fetch_status':'blocked','training_exclusion_reason':'blocked'}
                db.execute('INSERT INTO archive_source_coverage_v2 VALUES(?,?,?,?,?)',(api.SEARCH,'005930',f'ex-{n}',None,json.dumps(metadata)))
                db.execute('INSERT INTO archive_source_history_v2 VALUES(?,?,?,?,?,?)',(api.SEARCH,'news_articles',f'ex-{n}','verified',api._json(metadata),inputs._hash(metadata)))
        f.adopt();inputs.stage_exclusions(f.archive);inputs.complete_adoption(f.archive)
        with closing(sqlite3.connect(f.archive)) as db:
            queries=[];db.set_trace_callback(queries.append)
            self.assertEqual([],inputs.contract_blockers(db))
            self.assertEqual(1,len([q for q in queries if q.startswith('SELECT e.*,c.metadata_json')]))
            for sql in ("UPDATE archive_source_exclusions_v3 SET raw_hash='bad' WHERE identity='ex-0'",
                        "DELETE FROM archive_source_coverage_v2 WHERE identity='ex-0'",
                        "DELETE FROM archive_source_history_v2 WHERE source_row_key='ex-0'"):
                db.execute('SAVEPOINT corruption');db.execute(sql)
                self.assertIn('source_exclusion_evidence_invalid',inputs.contract_blockers(db))
                db.execute('ROLLBACK TO corruption');db.execute('RELEASE corruption')


if __name__=='__main__':unittest.main()
