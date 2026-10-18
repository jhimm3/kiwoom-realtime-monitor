from __future__ import annotations

import gc
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QRect
from PySide6.QtGui import QImage, QPainter, QStandardItemModel
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QToolBar

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.presentation.app_controller import AppController
from kiwoom_monitor.presentation.main_window import MainWindow, NxtMarkerDelegate, selected_high_cycle_periods
from kiwoom_monitor.presentation.theme_dialogs import ThemeEditDialog
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
from kiwoom_monitor.application.daily_high_service import DailyHighTargets
from kiwoom_monitor.application.trade_strength import StockFundamentals
from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope, LEGACY_ACCOUNT_SCOPE


class FakeRankingLoader:
    def load_top_stocks(self) -> tuple[object, ...]:
        class Stock:
            rank = 1
            code = "005930"
            name = "삼성전자"
            new_high_label = "5일"
            change_rate = "+1.23"

        return (Stock(),)


class MainWindowTest(unittest.TestCase):
    def test_drive_legacy_restore_clock_is_bound_to_current_runtime(self) -> None:
        from kiwoom_monitor.domain.time_basis import as_kst
        from kiwoom_monitor.infrastructure.persistence.google_drive_sync import GoogleDriveSyncService
        current = [datetime.fromisoformat("2030-01-02T03:04:05+09:00")]
        loader = FakeRankingLoader()
        loader.server_now = lambda: current[0]
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "monitor.sqlite3")
            database.initialize()
            drive = GoogleDriveSyncService(database.settings.database_path)
            window = MainWindow(database.settings, loader, google_drive_sync=drive)
            try:
                self.assertEqual(as_kst(current[0]), drive._now_provider())
                current[0] = datetime.fromisoformat("2030-01-01T18:06:07+00:00")
                self.assertEqual(as_kst(current[0]), drive._now_provider())
            finally:
                window.close()
                window.deleteLater()
                QApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)

    def test_aware_runtime_execution_signal_reaches_snapshot_with_kst_pressure_boundaries(self) -> None:
        from collections import deque
        from datetime import timezone
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import OrderExecution
        now = datetime.fromisoformat("2026-10-16T10:15:10+09:00")
        current = [now]
        loader = FakeRankingLoader()
        loader.server_now = lambda: current[0]
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, loader)
            snapshots = []
            writer = SimpleNamespace(isRunning=lambda: True, enqueue=snapshots.append)
            execution = OrderExecution("order", "fill", "005930", "삼성전자", "매수", 110, 1, "101510")
            try:
                self.assertEqual(now, window._ranking_now())
                with patch.object(MainWindow, "_entry_snapshot_writer", property(lambda owner: writer)):
                    window._realtime_worker_controller.order_executed.emit(execution)
                    self.assertEqual(1, len(snapshots))
                    self.assertEqual(now, snapshots[-1].executed_at)
                    self.assertEqual(0, snapshots[-1].orderbook["buy_execution_volume"])
                    self.assertIsNone(snapshots[-1].orderbook["buy_share_percent"])
                    window._realtime_pressure[execution.code] = deque((
                        (now - timedelta(seconds=61), 999),
                        ((now - timedelta(seconds=60)).replace(tzinfo=None), 10),
                        (now - timedelta(seconds=30), -5),
                        ((now - timedelta(seconds=20)).astimezone(timezone.utc), 5),
                    ))
                    window._realtime_worker_controller.order_executed.emit(execution)
                    pressure = dict(snapshots[-1].orderbook)
                    self.assertEqual((15, 5, 75.0), (pressure["buy_execution_volume"],
                        pressure["sell_execution_volume"], pressure["buy_share_percent"]))
                    deadline = now + timedelta(seconds=60)
                    for recovery_until in (deadline, deadline.replace(tzinfo=None),
                                           deadline.astimezone(timezone.utc)):
                        current[0] = deadline - timedelta(microseconds=1)
                        window._pressure_gap_until[execution.code] = recovery_until
                        window._realtime_worker_controller.order_executed.emit(execution)
                        self.assertFalse(snapshots[-1].orderbook["available"])
                        self.assertIsNone(snapshots[-1].orderbook["buy_execution_volume"])
                        current[0] = deadline
                        window._realtime_worker_controller.order_executed.emit(execution)
                        self.assertTrue(snapshots[-1].orderbook.get("available", True))
                        self.assertEqual(pressure, snapshots[1].orderbook)
                    self.assertEqual(8, len(snapshots))
            finally:
                window.close()

    def test_repository_receipts_follow_current_runtime_clock(self) -> None:
        import sqlite3
        from contextlib import closing
        from kiwoom_monitor.domain.time_basis import KST
        from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
        from kiwoom_monitor.infrastructure.persistence.daily_bar_repository import DailyBarRepository
        from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            database = Database(path)
            database.initialize()
            minute, daily, theme = MinuteBarRepository(path), DailyBarRepository(path), ThemeRepository(path)
            first = datetime(2030, 1, 2, 10, 0, tzinfo=KST)
            loader = FakeRankingLoader()
            loader.server_now = lambda: first
            window = MainWindow(database.settings, loader, minute_bar_repository=minute,
                                daily_bar_repository=daily, theme_store=theme)
            try:
                for observed in (first, first + timedelta(days=1)):
                    # API reload replaces this same controller-owned loader.
                    window._app_controller.ranking_loader = SimpleNamespace(server_now=lambda: observed)
                    minute.upsert_top20_trade_value_index(observed.replace(tzinfo=None), 12., ("005930",))
                    daily.mark_finalized(("005930",), observed.date())
                    theme.set_theme_alias("구이름", "새이름")
                    with closing(sqlite3.connect(path)) as connection:
                        stored = connection.execute("SELECT updated_at FROM top20_trade_value_index WHERE trade_date=?", (observed.date().isoformat(),)).fetchone()[0]
                        finalized = connection.execute("SELECT finalized_at FROM market_data_finalization_log WHERE trade_date=?", (observed.date().isoformat(),)).fetchone()[0]
                        alias = connection.execute("SELECT updated_at FROM profile_theme_name_decisions WHERE source_name='구이름'").fetchone()[0]
                    self.assertEqual((observed.isoformat(),) * 3, (stored, finalized, alias))
            finally:
                window.close()

    def test_ranking_restores_repository_batch_and_refreshes_nxt_display(self) -> None:
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        database = Database(path)
        database.initialize()
        repository = StockRepository(path)
        now = datetime(2026, 10, 16, 10, 0)
        repository.upsert("005930", "삼성전자", "KOSPI")
        repository.update_last_prices({"005930": 70000})
        repository.update_intraday_highs({"005930": 71000}, now.date())
        repository.update_fundamentals("005930", 2000000, 55.5, 72000, 500, 91000)
        window = MainWindow(database.settings, FakeRankingLoader(), stock_lookup=repository)
        window._ranking_now = lambda: now
        with patch.object(repository, "load_ranking_cache", wraps=repository.load_ranking_cache) as load:
            window._app_controller.ranking.completed.emit(FakeRankingLoader().load_top_stocks())
        self.assertEqual(1, load.call_count)
        self.assertEqual(now.date(), load.call_args.args[2])
        self.assertEqual("KOSPI", window._stock_markets["005930"])
        self.assertEqual(70000, window._current_prices["005930"])
        self.assertEqual(71000, window._today_high_prices["005930"])
        self.assertEqual(55.5, window._fundamentals["005930"].float_ratio_percent)
        self.assertNotIn("005930", window._nxt_checked_codes)
        for enabled in (True, False):
            repository.update_nxt_enabled("005930", enabled, now.date().isoformat())
            window._app_controller.ranking.completed.emit(FakeRankingLoader().load_top_stocks())
            self.assertIn("005930", window._nxt_checked_codes)
            self.assertEqual(enabled, "005930" in window._nxt_enabled_codes)
            self.assertEqual(enabled, window._table.item(0, 1).data(Qt.ItemDataRole.UserRole + 2))
        window.close()

    def test_google_drive_dirty_comparison_preserves_mixed_offset_instants(self):
        for local, uploaded, expected in (
            ('2026-10-16T14:00:00+09:00', '2026-10-16T06:00:00Z', False),
            ('2026-10-16T06:00:00Z', '2026-10-16T14:00:00+09:00', True),
            ('2026-10-16T14:00:00+09:00', '2026-10-16T05:00:00Z', False),
        ):
            with self.subTest(local=local, uploaded=uploaded):
                values = {'google_drive_local_changed_at': local,
                          'google_drive_last_upload_success_at': uploaded,
                          'google_drive_unsynced_changes': '0'}
                owner = SimpleNamespace(_settings=SimpleNamespace(get=values.get))
                self.assertEqual(MainWindow._has_newer_local_google_drive_changes(owner), expected)

    def test_google_drive_local_marker_uses_kst_without_changing_instant(self):
        from datetime import timezone
        from kiwoom_monitor.domain.time_basis import as_kst
        value = as_kst(datetime(2026, 10, 16, 5, tzinfo=timezone.utc))
        with patch('kiwoom_monitor.presentation.main_window.kst_now', return_value=value):
            self.assertEqual(MainWindow._google_drive_timestamp_now(), '2026-10-16T14:00:00+09:00')

    def test_saved_high_is_visible_on_ranking_and_blue_dot_tracks_daily_refresh(self):
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        from kiwoom_monitor.infrastructure.persistence.daily_bar_repository import DailyBarRepository
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        database = Database(path); database.initialize()
        database.settings.set("high_distance_period", "250")
        stocks = StockRepository(path)
        stocks.upsert("005930", "삼성전자", "KOSPI")
        stocks.update_fundamentals("005930",1000,50,70000)
        daily = DailyBarRepository(path)
        window = MainWindow(database.settings,FakeRankingLoader(),stock_lookup=stocks,daily_bar_repository=daily)
        now = datetime(2026,10,16,10,0,10)
        window._ranking_now = lambda: now
        window._app_controller.ranking.completed.emit(FakeRankingLoader().load_top_stocks())
        role = NxtMarkerDelegate.HIGH_REFRESH_PENDING_ROLE
        self.assertEqual("70,000",window._table.item(0,13).text())
        self.assertTrue(window._table.item(0,13).data(role))
        # Actual 0B updates the price/distance without reading cached bars again.
        with patch.object(daily,"load_targets",side_effect=AssertionError("DB read in 0B")):
            window._on_trade_tick(TradeTick("005930",65000,None,1000,10,66000,"100010"))
            window._flush_trade_tick_updates()
        self.assertTrue(window._table.item(0,13).data(role))
        self.assertIn("7.14",window._table.item(0,14).text())
        fresh = DailyHighTargets(None,None,68000,period_statuses=(("250","ready"),),
            collection_verified=True,query_basis_date=now.date().isoformat())
        window._on_daily_high_received("005930",fresh)
        self.assertEqual("68,000",window._table.item(0,13).text())
        self.assertFalse(window._table.item(0,13).data(role))
        window._on_fundamentals_received("005930",StockFundamentals(1000,50,99999))
        self.assertEqual("68,000",window._table.item(0,13).text())
        now += timedelta(days=1)
        window._render_new_high_price("005930")
        self.assertTrue(window._table.item(0,13).data(role))
        window._on_daily_high_received("005930",DailyHighTargets(None,None,None))
        self.assertEqual("68,000",window._table.item(0,13).text())
        self.assertTrue(window._table.item(0,13).data(role))
        window._on_daily_high_received("005930",replace(fresh,high_250_price=67000,
            query_basis_date=now.date().isoformat()))
        self.assertEqual("67,000",window._table.item(0,13).text())
        self.assertFalse(window._table.item(0,13).data(role))
        window.close()

    def test_short_period_cache_and_partial_refresh_keep_each_period_marker_independent(self):
        from kiwoom_monitor.application.daily_high_service import DailyBar
        from kiwoom_monitor.infrastructure.persistence.daily_bar_repository import DailyBarRepository
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        database = Database(path); database.initialize()
        database.settings.set("high_distance_period","5")
        daily = DailyBarRepository(path)
        now = datetime(2026,10,16,10,0)
        bars = tuple(DailyBar((now-timedelta(days=n+1)).strftime("%Y%m%d"),100+n,None) for n in range(20))
        daily.upsert_targets("005930",DailyHighTargets(None,None,None,daily_bars=bars),now.date())
        window = MainWindow(database.settings,FakeRankingLoader(),daily_bar_repository=daily)
        window._ranking_now = lambda: now
        window._app_controller.ranking.completed.emit(FakeRankingLoader().load_top_stocks())
        role = NxtMarkerDelegate.HIGH_REFRESH_PENDING_ROLE
        self.assertEqual("104",window._table.item(0,13).text())
        self.assertTrue(window._table.item(0,13).data(role))
        fresh = DailyHighTargets(103,None,None,period_statuses=(("5","ready"),),
            collection_verified=True,query_basis_date=now.date().isoformat())
        window._on_daily_high_received("005930",fresh)
        self.assertEqual("103",window._table.item(0,13).text())
        self.assertFalse(window._table.item(0,13).data(role))
        database.settings.set("high_distance_period","20")
        window._render_new_high_price("005930")
        self.assertEqual("119",window._table.item(0,13).text())
        self.assertTrue(window._table.item(0,13).data(role))
        window._on_daily_high_received("005930",replace(fresh,high_20_price=118,
            period_statuses=(("5","ready"),("20","ready"))))
        self.assertEqual("118",window._table.item(0,13).text())
        self.assertFalse(window._table.item(0,13).data(role))
        window.close()

    def test_historical_cache_refresh_marker_survives_finish_and_reloads_checked_date(self):
        from kiwoom_monitor.application.historical_high_service import HistoricalHighTarget
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "monitor.sqlite3"
        database = Database(path); database.initialize()
        database.settings.set("high_distance_period","historical")
        stocks = StockRepository(path); stocks.upsert("005930","삼성전자","KOSPI")
        stocks.update_historical_high_price("005930",70000,1980,2026,"2026-10-15")
        window = MainWindow(database.settings,FakeRankingLoader(),stock_lookup=stocks)
        now = datetime(2026,10,16,10,0)
        window._ranking_now = lambda: now
        window._app_controller.ranking.completed.emit(FakeRankingLoader().load_top_stocks())
        role = NxtMarkerDelegate.HIGH_REFRESH_PENDING_ROLE
        self.assertEqual("70,000",window._table.item(0,13).text())
        self.assertTrue(window._table.item(0,13).data(role))
        window._on_historical_high_received("005930",HistoricalHighTarget(68000,1980,2026))
        window._on_historical_high_worker_finished()
        window._render_new_high_price("005930")
        self.assertEqual("68,000",window._table.item(0,13).text())
        self.assertFalse(window._table.item(0,13).data(role))
        window._historical_high_refreshed_on.clear()
        window._load_cached_historical_highs(("005930",))
        self.assertFalse(window._table.item(0,13).data(role))
        window._on_historical_high_received("005930",HistoricalHighTarget(None,None,None))
        self.assertEqual("68,000",window._table.item(0,13).text())
        now += timedelta(days=1)
        window._render_new_high_price("005930")
        self.assertTrue(window._table.item(0,13).data(role))
        window.close()

    def test_high_refresh_blue_dot_paints_top_right_and_preserves_nxt_triangle(self):
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QImage, QPainter, QStandardItem, QStandardItemModel
        from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
        model = QStandardItemModel(1,1)
        item = QStandardItem("70,000")
        model.setItem(0,0,item)
        item.setData(True,Qt.ItemDataRole.UserRole+2)
        delegate = NxtMarkerDelegate()
        def render(pending):
            item.setData(pending,delegate.HIGH_REFRESH_PENDING_ROLE)
            image = QImage(120,48,QImage.Format.Format_ARGB32); image.fill(Qt.GlobalColor.white)
            option = QStyleOptionViewItem(); option.rect = QRect(0,0,120,48)
            option.state = QStyle.StateFlag.State_Enabled
            painter = QPainter(image)
            try: delegate.paint(painter,option,model.index(0,0))
            finally: painter.end()
            return image
        pending, fresh = render(True),render(False)
        blue = delegate.HIGH_REFRESH_PENDING_COLOR.name()
        self.assertTrue(any(pending.pixelColor(x,y).name()==blue for x in range(108,120) for y in range(9)))
        self.assertFalse(any(fresh.pixelColor(x,y).name()==blue for x in range(108,120) for y in range(9)))
        self.assertFalse(any(pending.pixelColor(x,y).name()==blue for x in range(12) for y in range(9)))
        for image in (pending,fresh):
            self.assertTrue(any(image.pixelColor(x,y).name()=="#c00000" for x in range(110,120) for y in range(39,48)))

    def test_gap_keeps_live_values_strength_and_alert_with_separate_quality(self) -> None:
        from kiwoom_monitor.domain.realtime_gap import GapRange, RealtimeGap
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "monitor.sqlite3")
            database.initialize()
            database.settings.set("trade_value_alert_enabled", "1")
            database.settings.set("trade_value_1m_alert_eok", "1")
            database.settings.set("strength_show_icon", "0")
            window = MainWindow(database.settings, FakeRankingLoader())
            now = datetime(2026, 10, 9, 10, 15, 10)
            window._ranking_now = lambda: now
            window._app_controller.ranking.completed.emit(FakeRankingLoader().load_top_stocks())
            window._fundamentals["005930"] = StockFundamentals(1000, 50)
            first = TradeTick("005930", 100, None, 1000, 10, None, "101501")
            window._on_trade_tick(first)
            window._on_trade_tick(replace(first, cumulative_trade_value=1250))
            window._render_trade_values("005930")
            self.assertTrue(window._table.item(0, 6).data(window.TRADE_VALUE_ALERT_ROLE))
            gap = RealtimeGap(1, (GapRange("trade", "005930", "KRX", now, now),), False)
            window._on_realtime_gap(gap.document())
            window._on_trade_tick(replace(first, current_price=110, cumulative_trade_value=1300))
            window._flush_trade_tick_updates()
            self.assertEqual(110, window._current_prices["005930"])
            self.assertAlmostEqual(2.5, float(window._table.item(0, 6).text()))
            self.assertIn("부분", window._table.item(0, 6).toolTip())
            self.assertAlmostEqual(0.5, float(window._table.item(0, 4).text().rstrip("%")))
            self.assertIn("부분", window._table.item(0, 4).toolTip())
            self.assertTrue(window._table.item(0, 6).data(window.TRADE_VALUE_ALERT_ROLE))
            state = window._entry_market_state()
            self.assertEqual(2.5, state["ranking_table"]["visible_trade_value_1m_eok"])
            self.assertEqual("partial", state["input_quality"]["005930"]["1"])
            from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import OrderExecution
            snapshots = []
            writer = SimpleNamespace(isRunning=lambda: True, enqueue=snapshots.append)
            with patch.object(MainWindow, "_entry_snapshot_writer", property(lambda owner: writer)):
                window._on_order_execution(OrderExecution("order", "fill", "005930", "삼성전자",
                    "매수", 110, 1, "101510"))
            self.assertEqual(2.5, snapshots[0].trade_value_1m_eok)
            self.assertEqual(2.5, snapshots[0].trade_value_5m_eok)
            self.assertEqual("partial", snapshots[0].market_state["input_quality"]["005930"]["1"])
            pressure = window._execution_pressure_at_entry("005930", now)
            self.assertFalse(pressure["available"])
            self.assertIsNone(pressure["buy_share_percent"])
            now += timedelta(seconds=61)
            window._on_trade_tick(replace(first, trade_time=now.strftime("%H%M%S"), cumulative_trade_value=1400))
            recovered = window._execution_pressure_at_entry("005930", now)
            self.assertTrue(recovered.get("available", True))
            self.assertEqual(100.0, recovered["buy_share_percent"])
            self.assertFalse(pressure["available"])  # Historical snapshot stays partial.
            window.close()

    def test_nas_unconfirmed_values_update_live_and_previous_minute_without_quality_gate(self) -> None:
        from kiwoom_monitor.domain.market_data_contract import (
            DataCompleteness, MarketDataMetadata, ObservationOrigin,
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "monitor.sqlite3")
            database.initialize()
            for period in ("1m", "5m", "60m", "day"):
                database.settings.set(f"trade_value_{period}_alert_eok", "1")
            database.settings.set("trade_value_alert_enabled", "1")
            database.settings.set("strength_show_icon", "0")
            database.settings.set("theme_trade_summary_enabled", "1")
            database.settings.set("theme_trade_summary_excluded_enabled", "1")
            database.settings.set("theme_trade_summary_excluded_stocks", "제외종목")
            database.settings.set("theme_trade_summary_period", "1m")
            window = MainWindow(database.settings, FakeRankingLoader())
            now = datetime(2026, 10, 9, 10, 15, 10)
            window._ranking_now = lambda: now
            window._app_controller.ranking.completed.emit(FakeRankingLoader().load_top_stocks())
            window._minute_aggregator = type(window._minute_aggregator)()
            window._fundamentals["005930"] = StockFundamentals(1000, 50)
            window._themes["삼성전자"] = "반도체"
            minute = now.replace(second=0)
            metadata = MarketDataMetadata(
                effective_at=minute, available_at=now, completeness=DataCompleteness.UNCONFIRMED,
                origin=ObservationOrigin.REALTIME, source="NAS:kiwoom-websocket-0B",
            )
            bar = MinuteOhlcv(minute, 100, 100, 100, 100, 10, 2.5, metadata)
            window._minute_aggregator.seed("005930", (bar,), now,
                include_current_snapshot=True, query_authoritative=False)
            window._on_trade_tick(TradeTick("005930", 100, None, 1000, 10, None, "101510"))
            window._on_trade_tick(TradeTick("005930", 110, None, 1250, 10, None, "101511"))
            window._minute_history_codes.add("005930")
            window._flush_trade_tick_updates()
            for column in (6, 7, 8, 9):
                self.assertEqual(5.0, float(window._table.item(0, column).text()))
                self.assertTrue(window._table.item(0, column).data(window.TRADE_VALUE_ALERT_ROLE))
                self.assertIn("미확인", window._table.item(0, column).toolTip())
            for column in (4, 10, 11, 12):
                self.assertAlmostEqual(1.0, float(window._table.item(0, column).text().rstrip("%")))
                self.assertIn("미확인", window._table.item(0, column).toolTip())
            window._refresh_theme_trade_summary()
            for label in (window._theme_trade_summary, window._theme_trade_excluded_summary):
                self.assertIn("반도체", label.text())
                self.assertNotIn("미확인", label.text())
                self.assertIn("미확인", label.toolTip())
            # The same received minute remains usable after changing to the prior-minute view.
            now += timedelta(minutes=1)
            database.settings.set("trade_display_1m_mode", "completed")
            window._render_trade_values("005930")
            self.assertEqual(5.0, float(window._table.item(0, 6).text()))
            self.assertAlmostEqual(1.0, float(window._table.item(0, 4).text().rstrip("%")))
            self.assertTrue(window._table.item(0, 6).data(window.TRADE_VALUE_ALERT_ROLE))
            self.assertEqual(DataCompleteness.UNCONFIRMED,
                window._minute_aggregator.bucket_quality("005930", 1, now, previous=True))
            # Disabling the existing alert setting still takes effect.
            database.settings.set("trade_value_alert_enabled", "0")
            window._render_trade_values("005930")
            self.assertFalse(window._table.item(0, 6).data(window.TRADE_VALUE_ALERT_ROLE))
            window.close()

    def test_after_hours_daily_restore_only_reads_missing_nas_proof(self) -> None:
        from kiwoom_monitor.application.secondary_data_schedule import SecondaryStartPhase
        now = datetime(2026, 10, 1, 21, 0)
        for nas in (True, False):
            calls = []
            def start(codes, **kwargs):
                calls.append(kwargs)
                return SecondaryStartPhase.AFTER_HOURS
            window = SimpleNamespace(
                _ranking_execution=SimpleNamespace(priority_preparing=False),
                _ranking_now=lambda: now, _journal_background_sync_day=now.date(),
                _load_cached_daily_highs=lambda codes: None,
                _load_cached_historical_highs=lambda codes: None,
                _finalization_candidates=lambda codes: (None, ()),
                _is_after_hours_data_pause=lambda: True,
                _uses_nas_market_data_source=lambda: nas,
                _daily_highs={"cached": DailyHighTargets(None, None, None),
                              "ready": DailyHighTargets(None, None, 100,
                                  query_basis_date=now.date().isoformat(), collection_verified=True)},
                _secondary_data_coordinator=SimpleNamespace(start=start),
            )
            request = MainWindow._prepare_secondary_loading(window, ("cached", "ready", "new"))
            self.assertEqual(("cached", "new") if nas else (), request.stored_daily_high_codes)

    def test_daily_high_completion_requires_verified_scope_and_target_day(self) -> None:
        from dataclasses import replace
        from datetime import date
        from kiwoom_monitor.application.daily_high_service import DailyBar
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)
            window._daily_bar_repository = None
            window._stock_lookup = None
            window._after_close_finalization_date = date(2026, 9, 29)
            window._after_close_finalization_codes = ("005930",)
            raw = DailyHighTargets(None, None, None, daily_bars=(DailyBar("20260929", 100, None),))
            window._on_daily_high_received("005930", raw)
            self.assertNotIn("005930", window._after_close_daily_received)
            self.assertNotIn("005930", window._daily_high_basis_refresh_received)
            initial = replace(raw, collection_verified=True, scope="initial", window_end="2026-09-29")
            window._on_daily_high_received("005930", initial)
            self.assertNotIn("005930", window._after_close_daily_received)
            window._on_daily_high_received("005930", replace(initial, scope="final"))
            self.assertIn("005930", window._after_close_daily_received)
            window.close()

    def test_settings_backup_import_confirmation_uses_main_window_as_parent(self) -> None:
        owner = SimpleNamespace()
        with patch("kiwoom_monitor.presentation.main_window.QFileDialog.getOpenFileName", return_value=("backup.json", "")), \
                patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question, \
                patch("kiwoom_monitor.presentation.main_window.SettingsBackupService") as service:
            MainWindow._import_settings_backup(owner)
            service.assert_not_called()
        self.assertIs(owner, question.call_args.args[0])

    def test_settings_backup_import_applies_after_confirmation(self) -> None:
        refreshed: list[bool] = []
        owner = SimpleNamespace(
            _settings=SimpleNamespace(database_path=Path("monitor.sqlite3")),
            _apply_downloaded_google_drive_data=lambda: refreshed.append(True),
        )
        with patch("kiwoom_monitor.presentation.main_window.QFileDialog.getOpenFileName", return_value=("backup.json", "")), \
                patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                patch.object(QMessageBox, "information"), \
                patch("kiwoom_monitor.presentation.main_window.SettingsBackupService") as backup_service:
            MainWindow._import_settings_backup(owner)
        self.assertEqual([True], refreshed)
        self.assertEqual(Path("backup.json"), backup_service.return_value.import_from.call_args.args[0])
        self.assertFalse(backup_service.return_value.import_from.call_args.kwargs["include_themes"])

    def test_bulk_theme_save_runs_after_submit_and_reports_only_after_success(self) -> None:
        class Signal:
            def connect(self, callback):
                self.callback = callback

        class Worker:
            def __init__(self, task, parent=None):
                self.task = task
                self.succeeded = Signal()
                self.failed = Signal()
                self.started = False

            def start(self):
                self.started = True

        saved: list[str] = []
        messages: list[str] = []
        store = SimpleNamespace(all_by_name=lambda: {"005930": "반도체"})
        owner = SimpleNamespace(
            _theme_store=store, _theme_save_request=None,
            _theme_save_completion=None, _closing=False, _app_controller=object(),
            statusBar=lambda: SimpleNamespace(showMessage=lambda message, *_: messages.append(message)),
            _refresh_theme_badges=lambda: None,
            _refresh_rankings=lambda: None,
            _schedule_google_drive_upload=lambda *_: None,
        )
        owner._main_theme_change_saved = MethodType(MainWindow._main_theme_change_saved, owner)
        owner._main_theme_change_failed = MethodType(MainWindow._main_theme_change_failed, owner)
        with patch("kiwoom_monitor.presentation.main_window.SettingsRequestWorker", Worker), \
                patch.object(QMessageBox, "information") as information:
            accepted = MainWindow._submit_main_theme_change(
                owner, lambda: saved.append("written"), completion=("완료", "1개 적용"),
            )
            self.assertTrue(accepted)
            self.assertEqual([], saved)
            information.assert_not_called()
            worker = owner._theme_save_request
            self.assertTrue(worker.started)
            worker.succeeded.callback(worker.task())
            self.assertEqual(["written"], saved)
            information.assert_called_once_with(owner, "완료", "1개 적용")

    def test_followups_start_once_per_applied_ranking_and_ignore_stale_subscription(self) -> None:
        started: list[tuple[str, ...]] = []
        execution = SimpleNamespace(priority_preparing=True)
        owner = SimpleNamespace(
            closing=False, api_reloading=False, ranking_execution=execution,
            ranked_codes=("005930", "000660"),
            _ranking_followup_revision=1, _started_followup_revision=-1,
            start_secondary_loading=lambda codes: started.append(codes),
        )
        codes = ("005930", "000660")
        AppController.start_realtime_followups(owner, codes)
        self.assertEqual([], started)
        execution.priority_preparing = False
        AppController.start_realtime_followups(owner, ("005930",))
        self.assertEqual([], started)
        AppController.start_realtime_followups(owner, codes)
        AppController.start_realtime_followups(owner, codes)
        self.assertEqual([codes], started)
        owner._ranking_followup_revision = 2
        AppController.start_realtime_followups(owner, codes)
        self.assertEqual([codes, codes], started)

    def test_theme_confirmation_modal_does_not_defer_live_table_updates(self) -> None:
        app = QApplication.instance() or QApplication([])
        editor = ThemeEditDialog("삼성전자", ("반도체",), ",/|;")
        confirmation = QMessageBox(editor)
        owner = SimpleNamespace(_image_theme_workflow_active=False)
        with patch.object(QApplication, "activeModalWidget", return_value=confirmation):
            self.assertFalse(MainWindow._has_blocking_modal(owner))
        with patch.object(QApplication, "activeModalWidget", return_value=QMessageBox()):
            self.assertTrue(MainWindow._has_blocking_modal(owner))
            owner._theme_confirmation_active = True
            self.assertFalse(MainWindow._has_blocking_modal(owner))
        confirmation.close()
        editor.close()

    def test_price_cache_flush_includes_latest_realtime_market_cap(self) -> None:
        from kiwoom_monitor.domain.time_basis import as_kst
        queued = []
        writer = SimpleNamespace(
            enqueue_price_cache=lambda *args, **kwargs: queued.append((args, kwargs)),
        )
        now = datetime(2026, 9, 21, 10, 15)
        owner = AppController()
        def dispose_owner() -> None:
            owner.deleteLater()
            QCoreApplication.sendPostedEvents(owner, QEvent.Type.DeferredDelete)
        self.addCleanup(dispose_owner)
        owner.pending_prices = {"005930": 71_000}
        owner.pending_highs = {"005930": 72_000}
        owner.pending_market_caps = {"005930": 4_200_000.0}
        owner.market_cache_writer = writer
        owner.ranking_now = lambda: now

        AppController.flush_price_cache(owner)

        self.assertEqual(
            [((
                {"005930": 71_000}, {(now.date(), "005930"): 72_000},
                {"005930": 4_200_000.0}, now.date(),
            ), {"observed_at": as_kst(now), "receipt": 1})],
            queued,
        )
        self.assertEqual({}, owner.pending_prices)
        self.assertEqual({}, owner.pending_highs)
        self.assertEqual({}, owner.pending_market_caps)

    def test_api_label_prioritizes_actual_ranking_local_fallback(self) -> None:
        shown = []
        owner = SimpleNamespace(
            _active_api_route="central",
            _ranking_uses_local_fallback=True,
            _set_api_status=lambda text, color: shown.append((text, color)),
        )

        MainWindow._set_connected_api_status(owner)

        self.assertEqual(("API: 로컬 전환", "#B36B00"), shown[-1])

    def test_api_label_keeps_nas_source_while_realtime_is_waiting(self) -> None:
        shown = []
        owner = SimpleNamespace(
            _active_api_route="central_waiting",
            _ranking_uses_local_fallback=False,
            _set_api_status=lambda text, color: shown.append((text, color)),
        )

        MainWindow._set_connected_api_status(owner)

        self.assertEqual(("API: NAS", "#008000"), shown[-1])

    def test_stored_market_cap_does_not_overwrite_newer_live_tick(self) -> None:
        rendered = []
        owner = SimpleNamespace(
            _market_cap_reference_pending={"005930", "000660"},
            _market_cap_reference_codes=set(),
            _realtime_market_caps={"005930": 5_000_000.0},
            _render_market_cap=lambda code: rendered.append(code),
        )

        MainWindow._apply_market_cap_references(owner, ("005930", "000660"), {
            "005930": {"market_cap_eok": 4_000_000},
            "000660": {"market_cap_eok": 900_000},
        })

        self.assertEqual(5_000_000.0, owner._realtime_market_caps["005930"])
        self.assertEqual(900_000.0, owner._realtime_market_caps["000660"])
        self.assertEqual({"005930", "000660"}, owner._market_cap_reference_codes)
        self.assertEqual(set(), owner._market_cap_reference_pending)
        self.assertEqual(["005930", "000660"], rendered)

    def test_stored_market_cap_from_previous_api_runtime_is_discarded(self) -> None:
        old_client, current_client = object(), object()
        owner = SimpleNamespace(
            _market_data_client=current_client,
            _market_cap_reference_pending=set(),
            _market_cap_reference_codes=set(),
            _realtime_market_caps={},
            _render_market_cap=lambda _code: self.fail("old runtime result was rendered"),
        )

        MainWindow._apply_market_cap_references(
            owner, ("005930",), {"005930": {"market_cap_eok": 4_000_000}}, old_client,
        )

        self.assertEqual({}, owner._realtime_market_caps)

    def test_api_reload_discards_late_ranking_response(self) -> None:
        owner = SimpleNamespace(api_reloading=True, ranking_execution=Mock(), _ranking_actions=Mock())

        AppController.handle_ranking_response(owner, (object(),))

        self.assertTrue(owner.api_reloading)
        self.assertEqual([], owner.ranking_execution.mock_calls)
        self.assertEqual([], owner._ranking_actions.mock_calls)

    def test_ranking_timer_does_not_fire_before_the_scheduled_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())

            self.assertEqual(Qt.TimerType.PreciseTimer, window._ranking_timer.timerType())
            controller = window._app_controller
            controller._initial_ranking_timer.stop()
            controller.ranking_execution._now = lambda: datetime(2035,1,2,9,0,29,900000)
            fired = []
            controller.ranking_requested.connect(lambda: fired.append(True))
            controller.schedule_next_ranking_refresh()
            self.assertGreaterEqual(window._ranking_timer.interval(), 350)
            QTest.qWait(100)
            self.assertEqual([], fired)
            self.assertTrue(window._ranking_timer.isActive())
            window.close()

    def test_journal_news_relay_preserves_late_request_scope_after_account_switch(self) -> None:
        requested = AccountScope(
            "kiwoom", AccountEnvironment.REAL,
            "11111111-1111-4111-8111-111111111111",
        )
        current = AccountScope(
            "kiwoom", AccountEnvironment.MOCK,
            "22222222-2222-4222-8222-222222222222",
        )
        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        owner = SimpleNamespace(
            _journal_news_inbox=SimpleNamespace(read_new=lambda: {
                "code": "005930", "name": "삼성전자", "group_id": "group-1",
                "trade_date": "2026-09-13", "origin_scope": requested.to_dict(),
                "account_scope": requested.to_dict(),
            }),
            _current_account_scope=current,
            _news_window=SimpleNamespace(
                ensure_started=lambda: None,
                send_command=lambda *args, **kwargs: calls.append((args, kwargs)),
            ),
        )

        MainWindow._poll_journal_news_request(owner)

        self.assertEqual(requested, calls[0][1]["origin_scope"])
        self.assertEqual(requested, calls[0][1]["account_scope"])

    def test_journal_news_relay_keeps_scope_free_legacy_and_rejects_partial_scope(self) -> None:
        sent: list[dict[str, object]] = []
        documents = iter((
            {"code": "005930", "name": "삼성전자", "group_id": "legacy"},
            {
                "code": "005930", "name": "삼성전자", "group_id": "invalid",
                "origin_scope": LEGACY_ACCOUNT_SCOPE.to_dict(),
            },
        ))
        owner = SimpleNamespace(
            _journal_news_inbox=SimpleNamespace(read_new=lambda: next(documents)),
            _news_window=SimpleNamespace(
                ensure_started=lambda: None,
                send_command=lambda *_args, **kwargs: sent.append(kwargs),
            ),
        )

        MainWindow._poll_journal_news_request(owner)
        MainWindow._poll_journal_news_request(owner)

        self.assertEqual(1, len(sent))
        self.assertEqual(LEGACY_ACCOUNT_SCOPE, sent[0]["origin_scope"])
        self.assertEqual(LEGACY_ACCOUNT_SCOPE, sent[0]["account_scope"])

    def test_theme_change_forces_google_settings_and_theme_backup(self) -> None:
        scheduled: list[str] = []
        refreshed: list[bool] = []
        badge_refreshes: list[bool] = []
        owner = SimpleNamespace(
            _theme_store=SimpleNamespace(all_by_name=lambda: {"005930": ("반도체",)}),
            _refresh_theme_badges=lambda: badge_refreshes.append(True),
            _refresh_rankings=lambda: refreshed.append(True),
            _schedule_google_drive_upload=scheduled.append,
        )

        MainWindow._on_themes_changed(owner)

        self.assertEqual({"005930": ("반도체",)}, owner._themes)
        self.assertEqual([True], badge_refreshes)
        self.assertEqual([True], refreshed)
        self.assertEqual(["both"], scheduled)

    def test_offline_google_change_remains_marked_for_later_upload(self) -> None:
        saved: dict[str, str] = {}
        owner = SimpleNamespace(
            _google_drive_sync=SimpleNamespace(connected=False),
            _closing=False,
            _google_drive_pending_target="",
            _google_drive_dirty=False,
            _google_drive_strict_restore_staged=False,
            _settings=SimpleNamespace(set=lambda key, value: saved.__setitem__(key, value), get=lambda _key: "1"),
            _google_drive_debounce=SimpleNamespace(start=lambda: self.fail("offline change must not start upload")),
            _google_drive_timestamp_now=lambda: "2026-09-12T00:00:00Z",
        )

        MainWindow._schedule_google_drive_upload(owner, "both")

        self.assertTrue(owner._google_drive_dirty)
        self.assertEqual("both", owner._google_drive_pending_target)
        self.assertEqual("1", saved["google_drive_unsynced_changes"])

    def test_dirty_google_upload_sends_settings_and_current_themes_together(self) -> None:
        started: list[tuple[object, ...]] = []
        controller = SimpleNamespace(
            is_running=False,
            start=lambda *args: started.append(args) or True,
        )
        owner = SimpleNamespace(
            _closing=False,
            _google_drive_sync=SimpleNamespace(configured=True, connected=True),
            _google_drive_worker_controller=controller,
            _google_drive_pending_target="",
            _google_drive_close_pending=False,
            _google_drive_operation="",
            _google_drive_show_completion=False,
            _google_drive_active_target="",
            _google_drive_strict_restore_staged=False,
            _settings=SimpleNamespace(get=lambda key: "settings" if key == "google_drive_sync_target" else ""),
            _has_newer_local_google_drive_changes=lambda: True,
            statusBar=lambda: SimpleNamespace(showMessage=lambda _message: None),
        )

        MainWindow._start_google_drive_sync(owner, "upload")

        self.assertEqual("both", started[0][2])
        self.assertEqual("both", owner._google_drive_active_target)

    def test_staged_drive_restore_suppresses_exit_upload(self) -> None:
        from kiwoom_monitor.infrastructure.persistence.google_drive_sync import GoogleDriveSyncResult

        stopped: list[bool] = []
        owner = SimpleNamespace(
            _google_drive_operation="restore",
            _google_drive_show_completion=False,
            _google_drive_strict_restore_staged=False,
            _google_drive_close_pending=True,
            _google_drive_debounce=SimpleNamespace(stop=lambda: stopped.append(True)),
            _refresh_google_drive_status=lambda: None,
            statusBar=lambda: SimpleNamespace(showMessage=lambda _message: None),
        )
        MainWindow._on_google_drive_sync_completed(
            owner, GoogleDriveSyncResult("restore", "staged", "복원 예약"),
        )

        self.assertTrue(owner._google_drive_strict_restore_staged)
        self.assertFalse(owner._google_drive_close_pending)
        self.assertEqual([True], stopped)

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        # close()만으로는 Qt C++ 객체가 즉시 파괴되지 않는다. 여러 메인 창이
        # 누적되면 뒤쪽 테스트에서 네이티브 종료가 발생하므로 지연 삭제까지
        # 처리한다. 제품 창의 종료 동작은 변경하지 않는다.
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, MainWindow):
                widget.close()
                widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        gc.collect()

    def test_main_window_has_default_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())

            self.assertEqual(window.windowTitle(), "키움 실시간 모니터 (테스트)")
            table = window.centralWidget().findChild(type(window._table))
            self.assertEqual(table.columnCount(), 16)
            self.assertFalse(table.horizontalHeader().stretchLastSection())
            toolbar = window.findChild(QToolBar, "main_tools_toolbar")
            version_label = window.findChild(QLabel, "main_version_label")
            settings_button = window.findChild(QPushButton, "main_settings_button")
            candidate_button = window.findChild(QPushButton, "candidate_monitor_button")
            research_button = window.findChild(QPushButton, "research_button")
            automation_button = window.findChild(QPushButton, "mock_automation_button")
            widgets = [toolbar.widgetForAction(action) for action in toolbar.actions()]
            self.assertEqual(widgets.index(settings_button), widgets.index(version_label) + 1)
            self.assertEqual((settings_button.width(), settings_button.height()), (24, 22))
            self.assertEqual(candidate_button.text(), "")
            self.assertEqual(research_button.text(), "")
            self.assertEqual(automation_button.text(), "")
            self.assertEqual((candidate_button.width(), candidate_button.height()), (14, 14))
            self.assertEqual((research_button.width(), research_button.height()), (14, 14))
            self.assertEqual((automation_button.width(), automation_button.height()), (14, 14))
            self.assertEqual(widgets.index(candidate_button) + 1, widgets.index(research_button))
            self.assertEqual(widgets.index(research_button) + 1, widgets.index(automation_button))
            self.assertEqual(widgets.index(automation_button) + 1, widgets.index(window._rank_query_selector))
            window._refresh_rankings()
            window._ranking_worker.wait()
            QApplication.processEvents()
            self.assertEqual("삼성전자", table.item(0, 1).text())
            window._on_trade_tick(TradeTick("005930", 71000, None, None, 1, 71000, None, 1.45))
            window._flush_trade_tick_updates()
            self.assertEqual("71,000", table.item(0, 5).text())
            self.assertEqual("+1.45%", table.item(0, 3).text())
            self.assertEqual("-", table.item(0, 13).text())
            window._refresh_rankings()
            window._ranking_worker.wait()
            QApplication.processEvents()
            self.assertEqual("71,000", table.item(0, 5).text())
            window.close()

    def test_google_drive_upload_targets_exist_before_deferred_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())

            self.assertEqual("", window._google_drive_pending_target)
            self.assertEqual("", window._google_drive_active_target)
            window.close()

    def test_upper_limit_uses_actual_limit_price_and_can_be_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())
            window._refresh_rankings()
            window._ranking_worker.wait()
            QApplication.processEvents()

            window._fundamentals["005930"] = StockFundamentals(
                1_000, 50, upper_limit_price=91_900,
            )
            window._current_prices["005930"] = 91_900
            window._today_high_prices["005930"] = 91_900
            window._last_change_rates["005930"] = 29.97
            window._render_change_rate("005930")

            item = window._table.item(0, 3)
            self.assertEqual("+29.97%", item.text())
            self.assertEqual(window.UPPER_LIMIT_BADGE_COLOR.casefold(), item.background().color().name().casefold())
            self.assertTrue(item.font().bold())
            self.assertIsNone(window._table.cellWidget(0, 3))

            window._last_change_rates["005930"] = 0.0
            window._render_change_rate("005930")
            self.assertFalse(window._table.item(0, 3).font().bold())
            self.assertNotEqual(
                window.UPPER_LIMIT_BADGE_COLOR.casefold(),
                window._table.item(0, 3).background().color().name().casefold(),
            )

            window._current_prices["005930"] = 90_000
            window._last_change_rates["005930"] = 27.28
            window._render_change_rate("005930")
            self.assertEqual("+27.28%", window._table.item(0, 3).text())
            self.assertFalse(window._table.item(0, 3).font().bold())
            self.assertIsNone(window._table.cellWidget(0, 3))

            window._current_prices["005930"] = 91_900
            database.settings.set("upper_limit_highlight_enabled", "0")
            window._render_change_rate("005930")
            self.assertEqual("+27.28%", window._table.item(0, 3).text())
            self.assertFalse(window._table.item(0, 3).font().bold())
            self.assertIsNone(window._table.cellWidget(0, 3))
            window.close()

    def test_high_header_cycles_only_selected_periods(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            database.settings.set("high_header_cycle_periods", "250,historical")
            database.settings.set("high_distance_period", "250")
            window = MainWindow(database.settings, FakeRankingLoader())

            window._toggle_table_header_mode(13)
            self.assertEqual("historical", database.settings.get("high_distance_period"))
            window._toggle_table_header_mode(13)
            self.assertEqual("250", database.settings.get("high_distance_period"))
            window.close()

    def test_failed_realtime_minute_save_restores_pending_values(self) -> None:
        class FailingRepository:
            def upsert_many(self, values) -> None:
                raise OSError("temporary failure")

            def upsert_market_index_minutes(self, values) -> None:
                raise AssertionError("stock save must fail first")

        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)
            minute = datetime(2026, 9, 10, 10, 15)
            bar = MinuteOhlcv(minute, 100, 102, 99, 101, 10, 0.5)
            window._app_controller.minute_bar_repository = FailingRepository()
            window._app_controller.pending_minutes = {("005930", minute): bar}
            window._app_controller.pending_market_minutes = {
                ("kospi", minute): (2800.0, 2801.0, 2799.0, 2800.5, 100.0)
            }

            window._app_controller.flush_minute_cache()

            self.assertEqual(bar, window._app_controller.pending_minutes[("005930", minute)])
            self.assertIn(("kospi", minute), window._app_controller.pending_market_minutes)
            window.close()

    def test_background_cache_failures_restore_work_without_overwriting_newer_values(self) -> None:
        class Timer:
            def __init__(self) -> None:
                self.started = 0

            def isActive(self) -> bool:
                return False

            def start(self) -> None:
                self.started += 1

        old_minute = datetime(2026, 9, 14, 10, 30)
        new_minute = datetime(2026, 9, 14, 10, 31)
        old_bar = MinuteOhlcv(old_minute, 100, 102, 99, 101, 10, 0.5)
        newer_bar = MinuteOhlcv(old_minute, 100, 103, 99, 102, 20, 1.0)
        minute_timer = Timer()
        owner = AppController()
        def dispose_owner() -> None:
            owner.deleteLater()
            QCoreApplication.sendPostedEvents(owner, QEvent.Type.DeferredDelete)
        self.addCleanup(dispose_owner)
        owner.pending_minutes = {("005930", old_minute): newer_bar}
        owner.pending_prices = {"005930": 102}
        owner.pending_market_caps = {"005930": 2_100_000.0}
        owner.pending_highs = {(new_minute.date(), "005930"): 103}
        owner.ranking_now = lambda: new_minute
        owner.minute_cache_timer = minute_timer
        owner.price_cache_timer = Timer()

        owner.on_minute_cache_failed(
            {
                ("005930", old_minute): old_bar,
                ("000660", new_minute): MinuteOhlcv(
                    new_minute, 200, 204, 198, 203, 30, 2.0,
                ),
            },
            {("kospi", old_minute): (2800.0, 2801.0, 2799.0, 2800.5, 100.0)},
            "temporary failure",
        )
        owner.on_price_cache_failed(
            {"005930": 101, "000660": 203},
            {"005930": 102, "000660": 204},
            {"005930": 2_000_000.0, "000660": 900_000.0},
            object(),
            "temporary failure",
        )

        self.assertEqual(newer_bar, owner.pending_minutes[("005930", old_minute)])
        self.assertIn(("000660", new_minute), owner.pending_minutes)
        self.assertIn(("kospi", old_minute), owner.pending_market_minutes)
        self.assertEqual({"005930": 102}, owner.pending_prices)
        self.assertEqual({(new_minute.date(), "005930"): 103}, owner.pending_highs)
        self.assertEqual({"005930": 2_100_000.0}, owner.pending_market_caps)
        self.assertEqual(1, len(owner._price_cache_retries))
        prices, highs, market_caps, context = next(iter(owner._price_cache_retries.values()))
        self.assertEqual({"000660": 203}, prices)
        self.assertEqual({(new_minute.date(), "000660"): 204}, highs)
        self.assertEqual({"000660": 900_000.0}, market_caps)
        self.assertEqual(new_minute.date(), context["trade_date"])
        self.assertEqual(new_minute, context["observed_at"].replace(tzinfo=None))
        self.assertEqual({"005930": 102, "000660": 203}, {**prices, **owner.pending_prices})
        self.assertEqual({(new_minute.date(), "005930"): 103, (new_minute.date(), "000660"): 204},
                         {**highs, **owner.pending_highs})
        self.assertEqual(
            {"005930": 2_100_000.0, "000660": 900_000.0},
            {**market_caps, **owner.pending_market_caps},
        )
        self.assertEqual(1, minute_timer.started)
        self.assertEqual(1, owner.price_cache_timer.started)

    def test_failed_history_save_does_not_mark_code_as_loaded(self) -> None:
        class FailingRepository:
            def upsert_bars(self, code, bars) -> None:
                raise OSError("temporary failure")

            def purge_before(self, value) -> None:
                pass

        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)
            window._minute_bar_repository = FailingRepository()
            now = window._ranking_now()
            bars = (MinuteOhlcv(now.replace(second=0, microsecond=0), 100, 102, 99, 101, 10),)

            window._on_history_received("005930", bars)

            self.assertNotIn("005930", window._minute_history_codes)
            window.close()

    def test_fundamentals_refresh_keeps_combined_adjusted_high_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)
            window._fundamentals["000660"] = StockFundamentals(1_000, 50, 3_002_000)
            window._daily_highs["000660"] = DailyHighTargets(None, None, None)

            window._on_fundamentals_received(
                "000660", StockFundamentals(1_100, 45, 2_987_000)
            )

            self.assertEqual(3_002_000, window._fundamentals["000660"].high_250_price)
            self.assertEqual(1_100, window._fundamentals["000660"].market_cap_eok)
            window.close()

    def test_fundamentals_refresh_prefers_current_daily_adjusted_high(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)
            window._fundamentals["000660"] = StockFundamentals(1_000, 50, 3_100_000)
            window._daily_highs["000660"] = DailyHighTargets(None, None, 3_002_000, period_statuses=(("250", "ready"),))

            window._on_fundamentals_received(
                "000660", StockFundamentals(1_100, 45, 2_987_000)
            )

            self.assertEqual(3_002_000, window._fundamentals["000660"].high_250_price)
            window.close()

    def test_theme_header_groups_common_theme_and_sorts_group_by_change_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, themes={"가": "AI", "나": "AI", "다": "바이오"})

            class Stock:
                new_high_periods = ()
                current_price = None

                def __init__(self, rank: int, code: str, name: str, change: str) -> None:
                    self.rank, self.code, self.name, self.change_rate = rank, code, name, change

            window._app_controller.ranking.completed.emit((
                Stock(1, "000001", "가", "+1.00"),
                Stock(2, "000002", "나", "+3.00"),
                Stock(3, "000003", "다", "+5.00"),
            ))
            window._toggle_table_header_mode(2)
            self.assertEqual(["나", "가", "다"], [window._table.item(row, 1).text() for row in range(3)])
            window._toggle_table_header_mode(2)
            self.assertEqual(["가", "나", "다"], [window._table.item(row, 1).text() for row in range(3)])
            window.close()

    def test_equal_theme_counts_are_sorted_by_selected_trade_value_period(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            database.settings.set("theme_trade_summary_period", "day")
            window = MainWindow(database.settings, themes={"가": "반도체", "나": "반도체", "다": "원전", "라": "원전"})

            class Stock:
                new_high_periods = ()
                current_price = None

                def __init__(self, rank: int, code: str, name: str, change: str) -> None:
                    self.rank, self.code, self.name, self.change_rate = rank, code, name, change

            trade_values = {"000001": 3.0, "000002": 2.0, "000003": 12.0, "000004": 8.0}
            window._minute_aggregator.today_trade_value_eok = lambda code, _now=None: trade_values[code]
            window._app_controller.ranking.completed.emit((
                Stock(1, "000001", "가", "+4.00"),
                Stock(2, "000002", "나", "+2.00"),
                Stock(3, "000003", "다", "+3.00"),
                Stock(4, "000004", "라", "+1.00"),
            ))
            window._toggle_table_header_mode(2)
            self.assertEqual(["다", "라", "가", "나"], [window._table.item(row, 1).text() for row in range(4)])
            window.close()

    def test_top20_minute_rows_can_be_aggregated_to_five_minutes(self) -> None:
        rows = [
            (datetime(2026, 9, 8, 9, 1), 1.0, 2.0, 0.0),
            (datetime(2026, 9, 8, 9, 4), 3.0, 4.0, 1.0),
            (datetime(2026, 9, 8, 9, 5), 5.0, 6.0, 0.0),
        ]
        self.assertEqual(
            [
                (datetime(2026, 9, 8, 9, 0), 4.0, 6.0, 1.0),
                (datetime(2026, 9, 8, 9, 5), 5.0, 6.0, 0.0),
            ],
            MainWindow._aggregate_top20_rows(rows, 5),
        )

    def test_nas_top20_rows_keep_only_completed_market_splits(self) -> None:
        rows = MainWindow._nas_top20_rows((
            {"minute": "2026-09-08T09:01", "market_values": [1, 2, 3], "capture_state": "realtime_complete"},
            {"minute": "2026-09-08T09:00", "market_values": [9, 9, 9], "capture_state": "partial"},
        ))

        self.assertEqual([(datetime(2026, 9, 8, 9, 1), 1.0, 2.0, 3.0)], rows)

    def test_nas_market_data_is_available_before_realtime_subscription_opens(self) -> None:
        owner = SimpleNamespace(
            _active_api_route="central_waiting",
            _market_data_client=SimpleNamespace(load_stored_minute_bars=lambda *_args: ()),
        )

        self.assertTrue(MainWindow._uses_nas_market_data_source(owner))
        owner._active_api_route = "local_fallback"
        self.assertFalse(MainWindow._uses_nas_market_data_source(owner))

    def test_nas_recovery_forces_current_top20_minute_history_reload(self) -> None:
        class StatusBar:
            def showMessage(self, _message):
                pass

        requests = []
        owner = SimpleNamespace(
            _active_api_route="local_fallback",
            _minute_history_codes={"005930"},
            _row_by_code={"005930": 0, "000660": 1},
            statusBar=lambda: StatusBar(),
            _set_connected_api_status=lambda: None,
            _start_minute_history_loading=lambda codes, force=False: requests.append((codes, force)),
        )

        MainWindow._on_realtime_status_changed(owner, "나스 실시간 체결 구독 중 · 2종목")

        self.assertEqual("central", owner._active_api_route)
        self.assertEqual(set(), owner._minute_history_codes)
        self.assertEqual([(("005930", "000660"), True)], requests)

    def test_top20_collection_continues_while_five_minute_chart_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings)
            window._top20_index_timer.stop()
            current = [datetime(2026, 9, 9, 9, 0, 10)]
            window._ranking_now = lambda: current[0]
            window._top20_view_date = current[0].date()
            window._top20_view_mode = "5m"
            collector = window._top20_collector
            collector.minute = current[0].replace(second=0)
            collector.active_codes = ("005930",)
            collector.minute_codes = {"005930"}
            collector.cohort_segments = [("2026-09-09T09:00:00", ("005930",))]
            window._stock_markets["005930"] = "KOSPI"
            window._minute_aggregator.bucket_trade_value_eok = lambda *_args, **_kwargs: 1.0
            saved: list[datetime] = []
            window._save_top20_trade_value_index = lambda minute, *_args: saved.append(minute)

            window._update_top20_trade_value_index()
            self.assertEqual([(10, 1.0)], collector.samples)

            current[0] = datetime(2026, 9, 9, 9, 1)
            window._update_top20_trade_value_index()
            self.assertEqual([datetime(2026, 9, 9, 9, 0)], saved)
            window.close()

    def test_high_header_reenables_table_updates_after_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())
            window._refresh_rankings()
            window._ranking_worker.wait()
            QApplication.processEvents()

            window._toggle_table_header_mode(13)

            self.assertTrue(window._table.updatesEnabled())
            self.assertEqual("historical", database.settings.get("high_distance_period"))
            window.close()

    def test_high_cycle_periods_keep_fixed_order_and_recover_empty_value(self) -> None:
        self.assertEqual(("20", "historical"), selected_high_cycle_periods("historical,20"))
        self.assertEqual(("5", "20", "250", "historical"), selected_high_cycle_periods(""))

    def test_selected_row_background_is_below_near_high_and_above_rank_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())
            window._selected_table_code = "005930"

            self.assertEqual("#ddebf7", window._row_background_color("005930", 0).name())

            window._rank_changed_codes.add("005930")
            self.assertEqual("#ddebf7", window._row_background_color("005930", 0).name())

            window._near_high_codes.add("005930")
            self.assertEqual("#fde9e7", window._row_background_color("005930", 0).name())
            window.close()

    def test_hover_marker_keeps_strong_blue_independent_of_row_selection_color(self) -> None:
        self.assertEqual("#0078d7", NxtMarkerDelegate.ACTIVE_MARKER_COLOR.name())
        delegate = NxtMarkerDelegate()
        model = QStandardItemModel(1,1)
        for state in (QStyle.StateFlag.State_MouseOver, QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Selected):
            option = QStyleOptionViewItem(); option.rect = QRect(0,0,80,24); option.state = state
            image = QImage(80,24,QImage.Format.Format_ARGB32); image.fill(Qt.GlobalColor.white)
            painter = QPainter(image)
            delegate.paint(painter, option, model.index(0,0)); painter.end()
            self.assertEqual('#0078d7', image.pixelColor(1,12).name())

    def test_delegate_remembers_only_the_clicked_cell_marker(self) -> None:
        delegate = NxtMarkerDelegate()
        delegate.set_selected_cell((2, 4))
        self.assertEqual((2, 4), delegate._selected_cell)
        delegate.set_selected_cell(None)
        self.assertIsNone(delegate._selected_cell)
        model = QStandardItemModel(3,5)
        delegate.set_selected_cell((2,4))
        for row,column,active in ((2,4,True),(2,3,False),(1,4,False)):
            option = QStyleOptionViewItem(); option.rect = QRect(0,0,80,24); option.state = QStyle.StateFlag.State_None
            image = QImage(80,24,QImage.Format.Format_ARGB32); image.fill(Qt.GlobalColor.white)
            painter=QPainter(image); delegate.paint(painter,option,model.index(row,column)); painter.end()
            self.assertEqual(active, image.pixelColor(1,12).name() == '#0078d7')
        delegate.set_selected_cell(None)
        image.fill(Qt.GlobalColor.white)
        painter=QPainter(image); delegate.paint(painter,option,model.index(2,4)); painter.end()
        self.assertNotEqual('#0078d7', image.pixelColor(1,12).name())

    def test_rank_changed_highlight_default_duration_is_one_second(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            self.assertEqual("1.00", database.settings.get("rank_changed_highlight_seconds"))

    def test_trade_value_cell_alert_stays_above_near_high_row_background(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Database(Path(temporary_directory) / "monitor.sqlite3")
            database.initialize()
            window = MainWindow(database.settings, FakeRankingLoader())
            window._app_controller.ranking.completed.emit(FakeRankingLoader().load_top_stocks())
            item = window._table.item(0, 6)
            item.setData(window.TRADE_VALUE_ALERT_ROLE, True)
            window._near_high_codes.add("005930")

            window._apply_row_background("005930")

            self.assertEqual("#f4cccc", item.background().color().name())
            self.assertEqual("#fde9e7", window._table.item(0, 5).background().color().name())
            window._theme_trade_summary_timer.stop()
            window.close()


if __name__ == "__main__":
    unittest.main()
