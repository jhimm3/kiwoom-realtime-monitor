"""Operator provisioning must refuse foreign resources before any DDL."""
import io
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from scripts import check_recorded_replay_baseline as operator


class RecordedReplayOperatorTests(unittest.TestCase):
    def test_endpoint_rejects_overrides_and_preserves_only_host_port(self):
        url = 'postgresql://admin:private-secret@database:5432/kiwoom_monitor'
        self.assertEqual('database:5432', operator._endpoint(url))
        for suffix in ('?options=unsafe', '#fragment'):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                operator._endpoint(url + suffix)

    def test_foreign_database_or_privileged_role_prevents_resource_ddl(self):
        settings = {'endpoint': 'database:5432', 'password': 'a' * 64}
        valid_role = (False, False, False, False, False, True)
        cases = [(valid_role, ('other_owner',)), ((True,) + valid_role[1:], None)]
        for role, database in cases:
            connection = MagicMock()
            cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [('postgres', 'admin', True, True, True), role, database]
            with self.subTest(role=role), patch.object(operator.psycopg, 'connect', return_value=connection) as connect:
                with self.assertRaisesRegex(RuntimeError, 'mismatch'):
                    operator._provision_role_database(
                        'postgresql://admin:private-secret@database/kiwoom_monitor', settings)
                self.assertEqual(1, connect.call_count)
            self.assertTrue(all(str(call.args[0]).startswith('SELECT')
                                for call in cursor.execute.call_args_list))

    def test_insufficient_admin_privilege_fails_before_resource_inspection(self):
        connection = MagicMock()
        cursor = connection.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ('postgres', 'readonly', False, False, False)
        with patch.object(operator.psycopg, 'connect', return_value=connection):
            with self.assertRaisesRegex(RuntimeError, 'admin_privileges'):
                operator._provision_role_database(
                    'postgresql://readonly:secret@database/kiwoom_monitor', {})
        self.assertEqual(1, cursor.execute.call_count)

    def test_connection_error_output_never_contains_credentials(self):
        url = 'postgresql://admin:private-secret@database/kiwoom_monitor'
        output = io.StringIO()
        with patch.dict(operator.os.environ, {'KIWOOM_SERVER_DATABASE_URL': url}), \
             patch.object(operator, '_locked_secrets', side_effect=operator.psycopg.OperationalError(url)), \
             redirect_stdout(output):
            self.assertEqual(1, operator.main(['--provision']))
        result = json.loads(output.getvalue())
        self.assertEqual('OperationalError', result['error_type'])
        self.assertNotIn('private-secret', output.getvalue())

    def test_optional_report_preserves_execution_id_source_and_no_overwrite_contract(self):
        url = 'postgresql://admin:private-secret@database/kiwoom_monitor'
        settings = {'endpoint': 'database:5432', 'password': 'a' * 64, 'owner_token': 'b' * 32}
        scenarios = (
            ('baseline', [], True), ('execution', ['--execution-gates'], True),
            ('capture', ['--recorded-capture-gates'], True), ('failure', [], False),
            ('skip', [], False), ('ids', [], False), ('duplicate_ids', [], False),
            ('source', [], False),
        )
        with tempfile.TemporaryDirectory() as directory:
            for kind, flags, expected in scenarios:
                with self.subTest(kind=kind), ExitStack() as stack:
                    path = Path(directory) / (kind + '.json')
                    stack.enter_context(patch.dict(operator.os.environ,
                        {'KIWOOM_SERVER_DATABASE_URL': url}, clear=True))
                    locked = stack.enter_context(patch.object(operator, '_locked_secrets'))
                    locked.return_value.__enter__.return_value = Path(directory)
                    stack.enter_context(patch.object(operator, '_settings', return_value=(settings, False)))
                    lease = stack.enter_context(patch.object(operator.baseline, 'ReplayDatabaseLease'))
                    lease.return_value.__enter__.return_value.status.return_value = {'baseline_id': 'sealed'}
                    stack.enter_context(patch.object(operator, '_code_tree_identity', side_effect=[
                        {'lf_sha256': 'same'}, {'lf_sha256': 'changed' if kind == 'source' else 'same'}]))
                    commit = stack.enter_context(patch.object(operator.subprocess, 'run'))
                    commit.return_value.stdout = 'candidate\n'

                    def load(name):
                        def body():
                            if kind == 'failure':
                                raise AssertionError('controlled failure')
                            if kind == 'skip':
                                raise unittest.SkipTest('controlled skip')
                        cases = [unittest.FunctionTestCase(body)
                                 for _ in range(2 if kind == 'duplicate_ids' else 1)]
                        for case in cases:
                            case.id = lambda: name + '.test_fixture'
                        return unittest.TestSuite(cases)

                    stack.enter_context(patch.object(operator.unittest.defaultTestLoader,
                                                    'loadTestsFromName', side_effect=load))
                    if kind == 'ids':
                        stack.enter_context(patch.object(operator, '_suite_test_ids', return_value=['wrong']))
                    output = stack.enter_context(redirect_stdout(io.StringIO()))
                    stack.enter_context(redirect_stderr(io.StringIO()))
                    self.assertEqual(0 if expected else 1, operator.main(flags + ['--report', str(path)]))
                    report = json.loads(path.read_text(encoding='utf-8'))
                    self.assertEqual('passed' if expected else 'failed', report['status'])
                    self.assertEqual(2 if kind in ('capture', 'duplicate_ids') else 1, report['ran'])
                    self.assertEqual(report['planned'], sum(report['discovered'].values()))
                    self.assertEqual(int(kind == 'failure'), report['failures'])
                    self.assertEqual(int(kind == 'skip'), report['skipped'])
                    self.assertEqual('candidate', report['git_commit'])
                    if expected:
                        self.assertEqual(report['planned_test_ids'], report['executed_test_ids'])
                        self.assertEqual(report['source_start'], report['source_end'])
                    serialized = path.read_text(encoding='utf-8') + output.getvalue()
                    for secret in (url, 'private-secret', settings['password'], settings['owner_token']):
                        self.assertNotIn(secret, serialized)
                    previous = path.read_bytes()
                    locked.reset_mock()
                    self.assertEqual(1, operator.main(['--report', str(path)]))
                    locked.assert_not_called()
                    self.assertEqual(previous, path.read_bytes())


if __name__ == '__main__':
    unittest.main()
