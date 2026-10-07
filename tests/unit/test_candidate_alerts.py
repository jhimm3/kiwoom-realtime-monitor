from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.presentation.candidate_monitor_dialog import CandidateMonitorDialog


class FakeClient:
    def load_candidate_events(self, **_kwargs):
        return {"high_watermark": 0, "events": [], "quality": {"status": "DISABLED"}}


def _event(event_id: str, sequence: int) -> dict:
    return {
        "event_id": event_id, "accepted_sequence": sequence, "symbol": "005930",
        "available_at": "2026-09-12T00:00:00+00:00",
        "expires_at": "2099-09-12T00:01:00+00:00", "status": "ACTIVE",
        "signal_reference_price": 1000, "quantity": 1,
    }


class CandidateAlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_initial_sync_is_silent_and_duplicate_transition_does_not_alert(self) -> None:
        with patch(
            "kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start"
        ), patch.object(QApplication, "beep") as beep:
            dialog = CandidateMonitorDialog(FakeClient())
            dialog._alert_enabled.setChecked(True)
            dialog._apply_page({
                "initial_sync": True, "high_watermark": 1,
                "events": [_event("event-1", 1)], "quality": {"status": "READY"},
            })
            dialog._apply_page({
                "initial_sync": False, "high_watermark": 1,
                "events": [_event("event-1", 1)], "quality": {"status": "READY"},
            })
            self.assertEqual(1, dialog._table.rowCount())
            beep.assert_not_called()

            dialog._apply_page({
                "initial_sync": False, "high_watermark": 2,
                "events": [_event("event-2", 2)], "quality": {"status": "READY"},
            })
            beep.assert_called_once()
            self.assertEqual("2", str(dialog._settings.value("last_consumed_sequence")))
            dialog._settings.clear()
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
