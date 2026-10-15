"""Order-free target sizing from supplied account/ledger cuts; no account owner."""

from __future__ import annotations

import hashlib
import json
import math
from fractions import Fraction
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from kiwoom_monitor.application.research_execution import SimulationCostModel, estimate_buy_cash_won
from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot, OrderIntent, OrderSide, OrderState, OrderType, TERMINAL_ORDER_STATES,
)
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRecord


POSITION_TARGET_SHADOW_VERSION = "position-target-shadow/v1"
POSITION_ACTION_COMPARISON_SHADOW_VERSION = "position-action-comparison-shadow/v1"
DYNAMIC_POSITION_ALLOCATION_SHADOW_VERSION = 'dynamic-position-allocation-shadow/v1'
_ACTIONS = frozenset({"ADD", "REDUCE", "REENTER", "EXIT", "HOLD", "WAIT"})


@dataclass(frozen=True)
class DynamicAllocationEvEvidence:
    """Paired native research EV at one tested size; no size interpolation."""
    symbol: str
    action: str
    initial_quantity: int
    target_quantity: int
    reference_price_won: int
    net_ev_bps: int
    ev_uncertainty_bps: int
    cost_model_ref: str
    horizon_seconds: int
    population_ref: str
    comparison_ref: str
    input_refs: tuple[str, ...]
    available_at: datetime
    valid_until: datetime

    def __post_init__(self):
        _symbol(self.symbol)
        for name in ('initial_quantity','ev_uncertainty_bps'):
            _integer(getattr(self,name),name)
        for name in ('target_quantity','reference_price_won','horizon_seconds'):
            _integer(getattr(self,name),name,1)
        if (self.action not in {'ADD','REENTER'} or self.target_quantity<=self.initial_quantity
                or (self.action=='ADD')!=(self.initial_quantity>0)
                or type(self.net_ev_bps) is not int
                or any(type(getattr(self,n)) is not str or not 0<len(getattr(self,n))<=256
                       for n in ('cost_model_ref','population_ref','comparison_ref'))
                or type(self.input_refs) is not tuple or not 4<=len(self.input_refs)<=200
                or len(set(self.input_refs))!=len(self.input_refs)
                or any(type(r) is not str or not r.startswith('native-position-outcome_') for r in self.input_refs)
                or _time(self.valid_until)<=_time(self.available_at)):
            raise ValueError('invalid bounded allocation EV evidence')


@dataclass(frozen=True)
class FullExitStressEvidence:
    """Declared complete scenario bound, never an actual liquidation guarantee."""
    symbol: str
    tested_position_quantity: int
    reference_price_won: int
    exit_window_seconds: int
    conservative_exit_capacity_won: int
    stress_loss_bps: int
    cost_model_ref: str
    source_ref: str
    scenario_count: int
    available_at: datetime
    valid_until: datetime

    def __post_init__(self):
        _symbol(self.symbol)
        for name in ('tested_position_quantity','reference_price_won','exit_window_seconds'):
            _integer(getattr(self,name),name,1)
        _integer(self.stress_loss_bps,'stress_loss_bps')
        _integer(self.conservative_exit_capacity_won,'conservative_exit_capacity_won',1)
        if (self.conservative_exit_capacity_won!=self.tested_position_quantity*self.reference_price_won
                or type(self.scenario_count) is not int or not 2<=self.scenario_count<=20
                or any(type(v) is not str or not 0<len(v)<=256 for v in (self.cost_model_ref,self.source_ref))
                or _time(self.valid_until)<=_time(self.available_at)):
            raise ValueError('invalid complete full-exit scenario evidence')


@dataclass(frozen=True)
class DynamicPositionAllocationPolicy:
    """Explicit research parameters; account limits, never per-symbol investment amounts."""
    policy_ref: str
    account_ref: str
    operating_limit_won: int
    max_symbol_weight_bps: int
    max_theme_weight_bps: int
    max_market_cap_ratio_bps: int
    max_turnover_ratio_bps: int
    max_stress_loss_won: int
    max_volatility_risk_won: int
    cash_buffer_won: int
    minimum_net_ev_bps: int
    ev_uncertainty_multiplier: int
    context_weights: tuple[int, int, int]
    capacity_window_seconds: int
    max_snapshot_age_seconds: int
    available_at: datetime
    valid_until: datetime
    allocation_ev_horizon_seconds: int | None = None
    allocation_exit_horizon_seconds: int | None = None

    def __post_init__(self):
        for name in ('policy_ref', 'account_ref'):
            if type(getattr(self, name)) is not str or not getattr(self, name).strip():
                raise ValueError(name+' is required')
        for name in ('operating_limit_won','max_stress_loss_won','max_volatility_risk_won',
                     'cash_buffer_won','minimum_net_ev_bps','ev_uncertainty_multiplier'):
            _integer(getattr(self,name),name)
        for name in ('max_symbol_weight_bps','max_theme_weight_bps','max_market_cap_ratio_bps','max_turnover_ratio_bps'):
            _integer(getattr(self,name),name)
            if getattr(self,name)>10000:raise ValueError('cash-only bps limit exceeded')
        for name in ('capacity_window_seconds','max_snapshot_age_seconds'):
            _integer(getattr(self,name),name,1)
        if (type(self.context_weights) is not tuple or len(self.context_weights)!=3
                or any(type(v) is not int or not 0 <= v <= 10000 for v in self.context_weights)
                or not sum(self.context_weights)):
            raise ValueError('explicit market/theme/stock context weights are required')
        if _time(self.valid_until)<=_time(self.available_at):raise ValueError('invalid allocation policy period')
        if self.allocation_ev_horizon_seconds is not None:
            _integer(self.allocation_ev_horizon_seconds,'allocation_ev_horizon_seconds',1)
        if self.allocation_exit_horizon_seconds is not None:
            _integer(self.allocation_exit_horizon_seconds,'allocation_exit_horizon_seconds',1)


@dataclass(frozen=True)
class DynamicPositionCandidate:
    symbol: str
    theme_ids: tuple[str, ...]
    input_ref: str
    available_at: datetime
    reference_price_won: int
    market_cap_won: int
    recent_turnover_won: int
    turnover_window_seconds: int
    conservative_exit_capacity_won: int
    market_strength_bps: int | None
    theme_strength_bps: int | None
    stock_strength_bps: int | None
    net_ev_bps: int | None
    ev_uncertainty_bps: int | None
    volatility_bps: int
    stress_loss_bps: int
    ev_evidence: DynamicAllocationEvEvidence | None = None
    full_exit_evidence: FullExitStressEvidence | None = None

    def __post_init__(self):
        _symbol(self.symbol);_time(self.available_at)
        if (type(self.theme_ids) is not tuple or not 1 <= len(self.theme_ids) <= 20
                or len(set(self.theme_ids))!=len(self.theme_ids)
                or any(type(v) is not str or not 0<len(v)<=128 for v in self.theme_ids)
                or type(self.input_ref) is not str or not 0<len(self.input_ref)<=512):
            raise ValueError('candidate theme/source identity invalid')
        for name in ('reference_price_won','market_cap_won','turnover_window_seconds','volatility_bps','stress_loss_bps'):
            _integer(getattr(self,name),name,1)
        for name in ('recent_turnover_won','conservative_exit_capacity_won'):
            _integer(getattr(self,name),name)
        for name in ('market_strength_bps','theme_strength_bps','stock_strength_bps'):
            if getattr(self,name) is None:continue
            _integer(getattr(self,name),name)
            if getattr(self,name)>10000:raise ValueError('context score outside explicit normalized range')
        if self.net_ev_bps is not None and type(self.net_ev_bps) is not int:raise ValueError('invalid net EV')
        if self.ev_uncertainty_bps is not None:_integer(self.ev_uncertainty_bps,'ev_uncertainty_bps')
        if self.ev_evidence is not None and not isinstance(self.ev_evidence,DynamicAllocationEvEvidence):
            raise ValueError('typed allocation EV evidence required')
        if self.full_exit_evidence is not None and not isinstance(self.full_exit_evidence,FullExitStressEvidence):
            raise ValueError('typed full-exit evidence required')


