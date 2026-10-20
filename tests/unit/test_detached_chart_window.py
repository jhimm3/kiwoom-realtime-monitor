from __future__ import annotations

import gc
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QSettings, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from kiwoom_monitor.presentation.detached_chart_window import DetachedChartWindow
from kiwoom_monitor.presentation.detached_chart_settings import (
    DetachedChartPanelSettingsDialog, DetachedChartSettingsDialog,
)
from kiwoom_monitor.application.trade_history_service import TradeFill


class DetachedChartWindowTests(unittest.TestCase):
    def test_panel_applies_each_minute_source_once_and_keeps_daily_empty_fallback(self) -> None:
        from kiwoom_monitor.presentation import journal_chart_widget
        from kiwoom_monitor.application.trade_chart import aggregate_chart_rows
        raw = (("2026-09-09T09:00:00", 100, 110, 90, 105, 10, 1.0, "api_confirmed"),
               ("2026-09-09T09:01:00", 105, 120, 100, 115, 20, 2.0, "realtime_partial"))
        daily = (("2026-09-08T00:00", 90, 100, 80, 95, 30, 3.0),)
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            window = DetachedChartWindow(settings)
            try:
                for panel in window.panels:
                    panel.source.setCurrentText("현재 종목")
                    panel.interval.setCurrentText("1분")
                with patch.object(journal_chart_widget, "aggregate_chart_rows", wraps=aggregate_chart_rows) as aggregate:
                    window.set_data(None, raw, (), ())
                with self.subTest(stage="single-aggregation"):
                    self.assertEqual(6, aggregate.call_count)
                self.assertTrue(all(panel.chart._all_rows == raw for panel in window.panels))
                panel = window.panels[0]
                panel.interval.setCurrentText("일봉")
                annotation = (("line", raw[0][0], 100.0, raw[1][0], 115.0),)
                panel.chart.set_annotations(annotation)
                for source_raw, source_daily in ((raw, daily), (raw, ()), ((), daily), ((), ()), (raw, ())):
                    panel.set_data(None, source_raw, source_daily, ())
                    expected = source_daily or aggregate_chart_rows(source_raw, "일봉")
                    self.assertEqual(expected, panel.chart._all_rows)
                    self.assertEqual(annotation, panel.chart.annotations())
                    self.assertIn("부분 자료", panel.chart.toolTip()) if source_raw else self.assertEqual("", panel.chart.toolTip())
                # Public setters still force a daily fallback and view reapplication.
                chart = panel.chart
                chart.set_rows(raw); chart.set_daily_rows(daily); chart.clear_daily_rows()
                chart.set_interval("일봉")
                self.assertEqual(aggregate_chart_rows(raw, "일봉"), chart._all_rows)
                chart.set_daily_rows(())
                self.assertEqual((), chart._all_rows)
                chart.set_interval("일봉")
                self.assertEqual(aggregate_chart_rows(raw, "일봉"), chart._all_rows)
                chart.set_rows(()); chart.set_interval("일봉")
                self.assertEqual((), chart._all_rows)
                chart.set_interval("1분"); chart.set_rows(raw); chart.set_view_count(1); chart.set_view_start(0)
                chart.set_interval("1분")
                self.assertEqual(1, chart._view_start)
            finally:
                window.close(); window.deleteLater()

    def test_native_journal_hidden_reopen_and_selected_sources_use_only_consumed_reads(self) -> None:
        import sqlite3
        from contextlib import closing
        from datetime import date
        from PySide6.QtCore import QTimer
        from unittest.mock import Mock
        from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
        from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
        from kiwoom_monitor.journal_process import JournalWindow

        day = date(2026, 9, 9)
        now = datetime.fromisoformat("2026-09-09T15:30:00+09:00")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); monitor = root / "monitor.sqlite3"
            Database(monitor).initialize()
            lookup = StockRepository(monitor)
            lookup.upsert("005930", "삼성전자"); lookup.upsert("000660", "SK하이닉스")
            with closing(sqlite3.connect(monitor)) as connection, connection:
                for market, price in (("kospi", 2000), ("kosdaq", 700)):
                    connection.execute("INSERT INTO market_index_minute_bars VALUES(?,?,?,?,?,?,?,?)",
                                       (day.isoformat(), market, "2026-09-09T09:00", price, price+5, price-5, price+1, 10.0))
                    connection.execute("INSERT INTO market_index_daily_bars VALUES(?,?,?,?,?,?,?,?)",
                                       (day.isoformat(), market, price, price+10, price-10, price+2, 100, 20.0))
            settings = QSettings(str(root / "journal.ini"), QSettings.Format.IniFormat)
            settings.setValue("auto_history_sync_enabled", False)
            with patch("kiwoom_monitor.journal_process.QSettings", return_value=settings), \
                    patch("kiwoom_monitor.journal_process.create_query_client", return_value=SimpleNamespace(server_now=lambda: now)), \
                    patch.object(QTimer, "singleShot"):
                owner = JournalWindow(monitor, root / "journal.sqlite3", root / "api.json")
            for timer in owner.findChildren(QTimer): timer.stop()
            episode = group_trade_episodes((
                TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 9, 9, 9), 1, 100),
                TradeFill("2", "005930", "삼성전자", "매도", datetime(2026, 9, 9, 9, 2), 1, 110),
            ))[0]
            owner._active_episode = episode
            owner._selected_history_code = "005930"; owner._selected_history_day = day
            owner._selected_history_range = (day, day)
            owner._repo.save_bar_backfill_result("005930", day, (MinuteOhlcv(datetime(2026, 9, 9, 9), 100, 110, 90, 105, 10),), now)
            owner._repo.save_bar_backfill_result("000660", day, (MinuteOhlcv(datetime(2026, 9, 9, 9), 200, 210, 190, 205, 20),), now)
            owner._repo.upsert_daily_bars("005930", (("2026-09-09", 100, 120, 90, 115, 30, 3.0),), now)
            try:
                with patch.object(owner, "_start_daily_chart"), patch.object(owner, "_start_bar_confirmation"), \
                        patch.object(owner, "_start_market_index_backfill") as backfill, \
                        patch.object(owner._repo, "load_monitor_market_index_bars", wraps=owner._repo.load_monitor_market_index_bars) as index_minute, \
                        patch.object(owner._repo, "load_monitor_market_index_daily", wraps=owner._repo.load_monitor_market_index_daily) as index_daily:
                    owner._open_detached_charts()
                    detached = owner._detached_chart_window
                    self.assertTrue(detached.isVisible())
                    with self.subTest(stage="no-index-selected"):
                        self.assertEqual((0, 0), (index_minute.call_count, index_daily.call_count))
                    self.assertEqual(105, detached.panels[0].chart._all_rows[-1][4])
                    annotation = (("line", "2026-09-09T09:00:00", 100.0, "2026-09-09T09:01:00", 110.0),)
                    detached.panels[0].chart.set_annotations(annotation)
                    owner._history_chart.set_annotations(annotation)
                    detached.hide()
                    with patch.object(owner, "_load_selected_chart_rows", wraps=owner._load_selected_chart_rows) as current:
                        owner._sync_detached_charts()
                        with self.subTest(stage="hidden"):
                            current.assert_not_called()
                    owner._repo.save_bar_backfill_result("005930", day, (MinuteOhlcv(datetime(2026, 9, 9, 9, 1), 105, 140, 100, 130, 10),), now)
                    owner._open_detached_charts()
                    self.assertIs(detached, owner._detached_chart_window)
                    self.assertEqual(130, detached.panels[0].chart._all_rows[-1][4])
                    self.assertEqual(annotation, detached.panels[0].chart.annotations())
                    changes = Mock(); detached.source_changed.connect(changes)
                    index_minute.reset_mock(); index_daily.reset_mock()
                    detached.panel_checks[3].setChecked(True)
                    with self.subTest(stage="enable-index"):
                        self.assertEqual(1, changes.call_count)
                        self.assertEqual(["kospi"], [call.args[1] for call in index_minute.call_args_list])
                        self.assertEqual(["kospi"], [call.args[1] for call in index_daily.call_args_list])
                    self.assertEqual(2001, detached.panels[3].chart._all_rows[-1][4])
                    with self.subTest(stage="index-backfill"):
                        self.assertGreater(backfill.call_count, 0)
                    changes.reset_mock(); detached._reflow()
                    self.assertEqual(0, changes.call_count)
                    detached.panels[5].source.setCurrentText("SK하이닉스 · 000660")
                    detached.panel_checks[5].setChecked(True)
                    with self.subTest(stage="enable-custom"):
                        self.assertTrue(detached.panels[5].chart._raw_rows)
                        self.assertEqual(205, detached.panels[5].chart._raw_rows[-1][4])
                    detached.hide(); changes.reset_mock(); index_minute.reset_mock()
                    with closing(sqlite3.connect(monitor)) as connection, connection:
                        connection.execute("INSERT INTO minute_bars VALUES(?,?,?,?,?,?,?,?,?)",
                            (day.isoformat(), "000660", "2026-09-09T09:01", 205, 240, 200, 230, 30, 3.0))
                        connection.execute("INSERT INTO daily_bars(stock_code,trade_date,open_price,high_price,low_price,close_price,volume,trade_value_eok) VALUES('000660','2026-09-09',200,240,190,231,50,5.0)")
                    detached.panel_checks[4].setChecked(True)
                    with self.subTest(stage="hidden-toggle"):
                        self.assertEqual(1, changes.call_count)
                        index_minute.assert_not_called()
                        self.assertEqual(230, owner._repo.load_bars("000660", datetime(2026,9,9))[-1][4])
                        self.assertEqual(231, owner._repo.load_daily_bars("000660", day)[-1][4])
                    owner._open_detached_charts()
                    self.assertEqual(701, detached.panels[4].chart._raw_rows[-1][4])
                    detached.hide()
                    with patch.object(owner._repo, "load_daily_bars", side_effect=sqlite3.OperationalError("controlled chart read failure")):
                        with self.assertRaisesRegex(sqlite3.OperationalError, "controlled chart read failure"):
                            owner._open_detached_charts()
                    with self.subTest(stage="open-failure-visibility"):
                        self.assertFalse(detached.isVisible())
                    owner._open_detached_charts()
                    self.assertTrue(detached.isVisible())
                    self.assertEqual(130, detached.panels[0].chart._all_rows[-1][4])
            finally:
                owner._active_episode = None  # The fixture injected chart context, not an edited review form.
                owner.close(); owner.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        # close()는 창을 숨길 뿐 C++ 객체를 즉시 파괴하지 않는다. 여러 테스트가
        # 남긴 차트/QSettings 자식이 인터프리터 종료 때 역순 파괴되면 Windows의
        # Qt 플러그인에서 간헐적으로 네이티브 종료 오류가 발생할 수 있다.
        for widget in QApplication.topLevelWidgets():
            if isinstance(
                widget,
                (DetachedChartWindow, DetachedChartSettingsDialog, DetachedChartPanelSettingsDialog),
            ):
                widget.close()
                widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        gc.collect()

    def test_each_panel_has_independent_interval_count_and_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            window = DetachedChartWindow(settings)
            self.assertEqual(6, len(window.panels))
            self.assertEqual(("1분", "5분", "일봉"), tuple(panel.interval.currentText() for panel in window.panels[:3]))
            self.assertEqual("코스피 지수", window.panels[3].source.currentText())
            self.assertEqual("코스닥 지수", window.panels[4].source.currentText())
            window.panels[0].view_count.setValue(30)
            window.panels[1].view_count.setValue(60)
            window.panel_checks[1].setChecked(False)
            window.close()
            restored = DetachedChartWindow(settings)
            self.assertEqual(30, restored.panels[0].view_count.value())
            self.assertEqual(60, restored.panels[1].view_count.value())
            self.assertFalse(restored.panel_checks[1].isChecked())
            self.assertFalse(restored.panel_checks[5].isChecked())
            restored.close()

    def test_other_stock_can_be_selected_per_panel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            window = DetachedChartWindow(settings); window.set_stock_choices((("005930", "삼성전자"), ("000660", "SK하이닉스")))
            window.panels[0].source.setCurrentText("삼성전자 · 005930")
            self.assertEqual("005930", window.panels[0].selected_stock_code())
            self.assertEqual(("005930",), window.selected_stock_codes()); window.close()

    def test_hidden_comparison_panels_do_not_request_stock_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            window = DetachedChartWindow(settings)
            window.set_stock_choices((("005930", "삼성전자"), ("000660", "SK하이닉스")))
            window.panels[0].source.setCurrentText("삼성전자 · 005930")
            window.panels[5].source.setCurrentText("SK하이닉스 · 000660")
            window.panel_checks[5].setChecked(False)

            self.assertEqual(("005930",), window.selected_stock_codes())
            self.assertEqual((), window.selected_index_markets())
            window.close()

    def test_comparison_stock_rows_are_applied_to_chart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            window = DetachedChartWindow(settings)
            window.set_stock_choices((("005930", "삼성전자"),))
            panel = window.panels[0]
            panel.source.setCurrentText("삼성전자 · 005930")
            rows = (("2026-09-09T09:10:00", 100, 105, 99, 103, 1000, 1.2, "api_confirmed"),)

            panel.set_data(None, (), (), (), {"005930": rows})

            self.assertEqual(rows, panel.chart._rows)
            self.assertTrue(panel.chart._reference_fills)
            window.close()

    def test_comparison_chart_keeps_fills_only_for_holding_background(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            window = DetachedChartWindow(settings); panel = window.panels[0]
            fill = TradeFill("1", "005930", "삼성전자", "매수", datetime(2026, 8, 28, 9, 10), 1, 100, "", "KRX")
            episode = SimpleNamespace(fills=(fill,), summary=SimpleNamespace(stock_name="삼성전자", stock_code="005930"))
            panel.source.setCurrentText("코스피 지수")
            panel.set_data(episode, (), (), (), {"kospi": (("2026-08-28T09:10", 100, 101, 99, 100, 0, None, "확정"),)})
            self.assertTrue(panel.chart._reference_fills)
            self.assertEqual((fill,), panel.chart._fills)
            self.assertFalse(panel.show_trades.isEnabled())
            from kiwoom_monitor.presentation import journal_chart_widget
            with patch.object(journal_chart_widget, "trade_marker_polygon", wraps=journal_chart_widget.trade_marker_polygon) as marker:
                image = panel.chart.grab().toImage()
                self.assertFalse(image.isNull())
                marker.assert_not_called()
                panel.chart.set_reference_fills(False)
                panel.chart.grab()
                self.assertGreater(marker.call_count, 0)
            window.close()

    def test_visual_settings_follow_shared_journal_background(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            settings.setValue("detached_chart_color_background", "#20252B")
            settings.setValue("detached_chart_color_grid", "#4A5560")
            settings.setValue("detached_chart_color_up", "#FF665E")
            settings.setValue("detached_chart_color_down", "#55A7FF")
            settings.setValue("detached_chart_2_accent", "#abcdef")
            window = DetachedChartWindow(settings)
            window.configure("#FFFFFF", True, 0, 0, True, 1, "#111111", "#222222", "#333333", "#444444")
            self.assertTrue(all(panel.chart._background.name() == "#ffffff" for panel in window.panels))
            self.assertNotEqual("#4a5560", window.panels[1].chart._grid.name())
            self.assertNotEqual(window.panels[0].title_label.styleSheet(), window.panels[1].title_label.styleSheet())
            self.assertIn("#abcdef", window.panels[2].title_label.styleSheet())
            self.assertEqual("DetachedChartPanel", window.panels[0].objectName())
            self.assertIn("QWidget#DetachedChartPanel", window.panels[0].styleSheet())
            self.assertIn("border:1px solid #BFC6CF", window.panels[0].styleSheet())
            self.assertTrue(window.panels[0].testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
            self.assertEqual(0, window.panel_layout.horizontalSpacing())
            self.assertEqual(0, window.panel_layout.verticalSpacing())
            self.assertTrue(callable(window._export_current_view))
            window.close()

    def test_window_can_shrink_and_panels_are_inside_scroll_area(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            window = DetachedChartWindow(settings); window.resize(560, 400)
            self.app.processEvents()
            self.assertLessEqual(window.minimumWidth(), 560)
            self.assertTrue(window.panel_scroll.widgetResizable())
            self.assertEqual(155, window.panels[0].chart.minimumHeight())
            window.close()

    def test_trade_details_toggle_updates_all_open_detached_panels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            window = DetachedChartWindow(settings)
            window.set_trade_details_visible(False)
            self.assertTrue(all(not panel.chart._show_trade_details for panel in window.panels))
            window.set_trade_details_visible(True)
            self.assertTrue(all(panel.chart._show_trade_details for panel in window.panels))
            window.close()

    def test_same_stock_and_interval_share_count_and_annotations_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            window = DetachedChartWindow(settings)
            episode = SimpleNamespace(fills=(), summary=SimpleNamespace(stock_name="삼성전자", stock_code="005930"))
            window.set_data(episode, (), (), ())
            window.panels[0].interval.setCurrentText("1분")
            window.panels[1].interval.setCurrentText("1분")
            annotation = (("가로선", datetime(2026, 9, 8, 9), 100.0, datetime(2026, 9, 8, 10), 100.0),)

            window.set_shared_chart_state("stock:005930", "1분", 77, annotation)

            self.assertEqual(77, window.panels[0].chart.view_count())
            self.assertEqual(annotation, window.panels[1].chart.annotations())
            window.panels[2].interval.setCurrentText("일봉")
            self.assertEqual((), window.panels[2].chart.annotations())
            window.close()

    def test_chart_can_be_dragged_to_older_bars_when_drawing_is_off(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            window = DetachedChartWindow(settings); panel = window.panels[0]
            rows = tuple(
                (f"2026-09-09T{9 + index // 60:02d}:{index % 60:02d}:00", 100, 101, 99, 100, 1, 0.1)
                for index in range(180)
            )
            episode = SimpleNamespace(fills=(), summary=SimpleNamespace(stock_name="삼성전자", stock_code="005930"))
            panel.set_data(episode, rows, (), ())
            panel.view_count.setValue(30)
            original = panel.chart._view_start
            accepted: list[bool] = []
            press = SimpleNamespace(
                position=lambda: QPointF(200, 100),
                button=lambda: Qt.MouseButton.LeftButton,
                accept=lambda: accepted.append(True),
            )
            move = SimpleNamespace(position=lambda: QPointF(400, 100), accept=lambda: accepted.append(True))
            release = SimpleNamespace(button=lambda: Qt.MouseButton.LeftButton, accept=lambda: accepted.append(True))

            panel.chart.mousePressEvent(press)
            panel.chart.mouseMoveEvent(move)
            panel.chart.mouseReleaseEvent(release)

            self.assertLess(panel.chart._view_start, original)
            self.assertTrue(accepted)
            window.close()

    def test_current_configuration_image_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = QSettings(str(root / "charts.ini"), QSettings.Format.IniFormat)
            window = DetachedChartWindow(settings)
            for check in window.panel_checks[1:]:
                check.setChecked(False)
            window.configure("#FFFFFF", True, 0, 0, True, 2, "#7B1FA2", "#7B1FA2", "#F57C00", "#00897B")
            window.panels[0].set_data(
                None,
                (("2026-09-09T09:00:00", 100, 105, 99, 103, 1000, 1.2),),
                (),
                (),
            )
            output = root / "current-view.png"

            with patch(
                "kiwoom_monitor.presentation.detached_chart_window.QFileDialog.getSaveFileName",
                return_value=(str(output), "PNG 이미지 (*.png)"),
            ), patch(
                "kiwoom_monitor.presentation.detached_chart_window.QMessageBox.information"
            ), patch(
                "kiwoom_monitor.presentation.detached_chart_window.QMessageBox.warning"
            ) as warning:
                window._export_current_view()

            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)
            image = QImage(str(output))
            self.assertFalse(image.isNull())
            self.assertGreater(image.width(), 0)
            self.assertGreater(image.height(), 0)
            warning.assert_not_called()
            window.close()

if __name__ == "__main__":
    unittest.main()
