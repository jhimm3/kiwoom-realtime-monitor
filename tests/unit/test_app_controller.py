from __future__ import annotations

import gc
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

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


class CapturingCacheWriter(QObject):
    minute_saved = Signal()
    minute_failed = Signal(object, object, str)
    price_failed = Signal(object, object, object, object, str)
    history_saved = Signal(str)
    history_failed = Signal(str, str)
    comparison_saved = Signal(int)
    comparison_failed = Signal(str)
    daily_high_failed = Signal(str, str)
    fundamentals_failed = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.enqueue_price_cache = Mock()
        self.enqueue_minute_bars = Mock()
        self.enqueue_trade_comparisons = Mock()
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
        for name, label in (("flush_price_cache", "prices"), ("flush_minute_cache", "minutes"),
                            ("flush_comparisons", "comparisons")):
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
        self.assertEqual((1000, 1000, 500), (
            controller.price_cache_timer.interval(), controller.minute_cache_timer.interval(),
            controller.comparison_timer.interval(),
        ))
        for timer in (controller.price_cache_timer, controller.minute_cache_timer, controller.comparison_timer):
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
        controller.queue_comparison("005930", (("20261006", 1.),))
        controller.queue_comparison("005930", (("20261006", 2.),))
        self.assertFalse(writer.enqueue_price_cache.called)
        self.assertFalse(writer.enqueue_minute_bars.called)
        self.wait_until(lambda: writer.enqueue_trade_comparisons.called
                        and writer.enqueue_minute_bars.called and writer.enqueue_price_cache.called)
        writer.enqueue_price_cache.assert_called_once_with(
            {"005930": 103, "000660": 90}, {"005930": 103}, {"005930": 202.}, now.date(),
        )
        writer.enqueue_minute_bars.assert_called_once_with(
            {("005930", now): latest}, {("KOSPI", now): (2500., 2510., 2495., 2495., 12.)},
        )
        writer.enqueue_trade_comparisons.assert_called_once_with(
            {"005930": (("20261006", 2.),)}, now.date(),
        )
        self.assertFalse(controller.pending_prices or controller.pending_highs or controller.pending_market_caps)
        self.assertFalse(controller.pending_minutes or controller.pending_market_minutes or controller.pending_comparisons)

    def test_queued_writer_failures_preserve_newer_values_and_close_does_not_restart_retry(self):
        now = datetime(2026, 10, 6, 10, 0)
        old = MinuteOhlcv(now, 100, 100, 100, 100, 1)
        latest = MinuteOhlcv(now, 100, 103, 100, 103, 3)
        writer = CapturingCacheWriter()
        with patch("kiwoom_monitor.presentation.app_controller.MarketCacheWriter", return_value=writer):
            controller = self.controller([], monitor_database_path=Path("unused.sqlite3"),
                                         minute_bar_repository=Mock())
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
        self.wait_until(lambda: "000660" in controller.pending_prices)
        self.assertEqual({"005930": 103, "000660": 90}, controller.pending_prices)
        self.assertEqual({"005930": 104}, controller.pending_highs)
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
                self.assertEqual({"005930": 71200}, controller.pending_highs)
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
            self.assertFalse(controller.request_close())
            self.wait_until(lambda: "ready" in events)
            controller.start_market_cache_writer()
            cache.return_value.start.assert_called_once()
            entry.return_value.start.assert_called_once()

    def test_flush_comparison_before_drain_and_timeout_prevents_accept(self):
        events = []
        controller = self.controller(events)
        writer = FakeWriter(events, completes=False)
        controller.market_cache_writer = writer
        self.assertFalse(controller.request_close())
        self.assertFalse(controller.request_close())
        self.assertTrue(controller.closing)
        self.assertLess(events.index("comparisons"), events.index("cache_drain"))
        self.assertNotIn("auxiliaries", events)
        controller._advance_shutdown()
        self.assertNotIn("ready", events)
        writer.running = False
        self.wait_until(lambda: "ready" in events)
        self.assertTrue(controller.request_close())
        for name in ("view", "backup", "comparisons", "cache_drain", "auxiliaries", "ready"):
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
        self.assertNotIn("comparisons", events)
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

    def test_main_window_close_saves_final_prices_and_comparison_before_real_writer_exit(self):
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
            events = []
            window._app_controller.pending_prices = {"005930": 71200}
            window._app_controller.pending_comparisons = {"005930": (("20261001", 1.),)}
            writer = window._market_cache_writer
            writer.comparison_saved.connect(lambda count: events.append(count))
            with patch("kiwoom_monitor.infrastructure.persistence.minute_bar_repository.MinuteBarRepository.update_comparison_reports", return_value=1) as save:
                close = QCloseEvent()
                window.closeEvent(close)
                self.assertFalse(close.isAccepted())
                self.wait_until(lambda: window._app_controller.request_close())
                self.assertFalse(writer.isRunning())
                save.assert_called_once()
                self.assertEqual([1], events)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(71200, connection.execute("SELECT last_price FROM stocks WHERE code='005930'").fetchone()[0])
            self.assertFalse(window._journal_news_timer.isActive())
            self.assertFalse(window._investment_notice_timer.isActive())
            self.assertNotIn("_closing", window.__dict__)
            self.assertNotIn("_market_cache_writer", window.__dict__)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
            self.windows.remove(window)

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
            with patch.object(StockRepository, "update_last_prices", side_effect=OSError("injected price save failure")) as save:
                for _ in range(2):
                    event = QCloseEvent()
                    window.closeEvent(event)
                    self.assertFalse(event.isAccepted())
                    self.wait_until(lambda: controller._shutdown_stage == "cache_failed")
                    self.assertEqual({"005930": 71200}, controller.pending_prices)
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
                method = "upsert_many" if kind == "minute" else "update_last_prices"
                original = getattr(repository_type, method)
                def save(repository, values):
                    calls.append(dict(values))
                    if len(calls) == 1:
                        entered.set()
                        if not release.wait(3):
                            raise TimeoutError("probe did not release old batch")
                        raise OSError("injected old batch failure")
                    return original(repository, values)
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
                    pending = controller.pending_minutes if kind == "minute" else controller.pending_prices
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

    def test_runtime_reload_waits_for_old_signals_then_replaces_resources_and_resets_view(self):
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
                patch.object(MainWindow, "_restore_environment_selector"):
            window = MainWindow(database.settings, ranking_loader=old_loader,
                                market_data_client=old_client, api_runtime_factory=factory)
            self.windows.append(window)
            controller = window._app_controller
            controller.entry_snapshot_writer = Mock(isRunning=Mock(return_value=False))
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
            self.assertNotIn("_api_reloading", window.__dict__)
            self.assertNotIn("_ranking_loader", window.__dict__)
            self.assertNotIn("_market_data_client", window.__dict__)

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


if __name__ == "__main__":
    unittest.main()
