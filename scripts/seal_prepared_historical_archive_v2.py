"""Seal a fully verified PC v2 archive and emit its external publication hash."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
import sqlite3
from contextlib import closing
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
for item in (ROOT,ROOT/'src'):
    if str(item) not in sys.path:
        sys.path.insert(0,str(item))

from scripts.prepared_historical_archive_v2 import _writer,_exclusive_lock,_transaction,report,_json
from scripts.audit_prepared_historical_archive_v2 import audit
from scripts.stage_prepared_historical_news_archive import _sha256


def seal(path: Path,seed: Path,receipt: Path) -> dict:
    path=path.resolve(strict=True); seed=seed.resolve(strict=True); receipt=receipt.resolve()
    if path==seed or receipt in (path,seed) or receipt.exists():
        raise ValueError('sealing inputs and new external receipt must be distinct')
    if not receipt.parent.is_dir():
        raise FileNotFoundError('receipt parent missing')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as existing:
        already_sealed=report(existing).get('build_state')=='sealed'
    if already_sealed:
        # Recover a receipt creation failure without reopening the sealed DB
        # for writes or trusting the sealed boolean as the entire proof.
        with _exclusive_lock(path):
            gate=audit(path,seed=seed,full_integrity_check=True,allow_sealed=True)
            if not gate['ready_for_seal']:
                raise ValueError('archive receipt recovery blockers: '+', '.join(gate['blockers']))
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                value=report(db)
    else:
        with _writer(path) as db:
            gate=audit(path,seed=seed,full_integrity_check=True)
            if not gate['ready_for_seal']:
                raise ValueError('archive publication blockers: '+', '.join(gate['blockers']))
            if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]!=0:
                raise ValueError('archive checkpoint busy')
            if db.execute('PRAGMA journal_mode=DELETE').fetchone()[0].lower()!='delete':
                raise ValueError('archive is not a standalone rollback-journal file')
            value=report(db)
            coverage=json.loads(db.execute("SELECT value_json FROM archive_build_manifest WHERE key='coverage_v2'").fetchone()[0])
            value.update(build_state='sealed',coverage_state=coverage['state'],sealed_at=time.time(),seal_gate=gate)
            with _transaction(db):
                db.execute('UPDATE archive_builder_v2 SET owner=NULL WHERE id=1')
                db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",(_json(value),))
    for suffix in ('-wal','-journal'):
        extra=Path(str(path)+suffix)
        if extra.exists() and extra.stat().st_size:
            raise ValueError('standalone archive still has journal content; do not publish')
    path.chmod(stat.S_IREAD)
    value={'schema':'historical-news-publication/v2','dataset_id':value['dataset_id'],
           'archive':str(path),'bytes':path.stat().st_size,'sha256':_sha256(path),
           'coverage_state':value['coverage_state'],'sealed_at':value['sealed_at'],
           'nas_publication_completed':False}
    # Exclusive creation preserves a prior receipt if another caller raced.
    with receipt.open('x',encoding='utf-8') as output:
        json.dump(value,output,ensure_ascii=False,indent=2)
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--seed',type=Path,required=True)
    parser.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(seal(args.archive,args.seed,args.receipt),ensure_ascii=False))
