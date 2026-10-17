"""Bounded native input reads for order-free evaluation, with conservative continuity."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, replace, asdict
from types import MappingProxyType
import hashlib
import json
from typing import Any, Protocol
from zoneinfo import ZoneInfo
import math
from fractions import Fraction

from kiwoom_monitor.application.market_research_features import market_frames_from_observations
from kiwoom_monitor.domain.ranking import normalize_stock_code
from kiwoom_monitor.application.market_theme_shadow import (
    MarketThemeShadowInputs, MatureBreakoutEvidence, stock_thesis_shadow_request_from_dict,
)
from kiwoom_monitor.application.theme_leadership import (
    ThemeLeadershipParameters, ThemeLeadershipState, UpperLimitReference, second_trade_points_from_rows,
    theme_memberships_from_snapshot,
)


def compile_dynamic_allocation_ram_selection(document):
    """Freeze an explicit research policy; market/model facts cannot be supplied here."""
    from kiwoom_monitor.application.position_target import DynamicPositionAllocationPolicy, _symbol
    from kiwoom_monitor.application.research_execution import SimulationCostModel
    keys={'schema','policy','symbols','expected_execution_run_id','expected_binding_revision',
          'data_path','maximum_daily_loss_won','cost_model'}
    if (not isinstance(document,dict) or set(document)!=keys
            or document['schema']!='dynamic-position-allocation-ram/v1'
            or len(json.dumps(document,allow_nan=False).encode())>64*1024):
        raise ValueError('invalid dynamic allocation RAM selection')
    symbols=document['symbols']
    if (not isinstance(symbols,list) or not 1<=len(symbols)<=200 or len(set(symbols))!=len(symbols)
            or any(not isinstance(s,str) for s in symbols)):
        raise ValueError('allocation requires 1..200 distinct symbols')
    for symbol in symbols:_symbol(symbol)
    if (type(document['expected_binding_revision']) is not int or document['expected_binding_revision']<=0
            or type(document['maximum_daily_loss_won']) is not int or document['maximum_daily_loss_won']<=0
            or type(document['expected_execution_run_id']) is not str or not 0<len(document['expected_execution_run_id'])<=256
            or document['data_path'] not in {'nas','direct'}):
        raise ValueError('explicit native account/Risk binding and research loss limit required')
    raw=dict(document['policy']);raw['context_weights']=tuple(raw['context_weights'])
    for field in ('available_at','valid_until'):raw[field]=datetime.fromisoformat(raw[field])
    policy=DynamicPositionAllocationPolicy(**raw)
    if policy.capacity_window_seconds>60:raise ValueError('native RAM capacity window exceeds retained 60-second contract')
    cost=SimulationCostModel(**document['cost_model']) if document['cost_model'] is not None else None
    return {**document,'symbols':tuple(sorted(symbols)),'policy':policy,'cost_model':cost}


def _model_json(value):
    from dataclasses import is_dataclass
    from kiwoom_monitor.application.position_target import _json_value
    return json.loads(json.dumps(value,default=lambda v:asdict(v) if is_dataclass(v) else _json_value(v),sort_keys=True,ensure_ascii=False,allow_nan=False))


def _allocation_selection_ref(selection):
    value={**selection,'policy':asdict(selection['policy']),
        'cost_model':selection['cost_model'].to_dict() if selection['cost_model'] else None}
    return 'allocation-selection:'+hashlib.sha256(json.dumps(_model_json(value),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def prepare_dynamic_allocation_model_publication(selection_document, rows, *, expected_selection_revision,
        position_studies=None):
    """Freeze already-prepared native receipts; no source read or HTTP send."""
    selection=compile_dynamic_allocation_ram_selection(selection_document)
    if type(expected_selection_revision) is not int or expected_selection_revision<1:
        raise ValueError('explicit applied selection revision required')
    rows=_model_json(rows)
    if position_studies is not None:
        if not isinstance(position_studies,dict) or set(position_studies)!=set(selection['symbols']):
            raise ValueError('v2 study publication requires exact selected symbols')
        rows=[{**row,'position_study':_model_json(position_studies[row['symbol']])} for row in rows]
    times=[selection['policy'].available_at];expiry=[selection['policy'].valid_until]
    for row in rows:
        for key in ('ev_receipt','exit_receipt'):
            evidence=row[key]['evidence']
            times.append(datetime.fromisoformat(evidence['available_at']));expiry.append(datetime.fromisoformat(evidence['valid_until']))
        if position_studies is not None:
            comparison=row['position_study']['comparison']
            times.append(datetime.fromisoformat(comparison['available_at']));expiry.append(datetime.fromisoformat(comparison['valid_until']))
    body=_model_json({'version':'dynamic-allocation-native-models/v2' if position_studies is not None else 'dynamic-allocation-native-models/v1','expected_selection_revision':expected_selection_revision,
        'selection_ref':_allocation_selection_ref(selection),'available_at':max(times),'valid_until':min(expiry),
        'rows':sorted(rows,key=lambda r:r['symbol']),'source_validation':'PC_READONLY_REPLAY_REPORTED',
        'source_durability':'not_verified','order_authorized':False})
    body['publication_ref']='allocation-model-publication:'+hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    decode_dynamic_allocation_model_publication(body,selection,expected_selection_revision)
    return body


def decode_dynamic_allocation_model_publication(document, selection, expected_revision):
    """One bounded replay on publication; runtime consumes frozen typed rows only.

    This checks reported native lineage/content and common replay consistency,
    not the PC database or raw capture durability, probability or order authority.
    """
    if isinstance(document,dict) and document.get('version')=='dynamic-allocation-native-models/v2':
        return _decode_dynamic_allocation_model_publication_v2(document,selection,expected_revision)
    from kiwoom_monitor.application.position_target import DynamicAllocationEvEvidence,FullExitStressEvidence
    from kiwoom_monitor.application.research_evaluation import (
        PositionActionOutcome,compare_position_action_outcomes,_position_action_estimate,_content_id,position_action_cost_model_ref)
    from kiwoom_monitor.application.research_execution import FullExitStressPolicy,FullExitStressScenario,SimulationExecutionConfig,SimulationCostModel,simulate_full_exit_stress
    from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
    from kiwoom_monitor.application.research_families import parse_strategy_config
    keys={'version','expected_selection_revision','selection_ref','available_at','valid_until','rows',
        'source_validation','source_durability','order_authorized','publication_ref'}
    if (not isinstance(document,dict) or set(document)!=keys
            or len(json.dumps(document,allow_nan=False).encode())>4*1024*1024
            or document['version']!='dynamic-allocation-native-models/v1'
            or type(document['expected_selection_revision']) is not int or document['expected_selection_revision']!=expected_revision
            or document['selection_ref']!=_allocation_selection_ref(selection)
            or document['order_authorized'] is not False or document['source_durability']!='not_verified'
            or document['source_validation']!='PC_READONLY_REPLAY_REPORTED'):
        raise ValueError('allocation publication scope/budget mismatch')
    body={k:v for k,v in document.items() if k!='publication_ref'}
    digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    if document['publication_ref']!='allocation-model-publication:'+digest:raise ValueError('allocation publication content mismatch')
    rows=document['rows']
    if (not isinstance(rows,list) or not 1<=len(rows)<=200
            or any(not isinstance(r,dict) for r in rows)
            or [r.get('symbol') for r in rows]!=list(selection['symbols'])):
        raise ValueError('allocation publication requires exact selected symbol coverage')
    cost=selection['cost_model']
    if cost is None:raise ValueError('allocation publication cost unknown')
    cost_ref=position_action_cost_model_ref(cost.to_dict());decoded={};times=[selection['policy'].available_at];expiry=[selection['policy'].valid_until]
    total_outcomes=total_bars=0;source_counts={}
    def evidence(raw,cls):
        raw=dict(raw)
        for key in ('available_at','valid_until'):raw[key]=datetime.fromisoformat(raw[key])
        if cls is DynamicAllocationEvEvidence:
            if not isinstance(raw.get('input_refs'),list):raise ValueError('model input refs require array')
            raw['input_refs']=tuple(raw['input_refs'])
        return cls(**raw)
    for row in rows:
        if set(row)!={'symbol','ev_receipt','exit_receipt'}:raise ValueError('unsupported allocation model row')
        er,xr=row['ev_receipt'],row['exit_receipt'];ev=evidence(er['evidence'],DynamicAllocationEvEvidence);ex=evidence(xr['evidence'],FullExitStressEvidence)
        if any(type(v) is not int or v<1 for v in (er['max_evidence_age_seconds'],xr['replay_inputs']['max_evidence_age_seconds'])):
            raise ValueError('native model age budget must be explicit integer')
        if (ev.symbol!=row['symbol'] or ex.symbol!=ev.symbol or ev.reference_price_won!=ex.reference_price_won
                or ev.cost_model_ref!=cost_ref or ex.cost_model_ref!=cost_ref or ev.target_quantity>ex.tested_position_quantity
                or ev.horizon_seconds!=selection['policy'].allocation_ev_horizon_seconds
                or ex.exit_window_seconds!=selection['policy'].allocation_exit_horizon_seconds
                or er['version']!='dynamic-allocation-native-ev/v1' or er['quality']['status']!='EMPIRICAL_ESTIMATE'
                or xr['version']!='dynamic-allocation-native-exit/v1' or xr['quality']!={'status':'CALCULATED','reasons':[]}
                or any(r['order_authorized'] is not False or r['source_durability']!='not_verified' for r in (er,xr))):
            raise ValueError('allocation models require matched native size/price/cost/horizon')
        outcomes=[]
        if not isinstance(er.get('outcomes'),list) or not 4<=len(er['outcomes'])<=200:raise ValueError('native EV outcomes required')
        for raw in er['outcomes']:
            value=dict(raw)
            for key in ('started_at','completed_at','available_at'):value[key]=datetime.fromisoformat(value[key])
            outcome=PositionActionOutcome(**value)
            if not outcome.input_ref.startswith('native-position-outcome_'):raise ValueError('native EV source ref required')
            outcomes.append(outcome)
        total_outcomes+=len(outcomes)
        sr=er['source_receipt']
        if (sr['version']!='native-position-outcome-source/v1' or sr['target_symbol']!=ev.symbol
                or sr['target_environment']!='mock' or sr['execution_environment']!='simulation'
                or sr['development_role'] not in {'TRAIN','VALIDATION'} or sr['excluded']
                or sr['requested_count']!=len(outcomes) or sr['accepted_count']!=len(outcomes)
                or not isinstance(sr['sources'],list) or not 1<=len(sr['sources'])<=200
                or sr['source_durability']!='not_verified' or sr['order_authorized'] is not False):raise ValueError('native EV source coverage mismatch')
        comparison=er['comparison'];cp=comparison['parameters'];targets=comparison['targets']
        if (comparison['status']!='COMPARABLE' or comparison['symbol']!=ev.symbol or comparison['order_authorized'] is not False
                or comparison['policy_version']!='position-action-comparison-shadow/v1'
                or comparison['execution_support']!='shadow_only'
                or set(cp)!={'baseline_action','horizon_seconds','population_ref','input_resolution','outcome_source_kind','execution_model_ref','minimum_samples'}
                or set(targets)!={'HOLD',ev.action} or comparison['decision_id']!=ev.comparison_ref
                or cp['baseline_action']!='HOLD' or cp['horizon_seconds']!=ev.horizon_seconds
                or cp['population_ref']!=ev.population_ref or cp['input_resolution']!='minute'
                or cp['outcome_source_kind']!='simulation'):
            raise ValueError('native EV comparison scope mismatch')
        cb={k:v for k,v in comparison.items() if k!='decision_id'}
        if ev.comparison_ref!='position-action-comparison-shadow:'+hashlib.sha256(json.dumps(cb,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest():
            raise ValueError('native EV comparison content mismatch')
        expected={}
        for action,target in targets.items():
            goal=ev.initial_quantity if action=='HOLD' else ev.target_quantity
            if (target['cost_model']!=cost.to_dict() or target['quality']['status']!='CALCULATED' or target['funding_shortfall_quantity']
                    or target['symbol']!=ev.symbol or target['actual_quantity']!=ev.initial_quantity
                    or target['pending_buy_quantity'] or target['pending_sell_quantity']
                    or target['target_quantity']!=goal or target['target_notional_won']!=goal*ev.reference_price_won):
                raise ValueError('native EV target funding/cost mismatch')
            expected[action]={'policy_version':target['target_policy_version'],'environment':'mock','venue':'KRX',
                'source_kind':'simulation','execution_model_ref':cp['execution_model_ref'],'initial_quantity':ev.initial_quantity,
                'pending_buy_quantity':0,'pending_sell_quantity':0,'target_quantity':ev.initial_quantity if action=='HOLD' else ev.target_quantity,
                'cost_model_ref':cost_ref,'cost_valid_from':cost.valid_from,'cost_valid_to':cost.valid_to,'reasons':()}
        calculated=compare_position_action_outcomes(tuple(outcomes),expected,baseline_action='HOLD',
            decision_time=datetime.fromisoformat(comparison['decision_time']),horizon_seconds=ev.horizon_seconds,
            population_ref=ev.population_ref,input_resolution='minute',minimum_samples=cp['minimum_samples'])
        if _model_json(calculated)!=comparison['actions']:raise ValueError('native EV paired replay mismatch')
        chosen=next(r for r in calculated if r['action']==ev.action)
        if (chosen['ev_status']!='EMPIRICAL_ESTIMATE' or chosen['paired_sample_count']*2!=len(outcomes)
                or chosen['excluded_counts'] or chosen['unpaired_sample_count'] or chosen['baseline_unpaired_sample_count']
                or ev.input_refs!=tuple(ref for ref,_ in chosen['evidence_refs'])):raise ValueError('native EV incomplete pairs')
        baseline={o.case_ref:o for o in outcomes if o.action=='HOLD'}
        stat=_position_action_estimate(tuple((o.gross_change_won-baseline[o.case_ref].gross_change_won,
            o.fee_tax_won-baseline[o.case_ref].fee_tax_won) for o in outcomes if o.action==ev.action),
            bps_notional_won=(ev.target_quantity-ev.initial_quantity)*ev.reference_price_won)
        ev_available=max(o.available_at for o in outcomes)
        ev_expiry=min(o.available_at+timedelta(seconds=er['max_evidence_age_seconds']) for o in outcomes)
        if cost.valid_to:ev_expiry=min(ev_expiry,datetime.fromisoformat(cost.valid_to))
        if targets[ev.action].get('target_sizing'):
            ev_expiry=min(ev_expiry,datetime.fromisoformat(targets[ev.action]['target_sizing']['parameters']['valid_until']))
        if ((ev.net_ev_bps,ev.ev_uncertainty_bps)!=(stat['mean_net_change_bps_floor'],stat['net_standard_error_bps_ceil'])
                or ev.available_at!=ev_available or ev.valid_until!=ev_expiry):raise ValueError('native EV original value/TTL mismatch')
        ri=xr['replay_inputs'];raw=dict(ri['policy'])
        raw['scenarios']=tuple(FullExitStressScenario(**{**s,'blocked_minute_offsets':tuple(s['blocked_minute_offsets'])}) for s in raw['scenarios'])
        for key in ('available_at','valid_until'):raw[key]=datetime.fromisoformat(raw[key])
        policy=FullExitStressPolicy(**raw);execution=SimulationExecutionConfig(**{**ri['execution'],'cost_model':SimulationCostModel(**ri['execution']['cost_model'])})
        if (policy.symbol!=ex.symbol or policy.tested_position_quantity!=ex.tested_position_quantity
                or policy.reference_price_won!=ex.reference_price_won or policy.exit_window_seconds!=ex.exit_window_seconds
                or xr['input_basis']!='native_market_bar_proofs_not_unfilled_trade_outcomes/v1'):
            raise ValueError('full-exit native subject mismatch')
        bars=tuple(KrxMinuteBarFrame(**b) for b in ri['bars']);waiting=tuple(KrxMinuteBarFrame(**b) for b in ri['waiting_bars']);source=KrxMinuteBarFrame(**ri['source_bar'])
        total_bars+=len(bars)+len(waiting)
        if execution.cost_model.to_dict()!=cost.to_dict():raise ValueError('full-exit replay cost mismatch')
        replay=simulate_full_exit_stress(policy,source,bars,execution=execution,
            strategy=parse_strategy_config(ri['strategy_family'],ri['strategy']),decision_time=datetime.fromisoformat(ri['decision_time']),waiting_bars=waiting)
        available=max(datetime.fromisoformat(ri['finished_at']),*(datetime.fromisoformat(b.available_at) for b in (source,*waiting,*bars)))
        expires=min(available+timedelta(seconds=ri['max_evidence_age_seconds']),policy.valid_until,datetime.fromisoformat(cost.valid_to))
        replay.update(available_at=available.isoformat(),valid_until=expires.isoformat())
        if (replay!=xr['result'] or replay['status']!='CALCULATED' or ex.available_at!=available or ex.valid_until!=expires
                or ex.stress_loss_bps!=replay['stress_loss_bps'] or ex.conservative_exit_capacity_won!=replay['conservative_exit_capacity_won']):
            raise ValueError('full-exit common replay/value/TTL mismatch')
        xs=xr['source_receipt']
        selection_keys={k:xs[k] for k in ('run_id','start_event_id','end_event_id')}
        if (xs['development_role'] not in {'TRAIN','VALIDATION'} or xs['bar_count']!=len(bars)
                or xs['waiting_bar_count']!=len(waiting) or ex.scenario_count!=len(policy.scenarios)
                or ex.source_ref!=_content_id('native-full-exit',_model_json({'selection':selection_keys,'policy':asdict(policy),'source':xs,'result':replay}))):
            raise ValueError('full-exit native source binding mismatch')
        for native in (*sr['sources'],xs):
            count=native['event_count']
            if type(count) is not int or not 1<=count<=10000:raise ValueError('native model event budget invalid')
            source_counts[native['run_id']]=max(source_counts.get(native['run_id'],0),count)
        if total_outcomes>2000 or total_bars>2000 or sum(source_counts.values())>10000:
            raise ValueError('allocation publication aggregate source/replay budget exceeded')
        decoded[row['symbol']]={'ev':ev,'exit':ex}
        times.extend((ev.available_at,ex.available_at));expiry.extend((ev.valid_until,ex.valid_until))
    if datetime.fromisoformat(document['available_at'])!=max(times) or datetime.fromisoformat(document['valid_until'])!=min(expiry):
        raise ValueError('allocation publication original TTL mismatch')
    if min(expiry)<=max(times):raise ValueError('allocation model sources have no common validity period')
    return {'rows':decoded,'publication_ref':document['publication_ref'],'available_at':max(times),'valid_until':min(expiry),
        'selection_ref':document['selection_ref'],'source_validation':'PC_READONLY_REPLAY_REPORTED'}


def _decode_dynamic_allocation_model_publication_v2(document,selection,expected_revision):
    """Keep v1 allocation receipts intact and bind a separate reported v2 study."""
    from kiwoom_monitor.application.research_evaluation import (
        decode_position_action_study_publication,decode_dynamic_allocation_ev_source_publication,audit_position_study_ev_lineage)
    keys={'version','expected_selection_revision','selection_ref','available_at','valid_until','rows',
          'source_validation','source_durability','order_authorized','publication_ref'}
    if (set(document)!=keys or len(json.dumps(document,allow_nan=False).encode())>4*1024*1024
            or not isinstance(document['rows'],list) or not 1<=len(document['rows'])<=200
            or any(not isinstance(row,dict) or set(row)!={'symbol','ev_receipt','exit_receipt','position_study'} for row in document['rows'])):
        raise ValueError('invalid bounded v2 allocation publication')
    digest=hashlib.sha256(json.dumps({k:v for k,v in document.items() if k!='publication_ref'},
        sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    if document['publication_ref']!='allocation-model-publication:'+digest:raise ValueError('v2 allocation content mismatch')
    rows=[{k:v for k,v in row.items() if k!='position_study'} for row in document['rows']]
    times=[selection['policy'].available_at];expiry=[selection['policy'].valid_until]
    counts={};study_bars={};outcomes=bars=0
    for row in rows:
        for key in ('ev_receipt','exit_receipt'):
            evidence=row[key]['evidence'];times.append(datetime.fromisoformat(evidence['available_at']));expiry.append(datetime.fromisoformat(evidence['valid_until']))
        outcomes+=len(row['ev_receipt']['outcomes'])
        ri=row['exit_receipt']['replay_inputs'];bars+=len(ri['bars'])+len(ri['waiting_bars'])
        for source in (*row['ev_receipt']['source_receipt']['sources'],row['exit_receipt']['source_receipt']):
            counts[source['run_id']]=max(counts.get(source['run_id'],0),source['event_count'])
    # Reuse the full existing decoder once; only the packet envelope is projected.
    legacy={**document,'version':'dynamic-allocation-native-models/v1','rows':rows,
        'available_at':max(times).isoformat(),'valid_until':min(expiry).isoformat()}
    legacy['publication_ref']='allocation-model-publication:'+hashlib.sha256(json.dumps(
        {k:v for k,v in legacy.items() if k!='publication_ref'},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    decoded=decode_dynamic_allocation_model_publication(legacy,selection,expected_revision)
    for row in document['rows']:
        study=decode_position_action_study_publication(row['position_study']);comparison=study['comparison']
        if (comparison['symbol']!=row['symbol'] or study['study'].horizon_seconds!=selection['policy'].allocation_ev_horizon_seconds
                or any(case['original_goal']['cost_model']!=selection['cost_model'].to_dict() for case in comparison['cases'])):
            raise ValueError('v2 study selected symbol/cost/horizon mismatch')
        for run_id,count in study['source_counts'].items():counts[run_id]=max(counts.get(run_id,0),count)
        for run_id,count in study['source_bar_counts'].items():study_bars[run_id]=max(study_bars.get(run_id,0),count)
        ev_publication=row['ev_receipt'].get('native_source_publication')
        if 'native_source_publication' in row['ev_receipt']:
            ev_source=decode_dynamic_allocation_ev_source_publication(ev_publication)
            if (ev_source['outcomes']!=row['ev_receipt']['outcomes']
                    or ev_source['source_receipt']!=row['ev_receipt']['source_receipt']
                    or datetime.fromisoformat(ev_publication['decision_time'])!=datetime.fromisoformat(row['ev_receipt']['comparison']['decision_time'])):
                raise ValueError('v2 allocation EV source is not its original receipt')
            for run_id,count in ev_source['source_counts'].items():counts[run_id]=max(counts.get(run_id,0),count)
            study['allocation_lineage']=audit_position_study_ev_lineage(study,ev_source)
        outcomes+=len(comparison['cases'])
        if outcomes>2000 or bars+sum(study_bars.values())>2000 or sum(counts.values())>10000:
            raise ValueError('v2 allocation aggregate source/replay budget exceeded')
        decoded['rows'][row['symbol']]['position_study']=study
        times.append(datetime.fromisoformat(comparison['available_at']));expiry.append(datetime.fromisoformat(comparison['valid_until']))
    if (datetime.fromisoformat(document['available_at'])!=max(times)
            or datetime.fromisoformat(document['valid_until'])!=min(expiry) or max(times)>=min(expiry)):
        raise ValueError('v2 allocation original validity mismatch')
    return {**decoded,'publication_ref':document['publication_ref'],'available_at':max(times),'valid_until':min(expiry),
        'version':'dynamic-allocation-native-models/v2'}


def position_study_current_goal_binding(model,request,target,observation,receipt,selection,*,current_support_observation=None):
    """Bind an existing RAM goal/Risk cut; unsupported native states stay missing."""
    from kiwoom_monitor.application.position_target import PositionAccountObservation,PositionTargetShadowRequest
    from kiwoom_monitor.application.mock_automation_risk import (
        MockAutomationRiskSnapshot,VERIFIED_DAILY_PNL_SOURCE,mock_automation_risk_snapshot_from_dict)
    from kiwoom_monitor.application.research_evaluation import position_action_cost_model_ref
    if not isinstance(request,PositionTargetShadowRequest):raise ValueError('typed current study goal required')
    study,comparison=model['study'],model['comparison'];cut=request.decision_time;policy=request.target_policy
    reasons=['historical_exact_context_proof_unavailable','allocation_input_lineage_independence_unverified',
        'current_position_protection_state_unavailable','current_registered_continuation_binding_unavailable']
    lineage=model.get('allocation_lineage')
    if lineage is not None and lineage['status']=='REPORTED_OVERLAP':
        reasons.append('allocation_study_reported_source_overlap')
    historical=model.get('historical_contexts')
    historical_summary=None
    if historical:
        historical_summary={'context_refs':[item['context_ref'] for item in historical],
            'distinct_context_count':len(historical),
            'covered_case_count':len({ref for item in historical for ref in item['case_refs']}),
            'declared_case_count':len({item['selection']['case_ref'] for item in comparison['cases']}),
            'market_statuses':sorted({item['market']['quality']['status'] for item in historical}),
            'source_window_truncated':any(item['source_window_truncated'] for item in historical),
            'observed_theme_ids':sorted({m['theme_id'] for item in historical for m in item['observed_memberships']}),
            'current_context_support_verified':False,'source_durability':'not_verified','order_authorized':False}
        reasons.append('historical_context_observed_but_current_support_unverified')
    expiry=[datetime.fromisoformat(comparison['valid_until']),datetime.fromisoformat(receipt['valid_until'])]
    available=[datetime.fromisoformat(comparison['available_at']),datetime.fromisoformat(receipt['available_at'])]
    if not datetime.fromisoformat(comparison['available_at'])<=cut<datetime.fromisoformat(comparison['valid_until']):
        reasons.append('study_model_expired_or_future')
    if (comparison['r4_status']!='COHORT_COMPARABLE' or request.symbol!=comparison['symbol']
            or request.action!=comparison['action'] or request.policy_version!=study.source_policy_version
            or policy is None or tuple(getattr(policy,k) for k in
                ('version','target_notional_won','max_position_notional_won','max_position_quantity','cash_buffer_won'))
                !=tuple(getattr(study,k) for k in
                ('sizing_version','target_notional_won','max_position_notional_won','max_position_quantity','cash_buffer_won'))
            or request.max_snapshot_age_seconds!=study.max_snapshot_age_seconds):
        reasons.append('current_study_sizing_or_action_mismatch')
    if policy is not None:
        expiry.append(policy.valid_until);available.append(policy.available_at)
        if (not policy.available_at<=cut<policy.valid_until
                or (policy.valid_until-policy.available_at).total_seconds()>study.max_decision_age_seconds):
            reasons.append('current_goal_lease_invalid')
    expected=comparison['result']
    if (target.get('quality',{}).get('status')!='CALCULATED' or target.get('funding_shortfall_quantity')
            or target.get('actual_quantity')!=expected['initial_quantity']
            or target.get('target_quantity')!=expected['target_quantity']
            or target.get('pending_buy_quantity') or target.get('pending_sell_quantity')
            or request.reference_price_won!=expected['reference_price_won']
            or request.cost_model is None or position_action_cost_model_ref(request.cost_model.to_dict())!=expected['cost_model_ref']):
        reasons.append('current_goal_quantity_price_cost_or_pending_mismatch')
    account_bound=False;risk_ref=None
    if isinstance(observation,PositionAccountObservation):
        account=observation.account;risk=observation.risk_snapshot
        available.extend((observation.observed_at,account.as_of,request.reference_price_available_at))
        expiry.extend(at+timedelta(seconds=request.max_snapshot_age_seconds) for at in
            (observation.observed_at,account.as_of,request.reference_price_available_at))
        if isinstance(risk,MockAutomationRiskSnapshot):
            mock_automation_risk_snapshot_from_dict(risk.to_dict());risk_ref=risk.snapshot_id
            available.append(risk.observed_at)
            expiry.extend(at+timedelta(seconds=request.max_snapshot_age_seconds) for at in (risk.observed_at,risk.account_as_of))
            account_bound=(account.account_ref==request.account_ref==selection['policy'].account_ref==risk.account_ref
                and risk.execution_run_id==selection['expected_execution_run_id']
                and risk.binding_revision==selection['expected_binding_revision'] and risk.data_path==selection['data_path']
                and risk.account_as_of==account.as_of and risk.trading_date==cut.astimezone(ZoneInfo('Asia/Seoul')).date()
                and receipt.get('account_input_ref')==observation.input_ref==request.account_input_ref==request.orders_input_ref
                and receipt.get('risk_snapshot_ref')==risk.snapshot_id
                and observation.reconciliation_complete and observation.open_orders_complete
                and not observation.reasons and not observation.unmapped_broker_order_ids
                and risk.reconciliation_complete and not risk.submission_unknown_count and not risk.balance_mismatch_count
                and risk.cost_complete and risk.daily_net_pnl_source==VERIFIED_DAILY_PNL_SOURCE
                and risk.daily_net_pnl_won is not None and risk.daily_net_pnl_won> -selection['maximum_daily_loss_won']
                and receipt['risk_disposition']=='ALLOW')
    if not account_bound:reasons.append('current_account_risk_binding_unverified')
    if receipt['risk_disposition']!='ALLOW' or receipt.get('protection_required'):
        reasons.append('native_risk_preempts_position_study')
    owner_facts=None;validated_state=None;family_match=None
    raw_state=getattr(observation,'runner_state',None)
    if raw_state is not None:
        from kiwoom_monitor.application.position_target import validate_position_runner_state
        try:
            state=validate_position_runner_state(raw_state,observation)
            state_at=datetime.fromisoformat(raw_state['state_available_at'])
            if state_at>cut:raise ValueError('native state from future')
            holding=None
            if state.status=='open':
                holding=int((cut-datetime.fromisoformat(state.opened_at)).total_seconds())
                if holding<0:raise ValueError('native opened time from future')
            source=model.get('source_continuation',{})
            family_match=(raw_state['family']==source.get('family') and raw_state['parameters']==source.get('parameters')
                and raw_state['session_profile']==source.get('session_profile',{}).get('profile')
                and raw_state['registered_implementation_hash']==source.get('code_hash'))
            notice=raw_state['protection_review'];pending=notice is not None and notice['state']=='PENDING'
            clock=raw_state['session_clock'];clock_pending=clock is not None and clock.get('phase')!='COMPLETED'
            owner_facts={'state_ref':raw_state['state_ref'],'fill_cursor':raw_state['fill_cursor'],
                'state_available_at':raw_state['state_available_at'],'entry_price_won':state.entry_price,
                'owned_quantity':state.position_quantity,'opened_at':state.opened_at,
                'holding_age_seconds':holding,'family':raw_state['family'],'family_scope_matches':family_match,
                'protection_pending':pending,'session_clock_pending':clock_pending}
            available.append(state_at)
            reasons.remove('current_position_protection_state_unavailable')
            reasons.remove('current_registered_continuation_binding_unavailable')
            reasons.extend(('native_protection_model_support_unverified','native_execution_continuation_support_unverified'))
            if not family_match:reasons.append('current_registered_family_model_mismatch')
            if pending or clock_pending:reasons.append('native_protection_preempts_position_study')
            validated_state=state
        except (ValueError,TypeError,KeyError):
            family_match=None
            reasons.append('native_runner_state_invalid_or_cut_mismatch')
    if not max(available)<=cut<min(expiry):
        reasons.append('current_goal_input_expired_or_future');account_bound=False
    # These are support dimensions, not an approval inferred from matching
    # prices, a normal current Risk snapshot, or a replayable historical mean.
    # Exact means the complete recorded state, including absolute state times;
    # no age/price/quantity interpolation or new equivalence rule is introduced.
    case_states=[];matched_cases=[]
    for case in comparison['cases']:
        if case['selection']['action']!=comparison['action']:continue
        proof=case['decision_proof'];source_state=proof['strategy_state']
        case_states.append({'case_ref':case['selection']['case_ref'],
            'case_evidence_ref':case['evidence_ref'],'proof_event_ref':case['proof_event_ref'],
            'status':source_state['status']})
        if validated_state is not None and _model_json(validated_state.to_dict())==source_state:
            matched_cases.append(case['selection']['case_ref'])
    if validated_state is not None and not matched_cases:
        reasons.append('current_state_outside_exact_observed_cases')
    source=model.get('source_continuation',{})
    support={'version':'position-study-model-support/v1',
        'historical_comparison':{'status':'SUPPORTED' if comparison['r4_status']=='COHORT_COMPARABLE' else 'UNAVAILABLE',
            'evidence_ref':comparison['evidence_ref'],'scope':'declared_paired_minute_study_only',
            'usable_for_current_goal':False},
        'declared_scope':{'study_policy_ref':comparison['study_policy_ref'],
            'source_policy_version':study.source_policy_version,'actions':list(study.actions),
            'horizon_seconds':study.horizon_seconds,'context_support_rule':study.context_support_rule,
            'continuation_rule':study.continuation_rule,
            'goal_scope':{key:expected[key] for key in ('initial_quantity','target_quantity',
                'reference_price_won','cost_model_ref','horizon_seconds')},
            'input_resolutions':sorted({case['outcome']['input_resolution'] for case in comparison['cases']}),
            'execution_model':_model_json(source.get('execution_model')),
            'observed_case_states':_model_json(case_states)},
        'current_state':{'status':'UNAVAILABLE' if validated_state is None else
            'EXACT_OBSERVED_CASE' if matched_cases else 'OUTSIDE_OBSERVED_CASES',
            'matched_case_refs':sorted(matched_cases),'registered_family_matches':family_match,
            'equivalence_verified':False},
        'current_goal':{'status':'BLOCKED','usable':False,
            'checks':{'account_risk_bound':bool(account_bound),
                'goal_policy_scope_matches':'current_study_sizing_or_action_mismatch' not in reasons,
                'quantity_price_cost_pending_match':'current_goal_quantity_price_cost_or_pending_mismatch' not in reasons,
                'input_lease_current':max(available)<=cut<min(expiry)},
            'reasons':sorted(set(reasons))},
        'native_execution':{'status':'UNVERIFIED',
            'reasons':['native_protection_model_support_unverified','native_execution_continuation_support_unverified'],
            'reentry_admission_verified':False},
        'order_authorized':False}
    if study.version in {'position-action-study/v3','position-action-study/v4'}:
        from kiwoom_monitor.application.research_evaluation import evaluate_position_study_conditional_shadow
        if study.version=='position-action-study/v4':support['native_execution']['equivalence_verified']=False
        support['version']='position-study-model-support/v2'
        conditional={'status':'UNAVAILABLE','result':None,
            'reasons':['current_closed_minute_support_observation_unavailable'],
            'usable_for_native_selection':False,'order_authorized':False}
        if current_support_observation is not None:
            current_checks=support['current_goal']['checks']
            raw=current_support_observation
            if (not all(current_checks.values()) or not family_match or validated_state is None
                    or owner_facts['protection_pending'] or owner_facts['session_clock_pending']
                    or receipt.get('protection_required')):
                conditional['reasons']=['conditional_current_native_binding_or_protection_invalid']
            elif (not isinstance(raw,dict) or set(raw)!={'source_bar','strategy_state','portfolio','source_continuation'}
                    or not isinstance(raw['portfolio'],dict)
                    or _model_json(validated_state.to_dict())!=raw['strategy_state']
                    or raw['portfolio'].get('cash_won')!=observation.account.available_cash_won
                    or raw['portfolio'].get('reserved_cash_won')!=observation.account.reserved_open_buy_won):
                conditional['reasons']=['conditional_current_owner_observation_mismatch']
            else:
                native_policy=None
                if study.version=='position-action-study/v4':
                    from kiwoom_monitor.application.mock_automation_risk import _sha256
                    copied=getattr(observation,'native_study_policy',None)
                    keys={'version','source_kind','account_input_ref','risk_snapshot_ref','state_ref','policy',
                        'order_authorized','policy_observation_ref'}
                    if (isinstance(copied,dict) and set(copied)==keys and isinstance(copied['policy'],dict)
                            and copied['version']=='native-position-study-policy/v1' and copied['source_kind']=='owner_ram'
                            and copied['account_input_ref']==observation.input_ref
                            and copied['risk_snapshot_ref']==risk_ref and copied['state_ref']==raw_state['state_ref']
                            and copied['order_authorized'] is False
                            and copied['policy_observation_ref']=='native-position-study-policy:'+_sha256(
                                {k:v for k,v in copied.items() if k!='policy_observation_ref'})
                            and all(copied['policy'].get(k)==v for k,v in raw_state['native_risk_limits'].items())):
                        native_policy=copied['policy']
                conditional=evaluate_position_study_conditional_shadow(model,request,target,**raw,
                    account_snapshot=observation.account,broker_open_orders=observation.broker_open_orders,
                    **({'native_policy':native_policy} if study.version=='position-action-study/v4' else {}))
        support['conditional_shadow']=_model_json(conditional)
    return {'version':'position-study-current-goal-binding/v2','symbol':request.symbol,'action':request.action,
        'study_policy_ref':comparison['study_policy_ref'],'model_evidence_ref':comparison['evidence_ref'],
        'goal_decision_ref':target['decision_id'],'goal_policy_ref':target['target_policy_version'],
        'upper_allocation_decision_ref':request.source_decision_ref,'account_input_ref':observation.input_ref if isinstance(observation,PositionAccountObservation) else None,
        'risk_snapshot_ref':risk_ref,'current_account_risk_bound':bool(account_bound),
        **({'native_owner_facts':owner_facts} if owner_facts is not None else {}),
        **({'allocation_lineage':_model_json(lineage)} if lineage is not None else {}),
        **({'historical_context_summary':historical_summary} if historical_summary is not None else {}),
        'model_support':support,
        'available_at':max(available).isoformat(),'valid_until':min(expiry).isoformat(),
        'status':'EV_UNAVAILABLE','result':None,'reasons':sorted(set(reasons)),
        'source_durability':'not_verified','order_authorized':False}


def freeze_dynamic_allocation_input_bundle(receipt,models,selection):
    """Bind already computed current inputs and original model refs; no new owner."""
    cut=datetime.fromisoformat(receipt['decision_time'])
    facts=receipt['facts'];symbols=set(selection['symbols'])
    def all_facts(keys):
        return len(facts)==len(symbols) and {f['symbol'] for f in facts}==symbols and all(
            all(f.get(key) is not None for key in keys) for f in facts)
    context_keys=tuple(key for weight,key in zip(selection['policy'].context_weights,
        ('market_strength_bps','theme_strength_bps','stock_strength_bps')) if weight)
    checks={
        'account_risk_cut':bool(receipt.get('account_input_ref') and receipt.get('risk_snapshot_ref')),
        'quote_market_cap':all_facts(('reference_price_won','market_cap_won','price_available_at')),
        'turnover_volatility':all_facts(('recent_turnover_won','observed_close_return_rms_bps','window_input_ref','window_start','window_end')),
        'market_theme_stock':all_facts((*context_keys,'context_basis'))
            and all(f['theme_ids'] for f in facts),
        'ev_exit_stress':all_facts(('net_ev_bps','ev_uncertainty_bps','conservative_exit_capacity_won','stress_loss_bps')),
        'reported_original_model_sources':True,
        'original_cut_and_scope':(models['selection_ref']==_allocation_selection_ref(selection)
            and receipt.get('model_publication_ref')==models['publication_ref']
            and datetime.fromisoformat(receipt['available_at'])<=cut<datetime.fromisoformat(receipt['valid_until'])
            and models['available_at']<=cut<models['valid_until'])}
    rows=[]
    for symbol in sorted(symbols):
        row=models['rows'].get(symbol)
        if row is None:
            checks['reported_original_model_sources']=False;continue
        ev,exit=row['ev'],row['exit'];study=row.get('position_study',{});lineage=study.get('allocation_lineage',{})
        source_ref=lineage.get('ev_source_publication_ref')
        if not source_ref or not study.get('publication_ref'):checks['reported_original_model_sources']=False
        rows.append({'symbol':symbol,'ev_comparison_ref':ev.comparison_ref,'ev_population_ref':ev.population_ref,
            'ev_outcome_count':len(ev.input_refs),'ev_outcome_refs_hash':hashlib.sha256(json.dumps(
                list(ev.input_refs),separators=(',',':'),ensure_ascii=False).encode()).hexdigest(),
            'ev_source_publication_ref':source_ref,'study_publication_ref':study.get('publication_ref'),
            'exit_source_ref':exit.source_ref,'ev_available_at':ev.available_at.isoformat(),'ev_valid_until':ev.valid_until.isoformat(),
            'exit_available_at':exit.available_at.isoformat(),'exit_valid_until':exit.valid_until.isoformat(),
            'ev_method':'paired_empirical_incremental_portfolio_delta/v1','trained_predictor_used':False,
            'historical_context_refs':[item['context_ref'] for item in study.get('historical_contexts',())]})
    source={'decision_time':receipt['decision_time'],'available_at':receipt['available_at'],'valid_until':receipt['valid_until'],
        'account_ref':receipt['account_ref'],'account_input_ref':receipt.get('account_input_ref'),
        'risk_snapshot_ref':receipt.get('risk_snapshot_ref'),'risk_disposition':receipt['risk_disposition'],
        'input_refs':receipt['input_refs'],'facts':facts,'quality':receipt['quality']}
    source_ref='dynamic-allocation-input-cut:'+hashlib.sha256(json.dumps(source,
        sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    ready=all(checks.values()) and receipt['quality']['status']=='CALCULATED'
    body={'version':'dynamic-allocation-input-bundle/v1','selection_ref':models['selection_ref'],
        'model_publication_ref':models['publication_ref'],'current_cut_ref':source_ref,'decision_time':receipt['decision_time'],
        'available_at':receipt['available_at'],'valid_until':receipt['valid_until'],'checks':checks,'models':rows,
        'status':'INPUTS_BOUND' if ready else 'INPUTS_UNAVAILABLE',
        'reasons':sorted([key for key,value in checks.items() if not value]+receipt['quality']['reasons']),
        'model_application_verified':False,'source_durability':'not_verified','order_authorized':False}
    body['bundle_ref']='dynamic-allocation-input-bundle:'+hashlib.sha256(json.dumps(body,
        sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    if len(json.dumps(body,ensure_ascii=False).encode())>256*1024:
        # Optional provenance cannot change an already calculated amount or its lease.
        return {'version':body['version'],'status':'INPUTS_UNAVAILABLE','reasons':['input_bundle_budget_exceeded'],
            'model_application_verified':False,'source_durability':'not_verified','order_authorized':False}
    return body


def _position_study_observation_from_ram(model,request,observation,snapshot):
    """Join the completed bar for the legacy exact Paper comparison only.

    A shared cut can contain both current 0B receipts and completed minutes.
    Its live resolution does not invalidate the separate minute observation;
    the existing exact-comparison checks below remain scoped to that study.
    """
    from kiwoom_monitor.application.mock_automation_risk import _sha256
    from kiwoom_monitor.application.research_replay import replay_krx_minute_bars
    basis=getattr(observation,'study_portfolio',None);state=getattr(observation,'runner_state',None)
    if not isinstance(basis,dict) or not isinstance(state,dict):
        return None,['current_account_study_basis_unavailable'],[]
    risk=observation.risk_snapshot
    if (basis.get('version')!='native-position-study-portfolio/v1' or basis.get('order_authorized') is not False
            or basis.get('portfolio_ref')!='native-position-study-portfolio:'+_sha256({k:v for k,v in basis.items() if k!='portfolio_ref'})
            or risk is None or basis.get('risk_snapshot_ref')!=risk.snapshot_id
            or basis.get('account_input_ref')!=observation.input_ref
            or basis.get('cost_evidence_revision')!=risk.cost_evidence_revision
            or basis.get('fill_cursor')!=state['fill_cursor']
            or basis.get('available_at')!=observation.observed_at.isoformat()):
        return None,['current_account_study_basis_cut_mismatch'],[]
    rows=[r for r in snapshot.get('closed_minute_observations',()) if r.get('code')==request.symbol and r.get('market')=='KRX']
    if len(rows)!=1:return None,['current_closed_minute_support_observation_unavailable'],[]
    row=rows[0];cut=request.decision_time
    start=datetime.fromisoformat(row['trading_date']+'T'+row['minute']).replace(tzinfo=ZoneInfo('Asia/Seoul'))
    at=datetime.fromtimestamp(row['available_at'],tz=ZoneInfo('Asia/Seoul'))
    since=snapshot['continuous_from'].get((request.symbol,'KRX'));gap=snapshot.get('gap_at')
    if (row.get('durable') is not False or row.get('capture_quality')!='complete'
            or (request.symbol,'KRX') not in snapshot['approved_sources'] or since is None or since>start
            or gap is not None and gap>start or not start+timedelta(minutes=1)<=at<=cut
            or not 0<=(cut-at).total_seconds()<request.max_snapshot_age_seconds):
        return None,['current_closed_minute_support_quality_or_continuity_invalid'],[]
    if cut.microsecond or at.microsecond:
        return None,['current_study_timestamp_precision_outside_declared_profile'],[]
    if row['close']!=request.reference_price_won or at!=request.reference_price_available_at:
        return None,['current_closed_minute_quote_cut_mismatch'],[]
    ref=f"collector-minute:{snapshot['source_id']}:{row['operation_id']}"
    payload={key:row[key] for key in ('code','open','high','low','close','volume','trade_value_million_won','capture_quality','finalization_source')}
    payload.update(market='KRX',bar_start=start.isoformat(),bar_end=(start+timedelta(minutes=1)).isoformat(),window_closed=True)
    source={'kind':'minute_bar','venue':'KRX','subject':request.symbol+':KRX','revision_id':ref,
        'observation_key':start.isoformat(),'available_at':at.isoformat(),'value_kind':'actual','completeness':'complete',
        'payload':payload}
    frames=replay_krx_minute_bars((source,),as_of=cut,session_profile='krx-regular/v1')
    if len(frames)!=1:return None,['current_closed_minute_session_unsupported'],[]
    raw={'source_bar':_model_json(asdict(frames[0])),'strategy_state':_model_json(state['strategy_state']),
        'portfolio':_model_json(basis['portfolio']),'source_continuation':_model_json(model['source_continuation'])}
    return raw,[],[ref,basis['portfolio_ref'],state['state_ref']]


def dynamic_allocation_inputs_from_ram(selection, snapshot, contexts, observation, models=None, *, theme_memberships=None):
    """Borrow existing facts once. Missing allocation EV/exit/stress models stay unknown.

    No trade reaggregation outside the existing RAM cut, DB/REST, model inference,
    missing-second imputation or order authority belongs in this adapter.
    """
    from kiwoom_monitor.application.mock_automation_risk import (
        MockAutomationRiskSnapshot, VERIFIED_DAILY_PNL_SOURCE, mock_automation_risk_snapshot_from_dict)
    from kiwoom_monitor.application.position_target import PositionAccountObservation
    snapshot=_prepared_ram_snapshot(snapshot)
    policy=selection['policy'];cut=datetime.fromisoformat(snapshot['decision_time'])
    age=policy.max_snapshot_age_seconds;reasons=[];expires=[policy.valid_until];source_times=[policy.available_at]
    receipt={'version':'dynamic-position-allocation-ram/v1','policy_ref':policy.policy_ref,
        'account_ref':policy.account_ref,'decision_time':cut.isoformat(),'result':None,'facts':[],
        'input_refs':[],'risk_disposition':'HALT_NEW_ORDERS','protection_required':False,
        'source_durability':'not_verified','order_authorized':False,
        'coverage':'selected_KRX_symbols_and_TOP20_context',
        'missing_producers':['allocation_ev','conservative_full_exit_capacity','stress_loss_model']}
    if snapshot.get('input_resolution') == '0B':
        receipt['input_resolution'] = '0B'
        receipt['observation_contract'] = 'individual-0B-price-volume/v1'
    def fresh(at,label):
        at=datetime.fromisoformat(at) if isinstance(at,str) else at
        if at.tzinfo is None or not 0<=(cut-at).total_seconds()<age:reasons.append(label+'_cut_invalid')
        expires.append(at+timedelta(seconds=age));source_times.append(at);return at
    if snapshot.get('durable') is not False or cut.tzinfo is None:raise ValueError('allocation RAM source is invalid')
    if not policy.available_at<=cut<policy.valid_until:reasons.append('allocation_policy_not_current')
    if selection['cost_model'] is None:reasons.append('allocation_buy_cost_unknown')
    if not isinstance(observation,PositionAccountObservation) or observation.account.account_ref!=policy.account_ref:
        reasons.append('native_account_observation_unavailable')
    else:
        receipt['input_refs'].append(observation.input_ref);receipt['account_input_ref']=observation.input_ref
        fresh(observation.observed_at,'account_observation');fresh(observation.account.as_of,'account')
        if (not observation.reconciliation_complete or not observation.open_orders_complete
                or observation.reasons or observation.unmapped_broker_order_ids):reasons.append('native_account_order_projection_unverified')
        needed={s for s,q in observation.account.positions.items() if q}
        needed.update(r.intent.symbol for r in observation.open_orders)
        needed.update(r.symbol for r in observation.broker_open_orders or ())
        if not needed<=set(selection['symbols']):reasons.append('held_or_pending_symbol_not_selected')
        risk=observation.risk_snapshot
        if not isinstance(risk,MockAutomationRiskSnapshot):reasons.append('native_risk_unavailable')
        else:
            try:
                mock_automation_risk_snapshot_from_dict(risk.to_dict())
                if (risk.account_ref!=policy.account_ref or risk.execution_run_id!=selection['expected_execution_run_id']
                        or risk.binding_revision!=selection['expected_binding_revision'] or risk.data_path!=selection['data_path']
                        or risk.account_as_of!=observation.account.as_of
                        or risk.trading_date!=cut.astimezone(ZoneInfo('Asia/Seoul')).date()):raise ValueError('scope')
                fresh(risk.observed_at,'native_risk');receipt['input_refs'].append(risk.snapshot_id)
                receipt['risk_snapshot_ref']=risk.snapshot_id
                disposition='ALLOW'
                if (not 0<=(cut-risk.observed_at).total_seconds()<age
                        or not 0<=(cut-risk.account_as_of).total_seconds()<age
                        or not risk.reconciliation_complete or risk.submission_unknown_count or risk.balance_mismatch_count):
                    disposition='HALT_NEW_ORDERS';reasons.append('native_risk_reconciliation_or_unknown')
                elif not risk.cost_complete or risk.daily_net_pnl_won is None or risk.daily_net_pnl_source!=VERIFIED_DAILY_PNL_SOURCE:
                    disposition='NO_NEW_RISK';reasons.append('native_risk_cost_or_pnl_unknown')
                elif risk.daily_net_pnl_won<=-selection['maximum_daily_loss_won']:
                    disposition='EXIT_REQUIRED';receipt['protection_required']=True
                receipt['risk_disposition']=disposition
            except (ValueError,TypeError,KeyError):reasons.append('native_risk_scope_or_content_invalid')
    references={r['code']:r for r in snapshot.get('trade_references',()) if r.get('market')=='KRX'}
    approved=set(snapshot['approved_sources']);start=cut-timedelta(seconds=policy.capacity_window_seconds)
    # Optional weighted consumers borrow membership from its existing owner,
    # independently of a leadership window. The full-context v1 path is unchanged.
    independent_context = theme_memberships is not None and 0 in policy.context_weights
    memberships={}
    if theme_memberships is not None:
        from kiwoom_monitor.application.theme_leadership import ThemeMembership
        if (not isinstance(theme_memberships,tuple) or len(theme_memberships)>32
                or any(not isinstance(m,ThemeMembership) for m in theme_memberships)):
            raise ValueError('bounded typed native theme memberships required')
        for membership in theme_memberships:
            if membership.theme_id in memberships and memberships[membership.theme_id]!=membership:
                raise ValueError('ambiguous native theme membership')
            if datetime.fromisoformat(membership.available_at)>cut:
                reasons.append('native_theme_membership_from_future');continue
            memberships[membership.theme_id]=membership
    prepared_contexts=[]
    for context in contexts:
        if (context.get('policy_version')!='market-theme-shadow/v1' or context.get('order_authorized') is not False
                or context.get('scope',{}).get('leadership_venue')!='KRX'
                or context.get('scope',{}).get('market_universe')!='TOP20'):continue
        body={k:v for k,v in context.items() if k!='revision_id'}
        digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        if context.get('revision_id')!='market-theme-shadow:'+digest:reasons.append('context_content_invalid');continue
        members=context['leadership']['facts'].get('members',())
        member_map={m['code']:m for m in members}
        if not independent_context and not set(member_map)&set(selection['symbols']):continue
        if independent_context:
            if datetime.fromisoformat(context['decision_time'])>cut or datetime.fromisoformat(context['available_at'])>cut:
                reasons.append('context_from_future');continue
            used=(context['market'],) if policy.context_weights[0] else ()
            if policy.context_weights[1] or policy.context_weights[2]:used+=(context['leadership'],)
            for component in used:
                fresh(component['available_at'],'context_component')
                expires.append(datetime.fromisoformat(component['expires_at']))
                if cut>=datetime.fromisoformat(component['expires_at']):reasons.append('context_not_complete_or_expired')
            if (policy.context_weights[1] or policy.context_weights[2]) and context['leadership']['quality']['status']!='COMPLETE':
                reasons.append('context_not_complete_or_expired')
        else:
            fresh(context['available_at'],'context')
            expiry=datetime.fromisoformat(context['expires_at']);expires.append(expiry)
            if cut>=expiry or context['leadership']['quality']['status']!='COMPLETE':reasons.append('context_not_complete_or_expired')
        breadth=context['market']['facts'].get('top20_breadth_ppm')
        theme_strength=sum(m['return_bps']>0 for m in members)*10000//len(members) if members else None
        prepared_contexts.append((context,member_map,breadth,theme_strength))
    window_rows={};incomplete_symbols=set()
    for row in _ram_price_rows(snapshot,selection['symbols'],'KRX'):
        if row['market']!='KRX' or row['code'] not in selection['symbols']:continue
        at=_ram_row_time(row,snapshot)
        if start<at<=cut and row['available_at']<=cut.timestamp():
            window_rows.setdefault(row['code'],[]).append((at,row))
            if type(row.get('trade_value_won')) is not int:incomplete_symbols.add(row['code'])
    for symbol in selection['symbols']:
        facts={'symbol':symbol,'reference_price_won':None,'market_cap_won':None,'theme_ids':[],
            'market_strength_bps':None,'theme_strength_bps':None,'stock_strength_bps':None,
            'recent_turnover_won':None,'turnover_window_seconds':policy.capacity_window_seconds,
            'observed_close_return_rms_bps':None,'net_ev_bps':None,'ev_uncertainty_bps':None,
            'conservative_exit_capacity_won':None,'stress_loss_bps':None,'input_refs':[]}
        reference=references.get(symbol)
        if reference is None or (symbol,'KRX') not in approved:reasons.append('approved_KRX_quote_unavailable:'+symbol)
        else:
            fresh(reference['available_at'],'quote:'+symbol)
            facts.update(reference_price_won=reference['price_won'],price_available_at=reference['available_at'].isoformat())
            cap=reference.get('market_cap_eok')
            if type(cap) is int and cap>0:facts['market_cap_won']=cap*100000000
            else:reasons.append('native_market_cap_unavailable:'+symbol)
            facts['input_refs'].append(reference['input_ref'])
        selected=[]
        for context,member_map,breadth,theme_strength in prepared_contexts:
            member=member_map.get(symbol)
            if member is None and not (independent_context and not any(policy.context_weights[1:])):continue
            score=member['leadership_score_ppm'] if member else None
            selected.append((breadth//100 if type(breadth) is int and 0<=breadth<=1000000 else None,
                theme_strength,score//100 if type(score) is int and 0<=score<=1000000 else None))
            if not independent_context or theme_memberships is None:
                theme_id=context['leadership']['facts'].get('theme_id')
                if member is not None and theme_id:facts['theme_ids'].append(theme_id)
            facts['input_refs'].append(context['revision_id'])
        if independent_context and theme_memberships is not None:
            for membership in memberships.values():
                if symbol in membership.member_codes:
                    facts['theme_ids'].append(membership.theme_id);facts['input_refs'].append(membership.revision_id)
            if not facts['theme_ids']:reasons.append('native_theme_risk_membership_unavailable:'+symbol)
        if independent_context:facts['theme_ids']=sorted(set(facts['theme_ids']))
        if selected:
            facts.update(zip(('market_strength_bps','theme_strength_bps','stock_strength_bps'),
                (min(s[i] for s in selected) if all(s[i] is not None for s in selected) else None for i in range(3))))
            facts['context_basis']=('policy_weighted_native_components/v2' if independent_context
                else 'TOP20_breadth_positive_theme_members_and_leadership_rank/v1')
        context_keys=tuple(key for weight,key in zip(policy.context_weights,
            ('market_strength_bps','theme_strength_bps','stock_strength_bps')) if weight)
        if not selected or any(facts[key] is None for key in context_keys):
            reasons.append('native_market_theme_factors_unavailable:'+symbol)
        floors=[snapshot.get('retention_since'),snapshot.get('gap_at'),snapshot['continuous_from'].get((symbol,'KRX'))]
        complete=all(at is not None and at<=start for at in (floors[0],floors[2])) and (floors[1] is None or floors[1]<=start)
        rows=window_rows.get(symbol,[])
        if complete and rows and symbol not in incomplete_symbols:
            rows.sort(key=lambda value:(value[0],value[1].get('sequence',0)));facts['recent_turnover_won']=sum(r['trade_value_won'] for _,r in rows)
            window_body={'source_id':snapshot['source_id'],'generation':snapshot['generation'],
                'symbol':symbol,'venue':'KRX','start':start.isoformat(),'end':cut.isoformat(),
                'absolute_rows':[r for _,r in rows], 'input_resolution':snapshot.get('input_resolution','seconds')}
            digest=hashlib.sha256(json.dumps(window_body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            facts['window_input_ref']=('ram-allocation-0B-window:' if snapshot.get('input_resolution')=='0B' else 'ram-allocation-window:')+digest
            facts['input_refs'].append(facts['window_input_ref'])
            if snapshot.get('input_resolution') == '0B':
                # Preserve the existing observed-second close RMS. Summing
                # turnover still uses every receipt; sampling prices here
                # creates no second OHLCV, retained state or writer.
                prices=list({at.replace(microsecond=0):r['price_won'] for at,r in rows}.values())
                facts['volatility_basis']='last_received_price_per_observed_second/v1'
                facts['volatility_sample_count']=len(prices)
            else:
                prices=[r['close'] for _,r in rows]
            if len(prices)>=3 and all(type(p) is int and p>0 for p in prices):
                returns=[Fraction((b-a)*10000,a) for a,b in zip(prices,prices[1:])]
                square=sum(r*r for r in returns)/len(returns)
                mean_square=(square.numerator+square.denominator-1)//square.denominator
                root=math.isqrt(mean_square);facts['observed_close_return_rms_bps']=root+(root*root<mean_square)
            facts['window_start']=start.isoformat();facts['window_end']=cut.isoformat()
        else:reasons.append('native_turnover_window_incomplete:'+symbol)
        receipt['input_refs'].extend(facts['input_refs']);receipt['facts'].append(facts)
    receipt['input_refs']=sorted(set(receipt['input_refs']))
    receipt['available_at']=max(source_times).isoformat()
    receipt['valid_until']=min(expires).isoformat()
    receipt['quality']={'status':'BLOCKED','reasons':sorted(set(reasons+['allocation_ev_unavailable','full_exit_capacity_unavailable','stress_model_unavailable']))}
    if models is not None:
        from kiwoom_monitor.application.position_target import (DynamicPositionCandidate,evaluate_dynamic_position_allocation,
            PositionTargetShadowRequest,PositionTargetSizingPolicy,evaluate_position_target_shadow)
        receipt['model_publication_ref']=models['publication_ref']
        expires.append(models['valid_until']);source_times.append(models['available_at'])
        receipt['missing_producers']=[]
        if (models['selection_ref']!=_allocation_selection_ref(selection)
                or not models['available_at']<=cut<models['valid_until']):reasons.append('allocation_model_publication_expired_or_scope_invalid')
        candidates=[]
        for facts in receipt['facts']:
            model=models['rows'].get(facts['symbol'])
            if model is None:reasons.append('allocation_models_unavailable:'+facts['symbol']);continue
            ev,ex=model['ev'],model['exit']
            facts.update(net_ev_bps=ev.net_ev_bps,ev_uncertainty_bps=ev.ev_uncertainty_bps,
                conservative_exit_capacity_won=ex.conservative_exit_capacity_won,stress_loss_bps=ex.stress_loss_bps)
            needed=('reference_price_won','market_cap_won','recent_turnover_won','observed_close_return_rms_bps',
                *(key for weight,key in zip(policy.context_weights,
                    ('market_strength_bps','theme_strength_bps','stock_strength_bps')) if weight))
            if (any(facts[k] is None for k in needed) or not facts['theme_ids']
                    or facts['observed_close_return_rms_bps']<=0 or ex.stress_loss_bps<=0):
                reasons.append('allocation_candidate_facts_unavailable:'+facts['symbol']);continue
            source_id='ram-allocation-candidate:'+hashlib.sha256(json.dumps(
                {'facts':facts,'model_publication':models['publication_ref']},sort_keys=True,separators=(',',':')).encode()).hexdigest()
            candidates.append(DynamicPositionCandidate(facts['symbol'],tuple(facts['theme_ids']),source_id,
                max(source_times),facts['reference_price_won'],facts['market_cap_won'],facts['recent_turnover_won'],
                facts['turnover_window_seconds'],ex.conservative_exit_capacity_won,facts['market_strength_bps'],
                facts['theme_strength_bps'],facts['stock_strength_bps'],ev.net_ev_bps,ev.ev_uncertainty_bps,
                facts['observed_close_return_rms_bps'],ex.stress_loss_bps,ev,ex))
        receipt['available_at']=max(source_times).isoformat();receipt['valid_until']=min(expires).isoformat()
        receipt['input_refs']=sorted(set(receipt['input_refs']+[models['publication_ref']]))
        if not reasons and len(candidates)==len(selection['symbols']):
            result=evaluate_dynamic_position_allocation(policy,tuple(candidates),observation,decision_time=cut,
                cost_model=selection['cost_model'],risk_disposition=receipt['risk_disposition'],risk_input_ref=receipt['risk_snapshot_ref'])
            receipt['result']=result
            if result['status']=='BLOCKED':reasons.extend(result['reasons'])
            else:
                lower=[];study_bindings=[];by_symbol={c.symbol:c for c in candidates}
                for allocation in result['allocations']:
                    c=by_symbol[allocation['symbol']];actual=observation.account.positions.get(c.symbol,0)
                    goal=allocation['target_notional_won']//c.reference_price_won
                    action='ADD' if goal>actual and actual else 'REENTER' if goal>actual else 'EXIT' if not goal and actual else 'REDUCE' if goal<actual else 'HOLD'
                    raw=dict(allocation['target_policy'])
                    for key in ('available_at','valid_until'):raw[key]=datetime.fromisoformat(raw[key])
                    # HOLD/EXIT use the existing explicit-share contract. The
                    # upper receipt retains their automatic goal and original TTL.
                    sized=action in {'ADD','REDUCE','REENTER'}
                    request=PositionTargetShadowRequest(policy.account_ref,'mock','KRX',c.symbol,action,None if sized else goal,
                        c.reference_price_won,datetime.fromisoformat(next(f['price_available_at'] for f in receipt['facts'] if f['symbol']==c.symbol)),
                        cut,'dynamic-position-allocation-shadow/v1',result['decision_id'],observation.input_ref,
                        observation.input_ref,age,observation.reconciliation_complete,observation.open_orders_complete,
                        selection['cost_model'],PositionTargetSizingPolicy(**raw) if sized else None)
                    lower.append(evaluate_position_target_shadow(request,observation.account,observation.open_orders,
                        broker_open_orders=observation.broker_open_orders))
                    if 'position_study' in models['rows'][c.symbol]:
                        study=models['rows'][c.symbol]['position_study'];current=None;missing=[];refs=[]
                        if study['study'].version in {'position-action-study/v3','position-action-study/v4'}:
                            current,missing,refs=_position_study_observation_from_ram(study,request,observation,snapshot)
                        binding=position_study_current_goal_binding(study,request,lower[-1],observation,receipt,selection,
                            current_support_observation=current)
                        if study['study'].version in {'position-action-study/v3','position-action-study/v4'}:
                            conditional=binding['model_support']['conditional_shadow']
                            if missing:conditional['reasons']=sorted(set(conditional['reasons']+missing))
                            conditional['input_refs']=sorted(set(conditional.get('input_refs',[])+refs))
                            if conditional.get('result') is not None:
                                binding['valid_until']=min(datetime.fromisoformat(binding['valid_until']),
                                    datetime.fromisoformat(conditional['valid_until'])).isoformat()
                                receipt['valid_until']=min(datetime.fromisoformat(receipt['valid_until']),
                                    datetime.fromisoformat(binding['valid_until'])).isoformat()
                        study_bindings.append(binding)
                receipt['lower_targets']=lower
                receipt['r4_action_comparison']={'result':None,'status':'EV_UNAVAILABLE',
                    'reasons':['generated_dynamic_target_policy_requires_its_own_action_population'],'order_authorized':False}
                if study_bindings:
                    receipt['r4_action_comparison']={'version':'position-study-current-goal-binding/v2',
                        'result':None,'status':'EV_UNAVAILABLE','goals':study_bindings,
                        'reasons':sorted({reason for binding in study_bindings for reason in binding['reasons']}),
                        'order_authorized':False}
                if any(r['quality']['status']!='CALCULATED' for r in lower):reasons.append('allocation_lower_target_unavailable')
        receipt['quality']={'status':'BLOCKED' if reasons else 'CALCULATED','reasons':sorted(set(reasons))}
    if models is not None and models.get('version')=='dynamic-allocation-native-models/v2':
        bundle=freeze_dynamic_allocation_input_bundle(receipt,models,selection)
        receipt['input_bundle']=bundle
        for binding in receipt.get('r4_action_comparison',{}).get('goals',()):
            if bundle.get('bundle_ref'):binding['input_bundle_ref']=bundle['bundle_ref']
    return receipt


def read_persisted_stock_thesis_shadow_request(
    repository, *, run_id: str, thesis_id: str, thesis_policy_version: str,
    leadership_venue: str, decision_time: str,
):
    """Recover explicit saved research evidence; grants no operational approval.

    Read once before creating a binding. No latest-thesis inference, writes,
    monitor-state recovery, or per-tick repository access belongs here.
    """
    _persisted_thesis_cut(decision_time, leadership_venue, thesis_policy_version)
    saved = repository.load_entry_thesis_evidence(run_id, thesis_id)
    return persisted_stock_thesis_shadow_request_from_evidence(saved, run_id=run_id, thesis_id=thesis_id,
        thesis_policy_version=thesis_policy_version, leadership_venue=leadership_venue, decision_time=decision_time)


def _persisted_thesis_cut(decision_time, leadership_venue, thesis_policy_version):
    from kiwoom_monitor.application.theme_leadership import THESIS_POLICIES
    cutoff = datetime.fromisoformat(decision_time)
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("saved thesis decision time must be timezone-aware")
    if leadership_venue not in {"KRX", "NXT"} or thesis_policy_version not in THESIS_POLICIES:
        raise ValueError("saved thesis venue and policy must be explicit and supported")
    return cutoff


def persisted_stock_thesis_shadow_request_from_evidence(
    saved, *, run_id: str, thesis_id: str, thesis_policy_version: str,
    leadership_venue: str, decision_time: str,
):
    """Validate a supplied immutable research snapshot, without source/order approval."""
    from kiwoom_monitor.application.theme_leadership import (
        ENTRY_THESIS_VERSION, LeadershipEvent, MemberLeadershipMetric, ThemeLeadershipEvaluation,
    )
    cutoff = _persisted_thesis_cut(decision_time, leadership_venue, thesis_policy_version)
    if saved is None:
        raise ValueError("saved research thesis not found in the requested run")
    if saved["run_status"] not in {"running", "completed"} or saved["leadership"] is None:
        raise ValueError("saved research thesis has no usable run or leadership evidence")
    request = stock_thesis_shadow_request_from_dict({
        "entry_thesis": saved["thesis"], "thesis_venue": leadership_venue,
        "thesis_policy_version": thesis_policy_version,
    })
    thesis = request.entry_thesis
    raw = saved["leadership"]
    leadership = ThemeLeadershipEvaluation(**{
        **raw, "members": tuple(MemberLeadershipMetric(**value) for value in raw["members"]),
        "events": tuple(LeadershipEvent(**{**value, "input_refs": tuple(value["input_refs"])}) for value in raw["events"]),
        "input_refs": tuple(raw["input_refs"]), "state": ThemeLeadershipState(**raw["state"]),
    })
    identity = saved["stored_identity"]
    if (thesis.thesis_id != thesis_id or thesis.run_id != run_id
            or any(identity[key] != getattr(thesis, key) for key in ("thesis_id", "run_id", "leadership_revision_id", "symbol"))
            or identity["leadership_id"] != leadership.revision_id or identity["theme_id"] != leadership.theme_id
            or datetime.fromisoformat(identity["created_at"]) != datetime.fromisoformat(thesis.created_at)
            or datetime.fromisoformat(identity["leadership_at"]) != datetime.fromisoformat(leadership.as_of)):
        raise ValueError("saved thesis identity columns differ from immutable evidence")
    if (thesis.policy_version != ENTRY_THESIS_VERSION or thesis.leadership_revision_id != leadership.revision_id
            or thesis.theme_id != leadership.theme_id or thesis.theme_revision_id != leadership.theme_revision_id
            or thesis.leader_id != leadership.displayed_leader_id or leadership.venue != leadership_venue
            or leadership.state.venue != leadership_venue or leadership.state.theme_id != thesis.theme_id
            or leadership.state.theme_revision_id != thesis.theme_revision_id
            or leadership.quality.get("status") != "COMPLETE"):
        raise ValueError("saved thesis scope or complete leadership evidence differs")
    if not (datetime.fromisoformat(leadership.as_of) <= datetime.fromisoformat(thesis.created_at)
            <= datetime.fromisoformat(thesis.available_at) <= cutoff):
        raise ValueError("saved research thesis is not available at the decision cut")
    refs = (*leadership.input_refs, *thesis.factor_refs, *thesis.hypothesis_refs, thesis.theme_revision_id)
    if any(not isinstance(ref, str) or ref.startswith(("ram:", "ram-0B:")) for ref in refs):
        raise ValueError("ephemeral RAM references cannot be recovered as saved thesis evidence")
    return request


class MarketThemeShadowReadStore(Protocol):
    def load_minute_bars(self, code: str, trading_date: str, market: str = '') -> list[dict[str, Any]]: ...
    def load_observation_revisions(self, kind: str, subject: str = "", limit: int = 100) -> list[dict[str, Any]]: ...
    def load_theme_snapshots(self, *, available_at: float | None = None, limit: int = 100) -> list[dict[str, Any]]: ...
    def load_second_trade_bars(
        self, codes: tuple[str, ...], trading_date: str, market: str, *,
        start_second: str, end_second: str, available_at: float, limit: int = 10000,
    ) -> list[dict[str, Any]]: ...


def read_stock_thesis_source_check(
    store: MarketThemeShadowReadStore, saved, *, thesis_policy_version: str,
    leadership_venue: str, lookback_seconds: int = 20, theme_limit: int = 100,
) -> dict[str, Any]:
    """Explicit bounded native comparison, never a completeness/approval gate.

    Each native read retains its own transaction. Second bars preserve current
    values only; a missing hash cannot distinguish correction, retention or an
    earlier value outside this requested window. No replay or live-path reads.
    """
    from time import perf_counter
    from kiwoom_monitor.domain.news_observation import stable_document_hash
    if (type(lookback_seconds) is not int or not 1 <= lookback_seconds <= 60
            or type(theme_limit) is not int or not 1 <= theme_limit <= 1000):
        raise ValueError("source check budgets must be 1-60 seconds and 1-1000 themes")
    if len(json.dumps(saved, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 1024 * 1024:
        raise ValueError("source check evidence exceeds 1 MiB")
    request = persisted_stock_thesis_shadow_request_from_evidence(saved,
        run_id=saved["thesis"]["run_id"], thesis_id=saved["thesis"]["thesis_id"],
        thesis_policy_version=thesis_policy_version, leadership_venue=leadership_venue,
        decision_time=saved["thesis"]["available_at"])
    thesis = request.entry_thesis
    leadership = saved["leadership"]
    codes = tuple(member["code"] for member in leadership["members"])
    refs = tuple(dict.fromkeys(leadership["input_refs"]))
    if (not 2 <= len(codes) <= 200 or len(set(codes)) != len(codes)
            or any(len(code) != 6 or not code.isdigit() for code in codes) or len(refs) > 10000):
        raise ValueError("source check requires 2-200 members and at most 10000 references")
    cut = datetime.fromisoformat(leadership["as_of"]).astimezone(timezone.utc)
    local = cut.astimezone(ZoneInfo("Asia/Seoul"))
    start = max(local.replace(hour=0, minute=0, second=0, microsecond=0),
                local - timedelta(seconds=lookback_seconds))
    expected = tuple(ref for ref in refs if ref.startswith("second-trade:")
                     and len(ref) == 77 and all(c in "0123456789abcdef" for c in ref[13:]))
    expected_set = set(expected)
    unknown = tuple(ref for ref in refs if ref != thesis.theme_revision_id and ref not in expected_set)
    started = perf_counter()
    snapshots = store.load_theme_snapshots(available_at=cut.timestamp(), limit=theme_limit)
    theme_ms = (perf_counter() - started) * 1000
    snapshot = next((value for value in snapshots if value["snapshot_id"] == thesis.theme_revision_id
                     and float(value["available_at"]) <= cut.timestamp()), None)
    theme_status = "NOT_FOUND_IN_BOUNDED_READ"
    if snapshot is not None:
        if snapshot["content_hash"] != stable_document_hash(snapshot["document"]):
            theme_status = "CONTENT_HASH_MISMATCH"
        else:
            membership = next((value for value in theme_memberships_from_snapshot(snapshot)
                               if value.theme_id == thesis.theme_id), None)
            theme_status = ("MATCHED" if membership and set(membership.member_codes) == set(codes)
                            and len(membership.member_codes) == len(codes)
                            and thesis.theme_revision_id in refs else "MEMBERSHIP_MISMATCH")
    second_started = perf_counter()
    rows = store.load_second_trade_bars(codes, local.date().isoformat(), leadership_venue,
        start_second=start.strftime("%H:%M:%S"), end_second=local.strftime("%H:%M:%S"),
        available_at=cut.timestamp(), limit=10000)
    if len(rows) > 10000:
        raise ValueError("source check second row budget exceeded")
    # Rehash full native values; never accept an arbitrary supplied revision ID.
    points = second_trade_points_from_rows({k: v for k, v in row.items() if k != "revision_id"} for row in rows)
    native_refs = {point.input_ref for point in points if point.code in codes and point.market == leadership_venue
                   and start.astimezone(timezone.utc) <= datetime.fromisoformat(point.observed_at) <= cut
                   and datetime.fromisoformat(point.available_at) <= cut}
    matched = tuple(ref for ref in expected if ref in native_refs)
    unmatched = tuple(ref for ref in expected if ref not in native_refs)
    return {
        "version": "stock-thesis-source-check/v1", "thesis_ref": thesis.thesis_id,
        "leadership_ref": thesis.leadership_revision_id, "as_of": cut.isoformat(),
        "scope": "bounded_current_native_values", "lookback_seconds": lookback_seconds,
        "theme": {"ref": thesis.theme_revision_id, "status": theme_status,
                  "rows_read": len(snapshots), "budget_reached": len(snapshots) >= theme_limit},
        "seconds": {"expected": len(expected), "matched": len(matched),
                    "unmatched_refs": list(unmatched), "rows_read": len(rows)},
        "unverified_refs": {"leadership": list(unknown), "factors": list(thesis.factor_refs),
                            "hypotheses": list(thesis.hypothesis_refs)},
        "unverified": ["second_revision_history", "capture_continuity", "factor_hypothesis_lineage",
                       "cross_read_snapshot", "operational_approval"],
        "source_durability": "not_verified", "replay_eligible": False, "order_authorized": False,
        "timing_ms": {"theme_read": theme_ms, "second_read_and_compare": (perf_counter() - second_started) * 1000,
                      "total": (perf_counter() - started) * 1000},
    }


def read_minute_stock_thesis_source_check(
    store: MarketThemeShadowReadStore, saved, *, thesis_policy_version: str,
    leadership_venue: str, lookback_minutes: int = 20, theme_limit: int = 100,
) -> dict[str, Any]:
    """Explicit bounded native comparison, never a completeness/approval gate.

    Each native read retains its own transaction. Second bars preserve current
    values only; a missing hash cannot distinguish correction, retention or an
    earlier value outside this requested window. No replay or live-path reads.
    """
    from time import perf_counter
    from kiwoom_monitor.domain.news_observation import stable_document_hash
    if (type(lookback_minutes) is not int or not 1 <= lookback_minutes <= 60
            or type(theme_limit) is not int or not 1 <= theme_limit <= 1000):
        raise ValueError("source check budgets must be 1-60 minutes and 1-1000 themes")
    if len(json.dumps(saved, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 1024 * 1024:
        raise ValueError("source check evidence exceeds 1 MiB")
    request = persisted_stock_thesis_shadow_request_from_evidence(saved,
        run_id=saved["thesis"]["run_id"], thesis_id=saved["thesis"]["thesis_id"],
        thesis_policy_version=thesis_policy_version, leadership_venue=leadership_venue,
        decision_time=saved["thesis"]["available_at"])
    thesis = request.entry_thesis
    leadership = saved["leadership"]
    codes = tuple(member["code"] for member in leadership["members"])
    refs = tuple(dict.fromkeys(leadership["input_refs"]))
    if (not 2 <= len(codes) <= 200 or len(set(codes)) != len(codes)
            or any(len(code) != 6 or not code.isdigit() for code in codes) or len(refs) > 10000):
        raise ValueError("source check requires 2-200 members and at most 10000 references")
    cut = datetime.fromisoformat(leadership["as_of"]).astimezone(timezone.utc)
    local = cut.astimezone(ZoneInfo("Asia/Seoul"))
    start = max(local.replace(hour=0, minute=0, second=0, microsecond=0),
                local - timedelta(minutes=lookback_minutes))
    expected = tuple(ref for ref in refs if ref.startswith("minute-trade:")
                     and len(ref) == 77 and all(c in "0123456789abcdef" for c in ref[13:]))
    expected_set = set(expected)
    unknown = tuple(ref for ref in refs if ref != thesis.theme_revision_id and ref not in expected_set)
    started = perf_counter()
    snapshots = store.load_theme_snapshots(available_at=cut.timestamp(), limit=theme_limit)
    theme_ms = (perf_counter() - started) * 1000
    snapshot = next((value for value in snapshots if value["snapshot_id"] == thesis.theme_revision_id
                     and float(value["available_at"]) <= cut.timestamp()), None)
    theme_status = "NOT_FOUND_IN_BOUNDED_READ"
    if snapshot is not None:
        if snapshot["content_hash"] != stable_document_hash(snapshot["document"]):
            theme_status = "CONTENT_HASH_MISMATCH"
        else:
            membership = next((value for value in theme_memberships_from_snapshot(snapshot)
                               if value.theme_id == thesis.theme_id), None)
            theme_status = ("MATCHED" if membership and set(membership.member_codes) == set(codes)
                            and len(membership.member_codes) == len(codes)
                            and thesis.theme_revision_id in refs else "MEMBERSHIP_MISMATCH")
    second_started = perf_counter()
    from kiwoom_monitor.application.theme_leadership import minute_trade_points_from_rows
    rows = []
    for code in codes:
        loaded = store.load_minute_bars(code, local.date().isoformat(), leadership_venue)
        if len(loaded) > 1440 or len(rows) + len(loaded) > 10000:
            raise ValueError('source check minute row budget exceeded')
        rows.extend(loaded)
    points = minute_trade_points_from_rows(rows, as_of=cut.isoformat())
    native_refs = {point.input_ref for point in points if point.code in codes and point.market == leadership_venue
                   and start.astimezone(timezone.utc) <= datetime.fromisoformat(point.observed_at) <= cut}
    matched = tuple(ref for ref in expected if ref in native_refs)
    unmatched = tuple(ref for ref in expected if ref not in native_refs)
    return {
        "version": "stock-thesis-source-check/v2", "thesis_ref": thesis.thesis_id,
        "leadership_ref": thesis.leadership_revision_id, "as_of": cut.isoformat(),
        "scope": "bounded_final_completed_minutes", "lookback_minutes": lookback_minutes,
        "theme": {"ref": thesis.theme_revision_id, "status": theme_status,
                  "rows_read": len(snapshots), "budget_reached": len(snapshots) >= theme_limit},
        "minutes": {"expected": len(expected), "matched": len(matched),
                    "unmatched_refs": list(unmatched), "rows_read": len(rows)},
        "unverified_refs": {"leadership": list(unknown), "factors": list(thesis.factor_refs),
                            "hypotheses": list(thesis.hypothesis_refs)},
        "unverified": ["minute_revision_history", "capture_continuity", "factor_hypothesis_lineage",
                       "cross_read_snapshot", "operational_approval"],
        "source_durability": "not_verified", "replay_eligible": False, "order_authorized": False,
        "timing_ms": {"theme_read": theme_ms, "minute_read_and_compare": (perf_counter() - second_started) * 1000,
                      "total": (perf_counter() - started) * 1000},
    }


def read_market_theme_shadow_inputs(
    store: MarketThemeShadowReadStore, *, decision_time: str, theme_id: str,
    leadership_venue: str, ranking_limit: int = 100, theme_limit: int = 100,
    leadership_parameters: ThemeLeadershipParameters,
    breakout_evidence: MatureBreakoutEvidence | None = None,
    previous_leadership_state: ThemeLeadershipState | None = None,
) -> MarketThemeShadowInputs:
    """Read an availability cut across separate native snapshots; not a historical replay."""
    cutoff = datetime.fromisoformat(decision_time)
    if cutoff.tzinfo is None:
        raise ValueError("shadow decision time must be timezone-aware")
    cutoff = cutoff.astimezone(timezone.utc)
    if not theme_id or leadership_venue not in {"KRX", "NXT"}:
        raise ValueError("shadow theme and leadership venue must be explicit")
    if not 1 <= ranking_limit <= 1000 or not 1 <= theme_limit <= 1000:
        raise ValueError("shadow source read budgets must be 1-1000")
    if (leadership_parameters.window_seconds < 60 or leadership_parameters.window_seconds % 60
            or leadership_parameters.max_data_age_seconds < 60):
        raise ValueError('minute research needs explicit whole-minute windows and freshness')
    snapshots = tuple(store.load_theme_snapshots(available_at=cutoff.timestamp(), limit=theme_limit))
    eligible = tuple(snapshot for snapshot in snapshots if float(snapshot["available_at"]) <= cutoff.timestamp())
    # Native reader already orders by accepted_sequence; timestamps are not revision order.
    latest = eligible[0] if eligible else None
    membership = next((value for value in theme_memberships_from_snapshot(latest)
                       if value.theme_id == theme_id), None) if latest else None
    observations = store.load_observation_revisions("ranking", "5", ranking_limit)
    market_frames = market_frames_from_observations(observations, eligible)
    from kiwoom_monitor.application.theme_leadership import minute_trade_points_from_rows
    from kiwoom_monitor.application.market_theme_shadow import MarketPriceTradeShadowInputs
    rows = []
    if membership:
        local = cutoff.astimezone(ZoneInfo('Asia/Seoul'))
        for code in membership.member_codes:
            loaded = store.load_minute_bars(code, local.date().isoformat(), leadership_venue)
            if len(loaded)>1440 or len(rows)+len(loaded)>10000:
                raise ValueError('minute research row budget exceeded')
            rows.extend(loaded)
    return MarketPriceTradeShadowInputs(
        decision_time=cutoff.isoformat(), market_venue="COMBINED", market_window="cumulative_session",
        leadership_venue=leadership_venue, market_frames=market_frames, theme_membership=membership,
        second_points=(), observation_points=minute_trade_points_from_rows(rows, as_of=cutoff.isoformat()),
        input_resolution='1m', continuity_status="UNAVAILABLE",
        breakout_evidence=breakout_evidence, previous_leadership_state=previous_leadership_state,
    )


@dataclass(frozen=True)
class RamTradeObservationCut:
    """One cut's shared index/immutable points; no collector, writer or lifetime."""
    rows: tuple
    rows_by_source: object
    points_by_ref: object
    times_by_ref: object

    def __deepcopy__(self, memo):
        # Keep copied raw rows isolated while retaining index-to-row identity.
        # Immutable points/timestamps can be reused; mappingproxy itself cannot
        # be pickled by the default deepcopy implementation.
        from copy import deepcopy
        value = RamTradeObservationCut(deepcopy(self.rows, memo),
            MappingProxyType({key:deepcopy(rows, memo) for key,rows in self.rows_by_source.items()}),
            MappingProxyType(dict(self.points_by_ref)), MappingProxyType(dict(self.times_by_ref)))
        memo[id(self)] = value
        return value


