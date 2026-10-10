"""One-shot NAS-local diagnostic capture starter (stdlib, Python 3.8+).

Only --arm changes controls. No capture restart/retry after an ambiguous POST.
This process survives an SSH disconnect, but not a NAS reboot.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


FLAGS = ("store_inputs", "collector_inputs", "top20_inputs")
EXTRA_FLAGS = ("large_inputs", "account_inputs")
BOUNDARIES = {"REST", "catalog", "ranking", "subscription", "lifecycle", "delivery_receipt"}


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def epoch(value):
    parsed = dt.datetime.fromisoformat(value)
    require(parsed.utcoffset() is not None, "explicit_timezone_required")
    return parsed.timestamp()


def capture_flags(plan):
    flags = plan.get("capture_flags", list(FLAGS))
    require(type(flags) is list and all(type(x) is str for x in flags), "invalid_capture_flags")
    require(len(flags) == len(set(flags)) and set(FLAGS).issubset(flags)
            and set(flags).issubset(FLAGS + EXTRA_FLAGS), "invalid_capture_flags")
    return tuple(flags)


def read_plan(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    require(re.fullmatch(r"[A-Za-z0-9.-]+", plan["release"]) is not None, "invalid_release")
    require(60 <= plan["seconds"] <= 7200, "invalid_duration")
    require(plan["memory_limit_bytes"] > 0 and plan["event_capacity"] > 0, "invalid_limits")
    require(epoch(plan["persist_at"]) >= epoch(plan["start_at"]) + plan["seconds"], "invalid_persistence_time")
    capture_flags(plan)
    return plan


class Api:
    def __init__(self, root):
        values = {}
        for line in (root / "deploy/synology/.env").read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
            if match:
                values[match[1]] = match[2].strip().strip("\"'")
        self.token = values["MONITOR_SERVER_ACCESS_TOKEN"]
        port = int(values.get("KIWOOM_MONITOR_PORT", "8787"))
        require(bool(self.token) and 0 < port < 65536, "invalid_local_API_configuration")
        self.base = "http://127.0.0.1:" + str(port)

    def request(self, path, body=None, method="GET"):
        headers = {"Authorization": "Bearer " + self.token}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        try:
            with urlopen(Request(self.base + path, data=data, headers=headers, method=method), timeout=10) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError("API_HTTP_%s_%s" % (error.code, path)) from None


def active_release(root):
    """Read selection through the installed status client when deploy protects active.json."""
    try:
        active = json.loads((root / "source-runtime/active.json").read_text(encoding="utf-8"))
        return active.get("release_id")
    except PermissionError:
        require(root == Path("/volume1/docker/kiwoom-monitor"), "unexpected_NAS_status_fallback_root")
    # The root-owned deployer replaces active.json with mode0600. Use its already
    # approved read-only interface; do not change file ownership or sudo rules.
    result = subprocess.run(["/usr/local/bin/kiwoom-nas", "status"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=20, cwd="/", env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C"})
    require(result.returncode == 0 and len(result.stdout) <= 1024**2,
            "active_release_status_unavailable")
    try:
        status = json.loads(result.stdout)
        selected = status["result"]["active_release"]
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("active_release_status_invalid") from None
    require(status.get("state") == "ok" and type(selected) is str
            and re.fullmatch(r"[A-Za-z0-9.-]+", selected) is not None,
            "active_release_status_invalid")
    return selected


def preflight(api, root, plan):
    flags = capture_flags(plan)
    require(active_release(root) == plan["release"], "active_release_mismatch")
    health = api.request("/health")
    caps = api.request("/api/v1/diagnostics/capabilities")
    work = api.request("/api/v1/diagnostics/workloads")
    trace = api.request("/api/v1/diagnostics/trace")
    reports = api.request("/api/v1/diagnostics/reports?limit=100")
    require(health.get("status") == "ok" and health.get("server_build") == caps.get("server_build") == plan["build"], "build_mismatch")
    require(caps.get("control_available") is True and caps.get("postgres_available") is True, "diagnostics_unavailable")
    capture = caps["trace_input_capture"]
    require(capture.get("schema_version") == 3 and capture.get("coverage") == "observed_paths_only", "capture_contract_mismatch")
    require(all(capture["options"].get(x) is False for x in FLAGS), "capture_default_mismatch")
    for flag, capability in (("large_inputs", "large_input_capture"),
                             ("account_inputs", "account_input_capture")):
        if flag not in flags:
            continue
        option = capture.get(capability, {})
        require(option.get("schema_version") == 4 and option.get("request_field") == flag
                and option.get("default") is False
                and option.get("requires") == ["store_inputs", "persist_at"],
                "capture_opt_in_contract_mismatch")
        if flag == "account_inputs":
            require(option.get("context_capture", {}).get("version") == "account-context/v2",
                    "account_context_contract_mismatch")
    require({"0B", "0w", "0J", "0U"}.issubset(capture["collector_event_types"]), "collector_types_missing")
    require(BOUNDARIES.issubset(capture["causal_input_boundaries"]), "causal_boundaries_missing")
    limits = capture["deferred_persistence"]
    require(limits.get("supported") is True and all(limits.get(k) == plan[k] for k in ("memory_limit_bytes", "event_capacity", "write_bytes_per_second")), "deferred_limits_mismatch")
    require(work["diagnostic_tool"].get("enabled") is False and work["trace_capture"].get("enabled") is False, "diagnostics_busy")
    require(not any(v.get("paused_by_diagnostic") for v in work.get("workloads", {}).values()), "workload_paused")
    require(trace.get("state") in {"off", "complete", "interrupted", "failed", "incomplete"}, "trace_retains_RAM")
    require(not any(x.get("state") in {"starting", "running", "finalizing"} for x in reports.get("items", [])), "run_active")
    require(bool(caps.get("producer_instance")), "instance_missing")
    return work, caps["producer_instance"]


def verify_started(trace, plan, session, instance):
    flags = capture_flags(plan)
    schema = 4 if any(x in flags for x in EXTRA_FLAGS) else 3
    require(trace.get("state") == "running" and trace.get("schema_version") == schema, "trace_start_not_running")
    require(trace.get("source_release") == plan["release"] and trace.get("instance_id") == instance, "trace_source_changed")
    options = trace.get("payload_capture", {})
    require(trace.get("master_session") == session and all(options.get(x) is True for x in flags)
            and all(options.get(x) is not True for x in EXTRA_FLAGS if x not in flags),
            "trace_session_options_mismatch")
    require(trace.get("persistence_mode") == "deferred_ram" and trace.get("persist_at") == epoch(plan["persist_at"]), "trace_persistence_mismatch")
    require(all(trace.get(k) == plan[k] for k in ("memory_limit_bytes", "event_capacity", "write_bytes_per_second")), "trace_limits_mismatch")
    require(abs(trace["expires_at"] - trace["started_at"] - plan["seconds"]) < 1, "trace_duration_mismatch")
    require(trace["started_at"] >= epoch(plan["start_at"]), "trace_started_early")


def start(api, root, plan, wait, clock=time.time):
    target = epoch(plan["start_at"])
    require(clock() <= target + plan["maximum_lateness_seconds"], "start_deadline_missed")
    work, instance = preflight(api, root, plan)
    wait(target - 5)
    require(clock() <= target + plan["maximum_lateness_seconds"], "start_deadline_missed")
    # A second check close to the target catches a release/control change while waiting.
    work, fresh_instance = preflight(api, root, plan)
    require(fresh_instance == instance, "server_restarted_after_preflight")
    master = api.request("/api/v1/diagnostics/control", {
        "target": "master", "enabled": True, "ttl_seconds": 4000,
        "expected_revision": work["control_revision"], "expected_instance": instance,
    }, "PUT")
    session = master["diagnostic_tool"]["session_id"]
    require(master["diagnostic_tool"].get("enabled") is True and bool(session), "master_not_enabled")
    wait(target)
    require(clock() <= target + plan["maximum_lateness_seconds"], "start_deadline_missed")
    body = {"seconds": plan["seconds"], "expected_session": session,
            "persist_at": epoch(plan["persist_at"]), **{x: True for x in capture_flags(plan)}}
    # Exactly one POST. A timeout may mean the server started; never retry or stop it blindly.
    trace = api.request("/api/v1/diagnostics/trace", body, "POST")
    verify_started(trace, plan, session, instance)
    return {"state": "started", "trace_id": trace["trace_id"], "trace": trace,
            "start_lateness_seconds": trace["started_at"] - target, "master": master["diagnostic_tool"]}


def wait_until(target):
    while True:
        remaining = target - time.time()
        if remaining <= 0:
            return
        time.sleep(min(10, remaining))


def publish(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    print(json.dumps(data, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--arm", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    require(root == Path("/volume1/docker/kiwoom-monitor"), "unexpected_NAS_root")
    require(args.status.resolve().parent == root / "artifacts", "unexpected_status_path")
    plan = read_plan(args.plan)
    api = Api(root)
    if not args.arm:
        preflight(api, root, plan)
        print(json.dumps({"state": "preflight_passed", "controls_changed": False, "plan": plan}))
        return 0
    import fcntl
    with open(str(args.status) + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not args.status.exists(), "one_shot_status_already_exists")
        preflight(api, root, plan)
        publish(args.status, {"state": "armed", "pid": os.getpid(), "plan": plan,
                              "armed_at": time.time(), "survives_NAS_reboot": False})
        try:
            wait_until(epoch(plan["start_at"]) - 30)
            result = start(api, root, plan, wait_until)
            publish(args.status, result)
            return 0
        except Exception as error:
            # Only exception type and controlled RuntimeError codes, never credential data.
            publish(args.status, {"state": "failed", "error_type": type(error).__name__,
                                  "reason": str(error) if isinstance(error, RuntimeError) else "inspect_local_execution",
                                  "failed_at": time.time(), "plan": plan,
                                  "automatic_retry": False, "controls_cleanup": "not_attempted; master TTL bounds any partial start"})
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
