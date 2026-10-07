"""Private, offline capacity probe. No operational controls, DB or network access.

Reports a specified workload envelope; it cannot establish an unknown market maximum.
The receiver/parser/hub/delivery boundaries are native. REST/catalog templates use
explicit conservative shapes, not claimed historical response equivalence.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]


def memory():
    if sys.platform == 'linux':
        status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
        host = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines() if ':' in line)
        cgroup = None
        for limit, used in (('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory.current'),
                            ('/sys/fs/cgroup/memory/memory.limit_in_bytes', '/sys/fs/cgroup/memory/memory.usage_in_bytes')):
            try:
                maximum = Path(limit).read_text().strip()
                cgroup = None if maximum == 'max' else int(maximum)-int(Path(used).read_text())
                break
            except (OSError, ValueError):
                continue
        return {'rss_bytes': int(status['VmRSS'].split()[0])*1024,
                'peak_rss_bytes': int(status['VmHWM'].split()[0])*1024,
                'host_available_bytes': int(host['MemAvailable'].split()[0])*1024,
                'container_headroom_bytes': cgroup}
    # Read-only process working set on Windows; NAS gates use /proc above.
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD),
                    ('peak', ctypes.c_size_t), ('working', ctypes.c_size_t),
                    ('paged_peak', ctypes.c_size_t), ('paged', ctypes.c_size_t),
                    ('nonpaged_peak', ctypes.c_size_t), ('nonpaged', ctypes.c_size_t),
                    ('pagefile', ctypes.c_size_t), ('pagefile_peak', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    values = Counters()
    values.cb = ctypes.sizeof(values)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(values), values.cb):
        raise OSError(ctypes.get_last_error())
    return {'rss_bytes': values.working, 'peak_rss_bytes': values.peak, 'host_available_bytes': None}


class PrivateStore:
    """Typed native capture boundary with an in-memory sink, never a DB benchmark."""
    def __init__(self):
        from kiwoom_monitor.central_server.diagnostic_replay_contract import install_store_capture
        self.calls = Counter()
        self.documents = []
        self.cache = {}
        install_store_capture(self)

    def load_query(self, cache_key):
        self.calls['load_query'] += 1
        return self.cache.get(cache_key)

    def save_query(self, cache_key, api_id, expires_at, value):
        self.calls['save_query'] += 1
        self.cache[cache_key] = value

    def load_documents(self, collection, owner='', limit=1000, offset=0, *, subject_prefix=None):
        self.calls['load_documents'] += 1
        return self.documents

    def replace_documents(self, collection, values):
        self.calls['replace_documents'] += 1
        self.documents = [{'key': row['key'], 'document': row['document']} for row in values]

    def save_dataset_snapshots(self, values):
        self.calls['save_dataset_snapshots'] += 1


class PrivateClient:
    def __init__(self, bars):
        self.bars, self.calls = bars, Counter()

    def request_with_continuation(self, api_id, path, body, **kwargs):
        self.calls[api_id] += 1
        if api_id in ('ka10080', 'ka10081'):
            payload = {'return_code': 0, 'rows': self.bars}
        else:
            payload = {'return_code': 0, 'stk_cd': body['stk_cd'], 'cur_prc': '70000'}
        return payload, False, ''


class PrivateSocket:
    async def send(self, value):
        pass  # Native subscription serialization is exercised without a socket.


async def durable_check(trace, identifier, timeout):
    """Move only the private test deadline; verify all durable references on disk."""
    trace.stop(timeout=.01)
    deadline = time.monotonic() + timeout
    while trace.status()['state'] != 'awaiting_persistence':
        if time.monotonic() > deadline:
            raise RuntimeError('private_capture_did_not_seal')
        await asyncio.sleep(.01)
    held = trace.status()
    if held['written'] or held.get('known_dropped') or held.get('input_rejected'):
        raise RuntimeError('private_capture_not_lossless_before_persistence')
    with trace._LOCK:
        trace._SESSION['persist_at'] = time.time() - 1
    trace._WAKE.set()
    started = time.monotonic()
    while trace.status()['state'] not in ('complete', 'incomplete', 'failed'):
        if time.monotonic() > deadline:
            raise RuntimeError('private_persistence_timeout')
        await asyncio.sleep(.05)
    final = trace.status()
    if final['state'] != 'complete' or final['accepted'] != final['written'] or final['charged_bytes']:
        raise RuntimeError('private_persistence_not_complete')
    directory = trace._directory() / identifier
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest['state'] != 'complete' or manifest['written'] != final['written']:
        raise RuntimeError('private_final_manifest_mismatch')
    seq, hydrated, jsonl_bytes = 0, [], 0
    for chunk in final['chunks']:
        content = trace.chunk_bytes(identifier, chunk['name'])  # Includes checksum validation.
        jsonl_bytes += len(content)
        for line in content.splitlines():
            event = json.loads(line)
            seq += 1
            if event['seq'] != seq:
                raise RuntimeError('private_persistence_sequence_gap')
            reference = event.pop('payload_ref', None)
            if reference is not None:
                event['payload'] = json.loads(trace.payload_bytes(identifier, reference))
            hydrated.append(event)
    if seq != final['written']:
        raise RuntimeError('private_persistence_event_count_mismatch')
    # Exercise the same pair/approval readers, not just a checksum-only gate.
    from kiwoom_monitor.central_server.diagnostic_rest_input import read_request_tape
    from kiwoom_monitor.central_server.diagnostic_top20_lifecycle_input import read_realtime_tape
    from kiwoom_monitor.central_server.diagnostic_top20_input import compile_delivery_coverage
    from kiwoom_monitor.central_server.diagnostic_replay_contract import thaw_operation_arguments
    rest = read_request_tape(hydrated)
    lifecycle = read_realtime_tape(hydrated)
    components = {row['producer_component'] for row in hydrated
                  if row['event_type'] == 'top20_delivery'}
    deliveries = sum(len(compile_delivery_coverage(hydrated, trace_id=identifier,
                         component=component)['delivery_ids']) for component in components)
    operation_starts = {row['operation_id']: row for row in hydrated if row.get('event_type') == 'operation_start'}
    operation_ends = {row['operation_id']: row for row in hydrated if row.get('event_type') == 'operation_end'}
    catalog_operations = 0
    catalog_rows = None
    for operation_id, start in operation_starts.items():
        end = operation_ends.get(operation_id)
        if start.get('method') == 'replace_documents' and start.get('collection') == 'stock_catalog':
            if not end or any(end.get(key) != start.get(key) for key in
                               ('method', 'collection', 'payload_profile', 'codec_version')):
                raise RuntimeError('catalog_operation_pair_mismatch')
            arguments = thaw_operation_arguments(start)
            catalog_rows = len(arguments['values']) - 1
            if catalog_rows < 1:
                raise RuntimeError('catalog_operation_rows_mismatch')
            catalog_operations += 1
    if trace.status(identifier).get('payload_capture', {}).get('store_inputs') and catalog_operations != 1:
        raise RuntimeError('catalog_operation_profile_missing_or_duplicate')
    file_bytes = sum(path.stat().st_size for path in directory.iterdir() if path.is_file())
    return {'state': final['state'], 'events': seq, 'chunks': len(final['chunks']),
            'jsonl_bytes': jsonl_bytes, 'payload_and_other_bytes': file_bytes-jsonl_bytes,
            'total_file_bytes': file_bytes, 'manifest_bytes': (directory / 'manifest.json').stat().st_size,
            'storage_limit_bytes': final['storage_limit_bytes'], 'elapsed_seconds': time.monotonic()-started,
            'native_rest_calls': len(rest['calls']), 'catalogs': len(rest['catalogs']),
            'catalog_store_operations': catalog_operations,
            'catalog_store_rows': catalog_rows or 0,
            'catalog_profile_roundtrip_verified': catalog_operations == 1,
            'subscriptions': len(lifecycle['subscriptions']), 'closed_deliveries': deliveries,
            'chunk_checksums_sequences_and_payloads_verified': True,
            'write_bytes_per_second': final['write_bytes_per_second'],
            'persistence_throttle_seconds': final['persistence_throttle_seconds']}


def distribution(values):
    values = sorted(values)
    return {'count': len(values), 'p50': values[(len(values)-1)//2],
            'p95': values[min(len(values)-1, int(len(values)*.95))], 'max': values[-1]}


def classify_capacity_stop(state):
    reasons = state.get('drop_reasons', {})
    rejected = state.get('input_rejected_reasons', {})
    # A profile/type/node error must never become an accepted saturation result
    # merely because a later input also reached the retained-memory budget.
    if any(count and reason != 'capture_memory_full' for reason, count in rejected.items()):
        return 'input_rejected'
    allowed_drops = {'event_capacity', 'memory_budget', 'payload_memory_budget'}
    if any(count and reason not in allowed_drops for reason, count in reasons.items()):
        return 'unexpected_drop'
    if rejected.get('capture_memory_full'):
        return 'copy_reservation_budget'
    if rejected:
        return 'input_rejected'
    for reason in ('event_capacity', 'memory_budget', 'payload_memory_budget'):
        if reasons.get(reason):
            return reason
    return 'unexpected_drop'


def memory_summary(before, after, samples):
    observations = [before, *(row['memory'] for row in samples), after]
    def extrema(key, select):
        values = [row[key] for row in observations if row.get(key) is not None]
        return select(values) if values else None
    return {
        'rss_bytes_max': extrema('rss_bytes', max),
        'peak_rss_bytes_max': extrema('peak_rss_bytes', max),
        'host_available_bytes_min': extrema('host_available_bytes', min),
        'container_headroom_bytes_min': extrema('container_headroom_bytes', min),
        'observations': len(observations),
    }


async def run(args):
    from kiwoom_monitor.central_server import diagnostic_trace as trace
    from kiwoom_monitor.central_server.diagnostic_replay_contract import freeze_payload
    from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool, _set_trace
    from kiwoom_monitor.central_server.diagnostic_top20_input import consumed_delivery
    from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
    from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
    from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
    from kiwoom_monitor.central_server.autonomous_top20 import AutonomousTop20Service
    from kiwoom_monitor.central_server.diagnostic_replay_contract import capture_owner
    from kiwoom_monitor.central_server.diagnostic_top20_lifecycle_input import record_ack

    origin = datetime(2026, 10, 8, 9, tzinfo=timezone(timedelta(hours=9)))
    now = [origin]
    hub = RealtimeHub()
    subscriber = hub.connect(capture_component='autonomous_top20:capacity-probe')
    codes = tuple(f'{5930+i:06d}' for i in range(args.rows))
    subscriber.codes = set(codes)
    subscriber.program_codes = set(codes)
    collector = CentralRealtimeCollector(lambda: '', 'real', hub, lambda: now[0])
    def message(index):
        return {'trnm': 'REAL', 'data': [{'type': '0B', 'item': code+'_AL',
            'values': {'10': str(70000+index%7), '15': '1', '14': str(70000*(index+1)),
                       '20': now[0].strftime('%H%M%S')}} for code in codes]}
    def consume():
        count = 0
        while not subscriber.queue.empty():
            with consumed_delivery(subscriber, subscriber.queue.get_nowait()):
                count += 1
        return count
    collector._publish_parsed(message(0))
    consume()
    before = memory()
    counts = Counter()
    latencies = []
    mixed_latencies = []
    samples = []
    stop_reason = 'requested_envelope_completed'
    with tempfile.TemporaryDirectory(prefix='causal-capacity-', dir=args.scratch) as temp, ExitStack() as stack:
        control = Path(temp) / 'control.json'
        stack.enter_context(patch.object(trace, 'control_path', return_value=control))
        stack.enter_context(patch('kiwoom_monitor.central_server.diagnostic_workloads.control_path', return_value=control))
        stack.enter_context(patch.object(trace, '_DEFERRED_MEMORY_LIMIT', args.memory_gib*1024**3))
        stack.enter_context(patch.object(trace, '_CAPACITY', args.event_capacity))
        if sys.platform != 'linux':
            stack.enter_context(patch.object(trace, '_deferred_memory_check', return_value={'windows_probe_only': True}))
        def forbidden(*a, **kw):
            raise RuntimeError('capacity_probe_network_forbidden')
        for name in ('connect', 'connect_ex'):
            stack.enter_context(patch.object(socket.socket, name, forbidden))
        stack.enter_context(patch.object(socket, 'create_connection', forbidden))
        if args.mode == 'on':
            session = _set_tool(control, True, 7200)['diagnostic_tool']['session_id']
            _set_trace(control, True, 7000, expected_session=session)
            trace.start(seconds=args.capture_seconds, store_inputs=True, collector_inputs=True, top20_inputs=True,
                        persist_at=time.time()+7200)
        bars = [{'cntr_tm': '20261008090000', 'cur_prc': str(70000+i), 'open_pric': '70000',
                 'high_pric': '71000', 'low_pric': '69000', 'trde_qty': '100000',
                 'acc_trde_qty': str(1000000+i), 'acc_trde_prica': str(70000000000+i)} for i in range(900)]
        catalog = tuple((f'{i:06d}', '대표 종목 이름 '+str(i), 'KOSPI') for i in range(args.catalog_rows))
        store, client = PrivateStore(), PrivateClient(bars)
        broker = CentralRestBroker(client, store)
        service = AutonomousTop20Service(broker, hub, store, catalog_loader=lambda: catalog, now_provider=lambda: now[0])
        async def mixed(index):
            with capture_owner('top20', subscriber.capture_component, 'capacity-mixed'):
                if index == 0:
                    await service._ensure_market_catalog(origin.date().isoformat())
                    counts['catalog_loads'] += 1
                hub.update_subscription(subscriber, list(codes), [], program_codes=list(codes))
                groups = await collector._send_subscription(PrivateSocket(), 'KRX', codes, (), codes, ())
                approval = None
                for group in groups:
                    approval = record_ack(collector, {'trnm': 'REG', 'grp_no': group, 'return_code': 0})
                hub.set_upstream_ready(codes, True, approval=approval)
                hub.publish({'type': 'subscription_ready'})
                counts['subscription_batches'] += 1
                counts['native_delivery_consumed'] += consume()
                for api, path in (('ka10080', '/api/dostk/chart'), ('ka10081', '/api/dostk/chart'),
                                  ('ka10001', '/api/dostk/stkinfo'), ('ka10100', '/api/dostk/stkinfo')):
                    # Distinct keys force the specified external response envelope.
                    await broker.request(api, path, {'stk_cd': codes[0], 'probe_round': index})
                store.save_dataset_snapshots([('ranking', 'fixture', str(index),
                    {'rows': [{'code': code, 'rank': rank+1} for rank, code in enumerate(codes)]}, None)])
            collector._publish_parsed({'trnm': 'REAL', 'data': [
                {'type': '0w', 'item': codes[0]+'_AL', 'values': {'20': '090000', '210': '1', '211': '2', '212': '3', '213': '4'}},
                {'type': '0J', 'item': '001', 'values': {'20': '090000', '10': '2500.1', '14': '100'}},
                {'type': '0U', 'item': '101', 'values': {'20': '090000', '10': '800.5'}},
                {'type': '0s', 'item': '', 'values': {'215': '3', '20': '090000', '214': '0'}}]})
            counts['native_delivery_consumed'] += consume()
            counts['mixed_rounds'] += 1
        try:
            for index in range(args.messages):
                now[0] = origin + timedelta(milliseconds=index*100)
                if args.mixed_every and index % args.mixed_every == 0:
                    started = time.perf_counter_ns()
                    await mixed(index)
                    mixed_latencies.append((time.perf_counter_ns()-started)/1e6)
                started = time.perf_counter_ns()
                collector._publish_parsed(message(index))
                counts['native_delivery_consumed'] += consume()
                latencies.append((time.perf_counter_ns()-started)/1e6)
                counts['trade_messages'] += 1
                if index % 100 == 0:
                    state = trace.status()
                    observed_memory = memory()
                    samples.append({'messages': index+1, 'memory': observed_memory,
                                    'charge': state.get('charged_bytes'), 'events': state.get('accepted')})
                    if index % 1000 == 0 and args.progress:
                        print(json.dumps({'progress': samples[-1]}), flush=True)
                    headrooms = [observed_memory.get(key) for key in ('host_available_bytes', 'container_headroom_bytes')]
                    if any(value is not None and value < args.reserve_gib*1024**3 for value in headrooms):
                        stop_reason = 'memory_headroom_guard'
                        break
                    if state.get('input_rejected', 0):
                        stop_reason = classify_capacity_stop(state)
                        break
                    if state.get('known_dropped', 0):
                        stop_reason = classify_capacity_stop(state)
                        break
                if args.mode == 'on':
                    with trace._LOCK:
                        if trace._SESSION['input_rejected']:
                            stop_reason = classify_capacity_stop(trace._SESSION)
                            break
                        if trace._SESSION['known_dropped']:
                            stop_reason = classify_capacity_stop(trace._SESSION)
                            break
            state = trace.status()
            event_types = Counter()
            group_charge = Counter()
            rejected_inputs = []
            if args.mode == 'on':
                trace.stop(timeout=0)
                seal_deadline = time.monotonic() + 30
                while time.monotonic() < seal_deadline:
                    state = trace.status()
                    if state['state'] in ('failed', 'interrupted') or (
                            state['state'] == 'awaiting_persistence' and not state.get('raw_charged_bytes', 0)
                            and not state.get('packing_events', 0)):
                        break
                    await asyncio.sleep(.01)
                event_types.update(state.get('event_counts', {}))
                group_charge.update(state.get('admitted_raw_bytes_by_event', {}))
                if state.get('written') != 0:
                    raise RuntimeError('deferred_probe_wrote_events_early')
            # Upper-shape payload accounting for response/catalog fan-out, before
            # disk deduplication; every response copy remains retained in RAM.
            shapes = {'REST_900_row_page': freeze_payload({'response': {'rows': bars}}).charge,
                      'catalog_stocks': freeze_payload({'rows': catalog}).charge}
            per_message_charge = state.get('charged_bytes', 0)/max(1, counts['trade_messages'])
            per_message_events = state.get('accepted', 0)/max(1, counts['trade_messages'])
            retained_memory = memory()
            persistence = await durable_check(trace, state['trace_id'], args.persist_timeout) if args.persist else None
            result = {'capture_seconds': args.capture_seconds, 'synthetic_input_span_seconds': args.messages / 10, 'mode': args.mode, 'memory_gib': args.memory_gib, 'event_capacity': args.event_capacity,
                'stop_reason': stop_reason, 'mixed_every_messages': args.mixed_every, 'catalog_rows': args.catalog_rows,
                'messages_completed': len(latencies), 'rows_per_message': args.rows, 'native_counts': dict(counts),
                'latency_ms': distribution(latencies), 'mixed_latency_ms': distribution(mixed_latencies) if mixed_latencies else None,
                'native_store_method_counts': dict(store.calls), 'native_transport_counts': dict(client.calls),
                'before': before, 'after': retained_memory, 'after_persistence': memory() if args.persist else None,
                'memory_summary': memory_summary(before, retained_memory, samples),
                'headroom_guard_bytes': args.reserve_gib*1024**3,
                'capture_start_required_headroom_bytes': (args.memory_gib+1)*1024**3 if args.mode == 'on' else None,
                'memory_samples': samples, 'rejected_input_details': rejected_inputs,
                'trace': {k: state.get(k) for k in ('state','schema_version','accepted','written','known_dropped',
                    'input_rejected','drop_reasons','input_rejected_reasons','charged_bytes','memory_high_water','copy_ms_max')},
                'event_types': dict(event_types), 'admitted_raw_bytes_by_event': dict(group_charge),
                'accounting_note': 'admitted raw bytes are cumulative admissions, not retained packed RAM bytes',
                'ram_packing': {key: state.get(key) for key in ('raw_charged_bytes', 'raw_high_water_bytes',
                    'packed_events', 'packed_bytes', 'packed_segments', 'pack_ms_max', 'pack_ms_total', 'worker_reserved_bytes')},
                'response_payload_shapes': shapes, 'charge_per_message_including_delivery': per_message_charge,
                'estimated_tick_rate_at_memory_limit_for_capture': ((args.memory_gib*1024**3-24*1024**2)/max(1,per_message_charge)*args.rows/args.capture_seconds if args.mode == 'on' else None),
                'estimated_tick_rate_at_event_limit_for_capture': (args.event_capacity/max(1,per_message_events)*args.rows/args.capture_seconds if args.mode == 'on' else None),
                'estimated_tick_rate_at_memory_limit_65m': ((args.memory_gib*1024**3-24*1024**2)/max(1,per_message_charge)*args.rows/3900
                    if args.mode == 'on' else None),
                'estimated_tick_rate_at_event_limit_65m': (args.event_capacity/max(1,per_message_events)*args.rows/3900
                    if args.mode == 'on' else None),
                'persistence': persistence,
                'scope': 'native_collector_hub_delivery_controlled_capacity_probe',
                'REST_catalog_execution_measured': bool(counts['mixed_rounds']), 'market_maximum_known': False,
                'native_consumer_logic_replayed': False, 'store_sink': 'private_in_memory_not_database',
                'capacity_acceptance': 'not_approved',
                'network_access': False, 'database_access': False, 'live_controls_changed': False}
            print(json.dumps(result), flush=True)
            if args.output:
                args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
            saturated = stop_reason in {
                'memory_budget', 'payload_memory_budget', 'copy_reservation_budget', 'event_capacity'}
            if (stop_reason in {'memory_headroom_guard', 'input_rejected', 'unexpected_drop'}
                    or args.require_limit and not saturated
                    or not args.require_limit and stop_reason != 'requested_envelope_completed'):
                raise RuntimeError('private_capacity_evidence_insufficient')
        finally:
            await broker.close()
            if args.mode == 'on':
                trace.stop('server_shutdown', timeout=20)
                _set_tool(control, False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('off','on'), required=True)
    parser.add_argument('--capture-seconds', type=int, default=3900, help='Requested recorder duration; synthetic timestamps advance 100ms per message')
    parser.add_argument('--messages', type=int, default=1000)
    parser.add_argument('--rows', type=int, default=20)
    parser.add_argument('--memory-gib', type=int, choices=(4,8,12,15,16), default=4,
                        help='Private probe budget only; never changes operational recorder defaults')
    parser.add_argument('--event-capacity', type=int, choices=(1000000,5000000), default=1000000)
    parser.add_argument('--scratch', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--mixed-every', type=int, default=0, help='Add catalog once and native REST/subscription/store round every N messages')
    parser.add_argument('--catalog-rows', type=int, default=5000)
    parser.add_argument('--reserve-gib', type=int, default=4)
    parser.add_argument('--require-limit', action='store_true')
    parser.add_argument('--progress', action='store_true')
    parser.add_argument('--persist', action='store_true', help='Small lossless mixed probe only; use real paced writes and verify all chunks/payloads')
    parser.add_argument('--persist-timeout', type=int, default=180)
    args = parser.parse_args()
    if not 60 <= args.capture_seconds <= 7200:
        parser.error('capture duration must be from 60 to 7200 seconds')
    if not 1 <= args.messages <= 250000 or not 1 <= args.rows <= 100:
        parser.error('controlled capacity bounds exceeded')
    if args.mixed_every < 0 or not 1 <= args.catalog_rows <= 5000 or not 1 <= args.reserve_gib <= 8 or args.persist_timeout < 1:
        parser.error('invalid private probe configuration')
    if args.persist and (args.mode != 'on' or args.messages > 1000 or not args.mixed_every or args.require_limit):
        parser.error('persistence verification requires a bounded lossless mixed ON probe')
    asyncio.run(run(args))


if __name__ == '__main__':
    main()
