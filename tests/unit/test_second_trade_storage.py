from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.central_server.central_schema import central_schema_migrations
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigrationRunner
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick


def _bar(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "trading_date": "2026-09-10",
        "trade_second": "10:00:01",
        "code": "005930",
        "market": "KRX",
        "open": 100,
        "high": 110,
        "low": 90,
        "close": 105,
        "volume": 10,
        "trade_value_won": 1_020,
        "trade_count": 3,
        "available_at": 2.0,
    }
    value.update(changes)
    return value


def _stored(path: Path) -> tuple[object, ...]:
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            "SELECT open,high,low,close,volume,trade_value_won,trade_count,available_at "
            "FROM central_second_trade_bars WHERE code='005930' AND market='KRX'"
        ).fetchone()
    assert row is not None
    return row


class SecondTradeStorageTests(unittest.TestCase):
    def test_v3_database_migrates_without_losing_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply(
                        central_schema_migrations()[:3]
                    )
                    connection.execute(
                        "INSERT INTO central_documents VALUES(?,?,?,?,?)",
                        ("test", "owner", "key", 1.0, '{"kept":true}'),
                    )
            store = SQLiteQueryStore(path)
            store.initialize()
            kept = store.load_documents("test", "owner")
            with closing(sqlite3.connect(path)) as connection:
                tables = {
                    str(row[0]) for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }

        self.assertIn("central_second_trade_bars", tables)
        self.assertTrue(kept[0]["document"]["kept"])

    def test_same_absolute_bar_retry_does_not_add_values_twice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()

            store.save_second_trade_bars([_bar()])
            store.save_second_trade_bars([_bar()])

            self.assertEqual((100, 110, 90, 105, 10, 1_020, 3, 2.0), _stored(path))

    def test_newer_absolute_state_replaces_row_and_stale_state_cannot_regress_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()

            store.save_second_trade_bars([_bar()])
            store.save_second_trade_bars([_bar(close=120, volume=12, trade_count=4, available_at=3.0)])
            store.save_second_trade_bars([_bar(close=88, volume=2, trade_count=2, available_at=3.0)])
            store.save_second_trade_bars([_bar(close=99, volume=1, trade_count=1, available_at=1.0)])

            self.assertEqual((100, 110, 90, 120, 12, 1_020, 4, 3.0), _stored(path))

    def test_historical_archive_retry_does_not_double_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'archive.sqlite3'
            store=SQLiteQueryStore(path);store.initialize()
            try:
                value=_bar()
                store.save_second_trade_bars([value])
                store.save_second_trade_bars([value])
                self.assertEqual((100,110,90,105,10,1020,3,2.0),_stored(path))
            finally:store.close()

    def test_parsed_0b_ticks_never_write_new_second_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            collector = CentralRealtimeCollector(
                lambda: "token", "real", RealtimeHub(),
                lambda: datetime(2026, 9, 10, 10, 0, 1), store,
            )

            collector._publish_parsed({
                "trnm": "REAL",
                "data": [{"type": "0B", "item": "005930", "values": {
                    "10": "+100", "13": "1001", "14": "700", "15": "1", "20": "100001",
                }}],
            })
            asyncio.run(collector._flush_snapshots())
            collector._publish_parsed({
                "trnm": "REAL",
                "data": [{"type": "0B", "item": "005930", "values": {
                    "10": "+110", "13": "1003", "14": "701", "15": "2", "20": "100001",
                }}],
            })
            asyncio.run(collector._flush_snapshots())

            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(0,connection.execute('SELECT count(*) FROM central_second_trade_bars').fetchone()[0])
            store.close()


if __name__ == "__main__":
    unittest.main()
