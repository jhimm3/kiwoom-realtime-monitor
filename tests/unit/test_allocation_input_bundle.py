"""One current input cut, original source refs, and separate model applicability."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch
from kiwoom_monitor.central_server import market_theme_shadow_inputs as inputs
from tests.unit import test_entry_thesis as native


class AllocationInputBundleTests(unittest.TestCase):
    def setUp(self):
        self.at=datetime(2026,10,14,0,0,tzinfo=timezone.utc)
        self.selection={'symbols':('005930',),'policy':SimpleNamespace(context_weights=(1,1,1))}
        self.receipt={'account_ref':'account-cut','decision_time':self.at.isoformat(),'available_at':self.at.isoformat(),
            'valid_until':(self.at+timedelta(seconds=20)).isoformat(),'account_input_ref':'account-original',
            'risk_snapshot_ref':'risk-original','risk_disposition':'ALLOW','input_refs':['quote-original','window-original'],
            'model_publication_ref':'model-original','quality':{'status':'CALCULATED','reasons':[]},'facts':[{
                'symbol':'005930','reference_price_won':1000,'market_cap_won':100000000,'price_available_at':self.at.isoformat(),
                'recent_turnover_won':10000,'observed_close_return_rms_bps':10,'window_input_ref':'window-original',
                'window_start':(self.at-timedelta(seconds=20)).isoformat(),'window_end':self.at.isoformat(),
                'market_strength_bps':5000,'theme_strength_bps':5000,'stock_strength_bps':5000,'context_basis':'existing-native',
                'theme_ids':['theme-original'],'net_ev_bps':10,'ev_uncertainty_bps':1,'conservative_exit_capacity_won':20000,'stress_loss_bps':100}]}
        ev=SimpleNamespace(comparison_ref='comparison-original',population_ref='population-original',
            input_refs=('native-position-outcome_one','native-position-outcome_two'),available_at=self.at-timedelta(seconds=1),
            valid_until=self.at+timedelta(seconds=100))
        ex=SimpleNamespace(source_ref='exit-original',available_at=ev.available_at,valid_until=ev.valid_until)
        self.models={'selection_ref':'selection-original','publication_ref':'model-original','available_at':ev.available_at,
            'valid_until':ev.valid_until,'rows':{'005930':{'ev':ev,'exit':ex,'position_study':{'publication_ref':'study-original',
                'allocation_lineage':{'ev_source_publication_ref':'ev-source-original'},'historical_contexts':[]}}}}
        self.scope=patch.object(inputs,'_allocation_selection_ref',return_value='selection-original');self.scope.start();self.addCleanup(self.scope.stop)

    def test_original_refs_and_cut_are_detached_and_not_model_or_order_approval(self):
        before=deepcopy(self.receipt)
        with patch('sqlite3.connect',side_effect=AssertionError('bundle DB')):
            bundle=inputs.freeze_dynamic_allocation_input_bundle(self.receipt,self.models,self.selection)
        self.assertEqual('INPUTS_BOUND',bundle['status']);self.assertTrue(all(bundle['checks'].values()))
        self.assertFalse(bundle['model_application_verified']);self.assertFalse(bundle['order_authorized'])
        self.assertFalse(bundle['models'][0]['trained_predictor_used']);self.assertEqual('exit-original',bundle['models'][0]['exit_source_ref'])
        self.assertEqual(before,self.receipt)
        self.receipt['facts'][0]['reference_price_won']=999
        changed=inputs.freeze_dynamic_allocation_input_bundle(self.receipt,self.models,self.selection)
        self.assertNotEqual(bundle['current_cut_ref'],changed['current_cut_ref']);self.assertNotEqual(bundle['bundle_ref'],changed['bundle_ref'])
        self.assertEqual('model-original',bundle['model_publication_ref'])

    def test_context_bundle_requires_used_scores_and_keeps_theme_concentration_data(self):
        self.selection['policy']=SimpleNamespace(context_weights=(0,0,1))
        self.receipt['facts'][0].update(market_strength_bps=None,theme_strength_bps=None)
        bundle=inputs.freeze_dynamic_allocation_input_bundle(self.receipt,self.models,self.selection)
        self.assertEqual('INPUTS_BOUND',bundle['status'])
        self.assertIsNone(self.receipt['facts'][0]['theme_strength_bps'])
        for change in ({'stock_strength_bps':None},{'theme_ids':[]}):
            receipt=deepcopy(self.receipt);receipt['facts'][0].update(change)
            denied=inputs.freeze_dynamic_allocation_input_bundle(receipt,self.models,self.selection)
            self.assertEqual('INPUTS_UNAVAILABLE',denied['status'])
            self.assertFalse(denied['checks']['market_theme_stock'])
            self.assertFalse(denied['order_authorized'])

    def test_missing_fields_unknown_risk_or_original_ev_source_do_not_look_bound(self):
        for mutate in (lambda r:r['facts'][0].update(market_cap_won=None),lambda r:r['facts'][0].update(window_input_ref=None),
                lambda r:r['facts'][0].update(theme_strength_bps=None),lambda r:r['facts'][0].update(net_ev_bps=None),
                lambda r:r.update(risk_snapshot_ref=None),lambda r:r.update(quality={'status':'BLOCKED','reasons':['unknown_reconciliation']})):
            receipt=deepcopy(self.receipt);mutate(receipt)
            bundle=inputs.freeze_dynamic_allocation_input_bundle(receipt,self.models,self.selection)
            self.assertEqual('INPUTS_UNAVAILABLE',bundle['status']);self.assertFalse(bundle['model_application_verified'])
        self.models['rows']['005930']['position_study'].pop('allocation_lineage')
        bundle=inputs.freeze_dynamic_allocation_input_bundle(self.receipt,self.models,self.selection)
        self.assertIn('reported_original_model_sources',bundle['reasons'])

    def test_model_scope_or_expiry_never_renews_the_original_receipt_lease(self):
        for change in ({'selection_ref':'other'},{'publication_ref':'other'},{'valid_until':self.at}):
            models={**self.models,**change}
            bundle=inputs.freeze_dynamic_allocation_input_bundle(self.receipt,models,self.selection)
            self.assertEqual('INPUTS_UNAVAILABLE',bundle['status']);self.assertFalse(bundle['checks']['original_cut_and_scope'])
            self.assertEqual(self.receipt['valid_until'],bundle['valid_until'])
        late={**self.receipt,'decision_time':self.receipt['valid_until']}
        self.assertEqual('INPUTS_UNAVAILABLE',inputs.freeze_dynamic_allocation_input_bundle(late,self.models,self.selection)['status'])

    def test_budget_failure_is_small_and_does_not_change_existing_amount_or_quality(self):
        self.models['rows']['005930']['position_study']['historical_contexts']=[{'context_ref':'x'*(256*1024)}]
        before=deepcopy(self.receipt)
        bundle=inputs.freeze_dynamic_allocation_input_bundle(self.receipt,self.models,self.selection)
        self.assertEqual(['input_bundle_budget_exceeded'],bundle['reasons']);self.assertNotIn('bundle_ref',bundle)
        self.assertEqual(before,self.receipt);self.assertLess(len(json.dumps(bundle)),1024)


class AllocationInputBundleRamTests(unittest.IsolatedAsyncioTestCase):
    from tests.unit.test_candidate_monitor import AllocationModelPublicationTests as _fixture_owner
    fixture=_fixture_owner.fixture

    def prepare(self,count):
        from tests.unit.test_position_historical_context import HistoricalContextRamTests
        from kiwoom_monitor.application.research_execution import SimulationCostModel
        runtime,collector,clock,source,packet=HistoricalContextRamTests.prepare(self,count)
        row=packet['rows'][0];case=native.NativeDynamicAllocationEvTests();case.setUp();self.addCleanup(case.doCleanups)
        cost=SimulationCostModel(**row['exit_receipt']['replay_inputs']['execution']['cost_model'])
        case.prepare(cost_override=cost,reference_price=row['ev_receipt']['evidence']['reference_price_won'],terminal_price=1060,horizon_minutes=3)
        er=case.read_ev(case.reader.load_position_action_evidence,case.selections,**case.arguments,include_source_records=True)
        from kiwoom_monitor.application.position_target import _json_value
        selection=json.loads(json.dumps({'schema':'dynamic-position-allocation-ram/v1',
            'policy':asdict(runtime._allocation['policy']),'symbols':['005930'],
            **{k:runtime._allocation[k] for k in ('expected_execution_run_id','expected_binding_revision','data_path','maximum_daily_loss_won')},
            'cost_model':cost.to_dict()},default=_json_value))
        packet=inputs.prepare_dynamic_allocation_model_publication(selection,[{'symbol':'005930','ev_receipt':er,'exit_receipt':row['exit_receipt']}],
            expected_selection_revision=1,position_studies={'005930':row['position_study']})
        return runtime,collector,clock,source,packet

    async def test_two_eight_share_one_bound_input_packet_and_r4_ref_with_inert_status_and_expiry(self):
        for count in (2,8):
            runtime,collector,clock,source,packet=self.prepare(count)
            runtime.offer_allocation_models(packet,inputs.decode_dynamic_allocation_model_publication(packet,runtime._allocation,1))
            with patch('sqlite3.connect',side_effect=AssertionError('bundle hot DB')),patch.object(
                    inputs,'freeze_dynamic_allocation_input_bundle',wraps=inputs.freeze_dynamic_allocation_input_bundle) as bundle_calls:
                await runtime._dispatch(runtime._snapshot())
                receipt=runtime.latest_results['position_allocation'];bundle=receipt['input_bundle']
                self.assertEqual('INPUTS_BOUND',bundle['status']);self.assertEqual(1,bundle_calls.call_count)
                goal=receipt['r4_action_comparison']['goals'][0]
                self.assertEqual(bundle['bundle_ref'],goal['input_bundle_ref']);self.assertEqual('EV_UNAVAILABLE',goal['status'])
                for _ in range(100):runtime.latest_results;runtime.status;runtime.allocation_publication_duplicate(packet)
                self.assertEqual(1,bundle_calls.call_count)
            self.assertEqual(1,source.call_count)
            original_until=bundle['valid_until'];clock[0]=datetime.fromisoformat(receipt['valid_until'])
            expired=runtime.latest_results['position_allocation']
            self.assertNotIn('input_bundle',expired);self.assertNotIn('goals',expired['r4_action_comparison'])
            self.assertEqual(original_until,bundle['valid_until'])

    async def test_existing_selected_record_writes_bundle_once_and_v1_has_no_new_fields(self):
        import tempfile
        from pathlib import Path
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import SHADOW_RESULT_COLLECTION
        runtime,_,_,_,packet=self.prepare(2)
        runtime.offer_allocation_models(packet,inputs.decode_dynamic_allocation_model_publication(packet,runtime._allocation,1))
        with tempfile.TemporaryDirectory() as directory:
            store=SQLiteQueryStore(Path(directory)/'bundle.sqlite3');store.initialize();self.addCleanup(store.close)
            runtime._record_store=store
            sql=[];connect=store._connect
            def traced():
                connection=connect();connection.set_trace_callback(sql.append);return connection
            with patch.object(store,'_connect',side_effect=traced),patch.object(store,'upsert_documents',wraps=store.upsert_documents) as writes:
                await runtime._dispatch(runtime._snapshot());await runtime._record_once()
                self.assertEqual(1,writes.call_count);self.assertEqual(1,sum(s=='COMMIT' for s in sql))
                document=writes.call_args.args[1][0]['document']
                self.assertIn('input_bundle',document['position_allocation'])
                self.assertEqual(SHADOW_RESULT_COLLECTION,writes.call_args.args[0])
                runtime._select_record();await runtime._record_once();self.assertEqual(1,writes.call_count)
            runtime._record_store=None
        baseline=self._fixture_owner.prepare(self,2)
        other,_,_,source,_,_,v1=baseline
        other.offer_allocation_models(v1,inputs.decode_dynamic_allocation_model_publication(v1,other._allocation,1))
        await other._dispatch(other._snapshot())
        self.assertNotIn('input_bundle',other.latest_results['position_allocation']);self.assertEqual(1,source.call_count)


class AllocationOffFlowTests(unittest.IsolatedAsyncioTestCase):
    _fixture_owner=AllocationInputBundleRamTests._fixture_owner
    fixture=AllocationInputBundleRamTests.fixture
    prepare=AllocationInputBundleRamTests.prepare

    async def test_current_selection_upper_lower_and_order_off_plan_share_exact_refs(self):
        from dataclasses import replace
        from kiwoom_monitor.application.position_target import (
            PositionTargetShadowRequest,PositionTargetSizingPolicy,evaluate_position_target_shadow)
        runtime,collector,clock,source,packet=self.prepare(8)
        runtime.offer_allocation_models(packet,inputs.decode_dynamic_allocation_model_publication(packet,runtime._allocation,1))
        with patch('sqlite3.connect',side_effect=AssertionError('current flow DB')):
            await runtime._dispatch(runtime._snapshot())
            receipt=runtime.latest_results['position_allocation'];upper=receipt['result'];lower=receipt['lower_targets'][0]
            observation=source.return_value;at=datetime.fromisoformat(receipt['decision_time'])
            row=upper['allocations'][0];fact=receipt['facts'][0]
            policy=dict(row['target_policy'])
            for key in ('available_at','valid_until'):policy[key]=datetime.fromisoformat(policy[key])
            request=PositionTargetShadowRequest(observation.account.account_ref,'mock','KRX',row['symbol'],lower['action'],None,
                fact['reference_price_won'],datetime.fromisoformat(fact['price_available_at']),at,
                lower['target_sizing']['source_policy_version'],upper['decision_id'],observation.input_ref,observation.input_ref,
                runtime._allocation['policy'].max_snapshot_age_seconds,True,True,runtime._allocation['cost_model'],
                PositionTargetSizingPolicy(**policy))
            plan=evaluate_position_target_shadow(request,observation.account,observation.open_orders,
                broker_open_orders=observation.broker_open_orders)
            self.assertEqual(lower,plan)
            self.assertEqual(runtime._allocation['symbols'],(row['symbol'],))
            self.assertEqual(row['new_buy_quantity'],plan['incremental_quantity'])
            self.assertEqual(row['target_notional_won'],plan['target_notional_won'])
            self.assertEqual(upper['decision_id'],plan['source_decision_ref'])
            self.assertEqual(1,source.call_count)
            self.assertEqual('EV_UNAVAILABLE',receipt['r4_action_comparison']['status'])
            self.assertFalse(receipt['input_bundle']['model_application_verified'])
            self.assertFalse(plan['order_authorized'])
            # A copied current quote cannot extend the original automatic goal.
            expired=replace(request,decision_time=request.target_policy.valid_until,
                reference_price_available_at=request.target_policy.valid_until)
            denied=evaluate_position_target_shadow(expired,replace(observation.account,as_of=expired.decision_time),(),broker_open_orders=())
            self.assertEqual('BLOCKED',denied['quality']['status'])
            self.assertIn('sizing_policy_expired',denied['quality']['reasons'])
        model_until=runtime._allocation_models['valid_until'];clock[0]=datetime.fromisoformat(receipt['valid_until'])
        projected=runtime.latest_results['position_allocation']
        self.assertIsNone(projected['result']);self.assertNotIn('lower_targets',projected)
        self.assertNotIn('input_bundle',projected);self.assertEqual(model_until,runtime._allocation_models['valid_until'])

    async def test_account_failure_discards_previous_plan_and_close_rejects_publication(self):
        runtime,_,_,source,packet=self.prepare(2)
        decoded=inputs.decode_dynamic_allocation_model_publication(packet,runtime._allocation,1)
        runtime.offer_allocation_models(packet,decoded);await runtime._dispatch(runtime._snapshot())
        self.assertIsNotNone(runtime.latest_results['position_allocation']['result'])
        source.side_effect=OSError('controlled account source loss')
        with patch('sqlite3.connect',side_effect=AssertionError('failure fallback DB')):
            await runtime._refresh_position_inputs(runtime._snapshot())
        receipt=runtime.latest_results['position_allocation']
        self.assertIsNone(receipt['result']);self.assertNotIn('lower_targets',receipt)
        self.assertFalse(receipt['order_authorized'])
        self.assertIn('native_allocation_input_error:OSError',runtime._allocation_receipt['quality']['reasons'])
        await runtime.close()
        with self.assertRaisesRegex(RuntimeError,'ALLOCATION_RUNTIME_NOT_READY'):runtime.offer_allocation_models(packet,decoded)

    async def test_v2_hold_and_exit_keep_targets_and_reject_add_study_scope_without_io(self):
        from dataclasses import replace
        from kiwoom_monitor.application.position_target import _json_value
        from kiwoom_monitor.central_server.market_theme_shadow_monitor import MarketThemeShadowRuntime
        parent,collector,_,source,packet=self.prepare(2)
        observation=source.return_value;row=packet['rows'][0];price=row['ev_receipt']['evidence']['reference_price_won']
        held=observation.account.positions[row['symbol']];cases=[]
        for action,budget in (('HOLD',held*price),('EXIT',0)):
            selection=json.loads(json.dumps({'schema':'dynamic-position-allocation-ram/v1',
                'policy':asdict(replace(parent._allocation['policy'],operating_limit_won=budget)),
                'symbols':list(parent._allocation['symbols']),
                **{k:parent._allocation[k] for k in ('expected_execution_run_id','expected_binding_revision','data_path','maximum_daily_loss_won')},
                'cost_model':parent._allocation['cost_model'].to_dict()},default=_json_value))
            published=inputs.prepare_dynamic_allocation_model_publication(selection,
                [{k:row[k] for k in ('symbol','ev_receipt','exit_receipt')}],expected_selection_revision=1,
                position_studies={row['symbol']:row['position_study']})
            runtime=MarketThemeShadowRuntime(parent._hub,collector,parent._bindings,
                position_account_source=source,position_allocation=inputs.compile_dynamic_allocation_ram_selection(selection))
            self.addAsyncCleanup(runtime.close)
            runtime.offer_allocation_models(published,inputs.decode_dynamic_allocation_model_publication(published,runtime._allocation,1))
            cases.append((action,budget,runtime))
        for action,budget,runtime in cases:
            source.reset_mock()
            with patch('sqlite3.connect',side_effect=AssertionError('v2 hold/exit hot DB')):
                await runtime._dispatch(runtime._snapshot())
            receipt=runtime.latest_results['position_allocation'];goal=receipt['r4_action_comparison']['goals'][0]
            self.assertEqual('CALCULATED',receipt['quality']['status'],receipt)
            self.assertEqual((action,budget),(receipt['lower_targets'][0]['action'],receipt['lower_targets'][0]['target_notional_won']))
            self.assertEqual('EV_UNAVAILABLE',goal['status']);self.assertIsNone(goal['result'])
            self.assertIn('current_study_sizing_or_action_mismatch',goal['reasons'])
            self.assertEqual(row['position_study']['comparison']['study_policy_ref'],goal['study_policy_ref'])
            self.assertFalse(receipt['input_bundle']['model_application_verified']);self.assertFalse(goal['order_authorized'])
            self.assertEqual(1,source.call_count)


if __name__=='__main__':unittest.main()
