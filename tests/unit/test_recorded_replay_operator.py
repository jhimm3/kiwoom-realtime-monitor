"""Operator provisioning must refuse foreign resources before any DDL."""
import io
from contextlib import redirect_stdout
import json
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


if __name__ == '__main__':
    unittest.main()
