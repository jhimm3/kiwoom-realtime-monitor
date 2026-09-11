from __future__ import annotations

import threading
import tempfile
import time
import unittest
from pathlib import Path

from kiwoom_monitor.infrastructure.central_theme_sync import CentralThemeSyncDispatcher


class _Service:
    def __init__(self) -> None:
        self.calls = 0
        self.called = threading.Event()
        self.remote_completed_at = None
        self.applied = 0

    def load_theme_snapshot(self):
        return {"theme_profile": [], "theme_stock": [], "theme_metadata": []}, self.remote_completed_at

    def apply_theme_snapshot(self, _path: Path, _snapshot) -> None:
        self.applied += 1
        self.called.set()

    def replace_themes(self, _path: Path) -> None:
        self.calls += 1
        self.called.set()


class _FailOnceService(_Service):
    def replace_themes(self, _path: Path) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary outage")
        self.called.set()


class _FailService(_Service):
    def replace_themes(self, _path: Path) -> None:
        self.calls += 1
        self.called.set()
        raise RuntimeError("offline")


class CentralThemeSyncDispatcherTests(unittest.TestCase):
    def test_consecutive_changes_are_debounced_off_the_calling_thread(self) -> None:
        service = _Service()
        dispatcher = CentralThemeSyncDispatcher(service, Path("monitor.sqlite3"), debounce_seconds=0.05)  # type: ignore[arg-type]

        dispatcher.notify()
        dispatcher.notify()

        self.assertTrue(service.called.wait(1.0))
        self.assertEqual(1, service.calls)
        dispatcher.close()

    def test_failed_upload_is_retried_and_clears_durable_pending_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / ".theme-pending"
            service = _FailOnceService()
            dispatcher = CentralThemeSyncDispatcher(
                service, Path(directory) / "monitor.sqlite3",
                debounce_seconds=0.05, retry_delays=(0.05,), pending_path=marker,
            )  # type: ignore[arg-type]

            dispatcher.notify()

            self.assertTrue(service.called.wait(1.0))
            self.assertEqual(2, service.calls)
            deadline = time.monotonic() + 1.0
            while marker.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(marker.exists())
            dispatcher.close()

    def test_pending_marker_survives_close_and_flushes_on_next_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / ".theme-pending"
            failed = _FailService()
            first = CentralThemeSyncDispatcher(
                failed, Path(directory) / "monitor.sqlite3",
                debounce_seconds=0.05, retry_delays=(1.0,), pending_path=marker,
            )  # type: ignore[arg-type]
            first.notify()
            self.assertTrue(failed.called.wait(1.0))
            first.close()
            self.assertTrue(marker.exists())

            recovered = _Service()
            second = CentralThemeSyncDispatcher(
                recovered, Path(directory) / "monitor.sqlite3", pending_path=marker,
            )  # type: ignore[arg-type]
            self.assertTrue(second.flush_pending())
            self.assertEqual(1, recovered.calls)
            self.assertFalse(marker.exists())
            second.close()

    def test_newer_nas_theme_is_applied_instead_of_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / ".theme-pending"
            marker.write_text("100.0", encoding="utf-8")
            service = _Service()
            service.remote_completed_at = 101.0
            dispatcher = CentralThemeSyncDispatcher(
                service, Path(directory) / "monitor.sqlite3", pending_path=marker,
            )  # type: ignore[arg-type]

            self.assertTrue(dispatcher.flush_pending())
            self.assertEqual(0, service.calls)
            self.assertEqual(1, service.applied)
            self.assertFalse(marker.exists())
            dispatcher.close()

    def test_newer_local_theme_is_uploaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / ".theme-pending"
            marker.write_text("101.0", encoding="utf-8")
            service = _Service()
            service.remote_completed_at = 100.0
            dispatcher = CentralThemeSyncDispatcher(
                service, Path(directory) / "monitor.sqlite3", pending_path=marker,
            )  # type: ignore[arg-type]

            self.assertTrue(dispatcher.flush_pending())
            self.assertEqual(1, service.calls)
            self.assertEqual(0, service.applied)
            self.assertFalse(marker.exists())
            dispatcher.close()

    def test_legacy_nanosecond_pending_marker_is_compared_as_seconds(self) -> None:
        self.assertEqual(100.0, CentralThemeSyncDispatcher._pending_time("100000000000"))


if __name__ == "__main__":
    unittest.main()
