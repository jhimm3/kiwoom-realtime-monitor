"""Internal recorded-operation scheduler; deliberately not a public DB runner.

The caller owns the isolated store and its baseline. No production API imports
this executor until dedicated DB provisioning, restore and run-lock gates exist.
Capture eligibility is broader than execution eligibility: generated references,
ordinal cursors and native wall-clock decisions need separate adapters.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections import defaultdict
from contextvars import copy_context
from datetime import datetime, timedelta
from threading import Event
from uuid import uuid4

from .diagnostic_replay_contract import (
    capture_owner, compile_recorded_plan, freeze_payload,
    replay_operation_identity, thaw_payload,
)
from .postgres_access import db_call_request_id, db_call_source
from .diagnostic_collector_replay import _MeasuredStore, _ReplayClock, _owned_close
from .realtime_collector import CentralRealtimeCollector
from .realtime_hub import RealtimeHub


# Explicit native methods with supplied natural keys. No arbitrary trace dispatch,
# query-cache expiry decisions, news generated-ID chains or sequence cursor reads.
_METHODS = frozenset({
    'save_realtime_snapshots', 'save_minute_bars', 'finalize_minute_bars',
    'save_second_trade_bars', 'replace_minute_bars', 'replace_daily_bars',
    'save_five_minute_bars', 'load_five_minute_bars', 'load_minute_bars',
    'load_daily_bars', 'load_realtime_snapshots', 'load_latest_market_caps',
    'save_market_data_metadata', 'load_market_data_metadata',
    'load_market_data_metadata_range', 'save_dataset_snapshot',
    'save_dataset_snapshots', 'load_dataset_snapshots', 'upsert_documents',
    'replace_documents', 'load_documents', 'load_document',
    'save_shadow_monitor_state', 'load_shadow_monitor_state',
    'save_external_bars', 'load_external_bars',
})


def run_owned_recorded_experiment(database_url, owner_token, baseline_id, events, *,
                                  config=None, **selection):
    """Offline worker entry: restore under ownership, execute, drain, unlock.

    This is not an API route. The native scheduler owns cancellation draining;
    the synchronous worker retains the lease until every native connection
    closes, including those whose COMMIT acknowledgement was lost.
    """
    from .diagnostic_replay_baseline import ReplayDatabaseLease, _ReplayStore

    lease = ReplayDatabaseLease(database_url, owner_token, config=config)
    bound = inspect.signature(_execute_recorded_operations).bind(None, events, **selection)
    bound.apply_defaults()
    options = bound.arguments
    if type(options['concurrency']) is not int or not 1 <= options['concurrency'] <= 16:
        raise ValueError('recorded_execution_concurrency_invalid')
    plan = compile_recorded_plan(events, **{name: options[name] for name in (
        'started_mono_ns', 'window_start_seconds', 'window_end_seconds',
        'include_workloads', 'exclude_workloads', 'mode', 'collector_components')})
    # Binding/codec/clock errors must not reset even the dedicated test DB.
    _prepare(_ReplayStore(lease), events, plan)
    _collector_inputs(events, plan)
    with lease:
        baseline = lease.restore(baseline_id)
        try:
            result = asyncio.run(_execute_recorded_operations(lease.store(), events, **selection))
            if lease.status()['owned_connections']:
                raise RuntimeError('recorded_execution_connections_not_drained')
            # Validation occurs after native drain, outside the measured interval.
            # Exact hashes include native wall time and generated revision IDs;
            # they are evidence, not a promise of source-state equivalence.
            with lease.connection.cursor() as cursor:
                result['final_tables'] = lease._tables_digest(cursor, 'public')
                result['final_sequences'] = lease._sequences(cursor)
            result.update(baseline_managed=True, baseline=baseline,
                          database_ownership_verified=True,
                          fidelity='native_operations_on_owned_logical_baseline')
        finally:
            # restore independently verifies drain and outsider-session gates.
            # Refusal propagates; do not claim cleanup after a failed reset.
            cleanup = lease.restore(baseline_id)
        result['cleanup'] = {'baseline_restored': True, **cleanup}
        return result


_MAX_OPERATIONS = 4096
_MAX_ACTORS = 64


def _prepare(store, events, plan):
    """Validate the entire selected frontier before the first native invocation."""
    starts = {row['operation_id']: row for row in events
              if row.get('event_type') == 'operation_start'}
    ends = {row['operation_id']: row for row in events
            if row.get('event_type') == 'operation_end'}
    if len(plan.operation_ids) > _MAX_OPERATIONS:
        raise ValueError('recorded_execution_operation_limit')
    actors = defaultdict(list)
    for identifier in plan.operation_ids:
        row = starts[identifier]
        method = row['method']
        if method not in _METHODS:
            raise ValueError(f'recorded_execution_adapter_missing:{method}')
        native = getattr(store, method, None)
        if not callable(native) or inspect.iscoroutinefunction(native):
            raise ValueError(f'recorded_execution_native_method_missing:{method}')
        arguments = thaw_payload(row['payload'])
        if type(arguments) is not dict:
            raise ValueError('recorded_execution_arguments_invalid')
        if (method in {'upsert_documents', 'replace_documents'}
                and arguments.get('collection') in {'news_article', 'theme_metadata'}):
            # These variants append projection/history outside the sealed table
            # allowlist. Capture support is broader than owned replay support.
            raise ValueError('recorded_execution_projection_adapter_missing')
        # Capture used Signature.bind/apply_defaults. Replay uses the same native
        # signature, including keyword-only parameters, before touching the DB.
        try:
            inspect.signature(native).bind(**arguments)
        except TypeError as error:
            raise ValueError('recorded_execution_signature_mismatch') from error
        actor = row.get('actor_id')
        sequence = row.get('actor_sequence')
        if not actor or type(sequence) is not int or sequence <= 0:
            raise ValueError('recorded_execution_actor_sequence_invalid')
        actors[actor].append((row, ends[identifier], native, arguments))
    if len(actors) > _MAX_ACTORS:
        raise ValueError('recorded_execution_actor_limit')
    for values in actors.values():
        for previous, current in zip(values, values[1:]):
            if current[0]['actor_sequence'] <= previous[0]['actor_sequence']:
                raise ValueError('recorded_execution_actor_sequence_invalid')
    return actors


def _result_digest(value):
    try:
        frozen = freeze_payload(value)
        data = json.dumps(frozen.value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(data).hexdigest(), None
    except Exception:
        # Result observation must never turn a committed native write into failure.
        return None, 'result_codec_unsupported'


def _source_keys(values):
    if type(values) not in (list, tuple) or any(
        type(key) not in (list, tuple) or len(key) != 2
        or type(key[0]) is not str or not key[0]
        or type(key[1]) is not str or key[1] not in {'KRX', 'NXT', 'SOR'} for key in values
    ):
        raise ValueError('recorded_execution_source_keys_invalid')
    keys = {tuple(key) for key in values}
    if len(keys) != len(values):
        raise ValueError('recorded_execution_source_keys_invalid')
    return keys


def _collector_inputs(events, plan):
    selected = set(plan.collector_input_ids)
    grouped = defaultdict(list)
    for row in sorted(events, key=lambda value: value['seq']):
        if row.get('event_type') != 'collector_input' or row.get('input_id') not in selected:
            continue
        value = thaw_payload(row['payload'])
        kind = row['input_kind']
        at = value.get('source_time')
        if not isinstance(at, datetime) or at.tzinfo is None:
            raise ValueError('recorded_execution_collector_clock_missing')
        if kind == 'message':
            message = value.get('message', {})
            data = message.get('data') if type(message) is dict else None
            if (type(message) is not dict or message.get('trnm') != 'REAL'
                    or type(data) is not list or not 1 <= len(data) <= 100
                    or any(type(item) is not dict or item.get('type') != '0B' for item in data)):
                raise ValueError('recorded_execution_collector_message_invalid')
        elif kind in {'initial_state', 'source_approval'}:
            sources = _source_keys(value.get('approved_sources' if kind == 'initial_state' else 'sources'))
            if kind == 'initial_state':
                continuous = value.get('continuous_from', ())
                if (type(continuous) not in (tuple, list) or any(
                    type(item) not in (tuple, list) or len(item) != 3
                    or tuple(item[:2]) not in sources or not isinstance(item[2], datetime)
                    or item[2].tzinfo is None for item in continuous
                ) or len({tuple(item[:2]) for item in continuous}) != len(continuous)):
                    raise ValueError('recorded_execution_collector_initial_state_invalid')
            else:
                if (type(value.get('reset_all')) is not bool
                        or not isinstance(value.get('subscribed_at'), datetime)
                        or value['subscribed_at'].tzinfo is None):
                    raise ValueError('recorded_execution_collector_approval_invalid')
        elif kind == 'capture_gap' and type(value.get('clear_continuous')) is not bool:
            raise ValueError('recorded_execution_collector_gap_invalid')
        grouped[row['producer_component']].append((row, value))
    if len(grouped) > 8:
        raise ValueError('recorded_execution_collector_limit')
    for values in grouped.values():
        if (values[0][0]['input_kind'] != 'initial_state'
                or sum(row['input_kind'] == 'initial_state' for row, _ in values) != 1
                or any(current[0]['mono_ns'] < previous[0]['mono_ns']
                       for previous, current in zip(values, values[1:]))):
            raise ValueError('recorded_execution_collector_initial_state_invalid')
    return grouped


class _RecordedCollectorStore(_MeasuredStore):
    def __init__(self, store, clock, component, window_start):
        super().__init__(store, clock)
        self.component, self.window_start = component, window_start
        self.input_id = ''
        self.draining = False

    def _write(self, name, kind, values, **kwargs):
        identifier = uuid4().hex
        self.phase = 'drain' if self.draining else (
            'prefix' if self.clock.elapsed() < self.window_start else 'measurement')
        # A flush can aggregate several input events. This is a high-water link,
        # not a false one-to-one causal assignment to the latest event.
        source_input = self.input_id
        before = len(self.calls)
        with (capture_owner('realtime', self.component, f'{self.component}:flush'),
              replay_operation_identity(identifier), db_call_request_id(identifier)):
            try:
                return super()._write(name, kind, values, **kwargs)
            finally:
                if len(self.calls) > before:
                    self.calls[-1].update(replay_operation_id=identifier,
                                         source_input_id_high_water=source_input)


async def _execute_recorded_operations(store, events, *, started_mono_ns,
                                       window_start_seconds, window_end_seconds,
                                       include_workloads=(), exclude_workloads=(),
                                       concurrency=8, stop=None,
                                       mode='recorded_operations', collector_components=(), clock=None):
    """Execute on a caller-owned test store; no baseline/reset/network access.

    Intended only for local correctness and a future gated runner. Source wall
    timestamps in arguments are preserved. Native system-time decisions are not
    virtualized and source DB/RAM equivalence is not claimed.
    """
    if type(concurrency) is not int or not 1 <= concurrency <= 16:
        raise ValueError('recorded_execution_concurrency_invalid')
    plan = compile_recorded_plan(
        events, started_mono_ns=started_mono_ns,
        window_start_seconds=window_start_seconds,
        window_end_seconds=window_end_seconds,
        include_workloads=include_workloads, exclude_workloads=exclude_workloads,
        mode=mode, collector_components=collector_components,
    )
    actors = _prepare(store, events, plan)
    inputs = _collector_inputs(events, plan)
    stop = stop if stop is not None else Event()
    limit = asyncio.Semaphore(concurrency)
    origin = time.monotonic()
    if inputs and clock is None:
        first, value = next(iter(inputs.values()))[0]
        clock = _ReplayClock(value['source_time'] - timedelta(
            seconds=(first['mono_ns'] - started_mono_ns) / 1e9))
    timeline_start = 0 if inputs else window_start_seconds
    records = []
    collector_reports = []
    # Source spans are associations, not a promise that the new code will issue
    # the same number of SQL calls or have identical transaction timing.
    source_calls = defaultdict(set)
    for row in events:
        if row.get('event_type') in {'call_start', 'call_end'} and row.get('call_id'):
            source_calls[row.get('input_operation_id')].add(row['call_id'])

    def elapsed():
        return clock.elapsed() if clock is not None else time.monotonic() - origin

    async def until(offset):
        while not stop.is_set() and elapsed() < offset:
            delay = min(0.05, max(0, offset - elapsed()))
            await (clock.sleep(delay) if clock is not None else asyncio.sleep(delay))

    async def actor(values):
        predecessor_finished = 0
        for row, ending, native, arguments in values:
            offset = (row['entered_mono_ns'] - started_mono_ns) / 1e9 - timeline_start
            record = {
                'source_operation_id': row['operation_id'],
                'replay_operation_id': uuid4().hex,
                'source_call_ids': sorted(source_calls[row['operation_id']]),
                'method': row['method'], 'workload_id': row['workload_id'],
                'actor_id': row['actor_id'], 'actor_sequence': row['actor_sequence'],
                'scheduled_seconds': offset, 'source_outcome': ending.get('outcome'),
                'state': 'not_started',
            }
            records.append(record)
            await until(offset)
            if stop.is_set():
                continue
            record['ready_seconds'] = elapsed()
            record['actor_wait_ms'] = max(0, (predecessor_finished - offset) * 1000)
            record['scheduler_lag_ms'] = max(0, (
                record['ready_seconds'] - max(offset, predecessor_finished)) * 1000)
            async with limit:
                if stop.is_set():
                    continue
                record['submitted_seconds'] = elapsed()
                record['concurrency_wait_ms'] = (
                    record['submitted_seconds'] - record['ready_seconds']) * 1000

                def invoke():
                    record['started_seconds'] = elapsed()
                    record['worker_wait_ms'] = (
                        record['started_seconds'] - record['submitted_seconds']) * 1000
                    record['start_lag_ms'] = max(0, (record['started_seconds'] - offset) * 1000)
                    with (
                        capture_owner(row['workload_id'], row.get('producer_component', ''),
                                      row['actor_id'], cause_input_id=row.get('cause_input_id', '')),
                        replay_operation_identity(record['replay_operation_id']),
                        db_call_source('diagnostic.recorded_operations'),
                        db_call_request_id(record['replay_operation_id']),
                    ):
                        try:
                            value = native(**arguments)
                        except Exception as error:
                            record.update(state='failed', exception_type=type(error).__name__)
                            stop.set()
                        else:
                            record['native_finished_seconds'] = elapsed()
                            observation_started = time.monotonic()
                            digest, unavailable = _result_digest(value)
                            record.update(state='returned', result_digest=digest,
                                          result_digest_unavailable=unavailable,
                                          result_observation_ms=(time.monotonic() - observation_started) * 1000)
                        finally:
                            record['finished_seconds'] = elapsed()
                            record['native_elapsed_ms'] = (
                                record.get('native_finished_seconds', record['finished_seconds'])
                                - record['started_seconds']) * 1000
                # Context variables reach the worker; the native method still owns
                # its connection, rollback, commit and close. Never cancel a worker
                # and claim that its DB transaction has already stopped.
                context = copy_context()
                await asyncio.to_thread(context.run, invoke)
                predecessor_finished = record['finished_seconds']

    async def collector_actor(component, values):
        def no_token():
            raise AssertionError('recorded collector replay attempted network credentials')
        measured = _RecordedCollectorStore(store, clock, component, window_start_seconds)
        collector = CentralRealtimeCollector(no_token, 'real', RealtimeHub(), clock.now,
                                             measured, snapshot_sleep=clock.sleep)
        report = {'component': component, 'input_count': 0, 'input_lag_ms_max': 0,
                  'state': 'incomplete', 'initial_state': 'cold_with_prefix',
                  'source_state_equivalent': False}
        collector_reports.append(report)
        started = False
        try:
            with db_call_source('diagnostic.recorded_collector'):
                for row, value in values:
                    offset = (row['mono_ns'] - started_mono_ns) / 1e9
                    await until(offset)
                    if stop.is_set():
                        break
                    report['input_lag_ms_max'] = max(report['input_lag_ms_max'],
                                                     max(0, elapsed() - offset) * 1000)
                    measured.input_id, measured.input_seq = row['input_id'], row['seq']
                    kind = row['input_kind']
                    if kind == 'initial_state':
                        await collector.start_input_replay()
                        started = True
                        collector.restore_replay_source_state(
                            _source_keys(value['approved_sources']),
                            {tuple(item[:2]): item[2] for item in value.get('continuous_from', ())})
                        report['source_warm_accumulators'] = value.get('warm_accumulators')
                    elif kind == 'source_approval':
                        collector.accept_replay_sources(_source_keys(value['sources']),
                                                        reset_all=value['reset_all'],
                                                        subscribed_at=value['subscribed_at'])
                    elif kind == 'capture_gap':
                        collector.accept_replay_gap(clear_continuous=value['clear_continuous'])
                    else:
                        collector.accept_replay_message(value['message'])
                    report['input_count'] += 1
                await until(window_end_seconds)
                if not stop.is_set():
                    report['state'] = 'complete'
        except Exception:
            stop.set()
            raise
        finally:
            if started:
                measured.draining = True
                await _owned_close(collector)
                report['pending_records_after_drain'] = collector.input_replay_status()['pending_records']
                report['calls'] = measured.calls
                report['call_records_dropped'] = measured.records_dropped

    tasks = [asyncio.create_task(actor(values)) for values in actors.values()]
    tasks.extend(asyncio.create_task(collector_actor(component, values))
                 for component, values in inputs.items())
    drain = asyncio.gather(*tasks, return_exceptions=True)
    cancelled = False
    while not drain.done():
        try:
            await asyncio.shield(drain)
        except asyncio.CancelledError:
            cancelled = True
            stop.set()
    errors = drain.result()
    if cancelled:
        raise asyncio.CancelledError
    if any(isinstance(error, BaseException) for error in errors):
        raise RuntimeError('recorded_execution_scheduler_failed')
    # Retain the selected quiet tail too, instead of shortening the experiment
    # when its last operation happens well before the window boundary.
    await until(window_end_seconds - timeline_start)
    records.sort(key=lambda value: (value['scheduled_seconds'], value['source_operation_id']))
    complete = (all(value['state'] == 'returned' for value in records)
                and all(value['state'] == 'complete' and value.get('pending_records_after_drain') == 0
                        and not value.get('call_records_dropped') for value in collector_reports))
    outcomes_match = all(value['source_outcome'] == value['state'] for value in records)
    return {
        'state': 'complete' if complete else 'incomplete', 'calls': records,
        'collector_reports': collector_reports, 'mode': mode,
        'replaced_operations': list(plan.replaced_operation_ids),
        'selected_workloads': list(plan.selected_workloads),
        'excluded_operations': len(plan.excluded_operation_ids),
        'actor_known': plan.actor_known, 'source_outcomes_match': outcomes_match,
        'input_timing_preserved': complete and plan.actor_known
            and not getattr(clock, 'correctness_only', False)
            and all(value.get('start_lag_ms', float('inf')) <= 100 for value in records)
            and all(value['input_lag_ms_max'] <= 100 for value in collector_reports),
        'source_state_equivalent': False,
        'source_time_semantics': 'collector_trace_clock_and_native_arguments' if inputs else 'arguments_only',
        'baseline_managed': False, 'public_execution_ready': False,
        'fidelity': 'native_operations_on_caller_owned_test_store',
        'elapsed_seconds': elapsed(),
    }
