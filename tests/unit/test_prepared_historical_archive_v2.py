from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import hashlib
import stat
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server.database import SQLiteQueryStore, stable_document_hash
from kiwoom_monitor.central_server.database_news_sources import _news_article_content_hash
from scripts.preprocess_historical_news_to_nas import prepare_job
from scripts.prepared_historical_archive_v2 import (
    MARKET, SEARCH, _writer, build_projection, finalize_events, finalize_inputs, initialize,
    stage, validated_targets,
)
from scripts.stage_prepared_historical_news_archive import _sha256
from kiwoom_monitor.central_server.historical_news_archive import HistoricalNewsArchiveReader, InvalidArchiveCursorError


class ArchiveV2Tests(unittest.TestCase):
    def test_direct_original_alias_preserves_bytes_origins_and_resume(self):
        self.add(MARKET, 'canonical')
        self.add(MARKET, 'legacy')
        self.add(MARKET, 'failed')
        with closing(sqlite3.connect(self.prepared)) as db, db:
            db.execute("UPDATE prepared_news SET scope='historical_market' WHERE identity!='canonical'")
            db.execute("UPDATE prepared_news SET state='failed',error='timeout' WHERE identity='failed'")
        digest = _sha256(self.prepared)
        aliases = ('historical_market',)
        stage(self.archive,self.prepared,MARKET,digest,max_rows=1,scope_aliases=aliases)
        stage(self.archive,self.prepared,MARKET,digest,scope_aliases=aliases)
        with self.assertRaisesRegex(ValueError, 'alias configuration changed'):
            finalize_inputs(self.archive,self.prepared,MARKET,digest)
        finalize_inputs(self.archive,self.prepared,MARKET,digest,scope_aliases=aliases)
        self.assertEqual(0,finalize_inputs(self.archive,self.prepared,MARKET,digest,scope_aliases=aliases)['inputs_finalized_now'])
        self.assertEqual(digest,_sha256(self.prepared))
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(2,db.execute('SELECT COUNT(*) FROM archive_prepared_scope_origins_v2').fetchone()[0])
            contexts=dict((key,json.loads(value)['original_prepared_scope']) for key,value in db.execute('SELECT identity,context_json FROM archive_input_context_v2'))
            self.assertEqual({'canonical':MARKET,'legacy':'historical_market'},contexts)
        with closing(sqlite3.connect(self.prepared)) as db:
            self.assertEqual({'historical_market':2,MARKET:1},dict(db.execute('SELECT scope,COUNT(*) FROM prepared_news GROUP BY scope')))
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='prepared_scope_origins'").fetchone())

    def test_direct_alias_overlap_fails_before_staging(self):
        self.add(MARKET,'same')
        with closing(sqlite3.connect(self.prepared)) as db, db:
            db.execute("INSERT INTO prepared_news SELECT 'historical_market',stock_code,identity,state,article_json,body_json,rules_json,error,updated_at FROM prepared_news")
        digest=_sha256(self.prepared)
        with self.assertRaisesRegex(ValueError,'overlaps canonical'):
            stage(self.archive,self.prepared,MARKET,digest,scope_aliases=('historical_market',))
        self.assertEqual(digest,_sha256(self.prepared))
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM archive_input_progress').fetchone()[0])

    def test_normalized_market_origin_survives_all_states_and_resume(self):
        from scripts.snapshot_prepared_historical_news import snapshot
        self.add(MARKET,'ready'); self.add(MARKET,'failed'); self.add(MARKET,'excluded')
        with closing(sqlite3.connect(self.prepared)) as db, db:
            db.execute("UPDATE prepared_news SET state='failed',error='timeout' WHERE identity='failed'")
            encoded=db.execute("SELECT article_json FROM prepared_news WHERE identity='excluded'").fetchone()[0]
            article=json.loads(encoded); article['source']='world'; article['document']['published_at']='2020-01-01T00:00:00+09:00'
            db.execute("UPDATE prepared_news SET article_json=? WHERE identity='excluded'",(json.dumps(article),))
            db.execute("UPDATE prepared_news SET scope='historical_market'")
        frozen=self.root/'normalized.sqlite3'
        digest=snapshot(self.prepared,frozen,MARKET,scope_aliases=('historical_market',))['sha256']
        stage(self.archive,frozen,MARKET,digest,max_rows=1)
        stage(self.archive,frozen,MARKET,digest)
        self.assertEqual(0,stage(self.archive,frozen,MARKET,digest)['staged_now'])
        finalize_inputs(self.archive,frozen,MARKET,digest)
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(3,db.execute('SELECT COUNT(*) FROM archive_prepared_scope_origins_v2').fetchone()[0])
            context=json.loads(db.execute('SELECT context_json FROM archive_input_context_v2').fetchone()[0])
            self.assertEqual('historical_market',context['original_prepared_scope'])
            self.assertEqual([('assessment_done',1),('source_excluded',1),('source_failed',1)],db.execute('SELECT progress_state,COUNT(*) FROM archive_input_progress GROUP BY progress_state ORDER BY progress_state').fetchall())
            db.execute("DELETE FROM archive_prepared_scope_origins_v2 WHERE identity='failed'"); db.commit()
        from scripts.audit_prepared_historical_archive_v2 import audit
        self.assertIn(MARKET+'_prepared_origin_count_mismatch',audit(self.archive)['blockers'])

    def test_corrupted_origin_rolls_back_staging_batch(self):
        from scripts.snapshot_prepared_historical_news import snapshot
        self.add(MARKET,'a'); self.add(MARKET,'b')
        with closing(sqlite3.connect(self.prepared)) as db, db:
            db.execute("UPDATE prepared_news SET scope='historical_market'")
        frozen=self.root/'normalized.sqlite3'
        snapshot(self.prepared,frozen,MARKET,scope_aliases=('historical_market',))
        with closing(sqlite3.connect(frozen)) as db, db:
            db.execute("UPDATE prepared_scope_origins SET input_hash='corrupt' WHERE identity='b'")
        with self.assertRaisesRegex(ValueError,'origin differs'):
            stage(self.archive,frozen,MARKET,_sha256(frozen),batch_size=2)
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM archive_input_progress').fetchone()[0])
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM archive_prepared_scope_origins_v2').fetchone()[0])
            self.assertEqual(0,db.execute('SELECT staged_rows FROM archive_sources_v2').fetchone()[0])

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        self.archive=self.root/'archive.sqlite3'
        self.prepared=self.root/'prepared.sqlite3'
        SQLiteQueryStore(self.archive).initialize()
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute('CREATE TABLE archive_build_manifest(key TEXT PRIMARY KEY,value_json TEXT)')
            db.execute('INSERT INTO archive_build_manifest VALUES(?,?)',('report',json.dumps({'build_state':'building'})))
            db.execute('CREATE TABLE archive_seed_roles(table_name TEXT,row_key TEXT,payload_hash TEXT,role TEXT,PRIMARY KEY(table_name,row_key))')
        with closing(sqlite3.connect(self.prepared)) as db, db:
            db.execute('CREATE TABLE prepared_news(scope TEXT,stock_code TEXT,identity TEXT,state TEXT,article_json TEXT,body_json TEXT,rules_json TEXT,error TEXT,updated_at REAL,PRIMARY KEY(scope,stock_code,identity))')
        initialize(self.archive,dataset_id='test-v2')

    def add(self,scope,identity='https://example.test/a',*,targets=None,stale=False,title='일반 시장 소식'):
        owner='GLOBAL' if scope==MARKET else '005930'
        targets=targets or ([['GLOBAL','시황'],['005930','삼성전자'],['000660','SK하이닉스']] if scope==MARKET else [['005930','삼성전자']])
        doc={'title':title,'description':'기사 요약','published_at':'2026-09-01T00:00:00+00:00'}
        if scope==SEARCH:
            doc.update(stock_code=owner,identity=identity,stock_name='삼성전자')
        article={'stock_code':owner,'identity':identity,'document':doc,'targets':targets}
        if scope==MARKET:
            article.update(source='flash',source_item={'identity':identity,'targets':[{'stock_code':'005930','relation_status':'confirmed'}]})
        body={'body_text':'기사 요약','body_status':'summary_only','fetched_at':1790000000.0,'source_url':identity,'original_published_at':'2026-09-01T09:00:00+09:00'}
        rules=[]
        for code,name in targets:
            result=prepare_job({'job_key':'test','attempts':1,'stage':'RULE','target_id':code,'payload':{'stock_code':code,'stock_name':name}},article,{'body_text':body['body_text'],'status':'summary_only'},allow_network=False)
            rules.append({'target_id':code,'stock_name':name,**{k:result[k] for k in ('assessment','core_sentences','rule_result')}})
        if stale:
            rules[0]['assessment']['reason']='old prepared reason'
        with closing(sqlite3.connect(self.prepared)) as db, db:
            db.execute('INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)',(scope,owner,identity,'ready',json.dumps(article),json.dumps(body),json.dumps(rules),'',1.0))
        return article,body,rules

    def prepare_both(self):
        self.add(SEARCH)
        self.add(MARKET)
        digest=_sha256(self.prepared)
        for scope in (SEARCH,MARKET):
            stage(self.archive,self.prepared,scope,digest)
            finalize_inputs(self.archive,self.prepared,scope,digest)
        return digest

    def test_two_scopes_multiple_targets_one_body_and_replay_no_jobs(self):
        self.prepare_both()
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(2,db.execute('SELECT COUNT(*) FROM central_news_article_revisions').fetchone()[0])
            self.assertEqual(2,db.execute('SELECT COUNT(*) FROM central_news_body_revisions').fetchone()[0])
            self.assertEqual(4,db.execute('SELECT COUNT(*) FROM archive_targets_v2').fetchone()[0])
            self.assertEqual(0,db.execute('SELECT COUNT(*) FROM central_news_jobs').fetchone()[0])
            before=db.execute('SELECT article_revision_id,body_revision_id FROM archive_input_progress ORDER BY scope').fetchall()
        result=finalize_events(self.archive,max_rows=1)
        self.assertEqual(3,result['remaining_targets'])
        self.assertEqual(0,finalize_events(self.archive)['remaining_targets'])
        self.assertEqual(0,finalize_events(self.archive)['targets_resolved_now'])
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(before,db.execute('SELECT article_revision_id,body_revision_id FROM archive_input_progress ORDER BY scope').fetchall())
            self.assertEqual([('event_done',2)],db.execute('SELECT progress_state,COUNT(*) FROM archive_input_progress GROUP BY progress_state').fetchall())
            self.assertEqual(4,db.execute('SELECT COUNT(DISTINCT ordinal) FROM archive_targets_v2').fetchone()[0])

    def test_market_exact_seed_ids_and_assessment_payload_preserved(self):
        article,body,_=self.add(MARKET,stale=True)
        document={**article['document'],'stock_code':'GLOBAL','identity':article['identity']}
        seed_assessment=json.dumps({'old':'unchanged'})
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute('INSERT INTO central_news_article_revisions(article_revision_id,stock_code,identity,content_hash,collector_id,published_at,received_at,available_at,collection_scope,revision_of,document_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('seed-a','GLOBAL',article['identity'],_news_article_content_hash(document),'seed',document['published_at'],1.,2.,'historical_market_backfill',None,json.dumps(document)))
            db.execute('INSERT INTO central_news_body_revisions(body_revision_id,article_revision_id,content_hash,extractor_version,fetched_at,available_at,status,body_text,error) VALUES(?,?,?,?,?,?,?,?,?)',('seed-b','seed-a',stable_document_hash({'body':body['body_text']}),'seed-extractor',1.,3.,'summary_only',body['body_text'],''))
            db.execute('INSERT INTO central_documents VALUES(?,?,?,?,?)',('news_assessment','GLOBAL','seed-a',4.,seed_assessment))
        digest=_sha256(self.prepared)
        stage(self.archive,self.prepared,MARKET,digest)
        finalize_inputs(self.archive,self.prepared,MARKET,digest)
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(('seed-a','seed-b'),db.execute('SELECT article_revision_id,body_revision_id FROM archive_input_progress').fetchone())
            self.assertEqual((4.,seed_assessment),db.execute('SELECT updated_at,document_json FROM central_documents').fetchone())
            self.assertEqual('recomputed_posthoc',db.execute("SELECT verification_state FROM archive_targets_v2 WHERE target_id='GLOBAL'").fetchone()[0])
            self.assertEqual((2.,),db.execute('SELECT available_at FROM central_news_article_revisions').fetchone())

    def test_target_failure_rolls_back_entire_batch_and_restarts(self):
        self.add(MARKET,'a'); self.add(MARKET,'b')
        digest=_sha256(self.prepared)
        stage(self.archive,self.prepared,MARKET,digest)
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON archive_targets_v2 WHEN NEW.identity='b' BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError,'injected'):
            finalize_inputs(self.archive,self.prepared,MARKET,digest,batch_size=2)
        with closing(sqlite3.connect(self.archive)) as db:
            for table in ('central_news_article_revisions','central_news_body_revisions','archive_targets_v2','archive_body_provenance'):
                self.assertEqual(0,db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0])
            self.assertEqual([('pending',2)],db.execute('SELECT progress_state,COUNT(*) FROM archive_input_progress GROUP BY progress_state').fetchall())
            db.execute('DROP TRIGGER fail_second'); db.commit()
        self.assertEqual(2,finalize_inputs(self.archive,self.prepared,MARKET,digest)['inputs_finalized_now'])

    def test_declared_missing_or_conflicting_targets_fail_before_writes(self):
        article,_,rules=self.add(MARKET)
        with self.assertRaisesRegex(ValueError,'missing'):
            validated_targets(article,rules[:-1],MARKET,'GLOBAL')
        self.assertEqual(3,len(validated_targets(article,rules+[rules[0]],MARKET,'GLOBAL')))
        changed={**rules[0],'core_sentences':['changed']}
        with self.assertRaisesRegex(ValueError,'duplicate'):
            validated_targets(article,rules+[changed],MARKET,'GLOBAL')

    def test_writer_lock_blocks_competitor_and_recovers_stale_ledger(self):
        with _writer(self.archive):
            with self.assertRaisesRegex(ValueError,'another'):
                with _writer(self.archive):
                    self.fail('second writer acquired')
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute("UPDATE archive_builder_v2 SET owner='dead-process' WHERE id=1")
        with _writer(self.archive) as db:
            self.assertNotEqual('dead-process',db.execute('SELECT owner FROM archive_builder_v2').fetchone()[0])

    def test_changed_input_rejected_and_v1_migration_rejected(self):
        self.add(SEARCH)
        digest=_sha256(self.prepared)
        stage(self.archive,self.prepared,SEARCH,digest)
        with closing(sqlite3.connect(self.prepared)) as db, db:
            db.execute('UPDATE prepared_news SET updated_at=2.0')
        with self.assertRaisesRegex(ValueError,'snapshot'):
            finalize_inputs(self.archive,self.prepared,SEARCH,digest)
        old=self.root/'old.sqlite3'
        with closing(sqlite3.connect(old)) as db, db:
            db.execute('CREATE TABLE archive_build_manifest(key TEXT PRIMARY KEY,value_json TEXT)')
            db.execute("INSERT INTO archive_build_manifest VALUES('report',?)",(json.dumps({'build_state':'building'}),))
            db.execute('CREATE TABLE archive_input_progress(x INTEGER)')
        with self.assertRaisesRegex(ValueError,'v1'):
            initialize(old,dataset_id='bad')

    def sealed_reader_fixture(self):
        self.prepare_both()
        finalize_events(self.archive)
        build_projection(self.archive)
        # Test-only fixture: production sealing requires the separate full gate.
        with closing(sqlite3.connect(self.archive)) as db, db:
            value=json.loads(db.execute("SELECT value_json FROM archive_build_manifest WHERE key='report'").fetchone()[0])
            value['build_state']='sealed'
            value['coverage_state']='complete'
            value['seal_gate']={'ready_for_seal':True}
            db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",(json.dumps(value),))
        return HistoricalNewsArchiveReader(self.archive,cursor_key=b'fixture-v2')

    def test_readonly_market_and_search_detail_has_all_targets_without_body_in_list(self):
        reader=self.sealed_reader_fixture()
        before=_sha256(self.archive)
        market=reader.page_v2('market',source='flash')
        search=reader.page_v2('search',stock_code='005930')
        self.assertEqual(1,len(market['items']))
        self.assertEqual(1,len(search['items']))
        self.assertEqual([],reader.page_v2('market',source='world')['items'])
        item=market['items'][0]
        self.assertNotIn('body_text',item['display'])
        detail=reader.article_by_id(item['article_revision_id'],body_revision_id=item['body_revision_id'])
        self.assertEqual({'GLOBAL','005930','000660'},{row['target_id'] for row in detail['target_assessments']})
        self.assertEqual('기사 요약',detail['body']['text'])
        self.assertEqual('2026-09-01T09:00:00+09:00',detail['source_contexts'][0]['metadata']['body']['original_published_at'])
        other=search['items'][0]['body_revision_id']
        with self.assertRaisesRegex(ValueError,'belong'):
            reader.article_by_id(item['article_revision_id'],body_revision_id=other)
        self.assertEqual(before,_sha256(self.archive))

    def test_low_space_stops_before_batch_and_releases_builder_owner(self):
        from scripts.prepared_historical_archive_v2 import _transaction
        from collections import namedtuple
        usage=namedtuple('Usage','total used free')
        with _writer(self.archive) as db:
            with patch('scripts.prepared_historical_archive_v2.shutil.disk_usage',return_value=usage(10,9,1)):
                with self.assertRaisesRegex(ValueError,'2GiB'):
                    with _transaction(db):
                        self.fail('low-space batch must not start')
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual((None,),db.execute('SELECT owner FROM archive_builder_v2 WHERE id=1').fetchone())

    def test_same_time_201_market_items_keyset_filters_and_tampering(self):
        self.add(SEARCH)
        for n in range(201):
            self.add(MARKET,f'https://example.test/{n:03d}')
        digest=_sha256(self.prepared)
        for scope in (SEARCH,MARKET):
            stage(self.archive,self.prepared,scope,digest)
            finalize_inputs(self.archive,self.prepared,scope,digest)
        finalize_events(self.archive)
        build_projection(self.archive,max_rows=1)
        self.assertEqual(201,build_projection(self.archive)['projected_now'])
        with closing(sqlite3.connect(self.archive)) as db, db:
            value=json.loads(db.execute("SELECT value_json FROM archive_build_manifest WHERE key='report'").fetchone()[0]); value['build_state']='sealed'
            value['coverage_state']='complete'; value['seal_gate']={'ready_for_seal':True}
            db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",(json.dumps(value),))
        reader=HistoricalNewsArchiveReader(self.archive,cursor_key=b'fixture-v2')
        first=reader.page_v2('market',limit=200)
        last=reader.page_v2('market',limit=200,cursor=first['next_cursor'])
        self.assertEqual(200,len(first['items'])); self.assertEqual(1,len(last['items']))
        self.assertIsNone(last['next_cursor'])
        self.assertEqual(201,len({r['identity'] for r in first['items']+last['items']}))
        for kwargs in ({'source':'world'},{'stock_code':'005930','kind':'search'}):
            kind=kwargs.pop('kind','market')
            with self.assertRaises(InvalidArchiveCursorError):
                reader.page_v2(kind,cursor=first['next_cursor'],**kwargs)
        with self.assertRaises(InvalidArchiveCursorError):
            reader.page_v2('market',cursor='!'+first['next_cursor'])

    def test_http_v2_authentication_diagnostic_pause_and_exact_ids(self):
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.historical_news_archive_routes import create_historical_news_archive_router
        reader=self.sealed_reader_fixture()
        before=_sha256(self.archive)
        def authorize(authorization: str | None = Header(default=None)):
            if authorization != 'Bearer fixture':
                raise HTTPException(status_code=401)
        app=FastAPI(); app.include_router(create_historical_news_archive_router(reader,authorize))
        headers={'Authorization':'Bearer fixture'}
        with patch('scripts.prepared_historical_archive_v2.prepare_job',side_effect=AssertionError('reader calculated RULE')), patch('scripts.prepared_historical_archive_v2._resolve_event',side_effect=AssertionError('reader wrote events')), TestClient(app) as client:
            endpoint='/api/v1/news/historical-archive/v2/market'
            self.assertEqual(401,client.get(endpoint).status_code)
            self.assertEqual(422,client.get(endpoint,headers=headers,params={'source':'unknown'}).status_code)
            response=client.get(endpoint,headers=headers)
            self.assertEqual(200,response.status_code)
            item=response.json()['items'][0]
            detail=f"/api/v1/news/historical-archive/articles/{item['article_revision_id']}"
            params={'dataset_id':reader.dataset_id,'body_revision_id':item['body_revision_id']}
            self.assertEqual(3,len(client.get(detail,headers=headers,params=params).json()['target_assessments']))
            self.assertEqual(409,client.get(detail,headers=headers,params={**params,'dataset_id':'old'}).status_code)
            self.assertEqual(400,client.get(detail,headers=headers,params={**params,'body_revision_id':'wrong'}).status_code)
            self.assertEqual(409,client.get('/api/v1/news/historical-archive/search',headers=headers,params={'stock_code':'005930'}).status_code)
            with patch('kiwoom_monitor.central_server.diagnostic_workloads.is_paused',return_value=True):
                self.assertEqual(503,client.get(endpoint,headers=headers).status_code)
        self.assertEqual(before,_sha256(self.archive))

    def test_market_event_uses_target_not_global_article_owner(self):
        self.add(SEARCH)
        self.add(MARKET,targets=[['GLOBAL','시황'],['005930','기업']],title='기업, 삼성전자와 500억원 공급계약 체결')
        digest=_sha256(self.prepared)
        for scope in (SEARCH,MARKET):
            stage(self.archive,self.prepared,scope,digest)
            finalize_inputs(self.archive,self.prepared,scope,digest)
        finalize_events(self.archive)
        with closing(sqlite3.connect(self.archive)) as db:
            target=db.execute("SELECT event_revision_id FROM archive_targets_v2 WHERE scope=? AND target_id='005930'",(MARKET,)).fetchone()[0]
            self.assertIsNotNone(target)
            self.assertEqual(('005930','GLOBAL'),db.execute('SELECT e.stock_code,a.stock_code FROM central_news_event_revisions e JOIN central_news_article_revisions a ON a.article_revision_id=e.article_revision_id WHERE e.event_revision_id=?',(target,)).fetchone())
            before=db.execute('SELECT event_revision_id,revision_of,accepted_sequence FROM central_news_event_revisions').fetchall()
        self.assertEqual(0,finalize_events(self.archive)['targets_resolved_now'])
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(before,db.execute('SELECT event_revision_id,revision_of,accepted_sequence FROM central_news_event_revisions').fetchall())

    def test_v2_gate_rejects_fake_completion_missing_raw_proof_and_target(self):
        from scripts.audit_prepared_historical_archive_v2 import audit
        self.prepare_both(); finalize_events(self.archive); build_projection(self.archive)
        result=audit(self.archive,full_integrity_check=True)
        self.assertFalse(result['ready_for_seal'])
        self.assertIn('raw_source_coverage_missing',result['blockers'])
        with closing(sqlite3.connect(self.archive)) as db, db:
            db.execute("INSERT INTO archive_build_manifest VALUES('coverage_v2',?)",(json.dumps({'schema':'historical-source-coverage/v2','state':'complete'}),))
            db.execute("DELETE FROM archive_targets_v2 WHERE target_id='000660'")
        result=audit(self.archive,full_integrity_check=True)
        self.assertFalse(result['ready_for_seal'])
        self.assertIn('target_set_mismatch',result['blockers'])
        self.assertIn('raw_source_coverage_missing',result['blockers'])

    def test_event_counter_resumes_without_per_batch_pending_recount(self):
        self.prepare_both()
        original=sqlite3.connect
        queries=[]
        def traced(*args,**kwargs):
            db=original(*args,**kwargs); db.set_trace_callback(queries.append); return db
        with patch('scripts.prepared_historical_archive_v2.sqlite3.connect',side_effect=traced):
            first=finalize_events(self.archive,max_rows=1,batch_size=1)
            with patch('scripts.prepared_historical_archive_v2._resolve_event',side_effect=RuntimeError('injected')):
                with self.assertRaisesRegex(RuntimeError,'injected'):
                    finalize_events(self.archive,batch_size=1)
            final=finalize_events(self.archive,batch_size=1)
        self.assertEqual(3,first['remaining_targets'])
        self.assertEqual(3,final['targets_resolved_now'])
        self.assertEqual(0,final['remaining_targets'])
        self.assertFalse(any("COUNT(*) FROM archive_event_ready_v2 WHERE state='pending'" in query for query in queries))
        with closing(original(self.archive)) as db:
            self.assertEqual(0,db.execute("SELECT COUNT(*) FROM archive_event_ready_v2 WHERE state='pending'").fetchone()[0])

    def test_event_dependency_checks_are_cached_per_article_and_input(self):
        targets=[['GLOBAL','시장']]+[[f'{n:06d}',f'stock-{n}'] for n in range(1,20)]
        self.add(SEARCH,'search-scope')
        self.add(MARKET,'many-targets',targets=targets)
        digest=_sha256(self.prepared)
        for scope in (SEARCH,MARKET):
            stage(self.archive,self.prepared,scope,digest)
            finalize_inputs(self.archive,self.prepared,scope,digest)

        original=sqlite3.connect
        queries=[]
        def traced(*args,**kwargs):
            db=original(*args,**kwargs); db.set_trace_callback(queries.append); return db
        resolution=('no_event','no_event',None,None,None,None,None,())
        with patch('scripts.prepared_historical_archive_v2.sqlite3.connect',side_effect=traced), \
             patch('scripts.prepared_historical_archive_v2._resolve_event',return_value=resolution):
            result=finalize_events(self.archive)

        self.assertEqual(21,result['targets_resolved_now'])
        article_counts=[q for q in queries if q.startswith('SELECT COUNT(*) FROM archive_targets_v2 WHERE article_revision_id=')]
        input_counts=[q for q in queries if q.startswith('SELECT COUNT(*) FROM archive_targets_v2 WHERE scope=')]
        self.assertEqual(2,len(article_counts))
        self.assertEqual(2,len(input_counts))
        with closing(original(self.archive)) as db:
            self.assertEqual(21,db.execute("SELECT COUNT(*) FROM archive_targets_v2 WHERE event_state='no_event'").fetchone()[0])
            self.assertEqual([('event_done',2)],db.execute('SELECT progress_state,COUNT(*) FROM archive_input_progress GROUP BY progress_state').fetchall())

    def test_capacity_sample_cannot_be_served_even_with_sealed_flag(self):
        reader=self.sealed_reader_fixture()
        del reader
        with closing(sqlite3.connect(self.archive)) as db, db:
            value=json.loads(db.execute("SELECT value_json FROM archive_build_manifest WHERE key='report'").fetchone()[0]); value['profile_only']=True
            db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",(json.dumps(value),))
        with self.assertRaisesRegex(ValueError,'sample'):
            HistoricalNewsArchiveReader(self.archive,cursor_key=b'test')

    def test_gate_detects_canonical_body_corruption(self):
        from scripts.audit_prepared_historical_archive_v2 import audit
        self.prepare_both(); finalize_events(self.archive); build_projection(self.archive)
        with closing(sqlite3.connect(self.archive)) as db,db:
            db.execute("UPDATE central_news_body_revisions SET body_text='altered without matching content hash'")
        self.assertIn('canonical_article_or_body_hash_invalid',audit(self.archive)['blockers'])

    def empty_seed(self):
        from scripts.materialize_historical_news_seed import materialize
        seed=self.root/'seed.sqlite3'
        with closing(sqlite3.connect(seed)) as db,db:
            db.execute('CREATE TABLE seed_rows(table_name TEXT,row_key TEXT,payload_json TEXT,payload_hash TEXT,role TEXT,PRIMARY KEY(table_name,row_key))')
            db.execute('CREATE TABLE seed_manifest(key TEXT PRIMARY KEY,value_json TEXT)')
            db.execute("INSERT INTO seed_manifest VALUES('report',?)",(json.dumps({'schema':'historical-news-seed/v1','snapshot_started_at':'2026-10-13T00:00:00Z','tables':{},'roles':{}}),))
        self.archive.unlink()
        materialize(seed,self.archive)
        initialize(self.archive,dataset_id='full-fixture-v2')
        return seed

    def raw_fixture(self,scope,*,missing=False):
        path=self.root/('raw-search.sqlite3' if scope==SEARCH else 'raw-market.sqlite3')
        identity='https://example.test/a'
        with closing(sqlite3.connect(path)) as db,db:
            if scope==SEARCH:
                db.execute('CREATE TABLE news_articles(provider TEXT,office_id TEXT,article_id TEXT,original_url TEXT,portal_url TEXT,title TEXT,summary TEXT)')
                db.execute('CREATE TABLE news_search_observations(provider TEXT,office_id TEXT,article_id TEXT,code TEXT,query_text TEXT)')
                db.execute('CREATE TABLE source_pages(provider TEXT,request_key TEXT,observed_at TEXT,response_sha256 TEXT,payload_json TEXT,item_count INTEGER)')
                db.execute('CREATE TABLE news_article_body_snapshots(provider TEXT,office_id TEXT,article_id TEXT,body_text TEXT,body_sha256 TEXT,extractor_version TEXT,fetched_at TEXT)')
                db.execute('CREATE TABLE news_article_fetch_attempts(provider TEXT,office_id TEXT,article_id TEXT,status TEXT,error TEXT)')
                db.execute("INSERT INTO news_article_fetch_attempts VALUES('naver','001','a','http_403','original response forbidden')")
                db.execute('INSERT INTO news_articles VALUES(?,?,?,?,?,?,?)',('naver','001','a',identity,'','일반 시장 소식','기사 요약'))
                db.execute('INSERT INTO news_search_observations VALUES(?,?,?,?,?)',('naver','001','a','005930','삼성전자'))
                body='a distinct older raw BODY version'
                db.execute('INSERT INTO news_article_body_snapshots VALUES(?,?,?,?,?,?,?)',('naver','001','a',body,hashlib.sha256(body.encode()).hexdigest(),'old-extractor','2026-01-01T00:00:00Z'))
                if missing:
                    db.execute('INSERT INTO news_articles VALUES(?,?,?,?,?,?,?)',('naver','001','missing','https://example.test/missing','','미준비 기사','요약'))
                    db.execute('INSERT INTO news_search_observations VALUES(?,?,?,?,?)',('naver','001','missing','005930','삼성전자'))
            else:
                db.execute('CREATE TABLE market_news_articles(source TEXT,office_id TEXT,article_id TEXT,article_url TEXT,title TEXT,summary TEXT)')
                db.execute('CREATE TABLE market_news_pages(source TEXT,target_date TEXT,page INTEGER,observed_at TEXT,endpoint TEXT,response_sha256 TEXT,payload_json TEXT,article_count INTEGER,invalid_count INTEGER)')
                db.execute('INSERT INTO market_news_articles VALUES(?,?,?,?,?,?)',('flash','001','a',identity,'일반 시장 소식','기사 요약'))
        return path

    def test_source_capture_uses_rowid_range_with_provider_index_and_preserves_rows(self):
        from scripts.capture_historical_archive_sources_v2 import capture
        raw=self.raw_fixture(SEARCH)
        payload=json.dumps({'collection':[]})
        with closing(sqlite3.connect(raw)) as db,db:
            db.execute('CREATE UNIQUE INDEX provider_key ON source_pages(provider,request_key,observed_at)')
            db.executemany('INSERT INTO source_pages VALUES(?,?,?,?,?,?)',[
                ('other' if n%4==0 else 'naver_historical_search',
                 f'code=005930&date=2026-09-01&query=test&start={n}',
                 '2026-09-01T00:00:00+00:00',hashlib.sha256(payload.encode()).hexdigest(),payload,0)
                for n in range(1,61)])
            reference=db.execute("SELECT rowid FROM source_pages WHERE provider='naver_historical_search' ORDER BY rowid").fetchall()
        before=_sha256(raw)
        trace=[]
        connect=sqlite3.connect
        def traced_connect(*args,**kwargs):
            db=connect(*args,**kwargs)
            db.set_trace_callback(trace.append)
            return db
        with patch('sqlite3.connect',side_effect=traced_connect):
            capture(self.archive,raw,SEARCH,batch_size=7)
        queries=[sql for sql in trace if sql.startswith('SELECT rowid AS source_rowid,* FROM source_pages')]
        self.assertGreater(len(queries),1)
        with closing(connect(raw)) as db:
            for sql in queries:
                plan=' '.join(str(row[3]) for row in db.execute('EXPLAIN QUERY PLAN '+sql))
                self.assertIn('INTEGER PRIMARY KEY',plan)
                self.assertNotIn('TEMP B-TREE',plan)
                self.assertEqual(db.execute(sql.replace(' NOT INDEXED','')).fetchall(),db.execute(sql).fetchall())
        with closing(connect(self.archive)) as db:
            actual=db.execute("SELECT CAST(source_row_key AS INTEGER) FROM archive_source_history_v2 WHERE source_table='source_pages' ORDER BY CAST(source_row_key AS INTEGER)").fetchall()
            self.assertEqual(reference,actual)
        self.assertEqual(before,_sha256(raw))
        self.assertEqual(0,capture(self.archive,raw,SEARCH)['source_rows_captured_now'])

    def test_real_source_coverage_seals_readonly_and_recovers_receipt(self):
        from scripts.capture_historical_archive_sources_v2 import capture,finish_coverage
        from scripts.audit_prepared_historical_archive_v2 import audit
        from scripts.seal_prepared_historical_archive_v2 import seal
        seed=self.empty_seed()
        self.prepare_both(); finalize_events(self.archive); build_projection(self.archive)
        for scope in (SEARCH,MARKET):
            raw=self.raw_fixture(scope)
            first=capture(self.archive,raw,scope,max_rows=1)
            self.assertEqual(1,first['source_rows_captured_now'])
            capture(self.archive,raw,scope)
            self.assertEqual(0,capture(self.archive,raw,scope)['source_rows_captured_now'])
        self.assertEqual('complete',finish_coverage(self.archive)['state'])
        gate=audit(self.archive,seed=seed,full_integrity_check=True)
        self.assertEqual([],gate['blockers'])
        published=seal(self.archive,seed,self.root/'seal.json')
        self.addCleanup(lambda: self.archive.chmod(stat.S_IWRITE) if self.archive.exists() else None)
        self.assertEqual(_sha256(self.archive),published['sha256'])
        with closing(sqlite3.connect(self.archive.as_uri()+'?mode=ro',uri=True)) as db:
            self.assertEqual('delete',db.execute('PRAGMA journal_mode').fetchone()[0])
            evidence=json.loads(db.execute("SELECT payload_json FROM archive_source_history_v2 WHERE source_table='news_article_body_snapshots'").fetchone()[0])
            self.assertEqual('a distinct older raw BODY version',evidence['body_text'])
            attempt=json.loads(db.execute("SELECT payload_json FROM archive_source_history_v2 WHERE source_table='news_article_fetch_attempts'").fetchone()[0])
            self.assertEqual('http_403',attempt['status'])
            self.assertEqual('original response forbidden',attempt['error'])
        before=_sha256(self.archive)
        reader=HistoricalNewsArchiveReader(self.archive,cursor_key=b'fixture')
        self.assertEqual(1,len(reader.page_v2('market')['items']))
        recovered=seal(self.archive,seed,self.root/'recovered.json')
        self.assertEqual(before,recovered['sha256'])
        self.assertEqual(before,_sha256(self.archive))
        from scripts.publish_historical_archive_file import publish
        destination=self.root/'historical-news-archives'; destination.mkdir()
        version=destination/published['dataset_id']; version.mkdir()
        expected={key:published[key] for key in ('dataset_id','bytes','sha256')}
        (version/'transfer.json').write_text(json.dumps(expected),encoding='utf-8')
        partial=version/'archive.sqlite3.partial'
        partial.write_bytes(self.archive.read_bytes()[:500])
        copied=publish(self.root/'seal.json',destination)
        target=Path(copied['archive'])
        self.addCleanup(lambda: target.chmod(stat.S_IWRITE) if target.exists() else None)
        self.assertFalse(copied['server_activated'])
        self.assertEqual(before,_sha256(target))
        self.assertFalse(partial.exists())
        (version/'publication.json').unlink()
        self.assertEqual(copied,publish(self.root/'seal.json',destination))
        self.assertTrue((version/'publication.json').exists())
        target.chmod(stat.S_IWRITE)
        with target.open('ab') as output:
            output.write(b'tamper')
        with self.assertRaisesRegex(FileExistsError,'never overwrite'):
            publish(self.root/'seal.json',destination)


    def test_missing_received_preparation_blocks_seal_and_mutated_raw_resume(self):
        from scripts.capture_historical_archive_sources_v2 import capture,finish_coverage
        from scripts.seal_prepared_historical_archive_v2 import seal
        seed=self.empty_seed()
        self.prepare_both(); finalize_events(self.archive); build_projection(self.archive)
        raw=self.raw_fixture(SEARCH,missing=True)
        capture(self.archive,raw,SEARCH)
        capture(self.archive,self.raw_fixture(MARKET),MARKET)
        result=finish_coverage(self.archive)
        self.assertEqual('incomplete',result['state'])
        self.assertIn(SEARCH+'_received_keys_without_prepared_input',result['blockers'])
        with self.assertRaisesRegex(ValueError,'blockers'):
            seal(self.archive,seed,self.root/'bad-seal.json')
        self.assertFalse((self.root/'bad-seal.json').exists())
        with closing(sqlite3.connect(raw)) as db,db:
            db.execute("UPDATE news_articles SET title='changed'")
        with self.assertRaisesRegex(ValueError,'changed'):
            capture(self.archive,raw,SEARCH)

    def test_corrupt_raw_body_rolls_back_evidence_and_records_precise_failure(self):
        from scripts.capture_historical_archive_sources_v2 import capture
        self.prepare_both()
        raw=self.raw_fixture(SEARCH)
        with closing(sqlite3.connect(raw)) as db,db:
            db.execute("UPDATE news_article_body_snapshots SET body_sha256='wrong'")
        with self.assertRaisesRegex(ValueError,'news_article_body_snapshots/1'):
            capture(self.archive,raw,SEARCH)
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(0,db.execute("SELECT COUNT(*) FROM archive_source_history_v2 WHERE source_table='news_article_body_snapshots'").fetchone()[0])
            error=json.loads(db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?',('source_capture_v2_'+SEARCH+'_failure',)).fetchone()[0])
            self.assertEqual('news_article_body_snapshots',error['table'])
            self.assertEqual(1,error['source_rowid'])

    def test_world_service_exclusion_uses_kst_and_does_not_recreate_old_news(self):
        from scripts.prepared_historical_archive_v2 import world_service_excluded
        self.assertTrue(world_service_excluded('world','2020-12-31T14:59:59+00:00'))
        self.assertFalse(world_service_excluded('world','2020-12-31T15:00:00+00:00'))
        self.assertFalse(world_service_excluded('flash','2020-01-01T00:00:00+09:00'))
        self.add(SEARCH)
        self.add(MARKET,'https://stock.naver.com/news/worldnews/old')
        with closing(sqlite3.connect(self.prepared)) as db,db:
            row=db.execute('SELECT article_json FROM prepared_news WHERE scope=?',(MARKET,)).fetchone()
            article=json.loads(row[0]); article['source']='world'; article['document']['published_at']='2020-01-01T00:00:00+09:00'
            db.execute('UPDATE prepared_news SET article_json=? WHERE scope=?',(json.dumps(article),MARKET))
        digest=_sha256(self.prepared)
        for scope in (SEARCH,MARKET):
            stage(self.archive,self.prepared,scope,digest)
            finalize_inputs(self.archive,self.prepared,scope,digest)
        finalize_events(self.archive); build_projection(self.archive)
        with closing(sqlite3.connect(self.archive)) as db:
            self.assertEqual(('source_excluded',),db.execute('SELECT progress_state FROM archive_input_progress WHERE scope=?',(MARKET,)).fetchone())
            self.assertEqual(0,db.execute("SELECT COUNT(*) FROM archive_projection_v2 WHERE kind='market'").fetchone()[0])
            self.assertEqual(0,db.execute("SELECT COUNT(*) FROM central_news_article_revisions WHERE stock_code='GLOBAL'").fetchone()[0])


if __name__=='__main__':
    unittest.main()
