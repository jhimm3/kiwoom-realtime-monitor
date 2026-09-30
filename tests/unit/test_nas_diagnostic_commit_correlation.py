from __future__ import annotations

import importlib
import sys
import types
import unittest
from threading import Event
from time import monotonic, time
from unittest.mock import patch


class NasDiagnosticCommitCorrelationTests(unittest.TestCase):
    def test_commit_activity_excludes_adjacent_statements_and_reused_backend_pids(self) -> None:
        from kiwoom_monitor.central_server.diagnostic_sampling import _correlate_db_commit_activity

        call = {"call_id": "slow", "writer_family": "realtime.minute",
                "writer_kind": "realtime_minute", "access_mode": "write",
                "started_at": 9.0, "commit_started_at": 10.0,
                "commit_finished_at": 12.0, "commit_ms": 2000, "backend_pid": 42}
        sample = {"pid": 42, "backend_started_at": 8.0, "started_at": 11.0,
                  "finished_at": 11.1, "statement_type": "COMMIT",
                  "wait_type": "IO", "wait_event": "WalSync", "blocking_pids": []}
        raw = {"calls": [call, dict(call, call_id="missed", started_at=19.0,
                                    commit_started_at=20.0, commit_finished_at=22.0)]}
        summary = _correlate_db_commit_activity(raw, [
            sample, dict(sample, pid=43), dict(sample, backend_started_at=10.5),
            dict(sample, started_at=9.9), dict(sample, finished_at=12.1),
            dict(sample, statement_type="OTHER", wait_event="DataFileRead"),
        ])
        group = summary["groups"]["realtime.minute/realtime_minute"]
        self.assertEqual((2, 1, 1), (group["slow_commit_attempts"],
                                    group["sampled_attempts"], group["unsampled_attempts"]))
        self.assertEqual({"IO:WalSync": 1}, group["wait_samples"])
        self.assertEqual("no_sample", raw["commit_activity"][1]["sampling_status"])
        self.assertIn("does not exclude", summary["scope_note"])
        self.assertEqual("incomplete", _correlate_db_commit_activity(
            raw, [sample], dropped_samples=1)["state"])

    @staticmethod
    def _diagnostic_module():
        if "fcntl" not in sys.modules:
            try:
                importlib.import_module("fcntl")
            except ImportError:
                with patch.dict(sys.modules, {"fcntl": types.ModuleType("fcntl")}):
                    return importlib.import_module("scripts.nas_workload_diagnostic")
        return importlib.import_module("scripts.nas_workload_diagnostic")

    def test_bar_upsert_wait_probe_is_capture_gated(self) -> None:
        from kiwoom_monitor.central_server.database import _execute_with_postgres_wait_probe

        with patch("kiwoom_monitor.central_server.database._sample_postgres_commit_waits") as probe:
            result, diagnostic = _execute_with_postgres_wait_probe(
                lambda: "saved", "postgresql://unused", 123, False,
            )
        self.assertEqual("saved", result)
        self.assertIsNone(diagnostic)
        probe.assert_not_called()

    def test_bar_upsert_wait_probe_attaches_phase_window_and_backend_waits(self) -> None:
        from kiwoom_monitor.central_server.database import _execute_with_postgres_wait_probe

        sampled = Event()
        sql_started = Event()
        def sample(_url, pid, stop, samples, _errors):
            self.assertEqual(456, pid)
            self.assertTrue(sql_started.wait(1))
            samples.append({"at": time(), "state": "active", "wait_type": "IO",
                            "wait_event": "DataFileRead", "blocking_pids": []})
            sampled.set()

        with patch("kiwoom_monitor.central_server.database._sample_postgres_commit_waits", sample):
            result, diagnostic = _execute_with_postgres_wait_probe(
                lambda: (sql_started.set(), sampled.wait(1), "upserted")[2],
                "postgresql://unused", 456, True,
            )
        self.assertEqual("upserted", result)
        self.assertIsNotNone(diagnostic)
        self.assertEqual(456, diagnostic["backend_pid"])
        self.assertLessEqual(diagnostic["started_at"], diagnostic["ended_at"])
        self.assertEqual("DataFileRead", diagnostic["samples"][0]["wait_event"])
        self.assertEqual("sampled", diagnostic["sampling_status"])

    def test_probe_cleanup_does_not_extend_sql_duration(self) -> None:
        from kiwoom_monitor.central_server.database import _execute_with_postgres_wait_probe

        entered = Event()
        release = Event()

        def blocked_probe(_url, _pid, _stop, _samples, _errors):
            entered.set()
            release.wait(1)

        try:
            with patch("kiwoom_monitor.central_server.database._sample_postgres_commit_waits", blocked_probe):
                started = monotonic()
                result, diagnostic = _execute_with_postgres_wait_probe(
                    lambda: (entered.wait(1), "saved")[1], "postgresql://unused", 456, True,
                )
                elapsed_ms = (monotonic() - started) * 1000
            self.assertEqual("saved", result)
            self.assertTrue(diagnostic["probe_pending_at_capture"])
            self.assertEqual("below_initial_delay", diagnostic["sampling_status"])
            self.assertLess(elapsed_ms, 300)
        finally:
            release.set()

    def test_probe_start_failure_cannot_cancel_bar_upsert(self) -> None:
        from kiwoom_monitor.central_server.database import _execute_with_postgres_wait_probe

        with patch("kiwoom_monitor.central_server.database.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("thread unavailable")
            result, diagnostic = _execute_with_postgres_wait_probe(
                lambda: "saved", "postgresql://unused", 456, True,
            )
        self.assertEqual("saved", result)
        self.assertEqual(["RuntimeError"], diagnostic["probe_errors"])
        self.assertEqual([], diagnostic["samples"])
        self.assertEqual("probe_error", diagnostic["sampling_status"])

    def test_executemany_is_one_window_for_multiple_row_operations(self) -> None:
        from kiwoom_monitor.central_server.database import _PostgresObservedCursor

        class Cursor:
            def executemany(self, _sql, _rows):
                return None

        records: list[dict[str, object]] = []
        with patch("kiwoom_monitor.central_server.database.Thread") as thread:
            thread.return_value.is_alive.return_value = False
            observed = _PostgresObservedCursor(Cursor(), "postgresql://unused", 456, True, records)
            observed.executemany("INSERT", [(1,), (2,), (3,)])
        self.assertEqual(1, len(records))
        self.assertEqual("executemany", records[0]["method"])
        self.assertEqual(3, records[0]["sql_operations"])

    def test_device_counters_are_aligned_to_each_commit_window(self) -> None:
        if "fcntl" not in sys.modules:
            try:
                importlib.import_module("fcntl")
            except ImportError:
                with patch.dict(sys.modules, {"fcntl": types.ModuleType("fcntl")}):
                    diagnostic = importlib.import_module("scripts.nas_workload_diagnostic")
            else:
                diagnostic = importlib.import_module("scripts.nas_workload_diagnostic")
        else:
            diagnostic = importlib.import_module("scripts.nas_workload_diagnostic")

        before = [0] * 17
        after = [0] * 17
        after[4], after[6], after[7], after[9], after[10] = 10, 500, 200, 1000, 1500
        after[15], after[16] = 5, 100
        result = diagnostic._correlate_commit_device_samples(
            {"kinds": {"minute": {"call_samples": [{
                "commit_diagnostics": {"commit_window": {
                    "started_at": 10.0, "ended_at": 11.0, "duration_ms": 1000,
                }},
            }]}}},
            [
                {"at": 9.75, "stats": {"dm-4": before}},
                {"at": 11.25, "stats": {"dm-4": after}},
            ],
        )

        window = result["kinds"]["minute"]["call_samples"][0]["commit_diagnostics"]["storage_device_window"]
        self.assertTrue(window["available"])
        self.assertEqual(500, window["boundary_padding_ms"])
        self.assertEqual(1500, window["observed_interval_ms"])
        self.assertEqual(20.0, window["host_device_delta"]["dm-4"]["write_await_ms"])
        self.assertIn("overlapping writes", window["scope_note"])

    def test_device_counters_can_be_aligned_to_bar_statement_windows(self) -> None:
        if "fcntl" not in sys.modules:
            try:
                importlib.import_module("fcntl")
            except ImportError:
                with patch.dict(sys.modules, {"fcntl": types.ModuleType("fcntl")}):
                    diagnostic = importlib.import_module("scripts.nas_workload_diagnostic")
            else:
                diagnostic = importlib.import_module("scripts.nas_workload_diagnostic")
        else:
            diagnostic = importlib.import_module("scripts.nas_workload_diagnostic")

        before = [0] * 17
        after = [0] * 17
        after[4], after[6], after[7], after[9], after[10] = 4, 80, 40, 500, 900
        result = diagnostic._correlate_commit_device_samples(
            {"kinds": {"minute": {"call_samples": [{
                "bar_statement_diagnostics": [{"started_at": 20.0, "ended_at": 21.0}],
            }]}}},
            [{"at": 19.75, "stats": {"dm-4": before}},
             {"at": 21.25, "stats": {"dm-4": after}}],
        )
        statement = result["kinds"]["minute"]["call_samples"][0]["bar_statement_diagnostics"][0]
        self.assertTrue(statement["storage_device_window"]["available"])
        self.assertEqual(500, statement["storage_device_window"]["boundary_padding_ms"])
        self.assertEqual(1500, statement["storage_device_window"]["observed_interval_ms"])
        self.assertEqual(10.0, statement["storage_device_window"]["host_device_delta"]["dm-4"]["write_await_ms"])

    def test_wal_timing_report_includes_transaction_local_writer_setting(self) -> None:
        if "fcntl" not in sys.modules:
            try:
                importlib.import_module("fcntl")
            except ImportError:
                with patch.dict(sys.modules, {"fcntl": types.ModuleType("fcntl")}):
                    diagnostic = importlib.import_module("scripts.nas_workload_diagnostic")
            else:
                diagnostic = importlib.import_module("scripts.nas_workload_diagnostic")
        else:
            diagnostic = importlib.import_module("scripts.nas_workload_diagnostic")

        report = diagnostic._wal_timing_report(
            {"enabled": False, "setting_scope": "measurement_connection"},
            {"wal_write_time_ms": 1.7, "wal_sync_time_ms": 8924.9},
            {"kinds": {"minute": {"call_samples": [
                {"commit_diagnostics": {"wal_io_timing_enabled_for_commit": True}},
                {"commit_diagnostics": {"wal_io_timing_enabled_for_commit": True}},
            ]}}},
        )
        self.assertFalse(report["enabled"])
        self.assertTrue(report["time_values_available"])
        self.assertEqual(2, report["transaction_local_timing_calls"])
        self.assertTrue(report["cluster_timing_delta_nonzero"])
        self.assertEqual(8924.9, report["cluster_wal_time_delta_ms"]["wal_sync_time_ms"])

    def test_pg_stat_io_delta_is_grouped_and_reports_counter_changes(self) -> None:
        diagnostic = self._diagnostic_module()
        before = {
            "available": True,
            "rows": {
                ("client backend", "relation", "normal"): {
                    "reads": 10, "read_time_ms": 1.2, "writes": 20,
                    "write_time_ms": 3.4, "writebacks": 2,
                    "writeback_time_ms": 0.2, "extends": 1,
                    "extend_time_ms": 0.1, "fsyncs": 0, "fsync_time_ms": 0,
                    "hits": 100, "evictions": 5, "reuses": 2,
                    "stats_reset": "same",
                },
            },
        }
        after = {
            "available": True,
            "rows": {
                ("client backend", "relation", "normal"): {
                    "reads": 12, "read_time_ms": 2.7, "writes": 25,
                    "write_time_ms": 8.4, "writebacks": 3,
                    "writeback_time_ms": 0.8, "extends": 2,
                    "extend_time_ms": 0.4, "fsyncs": 1, "fsync_time_ms": 0.5,
                    "hits": 110, "evictions": 7, "reuses": 3,
                    "stats_reset": "same",
                },
            },
        }
        delta = diagnostic._counter_delta(before, after)
        group = delta["groups"]["client backend|relation|normal"]["counters"]
        self.assertEqual(5, group["writes"])
        self.assertEqual(5.0, group["write_time_ms"])
        self.assertEqual(1, group["fsyncs"])
        self.assertFalse(delta["groups"]["client backend|relation|normal"]["stats_reset_changed"])
        self.assertIn("not per-query attribution", delta["note"])

    def test_pg_stat_io_snapshot_uses_backend_context_dimensions(self) -> None:
        diagnostic = self._diagnostic_module()

        class Cursor:
            query = ""

            def execute(self, query):
                self.query = query

            def fetchall(self):
                return [("client backend", "relation", "normal", 4, 1.5, 7, 3.5,
                         2, 0.5, 1, 0.25, 1, 0.75, 10, 3, 2, "reset-at")]

        cursor = Cursor()
        snapshot = diagnostic._pg_stat_io_snapshot(cursor)
        self.assertIn("FROM pg_stat_io", cursor.query)
        values = snapshot["rows"][("client backend", "relation", "normal")]
        self.assertEqual(7.0, values["writes"])
        self.assertEqual(3.5, values["write_time_ms"])
        self.assertEqual("reset-at", values["stats_reset"])

    def test_checkpointer_snapshot_includes_checkpoint_and_writeback_counters(self) -> None:
        diagnostic = self._diagnostic_module()

        class Cursor:
            query = ""

            def execute(self, query):
                self.query = query

            def fetchone(self):
                return (2, 3, 4, 5, 6, 7.5, 8.5, 9, "reset-at")

        cursor = Cursor()
        snapshot = diagnostic._checkpointer_snapshot(cursor)
        self.assertIn("FROM pg_stat_checkpointer", cursor.query)
        self.assertEqual(3.0, snapshot["counters"]["num_requested"])
        self.assertEqual(7.5, snapshot["counters"]["write_time_ms"])
        self.assertEqual(8.5, snapshot["counters"]["sync_time_ms"])
        self.assertEqual(9.0, snapshot["counters"]["buffers_written"])

    def test_io_delta_marks_reset_and_unavailable_statistics(self) -> None:
        diagnostic = self._diagnostic_module()
        reset = diagnostic._counter_delta(
            {"available": True, "counters": {"write_time_ms": 20}, "stats_reset": "old"},
            {"available": True, "counters": {"write_time_ms": 1}, "stats_reset": "new"},
        )
        self.assertTrue(reset["stats_reset_changed"])
        self.assertIsNone(reset["counters"]["write_time_ms"])
        unavailable = diagnostic._counter_delta(
            {"available": False, "reason": "InsufficientPrivilege"},
            {"available": True, "rows": {}},
        )
        self.assertFalse(unavailable["available"])
        self.assertEqual("InsufficientPrivilege", unavailable["reason"])

    def test_pg_stat_io_null_and_missing_groups_are_not_reported_as_zero(self) -> None:
        diagnostic = self._diagnostic_module()

        class Cursor:
            def execute(self, _query):
                pass

            def fetchall(self):
                return [("background writer", "relation", "normal", None, None,
                         3, 0.0, 2, 0.0, None, None, None, None,
                         10, 1, None, "same")]

        snapshot = diagnostic._pg_stat_io_snapshot(Cursor())
        key = ("background writer", "relation", "normal")
        self.assertIsNone(snapshot["rows"][key]["reads"])
        delta = diagnostic._counter_delta(
            {"available": True, "rows": {key: {
                **snapshot["rows"][key], "writes": 2.0,
            }}}, snapshot,
        )
        counters = delta["groups"]["background writer|relation|normal"]["counters"]
        self.assertIsNone(counters["reads"])
        self.assertEqual(1.0, counters["writes"])

        missing = diagnostic._counter_delta(
            {"available": True, "rows": {}}, snapshot,
        )["groups"]["background writer|relation|normal"]
        self.assertTrue(missing["missing_boundary"])
        self.assertIsNone(missing["counters"]["writes"])

    def test_checkpointer_time_is_independent_of_track_io_timing(self) -> None:
        diagnostic = self._diagnostic_module()
        delta = diagnostic._counter_delta(
            {"available": True, "counters": {"write_time_ms": 7.5, "sync_time_ms": 2.0},
             "stats_reset": "same"},
            {"available": True, "counters": {"write_time_ms": 10.5, "sync_time_ms": 4.0},
             "stats_reset": "same"},
        )
        self.assertEqual(3.0, delta["counters"]["write_time_ms"])
        self.assertEqual(2.0, delta["counters"]["sync_time_ms"])
        self.assertIn("independently of track_io_timing", delta["note"])

    def test_track_io_timing_read_failure_does_not_abort_measurement_status(self) -> None:
        diagnostic = self._diagnostic_module()

        class UnsupportedCursor:
            def execute(self, _query):
                raise RuntimeError("setting unavailable")

        status = diagnostic._io_timing_status(UnsupportedCursor())
        self.assertFalse(status["available"])
        self.assertIsNone(status["measurement_connection_enabled"])
        self.assertEqual("RuntimeError", status["reason"])


if __name__ == "__main__":
    unittest.main()
