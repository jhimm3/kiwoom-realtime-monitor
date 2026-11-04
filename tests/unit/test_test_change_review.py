from __future__ import annotations

import copy
import gzip
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_test_changes as review, run_regression


SOURCE = '''import unittest
from kiwoom_monitor.sample import compute
EXPECTED = 3
class Sample(unittest.TestCase):
    def test_value(self):
        self.assertEqual(EXPECTED, compute(1, 2))
    def test_failure(self):
        with self.assertRaises(ValueError): compute(None, 2)
'''
CASE = 'tests.unit.test_sample.Sample.test_value'


class TestChangeReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write('tests/unit/test_sample.py', SOURCE)
        self.write('src/kiwoom_monitor/sample.py', 'def compute(a, b): return a + b\n')
        self.write('scripts/placeholder.py', 'VALUE = 1\n')
        self.write(review.LEDGER, '')
        for name in ('cases.jsonl.gz', 'findings.json.gz', 'followup-cases.json.gz'):
            value = [{'owner': 'root', 'id': 'old-gap', 'status': 'open'}] if name == 'findings.json.gz' else []
            self.write(f'{review.AUDIT}/{name}', gzip.compress(json.dumps(value).encode()))
        for name, value in [('unresolved.json', [{'baseline_id': CASE}]), ('retired.json', []),
                            ('followup-verification.json', {'related_unique_passed': 1879}),
                            ('summary.json', {'decisions': {'retain': 5208, 'strengthen': 216,
                             'fix_expectation': 19, 'retire': 12, 'unresolved': 49}})]:
            self.write(f'{review.AUDIT}/{name}', json.dumps(value, indent=2))
        self.seed = {'version': 1, 'audit_sources': review.audit_sources(self.root),
                     'inventory': review.inventory(self.root), 'adoption': 'mechanical only'}
        self.write(review.BASELINE, gzip.compress(json.dumps(self.seed).encode()))
        self.catalog = patch.object(run_regression, '_catalog_errors', return_value=[])
        self.catalog.start()
        self.addCleanup(self.catalog.stop)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode())

    def draft(self):
        value = review.make_plan(self.root)
        value['id'] = 'fixture-review'
        for kind in ('tests', 'support', 'products'):
            for item in value[kind].values():
                item.update({field: 'Fixture: current addition contract, literal expected 3, invalid None.'
                             for field in review.REVIEW_FIELDS})
                if kind == 'products':
                    item['tests'] = [CASE]
                elif kind == 'support':
                    item['tests'] = [CASE]
        current = review.inventory(self.root)
        for module in value['module_counts']:
            value['module_counts'][module] = sum(identity.rsplit('.', 2)[0] == module for identity in current['tests'])
        return value

    def append(self, value):
        with (self.root / review.LEDGER).open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(value) + '\n')

    def test_static_inventory_does_not_import_test_or_product_and_ignores_formatting(self):
        before = review.inventory(self.root)
        self.write('tests/unit/test_sample.py', SOURCE + '\n# no runtime effect\n')
        self.assertEqual(before, review.inventory(self.root))
        self.write('tests/unit/test_sample.py', 'raise RuntimeError("must never import")\n' + SOURCE)
        self.assertEqual(2, len(review.inventory(self.root)['tests']))
        self.assertNotEqual(before['support'], review.inventory(self.root)['support'])

    def test_new_case_is_rejected_then_reviewed_without_resetting_audit(self):
        self.write('tests/unit/test_sample.py', SOURCE + '    def test_zero(self): self.assertEqual(0, compute(0, 0))\n')
        with self.assertRaisesRegex(ValueError, 'Unreviewed'):
            review.check(self.root)
        record = self.draft()
        self.assertEqual({'before': 2, 'after': 3, 'added': 1, 'deleted': 0, 'modified': 0}, record['counts'])
        self.append(record)
        self.assertEqual(3, review.check(self.root)['static_case_definitions'])
        self.assertEqual({}, review.check(self.root)['finding_updates'])

    def test_packaging_spec_change_requires_explicit_config_review(self):
        spec = 'KiwoomMonitor.spec'
        source = b"raise RuntimeError('must never execute')\n"
        baseline = (self.root / review.BASELINE).read_bytes()
        audit = review.audit_sources(self.root)
        self.write(spec, source)
        self.write('Unrelated.spec', b'unrelated = 1\n')
        products = review.inventory(self.root)['products']
        self.assertIn(spec, products)
        self.assertNotIn('Unrelated.spec', products)
        with self.assertRaisesRegex(ValueError, r'Unreviewed changes.*KiwoomMonitor\.spec'):
            review.check(self.root)
        record = self.draft()
        self.assertEqual({spec}, set(record['products']))
        self.append(record)
        self.assertEqual(2, review.check(self.root)['static_case_definitions'])
        self.write(spec, source.replace(b'\n', b'\r\n'))
        self.assertEqual(products[spec], review.inventory(self.root)['products'][spec])
        self.assertEqual(2, review.check(self.root)['static_case_definitions'])
        self.write(spec, b"raise RuntimeError('changed packaging input')\r\n")
        with self.assertRaisesRegex(ValueError, r'Unreviewed changes.*KiwoomMonitor\.spec'):
            review.check(self.root)
        self.assertEqual(baseline, (self.root / review.BASELINE).read_bytes())
        self.assertEqual(audit, review.audit_sources(self.root))

    def test_expectation_change_requires_independent_review_fields(self):
        self.write('tests/unit/test_sample.py', SOURCE.replace('compute(1, 2)', 'compute(1, 9)'))
        with self.assertRaisesRegex(ValueError, 'Unreviewed'):
            review.check(self.root)
        record = self.draft()
        record['tests'][CASE]['oracle'] = ''
        self.append(record)
        with self.assertRaisesRegex(ValueError, 'Unreviewed tests'):
            review.check(self.root)

    def test_fixture_change_is_recorded_even_when_case_bodies_are_unchanged(self):
        self.write('tests/unit/test_sample.py', SOURCE.replace('EXPECTED = 3', 'EXPECTED = 12'))
        record = self.draft()
        self.assertEqual({}, record['tests'])
        self.assertEqual(['tests/unit/test_sample.py'], list(record['support']))
        self.append(record)
        self.assertEqual('passed', review.check(self.root)['status'])

    def test_reviewed_deletion_keeps_purpose_and_does_not_close_findings(self):
        self.write('tests/unit/test_sample.py', SOURCE[:SOURCE.index('    def test_failure')])
        with self.assertRaisesRegex(ValueError, 'Unreviewed'):
            review.check(self.root)
        record = self.draft()
        record['tests']['tests.unit.test_sample.Sample.test_failure']['reason'] = 'Feature removed; compatible legacy comparison remains in separate suite.'
        self.append(record)
        self.assertEqual(1, review.check(self.root)['static_case_definitions'])
        self.assertEqual({}, review.check(self.root)['finding_updates'])

    def test_count_change_cannot_be_hidden_in_review(self):
        self.write('tests/unit/test_sample.py', SOURCE[:SOURCE.index('    def test_failure')])
        record = self.draft()
        record['counts']['deleted'] = 0
        self.append(record)
        with self.assertRaisesRegex(ValueError, 'Unexpected test count'):
            review.check(self.root)

    def test_rename_and_move_require_explicit_lineage(self):
        for move in (False, True):
            with self.subTest(move=move):
                (self.root / review.LEDGER).write_text('')
                self.write('tests/unit/test_sample.py', SOURCE.replace('test_value', 'test_sum'))
                record = self.draft()
                if move:
                    (self.root / 'tests/unit/test_sample.py').rename(self.root / 'tests/unit/test_moved.py')
                    record = self.draft()
                with self.assertRaisesRegex(ValueError, 'Unlinked test ID'):
                    self.append(record)
                    review.check(self.root)
                (self.root / review.LEDGER).write_text('')
                record['renames'] = {old: next(new for new, item in record['tests'].items()
                                              if item['before'] is None and item['after']['identity_free_body'] == row['before']['identity_free_body'])
                                     for old, row in record['tests'].items() if row['after'] is None}
                self.append(record)
                self.assertEqual('passed', review.check(self.root)['status'])
                if move:
                    (self.root / 'tests/unit/test_moved.py').unlink()

    def test_execution_follows_reviewed_rename_until_unlinked_retirement(self):
        module = 'tests.unit.test_sample'
        fixture = 'tests/unit/test_sample.py'
        product = 'src/kiwoom_monitor/sample.py'
        source_text = SOURCE.replace('EXPECTED = 3', 'EXPECTED = 4')
        self.write(fixture, source_text)
        self.write(product, 'def compute(a,b): return a-b\n')
        self.append(self.draft())

        previous = CASE
        for name in ('test_sum', 'test_total'):
            self.write(fixture, source_text.replace('test_value', name))
            record = self.draft()
            identity = module + '.Sample.' + name
            record.update(id=name, renames={previous: identity})
            self.append(record)
            previous = identity

        def checked(identity, changed, registered_modules=()):
            source = {'lf_sha256': 'current'}
            ids = [identity, module + '.Sample.test_failure']
            self.write('run.json', json.dumps({
                'status': 'passed', 'code_tree_start': source, 'code_tree_end': source,
                'batch_results': [{'status': 'passed', 'worker_exit_confirmed': True,
                    'process_tree_exit_confirmed': True, 'test_count': 2,
                    'module_test_counts': [{'module': module, 'test_count': 2}],
                    'planned_test_ids': ids, 'executed_test_ids': ids}],
            }))
            with patch.object(run_regression, '_code_tree_identity', return_value=source), \
                    patch.object(run_regression, '_test_modules_under', side_effect=[set(registered_modules), set()]), \
                    patch.object(review, 'validate_history', return_value='base'), \
                    patch.object(review.subprocess, 'check_output', side_effect=[changed, '']), \
                    patch.object(review.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'')):
                return review.check(self.root, executions=[self.root / 'run.json'], base_ref='base')

        changed = product + '\n' + fixture
        self.assertEqual([previous], checked(previous, changed)['related_product_tests'][product])
        with self.assertRaisesRegex(ValueError, 'Related tests not executed'):
            checked(CASE, changed)
        with self.assertRaisesRegex(ValueError, 'Related tests not executed'):
            checked(previous, changed, [CASE])

        # Reusing a former destination ID must not reconnect the retired lineage.
        self.write(fixture, source_text[:source_text.index('    def test_value')]
                   + source_text[source_text.index('    def test_failure'):])
        record = self.draft()
        record['id'] = 'retirement'
        self.append(record)
        self.write(fixture, source_text.replace('test_value', 'test_total'))
        record = self.draft()
        record['id'] = 'reused-name'
        self.append(record)
        with self.assertRaisesRegex(ValueError, 'Related tests not executed'):
            checked(previous, changed)
        with self.assertRaisesRegex(ValueError, 'Related tests not executed'):
            checked(CASE, changed)
        with self.assertRaisesRegex(ValueError, 'Fixture change has no executed consumers'):
            checked(previous, fixture)

    def test_skip_expected_failure_and_existing_decorator_edits_are_visible(self):
        for decorator, stored in (("unittest.skip('hidden')", "unittest.skip('hidden')"),
                                  ('unittest.expectedFailure', 'unittest.expectedFailure'),
                                  ("unittest.skipIf(True, 'hidden')", "unittest.skipIf(True, 'hidden')")):
            with self.subTest(decorator=decorator):
                self.write('tests/unit/test_sample.py', SOURCE.replace('    def test_value', f'    @{decorator}\n    def test_value'))
                change = review.make_plan(self.root)['tests'][CASE]
                self.assertEqual([stored], change['after']['decorators'])
                with self.assertRaisesRegex(ValueError, 'Unreviewed'):
                    review.check(self.root)

    def test_duplicate_method_definition_cannot_be_masked_by_python_override(self):
        self.write('tests/unit/test_sample.py', SOURCE + '    def test_value(self): pass\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate test definitions'):
            review.make_plan(self.root)

    def test_audit_and_resolved_issue_are_not_reset_by_new_test(self):
        record = self.draft()
        record['findings'] = [{'id': 'audit:root:old-gap', 'status': 'resolved', 'reason': 'new test', 'evidence': 'test name'}]
        self.append(record)
        with self.assertRaisesRegex(ValueError, 'New test alone'):
            review.check(self.root)

    def test_existing_finding_is_reused_and_explicit_fix_evidence_is_required(self):
        record = self.draft()
        record['findings'] = [{'id': 'audit:root:old-gap', 'status': 'open', 'new': True,
                               'reason': 'duplicate', 'evidence': 'same observed failure'}]
        self.append(record)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            review.check(self.root)

    def test_original_audit_content_is_immutable_across_git_line_endings(self):
        path = self.root / review.AUDIT / 'unresolved.json'
        path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        self.assertEqual('passed', review.check(self.root)['status'])
        self.write(f'{review.AUDIT}/unresolved.json', '[]')
        with self.assertRaisesRegex(ValueError, 'Original audit'):
            review.check(self.root)

    def test_stale_review_body_is_not_reusable_for_later_edit(self):
        self.write('tests/unit/test_sample.py', SOURCE.replace('compute(1, 2)', 'compute(1, 8)'))
        self.append(self.draft())
        self.write('tests/unit/test_sample.py', SOURCE.replace('compute(1, 2)', 'compute(1, 9)'))
        with self.assertRaisesRegex(ValueError, 'Unreviewed'):
            review.check(self.root)

    def test_product_change_requires_related_selection_even_without_test_changes(self):
        for deletion in (False, True):
            with self.subTest(deletion=deletion):
                (self.root / review.LEDGER).write_text('')
                if deletion:
                    (self.root / 'src/kiwoom_monitor/sample.py').unlink()
                else:
                    self.write('src/kiwoom_monitor/sample.py', 'def compute(a, b): return a - b\n')
                record = self.draft()
                self.assertEqual({}, record['tests'])
                record['products']['src/kiwoom_monitor/sample.py']['tests'] = []
                self.append(record)
                with self.assertRaisesRegex(ValueError, 'no related test'):
                    review.check(self.root)

    def test_platform_report_preserves_actual_ids_and_rejects_count_drift(self):
        report = {'status': 'passed', 'planned': 1, 'ran': 1,
                  'failures': 0, 'errors': 0, 'skipped': 0, 'expected_failures': 0, 'unexpected_successes': 0,
                  'source_start': {'lf_sha256': 'current'}, 'source_end': {'lf_sha256': 'current'},
                  'planned_test_ids': [CASE], 'executed_test_ids': [CASE]}
        path = self.root / 'platform.json'
        with patch.object(run_regression, '_code_tree_identity', return_value={'lf_sha256': 'current'}):
            self.write('platform.json', json.dumps(report))
            self.assertEqual({CASE}, review.execution_ids(self.root, [path]))
            report['planned'] = 2
            self.write('platform.json', json.dumps(report))
            with self.assertRaisesRegex(ValueError, 'Platform discovery'):
                review.execution_ids(self.root, [path])

    def test_baseline_reset_and_rewritten_history_are_rejected(self):
        for target in (review.BASELINE, review.LEDGER):
            def git_result(command, **kwargs):
                data = b'old snapshot' if target == review.BASELINE else b'{"id":"earlier"}\n'
                if command[-1].endswith(target):
                    return subprocess.CompletedProcess(command, 0, data, b'')
                return subprocess.CompletedProcess(command, 1, b'', b'')
            with self.subTest(target=target), patch.object(review.subprocess, 'check_output', return_value=b'commit'), \
                    patch.object(review.subprocess, 'run', side_effect=git_result):
                with self.assertRaisesRegex(ValueError, 'Baseline reset or rewritten'):
                    review.validate_history(self.root, 'base')

    def test_expected_module_discovery_count_is_required_and_validated_against_execution(self):
        self.write('tests/unit/test_sample.py', SOURCE.replace('compute(1, 2)', 'compute(1, 9)'))
        record = self.draft()
        record['module_counts']['tests.unit.test_sample'] = 3
        self.append(record)
        ids = [CASE, 'tests.unit.test_sample.Sample.test_failure']
        report = {'status': 'passed', 'code_tree_start': {'lf_sha256': 'current'},
                  'code_tree_end': {'lf_sha256': 'current'}, 'batch_results': [{
                      'status': 'passed', 'worker_exit_confirmed': True,
                      'process_tree_exit_confirmed': True, 'test_count': 2,
                      'planned_test_ids': ids, 'executed_test_ids': ids,
                      'module_test_counts': [{'module': 'tests.unit.test_sample', 'test_count': 2}]}]}
        self.write('run.json', json.dumps(report))
        for changed in ('tests/unit/test_sample.py\n', ''):
            with self.subTest(changed=changed), patch.object(review, 'validate_history', return_value='base'), \
                    patch.object(review.subprocess, 'check_output', side_effect=[changed, '']), \
                    patch.object(review.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'')), \
                    patch.object(run_regression, '_code_tree_identity', return_value={'lf_sha256': 'current'}):
                with self.assertRaisesRegex(ValueError, 'Unexpected runtime discovery count: tests.unit.test_sample: expected 3, observed 2'):
                    review.check(self.root, executions=[self.root / 'run.json'], base_ref='base')

    def test_explicit_finding_update_preserves_original_and_reuses_identity(self):
        original = (self.root / review.AUDIT / 'findings.json.gz').read_bytes()
        record = self.draft()
        record['findings'] = [{'id': 'audit:root:old-gap', 'status': 'resolved',
                              'reason': 'confirmed bug fixed', 'evidence': 'reproduced before; result after',
                              'requirement': 'addition result 3', 'fix': 'restore addition', 'tests': [CASE]}]
        self.append(record)
        self.assertEqual('resolved', review.check(self.root)['finding_updates']['audit:root:old-gap']['status'])
        self.assertEqual(original, (self.root / review.AUDIT / 'findings.json.gz').read_bytes())

    def test_execution_evidence_rejects_stale_skipped_duplicate_and_missing_ids(self):
        report = {'status': 'passed', 'code_tree_start': {'lf_sha256': 'current'},
                  'code_tree_end': {'lf_sha256': 'current'}, 'batch_results': [{
                      'status': 'passed', 'worker_exit_confirmed': True, 'process_tree_exit_confirmed': True,
                      'test_count': 1, 'planned_test_ids': [CASE], 'executed_test_ids': [CASE]}]}
        path = self.root / 'run.json'
        with patch.object(run_regression, '_code_tree_identity', return_value={'lf_sha256': 'current'}):
            self.write('run.json', json.dumps(report))
            self.assertEqual({CASE}, review.execution_ids(self.root, [path]))
            variants = []
            stale = copy.deepcopy(report); stale['code_tree_end']['lf_sha256'] = 'old'; variants.append(stale)
            skipped = copy.deepcopy(report); skipped['batch_results'][0]['skipped'] = [CASE]; variants.append(skipped)
            missing = copy.deepcopy(report); missing['batch_results'][0].pop('executed_test_ids'); variants.append(missing)
            duplicate = copy.deepcopy(report); duplicate['batch_results'] *= 2; variants.append(duplicate)
            for value in variants:
                with self.subTest(value=value):
                    self.write('run.json', json.dumps(value))
                    with self.assertRaises(ValueError):
                        review.execution_ids(self.root, [path])

    def test_selected_product_test_missing_from_actual_execution_fails(self):
        self.write('src/kiwoom_monitor/sample.py', 'def compute(a,b): return a-b\n')
        self.append(self.draft())
        with patch.object(review, 'validate_history', return_value='base'), \
                patch.object(review.subprocess, 'check_output', side_effect=['src/kiwoom_monitor/sample.py\n', '']), \
                patch.object(review, 'execution_ids', return_value=set()):
            with self.assertRaisesRegex(ValueError, 'Related tests not executed'):
                review.check(self.root, executions=[], base_ref='base')

    def test_hosted_ci_keeps_nas_operator_obligations_separate_from_code_regression(self):
        unit_module = 'tests.unit.test_sample'
        nas_module = 'tests.integration.test_nas_owned'
        nas_ids = [nas_module + '.Sample.test_failure', nas_module + '.Sample.test_value']
        product = 'src/kiwoom_monitor/sample.py'
        unit_test = 'tests/unit/test_sample.py'
        nas_test = 'tests/integration/test_nas_owned.py'
        self.write(product, 'def compute(a,b): return a-b\n')
        self.write(unit_test, SOURCE.replace('EXPECTED = 3', 'EXPECTED = 4'))
        self.write(nas_test, SOURCE)
        record = self.draft()
        record['products'][product]['tests'] = [CASE, nas_module, nas_ids[1]]
        self.append(record)

        source = {'lf_sha256': 'current'}
        code_ids = [CASE, unit_module + '.Sample.test_failure']
        changes = '\n'.join((product, unit_test, nas_test))

        def checked(ids, *, hosted_ci=True, discovery_count=2, source_end=source):
            report = {'status': 'passed', 'planned': len(ids), 'ran': len(ids),
                      'source_start': source, 'source_end': source_end,
                      'planned_test_ids': ids, 'executed_test_ids': ids,
                      'discovered': {unit_module: discovery_count},
                      **dict.fromkeys(('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'), 0)}
            self.write('platform.json', json.dumps(report))
            with patch.object(run_regression, '_load_ci_groups', return_value={'nas_operator_owned': [nas_module]}), \
                    patch.object(run_regression, '_test_modules_under', side_effect=[{unit_module}, {nas_module}]), \
                    patch.object(run_regression, '_code_tree_identity', return_value=source), \
                    patch.object(review, 'validate_history', return_value='base'), \
                    patch.object(review.subprocess, 'check_output', side_effect=[changes, '']), \
                    patch.object(review.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'')):
                scope = {'hosted_ci': True} if hosted_ci else {}
                return review.check(self.root, executions=[self.root / 'platform.json'], base_ref='base', **scope)

        hosted = checked(code_ids)
        self.assertEqual('passed', hosted['status'])
        self.assertEqual(nas_ids, hosted['nas_operator_pending_test_ids'])
        self.assertEqual([nas_module], hosted['nas_operator_pending_modules'])
        with self.assertRaisesRegex(ValueError, 'Related tests not executed.*test_nas_owned'):
            checked(code_ids, hosted_ci=False)
        with self.assertRaisesRegex(ValueError, 'Related tests not executed.*test_sample'):
            checked(code_ids[1:])
        with self.assertRaisesRegex(ValueError, 'Unexpected runtime discovery count: tests.unit.test_sample'):
            checked(code_ids, discovery_count=3)
        with self.assertRaisesRegex(ValueError, 'Stale/incomplete execution evidence'):
            checked(code_ids, source_end={'lf_sha256': 'old'})
        with self.assertRaisesRegex(ValueError, 'NAS operator execution is outside hosted CI scope'):
            checked(code_ids + [nas_ids[0]])

        unknown_nas_id = nas_module + '.Sample.test_typo'
        record['products'][product]['tests'].append(unknown_nas_id)
        self.write(review.LEDGER, json.dumps(record) + '\n')
        with self.assertRaisesRegex(ValueError, 'Related tests not executed.*test_typo'):
            checked(code_ids)

    def test_module_requirement_needs_a_complete_registered_worker(self):
        module = 'tests.unit.test_sample'
        product = 'src/kiwoom_monitor/sample.py'
        fixture = 'tests/unit/test_sample.py'
        self.write(product, 'def compute(a,b): return a-b\n')
        self.write(fixture, SOURCE.replace('EXPECTED = 3', 'EXPECTED = 4'))
        record = self.draft()
        record['products'][product]['tests'] = [module, CASE]
        record['support'][fixture]['tests'] = [module]
        self.append(record)

        source = {'lf_sha256': 'current'}
        manifest = {'core_batches': [], 'profiles': {'runner': [module]}}
        ids = [CASE, module + '.Sample.test_failure']
        worker = {'name': 'runner:' + module, 'modules': [module], 'status': 'passed',
                  'worker_exit_confirmed': True, 'process_tree_exit_confirmed': True,
                  'exit_code': 0, 'source': {'verified': True}, 'timeout': False,
                  'interrupted': False, 'leaked_descendants': False,
                  'test_count': 2, 'planned_test_count': 2, 'zero_test_modules': [],
                  'module_test_counts': [{'module': module, 'test_count': 2}],
                  'planned_test_ids': ids, 'executed_test_ids': ids, 'passed_test_ids': ids}
        report = {'schema_version': 1, 'profile': 'all-local', 'status': 'passed',
                  'code_tree_start': source, 'code_tree_end': source,
                  'source_modified_during_run': False, 'interrupted': False,
                  'missing_test_files': [], 'unrun_batches': [], 'batch_results': [worker],
                  'planned_batches': [{'name': 'runner', 'modules': [module]}]}

        def checked(value):
            self.write('run.json', json.dumps(value))
            with patch.object(run_regression, '_load_manifest', return_value=manifest), \
                    patch.object(run_regression, '_test_modules_under', side_effect=[{module}, set()]), \
                    patch.object(run_regression, '_code_tree_identity', return_value=source), \
                    patch.object(review, 'validate_history', return_value='base'), \
                    patch.object(review.subprocess, 'check_output', side_effect=[product + '\n' + fixture, '']), \
                    patch.object(review.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'')):
                return review.check(self.root, executions=[self.root / 'run.json'], base_ref='base')

        self.assertEqual('passed', checked(report)['status'])
        selected = dict(report, profile='selected', selection_verified=True,
                        selection={'kind': 'incremental_test_selection', 'version': 1, 'source': source,
                                   'selected_modules': [module]})
        self.assertEqual('passed', checked(selected)['status'])
        partial = dict(worker, test_count=1, planned_test_count=1,
                       module_test_counts=[{'module': module, 'test_count': 1}],
                       planned_test_ids=[CASE], executed_test_ids=[CASE], passed_test_ids=[CASE])
        adapter = dict(report, source_start=source, source_end=source, planned=2, ran=2,
                       planned_test_ids=ids, executed_test_ids=ids, discovered={module: 2},
                       **dict.fromkeys(('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'), 0))
        wrong_ids = [CASE, 'tests.unit.test_other.Sample.test_failure']
        for name, value in (
            ('partial discovery', dict(report, batch_results=[partial])),
            ('missing standard plan', dict(report, planned_batches=[])),
            ('worker count drift', dict(report, batch_results=[dict(worker, planned_test_count=3)])),
            ('foreign case at same count', dict(report, batch_results=[dict(worker,
                 planned_test_ids=wrong_ids, executed_test_ids=wrong_ids, passed_test_ids=wrong_ids)])),
            ('platform adapter', adapter),
            ('literal module ID', dict(report, planned_batches=[],
                 batch_results=[dict(worker, planned_test_ids=[CASE, module], executed_test_ids=[CASE, module])])),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Related tests not executed'):
                checked(value)

    def test_execution_report_is_read_once_without_changing_raw_counts_or_failure_order(self):
        self.write('tests/unit/test_sample.py', SOURCE.replace('compute(1, 2)', 'compute(1, 9)'))
        self.append(self.draft())
        ids = [CASE, 'tests.unit.test_sample.Sample.test_failure']
        report = {'status': 'passed', 'planned': 2, 'ran': 2,
                  'failures': 0, 'errors': 0, 'skipped': 0, 'expected_failures': 0, 'unexpected_successes': 0,
                  'source_start': {'lf_sha256': 'current'}, 'source_end': {'lf_sha256': 'current'},
                  'planned_test_ids': ids, 'executed_test_ids': ids,
                  'discovered': {'tests.unit.test_sample': 9},
                  'module_test_counts': [{'module': 'tests.unit.test_sample', 'test_count': 4}],
                  'batch_results': [{'module_test_counts': [{'module': 'tests.unit.test_sample', 'test_count': 2}]}]}
        path = self.root / 'platform.json'
        self.write('platform.json', json.dumps(report))

        def checked(paths):
            with patch.object(review, 'validate_history', return_value='base'), \
                    patch.object(review.subprocess, 'check_output', side_effect=['tests/unit/test_sample.py\n', '']), \
                    patch.object(review.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'')), \
                    patch.object(run_regression, '_code_tree_identity', return_value={'lf_sha256': 'current'}):
                return review.check(self.root, executions=paths, base_ref='base')

        with patch.object(review, 'read_json', wraps=review.read_json) as reads:
            self.assertEqual('passed', checked([path])['status'])
            self.assertEqual(1, sum(call.args[0] == path for call in reads.call_args_list))
        malformed = copy.deepcopy(report)
        malformed['discovered'] = None
        self.write('platform.json', json.dumps(malformed))
        stale = copy.deepcopy(report)
        stale['source_end']['lf_sha256'] = 'old'
        self.write('stale.json', json.dumps(stale))
        with self.assertRaisesRegex(ValueError, 'Stale/incomplete execution evidence'):
            checked([path, self.root / 'stale.json'])
        malformed.update(planned=1, ran=1, planned_test_ids=[ids[1]], executed_test_ids=[ids[1]])
        self.write('platform.json', json.dumps(malformed))
        with self.assertRaisesRegex(ValueError, 'Changed tests not executed'):
            checked([path])


if __name__ == '__main__':
    unittest.main()
