"""Executable preservation checks for changes to the realtime storage boundary.

The suite uses a fresh SQLite database for every case. It never opens the
operator's PC database or NAS PostgreSQL database.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.market_observations import bar_observation_key, minute_bar_observation
from kiwoom_monitor.domain.market_data_contract import (
    DataCompleteness, DataValueKind, MarketDatasetKind, ObservationOrigin,
)


KST = ZoneInfo("Asia/Seoul")


def _bar(code: str, operation_id: str, *, minute: str = "10:00", volume: int = 7) -> dict:
    return {
        "trading_date": "2026-09-21", "minute": minute, "code": code,
        "market": "KRX", "open": 10000, "high": 10100, "low": 9900,
        "close": 10050, "volume": volume,
        "trade_value_million_won": 1, "updated_at": 1_790_000_000.0,
        "operation_id": operation_id,
    }


class StorageBoundaryConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "isolated.sqlite3"
        self.store = SQLiteQueryStore(self.path)
        self.store.initialize()

    def tearDown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    def _operation_count(self) -> int:
        with closing(sqlite3.connect(self.path)) as connection:
            return int(connection.execute("SELECT count(*) FROM central_minute_bar_operations").fetchone()[0])

    def test_same_operation_retried_after_reopen_is_counted_once(self) -> None:
        value = _bar("005930", "trade-005930-1")
        self.store.save_minute_bars([value])
        self.store.close()
        self.store = SQLiteQueryStore(self.path)
        self.store.save_minute_bars([value])
        rows = self.store.load_minute_bars("005930", value["trading_date"], "KRX")
        self.assertEqual(7, rows[0]["volume"])
        self.assertEqual(1, self._operation_count())

    def test_concurrent_duplicate_and_distinct_stocks_preserve_each_operation(self) -> None:
        first = _bar("005930", "trade-005930-1")
        second = _bar("000660", "trade-000660-1", volume=11)
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(self.store.save_minute_bars, [[first], [first], [second], [second]] * 4))
        self.assertEqual([None] * 16, results)
        self.assertEqual(7, self.store.load_minute_bars("005930", first["trading_date"], "KRX")[0]["volume"])
        self.assertEqual(11, self.store.load_minute_bars("000660", second["trading_date"], "KRX")[0]["volume"])
        self.assertEqual(2, self._operation_count())

    def test_failed_second_item_rolls_back_first_item_and_operation(self) -> None:
        valid = _bar("005930", "trade-005930-rollback")
        invalid = {**_bar("000660", "trade-000660-invalid")}
        del invalid["close"]
        with self.assertRaises(KeyError):
            self.store.save_minute_bars([valid, invalid])
        self.store.close()
        self.store = SQLiteQueryStore(self.path)
        self.assertEqual([], self.store.load_minute_bars("005930", valid["trading_date"], "KRX"))
        self.assertEqual(0, self._operation_count())
        self.store.save_minute_bars([valid])
        self.assertEqual(1, self._operation_count())

    def test_conflicting_retry_cannot_change_a_committed_operation(self) -> None:
        original = _bar("005930", "trade-005930-conflict")
        self.store.save_minute_bars([original])
        with self.assertRaisesRegex(ValueError, "payload changed"):
            self.store.save_minute_bars([{**original, "volume": 99}])
        self.assertEqual(7, self.store.load_minute_bars("005930", original["trading_date"], "KRX")[0]["volume"])
        self.assertEqual(1, self._operation_count())

    def test_finalization_failure_rolls_back_then_duplicate_is_idempotent(self) -> None:
        value = _bar("005930", "trade-005930-final")
        self.store.save_minute_bars([value])
        closure = {
            "trading_date": value["trading_date"], "minute": value["minute"],
            "code": value["code"], "market": "KRX",
            "operation_id": "final-005930-1", "capture_quality": "complete",
            "finalization_source": "realtime_flush",
            "available_at": datetime(2026, 9, 21, 10, 1, tzinfo=KST).timestamp(),
        }
        with self.assertRaisesRegex(ValueError, "before bar_end"):
            self.store.finalize_minute_bars([{**closure, "available_at": closure["available_at"] - 2}])
        self.assertEqual(1, self._operation_count())
        self.store.finalize_minute_bars([closure])
        self.store.finalize_minute_bars([closure])
        self.assertEqual(2, self._operation_count())
        self.assertEqual(7, self.store.load_minute_bars("005930", value["trading_date"], "KRX")[0]["volume"])

    def test_collector_retries_after_commit_ack_loss_without_double_counting(self) -> None:
        class AckLostStore:
            def __init__(self, backing: SQLiteQueryStore) -> None:
                self.backing = backing
                self.calls = 0

            def save_minute_bars(self, values, *, observations=None) -> None:
                self.backing.save_minute_bars(values, observations=observations)
                self.calls += 1
                if self.calls == 1:
                    raise OSError("commit succeeded; acknowledgment lost")

        proxy = AckLostStore(self.store)
        collector = CentralRealtimeCollector(
            lambda: "token", "real", RealtimeHub(),
            lambda: datetime(2026, 9, 21, 10, 0, tzinfo=KST), proxy,
        )
        value = _bar("005930", "trade-005930-ack-lost")
        collector._merge_minute_retry(value)
        with self.assertRaisesRegex(OSError, "acknowledgment lost"):
            asyncio.run(collector._flush_snapshots())
        self.assertEqual(1, self._operation_count())
        self.assertTrue(collector._pending_minute_retries)
        asyncio.run(collector._flush_snapshots())
        self.assertEqual(2, proxy.calls)
        self.assertFalse(collector._pending_minute_retries)
        self.assertEqual(1, self._operation_count())
        self.assertEqual(7, self.store.load_minute_bars("005930", value["trading_date"], "KRX")[0]["volume"])

    def test_replay_preserves_metadata_and_single_observation_revision(self) -> None:
        value = _bar("005930", "trade-005930-provenance")
        observation = minute_bar_observation(
            value, origin=ObservationOrigin.REALTIME,
            completeness=DataCompleteness.IN_PROGRESS,
            source="kiwoom-websocket-0B", value_kind=DataValueKind.ACTUAL,
        )
        key = bar_observation_key(observation)
        self.store.save_minute_bars([value], observations=[(key, observation)])
        self.store.save_minute_bars([value], observations=[(key, observation)])
        metadata = self.store.load_market_data_metadata(
            MarketDatasetKind.MINUTE_BAR, observation.subject, key,
        )
        revisions = self.store.load_observation_revisions("minute_bar", observation.subject)
        self.assertIsNotNone(metadata)
        self.assertEqual(DataValueKind.ACTUAL, metadata.value_kind)
        self.assertEqual(1, len(revisions))
        self.assertEqual(1, self._operation_count())


if __name__ == "__main__":
    unittest.main()
