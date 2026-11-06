"""Native member facts stay inside the same fresh account Risk snapshot."""
from dataclasses import replace
from datetime import datetime,timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def member_fill_inputs(case,store):
    from tests.unit.test_mock_automation_account_execution import verify_group_native_reservation
    from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
    from kiwoom_monitor.domain.order_contract import AccountSnapshot,BrokerFill,BrokerOrderSnapshot,OrderState
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
    data,owner,recovery,risk,planned,buys=verify_group_native_reservation(case,store)
    for buy,price in zip(buys,(69_000,70_000),strict=True):
        owner.clock[0]+=timedelta(seconds=1)
        snapshot=BrokerOrderSnapshot(buy.broker_order_id,owner.runtime.account_ref,buy.intent.symbol,
            OrderState.FILLED,1,0,owner.clock[0],(BrokerFill('same-raw-id',1,price,owner.clock[0]),))
        owner.runtime.reconcile(buy.intent.intent_id,snapshot)
        owner.runtime.reconcile(buy.intent.intent_id,snapshot)
    owner.clock[0]+=timedelta(seconds=1)
    events=owner.execution.account_events('mock',owner.runtime.account_ref).events
    recovery=MockAccountRecovery(AccountSnapshot(owner.runtime.account_ref,111_000,0,{'005930':2},owner.clock[0]),())
    build=dict(execution_run_id=owner.run_id,binding_revision=data.selection.binding_revision,reconciliation_revision=2,
        observed_at=owner.clock[0],broker_query_started_at=owner.clock[0],broker_query_completed_at=owner.clock[0])
    risk=build_mock_automation_risk_snapshot(recovery,events,(),(),**build)
    case.assertTrue(risk.reconciliation_complete)
    return data,owner,planned,buys,events,recovery,risk,build


