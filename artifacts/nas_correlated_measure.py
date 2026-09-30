"""One owned, read-only NAS measure run with concurrent PostgreSQL background sampling."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from collections import Counter
from pathlib import Path


env = {}
for raw in Path("/volume1/docker/kiwoom-monitor/deploy/synology/.env").read_text().splitlines():
    raw = raw.strip()
    if raw and not raw.startswith("#") and "=" in raw:
        key, value = raw.split("=", 1)
        env[key.strip()] = value.strip().strip('"\'')
token = env["MONITOR_SERVER_ACCESS_TOKEN"]
base = "http://127.0.0.1:" + env.get("KIWOOM_MONITOR_PORT", "8787")
pg_env = os.environ.copy()
pg_env["PGPASSWORD"] = env["POSTGRES_PASSWORD"]
pg_env["PGOPTIONS"] = "-c default_transaction_read_only=on -c statement_timeout=2000"
pg_query = (
    "SELECT backend_type,COALESCE(wait_event_type,''),COALESCE(wait_event,''),count(*) "
    "FROM pg_stat_activity WHERE backend_type IN "
    "('walwriter','checkpointer','background writer') GROUP BY 1,2,3 ORDER BY 1,2,3"
)
pg_command = ["psql", "-X", "-A", "-t", "-F", "|", "-h", "172.18.0.2", "-p", "5432",
              "-U", env["POSTGRES_USER"], "-d", env["POSTGRES_DB"], "-c", pg_query]


def api(path: str, *, method: str = "GET", body: dict | None = None) -> dict:
    request = urllib.request.Request(
        base + path, data=json.dumps(body).encode() if body is not None else None,
        method=method, headers={"Authorization": "Bearer " + token,
                                "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def background_sample() -> dict:
    at = time.time()
    result = subprocess.run(pg_command, env=pg_env, capture_output=True, text=True, timeout=3)
    if result.returncode:
        return {"at": at, "error": "psql_connection_failed"}
    waits = {}
    for line in result.stdout.splitlines():
        parts = line.split("|")
        if len(parts) == 4:
            key = ":".join(parts[:3])
            waits[key] = int(parts[3])
    return {"at": at, "waits": waits}


state = api("/api/v1/diagnostics/workloads")
if state["diagnostic_tool"]["enabled"]:
    raise RuntimeError("another diagnostic master session is active")
if any(item.get("paused_by_diagnostic") for item in state["workloads"].values()):
    raise RuntimeError("a diagnostic workload is already paused")
enabled = api("/api/v1/diagnostics/control", method="PUT", body={
    "target": "master", "enabled": True, "ttl_seconds": 180,
    "expected_revision": state["control_revision"],
})
session = enabled["diagnostic_tool"]["session_id"]
run_id = None
samples = []
try:
    run = api("/api/v1/diagnostics/runs", method="POST", body={
        "kind": "measure", "seconds": 45, "label": "wal-background-same-window",
        "request_id": "wal-background-same-window-20260930",
        "expected_session": session, "expected_revision": enabled["control_revision"],
    })
    run_id = run["run_id"]
    print(json.dumps({"run_id": run_id, "state": run["state"]}), flush=True)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        samples.append(background_sample())
        time.sleep(max(0.0, min(1.0, deadline - time.monotonic())))
    status = api("/api/v1/diagnostics/runs/" + run_id)
    for _ in range(15):
        if status.get("report_url") or status.get("state") in {"failed", "cancelled", "incomplete"}:
            break
        time.sleep(1)
        status = api("/api/v1/diagnostics/runs/" + run_id)
    phase = {}
    slow = []
    if status.get("report_url"):
        report = api("/api/v1/diagnostics/reports/" + run_id + "?mode=raw")
        result = report.get("result", report)
        phases = [result.get("phase", {})] if result.get("kind") == "measure" else result.get("phases", [])
        if phases:
            phase = phases[0]
            for call in phase.get("market_bar_saves", {}).get("kinds", {}).get("minute", {}).get("call_samples", []):
                diag = call.get("commit_diagnostics") or {}
                window = diag.get("commit_window") or {}
                if float(call.get("commit_ms") or 0) < 500 or not window:
                    continue
                concurrent = [sample for sample in samples if window["started_at"] <= sample["at"] <= window["ended_at"]]
                waits = Counter(key for sample in concurrent for key, count in sample.get("waits", {}).items()
                                for _ in range(count) if key.split(":")[1] not in {"Activity", "Timeout"})
                storage = (diag.get("storage_device_window") or {}).get("host_device_delta") or {}
                slow.append({"commit_ms": call.get("commit_ms"),
                             "commit_started_at": window["started_at"],
                             "host_sample_count": len(concurrent),
                             "background_nonidle_waits": dict(waits),
                             "md4": {key: storage.get("md4", {}).get(key) for key in
                                     ("average_queue", "busy_percent", "write_await_ms")}})
    all_nonidle = Counter(key for sample in samples for key, count in sample.get("waits", {}).items()
                          for _ in range(count) if key.split(":")[1] not in {"Activity", "Timeout"})
    print(json.dumps({"run_id": run_id, "state": status.get("state"),
                      "phase_state": phase.get("state"), "phase_started_at_utc": phase.get("started_at_utc"),
                      "phase_elapsed_seconds": phase.get("elapsed_seconds"),
                      "host_samples": len(samples), "host_sample_errors": sum("error" in row for row in samples),
                      "host_first_at": samples[0]["at"] if samples else None,
                      "host_last_at": samples[-1]["at"] if samples else None,
                      "background_nonidle_waits": dict(all_nonidle),
                      "minute_calls": len(phase.get("market_bar_saves", {}).get("kinds", {}).get("minute", {}).get("call_samples", [])),
                      "slow_minute_commits": slow}, ensure_ascii=False))
finally:
    current = api("/api/v1/diagnostics/workloads")
    if current["diagnostic_tool"].get("session_id") == session:
        api("/api/v1/diagnostics/control", method="PUT", body={
            "target": "master", "enabled": False, "ttl_seconds": 180,
            "expected_revision": current["control_revision"],
            "expected_session": session,
        })
