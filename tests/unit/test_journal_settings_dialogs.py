from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from kiwoom_monitor.application.strategy_pack import MIMOSA_MANIFEST
from kiwoom_monitor.infrastructure.persistence.journal_database import JournalRepository
from kiwoom_monitor.presentation.journal_settings_dialogs import (
    JournalChartSettingsDialog, JournalSettingsDialog, MOVING_AVERAGE_DEFAULTS,
)


class JournalSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_reset_restores_cost_rates_and_strategy_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = JournalSettingsDialog(
                "rules.txt", 1.0, 1.0, False, "#ffffff", True, 40.0, 100.0,
                True, 2.0, "#111111", "#222222", "#333333", "#444444",
                (MIMOSA_MANIFEST,), "basic_first", JournalRepository(Path(directory) / "journal.db"),
            )
            dialog._reset_defaults()
            self.assertAlmostEqual(0.015, dialog.estimated_buy_cost_rate.value())
            self.assertAlmostEqual(0.215, dialog.estimated_sell_cost_rate.value())
            self.assertEqual("together", dialog.strategy_result_mode.currentData())
            dialog.close()

    def test_chart_settings_exposes_all_moving_average_defaults(self) -> None:
        owner = QWidget()
        owner._ctrl_wheel_zoom_enabled = True; owner._trade_value_threshold = 40.0
        owner._daily_trade_value_threshold = 100.0; owner._show_trade_details = True
        owner._drawing_line_width = 1.5; owner._chart_background = "#ffffff"
        owner._rectangle_color = "#111111"; owner._line_color = "#222222"
        owner._horizontal_line_color = "#333333"; owner._vertical_line_color = "#444444"
        owner._moving_average_styles = dict(MOVING_AVERAGE_DEFAULTS)
        dialog = JournalChartSettingsDialog(owner)
        self.assertEqual(set(MOVING_AVERAGE_DEFAULTS), set(dialog.moving_averages))
        self.assertFalse(hasattr(dialog, "export_daily_chart"))
        self.assertFalse(hasattr(dialog, "export_minute_view_count"))
        dialog.close(); owner.close()


if __name__ == "__main__":
    unittest.main()