def verify_member_risk_and_positions(case,store):
    from kiwoom_monitor.application.mock_automation_execution import dispatch_mock_automation_decision_from_risk
    from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
    from kiwoom_monitor.application.mock_automation_risk import (
        build_mock_automation_risk_snapshot,mock_automation_risk_snapshot_from_dict,validate_planned_leader_current_cut,
    )
    from kiwoom_monitor.central_server.mock_automation_episode import MockAutomationEpisodePosition
    from kiwoom_monitor.domain.order_contract import AccountSnapshot,BrokerFill,BrokerOrderSnapshot,OrderState
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery
    from tests.unit import test_mock_automation_specification as support
    data,owner,planned,buys,events,recovery,risk,build=member_fill_inputs(case,store)
    fills=[e for e in events if e.event_type=='FILL']
    case.assertEqual(2,len(fills),'same raw fill ID on two physical orders is two actual fills')
    case.assertEqual(list(data.selection.member_spec_ids),[e.member_spec_id for e in fills])
    case.assertEqual(list(data.selection.member_spec_ids),[e.to_dict()['member_spec_id'] for e in fills])
    case.assertEqual(list(data.selection.member_spec_ids),[owner.execution.load(buy.intent.intent_id).member_spec_id for buy in buys])
    case.assertEqual('COMPLETE',risk.member_attribution)
    case.assertEqual((('005930',2),),risk.owned_position_quantities)
    case.assertEqual((('005930',69_500),),risk.owned_entry_prices)
    case.assertEqual(risk,mock_automation_risk_snapshot_from_dict(risk.to_dict()))
    data.repository.save_mock_automation_risk_snapshot(risk)
    states=[]
    for spec,price in zip(data.specs,(69_000,70_000),strict=True):
        case.assertEqual((('005930',1),),risk.owned_for_member(spec.spec_id))
        case.assertEqual((('005930',price),),risk.entry_prices_for_member(spec.spec_id))
        state=MockAutomationEpisodePosition(account_ref=owner.runtime.account_ref,run_id=owner.run_id,member_spec_id=spec.spec_id)
        state.apply_new_fills(owner.execution,leader=True,config=None,position_time=lambda:owner.clock[0])
        case.assertEqual((1,price),(state.state.position_quantity,state.state.entry_price))
        case.assertTrue(state.matches_risk(risk))
        case.assertEqual(max(e.accepted_sequence for e in events),state.fill_cursor)
        states.append(state)
        record_mock_automation_recovery_from_risk(data.repository,owner.runtime,recovery,risk,
            account_ref=owner.runtime.account_ref,spec_id=spec.spec_id)
    for (spec,gate,intent) in planned:
        head=store.load_document('execution_mock_plan_positions',owner.runtime.account_ref,intent.plan.plan_id)['document']
        plan=replace(intent.plan,expected_revision=head['revision'])
        case.assertEqual(1,validate_planned_leader_current_cut(plan,spec=spec,run_id=owner.run_id,
            symbol=intent.symbol,risk=risk,plan_position=head,now=owner.clock[0]))
    plan=planned[0][2].plan
    head=owner.execution.load_plan_position(owner.runtime.account_ref,owner.run_id,'005930',plan)
    plan=head.plan
    decision=support._automation_decision(owner.run_id,'EXIT',owner.clock[0])
    gate,sell,receipt=dispatch_mock_automation_decision_from_risk(data.repository,owner.runtime,decision,recovery,risk,
        account_ref=owner.runtime.account_ref,spec_id=data.specs[0].spec_id,strategy_ref=data.specs[0].strategy_ref,plan=plan)
    case.assertEqual(risk.snapshot_id,gate.risk_snapshot_id,'scoping never makes a replacement Risk identity')
    pending=owner.runtime.load_active_intents()
    case.assertEqual((sell.intent.intent_id,data.specs[0].spec_id),(pending[0].intent.intent_id,pending[0].member_spec_id))
    # The existing order-free study codec keeps the same native plan/member,
    # while the pre-member standalone record shape remains independently frozen.
    from dataclasses import asdict
    import json
    from kiwoom_monitor.application.position_target import _position_account_orders_from_dict
    account_wire=json.loads(json.dumps(asdict(recovery.account),default=lambda item:item.isoformat()))
    orders_wire=json.loads(json.dumps([asdict(row) for row in pending],default=lambda item:item.isoformat()))
    case.assertEqual((recovery.account,pending),_position_account_orders_from_dict(account_wire,orders_wire))
    current_events=owner.execution.account_events('mock',owner.runtime.account_ref).events
    order=BrokerOrderSnapshot(sell.broker_order_id,owner.runtime.account_ref,'005930',OrderState.ACCEPTED,0,1,owner.clock[0])
    pending_recovery=replace(recovery,orders=(order,))
    pending_risk=build_mock_automation_risk_snapshot(pending_recovery,current_events,(),pending,
        **{**build,'reconciliation_revision':3})
    case.assertEqual((('005930',1),),pending_risk.pending_sell_quantities)
    case.assertEqual((('005930',1),),pending_risk.pending_sells_for_member(data.specs[0].spec_id))
    case.assertEqual((),pending_risk.pending_sells_for_member(data.specs[1].spec_id))
    beta_head=store.load_document('execution_mock_plan_positions',owner.runtime.account_ref,planned[1][2].plan.plan_id)['document']
    beta_plan=replace(planned[1][2].plan,expected_revision=beta_head['revision'])
    case.assertEqual(1,validate_planned_leader_current_cut(beta_plan,spec=data.specs[1],run_id=owner.run_id,
        symbol='005930',risk=pending_risk,plan_position=beta_head,now=owner.clock[0]))
    before_beta=states[1].state
    owner.clock[0]+=timedelta(seconds=1)
    owner.runtime.reconcile(sell.intent.intent_id,BrokerOrderSnapshot(sell.broker_order_id,owner.runtime.account_ref,
        '005930',OrderState.FILLED,1,0,owner.clock[0],(BrokerFill('alpha-exit',1,70_500,owner.clock[0]),)))
    events=owner.execution.account_events('mock',owner.runtime.account_ref).events
    remaining=MockAccountRecovery(AccountSnapshot(owner.runtime.account_ref,181_500,0,{'005930':1},owner.clock[0]),())
    after=build_mock_automation_risk_snapshot(remaining,events,(),(),**{**build,'reconciliation_revision':4,
        'observed_at':owner.clock[0],'broker_query_started_at':owner.clock[0],'broker_query_completed_at':owner.clock[0]})
    for state in states:
        state.apply_new_fills(owner.execution,leader=True,config=None,position_time=lambda:owner.clock[0])
        case.assertTrue(state.matches_risk(after))
    case.assertEqual('flat',states[0].state.status)
    case.assertEqual(before_beta,states[1].state)
    case.assertEqual((),after.owned_for_member(data.specs[0].spec_id))
    case.assertEqual((('005930',1),),after.owned_for_member(data.specs[1].spec_id))
    case.assertEqual(3,len(owner.transport.calls))
    return data,owner,events,after


