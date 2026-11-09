from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from dataclasses import asdict, replace
import json
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
from kiwoom_monitor.application.market_session_schedule import CURRENT_SCHEDULE_VERSION
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime, ManualMockOrderGateway
from kiwoom_monitor.domain.order_contract import (
    AccountSnapshot, BrokerFill, BrokerOrderSnapshot, BrokerSubmission,
    OrderEvent, OrderIntent, OrderSide, OrderState, OrderType,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionRejected, SubmissionUnknown
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository


NOW = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)


class _Transport:
    def __init__(self, submit_error: Exception | None = None,
                 cancel_error: Exception | None = None) -> None:
        self.submit_error = submit_error
        self.cancel_error = cancel_error
        self.submit_calls = 0
        self.cancel_calls = 0

    def submit(self, _intent):
        self.submit_calls += 1
        if self.submit_error:
            raise self.submit_error
        return BrokerSubmission("broker-1", NOW)

    def cancel(self, _intent, _broker_order_id, _quantity=0):
        self.cancel_calls += 1
        if self.cancel_error:
            raise self.cancel_error
        return BrokerSubmission("cancel-1", NOW)


def _intent(expires_at: datetime | None = None) -> OrderIntent:
    return OrderIntent(
        "intent-1", "run-1", "decision-1", "account-1", "mock", "005930", "KRX",
        OrderSide.BUY, 3, OrderType.LIMIT, 70_000, NOW - timedelta(seconds=1),
        expires_at or NOW + timedelta(minutes=1), "policy-v1",
    )


def _account(account_ref: str = "account-1") -> AccountSnapshot:
    return AccountSnapshot(account_ref, 1_000_000, 0, {}, NOW - timedelta(seconds=1))


