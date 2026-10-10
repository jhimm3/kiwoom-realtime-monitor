"""One owned, bounded NAS diagnostic run with persistent reports."""
from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
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

    def __init__(self, path: Path, api, database_url: str = "") -> None:
        self.path = path
        self.api = api
        self.database_url = database_url
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
              profile_report_id: str = "",
              profile_trace_id: str = "",
              window_start_seconds: float = 0,
              window_end_seconds: float | None = None,
              include_writer_kinds: tuple[str, ...] = (),
              exclude_writer_kinds: tuple[str, ...] = (),
              query_minute_scenario: str = "",
              expected_session: str | None = None,
              expected_revision: int | None = None) -> dict:
        from kiwoom_monitor.central_server.diagnostic_workloads import WORKLOADS

        limits = {"measure": 300, "compare": 1100, "replay": 120}
        if kind not in limits or not 5 <= seconds <= limits[kind]:
            raise ValueError("invalid_diagnostic_run")
        if kind == "compare" and workload not in WORKLOADS:
            raise ValueError("invalid_comparison_workload")
        replay_plan = None
        replay_database_url = ""
        if kind == "replay":
            if workload not in {"recorded_news_shadow", "trace_synthetic"}:
                raise ValueError("invalid_replay_profile")
            if not self.database_url.startswith("postgres"):
                raise ValueError("postgres_replay_unavailable")
            from .diagnostic_replay import (
                compile_replay_profile, compile_trace_replay_profile,
                dedicated_database_url, require_after_hours,
            )

            require_after_hours()
            if workload == "trace_synthetic":
                if (profile_report_id or not _REPORT_ID.fullmatch(profile_trace_id)
                        or window_end_seconds is None):
                    raise ValueError("invalid_replay_trace_profile")
                try:
                    replay_plan = compile_trace_replay_profile(
                        profile_trace_id, seconds,
                        window_start_seconds=window_start_seconds,
                        window_end_seconds=window_end_seconds,
                        include_writer_kinds=include_writer_kinds,
                        exclude_writer_kinds=exclude_writer_kinds,
                        query_minute_scenario=query_minute_scenario)
                except KeyError as error:
                    raise ValueError("replay_trace_not_found") from error
            else:
                if query_minute_scenario:
                    raise ValueError("replay_query_minute_requires_trace")
                if profile_trace_id or not _REPORT_ID.fullmatch(profile_report_id):
                    raise ValueError("invalid_replay_profile")
                try:
                    profile_report = self.report(profile_report_id)
                except KeyError as error:
                    raise ValueError("replay_profile_not_found") from error
                replay_plan = compile_replay_profile(
                    profile_report, seconds,
                    window_start_seconds=window_start_seconds,
                    window_end_seconds=window_end_seconds,
                    include_writer_kinds=include_writer_kinds,
                    exclude_writer_kinds=exclude_writer_kinds)
            replay_database_url = dedicated_database_url(self.database_url)
        elif (profile_report_id or profile_trace_id or window_start_seconds or window_end_seconds is not None
              or include_writer_kinds or exclude_writer_kinds or query_minute_scenario):
            raise ValueError("invalid_replay_profile")
        if len(label) > 80 or len(request_id) > 80 or not _REPORT_ID.fullmatch(request_id or "a"):
            raise ValueError("invalid_diagnostic_identifier")
        signature = (kind, seconds, label, workload, profile_report_id, profile_trace_id,
                     window_start_seconds, window_end_seconds,
                     include_writer_kinds, exclude_writer_kinds, query_minute_scenario)
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
        required_seconds = seconds + 30 if kind in {"measure", "replay"} else seconds * 3 + 34
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
                   "profile_report_id": profile_report_id,
                   "profile_trace_id": profile_trace_id,
                   "window_start_seconds": window_start_seconds,
                   "window_end_seconds": window_end_seconds,
                   "include_writer_kinds": include_writer_kinds,
                   "exclude_writer_kinds": exclude_writer_kinds,
                   "query_minute_scenario": query_minute_scenario,
                   "created_at": time.time(), "result": None}
            if replay_plan is not None:
                run["_replay_plan"] = replay_plan
                run["_replay_database_url"] = replay_database_url
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
        replay_child: threading.Thread | None = None
        try:
            if not capture_was_enabled:
                lease = seconds + 60 if run["kind"] in {"measure", "replay"} else seconds * 3 + 120
                _set_capture(self.path, True, lease, owner=owner,
                             expected_session=session_id)
                capture_owned = True
                _history("capture_auto_on", detail={"run_id": run_id})
                self.api("/api/v1/diagnostics/workloads")
            run["state"] = "running"
            self._write_manifest(run)
            def checkpoint(start: float, end: float) -> None:
                from .diagnostic_metrics import copy_db_call_samples
                run["_partial_calls"] = copy_db_call_samples(start, end)

            if run["kind"] == "replay":
                from .diagnostic_replay import require_after_hours, run_replay

                require_after_hours()

                ready = threading.Event()
                gate = threading.Event()
                measurement_done = threading.Event()
                replay_outcome: dict = {}

                def replay_worker() -> None:
                    try:
                        replay_outcome["result"] = run_replay(
                            run["_replay_plan"], run["_replay_database_url"],
                            run_id, self._stop, ready, gate, measurement_done)
                    except Exception as error:
                        replay_outcome["error_type"] = type(error).__name__
                        self._stop.set()
                        ready.set()

                worker = threading.Thread(target=replay_worker,
                                          name=f"replay-{run_id}", daemon=True)
                replay_child = worker
                worker.start()
                try:
                    if not ready.wait(timeout=15) or replay_outcome.get("error_type"):
                        raise RuntimeError("replay_preflight_failed")
                    phase = cli._measure(seconds, "replay", session_id,
                                         api=self.api, stop=self._stop,
                                         checkpoint=checkpoint,
                                         database_url=run["_replay_database_url"],
                                         on_started=gate.set)
                finally:
                    gate.set()
                    measurement_done.set()
                    worker.join(timeout=15)
                    if worker.is_alive():
                        self._stop.set()
                        worker.join(timeout=15)
                replay_data = replay_outcome.get("result", {})
                raw_calls = phase.get("db_calls_raw") or {}
                calls = raw_calls.get("calls", [])
                selected_kinds = {item.kind for item in run["_replay_plan"].supported_calls}
                replay_data["observed_test_db_calls"] = [
                    {key: call.get(key) for key in (
                        "call_id", "writer_kind", "request_id", "rows_attempted",
                        "total_ms", "execute_ms", "commit_ms", "transactions",
                        "commits", "rollbacks", "sql_calls", "outcome",
                    )}
                    for call in calls
                    if call.get("database_name") == "kiwoom_monitor_diagnostic_test"
                    and call.get("writer_kind") in selected_kinds]
                writer_samples = {}
                market_saves = phase.get("market_bar_saves") or {}
                for sample_group in (market_saves.get("writer_transactions") or {}).values():
                    for sample in sample_group.get("call_samples", []):
                        if sample.get("db_call_id"):
                            writer_samples[sample["db_call_id"]] = sample
                bar_samples = {}
                for sample in (market_saves.get("kinds", {}).get("minute", {})
                               .get("call_samples", [])):
                    if sample.get("db_call_id"):
                        bar_samples[sample["db_call_id"]] = sample
                calls_by_request = {}
                for call in replay_data.get("observed_test_db_calls", []):
                    if call.get("request_id"):
                        calls_by_request.setdefault(call["request_id"], []).append(call)
                replay_calls = replay_data.get("replayed_calls", [])
                for replay_call in replay_calls:
                    matches = calls_by_request.get(replay_call.get("replay_call_id"), [])
                    if len(matches) != 1:
                        if replay_call.get("state") == "committed":
                            replay_call["state"] = "call_observation_missing"
                        replay_call["database_call"] = None
                        continue
                    call = matches[0]
                    db_call_id = call.get("call_id")
                    writer_sample = writer_samples.get(db_call_id, {})
                    bar_sample = bar_samples.get(db_call_id, {})
                    domain_counts = writer_sample.get("domain_counts", {})
                    required_shape = ({"bar_shape_version", "bar_changed_rows",
                                       "duplicate_input_keys", "revision_insert_rows"}
                                      if call.get("writer_kind") == "query_minute" else set())
                    missing_shape = sorted(required_shape - set(domain_counts))
                    replay_call["database_call"] = {
                        "db_call_id": db_call_id,
                        "writer_kind": call.get("writer_kind"),
                        "rows_attempted": call.get("rows_attempted"),
                        "elapsed_ms": call.get("total_ms"),
                        "execute_ms": call.get("execute_ms"),
                        "commit_ms": call.get("commit_ms"),
                        "transactions": call.get("transactions"),
                        "commits": call.get("commits"),
                        "rollbacks": call.get("rollbacks"),
                        "sql_calls": call.get("sql_calls"),
                        "outcome": call.get("outcome"),
                        "domain_counts": domain_counts,
                        "domain_metrics_state": "available" if writer_sample else "unavailable",
                        "missing_shape_fields": missing_shape,
                        "changed_rows": domain_counts.get("bar_changed_rows"),
                        "duplicate_input_keys": domain_counts.get("duplicate_input_keys"),
                        "revision_insert_rows": domain_counts.get("revision_insert_rows"),
                        "market_bar": ({key: bar_sample.get(key) for key in (
                            "rows", "observations", "metadata_suppressed_rows",
                            "revision_insert_rows", "total_ms", "commit_ms",
                        )} if bar_sample else None),
                    }
                    expected_shape = replay_call.get("expected_domain_counts")
                    if expected_shape is not None:
                        differences = {
                            key: {"expected": value, "actual": domain_counts.get(key)}
                            for key, value in expected_shape.items()
                            if type(domain_counts.get(key)) is not int or domain_counts[key] != value
                        }
                        for key, expected, actual in (
                            ("rows_attempted", replay_call["rows_attempted"], call.get("rows_attempted")),
                            ("writer_kind", replay_call["writer_kind"], call.get("writer_kind")),
                            ("outcome", "committed", call.get("outcome")),
                            ("commits", 1, call.get("commits")),
                        ):
                            if type(actual) is not type(expected) or actual != expected:
                                differences[key] = {"expected": expected, "actual": actual}
                        replay_call["shape_comparison"] = {
                            "state": "mismatched" if differences else "matched",
                            "differences": differences,
                        }
                linked = [item for item in replay_calls if item.get("database_call")]
                transactions = [item["database_call"].get("transactions") for item in linked]
                domain_totals = Counter()
                for item in linked:
                    domain_totals.update({key: int(value) for key, value in
                                          item["database_call"].get("domain_counts", {}).items()
                                          if type(value) is int})
                replay_data["direct_call_totals"] = {
                    "observed_calls": len(linked),
                    "rows_attempted": sum(int(item["database_call"].get("rows_attempted") or 0)
                                           for item in linked),
                    "transactions": sum(int(value or 0) for value in transactions),
                    "transactions_unavailable_calls": sum(value is None for value in transactions),
                    "commits": sum(int(item["database_call"].get("commits") or 0)
                                   for item in linked),
                    "rollbacks": sum(int(item["database_call"].get("rollbacks") or 0)
                                     for item in linked),
                    "elapsed_ms": round(sum(float(item["database_call"].get("elapsed_ms") or 0)
                                             for item in linked), 3),
                    "commit_ms": round(sum(float(item["database_call"].get("commit_ms") or 0)
                                            for item in linked), 3),
                    "domain_counts": dict(domain_totals),
                    "wal_bytes": None,
                    "scope_note": (
                        "counts and timings sum only DB calls linked by replay_call_id; "
                        "per-transaction WAL bytes are not exposed by PostgreSQL counters"
                    ),
                }
                replay_data["correlation"] = {
                    "selected_replay_calls": len(replay_calls),
                    "linked_db_calls": len(linked),
                    "missing_or_ambiguous_db_calls": len(replay_calls) - len(linked),
                    "calls_without_domain_metrics": sum(
                        item["database_call"].get("domain_metrics_state") != "available"
                        for item in linked),
                    "calls_with_incomplete_query_minute_shape": sum(
                        bool(item["database_call"].get("missing_shape_fields"))
                        for item in linked),
                    "request_id_field": "replay_call_id",
                }
                shaped_calls = [item for item in replay_calls if item.get("expected_domain_counts") is not None]
                if shaped_calls:
                    matched = sum(item.get("shape_comparison", {}).get("state") == "matched"
                                  for item in shaped_calls)
                    replay_data["recorded_shape_validation"] = {
                        "expected_calls": len(shaped_calls), "matched_calls": matched,
                        "mismatched_or_unobserved_calls": len(shaped_calls) - matched,
                    }
                    if matched != len(shaped_calls):
                        replay_data["error_type"] = "replay_query_minute_recorded_shape_mismatch"
                if replay_calls and len(linked) != sum(replay_data.get("completed_calls", {}).values()):
                    replay_data["error_type"] = "replay_call_correlation_mismatch"
                if worker.is_alive():
                    replay_data["error_type"] = "replay_worker_did_not_stop"
                if replay_outcome.get("error_type"):
                    replay_data["error_type"] = replay_outcome["error_type"]
                completed_calls = replay_data.get("completed_calls")
                if (isinstance(completed_calls, dict)
                        and (len(replay_data["observed_test_db_calls"]) !=
                             sum(completed_calls.values())
                             or raw_calls.get("dropped")
                             or raw_calls.get("raw_truncated"))):
                    replay_data["error_type"] = "replay_call_capture_mismatch"
                result = {"kind": "replay", "test_id": run_id, "phase": phase,
                          "replay": replay_data,
                          "state": ("complete" if phase.get("state") == "complete"
                                    and replay_data.get("state") == "complete"
                                    and not replay_data.get("error_type") else "aborted")}
            else:
                args = SimpleNamespace(command="measure" if run["kind"] == "measure" else "compare",
                                       label=run["label"], workload=run["workload"])
                result = cli._run_measurement(args, self.path, seconds, run_id,
                                              session_id, api=self.api, stop=self._stop,
                                              checkpoint=checkpoint)
            run["state"] = "finalizing"
            if result.get("kind") in {"measure", "replay"}:
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
            # A bounded replay join can expire while its native DB work continues.
            # Keep this run's capture/report/lease owned until that child really exits.
            if replay_child is not None and replay_child.is_alive():
                self._stop.set()
                replay_child.join()
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
                    report_path = self._results_dir() / f"{run_id}.json"
                    try:
                        report_available = (
                            not report_path.is_symlink()
                            and report_path.is_file()
                            and report_path.stat().st_size <= _MAX_REPORT_BYTES
                        )
                    except OSError:
                        report_available = False
                    if report_available:
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

    def close(self, timeout: float | None = 10.0) -> None:
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
        _write(target, {key: value for key, value in run.items()
                        if key != "result" and not key.startswith("_")})

    def _write_report(self, run: dict) -> None:
        report = {"schema": 2, **{key: deepcopy(value) for key, value in run.items()
                                   if not key.startswith("_")}}
        encoded = json.dumps(report, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > _MAX_REPORT_BYTES:
            result = report.get("result")
            if isinstance(result, dict):
                phases = ([result.get("phase", {})] if result.get("kind") in {"measure", "replay"}
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
