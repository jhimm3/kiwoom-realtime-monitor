"""Exercise real control-file mutations, not only hand-written reader fixtures."""
from __future__ import annotations

import contextlib
import importlib.util
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.diagnostic_workloads import (
    capture_status, control_snapshot, diagnostic_tool_status, paused_workloads,
)


class DiagnosticCliControlTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[2] / "scripts/nas_workload_diagnostic.py"
        spec = importlib.util.spec_from_file_location("diagnostic_cli_under_test", source)
        self.cli = importlib.util.module_from_spec(spec)
        try:
            import fcntl  # noqa: F401 -- use the real Linux lock when available
        except ImportError:
            # Windows: only the Linux file lock is substituted. These are sequential
            # mutation tests, not evidence of cross-process flock correctness.
            with patch.dict(sys.modules, {"fcntl": types.ModuleType("fcntl")}):
                spec.loader.exec_module(self.cli)
            lock = patch.object(self.cli, "_control_lock", lambda path: contextlib.nullcontext())
            lock.start()
            self.addCleanup(lock.stop)
        else:
            spec.loader.exec_module(self.cli)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "controls.json"

    def test_db_call_report_distinguishes_complete_changed_and_unavailable(self):
        session_id = "session-one"
        control = {"control_revision": 4, "metrics_capture": {
            "enabled": True, "session_id": session_id}}
        response = {"producer": {"pid": 42, "process_id": "server-one"},
                    "capture": {"enabled": True, "session_id": session_id},
                    "writers": {"query_cache": {"calls": 1}}, "readers": {},
                    "coverage": "opt_in_observed_calls_only", "dropped": 0}
        complete = self.cli._db_calls_report(response, response, session_id,
                                             control, control)
        self.assertEqual("complete", complete["state"])
        self.assertIs(response, complete["summary"])
        changed = {**response, "producer": {"pid": 43, "process_id": "server-two"}}
        self.assertEqual("server_process_changed", self.cli._db_calls_report(
            response, changed, session_id, control, control)["reason"])
        expired = {**control, "metrics_capture": {"enabled": False,
                                                   "session_id": session_id}}
        self.assertEqual("capture_session_ended_or_changed", self.cli._db_calls_report(
            response, response, session_id, control, expired)["reason"])
        revised = {**control, "control_revision": 5}
        self.assertEqual("diagnostic_control_changed", self.cli._db_calls_report(
            response, response, session_id, control, revised)["reason"])
        self.assertEqual("HTTPError", self.cli._db_calls_report(
            response, {"unavailable": "HTTPError"}, session_id, control,
            control)["reason"])
        self.assertEqual("bounded_capture_truncated", self.cli._db_calls_report(
            response, {**response, "truncated": True}, session_id, control,
            control)["reason"])
        self.assertEqual("metadata_missing", self.cli._db_calls_report(
            {}, response, session_id, control, control)["reason"])

    def test_measure_collects_db_calls_for_the_same_window(self):
        session_id = self.cli._set_tool(self.path, True, 600)["diagnostic_tool"]["session_id"]
        self.cli._set_capture(self.path, True, 60)
        queries = []
        response = {"producer": {"pid": 42, "process_id": "server-one"},
                    "capture": {"enabled": True, "session_id": session_id},
                    "writers": {}, "readers": {}, "coverage": "opt_in_observed_calls_only"}

        class FakeCursor:
            def __enter__(self): return self
            def __exit__(self, *unused): return False
            def execute(self, query): pass

        class FakeConnection:
            def __enter__(self): return self
            def __exit__(self, *unused): return False
            def cursor(self): return FakeCursor()

        def api(path, query=None):
            if path.endswith("/db-calls"):
                queries.append(query)
                return response
            return {}

        device_before = {"dm-4": [0, 0, 0, 0, 3, 0, 10, 20, 0, 30, 40]}
        device_after = {"dm-4": [0, 0, 0, 0, 5, 0, 14, 26, 0, 34, 48]}

        with (patch.dict("os.environ", {
                "KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(self.path),
                "KIWOOM_SERVER_DATABASE_URL": "unused-test-connection"}),
              patch.dict(sys.modules, {"psycopg": types.SimpleNamespace(
                  connect=lambda *a, **k: FakeConnection(), Error=Exception)}),
              patch.object(self.cli, "_api", side_effect=api),
              patch.object(self.cli, "_log_position", return_value=(self.path, 0)),
              patch.object(self.cli, "_device_stats",
                           side_effect=[device_before, device_before, device_after]),
              patch.object(self.cli.time, "time", return_value=100.0),
              patch.object(self.cli, "_host_usage", return_value={}),
              patch.object(self.cli, "_uncontrolled_importers", return_value=[]),
              patch.object(self.cli, "_wal_timing_status", return_value={}),
              patch.object(self.cli, "_io_timing_status", return_value={}),
              patch.object(self.cli, "_snapshot", return_value={"wal_bytes": 0}),
              patch.object(self.cli, "_optional_io_snapshot", return_value={}),
              patch.object(self.cli, "_wal_timing_report", return_value={}),
              patch.object(self.cli, "_storage_mapping", return_value={}),
              patch.object(self.cli, "_log_counts", return_value={})):
            phase = self.cli._measure(0, "test", session_id)
        self.assertEqual("complete", phase["state"])
        self.assertEqual(0, phase["elapsed_seconds"])
        self.assertIsNone(phase["wal_bytes_per_second"])
        self.assertIsNone(phase["storage_devices"]["dm-4"]["average_queue"])
        self.assertIsNone(phase["storage_devices"]["dm-4"]["busy_percent"])
        self.assertEqual("complete", phase["db_calls"]["state"])
        self.assertEqual(2, len(queries))
        self.assertGreaterEqual(queries[1]["end"], queries[1]["start"])
        self.assertEqual("opt_in_observed_calls_only",
                         phase["db_calls"]["summary"]["coverage"])

    def test_external_market_activity_uses_collector_counter_delta(self):
        phase = {
            "runtime_before": {"workloads": {"external_market": {"runtime": {
                "collection_attempts": 4, "collection_completions": 4,
                "collection_saved_rows_total": 12,
            }}}},
            "runtime_after": {"workloads": {"external_market": {"runtime": {
                "collection_attempts": 5, "last_collection_saved_rows": 23,
                "collection_completions": 5, "collection_saved_rows_total": 35,
                "last_collection_completed_at": 100.0, "last_collection_error": None,
            }}}},
        }
        self.assertEqual({
            "observed": True, "collection_attempts": 1, "collection_completions": 1,
            "last_collection_completed_at": 100.0,
            "saved_rows_delta": 23, "last_collection_error": None,
        }, self.cli._external_market_activity(phase))

    def test_external_market_activity_marks_inactive_phase_unobserved(self):
        phase = {
            "runtime_before": {"workloads": {"external_market": {"runtime": {
                "collection_attempts": 4, "collection_completions": 4,
                "collection_saved_rows_total": 10,
            }}}},
            "runtime_after": {"workloads": {"external_market": {"runtime": {
                "collection_attempts": 4, "collection_completions": 4,
                "collection_saved_rows_total": 10,
            }}}},
        }
        self.assertFalse(self.cli._external_market_activity(phase)["observed"])

    def test_external_market_comparison_rejects_collector_that_is_not_running(self):
        session_id = self.cli._set_tool(self.path, True, 600)["diagnostic_tool"]["session_id"]
        runtime = {"workloads": {"external_market": {
            "configured": True,
            "runtime": {"operational_enabled": False, "running": False,
                         "poll_seconds": 300},
        }}}
        with patch.object(self.cli, "_api", return_value=runtime):
            with self.assertRaisesRegex(ValueError, "not operationally running"):
                self.cli._run_measurement(
                    SimpleNamespace(command="test", workload="external_market"),
                    self.path, 310, "test-id", session_id,
                )

    def test_external_market_comparison_rejects_phase_shorter_than_poll(self):
        session_id = self.cli._set_tool(self.path, True, 600)["diagnostic_tool"]["session_id"]
        runtime = {"workloads": {"external_market": {
            "configured": True,
            "runtime": {"operational_enabled": True, "running": True,
                         "poll_seconds": 300},
        }}}
        with patch.object(self.cli, "_api", return_value=runtime):
            with self.assertRaisesRegex(ValueError, "duration must exceed external_market poll_seconds"):
                self.cli._run_measurement(
                    SimpleNamespace(command="test", workload="external_market"),
                    self.path, 300, "test-id", session_id,
                )

    def test_child_mutations_preserve_master_and_other_children(self):
        parent = self.cli._set_tool(self.path, True, 600)["diagnostic_tool"]
        self.cli._set(self.path, "minute_backfill", True, 60)
        self.assertTrue(diagnostic_tool_status(self.path)["enabled"])
        self.assertEqual(paused_workloads(path=self.path), {"minute_backfill"})
        self.cli._set_capture(self.path, True, 90)
        self.assertTrue(capture_status(self.path)["enabled"])
        self.assertEqual(paused_workloads(path=self.path), {"minute_backfill"})
        self.cli._set(self.path, "news_jobs", True, 60)
        self.cli._set(self.path, "minute_backfill", False, 60)
        self.assertEqual(paused_workloads(path=self.path), {"news_jobs"})
        self.assertTrue(capture_status(self.path)["enabled"])
        self.cli._set_capture(self.path, False, 60)
        self.assertFalse(capture_status(self.path)["enabled"])
        self.assertEqual(paused_workloads(path=self.path), {"news_jobs"})
        self.assertEqual(self.cli._payload(self.path)["diagnostic_tool"], parent)

    def test_master_off_clears_children_and_cleanup_does_not_reenable_it(self):
        self.cli._set_tool(self.path, True, 600)
        self.cli._set_capture(self.path, True, 60, owner="run:first")
        self.cli._set(self.path, "news_jobs", True, 60)
        self.cli._set_tool(self.path, False)
        self.cli._set_capture(self.path, False, 60, owner="run:first")
        self.assertFalse(diagnostic_tool_status(self.path)["enabled"])
        self.assertFalse(capture_status(self.path)["enabled"])
        self.assertEqual(paused_workloads(path=self.path), set())
        with self.assertRaisesRegex(ValueError, "tool is OFF"):
            self.cli._set(self.path, "news_jobs", True, 60)
        self.cli._set_tool(self.path, True, 600)
        self.assertEqual(paused_workloads(path=self.path), set())
        self.assertFalse(capture_status(self.path)["enabled"])

    def test_run_cleanup_preserves_manual_capture_and_parent(self):
        self.cli._set_tool(self.path, True, 600)
        self.cli._set_capture(self.path, True, 60, owner="manual")
        self.cli._set_capture(self.path, False, 60, owner="run:old")
        self.assertTrue(diagnostic_tool_status(self.path)["enabled"])
        self.assertEqual(capture_status(self.path)["owner"], "manual")

    def test_repeated_on_does_not_reset_children_or_extend_ttl(self):
        first = self.cli._set_tool(self.path, True, 600)
        self.cli._set(self.path, "news_jobs", True, 60)
        second = self.cli._set_tool(self.path, True, 600)
        self.assertEqual(first["diagnostic_tool"], second["diagnostic_tool"])
        self.assertEqual(paused_workloads(path=self.path), {"news_jobs"})
        self.assertEqual(control_snapshot(self.path)["control_revision"], 2)
        self.cli._set_tool(self.path, False)
        third = self.cli._set_tool(self.path, True, 600)
        self.assertNotEqual(first["diagnostic_tool"]["session_id"],
                            third["diagnostic_tool"]["session_id"])
        self.assertEqual(paused_workloads(path=self.path), set())

    def test_old_run_cleanup_cannot_modify_new_session_even_with_same_owner(self):
        old_session = self.cli._set_tool(self.path, True, 600)["diagnostic_tool"]["session_id"]
        self.cli._set_tool(self.path, False)
        new_session = self.cli._set_tool(self.path, True, 600)["diagnostic_tool"]["session_id"]
        self.cli._set(self.path, "news_jobs", True, 60, owner="test:repeat")
        self.cli._set_capture(self.path, True, 60, owner="run:repeat")
        self.cli._set(self.path, "news_jobs", False, 60,
                      owner="test:repeat", expected_session=old_session)
        self.cli._set_capture(self.path, False, 60,
                              owner="run:repeat", expected_session=old_session)
        self.assertEqual(diagnostic_tool_status(self.path)["session_id"], new_session)
        self.assertEqual(paused_workloads(path=self.path), {"news_jobs"})
        self.assertTrue(capture_status(self.path)["enabled"])

    def test_old_run_stops_before_db_sampling_and_does_not_touch_new_session(self):
        old_session = self.cli._set_tool(self.path, True, 600)["diagnostic_tool"]["session_id"]
        self.cli._set_tool(self.path, False)
        self.cli._set_tool(self.path, True, 600)
        with patch.dict("os.environ", {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(self.path)}):
            phase = self.cli._measure(5, "old_run", old_session)
        self.assertEqual(phase["state"], "aborted")
        self.assertEqual(phase["samples"], 0)
        self.assertTrue(diagnostic_tool_status(self.path)["enabled"])

    def test_measurement_stops_when_master_turns_off_during_sampling(self):
        session_id = self.cli._set_tool(self.path, True, 600)["diagnostic_tool"]["session_id"]
        executed = []

        class FakeCursor:
            def __enter__(self): return self
            def __exit__(self, *unused): return False
            def execute(self, query): executed.append(query)
            def fetchall(self): return []
            def fetchone(self): return (0, 0, 0, 0, 0, 0, 0, 0, None)

        class FakeConnection:
            def __enter__(self): return self
            def __exit__(self, *unused): return False
            def cursor(self): return FakeCursor()

        def end_session(_seconds):
            self.cli._set_tool(self.path, False)

        with (patch.dict("os.environ", {
                "KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(self.path),
                "KIWOOM_SERVER_DATABASE_URL": "unused-test-connection"}),
              patch.dict(sys.modules, {"psycopg": types.SimpleNamespace(
                  connect=lambda *a, **k: FakeConnection(), Error=Exception)}),
              patch.object(self.cli, "_api", return_value={}),
              patch.object(self.cli, "_log_position", return_value=(self.path, 0)),
              patch.object(self.cli, "_device_stats", return_value={}),
              patch.object(self.cli, "_host_usage", return_value={}),
              patch.object(self.cli, "_uncontrolled_importers", return_value=[]),
              patch.object(self.cli, "_wal_timing_status", return_value={}),
              patch.object(self.cli, "_snapshot", return_value={"wal_bytes": 0}) as snapshot,
              patch.object(self.cli.time, "sleep", side_effect=end_session)):
            phase = self.cli._measure(5, "stopped", session_id)

        self.assertEqual(phase["state"], "aborted")
        self.assertEqual(phase["samples"], 1)
        self.assertEqual(snapshot.call_count, 1)  # No post-OFF DB snapshot.
        self.assertEqual(sum("pg_stat_activity" in query for query in executed), 1)

    def test_database_probe_failure_is_saved_as_aborted_phase(self):
        session_id = self.cli._set_tool(self.path, True, 600)["diagnostic_tool"]["session_id"]

        class ProbeError(Exception):
            pass

        def failed_connect(*unused, **kwargs):
            raise ProbeError("simulated read timeout")

        with (patch.dict("os.environ", {
                "KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(self.path),
                "KIWOOM_SERVER_DATABASE_URL": "unused-test-connection"}),
              patch.dict(sys.modules, {"psycopg": types.SimpleNamespace(
                  connect=failed_connect, Error=ProbeError)}),
              patch.object(self.cli, "_api", return_value={}),
              patch.object(self.cli, "_log_position", return_value=(self.path, 0)),
              patch.object(self.cli, "_device_stats", return_value={}),
              patch.object(self.cli, "_host_usage", return_value={}),
              patch.object(self.cli, "_uncontrolled_importers", return_value=[])):
            phase = self.cli._measure(5, "timeout", session_id)

        self.assertEqual(phase["state"], "aborted")
        self.assertEqual(phase["reason"], "database_probe_failed")
        self.assertEqual(phase["samples"], 0)

    def test_parent_expiry_caps_child_display_even_with_longer_file_lease(self):
        import time
        parent = self.cli._set_tool(self.path, True, 600)
        expiry = parent["diagnostic_tool"]["expires_at"]
        payload = self.cli._payload(self.path)
        payload["leases"] = {"news_jobs": expiry + 300}
        payload["capture"] = {"expires_at": expiry + 300, "owner": "manual"}
        self.cli._write(self.path, payload)
        snapshot = control_snapshot(self.path, now=expiry - 1)
        self.assertEqual(snapshot["workload_expiries"]["news_jobs"], expiry)
        self.assertEqual(snapshot["metrics_capture"]["expires_at"], expiry)
        self.assertEqual(snapshot["expires_at"], expiry)
        self.assertEqual(control_snapshot(self.path, now=expiry + 1)["paused"], set())

    def test_master_expires_on_monotonic_deadline_if_wall_clock_moves_back(self):
        import time
        value = self.cli._set_tool(self.path, True, 60)
        deadline = value["diagnostic_tool"]["monotonic_deadline"]
        with patch("kiwoom_monitor.central_server.diagnostic_workloads.time.monotonic",
                   return_value=deadline + 1):
            state = control_snapshot(self.path, now=time.time() - 300)
        self.assertFalse(state["diagnostic_tool"]["enabled"])
        self.assertEqual(state["paused"], set())


if __name__ == "__main__":
    unittest.main()