class UnfilledOrderManagementTests(unittest.TestCase):
    def setUp(self):
        from kiwoom_monitor.application.order_lifecycle import evaluate_unfilled_order_management_shadow
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.case=0;self.evaluate=evaluate_unfilled_order_management_shadow
        self.setup_order()

    def setup_order(self,side='BUY',purpose=None,account_ref='account-1',run_id=None):
        from kiwoom_monitor.application.order_lifecycle import UnfilledOrderManagementPolicy,UnfilledOrderAssessment,UnfilledRepriceEvidence
        from kiwoom_monitor.application.position_target import PositionAccountObservation,PositionTargetShadowRequest
        from tests.unit.test_entry_thesis import PositionTargetShadowTests
        self.case+=1;self.now=[NOW+timedelta(seconds=1)]
        self.store=SQLiteQueryStore(Path(self.directory.name)/f'case-{self.case}.sqlite3');self.store.initialize()
        self.addCleanup(self.store.close)
        self.repository=ExecutionRepository(self.store);self.transport=_Transport()
        self.lifecycle=OrderLifecycle(self.repository,self.transport,now_provider=lambda:self.now[0])
        self.side=OrderSide(side);self.purpose=purpose or ('BUY_OPPORTUNITY' if side=='BUY' else 'TAKE_PROFIT')
        self.original_held=0 if side=='BUY' else 10
        base=_intent()
        self.intent=replace(base,side=self.side,quantity=10 if side=='BUY' or self.purpose=='PROTECTIVE_EXIT' else 5,limit_price=1000,
            account_ref=account_ref,run_id=run_id or base.run_id)
        self.lifecycle.queue(self.intent)
        initial=AccountSnapshot(account_ref,100_000,0,{'005930':self.original_held},NOW)
        self.lifecycle.submit(self.intent.intent_id,initial)
        self.snapshot=BrokerOrderSnapshot('broker-1',account_ref,'005930',OrderState.ACCEPTED,0,self.intent.quantity,self.now[0])
        self.record=self.lifecycle.reconcile(self.intent.intent_id,self.snapshot)
        account=replace(initial,as_of=self.now[0],reserved_open_buy_won=10_000 if side=='BUY' else 0)
        self.observation=PositionAccountObservation(account,(self.record,),'account-orders:1',self.now[0],True,True,())
        costs=PositionTargetShadowTests();costs.setUp()
        from kiwoom_monitor.application.research_evaluation import position_action_cost_model_ref
        self.target=PositionTargetShadowRequest(account_ref,'mock','KRX','005930',
            'ADD' if side=='BUY' else 'EXIT' if self.purpose=='PROTECTIVE_EXIT' else 'REDUCE',
            10 if side=='BUY' else 0 if self.purpose=='PROTECTIVE_EXIT' else 5,
            1000,self.now[0],self.now[0],'target/v1','source-position-decision','account-orders:1','account-orders:1',20,True,True,costs.cost)
        self.policy=UnfilledOrderManagementPolicy('unfilled-purpose-policy/v1',self.purpose,'intent-1',1000,
            'original-price',self.intent.created_at,NOW+timedelta(seconds=60),NOW+timedelta(seconds=30),
            NOW+timedelta(seconds=15),100,300,3,20,9,1,2,10)
        self.assessment=UnfilledOrderAssessment(True,True,True,False,'thesis:1',NOW,NOW+timedelta(seconds=60),
            'risk:1',NOW,NOW+timedelta(seconds=60),'TRADABLE',1010 if side=='BUY' else 990,'current-price',self.now[0],0,
            UnfilledRepriceEvidence('unfilled-WAIT-vs-REPRICE/v1','unfilled-ev:1','paired-unfilled-cases','intent-1',
                self.intent.quantity,1010 if side=='BUY' else 990,10,self.now[0],2,20,10,position_action_cost_model_ref(costs.cost.to_dict())))

    def plan(self,**changes):
        values={'policy':self.policy,'record':self.record,'broker_snapshot':self.snapshot,'target':self.target,
            'observation':self.observation,'assessment':self.assessment};values.update(changes)
        return self.evaluate(**values)

    def cancel_and_reconcile(self,filled=0):
        from kiwoom_monitor.application.position_target import PositionAccountObservation
        if self.record.state is not OrderState.CANCEL_PENDING:
            self.record=self.lifecycle.cancel(self.intent.intent_id)
        self.now[0]+=timedelta(seconds=1)
        fills=(BrokerFill('cancel-race-fill',filled,1000,self.now[0]),) if filled else ()
        self.snapshot=BrokerOrderSnapshot('broker-1',self.intent.account_ref,'005930',OrderState.CANCELLED,filled,0,self.now[0],fills)
        self.record=self.lifecycle.reconcile('intent-1',self.snapshot)
        held=filled if self.side is OrderSide.BUY else self.original_held-filled
        account=replace(self.observation.account,as_of=self.now[0],reserved_open_buy_won=0,positions={'005930':held})
        self.observation=PositionAccountObservation(account,(),'account-orders:2',self.now[0],True,True,())
        self.target=replace(self.target,decision_time=self.now[0],reference_price_available_at=self.now[0],
            account_input_ref='account-orders:2',orders_input_ref='account-orders:2')
        evidence=self.assessment.reprice_evidence
        self.assessment=replace(self.assessment,price_available_at=self.now[0],
            reprice_evidence=replace(evidence,pending_quantity=self.intent.quantity-filled,available_at=self.now[0])
                if evidence and filled < self.intent.quantity else None)

    def document(self):
        from kiwoom_monitor.application.order_lifecycle import UNFILLED_MANAGEMENT_SHADOW_VERSION
        from kiwoom_monitor.application.position_target import _json_value
        return json.loads(json.dumps({'schema':UNFILLED_MANAGEMENT_SHADOW_VERSION,'policy':asdict(self.policy),
            'record':asdict(self.record),'broker_snapshot':asdict(self.snapshot),'target':asdict(self.target),
            'account':asdict(self.observation.account),'open_orders':[asdict(r) for r in self.observation.open_orders],
            'assessment':asdict(self.assessment)},default=_json_value))

    def test_supported_buy_reprice_proposes_cancel_and_never_replacement_before_confirmation(self):
        result=self.plan()
        self.assertEqual(('CANCEL','REPRICE',None,False),tuple(result[k] for k in ('action','after_cancel','replacement_quantity','order_authorized')))
        self.assertEqual(10,result['penalized_net_advantage_won'])
        self.assertEqual((1,0),(self.transport.submit_calls,self.transport.cancel_calls))

    def test_unknown_ev_only_allows_bounded_existing_order_wait(self):
        result=self.plan(assessment=replace(self.assessment,reprice_evidence=None),policy=replace(self.policy,maximum_reprices=0))
        self.assertEqual(('WAIT','EV_UNAVAILABLE'),(result['action'],result['ev_status']))
        for change in ({'risk_allows_new_risk':False},{'thesis_valid':False},{'protection_required':True}):
            result=self.plan(assessment=replace(self.assessment,**change))
            self.assertEqual(('CANCEL','ABANDON'),(result['action'],result['after_cancel']))

    def test_queued_unknown_and_cancel_ack_never_send_or_plan_replacement(self):
        for state in (OrderState.QUEUED,OrderState.SUBMISSION_UNKNOWN,OrderState.CANCEL_PENDING):
            result=self.plan(record=replace(self.record,state=state))
            self.assertEqual(('WAIT',None,False),tuple(result[k] for k in ('action','replacement_quantity','order_authorized')))
        self.record=self.lifecycle.cancel('intent-1')
        result=self.plan()
        self.assertIn('cancel_ack_is_not_cancellation_confirmation',result['reasons'])
        self.assertEqual((1,1),(self.transport.submit_calls,self.transport.cancel_calls))

    def test_native_cancel_race_partial_fill_recomputes_current_target_and_cost(self):
        self.cancel_and_reconcile(3)
        result=self.plan()
        self.assertEqual(('REPRICE',7,1010,False),tuple(result[k] for k in ('action','replacement_quantity','replacement_limit_price_won','order_authorized')))
        self.assertEqual(3,self.observation.account.positions['005930'])
        self.assertEqual(1,len([e for e in self.repository.events('intent-1') if e['event_type']=='FILL']))
        before=self.repository.events('intent-1')
        self.lifecycle.reconcile('intent-1',self.snapshot)
        self.assertEqual(before,self.repository.events('intent-1'))
        self.assertEqual((1,1),(self.transport.submit_calls,self.transport.cancel_calls))

    def test_cancel_with_only_aggregate_fill_is_not_ready_for_replacement(self):
        self.cancel_and_reconcile(3)
        result=self.plan(record=replace(self.record,detailed_filled_quantity=0,fill_ids=()))
        self.assertEqual('WAIT',result['action'])
        self.assertIn('cancel_race_fills_not_fully_reconciled',result['reasons'])

    def test_full_fill_during_cancel_completes_original_order_without_replacement(self):
        self.cancel_and_reconcile(10)
        self.assertEqual(OrderState.FILLED,self.record.state)
        result=self.plan()
        self.assertEqual(('WAIT','COMPLETED',None),tuple(result[k] for k in ('action','status','replacement_quantity')))

    def test_liquidation_is_confirmed_only_with_flat_account_and_no_other_pending_exposure(self):
        self.setup_order('SELL','PROTECTIVE_EXIT');self.cancel_and_reconcile(10)
        result=self.plan()
        self.assertEqual((True,False),(result['liquidation_confirmed'],result['risk_path_required']))
        pending=replace(self.record,intent=replace(self.intent,intent_id='other-buy',side=OrderSide.BUY),
            state=OrderState.SUBMISSION_UNKNOWN,filled_quantity=0,detailed_filled_quantity=0,broker_reported_filled_quantity=0)
        result=self.plan(observation=replace(self.observation,open_orders=(pending,)))
        self.assertEqual((False,True),(result['liquidation_confirmed'],result['risk_path_required']))

    def test_protection_deadline_remains_unresolved_with_operator_attention(self):
        self.setup_order('SELL','PROTECTIVE_EXIT')
        policy=replace(self.policy,protection_deadline=NOW)
        result=self.plan(policy=policy,assessment=replace(self.assessment,reprice_evidence=None))
        self.assertEqual(('CANCEL',True,True,False),tuple(result[k] for k in
            ('action','risk_path_required','requires_operator_attention','liquidation_confirmed')))

    def test_cancel_unknown_after_restart_uses_existing_lifecycle_and_stays_unresolved(self):
        self.transport.cancel_error=SubmissionUnknown('lost cancellation ACK')
        self.record=self.lifecycle.cancel('intent-1')
        replacement=_Transport()
        restarted=OrderLifecycle(ExecutionRepository(self.store),replacement,now_provider=lambda:self.now[0])
        with self.assertRaises(ValueError):restarted.cancel('intent-1')
        result=self.plan(record=restarted._required('intent-1'))
        self.assertEqual('WAIT',result['action'])
        self.assertEqual((0,0),(replacement.submit_calls,replacement.cancel_calls))

    def test_cancel_rejection_keeps_original_exposure_and_is_not_reprice_permission(self):
        self.transport.cancel_error=SubmissionRejected('cancel rejected')
        self.record=self.lifecycle.cancel('intent-1')
        self.assertEqual(OrderState.ACCEPTED,self.record.state)
        result=self.plan()
        self.assertEqual(('CANCEL','REPRICE',None),tuple(result[k] for k in ('action','after_cancel','replacement_quantity')))

    def test_buy_abandon_does_not_erase_already_filled_position(self):
        self.cancel_and_reconcile(3)
        result=self.plan(assessment=replace(self.assessment,thesis_valid=False,protection_required=True))
        self.assertEqual(('ABANDON',True),(result['action'],result['risk_path_required']))
        self.assertEqual(3,result['inputs']['account']['positions']['005930'])

    def test_profit_wait_and_reprice_are_distinct_from_protective_ev_override(self):
        self.setup_order('SELL')
        self.assertEqual('WAIT',self.plan(assessment=replace(self.assessment,reprice_evidence=None))['action'])
        blocked=self.plan(assessment=replace(self.assessment,reprice_evidence=None,trend_allows_wait=False))
        self.assertEqual(('WAIT','BLOCKED'),(blocked['action'],blocked['status']))
        self.assertEqual(('CANCEL','REPRICE'),tuple(self.plan()[k] for k in ('action','after_cancel')))
        protected=self.plan(target=replace(self.target,action='EXIT',target_quantity=0),
            assessment=replace(self.assessment,protection_required=True,reprice_evidence=None,thesis_valid=None,trend_allows_wait=None))
        self.assertEqual(('PROTECTIVE_EXIT','CANCEL','REPRICE',True),tuple(protected[k] for k in ('purpose','action','after_cancel','risk_path_required')))

    def test_protective_exit_recomputes_actual_sellable_holding_after_cancel_race(self):
        self.setup_order('SELL','PROTECTIVE_EXIT');self.cancel_and_reconcile(4)
        result=self.plan(assessment=replace(self.assessment,reprice_evidence=None))
        self.assertEqual(('REPRICE',6,True,False),tuple(result[k] for k in ('action','replacement_quantity','risk_path_required','liquidation_confirmed')))

    def test_single_offline_ledger_buy_cancel_unknown_profit_and_protective_flat(self):
        """Synthetic broker responses, one original ledger; no live admission."""
        from kiwoom_monitor.application.position_target import (
            PositionAccountObservation,PositionTargetShadowRequest,PositionTargetSizingPolicy,evaluate_position_target_shadow)
        from tests.unit.test_entry_thesis import DynamicPositionAllocationTests
        allocation=DynamicPositionAllocationTests();allocation.setUp()
        initial=AccountSnapshot(self.intent.account_ref,100_000,0,{},NOW)
        policy=replace(allocation.policy,account_ref=initial.account_ref,operating_limit_won=10_000,
            max_symbol_weight_bps=10000,max_theme_weight_bps=10000,available_at=NOW-timedelta(seconds=1),valid_until=NOW+timedelta(seconds=60))
        candidates=(replace(allocation.a,available_at=NOW),replace(allocation.b,available_at=NOW,net_ev_bps=-50))
        upper=allocation.evaluate(policy,candidates,PositionAccountObservation(initial,(),'off-initial',NOW,True,True,()),
            decision_time=NOW,cost_model=self.target.cost_model,risk_disposition='ALLOW',risk_input_ref='off-supplied-risk')
        chosen=next(row for row in upper['allocations'] if row['new_buy_quantity'])
        self.assertEqual(('005930',10),(chosen['symbol'],chosen['new_buy_quantity']))
        self.assertEqual(0,next(row for row in upper['allocations'] if row['symbol']=='000660')['new_buy_quantity'])
        raw=dict(chosen['target_policy'])
        for key in ('available_at','valid_until'):raw[key]=datetime.fromisoformat(raw[key])
        self.target=PositionTargetShadowRequest(initial.account_ref,'mock','KRX',chosen['symbol'],'REENTER',None,
            candidates[0].reference_price_won,NOW,NOW,upper['policy_version'],upper['decision_id'],'off-initial','off-initial',
            30,True,True,self.target.cost_model,PositionTargetSizingPolicy(**raw))
        lower=evaluate_position_target_shadow(self.target,initial,())
        self.assertEqual(('BUY',10),(lower['incremental_side'],lower['incremental_quantity']))
        self.assertFalse(upper['order_authorized']);self.assertFalse(lower['order_authorized'])
        # Use a fresh fixture ledger; the original setUp ledger is left intact.
        self.store=SQLiteQueryStore(Path(':memory:'));self.store.initialize();self.addCleanup(self.store.close)
        self.repository=ExecutionRepository(self.store);self.transport=_Transport()
        self.lifecycle=OrderLifecycle(self.repository,self.transport,now_provider=lambda:self.now[0])
        from kiwoom_monitor.application.research_execution import _fee_won
        trade_ids=[]
        def account_from_fills():
            cash=initial.available_cash_won;held=0
            for identity in trade_ids:
                native=self.repository.load(identity);buy=native.intent.side is OrderSide.BUY
                for event in self.repository.events(identity):
                    if event['event_type']!='FILL':continue
                    amount=event['quantity']*event['price']
                    fees=_fee_won(amount,self.target.cost_model.commission_bps)
                    if not buy:fees+=_fee_won(amount,self.target.cost_model.sell_tax_bps)
                    cash+=(-amount-fees) if buy else amount-fees
                    held+=event['quantity'] if buy else -event['quantity']
            return replace(initial,available_cash_won=cash,positions={initial_symbol:held} if held else {},as_of=self.now[0])
        self.intent=replace(self.intent,decision_id=lower['decision_id'],quantity=lower['incremental_quantity'],
            symbol=lower['symbol'],limit_price=self.target.reference_price_won,created_at=NOW)
        initial_symbol=self.intent.symbol;trade_ids.append(self.intent.intent_id)
        self.lifecycle.queue(self.intent);self.lifecycle.submit(self.intent.intent_id,initial)
        self.record=self.lifecycle.reconcile(self.intent.intent_id,self.snapshot)
        self.observation=PositionAccountObservation(replace(initial,as_of=self.now[0],reserved_open_buy_won=10_000),
            (self.record,),'off-initial',self.now[0],True,True,())
        self.cancel_and_reconcile(3)
        self.target=replace(self.target,action='ADD')
        self.observation=replace(self.observation,account=account_from_fills())
        self.assertEqual(96_997,self.observation.account.available_cash_won)
        old_size=self.plan();self.assertEqual('ABANDON',old_size['action'])
        # 10,000 / 1,010 floors to 9; the previous 7-share pair cannot price 6.
        self.assessment=replace(self.assessment,reprice_evidence=replace(self.assessment.reprice_evidence,
            input_ref='off-new-size-pair',population_ref='off-fixed-new-size-population',pending_quantity=6))
        first=self.plan();self.assertEqual(('REPRICE',6),(first['action'],first['replacement_quantity']))
        root=self.intent
        child=replace(root,intent_id='off-buy-child',decision_id=first['decision_id'],quantity=first['replacement_quantity'],
            limit_price=first['replacement_limit_price_won'],created_at=self.now[0])
        trade_ids.append(child.intent_id)
        self.lifecycle.queue(child);self.transport.submit_error=SubmissionUnknown('controlled child ACK loss')
        self.record=self.lifecycle.submit(child.intent_id,self.observation.account)
        self.assertEqual(OrderState.SUBMISSION_UNKNOWN,self.record.state)
        calls=self.transport.submit_calls
        self.lifecycle=OrderLifecycle(self.repository,self.transport,now_provider=lambda:self.now[0])
        self.assertEqual(self.record,self.lifecycle.submit(child.intent_id,self.observation.account))
        self.assertEqual(calls,self.transport.submit_calls)
        self.now[0]+=timedelta(seconds=1)
        buy_fill=BrokerFill('off-buy-child-fill',6,child.limit_price,self.now[0])
        self.snapshot=BrokerOrderSnapshot('off-buy-child-broker',root.account_ref,root.symbol,OrderState.FILLED,6,0,self.now[0],(buy_fill,))
        self.record=self.lifecycle.reconcile(child.intent_id,self.snapshot)
        self.assertEqual(6,self.record.detailed_filled_quantity)
        # Account holdings are the original detailed 3 plus the child's 6.
        account=account_from_fills();self.assertEqual(({root.symbol:9},90_931),(account.positions,account.available_cash_won))
        self.transport.submit_error=None
        def accept(intent):
            self.transport.submit_calls+=1
            return BrokerSubmission('broker:'+intent.intent_id,self.now[0])
        self.transport.submit=accept
        stop=replace(root,intent_id='off-protective-root',decision_id='off-protection-constraint',side=OrderSide.SELL,
            quantity=9,limit_price=1000,created_at=self.now[0])
        trade_ids.append(stop.intent_id)
        self.lifecycle.queue(stop);self.record=self.lifecycle.submit(stop.intent_id,account)
        self.now[0]+=timedelta(seconds=1)
        self.snapshot=BrokerOrderSnapshot(self.record.broker_order_id,root.account_ref,root.symbol,OrderState.ACCEPTED,0,9,self.now[0])
        self.record=self.lifecycle.reconcile(stop.intent_id,self.snapshot)
        self.policy=replace(self.policy,purpose='TAKE_PROFIT',anchor_intent_id=stop.intent_id,anchor_available_at=stop.created_at)
        self.target=replace(self.target,action='REDUCE',target_policy=None,target_quantity=5,decision_time=self.now[0],reference_price_available_at=self.now[0],
            account_input_ref='off-sell-cut',orders_input_ref='off-sell-cut')
        self.observation=PositionAccountObservation(replace(account,as_of=self.now[0]),(self.record,),'off-sell-cut',self.now[0],True,True,())
        self.assessment=replace(self.assessment,reprice_evidence=None,candidate_limit_price_won=990,price_available_at=self.now[0])
        self.assertEqual('WAIT',self.plan()['action'])
        self.policy=replace(self.policy,purpose='PROTECTIVE_EXIT')
        self.target=replace(self.target,action='EXIT',target_quantity=0)
        self.assessment=replace(self.assessment,protection_required=True,thesis_valid=None,trend_allows_wait=None)
        protected=self.plan();self.assertEqual(('CANCEL','REPRICE',True),(protected['action'],protected['after_cancel'],protected['risk_path_required']))
        self.record=self.lifecycle.cancel(stop.intent_id)
        pending=self.plan();self.assertIsNone(pending['replacement_quantity']);self.assertFalse(pending['liquidation_confirmed'])
        self.now[0]+=timedelta(seconds=1)
        fills=(BrokerFill('off-stop-race-fill',4,1000,self.now[0]),)
        self.snapshot=BrokerOrderSnapshot(self.record.broker_order_id,root.account_ref,root.symbol,OrderState.CANCELLED,4,0,self.now[0],fills)
        self.record=self.lifecycle.reconcile(stop.intent_id,self.snapshot)
        residual=account_from_fills();self.assertEqual(({root.symbol:5},94_919),(residual.positions,residual.available_cash_won))
        self.observation=replace(self.observation,account=residual,
            open_orders=(),observed_at=self.now[0])
        self.target=replace(self.target,decision_time=self.now[0],reference_price_available_at=self.now[0])
        self.assessment=replace(self.assessment,price_available_at=self.now[0])
        remaining=self.plan();self.assertEqual(('REPRICE',5,False),(remaining['action'],remaining['replacement_quantity'],remaining['liquidation_confirmed']))
        final=replace(stop,intent_id='off-protective-child',decision_id=remaining['decision_id'],quantity=5,
            limit_price=remaining['replacement_limit_price_won'],created_at=self.now[0])
        trade_ids.append(final.intent_id)
        self.lifecycle.queue(final);self.record=self.lifecycle.submit(final.intent_id,self.observation.account)
        self.now[0]+=timedelta(seconds=1)
        fills=(BrokerFill('off-final-fill',5,990,self.now[0]),)
        self.snapshot=BrokerOrderSnapshot(self.record.broker_order_id,root.account_ref,root.symbol,OrderState.FILLED,5,0,self.now[0],fills)
        self.record=self.lifecycle.reconcile(final.intent_id,self.snapshot)
        self.policy=replace(self.policy,anchor_intent_id=final.intent_id,anchor_price_won=final.limit_price,anchor_available_at=final.created_at)
        self.observation=replace(self.observation,account=account_from_fills(),observed_at=self.now[0])
        # Existing model rounds fees down per fill: gross -110, fees 34 => -144.
        self.assertEqual(100_000-144,self.observation.account.available_cash_won)
        self.target=replace(self.target,decision_time=self.now[0],reference_price_available_at=self.now[0])
        flat=self.plan();self.assertEqual(('COMPLETED',True,False),(flat['status'],flat['liquidation_confirmed'],flat['risk_path_required']))
        unverified=self.plan(observation=replace(self.observation,reconciliation_complete=False))
        self.assertFalse(unverified['liquidation_confirmed'])
        before=self.repository.events(final.intent_id);calls=self.transport.submit_calls
        self.lifecycle=OrderLifecycle(self.repository,self.transport,now_provider=lambda:self.now[0])
        self.assertEqual(self.record,self.lifecycle.reconcile(final.intent_id,self.snapshot))
        self.assertEqual(self.record,self.lifecycle.submit(final.intent_id,self.observation.account))
        self.assertEqual(before,self.repository.events(final.intent_id));self.assertEqual(calls,self.transport.submit_calls)
        for result in (first,pending,remaining,flat):self.assertFalse(result['order_authorized'])
        self.off_flow_evidence={'source_kind':'controlled_fixed_inputs_and_synthetic_broker_responses',
            'native_operating_admission_verified':False,'order_authorized':False,
            'upper_allocation':upper,'lower_target':lower,'old_size_reprice':old_size,
            'buy_reprice':first,'profit_wait_then_protection_constraint':protected,'cancel_pending':pending,
            'protective_residual':remaining,'final_plan':flat,'final_account':asdict(self.observation.account),
            'ledger':[{'intent':asdict(self.repository.load(identity).intent),'events':self.repository.events(identity)} for identity in trade_ids],
            'fake_transport':{'submits':self.transport.submit_calls,'cancels':self.transport.cancel_calls},
            'actual_broker_transport_calls':0,'gross_pnl_won':-110,'modeled_fee_tax_won':34,'net_pnl_won':-144}

    def test_profit_and_protection_use_distinct_anchored_slippage_budgets_without_waiting_for_shadow(self):
        from unittest.mock import patch
        self.setup_order('SELL')
        assessment=replace(self.assessment,candidate_limit_price_won=975,reprice_evidence=None)
        self.assertEqual('WAIT',self.plan(assessment=assessment)['action'])
        assessment=replace(assessment,protection_required=True,thesis_valid=None,thesis_input_ref='',
            thesis_available_at=None,thesis_valid_until=None,reprice_evidence=replace(self.assessment.reprice_evidence,net_advantage_won=-999_999))
        with patch('kiwoom_monitor.application.position_target.evaluate_position_action_comparison_shadow',
                   side_effect=AssertionError('protection waited for R4 Shadow')):
            result=self.plan(target=replace(self.target,action='EXIT',target_quantity=0),assessment=assessment)
        self.assertEqual(('CANCEL','REPRICE',True),tuple(result[k] for k in ('action','after_cancel','risk_path_required')))

    def test_protection_slippage_and_liquidity_failures_do_not_abandon_or_claim_fill(self):
        self.setup_order('SELL','PROTECTIVE_EXIT')
        for changes in ({'market_status':'HALTED'},{'market_status':'NO_LIQUIDITY'},
            {'candidate_limit_price_won':900},{'reprices_used':3},{'candidate_limit_price_won':None}):
            result=self.plan(assessment=replace(self.assessment,**changes))
            self.assertEqual(('WAIT',True,True,False),tuple(result[k] for k in ('action','risk_path_required','requires_operator_attention','liquidation_confirmed')))

    def test_buy_slippage_retry_and_expiry_abandon_while_price_anchor_does_not_reset(self):
        for changes in ({'candidate_limit_price_won':1020},{'reprices_used':3}):
            result=self.plan(assessment=replace(self.assessment,**changes))
            self.assertEqual(('CANCEL','ABANDON'),tuple(result[k] for k in ('action','after_cancel')))
        with self.assertRaises(ValueError):self.plan(policy=replace(self.policy,anchor_price_won=1020))
        self.assertEqual('CANCEL',self.plan(policy=replace(self.policy,wait_until=NOW))['action'])

    def test_future_stale_or_missing_risk_thesis_price_broker_inputs_never_authorize_reprice(self):
        for changes in ({'risk_available_at':self.now[0]+timedelta(seconds=1)}, {'risk_valid_until':self.now[0]},
            {'thesis_available_at':self.now[0]+timedelta(seconds=1)}, {'price_available_at':NOW-timedelta(seconds=30)},
            {'price_input_ref':''}):
            result=self.plan(assessment=replace(self.assessment,**changes))
            self.assertNotEqual('REPRICE',result['action']);self.assertIsNone(result['replacement_quantity'])
        result=self.plan(broker_snapshot=replace(self.snapshot,as_of=NOW-timedelta(seconds=30)))
        self.assertEqual('WAIT',result['action'])

    def test_reprice_ev_is_bound_to_management_action_remaining_price_horizon_and_cut(self):
        for changes in ({'intent_id':'other'},{'pending_quantity':9},{'candidate_limit_price_won':1011},
            {'horizon_seconds':60},{'available_at':self.now[0]+timedelta(seconds=1)},
            {'paired_sample_count':1},{'net_advantage_won':9},{'net_standard_error_won':50},{'cost_model_ref':'different-cost'}):
            result=self.plan(assessment=replace(self.assessment,reprice_evidence=replace(self.assessment.reprice_evidence,**changes)))
            self.assertEqual('WAIT',result['action'])

    def test_expired_cost_model_cannot_support_reprice_ev_but_does_not_delay_protection(self):
        from kiwoom_monitor.application.research_evaluation import position_action_cost_model_ref
        cost=replace(self.target.cost_model,valid_from=(NOW-timedelta(seconds=10)).isoformat(),valid_to=self.now[0].isoformat())
        evidence=replace(self.assessment.reprice_evidence,cost_model_ref=position_action_cost_model_ref(cost.to_dict()))
        result=self.plan(target=replace(self.target,cost_model=cost),assessment=replace(self.assessment,reprice_evidence=evidence))
        self.assertEqual(('WAIT','EV_UNAVAILABLE'),(result['action'],result['ev_status']))
        self.setup_order('SELL','PROTECTIVE_EXIT')
        self.assertEqual('CANCEL',self.plan(target=replace(self.target,cost_model=cost),assessment=replace(self.assessment,reprice_evidence=None))['action'])

    def test_after_market_manual_probe_permission_is_not_automatic_reprice_permission(self):
        self.setup_order('SELL','PROTECTIVE_EXIT')
        cut=NOW+timedelta(hours=7)
        self.now[0]=cut
        snapshot=replace(self.snapshot,as_of=cut)
        record=self.lifecycle.reconcile('intent-1',snapshot)
        observation=replace(self.observation,account=replace(self.observation.account,as_of=cut),observed_at=cut,open_orders=(record,))
        assessment=replace(self.assessment,risk_available_at=cut,risk_valid_until=cut+timedelta(minutes=1),
            thesis_available_at=cut,thesis_valid_until=cut+timedelta(minutes=1),price_available_at=cut,reprice_evidence=None)
        policy=replace(self.policy,valid_until=cut+timedelta(minutes=1),wait_until=cut+timedelta(seconds=30),protection_deadline=cut+timedelta(seconds=15))
        result=self.plan(record=record,broker_snapshot=snapshot,observation=observation,
            target=replace(self.target,decision_time=cut,reference_price_available_at=cut),assessment=assessment,policy=policy)
        self.assertEqual(('WAIT',True,False),tuple(result[k] for k in ('action','risk_path_required','order_authorized')))
        self.assertIn('replacement_session_not_verified',result['reasons'])
        self.assertEqual((1,0),(self.transport.submit_calls,self.transport.cancel_calls))

    def test_cancelled_reprice_does_not_duplicate_other_run_or_manual_exposure(self):
        self.cancel_and_reconcile(3)
        other=replace(self.record,intent=replace(self.intent,intent_id='manual-other',run_id='another-run',quantity=2),
            state=OrderState.ACCEPTED,broker_order_id='another-broker',filled_quantity=0,fill_ids=(),
            detailed_filled_quantity=0,broker_reported_filled_quantity=0)
        observation=replace(self.observation,open_orders=(other,),account=replace(self.observation.account,reserved_open_buy_won=2000))
        evidence=replace(self.assessment.reprice_evidence,pending_quantity=5)
        result=self.plan(observation=observation,assessment=replace(self.assessment,reprice_evidence=evidence))
        self.assertEqual(('REPRICE',5),(result['action'],result['replacement_quantity']))
        result=self.plan(observation=replace(observation,open_orders=(replace(other,state=OrderState.SUBMISSION_UNKNOWN),)))
        self.assertEqual('WAIT',result['action'])

    def test_replacement_buy_cannot_expand_original_budget_or_use_unfunded_target(self):
        self.cancel_and_reconcile(3)
        result=self.plan(target=replace(self.target,target_quantity=20))
        self.assertEqual('WAIT',result['action'])
        self.assertIn('replacement_buy_exceeds_original_unfilled_budget',result['reasons'])
        result=self.plan(observation=replace(self.observation,account=replace(self.observation.account,available_cash_won=1)))
        self.assertEqual('WAIT',result['action'])

    def test_typed_json_and_existing_cli_match_without_io_or_mutation(self):
        from unittest.mock import patch
        from scripts.preview_market_theme_shadow import evaluate_document
        self.cancel_and_reconcile(3);document=self.document();before=json.dumps(document,sort_keys=True)
        with patch('sqlite3.connect',side_effect=AssertionError('management preview accessed DB')):
            self.assertEqual(self.plan(),evaluate_document(document))
        self.assertEqual(before,json.dumps(document,sort_keys=True))
        self.assertEqual(self.plan()['decision_id'],self.plan()['decision_id'])

    def test_unknown_or_ambiguous_policy_values_and_cross_scope_are_rejected(self):
        for changes in ({'purpose':'other'},{'maximum_reprices':True},{'uncertainty_multiplier':float('nan')},
            {'minimum_paired_samples':1},{'maximum_adverse_slippage_bps':-1}):
            with self.assertRaises(ValueError):replace(self.policy,**changes)
        for changes in ({'account_ref':'other'},{'symbol':'000660'}):
            with self.assertRaises(ValueError):self.plan(target=replace(self.target,**changes))
        with self.assertRaises(ValueError):self.plan(target=replace(self.target,account_input_ref='unrelated-cut'))
        with self.assertRaises(ValueError):replace(self.assessment.reprice_evidence,version='position-action-comparison-shadow/v1')
        document=self.document();document['extra']='unsupported'
        from scripts.preview_market_theme_shadow import evaluate_document
        with self.assertRaises(ValueError):evaluate_document(document)


class OrderLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = SQLiteQueryStore(Path(self.directory.name) / "central.sqlite3")
        self.store.initialize()
        self.repository = ExecutionRepository(self.store)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_sor_ack_without_physical_venue_recovers_from_exact_order_evidence_after_restart(self):
        from types import SimpleNamespace
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import BrokerOrderDetails
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import LocalAutomationOrderTransport
        from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_broker_venue

        clock = [NOW + timedelta(minutes=5)]
        intent = replace(_intent(), venue='SOR', created_at=clock[0], expires_at=clock[0] + timedelta(minutes=1))
        account = replace(_account(), as_of=clock[0])
        calls = []

        def request_once(api, path, body):
            calls.append((api, body))
            if api == 'kt10000':
                return {'ord_no': 'broker-sor', 'dmst_stex_tp': 'SOR'}
            self.assertEqual(('kt10003', 'NXT', 'broker-sor'), (api, body['dmst_stex_tp'], body['orig_ord_no']))
            return {'ord_no': 'cancelled-sor', 'dmst_stex_tp': 'NXT'}

        transport = LocalAutomationOrderTransport(SimpleNamespace(request_once=request_once), now_provider=lambda: clock[0])

        def open_runtime(store, owner):
            store._execution_wall_now = lambda: clock[0]
            repository = ExecutionRepository(store)
            lifecycle = OrderLifecycle(repository, transport, now_provider=lambda: clock[0],
                                       local_automation_order_rejection=lambda _intent, _now: None)
            runtime = ExecutionRuntime(lifecycle, repository, account_ref=intent.account_ref, run_id=intent.run_id,
                                       owner_token=owner, now_provider=lambda: clock[0])
            runtime.start()
            return runtime

        first = open_runtime(self.store, 'sor-initial')
        pending = first.submit(intent, account)
        self.assertEqual((OrderState.SUBMISSION_UNKNOWN, 'broker-sor', None),
                         (pending.state, pending.broker_order_id, execution_broker_venue(pending)))
        self.assertTrue(first.stop())
        self.store.close()
        self.store = SQLiteQueryStore(Path(self.directory.name) / 'central.sqlite3')
        self.store.initialize()
        second = open_runtime(self.store, 'sor-reopened')
        self.assertEqual(pending, second.submit(intent, account))
        with self.assertRaisesRegex(ValueError, 'reconciled and accepted'):
            second.cancel(intent.intent_id)
        self.assertEqual(1, len(calls))
        clock[0] += timedelta(seconds=1)
        snapshot = BrokerOrderSnapshot('broker-sor', intent.account_ref, intent.symbol, OrderState.PARTIALLY_FILLED,
            1, intent.quantity - 1, clock[0], (BrokerFill('sor-fill', 1, intent.limit_price, clock[0]),))
        detail = BrokerOrderDetails('broker-sor', intent.quantity, intent.side, intent.limit_price, 'NXT')
        self.assertIsNone(second.reconcile_by_broker_order(snapshot, broker_venue='NXT'))
        fallback = replace(detail, venue='KRX', venue_confirmed=False)
        self.assertIsNone(second.reconcile_by_broker_order(snapshot, broker_venue='KRX', broker_details=(fallback,)))
        self.assertIsNone(second.reconcile_by_broker_order(snapshot, broker_venue='NXT',
                                                          broker_details=(replace(detail, side=OrderSide.SELL),)))
        recovered = second.reconcile_by_broker_order(snapshot, broker_venue='NXT', broker_details=(detail,))
        self.assertEqual(('NXT', 1, OrderState.PARTIALLY_FILLED),
                         (execution_broker_venue(recovered), recovered.detailed_filled_quantity, recovered.state))
        self.assertIsNone(second.reconcile_by_broker_order(snapshot, broker_venue='KRX',
                                                          broker_details=(replace(detail, venue='KRX'),)))
        second.cancel(intent.intent_id)
        self.assertEqual(['SOR', 'NXT'], [body['dmst_stex_tp'] for _, body in calls])
        clock[0] += timedelta(seconds=1)
        late = replace(snapshot, as_of=clock[0], filled_quantity=2, remaining_quantity=intent.quantity - 2,
                       fills=(BrokerFill('late-fill', 1, intent.limit_price, clock[0]),))
        self.assertIsNone(second.reconcile_by_broker_order(late, broker_venue='KRX'))
        self.assertEqual(2, second.reconcile_by_broker_order(late, broker_venue='NXT').detailed_filled_quantity)
        self.assertEqual(2, second.reconcile_by_broker_order(late, broker_venue='NXT').detailed_filled_quantity)
        self.assertEqual(2, len(calls))
        self.assertTrue(second.stop())
        self.store.close()

    def test_unknown_submission_is_never_automatically_retried_after_restart(self) -> None:
        first_transport = _Transport(SubmissionUnknown("timeout"))
        lifecycle = OrderLifecycle(self.repository, first_transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent())
        record = lifecycle.submit("intent-1", _account())
        self.assertEqual(OrderState.SUBMISSION_UNKNOWN, record.state)

        replacement = _Transport()
        restarted = OrderLifecycle(
            ExecutionRepository(SQLiteQueryStore(Path(self.directory.name) / "central.sqlite3")),
            replacement, now_provider=lambda: NOW,
        )
        record = restarted.submit("intent-1", _account())
        self.assertEqual(OrderState.SUBMISSION_UNKNOWN, record.state)
        self.assertEqual(0, replacement.submit_calls)

    def test_ambiguous_cancel_stays_pending_after_restart_until_broker_reconciliation(self) -> None:
        transport = _Transport(cancel_error=SubmissionUnknown("cancel timed out"))
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent())
        self.assertEqual(OrderState.ACCEPTED, lifecycle.submit("intent-1", _account()).state)

        pending = lifecycle.cancel("intent-1")
        self.assertEqual(OrderState.CANCEL_PENDING, pending.state)
        self.assertEqual(1, transport.cancel_calls)
        self.assertEqual(
            ["CANCEL_STARTED", "CANCEL_RESPONSE_UNKNOWN"],
            [event["event_type"] for event in self.repository.events("intent-1")][-2:],
        )

        replacement = _Transport()
        repository = ExecutionRepository(SQLiteQueryStore(Path(self.directory.name) / "central.sqlite3"))
        restarted = OrderLifecycle(repository, replacement, now_provider=lambda: NOW)
        with self.assertRaisesRegex(ValueError, "reconciled and accepted"):
            restarted.cancel("intent-1")
        self.assertEqual(0, replacement.cancel_calls)
        resolved = restarted.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.CANCELLED, 0, 3,
            NOW + timedelta(seconds=2), (),
        ))
        self.assertEqual(OrderState.CANCELLED, resolved.state)
        self.assertEqual("BROKER_RECONCILED", repository.events("intent-1")[-1]["event_type"])

    def test_rejected_cancel_without_fills_restores_accepted_and_allows_explicit_retry(self) -> None:
        transport = _Transport(cancel_error=SubmissionRejected("broker rejected cancel"))
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent())
        lifecycle.submit("intent-1", _account())
        rejected = lifecycle.cancel("intent-1")
        self.assertEqual(OrderState.ACCEPTED, rejected.state)
        self.assertEqual(1, transport.cancel_calls)
        self.assertEqual(
            ["CANCEL_STARTED", "CANCEL_REJECTED"],
            [event["event_type"] for event in self.repository.events("intent-1")][-2:],
        )

        replacement = _Transport()
        repository = ExecutionRepository(SQLiteQueryStore(Path(self.directory.name) / "central.sqlite3"))
        restarted = OrderLifecycle(repository, replacement, now_provider=lambda: NOW)
        self.assertEqual(OrderState.CANCEL_PENDING, restarted.cancel("intent-1").state)
        self.assertEqual(1, replacement.cancel_calls)
        self.assertEqual("CANCEL_ACCEPTED", repository.events("intent-1")[-1]["event_type"])
        filled = restarted.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.FILLED, 3, 0,
            NOW + timedelta(seconds=2), (BrokerFill("fill-all", 3, 70_000, NOW + timedelta(seconds=1)),),
        ))
        self.assertEqual(OrderState.FILLED, filled.state)
        self.assertEqual(3, filled.filled_quantity)

    def test_fresh_partial_observations_preserve_unknown_cancel_through_restart_and_late_details(self):
        from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_remaining_quantity
        transport = _Transport(cancel_error=SubmissionUnknown("cancel response lost"))
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent())
        lifecycle.submit("intent-1", _account())
        first = BrokerFill("first", 1, 70_000, NOW + timedelta(seconds=1))
        lifecycle.reconcile("intent-1", BrokerOrderSnapshot("broker-1", "account-1", "005930",
            OrderState.PARTIALLY_FILLED, 1, 2, NOW + timedelta(seconds=2), (first,)))
        self.assertEqual(OrderState.CANCEL_PENDING, lifecycle.cancel("intent-1").state)
        replacement = _Transport()
        repository = ExecutionRepository(SQLiteQueryStore(Path(self.directory.name) / "central.sqlite3"))
        restarted = OrderLifecycle(repository, replacement, now_provider=lambda: NOW)
        for second, reported, details in ((3, 1, (first,)), (4, 2, (first,)),
                (5, 2, (first, BrokerFill("late", 1, 70_100, NOW + timedelta(seconds=4))))):
            current = restarted.reconcile("intent-1", BrokerOrderSnapshot("broker-1", "account-1", "005930",
                OrderState.PARTIALLY_FILLED, reported, 3 - reported, NOW + timedelta(seconds=second), details))
            self.assertEqual(OrderState.CANCEL_PENDING, current.state, repository.events("intent-1"))
            self.assertEqual(3 - reported, execution_remaining_quantity(current))
            with self.assertRaisesRegex(ValueError, "reconciled and accepted"):
                restarted.cancel("intent-1")
            self.assertEqual(current, restarted.submit("intent-1", _account()))
        self.assertEqual((2, 2), (current.filled_quantity, current.detailed_filled_quantity))
        self.assertTrue(all(row["state"] == "CANCEL_PENDING" for row in repository.events("intent-1")
            if row["event_type"] in {"FILL", "BROKER_FILL_AGGREGATE"} and row["broker_as_of"] >
                (NOW + timedelta(seconds=2)).isoformat()))
        terminal = restarted.reconcile("intent-1", BrokerOrderSnapshot("broker-1", "account-1", "005930",
            OrderState.CANCELLED, 2, 0, NOW + timedelta(seconds=6), details))
        self.assertEqual((OrderState.CANCELLED, 2, 0), (terminal.state, terminal.detailed_filled_quantity,
            execution_remaining_quantity(terminal)))
        self.assertEqual(2, len([row for row in repository.events("intent-1") if row["event_type"] == "FILL"]))
        self.assertEqual((1, 1, 0, 0), (transport.submit_calls, transport.cancel_calls,
            replacement.submit_calls, replacement.cancel_calls))

    def test_physical_leg_fresh_partial_does_not_erase_its_uncertain_cancel(self):
        from tests.unit.test_order_amendment import amendment_fixture, broker_snapshot
        _, store, repository, transport, clock, lifecycle, intent = amendment_fixture(self)
        parent_fill = BrokerFill("parent-first", 2, 70_000, NOW + timedelta(seconds=1))
        lifecycle.reconcile(intent.intent_id, broker_snapshot(intent, "broker-1", 2, 4, 1, (parent_fill,)))
        clock[0] += timedelta(seconds=2)
        lifecycle.amend(intent.intent_id, 4, 70_100)
        transport.cancel_error = SubmissionUnknown("child cancel response lost")
        pending = lifecycle.cancel(intent.intent_id, broker_order_id="broker-2")
        self.assertEqual(OrderState.CANCEL_PENDING, pending.state)
        parent = pending.broker_legs[0]
        restarted = OrderLifecycle(ExecutionRepository(store), transport, now_provider=lambda: clock[0])
        for quantity in (1, 2):
            details = tuple(BrokerFill(f"child-{n}", 1, 70_100, NOW + timedelta(seconds=3 + n))
                            for n in range(1, quantity + 1))
            current = restarted.reconcile(intent.intent_id, broker_snapshot(intent, "broker-2", quantity,
                3 - quantity, 4 + quantity, details))
            self.assertEqual(OrderState.CANCEL_PENDING, current.state)
            self.assertEqual((parent, OrderState.CANCEL_PENDING), (current.broker_legs[0], current.broker_legs[1].state))
            with self.assertRaisesRegex(ValueError, "explicit reconciled broker order"):
                restarted.cancel(intent.intent_id, broker_order_id="broker-2")
        terminal = restarted.reconcile(intent.intent_id, broker_snapshot(intent, "broker-2", 2, 0, 7,
            details, state=OrderState.CANCELLED))
        self.assertEqual((OrderState.PARTIALLY_FILLED, 4, OrderState.CANCELLED),
            (terminal.state, terminal.detailed_filled_quantity, terminal.broker_legs[1].state))
        self.assertEqual(parent, terminal.broker_legs[0])
        self.assertEqual((1, 1, 1), (transport.submit_calls, transport.cancel_calls, len(transport.amend_calls)))

    def test_rejected_partial_cancel_preserves_fill_and_retry_reconciliation_deduplicates_it(self) -> None:
        transport = _Transport(cancel_error=SubmissionRejected("broker rejected partial cancel"))
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent())
        lifecycle.submit("intent-1", _account())
        first_fill = BrokerFill("fill-1", 1, 70_000, NOW + timedelta(seconds=1))
        partial = lifecycle.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.PARTIALLY_FILLED, 1, 2,
            NOW + timedelta(seconds=2), (first_fill,),
        ))
        self.assertEqual(OrderState.PARTIALLY_FILLED, partial.state)
        rejected = lifecycle.cancel("intent-1")
        self.assertEqual(OrderState.PARTIALLY_FILLED, rejected.state)
        self.assertEqual(1, rejected.filled_quantity)
        self.assertEqual("CANCEL_REJECTED", self.repository.events("intent-1")[-1]["event_type"])

        replacement = _Transport()
        repository = ExecutionRepository(SQLiteQueryStore(Path(self.directory.name) / "central.sqlite3"))
        restarted = OrderLifecycle(repository, replacement, now_provider=lambda: NOW)
        self.assertEqual(OrderState.CANCEL_PENDING, restarted.cancel("intent-1").state)
        self.assertEqual(1, replacement.cancel_calls)
        completed = restarted.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.FILLED, 3, 0,
            NOW + timedelta(seconds=3),
            (first_fill, BrokerFill("fill-2", 2, 70_100, NOW + timedelta(seconds=3))),
        ))
        self.assertEqual(OrderState.FILLED, completed.state)
        self.assertEqual(3, completed.filled_quantity)
        self.assertEqual(("fill-1", "fill-2"), completed.fill_ids)
        self.assertEqual(2, sum(event["event_type"] == "FILL" for event in repository.events("intent-1")))

    def test_preflight_rejects_expired_or_wrong_account_before_transport(self) -> None:
        transport = _Transport()
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent(NOW))
        expired = lifecycle.submit("intent-1", _account())
        self.assertEqual(OrderState.REJECTED, expired.state)
        self.assertEqual(0, transport.submit_calls)
        wrong_account_intent = replace(_intent(), intent_id="wrong-account")
        lifecycle.queue(wrong_account_intent)
        wrong_account = lifecycle.submit(wrong_account_intent.intent_id, _account("account-2"))
        self.assertEqual(OrderState.REJECTED, wrong_account.state)
        self.assertEqual("account mismatch", self.repository.events(wrong_account_intent.intent_id)[-1]["reason"])
        self.assertEqual(0, transport.submit_calls)

    def test_distinct_same_second_fills_are_deduplicated_by_execution_id(self) -> None:
        transport = _Transport()
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent())
        accepted = lifecycle.submit("intent-1", _account())
        self.assertEqual(OrderState.ACCEPTED, accepted.state)
        fills = (
            BrokerFill("fill-a", 1, 70_000, NOW + timedelta(seconds=1)),
            BrokerFill("fill-b", 2, 70_100, NOW + timedelta(seconds=1)),
        )
        snapshot = BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.FILLED, 3, 0,
            NOW + timedelta(seconds=2), fills,
        )
        filled = lifecycle.reconcile("intent-1", snapshot)
        duplicate = lifecycle.reconcile("intent-1", snapshot)
        self.assertEqual(OrderState.FILLED, duplicate.state)
        self.assertEqual(3, duplicate.filled_quantity)
        self.assertEqual(3, duplicate.broker_reported_filled_quantity)
        self.assertEqual(("fill-a", "fill-b"), duplicate.fill_ids)
        self.assertEqual(
            0,
            sum(event["event_type"] == "BROKER_FILL_AGGREGATE"
                for event in self.repository.events("intent-1")),
        )

    def test_fill_wins_cancel_race_and_stale_snapshot_cannot_roll_back(self) -> None:
        transport = _Transport()
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent())
        lifecycle.submit("intent-1", _account())
        pending = lifecycle.cancel("intent-1")
        self.assertEqual(OrderState.CANCEL_PENDING, pending.state)
        filled = lifecycle.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.FILLED, 3, 0,
            NOW + timedelta(seconds=3), (BrokerFill("fill-all", 3, 70_000, NOW + timedelta(seconds=2)),),
        ))
        stale = lifecycle.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.CANCELLED, 0, 3,
            NOW + timedelta(seconds=1), (),
        ))
        self.assertEqual(OrderState.FILLED, filled.state)
        self.assertEqual(OrderState.FILLED, stale.state)

    def test_aggregate_recovery_then_detailed_fills_does_not_double_count(self) -> None:
        transport = _Transport()
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: NOW)
        lifecycle.queue(_intent())
        lifecycle.submit("intent-1", _account())

        aggregate = lifecycle.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.FILLED, 3, 0,
            NOW + timedelta(seconds=1), (),
        ))
        detailed = lifecycle.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.FILLED, 3, 0,
            NOW + timedelta(seconds=2), (
                BrokerFill("fill-a", 1, 70_000, NOW + timedelta(seconds=1)),
                BrokerFill("fill-b", 2, 70_100, NOW + timedelta(seconds=1)),
            ),
        ))

        self.assertEqual(3, aggregate.filled_quantity)
        self.assertEqual(3, detailed.filled_quantity)
        self.assertEqual(3, detailed.detailed_filled_quantity)
        self.assertEqual(3, detailed.broker_reported_filled_quantity)
        self.assertEqual(("fill-a", "fill-b"), detailed.fill_ids)
        self.assertEqual(
            1,
            sum(event["event_type"] == "BROKER_FILL_AGGREGATE" for event in self.repository.events("intent-1")),
        )

    def test_scoped_same_request_and_run_are_separate_for_two_accounts(self):
        async def scenario():
            transport = _Transport()
            gateways = []
            for index, account_ref in enumerate(("account-1", "account-2")):
                repository = ExecutionRepository(self.store)
                runtime = ExecutionRuntime(OrderLifecycle(repository, transport, now_provider=lambda: NOW),
                    repository, account_ref=account_ref, run_id="run-1", owner_token=f"owner-{index}")
                runtime.start()
                async def refresh(ref=account_ref): return _account(ref)
                gateways.append(ManualMockOrderGateway(runtime, repository, refresh, now_provider=lambda: NOW))
            self.assertEqual(gateways[0].intent_id("same"), gateways[1].intent_id("same"))
            records = [await gateway.submit_limit(request_id="same", symbol="005930", side=OrderSide.BUY,
                quantity=1, limit_price=1000, expires_seconds=120, scoped=True) for gateway in gateways]
            self.assertNotEqual(records[0].intent.intent_id, records[1].intent.intent_id)
            self.assertEqual(2, transport.submit_calls)
            self.assertEqual(["account-1", "account-2"], [r.intent.account_ref for r in records])
        asyncio.run(scenario())

    def test_manual_gateway_is_idempotent_and_refreshes_before_submit_and_cancel(self) -> None:
        async def scenario() -> None:
            transport = _Transport()
            runtime = ExecutionRuntime(
                OrderLifecycle(self.repository, transport, now_provider=lambda: NOW),
                self.repository,
                account_ref="account-1", run_id="run-1", owner_token="owner-1",
            )
            runtime.start()
            refreshes = 0

            async def refresh_account() -> AccountSnapshot:
                nonlocal refreshes
                refreshes += 1
                return _account()

            gateway = ManualMockOrderGateway(
                runtime, self.repository, refresh_account, now_provider=lambda: NOW,
            )
            first = await gateway.submit_limit(
                request_id="button-click-1", symbol="005930", side=OrderSide.BUY,
                quantity=3, limit_price=70_000, expires_seconds=120,
            )
            duplicate = await gateway.submit_limit(
                request_id="button-click-1", symbol="005930", side=OrderSide.BUY,
                quantity=3, limit_price=70_000, expires_seconds=120,
            )
            self.assertEqual(first.intent.intent_id, duplicate.intent.intent_id)
            self.assertEqual(1, transport.submit_calls)
            self.assertEqual(1, refreshes)

            cancelled = await gateway.cancel(first.intent.intent_id)
            self.assertEqual(OrderState.CANCEL_PENDING, cancelled.state)
            self.assertEqual(1, transport.cancel_calls)
            self.assertEqual(2, refreshes)

        asyncio.run(scenario())

    def test_manual_gateway_rejects_reusing_request_id_for_another_order(self) -> None:
        async def scenario() -> None:
            runtime = ExecutionRuntime(
                OrderLifecycle(self.repository, _Transport(), now_provider=lambda: NOW),
                self.repository,
                account_ref="account-1", run_id="run-1", owner_token="owner-1",
            )
            runtime.start()

            async def refresh_account() -> AccountSnapshot:
                return _account()

            gateway = ManualMockOrderGateway(
                runtime, self.repository, refresh_account, now_provider=lambda: NOW,
            )
            await gateway.submit_limit(
                request_id="same-id", symbol="005930", side=OrderSide.BUY,
                quantity=1, limit_price=70_000, expires_seconds=120,
            )
            with self.assertRaisesRegex(ValueError, "different mock order"):
                await gateway.submit_limit(
                    request_id="same-id", symbol="005930", side=OrderSide.BUY,
                    quantity=1, limit_price=70_100, expires_seconds=120,
                )

        asyncio.run(scenario())

    def test_manual_duplicate_rechecks_native_scope_after_first_load(self) -> None:
        async def scenario() -> None:
            path = Path(self.directory.name) / "central.sqlite3"
            store = SQLiteQueryStore(path, now_provider=lambda: NOW)
            repository = ExecutionRepository(store)
            transport = _Transport()
            runtime = ExecutionRuntime(
                OrderLifecycle(repository, transport, now_provider=lambda: NOW),
                repository, account_ref="account-1", run_id="run-1",
                owner_token="owner-1", now_provider=lambda: NOW,
            )
            runtime.start()
            external_store = SQLiteQueryStore(path, now_provider=lambda: NOW)
            offline_repository = ExecutionRepository(external_store)
            refreshes = 0

            async def unexpected_refresh() -> AccountSnapshot:
                nonlocal refreshes
                refreshes += 1
                raise AssertionError("an existing request must not refresh or submit")

            gateway = ManualMockOrderGateway(
                runtime, repository, unexpected_refresh, now_provider=lambda: NOW,
            )
            try:
                for change in ("none", "state", "account_ref", "run_id"):
                    with self.subTest(change=change):
                        request_id = f"native-duplicate-{change}"
                        intent_id = gateway.intent_id(request_id)
                        initial = repository.create(replace(_intent(), intent_id=intent_id))
                        native_load = repository.load
                        first_loaded = False

                        def load_then_update(key):
                            nonlocal first_loaded
                            record = native_load(key)
                            if not first_loaded:
                                first_loaded = True
                                if change != "none":
                                    # The offline/import writer is intentionally unowned.
                                    # Frozen DTOs do not make its native JSON immutable.
                                    intent = (replace(record.intent, **{change: "different"})
                                              if change in {"account_ref", "run_id"} else record.intent)
                                    state = OrderState.SUBMISSION_UNKNOWN if change == "state" else record.state
                                    offline_repository.apply(replace(record, intent=intent), OrderEvent(
                                        f"offline-{change}", key, "OFFLINE_IMPORT", state,
                                        NOW, NOW + timedelta(seconds=1),
                                    ))
                            return record

                        with patch.object(repository, "load", side_effect=load_then_update):
                            command = gateway.submit_limit(
                                request_id=request_id, symbol="005930", side=OrderSide.BUY,
                                quantity=3, limit_price=70_000, expires_seconds=60,
                            )
                            if change in {"account_ref", "run_id"}:
                                with self.assertRaisesRegex(KeyError, "unknown order intent"):
                                    await command
                            else:
                                self.assertEqual(initial, await command)
                        latest = offline_repository.load(intent_id)
                        if change in {"account_ref", "run_id"}:
                            self.assertEqual("different", getattr(latest.intent, change))
                        elif change == "state":
                            self.assertEqual(OrderState.SUBMISSION_UNKNOWN, latest.state)
                            self.assertEqual(NOW + timedelta(seconds=1), latest.updated_at)
                        self.assertTrue(first_loaded)
                        self.assertEqual(0, refreshes)
                        self.assertEqual(0, transport.submit_calls)
                        self.assertEqual(0, transport.cancel_calls)
            finally:
                await gateway.close()
                runtime.stop()
                external_store.close()
                store.close()

        asyncio.run(scenario())

    def test_new_orders_are_rejected_at_unverified_mock_session_boundaries(self) -> None:
        kst = timezone(timedelta(hours=9))
        rejected_times = (
            datetime(2026, 9, 14, 15, 20, tzinfo=kst),
            datetime(2026, 9, 14, 15, 30, tzinfo=kst),
            datetime(2026, 9, 14, 15, 35, tzinfo=kst),
            datetime(2026, 9, 14, 15, 40, tzinfo=kst),
        )
        for sequence, at in enumerate(rejected_times):
            with self.subTest(at=at):
                intent_id = f"rejected-{sequence}"
                transport = _Transport()
                lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda at=at: at)
                intent = OrderIntent(
                    intent_id, "run-1", f"decision-{sequence}", "account-1", "mock",
                    "005930", "KRX", OrderSide.BUY, 1, OrderType.LIMIT, 70_000,
                    at - timedelta(seconds=1), at + timedelta(minutes=1), "policy-v1",
                )
                lifecycle.queue(intent)
                record = lifecycle.submit(intent_id, _account())
                self.assertEqual(OrderState.REJECTED, record.state)
                self.assertEqual(0, transport.submit_calls)
                rejection = self.repository.events(intent_id)[-1]
                self.assertEqual("PRECHECK_REJECTED", rejection["event_type"])
                self.assertTrue(rejection["reason"].startswith("UNSUPPORTED|"))
                self.assertIn(f"schedule={CURRENT_SCHEDULE_VERSION}", rejection["reason"])

    def test_manual_mock_krx_after_limit_reaches_transport_as_probe(self) -> None:
        kst = timezone(timedelta(hours=9))
        now = datetime(2026, 9, 14, 16, 0, tzinfo=kst)
        transport = _Transport()
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: now)
        intent = OrderIntent(
            "after-probe", "run-1", "decision-after", "account-1", "mock",
            "005930", "KRX", OrderSide.BUY, 1, OrderType.LIMIT, 70_000,
            now - timedelta(seconds=1), now + timedelta(minutes=1),
            "manual-mock-krx-after-limit-probe/v1",
        )
        lifecycle.queue(intent)

        record = lifecycle.submit(intent.intent_id, _account())

        self.assertEqual(OrderState.ACCEPTED, record.state)
        self.assertEqual(1, transport.submit_calls)

    def test_session_close_does_not_expire_or_resubmit_and_broker_reconciliation_stays_open(self) -> None:
        kst = timezone(timedelta(hours=9))
        clock = [datetime(2026, 9, 14, 15, 19, 59, tzinfo=kst)]
        transport = _Transport()
        lifecycle = OrderLifecycle(self.repository, transport, now_provider=lambda: clock[0])
        intent = OrderIntent(
            "close-boundary", "run-1", "decision-close", "account-1", "mock", "005930", "KRX",
            OrderSide.BUY, 3, OrderType.LIMIT, 70_000,
            clock[0] - timedelta(seconds=1), clock[0] + timedelta(minutes=10), "policy-v1",
        )
        lifecycle.queue(intent)
        accepted = lifecycle.submit(intent.intent_id, _account())
        self.assertEqual(OrderState.ACCEPTED, accepted.state)
        self.assertEqual(1, transport.submit_calls)

        clock[0] = datetime(2026, 9, 14, 16, 0, tzinfo=kst)
        unchanged = lifecycle.submit(intent.intent_id, _account())
        self.assertEqual(OrderState.ACCEPTED, unchanged.state)
        self.assertEqual(1, transport.submit_calls)

        cancelled = lifecycle.cancel(intent.intent_id)
        self.assertEqual(OrderState.CANCEL_PENDING, cancelled.state)
        self.assertEqual(1, transport.cancel_calls)
        broker_closed = lifecycle.reconcile(intent.intent_id, BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.CANCELLED, 0, 0,
            clock[0] + timedelta(seconds=1), (),
        ))
        self.assertEqual(OrderState.CANCELLED, broker_closed.state)
        self.assertEqual(0, broker_closed.filled_quantity)

        late_fill = lifecycle.reconcile(intent.intent_id, BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.CANCELLED, 1, 0,
            clock[0] + timedelta(seconds=2), (
                BrokerFill("late-fill-1", 1, 70_000, clock[0] - timedelta(minutes=31)),
            ),
        ))
        event_count = len(self.repository.events(intent.intent_id))
        duplicate = lifecycle.reconcile(intent.intent_id, BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.CANCELLED, 1, 0,
            clock[0] + timedelta(seconds=2), (),
        ))
        self.assertEqual(OrderState.CANCELLED, late_fill.state)
        self.assertEqual(1, late_fill.filled_quantity)
        self.assertEqual(1, duplicate.filled_quantity)
        self.assertEqual(event_count, len(self.repository.events(intent.intent_id)))

        restarted_repository = ExecutionRepository(
            SQLiteQueryStore(Path(self.directory.name) / "central.sqlite3"),
        )
        restarted = OrderLifecycle(restarted_repository, _Transport(), now_provider=lambda: clock[0])
        after_restart = restarted.reconcile(intent.intent_id, BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.CANCELLED, 1, 0,
            clock[0] + timedelta(seconds=2), (),
        ))
        self.assertEqual(OrderState.CANCELLED, after_restart.state)
        self.assertEqual(1, after_restart.filled_quantity)
        self.assertEqual(event_count, len(restarted_repository.events(intent.intent_id)))


class AutomationCancelBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = SQLiteQueryStore(Path(self.directory.name) / "cancel.sqlite3", now_provider=lambda: NOW)
        self.store.initialize()
        self.repository = ExecutionRepository(self.store)
        self.transport = _Transport()
        self.runtime = ExecutionRuntime(
            OrderLifecycle(self.repository, self.transport, now_provider=lambda: NOW), self.repository,
            account_ref="account-1", run_id="run-1", owner_token="owner-1", now_provider=lambda: NOW,
        )
        self.runtime.start()
        self.record = self.runtime.submit(_intent(), _account())
        self.runtime.set_new_orders_enabled(False)
        self.control()
        self.refreshes = 0
        self.refresh_hook = None
        async def refresh():
            self.refreshes += 1
            if self.refresh_hook is not None:
                self.refresh_hook()
            return _account()
        self.gateway = ManualMockOrderGateway(self.runtime, self.repository, refresh, now_provider=lambda: NOW)

    async def asyncTearDown(self):
        await self.gateway.close()
        self.runtime.stop()
        self.store.close()
        self.directory.cleanup()

    def control(self, *, revision=1, state="RUNNING", spec="spec-1", run="run-1"):
        self.store.upsert_documents("execution_mock_automation_control", [{
            "owner": "account-1", "key": "account-1", "document": {
                "control_revision": revision, "desired_state": state,
                "active_spec_id": spec, "execution_run_id": run,
            },
        }])

    async def cancel(self, expected=None):
        return await self.gateway.cancel_automation_intent(
            expected or self.record, spec_id="spec-1", control_revision=1,
        )

    def reconcile(self, state, quantity, remaining, *, detailed=True):
        return self.runtime.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", state, quantity, remaining, NOW + timedelta(seconds=1),
            (BrokerFill("fill-1", quantity, 70_000, NOW),) if quantity and detailed else (),
        ))

    async def test_buy_partial_cancel_ack_does_not_authorize_replacement(self):
        self.record = self.reconcile(OrderState.PARTIALLY_FILLED, 1, 2)
        result = await self.cancel()
        self.assertEqual((OrderState.CANCEL_PENDING, 1, 1),
                         (result.state, result.detailed_filled_quantity, self.transport.cancel_calls))
        self.assertEqual(1, self.transport.submit_calls)
        self.assertFalse(self.runtime._new_orders_enabled)
        self.assertEqual(self.record.intent, result.intent)
        self.assertEqual(1, self.refreshes)

    async def test_cancel_reads_share_each_scope_and_close_before_refresh_and_claim(self):
        timeline = []
        readers = []
        native_scope = self.store.mock_automation_read_scope
        load = self.repository.load
        events = self.repository.events

        @contextmanager
        def observed_scope():
            with native_scope() as reader:
                readers.append(reader)
                timeline.append('open')
                yield reader
            timeline.append('close')

        def observed_read(read, name):
            def read_order(intent, **options):
                reader = options.get('_reader')
                if reader is not None:
                    self.assertIs(reader, readers[-1])
                    timeline.append(name)
                return read(intent, **options)
            return read_order

        self.refresh_hook = lambda: timeline.append('refresh')
        with (patch.object(self.store, 'mock_automation_read_scope', observed_scope),
              patch.object(self.repository, 'load', observed_read(load, 'intent')),
              patch.object(self.repository, 'events', observed_read(events, 'history'))):
            result = await self.gateway.cancel_automation_intent(
                self.record, spec_id='spec-1', control_revision=1,
                before_cancel_claim=lambda: timeline.append('claim'),
            )
        self.assertEqual(['open', 'intent', 'history', 'close', 'refresh',
                          'open', 'intent', 'history', 'close', 'claim'], timeline)
        self.assertIsNot(readers[0], readers[1])
        self.assertEqual((OrderState.CANCEL_PENDING, 1, 1),
                         (result.state, self.refreshes, self.transport.cancel_calls))

    async def test_explicit_retry_history_error_precedes_refresh_and_runtime(self):
        failure = OSError('history unavailable')
        for terminal in (False, True):
            with self.subTest(terminal=terminal):
                if terminal:
                    self.reconcile(OrderState.FILLED, 3, 0)
                with (patch.object(self.repository, 'events', side_effect=failure),
                      patch.object(self.runtime, 'cancel_automation_intent') as cancel):
                    with self.assertRaises(OSError) as caught:
                        await self.gateway.cancel_automation_intent(
                            self.record, spec_id='spec-1', control_revision=1,
                            cancel_retry_claim={'attempt': 2},
                        )
                self.assertIs(failure, caught.exception)
                cancel.assert_not_called()
                self.assertEqual((0, 0), (self.refreshes, self.transport.cancel_calls))

    async def test_sell_uses_same_native_cancel_without_creating_new_sell(self):
        intent = replace(_intent(), intent_id="sell-1", side=OrderSide.SELL)
        self.runtime.set_new_orders_enabled(True)
        record = self.runtime.submit(intent, replace(_account(), positions={"005930": 3}))
        self.runtime.set_new_orders_enabled(False)
        result = await self.cancel(record)
        self.assertEqual(OrderState.CANCEL_PENDING, result.state)
        self.assertEqual((2, 1), (self.transport.submit_calls, self.transport.cancel_calls))

    async def test_stop_during_broker_refresh_blocks_cancel_claim(self):
        self.refresh_hook = lambda: self.control(state="STOPPED", revision=2)
        with self.assertRaisesRegex(RuntimeError, "MOCK_AUTOMATION_CONTROL_CHANGED"):
            await self.cancel()
        self.assertEqual(0, self.transport.cancel_calls)
        self.assertFalse(any(e["event_type"] == "CANCEL_STARTED" for e in self.repository.events("intent-1")))
        self.assertIsNone(self.repository._automation_control)
        self.assertFalse(self.runtime._new_orders_enabled)

    async def test_changed_running_revision_spec_or_run_blocks_cancel(self):
        for changes in ({"revision": 2}, {"spec": "spec-2"}, {"run": "run-2"}):
            with self.subTest(changes=changes):
                self.control(**changes)
                with self.assertRaisesRegex(RuntimeError, "MOCK_AUTOMATION_CONTROL_CHANGED"):
                    await self.cancel()
        self.assertEqual(0, self.transport.cancel_calls)

    async def test_control_fence_is_checked_in_cancel_started_transaction(self):
        from unittest.mock import patch
        apply = self.repository.apply
        def race(record, event, **kwargs):
            if event.event_type == "CANCEL_STARTED":
                self.control(state="STOPPED", revision=2)
            return apply(record, event, **kwargs)
        with patch.object(self.repository, "apply", side_effect=race):
            with self.assertRaisesRegex(RuntimeError, "MOCK_AUTOMATION_CONTROL_CHANGED"):
                await self.cancel()
        self.assertEqual(0, self.transport.cancel_calls)

    async def test_stop_after_cancel_claim_preserves_response_and_late_fill_reconciliation(self):
        from unittest.mock import patch
        original = self.transport.cancel
        def stop(*args):
            self.control(state="STOPPED", revision=2)
            return original(*args)
        with patch.object(self.transport, "cancel", side_effect=stop):
            result = await self.cancel()
        self.assertEqual(OrderState.CANCEL_PENDING, result.state)
        self.assertEqual("CANCEL_ACCEPTED", self.repository.events("intent-1")[-1]["event_type"])
        confirmed = self.reconcile(OrderState.CANCELLED, 1, 0)
        self.assertEqual((OrderState.CANCELLED, 1), (confirmed.state, confirmed.detailed_filled_quantity))
        self.assertEqual(1, self.transport.submit_calls)

    async def test_cancel_unknown_duplicate_needs_no_additional_broker_refresh(self):
        self.transport.cancel_error = SubmissionUnknown("response lost")
        result = await self.cancel()
        version = self.runtime.observation_revision
        for _ in range(5):
            self.assertEqual(result, await self.cancel())
        self.assertEqual((1, 1, 1), (self.refreshes, self.transport.cancel_calls, self.transport.submit_calls))
        self.assertEqual(OrderState.CANCEL_PENDING, result.state)
        self.assertEqual(version, self.runtime.observation_revision)

    async def test_rejected_cancel_is_not_automatic_retry_permission(self):
        self.transport.cancel_error = SubmissionRejected("broker refused")
        first = await self.cancel()
        self.assertEqual(OrderState.ACCEPTED, first.state)
        self.assertEqual(first, await self.cancel())
        self.assertEqual((1, 1), (self.transport.cancel_calls, self.refreshes))

    async def test_cancel_claim_ack_loss_survives_native_repository_restart(self):
        from unittest.mock import patch
        apply = self.repository.apply
        def lost_ack(record, event, **kwargs):
            result = apply(record, event, **kwargs)
            if event.event_type == "CANCEL_STARTED":
                raise OSError("committed claim ACK lost")
            return result
        with patch.object(self.repository, "apply", side_effect=lost_ack):
            with self.assertRaisesRegex(OSError, "ACK lost"):
                await self.cancel()
        await self.gateway.close()
        self.runtime.stop()
        self.repository = ExecutionRepository(self.store)
        self.runtime = ExecutionRuntime(
            OrderLifecycle(self.repository, self.transport, now_provider=lambda: NOW), self.repository,
            account_ref="account-1", run_id="run-1", owner_token="owner-restarted", now_provider=lambda: NOW,
        )
        self.runtime.start(); self.runtime.set_new_orders_enabled(False)
        async def unexpected_refresh():
            raise AssertionError("duplicate cancel refreshed broker")
        self.gateway = ManualMockOrderGateway(self.runtime, self.repository, unexpected_refresh)
        result = await self.cancel()
        self.assertEqual(OrderState.CANCEL_PENDING, result.state)
        self.assertEqual(0, self.transport.cancel_calls)

    async def test_partial_fill_during_refresh_requires_new_assessment(self):
        self.refresh_hook = lambda: self.reconcile(OrderState.PARTIALLY_FILLED, 1, 2)
        with self.assertRaisesRegex(ValueError, "ASSESSED_ORDER_CHANGED"):
            await self.cancel()
        self.assertEqual(0, self.transport.cancel_calls)
        self.refresh_hook = None
        result = await self.cancel(self.repository.load("intent-1"))
        self.assertEqual((OrderState.CANCEL_PENDING, 1), (result.state, result.detailed_filled_quantity))

    async def test_full_fill_during_refresh_prevents_cancellation(self):
        self.refresh_hook = lambda: self.reconcile(OrderState.FILLED, 3, 0)
        with self.assertRaisesRegex(ValueError, "ASSESSED_ORDER_CHANGED"):
            await self.cancel()
        self.assertEqual(0, self.transport.cancel_calls)

    async def test_unchanged_exposure_with_newer_rest_confirmation_can_cancel(self):
        self.record = self.runtime.reconcile("intent-1", BrokerOrderSnapshot(
            "broker-1", "account-1", "005930", OrderState.ACCEPTED, 0, 3, NOW, (),
        ))
        self.refresh_hook = lambda: self.reconcile(OrderState.ACCEPTED, 0, 3)
        result = await self.cancel()
        self.assertEqual(OrderState.CANCEL_PENDING, result.state)
        self.assertEqual(NOW + timedelta(seconds=1), result.last_broker_as_of)
        self.assertEqual((1, 1), (self.refreshes, self.transport.cancel_calls))

    async def test_confirmation_time_cannot_regress_from_assessed_order(self):
        expected = replace(self.record, last_broker_as_of=NOW + timedelta(seconds=2))
        with self.assertRaisesRegex(ValueError, "ASSESSED_ORDER_CHANGED"):
            await self.cancel(expected)
        self.assertEqual(0, self.transport.cancel_calls)

    async def test_unknown_order_cannot_start_cancel_or_duplicate_submit(self):
        from kiwoom_monitor.domain.order_contract import order_event
        current = self.repository.load("intent-1")
        unknown = self.repository.apply(current, order_event(
            "intent-1", "TEST_UNKNOWN", OrderState.SUBMISSION_UNKNOWN, NOW, NOW,
        ))
        with self.assertRaisesRegex(ValueError, "REQUIRES_RECONCILED_ORDER"):
            await self.cancel(unknown)
        self.assertEqual((0, 0, 1), (self.refreshes, self.transport.cancel_calls, self.transport.submit_calls))

    async def test_foreign_expected_intent_and_lost_owner_cannot_cancel(self):
        wrong = replace(self.record, intent=replace(self.record.intent, account_ref="account-2"))
        with self.assertRaisesRegex(ValueError, "ASSESSED_ORDER_CHANGED"):
            await self.cancel(wrong)
        self.assertEqual(0, self.refreshes)
        self.runtime.stop()
        with self.assertRaisesRegex(RuntimeError, "has not acquired ownership"):
            await self.cancel()
        self.assertEqual(0, self.transport.cancel_calls)

    async def test_manual_gate_must_remain_closed_for_automatic_cancel(self):
        self.runtime.set_new_orders_enabled(True)
        with self.assertRaisesRegex(RuntimeError, "REQUIRES_CLOSED_ORDER_GATE"):
            await self.cancel()
        self.assertEqual(0, self.transport.cancel_calls)
        self.runtime.set_new_orders_enabled(False)

    async def test_owner_loss_in_cancel_started_transaction_blocks_transport(self):
        from unittest.mock import patch
        apply = self.repository.apply
        def owner_loss(record, event, **kwargs):
            if event.event_type == "CANCEL_STARTED":
                self.runtime.stop()
            return apply(record, event, **kwargs)
        with patch.object(self.repository, "apply", side_effect=owner_loss):
            with self.assertRaisesRegex(RuntimeError, "EXECUTION_OWNERSHIP_LOST"):
                await self.cancel()
        self.assertEqual(0, self.transport.cancel_calls)

    async def test_caller_cancel_and_credential_close_drain_existing_command(self):
        entered = asyncio.Event(); release = asyncio.Event()
        async def delayed():
            entered.set(); await release.wait(); return _account()
        self.gateway._account_refresh = delayed
        caller = asyncio.create_task(self.cancel())
        await entered.wait()
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        close = asyncio.create_task(self.gateway.close())
        await asyncio.sleep(0)
        self.assertFalse(close.done())
        release.set(); await close
        self.assertEqual(1, self.transport.cancel_calls)
        self.assertEqual(OrderState.CANCEL_PENDING, self.repository.load("intent-1").state)
        with self.assertRaisesRegex(RuntimeError, "CREDENTIAL_CHANGE"):
            await self.cancel()


