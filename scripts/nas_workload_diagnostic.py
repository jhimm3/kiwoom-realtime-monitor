"""Control optional NAS background workloads and measure PostgreSQL response.

Run inside the server container. The controls expire automatically and do not
touch ranking, realtime ticks, account state, orders, PostgreSQL durability, or
external historical import processes.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from uuid import uuid4

from kiwoom_monitor.central_server.diagnostic_workloads import (
    WORKLOADS, capture_status, control_path, control_snapshot,
    diagnostic_tool_status, evaluate_diagnostic_control, instance_id,
    paused_workloads, diagnostic_run_lock,
)


def _path() -> Path:
    path = control_path()
    if path is None:
        raise RuntimeError("KIWOOM_DIAGNOSTIC_WORKLOAD_PATH is not configured")
    return path


def _duration(value: str) -> int:
    text = str(value).strip().lower()
    if len(text) < 2 or text[-1] not in {"s", "m"} or not text[:-1].isdigit():
        raise ValueError("duration must use seconds or minutes, e.g. 30s or 5m")
    return int(text[:-1]) * (60 if text[-1] == "m" else 1)


from kiwoom_monitor.central_server.diagnostic_workloads import (
    _payload, _write, _control_lock, _set, _set_capture, _set_tool, _history,
)



def _api(path: str, query: dict[str, float] | None = None) -> dict:
    token = os.environ.get("MONITOR_SERVER_ACCESS_TOKEN", "")
    if not token:
        return {"unavailable": "missing access token"}
    port = os.environ.get("KIWOOM_SERVER_PORT", "8787")
    url = f"http://127.0.0.1:{port}{path}"
    if query:
        url += "?" + urlencode(query)
    try:
        request = Request(url, headers={"Authorization": f"Bearer {token}"})
        with urlopen(request, timeout=10) as response:
            return json.load(response)
    except (OSError, ValueError) as error:
        return {"unavailable": type(error).__name__}


from kiwoom_monitor.central_server.diagnostic_sampling import (
    _db_calls_report, _device_stats, _device_delta,
    _correlate_commit_device_samples, _host_usage, _uncontrolled_importers,
    _correlate_db_commit_activity,
    _snapshot, _pg_stat_io_snapshot, _checkpointer_snapshot,
    _counter_delta, _optional_io_snapshot, _wal_timing_status,
    _io_timing_status, _wal_timing_report, _storage_mapping,
    _log_position, _log_counts,
)



def _session_active(path: Path, session_id: str) -> bool:
    tool = diagnostic_tool_status(path)
    return bool(tool["enabled"] and tool["session_id"] == session_id)


def _aborted_phase(label: str, reason: str, *, samples: int = 0) -> dict:
    return {"label": label, "state": "aborted", "reason": reason,
            "samples": samples, "elapsed_seconds": 0}


def _measure(seconds: int, label: str, session_id: str, *, api=None,
             stop=None, checkpoint=None) -> dict:
    api = api or _api
    path = _path()
    if (stop is not None and stop.is_set()) or not _session_active(path, session_id):
        return _aborted_phase(label, "diagnostic_session_ended")
    import psycopg

    database_url = os.environ["KIWOOM_SERVER_DATABASE_URL"]
    log_path, offset = _log_position()
    device_before = _device_stats()
    host_before = _host_usage()
    runtime_before = api("/api/v1/diagnostics/workloads")
    writer_registry = api("/api/v1/diagnostics/writers")
    importers_before = _uncontrolled_importers()
    control_before = control_snapshot(path)
    db_probe_at = time.time()
    db_calls_before = api("/api/v1/diagnostics/db-calls",
                           {"start": db_probe_at - 1, "end": db_probe_at})
    wait_counts: collections.Counter[str] = collections.Counter()
    active_counts: collections.Counter[str] = collections.Counter()
    blocking_counts: collections.Counter[str] = collections.Counter()
    max_query_ms: dict[str, int] = {}
    activity_rows_truncated = False
    commit_activity_samples: list[dict] = []
    commit_activity_samples_dropped = 0
    started = time.time()
    started_mono = time.monotonic()
    started_iso = datetime.now(UTC).isoformat()
    first_device_stats = _device_stats()
    device_samples = [{"at": time.time(), "stats": first_device_stats}]
    next_pg_sample = started_mono
    next_device_sample = started_mono + 0.25
    measure_deadline = started_mono + seconds
    aborted_reason = None
    samples = 0
    if (stop is not None and stop.is_set()) or not _session_active(path, session_id):
        return _aborted_phase(label, "diagnostic_session_ended")
    try:
        with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET default_transaction_read_only=on")
                cursor.execute("SET statement_timeout TO '2000ms'")
                wal_timing = _wal_timing_status(cursor)
                io_timing = _io_timing_status(cursor)
                before = _snapshot(cursor)
                before_pg_stat_io = _optional_io_snapshot(cursor, _pg_stat_io_snapshot)
                before_checkpointer = _optional_io_snapshot(cursor, _checkpointer_snapshot)
                while time.monotonic() < measure_deadline:
                    if stop is not None and stop.is_set():
                        aborted_reason = "cancel_requested"
                        break
                    if not _session_active(path, session_id):
                        aborted_reason = "diagnostic_session_ended"
                        break
                    now_mono = time.monotonic()
                    if now_mono >= next_pg_sample and next_pg_sample < measure_deadline:
                        activity_probe_started = time.time()
                        cursor.execute(
                            "SELECT pid,backend_start,state,COALESCE(wait_event_type,''),"
                            "COALESCE(wait_event,''),"
                            "EXTRACT(EPOCH FROM clock_timestamp()-query_start)*1000,"
                            "pg_blocking_pids(pid),query FROM pg_stat_activity "
                            "WHERE datname=current_database() AND pid<>pg_backend_pid() "
                            "ORDER BY query_start NULLS LAST LIMIT 201"
                        )
                        activity_rows = cursor.fetchall()
                        activity_probe_finished = time.time()
                        activity_rows_truncated |= len(activity_rows) > 200
                        for pid, backend_start, state, wait_type, wait_event, query_age, blockers, query in activity_rows[:200]:
                            if state != "active":
                                continue
                            wait_counts[f"{wait_type or 'CPU'}:{wait_event or '-'}"] += 1
                            statement = (query or "").lower()
                            if hasattr(backend_start, "timestamp"):
                                if len(commit_activity_samples) < 8192:
                                    commit_activity_samples.append({
                                        "pid": pid, "backend_started_at": backend_start.timestamp(),
                                        "started_at": activity_probe_started,
                                        "finished_at": activity_probe_finished,
                                        "wait_type": wait_type, "wait_event": wait_event,
                                        "blocking_pids": list(blockers or ()),
                                        "statement_type": ("COMMIT" if statement.strip().rstrip(";")
                                                           in {"commit", "end"} else "OTHER"),
                                    })
                                else:
                                    commit_activity_samples_dropped += 1
                            category = "minute_bars" if "central_minute_bars" in statement else (
                                "news" if "central_news_" in statement else "other"
                            )
                            active_counts[category] += 1
                            if blockers:
                                blocking_counts[category] += 1
                            max_query_ms[category] = max(max_query_ms.get(category, 0),
                                                         int(query_age or 0))
                        samples += 1
                        if checkpoint is not None:
                            checkpoint(started, time.time())
                        next_pg_sample = time.monotonic() + 1.0
                    if now_mono >= next_device_sample and next_device_sample < measure_deadline:
                        current_stats = _device_stats()
                        device_samples.append({"at": time.time(), "stats": current_stats})
                        next_device_sample = time.monotonic() + 0.25
                    if not _session_active(path, session_id):
                        aborted_reason = "diagnostic_session_ended"
                        break
                    next_tick = min(next_pg_sample, next_device_sample, measure_deadline)
                    delay = max(0.005, min(0.05, next_tick - time.monotonic()))
                    if stop is None:
                        time.sleep(delay)
                    else:
                        stop.wait(delay)
                if stop is not None and stop.is_set():
                    aborted_reason = "cancel_requested"
                elif not _session_active(path, session_id):
                    aborted_reason = "diagnostic_session_ended"
                if not aborted_reason:
                    after = _snapshot(cursor)
                    after_pg_stat_io = _optional_io_snapshot(cursor, _pg_stat_io_snapshot)
                    after_checkpointer = _optional_io_snapshot(cursor, _checkpointer_snapshot)
    except psycopg.Error as error:
        return {"label": label, "state": "aborted", "reason": "database_probe_failed",
                "error_type": type(error).__name__, "samples": samples,
                "started_at_utc": started_iso,
                "elapsed_seconds": round(time.time() - started, 3)}
    elapsed = time.time() - started
    ended = time.time()
    device_after = _device_stats()
    device_samples.append({"at": time.time(), "stats": device_after})
    if aborted_reason:
        return {"label": label, "state": "aborted", "reason": aborted_reason,
                "started_at_utc": started_iso, "elapsed_seconds": round(elapsed, 3),
                "samples": samples, "wait_samples": dict(wait_counts),
                "active_query_samples": dict(active_counts),
                "blocking_samples": dict(blocking_counts),
                "max_query_ms": max_query_ms,
                "activity_rows_truncated": activity_rows_truncated}
    reset_before = before.get("stats_reset", {})
    reset_after = after.get("stats_reset", {})
    resets = {scope: reset_before.get(scope) != reset_after.get(scope)
              for scope in ("wal", "database")}
    wal_keys = {"wal_records", "wal_fpi", "wal_bytes", "wal_buffers_full",
                "wal_write", "wal_sync", "wal_write_time_ms", "wal_sync_time_ms"}
    delta = {}
    for key, old in before.items():
        if key == "stats_reset":
            continue
        new = after[key]
        delta[key] = (None if new < old or resets["wal" if key in wal_keys else "database"]
                      else round(new - old, 3))
    pg_stat_io_delta = _counter_delta(before_pg_stat_io, after_pg_stat_io)
    checkpointer_delta = _counter_delta(before_checkpointer, after_checkpointer)
    runtime_after = api("/api/v1/diagnostics/workloads")
    market_bar_saves = api("/api/v1/diagnostics/market-bar-saves",
                            {"start": started, "end": ended})
    db_calls_after = api("/api/v1/diagnostics/db-calls",
                          {"start": started, "end": ended})
    db_calls_raw = (api("/api/v1/diagnostics/db-calls",
                        {"start": started, "end": ended, "mode": "raw", "limit": 50_000})
                    if api is not _api else None)
    db_calls = _db_calls_report(db_calls_before, db_calls_after, session_id,
                                control_before, control_snapshot(path))
    db_commit_activity = _correlate_db_commit_activity(
        db_calls_raw, commit_activity_samples,
        dropped_samples=commit_activity_samples_dropped,
        rows_truncated=activity_rows_truncated,
    )
    if db_calls.get("state") != "complete" and db_commit_activity["state"] != "unavailable":
        db_commit_activity["state"] = "incomplete"
        db_commit_activity["reason"] = db_calls.get("reason", "db_call_window_incomplete")
    market_bar_saves = _correlate_commit_device_samples(market_bar_saves, device_samples)
    wal_timing = (_wal_timing_report(wal_timing, delta, market_bar_saves)
                  if delta.get("wal_write_time_ms") is not None and
                  delta.get("wal_sync_time_ms") is not None else
                  {"time_values_available": False, "reason": "stats_reset_or_counter_decreased"})
    return {
        "label": label, "state": "aborted" if aborted_reason else "complete",
        "reason": aborted_reason, "samples": samples,
        "started_at_utc": started_iso,
        "elapsed_seconds": round(elapsed, 3),
        "paused_at_end": sorted(paused_workloads(path=path)),
        "runtime_before": runtime_before,
        "runtime_after": runtime_after,
        "writer_registry": writer_registry,
        "uncontrolled_importers_before": importers_before,
        "uncontrolled_importers_after": _uncontrolled_importers(),
        "database_delta": delta,
        "statistics_reset": resets,
        "pg_stat_io_delta": pg_stat_io_delta,
        "checkpointer_delta": checkpointer_delta,
        "wal_timing": wal_timing,
        "io_timing": io_timing,
        "storage_mapping": _storage_mapping(),
        "wal_bytes_per_second": (round(delta["wal_bytes"] / elapsed, 2)
                                 if elapsed > 0 and delta.get("wal_bytes") is not None
                                 else None),
        "wait_samples": dict(wait_counts), "active_query_samples": dict(active_counts),
        "blocking_samples": dict(blocking_counts), "max_query_ms": max_query_ms,
        "activity_rows_truncated": activity_rows_truncated,
        "log_counts": _log_counts(log_path, offset),
        "market_bar_saves": market_bar_saves,
        "db_calls": db_calls,
        "db_commit_activity": db_commit_activity,
        "db_calls_raw": db_calls_raw,
        "storage_devices": _device_delta(device_before, device_after, elapsed),
        "host_before": host_before, "host_after": _host_usage(),
    }


def _external_market_activity(phase: dict) -> dict[str, object]:
    def runtime(label: str) -> dict:
        workloads = phase.get(label, {}).get("workloads", {})
        item = workloads.get("external_market", {}) if isinstance(workloads, dict) else {}
        value = item.get("runtime", {}) if isinstance(item, dict) else {}
        return value if isinstance(value, dict) else {}

    before = runtime("runtime_before")
    after = runtime("runtime_after")
    try:
        attempts = max(0, int(after.get("collection_attempts", 0))
                      - int(before.get("collection_attempts", 0)))
    except (TypeError, ValueError):
        attempts = 0
    try:
        completions = max(0, int(after.get("collection_completions", 0))
                          - int(before.get("collection_completions", 0)))
    except (TypeError, ValueError):
        completions = 0
    try:
        saved_rows = max(0, int(after.get("collection_saved_rows_total", 0))
                         - int(before.get("collection_saved_rows_total", 0)))
    except (TypeError, ValueError):
        saved_rows = 0
    return {
        "observed": completions > 0,
        "collection_attempts": attempts,
        "collection_completions": completions,
        "last_collection_completed_at": after.get("last_collection_completed_at"),
        "saved_rows_delta": saved_rows,
        "last_collection_error": after.get("last_collection_error") if attempts else None,
    }


def _run_measurement(args, path: Path, seconds: int, test_id: str,
                     session_id: str, *, api=None, stop=None, checkpoint=None) -> dict:
    api = api or _api
    if args.command == "measure":
        phase = _measure(seconds, args.label, session_id, api=api, stop=stop,
                         checkpoint=checkpoint)
        return {"kind": "measure", "test_id": test_id,
                "state": phase["state"], "phase": phase}

    # A/B/A ordering helps distinguish workload effect from time trend.
    if args.workload in paused_workloads(path=path):
        raise ValueError("comparison target is already paused; resume it first")
    configured = api("/api/v1/diagnostics/workloads").get("workloads", {}).get(args.workload, {})
    if not configured.get("configured"):
        raise ValueError("target is disabled or live runtime status is unavailable")
    external_runtime = configured.get("runtime", {}) if args.workload == "external_market" else {}
    if args.workload == "external_market":
        if not external_runtime.get("operational_enabled") or not external_runtime.get("running"):
            raise ValueError("external_market collector is not operationally running; check its NAS setting")
        poll_seconds = int(external_runtime.get("poll_seconds") or 0)
        if poll_seconds and seconds <= poll_seconds:
            raise ValueError(
                f"duration must exceed external_market poll_seconds ({poll_seconds}s); "
                "use a longer phase or lower its configured polling interval"
            )
    phases = []
    lease = min(3600, seconds * 3 + 120)
    test_owner = f"test:{test_id}"
    paused_by_test = False
    try:
        phases.append(_measure(seconds, "baseline_on", session_id, api=api, stop=stop,
                               checkpoint=checkpoint))
        if phases[-1]["state"] == "aborted":
            return {"kind": "compare", "test_id": test_id, "workload": args.workload,
                    "state": "aborted", "phases": phases}
        if not _session_active(path, session_id):
            return {"kind": "compare", "test_id": test_id, "workload": args.workload,
                    "state": "aborted", "phases": phases}
        _set(path, args.workload, True, lease, owner=test_owner,
             expected_session=session_id)
        if not _session_active(path, session_id):
            return {"kind": "compare", "test_id": test_id, "workload": args.workload,
                    "state": "aborted", "phases": phases}
        paused_by_test = True
        _history("test_pause", workload=args.workload, detail={"test_id": test_id})
        (stop.wait(2) if stop is not None else time.sleep(2))  # settle, not drain ACK
        phases.append(_measure(seconds, "paused", session_id, api=api, stop=stop,
                               checkpoint=checkpoint))
        if phases[-1]["state"] == "aborted":
            return {"kind": "compare", "test_id": test_id, "workload": args.workload,
                    "state": "aborted", "phases": phases}
        _set(path, args.workload, False, lease, owner=test_owner,
             expected_session=session_id)
        paused_by_test = False
        _history("test_resume", workload=args.workload, detail={"test_id": test_id})
        (stop.wait(2) if stop is not None else time.sleep(2))
        phases.append(_measure(seconds, "resumed_on", session_id, api=api, stop=stop,
                               checkpoint=checkpoint))
        if args.workload == "external_market":
            for phase in phases:
                phase["target_activity"] = _external_market_activity(phase)
            on_activity = [phases[0]["target_activity"]["observed"],
                           phases[2]["target_activity"]["observed"]]
            paused_activity = phases[1]["target_activity"]["observed"]
            valid = all(on_activity) and not paused_activity
            report_conclusion = ("target_activity_observed; inspect measured deltas before attribution"
                                 if valid else "inconclusive_target_activity_not_confirmed")
        else:
            valid = None
            report_conclusion = "measurement_only; inspect activity and repeat before attribution"
    finally:
        if paused_by_test:
            if _session_active(path, session_id):
                _set(path, args.workload, False, lease, owner=test_owner,
                     expected_session=session_id)
                _history("test_restore", workload=args.workload,
                         detail={"test_id": test_id})
    report = {"kind": "compare", "test_id": test_id,
            "workload": args.workload, "phases": phases,
            "state": "aborted" if phases[-1]["state"] == "aborted" else "complete",
            "conclusion": report_conclusion,
            "limitations": [
                "An in-flight task can finish after its pause; check log counts before attribution.",
                "A phase without workload activity cannot establish that workload's effect.",
                "External importers, backup, and host I/O are not controlled by this command.",
            ]}
    if args.workload == "external_market":
        report["valid_for_attribution"] = valid
    return report


def _save(report: dict) -> Path:
    target = _path().parent / "diagnostic-results"
    target.mkdir(parents=True, exist_ok=True)
    filename = report["test_id"] + ".json"
    path = target / filename
    _write(path, report)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("reset")
    tool = commands.add_parser("tool", help="master switch for every diagnostic control")
    tool.add_argument("action", choices=("on", "off", "status"))
    tool.add_argument("--ttl", default="10m")
    capture = commands.add_parser("capture", help="enable or disable bounded metrics capture")
    capture.add_argument("action", choices=("on", "off", "status"))
    capture.add_argument("--ttl", default="10m")
    report_command = commands.add_parser("report")
    report_command.add_argument("test_id")
    for command in ("pause", "resume"):
        sub = commands.add_parser(command)
        sub.add_argument("workload", choices=sorted(WORKLOADS))
        if command == "pause":
            sub.add_argument("--ttl", default="10m")
    measure = commands.add_parser("measure")
    measure.add_argument("--seconds", type=int, default=30)
    measure.add_argument("--label", default="manual")
    compare = commands.add_parser("test", aliases=["compare"])
    compare.add_argument("workload", choices=sorted(WORKLOADS))
    compare.add_argument("--duration", default="30s")
    args = parser.parse_args()
    path = _path()
    if args.command == "status":
        runtime = _api("/api/v1/diagnostics/workloads")
        print(json.dumps({"diagnostic_tool": runtime.get("diagnostic_tool", diagnostic_tool_status(path)),
                          "workloads": runtime.get("workloads", sorted(WORKLOADS)),
                          "paused": sorted(paused_workloads(path=path)),
                          "expires_at": runtime.get("expires_at"),
                          "metrics_capture": runtime.get("metrics_capture", capture_status(path)),
                          "uncontrolled_importers": _uncontrolled_importers(),
                          "writer_registry": _api("/api/v1/diagnostics/writers"),
                          "file": str(path)}, ensure_ascii=False))
        return
    if args.command == "tool":
        if args.action == "status":
            result = diagnostic_tool_status(path)
        else:
            try:
                ttl = _duration(args.ttl) if args.action == "on" else 600
            except ValueError as error:
                parser.error(str(error))
            if args.action == "on" and not 60 <= ttl <= 3600:
                parser.error("--ttl must be 1m..60m")
            previous = _payload(path)
            updated = _set_tool(path, args.action == "on", ttl)
            result = diagnostic_tool_status(path)
            action = f"tool_{args.action}"
            _history(action if updated != previous else f"{action}_noop", detail=result)
            _api("/api/v1/diagnostics/workloads")
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.command == "capture":
        if args.action == "status":
            result = capture_status(path)
            _api("/api/v1/diagnostics/workloads")
        else:
            try:
                ttl = _duration(args.ttl) if args.action == "on" else 600
            except ValueError as error:
                parser.error(str(error))
            if not 60 <= ttl <= 3600:
                parser.error("--ttl must be 1m..60m")
            try:
                value = _set_capture(path, args.action == "on", ttl)
            except ValueError as error:
                parser.error(str(error))
            result = capture_status(path)
            _history(f"capture_{args.action}", detail=result)
            _api("/api/v1/diagnostics/workloads")  # refresh and clear buffers on OFF
        print(json.dumps(result, ensure_ascii=False))
        return
    if args.command == "report":
        if not args.test_id.replace("-", "").isalnum():
            parser.error("invalid test id")
        report_path = path.parent / "diagnostic-results" / f"{args.test_id}.json"
        print(report_path.read_text(encoding="utf-8"))
        return
    if args.command == "reset":
        with _control_lock(path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        _history("reset")
        print(json.dumps({"paused": [], "restored": True}))
        return
    if args.command in {"pause", "resume"}:
        try:
            ttl = _duration(args.ttl) if args.command == "pause" else 600
        except ValueError as error:
            parser.error(str(error))
        if not 60 <= ttl <= 3600:
            parser.error("--ttl must be 1m..60m")
        try:
            value = _set(path, args.workload, args.command == "pause", ttl)
        except ValueError as error:
            parser.error(str(error))
        _history(args.command, workload=args.workload,
                 detail={"expires_at": value["expires_at"]})
        print(json.dumps(value, ensure_ascii=False))
        return
    try:
        seconds = args.seconds if args.command == "measure" else _duration(args.duration)
    except ValueError as error:
        parser.error(str(error))
    maximum_seconds = 300 if args.command == "measure" else 1100
    if not 5 <= seconds <= maximum_seconds:
        parser.error(f"measurement duration must be 5s..{maximum_seconds}s")
    with diagnostic_run_lock(path):
        tool = diagnostic_tool_status(path)
        if not tool["enabled"]:
            parser.error("diagnostic tool is OFF; run 'tool on --ttl 10m' first")
        session_id = tool["session_id"]
        if not isinstance(session_id, str) or not session_id:
            parser.error("legacy diagnostic session; run 'tool off', then 'tool on'")
        required_seconds = seconds + 30 if args.command == "measure" else seconds * 3 + 34
        if float(tool["expires_at"]) - time.time() < required_seconds:
            parser.error(f"master TTL is too short; at least {required_seconds}s must remain")
        test_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        capture_was_enabled = bool(capture_status(path).get("enabled"))
        if capture_was_enabled and float(capture_status(path)["expires_at"]) - time.time() < required_seconds:
            parser.error(f"capture TTL is too short; at least {required_seconds}s must remain")
        capture_owner = f"run:{test_id}"
        capture_lease = seconds + 60 if args.command == "measure" else seconds * 3 + 120
        if not capture_was_enabled:
            _set_capture(path, True, capture_lease, owner=capture_owner,
                         expected_session=session_id)
            _history("capture_auto_on", detail={"test_id": test_id,
                                                 "expires_in_seconds": capture_lease})
            _api("/api/v1/diagnostics/workloads")
        try:
            report = _run_measurement(args, path, seconds, test_id, session_id)
        except ValueError as error:
            parser.error(str(error))
        finally:
            if not capture_was_enabled:
                if _session_active(path, session_id):
                    _set_capture(path, False, capture_lease, owner=capture_owner,
                                 expected_session=session_id)
                    _history("capture_auto_off", detail={"test_id": test_id})
                _api("/api/v1/diagnostics/workloads")
        saved = _save(report)
        _history("report_saved", workload=getattr(args, "workload", ""),
                 detail={"test_id": test_id, "path": str(saved)})
        print(json.dumps({"result": report, "saved": str(saved)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
