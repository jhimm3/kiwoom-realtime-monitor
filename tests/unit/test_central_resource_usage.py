from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from kiwoom_monitor.central_server import resource_usage as resources

from kiwoom_monitor.central_server.resource_usage import resource_usage


class CentralResourceUsageTests(unittest.TestCase):
    def test_reports_memory_disk_database_and_uptime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = resource_usage(Path(directory), 12345)
        self.assertEqual(12345, result["database_size_bytes"])
        self.assertGreater(result["data_disk_total_bytes"], 0)
        self.assertGreaterEqual(result["data_disk_free_bytes"], 0)
        self.assertGreaterEqual(result["uptime_seconds"], 0)
        self.assertIn("process_memory_bytes", result)
        if sys.platform == "win32":
            self.assertGreater(result["process_memory_bytes"], 0)
        self.assertIn("container_memory_bytes", result)
        for current, raw_limit, expected in (("2048", "4096", 4096), ("2048", "max", None), ("2048", str(1 << 61), None)):
            for v1 in (False, True):
                def read(path, **_kwargs):
                    name = path.as_posix()
                    if name.endswith("/statm"):
                        return "100 3 0 0"
                    if v1 and name.endswith(("memory.current", "memory.max")):
                        raise FileNotFoundError(name)
                    return current if name.endswith(("memory.current", "memory.usage_in_bytes")) else raw_limit
                with (
                    self.subTest(v1=v1, limit=raw_limit),
                    patch.object(resources, "platform", "linux"),
                    patch.object(Path, "read_text", read),
                    patch.object(resources.os, "sysconf", return_value=4096, create=True),
                ):
                    measured = resource_usage(Path.cwd(), 12345)
                self.assertEqual((12288, 2048, expected), tuple(measured[key] for key in ("process_memory_bytes", "container_memory_bytes", "container_memory_limit_bytes")))

    def test_windows_rss_failure_preserves_other_diagnostics(self) -> None:
        class ProcessReadError(Exception):
            pass

        def unavailable():
            raise ProcessReadError("fixture process RSS unavailable")

        psutil = SimpleNamespace(
            Error=ProcessReadError, Process=lambda: SimpleNamespace(memory_info=unavailable),
        )
        for library in (None, psutil):
            with (
                self.subTest(library_available=library is not None),
                patch.object(resources, "platform", "win32"),
                patch.dict(sys.modules, {"psutil": library}),
            ):
                result = resource_usage(Path.cwd(), 12345)
            self.assertIsNone(result["process_memory_bytes"])
            self.assertGreater(result["data_disk_total_bytes"], 0)
            self.assertEqual(12345, result["database_size_bytes"])

    def test_darwin_reuses_current_resident_measurement_and_preserves_unknown(self) -> None:
        for measured in (123_456_789, OSError('native measurement unavailable')):
            options = {'side_effect': measured} if isinstance(measured, OSError) else {'return_value': measured}
            with self.subTest(measured=type(measured).__name__), patch.object(resources, 'platform', 'darwin'), patch(
                    'kiwoom_monitor.application.research_resources.current_rss_bytes', **options) as rss:
                result = resource_usage(Path.cwd(), 12345)
            self.assertEqual(None if isinstance(measured, OSError) else measured, result['process_memory_bytes'])
            rss.assert_called_once_with()
            self.assertGreater(result['data_disk_total_bytes'], 0)
            self.assertEqual(12345, result['database_size_bytes'])
