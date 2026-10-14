"""Actual frozen D2/D5 values on native Paper studies; never current admission."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from tests.unit import test_position_study_cohort as native
from kiwoom_monitor.application.research_evaluation import (
    prepare_position_study_historical_context,validate_position_study_historical_context,
    prepare_position_action_study_publication,decode_position_action_study_publication,_content_id)
from kiwoom_monitor.domain.news_observation import stable_document_hash
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository


class HistoricalContextTests(unittest.TestCase):
    def setUp(self):
        self.case=native.NativePositionStudyCohortTests();self.case.setUp();self.addCleanup(self.case.doCleanups)
        helper=self.case.base.helper
        codes=['005930']+[f'{i:06d}' for i in range(1,20)]
        rankings=[]
        for i,seconds in enumerate((240,270,295,510,535,600)):
            at=(helper.base+timedelta(seconds=seconds)).isoformat()
            rankings.append({'ordinal':100+i,'accepted_sequence':100+i,'revision_id':f'actual-ranking-{i}',
                'kind':'ranking','venue':'COMBINED','available_at':at,'effective_at':at,'observation_key':at,
                'completeness':'complete','payload':{'query_type':'5','items':[{'stk_cd':c,'trde_prica':str(100+i),
                    'flu_rt':'1.00'} for c in codes]}})
        def theme(name,minute):
            doc={'active_profile':'test','profiles':[{'name':'test','stock_themes':[{'code':c,'theme':name} for c in codes]}]}
            return {'snapshot_id':name,'available_at':helper.at(minute),'document':doc,'content_hash':stable_document_hash(doc)}
        self.case.base.dataset=helper.prepare(replace(helper.dataset,
            observations=helper.dataset.observations+tuple(rankings),theme_snapshots=(theme('historical-theme',0),theme('future-theme',10))))

    def declaration(self,cost=None):
        if cost is not None:
            self.case.base.execution=replace(self.case.base.execution,cost_model=cost)
            self.case.base.case=replace(self.case.base.case,request=replace(self.case.base.case.request,cost_model=cost))
        return self.case.prepare()

    def run_pairs(self,cohort,cases,*,mixed=False):
        from scripts.run_research import execute_research
        selections=[];base=self.case.base
        for case in cases:
            historical=prepare_position_study_historical_context(base.dataset,case.request)
            for branch in ('HOLD',case.request.action):
                variant=replace(case,branch_action=branch)
                context=None if mixed and branch=='HOLD' and case is cases[0] else historical
                run_id,spec=base.identity(case=variant,position_study_cohort=cohort,position_study_context=context)
                base.writer.start_run(run_id,spec,base.dataset.manifest,claim_independent=True)
                result=execute_research(base.dataset,base.writer,base.helper.root/'runs',base.strategy,base.execution,base.evaluation,
                    session_profile=base.profile,execution_scope='independent_development_validation/v1',
                    position_action_study=base.study,position_study_case=variant,position_study_cohort=cohort,position_study_context=context)
                self.assertEqual('ELIGIBLE',result.report_status)
                events=base.writer.load_execution_events(run_id)
                start=case.request.decision_time.replace(second=0,microsecond=0);end=start+timedelta(seconds=base.study.horizon_seconds)
                def mark(at):return next(e['event_id'] for e in events if e['event_type']=='MARK' and datetime.fromisoformat(e['occurred_at'])==at)
                selections.append(dict(run_id=run_id,start_event_id=mark(start),end_event_id=mark(end),action=branch,case_ref=case.case_ref))
        return selections

    def publication(self):
        cohort,cases=self.declaration();selected=self.run_pairs(cohort,cases)
        return prepare_position_action_study_publication(ResearchRepository(self.case.base.writer.path,read_only=True).load_position_action_evidence,
            cohort,selected,decision_time=self.case.base.cut)

    def test_source_cut_keeps_historical_theme_and_no_calculation_or_database_read(self):
        from kiwoom_monitor.application import market_theme_shadow
        cohort,cases=self.declaration()
        with patch('sqlite3.connect',side_effect=AssertionError('context source DB')),patch.object(
                market_theme_shadow,'compute_market_regime',side_effect=AssertionError('context prepare calculation')):
            context=prepare_position_study_historical_context(self.case.base.dataset,cases[0].request)
        self.assertEqual(['actual-ranking-0','actual-ranking-1','actual-ranking-2'],[r['revision_id'] for r in context['rankings']])
        self.assertEqual(['historical-theme'],[r['snapshot_id'] for r in context['theme_snapshots']])
        self.assertNotIn('future-theme',json.dumps(context));self.assertEqual(cases[0].request.decision_time.isoformat(),context['decision_time'])

    def test_actual_four_runs_use_one_source_read_each_and_two_distinct_context_replays(self):
        from kiwoom_monitor.application import market_theme_shadow
        cohort,cases=self.declaration();selected=self.run_pairs(cohort,cases)
        reader=ResearchRepository(self.case.base.writer.path,read_only=True);connect=reader._connect;sql=[]
        before=self.case.base.writer.path.read_bytes()
        def observed():
            connection=connect();connection.set_trace_callback(sql.append);return connection
        with patch.object(reader,'_connect',side_effect=observed),patch.object(reader,'load_position_action_evidence',wraps=reader.load_position_action_evidence) as borrowed:
            publication=prepare_position_action_study_publication(borrowed,cohort,selected,decision_time=self.case.base.cut)
        self.assertEqual(4,borrowed.call_count);self.assertEqual(16,sum(s.lstrip().upper().startswith('SELECT') for s in sql))
        self.assertFalse(any(s.lstrip().upper().startswith('COMMIT') for s in sql));self.assertEqual(before,self.case.base.writer.path.read_bytes())
        with patch('sqlite3.connect',side_effect=AssertionError('context decoder DB')),patch.object(
                market_theme_shadow,'compute_market_regime',wraps=market_theme_shadow.compute_market_regime) as calculate:
            decoded=decode_position_action_study_publication(publication)
        self.assertEqual(2,calculate.call_count);self.assertEqual(2,len(decoded['historical_contexts']))
        self.assertEqual(-13,decoded['comparison']['result']['paired_delta']['mean_net_change_won'])
        for result in decoded['historical_contexts']:
            self.assertEqual('historical-theme',result['observed_memberships'][0]['theme_id'])
            self.assertIn('mature_breakout_evidence_unavailable',result['market']['quality']['reasons'])
        self.assertFalse(decoded['comparison']['usable_for_current_goal'])

    def test_resigned_scope_future_or_changed_actual_dataset_cannot_enter_native_identity(self):
        cohort,cases=self.declaration();request=cases[0].request
        context=prepare_position_study_historical_context(self.case.base.dataset,request)
        for mutate in (lambda d:d.update(symbol='000660'),lambda d:d.update(dataset_id='other'),
                lambda d:d['rankings'][0].update(available_at='2030-01-01T00:00:00+00:00'),
                lambda d:d['rankings'][0]['payload']['items'][0].update(trde_prica='999999')):
            bad=deepcopy(context);mutate(bad)
            bad['context_ref']=_content_id('position-study-historical-context',{k:v for k,v in bad.items() if k!='context_ref'})
            with self.assertRaises(ValueError):self.case.base.identity(case=cases[0],position_study_context=bad)
        invalid=deepcopy(context);invalid['theme_snapshots'][0]['document']['active_profile']='changed'
        invalid['context_ref']=_content_id('position-study-historical-context',{k:v for k,v in invalid.items() if k!='context_ref'})
        with self.assertRaisesRegex(ValueError,'theme content'):validate_position_study_historical_context(invalid,self.case.base.dataset.manifest,request)
        invalid=deepcopy(context);invalid['context_ref']='x'*(128*1024)
        with self.assertRaisesRegex(ValueError,'bounded'):validate_position_study_historical_context(invalid,self.case.base.dataset.manifest,request)

    def test_mixed_hold_action_context_is_not_a_comparable_cohort(self):
        cohort,cases=self.declaration();selected=self.run_pairs(cohort,cases,mixed=True)
        receipt=self.case.read(cohort,selected)
        self.assertIsNone(receipt['result']);self.assertIn('study_pair_initial_state_market_or_goal_mismatch',receipt['quality']['reasons'])

    def test_native_proof_context_cannot_be_replaced_after_its_declaration(self):
        publication=self.publication();bad=deepcopy(publication)
        event=next(e for e in bad['sources'][0]['events'] if e['event_type']=='POSITION_STUDY_CASE')
        proof=json.loads(event['reason']);proof['historical_context']['rankings'][0]['payload']['items'][0]['trde_prica']='9999'
        event['reason']=json.dumps(proof,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        event['event_id']=_content_id('execution',{k:v for k,v in event.items() if k!='event_id'})
        bad['publication_ref']=_content_id('native-position-study-publication',{k:v for k,v in bad.items() if k!='publication_ref'})
        with self.assertRaisesRegex(ValueError,'historical context differs'):decode_position_action_study_publication(bad)

    def test_shared_context_code_is_in_scientific_hash_and_legacy_id_stays_fixed(self):
        from kiwoom_monitor.application.research_implementation import research_implementation_hash
        current=research_implementation_hash('krx-regular/v1');legacy=research_implementation_hash();read=Path.read_bytes
        for name in ('market_theme_shadow.py','theme_leadership.py'):
            with patch.object(Path,'read_bytes',lambda p:read(p)+b'controlled' if p.name==name else read(p)):
                self.assertNotEqual(current,research_implementation_hash('krx-regular/v1'));self.assertEqual(legacy,research_implementation_hash())

    def test_explicit_bounded_history_and_empty_sources_do_not_invent_observations(self):
        cohort,cases=self.declaration();dataset=self.case.base.dataset
        request=cases[0].request
        row=next(r for r in dataset.observations if r['kind']=='ranking')
        large=tuple({**row,'revision_id':f'large-{i:03d}'} for i in range(101))
        with self.assertRaisesRegex(ValueError,'bounded historical context'):
            prepare_position_study_historical_context(replace(dataset,observations=large),request)
        row={**row,'payload':{**row['payload'],'items':row['payload']['items'][:1]}}
        values=tuple({**row,'revision_id':f'bounded-{i:03d}'} for i in range(101))
        bounded=prepare_position_study_historical_context(replace(dataset,observations=values),request)
        self.assertEqual(100,len(bounded['rankings']));self.assertTrue(bounded['ranking_window_truncated'])
        empty=prepare_position_study_historical_context(replace(dataset,observations=(),theme_snapshots=()),request)
        from kiwoom_monitor.application.research_evaluation import replay_position_study_historical_context
        result=replay_position_study_historical_context(empty)
        self.assertEqual([],result['ranking_refs']);self.assertEqual([],result['observed_memberships'])
        self.assertEqual('UNKNOWN',result['market']['inferences']['market_type'])


class HistoricalContextRamTests(unittest.IsolatedAsyncioTestCase):
    from tests.unit.test_candidate_monitor import AllocationModelPublicationTests as _fixture_owner
    fixture=_fixture_owner.fixture

    def prepare(self,count):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import prepare_dynamic_allocation_model_publication
        runtime,collector,clock,source,_,selection,legacy=self._fixture_owner.prepare(self,count)
        case=HistoricalContextTests();case.setUp();self.addCleanup(case.doCleanups)
        cohort,cases=case.declaration(runtime._allocation['cost_model']);selected=case.run_pairs(cohort,cases)
        publication=prepare_position_action_study_publication(ResearchRepository(case.case.base.writer.path,read_only=True).load_position_action_evidence,
            cohort,selected,decision_time=case.case.base.cut)
        packet=prepare_dynamic_allocation_model_publication(selection,legacy['rows'],expected_selection_revision=1,position_studies={'005930':publication})
        return runtime,collector,clock,source,packet

    async def test_two_eight_share_historical_summary_without_hot_replay_or_source_reads(self):
        from kiwoom_monitor.central_server import market_theme_shadow_inputs as inputs
        for count in (2,8):
            runtime,_,_,source,packet=self.prepare(count)
            decoded=inputs.decode_dynamic_allocation_model_publication(packet,runtime._allocation,1)
            runtime.offer_allocation_models(packet,decoded)
            with patch('sqlite3.connect',side_effect=AssertionError('hot context DB')),patch(
                    'kiwoom_monitor.application.research_evaluation.replay_position_study_historical_context',side_effect=AssertionError('hot context replay')):
                await runtime._dispatch(runtime._snapshot())
                goal=runtime.latest_results['position_allocation']['r4_action_comparison']['goals'][0]
                summary=goal['historical_context_summary']
                self.assertEqual(2,summary['distinct_context_count']);self.assertEqual(2,summary['covered_case_count'])
                self.assertEqual(['historical-theme'],summary['observed_theme_ids']);self.assertFalse(summary['current_context_support_verified'])
                self.assertIn('historical_exact_context_proof_unavailable',goal['reasons']);self.assertEqual('EV_UNAVAILABLE',goal['status'])
                for _ in range(100):runtime.latest_results;runtime.status;runtime.allocation_publication_duplicate(packet)
            self.assertEqual(1,source.call_count)


if __name__=='__main__':unittest.main()
