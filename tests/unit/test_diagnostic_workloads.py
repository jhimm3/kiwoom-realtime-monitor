from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.diagnostic_metrics import (
    record_market_bar_save, record_writer_transaction, refresh_capture_state,
    summarize_market_bar_saves,
)
from kiwoom_monitor.central_server.diagnostic_workloads import (
    capture_status, diagnostic_tool_status, instance_id, paused_workloads,
)


class DiagnosticWorkloadTests(unittest.TestCase):
    def _set_capture(self, path: Path, expires_at: float | None) -> None:
        value = {"schema": 1, "instance_id": instance_id(), "leases": {}, "owners": {},
                 "diagnostic_tool": {"expires_at": time.time() + 180, "owner": "test"}}
        if expires_at is not None:
            value["capture"] = {"expires_at": expires_at, "owner": "test"}
        path.write_text(json.dumps(value), encoding="utf-8")
        refresh_capture_state(force=True)

    def test_master_defaults_off_and_masks_all_children(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controls.json"
            now = time.time()
            path.write_text(json.dumps({"schema": 1, "instance_id": instance_id(),
                "leases": {"news_jobs": now + 60},
                "capture": {"expires_at": now + 60, "owner": "manual"}}), encoding="utf-8")
            self.assertFalse(diagnostic_tool_status(path, now=now)["enabled"])
            self.assertEqual(frozenset(), paused_workloads(path=path, now=now))
            self.assertFalse(capture_status(path, now=now)["enabled"])

    def test_pause_expires_and_restarting_container_discards_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controls.json"
            now = time.time()
            path.write_text(json.dumps({"schema": 1, "instance_id": instance_id(),
                "diagnostic_tool": {"expires_at": now + 30},
                "paused": ["news_jobs", "unknown"], "expires_at": now + 30}), encoding="utf-8")
            self.assertEqual(frozenset({"news_jobs"}), paused_workloads(path=path, now=now))
            self.assertEqual(frozenset(), paused_workloads(path=path, now=now + 31))
            with patch("kiwoom_monitor.central_server.diagnostic_workloads.instance_id", return_value="new-container"):
                self.assertEqual(frozenset(), paused_workloads(path=path, now=now))

    def test_minute_metadata_gate_expires_with_diagnostic_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controls.json"
            now = time.time()
            path.write_text(json.dumps({
                "schema": 1, "instance_id": instance_id(),
                "diagnostic_tool": {"expires_at": now + 10},
                "leases": {"minute_query_metadata": now + 60},
            }), encoding="utf-8")
            self.assertIn("minute_query_metadata", paused_workloads(path=path, now=now))
            self.assertNotIn("minute_query_metadata", paused_workloads(path=path, now=now + 11))

    def test_all_saves_including_fast_ones_are_summarized(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                "os.environ", {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH":
                               str(Path(directory) / "controls.json")}):
            path = Path(directory) / "controls.json"
            self._set_capture(path, time.time() + 60)
            start = time.time() - 1
            for commit_ms, revision_rows_ms, insert_ms in (
                    (12, 7, 4), (38, 9, 0), (1400, 800, 760)):
                record_market_bar_save(kind="minute", rows=11, observations=11,
                    connect_ms=12, bars_ms=4, metadata_ms=3,
                    revisions_ms=10 + revision_rows_ms,
                    commit_ms=commit_ms, close_ms=0, total_ms=commit_ms + 35,
                    revision_lookup_statements=11,
                    revision_lookup_keys=11,
                    revision_insert_statements=1,
                    revision_insert_rows=4,
                    revision_sources_ms=2, revision_locks_ms=3,
                    revision_lookup_ms=5, revision_rows_ms=revision_rows_ms,
                    revision_insert_execute_ms=insert_ms)
            record_writer_transaction("realtime_minute", 2, 17, connect_ms=3,
                                      execute_ms=10, commit_ms=4)
            result = summarize_market_bar_saves(start, time.time() + 1)
            self.assertEqual(3, result["kinds"]["minute"]["count"])
            self.assertEqual(33, result["kinds"]["minute"]["revision_lookup_statements"])
            self.assertEqual(33, result["kinds"]["minute"]["revision_lookup_keys"])
            self.assertEqual(3, result["kinds"]["minute"]["revision_insert_statements"])
            self.assertEqual(12, result["kinds"]["minute"]["revision_insert_rows"])
            self.assertEqual(2, result["kinds"]["minute"]["revision_sources_ms"]["median"])
            self.assertEqual(3, result["kinds"]["minute"]["revision_locks_ms"]["median"])
            self.assertEqual(5, result["kinds"]["minute"]["revision_lookup_ms"]["median"])
            self.assertEqual(9, result["kinds"]["minute"]["revision_rows_ms"]["median"])
            self.assertEqual(4, result["kinds"]["minute"]["revision_insert_execute_ms"]["median"])
            self.assertEqual(38, result["kinds"]["minute"]["commit_ms"]["median"])
            self.assertEqual(1400, result["kinds"]["minute"]["commit_ms"]["p95"])
            calls = result["kinds"]["minute"]["call_samples"]
            self.assertEqual(3, len(calls))
            self.assertEqual((800, 760, 1400), (
                calls[2]["revision_rows_ms"],
                calls[2]["revision_insert_execute_ms"],
                calls[2]["commit_ms"],
            ))
            self.assertEqual(11, calls[2]["revision_lookup_keys"])
            self.assertEqual(1, result["writer_transactions"]["realtime_minute"]["count"])
            writer = result["writer_transactions"]["realtime_minute"]
            self.assertEqual((1, 1, 2),
                             (writer["calls"], writer["transactions"], writer["rows_attempted"]))
            self.assertEqual(4, writer["commit_ms"]["median"])
            self.assertEqual(10, writer["execute_ms"]["p99"])
            self.assertEqual(1, writer["commit_latency_samples"])
            self.assertIsNone(writer["payload_bytes_estimated"])
            self._set_capture(path, None)

    def test_writer_commit_metrics_stay_unavailable_when_not_instrumented(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                "os.environ", {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH":
                               str(Path(directory) / "controls.json")}):
            path = Path(directory) / "controls.json"
            self._set_capture(path, time.time() + 60)
            start = time.time() - 1
            record_writer_transaction("realtime_latest", 3, 21)
            writer = summarize_market_bar_saves(start, time.time() + 1)["writer_transactions"]["realtime_latest"]
            self.assertEqual(1, writer["commits"])
            self.assertEqual(0, writer["commit_latency_samples"])
            self.assertIsNone(writer["commit_ms"]["median"])
            self._set_capture(path, None)

    def test_capture_defaults_off_and_off_clears_in_memory_samples(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
                "os.environ", {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH":
                               str(Path(directory) / "controls.json")}):
            path = Path(directory) / "controls.json"
            self._set_capture(path, None)
            start = time.time() - 1
            record_writer_transaction("query_minute", 1, 20)
            self.assertFalse(capture_status(path)["enabled"])
            self.assertEqual(0, summarize_market_bar_saves(start, time.time() + 1)
                             ["writer_transactions"].get("query_minute", {}).get("count", 0))

            self._set_capture(path, time.time() + 60)
            record_writer_transaction("query_minute", 1, 20)
            self.assertTrue(capture_status(path)["enabled"])
            self._set_capture(path, None)
            result = summarize_market_bar_saves(start, time.time() + 1)
            self.assertEqual({}, result["writer_transactions"])

    def test_capture_lease_expires_without_a_runtime_setting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controls.json"
            now = time.time()
            path.write_text(json.dumps({"schema": 1, "instance_id": instance_id(),
                "diagnostic_tool": {"expires_at": now + 60, "owner": "manual"},
                "capture": {"expires_at": now + 30, "owner": "manual"}}),
                encoding="utf-8")
            self.assertTrue(capture_status(path, now=now)["enabled"])
            self.assertFalse(capture_status(path, now=now + 31)["enabled"])

    def test_each_workload_lease_expires_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controls.json"
            now = time.time()
            path.write_text(json.dumps({"schema": 1, "instance_id": instance_id(),
                "diagnostic_tool": {"expires_at": now + 60, "owner": "manual"},
                "leases": {"minute_backfill": now + 10, "news_jobs": now + 30}}), encoding="utf-8")
            self.assertEqual(frozenset({"minute_backfill", "news_jobs"}), paused_workloads(path=path, now=now))
            self.assertEqual(frozenset({"news_jobs"}), paused_workloads(path=path, now=now + 11))

    def test_parent_switch_gates_every_child_and_master_off_restores_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "controls.json"
            now = time.time()
            path.write_text(json.dumps({"schema": 1, "instance_id": instance_id(),
                "diagnostic_tool": {"expires_at": now + 10, "owner": "manual"},
                "leases": {"news_jobs": now + 50}, "capture": {
                    "expires_at": now + 50, "owner": "manual"}}), encoding="utf-8")
            self.assertTrue(diagnostic_tool_status(path, now=now)["enabled"])
            self.assertEqual(frozenset({"news_jobs"}), paused_workloads(path=path, now=now))
            self.assertFalse(capture_status(path, now=now + 11)["enabled"])
            self.assertEqual(frozenset(), paused_workloads(path=path, now=now + 11))
