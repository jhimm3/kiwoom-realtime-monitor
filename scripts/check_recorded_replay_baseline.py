"""Provision and check the explicitly owned replay database on the NAS.

Run as an offline operator process in the existing server container. --provision
uses the server DSN only to create the fixed role/database via postgres; it never
reads operational tables. Subsequent acceptance uses only the replay role.
Credentials are kept in a mode-0600 file under the existing secrets mount.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import unittest
from urllib.parse import quote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]

import psycopg
from psycopg import sql
from kiwoom_monitor.central_server import diagnostic_replay_baseline as baseline
from scripts.run_postgres_access_integration import _dedicated_url


def _endpoint(server_url):
    parts = urlsplit(server_url)
    if (parts.scheme not in {'postgres', 'postgresql'} or not parts.hostname
            or not parts.username or parts.query or parts.fragment):
        raise ValueError('server_database_endpoint_invalid')
    host = parts.hostname
    if ':' in host:
        host = '[' + host + ']'
    return host + ':' + str(parts.port or 5432)


@contextmanager
def _locked_secrets(directory):
    import fcntl  # NAS operator tool; not a Windows daemon or product route.
    directory = directory.resolve(strict=True)
    if not directory.is_dir():
        raise RuntimeError('existing_secrets_directory_required')
    path = directory / 'recorded-replay-setup.lock'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
            raise RuntimeError('replay_setup_lock_permissions_invalid')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield directory
    finally:
        os.close(descriptor)


def _settings(directory, endpoint, *, provision):
    path = directory / 'recorded-replay-database.json'
    created = False
    if not path.exists():
        if not provision:
            raise RuntimeError('replay_resource_not_prepared_use_provision')
        value = {'version': 1, 'endpoint': endpoint, 'password': secrets.token_hex(32),
                 'owner_token': secrets.token_hex(16)}
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        created = True
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError('replay_secret_file_permissions_invalid')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (set(value) != {'version', 'endpoint', 'password', 'owner_token'}
            or value['version'] != 1 or value['endpoint'] != endpoint
            or not isinstance(value['password'], str) or len(value['password']) != 64
            or not isinstance(value['owner_token'], str) or len(value['owner_token']) != 32):
        raise RuntimeError('replay_secret_file_invalid_or_endpoint_changed')
    return value, created


def _replay_url(settings, database=baseline.DATABASE_NAME):
    return ('postgresql://' + baseline.ROLE_NAME + ':' + quote(settings['password'], safe='')
            + '@' + settings['endpoint'] + '/' + database)


def _provision_role_database(server_url, settings):
    parts = urlsplit(server_url)
    maintenance = urlunsplit((parts.scheme, parts.netloc, '/postgres', '', ''))
    with psycopg.connect(maintenance, autocommit=True, connect_timeout=5,
                        options='-c lock_timeout=5000 -c statement_timeout=60000') as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database(),current_user,rolsuper,rolcreatedb,rolcreaterole '
                           'FROM pg_roles WHERE rolname=current_user')
            identity = cursor.fetchone()
            if (not identity or identity[0] != 'postgres' or identity[1] == baseline.ROLE_NAME
                    or not (identity[2] or (identity[3] and identity[4]))):
                raise RuntimeError('replay_provision_admin_privileges_required')
            cursor.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,'
                           'rolcanlogin FROM pg_roles WHERE rolname=%s', (baseline.ROLE_NAME,))
            role = cursor.fetchone()
            cursor.execute('SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s',
                           (baseline.DATABASE_NAME,))
            database = cursor.fetchone()
            if database and database[0] != baseline.ROLE_NAME:
                raise RuntimeError('replay_existing_database_owner_mismatch')
            if role:
                if any(role[:5]) or not role[5]:
                    raise RuntimeError('replay_existing_role_privileges_mismatch')
                cursor.execute('SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member '
                               'WHERE r.rolname=%s', (baseline.ROLE_NAME,))
                if cursor.fetchone():
                    raise RuntimeError('replay_existing_role_membership_unsupported')
                # Authenticate the saved secret; never adopt or reset another role.
                target = baseline.DATABASE_NAME if database else 'postgres'
                with psycopg.connect(_replay_url(settings, target), connect_timeout=5):
                    pass
            else:
                cursor.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                                       'NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD {}').format(
                    sql.Identifier(baseline.ROLE_NAME), sql.Literal(settings['password'])))
            if not database:
                cursor.execute(sql.SQL('CREATE DATABASE {} OWNER {} TEMPLATE template0').format(
                    sql.Identifier(baseline.DATABASE_NAME), sql.Identifier(baseline.ROLE_NAME)))
            # Resume safely if a prior invocation stopped after CREATE DATABASE.
            cursor.execute(sql.SQL('REVOKE CONNECT ON DATABASE {} FROM PUBLIC').format(
                sql.Identifier(baseline.DATABASE_NAME)))
            cursor.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(
                sql.Identifier(baseline.DATABASE_NAME), sql.Identifier(baseline.ROLE_NAME)))


def _prepare_baseline(settings):
    url = _replay_url(settings)
    with baseline._maintenance_connection(url) as connection:
        marker = connection.execute("SELECT to_regclass('replay_meta.ownership')").fetchone()[0]
    if marker is None:
        baseline.provision_existing_empty_database(url, settings['owner_token'])
    with baseline.ReplayDatabaseLease(url, settings['owner_token']) as lease:
        status = lease.status()
        if not status['baseline_sealed']:
            with lease.connection.cursor() as cursor:
                cursor.execute('INSERT INTO central_documents '
                               '(collection,owner,document_key,updated_at,document_json) '
                               'VALUES(%s,%s,%s,0,%s::jsonb) ON CONFLICT DO NOTHING',
                               ('replay_baseline_gate', 'controlled_fixture', 'seed', '{"value":123}'))
            lease.seal()
        status = lease.status()
        lease.restore(status['baseline_id'])
        return status['baseline_id']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provision', action='store_true',
                        help='explicitly create the fixed replay role/database if absent')
    parser.add_argument('--secrets-directory', type=Path, default=Path('/app/secrets'))
    parser.add_argument('--execution-gates', action='store_true',
                        help='capture/window/native replay gates on the already sealed replay DB')
    parser.add_argument('--recorded-capture-gates', action='store_true',
                        help='run capture invariants and recorded collector replay gates together')
    args = parser.parse_args(argv)
    if args.execution_gates and args.recorded_capture_gates:
        parser.error('choose one acceptance gate set')
    if (args.execution_gates or args.recorded_capture_gates) and args.provision:
        parser.error('acceptance gates require an already provisioned and sealed replay DB')
    stage = 'settings'
    try:
        server_url = os.environ.get('KIWOOM_SERVER_DATABASE_URL', '')
        endpoint = _endpoint(server_url)
        with _locked_secrets(args.secrets_directory) as directory:
            settings, _ = _settings(directory, endpoint, provision=args.provision)
            if args.provision:
                stage = 'provision_role_database'
                _provision_role_database(server_url, settings)
                stage = 'prepare_controlled_baseline'
                baseline_id = _prepare_baseline(settings)
            else:
                with baseline.ReplayDatabaseLease(_replay_url(settings), settings['owner_token']) as lease:
                    baseline_id = lease.status()['baseline_id']
                    if not baseline_id:
                        raise RuntimeError('replay_sealed_baseline_required')
            # The test runner receives no operational DSN or broker credentials.
            for name in tuple(os.environ):
                if (name.startswith(('KIWOOM_', 'MONITOR_', 'POSTGRES_', 'NAVER_', 'OPENAI_',
                                     'GEMINI_', 'ANTHROPIC_', 'DART_', 'MOCK_', 'ACCOUNT_'))):
                    os.environ.pop(name, None)
            os.environ['KIWOOM_REPLAY_DATABASE_URL'] = _replay_url(settings)
            os.environ['KIWOOM_REPLAY_OWNER_TOKEN'] = settings['owner_token']
            # The capture invariant suite reads this dedicated URL directly;
            # keep it pointed at the same isolated replay database.
            # Capture invariants use the existing diagnostic test DB; recorded
            # execution gates use the separately sealed replay DB above.
            os.environ['KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL'] = _dedicated_url(server_url)
            stage = 'postgres_acceptance'
            if args.recorded_capture_gates:
                suite = unittest.TestSuite((
                    unittest.defaultTestLoader.loadTestsFromName(
                        'tests.integration.test_recorded_workload_capture_postgres.RecordedWorkloadCapturePostgresTests'),
                    unittest.defaultTestLoader.loadTestsFromName(
                        'tests.integration.test_recorded_execution_postgres.RecordedExecutionPostgresTests'),
                ))
                expected_tests = 7
            else:
                suite_name = ('tests.integration.test_recorded_execution_postgres.RecordedExecutionPostgresTests'
                              if args.execution_gates else
                              'tests.integration.test_recorded_replay_baseline_postgres.RecordedReplayBaselinePostgresTests')
                suite = unittest.defaultTestLoader.loadTestsFromName(suite_name)
                expected_tests = 3 if args.execution_gates else 4
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            success = result.wasSuccessful() and result.testsRun == expected_tests and not result.skipped
            print(json.dumps({'state': 'passed' if success else 'failed',
                              'database': baseline.DATABASE_NAME, 'source': str(ROOT),
                              'baseline_id': baseline_id, 'tests': result.testsRun,
                              'skipped': len(result.skipped), 'fixture': 'controlled_fixture',
                              'source_state_equivalent': False}), flush=True)
            return 0 if success else 1
    except Exception as error:
        # SQL/connection exceptions may contain credentials: print only the type.
        print(json.dumps({'state': 'failed', 'stage': stage,
                          'error_type': type(error).__name__}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
