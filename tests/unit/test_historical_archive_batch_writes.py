from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch

from tests.unit import test_prepared_historical_archive_v2 as fixtures
from scripts import prepared_historical_archive_v2 as api
from scripts.finalize_prepared_historical_article_bodies import _insert_many
from scripts.preprocess_historical_news_to_nas import prepare_job, _prepare_rule_job
from scripts.stage_prepared_historical_news_archive import _sha256


class BatchWriteTests(unittest.TestCase):
    setUp = fixtures.ArchiveV2Tests.setUp
    add = fixtures.ArchiveV2Tests.add

    def test_real_multirow_boundaries_and_replay(self):
        for n in range(1001):self.add(api.SEARCH,f'url-{n:04d}')
        digest=_sha256(self.prepared);api.stage(self.archive,self.prepared,api.SEARCH,digest)
        connect=sqlite3.connect
        for size in (1,79,80,81,249,250,261):
            statements=[]
            def traced(*args,**kwargs):
                db=connect(*args,**kwargs);db.set_trace_callback(statements.append);return db
            with patch.object(api.sqlite3,'connect',side_effect=traced):
                result=api.finalize_inputs(self.archive,self.prepared,api.SEARCH,digest,max_rows=size,batch_size=250)
            self.assertEqual(size,result['inputs_finalized_now'])
            inserts=[s for s in statements if s.startswith('INSERT INTO central_news_article_revisions(')]
            expected=sum((n+80)//81 for n in ([size] if size<=250 else [250,11]))
            self.assertEqual(expected,len(inserts),size)
            self.assertLessEqual(len(inserts),5)
        with closing(connect(self.archive)) as db:
            docs=[json.loads(r[0])['identity'] for r in db.execute('SELECT document_json FROM central_news_article_revisions ORDER BY accepted_sequence')]
            self.assertEqual([f'url-{n:04d}' for n in range(1001)],docs)
        self.assertEqual(0,api.finalize_inputs(self.archive,self.prepared,api.SEARCH,digest)['inputs_finalized_now'])

    def test_each_storage_failure_rolls_back_whole_batch(self):
        for n in range(3):self.add(api.SEARCH,str(n))
        digest=_sha256(self.prepared);api.stage(self.archive,self.prepared,api.SEARCH,digest)
        for table in ('central_news_article_revisions','central_news_body_revisions','archive_body_provenance','archive_targets_v2','archive_input_context_v2','archive_clean_bodies_v2','archive_expected_targets_v2','archive_input_progress'):
            verb='UPDATE' if table=='archive_input_progress' else 'INSERT'
            with closing(sqlite3.connect(self.archive)) as db,db:
                db.execute(f"CREATE TRIGGER injected BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'injected'); END")
            with self.assertRaisesRegex(sqlite3.IntegrityError,'injected'):
                api.finalize_inputs(self.archive,self.prepared,api.SEARCH,digest,batch_size=250)
            with closing(sqlite3.connect(self.archive)) as db,db:
                db.execute('DROP TRIGGER injected')
                for target in ('central_news_article_revisions','central_news_body_revisions','archive_body_provenance','archive_targets_v2','archive_input_context_v2','archive_clean_bodies_v2','archive_expected_targets_v2'):
                    self.assertEqual(0,db.execute('SELECT COUNT(*) FROM '+target).fetchone()[0],table)
                self.assertEqual([('pending',3)],db.execute('SELECT progress_state,COUNT(*) FROM archive_input_progress GROUP BY progress_state').fetchall())
        self.assertEqual(3,api.finalize_inputs(self.archive,self.prepared,api.SEARCH,digest)['inputs_finalized_now'])

    def test_runtime_bind_limit_and_first_clean_value(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE clean(id TEXT PRIMARY KEY,text TEXT)')
            db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,12)
            statements=[];db.set_trace_callback(statements.append)
            _insert_many(db,'INSERT INTO clean',[(str(n),'body') for n in range(31)])
            self.assertEqual(6,sum(s.startswith('INSERT INTO clean') for s in statements))
            _insert_many(db,'INSERT INTO clean',[('0','changed')],' ON CONFLICT(id) DO NOTHING')
            self.assertEqual('body',db.execute("SELECT text FROM clean WHERE id='0'").fetchone()[0])

    def test_rule_clean_reuse_all_statuses_and_one_clean_per_input(self):
        for scope in api.SCOPES:self.add(scope)
        digest=_sha256(self.prepared)
        for scope in api.SCOPES:
            api.stage(self.archive,self.prepared,scope,digest)
            with patch.object(api,'clean_article_text',wraps=api.clean_article_text) as clean:
                api.finalize_inputs(self.archive,self.prepared,scope,digest)
            self.assertEqual(1,clean.call_count)
        article={'identity':'x','stock_code':'005930','document':{'title':'삼성전자 계약','description':'대체 요약'}}
        job={'stage':'RULE','job_key':'test','attempts':1,'payload':{'stock_code':'005930','stock_name':'삼성전자'}}
        for text in ('','짧은 본문','  광고\n삼성전자 공급 계약 체결\n  '):
            for status in ('fulltext','summary_only'):
                body={'body_text':text,'status':status}
                self.assertEqual(prepare_job(job,article,body,allow_network=False),_prepare_rule_job(job,article,body,api.clean_article_text(text)))


if __name__=='__main__':unittest.main()
