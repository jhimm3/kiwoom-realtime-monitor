import unittest
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server.market_observations import market_state_observation


class Cursor:
    def __init__(self, fail_metadata=False):
        self.statements = []
        self.fail_metadata = fail_metadata

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        return False

    def execute(self, sql, parameters=()):
        self.statements.append((sql, parameters))
        if self.fail_metadata and sql.startswith('INSERT INTO central_market_data_observation_meta'):
            raise RuntimeError('metadata failure')


    def fetchone(self):
        return None


class Connection:
    info = SimpleNamespace(backend_pid=0)

    def __init__(self, fail_metadata=False):
        self.probe = Cursor(fail_metadata)
        self.commits = self.rollbacks = 0
        self.closed = False

    def cursor(self, *unused, **options):
        return self.probe

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def values(count=2, observation=True):
    now = datetime(2026, 10, 8, 0, 5, tzinfo=timezone.utc)
    key = '2026-10-08T09:05:00+09:00'
    return [('market_state', f'market-{i}', key, {'price': i},
             market_state_observation(f'market-{i}', key, {'price': i}, now)
             if observation else None) for i in range(count)]


class DatasetBatchPostgresTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection()
        self.store = PostgresQueryStore('postgresql://unused')
        self.store._connect = lambda: self.connection

    def test_distinct_keys_batch_snapshot_and_metadata_with_one_commit_and_same_saved_at(self):
        fixed = datetime.fromisoformat('2030-01-02T03:04:05.123456+09:00')
        with patch.object(self.store, '_now_provider', return_value=fixed) as clock:
            self.store.save_dataset_snapshots(values())
        clock.assert_called_once_with()
        sql = self.connection.probe.statements
        self.assertEqual(3, len(sql))
        self.assertEqual(10, len(sql[1][1]))
        self.assertEqual((fixed.timestamp(), fixed.timestamp()), (sql[1][1][3], sql[1][1][8]))
        self.assertEqual(24, len(sql[2][1]))
        self.assertEqual((1, 0, True), (self.connection.commits, self.connection.rollbacks, self.connection.closed))
        self.assertEqual(['market-0','market-1'], [sql[1][1][i] for i in (1,6)])
        self.assertEqual([{'price':0},{'price':1}], [json.loads(sql[1][1][i]) for i in (4,9)])
        self.assertEqual(['market-0','market-1'], [sql[2][1][i] for i in (1,13)])

    def test_duplicate_snapshot_keys_keep_sequential_updates(self):
        batch = values()
        batch[1] = (*batch[0][:3], {'price': 99}, batch[0][4])
        fixed = datetime.fromisoformat('2030-01-02T03:04:05.123456+09:00')
        with patch.object(self.store, '_now_provider', return_value=fixed) as clock:
            self.store.save_dataset_snapshots(batch)
        clock.assert_called_once_with()
        self.assertEqual(5, len(self.connection.probe.statements))
        self.assertIn('99', self.connection.probe.statements[3][1][-1])
        self.assertEqual([fixed.timestamp()] * 2,
                         [self.connection.probe.statements[i][1][3] for i in (1, 3)])

    def test_distinct_snapshots_with_shared_metadata_identity_keep_sequential_order(self):
        batch = values()
        batch[1] = (*batch[1][:4], batch[0][4])
        self.store.save_dataset_snapshots(batch)
        self.assertEqual(5, len(self.connection.probe.statements))

    def test_research_history_retains_per_row_append_order(self):
        batch = [('ranking', *row[1:]) for row in values()]
        self.store.save_dataset_snapshots(batch)
        inserted = [p for sql,p in self.connection.probe.statements if sql.startswith('INSERT INTO central_observation_revisions')]
        self.assertEqual(['market-0', 'market-1'], [row[7] for row in inserted])
        self.assertEqual([{'price':0}, {'price':1}], [json.loads(row[-1]) for row in inserted])
        self.assertTrue(all(row[6] == 'ranking' for row in inserted))
        self.assertEqual(1, self.connection.commits)

    def test_cache_invalidation_stays_sequential_and_locked(self):
        batch = [('top20_index', '2020-01-01', f'key-{i}', {}, None) for i in range(2)]
        self.store.save_dataset_snapshots(batch)
        sql = [row[0] for row in self.connection.probe.statements]
        self.assertEqual(1, sum('pg_advisory_xact_lock' in row for row in sql))
        deletes = [row for row in self.connection.probe.statements if row[0].startswith('DELETE ')]
        self.assertEqual([
            ("display", ('2020-01-01',)), ("raw", ('2020-01-01',)),
            ("display", ('2020-01-01',)), ("raw", ('2020-01-01',)),
        ], [("display" if 'top20_statistics_display_day' in statement else "raw", parameters)
            for statement, parameters in deletes])

    def test_large_text_falls_back_without_combining_bind_payloads(self):
        batch = [(kind, subject, key, {'text': 'x'*600_000}, None)
                 for kind, subject, key, *_ in values()]
        self.store.save_dataset_snapshots(batch)
        self.assertEqual([0, 5, 5], [len(row[1]) for row in self.connection.probe.statements])

    def test_metadata_failure_rolls_back_entire_batch_and_closes(self):
        self.connection.probe.fail_metadata = True
        with self.assertRaisesRegex(RuntimeError, 'metadata failure'):
            self.store.save_dataset_snapshots(values())
        self.assertEqual((0, 1, True), (self.connection.commits, self.connection.rollbacks, self.connection.closed))

    def test_empty_batch_and_clock_failure_do_not_open_connection(self):
        with patch.object(self.store, '_now_provider', side_effect=RuntimeError('clock failure')) as clock, \
                patch.object(self.store, '_connect') as connect:
            self.store.save_dataset_snapshots([])
            clock.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, 'clock failure'):
                self.store.save_dataset_snapshots(values())
            clock.assert_called_once_with()
            connect.assert_not_called()

    def test_chunk_boundary_keeps_one_commit(self):
        self.store.save_dataset_snapshots(values(1001, observation=False))
        self.assertEqual([0, 5000, 5], [len(row[1]) for row in self.connection.probe.statements])
        self.assertEqual(1, self.connection.commits)


if __name__ == '__main__':
    unittest.main()
