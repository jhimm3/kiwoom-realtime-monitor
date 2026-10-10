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
from contextlib import asynccontextmanager, nullcontext
from collections import defaultdict
from datetime import datetime, timedelta
from threading import Event
from uuid import uuid4

from .diagnostic_replay_contract import (
    LEGACY_COLLECTOR_INPUT_VERSION,
    capture_owner, compile_recorded_plan, freeze_payload,
    replay_operation_identity, thaw_payload, thaw_operation_arguments, validate_collector_message,
)
from .postgres_access import db_call_request_id, db_call_source
from .diagnostic_trace_payload import BlockPayloadReference
from .diagnostic_collector_replay import _MeasuredStore, _ReplayClock, _owned_close
from .realtime_collector import CentralRealtimeCollector
from .realtime_hub import RealtimeHub
from .diagnostic_account_input import METHODS as ACCOUNT_METHODS, native_error_receipt, resolve_owner_bindings


# Explicit native methods with supplied natural keys. No arbitrary trace dispatch,
# news generated-ID chains or sequence cursor reads. Query cache needs v2's
# restored cache table and lease-owned source clock, never the host wall time.
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
    'save_query', 'load_query', 'append_vi_events',
}) | ACCOUNT_METHODS


def run_owned_recorded_experiment(database_url, owner_token, baseline_id, events, *,
                                  config=None, baseline_version=1, cache_clock=None,
                                  collect_activity=False, account_context=None, **selection):
    """Offline worker entry: restore under ownership, execute, drain, unlock.

    This is not an API route. The native scheduler owns cancellation draining;
    the synchronous worker retains the lease until every native connection
    closes, including those whose COMMIT acknowledgement was lost.
    """
    from .diagnostic_replay_baseline import ReplayDatabaseLease, _ReplayStore

    if baseline_version == 3 and (account_context is None or cache_clock is None):
        raise ValueError('recorded_execution_v3_context_and_native_clock_required')
    if baseline_version != 3 and account_context is not None:
        raise ValueError('recorded_execution_account_context_requires_v3')
    if {'_shared_runtime', '_peer_operation_ids'} & selection.keys():
        raise ValueError('recorded_execution_shared_runtime_requires_lifecycle_owner')
    if type(collect_activity) is not bool:
        raise ValueError('recorded_execution_activity_flag_invalid')

    lease_options = {} if baseline_version == 1 and cache_clock is None else {
        'baseline_version': baseline_version, 'cache_clock': cache_clock}
    lease = ReplayDatabaseLease(database_url, owner_token, config=config, **lease_options)
    if baseline_version >= 2:
        from .diagnostic_top20_seed import Top20FixtureClock
        if (not isinstance(cache_clock, Top20FixtureClock) or cache_clock.armed
                or 'clock' in selection):
            raise ValueError('recorded_execution_v2_requires_fresh_owned_clock')
        selection = {**selection, 'clock': cache_clock}
    if baseline_version == 3:
        # Own a validated, bounded copy before any connection or reset.
        from .diagnostic_account_context import validate_account_context
        validate_account_context(account_context)
        account_context = validate_account_context(json.loads(json.dumps(account_context, allow_nan=False)))
        selection = {**selection, 'account_context': account_context}
    bound = inspect.signature(_execute_recorded_operations).bind(None, events, **selection)
    bound.apply_defaults()
    options = bound.arguments
    if type(options['concurrency']) is not int or not 1 <= options['concurrency'] <= 16:
        raise ValueError('recorded_execution_concurrency_invalid')
    plan = compile_recorded_plan(events, **{name: options[name] for name in (
        'started_mono_ns', 'window_start_seconds', 'window_end_seconds',
        'include_workloads', 'exclude_workloads', 'mode', 'collector_components')})
    # Binding/codec/clock errors must not reset even the dedicated test DB.
    _prepare(_ReplayStore(lease), events, plan, account_context=account_context)
    if baseline_version == 3:
        expected_origin = account_context['snapshot_finished_at'] + (
            options['started_mono_ns'] - account_context['snapshot_finished_mono_ns']) / 1e9
        if (account_context['snapshot_finished_mono_ns'] > options['started_mono_ns']
                or abs(cache_clock.origin.timestamp() - expected_origin) > .1):
            raise ValueError('recorded_execution_account_source_clock_mismatch')
    inputs = _collector_inputs(events, plan)
    if baseline_version >= 2:
        # Paired source time and monotonic metadata are sampled separately. The
        # same 100 ms timing budget used in the result bounds their alignment;
        # another source day/clock must fail before any restore or connection.
        for values in inputs.values():
            for row, value in values:
                expected = cache_clock.origin + timedelta(
                    seconds=(row['mono_ns'] - options['started_mono_ns']) / 1e9)
                if abs((value['source_time'] - expected).total_seconds()) > .1:
                    raise ValueError('recorded_execution_source_clock_mismatch')
    with lease:
        if baseline_version == 3:
            # Verify the sealed capsule and exact authority frontier before
            # resetting even this dedicated DB. A different selection must seal
            # its own baseline; source tokens are never copied from the source DB.
            from .diagnostic_replay_baseline import _hash
            bindings = _account_bindings(account_context, events, plan)
            expected = {'version': account_context['version'], 'trace_id': account_context['trace_id'],
                        'sha256': account_context['sha256'], 'source_state_equivalent': False,
                        'owner_bindings_sha256': _hash({domain + ':' + alias: value.token
                            for (domain, alias), value in bindings.items()})}
            with lease.connection.cursor() as cursor:
                sealed_id, sealed = lease._baseline(cursor)
            if sealed_id != baseline_id or sealed.get('account_context') != expected:
                raise ValueError('recorded_execution_account_sealed_context_mismatch')
        baseline = lease.restore(baseline_id)
        try:
            from .diagnostic_replay_sampling import ReplayActivitySampler
            observer = ReplayActivitySampler(lease.connection) if collect_activity else None
            # No other management-connection caller runs inside this interval.
            # Join the observer before status/digest/reset even if native replay fails.
            with observer if observer is not None else nullcontext():
                result = asyncio.run(_execute_recorded_operations(lease.store(), events, **selection))
            if observer is not None:
                result['postgres_activity'] = observer.report()
            if lease.status()['owned_connections']:
                raise RuntimeError('recorded_execution_connections_not_drained')
            # Validation occurs after native drain, outside the measured interval.
            # Exact hashes include native wall time and generated revision IDs;
            # they are evidence, not a promise of source-state equivalence.
            with lease.connection.cursor() as cursor:
                result['final_tables'] = lease._tables_digest(cursor, 'public')
                result['final_sequences'] = lease._sequences(cursor)
                from .diagnostic_replay_baseline import MAX_BASELINE_BYTES, MAX_BASELINE_ROWS
                from .diagnostic_replay_comparison import collect_final_content_comparison
                result['final_content_comparison'] = collect_final_content_comparison(
                    cursor, max_bytes=MAX_BASELINE_BYTES, max_rows=MAX_BASELINE_ROWS,
                )
            result.update(baseline_managed=True, baseline=baseline,
                          baseline_version=baseline_version,
                          database_ownership_verified=True,
                          fidelity='native_operations_on_owned_logical_baseline')
            if baseline_version >= 2:
                result['cache_clock'] = {
                    'policy': cache_clock.policy, 'origin': cache_clock.origin.isoformat(),
                    'start_seconds': 0 if inputs else options['window_start_seconds'],
                    'scope': 'query_cache_account_lease_and_selected_collector' if baseline_version == 3
                             else 'query_cache_and_selected_collector_only',
                    'collector_alignment_tolerance_ms': 100,
                }
        finally:
            # restore independently verifies drain and outsider-session gates.
            # Refusal propagates; do not claim cleanup after a failed reset.
            cleanup = lease.restore(baseline_id)
        result['cleanup'] = {'baseline_restored': True, **cleanup}
        return result


