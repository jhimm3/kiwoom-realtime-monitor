from contextlib import closing
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_research_independent_comparisons as fixtures
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.research_process import load_independent_comparison_request, execute_independent_comparison, main
from scripts.run_research import ResearchRunCancelled


def seed(root):
    repo = ResearchRepository(root / 'research.sqlite3')
    for record in (fixtures.record(pnl=100), fixtures.record('validation', 20, 'VALIDATION', -49)):
        run_id, document = record['run_id'], deepcopy(record['report'])
        document.update(report_id='report-' + run_id, status='ELIGIBLE')
        repo.start_run(run_id, record['spec'], record['input_manifest'])
        repo.save_research_report(SimpleNamespace(run_id=run_id, report_id=document['report_id'], status='ELIGIBLE', to_dict=lambda: document))
        repo.finish_run(run_id, record['logical_result_hash'])
    path = root / 'comparison.json'
    path.write_text(json.dumps({'version': 'independent_development_comparison_request/v1',
        'database': 'research.sqlite3', 'run_ids': ['train', 'validation']}), encoding='utf-8')
    return repo, path


class IndependentComparisonProcessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo, self.path = seed(self.root)
        self.result, self.cancel = self.root / 'result.json', self.root / 'cancel'

    def cli(self, *extra):
        with patch('sys.stdout', new=StringIO()):
            code = main(['--compare-runs', str(self.path), '--result', str(self.result), '--cancel', str(self.cancel), *extra])
        return code, json.loads(self.result.read_text(encoding='utf-8'))

    def test_parser_resolves_relative_path_and_preserves_explicit_scope(self):
        request = load_independent_comparison_request(self.path)
        self.assertEqual(self.repo.path.resolve(), request.database)
        self.assertEqual(('train', 'validation'), request.run_ids)

    def test_parser_rejects_unknown_version_fields_empty_excessive_ids_and_large_file(self):
        original = json.loads(self.path.read_text())
        for field, value in [('version', 'v2'), ('extra', True), ('run_ids', []),
                             ('run_ids', ['train'] * 201), ('run_ids', [' ']), ('run_ids', ['a' * 257])]:
            document = {**original, field: value}
            self.path.write_text(json.dumps(document))
            with self.assertRaises(ValueError):
                load_independent_comparison_request(self.path)
        self.path.write_bytes(b' ' * 65537)
        with self.assertRaises(ValueError):
            load_independent_comparison_request(self.path)

    def test_cli_queries_read_only_without_loader_runner_or_migrations(self):
        before = self.repo.path.read_bytes()
        with (patch('kiwoom_monitor.research_process.load_research_input', side_effect=AssertionError('no loader')),
              patch('kiwoom_monitor.research_process.execute_process_request', side_effect=AssertionError('no runner')),
              patch('kiwoom_monitor.infrastructure.persistence.research_repository.SQLiteMigrationRunner', side_effect=AssertionError('no migration'))):
            code, result = self.cli()
        self.assertEqual((0, 'ok', 'COMPLETE'), (code, result['status'], result['comparison']['status']))
        self.assertEqual(25.5, result['comparison']['median_partition_pnl_won'])
        self.assertEqual(before, self.repo.path.read_bytes())

    def test_read_only_mode_rejects_writes_and_missing_database_without_creating_parent(self):
        before = self.repo.path.read_bytes()
        readonly = ResearchRepository(self.repo.path, read_only=True)
        with self.assertRaises(sqlite3.Error):
            readonly.start_run('forbidden', {}, {})
        self.assertEqual(before, self.repo.path.read_bytes())
        missing = self.root / 'absent' / 'database.sqlite3'
        with self.assertRaises(ValueError):
            ResearchRepository(missing, read_only=True)
        self.assertFalse(missing.parent.exists())

    def test_old_database_requires_prior_migration_and_is_preserved(self):
        with closing(sqlite3.connect(self.repo.path)) as connection, connection:
            connection.execute('DELETE FROM research_schema_migrations WHERE version>=17')
        before = self.repo.path.read_bytes()
        code, result = self.cli()
        self.assertEqual((1, 'failed'), (code, result['status']))
        self.assertIn('v17', result['reason'])
        self.assertEqual(before, self.repo.path.read_bytes())

    def test_cancel_before_and_after_query_returns_native_two_without_scientific_writes(self):
        before = self.repo.path.read_bytes()
        self.cancel.write_text('cancel')
        self.assertEqual((2, 'cancelled'), (lambda pair: (pair[0], pair[1]['status']))(self.cli()))
        self.cancel.unlink()
        request = load_independent_comparison_request(self.path)
        original = ResearchRepository.load_independent_development_comparison
        def finish_then_cancel(repo, ids):
            result = original(repo, ids); self.cancel.write_text('cancel'); return result
        with patch.object(ResearchRepository, 'load_independent_development_comparison', new=finish_then_cancel):
            with self.assertRaises(ResearchRunCancelled):
                execute_independent_comparison(request, cancel_path=self.cancel)
        self.assertEqual(before, self.repo.path.read_bytes())

    def test_invalid_source_and_output_collisions_never_overwrite_database_or_request(self):
        db_before, source_before = self.repo.path.read_bytes(), self.path.read_bytes()
        for output in (self.repo.path, self.path):
            with patch('sys.stderr', new=StringIO()), self.assertRaises(SystemExit):
                main(['--compare-runs', str(self.path), '--result', str(output)])
        with patch('sys.stderr', new=StringIO()), self.assertRaises(SystemExit):
            main(['--compare-runs', str(self.path), '--result', str(self.result), '--cancel', str(self.repo.path)])
        self.assertEqual(db_before, self.repo.path.read_bytes())
        self.assertEqual(source_before, self.path.read_bytes())
        self.path.write_text('{invalid-json')
        with patch('sys.stderr', new=StringIO()), self.assertRaises(SystemExit):
            main(['--compare-runs', str(self.path), '--result', str(self.repo.path)])
        self.assertEqual(db_before, self.repo.path.read_bytes())

    def test_query_and_campaign_modes_are_mutually_exclusive(self):
        for extra in (['--campaign', 'c'], ['--register-campaign', 'c'], ['--database', str(self.repo.path)]):
            with patch('sys.stderr', new=StringIO()), self.assertRaises(SystemExit):
                self.cli(*extra)
        self.assertFalse(self.result.exists())

    def test_missing_run_remains_incomplete_not_native_failure(self):
        document = json.loads(self.path.read_text()); document['run_ids'].append('missing')
        self.path.write_text(json.dumps(document))
        code, result = self.cli()
        self.assertEqual((0, 'INCOMPLETE', 'MISSING'), (code, result['comparison']['status'], result['comparison']['partitions'][-1]['status']))

    def test_locked_database_fails_with_bounded_timeout_instead_of_waiting_indefinitely(self):
        before = self.repo.path.read_bytes()
        with closing(sqlite3.connect(self.repo.path)) as connection:
            connection.execute('BEGIN EXCLUSIVE')
            started = time.monotonic()
            code, result = self.cli()
            elapsed = time.monotonic() - started
            connection.rollback()
        self.assertEqual((1, 'failed'), (code, result['status']))
        self.assertIn('locked', result['reason'])
        self.assertLess(elapsed, 4)
        self.assertEqual(before, self.repo.path.read_bytes())
