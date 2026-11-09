"""General BODY authority, real native/wire delivery and headless ownership."""
from __future__ import annotations

import asyncio
import json
import importlib.util
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import contextmanager, closing
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from kiwoom_monitor.central_server.config import CentralServerSettings, ProducerGrant
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.domain.news_observation import NewsBodyConflict
from kiwoom_monitor.central_server.news_jobs import NewsJobRunner
from kiwoom_monitor.domain.news_observation import ARTICLE_BODY_EXTRACTOR_VERSION, NewsBodyDeliveryError
from kiwoom_monitor.infrastructure.news_body_client import NewsBodyClient
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient, CentralContentUnavailableError
from kiwoom_monitor.news_body_processor import run_news_body_processor
from tests.unit.test_news_observation_history import _article
from tests.unit.test_query_authority import wire_server, query_app


NOW=datetime(2026,10,24,10,tzinfo=timezone.utc)
ADMIN,QUERY,BODY,OTHER,RESEARCH='body-admin-fixture','body-query-fixture','body-token-fixture','other-body-fixture','research-only-fixture'


def settings(database='sqlite:///:memory:'):
    return CentralServerSettings(database,ADMIN,query_access_token=QUERY,
        research_result_grants=(ProducerGrant('research',RESEARCH),),
        news_body_grants=(ProducerGrant('body_a',BODY),ProducerGrant('body_b',OTHER)),
        news_history_jobs_enabled=False,news_naver_api_enabled=False,news_naver_stock_enabled=False,
        news_naver_market_enabled=False,news_query_set_enabled=False,autonomous_top20_enabled=False,
        market_event_collection_enabled=False,external_market_symbols='')


def seed(store, *, scope='watchlist', title='BODY fixture', link='https://example.invalid/article'):
    value=_article(title)[0]
    value['key']=value['document']['identity']='body-'+uuid.uuid4().hex
    value['document'].update(link=link,original_link=link)
    value['collection_scope']=scope
    store.upsert_documents('news_article',[value])
    return store.load_news_history('article',target='005930',identity=value['key'])[0]


def envelope(store,job, *, body='fixture BODY 원문'):
    admitted=store.load_news_body_job_input(job['owner'],job['job_key'],job['attempts'])
    return {key:job[key] for key in ('attempts','input_hash','processing_version','payload')} | {
        'input_descriptor':admitted['input_descriptor'],
        'result':{'stage':'BODY','body':{'extractor_version':ARTICLE_BODY_EXTRACTOR_VERSION,
            'fetched_at':NOW.timestamp(),'status':'fulltext','body_text':body,'error':''}}}


def seed_producer_article(store, scope):
    """Use the current producer's document and native writer, including publishers."""
    from kiwoom_monitor.domain.news_ai_contract import NewsAssessment, StockNewsItem
    from kiwoom_monitor.central_server.news_read_routes import _serialize
    from kiwoom_monitor.infrastructure.news_source_documents import article_document
    identity='publisher-'+uuid.uuid4().hex
    item=StockNewsItem('삼성전자 원문 기사', '삼성전자 원문 설명',
        'https://news.example.invalid/'+identity, 'https://origin.example.invalid/'+identity,
        NOW, NewsAssessment(True,'기업','중립','fixture',80,50))
    if scope in {'watchlist','local_projection_sync'}:
        document={'stock_code':'005930','stock_name':'삼성전자',**_serialize(item)}
        store.upsert_documents('news_article',[{'owner':'005930','key':identity,
            'document':document,'collector_id':'naver' if scope=='watchlist' else 'local-sync:fixture',
            'collection_scope':scope}])
        stock='005930'
    else:
        document=article_document(item)
        # The source-page writer uses the store's same injected NOW as claim and acceptance.
        store.save_news_source_page({'source_id':'publisher-fixture-'+identity,
            'query_text':'삼성전자','scope':scope,'items':[{'identity':identity,
                'document':document,'targets':[]}]})
        stock='GLOBAL'
    article=store.load_news_history('article',target=stock,identity=identity)[0]
    assert 'publisher_domain' in article['document'] and 'publisher_name' in article['document']
    return article


