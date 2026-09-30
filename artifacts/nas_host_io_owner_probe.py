"""One-shot NAS host I/O owner sample around a slow minute-bar COMMIT.

Requires interactive sudo on the NAS host to read other processes' /proc/io.
It only reads host and PostgreSQL diagnostics, owns its capture session, and
turns that session off in finally. Delete the staged /tmp copy after review.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import time
import urllib.parse
import urllib.request
from collections import Counter, deque


ENV_PATH = pathlib.Path("/volume1/docker/kiwoom-monitor/deploy/synology/.env")
DEVICES = {"dm-4", "md4", "md2"}
metadata: dict[int, dict[str, str]] = {}


def process_info(pid: int) -> dict[str, str]:
    if pid not in metadata:
        path = pathlib.Path("/proc") / str(pid)
        comm = path.joinpath("comm").read_text().strip()
        cgroups = path.joinpath("cgroup").read_text().splitlines()
        blkio = next((line.split(":", 2)[2] for line in cgroups
                      if line.split(":", 2)[1] == "blkio"), "unknown")
        metadata[pid] = {"comm": comm, "cgroup": blkio}
    return metadata[pid]


def host_snapshot() -> dict:
    at = time.time()
    processes = {}
    denied = 0
    for path in pathlib.Path("/proc").iterdir():
        if not path.name.isdecimal():
            continue
        pid = int(path.name)
        try:
            counters = {}
            for line in path.joinpath("io").read_text().splitlines():
                key, value = line.split(":", 1)
                if key in {"read_bytes", "write_bytes"}:
                    counters[key] = int(value.strip())
            if len(counters) == 2:
                processes[pid] = {**process_info(pid), **counters}
        except PermissionError:
            denied += 1
        except (FileNotFoundError, ProcessLookupError, ValueError):
            pass
    devices = {}
    for line in pathlib.Path("/proc/diskstats").read_text().splitlines():
        parts = line.split()
        if parts[2] in DEVICES:
            fields = [int(value) for value in parts[3:]]
            devices[parts[2]] = {"writes": fields[4], "write_sectors": fields[6],
                                 "write_ms": fields[7], "busy_ms": fields[9],
                                 "weighted_ms": fields[10]}
    return {"at": at, "processes": processes, "devices": devices,
            "io_permission_denied": denied}


def api(base: str, token: str, path: str, *, method="GET", body=None) -> dict:
    request = urllib.request.Request(
        base + path, data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def window_result(samples: list[dict], start: float, end: float, containers: dict) -> dict:
    earlier = [row for row in samples if row["at"] <= start]
    later = [row for row in samples if row["at"] >= end]
    if not earlier or not later:
        return {"available": False, "reason": "event_window_not_bracketed"}
    before, after = earlier[-1], later[0]
    elapsed_ms = (after["at"] - before["at"]) * 1000
    process_deltas = []
    for pid in before["processes"].keys() & after["processes"].keys():
        old, new = before["processes"][pid], after["processes"][pid]
        if (old["comm"], old["cgroup"]) != (new["comm"], new["cgroup"]):
            continue
        written = new["write_bytes"] - old["write_bytes"]
        read = new["read_bytes"] - old["read_bytes"]
        if written < 0 or read < 0:
            continue
        group = old["cgroup"]
        container = next((name for identity, name in containers.items()
                          if identity in group), None)
        process_deltas.append({"pid": pid, "comm": old["comm"], "cgroup": group,
                               "container": container, "write_bytes": written,
                               "read_bytes": read})
    grouped = Counter()
    for row in process_deltas:
        grouped[row["container"] or row["cgroup"]] += row["write_bytes"]
    device_deltas = {}
    for name in DEVICES:
        if name not in before["devices"] or name not in after["devices"]:
            continue
        old, new = before["devices"][name], after["devices"][name]
        device_deltas[name] = {
            "write_bytes": (new["write_sectors"] - old["write_sectors"]) * 512,
            "writes": new["writes"] - old["writes"],
            "busy_percent": round(100 * (new["busy_ms"] - old["busy_ms"]) / elapsed_ms, 2),
            "average_queue": round((new["weighted_ms"] - old["weighted_ms"]) / elapsed_ms, 2),
        }
    return {"available": True, "sample_interval_ms": round(elapsed_ms, 1),
            "processes_at_start": len(before["processes"]),
            "processes_at_end": len(after["processes"]),
            "unmatched_start": len(before["processes"].keys() - after["processes"].keys()),
            "unmatched_end": len(after["processes"].keys() - before["processes"].keys()),
            "io_permission_denied": max(before["io_permission_denied"],
                                        after["io_permission_denied"]),
            "top_process_writers": sorted(process_deltas, key=lambda row: row["write_bytes"],
                                          reverse=True)[:12],
            "top_cgroup_writers": grouped.most_common(12),
            "device_deltas": device_deltas,
            "scope_note": "process /proc/io and block-device sectors are different accounting layers; "
                          "do not subtract them to assign the remaining bytes"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        row = host_snapshot()
        print(json.dumps({"visible_process_io": len(row["processes"]),
                          "permission_denied": row["io_permission_denied"],
                          "devices": sorted(row["devices"])}))
        return
    if os.geteuid() != 0:
        raise SystemExit("sudo is required to read NAS host process I/O")
    values = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"\'')
    base = "http://127.0.0.1:" + values.get("KIWOOM_MONITOR_PORT", "8787")
    token = values["MONITOR_SERVER_ACCESS_TOKEN"]
    client = lambda path, **kwargs: api(base, token, path, **kwargs)
    container_output = subprocess.run(
        ["/usr/local/bin/docker", "ps", "--format", "{{.ID}}|{{.Names}}"],
        capture_output=True, text=True, timeout=5,
    )
    containers = dict(line.split("|", 1) for line in container_output.stdout.splitlines()
                      if "|" in line) if container_output.returncode == 0 else {}
    preflight = host_snapshot()
    if preflight["io_permission_denied"] > 10:
        raise RuntimeError("host process I/O still denied under sudo")
    state = client("/api/v1/diagnostics/workloads")
    if state["diagnostic_tool"]["enabled"] or state["metrics_capture"]["enabled"]:
        raise RuntimeError("another diagnostic session is active")
    if any(item.get("paused_by_diagnostic") for item in state["workloads"].values()):
        raise RuntimeError("a diagnostic workload is paused")
    enabled = client("/api/v1/diagnostics/control", method="PUT", body={
        "target": "master", "enabled": True, "ttl_seconds": 180,
        "expected_revision": state["control_revision"],
    })
    session = enabled["diagnostic_tool"]["session_id"]
    samples = deque(maxlen=240)
    seen = set()
    trigger = None
    minute_calls = 0
    max_minute_commit_ms = 0.0
    raw_truncated = False
    try:
        client("/api/v1/diagnostics/control", method="PUT", body={
            "target": "capture", "enabled": True, "ttl_seconds": 150,
            "expected_revision": enabled["control_revision"],
            "expected_session": session,
        })
        print(json.dumps({"capture_started": True, "maximum_seconds": 90}), flush=True)
        deadline = time.monotonic() + 90
        stop_after = None
        while time.monotonic() < deadline:
            samples.append(host_snapshot())
            now = time.time() + 0.01
            query = urllib.parse.urlencode({"start": now - 8, "end": now,
                                            "mode": "raw", "limit": 500})
            result = client("/api/v1/diagnostics/db-calls?" + query)
            raw_truncated |= bool(result.get("raw_truncated") or result.get("truncated")
                                  or result.get("dropped"))
            for call in result.get("calls", []):
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
                    stop_after = time.monotonic() + 2
                    print(json.dumps({"triggered": True,
                                      "minute_commit_ms": call["commit_ms"]}), flush=True)
            if stop_after is not None and time.monotonic() >= stop_after:
                break
            time.sleep(0.35)
        output = {"triggered": trigger is not None, "samples": len(samples),
                  "minute_calls": minute_calls,
                  "max_minute_commit_ms": round(max_minute_commit_ms, 3),
                  "raw_truncated_or_dropped": raw_truncated,
                  "maximum_permission_denied": max(row["io_permission_denied"]
                                                   for row in samples)}
        if trigger is not None:
            start, end = trigger["commit_started_at"], trigger["commit_finished_at"]
            output["slow_commit"] = {"backend_pid": trigger.get("backend_pid"),
                                     "commit_ms": trigger["commit_ms"],
                                     "started_at": start, "finished_at": end,
                                     "host_io": window_result(list(samples), start, end,
                                                              containers)}
        elif len(samples) >= 2:
            output["untriggered_interval"] = {
                "elapsed_seconds": round(samples[-1]["at"] - samples[0]["at"], 3),
                "scope_note": "whole-window host I/O only; no slow minute COMMIT attribution",
                "host_io": window_result(list(samples), samples[0]["at"] + 0.001,
                                         samples[-1]["at"] - 0.001, containers),
            }
        print(json.dumps(output, ensure_ascii=False), flush=True)
    finally:
        current = client("/api/v1/diagnostics/workloads")
        if current["diagnostic_tool"].get("session_id") == session:
            client("/api/v1/diagnostics/control", method="PUT", body={
                "target": "master", "enabled": False, "ttl_seconds": 180,
                "expected_revision": current["control_revision"],
                "expected_session": session,
            })
        final = client("/api/v1/diagnostics/workloads")
        print(json.dumps({"cleanup": {"master_enabled": final["diagnostic_tool"]["enabled"],
                                      "capture_enabled": final["metrics_capture"]["enabled"],
                                      "paused": [name for name, item in final["workloads"].items()
                                                 if item.get("paused_by_diagnostic")]}}), flush=True)


if __name__ == "__main__":
    main()