def verify_missing_member_facts(case,store):
    from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot,mock_automation_risk_snapshot_from_dict
    data,owner,planned,buys,events,recovery,risk,build=member_fill_inputs(case,store)
    # Model old/missing ownership metadata, preserving the actual physical FILL.
    unknown=tuple(replace(e,member_spec_id=None) if e.member_spec_id==data.specs[1].spec_id else e for e in events)
    partial=build_mock_automation_risk_snapshot(recovery,unknown,(),(),**build)
    case.assertTrue(partial.reconciliation_complete,'physical account facts remain readable')
    case.assertEqual(risk.owned_position_quantities,partial.owned_position_quantities)
    case.assertEqual(risk.daily_net_pnl_won,partial.daily_net_pnl_won)
    case.assertEqual('INCOMPLETE',partial.member_attribution)
    case.assertEqual(partial,mock_automation_risk_snapshot_from_dict(partial.to_dict()))
    false_complete=partial.to_dict()
    false_complete['member_attribution']='COMPLETE'
    with case.assertRaisesRegex(ValueError,'complete member facts'):
        mock_automation_risk_snapshot_from_dict(false_complete)
    with case.assertRaisesRegex(ValueError,'attribution'):
        partial.owned_for_member(data.specs[1].spec_id)
    legacy=build_mock_automation_risk_snapshot(recovery,tuple(replace(e,member_spec_id=None) for e in events),(),(),**build)
    case.assertNotIn('member_attribution',legacy.to_dict())
    with case.assertRaisesRegex(ValueError,'attribution'):
        legacy.owned_for_member(data.specs[0].spec_id)
    case.assertTrue(all(e.member_spec_id is not None for e in events))
    case.assertEqual(2,len([e for e in unknown if e.event_type=='FILL']))


