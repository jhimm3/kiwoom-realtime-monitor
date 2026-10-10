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
from contextlib import suppress
from collections import Counter
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1]))


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
    # Same read-only Windows working-set probe as the capacity runner.
    from scripts.check_causal_capture_capacity import memory
    values = memory()
    return {"rss_bytes": values['rss_bytes'], "process_peak_rss_bytes": values['peak_rss_bytes']}


class AccountLargeFixture:
    """Controlled native account/VI/Shadow inputs; no broker or authority access."""
    def __init__(self, store, origin, *, distinct_large_inputs=False):
        from kiwoom_monitor.domain.order_contract import AccountBinding, AccountEnvironment, AccountScope, AccountSnapshot
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import AccountRecovery
        self.store, self.origin = store, origin
        # Fixture wall clock only, using the same private native hook as v3 replay.
        self.store._execution_wall_now = lambda: self.origin
        self.ref = 'f9f2a34b-9188-4f8e-9e14-6fbc271c0001'
        self.binding = AccountBinding('profile-1', AccountScope('kiwoom', AccountEnvironment.REAL, self.ref), 3, origin)
        self.recovery = AccountRecovery(AccountSnapshot(self.ref, 10000, 0, {'005930': 3}, origin), ())
        self.large = {'frames': [{'symbol': '005930', 'value': '가' * 3000} for _ in range(950)]}
        self.distinct_large_inputs = distinct_large_inputs
        self.last_large = self.large
        self.encoded_bytes = len(json.dumps(self.large, ensure_ascii=False).encode())
        if self.encoded_bytes <= 8 * 1024**2:
            raise RuntimeError('controlled_large_input_not_large')
        self.counts, self.outcomes, self.latencies = Counter(), [], {}
        settings = {'scope': {'broker': 'kiwoom', 'environment': 'real', 'account_ref': self.ref},
                    'active_profile_id': 'profile-1', 'revision': 2, 'monitor_enabled': True,
                    'mock_order_enabled': False}
        with store._connection() as connection:
            connection.execute('INSERT INTO central_account_registry VALUES(?,?,?,?,?,?)',
                (self.ref, 'kiwoom', 'real', 'private-fixture-fingerprint', origin.isoformat(), 'active'))
            connection.execute('INSERT INTO central_account_binding_revisions VALUES(?,?,?,?,?,?,?,?)',
                ('binding-1', 'profile-1', 'kiwoom', 'real', self.ref, 3, origin.isoformat(), 'broker-read'))
            connection.execute('INSERT INTO central_documents VALUES(?,?,?,?,?)',
                ('server_account_settings', 'kiwoom:real:' + self.ref, 'settings', origin.timestamp(), json.dumps(settings)))

    def batch(self, index):
        from kiwoom_monitor.central_server.diagnostic_account_input import source_execution_owner
        from kiwoom_monitor.central_server.diagnostic_replay_contract import capture_owner
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import AccountBalanceChange
        now = self.origin + timedelta(milliseconds=index * 100)
        run, key = 'private-run', 'mock:' + self.ref
        # Fixed fixture authority is never an operational credential. Capture aliases it.
        token = run + ':private-authority'
        ownership = {'owner_key': key, 'owner_token': token, 'run_id': run}
        def call(method, *args, **kwargs):
            before = time.perf_counter_ns()
            result = getattr(self.store, method)(*args, **kwargs)
            self.latencies.setdefault(method, []).append((time.perf_counter_ns() - before) / 1e6)
            self.counts[method] += 1
            self.outcomes.append((method, result))
            return result
        with capture_owner('account', 'account:overhead', 'account-actor'), source_execution_owner(key, run):
            if not call('acquire_execution_runtime', key, token, now.isoformat(),
                        (now + timedelta(days=1)).isoformat()):
                raise RuntimeError('controlled_lease_acquire_failed')
            call('save_real_account_recovery', self.binding, self.recovery, now, settings_revision=2)
            balance = AccountBalanceChange('005930', 'fixture', 3, 3, 100, 300, 10000, 100,
                                          origin_scope=self.binding.scope)
            call('save_real_account_event', self.binding, 'account_balance', balance, now, settings_revision=2)
            snapshot = {'snapshot_id': 'snapshot-' + str(index), 'environment': 'mock', 'account_ref': self.ref,
                        'as_of': now.isoformat(), 'received_at': now.isoformat()}
            intent = {'intent_id': 'intent-' + str(index), 'run_id': run, 'environment': 'mock',
                      'account_ref': self.ref, 'state': 'CREATED', 'created_at': now.isoformat(), 'updated_at': now.isoformat()}
            event = {'event_id': 'event-' + str(index), 'intent_id': intent['intent_id'], 'state': 'SUBMITTED',
                     'occurred_at': now.isoformat(), 'received_at': now.isoformat()}
            for method, values in (('save_execution_account_snapshot', (snapshot,)),
                                   ('create_execution_intent', (intent,)),
                                   ('append_execution_event', ({**intent, 'state': 'SUBMITTED'}, event))):
                if call(method, *values, ownership=ownership) is not True:
                    raise RuntimeError('controlled_native_write_failed:' + method)
            if not call('release_execution_runtime', key, token):
                raise RuntimeError('controlled_lease_release_failed')
        with capture_owner('market_events', 'vi:overhead', 'vi-actor'):
            vi = {'event_id': 'vi-' + str(index), 'event_key': 'vi-key-' + str(index), 'stock_code': '005930',
                  'event_kind': 'start', 'vi_type': 'dynamic', 'received_at': now.timestamp(), 'available_at': now.timestamp()}
            if call('append_vi_events', [vi]) != 1 or call('append_vi_events', [vi]) != 0:
                raise RuntimeError('controlled_vi_revision_failed')
        with capture_owner('shadow', 'shadow:overhead', 'shadow-actor'):
            document = {**self.large, 'fixture_round': index} if self.distinct_large_inputs else self.large
            call('save_shadow_monitor_state', 'large-monitor', document)
            self.last_large = document

    def signature(self):
        # Native wall-clock storage freshness is excluded; values/revisions are exact.
        tables = ('central_execution_intents', 'central_execution_events', 'central_execution_account_snapshots',
                  'central_execution_runtime_leases', 'central_vi_event_revisions')
        with self.store._connection() as connection:
            values = {name: sorted(connection.execute('SELECT * FROM ' + name).fetchall()) for name in tables}
            values['documents'] = sorted((c, o, k, json.loads(d)) for c, o, k, d in connection.execute(
                "SELECT collection,owner,document_key,document_json FROM central_documents WHERE collection IN "
                "('real_account_recovery','real_account_event','server_account_settings')"))
        large = self.store.load_shadow_monitor_state('large-monitor')
        if large != self.last_large or values['central_execution_runtime_leases']:
            raise RuntimeError('controlled_account_large_content_failed')
        values['large'] = large
        return digest(values)


