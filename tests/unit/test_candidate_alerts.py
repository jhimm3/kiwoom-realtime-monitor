from __future__ import annotations

import os
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.presentation.candidate_monitor_dialog import CandidateMonitorDialog, CandidatePollWorker


class FakeClient:
    def load_candidate_events(self, **_kwargs):
        return {"high_watermark": 0, "events": [], "quality": {"status": "DISABLED"}}


class MemorySettings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def value(self, key: str, default=None):
        return self.values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value

    def clear(self) -> None:
        self.values.clear()


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
        settings = MemorySettings()
        with patch(
            "kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start"
        ), patch(
            "kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings",
            return_value=settings,
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

    def test_candidate_polling_parks_without_consumers_and_resumes_silently(self) -> None:
        class CountingClient:
            def __init__(self) -> None:
                self.lock = threading.Lock()
                self.calls = 0

            def load_candidate_events(self, **_kwargs):
                with self.lock:
                    self.calls += 1
                    sequence = self.calls
                return {
                    "high_watermark": sequence,
                    "next_cursor": None,
                    "has_more": False,
                    "events": [_event(f"event-{sequence}", sequence)],
                    "quality": {"status": "READY"},
                }

        client = CountingClient()
        pages: list[dict] = []
        worker = CandidatePollWorker(client, poll_seconds=0.05)
        worker.pageReceived.connect(pages.append)

        def pump_until(predicate, timeout: float = 2.0) -> bool:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                self.app.processEvents()
                if predicate():
                    return True
                time.sleep(0.005)
            self.app.processEvents()
            return bool(predicate())

        worker.start()
        try:
            time.sleep(0.1)
            self.assertEqual(0, client.calls)
            worker.set_polling_enabled(True)
            self.assertTrue(pump_until(lambda: len(pages) >= 1))
            self.assertTrue(pages[0]["initial_sync"])

            self.assertTrue(pump_until(lambda: client.calls >= 2))
            worker.set_polling_enabled(False)
            self.assertTrue(pump_until(lambda: len(pages) >= 2))
            paused_calls = client.calls
            time.sleep(0.15)
            self.app.processEvents()
            self.assertEqual(paused_calls, client.calls)

            resumed_page_index = len(pages)
            worker.set_polling_enabled(True)
            self.assertTrue(pump_until(lambda: len(pages) > resumed_page_index))
            self.assertTrue(pages[resumed_page_index]["initial_sync"])
            self.assertTrue(pump_until(lambda: len(pages) > resumed_page_index + 1))
            self.assertFalse(pages[resumed_page_index + 1]["initial_sync"])
        finally:
            worker.requestInterruption()
            worker.wake_for_shutdown()
            worker.wait(2000)
        self.assertFalse(worker.isRunning())

    def test_dialog_polls_only_when_visible_or_alerts_are_enabled(self) -> None:
        settings = MemorySettings()
        with patch(
            "kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start"
        ) as start:
            with patch(
                "kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings",
                return_value=settings,
            ):
                dialog = CandidateMonitorDialog(FakeClient())
                self.assertFalse(dialog._worker._polling_enabled)
                start.assert_not_called()

                dialog.show()
                self.app.processEvents()
                self.assertTrue(dialog._worker._polling_enabled)
                start.assert_called_once()

                dialog.close()
                self.app.processEvents()
                self.assertFalse(dialog._worker._polling_enabled)

                dialog._alert_enabled.setChecked(True)
                self.assertTrue(dialog._worker._polling_enabled)
                dialog.hide()
                self.app.processEvents()
                self.assertTrue(dialog._worker._polling_enabled)

                dialog._alert_enabled.setChecked(False)
                self.assertFalse(dialog._worker._polling_enabled)
                dialog.stop()
                dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