class MockMemberRiskTests(unittest.TestCase):
    def setUp(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store=SQLiteQueryStore(root/'members.sqlite3')
        self.store.initialize()
        self.addCleanup(self.store.close)

    def test_native_fills_pending_sells_and_position_owners_use_exact_members_of_one_risk(self):
        verify_member_risk_and_positions(self,self.store)

    def test_missing_attribution_preserves_the_account_and_never_becomes_a_zero_member_position(self):
        verify_missing_member_facts(self,self.store)

    def test_member_identity_can_only_be_assigned_by_the_native_claim(self):
        from tests.unit.test_mock_automation_account_execution import group_planned_inputs
        from kiwoom_monitor.domain.order_contract import OrderState,order_event
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRecord,_record_document
        data,owner,recovery,risk,planned=group_planned_inputs(self,self.store)
        spec,gate,intent=planned[0]
        record=ExecutionRecord(intent,OrderState.QUEUED,'',0,(),None,intent.created_at)
        candidate={**_record_document(record),'mock_account_member_spec_id':data.specs[1].spec_id}
        with self.assertRaisesRegex(ValueError,'assigned only by the native'):
            self.store.create_execution_intent(candidate,**owner.execution._write_options())
        self.assertIsNone(owner.execution.load(intent.intent_id))
        actual=owner.runtime.submit_automation_intent(intent,recovery.account,spec_id=spec.spec_id,
            control_revision=gate.control_revision,plan_recovery=recovery)
        before=owner.execution.events(intent.intent_id)
        forged=replace(actual,member_spec_id=data.specs[1].spec_id)
        event=order_event(intent.intent_id,'BROKER_RECONCILED',actual.state,owner.clock[0],owner.clock[0])
        with self.assertRaisesRegex(RuntimeError,'MEMBER_ATTRIBUTION_CHANGED'):
            owner.execution.apply(forged,event)
        self.assertEqual(actual,owner.execution.load(intent.intent_id))
        self.assertEqual(before,owner.execution.events(intent.intent_id))

    def test_native_events_retain_ownership_when_an_older_writer_omits_the_optional_field(self):
        from tests.unit.test_mock_automation_account_execution import group_planned_inputs
        from kiwoom_monitor.infrastructure.persistence import execution_repository as module
        from kiwoom_monitor.domain.order_contract import BrokerFill,BrokerOrderSnapshot,OrderState
        data,owner,recovery,risk,planned=group_planned_inputs(self,self.store)
        spec,gate,intent=planned[0]
        buy=owner.runtime.submit_automation_intent(intent,recovery.account,spec_id=spec.spec_id,
            control_revision=gate.control_revision,plan_recovery=recovery)
        encode=module._record_document
        def old_wire(record):
            value=encode(record);value.pop('mock_account_member_spec_id',None);return value
        owner.clock[0]+=timedelta(seconds=1)
        with patch.object(module,'_record_document',side_effect=old_wire):
            owner.runtime.reconcile(intent.intent_id,BrokerOrderSnapshot(buy.broker_order_id,owner.runtime.account_ref,
                intent.symbol,OrderState.FILLED,1,0,owner.clock[0],(BrokerFill('old-wire-fill',1,69_000,owner.clock[0]),)))
        self.assertEqual(spec.spec_id,owner.execution.load(intent.intent_id).member_spec_id)
        events=owner.execution.account_events('mock',owner.runtime.account_ref).events
        self.assertTrue(events)
        self.assertEqual({spec.spec_id},{e.member_spec_id for e in events})

    def test_shared_freshness_is_checked_again_at_submission_after_a_durable_queue(self):
        from tests.unit.test_mock_automation_account_execution import group_planned_inputs
        from kiwoom_monitor.domain.execution_activation import MockAutomationAccountLimits
        data,owner,recovery,risk,planned=group_planned_inputs(self,self.store,
            limits=MockAutomationAccountLimits(2,2,150_000,300_000,1,0,2,0))
        spec,gate,intent=planned[0]
        append=self.store.append_execution_event
        def delayed(document,event,**options):
            if event['event_type']=='SUBMISSION_STARTED':
                owner.clock[0]+=timedelta(seconds=2)
            return append(document,event,**options)
        with patch.object(self.store,'append_execution_event',side_effect=delayed):
            with self.assertRaisesRegex(RuntimeError,'PLANNED_EXECUTION_RISK_OR_RECOVERY_CHANGED'):
                owner.runtime.submit_automation_intent(intent,recovery.account,spec_id=spec.spec_id,
                    control_revision=gate.control_revision,plan_recovery=recovery)
        queued=owner.execution.load(intent.intent_id)
        self.assertEqual('QUEUED',queued.state.value)
        self.assertEqual(spec.spec_id,queued.member_spec_id)
        self.assertEqual([],owner.transport.calls)
        self.assertEqual(queued,owner.runtime.submit_automation_intent(intent,recovery.account,spec_id=spec.spec_id,
            control_revision=gate.control_revision,plan_recovery=recovery))
        self.assertEqual([],owner.transport.calls)

    def test_authenticated_account_event_api_preserves_native_member_ids_and_physical_fill_identity(self):
        from fastapi import FastAPI,Header,HTTPException
        from fastapi.testclient import TestClient
        from pydantic import BaseModel
        from types import SimpleNamespace
        from kiwoom_monitor.central_server.mock_order_routes import create_mock_order_routers
        from kiwoom_monitor.domain.order_contract import AccountBinding
        data,owner,planned,buys,events,recovery,risk,build=member_fill_inputs(self,self.store)
        binding=AccountBinding(data.selection.credential_profile_id,data.selection.account_scope,
            data.selection.binding_revision,datetime.fromisoformat(data.binding['verified_at']))
        class Target(BaseModel):
            broker:str
            environment:str
            account_ref:str
        def authorize(authorization:str|None=Header(default=None)):
            if authorization!='Bearer member-test':raise HTTPException(401)
        def selected(account_ref,scope,profile,revision,**options):
            current=self.store.load_latest_account_binding(profile,'kiwoom','mock')
            if (current is None or scope!=binding.scope.to_dict() or account_ref!=binding.scope.account_ref
                    or revision!=current['binding_revision'] or account_ref!=current['account_ref']):
                raise HTTPException(409,detail='current verified account mismatch')
            return binding,SimpleNamespace(repository=owner.execution)
        app=FastAPI()
        _,router=create_mock_order_routers(authorize,Target,lambda:None,selected)
        app.include_router(router)
        url=f'/api/v2/mock/accounts/{binding.scope.account_ref}/execution-events'
        query=dict(credential_profile_id=binding.credential_profile_id,
            expected_binding_revision=binding.binding_revision,after_sequence=0,limit=1000)
        with TestClient(app) as client:
            self.assertEqual(401,client.get(url,params=query).status_code)
            reply=client.get(url,params=query,headers={'Authorization':'Bearer member-test'})
            self.assertEqual(200,reply.status_code,reply.text)
            document=reply.json()
            self.assertEqual(binding.scope.account_ref,document['context']['account_ref'])
            fills=[row for row in document['events'] if row['event_type']=='FILL']
            self.assertEqual(list(data.selection.member_spec_ids),[row['member_spec_id'] for row in fills])
            self.assertEqual(['same-raw-id']*2,[row['broker_execution_id'] for row in fills])
            self.assertEqual(2,len({row['broker_order_id'] for row in fills}))
            self.assertEqual(409,client.get(url,params={**query,'expected_binding_revision':binding.binding_revision+1},
                headers={'Authorization':'Bearer member-test'}).status_code)
        self.assertEqual(2,len(owner.transport.calls))

    def test_standalone_dataclass_research_identity_matches_the_frozen_pre_member_shape(self):
        from dataclasses import asdict
        from datetime import timezone
        import hashlib,json
        from kiwoom_monitor.domain.order_contract import OrderIntent,OrderSide,OrderType,OrderState
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRecord
        at=datetime(2026,1,5,9,0,tzinfo=timezone(timedelta(hours=9)))
        intent=OrderIntent('legacy-shape-intent','legacy-shape-run','legacy-shape-decision',
            'ad88c094-130e-4c41-bf9d-b533a58b426a','mock','005930','KRX',OrderSide.BUY,1,OrderType.LIMIT,
            70000,at,at+timedelta(seconds=5),'legacy-shape/v1')
        record=ExecutionRecord(intent,OrderState.QUEUED,'',0,(),None,at)
        raw=asdict(record)
        self.assertNotIn('member_spec_id',raw)
        wire=json.dumps(raw,default=lambda value:value.isoformat(),ensure_ascii=False,sort_keys=True,separators=(',',':'))
        # Independently frozen from the actual14252b1 dataclass before the member extension.
        self.assertEqual('ed817754f1aa42c1fdd4c8d21c047f626840e56ddb05e65f41ccc09d8335468d',
            hashlib.sha256(wire.encode()).hexdigest())
