from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import unittest

from scripts.audit_prepared_historical_archive_v2 import _target_sets, audit
from scripts.historical_archive_completion_gate import audit_and_seal, verified_publication
from scripts import prepared_historical_archive_v2 as api
from scripts.stage_prepared_historical_news_archive import _sha256
from tests.unit import test_historical_archive_completion_gate as fixtures


class TargetStreamTests(unittest.TestCase):
    def test_one_stream_matches_original_sets_and_detects_missing_changed_extra(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript('CREATE TABLE archive_expected_targets_v2(scope,stock_code,identity,targets_json,PRIMARY KEY(scope,stock_code,identity)); CREATE TABLE archive_targets_v2(scope,stock_code,identity,target_id,target_name,PRIMARY KEY(scope,stock_code,identity,target_id));')
            for i in range(1001):
                owner=f'{i:06d}'; expected={} if i==0 else {'b':'B','a':'A'}
                db.execute('INSERT INTO archive_expected_targets_v2 VALUES(?,?,?,?)',('scope',owner,'key',json.dumps(expected)))
                db.executemany('INSERT INTO archive_targets_v2 VALUES(?,?,?,?,?)',[('scope',owner,'key',k,v) for k,v in expected.items()])
            statements=[]; db.set_trace_callback(statements.append)
            self.assertEqual((2000,True),_target_sets(db))
            self.assertEqual(1,len(statements)); db.set_trace_callback(None)
            plan=db.execute('EXPLAIN QUERY PLAN '+statements[0]).fetchall()
            self.assertFalse(any('TEMP B-TREE' in row[3] for row in plan),plan)
            for mutation in ("DELETE FROM archive_targets_v2 WHERE stock_code='000001' AND target_id='a'", "UPDATE archive_targets_v2 SET target_name='wrong' WHERE stock_code='000001'", "INSERT INTO archive_targets_v2 VALUES('scope','000001','key','extra','Extra')"):
                db.execute('SAVEPOINT corruption'); db.execute(mutation)
                self.assertFalse(_target_sets(db)[1]); db.execute('ROLLBACK TO corruption'); db.execute('RELEASE corruption')


class IndependentVerifierTests(unittest.TestCase):
    def setUp(self):
        self.base=fixtures.CompletionGateTests('test_one_real_full_audit_and_checksum_reuse_without_second_audit')
        self.base.setUp(); self.addCleanup(self.base.doCleanups)
        self.root=self.base.fixture.root/'verifier'/'scripts'; self.root.mkdir(parents=True)
        self.code={'construction_digest':self.base.digest}
        repo=Path(__file__).resolve().parents[2]
        for kind,name in [('audit','audit_prepared_historical_archive_v2.py'),('seal','seal_prepared_historical_archive_v2.py')]:
            target=self.root/name; shutil.copyfile(repo/'scripts'/name,target)
            self.code[kind+'_path']=str(target)
            self.code[kind+'_sha256']=_sha256(target)

    def complete(self):
        return audit_and_seal(self.base.path,self.base.seed,self.base.receipt,'full-fixture-v2',self.base.digest,verification_code=self.code)

    def test_independent_verifier_seals_preserving_constructor_and_records_actual_gate(self):
        before=audit(self.base.path,seed=self.base.seed,full_integrity_check=True)
        result=self.complete()
        self.assertEqual(before,{k:v for k,v in result.items() if k not in ('publication_receipt','full_audit_owned_by_sealer','verification_code')})
        self.assertEqual(self.code,result['verification_code'])
        publication,gate=verified_publication(self.base.path,self.base.receipt,'full-fixture-v2',self.base.digest,verification_code=self.code)
        self.assertEqual(result['publication_receipt'],publication)
        self.assertEqual(self.code,gate['verification_code'])
        self.base.receipt.unlink()
        self.assertTrue(self.complete()['ready_for_seal'])

    def test_changed_verifier_or_constructor_binding_is_rejected_before_seal(self):
        self.code['construction_digest']='wrong'
        with self.assertRaisesRegex(ValueError,'construction binding'):self.complete()
        self.code['construction_digest']=self.base.digest
        Path(self.code['audit_path']).write_text('raise AssertionError("must not execute")')
        with self.assertRaisesRegex(ValueError,'source checksum'):self.complete()
        self.assertFalse(self.base.receipt.exists())

    def test_independent_verifier_preserves_real_failure_gate(self):
        with closing(sqlite3.connect(self.base.path)) as db,db:
            db.execute('DELETE FROM archive_targets_v2 WHERE rowid=(SELECT MIN(rowid) FROM archive_targets_v2)')
        with self.assertRaisesRegex(ValueError,'target_set_mismatch'):self.complete()
        self.assertFalse(self.base.receipt.exists())

    def test_seed_schema_is_read_once_per_table_and_values_still_verified(self):
        with closing(sqlite3.connect(self.base.seed)) as seed,closing(sqlite3.connect(self.base.path)) as db,seed,db:
            for i in range(3):
                payload={'collection':'preserved','owner':'owner','document_key':str(i),'updated_at':1.0,'document_json':'{}'}
                encoded=api._json(payload); digest=hashlib.sha256(encoded.encode()).hexdigest(); key=api._json(['preserved','owner',str(i)])
                seed.execute('INSERT INTO seed_rows VALUES(?,?,?,?,?)',('central_documents',key,encoded,digest,'support_only'))
                db.execute('INSERT INTO central_documents VALUES(?,?,?,?,?)',tuple(payload.values()))
                db.execute('INSERT INTO archive_seed_roles VALUES(?,?,?,?)',('central_documents',key,digest,'support_only'))
            seed.commit(); value=api.report(db); value.update(seed_rows=3,seed_sha256=_sha256(self.base.seed)); db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",(api._json(value),))
        # Trace the real auditor connection, without changing its read-only mode.
        import scripts.audit_prepared_historical_archive_v2 as module
        from unittest.mock import patch
        connect=sqlite3.connect; statements=[]
        def traced(*args,**kwargs):
            result=connect(*args,**kwargs); result.set_trace_callback(statements.append); return result
        with patch.object(module.sqlite3,'connect',side_effect=traced):
            result=audit(self.base.path,seed=self.base.seed,full_integrity_check=True)
        self.assertTrue(result['ready_for_seal'],result['blockers'])
        self.assertEqual(1,sum('PRAGMA table_info(central_documents)' in s for s in statements))
        with closing(connect(self.base.path)) as db,db:db.execute("UPDATE central_documents SET document_json='changed' WHERE collection='preserved' AND document_key='1'")
        self.assertIn('seed_row_payload_or_role_changed',audit(self.base.path,seed=self.base.seed,full_integrity_check=True)['blockers'])


if __name__=='__main__':unittest.main()
