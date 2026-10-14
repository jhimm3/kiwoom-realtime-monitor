"""Original upper EV replay and conservative cross-study reported lineage."""
from copy import deepcopy
from dataclasses import asdict
import json
import unittest
from unittest.mock import patch,Mock
from tests.unit import test_entry_thesis as native
from kiwoom_monitor.application.research_evaluation import (
    decode_dynamic_allocation_ev_source_publication,audit_position_study_ev_lineage,_content_id,
    read_dynamic_allocation_ev_document)


class NativeEvLineageTests(unittest.TestCase):
    def setUp(self):
        self.case=native.NativeDynamicAllocationEvTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.original=self.case.prepare()

    def prepare(self):
        return self.case.read_ev(self.case.reader.load_position_action_evidence,self.case.selections,
            **self.case.arguments,include_source_records=True)

    def test_opt_in_uses_same_four_reads_and_default_receipt_ids_ttl_and_source_bytes(self):
        case=self.case;sql=[];connect=case.reader._connect;before=case.path.read_bytes()
        def observed():
            connection=connect();connection.set_trace_callback(sql.append);return connection
        with patch.object(case.reader,'_connect',side_effect=observed),patch.object(case.reader,
                'load_position_action_evidence',wraps=case.reader.load_position_action_evidence) as borrowed:
            result=self.prepare()
        proof=result.pop('native_source_publication')
        self.assertEqual(self.original,result);self.assertEqual(4,borrowed.call_count)
        self.assertEqual(16,sum(s.lstrip().upper().startswith('SELECT') for s in sql))
        self.assertFalse(any(s.lstrip().upper().startswith('COMMIT') for s in sql))
        self.assertEqual(before,case.path.read_bytes())
        with patch('sqlite3.connect',side_effect=AssertionError('proof decoder source read')):
            decoded=decode_dynamic_allocation_ev_source_publication(proof)
        self.assertEqual(result['outcomes'],decoded['outcomes']);self.assertEqual(result['source_receipt'],decoded['source_receipt'])
        self.assertEqual(4,len(decoded['source_windows']));self.assertFalse(decoded['order_authorized'])

    def test_resigned_original_source_events_outcomes_endpoint_or_final_change_is_rejected(self):
        proof=self.prepare()['native_source_publication']
        mutations=(lambda p:p['sources'][0]['events'][0].update(cash_after_won=1),
            lambda p:p['outcomes'][0].update(gross_change_won=999999),
            lambda p:p['sources'][0]['record'].update(final_execution='final-heldout'),
            lambda p:p['selections'][0].update(end_event_id='unrelated'),
            lambda p:p['source_receipt'].update(development_role='VALIDATION'),
            lambda p:p['sources'].append(deepcopy(p['sources'][0])))
        for mutate in mutations:
            bad=deepcopy(proof);mutate(bad)
            bad['publication_ref']=_content_id('native-allocation-ev-source-publication',{k:v for k,v in bad.items() if k!='publication_ref'})
            with self.assertRaises(ValueError):decode_dynamic_allocation_ev_source_publication(bad)

    def test_missing_records_non_boolean_or_oversized_proof_cannot_claim_lineage(self):
        case=self.case;loader=Mock(side_effect=AssertionError('option must precede read'))
        with self.assertRaises(ValueError):case.read_ev(loader,case.selections,**case.arguments,include_source_records=1)
        loader.assert_not_called()
        def missing(*args,**kwargs):
            kwargs.pop('include_source_record');return case.reader.load_position_action_evidence(*args,**kwargs)
        with self.assertRaisesRegex(ValueError,'original source record required'):
            case.read_ev(missing,case.selections,**case.arguments,include_source_records=True)
        proof=self.prepare()['native_source_publication'];proof['publication_ref']='x'*(4*1024*1024)
        with self.assertRaisesRegex(ValueError,'bounded native EV'):decode_dynamic_allocation_ev_source_publication(proof)
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            decode_dynamic_allocation_ev_source_publication(self.prepare()['native_source_publication'],
                checkpoint=Mock(side_effect=RuntimeError('cancelled')))

    def test_existing_cli_opt_in_and_default_keep_evidence_and_reject_unknown_fields(self):
        from kiwoom_monitor.application.position_target import _json_value
        case=self.case
        document=json.loads(json.dumps({'schema':'dynamic-allocation-native-ev/v1','request':asdict(case.request),
            'account':asdict(case.account),'open_orders':[],**{k:case.arguments[k] for k in
                ('horizon_seconds','population_ref','minimum_samples','max_evidence_age_seconds')}},default=_json_value))
        default=read_dynamic_allocation_ev_document(document,case.reader.load_position_action_evidence,case.selections,development_role='TRAIN')
        proof=read_dynamic_allocation_ev_document({**document,'include_source_records':True},case.reader.load_position_action_evidence,case.selections,development_role='TRAIN')
        self.assertEqual(default,{k:v for k,v in proof.items() if k!='native_source_publication'})
        self.assertNotIn('native_source_publication',default)
        with self.assertRaises(ValueError):read_dynamic_allocation_ev_document({**document,'caller_ev_bps':5},Mock(),case.selections,development_role='TRAIN')
        import subprocess,sys
        from pathlib import Path
        directory=Path(case.directory.name)
        input_path=directory/'lineage-input.json';selection_path=directory/'lineage-selection.json'
        input_path.write_text(json.dumps({**document,'include_source_records':True}),encoding='utf-8')
        selection_path.write_text(json.dumps(case.selections),encoding='utf-8')
        before=case.path.read_bytes()
        completed=subprocess.run([sys.executable,'-m','scripts.preview_market_theme_shadow','--input',str(input_path),
            '--research-db',str(case.path),'--position-outcome-selection',str(selection_path),
            '--position-outcome-role','TRAIN'],capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(0,completed.returncode,completed.stderr)
        actual=json.loads(completed.stdout)
        self.assertEqual(json.loads(json.dumps(proof)),actual);self.assertEqual(before,case.path.read_bytes())

    def test_overlap_audit_cannot_promote_renamed_sources_or_disjoint_reports_to_independence(self):
        ev=decode_dynamic_allocation_ev_source_publication(self.prepare()['native_source_publication'])
        study={'source_windows':deepcopy(ev['source_windows']),'publication_ref':'study-reported',
            'comparison':{'cases':[{'outcome':r} for r in ev['outcomes']]}}
        result=audit_position_study_ev_lineage(study,ev)
        self.assertEqual('REPORTED_OVERLAP',result['status']);self.assertEqual(4,result['shared_run_count'])
        self.assertEqual(4,result['shared_outcome_count']);self.assertFalse(result['independence_verified'])
        for row in study['source_windows']:row.update(run_id='renamed-'+row['run_id'],dataset_id='renamed')
        result=audit_position_study_ev_lineage(study,ev)
        self.assertEqual('REPORTED_OVERLAP',result['status']);self.assertIn('shared_input_revision_hash',result['reasons'])
        for row in study['source_windows']:
            row.update(revision_ids_hash='other',warmup_start='2030-01-01T00:00:00+00:00',end='2030-01-01T00:10:00+00:00')
        study['comparison']['cases']=[]
        result=audit_position_study_ev_lineage(study,ev)
        self.assertEqual('REPORTED_DISJOINT',result['status']);self.assertFalse(result['independence_verified'])


class EvLineageRamTests(unittest.IsolatedAsyncioTestCase):
    from tests.unit.test_candidate_monitor import AllocationModelPublicationTests as _fixture_owner
    fixture=_fixture_owner.fixture

    def prepare(self,count):
        from tests.unit.test_position_study_publication import StudyRamPublicationTests
        from kiwoom_monitor.application.research_execution import SimulationCostModel
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import prepare_dynamic_allocation_model_publication
        runtime,collector,clock,source,selection,legacy,packet=StudyRamPublicationTests.prepare(self,count)
        row=packet['rows'][0];case=native.NativeDynamicAllocationEvTests();case.setUp();self.addCleanup(case.doCleanups)
        cost=SimulationCostModel(**row['exit_receipt']['replay_inputs']['execution']['cost_model'])
        case.prepare(cost_override=cost,reference_price=row['ev_receipt']['evidence']['reference_price_won'],terminal_price=1060,horizon_minutes=3)
        ev=case.read_ev(case.reader.load_position_action_evidence,case.selections,**case.arguments,include_source_records=True)
        packet=prepare_dynamic_allocation_model_publication(selection,[{'symbol':row['symbol'],'ev_receipt':ev,'exit_receipt':row['exit_receipt']}],
            expected_selection_revision=1,position_studies={row['symbol']:row['position_study']})
        return runtime,collector,clock,source,packet

    async def test_two_eight_share_replayed_lineage_and_status_does_not_replay_or_read(self):
        from kiwoom_monitor.central_server import market_theme_shadow_inputs as inputs
        for count in (2,8):
            runtime,collector,_,source,packet=self.prepare(count)
            decoded=inputs.decode_dynamic_allocation_model_publication(packet,runtime._allocation,1)
            lineage=decoded['rows']['005930']['position_study']['allocation_lineage']
            self.assertEqual('REPORTED_OVERLAP',lineage['status']);self.assertFalse(lineage['independence_verified'])
            runtime.offer_allocation_models(packet,decoded)
            with patch('sqlite3.connect',side_effect=AssertionError('hot path DB')),patch(
                    'kiwoom_monitor.application.research_evaluation.decode_dynamic_allocation_ev_source_publication',side_effect=AssertionError('hot path replay')):
                await runtime._dispatch(runtime._snapshot())
                goal=runtime.latest_results['position_allocation']['r4_action_comparison']['goals'][0]
                self.assertEqual(lineage,goal['allocation_lineage'])
                self.assertIn('allocation_study_reported_source_overlap',goal['reasons'])
                self.assertIn('allocation_input_lineage_independence_unverified',goal['reasons'])
                self.assertEqual('EV_UNAVAILABLE',goal['status']);self.assertFalse(goal['order_authorized'])
                for _ in range(100):runtime.latest_results;runtime.status;runtime.allocation_publication_duplicate(packet)
            self.assertEqual(1,source.call_count)

    async def test_resigned_ev_proof_cannot_change_selection_or_replace_active_slot(self):
        from kiwoom_monitor.central_server import market_theme_shadow_inputs as inputs
        runtime,_,_,_,packet=self.prepare(2)
        runtime.offer_allocation_models(packet,inputs.decode_dynamic_allocation_model_publication(packet,runtime._allocation,1))
        original=runtime._allocation_models;generation=runtime._allocation_model_generation
        bad=deepcopy(packet);proof=bad['rows'][0]['ev_receipt']['native_source_publication']
        proof['outcomes'][0]['fee_tax_won']+=1
        proof['publication_ref']=_content_id('native-allocation-ev-source-publication',{k:v for k,v in proof.items() if k!='publication_ref'})
        import hashlib
        bad['publication_ref']='allocation-model-publication:'+hashlib.sha256(json.dumps(
            {k:v for k,v in bad.items() if k!='publication_ref'},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        with self.assertRaises(ValueError):inputs.decode_dynamic_allocation_model_publication(bad,runtime._allocation,1)
        self.assertIs(original,runtime._allocation_models);self.assertEqual(generation,runtime._allocation_model_generation)
        bad=deepcopy(packet);bad['rows'][0]['ev_receipt']['native_source_publication']=None
        bad['publication_ref']='allocation-model-publication:'+hashlib.sha256(json.dumps(
            {k:v for k,v in bad.items() if k!='publication_ref'},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        with self.assertRaises(ValueError):inputs.decode_dynamic_allocation_model_publication(bad,runtime._allocation,1)
        self.assertIs(original,runtime._allocation_models)


if __name__=='__main__':unittest.main()
