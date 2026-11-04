from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
from kiwoom_monitor.presentation.news_window_coordinator import NewsWindowCoordinator


class NewsWindowCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.coordinator = NewsWindowCoordinator(
            root / "news.env", root / "news.sqlite3",
            lambda: QRect(10, 20, 800, 600), lambda _message: None, self.app,
        )

    def test_command_uses_main_outer_frame_and_preserves_account_scope(self) -> None:
        documents: list[dict[str, object]] = []
        self.coordinator._channel = SimpleNamespace(send=documents.append)
        scope = AccountScope("kiwoom", AccountEnvironment.REAL, "11111111-1111-4111-8111-111111111111")
        self.coordinator.send_command("005930", "삼성전자", origin_scope=scope, account_scope=scope)
        self.assertEqual([10, 20, 800, 600], documents[0]["main_geometry"])
        self.assertEqual(scope.to_dict(), documents[0]["origin_scope"])
        self.assertEqual(scope.to_dict(), documents[0]["account_scope"])

    def test_partial_scope_rejected_before_command(self) -> None:
        send = Mock()
        self.coordinator._channel = SimpleNamespace(send=send)
        scope = AccountScope("kiwoom", AccountEnvironment.REAL, "11111111-1111-4111-8111-111111111111")
        self.coordinator.send_command("005930", "삼성전자", origin_scope=scope)
        send.assert_not_called()

    def test_restore_is_not_overwritten_by_activation_sync(self) -> None:
        send = Mock()
        self.coordinator._process = SimpleNamespace(is_running=True)
        self.coordinator.send_command = send
        with patch.object(self.coordinator, "window_mode", return_value="linked"):
            self.coordinator.on_window_state_change(False)
            self.coordinator.on_activation()
            send.assert_called_once_with(action="restore")
            self.coordinator._finish_restore_sync()
        self.assertEqual("sync", send.call_args.kwargs["action"])
        self.assertFalse(send.call_args.kwargs["activate"])

    def test_stop_requests_child_shutdown(self) -> None:
        requests: list[str] = []
        def stop(**kwargs: object) -> None:
            kwargs["request_shutdown"]()
        self.coordinator._process = SimpleNamespace(process=object(), stop=stop)
        self.coordinator.send_command = lambda **kwargs: requests.append(kwargs["action"])
        self.coordinator.stop()
        self.assertEqual(["shutdown"], requests)

    def test_inactive_geometry_never_reads_native_settings(self) -> None:
        for running in (False, True):
            self.coordinator._process = SimpleNamespace(is_running=running)
            with self.subTest(running=running), patch.object(self.coordinator, "window_mode", return_value="docked_right") as mode:
                self.coordinator.schedule_geometry_sync()
                self.assertEqual(running, self.coordinator._dock_timer.isActive())
                self.assertEqual(int(running), mode.call_count)
                self.coordinator._dock_timer.stop()

    def test_unavailable_query_news_geometry_never_constructs_qsettings(self) -> None:
        coordinator = NewsWindowCoordinator(None, None, lambda: QRect(), lambda _message: None, self.app)
        with patch("kiwoom_monitor.presentation.news_window_coordinator.QSettings", side_effect=AssertionError("inactive Native settings")):
            coordinator.schedule_geometry_sync()
            coordinator.sync_window()
            coordinator.on_window_state_change(False)
            coordinator.on_activation()
        self.assertFalse(coordinator.available)
        self.assertFalse(coordinator._dock_timer.isActive())


if __name__ == "__main__":
    unittest.main()
