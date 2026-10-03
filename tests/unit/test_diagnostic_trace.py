from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool, _set_trace
from kiwoom_monitor.central_server.diagnostic_workloads import diagnostic_run_lock, trace_status


class DiagnosticTraceTests(unittest.TestCase):
    def test_65_minute_budget_flushes_bounded_burst_without_loss(self):
        """Check the capture-duration envelope and queued writer under a burst."""
        with tempfile.TemporaryDirectory() as root:
            control = Path(root) / "control.json"
            with patch.object(trace, "control_path", return_value=control), \
                 patch("kiwoom_monitor.central_server.diagnostic_workloads.control_path",
                       return_value=control):
                parent = _set_tool(control, True, 4000)
                master_session = parent["diagnostic_tool"]["session_id"]
                _set_trace(control, True, 3900, expected_session=master_session)
                active = trace.start(seconds=3900)
                identifier = active["trace_id"]
                event_count = 20_000
                for index in range(event_count):
                    trace.emit(identifier, "call_end", {
                        "call_id": f"burst-{index}", "outcome": "committed",
                        "rows_attempted": index % 901,
                    })

                final = trace.stop()
                self.assertEqual("complete", final["state"])
                self.assertEqual(event_count, final["accepted"])
                self.assertEqual(event_count, final["written"])
                self.assertEqual(0, final["known_dropped"])
                self.assertGreater(final["bytes_written"], event_count * 50)
                self.assertGreaterEqual(final["queue_high_water"], event_count)
                self.assertEqual(sum(part["count"] for part in final["chunks"]), event_count)
                for part in final["chunks"]:
                    self.assertEqual(part["bytes"], len(trace.chunk_bytes(identifier, part["name"])))
                _set_tool(control, False)

    def test_start_end_and_master_off_preserve_complete_chunk(self):
        with tempfile.TemporaryDirectory() as root:
            control = Path(root) / "control.json"
            with patch.object(trace, "control_path", return_value=control), \
                 patch("kiwoom_monitor.central_server.diagnostic_workloads.control_path",
                       return_value=control):
                parent = _set_tool(control, True, 300)
                master_session = parent["diagnostic_tool"]["session_id"]
                _set_trace(control, True, 120, expected_session=master_session)
                active = trace.start(seconds=60)
                identifier = active["trace_id"]
                trace.emit(identifier, "call_start", {"call_id": "one"})
                trace.emit(identifier, "call_end", {"call_id": "one", "outcome": "committed"})
                with self.assertRaisesRegex(RuntimeError, "diagnostic_run_busy"):
                    with diagnostic_run_lock(control):
                        pass
                final = trace.stop()
                self.assertEqual("complete", final["state"])
                self.assertEqual(2, final["written"])
                self.assertEqual(0, final["known_dropped"])
                self.assertFalse(trace_status(control)["enabled"])
                chunk = control.parent / "diagnostic-traces" / identifier / final["chunks"][0]["name"]
                self.assertEqual(["call_start", "call_end"],
                                 [json.loads(line)["event_type"] for line in chunk.read_text().splitlines()])
                self.assertEqual([1, 2],
                                 [json.loads(line)["seq"] for line in chunk.read_text().splitlines()])
                self.assertEqual(chunk.read_bytes(),
                                 trace.chunk_bytes(identifier, final["chunks"][0]["name"]))
                with self.assertRaises(KeyError):
                    trace.chunk_bytes(identifier, "../manifest.json")
                chunk.write_bytes(b"corrupted")
                with self.assertRaisesRegex(ValueError, "checksum_mismatch"):
                    trace.chunk_bytes(identifier, final["chunks"][0]["name"])
                _set_tool(control, False)
                self.assertIsNone(trace.token())

    def test_overflow_is_incomplete_and_new_trace_cannot_bypass_run_lock(self):
        with tempfile.TemporaryDirectory() as root:
            control = Path(root) / "control.json"
            with patch.object(trace, "control_path", return_value=control), \
                 patch("kiwoom_monitor.central_server.diagnostic_workloads.control_path", return_value=control):
                parent = _set_tool(control, True, 300)
                session = parent["diagnostic_tool"]["session_id"]
                _set_trace(control, True, 120, expected_session=session)
                with diagnostic_run_lock(control):
                    with self.assertRaisesRegex(ValueError, "diagnostic_run_busy"):
                        trace.start(seconds=60)
                with patch.object(trace, "_CAPACITY", 2):
                    identifier = trace.start(seconds=60)["trace_id"]
                    for index in range(3):
                        trace.emit(identifier, "call_start", {"call_id": str(index)})
                    final = trace.stop()
                self.assertEqual("incomplete", final["state"])
                self.assertEqual((3, 2, 2, 1), (final["last_seq"], final["accepted"],
                                               final["written"], final["known_dropped"]))
                with patch.object(trace, "_MAX_SESSIONS", 1):
                    _set_trace(control, True, 120, expected_session=session)
                    with self.assertRaisesRegex(ValueError, "storage_quota"):
                        trace.start(seconds=60)

    def test_restart_marks_prior_manifest_interrupted_without_rearming(self):
        with tempfile.TemporaryDirectory() as root:
            control = Path(root) / "control.json"
            identifier = "20261002T034553Z-48afad5c80ef"
            directory = Path(root) / "diagnostic-traces" / identifier
            directory.mkdir(parents=True)
            (directory / "manifest.json").write_text(json.dumps({
                "trace_id": identifier, "instance_id": "previous-process",
                "state": "running", "chunks": [], "started_at": 1,
            }), encoding="utf-8")
            with patch.object(trace, "control_path", return_value=control):
                self.assertEqual(1, trace.recover_interrupted())
                saved = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual("interrupted", saved["state"])
                self.assertTrue(saved["unknown_tail_loss"])
                self.assertIsNone(trace.token())
if __name__ == "__main__":
    unittest.main()
