"""D3c 후보 시계열 label과 모의 실행 성과 계산."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from statistics import median
from fractions import Fraction
from math import sqrt, isqrt
from typing import Any, Iterable, Mapping, ClassVar
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.breakout_strategy import CandidateEvent
from kiwoom_monitor.application.research_execution import ExecutionEvent, SimulationCostModel, SimulationExecutionConfig
from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
from kiwoom_monitor.application.research_splits import (
    ResearchEvaluationSpec,
    DevelopmentPartitionSpec,
    assign_interval_to_fold,
    warmup_start,
)


OUTCOME_HORIZONS_SECONDS = (1, 60, 180, 300, 600)


def _position_context_time(value):
    if isinstance(value,bool):raise ValueError('historical context time requires an aware timestamp')
    if isinstance(value,(int,float)):return datetime.fromtimestamp(value,tz=timezone.utc)
    return _aware_datetime(value)


def prepare_position_study_historical_context(dataset,request):
    """Freeze only existing, already available D2/D5 inputs; no reads or inference."""
    cut=_aware_datetime(request.decision_time);rankings=[];themes=[]
    for row in dataset.observations:
        if row.get('kind')!='ranking' or _position_context_time(row['available_at'])>cut:continue
        payload=row.get('payload')
        if not isinstance(payload,dict):raise ValueError('historical ranking payload required')
        if row.get('venue','COMBINED')=='COMBINED' and str(payload.get('query_type'))=='5':rankings.append(row)
    for row in dataset.theme_snapshots:
        if row.get('available_at') is not None and _position_context_time(row['available_at'])<=cut:themes.append(row)
    body={'version':'position-study-historical-context/v1','dataset_id':dataset.manifest['dataset_id'],
        'revision_ids_hash':dataset.manifest['revision_ids_hash'],'decision_time':cut.isoformat(),'symbol':request.symbol,
        'market_venue':'COMBINED','ranking_window_truncated':len(rankings)>100,'theme_window_truncated':len(themes)>100,
        'rankings':sorted(rankings,key=lambda r:(_position_context_time(r['available_at']),r['revision_id']))[-100:],
        'theme_snapshots':sorted(themes,key=lambda r:(_position_context_time(r['available_at']),r['snapshot_id']))[-100:]}
    body=json.loads(json.dumps(body,ensure_ascii=False,allow_nan=False))
    body['context_ref']=_content_id('position-study-historical-context',body)
    validate_position_study_historical_context(body,dataset.manifest,request)
    return body


def validate_position_study_historical_context(document,manifest,request,*,dataset=None):
    """Verify the reported historical cut; dataset callers also verify actual values."""
    from kiwoom_monitor.domain.news_observation import stable_document_hash
    keys={'version','dataset_id','revision_ids_hash','decision_time','symbol','market_venue','rankings','theme_snapshots','context_ref',
        'ranking_window_truncated','theme_window_truncated'}
    if (not isinstance(document,dict) or set(document)!=keys
            or document['version']!='position-study-historical-context/v1' or document['market_venue']!='COMBINED'
            or any(type(document[key]) is not bool for key in ('ranking_window_truncated','theme_window_truncated'))
            or len(json.dumps(document,ensure_ascii=False,allow_nan=False).encode())>128*1024
            or any(not isinstance(document[key],list) or len(document[key])>100 for key in ('rankings','theme_snapshots'))):
        raise ValueError('invalid bounded historical context')
    if document['context_ref']!=_content_id('position-study-historical-context',{k:v for k,v in document.items() if k!='context_ref'}):
        raise ValueError('historical context content mismatch')
    cut=_aware_datetime(document['decision_time'])
    if (document['dataset_id']!=manifest['dataset_id'] or document['revision_ids_hash']!=manifest['revision_ids_hash']
            or document['symbol']!=request.symbol or cut!=request.decision_time
            or manifest.get('runtime_input_version')!='independent_development_input/v1'):
        raise ValueError('historical context original dataset/case cut mismatch')
    rankings=document['rankings'];themes=document['theme_snapshots']
    for rows,identity in ((rankings,'revision_id'),(themes,'snapshot_id')):
        if any(not isinstance(row,dict) or type(row.get(identity)) is not str or not 0<len(row[identity])<=256 for row in rows):
            raise ValueError('historical context requires original source identities')
        if len({row[identity] for row in rows})!=len(rows):raise ValueError('duplicate historical context source')
        if rows!=sorted(rows,key=lambda r:(_position_context_time(r['available_at']),r[identity])):
            raise ValueError('historical context source order mismatch')
        if any(_position_context_time(row['available_at'])>cut for row in rows):raise ValueError('historical context future source')
    for row in rankings:
        payload=row.get('payload')
        if (row.get('kind')!='ranking' or row.get('venue','COMBINED')!='COMBINED' or not isinstance(payload,dict)
                or str(payload.get('query_type'))!='5' or not isinstance(payload.get('items'),list) or len(payload['items'])>20):
            raise ValueError('historical context ranking scope mismatch')
        available=_position_context_time(row['available_at'])
        descriptor=manifest['development_partition']
        if (not _position_context_time(descriptor['warmup_start'])<=available< _position_context_time(descriptor['end'])
                or row.get('effective_at') is not None and _position_context_time(row['effective_at'])>cut):
            raise ValueError('historical ranking outside original cut/partition')
    for row in themes:
        if not isinstance(row.get('document'),dict):raise ValueError('historical context theme document required')
        if 'content_hash' in row and row['content_hash']!=stable_document_hash(row['document']):
            raise ValueError('historical context original theme content mismatch')
    if dataset is not None and document!=prepare_position_study_historical_context(dataset,request):
        raise ValueError('historical context differs from actual frozen dataset')
    return document


def replay_position_study_historical_context(document):
    """Reuse H1 and D5 conversion once per distinct context at explicit publication."""
    from kiwoom_monitor.application.market_research_features import market_frames_from_observations
    from kiwoom_monitor.application.theme_leadership import theme_memberships_from_snapshot
    from kiwoom_monitor.application.market_theme_shadow import MarketThemeShadowInputs,evaluate_market_theme_shadow
    frames=market_frames_from_observations(document['rankings'],document['theme_snapshots'])
    latest=document['theme_snapshots'][-1] if document['theme_snapshots'] else None
    memberships=theme_memberships_from_snapshot(latest) if latest else ()
    observed=tuple(m for m in memberships if document['symbol'] in m.member_codes)
    inputs=MarketThemeShadowInputs(document['decision_time'],'COMBINED','cumulative_session','KRX',
        frames,None,(),'UNAVAILABLE')
    context=evaluate_market_theme_shadow(inputs)
    return {'context_ref':document['context_ref'],'symbol':document['symbol'],'decision_time':document['decision_time'],
        'source_window_truncated':document['ranking_window_truncated'] or document['theme_window_truncated'],
        'ranking_refs':[row['revision_id'] for row in document['rankings']],
        'theme_snapshot_refs':[row['snapshot_id'] for row in document['theme_snapshots']],
        'observed_memberships':[asdict(m) for m in observed],'market':context['market'],
        'missing':['historical_second_trade_leadership','historical_breakout_evidence','historical_market_cap',
            'historical_allocation_ev_exit_stress_context','conditional_current_context_support'],
        'source_durability':'not_verified','order_authorized':False}


@dataclass(frozen=True)
class PositionActionOutcome:
    """Measured portfolio change over a fixed horizon, not an inferred trade label.

    Gross change uses execution prices (slippage already reflected). Fee/tax is
    subtracted once. HOLD requires its own terminal portfolio valuation too.
    """
    input_ref: str
    case_ref: str
    initial_state_ref: str
    action: str
    policy_version: str
    environment: str
    venue: str
    source_kind: str
    execution_model_ref: str
    initial_quantity: int
    pending_buy_quantity: int
    pending_sell_quantity: int
    target_quantity: int
    horizon_seconds: int
    population_ref: str
    input_resolution: str
    cost_model_ref: str | None
    started_at: datetime
    completed_at: datetime | None
    available_at: datetime
    status: str
    data_gap_count: int
    gross_change_won: int | None
    fee_tax_won: int | None
    value_basis: str = "portfolio_change_after_slippage_before_fees/v1"
    _actions: ClassVar[frozenset[str]] = frozenset({'ADD','REDUCE','REENTER','EXIT','HOLD'})

    def __post_init__(self) -> None:
        for name in ("input_ref", "case_ref", "initial_state_ref", "policy_version", "population_ref", "execution_model_ref"):
            if type(getattr(self, name)) is not str or not getattr(self, name).strip():
                raise ValueError(f"action outcome {name} is required")
        if self.action not in self._actions:
            raise ValueError("unsupported measured position action")
        if self.environment not in {"mock", "real", "simulation"} or self.venue not in {"KRX", "NXT"}:
            raise ValueError("action outcome scope must be explicit")
        if self.source_kind not in {"simulation", "broker", "shadow_valuation"}:
            raise ValueError("action outcome source kind must be explicit")
        if self.input_resolution not in {"minute", "second"}:
            raise ValueError("action outcome input resolution must be explicit")
        if self.status not in {"COMPLETE", "PARTIAL", "CENSORED", "UNAVAILABLE"}:
            raise ValueError("action outcome completion status must be explicit")
        if self.value_basis != "portfolio_change_after_slippage_before_fees/v1":
            raise ValueError("unsupported action outcome value basis")
        for name in ("initial_quantity", "pending_buy_quantity", "pending_sell_quantity",
                     "target_quantity", "horizon_seconds", "data_gap_count"):
            value = getattr(self, name)
            if type(value) is not int or value < (1 if name == "horizon_seconds" else 0):
                raise ValueError(f"action outcome {name} must be a nonnegative integer")
        for name in ("gross_change_won", "fee_tax_won"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or abs(value) > 10**15):
                raise ValueError(f"action outcome {name} must be integer won or unknown")
        if self.fee_tax_won is not None and self.fee_tax_won < 0:
            raise ValueError("action outcome fees/tax must not be negative")
        if self.cost_model_ref is not None and (type(self.cost_model_ref) is not str or not self.cost_model_ref):
            raise ValueError("action outcome cost model ref must be explicit or unknown")
        for value in (self.started_at, self.available_at):
            if not isinstance(value, datetime):
                raise ValueError("action outcome observation times must be aware datetimes")
        for value in (self.started_at, self.completed_at, self.available_at):
            if value is not None:
                if not isinstance(value, datetime):
                    raise ValueError("action outcome observation times must be aware datetimes")
                _aware_datetime(value)


def position_action_cost_model_ref(cost_model: Mapping[str, Any] | None) -> str | None:
    return _content_id("position-action-cost", cost_model) if cost_model is not None else None


@dataclass(frozen=True, kw_only=True)
class UnfilledActionOutcome(PositionActionOutcome):
    """Separate measured pending-LIMIT branches; never relabel HOLD/EXIT."""
    symbol: str
    side: str
    pending_quantity: int
    existing_limit_price_won: int
    candidate_limit_price_won: int
    purpose: str
    order_state: str
    _actions: ClassVar[frozenset[str]] = frozenset({'WAIT','REPRICE'})

    def __post_init__(self):
        super().__post_init__()
        if (self.policy_version != 'unfilled-order-management-shadow/v1'
                or self.execution_model_ref != 'unfilled-paired-limit/v1'
                or self.environment != 'simulation' or self.source_kind != 'simulation'
                or self.venue != 'KRX' or not isinstance(self.symbol,str)
                or len(self.symbol)!=6 or not self.symbol.isascii() or not self.symbol.isdigit()
                or self.side not in {'BUY','SELL'}
                or self.purpose not in {'BUY_OPPORTUNITY','TAKE_PROFIT'}
                or self.order_state not in {'ACCEPTED','PARTIALLY_FILLED','CANCELLED'}
                or self.input_resolution=='minute' and self.horizon_seconds%60!=0
                or (self.side=='BUY') != (self.purpose=='BUY_OPPORTUNITY')):
            raise ValueError('unfilled outcome scope/model must be explicit')
        for name in ('pending_quantity','existing_limit_price_won','candidate_limit_price_won'):
            if type(getattr(self,name)) is not int or getattr(self,name)<=0:
                raise ValueError('unfilled outcome quantities/prices must be positive integers')
        expected=0 if self.order_state=='CANCELLED' else self.pending_quantity
        if (self.pending_buy_quantity if self.side=='BUY' else self.pending_sell_quantity)!=expected:
            raise ValueError('unfilled outcome pending quantity mismatch')


class PositionOutcomeUnavailable(ValueError):
    """A selected native window cannot prove a measured action outcome."""


def position_action_source_context(record: Mapping[str, Any], decision_time: datetime) -> dict[str, Any]:
    """Reuse locked development acceptance before reading execution values."""
    cut = _aware_datetime(decision_time)
    source = _development_partition_result(record)
    if source.status != "ELIGIBLE":
        raise PositionOutcomeUnavailable(f"development_source_{source.status.lower()}")
    spec, manifest = record['spec'], record['input_manifest']
    if spec.get('execution_scope') != 'independent_development_validation/v1':
        raise ValueError('position outcomes require independent development execution')
    if record['run_id'] != _content_id('run', {'dataset_id': manifest['dataset_id'],
            'revision_ids_hash': manifest['revision_ids_hash'], 'spec': spec}):
        raise ValueError('native scientific run identity mismatch')
    if spec.get('data_manifest_hash') != hashlib.sha256(json.dumps(manifest, ensure_ascii=False,
            sort_keys=True, separators=(',', ':')).encode()).hexdigest():
        raise ValueError('native development manifest hash mismatch')
    finished = _aware_datetime(record['finished_at'])
    if finished > cut:
        raise PositionOutcomeUnavailable('source_run_not_available_at_cut')
    if spec.get('cost_model') != record['report'].get('cost_model'):
        raise ValueError('native source cost model mismatch')
    execution = spec['execution_model']
    if (not isinstance(execution, dict) or execution.get('version') != 'next_tradable_bar_open/v1'
            or execution.get('cost_model') != spec.get('cost_model')):
        raise ValueError('unsupported native execution source')
    SimulationExecutionConfig(**{**execution, 'cost_model':
        SimulationCostModel(**spec['cost_model']) if spec.get('cost_model') is not None else None})
    from kiwoom_monitor.application.market_session_schedule import (
        KRX_REGULAR_RESEARCH_PROFILE, research_session_profile_document)
    if spec['session_profile'] != research_session_profile_document(KRX_REGULAR_RESEARCH_PROFILE):
        raise PositionOutcomeUnavailable('native_execution_session_not_supported_by_reader')
    # Bind actual code, parameters, costs and execution semantics; never attach a caller's policy label.
    policy = {key: spec[key] for key in ('code_hash', 'family', 'policy_version', 'parameters', 'execution_model', 'session_profile')}
    position_execution = spec.get('position_action_execution')
    target_policy = None
    execution_identity = execution
    if position_execution is not None:
        from kiwoom_monitor.application.position_target import PositionTargetSizingPolicy
        from kiwoom_monitor.application.research_execution import POSITION_TARGET_EXECUTION_VERSION
        if (not isinstance(position_execution, dict)
                or set(position_execution) != {'version','source_policy_version','target_policy'}
                or position_execution['version'] != POSITION_TARGET_EXECUTION_VERSION
                or type(position_execution['source_policy_version']) is not str
                or not position_execution['source_policy_version'].strip()):
            raise ValueError('invalid native position execution policy')
        raw_policy = dict(position_execution['target_policy'])
        for name in ('available_at','valid_until'): raw_policy[name] = _aware_datetime(raw_policy[name])
        target_policy = PositionTargetSizingPolicy(**raw_policy)
        policy['position_action_execution'] = position_execution
        execution_identity = {'execution': execution,'position_action_execution_version':POSITION_TARGET_EXECUTION_VERSION,
                              'code_hash':spec['code_hash']}
    context = {'run_id': record['run_id'], 'finished_at': finished, 'evaluation': spec['evaluation_spec'], 'role': source.role,
        'warmup_start': manifest['development_partition']['warmup_start'], 'end': source.end,
        'dataset_id': source.input_dataset_id, 'revision_ids_hash': source.input_revision_ids_hash,
        'policy_version': _content_id('native-position-policy', policy),
        'execution_model_ref': _content_id('native-position-execution', execution_identity),
        'cost_model_ref': position_action_cost_model_ref(spec.get('cost_model'))}
    if target_policy is not None:
        context.update(position_target_policy=target_policy, position_source_policy=position_execution['source_policy_version'],
                       cost_model=SimulationCostModel(**spec['cost_model']) if spec.get('cost_model') else None)
        if 'position_target_plan' in spec:
            from kiwoom_monitor.application.position_target import _position_target_request_from_dict
            plan=spec['position_target_plan']
            if (not isinstance(plan,list) or not 1<=len(plan)<=200
                    or len(json.dumps(plan,ensure_ascii=False).encode('utf-8'))>256*1024):
                raise ValueError('invalid native locked target plan')
            requests=tuple(_position_target_request_from_dict(r) for r in plan)
            if (len({(r.decision_time,r.symbol) for r in requests})!=len(requests)
                    or any(r.target_policy!=target_policy or r.policy_version!=context['position_source_policy']
                           or r.cost_model!=context['cost_model'] or r.account_ref!='research-single-position' for r in requests)):
                raise ValueError('native locked target plan scope mismatch')
            context['position_target_plan']=requests
    if 'position_action_study' in spec:
        from kiwoom_monitor.application.position_target import (
            PositionActionStudyPolicy,PositionActionStudyCase,position_action_study_spec)
        raw=spec['position_action_study']
        if not isinstance(raw,dict) or set(raw) != {'version','policy','case','study_policy_ref'}:
            raise ValueError('invalid native position study declaration')
        study=PositionActionStudyPolicy.from_dict(raw['policy']);case=PositionActionStudyCase.from_dict(raw['case'])
        if (raw != position_action_study_spec(study,case,spec) or study.development_role != source.role
                or (case.branch_action == 'HOLD' and target_policy is not None)
                or (case.branch_action != 'HOLD' and context.get('position_target_plan') != (case.request,))):
            raise ValueError('native position study/branch identity mismatch')
        context.update(position_study_policy=study,position_study_case=case,
                       position_study_ref=raw['study_policy_ref'])
        if study.version=='position-action-study/v4':
            from kiwoom_monitor.application.research_families import parse_strategy_config
            inputs=spec.get('native_study_inputs')
            if (not isinstance(inputs,list) or not inputs or len(inputs)>10000
                    or len(json.dumps(inputs,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())>256*1024
                    or spec.get('native_study_inputs_ref')!='native-study-inputs:'+hashlib.sha256(json.dumps(inputs,
                        sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
                    or hashlib.sha256('\n'.join(str(r['revision_id']) for r in inputs).encode()).hexdigest()!=manifest['revision_ids_hash']
                    or spec.get('native_study_episode_session')!=case.request.decision_time.astimezone(
                        ZoneInfo('Asia/Seoul')).date().isoformat()+':KRX_REGULAR'):
                raise ValueError('native study frozen input identity mismatch')
            context.update(native_study_inputs=inputs,native_study_execution=SimulationExecutionConfig(**{
                **execution,'cost_model':SimulationCostModel(**execution['cost_model'])}),
                native_study_strategy=parse_strategy_config(spec['family'],spec['parameters']))
            context['policy_version']=_content_id('native-policy-study-continuation',{
                'base_policy_ref':context['policy_version'],'native_policy':study.native_policy.to_dict(),
                'intervention':'position-target-intervention/v1'})
            context['execution_model_ref']=_content_id('native-policy-study-execution',{
                'base_execution_ref':context['execution_model_ref'],'native_policy':study.native_policy.to_dict(),
                'fill':'next_eligible_open_full_quantity','fill_available_at':'original_bar_available_at',
                'cancel_ack':'unsupported'})
        if 'position_study_context' in spec:
            context['position_study_context']=validate_position_study_historical_context(spec['position_study_context'],manifest,case.request)
        if 'position_action_study_cohort' in spec:
            from kiwoom_monitor.application.position_target import validate_position_action_study_cohort
            context['position_study_cohort']=validate_position_action_study_cohort(
                spec['position_action_study_cohort'],study,case,spec,manifest)
    elif 'position_action_study_cohort' in spec or 'position_study_context' in spec:
        raise ValueError('native cohort is missing its v2 study case declaration')
    if 'unfilled_action_execution' in spec:
        from kiwoom_monitor.application.research_execution import UnfilledResearchAction, UNFILLED_EXECUTION_VERSION
        from kiwoom_monitor.application.research_families import parse_strategy_config
        expected={'version':UNFILLED_EXECUTION_VERSION,
            'fill_basis':'subsequent_minute_open_full_quantity_with_volume/v1',
            'cancel_latency_seconds':0,'partial_fills_supported':False}
        plan=spec.get('unfilled_action_plan')
        if (spec['unfilled_action_execution']!=expected or target_policy is not None
                or not isinstance(plan,list) or not 2<=len(plan)<=200
                or len(json.dumps(plan,ensure_ascii=False).encode())>256*1024):
            raise ValueError('invalid native unfilled execution plan')
        requests=tuple(UnfilledResearchAction.from_dict(r) for r in plan)
        if (len({(r.decision_time,r.symbol) for r in requests})!=len(requests)
                or len({r.symbol for r in requests})!=1
                or tuple(sorted(requests,key=lambda r:r.decision_time))!=requests):
            raise ValueError('invalid native unfilled plan source cuts')
        context.update(unfilled_action_plan=requests,
            unfilled_strategy_family=spec['family'],
            unfilled_source_code_hash=spec['code_hash'],
            unfilled_execution=SimulationExecutionConfig(**{**execution,'cost_model':SimulationCostModel(**spec['cost_model'])}),
            unfilled_strategy=parse_strategy_config(spec['family'],spec['parameters']))
    return context


def unfilled_action_outcome_from_events(context, events, *, start_event_id, end_event_id,
                                        action, case_ref, population_ref, horizon_seconds, symbol):
    """Replay the selected native LIMIT branch through the same paper engine.

    Native read guards must precede this adapter. Queue priority, cancel latency,
    broker acceptance and raw-market capture completeness remain unverified.
    """
    from kiwoom_monitor.application.research_execution import (
        PaperExecutionEngine,PendingOrder,SimulationPosition,SimulationPortfolio,
        UnfilledResearchAction,UNFILLED_EXECUTION_VERSION)
    from kiwoom_monitor.application.breakout_strategy import StrategyState
    if (action not in {'WAIT','REPRICE'} or type(horizon_seconds) is not int
            or not 60<=horizon_seconds<=599_940 or horizon_seconds%60
            or 'unfilled_action_plan' not in context):
        raise PositionOutcomeUnavailable('native_unfilled_model_or_horizon_unavailable')
    ids={e['event_id']:i for i,e in enumerate(events)}
    if start_event_id not in ids or end_event_id not in ids or ids[start_event_id]>=ids[end_event_id]:
        raise PositionOutcomeUnavailable('native_window_endpoints_missing_or_unordered')
    left,right=ids[start_event_id],ids[end_event_id]
    start,end=events[left],events[right]
    if start['event_type']!='UNFILLED_BASELINE' or end['event_type']!='MARK' or start['symbol']!=symbol:
        raise PositionOutcomeUnavailable('native_unfilled_window_valuation_missing')
    proof=json.loads(start['reason'])
    request=UnfilledResearchAction.from_dict(proof['request'])
    if (proof.get('version')!=UNFILLED_EXECUTION_VERSION or request not in context['unfilled_action_plan']
            or (request.action,request.case_ref,request.population_ref,request.symbol)!=(action,case_ref,population_ref,symbol)):
        raise ValueError('native unfilled action differs from its locked plan')
    source=KrxMinuteBarFrame(**proof['source_bar'])
    started,completed=_aware_datetime(start['occurred_at']),_aware_datetime(end['occurred_at'])
    if ((completed-started).total_seconds()!=horizon_seconds
            or _aware_datetime(source.bar_end)!=started
            or source.code!=symbol or source.capture_quality!='complete' or source.close<=0
            or source.session_profile!='krx-regular/v1' or source.market_phase!='CONTINUOUS'
            or (_aware_datetime(source.bar_end)-_aware_datetime(source.bar_start)).total_seconds()!=60
            or assign_interval_to_fold(ResearchEvaluationSpec.from_dict(context['evaluation']),
                    started.isoformat(),completed.isoformat()).status!='INCLUDED'):
        raise PositionOutcomeUnavailable('native_unfilled_window_outside_development_or_horizon')
    raw=dict(proof['initial_portfolio'])
    pending=PendingOrder(**raw['pending_order']) if raw['pending_order'] else None
    if pending is None or pending.limit_execution_version!=UNFILLED_EXECUTION_VERSION:
        raise PositionOutcomeUnavailable('native_initial_pending_limit_unproven')
    active={}
    for event in events[:left]:
        if event['event_type']=='ORDER_SUBMITTED': active[event['intent_id']]=event
        elif event['event_type'] in {'FILL','ORDER_CANCELLED','ORDER_CENSORED','ORDER_UNSUPPORTED'}:
            active.pop(event['intent_id'],None)
    order=active.get(pending.intent_id)
    if (len(active)!=1 or order is None or (order['quantity'],order['price'],order['side'],order['symbol'])!=(
            pending.quantity,pending.limit_price_won,pending.side,pending.symbol)):
        raise PositionOutcomeUnavailable('native_initial_pending_limit_unproven')
    order_proof=json.loads(order['reason'])
    origin=UnfilledResearchAction.from_dict(order_proof['request'])
    if origin not in context['unfilled_action_plan'] or origin.action not in {'SUBMIT','REPRICE'}:
        raise ValueError('native initial LIMIT origin differs from locked plan')
    previous=events[left-1]
    if any(previous[k]!=start[k] for k in ('cash_after_won','reserved_cash_after_won','position_quantity_after')):
        raise ValueError('native initial LIMIT portfolio projection mismatch')
    raw['pending_order']=pending
    raw['position']=SimulationPosition(**raw['position']) if raw['position'] else None
    engine=PaperExecutionEngine(context['run_id'],context['unfilled_execution'],context['unfilled_strategy'],'krx-regular/v1')
    engine.portfolio=SimulationPortfolio(**raw)
    engine.strategy_state=StrategyState(**proof['initial_strategy_state'])
    engine._last_processed_bar=source
    expected=list(engine.process_unfilled_action(request,source_bar=source))
    offset=left
    def match(batch):
        nonlocal offset
        if tuple(e.to_dict() for e in batch)!=tuple(events[offset:offset+len(batch)]):
            raise ValueError('native unfilled execution replay mismatch')
        offset+=len(batch)
    match(expected)
    times=[started]
    revisions=[source.revision_id]
    while offset<=right:
        observation=events[offset]
        if observation['event_type']!='UNFILLED_BAR':
            raise PositionOutcomeUnavailable('native_unfilled_window_mixed_or_censored')
        bar_proof=json.loads(observation['reason'])
        if bar_proof.get('version')!=UNFILLED_EXECUTION_VERSION:
            raise ValueError('native unfilled bar model mismatch')
        bar=KrxMinuteBarFrame(**bar_proof['source_bar'])
        if (bar.code!=symbol or bar.capture_quality!='complete' or bar.market_phase!='CONTINUOUS'
                or bar.session_profile!='krx-regular/v1' or not bar.revision_id
                or (_aware_datetime(bar.bar_end)-_aware_datetime(bar.bar_start)).total_seconds()!=60
                or _aware_datetime(bar.bar_start)!=times[-1]
                or bar.research_session!=source.research_session
                or _aware_datetime(bar.available_at)<_aware_datetime(bar.bar_end)):
            raise PositionOutcomeUnavailable('native_unfilled_minute_gap_or_session')
        batch=[engine.observe_unfilled_bar(bar),*engine.process_bar(bar)]
        if any(e.event_type in {'RISK_FILL','RISK_UNSUPPORTED','ORDER_CENSORED','ORDER_UNSUPPORTED','ORDER_CANCELLED'} for e in batch):
            raise PositionOutcomeUnavailable('native_unfilled_window_mixed_or_censored')
        if not any(e.event_type=='MARK' for e in batch): batch.append(engine.observe_position_value(bar))
        if len(batch)>right-offset+1:
            raise PositionOutcomeUnavailable('native_unfilled_terminal_valuation_missing')
        match(batch)
        times.append(_aware_datetime(bar.bar_end)); revisions.append(bar.revision_id)
    if (times!=[started+timedelta(minutes=i) for i in range(horizon_seconds//60+1)]
            or len(set(revisions))!=len(revisions)):
        raise PositionOutcomeUnavailable('native_unfilled_minute_gap_or_duplicate')
    window=events[left:right+1]
    fees=sum(e['total_cost_won'] for e in window if e['event_type']=='FILL')
    net=end['cash_after_won']+end['position_quantity_after']*end['price']-(
        start['cash_after_won']+start['position_quantity_after']*start['price'])
    state={'symbol':symbol,'side':pending.side,'quantity':pending.quantity,'limit':pending.limit_price_won,
        'submitted_at':pending.submitted_at,'eligible_after':pending.eligible_after,
        'cash':start['cash_after_won'],'reserved':start['reserved_cash_after_won'],
        'held':start['position_quantity_after'],'price':start['price'],'started_at':start['occurred_at'],
        'source_revision_id':start['source_revision_id'],'revision_ids_hash':context['revision_ids_hash'],
        'position':{k:v for k,v in asdict(raw['position']).items() if k!='entry_intent_id'} if raw['position'] else None,
        'strategy':context['unfilled_strategy'].to_dict(),'execution':context['unfilled_execution'].to_dict(),
        'code_hash':context['unfilled_source_code_hash']}
    identity={'run_id':context['run_id'],'start':start_event_id,'end':end_event_id,'revisions':revisions}
    return UnfilledActionOutcome(input_ref=_content_id('native-unfilled-outcome',identity),case_ref=case_ref,
        initial_state_ref=_content_id('native-unfilled-state',state),action=action,
        policy_version='unfilled-order-management-shadow/v1',environment='simulation',venue='KRX',source_kind='simulation',
        execution_model_ref=UNFILLED_EXECUTION_VERSION,initial_quantity=start['position_quantity_after'],
        pending_buy_quantity=pending.quantity if pending.side=='BUY' else 0,
        pending_sell_quantity=pending.quantity if pending.side=='SELL' else 0,target_quantity=request.target_quantity,
        horizon_seconds=horizon_seconds,population_ref=population_ref,input_resolution='minute',
        cost_model_ref=context['cost_model_ref'],started_at=started,completed_at=completed,
        available_at=max(context['finished_at'],*(_aware_datetime(e['received_at']) for e in window)),
        status='COMPLETE',data_gap_count=0,gross_change_won=net+fees,fee_tax_won=fees,
        symbol=symbol,side=pending.side,pending_quantity=pending.quantity,
        existing_limit_price_won=request.existing_limit_price_won,candidate_limit_price_won=request.candidate_limit_price_won,
        purpose='BUY_OPPORTUNITY' if pending.side=='BUY' else 'TAKE_PROFIT',order_state='ACCEPTED')


def position_action_outcome_from_events(context, events, *, start_event_id, end_event_id,
                                       action, case_ref, population_ref, environment, horizon_seconds, symbol):
    """Measured native windows; preserve legacy HOLD/EXIT and require target proofs."""
    if context.get('position_target_policy') is not None and action in {'HOLD','ADD','REDUCE','REENTER'}:
        return _position_target_outcome_from_events(context, events, start_event_id=start_event_id,
            end_event_id=end_event_id, action=action, case_ref=case_ref, population_ref=population_ref,
            environment=environment, horizon_seconds=horizon_seconds, symbol=symbol)
    if action not in {'HOLD', 'EXIT'}:
        raise PositionOutcomeUnavailable('native_action_not_supported_by_reader')
    if (environment != 'mock' or type(horizon_seconds) is not int or not 60 <= horizon_seconds <= 599_940
            or horizon_seconds % 60):
        raise ValueError('native minute source requires explicit mock scenario and whole-minute horizon')
    ids = {event['event_id']: index for index, event in enumerate(events)}
    if start_event_id not in ids or end_event_id not in ids or ids[start_event_id] >= ids[end_event_id]:
        raise PositionOutcomeUnavailable('native_window_endpoints_missing_or_unordered')
    start_index, end_index = ids[start_event_id], ids[end_event_id]
    start, end = events[start_index], events[end_index]
    if start['symbol'] != symbol:
        raise PositionOutcomeUnavailable('native_position_symbol_mismatch')
    started, completed = _aware_datetime(start['occurred_at']), _aware_datetime(end['occurred_at'])
    evaluation = ResearchEvaluationSpec.from_dict(context['evaluation'])
    if assign_interval_to_fold(evaluation, started.isoformat(), completed.isoformat()).status != 'INCLUDED':
        raise PositionOutcomeUnavailable('native_window_outside_development_or_purged')
    if (completed - started).total_seconds() != horizon_seconds:
        raise PositionOutcomeUnavailable('native_window_horizon_mismatch')
    if start['event_type'] != 'MARK' or start['position_quantity_after'] <= 0:
        raise PositionOutcomeUnavailable('native_initial_held_valuation_missing')
    active = {}
    for event in events[:start_index + 1]:
        kind = event['event_type']
        if kind == 'ORDER_SUBMITTED': active[event['intent_id']] = event
        elif kind in {'FILL', 'ORDER_CANCELLED', 'ORDER_CENSORED', 'ORDER_UNSUPPORTED'}:
            active.pop(event['intent_id'], None)
    if active or start['reserved_cash_after_won']:
        raise PositionOutcomeUnavailable('native_initial_open_order_unresolved')
    window = events[start_index + 1:end_index + 1]
    if any(event['symbol'] != start['symbol'] or event['execution_environment'] != 'simulation'
           or event['event_type'] not in {'MARK', 'ORDER_SUBMITTED', 'FILL'} for event in window):
        raise PositionOutcomeUnavailable('native_window_mixed_or_censored_execution')
    initial = start['position_quantity_after']
    fills = [event for event in window if event['event_type'] == 'FILL']
    orders = [event for event in window if event['event_type'] == 'ORDER_SUBMITTED']
    if action == 'HOLD':
        if orders or fills or end['event_type'] != 'MARK' or any(
                e['position_quantity_after'] != initial or e['cash_after_won'] != start['cash_after_won'] for e in window):
            raise PositionOutcomeUnavailable('native_hold_not_proven')
    else:
        if (len(orders) != 1 or len(fills) != 1 or end is not fills[0] or fills[0]['side'] != 'SELL'
                or orders[0]['side'] != 'SELL' or fills[0]['quantity'] != initial
                or orders[0]['quantity'] != initial or orders[0]['intent_id'] != fills[0]['intent_id']
                or end['position_quantity_after'] != 0):
            raise PositionOutcomeUnavailable('native_exit_not_proven')
        if end['cash_after_won'] != start['cash_after_won'] + end['notional_won'] - end['total_cost_won']:
            raise ValueError('native exit cash projection mismatch')
    marks = [start, *(event for event in window if event['event_type'] == 'MARK'), end]
    expected_times = {started + timedelta(seconds=60 * index) for index in range(horizon_seconds // 60 + 1)}
    if {_aware_datetime(e['occurred_at']) for e in marks} != expected_times:
        raise PositionOutcomeUnavailable('native_minute_valuation_gap')
    if any(e['reserved_cash_after_won'] for e in window) or any(
            e['position_quantity_after'] != initial or e['cash_after_won'] != start['cash_after_won']
            for e in window if e['event_type'] == 'MARK'):
        raise PositionOutcomeUnavailable('native_portfolio_projection_incomplete')
    for event in (start, *window):
        if (event['event_type'] in {'MARK', 'FILL'} and (not event['source_revision_id']
                or (event['position_quantity_after'] > 0 and event['price'] <= 0))):
            raise PositionOutcomeUnavailable('native_valuation_price_or_source_missing')
        if event['total_cost_won'] != event['commission_won'] + event['tax_won']:
            raise ValueError('native execution cost mismatch')
    costs = sum(event['total_cost_won'] for event in fills)
    net = end['cash_after_won'] + end['position_quantity_after'] * end['price'] - (
        start['cash_after_won'] + initial * start['price'])
    state = {key: start[key] for key in ('symbol', 'occurred_at', 'price', 'cash_after_won',
                                       'reserved_cash_after_won', 'position_quantity_after', 'source_revision_id')}
    native = {'run_id': context['run_id'], 'start': start_event_id, 'end': end_event_id,
              'dataset_id': context['dataset_id'], 'revision_ids_hash': context['revision_ids_hash']}
    return PositionActionOutcome(_content_id('native-position-outcome', native), case_ref,
        _content_id('native-position-state', state), action, context['policy_version'], environment, 'KRX',
        'simulation', context['execution_model_ref'], initial, 0, 0, initial if action == 'HOLD' else 0,
        horizon_seconds, population_ref, 'minute', context['cost_model_ref'], started, completed,
        max(context['finished_at'], *(_aware_datetime(e['received_at']) for e in (start, *window))),
        'COMPLETE', 0, net + costs, costs)


def _position_target_prefix_state(events, start_index, symbol):
    """Reuse native pending/full-close facts for target values and v2 case proofs."""
    active={};closed_before_start=False
    for event in events[:start_index+1]:
        if event['event_type']=='ORDER_SUBMITTED':active[event['intent_id']]=event
        elif event['event_type'] in {'FILL','ORDER_CANCELLED','ORDER_CENSORED','ORDER_UNSUPPORTED'}:
            submitted=active.pop(event['intent_id'],None)
            if (event['event_type']=='FILL' and event['side']=='SELL' and event['position_quantity_after']==0
                    and event['symbol']==symbol and event['quantity']>0 and submitted is not None
                    and submitted['side']=='SELL' and submitted['quantity']==event['quantity']):
                closed_before_start=True
    return active,closed_before_start


def _position_target_outcome_from_events(context, events, *, start_event_id, end_event_id,
                                         action, case_ref, population_ref, environment, horizon_seconds, symbol):
    """Replay actual cash/share transitions and the original automatic target proof."""
    from kiwoom_monitor.application.position_target import _position_target_request_from_dict, evaluate_position_target_shadow
    from kiwoom_monitor.application.research_execution import POSITION_TARGET_EXECUTION_VERSION, _fee_won, estimate_buy_cash_won
    from kiwoom_monitor.domain.order_contract import AccountSnapshot
    if environment != 'mock' or type(horizon_seconds) is not int or not 60 <= horizon_seconds <= 599_940 or horizon_seconds % 60:
        raise ValueError('native minute source requires explicit mock scenario and whole-minute horizon')
    ids = {e['event_id']:i for i,e in enumerate(events)}
    if start_event_id not in ids or end_event_id not in ids or ids[start_event_id] >= ids[end_event_id]:
        raise PositionOutcomeUnavailable('native_window_endpoints_missing_or_unordered')
    left,right = ids[start_event_id],ids[end_event_id]
    start,end = events[left],events[right]
    started,completed = _aware_datetime(start['occurred_at']),_aware_datetime(end['occurred_at'])
    if (end['event_type'] != 'MARK' or start['event_type'] != 'MARK' or start['symbol'] != symbol
            or (completed-started).total_seconds() != horizon_seconds):
        raise PositionOutcomeUnavailable('native_target_window_valuation_or_horizon_missing')
    if assign_interval_to_fold(ResearchEvaluationSpec.from_dict(context['evaluation']),started.isoformat(),completed.isoformat()).status != 'INCLUDED':
        raise PositionOutcomeUnavailable('native_window_outside_development_or_purged')
    active,closed_before_start=_position_target_prefix_state(events,left,symbol)
    if active or start['reserved_cash_after_won']:
        raise PositionOutcomeUnavailable('native_initial_open_order_unresolved')
    initial=start['position_quantity_after']
    if (action in {'ADD','REDUCE'} and initial<=0) or action=='REENTER' and (initial!=0 or not closed_before_start):
        raise PositionOutcomeUnavailable('native_target_initial_state_not_proven')
    window=events[left:right+1]
    if any(e['symbol']!=symbol or e['execution_environment']!='simulation' or e['event_type'] not in {'MARK','ORDER_SUBMITTED','FILL'}
           for e in window):
        raise PositionOutcomeUnavailable('native_window_mixed_or_censored_execution')
    marks=[e for e in window if e['event_type']=='MARK']
    times=[_aware_datetime(e['occurred_at']) for e in marks]
    expected=[started+timedelta(seconds=60*i) for i in range(horizon_seconds//60+1)]
    if times!=expected:
        raise PositionOutcomeUnavailable('native_minute_valuation_gap')
    if any(e['event_type'] in {'MARK','FILL'} and (not e['source_revision_id'] or e['price']<=0) for e in window):
        raise PositionOutcomeUnavailable('native_valuation_price_or_source_missing')
    orders=[e for e in window if e['event_type']=='ORDER_SUBMITTED']
    fills=[e for e in window if e['event_type']=='FILL']
    if action=='HOLD' and (orders or fills) or action!='HOLD' and (len(orders)!=1 or len(fills)!=1):
        raise PositionOutcomeUnavailable('native_target_order_or_fill_not_proven')
    cash,quantity,reserved=start['cash_after_won'],initial,0
    pending=None; quote=start; target=None; costs=0; policy_version=context['policy_version']
    for event in window:
        kind=event['event_type']
        if _aware_datetime(event['received_at']) < _aware_datetime(event['occurred_at']):
            raise ValueError('native target availability precedes occurrence')
        if kind=='ORDER_SUBMITTED':
            proof=json.loads(event['reason'])
            if set(proof)!={'version','request'} or proof['version']!=POSITION_TARGET_EXECUTION_VERSION:
                raise ValueError('native target execution proof missing')
            request=_position_target_request_from_dict(proof['request'])
            if 'position_target_plan' in context and request not in context['position_target_plan']:
                raise ValueError('native target is absent from its locked plan')
            at=_aware_datetime(quote['received_at'])
            if (request.action!=action or request.target_policy!=context['position_target_policy']
                    or request.policy_version!=context['position_source_policy'] or request.cost_model!=context['cost_model']
                    or request.account_ref!='research-single-position' or request.symbol!=symbol
                    or request.decision_time!=at or request.reference_price_available_at!=at
                    or request.reference_price_won!=quote['price'] or event['source_revision_id']!=quote['source_revision_id']
                    or _aware_datetime(event['occurred_at'])!=at or _aware_datetime(event['received_at'])!=at):
                raise ValueError('native target source policy/cut mismatch')
            account=AccountSnapshot('research-single-position',cash,0,{symbol:quantity} if quantity else {},at)
            result=evaluate_position_target_shadow(request,account,())
            if (result['quality']['status']!='CALCULATED' or not result['incremental_quantity']
                    or event['decision_id']!=result['decision_id'] or event['side']!=result['incremental_side']
                    or event['quantity']!=result['incremental_quantity'] or event['state']!='PENDING'
                    or event['intent_id']!=_content_id('intent',{'run_id':context['run_id'],'decision_id':result['decision_id'],
                                                             'side':event['side'],'quantity':event['quantity']})):
                raise ValueError('native target calculated intent mismatch')
            policy_version=result['target_policy_version']; target=result['target_quantity']; pending=event
            reserved=estimate_buy_cash_won(quote['price'],event['quantity'],context['cost_model']) if event['side']=='BUY' else 0
            if any(event[k] for k in ('price','notional_won','commission_won','tax_won','total_cost_won','realized_pnl_won')):
                raise ValueError('native submitted target contains realized values')
        elif kind=='FILL':
            if pending is None or any(event[k]!=pending[k] for k in ('intent_id','decision_id','side','quantity')) or event['state']!='FILLED':
                raise ValueError('native target fill has no matching intent')
            submitted=_aware_datetime(pending['occurred_at'])
            eligible=submitted.replace(second=0,microsecond=0)
            if submitted>eligible: eligible+=timedelta(minutes=1)
            if _aware_datetime(event['occurred_at'])!=eligible:
                raise PositionOutcomeUnavailable('native_target_fill_not_next_open')
            cost=context['cost_model']
            if cost is None: raise PositionOutcomeUnavailable('native_target_cost_model_missing')
            notional=event['price']*event['quantity']
            commission=_fee_won(notional,cost.commission_bps)
            tax=_fee_won(notional,cost.sell_tax_bps) if event['side']=='SELL' else 0
            if (event['notional_won'],event['commission_won'],event['tax_won'],event['total_cost_won'])!=(notional,commission,tax,commission+tax):
                raise ValueError('native target fill cost mismatch')
            costs+=commission+tax
            if event['side']=='BUY':
                cash-=notional+commission; quantity+=event['quantity']
                if cash<context['position_target_policy'].cash_buffer_won:
                    raise PositionOutcomeUnavailable('native_target_cash_buffer_exceeded')
            else:
                cash+=notional-commission-tax; quantity-=event['quantity']
            reserved=0; pending=None
        else:
            if (event['quantity']!=quantity or event['notional_won']!=quantity*event['price']
                    or any(event[k] for k in ('commission_won','tax_won','total_cost_won','realized_pnl_won'))):
                raise ValueError('native target valuation projection mismatch')
            quote=event
        if (event['cash_after_won'],event['position_quantity_after'],event['reserved_cash_after_won'])!=(cash,quantity,reserved) or quantity<0:
            raise ValueError('native target cash/share projection mismatch')
    if pending or reserved or action!='HOLD' and quantity!=target:
        raise PositionOutcomeUnavailable('native_target_not_completely_filled')
    state={k:start[k] for k in ('symbol','occurred_at','price','cash_after_won','reserved_cash_after_won','position_quantity_after','source_revision_id')}
    native={'run_id':context['run_id'],'start':start_event_id,'end':end_event_id,
            'dataset_id':context['dataset_id'],'revision_ids_hash':context['revision_ids_hash']}
    net=cash+quantity*end['price']-(start['cash_after_won']+initial*start['price'])
    return PositionActionOutcome(_content_id('native-position-outcome',native),case_ref,_content_id('native-position-state',state),
        action,policy_version,environment,'KRX','simulation',context['execution_model_ref'],initial,0,0,quantity,
        horizon_seconds,population_ref,'minute',context['cost_model_ref'],started,completed,
        max(context['finished_at'],*(_aware_datetime(e['received_at']) for e in window)),'COMPLETE',0,net+costs,costs)


def read_position_action_outcomes(load_evidence, selections, *, decision_time, horizon_seconds,
                                 population_ref, environment, symbol, development_role='TRAIN', checkpoint=None):
    return _read_action_outcomes(load_evidence,selections,decision_time=decision_time,horizon_seconds=horizon_seconds,
        population_ref=population_ref,environment=environment,symbol=symbol,development_role=development_role,
        checkpoint=checkpoint)


def read_position_action_study_case(load_evidence, selection, *, decision_time, population_ref,
                                    development_role='TRAIN', checkpoint=None):
    """Read one declared v2 native case. Preserves the original v1 outcome ID.

    A historical goal must be valid at its own cut, not at outcome completion.
    This is a case proof, not a paired cohort or a current executable target.
    """
    from kiwoom_monitor.application.position_target import evaluate_position_target_shadow,_json_value
    from kiwoom_monitor.domain.order_contract import AccountSnapshot
    from kiwoom_monitor.application.research_execution import SimulationPortfolio,SimulationPosition
    from kiwoom_monitor.application.breakout_strategy import StrategyState
    keys={'run_id','start_event_id','end_event_id','action','case_ref'}
    if (not isinstance(selection,dict) or set(selection) != keys
            or any(type(v) is not str or not 0 < len(v) <= 256 for v in selection.values())
            or development_role not in {'TRAIN','VALIDATION'}
            or type(population_ref) is not str or not 0 < len(population_ref) <= 256):
        raise ValueError('invalid explicit native study selection')
    cut=_aware_datetime(decision_time)
    if checkpoint is not None:checkpoint()
    context,events=load_evidence(selection['run_id'],(selection['end_event_id'],),decision_time=cut,
        limit=10000,development_role=development_role,**({'checkpoint':checkpoint} if checkpoint else {}))
    if not isinstance(events,(tuple,list)) or not 1 <= len(events) <= 10000:
        raise ValueError('native study execution source budget exceeded')
    if context.get('position_study_policy') is None:
        raise ValueError('native v2 study declaration required; v1 cannot be promoted')
    study=context['position_study_policy'];case=context['position_study_case'];request=case.request
    if (selection['case_ref'] != case.case_ref or selection['action'] != case.branch_action
            or context['run_id'] != selection['run_id'] or context['role'] != development_role
            or selection['action'] not in {'ADD','REDUCE','REENTER','HOLD'}):
        raise PositionOutcomeUnavailable('native_study_branch_not_supported_or_mismatched')
    proofs=[e for e in events if e['event_type']=='POSITION_STUDY_CASE']
    if len(proofs) != 1:raise ValueError('native study requires exactly one case proof')
    event=proofs[0]
    if len(event['reason'].encode()) > 256*1024:raise ValueError('native study proof budget exceeded')
    proof=json.loads(event['reason'])
    proof_keys={'version','study_policy_ref','case','source_bar','portfolio','strategy_state','goal'}
    if 'position_study_context' in context:proof_keys.add('historical_context')
    conditional=study.version in {'position-action-study/v3','position-action-study/v4'}
    if conditional:proof_keys.add('support_observation')
    if (not isinstance(proof,dict) or set(proof) != proof_keys
            or proof['version'] != ('position-action-study-case/'+study.version.rsplit('/',1)[1]) or proof['case'] != case.to_dict()
            or proof['study_policy_ref'] != context['position_study_ref']):
        raise ValueError('native study case proof identity mismatch')
    if 'historical_context' in proof and proof['historical_context']!=context['position_study_context']:
        raise ValueError('native study historical context differs from original declaration')
    source=KrxMinuteBarFrame(**proof['source_bar'])
    at=_aware_datetime(source.available_at)
    start=next((e for e in events if e['event_id']==selection['start_event_id']),None)
    end=next((e for e in events if e['event_id']==selection['end_event_id']),None)
    if (start is None or end is None or not events.index(start) < events.index(event) < events.index(end)
            or any(e['event_type'] in {'ORDER_SUBMITTED','FILL','RISK_FILL'}
                for e in events[events.index(start)+1:events.index(event)])):
        raise ValueError('native study proof is not before its branch execution')
    raw=dict(proof['portfolio']);position=SimulationPosition(**raw['position']) if raw['position'] is not None else None
    if raw['pending_order'] is not None:raise PositionOutcomeUnavailable('native_study_pending_order_unsupported')
    portfolio=SimulationPortfolio(**{**raw,'position':position})
    state=StrategyState(**{**proof['strategy_state'],'emitted_candidate_keys':tuple(proof['strategy_state']['emitted_candidate_keys'])})
    actual=position.quantity if position is not None else 0
    if request.action=='REENTER':
        state_invalid=(position is not None or actual!=0 or state.status!='cooldown' or state.symbol
            or state.entry_price is not None or state.position_quantity is not None or state.opened_at or not state.cooldown_until)
        if not state_invalid:_aware_datetime(state.cooldown_until)
        active,closed=_position_target_prefix_state(events,events.index(start),request.symbol)
        if active or not closed:raise PositionOutcomeUnavailable('native_study_reentry_prior_close_unverified')
    else:
        state_invalid=(actual<=0 or position.symbol!=request.symbol
            or any(type(v) is not int or v<0 for v in (position.entry_notional,position.entry_commission))
            or not 0<position.entry_price or _aware_datetime(position.opened_at)>at
            or state.status!='open' or state.symbol!=request.symbol or state.position_quantity!=actual
            or state.entry_price!=position.entry_price or state.opened_at!=position.opened_at)
    if (start is None or start['event_type'] != 'MARK' or source.code != request.symbol
            or at != request.decision_time or at != request.reference_price_available_at
            or source.close != request.reference_price_won or source.market_phase != 'CONTINUOUS'
            or source.capture_quality != 'complete' or source.session_profile != 'krx-regular/v1'
            or not source.research_session or at < _aware_datetime(source.bar_end)
            or (_aware_datetime(source.bar_end)-_aware_datetime(source.bar_start)).total_seconds() != 60
            or _aware_datetime(start['occurred_at']) != _aware_datetime(source.bar_end)
            or start['source_revision_id'] != source.revision_id or start['price'] != source.close
            or portfolio.reserved_cash_won or state_invalid
            or any(type(v) is not int or v < 0 for v in (portfolio.cash_won,actual))):
        raise ValueError('native study actual state/source mismatch')
    expected_event={'execution_environment':'simulation','account_ref':'research-single-position','intent_id':'',
        'event_type':'POSITION_STUDY_CASE','state':'OBSERVED','symbol':source.code,'side':'','quantity':0,
        'price':source.close,'source_revision_id':source.revision_id,'cash_after_won':portfolio.cash_won,
        'reserved_cash_after_won':0,'position_quantity_after':actual,'optimistic_exit_price':None}
    if (any(event[k] != v for k,v in expected_event.items())
            or _aware_datetime(event['occurred_at']) != _aware_datetime(source.bar_end)
            or _aware_datetime(event['received_at']) != at
            or any(event[k] for k in ('notional_won','commission_won','tax_won','total_cost_won','realized_pnl_won'))
            or (start['cash_after_won'],start['reserved_cash_after_won'],start['position_quantity_after']) != (portfolio.cash_won,0,actual)):
        raise ValueError('native study event/initial valuation mismatch')
    account=AccountSnapshot('research-single-position',portfolio.cash_won,0,{source.code:actual} if actual else {},at)
    goal=evaluate_position_target_shadow(request,account,())
    normalized=json.loads(json.dumps(goal,default=_json_value))
    if (normalized != proof['goal'] or event['decision_id'] != goal['decision_id']
            or goal['quality']['status'] != 'CALCULATED' or goal['funding_shortfall_quantity']):
        raise ValueError('native study original goal replay mismatch')
    if conditional:
        from kiwoom_monitor.application.position_target import position_study_support_observation
        if proof['support_observation']!=position_study_support_observation(study,request,goal,
                proof['source_bar'],proof['strategy_state'],proof['portfolio']):
            raise ValueError('conditional study support observation replay mismatch')
    # Only this fully validated metadata event is removed from the legacy value
    # adapter's view. Original run, endpoints, policy/outcome IDs remain intact.
    measured=[e for e in events if e is not event]
    if checkpoint is not None:checkpoint()
    # A validated flat v2 baseline uses the same cash/share value adapter as
    # REENTER, without adding a target declaration or an order to HOLD.
    adapter=(_position_target_outcome_from_events if request.action=='REENTER' and selection['action']=='HOLD'
        else position_action_outcome_from_events)
    if study.version=='position-action-study/v4':
        outcome=_native_policy_study_outcome(context,events,proof,event,selection,population_ref,checkpoint)
    else:
        outcome=adapter(context,measured,start_event_id=selection['start_event_id'],
            end_event_id=selection['end_event_id'],action=selection['action'],case_ref=selection['case_ref'],
            population_ref=population_ref,environment='mock',horizon_seconds=study.horizon_seconds,symbol=request.symbol)
    if not outcome.completed_at <= outcome.available_at <= cut:
        raise PositionOutcomeUnavailable('native_study_outcome_not_available_at_cut')
    body={'version':'native-position-study-case/'+study.version.rsplit('/',1)[1],'study_policy_ref':context['position_study_ref'],
        'selection':dict(selection),'outcome':json.loads(json.dumps(asdict(outcome),default=_json_value)),
        'original_goal':normalized,'original_goal_valid_until':request.target_policy.valid_until.isoformat(),
        'decision_proof':proof,'proof_event_ref':event['event_id'],
        'available_at':outcome.available_at.isoformat(),
        'completion_after_goal_expiry':outcome.available_at >= request.target_policy.valid_until,
        'source_receipt':{'run_id':context['run_id'],'dataset_id':context['dataset_id'],
            'revision_ids_hash':context['revision_ids_hash'],'event_count':len(events),'development_role':context['role']},
        'quality':{'status':'NATIVE_CASE_PROOF','reasons':[]},
        'r4_status':'EV_UNAVAILABLE','missing':['independent_paired_cohort','exact_context_support_binding'],
        'source_durability':'not_verified','order_authorized':False}
    body['evidence_ref']=_content_id('native-position-study-case',body)
    return body


def _native_policy_study_outcome(context,events,proof,case_event,selection,population_ref,checkpoint):
    """Replay the complete v4 path, including shared judgments and terminal orders."""
    from kiwoom_monitor.application.research_execution import PaperExecutionEngine,SimulationPortfolio,SimulationPosition
    from kiwoom_monitor.application.breakout_strategy import StrategyState
    from kiwoom_monitor.application.research_replay import replay_krx_minute_bars,replay_candidate_universe
    from kiwoom_monitor.application.research_families import family_for_config
    from kiwoom_monitor.application.position_target import _json_value
    inputs=context['native_study_inputs'];bars=replay_krx_minute_bars(inputs,session_profile='krx-regular/v1')
    universe=replay_candidate_universe(inputs)
    bar_map={b.revision_id:asdict(b) for b in bars};universe_map={f.revision_id:asdict(f) for f in universe}
    def bar(raw):
        if raw!=json.loads(json.dumps(bar_map.get(raw.get('revision_id')),default=_json_value)):
            raise ValueError('native policy bar is absent from frozen inputs')
        return KrxMinuteBarFrame(**raw)
    source=bar(proof['source_bar']);start=next(e for e in events if e['event_id']==selection['start_event_id'])
    end=next(e for e in events if e['event_id']==selection['end_event_id'])
    prefix=[e for e in events[:events.index(start)+1] if e['event_type'] in {'FILL','RISK_FILL','PARTIAL_FILL'}]
    position=SimulationPosition(**proof['portfolio']['position'])
    active,_=_position_target_prefix_state(events,events.index(start),source.code)
    if (len(prefix)!=1 or prefix[0]['event_type']!='FILL' or prefix[0]['side']!='BUY'
            or (prefix[0]['quantity'],prefix[0]['price'],prefix[0]['notional_won'],prefix[0]['commission_won'],prefix[0]['intent_id'])!=
                (position.quantity,position.entry_price,position.entry_notional,position.entry_commission,position.entry_intent_id)
            or active or prefix[0]['tax_won'] or proof['portfolio']['cash_won']+position.entry_notional+position.entry_commission!=
                context['native_study_execution'].initial_cash_won):
        raise ValueError('native policy first-entry prefix mismatch')
    from kiwoom_monitor.application.research_execution import _slipped_price,_fee_won
    entry=prefix[0];entry_bar=bar_map.get(entry['source_revision_id']);cost=context['native_study_execution'].cost_model
    if (entry_bar is None or entry['occurred_at']!=position.opened_at
            or _aware_datetime(entry['occurred_at'])!=_aware_datetime(entry_bar['bar_start'])
            or _aware_datetime(entry['received_at'])!=_aware_datetime(entry_bar['available_at'])
            or _aware_datetime(entry['received_at'])>_aware_datetime(source.available_at)
            or entry['price']!=_slipped_price(entry_bar['open'],'BUY',cost.slippage_bps)
            or entry['notional_won']!=entry['quantity']*entry['price']
            or entry['commission_won']!=_fee_won(entry['notional_won'],cost.commission_bps)):
        raise ValueError('native policy first-entry price cost or availability mismatch')
    engine=PaperExecutionEngine(context['run_id'],context['native_study_execution'],context['native_study_strategy'],'krx-regular/v1')
    engine.portfolio=SimulationPortfolio(**{**proof['portfolio'],'position':position})
    engine.strategy_state=StrategyState(**{**proof['strategy_state'],'emitted_candidate_keys':tuple(proof['strategy_state']['emitted_candidate_keys'])})
    engine._last_processed_bar=source
    observed=engine.observe_position_study_case(context['position_study_ref'],context['position_study_policy'],
        context['position_study_case'],source_bar=source,historical_context=context.get('position_study_context'))
    if observed.to_dict()!=case_event:raise ValueError('native policy initial event replay mismatch')
    offset=events.index(case_event)+1;right=events.index(end);family=family_for_config(engine.strategy)
    while offset<=right:
        if checkpoint is not None:checkpoint()
        event=events[offset];raw=json.loads(event['reason'])
        if event['event_type']=='NATIVE_POLICY_BAR':batch=engine.process_bar(bar(raw['source_bar']))
        elif event['event_type']=='NATIVE_POLICY_FAMILY':
            current=bar(raw['source_bar']);history=tuple(bar(r) for r in raw['bar_history']);frames=[]
            if current!=engine._native_study['bar'] or len({b.revision_id for b in history})!=len(history):
                raise ValueError('native Family input cut or duplicate mismatch')
            for r in raw['universe_frames']:
                match=universe_map.get(r.get('revision_id'))
                if r!=json.loads(json.dumps(match,default=_json_value)):raise ValueError('native Family universe input absent')
                frames.append(next(f for f in universe if f.revision_id==r['revision_id']))
            cut=_aware_datetime(current.available_at)
            if any(_aware_datetime(b.available_at)>cut for b in (*history,*frames)):
                raise ValueError('native Family input from future')
            required_bars={b.revision_id for b in bars if b.research_session==current.research_session
                and _aware_datetime(b.available_at)<=cut}
            required_universe={f.revision_id for f in universe if _aware_datetime(f.available_at).astimezone(ZoneInfo('Asia/Seoul')).date()==
                cut.astimezone(ZoneInfo('Asia/Seoul')).date() and _aware_datetime(f.available_at)<=cut}
            if not required_bars<={b.revision_id for b in history} or not required_universe<={f.revision_id for f in frames}:
                raise ValueError('native Family available input prefix incomplete')
            evaluation=family.evaluate_bar(run_id=engine.run_id,evaluation_bar=current,bar_history=history,
                universe_frames=tuple(frames),config=engine.strategy,state=engine.strategy_state)
            batch=engine.process_native_family(evaluation.decision,evaluation.state,source_bar=current,
                bar_history=history,universe_frames=tuple(frames))
        else:raise ValueError('native policy path input missing or reordered')
        if not batch or tuple(e.to_dict() for e in batch)!=tuple(events[offset:offset+len(batch)]):
            raise ValueError('native policy transition replay mismatch')
        offset+=len(batch)
        if engine._native_study['error']:
            raise PositionOutcomeUnavailable(engine._native_study['error'])
    study=context['position_study_policy'];started=_aware_datetime(source.bar_end);completed=_aware_datetime(end['occurred_at'])
    if (not engine._native_study['ended'] or end['event_type']!='MARK' or offset!=right+1
            or (completed-started).total_seconds()!=study.horizon_seconds
            or assign_interval_to_fold(ResearchEvaluationSpec.from_dict(context['evaluation']),
                started.isoformat(),completed.isoformat()).status!='INCLUDED'):
        raise PositionOutcomeUnavailable('native policy terminal horizon incomplete')
    window=events[events.index(case_event)+1:right+1];fees=sum(e['total_cost_won'] for e in window if e['event_type']=='FILL')
    net=end['cash_after_won']+end['position_quantity_after']*end['price']-(start['cash_after_won']+start['position_quantity_after']*start['price'])
    initial={**proof['portfolio'],'position':{k:v for k,v in proof['portfolio']['position'].items() if k!='entry_intent_id'}}
    identity={'run':context['run_id'],'start':start['event_id'],'end':end['event_id'],'policy':study.to_dict()}
    return PositionActionOutcome(_content_id('native-policy-outcome',identity),selection['case_ref'],
        _content_id('native-policy-initial',{'portfolio':initial,'state':proof['strategy_state'],'source':proof['source_bar']}),
        selection['action'],context['policy_version'],'mock','KRX','simulation',context['execution_model_ref'],
        position.quantity,0,0,proof['goal']['target_quantity'] if selection['action']=='REDUCE' else position.quantity,
        study.horizon_seconds,population_ref,'minute',context['cost_model_ref'],started,completed,
        max(context['finished_at'],*(_aware_datetime(e['received_at']) for e in window)),'COMPLETE',0,net+fees,fees)


def read_position_action_study_cohort(load_evidence,cohort,selections,*,decision_time,
                                      development_role='TRAIN',checkpoint=None):
    """Describe all frozen v2 native pairs; never approve a current RAM goal.

    Each original outcome/policy/model ID is retained. Study and economic-state
    proofs supply a separate v2 binding; only the existing paired math is reused.
    """
    from kiwoom_monitor.application.position_target import PositionActionStudyCase,_json_value
    keys={'version','study_policy_ref','dataset_id','revision_ids_hash','development_role','cases',
          'minimum_samples','max_evidence_age_seconds','model_valid_until','cohort_ref'}
    if (not isinstance(cohort,dict) or set(cohort) != keys or cohort['version'] not in {'position-action-study-cohort/v2','position-action-study-cohort/v3','position-action-study-cohort/v4'}
            or not isinstance(cohort['cases'],list) or not 2 <= len(cohort['cases']) <= 100
            or not isinstance(selections,(tuple,list)) or len(selections) > 200
            or type(cohort['minimum_samples']) is not int or not 2 <= cohort['minimum_samples'] <= len(cohort['cases'])
            or type(cohort['max_evidence_age_seconds']) is not int or cohort['max_evidence_age_seconds'] < 1
            or development_role not in {'TRAIN','VALIDATION'} or cohort['development_role'] != development_role
            or len(json.dumps(cohort,allow_nan=False).encode()) > 256*1024):
        raise ValueError('invalid bounded native study cohort')
    canonical=json.dumps({k:v for k,v in cohort.items() if k!='cohort_ref'},sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    if cohort['cohort_ref'] != 'position-action-study-cohort:'+hashlib.sha256(canonical.encode()).hexdigest():
        raise ValueError('study cohort content mismatch')
    cut=_aware_datetime(decision_time);model_until=_aware_datetime(cohort['model_valid_until'])
    conditional=cohort['version']!='position-action-study-cohort/v2'
    declared=tuple(PositionActionStudyCase.from_dict(row) for row in cohort['cases'])
    if len({c.case_ref for c in declared}) != len(declared):raise ValueError('duplicate declared study case')
    action=declared[0].request.action
    wanted={(c.case_ref,a) for c in declared for a in ('HOLD',action)}
    selection_keys={'run_id','start_event_id','end_event_id','action','case_ref'}
    selected=[];physical=set();runs=set();seen=set()
    for row in selections:
        if (not isinstance(row,dict) or set(row) != selection_keys
                or any(type(v) is not str or not 0 < len(v) <= 256 for v in row.values())):
            raise ValueError('invalid study cohort endpoint selection')
        key=(row['case_ref'],row['action']);identity=(row['run_id'],row['start_event_id'],row['end_event_id'])
        if key in seen or identity in physical or row['run_id'] in runs or key not in wanted:
            raise ValueError('duplicate, renamed, or undeclared native study branch')
        seen.add(key);physical.add(identity);runs.add(row['run_id']);selected.append(dict(row))
    # A declaration cannot shrink when an attempted branch fails or is absent.
    missing=sorted(wanted-seen)
    body={'version':'native-position-study-cohort-comparison/'+cohort['version'].rsplit('/',1)[1],'cohort_ref':cohort['cohort_ref'],
        'study_policy_ref':cohort['study_policy_ref'],'symbol':declared[0].request.symbol,'action':action,
        'cases':[],'pairs':[],'excluded':[],'missing_branches':missing,'result':None,
        'quality':{'status':'EV_UNAVAILABLE','reasons':[]},'r4_status':'EV_UNAVAILABLE',
        'current_goal_status':'EV_UNAVAILABLE','usable_for_current_goal':False,
        'missing_current_support':['exact_context_support_binding','allocation_input_lineage_independence','current_account_risk_binding'],
        'source_durability':'not_verified','order_authorized':False,
        'probability_calibration':'not_verified','independent_cases_assumption':'not_verified'}
    remaining=10000;receipts={};total_bytes=0;support_study=None
    def borrow(run_id,endpoints,**kwargs):
        nonlocal remaining,support_study
        if remaining < 1:raise ValueError('total native study event budget exceeded')
        context,events=load_evidence(run_id,endpoints,**{**kwargs,'limit':remaining})
        if not isinstance(events,(tuple,list)) or len(events) > remaining:
            raise ValueError('total native study event budget exceeded')
        remaining-=len(events)
        if context.get('position_study_cohort') != cohort:
            raise ValueError('native run lacks the same predeclared study cohort')
        if conditional:
            study=context.get('position_study_policy')
            if (study is None or study.version!='position-action-study/'+cohort['version'].rsplit('/',1)[1]
                    or (support_study is not None and support_study!=study)):
                raise ValueError('conditional study source policy mismatch')
            support_study=study
        return context,events
    for row in sorted(selected,key=lambda r:(r['case_ref'],r['action'])):
        if checkpoint is not None:checkpoint()
        try:
            receipt=read_position_action_study_case(borrow,row,decision_time=cut,
                population_ref=cohort['cohort_ref'],development_role=development_role,checkpoint=checkpoint)
        except PositionOutcomeUnavailable as error:
            body['excluded'].append({**row,'reason':str(error)});continue
        total_bytes+=len(json.dumps(receipt,ensure_ascii=False).encode())
        if total_bytes > 4*1024*1024:raise ValueError('native study aggregate case proof budget exceeded')
        if receipt['study_policy_ref'] != cohort['study_policy_ref']:
            raise ValueError('native study binding mismatch')
        receipts[(row['case_ref'],row['action'])]=receipt;body['cases'].append(receipt)
    reasons=body['quality']['reasons']
    if missing:reasons.append('frozen_cohort_branch_missing')
    if body['excluded']:reasons.append('frozen_cohort_branch_incomplete_or_unsupported')
    initial_quantity=target_quantity=price=None
    used_refs=set();pairs=[];available=[];expiry=[model_until];cost_ref=None
    for case in declared:
        if checkpoint is not None:checkpoint()
        a=receipts.get((case.case_ref,action));b=receipts.get((case.case_ref,'HOLD'))
        if a is None or b is None:continue
        pa,pb=a['decision_proof'],b['decision_proof']
        def economic_state(proof):
            position=proof['portfolio']['position']
            portfolio={**proof['portfolio'],'position':{k:v for k,v in position.items() if k!='entry_intent_id'} if position is not None else None}
            return {'portfolio':portfolio,'strategy_state':proof['strategy_state'],'source_bar':proof['source_bar']}
        oa,ob=a['outcome'],b['outcome']
        if (economic_state(pa) != economic_state(pb) or pa['case']['request'] != pb['case']['request']
                or pa.get('historical_context') != pb.get('historical_context')
                or pa['goal'] != pb['goal']
                or any(oa[k] != ob[k] for k in ('initial_state_ref','started_at','completed_at','initial_quantity',
                    'pending_buy_quantity','pending_sell_quantity','cost_model_ref','horizon_seconds','value_basis'))
                or oa['status'] != 'COMPLETE' or ob['status'] != 'COMPLETE'
                or oa['target_quantity'] != a['original_goal']['target_quantity']
                or ob['target_quantity'] != ob['initial_quantity']):
            reasons.append('study_pair_initial_state_market_or_goal_mismatch');continue
        scope=(oa['initial_quantity'],oa['target_quantity'],case.request.reference_price_won,oa['cost_model_ref'])
        if initial_quantity is None:initial_quantity,target_quantity,price,cost_ref=scope
        elif scope != (initial_quantity,target_quantity,price,cost_ref):
            reasons.append('study_cohort_exact_quantity_price_or_cost_mismatch');continue
        for sample in (oa,ob):
            if sample['input_ref'] in used_refs:raise ValueError('duplicate effective native study outcome')
            used_refs.add(sample['input_ref'])
            at=_aware_datetime(sample['available_at']);available.append(at)
            expiry.append(at+timedelta(seconds=cohort['max_evidence_age_seconds']))
            cost=a['original_goal']['cost_model']
            if not cost.get('valid_from') or not cost.get('valid_to'):
                reasons.append('study_cost_validity_unknown')
            else:
                if not _aware_datetime(cost['valid_from']) <= _aware_datetime(sample['started_at']) < _aware_datetime(sample['completed_at']) <= _aware_datetime(cost['valid_to']):
                    reasons.append('study_outcome_outside_cost_validity')
                expiry.append(_aware_datetime(cost['valid_to']))
        state_ref=_content_id('native-study-economic-state',economic_state(pa))
        body['pairs'].append({'case_ref':case.case_ref,'economic_state_ref':state_ref,
            'action_evidence_ref':a['evidence_ref'],'hold_evidence_ref':b['evidence_ref'],
            'original_action_policy_ref':oa['policy_version'],'original_hold_policy_ref':ob['policy_version'],
            'original_action_execution_ref':oa['execution_model_ref'],'original_hold_execution_ref':ob['execution_model_ref']})
        pairs.append((oa,ob))
    if len(pairs) != len(declared) or len(pairs) < cohort['minimum_samples']:
        reasons.append('complete_frozen_study_pairs_required')
    if available:
        body['available_at']=max(available).isoformat();body['valid_until']=min(expiry).isoformat()
        if not max(available) <= cut < min(expiry):reasons.append('study_cohort_evidence_expired_or_future')
    if not reasons:
        # This is the same Fraction/fee/empirical-error calculation used by R4 v1.
        difference=tuple((a['gross_change_won']-b['gross_change_won'],a['fee_tax_won']-b['fee_tax_won']) for a,b in pairs)
        denominator=abs(target_quantity-initial_quantity)*price
        if not denominator:raise ValueError('study cohort has no target exposure change')
        body['result']={'action':action,'baseline_action':'HOLD','paired_sample_count':len(pairs),
            'initial_quantity':initial_quantity,'target_quantity':target_quantity,'reference_price_won':price,
            'cost_model_ref':cost_ref,'horizon_seconds':pairs[0][0]['horizon_seconds'],
            'estimate':_position_action_estimate(tuple((a['gross_change_won'],a['fee_tax_won']) for a,_ in pairs)),
            'baseline_estimate':_position_action_estimate(tuple((b['gross_change_won'],b['fee_tax_won']) for _,b in pairs)),
            'paired_delta':_position_action_estimate(difference,bps_notional_won=denominator),
            'return_denominator':'changed_exposure_notional_won/v1','return_denominator_won':denominator,
            'execution_support':'declared_native_minute_study_only','order_authorized':False}
        body['quality']['status']='EMPIRICAL_ESTIMATE';body['r4_status']='COHORT_COMPARABLE'
    if conditional:
        body['conditional_cells']=_position_study_conditional_cells(body,support_study)
    body['quality']['reasons']=sorted(set(reasons))
    body['evidence_ref']=_content_id('native-position-study-cohort',body)
    return body


def _position_study_conditional_cells(comparison,study):
    """All predeclared pairs, grouped by the fixed initial-observation rule."""
    if study is None:return []
    indexed={(row['selection']['case_ref'],row['selection']['action']):row for row in comparison['cases']}
    groups={}
    for row in comparison['cases']:
        if row['selection']['action']!='REDUCE':continue
        observation=row['decision_proof']['support_observation']
        groups.setdefault(observation['cell_ref'],[]).append(row)
    result=[]
    for ref,rows in sorted(groups.items()):
        reasons=list(comparison['quality']['reasons']);differences=[];days=[];pair_refs=[]
        for row in rows:
            hold=indexed.get((row['selection']['case_ref'],'HOLD'))
            if hold is None:
                reasons.append('conditional_cell_pair_missing');continue
            observation=row['decision_proof']['support_observation']
            if observation!=hold['decision_proof']['support_observation']:
                raise ValueError('conditional pair support observation mismatch')
            action_outcome,hold_outcome=row['outcome'],hold['outcome']
            differences.append((action_outcome['gross_change_won']-action_outcome['fee_tax_won'])
                -(hold_outcome['gross_change_won']-hold_outcome['fee_tax_won']))
            days.append(observation['trading_date'])
            pair_refs.append({'case_ref':row['selection']['case_ref'],'action_evidence_ref':row['evidence_ref'],
                'hold_evidence_ref':hold['evidence_ref']})
        count=len(differences);day_count=len(set(days));spec=study.support_spec
        if count<spec.minimum_pairs:reasons.append('conditional_cell_minimum_pairs_not_met')
        if day_count<spec.minimum_trading_days:reasons.append('conditional_cell_minimum_trading_days_not_met')
        estimate=None
        if not reasons:
            mean=Fraction(sum(differences),count)
            clusters={day:sum((Fraction(d)-mean for d,t in zip(differences,days) if t==day),Fraction()) for day in set(days)}
            variance=Fraction(day_count,day_count-1)*sum((v*v for v in clusters.values()),Fraction())/(count*count)
            denominator=comparison['result']['return_denominator_won'];scale=Fraction(10000,denominator)
            squared=variance*scale*scale;root=isqrt(squared.numerator//squared.denominator)
            estimate={'paired_sample_count':count,'trading_day_count':day_count,
                'mean_net_change_won':float(mean),'net_standard_error_won':sqrt(float(variance)),
                'mean_net_change_bps_floor':int(mean*scale//1),
                'net_standard_error_bps_ceil':root+int(root*root*squared.denominator<squared.numerator),
                'uncertainty_method':spec.uncertainty_method,'return_denominator_won':denominator,
                'value_basis':study.value_basis,'fee_tax_treatment':'subtract_measured_action_minus_hold_fees/v1'}
        result.append({'cell_ref':ref,'cell':rows[0]['decision_proof']['support_observation']['cell'],
            'status':'SUPPORTED_UNDER_PAPER_ASSUMPTIONS' if estimate is not None else 'UNAVAILABLE',
            'reasons':sorted(set(reasons)),'result':estimate,'pair_refs':pair_refs,
            'paired_sample_count':count,'trading_day_count':day_count,
            'independence_verified':False,'probability_calibration':'not_verified',
            'usable_for_native_selection':False,'order_authorized':False})
    return result


def evaluate_position_study_conditional_shadow(model,request,target,*,source_bar,strategy_state,portfolio,source_continuation,
        account_snapshot=None,broker_open_orders=None,native_policy=None):
    """Match available current Paper inputs to a replayed v3 cell; no native EV."""
    from kiwoom_monitor.application.position_target import (
        PositionActionConditionalStudyPolicy,position_study_support_observation,evaluate_position_target_shadow)
    from kiwoom_monitor.domain.order_contract import AccountSnapshot
    study=model['study'];comparison=model['comparison'];cut=_aware_datetime(request.decision_time)
    result={'status':'UNAVAILABLE','reasons':[],'result':None,'model_publication_ref':model['publication_ref'],
        'scope':'conditional_registered_family_paper_minute_only',
        'usable_for_native_selection':False,'order_authorized':False,'independence_verified':False,
        'probability_calibration':'not_verified','input_refs':[]}
    if not isinstance(study,PositionActionConditionalStudyPolicy):
        result['reasons']=['conditional_v3_source_required'];return result
    reasons=result['reasons'];policy=request.target_policy
    if study.version=='position-action-study/v4':
        result['policy_support']={'profile':study.native_policy.to_dict(),'native_action_mapping_verified':False,
            'sampled_price':'available_minute_close','execution':'next_open_full_quantity_paper_assumption',
            'native_execution_equivalence_verified':False}
        if _content_id('native-study-policy',native_policy)!=_content_id('native-study-policy',study.native_policy.to_dict()):
            reasons.append('current_native_policy_observation_unavailable_or_mismatch');return result
    if (comparison['r4_status']!='COHORT_COMPARABLE' or policy is None
            or request.policy_version!=study.source_policy_version or request.action!='REDUCE'
            or request.max_snapshot_age_seconds!=study.max_snapshot_age_seconds
            or tuple(getattr(policy,k) for k in ('version','target_notional_won','max_position_notional_won','max_position_quantity','cash_buffer_won'))
                !=tuple(getattr(study,k) for k in ('sizing_version','target_notional_won','max_position_notional_won','max_position_quantity','cash_buffer_won'))
            or not request.reconciliation_complete or not request.open_orders_complete):
        reasons.append('conditional_current_goal_scope_mismatch')
    if source_continuation!=model['source_continuation']:reasons.append('conditional_current_continuation_mismatch')
    if (not max(_aware_datetime(comparison['available_at']),_aware_datetime(model['publication_available_at']))<=cut<_aware_datetime(comparison['valid_until'])
            or policy is None or not policy.available_at<=cut<policy.valid_until
            or (policy.valid_until-policy.available_at).total_seconds()>study.max_decision_age_seconds):
        reasons.append('conditional_source_or_goal_lease_invalid')
    result['valid_until']=min(_aware_datetime(comparison['valid_until']),policy.valid_until).isoformat() if policy else comparison['valid_until']
    if reasons:return result
    try:
        raw_position=portfolio['position'];quantity=raw_position['quantity'] if raw_position is not None else 0
        account=account_snapshot if account_snapshot is not None else AccountSnapshot(request.account_ref,
            portfolio['cash_won'],portfolio['reserved_cash_won'],{request.symbol:quantity},cut)
        if (not isinstance(account,AccountSnapshot) or account.account_ref!=request.account_ref
                or account.available_cash_won!=portfolio['cash_won'] or account.reserved_open_buy_won!=portfolio['reserved_cash_won']
                or account.positions!={request.symbol:quantity}
                or not 0<=(cut-_aware_datetime(account.as_of)).total_seconds()<request.max_snapshot_age_seconds):
            raise ValueError('current portfolio/account cut mismatch')
        replay=evaluate_position_target_shadow(request,account,(),broker_open_orders=broker_open_orders)
        if replay!=target:raise ValueError('current Paper target replay mismatch')
        observation=position_study_support_observation(study,request,target,source_bar,strategy_state,portfolio)
    except (ValueError,TypeError,KeyError,AttributeError):
        reasons.append('conditional_current_observation_invalid');return result
    result['cell_ref']=observation['cell_ref'];result['input_refs']=[observation['source_revision_id']]
    result['current_observation_ref']=_content_id('position-study-current-support',observation)
    result['valid_until']=min(_aware_datetime(result['valid_until']),
        _aware_datetime(observation['source_available_at'])+timedelta(seconds=study.max_snapshot_age_seconds)).isoformat()
    cells=[cell for cell in comparison['conditional_cells'] if cell['cell_ref']==observation['cell_ref']]
    if len(cells)!=1 or cells[0]['cell']!=observation['cell']:
        result['status']='OUT_OF_SUPPORT';reasons.append('conditional_exact_cell_not_observed');return result
    cell=cells[0];result['status']=cell['status'];result['reasons']=list(cell['reasons'])
    result['result']=json.loads(json.dumps(cell['result'],allow_nan=False))
    result['pair_refs']=json.loads(json.dumps(cell['pair_refs'],allow_nan=False))
    result['available_at']=max(_aware_datetime(comparison['available_at']),_aware_datetime(model['publication_available_at']),
        _aware_datetime(observation['source_available_at']),policy.available_at).isoformat()
    result['paired_sample_count']=cell['paired_sample_count'];result['trading_day_count']=cell['trading_day_count']
    if study.version=='position-action-study/v4' and result['result'] is not None:
        result['status']='SUPPORTED_UNDER_NATIVE_POLICY_PAPER_ASSUMPTIONS'
    return result


def prepare_position_action_study_publication(load_evidence,cohort,selections,*,decision_time,
        development_role='TRAIN',checkpoint=None):
    """Borrow each native source once and retain its original replay record."""
    sources=[]
    def borrow(*args,**kwargs):
        context,events=load_evidence(*args,**kwargs,include_source_record=True)
        context=dict(context);record=context.pop('source_record',None)
        if record is None:raise ValueError('native source record required for study publication')
        sources.append({'record':record,'events':list(events)})
        return context,events
    comparison=read_position_action_study_cohort(borrow,cohort,selections,decision_time=decision_time,
        development_role=development_role,checkpoint=checkpoint)
    if comparison['r4_status']!='COHORT_COMPARABLE':raise ValueError('only a complete native study can be published')
    body={'version':'native-position-study-publication/'+cohort['version'].rsplit('/',1)[1],'cohort':cohort,'selections':list(selections),
        'decision_time':_aware_datetime(decision_time).isoformat(),'comparison':comparison,
        'sources':sorted(sources,key=lambda s:s['record']['run_id'])}
    # Freeze a detached JSON snapshot before crossing the existing publication boundary.
    body=json.loads(json.dumps(body,ensure_ascii=False,allow_nan=False))
    body['publication_ref']=_content_id('native-position-study-publication',body)
    if len(json.dumps(body,ensure_ascii=False,allow_nan=False).encode())>4*1024*1024:
        raise ValueError('native study publication exceeds 4 MiB')
    return body


def _decode_native_position_sources(documents,cut,checkpoint=None):
    """Shared bounded original record/event validation; no external reads."""
    sources={};contexts={};event_count=0
    for source in documents:
        if checkpoint is not None:checkpoint()
        if not isinstance(source,dict) or set(source)!={'record','events'}:
            raise ValueError('invalid native study source record')
        record,events=source['record'],source['events']
        if not isinstance(record,dict) or 'final_execution' not in record or record['final_execution'] is not None:
            raise ValueError('native study final source is forbidden')
        context=position_action_source_context(record,cut);run_id=context['run_id']
        if run_id in sources or not isinstance(events,list) or not events:
            raise ValueError('duplicate or empty native study source')
        event_count+=len(events)
        if event_count>10000:raise ValueError('native study publication event budget exceeded')
        identities=set()
        for event in events:
            if checkpoint is not None:checkpoint()
            if (not isinstance(event,dict) or event.get('run_id')!=run_id
                    or event.get('account_ref')!='research-single-position'
                    or event.get('execution_environment')!='simulation'
                    or event.get('event_id') in identities
                    or event.get('event_id')!=_content_id('execution',{k:v for k,v in event.items() if k!='event_id'})
                    or not _aware_datetime(context['warmup_start'])<=_aware_datetime(event['occurred_at'])< _aware_datetime(context['end'])
                    or not _aware_datetime(context['warmup_start'])<=_aware_datetime(event['received_at'])< _aware_datetime(context['end'])
                    or max(_aware_datetime(event['occurred_at']),_aware_datetime(event['received_at']))>cut):
                raise ValueError('native study event identity/time mismatch')
            if any(type(event.get(key)) is not int or event[key]<0 for key in (
                    'quantity','price','cash_after_won','reserved_cash_after_won','position_quantity_after',
                    'commission_won','tax_won','total_cost_won','notional_won')):
                raise ValueError('native study event quantity/value invalid')
            identities.add(event['event_id'])
        sources[run_id]=events;contexts[run_id]=context
    return sources,contexts


def decode_position_action_study_publication(document,*,checkpoint=None):
    """Replay a PC-reported native study; no database/capture or order approval."""
    keys={'version','cohort','selections','decision_time','comparison','sources','publication_ref'}
    if (not isinstance(document,dict) or set(document)!=keys
            or document['version'] not in {'native-position-study-publication/v2','native-position-study-publication/v3','native-position-study-publication/v4'}
            or len(json.dumps(document,ensure_ascii=False,allow_nan=False).encode())>4*1024*1024
            or not isinstance(document['sources'],list) or not 4<=len(document['sources'])<=200):
        raise ValueError('invalid bounded native study publication')
    if document['publication_ref']!=_content_id('native-position-study-publication',
            {k:v for k,v in document.items() if k!='publication_ref'}):
        raise ValueError('native study publication content mismatch')
    cut=_aware_datetime(document['decision_time'])
    if document['cohort']['version']!=('position-action-study-cohort/'+document['version'].rsplit('/',1)[1]):
        raise ValueError('native study publication/cohort version mismatch')
    sources,contexts=_decode_native_position_sources(document['sources'],cut,checkpoint)
    borrowed=set()
    def borrow(run_id,endpoints,**kwargs):
        if (run_id not in sources or run_id in borrowed or len(endpoints)!=1
                or sources[run_id][-1]['event_id']!=endpoints[0] or len(sources[run_id])>kwargs['limit']):
            raise ValueError('native study publication source/endpoints mismatch')
        borrowed.add(run_id);return contexts[run_id],sources[run_id]
    comparison=read_position_action_study_cohort(borrow,document['cohort'],document['selections'],
        decision_time=cut,development_role=document['cohort']['development_role'],checkpoint=checkpoint)
    if (borrowed!=set(sources) or comparison!=document['comparison']
            or comparison['r4_status']!='COHORT_COMPARABLE' or comparison['usable_for_current_goal'] is not False):
        raise ValueError('native study publication replay mismatch')
    first=next(iter(contexts.values()))
    historical={}
    for context in contexts.values():
        document_context=context.get('position_study_context')
        if document_context is not None:
            ref=document_context['context_ref']
            if ref not in historical:
                if checkpoint is not None:checkpoint()
                historical[ref]={**replay_position_study_historical_context(document_context),'case_refs':[]}
            case_ref=context['position_study_case'].case_ref
            if case_ref not in historical[ref]['case_refs']:historical[ref]['case_refs'].append(case_ref)
    return {'comparison':comparison,'study':first['position_study_policy'],
        **({'publication_available_at':cut.isoformat()} if document['version']!='native-position-study-publication/v2' else {}),
        'source_continuation':{key:document['sources'][0]['record']['spec'][key] for key in
            ('code_hash','family','policy_version','parameters','execution_model','session_profile')},
        'source_counts':{run_id:len(events) for run_id,events in sources.items()},
        'source_bar_counts':{run_id:sum(event['event_type']=='MARK' for event in events) for run_id,events in sources.items()},
        'source_windows':[{'run_id':run_id,'dataset_id':context['dataset_id'],
            'revision_ids_hash':context['revision_ids_hash'],'warmup_start':context['warmup_start'],'end':context['end']}
            for run_id,context in sorted(contexts.items())],
        **({'historical_contexts':list(historical.values())} if historical else {}),
        'publication_ref':document['publication_ref']}


def read_dynamic_allocation_ev_evidence(load_evidence, selections, *, request, account, open_orders,
        horizon_seconds, population_ref, minimum_samples, max_evidence_age_seconds,
        development_role='TRAIN', checkpoint=None, include_source_records=False):
    """Reuse native replay and paired R4 comparison at exactly one BUY size.

    This explicit read is outside the realtime loop. It supplies no exit Capacity,
    stress bound, durable capture proof, external publication or order authority.
    Explicit include_source_records borrows the original header in the same read.
    """
    from kiwoom_monitor.application.position_target import (
        PositionTargetShadowRequest, PositionActionComparisonParameters,
        DynamicAllocationEvEvidence, evaluate_position_action_comparison_shadow)
    if (not isinstance(request,PositionTargetShadowRequest) or request.action not in {'ADD','REENTER'}
            or request.environment!='mock' or request.venue!='KRX' or request.cost_model is None
            or type(max_evidence_age_seconds) is not int or max_evidence_age_seconds<1
            or type(minimum_samples) is not int or not 2<=minimum_samples<=100
            or not isinstance(selections,(list,tuple)) or not 4<=len(selections)<=200
            or any(not isinstance(r,dict) or r.get('action') not in {'HOLD',request.action} for r in selections)):
        raise ValueError('explicit bounded native allocation BUY/HOLD scope required')
    if type(include_source_records) is not bool:raise ValueError('explicit native source option requires bool')
    cuts={};records=[]
    def borrow(run_id, endpoints, **kwargs):
        context,events=load_evidence(run_id,endpoints,**kwargs,**({'include_source_record':True} if include_source_records else {}))
        if include_source_records:
            context=dict(context);record=context.pop('source_record',None)
            if record is None:raise ValueError('native EV original source record required')
            records.append({'record':record,'events':list(events)})
        cuts[run_id]=(context,{e['event_id']:e for e in events})
        return context,events
    outcomes,source=read_position_action_outcomes(borrow,selections,decision_time=request.decision_time,
        horizon_seconds=horizon_seconds,population_ref=population_ref,environment='mock',symbol=request.symbol,
        development_role=development_role,checkpoint=checkpoint)
    receipt={'version':'dynamic-allocation-native-ev/v1','symbol':request.symbol,
        'action':request.action,'evidence':None,'source_receipt':source,'quality':{'status':'EV_UNAVAILABLE','reasons':[]},
        'source_durability':'not_verified','order_authorized':False,
        'missing_producers':['conservative_full_exit_capacity','stress_loss_model']}
    reasons=receipt['quality']['reasons']
    if source['excluded'] or len(outcomes)!=len(selections):
        reasons.append('native_allocation_selection_incomplete');return receipt
    policies={o.policy_version for o in outcomes if o.action=='HOLD'}
    models={o.execution_model_ref for o in outcomes}
    if len(policies)!=1 or len(models)!=1:
        reasons.append('native_allocation_model_or_baseline_mixed');return receipt
    for selection in selections:
        context,events=cuts[selection['run_id']]
        if (context.get('position_target_policy') is None
                or events[selection['start_event_id']]['price']!=request.reference_price_won):
            reasons.append('native_allocation_price_or_target_model_mismatch');return receipt
    initial=account.positions.get(request.symbol,0)
    hold=replace(request,action='HOLD',target_quantity=initial,target_policy=None,policy_version=next(iter(policies)))
    comparison=evaluate_position_action_comparison_shadow(
        PositionActionComparisonParameters('HOLD',horizon_seconds,population_ref,'minute','simulation',
            next(iter(models)),minimum_samples), (hold,request),account,open_orders,outcomes)
    row=next(r for r in comparison['actions'] if r['action']==request.action)
    if (comparison['status']!='COMPARABLE' or row['paired_sample_count']*2!=len(outcomes)
            or row['excluded_counts'] or row['unpaired_sample_count'] or row['baseline_unpaired_sample_count']):
        reasons.extend(row['reasons'] or ('native_allocation_complete_pairs_required',));return receipt
    target=comparison['targets'][request.action]['target_quantity']
    available=max(o.available_at for o in outcomes)
    expiry=min(o.available_at+timedelta(seconds=max_evidence_age_seconds) for o in outcomes)
    if request.cost_model.valid_to:expiry=min(expiry,_aware_datetime(request.cost_model.valid_to))
    if request.target_policy is not None:expiry=min(expiry,request.target_policy.valid_until)
    if not available<=request.decision_time<expiry:
        reasons.append('native_allocation_evidence_expired_or_future');return receipt
    denominator=(target-initial)*request.reference_price_won
    # Conservative integer bps: floor signed mean, ceil empirical standard error.
    # This is cohort variability, not calibrated probability or a confidence bound.
    baseline={o.case_ref:o for o in outcomes if o.action=='HOLD'}
    scaled=_position_action_estimate(tuple((o.gross_change_won-baseline[o.case_ref].gross_change_won,
        o.fee_tax_won-baseline[o.case_ref].fee_tax_won) for o in outcomes if o.action==request.action),
        bps_notional_won=denominator)
    mean=scaled['mean_net_change_bps_floor'];uncertainty=scaled['net_standard_error_bps_ceil']
    receipt['evidence']=DynamicAllocationEvEvidence(request.symbol,request.action,initial,target,
        request.reference_price_won,mean,uncertainty,position_action_cost_model_ref(request.cost_model.to_dict()),
        horizon_seconds,population_ref,comparison['decision_id'],tuple(ref for ref,_ in row['evidence_refs']),available,expiry)
    receipt['quality']['status']='EMPIRICAL_ESTIMATE'
    receipt['comparison']=comparison
    receipt['outcomes']=json.loads(json.dumps([asdict(o) for o in outcomes],
        default=lambda v:_aware_datetime(v).astimezone(timezone.utc).isoformat()))
    receipt['max_evidence_age_seconds']=max_evidence_age_seconds
    if include_source_records:
        body={'version':'native-allocation-ev-source-publication/v1','decision_time':request.decision_time.isoformat(),
            'selections':list(selections),'sources':sorted(records,key=lambda s:s['record']['run_id']),
            'source_receipt':source,'outcomes':receipt['outcomes']}
        body=json.loads(json.dumps(body,ensure_ascii=False,allow_nan=False))
        body['publication_ref']=_content_id('native-allocation-ev-source-publication',body)
        decode_dynamic_allocation_ev_source_publication(body)
        receipt['native_source_publication']=body
    return receipt


def decode_dynamic_allocation_ev_source_publication(document,*,checkpoint=None):
    """Replay original reported EV inputs, not independently attest their storage."""
    keys={'version','decision_time','selections','sources','source_receipt','outcomes','publication_ref'}
    if (not isinstance(document,dict) or set(document)!=keys
            or document['version']!='native-allocation-ev-source-publication/v1'
            or len(json.dumps(document,ensure_ascii=False,allow_nan=False).encode())>4*1024*1024
            or not isinstance(document['sources'],list) or not 1<=len(document['sources'])<=200
            or not isinstance(document['outcomes'],list) or not 4<=len(document['outcomes'])<=200):
        raise ValueError('invalid bounded native EV source publication')
    if document['publication_ref']!=_content_id('native-allocation-ev-source-publication',
            {k:v for k,v in document.items() if k!='publication_ref'}):
        raise ValueError('native EV source publication content mismatch')
    cut=_aware_datetime(document['decision_time'])
    sources,contexts=_decode_native_position_sources(document['sources'],cut,checkpoint)
    original=document['source_receipt'];first=document['outcomes'][0];borrowed=set()
    def borrow(run_id,endpoints,**kwargs):
        if (run_id not in sources or run_id in borrowed or not endpoints
                or sources[run_id][-1]['event_id'] not in endpoints or len(sources[run_id])>kwargs['limit']
                or contexts[run_id]['role']!=kwargs['development_role']):
            raise ValueError('native EV source publication source/endpoints mismatch')
        borrowed.add(run_id);return contexts[run_id],sources[run_id]
    outcomes,receipt=read_position_action_outcomes(borrow,document['selections'],decision_time=cut,
        horizon_seconds=first['horizon_seconds'],population_ref=first['population_ref'],environment='mock',
        symbol=original['target_symbol'],development_role=original['development_role'],checkpoint=checkpoint)
    serialized=json.loads(json.dumps([asdict(o) for o in outcomes],default=lambda v:_aware_datetime(v).astimezone(timezone.utc).isoformat()))
    if (borrowed!=set(sources) or receipt!=original or receipt['excluded'] or serialized!=document['outcomes']):
        raise ValueError('native EV original source/outcome replay mismatch')
    return {'publication_ref':document['publication_ref'],'outcomes':serialized,'source_receipt':receipt,
        'source_counts':{run_id:len(events) for run_id,events in sources.items()},
        'source_windows':[{'run_id':run_id,'dataset_id':context['dataset_id'],
            'revision_ids_hash':context['revision_ids_hash'],'warmup_start':context['warmup_start'],'end':context['end']}
            for run_id,context in sorted(contexts.items())],
        'source_validation':'PC_READONLY_REPLAY_REPORTED','source_durability':'not_verified','order_authorized':False}


def audit_position_study_ev_lineage(study,ev_source):
    """Conservative overlap of revalidated reported inputs; no independence claim."""
    left=study['source_windows'];right=ev_source['source_windows']
    runs={r['run_id'] for r in left}&{r['run_id'] for r in right}
    # Renaming a dataset does not hide an identical input revision hash.
    revisions={r['revision_ids_hash'] for r in left}&{r['revision_ids_hash'] for r in right}
    left_windows=[(_aware_datetime(r['warmup_start']),_aware_datetime(r['end'])) for r in left]
    right_windows=[(_aware_datetime(r['warmup_start']),_aware_datetime(r['end'])) for r in right]
    overlaps=sum(a[0]<=b[1] and b[0]<=a[1] for a in left_windows for b in right_windows)
    shared={r['outcome']['input_ref'] for r in study['comparison']['cases']}&{r['input_ref'] for r in ev_source['outcomes']}
    reasons=[]
    for value,reason in ((runs,'shared_native_run'),(revisions,'shared_input_revision_hash'),
            (overlaps,'overlapping_declared_source_windows'),(shared,'shared_native_outcomes')):
        if value:reasons.append(reason)
    return {'version':'position-study-allocation-lineage-audit/v1',
        'study_publication_ref':study['publication_ref'],'ev_source_publication_ref':ev_source['publication_ref'],
        'status':'REPORTED_OVERLAP' if reasons else 'REPORTED_DISJOINT',
        'shared_run_count':len(runs),'shared_revision_hash_count':len(revisions),
        'overlapping_window_pair_count':overlaps,'shared_outcome_count':len(shared),
        'reasons':reasons,'independence_verified':False,'source_durability':'not_verified','order_authorized':False}


def read_dynamic_allocation_ev_document(document, load_evidence, selections, *, development_role):
    """Strict existing CLI boundary; native selections are read, never relabelled."""
    from kiwoom_monitor.application.position_target import (
        _position_target_request_from_dict, _position_account_orders_from_dict)
    keys={'schema','request','account','open_orders','horizon_seconds','population_ref',
        'minimum_samples','max_evidence_age_seconds'}
    if (not isinstance(document,dict) or set(document) not in (keys,keys|{'include_source_records'})
            or document['schema']!='dynamic-allocation-native-ev/v1'):
        raise ValueError('unsupported native allocation EV document')
    request=_position_target_request_from_dict(document['request'])
    account,orders=_position_account_orders_from_dict(document['account'],document['open_orders'])
    result=read_dynamic_allocation_ev_evidence(load_evidence,selections,request=request,account=account,open_orders=orders,
        development_role=development_role,include_source_records=document.get('include_source_records',False),
        **{k:document[k] for k in ('horizon_seconds','population_ref','minimum_samples','max_evidence_age_seconds')})
    if result['evidence'] is not None:
        result['evidence']=asdict(result['evidence'])
        for name in ('available_at','valid_until'):result['evidence'][name]=result['evidence'][name].isoformat()
    return result


def read_full_exit_stress_evidence(load_evidence, selection, policy, *, decision_time,
        max_evidence_age_seconds, development_role='TRAIN', checkpoint=None):
    """Borrow market bar proofs from one guarded native run, not its trade EV.

    The complete recorded bar window is the input to a new declared scenario
    replay. Its old WAIT/REPRICE fills/outcomes supply no Capacity or stress.
    """
    from kiwoom_monitor.application.research_execution import (
        FullExitStressPolicy, simulate_full_exit_stress, UNFILLED_EXECUTION_VERSION, UnfilledResearchAction)
    from kiwoom_monitor.application.position_target import FullExitStressEvidence
    if (not isinstance(policy,FullExitStressPolicy) or not isinstance(selection,dict)
            or set(selection)!={'run_id','start_event_id','end_event_id'}
            or any(type(v) is not str or not 0<len(v)<=256 for v in selection.values())
            or type(max_evidence_age_seconds) is not int or max_evidence_age_seconds<1):
        raise ValueError('explicit bounded native full-exit source required')
    receipt={'version':'dynamic-allocation-native-exit/v1','evidence':None,'result':None,
        'quality':{'status':'UNAVAILABLE','reasons':[]},'source_durability':'not_verified','order_authorized':False,
        'input_basis':'native_market_bar_proofs_not_unfilled_trade_outcomes/v1'}
    try:
        context,events=load_evidence(selection['run_id'],(selection['end_event_id'],),decision_time=decision_time,
            limit=10000,development_role=development_role,**({'checkpoint':checkpoint} if checkpoint else {}))
    except PositionOutcomeUnavailable as error:
        receipt['quality']['reasons'].append(str(error));return receipt
    ids={e['event_id']:i for i,e in enumerate(events)}
    if ('unfilled_action_plan' not in context or selection['start_event_id'] not in ids
            or selection['end_event_id'] not in ids or ids[selection['start_event_id']]>=ids[selection['end_event_id']]):
        receipt['quality']['reasons'].append('native_full_exit_market_bar_source_unavailable');return receipt
    start,end=events[ids[selection['start_event_id']]],events[ids[selection['end_event_id']]]
    if start['event_type']!='UNFILLED_BASELINE' or end['event_type']!='MARK' or end['symbol']!=policy.symbol:
        receipt['quality']['reasons'].append('native_full_exit_window_endpoints_unavailable');return receipt
    proof=json.loads(start['reason'])
    source=KrxMinuteBarFrame(**proof['source_bar'])
    request=UnfilledResearchAction.from_dict(proof['request'])
    if (proof['version']!=UNFILLED_EXECUTION_VERSION or source.code!=policy.symbol
            or request not in context['unfilled_action_plan'] or request.symbol!=source.code
            or request.decision_time!=_aware_datetime(source.available_at)
            or start['symbol']!=source.code or start['source_revision_id']!=source.revision_id
            or _aware_datetime(start['received_at'])!=_aware_datetime(source.available_at)
            or _aware_datetime(start['occurred_at'])!=_aware_datetime(source.bar_end) or start['price']!=source.close):
        raise ValueError('native full-exit source bar binding mismatch')
    first=_aware_datetime(source.available_at).replace(second=0,microsecond=0)
    if first<_aware_datetime(source.available_at):first+=timedelta(minutes=1)
    deadline=first+timedelta(seconds=policy.exit_window_seconds)
    if _aware_datetime(end['occurred_at'])!=deadline:
        receipt['quality']['reasons'].append('native_full_exit_horizon_mismatch');return receipt
    assignment=assign_interval_to_fold(ResearchEvaluationSpec.from_dict(context['evaluation']),
        source.available_at,deadline.isoformat())
    if assignment.status!='INCLUDED':
        receipt['quality']['reasons'].append('native_full_exit_window_outside_development');return receipt
    bars=[];waiting_bars=[]
    for event in events[ids[selection['start_event_id']]+1:ids[selection['end_event_id']]+1]:
        if checkpoint is not None:checkpoint()
        if event['event_type']!='UNFILLED_BAR':continue
        raw=json.loads(event['reason'])
        if set(raw)!={'version','source_bar'} or raw['version']!=UNFILLED_EXECUTION_VERSION:
            raise ValueError('native full-exit market proof model mismatch')
        bar=KrxMinuteBarFrame(**raw['source_bar'])
        if (event['symbol']!=bar.code or event['source_revision_id']!=bar.revision_id or event['price']!=bar.close
                or _aware_datetime(event['occurred_at'])!=_aware_datetime(bar.bar_end)
                or _aware_datetime(event['received_at'])!=_aware_datetime(bar.available_at)):
            raise ValueError('native full-exit market proof binding mismatch')
        if _aware_datetime(bar.bar_start)>=first:bars.append(bar)
        elif _aware_datetime(bar.bar_start)>=_aware_datetime(source.bar_end):waiting_bars.append(bar)
    result=simulate_full_exit_stress(policy,source,tuple(bars),execution=context['unfilled_execution'],
        strategy=context['unfilled_strategy'],decision_time=decision_time,waiting_bars=tuple(waiting_bars))
    receipt['result']=result;receipt['quality']={'status':result['status'],'reasons':list(result['reasons'])}
    receipt['source_receipt']={'run_id':selection['run_id'],'dataset_id':context['dataset_id'],
        'revision_ids_hash':context['revision_ids_hash'],'development_role':development_role,
        'start_event_id':selection['start_event_id'],'end_event_id':selection['end_event_id'],
        'event_count':len(events),'bar_count':len(bars),'waiting_bar_count':len(waiting_bars),
        'source_code_hash':context['unfilled_source_code_hash']}
    if result['status']!='CALCULATED':return receipt
    available=max(context['finished_at'],*(_aware_datetime(b.available_at) for b in (source,*waiting_bars,*bars)))
    expiry=min(available+timedelta(seconds=max_evidence_age_seconds),policy.valid_until,
        _aware_datetime(context['unfilled_execution'].cost_model.valid_to))
    result['available_at']=available.isoformat();result['valid_until']=expiry.isoformat()
    if not available<=_aware_datetime(decision_time)<expiry:
        receipt['quality']={'status':'UNAVAILABLE','reasons':['native_full_exit_evidence_expired_or_future']};return receipt
    identity={'selection':selection,'policy':asdict(policy),'source':receipt['source_receipt'],'result':result}
    ref=_content_id('native-full-exit',json.loads(json.dumps(identity,
        default=lambda v:_aware_datetime(v).astimezone(timezone.utc).isoformat())))
    receipt['evidence']=FullExitStressEvidence(policy.symbol,policy.tested_position_quantity,policy.reference_price_won,
        policy.exit_window_seconds,result['conservative_exit_capacity_won'],result['stress_loss_bps'],
        position_action_cost_model_ref(context['unfilled_execution'].cost_model.to_dict()),ref,len(policy.scenarios),available,expiry)
    receipt['replay_inputs']=json.loads(json.dumps({'policy':asdict(policy),
        'execution':context['unfilled_execution'].to_dict(),'strategy_family':context['unfilled_strategy_family'],
        'strategy':context['unfilled_strategy'].to_dict(),'source_bar':asdict(source),
        'bars':[asdict(b) for b in bars],'waiting_bars':[asdict(b) for b in waiting_bars],
        'finished_at':context['finished_at'],'decision_time':decision_time,'max_evidence_age_seconds':max_evidence_age_seconds},
        default=lambda v:_aware_datetime(v).astimezone(timezone.utc).isoformat()))
    return receipt


def read_full_exit_stress_document(document, load_evidence, selection, *, development_role):
    from kiwoom_monitor.application.research_execution import FullExitStressPolicy,FullExitStressScenario
    if (not isinstance(document,dict) or set(document)!={'schema','policy','decision_time','max_evidence_age_seconds'}
            or document['schema']!='dynamic-allocation-native-exit/v1'):
        raise ValueError('unsupported native full-exit document')
    raw=dict(document['policy'])
    if not isinstance(raw.get('scenarios'),list):raise ValueError('exit scenarios require a JSON array')
    scenarios=[]
    for row in raw['scenarios']:
        row=dict(row)
        if not isinstance(row.get('blocked_minute_offsets'),list):raise ValueError('blocked minutes require a JSON array')
        row['blocked_minute_offsets']=tuple(row['blocked_minute_offsets']);scenarios.append(FullExitStressScenario(**row))
    raw['scenarios']=tuple(scenarios)
    for name in ('available_at','valid_until'):raw[name]=_aware_datetime(raw[name])
    result=read_full_exit_stress_evidence(load_evidence,selection,FullExitStressPolicy(**raw),
        decision_time=_aware_datetime(document['decision_time']),max_evidence_age_seconds=document['max_evidence_age_seconds'],
        development_role=development_role)
    if result['evidence'] is not None:
        result['evidence']=asdict(result['evidence'])
        for name in ('available_at','valid_until'):result['evidence'][name]=result['evidence'][name].isoformat()
    return result


def read_unfilled_action_outcomes(load_evidence, selections, *, decision_time, horizon_seconds,
                                 population_ref, symbol, development_role='TRAIN', checkpoint=None):
    """The same bounded native development reader, with a separate LIMIT adapter."""
    return _read_action_outcomes(load_evidence,selections,decision_time=decision_time,horizon_seconds=horizon_seconds,
        population_ref=population_ref,environment='mock',symbol=symbol,development_role=development_role,
        checkpoint=checkpoint,unfilled=True)


UNFILLED_POPULATION_PUBLICATION_VERSION = 'mock-unfilled-native-population/v1'


def prepare_unfilled_population_publication(outcomes, source_receipt, *, account_ref, credential_profile_id,
        spec_id, execution_run_id, expected_control_revision, root_intent_id, managed_intent_id):
    """Freeze the original native availability, never the HTTP send/retry time."""
    if not isinstance(outcomes,tuple) or not outcomes:
        raise ValueError('native_unfilled_population_unavailable')
    rows=[]
    for outcome in outcomes:
        if not isinstance(outcome,UnfilledActionOutcome):
            raise ValueError('native_unfilled_outcome_type_required')
        row=asdict(outcome)
        for name in ('started_at','completed_at','available_at'):
            row[name]=getattr(outcome,name).astimezone(timezone.utc).isoformat() if getattr(outcome,name) else None
        rows.append(row)
    body={'version':UNFILLED_POPULATION_PUBLICATION_VERSION,'account_ref':account_ref,
        'credential_profile_id':credential_profile_id,'spec_id':spec_id,'execution_run_id':execution_run_id,
        'expected_control_revision':expected_control_revision,'root_intent_id':root_intent_id,
        'managed_intent_id':managed_intent_id,'available_at':max(o.available_at for o in outcomes).astimezone(timezone.utc).isoformat(),
        'outcomes':rows,'source_receipt':source_receipt}
    # Stable input order for retry identity; the source row bindings remain intact.
    body['outcomes'].sort(key=lambda r:(r['case_ref'],r['action'],r['input_ref']))
    body=json.loads(json.dumps(body,ensure_ascii=False,sort_keys=True,allow_nan=False))
    body['publication_ref']=_content_id('unfilled-native-publication',body)
    unfilled_population_publication_from_dict(body)
    return body


def unfilled_population_publication_from_dict(document):
    """Validate a PC-reported native packet; it never grants durable/market authority."""
    import re
    from uuid import UUID
    keys={'version','account_ref','credential_profile_id','spec_id','execution_run_id','expected_control_revision',
        'root_intent_id','managed_intent_id','available_at','outcomes','source_receipt','publication_ref'}
    if not isinstance(document,dict) or set(document)!=keys:
        raise ValueError('invalid_native_unfilled_publication_fields')
    encoded=json.dumps(document,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()
    if len(encoded)>1024*1024:raise ValueError('native_unfilled_publication_budget_exceeded')
    if (document['version']!=UNFILLED_POPULATION_PUBLICATION_VERSION
            or type(document['expected_control_revision']) is not int or document['expected_control_revision']<1
            or any(type(document[n]) is not str or not document[n].strip() or len(document[n])>256
                   for n in ('account_ref','credential_profile_id','spec_id','execution_run_id','root_intent_id','managed_intent_id'))
            or re.fullmatch(r'[A-Za-z0-9_-]{1,96}',document['credential_profile_id']) is None
            or str(UUID(document['account_ref']))!=document['account_ref']
            or document['publication_ref']!=_content_id('unfilled-native-publication',
                    {k:v for k,v in document.items() if k!='publication_ref'})):
        raise ValueError('native_unfilled_publication_scope_or_identity_mismatch')
    raw=document['outcomes']; receipt=document['source_receipt']
    if not isinstance(raw,list) or not 4<=len(raw)<=200:
        raise ValueError('native_unfilled_publication_requires_complete_paired_selection')
    rows=[]
    for row in raw:
        value=dict(row)
        for name in ('started_at','completed_at','available_at'):
            value[name]=_aware_datetime(value[name]) if value[name] is not None else None
        rows.append(UnfilledActionOutcome(**value))
    outcomes=tuple(rows); published=_aware_datetime(document['available_at'])
    if (published!=max(o.available_at for o in outcomes)
            or len({o.input_ref for o in outcomes})!=len(outcomes)
            or any(o.order_state!='ACCEPTED' or o.input_resolution!='minute' or o.status!='COMPLETE'
                or o.data_gap_count or o.completed_at is None or o.gross_change_won is None or o.fee_tax_won is None
                or o.available_at<o.completed_at or (o.completed_at-o.started_at).total_seconds()!=o.horizon_seconds
                or re.fullmatch(r'native-unfilled-outcome_[0-9a-f]{64}',o.input_ref) is None
                or re.fullmatch(r'native-unfilled-state_[0-9a-f]{64}',o.initial_state_ref) is None for o in outcomes)):
        raise ValueError('native_unfilled_publication_state_time_or_source_unavailable')
    fields=('symbol','side','purpose','pending_quantity','existing_limit_price_won','candidate_limit_price_won',
        'initial_quantity','pending_buy_quantity','pending_sell_quantity','target_quantity','horizon_seconds','population_ref','cost_model_ref')
    if len({tuple(getattr(o,n) for n in fields) for o in outcomes})!=1 or outcomes[0].cost_model_ref is None:
        raise ValueError('native_unfilled_publication_mixed_cohort')
    pairs={}
    for row in outcomes:
        case=pairs.setdefault(row.case_ref,{})
        if row.action in case:raise ValueError('duplicate_native_unfilled_action_case')
        case[row.action]=row
    for pair in pairs.values():
        if (set(pair)!={'WAIT','REPRICE'} or (pair['WAIT'].initial_state_ref,pair['WAIT'].started_at,pair['WAIT'].completed_at)!=
                (pair['REPRICE'].initial_state_ref,pair['REPRICE'].started_at,pair['REPRICE'].completed_at)):
            raise ValueError('native_unfilled_publication_incomplete_pair')
    expected_keys={'version','requested_count','accepted_count','sources','excluded','execution_environment',
        'target_environment','target_symbol','development_role','source_durability','order_authorized'}
    if (not isinstance(receipt,dict) or set(receipt)!=expected_keys
            or receipt['version']!='native-unfilled-outcome-source/v1'
            or type(receipt['accepted_count']) is not int or type(receipt['requested_count']) is not int
            or receipt['accepted_count']!=len(outcomes) or receipt['requested_count']!=len(outcomes) or receipt['excluded']!=[]
            or receipt['execution_environment']!='simulation' or receipt['target_environment']!='mock'
            or receipt['target_symbol']!=outcomes[0].symbol or receipt['development_role'] not in {'TRAIN','VALIDATION'}
            or receipt['source_durability']!='not_verified' or receipt['order_authorized'] is not False
            or not isinstance(receipt['sources'],list) or not 2<=len(receipt['sources'])<=len(outcomes)):
        raise ValueError('native_unfilled_publication_receipt_unavailable')
    source_keys={'run_id','dataset_id','revision_ids_hash','event_count','policy_version','execution_model_ref'}
    for source in receipt['sources']:
        if (not isinstance(source,dict) or set(source)!=source_keys
                or type(source['event_count']) is not int or not 1<=source['event_count']<=10000
                or any(type(source[n]) is not str or not source[n].strip() or len(source[n])>256 for n in source_keys-{'event_count'})):
            raise ValueError('invalid_native_unfilled_source_receipt')
    if (len({s['run_id'] for s in receipt['sources']})!=len(receipt['sources'])
            or sum(s['event_count'] for s in receipt['sources'])>10000
            or len({s['policy_version'] for s in receipt['sources']})!=1
            or len({s['execution_model_ref'] for s in receipt['sources']})!=1):
        raise ValueError('native_unfilled_source_budget_or_duplicate')
    return outcomes,published


def _read_action_outcomes(load_evidence, selections, *, decision_time, horizon_seconds,
                         population_ref, environment, symbol, development_role='TRAIN', checkpoint=None, unfilled=False):
    """Explicit native windows, shared per-run reads, bounded evidence and exclusions."""
    if not isinstance(selections, list) or not 1 <= len(selections) <= 200:
        raise ValueError('position source selection requires 1 to 200 windows')
    if development_role not in {'TRAIN', 'VALIDATION'}:
        raise ValueError('native position source must select one development role')
    if (environment != 'mock' or type(horizon_seconds) is not int or not 60 <= horizon_seconds <= 599_940
            or horizon_seconds % 60 or type(symbol) is not str or len(symbol) != 6
            or not symbol.isascii() or not symbol.isdigit() or type(population_ref) is not str
            or not population_ref.strip() or len(population_ref) > 256):
        raise ValueError('explicit bounded mock/KRX minute source scope required')
    _aware_datetime(decision_time)
    keys = {'run_id', 'start_event_id', 'end_event_id', 'action', 'case_ref'}
    physical, cases, groups = set(), set(), {}
    for row in selections:
        if (not isinstance(row, dict) or set(row) != keys
                or any(type(value) is not str or not value.strip() or len(value) > 256 for value in row.values())
                or row['action'] not in ({'WAIT','REPRICE'} if unfilled else {'HOLD', 'EXIT', 'ADD', 'REDUCE', 'REENTER'})):
            raise ValueError('invalid explicit native position window')
        identity = (row['run_id'], row['start_event_id'], row['end_event_id'])
        case = (row['case_ref'], row['action'])
        if identity in physical or case in cases:
            raise ValueError('duplicate native position window or action case')
        physical.add(identity); cases.add(case)
        groups.setdefault(row['run_id'], []).append(row)
    outcomes, excluded, sources, fingerprints = [], [], [], set()
    remaining = 10_000
    for run_id, rows in sorted(groups.items()):
        if checkpoint is not None: checkpoint()
        try:
            if remaining <= 0: raise ValueError('total native execution source budget exceeded')
            context, events = load_evidence(run_id, tuple(sorted({r['end_event_id'] for r in rows})),
                decision_time=decision_time, limit=remaining, development_role=development_role,
                **({'checkpoint': checkpoint} if checkpoint is not None else {}))
            remaining -= len(events)
        except PositionOutcomeUnavailable as error:
            excluded.extend({**row, 'reason': str(error)} for row in rows)
            continue
        sources.append({'run_id': run_id, 'dataset_id': context['dataset_id'],
                        'revision_ids_hash': context['revision_ids_hash'], 'event_count': len(events),
                        'policy_version': context['policy_version'], 'execution_model_ref': context['execution_model_ref']})
        if context.get('position_target_policy') is not None:
            sources[-1]['position_action_execution']={'version':'paper-position-target/v1',
                'source_policy_version':context['position_source_policy'],
                'target_policy':json.loads(json.dumps(asdict(context['position_target_policy']),default=lambda v:v.isoformat()))}
        for row in sorted(rows, key=lambda value: (value['case_ref'], value['action'])):
            if checkpoint is not None: checkpoint()
            try:
                adapter=unfilled_action_outcome_from_events if unfilled else position_action_outcome_from_events
                outcome = adapter(context, events,
                    **{key: row[key] for key in ('start_event_id', 'end_event_id', 'action', 'case_ref')},
                    population_ref=population_ref, horizon_seconds=horizon_seconds, symbol=symbol,
                    **({} if unfilled else {'environment':environment}))
                fingerprint = (context['revision_ids_hash'], outcome.policy_version, outcome.action,
                               outcome.initial_state_ref, outcome.started_at, outcome.completed_at)
                if fingerprint in fingerprints:
                    raise PositionOutcomeUnavailable('duplicate_effective_native_evidence')
                fingerprints.add(fingerprint)
                outcomes.append(outcome)
            except PositionOutcomeUnavailable as error:
                excluded.append({**row, 'reason': str(error)})
    return tuple(outcomes), {'version': 'native-unfilled-outcome-source/v1' if unfilled else 'native-position-outcome-source/v1', 'requested_count': len(selections),
        'accepted_count': len(outcomes), 'sources': sources, 'excluded': excluded,
        'execution_environment': 'simulation', 'target_environment': environment, 'target_symbol': symbol,
        'development_role': development_role,
        'source_durability': 'not_verified', 'order_authorized': False}


def compare_position_action_outcomes(
    outcomes: tuple[PositionActionOutcome, ...], expected: Mapping[str, Mapping[str, Any]], *,
    baseline_action: str, decision_time: datetime, horizon_seconds: int,
    population_ref: str, input_resolution: str, minimum_samples: int,
) -> tuple[dict[str, Any], ...]:
    """Empirical paired comparison; never synthesizes missing counterfactuals.

    Expected bindings are produced by the target calculator, including its blocked
    or partially funded state. Means describe the supplied cohort, not a verified
    future probability or execution permission. Variability is not a confidence interval.
    """
    if type(minimum_samples) is not int or not 2 <= minimum_samples <= 10_000 or baseline_action not in expected:
        raise ValueError("action outcome comparison requires a baseline and at least two samples")
    if type(horizon_seconds) is not int or horizon_seconds <= 0:
        raise ValueError("action outcome horizon must be a positive integer")
    if len(outcomes) > 10_000:
        raise ValueError("position action comparison accepts at most 10000 outcomes")
    cutoff = _aware_datetime(decision_time).astimezone(timezone.utc)
    seen_refs: set[str] = set()
    seen_cases: set[tuple[str, str]] = set()
    accepted = {action: {} for action in expected}
    excluded = {action: {} for action in expected}
    for sample in outcomes:
        key = (sample.action, sample.case_ref)
        if sample.input_ref in seen_refs or key in seen_cases:
            raise ValueError("duplicate action outcome input/case identity")
        seen_refs.add(sample.input_ref)
        seen_cases.add(key)
        if sample.action not in expected:
            raise ValueError("action outcome has no requested target")
        binding = expected[sample.action]
        reasons = []
        if sample.status != "COMPLETE":
            reasons.append("outcome_not_complete")
        if sample.data_gap_count:
            reasons.append("outcome_data_gap")
        if sample.gross_change_won is None or sample.fee_tax_won is None:
            reasons.append("outcome_value_or_cost_unavailable")
        if sample.available_at > cutoff or sample.started_at > cutoff or (
            sample.completed_at is not None and sample.completed_at > cutoff
        ):
            reasons.append("outcome_not_available_at_cut")
        if sample.completed_at is None or (sample.completed_at - sample.started_at).total_seconds() != horizon_seconds or (
            sample.completed_at is not None and sample.available_at < sample.completed_at
        ):
            reasons.append("outcome_time_interval_mismatch")
        if sample.horizon_seconds != horizon_seconds or sample.population_ref != population_ref or sample.input_resolution != input_resolution:
            reasons.append("outcome_cohort_mismatch")
        for name in ("policy_version", "environment", "venue", "source_kind", "execution_model_ref", "initial_quantity", "pending_buy_quantity",
                     "pending_sell_quantity", "target_quantity", "cost_model_ref", *binding.get('comparison_fields',())):
            if getattr(sample, name) != binding[name]:
                reasons.append("outcome_target_or_model_mismatch")
                break
        if sample.cost_model_ref is None:
            reasons.append("outcome_cost_model_unavailable")
        if binding.get("cost_valid_from") and sample.completed_at is not None and not (
            _aware_datetime(binding["cost_valid_from"]) <= sample.started_at
            and sample.completed_at <= _aware_datetime(binding["cost_valid_to"])
        ):
            reasons.append("outcome_cost_model_outside_validity")
        if reasons:
            for reason in sorted(set(reasons)):
                excluded[sample.action][reason] = excluded[sample.action].get(reason, 0) + 1
        else:
            accepted[sample.action][sample.case_ref] = sample

    baseline = accepted[baseline_action]
    results = []
    for action in sorted(expected):
        binding = expected[action]
        samples = accepted[action]
        pairs = []
        mismatched = 0
        for case in sorted(samples.keys() & baseline.keys()):
            candidate, reference = samples[case], baseline[case]
            if (candidate.initial_state_ref, candidate.started_at, candidate.completed_at) != (
                reference.initial_state_ref, reference.started_at, reference.completed_at
            ):
                mismatched += 1
            else:
                pairs.append((candidate, reference))
        reasons = list(binding["reasons"])
        if action != baseline_action:
            reasons.extend(expected[baseline_action]["reasons"])
        if len(pairs) < minimum_samples:
            reasons.append("minimum_paired_samples_not_met")
        if mismatched:
            # Mismatched cases are excluded, not replaced by zero outcomes.
            excluded[action]["paired_initial_state_or_time_mismatch"] = mismatched
        estimate = baseline_estimate = difference = None
        if not reasons:
            estimate = _position_action_estimate(tuple((a.gross_change_won, a.fee_tax_won) for a, _ in pairs))
            baseline_estimate = _position_action_estimate(tuple((b.gross_change_won, b.fee_tax_won) for _, b in pairs))
            difference = _position_action_estimate(tuple((a.gross_change_won - b.gross_change_won,
                                                        a.fee_tax_won - b.fee_tax_won) for a, b in pairs))
        refs = []
        for a, b in pairs:
            for sample in (a, b):
                document = asdict(sample)
                for name in ("started_at", "completed_at", "available_at"):
                    document[name] = getattr(sample, name).astimezone(timezone.utc).isoformat()
                refs.append((sample.input_ref, _content_id("position-action-outcome", document)))
        results.append({
            "action": action, "baseline_action": baseline_action,
            "ev_status": "EV_UNAVAILABLE" if reasons else "EMPIRICAL_ESTIMATE",
            "reasons": tuple(sorted(set(reasons))), "sample_count": len(samples),
            "paired_sample_count": len(pairs),
            "unpaired_sample_count": len(samples) - len(pairs),
            "baseline_unpaired_sample_count": len(baseline) - len(pairs),
            "excluded_counts": dict(sorted(excluded[action].items())),
            "estimate": estimate, "baseline_estimate": baseline_estimate, "paired_delta": difference,
            "evidence_refs": tuple(sorted(set(refs))),
        })
    return tuple(results)


def _position_action_estimate(values: tuple[tuple[int, int], ...], *, bps_notional_won=None) -> dict[str, Any]:
    count = len(values)
    gross, costs = tuple(a for a, _ in values), tuple(b for _, b in values)
    net = tuple(a - b for a, b in values)
    average = Fraction(sum(net), count)
    variance = sum((Fraction(value) - average)**2 for value in net) / (count - 1)
    deviation = sqrt(float(variance))
    result={"sample_count": count, "mean_gross_change_won": float(Fraction(sum(gross), count)),
            "mean_fee_tax_won": float(Fraction(sum(costs), count)), "mean_net_change_won": float(average),
            "net_stddev_won": deviation, "net_standard_error_won": deviation / sqrt(count),
            "minimum_net_change_won": min(net), "maximum_net_change_won": max(net),
            "positive_sample_count": sum(value > 0 for value in net)}
    if bps_notional_won is not None:
        if type(bps_notional_won) is not int or bps_notional_won<1:
            raise ValueError('positive explicit EV notional basis required')
        scale=Fraction(10000,bps_notional_won)
        squared=variance/count*scale**2
        root=isqrt(squared.numerator//squared.denominator)
        result['mean_net_change_bps_floor']=int(average*scale//1)
        result['net_standard_error_bps_ceil']=root+int(root**2*squared.denominator<squared.numerator)
    return result


@dataclass(frozen=True)
class DevelopmentPartitionResult:
    run_id: str
    status: str
    reasons: tuple[str, ...] = ()
    condition_key: str = ''
    input_dataset_id: str = ''
    input_revision_ids_hash: str = ''
    role: str = ''
    start: str = ''
    end: str = ''
    closed_trade_count: int = 0
    active_day_count: int = 0
    net_pnl_won: int | None = None
    max_drawdown_won: int | None = None
    # dimension, bucket key, closed trades, net realized PnL; no mutable raw report.
    strata: tuple[tuple[str, str, int, int], ...] = ()
    excluded_reason: str = ''


@dataclass(frozen=True)
class IndependentDevelopmentComparison:
    version: str
    status: str
    requested_count: int
    unique_run_count: int
    condition_keys: tuple[str, ...]
    partitions: tuple[DevelopmentPartitionResult, ...]
    eligible_partition_count: int
    positive_partition_count: int
    minimum_partition_pnl_won: int | None
    maximum_partition_pnl_won: int | None
    median_partition_pnl_won: float | None
    worst_partition_drawdown_won: int | None
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _development_partition_result(record: Mapping[str, Any]) -> DevelopmentPartitionResult:
    run_id = str(record.get('run_id', ''))
    state = record.get('status')
    if state != 'completed':
        status = 'MISSING' if state == 'missing' else 'FAILED' if state == 'failed' else 'INCOMPLETE'
        result = DevelopmentPartitionResult(run_id, status, (str(record.get('error') or state),))
        # Failed runs can still identify their intended window; absent metadata is not invented.
        try:
            manifest = record['input_manifest']
            descriptor = manifest['development_partition']
            policy = DevelopmentPartitionSpec.from_dict(descriptor['spec'])
            evaluation = ResearchEvaluationSpec.from_dict(descriptor['evaluation'])
            fold = policy.evaluation_for(evaluation).folds[0]
            if manifest.get('runtime_input_version') == 'independent_development_input/v1':
                result = replace(result, role=fold.role,
                    start=_aware_datetime(fold.start).astimezone(timezone.utc).isoformat(),
                    end=_aware_datetime(fold.end).astimezone(timezone.utc).isoformat(),
                    input_dataset_id=str(manifest['dataset_id']),
                    input_revision_ids_hash=str(manifest['revision_ids_hash']))
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
        return result
    try:
        spec, manifest, report = record['spec'], record['input_manifest'], record['report']
        descriptor = manifest['development_partition']
        if manifest.get('runtime_input_version') != 'independent_development_input/v1':
            raise ValueError('independent development input required')
        partition = DevelopmentPartitionSpec.from_dict(descriptor['spec'])
        evaluation = ResearchEvaluationSpec.from_dict(spec['evaluation_spec'])
        selected = partition.evaluation_for(evaluation)
        if len(evaluation.folds) != 1 or selected.to_dict() != evaluation.to_dict():
            raise ValueError('one development fold required')
        fold = selected.folds[0]
        start, end = (_aware_datetime(value).astimezone(timezone.utc).isoformat()
                      for value in (fold.start, fold.end))
        if (descriptor['evaluation'] != evaluation.to_dict()
                or _aware_datetime(descriptor['warmup_start']).astimezone(timezone.utc) != warmup_start(evaluation)
                or _aware_datetime(descriptor['active_start']).astimezone(timezone.utc).isoformat() != start
                or _aware_datetime(descriptor['end']).astimezone(timezone.utc).isoformat() != end):
            raise ValueError('partition boundary mismatch')
        if (not spec.get('code_hash') or not spec.get('session_profile')
                or spec['session_profile'] != manifest.get('research_session_profile')
                or not spec.get('execution_model') or not record.get('logical_result_hash')
                or not str(manifest['dataset_id']).startswith('development-')
                or not manifest['revision_ids_hash']):
            raise ValueError('verified run identity required')
        rows = report['fold_reports']
        if (report['run_id'] != run_id or report['split_spec'] != evaluation.to_dict()
                or len(rows) != 1 or (rows[0]['name'], rows[0]['role']) != (fold.name, fold.role)):
            raise ValueError('report partition mismatch')
        row = rows[0]
        if row['status'] not in ('ELIGIBLE', 'INELIGIBLE', 'NOT_APPLICABLE'):
            raise ValueError('development report status invalid')
        # Preserve every run policy, excluding only per-input identity and active fold bounds.
        conditions = {key: value for key, value in spec.items()
                      if key not in ('data_manifest_hash', 'evaluation_spec')}
        conditions['evaluation_policy'] = {key: value for key, value in evaluation.to_dict().items()
                                           if key != 'folds'}
        conditions['partition_policy'] = {'version': partition.version, 'state_policy': partition.state_policy}
        if partition.symbol_partition is not None:
            conditions['partition_policy']['symbol_partition'] = partition.symbol_partition.to_dict()
        conditions['input_contract'] = {key: manifest.get(key) for key in (
            'schema_version', 'research_session_profile', 'subject', 'universe_rule', 'order_policy_version')}
        key = hashlib.sha256(json.dumps(conditions, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        reasons = tuple(str(reason) for reason in row.get('reasons', ()))
        sample_reasons = {'minimum_closed_trades_not_met', 'minimum_active_days_not_met'}
        status = ('INSUFFICIENT_SAMPLE' if row['status'] == 'INELIGIBLE' and reasons
                  and set(reasons) <= sample_reasons else
                  'NO_CLOSED_TRADE' if row['status'] == 'NOT_APPLICABLE' else row['status'])
        closed, days = int(row['closed_trade_count']), int(row['active_day_count'])
        net = row.get('net_realized_pnl_won')
        if min(closed, days) < 0 or (status == 'ELIGIBLE' and (closed == 0 or net is None)):
            raise ValueError('development metrics invalid')
        if status == 'ELIGIBLE' and (closed < evaluation.minimum_closed_trades or days < evaluation.minimum_active_days):
            raise ValueError('eligible report does not meet locked sample policy')
        strata = tuple((dimension, str(bucket['key']), int(bucket['closed_trade_count']),
                        int(bucket['net_realized_pnl_won']))
                       for dimension in ('by_symbol', 'by_date', 'by_time_bucket')
                       for bucket in row.get(dimension, ()))
        if partition.symbol_partition is not None:
            symbols = tuple(value for value in strata if value[0] == 'by_symbol')
            if (sum(value[2] for value in symbols) != closed
                    or any(value[2] < 0 or value[2] > 0 and not partition.symbol_partition.allows(value[1]) for value in symbols)):
                raise ValueError('closed trades do not match the declared stock partition')
        return DevelopmentPartitionResult(
            run_id, status, reasons, key, str(manifest['dataset_id']), str(manifest['revision_ids_hash']),
            fold.role, start, end, closed, days, int(net) if net is not None else None,
            int(row['max_drawdown_won']) if row.get('max_drawdown_won') is not None else None, strata)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return DevelopmentPartitionResult(run_id, 'INVALID', (str(exc),))


def build_independent_development_comparison(
    records: Iterable[Mapping[str, Any]],
) -> IndependentDevelopmentComparison:
    """Read-only development projection, never a combined account equity curve.

    Explicit run references define the requested scope; this is not a search-attempt census.
    Overlapping windows and revised inputs for the same window remain visible but cannot
    supply multiple independent observations. Missing/failed results are never silently dropped.
    """
    records = tuple(records)
    if not records or len(records) > 200:
        raise ValueError('comparison requires 1 to 200 explicit run references')
    results = [_development_partition_result(record) for record in records]
    seen: set[str] = set()
    first_reference: dict[str, int] = {}
    for index, result in enumerate(results):
        if result.run_id in seen:
            first = first_reference[result.run_id]
            if records[first] != records[index]:
                results[first] = replace(results[first], excluded_reason='conflicting_run_reference')
                results[index] = replace(result, excluded_reason='conflicting_run_reference')
            else:
                results[index] = replace(result, excluded_reason='duplicate_run_reference')
        else:
            first_reference[result.run_id] = index
        seen.add(result.run_id)
    keys = tuple(sorted({row.condition_key for row in results if row.condition_key}))
    unique = [(index, row) for index, row in enumerate(results)
              if row.condition_key and not row.excluded_reason]
    evidence_seen: dict[tuple[str, ...], int] = {}
    for index, row in unique:
        identity = (row.condition_key, row.start, row.end, row.role,
                    row.input_dataset_id, row.input_revision_ids_hash,
                    str(records[index].get('logical_result_hash', '')))
        if identity in evidence_seen:
            results[index] = replace(row, excluded_reason='duplicate_evidence')
        else:
            evidence_seen[identity] = index
    unique = [(index, results[index]) for index, _ in unique if not results[index].excluded_reason]
    for offset, (index, row) in enumerate(unique):
        for other_index, other in unique[offset + 1:]:
            if row.condition_key != other.condition_key:
                continue
            if max(row.start, other.start) < min(row.end, other.end):
                reason = ('same_window_input_revision' if (row.start, row.end) == (other.start, other.end)
                          else 'overlapping_active_windows')
                results[index] = replace(results[index], excluded_reason=reason)
                results[other_index] = replace(results[other_index], excluded_reason=reason)
    eligible = tuple(row for row in results if row.status == 'ELIGIBLE' and not row.excluded_reason) if len(keys) == 1 else ()
    pnl = tuple(row.net_pnl_won for row in eligible if row.net_pnl_won is not None)
    mdd = tuple(row.max_drawdown_won for row in eligible if row.max_drawdown_won is not None)
    considered = tuple(row for row in results if row.excluded_reason not in ('duplicate_run_reference', 'duplicate_evidence'))
    status = ('INCOMPARABLE' if len(keys) > 1 else
              'COMPLETE' if considered and all(row.status == 'ELIGIBLE' and not row.excluded_reason for row in considered)
              else 'INCOMPLETE')
    return IndependentDevelopmentComparison(
        'independent_development_comparison/v1', status, len(records), len(seen), keys, tuple(results),
        len(eligible), sum(value > 0 for value in pnl), min(pnl) if pnl else None,
        max(pnl) if pnl else None, float(median(pnl)) if pnl else None, max(mdd) if mdd else None,
        ('independent_initial_cash_not_continuous_portfolio', 'no_combined_return_or_drawdown',
         'active_days_are_per_partition_not_unique_days', 'shared_warmup_is_not_independent_data',
         'strata_describe_closed_trades_not_all_market_coverage', 'explicit_run_scope_not_search_attempt_census',
         'no_final_access_or_automatic_promotion'))


@dataclass(frozen=True)
class DevelopmentEvidence:
    """Selection input copied from development folds, with no raw/final report."""

    status: str
    reasons: tuple[str, ...]
    fold_refs: tuple[tuple[str, str], ...]
    net_pnl_won: int | None
    max_drawdown_won: int | None
    closed_trade_count: int
    active_day_count: int


def build_development_evidence(report: Mapping[str, Any]) -> DevelopmentEvidence:
    """Global report status/reasons and OOS fields are never selection inputs."""
    folds = tuple(
        row for row in report.get('fold_reports', ())
        if isinstance(row, Mapping) and row.get('role') in ('TRAIN', 'VALIDATION')
    )
    reasons = list(dict.fromkeys(str(reason) for row in folds for reason in row.get('reasons', ())))
    statuses = tuple(str(row.get('status', '')) for row in folds)
    if not folds:
        status = 'NOT_APPLICABLE'
        reasons.append('search_development_folds_missing')
    elif any(value not in ('ELIGIBLE', 'INELIGIBLE', 'NOT_APPLICABLE') for value in statuses):
        status = 'INELIGIBLE'
        reasons.append('search_development_fold_status_invalid')
    elif 'INELIGIBLE' in statuses:
        status = 'INELIGIBLE'
    elif 'ELIGIBLE' in statuses:
        status = 'ELIGIBLE'
    else:
        status = 'NOT_APPLICABLE'
    # These preserve the legacy fold-summary arithmetic, not independent portfolio statistics.
    visible = tuple(row for row in folds if row.get('status') != 'SEALED')
    net = tuple(int(row['net_realized_pnl_won']) for row in visible if row.get('net_realized_pnl_won') is not None)
    drawdowns = tuple(int(row['max_drawdown_won']) for row in visible if row.get('max_drawdown_won') is not None)
    return DevelopmentEvidence(
        status=status, reasons=tuple(dict.fromkeys(reasons)),
        fold_refs=tuple((str(row.get('name', '')), str(row['role'])) for row in folds),
        net_pnl_won=sum(net) if net else None,
        max_drawdown_won=max(drawdowns) if drawdowns else None,
        closed_trade_count=sum(int(row.get('closed_trade_count', 0)) for row in visible),
        active_day_count=sum(int(row.get('active_day_count', 0)) for row in visible),
    )


@dataclass(frozen=True)
class OutcomeLabel:
    label_id: str
    run_id: str
    candidate_event_id: str
    symbol: str
    horizon_seconds: int
    horizon_end: str
    matured_at: str
    computed_at: str
    available_at: str
    status: str
    values: Mapping[str, Any] | None
    input_refs: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PerformanceSummary:
    evaluation_id: str
    run_id: str
    status: str
    reason: str
    initial_cash_won: int
    closed_trade_count: int
    winning_trade_count: int | None
    gross_realized_pnl_won: int | None
    total_cost_won: int | None
    net_realized_pnl_won: int | None
    return_ppm: int | None
    optimistic_additional_pnl_won: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchFoldReport:
    name: str
    role: str
    status: str
    reasons: tuple[str, ...]
    closed_trade_count: int
    active_day_count: int
    winning_trade_count: int | None
    losing_trade_count: int | None
    gross_realized_pnl_won: int | None
    total_cost_won: int | None
    net_realized_pnl_won: int | None
    expectancy_won: int | None
    payoff_ratio_ppm: int | None
    profit_factor_ppm: int | None
    turnover_won: int
    exposure_seconds: int
    max_drawdown_won: int | None
    max_drawdown_ppm: int | None
    submitted_order_count: int
    filled_order_count: int
    fill_rate_ppm: int | None
    rejected_order_count: int
    censored_order_count: int
    unsupported_order_count: int
    partial_fill_count: int
    purged_trade_count: int
    purged_outcome_count: int
    no_trade_reasons: Mapping[str, int]
    event_outcomes: tuple[Mapping[str, Any], ...]
    by_symbol: tuple[Mapping[str, Any], ...]
    by_date: tuple[Mapping[str, Any], ...]
    by_time_bucket: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchReport:
    report_id: str
    run_id: str
    status: str
    reasons: tuple[str, ...]
    split_spec: Mapping[str, Any]
    data_quality: Mapping[str, Any]
    cost_model: Mapping[str, Any] | None
    limitations: tuple[str, ...]
    fold_reports: tuple[ResearchFoldReport, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchFoldComparison:
    name: str
    role: str
    status: str
    reasons: tuple[str, ...]
    baseline_closed_trade_count: int | None
    variant_closed_trade_count: int | None
    baseline_net_pnl_won: int | None
    variant_net_pnl_won: int | None
    net_pnl_delta_won: int | None
    shared_candidate_count: int | None
    baseline_only_candidate_count: int | None
    variant_only_candidate_count: int | None
    avoided_loss_won: int | None
    missed_profit_won: int | None
    variant_only_net_pnl_won: int | None
    event_outcome_deltas: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchComparison:
    comparison_id: str
    baseline_run_id: str
    variant_run_id: str
    changed_condition: str
    status: str
    reasons: tuple[str, ...]
    input_dataset_id: str
    input_revision_ids_hash: str
    fold_comparisons: tuple[ResearchFoldComparison, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_outcome_labels(
    candidate_events: Iterable[CandidateEvent],
    bars: Iterable[KrxMinuteBarFrame],
    *,
    run_ended: bool,
    session_profile: str | None = None,
) -> tuple[OutcomeLabel, ...]:
    ordered_bars = tuple(sorted(bars, key=lambda bar: (
        _aware_datetime(bar.bar_start).astimezone(timezone.utc),
        bar.code,
        bar.revision_id,
    )))
    labels: list[OutcomeLabel] = []
    for candidate in candidate_events:
        event_time = _aware_datetime(candidate.available_at).astimezone(timezone.utc)
        reference_bar = next(
            (bar for bar in ordered_bars if bar.revision_id == candidate.reference_revision_id),
            None,
        )
        event_session = reference_bar.research_session if reference_bar is not None else ""
        for horizon_seconds in OUTCOME_HORIZONS_SECONDS:
            horizon_end = event_time + timedelta(seconds=horizon_seconds)
            if horizon_seconds == 1:
                labels.append(_label(
                    candidate, horizon_seconds, horizon_end,
                    matured_at="", computed_at=candidate.available_at,
                    available_at=candidate.available_at, status="UNSUPPORTED", values=None,
                    input_refs=(), reason="second_level_input_not_exported",
                ))
                continue
            future = [
                bar for bar in ordered_bars
                if bar.code == candidate.symbol
                and _aware_datetime(bar.bar_start).astimezone(timezone.utc) >= event_time
                and (
                    session_profile is None
                    or not event_session
                    or bar.research_session == event_session
                )
            ]
            target = next((
                bar for bar in future
                if _aware_datetime(bar.bar_end).astimezone(timezone.utc) >= horizon_end
            ), None)
            if target is None:
                status = "CENSORED" if run_ended else "PENDING"
                labels.append(_label(
                    candidate, horizon_seconds, horizon_end,
                    matured_at="", computed_at=candidate.available_at,
                    available_at=candidate.available_at, status=status, values=None,
                    input_refs=tuple(bar.revision_id for bar in future),
                    reason=(
                        "research_interval_ended_before_horizon"
                        if run_ended else "horizon_not_matured"
                    ),
                ))
                continue
            through_target = [
                bar for bar in future
                if _aware_datetime(bar.bar_start) <= _aware_datetime(target.bar_start)
            ]
            continuous = (
                _continuous_minutes(through_target)
                if session_profile is None
                else _continuous_minutes_from(event_time, through_target)
            )
            if not continuous:
                labels.append(_label(
                    candidate, horizon_seconds, horizon_end,
                    matured_at=target.bar_end, computed_at=target.available_at,
                    available_at=target.available_at, status="CENSORED", values=None,
                    input_refs=tuple(bar.revision_id for bar in through_target),
                    reason="minute_bar_coverage_gap",
                ))
                continue
            if session_profile is not None and any(
                bar.market_phase != "CONTINUOUS" for bar in through_target
            ):
                labels.append(_label(
                    candidate, horizon_seconds, horizon_end,
                    matured_at=target.bar_end, computed_at=target.available_at,
                    available_at=target.available_at, status="UNSUPPORTED", values=None,
                    input_refs=tuple(bar.revision_id for bar in through_target),
                    reason="minute_bar_phase_cannot_prove_intrabar_path",
                ))
                continue
            reference = candidate.signal_reference_price
            values = {
                "reference_price": reference,
                "close": target.close,
                "return_bps": (target.close - reference) * 10_000 // reference,
                "max_up_bps": (max(bar.high for bar in through_target) - reference) * 10_000 // reference,
                "max_down_bps": (min(bar.low for bar in through_target) - reference) * 10_000 // reference,
            }
            labels.append(_label(
                candidate, horizon_seconds, horizon_end,
                matured_at=target.bar_end, computed_at=target.available_at,
                available_at=target.available_at, status="COMPLETE", values=values,
                input_refs=tuple(bar.revision_id for bar in through_target), reason="",
            ))
    return tuple(labels)


def evaluate_performance(
    run_id: str,
    execution_events: Iterable[ExecutionEvent],
    *,
    initial_cash_won: int,
    cost_model_present: bool,
) -> PerformanceSummary:
    events = tuple(execution_events)
    if not cost_model_present:
        return _summary(
            run_id, "INELIGIBLE", "cost_model_missing", initial_cash_won,
            0, None, None, None, None, None, None,
        )
    fills = tuple(event for event in events if event.event_type in {"FILL", "RISK_FILL"})
    sells = tuple(event for event in fills if event.side == "SELL")
    if not sells:
        return _summary(
            run_id, "NOT_APPLICABLE", "no_closed_simulated_trade", initial_cash_won,
            0, None, None, None, None, None, None,
        )
    net = sum(event.realized_pnl_won for event in sells)
    total_cost = sum(event.total_cost_won for event in fills)
    gross = net + total_cost
    optimistic_additional = sum(
        max(0, int(event.optimistic_exit_price or 0) - event.price) * event.quantity
        for event in sells
    )
    return _summary(
        run_id, "ELIGIBLE", "", initial_cash_won,
        len(sells), sum(event.realized_pnl_won > 0 for event in sells),
        gross, total_cost, net, net * 1_000_000 // initial_cash_won,
        optimistic_additional,
    )


def build_research_report(
    run_id: str,
    *,
    input_manifest: Mapping[str, Any],
    observations: Iterable[Mapping[str, Any]],
    candidate_events: Iterable[CandidateEvent | Mapping[str, Any]],
    decisions: Iterable[Mapping[str, Any]],
    execution_events: Iterable[ExecutionEvent | Mapping[str, Any]],
    outcome_labels: Iterable[OutcomeLabel | Mapping[str, Any]],
    initial_cash_won: int,
    cost_model: Mapping[str, Any] | None,
    spec: ResearchEvaluationSpec,
    logical_result_hash: str,
) -> ResearchReport:
    """한 불변 run을 시간순 fold별로 평가하며 경계 표본을 명시적으로 제외한다."""
    observation_rows = tuple(dict(row) for row in observations)
    candidate_rows = tuple(_document(row) for row in candidate_events)
    decision_rows = tuple(dict(row) for row in decisions)
    event_rows = tuple(_document(row) for row in execution_events)
    label_rows = tuple(_document(row) for row in outcome_labels)
    quality = _data_quality(input_manifest, observation_rows, spec, logical_result_hash)
    cost_reasons = _cost_model_reasons(cost_model, spec)
    historical_reconstruction = _historical_reconstruction_input(input_manifest)
    limitations = (
        "partial_fill_model_unsupported",
        "vi_orderability_not_exported",
    ) + ((
        "posthoc_candidate_population_not_contemporaneous_top20",
        "historical_bar_close_replay_clock_with_source_availability_preserved",
    ) if historical_reconstruction else ())
    trades = _closed_trades(event_rows)
    candidate_by_id = {
        str(row.get("event_id", "")): row for row in candidate_rows
    }
    fold_reports = tuple(
        _build_fold_report(
            fold=fold, spec=spec, trades=trades, decisions=decision_rows,
            events=event_rows, labels=label_rows, candidate_by_id=candidate_by_id,
            initial_cash_won=initial_cash_won,
            common_reasons=tuple(quality["reasons"]) + tuple(cost_reasons),
        )
        for fold in spec.folds
    )
    reasons = list(dict.fromkeys(tuple(quality["reasons"]) + tuple(cost_reasons)))
    if any(report.status == "INELIGIBLE" for report in fold_reports):
        status = "INELIGIBLE"
    elif all(report.status in {"NOT_APPLICABLE", "SEALED"} for report in fold_reports):
        status = "NOT_APPLICABLE"
        if not reasons:
            reasons.append("no_closed_simulated_trade")
    elif any(report.status == "SEALED" for report in fold_reports):
        status = "ELIGIBLE_WITH_SEALED_HOLDOUT"
    else:
        status = "ELIGIBLE"
    identity_body = {
        "run_id": run_id,
        "status": status,
        "reasons": tuple(reasons),
        "split_spec": spec.to_dict(),
        "data_quality": quality,
        "cost_model": dict(cost_model) if cost_model is not None else None,
        "limitations": limitations,
        "fold_reports": tuple(report.to_dict() for report in fold_reports),
    }
    return ResearchReport(
        report_id=_content_id("research_report", identity_body), run_id=run_id,
        status=status, reasons=tuple(reasons), split_spec=spec.to_dict(),
        data_quality=quality,
        cost_model=dict(cost_model) if cost_model is not None else None,
        limitations=limitations,
        fold_reports=fold_reports,
    )


def build_research_comparison(
    *,
    baseline_run_id: str,
    variant_run_id: str,
    changed_condition: str,
    input_dataset_id: str,
    input_revision_ids_hash: str,
    baseline_report: Mapping[str, Any],
    variant_report: Mapping[str, Any],
    baseline_candidates: Iterable[Mapping[str, Any]],
    variant_candidates: Iterable[Mapping[str, Any]],
    baseline_events: Iterable[Mapping[str, Any]],
    variant_events: Iterable[Mapping[str, Any]],
    spec: ResearchEvaluationSpec,
) -> ResearchComparison:
    """한 조건만 다른 두 run을 candidate identity와 fold 경계로 비교한다."""
    if not baseline_run_id or not variant_run_id or baseline_run_id == variant_run_id:
        raise ValueError("comparison requires two distinct research runs")
    if changed_condition not in {
        "rank_persistence_filter", "market_type_filter", "market_raw_feature_filter",
    }:
        raise ValueError(f"unsupported research comparison condition: {changed_condition}")
    baseline_folds = _fold_report_map(baseline_report)
    variant_folds = _fold_report_map(variant_report)
    expected_names = tuple(fold.name for fold in spec.folds)
    if tuple(baseline_folds) != expected_names or tuple(variant_folds) != expected_names:
        raise ValueError("comparison reports must use the same ordered fold spec")
    baseline_keys = {
        str(row.get("decision_id", "")): str(row.get("dedup_key", ""))
        for row in baseline_candidates if row.get("decision_id") and row.get("dedup_key")
    }
    variant_keys = {
        str(row.get("decision_id", "")): str(row.get("dedup_key", ""))
        for row in variant_candidates if row.get("decision_id") and row.get("dedup_key")
    }
    baseline_trades = _closed_trades(tuple(baseline_events), baseline_keys)
    variant_trades = _closed_trades(tuple(variant_events), variant_keys)
    comparisons = tuple(_compare_fold(
        fold, spec, baseline_folds[fold.name], variant_folds[fold.name],
        baseline_trades, variant_trades,
    ) for fold in spec.folds)
    reasons = tuple(dict.fromkeys(
        reason for comparison in comparisons for reason in comparison.reasons
    ))
    if any(comparison.status == "INELIGIBLE" for comparison in comparisons):
        status = "INELIGIBLE"
    elif all(comparison.status in {"NOT_APPLICABLE", "SEALED"} for comparison in comparisons):
        status = "NOT_APPLICABLE"
    elif any(comparison.status == "SEALED" for comparison in comparisons):
        status = "ELIGIBLE_WITH_SEALED_HOLDOUT"
    else:
        status = "ELIGIBLE"
    body = {
        "baseline_run_id": baseline_run_id, "variant_run_id": variant_run_id,
        "changed_condition": changed_condition, "status": status, "reasons": reasons,
        "input_dataset_id": input_dataset_id,
        "input_revision_ids_hash": input_revision_ids_hash,
        "fold_comparisons": tuple(value.to_dict() for value in comparisons),
    }
    return ResearchComparison(
        comparison_id=_content_id("research_comparison", body),
        baseline_run_id=baseline_run_id, variant_run_id=variant_run_id,
        changed_condition=changed_condition, status=status, reasons=reasons,
        input_dataset_id=input_dataset_id,
        input_revision_ids_hash=input_revision_ids_hash,
        fold_comparisons=comparisons,
    )


def _compare_fold(
    fold: Any,
    spec: ResearchEvaluationSpec,
    baseline_report: Mapping[str, Any],
    variant_report: Mapping[str, Any],
    baseline_trades: tuple[Mapping[str, Any], ...],
    variant_trades: tuple[Mapping[str, Any], ...],
) -> ResearchFoldComparison:
    statuses = (str(baseline_report.get("status", "")), str(variant_report.get("status", "")))
    if "SEALED" in statuses:
        return _empty_fold_comparison(fold.name, fold.role, "SEALED", ("final_holdout_not_accessed",))
    reasons = tuple(dict.fromkeys(
        str(reason) for report in (baseline_report, variant_report)
        for reason in report.get("reasons", ()) if str(reason)
    ))
    baseline_included = _fold_trade_map(baseline_trades, fold.name, spec)
    variant_included = _fold_trade_map(variant_trades, fold.name, spec)
    baseline_set = set(baseline_included)
    variant_set = set(variant_included)
    shared = baseline_set & variant_set
    baseline_only = baseline_set - variant_set
    variant_only = variant_set - baseline_set
    baseline_pnl = sum(int(row["net_pnl_won"]) for row in baseline_included.values())
    variant_pnl = sum(int(row["net_pnl_won"]) for row in variant_included.values())
    if "INELIGIBLE" in statuses:
        status = "INELIGIBLE"
    elif not baseline_included and not variant_included:
        status = "NOT_APPLICABLE"
        reasons = tuple(dict.fromkeys((*reasons, "no_closed_simulated_trade")))
    else:
        status = "ELIGIBLE"
    return ResearchFoldComparison(
        name=fold.name, role=fold.role, status=status, reasons=reasons,
        baseline_closed_trade_count=len(baseline_included),
        variant_closed_trade_count=len(variant_included),
        baseline_net_pnl_won=baseline_pnl, variant_net_pnl_won=variant_pnl,
        net_pnl_delta_won=variant_pnl - baseline_pnl,
        shared_candidate_count=len(shared),
        baseline_only_candidate_count=len(baseline_only),
        variant_only_candidate_count=len(variant_only),
        avoided_loss_won=sum(
            max(0, -int(baseline_included[key]["net_pnl_won"])) for key in baseline_only
        ),
        missed_profit_won=sum(
            max(0, int(baseline_included[key]["net_pnl_won"])) for key in baseline_only
        ),
        variant_only_net_pnl_won=sum(
            int(variant_included[key]["net_pnl_won"]) for key in variant_only
        ),
        event_outcome_deltas=_compare_event_outcomes(baseline_report, variant_report),
    )


def _empty_fold_comparison(
    name: str, role: str, status: str, reasons: tuple[str, ...],
) -> ResearchFoldComparison:
    return ResearchFoldComparison(
        name, role, status, reasons, None, None, None, None, None, None, None,
        None, None, None, None, (),
    )


def _fold_report_map(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = report.get("fold_reports")
    if not isinstance(rows, (list, tuple)):
        raise ValueError("research report fold rows are missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not str(row.get("name", "")):
            raise ValueError("research report contains an invalid fold row")
        result[str(row["name"])] = row
    return result


def _fold_trade_map(
    trades: tuple[Mapping[str, Any], ...], fold_name: str, spec: ResearchEvaluationSpec,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for trade in trades:
        candidate_key = str(trade.get("candidate_key", ""))
        if not candidate_key:
            continue
        assignment = assign_interval_to_fold(
            spec, str(trade["opened_at"]), str(trade["closed_at"]),
        )
        if assignment.fold_name == fold_name and assignment.status == "INCLUDED":
            if candidate_key in result:
                raise ValueError("comparison candidate key closed more than once in one run")
            result[candidate_key] = trade
    return result


def _compare_event_outcomes(
    baseline: Mapping[str, Any], variant: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    def rows(value: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
        raw = value.get("event_outcomes", ())
        return {
            int(row["horizon_seconds"]): row
            for row in raw if isinstance(row, Mapping) and row.get("horizon_seconds") is not None
        }
    base = rows(baseline)
    changed = rows(variant)
    return tuple({
        "horizon_seconds": horizon,
        "baseline_sample_count": int(base.get(horizon, {}).get("sample_count", 0)),
        "variant_sample_count": int(changed.get(horizon, {}).get("sample_count", 0)),
        "average_return_bps_delta": (
            int(changed[horizon]["average_return_bps"])
            - int(base[horizon]["average_return_bps"])
            if horizon in base and horizon in changed else None
        ),
    } for horizon in sorted(set(base) | set(changed)))


def _build_fold_report(
    *,
    fold: Any,
    spec: ResearchEvaluationSpec,
    trades: tuple[Mapping[str, Any], ...],
    decisions: tuple[Mapping[str, Any], ...],
    events: tuple[Mapping[str, Any], ...],
    labels: tuple[Mapping[str, Any], ...],
    candidate_by_id: Mapping[str, Mapping[str, Any]],
    initial_cash_won: int,
    common_reasons: tuple[str, ...],
) -> ResearchFoldReport:
    if fold.role == "OOS" and not spec.final_holdout_accessed_at:
        return _empty_fold_report(fold.name, fold.role, "SEALED", ("final_holdout_not_accessed",))
    included: list[Mapping[str, Any]] = []
    purged_trade_count = 0
    for trade in trades:
        assignment = assign_interval_to_fold(
            spec, str(trade["opened_at"]), str(trade["closed_at"]),
        )
        if assignment.fold_name != fold.name:
            continue
        if assignment.status == "INCLUDED":
            included.append(trade)
        elif assignment.status == "PURGED":
            purged_trade_count += 1
    included_labels: list[Mapping[str, Any]] = []
    purged_outcome_count = 0
    for label in labels:
        candidate = candidate_by_id.get(str(label.get("candidate_event_id", "")))
        if candidate is None:
            continue
        assignment = assign_interval_to_fold(
            spec, str(candidate.get("available_at", "")), str(label.get("horizon_end", "")),
        )
        if assignment.fold_name != fold.name:
            continue
        if assignment.status == "PURGED":
            purged_outcome_count += 1
        elif assignment.status == "INCLUDED" and label.get("status") == "COMPLETE":
            included_labels.append(label)
    fold_events = tuple(row for row in events if _point_in_fold(row.get("occurred_at"), fold))
    fold_decisions = tuple(
        row for row in decisions if _point_in_fold(row.get("decided_at"), fold)
    )
    no_trade_reasons: dict[str, int] = {}
    for decision in fold_decisions:
        if decision.get("final_action") != "NO_TRADE":
            continue
        raw_reasons = decision.get("reasons")
        for reason in raw_reasons if isinstance(raw_reasons, (list, tuple)) else ():
            key = str(reason)
            no_trade_reasons[key] = no_trade_reasons.get(key, 0) + 1
    reasons = list(common_reasons)
    closed = len(included)
    active_days = len({_kst_date(row["closed_at"]) for row in included})
    if closed < spec.minimum_closed_trades:
        reasons.append("minimum_closed_trades_not_met")
    if active_days < spec.minimum_active_days:
        reasons.append("minimum_active_days_not_met")
    if common_reasons or closed < spec.minimum_closed_trades or active_days < spec.minimum_active_days:
        status = "INELIGIBLE"
    elif not included:
        status = "NOT_APPLICABLE"
        reasons.append("no_closed_simulated_trade")
    else:
        status = "ELIGIBLE"
    pnl = [int(row["net_pnl_won"]) for row in included]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    total_cost = sum(int(row["total_cost_won"]) for row in included)
    net = sum(pnl)
    gross = net + total_cost
    mdd_won, mdd_ppm = _maximum_drawdown(fold, events, initial_cash_won)
    submitted_intents = {
        str(row.get("intent_id", "")) for row in fold_events
        if row.get("event_type") == "ORDER_SUBMITTED" and row.get("intent_id")
    }
    filled_intents = {
        str(row.get("intent_id", "")) for row in fold_events
        if row.get("event_type") in {"FILL", "PARTIAL_FILL"} and row.get("intent_id")
    }
    submitted = len(submitted_intents)
    filled = len(submitted_intents & filled_intents)
    return ResearchFoldReport(
        name=fold.name, role=fold.role, status=status,
        reasons=tuple(dict.fromkeys(reasons)), closed_trade_count=closed,
        active_day_count=active_days,
        winning_trade_count=len(wins) if included else None,
        losing_trade_count=len(losses) if included else None,
        gross_realized_pnl_won=gross if included else None,
        total_cost_won=total_cost if included else None,
        net_realized_pnl_won=net if included else None,
        expectancy_won=net // closed if included else None,
        payoff_ratio_ppm=(
            (sum(wins) * len(losses) * 1_000_000) // (abs(sum(losses)) * len(wins))
            if wins and losses else None
        ),
        profit_factor_ppm=(sum(wins) * 1_000_000 // abs(sum(losses))) if losses else None,
        turnover_won=sum(int(row["turnover_won"]) for row in included),
        exposure_seconds=sum(int(row["exposure_seconds"]) for row in included),
        max_drawdown_won=mdd_won, max_drawdown_ppm=mdd_ppm,
        submitted_order_count=submitted,
        filled_order_count=filled,
        fill_rate_ppm=filled * 1_000_000 // submitted if submitted else None,
        rejected_order_count=sum(
            row.get("event_type") in {"ORDER_REJECTED", "ORDER_CANCELLED"} for row in fold_events
        ),
        censored_order_count=sum(
            row.get("event_type") in {"ORDER_CENSORED", "POSITION_CENSORED"} for row in fold_events
        ),
        unsupported_order_count=sum(
            row.get("event_type") == "ORDER_UNSUPPORTED" for row in fold_events
        ),
        partial_fill_count=sum(
            row.get("event_type") == "PARTIAL_FILL" or row.get("state") == "PARTIAL"
            for row in fold_events
        ),
        purged_trade_count=purged_trade_count,
        purged_outcome_count=purged_outcome_count,
        no_trade_reasons=dict(sorted(no_trade_reasons.items())),
        event_outcomes=_event_outcome_rows(included_labels),
        by_symbol=_group_trade_rows(included, lambda row: str(row["symbol"])),
        by_date=_group_trade_rows(included, lambda row: _kst_date(row["opened_at"])),
        by_time_bucket=_group_trade_rows(included, lambda row: _time_bucket(row["opened_at"])),
    )


def _empty_fold_report(
    name: str, role: str, status: str, reasons: tuple[str, ...],
) -> ResearchFoldReport:
    return ResearchFoldReport(
        name, role, status, reasons, 0, 0, None, None, None, None, None, None,
        None, None, 0, 0, None, None, 0, 0, None, 0, 0, 0, 0, 0, 0,
        {}, (), (), (), (),
    )


def _closed_trades(
    events: tuple[Mapping[str, Any], ...],
    candidate_keys_by_decision: Mapping[str, str] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    ordered = sorted(events, key=lambda row: (
        _aware_datetime(row.get("occurred_at")).astimezone(timezone.utc),
        str(row.get("event_id", "")),
    ))
    entries: dict[str, list[dict[str, Any]]] = {}
    result: list[dict[str, Any]] = []
    for event in ordered:
        if event.get("event_type") not in {"FILL", "RISK_FILL", "PARTIAL_FILL"}:
            continue
        symbol = str(event.get("symbol", ""))
        quantity = int(event.get("quantity", 0))
        if event.get("side") == "BUY" and quantity > 0:
            entries.setdefault(symbol, []).append({
                "opened_at": str(event.get("occurred_at", "")),
                "quantity": quantity,
                "remaining": quantity,
                "notional_won": int(event.get("notional_won", 0)),
                "entry_cost_won": int(event.get("total_cost_won", 0)),
                "decision_id": str(event.get("decision_id", "")),
            })
            continue
        if event.get("side") != "SELL" or quantity <= 0 or not entries.get(symbol):
            continue
        remaining_sell = quantity
        while remaining_sell > 0 and entries[symbol]:
            entry = entries[symbol][0]
            matched = min(remaining_sell, int(entry["remaining"]))
            entry_quantity = int(entry["quantity"])
            allocated_notional = int(entry["notional_won"]) * matched // entry_quantity
            allocated_cost = int(entry["entry_cost_won"]) * matched // entry_quantity
            sell_cost = int(event.get("total_cost_won", 0)) * matched // quantity
            sell_notional = int(event.get("notional_won", 0)) * matched // quantity
            realized = int(event.get("realized_pnl_won", 0)) * matched // quantity
            opened_at = str(entry["opened_at"])
            closed_at = str(event.get("occurred_at", ""))
            result.append({
                "symbol": symbol, "opened_at": opened_at, "closed_at": closed_at,
                "candidate_key": (
                    candidate_keys_by_decision.get(str(entry["decision_id"]), "")
                    if candidate_keys_by_decision is not None else ""
                ),
                "quantity": matched, "net_pnl_won": realized,
                "total_cost_won": allocated_cost + sell_cost,
                "turnover_won": allocated_notional + sell_notional,
                "exposure_seconds": max(0, int(
                    (_aware_datetime(closed_at) - _aware_datetime(opened_at)).total_seconds()
                )),
            })
            entry["remaining"] = int(entry["remaining"]) - matched
            remaining_sell -= matched
            if entry["remaining"] == 0:
                entries[symbol].pop(0)
    return tuple(result)


def _event_outcome_rows(labels: list[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    grouped: dict[int, list[int]] = {}
    for label in labels:
        values = label.get("values")
        if not isinstance(values, Mapping) or values.get("return_bps") is None:
            continue
        grouped.setdefault(int(label.get("horizon_seconds", 0)), []).append(
            int(values["return_bps"])
        )
    return tuple({
        "horizon_seconds": horizon, "sample_count": len(values),
        "average_return_bps": sum(values) // len(values),
        "median_return_bps": int(median(values)),
        "positive_count": sum(value > 0 for value in values),
    } for horizon, values in sorted(grouped.items()))


def _group_trade_rows(
    trades: list[Mapping[str, Any]], key_fn: Any,
) -> tuple[Mapping[str, Any], ...]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for trade in trades:
        grouped.setdefault(str(key_fn(trade)), []).append(trade)
    return tuple({
        "key": key, "closed_trade_count": len(values),
        "net_realized_pnl_won": sum(int(row["net_pnl_won"]) for row in values),
        "winning_trade_count": sum(int(row["net_pnl_won"]) > 0 for row in values),
    } for key, values in sorted(grouped.items()))


def _maximum_drawdown(
    fold: Any, events: tuple[Mapping[str, Any], ...], initial_cash_won: int,
) -> tuple[int | None, int | None]:
    ordered = sorted(events, key=lambda row: (
        _aware_datetime(row.get("occurred_at")).astimezone(timezone.utc),
        str(row.get("event_id", "")),
    ))
    last_equity = initial_cash_won
    fold_start = _aware_datetime(fold.start).astimezone(timezone.utc)
    for event in ordered:
        occurred = _aware_datetime(event.get("occurred_at")).astimezone(timezone.utc)
        if occurred >= fold_start:
            break
        price = int(event.get("price", 0))
        if price > 0 and event.get("event_type") in {"FILL", "RISK_FILL", "MARK", "PARTIAL_FILL"}:
            last_equity = int(event.get("cash_after_won", 0)) + (
                int(event.get("position_quantity_after", 0)) * price
            )
    points: list[int] = [last_equity]
    for event in ordered:
        occurred = _aware_datetime(event.get("occurred_at")).astimezone(timezone.utc)
        if occurred < fold_start:
            continue
        price = int(event.get("price", 0))
        if price > 0 and event.get("event_type") in {"FILL", "RISK_FILL", "MARK", "PARTIAL_FILL"}:
            last_equity = int(event.get("cash_after_won", 0)) + (
                int(event.get("position_quantity_after", 0)) * price
            )
        if _point_in_fold(occurred.isoformat(), fold):
            points.append(last_equity)
    if len(points) == 1:
        return None, None
    peak = points[0]
    maximum = 0
    relative_drawdown = relative_peak = 0
    for value in points:
        peak = max(peak, value)
        drawdown = peak - value
        maximum = max(maximum, drawdown)
        # The worst relative loss can precede a later, larger absolute loss.
        if peak > 0 and (relative_peak == 0 or drawdown * relative_peak > relative_drawdown * peak):
            relative_drawdown, relative_peak = drawdown, peak
    return maximum, relative_drawdown * 1_000_000 // relative_peak if relative_peak > 0 else None


def _data_quality(
    manifest: Mapping[str, Any], observations: tuple[Mapping[str, Any], ...],
    spec: ResearchEvaluationSpec, logical_result_hash: str,
) -> Mapping[str, Any]:
    minute = tuple(row for row in observations if row.get("kind") == "minute_bar")
    historical_reconstruction = _historical_reconstruction_input(manifest)
    universe_kind = (
        "historical_candidate_population" if historical_reconstruction else "top20_membership"
    )
    universe = tuple(row for row in observations if row.get("kind") == universe_kind)
    strict = tuple(row for row in minute if (
        row.get("venue") == "KRX" and row.get("completeness") == "complete"
        and row.get("value_kind") == "actual" and isinstance(row.get("payload"), Mapping)
        and row["payload"].get("window_closed") is True
        and row["payload"].get("capture_quality") == "complete"
    ))
    reasons: list[str] = []
    if not strict:
        reasons.append("strict_krx_minute_bars_missing")
    if not universe:
        reasons.append("candidate_universe_missing")
    if historical_reconstruction and manifest.get("not_contemporaneous_top20") is not True:
        reasons.append("historical_population_boundary_missing")
    revision_ids = [str(row.get("revision_id", "")) for row in observations]
    actual_hash = hashlib.sha256("\n".join(revision_ids).encode("utf-8")).hexdigest()
    reproducible = (
        bool(logical_result_hash)
        and len(observations) == int(manifest.get("revision_count", -1))
        and actual_hash == manifest.get("revision_ids_hash")
    )
    if not reproducible:
        reasons.append("input_manifest_or_result_hash_unverified")
    available_times = [
        _aware_datetime(row.get("available_at")).astimezone(timezone.utc)
        for row in observations if row.get("available_at")
    ]
    warmup_covered = bool(available_times) and min(available_times) <= warmup_start(spec)
    if manifest.get('development_partition') is not None:
        # Universe seeds and backfills received after active start cannot prove bar warmup.
        lower = warmup_start(spec)
        active = _aware_datetime(spec.folds[0].start).astimezone(timezone.utc)
        warmup_covered = spec.warmup_seconds == 0 or any(
            _aware_datetime(row['payload']['bar_start']).astimezone(timezone.utc) <= lower
            < _aware_datetime(row['payload']['bar_end']).astimezone(timezone.utc) <= active
            and _aware_datetime(row['available_at']).astimezone(timezone.utc) <= active
            for row in strict
        )
    if not warmup_covered:
        reasons.append("configured_warmup_not_covered")
    return {
        "status": "INELIGIBLE" if reasons else "PASS",
        "reasons": tuple(reasons),
        "revision_count": len(observations),
        "strict_krx_minute_bar_revision_count": len(strict),
        "excluded_minute_bar_revision_count": len(minute) - len(strict),
        "candidate_universe_revision_count": len(universe),
        "reproducibility": "VERIFIED" if reproducible else "UNVERIFIED",
    }


def _historical_reconstruction_input(manifest: Mapping[str, Any]) -> bool:
    return (
        manifest.get("runtime_input_version") == "historical_reconstruction_strategy/v1"
        or manifest.get("source_runtime_input_version") == "historical_reconstruction_strategy/v1"
    )


def _cost_model_reasons(
    cost_model: Mapping[str, Any] | None, spec: ResearchEvaluationSpec,
) -> tuple[str, ...]:
    if cost_model is None:
        return ("cost_model_missing",)
    reasons: list[str] = []
    if cost_model.get("rate_basis") == "unspecified" or not str(cost_model.get("source", "")).strip():
        reasons.append("cost_model_provenance_missing")
    valid_from = str(cost_model.get("valid_from", ""))
    valid_to = str(cost_model.get("valid_to", ""))
    if not valid_from or not valid_to:
        reasons.append("cost_model_validity_missing")
    else:
        start = _aware_datetime(valid_from).astimezone(timezone.utc)
        end = _aware_datetime(valid_to).astimezone(timezone.utc)
        first = _aware_datetime(spec.folds[0].start).astimezone(timezone.utc)
        last = _aware_datetime(spec.folds[-1].end).astimezone(timezone.utc)
        if start > first or end < last:
            reasons.append("cost_model_validity_does_not_cover_folds")
    return tuple(reasons)


def _point_in_fold(value: object, fold: Any) -> bool:
    point = _aware_datetime(value).astimezone(timezone.utc)
    return (
        _aware_datetime(fold.start).astimezone(timezone.utc)
        <= point
        < _aware_datetime(fold.end).astimezone(timezone.utc)
    )


def _kst_date(value: object) -> str:
    return _aware_datetime(value).astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()


def _time_bucket(value: object) -> str:
    hour = _aware_datetime(value).astimezone(ZoneInfo("Asia/Seoul")).hour
    if 9 <= hour < 10:
        return "09:00-10:00"
    if 10 <= hour < 12:
        return "10:00-12:00"
    return "other"


def _document(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return value.to_dict()


def _label(
    candidate: CandidateEvent,
    horizon_seconds: int,
    horizon_end: datetime,
    *,
    matured_at: str,
    computed_at: str,
    available_at: str,
    status: str,
    values: Mapping[str, Any] | None,
    input_refs: tuple[str, ...],
    reason: str,
) -> OutcomeLabel:
    body = {
        "run_id": candidate.run_id,
        "candidate_event_id": candidate.event_id,
        "symbol": candidate.symbol,
        "horizon_seconds": horizon_seconds,
        "horizon_end": horizon_end.isoformat(),
        "matured_at": matured_at,
        "computed_at": computed_at,
        "available_at": available_at,
        "status": status,
        "values": values,
        "input_refs": input_refs,
        "reason": reason,
    }
    return OutcomeLabel(label_id=_content_id("outcome", body), **body)


def _summary(
    run_id: str,
    status: str,
    reason: str,
    initial_cash_won: int,
    closed_trade_count: int,
    winning_trade_count: int | None,
    gross: int | None,
    cost: int | None,
    net: int | None,
    return_ppm: int | None,
    optimistic: int | None,
) -> PerformanceSummary:
    body = {
        "run_id": run_id, "status": status, "reason": reason,
        "initial_cash_won": initial_cash_won,
        "closed_trade_count": closed_trade_count,
        "winning_trade_count": winning_trade_count,
        "gross_realized_pnl_won": gross, "total_cost_won": cost,
        "net_realized_pnl_won": net, "return_ppm": return_ppm,
        "optimistic_additional_pnl_won": optimistic,
    }
    return PerformanceSummary(evaluation_id=_content_id("performance", body), **body)


def _continuous_minutes(bars: list[KrxMinuteBarFrame]) -> bool:
    if not bars:
        return False
    for previous, current in zip(bars, bars[1:]):
        if _aware_datetime(previous.bar_end) != _aware_datetime(current.bar_start):
            return False
    return all(
        _aware_datetime(bar.bar_end) - _aware_datetime(bar.bar_start) == timedelta(minutes=1)
        for bar in bars
    )


def _continuous_minutes_from(event_time: datetime, bars: list[KrxMinuteBarFrame]) -> bool:
    if not bars:
        return False
    normalized = event_time.astimezone(timezone.utc)
    expected = normalized.replace(second=0, microsecond=0)
    if normalized != expected:
        expected += timedelta(minutes=1)
    first = _aware_datetime(bars[0].bar_start).astimezone(timezone.utc)
    return first == expected and _continuous_minutes(bars)


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def _aware_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("outcome timestamps must be timezone-aware")
    return parsed
