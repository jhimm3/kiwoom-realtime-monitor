"""Capture five minutes of ordinary observed DB calls from the server process.

Run with stdin inside the NAS server container. It refuses to take over an
existing diagnostic session, enables only the bounded metrics capture gate,
and lets the existing lease expire as a fallback if this process is stopped.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from kiwoom_monitor.central_server.diagnostic_workloads import (
    control_path,
    control_snapshot,
)


CLI = "/app/scripts/nas_workload_diagnostic.py"
SAMPLE_SECONDS = 300
TOOL_TTL = "10m"
CAPTURE_TTL = "7m"


def cli(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, CLI, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"diagnostic control command failed: {args[0]} ({result.returncode})")


def db_calls(start: float, end: float) -> dict:
    token = os.environ.get("MONITOR_SERVER_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("server access token is unavailable in the container environment")
    port = os.environ.get("KIWOOM_SERVER_PORT", "8787")
    query = urlencode({"start": start, "end": end, "mode": "summary"})
    request = Request(
        f"http://127.0.0.1:{port}/api/v1/diagnostics/db-calls?{query}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(request, timeout=10) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("db-calls returned a non-object response")
    return value


def captured_state(control: dict, response: dict) -> bool:
    state = control.get("metrics_capture", {})
    capture = response.get("capture", {})
    return bool(
        state.get("enabled")
        and capture.get("enabled")
        and state.get("session_id")
        and state.get("session_id") == capture.get("session_id")
    )


def main() -> None:
    path = control_path()
    if path is None:
        raise RuntimeError("diagnostic control path is not configured")
    initial = control_snapshot(path)
    if (initial["diagnostic_tool"].get("enabled")
            or initial["metrics_capture"].get("enabled")
            or initial["paused"]):
        raise RuntimeError("an existing diagnostic session or workload lease is active; left it unchanged")

    tool_started = False
    session_id: str | None = None
    result: dict[str, object]
    try:
        cli("tool", "on", "--ttl", TOOL_TTL)
        tool_started = True
        session = control_snapshot(path)
        session_id = session["diagnostic_tool"].get("session_id")
        if not session["diagnostic_tool"].get("enabled") or not session_id:
            raise RuntimeError("diagnostic master session did not start")

        cli("capture", "on", "--ttl", CAPTURE_TTL)
        control_before = control_snapshot(path)
        if control_before["diagnostic_tool"].get("session_id") != session_id:
            raise RuntimeError("diagnostic master session changed during capture setup")
        window_start = time.time()
        response_before = db_calls(window_start - 1.0, window_start)
        if not captured_state(control_before, response_before):
            raise RuntimeError("bounded DB-call capture did not become active")
        producer_before = response_before.get("producer")

        time.sleep(SAMPLE_SECONDS)

        window_end = time.time()
        response_after = db_calls(window_start, window_end)
        control_after = control_snapshot(path)
        stable = (
            producer_before == response_after.get("producer")
            and captured_state(control_after, response_after)
            and control_before["control_revision"] == control_after["control_revision"]
            and control_after["diagnostic_tool"].get("session_id") == session_id
            and not response_after.get("truncated")
        )
        writers = response_after.get("writers", {})
        readers = response_after.get("readers", {})
        observed = sum(int(item.get("calls", 0)) for item in (*writers.values(), *readers.values()))
        result = {
            "sample_status": "complete" if stable else "incomplete",
            "activity_status": "observed_calls" if observed else "no_observed_calls",
            "window_seconds": round(window_end - window_start, 1),
            "capture": {"enabled_at_start": True, "enabled_at_end": captured_state(control_after, response_after)},
            "producer_stable": producer_before == response_after.get("producer"),
            "control_revision_stable": control_before["control_revision"] == control_after["control_revision"],
            "coverage": response_after.get("coverage"),
            "writers": writers,
            "readers": readers,
            "unregistered_calls": response_after.get("unregistered_calls"),
            "dropped": response_after.get("dropped"),
            "truncated": response_after.get("truncated"),
            "scope_note": response_after.get("scope_note"),
        }
    finally:
        # Turning the master gate off also clears its child capture lease.
        # Check the session first so cleanup does not stop a later operator's run.
        if tool_started and session_id:
            current = control_snapshot(path)
            if current["diagnostic_tool"].get("session_id") == session_id:
                cli("tool", "off")

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"sample_status": "failed", "reason": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
