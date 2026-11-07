from __future__ import annotations

import json
import ctypes
import errno
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from types import SimpleNamespace
from unittest.mock import Mock, call, patch
from kiwoom_monitor.presentation import process_control as control

from kiwoom_monitor.presentation.process_control import (
    AuxiliaryProcessManager,
    build_auxiliary_command,
    JsonCommandChannel,
    JsonRequestInbox,
    launch_auxiliary_process,
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

    def test_darwin_native_identity_preserves_microseconds_and_detects_pid_reuse(self) -> None:
        raw = bytearray(136)
        struct.pack_into('=I', raw, 12, 42)
        struct.pack_into('=QQ', raw, 120, 1_793_678_400, 123_456)

        def query(pid, flavor, argument, buffer, size):
            self.assertEqual((42, 3, 0, 136), (pid, flavor, argument, size))
            ctypes.memmove(buffer, bytes(raw), len(raw))
            return len(raw)

        native = Mock(side_effect=query)
        control._darwin_process_api.cache_clear()
        try:
            with patch.object(control.sys, 'platform', 'darwin'), patch.object(
                    control.ctypes, 'CDLL', return_value=SimpleNamespace(proc_pidinfo=native)) as load:
                self.assertEqual('darwin:1793678400:123456', process_start_token(42))
                self.assertEqual('running', process_identity_state(42, 'darwin:1793678400:123456'))
                self.assertTrue(process_identity_is_alive(42, 'darwin:1793678400:123456'))
                struct.pack_into('=Q', raw, 128, 123_457)
                self.assertEqual('exited', process_identity_state(42, 'darwin:1793678400:123456'))
                self.assertFalse(process_identity_is_alive(42, 'darwin:1793678400:123456'))
            load.assert_called_once_with('/usr/lib/libproc.dylib', use_errno=True)
            self.assertEqual(ctypes.c_int, native.restype)
            self.assertEqual([ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int], native.argtypes)
        finally:
            control._darwin_process_api.cache_clear()

    def test_darwin_denied_incomplete_and_absent_identity_remain_distinct(self) -> None:
        def query(*_arguments):
            ctypes.set_errno(native_error[0])
            return returned[0]

        control._darwin_process_api.cache_clear()
        try:
            with patch.object(control.sys, 'platform', 'darwin'), patch.object(
                    control.ctypes, 'CDLL', return_value=SimpleNamespace(proc_pidinfo=Mock(side_effect=query))):
                for count, error, expected in ((0, errno.EPERM, 'unknown'), (135, 0, 'unknown'),
                                               (135, errno.ESRCH, 'unknown'),
                                               (0, errno.ESRCH, 'exited')):
                    returned, native_error = [count], [error]
                    with self.subTest(returned=count, errno=error):
                        self.assertEqual('', process_start_token(42))
                        self.assertEqual(expected, process_identity_state(42, 'darwin:1793678400:123456'))
                        self.assertFalse(process_identity_is_alive(42, 'darwin:1793678400:123456'))
        finally:
            control._darwin_process_api.cache_clear()

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

    def _start_owned_child(self, manager: AuxiliaryProcessManager) -> subprocess.Popen[bytes]:
        process = manager.start(
            [sys.executable, "-c", "import time; time.sleep(30)"], Path.cwd(),
        )

        def cleanup() -> None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

        self.addCleanup(cleanup)
        return process

    def test_default_kill_waits_for_owned_child_exit_before_releasing_handle(self) -> None:
        manager = AuxiliaryProcessManager()
        process = self._start_owned_child(manager)
        token = process_start_token(process.pid)
        self.assertTrue(token)

        # On Windows terminate and kill both use TerminateProcess. Keep this
        # real child alive through terminate to exercise the final native kill.
        with patch.object(process, "terminate"), patch.object(process, "wait", wraps=process.wait) as wait:
            manager.stop(graceful_timeout=0, terminate_timeout=0)

        self.assertEqual([call(timeout=0), call(timeout=0), call(timeout=None)], wait.call_args_list)
        self.assertIsNotNone(process.returncode)
        self.assertEqual("exited", process_identity_state(process.pid, token))
        self.assertIsNone(manager.process)

    def test_explicit_kill_timeout_retains_owned_child_until_retry_then_allows_restart(self) -> None:
        manager = AuxiliaryProcessManager()
        process = self._start_owned_child(manager)
        token = process_start_token(process.pid)
        self.assertTrue(token)

        # A failed native kill request must not be mistaken for child exit.
        with patch.object(process, "terminate"), patch.object(process, "kill"):
            with self.assertRaises(subprocess.TimeoutExpired):
                manager.stop(graceful_timeout=0, terminate_timeout=0, kill_timeout=0)

        self.assertIs(process, manager.process)
        self.assertTrue(manager.is_running)
        self.assertEqual("running", process_identity_state(process.pid, token))

        manager.stop(graceful_timeout=0, terminate_timeout=1, kill_timeout=1)
        self.assertIsNotNone(process.returncode)
        self.assertEqual("exited", process_identity_state(process.pid, token))
        self.assertIsNone(manager.process)

        restarted = self._start_owned_child(manager)
        self.assertIsNot(process, restarted)
        self.assertIs(restarted, manager.process)
        manager.stop(graceful_timeout=0, terminate_timeout=1, kill_timeout=1)
        self.assertIsNotNone(restarted.returncode)
        self.assertIsNone(manager.process)

    def test_shutdown_request_failure_retains_owned_child_for_retry(self) -> None:
        manager = AuxiliaryProcessManager()
        process = self._start_owned_child(manager)

        with self.assertRaisesRegex(OSError, "cancel publication failed"):
            manager.stop(request_shutdown=Mock(side_effect=OSError("cancel publication failed")))

        self.assertIs(process, manager.process)
        self.assertTrue(manager.is_running)
        manager.stop(graceful_timeout=0, terminate_timeout=1, kill_timeout=1)
        self.assertIsNotNone(process.returncode)
        self.assertIsNone(manager.process)

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

    def test_launch_process_preserves_default_environment_inheritance(self) -> None:
        process = Mock()
        for below_normal_priority in (False, True):
            for environment_options in ({}, {"env": None}):
                with self.subTest(priority=below_normal_priority, options=environment_options):
                    with patch("kiwoom_monitor.presentation.process_control.subprocess.Popen",
                               return_value=process) as popen:
                        result = launch_auxiliary_process(
                            ("child", "--request", "request.json"), Path("workspace"),
                            below_normal_priority=below_normal_priority,
                            **environment_options,
                        )

                    expected_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if below_normal_priority:
                        expected_flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
                    popen.assert_called_once_with(
                        ["child", "--request", "request.json"],
                        cwd="workspace", creationflags=expected_flags,
                    )
                    self.assertIs(process, result)

    def test_process_manager_passes_explicit_environment_without_mutating_it(self) -> None:
        process = Mock()
        process.poll.return_value = None
        for environment in ({"PATH": "child-runtime", "CHILD_INPUT": "request"}, {}):
            for below_normal_priority in (False, True):
                with self.subTest(environment=environment, priority=below_normal_priority):
                    supplied = MappingProxyType(environment)
                    original = dict(supplied)
                    manager = AuxiliaryProcessManager()
                    with patch("kiwoom_monitor.presentation.process_control.subprocess.Popen",
                               return_value=process) as popen:
                        result = manager.start(
                            ("child", "--request", "request.json"), Path("workspace"),
                            below_normal_priority=below_normal_priority, env=supplied,
                        )

                    expected_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if below_normal_priority:
                        expected_flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
                    popen.assert_called_once_with(
                        ["child", "--request", "request.json"],
                        cwd="workspace", creationflags=expected_flags, env=supplied,
                    )
                    self.assertIs(supplied, popen.call_args.kwargs["env"])
                    self.assertEqual(original, environment)
                    self.assertIs(process, result)
                    self.assertIs(process, manager.process)
                    self.assertTrue(manager.is_running)

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