class UnfilledPairedEvidenceTests(unittest.TestCase):
    def setUp(self):
        UnfilledOrderManagementTests.setUp(self)
        model=replace(self.target.cost_model,source='fixture declared cost',rate_basis='model_estimate',
            valid_from='2026-01-01T00:00:00+00:00',valid_to='2027-01-01T00:00:00+00:00')
        self.target=replace(self.target,cost_model=model)
    setup_order=UnfilledOrderManagementTests.setup_order

    @staticmethod
    def outcomes_for(record,policy,cost,cut,pending,held,target,price):
        from kiwoom_monitor.application.research_evaluation import UnfilledActionOutcome,position_action_cost_model_ref
        rows=[]
        active=record.state in {OrderState.ACCEPTED,OrderState.PARTIALLY_FILLED}
        for case,gain in (('A',30),('B',50)):
            for action in ('WAIT','REPRICE'):
                rows.append(UnfilledActionOutcome(input_ref=case+':'+action,case_ref=case,initial_state_ref='initial:'+case,
                    action=action,policy_version='unfilled-order-management-shadow/v1',environment='simulation',venue='KRX',
                    source_kind='simulation',execution_model_ref='unfilled-paired-limit/v1',initial_quantity=held,
                    pending_buy_quantity=pending if active and record.intent.side is OrderSide.BUY else 0,
                    pending_sell_quantity=pending if active and record.intent.side is OrderSide.SELL else 0,
                    target_quantity=target,horizon_seconds=policy.reprice_horizon_seconds,population_ref='cohort:unfilled',
                    input_resolution='second',cost_model_ref=position_action_cost_model_ref(cost.to_dict()),
                    started_at=cut-timedelta(seconds=policy.reprice_horizon_seconds),completed_at=cut,available_at=cut,
                    status='COMPLETE',data_gap_count=0,gross_change_won=100+(gain if action=='REPRICE' else 0),
                    fee_tax_won=13 if action=='REPRICE' else 3,symbol=record.intent.symbol,side=record.intent.side.value,
                    purpose=policy.purpose,pending_quantity=pending,existing_limit_price_won=record.intent.limit_price,
                    candidate_limit_price_won=price,order_state=record.state.value))
        return tuple(rows)

    def prepare(self, rows=None, **changes):
        from kiwoom_monitor.application.order_lifecycle import prepare_unfilled_reprice_evidence
        values=dict(policy=self.policy,record=self.record,pending_quantity=10,held_quantity=0,target_quantity=10,
                    candidate_price=1010,cost_model=self.target.cost_model,population_ref='cohort:unfilled',
                    available_at=self.now[0],decision_time=self.now[0])
        values.update(changes)
        if rows is None:rows=self.outcomes_for(self.record,self.policy,self.target.cost_model,self.now[0],10,0,10,1010)
        return prepare_unfilled_reprice_evidence(rows,**values)

    def test_common_paired_difference_subtracts_incremental_cost_once(self):
        evidence,report=self.prepare()
        self.assertEqual((2,30,10),(evidence.paired_sample_count,evidence.net_advantage_won,evidence.net_standard_error_won))
        self.assertEqual((self.now[0],self.intent.intent_id,10),(evidence.available_at,evidence.intent_id,evidence.pending_quantity))
        self.assertEqual(('EMPIRICAL_ESTIMATE','not_verified',False),
            (report['comparison']['ev_status'],report['source_durability'],report['order_authorized']))

    def test_partial_gap_future_and_missing_cost_never_become_zero_samples(self):
        original=self.outcomes_for(self.record,self.policy,self.target.cost_model,self.now[0],10,0,10,1010)
        for change in ({'status':'PARTIAL'},{'data_gap_count':1},{'fee_tax_won':None},
                       {'available_at':self.now[0]+timedelta(microseconds=1)},
                       {'completed_at':None}):
            rows=(*original[:-1],replace(original[-1],**change))
            with self.subTest(change=change):
                evidence,report=self.prepare(rows)
                self.assertIsNone(evidence)
                self.assertEqual(1,report['comparison']['paired_sample_count'])

    def test_pair_initial_state_mismatch_and_missing_wait_are_unavailable(self):
        rows=self.outcomes_for(self.record,self.policy,self.target.cost_model,self.now[0],10,0,10,1010)
        evidence,report=self.prepare((*rows[:-1],replace(rows[-1],initial_state_ref='different-initial')))
        self.assertIsNone(evidence);self.assertEqual(1,report['comparison']['paired_sample_count'])
        evidence,_=self.prepare(tuple(row for row in rows if row.action=='REPRICE'))
        self.assertIsNone(evidence)

    def test_current_price_pending_position_cost_and_model_are_exact_bindings(self):
        for changes in ({'candidate_price':1009},{'pending_quantity':9},{'held_quantity':1},{'target_quantity':11},
                        {'cost_model':replace(self.target.cost_model,slippage_bps=1)}):
            with self.subTest(changes=changes):self.assertIsNone(self.prepare(**changes)[0])

    def test_source_publication_and_evidence_ref_are_not_renewed_by_account_clock(self):
        first,report=self.prepare()
        next_value,next_report=self.prepare(decision_time=self.now[0]+timedelta(seconds=1))
        self.assertEqual(first,next_value);self.assertEqual(report,next_report)

    def test_cost_expiry_and_unspecified_source_do_not_support_reprice(self):
        for model in (None,replace(self.target.cost_model,source=''),
                      replace(self.target.cost_model,rate_basis='unspecified'),
                      replace(self.target.cost_model,valid_to=self.now[0].isoformat())):
            with self.subTest(model=model):self.assertIsNone(self.prepare(cost_model=model)[0])

    def test_duplicate_case_or_ref_is_rejected(self):
        rows=self.outcomes_for(self.record,self.policy,self.target.cost_model,self.now[0],10,0,10,1010)
        with self.assertRaisesRegex(ValueError,'duplicate'):self.prepare((*rows,rows[0]))

    def test_position_hold_exit_cannot_be_relabelled_or_used_as_pending_order_outcomes(self):
        from kiwoom_monitor.application.research_evaluation import PositionActionOutcome
        from dataclasses import fields
        row=self.outcomes_for(self.record,self.policy,self.target.cost_model,self.now[0],10,0,10,1010)[0]
        body={f.name:getattr(row,f.name) for f in fields(PositionActionOutcome)}
        with self.assertRaises(ValueError):PositionActionOutcome(**body)
        ordinary=PositionActionOutcome(**{**body,'action':'HOLD'})
        with self.assertRaisesRegex(ValueError,'explicit bounded'):self.prepare((ordinary,))

    def test_accepted_order_cohort_does_not_support_reentry_after_cancel_confirmation(self):
        rows=self.outcomes_for(self.record,self.policy,self.target.cost_model,self.now[0],10,0,10,1010)
        self.assertIsNone(self.prepare(rows,record=replace(self.record,state=OrderState.CANCELLED))[0])

    def test_minute_population_needs_a_whole_minute_horizon_and_no_second_bar_requirement(self):
        policy=replace(self.policy,reprice_horizon_seconds=60)
        rows=self.outcomes_for(self.record,policy,self.target.cost_model,self.now[0],10,0,10,1010)
        rows=tuple(replace(row,input_resolution='minute') for row in rows)
        self.assertIsNotNone(self.prepare(rows,policy=policy)[0])
        with self.assertRaises(ValueError):replace(rows[0],horizon_seconds=10)


