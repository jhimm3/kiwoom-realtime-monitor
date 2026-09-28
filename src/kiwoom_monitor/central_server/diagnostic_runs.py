"""One owned, bounded NAS diagnostic run with persistent reports."""
from __future__ import annotations

import json
import re
import threading
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from .diagnostic_workloads import (
    _history, _set, _set_capture, _write, capture_status, control_snapshot,
    diagnostic_run_lock, diagnostic_tool_status, instance_id,
)


_REPORT_ID = re.compile(r"[A-Za-z0-9-]{1,80}\Z")
_MAX_REPORT_BYTES = 32 * 1024 * 1024


class DiagnosticRuns:
    """Owns sampler lifetime; never owns an operational writer or connection."""

    def __init__(self, path: Path, api) -> None:
        self.path = path
        self.api = api
        self._guard = threading.RLock()
        self._current: dict | None = None
        self._request_ids: dict[tuple[str, str], tuple[str, tuple]] = {}
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._producer_id = uuid4().hex
        self._recover_previous_run()

    def _recover_previous_run(self) -> None:
        manifest = self.path.parent / "diagnostic-run-current.json"
        if manifest.is_symlink() or not manifest.is_file():
            return
        try:
            if manifest.stat().st_size > _MAX_REPORT_BYTES:
                return
            previous = json.loads(manifest.read_text(encoding="utf-8"))
            report_id = previous.get("run_id", "")
            if (not _REPORT_ID.fullmatch(report_id)
                    or previous.get("state") not in {"starting", "running", "finalizing"}
                    or (self._results_dir() / f"{report_id}.json").exists()):
                return
            with diagnostic_run_lock(self.path):
                previous.update({"state": "aborted", "reason": "producer_restarted",
                                 "finished_at": time.time(), "result": None})
                self._write_report(previous)
        except (OSError, ValueError, TypeError, RuntimeError):
            return

    def start(self, *, kind: str, seconds: int, label: str,
              workload: str = "", request_id: str = "",
              expected_session: str | None = None,
              expected_revision: int | None = None) -> dict:
        from kiwoom_monitor.central_server.diagnostic_workloads import WORKLOADS

        if kind not in {"measure", "compare"} or not 5 <= seconds <= (300 if kind == "measure" else 1100):
            raise ValueError("invalid_diagnostic_run")
        if kind == "compare" and workload not in WORKLOADS:
            raise ValueError("invalid_comparison_workload")
        if len(label) > 80 or len(request_id) > 80 or not _REPORT_ID.fullmatch(request_id or "a"):
            raise ValueError("invalid_diagnostic_identifier")
        signature = (kind, seconds, label, workload)
        if request_id and expected_session is not None:
            with self._guard:
                key = (expected_session, request_id)
                prior = self._request_ids.get(key)
                if prior is not None:
                    prior_id, prior_signature = prior
                    if prior_signature != signature:
                        raise ValueError("diagnostic_request_conflict")
                    return self.status(prior_id)
        tool = diagnostic_tool_status(self.path)
        session_id = tool.get("session_id")
        if not tool.get("enabled") or not isinstance(session_id, str):
            raise ValueError("diagnostic_master_off")
        control = control_snapshot(self.path)
        if expected_session is not None and expected_session != session_id:
            raise ValueError("diagnostic_control_conflict")
        required_seconds = seconds + 30 if kind == "measure" else seconds * 3 + 34
        if float(tool["expires_at"]) - time.time() < required_seconds:
            raise ValueError("diagnostic_master_ttl_too_short")
        capture = capture_status(self.path)
        if capture["enabled"] and float(capture["expires_at"]) - time.time() < required_seconds:
            raise ValueError("diagnostic_capture_ttl_too_short")
        with self._guard:
            key = (session_id, request_id)
            if request_id and key in self._request_ids:
                prior_id, prior_signature = self._request_ids[key]
                if prior_signature != signature:
                    raise ValueError("diagnostic_request_conflict")
                return self.status(prior_id)
            if expected_revision is not None and expected_revision != control["control_revision"]:
                raise ValueError("diagnostic_control_conflict")
            if self._worker is not None and self._worker.is_alive():
                raise ValueError("diagnostic_run_busy")
            self._check_quota()
            run_lock = diagnostic_run_lock(self.path)
            try:
                run_lock.__enter__()
            except RuntimeError as error:
                raise ValueError("diagnostic_run_busy") from error
            run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
            self._stop = threading.Event()
            run = {"run_id": run_id, "kind": kind, "state": "starting",
                   "session_id": session_id, "instance_id": instance_id(),
                   "producer_id": self._producer_id,
                   "control_revision": control["control_revision"],
                   "requested_seconds": seconds, "label": label, "workload": workload,
                   "created_at": time.time(), "result": None}
            self._current = run
            if request_id:
                self._request_ids[key] = (run_id, signature)
            try:
                self._write_manifest(run)
                self._worker = threading.Thread(
                    target=self._run, args=(run, run_lock, capture["enabled"]),
                    name=f"diagnostic-{run_id}", daemon=True,
                )
                self._worker.start()
            except Exception:
                run_lock.__exit__(None, None, None)
                self._current = None
                if request_id:
                    self._request_ids.pop(key, None)
                raise
            return self.status(run_id)

    def _run(self, run: dict, run_lock, capture_was_enabled: bool) -> None:
        from scripts import nas_workload_diagnostic as cli

        run_id = run["run_id"]
        session_id = run["session_id"]
        owner = f"run:{run_id}"
        seconds = run["requested_seconds"]
        capture_owned = False
        try:
            if not capture_was_enabled:
                lease = seconds + 60 if run["kind"] == "measure" else seconds * 3 + 120
                _set_capture(self.path, True, lease, owner=owner,
                             expected_session=session_id)
                capture_owned = True
                _history("capture_auto_on", detail={"run_id": run_id})
                self.api("/api/v1/diagnostics/workloads")
            run["state"] = "running"
            self._write_manifest(run)
            args = SimpleNamespace(command="measure" if run["kind"] == "measure" else "compare",
                                   label=run["label"], workload=run["workload"])
            def checkpoint(start: float, end: float) -> None:
                from .diagnostic_metrics import copy_db_call_samples
                run["_partial_calls"] = copy_db_call_samples(start, end)

            result = cli._run_measurement(args, self.path, seconds, run_id,
                                          session_id, api=self.api, stop=self._stop,
                                          checkpoint=checkpoint)
            run["state"] = "finalizing"
            if result.get("kind") == "measure":
                phases = [result.get("phase", {})]
            else:
                phases = result.get("phases", [])
            for phase in phases:
                if phase.get("state") != "complete" and run.get("_partial_calls"):
                    phase["db_calls_raw_last_checkpoint"] = run["_partial_calls"]
                    phase["capture_gap_seconds"] = max(
                        0.0, time.time() - run["_partial_calls"]["captured_at"])
            run["result"] = result
            phase_coverage = all(phase.get("db_calls", {}).get("state") == "complete"
                                 for phase in phases if phase.get("state") == "complete")
            run["state"] = ("completed" if result.get("state") == "complete" and phase_coverage
                            else "incomplete" if result.get("state") == "complete" else "aborted")
        except Exception as error:
            run["state"] = "failed"
            run["error_type"] = type(error).__name__
        finally:
            try:
                if capture_owned:
                    _set_capture(self.path, False, 600, owner=owner,
                                 expected_session=session_id)
                    _history("capture_auto_off", detail={"run_id": run_id})
                    self.api("/api/v1/diagnostics/workloads")
            except Exception as error:
                run["cleanup_error_type"] = type(error).__name__
                if run["state"] == "completed":
                    run["state"] = "incomplete"
            run["finished_at"] = time.time()
            try:
                self._write_report(run)
            except Exception as error:
                run["report_persist_error_type"] = type(error).__name__
                if run["state"] == "completed":
                    run["state"] = "incomplete"
            finally:
                run_lock.__exit__(None, None, None)

    def status(self, run_id: str) -> dict:
        if not _REPORT_ID.fullmatch(run_id):
            raise KeyError(run_id)
        with self._guard:
            if self._current is not None and self._current["run_id"] == run_id:
                response = {key: deepcopy(value) for key, value in self._current.items()
                            if not key.startswith("_") and key != "result"}
                response["status_url"] = f"/api/v1/diagnostics/runs/{run_id}"
                if response["state"] not in {"starting", "running", "finalizing"}:
                    response["report_url"] = f"/api/v1/diagnostics/reports/{run_id}"
                return response
        report = self.report(run_id)
        response = {key: value for key, value in report.items() if key != "result"}
        response["status_url"] = f"/api/v1/diagnostics/runs/{run_id}"
        response["report_url"] = f"/api/v1/diagnostics/reports/{run_id}"
        return response

    def cancel(self, run_id: str) -> dict:
        with self._guard:
            if self._current is None or self._current["run_id"] != run_id:
                return self.status(run_id)
            self._stop.set()
            return self.status(run_id)

    def close(self, timeout: float = 10.0) -> None:
        self._stop.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout)

    def _results_dir(self) -> Path:
        return self.path.parent / "diagnostic-results"

    def _report_path(self, report_id: str) -> Path:
        if not _REPORT_ID.fullmatch(report_id):
            raise KeyError(report_id)
        path = self._results_dir() / f"{report_id}.json"
        if path.is_symlink() or not path.is_file():
            raise KeyError(report_id)
        if path.stat().st_size > _MAX_REPORT_BYTES:
            raise ValueError("diagnostic_report_too_large")
        return path

    def report(self, report_id: str) -> dict:
        return json.loads(self._report_path(report_id).read_text(encoding="utf-8"))

    def reports(self, *, limit: int = 100, offset: int = 0) -> dict:
        if not 1 <= limit <= 100 or not 0 <= offset <= 100_000:
            raise ValueError("invalid_diagnostic_page")
        directory = self._results_dir()
        files = sorted(directory.glob("*.json"), key=lambda path: path.name, reverse=True) if directory.exists() else []
        items = []
        for path in files[offset:offset + limit]:
            if path.is_symlink() or path.stat().st_size > _MAX_REPORT_BYTES:
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items.append({"report_id": path.stem, "kind": value.get("kind"),
                          "state": value.get("state"), "schema": value.get("schema", "legacy"),
                          "created_at": value.get("created_at")})
        return {"items": items, "offset": offset, "has_more": len(files) > offset + limit}

    def history(self, *, limit: int = 100, offset: int = 0) -> dict:
        """Page the append-only event log from its tail without loading the whole file."""
        if not 1 <= limit <= 100 or not 0 <= offset <= 100_000:
            raise ValueError("invalid_diagnostic_page")
        path = self.path.parent / "diagnostic-history.jsonl"
        if path.is_symlink() or not path.is_file():
            return {"items": [], "offset": offset, "has_more": False}
        needed = offset + limit + 1
        chunks = []
        size = 0
        newlines = 0
        with path.open("rb") as source:
            position = source.seek(0, 2)
            while position > 0 and newlines < needed + 1 and size < _MAX_REPORT_BYTES:
                take = min(65_536, position, _MAX_REPORT_BYTES - size)
                position -= take
                source.seek(position)
                block = source.read(take)
                chunks.append(block)
                size += len(block)
                newlines += block.count(b"\n")
        lines = b"".join(reversed(chunks)).splitlines()
        if position > 0 and lines:
            lines.pop(0)  # The first line started before our bounded window.
        if position > 0 and len(lines) < needed:
            raise ValueError("diagnostic_history_window_exceeded")
        entries = []
        for line in reversed(lines):
            try:
                entries.append(json.loads(line))
            except (UnicodeError, ValueError):
                continue
        return {"items": entries[offset:offset + limit], "offset": offset,
                "has_more": len(entries) > offset + limit or position > 0}

    def _check_quota(self) -> None:
        directory = self._results_dir()
        if not directory.exists():
            return
        files = list(directory.glob("*.json"))
        if len(files) >= 1000 or sum(path.stat().st_size for path in files if not path.is_symlink()) >= 1_000_000_000:
            raise ValueError("diagnostic_report_quota")

    def _write_manifest(self, run: dict) -> None:
        target = self.path.parent / "diagnostic-run-current.json"
        _write(target, {key: value for key, value in run.items() if key != "result"})

    def _write_report(self, run: dict) -> None:
        report = {"schema": 2, **{key: deepcopy(value) for key, value in run.items()
                                   if not key.startswith("_")}}
        encoded = json.dumps(report, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > _MAX_REPORT_BYTES:
            result = report.get("result")
            if isinstance(result, dict):
                phases = ([result.get("phase", {})] if result.get("kind") == "measure"
                          else result.get("phases", []))
                for phase in phases:
                    if isinstance(phase, dict):
                        phase.pop("db_calls_raw", None)
                        phase.pop("db_calls_raw_last_checkpoint", None)
                        for kind in phase.get("market_bar_saves", {}).get("kinds", {}).values():
                            kind.pop("call_samples", None)
            report["state"] = run["state"] = "incomplete"
            report["reason"] = "report_size_limit_raw_omitted"
            if len(json.dumps(report, ensure_ascii=False).encode("utf-8")) > _MAX_REPORT_BYTES:
                report["result"] = None
                report["reason"] = "report_size_limit"
        target = self._results_dir() / f"{run['run_id']}.json"
        _write(target, report)
        self._write_manifest(run)
        _history("report_saved", detail={"run_id": run["run_id"]})
