"""Real blocking/ownership checks on the explicit disposable diagnostic DB."""
import json
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from kiwoom_monitor.central_server.database_market_bars import (
    _lock_postgres_advisory_scopes, _lock_postgres_minute_day_scopes,
)


class PostgresScopeLockTests(unittest.TestCase):
    def setUp(self):
        url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not url:
            raise unittest.SkipTest('dedicated PostgreSQL required')
        if urlsplit(url).path != '/kiwoom_monitor_diagnostic_test':
            raise RuntimeError('refusing non-diagnostic PostgreSQL')
        self.owner = psycopg.connect(url)
        self.worker = psycopg.connect(url)
        self.probe = psycopg.connect(url, autocommit=True)
        self.addCleanup(self.owner.close)
        self.addCleanup(self.worker.close)
        self.addCleanup(self.probe.close)
        for conn in (self.owner, self.worker, self.probe):
            conn.execute('SET statement_timeout=5000')
            conn.commit()
        self.token = uuid4().hex

    def try_lock(self, scope, seed):
        return self.probe.execute('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,%s))',
                                  (scope, seed)).fetchone()[0]

    def wait_blocked(self):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            row = self.probe.execute('SELECT wait_event_type,pg_blocking_pids(pid) '
                'FROM pg_stat_activity WHERE pid=%s', (self.worker.info.backend_pid,)).fetchone()
            if row and row[0] == 'Lock' and self.owner.info.backend_pid in row[1]:
                return
            time.sleep(.01)
        self.fail('worker did not block on expected owner')

    def drain(self, future):
        self.owner.rollback()
        if not future.done():
            self.worker.cancel()
        try:
            future.result(7)
        except psycopg.Error:
            pass
        self.worker.rollback()

    def test_day_batch_waits_on_first_sorted_scope_before_acquiring_later_scope(self):
        rows = [dict(trading_date='2099-01-14', code=self.token+code, market='KRX')
                for code in ('Z', 'A', 'Z')]
        scopes = [json.dumps(('2099-01-14', self.token+code, 'KRX'), separators=(',', ':'))
                  for code in ('A', 'Z')]
        self.owner.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,1))', (scopes[0],))
        with ThreadPoolExecutor(max_workers=1) as pool:
            def acquire():
                with self.worker.cursor() as cursor:
                    _lock_postgres_minute_day_scopes(cursor, rows)
            future = pool.submit(acquire)
            try:
                self.wait_blocked()
                self.assertTrue(self.try_lock(scopes[1], 1))
                self.owner.rollback()
                future.result(5)
                self.assertFalse(self.try_lock(scopes[0], 1))
                self.assertFalse(self.try_lock(scopes[1], 1))
                self.worker.commit()
                self.assertTrue(self.try_lock(scopes[0], 1))
                self.assertTrue(self.try_lock(scopes[1], 1))
            finally:
                self.drain(future)

    def test_chunk_boundary_holds_earlier_scopes_while_waiting_then_rollback_releases_all(self):
        scopes = [self.token+f'-{i:04}' for i in range(1001)]
        self.owner.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (scopes[-1],))
        with ThreadPoolExecutor(max_workers=1) as pool:
            def acquire():
                with self.worker.cursor() as cursor:
                    _lock_postgres_advisory_scopes(cursor, scopes, seed=0)
            future = pool.submit(acquire)
            try:
                self.wait_blocked()
                self.assertFalse(self.try_lock(scopes[0], 0))
                self.assertFalse(self.try_lock(scopes[999], 0))
                self.owner.rollback()
                future.result(5)
                self.assertFalse(self.try_lock(scopes[-1], 0))
                self.worker.rollback()
                self.assertTrue(self.try_lock(scopes[0], 0))
                self.assertTrue(self.try_lock(scopes[-1], 0))
            finally:
                self.drain(future)

    def test_hash_seed_namespaces_and_transaction_ownership_are_preserved(self):
        scopes = [self.token+'-A', self.token+'-Z']
        with self.worker.cursor() as cursor:
            _lock_postgres_advisory_scopes(cursor, scopes, seed=0)
        for scope in scopes:
            self.assertFalse(self.try_lock(scope, 0))
            self.assertTrue(self.try_lock(scope, 1))
        self.worker.rollback()
        for scope in scopes:
            self.assertTrue(self.try_lock(scope, 0))
