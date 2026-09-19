from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.market_cache_writer import MarketCacheWriter


class MarketCacheWriterTests(unittest.TestCase):
    def test_history_batch_is_saved_before_success_signal(self) -> None:
        application = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "monitor.sqlite3"
            Database(path).initialize()
            minute = datetime(2026, 9, 14, 10, 30)
            saved: list[str] = []
            writer = MarketCacheWriter(path)
            writer.history_saved.connect(saved.append)
            writer.start()
            writer.enqueue_history_bars(
                "005930",
                (MinuteOhlcv(minute, 70000, 71000, 69500, 70500, 10, 0.7),),
                date(2026, 9, 14),
                datetime(2026, 9, 14, 10, 31),
            )

            self.assertTrue(writer.stop_and_drain())
            application.processEvents()
            with closing(sqlite3.connect(path)) as connection:
                bar = connection.execute(
                    "SELECT close_price FROM minute_bars WHERE stock_code=? AND minute=?",
                    ("005930", minute.isoformat(timespec="minutes")),
                ).fetchone()
                sync = connection.execute(
                    "SELECT bar_count FROM minute_history_sync_log WHERE stock_code=?",
                    ("005930",),
                ).fetchone()
            self.assertEqual((70500,), bar)
            self.assertEqual((1,), sync)
            self.assertEqual(["005930"], saved)

    def test_stop_drains_minute_and_price_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "monitor.sqlite3"
            Database(path).initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "INSERT INTO stocks(code,name) VALUES (?,?)",
                    ("005930", "삼성전자"),
                )
                connection.commit()

            minute = datetime(2026, 9, 14, 10, 30)
            writer = MarketCacheWriter(path)
            writer.start()
            writer.enqueue_minute_bars(
                {("005930", minute): MinuteOhlcv(minute, 70000, 71000, 69500, 70500, 10, 0.7)},
                {},
            )
            writer.enqueue_price_cache(
                {"005930": 70500}, {"005930": 71000}, date(2026, 9, 14),
            )

            self.assertTrue(writer.stop_and_drain())
            with closing(sqlite3.connect(path)) as connection:
                bar = connection.execute(
                    "SELECT close_price FROM minute_bars WHERE stock_code=? AND minute=?",
                    ("005930", minute.isoformat(timespec="minutes")),
                ).fetchone()
                stock = connection.execute(
                    "SELECT last_price FROM stocks WHERE code=?", ("005930",),
                ).fetchone()
                high = connection.execute(
                    "SELECT high_price FROM intraday_highs WHERE trade_date=? AND stock_code=?",
                    ("2026-09-14", "005930"),
                ).fetchone()

            self.assertEqual((70500,), bar)
            self.assertEqual((70500,), stock)
            self.assertEqual((71000,), high)


if __name__ == "__main__":
    unittest.main()
