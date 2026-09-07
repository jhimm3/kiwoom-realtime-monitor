from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from kiwoom_monitor.journal_process import (
    DetachedChartPanelSettingsDialog, DetachedChartSettingsDialog, DetachedChartWindow,
)
from kiwoom_monitor.application.trade_history_service import TradeFill


class DetachedChartWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

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

    def test_visual_settings_are_applied_independently_to_all_panels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "charts.ini"), QSettings.Format.IniFormat)
            settings.setValue("detached_chart_color_background", "#20252B")
            settings.setValue("detached_chart_color_grid", "#4A5560")
            settings.setValue("detached_chart_color_up", "#FF665E")
            settings.setValue("detached_chart_color_down", "#55A7FF")
            window = DetachedChartWindow(settings)
            window.configure("#FFFFFF", True, 0, 0, True, 1, "#111111", "#222222", "#333333", "#444444")
            self.assertEqual("#20252b", window.panels[0].chart._background.name())
            self.assertEqual("#4a5560", window.panels[1].chart._grid.name())
            self.assertEqual("#ff665e", window.panels[2].chart._up_color.name())
            self.assertEqual("#55a7ff", window.panels[2].chart._down_color.name())
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

if __name__ == "__main__":
    unittest.main()
