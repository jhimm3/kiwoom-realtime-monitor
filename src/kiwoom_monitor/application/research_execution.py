"""D3c 전용 단일 포지션 모의 주문·체결 엔진."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_DOWN
from typing import Any, Mapping

from kiwoom_monitor.application.breakout_strategy import (
    StrategyRuntimeConfig,
    StrategyDecision,
    StrategyState,
    regular_session_fill_deadline,
    regular_session_time_rejection,
    session_entry_time_rejection,
)
from kiwoom_monitor.application.market_session_schedule import (
    KRX_REGULAR_RESEARCH_PROFILE,
    MarketPhase,
    SUPPORTED_RESEARCH_SESSION_PROFILES,
    research_session_key,
)
from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
from kiwoom_monitor.domain.ranking import normalize_stock_code


COST_MODEL_VERSION = "fixed_bps/v1"
COST_MODEL_DECIMAL_VERSION = "fixed_bps/v2"
EXECUTION_MODEL_VERSION = "next_tradable_bar_open/v1"
POSITION_TARGET_EXECUTION_VERSION = "paper-position-target/v1"
UNFILLED_EXECUTION_VERSION = "unfilled-paired-limit/v1"
SAME_BAR_PATH_VERSION = "conservative_with_optimistic_bound/v1"
FULL_EXIT_STRESS_VERSION = 'paper-full-exit-stress/v1'


@dataclass(frozen=True)
class FullExitStressScenario:
    scenario_ref: str
    participation_bps: int
    volume_haircut_bps: int
    price_shock_bps: int
    extra_sell_slippage_bps: int
    blocked_minute_offsets: tuple[int, ...]

    def __post_init__(self):
        if type(self.scenario_ref) is not str or not 0<len(self.scenario_ref)<=128:
            raise ValueError('explicit exit scenario identity required')
        for name in ('participation_bps','volume_haircut_bps','price_shock_bps','extra_sell_slippage_bps'):
            value=getattr(self,name)
            if type(value) is not int or not 0<=value<=(9999 if name in {'price_shock_bps','extra_sell_slippage_bps'} else 10000):
                raise ValueError('invalid explicit exit scenario bps')
        if (type(self.blocked_minute_offsets) is not tuple or len(self.blocked_minute_offsets)>200
                or self.blocked_minute_offsets!=tuple(sorted(set(self.blocked_minute_offsets)))
                or any(type(v) is not int or not 1<=v<=200 for v in self.blocked_minute_offsets)):
            raise ValueError('invalid blocked exit minutes')


@dataclass(frozen=True)
class FullExitStressPolicy:
    version: str
    policy_ref: str
    symbol: str
    tested_position_quantity: int
    reference_price_won: int
    exit_window_seconds: int
    scenarios: tuple[FullExitStressScenario, ...]
    available_at: datetime
    valid_until: datetime

    def __post_init__(self):
        if (self.version!=FULL_EXIT_STRESS_VERSION or type(self.policy_ref) is not str or not 0<len(self.policy_ref)<=256
                or type(self.symbol) is not str or re.fullmatch(r'[0-9]{6}',self.symbol) is None
                or any(type(v) is not int or not 1<=v<=10**12 for v in (self.tested_position_quantity,self.reference_price_won))
                or type(self.exit_window_seconds) is not int or not 60<=self.exit_window_seconds<=12000
                or self.exit_window_seconds%60 or type(self.scenarios) is not tuple or not 2<=len(self.scenarios)<=20
                or any(not isinstance(s,FullExitStressScenario) for s in self.scenarios)
                or len({s.scenario_ref for s in self.scenarios})!=len(self.scenarios)
                or len({(s.participation_bps,s.volume_haircut_bps,s.price_shock_bps,
                         s.extra_sell_slippage_bps,s.blocked_minute_offsets) for s in self.scenarios})!=len(self.scenarios)
                or any(any(v>self.exit_window_seconds//60 for v in s.blocked_minute_offsets) for s in self.scenarios)
                or not isinstance(self.available_at,datetime) or not isinstance(self.valid_until,datetime)
                or _aware_datetime(self.valid_until)<=_aware_datetime(self.available_at)):
            raise ValueError('invalid bounded full-exit stress policy')


def simulate_full_exit_stress(policy, source_bar, bars, *, execution, strategy, decision_time, waiting_bars=()):
    """Declared research scenarios; reuse paper partial-sell basis/cost accounting.

    Historical volume caps and shocks are assumptions, never liquidity forecasts
    or broker partial-fill probabilities. Low prices bound modeled loss only;
    they never become execution prices or an intrabar execution sequence.
    """
    if (not isinstance(policy,FullExitStressPolicy) or not isinstance(source_bar,KrxMinuteBarFrame)
            or not isinstance(execution,SimulationExecutionConfig) or type(bars) is not tuple
            or len(bars)>200 or any(not isinstance(b,KrxMinuteBarFrame) for b in bars)
            or type(waiting_bars) is not tuple or len(waiting_bars)>200
            or any(not isinstance(b,KrxMinuteBarFrame) for b in waiting_bars)):
        raise ValueError('typed bounded full-exit research inputs required')
    cut=_aware_datetime(decision_time);source_at=_aware_datetime(source_bar.available_at)
    first=source_at.replace(second=0,microsecond=0)
    if first<source_at:first+=timedelta(minutes=1)
    deadline=first+timedelta(seconds=policy.exit_window_seconds)
    lead_start=_aware_datetime(source_bar.bar_end)
    lead_count=int((first-lead_start).total_seconds()//60)
    cost=execution.cost_model
    from kiwoom_monitor.application.market_session_schedule import research_bar_allowed, research_session_key
    result={'version':FULL_EXIT_STRESS_VERSION,'policy_ref':policy.policy_ref,'symbol':policy.symbol,
        'tested_position_quantity':policy.tested_position_quantity,'reference_price_won':policy.reference_price_won,
        'exit_window_seconds':policy.exit_window_seconds,'eligible_at':first.isoformat(),'deadline':deadline.isoformat(),
        'status':'UNAVAILABLE','reasons':[],'conservative_exit_capacity_won':None,'stress_loss_bps':None,
        'scenarios':[],'source_durability':'not_verified','order_authorized':False,
        'scope':{'environment':'simulation','venue':'KRX','quantity_basis':'whole_potential_long_position'},
        'execution_support':'shadow_only','liquidation_guaranteed':False,'partial_fill_probability':'not_verified',
        'fill_basis':'eligible_minute_open_volume_capped/v1',
        'risk_basis':'declared_worst_price_and_per_share_fee_ceiling/v1',
        'waiting_bar_count':len(waiting_bars),'waiting_prices_used_for_fills':False,
        'probability_calibration':'not_verified','size_extrapolation_supported':False}
    reasons=result['reasons']
    def valid_bar(bar):
        return (bar.code==policy.symbol and bar.session_profile==KRX_REGULAR_RESEARCH_PROFILE
            and bar.market_phase==MarketPhase.CONTINUOUS.value and bar.capture_quality=='complete'
            and bar.research_session==source_bar.research_session and bool(bar.research_session)
            and type(bar.revision_id) is str and bool(bar.revision_id)
            and (_aware_datetime(bar.bar_end)-_aware_datetime(bar.bar_start)).total_seconds()==60
            and _aware_datetime(bar.bar_end)<=_aware_datetime(bar.available_at)<=cut
            and all(type(v) is int and v>0 for v in (bar.open,bar.high,bar.low,bar.close))
            and bar.low<=min(bar.open,bar.close)<=max(bar.open,bar.close)<=bar.high
            and type(bar.volume) is int and bar.volume>=0
            and research_bar_allowed(_aware_datetime(bar.bar_start),_aware_datetime(bar.bar_end),venue='KRX',
                session_profile=KRX_REGULAR_RESEARCH_PROFILE,declared_session=bar.market_session or None,
                declared_phase=bar.market_phase,declared_schedule_version=bar.schedule_version)
            and research_session_key(_aware_datetime(bar.bar_start),session_profile=KRX_REGULAR_RESEARCH_PROFILE)==bar.research_session)
    if (source_bar.close!=policy.reference_price_won or not valid_bar(source_bar)
            or not policy.available_at<=source_at<policy.valid_until or not policy.available_at<=cut<policy.valid_until):
        reasons.append('full_exit_source_or_policy_cut_invalid')
    if (len(bars)!=policy.exit_window_seconds//60 or any(not valid_bar(b) for b in bars)
            or len({b.revision_id for b in (source_bar,*waiting_bars,*bars)})!=len(bars)+len(waiting_bars)+1
            or any(_aware_datetime(b.bar_start)!=first+timedelta(minutes=i) for i,b in enumerate(bars))):
        reasons.append('full_exit_window_gap_session_or_availability_invalid')
    if (lead_count<0 or lead_count>200 or len(waiting_bars)!=lead_count
            or any(not valid_bar(b) or _aware_datetime(b.bar_start)!=lead_start+timedelta(minutes=i)
                   for i,b in enumerate(waiting_bars))):
        reasons.append('full_exit_pre_eligible_wait_window_unavailable')
    if (cost is None or not cost.valid_from or not cost.source.strip() or cost.rate_basis=='unspecified'
            or not _aware_datetime(cost.valid_from)<=source_at
            or any(not _aware_datetime(cost.valid_from)<=_aware_datetime(b.available_at)<_aware_datetime(cost.valid_to) for b in (*waiting_bars,*bars))
            or not _aware_datetime(cost.valid_from)<=cut<_aware_datetime(cost.valid_to)):
        reasons.append('full_exit_cost_period_unavailable')
    if reasons:return result
    source={'policy':asdict(policy),'source_bar':asdict(source_bar),'bars':[asdict(b) for b in bars],
        'waiting_bars':[asdict(b) for b in waiting_bars],
        'cost_model':cost.to_dict()}
    identity=_content_id('full-exit-stress-source',json.loads(json.dumps(source,
        default=lambda v:_aware_datetime(v).astimezone(timezone.utc).isoformat())))
    result['source_ref']=identity
    result['available_at']=max(_aware_datetime(b.available_at) for b in (source_bar,*waiting_bars,*bars)).isoformat()
    result['valid_until']=min(policy.valid_until,_aware_datetime(cost.valid_to)).isoformat()
    worst_unit_loss=0
    for scenario in policy.scenarios:
        engine=PaperExecutionEngine(identity+':'+scenario.scenario_ref,execution,strategy,KRX_REGULAR_RESEARCH_PROFILE)
        q=policy.tested_position_quantity;ref=policy.reference_price_won
        engine.portfolio=SimulationPortfolio(0,0,SimulationPosition(policy.symbol,q,ref,q*ref,0,
            source_bar.available_at,'full-exit-research-initial'),None,0,0)
        engine.strategy_state=StrategyState(status='open',symbol=policy.symbol,entry_price=ref,
            position_quantity=q,opened_at=source_bar.available_at)
        fills=[];budgets=[]
        def scenario_price(price):
            shocked=price*(10000-scenario.price_shock_bps)//10000
            shocked=shocked*(10000-scenario.extra_sell_slippage_bps)//10000
            return _slipped_price(shocked,'SELL',cost.slippage_bps)
        def ceiling_fee(price, rate):
            return int((Decimal(price)*rate/10000).to_integral_value(rounding=ROUND_CEILING))
        invalid_price=any(scenario_price(p)<=0 for b in (*waiting_bars,*bars) for p in (b.open,b.low))
        if invalid_price:
            result['scenarios'].append({'scenario_ref':scenario.scenario_ref,'status':'UNAVAILABLE',
                'remaining_quantity':q,'fills':[],'reasons':['scenario_price_nonpositive']})
            continue
        unit_loss=max(0,max(ref-scenario_price(b.low)+ceiling_fee(scenario_price(b.low),cost.commission_bps)
            +ceiling_fee(scenario_price(b.low),cost.sell_tax_bps) for b in (*waiting_bars,*bars)))
        worst_unit_loss=max(worst_unit_loss,unit_loss)
        for offset,bar in enumerate(bars,1):
            budget=(bar.volume*(10000-scenario.volume_haircut_bps)*scenario.participation_bps)//100000000
            if offset in scenario.blocked_minute_offsets:budget=0
            budgets.append(budget)
            remaining=engine.portfolio.position.quantity if engine.portfolio.position else 0
            chunk=min(remaining,budget)
            if chunk:
                # Same paper writer value path, with explicitly shocked open.
                event=engine._sell_fill(identity+':'+scenario.scenario_ref+':'+str(offset),identity,
                    bar,scenario_price(bar.open),chunk,event_type='FILL',reason=FULL_EXIT_STRESS_VERSION)
                fills.append(event.to_dict())
        remaining=engine.portfolio.position.quantity if engine.portfolio.position else 0
        result['scenarios'].append({'scenario_ref':scenario.scenario_ref,
            'status':'COMPLETE' if not remaining else 'PARTIAL' if fills else 'UNFILLED',
            'filled_quantity':q-remaining,'remaining_quantity':remaining,'minute_quantity_budgets':budgets,
            'fills':fills,'net_proceeds_won':engine.portfolio.cash_won,
            'realized_loss_won':max(0,-engine.portfolio.realized_pnl_won),
            'conservative_unit_loss_won':unit_loss,'reasons':[]})
    if all(r['status']=='COMPLETE' for r in result['scenarios']):
        result.update(status='CALCULATED',conservative_exit_capacity_won=policy.tested_position_quantity*policy.reference_price_won,
            stress_loss_bps=(worst_unit_loss*10000+policy.reference_price_won-1)//policy.reference_price_won)
    else:
        result['status']='INCOMPLETE';reasons.append('full_position_exit_not_proven_in_every_declared_scenario')
    return result


@dataclass(frozen=True)
class SimulationCostModel:
    version: str
    commission_bps: int | Decimal
    sell_tax_bps: int | Decimal
    slippage_bps: int | Decimal
    rate_basis: str = "unspecified"
    source: str = ""
    valid_from: str = ""
    valid_to: str = ""

    def __post_init__(self) -> None:
        if self.version not in {COST_MODEL_VERSION, COST_MODEL_DECIMAL_VERSION}:
            raise ValueError(f"unregistered cost model: {self.version}")
        for field in ("commission_bps", "sell_tax_bps", "slippage_bps"):
            value = getattr(self, field)
            if self.version == COST_MODEL_VERSION:
                if type(value) is not int:
                    raise ValueError(f"{field} must be an integer in fixed_bps/v1")
            else:
                if not isinstance(value, (str, Decimal)):
                    raise ValueError(f"{field} must be a canonical decimal string in fixed_bps/v2")
                text = str(value)
                if re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?", text) is None:
                    raise ValueError(f"{field} must be a canonical decimal string in fixed_bps/v2")
                try:
                    decimal_value = Decimal(text)
                except InvalidOperation as exc:
                    raise ValueError(f"{field} must be a decimal rate") from exc
                if not decimal_value.is_finite() or decimal_value < 0:
                    raise ValueError(f"{field} must be a canonical nonnegative rate in 0.01 bp units")
                object.__setattr__(self, field, decimal_value)
        if min(self.commission_bps, self.sell_tax_bps, self.slippage_bps) < 0:
            raise ValueError("simulation cost rates must not be negative")
        if self.slippage_bps >= 10_000:
            raise ValueError("slippage_bps must be below 10000")
        if self.rate_basis not in {
            "unspecified", "official_period_rule", "account_actual", "model_estimate",
        }:
            raise ValueError(f"unsupported simulation cost rate basis: {self.rate_basis}")
        if bool(self.valid_from) != bool(self.valid_to):
            raise ValueError("simulation cost validity requires both valid_from and valid_to")
        if self.valid_from:
            start = _aware_datetime(self.valid_from)
            end = _aware_datetime(self.valid_to)
            if end <= start:
                raise ValueError("simulation cost valid_to must be after valid_from")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.version == COST_MODEL_DECIMAL_VERSION:
            for field in ("commission_bps", "sell_tax_bps", "slippage_bps"):
                result[field] = str(result[field])
        return result


@dataclass(frozen=True)
class SimulationExecutionConfig:
    version: str
    same_bar_path_version: str
    initial_cash_won: int
    cost_model: SimulationCostModel | None

    def __post_init__(self) -> None:
        if self.version != EXECUTION_MODEL_VERSION:
            raise ValueError(f"unregistered execution model: {self.version}")
        if self.same_bar_path_version != SAME_BAR_PATH_VERSION:
            raise ValueError(f"unregistered same-bar path model: {self.same_bar_path_version}")
        if self.initial_cash_won <= 0:
            raise ValueError("initial_cash_won must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "same_bar_path_version": self.same_bar_path_version,
            "initial_cash_won": self.initial_cash_won,
            "cost_model": self.cost_model.to_dict() if self.cost_model else None,
        }


@dataclass(frozen=True)
class SimulationPosition:
    symbol: str
    quantity: int
    entry_price: int
    entry_notional: int
    entry_commission: int
    opened_at: str
    entry_intent_id: str


@dataclass(frozen=True)
class PendingOrder:
    intent_id: str
    decision_id: str
    symbol: str
    side: str
    quantity: int
    submitted_at: str
    eligible_after: str
    reserved_cash_won: int
    session_profile: str = ""
    research_session: str = ""
    cash_buffer_won: int = 0
    target_execution_version: str = ""
    limit_price_won: int = 0
    limit_execution_version: str = ""


@dataclass(frozen=True)
class UnfilledResearchAction:
    """Explicit research instructions; SUBMIT establishes the native pending owner.

    v1 observes only subsequent minute opens, fills the whole quantity when
    volume and slipped price permit, and models cancellation with zero latency.
    It cannot establish broker queue priority or partial-fill probabilities.
    """
    action: str
    symbol: str
    side: str
    quantity: int
    existing_limit_price_won: int
    candidate_limit_price_won: int
    target_quantity: int
    decision_time: datetime
    case_ref: str
    population_ref: str

    def __post_init__(self):
        if (self.action not in {'SUBMIT','WAIT','REPRICE'} or self.side not in {'BUY','SELL'}
                or type(self.symbol) is not str or not self.symbol.isascii()
                or len(self.symbol)!=6 or not self.symbol.isdigit()
                or not isinstance(self.decision_time,datetime) or self.decision_time.tzinfo is None
                or self.decision_time.utcoffset() is None
                or any(type(v) is not str or not v.strip() for v in (self.case_ref,self.population_ref))):
            raise ValueError('invalid explicit unfilled research action')
        for name in ('quantity','existing_limit_price_won','candidate_limit_price_won','target_quantity'):
            value=getattr(self,name)
            if type(value) is not int or not (0 if name=='target_quantity' else 1)<=value<=10**12:
                raise ValueError('invalid unfilled research quantity/price')

    def to_dict(self):
        return {**asdict(self),'decision_time':self.decision_time.isoformat()}

    @classmethod
    def from_dict(cls, value):
        return cls(**{**value,'decision_time':_aware_datetime(value['decision_time'])})


@dataclass(frozen=True)
class SimulationPortfolio:
    cash_won: int
    reserved_cash_won: int
    position: SimulationPosition | None
    pending_order: PendingOrder | None
    realized_pnl_won: int
    total_cost_won: int


@dataclass(frozen=True)
class ExecutionEvent:
    event_id: str
    run_id: str
    execution_environment: str
    account_ref: str
    intent_id: str
    decision_id: str
    event_type: str
    state: str
    symbol: str
    side: str
    occurred_at: str
    received_at: str
    quantity: int
    price: int
    notional_won: int
    commission_won: int
    tax_won: int
    total_cost_won: int
    realized_pnl_won: int
    cash_after_won: int
    reserved_cash_after_won: int
    position_quantity_after: int
    source_revision_id: str
    optimistic_exit_price: int | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PaperExecutionEngine:
    """실계좌·TradeCostService와 연결되지 않는 결정론적 단일 슬롯 실행기."""

    def __init__(
        self,
        run_id: str,
        execution: SimulationExecutionConfig,
        strategy: StrategyRuntimeConfig,
        session_profile: str | None = None,
    ) -> None:
        if not str(run_id).strip():
            raise ValueError("run_id is required")
        self.run_id = run_id
        if session_profile is not None and session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES:
            raise ValueError(f"unsupported research session profile: {session_profile}")
        self.execution = execution
        self.strategy = strategy
        self.session_profile = session_profile
        self._last_processed_bar = None
        self.strategy_state = StrategyState()
        self.portfolio = SimulationPortfolio(
            cash_won=execution.initial_cash_won,
            reserved_cash_won=0,
            position=None,
            pending_order=None,
            realized_pnl_won=0,
            total_cost_won=0,
        )

    def process_bar(self, bar: KrxMinuteBarFrame) -> tuple[ExecutionEvent, ...]:
        self._last_processed_bar = bar
        events: list[ExecutionEvent] = []
        pending = self.portfolio.pending_order
        if (pending is not None and self.session_profile is not None
                and pending.research_session and bar.research_session
                and bar.research_session != pending.research_session):
            events.append(self._discard_pending(pending, bar, "session_boundary_before_fill"))
            pending = None
        if pending is not None and pending.symbol == normalize_stock_code(bar.code):
            bar_start = _aware_datetime(bar.bar_start).astimezone(timezone.utc)
            eligible_after = _aware_datetime(pending.eligible_after).astimezone(timezone.utc)
            if bar_start >= eligible_after:
                reason = self._pending_bar_rejection(pending, bar)
                if not reason and pending.limit_execution_version:
                    if (bar.capture_quality!='complete' or bar.volume < pending.quantity
                            or self.execution.cost_model is None
                            or not _aware_datetime(self.execution.cost_model.valid_from)<=_aware_datetime(bar.available_at)
                                <_aware_datetime(self.execution.cost_model.valid_to)):
                        reason='limit_liquidity_or_cost_unproven'
                    elif not self._limit_open_crosses(pending,bar):
                        self.portfolio=replace(self.portfolio,pending_order=replace(pending,eligible_after=bar.bar_end))
                        pending=None
                if pending is not None:
                    events.append(self._discard_pending(pending,bar,reason) if reason else self._fill_pending(pending,bar))
        if self.portfolio.position is not None and (
            self.portfolio.position.symbol == normalize_stock_code(bar.code)
        ):
            deadline_unproven = (self.strategy.session_exit_policy
                and (regular_session_time_rejection(_aware_datetime(bar.bar_start))
                     or _aware_datetime(bar.bar_end) >= regular_session_fill_deadline(
                         _aware_datetime(self.portfolio.position.opened_at))))
            if deadline_unproven or self.session_profile is not None and bar.market_phase != MarketPhase.CONTINUOUS.value:
                events.append(self._event(
                    intent_id=self.portfolio.position.entry_intent_id, decision_id="",
                    event_type="RISK_UNSUPPORTED", state="OPEN",
                    symbol=self.portfolio.position.symbol, side="", occurred_at=bar.bar_end,
                    received_at=bar.available_at, quantity=self.portfolio.position.quantity,
                    price=0, notional=0, commission=0, tax=0, realized=0,
                    source_revision_id=bar.revision_id, optimistic_exit_price=None,
                    reason=("regular_session_fill_deadline_unproven" if deadline_unproven
                            else "minute_bar_phase_cannot_prove_intrabar_execution_order"),
                ))
                return tuple(events)
            risk_pending = self.portfolio.pending_order
            risk_event = self._apply_intrabar_risk(bar)
            if risk_event is not None:
                events.append(risk_event)
                if risk_pending is not None and (risk_pending.target_execution_version == POSITION_TARGET_EXECUTION_VERSION
                        or risk_pending.limit_execution_version):
                    events.append(self._event(intent_id=risk_pending.intent_id,decision_id=risk_pending.decision_id,
                        event_type='ORDER_CANCELLED',state='CANCELLED',symbol=risk_pending.symbol,side=risk_pending.side,
                        occurred_at=bar.bar_end,received_at=bar.available_at,quantity=risk_pending.quantity,
                        price=0,notional=0,commission=0,tax=0,realized=0,source_revision_id=bar.revision_id,
                        optimistic_exit_price=None,reason='risk_exit_preempted_target'))
            elif self.portfolio.position is not None:
                events.append(self._mark(bar))
        return tuple(events)

    def process_decision(
        self, decision: StrategyDecision, resulting_state: StrategyState,
        *, source_bar: KrxMinuteBarFrame | None = None,
    ) -> tuple[ExecutionEvent, ...]:
        self.strategy_state = resulting_state
        if decision.final_action not in {"ENTER", "EXIT"}:
            return ()
        side = "BUY" if decision.final_action == "ENTER" else "SELL"
        preempted = ()
        owned = self.portfolio.pending_order
        if (side=='SELL' and owned is not None and (owned.target_execution_version==POSITION_TARGET_EXECUTION_VERSION
                or owned.limit_execution_version)
                and self.portfolio.position is not None and decision.symbol==owned.symbol==self.portfolio.position.symbol):
            self.portfolio=replace(self.portfolio,reserved_cash_won=0,pending_order=None)
            preempted=(self._event(intent_id=owned.intent_id,decision_id=owned.decision_id,event_type='ORDER_CANCELLED',
                state='CANCELLED',symbol=owned.symbol,side=owned.side,occurred_at=decision.decided_at,
                received_at=decision.decided_at,quantity=owned.quantity,price=0,notional=0,commission=0,tax=0,
                realized=0,source_revision_id=source_bar.revision_id if source_bar else '',optimistic_exit_price=None,
                reason='strategy_exit_preempted_target'),)
        if self.portfolio.pending_order is not None:
            return (self._notice(decision, side, "ORDER_REJECTED", "pending_order_exists"),)
        if side == "BUY" and self.portfolio.position is not None:
            return (self._notice(decision, side, "ORDER_REJECTED", "position_already_open"),)
        if side == "SELL" and self.portfolio.position is None:
            return (self._notice(decision, side, "ORDER_REJECTED", "position_missing"),)
        if side == "BUY":
            reason = session_entry_time_rejection(self.strategy, _aware_datetime(decision.decided_at))
            if reason:
                self.strategy_state = StrategyState(emitted_candidate_keys=resulting_state.emitted_candidate_keys)
                return (self._notice(decision, side, "ORDER_REJECTED", reason),)
        quantity = (
            decision.quantity if side == "BUY" else self.portfolio.position.quantity
        )
        if quantity <= 0:
            return (self._notice(decision, side, "ORDER_REJECTED", "invalid_quantity"),)
        reserved = self._estimated_buy_cash(decision.signal_reference_price, quantity) if side == "BUY" else 0
        available_cash = self.portfolio.cash_won - self.portfolio.reserved_cash_won
        if side == "BUY" and reserved > available_cash:
            self.strategy_state = StrategyState(
                emitted_candidate_keys=resulting_state.emitted_candidate_keys,
            )
            return (self._notice(decision, side, "ORDER_REJECTED", "cash_reservation_exceeded"),)
        intent_id = _content_id("intent", {
            "run_id": self.run_id, "decision_id": decision.decision_id,
            "side": side, "quantity": quantity,
        })
        pending = PendingOrder(
            intent_id=intent_id,
            decision_id=decision.decision_id,
            symbol=decision.symbol,
            side=side,
            quantity=quantity,
            submitted_at=decision.decided_at,
            eligible_after=decision.decided_at,
            reserved_cash_won=reserved,
            session_profile=self.session_profile or "",
            research_session=(
                source_bar.research_session
                if source_bar is not None and source_bar.research_session
                else research_session_key(
                    _aware_datetime(decision.decided_at),
                    session_profile=self.session_profile or KRX_REGULAR_RESEARCH_PROFILE,
                ) or ""
            ),
        )
        self.portfolio = SimulationPortfolio(
            cash_won=self.portfolio.cash_won,
            reserved_cash_won=reserved,
            position=self.portfolio.position,
            pending_order=pending,
            realized_pnl_won=self.portfolio.realized_pnl_won,
            total_cost_won=self.portfolio.total_cost_won,
        )
        return preempted + (self._event(
            intent_id=intent_id, decision_id=decision.decision_id,
            event_type="ORDER_SUBMITTED", state="PENDING", symbol=decision.symbol,
            side=side, occurred_at=decision.decided_at, received_at=decision.decided_at,
            quantity=quantity, price=0, notional=0, commission=0, tax=0,
            realized=0, source_revision_id="", optimistic_exit_price=None, reason="",
        ),)

    def observe_position_value(self, bar: KrxMinuteBarFrame) -> ExecutionEvent:
        """Explicit valuation for measured target windows, including a flat account."""
        if (bar != self._last_processed_bar or self.session_profile != KRX_REGULAR_RESEARCH_PROFILE or bar.session_profile != self.session_profile
                or bar.market_phase != MarketPhase.CONTINUOUS.value or not bar.revision_id or bar.close <= 0
                or bar.capture_quality != 'complete' or regular_session_time_rejection(_aware_datetime(bar.bar_start))):
            raise ValueError('position target valuation requires a complete KRX regular bar')
        if self.portfolio.position is not None:
            if self.portfolio.position.symbol != normalize_stock_code(bar.code):
                raise ValueError('position target valuation symbol mismatch')
            return self._mark(bar)
        return self._event(intent_id='', decision_id='', event_type='MARK', state='FLAT',
            symbol=bar.code, side='', occurred_at=bar.bar_end, received_at=bar.available_at,
            quantity=0, price=bar.close, notional=0, commission=0, tax=0, realized=0,
            source_revision_id=bar.revision_id, optimistic_exit_price=None, reason='')

    def process_position_target(self, request, *, source_bar: KrxMinuteBarFrame) -> tuple[ExecutionEvent, ...]:
        """Reuse the single paper owner and next-open fill path for explicit auto targets."""
        from kiwoom_monitor.application.position_target import (
            PositionTargetShadowRequest, evaluate_position_target_shadow, _json_value)
        from kiwoom_monitor.domain.order_contract import AccountSnapshot
        if not isinstance(request, PositionTargetShadowRequest) or request.target_policy is None:
            raise ValueError('paper target execution requires an explicit automatic sizing policy')
        valuation = self.observe_position_value(source_bar)
        at = _aware_datetime(source_bar.available_at)
        if (request.account_ref != 'research-single-position' or request.symbol != valuation.symbol
                or request.decision_time != at or request.reference_price_available_at != at
                or request.reference_price_won != source_bar.close or request.cost_model != self.execution.cost_model):
            raise ValueError('paper target request does not match its native source cut/cost')
        if self.portfolio.pending_order is not None:
            raise ValueError('paper target cannot replace or duplicate a pending order')
        if request.action == 'REENTER' and self.strategy_state.status != 'cooldown':
            raise ValueError('paper reentry requires a preceding closed position')
        if request.action in {'ADD','REENTER'}:
            reason = session_entry_time_rejection(self.strategy, at)
            if reason: raise ValueError(reason)
        account = AccountSnapshot('research-single-position',self.portfolio.cash_won,self.portfolio.reserved_cash_won,
            {valuation.symbol:valuation.position_quantity_after} if valuation.position_quantity_after else {},at)
        result = evaluate_position_target_shadow(request,account,())
        if result['quality']['status'] != 'CALCULATED' or not result['incremental_quantity']:
            raise ValueError('paper target quantity is blocked or already satisfied')
        side, quantity = result['incremental_side'], result['incremental_quantity']
        intent_id = _content_id('intent',{'run_id':self.run_id,'decision_id':result['decision_id'],
                                        'side':side,'quantity':quantity})
        reserved = self._estimated_buy_cash(source_bar.close,quantity) if side == 'BUY' else 0
        pending = PendingOrder(intent_id,result['decision_id'],request.symbol,side,quantity,
            at.isoformat(),at.isoformat(),reserved,self.session_profile,source_bar.research_session,
            request.target_policy.cash_buffer_won,POSITION_TARGET_EXECUTION_VERSION)
        self.portfolio = replace(self.portfolio,reserved_cash_won=reserved,pending_order=pending)
        proof = json.dumps({'version':POSITION_TARGET_EXECUTION_VERSION,'request':asdict(request)},
                           default=_json_value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        return (self._event(intent_id=intent_id,decision_id=result['decision_id'],event_type='ORDER_SUBMITTED',
            state='PENDING',symbol=request.symbol,side=side,occurred_at=at.isoformat(),received_at=at.isoformat(),
            quantity=quantity,price=0,notional=0,commission=0,tax=0,realized=0,
            source_revision_id=source_bar.revision_id,optimistic_exit_price=None,reason=proof),)

    def _pending_bar_rejection(self, pending: PendingOrder, bar: KrxMinuteBarFrame) -> str:
        if self.strategy.session_exit_policy:
            bar_start = _aware_datetime(bar.bar_start)
            if bar_start >= regular_session_fill_deadline(_aware_datetime(pending.submitted_at)):
                return "regular_session_fill_deadline_passed"
            reason = regular_session_time_rejection(bar_start)
            if reason:
                return reason
            if pending.side == "BUY":
                reason = session_entry_time_rejection(self.strategy, bar_start, include_signal_validity=False)
                if reason:
                    return reason
        if self.session_profile is None:
            return ""
        submitted = _aware_datetime(pending.eligible_after if pending.limit_execution_version else pending.submitted_at).astimezone(timezone.utc)
        expected_start = submitted.replace(second=0, microsecond=0)
        if submitted > expected_start:
            expected_start += timedelta(minutes=1)
        bar_start = _aware_datetime(bar.bar_start).astimezone(timezone.utc)
        if pending.research_session and bar.research_session != pending.research_session:
            return "session_boundary_before_fill"
        if bar_start != expected_start:
            return "next_tradable_bar_gap"
        if bar.market_phase != MarketPhase.CONTINUOUS.value:
            return "minute_bar_phase_cannot_prove_next_open_fill"
        return ""

    def _limit_open_crosses(self, pending, bar):
        price=_slipped_price(bar.open,pending.side,self.execution.cost_model.slippage_bps)
        return price<=pending.limit_price_won if pending.side=='BUY' else price>=pending.limit_price_won

    def process_unfilled_action(self, request: UnfilledResearchAction, *, source_bar: KrxMinuteBarFrame):
        """Opt-in LIMIT branch using the existing portfolio, costs, fills and writer."""
        if not isinstance(request,UnfilledResearchAction):
            raise ValueError('typed unfilled research action required')
        valuation=self.observe_position_value(source_bar)
        cost=self.execution.cost_model
        at=_aware_datetime(source_bar.available_at)
        held=valuation.position_quantity_after
        owned=self.portfolio.pending_order
        if (request.decision_time!=at or request.symbol!=valuation.symbol or not source_bar.research_session or cost is None
                or not cost.source.strip() or cost.rate_basis=='unspecified' or not cost.valid_from
                or not _aware_datetime(cost.valid_from)<=at<_aware_datetime(cost.valid_to)
                or abs(request.target_quantity-held)!=request.quantity
                or (request.target_quantity>held)!=(request.side=='BUY')):
            raise ValueError('unfilled action does not match native cut/target/cost')
        if request.side=='BUY':
            rejection=session_entry_time_rejection(self.strategy,at)
            if rejection: raise ValueError(rejection)
        if request.action=='SUBMIT':
            if owned is not None: raise ValueError('pending order already exists')
        elif (owned is None or owned.limit_execution_version!=UNFILLED_EXECUTION_VERSION
                or (owned.symbol,owned.side,owned.quantity,owned.limit_price_won)!=(
                    request.symbol,request.side,request.quantity,request.existing_limit_price_won)):
            raise ValueError('unfilled action requires its native pending LIMIT order')
        limit=(request.candidate_limit_price_won if request.action=='REPRICE' else request.existing_limit_price_won)
        reserved=(limit*request.quantity+_fee_won(limit*request.quantity,cost.commission_bps)
                  if request.side=='BUY' else 0)
        if request.action!='WAIT' and reserved>self.portfolio.cash_won:
            raise ValueError('unfilled LIMIT reservation exceeds native cash')
        before=asdict(self.portfolio)
        proof={'version':UNFILLED_EXECUTION_VERSION,'request':request.to_dict(),
               'initial_portfolio':before,'initial_strategy_state':asdict(self.strategy_state),
               'source_bar':asdict(source_bar)}
        reason=json.dumps(proof,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        decision_id=_content_id('unfilled-decision',{'run_id':self.run_id,'proof':proof})
        events=[self._event(intent_id=owned.intent_id if owned else '',decision_id=decision_id,
            event_type='UNFILLED_BASELINE',state='PENDING' if owned else 'READY',symbol=request.symbol,
            side=request.side,occurred_at=source_bar.bar_end,received_at=source_bar.available_at,
            quantity=request.quantity,price=source_bar.close,notional=0,commission=0,tax=0,realized=0,
            source_revision_id=source_bar.revision_id,optimistic_exit_price=None,reason=reason)]
        if request.action=='WAIT': return tuple(events)
        if owned is not None:
            self.portfolio=replace(self.portfolio,reserved_cash_won=0,pending_order=None)
            events.append(self._event(intent_id=owned.intent_id,decision_id=decision_id,event_type='ORDER_CANCELLED',
                state='CANCELLED',symbol=owned.symbol,side=owned.side,occurred_at=at.isoformat(),
                received_at=source_bar.available_at,quantity=owned.quantity,price=0,notional=0,
                commission=0,tax=0,realized=0,source_revision_id=source_bar.revision_id,
                optimistic_exit_price=None,reason='research_zero_latency_limit_cancel'))
        intent_id=_content_id('intent',{'run_id':self.run_id,'decision_id':decision_id,'limit':limit})
        pending=PendingOrder(intent_id,decision_id,request.symbol,request.side,request.quantity,
            at.isoformat(),at.isoformat(),reserved,self.session_profile,source_bar.research_session,
            limit_price_won=limit,limit_execution_version=UNFILLED_EXECUTION_VERSION)
        self.portfolio=replace(self.portfolio,reserved_cash_won=reserved,pending_order=pending)
        events.append(self._event(intent_id=intent_id,decision_id=decision_id,event_type='ORDER_SUBMITTED',state='PENDING',
            symbol=request.symbol,side=request.side,occurred_at=at.isoformat(),received_at=source_bar.available_at,
            quantity=request.quantity,price=limit,notional=0,commission=0,tax=0,realized=0,
            source_revision_id=source_bar.revision_id,optimistic_exit_price=None,reason=reason))
        return tuple(events)

    def observe_position_study_case(self,study_ref,study,case,*,source_bar:KrxMinuteBarFrame,historical_context=None):
        """Freeze actual Paper state/goal on the existing event writer."""
        from kiwoom_monitor.application.position_target import (
            validate_position_action_study_case,evaluate_position_target_shadow,_json_value)
        from kiwoom_monitor.domain.order_contract import AccountSnapshot
        validate_position_action_study_case(study,case)
        request=case.request;at=_aware_datetime(source_bar.available_at)
        if (request.symbol != source_bar.code or request.decision_time != at
                or request.reference_price_available_at != at or request.reference_price_won != source_bar.close
                or request.cost_model != self.execution.cost_model or self.portfolio.pending_order is not None
                or self.portfolio.reserved_cash_won or self._last_processed_bar != source_bar):
            raise ValueError('native position study cut/state mismatch')
        account=AccountSnapshot('research-single-position',self.portfolio.cash_won,0,
            {source_bar.code:self.portfolio.position.quantity} if self.portfolio.position else {},at)
        goal=evaluate_position_target_shadow(request,account,())
        if goal['quality']['status'] != 'CALCULATED' or goal['funding_shortfall_quantity'] or not goal['incremental_quantity']:
            raise ValueError('native study goal is blocked or not fully funded')
        proof={'version':'position-action-study-case/v2','study_policy_ref':study_ref,'case':case.to_dict(),
            'source_bar':asdict(source_bar),'portfolio':asdict(self.portfolio),'strategy_state':asdict(self.strategy_state),
            'goal':goal}
        if historical_context is not None:proof['historical_context']=historical_context
        encoded=json.dumps(proof,default=_json_value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        if len(encoded.encode()) > 256*1024:raise ValueError('native study proof exceeds 256 KiB')
        return self._event(intent_id='',decision_id=goal['decision_id'],event_type='POSITION_STUDY_CASE',state='OBSERVED',
            symbol=source_bar.code,side='',occurred_at=source_bar.bar_end,received_at=source_bar.available_at,
            quantity=0,price=source_bar.close,notional=0,commission=0,tax=0,realized=0,
            source_revision_id=source_bar.revision_id,optimistic_exit_price=None,reason=encoded)

    def observe_unfilled_bar(self, bar: KrxMinuteBarFrame):
        """Offline plan lineage on the existing event writer, never a live collector."""
        proof={'version':UNFILLED_EXECUTION_VERSION,'source_bar':asdict(bar)}
        return self._event(intent_id='',decision_id='',event_type='UNFILLED_BAR',state='OBSERVED',
            symbol=bar.code,side='',occurred_at=bar.bar_end,received_at=bar.available_at,
            quantity=0,price=bar.close,notional=0,commission=0,tax=0,realized=0,
            source_revision_id=bar.revision_id,optimistic_exit_price=None,
            reason=json.dumps(proof,ensure_ascii=False,sort_keys=True,separators=(',',':')))

    def _discard_pending(
        self, pending: PendingOrder, bar: KrxMinuteBarFrame, reason: str,
    ) -> ExecutionEvent:
        unsupported = reason == "minute_bar_phase_cannot_prove_next_open_fill"
        self.portfolio = SimulationPortfolio(
            cash_won=self.portfolio.cash_won, reserved_cash_won=0,
            position=self.portfolio.position, pending_order=None,
            realized_pnl_won=self.portfolio.realized_pnl_won,
            total_cost_won=self.portfolio.total_cost_won,
        )
        if pending.side == "BUY" and self.portfolio.position is None:
            self.strategy_state = StrategyState(
                emitted_candidate_keys=self.strategy_state.emitted_candidate_keys,
            )
        return self._event(
            intent_id=pending.intent_id, decision_id=pending.decision_id,
            event_type="ORDER_UNSUPPORTED" if unsupported else "ORDER_CENSORED",
            state="UNSUPPORTED" if unsupported else "CENSORED",
            symbol=pending.symbol, side=pending.side, occurred_at=bar.bar_start,
            received_at=bar.available_at, quantity=pending.quantity, price=0,
            notional=0, commission=0, tax=0, realized=0,
            source_revision_id=(bar.revision_id if normalize_stock_code(bar.code) == pending.symbol else ""),
            optimistic_exit_price=None, reason=reason,
        )

    def finalize(self, ended_at: str) -> tuple[ExecutionEvent, ...]:
        events: list[ExecutionEvent] = []
        pending = self.portfolio.pending_order
        if pending is not None:
            self.portfolio = SimulationPortfolio(
                cash_won=self.portfolio.cash_won,
                reserved_cash_won=0,
                position=self.portfolio.position,
                pending_order=None,
                realized_pnl_won=self.portfolio.realized_pnl_won,
                total_cost_won=self.portfolio.total_cost_won,
            )
            if pending.side == "BUY" and self.portfolio.position is None:
                self.strategy_state = StrategyState(
                    emitted_candidate_keys=self.strategy_state.emitted_candidate_keys,
                )
            events.append(self._event(
                intent_id=pending.intent_id, decision_id=pending.decision_id,
                event_type="ORDER_CENSORED", state="CENSORED", symbol=pending.symbol,
                side=pending.side, occurred_at=ended_at, received_at=ended_at,
                quantity=pending.quantity, price=0, notional=0, commission=0, tax=0,
                realized=0, source_revision_id="", optimistic_exit_price=None,
                reason="research_interval_ended_before_fill",
            ))
        if self.portfolio.position is not None:
            position = self.portfolio.position
            events.append(self._event(
                intent_id=position.entry_intent_id, decision_id="",
                event_type="POSITION_CENSORED", state="OPEN", symbol=position.symbol,
                side="", occurred_at=ended_at, received_at=ended_at,
                quantity=position.quantity, price=0, notional=0, commission=0, tax=0,
                realized=0, source_revision_id="", optimistic_exit_price=None,
                reason="research_interval_ended_with_open_position",
            ))
        return tuple(events)

    def _fill_pending(self, pending: PendingOrder, bar: KrxMinuteBarFrame) -> ExecutionEvent:
        cost = self.execution.cost_model
        if cost is None:
            self.portfolio = SimulationPortfolio(
                cash_won=self.portfolio.cash_won, reserved_cash_won=0,
                position=self.portfolio.position, pending_order=None,
                realized_pnl_won=self.portfolio.realized_pnl_won,
                total_cost_won=self.portfolio.total_cost_won,
            )
            return self._event(
                intent_id=pending.intent_id, decision_id=pending.decision_id,
                event_type="ORDER_UNSUPPORTED", state="UNSUPPORTED", symbol=pending.symbol,
                side=pending.side, occurred_at=bar.bar_start, received_at=bar.available_at,
                quantity=pending.quantity, price=0, notional=0, commission=0, tax=0,
                realized=0, source_revision_id=bar.revision_id, optimistic_exit_price=None,
                reason="cost_model_missing",
            )
        price = _slipped_price(bar.open, pending.side, cost.slippage_bps)
        notional = price * pending.quantity
        commission = _fee_won(notional, cost.commission_bps)
        tax = _fee_won(notional, cost.sell_tax_bps) if pending.side == "SELL" else 0
        if pending.side == "BUY":
            required = notional + commission
            if required > self.portfolio.cash_won - pending.cash_buffer_won:
                self.portfolio = SimulationPortfolio(
                    cash_won=self.portfolio.cash_won, reserved_cash_won=0,
                    position=self.portfolio.position, pending_order=None,
                    realized_pnl_won=self.portfolio.realized_pnl_won,
                    total_cost_won=self.portfolio.total_cost_won,
                )
                if self.portfolio.position is None:
                    self.strategy_state = StrategyState(
                        emitted_candidate_keys=self.strategy_state.emitted_candidate_keys,
                    )
                return self._event(
                    intent_id=pending.intent_id, decision_id=pending.decision_id,
                    event_type="ORDER_CANCELLED", state="CANCELLED", symbol=pending.symbol,
                    side="BUY", occurred_at=bar.bar_start, received_at=bar.available_at,
                    quantity=pending.quantity, price=price, notional=notional,
                    commission=commission, tax=0, realized=0,
                    source_revision_id=bar.revision_id, optimistic_exit_price=None,
                    reason="fill_cash_exceeded",
                )
            position = SimulationPosition(
                symbol=pending.symbol, quantity=pending.quantity, entry_price=price,
                entry_notional=notional, entry_commission=commission,
                opened_at=bar.bar_start, entry_intent_id=pending.intent_id,
            )
            previous = self.portfolio.position
            if previous is not None:
                quantity = previous.quantity + pending.quantity
                notional_basis = previous.entry_notional + notional
                position = SimulationPosition(previous.symbol,quantity,notional_basis // quantity,
                    notional_basis,previous.entry_commission + commission,previous.opened_at,previous.entry_intent_id)
            self.portfolio = SimulationPortfolio(
                cash_won=self.portfolio.cash_won - required, reserved_cash_won=0,
                position=position, pending_order=None,
                realized_pnl_won=self.portfolio.realized_pnl_won,
                total_cost_won=self.portfolio.total_cost_won + commission,
            )
            self.strategy_state = StrategyState(
                status="open", symbol=pending.symbol, entry_price=position.entry_price,
                position_quantity=position.quantity, opened_at=position.opened_at,
                emitted_candidate_keys=self.strategy_state.emitted_candidate_keys,
            )
            return self._event(
                intent_id=pending.intent_id, decision_id=pending.decision_id,
                event_type="FILL", state="FILLED", symbol=pending.symbol, side="BUY",
                occurred_at=bar.bar_start, received_at=bar.available_at,
                quantity=pending.quantity, price=price, notional=notional,
                commission=commission, tax=0, realized=0,
                source_revision_id=bar.revision_id, optimistic_exit_price=None, reason="",
            )
        return self._sell_fill(
            pending.intent_id, pending.decision_id, bar, price, pending.quantity,
            event_type="FILL", reason="",
        )

    def _apply_intrabar_risk(self, bar: KrxMinuteBarFrame) -> ExecutionEvent | None:
        position = self.portfolio.position
        if position is None:
            return None
        stop_price = position.entry_price * (10_000 - self.strategy.stop_loss_bps) // 10_000
        target_price = (
            position.entry_price * (10_000 + self.strategy.target_bps) + 9_999
        ) // 10_000
        stop_hit = bar.low <= stop_price
        target_hit = bar.high >= target_price
        if not stop_hit and not target_hit:
            return None
        base_price = stop_price if stop_hit else target_price
        reason = "stop_and_target_same_bar_conservative_stop" if stop_hit and target_hit else (
            "stop_triggered" if stop_hit else "target_triggered"
        )
        optimistic = target_price if stop_hit and target_hit else None
        cost = self.execution.cost_model
        if cost is None:
            return None
        price = _slipped_price(base_price, "SELL", cost.slippage_bps)
        event = self._sell_fill(
            _content_id("risk_intent", {
                "run_id": self.run_id, "entry_intent_id": position.entry_intent_id,
                "bar_revision_id": bar.revision_id, "reason": reason,
            }),
            "", bar, price, position.quantity, event_type="RISK_FILL", reason=reason,
            optimistic_exit_price=optimistic,
        )
        return event

    def _sell_fill(
        self,
        intent_id: str,
        decision_id: str,
        bar: KrxMinuteBarFrame,
        price: int,
        quantity: int,
        *,
        event_type: str,
        reason: str,
        optimistic_exit_price: int | None = None,
    ) -> ExecutionEvent:
        position = self.portfolio.position
        cost = self.execution.cost_model
        assert position is not None and cost is not None
        quantity = min(quantity, position.quantity)
        notional = price * quantity
        commission = _fee_won(notional, cost.commission_bps)
        tax = _fee_won(notional, cost.sell_tax_bps)
        proceeds = notional - commission - tax
        notional_basis = position.entry_notional * quantity // position.quantity
        entry_basis = notional_basis + (
            position.entry_commission * quantity // position.quantity
        )
        realized = proceeds - entry_basis
        remaining = position.quantity - quantity
        remaining_position = None if remaining == 0 else SimulationPosition(
            symbol=position.symbol, quantity=remaining, entry_price=position.entry_price,
            entry_notional=position.entry_notional - notional_basis,
            entry_commission=position.entry_commission - (
                position.entry_commission * quantity // position.quantity
            ),
            opened_at=position.opened_at, entry_intent_id=position.entry_intent_id,
        )
        self.portfolio = SimulationPortfolio(
            cash_won=self.portfolio.cash_won + proceeds, reserved_cash_won=0,
            position=remaining_position, pending_order=None,
            realized_pnl_won=self.portfolio.realized_pnl_won + realized,
            total_cost_won=self.portfolio.total_cost_won + commission + tax,
        )
        if remaining == 0:
            fill_time = bar.bar_end if event_type == "RISK_FILL" else bar.bar_start
            ended = _aware_datetime(fill_time).astimezone(timezone.utc)
            self.strategy_state = StrategyState(
                status="cooldown", cooldown_until=(
                    ended + timedelta(seconds=self.strategy.cooldown_seconds)
                ).isoformat(),
                emitted_candidate_keys=self.strategy_state.emitted_candidate_keys,
            )
        else:
            self.strategy_state = replace(self.strategy_state,position_quantity=remaining)
        return self._event(
            intent_id=intent_id, decision_id=decision_id, event_type=event_type,
            state="FILLED", symbol=position.symbol, side="SELL",
            occurred_at=bar.bar_end if event_type == "RISK_FILL" else bar.bar_start,
            received_at=bar.available_at, quantity=quantity, price=price,
            notional=notional, commission=commission, tax=tax, realized=realized,
            source_revision_id=bar.revision_id,
            optimistic_exit_price=optimistic_exit_price, reason=reason,
        )

    def _mark(self, bar: KrxMinuteBarFrame) -> ExecutionEvent:
        position = self.portfolio.position
        assert position is not None
        return self._event(
            intent_id=position.entry_intent_id, decision_id="", event_type="MARK",
            state="OPEN", symbol=position.symbol, side="", occurred_at=bar.bar_end,
            received_at=bar.available_at, quantity=position.quantity, price=bar.close,
            notional=position.quantity * bar.close, commission=0, tax=0, realized=0,
            source_revision_id=bar.revision_id, optimistic_exit_price=None, reason="",
        )

    def _notice(
        self, decision: StrategyDecision, side: str, event_type: str, reason: str,
    ) -> ExecutionEvent:
        return self._event(
            intent_id=_content_id("intent", {
                "run_id": self.run_id, "decision_id": decision.decision_id, "side": side,
            }),
            decision_id=decision.decision_id, event_type=event_type, state="REJECTED",
            symbol=decision.symbol, side=side, occurred_at=decision.decided_at,
            received_at=decision.decided_at, quantity=decision.quantity,
            price=0, notional=0, commission=0, tax=0, realized=0,
            source_revision_id="", optimistic_exit_price=None, reason=reason,
        )

    def _estimated_buy_cash(self, reference_price: int, quantity: int) -> int:
        return estimate_buy_cash_won(reference_price, quantity, self.execution.cost_model)

    def _event(
        self,
        *,
        intent_id: str,
        decision_id: str,
        event_type: str,
        state: str,
        symbol: str,
        side: str,
        occurred_at: str,
        received_at: str,
        quantity: int,
        price: int,
        notional: int,
        commission: int,
        tax: int,
        realized: int,
        source_revision_id: str,
        optimistic_exit_price: int | None,
        reason: str,
    ) -> ExecutionEvent:
        body = {
            "run_id": self.run_id, "execution_environment": "simulation",
            "account_ref": "research-single-position", "intent_id": intent_id,
            "decision_id": decision_id, "event_type": event_type, "state": state,
            "symbol": normalize_stock_code(symbol), "side": side,
            "occurred_at": occurred_at, "received_at": received_at,
            "quantity": quantity, "price": price, "notional_won": notional,
            "commission_won": commission, "tax_won": tax,
            "total_cost_won": commission + tax, "realized_pnl_won": realized,
            "cash_after_won": self.portfolio.cash_won,
            "reserved_cash_after_won": self.portfolio.reserved_cash_won,
            "position_quantity_after": (
                self.portfolio.position.quantity if self.portfolio.position else 0
            ),
            "source_revision_id": source_revision_id,
            "optimistic_exit_price": optimistic_exit_price, "reason": reason,
        }
        return ExecutionEvent(event_id=_content_id("execution", body), **body)


def estimate_buy_cash_won(
    reference_price: int, quantity: int, cost: SimulationCostModel | None,
) -> int:
    """Existing research reservation arithmetic, also used by order-free targets."""
    if cost is None:
        return reference_price * quantity
    price = _slipped_price(reference_price, "BUY", cost.slippage_bps)
    notional = price * quantity
    return notional + _fee_won(notional, cost.commission_bps)


def _fee_won(notional: int, rate_bps: int | Decimal) -> int:
    return int((Decimal(notional) * rate_bps / 10_000).to_integral_value(rounding=ROUND_DOWN))


def _slipped_price(price: int, side: str, slippage_bps: int | Decimal) -> int:
    if side == "BUY":
        return int((Decimal(price) * (10_000 + slippage_bps) / 10_000)
                   .to_integral_value(rounding=ROUND_CEILING))
    return int((Decimal(price) * (10_000 - slippage_bps) / 10_000)
               .to_integral_value(rounding=ROUND_DOWN))


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def _aware_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("execution timestamps must be timezone-aware")
    return parsed
