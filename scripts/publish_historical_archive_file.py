"""Copy one sealed PC archive to a new NAS version, with resumable verification.

This publishes files only. It never changes active configuration, restarts the
server, opens PostgreSQL, or starts news processing.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import subprocess
import shlex
import time
import uuid
from typing import Callable

ROOT=Path(__file__).resolve().parents[1]
for item in (ROOT,ROOT/'src'):
    if str(item) not in sys.path:
        sys.path.insert(0,str(item))

from scripts.stage_prepared_historical_news_archive import _sha256
from scripts.prepared_historical_archive_v2 import _exclusive_lock

BLOCK_BYTES = 8*1024**2
PROGRESS_BYTES = 256*1024**2


def _identity(path):
    value=path.stat()
    return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)


class NASLocalChecksum:
    """Optional read-only SSH checksum; marker proves the selected SMB mapping.

    No sudo, database access, server restart or remote file writes are used.
    Failure falls back to the existing SMB read verification; mapping mismatch
    is a hard error. Both paths still verify all stored file bytes.
    """
    def __init__(self, host: str, remote_root: str, *, timeout: int=3600):
        if not re.fullmatch(r'[A-Za-z0-9_.]+@[A-Za-z0-9_.:-]+',host):
            raise ValueError('explicit user@host SSH destination required')
        if (not remote_root.startswith('/') or remote_root.rstrip('/').split('/')[-1]!='historical-news-archives'
                or any(c in remote_root for c in '\x00\r\n')
                or any(p in ('.','..','.env','postgres-data') for p in remote_root.split('/'))):
            raise ValueError('explicit safe NAS historical-news-archives root required')
        if type(timeout) is not int or timeout<1:
            raise ValueError('invalid NAS checksum timeout')
        self.host,self.root,self.timeout=host,remote_root.rstrip('/'),timeout

    def __call__(self, path: Path, progress=None) -> str:
        dataset=path.parent.name
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',dataset) or path.name not in ('archive.sqlite3','archive.sqlite3.partial'):
            raise ValueError('invalid NAS checksum file identity')
        token=uuid.uuid4().hex
        marker=path.parent/('checksum-location-'+token+'.txt')
        remote=self.root+'/'+dataset+'/'+path.name
        remote_marker=self.root+'/'+dataset+'/'+marker.name
        with marker.open('x',encoding='ascii',newline='') as output:
            output.write(token+'\n')
        stat_command='stat -c '+shlex.quote('%s:%Y:%i')+' -- '+shlex.quote(remote)
        command=('cat -- '+shlex.quote(remote_marker)+' && '+stat_command+
                 ' && sha256sum -- '+shlex.quote(remote)+' && '+stat_command)
        # Large cold files must not time out and then repeat a full SMB read
        # merely because they exceeded the small-file default time budget.
        timeout=max(self.timeout,300+path.stat().st_size//(8*1024**2))
        try:
            try:
                result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15',self.host,command],
                                      capture_output=True,text=True,encoding='utf-8',errors='strict',timeout=timeout)
            except (OSError,subprocess.TimeoutExpired,UnicodeError) as error:
                if progress:progress({'state':'checksum_fallback','method':'smb_full_read','reason':type(error).__name__})
                return _sha256(path)
            if result.returncode:
                if progress:progress({'state':'checksum_fallback','method':'smb_full_read','reason':'ssh_failed','exit_code':result.returncode})
                return _sha256(path)
            lines=result.stdout.splitlines()
            if len(lines)!=4 or lines[0]!=token:
                raise ValueError('NAS checksum mapping marker mismatch')
            if (not re.fullmatch(r'[0-9]+:[0-9]+:[0-9]+',lines[1]) or lines[1]!=lines[3]
                    or int(lines[1].split(':')[0])!=path.stat().st_size):
                raise ValueError('NAS checksum file changed or mapping size differs')
            hashed=lines[2].split(' ',1)[0]
            if not re.fullmatch('[0-9a-f]{64}',hashed):
                raise ValueError('invalid NAS checksum response')
            return hashed
        finally:
            marker.unlink(missing_ok=True)


def _copy_and_hash(source, partial, offset, expected, progress, prefix_digest=None):
    """One source pass, bounded read-ahead; prefix+suffix share one SHA stream."""
    if offset and prefix_digest is None:
        raise ValueError('verified resume prefix digest required')
    digest=prefix_digest.copy() if prefix_digest is not None else hashlib.sha256()
    with source.open('rb') as original,partial.open('ab') as output,ThreadPoolExecutor(max_workers=1) as reader:
        original.seek(offset)
        previous=offset
        pending=reader.submit(original.read,BLOCK_BYTES)
        while block:=pending.result():
            digest.update(block)
            # Only one future is outstanding; at most two blocks are resident.
            pending=reader.submit(original.read,BLOCK_BYTES)
            output.write(block);offset+=len(block)
            if offset>expected['bytes']:
                raise ValueError('source grew during copy')
            if progress and offset-previous>=PROGRESS_BYTES:
                progress({'state':'copying','copied_bytes':offset,'total_bytes':expected['bytes']})
                previous=offset
        output.flush();os.fsync(output.fileno())
    if offset!=expected['bytes'] or digest.hexdigest()!=expected['sha256']:
        raise ValueError('sealed source differs from publication receipt')
    return offset


def publish(receipt: Path, destination_root: Path, *,
            progress: Callable[[dict],None] | None=None,
            nas_checksum: NASLocalChecksum | None=None) -> dict:
    receipt=receipt.resolve(strict=True)
    record=json.loads(receipt.read_text(encoding='utf-8'))
    dataset=record.get('dataset_id','')
    if record.get('schema')!='historical-news-publication/v2' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',dataset):
        raise ValueError('invalid sealed publication receipt')
    source=Path(record['archive']).resolve(strict=True)
    source_identity=_identity(source)
    if (source.stat().st_size!=record.get('bytes') or type(record.get('bytes')) is not int
            or record['bytes']<=0 or not re.fullmatch('[0-9a-f]{64}',str(record.get('sha256','')))):
        raise ValueError('sealed source differs from publication receipt')
    for suffix in ('-wal','-journal'):
        journal=Path(str(source)+suffix)
        if journal.exists() and journal.stat().st_size:
            raise ValueError('sealed source has journal content')
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
    for path in (target,partial,metadata,publication,target.with_name(target.name+'.builder.lock')):
        if path.is_symlink() or not path.resolve().is_relative_to(version):
            raise ValueError('publication file escaped the selected version')
    def stored_hash(path, stage):
        if progress:progress({'state':'verifying','stage':stage,'method':'nas_local_ssh' if nas_checksum else 'smb_full_read','total_bytes':path.stat().st_size})
        started=time.monotonic()
        result=nas_checksum(path,progress) if nas_checksum else _sha256(path)
        if progress:progress({'state':'verification_complete','stage':stage,'elapsed_seconds':round(time.monotonic()-started,3)})
        return result
    def unchanged_source():
        if _identity(source)!=source_identity:
            raise ValueError('sealed source changed during publication')
        for suffix in ('-wal','-journal'):
            extra=Path(str(source)+suffix)
            if extra.exists() and extra.stat().st_size:
                raise ValueError('sealed source has journal content')
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
            if _sha256(source)!=expected['sha256']:
                raise ValueError('sealed source differs from publication receipt')
            if target.stat().st_size!=expected['bytes'] or stored_hash(target,'existing_version')!=expected['sha256']:
                raise FileExistsError('existing version differs; never overwrite it')
            unchanged_source()
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
        if offset==expected['bytes']:
            if _sha256(source)!=expected['sha256']:
                raise ValueError('sealed source differs from publication receipt')
        else:
            if offset:
                # Hash source prefix before appending; stored prefix is checked
                # locally on NAS when configured, rather than downloaded to PC.
                prefix=hashlib.sha256()
                with source.open('rb') as original:
                    remaining=offset
                    while remaining:
                        block=original.read(min(BLOCK_BYTES,remaining))
                        if not block:raise ValueError('source ended inside resumed prefix')
                        prefix.update(block);remaining-=len(block)
                if stored_hash(partial,'resume_prefix')!=prefix.hexdigest():
                    raise ValueError('partial copy prefix is invalid; refuse unsafe resume')
            _copy_and_hash(source,partial,offset,expected,progress,prefix if offset else None)
        unchanged_source()
        if partial.stat().st_size!=expected['bytes'] or stored_hash(partial,'complete_file')!=expected['sha256']:
            raise ValueError('NAS partial file hash mismatch; version remains inactive')
        unchanged_source()
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
    parser.add_argument('--nas-ssh',help='Optional key-authenticated user@host for NAS-local checksums')
    parser.add_argument('--nas-directory-root',help='Matching NAS absolute historical-news-archives path')
    parser.add_argument('--checksum-timeout',type=int,default=3600)
    args=parser.parse_args()
    if bool(args.nas_ssh)!=bool(args.nas_directory_root):parser.error('both NAS checksum options are required together')
    checksum=NASLocalChecksum(args.nas_ssh,args.nas_directory_root,timeout=args.checksum_timeout) if args.nas_ssh else None
    print(json.dumps(publish(args.receipt,args.destination_root,
                             progress=lambda value:print(json.dumps(value),flush=True),nas_checksum=checksum),ensure_ascii=False))
