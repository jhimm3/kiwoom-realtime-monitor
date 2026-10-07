"""Offline producer-cost comparison; never calls the NAS API or PostgreSQL.

Each source/mode/round uses a fresh process, private controls and in-memory SQLite.
This is a controlled partial fixture, not an intraday performance baseline.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str, separators=(",", ":")).encode()).hexdigest()


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "p50": None, "p95": None, "max": None}
    return {"samples": len(ordered), "p50": ordered[(len(ordered) - 1) // 2],
            "p95": ordered[min(len(ordered) - 1, int(len(ordered) * .95))], "max": ordered[-1]}


def minute_signature(collector, origin, rows):
    # Operation UUIDs identify independent writes and intentionally differ by process.
    return sorted(({k: v for k, v in row.items() if k != "operation_id"}
                   for n in range(rows)
                   for row in collector._minute_bars.pending_bars(f"{5930+n:06d}", origin.date().isoformat())),
                  key=lambda row: (row["code"], row["minute"], row["market"]))


def fixture(messages, rows, mixed):
    origin = datetime(2026, 10, 7, 9, tzinfo=timezone(timedelta(hours=9)))
    result = []
    for index in range(messages):
        batch = []
        for number in range(rows):
            code = f"{5930 + number:06d}"
            batch.append({"type": "0B", "item": code + "_AL", "values": {
                "10": str(70000 + index % 7), "15": "1", "14": str((index + 1) * 70000),
                "20": (origin + timedelta(seconds=index // 10)).strftime("%H%M%S")}})
        if mixed:
            batch += [
                {"type": "0w", "item": ["005930_NX"], "values": {"20": "090000", "210": "-5", "211": "2", "212": "7", "213": "-1"}},
                {"type": "0J", "item": "001", "values": {"20": "090000", "10": "2500.1", "12": "-1.2", "14": "99"}},
                {"type": "0U", "item": "101", "values": {"20": "090000", "10": "800.5"}},
            ]
        result.append({"trnm": "REAL", "data": batch})
    return origin, result


def rss():
    if sys.platform == "linux":
        fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
        return {"rss_bytes": int(fields["VmRSS"].split()[0]) * 1024,
                "process_peak_rss_bytes": int(fields["VmHWM"].split()[0]) * 1024}
    return {"rss_bytes": None, "process_peak_rss_bytes": None}


def worker(args):
    source = Path(args.source).resolve(strict=True)
    app_text = (source / "src/kiwoom_monitor/central_server/app.py").read_text(encoding="utf-8")
    build_match = re.search(r'^SERVER_BUILD\s*=\s*["\']([^"\']+)', app_text, re.M)
    if build_match is None:
        raise RuntimeError("source_build_marker_missing")
    sys.path.insert(0, str(source / "src"))
    from kiwoom_monitor.central_server import diagnostic_trace as trace
    from kiwoom_monitor.central_server.database import SQLiteQueryStore
    from kiwoom_monitor.central_server.diagnostic_replay_contract import thaw_payload
    from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool, _set_trace
    from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
    from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
    import kiwoom_monitor.central_server.realtime_collector as module
    imported = Path(module.__file__).resolve()
    if not imported.is_relative_to(source / "src"):
        raise RuntimeError("wrong_source_imported")

    origin, inputs = fixture(args.messages, args.rows, args.profile == "mixed")
    now = [origin]
    class Hub(RealtimeHub):
        def __init__(self):
            super().__init__()
            self.count = 0
        def publish(self, *a, **kw):
            self.count += 1
            return super().publish(*a, **kw)

    async def measure(store, collector):
        running = [True]
        lateness = []
        async def heartbeat():
            deadline = time.perf_counter()
            while running[0]:
                deadline += .002
                await asyncio.sleep(max(0, deadline - time.perf_counter()))
                lateness.append(max(0, (time.perf_counter() - deadline) * 1000))
        monitor = asyncio.create_task(heartbeat())
        await asyncio.sleep(0)
        producer, scheduling = [], []
        start = time.perf_counter()
        for index, message in enumerate(inputs):
            deadline = start + index / args.rate
            await asyncio.sleep(max(0, deadline - time.perf_counter()))
            scheduling.append(max(0, (time.perf_counter() - deadline) * 1000))
            now[0] = origin + timedelta(milliseconds=index * 100)
            before = time.perf_counter_ns()
            collector._publish_parsed(message)  # Same live input boundary, no network/task start.
            producer.append((time.perf_counter_ns() - before) / 1e6)
        collector_elapsed = time.perf_counter() - start
        store_calls = []
        for index in range(args.store_calls):
            payload = {"rows": [{"code": f"{5930 + n:06d}", "rank": n + 1} for n in range(20)]}
            before = time.perf_counter_ns()
            await asyncio.to_thread(store.save_dataset_snapshot, "ranking", "fixture", str(index), payload)
            store_calls.append((time.perf_counter_ns() - before) / 1e6)
        # Load after the capture is sealed below so validation does not add input events.
        running[0] = False
        await monitor
        return {"producer_ms": distribution(producer), "producer_ms_total": sum(producer),
                "ingress_schedule_lateness_ms": distribution(scheduling),
                "collector_elapsed_seconds": collector_elapsed,
                "loop_lateness_ms": distribution(lateness),
                "sqlite_store_call_ms": distribution(store_calls)}

    def forbidden(*_a, **_kw):
        raise RuntimeError("offline_benchmark_network_forbidden")

    # Windows asyncio creates a private socketpair while constructing its loop.
    # Construct it before blocking external connects; the measured path remains fenced.
    loop = asyncio.new_event_loop()
    if args.scratch:
        args.scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="capture-overhead-", dir=args.scratch) as root:
        control = Path(root) / "control.json"
        with patch.dict(os.environ, {"KIWOOM_DIAGNOSTIC_WORKLOAD_PATH": str(control)}), \
                patch.object(trace, "control_path", return_value=control), \
                patch("kiwoom_monitor.central_server.diagnostic_workloads.control_path", return_value=control), \
                patch.object(trace, "_deferred_memory_check", return_value={"controlled_fixture": True}), \
                patch.object(socket.socket, "connect", forbidden), \
                patch.object(socket.socket, "connect_ex", forbidden), \
                patch.object(socket, "create_connection", forbidden):
            store = SQLiteQueryStore(Path(":memory:"))
            store.initialize()
            hub = Hub()
            collector = CentralRealtimeCollector(lambda: "", "real", hub, lambda: now[0])
            # Warm parser imports outside capture/timing; discard its accumulator state.
            warm = CentralRealtimeCollector(lambda: "", "real", RealtimeHub(), lambda: origin)
            warm._publish_parsed(inputs[0])
            baseline_rss = rss()
            if args.mode == "on":
                master = _set_tool(control, True, 300)["diagnostic_tool"]["session_id"]
                _set_trace(control, True, 120, expected_session=master)
                trace.start(seconds=60, store_inputs=True, collector_inputs=True, persist_at=time.time() + 3600)
            try:
                result = loop.run_until_complete(measure(store, collector))
                measured_rss = rss()
                trace_metrics = trace.status()
                captured_rows, excluded_rows, event_counts = Counter(), Counter(), Counter()
                if args.mode == "on":
                    with trace._LOCK:
                        captured = list(trace._QUEUE)
                    for event in captured:
                        event_counts[event["event_type"]] += 1
                        if event.get("input_kind") == "message":
                            payload = thaw_payload(event["payload"])
                            captured_rows.update(row["type"] for row in payload["message"]["data"])
                            excluded_rows.update(event.get("excluded_types", {}))
                    if event_counts["operation_start"] != args.store_calls or event_counts["operation_end"] != args.store_calls:
                        raise RuntimeError("store_capture_pairs_missing")
                    if captured_rows["0B"] != args.messages * args.rows:
                        raise RuntimeError("collector_fixture_rows_missing")
                # Hold all payloads in RAM for the entire sample. Shutdown discards only this private fixture.
                if trace_metrics.get("written", 0) != 0 or trace_metrics.get("known_dropped", 0) or trace_metrics.get("input_rejected", 0):
                    raise RuntimeError("controlled_capture_dropped_rejected_or_wrote_payloads")
                if args.mode == "on":
                    trace.stop("server_shutdown", timeout=15)
                    _set_tool(control, False)
                stored = store.load_dataset_snapshots("ranking", "fixture", limit=100)
                if len(stored) != args.store_calls:
                    raise RuntimeError("native_sqlite_results_differ")
                result.update({"source": str(source), "server_build": build_match.group(1),
                    "imported_module": str(imported), "mode": args.mode,
                    "profile": args.profile, "fixture_sha256": digest(inputs), "input_messages": len(inputs),
                    "input_rows": sum(len(m["data"]) for m in inputs), "hub_events": hub.count,
                    "native_store_calls": args.store_calls, "collector_state_sha256": digest({
                        "latest": sorted(collector._latest_snapshots.items()),
                        "minutes": minute_signature(collector, origin, args.rows),
                        "seconds": sorted((str(k), v.as_record()) for k, v in collector._second_trades._bars.items())}),
                    "store_payload_sha256": digest(sorted((r["snapshot_key"], r["payload"]) for r in stored)),
                    "rss_before": baseline_rss, "rss_after": measured_rss,
                    "trace": {key: trace_metrics.get(key) for key in (
                        "accepted", "written", "known_dropped", "input_rejected", "input_coverage",
                        "memory_high_water", "charged_bytes", "copy_ms_total", "copy_ms_max", "queue_high_water")},
                    "captured_collector_rows_by_type": dict(captured_rows),
                    "explicitly_excluded_collector_rows_by_type": dict(excluded_rows),
                    "captured_event_counts": dict(event_counts),
                    "scope": "collector_parser_RAM_and_in_memory_SQLite_store_input_capture_only",
                    "network_access": False, "postgres_access": False, "live_controls_changed": False,
                    "memory_preflight_exercised": False, "durability_exercised": False,
                    "intraday_baseline": False})
                return result
            finally:
                if args.mode == "on":
                    trace.stop("server_shutdown", timeout=15)
                    _set_tool(control, False)
                store.close()
                loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--scratch", type=Path, help="Private test temporary parent, never the live data directory")
    parser.add_argument("--messages", type=int, default=300)
    parser.add_argument("--rows", type=int, default=10)
    parser.add_argument("--rate", type=float, default=100)
    parser.add_argument("--store-calls", type=int, default=12)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--profile", action="append", choices=("0B", "mixed"),
                        help="Can be repeated; defaults to both 0B-only and mixed inputs")
    parser.add_argument("--mode", choices=("off", "on"), default="off")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not (10 <= args.messages <= 3000 and 1 <= args.rows <= 100 and 1 <= args.rate <= 1000
            and 1 <= args.store_calls <= 100 and 1 <= args.rounds <= 5 and args.messages / args.rate <= 45
            and args.messages * args.rows <= 30000):
        parser.error("controlled fixture bounds exceeded")
    if args.worker:
        args.source = args.source[0]
        args.profile = args.profile[-1] if isinstance(args.profile, list) else args.profile
        print(json.dumps(worker(args), ensure_ascii=False))
        return 0
    env = {k: v for k, v in os.environ.items() if not k.startswith(("KIWOOM_", "MONITOR_", "POSTGRES_", "PG", "REPLAY_"))}
    env.pop("PYTHONPATH", None)
    profiles = args.profile or ["0B", "mixed"]
    results = []
    for profile in profiles:
      for round_index in range(args.rounds):
        sources = args.source if round_index % 2 == 0 else list(reversed(args.source))
        for source in sources:
            modes = ("off", "on") if round_index % 2 == 0 else ("on", "off")
            pair = []
            for mode in modes:
                command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--source", source,
                           "--messages", str(args.messages), "--rows", str(args.rows), "--rate", str(args.rate),
                           "--store-calls", str(args.store_calls), "--profile", profile, "--mode", mode]
                if args.scratch:
                    command += ["--scratch", str(args.scratch)]
                completed = subprocess.run(command, env=env, text=True, capture_output=True, timeout=90)
                if completed.returncode:
                    raise RuntimeError("controlled_worker_failed:\n" + completed.stderr)
                item = json.loads(completed.stdout)
                item["round"] = round_index + 1
                pair.append(item)
                results.append(item)
                print(json.dumps({"round": item["round"], "profile": profile, "source": source,
                                  "mode": mode, "producer_ms": item["producer_ms"], "trace": item["trace"]}), flush=True)
            for key in ("fixture_sha256", "collector_state_sha256", "store_payload_sha256", "hub_events", "native_store_calls"):
                if pair[0][key] != pair[1][key]:
                    raise RuntimeError("capture_off_on_parity_failed:" + key)
    references = {}
    for result in results:
        reference_key = (result["round"], result["profile"], result["mode"])
        signature = tuple(result[key] for key in (
            "fixture_sha256", "collector_state_sha256", "store_payload_sha256", "hub_events", "native_store_calls"))
        if reference_key in references and signature != references[reference_key]:
            raise RuntimeError("cross_source_fixture_parity_failed:" + str(reference_key))
        references[reference_key] = signature
    report = {"state": "passed", "functional_fixture_parity": True, "performance_acceptance": "not_decided",
              "intraday_baseline": False, "profiles": profiles, "results": results}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
