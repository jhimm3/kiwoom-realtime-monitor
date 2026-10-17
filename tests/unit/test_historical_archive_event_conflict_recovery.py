from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from scripts import prepared_historical_archive_v2 as api
from scripts import prepared_historical_archive_inputs as inputs
from scripts.transition_historical_archive_execution import _apply_active_events
from tests.unit import test_prepared_historical_archive_v2 as fixtures
from kiwoom_monitor.application.news_rules import SupplyContractRuleResult, rule_input_hash
from kiwoom_monitor.central_server.database_news_revisions import _save_sqlite_news_event


class EventConflictRecoveryTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.ArchiveV2Tests('test_two_scopes_multiple_targets_one_body_and_replay_no_jobs')
        f.setUp();self.addCleanup(f.doCleanups);self.f=f
        f.add(api.SEARCH,'a-completed')
        f.add(api.SEARCH,'b-conflict',title='삼성전자, 데이터센터와 100억원 공급계약 체결')
        f.add(api.SEARCH,'c-child')
        f.add(api.MARKET,'z-market',targets=[['GLOBAL','시황']])
        digest=api._sha256(f.prepared)
        for scope in api.SCOPES:
            api.stage(f.archive,f.prepared,scope,digest)
            api.finalize_inputs(f.archive,f.prepared,scope,digest)
        with closing(sqlite3.connect(f.archive)) as db,db:
            self.key=[api.SEARCH,'005930','b-conflict','005930']
            target=api._review_rows(db,"SELECT * FROM archive_targets_v2 WHERE identity='b-conflict'")[0]
            rule=json.loads(target['computed_json'])['rule_result'];self.assertIsNotNone(rule)
            typed=SupplyContractRuleResult(**rule)
            article=api._review_one(db,'central_news_article_revisions','article_revision_id',target['article_revision_id'])
            body=api._review_one(db,'central_news_body_revisions','body_revision_id',target['body_revision_id'])
            first=None
            for index in range(9):
                a={**article,'article_revision_id':'seed-article-'+str(index),'identity':'seed-'+str(index)}
                b={**body,'body_revision_id':'seed-body-'+str(index),'article_revision_id':a['article_revision_id']}
                self.insert(db,'central_news_article_revisions',a)
                self.insert(db,'central_news_body_revisions',b)
                if first is None:
                    revision=_save_sqlite_news_event(db,{'stock_code':'005930','article_revision_id':a['article_revision_id'],
                        'body_revision_id':b['body_revision_id'],'rule_version':typed.rule_version,
                        'input_hash':rule_input_hash(a['article_revision_id'],b['body_revision_id'],typed),'result':rule})
                    first=api._review_one(db,'central_news_event_revisions','event_revision_id',revision)
                    member=api._review_rows(db,'SELECT * FROM central_news_event_membership_revisions WHERE event_revision_id=?',(revision,))[0]
                else:
                    e={**first,'event_revision_id':'seed-event-'+str(index),'event_id':'event-'+str(index),
                       'article_revision_id':a['article_revision_id'],'body_revision_id':b['body_revision_id'],
                       'input_hash':rule_input_hash(a['article_revision_id'],b['body_revision_id'],typed)}
                    m={**member,'membership_revision_id':'seed-member-'+str(index),'event_id':e['event_id'],
                       'event_revision_id':e['event_revision_id'],'article_revision_id':a['article_revision_id'],'body_revision_id':b['body_revision_id']}
                    self.insert(db,'central_news_event_revisions',e);self.insert(db,'central_news_event_membership_revisions',m)
                    if index in (1,2):
                        newer={**e,'event_revision_id':'seed-newer-'+str(index),'event_key':'different-key' if index==1 else None,
                               'amount_won':882100000000 if index==1 else None,'input_hash':'different-input-'+str(index),'revision_of':e['event_revision_id']}
                        self.insert(db,'central_news_event_revisions',newer)
            db.execute("UPDATE central_news_article_revisions SET revision_of=? WHERE identity='c-child'",(article['article_revision_id'],))
            self.old_events=api._review_rows(db,'SELECT * FROM central_news_event_revisions ORDER BY accepted_sequence')
            self.old_members=api._review_rows(db,'SELECT * FROM central_news_event_membership_revisions ORDER BY accepted_sequence')
            self.old_articles=api._review_rows(db,'SELECT * FROM central_news_article_revisions ORDER BY accepted_sequence')
            self.old_bodies=api._review_rows(db,'SELECT * FROM central_news_body_revisions ORDER BY accepted_sequence')
        result=api.finalize_events(f.archive)
        self.assertEqual('ambiguous_event_key',result['blocked']['reason'])
        with closing(sqlite3.connect(f.archive)) as db,db:
            self.before=api._event_conflict_snapshot(db,self.key)
            self.assertEqual(9,len(self.before['candidate_event_ids']))
            self.assertEqual(0,db.execute("SELECT ready FROM archive_event_ready_v2 WHERE identity='c-child'").fetchone()[0])
            current=api.report(db)
            db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",(api._json({**current,'dataset_id':'parent-review','builder_code_digest':'a'*64}),))
            inputs._save(db,{'dataset_id':'parent-review','code_digest':'a'*64,'state':'complete',
                             'plan_path':str(f.root/'parent-plan.json'),'plan_sha256':'b'*64})
            db.execute('CREATE TABLE archive_execution_transitions_v1(dataset_id TEXT PRIMARY KEY,parent_dataset TEXT NOT NULL,parent_digest TEXT NOT NULL,code_digest TEXT NOT NULL,old_report_json TEXT NOT NULL,old_generation_json TEXT NOT NULL,plan_path TEXT NOT NULL,plan_sha256 TEXT NOT NULL,state TEXT NOT NULL)')
        self.evidence=f.root/'evidence.json';self.decision_path=f.root/'decision.json'
        self.write_evidence()
        api.create_event_conflict_decision(f.archive,self.evidence,self.decision_path)
        self.decision=json.loads(self.decision_path.read_text(encoding='utf-8'))

    def insert(self,db,table,value):
        fields=[k for k in value if k!='accepted_sequence']
        db.execute('INSERT INTO '+table+'('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',[value[k] for k in fields])

    def write_evidence(self):
        with closing(sqlite3.connect(self.f.archive)) as db:
            s=api._event_conflict_snapshot(db,self.key);t=s['target']
            value={'schema':'historical-event-conflict-review-evidence/v1','status':'design_review_only_not_applied',
                   'target_before':t,'ready_before':s['ready'],'manifest':{'events_v2':s['events_manifest']},
                   'candidate_event_ids':s['candidate_event_ids'],'selected_event_id':s['selected_event_id'],
                   'target_article':api._review_one(db,'central_news_article_revisions','article_revision_id',t['article_revision_id']),
                   'target_body_sha256':s['body_sha256'],'candidates':[]}
            for h in s['histories']:
                e=next(row for row in h['events'] if row['event_key']==json.loads(t['computed_json'])['rule_result']['event_key'])
                a=api._review_one(db,'central_news_article_revisions','article_revision_id',e['article_revision_id'])
                b=api._review_one(db,'central_news_body_revisions','body_revision_id',e['body_revision_id'])
                value['candidates'].append({'event_id':h['event_id'],'event_rows':h['events'],'membership_rows':h['memberships'],
                    'evidence_article':a,'evidence_body_id':b['body_revision_id'],'evidence_body_sha256':api._review_hash(b)})
        value['review_payload_sha256']=api._review_hash(value)
        self.evidence.write_text(api._json(value),encoding='utf-8')

    def transition(self,decision=None):
        decision=self.decision if decision is None else decision
        plan=self.f.root/'transition.json';digest=api.code_digest()
        value={'schema':'historical-active-events-execution/v1','dataset_id':'recovered-v2','code_digest':digest,
               'parent_dataset':'parent-review','parent_digest':'a'*64,'reviewed_event_resolution':decision}
        plan.write_text(api._json(value),encoding='utf-8')
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            _apply_active_events(db,'parent-review','a'*64,'recovered-v2',digest,plan,api._sha256(plan),reviewed_event_resolution=decision)

    def receipt(self,db):
        return json.loads(db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?',(api.EVENT_REVIEW_PREFIX+self.decision['decision_id'],)).fetchone()[0])

    def test_preserves_nine_ids_old_histories_and_cursor_then_resumes_child(self):
        self.transition()
        result=api.resolve_event_conflict(self.f.archive,self.decision_path)
        self.assertFalse(result['replayed'])
        with closing(sqlite3.connect(self.f.archive)) as db:
            receipt=self.receipt(db);api.validate_event_review_receipt(db,receipt)
            self.assertEqual(self.old_events,api._review_rows(db,'SELECT * FROM central_news_event_revisions ORDER BY accepted_sequence')[:-1])
            self.assertEqual(self.old_members,api._review_rows(db,'SELECT * FROM central_news_event_membership_revisions ORDER BY accepted_sequence')[:-1])
            self.assertEqual(self.old_articles,api._review_rows(db,'SELECT * FROM central_news_article_revisions ORDER BY accepted_sequence'))
            self.assertEqual(self.old_bodies,api._review_rows(db,'SELECT * FROM central_news_body_revisions ORDER BY accepted_sequence'))
            self.assertEqual(self.before['events_manifest'],json.loads(db.execute("SELECT value_json FROM archive_build_manifest WHERE key='events_v2'").fetchone()[0]))
            self.assertEqual(1,db.execute("SELECT ready FROM archive_event_ready_v2 WHERE identity='c-child'").fetchone()[0])
        self.assertTrue(api.resolve_event_conflict(self.f.archive,self.decision_path)['replayed'])
        self.assertEqual(2,api.finalize_events(self.f.archive)['targets_resolved_now'])
        self.assertEqual(0,api.finalize_events(self.f.archive)['targets_resolved_now'])
        with closing(sqlite3.connect(self.f.archive)) as db:
            api.validate_event_review_receipt(db,self.receipt(db))
            self.assertEqual(4,db.execute("SELECT COUNT(*) FROM archive_input_progress WHERE progress_state='event_done'").fetchone()[0])

    def test_unreviewed_transition_and_unpinned_recovery_are_rejected(self):
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError,'explicit review'):
                _apply_active_events(db,'parent-review','a'*64,'new','b'*64,self.f.root/'none','c'*64)
            db.rollback()
        self.transition()
        (self.f.root/'transition.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'pinned'):
            api.resolve_event_conflict(self.f.archive,self.decision_path)

    def test_stale_decisions_wrong_choice_and_extra_conflict_are_rejected(self):
        for field in ('selected_event_id','candidate_event_ids'):
            value=copy.deepcopy(self.decision)
            value['snapshot'][field]='wrong' if field=='selected_event_id' else ['wrong']
            value.pop('decision_id');value['decision_id']=api._review_hash(value)
            with closing(sqlite3.connect(self.f.archive)) as db:
                with self.assertRaisesRegex(ValueError,'snapshot changed'):
                    api.validate_event_conflict_decision(db,value)
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            db.execute("UPDATE archive_event_ready_v2 SET state='conflict' WHERE identity='c-child'")
        with closing(sqlite3.connect(self.f.archive)) as db:
            with self.assertRaisesRegex(ValueError,'checkpoint'):
                api.validate_event_conflict_decision(db,self.decision)

    def test_failure_after_event_insert_rolls_back_revisions_and_checkpoint(self):
        self.transition()
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            db.execute("CREATE TRIGGER fail_recovery BEFORE INSERT ON archive_build_manifest WHEN NEW.key LIKE 'event_conflict_review_v1_%' BEGIN SELECT RAISE(ABORT,'injected receipt failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError,'injected'):
            api.resolve_event_conflict(self.f.archive,self.decision_path)
        with closing(sqlite3.connect(self.f.archive)) as db:
            self.assertEqual(self.before,api._event_conflict_snapshot(db,self.key))
            self.assertEqual(self.old_events,api._review_rows(db,'SELECT * FROM central_news_event_revisions ORDER BY accepted_sequence'))
            self.assertEqual(self.old_members,api._review_rows(db,'SELECT * FROM central_news_event_membership_revisions ORDER BY accepted_sequence'))

    def test_audit_rejects_missing_receipt_and_receipt_target_tampering(self):
        from scripts.audit_prepared_historical_archive_v2 import audit
        self.transition();api.resolve_event_conflict(self.f.archive,self.decision_path)
        api.finalize_events(self.f.archive);api.build_projection(self.f.archive)
        # This fixture used a minimal metadata-only parent generation, rather
        # than the separate source-component fixture. Audit the base contract.
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            db.execute('DELETE FROM archive_build_manifest WHERE key=?',(inputs.KEY,))
        self.assertNotIn('event_conflict_review_invalid',audit(self.f.archive)['blockers'])
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            receipt=self.receipt(db)
            db.execute('DELETE FROM archive_build_manifest WHERE key=?',(api.EVENT_REVIEW_PREFIX+self.decision['decision_id'],))
        self.assertIn('event_conflict_review_invalid',audit(self.f.archive)['blockers'])
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            receipt['target_after']['ordinal']+=1
            db.execute('INSERT INTO archive_build_manifest VALUES(?,?)',(api.EVENT_REVIEW_PREFIX+self.decision['decision_id'],api._json(receipt)))
        self.assertIn('event_conflict_review_invalid',audit(self.f.archive)['blockers'])

    def test_writer_failure_and_other_targets_do_not_release_dependencies(self):
        self.transition()
        with patch.object(api,'_resolve_event',side_effect=ValueError('injected writer failure')):
            with self.assertRaisesRegex(ValueError,'injected'):
                api.resolve_event_conflict(self.f.archive,self.decision_path)

    def test_fingerprint_failure_does_not_hide_a_later_reviewed_target(self):
        from scripts.audit_prepared_historical_archive_v2 import audit
        self.transition();api.resolve_event_conflict(self.f.archive,self.decision_path)
        api.finalize_events(self.f.archive);api.build_projection(self.f.archive)
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            db.execute('DELETE FROM archive_build_manifest WHERE key=?',(inputs.KEY,))
            db.execute("UPDATE archive_targets_v2 SET fingerprint='corrupt' WHERE identity='a-completed'")
        result=audit(self.f.archive)
        self.assertIn('target_verification_fingerprint_invalid',result['blockers'])
        self.assertNotIn('event_conflict_review_invalid',result['blockers'])
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            db.execute('DELETE FROM archive_build_manifest WHERE key=?',
                       (api.EVENT_REVIEW_PREFIX+self.decision['decision_id'],))
        rejected=audit(self.f.archive)
        self.assertIn('event_conflict_review_invalid',rejected['blockers'])
        self.assertEqual(['event review receipt set differs from reviewed targets'],rejected['event_review_errors'])

    def test_remaining_target_on_same_article_keeps_input_and_child_waiting(self):
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            target={**self.before['target'],'target_id':'000660','event_state':'pending','ordinal':None,
                    'event_reason':None,'candidate_event_ids_json':None}
            self.insert(db,'archive_targets_v2',target)
            self.insert(db,'archive_event_ready_v2',{**self.before['ready'],'target_id':'000660','state':'pending'})
            events={**self.before['events_manifest'],'target_count':5}
            db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='events_v2'",(api._json(events),))
        self.write_evidence();self.decision_path=self.f.root/'decision-with-sibling.json'
        api.create_event_conflict_decision(self.f.archive,self.evidence,self.decision_path)
        self.decision=json.loads(self.decision_path.read_text(encoding='utf-8'));self.transition()
        api.resolve_event_conflict(self.f.archive,self.decision_path)
        with closing(sqlite3.connect(self.f.archive)) as db:
            self.assertEqual('assessment_done',db.execute("SELECT progress_state FROM archive_input_progress WHERE identity='b-conflict'").fetchone()[0])
            self.assertEqual(0,db.execute("SELECT ready FROM archive_event_ready_v2 WHERE identity='c-child'").fetchone()[0])

    def test_candidate_body_change_is_rejected_and_rule_change_rolls_back(self):
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            db.execute("UPDATE central_news_body_revisions SET body_text='changed' WHERE body_revision_id='seed-body-1'")
        with closing(sqlite3.connect(self.f.archive)) as db:
            with self.assertRaisesRegex(ValueError,'BODY evidence'):
                api.validate_event_conflict_decision(db,self.decision)
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            body=next(b for b in self.old_bodies if b['body_revision_id']=='seed-body-1')
            db.execute('UPDATE central_news_body_revisions SET body_text=? WHERE body_revision_id=?',(body['body_text'],body['body_revision_id']))
        self.transition()
        original=api._resolve_event
        def wrong_rule(db,*args,**kwargs):
            result=original(db,*args,**kwargs)
            db.execute("UPDATE central_news_event_revisions SET certainty='TERMINATED' WHERE event_revision_id=?",(result[3],))
            return result
        with patch.object(api,'_resolve_event',side_effect=wrong_rule):
            with self.assertRaisesRegex(ValueError,'prepared rule'):
                api.resolve_event_conflict(self.f.archive,self.decision_path)
        with closing(sqlite3.connect(self.f.archive)) as db:
            self.assertEqual(self.old_events,api._review_rows(db,'SELECT * FROM central_news_event_revisions ORDER BY accepted_sequence'))
        with closing(sqlite3.connect(self.f.archive)) as db:
            self.assertEqual(0,db.execute("SELECT ready FROM archive_event_ready_v2 WHERE identity='c-child'").fetchone()[0])
        with api._exclusive_lock(self.f.archive):
            with self.assertRaisesRegex(ValueError,'another archive builder'):
                api.resolve_event_conflict(self.f.archive,self.decision_path)

    def test_independent_review_registration_preserves_code_and_data_then_recovers(self):
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            current=api.report(db);digest=api.code_digest()
            db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",(api._json({**current,'builder_code_digest':digest}),))
            gen=inputs.generation(db);inputs._save(db,{**gen,'code_digest':digest})
        decision_path=self.f.root/'same-code-decision.json'
        api.create_event_conflict_decision(self.f.archive,self.evidence,decision_path)
        decision=json.loads(decision_path.read_text(encoding='utf-8'))
        self.assertFalse(api.register_event_conflict_decision(self.f.archive,decision_path)['replayed'])
        with closing(sqlite3.connect(self.f.archive)) as db:
            self.assertEqual(self.before,api._event_conflict_snapshot(db,self.key))
            self.assertEqual(('parent-review',digest),(api.report(db)['dataset_id'],api.report(db)['builder_code_digest']))
        self.assertTrue(api.register_event_conflict_decision(self.f.archive,decision_path)['replayed'])
        self.assertFalse(api.resolve_event_conflict(self.f.archive,decision_path)['replayed'])
        self.assertTrue(api.resolve_event_conflict(self.f.archive,decision_path)['replayed'])
        with closing(sqlite3.connect(self.f.archive)) as db,db:
            receipt=json.loads(db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?',(api.EVENT_REVIEW_PREFIX+decision['decision_id'],)).fetchone()[0])
            api.validate_event_review_receipt(db,receipt)
            db.execute('DELETE FROM archive_build_manifest WHERE key=?',(api.EVENT_DECISION_PREFIX+decision['decision_id'],))
            with self.assertRaisesRegex(ValueError,'transition identity missing'):
                api.validate_event_review_receipt(db,receipt)


if __name__=='__main__':unittest.main()
