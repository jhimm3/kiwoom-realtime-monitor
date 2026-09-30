"""Bounded, read-only host correlation for the first slow minute-bar COMMIT.

Run on the NAS host through SSH stdin. It owns and closes only its diagnostic
capture session. It never pauses a workload or changes PostgreSQL settings.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from collections import Counter, deque
from pathlib import Path


values = {}
for line in Path("/volume1/docker/kiwoom-monitor/deploy/synology/.env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"\'')

base = "http://127.0.0.1:" + values.get("KIWOOM_MONITOR_PORT", "8787")
token = values["MONITOR_SERVER_ACCESS_TOKEN"]
pg_env = os.environ.copy()
pg_env["PGPASSWORD"] = values["POSTGRES_PASSWORD"]
pg_env["PGOPTIONS"] = "-c default_transaction_read_only=on -c statement_timeout=2000"
query = (
    "SELECT backend_type,pid,COALESCE(wait_event_type,''),COALESCE(wait_event,''),"
    "substring(pg_read_file('/proc/'||pid||'/io') FROM "
    "'write_bytes:[[:space:]]*([0-9]+)') AS write_bytes,"
    "substring(pg_read_file('/proc/'||pid||'/io') FROM "
    "'read_bytes:[[:space:]]*([0-9]+)') AS read_bytes "
    "FROM pg_stat_activity WHERE pid IS NOT NULL AND pid<>pg_backend_pid()"
)
pg_command = ["psql", "-X", "-A", "-t", "-F", "|", "-h", "172.18.0.2", "-p", "5432",
              "-U", values["POSTGRES_USER"], "-d", values["POSTGRES_DB"], "-c", query]


def api(path, *, method="GET", body=None):
    request = urllib.request.Request(
        base + path, data=json.dumps(body).encode() if body is not None else None,
        method=method, headers={"Authorization": "Bearer " + token,
                                "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def host_sample():
    at = time.time()
    devices = {}
    for line in Path("/proc/diskstats").read_text().splitlines():
        parts = line.split()
        if parts[2] in {"dm-4", "md4", "md2"}:
            fields = [int(part) for part in parts[3:]]
            devices[parts[2]] = {"writes": fields[4], "write_sectors": fields[6],
                                 "write_ms": fields[7],
                                 "busy_ms": fields[9], "weighted_ms": fields[10]}
    result = subprocess.run(pg_command, env=pg_env, capture_output=True, text=True, timeout=3)
    waits = {}
    processes = {}
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            parts = line.split("|")
            if len(parts) == 6:
                backend, pid, wait_type, wait_event, write_bytes, read_bytes = parts
                key = ":".join((backend, wait_type, wait_event))
                waits[key] = waits.get(key, 0) + 1
                processes[pid] = {"backend_type": backend,
                                  "write_bytes": int(write_bytes or 0),
                                  "read_bytes": int(read_bytes or 0)}
    return {"at": at, "devices": devices, "waits": waits,
            "processes": processes,
            "pg_sample_ok": result.returncode == 0}


def device_window(samples, start, end):
    earlier = [row for row in samples if row["at"] <= start]
    later = [row for row in samples if row["at"] >= end]
    if not earlier or not later:
        return {"available": False, "reason": "commit_window_not_bracketed"}
    before = earlier[-1]
    after = later[0]
    elapsed_ms = (after["at"] - before["at"]) * 1000
    if elapsed_ms <= 0:
        return {"sample_interval_ms": round(elapsed_ms, 1), "available": False}
    result = {"sample_interval_ms": round(elapsed_ms, 1)}
    for name in ("md4", "md2", "dm-4"):
        if name not in before["devices"] or name not in after["devices"]:
            continue
        a, b = before["devices"][name], after["devices"][name]
        writes = b["writes"] - a["writes"]
        result[name] = {"writes": writes,
                        "write_bytes": (b["write_sectors"] - a["write_sectors"]) * 512,
                        "busy_percent": round(100 * (b["busy_ms"] - a["busy_ms"]) / elapsed_ms, 2),
                        "average_queue": round((b["weighted_ms"] - a["weighted_ms"]) / elapsed_ms, 2),
                        "write_await_ms": round((b["write_ms"] - a["write_ms"]) / writes, 2)
                        if writes > 0 else None}
    return result


def process_io_window(samples, start, end):
    earlier = [row for row in samples if row["at"] <= start]
    later = [row for row in samples if row["at"] >= end]
    if not earlier or not later:
        return {"available": False, "reason": "commit_window_not_bracketed"}
    window = [row for row in samples if earlier[-1]["at"] <= row["at"] <= later[0]["at"]]
    measurements = {}
    for sample in window:
        for pid, counters in sample["processes"].items():
            measurements.setdefault(pid, []).append((sample["at"], counters))
    deltas = []
    for pid, points in measurements.items():
        if len(points) < 2:
            continue
        old, new = points[0][1], points[-1][1]
        if old["backend_type"] != new["backend_type"]:
            continue
        writes = new["write_bytes"] - old["write_bytes"]
        reads = new["read_bytes"] - old["read_bytes"]
        if writes >= 0 and reads >= 0:
            deltas.append({"pid": int(pid), "backend_type": old["backend_type"],
                           "sampled_ms": round((points[-1][0] - points[0][0]) * 1000, 1),
                           "write_bytes": writes, "read_bytes": reads})
    grouped = {}
    for row in deltas:
        name = row["backend_type"]
        group = grouped.setdefault(name, {"processes": 0, "write_bytes": 0, "read_bytes": 0})
        group["processes"] += 1
        group["write_bytes"] += row["write_bytes"]
        group["read_bytes"] += row["read_bytes"]
    return {"available": True, "sample_count": len(window),
            "matched_processes": len(deltas),
            "single_sample_processes": sum(len(points) == 1 for points in measurements.values()),
            "by_backend_type": grouped,
            "top_writers": sorted(deltas, key=lambda row: row["write_bytes"], reverse=True)[:8]}


preflight = host_sample()
if not preflight["pg_sample_ok"] or not preflight["processes"]:
    raise RuntimeError("PostgreSQL process I/O preflight failed")
state = api("/api/v1/diagnostics/workloads")
if state["diagnostic_tool"]["enabled"] or state["metrics_capture"]["enabled"]:
    raise RuntimeError("another diagnostic session is active")
if any(item.get("paused_by_diagnostic") for item in state["workloads"].values()):
    raise RuntimeError("a diagnostic workload is paused")
enabled = api("/api/v1/diagnostics/control", method="PUT", body={
    "target": "master", "enabled": True, "ttl_seconds": 240,
    "expected_revision": state["control_revision"],
})
session = enabled["diagnostic_tool"]["session_id"]
samples = deque(maxlen=400)
seen = set()
minute_calls = 0
max_minute_commit_ms = 0.0
trigger = None
nearby_calls = []
raw_truncated = False
sample_errors = 0
try:
    capture = api("/api/v1/diagnostics/control", method="PUT", body={
        "target": "capture", "enabled": True, "ttl_seconds": 210,
        "expected_revision": enabled["control_revision"], "expected_session": session,
    })
    print(json.dumps({"capture_started": True, "session_id": session}), flush=True)
    deadline = time.monotonic() + 60
    stop_after = None
    while time.monotonic() < deadline:
        sample = host_sample()
        samples.append(sample)
        sample_errors += not sample["pg_sample_ok"]
        end = time.time() + 0.01
        query_string = urllib.parse.urlencode({"start": end - 8, "end": end,
                                               "mode": "raw", "limit": 500})
        db = api("/api/v1/diagnostics/db-calls?" + query_string)
        raw_truncated |= bool(db.get("raw_truncated") or db.get("truncated") or db.get("dropped"))
        for call in db.get("calls", []):
            call_id = call.get("call_id")
            if call_id in seen:
                continue
            seen.add(call_id)
            if call.get("writer_kind") != "query_minute" or call.get("access_mode") != "write":
                continue
            minute_calls += 1
            commit_ms = float(call.get("commit_ms") or 0)
            max_minute_commit_ms = max(max_minute_commit_ms, commit_ms)
            if trigger is None and commit_ms >= 1000:
                trigger = call
                nearby_calls = db.get("calls", [])
                stop_after = time.monotonic() + 3
                print(json.dumps({"triggered": True, "minute_commit_ms": commit_ms}), flush=True)
        if stop_after is not None and time.monotonic() >= stop_after:
            break
        time.sleep(0.15)

    result = {"triggered": trigger is not None, "minute_calls": minute_calls,
              "max_minute_commit_ms": round(max_minute_commit_ms, 3),
              "host_samples": len(samples), "pg_sample_errors": sample_errors,
              "raw_truncated_or_dropped": raw_truncated}
    if trigger is not None:
        start, end = trigger["commit_started_at"], trigger["commit_finished_at"]
        overlap = [row for row in samples if start <= row["at"] <= end]
        waits = Counter(key for row in overlap for key, count in row["waits"].items()
                        for _ in range(count) if key.split(":")[0] in
                        {"walwriter", "checkpointer", "background writer"}
                        and key.split(":")[1] not in {"", "Activity", "Timeout"})
        peers = [row for row in nearby_calls if row.get("call_id") != trigger["call_id"]
                 and row.get("commit_started_at") is not None
                 and row.get("commit_finished_at") is not None
                 and row["commit_started_at"] < end and row["commit_finished_at"] > start]
        bar_query = urllib.parse.urlencode({"start": start - 2, "end": end + 3})
        bar_saves = api("/api/v1/diagnostics/market-bar-saves?" + bar_query)
        matching_bars = [row for row in bar_saves.get("kinds", {}).get("minute", {}).get("call_samples", [])
                         if row.get("db_call_id") == trigger["call_id"]]
        diag = matching_bars[0].get("commit_diagnostics", {}) if matching_bars else {}
        backend_waits = Counter(
            f"{row.get('wait_type') or 'NONE'}:{row.get('wait_event') or 'NONE'}"
            for row in diag.get("samples", [])
        )
        result["slow_commit"] = {"call_id": trigger["call_id"],
                                 "backend_pid": trigger.get("backend_pid"),
                                 "started_at": start, "finished_at": end,
                                 "commit_ms": trigger["commit_ms"],
                                 "execute_ms": trigger.get("execute_ms"),
                                 "host_samples_during_commit": len(overlap),
                                 "background_nonidle_waits": dict(waits),
                                 "same_backend_waits": dict(backend_waits),
                                 "same_backend_probe_errors": diag.get("probe_errors"),
                                 "bar_diagnostic_matched": bool(matching_bars),
                                 "bar_capture_truncated": bar_saves.get("truncated"),
                                 "devices": device_window(list(samples), start, end),
                                 "postgres_process_io": process_io_window(list(samples), start, end),
                                 "peer_commits_by_kind": dict(Counter(
                                     f"{row.get('writer_family')}/{row.get('writer_kind')}" for row in peers)),
                                 "peer_commit_count": len(peers)}
    print(json.dumps(result, ensure_ascii=False), flush=True)
finally:
    current = api("/api/v1/diagnostics/workloads")
    if current["diagnostic_tool"].get("session_id") == session:
        api("/api/v1/diagnostics/control", method="PUT", body={
            "target": "master", "enabled": False, "ttl_seconds": 240,
            "expected_revision": current["control_revision"], "expected_session": session,
        })
    final = api("/api/v1/diagnostics/workloads")
    print(json.dumps({"cleanup": {"master_enabled": final["diagnostic_tool"]["enabled"],
                                  "capture_enabled": final["metrics_capture"]["enabled"],
                                  "paused": [key for key, item in final["workloads"].items()
                                             if item.get("paused_by_diagnostic")]}}), flush=True)
