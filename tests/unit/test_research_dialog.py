from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.presentation.research_dialog import (
    ResearchDialog,
    format_market_regime_summary,
    format_research_result_rows,
)


class ResearchDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_comparison_rows_keep_sealed_values_as_na(self) -> None:
        rows = format_research_result_rows({
            "kind": "rank_comparison",
            "comparison": {"fold_comparisons": [{
                "name": "final", "status": "SEALED",
                "baseline_net_pnl_won": None, "variant_net_pnl_won": None,
                "net_pnl_delta_won": None, "avoided_loss_won": None,
                "missed_profit_won": None,
            }]},
        })
        self.assertEqual(("final", "SEALED", "N/A", "N/A", "N/A", "N/A", "N/A"), rows[0])

    def test_single_run_uses_variant_column_without_inventing_comparison(self) -> None:
        rows = format_research_result_rows({
            "kind": "single_run",
            "report": {"fold_reports": [{
                "name": "train", "status": "ELIGIBLE", "net_realized_pnl_won": 1234,
            }]},
        })
        self.assertEqual("1,234원", rows[0][3])
        self.assertEqual("", rows[0][2])

    def test_market_regime_summary_keeps_unknown_reason_visible(self) -> None:
        text = format_market_regime_summary({
            "value": {
                "market_type": "UNKNOWN",
                "reasons": ["mature_breakout_sample_insufficient"],
            },
        })
        self.assertEqual(
            "시장 UNKNOWN (mature_breakout_sample_insufficient)", text,
        )

    def test_limited_search_keeps_drawdown_sample_and_reason_visible(self) -> None:
        rows = format_research_result_rows({
            "kind": "limited_search",
            "candidate_cards": [{
                "ordinal": 0, "variant": "baseline", "status": "INELIGIBLE",
                "net_pnl_won": 100, "max_drawdown_won": 30,
                "closed_trade_count": 2, "active_day_count": 1,
                "reasons": ["sample_insufficient"],
            }],
        })
        self.assertEqual(
            ("#1 baseline", "INELIGIBLE", "30원", "100원", "2", "1", "sample_insufficient"),
            rows[0],
        )

    def test_pending_automatic_resume_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ResearchDialog(Path(directory))
            dialog._continuous.setChecked(True)
            dialog._resume_timer.start()
            dialog._cancel.setEnabled(True)
            dialog._request_cancel()
            self.assertFalse(dialog._resume_timer.isActive())
            self.assertTrue(dialog._cancel_path.is_file())
            self.assertFalse(dialog._cancel.isEnabled())
            dialog.stop()

    def test_unchecking_continuous_mode_stops_pending_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dialog = ResearchDialog(Path(directory))
            dialog._continuous.setChecked(True)
            dialog._resume_timer.start()
            dialog._continuous.setChecked(False)
            self.assertFalse(dialog._resume_timer.isActive())
            dialog.stop()


if __name__ == "__main__":
    unittest.main()
