from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from io import StringIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_monitor.application.market_session_schedule import research_session_profile_document
from kiwoom_monitor.application.research_splits import (
    DevelopmentPartitionSpec, DEVELOPMENT_PARTITION_VERSION, ResearchEvaluationSpec, ResearchFoldSpec,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import (
    FrozenResearchDataset, prepare_development_partition, development_partition_start,
)
from kiwoom_monitor.research_process import execute_process_request, load_research_process_request
from scripts.run_research import execute_research, PaperExecutionEngine, ResearchRunCancelled, main as research_main
from test_research_bundle_execution import rows_for, child, KST, PROFILE
from test_research_execution import _execution as base_execution, _strategy
from test_research_process import _request_document


def _execution():
    execution = base_execution()
    return replace(execution, cost_model=replace(execution.cost_model, rate_basis='model_estimate',
                   source='partition fixture', valid_from='2026-09-14T00:00:00+09:00', valid_to='2026-09-15T00:00:00+09:00'))


class DevelopmentPartitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.day = date(2026, 9, 14)
        self.base = datetime.combine(self.day, datetime.min.time(), tzinfo=KST).replace(hour=9)
        self.rows = tuple(rows_for(self.day, 30))
        self.evaluation = ResearchEvaluationSpec(
            'chronological_holdout/v1', tuple(ResearchFoldSpec(name, role, self.at(start), self.at(end))
                for name, role, start, end in [('train', 'TRAIN', 2, 10), ('validation', 'VALIDATION', 12, 20), ('final', 'OOS', 22, 30)]),
            120, 120, 0, 0, 0,
        )
        self.partition = DevelopmentPartitionSpec(DEVELOPMENT_PARTITION_VERSION, 'train')
        self.dataset = FrozenResearchDataset({
            'dataset_id': 'source-with-final', 'revision_count': len(self.rows),
            'revision_ids_hash': hashlib.sha256('\n'.join(row['revision_id'] for row in self.rows).encode()).hexdigest(),
            'schema_version': 1, 'kinds': ['ranking', 'top20_membership', 'minute_bar'], 'subject': '',
            'research_session_profile': research_session_profile_document(PROFILE),
            'children': [{'final_private': True}], 'fixed_watermark': 'global-final-watermark',
            'quality_summary': {'reasons': ['final_shortage']}, 'future_count': 999,
        }, self.rows, (
            {'snapshot_id': 'old', 'available_at': (self.base - timedelta(hours=1)).timestamp(), 'document': {}},
            {'snapshot_id': 'during', 'available_at': self.at(5), 'document': {}},
            {'snapshot_id': 'future', 'available_at': self.at(23), 'document': {'secret_final': True}},
            {'snapshot_id': 'unknown', 'document': {'not_historical': True}},
        ))

    def at(self, minute):
        return (self.base + timedelta(minutes=minute)).isoformat()

    def prepare(self, dataset=None, partition=None, **kwargs):
        return prepare_development_partition(dataset or self.dataset, partition or self.partition, self.evaluation, **kwargs)

    def test_policy_roundtrip_and_final_selection_are_guarded(self):
        self.assertEqual(self.partition, DevelopmentPartitionSpec.from_dict(self.partition.to_dict()))
        for name in ('final', 'missing'):
            with self.assertRaisesRegex(ValueError, 'TRAIN or VALIDATION'):
                self.prepare(partition=replace(self.partition, fold_name=name))
        with self.assertRaisesRegex(ValueError, 'unregistered'):
            replace(self.partition, version='development_partition/v999')
        with self.assertRaisesRegex(ValueError, 'unknown'):
            DevelopmentPartitionSpec.from_dict({**self.partition.to_dict(), 'allow_final': True})

    def test_exposed_policy_cannot_be_relabelled_as_unused(self):
        seen = replace(self.evaluation, final_holdout_accessed_at=self.at(31), final_holdout_access_reason='already viewed')
        with self.assertRaisesRegex(ValueError, 'exposed final'):
            prepare_development_partition(self.dataset, self.partition, seen)

    def test_only_warmup_and_selected_window_are_passed_to_runner(self):
        prepared = self.prepare()
        end = datetime.fromisoformat(self.at(10))
        self.assertTrue(all(datetime.fromisoformat(row['available_at']) < end for row in prepared.observations))
        self.assertEqual(('old', 'during'), tuple(theme['snapshot_id'] for theme in prepared.theme_snapshots))
        self.assertEqual(len(prepared.observations), prepared.manifest['revision_count'])
        self.assertNotIn('children', prepared.manifest)
        self.assertNotIn('fixed_watermark', prepared.manifest)
        self.assertNotIn('future_count', prepared.manifest)
        self.assertNotIn('final_shortage', str(prepared.manifest))

    def test_final_data_and_global_source_identity_cannot_change_development_input(self):
        changed = deepcopy(self.dataset)
        changed.manifest.update(dataset_id='different-final', revision_ids_hash='f' * 64, revision_count=99999,
                                kinds=['future_only'], quality_summary={'final_failure': True})
        for row in changed.observations:
            if datetime.fromisoformat(row['available_at']) >= datetime.fromisoformat(self.at(22)):
                row['revision_id'] = 'changed-final-' + row['revision_id']
                row['payload'] = {'extreme_final': 10**30}
        changed.theme_snapshots[2]['document'] = {'extreme_final': -10**30}
        self.assertEqual(self.prepare(), self.prepare(changed))

    def test_mutating_source_does_not_mutate_prepared_input(self):
        prepared = self.prepare()
        original = deepcopy(prepared)
        self.dataset.observations[0]['payload'] = {'changed': True}
        self.dataset.manifest['research_session_profile']['profile'] = 'changed'
        self.dataset.theme_snapshots[0]['document']['changed'] = True
        self.assertEqual(original, prepared)

    def test_bar_with_old_or_future_interval_is_not_adopted_by_availability_only(self):
        changed = deepcopy(self.dataset)
        bars = [row for row in changed.observations if row['kind'] == 'minute_bar']
        bars[1]['payload'].update(bar_start=self.at(-5), bar_end=self.at(-4))
        bars[2]['payload'].update(bar_start=self.at(25), bar_end=self.at(26))
        ids = {row['revision_id'] for row in self.prepare(changed).observations}
        self.assertNotIn(bars[1]['revision_id'], ids)
        self.assertNotIn(bars[2]['revision_id'], ids)

    def test_cancelled_preparation_does_not_touch_source(self):
        original = deepcopy(self.dataset)
        with self.assertRaises(ResearchRunCancelled):
            self.prepare(checkpoint=lambda: (_ for _ in ()).throw(ResearchRunCancelled('stop')))
        self.assertEqual(original, self.dataset)

    def test_wrong_single_fold_policy_is_rejected_before_run_write(self):
        prepared = self.prepare()
        repo = ResearchRepository(self.root / 'guard.sqlite3')
        with patch.object(repo, 'start_run') as start:
            with self.assertRaisesRegex(ValueError, 'matching single-fold'):
                execute_research(prepared, repo, self.root / 'runs', _strategy(), _execution(), self.evaluation, session_profile=PROFILE)
        start.assert_not_called()

    def test_future_row_injection_is_rejected_before_run_write(self):
        prepared = self.prepare()
        injected = replace(prepared, observations=prepared.observations + (self.rows[-1],))
        repo = ResearchRepository(self.root / 'guard.sqlite3')
        with patch.object(repo, 'start_run') as start:
            with self.assertRaisesRegex(ValueError, 'outside its partition'):
                execute_research(injected, repo, self.root / 'runs', _strategy(), _execution(), self.partition.evaluation_for(self.evaluation), session_profile=PROFILE)
        start.assert_not_called()

    def test_no_orders_or_candidates_during_warmup(self):
        prepared = self.prepare()
        repo = ResearchRepository(self.root / 'warmup.sqlite3')
        events, candidates = [], []
        original_events, original_evaluations = repo.append_execution_events, repo.append_evaluations
        def record_events(values):
            events.extend(values)
            return original_events(values)
        def record_evaluations(values):
            candidates.extend(value.candidate_event for value in values if value.candidate_event is not None)
            return original_evaluations(values)
        with patch.object(repo, 'append_execution_events', side_effect=record_events), patch.object(repo, 'append_evaluations', side_effect=record_evaluations):
            execute_research(prepared, repo, self.root / 'runs', _strategy(), _execution(), self.partition.evaluation_for(self.evaluation), session_profile=PROFILE)
        self.assertTrue(candidates)
        active = datetime.fromisoformat(self.at(2))
        self.assertTrue(all(datetime.fromisoformat(value.available_at) > active for value in candidates))
        self.assertTrue(all(datetime.fromisoformat(value.occurred_at) >= active for value in events))

    def test_separate_partitions_start_with_fresh_cash_and_empty_positions(self):
        repo = ResearchRepository(self.root / 'separate.sqlite3')
        initial = []
        class RecordingEngine(PaperExecutionEngine):
            def __init__(engine, *args, **kwargs):
                super().__init__(*args, **kwargs)
                initial.append((deepcopy(engine.portfolio), deepcopy(engine.strategy_state)))
        results = []
        with patch('scripts.run_research.PaperExecutionEngine', RecordingEngine):
            for name in ('train', 'validation'):
                partition = replace(self.partition, fold_name=name)
                results.append(execute_research(self.prepare(partition=partition), repo, self.root / 'runs', _strategy(), _execution(), partition.evaluation_for(self.evaluation), session_profile=PROFILE))
        self.assertEqual(2, len(initial))
        self.assertEqual(initial[0], initial[1])
        self.assertEqual(_execution().initial_cash_won, initial[1][0].cash_won)
        self.assertIsNone(initial[1][0].position)
        self.assertIsNone(initial[1][0].pending_order)
        self.assertIsNotNone(json.loads(results[0].output_manifest.read_text())['final_portfolio']['position'])
        self.assertNotEqual(results[0].run_id, results[1].run_id)

    def test_final_canary_leaves_real_run_identity_results_and_quality_unchanged(self):
        changed = deepcopy(self.dataset)
        for row in changed.observations:
            if datetime.fromisoformat(row['available_at']) >= datetime.fromisoformat(self.at(22)):
                row['payload'] = {'final_canary': -10**30}
        changed.manifest.update(dataset_id='another-full-input', quality_summary={'final_missing': True})
        results, reports = [], []
        for suffix, dataset in [('original', self.dataset), ('changed', changed)]:
            repo = ResearchRepository(self.root / f'{suffix}.sqlite3')
            result = execute_research(self.prepare(dataset), repo, self.root / suffix, _strategy(), _execution(), self.partition.evaluation_for(self.evaluation), session_profile=PROFILE)
            results.append(result)
            reports.append(repo.load_research_report(result.run_id))
        self.assertEqual(results[0].run_id, results[1].run_id)
        self.assertEqual(results[0].logical_result_hash, results[1].logical_result_hash)
        self.assertEqual(reports[0], reports[1])
        self.assertEqual('PASS', reports[0]['data_quality']['status'])

    def test_earlier_training_values_do_not_change_validation_input_outside_warmup(self):
        changed = deepcopy(self.dataset)
        for row in changed.observations:
            if row['kind'] == 'minute_bar' and datetime.fromisoformat(row['available_at']) < datetime.fromisoformat(self.at(10)):
                row['payload'] = {'changed_training': True}
        validation = replace(self.partition, fold_name='validation')
        self.assertEqual(self.prepare(partition=validation), self.prepare(changed, validation))

    def test_missing_or_wrong_profile_is_rejected_before_run_write(self):
        prepared = self.prepare()
        repo = ResearchRepository(self.root / 'profile.sqlite3')
        for profile in (None, 'krx-full-day/2026-09-14/v1'):
            with patch.object(repo, 'start_run') as start:
                with self.assertRaises(ValueError):
                    execute_research(prepared, repo, self.root / 'runs', _strategy(), _execution(), self.partition.evaluation_for(self.evaluation), session_profile=profile)
            start.assert_not_called()

    def write_request(self, *, mode='single_run', name='train'):
        relative = child(self.root, self.day, list(self.rows))
        document = _request_document()
        document.update(mode=mode, dataset=relative, session_profile=PROFILE,
                        evaluation=self.evaluation.to_dict(), development_partition=replace(self.partition, fold_name=name).to_dict())
        document['execution']['cost_model'].update(valid_from='2026-09-14T00:00:00+09:00', valid_to='2026-09-15T00:00:00+09:00')
        path = self.root / 'request.json'
        path.write_text(json.dumps(document), encoding='utf-8')
        return path

    def test_process_request_runs_only_selected_development_window(self):
        request = load_research_process_request(self.write_request())
        result = execute_process_request(request)
        self.assertEqual('single_run', result['kind'])
        self.assertEqual(['train'], [fold['name'] for fold in result['report']['fold_reports']])
        manifest = json.loads(Path(result['manifest']).read_text())
        self.assertEqual('development_partition/v2', manifest['development_partition']['spec']['version'])

    def test_process_request_rejects_final_before_database_write(self):
        path = self.write_request(name='final')
        with self.assertRaisesRegex(ValueError, 'final access is disabled'):
            load_research_process_request(path)
        self.assertFalse((self.root / 'output' / 'research.sqlite3').exists())

    def test_validation_uses_last_known_universe_without_rewriting_its_timestamp(self):
        partition = replace(self.partition, fold_name='validation')
        prepared = self.prepare(partition=partition)
        universe = next(row for row in prepared.observations if row['kind'] == 'top20_membership')
        self.assertEqual(self.dataset.observations[0], universe)
        self.assertEqual(self.at(0), universe['available_at'])
        self.assertIsNotNone(development_partition_start(prepared, partition.evaluation_for(self.evaluation)))
        repo = ResearchRepository(self.root / 'validation-seed.sqlite3')
        result = execute_research(prepared, repo, self.root / 'runs', _strategy(), _execution(), partition.evaluation_for(self.evaluation), session_profile=PROFILE)
        self.assertEqual('PASS', repo.load_research_report(result.run_id)['data_quality']['status'])

    def test_old_universe_seed_does_not_conceal_missing_bar_warmup(self):
        partition = replace(self.partition, fold_name='validation')
        changed = replace(self.dataset, observations=tuple(row for row in self.dataset.observations
            if row['kind'] != 'minute_bar' or datetime.fromisoformat(row['payload']['bar_start']) >= datetime.fromisoformat(self.at(12))))
        prepared = self.prepare(changed, partition)
        repo = ResearchRepository(self.root / 'missing-warmup.sqlite3')
        result = execute_research(prepared, repo, self.root / 'runs', _strategy(), _execution(), partition.evaluation_for(self.evaluation), session_profile=PROFILE)
        self.assertIn('configured_warmup_not_covered', repo.load_research_report(result.run_id)['data_quality']['reasons'])

    def test_future_theme_injection_and_missing_tag_are_rejected(self):
        prepared = self.prepare()
        for invalid in (replace(prepared, theme_snapshots=prepared.theme_snapshots + (self.dataset.theme_snapshots[2],)),
                        replace(prepared, manifest={key: value for key, value in prepared.manifest.items() if key != 'development_partition'})):
            with self.assertRaises(ValueError):
                development_partition_start(invalid, self.partition.evaluation_for(self.evaluation))

    def test_boundary_open_position_is_censored_at_partition_end(self):
        repo = ResearchRepository(self.root / 'end.sqlite3')
        events, original = [], repo.append_execution_events
        def record(values):
            events.extend(values)
            return original(values)
        with patch.object(repo, 'append_execution_events', side_effect=record):
            execute_research(self.prepare(), repo, self.root / 'runs', _strategy(), _execution(), self.partition.evaluation_for(self.evaluation), session_profile=PROFILE)
        censored = [event for event in events if event.event_type == 'POSITION_CENSORED']
        self.assertTrue(censored)
        self.assertTrue(all(datetime.fromisoformat(event.occurred_at) == datetime.fromisoformat(self.at(10)) for event in censored))

    def test_cli_runs_selected_development_partition(self):
        path = self.write_request()
        document = json.loads(path.read_text())
        evaluation_path = self.root / 'evaluation.json'
        evaluation_path.write_text(json.dumps(self.evaluation.to_dict()))
        argv = ['run_research.py', '--dataset', str(self.root / document['dataset']), '--database', str(self.root / 'cli.sqlite3'),
                '--runs-dir', str(self.root / 'cli-runs'), '--session-profile', PROFILE,
                '--evaluation-spec', str(evaluation_path), '--development-partition', 'train']
        required_strategy = ('lookback_bars', 'buffer_bps', 'rank_top_k', 'rank_window_seconds', 'rank_max_gap_seconds',
                             'rank_min_residency_seconds', 'stop_loss_bps', 'target_bps', 'max_hold_minutes', 'quantity',
                             'capital_won', 'signal_valid_seconds', 'cooldown_seconds')
        for key in required_strategy:
            argv.extend(['--' + key.replace('_', '-'), str(document['strategy'][key])])
        argv.extend(['--initial-cash-won', '100000', '--cpu-duty-percent', '100'])
        for key in ('commission_bps', 'sell_tax_bps', 'slippage_bps'):
            argv.extend(['--' + key.replace('_', '-'), str(document['execution']['cost_model'][key])])
        for key in ('rate_basis', 'source', 'valid_from', 'valid_to'):
            argv.extend(['--cost-' + key.replace('_', '-'), document['execution']['cost_model'][key]])
        output = StringIO()
        with patch('sys.argv', argv), patch('sys.stdout', output):
            self.assertEqual(0, research_main())
        result = json.loads(output.getvalue())
        manifest = json.loads(Path(result['manifest']).read_text())
        self.assertEqual('train', manifest['development_partition']['spec']['fold_name'])

    def test_dates_inside_one_partition_keep_the_same_cash_and_position_engine(self):
        next_day = self.day + timedelta(days=1)
        next_base = self.base + timedelta(days=1)
        evaluation = replace(self.evaluation, folds=(
            ResearchFoldSpec('train', 'TRAIN', self.at(2), (next_base + timedelta(minutes=10)).isoformat()),
            ResearchFoldSpec('validation', 'VALIDATION', (next_base + timedelta(minutes=12)).isoformat(), (next_base + timedelta(minutes=20)).isoformat()),
            ResearchFoldSpec('final', 'OOS', (next_base + timedelta(minutes=22)).isoformat(), (next_base + timedelta(minutes=30)).isoformat()),
        ))
        dataset = replace(self.dataset, observations=tuple(rows_for(self.day, 10) + rows_for(next_day, 10)))
        prepared = prepare_development_partition(dataset, self.partition, evaluation)
        repo = ResearchRepository(self.root / 'two-days.sqlite3')
        strategy = replace(_strategy(), max_hold_minutes=2000)
        with patch('scripts.run_research.PaperExecutionEngine', wraps=PaperExecutionEngine) as engine:
            result = execute_research(prepared, repo, self.root / 'runs', strategy, _execution(), self.partition.evaluation_for(evaluation), session_profile=PROFILE)
        self.assertEqual(1, engine.call_count)
        self.assertEqual({self.day, next_day}, {datetime.fromisoformat(row['available_at']).astimezone(KST).date() for row in prepared.observations})
        self.assertIsNotNone(json.loads(result.output_manifest.read_text())['final_portfolio']['position'])

    def test_cancel_during_partition_validation_prevents_any_run_write(self):
        prepared = self.prepare()
        repo = ResearchRepository(self.root / 'cancel.sqlite3')
        calls = []
        def cancelled():
            calls.append(True)
            return len(calls) >= 3
        with patch.object(repo, 'start_run') as start:
            with self.assertRaises(ResearchRunCancelled):
                execute_research(prepared, repo, self.root / 'runs', _strategy(), _execution(),
                                 self.partition.evaluation_for(self.evaluation), cancel_requested=cancelled, session_profile=PROFILE)
        start.assert_not_called()

    def test_universe_at_warmup_start_does_not_prove_minute_bar_warmup(self):
        changed = replace(self.dataset, observations=tuple(row for row in self.dataset.observations
            if row['kind'] != 'minute_bar' or datetime.fromisoformat(row['payload']['bar_start']) >= datetime.fromisoformat(self.at(2))))
        repo = ResearchRepository(self.root / 'universe-only-warmup.sqlite3')
        result = execute_research(self.prepare(changed), repo, self.root / 'runs', _strategy(), _execution(),
                                 self.partition.evaluation_for(self.evaluation), session_profile=PROFILE)
        self.assertIn('configured_warmup_not_covered', repo.load_research_report(result.run_id)['data_quality']['reasons'])

    def test_late_warmup_backfill_does_not_prove_warmup_available_at_partition_start(self):
        changed = deepcopy(self.dataset)
        for row in changed.observations:
            if row['kind'] == 'minute_bar' and datetime.fromisoformat(row['payload']['bar_start']) < datetime.fromisoformat(self.at(2)):
                row['available_at'] = self.at(3)
        repo = ResearchRepository(self.root / 'late-warmup.sqlite3')
        result = execute_research(self.prepare(changed), repo, self.root / 'runs', _strategy(), _execution(),
                                 self.partition.evaluation_for(self.evaluation), session_profile=PROFILE)
        self.assertIn('configured_warmup_not_covered', repo.load_research_report(result.run_id)['data_quality']['reasons'])

    def test_verified_export_and_json_pipeline_final_canary_are_independent(self):
        results = []
        for suffix in ('original', 'changed'):
            root = self.root / suffix
            rows = deepcopy(list(self.rows))
            if suffix == 'changed':
                for row in rows:
                    if datetime.fromisoformat(row['available_at']) >= datetime.fromisoformat(self.at(22)):
                        row.update(revision_id='changed-final-' + row['revision_id'], payload={'final_canary': -10**30})
            relative = child(root, self.day, rows)
            input_path = root / relative
            manifest = json.loads((input_path / 'manifest.json').read_text())
            manifest.update(dataset_id='full-' + suffix, quality_summary={'global_final': suffix})
            (input_path / 'manifest.json').write_text(json.dumps(manifest))
            before = {path.name: path.read_bytes() for path in input_path.iterdir() if path.is_file()}
            document = _request_document()
            document.update(mode='single_run', dataset=relative, session_profile=PROFILE,
                            evaluation=self.evaluation.to_dict(), development_partition=self.partition.to_dict())
            document['execution']['cost_model'].update(valid_from='2026-09-14T00:00:00+09:00', valid_to='2026-09-15T00:00:00+09:00')
            path = root / 'request.json'
            path.write_text(json.dumps(document))
            results.append(execute_process_request(load_research_process_request(path)))
            self.assertEqual(before, {path.name: path.read_bytes() for path in input_path.iterdir() if path.is_file()})
        self.assertEqual(results[0]['run_id'], results[1]['run_id'])
        self.assertEqual(results[0]['report'], results[1]['report'])

    def test_filtering_never_bypasses_corrupt_source_file_hash(self):
        request = load_research_process_request(self.write_request())
        path = request.dataset / 'observations.jsonl'
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'hash'):
            execute_process_request(request)
        self.assertFalse(request.database.exists())
