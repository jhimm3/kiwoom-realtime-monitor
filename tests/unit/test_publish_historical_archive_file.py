from contextlib import closing
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import publish_historical_archive_file as module
from scripts.stage_prepared_historical_news_archive import _sha256


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.source=self.root/'sealed.sqlite3'
        self.destination=self.root/'historical-news-archives';self.destination.mkdir()
        self.version=self.destination/'test-transfer';self.version.mkdir()
        self.target=self.version/'archive.sqlite3';self.partial=self.version/'archive.sqlite3.partial'
        with closing(sqlite3.connect(self.source)) as db,db:
            db.execute('CREATE TABLE archive_build_manifest(key TEXT PRIMARY KEY,value_json TEXT)')
            report={'dataset_id':'test-transfer','build_state':'sealed','seal_gate':{'ready_for_seal':True}}
            db.execute('INSERT INTO archive_build_manifest VALUES(?,?)',('report',json.dumps(report)))
            db.execute('CREATE TABLE payload(data BLOB)')
            for _ in range(3):db.execute('INSERT INTO payload VALUES(?)',(b'news'*10000,))
        self.expected={'dataset_id':'test-transfer','bytes':self.source.stat().st_size,'sha256':_sha256(self.source)}
        self.receipt=self.root/'sealed.json'
        self.receipt.write_text(json.dumps({**self.expected,'schema':'historical-news-publication/v2','archive':str(self.source)}),encoding='utf-8')
        self.addCleanup(lambda:self.target.chmod(stat.S_IWRITE) if self.target.exists() else None)

    def run_publish(self, **kwargs):
        return module.publish(self.receipt,self.destination,**kwargs)

    def tracked(self, action):
        opened=Path.open;transferred=[]
        class Stream:
            def __init__(self,inner):self.inner=inner
            def __enter__(self):self.inner.__enter__();return self
            def __exit__(self,*args):return self.inner.__exit__(*args)
            def read(self,n=-1):
                value=self.inner.read(n);transferred.append(len(value));return value
            def __getattr__(self,name):return getattr(self.inner,name)
        def opening(path,*args,**kwargs):
            stream=opened(path,*args,**kwargs)
            return Stream(stream) if path==self.source and args and args[0]=='rb' else stream
        with patch.object(Path,'open',opening):result=action()
        return result,sum(transferred)

    def test_new_copy_and_resume_read_source_once_and_match_all_bytes(self):
        for offset in (0,37,self.expected['bytes']):
            if self.target.exists():self.target.chmod(stat.S_IWRITE);self.target.unlink()
            publication=self.version/'publication.json';publication.unlink(missing_ok=True)
            (self.version/'transfer.json').write_text(json.dumps(self.expected),encoding='utf-8')
            if offset:self.partial.write_bytes(self.source.read_bytes()[:offset])
            result,read=self.tracked(self.run_publish)
            self.assertEqual(self.expected['bytes'],read)
            self.assertEqual('file_verified',result['state'])
            self.assertEqual(self.source.read_bytes(),self.target.read_bytes())
            self.assertFalse(self.partial.exists())

    def test_incomplete_prefix_corruption_refused_before_append(self):
        (self.version/'transfer.json').write_text(json.dumps(self.expected),encoding='utf-8')
        self.partial.write_bytes(b'wrong bytes')
        with self.assertRaisesRegex(ValueError,'prefix is invalid'):self.run_publish()
        self.assertEqual(b'wrong bytes',self.partial.read_bytes())
        self.assertFalse(self.target.exists())

    def test_midcopy_failure_and_read_ahead_resume(self):
        def stop(_):raise RuntimeError('injected stop')
        with patch.object(module,'BLOCK_BYTES',4096),patch.object(module,'PROGRESS_BYTES',4096):
            with self.assertRaisesRegex(RuntimeError,'injected stop'):self.run_publish(progress=stop)
        self.assertTrue(self.partial.exists());self.assertFalse(self.target.exists())
        self.assertLess(self.partial.stat().st_size,self.expected['bytes'])
        self.run_publish();self.assertEqual(self.expected['sha256'],_sha256(self.target))

    def test_source_mutation_even_if_read_ahead_has_old_bytes_is_rejected(self):
        changed=False
        def mutate(_):
            nonlocal changed
            if not changed:
                changed=True
                with self.source.open('r+b') as f:f.seek(4096);f.write(b'CHANGED')
        with patch.object(module,'BLOCK_BYTES',4096),patch.object(module,'PROGRESS_BYTES',4096):
            with self.assertRaisesRegex(ValueError,'source differs|source changed'):
                self.run_publish(progress=mutate)
        self.assertFalse(self.target.exists())

    def fake_ssh(self, args, **kwargs):
        marker=next(self.version.glob('checksum-location-*'))
        token=marker.read_text().strip()
        path=self.partial if self.partial.exists() else self.target
        meta=str(path.stat().st_size)+':123:456'
        self.assertIn('BatchMode=yes',args);self.assertNotIn('sudo',args[-1])
        return SimpleNamespace(returncode=0,stdout=token+'\n'+meta+'\n'+_sha256(path)+'  /mapped/file\n'+meta+'\n')

    def test_remote_hash_full_prefix_and_final_without_smb_hash_calls(self):
        (self.version/'transfer.json').write_text(json.dumps(self.expected),encoding='utf-8')
        self.partial.write_bytes(self.source.read_bytes()[:123])
        callback=module.NASLocalChecksum('user@nas','/volume1/shared/historical-news-archives')
        with patch.object(module.subprocess,'run',side_effect=self.fake_ssh) as remote,patch.object(module,'_sha256',side_effect=AssertionError('SMB hash must not run')):
            result,read=self.tracked(lambda:self.run_publish(nas_checksum=callback))
        self.assertEqual(2,remote.call_count)
        self.assertEqual(self.expected['bytes'],read)
        self.assertEqual('file_verified',result['state'])
        self.assertFalse(list(self.version.glob('checksum-location-*')))

    def test_remote_failure_fallback_and_mapping_rejection(self):
        callback=module.NASLocalChecksum('user@nas',"/volume1/a quoted ' root/historical-news-archives")
        events=[]
        with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=255,stdout='')):
            self.run_publish(nas_checksum=callback,progress=events.append)
        self.assertTrue(any(e['state']=='checksum_fallback' for e in events))
        with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='wrong mapping\n')):
            with self.assertRaisesRegex(ValueError,'mapping marker'):self.run_publish(nas_checksum=callback)
        self.assertFalse(list(self.version.glob('checksum-location-*')))

    def test_stored_corruption_and_version_metadata_ownership(self):
        self.run_publish();self.target.chmod(stat.S_IWRITE)
        with self.target.open('r+b') as f:f.seek(100);f.write(b'X')
        with self.assertRaisesRegex(FileExistsError,'never overwrite'):self.run_publish()
        self.target.unlink();(self.version/'transfer.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'another input'):self.run_publish()

    def test_ssh_options_and_root_are_not_shell_injection(self):
        for host,root in (('-oProxyCommand=bad','/a/historical-news-archives'),('user@nas','/a/../historical-news-archives'),('user@nas','/a\n/historical-news-archives')):
            with self.assertRaises(ValueError):module.NASLocalChecksum(host,root)


if __name__=='__main__':unittest.main()
