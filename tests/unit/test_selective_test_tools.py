from __future__ import annotations

import copy
import gzip
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts import check_test_changes as review, run_regression as runner, select_ci_groups as selector


class SelectiveTestToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {
            'src/sample/value.py': 'VALUE = 1\n',
            'src/sample/reader.py': 'from .value import VALUE\n',
            'tests/unit/test_direct.py': 'from sample.value import VALUE\n',
            'tests/unit/test_indirect.py': 'from sample.reader import VALUE\n',
            'tests/unit/test_other.py': 'import unittest\n',
            'tests/unit/test_linux.py': 'from sample.value import VALUE\n',
        }
        for name, text in self.files.items():
            self.write(name, text)
        self.manifest = {'core_batches': [], 'profiles': {
            'sample': ['tests.unit.test_direct', 'tests.unit.test_indirect'],
            'other': ['tests.unit.test_other']}}
        self.write('tests/regression_profiles.json', json.dumps(self.manifest))
        self.write('tests/ci_groups.json', json.dumps({'linux_required': ['tests.unit.test_linux']}))

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode('utf-8'))

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE).decode('utf-8').strip()

    def initialize_git(self):
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')

    def test_git_diff_includes_staged_unstaged_untracked_deleted_and_old_expectations(self):
        self.write('tests/unit/test_cases.py', 'class Case:\n def test_old(self): assert 1 == 1\n def test_gone(self): assert 2 == 2\n')
        self.initialize_git()
        self.write('tests/unit/test_cases.py', 'class Case:\n def test_old(self): assert 1 == 2\n def test_new(self): assert 3 == 3\n')
        self.git('add', 'tests/unit/test_cases.py')
        self.write('src/sample/value.py', 'VALUE = 2\n')
        self.write('tests/unit/test_new.py', 'class New:\n def test_added(self): assert True\n')
        (self.root / 'src/sample/reader.py').unlink()
        with patch.object(runner, '_code_tree_identity', return_value={'lf_sha256': 'source'}):
            query = review.query_changes(self.root, 'HEAD', [])
        self.assertEqual(['tests.unit.test_cases.Case.test_old'], query['changed_tests']['modified'])
        self.assertEqual(['tests.unit.test_cases.Case.test_gone'], query['changed_tests']['deleted'])
        self.assertEqual(2, len(query['changed_tests']['added']))
        self.assertIn('src/sample/value.py', query['changed_paths'])
        self.assertIn('src/sample/reader.py', query['changed_paths'])
        with self.assertRaisesRegex(ValueError, 'No changed files'):
            review.changed_sources(self.root, 'HEAD', ['src/unchanged.py'])
        with self.assertRaises(subprocess.CalledProcessError):
            review.changed_sources(self.root, 'invalid-base', [])

    def test_direct_indirect_and_linux_consumers_have_explained_paths(self):
        result = selector.test_impact(['src/sample/value.py'], self.root)
        self.assertEqual(['tests.unit.test_direct', 'tests.unit.test_indirect', 'tests.unit.test_linux'], result['selected_modules'])
        self.assertIn('sample.reader', result['reasons']['tests.unit.test_indirect'][0])
        self.assertEqual(['linux_required'], result['external_modules']['tests.unit.test_linux'])
        self.assertEqual('selected', result['status'])

    def test_shared_fixture_and_package_initialization_reach_consumers(self):
        self.write('tests/helpers.py', 'EXPECTED = 3\n')
        self.write('tests/unit/test_direct.py', 'from tests.helpers import EXPECTED\n')
        result = selector.test_impact(['tests/helpers.py'], self.root)
        self.assertEqual(['tests.unit.test_direct'], result['selected_modules'])
        package = selector.test_impact(['tests/unit/__init__.py'], self.root)
        self.assertEqual('full_required', package['status'])
        self.assertEqual(4, len(package['selected_modules']))

    def test_removed_import_edges_are_preserved_for_selection(self):
        old = {'src/sample/reader.py': self.files['src/sample/reader.py']}
        self.write('src/sample/reader.py', 'VALUE = 9\n')
        result = selector.test_impact(['src/sample/value.py'], self.root, old)
        self.assertIn('tests.unit.test_indirect', result['selected_modules'])

    def test_unknown_dynamic_import_expands_existing_profile_without_unrelated_profile(self):
        self.write('tests/unit/test_direct.py', 'from sample.value import VALUE\nimport importlib\nimportlib.import_module(VALUE)\n')
        result = selector.test_impact(['tests/unit/test_direct.py'], self.root)
        self.assertEqual(['tests.unit.test_direct', 'tests.unit.test_indirect'], result['selected_modules'])
        self.assertEqual('expanded', result['status'])
        self.assertEqual('unresolved_dynamic_dependency', result['uncertainties'][0]['reason'])
        self.write('tests/unit/test_other.py', 'import importlib\nimportlib.import_module(variable_name)\n')
        changed_product = selector.test_impact(['src/sample/value.py'], self.root)
        self.assertIn('tests.unit.test_other', changed_product['selected_modules'])
        self.assertEqual('full_required', changed_product['status'])

    def test_literal_dynamic_import_is_a_dependency_not_a_silent_omission(self):
        self.write('scripts/registry.py', "MODULES = ['tests.unit.test_other']\n")
        self.write('tests/unit/test_registry.py', 'from scripts.registry import MODULES\n')
        self.manifest['profiles']['other'].append('tests.unit.test_registry')
        self.write('tests/regression_profiles.json', json.dumps(self.manifest))
        self.write('tests/unit/test_other.py', "import importlib\nimportlib.import_module('sample.value')\n")
        result = selector.test_impact(['src/sample/value.py'], self.root)
        self.assertIn('tests.unit.test_other', result['selected_modules'])
        self.assertFalse(result['uncertainties'])
        self.assertNotIn('tests.unit.test_registry', result['selected_modules'])

    def test_unknown_configuration_or_unmapped_code_requires_broad_scope(self):
        for path in ['settings.json', 'API_CONTRACT.md', 'src/sample/unmapped.py']:
            with self.subTest(path=path):
                result = selector.test_impact([path], self.root)
                self.assertEqual('full_required', result['status'])
                self.assertTrue(result['uncertainties'])
                self.assertEqual(4, len(result['selected_modules']))

    def test_unregistered_new_module_is_selection_failure(self):
        self.write('tests/unit/test_unregistered.py', 'import unittest\n')
        result = selector.test_impact(['tests/unit/test_unregistered.py'], self.root)
        self.assertEqual('failed', result['status'])
        self.assertEqual(['tests.unit.test_unregistered'], result['unregistered_modules'])

    def test_relevant_audit_only_and_rename_lineage_preserve_original_bytes(self):
        identity = 'tests.unit.test_direct.Case.test_old'
        rows = [dict(case_id=identity, baseline_id=identity, decision='unresolved', gap='old gap'),
                dict(case_id='tests.unit.test_other.Case.test_other', baseline_id='other', decision='strengthen', gap='unrelated'),
                dict(case_id='tests.unit.test_other.Case.test_bug', baseline_id='bug', decision='strengthen',
                     production_evidence=[{'file': 'src/sample/value.py', 'symbol': 'compute', 'line': 1}], gap='related defect')]
        archive = gzip.compress(('\n'.join(json.dumps(row) for row in rows) + '\n').encode())
        self.write(f'{review.AUDIT}/cases.jsonl.gz', archive)
        self.write(f'{review.AUDIT}/findings.json.gz', gzip.compress(b'[]'))
        self.write(f'{review.AUDIT}/followup-cases.json.gz', gzip.compress(json.dumps([
            {'baseline_id': identity, 'case_id': identity, 'status': 'implemented', 'current_related_execution': 'passed'}]).encode()))
        self.write(review.LEDGER, json.dumps({'renames': {identity: 'tests.unit.test_new.Case.test_new'}, 'findings': []}) + '\n')
        result = review.related_audit(self.root, ['src/sample/value.py'], [], {'added': ['tests.unit.test_new.Case.test_new']})
        self.assertEqual({'old gap', 'related defect'}, {row['original']['gap'] for row in result})
        self.assertEqual('implemented', next(row for row in result if row['original']['gap'] == 'old gap')['followup']['status'])
        self.assertEqual(archive, (self.root / review.AUDIT / 'cases.jsonl.gz').read_bytes())
        with patch.object(review.gzip, 'open', side_effect=AssertionError('warm query reopens whole archive')), \
                patch.object(review, 'read_json', side_effect=AssertionError('warm query reloads whole archive')):
            cached = review.related_audit(self.root, ['src/sample/value.py'], [],
                                          {'added': ['tests.unit.test_new.Case.test_new']})
        self.assertEqual(result, cached)
        rows.append(dict(case_id='new-gap', baseline_id='new-gap', decision='strengthen',
                         production_evidence=['src/sample/value.py:compute:2'], gap='new source record'))
        self.write(f'{review.AUDIT}/cases.jsonl.gz', gzip.compress(('\n'.join(json.dumps(row) for row in rows) + '\n').encode()))
        refreshed = review.related_audit(self.root, ['src/sample/value.py'], [], {})
        self.assertIn('new source record', {row['original']['gap'] for row in refreshed})

    def test_query_does_not_load_whole_audit_state_and_console_is_paginated(self):
        self.initialize_git()
        self.write('src/sample/value.py', 'VALUE = 2\n')
        with patch.object(review, 'load_state', side_effect=AssertionError('must not load baseline state')), \
                patch.object(runner, '_code_tree_identity', return_value={'lf_sha256': 'source'}):
            result = review.query_changes(self.root, 'HEAD', ['src/sample/value.py'])
        short = review.query_summary(result, limit=1)
        self.assertEqual(1, len(short['details']))
        self.assertEqual(2, short['more_details'])
        self.assertEqual(['src/sample/value.py'], short['scope_filters'])
        self.assertNotIn('audit_finding_keys', short)
        result['uncertainties'] = [{'reason': 'unknown', 'expansion': list('abcdef')}]
        result['reasons']['tests.unit.test_direct'] = ['reason'] * 9
        preview = review.query_summary(result, limit=10)
        self.assertEqual(3, preview['details'][0]['uncertainty']['more_expansion'])
        row = next(row for row in preview['details'] if row.get('module') == 'tests.unit.test_direct')
        self.assertEqual(2, len(row['reasons']))
        self.assertEqual(7, row['more_reasons'])

    def selection(self):
        paths = ['tests/unit/test_direct.py']
        impact = selector.test_impact(paths, self.root)
        return {'kind': 'incremental_test_selection', 'version': 1, 'source': {'lf_sha256': 'same'},
                'changed_paths': paths, 'base_ref': 'HEAD', 'scope_filters': paths, **impact}

    def test_runner_rechecks_plan_and_rejects_stale_edited_external_or_broad_selection(self):
        selection = self.selection()
        with patch.object(runner, 'ROOT', self.root), patch.object(runner, '_code_tree_identity', return_value={'lf_sha256': 'same'}), \
                patch.object(review, 'changed_sources', return_value=(selection['changed_paths'], {})):
            batches = runner._selection_batches(selection, self.manifest)
            self.assertEqual(['tests.unit.test_direct'], batches[0]['modules'])
            for kind in ['stale', 'edited', 'broad', 'external']:
                value = copy.deepcopy(selection)
                if kind == 'stale':
                    value['source']['lf_sha256'] = 'old'
                elif kind == 'edited':
                    value['selected_modules'] = []
                elif kind == 'broad':
                    value['changed_paths'] = ['settings.json']
                    value.update(selector.test_impact(value['changed_paths'], self.root))
                else:
                    value['changed_paths'] = ['tests/unit/test_linux.py']
                    value.update(selector.test_impact(value['changed_paths'], self.root))
                with self.subTest(kind=kind), patch.object(review, 'changed_sources', return_value=(value['changed_paths'], {})):
                    with self.assertRaises(ValueError):
                        runner._selection_batches(value, self.manifest)

    def passed_report(self):
        return {'status': 'passed', 'code_tree_start': {'lf_sha256': 'same'}, 'code_tree_end': {'lf_sha256': 'same'},
                'batch_results': [{'status': 'passed', 'test_count': 1, 'planned_test_ids': ['case'],
                    'executed_test_ids': ['case'], 'passed_test_ids': ['case'], 'duration_seconds': 1.25,
                    'worker_exit_confirmed': True, 'process_tree_exit_confirmed': True}]}

    def test_success_summary_is_small_and_never_reads_success_log(self):
        report = self.passed_report()
        report['batch_results'][0]['log'] = 'do-not-open-success.log'
        with patch.object(Path, 'open', side_effect=AssertionError('success log read')):
            result = runner.summarize_run(report)
        self.assertEqual({'status': 'passed', 'executed': 1, 'passed': 1, 'seconds': 1.25, 'source': 'same'}, result)

    def test_failure_summary_includes_only_failed_test_details_and_log_reference(self):
        for kind, detail in [('failures', 'AssertionError: 1 != 2'), ('errors', 'ValueError: invalid input')]:
            report = self.passed_report()
            worker = report['batch_results'][0]
            worker.update(status='failed', passed_test_ids=[], log='failed.log')
            worker[kind] = [{'test': 'case', 'detail': detail}]
            result = runner.summarize_run(report)
            self.assertEqual(0, result['passed'])
            self.assertEqual('case', result['problems'][0]['test'])
            self.assertEqual(detail, result['problems'][0]['detail'])
            self.assertEqual('failed.log', result['problems'][0]['log'])

    def test_skip_expected_failure_timeout_unrun_missing_ids_are_not_green(self):
        for kind in ['skipped', 'expected_failures', 'timeout', 'unrun', 'missing', 'empty', 'source', 'duplicate']:
            report = self.passed_report()
            worker = report['batch_results'][0]
            if kind in {'skipped', 'expected_failures'}:
                worker[kind] = ['case']
            elif kind == 'timeout':
                worker['timeout'] = True
            elif kind == 'unrun':
                report['unrun_batches'] = ['not-run']
            elif kind == 'missing':
                worker['executed_test_ids'] = []
            elif kind == 'empty':
                report['batch_results'] = []
            elif kind == 'duplicate':
                report['batch_results'] *= 2
            else:
                report['code_tree_end']['lf_sha256'] = 'changed'
            with self.subTest(kind=kind):
                self.assertNotEqual('passed', runner.summarize_run(report)['status'])

    def test_real_worker_outcomes_keep_full_logs_and_exact_results(self):
        for mode, expected in [('success', 'passed'), ('fail', 'failed'), ('skip', 'incomplete'),
                               ('expected-failure', 'incomplete'), ('timeout', 'incomplete')]:
            directory = self.root / mode
            directory.mkdir()
            with self.subTest(mode=mode), patch.dict(os.environ, {'REGRESSION_FIXTURE_MODE': mode,
                            'REGRESSION_FIXTURE_DIR': str(directory)}):
                worker = runner._run_process(modules=['tests.unit.regression_worker_fixtures'],
                    result_path=directory / 'result.json', log_path=directory / 'worker.log',
                    timeout_seconds=1 if mode == 'timeout' else 8)
                report = self.passed_report()
                report.update(status=worker['status'], batch_results=[worker])
                result = runner.summarize_run(report)
                self.assertEqual(expected, result['status'])
                self.assertEqual(1 if mode == 'success' else 0, result['passed'])
                self.assertTrue((directory / 'worker.log').stat().st_size)
                self.assertTrue(worker['process_tree_exit_confirmed'])

    def test_selection_cli_failure_never_runs_a_worker(self):
        self.write('selection.json', json.dumps({'kind': 'invalid'}))
        with patch.object(runner, '_run_process', side_effect=AssertionError('must not execute')), redirect_stdout(StringIO()) as output:
            result = runner.main(['--selection', str(self.root / 'selection.json'), '--output', str(self.root / 'run')])
        self.assertEqual(2, result)
        self.assertEqual('failed', json.loads(output.getvalue())['status'])

    def test_selected_module_missing_from_actual_discovery_cannot_pass(self):
        report = self.passed_report()
        worker = report['batch_results'][0]
        worker.update(module_test_counts=[{'module': 'tests.unit.wrong', 'test_count': 1}])
        selection = self.selection()
        # Keep source/process controls real in _execute_profile; only simulate the
        # worker's wrong discovery response at the process boundary.
        with patch.object(runner, '_selection_batches', return_value=[{
                'name': 'sample', 'modules': selection['selected_modules'], 'isolate_modules': True}]), \
                patch.object(runner, 'ROOT', self.root), \
                patch.object(runner, '_run_process', return_value=worker), \
                patch.object(runner, '_code_tree_identity', return_value={'lf_sha256': 'same'}), \
                redirect_stdout(StringIO()) as output:
            status = runner._execute_profile('selected', 5, self.root / 'wrong-discovery', selection=selection, compact=True)
        saved = json.loads((self.root / 'wrong-discovery/run.json').read_text(encoding='utf-8'))
        self.assertEqual(1, status)
        self.assertFalse(saved['selection_verified'])
        self.assertEqual('incomplete', json.loads(output.getvalue())['status'])


if __name__ == '__main__':
    unittest.main()