def evaluate_dynamic_position_allocation(policy, candidates, observation, *, decision_time,
        cost_model, risk_disposition, risk_input_ref):
    """Order-free cash allocation; native execution and protection retain authority.

    Context/EV scores and exit capacity are supplied research observations, not a
    new market model. Pending sells never finance another buy or release risk.
    """
    if not isinstance(policy,DynamicPositionAllocationPolicy) or not isinstance(observation,PositionAccountObservation):
        raise ValueError('typed allocation policy/account observation required')
    if (type(candidates) is not tuple or not 1<=len(candidates)<=200
            or any(not isinstance(c,DynamicPositionCandidate) for c in candidates)
            or len({c.symbol for c in candidates})!=len(candidates)
            or len({c.input_ref for c in candidates})!=len(candidates)):
        raise ValueError('bounded unique allocation candidates required')
    if (type(risk_disposition) is not str
            or risk_disposition not in {'ALLOW','CAP','NO_NEW_RISK','REDUCE_ONLY','EXIT_REQUIRED','HALT_NEW_ORDERS'}
            or type(risk_input_ref) is not str or not 0<len(risk_input_ref)<=512):
        raise ValueError('explicit Risk disposition and source required')
    account=observation.account
    if account.account_ref!=policy.account_ref:raise ValueError('allocation account mismatch')
    cut=_time(decision_time);rows=sorted((replace(c,theme_ids=tuple(sorted(c.theme_ids))) for c in candidates),key=lambda c:c.symbol)
    by_symbol={c.symbol:c for c in rows}
    reasons=[]
    def fresh(at,label):
        if not 0 <= (cut-_time(at)).total_seconds() <= policy.max_snapshot_age_seconds:reasons.append(label+'_cut_invalid')
    fresh(account.as_of,'account');fresh(observation.observed_at,'observation')
    if not _time(policy.available_at)<=cut<_time(policy.valid_until):reasons.append('allocation_policy_not_current')
    if not observation.reconciliation_complete or not observation.open_orders_complete or observation.reasons or observation.unmapped_broker_order_ids:
        reasons.append('account_order_projection_unverified')
    needed={s for s,q in account.positions.items() if q}
    needed.update(r.intent.symbol for r in observation.open_orders if r.state not in TERMINAL_ORDER_STATES)
    if observation.broker_open_orders is not None:needed.update(r.symbol for r in observation.broker_open_orders)
    if not needed<=set(by_symbol):reasons.append('held_or_pending_symbol_missing')
    if cost_model is None or not isinstance(cost_model,SimulationCostModel):reasons.append('allocation_buy_cost_unknown')
    elif cost_model.valid_from and not _time(datetime.fromisoformat(cost_model.valid_from))<=cut<_time(datetime.fromisoformat(cost_model.valid_to)):
        reasons.append('allocation_cost_period_invalid')
    source_times=[account.as_of,observation.observed_at,*[c.available_at for c in rows]]
    available_at=max([policy.available_at,*source_times])
    valid_until=min([policy.valid_until,*[at+timedelta(seconds=policy.max_snapshot_age_seconds) for at in source_times]])
    if isinstance(cost_model,SimulationCostModel) and cost_model.valid_to:
        valid_until=min(valid_until,datetime.fromisoformat(cost_model.valid_to))
    if cut>=_time(valid_until):reasons.append('allocation_source_expired')
    projections={}
    for candidate in rows:
        fresh(candidate.available_at,'candidate:'+candidate.symbol)
        for weight,name in zip(policy.context_weights,('market_strength_bps','theme_strength_bps','stock_strength_bps')):
            if weight and getattr(candidate,name) is None:
                reasons.append('allocation_context_score_unavailable:'+candidate.symbol+':'+name)
        if candidate.turnover_window_seconds!=policy.capacity_window_seconds:reasons.append('capacity_window_mismatch:'+candidate.symbol)
        request=PositionTargetShadowRequest(account.account_ref,'mock','KRX',candidate.symbol,'HOLD',
            account.positions.get(candidate.symbol,0),candidate.reference_price_won,candidate.available_at,cut,
            'dynamic-position-allocation-shadow/v1',candidate.input_ref,observation.input_ref,observation.input_ref,
            policy.max_snapshot_age_seconds,observation.reconciliation_complete,observation.open_orders_complete,cost_model)
        projected=evaluate_position_target_shadow(request,account,observation.open_orders,broker_open_orders=observation.broker_open_orders)
        reasons.extend(projected['quality']['projection_reasons']);projections[candidate.symbol]=projected
        exit_evidence=candidate.full_exit_evidence
        if exit_evidence is not None:
            from kiwoom_monitor.application.research_evaluation import position_action_cost_model_ref
            if (exit_evidence.symbol!=candidate.symbol or exit_evidence.reference_price_won!=candidate.reference_price_won
                    or exit_evidence.conservative_exit_capacity_won!=candidate.conservative_exit_capacity_won
                    or exit_evidence.stress_loss_bps!=candidate.stress_loss_bps
                    or exit_evidence.exit_window_seconds!=policy.allocation_exit_horizon_seconds
                    or exit_evidence.cost_model_ref!=position_action_cost_model_ref(cost_model.to_dict() if isinstance(cost_model,SimulationCostModel) else None)
                    or projected['actual_quantity'] is None or projected['pending_buy_quantity'] is None
                    or projected['actual_quantity']+projected['pending_buy_quantity']>exit_evidence.tested_position_quantity):
                reasons.append('full_exit_whole_position_or_model_binding_mismatch:'+candidate.symbol)
            if not _time(exit_evidence.available_at)<=cut<_time(exit_evidence.valid_until):
                reasons.append('full_exit_evidence_expired_or_future:'+candidate.symbol)
            available_at=max(available_at,exit_evidence.available_at)
            valid_until=min(valid_until,exit_evidence.valid_until)
        evidence=candidate.ev_evidence
        if evidence is not None:
            from kiwoom_monitor.application.research_evaluation import position_action_cost_model_ref
            if (evidence.symbol!=candidate.symbol or evidence.reference_price_won!=candidate.reference_price_won
                    or evidence.initial_quantity!=projected['actual_quantity']
                    or projected['pending_buy_quantity'] or projected['pending_sell_quantity']
                    or evidence.net_ev_bps!=candidate.net_ev_bps or evidence.ev_uncertainty_bps!=candidate.ev_uncertainty_bps
                    or evidence.cost_model_ref!=position_action_cost_model_ref(cost_model.to_dict() if cost_model else None)):
                reasons.append('allocation_ev_binding_mismatch:'+candidate.symbol)
            if evidence.horizon_seconds!=policy.allocation_ev_horizon_seconds:
                reasons.append('allocation_ev_horizon_mismatch:'+candidate.symbol)
            if not _time(evidence.available_at)<=cut<_time(evidence.valid_until):
                reasons.append('allocation_ev_expired_or_future:'+candidate.symbol)
            available_at=max(available_at,evidence.available_at)
            valid_until=min(valid_until,evidence.valid_until)
    equity=account.available_cash_won+sum(q*by_symbol[s].reference_price_won for s,q in account.positions.items() if s in by_symbol)
    if equity<=0:reasons.append('current_equity_nonpositive')
    body={'policy_version':'dynamic-position-allocation-shadow/v1','policy_ref':policy.policy_ref,
        'account_ref':account.account_ref,'decision_time':cut.isoformat(),
        'available_at':_time(available_at).isoformat(),'valid_until':_time(valid_until).isoformat(),
        'equity_basis':'cash_plus_marked_long_positions/v1','equity_won':equity if needed<=set(by_symbol) else None,
        'account_input_ref':observation.input_ref,'risk_input_ref':risk_input_ref,'risk_disposition':risk_disposition,
        'scope':{'environment':'mock','venue':'KRX','coverage':'supplied_observation_universe','source_durability':'not_verified'},
        'execution_support':'shadow_only','order_authorized':False,'protection_required':risk_disposition=='EXIT_REQUIRED',
        'allocations':[], 'reasons':sorted(set(reasons))}
    observation_identity=asdict(observation)
    observation_identity.pop('risk_snapshot')  # Native Risk binding is a later, explicit adapter.
    candidate_identity=[]
    for c in rows:
        value=asdict(c)
        if c.ev_evidence is None:value.pop('ev_evidence')  # Preserve supplied-v1 identities.
        if c.full_exit_evidence is None:value.pop('full_exit_evidence')
        candidate_identity.append(value)
    body.update(available_at=_time(available_at).isoformat(),valid_until=_time(valid_until).isoformat())
    policy_identity=asdict(policy)
    if policy.allocation_ev_horizon_seconds is None:policy_identity.pop('allocation_ev_horizon_seconds')
    if policy.allocation_exit_horizon_seconds is None:policy_identity.pop('allocation_exit_horizon_seconds')
    identity={'policy':policy_identity,'candidates':candidate_identity,
        'observation':observation_identity,'decision_time':cut,'risk_disposition':risk_disposition,'risk_input_ref':risk_input_ref,
        'cost':cost_model.to_dict() if isinstance(cost_model,SimulationCostModel) else None}
    digest=hashlib.sha256(json.dumps(identity,default=_json_value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    body['decision_id']='dynamic-position-allocation-shadow:'+digest
    if reasons:
        return {**body,'status':'BLOCKED','new_buy_cash_won':0}
    budget=min(equity,policy.operating_limit_won)
    theme_cap=equity*policy.max_theme_weight_bps//10000
    baseline={c.symbol:(projections[c.symbol]['actual_quantity']+projections[c.symbol]['pending_buy_quantity'])*c.reference_price_won for c in rows}
    themes={t:sum(baseline[c.symbol] for c in rows if t in c.theme_ids) for c in rows for t in c.theme_ids}
    # Use the same conservative per-share rounding for current and proposed risk.
    stress=sum((baseline[c.symbol]//c.reference_price_won)*((c.reference_price_won*c.stress_loss_bps+9999)//10000) for c in rows)
    volatility=sum((baseline[c.symbol]//c.reference_price_won)*((c.reference_price_won*c.volatility_bps+9999)//10000) for c in rows)
    total=sum(baseline.values())
    caps={c.symbol:min(equity*policy.max_symbol_weight_bps//10000,
        c.market_cap_won*policy.max_market_cap_ratio_bps//10000,
        c.recent_turnover_won*policy.max_turnover_ratio_bps//10000,c.conservative_exit_capacity_won) for c in rows}
    unsafe=(total>budget or stress>policy.max_stress_loss_won or volatility>policy.max_volatility_risk_won
        or any(v>theme_cap for v in themes.values()) or any(baseline[s]>caps[s] for s in baseline))
    no_new=unsafe or risk_disposition in {'NO_NEW_RISK','REDUCE_ONLY','EXIT_REQUIRED','HALT_NEW_ORDERS'}
    weights={}
    for c in rows:
        lower=None if c.net_ev_bps is None or c.ev_uncertainty_bps is None else c.net_ev_bps-policy.ev_uncertainty_multiplier*c.ev_uncertainty_bps
        context=sum(a*b for a,b in zip(policy.context_weights,(c.market_strength_bps,c.theme_strength_bps,c.stock_strength_bps)) if a)
        weights[c.symbol]=Fraction(max(0,(lower or 0)-policy.minimum_net_ev_bps)*context,
            sum(policy.context_weights)*c.volatility_bps)
    weight_sum=sum(weights.values());cash=max(0,account.available_cash_won-account.reserved_open_buy_won-policy.cash_buffer_won)
    remaining_cash=cash;allocations=[]
    goal_total=goal_stress=goal_volatility=0
    goal_themes={theme:0 for theme in themes}
    for c in sorted(rows,key=lambda c:(-weights[c.symbol],c.symbol)):
        price=c.reference_price_won;projection=projections[c.symbol];base_qty=baseline[c.symbol]//price
        ev_unknown=c.net_ev_bps is None or c.ev_uncertainty_bps is None
        desired=min(caps[c.symbol],baseline[c.symbol] if ev_unknown else int(budget*weights[c.symbol]/weight_sum) if weight_sum else 0)
        if c.ev_evidence is not None:
            # An ADD versus HOLD disadvantage says nothing about EXIT value.
            # Only the independent portfolio/Risk/Capacity caps may reduce here.
            desired=min(caps[c.symbol],max(baseline[c.symbol],desired))
        desired_qty=desired//price
        unit_stress=(price*c.stress_loss_bps+9999)//10000;unit_volatility=(price*c.volatility_bps+9999)//10000
        # Recheck the proposed portfolio too, including reductions of an already
        # over-limit portfolio. Those reductions never release current cash/risk.
        desired_qty=min(desired_qty,max(0,budget-goal_total)//price,
            min(max(0,theme_cap-goal_themes[t])//price for t in c.theme_ids),
            max(0,policy.max_stress_loss_won-goal_stress)//unit_stress,
            max(0,policy.max_volatility_risk_won-goal_volatility)//unit_volatility)
        if c.ev_evidence is not None:
            desired_qty=min(desired_qty,c.ev_evidence.target_quantity)
        extra=max(0,desired_qty-base_qty)
        limits=[]
        if no_new:extra=0;limits.append('existing_portfolio_or_risk_no_new_exposure')
        if c.net_ev_bps is None or c.ev_uncertainty_bps is None:limits.append('ev_unavailable')
        if projection['pending_sell_quantity'] or projection['pending_buy_quantity']:
            extra=0;limits.append('pending_order_retained_until_reconciled')
        maximum=min(extra,max(0,budget-total)//price,
            min(max(0,theme_cap-themes[t])//price for t in c.theme_ids),
            max(0,policy.max_stress_loss_won-stress)//unit_stress,
            max(0,policy.max_volatility_risk_won-volatility)//unit_volatility)
        extra=_affordable_buy_quantity(price,maximum,remaining_cash,cost_model)
        # Neither a larger nor a partially funded smaller BUY is the measured
        # branch. Retain holdings; independent Risk/Capacity may still reduce.
        if c.ev_evidence is not None and base_qty+extra!=c.ev_evidence.target_quantity:
            extra=0;limits.append('allocation_ev_exact_tested_size_required')
        spent=estimate_buy_cash_won(price,extra,cost_model);remaining_cash-=spent
        addition=extra*price;total+=addition;stress+=extra*unit_stress;volatility+=extra*unit_volatility
        for theme in c.theme_ids:themes[theme]+=addition
        target_qty=min(desired_qty,base_qty+extra)
        goal_total+=target_qty*price;goal_stress+=target_qty*unit_stress;goal_volatility+=target_qty*unit_volatility
        for theme in c.theme_ids:goal_themes[theme]+=target_qty*price
        if extra<max(0,desired_qty-base_qty):limits.append('portfolio_cash_theme_or_risk_cap')
        target_policy=PositionTargetSizingPolicy('bounded-notional-sizing/v1',target_qty*price,caps[c.symbol],
            caps[c.symbol]//price,policy.cash_buffer_won,cut,valid_until)
        allocations.append({'symbol':c.symbol,'input_ref':c.input_ref,'theme_ids':sorted(c.theme_ids),
            'desired_notional_won':desired,'target_notional_won':target_qty*price,
            'target_weight_bps':target_qty*price*10000//equity,'new_buy_quantity':extra,'new_buy_cash_won':spent,
            'worst_case_notional_won':baseline[c.symbol]+addition,'limits':limits,
            'target_policy':json.loads(json.dumps(asdict(target_policy),default=_json_value))})
        if c.ev_evidence is not None:
            allocations[-1]['ev_evidence']={'comparison_ref':c.ev_evidence.comparison_ref,
                'tested_target_quantity':c.ev_evidence.target_quantity,'horizon_seconds':c.ev_evidence.horizon_seconds,
                'source_durability':'not_verified','size_interpolation_supported':False}
        if c.full_exit_evidence is not None:
            allocations[-1]['full_exit_evidence']={'source_ref':c.full_exit_evidence.source_ref,
                'tested_position_quantity':c.full_exit_evidence.tested_position_quantity,
                'exit_window_seconds':c.full_exit_evidence.exit_window_seconds,
                'source_durability':'not_verified','liquidation_guaranteed':False}
    body.update(status='REDUCE_ONLY' if unsafe else 'NO_NEW_RISK' if no_new else 'CALCULATED',
        allocations=sorted(allocations,key=lambda a:a['symbol']),new_buy_cash_won=cash-remaining_cash,
        worst_case_portfolio_notional_won=total,worst_case_theme_notional_won=dict(sorted(themes.items())),
        modeled_stress_loss_won=stress,linear_volatility_risk_won=volatility,operating_budget_won=budget)
    body.update(target_portfolio_notional_won=goal_total,target_theme_notional_won=dict(sorted(goal_themes.items())),
        target_modeled_stress_loss_won=goal_stress,target_linear_volatility_risk_won=goal_volatility)
    if (goal_total>budget or goal_stress>policy.max_stress_loss_won or goal_volatility>policy.max_volatility_risk_won
            or any(v>theme_cap for v in goal_themes.values())):raise RuntimeError('allocation proposed portfolio limit violated')
    if not unsafe and (total>budget or stress>policy.max_stress_loss_won or volatility>policy.max_volatility_risk_won
            or any(v>theme_cap for v in themes.values())):raise RuntimeError('allocation final portfolio limit violated')
    return body


def evaluate_dynamic_position_allocation_document(document):
    """Existing fixed-input preview seam; no collection, account read or dispatch."""
    if (set(document)!={'schema','policy','candidates','observation','decision_time','cost_model','risk_disposition','risk_input_ref'}
            or document['schema']!=DYNAMIC_POSITION_ALLOCATION_SHADOW_VERSION
            or not isinstance(document['candidates'],list) or not 1<=len(document['candidates'])<=200):
        raise ValueError('unsupported dynamic allocation document schema/budget')
    raw=dict(document['policy']);raw['context_weights']=tuple(raw['context_weights'])
    for name in ('available_at','valid_until'):raw[name]=datetime.fromisoformat(raw[name])
    policy=DynamicPositionAllocationPolicy(**raw)
    candidates=[]
    for value in document['candidates']:
        if not isinstance(value.get('theme_ids'),list):raise ValueError('theme IDs require a JSON array')
        raw=dict(value);raw['theme_ids']=tuple(raw['theme_ids']);raw['available_at']=datetime.fromisoformat(raw['available_at'])
        if raw.get('ev_evidence') is not None:
            evidence=dict(raw['ev_evidence'])
            if not isinstance(evidence.get('input_refs'),list):raise ValueError('EV input refs require a JSON array')
            evidence['input_refs']=tuple(evidence['input_refs'])
            for name in ('available_at','valid_until'):evidence[name]=datetime.fromisoformat(evidence[name])
            raw['ev_evidence']=DynamicAllocationEvEvidence(**evidence)
        if raw.get('full_exit_evidence') is not None:
            evidence=dict(raw['full_exit_evidence'])
            for name in ('available_at','valid_until'):evidence[name]=datetime.fromisoformat(evidence[name])
            raw['full_exit_evidence']=FullExitStressEvidence(**evidence)
        candidates.append(DynamicPositionCandidate(**raw))
    raw=dict(document['observation'])
    if (not isinstance(raw.get('reasons'),list)
            or not isinstance(raw.get('unmapped_broker_order_ids',[]),list)):
        raise ValueError('observation reasons/order IDs require JSON arrays')
    if raw.get('risk_snapshot') is not None:raise ValueError('native Risk JSON binding is not supported by this research adapter')
    raw['account'],raw['open_orders']=_position_account_orders_from_dict(raw['account'],raw['open_orders'])
    raw['observed_at']=datetime.fromisoformat(raw['observed_at']);raw['reasons']=tuple(raw['reasons'])
    raw['unmapped_broker_order_ids']=tuple(raw.get('unmapped_broker_order_ids',()))
    if raw.get('broker_open_orders') is not None:
        if not isinstance(raw['broker_open_orders'],list) or len(raw['broker_open_orders'])>400:
            raise ValueError('broker order budget exceeded')
        exposures=[]
        for value in raw['broker_open_orders']:
            value=dict(value);value['side']=OrderSide(value['side']) if value['side'] else None
            value['state']=OrderState(value['state']);value['as_of']=datetime.fromisoformat(value['as_of'])
            exposures.append(PositionBrokerOrderExposure(**value))
        raw['broker_open_orders']=tuple(exposures)
    observation=PositionAccountObservation(**raw)
    cost=SimulationCostModel(**document['cost_model']) if document['cost_model'] is not None else None
    return evaluate_dynamic_position_allocation(policy,tuple(candidates),observation,
        decision_time=datetime.fromisoformat(document['decision_time']),cost_model=cost,
        risk_disposition=document['risk_disposition'],risk_input_ref=document['risk_input_ref'])


@dataclass(frozen=True)
class PositionAccountObservation:
    """Copied RAM receipt from the existing account owner, without order authority."""
    account: AccountSnapshot
    open_orders: tuple[ExecutionRecord, ...]
    input_ref: str
    observed_at: datetime
    reconciliation_complete: bool
    open_orders_complete: bool
    reasons: tuple[str, ...]
    unmapped_broker_order_ids: tuple[str, ...] = ()
    broker_open_orders: tuple | None = None
    risk_snapshot: Any | None = None


@dataclass(frozen=True)
class PositionRunnerAccountObservation(PositionAccountObservation):
    """Opt-in copied owner facts; the legacy observation shape stays unchanged."""
    runner_state: dict | None = None


@dataclass(frozen=True)
class PositionStudyRunnerAccountObservation(PositionRunnerAccountObservation):
    study_portfolio: dict | None = None
    native_study_policy: dict | None = None


def position_study_portfolio_from_account_cut(observation,events,costs):
    """Exact first-entry ledger projection; unsupported histories remain absent."""
    from kiwoom_monitor.application.mock_automation_risk import _exact_fills,_sha256
    from zoneinfo import ZoneInfo
    risk=observation.risk_snapshot
    if (risk is None or not risk.cost_complete or not risk.reconciliation_complete
            or observation.reasons or not observation.reconciliation_complete or not observation.open_orders_complete
            or observation.open_orders or observation.broker_open_orders or observation.account.reserved_open_buy_won
            or not 1<=len(events)<=2000 or len(costs)!=1
            or max(e.accepted_sequence for e in events)!=risk.execution_event_high_watermark):return None
    fills=_exact_fills(events)
    if len(fills)!=1:return None
    fill=fills[0];cost=costs[0];account=observation.account
    document=[{'fill_date':cost.fill_date.isoformat(),'stock_code':cost.stock_code,
        'side':cost.side,'gross_amount':cost.gross_amount,'total_cost':cost.total_cost}]
    if (fill.side!='BUY' or fill.venue!='KRX' or fill.account_ref!=account.account_ref
            or fill.run_id!=risk.execution_run_id or fill.environment!='mock'
            or account.positions!={fill.symbol:fill.quantity}
            or dict(risk.owned_position_quantities)!={fill.symbol:fill.quantity}
            or dict(risk.owned_entry_prices).get(fill.symbol)!=fill.price
            or cost.fill_date!=fill.occurred_at.astimezone(ZoneInfo('Asia/Seoul')).date()
            or cost.stock_code!=fill.symbol or cost.side!='매수' or cost.tax
            or cost.origin_scope.account_ref!=account.account_ref
            or cost.gross_amount!=fill.quantity*fill.price or cost.total_cost!=cost.commission
            or _sha256(document)!=risk.cost_evidence_revision or risk.daily_net_pnl_won!=0):return None
    portfolio={'cash_won':account.available_cash_won,'reserved_cash_won':0,'pending_order':None,
        'realized_pnl_won':0,'total_cost_won':cost.total_cost,
        'position':{'symbol':fill.symbol,'quantity':fill.quantity,'entry_price':fill.price,
            'entry_notional':cost.gross_amount,'entry_commission':cost.commission,
            'opened_at':fill.occurred_at.isoformat(),'entry_intent_id':fill.intent_id}}
    body={'version':'native-position-study-portfolio/v1','source_kind':'owner_ram_first_entry',
        'account_input_ref':observation.input_ref,'risk_snapshot_ref':risk.snapshot_id,
        'cost_evidence_revision':risk.cost_evidence_revision,'fill_cursor':risk.execution_event_high_watermark,
        'available_at':observation.observed_at.isoformat(),'portfolio':portfolio,'order_authorized':False}
    body['portfolio_ref']='native-position-study-portfolio:'+_sha256(body)
    return body


def validate_position_runner_state(document,observation):
    """Validate copied native state provenance, not its simulation equivalence."""
    from kiwoom_monitor.application.breakout_strategy import StrategyState
    from kiwoom_monitor.application.research_families import parse_strategy_config
    keys={'version','source_kind','account_ref','execution_run_id','binding_revision','data_path',
        'account_input_ref','risk_snapshot_ref','account_observed_at','state_available_at','state_revision',
        'runtime_observation_revision','fill_cursor','risk_event_high_watermark','family','parameters',
        'session_profile','registered_implementation_hash','candidate_package_hash','strategy_state','protection_review','session_clock',
        'native_risk_limits','order_authorized','source_durability','state_ref'}
    if (not isinstance(document,dict) or set(document)!=keys
            or document['version']!='native-position-runner-state/v1' or document['source_kind']!='owner_ram'
            or document['order_authorized'] is not False or document['source_durability']!='not_verified'
            or len(json.dumps(document,allow_nan=False).encode())>64*1024):
        raise ValueError('invalid bounded native runner state')
    digest=hashlib.sha256(json.dumps({k:v for k,v in document.items() if k!='state_ref'},
        sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    if document['state_ref']!='native-position-runner-state:'+digest:raise ValueError('native runner state content mismatch')
    for key in ('state_revision','runtime_observation_revision','fill_cursor','risk_event_high_watermark','binding_revision'):
        _integer(document[key],key)
    for key in ('registered_implementation_hash','candidate_package_hash'):
        if type(document[key]) is not str or len(document[key])!=64 or any(c not in '0123456789abcdef' for c in document[key]):
            raise ValueError('invalid native runner registered identity')
    risk=observation.risk_snapshot
    if (risk is None or document['account_ref']!=observation.account.account_ref or document['account_ref']!=risk.account_ref
            or document['execution_run_id']!=risk.execution_run_id or document['binding_revision']!=risk.binding_revision
            or document['data_path']!=risk.data_path or document['account_input_ref']!=observation.input_ref
            or document['risk_snapshot_ref']!=risk.snapshot_id
            or _time(datetime.fromisoformat(document['account_observed_at']))!=_time(observation.observed_at)
            or document['fill_cursor']!=document['risk_event_high_watermark'] or document['fill_cursor']!=risk.execution_event_high_watermark
            or risk.account_as_of!=observation.account.as_of):
        raise ValueError('native runner state account/Risk cut mismatch')
    _time(datetime.fromisoformat(document['state_available_at']))
    parse_strategy_config(document['family'],document['parameters'])
    raw=dict(document['strategy_state']);raw['emitted_candidate_keys']=tuple(raw['emitted_candidate_keys'])
    state=StrategyState(**raw);owned=dict(risk.owned_position_quantities)
    if state.status=='open':
        _integer(state.entry_price,'native entry price',1);_integer(state.position_quantity,'native position quantity',1)
        if (owned!={state.symbol:state.position_quantity} or observation.account.positions.get(state.symbol)!=state.position_quantity
                or dict(risk.owned_entry_prices).get(state.symbol)!=state.entry_price):
            raise ValueError('native runner state fill quantity/entry basis mismatch')
        _time(datetime.fromisoformat(state.opened_at))
    elif owned:raise ValueError('native runner state untracked owned position')
    if document['session_clock'] is not None and not isinstance(document['session_clock'],dict):
        raise ValueError('invalid native runner session clock')
    if document['protection_review'] is not None:
        notice=document['protection_review']
        if (not isinstance(notice,dict) or notice.get('account_ref')!=document['account_ref']
                or notice.get('execution_run_id')!=document['execution_run_id']
                or notice.get('state') not in {'PENDING','RESOLVED_FLAT'} or notice.get('order_authorized') is not False):
            raise ValueError('native runner protection scope mismatch')
    return state


@dataclass(frozen=True)
class PositionBrokerOrderExposure:
    broker_order_id: str
    account_ref: str
    symbol: str
    side: OrderSide | None
    ordered_quantity: int
    filled_quantity: int
    remaining_quantity: int
    state: OrderState
    limit_price: int | None
    as_of: datetime
    intent_ref: str | None
    venue: str = "KRX"


def reconcile_position_account_observation(recovery, before, after, *, input_ref, observed_at,
                                           ledger_complete, runtime_stable, ledger_read_failed=False):
    """Read-only account-wide reconciliation; never invent an intent or fill."""
    account = recovery.account
    reasons = []
    if not ledger_complete: reasons.append("account_order_ledger_read_unavailable" if ledger_read_failed else "account_order_ledger_budget_exceeded")
    if not runtime_stable: reasons.append("execution_changed_during_account_query")
    broker = {order.broker_order_id: order for order in recovery.orders}
    if len(broker) != len(recovery.orders): reasons.append("duplicate_broker_order_identity")
    local = {}
    for record in after:
        if record.intent.account_ref != account.account_ref or record.intent.environment != "mock" or record.intent.venue != "KRX":
            raise ValueError("account order ledger scope mismatch")
        if record.broker_order_id:
            local.setdefault(record.broker_order_id, []).append(record)
        if record.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}:
            reasons.append("unconfirmed_or_cancel_pending_order")
        if not record.broker_order_id or record.broker_order_id not in broker:
            reasons.append("local_active_order_not_confirmed_by_broker")
        if _time(record.intent.created_at) > _time(account.as_of):
            reasons.append("local_intent_after_account_cut")
    before_map = {r.intent.intent_id: r for r in before}
    after_map = {r.intent.intent_id: r for r in after}
    if set(after_map) - set(before_map): reasons.append("local_intent_added_during_account_query")
    for identity, previous in before_map.items():
        current = after_map.get(identity)
        snapshot = broker.get(previous.broker_order_id)
        if current != previous:
            if snapshot is None or (current is None and snapshot.state not in TERMINAL_ORDER_STATES):
                reasons.append("local_ledger_changed_without_broker_evidence")
            elif current is not None and (current.intent != previous.intent or current.broker_order_id != previous.broker_order_id
                    or current.state != snapshot.state or current.filled_quantity != snapshot.filled_quantity):
                reasons.append("local_ledger_changed_without_broker_evidence")
    details = {}
    for detail in getattr(recovery, "order_details", ()):
        details.setdefault(detail.broker_order_id, []).append(detail)
    exposures, unmapped = [], []
    for snapshot in recovery.orders:
        if snapshot.account_ref != account.account_ref:
            raise ValueError("broker order account scope mismatch")
        if _time(snapshot.as_of) > _time(account.as_of): reasons.append("broker_order_after_account_cut")
        if not isinstance(snapshot.state, OrderState): reasons.append("broker_order_state_unknown")
        matches = local.get(snapshot.broker_order_id, [])
        if len(matches) > 1: reasons.append("ambiguous_local_broker_order_identity")
        record = matches[0] if len(matches) == 1 else None
        if record is not None and record.intent.symbol != snapshot.symbol:
            reasons.append("broker_local_symbol_mismatch")
        if snapshot.state in TERMINAL_ORDER_STATES:
            continue
        metadata = details.get(snapshot.broker_order_id, ())
        quantities = {d.ordered_quantity for d in metadata}
        sides = {d.side for d in metadata if d.side is not None}
        prices = {d.limit_price for d in metadata if "broker_order_price_unknown" not in d.reasons}
        venues = {d.venue for d in metadata}
        if any(len(v) > 1 for v in (quantities, sides, prices, venues)):
            reasons.append("broker_order_details_conflict")
        quantity = next(iter(quantities)) if len(quantities) == 1 else record.intent.quantity if not quantities and record else 0
        side = next(iter(sides)) if len(sides) == 1 else record.intent.side if not sides and record else None
        price = next(iter(prices)) if len(prices) == 1 else record.intent.limit_price if not prices and record else None
        venue = next(iter(venues)) if len(venues) == 1 else record.intent.venue if not venues and record else "UNKNOWN"
        if record is None: unmapped.append(snapshot.broker_order_id)
        if record is not None and (quantity != record.intent.quantity or side != record.intent.side
                                  or price != record.intent.limit_price or venue != record.intent.venue):
            reasons.append("broker_local_order_details_mismatch")
        if (side is None or quantity <= 0 or snapshot.filled_quantity + snapshot.remaining_quantity > quantity
                or snapshot.remaining_quantity <= 0 or venue != "KRX"):
            reasons.append("broker_open_order_basis_incomplete")
        if metadata and not prices: reasons.append("broker_order_price_unknown")
        if side is OrderSide.BUY and price is None: reasons.append("open_buy_reservation_unverified")
        exposures.append(PositionBrokerOrderExposure(snapshot.broker_order_id, account.account_ref, snapshot.symbol,
            side, quantity, snapshot.filled_quantity, snapshot.remaining_quantity, snapshot.state, price, snapshot.as_of,
            record.intent.intent_id if record else None, venue))
    if len(exposures) > 400: reasons.append("broker_order_observation_budget_exceeded")
    known_reserved = sum(value.remaining_quantity * value.limit_price for value in exposures
                         if value.side is OrderSide.BUY and value.limit_price is not None)
    if known_reserved > account.reserved_open_buy_won:
        reasons.append("open_buy_reservation_incomplete")
    for symbol in {value.symbol for value in exposures if value.side is OrderSide.SELL}:
        if sum(value.remaining_quantity for value in exposures
               if value.symbol == symbol and value.side is OrderSide.SELL) > account.positions.get(symbol, 0):
            reasons.append("pending_sell_exceeds_actual_position")
    unique_reasons = tuple(sorted(set(reasons)))
    return PositionAccountObservation(account, tuple(after), input_ref, observed_at, not unique_reasons,
        not unique_reasons, unique_reasons, tuple(sorted(unmapped)[:400]), tuple(exposures[:400]))


def _time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("position target times must be timezone-aware")
    return value.astimezone(timezone.utc)


def _integer(value: Any, name: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _symbol(value: str) -> None:
    if type(value) is not str or len(value) != 6 or not value.isascii() or not value.isdigit():
        raise ValueError("position target symbol must be a six-digit stock code")


@dataclass(frozen=True)
class PositionTargetSizingPolicy:
    """Explicit bounded notional policy; derives shares, never selects an action."""
    version: str
    target_notional_won: int
    max_position_notional_won: int
    max_position_quantity: int
    cash_buffer_won: int
    available_at: datetime
    valid_until: datetime

    def __post_init__(self) -> None:
        if self.version != "bounded-notional-sizing/v1":
            raise ValueError("unsupported position sizing policy")
        for name in ("target_notional_won", "max_position_notional_won", "max_position_quantity", "cash_buffer_won"):
            _integer(getattr(self, name), name)
        if _time(self.valid_until) <= _time(self.available_at):
            raise ValueError("position sizing policy validity must follow availability")


def _affordable_buy_quantity(price, maximum, cash, cost):
    """Use the existing rounded paper reservation cost, including fees/slippage."""
    low, high = 0, maximum
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_buy_cash_won(price, middle, cost) <= cash:
            low = middle
        else:
            high = middle - 1
    return low


@dataclass(frozen=True)
class PositionTargetShadowRequest:
    account_ref: str
    environment: str
    venue: str
    symbol: str
    action: str
    target_quantity: int | None
    reference_price_won: int
    reference_price_available_at: datetime
    decision_time: datetime
    policy_version: str
    source_decision_ref: str
    account_input_ref: str
    orders_input_ref: str
    max_snapshot_age_seconds: int
    reconciliation_complete: bool
    open_orders_complete: bool
    cost_model: SimulationCostModel | None
    target_policy: PositionTargetSizingPolicy | None = None

    def __post_init__(self) -> None:
        for name in ("account_ref", "policy_version", "source_decision_ref", "account_input_ref", "orders_input_ref"):
            if type(getattr(self, name)) is not str or not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        _symbol(self.symbol)
        if self.environment != "mock" or self.venue != "KRX":
            raise ValueError("position target currently supports explicit mock/KRX input only")
        if self.action not in _ACTIONS:
            raise ValueError("unsupported position target action")
        if self.target_quantity is not None:
            _integer(self.target_quantity, "target_quantity")
        _integer(self.reference_price_won, "reference_price_won", 1)
        _integer(self.max_snapshot_age_seconds, "max_snapshot_age_seconds", 1)
        _time(self.reference_price_available_at)
        _time(self.decision_time)
        if type(self.reconciliation_complete) is not bool or type(self.open_orders_complete) is not bool:
            raise ValueError("account/order completeness must be explicit booleans")
        if self.cost_model is not None and not isinstance(self.cost_model, SimulationCostModel):
            raise ValueError("cost_model must use the registered research cost contract")
        if self.target_policy is not None:
            if not isinstance(self.target_policy, PositionTargetSizingPolicy):
                raise ValueError("target_policy must use the position sizing contract")
            if self.target_quantity is not None or self.action not in {"ADD", "REDUCE", "REENTER"}:
                raise ValueError("automatic sizing requires a null target and explicit ADD/REDUCE/REENTER action")


@dataclass(frozen=True)
class PositionActionStudyPolicy:
    """Stable, opt-in study semantics; a case's absolute lease is separate."""
    version: str
    source_policy_version: str
    sizing_version: str
    target_notional_won: int
    max_position_notional_won: int
    max_position_quantity: int
    cash_buffer_won: int
    actions: tuple[str, ...]
    horizon_seconds: int
    max_snapshot_age_seconds: int
    max_decision_age_seconds: int
    development_role: str
    population_rule: str = 'frozen-independent-paired-windows/v1'
    context_support_rule: str = 'exact-observed-context/v1'
    continuation_rule: str = 'single-target-then-registered-family/v1'
    value_basis: str = 'portfolio_change_after_slippage_before_fees/v1'

    def __post_init__(self):
        conditional=isinstance(self,PositionActionConditionalStudyPolicy)
        native=isinstance(self,PositionActionNativePolicyStudyPolicy)
        if (self.version != ('position-action-study/v4' if native else 'position-action-study/v3' if conditional else 'position-action-study/v2')
                or self.sizing_version != 'bounded-notional-sizing/v1'
                or type(self.source_policy_version) is not str or not 0 < len(self.source_policy_version) <= 256
                or type(self.actions) is not tuple or len(self.actions) != 2
                or tuple(sorted(set(self.actions))) != self.actions or 'HOLD' not in self.actions
                or not set(self.actions) <= {'ADD', 'REDUCE', 'REENTER', 'EXIT', 'HOLD'}
                or self.development_role not in {'TRAIN', 'VALIDATION'}
                or self.population_rule != 'frozen-independent-paired-windows/v1'
                or self.context_support_rule != ('held-minute-relative-state/v1' if conditional else 'exact-observed-context/v1')
                or self.continuation_rule != ('single-limit-intervention-then-native-policy/v1' if native else 'single-target-then-registered-family/v1')
                or self.value_basis != 'portfolio_change_after_slippage_before_fees/v1'):
            raise ValueError('invalid explicit position study policy')
        for name in ('target_notional_won','max_position_notional_won','max_position_quantity','cash_buffer_won'):
            _integer(getattr(self,name),name)
        for name in ('horizon_seconds','max_snapshot_age_seconds','max_decision_age_seconds'):
            _integer(getattr(self,name),name,1)
        if not 60 <= self.horizon_seconds <= 599940 or self.horizon_seconds % 60:
            raise ValueError('native position study requires a whole-minute horizon')

    def to_dict(self):
        return {**asdict(self), 'actions':list(self.actions)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value,dict) or not isinstance(value.get('actions'),list):
            raise ValueError('position study policy requires an actions array')
        if value.get('version') in {'position-action-study/v3','position-action-study/v4'}:
            try:
                raw={**value,'actions':tuple(value['actions']),
                    'support_spec':PositionStudySupportSpec.from_dict(value['support_spec'])}
                if value['version']=='position-action-study/v4':
                    raw['native_policy']=NativePositionStudyPolicy.from_dict(value['native_policy'])
                    return PositionActionNativePolicyStudyPolicy(**raw)
                return PositionActionConditionalStudyPolicy(**raw)
            except (KeyError,TypeError) as error:
                raise ValueError('invalid conditional study fields') from error
        return cls(**{**value,'actions':tuple(value['actions'])})


@dataclass(frozen=True)
class PositionStudySupportSpec:
    """One bounded experimental Paper model; no native execution claim."""
    version: str
    state_representation: str
    required_data_capabilities: tuple[str,...]
    minimum_pairs: int
    minimum_trading_days: int
    uncertainty_method: str

    def __post_init__(self):
        if (self.version!='conditional-paper-support/v1'
                or self.state_representation!='held-minute-relative-state/v1'
                or self.required_data_capabilities!=('sampled_price',)
                or type(self.required_data_capabilities) is not tuple
                or type(self.minimum_pairs) is not int or not 2<=self.minimum_pairs<=100
                or type(self.minimum_trading_days) is not int or not 2<=self.minimum_trading_days<=self.minimum_pairs
                or self.uncertainty_method!='paired-trading-day-cluster-standard-error/v1'):
            raise ValueError('invalid explicit conditional Paper support spec')

    def to_dict(self):
        return {**asdict(self),'required_data_capabilities':list(self.required_data_capabilities)}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,dict) or not isinstance(value.get('required_data_capabilities'),list):
            raise ValueError('conditional support requires a capabilities array')
        return cls(**{**value,'required_data_capabilities':tuple(value['required_data_capabilities'])})


@dataclass(frozen=True)
class PositionActionConditionalStudyPolicy(PositionActionStudyPolicy):
    support_spec: PositionStudySupportSpec | None = None

    def __post_init__(self):
        super().__post_init__()
        if self.actions!=('HOLD','REDUCE') or not isinstance(self.support_spec,PositionStudySupportSpec):
            raise ValueError('conditional Paper study supports explicit HOLD/REDUCE only')

    def to_dict(self):
        return {**super().to_dict(),'support_spec':self.support_spec.to_dict()}


def native_study_policy_implementation_ref():
    """Offline/registration identity; never read policy files on a live tick."""
    from pathlib import Path
    from kiwoom_monitor.application.research_implementation import research_implementation_hash
    root=Path(__file__).resolve().parents[3]
    paths=('application/mock_automation_risk.py','application/order_lifecycle.py',
        'application/mock_automation_session_clock.py','domain/order_contract.py','domain/execution_activation.py')
    digest=hashlib.sha256(research_implementation_hash('krx-regular/v1').encode())
    for name in paths:
        digest.update(name.encode());digest.update(b'\0')
        digest.update((root/'src/kiwoom_monitor'/name).read_text(encoding='utf-8').replace('\r\n','\n').encode())
        digest.update(b'\0')
    return 'native-study-policy-implementation:'+digest.hexdigest()


@dataclass(frozen=True)
class NativePositionStudyPolicy:
    version: str
    maximum_daily_loss_won: int
    maximum_data_gap_seconds: int
    maximum_submission_unknown_count: int
    maximum_reconnect_count: int
    maximum_balance_mismatch_count: int
    unfilled_policy: Any
    implementation_ref: str

    def __post_init__(self):
        from kiwoom_monitor.domain.execution_activation import MockUnfilledExecutionPolicy
        for name in ('maximum_daily_loss_won','maximum_data_gap_seconds'):_integer(getattr(self,name),name,1)
        for name in ('maximum_submission_unknown_count','maximum_reconnect_count','maximum_balance_mismatch_count'):
            _integer(getattr(self,name),name)
        if (self.version!='native-policy-paper/v1' or not isinstance(self.unfilled_policy,MockUnfilledExecutionPolicy)
                or self.unfilled_policy.allow_supplied_paired_mock_evidence
                or self.unfilled_policy.maximum_protective_cancel_retries
                or self.implementation_ref!=native_study_policy_implementation_ref()):
            raise ValueError('unsupported native policy Paper profile or implementation')

    def to_dict(self):
        return {**asdict(self),'unfilled_policy':self.unfilled_policy.to_dict()}

    @classmethod
    def from_dict(cls,value):
        from kiwoom_monitor.domain.execution_activation import MockUnfilledExecutionPolicy
        try:return cls(**{**value,'unfilled_policy':MockUnfilledExecutionPolicy(**value['unfilled_policy'])})
        except (TypeError,KeyError) as error:raise ValueError('invalid native study profile') from error


@dataclass(frozen=True)
class PositionActionNativePolicyStudyPolicy(PositionActionConditionalStudyPolicy):
    native_policy: NativePositionStudyPolicy | None = None

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.native_policy,NativePositionStudyPolicy) or self.horizon_seconds>3600:
            raise ValueError('native study requires an explicit bounded policy')

    def to_dict(self):
        return {**super().to_dict(),'native_policy':self.native_policy.to_dict()}


def position_study_support_observation(study,request,goal,source_bar,strategy_state,portfolio):
    """Recompute the fixed cell from available initial state, never outcomes."""
    from zoneinfo import ZoneInfo
    from kiwoom_monitor.application.breakout_strategy import StrategyState
    from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
    from kiwoom_monitor.application.research_execution import SimulationPortfolio,SimulationPosition
    if not isinstance(study,PositionActionConditionalStudyPolicy):
        raise ValueError('conditional support requires a v3 study')
    state=StrategyState(**{**strategy_state,'emitted_candidate_keys':tuple(strategy_state['emitted_candidate_keys'])})
    bar=KrxMinuteBarFrame(**source_bar)
    position=SimulationPosition(**portfolio['position']) if portfolio.get('position') is not None else None
    owned=SimulationPortfolio(**{**portfolio,'position':position})
    def aware(value):return _time(datetime.fromisoformat(value) if isinstance(value,str) else value)
    cut=aware(request.decision_time);start=aware(bar.bar_start);end=aware(bar.bar_end);at=aware(bar.available_at)
    if (state.status!='open' or position is None or portfolio['pending_order'] is not None
            or owned.reserved_cash_won or position.symbol!=request.symbol or state.symbol!=request.symbol
            or state.position_quantity!=position.quantity or state.entry_price!=position.entry_price
            or state.opened_at!=position.opened_at or aware(state.opened_at)>cut
            or request.action!='REDUCE' or goal.get('quality',{}).get('status')!='CALCULATED'
            or goal.get('actual_quantity')!=position.quantity or goal.get('funding_shortfall_quantity')
            or goal.get('pending_buy_quantity') or goal.get('pending_sell_quantity')
            or not 0<goal['target_quantity']<position.quantity
            or bar.code!=request.symbol or bar.close!=request.reference_price_won
            or at!=aware(request.reference_price_available_at) or not end<=at<=cut
            or not 0<=(cut-at).total_seconds()<study.max_snapshot_age_seconds
            or (end-start).total_seconds()!=60 or bar.capture_quality!='complete'
            or bar.session_profile!='krx-regular/v1' or bar.market_phase!='CONTINUOUS'
            or not bar.research_session or not bar.revision_id
            or cut.microsecond or start.second or start.microsecond or end.second or end.microsecond
            or not (at-end).total_seconds().is_integer() or not (cut-at).total_seconds().is_integer()
            or any(type(v) is not int or v<0 for v in (owned.cash_won,owned.total_cost_won,
                position.quantity,position.entry_price,position.entry_notional,position.entry_commission,
                bar.open,bar.high,bar.low,bar.close,bar.volume,bar.trade_value_million_won))
            or min(bar.open,bar.high,bar.low,bar.close)<=0
            or not bar.low<=min(bar.open,bar.close)<=max(bar.open,bar.close)<=bar.high):
        raise ValueError('conditional support initial state or closed-minute observation invalid')
    local=cut.astimezone(ZoneInfo('Asia/Seoul'))
    if bar.research_session!=local.date().isoformat()+':KRX_REGULAR':
        raise ValueError('conditional support research session mismatch')
    relative=state.to_dict()
    for name in ('opened_at','cooldown_until','candidate_expires_at'):
        value=relative[name]
        if value not in (None,''):
            seconds=(aware(value)-cut).total_seconds()
            if not seconds.is_integer():raise ValueError('conditional state requires whole-second offsets')
            relative[name]=int(seconds)
    economic_position={k:v for k,v in portfolio['position'].items() if k not in {'opened_at','entry_intent_id'}}
    cell={'state_representation':study.support_spec.state_representation,'strategy_state':relative,
        'symbol':request.symbol,'venue':request.venue,'source_policy_version':study.source_policy_version,
        'horizon_seconds':study.horizon_seconds,'initial_quantity':position.quantity,
        'target_quantity':goal['target_quantity'],'reference_price_won':request.reference_price_won,
        'cost_model':request.cost_model.to_dict(),'session_profile':bar.session_profile,
        'session_cut_seconds':local.hour*3600+local.minute*60+local.second,
        'observation_age_seconds':int((cut-at).total_seconds()),
        'bar_availability_lag_seconds':int((at-end).total_seconds()),
        'market_session':bar.market_session,'market_phase':bar.market_phase,'schedule_version':bar.schedule_version,
        'observed_values':{key:getattr(bar,key) for key in ('open','high','low','close','volume','trade_value_million_won')},
        'portfolio':{**{k:v for k,v in portfolio.items() if k not in {'position','pending_order'}},'position':economic_position}}
    if isinstance(study,PositionActionNativePolicyStudyPolicy):cell['native_policy']=study.native_policy.to_dict()
    cell=json.loads(json.dumps(cell,default=_json_value,allow_nan=False))
    encoded=json.dumps(cell,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    return {'version':'position-study-support-observation/v1','cell':cell,
        'cell_ref':'position-study-support-cell:'+hashlib.sha256(encoded.encode()).hexdigest(),
        'source_revision_id':bar.revision_id,'source_available_at':bar.available_at,
        'decision_time':cut.isoformat(),'trading_date':local.date().isoformat(),
        'absolute_strategy_state':json.loads(json.dumps(strategy_state,default=_json_value)),
        'input_resolution':'minute','source_venue':'KRX','source_durability':'not_verified'}


@dataclass(frozen=True)
class PositionActionStudyCase:
    """One frozen historical branch; HOLD records a goal without submitting it."""
    case_ref: str
    branch_action: str
    request: PositionTargetShadowRequest

    def __post_init__(self):
        if (type(self.case_ref) is not str or not 0 < len(self.case_ref) <= 256
                or self.branch_action not in {'ADD','REDUCE','REENTER','EXIT','HOLD'}
                or not isinstance(self.request,PositionTargetShadowRequest)):
            raise ValueError('invalid native position study case')

    def to_dict(self):
        return json.loads(json.dumps({'case_ref':self.case_ref,'branch_action':self.branch_action,
            'request':asdict(self.request)},default=_json_value))

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value,dict) or set(value) != {'case_ref','branch_action','request'}:
            raise ValueError('invalid position study case document')
        return cls(value['case_ref'],value['branch_action'],_position_target_request_from_dict(value['request']))


def validate_position_action_study_case(study, case):
    if not isinstance(study,PositionActionStudyPolicy) or not isinstance(case,PositionActionStudyCase):
        raise ValueError('typed native study and case required')
    request=case.request;policy=request.target_policy
    if (policy is None or request.action == 'HOLD' or request.action not in study.actions
            or case.branch_action not in {'HOLD',request.action}
            or request.account_ref != 'research-single-position' or request.environment != 'mock' or request.venue != 'KRX'
            or request.policy_version != study.source_policy_version
            or request.max_snapshot_age_seconds != study.max_snapshot_age_seconds
            or request.target_quantity is not None or request.cost_model is None
            or not request.reconciliation_complete or not request.open_orders_complete
            or tuple(getattr(policy,k) for k in ('version','target_notional_won','max_position_notional_won','max_position_quantity','cash_buffer_won'))
                != tuple(getattr(study,k) for k in ('sizing_version','target_notional_won','max_position_notional_won','max_position_quantity','cash_buffer_won'))):
        raise ValueError('position study goal/sizing scope mismatch')
    cut=_time(request.decision_time);available=_time(policy.available_at);expires=_time(policy.valid_until)
    if (not available <= cut < expires
            or (expires-available).total_seconds() > study.max_decision_age_seconds
            or not 0 <= (cut-_time(request.reference_price_available_at)).total_seconds() < study.max_snapshot_age_seconds):
        raise ValueError('position study original decision lease is invalid')


def position_action_study_spec(study, case, source_spec):
    """Bind semantics to the actual scientific implementation/family/execution."""
    validate_position_action_study_case(study,case)
    from kiwoom_monitor.application.research_families import parse_strategy_config
    from kiwoom_monitor.application.research_execution import SimulationExecutionConfig
    from kiwoom_monitor.application.market_session_schedule import research_session_profile_document,KRX_REGULAR_RESEARCH_PROFILE
    required=('code_hash','family','policy_version','parameters','execution_model','session_profile')
    if study.version=='position-action-study/v4':required+=('native_study_inputs_ref',)
    if (any(k not in source_spec for k in required) or type(source_spec['code_hash']) is not str
            or len(source_spec['code_hash']) != 64
            or any(c not in '0123456789abcdef' for c in source_spec['code_hash'])
            or source_spec['session_profile'] != research_session_profile_document(KRX_REGULAR_RESEARCH_PROFILE)):
        raise ValueError('position study requires an explicit scientific KRX source')
    parse_strategy_config(source_spec['family'],source_spec['parameters'])
    execution=dict(source_spec['execution_model'])
    from kiwoom_monitor.application.research_execution import SimulationCostModel
    execution['cost_model']=SimulationCostModel(**execution['cost_model']) if execution.get('cost_model') else None
    model=SimulationExecutionConfig(**execution)
    if model.cost_model != case.request.cost_model:
        raise ValueError('position study native cost mismatch')
    semantics={'policy':study.to_dict(),**{k:source_spec[k] for k in required}}
    encoded=json.dumps(semantics,default=_json_value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    if len(encoded.encode()) > 64*1024:raise ValueError('position study semantics exceed 64 KiB')
    result={'version':study.version,'policy':study.to_dict(),'case':case.to_dict(),
        'study_policy_ref':'position-action-study:'+hashlib.sha256(encoded.encode()).hexdigest()}
    if len(json.dumps(result,ensure_ascii=False).encode()) > 256*1024:
        raise ValueError('position study case exceeds 256 KiB')
    return result


def freeze_position_action_study_cohort(study,cases,source_spec,manifest,*,minimum_samples,
        max_evidence_age_seconds,model_valid_until):
    """Declare every paired case before native branch execution; no outcome input."""
    if (not isinstance(study,PositionActionStudyPolicy) or not isinstance(cases,tuple)
            or not 2 <= len(cases) <= 100 or type(minimum_samples) is not int
            or not 2 <= minimum_samples <= len(cases)
            or type(max_evidence_age_seconds) is not int or max_evidence_age_seconds < 1):
        raise ValueError('bounded explicit paired study cohort required')
    if (manifest.get('runtime_input_version') != 'independent_development_input/v1'
            or not isinstance(manifest.get('development_partition'),dict)):
        raise ValueError('study cohort requires prepared independent development input')
    from kiwoom_monitor.application.research_splits import DevelopmentPartitionSpec,ResearchEvaluationSpec
    descriptor=manifest['development_partition']
    fold=DevelopmentPartitionSpec.from_dict(descriptor['spec']).evaluation_for(
        ResearchEvaluationSpec.from_dict(descriptor['evaluation'])).folds[0]
    if fold.role != study.development_role:raise ValueError('study cohort role mismatch')
    expires=_time(model_valid_until);refs=set();shared_ref=None;common=None;previous_end=None
    rows=[]
    for case in cases:
        validate_position_action_study_case(study,case)
        if case.branch_action != case.request.action or case.case_ref in refs:
            raise ValueError('cohort must declare unique goal cases, not branch duplicates')
        refs.add(case.case_ref)
        reference=position_action_study_spec(study,case,source_spec)['study_policy_ref']
        scope=(case.request.symbol,case.request.reference_price_won,case.request.action)
        if shared_ref is not None and (reference != shared_ref or scope != common):
            raise ValueError('study cohort model/symbol/price/action mismatch')
        shared_ref=reference;common=scope
        start=_time(case.request.decision_time).replace(second=0,microsecond=0)
        end=start+timedelta(seconds=study.horizon_seconds)
        if (not _time(datetime.fromisoformat(fold.start)) <= start < end <= _time(datetime.fromisoformat(fold.end))
                or end >= expires or (previous_end is not None and start <= previous_end)):
            raise ValueError('study cases must be ordered nonoverlapping development windows')
        previous_end=end;rows.append(case.to_dict())
    if isinstance(study,PositionActionConditionalStudyPolicy) and minimum_samples!=study.support_spec.minimum_pairs:
        raise ValueError('cohort minimum must match declared conditional support')
    body={'version':'position-action-study-cohort/'+study.version.rsplit('/',1)[1],'study_policy_ref':shared_ref,
        'dataset_id':manifest['dataset_id'],'revision_ids_hash':manifest['revision_ids_hash'],
        'development_role':study.development_role,'cases':rows,'minimum_samples':minimum_samples,
        'max_evidence_age_seconds':max_evidence_age_seconds,'model_valid_until':expires.isoformat()}
    encoded=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    if len(encoded.encode()) > 256*1024:raise ValueError('study cohort declaration exceeds 256 KiB')
    return {**body,'cohort_ref':'position-action-study-cohort:'+hashlib.sha256(encoded.encode()).hexdigest()}


def validate_position_action_study_cohort(document,study,case,source_spec,manifest):
    keys={'version','study_policy_ref','dataset_id','revision_ids_hash','development_role','cases',
          'minimum_samples','max_evidence_age_seconds','model_valid_until','cohort_ref'}
    if (not isinstance(document,dict) or set(document) != keys or not isinstance(document.get('cases'),list)
            or len(json.dumps(document,allow_nan=False).encode()) > 256*1024):
        raise ValueError('invalid native study cohort declaration')
    cases=tuple(PositionActionStudyCase.from_dict(r) for r in document['cases'])
    frozen=freeze_position_action_study_cohort(study,cases,source_spec,manifest,
        minimum_samples=document['minimum_samples'],max_evidence_age_seconds=document['max_evidence_age_seconds'],
        model_valid_until=datetime.fromisoformat(document['model_valid_until']))
    goal=replace(case,branch_action=case.request.action)
    if document != frozen or goal not in cases:
        raise ValueError('native branch is absent from its frozen study cohort')
    return frozen


def evaluate_position_target_shadow(
    request: PositionTargetShadowRequest,
    account: AccountSnapshot,
    open_orders: tuple[ExecutionRecord, ...],
    *, broker_open_orders: tuple[PositionBrokerOrderExposure, ...] | None = None,
) -> dict[str, Any]:
    """Compare an explicit policy target with actual holdings and remaining orders.

    The caller supplies the existing reconciled account-wide ledger projection.
    Completeness flags describe that supplied cut, never grant execution approval.
    Filled quantities already belong to account.positions and are not added again.
    """
    cutoff = _time(request.decision_time)
    if account.account_ref != request.account_ref:
        raise ValueError("position target account mismatch")
    _integer(account.available_cash_won, "available_cash_won")
    _integer(account.reserved_open_buy_won, "reserved_open_buy_won")
    positions = dict(account.positions)
    for symbol, quantity in positions.items():
        _symbol(symbol)
        _integer(quantity, "position quantity")
    if len(open_orders) > 400:
        raise ValueError("position target accepts at most 400 account order records")

    reasons: list[str] = []
    def check_cut(value: datetime, label: str) -> None:
        age = (cutoff - _time(value)).total_seconds()
        if age < 0:
            reasons.append(f"{label}_from_future")
        elif age > request.max_snapshot_age_seconds:
            reasons.append(f"{label}_stale")

    check_cut(account.as_of, "account")
    check_cut(request.reference_price_available_at, "reference_price")
    if not request.reconciliation_complete:
        reasons.append("account_reconciliation_incomplete")
    if not request.open_orders_complete:
        reasons.append("account_open_orders_incomplete")
    free_cash = max(0, account.available_cash_won - account.reserved_open_buy_won)
    if account.reserved_open_buy_won > account.available_cash_won:
        reasons.append("reservation_exceeds_cash")

    seen_intents: set[str] = set()
    seen_broker_ids: set[str] = set()
    pending_buy = pending_sell = 0
    minimum_buy_reservation = 0
    record_inputs = []
    for record in open_orders:
        intent = record.intent
        if intent.account_ref != account.account_ref or intent.environment != "mock" or intent.venue != "KRX":
            raise ValueError("position target order account/environment/venue mismatch")
        _symbol(intent.symbol)
        _integer(intent.quantity, "intent quantity", 1)
        for name in ("filled_quantity", "detailed_filled_quantity", "broker_reported_filled_quantity"):
            _integer(getattr(record, name), name)
        if max(record.filled_quantity, record.detailed_filled_quantity,
               record.broker_reported_filled_quantity) > intent.quantity:
            raise ValueError("order fills exceed requested quantity")
        if not isinstance(record.state, OrderState) or not isinstance(intent.side, OrderSide):
            raise ValueError("order state/side must use the existing order contract")
        if intent.intent_id in seen_intents or (broker_open_orders is None and record.broker_order_id and record.broker_order_id in seen_broker_ids):
            raise ValueError("duplicate account order identity")
        seen_intents.add(intent.intent_id)
        if record.broker_order_id:
            seen_broker_ids.add(record.broker_order_id)
        record_inputs.append(asdict(record))
        if broker_open_orders is not None:
            if record.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}:
                reasons.append("unconfirmed_or_cancel_pending_order")
            if record.broker_order_id not in {value.broker_order_id for value in broker_open_orders}:
                # A confirmed terminal broker row can close exposure without changing this local ledger.
                if request.open_orders_complete is not True:
                    reasons.append("local_active_order_not_confirmed_by_broker")
            continue
        latest_projection = _time(record.updated_at)
        if latest_projection > cutoff:
            reasons.append("order_from_future")
        if record.last_broker_as_of is not None:
            latest_projection = max(latest_projection, _time(record.last_broker_as_of))
        if latest_projection > _time(account.as_of):
            reasons.append("account_predates_order_projection")
        if record.filled_quantity != record.detailed_filled_quantity or (
            record.broker_reported_filled_quantity > record.detailed_filled_quantity
        ):
            reasons.append("order_fill_reconciliation_incomplete")
        # Terminal orders have no remaining exposure, including cancelled remainders.
        if record.state in TERMINAL_ORDER_STATES:
            continue
        if _time(intent.created_at) > cutoff:
            reasons.append("intent_from_future")
        # State may be unchanged for minutes while fresh reconciliation confirms
        # the order. Its broker cut, rather than the last state change, ages it.
        check_cut(record.last_broker_as_of or record.updated_at, "broker_order")
        if record.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED} or not record.broker_order_id:
            reasons.append("unconfirmed_or_cancel_pending_order")
            continue
        remaining = intent.quantity - record.filled_quantity
        if remaining <= 0:
            reasons.append("nonterminal_order_without_remaining_quantity")
        if intent.side is OrderSide.BUY:
            if intent.limit_price is None:
                reasons.append("open_buy_reservation_unverified")
            else:
                _integer(intent.limit_price, "open buy limit_price", 1)
                minimum_buy_reservation += remaining * intent.limit_price
        if intent.symbol == request.symbol:
            if intent.side is OrderSide.BUY:
                pending_buy += remaining
            else:
                pending_sell += remaining

    native_inputs = []
    if broker_open_orders is not None:
        if len(broker_open_orders) > 400: raise ValueError("broker order observation exceeds 400 rows")
        native_ids = set()
        for value in broker_open_orders:
            if value.account_ref != account.account_ref or value.venue != "KRX":
                reasons.append("broker_order_scope_incomplete")
            _symbol(value.symbol)
            if value.broker_order_id in native_ids: raise ValueError("duplicate broker exposure")
            native_ids.add(value.broker_order_id)
            for name in ("ordered_quantity", "filled_quantity", "remaining_quantity"):
                _integer(getattr(value, name), name)
            native_inputs.append(asdict(value))
            check_cut(value.as_of, "broker_order")
            if _time(value.as_of) > _time(account.as_of): reasons.append("account_predates_order_projection")
            if (not isinstance(value.state, OrderState) or not isinstance(value.side, OrderSide)
                    or value.state not in {OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED}
                    or not value.remaining_quantity or value.filled_quantity + value.remaining_quantity > value.ordered_quantity):
                reasons.append("broker_open_order_basis_incomplete")
                continue
            if value.side is OrderSide.BUY:
                if value.limit_price is None: reasons.append("open_buy_reservation_unverified")
                else:
                    _integer(value.limit_price, "broker limit price", 1)
                    minimum_buy_reservation += value.remaining_quantity * value.limit_price
            if value.symbol == request.symbol:
                if value.side is OrderSide.BUY: pending_buy += value.remaining_quantity
                elif value.side is OrderSide.SELL: pending_sell += value.remaining_quantity
    actual = positions.get(request.symbol, 0)
    if minimum_buy_reservation > account.reserved_open_buy_won:
        reasons.append("open_buy_reservation_incomplete")
    if pending_sell > actual:
        reasons.append("pending_sell_exceeds_actual_position")
    projection_reasons = tuple(sorted(set(reasons)))
    projected = None if projection_reasons else actual + pending_buy - pending_sell
    target = request.target_quantity
    sizing = None
    policy_version = request.policy_version
    if request.target_policy is not None:
        policy = request.target_policy
        policy_inputs = {"source_policy_version": request.policy_version, "parameters": asdict(policy)}
        digest = hashlib.sha256(json.dumps(policy_inputs, default=_json_value, sort_keys=True,
                                          separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        policy_version = "position-sizing-policy:" + digest
        sizing = {"policy_ref": policy_version, "source_policy_version": request.policy_version,
                  "parameters": asdict(policy), "desired_quantity": None, "limits": [],
                  "status": "BLOCKED"}
        if _time(policy.available_at) > cutoff:
            reasons.append("sizing_policy_from_future")
        if _time(policy.valid_until) <= cutoff:
            reasons.append("sizing_policy_expired")
        if not reasons:
            nominal = policy.target_notional_won // request.reference_price_won
            target = min(nominal, policy.max_position_notional_won // request.reference_price_won,
                         policy.max_position_quantity)
            sizing['desired_quantity'] = nominal
            if target < nominal: sizing['limits'].append('position_limit')
            if request.action in {"ADD", "REENTER"} and target > projected:
                cost = request.cost_model
                if cost is None:
                    reasons.append("buy_cost_model_unavailable")
                elif cost.valid_from and not (_time(datetime.fromisoformat(cost.valid_from)) <= cutoff
                                              < _time(datetime.fromisoformat(cost.valid_to))):
                    reasons.append("buy_cost_model_outside_validity")
                else:
                    budget = max(0, free_cash - policy.cash_buffer_won)
                    affordable = _affordable_buy_quantity(request.reference_price_won, target - projected, budget, cost)
                    if affordable < target - projected: sizing['limits'].append('cash_after_reservations_and_buffer')
                    target = projected + affordable
            if reasons: target = None
    delta = None if projected is None or target is None else target - projected
    action = request.action
    if target is None:
        reasons.append("target_quantity_unknown")
    elif ((action == "ADD" and not (actual > 0 and target > actual))
          or (action == "REENTER" and not (actual == 0 and target > 0))
          or (action == "REDUCE" and not (0 < target < actual))
          or (action == "EXIT" and target != 0)
          or (action == "HOLD" and target != actual)
          or action == "WAIT"):
        reasons.append("action_target_mismatch")
    if delta is not None and (
        (action in {"ADD", "REENTER"} and (pending_sell or delta < 0))
        or (action in {"REDUCE", "EXIT"} and (pending_buy or delta > 0))
        or (action in {"HOLD", "WAIT"} and delta != 0)
    ):
        reasons.append("existing_order_conflicts_with_target")
    if delta is not None and delta > 0:
        cost = request.cost_model
        if cost is None:
            reasons.append("buy_cost_model_unavailable")
        elif cost.valid_from and not (_time(datetime.fromisoformat(cost.valid_from)) <= cutoff
                                      < _time(datetime.fromisoformat(cost.valid_to))):
            reasons.append("buy_cost_model_outside_validity")

    incremental = None
    side = None
    required_cash = funded_cash = None
    shortfall = None
    if not reasons and delta is not None:
        incremental = abs(delta)
        shortfall = 0
        if delta > 0:
            side = "BUY"
            required_cash = estimate_buy_cash_won(request.reference_price_won, delta, request.cost_model)
            # Search the same rounded reservation function used by paper execution.
            low = _affordable_buy_quantity(request.reference_price_won, delta, free_cash, request.cost_model)
            incremental = low
            shortfall = delta - low
            funded_cash = estimate_buy_cash_won(request.reference_price_won, low, request.cost_model)
        elif delta < 0:
            side = "SELL"
            # Pending sells are already subtracted; their shares cannot be sold twice.
            incremental = min(-delta, actual - pending_sell)

    body = {
        "policy_version": POSITION_TARGET_SHADOW_VERSION,
        "target_policy_version": policy_version,
        "account_ref": account.account_ref, "symbol": request.symbol,
        "scope": {"environment": request.environment, "venue": request.venue, "quantity_basis": "account_shares",
                  "source_durability": "not_verified"},
        "source_decision_ref": request.source_decision_ref,
        "input_refs": (request.account_input_ref, request.orders_input_ref),
        "decision_time": cutoff.isoformat(), "action": action,
        "actual_quantity": actual, "pending_buy_quantity": pending_buy,
        "pending_sell_quantity": pending_sell, "projected_quantity": projected,
        "target_quantity": target, "target_notional_won": (
            target * request.reference_price_won if target is not None else None),
        "remaining_target_delta": delta, "incremental_side": side,
        "incremental_quantity": incremental, "funding_shortfall_quantity": shortfall,
        "cash_after_reservations_won": free_cash, "required_buy_cash_won": required_cash,
        "funded_buy_cash_won": funded_cash,
        "quality": {"status": "BLOCKED" if reasons else "CALCULATED",
                    "reasons": tuple(sorted(set(reasons))),
                    "projection_reasons": projection_reasons},
        "cost_model": request.cost_model.to_dict() if request.cost_model else None,
        "ev_status": "EV_UNAVAILABLE", "execution_support": "shadow_only",
        "order_authorized": False,
    }
    request_identity = asdict(request)
    if sizing is None:
        request_identity.pop('target_policy')  # Preserve existing v1 scientific IDs exactly.
    else:
        sizing['status'] = body['quality']['status']
        body['target_sizing'] = json.loads(json.dumps(sizing, default=_json_value))
    identity = {"request": request_identity, "account": asdict(account),
                "orders": sorted(record_inputs, key=lambda value: value["intent"]["intent_id"]),
                "result": body}
    if broker_open_orders is not None:
        body["scope"]["pending_quantity_basis"] = "broker_remaining_quantity/v1"
        identity["broker_open_orders"] = sorted(native_inputs, key=lambda value: value["broker_order_id"])
    digest = hashlib.sha256(json.dumps(identity, default=_json_value, sort_keys=True,
                                       separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"decision_id": f"position-target-shadow:{digest}", **body}


def _json_value(value: Any) -> str:
    if isinstance(value, datetime):
        return _time(value).isoformat()
    # SimulationCostModel v2 holds canonical Decimal rates, not floats.
    from decimal import Decimal
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported position target input: {type(value).__name__}")


@dataclass(frozen=True)
class PositionActionSelectionPolicy:
    """Explicit research constraints, never a native Risk approval or allocation policy."""
    version: str
    minimum_net_advantage_won: int
    uncertainty_multiplier: float
    risk_constraint: str
    risk_input_ref: str
    account_ref: str
    symbol: str
    account_input_ref: str
    orders_input_ref: str
    available_at: datetime
    valid_until: datetime
    input_mode: str = 'supplied_fixed'
    thesis_ref: str = ''

    def __post_init__(self) -> None:
        capability_policy=isinstance(self,PositionActionCapabilitySelectionPolicy)
        if self.version != ('paired-net-action-selection/v2' if capability_policy else 'paired-net-action-selection/v1'):
            raise ValueError('unsupported position action selection policy')
        _integer(self.minimum_net_advantage_won, 'minimum_net_advantage_won', 0)
        try:
            multiplier_valid = (type(self.uncertainty_multiplier) in (int, float)
                and math.isfinite(self.uncertainty_multiplier) and self.uncertainty_multiplier >= 0)
        except OverflowError:
            multiplier_valid = False
        if not multiplier_valid:
            raise ValueError('uncertainty_multiplier must be finite and non-negative')
        if self.risk_constraint not in {'ALLOW', 'NO_NEW_RISK', 'REDUCE_ONLY', 'EXIT_REQUIRED', 'HALT_NEW_ORDERS'}:
            raise ValueError('unsupported supplied risk constraint')
        for name in ('risk_input_ref', 'account_ref', 'symbol', 'account_input_ref', 'orders_input_ref'):
            if type(getattr(self, name)) is not str or not getattr(self, name).strip():
                raise ValueError(f'{name} must be explicit')
        if _time(self.valid_until) <= _time(self.available_at):
            raise ValueError('selection policy validity must be positive')
        if self.input_mode not in ({'owner_ram_risk'} if capability_policy else {'supplied_fixed', 'owner_ram_risk_and_thesis'}):
            raise ValueError('unsupported selection input mode')
        if type(self.thesis_ref) is not str or (self.input_mode == 'owner_ram_risk_and_thesis' and not self.thesis_ref.strip()):
            raise ValueError('native selection requires an explicit thesis_ref')
        if self.input_mode == 'supplied_fixed' and self.thesis_ref:
            raise ValueError('fixed selection cannot claim a native thesis binding')


@dataclass(frozen=True)
class PositionActionCapabilitySelectionPolicy(PositionActionSelectionPolicy):
    """Opt-in v2 strategy requirements; account/Risk/funding gates remain mandatory."""
    required_data_capabilities: tuple[str, ...] | None = None
    action_required_data_capabilities: tuple[tuple[str, tuple[str, ...]], ...] | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        capabilities=self.required_data_capabilities
        allowed={'sampled_price','stock_thesis','theme_context','news_context','turnover_delta'}
        if (type(capabilities) is not tuple or len(capabilities)>len(allowed)
                or any(type(value) is not str or value not in allowed for value in capabilities)
                or tuple(sorted(set(capabilities)))!=capabilities):
            raise ValueError('explicit sorted unique required_data_capabilities are required')
        per_action=self.action_required_data_capabilities
        if per_action is not None:
            if (type(per_action) is not tuple or not 1<=len(per_action)<=5
                    or any(type(row) is not tuple or len(row)!=2 or type(row[0]) is not str or row[0] not in {'HOLD','ADD','REDUCE','REENTER','EXIT'}
                        or type(row[1]) is not tuple or len(row[1])>len(allowed)
                        or any(type(name) is not str or name not in allowed for name in row[1])
                        or tuple(sorted(set(row[1])))!=row[1] for row in per_action)
                    or tuple(sorted(set(row[0] for row in per_action)))!=tuple(row[0] for row in per_action)):
                raise ValueError('explicit sorted unique action data capabilities required')
        all_required=set(capabilities)|{name for _,values in per_action or () for name in values}
        if bool(self.thesis_ref.strip()) != ('stock_thesis' in all_required):
            raise ValueError('stock_thesis capability requires its explicit binding only')


@dataclass(frozen=True)
class PositionActionComparisonParameters:
    baseline_action: str
    horizon_seconds: int
    population_ref: str
    input_resolution: str
    outcome_source_kind: str
    execution_model_ref: str
    minimum_samples: int
    selection_policy: PositionActionSelectionPolicy | None = None

    def __post_init__(self) -> None:
        if self.baseline_action != "HOLD":
            raise ValueError("position action comparison currently requires an explicit HOLD baseline")
        _integer(self.horizon_seconds, "horizon_seconds", 1)
        if self.input_resolution not in {"minute", "second"}:
            raise ValueError("position action comparison resolution must be explicit")
        if self.input_resolution == "minute" and self.horizon_seconds % 60:
            raise ValueError("minute input cannot support a fractional-minute outcome horizon")
        if type(self.population_ref) is not str or not self.population_ref.strip():
            raise ValueError("position action comparison population must be explicit")
        if self.outcome_source_kind not in {"simulation", "broker", "shadow_valuation"}:
            raise ValueError("position action comparison source kind must be explicit")
        if type(self.execution_model_ref) is not str or not self.execution_model_ref.strip():
            raise ValueError("position action comparison execution model must be explicit")
        _integer(self.minimum_samples, "minimum_samples", 2)
        if self.minimum_samples > 10_000:
            raise ValueError("minimum_samples exceeds the outcome budget")
        if self.selection_policy is not None and not isinstance(self.selection_policy, PositionActionSelectionPolicy):
            raise ValueError('selection_policy must be a typed explicit policy')


def _select_position_action(policy, first, targets, rows, *, action_inputs=None):
    """Pure recommendation over existing paired observations; no I/O or execution permission."""
    result = {'version': policy.version, 'action': 'WAIT', 'status': 'BLOCKED', 'reasons': [],
        'scores': {}, 'risk_constraint': policy.risk_constraint, 'risk_input_ref': policy.risk_input_ref,
        'risk_path_required': False, 'target_decision_ref': None,
        'score_method': 'paired_net_advantage_minus_standard_error_penalty/v1',
        'risk_authority': 'supplied_research_constraint_only', 'order_authorized': False}
    if action_inputs is not None:
        if set(action_inputs)-set(targets):raise ValueError('action data requirement has no compared target')
        result['action_input_rejections']={action:list(value['reasons']) for action,value in action_inputs.items() if value['reasons']}
    cut = _time(first.decision_time)
    if (policy.account_ref, policy.symbol, policy.account_input_ref, policy.orders_input_ref) != (
            first.account_ref, first.symbol, first.account_input_ref, first.orders_input_ref):
        result['reasons'].append('selection_risk_input_cut_mismatch')
    if _time(policy.available_at) > cut:
        result['reasons'].append('selection_risk_input_from_future')
    if cut >= _time(policy.valid_until):
        result['reasons'].append('selection_risk_input_expired')
    if result['reasons']:
        return result
    # A hard protection declaration bypasses empirical EV. It requests the existing
    # Risk/Execution path, even when the target cannot safely supply a quantity.
    if policy.risk_constraint == 'EXIT_REQUIRED':
        target = targets.get('EXIT')
        result.update(action='EXIT', status='RISK_REQUIRED', risk_path_required=True,
            reasons=['protection_precedes_empirical_ev'],
            target_decision_ref=target['decision_id'] if target and target['quality']['status'] == 'CALCULATED' else None)
        return result
    if policy.risk_constraint == 'HALT_NEW_ORDERS':
        result['reasons'] = ['supplied_risk_halts_new_orders']
        return result
    if action_inputs and action_inputs.get('HOLD',{}).get('reasons'):
        result['reasons']=['hold_required_data_unavailable']
        return result
    baseline = next(row for row in rows if row['action'] == 'HOLD')
    if baseline['ev_status'] != 'EMPIRICAL_ESTIMATE':
        result['reasons'] = ['hold_evidence_unavailable']
        return result
    baseline_refs = set(baseline['evidence_refs'])
    paired_cohort = None
    candidates = []
    for row in rows:
        action = row['action']
        if action in {'HOLD', 'WAIT'}:
            continue
        target = targets[action]
        if action_inputs and action_inputs.get(action,{}).get('reasons'):continue
        if policy.risk_constraint in {'NO_NEW_RISK', 'REDUCE_ONLY'} and action in {'ADD', 'REENTER'}:
            continue
        candidates.append(action)
        if row['ev_status'] != 'EMPIRICAL_ESTIMATE' or target['incremental_quantity'] is None:
            result['reasons'].append(f'action_evidence_unavailable:{action}')
            continue
        cohort = baseline_refs.intersection(row['evidence_refs'])
        if paired_cohort is not None and cohort != paired_cohort:
            result['reasons'].append('candidate_paired_cohorts_differ')
        paired_cohort = cohort
        delta = row['paired_delta']
        score = delta['mean_net_change_won'] - policy.uncertainty_multiplier * delta['net_standard_error_won']
        if not math.isfinite(score):
            result['reasons'].append('selection_score_not_finite')
            continue
        result['scores'][action] = {'net_advantage_won': delta['mean_net_change_won'],
            'standard_error_won': delta['net_standard_error_won'], 'penalized_advantage_won': score,
            'paired_sample_count': row['paired_sample_count']}
    result['eligible_actions'] = candidates
    if not candidates and action_inputs and result['action_input_rejections']:
        result['reasons']=['action_required_data_unavailable']
        return result
    if result['reasons']:
        result['reasons'] = sorted(set(result['reasons']))
        return result
    # Strict improvement, including a strict zero boundary: an equal estimate does
    # not churn holdings. An equal best score has no justified unique action.
    improving = [(value['penalized_advantage_won'], action) for action, value in result['scores'].items()
                 if value['penalized_advantage_won'] > policy.minimum_net_advantage_won
                 and targets[action]['incremental_quantity'] > 0]
    if not improving:
        result.update(action='HOLD', status='SELECTED', reasons=['no_supported_net_improvement'],
                      target_decision_ref=targets['HOLD']['decision_id'])
        return result
    best = max(score for score, _ in improving)
    winners = [action for score, action in improving if score == best]
    if len(winners) != 1:
        result['reasons'] = ['best_action_score_tied']
        return result
    action = winners[0]
    result.update(action=action, status='SELECTED', reasons=['supported_net_improvement'],
                  target_decision_ref=targets[action]['decision_id'])
    return result


def _native_selection_inputs(policy, first, account, risk, thesis):
    """Bind already-published RAM evidence; never reads the Risk repository or dispatches."""
    from kiwoom_monitor.application.mock_automation_risk import (
        MockAutomationRiskSnapshot, VERIFIED_DAILY_PNL_SOURCE, mock_automation_risk_snapshot_from_dict,
    )
    cut = _time(first.decision_time)
    receipt = {'input_mode': policy.input_mode, 'risk_snapshot_ref': None, 'thesis_decision_ref': None,
        'risk_authority': 'native_observation_not_execution_approval', 'reasons': [], 'expires_at': None}
    blocked, restrict = [], []
    thesis_action = None
    capability_policy=isinstance(policy,PositionActionCapabilitySelectionPolicy)
    required=policy.required_data_capabilities if capability_policy else ('stock_thesis',)
    per_action=dict(policy.action_required_data_capabilities or ()) if capability_policy else {}
    all_required=set(required)|{name for values in per_action.values() for name in values}
    thesis_blocked=[];thesis_expires=None
    available_capabilities=set()
    if (type(first.reference_price_won) is int and first.reference_price_won>0
            and _time(first.reference_price_available_at)<=cut
            <_time(first.reference_price_available_at)+timedelta(seconds=first.max_snapshot_age_seconds)):
        available_capabilities.add('sampled_price')
    if 'stock_thesis' not in all_required:
        pass  # An unused context cannot constrain, invalidate or shorten this policy.
    elif not isinstance(thesis, dict):
        thesis_blocked.append('native_thesis_unavailable_or_ambiguous')
    else:
        try:
            if (thesis['policy_version'] != 'stock-thesis-shadow/v1' or thesis['order_authorized'] is not False
                    or thesis['thesis_ref'] != policy.thesis_ref or thesis['symbol'] != first.symbol
                    or thesis['thesis_venue'] != first.venue):
                raise ValueError('scope')
            available = _time(datetime.fromisoformat(thesis['available_at']))
            decided = _time(datetime.fromisoformat(thesis['decision_time']))
            expires = _time(datetime.fromisoformat(thesis['valid_until']))
            if available > cut or decided > cut or cut >= expires or (
                    cut - decided).total_seconds() > first.max_snapshot_age_seconds:
                raise ValueError('time')
            if thesis['quality']['status'] != 'COMPLETE':
                raise ValueError('quality')
            thesis_action = thesis['action']
            if thesis_action not in {'HOLD', 'WAIT', 'REDUCE', 'EXIT'}:
                raise ValueError('action')
            receipt.update(thesis_decision_ref=thesis['decision_id'], thesis_action=thesis_action)
            thesis_expires=expires
            if 'stock_thesis' in required:receipt['expires_at']=expires.isoformat()
            available_capabilities.add('stock_thesis')
        except (KeyError, TypeError, ValueError):
            thesis_blocked.append('native_thesis_scope_time_or_quality_invalid')
    if 'stock_thesis' in required:blocked.extend(thesis_blocked)
    risk_valid = False
    if not isinstance(risk, MockAutomationRiskSnapshot):
        blocked.append('native_risk_unavailable')
    else:
        try:
            mock_automation_risk_snapshot_from_dict(risk.to_dict())
            if risk.account_ref != first.account_ref or risk.account_as_of != account.as_of:
                raise ValueError('account cut')
            if risk.observed_at > cut or risk.account_as_of > risk.observed_at or risk.account_as_of > cut or (
                    cut - _time(risk.observed_at)).total_seconds() > first.max_snapshot_age_seconds:
                raise ValueError('time')
            risk_valid = True
            receipt.update(risk_snapshot_ref=risk.snapshot_id, account_revision=risk.account_revision,
                reconciliation_revision=risk.reconciliation_revision, risk_observed_at=_time(risk.observed_at).isoformat())
            if not risk.reconciliation_complete or risk.balance_mismatch_count or risk.submission_unknown_count:
                blocked.append('native_risk_reconciliation_or_order_unconfirmed')
            if not risk.cost_complete or risk.daily_net_pnl_won is None or risk.daily_net_pnl_source != VERIFIED_DAILY_PNL_SOURCE:
                restrict.append('native_risk_cost_or_daily_pnl_unverified')
        except (TypeError, ValueError):
            blocked.append('native_risk_scope_time_or_content_invalid')
    policy_valid = True
    if cut < _time(policy.available_at) or cut >= _time(policy.valid_until):
        blocked.append('native_selection_policy_outside_validity')
        thesis_action = None
        policy_valid = False
    if policy.account_ref != first.account_ref or policy.symbol != first.symbol:
        blocked.append('native_selection_policy_scope_mismatch')
        thesis_action = None
        policy_valid = False
    if capability_policy:
        missing=sorted(set(required)-available_capabilities)
        # Other registered declarations have no validated R4 producer yet.
        # They remain missing, rather than borrowing labels or inventing data.
        blocked.extend('required_data_capability_unavailable:'+name for name in missing)
        receipt.update(required_data_capabilities=list(required),
            available_data_capabilities=sorted(available_capabilities),missing_data_capabilities=missing)
        expires=[_time(policy.valid_until),
            _time(first.reference_price_available_at)+timedelta(seconds=first.max_snapshot_age_seconds)]
        if risk_valid:
            expires.extend(_time(at)+timedelta(seconds=first.max_snapshot_age_seconds)
                for at in (risk.observed_at,risk.account_as_of))
        if receipt['expires_at'] is not None:expires.append(_time(datetime.fromisoformat(receipt['expires_at'])))
        receipt['expires_at']=min(expires).isoformat()
        if policy.action_required_data_capabilities is not None:
            action_inputs={}
            for action,values in per_action.items():
                action_required=sorted(set(required)|set(values))
                missing=sorted(set(action_required)-available_capabilities)
                reasons=['required_data_capability_unavailable:'+name for name in missing]
                if 'stock_thesis' in action_required:
                    reasons.extend(thesis_blocked)
                    if thesis_action=='WAIT':reasons.append('native_thesis_wait')
                    if thesis_action in {'HOLD','REDUCE'} and action in {'ADD','REENTER'}:
                        reasons.append('native_thesis_disallows_new_risk')
                action_expiry=[_time(datetime.fromisoformat(receipt['expires_at']))]
                if 'stock_thesis' in action_required and thesis_expires is not None:action_expiry.append(thesis_expires)
                action_inputs[action]={'required_data_capabilities':action_required,'missing_data_capabilities':missing,
                    'reasons':sorted(set(reasons)),'valid_until':min(action_expiry).isoformat()}
            receipt['action_inputs']=action_inputs
    constraint = policy.risk_constraint
    # Existing R3 HOLD/WAIT/REDUCE supplies no ADD or REENTER admission.
    if thesis_action == 'WAIT' and 'stock_thesis' in required:
        blocked.append('native_thesis_wait')
    elif (thesis_action in {'HOLD', 'REDUCE'} and 'stock_thesis' in required) or restrict:
        if constraint == 'ALLOW':
            constraint = 'REDUCE_ONLY' if thesis_action == 'REDUCE' else 'NO_NEW_RISK'
    if thesis_action == 'EXIT':
        constraint = 'EXIT_REQUIRED'
    adapted = replace(policy, risk_constraint=constraint,
        risk_input_ref=risk.snapshot_id if risk_valid else policy.risk_input_ref,
        account_input_ref=first.account_input_ref, orders_input_ref=first.orders_input_ref)
    receipt['reasons'] = sorted(set(blocked + restrict))
    return adapted, receipt, blocked, policy_valid and (thesis_action == 'EXIT' or (risk_valid and constraint == 'EXIT_REQUIRED'))


def evaluate_position_action_comparison_shadow(
    parameters: PositionActionComparisonParameters,
    requests: tuple[PositionTargetShadowRequest, ...], account: AccountSnapshot,
    open_orders: tuple[ExecutionRecord, ...], outcomes: tuple,
    *, broker_open_orders: tuple[PositionBrokerOrderExposure, ...] | None = None,
    risk_snapshot=None, stock_thesis_result=None,
) -> dict[str, Any]:
    """Compare feasible targets using explicitly measured, paired action outcomes."""
    from kiwoom_monitor.application.research_evaluation import (
        compare_position_action_outcomes, position_action_cost_model_ref,
    )
    if not 2 <= len(requests) <= 5 or len({request.action for request in requests}) != len(requests):
        raise ValueError("position action comparison requires 2..5 distinct actions")
    if parameters.baseline_action not in {request.action for request in requests}:
        raise ValueError("position action comparison baseline target is missing")
    common_names = ("account_ref", "environment", "venue", "symbol", "reference_price_won",
                    "reference_price_available_at", "decision_time", "account_input_ref", "orders_input_ref",
                    "max_snapshot_age_seconds", "reconciliation_complete", "open_orders_complete")
    first = requests[0]
    cost = first.cost_model
    for request in requests[1:]:
        if any(getattr(request, name) != getattr(first, name) for name in common_names) or (
            (request.cost_model.to_dict() if request.cost_model else None) != (cost.to_dict() if cost else None)
        ):
            raise ValueError("position action targets do not share the same account/input cut and cost model")
    targets = {}
    expected = {}
    for request in sorted(requests, key=lambda value: value.action):
        result = evaluate_position_target_shadow(request, account, open_orders, broker_open_orders=broker_open_orders)
        targets[request.action] = result
        reasons = list(result["quality"]["reasons"])
        if result["funding_shortfall_quantity"]:
            reasons.append("target_not_fully_funded")
        if cost is None:
            reasons.append("cost_model_unavailable")
        elif cost.valid_from and not (_time(datetime.fromisoformat(cost.valid_from)) <= _time(request.decision_time)
                                      < _time(datetime.fromisoformat(cost.valid_to))):
            reasons.append("cost_model_outside_current_validity")
        expected[request.action] = {
            "policy_version": result["target_policy_version"], "environment": request.environment, "venue": request.venue,
            "source_kind": parameters.outcome_source_kind, "execution_model_ref": parameters.execution_model_ref,
            "initial_quantity": result["actual_quantity"], "pending_buy_quantity": result["pending_buy_quantity"],
            "pending_sell_quantity": result["pending_sell_quantity"], "target_quantity": result["target_quantity"],
            "cost_model_ref": position_action_cost_model_ref(cost.to_dict() if cost else None),
            "cost_valid_from": cost.valid_from if cost else "", "cost_valid_to": cost.valid_to if cost else "",
            "reasons": tuple(sorted(set(reasons))),
        }
    rows = compare_position_action_outcomes(
        outcomes, expected, baseline_action=parameters.baseline_action, decision_time=first.decision_time,
        horizon_seconds=parameters.horizon_seconds, population_ref=parameters.population_ref,
        input_resolution=parameters.input_resolution, minimum_samples=parameters.minimum_samples,
    )
    compared = sum(row["ev_status"] == "EMPIRICAL_ESTIMATE" for row in rows if row["action"] != parameters.baseline_action)
    parameter_document = asdict(parameters)
    if parameters.selection_policy is None:
        parameter_document.pop('selection_policy')  # Preserve legacy v1 identities.
    else:
        for name in ('available_at', 'valid_until'):
            parameter_document['selection_policy'][name] = _time(getattr(parameters.selection_policy, name)).isoformat()
        if isinstance(parameters.selection_policy,PositionActionCapabilitySelectionPolicy):
            parameter_document['selection_policy']['required_data_capabilities']=list(parameters.selection_policy.required_data_capabilities)
            if parameters.selection_policy.action_required_data_capabilities is None:
                parameter_document['selection_policy'].pop('action_required_data_capabilities')
            else:
                parameter_document['selection_policy']['action_required_data_capabilities']=[
                    [action,list(values)] for action,values in parameters.selection_policy.action_required_data_capabilities]
        if parameters.selection_policy.input_mode == 'supplied_fixed':
            for name in ('input_mode', 'thesis_ref'):
                parameter_document['selection_policy'].pop(name)
    body = {
        "policy_version": POSITION_ACTION_COMPARISON_SHADOW_VERSION,
        "decision_time": _time(first.decision_time).isoformat(), "symbol": first.symbol,
        "scope": {"environment": first.environment, "venue": first.venue, "source_durability": "not_verified"},
        "parameters": parameter_document, "targets": targets, "actions": rows,
        "status": "COMPARABLE" if compared == len(requests) - 1 else "PARTIAL" if compared else "EV_UNAVAILABLE",
        "uncertainty_method": "paired_empirical_stddev_and_standard_error/v1",
        "standard_error_assumption": "independent_cases_not_verified",
        "probability_calibration": "not_verified", "execution_support": "shadow_only", "order_authorized": False,
    }
    if parameters.selection_policy is not None:
        policy = parameters.selection_policy
        if policy.input_mode in {'owner_ram_risk_and_thesis','owner_ram_risk'}:
            adapted, receipt, blocked, protection = _native_selection_inputs(policy, first, account,
                risk_snapshot, stock_thesis_result)
            body['selection_inputs'] = receipt
            selected = _select_position_action(adapted, first, targets, rows,action_inputs=receipt.get('action_inputs'))
            selected['risk_authority'] = receipt['risk_authority']
            if blocked:
                selected.update(action='EXIT' if protection else 'WAIT', status='RISK_REQUIRED' if protection else 'BLOCKED',
                    risk_path_required=protection, target_decision_ref=None, scores={}, reasons=receipt['reasons'])
            if selected['action'] in receipt.get('action_inputs',{}):
                receipt['expires_at']=min((receipt['expires_at'],receipt['action_inputs'][selected['action']]['valid_until']),key=lambda at:_time(datetime.fromisoformat(at)))
            body['action_selection'] = selected
        else:
            body['action_selection'] = _select_position_action(policy, first, targets, rows)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode("utf-8")).hexdigest()
    return {"decision_id": f"position-action-comparison-shadow:{digest}", **body}


def _position_target_request_from_dict(value: dict[str, Any]) -> PositionTargetShadowRequest:
    raw_request = dict(value)
    for name in ("decision_time", "reference_price_available_at"):
        raw_request[name] = datetime.fromisoformat(raw_request[name])
    if raw_request["cost_model"] is not None:
        raw_request["cost_model"] = SimulationCostModel(**raw_request["cost_model"])
    if raw_request.get('target_policy') is not None:
        policy = dict(raw_request['target_policy'])
        for name in ('available_at', 'valid_until'):
            policy[name] = datetime.fromisoformat(policy[name])
        raw_request['target_policy'] = PositionTargetSizingPolicy(**policy)
    return PositionTargetShadowRequest(**raw_request)


def evaluate_position_target_document(document: dict[str, Any]) -> dict[str, Any]:
    """Strict fixed-input CLI adapter; constructs existing DTOs without any I/O."""
    if set(document) != {"schema", "request", "account", "open_orders"} or document["schema"] != POSITION_TARGET_SHADOW_VERSION:
        raise ValueError("unsupported position target document fields/schema")
    request = _position_target_request_from_dict(document["request"])
    account, records = _position_account_orders_from_dict(document["account"], document["open_orders"])
    return evaluate_position_target_shadow(request, account, records)


def _position_account_orders_from_dict(value: dict[str, Any], orders: list) -> tuple[AccountSnapshot, tuple]:
    if not isinstance(orders, list) or len(orders) > 400:
        raise ValueError("position target requires at most 400 account order records")
    raw_account = dict(value)
    raw_account["as_of"] = datetime.fromisoformat(raw_account["as_of"])
    account = AccountSnapshot(**raw_account)
    records = []
    for value in orders:
        raw_record = dict(value)
        intent = dict(raw_record["intent"])
        intent["side"] = OrderSide(intent["side"])
        intent["order_type"] = OrderType(intent["order_type"])
        for name in ("created_at", "expires_at"):
            intent[name] = datetime.fromisoformat(intent[name])
        raw_record["intent"] = OrderIntent(**intent)
        raw_record["state"] = OrderState(raw_record["state"])
        raw_record["updated_at"] = datetime.fromisoformat(raw_record["updated_at"])
        if raw_record["last_broker_as_of"] is not None:
            raw_record["last_broker_as_of"] = datetime.fromisoformat(raw_record["last_broker_as_of"])
        raw_record["fill_ids"] = tuple(raw_record["fill_ids"])
        records.append(ExecutionRecord(**raw_record))
    return account, tuple(records)


def position_action_comparison_inputs_from_document(document: dict[str, Any]) -> tuple:
    """Parse fixed policy/outcome inputs once for CLI and common RAM consumers."""
    from kiwoom_monitor.application.research_evaluation import PositionActionOutcome
    if set(document) != {"schema", "parameters", "requests", "account", "open_orders", "outcomes"} or (
        document["schema"] != POSITION_ACTION_COMPARISON_SHADOW_VERSION
    ):
        raise ValueError("unsupported position action comparison document fields/schema")
    if not isinstance(document["requests"], list) or not 2 <= len(document["requests"]) <= 5:
        raise ValueError("position action comparison requires 2..5 requests")
    if not isinstance(document["outcomes"], list) or len(document["outcomes"]) > 10_000:
        raise ValueError("position action comparison accepts at most 10000 outcomes")
    raw_parameters = dict(document['parameters'])
    if raw_parameters.get('selection_policy') is not None:
        policy = dict(raw_parameters['selection_policy'])
        for name in ('available_at', 'valid_until'):
            policy[name] = datetime.fromisoformat(policy[name])
        if policy.get('version')=='paired-net-action-selection/v2':
            if not isinstance(policy.get('required_data_capabilities'),list):
                raise ValueError('required_data_capabilities requires an explicit JSON array')
            policy['required_data_capabilities']=tuple(policy['required_data_capabilities'])
            if policy.get('action_required_data_capabilities') is not None:
                matrix=policy['action_required_data_capabilities']
                if (not isinstance(matrix,list) or any(not isinstance(row,list) or len(row)!=2
                        or not isinstance(row[1],list) for row in matrix)):
                    raise ValueError('action data capabilities require explicit JSON array pairs')
                policy['action_required_data_capabilities']=tuple((action,tuple(values)) for action,values in matrix)
            raw_parameters['selection_policy'] = PositionActionCapabilitySelectionPolicy(**policy)
        else:
            raw_parameters['selection_policy'] = PositionActionSelectionPolicy(**policy)
    parameters = PositionActionComparisonParameters(**raw_parameters)
    requests = tuple(_position_target_request_from_dict(value) for value in document["requests"])
    if isinstance(parameters.selection_policy,PositionActionCapabilitySelectionPolicy):
        if {action for action,_ in parameters.selection_policy.action_required_data_capabilities or ()}-{r.action for r in requests}:
            raise ValueError('action data requirement has no compared target')
    account, records = _position_account_orders_from_dict(document["account"], document["open_orders"])
    outcomes = []
    for value in document["outcomes"]:
        raw = dict(value)
        for name in ("started_at", "completed_at", "available_at"):
            if raw[name] is not None:
                raw[name] = datetime.fromisoformat(raw[name])
        outcomes.append(PositionActionOutcome(**raw))
    return parameters, requests, account, records, tuple(outcomes)


def evaluate_position_action_comparison_document(document: dict[str, Any]) -> dict[str, Any]:
    return evaluate_position_action_comparison_shadow(*position_action_comparison_inputs_from_document(document))
