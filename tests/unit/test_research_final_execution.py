from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
import sqlite3
from threading import Barrier
import unittest
from unittest.mock import patch

import test_research_final_preparation as fixtures
from kiwoom_monitor import research_process as rp
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset
from scripts import run_research as runner
from kiwoom_monitor.application.mock_automation_candidate import (
    CandidateEligibilityStatus,
    MockAutomationEligibilityPolicy,
)
from datetime import datetime, timedelta


class FinalExecutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FinalPreparationTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.prepared = self.fixture.prepare()
        self.request = self.prepared.candidates[0]
        self.repo = ResearchRepository(self.request.database)
        self.owner = 'final-owner'

    def identity(self, request=None):
        request = request or self.request
        candidate_hash = rp.final_candidate_spec_hash(request, self.prepared.implementation_hash)
        run_id, spec = runner.research_run_identity(self.prepared.dataset, request.strategy, request.execution,
            request.evaluation, session_profile=request.session_profile,
            execution_scope='independent_final_holdout/v1')
        return candidate_hash, run_id, spec

    def claim(self, owner=None):
        candidate_hash, run_id, spec = self.identity()
        state = self.repo.claim_final_holdout_execution(self.prepared.batch,
            candidate_spec_hash=candidate_hash, run_id=run_id, spec=spec,
            input_manifest=self.prepared.dataset.manifest, owner_token=owner or self.owner)
        return state, candidate_hash, run_id, spec

    def execute(self, **kwargs):
        return rp.execute_final_holdout_evaluation(self.prepared, owner_token=self.owner, **kwargs)

    def test_real_final_execution_uses_fresh_engine_and_completes_owned_candidate(self):
        initial = []
        original = runner.PaperExecutionEngine
        class RecordingEngine(original):
            def __init__(engine, *args, **kwargs):
                super().__init__(*args, **kwargs)
                initial.append((engine.portfolio.cash_won, engine.strategy_state.status))
        with patch.object(runner, 'PaperExecutionEngine', RecordingEngine):
            result = self.execute()
        row = result['candidates'][0]
        self.assertEqual(('COMPLETED','COMPLETED'), (result['batch_status'], row['state']))
        self.assertEqual([(self.request.execution.initial_cash_won, 'flat')], initial)
        self.assertEqual('completed', self.repo.load_run(row['run_id'])['status'])
        execution = self.repo.load_final_holdout_executions(self.prepared.batch.batch_id)[0]
        self.assertEqual(('COMPLETED',row['logical_result_hash']),
                         (execution['state'],execution['logical_result_hash']))
        report = self.repo.load_research_report(row['run_id'])
        self.assertEqual(('OOS',1), (report['fold_reports'][0]['role'],len(report['fold_reports'])))
        output = json.loads((self.request.runs_dir/row['run_id']/'manifest.json').read_text(encoding='utf-8'))
        self.assertIn('final_holdout_partition', output); self.assertNotIn('development_partition', output)
        self.assertIn('no_cross_candidate_selection', result['limitations'])

    def test_completed_final_evidence_builds_bounded_candidate_publication(self):
        result = self.execute()
        row = result['candidates'][0]
        run = self.repo.load_run(row['run_id'])
        candidate_hash = rp.final_candidate_spec_hash(
            self.request, self.prepared.implementation_hash,
        )
        policy = MockAutomationEligibilityPolicy(
            strategy_ref='strategy-final-1', candidate_spec_hash=candidate_hash,
            frozen_at=datetime.fromisoformat(run['started_at']) - timedelta(seconds=1),
            minimum_closed_trades=1_000_000, minimum_active_days=1,
            minimum_net_realized_pnl_won=0, maximum_drawdown_ppm=100_000,
        )
        publication = rp.prepare_mock_automation_candidate_publication(
            self.repo, self.request, implementation_hash=self.prepared.implementation_hash,
            strategy_ref='strategy-final-1', account_ref='account-ref-1',
            final_batch_id=self.prepared.batch.batch_id, final_run_id=row['run_id'],
            eligibility_policy=policy,
        )
        self.assertEqual(candidate_hash, publication['package']['candidate_spec_hash'])
        self.assertEqual(row['logical_result_hash'], publication['package']['source_final']['result_hash'])
        self.assertEqual(
            CandidateEligibilityStatus.BLOCKED.value,
            publication['eligibility_receipt']['status'],
        )

    def test_completed_candidate_is_cached_only_after_report_and_manifest_validation(self):
        first = self.execute(); run_id = first['candidates'][0]['run_id']
        with patch.object(rp, 'execute_research', side_effect=AssertionError('must not execute')):
            second = self.execute()
        self.assertEqual(('COMPLETED','CACHED'), (second['batch_status'],second['candidates'][0]['state']))
        (self.request.runs_dir/run_id/'manifest.json').unlink()
        with patch.object(rp, 'execute_research', side_effect=AssertionError('immutable completion cannot rerun')):
            invalid = self.execute()
        self.assertEqual(('PARTIAL','CACHE_INVALID'), (invalid['batch_status'],invalid['candidates'][0]['state']))

    def test_final_scope_without_repository_claim_is_rejected_before_engine(self):
        candidate_hash, _, _ = self.identity()
        with patch.object(runner, 'PaperExecutionEngine', side_effect=AssertionError('no engine')):
            with self.assertRaisesRegex(ValueError, 'repository-owned claim'):
                runner.execute_research(self.prepared.dataset, self.repo, self.request.runs_dir,
                    self.request.strategy, self.request.execution, self.request.evaluation,
                    session_profile=self.request.session_profile,
                    execution_scope='independent_final_holdout/v1',
                    expected_code_hash=self.prepared.implementation_hash)
            with self.assertRaisesRegex(ValueError, 'not owned'):
                runner.execute_research(self.prepared.dataset, self.repo, self.request.runs_dir,
                    self.request.strategy, self.request.execution, self.request.evaluation,
                    session_profile=self.request.session_profile,
                    execution_scope='independent_final_holdout/v1',
                    expected_code_hash=self.prepared.implementation_hash,
                    final_execution={'batch_id':self.prepared.batch.batch_id,
                                     'candidate_spec_hash':candidate_hash,'owner_token':self.owner})

    def test_atomic_claim_has_one_owner_and_running_candidate_is_not_retried(self):
        candidate_hash, run_id, spec = self.identity(); barrier = Barrier(2)
        def claim(owner):
            repository = ResearchRepository(self.request.database); barrier.wait(timeout=5)
            return repository.claim_final_holdout_execution(self.prepared.batch,
                candidate_spec_hash=candidate_hash, run_id=run_id, spec=spec,
                input_manifest=self.prepared.dataset.manifest, owner_token=owner)
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(['busy','claimed'], sorted(executor.map(claim, ('one','two'))))
        with patch.object(rp, 'execute_research', side_effect=AssertionError('busy cannot run')):
            result = self.execute()
        self.assertEqual(('PARTIAL','BUSY'), (result['batch_status'],result['candidates'][0]['state']))

    def test_claim_rejects_unlocked_candidate_identity_or_unowned_existing_run(self):
        candidate_hash, run_id, spec = self.identity()
        for changed in ('candidate','run','scope','manifest'):
            kwargs = dict(candidate_spec_hash=candidate_hash,run_id=run_id,spec=spec,
                          input_manifest=self.prepared.dataset.manifest,owner_token=self.owner)
            if changed == 'candidate': kwargs['candidate_spec_hash'] = 'f'*64
            elif changed == 'run': kwargs['run_id'] = 'run_'+'f'*64
            elif changed == 'scope': kwargs['spec'] = {**spec,'execution_scope':'other'}
            else: kwargs['input_manifest'] = {**self.prepared.dataset.manifest,'runtime_input_version':'other'}
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.repo.claim_final_holdout_execution(self.prepared.batch, **kwargs)
        self.repo.start_run(run_id, spec, self.prepared.dataset.manifest)
        with self.assertRaisesRegex(ValueError, 'without final ownership'): self.claim()

    def test_exposed_window_cannot_claim(self):
        self.repo.expose_final_holdout(self.prepared.batch.window_id, request_id='expose-final',
            exposed_at=self.fixture.at, reason='used to change development strategy')
        with self.assertRaisesRegex(ValueError, 'cannot execute'): self.claim()

    def test_running_candidate_blocks_development_exposure_until_terminal(self):
        _, candidate_hash, run_id, _ = self.claim()
        with self.assertRaisesRegex(ValueError, 'running final execution'):
            self.repo.expose_final_holdout(self.prepared.batch.window_id, request_id='too-early',
                exposed_at=self.fixture.at, reason='premature development use')
        self.repo.finish_final_holdout_execution(self.prepared.batch.batch_id,candidate_hash,
            run_id=run_id,owner_token=self.owner,outcome='CANCELLED',reason='fixture cancellation')
        self.assertTrue(self.repo.expose_final_holdout(self.prepared.batch.window_id, request_id='after-terminal',
            exposed_at=self.fixture.at, reason='reviewed terminal final evidence'))

    def test_execution_failure_is_terminal_and_never_automatically_retried(self):
        with patch.object(rp, 'execute_research', side_effect=ValueError('engine fixture')) as execute:
            first = self.execute()
        self.assertEqual(('FAILED','failed'), (first['candidates'][0]['state'],
                         self.repo.load_run(first['candidates'][0]['run_id'])['status']))
        with patch.object(rp, 'execute_research', side_effect=AssertionError('terminal failure cannot retry')) as retry:
            second = self.execute()
        self.assertEqual(('PARTIAL','FAILED'), (second['batch_status'],second['candidates'][0]['state']))
        self.assertEqual((1,0), (execute.call_count,retry.call_count))

    def test_cancellation_is_terminal_and_leaves_later_candidates_not_started(self):
        separate = fixtures.FinalPreparationTests(); separate.setUp(); self.addCleanup(separate.doCleanups)
        request = separate.request
        other = replace(request, strategy=replace(request.strategy,
                        buffer_bps=request.strategy.buffer_bps+1))
        batch = separate.make_batch((request,other))
        prepared = rp.prepare_final_holdout_evaluation(batch, (request,other),
            request_id='two-candidates', accessed_at=separate.at)
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            return calls >= 2
        result = rp.execute_final_holdout_evaluation(prepared, owner_token=self.owner,
                                                     cancel_requested=cancelled)
        self.assertEqual(['CANCELLED','NOT_STARTED'], [row['state'] for row in result['candidates']])
        repo = ResearchRepository(request.database)
        self.assertEqual('cancelled', repo.load_run(result['candidates'][0]['run_id'])['status'])
        self.assertEqual(1,len(repo.load_final_holdout_executions(batch.batch_id)))

    def test_two_fixed_candidates_each_receive_fresh_engine_without_combined_selection(self):
        separate = fixtures.FinalPreparationTests(); separate.setUp(); self.addCleanup(separate.doCleanups)
        request = separate.request
        other = replace(request, strategy=replace(request.strategy,
                        buffer_bps=request.strategy.buffer_bps+1))
        batch = separate.make_batch((request,other))
        prepared = rp.prepare_final_holdout_evaluation(batch,(request,other),
            request_id='two-complete',accessed_at=separate.at)
        initial = []
        original = runner.PaperExecutionEngine
        class RecordingEngine(original):
            def __init__(engine,*args,**kwargs):
                super().__init__(*args,**kwargs)
                initial.append((engine.portfolio.cash_won,engine.strategy_state.status))
        with patch.object(runner,'PaperExecutionEngine',RecordingEngine):
            result = rp.execute_final_holdout_evaluation(prepared,owner_token=self.owner)
        self.assertEqual(('COMPLETED',['COMPLETED','COMPLETED']),
                         (result['batch_status'],[row['state'] for row in result['candidates']]))
        self.assertEqual([(request.execution.initial_cash_won,'flat')]*2,initial)
        self.assertEqual(batch.candidate_spec_hashes,tuple(row['candidate_spec_hash'] for row in result['candidates']))
        self.assertNotIn('comparison',result)

    def test_cancel_before_first_claim_creates_no_execution_or_run(self):
        result = self.execute(cancel_requested=lambda: True)
        self.assertEqual(('PARTIAL','CANCELLED'), (result['batch_status'],result['candidates'][0]['state']))
        self.assertEqual((),self.repo.load_final_holdout_executions(self.prepared.batch.batch_id))
        with closing(sqlite3.connect(self.request.database)) as connection:
            self.assertEqual(0,connection.execute('SELECT COUNT(*) FROM research_runs').fetchone()[0])

    def test_invalid_owner_paths_or_mutated_input_are_rejected_before_claim(self):
        for owner in ('',True,'x'*257,'bad\x00owner'):
            with self.subTest(owner=owner), self.assertRaises(ValueError):
                rp.execute_final_holdout_evaluation(self.prepared,owner_token=owner,
                                                    cancel_requested=lambda: True)
        changed_request = replace(self.request,runs_dir=self.fixture.root/'different-runs')
        changed = replace(self.prepared,candidates=(changed_request,))
        with self.assertRaisesRegex(ValueError,'shared absolute storage paths'):
            rp.execute_final_holdout_evaluation(changed,owner_token=self.owner)
        malformed = replace(self.prepared,dataset=FrozenResearchDataset(
            {**self.prepared.dataset.manifest,'runtime_input_version':'other'},self.prepared.dataset.observations))
        with self.assertRaisesRegex(ValueError,'isolated final input'):
            rp.execute_final_holdout_evaluation(malformed,owner_token=self.owner)
        self.assertEqual((),self.repo.load_final_holdout_executions(self.prepared.batch.batch_id))

    def test_owner_and_terminal_completion_are_fenced_and_immutable(self):
        state, candidate_hash, run_id, _ = self.claim(); self.assertEqual('claimed',state)
        with self.assertRaisesRegex(ValueError, 'not owned'):
            self.repo.assert_final_holdout_execution_owner(run_id,batch_id=self.prepared.batch.batch_id,
                candidate_spec_hash=candidate_hash,owner_token='wrong')
        self.repo.cancel_run(run_id,'cancelled fixture')
        self.assertTrue(self.repo.finish_final_holdout_execution(self.prepared.batch.batch_id,candidate_hash,
            run_id=run_id,owner_token=self.owner,outcome='CANCELLED',reason='cancelled fixture'))
        self.assertFalse(self.repo.finish_final_holdout_execution(self.prepared.batch.batch_id,candidate_hash,
            run_id=run_id,owner_token=self.owner,outcome='CANCELLED',reason='cancelled fixture'))
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.repo.finish_final_holdout_execution(self.prepared.batch.batch_id,candidate_hash,
                run_id=run_id,owner_token=self.owner,outcome='FAILED',reason='changed')
        self.assertEqual('cancelled',self.claim()[0])

    def test_same_batch_can_be_prepared_again_but_terminal_candidate_cannot_run_again(self):
        first = self.execute()
        again = self.fixture.prepare()
        with patch.object(rp, 'execute_research', side_effect=AssertionError('no rerun')):
            second = rp.execute_final_holdout_evaluation(again,owner_token='new-owner')
        self.assertEqual(('COMPLETED','CACHED'), (second['batch_status'],second['candidates'][0]['state']))
        self.assertEqual(first['candidates'][0]['run_id'],second['candidates'][0]['run_id'])

    def test_output_publication_failure_is_durable_failed_not_completed(self):
        with patch.object(runner, '_write_immutable_json', side_effect=OSError('publish fixture')):
            result = self.execute()
        row = result['candidates'][0]
        self.assertEqual(('FAILED','failed'), (row['state'],self.repo.load_run(row['run_id'])['status']))
        self.assertEqual('FAILED',self.repo.load_final_holdout_executions(self.prepared.batch.batch_id)[0]['state'])

    def test_failed_candidate_requires_explicit_audited_recovery_and_reuses_identity(self):
        with patch.object(rp, 'execute_research', side_effect=ValueError('recoverable fixture')):
            failed = self.execute()
        candidate_hash, run_id, _ = self.identity()
        recovered = rp.execute_final_holdout_evaluation(self.prepared, owner_token='recovery-owner',
            recoveries={candidate_hash: {'request_id': 'recover-failed-1',
                                         'reason': 'reviewed deterministic fixture failure'}})
        self.assertEqual(('FAILED', 'COMPLETED'),
                         (failed['candidates'][0]['state'], recovered['candidates'][0]['state']))
        self.assertEqual(run_id, recovered['candidates'][0]['run_id'])
        execution = self.repo.load_final_holdout_executions(self.prepared.batch.batch_id)[0]
        recovery = self.repo.load_final_holdout_recoveries(self.prepared.batch.batch_id)[0]
        self.assertEqual(('COMPLETED', 2, 'recovery-owner'),
                         (execution['state'], execution['generation'], execution['owner_token']))
        self.assertEqual(('CLAIMED', 2, 'recover-failed-1'),
                         (recovery['state'], recovery['generation'], recovery['request_id']))

    def test_cancelled_candidate_can_be_explicitly_recovered(self):
        with patch.object(rp, 'execute_research', side_effect=runner.ResearchRunCancelled('fixture cancel')):
            cancelled = self.execute()
        candidate_hash, _, _ = self.identity()
        recovered = rp.execute_final_holdout_evaluation(self.prepared, owner_token='cancel-recovery-owner',
            recoveries={candidate_hash: {'request_id': 'recover-cancelled-1',
                                         'reason': 'operator reviewed cancellation'}})
        self.assertEqual(('CANCELLED', 'COMPLETED'),
                         (cancelled['candidates'][0]['state'], recovered['candidates'][0]['state']))

    def test_output_publication_failure_can_recover_existing_immutable_rows(self):
        with patch.object(runner, '_write_immutable_json', side_effect=OSError('publish fixture')):
            failed = self.execute()
        candidate_hash, run_id, _ = self.identity()
        self.assertIsNotNone(self.repo.load_research_report(run_id))
        recovered = rp.execute_final_holdout_evaluation(self.prepared, owner_token='publish-recovery-owner',
            recoveries={candidate_hash: {'request_id': 'recover-publish-1',
                                         'reason': 'confirmed manifest publication failure'}})
        self.assertEqual(('FAILED', 'COMPLETED'),
                         (failed['candidates'][0]['state'], recovered['candidates'][0]['state']))
        self.assertTrue((self.request.runs_dir/run_id/'manifest.json').is_file())

    def test_existing_output_manifest_blocks_recovery_before_audit_write(self):
        with patch.object(rp, 'execute_research', side_effect=ValueError('failed fixture')):
            self.execute()
        candidate_hash, run_id, _ = self.identity()
        path = self.request.runs_dir/run_id/'manifest.json'
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'manual review'):
            rp.execute_final_holdout_evaluation(self.prepared, owner_token='recovery-owner',
                recoveries={candidate_hash: {'request_id': 'unsafe-output',
                                             'reason': 'must not accept existing artifact'}})
        self.assertEqual((), self.repo.load_final_holdout_recoveries(self.prepared.batch.batch_id))
        self.assertEqual('FAILED', self.repo.load_final_holdout_executions(self.prepared.batch.batch_id)[0]['state'])

    def test_recovery_request_is_idempotent_but_request_id_collision_is_rejected(self):
        with patch.object(rp, 'execute_research', side_effect=ValueError('failed fixture')):
            self.execute()
        candidate_hash, _, _ = self.identity()
        arguments = dict(candidate_spec_hash=candidate_hash, request_id='recovery-idempotent',
                         owner_token='recovery-owner', reason='reviewed failure evidence')
        self.assertTrue(self.repo.request_final_holdout_recovery(self.prepared.batch, **arguments))
        self.assertFalse(self.repo.request_final_holdout_recovery(self.prepared.batch, **arguments))
        with self.assertRaisesRegex(ValueError, 'already bound'):
            self.repo.request_final_holdout_recovery(self.prepared.batch,
                **{**arguments, 'reason': 'different reason'})
        with self.assertRaisesRegex(ValueError, 'generation already has'):
            self.repo.request_final_holdout_recovery(self.prepared.batch,
                **{**arguments, 'request_id': 'premature-next-recovery'})

    def test_concurrent_recovery_claim_has_one_owner(self):
        with patch.object(rp, 'execute_research', side_effect=ValueError('failed fixture')):
            self.execute()
        candidate_hash, run_id, spec = self.identity()
        self.repo.request_final_holdout_recovery(self.prepared.batch, candidate_spec_hash=candidate_hash,
            request_id='recovery-race', owner_token='recovery-owner', reason='reviewed race fixture')
        barrier = Barrier(2)
        def claim():
            repository = ResearchRepository(self.request.database); barrier.wait(timeout=5)
            return repository.claim_final_holdout_execution(self.prepared.batch,
                candidate_spec_hash=candidate_hash, run_id=run_id, spec=spec,
                input_manifest=self.prepared.dataset.manifest, owner_token='recovery-owner',
                recovery_request_id='recovery-race')
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(['busy', 'claimed'], sorted(executor.map(lambda _: claim(), range(2))))
        self.assertEqual(2, self.repo.load_final_holdout_executions(self.prepared.batch.batch_id)[0]['generation'])

    def test_each_additional_terminal_retry_requires_a_new_audited_request(self):
        candidate_hash, _, _ = self.identity()
        with patch.object(rp, 'execute_research', side_effect=ValueError('initial failure')):
            self.execute()
        with patch.object(rp, 'execute_research', side_effect=ValueError('recovery failure')):
            second = rp.execute_final_holdout_evaluation(self.prepared, owner_token='recovery-owner-1',
                recoveries={candidate_hash: {'request_id': 'recovery-generation-2',
                                             'reason': 'first reviewed recovery'}})
        self.assertEqual('FAILED', second['candidates'][0]['state'])
        with patch.object(rp, 'execute_research', side_effect=AssertionError('claimed request cannot be reused')) as retry:
            same = rp.execute_final_holdout_evaluation(self.prepared, owner_token='recovery-owner-1',
                recoveries={candidate_hash: {'request_id': 'recovery-generation-2',
                                             'reason': 'first reviewed recovery'}})
        self.assertEqual(('FAILED', 0), (same['candidates'][0]['state'], retry.call_count))
        completed = rp.execute_final_holdout_evaluation(self.prepared, owner_token='recovery-owner-2',
            recoveries={candidate_hash: {'request_id': 'recovery-generation-3',
                                         'reason': 'second reviewed recovery'}})
        self.assertEqual('COMPLETED', completed['candidates'][0]['state'])
        execution = self.repo.load_final_holdout_executions(self.prepared.batch.batch_id)[0]
        recoveries = self.repo.load_final_holdout_recoveries(self.prepared.batch.batch_id)
        self.assertEqual(3, execution['generation'])
        self.assertEqual([2, 3], [row['generation'] for row in recoveries])

    def test_recovery_rejects_nonterminal_unknown_or_exposed_candidate_before_claim(self):
        candidate_hash, _, _ = self.identity()
        with self.assertRaisesRegex(ValueError, 'failed or cancelled'):
            rp.execute_final_holdout_evaluation(self.prepared, owner_token='recovery-owner',
                recoveries={candidate_hash: {'request_id': 'not-run', 'reason': 'not terminal'}})
        with patch.object(rp, 'execute_research', side_effect=ValueError('failed fixture')):
            self.execute()
        self.repo.expose_final_holdout(self.prepared.batch.window_id, request_id='expose-after-failure',
            exposed_at=self.fixture.at, reason='reviewed failure for development')
        with self.assertRaisesRegex(ValueError, 'exposed'):
            self.repo.request_final_holdout_recovery(self.prepared.batch, candidate_spec_hash=candidate_hash,
                request_id='after-exposure', owner_token='recovery-owner', reason='too late')

    def test_malformed_recovery_map_is_rejected_without_ledger_changes(self):
        candidate_hash, _, _ = self.identity()
        malformed = (
            {'f'*64: {'request_id': 'unknown', 'reason': 'unknown candidate'}},
            {candidate_hash: {'request_id': '', 'reason': 'missing ID'}},
            {candidate_hash: {'request_id': 'extra', 'reason': 'extra field', 'other': 'x'}},
        )
        for recoveries in malformed:
            with self.subTest(recoveries=recoveries), self.assertRaises(ValueError):
                rp.execute_final_holdout_evaluation(self.prepared, owner_token=self.owner,
                                                    recoveries=recoveries)
        self.assertEqual((), self.repo.load_final_holdout_executions(self.prepared.batch.batch_id))
        self.assertEqual((), self.repo.load_final_holdout_recoveries(self.prepared.batch.batch_id))

    def test_implementation_or_prepared_policy_drift_rejected_before_claim(self):
        with patch.object(rp, 'research_implementation_hash', return_value='f'*64):
            with self.assertRaisesRegex(ValueError, 'changed before execution'): self.execute()
        changed = replace(self.prepared, candidates=(replace(self.request,
                          evaluation=self.fixture.fixture.batch.request.evaluation),))
        with self.assertRaises(ValueError):
            rp.execute_final_holdout_evaluation(changed,owner_token=self.owner)
        self.assertEqual((),self.repo.load_final_holdout_executions(self.prepared.batch.batch_id))


if __name__ == '__main__':
    unittest.main()
