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
from PySide6.QtWidgets import QApplication

from kiwoom_monitor.presentation.detached_chart_window import DetachedChartWindow
from kiwoom_monitor.presentation.detached_chart_settings import (
    DetachedChartPanelSettingsDialog, DetachedChartSettingsDialog,
)
from kiwoom_monitor.application.trade_history_service import TradeFill


class DetachedChartWindowTests(unittest.TestCase):
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
            window.close()

    def test_visual_settings_follow_shared_journal_background(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            settings.setValue("detached_chart_color_background", "#20252B")
            settings.setValue("detached_chart_color_grid", "#4A5560")
            settings.setValue("detached_chart_color_up", "#FF665E")
            settings.setValue("detached_chart_color_down", "#55A7FF")
            window = DetachedChartWindow(settings)
            window.configure("#FFFFFF", True, 0, 0, True, 1, "#111111", "#222222", "#333333", "#444444")
            self.assertTrue(all(panel.chart._background.name() == "#ffffff" for panel in window.panels))
            self.assertNotEqual("#4a5560", window.panels[1].chart._grid.name())
            self.assertNotEqual(window.panels[0].title_label.styleSheet(), window.panels[1].title_label.styleSheet())
            self.assertEqual("DetachedChartPanel", window.panels[0].objectName())
            self.assertIn("QWidget#DetachedChartPanel", window.panels[0].styleSheet())
            self.assertIn("border:1px solid #BFC6CF", window.panels[0].styleSheet())
            self.assertTrue(window.panels[0].testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
            self.assertEqual(0, window.panel_layout.horizontalSpacing())
            self.assertEqual(0, window.panel_layout.verticalSpacing())
            self.assertTrue(callable(window._export_current_view))
            window.close()

    def test_settings_dialog_saves_selected_colors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            dialog = DetachedChartSettingsDialog(settings)
            dialog.colors["grid"].setText("#123456")
            dialog.colors["background"].setText("#202020")
            dialog.colors["holding"].setText("#80cbc4")
            dialog.save(); settings.sync()
            self.assertEqual("#123456", settings.value("detached_chart_color_grid"))
            for index in range(6):
                self.assertEqual("#202020", settings.value(f"detached_chart_{index}_background"))
                self.assertEqual("#80cbc4", settings.value(f"detached_chart_{index}_holding"))
            dialog.close()

    def test_each_panel_saves_its_own_background_and_accent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            dialog = DetachedChartPanelSettingsDialog(settings, 2, "#ffffff", "#112233")
            dialog.edits["background"].setText("#202020"); dialog.edits["accent"].setText("#abcdef")
            dialog.edits["holding"].setText("#80cbc4")
            dialog.save(); settings.sync()
            self.assertEqual("#202020", settings.value("detached_chart_2_background"))
            self.assertEqual("#abcdef", settings.value("detached_chart_2_accent"))
            self.assertEqual("#80cbc4", settings.value("detached_chart_2_holding"))
            dialog._reset()
            self.assertEqual("#ffffff", dialog.edits["background"].text())
            self.assertEqual("#112233", dialog.edits["accent"].text())
            self.assertEqual("#ffeb96", dialog.edits["holding"].text().lower())
            dialog.close()

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
            warning.assert_not_called()
            window.close()

if __name__ == "__main__":
    unittest.main()
