"""Offline owned TOP20/peer fixture experiment, with explicit coverage limits.

Hydrated metadata is checked here; file/chunk checksum verification belongs to
the source reader. This entry is not wired to a live run API or performance gate.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import platform

from .diagnostic_recorded_execution import _prepare
from .diagnostic_replay_baseline import ReplayDatabaseLease, _ReplayStore
from .diagnostic_replay_contract import compile_recorded_plan, freeze_payload, thaw_payload
from .diagnostic_replay_runtime import ReplayRuntimeScope
from .diagnostic_rest_input import RequestTapeClient, read_request_tape
from .diagnostic_top20_execution import build_top20_session, execute_top20_session, prepare_top20_source_inputs
from .diagnostic_top20_lifecycle_input import SubscriptionTape, compile_lifecycle_descendants, read_realtime_tape
from .diagnostic_top20_outbox import Top20ReplayOutbox, restore_top20_replay_baseline
from .diagnostic_top20_seed import Top20FixtureClock, seal_top20_cold_fixture
from .diagnostic_trace import validate_top20_capture_manifest


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _implementation_identity():
    # Preflight only, outside the measured timeline. The inactive candidate's
    # complete package binds indirect consumers too; no source body is reported.
    root = Path(__file__).resolve().parents[1]
    digest, count, size = hashlib.sha256(), 0, 0
    for path in sorted(root.rglob('*.py')):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('top20_owned_code_source_invalid')
        value = path.read_bytes()
        count, size = count+1, size+len(value)
        if count > 4096 or size > 64*1024*1024:
            raise ValueError('top20_owned_code_source_invalid')
        name = path.relative_to(root).as_posix().encode()
        digest.update(len(name).to_bytes(4, 'big')+name+len(value).to_bytes(8, 'big')+value)
    if not count:
        raise ValueError('top20_owned_code_source_invalid')
    return {'package_source_sha256': digest.hexdigest(), 'python': platform.python_version(),
            'source_files': count, 'scope': 'candidate_package_source'}


def compile_top20_session_plan(events, manifest, *, component, source_hub_component,
        source_collector_component, include_top20=True, include_peer_workloads=(),
        exclude_peer_workloads=(), end_seconds):
    """Close TOP20 and collector sink frontiers before any reset/native write."""
    if (type(events) is not list or not 1 <= len(events) <= 50000 or type(manifest) is not dict
            or type(include_top20) is not bool
            or any(type(value) is not str or not value or len(value) > 160 for value in
                   (component, source_hub_component, source_collector_component))
            or type(end_seconds) not in (int, float) or not math.isfinite(end_seconds)
            or not 0 < end_seconds <= 600):
        raise ValueError('top20_owned_selection_invalid')
    for mask in (include_peer_workloads, exclude_peer_workloads):
        if (type(mask) is not tuple or any(type(value) is not str or not value for value in mask)
                or len(set(mask)) != len(mask)):
            raise ValueError('top20_owned_peer_mask_invalid')
    if set(include_peer_workloads) & set(exclude_peer_workloads):
        raise ValueError('top20_owned_peer_mask_invalid')
    freeze_payload(events, maximum_bytes=32 * 1024 * 1024)
    validate_top20_capture_manifest(manifest, events, trace_id=manifest.get('trace_id'))
    started = manifest['started_mono_ns']
    if end_seconds * 1e9 > manifest['finished_mono_ns'] - started:
        raise ValueError('top20_owned_window_out_of_bounds')
    realtime = read_realtime_tape(events)
    if realtime['trace_id'] != manifest['trace_id'] or set(realtime['initial']) != {source_hub_component}:
        raise ValueError('top20_owned_hub_identity_invalid')
    # This first native profile owns one TOP20 subscription contributor. An
    # additional native producer needs its own lifecycle input adapter; a DB
    # peer is not allowed to stand in for its subscription contribution.
    for row in events:
        if row.get('event_type') == 'top20_realtime_input' and row.get('payload'):
            value = thaw_payload(row['payload'])
            if any(item['component'] != component for item in value.get('subscriptions', ())):
                raise ValueError('top20_owned_subscription_peer_unsupported')
    lifecycle = compile_lifecycle_descendants(events, component=component, include_top20=include_top20)
    collector_inputs = [row for row in events if row.get('event_type') == 'collector_input']
    if {row.get('producer_component') for row in collector_inputs} != {source_collector_component}:
        raise ValueError('top20_owned_collector_identity_invalid')
    hybrid = compile_recorded_plan(events, started_mono_ns=started, window_start_seconds=0,
        window_end_seconds=end_seconds, mode='collector_with_background',
        collector_components=(source_collector_component,))
    ordinary = compile_recorded_plan(events, started_mono_ns=started, window_start_seconds=0,
        window_end_seconds=end_seconds)
    top20_sinks = set(lifecycle['replaced_operation_ids']) | set(lifecycle['excluded_operation_ids'])
    collector_sinks = set(hybrid.replaced_operation_ids)
    starts = {row['operation_id']: row for row in events if row.get('event_type') == 'operation_start'}
    peers = [identifier for identifier in ordinary.operation_ids
             if identifier not in top20_sinks | collector_sinks]
    for identifier in peers:
        row = starts[identifier]
        if row.get('actor_known') is not True or row.get('shared_owner_components'):
            raise ValueError('top20_owned_peer_owner_unknown_or_shared')
        if row.get('producer_component') == source_collector_component:
            # Unclassified collector effects cannot become background peers.
            raise ValueError('top20_owned_collector_effect_unsupported')
    known = {starts[identifier]['workload_id'] for identifier in peers}
    if (set(include_peer_workloads) | set(exclude_peer_workloads)) - known:
        raise ValueError('top20_owned_peer_workload_not_observed')
    selected = (set(include_peer_workloads) if include_peer_workloads else known) - set(exclude_peer_workloads)
    selected_peers = tuple(identifier for identifier in peers if starts[identifier]['workload_id'] in selected)
    source_inputs = []
    for row in events:
        is_collector = row.get('event_type') == 'collector_input' and row.get('input_kind') in {'message', 'capture_gap'}
        is_market = (row.get('event_type') == 'top20_realtime_input'
                     and row.get('input_kind') == 'market_operation')
        if not (is_collector or is_market):
            continue
        at = row.get('entered_mono_ns', row['mono_ns'])
        if (at - started) / 1e9 < end_seconds:
            source_inputs.append({'input_id': row['input_id'], 'kind': row['input_kind'],
                'entered_mono_ns': at, 'seq': row['seq'], 'payload': thaw_payload(row['payload'])})
    source_inputs.sort(key=lambda item: (item['entered_mono_ns'], item['seq']))
    peer_plan = replace(ordinary, operation_ids=selected_peers,
        excluded_operation_ids=tuple(identifier for identifier in ordinary.operation_ids
                                     if identifier not in selected_peers))
    return {'peer_plan': peer_plan, 'source_inputs': source_inputs,
        'replaced_top20_operations': tuple(sorted(top20_sinks)) if include_top20 else (),
        'excluded_top20_operations': () if include_top20 else tuple(sorted(top20_sinks)),
        'replaced_collector_operations': tuple(sorted(collector_sinks)),
        'excluded_peer_operations': tuple(identifier for identifier in peers if identifier not in selected_peers),
        'source_integrity': 'hydrated_manifest_checked', 'file_checksums_verified': False}


async def execute_owned_top20_fixture(lease, baseline_id, *, events, manifest, task_bindings,
        component, source_hub_component, source_collector_component, outbox_parent, end_seconds,
        window_start_seconds=0.0, include_top20=True, include_peer_workloads=(),
        exclude_peer_workloads=(), peer_concurrency=8, minute_backfill_enabled=True, drain_timeout=60.0):
    """Caller retains the entered dedicated lease through failures/quarantine."""
    if (type(lease) is not ReplayDatabaseLease or lease.baseline_version != 2
            or type(lease._cache_clock) is not Top20FixtureClock or lease._cache_clock.armed
            or not lease._active or lease.connection is None
            or type(end_seconds) not in (int, float) or not math.isfinite(end_seconds)
            or not 0 < end_seconds <= 600
            or type(window_start_seconds) not in (int, float) or not math.isfinite(window_start_seconds)
            or not 0 <= window_start_seconds < end_seconds
            or type(peer_concurrency) is not int or not 1 <= peer_concurrency <= 16
            or type(drain_timeout) not in (int, float) or not math.isfinite(drain_timeout)
            or not 0 < drain_timeout <= 300 or type(minute_backfill_enabled) is not bool):
        raise ValueError('top20_owned_fixture_resources_invalid')
    # Freeze source inputs/selection before the first baseline reset. No async
    # yield precedes this copy; callers cannot mutate a running experiment tape.
    from .diagnostic_trace import input_token
    if any(input_token(option) is not None for option in ('store_inputs', 'collector_inputs', 'top20_inputs')):
        raise ValueError('top20_owned_fixture_capture_must_be_off')
    events, manifest, task_bindings = copy.deepcopy((events, manifest, task_bindings))
    plan = compile_top20_session_plan(events, manifest, component=component,
        source_hub_component=source_hub_component, source_collector_component=source_collector_component,
        include_top20=include_top20, include_peer_workloads=include_peer_workloads,
        exclude_peer_workloads=exclude_peer_workloads, end_seconds=end_seconds)
    clock = lease._cache_clock
    if (type(manifest.get('started_at')) not in (int, float)
            or manifest['started_at'] != clock.origin.timestamp()):
        raise ValueError('top20_owned_source_clock_mismatch')
    client = RequestTapeClient(read_request_tape(events))
    bindings = client.task_bindings(task_bindings)
    prepared = prepare_top20_source_inputs(plan['source_inputs'], clock=clock,
        started_mono_ns=manifest['started_mono_ns'], end_seconds=end_seconds)
    _prepare(_ReplayStore(lease), events, plan['peer_plan'])
    implementation = _implementation_identity()
    # Reject invalid file resources before touching the DB baseline.
    outbox, runtime = Top20ReplayOutbox(outbox_parent), ReplayRuntimeScope()
    baseline = lease.restore(baseline_id)
    service, broker, collector = build_top20_session(lease.store(), clock, client, bindings,
        outbox_path=outbox.path, minute_backfill_enabled=minute_backfill_enabled)
    with lease.connection.cursor() as cursor:
        found_id, baseline_manifest = lease._baseline(cursor)
    seed = seal_top20_cold_fixture(service, clock,
        baseline={'baseline_id': found_id, 'manifest': baseline_manifest},
        source_manifest=manifest, outbox_fixture=outbox)
    identity = {'baseline_id': baseline_id, 'ram_seed_id': seed.ram_seed_id,
        'implementation': implementation,
        'source_manifest_hash': _hash(manifest), 'source_events_hash': _hash(events),
        'task_bindings': task_bindings, 'roles': {'top20': component, 'hub': source_hub_component,
                                              'collector': source_collector_component},
        'selection': {'include_top20': include_top20, 'include_peer_workloads': include_peer_workloads,
            'exclude_peer_workloads': exclude_peer_workloads, 'window_start_seconds': window_start_seconds,
            'end_seconds': end_seconds, 'mask_applies_from_seconds': 0},
        'outbox': outbox.identity(), 'peer_concurrency': peer_concurrency,
        'minute_backfill_enabled': minute_backfill_enabled}
    lease.bind_runtime(runtime)
    try:
        result = await execute_top20_session(service, broker, collector, clock=clock, runtime=runtime,
            bindings=bindings, subscription_tape=SubscriptionTape(read_realtime_tape(events)),
            source_hub_component=source_hub_component, source_component=component,
            prepared_inputs=prepared, started_mono_ns=manifest['started_mono_ns'], end_seconds=end_seconds,
            window_start_seconds=window_start_seconds, include_top20=include_top20,
            outbox_fixture=outbox, drain_timeout=drain_timeout, peer_events=events,
            peer_operation_ids=plan['peer_plan'].operation_ids, peer_concurrency=peer_concurrency)
        runtime.require_drained()
        if lease.status()['owned_connections']:
            raise RuntimeError('top20_owned_connections_not_drained')
        with lease.connection.cursor() as cursor:
            result['final_tables'] = lease._tables_digest(cursor, 'public')
            result['final_sequences'] = lease._sequences(cursor)
        result.update(experiment_id=_hash(identity), experiment_identity=identity, baseline=baseline,
            frontier={key: value for key, value in plan.items() if key not in {'source_inputs', 'peer_plan'}},
            source_integrity=plan['source_integrity'], file_checksums_verified=False,
            final_database_snapshot_verified=True, source_state_equivalent=False,
            full_experiment_acceptance=False)
    finally:
        # A rejected cold state has not started native work or invalidated the
        # outbox seed. Everything else must prove actual task/thread drain.
        if outbox._runtime is None:
            runtime.begin_shutdown()
            await runtime.drain(timeout=drain_timeout)
            cleanup = {'cleanup_complete': True, 'database': lease.restore(baseline_id),
                       'outbox': outbox.identity()}
        else:
            cleanup = restore_top20_replay_baseline(lease, baseline_id, outbox, runtime)
    result['cleanup'] = cleanup
    return result
