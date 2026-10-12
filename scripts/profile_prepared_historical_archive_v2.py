"""Bounded PC-only archive capacity sample; output can never be published."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
for item in (ROOT,ROOT/'src'):
    if str(item) not in sys.path:
        sys.path.insert(0,str(item))

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from scripts.prepared_historical_archive_v2 import (
    SEARCH,MARKET,code_digest,initialize,stage,finalize_inputs,finalize_events,build_projection,
)
from scripts.stage_prepared_historical_news_archive import _sha256


def profile(search: Path, market: Path, output: Path, *, per_scope: int=512) -> dict:
    if not 32<=per_scope<=4096:
        raise ValueError('sample size must be 32..4096 per scope')
    output=output.resolve()
    if output.exists():
        raise FileExistsError('profile directory already exists')
    if shutil.disk_usage(output.parent).free<1024**3:
        raise ValueError('capacity sample needs 1GiB free')
    output.mkdir()
    prepared=output/'sample-prepared.sqlite3'
    archive=output/'sample-archive.building.sqlite3'
    counts={}
    samples={}
    source_files={}
    with closing(sqlite3.connect(prepared)) as sample:
        sample.execute('CREATE TABLE prepared_news(scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,state TEXT NOT NULL,article_json TEXT NOT NULL,body_json TEXT NOT NULL,rules_json TEXT NOT NULL,error TEXT NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(scope,stock_code,identity))')
        for scope,path in ((SEARCH,search),(MARKET,market)):
            path=path.resolve(strict=True)
            source_files[scope]={'path':str(path),'bytes':path.stat().st_size,'mtime_ns':path.stat().st_mtime_ns}
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source:
                source.execute('PRAGMA query_only=ON'); source.execute('BEGIN')
                max_row=source.execute('SELECT MAX(rowid) FROM prepared_news').fetchone()[0]
                # Existing trigger-maintained counts avoid an extra full scan.
                rows=source.execute("SELECT bucket,n FROM monitor_counts WHERE source='prepared_news' AND dimension='state'").fetchall()
                counts[scope]=dict(rows)
                seen=set()
                for partition in range(8):
                    take=(per_scope+7)//8
                    selected=source.execute('SELECT * FROM prepared_news WHERE rowid>=? ORDER BY rowid LIMIT ?',(1+(max_row*partition)//8,take)).fetchall()
                    for row in selected:
                        key=row[:3]
                        if key in seen or row[0]!=scope or row[3]!='ready':
                            continue
                        seen.add(key)
                        sample.execute('INSERT INTO prepared_news VALUES(?,?,?,?,?,?,?,?,?)',row)
                samples[scope]=len(seen)
                if not seen:
                    raise ValueError('scope sample has no ready rows')
                source.rollback()
        sample.commit()
    SQLiteQueryStore(archive).initialize()
    with closing(sqlite3.connect(archive)) as db,db:
        db.execute('CREATE TABLE archive_build_manifest(key TEXT PRIMARY KEY,value_json TEXT)')
        db.execute("INSERT INTO archive_build_manifest VALUES('report',?)",(json.dumps({'build_state':'building','profile_only':True,'seed_rows':0}),))
        db.execute('CREATE TABLE archive_seed_roles(table_name TEXT,row_key TEXT,payload_hash TEXT,role TEXT,PRIMARY KEY(table_name,row_key))')
    initialize(archive,dataset_id='capacity-sample-only')
    empty_bytes=archive.stat().st_size
    digest=_sha256(prepared)
    timings={}
    scope_sizes={}
    for scope in (SEARCH,MARKET):
        started=time.monotonic(); before=archive.stat().st_size
        stage(archive,prepared,scope,digest)
        result=finalize_inputs(archive,prepared,scope,digest)
        timings[scope]={'seconds':round(time.monotonic()-started,3),**result}
        scope_sizes[scope]=max(0,archive.stat().st_size-before)
    started=time.monotonic()
    events=finalize_events(archive)
    if events['blocked']:
        raise ValueError('sample event ambiguity needs investigation; no capacity approval')
    build_projection(archive)
    timings['events_projection_seconds']=round(time.monotonic()-started,3)
    shared_bytes=max(0,archive.stat().st_size-empty_bytes-sum(scope_sizes.values()))
    estimates={scope:round((scope_sizes[scope]/samples[scope]+shared_bytes/sum(samples.values()))*counts[scope].get('ready',0)) for scope in (SEARCH,MARKET)}
    frozen_bytes=sum(v['bytes'] for v in source_files.values())
    archive_estimate=sum(estimates.values())
    # Conservative staging/index/revision/history headroom, not a measured peak.
    reserve=max(20*1024**3,round(archive_estimate*.30))
    free=shutil.disk_usage(output).free
    result={'profile_only':True,'publishable':False,'builder_code_digest':code_digest(),
            'sample_prepared_sha256':digest,
            'method':'8 rowid strata per scope; maintained counts; empty seed; canonical/body/target/event/compact projection sample',
            'sources':source_files,'maintained_state_counts':counts,'sample_rows':samples,
            'sample_archive_bytes':archive.stat().st_size,'estimated_new_archive_bytes_by_scope':estimates,
            'frozen_prepared_bytes':frozen_bytes,'reserve_bytes_assumed':reserve,
            'estimated_required_bytes_excluding_fresh_seed_and_raw_exports':frozen_bytes+archive_estimate+reserve,
            'free_bytes':free,'space_sufficient_for_estimate':free>=frozen_bytes+archive_estimate+reserve,
            'timings':timings,'limitations':['not a random statistical sample','seed and raw history/provenance export not included','raw BODY versions unverified in this profile','not an end-to-end collection count or ETA','reserve is an assumption; full indexes/temp/WAL peak unmeasured']}
    (output/'profile.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--search',type=Path,required=True)
    parser.add_argument('--market',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--per-scope',type=int,default=512)
    args=parser.parse_args()
    print(json.dumps(profile(args.search,args.market,args.output,per_scope=args.per_scope),ensure_ascii=True))
