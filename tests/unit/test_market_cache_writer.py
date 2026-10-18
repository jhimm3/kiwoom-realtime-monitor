from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, Qt

from kiwoom_monitor.domain.time_basis import as_kst
from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
from kiwoom_monitor.application.daily_high_service import DailyBar, DailyHighTargets
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.market_cache_writer import MarketCacheWriter


class MarketCacheWriterTests(unittest.TestCase):
    def test_repeated_market_cap_jobs_skip_row_changes_but_preserve_price_receipts_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            original_connect = sqlite3.connect
            generic_time = "2026-10-15T16:00:00+09:00"
            price_time = "2026-10-15T15:30:00+09:00"
            fundamentals_time = "2026-10-15T15:00:00+09:00"
            with closing(original_connect(path)) as connection:
                connection.execute(
                    "INSERT INTO stocks(code,name,last_price,market_cap,circulating_market_cap,float_ratio,"
                    "updated_at,last_price_updated_at,fundamentals_updated_at) VALUES('A','A',100,1000,500,50,?,?,?)",
                    (generic_time, price_time, fundamentals_time),
                )
                connection.commit()

            writes = []

            class TracedConnection(sqlite3.Connection):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.statements = []
                    self.set_trace_callback(self.statements.append)

                def close(self):
                    # SQL attempts and affected rows are distinct; a no-match UPDATE contributes zero.
                    # Read total_changes before close; it does not measure physical disk/WAL writes.
                    writes.append((self.total_changes, tuple(self.statements)))
                    super().close()

            def connect(*args, **kwargs):
                return original_connect(*args, factory=TracedConnection, **kwargs)

            failures = []
            writer = MarketCacheWriter(path)
            restarted = MarketCacheWriter(path)
            for current in (writer, restarted):
                current.price_failed.connect(lambda *values: failures.append(values), Qt.ConnectionType.DirectConnection)
            # Every stop drains the real queue; phase two restarts the same QThread,
            # and phase three creates a fresh writer/repository without an acknowledgement cache.
            for index, (current, prices) in enumerate(((writer, {}), (writer, {"A": 100}), (restarted, {}))):
                observed_at = as_kst(datetime(2026, 10, 16, 10, index))
                with patch("kiwoom_monitor.infrastructure.persistence.stock_repository.sqlite3.connect", connect):
                    current.enqueue_price_cache(prices, {}, {"A": 1000.}, observed_at.date(), observed_at=observed_at)
                    current.start()
                    self.assertTrue(current.stop_and_drain())
                self.assertEqual([], failures)
                self.assertEqual(index + 1, len(writes))
                changes, statements = writes[-1]
                normalized = tuple(sql.strip().upper() for sql in statements)
                self.assertEqual(1, sum(sql.startswith("UPDATE STOCKS SET MARKET_CAP=") for sql in normalized))
                self.assertEqual(1 if prices else 0, sum(sql.startswith("UPDATE STOCKS SET LAST_PRICE=") for sql in normalized))
                self.assertEqual(0, sum(sql.startswith("SELECT") for sql in normalized))
                self.assertEqual(1, normalized.count("BEGIN"))
                self.assertEqual(1, normalized.count("COMMIT"))
                self.assertEqual(0, normalized.count("ROLLBACK"))
                self.assertEqual(1 if prices else 0, changes)
                if prices:
                    price_time = observed_at.isoformat()
                with closing(original_connect(path)) as connection:
                    self.assertEqual((100, 1000., 500., generic_time, price_time, fundamentals_time), connection.execute(
                        "SELECT last_price,market_cap,circulating_market_cap,updated_at,"
                        "last_price_updated_at,fundamentals_updated_at FROM stocks WHERE code='A'",
                    ).fetchone())

    def test_price_job_rolls_back_all_values_on_last_write_failure_then_same_writer_retries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("INSERT INTO stocks(code,name,last_price,market_cap,float_ratio) VALUES('A','A',90,900,50)")
                connection.execute("CREATE TRIGGER fail_high BEFORE INSERT ON intraday_highs BEGIN SELECT RAISE(ABORT,'high fault'); END")
                connection.commit()
            now = datetime(2026, 10, 16, 1, tzinfo=timezone.utc)
            writer = MarketCacheWriter(path, now_provider=lambda: now)
            failures = []
            writer.price_failed.connect(lambda *values: failures.append(values), Qt.ConnectionType.DirectConnection)
            payload = ({"A": 100}, {"A": 110}, {"A": 1000.}, date(2026, 10, 16))
            writer.enqueue_price_cache(*payload)
            writer._now = lambda: datetime(2026, 10, 17, 1, tzinfo=timezone.utc)
            writer.start()
            self.assertTrue(writer.stop_and_drain())
            self.assertEqual(1, len(failures))
            self.assertEqual(payload[:3], failures[0][:3])
            self.assertEqual({"trade_date": payload[3], "observed_at": as_kst(now), "receipt": 0},
                             failures[0][3])
            self.assertIn("high fault", failures[0][-1])
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual((90, 900), connection.execute("SELECT last_price,market_cap FROM stocks").fetchone())
                self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM intraday_highs").fetchone()[0])
                connection.execute("DROP TRIGGER fail_high")
                connection.commit()
            context = failures[0][3]
            writer.enqueue_price_cache(*payload, observed_at=context["observed_at"], receipt=context["receipt"])
            writer.start()
            self.assertTrue(writer.stop_and_drain())
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual((100, 1000, "2026-10-16T10:00:00+09:00"), connection.execute(
                    "SELECT last_price,market_cap,last_price_updated_at FROM stocks").fetchone())
                self.assertEqual((110, "2026-10-16T10:00:00+09:00"), connection.execute(
                    "SELECT high_price,updated_at FROM intraday_highs").fetchone())
            self.assertEqual(1, len(failures))

    def test_price_enqueue_returns_while_sqlite_is_locked_and_drain_preserves_whole_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            with closing(sqlite3.connect(path)) as con:
                con.execute("INSERT INTO stocks(code,name,float_ratio) VALUES('A','A',50)")
                con.commit()
            now = datetime(2026, 10, 16, 10)
            writer = MarketCacheWriter(path, now_provider=lambda: now)
            writer.start()
            with closing(sqlite3.connect(path)) as blocker:
                blocker.execute("BEGIN IMMEDIATE")
                started = perf_counter()
                writer.enqueue_price_cache({"A": 100}, {"A": 110}, {"A": 1000.}, now.date())
                self.assertLess(perf_counter() - started, 0.5)
                blocker.rollback()
            self.assertTrue(writer.stop_and_drain())
            with closing(sqlite3.connect(path)) as con:
                self.assertEqual((100, 1000, 500), con.execute(
                    "SELECT last_price,market_cap,circulating_market_cap FROM stocks").fetchone())
                self.assertEqual([(110,)], con.execute("SELECT high_price FROM intraday_highs").fetchall())

    def test_fundamentals_write_does_not_block_gui_caller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "monitor.sqlite3"
            Database(path).initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("INSERT INTO stocks(code,name) VALUES (?,?)", ("005930", "삼성전자"))
                connection.commit()
            writer = MarketCacheWriter(path)
            writer.start()
            with closing(sqlite3.connect(path)) as blocker:
                blocker.execute("BEGIN IMMEDIATE")
                started = perf_counter()
                writer.enqueue_fundamentals("005930", 420000.0, 75.0, 100000, 71000)
                self.assertLess(perf_counter() - started, 0.5)
                blocker.rollback()
            self.assertTrue(writer.stop_and_drain())
            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute(
                    "SELECT market_cap,float_ratio,float_shares,upper_limit_price "
                    "FROM stocks WHERE code='005930'"
                ).fetchone()
            self.assertEqual((420000.0, 75.0, 100000, 71000), row)

    def test_daily_high_enqueue_does_not_wait_for_sqlite_writer_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "monitor.sqlite3"
            Database(path).initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("INSERT INTO stocks(code,name) VALUES (?,?)", ("005930", "삼성전자"))
                connection.commit()
            writer = MarketCacheWriter(path)
            writer.start()
            with closing(sqlite3.connect(path)) as blocker:
                blocker.execute("BEGIN IMMEDIATE")
                started = perf_counter()
                writer.enqueue_daily_high(
                    "005930", DailyHighTargets(None, None, 71000, period_statuses=(("250", "ready"),)),
                    date(2026, 9, 15), datetime(2026, 9, 15, 10, 31),
                )
                self.assertLess(perf_counter() - started, 0.5)
                blocker.rollback()
            self.assertTrue(writer.stop_and_drain())
            with closing(sqlite3.connect(path)) as connection:
                high = connection.execute("SELECT high_250_price FROM stocks WHERE code='005930'").fetchone()
            self.assertEqual((71000,), high)

    def test_daily_high_and_minute_bars_are_saved_outside_caller(self) -> None:
        application = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "monitor.sqlite3"
            Database(path).initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("INSERT INTO stocks(code,name) VALUES (?,?)", ("005930", "삼성전자"))
                connection.commit()
            now = datetime(2026, 9, 15, 10, 31)
            writer = MarketCacheWriter(path, now_provider=lambda: now)
            writer.start()
            targets = DailyHighTargets.from_daily_bars((
                DailyBar("20260914", 71000, 100.0, 70500, 70000, 69500, 1000),
            ), as_of=now.date())
            minute = datetime(2026, 9, 14, 10, 30)
            writer.enqueue_minute_bars({
                ("005930", minute): MinuteOhlcv(minute, 70000, 71000, 69500, 70500, 1000, 100.0),
            }, {})
            writer.enqueue_daily_high("005930", targets, now.date(), now)
            self.assertTrue(writer.stop_and_drain())
            application.processEvents()
            with closing(sqlite3.connect(path)) as connection:
                stock = connection.execute("SELECT high_250_price FROM stocks WHERE code='005930'").fetchone()
                bar = connection.execute("SELECT high_price FROM daily_bars WHERE stock_code='005930'").fetchone()
                metadata = connection.execute(
                    "SELECT available_at FROM market_data_observation_meta WHERE dataset_kind='minute_bar'"
                ).fetchone()
            self.assertEqual((None,), stock)  # Unverified one-row cache must not replace adjusted high
            self.assertEqual((71000,), bar)
            self.assertEqual(("2026-09-15T10:31:00+09:00",), metadata)
            self.assertFalse((path.parent / "logs" / "developer_checks").exists())

    def test_history_batch_is_saved_before_success_signal(self) -> None:
        application = QCoreApplication.instance() or QCoreApplication([])
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "monitor.sqlite3"
            Database(path).initialize()
            minute = datetime(2026, 9, 14, 10, 30)
            saved: list[str] = []
            committed_at_signal = []
            writer = MarketCacheWriter(path)
            writer.history_saved.connect(saved.append)

            def observe_commit(_code: str) -> None:
                with closing(sqlite3.connect(path)) as connection:
                    committed_at_signal.append((
                        connection.execute("SELECT close_price FROM minute_bars").fetchone(),
                        connection.execute("SELECT bar_count FROM minute_history_sync_log").fetchone(),
                    ))

            writer.history_saved.connect(observe_commit, Qt.ConnectionType.DirectConnection)
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
            self.assertEqual([((70500,), (1,))], committed_at_signal)

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
                {"005930": 70500}, {"005930": 71000}, {"005930": 4_200_000.0},
                date(2026, 9, 14),
            )

            self.assertTrue(writer.stop_and_drain())
            with closing(sqlite3.connect(path)) as connection:
                bar = connection.execute(
                    "SELECT close_price FROM minute_bars WHERE stock_code=? AND minute=?",
                    ("005930", minute.isoformat(timespec="minutes")),
                ).fetchone()
                stock = connection.execute(
                    "SELECT last_price,market_cap FROM stocks WHERE code=?", ("005930",),
                ).fetchone()
                high = connection.execute(
                    "SELECT high_price FROM intraday_highs WHERE trade_date=? AND stock_code=?",
                    ("2026-09-14", "005930"),
                ).fetchone()

            self.assertEqual((70500,), bar)
            self.assertEqual((70500, 4_200_000.0), stock)
            self.assertEqual((71000,), high)


if __name__ == "__main__":
    unittest.main()