_MAX_OPERATIONS = 4096
_MAX_ACTORS = 64


def _account_bindings(context, events, plan):
    if context is None:
        return None
    from .diagnostic_account_context import validate_account_context
    validate_account_context(context)
    selected = set(plan.operation_ids)
    rows = [row for row in events if row.get('event_type') == 'operation_start'
            and row.get('operation_id') in selected and row.get('method') in ACCOUNT_METHODS]
    if any(row.get('account_alias_domain') != context['alias_domain'] for row in rows):
        raise ValueError('recorded_execution_account_context_domain_mismatch')
    return resolve_owner_bindings([*context['owner_bindings'], *rows])


def _prepare(store, events, plan, *, account_context=None):
    """Validate the entire selected frontier before the first native invocation."""
    starts = {row['operation_id']: row for row in events
              if row.get('event_type') == 'operation_start'}
    ends = {row['operation_id']: row for row in events
            if row.get('event_type') == 'operation_end'}
    if len(plan.operation_ids) > _MAX_OPERATIONS:
        raise ValueError('recorded_execution_operation_limit')
    bindings = _account_bindings(account_context, events, plan)
    actors = defaultdict(list)
    for identifier in plan.operation_ids:
        row = starts[identifier]
        method = row['method']
        if method not in _METHODS:
            raise ValueError(f'recorded_execution_adapter_missing:{method}')
        if method in ACCOUNT_METHODS or method == 'append_vi_events':
            if getattr(getattr(store, '_lease', None), 'baseline_version', None) != 3:
                raise ValueError('recorded_execution_account_vi_requires_v3')
        if method in ACCOUNT_METHODS:
            if bindings is None or not callable(getattr(store, '_execution_wall_now', None)):
                raise ValueError('recorded_execution_account_context_and_clock_required')
            ending = ends[identifier]
            if ending.get('outcome') == 'failed':
                from .diagnostic_account_input import NATIVE_ERROR_CODES
                if (ending.get('native_error_code') not in NATIVE_ERROR_CODES
                        or ending.get('exception_type') not in {'ValueError', 'RuntimeError'}):
                    raise ValueError('recorded_execution_account_failure_receipt_required')
            elif ending.get('outcome') != 'returned' or not (
                    'result' in ending and type(ending['result']) in (bool, int, type(None))
                    or ending.get('result_type') in {'dict', 'list', 'tuple'}
                    and type(ending.get('result_count')) is int and ending['result_count'] >= 0):
                raise ValueError('recorded_execution_account_result_receipt_required')
        if method == 'append_vi_events' and (ends[identifier].get('outcome') != 'returned'
                or type(ends[identifier].get('result')) is not int):
            raise ValueError('recorded_execution_vi_result_receipt_required')
        if method in {'save_query', 'load_query'} and not callable(
                getattr(store, '_query_cache_wall_time', None)):
            raise ValueError('recorded_execution_query_cache_requires_v2')
        native = getattr(store, method, None)
        if not callable(native) or inspect.iscoroutinefunction(native):
            raise ValueError(f'recorded_execution_native_method_missing:{method}')
        arguments = (thaw_operation_arguments(row) if bindings is None
                     else thaw_operation_arguments(row, owner_bindings=bindings))
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
        # Keep large immutable references, not every decoded window input.
        retained = row['payload'] if type(row.get('payload')) is BlockPayloadReference else arguments
        actors[actor].append((row, ends[identifier], native, retained))
        del arguments
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
            version = row.get('collector_input_version', LEGACY_COLLECTOR_INPUT_VERSION)
            validate_collector_message(value.get('message'), version=version,
                                       allow_empty=bool(row.get('excluded_types')))
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

    def save_dataset_snapshots(self, values):
        if any(value[0] != 'market_state' for value in values):
            raise ValueError('recorded_collector_dataset_sink_unsupported')
        return self._write('save_dataset_snapshots', 'market_state', values)

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
                                       mode='recorded_operations', collector_components=(), clock=None,
                                       _shared_runtime=None, _peer_operation_ids=None, account_context=None):
    """Execute on a caller-owned test store; no baseline/reset/network access.

    Intended only for local correctness and a future gated runner. Source wall
    timestamps in arguments are preserved. The v2 clock virtualizes query-cache
    and selected collector time only; source DB/RAM equivalence is not claimed.
    """
    if type(concurrency) is not int or not 1 <= concurrency <= 16:
        raise ValueError('recorded_execution_concurrency_invalid')
    from .diagnostic_replay_runtime import (
        ReplayRuntimeScope, owned_create_task, owned_to_thread, owned_payload_credit,
    )
    if _shared_runtime is not None:
        from .diagnostic_top20_seed import Top20FixtureClock
        if (type(_shared_runtime) is not ReplayRuntimeScope or type(clock) is not Top20FixtureClock
                or not clock.armed or mode != 'recorded_operations' or collector_components):
            raise ValueError('recorded_execution_shared_runtime_invalid')
        _shared_runtime.require_active()
        lease = getattr(store, '_lease', None)
        if lease is not None and (lease._runtime is not _shared_runtime or lease._cache_clock is not clock):
            raise ValueError('recorded_execution_shared_lease_mismatch')
    plan = compile_recorded_plan(
        events, started_mono_ns=started_mono_ns,
        window_start_seconds=window_start_seconds,
        window_end_seconds=window_end_seconds,
        include_workloads=include_workloads, exclude_workloads=exclude_workloads,
        mode=mode, collector_components=collector_components,
    )
    if _peer_operation_ids is not None:
        from dataclasses import replace
        if (_shared_runtime is None or type(_peer_operation_ids) is not tuple
                or any(type(value) is not str or not value for value in _peer_operation_ids)
                or len(set(_peer_operation_ids)) != len(_peer_operation_ids)
                or set(_peer_operation_ids) - set(plan.operation_ids)):
            raise ValueError('recorded_execution_peer_frontier_invalid')
        selected = set(_peer_operation_ids)
        plan = replace(plan, operation_ids=tuple(identifier for identifier in plan.operation_ids
                                                 if identifier in selected),
                       excluded_operation_ids=(*plan.excluded_operation_ids,
                           *(identifier for identifier in plan.operation_ids if identifier not in selected)))
    actors = _prepare(store, events, plan, account_context=account_context)
    bindings = _account_bindings(account_context, events, plan)
    inputs = _collector_inputs(events, plan)
    stop = stop if stop is not None else Event()
    limit = asyncio.Semaphore(concurrency)
    if inputs and clock is None:
        first, value = next(iter(inputs.values()))[0]
        clock = _ReplayClock(value['source_time'] - timedelta(
            seconds=(first['mono_ns'] - started_mono_ns) / 1e9))
    # An owned lifecycle already started at source zero. Peers join that same
    # absolute timeline; they must not reset its clock or shift the window.
    timeline_start = 0 if inputs or _shared_runtime is not None else window_start_seconds
    if (_shared_runtime is None and clock is not None
            and callable(getattr(clock, 'arm', None)) and clock.armed):
        raise ValueError('recorded_execution_clock_already_armed')
    block_credit = owned_payload_credit()
    primed = {}

    async def drained_thread(function, *arguments, name):
        # A cancelled waiter cannot retire the input credit until its actual
        # decoding or native thread has returned.
        task = owned_create_task(owned_to_thread(function, *arguments), name=name)
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
                stop.set()
        result = task.result()
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def decode_block(row):
        started = time.monotonic()
        decode = (lambda: thaw_operation_arguments(row)) if bindings is None else (
            lambda: thaw_operation_arguments(row, owner_bindings=bindings))
        result = await drained_thread(decode, name='recorded-payload-prepare')
        return result, (time.monotonic() - started) * 1000

    # Prepare at most the first three near-boundary large inputs before starting
    # a standalone timeline. All selected inputs already passed _prepare; this
    # phase opens no native connection and retains only the credited frontier.
    # Peers join an already running clock and must never move its origin.
    if _shared_runtime is None:
        first_blocks = sorted((entry for values in actors.values() for entry in values
                               if type(entry[3]) is BlockPayloadReference),
                              key=lambda entry: (entry[0]['entered_mono_ns'], entry[0]['seq']))
        try:
            for row, _, _, _ in first_blocks[:3]:
                offset = (row['entered_mono_ns'] - started_mono_ns) / 1e9 - timeline_start
                if offset > 1:
                    break
                await block_credit.acquire()
                try:
                    primed[row['operation_id']] = await decode_block(row)
                except BaseException:
                    block_credit.release()
                    raise
        except BaseException:
            for _ in primed:
                block_credit.release()
            primed.clear()
            raise
    origin = time.monotonic()
    # v2's source origin identifies trace zero, while native-operation-only
    # windows skip the prefix. Cache wall time must advance to that window, but
    # scheduling/duration still start at zero. Restoration/preflight never arm.
    clock_offset = 0.0
    if _shared_runtime is None and clock is not None and callable(getattr(clock, 'arm', None)):
        if clock.armed:
            raise ValueError('recorded_execution_clock_already_armed')
        clock.arm(start_seconds=timeline_start)
        clock_offset = timeline_start
    records = []
    collector_reports = []
    # Source spans are associations, not a promise that the new code will issue
    # the same number of SQL calls or have identical transaction timing.
    source_calls = defaultdict(set)
    for row in events:
        if row.get('event_type') in {'call_start', 'call_end'} and row.get('call_id'):
            source_calls[row.get('input_operation_id')].add(row['call_id'])

    def elapsed():
        return clock.elapsed() - clock_offset if clock is not None else time.monotonic() - origin

    async def until(offset):
        while not stop.is_set() and elapsed() < offset:
            delay = min(0.05, max(0, offset - elapsed()))
            await (clock.sleep(delay) if clock is not None else asyncio.sleep(delay))

    # Three 64MiB typed inputs plus the existing 32MiB small-input window fit
    # within 256MiB typed-copy credit. Physical RSS is measured separately.
    @asynccontextmanager
    async def prepared_input(row, arguments, record, offset):
        if type(arguments) is not BlockPayloadReference:
            record.update(payload_prepare_ms=0.0, payload_credit_wait_ms=0.0)
            yield arguments
            return
        preloaded = primed.pop(row['operation_id'], None)
        if preloaded is None:
            # Do not let far-future actors occupy the bounded credits while
            # earlier actors need them. Actor predecessor order is unchanged.
            await until(max(0, offset - 1))
            if stop.is_set():
                yield None
                return
            credit_started = elapsed()
            await block_credit.acquire()
            record['payload_credit_wait_ms'] = (elapsed() - credit_started) * 1000
        else:
            record['payload_credit_wait_ms'] = 0.0
        decoded = None
        try:
            decoded, prepare_ms = preloaded if preloaded is not None else await decode_block(row)
            record['payload_prepare_ms'] = prepare_ms
            record['payload_prepared_seconds'] = elapsed()
            yield decoded
        except BaseException:
            stop.set()
            raise
        finally:
            decoded = preloaded = None
            block_credit.release()

    async def actor(values):
        predecessor_finished = 0
        for row, ending, native, arguments in values:
            offset = (row['entered_mono_ns'] - started_mono_ns) / 1e9 - timeline_start
            record = {
                'source_operation_id': row['operation_id'],
                'replay_operation_id': uuid4().hex,
                'source_call_ids': sorted(source_calls[row['operation_id']]),
                'method': row['method'], 'workload_id': row['workload_id'],
                'producer_component': row['producer_component'],
                'actor_id': row['actor_id'], 'actor_sequence': row['actor_sequence'],
                'scheduled_seconds': offset, 'source_outcome': ending.get('outcome'),
                'state': 'not_started',
            }
            records.append(record)
            async with prepared_input(row, arguments, record, offset) as invocation_arguments:
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
                        worker_started = elapsed()
                        record['worker_wait_ms'] = (
                            worker_started - record['submitted_seconds']) * 1000
                        record['started_seconds'] = worker_started
                        record['start_lag_ms'] = max(0, (worker_started - offset) * 1000)
                        with (
                            capture_owner(row['workload_id'], row.get('producer_component', ''),
                                          row['actor_id'], cause_input_id=row.get('cause_input_id', '')),
                            replay_operation_identity(record['replay_operation_id']),
                            db_call_source('diagnostic.recorded_operations'),
                            db_call_request_id(record['replay_operation_id']),
                        ):
                            try:
                                value = native(**invocation_arguments)
                            except Exception as error:
                                receipt = native_error_receipt(row['method'], error)
                                matched = (row['method'] in ACCOUNT_METHODS and ending.get('outcome') == 'failed'
                                    and receipt.get('native_error_code') is not None
                                    and receipt['native_error_code'] == ending.get('native_error_code')
                                    and type(error).__name__ == ending.get('exception_type'))
                                # Native context managers have already rolled back
                                # and closed before this receipt can be accepted.
                                record.update(state='failed', exception_type=type(error).__name__,
                                              expected_failure_matched=matched, **receipt)
                                if not matched:
                                    if _shared_runtime is not None:
                                        _shared_runtime._failure('native_operation', type(error).__name__)
                                    stop.set()
                            else:
                                record['native_finished_seconds'] = elapsed()
                                observation_started = time.monotonic()
                                digest, unavailable = _result_digest(value)
                                record.update(state='returned', result_digest=digest,
                                              result_digest_unavailable=unavailable,
                                              result_observation_ms=(time.monotonic() - observation_started) * 1000)
                                if row['method'] in ACCOUNT_METHODS or row['method'] == 'append_vi_events':
                                    from .diagnostic_replay_contract import _result_summary
                                    summary = _result_summary(row['method'], value)
                                    record['result_summary'] = summary
                                    record['source_result_matched'] = ending.get('outcome') == 'returned' and all(
                                        key in ending and type(ending[key]) is type(item) and ending[key] == item
                                        for key, item in summary.items())
                                    if not record['source_result_matched']:
                                        if _shared_runtime is not None:
                                            _shared_runtime._failure('native_result', 'SourceResultMismatch')
                                        stop.set()
                            finally:
                                record['finished_seconds'] = elapsed()
                                record['native_elapsed_ms'] = (
                                    record.get('native_finished_seconds', record['finished_seconds'])
                                    - record['started_seconds']) * 1000
                    # This actor retains both the input and its credit until
                    # the owned native thread has actually returned.
                    try:
                        await drained_thread(invoke, name='recorded-native-worker')
                    finally:
                        invoke = None
                        invocation_arguments = None
                predecessor_finished = record['finished_seconds']

    async def collector_actor(component, values):
        def no_token():
            raise AssertionError('recorded collector replay attempted network credentials')
        measured = _RecordedCollectorStore(store, clock, component, window_start_seconds)
        collector = CentralRealtimeCollector(no_token, 'real', RealtimeHub(), clock.now,
                                             measured, snapshot_sleep=clock.sleep)
        report = {'component': component, 'input_count': 0, 'input_lag_ms_max': 0,
                  'collector_input_version': dict(plan.collector_input_versions)[component],
                  'event_type_counts': {}, 'excluded_type_counts': {},
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
                        if value['message']['data']:
                            collector.accept_replay_message(value['message'])
                        for item in value['message']['data']:
                            name = item['type']
                            report['event_type_counts'][name] = report['event_type_counts'].get(name, 0) + 1
                        for name, count in row.get('excluded_types', {}).items():
                            report['excluded_type_counts'][name] = report['excluded_type_counts'].get(name, 0) + count
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

    tasks = [owned_create_task(actor(values), name='recorded-replay-actor') for values in actors.values()]
    tasks.extend(owned_create_task(collector_actor(component, values), name='recorded-replay-collector')
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
    # Failed/stopped actors may never reach a primed entry. Retire its retained
    # arguments before returning, and release the run-wide credit exactly once.
    for _ in primed:
        block_credit.release()
    primed.clear()
    if cancelled:
        if _shared_runtime is not None:
            _shared_runtime._failure('peer_waiter_cancelled', 'CancelledError')
        raise asyncio.CancelledError
    if any(isinstance(error, BaseException) for error in errors):
        raise RuntimeError('recorded_execution_scheduler_failed')
    # Retain the selected quiet tail too, instead of shortening the experiment
    # when its last operation happens well before the window boundary.
    await until(window_end_seconds - timeline_start)
    records.sort(key=lambda value: (value['scheduled_seconds'], value['source_operation_id']))
    complete = (all((value['state'] == 'returned' and value.get('source_result_matched', True)
                    and value['source_outcome'] == 'returned') or value.get('expected_failure_matched', False)
                   for value in records)
                and all(value['state'] == 'complete' and value.get('pending_records_after_drain') == 0
                        and not value.get('call_records_dropped') for value in collector_reports))
    outcomes_match = all(value['source_outcome'] == value['state']
                        and value.get('source_result_matched', True)
                        and (value['state'] != 'failed' or value.get('expected_failure_matched', False))
                        for value in records)
    return {
        'state': 'complete' if complete else 'incomplete', 'calls': records,
        'collector_reports': collector_reports, 'mode': mode,
        'selection_semantics': 'hybrid_collector_and_native_operations' if inputs else 'native_operation_mask',
        'cross_component_causal_replay': False,
        'warnings': list(plan.warnings),
        'replaced_operations': list(plan.replaced_operation_ids),
        'replaced_operation_details': [
            {'source_operation_id': row['operation_id'], 'method': row['method'],
             'producer_component': row['producer_component'],
             'source_call_ids': sorted(source_calls[row['operation_id']])}
            for row in events if row.get('event_type') == 'operation_start'
            and row['operation_id'] in plan.replaced_operation_ids],
        'selected_workloads': list(plan.selected_workloads),
        'excluded_operations': len(plan.excluded_operation_ids),
        'actor_known': plan.actor_known, 'source_outcomes_match': outcomes_match,
        'input_timing_preserved': complete and plan.actor_known
            and not getattr(clock, 'correctness_only', False)
            and all(value.get('start_lag_ms', float('inf')) <= 100 for value in records)
            and all(value['input_lag_ms_max'] <= 100 for value in collector_reports),
        'source_state_equivalent': False,
        'source_time_semantics': 'cache_account_lease_and_selected_collector_source_clock'
            if account_context is not None else 'cache_and_selected_collector_source_clock'
            if getattr(clock, 'policy', None) == 'source_wall_real_elapsed/v1' else
            'collector_trace_clock_and_native_arguments' if inputs else 'arguments_only',
        'baseline_managed': False, 'public_execution_ready': False,
        'fidelity': 'native_operations_on_caller_owned_test_store',
        'elapsed_seconds': elapsed(),
    }