class LocalRehearsalTests(unittest.TestCase):
    class Broker:
        environment = 'real'

        def __init__(self, clock, venue):
            self.clock, self.venue = clock, venue
            self.calls, self.orders = [], {}
            self.lose_amendment_reply = False

        def request_once(self, api_id, path, body):
            from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomSubmissionUnknownError
            assert path == '/api/dostk/ordr' and body['dmst_stex_tp'] == self.venue and body['stk_cd'] == '005930'
            self.calls.append((api_id, dict(body)))
            number = f'{len(self.calls):07d}'
            response = {'ord_no': number, 'dmst_stex_tp': self.venue, 'return_code': 0}
            if api_id in {'kt10000', 'kt10001'}:
                self.orders[number] = dict(side=OrderSide.BUY if api_id == 'kt10000' else OrderSide.SELL,
                    quantity=int(body['ord_qty']), remaining=int(body['ord_qty']), price=int(body['ord_uv']),
                    parent=None, fills=[], state=OrderState.ACCEPTED)
            elif api_id == 'kt10002':
                original = body['orig_ord_no']
                source = self.orders[original]
                quantity = int(body['mdfy_qty'])
                assert quantity == source['remaining']
                source.update(remaining=0, state=OrderState.FILLED if source['fills'] else OrderState.CANCELLED)
                self.orders[number] = dict(side=source['side'], quantity=quantity, remaining=quantity,
                    price=int(body['mdfy_uv']), parent=original, fills=[], state=OrderState.ACCEPTED)
                if self.lose_amendment_reply:
                    raise KiwoomSubmissionUnknownError('test peer accepted amendment but dropped reply')
                response.update(base_orig_ord_no=original, mdfy_qty=f'+{quantity:011d}')
            else:
                assert api_id == 'kt10003' and body['cncl_qty'] == '0'
                assert self.orders[body['orig_ord_no']]['remaining'] > 0
            return response

        def fill(self, number, quantity):
            order = self.orders[number]
            assert 0 < quantity <= order['remaining']
            order['remaining'] -= quantity
            order['fills'].append(BrokerFill(f'fill-{len(order["fills"])}', quantity, order['price'], self.clock[0]))
            order['state'] = OrderState.PARTIALLY_FILLED if order['remaining'] else OrderState.FILLED

        def read_recovery(self):
            from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import AccountRecovery, BrokerOrderDetails
            positions, reserved, orders, details = 0, 0, [], []
            for number, row in self.orders.items():
                filled = sum(fill.quantity for fill in row['fills'])
                positions += filled if row['side'] is OrderSide.BUY else -filled
                reserved += row['remaining'] * row['price'] if row['side'] is OrderSide.BUY else 0
                orders.append(BrokerOrderSnapshot(number, 'fixture-real', '005930', row['state'],
                    filled, row['remaining'], self.clock[0], tuple(row['fills'])))
                details.append(BrokerOrderDetails(number, row['quantity'], row['side'], row['price'], self.venue,
                                                  original_broker_order_id=row['parent']))
            return AccountRecovery(AccountSnapshot('fixture-real', 1_000_000, reserved,
                {'005930': positions} if positions else {}, self.clock[0]), tuple(orders), tuple(details))

    def test_real_krx_and_nxt_rehearse_orders_and_recover_physical_remainder_without_resending(self):
        from kiwoom_monitor.application.market_session_schedule import MarketPhase, MarketSession
        from kiwoom_monitor.central_server.local_order_rehearsal import LocalOrderRehearsal, RehearsalConditions
        routes = (('KRX', MarketSession.KRX_REGULAR, 0, 5),
                  ('NXT', MarketSession.NXT_PRE_MARKET, -1, 15),
                  ('NXT', MarketSession.NXT_MAIN_MARKET, 0, 5),
                  ('NXT', MarketSession.NXT_AFTER_MARKET, 7, 0))
        for venue, session, hours, minutes in routes:
            with self.subTest(venue=venue, session=session), tempfile.TemporaryDirectory() as directory:
                clock = [NOW + timedelta(hours=hours, minutes=minutes)]
                peer = self.Broker(clock, venue)
                settings = dict(account_ref='fixture-real', run_id='local-run', symbol='005930', venue=venue,
                    sessions=(session,), maximum_age=timedelta(seconds=3), now_provider=lambda: clock[0])

                def open_run():
                    return LocalOrderRehearsal(Path(directory), peer, **settings)

                def conditions(price, valid=True, *, eligible=True, phase=MarketPhase.CONTINUOUS):
                    return lambda _: RehearsalConditions('005930', venue, clock[0], phase, eligible, valid, price)

                def tick():
                    clock[0] += timedelta(seconds=1)

                with open_run() as local:
                    self.assertIsNone(local.step(OrderSide.BUY, current_conditions=conditions(100, eligible=None), entry_quantity=5))
                    self.assertIsNone(local.step(OrderSide.BUY, current_conditions=conditions(100, phase=MarketPhase.UNKNOWN), entry_quantity=5))
                    self.assertEqual([], peer.calls)
                    buy = local.step(OrderSide.BUY, current_conditions=conditions(100), entry_quantity=5)
                    self.assertEqual(('real', venue, OrderState.ACCEPTED), (buy.intent.environment, buy.intent.venue, buy.state))
                    tick()
                    peer.fill('0000001', 2)
                    partial = local.step(OrderSide.BUY, current_conditions=conditions(100))
                    self.assertEqual((2, OrderState.PARTIALLY_FILLED), (partial.detailed_filled_quantity, partial.state))
                    tick()
                    peer.lose_amendment_reply = True
                    unknown = local.step(OrderSide.BUY, current_conditions=conditions(103))
                    self.assertEqual((OrderState.SUBMISSION_UNKNOWN, 'UNKNOWN'), (unknown.state, unknown.amendment.status))
                    self.assertEqual(['kt10000', 'kt10002'], [api for api, _ in peer.calls])

                with open_run() as restarted:
                    tick()
                    recovered = restarted.step(OrderSide.BUY, current_conditions=conditions(103))
                    self.assertEqual('ACCEPTED', recovered.amendment.status)
                    self.assertEqual(2, len(peer.calls))
                    tick()
                    cancelled = restarted.step(OrderSide.BUY, current_conditions=conditions(None, False))
                    self.assertEqual(OrderState.CANCEL_PENDING, cancelled.state)
                    tick()
                    self.assertIsNone(restarted.step(OrderSide.SELL, current_conditions=conditions(97)))
                    self.assertEqual(3, len(peer.calls))

                tick()
                peer.fill('0000002', 1)
                peer.orders['0000002'].update(state=OrderState.CANCELLED, remaining=0)
                with open_run() as restarted:
                    sell = restarted.step(OrderSide.SELL, current_conditions=conditions(97))
                    self.assertEqual((3, OrderState.ACCEPTED), (sell.intent.quantity, sell.state))
                    self.assertEqual((OrderState.CANCELLED, 3), (restarted.read_order(OrderSide.BUY).state,
                                                                restarted.read_order(OrderSide.BUY).detailed_filled_quantity))
                    tick()
                    peer.fill('0000004', 3)
                    flat = restarted.step(OrderSide.SELL, current_conditions=conditions(97))
                    self.assertEqual(OrderState.FILLED, flat.state)
                    self.assertEqual({}, peer.read_recovery().account.positions)
                    self.assertIn('AMENDMENT_RECOVERED', {row['event_type'] for row in restarted.read_events(OrderSide.BUY)})
                with open_run() as restarted:
                    tick()
                    self.assertEqual(OrderState.FILLED, restarted.step(OrderSide.SELL, current_conditions=conditions(97)).state)
                self.assertEqual(['kt10000', 'kt10002', 'kt10003', 'kt10001'], [api for api, _ in peer.calls])
                self.assertEqual(('0000001', '3', '103'), tuple(peer.calls[1][1][field]
                                                                 for field in ('orig_ord_no', 'mdfy_qty', 'mdfy_uv')))
                self.assertEqual('0000002', peer.calls[2][1]['orig_ord_no'])

    def test_rehearsal_rejects_production_ledger_client_and_crossed_broker_venue(self):
        from kiwoom_monitor.application.market_session_schedule import MarketPhase, MarketSession
        from kiwoom_monitor.central_server.local_order_rehearsal import LocalOrderRehearsal, RehearsalConditions
        from kiwoom_monitor.domain.order_contract import LocalRehearsalOrderIntent
        from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient
        from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            store = SQLiteQueryStore(path / 'normal.sqlite3')
            store.initialize()
            self.addCleanup(store.close)
            rehearsal_intent = LocalRehearsalOrderIntent('id', 'run', 'decision', 'fixture-real', 'real', '005930',
                'NXT', OrderSide.BUY, 2, OrderType.LIMIT, 100, NOW, NOW + timedelta(minutes=1), 'rehearsal')
            with self.assertRaisesRegex(ValueError, 'ledger mode'):
                ExecutionRepository(store).create(rehearsal_intent)
            with self.assertRaisesRegex(ValueError, 'mock environment'):
                replace(_intent(), environment='real')

            clock = [NOW + timedelta(minutes=5)]
            config = dict(account_ref='fixture-real', run_id='local-run', symbol='005930', venue='NXT',
                sessions=(MarketSession.NXT_MAIN_MARKET,), maximum_age=timedelta(seconds=3), now_provider=lambda: clock[0])
            with self.assertRaisesRegex(ValueError, 'live Kiwoom client'):
                LocalOrderRehearsal(path / 'unused', KiwoomRestClient(KiwoomSettings('', '', 'real')), **config)
            peer = self.Broker(clock, 'NXT')
            with self.assertRaisesRegex(ValueError, 'empty directory'):
                LocalOrderRehearsal(path, peer, **config)
            with LocalOrderRehearsal(path / 'rehearsal', peer, **config) as local:
                def current(_):
                    return RehearsalConditions('005930', 'NXT', clock[0], MarketPhase.CONTINUOUS, True, True, 100)
                local.step(OrderSide.BUY, current_conditions=current, entry_quantity=2)
                clock[0] += timedelta(seconds=1)
                peer.venue = 'KRX'
                with self.assertRaisesRegex(ValueError, 'does not match the route'):
                    local.step(OrderSide.BUY, current_conditions=current)
                self.assertEqual(1, len(peer.calls))


if __name__ == "__main__":
    unittest.main()
