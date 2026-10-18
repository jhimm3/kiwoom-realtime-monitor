from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from kiwoom_monitor.presentation.process_control import (
    AuxiliaryProcessManager,
    build_auxiliary_command,
    JsonCommandChannel,
    JsonRequestInbox,
    process_is_alive,
    process_identity_document,
    process_identity_is_alive,
    process_identity_state,
    process_start_token,
    read_process_identity,
    stop_auxiliary_process,
    write_json_command,
)


class ProcessControlTests(unittest.TestCase):
    def test_current_process_is_alive_and_invalid_pid_is_not(self) -> None:
        self.assertTrue(process_is_alive(os.getpid()))
        self.assertFalse(process_is_alive(0))
        self.assertFalse(process_is_alive(-1))

    def test_process_identity_distinguishes_current_process_from_wrong_token(self) -> None:
        identity = process_identity_document()
        self.assertTrue(process_identity_is_alive(identity["pid"], identity["start_token"]))
        self.assertFalse(process_identity_is_alive(identity["pid"], "wrong-token"))
        self.assertEqual('running', process_identity_state(identity['pid'], identity['start_token']))
        self.assertEqual('exited', process_identity_state(identity['pid'], 'wrong-token'))

    def test_exited_child_is_distinguished_from_unverifiable_identity(self) -> None:
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1)'])
        try:
            token = process_start_token(child.pid)
            self.assertTrue(token)
            child.wait(timeout=5)
            self.assertEqual('exited', process_identity_state(child.pid, token))
            self.assertEqual('unknown', process_identity_state(child.pid, ''))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_legacy_pid_file_is_read_as_unverified_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal_process.pid"
            path.write_text(str(os.getpid()), encoding="ascii")
            self.assertEqual((os.getpid(), ""), read_process_identity(path))

    def test_json_command_replaces_target_without_leaving_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "window_command.json"
            path.write_text('{"request_id": 1}', encoding="utf-8")
            write_json_command(path, {"request_id": 7, "name": "테스트"})

            self.assertEqual(
                {"request_id": 7, "name": "테스트"},
                json.loads(path.read_text(encoding="utf-8")),
            )
            self.assertFalse(path.with_suffix(".tmp").exists())
            with patch.object(Path, "replace", side_effect=OSError("publication failed")):
                with self.assertRaisesRegex(OSError, "publication failed"):
                    write_json_command(path, {"request_id": 8})
            self.assertEqual(7, json.loads(path.read_text(encoding="utf-8"))["request_id"])

    def test_development_command_uses_module_entry_point(self) -> None:
        command = build_auxiliary_command(
            "kiwoom_monitor.news_process",
            "--news-process",
            ["--database", "news.sqlite3"],
            frozen=False,
            executable="python.exe",
            prefix="missing-environment",
        )
        self.assertEqual(
            ["python.exe", "-m", "kiwoom_monitor.news_process", "--database", "news.sqlite3"],
            command,
        )

    def test_frozen_command_uses_application_switch(self) -> None:
        command = build_auxiliary_command(
            "kiwoom_monitor.journal_process",
            "--journal-process",
            ["--parent-pid", "42"],
            frozen=True,
            executable="KiwoomMonitor.exe",
        )
        self.assertEqual(
            ["KiwoomMonitor.exe", "--journal-process", "--parent-pid", "42"],
            command,
        )

    def test_stop_process_requests_graceful_shutdown_first(self) -> None:
        process = Mock()
        process.poll.return_value = None
        shutdown = Mock()

        stop_auxiliary_process(process, request_shutdown=shutdown, graceful_timeout=3.0)

        shutdown.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3.0)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()
        self.assertEqual([call.poll(), call.wait(timeout=3.0)], process.mock_calls)

    def test_stop_process_escalates_to_terminate_and_kill(self) -> None:
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [
            subprocess.TimeoutExpired("child", 2.0),
            subprocess.TimeoutExpired("child", 1.0),
            None,
        ]

        stop_auxiliary_process(
            process,
            graceful_timeout=2.0,
            terminate_timeout=1.0,
            kill_timeout=1.0,
        )

        self.assertEqual([call(timeout=2.0), call(timeout=1.0), call(timeout=1.0)], process.wait.call_args_list)
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual([
            call.poll(), call.wait(timeout=2.0), call.terminate(),
            call.wait(timeout=1.0), call.kill(), call.wait(timeout=1.0),
        ], process.mock_calls)

    def test_command_channel_increments_and_writes_request_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news_command.json"
            channel = JsonCommandChannel(path)

            first = channel.send({"action": "show", "name": "첫 종목"})
            second = channel.send({"action": "sync"})

            self.assertEqual(1, first)
            self.assertEqual(2, second)
            self.assertEqual(2, channel.request_id)
            self.assertEqual(
                {"action": "sync", "request_id": 2},
                json.loads(path.read_text(encoding="utf-8")),
            )

    def test_time_based_channel_keeps_ids_monotonic(self) -> None:
        channel = JsonCommandChannel(None, time_based_ids=True)
        with patch("kiwoom_monitor.presentation.process_control.time.time_ns", side_effect=[100, 100, 50]):
            first = channel.advance()
            second = channel.advance()
            third = channel.advance()
        self.assertGreater(first, 0)
        self.assertEqual((100, 101, 102), (first, second, third))

    def test_request_inbox_returns_each_request_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal_news_request.json"
            inbox = JsonRequestInbox(path)
            path.write_text(
                json.dumps({"request_id": 10, "code": "005930", "name": "삼성전자"}, ensure_ascii=False),
                encoding="utf-8",
            )

            self.assertEqual("005930", inbox.read_new()["code"])
            self.assertIsNone(inbox.read_new())
            self.assertEqual(10, inbox.last_request_id)

    def test_request_inbox_ignores_invalid_or_older_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal_news_request.json"
            inbox = JsonRequestInbox(path)
            path.write_text("not-json", encoding="utf-8")
            self.assertIsNone(inbox.read_new())

            path.write_text(json.dumps({"request_id": 3}), encoding="utf-8")
            self.assertIsNotNone(inbox.read_new())
            path.write_text(json.dumps({"request_id": 2}), encoding="utf-8")
            self.assertIsNone(inbox.read_new())

    def test_process_manager_tracks_started_process(self) -> None:
        process = Mock()
        process.poll.return_value = None
        manager = AuxiliaryProcessManager()
        with patch(
            "kiwoom_monitor.presentation.process_control.launch_auxiliary_process",
            return_value=process,
        ) as launch:
            self.assertIs(process, manager.start(["python", "-m", "news"], Path("workspace")))

        launch.assert_called_once_with(["python", "-m", "news"], Path("workspace"))
        self.assertTrue(manager.is_running)
        self.assertIs(process, manager.process)

    def test_process_manager_can_request_below_normal_priority(self) -> None:
        process = Mock()
        process.poll.return_value = None
        manager = AuxiliaryProcessManager()
        with patch(
            "kiwoom_monitor.presentation.process_control.launch_auxiliary_process",
            return_value=process,
        ) as launch:
            manager.start(["research"], Path("workspace"), below_normal_priority=True)

        launch.assert_called_once_with(
            ["research"], Path("workspace"), below_normal_priority=True,
        )
        from kiwoom_monitor.presentation.process_control import launch_auxiliary_process

        with patch("kiwoom_monitor.presentation.process_control.subprocess.Popen",
                   return_value=process) as popen:
            launch_auxiliary_process(["research"], Path("workspace"), below_normal_priority=True)
        expected_flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                          | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        self.assertEqual(expected_flags, popen.call_args.kwargs["creationflags"])
        self.assertEqual("workspace", popen.call_args.kwargs["cwd"])

    def test_process_manager_clears_process_after_stop(self) -> None:
        process = Mock()
        process.poll.return_value = None
        manager = AuxiliaryProcessManager()
        with patch(
            "kiwoom_monitor.presentation.process_control.launch_auxiliary_process",
            return_value=process,
        ):
            manager.start(["child"], Path("workspace"))

        manager.stop(graceful_timeout=2.0)

        process.wait.assert_called_once_with(timeout=2.0)
        self.assertFalse(manager.is_running)
        self.assertIsNone(manager.process)


if __name__ == "__main__":
    unittest.main()
