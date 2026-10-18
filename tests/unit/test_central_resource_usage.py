from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
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
                with self.subTest(v1=v1, limit=raw_limit), patch.object(Path, "read_text", read), patch.object(resources.os, "sysconf", return_value=4096, create=True):
                    measured = resource_usage(Path.cwd(), 12345)
                self.assertEqual((12288, 2048, expected), tuple(measured[key] for key in ("process_memory_bytes", "container_memory_bytes", "container_memory_limit_bytes")))
