"""Owned NAS diagnostic run and authenticated control contract."""
from __future__ import annotations

import os
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
