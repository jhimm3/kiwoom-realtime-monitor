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

    def test_support_scopes_preserve_original_comparison_and_never_promote_current_risk(self):
        publication=self.prepare();before=deepcopy(publication)
        values=self.current(publication)
        with patch('sqlite3.connect',side_effect=AssertionError('support must use decoded RAM only')):
            result=position_study_current_goal_binding(*values)
        support=result['model_support']
        self.assertEqual('SUPPORTED',support['historical_comparison']['status'])
        self.assertEqual(publication['comparison']['evidence_ref'],support['historical_comparison']['evidence_ref'])
        self.assertFalse(support['historical_comparison']['usable_for_current_goal'])
        self.assertEqual(['minute'],support['declared_scope']['input_resolutions'])
        self.assertEqual('exact-observed-context/v1',support['declared_scope']['context_support_rule'])
        self.assertEqual('single-target-then-registered-family/v1',support['declared_scope']['continuation_rule'])
        self.assertEqual(2,len(support['declared_scope']['observed_case_states']))
        self.assertEqual('UNAVAILABLE',support['current_state']['status'])
        self.assertIsNone(support['current_state']['registered_family_matches'])
        self.assertEqual('BLOCKED',support['current_goal']['status'])
        self.assertTrue(all(support['current_goal']['checks'].values()))
        self.assertTrue(result['current_account_risk_bound'])
        self.assertEqual('UNVERIFIED',support['native_execution']['status'])
        self.assertFalse(support['native_execution']['reentry_admission_verified'])
        self.assertIsNone(result['result']);self.assertFalse(support['order_authorized'])
        self.assertEqual(before,publication)
        self.assertEqual(publication['comparison'],values[0]['comparison'])

    def test_changed_current_action_or_expiry_keeps_historical_support_scoped(self):
        model,request,target,obs,receipt,selection=self.current(self.prepare())
        for changed,reason in ((replace(request,action='HOLD',target_quantity=10,target_policy=None),
                               'current_study_sizing_or_action_mismatch'),
                              (replace(request,decision_time=request.target_policy.valid_until),
                               'current_goal_lease_invalid')):
            result=position_study_current_goal_binding(model,changed,target,obs,receipt,selection)
            support=result['model_support']
            self.assertEqual('SUPPORTED',support['historical_comparison']['status'])
            self.assertIn(reason,support['current_goal']['reasons'])
            check='goal_policy_scope_matches' if changed.action=='HOLD' else 'input_lease_current'
            self.assertFalse(support['current_goal']['checks'][check])
            self.assertEqual('EV_UNAVAILABLE',result['status']);self.assertIsNone(result['result'])
            self.assertFalse(support['current_goal']['usable']);self.assertFalse(result['order_authorized'])


