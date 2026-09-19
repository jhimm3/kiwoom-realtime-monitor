from __future__ import annotations

import unittest
import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.realtime_collector import (
    CentralRealtimeCollector,
    REALTIME_ITEMS_PER_TYPE,
    _registered_trade_sources,
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

            asyncio.run(collector._flush_snapshots())
            metadata = store.load_market_data_metadata(
                MarketDatasetKind.MINUTE_BAR,
                "005930:KRX",
                "2026-09-10T10:15",
            )
            store.close()

        self.assertIsNotNone(metadata)
        assert metadata is not None
        self.assertEqual(DataCompleteness.IN_PROGRESS, metadata.completeness)
        self.assertEqual(ObservationOrigin.REALTIME, metadata.origin)
        self.assertEqual(DataValueKind.ACTUAL, metadata.value_kind)


if __name__ == "__main__":
    unittest.main()
