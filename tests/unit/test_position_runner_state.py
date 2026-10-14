"""Native fill-owned facts use one RAM borrow, without changing execution."""
from copy import deepcopy
from dataclasses import asdict,replace
from datetime import timedelta
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

from tests.unit import test_mock_automation_runner as native
from tests.unit.test_mock_automation_risk import cost
from kiwoom_monitor.application.mock_automation_risk import build_mock_automation_risk_snapshot
from kiwoom_monitor.application.position_target import (
    PositionAccountObservation,PositionRunnerAccountObservation,validate_position_runner_state)
from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
from kiwoom_monitor.domain.order_contract import AccountSnapshot
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import MockAccountRecovery


class NativeRunnerStateTests(unittest.TestCase):
    def setUp(self):
        self.base=native.MockAutomationRunnerTests();self.base.setUp();self.addCleanup(self.base.doCleanups)
        self.now=native.NOW+timedelta(seconds=10)
        self.runtime=SimpleNamespace(server_now=lambda:self.now,observation_revision=0,observation_in_progress=False)
        self.base.bundle.runtime=self.runtime
        self.base.bundle.monitor=SimpleNamespace(position_observation_matches=lambda observation:observation==self.latest_observation)
        self.latest_observation=None
        self.runner=MockAutomationRunner(self.base.store,self.base.bundle,self.base.spec)
        self.runner._bootstrap_pending=False

    def fill(self,sequence,side,quantity,price):
        self.base.events.values.append(self.base.event(sequence,f'fill-{sequence}',side,quantity,price))
        self.runner._apply_new_fills()

    def observation(self):
        state=self.runner._state
        positions={state.symbol:state.position_quantity} if state.status=='open' else {}
        account=AccountSnapshot(native.ACCOUNT_REF,90000,0,positions,self.now)
        daily=tuple(replace(cost(self.now.date(),'매수' if event.side=='BUY' else '매도',event.quantity*event.price,0),
            origin_scope=self.base.spec.account_scope) for event in self.base.events.values)
        risk=build_mock_automation_risk_snapshot(MockAccountRecovery(account,()),tuple(self.base.events.values),daily,(),
            execution_run_id=self.base.run_id,binding_revision=1,reconciliation_revision=1,observed_at=self.now,
            broker_query_started_at=self.now,broker_query_completed_at=self.now,cost_query_complete=True)
        self.latest_observation=PositionAccountObservation(account,(),'native-current-account',self.now,True,True,(),(),(),risk)
        return self.latest_observation

    def test_real_fill_basis_partial_sell_preserves_opened_time_and_zero_getter_io(self):
        self.fill(1,'BUY',2,1000);self.fill(2,'BUY',2,1200)
        opened=self.runner._state.opened_at;self.fill(3,'SELL',1,1300)
        observation=self.observation();before=(self.runner._state,self.runner._fill_cursor,self.base.store.load_documents(native.CHECKPOINT_COLLECTION))
        with patch.object(self.base.events,'account_events',side_effect=AssertionError('getter fill read')),patch('sqlite3.connect',side_effect=AssertionError('getter DB')):
            first=self.runner.position_study_state(observation);second=self.runner.position_study_state(observation)
        self.assertIsNotNone(first);self.assertEqual(first,second)
        self.assertEqual(1100,first['strategy_state']['entry_price']);self.assertEqual(3,first['strategy_state']['position_quantity'])
        self.assertEqual(opened,first['strategy_state']['opened_at']);self.assertEqual(self.now.isoformat(),first['state_available_at'])
        self.assertEqual(before,(self.runner._state,self.runner._fill_cursor,self.base.store.load_documents(native.CHECKPOINT_COLLECTION)))
        first['strategy_state']['entry_price']=1
        self.assertEqual(1100,self.runner.position_study_state(observation)['strategy_state']['entry_price'])

    def test_misaligned_hwm_price_scope_warmup_close_and_busy_are_unavailable(self):
        self.fill(1,'BUY',2,1000);observation=self.observation()
        for state in (replace(self.runner._state,entry_price=999),replace(self.runner._state,position_quantity=3)):
            before=self.runner._state;self.runner._replace_strategy_state(state)
            self.assertIsNone(self.runner.position_study_state(observation));self.runner._replace_strategy_state(before)
        self.runner._fill_cursor=0;self.assertIsNone(self.runner.position_study_state(observation));self.runner._fill_cursor=1
        self.runner._bootstrap_pending=True;self.assertIsNone(self.runner.position_study_state(observation));self.runner._bootstrap_pending=False
        self.runtime.observation_in_progress=True;self.assertIsNone(self.runner.position_study_state(observation));self.runtime.observation_in_progress=False
        for lock in (self.runner._position_state_lock,self.runner._protection_lock):
            lock.acquire()
            try:self.assertIsNone(self.runner.position_study_state(observation))
            finally:lock.release()
        self.runner._closing=True;self.assertIsNone(self.runner.position_study_state(observation))

    def test_fill_read_inflight_is_not_a_ready_snapshot_and_failure_stays_unavailable(self):
        self.fill(1,'BUY',2,1000);observation=self.observation()
        entered=threading.Event();release=threading.Event();original=self.base.events.account_events
        def held(*args,**kwargs):entered.set();release.wait(5);return original(*args,**kwargs)
        with patch.object(self.base.events,'account_events',side_effect=held):
            thread=threading.Thread(target=self.runner._apply_new_fills);thread.start()
            try:
                self.assertTrue(entered.wait(2));self.assertIsNone(self.runner.position_study_state(observation))
            finally:release.set();thread.join(5)
        self.assertIsNotNone(self.runner.position_study_state(observation))
        with patch.object(self.base.events,'account_events',side_effect=OSError('controlled fill failure')):
            with self.assertRaises(OSError):self.runner._apply_new_fills()
        self.assertIsNone(self.runner.position_study_state(observation))

    def test_protection_notice_first_time_is_preserved_and_repeated_getter_does_not_refresh(self):
        self.fill(1,'BUY',2,1000);observation=self.observation()
        notice=self.runner.request_protection_review(reason='controlled risk',input_ref='first-price',requested_at=self.now)
        self.now+=timedelta(seconds=1)
        with patch.object(self.base.events,'account_events',side_effect=AssertionError('source read')):
            first=self.runner.position_study_state(observation);second=self.runner.position_study_state(observation)
        self.assertEqual(notice,first['protection_review']);self.assertEqual(first,second)
        self.assertEqual(observation.observed_at.isoformat(),first['state_available_at'])
        self.runner._apply_new_fills()
        self.assertEqual(observation.observed_at.isoformat(),self.runner.position_study_state(observation)['state_available_at'])

    def test_owner_registry_enrichment_is_detached_and_disappears_on_owner_change(self):
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
        self.fill(1,'BUY',2,1000);observation=self.observation()
        owner=MockAutomationSupervisor(self.base.store,SimpleNamespace());owner._runners[native.ACCOUNT_REF]=self.runner
        first=owner.position_shadow_observation(observation)
        self.assertIsInstance(first,PositionRunnerAccountObservation)
        self.assertEqual(asdict(observation),{k:v for k,v in asdict(first).items() if k!='runner_state'})
        owner._runners.clear();base=owner.position_shadow_observation(first)
        self.assertIs(type(base),PositionAccountObservation);self.assertNotIn('runner_state',asdict(base))
        owner._runners[native.ACCOUNT_REF]=self.runner;owner._closing=True
        self.assertIs(type(owner.position_shadow_observation(observation)),PositionAccountObservation)

    def test_account_epoch_or_registry_change_during_copy_discards_enrichment(self):
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
        self.fill(1,'BUY',2,1000);observation=self.observation()
        owner=MockAutomationSupervisor(self.base.store,SimpleNamespace());owner._runners[native.ACCOUNT_REF]=self.runner
        with patch.object(self.base.bundle.monitor,'position_observation_matches',side_effect=[True,False]):
            self.assertIs(type(owner.position_shadow_observation(observation)),PositionAccountObservation)
        original=self.runner.position_study_state
        def replaced(value):
            result=original(value);owner._runners.clear();return result
        with patch.object(self.runner,'position_study_state',side_effect=replaced):
            self.assertIs(type(owner.position_shadow_observation(observation)),PositionAccountObservation)

    def test_monitor_epoch_check_uses_existing_ram_without_an_extra_borrow(self):
        from kiwoom_monitor.central_server.mock_account_monitor import MockAccountMonitor
        self.fill(1,'BUY',2,1000);observation=self.observation()
        monitor=MockAccountMonitor.__new__(MockAccountMonitor)
        monitor._accepting=True;monitor._runtime=self.runtime
        monitor._position_observation=observation;monitor._position_runtime_revision=0
        self.assertTrue(monitor.position_observation_matches(observation))
        self.runtime.observation_revision=1;self.assertFalse(monitor.position_observation_matches(observation))
        self.runtime.observation_revision=0;monitor._position_observation=None
        self.assertFalse(monitor.position_observation_matches(observation))

    def test_resigned_cut_state_or_notice_tamper_is_not_native_equivalence(self):
        import hashlib,json
        self.fill(1,'BUY',2,1000);observation=self.observation();state=self.runner.position_study_state(observation)
        for mutate in (lambda p:p.update(account_input_ref='other'),lambda p:p.update(fill_cursor=2),
                       lambda p:p['strategy_state'].update(entry_price=999),lambda p:p.update(session_clock=3)):
            bad=deepcopy(state);mutate(bad)
            bad['state_ref']='native-position-runner-state:'+hashlib.sha256(json.dumps(
                {k:v for k,v in bad.items() if k!='state_ref'},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            with self.assertRaises(ValueError):validate_position_runner_state(bad,observation)

    def test_copy_race_on_state_or_protection_is_discarded(self):
        from kiwoom_monitor.application import position_target
        self.fill(1,'BUY',2,1000);observation=self.observation()
        validate=position_target.validate_position_runner_state
        def changed(*args):
            result=validate(*args);self.runner._replace_strategy_state(self.runner._state);return result
        with patch.object(position_target,'validate_position_runner_state',side_effect=changed):
            self.assertIsNone(self.runner.position_study_state(observation))
        def protected(*args):
            result=validate(*args)
            self.runner.request_protection_review(reason='race',input_ref='first',requested_at=self.now)
            return result
        with patch.object(position_target,'validate_position_runner_state',side_effect=protected):
            self.assertIsNone(self.runner.position_study_state(observation))
        def clock_changed(*args):
            result=validate(*args);self.runner._session_clock={'phase':'STOPPING'};return result
        with patch.object(position_target,'validate_position_runner_state',side_effect=clock_changed):
            self.assertIsNone(self.runner.position_study_state(observation))


class RunnerStateRamTests(unittest.IsolatedAsyncioTestCase):
    from tests.unit.test_candidate_monitor import AllocationModelPublicationTests as _fixture_owner
    fixture=_fixture_owner.fixture

    def prepare(self,count):
        from tests.unit.test_position_study_publication import StudyRamPublicationTests
        from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
        runtime,collector,clock,source,selection,legacy,packet=StudyRamPublicationTests.prepare(self,count)
        current=source.return_value;risk=current.risk_snapshot
        state=NativeRunnerStateTests();state.setUp();self.addCleanup(state.doCleanups)
        state.now=current.observed_at
        state.base.spec=replace(state.base.spec,account_scope=replace(state.base.spec.account_scope,account_ref=current.account.account_ref),
            binding_revision=risk.binding_revision,data_path=risk.data_path)
        state.base.bundle.account_ref=current.account.account_ref;state.base.bundle.run_id=risk.execution_run_id
        state.base.bundle.automation_context=SimpleNamespace(execution_run_id=risk.execution_run_id,spec_id=state.base.spec.spec_id)
        state.runner=MockAutomationRunner(state.base.store,state.base.bundle,state.base.spec);state.runner._bootstrap_pending=False
        event=replace(state.base.event(1,'native-current-fill','BUY',10,1000),run_id=risk.execution_run_id,
            account_ref=current.account.account_ref,occurred_at=current.observed_at,received_at=current.observed_at)
        state.base.events.values=[event];state.runner._apply_new_fills();state.latest_observation=current
        owner=MockAutomationSupervisor(state.base.store,SimpleNamespace());owner._runners[current.account.account_ref]=state.runner
        source.side_effect=lambda account_ref:owner.position_shadow_observation(current)
        return runtime,collector,clock,source,packet,state,owner

    async def test_two_eight_share_fill_owned_state_and_preserve_missing_research_support(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import decode_dynamic_allocation_model_publication
        for count in (2,8):
            runtime,collector,_,source,packet,state,_=self.prepare(count)
            runtime.offer_allocation_models(packet,decode_dynamic_allocation_model_publication(packet,runtime._allocation,1))
            with (patch.object(state.base.events,'account_events',side_effect=AssertionError('state getter ledger read')),
                    patch('sqlite3.connect',side_effect=AssertionError('state hot path DB'))):
                await runtime._dispatch(runtime._snapshot())
            self.assertEqual(1,source.call_count)
            receipt=runtime.latest_results['position_allocation'];goal=receipt['r4_action_comparison']['goals'][0]
            self.assertEqual(1000,goal['native_owner_facts']['entry_price_won'])
            self.assertEqual(10,goal['native_owner_facts']['owned_quantity'])
            self.assertEqual(0,goal['native_owner_facts']['holding_age_seconds'])
            self.assertNotIn('current_position_protection_state_unavailable',goal['reasons'])
            self.assertIn('native_protection_model_support_unverified',goal['reasons'])
            self.assertEqual('EV_UNAVAILABLE',goal['status']);self.assertFalse(goal['order_authorized'])
            before=deepcopy(receipt)
            for _ in range(100):runtime.latest_results
            self.assertEqual(before,runtime.latest_results['position_allocation']);self.assertEqual(1,source.call_count)

    async def test_native_pending_protection_and_owner_loss_never_become_supported_goals(self):
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import decode_dynamic_allocation_model_publication
        runtime,_,_,source,packet,state,owner=self.prepare(2)
        notice=state.runner.request_protection_review(reason='controlled native risk',input_ref='first-trigger',requested_at=state.now)
        runtime.offer_allocation_models(packet,decode_dynamic_allocation_model_publication(packet,runtime._allocation,1))
        await runtime._dispatch(runtime._snapshot())
        goal=runtime.latest_results['position_allocation']['r4_action_comparison']['goals'][0]
        self.assertTrue(goal['native_owner_facts']['protection_pending'])
        self.assertIn('native_protection_preempts_position_study',goal['reasons'])
        self.assertEqual(notice,state.runner._protection_snapshot())
        owner._runners.clear();await runtime._refresh_position_inputs(runtime._snapshot())
        goal=runtime.latest_results['position_allocation']['r4_action_comparison']['goals'][0]
        self.assertNotIn('native_owner_facts',goal);self.assertIn('current_position_protection_state_unavailable',goal['reasons'])
        self.assertFalse(goal['order_authorized'])


if __name__=='__main__':unittest.main()
