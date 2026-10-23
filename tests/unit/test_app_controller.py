from __future__ import annotations

import gc
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
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
                self.windows.append(window)
                controller = window._app_controller
                window._row_by_code["005930"] = 0
                window._minute_bar_storage_date = now.date()
                controller.realtime.trade_received.emit(TradeTick(
                    "005930", 71000, 100, 7100000, 2, 71200, "100000", market_cap_eok=200,
                ))
                controller.realtime.market_state_received.emit(MarketIndexTick(
                    "KOSPI", "100000", 2500., cumulative_trade_value_million_won=1200,
                ))
                self.assertEqual(71000, window._current_prices["005930"])
                self.assertEqual(200., window._realtime_market_caps["005930"])
                self.assertEqual(2500., window._latest_market_state["KOSPI"]["index"])
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
            controller = window._app_controller
            self.assertIsNone(window._monitor_database_path)
            self.assertIsNone(controller.market_cache_writer)
            window._ensure_today_minute_bar_storage(now)

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
                window._minute_aggregator.mark_gap(gap, ("EMPTY",), now)
                generation = window._minute_aggregator.gap_generation("EMPTY")
                window._minute_recovery_requests["EMPTY"] = generation
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
                        "history_loaded": "TARGET" in window._minute_history_codes,
                        "empty_quality": window._minute_aggregator.quality_for_range("EMPTY", minute, minute + timedelta(minutes=1)).value,
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
            self.windows.append(window)
            controller = window._app_controller
            window._row_by_code["005930"] = 0
            window._minute_bar_storage_date = now.date()
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
            self.assertIn("005930", window._minute_history_codes)
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
                    with patch.object(writer, "set_investor_loader", wraps=writer.set_investor_loader) as setter:
                        if rollback:
                            with self.assertRaisesRegex(RuntimeError, "controlled view rollback"):
                                controller._apply_api_runtime(runtime, actions)
                            self.assertEqual([new, old], [call.args[0] for call in setter.call_args_list])
                        else:
                            controller._apply_api_runtime(runtime, actions)
                            setter.assert_called_once_with(new)
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
            self.windows.append(window)
            controller = window._app_controller
            writer = EntrySnapshotWriter(Path(directory) / "journal.sqlite3")
            controller.entry_snapshot_writer = writer
            self.enterContext(patch.object(writer, "set_investor_loader", wraps=writer.set_investor_loader))
            self.enterContext(patch.object(writer, "set_program_loader", wraps=writer.set_program_loader))
            window._market_cap_reference_codes.add("005930")
            window._market_cap_reference_pending.add("005930")
            window._realtime_market_caps["005930"] = 100
            window._minute_history_codes.add("005930")
            old_aggregator = window._minute_aggregator
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
            self.assertEqual("local", window._active_api_route)
            self.assertEqual(initial_rows, window._table.rowCount())
            self.assertFalse(window._ranking_execution.priority_preparing)
            self.assertIsNot(old_aggregator, window._minute_aggregator)
            self.assertFalse(window._market_cap_reference_codes)
            self.assertFalse(window._market_cap_reference_pending)
            self.assertFalse(window._realtime_market_caps)
            self.assertFalse(window._minute_history_codes)
            for name, feature in (
                ("realtime_worker_factory", controller.realtime),
                ("minute_history_worker_factory", controller.minute_history),
                ("fundamentals_worker_factory", controller.fundamentals),
                ("daily_high_worker_factory", controller.daily_high),
                ("historical_high_worker_factory", controller.historical_high),
                ("nxt_eligibility_worker_factory", controller.nxt_eligibility),
            ):
                self.assertIs(factories[name], feature._worker_factory)
            controller.entry_snapshot_writer.set_investor_loader.assert_called_once_with(investor_loader)
            controller.entry_snapshot_writer.set_program_loader.assert_called_once_with(program_loader)
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
                    self.windows.append(window)
                    controller=window._app_controller
                    controller._initial_ranking_timer.stop()
                    controller._api_reloading=True; controller._api_reload_stage='signals'
                    old_factory=controller.realtime._worker_factory
                    old_aggregator=window._minute_aggregator
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
                    self.assertIs(old_aggregator,window._minute_aggregator)
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
                self.windows.append(window)
                window._row_by_code = {"005930": 0}
                window._app_controller.ranked_codes = ("005930",)
                window._app_controller.nxt_eligibility.finished.emit()
                self.assertEqual(["catalog", ("subscribe", ("005930",)),
                                  ("followups", ("005930",))], events)
                window._minute_aggregator = SimpleNamespace(
                    reset_cumulative_baselines=lambda codes: events.append(("reset", codes)))
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


if __name__ == "__main__":
    unittest.main()
