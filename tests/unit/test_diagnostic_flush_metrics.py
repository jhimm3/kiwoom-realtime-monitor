from __future__ import annotations

import asyncio
import unittest
from time import time
from unittest.mock import patch

from kiwoom_monitor.central_server.diagnostic_metrics import (
    CURRENT_FLUSH_ID, _commit_diagnostic_record, summarize_writer_flushes,
)


class DiagnosticFlushMetricsTests(unittest.TestCase):
    def test_commit_trace_keeps_backend_waits_relative_to_same_commit(self) -> None:
        trace = _commit_diagnostic_record(
            [{"at": 12.125, "state": "active", "wait_type": "IO",
              "wait_event": "WalSync", "blocking_pids": []}],
            [], 4321, 12.0, 200, True, "",
        )
        self.assertEqual(4321, trace["backend_pid"])
        self.assertTrue(trace["wal_io_timing_enabled_for_commit"])
        self.assertEqual(125, trace["samples"][0]["offset_ms"])
        self.assertEqual("WalSync", trace["samples"][0]["wait_event"])
        self.assertAlmostEqual(12.2, trace["commit_window"]["ended_at"])
        self.assertEqual([], trace["probe_errors"])

    def test_commit_trace_records_missing_wal_timing_without_failing_storage(self) -> None:
        trace = _commit_diagnostic_record(None, ["InsufficientPrivilege"], 0, None, 0, False,
                                          "InsufficientPrivilege")
        self.assertIsNone(trace["backend_pid"])
        self.assertFalse(trace["wal_io_timing_enabled_for_commit"])
        self.assertEqual("InsufficientPrivilege", trace["wal_io_timing_error"])
        self.assertEqual(["InsufficientPrivilege"], trace["probe_errors"])

    def test_commit_trace_discards_wait_samples_outside_the_commit_window(self) -> None:
        trace = _commit_diagnostic_record(
            [{"at": 12.1, "wait_type": "IO", "wait_event": "WalSync"},
             {"at": 12.4, "wait_type": "IO", "wait_event": "DataFileRead"}],
            [], 4321, 12.0, 200, True, "", ended_at=12.2,
            probe_incomplete=True,
        )
        self.assertEqual(["WalSync"], [sample["wait_event"] for sample in trace["samples"]])
        self.assertTrue(trace["probe_pending_at_capture"])
        self.assertEqual(12.2, trace["commit_window"]["ended_at"])

    def test_bar_summary_counts_execution_windows_separately_from_row_operations(self) -> None:
        from kiwoom_monitor.central_server import diagnostic_metrics as metrics

        metrics.clear_metrics()
        try:
            with patch.object(metrics, "_capture_is_enabled", return_value=True), \
                    patch.object(metrics, "refresh_capture_state", return_value={"enabled": True}), \
                    patch.object(metrics, "_CAPTURE_ENABLED", True):
                metrics.record_market_bar_save(
                    kind="minute", rows=3, observations=0,
                    connect_ms=1, bars_ms=200, metadata_ms=0, revisions_ms=0,
                    commit_ms=2, close_ms=0, total_ms=203,
                    bar_statement_diagnostics=[{
                        "method": "executemany", "sql_operations": 3,
                        "probe_pending_at_capture": True, "probe_errors": [],
                        "samples": [{"wait_type": "IO", "wait_event": "DataFileRead"}],
                    }],
                )
                metrics.record_market_bar_save(
                    kind="minute", rows=1, observations=0,
                    connect_ms=1, bars_ms=20, metadata_ms=0, revisions_ms=0,
                    commit_ms=2, close_ms=0, total_ms=23,
                    bar_statement_diagnostics=[{
                        "method": "execute", "sql_operations": 1,
                        "sampling_status": "below_initial_delay",
                        "probe_pending_at_capture": False, "probe_errors": [],
                        "samples": [],
                    }],
                )
                summary = metrics.summarize_market_bar_saves(0, time() + 1)
            group = summary["kinds"]["minute"]["bar_statement_diagnostics"]
            self.assertEqual(2, group["execution_windows"])
            self.assertEqual(4, group["sql_operations"])
            self.assertEqual(1, group["windows_with_incomplete_probe"])
            self.assertEqual(1, group["windows_below_initial_delay"])
            self.assertEqual({"IO:DataFileRead": 1}, group["wait_event_samples"])
        finally:
            metrics.clear_metrics()

    def test_successful_commits_are_grouped_by_cycle_without_inventing_zero_cycles(self) -> None:
        values = [
            {"flush_id": "one", "commits": 1},
            {"flush_id": "one", "commits": 1},
            {"flush_id": "two", "commits": 1},
            {"flush_id": "", "commits": 1},
        ]
        summary = summarize_writer_flushes(values)
        self.assertEqual(2, summary["observed_cycles_with_successful_writes"])
        self.assertEqual(1, summary["successful_commits_per_cycle"]["min"])
        self.assertEqual(2, summary["successful_commits_per_cycle"]["max"])
        self.assertIn("failed/zero-write cycles are absent", summary["scope_note"])

    def test_flush_id_crosses_to_thread_and_is_restored(self) -> None:
        async def check() -> str:
            token = CURRENT_FLUSH_ID.set("cycle-1")
            try:
                return await asyncio.to_thread(CURRENT_FLUSH_ID.get)
            finally:
                CURRENT_FLUSH_ID.reset(token)

        self.assertEqual("cycle-1", asyncio.run(check()))
        self.assertEqual("", CURRENT_FLUSH_ID.get())


if __name__ == "__main__":
    unittest.main()
