"""Call the authenticated NAS diagnostic API without printing its token."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


env = {}
for line in Path("/volume1/docker/kiwoom-monitor/deploy/synology/.env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    env[key.strip()] = value.strip().strip('"\'')
token = env["MONITOR_SERVER_ACCESS_TOKEN"]
port = env.get("KIWOOM_MONITOR_PORT", "8787")
base = f"http://127.0.0.1:{port}"


def api(path: str, *, method: str = "GET", body: dict | None = None) -> dict:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        print(json.dumps({"http_error": error.code, "body": error.read().decode()[:500]}))
        raise


def controls() -> dict:
    return api("/api/v1/diagnostics/workloads")


mode = sys.argv[1]
if mode == "status":
    health = json.load(urllib.request.urlopen(base + "/health", timeout=5))
    state = controls()
    print(json.dumps({
        "build": health.get("server_build"),
        "diagnostic_tool": state.get("diagnostic_tool"),
        "capture": state.get("metrics_capture"),
        "control_revision": state.get("control_revision"),
        "metadata_gate": state.get("workloads", {}).get("minute_query_metadata"),
        "paused": [name for name, item in state.get("workloads", {}).items()
                   if item.get("paused_by_diagnostic")],
    }))
elif mode == "start":
    state = controls()
    gate = state.get("workloads", {}).get("minute_query_metadata", {})
    if not gate.get("configured") or gate.get("paused_by_diagnostic"):
        raise RuntimeError("minute metadata gate unavailable or already paused")
    if any(item.get("paused_by_diagnostic") for item in state["workloads"].values()):
        raise RuntimeError("another diagnostic workload is paused")
    if state["diagnostic_tool"]["enabled"]:
        raise RuntimeError("diagnostic master is already enabled; avoid changing another session")
    enabled = api("/api/v1/diagnostics/control", method="PUT", body={
        "target": "master", "enabled": True, "ttl_seconds": 600,
        "expected_revision": state["control_revision"],
    })
    tool = enabled["diagnostic_tool"]
    run = api("/api/v1/diagnostics/runs", method="POST", body={
        "kind": "compare", "seconds": 60,
        "label": "minute-metadata-aba", "workload": "minute_query_metadata",
        "request_id": "minute-metadata-aba-20260930",
        "expected_session": tool["session_id"],
        "expected_revision": enabled["control_revision"],
    })
    print(json.dumps({"run_id": run["run_id"], "state": run["state"],
                      "session_id": tool["session_id"]}))
elif mode == "measure_start":
    state = controls()
    if state["diagnostic_tool"]["enabled"]:
        raise RuntimeError("diagnostic master already enabled; do not take over another session")
    if any(item.get("paused_by_diagnostic") for item in state["workloads"].values()):
        raise RuntimeError("diagnostic workload already paused")
    enabled = api("/api/v1/diagnostics/control", method="PUT", body={
        "target": "master", "enabled": True, "ttl_seconds": 300,
        "expected_revision": state["control_revision"],
    })
    tool = enabled["diagnostic_tool"]
    run = api("/api/v1/diagnostics/runs", method="POST", body={
        "kind": "measure", "seconds": 45, "label": "wal-background-correlation",
        "request_id": "wal-background-correlation-20260930",
        "expected_session": tool["session_id"],
        "expected_revision": enabled["control_revision"],
    })
    print(json.dumps({"run_id": run["run_id"], "state": run["state"],
                      "session_id": tool["session_id"]}))
elif mode == "run":
    run = api("/api/v1/diagnostics/runs/" + sys.argv[2])
    print(json.dumps({key: run.get(key) for key in
                      ("run_id", "state", "reason", "report_url", "created_at", "finished_at")}))
elif mode == "report":
    report = api("/api/v1/diagnostics/reports/" + sys.argv[2])
    result = report.get("result", report)
    phases = []
    for phase in result.get("phases", []):
        minute = phase.get("market_bar_saves", {}).get("kinds", {}).get("minute", {})
        delta = phase.get("database_delta", {})
        phases.append({
            "label": phase.get("label"), "state": phase.get("state"),
            "seconds": phase.get("elapsed_seconds"),
            "paused_at_end": phase.get("paused_at_end"),
            "minute_calls": minute.get("count"),
            "minute_rows": minute.get("bar_rows"),
            "metadata_suppressed_rows": minute.get("metadata_suppressed_rows"),
            "metadata_affected_rows": minute.get("metadata_statement_diagnostics", {}).get("affected_rows"),
            "bar_affected_rows": minute.get("bar_statement_diagnostics", {}).get("affected_rows"),
            "revision_insert_rows": minute.get("revision_insert_rows"),
            "commit_ms": minute.get("commit_ms"),
            "minute_commit_waits": minute.get("commit_diagnostics", {}).get("wait_event_samples"),
            "all_waits": phase.get("wait_samples"),
            "wal": {key: delta.get(key) for key in ("wal_bytes", "wal_write", "wal_sync",
                                                 "wal_write_time_ms", "wal_sync_time_ms")},
            "dm4": phase.get("storage_devices", {}).get("dm-4"),
            "stats_reset": phase.get("statistics_reset"),
            "bar_capture_truncated": phase.get("market_bar_saves", {}).get("truncated"),
            "db_calls_coverage": phase.get("db_calls", {}).get("coverage"),
        })
    print(json.dumps({"run_id": report.get("run_id"), "state": report.get("state"),
                      "conclusion": result.get("conclusion"), "phases": phases},
                     ensure_ascii=False))
elif mode == "detail":
    report = api("/api/v1/diagnostics/reports/" + sys.argv[2])
    result = report.get("result", report)
    for phase in result.get("phases", []):
        calls = phase.get("db_calls", {})
        summary = calls.get("summary", {})
        counts = {}
        for group in ("writers", "readers"):
            for name, item in summary.get(group, {}).items():
                count = item.get("calls", 0)
                if count:
                    counts[name] = count
        print(json.dumps({
            "phase": phase.get("label"), "db_call_state": calls.get("state"),
            "db_call_groups": dict(sorted(counts.items(), key=lambda item: -item[1])[:12]),
            "writer_groups": {name: item.get("calls") for name, item in
                              summary.get("writers", {}).items() if item.get("calls")},
            "log_counts": phase.get("log_counts"),
            "checkpointer_counters": phase.get("checkpointer_delta", {}).get("counters"),
            "pg_stat_io_client_backend": phase.get("pg_stat_io_delta", {}).get("groups", {}).get(
                "client backend|relation|normal", {}).get("counters"),
            "capture": phase.get("market_bar_saves", {}).get("capture"),
        }, ensure_ascii=False, default=str))
elif mode == "shape":
    report = api("/api/v1/diagnostics/reports/" + sys.argv[2] + "?mode=raw")
    result = report.get("result", report)
    for phase in result.get("phases", []):
        raw = phase.get("db_calls_raw")
        summary = phase.get("db_calls", {}).get("summary", {})
        print(json.dumps({
            "phase": phase.get("label"),
            "raw_type": type(raw).__name__,
            "raw_keys": list(raw) if isinstance(raw, dict) else None,
            "raw_call_count": len(raw.get("calls", [])) if isinstance(raw, dict) else None,
            "raw_call_keys": list(raw.get("calls", [{}])[0]) if raw and raw.get("calls") else None,
            "raw_first_call": {key: raw["calls"][0].get(key) for key in
                               ("started_at", "finished_at", "at", "writer_family", "writer_kind",
                                "access_mode", "commits", "transactions", "commit_ms")}
                              if raw and raw.get("calls") else None,
            "summary_keys": list(summary),
            "summary_writer_example": next(iter(summary.get("writers", {}).values()), None),
        }, default=str))
elif mode == "commit_audit":
    report = api("/api/v1/diagnostics/reports/" + sys.argv[2] + "?mode=raw")
    result = report.get("result", report)
    for phase in result.get("phases", []):
        raw = phase.get("db_calls_raw") or {}
        calls = raw.get("calls", [])
        writer_commits = Counter()
        seconds = defaultdict(Counter)
        no_timestamp = 0
        for call in calls:
            commits = int(call.get("commits") or 0)
            if not commits:
                continue
            mode_name = call.get("access_mode") or "unknown"
            if mode_name == "write":
                writer_commits[f"{call.get('writer_family')}/{call.get('writer_kind')}"] += commits
            when = call.get("commit_finished_at")
            if when is None:
                no_timestamp += commits
            else:
                seconds[int(float(when))][mode_name] += commits
        read_calls = [call for call in calls if call.get("writer_family") == "read.market_bars"
                      and call.get("writer_kind") == "minute_bar"]
        sources = Counter(str(call.get("source")) for call in read_calls)
        request_ids = Counter(str(call.get("request_id")) for call in read_calls)
        api_ids = Counter(str(call.get("api_id")) for call in read_calls)
        busiest = sorted(seconds.items(), key=lambda item: (-item[1]["write"], item[0]))[:8]
        print(json.dumps({
            "phase": phase.get("label"), "raw_calls": len(calls),
            "raw_truncated": raw.get("raw_truncated"), "dropped": raw.get("dropped"),
            "coverage": raw.get("coverage"),
            "writer_commits": sum(writer_commits.values()),
            "reader_commits": sum(counts["read"] for counts in seconds.values()),
            "writer_by_kind": dict(writer_commits.most_common()),
            "commit_timestamp_missing": no_timestamp,
            "active_seconds_with_writer_commit": sum(counts["write"] > 0 for counts in seconds.values()),
            "max_writer_commits_in_one_second": max((counts["write"] for counts in seconds.values()), default=0),
            "top_write_seconds": [{"epoch_second": second, "write_commits": count["write"],
                                   "read_commits": count["read"]} for second, count in busiest],
            "minute_read_count": len(read_calls),
            "minute_read_source": dict(sources),
            "minute_read_request_id": dict(request_ids),
            "minute_read_api_id": dict(api_ids),
            "minute_read_first_at": min((call.get("at") for call in read_calls), default=None),
            "minute_read_last_at": max((call.get("at") for call in read_calls), default=None),
        }, ensure_ascii=False))
elif mode == "slow_commit_overlap":
    report = api("/api/v1/diagnostics/reports/" + sys.argv[2] + "?mode=raw")
    result = report.get("result", report)
    for phase in result.get("phases", []):
        calls = (phase.get("db_calls_raw") or {}).get("calls", [])
        minute = [call for call in calls if call.get("writer_family") == "rest.market_bars.minute"
                  and call.get("access_mode") == "write" and call.get("commit_ms") is not None]
        samples = phase.get("market_bar_saves", {}).get("kinds", {}).get("minute", {}).get("call_samples", [])
        by_id = {sample.get("db_call_id"): sample for sample in samples}
        slow = []
        for call in sorted(minute, key=lambda row: float(row.get("commit_ms") or 0), reverse=True)[:5]:
            start, end = call.get("commit_started_at"), call.get("commit_finished_at")
            if start is None or end is None:
                continue
            peers = [other for other in calls if other.get("call_id") != call.get("call_id")
                     and other.get("access_mode") == "write" and other.get("commits")
                     and other.get("commit_started_at") is not None
                     and other.get("commit_finished_at") is not None
                     and float(other["commit_started_at"]) < float(end)
                     and float(other["commit_finished_at"]) > float(start)]
            peer_kinds = Counter(f"{row.get('writer_family')}/{row.get('writer_kind')}" for row in peers)
            material_peers = [row for row in peers if float(row.get("commit_ms") or 0) >= 50]
            sample = by_id.get(call.get("call_id"), {})
            diag = sample.get("commit_diagnostics") or {}
            waits = Counter(f"{row.get('wait_type') or 'NONE'}:{row.get('wait_event') or 'NONE'}"
                            for row in diag.get("samples", []))
            slow.append({"commit_started_at": start, "commit_ms": call.get("commit_ms"),
                         "execute_ms": call.get("execute_ms"), "peer_writer_commit_windows": len(peers),
                         "peer_kinds": dict(peer_kinds.most_common()), "same_backend_waits": dict(waits),
                         "peer_commit_over_50ms": len(material_peers),
                         "peer_commit_over_50ms_kinds": dict(Counter(
                             f"{row.get('writer_family')}/{row.get('writer_kind')}"
                             for row in material_peers).most_common()),
                         "probe_errors": len(diag.get("probe_errors", [])),
                         "probe_pending": diag.get("probe_pending_at_capture")})
        claim_ms = sorted(float(call.get("commit_ms") or 0) for call in calls
                          if call.get("writer_family") == "news.job_claim"
                          and call.get("access_mode") == "write" and call.get("commit_ms") is not None)
        no_peer = []
        for call in minute:
            start, end = call.get("commit_started_at"), call.get("commit_finished_at")
            if start is None or end is None:
                continue
            overlaps = any(other.get("call_id") != call.get("call_id")
                           and other.get("access_mode") == "write" and other.get("commits")
                           and other.get("commit_started_at") is not None
                           and other.get("commit_finished_at") is not None
                           and float(other["commit_started_at"]) < float(end)
                           and float(other["commit_finished_at"]) > float(start)
                           for other in calls)
            if not overlaps:
                no_peer.append(float(call.get("commit_ms") or 0))
        print(json.dumps({"phase": phase.get("label"), "minute_writer_calls": len(minute),
                          "raw_truncated": (phase.get("db_calls_raw") or {}).get("raw_truncated"),
                          "news_claim_commit": {"n": len(claim_ms),
                                                "median_ms": claim_ms[len(claim_ms)//2] if claim_ms else None,
                                                "max_ms": claim_ms[-1] if claim_ms else None,
                                                "over_50ms": sum(value >= 50 for value in claim_ms)},
                          "minute_commit_without_observed_writer_peer": {
                              "count": len(no_peer), "max_ms": max(no_peer, default=None),
                              "over_500ms": sum(value >= 500 for value in no_peer)},
                          "slowest_minute_commits": slow}, ensure_ascii=False))
elif mode == "commit_device":
    report = api("/api/v1/diagnostics/reports/" + sys.argv[2] + "?mode=raw")
    result = report.get("result", report)
    for phase in result.get("phases", []):
        kind = phase.get("market_bar_saves", {}).get("kinds", {}).get("minute", {})
        calls = kind.get("call_samples", [])
        rows = []
        for call in sorted(calls, key=lambda item: float(item.get("commit_ms") or 0), reverse=True)[:6]:
            diag = call.get("commit_diagnostics") or {}
            storage = diag.get("storage_device_window") or {}
            devices = storage.get("host_device_delta") or {}
            rows.append({"commit_ms": call.get("commit_ms"), "rows": call.get("rows"),
                         "storage_available": storage.get("available"),
                         "observed_interval_ms": storage.get("observed_interval_ms"),
                         "boundary_padding_ms": storage.get("boundary_padding_ms"),
                         "dm4": devices.get("dm-4"), "dm1": devices.get("dm-1"),
                         "md4": devices.get("md4"), "dm3": devices.get("dm-3"),
                         "md2": devices.get("md2"), "md3": devices.get("md3")})
        print(json.dumps({"phase": phase.get("label"), "minute_samples": len(calls),
                          "slowest_commit_devices": rows}, ensure_ascii=False))
elif mode == "device_compare":
    report = api("/api/v1/diagnostics/reports/" + sys.argv[2] + "?mode=raw")
    for phase in report.get("result", report).get("phases", []):
        calls = phase.get("market_bar_saves", {}).get("kinds", {}).get("minute", {}).get("call_samples", [])
        buckets = {"under_100ms": [], "100_to_499ms": [], "at_least_500ms": []}
        for call in calls:
            ms = float(call.get("commit_ms") or 0)
            storage = (call.get("commit_diagnostics") or {}).get("storage_device_window") or {}
            if storage.get("available") is not True:
                continue
            bucket = "under_100ms" if ms < 100 else "100_to_499ms" if ms < 500 else "at_least_500ms"
            buckets[bucket].append((ms, storage.get("host_device_delta") or {}))
        summary = {}
        for name, rows in buckets.items():
            values = {}
            for device in ("dm-4", "md4", "md2"):
                for metric in ("average_queue", "busy_percent", "write_await_ms"):
                    observed = sorted(float(devices.get(device, {}).get(metric)) for _ms, devices in rows
                                      if devices.get(device, {}).get(metric) is not None)
                    values[f"{device}.{metric}.median"] = observed[len(observed)//2] if observed else None
            summary[name] = {"n": len(rows), "commit_median_ms": sorted(row[0] for row in rows)[len(rows)//2]
                             if rows else None, **values}
        print(json.dumps({"phase": phase.get("label"), "minute_samples": len(calls),
                          "storage_aligned": sum(len(rows) for rows in buckets.values()),
                          "buckets": summary}, ensure_ascii=False))
elif mode == "io_backend_audit":
    report = api("/api/v1/diagnostics/reports/" + sys.argv[2])
    for phase in report.get("result", report).get("phases", []):
        groups = phase.get("pg_stat_io_delta", {}).get("groups", {})
        selected = {}
        for key, group in groups.items():
            counters = group.get("counters", {}) if isinstance(group, dict) else {}
            values = {name: counters.get(name) for name in
                      ("reads", "writes", "writebacks", "fsyncs", "evictions", "extends")}
            if any(isinstance(value, (int, float)) and value > 0 for value in values.values()):
                selected[key] = values
        wal = phase.get("database_delta", {})
        print(json.dumps({"phase": phase.get("label"), "pg_stat_io": selected,
                          "checkpointer": phase.get("checkpointer_delta", {}).get("counters"),
                          "wal": {key: wal.get(key) for key in
                                  ("wal_bytes", "wal_write", "wal_sync", "wal_buffers_full")}},
                         ensure_ascii=False))
elif mode == "measure_summary":
    report = api("/api/v1/diagnostics/reports/" + sys.argv[2] + "?mode=raw")
    result = report.get("result", report)
    phase = result.get("phase") or {}
    minute = phase.get("market_bar_saves", {}).get("kinds", {}).get("minute", {})
    calls = (phase.get("db_calls_raw") or {}).get("calls", [])
    slow = sorted((call for call in calls if call.get("access_mode") == "write"
                   and float(call.get("commit_ms") or 0) >= 500),
                  key=lambda call: float(call.get("commit_ms") or 0), reverse=True)
    print(json.dumps({"run_id": report.get("run_id"), "state": report.get("state"),
                      "phase_state": phase.get("state"),
                      "minute_calls": minute.get("count"), "minute_commit_ms": minute.get("commit_ms"),
                      "wal": {key: phase.get("database_delta", {}).get(key) for key in
                              ("wal_bytes", "wal_write", "wal_sync", "wal_sync_time_ms")},
                      "dm4": phase.get("storage_devices", {}).get("dm-4"),
                      "md4": phase.get("storage_devices", {}).get("md4"),
                      "raw_calls": len(calls),
                      "slow_writer_commits": [{"kind": f"{call.get('writer_family')}/{call.get('writer_kind')}",
                                                "commit_ms": call.get("commit_ms"),
                                                "commit_started_at": call.get("commit_started_at")}
                                               for call in slow[:10]]}, ensure_ascii=False))
elif mode == "access_audit":
    report = api("/api/v1/diagnostics/reports/" + sys.argv[2])
    phases = report.get("result", report).get("phases", [])
    windows = []
    for phase in phases:
        start = datetime.fromisoformat(phase["started_at_utc"]).timestamp()
        windows.append((phase["label"], start, start + phase["elapsed_seconds"], Counter()))
    log_path = Path("/volume1/docker/kiwoom-monitor/deploy/synology/server-data/logs/server.log")
    for line in log_path.open(encoding="utf-8", errors="replace"):
        if len(line) < 23 or line[4] != "-":
            continue
        try:
            when = datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        for _label, start, end, counts in windows:
            if start <= when < end:
                if '"GET /api/v1/market/minute-bars' in line:
                    counts["GET_minute_bars"] += 1
                elif '"GET /api/v1/market/recent-minute-bars' in line:
                    counts["GET_recent_minute_bars"] += 1
                elif '"POST /api/v1/kiwoom/query' in line:
                    counts["POST_kiwoom_query"] += 1
                if "request completed namespace=market api_id=ka10080" in line:
                    counts["ka10080_completed"] += 1
                if "slow market minute ingest" in line:
                    counts["slow_minute_ingest"] += 1
                if "TOP20 편입종목 자료 준비 실패" in line:
                    counts["top20_entry_failure"] += 1
                if "TOP20 편입종목 장후 보완 실패" in line:
                    counts["top20_backfill_failure"] += 1
                if "TOP20 장후 보완 미완료" in line:
                    counts["top20_backfill_retry"] += 1
                if "대상일 분봉이 없어" in line:
                    counts["missing_minute_bars"] += 1
                break
    for label, start, end, counts in windows:
        print(json.dumps({"phase": label, "start": start, "end": end,
                          "log_counts": counts}, default=dict))
elif mode == "off":
    state = controls()
    tool = state["diagnostic_tool"]
    if tool["enabled"] and tool.get("session_id") == sys.argv[2]:
        state = api("/api/v1/diagnostics/control", method="PUT", body={
            "target": "master", "enabled": False, "ttl_seconds": 600,
            "expected_revision": state["control_revision"],
            "expected_session": tool["session_id"],
        })
    print(json.dumps({"diagnostic_tool": state.get("diagnostic_tool"),
                      "paused": [name for name, item in state.get("workloads", {}).items()
                                 if item.get("paused_by_diagnostic")]}))
else:
    raise ValueError(mode)
