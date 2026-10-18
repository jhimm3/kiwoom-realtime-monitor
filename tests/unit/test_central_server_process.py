from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kiwoom_monitor.central_server import __main__ as central_server_main
from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings
from kiwoom_monitor.infrastructure.central_server_process import LocalCentralServerProcess


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
            with patch.object(manager, "_is_ready", side_effect=[False, True]):
                self.assertTrue(manager.start())
        command = popen.call_args.args[0]
        self.assertEqual(["python-test", "-m", "kiwoom_monitor.central_server", "--parent-pid", str(os.getpid())], command)
        options = popen.call_args.kwargs
        environment = options["env"]
        self.assertEqual("sqlite:///" + (Path(directory) / "monitor.sqlite3").resolve().as_posix(), environment["KIWOOM_SERVER_DATABASE_URL"])
        self.assertEqual(("secret", "127.0.0.1", "8787", "real", "app", "secret"), tuple(environment[key] for key in ("MONITOR_SERVER_ACCESS_TOKEN", "KIWOOM_SERVER_HOST", "KIWOOM_SERVER_PORT", "KIWOOM_ENVIRONMENT", "KIWOOM_APP_KEY", "KIWOOM_SECRET_KEY")))
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
            with patch.object(manager, "_is_ready", side_effect=[False, True]):
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


if __name__ == "__main__":
    unittest.main()
