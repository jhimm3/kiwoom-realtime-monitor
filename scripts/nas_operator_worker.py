"""Container-only operator worker. Never run on the host or in the live server."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import unittest

ROOT = Path('/app/candidate')
WORK = Path('/run/operator')


def require(value, code):
    if not value:
        raise RuntimeError(code)


def memory_limit():
    for name in ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
        try:
            value = int(Path(name).read_text().strip())
        except (OSError, ValueError):
            continue
        require(0 < value < 64 * 1024 ** 3, 'worker_memory_limit_not_enforced')
        return value
    raise RuntimeError('worker_memory_limit_unavailable')


def run_tests(names):
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    failed_tests = []
    for test, details in result.failures + result.errors:
        rendered = str(details)
        lines = rendered.strip().splitlines()
        final_line = lines[-1].strip() if lines else ''
        exception_type = final_line.split(':', 1)[0].rsplit('.', 1)[-1]
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', exception_type):
            exception_type = 'unknown'
        frames = []
        for match in re.finditer(r'File "([^"\r\n]+)", line ([0-9]+), in ([A-Za-z_][A-Za-z0-9_]*)', rendered):
            path = match.group(1).replace('\\', '/')
            frames.append({'file': path.rsplit('/', 1)[-1][:128],
                           'line': int(match.group(2)), 'function': match.group(3)[:128]})
        try:
            test_id = test.id()
        except Exception:
            test_id = 'unknown'
        failed_tests.append({'test_id': test_id[:256], 'error_type': exception_type,
                             'traceback_locations': frames[-8:]})
    return {'state': 'passed' if result.wasSuccessful() and result.testsRun > 0 and not result.skipped else 'failed',
            'tests': result.testsRun, 'skipped': len(result.skipped),
            'failures': len(result.failures), 'errors': len(result.errors),
            'failed_tests': failed_tests[:20], 'failed_tests_truncated': len(failed_tests) > 20}


def database_setup(request):
    import psycopg
    from psycopg import sql
    admin = 'postgresql://kiwoom_operator_admin:' + request['admin_password'] + '@127.0.0.1:5432/postgres'
    deadline = time.monotonic() + 60
    while True:
        try:
            connection = psycopg.connect(admin, autocommit=True, connect_timeout=2)
            break
        except psycopg.Error:
            require(time.monotonic() < deadline, 'temporary_postgres_not_ready')
            time.sleep(.5)
    password = os.urandom(32).hex()
    with connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT datname FROM pg_database')
            require({row[0] for row in cursor.fetchall()} == {'postgres', 'template0', 'template1'}, 'cluster_not_fresh')
            for role in ('kiwoom_operator_fixture', 'kiwoom_monitor_replay'):
                cursor.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT').format(sql.Identifier(role), sql.Literal(password)))
            for name, role in (('kiwoom_monitor_diagnostic_test', 'kiwoom_operator_fixture'),
                               ('kiwoom_monitor_replay_test', 'kiwoom_monitor_replay')):
                cursor.execute(sql.SQL('CREATE DATABASE {} OWNER {} TEMPLATE template0').format(sql.Identifier(name), sql.Identifier(role)))
                cursor.execute(sql.SQL('REVOKE CONNECT ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
    base = 'postgresql://kiwoom_operator_fixture:' + password + '@127.0.0.1:5432/'
    return base + 'kiwoom_monitor_diagnostic_test', 'postgresql://kiwoom_monitor_replay:' + password + '@127.0.0.1:5432/kiwoom_monitor_replay_test'


def test(request, diagnostic_url, replay_url):
    if request['profile'] == 'replay-cache':
        # Existing fixture builder owns baseline/restore gates; credentials are temporary.
        import importlib.util
        spec = importlib.util.spec_from_file_location('operator_cache_gate', ROOT / 'scripts/check_replay_cache_baseline.py')
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        # This existing gate requires a pristine cluster and provisions its own role.
        # Our general setup therefore is not used for this profile (see main).
        secret = WORK / 'fixture'
        secret.mkdir(mode=0o700)
        gate.make_secrets(secret)
        fixture = json.loads((secret / 'fixture.json').read_text())
        fixture['admin_password'] = request['admin_password']
        fixture['fixture_id'] = request['job_id']
        (secret / 'fixture.json').write_text(json.dumps(fixture))
        os.chmod(str(secret / 'fixture.json'), 0o600)
        try:
            import contextlib
            import io
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = gate.run(secret / 'fixture.json', top20_lifecycle=False)
            values = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith('{')]
            outcome = values[-1]
            require(rc == 0 and outcome.get('state') == 'passed' and outcome.get('tests', 0) > 0 and
                    outcome.get('skipped') == 0, 'cache_acceptance_incomplete')
            return outcome
        finally:
            for name in ('fixture.json', 'postgres-password'):
                path = secret / name
                if path.exists():
                    path.write_text('')
                    path.unlink()
            secret.rmdir()
    os.environ.update(KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL=diagnostic_url)
    from kiwoom_monitor.central_server.database import PostgresQueryStore
    PostgresQueryStore(diagnostic_url).initialize()
    names = request['test'] if request['profile'] == 'selected' else [
        'tests.integration.test_storage_boundary_postgres.PostgresStorageBoundaryTests',
        'tests.integration.test_postgres_access_postgres.PostgresAccessIntegrationTests']
    return run_tests(names)


def replay(request, url):
    import psycopg
    profile = request.get('baseline_profile')
    require(profile in (None, 'empty-v1'), 'invalid_baseline_profile')
    if profile:
        require(not request.get('baseline'), 'conflicting_baseline')
        baseline = dict(version=1, owner_token=os.urandom(16).hex(), source_state_equivalent=False)
    else:
        baseline = json.loads(Path('/run/baseline/baseline.json').read_text())
        raw = Path('/run/baseline/statements.json').read_bytes()
        require(hashlib.sha256(raw).hexdigest() == baseline['statements_sha256'], 'baseline_statement_hash_mismatch')
        statements = json.loads(raw)
        require(type(statements) is list and 0 < len(statements) <= 1000000 and
                all(type(value) is str and value for value in statements), 'invalid_baseline_statements')
        # Registered fixture SQL stays non-superuser in the disposable cluster.
        with psycopg.connect(url) as connection:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement, prepare=False)
    os.environ.update(KIWOOM_REPLAY_DATABASE_URL=url, KIWOOM_REPLAY_OWNER_TOKEN=baseline['owner_token'],
                      KIWOOM_DIAGNOSTIC_WORKLOAD_PATH='/tmp/replay-control.json')
    # Supervisor mounts the immutable trace at this reader's expected path.
    # No multi-gigabyte copy into the bounded /tmp filesystem is necessary.
    from kiwoom_monitor.central_server import diagnostic_replay_database_cli as cli
    args = ['run', '--trace-id', request['trace'],
            '--window-start', str(request['window_start']), '--window-end', str(request['window_end']),
            '--mode', request['mode'], '--concurrency', str(request['concurrency']),
            '--baseline-version', str(baseline['version'])]
    if profile:
        args += ['--baseline-profile', profile]
    else:
        args += ['--baseline-id', request['baseline']]
    if request.get('preflight_only'):
        require(profile == 'empty-v1' and not request.get('pause_operational'), 'invalid_preflight_request')
        args += ['--preflight-only']
    if request.get('expected_baseline_id'):
        args += ['--expected-baseline-id', request['expected_baseline_id']]
    args += ['--capture-policy', request.get('capture_policy', 'complete')]
    if baseline['version'] == 2:
        args += ['--source-origin', baseline['source_origin']]
    for key, flag in (('include_workload', '--include-workload'), ('exclude_workload', '--exclude-workload'),
                      ('collector_component', '--collector-component')):
        for value in request[key]:
            args += [flag, value]
    import io
    output = io.StringIO()
    rc = cli.main(args, output=output)
    response = json.loads(output.getvalue())
    # This result contains no DSN/owner token; preserve exact call mapping and coverage.
    return {'state': 'passed' if rc == 0 else 'failed', 'replay': response,
            'source_state_equivalent': baseline['source_state_equivalent']}


def main():
    outcome = {'state': 'failed'}
    try:
        require(os.getuid() != 0 and Path('/app/candidate/src').is_dir() and
                not Path('/var/run/docker.sock').exists(), 'worker_isolation_required')
        limit = memory_limit()
        affinity = ','.join(str(x) for x in sorted(os.sched_getaffinity(0)))
        request = json.loads((WORK / 'request.json').read_text())
        # Only temporary fixture input is visible. No inherited operational configuration.
        sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
        if request['command'] == 'test' and request['profile'] == 'replay-cache':
            outcome = test(request, None, None)
        else:
            diagnostic_url, replay_url = database_setup(request)
            outcome = test(request, diagnostic_url, replay_url) if request['command'] == 'test' else replay(request, replay_url)
        outcome['memory_limit_bytes'] = limit
        outcome['cpu_affinity'] = affinity
        return 0 if outcome['state'] == 'passed' else 1
    except Exception as error:
        outcome.update(error_type=type(error).__name__)
        return 1
    finally:
        (WORK / 'result.json').write_text(json.dumps(outcome, sort_keys=True))


if __name__ == '__main__':
    raise SystemExit(main())
