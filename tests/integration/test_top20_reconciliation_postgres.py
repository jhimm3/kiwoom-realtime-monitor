"""Native derived publication gates in the dedicated PostgreSQL test database."""
from __future__ import annotations

from datetime import datetime
import asyncio
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server import database_top20_reconciliation as reads
from kiwoom_monitor.domain.top20_reconciliation import RECONCILED_KIND, build_top20_reconciliation
from tests.unit import test_top20_reconciliation as fixtures
from tests.integration.postgres_test_support import isolated_observation_schema

DAY, NOW, inputs = fixtures.DAY, fixtures.NOW, fixtures.inputs


class PostgresTop20ReconciliationTests(fixtures.NativeStoreTests):
    def setUp(self):
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            raise unittest.SkipTest("dedicated PostgreSQL test database not configured")
        self.store = PostgresQueryStore(self.enterContext(isolated_observation_schema(url)))
        self.store.initialize()

    def test_recorded_display_native_replay_twice_preserves_cache_receipt_and_values(self):
        from tests.unit.test_diagnostic_display_capture import captured_rows
        from tests.unit.test_recorded_execution import execute
        self.seed()
        with captured_rows() as (rows, rejected), \
             patch.object(reads, '_top20_today', return_value=datetime(2026, 10, 12).date()), \
             patch.object(reads, 'is_paused', return_value=False):
            self.store.load_top20_reconciliation_inputs(DAY)
            self.store.load_top20_display_statistics(DAY, DAY)
            self.store.load_top20_display_statistics(DAY, DAY)
        self.assertEqual([], rejected)
        expected = self.store.load_dataset_snapshots('top20_statistics_display_day', DAY)
        url = os.environ['KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL']
        for _ in range(2):
            target = PostgresQueryStore(self.enterContext(isolated_observation_schema(url)))
            target.initialize()
            self.addCleanup(target.close)
            fixtures.NativeStoreTests.seed(SimpleNamespace(store=target))
            with patch.object(reads, '_top20_today', side_effect=AssertionError('host day')), \
                 patch.object(reads, 'is_paused', side_effect=AssertionError('operating control')):
                result = asyncio.run(execute(target, rows))
            self.assertEqual('complete', result['state'])
            self.assertTrue(result['source_outcomes_match'])
            self.assertEqual(expected, target.load_dataset_snapshots('top20_statistics_display_day', DAY))

    def test_input_read_uses_one_snapshot_when_bars_change_mid_read(self):
        self.seed()
        original = reads._document
        changed = False

        def decode(value):
            nonlocal changed
            document = original(value)
            if not changed and "cohort_segments" in document:
                changed = True
                with self.store._connect() as connection, connection.cursor() as cursor:
                    cursor.execute("UPDATE central_minute_bars SET trade_value_million_won=20000 WHERE code='A'")
            return document

        with patch.object(reads, "_document", side_effect=decode):
            first = self.store.load_top20_reconciliation_inputs(DAY)
        self.assertTrue(changed)
        self.assertEqual(10000, first.pairs["A:KRX"]["bars"]["09:00"]["trade_value_million_won"])
        second = self.store.load_top20_reconciliation_inputs(DAY)
        self.assertEqual(20000, second.pairs["A:KRX"]["bars"]["09:00"]["trade_value_million_won"])

    def test_failed_cache_invalidation_rolls_back_entire_publication(self):
        first = build_top20_reconciliation(inputs(), available_at=NOW.isoformat())
        self.store.save_dataset_snapshot(RECONCILED_KIND, DAY, DAY, first)
        self.store.save_dataset_snapshot("top20_statistics_display_day", DAY, DAY, {"old": True})
        with self.store._connect() as connection, connection.cursor() as cursor:
            cursor.execute("CREATE FUNCTION fail_cache_delete() RETURNS trigger LANGUAGE plpgsql AS $$ "
                           "BEGIN RAISE EXCEPTION 'test cache failure'; END $$")
            cursor.execute("CREATE TRIGGER fail_cache BEFORE DELETE ON central_dataset_snapshots "
                           "FOR EACH ROW WHEN (OLD.kind='top20_statistics_display_day') "
                           "EXECUTE FUNCTION fail_cache_delete()")
        next_value = dict(first, generation="replacement")
        with self.assertRaises(Exception):
            self.store.save_dataset_snapshot(RECONCILED_KIND, DAY, DAY, next_value)
        self.assertEqual(first, self.store.load_top20_series(DAY))
        self.assertEqual({"old": True}, self.store.load_dataset_snapshots("top20_statistics_display_day", DAY)[0]["payload"])

    def test_cache_fill_and_publication_share_day_lock(self):
        self.seed()
        computing, release = threading.Event(), threading.Event()
        original = reads.display_statistics
        errors = []

        def slow_compute(*args):
            computing.set()
            if not release.wait(10):
                raise RuntimeError("cache gate timed out")
            return original(*args)

        def fill():
            try:
                self.store.load_top20_display_statistics(DAY, DAY)
            except BaseException as error:
                errors.append(error)

        value = build_top20_reconciliation(self.store.load_top20_reconciliation_inputs(DAY), available_at=NOW.isoformat())
        published = threading.Event()

        def publish():
            try:
                self.store.save_dataset_snapshot(RECONCILED_KIND, DAY, DAY, value)
                published.set()
            except BaseException as error:
                errors.append(error)

        with patch.object(reads, "display_statistics", side_effect=slow_compute), \
                patch.object(reads, "_top20_today", return_value=datetime(2026, 10, 12).date()):
            reader = threading.Thread(target=fill)
            writer = threading.Thread(target=publish)
            reader.start()
            try:
                self.assertTrue(computing.wait(10))
                writer.start()
                self.assertFalse(published.wait(.15))
            finally:
                release.set()
                reader.join(15)
                if writer.ident is not None:
                    writer.join(15)
        self.assertFalse(reader.is_alive())
        self.assertFalse(writer.is_alive())
        self.assertEqual([], errors)
        self.assertTrue(published.is_set())
        self.assertEqual([], self.store.load_dataset_snapshots("top20_statistics_display_day", DAY))
        self.assertEqual(100, self.store.load_top20_display_statistics(DAY, DAY)["comparisons"][0]["known_top20_eok"])