def prepare_ram_trade_observations(snapshot):
    from kiwoom_monitor.application.theme_leadership import realtime_trade_points_from_rows
    rows = snapshot['rows']
    groups = {}
    for row in rows:
        groups.setdefault((row['code'],row['market']),[]).append(row)
    return RamTradeObservationCut(rows,
        MappingProxyType({key:tuple(values) for key,values in groups.items()}),
        MappingProxyType({point.input_ref:point for point in realtime_trade_points_from_rows(rows)}),
        MappingProxyType({row['input_ref']:datetime.fromisoformat(row['observed_at']) for row in rows}))


def _prepared_ram_snapshot(snapshot):
    if snapshot.get('input_resolution') != '0B':
        return snapshot
    value = snapshot.get('_trade_cut')
    if isinstance(value,RamTradeObservationCut) and value.rows is snapshot['rows']:
        return snapshot
    return {**snapshot, '_trade_cut':prepare_ram_trade_observations(snapshot)}


def _ram_cut(snapshot):
    value = snapshot.get('_trade_cut')
    return value if isinstance(value,RamTradeObservationCut) and value.rows is snapshot['rows'] else prepare_ram_trade_observations(snapshot)


def _ram_price_rows(snapshot, codes=None, venue=None):
    if snapshot.get('input_resolution') != '0B' or codes is None:
        return snapshot['rows']
    cut = _ram_cut(snapshot)
    return (row for code in sorted(set(codes)) for row in cut.rows_by_source.get((code,venue),()))


