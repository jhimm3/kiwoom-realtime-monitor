from contextlib import closing
import json
import hashlib
import sqlite3
import unittest

from tests.unit import test_prepared_historical_archive_inputs as fixtures
from scripts import prepared_historical_archive_v2 as api
from scripts import prepared_historical_archive_inputs as inputs
from scripts.capture_historical_archive_sources_v2 import finish_coverage
from scripts.transition_historical_archive_execution import _apply, _apply_active_events, rollback


class ExecutionTransitionTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.InputGenerationTests('test_bad_parent_and_competing_writer_rejected')
        f.setUp();self.addCleanup(f.doCleanups);f.base();f.adopt()
        inputs.stage_exclusions(f.archive);inputs.complete_adoption(f.archive);finish_coverage(f.archive)
        self.f=f;self.db=sqlite3.connect(f.archive);self.addCleanup(self.db.close)
        self.before=dict(self.db.execute('SELECT key,value_json FROM archive_build_manifest'))
        self.digest=api.code_digest()
        self.data={t:list(self.db.execute('SELECT * FROM '+t)) for t in ('archive_sources_v2','archive_input_progress','archive_source_coverage_v2','archive_source_history_v2','archive_seed_roles','archive_failures_v2')}

    def apply(self,parent='test-components'):
        return _apply(self.db,parent,self.digest,'test-execution-v6','b'*64,self.f.root/'new-plan.json','c'*64)

    def test_only_manifests_change_and_rollback_restores_previous_generation(self):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');self.apply()
        self.assertEqual('test-execution-v6',api.report(self.db)['dataset_id'])
        gen=inputs.generation(self.db)
        old=json.loads(self.before[inputs.KEY])
        self.assertEqual(old['protected_roots'],gen['protected_roots'])
        self.assertEqual(old['base_sources'],gen['base_sources'])
        self.assertEqual(old['base_progress_root'],gen['base_progress_root'])
        self.assertEqual(self.data,{t:list(self.db.execute('SELECT * FROM '+t)) for t in self.data})
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');rollback(self.db,'test-execution-v6','b'*64)
        self.assertEqual(self.before,dict(self.db.execute('SELECT key,value_json FROM archive_build_manifest')))
        self.assertEqual('reverted',self.db.execute('SELECT state FROM archive_execution_transitions_v1').fetchone()[0])

    def test_middle_sql_failure_rolls_back_all_metadata_and_history(self):
        self.db.execute("CREATE TRIGGER fail_transition BEFORE UPDATE ON archive_build_manifest WHEN NEW.key='report' BEGIN SELECT RAISE(ABORT,'injected'); END")
        self.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError,'injected'):
            with self.db:
                self.db.execute('BEGIN IMMEDIATE');self.apply()
        self.assertEqual(self.before,dict(self.db.execute('SELECT key,value_json FROM archive_build_manifest')))
        self.assertIsNone(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='archive_execution_transitions_v1'").fetchone())

    def test_wrong_parent_or_started_finalization_cannot_transition(self):
        with self.assertRaisesRegex(ValueError,'parent construction mismatch'):
            with self.db:
                self.db.execute('BEGIN IMMEDIATE');self.apply('other')
        with self.db:
            self.db.execute("INSERT INTO archive_expected_targets_v2 VALUES('historical_backfill','005930','started','{}')")
        with self.assertRaisesRegex(ValueError,'finalized'):
            with self.db:
                self.db.execute('BEGIN IMMEDIATE');self.apply()
        self.assertEqual(self.before,dict(self.db.execute('SELECT key,value_json FROM archive_build_manifest')))

    def test_active_event_transition_preserves_cursor_and_input_rows(self):
        self.db.execute('CREATE TABLE archive_execution_transitions_v1(dataset_id TEXT PRIMARY KEY,parent_dataset TEXT NOT NULL,parent_digest TEXT NOT NULL,code_digest TEXT NOT NULL,old_report_json TEXT NOT NULL,old_generation_json TEXT NOT NULL,plan_path TEXT NOT NULL,plan_sha256 TEXT NOT NULL,state TEXT NOT NULL)')
        parent_report=json.dumps({'dataset_id':'test-parent','builder_code_digest':'f'*64})
        parent_generation=json.dumps({'dataset_id':'test-parent','code_digest':'f'*64})
        self.db.execute('INSERT INTO archive_execution_transitions_v1 VALUES(?,?,?,?,?,?,?,?,?)',
                        ('test-components','test-parent','f'*64,self.digest,parent_report,parent_generation,'old-plan.json','a'*64,'complete'))
        scopes={scope:{'last':None,'inputs':{'rows':0,'sha256':'0'*64},'targets':{'rows':0,'sha256':'0'*64}} for scope in api.SCOPES}
        self.db.execute('INSERT INTO archive_build_manifest VALUES(?,?)',
                        ('finalization_execution_v1',json.dumps({'schema':'historical-finalization-execution/v1','dataset_id':'test-components','code_digest':self.digest,'parent_digest':'f'*64,'plan_path':'old-plan.json','plan_sha256':'a'*64,'parent_dataset':'test-parent','scopes':scopes})))
        self.db.execute("INSERT INTO archive_build_manifest VALUES('events_v2',?)",
                        (json.dumps({'state':'building','next_ordinal':41,'target_count':100}),))
        self.db.commit()
        before_targets=list(self.db.execute('SELECT * FROM archive_targets_v2'))
        before_inputs=list(self.db.execute('SELECT * FROM archive_input_progress'))
        before_events=self.db.execute("SELECT value_json FROM archive_build_manifest WHERE key='events_v2'").fetchone()[0]
        plan1=self.f.root/'events-plan.json';content1=json.dumps({'schema':'historical-active-events-execution/v1','dataset_id':'test-events-v8','code_digest':'d'*64,'parent_dataset':'test-components','parent_digest':self.digest},sort_keys=True)
        plan1.write_text(content1,encoding='utf-8');hash1=hashlib.sha256(content1.encode()).hexdigest()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            _apply_active_events(self.db,'test-components',self.digest,'test-events-v8','d'*64,
                                 plan1,hash1)
        plan2=self.f.root/'events-plan-v8-1.json';content2=json.dumps({'schema':'historical-active-events-execution/v1','dataset_id':'test-events-v8-1','code_digest':'f'*64,'parent_dataset':'test-events-v8','parent_digest':'d'*64},sort_keys=True)
        plan2.write_text(content2,encoding='utf-8');hash2=hashlib.sha256(content2.encode()).hexdigest()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            _apply_active_events(self.db,'test-events-v8','d'*64,'test-events-v8-1','f'*64,
                                 plan2,hash2)
        self.assertEqual(before_targets,list(self.db.execute('SELECT * FROM archive_targets_v2')))
        self.assertEqual(before_inputs,list(self.db.execute('SELECT * FROM archive_input_progress')))
        self.assertEqual(before_events,self.db.execute("SELECT value_json FROM archive_build_manifest WHERE key='events_v2'").fetchone()[0])
        self.assertEqual(('test-events-v8-1','f'*64),
                         (api.report(self.db)['dataset_id'],api.report(self.db)['builder_code_digest']))
        self.assertEqual('complete',self.db.execute("SELECT state FROM archive_execution_transitions_v1 WHERE dataset_id='test-events-v8-1'").fetchone()[0])
        from scripts.transition_historical_archive_execution import _active_event_ancestors, finalization_lineage
        self.assertIn(('test-components',self.digest),_active_event_ancestors(
            self.db,api.report(self.db),stop_identity=('test-components',self.digest)))
        self.assertEqual('test-components',finalization_lineage(self.db)['dataset_id'])


if __name__=='__main__':unittest.main()
