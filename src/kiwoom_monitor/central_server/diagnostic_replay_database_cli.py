"""Offline operator commands for the isolated recorded-replay database."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time

import psycopg

from .diagnostic_replay_baseline import (
    DATABASE_NAME, ReplayDatabaseLease, provision_existing_empty_database,
)


_URL_ENV = 'KIWOOM_REPLAY_DATABASE_URL'
_TOKEN_ENV = 'KIWOOM_REPLAY_OWNER_TOKEN'


def _settings(environ):
    url, token = environ.get(_URL_ENV, ''), environ.get(_TOKEN_ENV, '')
    if not url or not token:
        raise ValueError(f'{_URL_ENV} and {_TOKEN_ENV} must be set in the local environment')
    if not re.fullmatch('[a-f0-9]{32}', token):
        raise ValueError(f'{_TOKEN_ENV} must be 32 lowercase hexadecimal characters')
    return url, token


def _parser():
    parser = argparse.ArgumentParser(
        description=f'Offline baseline control for the dedicated {DATABASE_NAME} database. '
                    'Never accepts a database URL, SQL or table name as an argument.'
    )
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('provision', help='initialize an already-created, empty replay database')
    status = commands.add_parser('status', help='verify replay DB identity and baseline marker')
    seal = commands.add_parser('seal', help='seal the current controlled fixture as immutable baseline')
    restore = commands.add_parser('restore', help='restore the specified baseline atomically')
    restore.add_argument('--baseline-id', required=True)
    run = commands.add_parser('run', help='offline replay of a bounded, checksummed schema-2 trace')
    for command in (status, seal, restore, run):
        command.add_argument('--baseline-version', type=int, choices=(1, 2), default=1)
        command.add_argument('--source-origin', help='aware ISO trace-start time; required only for cache baseline v2')
    run.add_argument('--baseline-id', required=True)
    run.add_argument('--trace-id', required=True)
    run.add_argument('--window-start', type=float, required=True)
    run.add_argument('--window-end', type=float, required=True)
    run.add_argument('--include-workload', action='append', default=[])
    run.add_argument('--exclude-workload', action='append', default=[])
    run.add_argument('--mode', choices=('recorded_operations', 'collector_with_background'),
                     default='recorded_operations')
    run.add_argument('--collector-component', action='append', default=[])
    run.add_argument('--concurrency', type=int, default=8)
    return parser


def _baseline_options(args):
    if args.baseline_version == 2:
        if not args.source_origin:
            raise ValueError('replay_v2_source_origin_required')
        from .diagnostic_top20_seed import Top20FixtureClock
        return {'baseline_version': 2,
                'cache_clock': Top20FixtureClock(datetime.fromisoformat(args.source_origin))}
    if args.source_origin is not None:
        raise ValueError('replay_v1_does_not_accept_source_origin')
    return {}


def _run_trace(url, token, args):
    """Separate offline process only; never point diagnostic control at the app."""
    from . import diagnostic_trace as trace
    from .diagnostic_metrics import refresh_capture_state, summarize_db_calls
    from .diagnostic_recorded_execution import run_owned_recorded_experiment
    from .diagnostic_workloads import _set_capture, _set_tool

    baseline_options = _baseline_options(args)
    manifest, events = trace.recorded_window_events(
        args.trace_id, window_start_seconds=args.window_start,
        window_end_seconds=args.window_end, mode=args.mode,
        collector_components=tuple(args.collector_component))
    if baseline_options:
        started_at = manifest.get('started_at')
        if (type(started_at) not in (int, float) or not math.isfinite(started_at)
                or abs(started_at - baseline_options['cache_clock'].origin.timestamp()) > .1):
            raise ValueError('recorded_execution_trace_source_origin_mismatch')
    selection = dict(started_mono_ns=manifest['started_mono_ns'],
                     window_start_seconds=args.window_start, window_end_seconds=args.window_end,
                     include_workloads=tuple(args.include_workload),
                     exclude_workloads=tuple(args.exclude_workload), mode=args.mode,
                     collector_components=tuple(args.collector_component), concurrency=args.concurrency)
    # Read the immutable source before switching the process's diagnostic path.
    # Native observer uses this private file; the running server's master/trace
    # and pause controls are never modified.
    control_env = 'KIWOOM_DIAGNOSTIC_WORKLOAD_PATH'
    previous = os.environ.get(control_env)
    with tempfile.TemporaryDirectory(prefix='recorded-replay-observer-') as directory:
        control = Path(directory) / 'control.json'
        try:
            os.environ[control_env] = str(control)
            session = _set_tool(control, True, 7200)['diagnostic_tool']['session_id']
            _set_capture(control, True, 7200, expected_session=session)
            refresh_capture_state(force=True)
            started = time.time()
            result = run_owned_recorded_experiment(url, token, args.baseline_id, events,
                                                  **baseline_options, **selection)
            observed = summarize_db_calls(started, time.time(), mode='raw', limit=10_000)
            by_operation = {}
            for call in observed.get('calls', ()):
                by_operation.setdefault(call.get('input_operation_id'), []).append(call['call_id'])
            for call in result['calls']:
                call['replay_call_ids'] = by_operation.get(call['replay_operation_id'], [])
            for collector in result['collector_reports']:
                for call in collector.get('calls', ()):
                    call['replay_call_ids'] = by_operation.get(call['replay_operation_id'], [])
            result.update(db_calls=observed,
                          db_observation_complete=not observed.get('dropped')
                              and not observed.get('raw_truncated'),
                          unobserved_replay_operations=[call['source_operation_id']
                              for call in result['calls'] if call.get('state') != 'not_started'
                              and not call['replay_call_ids']])
        finally:
            try:
                _set_tool(control, False)
                refresh_capture_state(force=True)
            finally:
                if previous is None:
                    os.environ.pop(control_env, None)
                else:
                    os.environ[control_env] = previous
                refresh_capture_state(force=True)
    source = Path(__file__).resolve().parents[3]
    result.update(trace_id=args.trace_id, capture_source_release=manifest.get('source_release'),
                  replay_source=str(source), window_read=manifest['window_read'],
                  input_sha256=hashlib.sha256(json.dumps(events, sort_keys=True,
                      separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest(),
                  selection=selection, statistics_scope='observed_replay_connections_only',
                  wal_attribution_available=False)
    return result


def main(argv=None, *, environ=None, output=None):
    environ = os.environ if environ is None else environ
    output = sys.stdout if output is None else output
    args = _parser().parse_args(argv)
    try:
        url, token = _settings(environ)
        if args.command in {'restore', 'run'} and not re.fullmatch('[a-f0-9]{64}', args.baseline_id):
            raise ValueError('baseline_id_must_be_64_lowercase_hex_characters')
        if args.command == 'provision':
            result = provision_existing_empty_database(url, token)
        elif args.command == 'run':
            result = _run_trace(url, token, args)
        else:
            options = _baseline_options(args)
            with ReplayDatabaseLease(url, token, **options) as lease:
                if args.command == 'status':
                    result = lease.status()
                elif args.command == 'seal':
                    result = lease.seal()
                else:
                    result = lease.restore(args.baseline_id)
        print(json.dumps({'state': 'ok', 'command': args.command, 'result': result},
                         ensure_ascii=False, sort_keys=True), file=output)
        return 0 if args.command != 'run' or result['state'] == 'complete' else 1
    except (OSError, ValueError, RuntimeError, KeyError, psycopg.Error) as error:
        # Do not print the DSN, password, connection options or owner token.
        reason = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        print(json.dumps({'state': 'failed', 'command': args.command,
                          'error': type(error).__name__, 'reason': reason},
                         ensure_ascii=False, sort_keys=True), file=output)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
