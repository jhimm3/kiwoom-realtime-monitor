from __future__ import annotations

import unittest
import asyncio
import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.realtime_collector import (
    CentralRealtimeCollector,
    REALTIME_ITEMS_PER_TYPE,
    _registered_trade_sources,
    _next_latest_checkpoint,
    _next_second_checkpoint,
    contains_krx_observation,
)
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.domain.market_data_contract import (
    CandidateUniverse,
    DataCompleteness,
    DataValueKind,
    MarketDatasetKind,
    ObservationOrigin,
    TradingVenue,
)


class CentralRealtimeCollectorTests(unittest.TestCase):
    def test_live_read_keeps_inflight_and_lost_ack_delta_visible_without_double_count(self) -> None:
        from threading import Event
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / 'live.sqlite3')
            store.initialize()
            entered, release = Event(), Event()

            class LostAckStore:
                def save_minute_bars(self, rows, *, observations=None):
                    entered.set()
                    if not release.wait(5):
                        raise TimeoutError('test did not release writer')
                    store.save_minute_bars(rows, observations=observations)
                    raise OSError('injected lost commit acknowledgement')

                def load_minute_bars(self, *args, **kwargs):
                    return store.load_minute_bars(*args, **kwargs)

            at = datetime(2026, 10, 6, 10, 4, 40)
            collector = CentralRealtimeCollector(lambda: 'token', 'real', RealtimeHub(), lambda: at, LostAckStore())
            collector._minute_bars.add(TradeTick('005930', 100, None, None, 2, None, '100440'), at, at.timestamp())

            async def exercise():
                flush = asyncio.create_task(collector._flush_snapshots(flush_latest=False, flush_seconds=False))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                    [during] = await collector.load_live_minute_bars('005930', '2026-10-06', 'KRX')
                    self.assertEqual(2, during['volume'])
                    self.assertEqual([], store.load_minute_bars('005930', '2026-10-06', 'KRX'))
                finally:
                    release.set()
                    with self.assertRaisesRegex(OSError, 'lost commit'):
                        await flush
                collector._minute_bars.add(TradeTick('005930', 110, None, None, 3, None, '100441'), at, at.timestamp())
                [after] = await collector.load_live_minute_bars('005930', '2026-10-06', 'KRX')
                self.assertEqual(5, after['volume'])
                self.assertEqual(2, store.load_minute_bars('005930', '2026-10-06', 'KRX')[0]['volume'])

            try:
                asyncio.run(exercise())
            finally:
                release.set()
                store.close()

    def test_delayed_bar_cadence_persists_late_trades_and_final_closed_metadata(self) -> None:
        import sqlite3
        from contextlib import closing
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cadence.sqlite3'
            store = SQLiteQueryStore(path)
            store.initialize()
            now = [datetime(2026, 10, 6, 10, 4, 59)]
            collector = CentralRealtimeCollector(lambda: 'token', 'real', RealtimeHub(), lambda: now[0], store)

            async def exercise():
                tick = TradeTick('005930', 100, 1, 1000, 2, None, '100459')
                collector._minute_bars.add(tick, now[0], now[0].timestamp())
                collector._second_trades.add(tick, now[0], now[0].timestamp())
                await collector._flush_snapshots(flush_latest=False, periodic=True, flush_seconds=False)
                self.assertFalse(store.load_minute_bars('005930', '2026-10-06', 'KRX'))
                now[0] = datetime(2026, 10, 6, 10, 5, 2)
                late = TradeTick('005930', 110, 2, 1010, 3, None, '100459')
                collector._minute_bars.add(late, now[0], now[0].timestamp())
                collector._second_trades.add(late, now[0], now[0].timestamp())
                await collector._flush_snapshots(flush_latest=False, periodic=True, flush_seconds=False)
                [bar] = store.load_minute_bars('005930', '2026-10-06', 'KRX')
                self.assertEqual((100, 110, 100, 110, 5), tuple(bar[key] for key in ('open', 'high', 'low', 'close', 'volume')))
                revisions = store.load_observation_revisions_after(0, kinds=('minute_bar',), limit=100)
                self.assertTrue(revisions[-1]['payload']['window_closed'])
                now[0] = datetime(2026, 10, 6, 10, 5, 45)
                await collector._flush_snapshots(flush_latest=False, periodic=True)
                with closing(sqlite3.connect(path)) as db:
                    row = db.execute('SELECT volume,trade_count FROM central_second_trade_bars').fetchone()
                self.assertEqual((5, 2), row)

            try:
                asyncio.run(exercise())
            finally:
                store.close()

    def test_periodic_loop_delays_second_writer_until_forty_five(self) -> None:
        from unittest.mock import patch
        clock = [datetime(2026, 10, 6, 10, 5, 43).timestamp()]
        calls = []

        async def sleep(_):
            clock[0] += 1
            if len(calls) == 3:
                raise asyncio.CancelledError

        async def flush(**kwargs):
            calls.append(kwargs)

        collector = CentralRealtimeCollector(
            lambda: 'token', 'real', RealtimeHub(), lambda: datetime.fromtimestamp(clock[0]),
            snapshot_sleep=sleep,
        )

        async def exercise():
            with patch.object(collector, '_flush_snapshots', side_effect=flush):
                with self.assertRaises(asyncio.CancelledError):
                    await collector._save_snapshots()

        asyncio.run(exercise())
        self.assertEqual([False, True, False], [call['flush_seconds'] for call in calls])
        self.assertTrue(all(call['periodic'] for call in calls))
        self.assertTrue(all(not call['flush_latest'] for call in calls))

    def test_slow_closed_minute_save_does_not_finalize_still_buffered_next_minute(self) -> None:
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
        now = [datetime(2026, 10, 6, 10, 5, 2)]

        class Store:
            def __init__(self):
                self.saved, self.closed, self.available = [], [], []

            def save_minute_bars(self, rows, *, observations=None):
                self.saved.extend(row['minute'] for row in rows)
                # Simulate a DB stall crossing the next minute's close.
                now[0] = datetime(2026, 10, 6, 10, 6, 2)

            def finalize_minute_bars(self, rows):
                self.closed.extend(row['minute'] for row in rows)
                self.available.extend(row['available_at'] for row in rows)

        store = Store()
        collector = CentralRealtimeCollector(lambda: 'token', 'real', RealtimeHub(), lambda: now[0], store)
        for minute in (4, 5):
            at = datetime(2026, 10, 6, 10, minute, 1)
            collector._minute_bars.add(TradeTick('005930', 100, None, None, 1, None, at.strftime('%H%M%S')),
                                       at, at.timestamp())

        async def exercise():
            await collector._flush_snapshots(flush_latest=False, periodic=True, flush_seconds=False)
            self.assertEqual(['10:04'], store.saved)
            self.assertEqual(['10:04'], store.closed)
            self.assertEqual([now[0].timestamp()], store.available)
            await collector._flush_snapshots(flush_latest=False, periodic=True, flush_seconds=False)

        asyncio.run(exercise())
        self.assertEqual(['10:04', '10:05'], store.saved)
        self.assertEqual(['10:04', '10:05'], store.closed)

    def test_second_checkpoint_uses_next_minute_forty_five_seconds(self) -> None:
        start = datetime(2026, 10, 6, 10, 5, tzinfo=timezone(timedelta(hours=9))).timestamp()
        self.assertEqual(start + 45, _next_second_checkpoint(start))
        self.assertEqual(start + 45, _next_second_checkpoint(start + 44.9))
        self.assertEqual(start + 105, _next_second_checkpoint(start + 45))

    def test_periodic_bars_keep_open_minute_and_save_previous_seconds_at_forty_five(self) -> None:
        class Store:
            def __init__(self):
                self.minutes, self.seconds, self.closures, self.order, self.documents = [], [], [], [], []

            def save_minute_bars(self, rows, *, observations=None):
                self.minutes.extend(rows)
                self.order.append('minute')

            def finalize_minute_bars(self, rows):
                self.closures.extend(rows)
                self.order.append('finalize')

            def save_second_trade_bars(self, rows):
                self.seconds.extend(rows)

            def upsert_documents(self, collection, rows):
                self.documents.append(collection)

        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
        now = [datetime(2026, 10, 6, 10, 4, 59)]
        store = Store()
        collector = CentralRealtimeCollector(lambda: 'token', 'real', RealtimeHub(), lambda: now[0], store)

        def add(at, volume, cumulative):
            now[0] = at
            tick = TradeTick('005930', 100, cumulative, cumulative, volume, None, at.strftime('%H%M%S'))
            collector._minute_bars.add(tick, at, at.timestamp())
            collector._second_trades.add(tick, at, at.timestamp())

        async def exercise():
            add(now[0], 2, 1)
            collector._pending_stock_references['005930'] = {'owner': '005930', 'key': 'reference'}
            await collector._flush_snapshots(flush_latest=False, periodic=True, flush_seconds=False)
            self.assertFalse(store.minutes)
            self.assertFalse(store.seconds)
            self.assertEqual(['stock_price_references'], store.documents)
            add(datetime(2026, 10, 6, 10, 5, 1), 3, 2)
            await collector._flush_snapshots(flush_latest=False, periodic=True, flush_seconds=False)
            self.assertFalse(store.minutes)
            now[0] = datetime(2026, 10, 6, 10, 5, 2)
            await collector._flush_snapshots(flush_latest=False, periodic=True, flush_seconds=False)
            self.assertEqual(['minute', 'finalize'], store.order)
            self.assertEqual(['10:04'], [row['minute'] for row in store.minutes])
            self.assertEqual(2, store.minutes[0]['volume'])
            self.assertFalse(store.seconds)
            now[0] = datetime(2026, 10, 6, 10, 5, 45)
            await collector._flush_snapshots(flush_latest=False, periodic=True)
            self.assertEqual(['10:04:59'], [row['trade_second'] for row in store.seconds])
            self.assertEqual(['10:04'], [row['minute'] for row in store.minutes])
            # Normal shutdown flushes the open minute and all deferred seconds.
            await collector.close()
            self.assertEqual(['10:04', '10:05'], [row['minute'] for row in store.minutes])
            self.assertEqual(['10:04:59', '10:05:01'], [row['trade_second'] for row in store.seconds])

        asyncio.run(exercise())

    def test_failed_delayed_seconds_retry_without_waiting_for_next_deadline(self) -> None:
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick

        class Store:
            def __init__(self):
                self.calls, self.saved = 0, []

            def save_second_trade_bars(self, rows):
                self.calls += 1
                if self.calls == 1:
                    raise OSError('delayed second batch failed')
                self.saved.extend(rows)

        store = Store()
        at = datetime(2026, 10, 6, 10, 4, 59)
        collector = CentralRealtimeCollector(
            lambda: 'token', 'real', RealtimeHub(), lambda: at.replace(minute=5, second=45), store,
        )
        collector._second_trades.add(TradeTick('005930', 100, 1, 1, 2, None, '100459'), at, at.timestamp())

        async def exercise():
            with self.assertRaises(OSError):
                await collector._flush_snapshots(flush_latest=False, periodic=True)
            await collector._flush_snapshots(flush_latest=False, periodic=True, flush_seconds=False)

        asyncio.run(exercise())
        self.assertEqual(2, store.calls)
        self.assertEqual(2, store.saved[0]['volume'])

    def test_latest_checkpoint_uses_five_minute_boundary_plus_ten_seconds(self) -> None:
        kst = timezone(timedelta(hours=9))
        at_nine = datetime(2026, 10, 6, 9, tzinfo=kst).timestamp()
        self.assertEqual(at_nine + 10, _next_latest_checkpoint(at_nine))
        self.assertEqual(at_nine + 310, _next_latest_checkpoint(at_nine + 10))
        self.assertEqual(at_nine + 310, _next_latest_checkpoint(at_nine + 309.9))

    def test_periodic_flush_keeps_latest_pending_but_normal_close_saves_it(self) -> None:
        class Store:
            def __init__(self) -> None:
                self.saved = []

            def save_realtime_snapshots(self, values):
                self.saved.extend(values)

        store = Store()
        collector = CentralRealtimeCollector(
            lambda: "token", "real", RealtimeHub(), lambda: datetime(2026, 10, 6, 9), store,
        )
        snapshot = {
            "event_type": "trade", "item_key": "005930", "received_at": 1.0,
            "event": {"type": "trade", "payload": {"code": "005930"}},
        }
        collector._pending_snapshots[("trade", "005930")] = snapshot

        async def exercise() -> None:
            await collector._flush_snapshots(flush_latest=False)
            self.assertEqual([], store.saved)
            self.assertEqual(snapshot, collector._pending_snapshots[("trade", "005930")])
            await collector.close()

        asyncio.run(exercise())
        self.assertEqual([snapshot], store.saved)
        self.assertFalse(collector._pending_snapshots)

    def test_subscribe_uses_fresh_ram_snapshot_over_older_db_checkpoint(self) -> None:
        collector = CentralRealtimeCollector(
            lambda: "token", "real", RealtimeHub(), lambda: datetime(2026, 10, 6, 9),
        )
        old = {"type": "trade", "payload": {"code": "005930", "current_price": 100}}
        current = {"type": "trade", "payload": {"code": "005930", "current_price": 101}}
        other = {"type": "trade", "payload": {"code": "000660", "current_price": 200}}
        collector._latest_snapshots[("trade", "005930")] = {
            "received_at": collector._now_provider().timestamp(), "event": current,
        }
        collector._latest_snapshots[("trade", "035420")] = {
            "received_at": collector._now_provider().timestamp(),
            "event": {"type": "trade", "payload": {"code": "035420"}},
        }
        self.assertEqual(
            [other, current],
            collector.initial_realtime_snapshots([old, other], ["005930", "000660"]),
        )

    def test_integrated_trade_is_sor_source_and_not_strict_krx_observation(self) -> None:
        message = {"trnm": "REAL", "data": [{"type": "0B", "item": "005930_AL"}]}
        groups = {"1000": [{"item": ["005930_AL", "000660", "035420_NX"], "type": ["0B"]}]}

        self.assertFalse(contains_krx_observation(message))
        self.assertEqual({
            ("005930", "SOR"), ("000660", "KRX"), ("035420", "NXT"),
        }, _registered_trade_sources(groups))

    def test_hub_keeps_cohort_out_of_program_subscription(self) -> None:
        hub = RealtimeHub()
        primary = hub.connect()
        cohort = hub.connect()
        hub.update_subscription(
            primary, ["005930", "000660"], ["005930"],
            program_codes=["005930", "000660"],
        )
        hub.update_subscription(
            cohort, ["123456"], ["123456"], program_codes=[],
        )
        self.assertEqual(("000660", "005930"), hub.requested_program_codes())

    def test_buy_execution_is_saved_as_account_neutral_market_data_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            collector = CentralRealtimeCollector(
                lambda: "token", "real", RealtimeHub(),
                lambda: datetime(2026, 9, 14, 10, 15, 30), store,
            )
            collector._publish_parsed({
                "trnm": "REAL", "data": [{"type": "00", "values": {
                    "9203": "18", "909": "7", "9001": "A005930",
                    "913": "체결", "905": "+매수", "908": "101530",
                    "910": "60700", "911": "2",
                }}],
            })

            asyncio.run(collector._flush_snapshots())
            values = store.load_documents(
                "account_entry_symbols_daily", "2026-09-14", 10,
            )
            store.close()

        self.assertEqual("005930", values[0]["key"])
        self.assertEqual("real", values[0]["document"]["environment"])
        self.assertNotIn("account", values[0]["document"])

    def test_sell_execution_does_not_create_entry_market_data_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            collector = CentralRealtimeCollector(
                lambda: "token", "real", RealtimeHub(),
                lambda: datetime(2026, 9, 14, 10, 15, 30), store,
            )
            collector._publish_parsed({
                "trnm": "REAL", "data": [{"type": "00", "values": {
                    "9203": "18", "909": "7", "9001": "A005930",
                    "913": "체결", "905": "-매도", "908": "101530",
                    "910": "60700", "911": "2",
                }}],
            })
            asyncio.run(collector._flush_snapshots())
            values = store.load_documents(
                "account_entry_symbols_daily", "2026-09-14", 10,
            )
            store.close()

        self.assertEqual([], values)

    def test_null_realtime_data_is_not_a_disconnect_or_krx_observation(self) -> None:
        collector = CentralRealtimeCollector(
            lambda: "token", "real", RealtimeHub(),
            lambda: datetime(2026, 9, 14, 8, 55),
        )
        message = {"trnm": "REAL", "data": None}

        self.assertFalse(contains_krx_observation(message))
        collector._publish_parsed(message)

    def test_market_operation_0s_is_published_without_changing_market_data_semantics(self) -> None:
        hub = RealtimeHub()
        subscriber = hub.connect()
        collector = CentralRealtimeCollector(
            lambda: "token", "real", hub, lambda: datetime(2026, 9, 14, 9),
        )

        collector._publish_parsed({"trnm": "REAL", "data": [{
            "type": "0s", "item": "", "values": {"215": "3", "20": "090000"},
        }]})

        self.assertEqual("market_operation", subscriber.queue.get_nowait()["type"])

    def test_failed_snapshot_flush_keeps_values_for_retry(self) -> None:
        class FlakyStore:
            def __init__(self) -> None:
                self.calls = 0
                self.saved = []

            def save_realtime_snapshots(self, values):
                self.calls += 1
                if self.calls == 1:
                    raise OSError("temporary failure")
                self.saved.extend(values)

        store = FlakyStore()
        collector = CentralRealtimeCollector(
            lambda: "token", "real", RealtimeHub(), lambda: datetime(2026, 9, 10, 10), store,
        )
        collector._pending_snapshots[("trade", "005930")] = {
            "event_type": "trade", "item_key": "005930", "received_at": 1.0,
            "event": {"type": "trade"},
        }

        with self.assertRaises(OSError):
            asyncio.run(collector._flush_snapshots())
        self.assertIn(("trade", "005930"), collector._pending_snapshots)

        asyncio.run(collector._flush_snapshots())
        self.assertEqual(2, store.calls)
        self.assertEqual(1, len(store.saved))
        self.assertFalse(collector._pending_snapshots)

    def test_failed_minute_flush_merges_retry_without_losing_increment(self) -> None:
        class FlakyStore:
            def __init__(self) -> None:
                self.calls = 0
                self.saved = []

            def save_minute_bars(self, values, *, observations=None):
                self.calls += 1
                if self.calls == 1:
                    raise OSError("temporary failure")
                self.saved.extend(values)

        store = FlakyStore()
        collector = CentralRealtimeCollector(
            lambda: "token", "real", RealtimeHub(), lambda: datetime(2026, 9, 10, 10), store,
        )
        collector._merge_minute_retry({
            "trading_date": "2026-09-10", "minute": "10:00", "code": "005930", "market": "KRX",
            "open": 100, "high": 101, "low": 99, "close": 101, "volume": 3,
            "trade_value_million_won": 7, "updated_at": 1.0, "operation_id": "first",
        })

        with self.assertRaises(OSError):
            asyncio.run(collector._flush_snapshots())
        collector._merge_minute_retry({
            "trading_date": "2026-09-10", "minute": "10:00", "code": "005930", "market": "KRX",
            "open": 101, "high": 103, "low": 100, "close": 102, "volume": 5,
            "trade_value_million_won": 11, "updated_at": 2.0, "operation_id": "second",
        })

        asyncio.run(collector._flush_snapshots())
        self.assertEqual(2, store.calls)
        self.assertEqual(8, sum(value["volume"] for value in store.saved))
        self.assertEqual(18, sum(value["trade_value_million_won"] for value in store.saved))
        self.assertEqual({"first", "second"}, {value["operation_id"] for value in store.saved})

    def test_parsed_trade_is_published_only_to_matching_client(self) -> None:
        hub = RealtimeHub()
        samsung, hynix = hub.connect(), hub.connect()
        hub.update_subscription(samsung, ["005930"], [])
        hub.update_subscription(hynix, ["000660"], [])
        collector = CentralRealtimeCollector(lambda: "token", "real", hub, lambda: datetime(2026, 9, 8, 10))
        collector._publish_parsed({
            "trnm": "REAL",
            "data": [{"type": "0B", "item": "005930", "values": {"10": "+100", "15": "2"}}],
        })
        event = samsung.queue.get_nowait()
        self.assertEqual("trade", event["type"])
        self.assertEqual("005930", event["payload"]["code"])
        self.assertTrue(hynix.queue.empty())

    def test_market_session_uses_krx_and_nxt_hours(self) -> None:
        hub = RealtimeHub()
        now = [datetime(2026, 9, 8, 8, 30)]
        collector = CentralRealtimeCollector(lambda: "token", "real", hub, lambda: now[0])
        self.assertEqual("NXT", collector._market_session())
        now[0] = datetime(2026, 9, 8, 9, 0)
        self.assertEqual("KRX", collector._market_session())
        now[0] = datetime(2026, 9, 8, 20, 0)
        self.assertIsNone(collector._market_session())

    def test_effective_date_keeps_krx_connection_open_until_twenty(self) -> None:
        now = [datetime(2026, 9, 14, 16, 0)]
        collector = CentralRealtimeCollector(lambda: "token", "real", RealtimeHub(), lambda: now[0])
        self.assertEqual("KRX", collector._market_session())
        now[0] = datetime(2026, 9, 14, 20, 0)
        self.assertIsNone(collector._market_session())

    def test_regular_close_and_full_day_close_are_notified_separately_once(self) -> None:
        class Events:
            def __init__(self): self.calls = []
            async def close_krx_regular_session(self, session): self.calls.append(("regular", session))
            async def close_observation_day(self, session): self.calls.append(("full", session))

        now = [datetime(2026, 9, 14, 15, 30)]
        events = Events()
        collector = CentralRealtimeCollector(
            lambda: "token", "real", RealtimeHub(), lambda: now[0], market_events=events,
        )
        asyncio.run(collector._notify_market_event_boundaries())
        asyncio.run(collector._notify_market_event_boundaries())
        now[0] = datetime(2026, 9, 14, 20, 0)
        asyncio.run(collector._notify_market_event_boundaries())
        self.assertEqual([
            ("regular", "2026-09-14"), ("full", "2026-09-14"),
        ], events.calls)

    def test_minute_capture_requires_subscription_from_window_start(self) -> None:
        now = datetime(2026, 9, 8, 10, 15, 30)
        collector = CentralRealtimeCollector(lambda: "token", "real", RealtimeHub(), lambda: now)
        tick = type("Tick", (), {"code": "005930", "market": "KRX", "trade_time": "101530"})()
        collector._continuous_from[("005930", "KRX")] = now.replace(second=0)
        self.assertTrue(collector._minute_capture_complete(tick, now))
        collector._continuous_from[("005930", "KRX")] = now.replace(second=10)
        self.assertFalse(collector._minute_capture_complete(tick, now))

    def test_nxt_subscription_excludes_ineligible_codes(self) -> None:
        class Socket:
            def __init__(self): self.sent = []
            async def send(self, value): self.sent.append(json.loads(value))

        hub = RealtimeHub()
        collector = CentralRealtimeCollector(lambda: "token", "real", hub, lambda: datetime(2026, 9, 8, 8, 30))
        socket = Socket()
        asyncio.run(collector._send_subscription(socket, "NXT", ("005930", "000660"), ("005930",)))
        self.assertEqual(["005930_NX"], socket.sent[0]["data"][0]["item"])

    def test_krx_market_event_subscription_adds_marketwide_vi_without_duplicate_codes(self) -> None:
        class Socket:
            def __init__(self): self.sent = []
            async def send(self, value): self.sent.append(json.loads(value))

        collector = CentralRealtimeCollector(
            lambda: "token", "real", RealtimeHub(), lambda: datetime(2026, 9, 8, 10),
            market_events=object(),
        )
        socket = Socket()
        asyncio.run(collector._send_subscription(socket, "KRX", ("005930",), ("005930",)))
        self.assertEqual(["005930", "005930_NX"], socket.sent[0]["data"][0]["item"])
        self.assertEqual("0", socket.sent[0]["refresh"])
        self.assertTrue(any(
            row == {"item": [""], "type": ["0s"]}
            for packet in socket.sent for row in packet["data"]
        ))
        self.assertEqual([{"item": [], "type": ["1h"]}],
                         [row for packet in socket.sent for row in packet["data"] if row["type"] == ["1h"]])

    def test_large_central_subscription_is_split_and_replaces_each_group(self) -> None:
        class Socket:
            def __init__(self): self.sent = []
            async def send(self, value): self.sent.append(json.loads(value))

        codes = tuple(f"{index:06d}" for index in range(117))
        nxt_codes = codes[:97]
        collector = CentralRealtimeCollector(
            lambda: "token", "real", RealtimeHub(), lambda: datetime(2026, 9, 14, 16, 30),
            market_events=object(),
        )
        socket = Socket()
        groups = asyncio.run(collector._send_subscription(
            socket, "KRX", codes, nxt_codes, codes[:20],
        ))

        registrations = [value for value in socket.sent if value["trnm"] == "REG"]
        self.assertEqual(6, len(registrations))
        self.assertTrue(all(value["refresh"] == "0" for value in registrations))
        self.assertTrue(all(
            len(row["item"]) <= 100
            for value in registrations for row in value["data"]
        ))
        trade_items = [
            item for value in registrations for row in value["data"]
            if row["type"] == ["0B"] for item in row["item"]
        ]
        self.assertIn("000000", trade_items)
        self.assertIn("000000_NX", trade_items)
        self.assertIn("000096_AL", trade_items)
        program_items = [
            item for value in registrations for row in value["data"]
            if row["type"] == ["0w"] for item in row["item"]
        ]
        self.assertLessEqual(len(trade_items), REALTIME_ITEMS_PER_TYPE)
        self.assertEqual([f"{code}_AL" for code in codes[:20]], program_items)
        reference_items = [
            item for value in registrations for row in value["data"]
            if row["type"] == ["0g"] for item in row["item"]
        ]
        self.assertEqual(list(codes), reference_items)
        self.assertEqual(set(groups), {value["grp_no"] for value in registrations})

    def test_0b_and_program_limits_are_independent_and_detail_uses_priority(self) -> None:
        class Socket:
            def __init__(self): self.sent = []
            async def send(self, value): self.sent.append(json.loads(value))

        codes = tuple(f"{index:06d}" for index in range(110))
        priority = ("000109", "000108", *codes[:38])
        collector = CentralRealtimeCollector(
            lambda: "token", "real", RealtimeHub(), lambda: datetime(2026, 9, 14, 16, 30),
        )
        socket = Socket()
        asyncio.run(collector._send_subscription(
            socket, "KRX", codes, codes, codes, priority,
        ))

        rows = [row for packet in socket.sent if packet["trnm"] == "REG" for row in packet["data"]]
        trade_items = [item for row in rows if row["type"] == ["0B"] for item in row["item"]]
        program_items = [item for row in rows if row["type"] == ["0w"] for item in row["item"]]
        self.assertEqual(200, len(trade_items))
        self.assertEqual(110, len(program_items))
        self.assertIn("000109_NX", trade_items)
        self.assertIn("000108_NX", trade_items)

    def test_unused_central_subscription_group_is_removed(self) -> None:
        class Socket:
            def __init__(self): self.sent = []
            async def send(self, value): self.sent.append(json.loads(value))

        collector = CentralRealtimeCollector(
            lambda: "token", "real", RealtimeHub(), lambda: datetime(2026, 9, 14, 16, 30),
        )
        socket = Socket()
        previous = {
            "1000": [{"item": ["005930"], "type": ["0B"]}],
            "1001": [{"item": ["000660"], "type": ["0B"]}],
        }
        asyncio.run(collector._send_subscription(
            socket, "KRX", ("005930",), (), previous_groups=previous,
        ))

        removes = [value for value in socket.sent if value["trnm"] == "REMOVE"]
        self.assertEqual(["1001"], [value["grp_no"] for value in removes])

    def test_market_state_history_is_saved_with_realtime_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            collector = CentralRealtimeCollector(
                lambda: "token",
                "real",
                RealtimeHub(),
                lambda: datetime(2026, 9, 10, 10, 15, 30),
                store,
            )
            collector._publish_parsed({
                "trnm": "REAL",
                "data": [{
                    "type": "0J",
                    "item": "001",
                    "values": {"20": "101530", "10": "+2812.34", "12": "+1.25"},
                }],
            })

            asyncio.run(collector._flush_snapshots())
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.MARKET_STATE,
                "kospi",
                "2026-09-10T10:15",
            )
            store.close()

        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(TradingVenue.KRX, metadata.venue)
        self.assertEqual(ObservationOrigin.REALTIME, metadata.origin)
        self.assertEqual(CandidateUniverse.MARKET_ALL, metadata.candidate_universe)

    def test_0g_reference_is_published_and_saved_as_latest_basis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            hub = RealtimeHub()
            subscriber = hub.connect()
            hub.update_subscription(subscriber, ["005930"], [])
            collector = CentralRealtimeCollector(
                lambda: "token", "real", hub,
                lambda: datetime(2026, 9, 15, 16, 30), store,
            )

            collector._publish_parsed({
                "trnm": "REAL",
                "data": [{"type": "0g", "item": "005930", "values": {
                    "305": "+91900", "306": "-49500", "307": "70700",
                }}],
            })
            event = subscriber.queue.get_nowait()
            asyncio.run(collector._flush_snapshots())
            rows = store.load_documents("stock_price_references", "005930", 1)
            store.close()

        self.assertEqual("stock_reference", event["type"])
        self.assertEqual(91_900, rows[0]["document"]["upper_limit_price"])
        self.assertEqual("kiwoom-websocket-0g", rows[0]["document"]["source"])

    def test_market_state_special_trade_time_uses_received_minute(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            collector = CentralRealtimeCollector(
                lambda: "token",
                "real",
                RealtimeHub(),
                lambda: datetime(2026, 9, 11, 15, 33, 1),
                store,
            )
            collector._publish_parsed({
                "trnm": "REAL",
                "data": [{
                    "type": "0J",
                    "item": "001",
                    "values": {"20": "888888", "10": "+2812.34", "12": "+1.25"},
                }],
            })

            asyncio.run(collector._flush_snapshots())
            snapshots = store.load_dataset_snapshots("market_state", "kospi")
            store.close()

        self.assertEqual("2026-09-11T15:33", snapshots[0]["snapshot_key"])
        self.assertEqual("888888", snapshots[0]["payload"]["value"]["trade_time"])

    def test_realtime_minute_bar_is_saved_as_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            collector = CentralRealtimeCollector(
                lambda: "token",
                "real",
                RealtimeHub(),
                lambda: datetime(2026, 9, 10, 10, 15, 30),
                store,
            )
            collector._publish_parsed({
                "trnm": "REAL",
                "data": [{
                    "type": "0B",
                    "item": "005930",
                    "values": {
                        "10": "+70000", "14": "1000", "15": "2", "20": "101530"
                    },
                }],
            })

            asyncio.run(collector._flush_snapshots(flush_latest=False))
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.MINUTE_BAR,
                "005930:KRX",
                "2026-09-10T10:15",
            )
            self.assertEqual([], store.load_realtime_snapshots(["005930"]))
            self.assertIn(("trade", "005930"), collector._pending_snapshots)
            store.close()

        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(DataCompleteness.IN_PROGRESS, metadata.completeness)
        self.assertEqual(ObservationOrigin.REALTIME, metadata.origin)
        self.assertEqual(DataValueKind.ACTUAL, metadata.value_kind)


if __name__ == "__main__":
    unittest.main()
