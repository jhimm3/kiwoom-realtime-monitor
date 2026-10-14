"""Native v2 reported replay and conservative current-goal RAM integration."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timedelta
import json
import unittest
from unittest.mock import patch,Mock

from tests.unit import test_position_study_cohort as cohort_tests
from tests.unit import test_candidate_monitor as monitor_tests
from kiwoom_monitor.application.research_evaluation import (
    prepare_position_action_study_publication,decode_position_action_study_publication,_content_id)
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.central_server.market_theme_shadow_inputs import (
    prepare_dynamic_allocation_model_publication,decode_dynamic_allocation_model_publication,
    position_study_current_goal_binding)


class NativeStudyPublicationTests(unittest.TestCase):
    def setUp(self):
        self.case=cohort_tests.NativePositionStudyCohortTests();self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def prepare(self):
        cohort,cases=self.case.prepare();selections=self.case.run_pairs(cohort,cases)
        reader=ResearchRepository(self.case.base.writer.path,read_only=True)
        return prepare_position_action_study_publication(reader.load_position_action_evidence,
            cohort,selections,decision_time=self.case.base.cut)

    def test_actual_source_publication_replays_original_ids_and_ttl(self):
        cohort,cases=self.case.prepare();selected=self.case.run_pairs(cohort,cases)
        reader=ResearchRepository(self.case.base.writer.path,read_only=True);sql=[];connect=reader._connect
        before=self.case.base.writer.path.read_bytes()
        def observed():
            connection=connect();connection.set_trace_callback(sql.append);return connection
        with patch.object(reader,'_connect',side_effect=observed),patch.object(reader,
                'load_position_action_evidence',wraps=reader.load_position_action_evidence) as borrowed:
            publication=prepare_position_action_study_publication(borrowed,cohort,selected,decision_time=self.case.base.cut)
        self.assertEqual(4,borrowed.call_count)
        self.assertEqual(16,sum(s.lstrip().upper().startswith('SELECT') for s in sql))
        self.assertFalse(any(s.lstrip().upper().startswith('COMMIT') for s in sql))
        self.assertEqual(before,self.case.base.writer.path.read_bytes())
        first=decode_position_action_study_publication(publication)
        second=decode_position_action_study_publication(publication)
        self.assertEqual(first,second);self.assertEqual(publication['comparison'],first['comparison'])
        self.assertFalse(first['comparison']['usable_for_current_goal'])

    def test_resigned_statistic_event_source_or_endpoint_tampering_is_rejected(self):
        publication=self.prepare()
        for mutate in (lambda p:p['comparison']['result']['paired_delta'].update(mean_net_change_won=100000),
                       lambda p:p['sources'][0]['events'][0].update(cash_after_won=99999),
                       lambda p:p['sources'][0]['record']['spec'].update(code_hash='0'*64),
                       lambda p:p['sources'][0]['record'].update(final_execution='final-heldout'),
                       lambda p:p['selections'][0].update(end_event_id='other'),
                       lambda p:p['sources'].append(deepcopy(p['sources'][0]))):
            value=deepcopy(publication);mutate(value)
            value['publication_ref']=_content_id('native-position-study-publication',{k:v for k,v in value.items() if k!='publication_ref'})
            with self.assertRaises(ValueError):decode_position_action_study_publication(value)

    def test_publication_budget_and_cancel_checkpoint_precede_replay(self):
        publication=self.prepare()
        oversized={**publication,'publication_ref':'x'*(4*1024*1024)}
        with self.assertRaisesRegex(ValueError,'bounded native study'):decode_position_action_study_publication(oversized)
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            decode_position_action_study_publication(publication,checkpoint=Mock(side_effect=RuntimeError('cancelled')))
        invalid=deepcopy(publication);event=invalid['sources'][0]['events'][0];event['quantity']=False
        event['event_id']=_content_id('execution',{k:v for k,v in event.items() if k!='event_id'})
        invalid['publication_ref']=_content_id('native-position-study-publication',{k:v for k,v in invalid.items() if k!='publication_ref'})
        with self.assertRaisesRegex(ValueError,'quantity/value invalid'):decode_position_action_study_publication(invalid)

    def current(self,publication):
        from tests.unit import test_entry_thesis as fixtures
        from kiwoom_monitor.application.position_target import PositionAccountObservation,evaluate_position_target_shadow
        native=fixtures.NativePositionSelectionInputsTests();native.setUp()
        at=self.case.base.cut;account=replace(native.account,positions={'005930':10},as_of=at)
        risk=native.risk_snapshot(observed=at,account_at=at,positions=account.positions)
        observation=PositionAccountObservation(account,(),'current-account',at,True,True,(),(),(),risk)
        request=replace(self.case.base.case.request,account_ref=account.account_ref,decision_time=at,
            reference_price_available_at=at,account_input_ref=observation.input_ref,orders_input_ref=observation.input_ref,
            source_decision_ref='current-upper-allocation',target_policy=replace(self.case.base.case.request.target_policy,
                available_at=at,valid_until=at+timedelta(seconds=30)))
        target=evaluate_position_target_shadow(request,account,(),broker_open_orders=())
        receipt={'available_at':at.isoformat(),'valid_until':(at+timedelta(seconds=20)).isoformat(),
            'account_input_ref':observation.input_ref,'risk_snapshot_ref':risk.snapshot_id,'risk_disposition':'ALLOW'}
        selection={'policy':Mock(account_ref=account.account_ref),'expected_execution_run_id':'run-a',
            'expected_binding_revision':3,'data_path':'nas','maximum_daily_loss_won':1000}
        return decode_position_action_study_publication(publication),request,target,observation,receipt,selection

    def test_current_account_risk_binds_but_missing_context_never_grants_use(self):
        values=self.current(self.prepare())
        first=position_study_current_goal_binding(*values)
        self.assertTrue(first['current_account_risk_bound'])
        self.assertEqual('EV_UNAVAILABLE',first['status']);self.assertIsNone(first['result'])
        self.assertFalse(first['order_authorized'])
        self.assertIn('historical_exact_context_proof_unavailable',first['reasons'])
        _,request,target,observation,receipt,selection=values
        later=request.decision_time+timedelta(seconds=1)
        fresh=replace(request,decision_time=later,reference_price_available_at=later,
            target_policy=replace(request.target_policy,available_at=later,valid_until=later+timedelta(seconds=30)))
        from kiwoom_monitor.application.position_target import evaluate_position_target_shadow
        goal=evaluate_position_target_shadow(fresh,observation.account,(),broker_open_orders=())
        second=position_study_current_goal_binding(values[0],fresh,goal,observation,receipt,selection)
        self.assertNotEqual(first['goal_decision_ref'],second['goal_decision_ref'])
        self.assertEqual(first['model_evidence_ref'],second['model_evidence_ref'])
        self.assertEqual(first['valid_until'],second['valid_until'])

    def test_current_expiry_binding_revision_price_size_and_native_risk_preempt(self):
        values=self.current(self.prepare());model,request,target,obs,receipt,selection=values
        changed=position_study_current_goal_binding(model,request,target,obs,receipt,{**selection,'expected_binding_revision':4})
        self.assertFalse(changed['current_account_risk_bound'])
        changed=position_study_current_goal_binding(model,replace(request,reference_price_won=1021),target,obs,receipt,selection)
        self.assertIn('current_goal_quantity_price_cost_or_pending_mismatch',changed['reasons'])
        changed=position_study_current_goal_binding(model,request,{**target,'target_quantity':20},obs,receipt,selection)
        self.assertIn('current_goal_quantity_price_cost_or_pending_mismatch',changed['reasons'])
        expired=position_study_current_goal_binding(model,replace(request,decision_time=request.target_policy.valid_until),target,obs,receipt,selection)
        self.assertFalse(expired['current_account_risk_bound']);self.assertIn('current_goal_lease_invalid',expired['reasons'])
        risk=position_study_current_goal_binding(model,request,target,obs,{**receipt,'risk_disposition':'EXIT_REQUIRED','protection_required':True},selection)
        self.assertIn('native_risk_preempts_position_study',risk['reasons']);self.assertFalse(risk['current_account_risk_bound'])


class StudyRamPublicationTests(unittest.IsolatedAsyncioTestCase):
    fixture=monitor_tests.AllocationModelPublicationTests.fixture
    def prepare(self,count=2):
        runtime,collector,now,source,observation,selection,legacy=monitor_tests.AllocationModelPublicationTests.prepare(self,count)
        case=cohort_tests.NativePositionStudyCohortTests();case.setUp();self.addCleanup(case.doCleanups)
        cost=runtime._allocation['cost_model']
        case.base.execution=replace(case.base.execution,cost_model=cost)
        case.base.case=replace(case.base.case,request=replace(case.base.case.request,cost_model=cost))
        cohort,cases=case.prepare();selected=case.run_pairs(cohort,cases)
        publication=prepare_position_action_study_publication(ResearchRepository(case.base.writer.path,read_only=True).load_position_action_evidence,
            cohort,selected,decision_time=case.base.cut)
        rows=[{k:v for k,v in row.items()} for row in legacy['rows']]
        packet=prepare_dynamic_allocation_model_publication(selection,rows,expected_selection_revision=1,
            position_studies={'005930':publication})
        return runtime,collector,now,source,selection,legacy,packet

    async def test_two_and_eight_shadows_share_v2_slot_inputs_and_inert_duplicates(self):
        for count in (2,8):
            runtime,collector,now,source,selection,legacy,packet=self.prepare(count)
            from kiwoom_monitor.application import position_target as targets
            from kiwoom_monitor.application import market_theme_shadow as market
            with patch('sqlite3.connect',side_effect=AssertionError('publication DB read')):
                decoded=decode_dynamic_allocation_model_publication(packet,runtime._allocation,1)
            with (patch.object(collector,'ram_observation_snapshot',wraps=collector.ram_observation_snapshot) as borrowed,
                  patch.object(targets,'evaluate_dynamic_position_allocation',wraps=targets.evaluate_dynamic_position_allocation) as upper,
                  patch.object(market,'compute_market_regime',wraps=market.compute_market_regime) as common,
                  patch('sqlite3.connect',side_effect=AssertionError('hot path DB read'))):
                runtime.offer_allocation_models(packet,decoded)
                await runtime._dispatch(runtime._snapshot())
            receipt=runtime.latest_results['position_allocation']
            self.assertEqual(1,borrowed.call_count);self.assertEqual(1,source.call_count)
            self.assertEqual(1,upper.call_count);self.assertEqual(1,common.call_count)
            self.assertEqual(1,runtime._allocation_evaluations)
            self.assertEqual('CALCULATED',receipt['quality']['status'])
            self.assertEqual('EV_UNAVAILABLE',receipt['r4_action_comparison']['status'])
            self.assertTrue(receipt['r4_action_comparison']['goals'][0]['current_account_risk_bound'])
            before=deepcopy(receipt)
            with patch('kiwoom_monitor.application.research_evaluation.decode_position_action_study_publication',side_effect=AssertionError('duplicate replay')):
                for _ in range(100):
                    self.assertTrue(runtime.allocation_publication_duplicate(packet)['duplicate']);runtime.latest_results
            self.assertEqual(before,runtime.latest_results['position_allocation'])
            self.assertEqual(1,source.call_count);self.assertEqual(1,runtime._allocation_evaluations)
            self.assertFalse(receipt['order_authorized'])
            model_until=runtime._allocation_models['valid_until']
            now[0]=datetime.fromisoformat(receipt['valid_until'])
            expired=runtime.latest_results['position_allocation']
            self.assertIsNone(expired['result']);self.assertNotIn('lower_targets',expired)
            self.assertNotIn('goals',expired['r4_action_comparison'])
            self.assertEqual(model_until,runtime._allocation_models['valid_until'])

    async def test_mixed_v1_v2_rows_wrong_selection_and_statistics_are_atomic_rejections(self):
        runtime,_,_,_,_,legacy,packet=self.prepare()
        def resign(value):
            import hashlib
            value['publication_ref']='allocation-model-publication:'+hashlib.sha256(json.dumps(
                {k:v for k,v in value.items() if k!='publication_ref'},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        for mutate in (lambda p:p['rows'][0].pop('position_study'),lambda p:p.update(expected_selection_revision=2),
                       lambda p:p['rows'][0]['position_study']['comparison']['result']['paired_delta'].update(mean_net_change_won=999)):
            bad=deepcopy(packet);mutate(bad);resign(bad)
            with self.assertRaises(ValueError):decode_dynamic_allocation_model_publication(bad,runtime._allocation,1)
            self.assertIsNone(runtime._allocation_models)
        self.assertEqual(legacy['rows'],[{k:v for k,v in row.items() if k!='position_study'} for row in packet['rows']])
        self.assertEqual(10,len(packet));self.assertEqual('dynamic-allocation-native-models/v2',packet['version'])
        # The v2 native valuation MARKs share the existing 2,000-bar budget.
        from kiwoom_monitor.application import research_evaluation as evaluation
        original=evaluation.decode_position_action_study_publication
        def enlarged(*args,**kwargs):
            decoded=original(*args,**kwargs)
            decoded['source_bar_counts']={run_id:2000 for run_id in decoded['source_counts']}
            return decoded
        with patch.object(evaluation,'decode_position_action_study_publication',side_effect=enlarged):
            with self.assertRaisesRegex(ValueError,'aggregate source/replay budget'):
                decode_dynamic_allocation_model_publication(packet,runtime._allocation,1)

    async def test_v2_http_auth_revision_duplicate_clear_and_client_use_existing_path(self):
        import httpx
        import tempfile
        from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
        _,_,clock,_,selection,legacy,packet=self.prepare(1)
        helper=monitor_tests.RamShadowSelectionTests();headers={'Authorization':'Bearer offline-token'}
        with tempfile.TemporaryDirectory() as directory:
            app,store,now,_=helper.app_fixture(directory);now[0]=clock[0]
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
                    url='/api/v1/research/ram-shadow/allocation-models'
                    self.assertEqual(401,(await client.post(url,json=packet)).status_code)
                    self.assertEqual(503,(await client.post(url,json=packet,headers=headers)).status_code)
                    response=await client.put('/api/v1/research/ram-shadow/selection',headers=headers,
                        json={'expected_revision':0,'enabled':True,'theme_ids':['반도체'],'leadership_venue':'KRX','position_allocation':selection})
                    self.assertEqual(200,response.status_code,response.text)
                    response=await client.post(url,headers=headers,json=packet)
                    self.assertEqual(200,response.status_code,response.text);self.assertFalse(response.json()['duplicate'])
                    active=app.state.market_theme_shadow_runtime
                    with patch('kiwoom_monitor.central_server.market_theme_shadow_inputs.decode_dynamic_allocation_model_publication',
                            side_effect=AssertionError('duplicate HTTP replay')):
                        for _ in range(10):
                            response=await client.post(url,headers=headers,json=packet)
                            self.assertEqual(200,response.status_code,response.text);self.assertTrue(response.json()['duplicate'])
                    self.assertEqual(1,active._allocation_model_generation)
                    self.assertEqual(422,(await client.post(url,headers=headers,json={**packet,'expected_selection_revision':2})).status_code)
                    cleared=await client.put('/api/v1/research/ram-shadow/selection',headers=headers,
                        json={'expected_revision':1,'position_allocation':None})
                    self.assertEqual(200,cleared.status_code,cleared.text)
                    self.assertNotIn('position_allocation_models',app.state.market_theme_shadow_runtime.latest_results)
                    self.assertEqual(503,(await client.post(url,headers=headers,json=packet)).status_code)
            finally:await helper.cleanup_app(app,store)
        client=CentralContentClient('http://offline','offline-token')
        with patch('sqlite3.connect',side_effect=AssertionError('client source reread')),patch.object(client,'_request',return_value={'accepted':True}) as send:
            prepared=client.prepare_dynamic_allocation_models(selection,legacy['rows'],expected_selection_revision=1,
                position_studies={'005930':packet['rows'][0]['position_study']})
            self.assertEqual(packet,prepared);self.assertEqual({'accepted':True},client.publish_dynamic_allocation_models(prepared))
        send.assert_called_once_with('POST','/api/v1/research/ram-shadow/allocation-models',packet)


if __name__=='__main__':unittest.main()
