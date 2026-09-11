from __future__ import annotations

import unittest
import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
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
            "trade_value_million_won": 7, "updated_at": 1.0,
        })

        with self.assertRaises(OSError):
            asyncio.run(collector._flush_snapshots())
        collector._merge_minute_retry({
            "trading_date": "2026-09-10", "minute": "10:00", "code": "005930", "market": "KRX",
            "open": 101, "high": 103, "low": 100, "close": 102, "volume": 5,
            "trade_value_million_won": 11, "updated_at": 2.0,
        })

        asyncio.run(collector._flush_snapshots())
        self.assertEqual(2, store.calls)
        self.assertEqual(8, store.saved[0]["volume"])
        self.assertEqual(18, store.saved[0]["trade_value_million_won"])
        self.assertEqual(103, store.saved[0]["high"])
        self.assertEqual(99, store.saved[0]["low"])

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

    def test_nxt_subscription_excludes_ineligible_codes(self) -> None:
        class Socket:
            def __init__(self): self.sent = []
            async def send(self, value): self.sent.append(json.loads(value))

        hub = RealtimeHub()
        collector = CentralRealtimeCollector(lambda: "token", "real", hub, lambda: datetime(2026, 9, 8, 8, 30))
        socket = Socket()
        asyncio.run(collector._send_subscription(socket, "NXT", ("005930", "000660"), ("005930",)))
        self.assertEqual(["005930_NX"], socket.sent[0]["data"][0]["item"])

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
