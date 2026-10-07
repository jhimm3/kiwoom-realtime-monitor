from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
