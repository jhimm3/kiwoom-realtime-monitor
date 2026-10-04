from __future__ import annotations

import json
import itertools
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool, _set_trace
from kiwoom_monitor.central_server.diagnostic_workloads import diagnostic_run_lock, trace_status


class DiagnosticTraceTests(unittest.TestCase):
    def test_periodic_byte_limit_keeps_pending_suffix_before_new_events(self):
        with tempfile.TemporaryDirectory() as root:
            control = Path(root) / "control.json"
            committed = threading.Event()
            release = threading.Event()
            original_manifest = trace._manifest

            def hold_first_chunk(directory, session):
                original_manifest(directory, session)
                if len(session["chunks"]) == 1 and session["state"] == "running":
                    committed.set()
                    release.wait(5)

            with patch.object(trace, "control_path", return_value=control), \
                 patch("kiwoom_monitor.central_server.diagnostic_workloads.control_path",
                       return_value=control), \
                 patch.object(trace, "_manifest", side_effect=hold_first_chunk), \
                 patch.object(trace.time, "monotonic", side_effect=itertools.count(step=5)):
                parent = _set_tool(control, True, 300)
                session = parent["diagnostic_tool"]["session_id"]
                _set_trace(control, True, 120, expected_session=session)
                identifier = trace.start(seconds=60)["trace_id"]
                counts = {f"{index:02d}-" + "가" * 47: index for index in range(24)}
                event_count = 1024
                try:
                    for index in range(event_count):
                        trace.emit(identifier, "domain", {
                            "call_id": f"periodic-{index}", "domain_counts": counts,
                        })
                    self.assertTrue(committed.wait(5), "first periodic chunk was not committed")
                    active = trace.status()
                    self.assertEqual(1, len(active["chunks"]))
                    self.assertGreater(active["pending_events"], 0)
                    self.assertEqual(event_count, active["written"] + active["queued"])
                    trace.emit(identifier, "call_end", {"call_id": "arrived-during-flush"})
                finally:
                    release.set()
                    final = trace.stop()
                    _set_tool(control, False)
                self.assertEqual("complete", final["state"])
                self.assertEqual((event_count + 1, event_count + 1, 0), (
                    final["accepted"], final["written"], final["queued"],
                ))
                rows = [json.loads(line) for part in final["chunks"]
                        for line in trace.chunk_bytes(identifier, part["name"]).splitlines()]
                self.assertEqual(list(range(1, event_count + 2)), [row["seq"] for row in rows])
                self.assertEqual("arrived-during-flush", rows[-1]["call_id"])

    def test_oversized_event_fails_without_publishing_an_unreadable_chunk(self):
        with tempfile.TemporaryDirectory() as root:
            control = Path(root) / "control.json"
            with patch.object(trace, "control_path", return_value=control), \
                 patch("kiwoom_monitor.central_server.diagnostic_workloads.control_path",
                       return_value=control):
                parent = _set_tool(control, True, 300)
                session = parent["diagnostic_tool"]["session_id"]
                _set_trace(control, True, 120, expected_session=session)
                identifier = trace.start(seconds=60)["trace_id"]
                trace.emit(identifier, "call_start", {"call_id": "readable-prefix"})
                trace.emit(identifier, "domain", {"extra": "가" * 1_048_576})
                trace.emit(identifier, "call_end", {"call_id": "unwritten-suffix"})
                final = trace.stop()
                self.assertEqual("failed", final["state"])
                self.assertEqual("trace_event_too_large", final["reason"])
                self.assertEqual((3, 1, 2, 0), (
                    final["accepted"], final["written"], final["queued"], final["known_dropped"],
                ))
                rows = [json.loads(line) for part in final["chunks"]
                        for line in trace.chunk_bytes(identifier, part["name"]).splitlines()]
                self.assertEqual(["readable-prefix"], [row["call_id"] for row in rows])
                self.assertFalse(trace_status(control)["enabled"])
                with diagnostic_run_lock(control):
                    pass
                _set_tool(control, False)

    def test_wide_unicode_events_remain_downloadable_in_order_after_stop(self):
        with tempfile.TemporaryDirectory() as root:
            control = Path(root) / "control.json"
            with patch.object(trace, "control_path", return_value=control), \
                 patch("kiwoom_monitor.central_server.diagnostic_workloads.control_path",
                       return_value=control):
                parent = _set_tool(control, True, 300)
                session = parent["diagnostic_tool"]["session_id"]
                _set_trace(control, True, 120, expected_session=session)
                identifier = trace.start(seconds=60)["trace_id"]
                counts = {f"{index:02d}-" + "가" * 47: index for index in range(24)}
                event_count = 1024
                for index in range(event_count):
                    trace.emit(identifier, "domain", {
                        "call_id": f"wide-{index}", "writer_kind": "query_minute",
                        "rows": 900, "domain_counts": counts,
                    })

                final = trace.stop()
                self.assertEqual("complete", final["state"])
                self.assertEqual((event_count, event_count, 0, 0), (
                    final["accepted"], final["written"], final["known_dropped"], final["queued"],
                ))
                rows = []
                for part in final["chunks"]:
                    self.assertLessEqual(part["bytes"], 1_048_576)
                    content = trace.chunk_bytes(identifier, part["name"])
                    chunk_rows = [json.loads(line) for line in content.splitlines()]
                    self.assertEqual((part["first_seq"], part["last_seq"], part["count"]), (
                        chunk_rows[0]["seq"], chunk_rows[-1]["seq"], len(chunk_rows),
                    ))
                    rows.extend(chunk_rows)
                self.assertGreater(len(final["chunks"]), 1)
                self.assertEqual(list(range(1, event_count + 1)), [row["seq"] for row in rows])
                self.assertEqual([f"wide-{index}" for index in range(event_count)],
                                 [row["call_id"] for row in rows])
                self.assertTrue(all(row["domain_counts"] == counts for row in rows))
                _set_tool(control, False)

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
