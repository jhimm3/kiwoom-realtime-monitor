"""Desired reader contract under reversed native writer COMMIT visibility.

This is a correctness reproducer, not a performance benchmark or an expected-fail
acceptance waiver. A failure at the final assertion means a late committed input
exists in storage but is permanently behind the consumer's incremental cursor.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from threading import Event
import unittest
from urllib.parse import urlsplit
import uuid

from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server.market_observations import bar_observation_key, minute_bar_observation
from kiwoom_monitor.domain.market_data_contract import DataCompleteness, DataValueKind, ObservationOrigin


class ObservationCursorCommitOrderPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not cls.url:
            raise unittest.SkipTest('dedicated diagnostic PostgreSQL URL is required')
        if urlsplit(cls.url).path != '/kiwoom_monitor_diagnostic_test':
            raise RuntimeError('commit-order reproducer requires diagnostic database')
        cls.store = PostgresQueryStore(cls.url)
        cls.store.initialize()

    def _closed_minutes_through_shadow(self, *, reverse_commits, bootstrap_during_gap=False):
        """Exercise native finalization, processing and saved restart checkpoints."""
        import psycopg
        from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
        from kiwoom_monitor.application.research_replay import replay_krx_minute_bars
        from kiwoom_monitor.central_server.candidate_monitor import CandidateMonitor

        token = uuid.uuid4().hex[:12]
        codes = ['CC' + token, 'CD' + token]
        subjects = [code + ':KRX' for code in codes]
        profile = 'krx-regular/v1'  # The fixture is a weekday regular-session minute.
        config = replace(default_shadow_breakout_config(),
                         capital_won=1_000_000 + int(token[:8], 16))
        entered, release = Event(), Event()
        monitor_ids = []
        seed_at = datetime(2099, 1, 14, 10, 1, 10,
                           tzinfo=timezone(timedelta(hours=9))).timestamp()
        values = [{'trading_date': '2099-01-14', 'minute': '10:01', 'code': code,
                   'market': 'KRX', 'open': 100, 'high': 110, 'low': 90, 'close': 105,
                   'volume': 10, 'trade_value_million_won': 2, 'updated_at': seed_at,
                   'operation_id': 'closed-cursor-seed-' + code} for code in codes]
        closures = [{**{key: row[key] for key in ('trading_date', 'minute', 'code', 'market')},
                     'available_at': seed_at + 60, 'capture_quality': 'complete',
                     'finalization_source': 'timer', 'operation_id': 'closed-cursor-final-' + row['code']}
                    for row in values]

        class HeldCommit(psycopg.Connection):
            def commit(self):
                entered.set()
                if not release.wait(30):
                    raise TimeoutError('held finalization COMMIT was not released')
                return super().commit()

        def open_monitor(configuration):
            monitor = CandidateMonitor(self.store, configuration, poll_seconds=1,
                                       universe_max_age_seconds=300, session_profile=profile)
            monitor_ids.append(monitor.monitor_id)
            return monitor

        try:
            observations = []
            for value in values:
                observation = minute_bar_observation(
                    value, origin=ObservationOrigin.REALTIME, completeness=DataCompleteness.IN_PROGRESS,
                    source='kiwoom-websocket-0B', value_kind=DataValueKind.ACTUAL)
                observations.append((bar_observation_key(observation), observation))
            self.store.save_minute_bars(values, observations=observations)
            if not bootstrap_during_gap:
                monitor = open_monitor(config)
                self.assertEqual([], self.store.load_shadow_monitor_state(monitor.monitor_id)['bars'])

            if reverse_commits:
                held_store = PostgresQueryStore(self.url)
                held_store._connect = lambda: HeldCommit.connect(self.url)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(held_store.finalize_minute_bars, [closures[0]])
                    try:
                        self.assertTrue(entered.wait(10), 'native finalizer must reach COMMIT')
                        self.store.finalize_minute_bars([closures[1]])
                        if bootstrap_during_gap:
                            monitor = open_monitor(config)
                            self.assertEqual(0, monitor.run_once())
                        else:
                            self.assertEqual(1, monitor.run_once())
                        first_checkpoint = self.store.load_shadow_monitor_state(monitor.monitor_id)
                        self.assertEqual({codes[1]}, {frame['code'] for frame in first_checkpoint['bars']})
                    finally:
                        release.set()
                    future.result(timeout=10)
            else:
                self.store.finalize_minute_bars([closures[0]])
                self.assertEqual(1, monitor.run_once())
                self.store.finalize_minute_bars([closures[1]])
                self.assertEqual(1, monitor.run_once())

            stored = self.store.load_observation_revisions('minute_bar', subjects[0])
            [closed] = replay_krx_minute_bars(stored, strict=True, session_profile=profile)
            self.assertEqual(codes[0], closed.code)  # The omitted closed input exists and is eligible.
            if reverse_commits:
                [closed_sequence] = [row['accepted_sequence'] for row in stored
                                     if row['revision_id'] == closed.revision_id]
                self.assertLess(closed_sequence, first_checkpoint['cursor'])
            monitor.run_once()
            checkpoint = self.store.load_shadow_monitor_state(monitor.monitor_id)
            restarted = open_monitor(config)
            restarted.run_once()
            recovered = self.store.load_shadow_monitor_state(restarted.monitor_id)
            self.assertEqual(checkpoint, recovered)

            # A new consumer without the advanced checkpoint can see both completed bars.
            bootstrapped = open_monitor(replace(config, capital_won=config.capital_won + 1))
            full = self.store.load_shadow_monitor_state(bootstrapped.monitor_id)
            self.assertEqual(set(codes), {frame['code'] for frame in full['bars']})
            return set(codes), {frame['code'] for frame in recovered['bars']}
        finally:
            release.set()
            with self.store._connect() as connection, connection.cursor() as cursor:
                # Deleting the unique checkpoint parent also removes normalized frames.
                for table in ('central_shadow_candidate_events', 'central_shadow_decisions',
                              'central_shadow_monitor_state'):
                    cursor.execute(f'DELETE FROM {table} WHERE monitor_id=ANY(%s)', (monitor_ids,))
                cursor.execute("DELETE FROM central_observation_revisions WHERE kind='minute_bar' AND subject=ANY(%s)",
                               (subjects,))
                cursor.execute("DELETE FROM central_market_data_observation_meta WHERE dataset_kind='minute_bar' AND subject=ANY(%s)",
                               (subjects,))
                cursor.execute('DELETE FROM central_minute_bar_operations WHERE operation_id=ANY(%s)',
                               ([row['operation_id'] for row in [*values, *closures]],))
                cursor.execute('DELETE FROM central_minute_bars WHERE code=ANY(%s)', (codes,))

    def test_closed_minutes_committed_in_order_restore_shadow_checkpoint(self):
        expected, recovered = self._closed_minutes_through_shadow(reverse_commits=False)
        self.assertEqual(expected, recovered)

    def test_closed_minute_late_commit_reaches_shadow_checkpoint_and_restart(self):
        expected, recovered = self._closed_minutes_through_shadow(reverse_commits=True)
        self.assertEqual(expected, recovered,
                         'advanced shadow checkpoint must not permanently skip a completed late COMMIT')

    def test_shadow_bootstrap_during_peer_commit_keeps_late_closed_minute_after_restart(self):
        expected, recovered = self._closed_minutes_through_shadow(
            reverse_commits=True, bootstrap_during_gap=True,
        )
        self.assertEqual(expected, recovered,
                         'initial bootstrap must not checkpoint past a pending closed-minute COMMIT')

    def test_incremental_reader_keeps_lower_sequence_committed_after_peer(self):
        import psycopg

        entered, release = Event(), Event()
        token = uuid.uuid4().hex[:12]
        codes = ['CA' + token, 'CB' + token]
        subjects = [code + ':KRX' for code in codes]
        observed_at = datetime(2099, 1, 14, 10, 1, 10, tzinfo=timezone(timedelta(hours=9)))
        values = [{'trading_date': '2099-01-14', 'minute': '10:01', 'code': code,
                   'market': 'KRX', 'open': 100, 'high': 110, 'low': 90, 'close': 105,
                   'volume': 10, 'trade_value_million_won': 2, 'updated_at': observed_at.timestamp(),
                   'operation_id': 'cursor-visibility-' + code} for code in codes]

        class HeldCommit(psycopg.Connection):
            def commit(self):
                entered.set()  # Native SQL and sequence allocation already happened.
                if not release.wait(15):
                    raise TimeoutError('held native COMMIT was not released')
                return super().commit()

        held_store = PostgresQueryStore(self.url)
        held_store._connect = lambda: HeldCommit.connect(self.url)

        def save(store, row):
            observation = minute_bar_observation(
                row, origin=ObservationOrigin.REALTIME, completeness=DataCompleteness.IN_PROGRESS,
                source='kiwoom-websocket-0B', value_kind=DataValueKind.ACTUAL)
            store.save_minute_bars([row], observations=[(bar_observation_key(observation), observation)])

        try:
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute('SELECT COALESCE(MAX(accepted_sequence),0) FROM central_observation_revisions')
                starting_cursor = cursor.fetchone()[0]
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(save, held_store, values[0])
                try:
                    self.assertTrue(entered.wait(10), 'first writer must reach native COMMIT')
                    save(self.store, values[1])  # Separate stock/day locks and independent COMMIT.
                    first_page = self.store.load_observation_revisions_after(starting_cursor, ('minute_bar',), 1000)
                    selected = [row for row in first_page if row['subject'] in subjects]
                    self.assertEqual([subjects[1]], [row['subject'] for row in selected])
                    advanced_cursor = max(row['accepted_sequence'] for row in first_page)
                finally:
                    release.set()
                future.result(timeout=10)
            [late] = self.store.load_observation_revisions('minute_bar', subjects[0])
            self.assertLess(late['accepted_sequence'], advanced_cursor)
            next_page = self.store.load_observation_revisions_after(advanced_cursor, ('minute_bar',), 1000)
            self.assertIn(late['revision_id'], [row['revision_id'] for row in next_page],
                          'late COMMIT is stored but missing from an advanced incremental cursor')
        finally:
            release.set()
            with self.store._connect() as connection, connection.cursor() as cursor:
                cursor.execute("DELETE FROM central_observation_revisions WHERE kind='minute_bar' AND subject=ANY(%s)",
                               (subjects,))
                cursor.execute("DELETE FROM central_market_data_observation_meta WHERE dataset_kind='minute_bar' AND subject=ANY(%s)",
                               (subjects,))
                cursor.execute('DELETE FROM central_minute_bar_operations WHERE operation_id=ANY(%s)',
                               ([row['operation_id'] for row in values],))
                cursor.execute('DELETE FROM central_minute_bars WHERE code=ANY(%s)', (codes,))
