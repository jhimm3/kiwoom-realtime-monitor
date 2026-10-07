"""v1/v2 gates on a disposable, network-isolated PostgreSQL fixture only.

The companion NAS shell starts a fresh tmpfs-backed PostgreSQL container and an
offline Python container sharing its network namespace. No saved NAS credentials
or production volumes are mounted. This is correctness acceptance, not a load
baseline. Never seal the persistent replay DB with this controlled source clock.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import unittest
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]


ADMIN = 'kiwoom_replay_fixture_admin'
SOURCE_ORIGIN = '2026-10-06T08:55:00+09:00'
_STAGE = 'startup'


def mark_stage(value):
    global _STAGE
    _STAGE = value


def make_secrets(directory):
    directory = directory.resolve(strict=True)
    value = {'version': 1, 'fixture_id': secrets.token_hex(16),
             'admin_password': secrets.token_hex(32), 'role_password': secrets.token_hex(32),
             'owner_token': secrets.token_hex(16)}
    for name, payload in (('fixture.json', json.dumps(value)), ('postgres-password', value['admin_password'])):
        descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    return value['fixture_id']


def read_secrets(path):
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError('temporary_fixture_secret_permissions_invalid')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (set(value) != {'version', 'fixture_id', 'admin_password', 'role_password', 'owner_token'}
            or type(value['version']) is not int or value['version'] != 1
            or any(type(value[key]) is not str or not re.fullmatch('[a-f0-9]{64}', value[key])
                   for key in ('admin_password', 'role_password'))
            or any(type(value[key]) is not str or not re.fullmatch('[a-f0-9]{32}', value[key])
                   for key in ('fixture_id', 'owner_token'))):
        raise RuntimeError('temporary_fixture_secret_invalid')
    return value


def run(path, *, top20_lifecycle=False):
    mark_stage('load_fixture_and_test_modules')
    import psycopg
    from psycopg import sql
    from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline
    from kiwoom_monitor.central_server.diagnostic_top20_seed import Top20FixtureClock

    mark_stage('read_temporary_fixture_secrets')
    value = read_secrets(path)
    admin_url = 'postgresql://' + ADMIN + ':' + quote(value['admin_password']) + '@127.0.0.1:5432/postgres'
    url = ('postgresql://' + baseline.ROLE_NAME + ':' + quote(value['role_password'])
           + '@127.0.0.1:5432/' + baseline.DATABASE_NAME)
    mark_stage('verify_temporary_postgres_identity')
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(),current_user,current_setting('kiwoom.replay_fixture_id',true)")
            if cursor.fetchone() != ('postgres', ADMIN, value['fixture_id']):
                raise RuntimeError('disposable_postgres_fixture_identity_mismatch')
            cursor.execute('SELECT datname FROM pg_database ORDER BY datname')
            if cursor.fetchall() != [('postgres',), ('template0',), ('template1',)]:
                raise RuntimeError('disposable_postgres_must_be_empty')
            mark_stage('create_temporary_replay_role_and_database')
            cursor.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                                   'NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD {}').format(
                                       sql.Identifier(baseline.ROLE_NAME), sql.Literal(value['role_password'])))
            cursor.execute(sql.SQL('CREATE DATABASE {} OWNER {} TEMPLATE template0').format(
                sql.Identifier(baseline.DATABASE_NAME), sql.Identifier(baseline.ROLE_NAME)))
            cursor.execute(sql.SQL('REVOKE CONNECT ON DATABASE {} FROM PUBLIC').format(sql.Identifier(baseline.DATABASE_NAME)))

    mark_stage('provision_empty_temporary_replay_database')
    baseline.provision_existing_empty_database(url, value['owner_token'])
    mark_stage('seal_and_restore_v1_baseline')
    with baseline.ReplayDatabaseLease(url, value['owner_token']) as lease:
        first = lease.seal()
        lease.restore(first['baseline_id'])

    mark_stage('seed_seal_and_restore_v2_cache_baseline')
    source_origin = '2026-10-06T09:30:01+09:00' if top20_lifecycle else SOURCE_ORIGIN
    clock = Top20FixtureClock(datetime.fromisoformat(source_origin))
    with baseline.ReplayDatabaseLease(url, value['owner_token'], baseline_version=2, cache_clock=clock) as lease:
        # Explicit controlled seed: v1 never captures/resets this cache table.
        with lease.connection.cursor() as cursor:
            cursor.execute('INSERT INTO central_api_query_cache '
                           '(cache_key,api_id,expires_at,payload_json,has_next,next_key) '
                           "VALUES('immutable-cache-seed','ka10001',%s,%s::jsonb,false,'')",
                           (clock.wall_time() + 60, json.dumps({'fixture': value['fixture_id']})))
        second = lease.seal()
        lease.restore(second['baseline_id'])
        store = lease.store()
        if store.load_query('immutable-cache-seed').payload != {'fixture': value['fixture_id']}:
            raise RuntimeError('source_dated_cache_seed_not_retained')

    # Tests receive only temporary cluster credentials, never operational DSNs.
    for name in tuple(os.environ):
        if name.startswith(('KIWOOM_', 'MONITOR_', 'POSTGRES_', 'NAVER_', 'OPENAI_',
                            'GEMINI_', 'ANTHROPIC_', 'DART_', 'MOCK_', 'ACCOUNT_')):
            os.environ.pop(name, None)
    os.environ.update(KIWOOM_REPLAY_DATABASE_URL=url, KIWOOM_REPLAY_OWNER_TOKEN=value['owner_token'],
                      KIWOOM_REPLAY_SOURCE_ORIGIN=source_origin, KIWOOM_REPLAY_TEMPORARY_FIXTURE='1')
    mark_stage('run_v1_and_v2_postgres_acceptance_suites')
    outcomes = []
    suites = [
        ('tests.integration.test_recorded_replay_baseline_postgres.RecordedReplayBaselinePostgresTests', 4),
        ('tests.integration.test_replay_cache_baseline_postgres.ReplayCacheBaselinePostgresTests', 3),
        ('tests.integration.test_replay_cache_execution_postgres.ReplayCacheExecutionPostgresTests', 3),
        ('tests.integration.test_top20_replay_runtime_postgres.Top20ReplayRuntimePostgresTests', 3),
    ]
    if top20_lifecycle:
        suites.append(('tests.integration.test_top20_session_postgres.Top20SessionPostgresTests', 2))
    for name, expected in suites:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName(name))
        outcomes.append({'suite': name, 'tests': result.testsRun, 'skipped': len(result.skipped),
                         'passed': result.wasSuccessful() and result.testsRun == expected and not result.skipped})
    mark_stage('verify_v1_snapshot_and_v2_baseline_after_suites')
    with baseline.ReplayDatabaseLease(url, value['owner_token'], baseline_version=2, cache_clock=clock) as lease:
        lease.restore(second['baseline_id'])
        with lease.connection.cursor() as cursor:
            cursor.execute('SELECT baseline_id,manifest FROM replay_meta.baseline WHERE singleton')
            v1_id, manifest = cursor.fetchone()
            preserved = (v1_id == first['baseline_id'] and manifest == first['manifest']
                         and lease._tables_digest(cursor, 'replay_baseline', tables=baseline.TABLES) == manifest['tables'])
        if not preserved:
            raise RuntimeError('original_v1_snapshot_changed')
    success = all(item['passed'] for item in outcomes)
    print(json.dumps({'state': 'passed' if success else 'failed', 'source': str(ROOT),
                      'database': baseline.DATABASE_NAME, 'temporary_postgres': True,
                      'v1_baseline_id': first['baseline_id'], 'v2_baseline_id': second['baseline_id'],
                      'v1_snapshot_preserved': preserved, 'tests': sum(item['tests'] for item in outcomes),
                      'skipped': sum(item['skipped'] for item in outcomes), 'outcomes': outcomes,
                      'fixture': 'controlled_fixture', 'source_state_equivalent': False}), flush=True)
    return 0 if success else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--make-secrets', type=Path)
    group.add_argument('--secrets-file', type=Path)
    parser.add_argument('--top20-lifecycle', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.make_secrets:
            print(make_secrets(args.make_secrets), flush=True)
            return 0
        return run(args.secrets_file, top20_lifecycle=args.top20_lifecycle)
    except Exception as error:
        # libpq/SQL errors can contain secrets; do not echo their text or DSN.
        message = str(error)
        safe_code = message if re.fullmatch('[a-z0-9_]{1,100}', message) else None
        print(json.dumps({'state': 'failed', 'stage': _STAGE,
                          'error_type': type(error).__name__, 'error_code': safe_code}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
