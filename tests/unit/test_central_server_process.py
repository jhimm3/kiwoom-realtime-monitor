from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import sqlite3
import time
import json
import shutil
from contextlib import asynccontextmanager, closing
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from kiwoom_monitor.central_server import __main__ as central_server_main
from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
from kiwoom_monitor.infrastructure.central_server_process import LocalCentralServerProcess
from kiwoom_monitor.infrastructure.owned_server_shutdown import WindowsShutdownEvent


class LocalCentralServerProcessTests(unittest.TestCase):
    def _manager(self, directory: str, *, frozen: bool = False) -> LocalCentralServerProcess:
        return LocalCentralServerProcess(
            DataSourceSettings("local_server", "http://127.0.0.1:8787", "secret"),
            Path(directory) / "api.env",
            Path(directory) / "monitor.sqlite3",
            executable="python-test",
            frozen=frozen,
        )

    def test_rejects_non_local_address(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "이 PC"):
                LocalCentralServerProcess(
                    DataSourceSettings("local_server", "https://nas.example.test", "secret"),
                    Path(directory) / "api.env",
                    Path(directory) / "monitor.sqlite3",
                )

    @patch("kiwoom_monitor.infrastructure.central_server_process.LocalApiConfig")
    @patch("kiwoom_monitor.infrastructure.central_server_process.subprocess.Popen")
    def test_development_uses_module_entry_point(self, popen: MagicMock, config: MagicMock) -> None:
        profiles = config.return_value.load_profiles.return_value
        profiles.active_environment = "real"
        profiles.real_app_key = "app"
        profiles.real_secret_key = "secret"
        process = popen.return_value
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory)
            with (patch.object(manager, "_is_ready", side_effect=[False, True]),
                  patch("kiwoom_monitor.infrastructure.central_server_process.WindowsShutdownEvent.create") as event):
                event.return_value.name = "owned-test-event"
                self.assertTrue(manager.start())
        command = popen.call_args.args[0]
        expected = ["python-test", "-m", "kiwoom_monitor.central_server", "--parent-pid", str(os.getpid()),
                    "--local-mock-automation"]
        if os.name == "nt":
            expected += ["--shutdown-event", "owned-test-event"]
        self.assertEqual(expected, command)
        options = popen.call_args.kwargs
        environment = options["env"]
        self.assertEqual("sqlite:///" + (Path(directory) / "monitor.sqlite3").resolve().as_posix(), environment["KIWOOM_SERVER_DATABASE_URL"])
        self.assertEqual(("secret", "127.0.0.1", "8787", "real", "app", "secret"), tuple(environment[key] for key in ("MONITOR_SERVER_ACCESS_TOKEN", "KIWOOM_SERVER_HOST", "KIWOOM_SERVER_PORT", "KIWOOM_ENVIRONMENT", "KIWOOM_APP_KEY", "KIWOOM_SECRET_KEY")))
        self.assertEqual("0", environment["MOCK_ORDER_TRANSPORT_ENABLED"])
        self.assertEqual((subprocess.DEVNULL,) * 3, tuple(options[key] for key in ("stdin", "stdout", "stderr")))

    @patch("kiwoom_monitor.infrastructure.central_server_process.LocalApiConfig")
    @patch("kiwoom_monitor.infrastructure.central_server_process.subprocess.Popen")
    def test_packaged_app_uses_bootstrap_switch(self, popen: MagicMock, config: MagicMock) -> None:
        profiles = config.return_value.load_profiles.return_value
        profiles.active_environment = "real"
        profiles.real_app_key = "app"
        profiles.real_secret_key = "secret"
        process = popen.return_value
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory, frozen=True)
            with (patch.object(manager, "_is_ready", side_effect=[False, True]),
                  patch("kiwoom_monitor.infrastructure.central_server_process.WindowsShutdownEvent.create") as event):
                event.return_value.name = "owned-test-event"
                self.assertTrue(manager.start())
        command = popen.call_args.args[0]
        self.assertEqual(["python-test", "--central-server"], command[:2])

    @patch("kiwoom_monitor.infrastructure.central_server_process.subprocess.Popen")
    def test_reuses_running_server(self, popen: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory)
            with patch.object(manager, "_is_ready", return_value=True):
                self.assertFalse(manager.start())
        popen.assert_not_called()

    def test_windowed_server_supplies_missing_standard_streams(self) -> None:
        with (
            patch.object(central_server_main.sys, "stdout", None),
            patch.object(central_server_main.sys, "stderr", None),
        ):
            central_server_main._ensure_standard_streams()
            self.assertIsNotNone(central_server_main.sys.stdout)
            self.assertIsNotNone(central_server_main.sys.stderr)
            central_server_main.sys.stdout.close()
            central_server_main.sys.stderr.close()

    def test_server_parent_watch_recognizes_the_current_live_process(self) -> None:
        self.assertTrue(central_server_main._process_is_alive(os.getpid()))
        self.assertFalse(central_server_main._process_is_alive(0))
        self.assertFalse(central_server_main._process_is_alive(-1))

    def test_server_parent_watch_recognizes_an_exited_process(self) -> None:
        # Windows permits 259 as an exit code; it still denotes a dead process.
        exit_code = 259 if os.name == "nt" else 0
        process = subprocess.Popen([
            sys.executable, "-I", "-c", f"raise SystemExit({exit_code})",
        ])
        try:
            self.assertEqual(exit_code, process.wait(timeout=5))
            self.assertFalse(central_server_main._process_is_alive(process.pid))
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


    @patch("kiwoom_monitor.infrastructure.central_server_process.LocalApiConfig")
    @patch("kiwoom_monitor.infrastructure.central_server_process.subprocess.Popen")
    def test_start_readiness_failure_drains_child_without_losing_original_error(self, popen, config):
        profiles = config.return_value.load_profiles.return_value
        profiles.active_environment = "real"
        profiles.real_app_key, profiles.real_secret_key = "app", "secret"
        process = popen.return_value
        process.poll.side_effect = [None, None, 0]
        process.wait.return_value = 0
        failure = OSError("readiness failed")
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory)
            with (patch.object(manager, "_is_ready", side_effect=[False, failure]),
                  patch("kiwoom_monitor.infrastructure.central_server_process.WindowsShutdownEvent.create") as event):
                event.return_value.name = "owned-test-event"
                with self.assertRaises(OSError) as raised:
                    manager.start()
                self.assertIs(raised.exception, failure)
                process.wait.assert_called_once_with(timeout=5)
                if os.name == "nt":
                    event.return_value.close.assert_called_once_with()
                self.assertIsNone(manager.process)
                self.assertIsNone(manager._shutdown_event)

    def test_stop_requests_graceful_exit_and_releases_only_after_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory)
            process, event = MagicMock(), MagicMock()
            manager._process, manager._shutdown_event = process, event
            process.poll.side_effect = [None, 0]
            process.wait.return_value = 0
            event.signal.side_effect = lambda: self.assertIs(manager.process, process)
            event.close.side_effect = lambda: self.assertIs(manager.process, process)
            manager.stop()
            process.wait.assert_called_once_with(timeout=5)
            process.kill.assert_not_called()
            if os.name == "nt":
                event.signal.assert_called_once_with()
                process.terminate.assert_not_called()
            else:
                process.terminate.assert_called_once_with()
            event.close.assert_called_once_with()
            self.assertIsNone(manager.process)
            self.assertIsNone(manager._shutdown_event)

    def test_stop_timeout_is_forced_failure_with_original_five_plus_two_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory)
            process, event = MagicMock(), MagicMock()
            manager._process, manager._shutdown_event = process, event
            process.poll.side_effect = [None, 1]
            process.wait.side_effect = [subprocess.TimeoutExpired("server", 5), 1]
            with self.assertRaisesRegex(RuntimeError, "강제 종료"):
                manager.stop()
            self.assertEqual([5, 2], [call.kwargs["timeout"] for call in process.wait.call_args_list])
            process.kill.assert_called_once_with()
            event.close.assert_called_once_with()
            self.assertIsNone(manager.process)

    def test_stop_interrupted_or_failed_wait_keeps_owner_when_forced_wait_also_fails(self):
        for failure in (KeyboardInterrupt(), OSError("wait failed")):
            with self.subTest(error=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                manager = self._manager(directory)
                process, event = MagicMock(), MagicMock()
                manager._process, manager._shutdown_event = process, event
                process.poll.return_value = None
                process.wait.side_effect = [failure, subprocess.TimeoutExpired("server", 2)]
                with self.assertRaises(type(failure)):
                    manager.stop()
                self.assertIs(manager.process, process)
                self.assertIs(manager._shutdown_event, event)
                event.close.assert_not_called()
                process.kill.assert_called_once_with()
                self.assertEqual([5, 2], [call.kwargs["timeout"] for call in process.wait.call_args_list])
                with patch.object(manager, "_is_ready", return_value=False), patch("kiwoom_monitor.infrastructure.central_server_process.subprocess.Popen") as popen:
                    with self.assertRaisesRegex(RuntimeError, "종료가 아직"):
                        manager.start()
                    popen.assert_not_called()

    def test_stop_preserves_first_interrupt_after_bounded_forced_terminal_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory)
            process, event = MagicMock(), MagicMock()
            manager._process, manager._shutdown_event = process, event
            failure = KeyboardInterrupt()
            process.poll.side_effect = [None, 1]
            process.wait.side_effect = [failure, 1]
            with self.assertRaises(KeyboardInterrupt) as raised:
                manager.stop()
            self.assertIs(raised.exception, failure)
            process.kill.assert_called_once_with()
            self.assertEqual([5, 2], [call.kwargs["timeout"] for call in process.wait.call_args_list])
            event.close.assert_called_once_with()
            self.assertIsNone(manager.process)

    def test_stop_nonzero_terminal_is_not_durable_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory)
            process, event = MagicMock(), MagicMock()
            manager._process, manager._shutdown_event = process, event
            process.poll.return_value = 3
            with self.assertRaisesRegex(RuntimeError, "exit=3"):
                manager.stop()
            process.wait.assert_not_called()
            event.close.assert_called_once_with()
            self.assertIsNone(manager.process)

    def test_stop_signal_failure_still_owns_bounded_forced_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory)
            process, event = MagicMock(), MagicMock()
            manager._process, manager._shutdown_event = process, event
            process.poll.side_effect = [None, 1]
            event.signal.side_effect = OSError("signal failed")
            process.terminate.side_effect = OSError("signal failed")
            process.wait.return_value = 1
            with self.assertRaisesRegex(RuntimeError, "정상 종료를 요청하지"):
                manager.stop()
            process.kill.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=2)
            event.close.assert_called_once_with()

    def test_stop_reused_external_server_never_creates_or_signals_control(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(directory)
            with patch.object(manager, "_is_ready", return_value=True), patch("kiwoom_monitor.infrastructure.central_server_process.WindowsShutdownEvent.create") as event:
                self.assertFalse(manager.start())
                manager.stop()
                event.assert_not_called()

    def test_child_watch_control_error_requests_exit_and_reports_failure(self):
        server, event, errors = SimpleNamespace(should_exit=False), MagicMock(), []
        failure = OSError("native event wait failed")
        event.wait.side_effect = failure
        with patch.object(central_server_main, "_process_is_alive", return_value=True):
            central_server_main._stop_with_parent(server, os.getpid(), event, errors=errors)
        self.assertTrue(server.should_exit)
        self.assertEqual([failure], errors)

    def test_actual_owned_child_graceful_exit_commits_sqlite_before_exit_zero(self):
        self._actual_owned_child_probe(fail_shutdown=False)

    def test_actual_owned_child_lifespan_failure_is_nonzero_after_sqlite_commit(self):
        self._actual_owned_child_probe(fail_shutdown=True)

    def _actual_owned_child_probe(self, *, fail_shutdown):
        temporary_parent = Path(tempfile.gettempdir()).resolve(strict=True)
        directory = tempfile.mkdtemp(prefix="owned-central-shutdown-", dir=temporary_parent)
        root = Path(directory).resolve(strict=True)
        if root.parent != temporary_parent or Path(directory).is_symlink():
            raise RuntimeError("native probe root is outside its owned worker TEMP")
        manager = self._manager(directory)
        event = None
        process = None
        success = False
        first_failure = None
        try:
            try:
                event = WindowsShutdownEvent.create() if os.name == "nt" else None
                name = event.name if event is not None else ""
                code = "from tests.unit.test_central_server_process import _owned_server_probe; import sys; _owned_server_probe(sys.argv[1],sys.argv[2],sys.argv[3]=='1')"
                with (root / "child.log").open("wb") as child_log:
                    process = subprocess.Popen([sys.executable, "-c", code, str(root), name, str(int(fail_shutdown))], stdin=subprocess.DEVNULL, stdout=child_log, stderr=subprocess.STDOUT)
                manager._process, manager._shutdown_event = process, event
                deadline = time.monotonic() + 5
                while not (root / "ready").is_file() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((root / "ready").is_file(), "real Uvicorn startup/socket binding did not complete")
                ready = json.loads((root / "ready").read_text(encoding="utf-8"))
                self.assertEqual({"started": True, "startup_event_set": True, "startup_failed": False}, {key: ready[key] for key in ("started", "startup_event_set", "startup_failed")})
                self.assertGreater(ready["bound_sockets"], 0)
                if fail_shutdown:
                    with self.assertRaisesRegex(RuntimeError, "exit="):
                        manager.stop()
                    self.assertEqual(1, process.returncode)
                else:
                    manager.stop()
                    self.assertEqual(0, process.returncode)
                with closing(sqlite3.connect(root / "probe.sqlite3")) as db:
                    self.assertEqual([("durable-final-ack",)], db.execute("SELECT value FROM native_ack").fetchall())
                post = json.loads((root / "post-main.json").read_text(encoding="utf-8"))
                self.assertEqual({"started": True, "force_exit": False, "startup_event_set": True, "startup_failed": False, "shutdown_event_set": True, "shutdown_failed": fail_shutdown, "error_occurred": fail_shutdown}, post["uvicorn"])
                self.assertEqual("failed" if fail_shutdown else "completed", post["app_shutdown_state"])
                self.assertEqual(process.returncode, post["expected_process_exit"])
                self.assertIsNone(post["system_exit_code"])
                if fail_shutdown:
                    fault = json.loads((root / "shutdown-fault.json").read_text(encoding="utf-8"))
                    self.assertEqual({"injected": "lifespan_shutdown_after_native_commit", "native_commit_completed": True}, fault)
                    self.assertEqual({"type": "RuntimeError", "message": "owned server durable shutdown acknowledgement is unconfirmed"}, post["main_exception"])
                else:
                    self.assertFalse((root / "shutdown-fault.json").exists())
                    self.assertIsNone(post["main_exception"])
                self.assertIsNone(manager.process)
                self.assertIsNone(manager._shutdown_event)
                if name:
                    try:
                        unexpected = WindowsShutdownEvent.open(name)
                    except OSError:
                        pass
                    else:
                        unexpected.close()
                        self.fail("owned event remains open after parent/child terminal cleanup")
                success = True
            except BaseException as failure:
                first_failure = failure
                failure.add_note("owned native child evidence retained under worker TEMP: " + str(root))
                raise
        finally:
            cleanup_succeeded = False
            try:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
                if process is not None and process.poll() is None:
                    raise RuntimeError("native probe child terminal state is unconfirmed")
                if getattr(manager, "_shutdown_event", None) is not None:
                    manager._shutdown_event.close()
                    manager._shutdown_event = None
                    manager._process = None
                elif process is None and event is not None:
                    event.close()
                cleanup_succeeded = True
            except BaseException as cleanup_error:
                cleanup_error.add_note("owned native child cleanup evidence retained under worker TEMP: " + str(root))
                if first_failure is None:
                    raise
                first_failure.add_note("native cleanup also failed: " + type(cleanup_error).__name__ + ": " + str(cleanup_error))
            finally:
                if success and cleanup_succeeded:
                    resolved = Path(directory).resolve(strict=True)
                    if (resolved != root or resolved.parent != temporary_parent
                            or Path(directory).is_symlink()
                            or not resolved.name.startswith("owned-central-shutdown-")):
                        raise RuntimeError("native probe cleanup ownership changed")
                    shutil.rmtree(resolved)

def _owned_server_probe(directory, event_name, fail_shutdown):
    from fastapi import FastAPI
    import uvicorn
    root = Path(directory)
    servers = []

    def write_record(filename, value):
        temporary = root / (filename + ".tmp")
        temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        temporary.replace(root / filename)

    actual_startup = uvicorn.Server.startup

    async def record_actual_startup(server, *args, **kwargs):
        servers.append(server)
        await actual_startup(server, *args, **kwargs)
        if server.started:
            write_record("ready", {
                "started": server.started,
                "startup_event_set": server.lifespan.startup_event.is_set(),
                "startup_failed": server.lifespan.startup_failed,
                "bound_sockets": sum(len(listener.sockets or ()) for listener in server.servers),
            })

    @asynccontextmanager
    async def lifespan(application):
        application.state.shutdown_status = {"state": "running"}
        with closing(sqlite3.connect(root / "probe.sqlite3")) as db:
            db.execute("CREATE TABLE native_ack(value TEXT)")
            db.commit()
        try:
            yield
        finally:
            with closing(sqlite3.connect(root / "probe.sqlite3")) as db:
                db.execute("INSERT INTO native_ack VALUES(?)", ("durable-final-ack",))
                db.commit()
            if fail_shutdown:
                application.state.shutdown_status = {"state": "failed"}
                write_record("shutdown-fault.json", {"injected": "lifespan_shutdown_after_native_commit", "native_commit_completed": True})
                raise RuntimeError("actual lifespan shutdown failure after commit")
            application.state.shutdown_status = {"state": "completed"}

    application = FastAPI(lifespan=lifespan)
    settings = SimpleNamespace(database_url="sqlite:///" + (root / "probe.sqlite3").as_posix(), host="127.0.0.1", port=0)
    arguments = ["owned-server-probe", "--parent-pid", str(os.getppid())]
    if event_name:
        arguments += ["--shutdown-event", event_name]
    main_exception = None
    expected_process_exit = 0
    system_exit_code = None
    try:
        with (patch.object(central_server_main.sys, "argv", arguments),
              patch.object(central_server_main.CentralServerSettings, "from_environment", return_value=settings),
              patch.object(central_server_main, "configure_server_logging"),
              patch.object(central_server_main, "create_app", return_value=application),
              patch.object(uvicorn.Server, "startup", record_actual_startup)):
            central_server_main.main()
    except BaseException as error:
        main_exception = {"type": type(error).__name__, "message": str(error)}
        system_exit_code = error.code if isinstance(error, SystemExit) else None
        expected_process_exit = (error.code if isinstance(error, SystemExit) and isinstance(error.code, int) else 1)
        raise
    finally:
        server = servers[-1] if servers else None
        lifespan_state = getattr(server, "lifespan", None)
        write_record("post-main.json", {
            "main_exception": main_exception,
            "expected_process_exit": expected_process_exit,
            "system_exit_code": system_exit_code,
            "app_shutdown_state": getattr(application.state, "shutdown_status", {}).get("state"),
            "uvicorn": None if server is None or lifespan_state is None else {
                "started": server.started, "force_exit": server.force_exit,
                "startup_event_set": lifespan_state.startup_event.is_set(),
                "startup_failed": lifespan_state.startup_failed,
                "shutdown_event_set": lifespan_state.shutdown_event.is_set(),
                "shutdown_failed": lifespan_state.shutdown_failed,
                "error_occurred": lifespan_state.error_occurred,
            },
        })


if __name__ == "__main__":
    unittest.main()