class BodyNativeCases:
    postgres=False

    def _sql(self,sql,args=()):
        if self.postgres:
            with self.store._connect() as connection:
                cursor=connection.execute(sql.replace('?','%s'),args)
                return cursor.fetchall() if cursor.description else []
        with self.store._lock,self.store._connection() as connection:
            cursor=connection.execute(sql,args)
            return cursor.fetchall() if cursor.description else []

    def snapshot(self):
        return self._sql('SELECT * FROM central_news_jobs ORDER BY job_key'),self._sql('SELECT * FROM central_news_body_revisions ORDER BY body_revision_id')

    def claim(self,owner='body_a',**kwargs):
        return self.store.claim_news_body_job(owner,now=kwargs.get('now',self.clock))

    def test_hard_body_nonhistorical_filter_precedes_attempt_and_recovery_mutation(self):
        valid=seed(self.store)
        historical=seed(self.store,scope='historical_backfill')
        old_version=seed(self.store)
        self._sql('UPDATE central_news_jobs SET processing_version=?,state=\'RUNNING\',updated_at=0 WHERE article_revision_id=?',('unsupported-extractor',old_version['article_revision_id']))
        for stage in ('RULE','AI'):
            article=seed(self.store)
            self._sql('UPDATE central_news_jobs SET stage=?,state=\'RUNNING\',updated_at=0 WHERE article_revision_id=?',(stage,article['article_revision_id']))
        before=self.snapshot()[0]
        job=self.claim()
        self.assertEqual(valid['article_revision_id'],job['article_revision_id'])
        self.assertEqual(('BODY',1,'body_a'),(job['stage'],job['attempts'],job['owner']))
        untouched=[row for row in before if row[1]!=valid['article_revision_id']]
        self.assertEqual(untouched,[row for row in self.snapshot()[0] if row[1]!=valid['article_revision_id']])
        self.assertIsNone(self.claim())

    def test_owner_attempt_hash_version_payload_and_unadmitted_result_reject_without_write(self):
        seed(self.store)
        job=self.claim()
        raw={key:job[key] for key in ('attempts','input_hash','processing_version','payload')} | {'input_descriptor':'unadmitted','result':{'stage':'BODY','error':'x'}}
        before=self.snapshot()
        with self.assertRaises(ValueError): self.store.accept_news_body_job_result('body_a',job['job_key'],raw)
        self.assertEqual(before,self.snapshot())
        value=envelope(self.store,job)
        for owner,key,alter in [('body_b','attempts',job['attempts']),('body_a','attempts',job['attempts']+1),
                ('body_a','input_hash','0'*64),('body_a','processing_version','different'),
                ('body_a','input_descriptor','forged'),('body_a','payload',dict(job['payload'],link='https://forged.invalid'))]:
            with self.subTest(owner=owner,field=key):
                bad=deepcopy(value);bad[key]=alter;before=self.snapshot()
                with self.assertRaises(ValueError): self.store.accept_news_body_job_result(owner,job['job_key'],bad)
                self.assertEqual(before,self.snapshot())

    def test_changed_native_article_url_source_or_pinned_body_is_not_hidden_by_echoed_hash(self):
        from types import SimpleNamespace
        from kiwoom_monitor.domain.news_observation import stable_document_hash, news_article_content_hash
        scopes=('watchlist','local_projection_sync','query_set','naver_stock_market')
        for scope in scopes:
            with self.subTest(producer=scope,case='normal-publisher-native-runner'):
                article=seed_producer_article(self.store,scope)
                document=deepcopy(article['document'])
                full_hash=stable_document_hash(document)
                source_hash=news_article_content_hash(document)
                self.assertNotEqual(full_hash,source_hash)
                expected=full_hash if scope in {'watchlist','local_projection_sync'} else source_hash
                self.assertEqual(expected,article['content_hash'])
                claimed=[]
                def claim(**options):
                    job=self.store.claim_news_body_job('body_a',now=options['now'])
                    claimed.append(job)
                    return [job] if job else []
                def body_input(job):
                    try:
                        admitted=self.store.load_news_body_job_input('body_a',job['job_key'],job['attempts'])
                    except NewsBodyConflict as error:
                        raise NewsBodyDeliveryError('native BODY input rejected') from error
                    job['input_descriptor']=admitted['input_descriptor']
                    return admitted['cached_body']
                def body_accept(job,result):
                    value={key:job[key] for key in ('attempts','input_hash','processing_version','input_descriptor','payload')}
                    return self.store.accept_news_body_job_result('body_a',job['job_key'],dict(value,result=result))
                runner=NewsJobRunner(SimpleNamespace(claim_news_jobs=claim),body_input=body_input,
                    body_accept=body_accept,fetcher=lambda *_a,**_k:'publisher native BODY',now_provider=self._now)
                self.assertEqual(1,asyncio.run(runner.run_once()))
                self.assertEqual(article['article_revision_id'],claimed[0]['article_revision_id'])
                receipt=self.store.load_news_body_job_receipt('body_a',claimed[0]['job_key'],claimed[0]['attempts'])
                self.assertEqual('completed',receipt['state'])
                body=self.store.load_news_body_revision(receipt['output_ref'])
                self.assertEqual('publisher native BODY',body['body_text'])
                self.assertEqual(expected,self.store.load_news_article_revision(article['article_revision_id'])['content_hash'])
                self.assertEqual(document,self.store.load_news_article_revision(article['article_revision_id'])['document'])
        for scope,field in [('watchlist','publisher_name'),('local_projection_sync','publisher_domain'),
                ('query_set','title'),('naver_stock_market','link')]:
            with self.subTest(producer=scope,case='admitted-native-tamper',field=field):
                article=seed_producer_article(self.store,scope);job=self.claim();value=envelope(self.store,job)
                document=deepcopy(article['document']);document[field]='changed native value'
                self._sql('UPDATE central_news_article_revisions SET document_json=? WHERE article_revision_id=?',
                    (json.dumps(document),article['article_revision_id']))
                before=self.snapshot()
                with self.assertRaises(ValueError): self.store.accept_news_body_job_result('body_a',job['job_key'],value)
                self.assertEqual(before,self.snapshot())

        first=seed(self.store);job=self.claim()
        document=deepcopy(first['document']);document['link']='https://changed-before-input.invalid'
        self._sql('UPDATE central_news_article_revisions SET document_json=? WHERE article_revision_id=?',(json.dumps(document),first['article_revision_id']))
        before=self.snapshot()
        with self.assertRaises(ValueError): self.store.load_news_body_job_input('body_a',job['job_key'],job['attempts'])
        self.assertEqual(before,self.snapshot())
        mutations=('article','payload','source','body')
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                article=seed(self.store)
                if mutation=='body':
                    self.store.save_news_body_revision({'article_revision_id':article['article_revision_id'],
                        'status':'fulltext','body_text':'original cached','fetched_at':self.clock})
                job=self.claim();value=envelope(self.store,job)
                if mutation=='article':
                    document=deepcopy(article['document']);document['description']='changed input'
                    self._sql('UPDATE central_news_article_revisions SET document_json=? WHERE article_revision_id=?',(json.dumps(document),article['article_revision_id']))
                elif mutation=='payload':
                    self._sql('UPDATE central_news_jobs SET payload_json=? WHERE job_key=?',(json.dumps(dict(job['payload'],link='https://changed.invalid')),job['job_key']))
                elif mutation=='source':
                    self._sql('UPDATE central_news_article_revisions SET collector_id=? WHERE article_revision_id=?',('other-source',article['article_revision_id']))
                else:
                    self._sql('UPDATE central_news_body_revisions SET body_text=? WHERE article_revision_id=?',('tampered cache',article['article_revision_id']))
                before=self.snapshot()
                with self.assertRaises(ValueError): self.store.accept_news_body_job_result('body_a',job['job_key'],value)
                self.assertEqual(before,self.snapshot())

    def test_exact_cached_ref_and_first_receipt_survive_duplicate_and_newer_body(self):
        article=seed(self.store)
        first=self.store.save_news_body_revision({'article_revision_id':article['article_revision_id'],'status':'fulltext','body_text':'cached first','fetched_at':self.clock})
        job=self.claim();value=envelope(self.store,job)
        value['result']={'stage':'BODY','output_ref':first}
        self.store.save_news_body_revision({'article_revision_id':article['article_revision_id'],'status':'fulltext','body_text':'cached later','fetched_at':self.clock})
        wrong=deepcopy(value);wrong['result']['output_ref']='wrong-ref'
        before=self.snapshot()
        with self.assertRaises(ValueError): self.store.accept_news_body_job_result('body_a',job['job_key'],wrong)
        self.assertEqual(before,self.snapshot())
        receipt=self.store.accept_news_body_job_result('body_a',job['job_key'],value)
        self.assertEqual(('completed',first),(receipt['state'],receipt['output_ref']))
        before=self.snapshot()
        again=self.store.accept_news_body_job_result('body_a',job['job_key'],value)
        self.assertEqual(('already_completed',first),(again['state'],again['output_ref']))
        self.assertEqual(before,self.snapshot())

    def test_reclaim_fences_old_owner_and_local_claim_clears_remote_authority(self):
        seed(self.store);old=self.claim();value=envelope(self.store,old)
        newer=self.claim('body_b',now=self.clock+121)
        self.assertEqual((old['job_key'],2),(newer['job_key'],newer['attempts']))
        before=self.snapshot()
        with self.assertRaises(ValueError): self.store.accept_news_body_job_result('body_a',old['job_key'],value)
        self.assertEqual(before,self.snapshot())
        local=self.store.claim_news_jobs(now=self.clock+242)[0]
        self.assertEqual(3,local['attempts'])
        before=self.snapshot()
        with self.assertRaises(ValueError): self.store.load_news_body_job_input('body_b',local['job_key'],local['attempts'])
        self.assertEqual(before,self.snapshot())

    def test_failure_uses_server_retry_clock_and_body_terminal_publication_is_atomic(self):
        article=seed(self.store);job=self.claim();value=envelope(self.store,job)
        value['result']={'stage':'BODY','error':'fixture compute error','next_retry_at':99999999999}
        receipt=self.store.accept_news_body_job_result('body_a',job['job_key'],value)
        self.assertEqual('pending',receipt['state'])
        retry=self._sql('SELECT next_retry_at FROM central_news_jobs WHERE job_key=?',(job['job_key'],))[0][0]
        self.assertEqual(self.clock+2,retry)
        self.assertIsNone(self.claim(now=self.clock+1))
        next_job=self.claim(now=self.clock+3);result=envelope(self.store,next_job)
        self.store.accept_news_body_job_result('body_a',next_job['job_key'],result)
        bodies=self.store.load_news_history('body',target=article['article_revision_id'])
        self.assertEqual(1,len(bodies))
        self.assertEqual('completed',self.store.load_news_body_job_receipt('body_a',job['job_key'],2)['state'])

    def test_body_rule_enqueue_and_terminal_rollback_together_on_native_rejection(self):
        from tests.unit.test_news_jobs import NewsJobResultNativeTests
        article=seed(self.store);job=self.claim();value=envelope(self.store,job)
        before=self.snapshot()
        with NewsJobResultNativeTests._reject(self,'central_news_jobs','UPDATE',"NEW.job_key='"+job['job_key']+"' AND NEW.state='COMPLETED'"):
            with self.assertRaises(Exception): self.store.accept_news_body_job_result('body_a',job['job_key'],value)
        self.assertEqual(before,self.snapshot())
        self.assertEqual([],self.store.load_news_history('body',target=article['article_revision_id']))

    def test_pending_error_first_receipt_is_durable_exact_and_duplicate_does_not_settle_again(self):
        from kiwoom_monitor.domain.news_observation import stable_document_hash
        article=seed(self.store);job=self.claim();value=envelope(self.store,job)
        value['result']={'stage':'BODY','error':'accepted retry error','next_retry_at':99999999999}
        admitted=json.loads(self._sql('SELECT body_input_ref FROM central_news_jobs WHERE job_key=?',(job['job_key'],))[0][0])
        wakes=[];self.store.set_news_job_wakeup(lambda:wakes.append('accepted'))
        first=self.store.accept_news_body_job_result('body_a',job['job_key'],value)
        self.assertEqual({'state':'pending','output_ref':'','job_key':job['job_key'],'attempts':1,'owner':'body_a'},first)
        # Each public call uses a fresh native connection, after acceptance COMMIT.
        readback=self.store.load_news_body_job_receipt('body_a',job['job_key'],1)
        self.assertEqual(first,readback['accepted_receipt'])
        self.assertEqual(stable_document_hash({'owner':'body_a','job_key':job['job_key'],'envelope':value}),readback['accepted_envelope_hash'])
        pin=json.loads(self._sql('SELECT body_input_ref FROM central_news_jobs WHERE job_key=?',(job['job_key'],))[0][0])
        self.assertEqual(admitted,{key:pin[key] for key in admitted})
        self.assertEqual(first,pin['accepted_receipt'])
        self.assertEqual(self.clock+2,self._sql('SELECT next_retry_at FROM central_news_jobs WHERE job_key=?',(job['job_key'],))[0][0])
        before=self.snapshot()
        self.clock+=1
        from tests.unit.test_news_jobs import NewsJobResultNativeTests
        with NewsJobResultNativeTests._reject(self,'central_news_jobs','UPDATE',"NEW.job_key='"+job['job_key']+"'"), \
                patch.object(self.store,'_now_provider',side_effect=AssertionError('duplicate must not read retry clock')):
            self.assertEqual(first,self.store.accept_news_body_job_result('body_a',job['job_key'],deepcopy(value)))
        self.assertEqual(before,self.snapshot())
        self.assertEqual(['accepted'],wakes)
        self.assertEqual([],self.store.load_news_history('body',target=article['article_revision_id']))

    def test_concurrent_pending_error_duplicate_observes_one_locked_first_receipt(self):
        from concurrent.futures import ThreadPoolExecutor
        seed(self.store);job=self.claim();value=envelope(self.store,job)
        value['result']={'stage':'BODY','error':'concurrent exact error'}
        entered,release,second_entered=threading.Event(),threading.Event(),threading.Event()
        calls=[];wakes=[];self.store.set_news_job_wakeup(lambda:wakes.append('accepted'))
        def native_now():
            calls.append('clock')
            if len(calls)==1:
                entered.set()
                if not release.wait(5): raise AssertionError('native acceptance release missing')
            return self._now()
        def duplicate():
            second_entered.set()
            return self.store.accept_news_body_job_result('body_a',job['job_key'],deepcopy(value))
        with patch.object(self.store,'_now_provider',side_effect=native_now),ThreadPoolExecutor(max_workers=2) as workers:
            first=workers.submit(self.store.accept_news_body_job_result,'body_a',job['job_key'],value)
            try:
                self.assertTrue(entered.wait(5))
                second=workers.submit(duplicate)
                self.assertTrue(second_entered.wait(5));self.assertFalse(second.done())
            finally: release.set()
            first_receipt=first.result(timeout=5);self.assertEqual(first_receipt,second.result(timeout=5))
        self.assertEqual('pending',first_receipt['state']);self.assertEqual(['accepted'],wakes)
        self.assertEqual(2,len(calls),'only original normalization and native settlement read the clock')
        self.assertEqual(first_receipt,self.store.load_news_body_job_receipt('body_a',job['job_key'],1)['accepted_receipt'])

    def test_pending_error_proof_binds_original_envelope_before_retry_and_error_normalization(self):
        seed(self.store);job=self.claim();value=envelope(self.store,job)
        value['result']={'stage':'BODY','error':'x'*1000+'original tail','next_retry_at':99999999999}
        self.store.accept_news_body_job_result('body_a',job['job_key'],value)
        before=self.snapshot()
        for field,alter in [('error','x'*1000+'different tail'),('next_retry_at',99999999998)]:
            with self.subTest(field=field):
                changed=deepcopy(value);changed['result'][field]=alter
                with self.assertRaises(NewsBodyConflict):
                    self.store.accept_news_body_job_result('body_a',job['job_key'],changed)
                self.assertEqual(before,self.snapshot())

    def test_pending_error_legacy_receipt_has_no_proof_and_reclaim_clears_accepted_proof(self):
        seed(self.store);job=self.claim();value=envelope(self.store,job)
        value['result']={'stage':'BODY','error':'retry'}
        self.store.accept_news_body_job_result('body_a',job['job_key'],value)
        raw=self._sql('SELECT body_input_ref FROM central_news_jobs WHERE job_key=?',(job['job_key'],))[0][0]
        pin=json.loads(raw);pin.pop('accepted_envelope_hash');pin.pop('accepted_receipt')
        self._sql('UPDATE central_news_jobs SET body_input_ref=? WHERE job_key=?',(json.dumps(pin),job['job_key']))
        legacy=self.store.load_news_body_job_receipt('body_a',job['job_key'],1)
        self.assertEqual('pending',legacy['state']);self.assertNotIn('accepted_envelope_hash',legacy)
        before=self.snapshot()
        self.assertEqual('stale',self.store.accept_news_body_job_result('body_a',job['job_key'],value)['state'])
        self.assertEqual(before,self.snapshot())
        # Restore the originally accepted proof before actual reclaim; new claim
        # must remove it rather than accidentally lend it to a later attempt.
        self._sql('UPDATE central_news_jobs SET body_input_ref=? WHERE job_key=?',(raw,job['job_key']))
        newer=self.claim('body_b',now=self.clock+3)
        self.assertEqual(2,newer['attempts'])
        self.assertIsNone(self._sql('SELECT body_input_ref FROM central_news_jobs WHERE job_key=?',(job['job_key'],))[0][0])
        for operation in (lambda:self.store.load_news_body_job_receipt('body_a',job['job_key'],1),
                lambda:self.store.accept_news_body_job_result('body_a',job['job_key'],value)):
            with self.assertRaises(NewsBodyConflict): operation()
        current=self.store.load_news_body_job_receipt('body_b',job['job_key'],2)
        self.assertEqual('running',current['state']);self.assertNotIn('accepted_receipt',current)

    def test_pending_error_settlement_and_first_receipt_roll_back_on_proof_write_failure(self):
        from tests.unit.test_news_jobs import NewsJobResultNativeTests
        seed(self.store);job=self.claim();value=envelope(self.store,job)
        value['result']={'stage':'BODY','error':'retry atomically'}
        before=self.snapshot();wakes=[];self.store.set_news_job_wakeup(lambda:wakes.append('accepted'))
        condition="NEW.job_key='"+job['job_key']+"' AND NEW.body_input_ref<>OLD.body_input_ref"
        with NewsJobResultNativeTests._reject(self,'central_news_jobs','UPDATE',condition):
            with self.assertRaises(Exception): self.store.accept_news_body_job_result('body_a',job['job_key'],value)
        self.assertEqual(before,self.snapshot());self.assertEqual([],wakes)
        receipt=self.store.load_news_body_job_receipt('body_a',job['job_key'],1)
        self.assertEqual('running',receipt['state']);self.assertNotIn('accepted_receipt',receipt)
        first=self.store.accept_news_body_job_result('body_a',job['job_key'],value)
        self.assertEqual('pending',first['state']);self.assertEqual(['accepted'],wakes)

    def test_third_failure_preserves_failed_duplicate_and_durable_first_receipt(self):
        article=seed(self.store)
        for attempt in range(1,4):
            job=self.claim();value=envelope(self.store,job);value['result']={'stage':'BODY','error':'failure '+str(attempt)}
            first=self.store.accept_news_body_job_result('body_a',job['job_key'],value)
            self.assertEqual('failed' if attempt==3 else 'pending',first['state'])
            self.assertEqual(first,self.store.load_news_body_job_receipt('body_a',job['job_key'],attempt)['accepted_receipt'])
            self.clock+=70
        before=self.snapshot()
        again=self.store.accept_news_body_job_result('body_a',job['job_key'],value)
        self.assertEqual(('already_failed',first['output_ref']),(again['state'],again['output_ref']))
        self.assertEqual(before,self.snapshot())
        bodies=self.store.load_news_history('body',target=article['article_revision_id'])
        self.assertEqual(1,len(bodies));self.assertEqual('failed',bodies[0]['status'])


