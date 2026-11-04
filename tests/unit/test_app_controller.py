from __future__ import annotations

import gc
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing, nullcontext
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from kiwoom_monitor.domain.time_basis import as_kst
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import MarketIndexTick, TradeTick
from kiwoom_monitor.presentation.app_controller import (
    AppController, AppShutdownActions, AppRankingActions, AppSecondaryActions,
    AppRuntimeActions, SecondaryLoadingRequest,
)
from kiwoom_monitor.presentation.main_window import MainWindow
from kiwoom_monitor.presentation.settings_request_worker import SettingsRequestWorker


class DelayedProducer(QThread):
    result = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.started_run = Event()
        self.release = Event()

    def run(self):
        self.started_run.set()
        self.release.wait(3)
        self.result.emit()


class FakeWriter:
    def __init__(self, events, *, completes=True):
        self.events = events
        self.running = True
        self.completes = completes

    def isRunning(self):
        return self.running

    def requestInterruption(self):
        self.events.append("entry_stop")

    def stop_and_drain(self):
        self.events.append("cache_drain")
        self.running = not self.completes
        return self.completes

    def has_pending_work(self):
        return False


class CapturingCacheWriter(QObject):
    minute_saved = Signal()
    minute_failed = Signal(object, object, str)
    price_failed = Signal(object, object, object, object, str)
    history_saved = Signal(str)
    history_failed = Signal(str, str)
    history_quality_saved = Signal(str, object)
    gap_failed = Signal(object, str)
    daily_high_failed = Signal(str, str)
    fundamentals_failed = Signal(str, str)
    retention_failed = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.enqueue_price_cache = Mock()
        self.enqueue_minute_bars = Mock()
        self.enqueue_gap_minutes = Mock()
        self.enqueue_minute_retention = Mock()
        self.start = Mock()

    def isRunning(self):
        return False

    def stop_and_drain(self):
        return True


class CacheFailureProducer(DelayedProducer):
    def __init__(self, writer, minute, price, parent):
        super().__init__(parent)
        self.writer = writer
        self.minute = minute
        self.price = price

    def run(self):
        self.started_run.set()
        self.release.wait(3)
        self.writer.minute_failed.emit(*self.minute, "injected failure")
        self.writer.price_failed.emit(*self.price, "injected failure")


class DelayedHistoryProducer(DelayedProducer):
    history = Signal(str, object)

    def run(self):
        self.started_run.set()
        self.release.wait(3)
        self.history.emit("005930", (
            MinuteOhlcv(datetime.now().replace(second=0, microsecond=0),
                        71000, 71200, 70800, 71100, 300),
        ))


class DelayedTradeProducer(DelayedProducer):
    trade = Signal(object)

    def run(self):
        self.started_run.set()
        self.release.wait(3)
        self.trade.emit(TradeTick("005930", 71200, 100, 7120000, 2, 71300, "100000"))


class DelayedRankingProducer(DelayedProducer):
    completed = Signal(object)
    failed = Signal(str)

    def run(self):
        self.started_run.set()
        self.release.wait(3)
        # Deliberately emit despite interruption to exercise the old-result guard.
        self.completed.emit((object(),))


class AppControllerTests(unittest.TestCase):
    def test_price_writer_uses_current_loader_clock_and_shutdown_reloads_complete_cache(self):
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            repository = StockRepository(path)
            repository.upsert("A", "A", "KOSPI")
            repository.update_fundamentals("A", 900, 50)
            old = datetime(2026, 10, 16, 9)
            current = datetime(2026, 10, 16, 10)
            controller = self.controller([], monitor_database_path=path,
                                         ranking_loader=SimpleNamespace(server_now=lambda: old))
            controller.start_market_cache_writer()
            # The writer borrows the current loader, including after runtime replacement.
            controller.ranking_loader = SimpleNamespace(server_now=lambda: current)
            controller.queue_price_cache("A", price=100, high=110, market_cap=1000)
            self.assertFalse(controller.request_close())
            self.wait_until(controller.request_close)
            self.assertFalse(controller.market_cache_writer.isRunning())
            cached = repository.load_ranking_cache(("A",), ("A",), current.date())
            self.assertEqual({"A": 100}, cached.last_prices)
            self.assertEqual({"A": 110}, cached.intraday_highs)
            self.assertEqual(1000, cached.fundamentals["A"].market_cap_eok)
            with closing(sqlite3.connect(path)) as con:
                self.assertEqual("2026-10-16T10:00:00+09:00", con.execute(
                    "SELECT last_price_updated_at FROM stocks").fetchone()[0])

    def test_price_cache_without_writer_uses_same_transaction_and_restores_failed_batch(self):
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            repository = StockRepository(path)
            repository.upsert("A", "A")
            now = datetime(2026, 10, 16, 10)
            controller = self.controller([], stock_lookup=repository,
                                         ranking_loader=SimpleNamespace(server_now=lambda: now))
            with closing(sqlite3.connect(path)) as con:
                con.execute("CREATE TRIGGER fail_high BEFORE INSERT ON intraday_highs BEGIN SELECT RAISE(ABORT,'high fault'); END")
                con.commit()
            controller.queue_price_cache("A", price=100, high=110, market_cap=1000)
            controller.flush_price_cache()
            self.assertEqual({"A": 100}, self.price_retry_values(controller, 0))
            self.assertEqual({(now.date(), "A"): 110}, self.price_retry_values(controller, 1))
            self.assertEqual({"A": 1000}, self.price_retry_values(controller, 2))
            with closing(sqlite3.connect(path)) as con:
                self.assertEqual((None, None), con.execute("SELECT last_price,market_cap FROM stocks").fetchone())
                con.execute("DROP TRIGGER fail_high")
                con.commit()
            controller.queue_price_cache("A", price=105)
            controller.flush_price_cache()
            self.assertFalse(controller.pending_prices or controller.pending_highs or controller.pending_market_caps)
            self.assertEqual({"A": 105}, repository.load_last_prices(("A",)))
            self.assertEqual({"A": 110}, repository.load_intraday_highs(("A",), now.date()))

    def test_price_failure_keeps_original_day_timestamp_and_bounded_retry_through_close(self):
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        original = as_kst(datetime(2026, 10, 19, 23, 59, 58))
        for offset, high in ((4, None), (4, 90), (1, 115)):
            with self.subTest(offset=offset, high=high), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "monitor.sqlite3"
                Database(path).initialize()
                repository = StockRepository(path)
                repository.upsert("A", "A")
                now = [original]
                controller = self.controller([], stock_lookup=repository,
                    ranking_loader=SimpleNamespace(server_now=lambda: now[0]))
                with closing(sqlite3.connect(path)) as con:
                    con.execute("CREATE TRIGGER fail_high BEFORE INSERT ON intraday_highs BEGIN SELECT RAISE(ABORT,'high fault'); END")
                    con.commit()
                controller.queue_price_cache("A", price=100, high=120, market_cap=1000)
                for _ in range(4):
                    # No writer: synchronous failure must also hold the close boundary.
                    self.assertFalse(controller.request_close())
                    self.wait_until(lambda: controller._shutdown_stage == "cache_failed")
                    self.assertEqual(1, len(controller._price_cache_retries))
                    context = next(iter(controller._price_cache_retries.values()))[3]
                    self.assertEqual(original, context["observed_at"])
                    self.assertEqual(1, context["receipt"])
                with closing(sqlite3.connect(path)) as con:
                    self.assertEqual((None, None), con.execute("SELECT last_price,market_cap FROM stocks").fetchone())
                    con.execute("DROP TRIGGER fail_high")
                    con.commit()
                now[0] = original + timedelta(seconds=offset)
                # Replacing the API clock must not change the retained job's timestamp.
                controller.ranking_loader = SimpleNamespace(server_now=lambda: now[0])
                if high is not None:
                    controller.queue_price_cache("A", price=90, high=high, market_cap=900)
                self.assertFalse(controller.request_close())
                self.wait_until(controller.request_close)
                with closing(sqlite3.connect(path)) as con:
                    rows = con.execute("SELECT trade_date,high_price FROM intraday_highs ORDER BY trade_date").fetchall()
                    stock = con.execute("SELECT last_price,last_price_updated_at,market_cap,updated_at FROM stocks").fetchone()
                expected = [(original.date().isoformat(), 120)]
                if high is not None and now[0].date() != original.date():
                    expected.append((now[0].date().isoformat(), high))
                self.assertEqual(expected, rows)
                timestamp = (original if high is None else now[0]).isoformat()
                self.assertEqual((100 if high is None else 90, timestamp,
                                  1000 if high is None else 900, timestamp), stock)
                self.assertFalse(controller._price_cache_retries)
                self.assertLessEqual(len(controller._price_cache_latest), 3)

    def test_midnight_high_batch_and_late_native_failure_preserve_day_latest_peer_and_clock(self):
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        original = as_kst(datetime(2026, 10, 19, 23, 59, 58))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = StockRepository(path)
            for code in ("A", "B"):
                repository.upsert(code, code)
            now = [original]
            controller = self.controller([], monitor_database_path=path,
                ranking_loader=SimpleNamespace(server_now=lambda: now[0]))
            # Two days queued before the first flush remain one normal writer job.
            controller.queue_price_cache("A", high=120, observed_at=original)
            now[0] = original + timedelta(seconds=4)
            controller.queue_price_cache("A", high=90, observed_at=now[0])
            controller.flush_price_cache()
            controller.start_market_cache_writer()
            self.assertTrue(controller.market_cache_writer.stop_and_drain())
            self.assertEqual({"A": 120}, repository.load_intraday_highs(("A",), original.date()))
            self.assertEqual({"A": 90}, repository.load_intraday_highs(("A",), now[0].date()))
            # Clear only this isolated fixture so the first failed 120 is absent from storage.
            with closing(sqlite3.connect(path)) as con:
                con.execute("DELETE FROM intraday_highs")
                con.execute("CREATE TRIGGER fail_high BEFORE INSERT ON intraday_highs BEGIN SELECT RAISE(ABORT,'queued high fault'); END")
                con.commit()
            failed, peer, retried = Event(), Event(), Event()
            calls = []
            save = StockRepository.update_price_cache
            def observed(repo, prices, highs, caps, trade_date, **kwargs):
                number = len(calls) + 1
                calls.append((dict(prices), dict(highs), dict(caps), kwargs["observed_at"]))
                try:
                    save(repo, prices, highs, caps, trade_date, **kwargs)
                except Exception:
                    failed.set()
                    raise
                else:
                    (peer if number == 2 else retried).set()
            try:
                with patch.object(StockRepository, "update_price_cache", observed):
                    now[0] = original
                    controller.queue_price_cache("A", price=100, high=120, market_cap=1000)
                    controller.queue_price_cache("B", price=80, high=85, market_cap=800)
                    controller.flush_price_cache()
                    controller.market_cache_writer.start()
                    self.assertTrue(failed.wait(3))
                    with closing(sqlite3.connect(path)) as con:
                        con.execute("DROP TRIGGER fail_high")
                        con.commit()
                    now[0] = original + timedelta(seconds=1)
                    controller.queue_price_cache("A", price=90, high=115, market_cap=900)
                    controller.flush_price_cache()
                    self.assertTrue(peer.wait(3))
                    # A real worker emitted the old failure; GUI handling follows the new COMMIT.
                    self.app.processEvents()
                    controller.flush_price_cache()
                    self.assertTrue(retried.wait(3))
                    self.assertTrue(controller.market_cache_writer.stop_and_drain())
                self.assertEqual({"B": 80}, calls[-1][0])
                self.assertEqual({"B": 800}, calls[-1][2])
                self.assertEqual(original, calls[-1][3])
                with closing(sqlite3.connect(path)) as con:
                    self.assertEqual([("A", 90, (original + timedelta(seconds=1)).isoformat(), 900),
                                      ("B", 80, original.isoformat(), 800)], con.execute(
                        "SELECT code,last_price,last_price_updated_at,market_cap FROM stocks ORDER BY code").fetchall())
                self.assertEqual({"A": 120, "B": 85}, repository.load_intraday_highs(("A", "B"), original.date()))
                self.assertFalse(controller._price_cache_retries)
            finally:
                self.assertTrue(controller.market_cache_writer.stop_and_drain())
                controller._stop_storage_timers()

    def test_legacy_date_only_price_failure_is_not_discarded_by_receipt_metadata(self):
        now = datetime(2026, 10, 20, 0, 0, 2)
        old_day = date(2026, 10, 19)
        for supplied_day in (old_day, object()):
            with self.subTest(supplied_day=supplied_day):
                writer = CapturingCacheWriter()
                with patch("kiwoom_monitor.presentation.app_controller.MarketCacheWriter", return_value=writer):
                    controller = self.controller([], monitor_database_path=Path("unused.sqlite3"),
                        ranking_loader=SimpleNamespace(server_now=lambda: now))
                controller.queue_price_cache("A", price=100, high=120, market_cap=1000)
                controller.flush_price_cache()
                # A legacy emitter cannot identify the dispatch sequence. Retain
                # its values rather than treating receipt zero as stale evidence.
                controller.on_price_cache_failed({"A": 100}, {"A": 120}, {"A": 1000},
                                                 supplied_day, "legacy failure")
                self.assertEqual({"A": 100}, self.price_retry_values(controller, 0))
                expected_day = old_day if isinstance(supplied_day, date) else now.date()
                self.assertEqual({(expected_day, "A"): 120}, self.price_retry_values(controller, 1))
                controller.flush_price_cache()
                self.assertEqual(2, writer.enqueue_price_cache.call_count)
                self.assertEqual(expected_day, writer.enqueue_price_cache.call_args.args[3])
                self.assertEqual(0, writer.enqueue_price_cache.call_args.kwargs["receipt"])
                controller._stop_storage_timers()

    @staticmethod
    def price_retry_values(controller, field):
        return {key: value for batch in controller._price_cache_retries.values()
                for key, value in batch[field].items()}

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controllers = []
        self.threads = []
        self.windows = []

    def tearDown(self):
        for thread in self.threads:
            thread.release.set()
            thread.wait(4000)
        for window in self.windows:
            window.close()
        for controller in self.controllers:
            controller._shutdown_timer.stop()
        self.app.processEvents()
        for window in self.windows:
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        for controller in self.controllers:
            controller.deleteLater()
            QCoreApplication.sendPostedEvents(controller, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        gc.collect()

    def controller(self, events, **kwargs):
        controller = AppController(**kwargs)
        self.controllers.append(controller)
        timer = QTimer(controller)
        timer.start(50000)
        controller.configure_shutdown(AppShutdownActions(
            timers=(timer,), tick_timer=lambda: None,
            save_view_state=lambda: events.append("view"),
            stop_visual_work=lambda: events.append("visual"),
            start_exit_backup_if_needed=lambda: events.append("backup"),
            flush_partial_top20=lambda: events.append("top20"),
            stop_auxiliaries=lambda: events.append("auxiliaries"),
        ))
        for name, label in (("flush_price_cache", "prices"), ("flush_minute_cache", "minutes")):
            original = getattr(controller, name)
            setattr(controller, name, lambda original=original, label=label: (events.append(label), original()))
        controller.shutdown_ready.connect(lambda: events.append("ready"))
        return controller

    def wait_until(self, condition):
        deadline = time.monotonic() + 4
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition(), "Qt shutdown did not complete")

    def ranking_controller(self, events, *, now=None, nas=True, nxt_started=False):
        state = SimpleNamespace(now=now or datetime(2026, 10, 6, 10, 0), modal=False)
        loader = SimpleNamespace(server_now=lambda: state.now, EXPECTED_STOCKS=2,
                                 last_response_from_storage=False)
        controller = self.controller(events, ranking_loader=loader, realtime_worker_factory=Mock())
        controller.configure_runtime(AppRuntimeActions(apply_runtime=lambda runtime: None))
        controller.configure_ranking(AppRankingActions(
            query_type=lambda: "5", environment=lambda: "real",
            has_blocking_modal=lambda: state.modal,
            apply_ranking=lambda stocks, summary, codes: events.append(("apply", codes)),
            realtime_codes=lambda codes: codes,
            uses_nas_source=lambda: nas,
            start_nxt_eligibility=lambda codes: events.append(("nxt", codes)) or nxt_started,
            start_minute_history=lambda codes: events.append(("stored_minutes", codes)) or False,
        ), AppSecondaryActions(
            prepare=lambda codes: events.append(("prepare", codes)) or SecondaryLoadingRequest(codes),
            phase_started=lambda request, phase: events.append(("phase", phase.value)),
            start_minute_history=lambda codes, forced: events.append(("minute", codes, forced)) or False,
            start_daily_high=lambda codes, forced: events.append(("daily", codes, forced)) or False,
            start_fundamentals=lambda codes: events.append(("fundamentals", codes)) or False,
            start_nxt_eligibility=lambda codes: events.append(("secondary_nxt", codes)) or False,
            start_catalog=lambda: events.append("catalog"),
        ))
        return controller, loader, state

    @staticmethod
    def stocks(*codes):
        return tuple(SimpleNamespace(code=code, rank=i+1, name=code)
                     for i, code in enumerate(codes))

    def test_storage_timers_coalesce_latest_prices_and_mixed_minutes_without_window(self):
        now = datetime(2026, 10, 6, 10, 0)
        writer = CapturingCacheWriter()
        with patch("kiwoom_monitor.presentation.app_controller.MarketCacheWriter", return_value=writer):
            controller = self.controller([], monitor_database_path=Path("unused.sqlite3"),
                                         minute_bar_repository=Mock(),
                                         ranking_loader=SimpleNamespace(server_now=lambda: now))
        self.assertEqual((1000, 1000), (
            controller.price_cache_timer.interval(), controller.minute_cache_timer.interval(),
        ))
        for timer in (controller.price_cache_timer, controller.minute_cache_timer):
            self.assertIs(controller, timer.parent())
            timer.setInterval(20)
        first = MinuteOhlcv(now, 100, 101, 99, 100, 1)
        latest = MinuteOhlcv(now, 100, 103, 99, 103, 3)
        controller.queue_price_cache("005930", price=100, high=101, market_cap=200.)
        controller.queue_price_cache("005930", price=103, high=103, market_cap=202.)
        controller.queue_price_cache("000660", price=90)
        controller.queue_minute_bar("005930", first)
        controller.queue_minute_bar("005930", latest)
        for value, trade in ((2500., 10.), (2510., 11.), (2495., 12.)):
            controller.queue_market_index("KOSPI", now, value, trade)
        self.assertFalse(writer.enqueue_price_cache.called)
        self.assertFalse(writer.enqueue_minute_bars.called)
        self.wait_until(lambda: writer.enqueue_minute_bars.called and writer.enqueue_price_cache.called)
        writer.enqueue_price_cache.assert_called_once_with(
            {"005930": 103, "000660": 90}, {(now.date(), "005930"): 103}, {"005930": 202.}, now.date(),
            observed_at=as_kst(now), receipt=1,
        )
        writer.enqueue_minute_bars.assert_called_once_with(
            {("005930", now): latest}, {("KOSPI", now): (2500., 2510., 2495., 2495., 12.)},
        )
        self.assertFalse(controller.pending_prices or controller.pending_highs or controller.pending_market_caps)
        self.assertFalse(controller.pending_minutes or controller.pending_market_minutes)

    def test_writer_free_price_failure_preserves_all_values_and_retries_latest(self):
        for failed_method in ("update_last_prices", "update_last_market_caps", "update_intraday_highs"):
            with self.subTest(failed_method=failed_method):
                saved = {}
                lookup = Mock(spec=["update_last_prices", "update_last_market_caps", "update_intraday_highs"])
                controller = self.controller([], stock_lookup=lookup,
                    ranking_loader=SimpleNamespace(server_now=lambda: datetime(2026, 10, 6, 10, 0)))
                controller.queue_price_cache("005930", price=100, high=110, market_cap=200.)
                def fail(*args):
                    controller.queue_price_cache("005930", price=103, high=113, market_cap=203.)
                    raise OSError("injected fallback failure")
                getattr(lookup, failed_method).side_effect = fail
                controller.flush_price_cache()
                self.assertEqual({"005930": 103}, controller.pending_prices)
                self.assertEqual({(date(2026, 10, 6), "005930"): 113}, controller.pending_highs)
                self.assertEqual({"005930": 203.}, controller.pending_market_caps)
                for method in ("update_last_prices", "update_last_market_caps", "update_intraday_highs"):
                    getattr(lookup, method).side_effect = lambda values, *rest, method=method: saved.update({method: dict(values)})
                controller.flush_price_cache()
                self.assertEqual({"005930": 103}, saved["update_last_prices"])
                self.assertEqual({"005930": 113}, saved["update_intraday_highs"])
                self.assertEqual({"005930": 203.}, saved["update_last_market_caps"])
                self.assertFalse(controller.pending_prices or controller.pending_highs or controller.pending_market_caps)
                controller._stop_storage_timers()

    def test_queued_writer_failures_preserve_newer_values_and_close_does_not_restart_retry(self):
        now = datetime(2026, 10, 6, 10, 0)
        old = MinuteOhlcv(now, 100, 100, 100, 100, 1)
        latest = MinuteOhlcv(now, 100, 103, 100, 103, 3)
        writer = CapturingCacheWriter()
        with patch("kiwoom_monitor.presentation.app_controller.MarketCacheWriter", return_value=writer):
            controller = self.controller([], monitor_database_path=Path("unused.sqlite3"),
                                         minute_bar_repository=Mock(),
                                         ranking_loader=SimpleNamespace(server_now=lambda: now))
        controller.queue_price_cache("005930", price=103, high=104, market_cap=202.)
        controller.queue_minute_bar("005930", latest)
        controller.queue_market_index("KOSPI", now, 2510., 12.)
        controller._stop_storage_timers()
        failures = (
            ({("005930", now): old, ("000660", now): old},
             {("KOSPI", now): (2500., 2500., 2500., 2500., 10.)}),
            ({"005930": 100, "000660": 90}, {"005930": 101}, {"005930": 200.}, now.date()),
        )
        producer = CacheFailureProducer(writer, *failures, controller)
        self.threads.append(producer)
        producer.start()
        self.assertTrue(producer.started_run.wait(1))
        producer.release.set()
        producer.wait(1000)
        # A worker-thread signal must remain queued until the GUI handles it.
        self.assertNotIn("000660", controller.pending_prices)
        self.wait_until(lambda: "000660" in self.price_retry_values(controller, 0))
        self.assertEqual({"005930": 103}, controller.pending_prices)
        self.assertEqual({"000660": 90}, self.price_retry_values(controller, 0))
        self.assertEqual({(now.date(), "005930"): 104}, controller.pending_highs)
        self.assertEqual({"005930": 202.}, controller.pending_market_caps)
        self.assertEqual(latest, controller.pending_minutes[("005930", now)])
        self.assertEqual(old, controller.pending_minutes[("000660", now)])
        self.assertEqual(2510., controller.pending_market_minutes[("KOSPI", now)][3])
        self.assertTrue(controller.price_cache_timer.isActive())
        self.assertTrue(controller.minute_cache_timer.isActive())
        self.assertFalse(controller.request_close())
        self.wait_until(controller.request_close)
        writer.minute_failed.emit(*failures[0], "late failure")
        writer.price_failed.emit(*failures[1], "late failure")
        self.assertFalse(controller.price_cache_timer.isActive())
        self.assertFalse(controller.minute_cache_timer.isActive())
        self.assertEqual(old, controller.pending_minutes[("000660", now)])

    def test_main_window_realtime_signals_feed_app_buffers_and_saved_signal_keeps_repair_receiver(self):
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import ProgramTradeTick
        now = datetime(2026, 10, 6, 10, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            writer = CapturingCacheWriter()
            with patch("kiwoom_monitor.presentation.app_controller.MarketCacheWriter", return_value=writer), \
                    patch.object(MainWindow, "_start_top20_market_repair") as repair:
                window = MainWindow(database.settings, monitor_database_path=path,
                                    minute_bar_repository=MinuteBarRepository(path),
                                    ranking_loader=SimpleNamespace(server_now=lambda: now))
                window.activate_startup()
                self.windows.append(window)
                controller = window._app_controller
                window._row_by_code["005930"] = 0
                window._pc_market_session.ensure_minute_day(now, flush=lambda: None, retain=None)
                controller.realtime.trade_received.emit(TradeTick(
                    "005930", 71000, 100, 7100000, 2, 71200, "100000", market_cap_eok=200,
                ))
                controller.realtime.market_state_received.emit(MarketIndexTick(
                    "KOSPI", "100000", 2500., cumulative_trade_value_million_won=1200,
                ))
                self.assertEqual(71000, window._current_prices["005930"])
                self.assertEqual(200., window._realtime_market_caps["005930"])
                self.assertEqual(2500., window._pc_market_session.market_state_at_entry()["KOSPI"]["index"])
                self.assertEqual("2026-10-06T10:00:00",
                    window._pc_market_session.market_state_at_entry()["observed_at"])
                controller.realtime.program_trade_received.emit(ProgramTradeTick(
                    "005930", "100000", 10, -2, 1200, -3,
                ))
                self.assertEqual(10, window._pc_market_session.investor_flow_at_entry("005930")["program_trade"]["net_buy_quantity"])
                self.assertEqual({"005930": 71000}, controller.pending_prices)
                self.assertEqual({(now.date(), "005930"): 71200}, controller.pending_highs)
                self.assertEqual(71000, controller.pending_minutes[("005930", now)].close_price)
                self.assertEqual(12., controller.pending_market_minutes[("KOSPI", now)][4])
                window._trade_tick_flush_timer.stop()
                controller._stop_storage_timers()
                controller.price_cache_timer.timeout.emit()
                controller.minute_cache_timer.timeout.emit()
                writer.enqueue_price_cache.assert_called_once()
                writer.enqueue_minute_bars.assert_called_once()
                repair.assert_not_called()
                writer.minute_saved.emit()
                repair.assert_called_once_with()
                # The writer-free repository fallback delivers the same existing UI effect.
                repair.reset_mock()
                controller.market_cache_writer = None
                controller.queue_market_index("KOSPI", now, 2501., 13.)
                controller.minute_cache_timer.stop()
                controller.flush_minute_cache()
                repair.assert_called_once_with()
                window.close()

    def test_minute_history_finished_signal_keeps_forced_false_result_and_normal_fallback(self):
        codes = ("005930", "000660")
        for forced, expected in (
            (False, [("daily", codes, False), ("fundamentals", codes),
                     ("secondary_nxt", codes), "catalog"]),
            (True, [("daily", codes, True)]),
        ):
            with self.subTest(forced=forced):
                events = []
                controller, _, _ = self.ranking_controller(events)
                controller.minute_history.finished.emit(codes, forced)
                self.assertEqual(expected, events)
        events = []
        unconfigured = self.controller(events)
        unconfigured.minute_history.finished.emit(codes, False)
        unconfigured.minute_history.finished.emit(codes, True)
        self.assertEqual([], events)

    def test_minute_worker_failure_keeps_daily_followup_and_late_signal_boundaries(self):
        from kiwoom_monitor.application.daily_high_service import DailyHighTargets
        from kiwoom_monitor.infrastructure.kiwoom_rest.daily_high_worker import DailyHighWorker
        from kiwoom_monitor.infrastructure.kiwoom_rest.minute_history_worker import MinuteHistoryWorker

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        codes = ("005930",)
        now = as_kst(datetime(2026, 10, 6, 10, 0))
        for forced in (False, True):
            with self.subTest(forced=forced):
                database = Database(Path(temporary.name) / f"monitor-{forced}.sqlite3")
                database.initialize()
                minute_load = Mock(side_effect=ValueError("controlled minute failure"))
                daily_load = Mock(return_value=DailyHighTargets(None, None, None))
                minute_factory = Mock(side_effect=lambda requested: MinuteHistoryWorker(
                    SimpleNamespace(load_today_with_completion=minute_load), requested,
                    now_provider=lambda: now))
                daily_factory = Mock(side_effect=lambda requested: DailyHighWorker(
                    SimpleNamespace(load=daily_load), requested))
                with patch.object(MainWindow, "_open_api_settings"), \
                        patch.object(MainWindow, "_start_fundamentals_loading", return_value=False) as fundamentals, \
                        patch.object(MainWindow, "_start_nxt_eligibility_loading", return_value=False), \
                        patch.object(MainWindow, "_start_daily_krx_catalog_sync"):
                    window = MainWindow(database.settings,
                        minute_history_worker_factory=minute_factory,
                        daily_high_worker_factory=daily_factory)
                    window.activate_startup()
                    self.windows.append(window)
                    controller = window._app_controller
                    controller.ranking_loader = SimpleNamespace(server_now=lambda: now)
                    failures, completed = [], []
                    controller.minute_history.failed.connect(failures.append)
                    controller.daily_high.finished.connect(completed.append)

                    self.assertTrue(window._start_minute_history_loading(codes, force=forced))
                    self.wait_until(lambda: completed == [codes]
                        and controller.minute_history.worker is None
                        and controller.daily_high.worker is None)
                    minute_factory.assert_called_once_with(codes)
                    daily_factory.assert_called_once_with(codes)
                    minute_load.assert_called_once_with(codes[0], now)
                    daily_load.assert_called_once_with(codes[0])
                    self.assertEqual(1, len(failures))
                    self.assertIn("controlled minute failure", failures[0])
                    fundamentals.assert_called_once_with(codes)

                    completed_fundamentals = fundamentals.call_count
                    for boundary in ("close", "reload", "priority"):
                        with self.subTest(boundary=boundary):
                            controller._closing = boundary == "close"
                            controller._api_reloading = boundary == "reload"
                            if boundary in {"reload", "priority"}:
                                controller.ranking_execution.begin_priority_preparation()
                            try:
                                controller.minute_history.finished.emit(codes, forced)
                                daily_factory.assert_called_once_with(codes)
                                self.assertEqual(completed_fundamentals, fundamentals.call_count)
                            finally:
                                controller._closing = False
                                controller._api_reloading = False
                                controller.ranking_execution.end_priority_preparation()
                    window.close()
                    self.wait_until(controller.request_close)
                    self.assertTrue(window.close())

    def test_daily_high_finished_signal_preserves_cleanup_first_fallback_and_guards(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        now = as_kst(datetime(2026, 10, 6, 20, 10))
        target = now.date()
        codes = ("OK", "RETRY", "UNCONFIRMED")
        for fundamentals_started, nxt_started in ((True, False), (False, True), (False, False)):
            with self.subTest(fundamentals=fundamentals_started, nxt=nxt_started):
                database = Database(Path(temporary.name) / f"daily-{fundamentals_started}-{nxt_started}.sqlite3")
                database.initialize()
                phases, effects, observations = [], [], []

                def start_fundamentals(requested):
                    observations.append((bool(window._after_close_finalization_codes),
                        window._after_close_finalization_date,
                        bool(window._after_close_minute_received), bool(window._after_close_daily_received),
                        bool(window._daily_high_basis_refresh_expected), bool(window._daily_high_basis_refresh_received),
                        database.settings.get("daily_high_adjusted_basis_version")))
                    phases.append(("fundamentals", requested))
                    return fundamentals_started

                def start_nxt(requested):
                    phases.append(("nxt", requested))
                    return nxt_started

                with patch.object(MainWindow, "_open_api_settings"), \
                        patch.object(MainWindow, "_start_fundamentals_loading", side_effect=start_fundamentals), \
                        patch.object(MainWindow, "_start_nxt_eligibility_loading", side_effect=start_nxt), \
                        patch.object(MainWindow, "_start_daily_krx_catalog_sync", side_effect=lambda: phases.append("catalog")):
                    window = MainWindow(database.settings)
                    window.activate_startup()
                    self.windows.append(window)
                    controller = window._app_controller
                    controller.ranking_loader = SimpleNamespace(server_now=lambda: now)
                    window._daily_bar_repository = SimpleNamespace(
                        mark_finalized=lambda requested, day: effects.append(("finalized", requested, day)),
                        mark_unconfirmed=lambda code, day, missing, attempts: effects.append(
                            ("unconfirmed", code, day, missing, attempts)))
                    controller.entry_snapshot_writer = SimpleNamespace(enqueue_program_backfill=
                        lambda day, requested: effects.append(("program", day, requested)))

                    def prime():
                        effects.clear()
                        window._after_close_finalization_codes = set(codes)
                        window._after_close_finalization_date = target
                        window._after_close_minute_received = {"OK"}
                        window._after_close_daily_received = {"OK"}
                        window._finalization_attempts = {(target, "RETRY"): 1, (target, "UNCONFIRMED"): 2}
                        window._daily_high_basis_refresh_expected = {"OK"}
                        window._daily_high_basis_refresh_received = {"OK"}
                        database.settings.set("daily_high_adjusted_basis_version", "0")

                    expected_effects = [("finalized", ("OK",), target),
                        ("program", target, codes),
                        ("unconfirmed", "UNCONFIRMED", target, ("분봉", "일봉"), 2)]
                    try:
                        prime()
                        controller.daily_high.finished.emit(codes)
                        self.assertEqual(expected_effects, effects)
                        self.assertEqual([(False, None, False, False, False, False, "1")], observations)
                        expected_phases = [("fundamentals", codes)]
                        if not fundamentals_started:
                            expected_phases.append(("nxt", codes))
                            if not nxt_started:
                                expected_phases.append("catalog")
                        self.assertEqual(expected_phases, phases)
                        self.assertEqual(now + timedelta(minutes=5), window._finalization_retry_after[(target, "RETRY")])

                        for boundary in ("close", "reload", "priority"):
                            with self.subTest(boundary=boundary):
                                prime()
                                controller._closing = boundary == "close"
                                controller._api_reloading = boundary == "reload"
                                if boundary in {"reload", "priority"}:
                                    controller.ranking_execution.begin_priority_preparation()
                                try:
                                    controller.daily_high.finished.emit(codes)
                                    self.assertEqual(expected_effects, effects)
                                    self.assertFalse(window._after_close_finalization_codes)
                                    self.assertIsNone(window._after_close_finalization_date)
                                    self.assertFalse(window._daily_high_basis_refresh_expected)
                                    self.assertEqual("1", database.settings.get("daily_high_adjusted_basis_version"))
                                    self.assertEqual(expected_phases, phases)
                                    self.assertEqual(1, len(observations))
                                finally:
                                    controller._closing = False
                                    controller._api_reloading = False
                                    controller.ranking_execution.end_priority_preparation()
                    finally:
                        controller.entry_snapshot_writer = None
                        window._daily_bar_repository = None
                    window.close()
                    self.wait_until(controller.request_close)
                    self.assertTrue(window.close())

    def test_ranking_signal_applies_before_nas_history_subscription_and_secondary(self):
        events = []
        controller, _, _ = self.ranking_controller(events)
        codes = ("005930", "000660")
        with patch.object(controller.realtime, "start", side_effect=lambda *args, **kwargs: events.append(("subscribe", args, kwargs)) or True):
            controller.ranking.completed.emit(self.stocks(*codes))
            self.assertEqual(["apply", "stored_minutes", "subscribe"], [row[0] for row in events])
            controller.realtime.subscription_ready.emit(("005930",))
            self.assertEqual(3, len(events))
            controller.realtime.subscription_ready.emit(codes)
            controller.realtime.subscription_ready.emit(codes)
            self.assertEqual(1, sum(row[0] == "prepare" for row in events if isinstance(row, tuple)))
            self.assertEqual(["minute", "daily", "fundamentals", "secondary_nxt"],
                             [row[0] for row in events if isinstance(row, tuple) and row[0] in {"minute", "daily", "fundamentals", "secondary_nxt"}])
            fallback = next(iter(controller._followup_timers))
            fallback.timeout.emit()
            self.assertEqual(1, sum(row[0] == "prepare" for row in events if isinstance(row, tuple)))
            controller.ranking.completed.emit(self.stocks(*codes))
            controller.realtime.subscription_ready.emit(codes)
            self.assertEqual(2, sum(row[0] == "prepare" for row in events if isinstance(row, tuple)))

    def test_nxt_finish_filters_subscription_and_preserves_catalog_then_followup_order(self):
        events = []
        controller, _, _ = self.ranking_controller(events, now=datetime(2026, 10, 6, 8, 10), nxt_started=True)
        codes = ("005930", "000660")
        with patch.object(controller.realtime, "start", side_effect=lambda *args, **kwargs: events.append(("subscribe", args, kwargs)) or True):
            controller.ranking.completed.emit(self.stocks(*codes))
            self.assertEqual([("apply", codes), ("nxt", codes)], events)
            controller.nxt_enabled_codes.add("005930")
            controller.nxt_eligibility.finished.emit()
            self.assertEqual("catalog", events[2])
            self.assertEqual(("005930",), events[3][1][0])
            self.assertEqual(codes, events[3][2]["followup_codes"])
            self.assertEqual(("prepare", codes), events[4])

    def test_direct_partial_retries_but_stored_partial_waits_and_modal_keeps_latest(self):
        events = []
        controller, loader, state = self.ranking_controller(events)
        waiting = []
        controller.ranking_waiting.connect(lambda *args: waiting.append(args))
        controller.ranking.completed.emit(self.stocks("005930"))
        self.assertEqual((1, 2, False, True), waiting[-1])
        self.assertEqual(1500, controller._ranking_retry_timer.interval())
        controller._ranking_retry_timer.stop()
        loader.last_response_from_storage = True
        controller.ranking.completed.emit(self.stocks("005930"))
        self.assertEqual((1, 2, True, False), waiting[-1])
        self.assertFalse(controller._ranking_retry_timer.isActive())
        self.assertTrue(controller.ranking_timer.isActive())
        state.modal = True
        controller.ranking.completed.emit(self.stocks("005930", "000660"))
        controller.ranking.completed.emit(self.stocks("035420", "005930"))
        self.assertEqual([], events)
        self.assertTrue(controller._deferred_ranking_timer.isActive())
        state.modal = False
        with patch.object(controller.realtime, "start", return_value=True):
            controller.flush_deferred_ranking()
        self.assertEqual(("apply", ("035420", "005930")), events[0])
        self.assertFalse(controller.ranking_execution.has_deferred_response)

    def test_ranking_boundaries_coalesce_while_worker_runs_then_request_once(self):
        events = []
        controller, loader, _ = self.ranking_controller(events)
        producer = DelayedRankingProducer()
        self.threads.append(producer)
        controller.ranking._worker_factory = lambda _loader: producer
        # Do not apply this fixture's object payload; observe the finish scheduling instead.
        controller.ranking.completed.disconnect(controller.handle_ranking_response)
        controller.ranking.start(loader)
        self.assertTrue(producer.started_run.wait(1))
        requested = []
        controller.ranking_requested.connect(lambda: requested.append(True))
        controller.on_ranking_timer()
        controller.on_ranking_timer()
        self.assertEqual([], requested)
        producer.release.set()
        self.wait_until(lambda: len(requested) == 1)
        self.app.processEvents()
        self.assertEqual([True], requested)
        # The feature controller has already handled finished/deleteLater.
        self.threads.remove(producer)

    def test_subscription_same_target_updates_venue_and_stops_outside_market(self):
        events = []
        controller, _, state = self.ranking_controller(events)
        codes = ("005930", "000660")
        worker = SimpleNamespace(isRunning=lambda: True)
        self.addCleanup(lambda: setattr(controller.realtime, "_worker", None))
        with patch.object(controller.realtime, "start", return_value=True) as start, \
                patch.object(controller.realtime, "update_codes") as update, \
                patch.object(controller.realtime, "stop", return_value=True) as stop:
            controller.start_realtime_subscription(codes)
            self.assertEqual(1, start.call_count)
            controller.realtime._worker = worker
            controller.start_realtime_subscription(codes)
            update.assert_not_called()
            state.now = datetime(2026, 10, 6, 18, 0)
            controller.nxt_enabled_codes.add("005930")
            controller.ranked_codes = codes
            controller.on_realtime_session_boundary()
            # The existing schedule keeps KRX after-market observation enabled.
            update.assert_called_once_with(codes, ("005930",))
            state.now = datetime(2026, 10, 6, 21, 0)
            controller.on_realtime_session_boundary()
            stop.assert_called_once()
            self.assertEqual((), controller.realtime_subscription.current_codes)
            controller.realtime._worker = None

    def test_shutdown_cancels_scheduled_ranking_deferred_response_and_followup(self):
        events = []
        controller, _, state = self.ranking_controller(events)
        state.modal = True
        controller.ranking.completed.emit(self.stocks("005930", "000660"))
        controller._ranking_retry_timer.start(1500)
        controller._schedule_followup_fallback(("005930", "000660"))
        self.assertTrue(controller.request_close())
        self.assertFalse(controller.ranking_timer.isActive())
        self.assertFalse(controller._ranking_retry_timer.isActive())
        self.assertFalse(controller._deferred_ranking_timer.isActive())
        self.assertEqual(set(), controller._followup_timers)
        state.modal = False
        controller.flush_deferred_ranking()
        self.assertNotIn("apply", [row[0] for row in events if isinstance(row, tuple)])

    def test_nxt_finish_during_runtime_exchange_does_not_restart_old_workers(self):
        events = []
        controller, _, _ = self.ranking_controller(events)
        controller.ranked_codes = ("005930",)
        controller._api_reloading = True
        with patch.object(controller.realtime, "start") as start:
            controller.nxt_eligibility.finished.emit()
        start.assert_not_called()
        self.assertEqual([], events)

    def test_main_window_ranking_signal_updates_table_before_subscription_and_followup(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        database = Database(Path(temporary.name) / "monitor.sqlite3")
        database.initialize()
        with patch.object(MainWindow, "_start_market_cap_reference_loading"), \
                patch.object(MainWindow, "_start_minute_history_loading", return_value=False), \
                patch.object(MainWindow, "_start_daily_high_loading", return_value=False), \
                patch.object(MainWindow, "_start_fundamentals_loading", return_value=False), \
                patch.object(MainWindow, "_start_nxt_eligibility_loading", return_value=False), \
                patch.object(MainWindow, "_start_daily_krx_catalog_sync"):
            window = MainWindow(database.settings, realtime_worker_factory=Mock())
            window.activate_startup()
            self.windows.append(window)
            controller = window._app_controller
            controller.ranking_loader = SimpleNamespace(server_now=lambda: datetime(2026, 10, 6, 10, 0))
            codes = ("005930", "000660")
            observations = []
            def subscribed(active, nxt, **kwargs):
                observations.append(tuple(window._table.item(i, 1).data(Qt.ItemDataRole.UserRole)
                                          for i in range(window._table.rowCount())))
                return True
            stocks = tuple(SimpleNamespace(code=code, name=code, rank=i+1, change_rate="1.0")
                           for i, code in enumerate(codes))
            with patch.object(controller.realtime, "start", side_effect=subscribed):
                controller.ranking.completed.emit(stocks)
            self.assertEqual([codes], observations)
            self.assertEqual(codes, controller.ranked_codes)
            # Display order can change without changing the applied ranking snapshot.
            window._row_by_code = {"000660": 0, "005930": 1}
            with patch.object(controller, "start_secondary_loading") as secondary:
                controller.realtime.subscription_ready.emit(codes)
                controller.realtime.subscription_ready.emit(codes)
                secondary.assert_called_once_with(codes)
            window.close()

    def test_controller_owns_all_feature_resources_and_optional_paths(self):
        controller = self.controller([])
        features = (controller.ranking, controller.realtime, controller.minute_history,
                    controller.fundamentals, controller.daily_high, controller.historical_high,
                    controller.nxt_eligibility, controller.krx_catalog, controller.image_ocr,
                    controller.top20_repair, controller.google_drive, controller.updates)
        self.assertEqual(12, len(set(features)))
        self.assertTrue(all(feature.parent() is controller for feature in features))
        self.assertTrue(all(feature.thread() is controller.thread() for feature in features))
        self.assertIsNone(controller.market_cache_writer)
        self.assertIsNone(controller.entry_snapshot_writer)
        self.assertTrue(controller.request_close())

    def test_writers_start_once_after_connections_and_never_restart_on_close(self):
        events = []
        with patch("kiwoom_monitor.presentation.app_controller.MarketCacheWriter") as cache, \
                patch("kiwoom_monitor.presentation.app_controller.EntrySnapshotWriter") as entry:
            controller = self.controller(events, monitor_database_path=Path("monitor"),
                                         journal_database_path=Path("journal"))
            cache.return_value.start.assert_not_called()
            entry.return_value.start.assert_not_called()
            cache.return_value.start.side_effect = lambda: events.append("cache_start")
            entry.return_value.start.side_effect = lambda: events.append("entry_start")
            events.append("signals_connected")
            controller.start_market_cache_writer()
            controller.start_market_cache_writer()
            controller.start_entry_snapshot_writer()
            controller.start_entry_snapshot_writer()
            self.assertEqual(["signals_connected", "cache_start", "entry_start"], events)
            cache.return_value.isRunning.return_value = False
            entry.return_value.isRunning.return_value = False
            entry.return_value.has_pending_work.return_value = False
            self.assertFalse(controller.request_close())
            self.wait_until(lambda: "ready" in events)
            controller.start_market_cache_writer()
            cache.return_value.start.assert_called_once()
            entry.return_value.start.assert_called_once()

    def test_flush_caches_before_drain_and_timeout_prevents_accept(self):
        events = []
        controller = self.controller(events)
        writer = FakeWriter(events, completes=False)
        controller.market_cache_writer = writer
        self.assertFalse(controller.request_close())
        self.assertFalse(controller.request_close())
        self.assertTrue(controller.closing)
        self.assertLess(events.index("prices"), events.index("cache_drain"))
        self.assertLess(events.index("minutes"), events.index("cache_drain"))
        self.assertNotIn("auxiliaries", events)
        controller._advance_shutdown()
        self.assertNotIn("ready", events)
        writer.running = False
        self.wait_until(lambda: "ready" in events)
        self.assertTrue(controller.request_close())
        for name in ("view", "backup", "prices", "minutes", "cache_drain", "auxiliaries", "ready"):
            self.assertEqual(1, events.count(name), name)

    def test_late_producer_result_is_delivered_before_writer_is_stopped(self):
        events = []
        controller = self.controller(events)
        controller.market_cache_writer = FakeWriter(events)
        entry = FakeWriter(events)
        controller.entry_snapshot_writer = entry
        thread = DelayedProducer(controller)
        self.threads.append(thread)
        controller.top20_nas_workers.add(thread)
        thread.result.connect(lambda: events.append("late_result"))
        thread.finished.connect(lambda: controller.top20_nas_workers.discard(thread))
        thread.start()
        self.assertTrue(thread.started_run.wait(1))
        self.assertFalse(controller.request_close())
        self.assertTrue(thread.isInterruptionRequested())
        self.assertNotIn("entry_stop", events)
        self.assertNotIn("cache_drain", events)
        thread.release.set()
        self.wait_until(lambda: "cache_drain" in events)
        self.assertLess(events.index("late_result"), events.index("minutes"))
        self.assertLess(events.index("late_result"), events.index("entry_stop"))
        self.assertNotIn("ready", events)  # Entry writer still alive.
        entry.running = False
        self.wait_until(lambda: "ready" in events)

    def test_entry_enriched_commit_ack_loss_blocks_close_while_selective_seed_stays_unbound_until_same_writer_retry(self):
        from dataclasses import replace
        from scripts import preserve_current_settings as operator
        from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import (
            TradeEntrySnapshot, scoped_snapshot_execution_key,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, journal = root / "monitor.sqlite3", root / "journal.sqlite3"
            at = as_kst(datetime(2026, 10, 19, 10, 30))
            database = Database(source, now_provider=lambda: at)
            database.initialize()
            database.settings.set_many({"theme_active_profile": "기본 테마", "rank_query_type": "5"})
            repository = JournalRepository(journal)
            scope = AccountScope("kiwoom", AccountEnvironment.MOCK, "00000000-0000-4000-8000-000000000041")
            snapshot = TradeEntrySnapshot(
                scoped_snapshot_execution_key("entry-closure-041", scope), "order-041", "005930",
                "삼성전자", "매수", at, 70000, 1, "KRX", origin_scope=scope,
                market_state={"source": "original-041"}, orderbook={"bid": 69900},
                capture_state="realtime_core",
                investor_flow={"program_trade": {"available": True, "source": "original-0w-041"}},
            )
            news = ({"title": "original news-041", "published_at": "2026-10-19T10:29:00+09:00"},)
            investor = {"available": True, "source": "original-investor-041"}
            expected = replace(snapshot, news=news, capture_state="realtime_enriched",
                               investor_flow={**investor, "program_trade": snapshot.investor_flow["program_trade"]})
            loads, saves, failures, events = [], [], [], []
            committed, release = Event(), Event()
            lost_ack = []
            def load(code, observed):
                loads.append((code, observed))
                return dict(investor)
            native_save = JournalRepository.save_entry_snapshot
            def save(owner, value, now=None, *, account_scope=None):
                result = native_save(owner, value, now, account_scope=account_scope)
                saves.append((value, account_scope))
                if value.capture_state == "realtime_enriched" and not lost_ack:
                    lost_ack.append(value)
                    committed.set()
                    if not release.wait(3):
                        raise AssertionError("entry ACK-loss barrier was not released")
                    raise RuntimeError("041 enriched native COMMIT ACK loss")
                return result
            controller = self.controller(events, journal_database_path=journal, entry_investor_loader=load)
            writer = controller.entry_snapshot_writer
            writer.failed.connect(failures.append)
            try:
                with patch.object(JournalRepository, "save_entry_snapshot", autospec=True, side_effect=save), \
                        patch("kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer.news_at_execution",
                              return_value=news) as news_lookup:
                    writer.enqueue(snapshot)
                    controller.start_entry_snapshot_writer()
                    self.assertTrue(committed.wait(2))
                    self.assertTrue(writer.isRunning())
                    self.assertFalse(controller.request_close())
                    self.assertNotIn("ready", events)
                    # The row exists, but its caller has not received a native ACK.
                    stored = repository.load_entry_snapshots("005930", at, at + timedelta(seconds=1), scope)[0]
                    self.assertEqual(news, stored.news)
                    self.assertEqual(expected.investor_flow, stored.investor_flow)
                    release.set()
                    self.wait_until(lambda: not writer.isRunning())
                    self.wait_until(lambda: controller._shutdown_stage == "cache_failed")
                    self.assertTrue(any("041 enriched native COMMIT ACK loss" in error for error in failures))
                    self.assertTrue(writer.has_pending_work())
                    self.assertEqual(expected, writer._pending_job)
                    self.assertTrue(writer._pending_enriched)
                    self.assertNotIn("auxiliaries", events)
                    self.assertNotIn("ready", events)

                    # Only the settings DB is frozen here; the separate source
                    # journal owner explicitly still has an unacknowledged job.
                    artifact, target = root / "settings-cut", root / "query-role.sqlite3"
                    operator.capture_current_state(database=source, source_instance="fixture-source",
                        source_owner="fixture-settings-owner", operation_id="fixture-entry-cut",
                        output=artifact, sqlite_frozen=True, artifact_now=at)
                    manifest, records, digest = operator.verified_current_state_rows(artifact)
                    keys = {"theme_active_profile", "rank_query_type"}
                    theme_relations = set(operator.CURRENT_STATE_TABLES) - set(operator.TABLES)
                    entries = []
                    for record_id, item in records.items():
                        relation = item["relation"]
                        row = tuple(operator.decode_value(value) for value in item["row"])
                        chosen = (relation in theme_relations or
                                  relation in {"settings", "central_setting_versions"} and row[0] in keys)
                        entries.append(dict(record_id=record_id,
                            placement="TRANSFER" if chosen else "RETAIN_SOURCE",
                            **({"target_role": "query-local"} if chosen else {})))
                    selection = dict(format="current-state-selection/v1",
                        source_instance=manifest["source_instance"], source_owner=manifest["source_owner"],
                        operation_id=manifest["operation_id"], manifest_sha256=digest,
                        target_role="query-local", records=entries)
                    selected = operator.select_current_state_rows(artifact, selection)
                    self.assertEqual("CLOSED", selected["closure"]["state"])
                    seeded = operator.install_current_state(artifact=artifact, selection=selection, target=target,
                        operation_id="fixture-entry-install", target_id="fixture-entry-target", receipt_now=at)
                    self.assertEqual("OFFLINE_UNBOUND", seeded["runtime_state"])
                    self.assertFalse(seeded["role_ready"])
                    self.assertFalse(seeded["applied"])
                    self.assertFalse(seeded["order_authority"])
                    self.assertEqual("NOT_EVALUATED", seeded["consumer_status"])
                    with closing(sqlite3.connect(target)) as connection:
                        self.assertEqual([], connection.execute(
                            "SELECT name FROM sqlite_master WHERE name='trade_entry_snapshots'").fetchall())
                    self.assertTrue(writer.has_pending_work())
                    self.assertNotIn("ready", events)

                    # Preserve the already observed cut even if its providers are
                    # detached before an explicit retry by this same owner.
                    with writer.replace_sources(investor_loader=None, program_loader=None):
                        self.assertFalse(controller.request_close())
                    self.wait_until(lambda: "ready" in events)
                    self.assertFalse(writer.isRunning())
                    self.assertFalse(writer.has_pending_work())
                    self.assertEqual([(snapshot, scope), (expected, scope), (expected, scope)], saves)
                    self.assertEqual([("005930", at)], loads)
                    self.assertEqual(1, news_lookup.call_count)
                    stored = repository.load_entry_snapshots("005930", at, at + timedelta(seconds=1), scope)[0]
                    self.assertEqual(snapshot.execution_key, stored.execution_key)
                    self.assertEqual(scope, stored.origin_scope)
                    self.assertEqual(snapshot.market_state, stored.market_state)
                    self.assertEqual(snapshot.orderbook, stored.orderbook)
                    self.assertEqual(news, stored.news)
                    self.assertEqual(expected.investor_flow, stored.investor_flow)
                    with closing(sqlite3.connect(journal)) as connection:
                        self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM trade_entry_snapshots").fetchone()[0])
                    reopened = Database(target).initialize_query_role(role="query-local")
                    self.assertEqual(seeded["receipt"], reopened["receipt"])
                    self.assertFalse(reopened["role_ready"])
                    self.assertFalse(reopened["order_authority"])
            finally:
                release.set()
                writer.requestInterruption()
                self.assertTrue(writer.wait(4000))

    def test_entry_backfill_native_failure_blocks_close_and_retries_retained_jobs(self):
        from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import (
            TradeEntrySnapshot, scoped_snapshot_execution_key,
        )
        for kind in ("program", "investor"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "journal.sqlite3"
                repository = JournalRepository(path)
                scope = AccountScope("kiwoom", AccountEnvironment.MOCK, "00000000-0000-4000-8000-000000000031")
                at = datetime(2026, 10, 19, 10, 30)
                snapshot = TradeEntrySnapshot(
                    scoped_snapshot_execution_key("entry-031", scope), "order-031",
                    "005930", "삼성전자", "매수", at, 70000, 1, "KRX",
                    origin_scope=scope, market_state={"source": "original"},
                )
                repository.save_entry_snapshot(snapshot, account_scope=scope)
                from dataclasses import replace
                prefix = replace(snapshot, execution_key=scoped_snapshot_execution_key("prefix-031", scope),
                                 stock_code="000001", stock_name="prefix")
                repository.save_entry_snapshot(prefix, account_scope=scope)
                with closing(sqlite3.connect(path)) as con:
                    con.execute("CREATE TRIGGER fail_backfill BEFORE UPDATE OF investor_flow_json "
                                "ON trade_entry_snapshots WHEN OLD.stock_code='005930' "
                                "BEGIN SELECT RAISE(ABORT,'031 native fault'); END")
                    con.commit()
                entered, release = Event(), Event()
                loads = []
                def loader(_code, observed):
                    if kind == "investor" and observed.time().hour != 20:
                        return {}
                    loads.append(_code)
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError("backfill was not released")
                    value = {"available": True, "source": f"031-{kind}", "trade_time": "102900",
                             "as_of_date": "20261019", "foreign_net_buy_quantity": 1,
                             "institution_net_buy_quantity": 2}
                    return (value,) if kind == "program" else value
                events, failures = [], []
                controller = self.controller(events, journal_database_path=path,
                    **({"program_trade_loader": loader} if kind == "program"
                       else {"entry_investor_loader": loader}))
                writer = controller.entry_snapshot_writer
                writer.failed.connect(failures.append)
                getattr(writer, f"enqueue_{kind}_backfill")(at.date(), ("000001", "005930"))
                # This following job must remain available after a backfill fault.
                following = replace(snapshot, execution_key=scoped_snapshot_execution_key("following-031", scope),
                                    stock_code="000660", stock_name="SK하이닉스")
                writer.enqueue(following)
                controller.start_entry_snapshot_writer()
                try:
                    self.assertTrue(entered.wait(2))
                    self.assertFalse(controller.request_close())
                    release.set()
                    self.wait_until(lambda: not writer.isRunning())
                    self.wait_until(lambda: controller._shutdown_stage in {"ready", "cache_failed"})
                    self.assertNotIn("ready", events,
                        f"stage={controller._shutdown_stage}, queue={writer._queue.qsize()}, failures={failures}")
                    self.assertEqual("cache_failed", controller._shutdown_stage)
                    self.assertTrue(any("031 native fault" in message for message in failures))
                    self.assertNotIn("auxiliaries", events)
                    # Already observed payloads survive removal of their provider.
                    getattr(writer, f"set_{kind}_loader")(None)
                    self.assertFalse(controller.request_close())
                    self.wait_until(lambda: controller._shutdown_stage in {"cache_failed", "ready"})
                    self.assertEqual("cache_failed", controller._shutdown_stage)
                    self.assertNotIn("ready", events)
                    self.assertEqual(["000001", "005930"], loads)
                    with closing(sqlite3.connect(path)) as con:
                        con.execute("DROP TRIGGER fail_backfill")
                        con.commit()
                    self.assertFalse(controller.request_close())
                    self.wait_until(lambda: "ready" in events)
                    self.assertFalse(writer.isRunning())
                    self.assertFalse(writer.has_pending_work())
                    self.assertEqual({}, writer._pending_backfill_values)
                    self.assertEqual(["000001", "005930"], loads)
                    stored = repository.load_entry_snapshots("005930", at, at + timedelta(seconds=1), scope)[0]
                    value = stored.investor_flow["program_trade"] if kind == "program" else stored.investor_flow
                    self.assertTrue(value["backfilled"])
                    self.assertEqual(f"031-{kind}", value["source"])
                    self.assertEqual(snapshot.market_state, stored.market_state)
                    self.assertEqual(scope, stored.origin_scope)
                    completed_prefix = repository.load_entry_snapshots("000001", at, at + timedelta(seconds=1), scope)[0]
                    prefix_value = (completed_prefix.investor_flow["program_trade"] if kind == "program"
                                    else completed_prefix.investor_flow)
                    self.assertTrue(prefix_value["backfilled"])
                    self.assertEqual(1, len(repository.load_entry_snapshots("000660", at, at + timedelta(seconds=1), scope)))
                finally:
                    release.set()
                    writer.requestInterruption()
                    self.assertTrue(writer.wait(4000))

    def test_thread_still_running_after_controller_reference_release_is_waited(self):
        events = []
        controller = self.controller(events)
        thread = DelayedProducer(controller.google_drive)
        self.threads.append(thread)
        # A Drive controller can already have cleared its public reference.
        self.assertIsNone(controller.google_drive.worker)
        thread.start()
        self.assertTrue(thread.started_run.wait(1))
        self.assertFalse(controller.request_close())
        self.assertNotIn("minutes", events)
        thread.release.set()
        self.wait_until(lambda: "ready" in events)

    def test_shutdown_checks_dynamic_worker_set_again(self):
        events = []
        controller = self.controller(events)
        # Hashable dummy thread-like object; no Qt lifetime behavior is faked here.
        class Worker:
            def __init__(self, running): self.running = running; self.stops = 0
            def isRunning(self): return self.running
            def requestInterruption(self): self.stops += 1
        first = Worker(True)
        second = Worker(True)
        controller.top20_nas_workers.add(first)
        self.assertFalse(controller.request_close())
        controller.top20_nas_workers.remove(first)
        controller.top20_nas_workers.add(second)
        controller._advance_shutdown()
        self.assertEqual(1, second.stops)
        self.assertNotIn("prices", events)
        second.running = False
        self.wait_until(lambda: "ready" in events)

    def test_main_window_close_saves_final_prices_before_real_writer_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("INSERT INTO stocks(code,name) VALUES('005930','삼성전자')")
                connection.commit()
            window = MainWindow(database.settings, monitor_database_path=path,
                                minute_bar_repository=MinuteBarRepository(path))
            window.activate_startup()
            self.windows.append(window)
            window._app_controller.pending_prices = {"005930": 71200}
            writer = window._market_cache_writer
            close = QCloseEvent()
            window.closeEvent(close)
            self.assertFalse(close.isAccepted())
            self.wait_until(lambda: window._app_controller.request_close())
            self.assertFalse(writer.isRunning())
            self.assertFalse((path.parent / "logs" / "developer_checks").exists())
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(71200, connection.execute("SELECT last_price FROM stocks WHERE code='005930'").fetchone()[0])
            self.assertFalse(window._journal_news_timer.isActive())
            self.assertFalse(window._investment_notice_timer.isActive())
            self.assertNotIn("_closing", window.__dict__)
            self.assertNotIn("_market_cache_writer", window.__dict__)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
            self.windows.remove(window)

    def _observe_public_writer_free_failure(self, stage, *, closing=True, latest=False, recover=False):
        from contextlib import closing as closing_connection, nullcontext

        now = as_kst(datetime(2026, 10, 21, 10))
        minute = now - timedelta(minutes=1)
        original_connect = sqlite3.connect
        gap = stage.startswith("gap")
        failed = stage not in {"success", "gap_success"}
        clock_calls, job_connections, committed_prefixes, ack_prefixes = [], [], [], []

        def clock():
            clock_calls.append(1)
            return now

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            repository = MinuteBarRepository(path, now_provider=clock)
            repository.upsert_bars("KEEP", (MinuteOhlcv(minute, 200, 220, 190, 210, 30),))
            repository.upsert_market_index_minutes({("kosdaq", minute): (1., 2., 1., 2., 10.)})
            repository.record_history_sync("KEEP", now.date(), now, 1)
            window = MainWindow(database.settings, minute_bar_repository=repository,
                                ranking_loader=SimpleNamespace(server_now=clock, load_top_stocks=lambda: ()))
            window.activate_startup()
            controller = window._app_controller
            controller._initial_ranking_timer.stop()
            self.assertIsNone(window._monitor_database_path)
            self.assertIsNone(controller.market_cache_writer)
            self.assertIs(repository, controller.minute_bar_repository)

            def state():
                with closing_connection(original_connect(path)) as connection:
                    return {
                        "bars": connection.execute("SELECT * FROM minute_bars WHERE stock_code='TARGET' ORDER BY minute").fetchall(),
                        "index": connection.execute("SELECT * FROM market_index_minute_bars WHERE market='kospi' ORDER BY minute").fetchall(),
                        "metadata": connection.execute("SELECT * FROM market_data_observation_meta WHERE subject IN ('TARGET','market:kospi') ORDER BY subject,observation_key").fetchall(),
                        "keeper": (
                            connection.execute("SELECT * FROM minute_bars WHERE stock_code='KEEP'").fetchall(),
                            connection.execute("SELECT * FROM market_index_minute_bars WHERE market='kosdaq'").fetchall(),
                            connection.execute("SELECT * FROM minute_history_sync_log WHERE stock_code='KEEP'").fetchall(),
                            connection.execute("SELECT * FROM market_data_observation_meta WHERE subject IN ('KEEP','market:kosdaq') ORDER BY subject,observation_key").fetchall()),
                    }

            initial = state()
            old = MinuteOhlcv(minute, 100, 110, 90, 105, 10)
            newer = MinuteOhlcv(minute, 100, 120, 90, 115, 20)
            if gap:
                controller.pending_minute_gaps = {"TARGET": (minute,)}
            else:
                controller.queue_minute_bar("TARGET", old)
                controller.queue_market_index("kospi", minute, 2500., 12.)
            controller._stop_storage_timers()
            original_pending = repr((controller.pending_minutes, controller.pending_market_minutes, controller.pending_minute_gaps))
            message = "native fallback INSERT failed: " + stage if stage.endswith("insert") else "native fallback COMMIT return lost: " + stage
            with closing_connection(original_connect(path)) as connection:
                if stage.endswith("insert"):
                    table = "market_data_observation_meta" if gap else "minute_bars" if stage == "stock_insert" else "market_index_minute_bars"
                    connection.execute(f"CREATE TRIGGER reject_fallback BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'{message}'); END")
                connection.commit()
            injected = []

            def new_input():
                if latest and "latest" not in injected:
                    injected.append("latest")
                    controller.queue_minute_bar("TARGET", newer)
                    controller.queue_market_index("kospi", minute, 2510., 13.)

            class NativeFallbackConnection(sqlite3.Connection):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.role, self.job, self.sealed, self.sql = None, False, False, []
                    self.set_trace_callback(self.sql.append)

                def execute(self, sql, *args, **kwargs):
                    upper = sql.lstrip().upper()
                    if "MARKET_DATA_OBSERVATION_META" in upper or "MINUTE_BARS" in upper or "MARKET_INDEX_MINUTE_BARS" in upper:
                        self.job = True
                    if gap and upper.startswith("INSERT INTO MARKET_DATA_OBSERVATION_META"):
                        self.role = "gap"
                    return super().execute(sql, *args, **kwargs)

                def executemany(self, sql, *args, **kwargs):
                    upper = sql.lstrip().upper()
                    if upper.startswith("INSERT INTO MINUTE_BARS"):
                        self.role, self.job = "stock", True
                    elif upper.startswith("INSERT INTO MARKET_INDEX_MINUTE_BARS"):
                        self.role, self.job = "index", True
                    if self.role is not None and stage == self.role + "_insert":
                        new_input()
                    return super().executemany(sql, *args, **kwargs)

                def commit(self):
                    super().commit()
                    if self.role is not None and stage == self.role + "_commit" and "commit" not in injected:
                        injected.append("commit")
                        committed_prefixes.append({"in_transaction": self.in_transaction, "state": state()})
                        new_input()
                        raise OSError(message)

                def close(self):
                    if self.job and not self.sealed:
                        self.sealed = True
                        job_connections.append({"role": self.role, "sql": self.sql.copy()})
                    return super().close()

            def connect(*args, **kwargs):
                kwargs["factory"] = NativeFallbackConnection
                return original_connect(*args, **kwargs)

            controller.minute_cache_saved.connect(lambda: ack_prefixes.append(state()))
            clock_calls.clear()
            try:
                with patch.object(window._news_window, "stop", wraps=window._news_window.stop) as auxiliary_stop:
                    log_context = self.assertLogs("kiwoom_monitor.presentation.app_controller", level="WARNING") if failed else nullcontext()
                    with log_context as logged, patch("sqlite3.connect", side_effect=connect):
                        if closing:
                            event = QCloseEvent()
                            window.closeEvent(event)
                            accepted = event.isAccepted()
                        else:
                            controller.flush_minute_cache()
                            accepted = None
                    final = state()
                    messages = [record.getMessage() for record in logged.records] if failed else []
                    self.assertEqual([("분봉 누락 품질 저장 실패: " if gap else "실시간 분봉 DB 저장 실패: ") + message] if failed else [], messages)
                    self.assertEqual(initial["keeper"], final["keeper"])
                    self.assertEqual(int(not failed or gap), len(ack_prefixes))
                    if failed:
                        if gap:
                            self.assertEqual({"TARGET": (minute,)}, controller.pending_minute_gaps)
                        else:
                            self.assertEqual(newer if latest else old, controller.pending_minutes["TARGET", minute])
                            self.assertEqual(2510. if latest else 2500., controller.pending_market_minutes["kospi", minute][3])
                    if stage.endswith("commit"):
                        self.assertEqual(1, len(committed_prefixes))
                        self.assertFalse(committed_prefixes[0]["in_transaction"])
                        self.assertEqual(committed_prefixes[0]["state"], final)
                    statements = [sql for connection in job_connections for sql in connection["sql"]]
                    counts = {key: sum(sql.lstrip().upper().startswith(key.upper()) for sql in statements) for key in ("select", "delete", "begin", "commit", "rollback")}
                    result = {"stage": stage, "closing": closing, "latest": latest, "accepted": accepted,
                              "shutdown_stage": controller._shutdown_stage, "shutdown_cache_failed": controller._shutdown_cache_failed,
                              "timer_active": controller.minute_cache_timer.isActive(), "auxiliary_stops": auxiliary_stop.call_count,
                              "pending": repr((controller.pending_minutes, controller.pending_market_minutes, controller.pending_minute_gaps)),
                              "original_pending": original_pending, "messages": messages, "ack_prefixes": ack_prefixes.copy(),
                              "committed_prefixes": committed_prefixes, "after_attempt": final,
                              "connections": len(job_connections), **counts, "clock": len(clock_calls), "sql": statements}
                    if recover:
                        with closing_connection(original_connect(path)) as connection:
                            connection.execute("DROP TRIGGER IF EXISTS reject_fallback")
                            connection.commit()
                        if closing:
                            retry = QCloseEvent()
                            window.closeEvent(retry)
                            self.assertFalse(retry.isAccepted())
                            self.wait_until(lambda: controller._shutdown_stage == "ready")
                        else:
                            self.assertTrue(controller.minute_cache_timer.isActive())
                            controller.minute_cache_timer.stop()  # Consume the already scheduled single-shot timeout deterministically.
                            controller.minute_cache_timer.timeout.emit()
                            self.assertEqual({}, controller.pending_minutes)
                            self.assertEqual({}, controller.pending_market_minutes)
                            close = QCloseEvent()
                            window.closeEvent(close)
                            self.wait_until(lambda: controller._shutdown_stage == "ready")
                        self.assertEqual({}, controller.pending_minutes)
                        self.assertEqual({}, controller.pending_market_minutes)
                        self.assertEqual({}, controller.pending_minute_gaps)
                        self.assertFalse(controller.minute_cache_timer.isActive())
                        self.assertEqual(1, auxiliary_stop.call_count)
                        if not gap:
                            self.assertEqual(115 if latest else 105, state()["bars"][0][6])
                            self.assertEqual(2510. if latest else 2500., state()["index"][0][6])
                        self.assertEqual(initial["keeper"], state()["keeper"])
                        self.assertIsNone(controller.market_cache_writer)
                        result["recovery"] = {"stage": controller._shutdown_stage, "state": state(), "ack_count": len(ack_prefixes), "auxiliary_stops": auxiliary_stop.call_count}
                    return result
            finally:
                # Observation is complete; close admission before draining queued startup work.
                controller._closing = True
                controller._initial_ranking_timer.stop()
                controller._stop_timers()
                controller._shutdown_timer.stop()
                self.app.processEvents()
                producers = controller._producer_workers()
                if controller.top20_repair.worker is not None:
                    self.assertIn(controller.top20_repair.worker, [worker for worker, _ in producers])
                for worker, _ in producers:
                    if worker is not None and worker.isRunning():
                        worker.requestInterruption()
                        self.assertTrue(worker.wait(4000))
                self.app.processEvents()
                self.assertEqual([], [worker for worker in window.findChildren(QThread) if worker.isRunning()])
                window.deleteLater()
                QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
                self.app.processEvents()

    def test_public_writer_free_native_failure_preserves_pending_timer_and_explicit_close_retry(self):
        for stage in ("stock_insert", "stock_commit", "index_insert", "index_commit", "gap_insert", "gap_commit"):
            with self.subTest(stage=stage):
                result = self._observe_public_writer_free_failure(stage, latest=stage == "stock_commit", recover=True)
                self.assertFalse(result["accepted"])
                self.assertEqual("cache_failed", result["shutdown_stage"])
                self.assertTrue(result["shutdown_cache_failed"])
                self.assertFalse(result["timer_active"])
                self.assertEqual(0, result["auxiliary_stops"])
        for stage in ("stock_insert", "stock_commit"):
            with self.subTest(open_failure=stage):
                result = self._observe_public_writer_free_failure(stage, closing=False, latest=stage == "stock_commit", recover=True)
                self.assertTrue(result["timer_active"])
                self.assertFalse(result["shutdown_cache_failed"])

    def _observe_public_writer_free_applied_cutoff(self, kind, *, day="expired", postcommit=False, cleanup=None):
        import json
        from kiwoom_monitor.domain.realtime_gap import GapRange, RealtimeGap

        now = as_kst(datetime(2026, 10, 23, 10))
        kept = now - timedelta(days=30)
        minute = {"expired": kept - timedelta(days=1), "cutoff": kept,
                  "current": now - timedelta(minutes=1)}[day]
        original_connect = sqlite3.connect
        statements, connections, commits, acks, purges = [], [], [], [], []
        injection = {"write": postcommit, "cleanup": cleanup is not None}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            repository = MinuteBarRepository(path, now_provider=lambda: now)
            repository.upsert_bars("KEEP", (MinuteOhlcv(kept, 200, 220, 190, 210, 30),))
            repository.upsert_market_index_minutes({("kosdaq", kept): (1., 2., 1., 2., 10.)})
            repository.record_history_sync("KEEP", kept.date(), kept, 1)
            with patch.object(AppController, "start_initial_ranking"), \
                    patch.object(MainWindow, "_start_top20_market_repair"):
                window = MainWindow(database.settings, minute_bar_repository=repository,
                    ranking_loader=SimpleNamespace(server_now=lambda: now, load_top_stocks=lambda: ()))
                window.activate_startup()
            controller = window._app_controller
            self.assertIsNone(window._monitor_database_path)
            self.assertIsNone(controller.market_cache_writer)
            window._pc_market_session.ensure_minute_day(now, flush=window._app_controller.flush_minute_cache, retain=window._app_controller.queue_minute_retention if window._minute_bar_repository is not None else None)

            def state():
                with closing(original_connect(path)) as connection:
                    return {
                        "expired": tuple(connection.execute(sql, (kept.date().isoformat(),)).fetchall() for sql in (
                            "SELECT * FROM minute_bars WHERE trade_date<? ORDER BY stock_code,minute",
                            "SELECT * FROM market_index_minute_bars WHERE trade_date<? ORDER BY market,minute",
                            "SELECT * FROM market_data_observation_meta WHERE dataset_kind='minute_bar' AND substr(observation_key,1,10)<? ORDER BY subject,observation_key",
                            "SELECT * FROM minute_history_sync_log WHERE trade_date<? ORDER BY stock_code",
                        )),
                        "keeper": tuple(connection.execute(sql).fetchall() for sql in (
                            "SELECT * FROM minute_bars WHERE stock_code='KEEP'",
                            "SELECT * FROM market_index_minute_bars WHERE market='kosdaq'",
                            "SELECT * FROM minute_history_sync_log WHERE stock_code='KEEP'",
                            "SELECT * FROM market_data_observation_meta WHERE subject IN ('KEEP','market:kosdaq') ORDER BY subject,observation_key",
                        )),
                        "receipt": connection.execute("SELECT trade_date,stock_code,bar_count FROM minute_history_sync_log WHERE stock_code='TARGET'").fetchall(),
                    }

            if kind == "empty":
                gap = RealtimeGap(1, (GapRange("trade", "EMPTY", "KRX", minute, minute),), False)
                window._pc_market_session.minute_aggregator.mark_gap(gap, ("EMPTY",), now)
                generation = window._pc_market_session.minute_aggregator.gap_generation("EMPTY")
                window._pc_market_session.prepare_history(("EMPTY",), now, force=True)
                controller.minute_history.history_received.emit("EMPTY", ())
            initial = state()
            self.assertEqual(([], [], [], []), initial["expired"])
            controller.minute_cache_saved.connect(lambda: acks.append(state()))
            native_purge = repository.purge_before

            def purge(cutoff):
                purges.append(cutoff)
                return native_purge(cutoff)

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
                        if cleanup == "precommit" and injection["cleanup"]:
                            self.set_authorizer(lambda action, *_:
                                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DELETE else sqlite3.SQLITE_OK)
                    elif upper.startswith("INSERT INTO MINUTE_HISTORY_SYNC_LOG"):
                        self.role = "history"
                    elif upper.startswith("INSERT INTO MARKET_DATA_OBSERVATION_META") and kind in {"gap", "empty"}:
                        self.role = kind
                    return super().execute(sql, *args, **kwargs)

                def executemany(self, sql, *args, **kwargs):
                    upper = sql.lstrip().upper()
                    if upper.startswith("INSERT INTO MINUTE_BARS"):
                        self.role = "stock"
                    elif upper.startswith("INSERT INTO MARKET_INDEX_MINUTE_BARS"):
                        self.role = "index"
                    return super().executemany(sql, *args, **kwargs)

                def after_commit(self):
                    if self.role == "cleanup":
                        if cleanup == "postcommit" and injection["cleanup"]:
                            raise OSError("native cleanup COMMIT return lost")
                    elif injection["write"] and self.role == kind:
                        injection["write"] = False
                        commits.append({"role": self.role, "in_transaction": self.in_transaction, "state": state()})
                        raise OSError("native expired " + kind + " COMMIT return lost")

                def commit(self):
                    super().commit()
                    self.after_commit()

                def __exit__(self, *args):
                    result = super().__exit__(*args)
                    if args[0] is None and self.role == "empty":
                        self.after_commit()
                    return result

            def connect(*args, **kwargs):
                kwargs["factory"] = NativeReturnConnection
                return original_connect(*args, **kwargs)

            def deliver():
                if kind in {"batch", "stock", "index"}:
                    controller.queue_minute_bar("TARGET", MinuteOhlcv(minute, 100, 110, 90, 105, 10))
                    controller.queue_market_index("kospi", minute, 2500., 12.)
                    controller.minute_cache_timer.stop()
                    controller.flush_minute_cache()
                elif kind == "gap":
                    controller.queue_minute_gaps({"GAP": (minute,), "market:kospi": (minute,)})
                elif kind == "history":
                    controller.minute_history.history_received.emit("TARGET", (
                        MinuteOhlcv(minute, 100, 110, 90, 105, 10),
                        MinuteOhlcv(kept, 120, 130, 110, 125, 20),
                        MinuteOhlcv(now - timedelta(minutes=1), 140, 150, 130, 145, 30),
                    ))
                else:
                    controller.minute_history.coverage_received.emit("EMPTY", {
                        "complete": True, "empty_authority": True,
                        "checked_at": minute + timedelta(minutes=1), "present_minutes": (),
                    })

            try:
                with patch.object(sqlite3, "connect", side_effect=connect), \
                        patch.object(repository, "purge_before", purge):
                    deliver()
                    after_attempt = state()
                    result = {"kind": kind, "day": day, "postcommit": postcommit, "cleanup": cleanup,
                        "initial": initial, "after_attempt": after_attempt, "acks_after_attempt": acks.copy(),
                        "commits": commits.copy(), "pending_cutoff": controller.pending_minute_retention,
                        "timer_active": controller.minute_cache_timer.isActive(),
                        "pending_data": bool(controller.pending_minutes or controller.pending_market_minutes or controller.pending_minute_gaps),
                        "history_loaded": "TARGET" in window._pc_market_session.history_codes,
                        "empty_quality": window._pc_market_session.minute_aggregator.quality_for_range("EMPTY", minute, minute + timedelta(minutes=1)).value,
                    }
                    if cleanup is not None:
                        controller.request_close()
                        self.wait_until(lambda: controller._shutdown_stage in {"ready", "cache_failed"})
                        result["failed_close_stage"] = controller._shutdown_stage
                        result["cutoff_after_failed_close"] = controller.pending_minute_retention
                        injection["cleanup"] = False
                    elif controller.minute_cache_timer.isActive():
                        controller.minute_cache_timer.stop()
                        controller.minute_cache_timer.timeout.emit()
                    controller.request_close()
                    self.wait_until(controller.request_close)
                    result.update(final=state(), purges=purges.copy(), acks=acks.copy(),
                        final_pending_cutoff=controller.pending_minute_retention, close_stage=controller._shutdown_stage,
                        native_cost={"connections": len(connections), **{operation: sum(
                            sql.lstrip().upper().startswith(operation.upper()) for sql in statements)
                            for operation in ("select", "delete", "begin", "commit", "rollback")}})
                self.assertEqual(initial["keeper"], result["final"]["keeper"])
                print("R033_FALLBACK_RETENTION_OBSERVATION " + json.dumps({
                    key: value for key, value in result.items()
                    if key not in {"initial", "after_attempt", "final", "acks", "acks_after_attempt", "commits"}
                }, default=str))
                return result
            finally:
                injection["write"] = injection["cleanup"] = False
                controller.request_close()
                self.wait_until(controller.request_close)
                window.deleteLater()
                QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
                self.app.processEvents()

    def test_public_writer_free_applied_cutoff_fences_late_native_batches(self):
        for kind in ("batch", "gap"):
            for day in ("expired", "cutoff", "current"):
                with self.subTest(kind=kind, day=day):
                    result = self._observe_public_writer_free_applied_cutoff(kind, day=day)
                    self.assertEqual(([], [], [], []), result["after_attempt"]["expired"])
                    self.assertEqual(1, len(result["acks_after_attempt"]))
                    self.assertEqual(day == "expired", any(result["acks_after_attempt"][0]["expired"]))
                    self.assertEqual([date(2026, 9, 23)] if day == "expired" else [], result["purges"])
                    self.assertEqual("ready", result["close_stage"])

    def test_public_writer_free_expired_commit_failure_keeps_cleanup_retry_before_close(self):
        for kind in ("stock", "index", "gap"):
            for cleanup in ("precommit", "postcommit"):
                with self.subTest(kind=kind, cleanup=cleanup):
                    result = self._observe_public_writer_free_applied_cutoff(kind, postcommit=True, cleanup=cleanup)
                    self.assertEqual(1, len(result["commits"]))
                    self.assertFalse(result["commits"][0]["in_transaction"])
                    self.assertTrue(any(result["commits"][0]["state"]["expired"]))
                    self.assertEqual(int(kind == "gap"), len(result["acks_after_attempt"]))
                    self.assertTrue(result["pending_data"])
                    self.assertEqual(date(2026, 9, 23), result["pending_cutoff"])
                    self.assertTrue(result["timer_active"])
                    self.assertEqual("cache_failed", result["failed_close_stage"])
                    self.assertEqual(date(2026, 9, 23), result["cutoff_after_failed_close"])
                    self.assertEqual(([], [], [], []), result["final"]["expired"])
                    self.assertIsNone(result["final_pending_cutoff"])
                    self.assertEqual([date(2026, 9, 23)] * 3, result["purges"])

    def test_public_writer_free_history_and_empty_preserve_native_outcome_then_cutoff(self):
        for kind in ("history", "empty"):
            for postcommit, cleanup in ((False, None), (True, None), (False, "precommit")):
                with self.subTest(kind=kind, postcommit=postcommit, cleanup=cleanup):
                    result = self._observe_public_writer_free_applied_cutoff(kind, postcommit=postcommit, cleanup=cleanup)
                    self.assertEqual(int(postcommit), len(result["commits"]))
                    if postcommit:
                        self.assertFalse(result["commits"][0]["in_transaction"])
                        self.assertTrue(any(result["commits"][0]["state"]["expired"]))
                    if kind == "history":
                        self.assertEqual(not postcommit, result["history_loaded"])
                        self.assertEqual([("2026-10-23", "TARGET", 3)], result["after_attempt"]["receipt"])
                    else:
                        self.assertEqual("partial" if postcommit else "complete", result["empty_quality"])
                    if cleanup is not None:
                        self.assertEqual(date(2026, 9, 23), result["pending_cutoff"])
                        self.assertEqual("cache_failed", result["failed_close_stage"])
                    else:
                        self.assertEqual(([], [], [], []), result["after_attempt"]["expired"])
                    self.assertEqual(([], [], [], []), result["final"]["expired"])
                    self.assertEqual([], result["acks_after_attempt"])

    def _observe_older_requested_retention(self, *, writer=False, kind="batch", requested=None,
                                           minute=None, cleanup=None, write_failure=False):
        import json

        now = as_kst(datetime(2026, 10, 23, 10))
        applied = date(2026, 9, 23)
        requested = requested or date(2026, 9, 20)
        minute = minute or datetime(2026, 9, 22, 10)
        kept = datetime(2026, 9, 25, 10)
        original_connect = sqlite3.connect
        original_purge = MinuteBarRepository.purge_before
        purges, statements, connections, acks, commits = [], [], [], [], []
        injection = {"cleanup": cleanup is not None, "write": write_failure, "purge": None}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            repository = MinuteBarRepository(path, now_provider=lambda: now)
            repository.upsert_bars("KEEP", (MinuteOhlcv(kept, 200, 220, 190, 210, 30),))
            repository.upsert_market_index_minutes({("kosdaq", kept): (1., 2., 1., 2., 10.)})
            repository.record_history_sync("KEEP", kept.date(), kept, 1)
            controller = self.controller([], minute_bar_repository=repository,
                monitor_database_path=path if writer else None,
                ranking_loader=SimpleNamespace(server_now=lambda: now))

            def state():
                with closing(original_connect(path)) as connection:
                    return {
                        "expired": tuple(connection.execute(sql, (applied.isoformat(),)).fetchall() for sql in (
                            "SELECT * FROM minute_bars WHERE trade_date<? ORDER BY stock_code,minute",
                            "SELECT * FROM market_index_minute_bars WHERE trade_date<? ORDER BY market,minute",
                            "SELECT * FROM market_data_observation_meta WHERE dataset_kind='minute_bar' AND substr(observation_key,1,10)<? ORDER BY subject,observation_key",
                            "SELECT * FROM minute_history_sync_log WHERE trade_date<? ORDER BY stock_code",
                        )),
                        "keeper": tuple(connection.execute(sql).fetchall() for sql in (
                            "SELECT * FROM minute_bars WHERE stock_code='KEEP'",
                            "SELECT * FROM market_index_minute_bars WHERE market='kosdaq'",
                            "SELECT * FROM minute_history_sync_log WHERE stock_code='KEEP'",
                            "SELECT * FROM market_data_observation_meta WHERE subject IN ('KEEP','market:kosdaq') ORDER BY subject,observation_key",
                        )),
                    }

            cache_writer = controller.market_cache_writer
            if cache_writer is not None:
                controller.start_market_cache_writer()
            controller.queue_minute_retention(applied)
            if cache_writer is not None:
                self.wait_until(lambda: cache_writer._minute_retention_cutoff == applied)
                cache_writer.minute_saved.connect(lambda: acks.append(state()), Qt.ConnectionType.DirectConnection)
            else:
                self.assertEqual(applied, controller._minute_retention_cutoff)
                controller.minute_cache_saved.connect(lambda: acks.append(state()))
            initial = state()

            def purge(current, cutoff):
                purges.append(cutoff)
                injection["purge"] = cutoff
                try:
                    return original_purge(current, cutoff)
                finally:
                    injection["purge"] = None

            class NativeConnection(sqlite3.Connection):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.role = None
                    connections.append(1)
                    self.set_trace_callback(statements.append)

                def execute(self, sql, *args, **kwargs):
                    if sql.lstrip().upper().startswith("DELETE FROM MINUTE_BARS"):
                        self.role = "cleanup"
                        if injection["cleanup"] and cleanup == "requested-pre" and injection["purge"] == requested:
                            self.set_authorizer(lambda action, *_:
                                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DELETE else sqlite3.SQLITE_OK)
                        if injection["cleanup"] and cleanup == "applied-pre" and injection["purge"] == applied:
                            self.set_authorizer(lambda action, *_:
                                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DELETE else sqlite3.SQLITE_OK)
                    return super().execute(sql, *args, **kwargs)

                def executemany(self, sql, *args, **kwargs):
                    if sql.lstrip().upper().startswith("INSERT INTO MINUTE_BARS"):
                        self.role = "stock"
                    return super().executemany(sql, *args, **kwargs)

                def commit(self):
                    super().commit()
                    if self.role == "cleanup" and injection["cleanup"] and cleanup == "requested-post" and injection["purge"] == requested:
                        commits.append(("cleanup", self.in_transaction))
                        raise OSError("native requested retention COMMIT return lost")
                    if self.role == "stock" and injection["write"]:
                        injection["write"] = False
                        commits.append(("stock", self.in_transaction))
                        raise OSError("native stock COMMIT return lost")

            def connect(*args, **kwargs):
                kwargs["factory"] = NativeConnection
                return original_connect(*args, **kwargs)

            try:
                with patch.object(sqlite3, "connect", side_effect=connect), \
                        patch.object(MinuteBarRepository, "purge_before", purge):
                    if kind == "batch":
                        controller.queue_minute_bar("TARGET", MinuteOhlcv(minute, 100, 110, 90, 105, 10))
                        controller.queue_market_index("kospi", minute, 2500., 12.)
                    elif kind == "gap":
                        controller.pending_minute_gaps = {"GAP": (minute,), "market:kospi": (minute,)}
                    controller.queue_minute_retention(requested)
                    if cache_writer is not None:
                        self.assertTrue(cache_writer.stop_and_drain())
                        self.app.processEvents()
                    controller.minute_cache_timer.stop()
                    result = {"after": state(), "purges": purges.copy(), "acks": acks.copy(),
                        "pending": controller.pending_minute_retention,
                        "pending_stock": dict(controller.pending_minutes),
                        "pending_index": dict(controller.pending_market_minutes), "commits": commits.copy(),
                        "applied": cache_writer._minute_retention_cutoff if cache_writer else controller._minute_retention_cutoff,
                        "native_cost": {"connections": len(connections), **{operation: sum(
                            sql.lstrip().upper().startswith(operation.upper()) for sql in statements)
                            for operation in ("select", "delete", "insert", "begin", "commit", "rollback")}}}
                    injection["cleanup"] = False
                    if not writer:
                        controller.request_close()
                        self.wait_until(controller.request_close)
                        result.update(final=state(), final_pending=controller.pending_minute_retention,
                            close_stage=controller._shutdown_stage, final_purges=purges.copy())
                self.assertEqual(initial["keeper"], result["after"]["keeper"])
                print("R033_OLDER_REQUEST_OBSERVATION " + json.dumps({
                    "writer": writer, "kind": kind, "requested": requested, "minute": minute,
                    "cleanup": cleanup, "write_failure": write_failure,
                    **{key: value for key, value in result.items() if key not in {"after", "final", "acks", "pending_stock", "pending_index"}},
                    "expired_counts": [len(rows) for rows in result["after"]["expired"]],
                    "ack_count": len(result["acks"]),
                }, default=str))
                return result
            finally:
                injection["cleanup"] = injection["write"] = False
                if cache_writer is not None:
                    self.assertTrue(cache_writer.stop_and_drain())
                controller.request_close()
                self.wait_until(controller.request_close)

    def test_older_requested_cutoff_matches_native_fifo_late_input_fence(self):
        for kind in ("batch", "gap"):
            for writer in (True, False):
                with self.subTest(kind=kind, writer=writer):
                    result = self._observe_older_requested_retention(kind=kind, writer=writer)
                    self.assertEqual([date(2026, 9, 23), date(2026, 9, 20)], result["purges"])
                    self.assertEqual(([], [], [], []), result["after"]["expired"])
                    self.assertEqual(date(2026, 9, 23), result["applied"])
                    self.assertIsNone(result["pending"])
                    self.assertEqual(int(kind == "batch" or not writer), len(result["acks"]))
                    if result["acks"]:
                        self.assertTrue(any(result["acks"][0]["expired"]))

    def test_explicit_cutoff_controls_keep_requested_native_purge_without_extra_fence(self):
        for requested, minute, kind in (
            (date(2026, 9, 23), datetime(2026, 9, 22, 10), "batch"),
            (date(2026, 9, 24), datetime(2026, 9, 22, 10), "batch"),
            (date(2026, 9, 20), datetime(2026, 9, 23, 10), "batch"),
            (date(2026, 9, 20), datetime(2026, 10, 23, 10), "gap"),
            (date(2026, 9, 20), datetime(2026, 9, 22, 10), "none"),
        ):
            with self.subTest(requested=requested, minute=minute, kind=kind):
                result = self._observe_older_requested_retention(requested=requested, minute=minute, kind=kind)
                self.assertEqual([requested], result["purges"])
                self.assertEqual(([], [], [], []), result["after"]["expired"])
                self.assertEqual(max(date(2026, 9, 23), requested), result["applied"])
                self.assertIsNone(result["pending"])

    def test_older_requested_cutoff_preserves_native_failure_payload_and_explicit_retry(self):
        for cleanup, write_failure in (("requested-pre", False), ("requested-post", False),
                                       ("applied-pre", False), (None, True)):
            with self.subTest(cleanup=cleanup, write_failure=write_failure):
                result = self._observe_older_requested_retention(cleanup=cleanup, write_failure=write_failure)
                self.assertEqual([date(2026, 9, 23), date(2026, 9, 20)], result["purges"])
                expected = date(2026, 9, 23) if cleanup == "applied-pre" else date(2026, 9, 20) if cleanup else None
                self.assertEqual(expected, result["pending"])
                self.assertEqual(int(not write_failure), len(result["acks"]))
                if cleanup != "applied-pre":
                    self.assertEqual(([], [], [], []), result["after"]["expired"])
                else:
                    self.assertTrue(any(result["after"]["expired"]))
                self.assertEqual(int(write_failure), len(result["pending_stock"]))
                self.assertEqual(int(write_failure), len(result["pending_index"]))
                if write_failure:
                    self.assertEqual([("stock", False)], result["commits"])
                    minute = datetime(2026, 9, 22, 10)
                    self.assertEqual({("TARGET", minute): MinuteOhlcv(minute, 100, 110, 90, 105, 10)}, result["pending_stock"])
                    self.assertEqual({("kospi", minute): (2500., 2500., 2500., 2500., 12.)}, result["pending_index"])
                if cleanup == "requested-post":
                    self.assertEqual([("cleanup", False)], result["commits"])
                self.assertEqual([date(2026, 9, 23), date(2026, 9, 20),
                    expected or date(2026, 9, 23)], result["final_purges"])
                self.assertEqual(([], [], [], []), result["final"]["expired"])
                self.assertIsNone(result["final_pending"])
                self.assertEqual("ready", result["close_stage"])
                self.assertEqual(date(2026, 9, 23), result["applied"])

    def test_retention_failure_keeps_original_cutoff_for_timer_and_explicit_close_retry(self):
        for after_commit in (False, True):
            with self.subTest(after_commit=after_commit), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "monitor.sqlite3"
                Database(path).initialize()
                repository = MinuteBarRepository(path)
                old = datetime(2026, 9, 19, 10)
                repository.upsert_many({"005930": (MinuteOhlcv(old, 100, 100, 100, 100, 1),)})
                events = []
                controller = self.controller(events, monitor_database_path=path,
                    minute_bar_repository=repository)
                controller.start_market_cache_writer()
                writer = controller.market_cache_writer
                cutoff = date(2026, 9, 20)
                calls, timer_states = [], []
                original = MinuteBarRepository.purge_before

                def purge(current, requested):
                    calls.append(requested)
                    if len(calls) <= 3:
                        if after_commit:
                            original(current, requested)
                        raise OSError("controlled retention failure")
                    original(current, requested)

                def observed_failure(*_):
                    timer_states.append(controller.minute_cache_timer.isActive())
                    # Drive the existing timeout signal explicitly, without a sleep race.
                    controller.minute_cache_timer.stop()

                writer.retention_failed.connect(observed_failure, Qt.ConnectionType.QueuedConnection)
                with patch.object(MinuteBarRepository, "purge_before", purge):
                    controller.queue_minute_retention(cutoff)
                    self.wait_until(lambda: len(timer_states) == 1)
                    self.assertEqual(cutoff, controller.pending_minute_retention)
                    self.assertEqual([True], timer_states)
                    controller.minute_cache_timer.timeout.emit()
                    self.wait_until(lambda: len(timer_states) == 2)
                    self.assertEqual(cutoff, controller.pending_minute_retention)
                    self.assertFalse(controller.request_close())
                    self.wait_until(lambda: controller._shutdown_stage == "cache_failed")
                    self.assertEqual(cutoff, controller.pending_minute_retention)
                    self.assertEqual([True, True, False], timer_states)
                    self.assertNotIn("ready", events)
                    self.assertNotIn("auxiliaries", events)
                    self.assertFalse(controller.minute_cache_timer.isActive())
                    with closing(sqlite3.connect(path)) as connection:
                        self.assertEqual(int(not after_commit), connection.execute(
                            "SELECT COUNT(*) FROM minute_bars").fetchone()[0])
                    self.assertFalse(controller.request_close())
                    self.wait_until(controller.request_close)
                self.assertIs(writer, controller.market_cache_writer)
                self.assertEqual([cutoff] * 4, calls)
                self.assertIsNone(controller.pending_minute_retention)
                self.assertEqual(1, events.count("ready"))
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM minute_bars").fetchone()[0])

        # Explicit embedded/offline mode retains its synchronous repository path.
        repository = Mock(spec=["purge_before"])
        repository.purge_before.side_effect = OSError("offline retention failure")
        events = []
        controller = self.controller(events, minute_bar_repository=repository)
        saved = Mock()
        controller.minute_cache_saved.connect(saved)
        controller.queue_minute_retention(cutoff)
        self.assertEqual(cutoff, controller.pending_minute_retention)
        self.assertFalse(controller.request_close())
        self.assertEqual("cache_failed", controller._shutdown_stage)
        self.assertNotIn("ready", events)
        repository.purge_before.side_effect = None
        self.assertFalse(controller.request_close())
        self.wait_until(controller.request_close)
        self.assertIsNone(controller.pending_minute_retention)
        saved.assert_not_called()  # Retention itself is not a minute-data ACK.

    def test_expired_batch_retry_keeps_committed_ack_and_retention_failure_until_explicit_close_retry(self):
        for after_commit in (False, True):
            with self.subTest(after_commit=after_commit), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "monitor.sqlite3"
                Database(path).initialize()
                events, restored, failures, acks, calls = [], [], [], [], []
                controller = self.controller(events, monitor_database_path=path,
                    minute_bar_repository=MinuteBarRepository(path),
                    ranking_loader=SimpleNamespace(server_now=lambda: as_kst(datetime(2026, 10, 21, 10))))
                controller.start_market_cache_writer()
                writer = controller.market_cache_writer
                old = datetime(2026, 9, 20, 10)
                cutoff = date(2026, 9, 21)
                pending = {("EXPIRED", old): MinuteOhlcv(old, 100, 110, 90, 105, 10)}
                market = {("kospi", old): (1., 2., 1., 2., 10.)}
                real_purge = MinuteBarRepository.purge_before

                def purge(repository, requested):
                    calls.append(requested)
                    real_purge(repository, requested)
                    if after_commit and len(calls) in (2, 3):
                        raise OSError("controlled cleanup ACK loss after COMMIT")

                def failed_batch(*_):
                    controller.minute_cache_timer.stop()
                    restored.append((dict(controller.pending_minutes), dict(controller.pending_market_minutes)))

                def failed_retention(*_):
                    controller.minute_cache_timer.stop()
                    failures.append(controller.pending_minute_retention)

                def saved():
                    with closing(sqlite3.connect(path)) as connection:
                        acks.append((connection.execute("SELECT COUNT(*) FROM minute_bars WHERE stock_code='EXPIRED'").fetchone()[0],
                                     connection.execute("SELECT COUNT(*) FROM market_index_minute_bars").fetchone()[0]))

                writer.minute_failed.connect(failed_batch, Qt.ConnectionType.QueuedConnection)
                writer.retention_failed.connect(failed_retention, Qt.ConnectionType.QueuedConnection)
                writer.minute_saved.connect(saved, Qt.ConnectionType.DirectConnection)
                with closing(sqlite3.connect(path)) as connection:
                    connection.execute("CREATE TRIGGER reject_expired_insert BEFORE INSERT ON minute_bars WHEN NEW.stock_code='EXPIRED' BEGIN SELECT RAISE(ABORT,'controlled expired write failure'); END")
                    connection.commit()
                controller.pending_minutes.update(pending)
                controller.pending_market_minutes.update(market)
                with patch.object(MinuteBarRepository, "purge_before", purge):
                    controller.queue_minute_retention(cutoff)
                    self.wait_until(lambda: restored and writer._minute_retention_cutoff == cutoff)
                    self.assertEqual([(pending, market)], restored)
                    self.assertEqual([], acks)
                    with closing(sqlite3.connect(path)) as connection:
                        connection.execute("DROP TRIGGER reject_expired_insert")
                        if not after_commit:
                            connection.execute("CREATE TRIGGER reject_expired_delete BEFORE DELETE ON minute_bars WHEN OLD.stock_code='EXPIRED' BEGIN SELECT RAISE(ABORT,'controlled cleanup precommit failure'); END")
                        connection.commit()
                    controller.minute_cache_timer.timeout.emit()
                    self.wait_until(lambda: len(failures) == 1)
                    # Direct ACK readback precedes maintenance; its native data
                    # save stays successful even when the later purge fails.
                    self.assertEqual([(1, 1)], acks)
                    self.assertEqual({}, controller.pending_minutes)
                    self.assertEqual({}, controller.pending_market_minutes)
                    self.assertEqual(cutoff, controller.pending_minute_retention)
                    with closing(sqlite3.connect(path)) as connection:
                        self.assertEqual(int(not after_commit), connection.execute(
                            "SELECT COUNT(*) FROM minute_bars WHERE stock_code='EXPIRED'").fetchone()[0])
                    self.assertFalse(controller.request_close())
                    self.wait_until(lambda: controller._shutdown_stage == "cache_failed")
                    self.assertEqual([cutoff, cutoff], failures)
                    self.assertEqual(cutoff, controller.pending_minute_retention)
                    self.assertNotIn("ready", events)
                    self.assertNotIn("auxiliaries", events)
                    self.assertFalse(writer.isRunning())
                    self.assertEqual(cutoff, writer._minute_retention_cutoff)
                    if not after_commit:
                        with closing(sqlite3.connect(path)) as connection:
                            connection.execute("DROP TRIGGER reject_expired_delete")
                            connection.commit()
                    self.assertFalse(controller.request_close())
                    self.wait_until(controller.request_close)
                self.assertIs(writer, controller.market_cache_writer)
                self.assertEqual(cutoff, writer._minute_retention_cutoff)
                self.assertEqual([cutoff] * 4, calls)
                self.assertEqual([(1, 1)], acks)
                self.assertEqual(1, events.count("ready"))
                self.assertIsNone(controller.pending_minute_retention)
                with closing(sqlite3.connect(path)) as connection:
                    for table in ("minute_bars", "market_index_minute_bars", "market_data_observation_meta"):
                        self.assertEqual(0, connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], table)

    def test_gap_metadata_failure_blocks_close_until_existing_writer_retry_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            Database(path).initialize()
            events = []
            controller = self.controller(events, monitor_database_path=path,
                minute_bar_repository=MinuteBarRepository(path))
            controller.start_market_cache_writer()
            minute = datetime(2026, 10, 11, 10, 0)
            # Hold the final gap batch in the owner's existing pending buffer.
            controller.pending_minute_gaps = {"005930": (minute,)}
            with patch.object(MinuteBarRepository, "mark_gap_minutes", side_effect=OSError("gap save")):
                self.assertFalse(controller.request_close())
                self.wait_until(lambda: controller._shutdown_stage == "cache_failed")
                self.assertNotIn("ready", events)
                self.assertNotIn("auxiliaries", events)
                self.assertEqual({"005930": (minute,)}, controller.pending_minute_gaps)
            self.assertFalse(controller.request_close())
            self.wait_until(controller.request_close)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(("missing",), connection.execute(
                    "SELECT completeness FROM market_data_observation_meta WHERE subject='005930'").fetchone())
            self.assertEqual(1, events.count("ready"))
            self.assertEqual({}, controller.pending_minute_gaps)

    def test_final_minute_failure_blocks_close_and_retries_without_duplicate_commit(self):
        for after_commit in (False, True):
            with self.subTest(after_commit=after_commit), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "monitor.sqlite3"
                database = Database(path)
                database.initialize()
                with closing(sqlite3.connect(path)) as connection:
                    connection.execute("INSERT INTO stocks(code,name) VALUES('005930','probe')")
                    connection.commit()
                events = []
                controller = self.controller(events, monitor_database_path=path,
                    minute_bar_repository=MinuteBarRepository(path))
                controller.start_market_cache_writer()
                writer = controller.market_cache_writer
                now = datetime(2026, 10, 6, 10, 0)
                bar = MinuteOhlcv(now, 100, 100, 100, 100, 1)
                controller.queue_minute_bar("005930", bar)
                original = MinuteBarRepository.upsert_many
                calls = []
                def save(repository, values):
                    calls.append(values)
                    if len(calls) == 1:
                        if after_commit:
                            original(repository, values)
                        raise OSError("injected final save failure")
                    return original(repository, values)
                with patch.object(MinuteBarRepository, "upsert_many", save):
                    self.assertFalse(controller.request_close())
                    self.wait_until(lambda: controller._shutdown_stage == "cache_failed")
                    self.assertNotIn("ready", events)
                    self.assertNotIn("auxiliaries", events)
                    self.assertFalse(writer.isRunning())
                    self.assertEqual({("005930", now): bar}, controller.pending_minutes)
                    self.assertFalse(controller.minute_cache_timer.isActive())
                    with closing(sqlite3.connect(path)) as connection:
                        count = connection.execute("SELECT COUNT(*) FROM minute_bars WHERE stock_code='005930'").fetchone()[0]
                    self.assertEqual(int(after_commit), count)
                    self.assertFalse(controller.request_close())
                    self.wait_until(controller.request_close)
                self.assertIs(writer, controller.market_cache_writer)
                self.assertEqual(2, len(calls))
                self.assertEqual({}, controller.pending_minutes)
                with closing(sqlite3.connect(path)) as connection:
                    rows = connection.execute("SELECT close_price,volume FROM minute_bars WHERE stock_code='005930'").fetchall()
                self.assertEqual([(100, 1)], rows)
                self.assertEqual(1, events.count("ready"))
                self.assertEqual(1, events.count("top20"))
                self.assertEqual(1, events.count("backup"))
                self.assertEqual(1, events.count("view"))

    def test_window_keeps_failed_final_price_and_allows_explicit_retry_after_recovery(self):
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("INSERT INTO stocks(code,name) VALUES('005930','probe')")
                connection.commit()
            window = MainWindow(database.settings, monitor_database_path=path,
                minute_bar_repository=MinuteBarRepository(path))
            window.activate_startup()
            self.windows.append(window)
            controller = window._app_controller
            writer = controller.market_cache_writer
            controller.queue_price_cache("005930", price=71200)
            with patch.object(StockRepository, "update_price_cache", side_effect=OSError("injected price save failure")) as save:
                for _ in range(2):
                    event = QCloseEvent()
                    window.closeEvent(event)
                    self.assertFalse(event.isAccepted())
                    self.wait_until(lambda: controller._shutdown_stage == "cache_failed")
                    self.assertEqual({"005930": 71200}, self.price_retry_values(controller, 0))
                    self.assertEqual(1, len(controller._price_cache_retries))
                    self.assertFalse(writer.isRunning())
                    self.assertFalse(controller.price_cache_timer.isActive())
                self.assertEqual(2, save.call_count)
            event = QCloseEvent()
            window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.wait_until(controller.request_close)
            self.assertIs(writer, controller.market_cache_writer)
            self.assertEqual({}, controller.pending_prices)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(71200, connection.execute("SELECT last_price FROM stocks WHERE code='005930'").fetchone()[0])
            event = QCloseEvent()
            window.closeEvent(event)
            self.assertTrue(event.isAccepted())

    def test_shutdown_late_old_failure_retries_only_unsaved_peer_and_keeps_newer_commit(self):
        from threading import Timer
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        for kind in ("minute", "price"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "monitor.sqlite3"
                database = Database(path)
                database.initialize()
                with closing(sqlite3.connect(path)) as connection:
                    connection.executemany("INSERT INTO stocks(code,name) VALUES(?,?)", [("005930","first"),("000660","peer")])
                    connection.commit()
                controller = self.controller([], monitor_database_path=path,
                    minute_bar_repository=MinuteBarRepository(path))
                controller.start_market_cache_writer()
                now = datetime(2026, 10, 6, 10, 0)
                old = MinuteOhlcv(now, 100, 100, 100, 100, 1)
                latest = MinuteOhlcv(now, 100, 103, 100, 103, 3)
                entered, release = Event(), Event()
                calls = []
                repository_type = MinuteBarRepository if kind == "minute" else StockRepository
                method = "upsert_many" if kind == "minute" else "update_price_cache"
                original = getattr(repository_type, method)
                def save(repository, values, *args, **kwargs):
                    calls.append(dict(values))
                    if len(calls) == 1:
                        entered.set()
                        if not release.wait(3):
                            raise TimeoutError("probe did not release old batch")
                        raise OSError("injected old batch failure")
                    return original(repository, values, *args, **kwargs)
                with patch.object(repository_type, method, save):
                    if kind == "minute":
                        controller.queue_minute_bar("005930", old)
                        controller.queue_minute_bar("000660", old)
                        controller.flush_minute_cache()
                    else:
                        controller.queue_price_cache("005930", price=100)
                        controller.queue_price_cache("000660", price=90)
                        controller.flush_price_cache()
                    self.assertTrue(entered.wait(1))
                    if kind == "minute":
                        controller.queue_minute_bar("005930", latest)
                    else:
                        controller.queue_price_cache("005930", price=103)
                    releaser = Timer(.05, release.set)
                    releaser.start()
                    try:
                        self.assertFalse(controller.request_close())
                    finally:
                        release.set()
                        releaser.join(1)
                        self.assertFalse(releaser.is_alive())
                    self.wait_until(lambda: controller._shutdown_stage == "cache_failed")
                    expected = {("000660", now): old} if kind == "minute" else {"000660": 90}
                    pending = controller.pending_minutes if kind == "minute" else self.price_retry_values(controller, 0)
                    self.assertEqual(expected, pending)
                    query = ("SELECT close_price,volume FROM minute_bars WHERE stock_code='005930'"
                        if kind == "minute" else "SELECT last_price FROM stocks WHERE code='005930'")
                    with closing(sqlite3.connect(path)) as connection:
                        self.assertEqual((103, 3) if kind == "minute" else (103,), connection.execute(query).fetchone())
                    self.assertFalse(controller.request_close())
                    self.wait_until(controller.request_close)
                    self.assertEqual({"000660"}, set(calls[-1]))
                    self.assertEqual(3, len(calls))
                    with closing(sqlite3.connect(path)) as connection:
                        self.assertEqual((103, 3) if kind == "minute" else (103,), connection.execute(query).fetchone())

    def test_close_waits_for_late_trade_to_reach_app_buffers_and_real_database(self):
        now = datetime(2026, 10, 6, 10, 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("INSERT INTO stocks(code,name) VALUES('005930','삼성전자')")
                connection.commit()
            window = MainWindow(database.settings, monitor_database_path=path,
                                minute_bar_repository=MinuteBarRepository(path),
                                ranking_loader=SimpleNamespace(server_now=lambda: now))
            window.activate_startup()
            self.windows.append(window)
            controller = window._app_controller
            window._row_by_code["005930"] = 0
            window._pc_market_session.ensure_minute_day(now, flush=lambda: None, retain=None)
            producer = DelayedTradeProducer(controller.realtime)
            self.threads.append(producer)
            producer.trade.connect(controller.realtime.trade_received.emit)
            producer.start()
            self.assertTrue(producer.started_run.wait(1))
            close = QCloseEvent()
            window.closeEvent(close)
            self.assertFalse(close.isAccepted())
            self.assertTrue(window._market_cache_writer.isRunning())
            producer.release.set()
            self.wait_until(controller.request_close)
            self.assertEqual(71200, window._current_prices["005930"])
            self.assertFalse(window._market_cache_writer.isRunning())
            self.assertFalse(controller.pending_prices or controller.pending_minutes)
            self.assertFalse(controller.price_cache_timer.isActive())
            self.assertFalse(controller.minute_cache_timer.isActive())
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(71200, connection.execute(
                    "SELECT last_price FROM stocks WHERE code='005930'",
                ).fetchone()[0])
                self.assertEqual((71200, 2), connection.execute(
                    "SELECT close_price,volume FROM minute_bars WHERE stock_code='005930'",
                ).fetchone())
            producer.wait(1000)
            self.threads.remove(producer)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
            self.windows.remove(window)

    def test_main_window_shutdown_blocks_delayed_new_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)
            window.activate_startup()
            self.windows.append(window)
            self.assertTrue(window.close())
            with patch("kiwoom_monitor.presentation.main_window.Top20NasDataWorker") as nas, \
                    patch.object(window._update_worker_controller, "start_check") as updates, \
                    patch.object(window._image_theme_ocr_worker_controller, "start") as ocr:
                window._app_controller.market_data_client = object()
                window._start_top20_nas_request(("daily", ()), lambda result: None)
                window._check_for_updates(silent=True)
                window._start_image_theme_ocr(())
                nas.assert_not_called()
                updates.assert_not_called()
                ocr.assert_not_called()

    def test_main_window_late_history_signal_reaches_database_and_saved_handler_before_close(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            window = MainWindow(database.settings, monitor_database_path=path,
                                minute_bar_repository=MinuteBarRepository(path))
            window.activate_startup()
            self.windows.append(window)
            producer = DelayedHistoryProducer(window._app_controller.minute_history)
            self.threads.append(producer)
            producer.history.connect(window._app_controller.minute_history.history_received.emit)
            producer.start()
            self.assertTrue(producer.started_run.wait(1))
            close = QCloseEvent()
            window.closeEvent(close)
            self.assertFalse(close.isAccepted())
            self.assertTrue(window._market_cache_writer.isRunning())
            self.assertFalse(window._market_cache_writer.isInterruptionRequested())
            producer.release.set()
            self.wait_until(lambda: window._app_controller.request_close())
            self.assertIn("005930", window._pc_market_session.history_codes)
            self.assertFalse(window._app_controller.price_cache_timer.isActive())
            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute("SELECT close_price,volume FROM minute_bars WHERE stock_code='005930'").fetchone()
                self.assertEqual((71100, 300), row)
            producer.wait(1000)
            self.threads.remove(producer)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
            self.windows.remove(window)

    def test_theme_save_worker_is_owned_and_shutdown_waits_for_it(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)
            window.activate_startup()
            self.windows.append(window)
            started = Event()
            release = Event()

            completed = Event()

            def save_theme():
                started.set()
                release.wait(3)
                completed.set()

            worker = SettingsRequestWorker(save_theme, window._app_controller)
            worker.start()
            self.assertTrue(started.wait(1))
            self.assertIn((worker, "백그라운드 작업"), window._app_controller._producer_workers())
            close = QCloseEvent()
            window.closeEvent(close)
            self.assertFalse(close.isAccepted())
            release.set()
            worker.wait(1000)
            self.wait_until(lambda: window._app_controller.request_close())
            self.assertTrue(completed.is_set())

    def _bootstrap_settings_desktop(self, during_loop, after_exit=None, *,
                                    content_service=None, expected_error=SystemExit,
                                    source_mode="local_server", before_run=None, cleanup_window=False,
                                    query_client_factory=None):
        import json
        import sys
        import threading
        from contextlib import ExitStack
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.app_paths import AppPaths
        from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
        from kiwoom_monitor.infrastructure.central_settings_sync import CentralSettingsSyncService
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        from kiwoom_monitor.infrastructure.persistence.stock_news_repository import StockNewsRepository
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease, StrictRestoreError

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        paths = AppPaths(root, root / "logs", root / "monitor.sqlite3")
        JournalRepository(paths.journal_database_path)
        StockNewsRepository(paths.news_database_path)
        fixture = SimpleNamespace(
            paths=paths, entered=Event(), release=Event(), completed=Event(), events=[],
            callbacks=[], timers=[], windows=[], http_calls=0, fail_http=False, network_calls=0,
        )
        now = datetime.fromisoformat("2030-01-02T10:00:00+09:00")

        class Response:
            headers = {"X-Kiwoom-Server-Time": now.isoformat()}

            def __init__(self, value):
                self.value = value

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(self.value).encode()

        def http(request, **_kwargs):
            fixture.network_calls += 1
            if "/app_settings?" in request.full_url:
                fixture.http_calls += 1
                fixture.events.append("settings.http")
                fixture.entered.set()
                if not fixture.release.wait(4):
                    raise RuntimeError("settings HTTP fixture was not released")
                if fixture.fail_http:
                    raise OSError("settings HTTP fixture failure")
                return Response({"documents": [{
                    "owner": "default", "key": "rank_query_type", "document": {
                        "key": "rank_query_type", "value": "4", "updated_at": now.isoformat(),
                    },
                }]})
            if request.get_method() == "GET":
                return Response({"documents": []})
            return Response({"saved": len(json.loads(request.data)["documents"])})

        class TrackedSync(CentralSettingsSyncService):
            def sync(self, path):
                try:
                    return super().sync(path)
                finally:
                    fixture.events.append("settings.finished")
                    fixture.completed.set()

        def timer(*args, **kwargs):
            value = QTimer(*args, **kwargs)
            fixture.timers.append(value)
            return value

        def window(*args, **kwargs):
            value = MainWindow(*args, **kwargs)
            fixture.windows.append(value)
            self.windows.append(value)
            return value

        def restore_available():
            try:
                lease = AppDataLease(root, exclusive=True, timeout=0)
            except StrictRestoreError:
                return False
            lease.close()
            return True

        fixture.restore_available = restore_available

        def loop():
            fixture.window = fixture.windows[0]
            fixture.schedule = next((
                callback for _delay, callback in fixture.callbacks
                if getattr(callback, "__name__", "") == "schedule_central_settings_sync"
            ), None)
            fixture.timer = fixture.timers[-1] if fixture.timers else None
            during_loop(fixture)
            return 0

        application = Mock()
        application.exec.side_effect = loop
        server = Mock()
        server.process = None
        server.stop.side_effect = lambda: fixture.events.append("server.stop")
        fixture.server = server
        service = Mock() if content_service is None else content_service
        if content_service is None:
            service.pull.return_value = SimpleNamespace(total=0)
            service.push.return_value = SimpleNamespace(total=0)
        source = DataSourceSettings(mode=source_mode, server_url="https://fixture.invalid", access_token="fixture")
        single_shot = lambda delay, callback: fixture.callbacks.append((delay, callback))
        with ExitStack() as stack:
            for active in (
                patch.dict(os.environ), patch.object(sys, "excepthook"),
                patch.object(bootstrap, "_set_taskbar_app_id"),
                patch.object(bootstrap, "configure_logging"),
                patch.object(bootstrap.AppPaths, "for_current_user", return_value=paths),
                patch.object(bootstrap.DataSourceConfig, "load", return_value=source),
                patch.object(bootstrap, "LocalCentralServerProcess", return_value=server),
                patch.object(bootstrap, "CentralContentSyncService", return_value=service),
                patch.object(bootstrap, "CentralContentClient", side_effect=lambda *args, **kwargs:
                             CentralContentClient(*args, opener=http, **kwargs)),
                patch.object(bootstrap, "CentralSettingsSyncService", TrackedSync),
                (patch.object(bootstrap, "create_query_client", side_effect=query_client_factory)
                 if query_client_factory is not None else
                 patch.object(bootstrap, "create_query_client", return_value=SimpleNamespace(server_now=lambda: now))),
                patch.object(bootstrap, "QApplication", return_value=application),
                patch.object(bootstrap, "MainWindow", side_effect=window),
                patch.object(QTimer, "singleShot", side_effect=single_shot),
                patch.object(bootstrap, "QTimer", side_effect=timer),
            ):
                stack.enter_context(active)
            bootstrap.QTimer.singleShot.side_effect = single_shot
            try:
                if before_run is not None:
                    before_run(fixture)
                with self.assertRaises(expected_error) as exited:
                    bootstrap._main_with_restore_gate()
                fixture.exit_error = exited.exception
                if expected_error is SystemExit:
                    self.assertEqual(0, exited.exception.code)
                fixture.lease_released_while_running = (
                    not fixture.completed.is_set() and restore_available()
                )
                fixture.before_release = list(fixture.events)
                if after_exit is not None:
                    after_exit(fixture)
            finally:
                fixture.release.set()
                for worker in threading.enumerate():
                    if worker.name in {"central-content-sync", "central-app-settings-sync"}:
                        worker.join(5)
                        self.assertFalse(worker.is_alive())
                for value in fixture.windows:
                    if content_service is not None or cleanup_window:
                        value.close()
                    self.wait_until(lambda value=value: not any(
                        worker.isRunning() for worker in value._app_controller.findChildren(QThread)
                    ))
                self.app.processEvents()
        return fixture

    def _bootstrap_content_case(self, *, stage="sqlite", exit_mode="quit", fail_http=False,
                                source_mode="local_server", setup_read_failure=False):
        import json
        import threading
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
        from kiwoom_monitor.infrastructure.central_content_sync import CentralContentSyncService
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease

        state = SimpleNamespace(
            entered=Event(), release=Event(), completed=Event(), lease_released=Event(),
            stopped=Event(), errors=[], events=[], threads=[], observer=None, calls=0,
            connections=0, sql=[], timed_out=False, observed=None,
            local=threading.local(), setup_error=None, denied_reads=0,
        )
        now = "2030-01-02T10:00:00+09:00"

        def barrier():
            state.events.append(stage + ".entered")
            state.entered.set()
            if not state.release.wait(4):
                state.timed_out = True

        class Response:
            headers = {"X-Kiwoom-Server-Time": now}

            def __init__(self, value):
                self.value = value

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(self.value).encode()

        def http(request, **_kwargs):
            state.calls += 1
            if request.full_url.endswith("/api/v1/themes/snapshot"):
                from urllib.error import HTTPError
                from io import BytesIO
                raise HTTPError(request.full_url, 404, "legacy content fixture", {}, BytesIO(b'{}'))
            if "/news_article?" in request.full_url:
                if stage == "http":
                    barrier()
                if fail_http:
                    raise OSError("controlled content HTTP failure")
                return Response({"documents": [{
                    "owner": "005930", "key": "content-fixture", "updated_at": 1.0,
                    "document": {"stock_code": "005930", "identity": "content-fixture",
                                 "title": "content lifetime fixture", "first_seen_at": now},
                }]})
            if request.get_method() == "GET":
                return Response({"documents": []})
            return Response({"saved": len(json.loads(request.data)["documents"])})

        service = CentralContentSyncService(CentralContentClient(
            "https://fixture.invalid", "fixture", opener=http,
        ))
        connect = sqlite3.connect
        thread_type = threading.Thread
        thread_start = thread_type.start
        lease_close = AppDataLease.close
        all_by_name = bootstrap.DatabaseThemeRepository.all_by_name

        def authorize(action, table, *_args):
            if action == sqlite3.SQLITE_READ and table == "stocks":
                state.denied_reads += 1
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        def trace(statement):
            state.sql.append(statement)
            if stage == "sqlite" and statement.lstrip().upper().startswith("BEGIN") and not state.entered.is_set():
                barrier()

        def connection(*args, **kwargs):
            value = connect(*args, **kwargs)
            if threading.current_thread().name == "central-content-sync":
                state.connections += 1
                value.set_trace_callback(trace)
            if getattr(state.local, "setup_read", False):
                value.set_authorizer(authorize)
            return value

        def setup_read(owner):
            if not setup_read_failure:
                return all_by_name(owner)
            self.assertTrue(state.entered.wait(2))
            state.local.setup_read = True
            try:
                return all_by_name(owner)
            except sqlite3.DatabaseError as error:
                state.setup_error = error
                during_loop(state.fixture)
                raise
            finally:
                state.local.setup_read = False

        def start_thread(worker):
            if worker.name == "central-content-sync":
                target = worker._target

                def tracked_target():
                    state.events.append("content.begin")
                    try:
                        target()
                    finally:
                        state.events.append("content.done")
                        state.completed.set()

                worker._target = tracked_target
                state.threads.append(worker)
            thread_start(worker)

        def close_lease(lease):
            shared = not lease._exclusive and not lease._stream.closed
            lease_close(lease)
            if shared:
                state.events.append("lease.released")
                state.lease_released.set()

        def during_loop(fixture):
            state.fixture = fixture
            stop = fixture.server.stop.side_effect

            def stop_server():
                stop()
                state.events.append("server.stop")
                state.stopped.set()

            fixture.server.stop.side_effect = stop_server
            if source_mode == "local":
                return
            self.assertTrue((state.completed if stage is None else state.entered).wait(2))
            if exit_mode == "normal":
                fixture.window.close()
                self.wait_until(lambda: fixture.window._app_controller._shutdown_stage == "ready")
            state.events.append("setup.failed" if setup_read_failure else "app.exec.leaving")

            if stage is not None:
                def observe():
                    try:
                        state.lease_released.wait(0.15)
                        state.observed = (state.stopped.is_set(), state.lease_released.is_set(),
                                          fixture.restore_available())
                        self.assertEqual((False, False, False), state.observed, state.events)
                        self.assertFalse(state.completed.is_set())
                    except BaseException as error:
                        state.errors.append(error)
                    finally:
                        state.release.set()

                state.observer = thread_type(target=observe, name="content-close-observer")
                state.observer.start()
            if exit_mode == "error":
                raise RuntimeError("controlled app.exec failure")

        def after_exit(fixture):
            if state.observer is not None:
                state.observer.join(5)
                self.assertFalse(state.observer.is_alive())
            if state.errors:
                raise state.errors[0]
            self.assertTrue(fixture.restore_available())
            if setup_read_failure:
                self.assertIs(state.setup_error, fixture.exit_error)
                self.assertEqual(sqlite3.SQLITE_AUTH, state.setup_error.sqlite_errorcode)
                self.assertEqual(1, state.denied_reads)
                self.assertEqual([], fixture.windows)
                self.assertNotIn("app.exec.leaving", state.events)
            if source_mode == "local":
                self.assertEqual([], state.threads)
                self.assertEqual(0, state.calls)
                fixture.server.stop.assert_not_called()
                return
            self.assertTrue(state.completed.is_set())
            self.assertEqual(1, len(state.threads))
            self.assertTrue(state.threads[0].daemon)
            self.assertFalse(state.threads[0].is_alive())
            self.assertLess(state.events.index("content.done"), state.events.index("server.stop"))
            self.assertLess(state.events.index("server.stop"), state.events.index("lease.released"))
            if exit_mode == "error":
                self.assertEqual("controlled app.exec failure", str(fixture.exit_error))
            self.assertEqual(not fail_http, (fixture.paths.data_dir / ".central_content_seeded").exists())
            with closing(sqlite3.connect(fixture.paths.news_database_path)) as value:
                rows = value.execute("SELECT title FROM stock_news WHERE identity='content-fixture'").fetchall()
            self.assertEqual([] if fail_http else [("content lifetime fixture",)], rows)

        with patch.object(thread_type, "start", start_thread), \
                patch.object(sqlite3, "connect", side_effect=connection), \
                patch.object(bootstrap.DatabaseThemeRepository, "all_by_name", setup_read), \
                patch.object(AppDataLease, "close", close_lease):
            try:
                self._bootstrap_settings_desktop(
                    during_loop, after_exit, content_service=service, source_mode=source_mode,
                    expected_error=(sqlite3.DatabaseError if setup_read_failure else
                                    RuntimeError if exit_mode == "error" else SystemExit),
                    before_run=(lambda fixture: setattr(state, "fixture", fixture)) if setup_read_failure else None,
                )
            finally:
                state.release.set()
                if state.observer is not None:
                    state.observer.join(5)
                    self.assertFalse(state.observer.is_alive())
                for worker in state.threads:
                    worker.join(5)
                    self.assertFalse(worker.is_alive())
                print("R032_CONTENT_CLOSE " + json.dumps({
                    "stage": stage, "exit_mode": exit_mode, "fail_http": fail_http,
                    "source_mode": source_mode, "observed": state.observed,
                    "events": state.events, "http_calls": state.calls,
                    "connections": state.connections, "sql_count": len(state.sql),
                    "timed_out": state.timed_out,
                    "setup_read_failure": setup_read_failure, "denied_reads": state.denied_reads,
                    "setup_error": str(state.setup_error) if state.setup_error is not None else None,
                }))
        self.assertFalse(state.timed_out)

    def test_bootstrap_content_quit_keeps_native_sqlite_and_restore_lease(self):
        self._bootstrap_content_case()

    def test_bootstrap_content_normal_close_drains_after_controller_ready(self):
        self._bootstrap_content_case(exit_mode="normal")

    def test_bootstrap_content_exec_exception_keeps_http_and_restore_lease(self):
        self._bootstrap_content_case(stage="http", exit_mode="error")

    def test_bootstrap_content_http_failure_finishes_before_server_and_lease_close(self):
        self._bootstrap_content_case(stage="http", exit_mode="normal", fail_http=True)

    def test_bootstrap_content_already_finished_thread_joins_without_restart(self):
        self._bootstrap_content_case(stage=None)

    def test_bootstrap_content_disabled_source_has_no_thread_or_http(self):
        self._bootstrap_content_case(stage=None, source_mode="local")

    def test_bootstrap_setup_read_error_keeps_content_and_restore_lease(self):
        self._bootstrap_content_case(setup_read_failure=True)

    def test_bootstrap_settings_close_preserves_inflight_dependency_and_restore_lease(self):
        def close_while_http_is_running(fixture):
            fixture.schedule()
            self.assertTrue(fixture.entered.wait(1))
            fixture.initial_close_accepted = fixture.window.close()
            fixture.timer_active_after_close = fixture.timer.isActive()
            fixture.schedule()
            deadline = time.monotonic() + 1
            while not fixture.window._app_controller.request_close() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            fixture.ready_while_http_running = fixture.window._app_controller.request_close()
            controller = fixture.window._app_controller
            fixture.close_state = {
                "stage": controller._shutdown_stage,
                "shutdown_timer_active": controller._shutdown_timer.isActive(),
                "shutdown_timer_remaining": controller._shutdown_timer.remainingTime(),
                "running_producers": [(type(worker).__name__, label) for worker, label
                                      in controller._producer_workers() if worker is not None and worker.isRunning()],
                "running_writers": [type(worker).__name__ for worker in controller._running_writers()],
                "entry_pending": controller.entry_snapshot_writer.has_pending_work(),
            }
            if not fixture.ready_while_http_running:
                self.assertFalse(fixture.restore_available())
                self.assertNotIn("server.stop", fixture.events)
                fixture.release.set()
                self.wait_until(lambda: fixture.window._app_controller.request_close())

        def assert_before_cleanup(fixture):
            self.assertEqual(
                (False, False, False, 1, ["settings.http", "settings.finished", "server.stop"]),
                (fixture.ready_while_http_running, fixture.timer_active_after_close,
                 fixture.lease_released_while_running, fixture.http_calls, fixture.before_release),
                fixture.close_state,
            )

        fixture = self._bootstrap_settings_desktop(close_while_http_is_running, assert_before_cleanup)
        self.assertTrue(fixture.restore_available())
        with closing(sqlite3.connect(fixture.paths.database_path)) as connection:
            self.assertEqual("4", connection.execute(
                "SELECT value FROM settings WHERE key='rank_query_type'",
            ).fetchone()[0])

    def test_bootstrap_settings_failure_retries_and_close_rejects_delayed_start(self):
        def retry_after_http_failure(fixture):
            settings = fixture.window._settings
            before = settings.get("rank_query_type")
            fixture.fail_http = True
            fixture.schedule()
            self.assertTrue(fixture.entered.wait(1))
            fixture.schedule()
            self.assertEqual(1, fixture.http_calls)
            fixture.release.set()
            self.assertTrue(fixture.completed.wait(2))
            self.wait_until(lambda: not any(
                worker.isRunning() for worker in fixture.window._app_controller.findChildren(SettingsRequestWorker)
            ))
            self.app.processEvents()
            with closing(sqlite3.connect(fixture.paths.database_path)) as connection:
                self.assertEqual(before, connection.execute(
                    "SELECT value FROM settings WHERE key='rank_query_type'",
                ).fetchone()[0])
            self.assertEqual(before, settings.get("rank_query_type"))

            fixture.fail_http = False
            fixture.entered.clear()
            fixture.completed.clear()
            fixture.release.clear()
            fixture.schedule()
            self.assertTrue(fixture.entered.wait(1))
            fixture.schedule()
            self.assertEqual(2, fixture.http_calls)
            self.assertFalse(fixture.window.close())
            self.assertFalse(fixture.timer.isActive())
            self.assertFalse(fixture.restore_available())
            self.assertNotIn("server.stop", fixture.events)
            fixture.release.set()
            self.wait_until(lambda: fixture.window._app_controller.request_close())
            self.assertTrue(fixture.window._app_controller.request_close())
            self.app.processEvents()
            fixture.schedule()
            self.assertEqual(2, fixture.http_calls)
            self.assertEqual("4", settings.get("rank_query_type"))

        def assert_before_cleanup(fixture):
            self.assertEqual([
                "settings.http", "settings.finished", "settings.http", "settings.finished", "server.stop",
            ], fixture.before_release)
            self.assertFalse(fixture.lease_released_while_running)

        fixture = self._bootstrap_settings_desktop(retry_after_http_failure, assert_before_cleanup)
        self.assertTrue(fixture.restore_available())

    def _bootstrap_settings_direct_exit_case(self, *, exit_mode, stage):
        import json
        import threading
        from kiwoom_monitor.infrastructure.central_settings_sync import CentralSettingsSyncService
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease

        state = SimpleNamespace(
            entered=Event(), release=Event(), lease_released=Event(), stopped=Event(),
            active=threading.local(), observer=None, observed=None, timed_out=False,
            traces=[], connections=0, closed=0, events=[], fixture=None,
        )
        sentinel = RuntimeError("controlled settings app.exec failure")
        connect, sync, lease_close = sqlite3.connect, CentralSettingsSyncService.sync, AppDataLease.close

        def trace(statement):
            state.traces.append(statement)
            if stage == "sqlite" and statement.startswith("INSERT INTO settings("):
                state.events.append("settings.native.entered")
                state.entered.set()
                if not state.release.wait(4):
                    state.timed_out = True

        class Connection(sqlite3.Connection):
            def close(connection):
                super().close()
                state.closed += 1
                state.events.append("settings.connection.closed")

        def connection(*args, **kwargs):
            if not getattr(state.active, "value", False):
                return connect(*args, **kwargs)
            value = connect(*args, factory=Connection, **kwargs)
            state.connections += 1
            value.set_trace_callback(trace)
            return value

        def sync_settings(service, database_path):
            state.active.value = True
            try:
                return sync(service, database_path)
            finally:
                state.active.value = False
                state.events.append("settings.body.closed")

        def close_lease(lease):
            shared = not lease._exclusive and not lease._stream.closed
            lease_close(lease)
            if shared:
                state.events.append("lease.released")
                state.lease_released.set()

        def leave_while_settings_is_running(fixture):
            state.fixture = fixture
            stop = fixture.server.stop.side_effect

            def stop_server():
                state.events.append("server.stop")
                stop()
                state.stopped.set()

            fixture.server.stop.side_effect = stop_server
            if stage != "http":
                fixture.release.set()
            fixture.schedule()
            if stage == "finished":
                self.assertTrue(fixture.completed.wait(2))

                def worker_released():
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    return not fixture.window._app_controller.findChildren(SettingsRequestWorker)

                self.wait_until(worker_released)
            else:
                try:
                    self.assertTrue((fixture.entered if stage == "http" else state.entered).wait(1))
                    self.assertFalse(fixture.completed.is_set())
                except BaseException:
                    fixture.release.set()
                    state.release.set()
                    raise

                def observe():
                    try:
                        state.lease_released.wait(.15)
                        state.observed = (
                            state.stopped.is_set(), state.lease_released.is_set(),
                            fixture.restore_available(), fixture.completed.is_set(),
                        )
                    finally:
                        fixture.release.set()
                        state.release.set()

                state.observer = threading.Thread(target=observe, name="settings-exit-observer")
                state.observer.start()
            if exit_mode == "error":
                raise sentinel

        def after_exit(fixture):
            if state.observer is not None:
                state.observer.join(5)
                self.assertFalse(state.observer.is_alive())
            state.exit_error = fixture.exit_error

        try:
            with patch.object(sqlite3, "connect", side_effect=connection), \
                    patch.object(CentralSettingsSyncService, "sync", sync_settings), \
                    patch.object(AppDataLease, "close", close_lease):
                try:
                    fixture = self._bootstrap_settings_desktop(
                        leave_while_settings_is_running, after_exit,
                        expected_error=RuntimeError if exit_mode == "error" else SystemExit,
                        cleanup_window=True,
                    )
                finally:
                    state.release.set()
                    if state.fixture is not None:
                        state.fixture.release.set()
                    if state.observer is not None:
                        state.observer.join(5)
                        self.assertFalse(state.observer.is_alive())
            self.assertFalse(state.timed_out)
            self.assertEqual((1, 1), (state.connections, state.closed))
            self.assertEqual(1, fixture.http_calls)
            self.assertTrue(fixture.completed.is_set())
            self.assertTrue(fixture.restore_available())
            if exit_mode == "error":
                self.assertIs(sentinel, state.exit_error)
            with closing(connect(fixture.paths.database_path)) as native:
                self.assertEqual("4", native.execute(
                    "SELECT value FROM settings WHERE key='rank_query_type'",
                ).fetchone()[0])
                self.assertEqual("2030-01-02T10:00:00+09:00", native.execute(
                    "SELECT updated_at FROM central_setting_versions WHERE setting_key='rank_query_type'",
                ).fetchone()[0])
            if stage != "finished":
                self.assertEqual((False, False, False, False), state.observed, state.events)
            self.assertLess(state.events.index("settings.connection.closed"), state.events.index("server.stop"))
            self.assertLess(state.events.index("server.stop"), state.events.index("lease.released"))
        finally:
            print("R032_SETTINGS_EXIT_OBSERVATION " + json.dumps({
                "exit_mode": exit_mode, "stage": stage, "observed": state.observed,
                "events": state.events, "timed_out": state.timed_out,
                "native_cost": {"connections": state.connections, "closed": state.closed, **{
                    operation: sum(statement.lstrip().upper().startswith(operation.upper())
                                   for statement in state.traces)
                    for operation in ("select", "insert", "begin", "commit")
                }},
            }))

    def test_bootstrap_settings_direct_exec_return_preserves_sync_and_restore_lease(self):
        for stage in ("http", "sqlite", "finished"):
            with self.subTest(stage=stage):
                self._bootstrap_settings_direct_exit_case(exit_mode="quit", stage=stage)

    def test_bootstrap_settings_exec_error_preserves_sync_and_original_error(self):
        for stage in ("http", "sqlite", "finished"):
            with self.subTest(stage=stage):
                self._bootstrap_settings_direct_exit_case(exit_mode="error", stage=stage)

    def _bootstrap_retention_case(self, *, fail_minute=False, exit_mode="normal", finish_before_exit=False,
                                  query_client_factory=None, before_retention=None, fallback_now=None):
        import json
        import threading
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.persistence.daily_bar_repository import DailyBarRepository
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease

        fixed = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
        cutoff = fixed.date() - timedelta(days=30)
        old_minute = datetime.combine(cutoff - timedelta(days=1), datetime.min.time()).replace(hour=9)
        kept_minute = old_minute + timedelta(days=1)
        state = SimpleNamespace(
            entered=Event(), release=Event(), done=Event(), phase=threading.local(),
            traces=[], connections=0, timed_out=False, begin_count=0, cutoffs=[], limits=[],
            lease_released=Event(), stopped=Event(), observer=None, errors=[], observed=None,
        )
        connect = sqlite3.connect
        purge = MinuteBarRepository.purge_before
        retain = DailyBarRepository.retain_latest
        lease_close = AppDataLease.close

        def close_lease(lease):
            shared = not lease._exclusive and not lease._stream.closed
            lease_close(lease)
            if shared and exit_mode != "normal":
                state.fixture.events.append("lease.released")
                state.lease_released.set()

        def trace(statement):
            phase = state.phase.value
            state.traces.append((phase, statement))
            if phase == "minute" and statement.strip().upper().startswith("BEGIN"):
                state.begin_count += 1
                state.fixture.events.append("retention.begin")
                state.entered.set()
                if not finish_before_exit and not state.release.wait(4):
                    state.timed_out = True

        def connection(*args, **kwargs):
            value = connect(*args, **kwargs)
            if getattr(state.phase, "value", None) is not None:
                state.connections += 1
                value.set_trace_callback(trace)
                if fail_minute and state.phase.value == "minute":
                    value.set_authorizer(lambda action, *_args:
                        sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DELETE else sqlite3.SQLITE_OK)
            return value

        def purge_minute(repository, value):
            state.cutoffs.append(value)
            state.phase.value = "minute"
            try:
                return purge(repository, value)
            finally:
                state.fixture.events.append("retention.minute.closed")
                state.phase.value = None

        def retain_daily(repository, limit):
            state.limits.append(limit)
            state.phase.value = "daily"
            try:
                return retain(repository, limit)
            finally:
                state.fixture.events.append("retention.daily.closed")
                state.phase.value = None
                state.done.set()

        def close_while_native_begin_is_running(fixture):
            state.fixture = fixture
            if exit_mode != "normal":
                stop = fixture.server.stop.side_effect

                def stop_server():
                    stop()
                    state.stopped.set()

                fixture.server.stop.side_effect = stop_server
            repository = MinuteBarRepository(fixture.paths.database_path, now_provider=lambda: fixed)
            repository.upsert_bars("A", (
                MinuteOhlcv(old_minute, 100, 110, 90, 105, 10),
                MinuteOhlcv(kept_minute, 100, 110, 90, 105, 10),
            ))
            with closing(connect(fixture.paths.database_path)) as native, native:
                native.executemany(
                    "INSERT INTO daily_bars(stock_code,trade_date,high_price,trade_value_eok,close_price) "
                    "VALUES(?,?,?,?,?)",
                    (("A", (fixed.date() - timedelta(days=index)).isoformat(), index, None, index)
                     for index in range(280)),
                )
            delay, callback = next(
                (delay, callback) for delay, callback in fixture.callbacks
                if getattr(callback, "__name__", "") == "retain_market_bars_after_startup"
            )
            self.assertEqual(30_000, delay)
            fixture.retention_callback = callback
            if before_retention is not None:
                before_retention(fixture)
            callback()
            try:
                if fail_minute or finish_before_exit:
                    self.assertTrue(state.done.wait(2))
                    if finish_before_exit:
                        def worker_released():
                            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                            return not fixture.window._app_controller.findChildren(SettingsRequestWorker)

                        self.wait_until(worker_released)
                else:
                    self.assertTrue(state.entered.wait(1))
                    self.assertEqual(1, state.begin_count)
            except BaseException:
                state.release.set()
                raise

            if exit_mode != "normal":
                if not state.done.is_set():
                    def observe():
                        try:
                            state.lease_released.wait(0.15)
                            state.observed = (state.stopped.is_set(), state.lease_released.is_set(),
                                              fixture.restore_available())
                            self.assertEqual((False, False, False), state.observed, fixture.events)
                            self.assertFalse(state.done.is_set())
                        except BaseException as error:
                            state.errors.append(error)
                        finally:
                            state.release.set()

                    state.observer = threading.Thread(target=observe, name="retention-close-observer")
                    state.observer.start()
                if exit_mode == "error":
                    raise RuntimeError("controlled retention app.exec failure")
                return

            try:
                fixture.window.close()
                deadline = time.monotonic() + 1
                while not fixture.window._app_controller.request_close() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.005)
                state.ready_before_release = fixture.window._app_controller.request_close()
                if not state.ready_before_release:
                    self.assertFalse(fixture.restore_available())
                    self.assertNotIn("server.stop", fixture.events)
                    state.release.set()
                    self.wait_until(lambda: fixture.window._app_controller.request_close())
            except BaseException:
                state.release.set()
                raise

        def assert_before_cleanup(fixture):
            try:
                if exit_mode != "normal":
                    if state.observer is not None:
                        state.observer.join(5)
                        self.assertFalse(state.observer.is_alive())
                    if state.errors:
                        raise state.errors[0]
                    self.assertTrue(state.done.is_set())
                    self.assertLess(fixture.before_release.index("retention.daily.closed"),
                                    fixture.before_release.index("server.stop"))
                    self.assertLess(fixture.before_release.index("server.stop"),
                                    fixture.before_release.index("lease.released"))
                    if exit_mode == "error":
                        self.assertEqual("controlled retention app.exec failure", str(fixture.exit_error))
                    return
                state.lease_released_while_running = not state.done.is_set() and fixture.restore_available()
                self.assertFalse(state.timed_out)
                self.assertEqual(0, fixture.http_calls, "settings sync must not be started by this retention test")
                if not fail_minute:
                    self.assertEqual(
                        (False, True, ["retention.begin", "retention.minute.closed", "retention.daily.closed", "server.stop"]),
                        (state.ready_before_release, state.done.is_set(), fixture.before_release),
                        {"stage": fixture.window._app_controller._shutdown_stage,
                         "exclusive_restore_available": fixture.restore_available(),
                         "native_begin_entered": state.entered.is_set()},
                    )
                self.assertTrue(fixture.restore_available())
            finally:
                state.release.set()

        try:
            with patch("kiwoom_monitor.domain.time_basis.kst_now", return_value=fallback_now or fixed), \
                    patch.object(sqlite3, "connect", side_effect=connection), \
                    patch.object(AppDataLease, "close", close_lease), \
                    patch.object(MinuteBarRepository, "purge_before", purge_minute), \
                    patch.object(DailyBarRepository, "retain_latest", retain_daily):
                try:
                    fixture = self._bootstrap_settings_desktop(
                        close_while_native_begin_is_running, assert_before_cleanup,
                        expected_error=RuntimeError if exit_mode == "error" else SystemExit,
                        cleanup_window=exit_mode != "normal",
                        query_client_factory=query_client_factory,
                    )
                    with patch.object(bootstrap, "SettingsRequestWorker", wraps=SettingsRequestWorker) as start:
                        fixture.retention_callback()
                        start.assert_not_called()
                finally:
                    state.release.set()
                    if state.observer is not None:
                        state.observer.join(5)
                        self.assertFalse(state.observer.is_alive())
                    if getattr(state, "fixture", None) is not None:
                        for worker in state.fixture.window._app_controller.findChildren(SettingsRequestWorker):
                            self.assertTrue(worker.wait(5000))
                    for worker in threading.enumerate():
                        if worker.name == "market-bar-retention":
                            worker.join(5)
                            self.assertFalse(worker.is_alive())
        finally:
            print("R032_RETENTION_OBSERVATION " + json.dumps({
                "case": "minute_failure" if fail_minute else "native_begin_close",
                "ready_before_release": getattr(state, "ready_before_release", None),
                "lease_released_while_running": getattr(state, "lease_released_while_running", None),
                "events_before_cleanup": getattr(getattr(state, "fixture", None), "before_release", None),
                "done_after_cleanup": state.done.is_set(), "timed_out": state.timed_out,
                "exit_mode": exit_mode, "finish_before_exit": finish_before_exit,
                "observed": state.observed,
                "native_cost": {"connections": state.connections, **{
                    operation: sum(statement.lstrip().upper().startswith(operation.upper())
                                   for _phase, statement in state.traces)
                    for operation in ("select", "delete", "begin", "commit")
                }},
            }))
        self.assertTrue(state.done.is_set())
        self.assertEqual([cutoff], state.cutoffs)
        self.assertEqual([250], state.limits)
        with closing(connect(fixture.paths.database_path)) as native:
            self.assertEqual(2 if fail_minute else 1, native.execute(
                "SELECT count(*) FROM minute_bars WHERE stock_code='A'",
            ).fetchone()[0])
            self.assertEqual(250, native.execute(
                "SELECT count(*) FROM daily_bars WHERE stock_code='A'",
            ).fetchone()[0])
            self.assertEqual(1, native.execute(
                "SELECT count(*) FROM market_data_observation_meta WHERE dataset_kind='minute_bar' "
                "AND subject='A' AND observation_key LIKE ?", (kept_minute.date().isoformat() + "%",),
            ).fetchone()[0])
            self.assertEqual(2 if fail_minute else 1, native.execute(
                "SELECT count(*) FROM market_data_observation_meta WHERE dataset_kind='minute_bar' AND subject='A'",
            ).fetchone()[0])
        return fixture, state

    def test_bootstrap_retention_close_preserves_native_begin_and_restore_lease(self):
        self._bootstrap_retention_case()

    def test_bootstrap_retention_quit_keeps_native_begin_and_restore_lease(self):
        self._bootstrap_retention_case(exit_mode="quit")

    def test_bootstrap_retention_exec_exception_keeps_native_begin_and_restore_lease(self):
        self._bootstrap_retention_case(exit_mode="error")

    def test_bootstrap_retention_finished_deleted_worker_does_not_restart(self):
        self._bootstrap_retention_case(exit_mode="quit", finish_before_exit=True)

    def test_bootstrap_retention_quit_after_minute_failure_keeps_daily_cleanup(self):
        self._bootstrap_retention_case(exit_mode="quit", fail_minute=True)

    def test_bootstrap_retention_minute_sql_failure_keeps_daily_cleanup_and_blocks_late_start(self):
        self._bootstrap_retention_case(fail_minute=True)

    def test_bootstrap_retention_uses_replaced_api_clock_in_kst(self):
        import threading

        main_thread = threading.get_ident()
        for value in (datetime.fromisoformat("2030-01-01T15:05:00+00:00"),
                      datetime(2030, 1, 2, 23, 5)):
            with self.subTest(clock=value.isoformat()):
                worker_clock_calls = []

                def corrected_now():
                    if threading.get_ident() != main_thread:
                        worker_clock_calls.append(1)
                    return value

                old_client = SimpleNamespace(server_now=lambda: datetime(2030, 1, 1, 10))
                new_client = SimpleNamespace(server_now=corrected_now)
                clients = Mock(side_effect=(old_client, new_client))

                def replace_runtime(fixture):
                    controller = fixture.window._app_controller
                    old_loader = controller.ranking_loader
                    controller.restart_for_api_settings()
                    controller._advance_api_reload()
                    controller._advance_api_reload()
                    controller._initial_ranking_timer.stop()
                    self.assertFalse(controller.api_reloading)
                    self.assertIsNot(old_loader, controller.ranking_loader)
                    self.assertEqual(2, clients.call_count)
                    self.assertEqual(date(2030, 1, 2), as_kst(controller.ranking_now()).date())

                fixture, state = self._bootstrap_retention_case(
                    query_client_factory=clients, before_retention=replace_runtime,
                    fallback_now=as_kst(datetime(2030, 1, 3, 10)),
                )
                self.assertEqual((1, 0), (len(worker_clock_calls), fixture.network_calls))
                self.assertEqual((2, 2, 6, 2, 2), (state.connections, *(
                    sum(statement.lstrip().upper().startswith(operation)
                        for _phase, statement in state.traces)
                    for operation in ("SELECT", "DELETE", "BEGIN", "COMMIT")
                )))

    def test_bootstrap_retention_without_api_clock_uses_common_kst_fallback(self):
        for mode in ("unconfigured", "offline"):
            with self.subTest(mode=mode):
                clients = (Mock(side_effect=ValueError("controlled unconfigured API"))
                           if mode == "unconfigured" else Mock(return_value=SimpleNamespace()))
                fixture, _state = self._bootstrap_retention_case(query_client_factory=clients)
                clients.assert_called_once()
                self.assertEqual(0, fixture.network_calls)

    def test_runtime_provider_swap_and_view_rollback_use_public_setter_without_old_cache_publication(self):
        import threading
        from dataclasses import replace
        from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
        from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import TradeEntrySnapshot, scoped_snapshot_execution_key

        for rollback in (False, True):
            with self.subTest(rollback=rollback), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "journal.sqlite3"
                scope = AccountScope("kiwoom", AccountEnvironment.MOCK, "00000000-0000-4000-8000-000000000030")
                at = datetime(2026, 10, 20, 10, 30)
                snapshot = TradeEntrySnapshot(scoped_snapshot_execution_key("apply-old-030", scope), "order-030",
                    "005930", "삼성전자", "매수", at, 70000, 1, "KRX", origin_scope=scope)
                entered, release = threading.Event(), threading.Event()
                old_calls, new_calls = [], []
                def old(code, observed):
                    old_calls.append((code, observed))
                    count = len(old_calls)
                    if count == 1:
                        entered.set()
                        if not release.wait(3):
                            raise TimeoutError("controller did not release old provider")
                    return {"available": True, "source": "old", "provider_call": count}
                def new(code, observed):
                    new_calls.append((code, observed))
                    return {"available": True, "source": "new", "provider_call": len(new_calls)}
                controller = self.controller([])
                writer = EntrySnapshotWriter(path, investor_loader=old)
                controller.entry_snapshot_writer = writer
                # A valid pre-swap cache entry must be restored on view rollback.
                writer._investor_cache["000660"] = (time.monotonic(), {"available": True, "source": "old-seed"})
                runtime = {"entry_investor_loader": new, "program_trade_loader": None}
                def apply_runtime(_runtime):
                    if rollback:
                        raise RuntimeError("controlled view rollback")
                actions = AppRuntimeActions(apply_runtime=apply_runtime)
                writer.enqueue(snapshot)
                writer.start()
                try:
                    self.assertTrue(entered.wait(2))
                    with patch.object(writer, "replace_sources", wraps=writer.replace_sources) as source_change:
                        if rollback:
                            with self.assertRaisesRegex(RuntimeError, "controlled view rollback"):
                                controller._apply_api_runtime(runtime, actions)
                        else:
                            controller._apply_api_runtime(runtime, actions)
                        source_change.assert_called_once_with(investor_loader=new, program_loader=None)
                    self.assertFalse(release.is_set())
                    release.set()
                    writer.enqueue(replace(snapshot, execution_key=scoped_snapshot_execution_key("apply-new-030", scope),
                                           executed_at=at + timedelta(seconds=1)))
                    writer.requestInterruption()
                    self.assertTrue(writer.wait(5000))
                    stored = JournalRepository(path).load_entry_snapshots("005930", at, at + timedelta(seconds=2), scope)
                    self.assertEqual(2, len(stored))
                    self.assertEqual({"available": True, "source": "old", "provider_call": 1}, stored[0].investor_flow)
                    self.assertEqual({"available": True, "source": "old" if rollback else "new", "provider_call": 2 if rollback else 1}, stored[1].investor_flow)
                    self.assertEqual((2, 0) if rollback else (1, 1), (len(old_calls), len(new_calls)))
                    if rollback:
                        self.assertEqual({"available": True, "source": "old-seed", "cached": True},
                                         writer._investor_at_execution(replace(snapshot, stock_code="000660")))
                    else:
                        self.assertNotIn("000660", writer._investor_cache)
                    self.assertFalse(writer.has_pending_work())
                finally:
                    release.set()
                    writer.requestInterruption()
                    self.assertTrue(writer.wait(5000))

    def test_runtime_retirement_records_committed_old_client_even_if_later_reset_fails(self):
        from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
        for reset_fails in (False, True):
            with self.subTest(reset_fails=reset_fails):
                old, candidate = SimpleNamespace(close=Mock()), SimpleNamespace(close=Mock())
                controller = self.controller([], market_data_client=old, owned_observers=(old,))
                writer = EntrySnapshotWriter(Path("unused-retirement.sqlite3"))
                writer.setParent(controller)
                controller.entry_snapshot_writer = writer
                runtime = {"market_data_client": candidate, "owned_observers": (candidate,)}
                sentinel = OSError("fixture post-commit reset")
                actions = AppRuntimeActions(apply_runtime=lambda _runtime: None)
                with patch.object(controller.realtime_subscription, "reset", side_effect=sentinel if reset_fails else None):
                    if reset_fails:
                        with self.assertRaises(OSError) as caught:
                            controller._apply_api_runtime(runtime, actions)
                        self.assertIs(sentinel, caught.exception)
                    else:
                        controller._apply_api_runtime(runtime, actions)
                self.assertIs(candidate, controller.market_data_client)
                retained = getattr(controller, "_api_client_retirements", ())
                self.assertEqual(1, len(retained), "the detached old observer must have an owner")
                self.assertIs(old, retained[0].client)
                self.assertTrue(retained[0].runtime_detached)
                self.assertIsNotNone(retained[0].source_release)
                self.assertTrue(retained[0].source_release.released)
                self.assertIs(sentinel if reset_fails else None, retained[0].error)
                old.close.assert_not_called()
                candidate.close.assert_not_called()

    def test_runtime_retirement_records_only_unpublished_or_completely_restored_candidate(self):
        from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
        for failure in ("capture", "factory", "view"):
            with self.subTest(failure=failure):
                old, candidate = SimpleNamespace(close=Mock()), SimpleNamespace(close=Mock())
                controller = self.controller([], market_data_client=old, owned_observers=(old,))
                writer = EntrySnapshotWriter(Path("unused-retirement.sqlite3"))
                writer.setParent(controller)
                controller.entry_snapshot_writer = writer
                sentinel = ValueError("fixture " + failure)
                new_factory = Mock()
                old_factory = controller.fundamentals._worker_factory
                original_set = controller.fundamentals.set_factory
                runtime = {"market_data_client": candidate, "owned_observers": (candidate,), "fundamentals_worker_factory": new_factory}
                def capture():
                    if failure == "capture":
                        raise sentinel
                    return lambda: None
                def apply(_runtime):
                    if failure == "view":
                        raise sentinel
                def set_factory(value):
                    if failure == "factory" and value is new_factory:
                        raise sentinel
                    original_set(value)
                actions = AppRuntimeActions(apply_runtime=apply, capture_state=capture)
                with patch.object(writer, "replace_sources", wraps=writer.replace_sources) as source_change, \
                        patch.object(controller.fundamentals, "set_factory", side_effect=set_factory):
                    with self.assertRaises(ValueError) as caught:
                        controller._apply_api_runtime(runtime, actions)
                self.assertIs(sentinel, caught.exception)
                self.assertIs(old, controller.market_data_client)
                self.assertIs(old_factory, controller.fundamentals._worker_factory)
                self.assertEqual(1 if failure == "view" else 0, source_change.call_count)
                retained = getattr(controller, "_api_client_retirements", ())
                self.assertEqual(1, len(retained), "the failed candidate must remain owned after rollback")
                self.assertIs(candidate, retained[0].client)
                self.assertTrue(retained[0].runtime_detached)
                self.assertIs(sentinel, retained[0].error)
                if failure == "view":
                    self.assertIsNotNone(retained[0].source_release)
                    self.assertTrue(retained[0].source_release.released)
                else:
                    self.assertIsNone(retained[0].source_release)
                old.close.assert_not_called()
                candidate.close.assert_not_called()

    def test_runtime_retirement_keeps_restore_failure_unknown_despite_released_source(self):
        from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
        for failure in ("source_enter", "factory_restore", "view_restore"):
            with self.subTest(failure=failure):
                old, candidate = SimpleNamespace(close=Mock()), SimpleNamespace(close=Mock())
                controller = self.controller([], market_data_client=old, owned_observers=(old,))
                writer = EntrySnapshotWriter(Path("unused-retirement.sqlite3"))
                writer.setParent(controller)
                controller.entry_snapshot_writer = writer
                original = ValueError("fixture apply")
                restore = OSError("fixture " + failure)
                old_factory = controller.realtime._worker_factory
                new_factory = Mock()
                original_set = controller.realtime.set_factory
                def apply(_runtime):
                    raise original
                def restore_view():
                    if failure == "view_restore":
                        raise restore
                def set_factory(value):
                    if failure == "factory_restore" and value is old_factory:
                        raise restore
                    original_set(value)
                actions = AppRuntimeActions(apply_runtime=apply, capture_state=lambda: restore_view)
                with patch.object(controller.realtime, "set_factory", side_effect=set_factory), \
                        (patch.object(writer, "replace_sources", side_effect=original)
                         if failure == "source_enter" else nullcontext()):
                    with self.assertRaises(ValueError if failure == "source_enter" else RuntimeError) as caught:
                        controller._apply_api_runtime({"market_data_client": candidate, "owned_observers": (candidate,),
                                                       "realtime_worker_factory": new_factory}, actions)
                retained = getattr(controller, "_api_client_retirements", ())
                self.assertEqual(1, len(retained))
                self.assertIs(candidate, retained[0].client)
                self.assertFalse(retained[0].runtime_detached, "source release cannot prove runtime references restored")
                self.assertIs(caught.exception, retained[0].error)
                if failure == "source_enter":
                    self.assertIs(original, retained[0].error)
                    self.assertIsNone(retained[0].source_release)
                else:
                    self.assertIs(original, retained[0].error.__cause__)
                    self.assertIn(str(restore), str(caught.exception))
                    self.assertIsNotNone(retained[0].source_release)
                    self.assertTrue(retained[0].source_release.released)
                self.assertIs(old, controller.market_data_client)
                self.assertIs(new_factory if failure == "factory_restore" else old_factory,
                              controller.realtime._worker_factory)
                old.close.assert_not_called()
                candidate.close.assert_not_called()

    def test_runtime_retirement_never_targets_the_same_installed_client_identity(self):
        for failure in (None, "capture", "view"):
            with self.subTest(failure=failure):
                client = SimpleNamespace(close=Mock())
                controller = self.controller([], market_data_client=client, owned_observers=(client,))
                sentinel = ValueError("fixture same client")
                def capture():
                    if failure == "capture":
                        raise sentinel
                    return lambda: None
                def apply(_runtime):
                    if failure == "view":
                        raise sentinel
                actions = AppRuntimeActions(apply_runtime=apply, capture_state=capture)
                if failure:
                    with self.assertRaises(ValueError) as caught:
                        controller._apply_api_runtime({"market_data_client": client, "owned_observers": (client,)}, actions)
                    self.assertIs(sentinel, caught.exception)
                else:
                    controller._apply_api_runtime({"market_data_client": client, "owned_observers": (client,)}, actions)
                self.assertIs(client, controller.market_data_client)
                self.assertEqual([], list(getattr(controller, "_api_client_retirements", ())))
                client.close.assert_not_called()

    def _api_retirement_probe(self, *, fact_mode="normal", close_error=None, fact_error=None):
        calls, closed = [], Event()
        def close():
            calls.append(QThread.currentThread())
            closed.set()
            if close_error is not None:
                raise close_error
        def fact():
            if fact_mode == "read_error":
                raise fact_error
            return SimpleNamespace(
                admission_closed=closed.is_set(), active_requests=1 if fact_mode == "active" else 0,
                worker_stopped=closed.is_set() and fact_mode != "alive",
                failure_stage="unexpected" if fact_mode == "unknown_stage" else ("report" if close_error else None),
            )
        client = SimpleNamespace(close=close)
        if fact_mode != "missing":
            client.comparison_lifecycle_state = fact
        return client, calls, closed

    def _drain_api_retirement_fixture(self, controller):
        if controller is None:
            return
        controller._shutdown_timer.stop()
        for item in tuple(getattr(controller, "_api_client_retirements", ())):
            if item.worker is not None:
                self.assertTrue(item.worker.wait(4000))
                self.assertTrue(item.worker.release_after_join())
        self.app.processEvents()

    def test_actual_api_reload_ends_old_comparison_daemon_after_whole_source_release(self):
        import threading
        from kiwoom_monitor.infrastructure.kiwoom_rest.validation_client import ParallelValidationClient
        from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
        local_entered, local_resume = Event(), Event()
        source_entered, source_resume = Event(), Event()
        completed, close_entered = Event(), Event()
        observed_threads, close_threads, old_values = [], [], []
        original_thread = threading.Thread
        def thread_factory(*args, **kwargs):
            thread = original_thread(*args, **kwargs)
            if kwargs.get("name") == "central-local-validation":
                observed_threads.append(thread)
            return thread
        def primary(api_id, path, body, **kwargs):
            return ({"revision_id": api_id, "value": 7}, False, "")
        def local(api_id, path, body, **kwargs):
            if api_id == "ka10001":
                local_entered.set()
                if not local_resume.wait(3):
                    raise TimeoutError("fixture comparison release")
            return primary(api_id, path, body, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            with patch("kiwoom_monitor.infrastructure.kiwoom_rest.validation_client.threading.Thread", side_effect=thread_factory):
                old = ParallelValidationClient(SimpleNamespace(request_with_continuation=primary),
                    SimpleNamespace(request_with_continuation=local), Path(directory) / "report.jsonl")
            self.assertEqual(1, len(observed_threads))
            daemon = observed_threads[0]
            original_close = old.close
            def close():
                close_threads.append(QThread.currentThread())
                close_entered.set()
                return original_close()
            old.close = close
            def old_source(code, observed):
                source_entered.set()
                if not source_resume.wait(3):
                    raise TimeoutError("fixture whole source release")
                return {"available": True, "source": "old", "value": old.request("ka10045", "/fixture", {})["value"]}
            writer = EntrySnapshotWriter(Path(directory) / "unused.sqlite3", investor_loader=old_source)
            current = object()
            runtime = {"market_data_client": current,
                       "entry_investor_loader": lambda *args: {"available": True, "source": "new"}}
            controller = self.controller([], market_data_client=old, api_runtime_factory=lambda: runtime)
            controller._owned_observers = (old,)
            writer.setParent(controller)
            controller.entry_snapshot_writer = writer
            controller.configure_runtime(AppRuntimeActions(apply_runtime=lambda _: None))
            controller.api_reload_completed.connect(completed.set)
            snapshot = SimpleNamespace(stock_code="005930", executed_at=datetime(2026, 10, 20, 20, 5))
            caller = original_thread(target=lambda: old_values.append(writer._investor_at_execution(snapshot)))
            caller.start()
            try:
                self.assertEqual(7, old.request("ka10001", "/fixture", {})["value"])
                self.assertTrue(local_entered.wait(2))
                self.assertTrue(source_entered.wait(2))
                with patch.object(controller, "start_initial_ranking"):
                    controller.restart_for_api_settings()
                    self.wait_until(completed.is_set)
                self.assertIs(current, controller.market_data_client)
                self.assertFalse(close_entered.is_set(), "close cut an admitted whole source call")
                self.assertEqual({"available": True, "source": "new"}, writer._investor_at_execution(snapshot))
                source_resume.set()
                caller.join(3)
                self.assertFalse(caller.is_alive())
                self.assertEqual([{"available": True, "source": "old", "value": 7}], old_values)
                local_resume.set()
                daemon.join(3)
                # This behavior assertion precedes all new owner/fact reads. The
                # current baseline leaves the real compared client idle/alive.
                self.assertFalse(daemon.is_alive(), "API replacement left the old comparison daemon alive")
                self.assertTrue(close_entered.is_set())
                self.assertTrue(all(thread is not controller.thread() for thread in close_threads))
                controller.request_close()
                self.wait_until(lambda: controller._shutdown_stage == "ready")
                self.assertEqual([], controller._api_client_retirements)
            finally:
                source_resume.set()
                local_resume.set()
                caller.join(3)
                original_close()
                daemon.join(3)
                self._drain_api_retirement_fixture(controller)

    def test_retained_cleanup_native_tail_cannot_auto_delete_or_block_writer_drain(self):
        from shiboken6 import isValid
        from kiwoom_monitor.presentation import app_controller as module
        tail_entered, tail_resume = Event(), Event()
        created = []
        class TailRequest(SettingsRequestWorker):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                created.append(self)
            def run(self):
                super().run()
                # Controlled early finished notification while a real native
                # QThread tail remains live; not a claim of observed Qt TLS cause.
                self.finished.emit()
                tail_entered.set()
                if not tail_resume.wait(3):
                    raise TimeoutError("fixture native tail release")
        old, calls, closed = self._api_retirement_probe()
        events = []
        controller = self.controller(events, market_data_client=old, owned_observers=(old,),
                                     api_runtime_factory=lambda: {"market_data_client": object()})
        controller.configure_runtime(AppRuntimeActions(apply_runtime=lambda _: None))
        controller._api_reloading = True
        controller._api_reload_stage = "signals"
        try:
            with patch.object(module, "SettingsRequestWorker", TailRequest), patch.object(controller, "start_initial_ranking"):
                controller._advance_api_reload()
                self.wait_until(tail_entered.is_set)
                self.wait_until(lambda: bool(controller._api_client_retirements) and
                                controller._api_client_retirements[0].result is not None)
                retirement = controller._api_client_retirements[0]
                worker = created[0]
                self.assertTrue(closed.is_set())
                self.assertIs(worker, retirement.worker)
                self.assertFalse(worker.wait(0))
                self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()
                self.assertTrue(isValid(worker), "finished must not auto-delete an explicitly retained native tail")
                cache = FakeWriter(events, completes=False)
                controller.market_cache_writer = cache
                self.assertFalse(controller.request_close())
                self.assertIn("cache_drain", events, "cleanup was treated as a producer ahead of writer drain")
                self.assertNotIn("ready", events)
                cache.running = False
                self.wait_until(lambda: controller._shutdown_stage == "api_retirements")
                self.assertNotIn("ready", events)
                self.assertFalse(worker.wait(0))
                tail_resume.set()
                self.wait_until(lambda: controller._shutdown_stage == "ready")
                self.assertEqual([], controller._api_client_retirements)
                self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()
                self.assertFalse(isValid(worker))
                self.assertEqual(1, len(calls))
        finally:
            tail_resume.set()
            for worker in created:
                if isValid(worker):
                    worker.wait(4000)
                    worker.release_after_join()

    def test_current_comparison_report_error_joins_after_writer_drain_without_permanent_gate(self):
        import threading
        from kiwoom_monitor.infrastructure.kiwoom_rest.validation_client import ParallelValidationClient
        with self.subTest(writer="fake_cache"):
            observed, writes, close_threads = [], [], []
            original_thread = threading.Thread
            def thread_factory(*args, **kwargs):
                thread = original_thread(*args, **kwargs)
                if kwargs.get("name") == "central-local-validation":
                    observed.append(thread)
                return thread
            def request(*args, **kwargs):
                return ({"revision_id": "r1", "value": 7}, False, "")
            sentinel = PermissionError("fixture report denied")
            original_open = Path.open
            with tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / "report.jsonl"
                def denied(path, *args, **kwargs):
                    if path == report:
                        writes.append(path)
                        raise sentinel
                    return original_open(path, *args, **kwargs)
                with patch("kiwoom_monitor.infrastructure.kiwoom_rest.validation_client.threading.Thread", side_effect=thread_factory):
                    client = ParallelValidationClient(SimpleNamespace(request_with_continuation=request),
                        SimpleNamespace(request_with_continuation=request), report)
                original_close = client.close
                def close():
                    close_threads.append(QThread.currentThread())
                    return original_close()
                client.close = close
                events = []
                controller = self.controller(events, market_data_client=client)
                controller._owned_observers = (client,)
                cache = FakeWriter(events, completes=False)
                controller.market_cache_writer = cache
                with patch.object(Path, "open", denied):
                    try:
                        self.assertEqual(7, client.request("ka10001", "/fixture", {})["value"])
                        observed[0].join(3)
                        self.assertFalse(observed[0].is_alive())
                        self.assertFalse(controller.request_close())
                        self.assertIn("cache_drain", events)
                        self.assertEqual([], close_threads, "current observer closed before native writer drain")
                        cache.running = False
                        self.wait_until(lambda: controller._shutdown_stage == "ready")
                        self.assertEqual(1, len(close_threads), "current observer must be handed off exactly once")
                        self.assertIsNot(close_threads[0], controller.thread())
                        self.assertIsNone(controller.market_data_client)
                        self.assertEqual(1, len(controller._api_client_retirements))
                        retirement = controller._api_client_retirements[0]
                        self.assertIs(client, retirement.client)
                        self.assertIs(sentinel, retirement.result.error)
                        self.assertEqual("close", retirement.result.error_stage)
                        self.assertIsNone(retirement.result.observation_error)
                        self.assertTrue(retirement.result.lifecycle.worker_stopped)
                        self.assertTrue(retirement.result.lifecycle.admission_closed)
                        self.assertEqual(0, retirement.result.lifecycle.active_requests)
                        self.assertEqual("report", retirement.result.lifecycle.failure_stage)
                        self.assertIs(True, retirement.native_result)
                        self.assertEqual([report], writes)
                        self.assertFalse(report.exists())
                        self.assertTrue(controller.request_close())
                        self.assertEqual(1, len(close_threads))
                    finally:
                        with self.assertRaises(PermissionError) as caught:
                            original_close()
                        self.assertIs(sentinel, caught.exception)
                        observed[0].join(3)
                        self._drain_api_retirement_fixture(controller)

        with self.subTest(writer="native_entry_ack_loss"), tempfile.TemporaryDirectory() as native_directory:
            from dataclasses import replace
            from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
            from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
            from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import (
                TradeEntrySnapshot, scoped_snapshot_execution_key,
            )
            native_journal = Path(native_directory) / "journal.sqlite3"
            native_repository = JournalRepository(native_journal)
            native_at = as_kst(datetime(2026, 10, 19, 10, 30))
            native_scope = AccountScope("kiwoom", AccountEnvironment.MOCK,
                                       "00000000-0000-4000-8000-000000000061")
            native_snapshot = TradeEntrySnapshot(
                scoped_snapshot_execution_key("rest-observer-closure-061", native_scope), "order-061",
                "005930", "삼성전자", "매수", native_at, 70000, 1, "KRX", origin_scope=native_scope,
                market_state={"source": "native-original-061"}, orderbook={"bid": 69900},
                capture_state="realtime_core",
                investor_flow={"program_trade": {"available": True, "source": "native-original-0w-061"}},
            )
            native_news = ({"title": "native original news-061", "published_at": "2026-10-19T10:29:00+09:00"},)
            native_investor = {"available": True, "source": "native-investor-061", "value": 3}
            native_expected = replace(native_snapshot, news=native_news, capture_state="realtime_enriched",
                investor_flow={**native_investor, "program_trade": native_snapshot.investor_flow["program_trade"]})
            native_saves, native_loads, native_failures, native_events = [], [], [], []
            native_threads, native_report_writes, native_close_observations, native_local_calls = [], [], [], []
            native_report_error = PermissionError("fixture native-path report denied")
            native_ack_error = RuntimeError("061 enriched native COMMIT ACK loss")
            native_committed, native_ack_resume = Event(), Event()
            native_retry_committed, native_retry_resume = Event(), Event()
            native_local_entered, native_local_resume = Event(), Event()
            native_lost_ack = []
            native_report = Path(native_directory) / "report.jsonl"
            native_original_thread = threading.Thread
            native_original_open = Path.open
            native_save = JournalRepository.save_entry_snapshot
            def native_thread_factory(*args, **kwargs):
                thread = native_original_thread(*args, **kwargs)
                if kwargs.get("name") == "central-local-validation":
                    native_threads.append(thread)
                return thread
            def native_request(api_id, path, body, **kwargs):
                return ({"revision_id": api_id, "value": body["n"]}, False, "")
            def native_local(api_id, path, body, **kwargs):
                native_local_calls.append((api_id, dict(body)))
                if api_id == "ka10001":
                    native_local_entered.set()
                    if not native_local_resume.wait(3):
                        raise AssertionError("native comparison report gate was not released")
                return native_request(api_id, path, body, **kwargs)
            def native_denied(path, *args, **kwargs):
                if path == native_report:
                    native_report_writes.append(path)
                    raise native_report_error
                return native_original_open(path, *args, **kwargs)
            def native_load(code, observed_at):
                native_loads.append((code, observed_at))
                # The real report worker has failed, but primary enrichment is
                # still valid; this accepted comparison stays in the owned RAM.
                value = native_client.request("ka10045", "/fixture", {"n": 3})["value"]
                return {**native_investor, "value": value}
            def native_save_with_ack_loss(owner, value, now=None, *, account_scope=None):
                result = native_save(owner, value, now, account_scope=account_scope)
                native_saves.append((value, account_scope))
                if value.capture_state == "realtime_enriched":
                    if not native_lost_ack:
                        native_lost_ack.append(value)
                        native_committed.set()
                        if not native_ack_resume.wait(3):
                            raise AssertionError("native entry ACK-loss gate was not released")
                        raise native_ack_error
                    native_retry_committed.set()
                    if not native_retry_resume.wait(3):
                        raise AssertionError("native entry retry ACK gate was not released")
                return result
            native_controller = self.controller(native_events, journal_database_path=native_journal,
                entry_investor_loader=native_load)
            native_writer = native_controller.entry_snapshot_writer
            native_writer.failed.connect(native_failures.append)
            with patch("kiwoom_monitor.infrastructure.kiwoom_rest.validation_client.threading.Thread", side_effect=native_thread_factory):
                native_client = ParallelValidationClient(SimpleNamespace(request_with_continuation=native_request),
                    SimpleNamespace(request_with_continuation=native_local), native_report)
            native_original_close = native_client.close
            def native_close():
                native_close_observations.append((native_writer.wait(0), native_writer.has_pending_work(),
                    QThread.currentThread(), tuple(native_saves)))
                return native_original_close()
            native_client.close = native_close
            native_controller.market_data_client = native_client
            native_controller._owned_observers = (native_client,)
            try:
                with patch.object(Path, "open", native_denied), \
                        patch.object(JournalRepository, "save_entry_snapshot", autospec=True, side_effect=native_save_with_ack_loss), \
                        patch("kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer.news_at_execution",
                              return_value=native_news) as native_news_lookup:
                    self.assertEqual(1, native_client.request("ka10001", "/fixture", {"n": 1})["value"])
                    self.assertTrue(native_local_entered.wait(2))
                    self.assertEqual(2, native_client.request("ka10080", "/fixture", {"n": 2})["value"])
                    native_local_resume.set()
                    self.assertEqual(1, len(native_threads))
                    native_threads[0].join(3)
                    self.assertFalse(native_threads[0].is_alive())
                    self.assertEqual([native_report], native_report_writes)
                    self.assertFalse(native_report.exists())
                    native_writer.enqueue(native_snapshot)
                    native_controller.start_entry_snapshot_writer()
                    self.assertTrue(native_committed.wait(2))
                    self.assertTrue(native_writer.isRunning())
                    self.assertFalse(native_writer.wait(0))
                    self.assertFalse(native_controller.request_close())
                    self.assertEqual([], native_close_observations)
                    self.assertEqual([], native_controller.findChildren(SettingsRequestWorker))
                    self.assertIs(native_client, native_controller.market_data_client)
                    self.assertNotIn("ready", native_events)
                    # The actual row exists despite the caller's missing ACK.
                    native_stored = native_repository.load_entry_snapshots(
                        "005930", native_at, native_at + timedelta(seconds=1), native_scope)[0]
                    self.assertEqual(native_news, native_stored.news)
                    self.assertEqual(native_expected.investor_flow, native_stored.investor_flow)
                    native_ack_resume.set()
                    self.assertTrue(native_writer.wait(4000))
                    self.wait_until(lambda: native_controller._shutdown_stage == "cache_failed")
                    self.assertTrue(any(str(native_ack_error) in message for message in native_failures))
                    self.assertTrue(native_writer.has_pending_work())
                    self.assertEqual(native_expected, native_writer._pending_job)
                    self.assertTrue(native_writer._pending_enriched)
                    self.assertIs(native_client, native_controller.market_data_client)
                    self.assertEqual([], native_close_observations)
                    self.assertEqual([], native_controller.findChildren(SettingsRequestWorker))
                    self.assertNotIn("auxiliaries", native_events)
                    self.assertNotIn("ready", native_events)
                    with native_writer.replace_sources(investor_loader=None, program_loader=None) as native_source_release:
                        pass
                    self.assertTrue(native_source_release.released)
                    self.assertFalse(native_controller.request_close())
                    self.assertTrue(native_retry_committed.wait(2))
                    self.assertTrue(native_writer.isRunning())
                    self.assertFalse(native_writer.wait(0))
                    self.assertTrue(native_writer.has_pending_work())
                    native_controller._advance_shutdown()
                    self.assertEqual([], native_close_observations, "observer close preceded the real retry native ACK")
                    self.assertEqual([], native_controller.findChildren(SettingsRequestWorker))
                    self.assertIs(native_client, native_controller.market_data_client)
                    native_retry_resume.set()
                    self.assertTrue(native_writer.wait(4000))
                    self.assertFalse(native_writer.has_pending_work())
                    native_stored = native_repository.load_entry_snapshots(
                        "005930", native_at, native_at + timedelta(seconds=1), native_scope)[0]
                    self.assertEqual(native_snapshot.execution_key, native_stored.execution_key)
                    self.assertEqual(native_scope, native_stored.origin_scope)
                    self.assertEqual(native_snapshot.market_state, native_stored.market_state)
                    self.assertEqual(native_snapshot.orderbook, native_stored.orderbook)
                    self.assertEqual(native_news, native_stored.news)
                    self.assertEqual(native_expected.investor_flow, native_stored.investor_flow)
                    with closing(sqlite3.connect(native_journal)) as native_connection:
                        self.assertEqual(1, native_connection.execute(
                            "SELECT COUNT(*) FROM trade_entry_snapshots").fetchone()[0])
                    self.wait_until(lambda: native_controller._shutdown_stage == "ready")
                    self.assertEqual([(native_snapshot, native_scope), (native_expected, native_scope),
                                      (native_expected, native_scope)], native_saves)
                    self.assertEqual([("005930", native_at)], native_loads)
                    self.assertEqual(1, native_news_lookup.call_count)
                    self.assertEqual(1, len(native_close_observations), "native current observer must close exactly once")
                    native_joined, native_pending, native_close_thread, native_saved_at_close = native_close_observations[0]
                    self.assertIs(True, native_joined)
                    self.assertIs(False, native_pending)
                    self.assertIsNot(native_close_thread, native_controller.thread())
                    self.assertEqual(tuple(native_saves), native_saved_at_close)
                    self.assertIsNone(native_controller.market_data_client)
                    self.assertEqual(1, len(native_controller._api_client_retirements))
                    native_retirement = native_controller._api_client_retirements[0]
                    self.assertIs(native_client, native_retirement.client)
                    self.assertIs(native_report_error, native_retirement.result.error)
                    self.assertIsNot(native_ack_error, native_retirement.result.error)
                    self.assertEqual("close", native_retirement.result.error_stage)
                    self.assertIsNone(native_retirement.result.observation_error)
                    self.assertIs(True, native_retirement.native_result)
                    self.assertTrue(native_retirement.result.lifecycle.admission_closed)
                    self.assertEqual(0, native_retirement.result.lifecycle.active_requests)
                    self.assertTrue(native_retirement.result.lifecycle.worker_stopped)
                    self.assertEqual("report", native_retirement.result.lifecycle.failure_stage)
                    native_job, native_result, native_local_error = native_client._failed_job
                    self.assertEqual(("ka10001", {"n": 1}), (native_job[0], native_job[2]))
                    self.assertEqual(({"revision_id": "ka10001", "value": 1}, False, ""), native_result)
                    self.assertEqual("", native_local_error)
                    with native_client._jobs.mutex:
                        native_remaining = tuple(native_client._jobs.queue)
                    self.assertEqual([("ka10080", {"n": 2}), ("ka10045", {"n": 3})],
                                     [(job[0], job[2]) for job in native_remaining])
                    self.assertEqual([("ka10001", {"n": 1})], native_local_calls)
                    self.assertEqual([native_report], native_report_writes)
                    self.assertFalse(native_report.exists())
                    self.assertTrue(native_controller.request_close())
                    self.assertEqual(1, len(native_close_observations))
            finally:
                native_local_resume.set()
                native_ack_resume.set()
                native_retry_resume.set()
                native_writer.requestInterruption()
                self.assertTrue(native_writer.wait(4000))
                try:
                    native_original_close()
                except PermissionError as native_cleanup_error:
                    self.assertIs(native_report_error, native_cleanup_error)
                for native_thread in native_threads:
                    native_thread.join(3)
                    self.assertFalse(native_thread.is_alive())
                self._drain_api_retirement_fixture(native_controller)

    def test_retirement_preserves_unknown_fact_wait_and_worker_start_outcomes(self):
        from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter, SourceRelease
        for mode in ("missing", "read_error", "unknown_stage", "active", "alive", "wait_error", "start_error", "started_then_error", "native_join_error"):
            with self.subTest(mode=mode):
                sentinel = OSError("fixture " + mode)
                old, calls, closed = self._api_retirement_probe(
                    fact_mode=mode, fact_error=sentinel,
                )
                controller = self.controller([], market_data_client=old, owned_observers=(old,),
                    api_runtime_factory=lambda: {"market_data_client": object()})
                writer = EntrySnapshotWriter(Path("unused-retirement.sqlite3"))
                writer.setParent(controller)
                controller.entry_snapshot_writer = writer
                controller.configure_runtime(AppRuntimeActions(apply_runtime=lambda _: None))
                controller._api_reloading = True
                controller._api_reload_stage = "signals"
                original_start = SettingsRequestWorker.start
                original_release = SettingsRequestWorker.release_after_join
                def start(worker):
                    if mode == "start_error":
                        raise sentinel
                    original_start(worker)
                    if mode == "started_then_error":
                        raise sentinel
                def release_fixture():
                    controller._shutdown_timer.stop()
                    for item in getattr(controller, "_api_client_retirements", ()):
                        if item.worker is not None:
                            self.assertTrue(item.worker.wait(4000))
                            self.assertTrue(original_release(item.worker))
                with closing(SimpleNamespace(close=release_fixture)), \
                        patch.object(controller, "start_initial_ranking"), \
                        patch.object(SettingsRequestWorker, "start", start), \
                        (patch.object(SettingsRequestWorker, "release_after_join", side_effect=sentinel)
                         if mode == "native_join_error" else nullcontext()), \
                        (patch.object(SourceRelease, "wait", side_effect=sentinel) if mode == "wait_error" else nullcontext()):
                    controller._advance_api_reload()
                    self.wait_until(lambda: bool(controller._api_client_retirements) and
                        controller._api_client_retirements[0].result is not None)
                    controller.request_close()
                    if mode == "native_join_error":
                        self.wait_until(lambda: controller._api_client_retirements[0].native_error is sentinel)
                    else:
                        self.wait_until(lambda: controller._api_client_retirements[0].worker is None)
                    retirement = controller._api_client_retirements[0]
                    result = retirement.result
                    self.assertIs(old, retirement.client)
                    if mode == "native_join_error":
                        self.assertIs(sentinel, retirement.native_error)
                        self.assertIs(False, retirement.native_result)
                        self.assertIsNotNone(retirement.worker)
                    else:
                        self.assertIs(True, retirement.native_result)
                    if mode in {"start_error", "started_then_error"}:
                        self.assertIs(sentinel, retirement.start_error)
                    if mode == "read_error":
                        self.assertIs(sentinel, result.observation_error)
                    elif mode == "missing":
                        self.assertIsInstance(result.observation_error, AttributeError)
                    elif mode == "wait_error":
                        self.assertIs(sentinel, result.error)
                        self.assertEqual("source_release", result.error_stage)
                    elif mode == "start_error":
                        self.assertIs(sentinel, result.error)
                        self.assertEqual("start", result.error_stage)
                    self.assertEqual(0 if mode in {"wait_error", "start_error"} else 1, len(calls))
                    if mode == "started_then_error":
                        self.wait_until(lambda: controller._shutdown_stage == "ready")
                        self.assertIs(sentinel, controller._api_client_retirements[0].start_error)
                    else:
                        self.wait_until(lambda: controller._shutdown_stage == "api_retirements")
                        self.assertNotEqual("ready", controller._shutdown_stage)
                        self.assertIs(old, controller._api_client_retirements[0].client)
                    controller._shutdown_timer.stop()
                if mode == "native_join_error":
                    # Fixture recovery used only its exact public worker APIs;
                    # it never changed the product's original UNKNOWN outcome.
                    self.assertIs(sentinel, retirement.native_error)

    def test_actual_retirement_consumer_keeps_old_candidate_reset_and_same_identity_distinct(self):
        from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
        for outcome in ("capture", "factory", "view", "restore", "reset", "same"):
            controller = None
            with self.subTest(outcome=outcome), closing(SimpleNamespace(
                    close=lambda: self._drain_api_retirement_fixture(controller))):
                old, old_calls, old_closed = self._api_retirement_probe()
                candidate, new_calls, new_closed = self._api_retirement_probe()
                if outcome == "same":
                    candidate, new_calls, new_closed = old, old_calls, old_closed
                sentinel = ValueError("fixture " + outcome)
                factory = Mock()
                runtime = {"market_data_client": candidate, "owned_observers": (candidate,), "fundamentals_worker_factory": factory}
                controller = self.controller([], market_data_client=old, owned_observers=(old,), api_runtime_factory=lambda: runtime)
                writer = EntrySnapshotWriter(Path("unused-retirement.sqlite3"))
                writer.setParent(controller)
                controller.entry_snapshot_writer = writer
                def capture():
                    if outcome == "capture":
                        raise sentinel
                    def restore():
                        if outcome == "restore":
                            raise OSError("fixture restore")
                    return restore
                def apply(_runtime):
                    if outcome in {"view", "restore"}:
                        raise sentinel
                original_set = controller.fundamentals.set_factory
                def set_factory(value):
                    if outcome == "factory" and value is factory:
                        raise sentinel
                    original_set(value)
                controller.configure_runtime(AppRuntimeActions(apply_runtime=apply, capture_state=capture))
                errors = []
                controller.api_reload_failed.connect(errors.append)
                controller._api_reloading = True
                controller._api_reload_stage = "signals"
                with patch.object(controller, "start_initial_ranking"), \
                        patch.object(controller.fundamentals, "set_factory", side_effect=set_factory), \
                        patch.object(controller.realtime_subscription, "reset", side_effect=sentinel if outcome == "reset" else None):
                    controller._advance_api_reload()
                    if outcome == "same":
                        self.assertEqual([], controller._api_client_retirements)
                    elif outcome == "restore":
                        self.assertEqual(1, len(controller._api_client_retirements))
                        self.assertFalse(controller._api_client_retirements[0].runtime_detached)
                        self.assertFalse(new_closed.is_set())
                    else:
                        target_closed = old_closed if outcome == "reset" else new_closed
                        self.wait_until(target_closed.is_set)
                self.assertIs(candidate if outcome in {"reset", "same"} else old, controller.market_data_client)
                self.assertEqual(1 if outcome == "reset" else 0, len(old_calls))
                self.assertEqual(1 if outcome in {"capture", "factory", "view"} else 0, len(new_calls))
                if outcome == "restore":
                    self.assertEqual(1, len(errors))
                    self.assertIn(str(sentinel), errors[0])
                    self.assertIn("fixture restore", errors[0])
                else:
                    self.assertEqual([] if outcome == "same" else [str(sentinel)], errors)
                controller.request_close()
                self.wait_until(lambda: controller._shutdown_stage == "ready")
                self.assertEqual([], controller._api_client_retirements)
                self.assertEqual(1, len(old_calls))
                self.assertEqual(1, len(new_calls))

    def test_runtime_reload_waits_for_old_signals_then_replaces_resources_and_resets_view(self):
        from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = temporary.name
        database = Database(Path(directory) / "monitor.sqlite3")
        database.initialize()
        old_loader, new_loader = object(), object()
        old_client, new_client = object(), object()
        factories = {name: Mock() for name in (
            "realtime_worker_factory", "minute_history_worker_factory",
            "fundamentals_worker_factory", "daily_high_worker_factory",
            "historical_high_worker_factory", "nxt_eligibility_worker_factory",
        )}
        investor_loader, program_loader = Mock(), Mock()
        runtime = {**factories, "ranking_loader": new_loader,
                   "market_data_client": new_client, "source_mode": "local",
                   "entry_investor_loader": investor_loader,
                   "program_trade_loader": program_loader}
        factory = Mock(return_value=runtime)
        with patch.object(MainWindow, "_refresh_rankings") as refresh, \
                patch.object(AppController, "schedule_next_ranking_refresh"), \
                patch.object(AppController, "schedule_realtime_session_refresh"), \
                patch.object(MainWindow, "_restore_environment_selector"), \
                patch("kiwoom_monitor.presentation.main_window.QMessageBox.warning") as warning:
            window = MainWindow(database.settings, ranking_loader=old_loader,
                                market_data_client=old_client, api_runtime_factory=factory)
            window.activate_startup()
            self.windows.append(window)
            controller = window._app_controller
            writer = EntrySnapshotWriter(Path(directory) / "journal.sqlite3")
            controller.entry_snapshot_writer = writer
            self.enterContext(patch.object(writer, "replace_sources", wraps=writer.replace_sources))
            window._market_cap_reference_codes.add("005930")
            window._market_cap_reference_pending.add("005930")
            window._realtime_market_caps["005930"] = 100
            window._pc_market_session.history_cache_saved("005930")
            old_aggregator = window._pc_market_session.minute_aggregator
            initial_rows = window._table.rowCount()
            producer = DelayedRankingProducer(controller.ranking)
            controller.ranking._worker = producer
            self.threads.append(producer)
            observed = []
            producer.completed.connect(controller.ranking.completed.emit)
            producer.completed.connect(lambda _: observed.append(
                (controller.api_reloading, controller.market_data_client)))
            producer.start()
            self.assertTrue(producer.started_run.wait(1))
            controller.restart_for_api_settings()
            controller.restart_for_api_settings()
            controller._advance_api_reload()
            factory.assert_not_called()
            self.assertTrue(window._api_reloading)
            self.assertTrue(window._ranking_execution.priority_preparing)
            producer.release.set()
            producer.wait(1000)
            controller._advance_api_reload()
            factory.assert_not_called()
            self.wait_until(lambda: not controller.api_reloading and refresh.called)
            self.assertEqual([(True, old_client)], observed)
            factory.assert_called_once()
            self.assertIs(new_loader, window._ranking_loader)
            self.assertIs(new_client, window._market_data_client)
            self.assertEqual("local", window._pc_market_session.source_route)
            self.assertEqual(initial_rows, window._table.rowCount())
            self.assertFalse(window._ranking_execution.priority_preparing)
            self.assertIsNot(old_aggregator, window._pc_market_session.minute_aggregator)
            self.assertFalse(window._market_cap_reference_codes)
            self.assertFalse(window._market_cap_reference_pending)
            self.assertFalse(window._realtime_market_caps)
            self.assertFalse(window._pc_market_session.history_codes)
            for name, feature in (
                ("realtime_worker_factory", controller.realtime),
                ("minute_history_worker_factory", controller.minute_history),
                ("fundamentals_worker_factory", controller.fundamentals),
                ("daily_high_worker_factory", controller.daily_high),
                ("historical_high_worker_factory", controller.historical_high),
                ("nxt_eligibility_worker_factory", controller.nxt_eligibility),
            ):
                self.assertIs(factories[name], feature._worker_factory)
            controller.entry_snapshot_writer.replace_sources.assert_called_once_with(
                investor_loader=investor_loader, program_loader=program_loader)
            warning.assert_not_called()
            self.assertNotIn("_api_reloading", window.__dict__)
            self.assertNotIn("_ranking_loader", window.__dict__)
            self.assertNotIn("_market_data_client", window.__dict__)
            warning.assert_not_called()

    def test_runtime_factory_failure_keeps_old_resources_and_ends_reload(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = temporary.name
        database = Database(Path(directory) / "monitor.sqlite3")
        database.initialize()
        old_loader, old_client = object(), object()
        factory = Mock(side_effect=ValueError("runtime construction failed"))
        with patch.object(MainWindow, "_refresh_rankings") as refresh, \
                patch.object(AppController, "schedule_next_ranking_refresh"), \
                patch.object(AppController, "schedule_realtime_session_refresh"), \
                patch("kiwoom_monitor.presentation.main_window.QMessageBox.warning") as warning:
            window = MainWindow(database.settings, ranking_loader=old_loader,
                                market_data_client=old_client, api_runtime_factory=factory)
            window.activate_startup()
            self.windows.append(window)
            window._app_controller.restart_for_api_settings()
            self.wait_until(lambda: warning.called)
            self.assertFalse(window._api_reloading)
            self.assertFalse(window._ranking_execution.priority_preparing)
            self.assertIs(old_loader, window._ranking_loader)
            self.assertIs(old_client, window._market_data_client)
            refresh.assert_not_called()
            factory.assert_called_once()

    def test_partial_runtime_failure_restores_controller_factories_and_view(self):
        from dataclasses import replace
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        database = Database(Path(temporary.name) / 'monitor.sqlite3'); database.initialize()
        for boundary in ('worker_factory', 'view'):
            with self.subTest(boundary=boundary):
                old_loader, old_client = SimpleNamespace(server_now=lambda: datetime(2035,1,2,10,0)), object()
                runtime = {'ranking_loader':SimpleNamespace(server_now=lambda: datetime(2036,1,2,10,0)), 'market_data_client':object(),
                           'realtime_worker_factory':lambda codes: None, 'fundamentals_worker_factory':lambda codes:None, 'source_mode':'local'}
                with patch.object(AppController, 'schedule_next_ranking_refresh'), \
                     patch.object(AppController, 'schedule_realtime_session_refresh'), \
                     patch('kiwoom_monitor.presentation.main_window.QMessageBox.warning'):
                    minute_repository = MinuteBarRepository(database.settings.database_path)
                    window = MainWindow(database.settings, ranking_loader=old_loader, market_data_client=old_client, minute_bar_repository=minute_repository,
                                        api_runtime_factory=lambda: runtime)
                    window.activate_startup()
                    self.windows.append(window)
                    controller=window._app_controller
                    controller._initial_ranking_timer.stop()
                    controller._api_reloading=True; controller._api_reload_stage='signals'
                    old_factory=controller.realtime._worker_factory
                    old_aggregator=window._pc_market_session.minute_aggregator
                    window._realtime_market_caps['005930']=123.
                    original_apply=controller._runtime_actions.apply_runtime
                    def fail_view(value):
                        original_apply(value)
                        raise RuntimeError('injected-view-failure')
                    setter=controller.fundamentals.set_factory
                    def fail_factory(value):
                        if value is runtime.get('fundamentals_worker_factory'):
                            raise RuntimeError('injected-factory-failure')
                        setter(value)
                    if boundary=='view':
                        controller._runtime_actions=replace(controller._runtime_actions, apply_runtime=fail_view)
                        controller._advance_api_reload()
                    else:
                        with patch.object(controller.fundamentals,'set_factory',side_effect=fail_factory):
                            controller._advance_api_reload()
                    self.assertIs(old_loader, controller.ranking_loader)
                    self.assertIs(old_client, controller.market_data_client)
                    self.assertIs(old_factory, controller.realtime._worker_factory)
                    self.assertEqual({'005930':123.},window._realtime_market_caps)
                    self.assertIs(old_aggregator,window._pc_market_session.minute_aggregator)
                    self.assertEqual(2035, minute_repository._now().year)
                    self.assertFalse(controller.api_reloading)
                    window.close()

    def test_runtime_restore_failure_keeps_original_error_and_attempts_other_restore(self):
        controller=self.controller([],ranking_loader=object(), market_data_client=object())
        old_factory=controller.realtime._worker_factory
        restored=[]
        def fail_apply(runtime):
            raise ValueError('original-view-error')
        def fail_restore():
            restored.append('view')
            raise OSError('view-restore-error')
        actions=AppRuntimeActions(apply_runtime=fail_apply,capture_state=lambda: fail_restore)
        setter=controller.realtime.set_factory
        new_factory=lambda codes:None
        def fail_factory_restore(factory):
            if factory is old_factory:
                raise OSError('factory-restore-error')
            setter(factory)
        with patch.object(controller.realtime,'set_factory',side_effect=fail_factory_restore):
            with self.assertRaisesRegex(RuntimeError,'original-view-error.*factory-restore-error.*view-restore-error'):
                controller._apply_api_runtime({'ranking_loader':object(),'realtime_worker_factory':new_factory},actions)
        self.assertEqual(['view'],restored)

    def test_close_cancels_runtime_replacement_and_queued_initial_ranking(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = temporary.name
        database = Database(Path(directory) / "monitor.sqlite3")
        database.initialize()
        factory = Mock()
        with patch.object(MainWindow, "_refresh_rankings") as refresh, \
                patch.object(AppController, "schedule_next_ranking_refresh"), \
                patch.object(AppController, "schedule_realtime_session_refresh"):
            window = MainWindow(database.settings, ranking_loader=object(),
                                api_runtime_factory=factory)
            window.activate_startup()
            self.windows.append(window)
            window._app_controller.restart_for_api_settings()
            self.assertTrue(window.close())
            window._app_controller._advance_api_reload()
            self.app.processEvents()
            factory.assert_not_called()
            refresh.assert_not_called()
            self.assertFalse(window._app_controller._api_reload_timer.isActive())
            self.assertFalse(window._app_controller._initial_ranking_timer.isActive())

    def test_nxt_finished_order_and_realtime_callbacks_keep_current_receivers(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "monitor.sqlite3")
            database.initialize()
            events = []
            with patch.object(MainWindow, "_open_api_settings"), \
                    patch.object(MainWindow, "_start_daily_krx_catalog_sync", side_effect=lambda: events.append("catalog")), \
                    patch.object(AppController, "start_realtime_subscription", side_effect=lambda codes: events.append(("subscribe", codes))), \
                    patch.object(AppController, "start_realtime_followups", side_effect=lambda codes: events.append(("followups", codes))):
                window = MainWindow(database.settings)
                window.activate_startup()
                self.windows.append(window)
                window._row_by_code = {"005930": 0}
                window._app_controller.ranked_codes = ("005930",)
                window._app_controller.nxt_eligibility.finished.emit()
                self.assertEqual(["catalog", ("subscribe", ("005930",)),
                                  ("followups", ("005930",))], events)
                window._pc_market_session.minute_aggregator.reset_cumulative_baselines = lambda codes: events.append(("reset", codes))
                window._app_controller.realtime.connection_opened.emit(("005930",))
                window._app_controller.realtime.codes_added.emit(("000660",))
                self.assertEqual([("reset", ("005930",)), ("reset", ("000660",))], events[-2:])
                window.close()
                QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)


    def test_bootstrap_runtime_reads_one_encrypted_profile_per_build_and_reloads_environment(self):
        from kiwoom_monitor.infrastructure.kiwoom_rest import local_config
        from kiwoom_monitor.infrastructure.kiwoom_rest.client_factory import create_query_client

        original_load = local_config.LocalApiConfig.load
        original_read = Path.read_text
        reads, observed_counts, environments = [], [], []
        profiles = local_config.ApiProfiles("mock-app", "mock-secret", "real-app", "real-secret", "mock")

        def read(path, *args, **kwargs):
            if path.name == "api.env":
                reads.append(path)
            return original_read(path, *args, **kwargs)

        def setup(fixture):
            local_config.LocalApiConfig(fixture.paths.data_dir / "api.env").save_profiles(profiles)

        def capture(controller):
            client = controller.market_data_client
            worker = controller.realtime._worker_factory(("005930",))
            worker.setParent(controller.realtime)
            self.assertIs(client, worker._token_provider.__self__)
            self.assertIs(client, worker._now_provider.__self__)
            self.assertEqual(client.environment, worker._environment)
            environments.append(worker._environment)
            observed_counts.append((load.call_count, decrypt.call_count, len(reads)))

        def during_loop(fixture):
            controller = fixture.window._app_controller
            capture(controller)
            local_config.LocalApiConfig(fixture.paths.data_dir / "api.env").save_profiles(
                local_config.ApiProfiles("mock-app", "mock-secret", "real-app", "real-secret", "real")
            )
            controller.restart_for_api_settings()
            controller._advance_api_reload()
            controller._advance_api_reload()
            self.assertFalse(controller.api_reloading)
            capture(controller)

        with patch.object(local_config.LocalApiConfig, "load", autospec=True, side_effect=original_load) as load, \
                patch.object(local_config, "_unprotect", wraps=local_config._unprotect) as decrypt, \
                patch.object(Path, "read_text", autospec=True, side_effect=read), \
                patch.object(MainWindow, "_refresh_rankings"), \
                patch.object(MainWindow, "_restore_environment_selector"), \
                patch.object(AppController, "schedule_next_ranking_refresh"), \
                patch.object(AppController, "schedule_realtime_session_refresh"):
            self._bootstrap_settings_desktop(
                during_loop, source_mode="local", before_run=setup, cleanup_window=True,
                query_client_factory=create_query_client,
            )
        self.assertEqual(["mock", "real"], environments)
        self.assertEqual([(1, 1, 1), (2, 2, 2)], observed_counts)

    def test_bootstrap_unpublished_parallel_client_is_joined_before_factory_failure_returns(self):
        import threading
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
        from kiwoom_monitor.infrastructure.kiwoom_rest import validation_client
        from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient
        from kiwoom_monitor.infrastructure.kiwoom_rest.client_factory import create_query_client
        from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient
        from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings

        for cleanup_fails in (False, True):
            with self.subTest(cleanup_fails=cleanup_fails):
                assembly_error = RuntimeError("fixture recorder construction failure")
                cleanup_error = PermissionError("fixture close error after actual stop")
                now = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
                old_close = Mock()
                old_client = SimpleNamespace(server_now=lambda: now)
                clients, closes = [], []
                remote_transport = Mock(side_effect=AssertionError("unexpected NAS transport"))

                def factory(*args, **kwargs):
                    if not kwargs["source_settings"].parallel_validation_enabled:
                        return old_client
                    previous = set(threading.enumerate())
                    client = create_query_client(*args, **kwargs)
                    self.assertIsInstance(client, validation_client.ParallelValidationClient)
                    # Document reads have a separate guard below. Direct query
                    # POST also calls this actual instance transport, so guard
                    # the common opener before the factory can use the client.
                    client._primary._opener = remote_transport
                    created = [thread for thread in threading.enumerate()
                               if thread not in previous and thread.name == "central-local-validation"]
                    self.assertEqual(1, len(created))
                    thread = created[0]
                    self.assertTrue(thread.is_alive())
                    original_close = client.close
                    clients.append((client, thread, original_close))

                    def close():
                        closes.append((client, threading.get_ident(), thread.is_alive()))
                        original_close()
                        self.assertFalse(thread.is_alive())
                        if cleanup_fails:
                            raise cleanup_error

                    client.close = close
                    return client

                def during_loop(fixture):
                    controller = fixture.window._app_controller
                    self.assertIs(old_client, controller.market_data_client)
                    bootstrap.DataSourceConfig.load.return_value = DataSourceSettings(
                        "personal_server", "https://fixture.invalid", "fixture", False, True,
                    )
                    old_client.close = old_close
                    try:
                        with patch.object(validation_client, "RealtimeValidationRecorder",
                                          side_effect=assembly_error) as recorder:
                            with self.assertRaises(RuntimeError) as caught:
                                # The exact nested bootstrap factory passed to the
                                # controller is the entry; it never returns a dict.
                                controller._api_runtime_factory()
                        self.assertIs(assembly_error, caught.exception)
                        if cleanup_fails:
                            self.assertIs(cleanup_error, caught.exception.__cause__)
                        self.assertEqual(1, recorder.call_count)
                        self.assertEqual(1, len(clients))
                        client, thread, _close = clients[0]
                        self.assertFalse(thread.is_alive(), "unpublished client outlived its failed factory")
                        self.assertEqual([(client, threading.get_ident(), True)], closes)
                        self.assertEqual(0, client.validation_summary()["total_observations"])
                        self.assertIs(old_client, controller.market_data_client)
                        old_close.assert_not_called()
                    finally:
                        # Keep the established non-observer bootstrap fixture
                        # unchanged for the separate app-exit/C owner contract.
                        del old_client.close

                try:
                    with patch.object(bootstrap.LocalApiConfig, "load", return_value=KiwoomSettings(
                            "fixture-key", "fixture-secret", "mock")), \
                            patch.object(KiwoomRestClient, "_post", side_effect=AssertionError("unexpected Kiwoom HTTP")) as local_http, \
                            patch.object(RemoteKiwoomRestClient, "_read_central_document", side_effect=AssertionError("unexpected NAS HTTP")) as remote_http, \
                            patch.object(MainWindow, "_refresh_rankings"), \
                            patch.object(MainWindow, "_restore_environment_selector"), \
                            patch.object(AppController, "schedule_next_ranking_refresh"), \
                            patch.object(AppController, "schedule_realtime_session_refresh"):
                        self._bootstrap_settings_desktop(
                            during_loop, source_mode="local", cleanup_window=True,
                            query_client_factory=factory,
                        )
                        local_http.assert_not_called()
                        remote_http.assert_not_called()
                        remote_transport.assert_not_called()
                finally:
                    # Baseline has public close but omits this factory call.
                    # Observe the live-thread failure above, then clean that
                    # exact idle client before the owned report TEMP is removed.
                    for _client, thread, original_close in clients:
                        original_close()
                        thread.join(3)
                        self.assertFalse(thread.is_alive())


    def _observer_owner_real_pair(self, directory, prefix):
        import threading
        from kiwoom_monitor.infrastructure.kiwoom_rest.validation_client import (
            ParallelValidationClient, RealtimeValidationRecorder,
        )
        threads = []
        original_thread = threading.Thread
        def create_thread(*args, **kwargs):
            thread = original_thread(*args, **kwargs)
            if kwargs.get("name") in {"central-local-validation", "realtime-validation-writer"}:
                threads.append(thread)
            return thread
        def request(*args, **kwargs):
            raise AssertionError("idle observer owner fixture must not query")
        acquired_closes = []
        try:
            with patch("kiwoom_monitor.infrastructure.kiwoom_rest.validation_client.threading.Thread",
                       side_effect=create_thread):
                rest = ParallelValidationClient(SimpleNamespace(request_with_continuation=request),
                    SimpleNamespace(request_with_continuation=request), Path(directory) / (prefix + "-rest.jsonl"))
                acquired_closes.append(rest.close)
                recorder = RealtimeValidationRecorder(Path(directory) / (prefix + "-realtime.jsonl"))
                acquired_closes.append(recorder.close)
            self.assertEqual(["central-local-validation", "realtime-validation-writer"], [t.name for t in threads])
            self.assertTrue(all(t.is_alive() for t in threads))
            return rest, recorder, threads, tuple(acquired_closes)
        except BaseException:
            # The helper has not transferred its exact public handles yet.
            # Attempt every acquired close before joining the observed daemons.
            for original_close in acquired_closes:
                try:
                    original_close()
                except BaseException:
                    pass
            for thread in threads:
                if thread.ident is not None:
                    thread.join(3)
            raise

    def test_observer_owner_actual_window_receives_initial_inline_error_without_reclose_or_fake_native_join(self):
        import threading
        with tempfile.TemporaryDirectory() as directory:
            rest, recorder, threads, original_closes = self._observer_owner_real_pair(directory, "initial")
            try:
                attempts = []
                close_error = PermissionError("inline wrapper failed after actual public close")
                assembly_error = ValueError("initial runtime assembly failed")
                cleanup = []
                for observer, original_close in zip((rest, recorder), original_closes):
                    caught_error = None
                    try:
                        attempts.append((observer, QThread.currentThread()))
                        original_close()
                        if observer is rest:
                            raise close_error
                    except PermissionError as error:
                        caught_error = error
                    cleanup.append((observer, caught_error, observer.comparison_lifecycle_state(), None))
                payload = tuple(cleanup)
                assembly_error.api_runtime_observer_cleanup = payload
                assembly_error.__cause__ = close_error
                for thread in threads:
                    thread.join(3)
                    self.assertFalse(thread.is_alive())
                self.assertIsNone(cleanup[0][2].failure_stage,
                                  "the wrapper error is independent of the actual producer's failure stage")
                rest.close = Mock(side_effect=AssertionError("already attempted REST close repeated"))
                recorder.close = Mock(side_effect=AssertionError("already attempted Recorder close repeated"))
                database = Database(Path(directory) / "window.sqlite3")
                database.initialize()
                borrowed = SimpleNamespace(close=Mock())
                current_rest, rest_calls, _ = self._api_retirement_probe()
                current_recorder, recorder_calls, _ = self._api_retirement_probe()
                with patch.object(MainWindow, "_open_api_settings"), \
                        patch.object(MainWindow, "_refresh_rankings"), \
                        patch.object(MainWindow, "_restore_environment_selector"), \
                        patch.object(AppController, "schedule_next_ranking_refresh"), \
                        patch.object(AppController, "schedule_realtime_session_refresh"):
                    # Execute the real constructor and its real AppController receiver.
                    window = MainWindow(database.settings, market_data_client=borrowed,
                        owned_observers=(current_rest, current_recorder, current_rest),
                        unpublished_observer_error=assembly_error)
                    window.activate_startup()
                    self.windows.append(window)
                    controller = window._app_controller
                    self.assertEqual((current_rest, current_recorder), controller._owned_observers)
                    self.assertIs(borrowed, controller.market_data_client)
                    self.assertEqual(1, len(controller._api_client_retirements))
                    retained = controller._api_client_retirements[0]
                    self.assertIs(rest, retained.client)
                    self.assertIs(assembly_error, retained.error)
                    self.assertIs(close_error, retained.result.error)
                    self.assertIs(cleanup[0][2], retained.result.lifecycle)
                    self.assertIsNone(retained.result.observation_error)
                    self.assertTrue(retained.inline_cleanup)
                    self.assertIsNone(retained.native_result, "there was no Qt worker/native join")
                    self.assertIsNone(retained.worker)
                    self.assertTrue(controller._api_client_stop_confirmed(retained))
                    self.assertEqual([], controller.findChildren(SettingsRequestWorker))
                    self.assertEqual([(rest, controller.thread()), (recorder, controller.thread())], attempts)
                    window.close()
                    self.wait_until(controller.request_close)
                    self.assertEqual("ready", controller._shutdown_stage)
                    self.assertEqual((1, 1), (len(rest_calls), len(recorder_calls)))
                    borrowed.close.assert_not_called()
                    rest.close.assert_not_called()
                    recorder.close.assert_not_called()
                    self.assertEqual(1, len(controller._api_client_retirements))
                    self.assertIs(retained, controller._api_client_retirements[0])
                    self.assertIs(payload, assembly_error.api_runtime_observer_cleanup)
                    self.assertEqual((rest, recorder), tuple(row[0] for row in assembly_error.api_runtime_observer_cleanup))
                    self.assertIs(close_error, assembly_error.__cause__)
                    self.assertTrue(window.close())
            finally:
                for original_close, thread in zip(original_closes, threads):
                    original_close()
                    thread.join(3)
                    self.assertFalse(thread.is_alive())

    def test_observer_owner_actual_reload_closes_recorder_after_realtime_fence_while_rest_waits_for_source(self):
        import threading
        from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
        source_entered, source_resume = Event(), Event()
        closed = (Event(), Event())
        close_threads, values, delivered, factory_signals = [], [], [], []
        with tempfile.TemporaryDirectory() as directory:
            rest, recorder, threads, original_closes = self._observer_owner_real_pair(directory, "reload")
            controller = producer = caller = None
            try:
                def old_source(code, observed):
                    source_entered.set()
                    if not source_resume.wait(3):
                        raise TimeoutError("observer tuple whole-source gate")
                    return {"available": True, "source": "retired"}
                def wrap_close(index):
                    def close():
                        close_threads.append((index, QThread.currentThread()))
                        original_closes[index]()
                        closed[index].set()
                    return close
                rest.close, recorder.close = wrap_close(0), wrap_close(1)
                runtime = {"market_data_client": object(), "owned_observers": (),
                           "entry_investor_loader": lambda *args: {"available": True, "source": "current"}}
                def build():
                    factory_signals.append(tuple(delivered))
                    return runtime
                factory = Mock(side_effect=build)
                # Install via the existing runtime entry so the baseline reaches the
                # actual reload/daemon behavior before any new API or field access.
                controller = self.controller([], api_runtime_factory=factory)
                writer = EntrySnapshotWriter(Path(directory) / "unused.sqlite3", investor_loader=old_source)
                writer.setParent(controller)
                controller.entry_snapshot_writer = writer
                controller.configure_runtime(AppRuntimeActions(apply_runtime=lambda _: None))
                controller._apply_api_runtime({"market_data_client": rest,
                    "owned_observers": (rest, recorder, rest), "entry_investor_loader": old_source},
                    AppRuntimeActions(apply_runtime=lambda _: None))
                producer = DelayedProducer(controller.realtime)
                self.threads.append(producer)
                controller.realtime._worker = producer
                producer.result.connect(lambda: delivered.append("last"), Qt.ConnectionType.QueuedConnection)
                producer.start()
                caller = threading.Thread(target=lambda: values.append(writer._investor_at_execution(
                    SimpleNamespace(stock_code="005930", executed_at=datetime(2026, 10, 20, 20, 5)))))
                caller.start()
                self.assertTrue(producer.started_run.wait(1))
                self.assertTrue(source_entered.wait(2))
                with patch.object(controller, "start_initial_ranking"):
                    controller.restart_for_api_settings()
                    controller._api_reload_timer.stop()
                    controller._advance_api_reload()
                    controller._api_reload_timer.stop()
                    factory.assert_not_called()
                    self.assertEqual([], close_threads)
                    producer.release.set()
                    self.assertTrue(producer.wait(4000))
                    controller._advance_api_reload()
                    self.assertEqual("signals", controller._api_reload_stage)
                    factory.assert_not_called()
                    self.assertEqual([], close_threads)
                    self.wait_until(lambda: factory.call_count == 1)
                # Behavior comes before any new retirement/public-fact reads.
                # MAIN150 closes the old REST port but leaves this real Recorder alive.
                threads[1].join(3)
                self.assertFalse(threads[1].is_alive(), "API replacement left its old Recorder daemon alive")
                self.assertTrue(closed[1].is_set())
                self.assertEqual([("last",)], factory_signals)
                self.assertTrue(caller.is_alive())
                self.assertTrue(threads[0].is_alive())
                self.assertFalse(closed[0].is_set())
                retained = next(item for item in controller._api_client_retirements if item.client is rest)
                self.assertIsNotNone(retained.source_release)
                self.assertFalse(retained.source_release.released)
                self.assertIs(runtime["market_data_client"], controller.market_data_client)
                self.assertEqual((), controller._owned_observers)
                source_resume.set()
                caller.join(3)
                self.assertFalse(caller.is_alive())
                self.assertEqual([{"available": True, "source": "retired"}], values)
                self.wait_until(closed[0].is_set)
                threads[0].join(3)
                self.assertFalse(threads[0].is_alive())
                self.assertTrue(all(thread is not controller.thread() for _, thread in close_threads))
                self.assertEqual([0, 1], sorted(index for index, _ in close_threads))
                controller.request_close()
                self.wait_until(lambda: controller._shutdown_stage == "ready")
                self.assertEqual([], controller._api_client_retirements)
            finally:
                try:
                    source_resume.set()
                    if producer is not None:
                        producer.release.set()
                        self.assertTrue(producer.wait(4000))
                    if caller is not None and caller.ident is not None:
                        caller.join(3)
                        self.assertFalse(caller.is_alive())
                finally:
                    try:
                        for close, thread in zip(original_closes, threads):
                            close()
                            thread.join(3)
                            self.assertFalse(thread.is_alive())
                    finally:
                        self._drain_api_retirement_fixture(controller)

    def test_observer_owner_tuple_swap_restores_all_identities_and_preserves_unknown_and_postcommit_cases(self):
        from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
        cases = (("success", "disjoint"), ("capture", "disjoint"), ("factory", "disjoint"),
                 ("view", "disjoint"), ("source_enter", "disjoint"), ("view_restore", "disjoint"),
                 ("factory_restore", "disjoint"), ("reset", "disjoint"),
                 ("success", "same_rest"), ("success", "same_recorder"), ("success", "same_both"))
        for outcome, alias in cases:
            controller = None
            with self.subTest(outcome=outcome, alias=alias), closing(SimpleNamespace(
                    close=lambda: self._drain_api_retirement_fixture(controller))):
                old_rest, old_rest_calls, _ = self._api_retirement_probe()
                old_recorder, old_recorder_calls, _ = self._api_retirement_probe()
                candidate, candidate_calls, _ = self._api_retirement_probe()
                recorder, recorder_calls, _ = self._api_retirement_probe()
                if alias in {"same_rest", "same_both"}:
                    candidate, candidate_calls = old_rest, old_rest_calls
                if alias in {"same_recorder", "same_both"}:
                    recorder, recorder_calls = old_recorder, old_recorder_calls
                old_tuple, candidate_tuple = (old_rest, old_recorder), (candidate, recorder)
                sentinel = ValueError("tuple " + outcome)
                runtime_factory = Mock()
                runtime = {"market_data_client": candidate,
                    "owned_observers": (candidate, recorder, candidate, recorder),
                    "fundamentals_worker_factory": runtime_factory}
                controller = self.controller([], market_data_client=old_rest, owned_observers=old_tuple,
                    api_runtime_factory=lambda: runtime)
                writer = EntrySnapshotWriter(Path("unused-observer-tuple.sqlite3"))
                writer.setParent(controller)
                controller.entry_snapshot_writer = writer
                old_factory = controller.fundamentals._worker_factory
                setter = controller.fundamentals.set_factory
                def set_factory(value):
                    if outcome == "factory" and value is runtime_factory:
                        raise sentinel
                    if outcome == "factory_restore" and value is old_factory:
                        raise OSError("tuple factory restore")
                    setter(value)
                def capture():
                    if outcome == "capture":
                        raise sentinel
                    def restore():
                        if outcome == "view_restore":
                            raise OSError("tuple view restore")
                    return restore
                def apply(_runtime):
                    self.assertIs(candidate, controller.market_data_client)
                    self.assertEqual(candidate_tuple, controller._owned_observers)
                    if outcome in {"view", "view_restore", "factory_restore"}:
                        raise sentinel
                controller.configure_runtime(AppRuntimeActions(apply_runtime=apply, capture_state=capture))
                errors = []
                controller.api_reload_failed.connect(errors.append)
                controller._api_reloading, controller._api_reload_stage = True, "signals"
                with patch.object(controller, "start_initial_ranking"), \
                        patch.object(controller.fundamentals, "set_factory", side_effect=set_factory), \
                        patch.object(controller.realtime_subscription, "reset", side_effect=sentinel if outcome == "reset" else None), \
                        (patch.object(writer, "replace_sources", side_effect=sentinel) if outcome == "source_enter" else nullcontext()):
                    controller._advance_api_reload()
                committed = outcome in {"success", "reset"}
                self.assertIs(candidate if committed else old_rest, controller.market_data_client)
                self.assertEqual(candidate_tuple if committed else old_tuple, controller._owned_observers)
                unknown = outcome in {"source_enter", "view_restore", "factory_restore"}
                target_tuple = old_tuple if committed else candidate_tuple
                protected = candidate_tuple if committed else old_tuple
                expected = tuple(value for value in target_tuple if all(value is not current for current in protected))
                records = tuple(controller._api_client_retirements)
                self.assertEqual(len(expected), len(records))
                self.assertEqual({id(value) for value in expected}, {id(item.client) for item in records})
                for item in records:
                    self.assertIs(not unknown, item.runtime_detached)
                    if item.client is not (old_rest if committed else candidate):
                        self.assertIsNone(item.source_release)
                    if item.client is (old_rest if committed else candidate) and outcome not in {"capture", "factory", "source_enter"}:
                        self.assertIsNotNone(item.source_release)
                        self.assertTrue(item.source_release.released)
                    if outcome == "reset":
                        self.assertIs(sentinel, item.error)
                    elif unknown:
                        self.assertIsNotNone(item.error)
                if unknown:
                    self.assertFalse(candidate_calls or recorder_calls or old_rest_calls or old_recorder_calls)
                    self.assertIs(sentinel, records[0].error if outcome == "source_enter" else records[0].error.__cause__)
                else:
                    self.wait_until(lambda: all(len(calls) == (1 if any(value is target for target in expected) else 0)
                        for value, calls in ((old_rest, old_rest_calls), (old_recorder, old_recorder_calls),
                                             (candidate, candidate_calls), (recorder, recorder_calls))))
                self.assertEqual(0 if outcome == "success" else 1, len(errors))
                controller.request_close()
                self.wait_until(lambda: controller._shutdown_stage == "ready")
                for value, calls in ((old_rest, old_rest_calls), (old_recorder, old_recorder_calls),
                                     (candidate, candidate_calls), (recorder, recorder_calls)):
                    self.assertEqual(1, len(calls), "each distinct owned observer closes exactly once")
                self.assertEqual((), controller._owned_observers)
                self.assertEqual([], controller._api_client_retirements)

    def test_observer_owner_inline_results_keep_unknown_facts_and_do_not_widen_qt_error_stage_none(self):
        modes = ("postclose_stage_none", "report", "missing", "read_error", "unknown_stage",
                 "active", "bool_active", "alive", "admission_open", "qt_stage_none")
        for mode in modes:
            controller = None
            with self.subTest(mode=mode), closing(SimpleNamespace(
                    close=lambda: self._drain_api_retirement_fixture(controller))):
                error = PermissionError("raw close error " + mode)
                fact_error = OSError("raw fact error " + mode)
                assembly = ValueError("raw assembly " + mode)
                calls = []
                def close():
                    calls.append(QThread.currentThread())
                    raise error
                state = SimpleNamespace(admission_closed=mode != "admission_open",
                    active_requests=True if mode == "bool_active" else (1 if mode == "active" else 0),
                    worker_stopped=mode != "alive",
                    failure_stage="unexpected" if mode == "unknown_stage" else ("report" if mode == "report" else None))
                def fact():
                    if mode == "read_error":
                        raise fact_error
                    return state
                observer = SimpleNamespace(close=close)
                if mode != "missing":
                    observer.comparison_lifecycle_state = fact
                if mode == "qt_stage_none":
                    controller = self.controller([], market_data_client=observer, owned_observers=(observer,),
                        api_runtime_factory=lambda: {"market_data_client": object(), "owned_observers": ()})
                else:
                    close_caught, observed, fact_caught = None, None, None
                    try:
                        observer.close()
                    except BaseException as caught:
                        close_caught = caught
                    try:
                        observed = observer.comparison_lifecycle_state()
                    except BaseException as caught:
                        fact_caught = caught
                    payload = ((observer, close_caught, observed, fact_caught),)
                    assembly.api_runtime_observer_cleanup = payload
                    assembly.__cause__ = error
                    controller = self.controller([], market_data_client=object(), owned_observers=(),
                        api_runtime_factory=Mock(side_effect=assembly))
                controller.configure_runtime(AppRuntimeActions(apply_runtime=lambda _: None))
                controller._api_reloading, controller._api_reload_stage = True, "signals"
                with patch.object(controller, "start_initial_ranking"):
                    controller._advance_api_reload()
                self.wait_until(lambda: bool(controller._api_client_retirements) and
                    controller._api_client_retirements[0].result is not None)
                retained = controller._api_client_retirements[0]
                if mode == "qt_stage_none":
                    self.wait_until(lambda: retained.worker is None)
                    self.assertFalse(retained.inline_cleanup)
                    self.assertIs(True, retained.native_result)
                else:
                    self.assertTrue(retained.inline_cleanup)
                    self.assertIsNone(retained.native_result)
                    self.assertIsNone(retained.worker)
                    self.assertEqual([], controller.findChildren(SettingsRequestWorker))
                    self.assertIs(assembly, retained.error)
                    self.assertIs(payload, assembly.api_runtime_observer_cleanup)
                    self.assertIs(error, assembly.__cause__)
                self.assertIs(error, retained.result.error)
                self.assertEqual("close", retained.result.error_stage)
                if mode == "missing":
                    self.assertIsInstance(retained.result.observation_error, AttributeError)
                elif mode == "read_error":
                    self.assertIs(fact_error, retained.result.observation_error)
                else:
                    self.assertIs(state, retained.result.lifecycle)
                    self.assertIsNone(retained.result.observation_error)
                self.assertEqual(1, len(calls))
                known_stopped = mode in {"postclose_stage_none", "report"}
                self.assertIs(known_stopped, controller._api_client_stop_confirmed(retained))
                controller.request_close()
                if known_stopped:
                    self.wait_until(lambda: controller._shutdown_stage == "ready")
                else:
                    self.wait_until(lambda: controller._shutdown_stage == "api_retirements")
                    self.assertFalse(controller.request_close())
                    self.assertNotEqual("ready", controller._shutdown_stage)
                controller._shutdown_timer.stop()
                self.assertEqual(1, len(calls), "adoption and app close must not repeat an inline attempt")
                self.assertIs(retained, controller._api_client_retirements[0])

    def test_observer_owner_current_tuple_waits_for_real_entry_ack_retry_and_actual_native_join(self):
        from dataclasses import replace
        from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import (
            TradeEntrySnapshot, scoped_snapshot_execution_key,
        )
        with tempfile.TemporaryDirectory() as directory:
            rest, recorder, threads, original_closes = self._observer_owner_real_pair(directory, "shutdown")
            controller = writer = None
            resume, retry_resume = Event(), Event()
            try:
                path = Path(directory) / "journal.sqlite3"
                repository = JournalRepository(path)
                scope = AccountScope("kiwoom", AccountEnvironment.MOCK, "00000000-0000-4000-8000-000000000070")
                at = as_kst(datetime(2026, 10, 20, 10, 30))
                base = TradeEntrySnapshot(scoped_snapshot_execution_key("both-observers-native-070", scope),
                    "order-070", "005930", "삼성전자", "매수", at, 70000, 1, "KRX", origin_scope=scope, theme_ranks={},
                    market_state={"source": "native-070"}, orderbook={"bid": 69900}, capture_state="realtime_core")
                investor = {"available": True, "source": "native-070"}
                enriched = replace(base, investor_flow=investor, capture_state="realtime_enriched")
                committed, resume, retried, retry_resume = Event(), Event(), Event(), Event()
                saves, loads, failures, closes, events = [], [], [], [], []
                ack_error = RuntimeError("both observers native enriched ACK loss")
                original_save = JournalRepository.save_entry_snapshot
                def save(owner, value, now=None, *, account_scope=None):
                    result = original_save(owner, value, now, account_scope=account_scope)
                    saves.append((value, account_scope))
                    if value.capture_state == "realtime_enriched":
                        if len(saves) == 2:
                            committed.set()
                            if not resume.wait(3):
                                raise AssertionError("both observers first native ACK gate")
                            raise ack_error
                        retried.set()
                        if not retry_resume.wait(3):
                            raise AssertionError("both observers retry native ACK gate")
                    return result
                def load(code, observed):
                    loads.append((code, observed))
                    return investor
                controller = self.controller(events, market_data_client=rest, owned_observers=(rest, recorder, recorder),
                    journal_database_path=path, entry_investor_loader=load)
                writer = controller.entry_snapshot_writer
                writer.failed.connect(failures.append)
                def wrap(index):
                    def close():
                        closes.append((index, writer.wait(0), writer.has_pending_work(), QThread.currentThread(), tuple(saves)))
                        return original_closes[index]()
                    return close
                rest.close, recorder.close = wrap(0), wrap(1)
                with patch.object(JournalRepository, "save_entry_snapshot", autospec=True, side_effect=save), \
                        patch("kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer.news_at_execution", return_value=()) as news:
                    writer.enqueue(base)
                    controller.start_entry_snapshot_writer()
                    self.assertTrue(committed.wait(2))
                    self.assertTrue(writer.isRunning())
                    self.assertFalse(writer.wait(0))
                    self.assertFalse(controller.request_close())
                    self.assertEqual([], closes)
                    self.assertEqual((rest, recorder), controller._owned_observers)
                    self.assertEqual(enriched, repository.load_entry_snapshots("005930", at, at + timedelta(seconds=1), scope)[0])
                    resume.set()
                    self.assertTrue(writer.wait(4000))
                    self.wait_until(lambda: controller._shutdown_stage == "cache_failed")
                    self.assertTrue(writer.has_pending_work())
                    self.assertIsNotNone(writer._pending_job)
                    self.assertEqual(enriched, writer._pending_job)
                    self.assertTrue(any(str(ack_error) in message for message in failures))
                    self.assertEqual([], closes)
                    self.assertTrue(all(thread.is_alive() for thread in threads))
                    self.assertFalse(controller.request_close())
                    self.assertTrue(retried.wait(2))
                    self.assertTrue(writer.isRunning())
                    self.assertFalse(writer.wait(0))
                    self.assertTrue(writer.has_pending_work())
                    controller._advance_shutdown()
                    self.assertEqual([], closes)
                    self.assertEqual((rest, recorder), controller._owned_observers)
                    retry_resume.set()
                    self.assertTrue(writer.wait(4000))
                    self.assertFalse(writer.has_pending_work())
                    self.wait_until(lambda: controller._shutdown_stage == "ready")
                    for thread in threads:
                        thread.join(3)
                        self.assertFalse(thread.is_alive(), "current observer survived actual native writer drain")
                    self.assertEqual([0, 1], sorted(row[0] for row in closes))
                    for _, joined, pending, qt_thread, saved in closes:
                        self.assertIs(True, joined)
                        self.assertIs(False, pending)
                        self.assertIsNot(qt_thread, controller.thread())
                        self.assertEqual(tuple(saves), saved)
                    self.assertEqual([(base, scope), (enriched, scope), (enriched, scope)], saves)
                    self.assertEqual([("005930", at)], loads)
                    self.assertEqual(1, news.call_count)
                    with closing(sqlite3.connect(path)) as connection:
                        self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM trade_entry_snapshots").fetchone()[0])
                    self.assertEqual(enriched, repository.load_entry_snapshots("005930", at, at + timedelta(seconds=1), scope)[0])
                    self.assertIsNone(controller.market_data_client)
                    self.assertEqual((), controller._owned_observers)
                    self.assertEqual([], controller._api_client_retirements)
                    self.assertTrue(controller.request_close())
                    self.assertEqual(2, len(closes))
            finally:
                try:
                    resume.set()
                    retry_resume.set()
                    if writer is not None:
                        writer.requestInterruption()
                        self.assertTrue(writer.wait(4000))
                finally:
                    try:
                        for close, thread in zip(original_closes, threads):
                            close()
                            thread.join(3)
                            self.assertFalse(thread.is_alive())
                    finally:
                        self._drain_api_retirement_fixture(controller)

    def _run_observer_factory_probe(self, check, *, query_only=False):
        import json
        import threading
        from contextlib import ExitStack
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.kiwoom_rest import validation_client
        from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient
        from kiwoom_monitor.infrastructure.kiwoom_rest.client_factory import create_query_client
        from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient
        from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings

        now = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
        old_client = SimpleNamespace(server_now=lambda: now,
            require_query_access=lambda: {"api_version": 1, "access_scope": "query"})
        state = SimpleNamespace(active=False, resources=[], clients=[], closes=[], facts=[],
            close_errors={}, borrowed_closes=[], attach_borrowed_close=False, old_close=Mock(), last=None)
        recorder_type = validation_client.RealtimeValidationRecorder
        original_local_init = KiwoomRestClient.__init__
        original_remote_init = RemoteKiwoomRestClient.__init__
        original_entry = bootstrap._main_with_restore_gate

        class CapabilityResponse:
            headers = {"X-Kiwoom-Server-Time": now.isoformat()}

            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return json.dumps({"api_version": 1, "access_scope": "query"}).encode()

        def remote_transport(request, **_kwargs):
            if query_only and request.get_method() == "GET" and request.full_url == "https://fixture.invalid/api/v1/capabilities":
                return CapabilityResponse()
            raise AssertionError("unexpected factory NAS transport")

        state.remote_transport = Mock(side_effect=remote_transport)
        state.local_transport = Mock(side_effect=AssertionError("unexpected factory Kiwoom transport"))

        def local_init(client, *args, **kwargs):
            original_local_init(client, *args, **kwargs)
            client._opener = state.local_transport

        def remote_init(client, *args, **kwargs):
            original_remote_init(client, *args, **kwargs)
            client._opener = state.remote_transport

        def track(observer, previous, name, kind):
            created = [thread for thread in threading.enumerate()
                       if thread not in previous and thread.name == name]
            # Keep the class's exact real handle for failure cleanup before any
            # oracle assertion, then cross-check independent thread enumeration.
            thread = observer._thread
            raw_close = observer.close
            raw_fact = observer.comparison_lifecycle_state
            resource = SimpleNamespace(observer=observer, thread=thread, kind=kind,
                                       raw_close=raw_close, raw_fact=raw_fact)
            state.resources.append(resource)
            self.assertEqual(1, len(created))
            self.assertIs(thread, created[0])
            self.assertTrue(thread.is_alive())

            def close():
                state.closes.append((observer, threading.get_ident()))
                raw_close()
                self.assertFalse(thread.is_alive())
                error = state.close_errors.get(kind)
                if error is not None:
                    raise error  # Deliberate wrapper fault after real actual stop.

            def fact():
                state.facts.append(observer)
                return raw_fact()

            observer.close, observer.comparison_lifecycle_state = close, fact
            return observer

        def factory(*args, **kwargs):
            if not state.active:
                return old_client
            self.assertEqual(state.fixture.paths.data_dir.resolve(), Path(args[0]).resolve().parent)
            previous = set(threading.enumerate())
            client = create_query_client(*args, **kwargs)
            state.clients.append((client, kwargs["source_settings"]))
            if isinstance(client, validation_client.ParallelValidationClient):
                track(client, previous, "central-local-validation", "rest")
            elif state.attach_borrowed_close:
                close = Mock()
                client.close = close  # A borrowed port does not become an owned observer.
                state.borrowed_closes.append((client, close))
            return client

        def recorder(*args, **kwargs):
            previous = set(threading.enumerate())
            observer = recorder_type(*args, **kwargs)
            self.assertIsInstance(observer, recorder_type)
            return track(observer, previous, "realtime-validation-writer", "recorder")

        def during_loop(fixture):
            state.fixture = fixture
            state.active = True
            old_client.close = state.old_close

            def invoke(source, *, late_error=None, attach_borrowed_close=False):
                bootstrap.DataSourceConfig.load.return_value = source
                state.attach_borrowed_close = attach_borrowed_close
                # A real existing settings input is prepared before the scoped
                # DB guard; no fake database or altered settings value is used.
                fixture.window._settings.get("rank_query_type")
                scope = SimpleNamespace(resource_start=len(state.resources), client_start=len(state.clients),
                    close_start=len(state.closes), fact_start=len(state.facts), db_calls=[],
                    remote_start=state.remote_transport.call_count, profiles_start=profile_load.call_count)
                state.last = scope
                owner_thread = threading.get_ident()
                real_connect = sqlite3.connect

                def no_factory_db(*args, **kwargs):
                    if threading.get_ident() == owner_thread:
                        scope.db_calls.append(args)
                        raise AssertionError("nested factory unexpectedly opened a database")
                    return real_connect(*args, **kwargs)

                with ExitStack() as guards:
                    guards.enter_context(patch.object(sqlite3, "connect", side_effect=no_factory_db))
                    local_tr = guards.enter_context(patch.object(KiwoomRestClient, "request_with_continuation",
                        side_effect=AssertionError("unexpected factory Kiwoom TR")))
                    remote_tr = guards.enter_context(patch.object(RemoteKiwoomRestClient, "request_with_continuation",
                        side_effect=AssertionError("unexpected factory NAS TR")))
                    inputs = [guards.enter_context(patch.object(recorder_type, name,
                        side_effect=AssertionError("factory accepted an observation")))
                        for name in ("observe_central", "observe_local", "flush_expired")]
                    if late_error is not None:
                        guards.enter_context(patch.object(bootstrap, "StockRepository", side_effect=late_error))
                    try:
                        return fixture.window._app_controller._api_runtime_factory()
                    finally:
                        self.assertEqual([], scope.db_calls)
                        local_tr.assert_not_called()
                        remote_tr.assert_not_called()
                        for input_guard in inputs: input_guard.assert_not_called()
                        state.local_transport.assert_not_called()
                        state.old_close.assert_not_called()
                        scope.remote_calls = state.remote_transport.call_count - scope.remote_start
                        scope.profile_reads = profile_load.call_count - scope.profiles_start

            try:
                check(fixture, invoke, state)
            finally:
                state.active = False
                del old_client.close

        try:
            with ExitStack() as stack:
                profile_load = stack.enter_context(patch.object(bootstrap.LocalApiConfig, "load",
                    return_value=KiwoomSettings("fixture-key", "fixture-secret", "mock")))
                stack.enter_context(patch.object(KiwoomRestClient, "__init__", local_init))
                stack.enter_context(patch.object(RemoteKiwoomRestClient, "__init__", remote_init))
                stack.enter_context(patch.object(validation_client, "RealtimeValidationRecorder", side_effect=recorder))
                stack.enter_context(patch.object(MainWindow, "_refresh_rankings"))
                stack.enter_context(patch.object(MainWindow, "_restore_environment_selector"))
                stack.enter_context(patch.object(AppController, "schedule_next_ranking_refresh"))
                stack.enter_context(patch.object(AppController, "schedule_realtime_session_refresh"))
                if query_only:
                    stack.enter_context(patch.object(bootstrap, "_main_with_restore_gate",
                        side_effect=lambda: original_entry(query_only=True)))
                self._bootstrap_settings_desktop(during_loop, cleanup_window=True,
                    source_mode="personal_server" if query_only else "local", query_client_factory=factory)
        finally:
            # The original MAIN150 failure leaves the actual Recorder daemon
            # alive. Observe that first; then close exact test-owned resources
            # before the fixture's owned TEMP is removed. This is not product proof.
            for resource in state.resources:
                if resource.thread.is_alive():
                    resource.raw_close()
                resource.thread.join(3)
                self.assertFalse(resource.thread.is_alive())

    def test_bootstrap_factory_owned_tuple_tracks_real_observers_and_borrowed_branches(self):
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings

        def ordinary_cases(fixture, invoke, state):
            for name, fallback, parallel, expected_profiles in (
                    ("parallel", False, True, 1), ("fallback-only", True, False, 1), ("bare", False, False, 0)):
                with self.subTest(branch=name):
                    runtime = invoke(DataSourceSettings("personal_server", "https://fixture.invalid", "fixture", fallback, parallel))
                    created = state.resources[state.last.resource_start:]
                    client, source = state.clients[-1]
                    if parallel:
                        self.assertEqual(["rest", "recorder"], [item.kind for item in created])
                        owned = runtime.get("owned_observers")
                        self.assertIsInstance(owned, tuple)
                        self.assertEqual(2, len(owned))
                        self.assertIs(created[0].observer, owned[0])
                        self.assertIs(created[1].observer, owned[1])
                        self.assertIs(client, owned[0])
                        for item in created:
                            fact = item.raw_fact()
                            self.assertIs(type(fact.active_requests), int)
                            self.assertIs(fact.admission_closed, False)
                            self.assertIs(fact.worker_stopped, False)
                            self.assertEqual((False, 0, False, None), (fact.admission_closed,
                                fact.active_requests, fact.worker_stopped, fact.failure_stage))
                            self.assertTrue(item.thread.is_alive())
                    else:
                        self.assertEqual([], created)
                        self.assertEqual((), runtime.get("owned_observers"))
                    self.assertIs(client, runtime["market_data_client"])
                    worker = runtime["realtime_worker_factory"](("005930",))
                    worker.setParent(fixture.window)
                    try:
                        self.assertFalse(worker.isRunning())
                        self.assertIs(created[1].observer if parallel else None, worker._validation_recorder)
                    finally:
                        worker.deleteLater()
                    self.assertEqual(fallback, source.local_fallback_enabled)
                    self.assertEqual(parallel, source.parallel_validation_enabled)
                    self.assertEqual(0, state.last.remote_calls)
                    self.assertEqual(expected_profiles, state.last.profile_reads)
                    self.assertEqual([], state.closes[state.last.close_start:])
                    self.assertEqual([], state.facts[state.last.fact_start:])
                    self.assertFalse((fixture.paths.data_dir / "central_local_validation.jsonl").exists())
                    self.assertFalse((fixture.paths.data_dir / "central_local_realtime_validation.jsonl").exists())

        def query_case(_fixture, invoke, state):
            runtime = invoke(DataSourceSettings("personal_server", "https://fixture.invalid", "fixture", True, True))
            client, projected = state.clients[-1]
            self.assertFalse(projected.local_fallback_enabled)
            self.assertFalse(projected.parallel_validation_enabled)
            self.assertEqual([], state.resources[state.last.resource_start:])
            self.assertIs(client, runtime["market_data_client"])
            self.assertEqual((), runtime.get("owned_observers"))
            self.assertEqual(0, state.last.profile_reads)
            self.assertEqual(1, state.last.remote_calls)  # Required capability GET, not a query TR or network.
            self.assertEqual([], state.closes[state.last.close_start:])

        self._run_observer_factory_probe(ordinary_cases)
        self._run_observer_factory_probe(query_case, query_only=True)

    def test_bootstrap_late_factory_failure_preserves_both_real_observer_outcomes(self):
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings

        def cases(fixture, invoke, state):
            for failed_kinds in ((), ("recorder",), ("rest", "recorder")):
                with self.subTest(cleanup_errors=failed_kinds):
                    assembly_error = RuntimeError("fixture late StockRepository construction failure")
                    state.close_errors = {kind: PermissionError("fixture " + kind + " close wrapper failure")
                                          for kind in failed_kinds}
                    with self.assertRaises(RuntimeError) as caught:
                        invoke(DataSourceSettings("personal_server", "https://fixture.invalid", "fixture", False, True),
                               late_error=assembly_error)
                    self.assertIs(assembly_error, caught.exception)
                    created = state.resources[state.last.resource_start:]
                    self.assertEqual(["rest", "recorder"], [item.kind for item in created])
                    # Actual producer stop is the independent first baseline
                    # oracle; missing metadata is not substituted for old leakage.
                    self.assertFalse(created[0].thread.is_alive())
                    self.assertFalse(created[1].thread.is_alive())
                    expected = [item.observer for item in created]
                    self.assertEqual(expected, [observer for observer, _thread in state.closes[state.last.close_start:]])
                    self.assertEqual(expected, state.facts[state.last.fact_start:])
                    cleanup = getattr(caught.exception, "api_runtime_observer_cleanup", None)
                    self.assertIsInstance(cleanup, tuple)
                    self.assertEqual(2, len(cleanup))
                    for item, entry in zip(created, cleanup):
                        self.assertIsInstance(entry, tuple)
                        self.assertEqual(4, len(entry))
                        observer, close_error, fact, read_error = entry
                        self.assertIs(item.observer, observer)
                        self.assertIs(state.close_errors.get(item.kind), close_error)
                        self.assertIsNone(read_error)
                        self.assertIs(type(fact.active_requests), int)
                        self.assertIs(fact.admission_closed, True)
                        self.assertIs(fact.worker_stopped, True)
                        self.assertEqual((True, 0, True, None), (fact.admission_closed,
                            fact.active_requests, fact.worker_stopped, fact.failure_stage))
                    first = next((state.close_errors[item.kind] for item in created if item.kind in state.close_errors), None)
                    self.assertIs(first, caught.exception.__cause__)
                    self.assertEqual(0, created[0].observer.validation_summary()["total_observations"])
                    self.assertEqual(0, created[1].observer.validation_summary()["total_observations"])
                    self.assertFalse((fixture.paths.data_dir / "central_local_validation.jsonl").exists())
                    self.assertFalse((fixture.paths.data_dir / "central_local_realtime_validation.jsonl").exists())
                    self.assertEqual(0, state.last.remote_calls)
                    self.assertEqual(1, state.last.profile_reads)

        self._run_observer_factory_probe(cases)

    def test_bootstrap_late_borrowed_factory_failure_does_not_close_data_ports(self):
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings

        def case(_fixture, invoke, state):
            assembly_error = RuntimeError("fixture borrowed factory construction failure")
            source = DataSourceSettings("personal_server", "https://fixture.invalid", "fixture", True, True) if query_mode else DataSourceSettings()
            with self.assertRaises(RuntimeError) as caught:
                invoke(source, late_error=assembly_error, attach_borrowed_close=True)
            self.assertIs(assembly_error, caught.exception)
            self.assertEqual([], state.resources[state.last.resource_start:])
            self.assertEqual(1, len(state.borrowed_closes))
            client, close = state.borrowed_closes[0]
            self.assertIs(client, state.clients[-1][0])
            close.assert_not_called()  # Presence of close does not grant ownership.
            self.assertEqual((), getattr(caught.exception, "api_runtime_observer_cleanup", None))
            self.assertIsNone(caught.exception.__cause__)
            self.assertEqual([], state.closes[state.last.close_start:])
            self.assertEqual([], state.facts[state.last.fact_start:])
            self.assertEqual(int(query_mode), state.last.remote_calls)
            self.assertEqual(0 if query_mode else 1, state.last.profile_reads)

        for query_mode in (False, True):
            with self.subTest(query_only=query_mode):
                self._run_observer_factory_probe(case, query_only=query_mode)

    def test_bootstrap_pre_window_setup_failure_closes_only_fresh_observers_before_source_lease(self):
        import threading
        from contextlib import ExitStack
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
        from kiwoom_monitor.infrastructure.kiwoom_rest import validation_client
        from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient
        from kiwoom_monitor.infrastructure.kiwoom_rest.client_factory import create_query_client
        from kiwoom_monitor.infrastructure.kiwoom_rest.remote_client import RemoteKiwoomRestClient
        from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease

        # Existing content/settings fixture owns its TEMP with TestCase.enterContext.
        # Its finalizer may release content; it never closes these observer handles.
        # The first baseline oracle below is actual liveness BEFORE test cleanup.
        cases = (
            ("application", True, (), (), None),
            ("icon", True, ("rest", "recorder"), ("theme", "content", "server"), None),
            ("theme", True, (), ("theme", "content", "server"), None),
            ("theme", True, (), (), "rest"),
            ("theme", False, (), (), None),
        )
        for stage, parallel, failed_closes, failed_sources, failed_fact in cases:
            with self.subTest(stage=stage, parallel=parallel, failed_closes=failed_closes,
                              failed_sources=failed_sources, failed_fact=failed_fact):
                original_error = RuntimeError("fixture pre-window " + stage + " failure")
                state = SimpleNamespace(resources=[], borrowed_closes=[], events=[], fixture=None,
                    failed=False, lease_snapshots=[], query_clients=[],
                    close_errors={kind: PermissionError("fixture " + kind + " after actual close")
                                  for kind in failed_closes},
                    source_errors={kind: OSError("fixture " + kind + " after actual cleanup")
                                   for kind in failed_sources},
                    fact_error=RuntimeError("fixture lifecycle observation unavailable"))
                original_recorder = validation_client.RealtimeValidationRecorder
                original_join = threading.Thread.join
                original_lease_close = AppDataLease.close
                original_theme_close = bootstrap.CentralThemeSyncDispatcher.close
                original_settings_get = bootstrap.SettingsRepository.get
                owner_thread = threading.get_ident()
                service = Mock()
                service.replace_themes.return_value = SimpleNamespace(pending_collections=())
                service.push.return_value = SimpleNamespace(total=0)
                transport = Mock(side_effect=AssertionError("pre-window fixture must not query"))

                def track(observer, previous, kind, thread_name):
                    thread = observer._thread
                    resource = SimpleNamespace(observer=observer, thread=thread, kind=kind,
                        raw_close=observer.close, raw_fact=observer.comparison_lifecycle_state)
                    state.resources.append(resource)  # Own safety cleanup before assertions.
                    created = [value for value in threading.enumerate()
                               if value not in previous and value.name == thread_name]
                    self.assertEqual([thread], created)
                    self.assertTrue(thread.is_alive())

                    def close():
                        state.events.append(kind + ".close")
                        resource.raw_close()
                        if kind in state.close_errors:
                            raise state.close_errors[kind]

                    def fact():
                        state.events.append(kind + ".fact")
                        if kind == failed_fact:
                            raise state.fact_error
                        return resource.raw_fact()

                    observer.close, observer.comparison_lifecycle_state = close, fact
                    return observer

                def factory(*args, **kwargs):
                    previous = set(threading.enumerate())
                    client = create_query_client(*args, **kwargs)
                    state.query_clients.append(client)
                    if parallel:
                        self.assertIsInstance(client, validation_client.ParallelValidationClient)
                        for borrowed in (client._primary, client._local):
                            borrowed._opener = transport
                            close = Mock()
                            borrowed.close = close
                            state.borrowed_closes.append(close)
                        return track(client, previous, "rest", "central-local-validation")
                    client._opener = transport
                    close = Mock()
                    client.close = close
                    state.borrowed_closes.append(close)
                    return client

                def recorder(*args, **kwargs):
                    previous = set(threading.enumerate())
                    return track(original_recorder(*args, **kwargs), previous,
                                 "recorder", "realtime-validation-writer")

                def pull(*_args, **_kwargs):
                    fixture = state.fixture
                    state.content_thread = threading.current_thread()
                    fixture.entered.set()
                    try:
                        if not fixture.release.wait(4):
                            raise RuntimeError("content HTTP fixture was not released")
                        return SimpleNamespace(total=0)
                    finally:
                        fixture.completed.set()

                service.pull.side_effect = pull

                def fail_setup(*_args, **_kwargs):
                    self.assertEqual(2 if parallel else 0, len(state.resources))
                    self.assertEqual(1, len(state.query_clients))
                    self.assertTrue(state.fixture.entered.wait(4))
                    self.assertTrue(state.content_thread.is_alive())
                    self.assertTrue(all(item.thread.is_alive() for item in state.resources))
                    self.assertFalse(state.fixture.restore_available())
                    state.fixture.server.stop.assert_not_called()
                    state.events.append("setup.failure")
                    state.failed = True
                    raise original_error

                def settings_get(repository, key, *args, **kwargs):
                    if stage == "theme" and key == "theme_active_profile" and threading.get_ident() == owner_thread:
                        fail_setup()
                    return original_settings_get(repository, key, *args, **kwargs)

                def theme_close(dispatcher):
                    if not state.failed:
                        return original_theme_close(dispatcher)
                    state.events.append("theme.close")
                    original_theme_close(dispatcher)
                    if "theme" in state.source_errors:
                        raise state.source_errors["theme"]

                def join(thread, *args, **kwargs):
                    if thread is not getattr(state, "content_thread", None) or not state.failed or args or kwargs:
                        return original_join(thread, *args, **kwargs)
                    # This is the product's actual join call. Release only here;
                    # the fixture safety join(5) above uses the original join.
                    # An old source missing this call stays live until that cleanup.
                    if "content.join" not in state.events:
                        state.events.append("content.join")
                        state.content_join_snapshot = [item.thread.is_alive() for item in state.resources]
                        state.fixture.release.set()
                        result = original_join(thread, *args, **kwargs)
                        if "content" in state.source_errors:
                            raise state.source_errors["content"]
                        return result
                    return original_join(thread, *args, **kwargs)

                def close_lease(lease):
                    if state.failed and not lease._exclusive:
                        state.lease_snapshots.append({
                            "observer_alive": [item.thread.is_alive() for item in state.resources],
                            "content_alive": state.content_thread.is_alive(),
                            "server_stops": state.fixture.server.stop.call_count,
                        })
                        state.events.append("lease.close")
                    return original_lease_close(lease)

                def before_run(fixture):
                    state.fixture = fixture
                    configured = bootstrap.DataSourceConfig.load.return_value
                    bootstrap.DataSourceConfig.load.side_effect = (
                        configured,
                        DataSourceSettings("personal_server", "https://fixture.invalid", "fixture", False, parallel),
                    )
                    application = bootstrap.QApplication.return_value
                    if stage == "application":
                        bootstrap.QApplication.side_effect = fail_setup
                    elif stage == "icon":
                        application.setWindowIcon.side_effect = fail_setup

                    def stop_server():
                        state.events.append("server.stop")
                        if "server" in state.source_errors:
                            raise state.source_errors["server"]

                    fixture.server.stop.side_effect = stop_server

                def check(fixture):
                    # Baseline must fail on these real threads before metadata,
                    # helper finalizers, or test-owned cleanup can hide leakage.
                    self.assertEqual(2 if parallel else 0, len(state.resources))
                    for resource in state.resources:
                        self.assertFalse(resource.thread.is_alive(),
                            "pre-test-cleanup actual " + resource.kind + " thread remains alive; lease="
                            + repr(state.lease_snapshots))
                    self.assertFalse(state.content_thread.is_alive())
                    self.assertEqual([{"observer_alive": [False] * len(state.resources),
                        "content_alive": False, "server_stops": 1}], state.lease_snapshots)
                    self.assertEqual([False] * len(state.resources), state.content_join_snapshot)
                    self.assertIs(original_error, fixture.exit_error)
                    for close in state.borrowed_closes:
                        close.assert_not_called()
                    bootstrap.MainWindow.assert_not_called()
                    cleanup = getattr(fixture.exit_error, "api_runtime_observer_cleanup", None)
                    self.assertIsInstance(cleanup, tuple)
                    self.assertEqual(len(state.resources), len(cleanup))
                    for resource, entry in zip(state.resources, cleanup):
                        self.assertIsInstance(entry, tuple)
                        self.assertEqual(4, len(entry))
                        observer, close_error, fact, read_error = entry
                        self.assertIs(resource.observer, observer)
                        self.assertIs(state.close_errors.get(resource.kind), close_error)
                        if resource.kind == failed_fact:
                            self.assertIsNone(fact)  # UNKNOWN is not a fabricated stopped fact.
                            self.assertIs(state.fact_error, read_error)
                        else:
                            self.assertIsNone(read_error)
                            self.assertIs(type(fact.active_requests), int)
                            self.assertEqual((True, 0, True, None), (fact.admission_closed,
                                fact.active_requests, fact.worker_stopped, fact.failure_stage))
                    outer = getattr(fixture.exit_error, "desktop_setup_cleanup", None)
                    self.assertIsInstance(outer, tuple)
                    self.assertEqual(("close", "join", "stop"), tuple(entry[1] for entry in outer))
                    self.assertIs(state.content_thread, outer[1][0])
                    self.assertIs(fixture.server, outer[2][0])
                    for kind, entry in zip(("theme", "content", "server"), outer):
                        self.assertIs(state.source_errors.get(kind), entry[2])
                    first = next((state.close_errors[kind] for kind in ("rest", "recorder")
                                  if kind in state.close_errors), None)
                    if first is None:
                        first = next((state.source_errors[kind] for kind in ("theme", "content", "server")
                                      if kind in state.source_errors), None)
                    self.assertIs(first, fixture.exit_error.__cause__)
                    self.assertEqual(["setup.failure"] + [event for item in state.resources
                        for event in (item.kind + ".close", item.kind + ".fact")]
                        + ["theme.close", "content.join", "server.stop", "lease.close"], state.events)
                    self.assertTrue(fixture.restore_available())
                    transport.assert_not_called()

                try:
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(bootstrap.LocalApiConfig, "load",
                            return_value=KiwoomSettings("fixture-key", "fixture-secret", "mock")))
                        stack.enter_context(patch.object(validation_client, "RealtimeValidationRecorder", side_effect=recorder))
                        stack.enter_context(patch.object(bootstrap.SettingsRepository, "get", autospec=True, side_effect=settings_get))
                        stack.enter_context(patch.object(bootstrap.CentralThemeSyncDispatcher, "close", theme_close))
                        stack.enter_context(patch.object(threading.Thread, "join", join))
                        stack.enter_context(patch.object(AppDataLease, "close", close_lease))
                        local_tr = stack.enter_context(patch.object(KiwoomRestClient, "request_with_continuation",
                            side_effect=AssertionError("unexpected local query before Window")))
                        remote_tr = stack.enter_context(patch.object(RemoteKiwoomRestClient, "request_with_continuation",
                            side_effect=AssertionError("unexpected central query before Window")))
                        self._bootstrap_settings_desktop(
                            lambda _fixture: self.fail("pre-window failure reached event loop"),
                            after_exit=check, content_service=service, expected_error=RuntimeError,
                            before_run=before_run, query_client_factory=factory,
                        )
                        local_tr.assert_not_called()
                        remote_tr.assert_not_called()
                finally:
                    # Baseline liveness was asserted inside check first. Only
                    # exact handles owned by this test are now safety-cleaned.
                    # TEMP survives until TestCase cleanup (enterContext owner).
                    for resource in state.resources:
                        resource.raw_close()
                        resource.thread.join(3)
                        self.assertFalse(resource.thread.is_alive())

    def test_bootstrap_pre_window_close_failure_does_not_release_source_lease_with_live_owned_observer(self):
        import threading
        from contextlib import ExitStack
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
        from kiwoom_monitor.infrastructure.kiwoom_rest import validation_client
        from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease

        # Independent strict oracle for B1: do not turn the current missing
        # lifetime guarantee into a PASS by expecting early lease release.
        setup_error = RuntimeError("fixture application setup failure before Window")
        close_error = PermissionError("fixture REST close failed before real join")
        observation_error = RuntimeError("fixture REST lifecycle is UNKNOWN")
        state = SimpleNamespace(fixture=None, pair=None, failed=False, before_lease=[], borrowed=[])
        original_lease_close = AppDataLease.close
        original_recorder_class = validation_client.RealtimeValidationRecorder
        service = Mock()
        service.replace_themes.return_value = SimpleNamespace(pending_collections=())
        service.pull.return_value = SimpleNamespace(total=0)
        service.push.return_value = SimpleNamespace(total=0)

        def factory(*_args, **_kwargs):
            # The actual two observer classes/threads are acquired here while
            # the real main lease is held, using the existing real-pair helper.
            with patch.object(validation_client, "RealtimeValidationRecorder", original_recorder_class):
                rest, recorder, threads, raw_closes = self._observer_owner_real_pair(
                    state.fixture.paths.data_dir, "setup-before-join")
            state.pair = rest, recorder, threads, raw_closes
            self.assertFalse(state.fixture.restore_available())
            now = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
            for borrowed in (rest._primary, rest._local):
                borrowed.server_now = lambda: now
                borrowed.get_access_token = Mock(side_effect=AssertionError("unexpected token read"))
                close = Mock()
                borrowed.close = close
                state.borrowed.append(close)

            def fail_close():
                raise close_error  # The real REST daemon is still alive.

            def unknown_fact():
                raise observation_error

            rest.close, rest.comparison_lifecycle_state = fail_close, unknown_fact
            return rest

        def recorder(*_args, **_kwargs):
            return state.pair[1]

        def fail_setup(*_args, **_kwargs):
            self.assertIsNotNone(state.pair)
            self.assertTrue(all(thread.is_alive() for thread in state.pair[2]))
            state.failed = True
            raise setup_error

        def close_lease(lease):
            if state.failed and not lease._exclusive:
                state.before_lease.append({
                    "observer_alive": tuple(thread.is_alive() for thread in state.pair[2]),
                    "rest_fact": "UNKNOWN",
                    "server_stops": state.fixture.server.stop.call_count,
                    "setup_error": setup_error,
                    "close_error": close_error,
                    "observation_error": observation_error,
                })
            return original_lease_close(lease)

        def before_run(fixture):
            state.fixture = fixture
            configured = bootstrap.DataSourceConfig.load.return_value
            bootstrap.DataSourceConfig.load.side_effect = (
                configured,
                DataSourceSettings("personal_server", "https://fixture.invalid", "fixture", False, True),
            )
            bootstrap.QApplication.side_effect = fail_setup

        def check(fixture):
            # Preserve independent identity/UNKNOWN evidence before the strict
            # native ordering assertion. V1 is expected to expose this gap;
            # neither expectedFailure nor an inverse assertion is permitted.
            self.assertIs(setup_error, fixture.exit_error)
            self.assertIs(close_error, fixture.exit_error.__cause__)
            cleanup = getattr(fixture.exit_error, "api_runtime_observer_cleanup", None)
            self.assertIsInstance(cleanup, tuple)
            self.assertEqual(2, len(cleanup))
            self.assertIs(state.pair[0], cleanup[0][0])
            self.assertIs(close_error, cleanup[0][1])
            self.assertIsNone(cleanup[0][2])
            self.assertIs(observation_error, cleanup[0][3])
            self.assertIs(state.pair[1], cleanup[1][0])
            self.assertTrue(cleanup[1][2].worker_stopped)
            for close in state.borrowed:
                close.assert_not_called()
            bootstrap.MainWindow.assert_not_called()
            self.assertEqual(1, len(state.before_lease))
            self.assertEqual((False, False), state.before_lease[0]["observer_alive"],
                "B1 HOLD: source lease close was entered with an actual live REST observer, "
                "original setup/close errors and UNKNOWN lifecycle: " + repr(state.before_lease))
            self.assertTrue(all(not thread.is_alive() for thread in state.pair[2]))

        try:
            with ExitStack() as stack:
                stack.enter_context(patch.object(bootstrap.LocalApiConfig, "load",
                    return_value=KiwoomSettings("fixture-key", "fixture-secret", "mock")))
                stack.enter_context(patch.object(validation_client, "RealtimeValidationRecorder", side_effect=recorder))
                stack.enter_context(patch.object(AppDataLease, "close", close_lease))
                self._bootstrap_settings_desktop(
                    lambda _fixture: self.fail("pre-window failure reached event loop"),
                    after_exit=check, content_service=service, expected_error=RuntimeError,
                    before_run=before_run, query_client_factory=factory,
                )
        finally:
            # The real-live/UNKNOWN failure is collected above first. Only now
            # may this test's exact captured public handles finish safe cleanup.
            if state.pair is not None:
                for raw_close in state.pair[3]:
                    raw_close()
                for thread in state.pair[2]:
                    thread.join(3)
                    self.assertFalse(thread.is_alive())


    def test_bootstrap_native_completion_defers_repeated_wait_errors_until_sources_and_lease_end(self):
        import subprocess
        import sys
        import threading
        from contextlib import ExitStack
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
        from kiwoom_monitor.infrastructure.kiwoom_rest import validation_client
        from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease

        setup_error = RuntimeError("fixture QApplication failure with exact native owners")
        close_error = PermissionError("fixture legacy REST close failed before join")
        getter_error = RuntimeError("fixture independent REST lifecycle UNKNOWN")
        rest_interrupts = (KeyboardInterrupt("first native join interruption"),
                           SystemExit("second native join interruption"))
        content_interrupts = (KeyboardInterrupt("first content join interruption"),
                              OSError("second content join interruption"))
        process_interrupt = KeyboardInterrupt("process wait interruption")
        stop_error = OSError("fixture stop failed before terminate")
        state = SimpleNamespace(fixture=None, pair=None, failed=False, events=[], proof_errors=[],
            lease_snapshots=[], rest_wait_errors=[], content_wait_errors=[], process_wait_errors=[],
            rest_wait=Event(), rest_tail=Event(), rest_release=Event(), content_wait=Event(),
            process_wait=Event(), abort=Event(), proof_done=Event(), proof=None, process=None,
            borrowed=[], no_queue_waits=[])
        original_recorder_class = validation_client.RealtimeValidationRecorder
        real_thread, raw_join = threading.Thread, threading.Thread.join
        raw_lease_close = AppDataLease.close
        owner_ident = threading.get_ident()
        service = Mock()
        service.replace_themes.return_value = SimpleNamespace(pending_collections=())
        service.push.return_value = SimpleNamespace(total=0)

        def pull(*_args, **_kwargs):
            state.content_thread = threading.current_thread()
            state.fixture.entered.set()
            try:
                if not state.fixture.release.wait(4):
                    raise RuntimeError("content fixture was not released")
                return SimpleNamespace(total=0)
            finally:
                state.fixture.completed.set()

        service.pull.side_effect = pull

        def real_thread_factory(*args, **kwargs):
            target = kwargs.get("target")
            if kwargs.get("name") == "central-local-validation":
                def native_target():
                    try:
                        target()
                    finally:
                        state.rest_tail.set()
                        if not state.rest_release.wait(4):
                            raise RuntimeError("native REST tail was not released")
                kwargs = dict(kwargs, target=native_target)
            return real_thread(*args, **kwargs)

        def factory(*_args, **_kwargs):
            # Preserve V2's constructor wiring: the helper must instantiate the
            # real Recorder before the outer callback reuses that exact object.
            with patch.object(validation_client, "RealtimeValidationRecorder", original_recorder_class), \
                    patch.object(validation_client.threading, "Thread", side_effect=real_thread_factory):
                state.pair = self._observer_owner_real_pair(state.fixture.paths.data_dir, "native-completion")
            rest, recorder, threads, _raw_closes = state.pair
            self.assertFalse(state.fixture.restore_available())
            now = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
            for borrowed in (rest._primary, rest._local):
                borrowed.server_now = lambda: now
                borrowed.get_access_token = Mock(side_effect=AssertionError("unexpected token query"))
                close = Mock()
                borrowed.close = close
                state.borrowed.append(close)

            def legacy_close():
                state.events.append("legacy.close.error")
                raise close_error

            def unknown_fact():
                raise getter_error

            rest.close, rest.comparison_lifecycle_state = legacy_close, unknown_fact
            rest_thread = threads[0]

            def rest_join(*args, **kwargs):
                if threading.get_ident() == owner_ident and not args and not kwargs:
                    if len(state.rest_wait_errors) < len(rest_interrupts):
                        error = rest_interrupts[len(state.rest_wait_errors)]
                        state.rest_wait_errors.append(error)
                        raise error
                    state.events.append("rest.join.blocking")
                    state.rest_wait.set()
                result = raw_join(rest_thread, *args, **kwargs)
                if threading.get_ident() == owner_ident and not args and not kwargs:
                    state.events.append("rest.join.completed")
                return result

            rest_thread.join = rest_join
            # These are separate diagnostics, never actual native completion.
            for queue in (rest._jobs, recorder._report_jobs):
                guard = Mock(side_effect=AssertionError("Queue.join is not native completion"))
                queue.join = guard
                state.no_queue_waits.append(guard)
            recorder.wait_for_writes = Mock(side_effect=AssertionError("write counter is not native completion"))
            state.no_queue_waits.append(recorder.wait_for_writes)
            return rest

        def recorder(*_args, **_kwargs):
            return state.pair[1]

        def thread_join(thread, *args, **kwargs):
            if (thread is getattr(state, "content_thread", None) and state.failed
                    and threading.get_ident() == owner_ident and not args and not kwargs):
                if len(state.content_wait_errors) < len(content_interrupts):
                    error = content_interrupts[len(state.content_wait_errors)]
                    state.content_wait_errors.append(error)
                    raise error
                state.events.append("content.join.blocking")
                state.content_wait.set()
                result = raw_join(thread)
                state.events.append("content.join.completed")
                return result
            return raw_join(thread, *args, **kwargs)

        def proof():
            try:
                for entered in (state.rest_wait, state.rest_tail):
                    if not entered.wait(4):
                        raise AssertionError("actual REST native wait was not entered")
                if state.abort.is_set(): return
                self.assertTrue(state.pair[2][0].is_alive())
                self.assertFalse(state.pair[2][1].is_alive())
                self.assertTrue(state.content_thread.is_alive())
                self.assertIsNone(state.process.poll())
                self.assertFalse(state.fixture.restore_available())
                self.assertEqual([], state.lease_snapshots)
                state.fixture.server.stop.assert_not_called()
                state.events.append("proof.rest.held")
                state.rest_release.set()
                if not state.content_wait.wait(4):
                    raise AssertionError("actual content native wait was not entered")
                if state.abort.is_set(): return
                self.assertFalse(any(thread.is_alive() for thread in state.pair[2]))
                self.assertTrue(state.content_thread.is_alive())
                self.assertFalse(state.fixture.restore_available())
                state.fixture.server.stop.assert_not_called()
                self.assertEqual([], state.lease_snapshots)
                state.events.append("proof.content.held")
                state.fixture.release.set()
                if not state.process_wait.wait(4):
                    raise AssertionError("captured process native wait was not entered")
                if state.abort.is_set(): return
                self.assertFalse(any(thread.is_alive() for thread in state.pair[2]))
                self.assertFalse(state.content_thread.is_alive())
                self.assertIsNone(state.process.poll())
                self.assertIsNone(state.fixture.server.process)  # stop removed the public reference.
                self.assertEqual(1, state.fixture.server.stop.call_count)
                self.assertFalse(state.fixture.restore_available())
                self.assertEqual([], state.lease_snapshots)
                state.events.append("proof.process.held")
                # This is the external completion of the test-owned child.
                # Product stop failed before terminate; no new kill policy is asserted.
                state.process.stdin.close()
            except BaseException as error:
                state.proof_errors.append(error)
            finally:
                state.rest_release.set()
                if state.fixture is not None: state.fixture.release.set()
                if state.process is not None and state.process.stdin is not None and not state.process.stdin.closed:
                    state.process.stdin.close()
                state.proof_done.set()

        def fail_setup(*_args, **_kwargs):
            self.assertIsNotNone(state.pair)
            self.assertTrue(all(thread.is_alive() for thread in state.pair[2]))
            self.assertTrue(state.fixture.entered.wait(4))
            self.assertFalse(state.fixture.restore_available())
            state.failed = True
            state.proof = real_thread(target=proof, name="c10-native-proof")
            state.proof.start()
            raise setup_error

        def before_run(fixture):
            state.fixture = fixture
            fixture.server.process = state.process = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            raw_wait = state.process.wait

            def wait(*args, **kwargs):
                if threading.get_ident() == owner_ident and not args and not kwargs:
                    if not state.process_wait_errors:
                        state.process_wait_errors.append(process_interrupt)
                        raise process_interrupt
                    state.events.append("process.wait.blocking")
                    state.process_wait.set()
                    result = raw_wait()
                    state.events.append("process.wait.completed")
                    return result
                return raw_wait(*args, **kwargs)

            state.process.wait = wait

            def stop():
                state.events.append("server.stop.error")
                fixture.server.process = None
                raise stop_error  # Child stays alive until the proof thread ends it externally.

            fixture.server.stop.side_effect = stop
            configured = bootstrap.DataSourceConfig.load.return_value
            bootstrap.DataSourceConfig.load.side_effect = (
                configured, DataSourceSettings("personal_server", "https://fixture.invalid", "fixture", False, True))
            bootstrap.QApplication.side_effect = fail_setup

        def close_lease(lease):
            if state.failed and not lease._exclusive:
                state.lease_snapshots.append({
                    "observer_alive": tuple(thread.is_alive() for thread in state.pair[2]),
                    "content_alive": state.content_thread.is_alive(),
                    "process_returncode": state.process.poll(),
                    "server_stops": state.fixture.server.stop.call_count})
                state.events.append("lease.close")
            return raw_lease_close(lease)

        def check(fixture):
            # First baseline oracle is actual liveness before fixture/raw cleanup.
            self.assertFalse(state.pair[2][0].is_alive(), repr(state.lease_snapshots))
            self.assertFalse(state.pair[2][1].is_alive(), repr(state.lease_snapshots))
            self.assertTrue(state.proof_done.wait(4))
            self.assertEqual([], state.proof_errors)
            self.assertEqual([{"observer_alive": (False, False), "content_alive": False,
                "process_returncode": 0, "server_stops": 1}], state.lease_snapshots)
            self.assertIs(setup_error, fixture.exit_error)
            self.assertIs(close_error, fixture.exit_error.__cause__)
            cleanup = fixture.exit_error.api_runtime_observer_cleanup
            self.assertIs(state.pair[0], cleanup[0][0])
            self.assertIs(close_error, cleanup[0][1])
            self.assertIsNone(cleanup[0][2])
            self.assertIs(getter_error, cleanup[0][3])
            self.assertIs(state.pair[1], cleanup[1][0])
            self.assertTrue(cleanup[1][2].worker_stopped)
            completions = fixture.exit_error.api_runtime_observer_completion
            self.assertEqual(2, len(completions))
            for observer, completion, completion_error in completions:
                self.assertIs(observer, completion.owner)
                self.assertIs(True, completion.native_completed)
                self.assertIsNone(completion_error)
            self.assertIs(state.pair[0], completions[0][0])
            self.assertIs(rest_interrupts[0], completions[0][1].wait_error)
            self.assertIs(state.pair[1], completions[1][0])
            self.assertIsNone(completions[1][1].wait_error)
            self.assertEqual(list(rest_interrupts), state.rest_wait_errors)
            self.assertEqual(list(content_interrupts), state.content_wait_errors)
            self.assertEqual([process_interrupt], state.process_wait_errors)
            source = fixture.exit_error.desktop_setup_cleanup
            self.assertEqual(("close", "join", "stop"), tuple(item[1] for item in source))
            self.assertIs(content_interrupts[0], source[1][2])
            self.assertIs(stop_error, source[2][2])
            expected_order = ("proof.rest.held", "rest.join.completed", "proof.content.held",
                "content.join.completed", "server.stop.error", "proof.process.held",
                "process.wait.completed", "lease.close")
            positions = [state.events.index(item) for item in expected_order]
            self.assertEqual(sorted(positions), positions)
            for close in state.borrowed: close.assert_not_called()
            for guard in state.no_queue_waits: guard.assert_not_called()
            self.assertEqual(0, state.pair[0].validation_summary()["total_observations"])
            self.assertEqual(0, state.pair[1].validation_summary()["total_observations"])
            self.assertTrue(fixture.restore_available())
            bootstrap.MainWindow.assert_not_called()

        try:
            with ExitStack() as stack:
                stack.enter_context(patch.object(bootstrap.LocalApiConfig, "load",
                    return_value=KiwoomSettings("fixture-key", "fixture-secret", "mock")))
                stack.enter_context(patch.object(validation_client, "RealtimeValidationRecorder", side_effect=recorder))
                stack.enter_context(patch.object(threading.Thread, "join", thread_join))
                stack.enter_context(patch.object(AppDataLease, "close", close_lease))
                self._bootstrap_settings_desktop(
                    lambda _fixture: self.fail("pre-window failure reached event loop"), after_exit=check,
                    content_service=service, expected_error=RuntimeError, before_run=before_run,
                    query_client_factory=factory)
        finally:
            state.abort.set(); state.rest_release.set()
            state.rest_wait.set(); state.rest_tail.set(); state.content_wait.set(); state.process_wait.set()
            if state.fixture is not None: state.fixture.release.set()
            if state.process is not None and state.process.stdin is not None and not state.process.stdin.closed:
                state.process.stdin.close()
            if state.proof is not None:
                raw_join(state.proof, 3)
                self.assertFalse(state.proof.is_alive())
            if state.pair is not None:
                for raw_close in state.pair[3]:
                    try:
                        raw_close()
                    except BaseException as error:
                        # A terminal owner consistently re-raises its original
                        # deferred wait error, after the liveness oracle above.
                        self.assertIs(rest_interrupts[0], error)
                for thread in state.pair[2]:
                    raw_join(thread, 3)
                    self.assertFalse(thread.is_alive())
            if state.process is not None:
                state.process.wait(timeout=3)

    def test_bootstrap_theme_retry_active_run_drains_after_wait_interruptions_before_source_lease(self):
        import threading
        from contextlib import ExitStack
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
        from kiwoom_monitor.infrastructure.kiwoom_rest import validation_client
        from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease

        setup_error = RuntimeError("fixture QApplication failure while theme retry is active")
        seed_error = OSError("fixture first theme publish schedules existing retry")
        wait_errors = (KeyboardInterrupt("first active theme drain interruption"),
                       SystemExit("second active theme drain interruption"))
        state = SimpleNamespace(fixture=None, dispatcher=None, pair=None, failed=False,
            theme_entered=Event(), theme_release=Event(), theme_returned=Event(), drain_wait=Event(),
            proof_done=Event(), abort=Event(), proof=None, proof_errors=[], wait_errors=[],
            calls=0, events=[], lease_snapshots=[])
        real_recorder_class = validation_client.RealtimeValidationRecorder
        real_dispatcher_class = bootstrap.CentralThemeSyncDispatcher
        real_thread, raw_thread_join = threading.Thread, threading.Thread.join
        raw_lease_close = AppDataLease.close
        service = Mock()
        service.push.return_value = SimpleNamespace(total=0)
        owner_ident = threading.get_ident()

        def replace_themes(*_args, **_kwargs):
            state.calls += 1
            if state.calls == 1:
                raise seed_error  # Existing default 2-second retry; no new policy/config.
            state.theme_thread = threading.current_thread()
            state.theme_entered.set()
            try:
                if not state.theme_release.wait(4):
                    raise RuntimeError("active theme fixture was not released")
                return SimpleNamespace(pending_collections=())
            finally:
                state.theme_returned.set()

        service.replace_themes.side_effect = replace_themes

        def pull(*_args, **_kwargs):
            state.content_thread = threading.current_thread()
            state.fixture.completed.set()
            return SimpleNamespace(total=0)

        service.pull.side_effect = pull

        def dispatcher(*args, **kwargs):
            value = real_dispatcher_class(*args, **kwargs)
            state.dispatcher = value
            state.raw_theme_wait = value._runs_drained.wait

            def wait(*args, **kwargs):
                if threading.get_ident() == owner_ident and state.failed:
                    if len(state.wait_errors) < len(wait_errors):
                        error = wait_errors[len(state.wait_errors)]
                        state.wait_errors.append(error)
                        raise error
                    state.drain_wait.set()
                return state.raw_theme_wait(*args, **kwargs)

            value._runs_drained.wait = wait
            return value

        def factory(*_args, **_kwargs):
            # Keep V2's actual Recorder constructor wiring and exact two owners.
            with patch.object(validation_client, "RealtimeValidationRecorder", real_recorder_class):
                state.pair = self._observer_owner_real_pair(state.fixture.paths.data_dir, "theme-drain")
            now = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
            for borrowed in (state.pair[0]._primary, state.pair[0]._local):
                borrowed.server_now = lambda: now
                borrowed.get_access_token = Mock(side_effect=AssertionError("unexpected token query"))
            return state.pair[0]

        def recorder(*_args, **_kwargs):
            return state.pair[1]

        def proof():
            try:
                if not state.drain_wait.wait(4):
                    raise AssertionError("real active-run drain wait was not entered")
                if state.abort.is_set(): return
                self.assertTrue(state.theme_thread.is_alive())
                self.assertFalse(state.theme_returned.is_set())
                with state.dispatcher._state_lock:
                    self.assertEqual(1, state.dispatcher._active_runs)
                self.assertFalse(any(thread.is_alive() for thread in state.pair[2]))
                self.assertFalse(state.content_thread.is_alive())
                self.assertFalse(state.fixture.restore_available())
                state.fixture.server.stop.assert_not_called()
                self.assertEqual([], state.lease_snapshots)
                state.events.append("proof.theme.active.lease.held")
            except BaseException as error:
                state.proof_errors.append(error)
            finally:
                state.theme_release.set()
                state.proof_done.set()

        def fail_setup(*_args, **_kwargs):
            self.assertTrue(state.theme_entered.wait(4))
            self.assertTrue(state.theme_thread.is_alive())
            # Isolate the independent retry timer: content is a completed test
            # precondition here, while the separate native/source oracle keeps
            # content live through the actual product join.
            raw_thread_join(state.content_thread, 3)
            self.assertFalse(state.content_thread.is_alive())
            self.assertFalse(state.fixture.restore_available())
            state.failed = True
            state.proof = real_thread(target=proof, name="c10-theme-proof")
            state.proof.start()
            raise setup_error

        def before_run(fixture):
            state.fixture = fixture
            configured = bootstrap.DataSourceConfig.load.return_value
            bootstrap.DataSourceConfig.load.side_effect = (
                configured, DataSourceSettings("personal_server", "https://fixture.invalid", "fixture", False, True))
            bootstrap.QApplication.side_effect = fail_setup

        def close_lease(lease):
            if state.failed and not lease._exclusive:
                with state.dispatcher._state_lock:
                    active = state.dispatcher._active_runs
                state.lease_snapshots.append({"theme_active_runs": active,
                    "theme_service_returned": state.theme_returned.is_set(),
                    "observer_alive": tuple(thread.is_alive() for thread in state.pair[2]),
                    "content_alive": state.content_thread.is_alive(),
                    "server_stops": state.fixture.server.stop.call_count})
                state.events.append("lease.close")
            return raw_lease_close(lease)

        def check(fixture):
            # First V4 counterexample oracle is real service work, before test
            # release/cleanup. Do not infer completion from an exception tuple.
            self.assertTrue(state.theme_returned.is_set(), repr(state.lease_snapshots))
            with state.dispatcher._state_lock:
                self.assertEqual(0, state.dispatcher._active_runs)
            self.assertTrue(state.proof_done.wait(4))
            self.assertEqual([], state.proof_errors)
            self.assertEqual([{"theme_active_runs": 0, "theme_service_returned": True,
                "observer_alive": (False, False), "content_alive": False,
                "server_stops": 1}], state.lease_snapshots)
            self.assertIs(setup_error, fixture.exit_error)
            self.assertIs(wait_errors[0], fixture.exit_error.__cause__)
            self.assertEqual(list(wait_errors), state.wait_errors)
            self.assertEqual(2, state.calls)  # Accepted retry is attempted once; no report retry.
            source = fixture.exit_error.desktop_setup_cleanup
            self.assertIs(state.dispatcher, source[0][0])
            self.assertIs(wait_errors[0], source[0][2])
            self.assertIsNone(source[1][2])
            self.assertIsNone(source[2][2])
            self.assertLess(state.events.index("proof.theme.active.lease.held"), state.events.index("lease.close"))
            for observer, completion, completion_error in fixture.exit_error.api_runtime_observer_completion:
                self.assertIs(observer, completion.owner)
                self.assertIs(True, completion.native_completed)
                self.assertIsNone(completion_error)
            self.assertTrue(fixture.restore_available())
            bootstrap.MainWindow.assert_not_called()
            # No new policy requires joining the Timer's native tail before the
            # lease. The contract here is the existing real active-run drain.

        try:
            with ExitStack() as stack:
                stack.enter_context(patch.object(bootstrap.LocalApiConfig, "load",
                    return_value=KiwoomSettings("fixture-key", "fixture-secret", "mock")))
                stack.enter_context(patch.object(bootstrap, "CentralThemeSyncDispatcher", side_effect=dispatcher))
                stack.enter_context(patch.object(validation_client, "RealtimeValidationRecorder", side_effect=recorder))
                stack.enter_context(patch.object(AppDataLease, "close", close_lease))
                self._bootstrap_settings_desktop(
                    lambda _fixture: self.fail("pre-window failure reached event loop"), after_exit=check,
                    content_service=service, expected_error=RuntimeError, before_run=before_run,
                    query_client_factory=factory)
        finally:
            state.abort.set(); state.theme_release.set(); state.drain_wait.set()
            if state.proof is not None:
                raw_thread_join(state.proof, 3)
                self.assertFalse(state.proof.is_alive())
            if state.dispatcher is not None:
                state.dispatcher._runs_drained.wait = state.raw_theme_wait
                state.dispatcher.close()
            if hasattr(state, "theme_thread"):
                raw_thread_join(state.theme_thread, 3)
                self.assertFalse(state.theme_thread.is_alive())
            if state.pair is not None:
                for raw_close in state.pair[3]: raw_close()
                for thread in state.pair[2]:
                    raw_thread_join(thread, 3)
                    self.assertFalse(thread.is_alive())

    def test_current_observers_reuse_one_terminal_native_result_after_two_finite_wait_faults(self):
        import threading
        from kiwoom_monitor.presentation.app_controller import _ApiClientRetirementResult

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        rest, recorder, threads, raw_closes = self._observer_owner_real_pair(root, "terminal-result")
        controller = None
        resources = tuple(zip((rest, recorder), threads))
        waits = {id(observer): [] for observer, _thread in resources}
        first = {id(observer): KeyboardInterrupt("first " + kind + " native wait")
                 for (observer, _thread), kind in zip(resources, ("rest", "recorder"))}
        second = {id(observer): OSError("second " + kind + " native wait")
                  for (observer, _thread), kind in zip(resources, ("rest", "recorder"))}
        real_join = threading.Thread.join
        for observer, thread in resources:
            def join(*args, observer=observer, thread=thread, **kwargs):
                if not args and not kwargs:
                    calls = waits[id(observer)]
                    calls.append(len(calls) + 1)
                    if len(calls) == 1:
                        raise first[id(observer)]
                    if len(calls) == 3:
                        # This is finite, valid native-wait failure, not code
                        # corruption. A second drain must not be started at all.
                        raise second[id(observer)]
                return real_join(thread, *args, **kwargs)
            thread.join = join
        now = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
        for borrowed in (rest._primary, rest._local):
            borrowed.server_now = lambda: now
            borrowed.get_access_token = Mock(side_effect=AssertionError("unexpected token query"))
        events = []
        try:
            controller = self.controller(events, market_data_client=rest, owned_observers=(rest, recorder))
            self.assertFalse(controller.request_close())
            self.wait_until(lambda: len(controller._api_client_retirements) == 2 and all(
                item.result is not None and item.native_result is True
                for item in controller._api_client_retirements))
            # Actual observer and Qt task completion precede semantic assertions.
            self.assertFalse(any(thread.is_alive() for thread in threads))
            records = {id(item.client): item for item in controller._api_client_retirements}
            self.assertEqual({id(rest), id(recorder)}, set(records))
            for observer, _thread in resources:
                item = records[id(observer)]
                self.assertIsInstance(item.result, _ApiClientRetirementResult)
                self.assertIs(first[id(observer)], item.result.error)
                self.assertEqual("close", item.result.error_stage)
                self.assertEqual([1, 2], waits[id(observer)])
                completion = item.result.completion
                self.assertIs(observer, completion.owner)
                self.assertIs(True, completion.native_completed)
                self.assertIs(first[id(observer)], completion.wait_error)
                self.assertIsNone(item.result.completion_error)
                self.assertIs(completion, observer.finish_comparison_shutdown())
                self.assertTrue(controller._api_client_stop_confirmed(item))
            self.wait_until(lambda: controller._shutdown_stage == "ready")
            self.assertEqual(1, events.count("ready"))
        finally:
            for raw_close in raw_closes:
                try:
                    raw_close()
                except BaseException as error:
                    self.assertTrue(any(error is value for value in first.values()))
            for thread in threads:
                real_join(thread, 3)
                self.assertFalse(thread.is_alive())
            if controller is not None:
                self._drain_api_retirement_fixture(controller)

    def test_actual_api_reload_adopts_exact_inline_native_completion_without_erasing_getter_unknown(self):
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings

        def case(fixture, _invoke, state):
            controller = fixture.window._app_controller
            previous_client = controller.market_data_client
            assembly_error = RuntimeError("fixture late actual factory assembly failure")
            legacy_error = PermissionError("fixture legacy close before actual join")
            getter_error = RuntimeError("fixture independent getter UNKNOWN")
            source = DataSourceSettings("personal_server", "https://fixture.invalid", "fixture", False, True)
            bootstrap.DataSourceConfig.load.return_value = source
            failures = []
            controller.api_reload_failed.connect(failures.append)

            def fail_after_acquisition(*_args, **_kwargs):
                self.assertEqual(["rest", "recorder"], [resource.kind for resource in state.resources])
                rest = state.resources[0]
                self.assertTrue(all(resource.thread.is_alive() for resource in state.resources))
                self.assertFalse(fixture.restore_available())
                def legacy_close():
                    raise legacy_error
                def unknown_fact():
                    raise getter_error
                # Only legacy/fallible ports change. The real factory already
                # owns each exact public native completion capability.
                rest.observer.close = legacy_close
                rest.observer.comparison_lifecycle_state = unknown_fact
                raise assembly_error

            with patch.object(bootstrap, "StockRepository", side_effect=fail_after_acquisition), \
                    patch("kiwoom_monitor.presentation.main_window.QMessageBox.warning", return_value=None) as warning:
                controller.restart_for_api_settings()
                controller._advance_api_reload()  # existing worker -> queued signal fence
                controller._advance_api_reload()  # actual factory failure -> actual adopt caller
            warning.assert_called_once_with(
                fixture.window, "API 설정", "새 API 설정을 적용하지 못했습니다.\n" + str(assembly_error))
            # First baseline oracle is actual liveness before helper cleanup.
            self.assertFalse(state.resources[0].thread.is_alive())
            self.assertFalse(state.resources[1].thread.is_alive())
            self.assertEqual([str(assembly_error)], failures)
            self.assertFalse(controller.api_reloading)
            self.assertIs(previous_client, controller.market_data_client)
            original = assembly_error.api_runtime_observer_cleanup
            self.assertIs(legacy_error, original[0][1])
            self.assertIsNone(original[0][2])
            self.assertIs(getter_error, original[0][3])
            completion_rows = assembly_error.api_runtime_observer_completion
            self.assertEqual(2, len(completion_rows))
            rest = state.resources[0].observer
            record = next(item for item in controller._api_client_retirements if item.client is rest)
            self.assertIs(assembly_error, record.error)
            self.assertTrue(record.inline_cleanup)
            self.assertIsNone(record.worker)  # No invented Qt join for inline completion.
            self.assertIs(legacy_error, record.result.error)
            self.assertIs(getter_error, record.result.observation_error)
            self.assertIsNone(record.result.lifecycle)
            self.assertIs(completion_rows[0][1], record.result.completion)
            self.assertIs(rest, record.result.completion.owner)
            self.assertIs(True, record.result.completion.native_completed)
            self.assertIsNone(record.result.completion_error)
            self.assertTrue(controller._api_client_stop_confirmed(record))
            state.old_close.assert_not_called()
            fixture.window.close()
            self.wait_until(lambda: controller._shutdown_stage == "ready")
            self.assertFalse(fixture.restore_available())  # outer bootstrap still owns its lease

        self._run_observer_factory_probe(case)

    def test_current_native_completion_retains_getter_only_unknown_result_after_ready(self):
        import threading

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        rest, recorder, threads, raw_closes = self._observer_owner_real_pair(root, "getter-only-unknown")
        getter_error = RuntimeError("fixture getter-only UNKNOWN after actual completion")
        controller = None
        now = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
        for borrowed in (rest._primary, rest._local):
            borrowed.server_now = lambda: now
            borrowed.get_access_token = Mock(side_effect=AssertionError("unexpected token query"))
        def unknown_fact():
            raise getter_error
        rest.comparison_lifecycle_state = unknown_fact
        events = []
        try:
            controller = self.controller(events, market_data_client=rest, owned_observers=(rest, recorder))
            self.assertFalse(controller.request_close())
            self.wait_until(lambda: controller._shutdown_stage == "ready")
            self.assertFalse(any(thread.is_alive() for thread in threads))
            records = [item for item in controller._api_client_retirements if item.client is rest]
            self.assertEqual(1, len(records), "actual proof must release the gate while retaining getter UNKNOWN")
            record = records[0]
            result = record.result
            self.assertIsNone(result.error)
            self.assertIsNone(result.lifecycle)
            self.assertIs(getter_error, result.observation_error)
            self.assertIsNone(result.completion_error)
            self.assertIs(rest, result.completion.owner)
            self.assertIs(True, result.completion.native_completed)
            self.assertIsNone(result.completion.wait_error)
            self.assertTrue(controller._api_client_stop_confirmed(record))
            self.assertIs(True, record.native_result)
            self.assertIsNone(record.worker)
            controller._settle_api_client_retirement(record)
            self.assertIs(record, next(item for item in controller._api_client_retirements if item.client is rest))
            self.assertIs(result, record.result)
            self.assertIs(getter_error, record.result.observation_error)
            self.assertEqual(1, events.count("ready"))
        finally:
            for raw_close in raw_closes: raw_close()
            for thread in threads:
                threading.Thread.join(thread, 3)
                self.assertFalse(thread.is_alive())
            if controller is not None:
                self._drain_api_retirement_fixture(controller)


    def test_constructor_keeps_native_writers_and_persisted_alert_inactive_until_activation(self):
        from PySide6.QtCore import QSettings
        candidate_settings = QSettings("KiwoomMonitor", "CandidateMonitor")
        previous = candidate_settings.value("alert_enabled")
        candidate_settings.setValue("alert_enabled", "1")
        entered = Event()
        calls = []
        def load_events(**_kwargs):
            calls.append(QThread.currentThread())
            entered.set()
            return {"events": [], "quality": {"status": "READY"}, "high_watermark": 0,
                    "next_cursor": None, "has_more": False}
        window = None
        try:
            with tempfile.TemporaryDirectory() as directory, \
                 patch.object(MainWindow, "_open_api_settings"), \
                 patch.object(MainWindow, "_start_top20_market_repair"):
                root = Path(directory)
                database = Database(root / "monitor.sqlite3")
                database.initialize()
                window = MainWindow(database.settings, monitor_database_path=root / "monitor.sqlite3",
                    journal_database_path=root / "journal.sqlite3", candidate_client=SimpleNamespace(load_candidate_events=load_events),
                    research_data_dir=root / "research")
                try:
                    self.app.processEvents()
                    self.assertFalse(window._market_cache_writer.isRunning())
                    self.assertFalse(window._entry_snapshot_writer.isRunning())
                    self.assertTrue(window._candidate_dialog._alert_enabled.isChecked())
                    self.assertFalse(window._candidate_dialog._worker.isRunning())
                    self.assertEqual([], calls)
                    self.assertIsNotNone(window._app_controller._shutdown_actions)
                    window.activate_startup()
                    self.assertTrue(entered.wait(3))
                    self.assertTrue(window._market_cache_writer.isRunning())
                    self.assertTrue(window._entry_snapshot_writer.isRunning())
                    self.assertIsNot(calls[0], self.app.thread())
                    window.activate_startup()  # Admission is one-time, never a second writer start.
                    self.assertEqual({"market_cache_writer", "entry_snapshot_writer"}, window._app_controller._started_writers)
                finally:
                    window.close()
                    self.wait_until(window._app_controller.request_close)
                    self.assertFalse(window._market_cache_writer.isRunning())
                    self.assertFalse(window._entry_snapshot_writer.isRunning())
                    self.assertFalse(window._candidate_dialog._worker.isRunning())
                    window.deleteLater()
                    self.app.processEvents()
        finally:
            if previous is None:
                candidate_settings.remove("alert_enabled")
            else:
                candidate_settings.setValue("alert_enabled", previous)

    def test_partial_constructor_failure_leaves_only_inactive_owned_children(self):
        from PySide6.QtCore import QSettings
        candidate_settings = QSettings("KiwoomMonitor", "CandidateMonitor")
        previous = candidate_settings.value("alert_enabled")
        candidate_settings.setValue("alert_enabled", "1")
        failure = RuntimeError("independent late construction failure")
        partial = []
        calls = []
        def fail(window, *args, **kwargs):
            partial.append(window)
            raise failure
        def load_events(**_kwargs):
            calls.append(True)
            return {"events": [], "quality": {"status": "READY"}, "high_watermark": 0}
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                database = Database(root / "monitor.sqlite3")
                database.initialize()
                try:
                    with patch.object(MainWindow, "_apply_table_visuals", fail):
                        with self.assertRaises(RuntimeError) as caught:
                            MainWindow(database.settings, monitor_database_path=root / "monitor.sqlite3",
                                journal_database_path=root / "journal.sqlite3", candidate_client=SimpleNamespace(load_candidate_events=load_events),
                                research_data_dir=root / "research")
                    self.assertIs(failure, caught.exception)
                    self.assertEqual(1, len(partial))
                    window = partial[0]
                    self.assertIsNone(window._app_controller._shutdown_actions)
                    self.assertFalse(window._market_cache_writer.isRunning())
                    self.assertFalse(window._entry_snapshot_writer.isRunning())
                    self.assertFalse(window._candidate_dialog._worker.isRunning())
                    self.app.processEvents()
                    self.assertEqual([], calls)
                    self.assertFalse(window._candidate_dialog._worker.isRunning())
                finally:
                    # Before-source safety cleanup is not product ownership evidence.
                    for window in partial:
                        for timer in window.findChildren(QTimer):
                            timer.stop()
                        for dialog in (window._candidate_dialog, window._research_dialog):
                            if dialog is not None:
                                dialog.stop()
                        window._entry_snapshot_writer.requestInterruption()
                        window._entry_snapshot_writer.wait()
                        window._market_cache_writer.stop_and_drain()
                        window._market_cache_writer.wait()
                        window.deleteLater()
                    self.app.processEvents()
        finally:
            if previous is None:
                candidate_settings.remove("alert_enabled")
            else:
                candidate_settings.setValue("alert_enabled", previous)



    def test_bootstrap_constructor_failure_closes_fresh_observers_before_source_lease(self):
        import threading
        from contextlib import ExitStack
        from PySide6.QtCore import QSettings
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
        from kiwoom_monitor.infrastructure.kiwoom_rest import validation_client
        from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease

        # Reuse the actual bootstrap/content/lease fixture and the existing
        # real observer-pair helper. Assertions run BEFORE either safety cleanup.
        settings = QSettings("KiwoomMonitor", "CandidateMonitor")
        previous_alert = settings.value("alert_enabled")
        settings.setValue("alert_enabled", "1")
        original_controller_init = AppController.__init__
        original_join = threading.Thread.join
        original_lease_close = AppDataLease.close
        original_theme_close = bootstrap.CentralThemeSyncDispatcher.close
        now = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
        try:
            for stage in ("before_controller", "late_ui"):
                with self.subTest(stage=stage):
                    original_error = RuntimeError("fixture actual constructor " + stage)
                    state = SimpleNamespace(resources=[], borrowed=[], partials=[], controllers=[],
                        fixture=None, failed=False, stages=[], events=[], leases=[], constructor_kwargs=[],
                        joins=[], theme_owners=[])
                    transport = Mock(side_effect=AssertionError("constructor fixture must not query"))
                    service = Mock()
                    service.replace_themes.return_value = SimpleNamespace(pending_collections=())
                    service.push.return_value = SimpleNamespace(total=0)

                    def factory(*_args, **_kwargs):
                        rest, recorder, threads, raw_closes = self._observer_owner_real_pair(
                            state.fixture.paths.data_dir, "constructor-" + stage)
                        for observer, thread, raw_close, kind in zip(
                                (rest, recorder), threads, raw_closes, ("rest", "recorder")):
                            resource = SimpleNamespace(observer=observer, thread=thread, kind=kind,
                                raw_close=raw_close, raw_fact=observer.comparison_lifecycle_state,
                                raw_finish=observer.finish_comparison_shutdown, receipts=[])
                            state.resources.append(resource)

                            def close(resource=resource):
                                state.events.append(resource.kind + ".close")
                                resource.raw_close()

                            def fact(resource=resource):
                                state.events.append(resource.kind + ".fact")
                                return resource.raw_fact()

                            def finish(resource=resource):
                                state.events.append(resource.kind + ".finish")
                                receipt = resource.raw_finish()
                                resource.receipts.append(receipt)
                                return receipt

                            observer.close = close
                            observer.comparison_lifecycle_state = fact
                            observer.finish_comparison_shutdown = finish
                        for borrowed in (rest._primary, rest._local):
                            borrowed.server_now = lambda: now
                            borrowed.get_access_token = transport
                            borrowed.close = Mock()
                            state.borrowed.append(borrowed.close)
                        # The pair helper has already created the real recorder.
                        # Reuse that exact owner when bootstrap imports its ctor.
                        stack.enter_context(patch.object(validation_client,
                            "RealtimeValidationRecorder", return_value=recorder))
                        return rest

                    def pull(*_args, **_kwargs):
                        state.content_thread = threading.current_thread()
                        state.fixture.entered.set()
                        try:
                            if not state.fixture.release.wait(4):
                                raise RuntimeError("content HTTP fixture was not released")
                            return SimpleNamespace(total=0)
                        finally:
                            state.fixture.completed.set()
                    service.pull.side_effect = pull

                    def fail_constructor():
                        self.assertEqual(2, len(state.resources))
                        self.assertTrue(state.fixture.entered.wait(4))
                        self.assertTrue(state.content_thread.is_alive())
                        self.assertTrue(all(item.thread.is_alive() for item in state.resources))
                        self.assertFalse(state.fixture.restore_available())
                        state.fixture.server.stop.assert_not_called()
                        state.events.append("constructor.failure")
                        state.failed = True
                        raise original_error

                    def controller_init(controller, parent=None, *args, **kwargs):
                        state.partials.append(parent)  # Exact partial QObject, only for later safety cleanup.
                        state.stages.append("controller.enter")
                        if stage == "before_controller":
                            fail_constructor()
                        original_controller_init(controller, parent, *args, **kwargs)
                        state.controllers.append(controller)
                        state.stages.append("controller.return")

                    def late_ui(window, *_args, **_kwargs):
                        state.stages.append("ui.enter")
                        self.assertIs(state.partials[0], window)
                        fail_constructor()

                    def join(thread, *args, **kwargs):
                        if (thread is getattr(state, "content_thread", None)
                                and state.failed and not args and not kwargs):
                            state.events.append("content.join")
                            snapshot = dict(owner=thread, observer_alive=[item.thread.is_alive()
                                for item in state.resources], content_alive_before=thread.is_alive(),
                                server_stops=state.fixture.server.stop.call_count)
                            state.joins.append(snapshot)
                            state.fixture.release.set()  # Only the product's real join releases it.
                            result = original_join(thread, *args, **kwargs)
                            snapshot.update(result=result, content_alive_after=thread.is_alive(),
                                content_completed=state.fixture.completed.is_set())
                            return result
                        return original_join(thread, *args, **kwargs)

                    def theme_close(dispatcher):
                        if state.failed:
                            state.events.append("theme.close")
                            state.theme_owners.append(dispatcher)
                        return original_theme_close(dispatcher)

                    def close_lease(lease):
                        if state.failed and not lease._exclusive:
                            state.events.append("lease.close")
                            state.leases.append(dict(observer_alive=[item.thread.is_alive()
                                for item in state.resources], content_alive=state.content_thread.is_alive(),
                                server_stops=state.fixture.server.stop.call_count))
                        return original_lease_close(lease)

                    def before_run(fixture):
                        state.fixture = fixture
                        configured = bootstrap.DataSourceConfig.load.return_value
                        bootstrap.DataSourceConfig.load.side_effect = (configured,
                            DataSourceSettings("personal_server", "https://fixture.invalid", "fixture", False, True))
                        real_constructor_call = bootstrap.MainWindow.side_effect
                        def construct(*args, **kwargs):
                            state.constructor_kwargs.append(dict(kwargs))
                            return real_constructor_call(*args, **kwargs)
                        bootstrap.MainWindow.side_effect = construct
                        fixture.server.stop.side_effect = lambda: state.events.append("server.stop")

                    def check(fixture):
                        # Prove the intended actual constructor boundary first;
                        # an unrelated early exception is not causal evidence.
                        self.assertIs(original_error, fixture.exit_error)
                        expected_stages = (["controller.enter"] if stage == "before_controller"
                            else ["controller.enter", "controller.return", "ui.enter"])
                        self.assertEqual(expected_stages, state.stages)
                        bootstrap.MainWindow.assert_called_once()
                        self.assertEqual(1, len(state.constructor_kwargs))
                        # An old source fails first on actual owned threads here;
                        # fixture release/join/raw_close cannot create a PASS.
                        self.assertEqual(2, len(state.resources))
                        for resource in state.resources:
                            self.assertFalse(resource.thread.is_alive(),
                                "pre-test-cleanup actual " + resource.kind + " remains alive")
                        self.assertIsNone(fixture.exit_error.__cause__)
                        kwargs = state.constructor_kwargs[0]
                        self.assertNotIn("initial_google_drive_download", kwargs)
                        self.assertIs(state.resources[0].observer, kwargs["market_data_client"])
                        self.assertEqual(tuple(item.observer for item in state.resources), kwargs["owned_observers"])
                        self.assertEqual([], fixture.windows)  # Failed RHS was never published.
                        self.assertEqual(1, len(state.partials))
                        if stage == "before_controller":
                            self.assertFalse(hasattr(state.partials[0], "_app_controller"))
                            self.assertEqual([], state.controllers)
                        else:
                            window = state.partials[0]
                            self.assertIsNone(window._app_controller._shutdown_actions)
                            self.assertFalse(window._market_cache_writer.isRunning())
                            self.assertFalse(window._entry_snapshot_writer.isRunning())
                            self.assertTrue(window._candidate_dialog._alert_enabled.isChecked())
                            self.assertFalse(window._candidate_dialog._worker.isRunning())
                            self.assertFalse(any(getattr(callback, "__self__", None) is window._research_dialog
                                for _delay, callback in fixture.callbacks))
                        for borrowed_close in state.borrowed:
                            borrowed_close.assert_not_called()
                        cleanup = fixture.exit_error.api_runtime_observer_cleanup
                        completions = fixture.exit_error.api_runtime_observer_completion
                        self.assertEqual(2, len(cleanup))
                        self.assertEqual(2, len(completions))
                        for resource, row, completion_row in zip(state.resources, cleanup, completions):
                            observer, close_error, fact, read_error = row
                            self.assertIs(resource.observer, observer)
                            self.assertIsNone(close_error)
                            self.assertIsNone(read_error)
                            self.assertEqual((True, 0, True, None), (fact.admission_closed,
                                fact.active_requests, fact.worker_stopped, fact.failure_stage))
                            owner, completion, completion_error = completion_row
                            self.assertIs(resource.observer, owner)
                            self.assertIs(resource.observer, completion.owner)
                            self.assertIs(True, completion.native_completed)
                            self.assertIsNone(completion.wait_error)
                            self.assertIsNone(completion_error)
                            self.assertEqual(1, state.events.count(resource.kind + ".close"))
                            self.assertEqual(1, state.events.count(resource.kind + ".fact"))
                            # Normal public close + retained exact capability share one receipt.
                            self.assertEqual(2, len(resource.receipts))
                            self.assertIs(resource.receipts[0], resource.receipts[1])
                            self.assertIs(completion, resource.receipts[-1])
                        self.assertFalse(state.content_thread.is_alive())
                        # Unchanged source cleanup does a generic join and then
                        # its exact native-completion join. Observe both real calls.
                        self.assertEqual(2, len(state.joins))
                        self.assertEqual([True, False], [row["content_alive_before"] for row in state.joins])
                        for row in state.joins:
                            self.assertIs(state.content_thread, row["owner"])
                            self.assertEqual([False, False], row["observer_alive"])
                            self.assertEqual(0, row["server_stops"])
                            self.assertIsNone(row["result"])
                            self.assertFalse(row["content_alive_after"])
                            self.assertTrue(row["content_completed"])
                        self.assertEqual([dict(observer_alive=[False, False], content_alive=False,
                            server_stops=1)], state.leases)
                        # Constructor errors do not expose the pre-window
                        # desktop_setup_cleanup tuple. Use exact actual owners.
                        self.assertEqual(1, len(state.theme_owners))
                        self.assertIsInstance(state.theme_owners[0], bootstrap.CentralThemeSyncDispatcher)
                        fixture.server.stop.assert_called_once_with()
                        self.assertEqual(["constructor.failure", "rest.close", "rest.finish", "rest.fact",
                            "recorder.close", "recorder.finish", "recorder.fact", "rest.finish",
                            "recorder.finish", "theme.close", "content.join", "content.join", "server.stop", "lease.close"],
                            state.events)
                        self.assertTrue(fixture.restore_available())
                        transport.assert_not_called()

                    try:
                        with ExitStack() as stack:
                            stack.enter_context(patch.object(bootstrap.LocalApiConfig, "load",
                                return_value=KiwoomSettings("fixture-key", "fixture-secret", "mock")))
                            stack.enter_context(patch.object(AppController, "__init__", controller_init))
                            if stage == "late_ui":
                                stack.enter_context(patch.object(MainWindow, "_apply_table_visuals", late_ui))
                            stack.enter_context(patch.object(threading.Thread, "join", join))
                            stack.enter_context(patch.object(AppDataLease, "close", close_lease))
                            stack.enter_context(patch.object(bootstrap.CentralThemeSyncDispatcher, "close", theme_close))
                            self._bootstrap_settings_desktop(
                                lambda _fixture: self.fail("constructor failure reached event loop"),
                                after_exit=check, content_service=service, expected_error=RuntimeError,
                                before_run=before_run, query_client_factory=factory)
                    finally:
                        # Safety only, AFTER check: no broad discovery or product-success inference.
                        for resource in state.resources:
                            resource.raw_close()
                            original_join(resource.thread, 3)
                            self.assertFalse(resource.thread.is_alive())
                        for window in state.partials:
                            for timer in window.findChildren(QTimer):
                                timer.stop()
                            for name in ("_candidate_dialog", "_research_dialog"):
                                dialog = getattr(window, name, None)
                                if dialog is not None:
                                    dialog.stop()
                            for controller in state.controllers:
                                for writer in (controller.entry_snapshot_writer, controller.market_cache_writer):
                                    if writer is not None:
                                        writer.requestInterruption()
                                        if hasattr(writer, "stop_and_drain"):
                                            writer.stop_and_drain()
                                        writer.wait()
                            window.deleteLater()
                        self.app.processEvents()
        finally:
            if previous_alert is None:
                settings.remove("alert_enabled")
            else:
                settings.setValue("alert_enabled", previous_alert)

    def test_bootstrap_publishes_complete_window_then_forwards_drive_option_to_activation(self):
        from kiwoom_monitor import bootstrap

        # After-only phase-contract check. It deliberately stops at activation
        # admission; activation-midflight/full exec drain belongs to C12.
        for download, upload, local, uploaded, dirty, expected_download in (
            (False, False, "", "", "0", False),
            (True, False, "", "", "0", True),
            (False, True, "2026-10-16T06:00:00Z", "2026-10-16T14:00:00+09:00", "0", True),
            (False, True, "2026-10-16T14:00:00+09:00", "2026-10-16T06:00:00Z", "0", False),
            (False, True, "2026-10-16T14:00:00+09:00", "2026-10-16T05:00:00Z", "0", False),
            (False, True, "", "", "1", True),
            (False, False, "2026-10-16T06:00:00Z", "2026-10-16T14:00:00+09:00", "0", False),
        ):
            with self.subTest(download=download, upload=upload, local=local, uploaded=uploaded, dirty=dirty):
                failure = RuntimeError("fixture activation admission boundary")
                drive = Mock(connected=True, configured=True)
                state = SimpleNamespace(kwargs=[], activations=[], fixture=None)

                def before_run(fixture):
                    state.fixture = fixture
                    database = Database(fixture.paths.database_path)
                    database.initialize()
                    database.settings.set("google_drive_auto_download", "1" if download else "0")
                    database.settings.set("google_drive_auto_upload", "1" if upload else "0")
                    database.settings.set("google_drive_local_changed_at", local)
                    database.settings.set("google_drive_last_upload_success_at", uploaded)
                    database.settings.set("google_drive_unsynced_changes", dirty)
                    real_constructor_call = bootstrap.MainWindow.side_effect
                    def construct(*args, **kwargs):
                        state.kwargs.append(dict(kwargs))
                        return real_constructor_call(*args, **kwargs)
                    bootstrap.MainWindow.side_effect = construct

                def activate(window, *, initial_google_drive_download=False):
                    # Actual MainWindow ctor returned and helper published it
                    # before the real bootstrap caller entered this phase.
                    self.assertEqual([window], state.fixture.windows)
                    self.assertIsInstance(window, MainWindow)
                    self.assertIsNotNone(window._app_controller._shutdown_actions)
                    self.assertFalse(window._market_cache_writer.isRunning())
                    self.assertFalse(window._entry_snapshot_writer.isRunning())
                    state.activations.append((window, initial_google_drive_download))
                    raise failure

                def check(fixture):
                    self.assertIs(failure, fixture.exit_error)
                    bootstrap.MainWindow.assert_called_once()
                    self.assertEqual(1, len(state.kwargs))
                    self.assertNotIn("initial_google_drive_download", state.kwargs[0])
                    self.assertIs(drive, state.kwargs[0]["google_drive_sync"])
                    bootstrap.GoogleDriveSyncService.assert_called_once_with(
                        fixture.paths.database_path, fixture.paths.news_database_path,
                        backup_set="architecture-v1",
                    )
                    self.assertEqual(1, len(state.activations))
                    self.assertIs(fixture.windows[0], state.activations[0][0])
                    self.assertIs(expected_download, state.activations[0][1])

                with patch.object(bootstrap, "GoogleDriveSyncService", return_value=drive), \
                     patch.object(MainWindow, "activate_startup", activate):
                    self._bootstrap_settings_desktop(
                        lambda _fixture: self.fail("activation boundary reached event loop"),
                        after_exit=check, expected_error=RuntimeError, source_mode="local",
                        before_run=before_run, cleanup_window=True)


    def _c12_published_owner_native_case(self, mode):
        """C11 prerequisite; real Qt exec/dispatcher and native owners, no fake ACK."""
        import threading
        import sys
        import traceback
        import json
        from contextlib import ExitStack
        from dataclasses import replace
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
        from kiwoom_monitor.infrastructure.kiwoom_rest import validation_client
        from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings
        from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
        from kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer import EntrySnapshotWriter
        from kiwoom_monitor.infrastructure.persistence.journal_snapshot_repository import (
            TradeEntrySnapshot, scoped_snapshot_execution_key,
        )
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease

        native_wait_modes = {"producer_join_interrupt", "writer_join_interrupt"}
        native_modes = {"quit", "exec_error", "shutdown_effect_error", "dispatch_interrupts"} | native_wait_modes
        setup_error = (KeyboardInterrupt("C12 finite native wait " + mode) if mode in native_wait_modes
            else RuntimeError("C12 original " + mode))
        previous_cause = OSError("C12 original existing cause")
        source_error = PermissionError("C12 source close after actual stop")
        ack_error = RuntimeError("C12 actual enriched COMMIT ACK loss")
        dispatch_errors = (KeyboardInterrupt("C12 real dispatch wait interrupt one"),
            KeyboardInterrupt("C12 real dispatch wait interrupt two"))
        if mode == "show_error":
            setup_error.__cause__ = previous_cause
        fixed = datetime.fromisoformat("2030-01-02T10:00:00+09:00")
        scope = AccountScope("kiwoom", AccountEnvironment.MOCK,
            "00000000-0000-4000-8000-000000000042")
        snapshot = TradeEntrySnapshot(scoped_snapshot_execution_key("c12-" + mode, scope),
            "c12-order", "005930", "삼성전자", "매수", fixed, 70000, 1, "KRX",
            origin_scope=scope, theme_ranks={}, orderbook={}, market_state={}, capture_state="realtime_core")
        news = ({"title": "C12 original news", "published_at": "2030-01-02T09:59:00+09:00"},)
        expected = replace(snapshot, news=news, capture_state="realtime_enriched",
            investor_flow={"available": True, "source": "C12 original investor"})
        state = SimpleNamespace(fixture=None, pair=None, producer=None, timers=[], errors=[],
            events=[], snapshots=[], borrowed=[], native_execs=0, native_returns=0,
            producer_release=Event(), committed=Event(), ack_release=Event(), saves=[],
            failures=[], retry_count=0, prepared=False, content_thread=None, close_seen=False,
            dispatch_interrupts=[], real_dispatch_returns=0, in_shutdown_tick=False,
            wait_injected=False, wait_attempts=[], tick_snapshots=[], slot_errors=[], test_owned_wake=False,
            qt_hook_records=[], critical_calls=[], critical_modals=[], watcher_error_logged=False,
            retry_views=[], retry_requests=[], retry_native_starts=[], retry_close_returns=[],
            close_error_events=[], error_hook_visible=[])
        service = Mock()
        service.replace_themes.return_value = SimpleNamespace(pending_collections=())
        service.pull.return_value = SimpleNamespace(total=0)
        service.push.return_value = SimpleNamespace(total=0)
        ranking_start = Mock()
        original_recorder_class = validation_client.RealtimeValidationRecorder
        raw_save = JournalRepository.save_entry_snapshot
        raw_lease_close = AppDataLease.close
        raw_start = threading.Thread.start
        raw_exec = QApplication.exec
        raw_show = MainWindow.show
        raw_activate = MainWindow.activate_startup
        raw_start_entry = AppController.start_entry_snapshot_writer
        raw_request_close = AppController.request_close
        raw_close_event = MainWindow.closeEvent
        raw_entry_native_start = EntrySnapshotWriter.start
        raw_save_view = MainWindow._save_shutdown_view_state
        raw_process_events = QCoreApplication.processEvents
        raw_wait = QThread.wait
        raw_advance = AppController._advance_shutdown
        slot_notice = Mock()
        raw_critical = bootstrap.QMessageBox.critical

        def start(thread):
            if thread.name == "central-content-sync":
                state.content_thread = thread
            return raw_start(thread)

        def factory(*_args, **_kwargs):
            with patch.object(validation_client, "RealtimeValidationRecorder", original_recorder_class):
                state.pair = self._observer_owner_real_pair(state.fixture.paths.data_dir, "c12-" + mode)
            for borrowed in (state.pair[0]._primary, state.pair[0]._local):
                borrowed.server_now = lambda: fixed
                borrowed.close = Mock()
                state.borrowed.append(borrowed.close)
            self.assertFalse(state.fixture.restore_available())
            return state.pair[0]

        def save(owner, value, now=None, *, account_scope=None):
            result = raw_save(owner, value, now, account_scope=account_scope)
            state.saves.append((value, account_scope))
            if value.capture_state == "realtime_enriched" and not state.committed.is_set():
                state.events.append("entry.commit.before_ack")
                state.committed.set()
                if not state.ack_release.wait(3):
                    raise AssertionError("C12 native ACK barrier was not released")
                raise ack_error
            return result

        def sample(label):
            fixture = state.fixture
            controller = fixture.windows[0]._app_controller
            writer = controller.entry_snapshot_writer
            value = dict(label=label, stage=controller._shutdown_stage,
                producer_running=state.producer.isRunning(),
                producer_join=state.producer.wait(0), writer_running=writer.isRunning(),
                writer_join=writer.wait(0), pending=writer.has_pending_work(),
                observer_alive=tuple(thread.is_alive() for thread in state.pair[2]),
                content_alive=state.content_thread.is_alive(),
                server_stops=fixture.server.stop.call_count,
                restore_available=fixture.restore_available())
            state.snapshots.append(value)
            state.events.append(label)
            return value

        def timer(interval, callback, *, single=False):
            value = QTimer(self.app)
            value.setSingleShot(single)
            value.timeout.connect(callback)
            state.timers.append(value)
            value.start(interval)
            return value

        def prepare(window):
            if state.prepared:
                return
            state.prepared = True
            fixture = state.fixture
            fixture.window = window
            controller = window._app_controller
            writer = controller.entry_snapshot_writer
            state.sources = writer.replace_sources(
                investor_loader=lambda *_: {"available": True, "source": "C12 original investor"},
                program_loader=None)
            state.sources.__enter__()
            writer.failed.connect(state.failures.append)
            def retry_visible():
                # The product's existing Window slot runs before this observer.
                # No fixture show/request_close/save retry is performed here.
                state.retry_views.append((window.isVisible(), controller.closing,
                    controller._shutdown_stage,
                    tuple(value.isActive() for value in controller._shutdown_actions.timers)))
            retry_signal = getattr(controller, "shutdown_save_retry_needed", None)
            if retry_signal is not None:
                retry_signal.connect(retry_visible)
            # This actual QThread belongs to Window, outside Controller's tree.
            producer = DelayedProducer(window)
            producer.release = state.producer_release
            state.producer = producer
            def delivered():
                state.events.append("producer.queued_result")
                writer.enqueue(snapshot)
            producer.result.connect(delivered, Qt.ConnectionType.QueuedConnection)
            producer.start()
            self.assertTrue(producer.started_run.wait(2))

            def release_producer():
                try:
                    if mode in native_modes:
                        self.assertEqual(1, state.native_returns)
                    held = sample("after.exec.owner.held")
                    self.assertEqual(0, held["server_stops"])
                    self.assertFalse(held["restore_available"])
                    self.assertTrue(all(held["observer_alive"]))
                except BaseException as error:
                    state.errors.append(error)
                finally:
                    state.producer_release.set()
            timer(50, release_producer, single=True)
            started = time.monotonic()
            def observe_ack_and_retry():
                try:
                    if (mode in native_wait_modes and state.tick_snapshots
                            and not state.tick_snapshots[0]["timer_active"] and not state.test_owned_wake):
                        # Before cleanup, preserve the actual missing wake and
                        # still-owned native/source/lease facts. Recovery here
                        # is test-owned and must make the first oracle FAIL.
                        state.test_owned_wake = True
                        controller._shutdown_timer.start(0)
                    if state.committed.is_set() and not state.ack_release.is_set():
                        held = sample("entry.commit.ack.held")
                        self.assertTrue(held["writer_running"])
                        self.assertFalse(held["writer_join"])
                        self.assertEqual(0, held["server_stops"])
                        self.assertFalse(held["restore_available"])
                        state.ack_release.set()
                    if controller._shutdown_stage == "cache_failed" and not state.retry_count:
                        held = sample("entry.failed.before.explicit.retry")
                        self.assertTrue(held["pending"])
                        self.assertTrue(held["writer_join"])
                        self.assertEqual(0, held["server_stops"])
                        self.assertTrue(all(held["observer_alive"]))
                        self.assertFalse(held["restore_available"])
                        state.retry_count += 1
                        state.events.append("explicit.window.close.retry")
                        self.assertTrue(window.isVisible(), "manual save retry needs the actual existing visible Window")
                        state.retry_close_returns.append(window.close())
                        self.assertEqual([controller], state.retry_requests)
                        self.assertEqual([writer], state.retry_native_starts)
                    if controller._shutdown_stage == "ready":
                        watcher.stop()
                    elif time.monotonic() - started >= 4:
                        raise AssertionError("C12 same-owner shutdown did not reach ready within existing 4-second wait")
                except BaseException as error:
                    if not state.watcher_error_logged:
                        state.watcher_error_logged = True
                        traceback.print_exception(error)
                        def read(action):
                            try:
                                return action()
                            except BaseException as observation_error:
                                return {"UNKNOWN": type(observation_error).__name__ + ": " + str(observation_error)}
                        market, entry = controller.market_cache_writer, controller.entry_snapshot_writer
                        snapshot = {
                            "mode": mode, "stage": controller._shutdown_stage, "closing": controller.closing,
                            "started_writers": sorted(controller._started_writers),
                            "shutdown_timer_active": read(controller._shutdown_timer.isActive),
                            "shutdown_timer_remaining_ms": read(controller._shutdown_timer.remainingTime),
                            "watcher_active": read(watcher.isActive),
                            "market_running": read(market.isRunning) if market is not None else None,
                            "market_queue_size_observation_only": read(market._queue.qsize) if market is not None else None,
                            "entry_running": read(entry.isRunning) if entry is not None else None,
                            "entry_pending": read(entry.has_pending_work) if entry is not None else None,
                            "pending_prices": len(controller.pending_prices),
                            "pending_minutes": len(controller.pending_minutes),
                            "pending_market_minutes": len(controller.pending_market_minutes),
                            "pending_minute_gaps": len(controller.pending_minute_gaps),
                            "price_retry_batches": len(controller._price_cache_retries),
                            "pending_minute_retention": repr(controller.pending_minute_retention),
                            "shutdown_cache_failed": controller._shutdown_cache_failed,
                            "producer_release": state.producer_release.is_set(),
                            "committed": state.committed.is_set(), "ack_release": state.ack_release.is_set(),
                            "retry_count": state.retry_count, "server_stops": fixture.server.stop.call_count,
                            "events": list(state.events),
                        }
                        print("C12_WATCHER_FIRST_FAILURE=" + json.dumps(snapshot, ensure_ascii=False), file=sys.stderr, flush=True)
                    watcher.stop()
                    state.errors.append(error)
                    state.producer_release.set()
                    state.ack_release.set()
            watcher = timer(10, observe_ack_and_retry)

        def show(window):
            result = raw_show(window)
            if mode == "show_error":
                prepare(window)
                raise setup_error
            return result

        def activate(window, *args, **kwargs):
            return raw_activate(window, *args, **kwargs)

        def start_entry(controller):
            result = raw_start_entry(controller)
            if mode == "activation_error":
                window = controller.parent()
                self.assertIsInstance(window, MainWindow)
                prepare(window)
                raise setup_error
            return result

        def request_close(controller):
            if state.prepared and controller is state.fixture.windows[0]._app_controller and controller._shutdown_stage == "cache_failed":
                state.retry_requests.append(controller)
            return raw_request_close(controller)

        def close_event(window, event):
            try:
                return raw_close_event(window, event)
            except BaseException as error:
                if mode == "shutdown_effect_error":
                    state.close_error_events.append((window, event.isAccepted(), error))
                raise

        def native_entry_start(writer, *args, **kwargs):
            if state.prepared and state.retry_count and writer is state.fixture.windows[0]._app_controller.entry_snapshot_writer:
                state.retry_native_starts.append(writer)
            return raw_entry_native_start(writer, *args, **kwargs)

        def real_exec(*_args):
            state.native_execs += 1
            result = raw_exec()
            state.native_returns += 1
            state.events.append("real.app.exec.returned")
            if mode == "exec_error":
                raise setup_error
            return result

        def save_view(window):
            result = raw_save_view(window)
            if mode == "shutdown_effect_error":
                raise setup_error
            return result

        def dispatch(*args):
            result = raw_process_events(*args)
            if mode == "dispatch_interrupts" and state.native_returns and state.prepared:
                state.real_dispatch_returns += 1
                if len(state.dispatch_interrupts) < 2 and state.fixture.windows[0]._app_controller._shutdown_stage != "ready":
                    error = dispatch_errors[len(state.dispatch_interrupts)]
                    state.dispatch_interrupts.append(error)
                    raise error
            return result

        def install_qt_hook_observation():
            original_hook = sys.excepthook
            def observed(error_type, error, tb):
                state.qt_hook_records.append((error_type, error, tb, str(error)))
                if mode == "shutdown_effect_error":
                    state.error_hook_visible.append(state.fixture.windows[0].isVisible())
                result = original_hook(error_type, error, tb)
                if mode == "shutdown_effect_error":
                    # PySide may deliver this hook after enter_native returns.
                    # Let the original hook and actual modal OK finish first.
                    self.app.exit(0)
                return result
            sys.excepthook = observed

        def critical_modal(*args, **kwargs):
            state.critical_calls.append((args, kwargs))
            response = QTimer(self.app)
            response.setSingleShot(True)
            state.timers.append(response)
            def answer():
                modal = self.app.activeModalWidget()
                try:
                    self.assertIsInstance(modal, bootstrap.QMessageBox)
                    state.critical_modals.append((modal.windowTitle(), modal.text()))
                    self.assertEqual(args[1:3], (modal.windowTitle(), modal.text()))
                except BaseException as error:
                    state.errors.append(error)
                finally:
                    if isinstance(modal, bootstrap.QMessageBox):
                        button = modal.button(bootstrap.QMessageBox.StandardButton.Ok)
                        if button is not None:
                            button.click()
                        else:
                            modal.accept()
            response.timeout.connect(answer)
            response.start(0)
            # Delegate to the actual native modal. The timer reproduces only
            # a person's OK response; this call is not silently discarded.
            return raw_critical(*args, **kwargs)

        def wait(worker, *args, **kwargs):
            result = raw_wait(worker, *args, **kwargs)
            if mode in native_wait_modes and state.prepared and state.in_shutdown_tick and args == (0,) and not kwargs:
                target = (state.producer if mode == "producer_join_interrupt"
                    else state.fixture.windows[0]._app_controller.entry_snapshot_writer)
                if worker is target:
                    state.wait_attempts.append((worker, result))
                    if not state.wait_injected:
                        state.wait_injected = True
                        raise setup_error
            return result

        def advance(controller):
            if mode not in native_wait_modes:
                return raw_advance(controller)
            state.in_shutdown_tick = True
            try:
                return raw_advance(controller)
            except BaseException as error:
                state.slot_errors.append(error)
                raise
            finally:
                state.in_shutdown_tick = False
                if state.wait_injected and not state.tick_snapshots:
                    observed = sample("native.wait.timer.turn")
                    observed["timer_active"] = controller._shutdown_timer.isActive()
                    state.tick_snapshots.append(observed)

        def before_run(fixture):
            state.fixture = fixture
            configured = bootstrap.DataSourceConfig.load.return_value
            bootstrap.DataSourceConfig.load.side_effect = (
                configured,
                DataSourceSettings("personal_server", "https://fixture.invalid", "fixture", False, True),
            )
            # Retain the existing helper's real repositories/lease and use the
            # existing real QApplication, replacing only its Mock exec input.
            bootstrap.QApplication.return_value = self.app
            original_stop = fixture.server.stop.side_effect
            def stop():
                state.close_seen = True
                state.events.append("server.stop")
                original_stop()
                if mode != "quit":
                    raise source_error
            fixture.server.stop.side_effect = stop
            if mode in native_modes:
                def enter_native():
                    try:
                        if mode == 'shutdown_effect_error':
                            install_qt_hook_observation()
                        prepare(fixture.windows[0])
                    except BaseException as error:
                        state.errors.append(error)
                    finally:
                        if mode == "shutdown_effect_error":
                            # Retain the actual Qt closeEvent/error/modal path.
                            # The observed original hook exits after real OK.
                            self.app.quit()
                        else:
                            # Direct exec-return inputs must not wait for normal
                            # close to become ready before exercising outer drain.
                            self.app.exit(0)
                timer(0, enter_native, single=True)

        def close_lease(lease):
            if state.prepared and not lease._exclusive and not lease._stream.closed:
                sample("outer.lease.before.close")
            return raw_lease_close(lease)

        def fixture_cleanup():
            # Only after the lease/native snapshot and assertions. Baseline
            # leaks are never converted to production completion evidence.
            for value in state.timers:
                value.stop()
            state.producer_release.set()
            state.ack_release.set()
            if state.producer is not None:
                self.assertTrue(state.producer.wait(3000))
            if state.prepared:
                window = state.fixture.windows[0]
                controller = window._app_controller
                window.close()
                self.wait_until(lambda: controller._shutdown_stage in {"cache_failed", "ready"})
                if controller._shutdown_stage == "cache_failed":
                    window.close()
                self.wait_until(lambda: controller._shutdown_stage == "ready")
                state.sources.__exit__(None, None, None)
            if state.pair is not None:
                for raw_close in state.pair[3]:
                    raw_close()
                for thread in state.pair[2]:
                    thread.join(3)
                    self.assertFalse(thread.is_alive())

        def check(fixture):
            try:
                if mode in native_wait_modes:
                    self.assertFalse(state.test_owned_wake, repr(state.tick_snapshots))
                    self.assertEqual([], state.slot_errors)
                    self.assertEqual(1, len(state.tick_snapshots))
                    tick = state.tick_snapshots[0]
                    self.assertTrue(tick["timer_active"])
                    self.assertEqual("producers" if mode == "producer_join_interrupt" else "writers", tick["stage"])
                    self.assertEqual((True, True), tick["observer_alive"])
                    self.assertEqual(0, tick["server_stops"])
                    self.assertFalse(tick["restore_available"])
                    self.assertGreaterEqual(len(state.wait_attempts), 2)
                    self.assertIs(True, state.wait_attempts[-1][1])
                    target = state.producer if mode == "producer_join_interrupt" else fixture.window._app_controller.entry_snapshot_writer
                    phase = "producer" if mode == "producer_join_interrupt" else "writer"
                    self.assertEqual((target, phase, setup_error), setup_error.app_shutdown_native_wait_error)
                    self.assertEqual((("native_join", setup_error),), setup_error.app_shutdown_cleanup)
                    slot_notice.assert_not_called()
                final = next(item for item in state.snapshots if item["label"] == "outer.lease.before.close")
                # First assertion checks actual lifetime at source release,
                # before any fallback test-owned cleanup can alter the facts.
                self.assertEqual("ready", final["stage"], repr(state.snapshots))
                self.assertTrue(final["producer_join"])
                self.assertTrue(final["writer_join"])
                self.assertFalse(final["pending"])
                self.assertEqual((False, False), final["observer_alive"])
                self.assertFalse(final["content_alive"])
                self.assertEqual(1, final["server_stops"])
                self.assertFalse(final["restore_available"])
                if mode == "shutdown_effect_error":
                    self.assertEqual(1, len(state.close_error_events))
                    error_window, accepted, close_error = state.close_error_events[0]
                    self.assertIs(fixture.window, error_window)
                    self.assertFalse(accepted)
                    self.assertIs(setup_error, close_error)
                    self.assertEqual([True], state.error_hook_visible)
                    self.assertEqual(1, len(state.qt_hook_records))
                    error_type, error, tb, text = state.qt_hook_records[0]
                    self.assertIsInstance(error, BaseException)
                    self.assertIs(setup_error, error)
                    message = f"처리 중 오류가 발생했습니다.\n{text}\n\n로그 열기에서 자세한 내용을 확인할 수 있습니다."
                    self.assertEqual([((None, "프로그램 오류", message), {})], state.critical_calls)
                    self.assertEqual([("프로그램 오류", message)], state.critical_modals)
                self.assertEqual([], state.errors, "\n".join("".join(
                    traceback.format_exception(type(error), error, error.__traceback__)) for error in state.errors))
                self.assertEqual(0 if mode == "activation_error" else 1, ranking_start.call_count)
                self.assertEqual(1, state.retry_count)
                self.assertEqual(1, len(state.retry_views))
                visible, closing, stage, timers = state.retry_views[0]
                self.assertTrue(visible)
                self.assertTrue(closing)
                self.assertEqual("cache_failed", stage)
                self.assertFalse(any(timers), "retry visibility must not rearm stopped work/storage timers")
                self.assertEqual([False], state.retry_close_returns)
                self.assertEqual([fixture.window._app_controller], state.retry_requests)
                self.assertEqual([fixture.window._app_controller.entry_snapshot_writer], state.retry_native_starts)
                self.assertEqual([snapshot, expected, expected], [value for value, _scope in state.saves])
                self.assertEqual([scope, scope, scope], [value for _snapshot, value in state.saves])
                self.assertTrue(any(str(ack_error) in error for error in state.failures))
                if mode in native_modes:
                    self.assertEqual((1, 1), (state.native_execs, state.native_returns))
                else:
                    self.assertEqual((0, 0), (state.native_execs, state.native_returns))
                for close in state.borrowed:
                    close.assert_not_called()
                if mode != "quit":
                    expected_error = dispatch_errors[0] if mode == "dispatch_interrupts" else setup_error
                    self.assertIs(expected_error, fixture.exit_error)
                    if mode in {"shutdown_effect_error", "dispatch_interrupts"} | native_wait_modes:
                        self.assertIsNone(fixture.exit_error.__cause__)
                        self.assertIs(expected_error, fixture.exit_error.desktop_shutdown_cleanup[0][2])
                    else:
                        self.assertIs(previous_cause if mode == "show_error" else source_error,
                            fixture.exit_error.__cause__)
                    cleanup = fixture.exit_error.desktop_shutdown_cleanup
                    self.assertIs(source_error, cleanup[-1][2])
                if mode == "shutdown_effect_error":
                    self.assertEqual("save_view_state", setup_error.app_shutdown_start_cleanup[0][0])
                    self.assertIs(setup_error, setup_error.app_shutdown_start_cleanup[0][1])
                    self.assertEqual((("request_close", setup_error),), setup_error.app_shutdown_cleanup)
                if mode == "dispatch_interrupts":
                    self.assertEqual(list(dispatch_errors), state.dispatch_interrupts)
                    self.assertGreaterEqual(state.real_dispatch_returns, 2)
                    self.assertEqual((("event_dispatch", dispatch_errors[0]),),
                        dispatch_errors[0].app_shutdown_cleanup)
                order = ("producer.queued_result", "entry.commit.before_ack",
                    "entry.failed.before.explicit.retry", "explicit.window.close.retry",
                    "server.stop", "outer.lease.before.close")
                self.assertEqual(sorted(state.events.index(item) for item in order),
                    [state.events.index(item) for item in order])
                stored = JournalRepository(fixture.paths.journal_database_path).load_entry_snapshots(
                    "005930", fixed, fixed + timedelta(seconds=1), scope)
                self.assertEqual(1, len(stored))
                self.assertEqual(expected, stored[0])
                self.assertTrue(fixture.restore_available())
            finally:
                fixture_cleanup()

        try:
            with ExitStack() as stack:
                if mode in native_wait_modes:
                    # Only the baseline timer-slot failure reaches this UI.
                    # Record it without a modal; the missing-wake snapshot and
                    # any test-owned recovery remain independent FAIL evidence.
                    stack.enter_context(patch.object(bootstrap.QMessageBox, "critical", slot_notice))
                elif mode == "shutdown_effect_error":
                    stack.enter_context(patch.object(bootstrap.QMessageBox, "critical", side_effect=critical_modal))
                for active in (
                    patch.object(bootstrap.LocalApiConfig, "load",
                        return_value=KiwoomSettings("fixture-key", "fixture-secret", "mock")),
                    patch.object(validation_client, "RealtimeValidationRecorder",
                        side_effect=lambda *_args, **_kwargs: state.pair[1]),
                    patch.object(JournalRepository, "save_entry_snapshot", autospec=True, side_effect=save),
                    patch("kiwoom_monitor.infrastructure.persistence.entry_snapshot_writer.news_at_execution", return_value=news),
                    patch.object(threading.Thread, "start", start),
                    patch.object(AppDataLease, "close", close_lease),
                    patch.object(QApplication, "exec", real_exec),
                    patch.object(QThread, "wait", wait),
                    patch.object(AppController, "_advance_shutdown", advance),
                    patch.object(QCoreApplication, "processEvents", staticmethod(dispatch)),
                    patch.object(MainWindow, "show", show),
                    patch.object(MainWindow, "activate_startup", activate),
                    patch.object(AppController, "start_entry_snapshot_writer", start_entry),
                    patch.object(AppController, "request_close", request_close),
                    patch.object(MainWindow, "closeEvent", close_event),
                    patch.object(EntrySnapshotWriter, "start", native_entry_start),
                    patch.object(MainWindow, "_save_shutdown_view_state", save_view),
                    patch.object(AppController, "start_initial_ranking", ranking_start),
                ):
                    stack.enter_context(active)
                self._bootstrap_settings_desktop(
                    lambda _fixture: self.fail("C12 must use the real Qt exec"), after_exit=check,
                    expected_error=SystemExit if mode == "quit" else BaseException,
                    cleanup_window=True, before_run=before_run, query_client_factory=factory,
                    content_service=service)
        finally:
            if state.pair is not None and any(thread.is_alive() for thread in state.pair[2]):
                fixture_cleanup()

    def test_c12_direct_real_exec_return_keeps_same_writer_ack_retry_and_outer_lease(self):
        for mode in ("quit", "shutdown_effect_error", "dispatch_interrupts",
                "producer_join_interrupt", "writer_join_interrupt"):
            with self.subTest(mode=mode):
                self._c12_published_owner_native_case(mode)

    def test_c12_real_exec_error_keeps_original_error_and_cleanup_after_actual_shutdown(self):
        self._c12_published_owner_native_case("exec_error")

    def test_c12_activation_error_keeps_published_owner_until_pending_ack_retry(self):
        self._c12_published_owner_native_case("activation_error")

    def test_c12_postsetup_error_keeps_existing_cause_and_ordered_source_cleanup(self):
        self._c12_published_owner_native_case("show_error")
        for closing in (False, True):
            with self.subTest(restore_notice_closing=closing):
                self._c12_restore_notice_queued_case(closing)

    def test_c12_constructor_error_is_not_replaced_by_simultaneous_source_close_error(self):
        import threading
        from contextlib import ExitStack
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
        from kiwoom_monitor.infrastructure.kiwoom_rest import validation_client
        from kiwoom_monitor.infrastructure.kiwoom_rest.settings import KiwoomSettings
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease

        original_error = RuntimeError("C12 actual constructor before Controller")
        source_error = OSError("C12 actual constructor simultaneous source close")
        state = SimpleNamespace(fixture=None, pair=None, partial=None, borrowed=[], leases=[], entered=0)
        original_recorder_class = validation_client.RealtimeValidationRecorder
        raw_close = AppDataLease.close
        service = Mock()
        service.replace_themes.return_value = SimpleNamespace(pending_collections=())
        service.push.return_value = SimpleNamespace(total=0)
        def pull(*_args, **_kwargs):
            state.content_thread = threading.current_thread()
            return SimpleNamespace(total=0)
        service.pull.side_effect = pull
        def factory(*_args, **_kwargs):
            with patch.object(validation_client, "RealtimeValidationRecorder", original_recorder_class):
                state.pair = self._observer_owner_real_pair(state.fixture.paths.data_dir, "c12-constructor-source")
            for borrowed in (state.pair[0]._primary, state.pair[0]._local):
                borrowed.server_now = lambda: datetime.fromisoformat("2030-01-02T10:00:00+09:00")
                borrowed.close = Mock()
                state.borrowed.append(borrowed.close)
            return state.pair[0]
        def before_run(fixture):
            state.fixture = fixture
            configured = bootstrap.DataSourceConfig.load.return_value
            bootstrap.DataSourceConfig.load.side_effect = (configured,
                DataSourceSettings("personal_server", "https://fixture.invalid", "fixture", False, True))
            def stop():
                fixture.events.append("server.stop")
                raise source_error
            fixture.server.stop.side_effect = stop
        def fail_controller(_controller, parent=None, *_args, **_kwargs):
            state.partial = parent
            state.entered += 1
            raise original_error
        def close_lease(lease):
            if state.entered and not lease._exclusive and not lease._stream.closed:
                state.leases.append((tuple(thread.is_alive() for thread in state.pair[2]),
                    state.content_thread.is_alive(), state.fixture.server.stop.call_count))
            return raw_close(lease)
        def check(fixture):
            self.assertEqual(1, state.entered)
            self.assertIs(original_error, fixture.exit_error)
            self.assertIs(source_error, original_error.__cause__)
            self.assertEqual([((False, False), False, 1)], state.leases)
            self.assertIs(source_error, original_error.desktop_shutdown_cleanup[-1][2])
            self.assertEqual(2, len(original_error.api_runtime_observer_cleanup))
            self.assertTrue(all(item[1].native_completed for item in original_error.api_runtime_observer_completion))
            self.assertEqual([], fixture.windows)
            for close in state.borrowed:
                close.assert_not_called()
            self.assertTrue(fixture.restore_available())
        try:
            with ExitStack() as stack:
                for active in (
                    patch.object(bootstrap.LocalApiConfig, "load",
                        return_value=KiwoomSettings("fixture-key", "fixture-secret", "mock")),
                    patch.object(validation_client, "RealtimeValidationRecorder",
                        side_effect=lambda *_args, **_kwargs: state.pair[1]),
                    patch.object(AppController, "__init__", fail_controller),
                    patch.object(AppDataLease, "close", close_lease),
                ):
                    stack.enter_context(active)
                self._bootstrap_settings_desktop(
                    lambda _fixture: self.fail("failed constructor entered app.exec"), after_exit=check,
                    expected_error=BaseException, before_run=before_run,
                    query_client_factory=factory, content_service=service)
        finally:
            if state.pair is not None:
                # Only after intended-error/native-at-lease assertions.
                for close in state.pair[3]:
                    close()
                for thread in state.pair[2]:
                    thread.join(3)
                    self.assertFalse(thread.is_alive())
            if state.partial is not None:
                state.partial.deleteLater()


    def _c12_restore_notice_queued_case(self, closing):
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.infrastructure.persistence.strict_restore import RestoreOutcome, StrictRestoreCoordinator

        message = "C12 원 복원 결과 안내"
        outcome = RestoreOutcome("ROLLED_BACK", message)
        state = SimpleNamespace(fixture=None, errors=[], invoked=0, execs=0, timers=[], producer=None)
        raw_enter = StrictRestoreCoordinator.enter_main
        warning = Mock()
        def enter(owner, *args, **kwargs):
            lease, _original_outcome = raw_enter(owner, *args, **kwargs)
            return lease, outcome
        def before_run(fixture):
            state.fixture = fixture
            bootstrap.QApplication.return_value = self.app
            # Requeue the exact captured production lambda on a real native
            # QTimer. Capturing it without delivery cannot satisfy this oracle.
            driver = QTimer(self.app)
            driver.setSingleShot(True)
            state.timers.append(driver)
            def deliver():
                try:
                    window = fixture.windows[0]
                    fixture.window = window
                    callbacks = [callback for _delay, callback in fixture.callbacks
                        if "restore_outcome" in getattr(getattr(callback, "__code__", None), "co_freevars", ())]
                    self.assertEqual(1, len(callbacks))
                    self.assertFalse(window._app_controller.closing)
                    if closing:
                        # Keep one real accepted producer active so the ready
                        # signal cannot quit exec before the queued notice turn.
                        state.producer = DelayedProducer(window._app_controller)
                        state.producer.start()
                        self.assertTrue(state.producer.started_run.wait(2))
                        window._app_controller.request_close()
                        self.assertTrue(window._app_controller.closing)
                    notice = QTimer(self.app)
                    notice.setSingleShot(True)
                    state.timers.append(notice)
                    def invoke():
                        try:
                            state.invoked += 1
                            callbacks[0]()
                        except BaseException as error:
                            state.errors.append(error)
                        finally:
                            if state.producer is not None:
                                state.producer.release.set()
                            self.app.quit()
                    notice.timeout.connect(invoke)
                    notice.start(0)
                except BaseException as error:
                    state.errors.append(error)
                    self.app.quit()
            driver.timeout.connect(deliver)
            driver.start(0)
        def after_exit(fixture):
            self.assertEqual([], state.errors)
            self.assertEqual(1, state.invoked)
            if closing:
                warning.assert_not_called()
            else:
                warning.assert_called_once_with(fixture.window, "Google Drive 복원", message)
            self.assertEqual("ready", fixture.window._app_controller._shutdown_stage)
            self.assertTrue(fixture.restore_available())
        try:
            with patch.object(StrictRestoreCoordinator, "enter_main", enter), \
                    patch.object(bootstrap.QMessageBox, "warning", warning), \
                    patch.object(AppController, "start_initial_ranking"):
                self._bootstrap_settings_desktop(
                    lambda _fixture: self.fail("restore notice must use actual QApplication.exec"),
                    after_exit=after_exit, before_run=before_run, cleanup_window=True)
        finally:
            for timer in state.timers:
                timer.stop()
            if state.producer is not None:
                state.producer.release.set()
                self.assertTrue(state.producer.wait(3000))


    def _c12_settings_geometry_native_case(self, mode):
        import threading
        from contextlib import ExitStack
        from shiboken6 import isValid
        from PySide6.QtWidgets import QDialog
        from kiwoom_monitor import bootstrap
        from kiwoom_monitor.presentation import main_window as window_module
        from kiwoom_monitor.presentation import settings_request_worker as request_module
        from kiwoom_monitor.presentation.settings_dialog import SettingsDialog
        from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease

        expected = {"settings_dialog_width": "741", "settings_dialog_height": "683"}
        normal_modes = {"normal_callback_complete", "normal_callback_join_interrupt"}
        join_error = KeyboardInterrupt("C12 geometry normal callback native wait interrupted")
        state = SimpleNamespace(fixture=None, window=None, dialog=None, worker=None,
            inside_size_submit=False, entered=Event(), release=Event(), committed=Event(),
            connection_closed=Event(), owner_cut=False, lease_started=False,
            test_owned_lease_release=False, cleanup=False, errors=[], events=[], waits=[],
            wait_errors=[], lease_facts=[], timers=[], payloads=[], parents=[], retains=[],
            transaction_at_gate=None, target_connection=None, content_thread=None,
            submissions=0, finished_results=[], admission_error=None, close_requested=False,
            in_owner_callback=False, callback_observations=[], release_attempts=[], wait_injected=False, destroyed=False)
        raw_connect = sqlite3.connect
        raw_start = SettingsRequestWorker.start
        raw_submit = SettingsDialog._save_dialog_size
        raw_wait = QThread.wait
        raw_interrupt = QThread.requestInterruption
        raw_lease_close = AppDataLease.close
        raw_thread_start = threading.Thread.start
        raw_release = SettingsRequestWorker.release_after_join
        raw_complete = getattr(AppController, 'finish_settings_size_request', None)
        admission = Mock(side_effect=AssertionError("closing must not construct a new SettingsDialog"))

        class GateConnection(sqlite3.Connection):
            def executemany(connection, sql, rows):
                rows = tuple(rows)
                keys = {row[0] for row in rows} if rows else set()
                if ("INSERT INTO central_setting_versions" in sql and keys == set(expected)
                        and QThread.currentThread() is state.worker and state.target_connection is None):
                    state.target_connection = connection
                    state.payloads.append({row[0]: row[2] for row in rows})
                    state.transaction_at_gate = connection.in_transaction
                    state.events.append("geometry.sql.before.first.write")
                    state.entered.set()
                    if not state.release.wait(4):
                        state.errors.append(AssertionError("geometry exact SQL gate was not released"))
                return super().executemany(sql, rows)

            def commit(connection):
                result = super().commit()
                if connection is state.target_connection:
                    state.events.append("geometry.native.commit")
                    state.committed.set()
                return result

            def close(connection):
                result = super().close()
                if connection is state.target_connection:
                    state.events.append("geometry.native.connection.closed")
                    state.connection_closed.set()
                return result

        def connect(*args, **kwargs):
            if state.fixture is not None and args and Path(args[0]).resolve() == state.fixture.paths.database_path.resolve():
                kwargs = {**kwargs, "factory": GateConnection}
            return raw_connect(*args, **kwargs)

        def submit(dialog):
            state.inside_size_submit = True
            state.submissions += 1
            state.events.append("dialog.size.submit")
            try:
                return raw_submit(dialog)
            finally:
                state.inside_size_submit = False

        def start(worker):
            if state.inside_size_submit and state.worker is None:
                state.worker = worker
                state.parents.append(worker.parent())
                # Observation only: no fixture retain flag or release override.
                state.retains.append(worker._retain_until_joined)
                def destroyed(*_args):
                    state.destroyed = True
                    state.events.append("geometry.qobject.destroyed")
                    if mode in normal_modes and not state.close_requested:
                        schedule_close()
                worker.destroyed.connect(destroyed)
            return raw_start(worker)

        def wait(worker, *args, **kwargs):
            try:
                result = raw_wait(worker, *args, **kwargs)
                if (worker is state.worker and mode == "normal_callback_join_interrupt"
                        and state.in_owner_callback and args == () and not kwargs and not state.wait_injected):
                    state.wait_injected = True
                    raise join_error
            except BaseException as error:
                if worker is state.worker:
                    state.wait_errors.append(error)
                raise
            if worker is state.worker:
                before_lease = not state.lease_started and not state.cleanup
                state.waits.append((result, before_lease, args))
                if result is True and before_lease:
                    state.events.append("geometry.actual.native.join")
            return result

        def interrupt(worker):
            result = raw_interrupt(worker)
            if worker is state.worker and not state.cleanup and state.window._app_controller.closing:
                state.owner_cut = True
                state.events.append("geometry.product.owner.cut")
                # Release SQL only when the actual producer owner reached this
                # exact worker. This is not a native-completion receipt.
                state.release.set()
            return result

        def thread_start(thread):
            if thread.name == "central-content-sync":
                state.content_thread = thread
            return raw_thread_start(thread)

        def close_lease(lease):
            if state.window is not None and not lease._exclusive and not lease._stream.closed:
                state.lease_started = True
                state.entered.wait(2)
                running, observation_error = None, None
                if state.worker is not None:
                    try:
                        running = state.worker.isRunning()
                    except RuntimeError as error:
                        observation_error = error
                facts = {"gate_entered": state.entered.is_set(), "owner_cut": state.owner_cut,
                    "worker_running": running, "observation_error": observation_error,
                    "native_join_before_lease": any(result is True and before for result, before, _args in state.waits),
                    "committed": state.committed.is_set(), "connection_closed": state.connection_closed.is_set(),
                    "content_alive": state.content_thread.is_alive(),
                    "server_stops": state.fixture.server.stop.call_count,
                    "stage": state.window._app_controller._shutdown_stage,
                    "global_owned": any(item is state.worker for item in tuple(request_module._running_requests))}
                state.lease_facts.append(facts)
                state.events.append("outer.lease.before.close")
                if not state.owner_cut and mode not in normal_modes:
                    # Preserve the actual live-worker/early-lease facts first.
                    # This fallback is test-owned and must make the test FAIL.
                    state.test_owned_lease_release = True
                    state.release.set()
            return raw_lease_close(lease)

        def request_close():
            if state.close_requested:
                return
            state.close_requested = True
            state.window.close()
            with patch.object(window_module, "SettingsDialog", admission):
                try:
                    state.window._open_settings()
                except AssertionError as error:
                    state.admission_error = error
            self.app.quit()

        def schedule_close():
            close_timer = QTimer(self.app)
            close_timer.setSingleShot(True)
            state.timers.append(close_timer)
            close_timer.timeout.connect(request_close)
            close_timer.start(0)

        def release(worker, *args, **kwargs):
            observation = None
            if worker is state.worker:
                observation = {"kwargs": dict(kwargs), "before_lease": not state.lease_started,
                    "result": None, "error": None}
                state.release_attempts.append(observation)
            try:
                result = raw_release(worker, *args, **kwargs)
            except BaseException as error:
                if observation is not None:
                    observation["error"] = error
                raise
            if observation is not None:
                observation["result"] = result
            return result

        def complete(controller, worker):
            if worker is not state.worker:
                return raw_complete(controller, worker)
            state.in_owner_callback = True
            try:
                return raw_complete(controller, worker)
            finally:
                state.in_owner_callback = False
                if mode in normal_modes:
                    failure = controller._shutdown_native_wait_error
                    state.callback_observations.append({
                        "closing": controller.closing,
                        "gui_receiver": QThread.currentThread() is controller.thread(),
                        "outside_worker": QThread.currentThread() is not worker,
                        "global_owned": any(item is worker for item in tuple(request_module._running_requests)),
                        "valid": isValid(worker), "failure": failure})
                    if failure is not None:
                        schedule_close()

        def before_run(fixture):
            state.fixture = fixture
            bootstrap.QApplication.return_value = self.app
            original_stop = fixture.server.stop.side_effect
            def stop():
                state.events.append("server.stop")
                return original_stop()
            fixture.server.stop.side_effect = stop
            driver = QTimer(self.app)
            driver.setSingleShot(True)
            state.timers.append(driver)
            def enter():
                try:
                    state.window = fixture.windows[0]
                    fixture.window = state.window
                    state.window._open_settings()
                    state.dialog = state.window._settings_dialog
                    self.assertIsInstance(state.dialog, SettingsDialog)
                    self.assertIs(state.window, state.dialog.parent())
                    state.dialog.finished.connect(state.finished_results.append)
                    state.dialog.resize(741, 683)
                    self.assertEqual((741, 683), (state.dialog.width(), state.dialog.height()))
                    self.assertEqual(350, state.dialog._dialog_size_save_timer.interval())
                    if mode == "reject_before_close" or mode in normal_modes:
                        state.dialog.reject()
                        self.assertTrue(state.entered.wait(2))
                        self.assertTrue(state.worker.isRunning())
                        self.assertFalse(state.committed.is_set())
                    if mode in normal_modes:
                        # Normal completion is a distinct input: make this
                        # native SQL available before asking the main to close.
                        state.release.set()
                        return
                    state.close_requested = True
                    state.window.close()
                    if mode == "close_current_dialog":
                        self.assertTrue(state.entered.wait(2))
                    # Probe only the forbidden new admission; never create a
                    # second fake dialog or a second geometry write in fixture.
                    with patch.object(window_module, "SettingsDialog", admission):
                        try:
                            state.window._open_settings()
                        except AssertionError as error:
                            state.admission_error = error
                except BaseException as error:
                    state.errors.append(error)
                finally:
                    # Use the real Qt quit/closeEvent link, without replacing
                    # the published owner's close path or native release slot.
                    if mode not in normal_modes or state.errors:
                        self.app.quit()
            driver.timeout.connect(enter)
            driver.start(0)

        def cleanup():
            state.cleanup = True
            state.release.set()
            for timer in state.timers:
                timer.stop()
            if state.worker is not None and isValid(state.worker):
                self.assertTrue(raw_wait(state.worker, 3000))
            if state.window is not None:
                state.window.close()
                self.wait_until(lambda: state.window._app_controller._shutdown_stage == "ready")

        def check(fixture):
            try:
                # Actual body/store/source/lease evidence precedes any cleanup.
                self.assertEqual(1, len(state.lease_facts), repr(state.events))
                facts = state.lease_facts[0]
                self.assertTrue(facts["gate_entered"], repr(facts))
                self.assertFalse(state.test_owned_lease_release, repr(facts))
                self.assertTrue(facts["native_join_before_lease"], repr(facts))
                self.assertTrue(facts["committed"])
                self.assertTrue(facts["connection_closed"])
                self.assertFalse(facts["content_alive"])
                self.assertEqual(1, facts["server_stops"])
                self.assertEqual("ready", facts["stage"])
                self.assertEqual([], state.errors)
                self.assertFalse(facts["global_owned"])
                if mode == "normal_callback_join_interrupt":
                    self.assertEqual([join_error], state.wait_errors)
                    self.assertIs(join_error, fixture.exit_error)
                    self.assertEqual((state.worker, "settings_size", join_error),
                        fixture.exit_error.app_shutdown_native_wait_error)
                    self.assertEqual(2, len(state.release_attempts))
                    self.assertIs(join_error, state.release_attempts[0]["error"])
                    self.assertEqual({"wait": True}, state.release_attempts[0]["kwargs"])
                    self.assertEqual({}, state.release_attempts[1]["kwargs"])
                    self.assertIs(True, state.release_attempts[1]["result"])
                else:
                    self.assertEqual([], state.wait_errors)
                if mode in normal_modes:
                    self.assertEqual(1, len(state.callback_observations))
                    observed = state.callback_observations[0]
                    self.assertFalse(observed["closing"])
                    self.assertTrue(observed["gui_receiver"])
                    self.assertTrue(observed["outside_worker"])
                    self.assertEqual([True], state.retains)
                    if mode == "normal_callback_complete":
                        self.assertFalse(observed["global_owned"])
                        self.assertIsNone(observed["failure"])
                        self.assertEqual(1, len(state.release_attempts))
                        self.assertEqual({"wait": True}, state.release_attempts[0]["kwargs"])
                        self.assertIs(True, state.release_attempts[0]["result"])
                        self.assertTrue(state.destroyed)
                    else:
                        self.assertTrue(observed["global_owned"])
                        self.assertTrue(observed["valid"])
                        self.assertEqual((state.worker, "settings_size", join_error), observed["failure"])
                self.assertIsNone(state.admission_error)
                admission.assert_not_called()
                self.assertEqual([state.window], state.parents)
                self.assertEqual([expected], state.payloads)
                self.assertFalse(state.transaction_at_gate,
                    "gate must not hold a SQLite write transaction that also blocks MainWindow geometry")
                self.assertEqual(1, state.submissions)
                self.assertEqual([QDialog.DialogCode.Rejected], state.finished_results)
                self.assertIsNone(state.window._settings_dialog)
                with closing(raw_connect(fixture.paths.database_path)) as native:
                    stored = dict(native.execute("SELECT key,value FROM settings WHERE key IN (?,?)",
                        tuple(expected)).fetchall())
                self.assertEqual(expected, stored)
                order = ("geometry.sql.before.first.write", "geometry.native.commit",
                    "geometry.native.connection.closed", "geometry.actual.native.join",
                    "server.stop", "outer.lease.before.close")
                self.assertEqual(sorted(state.events.index(item) for item in order),
                    [state.events.index(item) for item in order])
                self.assertTrue(fixture.restore_available())
            finally:
                cleanup()

        try:
            with ExitStack() as stack:
                if raw_complete is not None:
                    stack.enter_context(patch.object(AppController, 'finish_settings_size_request', complete))
                for active in (
                    patch.object(sqlite3, "connect", connect),
                    patch.object(SettingsDialog, "_save_dialog_size", submit),
                    patch.object(SettingsRequestWorker, "start", start),
                    patch.object(SettingsRequestWorker, "release_after_join", release),
                    patch.object(QThread, "wait", wait),
                    patch.object(QThread, "requestInterruption", interrupt),
                    patch.object(threading.Thread, "start", thread_start),
                    patch.object(AppDataLease, "close", close_lease),
                    patch.object(AppController, "start_initial_ranking"),
                ):
                    stack.enter_context(active)
                self._bootstrap_settings_desktop(
                    lambda _fixture: self.fail("geometry oracle must use the real QApplication.exec"),
                    after_exit=check, before_run=before_run, cleanup_window=True,
                    expected_error=BaseException if mode == 'normal_callback_join_interrupt' else SystemExit)
        finally:
            state.release.set()
            for timer in state.timers:
                timer.stop()
            if state.worker is not None and isValid(state.worker):
                self.assertTrue(raw_wait(state.worker, 3000))

    def test_c12_settings_reject_geometry_native_commit_and_join_precede_outer_lease(self):
        for mode in ("reject_before_close", "close_current_dialog",
                "normal_callback_complete", "normal_callback_join_interrupt"):
            with self.subTest(mode=mode):
                self._c12_settings_geometry_native_case(mode)



if __name__ == "__main__":
    unittest.main()
