import json
import unittest
from unittest.mock import MagicMock

from kiwoom_monitor.central_server.database_market_bars import (
    _lock_postgres_advisory_scopes, _lock_postgres_minute_day_scopes,
    _load_postgres_latest_revisions,
)


class PostgresScopeLockTests(unittest.TestCase):
    def test_empty_and_invalid_seed_do_not_execute(self):
        cursor = MagicMock()
        _lock_postgres_advisory_scopes(cursor, [], seed=1)
        with self.assertRaises(ValueError):
            _lock_postgres_advisory_scopes(cursor, ['x'], seed=2)
        cursor.execute.assert_not_called()

    def test_scalar_preserves_existing_statement_and_key(self):
        cursor = MagicMock()
        _lock_postgres_advisory_scopes(cursor, ['encoded'], seed=1)
        cursor.execute.assert_called_once_with(
            'SELECT pg_advisory_xact_lock(hashtextextended(%s,1))', ('encoded',))

    def test_day_scope_deduplicates_and_retains_python_sort_order(self):
        cursor = MagicMock()
        rows = [dict(trading_date='2026-10-08', code=code, market='KRX')
                for code in ('Z', 'A', 'Z')]
        _lock_postgres_minute_day_scopes(cursor, rows)
        query, params = cursor.execute.call_args.args
        self.assertEqual(1, cursor.execute.call_count)
        self.assertEqual([['2026-10-08', 'A', 'KRX'], ['2026-10-08', 'Z', 'KRX']],
                         [json.loads(value) for value in params[0]])
        self.assertIn('hashtextextended(scope,1)', query)
        self.assertIn('ORDER BY requested.ordinal', query)
        self.assertNotIn('LIMIT', query)

    def test_chunk_boundary_preserves_entire_scope_order(self):
        cursor = MagicMock()
        scopes = [str(i) for i in range(1001)]
        _lock_postgres_advisory_scopes(cursor, scopes, seed=0)
        first, second = cursor.execute.call_args_list
        self.assertEqual(scopes[:1000], first.args[1][0])
        self.assertEqual((scopes[-1],), second.args[1])

    def test_revision_scope_batch_and_already_locked_path_keep_lookup_contract(self):
        sources = [MagicMock(kind='minute_bar', subject=subject, observation_key='key', source_id='source')
                   for subject in ('Z', 'A', 'Z')]
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        _load_postgres_latest_revisions(cursor, sources)
        self.assertEqual(2, cursor.execute.call_count)
        self.assertIn('hashtextextended(scope,0)', cursor.execute.call_args_list[0].args[0])
        cursor.reset_mock()
        _load_postgres_latest_revisions(cursor, sources, lock_scopes=False)
        self.assertEqual(1, cursor.execute.call_count)
        self.assertNotIn('pg_advisory', cursor.execute.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
