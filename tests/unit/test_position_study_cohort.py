"""Frozen native v2 pairs, using actual existing Paper runs and readonly stores."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from tests.unit import test_entry_thesis as native_cases
from kiwoom_monitor.application.position_target import freeze_position_action_study_cohort
from kiwoom_monitor.application.research_evaluation import (
    read_position_action_study_cohort, PositionOutcomeUnavailable)
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository


class NativePositionStudyCohortTests(unittest.TestCase):
    def setUp(self):
        self.base = native_cases.NativePositionStudyCaseTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.base.execution = replace(self.base.execution, cost_model=replace(
            self.base.execution.cost_model, commission_bps=15))
        self.base.case = replace(self.base.case, request=replace(
            self.base.case.request, cost_model=self.base.execution.cost_model))

    def prepare(self, *, action='ADD', minutes=(5, 9)):
        base = self.base
        target = {'ADD':20_000, 'REDUCE':5_000, 'EXIT':0, 'REENTER':20_000}[action]
        base.study = replace(base.study, actions=tuple(sorted(('HOLD', action))), target_notional_won=target)
        cases = []
        for minute in minutes:
            at = datetime.fromisoformat(base.helper.at(minute)) + timedelta(seconds=2)
            request = replace(base.case.request, action=action, decision_time=at,
                reference_price_available_at=at, target_policy=replace(base.case.request.target_policy,
                    target_notional_won=target, available_at=at, valid_until=at+timedelta(seconds=30)))
            cases.append(replace(base.case, case_ref=f'frozen-case-{minute}', branch_action=action, request=request))
        _, spec = base.identity(case=cases[0])
        return freeze_position_action_study_cohort(base.study, tuple(cases), spec, base.dataset.manifest,
            minimum_samples=2, max_evidence_age_seconds=86400,
            model_valid_until=datetime(2030, 1, 1, tzinfo=timezone.utc)), tuple(cases)

    def run_pairs(self, cohort, cases):
        from scripts.run_research import execute_research
        selected = []
        base = self.base
        for case in cases:
            for branch in ('HOLD', case.request.action):
                native_case = replace(case, branch_action=branch)
                run_id, spec = base.identity(case=native_case, position_study_cohort=cohort)
                base.writer.start_run(run_id, spec, base.dataset.manifest, claim_independent=True)
                result = execute_research(base.dataset, base.writer, base.helper.root/'runs', base.strategy,
                    base.execution, base.evaluation, session_profile=base.profile,
                    execution_scope='independent_development_validation/v1',
                    position_action_study=base.study, position_study_case=native_case, position_study_cohort=cohort)
                self.assertEqual('ELIGIBLE', result.report_status)
                events = base.writer.load_execution_events(run_id)
                start_time = case.request.decision_time.replace(second=0, microsecond=0)
                end_time = start_time + timedelta(seconds=base.study.horizon_seconds)
                def mark(at):
                    return next(e['event_id'] for e in events if e['event_type']=='MARK'
                        and datetime.fromisoformat(e['occurred_at']) == at)
                selected.append(dict(run_id=run_id, start_event_id=mark(start_time), end_event_id=mark(end_time),
                    action=branch, case_ref=case.case_ref))
        return selected

    def read(self, cohort, selections, *, loader=None, cut=None):
        reader = ResearchRepository(self.base.writer.path, read_only=True)
        return read_position_action_study_cohort(loader or reader.load_position_action_evidence,
            cohort, selections, decision_time=cut or self.base.cut)

    def test_actual_add_pairs_keep_negative_fees_and_original_policy_ids(self):
        cohort, cases = self.prepare()
        selected = self.run_pairs(cohort, cases)
        result = self.read(cohort, selected)
        self.assertEqual([], result['quality']['reasons'])
        self.assertEqual('COHORT_COMPARABLE', result['r4_status'])
        self.assertEqual('EV_UNAVAILABLE', result['current_goal_status'])
        self.assertFalse(result['usable_for_current_goal']); self.assertFalse(result['order_authorized'])
        self.assertEqual(2, result['result']['paired_sample_count'])
        self.assertEqual(9180, result['result']['return_denominator_won'])
        delta = result['result']['paired_delta']
        self.assertEqual(-13, delta['mean_net_change_won'])
        self.assertEqual(-15, delta['mean_net_change_bps_floor'])
        self.assertEqual(0, delta['net_standard_error_bps_ceil'])
        for binding in result['pairs']:
            originals = [c for c in result['cases'] if c['selection']['case_ref']==binding['case_ref']]
            for receipt in originals:
                key = 'action' if receipt['outcome']['action']=='ADD' else 'hold'
                self.assertEqual(receipt['outcome']['policy_version'], binding[f'original_{key}_policy_ref'])
                self.assertEqual(receipt['outcome']['execution_model_ref'], binding[f'original_{key}_execution_ref'])
                self.assertTrue(receipt['completion_after_goal_expiry'])

    def test_actual_reduce_pairs_use_native_sell_fee_and_existing_math(self):
        cohort, cases = self.prepare(action='REDUCE')
        result = self.read(cohort, self.run_pairs(cohort, cases))
        self.assertEqual([], result['quality']['reasons'])
        self.assertEqual('COHORT_COMPARABLE', result['r4_status'])
        self.assertEqual(4, result['result']['target_quantity'])
        self.assertEqual(6120, result['result']['return_denominator_won'])
        self.assertEqual(-9, result['result']['paired_delta']['mean_net_change_won'])
        self.assertFalse(result['order_authorized'])

    def reentry_pairs(self):
        # Existing registered family closes the prior trade before the case;
        # an explicit research target tests reentry, not native cooldown admission.
        self.base.strategy=replace(self.base.strategy,max_hold_minutes=1,cooldown_seconds=1800)
        self.base.study=replace(self.base.study,horizon_seconds=120)
        cohort,cases=self.prepare(action='REENTER',minutes=(7,11))
        return cohort,self.run_pairs(cohort,cases)

    def test_real_flat_reentry_pairs_follow_native_close_and_preserve_current_gate(self):
        cohort,selected=self.reentry_pairs()
        result=self.read(cohort,selected)
        self.assertEqual('COHORT_COMPARABLE',result['r4_status'],result)
        self.assertEqual((0,19,2),(result['result']['initial_quantity'],result['result']['target_quantity'],result['result']['paired_sample_count']))
        self.assertEqual(-29,result['result']['paired_delta']['mean_net_change_won'])
        self.assertEqual('EV_UNAVAILABLE',result['current_goal_status']);self.assertFalse(result['usable_for_current_goal'])
        self.assertFalse(result['order_authorized'])
        for receipt in result['cases']:
            self.assertIsNone(receipt['decision_proof']['portfolio']['position'])
            self.assertEqual('cooldown',receipt['decision_proof']['strategy_state']['status'])
            self.assertTrue(receipt['completion_after_goal_expiry'])

    def test_reentry_requires_prior_native_full_close_for_both_hold_and_action(self):
        cohort,selected=self.reentry_pairs()
        reader=ResearchRepository(self.base.writer.path,read_only=True)
        def absent(run_id,endpoints,**kwargs):
            context,events=reader.load_position_action_evidence(run_id,endpoints,**kwargs)
            return context,[e for e in events if not (e['event_type']=='FILL' and e['side']=='SELL')]
        result=self.read(cohort,selected,loader=absent)
        self.assertEqual('EV_UNAVAILABLE',result['r4_status']);self.assertIsNone(result['result'])
        self.assertEqual(4,len(result['excluded']));self.assertFalse(result['order_authorized'])

    def test_reentry_publication_uses_original_sources_lease_and_one_read_per_run(self):
        from kiwoom_monitor.application.research_evaluation import prepare_position_action_study_publication,decode_position_action_study_publication
        cohort,selected=self.reentry_pairs();reader=ResearchRepository(self.base.writer.path,read_only=True)
        before=self.base.writer.path.read_bytes();sql=[];connect=reader._connect
        def traced():
            connection=connect();connection.set_trace_callback(sql.append);return connection
        with patch.object(reader,'_connect',side_effect=traced),patch.object(reader,'load_position_action_evidence',wraps=reader.load_position_action_evidence) as borrowed:
            packet=prepare_position_action_study_publication(borrowed,cohort,selected,decision_time=self.base.cut)
        self.assertEqual(4,borrowed.call_count);self.assertEqual(16,sum(s.lstrip().upper().startswith('SELECT') for s in sql))
        self.assertFalse(any(s.lstrip().upper().startswith('COMMIT') for s in sql))
        self.assertEqual(before,self.base.writer.path.read_bytes())
        with patch('sqlite3.connect',side_effect=AssertionError('publication replay DB')):
            decoded=decode_position_action_study_publication(packet)
        self.assertEqual(packet['comparison'],decoded['comparison'])
        self.assertEqual(-29,decoded['comparison']['result']['paired_delta']['mean_net_change_won'])
        self.assertFalse(decoded['comparison']['usable_for_current_goal']);self.assertFalse(decoded['comparison']['order_authorized'])
        self.reentry_publication=packet

    def test_missing_declared_case_cannot_shrink_to_successful_subset(self):
        cohort, cases = self.prepare(minutes=(5, 9, 13))
        result = self.read(cohort, self.run_pairs(cohort, cases[:2]))
        self.assertIsNone(result['result']); self.assertEqual('EV_UNAVAILABLE', result['r4_status'])
        self.assertEqual(2, len(result['missing_branches']))
        self.assertIn('complete_frozen_study_pairs_required', result['quality']['reasons'])

    def test_no_completed_branches_remains_unavailable_without_source_read(self):
        cohort, _ = self.prepare()
        with patch.object(self.base.writer, 'load_position_action_evidence') as loader:
            result=self.read(cohort,[],loader=loader)
            loader.assert_not_called()
        self.assertEqual(4,len(result['missing_branches']))
        self.assertIsNone(result['result']);self.assertFalse(result['usable_for_current_goal'])

    def test_duplicate_renamed_or_undeclared_selection_fails_before_borrow(self):
        cohort, _ = self.prepare()
        selected = [dict(run_id=f'run-{i}', start_event_id=f'start-{i}', end_event_id=f'end-{i}',
            action=branch, case_ref=f'frozen-case-{minute}')
            for i, (minute, branch) in enumerate(((5,'HOLD'),(5,'ADD'),(9,'HOLD'),(9,'ADD')))]
        for change in (lambda s:s.__setitem__(3, deepcopy(s[0])),
                       lambda s:s[0].update(case_ref='renamed'), lambda s:s[3].update(run_id='run-0')):
            values=deepcopy(selected); change(values)
            with patch.object(self.base.writer, 'load_position_action_evidence') as loader:
                with self.assertRaises(ValueError):self.read(cohort, values, loader=loader)
                loader.assert_not_called()

    def test_cohort_declaration_rejects_overlap_hash_and_posthoc_changes(self):
        cohort, cases = self.prepare()
        # Inclusive MARK endpoints cannot be shared by successive cases.
        with self.assertRaises(ValueError):self.prepare(minutes=(5,8))
        _, spec = self.base.identity(case=cases[0])
        with self.assertRaises(ValueError):
            freeze_position_action_study_cohort(self.base.study, (cases[0], replace(cases[1], request=cases[0].request)),
                spec, self.base.dataset.manifest, minimum_samples=2, max_evidence_age_seconds=86400,
                model_valid_until=datetime(2030,1,1,tzinfo=timezone.utc))
        for altered in ({**cohort, 'cohort_ref':'changed'}, {**cohort, 'minimum_samples':True},
                        {**cohort, 'cases':cohort['cases'][:1]}):
            with self.assertRaises(ValueError):self.base.identity(case=cases[0], position_study_cohort=altered)
        with self.assertRaises(ValueError):
            self.base.identity(case=replace(cases[0], case_ref='posthoc'), position_study_cohort=cohort)

    def test_incomplete_native_branch_prevents_subset_statistics(self):
        cohort, cases = self.prepare(); selected = self.run_pairs(cohort, cases)
        from kiwoom_monitor.application import research_evaluation as evaluation
        original = evaluation.read_position_action_study_case
        def unavailable(loader, selection, **kwargs):
            if selection['case_ref']==cases[1].case_ref and selection['action']=='ADD':
                raise PositionOutcomeUnavailable('controlled unsupported native branch')
            return original(loader, selection, **kwargs)
        with patch.object(evaluation, 'read_position_action_study_case', side_effect=unavailable):
            result = self.read(cohort, selected)
        self.assertIsNone(result['result']); self.assertEqual(1,len(result['excluded']))
        self.assertEqual('EV_UNAVAILABLE', result['r4_status'])

    def test_exit_declaration_is_rejected_before_native_execution(self):
        # The native automatic-sizing request contract excludes EXIT; do not
        # bypass it to manufacture an unsupported source outcome.
        with patch.object(self.base.writer,'start_run') as start:
            with self.assertRaises(ValueError):self.prepare(action='EXIT')
            start.assert_not_called()

    def test_exact_pair_state_and_whole_cohort_expiry_are_required(self):
        cohort, cases = self.prepare(); selected = self.run_pairs(cohort, cases)
        from kiwoom_monitor.application import research_evaluation as evaluation
        original = evaluation.read_position_action_study_case
        def altered(loader, selection, **kwargs):
            result = original(loader, selection, **kwargs)
            if selection['action']=='HOLD':result['decision_proof']['portfolio']['cash_won']+=1
            return result
        with patch.object(evaluation, 'read_position_action_study_case', side_effect=altered):
            result = self.read(cohort, selected)
        self.assertIsNone(result['result'])
        self.assertIn('study_pair_initial_state_market_or_goal_mismatch', result['quality']['reasons'])
        expired = self.read(cohort, selected, cut=self.base.cut+timedelta(days=2))
        self.assertIsNone(expired['result'])
        self.assertIn('study_cohort_evidence_expired_or_future',expired['quality']['reasons'])

    def test_one_readonly_borrow_per_run_no_commit_refresh_or_source_write(self):
        cohort, cases = self.prepare(); selected = self.run_pairs(cohort, cases)
        reader = ResearchRepository(self.base.writer.path, read_only=True)
        sql=[]; connect=reader._connect; before=self.base.writer.path.read_bytes()
        def observed():
            connection=connect();connection.set_trace_callback(sql.append);return connection
        with patch.object(reader, '_connect', side_effect=observed), patch.object(reader,
                'load_position_action_evidence', wraps=reader.load_position_action_evidence) as borrow:
            first=self.read(cohort,selected,loader=borrow);second=self.read(cohort,selected,loader=borrow)
        self.assertEqual(8,borrow.call_count)
        self.assertEqual(32,sum(s.lstrip().upper().startswith('SELECT') for s in sql))
        self.assertFalse(any(s.lstrip().upper().startswith('COMMIT') for s in sql))
        self.assertEqual(first,second);self.assertEqual(before,self.base.writer.path.read_bytes())

    def test_native_source_cannot_attach_different_cohort_after_run(self):
        cohort, cases = self.prepare(); selected=self.run_pairs(cohort,cases)
        reader=ResearchRepository(self.base.writer.path,read_only=True)
        def missing(*args,**kwargs):
            context,events=reader.load_position_action_evidence(*args,**kwargs)
            context.pop('position_study_cohort')
            return context,events
        with self.assertRaises(ValueError):self.read(cohort,selected,loader=missing)

    def test_aggregate_event_budget_is_shared_across_all_native_branches(self):
        cohort,cases=self.prepare(); selected=self.run_pairs(cohort,cases)
        reader=ResearchRepository(self.base.writer.path,read_only=True)
        def oversized(*args,**kwargs):
            context,events=reader.load_position_action_evidence(*args,**kwargs)
            return context,events*(kwargs['limit']//len(events)+1)
        with self.assertRaisesRegex(ValueError,'total native study event budget'):
            self.read(cohort,selected,loader=oversized)


if __name__=='__main__':unittest.main()
