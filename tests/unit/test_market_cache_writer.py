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

    def test_history_sync_failure_after_bar_commit_reapplies_existing_cutoff_without_success_ack(self) -> None:
        from datetime import timedelta
        from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository

        now = as_kst(datetime(2026, 10, 21, 10))
        kept, current = now - timedelta(days=30), now - timedelta(minutes=1)
        expired = kept - timedelta(days=1)
        for stage in ("bar", "current", "cutoff", "expired", "cleanup_precommit", "cleanup_postcommit"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
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
                repository.record_history_sync("TARGET", now.date(), now - timedelta(minutes=5), 17)

                def keeper():
                    with closing(sqlite3.connect(path)) as connection:
                        return (
                            connection.execute("SELECT * FROM minute_bars WHERE stock_code='KEEP'").fetchall(),
                            connection.execute("SELECT * FROM market_index_minute_bars WHERE market='kosdaq'").fetchall(),
                            connection.execute("SELECT * FROM minute_history_sync_log WHERE stock_code='KEEP'").fetchall(),
                            connection.execute("SELECT * FROM market_data_observation_meta WHERE subject IN ('KEEP','market:kosdaq') ORDER BY subject").fetchall(),
                        )

                def target():
                    with closing(sqlite3.connect(path)) as connection:
                        return {
                            "bars": connection.execute("SELECT * FROM minute_bars WHERE stock_code='TARGET' ORDER BY minute").fetchall(),
                            "metadata": connection.execute("SELECT * FROM market_data_observation_meta WHERE subject='TARGET' ORDER BY observation_key").fetchall(),
                            "receipt": connection.execute("SELECT * FROM minute_history_sync_log WHERE stock_code='TARGET'").fetchall(),
                        }

                original_keeper, original_receipt = keeper(), target()["receipt"]
                writer = MarketCacheWriter(path, now_provider=clock)
                events, prefixes, saved, quality, purges = [], [], [], [], []
                real_purge = MinuteBarRepository.purge_before

                def purge(current_repository, cutoff):
                    purges.append(cutoff)
                    real_purge(current_repository, cutoff)
                    if stage == "cleanup_postcommit" and len(purges) == 2:
                        raise OSError("controlled cleanup lost ACK after COMMIT")

                def failed(code, message):
                    events.append(("history_failed", code, message))
                    prefixes.append(target())  # Original failure emission precedes cleanup.

                writer.history_failed.connect(failed, Qt.ConnectionType.DirectConnection)
                writer.retention_failed.connect(lambda cutoff, message: events.append(("retention_failed", cutoff, message)), Qt.ConnectionType.DirectConnection)
                writer.history_saved.connect(lambda code: saved.append((code, target())), Qt.ConnectionType.DirectConnection)
                writer.history_quality_saved.connect(lambda code, value: quality.append((code, value)), Qt.ConnectionType.DirectConnection)
                minutes = ((current,) if stage == "current" else (kept,) if stage == "cutoff" else (expired, kept, current))
                bars = tuple(MinuteOhlcv(minute, 50, 70, 40, 60, 3) for minute in minutes)
                original_payload = repr(bars)
                with patch.object(MinuteBarRepository, "purge_before", purge):
                    writer.enqueue_minute_retention(kept.date())
                    writer.start()
                    self.assertTrue(writer.stop_and_drain())
                    self.assertEqual(kept.date(), writer._minute_retention_cutoff)
                    with closing(sqlite3.connect(path)) as connection:
                        table = "minute_bars" if stage == "bar" else "minute_history_sync_log"
                        message = "controlled bar failure" if stage == "bar" else "controlled sync failure"
                        connection.execute(f"CREATE TRIGGER reject_target_insert BEFORE INSERT ON {table} WHEN NEW.stock_code='TARGET' BEGIN SELECT RAISE(ABORT,'{message}'); END")
                        if stage == "cleanup_precommit":
                            connection.execute("CREATE TRIGGER reject_expired_delete BEFORE DELETE ON minute_bars WHEN OLD.stock_code='TARGET' AND OLD.trade_date < '2026-09-21' BEGIN SELECT RAISE(ABORT,'controlled cleanup DELETE failure'); END")
                        connection.commit()
                    clock_calls.clear()
                    writer.enqueue_history_bars("TARGET", bars, now.date(), now, recovery_generation=11)
                    writer.start()
                    self.assertTrue(writer.stop_and_drain())
                    self.assertEqual([("history_failed", "TARGET", message)], events[:1])
                    self.assertEqual([], saved)
                    self.assertEqual([], quality)
                    self.assertEqual(original_payload, repr(bars))
                    self.assertEqual(original_receipt, prefixes[0]["receipt"])
                    self.assertEqual(original_receipt, target()["receipt"])
                    self.assertEqual(0 if stage == "bar" else len(bars), len(prefixes[0]["bars"]))
                    self.assertEqual(0 if stage == "bar" else len(bars), len(prefixes[0]["metadata"]))
                    if stage != "bar":
                        for row, bar in zip(prefixes[0]["metadata"], bars):
                            self.assertEqual(bar.minute, datetime.fromisoformat(row[3]))
                            self.assertEqual(now, datetime.fromisoformat(row[4]))
                            self.assertEqual(("complete", "query", "kiwoom-ka10080"), (row[8], row[9], row[10]))
                    self.assertEqual(1, len(clock_calls))
                    self.assertEqual(original_keeper, keeper())
                    if stage in {"current", "cutoff"}:
                        self.assertEqual([kept.date()], purges)  # No additional SQL/lookup.
                        self.assertEqual(1, len(events))
                    else:
                        # A failed expired upsert cannot reveal whether COMMIT returned;
                        # even native rollback now reuses the same no-op retention fence.
                        self.assertEqual([kept.date()] * 2, purges)
                        self.assertEqual(int(stage in {"cleanup_precommit", "cleanup_postcommit"}), len(events) - 1)
                        if len(events) == 2:
                            self.assertEqual(("retention_failed", kept.date()), events[1][:2])
                            self.assertIn("controlled cleanup", events[1][2])
                    expected = (() if stage == "bar" else minutes if stage == "cleanup_precommit"
                                else tuple(minute for minute in minutes if minute.date() >= kept.date()))
                    self.assertEqual([minute.isoformat(timespec="minutes") for minute in expected],
                                     [row[2] for row in target()["bars"]])
                    self.assertEqual([row[2] for row in target()["bars"]], [row[2] for row in target()["metadata"]])
                    self.assertEqual([row for row in prefixes[0]["bars"] if datetime.fromisoformat(row[2]) in expected], target()["bars"])
                    self.assertEqual([row for row in prefixes[0]["metadata"] if datetime.fromisoformat(row[2]) in expected], target()["metadata"])
                    if stage in {"cleanup_precommit", "cleanup_postcommit"}:
                        with closing(sqlite3.connect(path)) as connection:
                            connection.execute("DROP TRIGGER IF EXISTS reject_expired_delete")
                            connection.commit()
                        writer.enqueue_minute_retention(kept.date())
                        writer.start()
                        self.assertTrue(writer.stop_and_drain())
                        self.assertEqual(2, len(events))
                        self.assertEqual([], saved)
                        self.assertEqual([], quality)
                        self.assertEqual([minute.isoformat(timespec="minutes") for minute in minutes if minute.date() >= kept.date()],
                                         [row[2] for row in target()["bars"]])
                    if stage == "expired":
                        with closing(sqlite3.connect(path)) as connection:
                            connection.execute("DROP TRIGGER reject_target_insert")
                            connection.commit()
                        writer.enqueue_history_bars("TARGET", bars, now.date(), now, recovery_generation=11)
                        writer.start()
                        self.assertTrue(writer.stop_and_drain())
                        self.assertEqual(1, len(events))
                        self.assertEqual(1, len(saved))
                        self.assertEqual(len(bars), len(saved[0][1]["bars"]))
                        self.assertEqual([("TARGET", {"generation": 11, "minutes": minutes})], quality)
                        self.assertEqual([(now.date().isoformat(), "TARGET", now.isoformat(timespec="seconds"), len(bars))], target()["receipt"])
                    self.assertEqual(original_keeper, keeper())

    def _observe_failed_native_commit_return(self, stage, *, day="expired", cleanup=None, recover=False):
        """Run real SQLite transactions; only their commit return can be lost."""
        from datetime import timedelta
        from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository

        now = as_kst(datetime(2026, 10, 21, 10))
        kept = now - timedelta(days=30)
        minute = {"expired": kept - timedelta(days=1), "cutoff": kept,
                  "current": now - timedelta(minutes=1)}[day]
        native_connect = sqlite3.connect
        clock_calls, sql, connections, committed, failures, prefixes, acks, quality = [], [], [], [], [], [], [], []

        def clock():
            clock_calls.append(1)
            return now

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path, now_provider=clock)
            repository.upsert_bars("KEEP", (MinuteOhlcv(kept, 100, 110, 90, 105, 10),))
            repository.upsert_market_index_minutes({("kosdaq", kept): (1., 2., 1., 2., 10.)})
            repository.record_history_sync("KEEP", kept.date(), kept, 1)
            repository.record_history_sync("TARGET", now.date(), now - timedelta(minutes=5), 17)

            def state():
                # Readbacks bypass the traced/faulting production connection.
                with closing(native_connect(path)) as connection:
                    return {
                        "bars": connection.execute("SELECT * FROM minute_bars WHERE stock_code='TARGET' ORDER BY minute").fetchall(),
                        "index": connection.execute("SELECT * FROM market_index_minute_bars WHERE market='kospi' ORDER BY minute").fetchall(),
                        "metadata": connection.execute("SELECT * FROM market_data_observation_meta WHERE subject IN ('TARGET','market:kospi') ORDER BY subject,observation_key").fetchall(),
                        "receipt": connection.execute("SELECT * FROM minute_history_sync_log WHERE stock_code='TARGET'").fetchall(),
                        "keeper": (
                            connection.execute("SELECT * FROM minute_bars WHERE stock_code='KEEP'").fetchall(),
                            connection.execute("SELECT * FROM market_index_minute_bars WHERE market='kosdaq'").fetchall(),
                            connection.execute("SELECT * FROM minute_history_sync_log WHERE stock_code='KEEP'").fetchall(),
                            connection.execute("SELECT * FROM market_data_observation_meta WHERE subject IN ('KEEP','market:kosdaq') ORDER BY subject,observation_key").fetchall()),
                    }

            initial = state()
            writer = MarketCacheWriter(path, now_provider=clock)
            writer.enqueue_minute_retention(kept.date())
            writer.start()
            self.assertTrue(writer.stop_and_drain())
            self.assertEqual(kept.date(), writer._minute_retention_cutoff)
            bar = MinuteOhlcv(minute, 50, 70, 40, 60, 3)
            pending, market = {("TARGET", minute): bar}, {("kospi", minute): (1., 2., 1., 2., 10.)}
            history = stage == "history_commit"
            original_payload = repr((bar,) if history else (pending, market))
            message = "native index INSERT failed" if stage == "index_insert" else "native stock INSERT failed" if stage == "stock_insert" else "native COMMIT return lost: " + stage
            cleanup_message = "native cleanup COMMIT return lost" if cleanup == "commit" else "native cleanup DELETE failed"

            def history_failed(code, error):
                failures.append(("history_failed", code, error))
                prefixes.append(state())

            failed_pending = []

            def minute_failed(values, indexes, error):
                failed_pending.append((values, indexes))
                failures.append(("minute_failed", repr(values), repr(indexes), error))
                prefixes.append(state())

            writer.history_failed.connect(history_failed, Qt.ConnectionType.DirectConnection)
            writer.minute_failed.connect(minute_failed, Qt.ConnectionType.DirectConnection)
            writer.retention_failed.connect(lambda cutoff, error: failures.append(("retention_failed", cutoff.isoformat(), error)), Qt.ConnectionType.DirectConnection)
            writer.history_saved.connect(lambda *args: acks.append(args), Qt.ConnectionType.DirectConnection)
            writer.minute_saved.connect(lambda *args: acks.append(args), Qt.ConnectionType.DirectConnection)
            writer.history_quality_saved.connect(lambda *args: quality.append(args), Qt.ConnectionType.DirectConnection)
            with closing(native_connect(path)) as connection:
                if stage in {"stock_insert", "index_insert"}:
                    table = "minute_bars" if stage == "stock_insert" else "market_index_minute_bars"
                    connection.execute(f"CREATE TRIGGER reject_insert BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'{message}'); END")
                if cleanup == "delete":
                    connection.execute("CREATE TRIGGER reject_cleanup BEFORE DELETE ON minute_bars WHEN OLD.stock_code='TARGET' AND OLD.trade_date < '2026-09-21' BEGIN SELECT RAISE(ABORT,'native cleanup DELETE failed'); END")
                connection.commit()

            injected = []

            class CommitReturnConnection(sqlite3.Connection):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.role = None
                    connections.append(1)
                    self.set_trace_callback(sql.append)

                def execute(self, statement, *args, **kwargs):
                    if statement.lstrip().upper().startswith("DELETE FROM MINUTE_BARS"):
                        self.role = "cleanup"
                    return super().execute(statement, *args, **kwargs)

                def executemany(self, statement, *args, **kwargs):
                    upper = statement.lstrip().upper()
                    if upper.startswith("INSERT INTO MINUTE_BARS"):
                        self.role = "stock"
                    elif upper.startswith("INSERT INTO MARKET_INDEX_MINUTE_BARS"):
                        self.role = "index"
                    return super().executemany(statement, *args, **kwargs)

                def commit(self):
                    super().commit()  # Actual SQLite COMMIT occurs before fault injection.
                    target = "stock" if stage in {"history_commit", "stock_commit"} else "index" if stage == "index_commit" else None
                    if self.role == target and "save" not in injected:
                        injected.append("save")
                        committed.append({"in_transaction": self.in_transaction, "state": state()})
                        raise OSError(message)
                    if self.role == "cleanup" and cleanup == "commit" and "cleanup" not in injected:
                        injected.append("cleanup")
                        raise OSError(cleanup_message)

            def connect(*args, **kwargs):
                kwargs["factory"] = CommitReturnConnection
                return native_connect(*args, **kwargs)

            def enqueue():
                if history:
                    writer.enqueue_history_bars("TARGET", (bar,), now.date(), now, recovery_generation=23)
                else:
                    writer.enqueue_minute_bars(pending, market)

            clock_calls.clear()
            with patch("sqlite3.connect", side_effect=connect):
                enqueue()
                writer.start()
                self.assertTrue(writer.stop_and_drain())
            final = state()
            self.assertEqual(message, failures[0][-1])
            self.assertEqual("history_failed" if history else "minute_failed", failures[0][0])
            self.assertEqual([], acks)
            self.assertEqual([], quality)
            self.assertEqual(1, len(prefixes))
            self.assertEqual(initial["receipt"], final["receipt"])
            self.assertEqual(initial["keeper"], prefixes[0]["keeper"])
            self.assertEqual(initial["keeper"], final["keeper"])
            if not history:
                self.assertEqual([(pending, market)], failed_pending)
            self.assertEqual(original_payload, repr((bar,) if history else (pending, market)))
            expected_stock, expected_index = int(stage != "stock_insert"), int(stage == "index_commit")
            self.assertEqual((expected_stock, expected_index), (len(prefixes[0]["bars"]), len(prefixes[0]["index"])))
            self.assertEqual(expected_stock + expected_index, len(prefixes[0]["metadata"]))
            if stage.endswith("commit"):
                self.assertEqual(1, len(committed))
                self.assertFalse(committed[0]["in_transaction"])
                self.assertEqual(committed[0]["state"], prefixes[0])
            else:
                self.assertEqual([], committed)
            for row in prefixes[0]["metadata"]:
                self.assertEqual(minute, datetime.fromisoformat(row[3]))
                self.assertEqual(now, datetime.fromisoformat(row[4]))
                self.assertEqual("kiwoom-ka10080" if history else "kiwoom-websocket-0B" if row[1] == "TARGET" else "kiwoom-websocket-index", row[10])
            counts = {key: sum(statement.lstrip().upper().startswith(key.upper()) for statement in sql)
                      for key in ("select", "delete", "begin", "commit", "rollback")}
            result = {"stage": stage, "day": day, "cleanup": cleanup, "connections": len(connections), **counts,
                      "clock": len(clock_calls), "sql": sql, "original_payload": original_payload,
                      "failures": failures.copy(), "success_acks": acks.copy(), "quality_acks": quality.copy(),
                      "committed_before_raise": committed, "at_failure": prefixes[0], "after_drain": final,
                      "cutoff": writer._minute_retention_cutoff.isoformat(), "keeper_exact": True}
            if recover:
                with closing(native_connect(path)) as connection:
                    connection.execute("DROP TRIGGER IF EXISTS reject_insert")
                    connection.execute("DROP TRIGGER IF EXISTS reject_cleanup")
                    connection.commit()
                enqueue()
                writer.start()
                self.assertTrue(writer.stop_and_drain())
                self.assertEqual(1, len(acks))
                self.assertEqual(int(history), len(quality))
                if history:
                    self.assertEqual([("TARGET", {"generation": 23, "minutes": (minute,)})], quality)
                self.assertEqual(initial["keeper"], state()["keeper"])
                self.assertEqual(int(day != "expired"), len(state()["bars"]))
                self.assertEqual(int(not history and day != "expired"), len(state()["index"]))
                self.assertEqual(kept.date(), writer._minute_retention_cutoff)
                self.assertEqual(result["failures"], failures)
                result["same_writer_retry"] = {"success_acks": acks, "quality_acks": quality, "after_retry": state()}
            return result

    def test_failed_native_commit_return_and_partial_index_failure_keep_failure_ack_and_existing_cutoff(self):
        for stage in ("history_commit", "stock_commit", "index_commit", "index_insert", "stock_insert"):
            with self.subTest(stage=stage):
                observed = self._observe_failed_native_commit_return(stage, recover=True)
                self.assertEqual([], observed["after_drain"]["bars"])
                self.assertEqual([], observed["after_drain"]["index"])
                self.assertEqual([], observed["after_drain"]["metadata"])
                self.assertEqual(1, len(observed["failures"]))
        for cleanup in ("delete", "commit"):
            with self.subTest(cleanup=cleanup):
                observed = self._observe_failed_native_commit_return("index_commit", cleanup=cleanup, recover=True)
                self.assertEqual(["minute_failed", "retention_failed"], [event[0] for event in observed["failures"]])
                self.assertEqual("2026-09-21", observed["failures"][1][1])
                self.assertEqual("native cleanup DELETE failed" if cleanup == "delete" else "native cleanup COMMIT return lost", observed["failures"][1][2])
                self.assertEqual(int(cleanup == "delete"), len(observed["after_drain"]["bars"]))
                self.assertEqual(int(cleanup == "delete"), len(observed["after_drain"]["index"]))

    def _observe_metadata_only_retention(self, kind, mode, *, day="expired", variant=None, cleanup=None, recover=False, applied=True):
        from datetime import timedelta
        from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository

        now = as_kst(datetime(2026, 10, 21, 10))
        kept = now - timedelta(days=30)
        minute = {"expired": kept - timedelta(days=1), "cutoff": kept, "current": now - timedelta(minutes=1)}[day]
        code = "KEEP" if variant == "skip" else "market:kospi" if variant == "legacy" else "TARGET"
        minutes = () if variant in {"empty", "legacy", "empty_dict"} else (kept,) if variant == "skip" else (minute,)
        gaps = {} if variant == "empty_dict" else {code: minutes}
        native_connect = sqlite3.connect
        clock_calls, statements, connections, commits, failures, acks, data_acks, events = [], [], [], [], [], [], [], []

        def clock():
            clock_calls.append(1)
            return now

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path, now_provider=clock)
            repository.upsert_bars("KEEP", (MinuteOhlcv(kept, 100, 110, 90, 105, 10),))
            repository.upsert_market_index_minutes({("kosdaq", kept): (1., 2., 1., 2., 10.)})
            repository.record_history_sync("KEEP", kept.date(), kept, 1)
            if variant == "legacy":
                repository.upsert_market_index_minutes({("kospi", now - timedelta(minutes=1)): (3., 4., 2., 3., 20.)})

            def state():
                with closing(native_connect(path)) as connection:
                    return {
                        "metadata": connection.execute("SELECT * FROM market_data_observation_meta WHERE subject=? ORDER BY observation_key", (code,)).fetchall(),
                        "bars": connection.execute("SELECT * FROM minute_bars ORDER BY trade_date,stock_code,minute").fetchall(),
                        "index": connection.execute("SELECT * FROM market_index_minute_bars ORDER BY trade_date,market,minute").fetchall(),
                        "receipt": connection.execute("SELECT * FROM minute_history_sync_log ORDER BY trade_date,stock_code").fetchall(),
                        "keeper": (
                            connection.execute("SELECT * FROM minute_bars WHERE stock_code='KEEP'").fetchall(),
                            connection.execute("SELECT * FROM market_index_minute_bars WHERE market='kosdaq'").fetchall(),
                            connection.execute("SELECT * FROM minute_history_sync_log WHERE stock_code='KEEP'").fetchall(),
                            connection.execute("SELECT * FROM market_data_observation_meta WHERE subject IN ('KEEP','market:kosdaq') ORDER BY subject,observation_key").fetchall()),
                    }

            initial = state()
            writer = MarketCacheWriter(path, now_provider=clock)
            if applied:
                writer.enqueue_minute_retention(kept.date())
                writer.start()
                self.assertTrue(writer.stop_and_drain())
            self.assertEqual(kept.date() if applied else None, writer._minute_retention_cutoff)
            message = "native metadata INSERT failed" if mode == "precommit" else "native metadata COMMIT return lost"
            cleanup_message = "native cleanup metadata DELETE failed" if cleanup == "precommit" else "native cleanup COMMIT return lost"
            failed_prefixes = []

            def failed(*args):
                label = "gap_failed" if kind == "gap" else "history_failed"
                failures.append((label, repr(args[0]), args[-1]))
                events.append((label, args[-1]))
                failed_prefixes.append(state())

            def acknowledged(received_code, value):
                acks.append((received_code, value, state()))
                events.append(("quality", received_code, value))

            writer.gap_failed.connect(failed, Qt.ConnectionType.DirectConnection)
            writer.history_failed.connect(failed, Qt.ConnectionType.DirectConnection)
            writer.retention_failed.connect(lambda cutoff, error: (failures.append(("retention_failed", cutoff.isoformat(), error)), events.append(("retention_failed", error))), Qt.ConnectionType.DirectConnection)
            writer.history_quality_saved.connect(acknowledged, Qt.ConnectionType.DirectConnection)
            writer.history_saved.connect(lambda *args: data_acks.append(args), Qt.ConnectionType.DirectConnection)
            writer.minute_saved.connect(lambda *args: data_acks.append(args), Qt.ConnectionType.DirectConnection)
            with closing(native_connect(path)) as connection:
                if mode == "precommit":
                    connection.execute(f"CREATE TRIGGER reject_metadata BEFORE INSERT ON market_data_observation_meta WHEN NEW.subject='{code}' BEGIN SELECT RAISE(ABORT,'{message}'); END")
                if cleanup == "precommit":
                    connection.execute(f"CREATE TRIGGER reject_cleanup BEFORE DELETE ON market_data_observation_meta WHEN OLD.subject='{code}' AND substr(OLD.observation_key,1,10)<'2026-09-21' BEGIN SELECT RAISE(ABORT,'{cleanup_message}'); END")
                connection.commit()

            class NativeReturnConnection(sqlite3.Connection):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.role = None
                    connections.append(1)
                    self.set_trace_callback(statements.append)

                def execute(self, sql, *args, **kwargs):
                    upper = sql.lstrip().upper()
                    if upper.startswith("DELETE FROM MINUTE_BARS"):
                        self.role = "cleanup"
                    elif upper.startswith("INSERT INTO MARKET_DATA_OBSERVATION_META"):
                        self.role = "metadata"
                    return super().execute(sql, *args, **kwargs)

                def committed(self):
                    commits.append({"boundary": "explicit commit" if kind == "gap" else "native context __exit__", "in_transaction": self.in_transaction, "state": state()})
                    if mode == "postcommit":
                        raise OSError(message)

                def commit(self):
                    super().commit()
                    if self.role == "cleanup":
                        if cleanup == "postcommit":
                            raise OSError(cleanup_message)
                    elif kind == "gap":
                        self.committed()

                def __exit__(self, *args):
                    result = super().__exit__(*args)  # CPython's native context COMMIT, not Python .commit().
                    if args[0] is None and kind == "empty":
                        self.committed()
                    return result

            def connect(*args, **kwargs):
                kwargs["factory"] = NativeReturnConnection
                return native_connect(*args, **kwargs)

            def enqueue():
                if kind == "gap":
                    writer.enqueue_gap_minutes(gaps)
                else:
                    writer.enqueue_empty_coverage(code, minutes, 29)

            original_payload = repr(gaps if kind == "gap" else (code, minutes, 29))
            clock_calls.clear()
            with patch("sqlite3.connect", side_effect=connect):
                enqueue()
                writer.start()
                self.assertTrue(writer.stop_and_drain())
            final = state()
            self.assertEqual([], data_acks)
            self.assertEqual(original_payload, repr(gaps if kind == "gap" else (code, minutes, 29)))
            for key in ("bars", "index", "receipt", "keeper"):
                self.assertEqual(initial[key], final[key], key)
            attempted = variant != "empty_dict"
            self.assertEqual(int(attempted), len(clock_calls))
            expected_failure = int(attempted and mode != "success")
            self.assertEqual(expected_failure, len(failed_prefixes))
            self.assertEqual(int(kind == "empty" and mode == "success"), len(acks))
            if expected_failure:
                self.assertEqual(message, failures[0][-1])
                self.assertEqual(repr(gaps) if kind == "gap" else repr(code), failures[0][1])
            if acks:
                expected_minutes = () if variant in {"empty", "skip"} else minutes
                self.assertEqual((code, {"generation": 29, "minutes": expected_minutes, "empty": True}), acks[0][:2])
            self.assertEqual(int(attempted and mode != "precommit"), len(commits))
            if commits:
                self.assertFalse(commits[0]["in_transaction"])
                if mode == "postcommit":
                    self.assertEqual(commits[0]["state"], failed_prefixes[0])
                if acks:
                    self.assertEqual(commits[0]["state"], acks[0][2])
            if not variant and mode != "precommit":
                rows = commits[0]["state"]["metadata"]
                self.assertEqual(1, len(rows))
                self.assertEqual(minute, datetime.fromisoformat(rows[0][3]))
                self.assertEqual(now, datetime.fromisoformat(rows[0][4]))
                self.assertEqual(minute.isoformat(timespec="minutes"), rows[0][2])
                self.assertEqual("estimated", rows[0][7])
                self.assertEqual(("missing", "realtime", "subscriber-gap") if kind == "gap" else ("complete", "query", "kiwoom-ka10080-coverage-empty"), tuple(rows[0][8:11]))
            counts = {key: sum(sql.lstrip().upper().startswith(key.upper()) for sql in statements) for key in ("select", "delete", "begin", "commit", "rollback")}
            result = {"kind": kind, "mode": mode, "day": day, "variant": variant, "cleanup": cleanup, "applied": applied,
                      "connections": len(connections), **counts, "clock": len(clock_calls), "sql": statements,
                      "original_payload": original_payload, "commits": commits, "failure_prefixes": failed_prefixes,
                      "failures": failures.copy(), "quality_acks": acks.copy(), "data_acks": data_acks.copy(),
                      "events": events.copy(), "after_drain": final, "initial": initial,
                      "cutoff": writer._minute_retention_cutoff.isoformat() if writer._minute_retention_cutoff is not None else None}
            if recover:
                with closing(native_connect(path)) as connection:
                    connection.execute("DROP TRIGGER IF EXISTS reject_metadata")
                    connection.execute("DROP TRIGGER IF EXISTS reject_cleanup")
                    connection.commit()
                enqueue()
                writer.start()
                self.assertTrue(writer.stop_and_drain())
                self.assertEqual(result["failures"], failures)
                self.assertEqual(int(kind == "empty"), len(acks))
                if acks:
                    self.assertEqual((code, {"generation": 29, "minutes": minutes, "empty": True}), acks[-1][:2])
                self.assertEqual([], state()["metadata"])
                self.assertEqual(initial["keeper"], state()["keeper"])
                self.assertEqual(kept.date(), writer._minute_retention_cutoff)
                result["same_writer_retry"] = {"after_retry": state(), "quality_acks": acks.copy()}
            return result

    def test_gap_and_empty_metadata_only_jobs_preserve_original_outcome_then_reapply_existing_cutoff(self):
        for kind in ("gap", "empty"):
            for mode in ("success", "precommit", "postcommit"):
                with self.subTest(kind=kind, mode=mode):
                    observed = self._observe_metadata_only_retention(kind, mode, recover=mode != "success")
                    self.assertEqual([], observed["after_drain"]["metadata"])
                    self.assertEqual(int(mode != "success"), len(observed["failures"]))
            for cleanup in ("precommit", "postcommit"):
                with self.subTest(kind=kind, cleanup=cleanup):
                    observed = self._observe_metadata_only_retention(kind, "postcommit", cleanup=cleanup, recover=True)
                    self.assertEqual(["gap_failed" if kind == "gap" else "history_failed", "retention_failed"], [row[0] for row in observed["failures"]])
                    self.assertEqual("2026-09-21", observed["failures"][1][1])
                    self.assertEqual(int(cleanup == "precommit"), len(observed["after_drain"]["metadata"]))
        for cleanup in ("precommit", "postcommit"):
            with self.subTest(empty_success_cleanup=cleanup):
                observed = self._observe_metadata_only_retention("empty", "success", cleanup=cleanup)
                self.assertEqual(["quality", "retention_failed"], [event[0] for event in observed["events"]])
                self.assertEqual(["retention_failed"], [event[0] for event in observed["failures"]])
                self.assertEqual(1, len(observed["quality_acks"]))
                self.assertEqual(int(cleanup == "precommit"), len(observed["after_drain"]["metadata"]))
        for kind, variant in (("gap", "empty"), ("gap", "empty_dict"), ("gap", "legacy"), ("empty", "empty"), ("empty", "skip")):
            with self.subTest(kind=kind, variant=variant):
                observed = self._observe_metadata_only_retention(kind, "success", day="current", variant=variant)
                self.assertEqual([], observed["failures"])
                self.assertEqual(0, observed["connections"] if variant == "empty_dict" else observed["delete"])

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