class ConditionalStudyPublicationTests(unittest.TestCase):
    """Controlled actual Paper transitions; no claim of strategy promotion."""
    def setUp(self):
        from dataclasses import asdict
        from datetime import date,timezone
        from kiwoom_monitor.application.position_target import PositionActionConditionalStudyPolicy,PositionStudySupportSpec
        from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec,ResearchFoldSpec
        from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset
        from tests.unit.test_research_bundle_execution import rows_for
        self.asdict=asdict;self.base=cohort_tests.NativePositionStudyCohortTests();self.base.setUp()
        self.addCleanup(self.base.doCleanups);base=self.base.base;helper=base.helper
        rows=tuple(rows_for(date(2026,9,14),20)+rows_for(date(2026,9,15),20))
        helper.dataset=FrozenResearchDataset(deepcopy(helper.dataset.manifest),rows,())
        helper.evaluation=ResearchEvaluationSpec('chronological_holdout/v1',
            (ResearchFoldSpec('train','TRAIN',helper.at(2),'2026-09-15T09:20:00+09:00'),),120,180,0,1,1)
        base.dataset=helper.prepare();base.evaluation=helper.partition.evaluation_for(helper.evaluation)
        self.cut=datetime(2026,9,16,8,tzinfo=timezone(timedelta(hours=9)))
        self.study=PositionActionConditionalStudyPolicy(**{**base.study.to_dict(),
            'version':'position-action-study/v3','actions':('HOLD','REDUCE'),'target_notional_won':5000,
            'context_support_rule':'held-minute-relative-state/v1',
            'support_spec':PositionStudySupportSpec('conditional-paper-support/v1','held-minute-relative-state/v1',
                ('sampled_price',),2,2,'paired-trading-day-cluster-standard-error/v1')})
        base.study=self.study

    def generate(self,*,days=(14,15),future_prices=None,low_only=False,allow_unavailable=False):
        from kiwoom_monitor.application.position_target import freeze_position_action_study_cohort
        from kiwoom_monitor.application.research_execution import PaperExecutionEngine
        from kiwoom_monitor.application.research_replay import replay_krx_minute_bars
        from kiwoom_monitor.application.breakout_strategy import StrategyState
        from kiwoom_monitor.application.research_evaluation import build_research_report
        from tests.unit.test_research_execution import _decision
        from datetime import timezone
        base=self.base.base;cases=[]
        if len(set(days))>1:
            changed=deepcopy(base.helper.dataset)
            for row in changed.observations:
                if row['kind']=='minute_bar' and int(row['revision_id'].rsplit('-',1)[1])>=(6 if future_prices is not None else 7):
                    price=(future_prices.get(int(row['revision_id'].rsplit('-',1)[1]),1020) if future_prices is not None
                        else 1040 if '2026-09-14' in row['revision_id'] else 1000)
                    row['revision_id']+='-controlled-future'
                    row['payload'].update(open=price,high=price,low=950 if low_only else price,close=price)
            base.dataset=base.helper.prepare(changed)
        for index,day in enumerate(days):
            at=datetime.fromisoformat(f'2026-09-{day:02d}T09:05:02+09:00')
            if days.count(day)>1:at+=timedelta(minutes=index*5)
            policy=replace(base.case.request.target_policy,target_notional_won=self.study.target_notional_won,available_at=at,
                valid_until=at+timedelta(seconds=30))
            cases.append(replace(base.case,case_ref=f'conditional-{index}',branch_action='REDUCE',
                request=replace(base.case.request,action='REDUCE',decision_time=at,
                    reference_price_available_at=at,target_policy=policy)))
        _,spec=base.identity(case=cases[0]);cohort=freeze_position_action_study_cohort(self.study,tuple(cases),spec,
            base.dataset.manifest,minimum_samples=2,max_evidence_age_seconds=10*86400,
            model_valid_until=datetime(2030,1,1,tzinfo=timezone.utc))
        selections=[]
        class FixedClock(datetime):
            @classmethod
            def now(cls,tz=None):return self.cut.astimezone(tz or timezone.utc)
        for case in cases:
            for action in ('HOLD','REDUCE'):
                branch=replace(case,branch_action=action)
                run_id,spec=base.identity(case=branch,position_study_cohort=cohort)
                engine=PaperExecutionEngine(run_id,base.execution,base.strategy,session_profile=base.profile)
                # Initial position is created by the real Paper order/fill path.
                # Empty candidate keys are explicit controlled fixture state.
                at=case.request.decision_time;bars=replay_krx_minute_bars(base.dataset.observations,session_profile=base.profile)
                day_bars=[b for b in bars if datetime.fromisoformat(b.bar_start).date()==at.date()]
                opening=next(b for b in day_bars if datetime.fromisoformat(b.bar_end)==at.replace(second=0)-timedelta(minutes=1))
                decision=replace(_decision((datetime.fromisoformat(opening.bar_start)-timedelta(seconds=1)).isoformat()),run_id=run_id)
                events=list(engine.process_decision(decision,StrategyState(status='candidate',symbol='005930')))
                events+=engine.process_bar(opening)
                source=next(b for b in day_bars if datetime.fromisoformat(b.available_at)==at)
                events+=engine.process_bar(source);start=events[-1].event_id
                events.append(engine.observe_position_study_case(spec['position_action_study']['study_policy_ref'],
                    self.study,branch,source_bar=source))
                if self.study.version=='position-action-study/v4':
                    def native_family(frame):
                        from kiwoom_monitor.application.research_families import family_for_config
                        from kiwoom_monitor.application.research_replay import replay_candidate_universe
                        history=tuple(b for b in day_bars if datetime.fromisoformat(b.available_at)<=datetime.fromisoformat(frame.available_at))
                        frames=tuple(f for f in replay_candidate_universe(base.dataset.observations) if datetime.fromisoformat(f.available_at)<=datetime.fromisoformat(frame.available_at))
                        evaluation=family_for_config(base.strategy).evaluate_bar(run_id=run_id,evaluation_bar=frame,
                            bar_history=history,universe_frames=frames,config=base.strategy,state=engine.strategy_state)
                        return engine.process_native_family(evaluation.decision,evaluation.state,source_bar=frame,
                            bar_history=history,universe_frames=frames)
                    events+=native_family(source)
                elif action=='REDUCE':events+=engine.process_position_target(case.request,source_bar=source)
                endpoint=at.replace(second=0)+timedelta(seconds=self.study.horizon_seconds)
                later=[b for b in day_bars if datetime.fromisoformat(b.bar_end)>at.replace(second=0)]
                for bar in later:
                    events+=engine.process_bar(bar)
                    if datetime.fromisoformat(bar.bar_end)==endpoint:break
                    if self.study.version=='position-action-study/v4':events+=native_family(bar)
                end=events[-1].event_id
                exit_decision=replace(decision,decision_id='close-'+run_id,proposal='EXIT',final_action='EXIT',
                    decided_at=bar.available_at)
                events+=engine.process_decision(exit_decision,engine.strategy_state,source_bar=bar)
                follow=next(b for b in day_bars if datetime.fromisoformat(b.bar_start)>datetime.fromisoformat(bar.available_at))
                events+=engine.process_bar(follow)
                report=build_research_report(run_id,input_manifest=base.dataset.manifest,observations=base.dataset.observations,
                    candidate_events=(),decisions=(),execution_events=events,outcome_labels=(),
                    initial_cash_won=base.execution.initial_cash_won,cost_model=base.execution.cost_model.to_dict(),
                    spec=base.evaluation,logical_result_hash='controlled-'+run_id)
                self.assertEqual('ELIGIBLE',report.status,report.reasons)
                with patch('kiwoom_monitor.infrastructure.persistence.research_repository.datetime',FixedClock):
                    base.writer.start_run(run_id,spec,base.dataset.manifest,claim_independent=True)
                    base.writer.append_execution_events(tuple(events));base.writer.save_research_report(report)
                    base.writer.finish_run(run_id,'controlled-'+run_id)
                selections.append({'run_id':run_id,'start_event_id':start,'end_event_id':end,'action':action,'case_ref':case.case_ref})
        reader=ResearchRepository(base.writer.path,read_only=True)
        before=base.writer.path.read_bytes()
        if allow_unavailable:
            from kiwoom_monitor.application.research_evaluation import read_position_action_study_cohort
            return {'comparison':read_position_action_study_cohort(reader.load_position_action_evidence,cohort,selections,decision_time=self.cut),
                'cohort':cohort,'selections':selections}
        packet=prepare_position_action_study_publication(reader.load_position_action_evidence,cohort,selections,decision_time=self.cut)
        self.assertEqual(before,base.writer.path.read_bytes())
        return packet

    def current(self,model):
        from kiwoom_monitor.application.position_target import PositionActionStudyCase,evaluate_position_target_shadow
        from kiwoom_monitor.domain.order_contract import AccountSnapshot
        proof=model['comparison']['cases'][0]['decision_proof'];case=PositionActionStudyCase.from_dict(proof['case'])
        at=self.cut+timedelta(hours=1,minutes=5,seconds=2)
        request=replace(case.request,decision_time=at,reference_price_available_at=at,
            target_policy=replace(case.request.target_policy,available_at=at,valid_until=at+timedelta(seconds=30)))
        source=deepcopy(proof['source_bar']);state=deepcopy(proof['strategy_state']);portfolio=deepcopy(proof['portfolio'])
        shift=at-datetime.fromisoformat(source['available_at'])
        for name in ('bar_start','bar_end','available_at','observation_key'):
            source[name]=(datetime.fromisoformat(source[name])+shift).isoformat()
        source['research_session']=at.date().isoformat()+':KRX_REGULAR';source['revision_id']='current-controlled-bar'
        for name in ('opened_at','candidate_expires_at','cooldown_until'):
            if state[name]:state[name]=(datetime.fromisoformat(state[name])+shift).isoformat()
        portfolio['position']['opened_at']=state['opened_at']
        account=AccountSnapshot(request.account_ref,portfolio['cash_won'],0,{request.symbol:portfolio['position']['quantity']},at)
        target=evaluate_position_target_shadow(request,account,())
        raw=dict(source_bar=source,strategy_state=state,portfolio=portfolio,source_continuation=model['source_continuation'])
        return request,target,raw

    def test_v3_actual_paper_sources_replay_and_support_exact_current_cell(self):
        from kiwoom_monitor.application.research_evaluation import evaluate_position_study_conditional_shadow
        packet=self.generate();original=deepcopy(packet)
        with patch('sqlite3.connect',side_effect=AssertionError('RAM replay must not read DB')):
            model=decode_position_action_study_publication(packet)
            request,target,raw=self.current(model)
            result=evaluate_position_study_conditional_shadow(model,request,target,**raw)
        self.assertEqual('native-position-study-publication/v3',packet['version'])
        self.assertEqual('SUPPORTED_UNDER_PAPER_ASSUMPTIONS',result['status'],result)
        self.assertEqual((2,2),(result['paired_sample_count'],result['trading_day_count']))
        rows={r['selection']['case_ref']:r for r in model['comparison']['cases'] if r['selection']['action']=='REDUCE'}
        holds={r['selection']['case_ref']:r for r in model['comparison']['cases'] if r['selection']['action']=='HOLD'}
        deltas=[(r['outcome']['gross_change_won']-r['outcome']['fee_tax_won'])
            -(holds[k]['outcome']['gross_change_won']-holds[k]['outcome']['fee_tax_won']) for k,r in rows.items()]
        self.assertNotEqual(deltas[0],deltas[1])
        self.assertEqual(sum(deltas)/2,result['result']['mean_net_change_won'])
        self.assertEqual(abs(deltas[0]-deltas[1])/2,result['result']['net_standard_error_won'])
        self.assertFalse(result['usable_for_native_selection']);self.assertFalse(result['order_authorized'])
        self.assertFalse(model['comparison']['usable_for_current_goal']);self.assertEqual(original,packet)
        result['result']['mean_net_change_won']=999
        again=evaluate_position_study_conditional_shadow(model,request,target,**raw)
        self.assertEqual(sum(deltas)/2,again['result']['mean_net_change_won'])
        self.assertLessEqual(datetime.fromisoformat(again['available_at']),request.decision_time)
        self.assertEqual(request.target_policy.valid_until.isoformat(),again['valid_until'])

    def test_same_day_cells_and_missing_pairs_never_supply_two_trading_days(self):
        from kiwoom_monitor.application.research_evaluation import read_position_action_study_cohort
        packet=self.generate(days=(14,14))
        self.assertTrue(packet['comparison']['conditional_cells'])
        for cell in packet['comparison']['conditional_cells']:
            self.assertEqual(1,cell['trading_day_count']);self.assertIsNone(cell['result'])
            self.assertIn('conditional_cell_minimum_trading_days_not_met',cell['reasons'])
        result=read_position_action_study_cohort(ResearchRepository(self.base.base.writer.path,read_only=True).load_position_action_evidence,
            packet['cohort'],packet['selections'][:-1],decision_time=self.cut)
        self.assertEqual('EV_UNAVAILABLE',result['r4_status']);self.assertIsNone(result['result'])
        self.assertTrue(all(cell['result'] is None for cell in result['conditional_cells']))

    def test_existing_research_entrypoint_generates_v3_without_extra_producer(self):
        from kiwoom_monitor.application.position_target import freeze_position_action_study_cohort
        from datetime import timezone
        base=self.base.base;cohort,cases=self.base.prepare(action='REDUCE')
        _,spec=base.identity(case=cases[0])
        cohort=freeze_position_action_study_cohort(self.study,cases,spec,base.dataset.manifest,
            minimum_samples=2,max_evidence_age_seconds=10*86400,model_valid_until=datetime(2030,1,1,tzinfo=timezone.utc))
        class FixedClock(datetime):
            @classmethod
            def now(cls,tz=None):return self.cut.astimezone(tz or timezone.utc)
        with patch('kiwoom_monitor.infrastructure.persistence.research_repository.datetime',FixedClock):
            selections=self.base.run_pairs(cohort,cases)
        packet=prepare_position_action_study_publication(ResearchRepository(base.writer.path,read_only=True).load_position_action_evidence,
            cohort,selections,decision_time=self.cut)
        model=decode_position_action_study_publication(packet)
        self.assertEqual('position-action-study/v3',model['study'].version)
        self.assertTrue(all('support_observation' in r['decision_proof'] for r in model['comparison']['cases']))
        self.assertTrue(all(cell['result'] is None for cell in model['comparison']['conditional_cells']))

    def test_v3_native_binding_requires_current_owner_and_keeps_ev_off(self):
        model=decode_position_action_study_publication(self.generate());request,target,raw=self.current(model)
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        from kiwoom_monitor.domain.order_contract import AccountSnapshot
        from types import SimpleNamespace
        account=AccountSnapshot(request.account_ref,raw['portfolio']['cash_won'],0,{'005930':10},request.decision_time)
        observation=PositionAccountObservation(account,(),'current-account',request.decision_time,True,True,())
        receipt={'available_at':request.decision_time.isoformat(),'valid_until':(request.decision_time+timedelta(seconds=20)).isoformat(),
            'risk_disposition':'ALLOW'}
        selection={'policy':SimpleNamespace(account_ref=request.account_ref)}
        result=position_study_current_goal_binding(model,request,target,observation,receipt,selection)
        self.assertEqual('position-study-model-support/v2',result['model_support']['version'])
        self.assertEqual('UNAVAILABLE',result['model_support']['conditional_shadow']['status'])
        result=position_study_current_goal_binding(model,request,target,observation,{**receipt,'risk_disposition':'EXIT_REQUIRED',
            'protection_required':True},selection,current_support_observation=raw)
        self.assertIsNone(result['model_support']['conditional_shadow']['result'])
        self.assertEqual('EV_UNAVAILABLE',result['status']);self.assertIsNone(result['result']);self.assertFalse(result['order_authorized'])

    def test_v3_original_observation_and_statistics_tampering_cannot_be_resigned(self):
        packet=self.generate()
        def change_observation(p):
            event=next(e for e in p['sources'][0]['events'] if e['event_type']=='POSITION_STUDY_CASE')
            proof=json.loads(event['reason']);proof['support_observation']['cell']['strategy_state']['opened_at']=-1
            event['reason']=json.dumps(proof,sort_keys=True,separators=(',',':'))
            event['event_id']=_content_id('execution',{k:v for k,v in event.items() if k!='event_id'})
        for mutate in (lambda p:p['comparison']['conditional_cells'][0]['result'].update(mean_net_change_won=9999),
                       lambda p:p['sources'][0]['record']['spec']['position_action_study']['policy']['support_spec'].update(minimum_trading_days=1),
                       change_observation):
            value=deepcopy(packet);mutate(value)
            value['publication_ref']=_content_id('native-position-study-publication',{k:v for k,v in value.items() if k!='publication_ref'})
            with self.assertRaises(ValueError):decode_position_action_study_publication(value)

    def test_current_scope_lease_bar_and_continuation_mismatch_stay_null(self):
        from kiwoom_monitor.application.research_evaluation import evaluate_position_study_conditional_shadow
        model=decode_position_action_study_publication(self.generate());request,target,raw=self.current(model)
        variations=[(request,{**target,'target_quantity':5},raw),
            (replace(request,decision_time=request.target_policy.valid_until),target,raw),
            (replace(request,reference_price_won=request.reference_price_won+1),target,raw),
            (replace(request,max_snapshot_age_seconds=31),target,raw),
            (request,target,{**raw,'source_bar':{**raw['source_bar'],'capture_quality':'partial'}}),
            (request,target,{**raw,'source_bar':{**raw['source_bar'],'available_at':(request.decision_time+timedelta(seconds=1)).isoformat()}}),
            (request,target,{**raw,'source_bar':{**raw['source_bar'],'volume':raw['source_bar']['volume']+1}}),
            (request,target,{**raw,'source_continuation':{**raw['source_continuation'],'code_hash':'0'*64}}),
            (request,target,{**raw,'strategy_state':{**raw['strategy_state'],'emitted_candidate_keys':['different']}})]
        for changed,goal,observation in variations:
            result=evaluate_position_study_conditional_shadow(model,changed,goal,**observation)
            self.assertIsNone(result['result']);self.assertTrue(result['reasons']);self.assertFalse(result['order_authorized'])