class NewsBodyNativeTests(BodyNativeCases,unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.clock=NOW.timestamp()
        self.store=self.make_store(Path(self.tmp.name)/'body.sqlite3')
        self.addCleanup(self.store.close);self.store.initialize()

    def _now(self): return datetime.fromtimestamp(self.clock,timezone.utc)

    def make_store(self,path): return SQLiteQueryStore(path,now_provider=self._now)


class NewsBodyWireTests(unittest.TestCase):
    def test_explicit_body_route_inventory_is_additive_to_reviewed_C2_baseline(self):
        from dataclasses import replace
        off=query_app(replace(settings(),news_body_grants=()))
        on=query_app(settings())
        try:
            old,new=off.openapi(),on.openapi()
            expected={
                '/api/v1/news/body-jobs/claim':{'post'},
                '/api/v1/news/body-jobs/{key}/input':{'get'},
                '/api/v1/news/body-jobs/{key}/receipt':{'get'},
                '/api/v1/news/body-jobs/{key}/result':{'post'},
            }
            self.assertEqual(set(expected),set(new['paths'])-set(old['paths']))
            for path,methods in expected.items():
                self.assertEqual(methods,set(new['paths'][path]))
                for method in methods:
                    operation=new['paths'][path][method]
                    self.assertEqual({},operation['responses']['200']['content']['application/json']['schema'])
                    parameters={(p['name'],p['in']):p for p in operation['parameters']}
                    self.assertEqual({'authorization','x-kiwoom-expected-result-owner'},
                        {name for name,where in parameters if where=='header'})
                    if path.endswith(('/input','/receipt')):
                        self.assertEqual({'type':'integer','minimum':1,'title':'Attempts'},parameters[('attempts','query')]['schema'])
                        self.assertTrue(parameters[('attempts','query')]['required'])
            self.assertEqual(old['components'],new['components'])
            for path in old['paths']: self.assertEqual(old['paths'][path],new['paths'][path])
        finally:
            off.state.query_authority_store.close();on.state.query_authority_store.close()

    def test_grants_are_explicit_disjoint_and_research_wire_defaults_are_preserved(self):
        from kiwoom_monitor.central_server import CentralServerSettings as public_settings
        self.assertIs(CentralServerSettings,public_settings)
        self.assertEqual((),CentralServerSettings('sqlite:///:memory:',ADMIN).news_body_grants)
        for token in (ADMIN,QUERY,RESEARCH):
            with self.subTest(token=token),self.assertRaises(ValueError):
                settings_with=CentralServerSettings('sqlite:///:memory:',ADMIN,query_access_token=QUERY,
                    research_result_grants=(ProducerGrant('research',RESEARCH),),news_body_grants=(ProducerGrant('body_a',token),))
        self.assertNotIn(BODY,repr(ProducerGrant('body_a',BODY)))

    def test_actual_loopback_auth_scope_and_same_runner_body_output(self):
        app=query_app(settings());store=app.state.query_authority_store
        article=seed_producer_article(store,'watchlist')
        from kiwoom_monitor.domain.research_contract import encode_reported_result_owner
        expected={'X-Kiwoom-Expected-Result-Owner':encode_reported_result_owner('body_a')}
        with wire_server(app) as (url,_),httpx.Client(base_url=url,timeout=5) as wire:
            before=store.load_news_history('body',target=article['article_revision_id'])
            for token in (ADMIN,QUERY,RESEARCH):
                self.assertEqual(401,wire.post('/api/v1/news/body-jobs/claim',json={},headers={**expected,'Authorization':'Bearer '+token}).status_code)
            client=NewsBodyClient(url,'body_a',BODY)
            self.assertEqual(9*3600,client.server_now().utcoffset().total_seconds())
            async def run():
                runner=NewsJobRunner(client,body_input=client.load_body_input,body_accept=client.accept_body_result,
                    fetcher=lambda *_a,**_k:('identical BODY fixture',''),now_provider=client.server_now)
                self.assertEqual(1,await runner.run_once())
                await runner.close()
            asyncio.run(run())
            self.assertIsNotNone(client.client._server_clock)
            bodies=store.load_news_history('body',target=article['article_revision_id'])
            self.assertEqual([],before);self.assertEqual(1,len(bodies))
            with tempfile.TemporaryDirectory() as directory:
                local=SQLiteQueryStore(Path(directory)/'local.sqlite3');local.initialize()
                local_article=seed_producer_article(local,'watchlist')
                asyncio.run(NewsJobRunner(local,fetcher=lambda *_a,**_k:('identical BODY fixture','')).run_once())
                local_body=local.load_news_history('body',target=local_article['article_revision_id'])[0]
                self.assertEqual({key:bodies[0][key] for key in ('body_text','content_hash','status','extractor_version')},
                                 {key:local_body[key] for key in ('body_text','content_hash','status','extractor_version')})
                local.close()
            query=CentralContentClient(url,QUERY).load_news_history('body',target=article['article_revision_id'])
            self.assertEqual(bodies,query['revisions'])
            filing_url = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261024900001"
            official = seed(store, title="단일판매ㆍ공급계약해지", link=filing_url)
            filing = {"filing_url": filing_url, "original_filing_url": "",
                      "document_url": "https://dart.fss.or.kr/report/viewer.do?rcpNo=20261024900001",
                      "filing_html": "<table><tr><td>해지금액</td><td>100</td></tr></table>"}
            filing_runner = NewsJobRunner(client, body_input=client.load_body_input,
                                          body_accept=client.accept_body_result, now_provider=client.server_now,
                                          filing_fetcher=lambda *_a, **_kw: filing)
            self.assertEqual(1, asyncio.run(filing_runner.run_once(preferred_stage="BODY")))
            archived, = store.load_documents("news_official_filing", "005930")
            self.assertEqual(filing["filing_html"], archived["document"]["filing_html"])
            self.assertEqual(official["article_revision_id"], archived["document"]["article_revision_id"])
            for path in ('/api/v1/settings/operations','/api/v1/research/reported-results/not-mine','/api/v1/settings/credentials'):
                self.assertNotEqual(200,wire.get(path,headers={**expected,'Authorization':'Bearer '+BODY}).status_code)

    def test_true_commit_ack_loss_confirms_first_receipt_without_generic_failure_or_recompute(self):
        app=query_app(settings());store=app.state.query_authority_store;article=seed(store)
        with wire_server(app) as (url,_):
            from kiwoom_monitor.infrastructure.central_content_client import _result_urlopen
            dropped=[]
            def lose_response(request,**kwargs):
                response=_result_urlopen(request,**kwargs)
                if request.method=='POST' and request.full_url.endswith('/result') and not dropped:
                    # Native transaction committed before HTTP response headers.
                    self.assertEqual(1,len(store.load_news_history('body',target=article['article_revision_id'])))
                    dropped.append(True);response.close()
                    raise ConnectionResetError('fixture HTTP response discarded before body read')
                return response
            client=NewsBodyClient(url,'body_a',BODY,opener=lose_response);job=client.claim_news_jobs()[0]
            client.load_body_input(job)
            result={'stage':'BODY','body':{'extractor_version':ARTICLE_BODY_EXTRACTOR_VERSION,'fetched_at':client.server_now().timestamp(),
                'status':'fulltext','body_text':'committed original','error':''}}
            receipt=client.accept_body_result(job,result)
            self.assertEqual([True],dropped)
            self.assertEqual('completed',receipt['state'])
            first=store.load_news_history('body',target=article['article_revision_id'])
            again=client.accept_body_result(job,result)
            self.assertEqual(('already_completed',receipt['output_ref']),(again['state'],again['output_ref']))
            self.assertEqual(first,store.load_news_history('body',target=article['article_revision_id']))
            self.assertEqual(1,len(first))

    def test_unknown_transport_is_not_resubmitted_as_calculation_error(self):
        client=NewsBodyClient('http://127.0.0.1:1','body_a',BODY)
        with patch.object(client.client,'claim_news_body_job',side_effect=CentralContentUnavailableError('fixture')), \
             self.assertRaises(NewsBodyDeliveryError):
            client.claim_news_jobs()

    def test_pending_error_true_commit_ack_loss_confirms_exact_receipt_without_repost_or_recompute(self):
        app=query_app(settings());store=app.state.query_authority_store
        value=_article('empty BODY failure fixture')[0]
        value['key']=value['document']['identity']='body-failure-'+uuid.uuid4().hex
        value['document'].update(link='https://example.invalid/failure',original_link='https://example.invalid/failure',description='')
        value['collection_scope']='watchlist';store.upsert_documents('news_article',[value])
        article=store.load_news_history('article',target='005930',identity=value['key'])[0]
        from kiwoom_monitor.infrastructure.central_content_client import _result_urlopen
        requests=[];committed=[];fetched=[]
        def lose_response(request,**kwargs):
            requests.append((request.method,request.full_url))
            response=_result_urlopen(request,**kwargs)
            if request.method=='POST' and request.full_url.endswith('/result'):
                self.assertEqual([],committed,'an uncertain result must not be posted again')
                value=json.loads(request.data);key=request.full_url.rsplit('/',2)[1]
                receipt=store.load_news_body_job_receipt('body_a',key,value['attempts'])
                self.assertEqual('pending',receipt['state'])
                with store._lock,store._connection() as db:
                    row=db.execute('SELECT state,error,next_retry_at,updated_at,body_input_ref FROM central_news_jobs WHERE job_key=?',(key,)).fetchone()
                committed.append((key,value,receipt,tuple(row)))
                response.close()
                raise ConnectionResetError('pending result committed; HTTP response discarded')
            return response
        def fail_body(*_args,**_kwargs):
            fetched.append('once');raise RuntimeError('controlled BODY calculation failure')
        with wire_server(app) as (url,_):
            client=NewsBodyClient(url,'body_a',BODY,opener=lose_response)
            async def run():
                runner=NewsJobRunner(client,body_input=client.load_body_input,body_accept=client.accept_body_result,
                    fetcher=fail_body,now_provider=client.server_now)
                try: self.assertEqual(1,await runner.run_once())
                finally: await runner.close()
            asyncio.run(run())
            self.assertEqual(['once'],fetched);self.assertEqual(1,len(committed))
            self.assertEqual(1,sum(method=='POST' and path.endswith('/result') for method,path in requests))
            self.assertEqual(1,sum(method=='GET' and '/receipt?' in path for method,path in requests))
            key,value,first,row=committed[0]
            current=store.load_news_body_job_receipt('body_a',key,value['attempts'])
            self.assertEqual(first,current)
            with store._lock,store._connection() as db:
                after=db.execute('SELECT state,error,next_retry_at,updated_at,body_input_ref FROM central_news_jobs WHERE job_key=?',(key,)).fetchone()
            self.assertEqual(row,tuple(after))
            self.assertEqual('기사 본문과 검색 요약을 가져오지 못했습니다.',row[1])
            self.assertEqual([],store.load_news_history('body',target=article['article_revision_id']))

    def test_lost_ack_without_exact_native_proof_remains_unknown_including_legacy_terminal(self):
        from kiwoom_monitor.infrastructure.central_content_client import _result_urlopen
        for mutation in ('missing_pending','missing_completed','hash','owner','key','attempt','attempt_type','state','missing_output','corrupt'):
            with self.subTest(proof=mutation):
                app=query_app(settings());store=app.state.query_authority_store;seed(store);requests=[]
                def lose_response(request,**kwargs):
                    requests.append((request.method,request.full_url))
                    response=_result_urlopen(request,**kwargs)
                    if request.method=='POST' and request.full_url.endswith('/result'):
                        key=request.full_url.rsplit('/',2)[1]
                        with store._lock,store._connection() as db:
                            pin=json.loads(db.execute('SELECT body_input_ref FROM central_news_jobs WHERE job_key=?',(key,)).fetchone()[0])
                            if mutation.startswith('missing'):
                                pin.pop('accepted_envelope_hash');pin.pop('accepted_receipt')
                            elif mutation=='hash': pin['accepted_envelope_hash']='0'*64
                            elif mutation=='owner': pin['accepted_receipt']['owner']='body_b'
                            elif mutation=='key': pin['accepted_receipt']['job_key']='0'*64
                            elif mutation=='attempt': pin['accepted_receipt']['attempts']+=1
                            elif mutation=='attempt_type': pin['accepted_receipt']['attempts']=True
                            elif mutation=='state': pin['accepted_receipt']['state']='completed'
                            elif mutation=='missing_output': pin['accepted_receipt'].pop('output_ref')
                            else: pin['accepted_receipt']='corrupt first receipt'
                            db.execute('UPDATE central_news_jobs SET body_input_ref=? WHERE job_key=?',(json.dumps(pin),key))
                        response.close();raise ConnectionResetError('committed response lost with unusable proof')
                    return response
                with wire_server(app) as (url,_):
                    client=NewsBodyClient(url,'body_a',BODY,opener=lose_response);job=client.claim_news_jobs()[0]
                    client.load_body_input(job)
                    result=({'stage':'BODY','body':{'extractor_version':ARTICLE_BODY_EXTRACTOR_VERSION,
                        'fetched_at':client.server_now().timestamp(),'status':'fulltext','body_text':'legacy completed','error':''}}
                        if mutation=='missing_completed' else {'stage':'BODY','error':'pending compute error'})
                    with self.assertRaises(NewsBodyDeliveryError): client.accept_body_result(job,result)
                    self.assertEqual(1,sum(method=='POST' and path.endswith('/result') for method,path in requests))
                    self.assertEqual(1,sum(method=='GET' and '/receipt?' in path for method,path in requests))

    def test_pending_error_definite_http_rejection_cannot_confirm_prior_accepted_receipt(self):
        from kiwoom_monitor.infrastructure.central_content_client import _result_urlopen
        app=query_app(settings());store=app.state.query_authority_store;seed(store);requests=[]
        def record_request(request,**kwargs):
            requests.append((request.method,request.full_url));return _result_urlopen(request,**kwargs)
        with wire_server(app) as (url,_):
            client=NewsBodyClient(url,'body_a',BODY,opener=record_request);job=client.claim_news_jobs()[0]
            client.load_body_input(job)
            first=client.accept_body_result(job,{'stage':'BODY','error':'first accepted error'})
            self.assertEqual('pending',first['state'])
            requests.clear()
            with self.assertRaises(NewsBodyDeliveryError):
                client.accept_body_result(job,{'stage':'BODY','error':'different rejected error'})
            self.assertEqual(1,len(requests));self.assertEqual('POST',requests[0][0])
            self.assertEqual(first,store.load_news_body_job_receipt('body_a',job['job_key'],1)['accepted_receipt'])

    def test_direct_success_keeps_legacy_terminal_receipt_compatibility(self):
        app=query_app(settings());store=app.state.query_authority_store;seed(store)
        with wire_server(app) as (url,_):
            client=NewsBodyClient(url,'body_a',BODY);job=client.claim_news_jobs()[0];client.load_body_input(job)
            result={'stage':'BODY','body':{'extractor_version':ARTICLE_BODY_EXTRACTOR_VERSION,
                'fetched_at':client.server_now().timestamp(),'status':'fulltext','body_text':'legacy direct','error':''}}
            first=client.accept_body_result(job,result)
            self.assertEqual('completed',first['state']);self.assertNotIn('accepted_envelope_hash',first)
            with store._lock,store._connection() as db:
                pin=json.loads(db.execute('SELECT body_input_ref FROM central_news_jobs WHERE job_key=?',(job['job_key'],)).fetchone()[0])
                pin.pop('accepted_envelope_hash');pin.pop('accepted_receipt')
                db.execute('UPDATE central_news_jobs SET body_input_ref=? WHERE job_key=?',(json.dumps(pin),job['job_key']))
            again=client.accept_body_result(job,result)
            self.assertEqual(('already_completed',first['output_ref']),(again['state'],again['output_ref']))
            self.assertNotIn('accepted_envelope_hash',store.load_news_body_job_receipt('body_a',job['job_key'],1))

    def test_actual_module_subprocess_with_pruned_roles_and_no_site_packages_stops_its_own_work(self):
        provider=FastAPI()
        @provider.get('/article')
        def article_html():
            return HTMLResponse('<html><head><meta property="article:published_time" content="2026-10-24T10:00:00+09:00"></head><body><div id="dic_area"><p>'+('일반 BODY fixture 원문입니다. '*30)+'</p></div></body></html>')
        app=query_app(settings());store=app.state.query_authority_store
        with tempfile.TemporaryDirectory() as directory,wire_server(provider) as (article_url,_),wire_server(app) as (url,_):
            root=Path(directory);source=root/'src'
            shutil.copytree(Path(__file__).resolve().parents[2]/'src',source,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            package=source/'kiwoom_monitor'
            for relative in ('presentation','infrastructure/kiwoom_rest','central_server/app.py','central_server/config.py',
                    'central_server/ai_service.py','central_server/news_service.py','central_server/diagnostic_replay_contract.py',
                    'central_server/diagnostic_workloads.py','infrastructure/news_ai.py','research_process.py',
                    'research_run.py','research_result_delivery.py','news_process.py','domain/order_contract.py'):
                target=package/relative
                self.assertTrue(target.resolve().is_relative_to(package.resolve()))
                if target.is_dir(): shutil.rmtree(target)
                elif target.exists(): target.unlink()
            connection=root/'producer.json'
            connection.write_text(json.dumps({'mode':'personal_server','server_url':url,'access_token':BODY}),encoding='utf-8')
            # Keep the existing common IANA clock data, while site-packages and
            # all optional UI/account/AI packages remain inaccessible under -S.
            common=root/'common-dependencies';common.mkdir()
            tzdata=importlib.util.find_spec('tzdata')
            self.assertIsNotNone(tzdata)
            shutil.copytree(Path(tzdata.origin).parent,common/'tzdata')
            env=dict(os.environ,PYTHONPATH=os.pathsep.join((str(source),str(common))),PYTHONDONTWRITEBYTECODE='1')
            probe="import sys,json; import kiwoom_monitor.news_body_processor as entry; assert not any('site-packages' in p for p in sys.path); assert not any(n.startswith(('PySide6','kiwoom_monitor.presentation','kiwoom_monitor.infrastructure.kiwoom_rest','kiwoom_monitor.infrastructure.news_ai','kiwoom_monitor.central_server.app')) for n in sys.modules); print(json.dumps({'entry':entry.__file__,'no_site_packages':True}))"
            checked=subprocess.run([sys.executable,'-S','-c',probe],cwd=root,env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(0,checked.returncode,checked.stdout+checked.stderr)
            self.assertTrue(Path(json.loads(checked.stdout)['entry']).is_relative_to(source))
            # A wrong existing research-only credential exits; it cannot claim BODY.
            wrong=root/'research.json';wrong.write_text(json.dumps({'mode':'personal_server','server_url':url,'access_token':RESEARCH}),encoding='utf-8')
            denied=subprocess.run([sys.executable,'-S','-m','kiwoom_monitor.news_body_processor','--connection',str(wrong),'--owner','research'],
                cwd=root,env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(1,denied.returncode,denied.stdout+denied.stderr)
            article=seed(store,link=article_url+'/article')
            flags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
            process=subprocess.Popen([sys.executable,'-S','-m','kiwoom_monitor.news_body_processor','--connection',str(connection),'--owner','body_a'],
                cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=flags)
            try:
                deadline=time.monotonic()+10
                while not store.load_news_history('body',target=article['article_revision_id']) and process.poll() is None and time.monotonic()<deadline:
                    time.sleep(0.02)
                self.assertEqual(1,len(store.load_news_history('body',target=article['article_revision_id'])))
                self.assertEqual('fulltext',store.load_news_history('body',target=article['article_revision_id'])[0]['status'])
                publication=store.load_document('news_original_publication',article['article_revision_id'],'published_at')
                self.assertEqual(article_url+'/article',publication['document']['source_url'])
                process.send_signal(getattr(signal,'CTRL_BREAK_EVENT',signal.SIGINT))
                stdout,stderr=process.communicate(timeout=10)
                self.assertEqual(0,process.returncode,stdout+stderr)
                self.assertEqual('stopped',json.loads(stdout)['status'])
                self.assertNotIn(BODY,stdout+stderr)
            finally:
                if process.poll() is None:
                    process.send_signal(getattr(signal,'CTRL_BREAK_EVENT',signal.SIGINT))
                    process.communicate(timeout=10)

    def test_default_server_without_body_grants_does_not_require_body_entry_or_activate_worker(self):
        from dataclasses import replace
        app=query_app(replace(settings(),news_body_grants=()))
        with wire_server(app) as (url,_),httpx.Client(base_url=url,timeout=5) as client:
            self.assertEqual(404,client.post('/api/v1/news/body-jobs/claim',json={},headers={'Authorization':'Bearer '+ADMIN}).status_code)


class NewsBodyLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_http_waiter_drains_native_and_preserves_native_failure_cause(self):
        from kiwoom_monitor.central_server.news_body_routes import create_news_body_router
        for fails in (False,True):
            with self.subTest(native_error=fails),tempfile.TemporaryDirectory() as directory:
                store=SQLiteQueryStore(Path(directory)/'native.sqlite3',now_provider=lambda:NOW);store.initialize()
                article=seed(store);job=store.claim_news_body_job('body_a',now=NOW.timestamp())
                value=envelope(store,job);entered,release=threading.Event(),threading.Event()
                original=store.accept_news_body_job_result;native_error=RuntimeError('controlled native BODY failure')
                def accepted(*args,**kwargs):
                    entered.set()
                    if not release.wait(5): raise AssertionError('native release missing')
                    if fails: raise native_error
                    return original(*args,**kwargs)
                app=FastAPI();app.include_router(create_news_body_router(store,lambda:'body_a',now_provider=lambda:NOW))
                with patch.object(store,'accept_news_body_job_result',side_effect=accepted):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
                        task=asyncio.create_task(client.post('/api/v1/news/body-jobs/'+job['job_key']+'/result',json=value))
                        try:
                            self.assertTrue(await asyncio.to_thread(entered.wait,5))
                            task.cancel();await asyncio.sleep(0.02);task.cancel();await asyncio.sleep(0.02)
                            self.assertFalse(task.done())
                            self.assertEqual(1,len(store.load_news_history('article',target='005930')))
                            self.assertEqual([],store.load_news_history('body',target=article['article_revision_id']))
                            release.set()
                            with self.assertRaises(asyncio.CancelledError) as caught: await task
                            if fails: self.assertIs(native_error,caught.exception.__cause__)
                            self.assertEqual(0 if fails else 1,len(store.load_news_history('body',target=article['article_revision_id'])))
                        finally:
                            release.set();await asyncio.gather(task,return_exceptions=True)
                store.close()

    async def test_stop_drains_native_acceptance_while_peer_reader_remains_alive(self):
        app=query_app(settings());store=app.state.query_authority_store;article=seed(store)
        entered,release=threading.Event(),threading.Event()
        original=store.accept_news_body_job_result
        def accept(*args,**kwargs):
            entered.set()
            if not release.wait(5): raise AssertionError('fixture native release missing')
            return original(*args,**kwargs)
        with patch.object(store,'accept_news_body_job_result',side_effect=accept),wire_server(app) as (url,_):
            stop=asyncio.Event();client=NewsBodyClient(url,'body_a',BODY)
            task=asyncio.create_task(run_news_body_processor(client,stop,fetcher=lambda *_a,**_k:'owned native BODY'))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,5))
                stop.set();await asyncio.sleep(0.05)
                self.assertFalse(task.done())
                self.assertEqual([],store.load_news_history('body',target=article['article_revision_id']))
                self.assertEqual(1,len(store.load_news_history('article',target='005930')))
                release.set();await asyncio.wait_for(task,5)
                self.assertEqual(1,len(store.load_news_history('body',target=article['article_revision_id'])))
            finally:
                release.set();stop.set();await asyncio.gather(task,return_exceptions=True)


class NewsBodyOwnerRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_owner_cancellation_during_stop_waiter_join_still_drains_native(self):
        app=query_app(settings());store=app.state.query_authority_store;article=seed(store)
        entered,release=threading.Event(),threading.Event();cleanup_entered=asyncio.Event();cleanup_release=asyncio.Event()
        native=store.accept_news_body_job_result;runners=[]
        def accept(*args,**kwargs):
            entered.set()
            if not release.wait(5): raise AssertionError('fixture native release missing')
            return native(*args,**kwargs)
        class Stop(asyncio.Event):
            async def wait(self):
                try: return await super().wait()
                finally:
                    cleanup_entered.set();await cleanup_release.wait()
        def runner_factory(*args,**kwargs):
            value=NewsJobRunner(*args,**kwargs);runners.append(value);return value
        with patch.object(store,'accept_news_body_job_result',side_effect=accept),wire_server(app) as (url,_), \
                patch('kiwoom_monitor.news_body_processor.NewsJobRunner',side_effect=runner_factory):
            stop=Stop();task=asyncio.create_task(run_news_body_processor(NewsBodyClient(url,'body_a',BODY),stop,
                fetcher=lambda *_a,**_k:'cancel-owned native BODY'))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,5))
                task.cancel();await asyncio.wait_for(cleanup_entered.wait(),5)
                task.cancel();await asyncio.sleep(0.05)
                self.assertFalse(task.done(),'owner returned before accepted native completion')
                self.assertEqual([],store.load_news_history('body',target=article['article_revision_id']))
                self.assertEqual(1,len(store.load_news_history('article',target='005930')))
                cleanup_release.set();release.set()
                with self.assertRaises(asyncio.CancelledError): await task
                self.assertEqual(1,len(store.load_news_history('body',target=article['article_revision_id'])))
            finally:
                cleanup_release.set();release.set();stop.set()
                await asyncio.gather(task,return_exceptions=True)
                for runner in runners: await runner.close()

    async def test_real_diagnostic_scope_keeps_runner_identity_and_recorded_input_high_water(self):
        from kiwoom_monitor.central_server.diagnostic_replay_contract import captured_workload,_OWNER
        from kiwoom_monitor.central_server.diagnostic_trace import input_token
        observed=[];claimed=threading.Event()
        class Store:
            def claim_news_jobs(self,**kwargs):
                observed.append(_OWNER.get());claimed.set();return []
        runner=NewsJobRunner(Store(),diagnostic_scope=captured_workload('news','news_jobs'))
        runner._recorded_input_high_water=(input_token('collector_inputs'),'fixture-recorded-input')
        await runner.start()
        try: self.assertTrue(await asyncio.to_thread(claimed.wait,5))
        finally: await runner.close()
        self.assertEqual('news_jobs:'+format(id(runner),'x'),observed[0].producer_component)
        self.assertEqual('fixture-recorded-input',observed[0].cause_input_id)


if __name__=='__main__': unittest.main()
