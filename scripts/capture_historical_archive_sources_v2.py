"""Capture received-key coverage and immutable source evidence on this PC.

Missing preparation is a blocker, not an automatically excluded article. Page
observations and unmapped BODY versions are evidence, never invented canonical
revisions. Sources are read-only; a changed quiescent source refuses resume.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Iterator

ROOT=Path(__file__).resolve().parents[1]
for item in (ROOT,ROOT/'src'):
    if str(item) not in sys.path:
        sys.path.insert(0,str(item))

from scripts.prepared_historical_archive_v2 import SEARCH,MARKET,SCOPES,WORLD_EXCLUSION,world_service_excluded,_writer,_transaction,_json
from scripts.select_historical_news_page_keys import _request_fields
from kiwoom_monitor.infrastructure.historical_backfill import parse_naver_historical_search_page
from kiwoom_monitor.infrastructure.naver_stock_market_news import parse_page as parse_market_page


@contextmanager
def _raw(path: Path) -> Iterator[tuple[sqlite3.Connection,dict]]:
    path=path.resolve(strict=True)
    fp={'path':str(path),'bytes':path.stat().st_size,'mtime_ns':path.stat().st_mtime_ns}
    wal=Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('raw source has live WAL; freeze/quiesce before capture')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        yield db,fp
    if path.stat().st_size!=fp['bytes'] or path.stat().st_mtime_ns!=fp['mtime_ns'] or wal.exists() and wal.stat().st_size:
        raise ValueError('raw source changed during evidence capture')


def _tables(db: sqlite3.Connection) -> None:
    with _transaction(db):
        db.execute('CREATE TABLE IF NOT EXISTS archive_source_coverage_v2(scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,input_hash TEXT,metadata_json TEXT NOT NULL,PRIMARY KEY(scope,stock_code,identity))')
        db.execute('CREATE TABLE IF NOT EXISTS archive_source_history_v2(scope TEXT NOT NULL,source_table TEXT NOT NULL,source_row_key TEXT NOT NULL,state TEXT NOT NULL,payload_json TEXT NOT NULL,payload_sha256 TEXT NOT NULL,PRIMARY KEY(scope,source_table,source_row_key))')
        db.execute('CREATE TABLE IF NOT EXISTS archive_failure_review_v2(scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,input_hash TEXT NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(scope,stock_code,identity))')
        db.execute("CREATE INDEX IF NOT EXISTS archive_source_body_evidence_v2 ON archive_source_history_v2(scope,json_extract(payload_json,'$.provider'),json_extract(payload_json,'$.office_id'),json_extract(payload_json,'$.article_id')) WHERE source_table='news_article_body_snapshots'")
        db.execute("CREATE INDEX IF NOT EXISTS archive_source_market_identity_v2 ON archive_source_history_v2(scope,json_extract(payload_json,'$.article_url')) WHERE source_table='market_news_articles'")


def _manifest(db: sqlite3.Connection,raw: sqlite3.Connection,scope: str,fp: dict) -> dict:
    key='source_capture_v2_'+scope
    saved=db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?',(key,)).fetchone()
    if saved:
        value=json.loads(saved[0])
        if value['raw_fingerprint']!=fp:
            raise ValueError('raw capture input changed; no mixed-generation resume')
        return value
    names=('news_articles','source_pages','news_article_body_snapshots') if scope==SEARCH else ('market_news_articles','market_news_pages')
    if scope==SEARCH and raw.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_article_fetch_attempts'").fetchone():
        names+=('news_article_fetch_attempts',)
    maxima={name:raw.execute('SELECT COALESCE(MAX(rowid),0) FROM '+name).fetchone()[0] for name in names}
    expected={name:raw.execute('SELECT COUNT(*) FROM '+name+" WHERE rowid<=?"+(" AND provider='naver_historical_search'" if name=='source_pages' else ''),(cutoff,)).fetchone()[0] for name,cutoff in maxima.items()}
    value={'raw_fingerprint':fp,'scope':scope,'cutoffs':maxima,'source_rows_expected':expected,'history_counts':{},'last':{name:0 for name in names},'state':'building'}
    with _transaction(db):
        db.execute('INSERT INTO archive_build_manifest VALUES(?,?)',(key,_json(value)))
    return value


def _evidence(db: sqlite3.Connection,scope: str,table: str,key: str,payload: dict) -> int:
    encoded=_json(payload)
    state='source_excluded' if payload.get('scope_exclusion_reason')==WORLD_EXCLUSION else 'unmapped_evidence_preserved'
    return db.execute('INSERT INTO archive_source_history_v2 VALUES(?,?,?,?,?,?) ON CONFLICT(scope,source_table,source_row_key) DO NOTHING',(scope,table,key,state,encoded,hashlib.sha256(encoded.encode()).hexdigest())).rowcount


def _received(db: sqlite3.Connection,scope: str,owner: str,identity: str,metadata: dict) -> None:
    current=db.execute('SELECT input_hash FROM archive_input_progress WHERE scope=? AND stock_code=? AND identity=?',(scope,owner,identity)).fetchone()
    db.execute('INSERT INTO archive_source_coverage_v2 VALUES(?,?,?,?,?) ON CONFLICT(scope,stock_code,identity) DO UPDATE SET input_hash=excluded.input_hash,metadata_json=excluded.metadata_json',(scope,owner,identity,current[0] if current else None,_json(metadata)))


def capture(path: Path,raw_path: Path,scope: str,*,max_rows: int=0,batch_size: int=80) -> dict:
    if scope not in SCOPES or max_rows<0 or not 1<=batch_size<=250:
        raise ValueError('invalid source capture arguments')
    if path.resolve()==raw_path.resolve():
        raise ValueError('archive cannot be a source')
    with _raw(raw_path) as (raw,fp), _writer(path) as db:
        _tables(db); manifest=_manifest(db,raw,scope,fp)
        def record(table: str,key: str,payload: dict) -> None:
            added=_evidence(db,scope,table,key,payload)
            manifest['history_counts'][table]=manifest['history_counts'].get(table,0)+added
        raw.row_factory=sqlite3.Row
        done=0
        for table,cutoff in manifest['cutoffs'].items():
            while manifest['last'][table]<cutoff and (not max_rows or done<max_rows):
                take=min(batch_size,max_rows-done) if max_rows else batch_size
                extra=" AND provider='naver_historical_search'" if table=='source_pages' else ''
                rows=raw.execute('SELECT rowid AS source_rowid,* FROM '+table+' WHERE rowid>? AND rowid<=?'+extra+' ORDER BY rowid LIMIT ?',(manifest['last'][table],cutoff,take)).fetchall()
                if not rows:
                    manifest['last'][table]=cutoff
                    with _transaction(db):
                        db.execute('UPDATE archive_build_manifest SET value_json=? WHERE key=?',(_json(manifest),'source_capture_v2_'+scope))
                    break
                rowid=rows[0]['source_rowid']
                try:
                    with _transaction(db):
                        for raw_row in rows:
                            row=dict(raw_row); rowid=row.pop('source_rowid')
                            if table=='news_articles':
                                identity=row['original_url'] or row['portal_url'] or f"naver:{row['office_id']}:{row['article_id']}"
                                links=raw.execute('SELECT code,MIN(query_text) AS name FROM news_search_observations WHERE provider=? AND office_id=? AND article_id=? GROUP BY code',(row['provider'],row['office_id'],row['article_id'])).fetchall()
                                for link in links:
                                    _received(db,scope,str(link['code']),identity,{**row,'stock_name':link['name']})
                                record(table,str(rowid),row)
                            elif table=='market_news_articles':
                                identity=row['article_url']
                                if world_service_excluded(row['source'],row.get('published_at')):
                                    row['scope_exclusion_reason']=WORLD_EXCLUSION
                                    db.execute("UPDATE archive_input_progress SET progress_state='source_excluded',error=? WHERE scope=? AND stock_code='GLOBAL' AND identity=? AND progress_state IN ('source_failed','pending')",(WORLD_EXCLUSION,scope,identity))
                                else:
                                    _received(db,scope,'GLOBAL',identity,row)
                                    db.execute('INSERT INTO archive_market_sources_v2 VALUES(?,?,?) ON CONFLICT(identity,source) DO NOTHING',(identity,row['source'],_json(row)))
                                record(table,str(rowid),row)
                            elif table=='news_article_fetch_attempts':
                                # Request failures/exclusions are original evidence,
                                # never an implicit waiver of missing preparation.
                                record(table,str(rowid),row)
                            elif table=='news_article_body_snapshots':
                                if hashlib.sha256(row['body_text'].encode()).hexdigest()!=row['body_sha256']:
                                    raise ValueError('raw BODY payload hash mismatch')
                                record(table,str(rowid),row)
                            else:
                                payload=row['payload_json']
                                if hashlib.sha256(payload.encode()).hexdigest()!=row['response_sha256']:
                                    raise ValueError('raw page payload hash mismatch')
                                if table=='source_pages':
                                    code,first,last,query,start=_request_fields(row['request_key'])
                                    page=parse_naver_historical_search_page(code,query,first,start,json.loads(payload),target_end_date=last,observed_at=datetime.fromisoformat(row['observed_at']))
                                    if len(page.items)!=row['item_count']:
                                        raise ValueError('raw page item count changed')
                                    for n,item in enumerate(page.items):
                                        evidence=asdict(item)
                                        evidence.update(source_rowid=rowid,observed_at=row['observed_at'],response_sha256=row['response_sha256'])
                                        record('parsed_search_items',f'{rowid}:{n}',evidence)
                                else:
                                    page=parse_market_page(row['source'],row['target_date'],row['page'],json.loads(payload),endpoint=row['endpoint'],observed_at=row['observed_at'])
                                    if len(page.articles)!=row['article_count'] or page.invalid_count!=row['invalid_count']:
                                        raise ValueError('market page item count changed')
                                    for n,item in enumerate(page.articles):
                                        evidence=asdict(item)
                                        evidence.update(source_rowid=rowid,source=row['source'],observed_at=row['observed_at'],response_sha256=row['response_sha256'])
                                        if world_service_excluded(row['source'],item.published_at):
                                            evidence['scope_exclusion_reason']=WORLD_EXCLUSION
                                        record('parsed_market_items',f'{rowid}:{n}',evidence)
                                record(table,str(rowid),{k:v for k,v in row.items() if k!='payload_json'})
                            manifest['last'][table]=rowid
                        db.execute('UPDATE archive_build_manifest SET value_json=? WHERE key=?',(_json(manifest),'source_capture_v2_'+scope))
                except Exception as error:
                    failure={'scope':scope,'table':table,'source_rowid':rowid,
                             'error':f'{type(error).__name__}: {error}'[:1000]}
                    with _transaction(db):
                        db.execute('INSERT INTO archive_build_manifest VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',('source_capture_v2_'+scope+'_failure',_json(failure)))
                    raise ValueError(f'source evidence {scope}/{table}/{rowid}: {error}') from error
                done+=len(rows)
        if all(manifest['last'][table]>=cutoff for table,cutoff in manifest['cutoffs'].items()):
            manifest['state']='complete'
            with _transaction(db):
                db.execute('UPDATE archive_build_manifest SET value_json=? WHERE key=?',(_json(manifest),'source_capture_v2_'+scope))
                db.execute('DELETE FROM archive_build_manifest WHERE key=?',('source_capture_v2_'+scope+'_failure',))
        missing=db.execute('SELECT COUNT(*) FROM archive_source_coverage_v2 WHERE scope=? AND input_hash IS NULL',(scope,)).fetchone()[0]
        return {'scope':scope,'source_rows_captured_now':done,'capture_state':manifest['state'],'received_keys_without_prepared_input':missing,'build_state':'building'}


def finish_coverage(path: Path) -> dict:
    """Account for source keys; unknown failures and missing prepared inputs block."""
    with _writer(path) as db:
        _tables(db)
        scopes={}
        blockers=[]
        for scope in SCOPES:
            saved=db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?',('source_capture_v2_'+scope,)).fetchone()
            source=db.execute('SELECT source_sha256,state FROM archive_sources_v2 WHERE scope=?',(scope,)).fetchone()
            if not saved or json.loads(saved[0]).get('state')!='complete' or not source or source[1]!='complete':
                blockers.append(scope+'_source_capture_incomplete'); continue
            n=db.execute('SELECT COUNT(*) FROM archive_source_coverage_v2 WHERE scope=?',(scope,)).fetchone()[0]
            missing=db.execute('SELECT COUNT(*) FROM archive_source_coverage_v2 c LEFT JOIN archive_input_progress p ON p.scope=c.scope AND p.stock_code=c.stock_code AND p.identity=c.identity WHERE c.scope=? AND (p.input_hash IS NULL OR p.input_hash<>c.input_hash)',(scope,)).fetchone()[0]
            unlinked=db.execute("SELECT COUNT(*) FROM archive_input_progress p WHERE p.scope=? AND p.progress_state<>'source_excluded' AND NOT EXISTS(SELECT 1 FROM archive_source_coverage_v2 c WHERE c.scope=p.scope AND c.stock_code=p.stock_code AND c.identity=p.identity)",(scope,)).fetchone()[0]
            if missing: blockers.append(scope+'_received_keys_without_prepared_input')
            if unlinked: blockers.append(scope+'_prepared_keys_without_raw_mapping')
            scopes[scope]={'prepared_sha256':source[0],'source_keys':n,'missing_inputs':missing,'unlinked_prepared':unlinked,'raw_fingerprint':json.loads(saved[0])['raw_fingerprint']}
        # Automatic review has a deliberately narrow evidence-based condition.
        # A preparation error with available title/summary/fulltext requires PC
        # repair; it must not be relabeled as an unavoidable source failure.
        with _transaction(db):
            for scope,owner,identity,input_hash,error,metadata_json in db.execute("SELECT p.scope,p.stock_code,p.identity,p.input_hash,p.error,c.metadata_json FROM archive_input_progress p JOIN archive_source_coverage_v2 c ON c.scope=p.scope AND c.stock_code=p.stock_code AND c.identity=p.identity WHERE p.progress_state='source_failed'"):
                metadata=json.loads(metadata_json)
                if str(metadata.get('title') or '').strip() or str(metadata.get('summary') or '').strip():
                    continue
                if scope==SEARCH and db.execute("SELECT 1 FROM archive_source_history_v2 WHERE scope=? AND source_table='news_article_body_snapshots' AND json_extract(payload_json,'$.provider')=? AND json_extract(payload_json,'$.office_id')=? AND json_extract(payload_json,'$.article_id')=? AND LENGTH(TRIM(COALESCE(json_extract(payload_json,'$.body_text'),'')))>0 LIMIT 1",(scope,metadata.get('provider'),metadata.get('office_id'),metadata.get('article_id'))).fetchone():
                    continue
                prepared=db.execute('SELECT body_json FROM archive_failures_v2 WHERE scope=? AND stock_code=? AND identity=?',(scope,owner,identity)).fetchone()
                if prepared and str(json.loads(prepared[0]).get('body_text') or '').strip():
                    continue
                if error=='기사 본문과 목록 요약을 가져오지 못했습니다':
                    db.execute('INSERT INTO archive_failure_review_v2 VALUES(?,?,?,?,?) ON CONFLICT(scope,stock_code,identity) DO NOTHING',(scope,owner,identity,input_hash,'raw title/summary unavailable; original preparation error preserved'))
            unresolved=db.execute("SELECT COUNT(*) FROM archive_input_progress p LEFT JOIN archive_failure_review_v2 r ON r.scope=p.scope AND r.stock_code=p.stock_code AND r.identity=p.identity WHERE p.progress_state='source_failed' AND (r.input_hash IS NULL OR r.input_hash<>p.input_hash)").fetchone()[0]
            if unresolved: blockers.append('source_failures_require_pc_repair_or_explicit_review')
            failures=db.execute("SELECT COUNT(*) FROM archive_input_progress WHERE progress_state='source_failed'").fetchone()[0]
            value={'schema':'historical-source-coverage/v2','state':'incomplete' if blockers else 'complete_with_source_failures' if failures else 'complete','scopes':scopes,'blockers':sorted(blockers)}
            db.execute("INSERT INTO archive_build_manifest VALUES('coverage_v2',?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",(_json(value),))
        return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--raw',type=Path)
    parser.add_argument('--scope',choices=SCOPES)
    parser.add_argument('--max-rows',type=int,default=0)
    parser.add_argument('--finish-coverage',action='store_true')
    args=parser.parse_args()
    if args.finish_coverage:
        value=finish_coverage(args.archive)
    else:
        if args.raw is None or args.scope is None:
            parser.error('--raw and --scope are required for capture')
        value=capture(args.archive,args.raw,args.scope,max_rows=args.max_rows)
    print(json.dumps(value,ensure_ascii=False))