def native_policy_study(paper,*,daily_loss=1000,horizon=180,gap=300):
    from kiwoom_monitor.application.position_target import (
        PositionActionNativePolicyStudyPolicy,NativePositionStudyPolicy,native_study_policy_implementation_ref)
    from kiwoom_monitor.domain.execution_activation import MockUnfilledExecutionPolicy
    policy=NativePositionStudyPolicy('native-policy-paper/v1',daily_loss,gap,0,0,0,
        MockUnfilledExecutionPolicy(1,1000,2000,600,300,0,1,2,60),native_study_policy_implementation_ref())
    paper.study=PositionActionNativePolicyStudyPolicy(**{**paper.study.__dict__,'version':'position-action-study/v4',
        'horizon_seconds':horizon,'continuation_rule':'single-limit-intervention-then-native-policy/v1','native_policy':policy})
    paper.base.base.study=paper.study
    return policy


class NativePolicyStudyTests(unittest.TestCase):
    def setUp(self):
        self.paper=ConditionalStudyPublicationTests();self.paper.setUp();self.addCleanup(self.paper.doCleanups)

    def test_v4_limit_wait_terminal_and_actual_source_replay(self):
        from kiwoom_monitor.application.research_evaluation import evaluate_position_study_conditional_shadow
        policy=native_policy_study(self.paper);packet=self.paper.generate(future_prices={6:1000,7:1000,8:1000});model=decode_position_action_study_publication(packet)
        request,target,raw=self.paper.current(model)
        result=evaluate_position_study_conditional_shadow(model,request,target,**raw,native_policy=policy.to_dict())
        self.assertEqual('SUPPORTED_UNDER_NATIVE_POLICY_PAPER_ASSUMPTIONS',result['status'],result)
        self.assertFalse(result['usable_for_native_selection']);self.assertFalse(result['order_authorized'])
        terminal=[json.loads(e['reason']) for row in packet['sources'] for e in row['events'] if e['event_type']=='NATIVE_POLICY_TERMINAL']
        self.assertTrue(any(p['portfolio']['pending_order'] is not None for p in terminal))
        self.assertTrue(any(p['portfolio']['pending_order'] is None for p in terminal))
        self.assertEqual(0,result['result']['mean_net_change_won'])
        self.assertEqual(0,result['result']['net_standard_error_won'])
        unavailable=evaluate_position_study_conditional_shadow(model,request,target,**raw)
        self.assertIsNone(unavailable['result'])
        different_id=deepcopy(policy.to_dict())
        different_id['unfilled_policy']['uncertainty_multiplier']=float(different_id['unfilled_policy']['uncertainty_multiplier'])
        self.assertNotEqual(_content_id('native-study-policy',policy.to_dict()),_content_id('native-study-policy',different_id))
        self.assertIsNone(evaluate_position_study_conditional_shadow(model,request,target,**raw,native_policy=different_id)['result'])
        changed=deepcopy(packet);event=next(e for e in changed['sources'][0]['events'] if e['event_type']=='NATIVE_POLICY_ASSESSMENT')
        proof=json.loads(event['reason']);proof['risk']['daily_net_pnl_won']=100000;event['reason']=json.dumps(proof,sort_keys=True)
        event['event_id']=_content_id('execution',{k:v for k,v in event.items() if k!='event_id'})
        changed['publication_ref']=_content_id('native-position-study-publication',{k:v for k,v in changed.items() if k!='publication_ref'})
        with self.assertRaisesRegex(ValueError,'replay'):decode_position_action_study_publication(changed)

    def test_shared_daily_loss_changes_real_limit_fills_and_value(self):
        native_policy_study(self.paper,daily_loss=10,horizon=300)
        packet=self.paper.generate(future_prices={});model=decode_position_action_study_publication(packet)
        assessments=[json.loads(e['reason']) for s in packet['sources'] for e in s['events'] if e['event_type']=='NATIVE_POLICY_ASSESSMENT']
        self.assertTrue(any('DAILY_LOSS_LIMIT_REACHED' in a['protection']['reasons'] for a in assessments))
        orders=[e for s in packet['sources'] for e in s['events'] if e['event_type']=='ORDER_SUBMITTED' and 'shared_policy_continuation' in e['reason']]
        self.assertTrue(orders)
        self.assertTrue(any(e['position_quantity_after']==0 for s in packet['sources'] for e in s['events'] if e['event_type']=='FILL'))
        self.assertEqual('COHORT_COMPARABLE',model['comparison']['r4_status'])
        original_ref=model['comparison']['study_policy_ref'];original_mean=model['comparison']['result']['paired_delta']['mean_net_change_won']
        native_policy_study(self.paper,daily_loss=1000,horizon=300)
        other=decode_position_action_study_publication(self.paper.generate(future_prices={}))
        self.assertNotEqual(original_ref,other['comparison']['study_policy_ref'])
        self.assertNotEqual(original_mean,other['comparison']['result']['paired_delta']['mean_net_change_won'])

    def test_shared_sampled_stop_and_clock_exit_have_filled_paths(self):
        for clock in (False,True):
            paper=ConditionalStudyPublicationTests();paper.setUp();self.addCleanup(paper.doCleanups)
            paper.base.base.strategy=replace(paper.base.base.strategy,stop_loss_bps=300,
                **({'max_hold_minutes':5,'session_exit_policy':'krx_regular_before_auction/v1','session_exit_buffer_seconds':60} if clock else {}))
            native_policy_study(paper,horizon=300)
            packet=paper.generate(future_prices={} if clock else {7:980,8:980,9:980,10:980})
            model=decode_position_action_study_publication(packet)
            assessments=[json.loads(e['reason']) for s in packet['sources'] for e in s['events'] if e['event_type']=='NATIVE_POLICY_ASSESSMENT']
            self.assertTrue(any(a['clock']['exit_required'] if clock else a['sampled_stop'] for a in assessments))
            self.assertEqual('COHORT_COMPARABLE',model['comparison']['r4_status'])

    def test_pending_protection_and_intrabar_only_stop_remain_distinct(self):
        self.paper.base.base.strategy=replace(self.paper.base.base.strategy,stop_loss_bps=300)
        native_policy_study(self.paper,horizon=300)
        failed=self.paper.generate(future_prices={6:980,7:980,8:980,9:980,10:980},allow_unavailable=True)
        self.assertIsNone(failed['comparison']['result'])
        self.assertTrue(failed['comparison']['excluded'])
        self.assertTrue(any('cancel_ack' in r['reason'] for r in failed['comparison']['excluded']))
        other=ConditionalStudyPublicationTests();other.setUp();self.addCleanup(other.doCleanups);native_policy_study(other)
        packet=other.generate(future_prices={},low_only=True)
        model=decode_position_action_study_publication(packet)
        self.assertEqual('COHORT_COMPARABLE',model['comparison']['r4_status'])
        window_events=[e for s in packet['sources'] for e in s['events'] if e['event_type']=='NATIVE_POLICY_ASSESSMENT']
        self.assertFalse(any(json.loads(e['reason'])['sampled_stop'] for e in window_events))

    def test_independent_expiry_and_missing_pair_cannot_be_censored(self):
        from kiwoom_monitor.application.research_evaluation import read_position_action_study_cohort
        native_policy_study(self.paper,gap=45)
        failed=self.paper.generate(future_prices={},allow_unavailable=True)
        self.assertIsNone(failed['comparison']['result'])
        self.assertTrue(any('BLOCKED' in row['reason'] for row in failed['comparison']['excluded']))
        missing=read_position_action_study_cohort(ResearchRepository(self.paper.base.base.writer.path,read_only=True).load_position_action_evidence,
            failed['cohort'],failed['selections'][:-1],decision_time=self.paper.cut)
        self.assertIsNone(missing['result']);self.assertIn('frozen_cohort_branch_missing',missing['quality']['reasons'])

    def test_real_research_entrypoint_runs_v4_episode_and_replays_policy(self):
        from datetime import timezone
        native_policy_study(self.paper)
        base=self.paper.base.base;cohort,cases=self.paper.base.prepare(action='REDUCE')
        class FixedClock(datetime):
            @classmethod
            def now(cls,tz=None):return self.paper.cut.astimezone(tz or timezone.utc)
        with patch('kiwoom_monitor.infrastructure.persistence.research_repository.datetime',FixedClock):
            selections=self.paper.base.run_pairs(cohort,cases)
        packet=prepare_position_action_study_publication(ResearchRepository(base.writer.path,read_only=True).load_position_action_evidence,
            cohort,selections,decision_time=self.paper.cut)
        model=decode_position_action_study_publication(packet)
        self.assertEqual('position-action-study/v4',model['study'].version)
        self.assertTrue(all('native_study_inputs' in source['record']['spec'] for source in packet['sources']))
        self.assertTrue(any(e['event_type']=='NATIVE_POLICY_UNFILLED' for source in packet['sources'] for e in source['events']))

    def test_partial_unknown_missing_family_input_or_clock_is_not_replay_support(self):
        native_policy_study(self.paper,horizon=300)
        packet=self.paper.generate(future_prices={})
        for mode in ('partial','unknown','family','entry_future','entry_pending'):
            changed=deepcopy(packet)
            source=next(s for s in changed['sources'] if s['record']['spec']['position_action_study']['case']['branch_action']=='REDUCE')
            if mode=='entry_future':
                event=next(e for e in source['events'] if e['event_type']=='FILL' and e['side']=='BUY')
                cut=source['record']['spec']['position_action_study']['case']['request']['decision_time']
                event['received_at']=(datetime.fromisoformat(cut)+timedelta(seconds=1)).isoformat()
            elif mode=='entry_pending':
                event=deepcopy(next(e for e in source['events'] if e['event_type']=='ORDER_SUBMITTED'))
                event['intent_id']='unresolved-start-order'
                source['events'].insert(1,event)
            elif mode=='family':
                event=next(e for e in source['events'] if e['event_type']=='NATIVE_POLICY_FAMILY')
                raw=json.loads(event['reason']);raw['bar_history']=raw['bar_history'][-1:]
                event['reason']=json.dumps(raw,sort_keys=True)
            else:
                event=next(e for e in source['events'] if e['event_type']=='ORDER_SUBMITTED' and 'native-policy-limit' in e['reason'])
                event['state']='PARTIALLY_FILLED' if mode=='partial' else 'SUBMISSION_UNKNOWN'
            event['event_id']=_content_id('execution',{k:v for k,v in event.items() if k!='event_id'})
            changed['publication_ref']=_content_id('native-position-study-publication',{k:v for k,v in changed.items() if k!='publication_ref'})
            with self.assertRaises(ValueError):decode_position_action_study_publication(changed)

    def test_native_reentry_requirement_blocks_whole_declared_comparison(self):
        native_policy_study(self.paper,daily_loss=10,horizon=600)
        packet=self.paper.generate(future_prices={8:1100,9:1100,10:1200,11:1300},allow_unavailable=True)
        self.assertIsNone(packet['comparison']['result'])
        self.assertTrue(any('reentry_owner_admission' in row['reason'] for row in packet['comparison']['excluded']),packet['comparison'])

    def test_fraction_fifo_rounding_drives_protection_not_paper_integer_basis(self):
        base=self.paper.base.base
        base.execution=replace(base.execution,cost_model=replace(base.execution.cost_model,commission_bps=18))
        base.case=replace(base.case,request=replace(base.case.request,cost_model=base.execution.cost_model))
        native_policy_study(self.paper,daily_loss=22,horizon=300)
        packet=self.paper.generate(future_prices={})
        decoded=decode_position_action_study_publication(packet)
        rows=[json.loads(e['reason']) for source in packet['sources'] for e in source['events'] if e['event_type']=='NATIVE_POLICY_ASSESSMENT']
        self.assertTrue(any(r['risk']['daily_net_pnl_won']==-22 and r['protection']['disposition']=='EXIT_REQUIRED' for r in rows))
        self.assertEqual('COHORT_COMPARABLE',decoded['comparison']['r4_status'])


