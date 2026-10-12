"""Copy one sealed PC archive to a new NAS version, with resumable verification.

This publishes files only. It never changes active configuration, restarts the
server, opens PostgreSQL, or starts news processing.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from typing import Callable

ROOT=Path(__file__).resolve().parents[1]
for item in (ROOT,ROOT/'src'):
    if str(item) not in sys.path:
        sys.path.insert(0,str(item))

from scripts.stage_prepared_historical_news_archive import _sha256
from scripts.prepared_historical_archive_v2 import _exclusive_lock


def publish(receipt: Path, destination_root: Path, *,
            progress: Callable[[dict],None] | None=None) -> dict:
    receipt=receipt.resolve(strict=True)
    record=json.loads(receipt.read_text(encoding='utf-8'))
    dataset=record.get('dataset_id','')
    if record.get('schema')!='historical-news-publication/v2' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',dataset):
        raise ValueError('invalid sealed publication receipt')
    source=Path(record['archive']).resolve(strict=True)
    if source.stat().st_size!=record.get('bytes') or _sha256(source)!=record.get('sha256'):
        raise ValueError('sealed source differs from publication receipt')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as db:
        value=json.loads(db.execute("SELECT value_json FROM archive_build_manifest WHERE key='report'").fetchone()[0])
        if value.get('build_state')!='sealed' or value.get('profile_only') or value.get('dataset_id')!=dataset or not value.get('seal_gate',{}).get('ready_for_seal'):
            raise ValueError('source is not a verified serving archive')
    root=destination_root.resolve(strict=True)
    if root.name!='historical-news-archives' or any(part.lower() in {'.env','postgres-data'} for part in root.parts):
        raise ValueError('destination must be the explicitly selected historical-news-archives directory')
    version=(root/dataset).resolve()
    version.relative_to(root)
    if source.is_relative_to(version):
        raise ValueError('source and destination must differ')
    version.mkdir(exist_ok=True)
    target=version/'archive.sqlite3'
    partial=version/'archive.sqlite3.partial'
    metadata=version/'transfer.json'
    expected={'dataset_id':dataset,'bytes':record['bytes'],'sha256':record['sha256']}
    publication=version/'publication.json'
    def verified_receipt():
        value={**expected,'schema':record['schema'],'file_verified':True,'server_activated':False}
        if publication.exists():
            if json.loads(publication.read_text(encoding='utf-8'))!=value:
                raise ValueError('existing publication receipt differs')
        else:
            with publication.open('x',encoding='utf-8') as output:
                json.dump(value,output,indent=2)
        target.chmod(stat.S_IREAD)
        return {**expected,'state':'file_verified','archive':str(target),'server_activated':False}
    with _exclusive_lock(target):
        if target.exists():
            if target.stat().st_size!=expected['bytes'] or _sha256(target)!=expected['sha256']:
                raise FileExistsError('existing version differs; never overwrite it')
            return verified_receipt()
        if metadata.exists():
            if json.loads(metadata.read_text(encoding='utf-8'))!=expected:
                raise ValueError('partial copy belongs to another input')
        else:
            if partial.exists():
                raise ValueError('unowned partial copy; do not overwrite')
            with metadata.open('x',encoding='utf-8') as output:
                json.dump(expected,output)
        offset=partial.stat().st_size if partial.exists() else 0
        if offset>expected['bytes']:
            raise ValueError('partial copy exceeds source size')
        if offset:
            with source.open('rb') as original,partial.open('rb') as prior:
                remaining=offset
                while remaining:
                    take=min(8*1024**2,remaining)
                    if original.read(take)!=prior.read(take):
                        raise ValueError('partial copy prefix is invalid; refuse unsafe resume')
                    remaining-=take
        with source.open('rb') as original,partial.open('ab') as output:
            original.seek(offset)
            previous=offset
            while block:=original.read(8*1024**2):
                output.write(block); offset+=len(block)
                if progress and offset-previous>=256*1024**2:
                    progress({'state':'copying','copied_bytes':offset,'total_bytes':expected['bytes']})
                    previous=offset
            output.flush(); os.fsync(output.fileno())
        if partial.stat().st_size!=expected['bytes'] or _sha256(partial)!=expected['sha256']:
            raise ValueError('NAS partial file hash mismatch; version remains inactive')
        # Windows rename is atomic and refuses an existing destination (also
        # on a mapped SMB share). POSIX rename can overwrite, so use link there.
        # Unsupported filesystems retain the verified partial for recovery.
        if os.name=='nt':
            os.rename(partial,target)
        else:
            os.link(partial,target)
            partial.unlink()
        return verified_receipt()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--destination-root',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(publish(args.receipt,args.destination_root,
                             progress=lambda value:print(json.dumps(value),flush=True)),ensure_ascii=False))
