import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server import database_datasets as datasets
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
        with patch.object(datasets, 'time', return_value=123):
            self.store.save_dataset_snapshots(values())
        sql = self.connection.probe.statements
        self.assertEqual(3, len(sql))
        self.assertEqual(10, len(sql[1][1]))
        self.assertEqual((123, 123), (sql[1][1][3], sql[1][1][8]))
        self.assertEqual(24, len(sql[2][1]))
        self.assertEqual((1, 0, True), (self.connection.commits, self.connection.rollbacks, self.connection.closed))

    def test_duplicate_snapshot_keys_keep_sequential_updates(self):
        batch = values()
        batch[1] = (*batch[0][:3], {'price': 99}, batch[0][4])
        self.store.save_dataset_snapshots(batch)
        self.assertEqual(5, len(self.connection.probe.statements))
        self.assertIn('99', self.connection.probe.statements[3][1][-1])

    def test_distinct_snapshots_with_shared_metadata_identity_keep_sequential_order(self):
        batch = values()
        batch[1] = (*batch[1][:4], batch[0][4])
        self.store.save_dataset_snapshots(batch)
        self.assertEqual(5, len(self.connection.probe.statements))

    def test_research_history_retains_per_row_append_order(self):
        batch = [('ranking', *row[1:]) for row in values()]
        with patch.object(datasets, '_append_postgres_observation_revision') as append:
            self.store.save_dataset_snapshots(batch)
        self.assertEqual(2, append.call_count)
        self.assertEqual(['market-0', 'market-1'], [c.args[2] for c in append.call_args_list])
        self.assertEqual(5, len(self.connection.probe.statements))

    def test_cache_invalidation_stays_sequential_and_locked(self):
        batch = [('top20_index', '2020-01-01', f'key-{i}', {}, None) for i in range(2)]
        self.store.save_dataset_snapshots(batch)
        sql = [row[0] for row in self.connection.probe.statements]
        self.assertEqual(1, sum('pg_advisory_xact_lock' in row for row in sql))
        self.assertEqual(2, sum(row.startswith('DELETE ') for row in sql))

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

    def test_chunk_boundary_keeps_one_commit(self):
        self.store.save_dataset_snapshots(values(1001, observation=False))
        self.assertEqual([0, 5000, 5], [len(row[1]) for row in self.connection.probe.statements])
        self.assertEqual(1, self.connection.commits)


if __name__ == '__main__':
    unittest.main()