class CurrentStudyRamTests(unittest.IsolatedAsyncioTestCase):
    fixture=monitor_tests.AllocationModelPublicationTests.fixture
    wait_for=monitor_tests.AllocationModelPublicationTests.wait_for

    def prepare(self,count=2,native_mode=False):
        from types import SimpleNamespace
        from tests.unit.test_entry_thesis import NativeFullExitStressTests,NativeDynamicAllocationEvTests,DynamicPositionAllocationTests
        from tests.unit.test_position_runner_state import NativeRunnerStateTests,native,cost
        from kiwoom_monitor.central_server.mock_account_monitor import MockAccountMonitor
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import MarketThemeShadowRuntime
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import compile_dynamic_allocation_ram_selection
        from kiwoom_monitor.application.position_target import PositionAccountObservation,_json_value
        from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
        from kiwoom_monitor.domain.order_contract import AccountSnapshot
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
        from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
        from tests.unit.test_candidate_monitor import shadow_inputs
        paper=ConditionalStudyPublicationTests();paper.setUp();self.addCleanup(paper.doCleanups)
        native_profile=native_policy_study(paper) if native_mode else None
        base=paper.base.base
        turnover_cap=sum(1019 if second%2 and second<59 else 1020 for second in range(42,60))
        paper.study=replace(paper.study,target_notional_won=4080,max_position_notional_won=turnover_cap,max_position_quantity=turnover_cap//1020)
        base.study=paper.study
        base.case=replace(base.case,request=replace(base.case.request,reference_price_won=1020,
            target_policy=replace(base.case.request.target_policy,target_notional_won=4080,
                max_position_notional_won=turnover_cap,max_position_quantity=turnover_cap//1020)))
        for row in base.helper.dataset.observations:
            if row['kind']=='minute_bar':
                row['payload'].update(open=1020,high=1020,low=1019,close=1020,volume=60,trade_value_million_won=59)
        base.dataset=base.helper.prepare()
        packet=paper.generate();model=decode_position_action_study_publication(packet)
        request,_,raw=paper.current(model);cut=request.decision_time
        state=NativeRunnerStateTests();state.setUp();self.addCleanup(state.doCleanups);state.now=cut
        candidate={**state.base.package.candidate_spec,'parameters':model['source_continuation']['parameters'],
            'implementation_hash':model['source_continuation']['code_hash']}
        state.base.package=replace(state.base.package,candidate_spec=candidate,candidate_spec_hash=native._hash(candidate),
            scientific_implementation_hash=model['source_continuation']['code_hash'])
        state.base.store.upsert_documents(ForwardEvaluationRepository.MOCK_AUTOMATION_CANDIDATE_COLLECTION,
            [{'owner':state.base.package.strategy_ref,'key':state.base.package.package_hash,'document':state.base.package.to_dict()}])
        state.base.spec=replace(state.base.spec,candidate_package_hash=state.base.package.package_hash)
        if native_profile is not None:
            state.base.spec=replace(state.base.spec,unfilled_execution_policy=native_profile.unfilled_policy,
                **{key:getattr(native_profile,key) for key in ('maximum_daily_loss_won','maximum_data_gap_seconds',
                    'maximum_submission_unknown_count','maximum_reconnect_count','maximum_balance_mismatch_count')})
        state.base.bundle.automation_context=SimpleNamespace(execution_run_id=state.base.run_id,spec_id=state.base.spec.spec_id)
        state.runner=MockAutomationRunner(state.base.store,state.base.bundle,state.base.spec);state.runner._bootstrap_pending=False
        position=raw['portfolio']['position'];opened=datetime.fromisoformat(position['opened_at'])
        fill=replace(state.base.event(1,'controlled-current','BUY',10,1020),occurred_at=opened,received_at=opened)
        state.base.events.values=[fill];state.runner._apply_new_fills()
        account=AccountSnapshot(native.ACCOUNT_REF,raw['portfolio']['cash_won'],0,{'005930':10},cut)
        paid=replace(cost(cut.date(),'매수',10200,position['entry_commission']),origin_scope=state.base.spec.account_scope)
        risk=build_mock_automation_risk_snapshot(MockAccountRecovery(account,()),(fill,),(paid,),(),
            execution_run_id=state.base.run_id,binding_revision=1,reconciliation_revision=1,observed_at=cut,
            broker_query_started_at=cut,broker_query_completed_at=cut,cost_query_complete=True)
        observation=PositionAccountObservation(account,(),'current-native-cut',cut,True,True,(),(),(),risk)
        monitor=MockAccountMonitor.__new__(MockAccountMonitor);monitor._accepting=True;monitor._runtime=state.runtime
        monitor._position_runtime_revision=0;monitor._position_observation=observation
        monitor._risk_events=[fill];monitor._risk_costs={('paid',):paid};state.base.bundle.monitor=monitor
        owner=MockAutomationSupervisor(state.base.store,SimpleNamespace());owner._runners[native.ACCOUNT_REF]=state.runner
        source=Mock(side_effect=lambda account_ref:owner.position_shadow_observation(monitor.latest_position_observation))
        class PublicationClock(datetime):
            @classmethod
            def now(cls,tz=None):return paper.cut.astimezone(tz or paper.cut.tzinfo)
        xr_case=NativeFullExitStressTests();self.addCleanup(xr_case.doCleanups)
        with patch('kiwoom_monitor.infrastructure.persistence.research_repository.datetime',PublicationClock):
            xr_case.prepare(execution_override=base.execution)
        xr=xr_case.read_exit(xr_case.reader.load_position_action_evidence,xr_case.selection,xr_case.policy,
            decision_time=paper.cut,max_evidence_age_seconds=10*86400)
        ev_case=NativeDynamicAllocationEvTests();ev_case.setUp();self.addCleanup(ev_case.doCleanups)
        with patch('kiwoom_monitor.infrastructure.persistence.research_repository.datetime',PublicationClock):
            ev_case.prepare(cost_override=base.execution.cost_model,reference_price=1020,terminal_price=1060,horizon_minutes=3)
        arguments={**ev_case.arguments,'request':replace(ev_case.request,decision_time=paper.cut,reference_price_available_at=paper.cut),
            'account':replace(ev_case.account,as_of=paper.cut),'max_evidence_age_seconds':10*86400}
        er=ev_case.read_ev(ev_case.reader.load_position_action_evidence,ev_case.selections,**arguments)
        self.assertIsNotNone(xr['evidence'],xr['quality'])
        self.assertIsNotNone(er['evidence'],er['source_receipt']['excluded'])
        clock,hub,collector,monitors,_=self.fixture(count,cleanup=False)
        start=cut.replace(minute=4,second=0);clock[0]=start
        collector._apply_trade_source_approval({('005930','KRX'),('000660','KRX')},start,reset_all=True)
        collector._ram_retention_since=start
        context=shadow_inputs();context=replace(context,
            market_frames=tuple(replace(frame,available_at=(cut-timedelta(seconds=20)+timedelta(seconds=5*i)).isoformat(),
                trading_date=cut.date().isoformat(),session_minute=start.strftime('%H:%M')) for i,frame in enumerate(context.market_frames)),
            theme_membership=replace(context.theme_membership,available_at=start.isoformat()),
            breakout_evidence=replace(context.breakout_evidence,available_at=start.isoformat()))
        policy_fixture=DynamicPositionAllocationTests();policy_fixture.setUp()
        policy=replace(policy_fixture.policy,account_ref=native.ACCOUNT_REF,operating_limit_won=5000,context_weights=(1,0,0),
            capacity_window_seconds=20,allocation_ev_horizon_seconds=180,allocation_exit_horizon_seconds=120,
            available_at=cut-timedelta(seconds=1),valid_until=cut+timedelta(seconds=30),max_turnover_ratio_bps=10000)
        document=json.loads(json.dumps({'schema':'dynamic-position-allocation-ram/v1','policy':paper.asdict(policy),
            'symbols':['005930'],'expected_execution_run_id':state.base.run_id,'expected_binding_revision':1,
            'data_path':'nas','maximum_daily_loss_won':1000,'cost_model':base.execution.cost_model.to_dict()},default=_json_value))
        runtime=MarketThemeShadowRuntime(hub,collector,tuple((m,context) for m in monitors),
            position_account_source=source,position_allocation=compile_dynamic_allocation_ram_selection(document))
        collector.set_ram_observation_retention(runtime._retention,owner=runtime);runtime._retention_owned=True
        for second in range(60):
            clock[0]=start+timedelta(seconds=second)
            price=1019 if second%2 and second<59 else 1020
            collector._publish_parsed({'trnm':'REAL','data':[{'type':'0B','item':code,'values':{
                '10':str(price if code=='005930' else 900),'15':'1','13':str(second+1),'14':str(second),
                '20':clock[0].strftime('%H%M%S'),'311':'1234'}} for code in ('005930','000660')]})
        clock[0]=cut
        collector._publish_parsed({'trnm':'REAL','data':[{'type':'0B','item':'005930','values':{
            '10':'1020','15':'1','13':'61','14':'60','20':cut.strftime('%H%M%S'),'311':'1234'}}]})
        combined=prepare_dynamic_allocation_model_publication(document,[{'symbol':'005930','ev_receipt':er,'exit_receipt':xr}],
            expected_selection_revision=1,position_studies={'005930':packet})
        runtime.offer_allocation_models(combined,decode_dynamic_allocation_model_publication(combined,runtime._allocation,1))
        self.addAsyncCleanup(runtime.close)
        return runtime,collector,clock,source,monitor,state,owner

    async def test_current_collector_and_owner_produce_shared_conditional_result_orders_off(self):
        for count in (2,8):
            runtime,collector,clock,source,monitor,state,owner=self.prepare(count)
            with patch('sqlite3.connect',side_effect=AssertionError('current Shadow DB read')),patch.object(
                    collector,'ram_observation_snapshot',wraps=collector.ram_observation_snapshot) as borrowed:
                await runtime._dispatch(runtime._snapshot())
            receipt=runtime.latest_results['position_allocation']
            self.assertEqual(1,borrowed.call_count);self.assertEqual(1,source.call_count)
            self.assertEqual('CALCULATED',receipt['quality']['status'],receipt)
            goal=receipt['r4_action_comparison']['goals'][0];conditional=goal['model_support']['conditional_shadow']
            self.assertEqual('SUPPORTED_UNDER_PAPER_ASSUMPTIONS',conditional['status'],goal)
            self.assertIsNotNone(conditional['result']);self.assertTrue(goal['current_account_risk_bound'])
            self.assertIsNone(goal['result']);self.assertFalse(goal['order_authorized']);self.assertFalse(receipt['order_authorized'])
            before=deepcopy(receipt)
            for _ in range(100):runtime.latest_results
            self.assertEqual(before,runtime.latest_results['position_allocation']);self.assertEqual(1,source.call_count)
            clock[0]=datetime.fromisoformat(receipt['valid_until'])
            self.assertNotIn('goals',runtime.latest_results['position_allocation']['r4_action_comparison'])

    async def test_cost_owner_loss_and_pending_protection_remove_only_conditional_support(self):
        runtime,collector,clock,source,monitor,state,owner=self.prepare(2)
        def conditional():
            return runtime.latest_results['position_allocation']['r4_action_comparison']['goals'][0]['model_support']['conditional_shadow']
        with patch('sqlite3.connect',side_effect=AssertionError('hot path DB')):
            await runtime._dispatch(runtime._snapshot());self.assertIsNotNone(conditional()['result'])
            paid=next(iter(monitor._risk_costs.values()));monitor._risk_costs={('paid',):replace(paid,commission=0,total_cost=0)}
            await runtime._dispatch(runtime._snapshot())
            self.assertIsNone(conditional()['result']);self.assertIn('current_account_study_basis_unavailable',conditional()['reasons'])
            monitor._risk_costs={('paid',):paid}
            state.runner.request_protection_review(reason='controlled',input_ref='native-risk',requested_at=clock[0])
            await runtime._dispatch(runtime._snapshot())
            self.assertIsNone(conditional()['result']);self.assertIn('conditional_current_native_binding_or_protection_invalid',conditional()['reasons'])
            owner._runners.clear();await runtime._dispatch(runtime._snapshot())
            self.assertIsNone(conditional()['result']);self.assertIn('current_account_study_basis_unavailable',conditional()['reasons'])
        self.assertEqual(4,source.call_count);self.assertFalse(runtime.latest_results['position_allocation']['order_authorized'])

    async def test_late_trade_cannot_relabel_incomplete_minute_as_supported(self):
        runtime,collector,clock,source,monitor,state,owner=self.prepare(2)
        with patch('sqlite3.connect',side_effect=AssertionError('hot path DB')):
            await runtime._dispatch(runtime._snapshot())
            clock[0]+=timedelta(seconds=1)
            collector._publish_parsed({'trnm':'REAL','data':[{'type':'0B','item':'005930','values':{
                '10':'1020','15':'1','13':'62','14':'61','20':'090459','311':'1234'}}]})
            await runtime._dispatch(runtime._snapshot())
        receipt=runtime.latest_results['position_allocation']
        conditional=receipt['r4_action_comparison']['goals'][0]['model_support']['conditional_shadow']
        self.assertIsNone(conditional['result'])
        self.assertIn('current_closed_minute_support_quality_or_continuity_invalid',conditional['reasons'])
        self.assertFalse(receipt['order_authorized']);self.assertEqual(2,source.call_count)

    async def test_running_shadow_worker_uses_existing_owner_and_publishes_conditional_value(self):
        runtime,collector,clock,source,monitor,state,owner=self.prepare(2)
        with patch('sqlite3.connect',side_effect=AssertionError('running Shadow DB read')):
            await runtime.start()
            await self.wait_for(lambda:runtime._allocation_receipt is not None)
            receipt=runtime.latest_results['position_allocation']
            self.assertEqual('RUNNING',runtime.status['state'])
            conditional=receipt['r4_action_comparison']['goals'][0]['model_support']['conditional_shadow']
            self.assertEqual('SUPPORTED_UNDER_PAPER_ASSUMPTIONS',conditional['status'],receipt)
            self.assertIsNotNone(conditional['result']);self.assertFalse(receipt['order_authorized'])
            self.assertEqual(1,source.call_count);self.assertEqual(1,runtime._snapshot_reads)

    async def test_current_timestamp_precision_is_not_silently_normalized(self):
        runtime,collector,clock,source,monitor,state,owner=self.prepare(2)
        await runtime._dispatch(runtime._snapshot())
        clock[0]+=timedelta(microseconds=1)
        with patch('sqlite3.connect',side_effect=AssertionError('hot path DB')):
            await runtime._dispatch(runtime._snapshot())
        receipt=runtime.latest_results['position_allocation']
        conditional=receipt['r4_action_comparison']['goals'][0]['model_support']['conditional_shadow']
        self.assertIsNone(conditional['result'])
        self.assertIn('current_study_timestamp_precision_outside_declared_profile',conditional['reasons'])
        self.assertFalse(receipt['order_authorized'])

    async def test_v4_running_worker_matches_owner_policy_and_rejects_policy_loss(self):
        runtime,collector,clock,source,monitor,state,owner=self.prepare(8,native_mode=True)
        with patch('sqlite3.connect',side_effect=AssertionError('v4 running Shadow DB read')):
            await runtime.start()
            await self.wait_for(lambda:runtime._allocation_receipt is not None)
            receipt=runtime.latest_results['position_allocation']
            binding=receipt['r4_action_comparison']['goals'][0]
            conditional=binding['model_support']['conditional_shadow']
            self.assertEqual('SUPPORTED_UNDER_NATIVE_POLICY_PAPER_ASSUMPTIONS',conditional['status'],binding)
            self.assertIsNotNone(conditional['result']);self.assertFalse(receipt['order_authorized'])
            self.assertEqual('EV_UNAVAILABLE',binding['status']);self.assertIsNone(binding['result'])
            self.assertFalse(binding['model_support']['native_execution']['equivalence_verified'])
            self.assertEqual(1,source.call_count);self.assertEqual(1,runtime._snapshot_reads)
            for _ in range(100):runtime.latest_results
            self.assertEqual(1,source.call_count)
            await runtime.close()
        changed,_,_,changed_source,changed_monitor,changed_state,changed_owner=self.prepare(2,native_mode=True)
        changed_state.runner._spec=replace(changed_state.runner._spec,maximum_daily_loss_won=999)
        with patch('sqlite3.connect',side_effect=AssertionError('v4 policy mismatch DB read')):
            await changed._dispatch(changed._snapshot())
        conditional=changed.latest_results['position_allocation']['r4_action_comparison']['goals'][0]['model_support']['conditional_shadow']
        self.assertIsNone(conditional['result'])
        self.assertIn('current_native_policy_observation_unavailable_or_mismatch',conditional['reasons'])


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
