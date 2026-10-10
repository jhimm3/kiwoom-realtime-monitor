import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

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
            schema_override = None
            missing_post_flag = None
            def request(self, path, body=None, method="GET"):
                owner.calls.append((path, method, copy.deepcopy(body)))
                if method == "PUT":
                    return {"diagnostic_tool": {"enabled": True, "session_id": "session"}}
                if method == "POST":
                    if self.fail_post:
                        raise TimeoutError("uncertain acknowledgement")
                    schema = 4 if any(body.get(x) for x in scheduler.EXTRA_FLAGS) else 3
                    options = {x: body.get(x, False) for x in scheduler.FLAGS + scheduler.EXTRA_FLAGS}
                    if self.missing_post_flag:
                        options[self.missing_post_flag] = False
                    return {"state": "running", "schema_version": self.schema_override or schema,
                            "source_release": "wrong" if self.corrupt_post else "test-release",
                            "instance_id": "instance", "master_session": "session",
                            "payload_capture": options,
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

    def test_protected_selector_uses_fixed_read_only_status_client(self):
        root = Path('/volume1/docker/kiwoom-monitor')
        response = SimpleNamespace(returncode=0, stdout=json.dumps({
            'state': 'ok', 'result': {'active_release': self.plan['release']}}).encode())
        with patch.object(Path, 'read_text', side_effect=PermissionError), \
                patch.object(scheduler.subprocess, 'run', return_value=response) as run:
            scheduler.preflight(self.api, root, self.plan)
        self.assertEqual(['/usr/local/bin/kiwoom-nas', 'status'], run.call_args.args[0])
        self.assertEqual(20, run.call_args.kwargs['timeout'])
        self.assertNotIn('shell', run.call_args.kwargs)
        self.assertTrue(all(x[1] == 'GET' for x in self.calls))

    def test_failed_or_invalid_status_is_not_accepted_or_followed_by_control_changes(self):
        root = Path('/volume1/docker/kiwoom-monitor')
        for response in (SimpleNamespace(returncode=1, stdout=b'{}'),
                         SimpleNamespace(returncode=0, stdout=b'not-json'),
                         SimpleNamespace(returncode=0, stdout=b'{"state":"ok","result":{"active_release":12}}'),
                         SimpleNamespace(returncode=0, stdout=b'{"state":"ok","result":{"active_release":"wrong"}}')):
            with self.subTest(response=response), \
                    patch.object(Path, 'read_text', side_effect=PermissionError), \
                    patch.object(scheduler.subprocess, 'run', return_value=response):
                with self.assertRaises(RuntimeError):
                    scheduler.preflight(self.api, root, self.plan)
        self.assertEqual([], self.calls)

    def test_permission_fallback_is_limited_to_the_fixed_NAS_root(self):
        with patch.object(Path, 'read_text', side_effect=PermissionError), \
                patch.object(scheduler.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'unexpected_NAS_status_fallback_root'):
                scheduler.active_release(self.root)
        run.assert_not_called()

    def test_invalid_readable_selector_does_not_fall_back_to_status(self):
        (self.root / 'source-runtime/active.json').write_text('not-json')
        with patch.object(scheduler.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                scheduler.active_release(self.root)
        run.assert_not_called()

    def enable_account_large_plan(self):
        self.plan["capture_flags"] = list(scheduler.FLAGS + scheduler.EXTRA_FLAGS)
        for flag, key in (("large_inputs", "large_input_capture"),
                          ("account_inputs", "account_input_capture")):
            self.caps["trace_input_capture"][key] = {
                "schema_version": 4, "request_field": flag, "default": False,
                "requires": ["store_inputs", "persist_at"],
                "context_capture": {"version": "account-context/v2"}}

    def test_monday_account_large_plan_posts_five_flags_and_verifies_schema4(self):
        self.enable_account_large_plan()
        result = self.run_start()
        self.assertEqual(4, result["trace"]["schema_version"])
        post = next(body for _, method, body in self.calls if method == "POST")
        self.assertTrue(all(post[x] is True for x in scheduler.FLAGS + scheduler.EXTRA_FLAGS))
        self.assertEqual(1, sum(method == "POST" for _, method, _ in self.calls))

    def test_legacy_plan_preserves_schema3_and_omits_new_opt_ins(self):
        result = self.run_start()
        self.assertEqual(3, result["trace"]["schema_version"])
        post = next(body for _, method, body in self.calls if method == "POST")
        self.assertTrue(all(x not in post for x in scheduler.EXTRA_FLAGS))

    def test_unknown_duplicate_or_incomplete_flags_fail_before_api_calls(self):
        for flags in (True, [*scheduler.FLAGS, "unknown"], [*scheduler.FLAGS, "store_inputs"],
                      ["account_inputs"], [*scheduler.FLAGS, 1]):
            with self.subTest(flags=flags):
                self.plan["capture_flags"] = flags
                with self.assertRaisesRegex(RuntimeError, "invalid_capture_flags"):
                    self.run_start()
                self.assertEqual([], self.calls)

    def test_missing_new_capability_or_context_fails_before_controls_change(self):
        for fault in ("missing", "default", "context", "schema"):
            with self.subTest(fault=fault):
                self.setUp()
                self.enable_account_large_plan()
                option = self.caps["trace_input_capture"]["account_input_capture"]
                if fault == "missing": del self.caps["trace_input_capture"]["large_input_capture"]
                if fault == "default": option["default"] = True
                if fault == "context": option["context_capture"]["version"] = "account-context/v1"
                if fault == "schema": option["schema_version"] = 3
                with self.assertRaises(RuntimeError): self.run_start()
                self.assertTrue(all(method == "GET" for _, method, _ in self.calls))

    def test_schema_or_flag_mismatch_is_reported_after_one_post_without_retry(self):
        for fault in ("schema", "account_inputs", "large_inputs"):
            with self.subTest(fault=fault):
                self.setUp()
                self.enable_account_large_plan()
                if fault == "schema": self.api.schema_override = 3
                else: self.api.missing_post_flag = fault
                with self.assertRaises(RuntimeError): self.run_start()
                self.assertEqual(1, sum(method == "POST" for _, method, _ in self.calls))
                self.assertEqual(1, sum(method == "PUT" for _, method, _ in self.calls))


if __name__ == "__main__":
    unittest.main()
