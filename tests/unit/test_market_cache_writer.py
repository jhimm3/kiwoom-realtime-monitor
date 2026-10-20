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
    def test_retention_follows_dirty_minutes_and_preserves_cutoff_rows_and_metadata(self) -> None:
        from threading import get_ident
        from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path)
            old, kept = datetime(2026, 9, 19, 10), datetime(2026, 9, 20, 10)
            repository.upsert_many({"005930": tuple(
                MinuteOhlcv(minute, 100, 110, 90, 105, 10) for minute in (old, kept))})
            repository.mark_gap_minutes({"005930": (old, kept)})
            repository.upsert_market_index_minutes({("kospi", minute): (1., 2., 1., 2., 10.)
                for minute in (old, kept)})
            for minute in (old, kept):
                repository.record_history_sync("005930", minute.date(), minute, 1)
            with closing(sqlite3.connect(path)) as connection:
                kept_metadata = connection.execute(
                    "SELECT * FROM market_data_observation_meta WHERE substr(observation_key,1,10)=? ORDER BY subject",
                    (kept.date().isoformat(),)).fetchall()
            original = MinuteBarRepository.purge_before
            purges, failures = [], []

            def purge(current, cutoff):
                with closing(sqlite3.connect(path)) as connection:
                    dirty = connection.execute("SELECT close_price FROM minute_bars WHERE stock_code='dirty'").fetchone()
                purges.append((get_ident(), cutoff, dirty))
                original(current, cutoff)

            writer = MarketCacheWriter(path)
            writer.retention_failed.connect(lambda *values: failures.append(values), Qt.ConnectionType.DirectConnection)
            writer.minute_failed.connect(lambda *values: failures.append(values), Qt.ConnectionType.DirectConnection)
            writer.enqueue_minute_bars({("dirty", old): MinuteOhlcv(old, 50, 50, 50, 50, 1)}, {})
            writer.enqueue_minute_retention(kept.date())
            with patch.object(MinuteBarRepository, "purge_before", purge):
                writer.start()
                self.assertTrue(writer.stop_and_drain())
            self.assertEqual([], failures)
            self.assertEqual([(kept.date(), (50,))], [(cutoff, dirty) for _, cutoff, dirty in purges])
            self.assertNotEqual(get_ident(), purges[0][0])
            with closing(sqlite3.connect(path)) as connection:
                for table in ("minute_bars", "minute_history_sync_log", "market_index_minute_bars"):
                    self.assertEqual([(kept.date().isoformat(),)], connection.execute(
                        f"SELECT DISTINCT trade_date FROM {table}").fetchall(), table)
                self.assertEqual(kept_metadata, connection.execute(
                    "SELECT * FROM market_data_observation_meta ORDER BY subject").fetchall())

    def test_applied_cutoff_survives_restart_and_late_jobs_preserve_ack_keeper_and_native_cost(self) -> None:
        from datetime import timedelta
        from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository

        now = as_kst(datetime(2026, 10, 21, 10))
        kept = now - timedelta(days=30)
        observations = {}

        def observe(kind, minute, applied):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "monitor.sqlite3"
                Database(path).initialize()
                clock_calls = []

                def clock():
                    clock_calls.append(1)
                    return now

                repository = MinuteBarRepository(path, now_provider=clock)
                repository.upsert_bars("KEEP", (MinuteOhlcv(kept, 100, 110, 90, 105, 10),))
                repository.upsert_market_index_minutes({("kosdaq", kept): (1., 2., 1., 2., 10.)})
                repository.record_history_sync("KEEP", kept.date(), kept, 1)
                original_connect = sqlite3.connect

                def keeper():
                    with closing(original_connect(path)) as connection:
                        return (
                            connection.execute("SELECT * FROM minute_bars WHERE stock_code='KEEP'").fetchall(),
                            connection.execute("SELECT * FROM market_index_minute_bars WHERE market='kosdaq'").fetchall(),
                            connection.execute("SELECT * FROM minute_history_sync_log WHERE stock_code='KEEP'").fetchall(),
                            connection.execute("SELECT * FROM market_data_observation_meta WHERE subject IN ('KEEP','market:kosdaq') ORDER BY subject").fetchall(),
                        )

                original_keeper = keeper()
                writer = MarketCacheWriter(path, now_provider=clock)
                failures, acks, quality, statements, connections = [], [], [], [], []
                writer.retention_failed.connect(lambda *args: failures.append(args), Qt.ConnectionType.DirectConnection)
                writer.minute_failed.connect(lambda *args: failures.append(args), Qt.ConnectionType.DirectConnection)
                writer.history_failed.connect(lambda *args: failures.append(args), Qt.ConnectionType.DirectConnection)
                if applied:
                    writer.enqueue_minute_retention(kept.date())
                    writer.enqueue_minute_retention(kept.date() - timedelta(days=1))
                    writer.start()
                    self.assertTrue(writer.stop_and_drain())
                    self.assertEqual(kept.date(), writer._minute_retention_cutoff)
                self.assertEqual(original_keeper, keeper())

                def committed(*_):
                    # Exclude these diagnostic reads from production SQL cost.
                    with closing(original_connect(path)) as connection:
                        acks.append((connection.execute("SELECT * FROM minute_bars WHERE stock_code='TARGET'").fetchall(),
                                     connection.execute("SELECT * FROM market_index_minute_bars WHERE market='kospi'").fetchall()))

                writer.minute_saved.connect(committed, Qt.ConnectionType.DirectConnection)
                writer.history_saved.connect(committed, Qt.ConnectionType.DirectConnection)
                writer.history_quality_saved.connect(lambda code, value: quality.append((code, value)), Qt.ConnectionType.DirectConnection)

                class TracedConnection(sqlite3.Connection):
                    def __init__(self, *args, **kwargs):
                        super().__init__(*args, **kwargs)
                        connections.append(1)
                        self.set_trace_callback(statements.append)

                def connect(*args, **kwargs):
                    kwargs["factory"] = TracedConnection
                    return original_connect(*args, **kwargs)

                bar = MinuteOhlcv(minute, 50, 70, 40, 60, 3)
                if kind == "minute":
                    writer.enqueue_minute_bars({("TARGET", minute): bar}, {("kospi", minute): (1., 2., 1., 2., 10.)})
                else:
                    writer.enqueue_history_bars("TARGET", (bar,), now.date(), now, recovery_generation=7)
                clock_calls.clear()
                with patch("sqlite3.connect", side_effect=connect):
                    writer.start()  # Same instance restarts with its successful cutoff.
                    self.assertTrue(writer.stop_and_drain())
                self.assertEqual([], failures)
                self.assertEqual(1, len(acks))
                self.assertEqual(1, len(acks[0][0]))
                self.assertEqual(int(kind == "minute"), len(acks[0][1]))
                self.assertEqual(original_keeper, keeper())
                if kind == "history":
                    self.assertEqual([("TARGET", {"generation": 7, "minutes": (minute,)})], quality)
                with closing(original_connect(path)) as connection:
                    target_rows = connection.execute("SELECT COUNT(*) FROM minute_bars WHERE stock_code='TARGET'").fetchone()[0]
                    self.assertEqual(int(not (applied and minute.date() < kept.date())), target_rows)
                    if kind == "history":
                        self.assertEqual([(now.date().isoformat(), "TARGET", now.isoformat(timespec="seconds"), 1)],
                            connection.execute("SELECT * FROM minute_history_sync_log WHERE stock_code='TARGET'").fetchall())
                counts = {key: sum(statement.lstrip().upper().startswith(key.upper()) for statement in statements)
                          for key in ("select", "delete", "begin", "commit")}
                return {"connections": len(connections), **counts, "clock": len(clock_calls),
                        "sql": statements, "direct_ack_rows": [len(rows) for rows in acks[0]],
                        "target_rows_after_drain": target_rows, "keeper_exact": True}

        for kind in ("minute", "history"):
            for label, minute in (("expired", kept - timedelta(days=1)), ("cutoff", kept), ("current", now - timedelta(minutes=1))):
                with self.subTest(kind=kind, date=label):
                    baseline, retained = observe(kind, minute, False), observe(kind, minute, True)
                    extra = int(label == "expired")
                    self.assertEqual(baseline["sql"], retained["sql"][:len(baseline["sql"])])
                    self.assertEqual(baseline["connections"] + extra, retained["connections"])
                    self.assertEqual(baseline["select"] + extra, retained["select"])
                    self.assertEqual(baseline["delete"] + 4 * extra, retained["delete"])
                    self.assertEqual(baseline["begin"] + extra, retained["begin"])
                    self.assertEqual(baseline["commit"] + extra, retained["commit"])
                    self.assertEqual(baseline["clock"], retained["clock"])
                    if not extra:
                        self.assertEqual(baseline["sql"], retained["sql"])
                    observations[f"{kind}:{label}"] = {
                        name: {key: value for key, value in result.items() if key != "sql"}
                        for name, result in (("without_applied_cutoff", baseline), ("with_applied_cutoff", retained))}
        self.retention_cost_observations = observations

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
