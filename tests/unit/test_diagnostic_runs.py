"""Owned NAS diagnostic run and authenticated control contract."""
from __future__ import annotations

import os
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.diagnostic_runs import DiagnosticRuns
from kiwoom_monitor.central_server.diagnostic_workloads import (
    _set_tool, capture_status, control_snapshot, diagnostic_run_lock,
)


class DiagnosticRunTests(unittest.TestCase):
    def test_trace_api_forwards_deferred_deadline_and_guards_ram_held_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'diagnostic-workloads.json'
            settings = CentralServerSettings(f'sqlite:///{Path(directory) / "monitor.sqlite3"}', 'private-token')
            with patch.dict(os.environ, {'KIWOOM_DIAGNOSTIC_WORKLOAD_PATH': str(path)}):
                client = TestClient(create_app(settings))  # No lifespan/network collectors.
                try:
                    session = _set_tool(path, True, 300)['diagnostic_tool']['session_id']
                    body = {'seconds': 60, 'expected_session': session, 'persist_at': time.time() + 3600,
                            'store_inputs': True, 'collector_inputs': True}
                    headers = {'Authorization': 'Bearer private-token'}
                    with patch('kiwoom_monitor.central_server.diagnostic_trace.status', return_value={'state': 'off'}), \
                            patch('kiwoom_monitor.central_server.diagnostic_trace.start', return_value={'state': 'running'}) as start:
                        response = client.post('/api/v1/diagnostics/trace', json=body, headers=headers)
                        self.assertEqual(200, response.status_code, response.text)
                        self.assertEqual(body['persist_at'], start.call_args.kwargs['persist_at'])
                    for state in ('awaiting_persistence', 'persisting'):
                        with patch('kiwoom_monitor.central_server.diagnostic_trace.status', return_value={'state': state}), \
                                patch('kiwoom_monitor.central_server.diagnostic_workloads._set_trace') as child:
                            response = client.post('/api/v1/diagnostics/trace', json=body, headers=headers)
                            self.assertEqual(409, response.status_code, response.text)
                            child.assert_not_called()
                    capabilities = client.get('/api/v1/diagnostics/capabilities', headers=headers).json()
                    deferred = capabilities['trace_input_capture']['deferred_persistence']
                    self.assertEqual(4 * 1024**3, deferred['memory_limit_bytes'])
                    self.assertEqual(1_000_000, deferred['event_capacity'])
                finally:
                    client.close()

    def test_api_forwards_explicit_minute_scenario_and_rejects_unknown_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic-workloads.json"
            settings = CentralServerSettings("postgresql://user:password@database/kiwoom_monitor", "private-token")
            from kiwoom_monitor.central_server.database import create_query_store
            store = create_query_store(f"sqlite:///{Path(directory) / 'monitor.sqlite3'}")
            with patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(path)}), \
                    patch("kiwoom_monitor.central_server.app.create_query_store", return_value=store):
                app = create_app(settings)
                client = TestClient(app)  # No lifespan or external DB access.
                body = {"kind": "replay", "seconds": 15, "workload": "trace_synthetic",
                        "profile_trace_id": "trace-1", "window_end_seconds": 10,
                        "include_writer_kinds": ["query_minute"],
                        "query_minute_scenario": "unchanged_page"}
                with patch.object(app.state.diagnostic_runs, "start", return_value={"run_id": "unit"}) as start:
                    response = client.post("/api/v1/diagnostics/runs", json=body,
                                           headers={"Authorization": "Bearer private-token"})
                    self.assertEqual(202, response.status_code, response.text)
                    self.assertEqual("unchanged_page", start.call_args.kwargs["query_minute_scenario"])
                    response = client.post("/api/v1/diagnostics/runs", json={**body, "query_minute_scenario": "recorded_counts"},
                                           headers={"Authorization": "Bearer private-token"})
                    self.assertEqual(202, response.status_code, response.text)
                    self.assertEqual("recorded_counts", start.call_args.kwargs["query_minute_scenario"])
                    response = client.post("/api/v1/diagnostics/runs", json={**body, "query_minute_scenario": "guess"},
                                           headers={"Authorization": "Bearer private-token"})
                    self.assertEqual(422, response.status_code)
                    self.assertEqual(2, start.call_count)
                client.close()

    def test_racing_trace_start_failure_does_not_disable_the_active_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic-workloads.json"
            settings = CentralServerSettings(f"sqlite:///{Path(directory) / 'monitor.sqlite3'}", "private-token")
            with patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(path)}):
                with TestClient(create_app(settings)) as client:
                    session = _set_tool(path, True, 300)["diagnostic_tool"]["session_id"]
                    with patch("kiwoom_monitor.central_server.diagnostic_trace.status",
                               side_effect=[{"state": "off"}, {"state": "running"}]), \
                            patch("kiwoom_monitor.central_server.diagnostic_trace.start",
                                  side_effect=ValueError("trace_already_running")), \
                            patch("kiwoom_monitor.central_server.diagnostic_workloads._set_trace") as child:
                        response = client.post("/api/v1/diagnostics/trace",
                                               headers={"Authorization": "Bearer private-token"},
                                               json={"seconds": 60, "expected_session": session})
                    self.assertEqual(409, response.status_code)
                    self.assertEqual(1, child.call_count)
                    self.assertTrue(child.call_args.args[1])

    def test_recorded_shape_report_matches_actual_call_and_aborts_on_mismatch_or_missing_metrics(self) -> None:
        from kiwoom_monitor.central_server.diagnostic_replay import ReplayCall, ReplayProfile
        expected = {"bar_shape_version": 1, "bar_changed_rows": 2, "observations": 7,
                    "duplicate_input_keys": 2, "metadata_suppressed_rows": 0,
                    "revision_insert_rows": 1, "revision_history_enabled": 1}
        profile = ReplayProfile("trace-1", 10, 1, 1, (
            ReplayCall(0, "query_minute", rows_attempted=7, source_call_id="source-minute",
                       observed_domain_counts=tuple(expected.items())),), {}, 0, 10,
            ("query_minute",), (), query_minute_scenario="recorded_counts")
        for actual, actual_rows in ((expected, 7), ({**expected, "bar_changed_rows": 3}, 7),
                                    ({}, 7), (expected, 8)):
            with self.subTest(actual=actual, rows=actual_rows), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "diagnostic-workloads.json"
                with patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(path)}):
                    _set_tool(path, True, 120)
                    runs = DiagnosticRuns(path, lambda *_: {}, "postgresql://user:password@database/kiwoom_monitor")
                    def replay(plan, url, run_id, stop, ready, started, done):
                        ready.set()
                        self.assertTrue(started.wait(3))
                        self.assertTrue(done.wait(3))
                        return {"state": "complete", "completed_calls": {"query_minute": 1},
                                "replayed_calls": [{"source_call_id": "source-minute", "replay_call_id": "replay-1",
                                                    "writer_kind": "query_minute", "rows_attempted": 7,
                                                    "expected_domain_counts": expected, "state": "committed"}]}
                    def measure(*args, **kwargs):
                        kwargs["on_started"]()
                        return {"state": "complete", "db_calls": {"state": "complete"},
                                "db_calls_raw": {"calls": [{
                                    "call_id": "db-1", "request_id": "replay-1", "writer_kind": "query_minute",
                                    "database_name": "kiwoom_monitor_diagnostic_test", "rows_attempted": actual_rows,
                                    "outcome": "committed", "commits": 1, "transactions": 1,
                                }]}, "market_bar_saves": {"writer_transactions": {"query_minute": {
                                    "call_samples": [{"db_call_id": "db-1", "domain_counts": actual}]}}}}
                    try:
                        with patch("kiwoom_monitor.central_server.diagnostic_replay.require_after_hours"), \
                                patch("kiwoom_monitor.central_server.diagnostic_replay.compile_trace_replay_profile", return_value=profile), \
                                patch("kiwoom_monitor.central_server.diagnostic_replay.run_replay", side_effect=replay), \
                                patch("scripts.nas_workload_diagnostic._measure", side_effect=measure):
                            run = runs.start(kind="replay", seconds=15, label="shape", workload="trace_synthetic",
                                             profile_trace_id="trace-1", window_end_seconds=10,
                                             include_writer_kinds=("query_minute",), query_minute_scenario="recorded_counts")
                            runs._worker.join(5)
                        report = runs.report(run["run_id"])
                        matched = actual == expected and actual_rows == 7
                        self.assertEqual("completed" if matched else "aborted", report["state"], report)
                        result = report["result"]["replay"]
                        self.assertEqual(int(matched), result["recorded_shape_validation"]["matched_calls"])
                        comparison = result["replayed_calls"][0]["shape_comparison"]
                        self.assertEqual("matched" if matched else "mismatched", comparison["state"])
                        self.assertEqual(not matched, bool(comparison["differences"]))
                        if not matched:
                            self.assertEqual("replay_query_minute_recorded_shape_mismatch", result["error_type"])
                    finally:
                        runs.close()

    def test_selected_replay_uses_dedicated_db_and_writes_report(self) -> None:
        from tests.unit.test_diagnostic_replay import _report

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic-workloads.json"
            with patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(path)}):
                _set_tool(path, True, 120)
                runs = DiagnosticRuns(
                    path, lambda *_: {},
                    "postgresql://user:password@database/kiwoom_monitor")
                reports = runs._results_dir()
                reports.mkdir(parents=True, exist_ok=True)
                (reports / "source-1.json").write_text(
                    json.dumps(_report()), encoding="utf-8")
                observed = {}

                def replay(profile, database_url, _run_id, stop, ready, started, done):
                    observed["database_url"] = database_url
                    ready.set()
                    self.assertTrue(started.wait(3))
                    self.assertTrue(done.wait(3))
                    replayed_calls = [{
                        "source_call_id": call.source_call_id,
                        "replay_call_id": f"replay-{index}",
                        "writer_kind": call.kind,
                        "rows_attempted": call.rows_attempted,
                        "source_domain_counts": {},
                        "state": "committed",
                    } for index, call in enumerate(profile.supported_calls, 1)]
                    return {"state": "complete", "selected_calls": len(profile.supported_calls),
                            "completed_calls": {"news_job_claim": 1,
                                                 "shadow_monitor_state": 1},
                            "replayed_calls": replayed_calls}

                def measure(*args, **kwargs):
                    observed["measurement_database_url"] = kwargs["database_url"]
                    kwargs["on_started"]()
                    return {"state": "complete", "db_calls": {"state": "complete"},
                            "db_calls_raw": {"calls": [
                                {"call_id": "replayed-1", "writer_kind": "shadow_monitor_state",
                                 "database_name": "kiwoom_monitor_diagnostic_test",
                                 "request_id": "replay-1", "rows_attempted": 1,
                                 "total_ms": 18, "execute_ms": 12, "commit_ms": 3,
                                 "transactions": 1, "commits": 1, "rollbacks": 0,
                                 "sql_calls": 2, "outcome": "committed"},
                                {"call_id": "replayed-2", "writer_kind": "news_job_claim",
                                 "database_name": "kiwoom_monitor_diagnostic_test",
                                 "request_id": "replay-2", "rows_attempted": 1,
                                 "total_ms": 24, "execute_ms": 15, "commit_ms": 4,
                                 "transactions": 1, "commits": 1, "rollbacks": 0,
                                 "sql_calls": 2, "outcome": "committed"}]},
                            "market_bar_saves": {"writer_transactions": {
                                "news_job_claim": {"call_samples": []},
                                "shadow_monitor_state": {"call_samples": [
                                    {"db_call_id": "replayed-1",
                                     "domain_counts": {"snapshot_changed": 0}}]}}}}

                try:
                    with patch("kiwoom_monitor.central_server.diagnostic_replay.require_after_hours"), \
                            patch("kiwoom_monitor.central_server.diagnostic_replay.run_replay",
                                  side_effect=replay), \
                            patch("scripts.nas_workload_diagnostic._measure",
                                  side_effect=measure):
                        started = runs.start(
                            kind="replay", seconds=17, label="selected",
                            workload="recorded_news_shadow",
                            profile_report_id="source-1",
                            window_start_seconds=10, window_end_seconds=22,
                            include_writer_kinds=("news_job_claim", "shadow_monitor_state"))
                        runs._worker.join(5)
                    report = runs.report(started["run_id"])
                    self.assertEqual("completed", report["state"])
                    self.assertEqual(2, report["result"]["replay"]["selected_calls"])
                    self.assertEqual("replayed-1", report["result"]["replay"]
                                     ["observed_test_db_calls"][0]["call_id"])
                    replay_calls = report["result"]["replay"]["replayed_calls"]
                    self.assertEqual("shadow-1", replay_calls[0]["source_call_id"])
                    self.assertEqual("replayed-1", replay_calls[0]["database_call"]["db_call_id"])
                    self.assertEqual(18, replay_calls[0]["database_call"]["elapsed_ms"], replay_calls)
                    self.assertEqual({"snapshot_changed": 0},
                                     replay_calls[0]["database_call"]["domain_counts"])
                    self.assertEqual(2, report["result"]["replay"]
                                     ["direct_call_totals"]["commits"])
                    self.assertEqual(2, report["result"]["replay"]
                                     ["correlation"]["linked_db_calls"])
                    for key in ("database_url", "measurement_database_url"):
                        self.assertTrue(observed[key].endswith(
                            "/kiwoom_monitor_diagnostic_test"))
                    self.assertNotIn("_replay_database_url", report)
                finally:
                    runs.close()

    def test_terminal_status_does_not_publish_report_url_before_report_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic-workloads.json"
            with patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(path)}):
                _set_tool(path, True, 120)
                runs = DiagnosticRuns(path, lambda *_: {})
                writing = threading.Event()
                release = threading.Event()
                write_report = runs._write_report

                def delayed_report(run: dict) -> None:
                    writing.set()
                    self.assertTrue(release.wait(3))
                    write_report(run)

                result = {"kind": "measure", "state": "complete",
                          "phase": {"state": "complete",
                                    "db_calls": {"state": "complete"}}}
                try:
                    with patch("scripts.nas_workload_diagnostic._run_measurement",
                               return_value=result), patch.object(
                                   runs, "_write_report", side_effect=delayed_report,
                               ):
                        first = runs.start(kind="measure", seconds=5, label="test")
                        self.assertTrue(writing.wait(3))
                        status = runs.status(first["run_id"])
                        self.assertEqual("completed", status["state"])
                        self.assertNotIn("report_url", status)
                        release.set()
                        runs._worker.join(3)
                        status = runs.status(first["run_id"])
                        self.assertEqual(
                            f"/api/v1/diagnostics/reports/{first['run_id']}",
                            status["report_url"],
                        )
                finally:
                    release.set()
                    runs.close()

    def test_run_has_one_owner_replay_cancel_and_persisted_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic-workloads.json"
            with patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(path)}):
                _set_tool(path, True, 120)
                control = control_snapshot(path)
                started = threading.Event()

                def measure(*args, **kwargs):
                    started.set()
                    kwargs["stop"].wait(3)
                    return {"kind": "measure", "state": "aborted",
                            "phase": {"state": "aborted", "reason": "cancel_requested"}}

                runs = DiagnosticRuns(path, lambda *_: {})
                with patch("scripts.nas_workload_diagnostic._run_measurement", side_effect=measure):
                    first = runs.start(kind="measure", seconds=5, label="test", request_id="same",
                                       expected_session=control["diagnostic_tool"]["session_id"],
                                       expected_revision=control["control_revision"])
                    self.assertTrue(started.wait(3))
                    self.assertNotEqual(control["control_revision"],
                                        control_snapshot(path)["control_revision"])
                    try:
                        self.assertEqual(first["run_id"], runs.start(
                            kind="measure", seconds=5, label="test", request_id="same",
                            expected_session=control["diagnostic_tool"]["session_id"],
                            expected_revision=control["control_revision"])["run_id"])
                        with self.assertRaisesRegex(ValueError, "diagnostic_run_busy"):
                            runs.start(kind="measure", seconds=5, label="another")
                        with self.assertRaisesRegex(RuntimeError, "diagnostic_run_busy"):
                            with diagnostic_run_lock(path):
                                pass
                    finally:
                        runs.cancel(first["run_id"])
                        runs.close()

                result = runs.status(first["run_id"])
                self.assertEqual("aborted", result["state"])
                self.assertFalse(capture_status(path)["enabled"])
                self.assertEqual("aborted", runs.report(first["run_id"])["state"])
                self.assertIn(first["run_id"], [item["report_id"] for item in runs.reports()["items"]])

    def test_control_requires_auth_and_revision_and_snapshot_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic-workloads.json"
            settings = CentralServerSettings(f"sqlite:///{Path(directory) / 'monitor.sqlite3'}",
                                             "private-token")
            with patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(path)}):
                with TestClient(create_app(settings)) as client:
                    headers = {"Authorization": "Bearer private-token"}
                    self.assertEqual(401, client.get("/api/v1/diagnostics/capabilities").status_code)
                    caps = client.get("/api/v1/diagnostics/capabilities", headers=headers).json()
                    self.assertTrue(caps["control_available"])
                    self.assertFalse(caps["postgres_available"])
                    self.assertEqual(501, client.get("/api/v1/diagnostics/snapshot",
                                                     params={"sections": "postgres"},
                                                     headers=headers).status_code)
                    before = control_snapshot(path)
                    response = client.put("/api/v1/diagnostics/control", headers=headers,
                                          json={"target": "master", "enabled": True,
                                                "ttl_seconds": 120, "expected_revision": 0})
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertTrue(response.json()["diagnostic_tool"]["enabled"])
                    self.assertEqual(409, client.put("/api/v1/diagnostics/control", headers=headers,
                                                     json={"target": "master", "enabled": False,
                                                           "expected_revision": 0}).status_code)
                    self.assertEqual(before["control_revision"] + 1,
                                     control_snapshot(path)["control_revision"])
                    revision = control_snapshot(path)["control_revision"]
                    client.put("/api/v1/diagnostics/control", headers=headers,
                               json={"target": "master", "enabled": False,
                                     "expected_revision": revision})
                    self.assertFalse(capture_status(path)["enabled"])

    def test_failed_start_releases_request_id_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic-workloads.json"
            with patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(path)}):
                _set_tool(path, True, 120)
                runs = DiagnosticRuns(path, lambda *_: {})
                with patch.object(runs, "_write_manifest", side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        runs.start(kind="measure", seconds=5, label="test", request_id="retry")
                self.assertIsNone(runs._current)
                with diagnostic_run_lock(path):
                    pass
                with patch("scripts.nas_workload_diagnostic._run_measurement",
                           return_value={"kind": "measure", "state": "aborted",
                                         "phase": {"state": "aborted"}}):
                    second = runs.start(kind="measure", seconds=5, label="test",
                                        request_id="retry")
                    runs.close()
                self.assertEqual("aborted", runs.status(second["run_id"])["state"])

    def test_api_rejects_stale_capture_session_even_if_revision_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic-workloads.json"
            settings = CentralServerSettings(f"sqlite:///{Path(directory) / 'monitor.sqlite3'}",
                                             "private-token")
            with patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(path)}):
                with TestClient(create_app(settings)) as client:
                    headers = {"Authorization": "Bearer private-token"}
                    _set_tool(path, True, 120)
                    revision = control_snapshot(path)["control_revision"]
                    response = client.put("/api/v1/diagnostics/control", headers=headers,
                                          json={"target": "capture", "enabled": True,
                                                "expected_session": "stale-session",
                                                "expected_revision": revision})
                    self.assertEqual(409, response.status_code)
                    self.assertFalse(capture_status(path)["enabled"])

    def test_history_pages_from_tail_and_ignores_partial_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic-workloads.json"
            path.parent.joinpath("diagnostic-history.jsonl").write_text(
                "".join(f'{{"event":{number}}}\n' for number in range(8)),
                encoding="utf-8")
            runs = DiagnosticRuns(path, lambda *_: {})
            self.assertEqual([7, 6, 5], [item["event"] for item in
                             runs.history(limit=3)["items"]])
            self.assertEqual([4, 3, 2], [item["event"] for item in
                             runs.history(limit=3, offset=3)["items"]])
            self.assertTrue(runs.history(limit=3)["has_more"])
            self.assertFalse(runs.history(limit=3, offset=6)["has_more"])


if __name__ == "__main__":
    unittest.main()
