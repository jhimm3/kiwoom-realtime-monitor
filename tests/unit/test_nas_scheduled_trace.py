import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("nas_scheduled_trace", Path(__file__).resolve().parents[2] / "scripts/nas_scheduled_trace.py")
scheduler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scheduler)


class ScheduledTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "source-runtime").mkdir()
        self.plan = {"start_at": "2026-10-08T08:59:50+09:00", "persist_at": "2026-10-08T20:10:00+09:00",
                     "seconds": 3600, "build": "test-build", "release": "test-release",
                     "memory_limit_bytes": 8 * 1024**3, "event_capacity": 5000000,
                     "write_bytes_per_second": 1024**2, "maximum_lateness_seconds": 30}
        (self.root / "source-runtime/active.json").write_text(json.dumps({"release_id": "test-release"}))
        self.now = scheduler.epoch(self.plan["start_at"]) - 30
        self.calls = []
        self.flags = {x: False for x in scheduler.FLAGS}
        self.caps = {"server_build": "test-build", "control_available": True, "postgres_available": True,
                     "producer_instance": "instance", "trace_input_capture": {
                         "schema_version": 3, "coverage": "observed_paths_only", "options": self.flags,
                         "collector_event_types": ["0B", "0w", "0J", "0U"],
                         "causal_input_boundaries": list(scheduler.BOUNDARIES),
                         "deferred_persistence": {"supported": True, **{k: self.plan[k] for k in
                             ("memory_limit_bytes", "event_capacity", "write_bytes_per_second")}}}}
        self.work = {"control_revision": 7, "diagnostic_tool": {"enabled": False},
                     "trace_capture": {"enabled": False}, "workloads": {}}
        owner = self
        class FakeApi:
            fail_post = False
            corrupt_post = False
            def request(self, path, body=None, method="GET"):
                owner.calls.append((path, method, copy.deepcopy(body)))
                if method == "PUT":
                    return {"diagnostic_tool": {"enabled": True, "session_id": "session"}}
                if method == "POST":
                    if self.fail_post:
                        raise TimeoutError("uncertain acknowledgement")
                    return {"state": "running", "schema_version": 3,
                            "source_release": "wrong" if self.corrupt_post else "test-release",
                            "instance_id": "instance", "master_session": "session",
                            "payload_capture": {x: True for x in scheduler.FLAGS},
                            "persistence_mode": "deferred_ram", "persist_at": body["persist_at"],
                            "started_at": owner.now, "expires_at": owner.now + body["seconds"],
                            "trace_id": "trace", **{k: owner.plan[k] for k in
                                ("memory_limit_bytes", "event_capacity", "write_bytes_per_second")}}
                return {"/health": {"status": "ok", "server_build": "test-build"},
                        "/api/v1/diagnostics/capabilities": owner.caps,
                        "/api/v1/diagnostics/workloads": owner.work,
                        "/api/v1/diagnostics/trace": {"state": "off"},
                        "/api/v1/diagnostics/reports?limit=100": {"items": []}}[path]
        self.api = FakeApi()

    def wait(self, deadline):
        self.now = max(self.now, deadline)

    def run_start(self):
        return scheduler.start(self.api, self.root, self.plan, self.wait, lambda: self.now)

    def test_only_capture_starts_at_target_with_all_flags_and_numeric_deadline(self):
        result = self.run_start()
        self.assertEqual("started", result["state"])
        self.assertEqual(0, result["start_lateness_seconds"])
        writes = [x for x in self.calls if x[1] != "GET"]
        self.assertEqual(["PUT", "POST"], [x[1] for x in writes])
        self.assertEqual(7, writes[0][2]["expected_revision"])
        self.assertEqual("instance", writes[0][2]["expected_instance"])
        self.assertEqual(3600, writes[1][2]["seconds"])
        self.assertTrue(all(writes[1][2][x] is True for x in scheduler.FLAGS))
        self.assertIsInstance(writes[1][2]["persist_at"], float)

    def test_busy_controls_or_release_mismatch_fail_without_mutation(self):
        for kind in ("busy", "release", "limits", "schema"):
            with self.subTest(kind=kind):
                self.setUp()
                if kind == "busy": self.work["diagnostic_tool"]["enabled"] = True
                if kind == "release": self.plan["release"] = "wrong"
                if kind == "limits": self.plan["event_capacity"] = 1
                if kind == "schema": self.caps["trace_input_capture"]["schema_version"] = 2
                with self.assertRaises(RuntimeError): self.run_start()
                self.assertTrue(all(x[1] == "GET" for x in self.calls))

    def test_control_change_while_waiting_fails_before_enabling_master(self):
        def wait(deadline):
            self.wait(deadline)
            self.work["trace_capture"]["enabled"] = True
        with self.assertRaisesRegex(RuntimeError, "diagnostics_busy"):
            scheduler.start(self.api, self.root, self.plan, wait, lambda: self.now)
        self.assertTrue(all(x[1] == "GET" for x in self.calls))

    def test_late_start_is_rejected_without_mutation(self):
        self.now = scheduler.epoch(self.plan["start_at"]) + 31
        with self.assertRaisesRegex(RuntimeError, "start_deadline_missed"): self.run_start()
        self.assertEqual([], self.calls)

    def test_uncertain_post_ack_is_never_retried_or_stopped(self):
        self.api.fail_post = True
        with self.assertRaises(TimeoutError): self.run_start()
        self.assertEqual(1, sum(x[1] == "POST" for x in self.calls))
        self.assertEqual(1, sum(x[1] == "PUT" for x in self.calls))
        self.assertFalse(any("stop" in x[0] or "runs" in x[0] for x in self.calls))

    def test_wrong_post_release_is_reported_without_stopping_native_capture(self):
        self.api.corrupt_post = True
        with self.assertRaisesRegex(RuntimeError, "trace_source_changed"): self.run_start()
        self.assertEqual(1, sum(x[1] == "POST" for x in self.calls))

    def test_preflight_is_read_only(self):
        scheduler.preflight(self.api, self.root, self.plan)
        self.assertTrue(all(x[1] == "GET" for x in self.calls))


if __name__ == "__main__":
    unittest.main()