def worker(args):
    source = Path(args.source).resolve(strict=True)
    app_text = (source / "src/kiwoom_monitor/central_server/app.py").read_text(encoding="utf-8")
    build_match = re.search(r'^SERVER_BUILD\s*=\s*["\']([^"\']+)', app_text, re.M)
    if build_match is None:
        raise RuntimeError("source_build_marker_missing")
    sys.path.insert(0, str(source / "src"))
    from kiwoom_monitor.central_server import diagnostic_trace as trace
    from kiwoom_monitor.central_server.database import SQLiteQueryStore
    from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool, _set_trace
    from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
    from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
    import kiwoom_monitor.central_server.realtime_collector as module
    imported = Path(module.__file__).resolve()
    if not imported.is_relative_to(source / "src"):
        raise RuntimeError("wrong_source_imported")

    origin, inputs = fixture(args.messages, args.rows, args.profile in ("mixed", "account-large"))
    account = None
    transactions = Counter()
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
        async def account_producer():
            if account is None:
                return
            for index in range(0, args.messages, args.account_every):
                await asyncio.sleep(max(0, start + index / args.rate - time.perf_counter()))
                await asyncio.to_thread(account.batch, index)
        account_task = asyncio.create_task(account_producer())
        try:
            for index, message in enumerate(inputs):
                deadline = start + index / args.rate
                await asyncio.sleep(max(0, deadline - time.perf_counter()))
                scheduling.append(max(0, (time.perf_counter() - deadline) * 1000))
                now[0] = origin + timedelta(milliseconds=index * 100)
                before = time.perf_counter_ns()
                collector._publish_parsed(message)  # Same live input boundary, no network/task start.
                producer.append((time.perf_counter_ns() - before) / 1e6)
            collector_elapsed = time.perf_counter() - start
            await account_task
            store_calls = []
            for index in range(args.store_calls):
                payload = {"rows": [{"code": f"{5930 + n:06d}", "rank": n + 1} for n in range(20)]}
                before = time.perf_counter_ns()
                await asyncio.to_thread(store.save_dataset_snapshot, "ranking", "fixture", str(index), payload)
                store_calls.append((time.perf_counter_ns() - before) / 1e6)
        finally:
            account_task.cancel()
            running[0] = False
            try:
                with suppress(asyncio.CancelledError):
                    await account_task
            finally:
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
            class CountedStore(SQLiteQueryStore):
                def _connect(self):
                    connection = super()._connect()
                    def observe(statement):
                        command = statement.split(None, 1)[0].upper()
                        if command in ('BEGIN', 'COMMIT', 'ROLLBACK'):
                            transactions[command] += 1
                    connection.set_trace_callback(observe)
                    return connection
            store = CountedStore(Path(":memory:"))
            store.initialize()
            if args.profile == 'account-large':
                account = AccountLargeFixture(store, origin)
            hub = Hub()
            collector = CentralRealtimeCollector(lambda: "", "real", hub, lambda: now[0])
            # Warm parser imports outside capture/timing; discard its accumulator state.
            warm = CentralRealtimeCollector(lambda: "", "real", RealtimeHub(), lambda: origin)
            warm._publish_parsed(inputs[0])
            baseline_rss = rss()
            if args.mode == "on":
                master = _set_tool(control, True, 300)["diagnostic_tool"]["session_id"]
                _set_trace(control, True, 120, expected_session=master)
                trace.start(seconds=60, store_inputs=True, collector_inputs=True,
                            account_inputs=account is not None, large_inputs=account is not None,
                            account_context_store=store if account else None, persist_at=time.time() + 3600)
            try:
                transactions.clear()
                cpu_start, wall_start = time.process_time(), time.perf_counter()
                result = loop.run_until_complete(measure(store, collector))
                result['process_cpu_seconds'] = time.process_time() - cpu_start
                result['measured_wall_seconds'] = time.perf_counter() - wall_start
                result['native_sqlite_transactions'] = dict(transactions)
                measured_rss = rss()
                trace_metrics = trace.status()
                event_counts = Counter(trace_metrics.get("event_counts", {}))
                captured_collector_payload_events = event_counts["collector_input"]
                expected_collector_payload_events = args.messages + 1  # messages plus one initial-state payload
                if args.mode == "on":
                    expected_store_calls = args.store_calls + (sum(account.counts.values()) if account else 0)
                    if event_counts["operation_start"] != expected_store_calls or event_counts["operation_end"] != expected_store_calls:
                        raise RuntimeError("store_capture_pairs_missing")
                    realtime_coverage = trace_metrics.get("input_coverage", {}).get("realtime", {})
                    if (captured_collector_payload_events != expected_collector_payload_events
                            or realtime_coverage.get("accepted") != expected_collector_payload_events
                            or trace_metrics.get("input_rejected") != 0):
                        raise RuntimeError("collector_fixture_payload_events_missing:" + json.dumps({
                            "event_counts": dict(event_counts),
                            "realtime_coverage": realtime_coverage,
                            "input_rejected": trace_metrics.get("input_rejected"),
                            "input_coverage": trace_metrics.get("input_coverage"),
                        }, ensure_ascii=False, sort_keys=True))
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
                    "native_store_calls": args.store_calls + (sum(account.counts.values()) if account else 0),
                    'dataset_store_calls': args.store_calls, "collector_state_sha256": digest({
                        "latest": sorted(collector._latest_snapshots.items()),
                        "minutes": minute_signature(collector, origin, args.rows),
                        "seconds": sorted((str(k), v.as_record()) for k, v in collector._second_trades._bars.items())}),
                    "store_payload_sha256": digest(sorted((r["snapshot_key"], r["payload"]) for r in stored)),
                    'account_large': None if account is None else {
                        'native_method_counts': dict(account.counts), 'outcomes_sha256': digest(account.outcomes),
                        'stored_contents_sha256': account.signature(), 'large_encoded_bytes': account.encoded_bytes,
                        'call_ms': {key: distribution(value) for key, value in account.latencies.items()},
                        'source_fixture_sha256': digest({'origin': origin.isoformat(), 'account_ref': account.ref,
                                                       'every': args.account_every, 'large': account.large})},
                    "rss_before": baseline_rss, "rss_after": measured_rss,
                    "trace": {key: trace_metrics.get(key) for key in (
                        "accepted", "written", "known_dropped", "input_rejected", "input_coverage",
                        "memory_high_water", "charged_bytes", "copy_ms_total", "copy_ms_max", "queue_high_water",
                        'raw_charged_bytes', 'raw_high_water_bytes', 'packing_events', 'packed_events',
                        'packed_bytes', 'worker_reserved_bytes', 'pack_ms_total', 'pack_ms_max')},
                    'account_fixture_wall_clock': origin.isoformat() if account else None,
                    "expected_collector_input_rows": args.messages * args.rows if args.mode == "on" else 0,
                    "captured_collector_payload_events": captured_collector_payload_events,
                    "expected_collector_payload_events": expected_collector_payload_events if args.mode == "on" else 0,
                    "captured_event_counts": dict(event_counts),
                    "scope": "controlled_collector_account_VI_large_input_RAM_capture" if account else
                             "collector_parser_RAM_and_in_memory_SQLite_store_input_capture_only",
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
    parser.add_argument("--account-every", type=int, default=50, help='Account/VI/large native batch frequency in the controlled account-large profile')
    parser.add_argument("--profile", action="append", choices=("0B", "mixed", "account-large"),
                        help="Can be repeated; defaults to both 0B-only and mixed inputs")
    parser.add_argument("--mode", choices=("off", "on"), default="off")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not (10 <= args.messages <= 3000 and 1 <= args.rows <= 100 and 1 <= args.rate <= 1000
            and 1 <= args.store_calls <= 100 and 1 <= args.rounds <= 5 and args.messages / args.rate <= 45
            and args.messages * args.rows <= 30000 and 10 <= args.account_every <= 3000
            and (args.messages + args.account_every - 1) // args.account_every <= 60):
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
                command += ['--account-every', str(args.account_every)]
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
            if pair[0]['native_sqlite_transactions'] != pair[1]['native_sqlite_transactions']:
                raise RuntimeError('capture_off_on_transaction_parity_failed')
            if profile == 'account-large':
                for key in ('native_method_counts', 'outcomes_sha256', 'stored_contents_sha256', 'large_encoded_bytes', 'source_fixture_sha256'):
                    if pair[0]['account_large'][key] != pair[1]['account_large'][key]:
                        raise RuntimeError('capture_off_on_account_large_parity_failed:' + key)
    references = {}
    for result in results:
        reference_key = (result["round"], result["profile"], result["mode"])
        signature = tuple(result[key] for key in (
            "fixture_sha256", "collector_state_sha256", "store_payload_sha256", "hub_events", "native_store_calls"))
        if result['account_large']:
            signature += tuple(result['account_large'][key] for key in (
                'outcomes_sha256', 'stored_contents_sha256', 'source_fixture_sha256'))
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
