from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from unittest.mock import patch

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.diagnostic_collector_replay import (
    _MeasuredStore, _exercise_fixture, build_collector_fixture, run_collector_fixture,
)
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from tests.collector_replay_clock import CollectorTestClock


class CollectorReplayTests(unittest.TestCase):
    def test_actual_parser_loop_preserves_values_and_distinct_checkpoint_phases(self):
        fixture = build_collector_fixture('unit-cadence')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.sqlite3'
            store = SQLiteQueryStore(path)
            store.initialize()
            clock = CollectorTestClock(fixture.origin)
            measured = _MeasuredStore(store, clock)
            try:
                with patch('kiwoom_monitor.central_server.realtime_collector.connect',
                           side_effect=AssertionError('must not open Kiwoom WebSocket')):
                    result = asyncio.run(_exercise_fixture(measured, fixture, Event(), clock))
                self.assertEqual('complete', result['state'])
                self.assertEqual('correctness_only', result['fidelity'])
                self.assertFalse(result['input_timing_preserved'])
                self.assertEqual(362, result['input_messages'])
                self.assertEqual(0, result['pending_records_after_drain'])
                actual = store.load_minute_bars(fixture.code, fixture.origin.date().isoformat())
                fields = ('minute', 'market', 'open', 'high', 'low', 'close', 'volume', 'trade_value_million_won')
                signature = lambda values: sorted(tuple(row[k] for k in fields) for row in values)
                self.assertEqual(signature(fixture.minute_rows), signature(actual))
                with closing(sqlite3.connect(path)) as db:
                    seconds = db.execute('SELECT trade_second,market,open,high,low,close,volume,trade_value_won,trade_count '
                                         'FROM central_second_trade_bars ORDER BY trade_second,market').fetchall()
                    latest = db.execute('SELECT count(*) FROM central_realtime_latest').fetchone()[0]
                second_fields = ('trade_second', 'market', 'open', 'high', 'low', 'close', 'volume', 'trade_value_won', 'trade_count')
                self.assertEqual(sorted(tuple(row[k] for k in second_fields) for row in fixture.second_rows), seconds)
                self.assertEqual(1, latest)
                periodic = [call for call in result['calls'] if call['phase'] == 'measurement']
                expected = {'realtime_latest': [12, 312], 'realtime_second_bar': [47, 107, 167, 227, 287, 347],
                            'realtime_minute': [4, 64, 124, 184, 244, 304],
                            'realtime_minute_finalize': [4, 64, 124, 184, 244, 304]}
                for kind, offsets in expected.items():
                    times = [(datetime.fromisoformat(call['source_time']) - fixture.origin).total_seconds()
                             for call in periodic if call['kind'] == kind]
                    self.assertEqual(offsets, times, kind)
                self.assertEqual(3, len([call for call in result['calls'] if call['phase'] == 'drain']))
                self.assertTrue(all(call['flush_id'] for call in result['calls']))
            finally:
                store.close()

    def test_replay_input_rejects_account_data_and_network_start(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as directory:
                store = SQLiteQueryStore(Path(directory) / 'input.sqlite3')
                store.initialize()
                collector = CentralRealtimeCollector(lambda: 'never', 'real', RealtimeHub(), datetime.now, store)
                try:
                    await collector.start_input_replay()
                    with self.assertRaisesRegex(RuntimeError, 'CANNOT_OPEN_NETWORK'):
                        await collector.start()
                    for rows in ([{'type': '00'}], [{'type': '0B'}, {'type': '04'}], []):
                        with self.assertRaisesRegex(ValueError, 'collector_message_invalid'):
                            collector.accept_replay_message({'trnm': 'REAL', 'data': rows})
                finally:
                    await collector.close()
                    store.close()
        asyncio.run(exercise())

    def test_cancelled_feeder_waits_for_actual_owned_write_and_final_flush(self):
        async def exercise():
            fixture = build_collector_fixture('cancel-owner')
            with tempfile.TemporaryDirectory() as directory:
                store = SQLiteQueryStore(Path(directory) / 'cancel.sqlite3')
                store.initialize()
                entered, release, committed = Event(), Event(), Event()
                class HeldStore:
                    def __getattr__(self, name):
                        return getattr(store, name)

                    def save_minute_bars(self, values, *, observations=None):
                        entered.set()
                        if not release.wait(10):
                            raise TimeoutError('writer gate was not released')
                        store.save_minute_bars(values, observations=observations)
                        committed.set()

                clock = CollectorTestClock(fixture.origin)
                measured = _MeasuredStore(HeldStore(), clock)
                task = asyncio.create_task(_exercise_fixture(measured, fixture, Event(), clock))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    self.assertFalse(committed.is_set())
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                    self.assertTrue(committed.is_set())
                    self.assertEqual(6, len(store.load_minute_bars(fixture.code, '2026-10-06')))
                    self.assertTrue(any(call['phase'] == 'drain' and call['kind'] == 'realtime_latest'
                                        for call in measured.calls))
                finally:
                    release.set()
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    store.close()
        asyncio.run(exercise())

    def test_production_url_and_invalid_identifier_fail_before_connect(self):
        with patch('psycopg.connect', side_effect=AssertionError('no DB access')):
            with self.assertRaisesRegex(ValueError, 'dedicated_database'):
                run_collector_fixture('postgresql://host/kiwoom_monitor', 'safe-id', Event(), Event(), Event(), Event())
            with self.assertRaisesRegex(ValueError, 'identifier'):
                build_collector_fixture('../unsafe')

    def test_subscription_reset_and_gap_use_collector_continuity(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as directory:
                store = SQLiteQueryStore(Path(directory) / 'sources.sqlite3')
                store.initialize()
                now = [datetime(2026, 10, 6, 10, tzinfo=timezone(timedelta(hours=9)))]
                collector = CentralRealtimeCollector(lambda: 'never', 'real', RealtimeHub(), lambda: now[0], store)
                try:
                    await collector.start_input_replay()
                    collector.accept_replay_sources({('005930', 'KRX')}, reset_all=True)
                    message = {'trnm': 'REAL', 'data': [{'type': '0B', 'item': '005930',
                               'values': {'10': '100', '14': '1000', '15': '1', '20': '100000'}}]}
                    collector.accept_replay_message(message)
                    collector.accept_replay_gap()
                    now[0] += timedelta(seconds=3)
                    collector.accept_replay_sources({('005930', 'KRX')}, reset_all=True)
                    message['data'][0]['values']['14'] = '9000'
                    collector.accept_replay_message(message)
                    self.assertEqual(0, collector._minute_bars.pending_bars('005930', '2026-10-06')[0]['trade_value_million_won'])
                    now[0] += timedelta(minutes=1)
                    await collector.close()
                    revisions = store.load_observation_revisions_after(0, kinds=('minute_bar',), limit=100)
                    self.assertEqual('partial', revisions[-1]['payload']['capture_quality'])
                finally:
                    await collector.close()
                    store.close()
        asyncio.run(exercise())

    def test_input_path_restores_current_previous_ram_and_preserves_query_authority(self):
        from kiwoom_monitor.central_server.market_observations import minute_bar_observation, bar_observation_key
        from kiwoom_monitor.domain.market_data_contract import ObservationOrigin, DataCompleteness, DataValueKind

        async def exercise():
            with tempfile.TemporaryDirectory() as directory:
                store = SQLiteQueryStore(Path(directory) / 'ram.sqlite3')
                store.initialize()
                now = [datetime(2026, 10, 6, 10, 3, 59, tzinfo=timezone(timedelta(hours=9)))]
                sleep_gate = asyncio.Event()
                collector = CentralRealtimeCollector(lambda: 'never', 'real', RealtimeHub(), lambda: now[0],
                                                     store, snapshot_sleep=lambda _: sleep_gate.wait())
                try:
                    await collector.start_input_replay()
                    collector.accept_replay_sources({('005930', 'KRX')}, reset_all=True)
                    def feed(price, volume, trade_time):
                        collector.accept_replay_message({'trnm': 'REAL', 'data': [{'type': '0B', 'item': '005930',
                            'values': {'10': str(price), '15': str(volume), '20': trade_time}}]})
                    feed(100, 3, '100359')
                    now[0] += timedelta(seconds=41)
                    feed(110, 5, '100440')
                    self.assertEqual([], store.load_minute_bars('005930', '2026-10-06'))
                    live = await collector.load_live_minute_bars('005930', '2026-10-06', 'KRX')
                    self.assertEqual([('10:03', 3), ('10:04', 5)], [(row['minute'], row['volume']) for row in live])
                    canonical = {**live[1], 'volume': 99, 'updated_at': now[0].timestamp()}
                    observation = minute_bar_observation(canonical, origin=ObservationOrigin.QUERY,
                        completeness=DataCompleteness.COMPLETE, source='kiwoom-ka10080', value_kind=DataValueKind.ACTUAL)
                    store.replace_minute_bars([canonical], observations=[(bar_observation_key(observation), observation)])
                    live = await collector.load_live_minute_bars('005930', '2026-10-06', 'KRX')
                    self.assertEqual([3, 99], [row['volume'] for row in live])
                    await collector.close()
                    self.assertEqual(99, store.load_minute_bars('005930', '2026-10-06', 'KRX')[1]['volume'])
                finally:
                    await collector.close()
                    store.close()
        asyncio.run(exercise())
