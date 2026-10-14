from contextlib import closing
import hashlib
import json
import sqlite3
import stat
import unittest
from unittest.mock import patch

from tests.unit import test_prepared_historical_archive_v2 as fixtures
from scripts import prepared_historical_archive_v2 as api
from scripts import prepared_historical_archive_inputs as inputs
from scripts.capture_historical_archive_sources_v2 import capture, finish_coverage
from scripts.transition_historical_archive_execution import (
    PREFIX_KEY, _prefix_snapshot, _apply_finalized, finalization_lineage,
    fingerprint_digest, _encoded,
)
from scripts.audit_prepared_historical_archive_v2 import audit
from scripts.historical_archive_completion_gate import audit_and_seal, verified_publication
from scripts.stage_prepared_historical_news_archive import _sha256


class PrefixTransitionTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.ArchiveV2Tests('test_real_source_coverage_seals_readonly_and_recovers_receipt')
        f.setUp();self.addCleanup(f.doCleanups);self.f=f
        self.seed=f.empty_seed();self.old='a'*64;self.new=api.code_digest()
        with closing(sqlite3.connect(f.archive)) as db,db:
            value=api.report(db);value['builder_code_digest']=self.old
            db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",(api._json(value),))
        with patch.object(api,'code_digest',return_value=self.old):
            for scope in api.SCOPES:f.add(scope)
            self.source_hash=_sha256(f.prepared)
            for scope in api.SCOPES:
                api.stage(f.archive,f.prepared,scope,self.source_hash)
                capture(f.archive,f.raw_fixture(scope),scope)
            inputs.adopt(f.archive,parent_dataset='full-fixture-v2',parent_digest=self.old,dataset_id='parent')
            inputs.stage_exclusions(f.archive);inputs.complete_adoption(f.archive);finish_coverage(f.archive)
            api.finalize_inputs(f.archive,f.prepared,api.SEARCH,self.source_hash)
        self.plan=f.root/'transition.json'

    def transition(self):
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            scopes=_prefix_snapshot(db)
            content=_encoded({'schema':'historical-finalization-execution/v1','parent_dataset':'parent','parent_digest':self.old,'dataset_id':'child','code_digest':self.new,'scopes':scopes})
            self.plan.write_text(content,encoding='utf-8')
            db.execute('BEGIN IMMEDIATE')
            return _apply_finalized(db,'parent',self.old,'child',self.new,self.plan,hashlib.sha256(content.encode()).hexdigest(),scopes)

    def complete(self):
        api.finalize_inputs(self.f.archive,self.f.prepared,api.MARKET,self.source_hash)
        api.finalize_events(self.f.archive);api.build_projection(self.f.archive)

    def test_mixed_digests_preserve_parent_ids_and_real_full_gate(self):
        with closing(sqlite3.connect(self.f.archive)) as db:
            before=db.execute('SELECT * FROM archive_targets_v2 ORDER BY scope').fetchall()
            core=db.execute('SELECT * FROM central_news_article_revisions').fetchall()
        lineage=self.transition()
        self.assertIsNone(lineage['scopes'][api.MARKET]['last'])
        self.complete()
        with closing(sqlite3.connect(self.f.archive)) as db:
            self.assertEqual(core,db.execute("SELECT * FROM central_news_article_revisions WHERE collection_scope='historical_news_pc_backfill'").fetchall())
            self.assertEqual(before[0][:13],db.execute('SELECT * FROM archive_targets_v2 WHERE scope=?',(api.SEARCH,)).fetchone()[:13])
            self.assertEqual(lineage,finalization_lineage(db,check_file=True))
        result=audit(self.f.archive,seed=self.seed,full_integrity_check=True)
        self.assertTrue(result['ready_for_seal'],result['blockers'])
        receipt=self.f.root/'seal.json'
        audit_and_seal(self.f.archive,self.seed,receipt,'child',self.new)
        self.addCleanup(lambda:self.f.archive.chmod(stat.S_IWRITE))
        self.assertTrue(verified_publication(self.f.archive,receipt,'child',self.new)[1]['ready_for_seal'])

    def test_prefix_tamper_and_suffix_parent_fingerprint_are_blocked(self):
        self.transition();self.complete()
        with closing(sqlite3.connect(self.f.archive)) as db:
            saved={t:db.execute('SELECT * FROM '+t).fetchall() for t in ('archive_targets_v2','archive_input_progress')}
            sqls=("UPDATE archive_targets_v2 SET target_name='changed' WHERE scope='historical_backfill'",
                  "DELETE FROM archive_targets_v2 WHERE scope='historical_backfill'",
                  "UPDATE archive_input_progress SET article_revision_id='changed' WHERE scope='historical_backfill'")
            for sql in sqls:
                db.execute(sql);db.commit()
                self.assertFalse(audit(self.f.archive,seed=self.seed)['ready_for_seal'])
                for table,rows in saved.items():
                    db.execute('DELETE FROM '+table)
                    db.executemany('INSERT INTO '+table+' VALUES('+','.join('?' for _ in rows[0])+')',rows)
                db.commit()
            from scripts.transition_historical_archive_execution import TARGET_FIELDS
            row=db.execute('SELECT '+TARGET_FIELDS+' FROM archive_targets_v2 WHERE scope=? LIMIT 1',(api.MARKET,)).fetchone()
            scope,owner,identity,target,name,hashed,aid,bid,_,_,computed,*_=row
            fake=hashlib.sha256(api._json((api.POLICY,hashed,aid,bid,target,name,self.old,json.loads(computed))).encode()).hexdigest()
            db.execute('UPDATE archive_targets_v2 SET fingerprint=? WHERE scope=? AND stock_code=? AND identity=? AND target_id=?',(fake,scope,owner,identity,target));db.commit()
            self.assertIn('target_verification_fingerprint_invalid',audit(self.f.archive,seed=self.seed)['blockers'])
        self.plan.write_text('changed',encoding='utf-8')
        self.assertIn('finalization_lineage_invalid',audit(self.f.archive,seed=self.seed)['blockers'])

    def test_middle_transition_failure_preserves_all_completed_rows(self):
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            before={t:db.execute('SELECT * FROM '+t).fetchall() for t in ('archive_build_manifest','archive_input_progress','archive_targets_v2','central_news_article_revisions','central_news_body_revisions')}
            db.execute("CREATE TRIGGER injected BEFORE UPDATE ON archive_build_manifest WHEN NEW.key='report' BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError,'injected'):self.transition()
        with closing(sqlite3.connect(self.f.archive)) as db:
            self.assertEqual(before,{t:db.execute('SELECT * FROM '+t).fetchall() for t in before})

    def test_nonprefix_partial_events_and_bounded_key_order(self):
        self.assertEqual(self.old,fingerprint_digest({'parent_digest':self.old,'scopes':{api.SEARCH:{'last':['005930','한글']}}},api.SEARCH,'005930','URL',self.new))
        with closing(sqlite3.connect(self.f.archive)) as db:
            db.execute("UPDATE archive_input_progress SET progress_state='article_body_done' WHERE scope=?",(api.MARKET,));db.commit()
            with self.assertRaisesRegex(ValueError,'partial'):_prefix_snapshot(db)


if __name__=='__main__':unittest.main()
