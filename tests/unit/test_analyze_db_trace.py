from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_db_trace import summarize


class AnalyzeDbTraceTests(unittest.TestCase):
    def test_window_inventory_counts_workloads_rejections_and_prefix_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digest_a = "a" * 64
            digest_b = "b" * 64
            digest_c = "c" * 64
            rows = [
                {"seq": 1, "event_type": "collector_input", "mono_ns": 150_000_000_000,
                 "producer_component": "realtime:test", "input_kind": "message",
                 "payload_ref": digest_a},
                {"seq": 2, "event_type": "operation_start", "mono_ns": 200_000_000_000,
                 "entered_mono_ns": 200_000_000_000, "operation_id": "op-1",
                 "workload_id": "realtime", "method": "save_minute_bars",
                 "payload_ref": digest_b},
                {"seq": 3, "event_type": "operation_end", "mono_ns": 201_000_000_000,
                 "operation_id": "op-1"},
                {"seq": 4, "event_type": "input_rejected", "mono_ns": 500_000_000_000,
                 "workload_id": "news", "reason": "payload_budget_exceeded"},
                {"seq": 5, "event_type": "collector_input", "mono_ns": 720_000_000_000,
                 "producer_component": "realtime:test", "input_kind": "message",
                 "payload_ref": digest_c},
                {"seq": 6, "event_type": "operation_start", "mono_ns": 800_000_000_000,
                 "entered_mono_ns": 800_000_000_000, "operation_id": "op-2",
                 "workload_id": "top20", "method": "save_dataset_snapshot",
                 "payload_ref": digest_a},
                {"seq": 7, "event_type": "operation_end", "mono_ns": 801_000_000_000,
                 "operation_id": "op-2"},
            ]
            content = b"\n".join(json.dumps(row).encode("utf-8") for row in rows) + b"\n"
            (root / "000001.jsonl").write_bytes(content)
            manifest = {
                "trace_id": "fixture", "state": "complete", "started_at": 1000,
                "finished_at": 1040, "started_mono_ns": 100_000_000_000,
                "finished_mono_ns": 1_300_000_000_000, "accepted": len(rows),
                "written": len(rows), "last_seq": len(rows), "known_dropped": 0,
                "input_rejected": 1, "input_capture_censored": False, "bytes_written": len(content),
                "chunks": [{"name": "000001.jsonl", "sha256": hashlib.sha256(content).hexdigest(),
                            "count": len(rows), "first_seq": 1, "last_seq": len(rows)}],
                "blobs": {digest_a: {"bytes": 100}, digest_b: {"bytes": 200},
                          digest_c: {"bytes": 300}},
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            result = summarize(root, window_seconds=600)

        inventory = result["window_inventory"]
        self.assertEqual(2, len(inventory["windows"]))
        first, second = inventory["windows"]
        self.assertEqual({"realtime": 1}, first["operations_by_workload"])
        self.assertEqual({"save_minute_bars": 1}, first["operation_starts_by_writer"])
        self.assertEqual(1, first["operation_ends_for_starts"])
        self.assertEqual(300, first["combined_unique_payload_bytes_declared"])
        self.assertEqual(1, first["collector_prefix_by_component"]["realtime:test"]["prefix_input_events"])
        self.assertEqual(100, first["collector_prefix_payload_bytes_declared"])
        self.assertEqual([{"workload": "news", "reason": "payload_budget_exceeded", "count": 1}],
                         first["rejections_by_workload_and_reason"])
        self.assertEqual({"top20": 1}, second["operations_by_workload"])
        self.assertEqual(400, second["collector_prefix_payload_bytes_declared"])
        self.assertEqual(400, second["combined_unique_payload_bytes_declared"])
        self.assertFalse(second["replay_ready"])
        self.assertFalse(result["capture_integrity_ok"])
        self.assertIn("zero count", inventory["scope_note"])

    def test_window_inventory_is_opt_in_and_rejects_unbounded_width(self):
        with self.assertRaisesRegex(ValueError, "window_seconds_out_of_bounds"):
            summarize(Path("unused"), window_seconds=3901)


if __name__ == "__main__":
    unittest.main()
