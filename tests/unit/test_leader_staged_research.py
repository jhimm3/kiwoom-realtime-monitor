"""Frozen staged research/final lineage, independent of synthetic eligible reports."""
from dataclasses import replace
from datetime import date, datetime, timedelta
import json
import unittest
from unittest.mock import Mock, patch


def staged_final(case, *, eligibility_min_trades=1, config=None):
    from tests.unit.test_research_final_preparation import FinalPreparationTests
    from tests.unit.test_research_bundle_execution import child, rows_for
    from tests.unit.test_leader_position_steps import staged_config
    from kiwoom_monitor import research_process as rp
    from kiwoom_monitor.infrastructure.research_data_source import load_research_input
    from scripts import prepare_mock_automation_candidate as cli
    from tests.unit.test_mock_automation_specification import ACCOUNT_REF

    fixture=FinalPreparationTests()
    fixture.setUp()
    case.addCleanup(fixture.doCleanups)
    day=date(2026,9,14)
    rows=rows_for(day,40)
    # Explicit synthetic completed bars; each pending signal fills only at
    # the next real bar open. No inferred path inside high/low is consumed.
    prices=dict(enumerate((990,1001,1040,1049,1051,1060,1101,1120,940,930),22))
    for row in rows:
        if row['kind']=='minute_bar':
            index=datetime.fromisoformat(row['payload']['bar_start']).minute
            price=prices.get(index,990 if index<22 else 940)
            row['payload'].update(open=price,high=price,low=price,close=price,trade_value_million_won=100)
    parent=fixture.root/'staged-input'
    relative=child(parent,day,rows)
    dataset=parent/relative
    fixture.source=load_research_input(dataset,session_profile=fixture.request.session_profile)
    config=config or staged_config(reduce_quantity=2)
    config=replace(config,contexts=(replace(config.contexts[0],
        available_at='2026-09-14T09:00:00+09:00',valid_until='2026-09-14T10:00:00+09:00'),))
    fixture.evaluation=replace(fixture.evaluation,folds=(replace(fixture.evaluation.folds[0],
        end='2026-09-14T09:36:00+09:00'),))
    execution=replace(fixture.request.execution,cost_model=replace(fixture.request.execution.cost_model,
        valid_from='2026-01-01T00:00:00+09:00',valid_to='2027-01-01T00:00:00+09:00'))
    request=replace(fixture.request,family='leader_breakout/v1',strategy=config,dataset=dataset,
        evaluation=fixture.evaluation,execution=execution)
    fixture.request=request
    document=json.loads((fixture.root/'request.json').read_text(encoding='utf-8'))
    document.pop('development_partition')
    document.update(family=request.family,strategy=config.to_dict(),dataset=str(dataset),
        database=str(request.database),runs_dir=str(request.runs_dir),evaluation=request.evaluation.to_dict(),
        execution=execution.to_dict())
    path=fixture.root/'staged-final-request.json'
    path.write_text(json.dumps(document),encoding='utf-8')
    policy_path=fixture.root/'staged-eligibility.json'
    frozen_at=datetime.fromisoformat('2026-09-14T08:50:00+09:00')
    policy=cli.freeze_policy(path,policy_path,strategy_ref='staged-producer',
        minimum_closed_trades=eligibility_min_trades,minimum_active_days=1,minimum_net_realized_pnl_won=0,
        maximum_drawdown_ppm=1_000_000,now_provider=lambda:frozen_at)
    case.assertFalse(policy.tbd_fields)
    original=(dataset/'observations.jsonl').read_bytes()
    batch=fixture.make_batch((request,))
    prepared=fixture.prepare(batch=batch,candidates=(request,))
    fixture.staged_input_manifest=prepared.dataset.manifest
    active=datetime.fromisoformat(request.evaluation.folds[0].start)
    lower=active-timedelta(seconds=request.evaluation.warmup_seconds)
    case.assertTrue(any(row['kind']=='minute_bar' and
        datetime.fromisoformat(row['payload']['bar_start'])<=lower<
        datetime.fromisoformat(row['payload']['bar_end'])<=active and
        datetime.fromisoformat(row['available_at'])<=active for row in prepared.dataset.observations))
    from kiwoom_monitor.application.leader_breakout import evaluate_leader_breakout_bar_step
    from kiwoom_monitor.application.research_families import family_for_config
    family=family_for_config(config)
    legacy=Mock(side_effect=AssertionError('staged final must retain actual incremental observation state'))
    with patch('kiwoom_monitor.research_run.family_for_config',return_value=replace(family,evaluate_bar=legacy)), \
            patch('kiwoom_monitor.research_run.evaluate_leader_breakout_bar_step',
                  wraps=evaluate_leader_breakout_bar_step) as step:
        result=rp.execute_final_holdout_evaluation(prepared,owner_token='staged-final-fixture')
    legacy.assert_not_called()
    case.assertTrue(any(call.kwargs['state'].position_quantity==3 for call in step.call_args_list))
    row,=result['candidates']
    case.assertEqual('COMPLETED',row['state'],row)
    case.assertEqual(original,(dataset/'observations.jsonl').read_bytes())
    events=fixture.repo().load_execution_events(row['run_id'])
    publication=cli.prepare_publication(path,policy_path,fixture.root/'staged-publication.json',
        account_ref=ACCOUNT_REF,final_batch_id=prepared.batch.batch_id,
        final_run_id=row['run_id'])
    return fixture,request,row,publication,events


class LeaderStagedResearchChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.application=QApplication.instance() or QApplication([])

    def test_frozen_final_producer_preserves_declared_steps_actual_fills_and_eligibility(self):
        fixture,request,row,publication,events=staged_final(self)
        fills=[event for event in events if event['event_type']=='FILL']
        self.assertEqual([('BUY',2),('BUY',1),('SELL',2),('SELL',1)],
                         [(event['side'],event['quantity']) for event in fills])
        self.assertEqual([1041,1061,1119,929],[event['price'] for event in fills])
        package=publication['package']
        self.assertEqual('ELIGIBLE',publication['eligibility_receipt']['status'],package['evaluation_evidence'])
        self.assertEqual(json.loads(json.dumps(request.strategy.execution_contract.to_dict())),
            package['candidate_spec']['parameters']['execution_contract'])
        self.assertEqual([],package['candidate_spec']['parameters']['contexts'])
        self.assertEqual(row['run_id'],package['source_final']['run_id'])
        self.assertEqual('ELIGIBLE',package['evaluation_evidence']['report_status'])
        self.assertEqual(0,fills[-1]['position_quantity_after'])
        quality=fixture.repo().load_research_report(row['run_id'])['data_quality']
        self.assertEqual(('PASS','VERIFIED',fixture.staged_input_manifest['revision_count'],1),
            (quality['status'],quality['reproducibility'],quality['revision_count'],quality['candidate_universe_revision_count']))
        self._verify_shadow_ready(fixture,request,publication)

    def _verify_shadow_ready(self,fixture,request,publication):
        from fastapi.testclient import TestClient
        from tests.unit import test_mock_automation_specification as support
        from kiwoom_monitor.application.market_session_schedule import KST
        from kiwoom_monitor.application.mock_automation_specification import matching_shadow_events
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
        from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationPublicationDialog
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository

        final=publication['package']['source_final']
        shadow_at=datetime.fromisoformat(final['finished_at']).astimezone(KST)+timedelta(seconds=1)
        config=replace(request.strategy,contexts=(replace(request.strategy.contexts[0],
            available_at=(shadow_at-timedelta(hours=1)).isoformat(),
            valid_until=(shadow_at+timedelta(days=1)).isoformat()),))
        with support.mock_publication_contract_app(create_app,fixture.root/'staged-ready.sqlite3') as (app,store,inputs):
            monitor=CandidateMonitor(store,config.policy,poll_seconds=1,universe_max_age_seconds=60,
                                     now_provider=lambda:shadow_at)
            monitor._leader_accepting=True
            for offset,price in ((-0.001,999),(0,1001)):
                at=shadow_at+timedelta(seconds=offset)
                monitor.observe_trade(dict(code='005930',market='KRX',trade_time=at.strftime('%H%M%S'),
                    current_price=price,cumulative_trade_value=100),at,source='synthetic-live-0B',approved=True)
            monitor.run_once()
            shadow,=[event for event in store.load_shadow_candidates(0,100)['events']
                     if event.get('strategy_id')=='leader_breakout']
            self.assertEqual((shadow,),matching_shadow_events(publication,(shadow,)))
            self.assertFalse(shadow['order_authorized'])
            dialog=MockAutomationPublicationDialog(publication,inputs.binding,support.PROFILE_ID,(shadow,))
            self.addCleanup(dialog.deleteLater)
            self.assertIn('추가 매수 1주',dialog._position_terms.text())
            self.assertIn('일부 청산 2주',dialog._position_terms.text())
            policy=replace(support.MockUnfilledReplacementDispatchTests.unfilled_execution_policy,
                           version='mock-unfilled-execution-policy/v2')
            path=fixture.root/'explicit-current-order-policy.json'
            path.write_text(json.dumps(policy.to_dict()),encoding='utf-8')
            dialog._plan_policy_path.setText(str(path))
            with patch('kiwoom_monitor.presentation.mock_automation_dialog.kst_now',
                       return_value=shadow_at+timedelta(minutes=1)):
                documents=dialog.publication_values()
            headers={'Authorization':'Bearer private-token'}
            with TestClient(app) as client:
                candidate={**publication,'account_ref':support.ACCOUNT_REF,
                    'credential_profile_id':support.PROFILE_ID,'expected_binding_revision':inputs.binding['binding_revision']}
                reply=client.post('/api/v1/research/mock-automation-candidates',headers=headers,json=candidate)
                self.assertEqual(200,reply.status_code,reply.text)
                self.assertEqual(('saved','ELIGIBLE',False),
                    (reply.json()['status'],reply.json()['eligibility_status'],reply.json()['orders_started']))
                reply=client.get('/api/v1/research/mock-automation-candidates/'+support.ACCOUNT_REF,
                                 headers=headers,params={'credential_profile_id':support.PROFILE_ID})
                self.assertEqual(200,reply.status_code,reply.text)
                saved,=reply.json()['candidates']
                self.assertEqual(publication,saved)
                reply=client.post('/api/v1/research/mock-automation-specs',headers=headers,json=documents)
                self.assertEqual(200,reply.status_code,reply.text)
                self.assertEqual(('saved','READY',False),
                    (reply.json()['status'],reply.json()['readiness'],reply.json()['orders_started']))
                reply=client.get('/api/v1/research/mock-automation-specs/'+support.ACCOUNT_REF,headers=headers)
                self.assertEqual(200,reply.status_code,reply.text)
                saved,=reply.json()['specs']
                self.assertEqual(('READY',documents['operating_spec']),(saved['readiness'],saved['spec']))
                self.assertEqual(final['run_id'],saved['spec']['final_run_id'])
                self.assertEqual(final['result_hash'],saved['spec']['final_result_hash'])
                self.assertEqual(publication['package']['package_hash'],saved['spec']['candidate_package_hash'])
            self.assertIsNone(store.load_mock_automation_control(support.ACCOUNT_REF))
            execution=ExecutionRepository(store)
            self.assertEqual(((),True),execution.account_active_intents(support.ACCOUNT_REF))
            self.assertEqual((),execution.account_events('mock',support.ACCOUNT_REF).events)

    def test_real_completed_final_keeps_prefrozen_unmet_criteria_blocked(self):
        _,_,_,publication,_=staged_final(self,eligibility_min_trades=3)
        self.assertEqual(('ELIGIBLE','BLOCKED',['MINIMUM_CLOSED_TRADES_NOT_MET']),
            (publication['package']['evaluation_evidence']['report_status'],
             publication['eligibility_receipt']['status'],publication['eligibility_receipt']['reasons']))


if __name__=='__main__':
    unittest.main()