def _ram_row_time(row, snapshot=None):
    if row.get('input_resolution') == '0B':
        if snapshot is not None:
            return _ram_cut(snapshot).times_by_ref[row['input_ref']]
        return datetime.fromisoformat(row['observed_at'])
    return datetime.fromisoformat(row['trading_date']+'T'+row['trade_second']).replace(tzinfo=ZoneInfo('Asia/Seoul'))


def _ram_points(snapshot, rows):
    if snapshot.get('input_resolution') == '0B':
        from kiwoom_monitor.application.theme_leadership import RealtimeTradePoints
        points = _ram_cut(snapshot).points_by_ref
        return RealtimeTradePoints(points[row['input_ref']] for row in rows)
    return second_trade_points_from_rows(rows)


def market_theme_shadow_inputs_from_ram(snapshot, context: MarketThemeShadowInputs, *, window_seconds: int):
    """Use individual collector 0B receipts with explicit refs and conservative coverage.

    Market/theme/reference context is supplied by its existing owner. No native
    revision/accepted sequence or durable status is invented for RAM observations.
    """
    snapshot=_prepared_ram_snapshot(snapshot)
    cutoff = datetime.fromisoformat(snapshot["decision_time"])
    if cutoff.tzinfo is None or not 1 <= window_seconds <= 30 or snapshot["durable"] is not False:
        raise ValueError("RAM source cut/window/durability is invalid")
    membership = context.theme_membership
    codes = membership.member_codes if membership else ()
    venue = context.leadership_venue
    start = cutoff - timedelta(seconds=2 * window_seconds)
    approved = set(snapshot["approved_sources"])
    since = snapshot["continuous_from"]
    floors = tuple(value for value in (snapshot["retention_since"], snapshot["gap_at"]) if value is not None)
    complete = bool(codes) and snapshot["retention_since"] is not None and all(value <= start for value in floors)
    complete = complete and all((code, venue) in approved and since.get((code, venue)) is not None
                                and since[(code, venue)] <= start for code in codes)
    rows = []
    for record in _ram_price_rows(snapshot,codes,venue):
        if record["code"] not in codes or record["market"] != venue:
            continue
        observed = _ram_row_time(record,snapshot)
        if not start <= observed <= cutoff or record["available_at"] > cutoff.timestamp():
            continue
        source_start = since.get((record["code"], venue))
        if source_start is None or observed < source_start or any(observed < floor for floor in floors):
            continue
        if snapshot.get('input_resolution')=='0B':
            rows.append(record)
        else:
            digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            rows.append({**record, "revision_id": f"ram:{snapshot['source_id']}:{snapshot['generation']}:{digest}"})
    limits = list(context.upper_limits)
    day = cutoff.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    for reference in snapshot.get("stock_references", ()):
        if (reference["code"] not in codes or reference["market"] != venue
                or reference["trading_date"] != day or reference["available_at"] > cutoff.timestamp()
                or (reference["code"], venue) not in approved):
            continue
        # An observed missing/invalid basis replaces this code's old reference.
        limits = [limit for limit in limits if limit.code != reference["code"]]
        price = reference["upper_limit_price"]
        if type(price) is not int or price <= 0:
            continue
        digest = hashlib.sha256(json.dumps(reference, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        limits.append(UpperLimitReference(
            input_ref=f"ram:{snapshot['source_id']}:{snapshot['generation']}:0g:{digest}",
            code=reference["code"], price=price,
            available_at=datetime.fromtimestamp(reference["available_at"], timezone.utc).isoformat(),
        ))
    points = _ram_points(snapshot, rows)
    complete = complete and len(points) == len(rows)
    values = dict(decision_time=cutoff.isoformat(), upper_limits=tuple(limits),
                  continuity_status="COMPLETE" if complete else "PARTIAL" if rows else "UNAVAILABLE")
    if snapshot.get('input_resolution') == '0B':
        from kiwoom_monitor.application.market_theme_shadow import MarketPriceTradeShadowInputs
        return MarketPriceTradeShadowInputs(**{**vars(context), **values,
            'second_points': (), 'observation_points': points, 'input_resolution': '0B',
            'market_primary_timeframe': '0B'})
    return replace(context, second_points=points, **values)


def market_minute_trade_inputs_from_rows(snapshot, context, rows, *, max_membership_age_seconds=90):
    from kiwoom_monitor.application.market_research_features import MarketMinuteTradeInputs
    cutoff = datetime.fromisoformat(snapshot["decision_time"])
    eligible = [frame for frame in context.market_frames if frame.venue == context.market_venue
        and frame.window == context.market_window and datetime.fromisoformat(frame.available_at) <= cutoff]
    if not eligible:
        return None
    frame = max(eligible, key=lambda value: (datetime.fromisoformat(value.available_at), value.revision_id))
    if frame.capture_quality != "complete":
        return None
    codes = tuple(normalize_stock_code(member.code) for member in frame.members)
    return MarketMinuteTradeInputs(frame.revision_id, frame.available_at, codes, context.leadership_venue,
        tuple(row for row in rows if row.get("code") in codes and row.get("market") == context.leadership_venue),
        max_membership_age_seconds=max_membership_age_seconds)


def market_price_trade_inputs_from_ram(snapshot, context, *, window_seconds, max_data_age_seconds, max_membership_age_seconds=90):
    """Reuse one approved RAM cut for the actual TOP20 price/trade cohort, without a theme."""
    from kiwoom_monitor.application.market_research_features import MarketPriceTradeInputs
    snapshot=_prepared_ram_snapshot(snapshot)
    cutoff = datetime.fromisoformat(snapshot["decision_time"])
    eligible = [frame for frame in context.market_frames if frame.venue == context.market_venue
                and frame.window == context.market_window and datetime.fromisoformat(frame.available_at) <= cutoff]
    if not eligible:
        return None
    frame = max(eligible, key=lambda value: (datetime.fromisoformat(value.available_at), value.revision_id))
    codes = tuple(normalize_stock_code(row.code) for row in frame.members)
    venue = context.leadership_venue
    start = cutoff - timedelta(seconds=2 * window_seconds)
    floors = tuple(value for value in (snapshot["retention_since"], snapshot["gap_at"]) if value is not None)
    approved, since = set(snapshot["approved_sources"]), snapshot["continuous_from"]
    continuity, continuity_reasons = {}, {}
    for code in codes:
        source_start = since.get((code, venue))
        issues = tuple(reason for reason, failed in (
            ("membership_capture_incomplete", frame.capture_quality != "complete"),
            ("membership_trading_day_mismatch", frame.trading_date != cutoff.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()),
            ("ram_retention_history_insufficient", snapshot["retention_since"] is None or snapshot["retention_since"] > start),
            ("ram_delivery_gap_in_window", snapshot["gap_at"] is not None and snapshot["gap_at"] > start),
            ("trade_source_not_approved", (code, venue) not in approved),
            ("trade_source_history_unconfirmed", source_start is None),
            ("trade_source_history_insufficient", source_start is not None and source_start > start),
        ) if failed)
        continuity[code] = "UNKNOWN" if issues else "COMPLETE"
        continuity_reasons[code] = issues
    rows = []
    for record in _ram_price_rows(snapshot,codes,venue):
        if record["code"] not in codes or record["market"] != venue:
            continue
        observed = _ram_row_time(record,snapshot)
        source_start = since.get((record["code"], venue))
        if (not start < observed <= cutoff or record["available_at"] > cutoff.timestamp()
                or source_start is None or observed < source_start or any(observed < floor for floor in floors)):
            continue
        if snapshot.get('input_resolution')=='0B':
            rows.append(record)
        else:
            digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            rows.append({**record, "revision_id": f"ram:{snapshot['source_id']}:{snapshot['generation']}:{digest}"})
    points = _ram_points(snapshot, rows)
    if len(points) != len(rows):
        continuity = dict.fromkeys(codes, "UNKNOWN")
        continuity_reasons = {code: (*continuity_reasons[code], "price_trade_row_conversion_incomplete") for code in codes}
    return MarketPriceTradeInputs(frame.revision_id, frame.available_at, codes, venue, points, continuity,
                                 window_seconds, max_data_age_seconds, max_membership_age_seconds=max_membership_age_seconds,
                                 continuity_reasons=continuity_reasons,
                                 input_resolution=snapshot.get('input_resolution','seconds'))


def market_frame_from_live_membership(snapshot, theme_snapshots=()):
    """Convert one validated RAM TOP20 receipt; persistence ACK is not its identity."""
    available = datetime.fromtimestamp(float(snapshot["saved_at"]), tz=timezone.utc)
    payload = snapshot["payload"]
    items = payload["items"]
    if len(items) != 20 or len(set(payload["codes"])) != 20:
        raise ValueError("live market context requires a validated 20-slot projection")
    effective = datetime.fromisoformat(snapshot["snapshot_key"])
    if effective.tzinfo is None:
        effective = effective.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    observations = ({"kind": "ranking", "revision_id": live_membership_ref(snapshot),
        "available_at": available.isoformat(), "effective_at": effective.isoformat(),
        "venue": "COMBINED", "completeness": "complete", "payload": {"query_type": "5", "items": items}},)
    frames = market_frames_from_observations(observations, theme_snapshots)
    if len(frames) != 1 or len(frames[0].members) != 20:
        raise ValueError("live market context has missing or duplicate members")
    return frames[0]


def live_membership_ref(snapshot):
    identity = {"subject": snapshot["subject"], "snapshot_key": snapshot["snapshot_key"], "payload": snapshot["payload"]}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return f"ram:top20:{digest}"
