"""Explicit same-account composition without creating an execution owner."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch


def account_selection_inputs(case,store,*,base=None):
    from tests.unit import test_mock_automation_specification as support
    from tests.unit.test_leader_position_steps import staged_config
    from kiwoom_monitor.application.mock_automation_candidate import evaluate_candidate_eligibility
    from kiwoom_monitor.application.mock_automation_specification import publish_ready_mock_automation_spec
    from kiwoom_monitor.application.research_families import get_research_family
    from kiwoom_monitor.application.market_session_schedule import KST
    from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor
    from kiwoom_monitor.domain.execution_activation import (
        MockAutomationAccountLimits,MockAutomationAccountSelection,LEADER_NATIVE_PLAN_MODE,
        StrategyLifecycleStage, strategy_stage_revision,
    )
    base=base or support.mock_specification_inputs(store,publish_candidate=False)
    repo=base.repository
    specs=[]
    for name,quantity in (('alpha',2),('beta',3)):
        config=staged_config()
        config=replace(config,contexts=(replace(config.contexts[0],
            available_at=(support.NOW-timedelta(hours=1)).isoformat(),
            valid_until=(support.NOW+timedelta(days=1)).isoformat()),),
            execution_contract=replace(config.execution_contract,quantity=quantity))
        candidate=deepcopy(base.package.candidate_spec)
        candidate.update(version='final_candidate/v2',family='leader_breakout/v1',
                         parameters={**config.to_dict(),'contexts':[]})
        package=replace(base.package,strategy_ref=name,candidate_spec=candidate,
            candidate_spec_hash=support._hash(candidate),source_final_batch_id='batch-'+name,
            source_final_run_id='run-'+name)
        policy=replace(base.policy,strategy_ref=name,candidate_spec_hash=package.candidate_spec_hash)
        receipt=evaluate_candidate_eligibility(package,policy,account_ref=support.ACCOUNT_REF)
        case.assertEqual('ELIGIBLE',receipt.status.value)
        repo.publish_mock_automation_candidate(package,policy,receipt,credential_profile_id=support.PROFILE_ID,
            expected_binding_revision=base.binding['binding_revision'])
        monitor=CandidateMonitor(store,config.policy,poll_seconds=1,universe_max_age_seconds=60,
                                 now_provider=lambda:support.NOW)
        monitor._leader_accepting=True
        for offset,price in ((-0.001,999),(0,1001)):
            at=support.NOW+timedelta(seconds=offset)
            monitor.observe_trade(dict(code='005930',market='KRX',trade_time=at.astimezone(KST).strftime('%H%M%S'),
                current_price=price,cumulative_trade_value=100),at,source='declared-'+name,approved=True)
        monitor.run_once()
        shadow=store.load_shadow_candidates(0,100)['events'][-1]
        family=get_research_family('leader_breakout/v1')
        profile=replace(base.profile,strategy_ref=name,family_version=family.family_id,
                        factor_versions=family.factor_ids,policy_version=policy.policy_id)
        stages=[]
        for previous,current,evidence,at in (
            (StrategyLifecycleStage.DRAFT,StrategyLifecycleStage.EVALUATED,package.package_hash,support.NOW-timedelta(hours=2)),
            (StrategyLifecycleStage.EVALUATED,StrategyLifecycleStage.VALIDATED,receipt.receipt_id,support.NOW-timedelta(hours=1)),
            (StrategyLifecycleStage.VALIDATED,StrategyLifecycleStage.SHADOW,shadow['event_id'],support.NOW)):
            stages.append(strategy_stage_revision(strategy_ref=name,previous_stage=previous,stage=current,
                evidence_refs=(evidence,),reason='declared isolated '+name,decided_at=at))
        spec=replace(base.spec,strategy_ref=name,candidate_package_hash=package.package_hash,
            final_batch_id=package.source_final_batch_id,final_run_id=package.source_final_run_id,
            final_result_hash=package.source_final_result_hash,forward_profile_id=profile.profile_id,
            shadow_evidence_ref=stages[-1].revision_id,maximum_capital_won=10000,
            execution_plan_mode=LEADER_NATIVE_PLAN_MODE,
            unfilled_execution_policy=replace(support.MockUnfilledReplacementDispatchTests.unfilled_execution_policy,
                version='mock-unfilled-execution-policy/v2'))
        readiness,_=publish_ready_mock_automation_spec(repo,profile=profile,stage_revisions=tuple(stages),spec=spec,
            shadow_event=shadow,current_binding=base.binding)
        case.assertEqual('READY',readiness.status.value)
        specs.append(spec)
    limits=MockAutomationAccountLimits(maximum_concurrent_strategies=2,maximum_concurrent_positions=2,
        maximum_capital_won=15000,maximum_daily_loss_won=3000,maximum_data_gap_seconds=5,
        maximum_submission_unknown_count=0,maximum_reconnect_count=2,maximum_balance_mismatch_count=0)
    selection=MockAutomationAccountSelection(specs[0].account_scope,support.PROFILE_ID,base.binding['binding_revision'],
        datetime.fromisoformat(base.binding['verified_at']),support.NOW+timedelta(hours=1),
        tuple(spec.spec_id for spec in specs),limits)
    return SimpleNamespace(repository=repo,selection=selection,specs=tuple(specs),binding=base.binding,
        implementation_hash=base.package.scientific_implementation_hash)


def verify_account_selection_round_trip(case,store):
    from kiwoom_monitor.application.mock_automation_admission import admit_mock_automation
    from kiwoom_monitor.domain.execution_activation import mock_automation_account_selection_from_dict
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
    data=account_selection_inputs(case,store)
    repo,value=data.repository,data.selection
    originals=[spec.to_dict() for spec in data.specs]
    source=Mock(return_value=data.implementation_hash)
    case.assertTrue(repo.publish_mock_automation_account_selection(value,expected_implementation_hash=source))
    source.assert_called_once_with('krx-regular/v1')
    case.assertFalse(repo.publish_mock_automation_account_selection(value,expected_implementation_hash=source))
    case.assertEqual(value,mock_automation_account_selection_from_dict(value.to_dict()))
    case.assertEqual((value,),repo.load_mock_automation_account_selections(value.account_scope.account_ref))
    case.assertEqual(value,repo.load_mock_automation_account_selection(value.account_scope.account_ref,value.selection_id))
    case.assertEqual(originals,[repo.load_mock_automation_spec(value.account_scope.account_ref,s.spec_id).to_dict() for s in data.specs])
    case.assertEqual(data.specs[0].spec_id,value.primary_spec_id)
    case.assertIsNone(repo.load_mock_automation_spec(value.account_scope.account_ref,value.selection_id))
    with case.assertRaisesRegex(ValueError,'operating spec is not stored'):
        admit_mock_automation(repo,repo,object(),account_ref=value.account_scope.account_ref,
            spec_id=value.selection_id,requested_at=value.frozen_at)
    case.assertIsNone(store.load_mock_automation_control(value.account_scope.account_ref))
    execution=ExecutionRepository(store)
    case.assertEqual(((),True),execution.account_active_intents(value.account_scope.account_ref))
    case.assertEqual((),execution.account_events('mock',value.account_scope.account_ref).events)
    return data


def verify_account_selection_ack_loss(case,store):
    data=account_selection_inputs(case,store)
    repo,value=data.repository,data.selection
    insert=store.insert_immutable_document
    committed=[]
    def lost_ack(collection,owner,key,document):
        result=insert(collection,owner,key,document)
        case.assertEqual(repo.MOCK_AUTOMATION_ACCOUNT_SELECTION_COLLECTION,collection)
        case.assertTrue(result)
        committed.append((owner,key))
        raise OSError('account selection immutable COMMIT response lost')
    with patch.object(store,'insert_immutable_document',side_effect=lost_ack):
        with case.assertRaisesRegex(OSError,'COMMIT response lost'):
            repo.publish_mock_automation_account_selection(value,expected_implementation_hash=lambda _:data.implementation_hash)
    case.assertEqual([(value.account_scope.account_ref,value.selection_id)],committed)
    case.assertEqual(value,repo.load_mock_automation_account_selection(value.account_scope.account_ref,value.selection_id))
    case.assertFalse(repo.publish_mock_automation_account_selection(value,expected_implementation_hash=lambda _:data.implementation_hash))
    with case.assertRaisesRegex(ValueError,'implementation changed'):
        repo.publish_mock_automation_account_selection(value,expected_implementation_hash=lambda _:'f'*64)
    case.assertEqual((value,),repo.load_mock_automation_account_selections(value.account_scope.account_ref))
    case.assertIsNone(store.load_mock_automation_control(value.account_scope.account_ref))


def verify_account_selection_invalid_member(case,store):
    from kiwoom_monitor.domain.order_contract import AccountScope
    data=account_selection_inputs(case,store)
    repo,value=data.repository,data.selection
    alpha,beta=data.specs
    for invalid in (replace(beta,shadow_evidence_ref=None),replace(beta,final_run_id='unrelated-final-run'),
                    replace(beta,frozen_at=value.frozen_at+timedelta(minutes=1))):
        repo.save_mock_automation_spec(invalid)
        selected=replace(value,member_spec_ids=(alpha.spec_id,invalid.spec_id))
        with case.assertRaises(ValueError):
            repo.publish_mock_automation_account_selection(selected,expected_implementation_hash=lambda _:data.implementation_hash)
    # Deliberately misplaced immutable input is rejected as a scope conflict;
    # it is not an actual peer plan and confers no account/order authority.
    other=replace(beta,account_scope=AccountScope(beta.account_scope.broker,beta.account_scope.environment,
        '11111111-1111-1111-1111-111111111111'))
    store.insert_immutable_document(repo.MOCK_AUTOMATION_SPEC_COLLECTION,value.account_scope.account_ref,
                                   other.spec_id,other.to_dict())
    with case.assertRaisesRegex(ValueError,'scope or freeze conflict'):
        repo.publish_mock_automation_account_selection(replace(value,member_spec_ids=(alpha.spec_id,other.spec_id)),
            expected_implementation_hash=lambda _:data.implementation_hash)
    misplaced='mock_automation_spec_'+'e'*64
    store.insert_immutable_document(repo.MOCK_AUTOMATION_SPEC_COLLECTION,value.account_scope.account_ref,
                                   misplaced,beta.to_dict())
    with case.assertRaisesRegex(ValueError,'identity conflict'):
        repo.publish_mock_automation_account_selection(replace(value,member_spec_ids=(alpha.spec_id,misplaced)),
            expected_implementation_hash=lambda _:data.implementation_hash)
    case.assertEqual((),repo.load_mock_automation_account_selections(value.account_scope.account_ref))


class MockAccountSelectionTests(unittest.TestCase):
    def setUp(self):
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.store=SQLiteQueryStore(self.root/'selection.sqlite3')
        self.store.initialize()
        self.addCleanup(self.store.close)

    def test_two_actual_ready_members_roundtrip_without_authority_or_legacy_spec_changes(self):
        verify_account_selection_round_trip(self,self.store)

    def test_explicit_order_changes_identity_and_missing_duplicate_boolean_or_extra_terms_fail(self):
        from kiwoom_monitor.domain.execution_activation import mock_automation_account_selection_from_dict
        data=account_selection_inputs(self,self.store)
        value=data.selection
        reordered=replace(value,member_spec_ids=tuple(reversed(value.member_spec_ids)))
        self.assertNotEqual(value.selection_id,reordered.selection_id)
        self.assertEqual(value.member_spec_ids[1],reordered.primary_spec_id)
        for change in (dict(member_spec_ids=()),dict(member_spec_ids=(value.member_spec_ids[0],)*2),
                       dict(binding_revision=True),dict(limits=None)):
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):
                replace(value,**change)
        for name in ('maximum_capital_won','maximum_data_gap_seconds'):
            with self.assertRaises(ValueError):replace(value.limits,**{name:True})
        for field in ('limits','member_spec_ids','frozen_at'):
            raw=value.to_dict();raw.pop(field)
            with self.assertRaises((ValueError,TypeError,KeyError)):mock_automation_account_selection_from_dict(raw)
        raw=value.to_dict();raw['unexpected_authority']=True
        with self.assertRaises((ValueError,TypeError)):mock_automation_account_selection_from_dict(raw)
        raw=value.to_dict();raw['limits']['maximum_capital_won']+=1
        with self.assertRaisesRegex(ValueError,'canonical'):mock_automation_account_selection_from_dict(raw)

    def test_missing_member_stale_implementation_and_binding_cannot_publish_partial_selection(self):
        data=account_selection_inputs(self,self.store)
        repo,value=data.repository,data.selection
        missing=replace(value,member_spec_ids=(value.member_spec_ids[0],'mock_automation_spec_'+'f'*64))
        with self.assertRaisesRegex(ValueError,'missing'):
            repo.publish_mock_automation_account_selection(missing,expected_implementation_hash=lambda _:data.implementation_hash)
        with self.assertRaisesRegex(ValueError,'implementation changed'):
            repo.publish_mock_automation_account_selection(value,expected_implementation_hash=lambda _:'f'*64)
        rotated={**data.binding,'verified_at':(value.frozen_at+timedelta(seconds=1)).isoformat()}
        self.store.append_account_binding(rotated)
        with self.assertRaisesRegex(ValueError,'current verified'):
            repo.publish_mock_automation_account_selection(value,expected_implementation_hash=lambda _:data.implementation_hash)
        self.assertEqual((),repo.load_mock_automation_account_selections(value.account_scope.account_ref))

    def test_authenticated_api_preserves_exact_composition_and_does_not_start_orders(self):
        from fastapi.testclient import TestClient
        from tests.unit import test_mock_automation_specification as support
        from kiwoom_monitor.central_server.app import create_app
        with support.mock_publication_contract_app(create_app,self.root/'api.sqlite3') as (app,store,base):
            data=account_selection_inputs(self,store,base=base)
            value=data.selection
            path='/api/v1/research/mock-automation-account-selections'
            body=dict(account_ref=value.account_scope.account_ref,credential_profile_id=value.credential_profile_id,
                expected_binding_revision=value.binding_revision,selection=value.to_dict())
            headers={'Authorization':'Bearer private-token'}
            with TestClient(app) as client:
                self.assertEqual(401,client.post(path,json=body).status_code)
                reply=client.post(path,headers=headers,json=body)
                self.assertEqual(200,reply.status_code,reply.text)
                self.assertEqual(dict(status='saved',selection_id=value.selection_id,orders_started=False),reply.json())
                reply=client.post(path,headers=headers,json=body)
                self.assertEqual(('unchanged',False),(reply.json()['status'],reply.json()['orders_started']))
                reply=client.get(path+'/'+value.account_scope.account_ref,headers=headers,
                    params={'credential_profile_id':value.credential_profile_id})
                self.assertEqual(200,reply.status_code,reply.text)
                self.assertEqual([value.to_dict()],reply.json()['selections'])
                self.assertEqual(422,client.post(path,headers=headers,json={**body,'expected_binding_revision':True}).status_code)
                self.assertEqual(409,client.post(path,headers=headers,json={**body,'expected_binding_revision':99}).status_code)
            self.assertIsNone(store.load_mock_automation_control(value.account_scope.account_ref))

    def test_committed_response_loss_returns_one_immutable_selection_and_rechecks_source_on_retry(self):
        verify_account_selection_ack_loss(self,self.store)

    def test_incomplete_foreign_or_conflicting_member_never_persists_a_partial_composition(self):
        verify_account_selection_invalid_member(self,self.store)

    def test_equivalent_binding_offsets_preserve_their_original_frozen_wire(self):
        from kiwoom_monitor.domain.execution_activation import mock_automation_account_selection_from_dict
        data=account_selection_inputs(self,self.store)
        value=replace(data.selection,binding_verified_at=data.selection.binding_verified_at.astimezone(
            timezone(timedelta(hours=9))))
        self.assertTrue(data.repository.publish_mock_automation_account_selection(value,
            expected_implementation_hash=lambda _:data.implementation_hash))
        self.assertEqual(value.to_dict(),data.repository.load_mock_automation_account_selection(
            value.account_scope.account_ref,value.selection_id).to_dict())
        self.assertEqual(value,mock_automation_account_selection_from_dict(value.to_dict()))

    def test_misplaced_selection_documents_fail_read_without_rewriting_the_original(self):
        data=account_selection_inputs(self,self.store)
        repo,value=data.repository,data.selection
        collection=repo.MOCK_AUTOMATION_ACCOUNT_SELECTION_COLLECTION
        self.store.insert_immutable_document(collection,value.account_scope.account_ref,'misplaced-key',value.to_dict())
        with self.assertRaisesRegex(ValueError,'identity conflict'):
            repo.load_mock_automation_account_selection(value.account_scope.account_ref,'misplaced-key')
        with self.assertRaisesRegex(ValueError,'identity conflict'):
            repo.load_mock_automation_account_selections(value.account_scope.account_ref)
        self.assertEqual(value.to_dict(),self.store.load_document(collection,value.account_scope.account_ref,
                                                                 'misplaced-key')['document'])


if __name__=='__main__':unittest.main()
