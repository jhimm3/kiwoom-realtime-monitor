"""Control optional NAS background workloads and measure PostgreSQL response.

Run inside the server container. The controls expire automatically and do not
touch ranking, realtime ticks, account state, orders, PostgreSQL durability, or
external historical import processes.
"""

from __future__ import annotations

import argparse
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


from kiwoom_monitor.central_server import diagnostic_sampling as _sampling
from kiwoom_monitor.central_server.diagnostic_sampling import (
    _session_active, _aborted_phase, _external_market_activity,
    _db_calls_report, _device_stats, _device_delta,
    _correlate_commit_device_samples, _host_usage, _uncontrolled_importers,
    _correlate_db_commit_activity,
    _snapshot, _pg_stat_io_snapshot, _checkpointer_snapshot,
    _counter_delta, _optional_io_snapshot, _wal_timing_status,
    _io_timing_status, _wal_timing_report, _storage_mapping,
    _log_position, _log_counts,
)



def _measure(seconds: int, label: str, session_id: str, *, api=None,
             stop=None, checkpoint=None, database_url=None,
             on_started=None) -> dict:
    # The default CLI transport exposes summaries; the in-process API can
    # provide correlated raw calls. Keep that explicit at this adapter.
    api = api or _api
    return _sampling.measure(
        seconds, label, session_id, path=_path(), api=api, stop=stop,
        checkpoint=checkpoint, database_url=database_url, on_started=on_started,
        include_raw_calls=api is not _api,
    )


def _run_measurement(args, path: Path, seconds: int, test_id: str,
                     session_id: str, *, api=None, stop=None, checkpoint=None) -> dict:
    api = api or _api
    return _sampling.run_measurement(
        args, path, seconds, test_id, session_id, api=api, stop=stop,
        checkpoint=checkpoint, include_raw_calls=api is not _api,
    )


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
