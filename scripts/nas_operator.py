"""Installed, narrowly privileged NAS operator. Python 3.8 stdlib only.

Never import a candidate, run a candidate host script, or accept Docker options.
All mutable source is admitted as data before being mounted in a container.
"""
import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time

CONFIG = '/etc/kiwoom-nas/operator.json'
HELPER = '/usr/local/libexec/kiwoom-nas'
ROOT_LAUNCHER = '/usr/local/sbin/kiwoom-nas-root'
CLIENT = '/usr/local/bin/kiwoom-nas'
SUDOERS = '/etc/sudoers.d/kiwoom-nas-operator'
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}\Z')
HEX = re.compile(r'[a-f0-9]{64}\Z')
TEST = re.compile(r'tests\.(?:unit|integration)\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*\Z')
WORD = re.compile(r'[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,99}\Z')
LABEL = 'com.kiwoom.operator-job'
CLEAN_ENV = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/'}
CONTRACT = ('pyproject.toml', 'deploy/synology/server.Dockerfile',
            'src/kiwoom_monitor/central_server/central_schema.py',
            'src/kiwoom_monitor/central_server/schema_migrations.py')


class Rejected(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise Rejected(code)


def installed_paths():
    return {HELPER + '/nas_operator.py', HELPER + '/nas_operator_worker.py',
            ROOT_LAUNCHER, CLIENT, CONFIG, SUDOERS}


def access_file(path):
    require(path in (SUDOERS, CLIENT, ROOT_LAUNCHER), 'access_path_outside_scope')
    with Tree(str(Path(path).parent), owners=(0,), protected=True) as tree:
        try:
            data = tree.read(Path(path).name, 8 * 1024 ** 2)
        except FileNotFoundError:
            return None
        with tree.parent(Path(path).name) as (parent, leaf):
            mode = stat.S_IMODE(os.stat(leaf, dir_fd=parent, follow_symlinks=False).st_mode)
        return data, mode


def restore_access_file(path, previous):
    require(path in (SUDOERS, CLIENT, ROOT_LAUNCHER), 'access_path_outside_scope')
    require(path != SUDOERS or previous is None, 'sudo_access_must_not_be_restored')
    with Tree(str(Path(path).parent), owners=(0,), protected=True) as tree:
        if previous is not None:
            tree.write(Path(path).name, previous[0], previous[1])
        else:
            with tree.parent(Path(path).name) as (parent, leaf):
                try:
                    os.unlink(leaf, dir_fd=parent)
                except FileNotFoundError:
                    pass
                os.fsync(parent)


def cpu_set(value):
    """Parse bounded Linux cpulists without accepting Docker option text."""
    require(type(value) is str and 0 < len(value) <= 256, 'invalid_cpu_set')
    result = set()
    for part in value.split(','):
        require(re.fullmatch(r'[0-9]{1,4}(?:-[0-9]{1,4})?', part), 'invalid_cpu_set')
        bounds = [int(x) for x in part.split('-')]
        low, high = bounds[0], bounds[-1]
        require(0 <= low <= high <= 4095, 'invalid_cpu_set')
        values = set(range(low, high + 1))
        require(not result & values, 'invalid_cpu_set')
        result.update(values)
    return result


def cpu_limits(available):
    require(type(available) is set and len(available) >= 2 and
            all(type(x) is int and 0 <= x <= 4095 for x in available), 'cpu_affinity_unavailable')
    selected = sorted(available)[:2]
    # Both jobs share a two-core ceiling; cpuset is not an exclusive CPU reservation.
    return {'worker_cpuset': ','.join(str(x) for x in selected), 'pg_cpuset': str(selected[0])}


def validate_cpu_limits(config, available):
    worker, postgres = cpu_set(config.get('worker_cpuset')), cpu_set(config.get('pg_cpuset'))
    require(len(worker) <= 2 and len(postgres) <= 1 and postgres <= worker <= available,
            'cpu_affinity_unavailable')
    return worker, postgres


def verify_cpu_affinity(actual, expected):
    require(cpu_set(actual) == cpu_set(expected), 'cpu_limit_not_enforced')


def identifier(value):
    require(type(value) is str and ID.fullmatch(value), 'invalid_identifier')
    return value


def relative(value):
    require(type(value) is str and value and '\\' not in value and
            all(x not in ('', '.', '..') for x in value.split('/')) and
            not value.startswith('/'), 'unsafe_relative_path')
    return value.split('/')


def decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    return json.loads(data.decode('utf-8'), object_pairs_hook=pairs)


class Tree:
    """Directory-fd anchored IO; no follow, hardlinks or special input files."""
    def __init__(self, path, owners=None, protected=False):
        require(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW'), 'linux_filesystem_required')
        self.path = str(path)
        self.owners = set(owners) if owners is not None else None
        self.protected = protected
        self.fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in relative(self.path.lstrip('/')):
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
                os.close(self.fd)
                self.fd = next_fd
                if protected:
                    self._check(os.fstat(self.fd), directory=True)
            self.identity = (os.fstat(self.fd).st_dev, os.fstat(self.fd).st_ino)
        except BaseException:
            os.close(self.fd)
            raise

    def _check(self, info, directory=False):
        require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), 'special_file_rejected')
        if self.owners is not None:
            require(info.st_uid in self.owners, 'file_owner_rejected')
        if not directory:
            require(info.st_nlink == 1, 'hardlink_rejected')
        if self.protected:
            require(info.st_uid == 0 and not info.st_mode & 0o022, 'protected_path_writable')

    @contextlib.contextmanager
    def parent(self, name, create=False):
        parts = relative(name)
        fd = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                created = False
                if create:
                    try:
                        os.mkdir(part, 0o755, dir_fd=fd)
                        created = True
                    except FileExistsError:
                        pass
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                if created:
                    os.fchmod(nxt, 0o755)  # Read-only bind consumers are non-root.
                os.close(fd)
                fd = nxt
                self._check(os.fstat(fd), directory=True)
            yield fd, parts[-1]
        finally:
            os.close(fd)

    def read(self, name, maximum=2 * 1024 * 1024):
        with self.parent(name) as (parent, leaf):
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                before = os.fstat(fd)
                self._check(before)
                require(before.st_size <= maximum, 'input_size_limit')
                with os.fdopen(os.dup(fd), 'rb') as stream:
                    data = stream.read(maximum + 1)
                after = os.fstat(fd)
                require(len(data) <= maximum and len(data) == before.st_size and
                        (before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                        (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'input_changed_during_copy')
                return data
            finally:
                os.close(fd)

    def json(self, name):
        return decode(self.read(name))

    def write(self, name, data, mode=0o600):
        with self.parent(name, create=True) as (parent, leaf):
            temporary = '.operator-' + secrets.token_hex(16)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         mode, dir_fd=parent)
            try:
                os.fchmod(fd, mode)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, leaf, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def put_json(self, name, value):
        self.write(name, (json.dumps(value, sort_keys=True, ensure_ascii=True) + '\n').encode())

    @contextlib.contextmanager
    def lock(self, name):
        import fcntl
        with self.parent(name) as (parent, leaf):
            fd = os.open(leaf, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=parent)
            try:
                self._check(os.fstat(fd))
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Rejected('operation_busy')
                yield
            finally:
                os.close(fd)  # Never unlink a flock inode.

    def verify_identity(self):
        with Tree(self.path, owners=self.owners, protected=self.protected) as current:
            require(current.identity == self.identity, 'directory_replaced')

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        os.close(self.fd)


def source_name(name):
    relative(name)
    require(name == 'pyproject.toml' or name.startswith(('src/', 'tests/', 'scripts/')), 'source_outside_allowlist')


def validate_manifest(manifest, release_id, contract):
    require(type(manifest) is dict and manifest.get('format') == 1 and
            manifest.get('release_id') == release_id, 'invalid_release_manifest')
    require(manifest.get('contract') == contract and set(contract) == set(CONTRACT), 'contract_requires_admin_update')
    files = manifest.get('files')
    require(type(files) is dict and 0 < len(files) <= 20000, 'invalid_source_files')
    for name, digest in files.items():
        source_name(name)
        require(type(digest) is str and HEX.fullmatch(digest), 'invalid_file_hash')
    build = manifest.get('server_build')
    identifier(build)
    content = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    require(release_id == build + '-' + content[:16], 'release_content_id_mismatch')
    require('src/kiwoom_monitor/central_server/app.py' in files, 'build_source_missing')
    expected_src = hashlib.sha256(json.dumps({k: v for k, v in files.items() if k.startswith('src/')},
                                            sort_keys=True).encode()).hexdigest()
    require(manifest.get('src_hash') == expected_src, 'source_manifest_hash_mismatch')
    for name in CONTRACT:
        if name != 'deploy/synology/server.Dockerfile':
            require(name in files, 'contract_source_missing')
            require(files[name] == contract[name], 'contract_file_hash_mismatch')
    return content


def admit_source(incoming, private, release_id, contract):
    """Copy only manifested bytes, then publish a verified private snapshot."""
    identifier(release_id)
    raw = incoming.read('manifest.json')
    manifest = decode(raw)
    content = validate_manifest(manifest, release_id, contract)
    base = 'releases/' + release_id
    try:
        require(private.read(base + '/manifest.json') == raw, 'admitted_release_conflict')
        for name, expected in manifest['files'].items():
            require(hashlib.sha256(private.read(base + '/' + name, 16 * 1024 * 1024)).hexdigest() == expected,
                    'admitted_release_changed')
        return manifest, content
    except FileNotFoundError:
        pass
    temporary = '.admit-' + secrets.token_hex(16)
    total = 0
    try:
        for name, expected in manifest['files'].items():
            data = incoming.read(name, 16 * 1024 * 1024)
            total += len(data)
            require(total <= 512 * 1024 * 1024, 'source_total_size_limit')
            require(hashlib.sha256(data).hexdigest() == expected, 'source_file_hash_mismatch')
            if name == 'src/kiwoom_monitor/central_server/app.py':
                marker = re.search(rb'^SERVER_BUILD = "([A-Za-z0-9._-]+)"$', data, re.M)
                require(marker is not None and marker.group(1).decode() == manifest['server_build'], 'build_marker_mismatch')
            private.write(temporary + '/' + name, data, mode=0o644)
        require(incoming.read('manifest.json') == raw, 'manifest_changed_during_copy')
        incoming.verify_identity()
        private.write(temporary + '/manifest.json', raw, mode=0o644)
        with private.parent(base, create=True) as (parent, leaf):
            os.rename(temporary, leaf, src_dir_fd=private.fd, dst_dir_fd=parent)
            os.fsync(parent)
    finally:
        cleanup_directory(private, temporary)
    return manifest, content


def cleanup_directory(tree, name):
    """Only a generated direct child in the protected private tree."""
    require(re.fullmatch(r'(?:\.admit-|job-)[a-f0-9]{32}', name), 'cleanup_scope_invalid')
    tree.verify_identity()
    path = Path(tree.path) / name
    if path.exists():
        require(not path.is_symlink(), 'cleanup_symlink')
        shutil.rmtree(str(path))  # Private parent; candidate has no access to it.


def fingerprint(value):
    profile = {key: value[key] for key in ('Id', 'Image', 'Config', 'HostConfig', 'Mounts')}
    profile['networks'] = {name: item['NetworkID'] for name, item in
                           value['NetworkSettings']['Networks'].items()}
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()


def idle(snapshot):
    require(type(snapshot) is dict, 'diagnostic_state_unknown')
    for key in ('diagnostic_tool', 'trace_capture'):
        require(type(snapshot.get(key)) is dict and snapshot[key].get('enabled') is False, 'diagnostics_enabled')
    require(snapshot.get('paused_workloads') == [] and snapshot.get('active_runs') == [], 'workload_or_run_active')
    trace = snapshot.get('trace')
    require(type(trace) is dict and trace.get('state') in ('off', 'complete'), 'trace_not_durably_idle')
    if trace['state'] != 'off':
        for key in ('queued', 'pending_events', 'copy_reserved_bytes', 'charged_bytes',
                    'packing_events', 'packed_events'):
            require(type(trace.get(key)) is int and trace[key] == 0, 'trace_retains_memory_or_state_unknown')
        # Replay eligibility is checked on registration. A durably finished trace
        # with rejected inputs must not permanently disable maintenance commands.
        require(trace.get('accepted') == trace.get('written') and type(trace.get('accepted')) is int,
                'trace_incomplete')


SNAPSHOT_CODE = '''import json,os,urllib.request
from pathlib import Path
base='http://127.0.0.1:'+os.environ.get('KIWOOM_SERVER_PORT','8787')
headers={'Authorization':'Bearer '+os.environ['MONITOR_SERVER_ACCESS_TOKEN']}
def get(p):
    with urllib.request.urlopen(urllib.request.Request(base+p,headers=headers),timeout=5) as r:return json.load(r)
h=get('/health');w=get('/api/v1/diagnostics/workloads');t=get('/api/v1/diagnostics/trace');r=get('/api/v1/diagnostics/reports?limit=20')
env=dict(x.split(b'=',1) for x in Path('/proc/1/environ').read_bytes().split(b'\\0') if b'=' in x)
print(json.dumps({'health':h.get('status'),'server_build':h.get('server_build'),
'source_path':env.get(b'PYTHONPATH',b'').decode(),'diagnostic_tool':w.get('diagnostic_tool'),
'trace_capture':w.get('trace_capture'),'trace':{k:t[k] for k in ('state','queued','pending_events','copy_reserved_bytes','charged_bytes','packing_events','packed_events','accepted','written','known_dropped','input_rejected','input_capture_censored') if k in t},
'paused_workloads':[k for k,v in w['workloads'].items() if v.get('paused_by_diagnostic')],
'active_runs':[x.get('report_id') for x in r['items'] if x.get('state') in ('starting','running','finalizing')]}))
'''


class Operator:
    def __init__(self, config, private, run=None):
        self.config = config
        self.private = private
        self.run_process = run or subprocess.run

    def docker(self, args, timeout=30, log=None):
        command = [self.config['docker'], '--host', 'unix:///var/run/docker.sock'] + list(args)
        require(all(type(x) is str for x in command), 'non_string_argument')
        try:
            if log is None:
                result = self.run_process(command, env=CLEAN_ENV, cwd='/', stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
                require(len(result.stdout) <= 8 * 1024 * 1024, 'docker_response_too_large')
            else:
                result = self.run_process(command, env=CLEAN_ENV, cwd='/', stdin=subprocess.DEVNULL,
                                          stdout=log, stderr=log, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Rejected('docker_command_timeout')
        require(result.returncode == 0, 'docker_command_failed')
        return result.stdout if log is None else b''

    def inspect(self, container):
        result = decode(self.docker(['inspect', container]))
        require(type(result) is list and len(result) == 1, 'container_inspection_invalid')
        return result[0]

    def identities(self, running=True):
        for kind in ('server', 'database'):
            value = self.inspect(self.config[kind + '_id'])
            require(fingerprint(value) == self.config[kind + '_fingerprint'], 'approved_container_changed')
            if running or kind == 'database':
                require(value['State']['Running'] is True, 'required_container_stopped')

    def snapshot(self):
        return decode(self.docker(['exec', self.config['server_id'], 'python', '-I', '-c', SNAPSHOT_CODE]))

    def assert_mount_inode(self, host, destination):
        raw = self.docker(['exec', self.config['server_id'], 'python', '-I', '-c',
                          'import os,json,sys;s=os.stat(sys.argv[1]);print(json.dumps([s.st_dev,s.st_ino]))', destination])
        require(tuple(decode(raw)) == host.identity, 'bind_mount_inode_mismatch')

    @contextlib.contextmanager
    def fence(self, recovery=False):
        with self.private.lock('operator.lock'), contextlib.ExitStack() as stack:
            require(self.access_state().get('state') == 'enabled', 'operator_access_revoked')
            self.identities(running=not recovery)
            journal = self.journal()
            if not recovery:
                require(journal.get('state') in ('idle', 'complete', 'rolled_back'), 'deployment_recovery_required')
                require(self.job_journal().get('state') in ('idle', 'complete'), 'job_recovery_required')
            store = stack.enter_context(Tree(self.config['store'], owners=(0, self.config['allowed_uid'])))
            control = stack.enter_context(Tree(self.config['control_dir'], owners=(0, self.config['allowed_uid'])))
            self.assert_mount_inode(store, '/app/source-runtime')
            self.assert_mount_inode(control, '/app/data/maintenance')
            require(hashlib.sha256(store.read('runner.py')).hexdigest() == self.config['runner_sha256'] and
                    hashlib.sha256(store.read('runtime.json')).hexdigest() == self.config['runtime_sha256'],
                    'approved_runtime_launcher_changed')
            stack.enter_context(control.lock('diagnostic-run.lock'))
            # One-shot scheduler holds these from arming until its POST completes.
            artifacts = stack.enter_context(Tree(self.config['artifacts'], owners=(0, self.config['allowed_uid'])))
            for name in os.listdir(artifacts.fd):
                if re.fullmatch(r'nas-trace-start-[0-9]{8}\.status\.json\.lock', name):
                    stack.enter_context(artifacts.lock(name))
            if self.inspect(self.config['server_id'])['State']['Running']:
                idle(self.snapshot())
            else:
                require(recovery and journal.get('state') in ('prepared', 'stopped', 'selected', 'needs_admin') and
                        journal.get('store_identity') == list(store.identity), 'stopped_server_recovery_unknown')
            store.verify_identity()
            control.verify_identity()
            self.identities(running=not recovery)
            yield store
            store.verify_identity()
            control.verify_identity()
            self.identities(running=not recovery)

    def journal(self):
        try:
            return self.private.json('deployment.json')
        except FileNotFoundError:
            return {'state': 'idle'}

    def job_journal(self):
        try:
            return self.private.json('job.json')
        except FileNotFoundError:
            return {'state': 'idle'}

    def access_state(self):
        try:
            return self.private.json('revocation.json')
        except FileNotFoundError:
            return {'state': 'enabled'}

    def revoke_access(self):
        # This changes only this installation's access files, so no live API is needed.
        # A running operator retains this lock until its actual work has drained.
        with self.private.lock('operator.lock'):
            require(self.journal().get('state') in ('idle', 'complete', 'rolled_back'), 'deployment_recovery_required')
            require(self.job_journal().get('state') in ('idle', 'complete'), 'job_recovery_required')
            index = self.private.json('install-backup-index.json')
            entries = index.get('entries', [])
            require(index.get('format') == 2 and index.get('state') == 'complete' and
                    len(entries) == len(installed_paths()) and
                    {x.get('path') for x in entries} == installed_paths(), 'revocation_snapshot_invalid')
            digest = hashlib.sha256(json.dumps(index, sort_keys=True).encode()).hexdigest()
            state = self.access_state()
            require(state.get('state') in ('enabled', 'prepared', 'revoked') and
                    (state['state'] == 'enabled' or state.get('index_hash') == digest), 'revocation_snapshot_changed')
            previous = {}
            for entry in entries:
                path = entry['path']
                if path not in (SUDOERS, CLIENT, ROOT_LAUNCHER):
                    continue
                require(type(entry.get('index')) is int and 0 <= entry['index'] < len(entries) and
                        type(entry.get('existed')) is bool and
                        type(entry.get('installed_sha256')) is str and HEX.fullmatch(entry['installed_sha256']) and
                        type(entry.get('installed_mode')) is int and 0 <= entry['installed_mode'] <= 0o777,
                        'revocation_snapshot_invalid')
                require(path != SUDOERS or not entry['existed'], 'previous_sudoers_rule_requires_administrator')
                old = None
                if entry['existed']:
                    data = self.private.read('install-backups/' + str(entry['index']), 8 * 1024 ** 2)
                    require(hashlib.sha256(data).hexdigest() == entry.get('sha256') and
                            type(entry.get('mode')) is int and 0 <= entry['mode'] <= 0o777 and
                            not entry['mode'] & 0o022, 'install_backup_changed')
                    old = data, entry['mode']
                current = access_file(path)
                matches_installed = current is not None and (
                    hashlib.sha256(current[0]).hexdigest(), current[1]) == (
                        entry['installed_sha256'], entry['installed_mode'])
                # A prepared journal allows a retry after unlink/restore or fsync failure.
                matches_previous = current == old and (state['state'] != 'enabled' or path == SUDOERS)
                require(matches_installed or matches_previous, 'installed_access_file_changed')
                if state['state'] == 'revoked':
                    require(current == old, 'revoked_access_file_reappeared')
                previous[path] = old
            if state['state'] != 'revoked':
                self.private.put_json('revocation.json', {'state': 'prepared', 'index_hash': digest})
                # Do not reinstate NOPASSWD on cleanup or validation failure.
                restore_access_file(SUDOERS, None)
                try:
                    checked = self.run_process([self.config['visudo'], '-c'], env=CLEAN_ENV, cwd='/',
                                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE, timeout=15)
                except subprocess.TimeoutExpired:
                    raise Rejected('sudoers_validation_timeout_after_revocation')
                require(checked.returncode == 0, 'sudoers_validation_failed_after_revocation')
                for path in (CLIENT, ROOT_LAUNCHER):
                    restore_access_file(path, previous[path])
                self.private.verify_identity()
                self.private.put_json('revocation.json', {'state': 'revoked', 'index_hash': digest})
            return {'temporary_access_revoked': True, 'access_files_restored': True,
                    'private_reports_and_inputs_preserved': True, 'server_restarted': False}

    def source(self, release_id):
        with Tree(self.config['store'] + '/releases/' + identifier(release_id),
                  owners=(0, self.config['allowed_uid'])) as incoming:
            return admit_source(incoming, self.private, release_id, self.config['contract'])

    def status(self):
        self.identities(running=False)
        journal = self.journal()
        result = {'journal': {k: journal.get(k) for k in ('state', 'target', 'previous')},
                  'job': self.job_journal(), 'installed': True, 'active_release': None,
                  'temporary_access': self.access_state()['state']}
        with Tree(self.config['store'], owners=(0, self.config['allowed_uid'])) as tree:
            result['active_release'] = tree.json('active.json')['release_id']
        if self.inspect(self.config['server_id'])['State']['Running']:
            snapshot = self.snapshot()
            result.update(snapshot=snapshot)
            try:
                idle(snapshot)
                result['diagnostics_idle'] = True
            except Rejected as error:
                result.update(diagnostics_idle=False, blocker=str(error))
        else:
            result.update(diagnostics_idle=False, blocker='server_stopped')
        return result

    def worker_argv(self, job, pg_name, release_id, extra_mounts=()):
        root = self.private.path
        return ['create', '--name', 'kiwoom-op-worker-' + job, '--label', LABEL + '=' + job,
                '--network', 'container:' + pg_name, '--user', '65534:65534', '--read-only',
                '--security-opt', 'no-new-privileges', '--cap-drop', 'ALL',
                '--memory', str(self.config['worker_memory']), '--memory-swap', str(self.config['worker_memory']),
                '--cpuset-cpus', self.config['worker_cpuset'], '--pids-limit', '256',
                '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m,mode=1777',
                '--mount', 'type=bind,src=' + root + '/releases/' + release_id + ',dst=/app/candidate,readonly',
                '--mount', 'type=bind,src=' + self.config['helper_dir'] + ',dst=/opt/kiwoom-operator,readonly',
                '--mount', 'type=bind,src=' + root + '/job-' + job + '/worker,dst=/run/operator',
                '--env', 'PYTHONDONTWRITEBYTECODE=1', '--env', 'HOME=/tmp'] + list(extra_mounts) + ['--entrypoint', 'python',
                self.config['runtime_image_id'], '-I', '/opt/kiwoom-operator/nas_operator_worker.py']

    def cleanup_containers(self, job):
        require(re.fullmatch('[a-f0-9]{32}', job), 'invalid_job_id')
        failed = False
        for name in ('kiwoom-op-worker-' + job, 'kiwoom-op-pg-' + job):
            # inspect failures cannot be confused with a missing container.
            ids = self.docker(['ps', '-aq', '--filter', 'name=^/' + name + '$']).decode().split()
            for cid in ids:
                value = self.inspect(cid)
                if value['Name'] != '/' + name or value['Config'].get('Labels', {}).get(LABEL) != job:
                    failed = True
                    continue
                try:
                    self.docker(['rm', '-f', cid], timeout=60)
                except Rejected:
                    failed = True
        require(not failed, 'job_cleanup_failed')

    def cleanup_job(self, job):
        self.cleanup_containers(job)
        base = 'job-' + job
        self.private.verify_identity()
        with self.private.parent(base) as (parent, leaf):
            try:
                info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return
            self.private._check(info, directory=True)
        for name in ('worker', 'postgres'):
            path = Path(self.private.path) / base / name
            if path.is_symlink():
                raise Rejected('job_directory_symlink')
            if path.exists():
                # Only our generated private child, after both containers are gone.
                shutil.rmtree(str(path))
        with self.private.parent(base + '/postgres-password') as (parent, leaf):
            try:
                os.unlink(leaf, dir_fd=parent)
                os.fsync(parent)
            except FileNotFoundError:
                pass

    def run_job(self, args, manifest, content):
        validate_cpu_limits(self.config, set(os.sched_getaffinity(0)))
        require(shutil.disk_usage(self.private.path).free >= self.config['minimum_disk_free'], 'insufficient_disk_headroom')
        available = next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))
        require(available >= self.config['worker_memory'] + self.config['pg_memory'] + 1024 ** 3,
                'insufficient_memory_headroom')
        with Tree(self.config['store'], owners=(0, self.config['allowed_uid'])) as store:
            active_before = store.read('active.json')
        job = secrets.token_hex(16)
        base = 'job-' + job
        worker = Path(self.private.path) / base / 'worker'
        password = secrets.token_hex(32)
        request = vars(args).copy()
        request.update(admin_password=password, job_id=job)
        pg_name = 'kiwoom-op-pg-' + job
        source_state_equivalent = False
        extra_mounts = []
        storage = args.storage if args.command == 'replay' else 'ram'
        pg = ['create', '--name', pg_name, '--label', LABEL + '=' + job,
              '--network', 'none', '--memory', str(self.config['pg_memory']),
              '--memory-swap', str(self.config['pg_memory']), '--cpuset-cpus', self.config['pg_cpuset'],
              '--pids-limit', '256', '--security-opt', 'no-new-privileges',
              '--env', 'POSTGRES_USER=' + ('kiwoom_replay_fixture_admin' if args.command == 'test' and args.profile == 'replay-cache' else 'kiwoom_operator_admin'),
              '--env', 'POSTGRES_DB=postgres',
              '--env', 'PGDATA=/var/lib/postgresql/data/pgdata',
              '--env', 'POSTGRES_PASSWORD_FILE=/run/operator-password',
              '--mount', 'type=bind,src=' + self.private.path + '/' + base + '/postgres-password,dst=/run/operator-password,readonly']
        if storage == 'ram':
            pg += ['--tmpfs', '/var/lib/postgresql/data:rw,nosuid,nodev,size=512m']
        else:
            data = Path(self.private.path) / base / 'postgres'
            pg += ['--mount', 'type=bind,src=' + str(data) + ',dst=/var/lib/postgresql/data']
        pg += [self.config['pg_image_id'], 'postgres', '-c', 'shared_buffers=16MB', '-c', 'max_connections=20',
               '-c', 'kiwoom.replay_fixture_id=' + job]
        report = {'job_id': job, 'command': args.command, 'release_id': args.release,
                  'source_hash': content, 'storage': storage, 'source_state_equivalent': source_state_equivalent,
                  'runtime_image_id': self.config['runtime_image_id'], 'pg_image_id': self.config['pg_image_id'],
                  'worker_cpuset': self.config['worker_cpuset'], 'pg_cpuset': self.config['pg_cpuset'],
                  'pids_support': 'not_yet_verified', 'state': 'failed'}
        self.private.put_json('job.json', {'state': 'running', 'job_id': job, 'command': args.command})
        try:
            worker.mkdir(parents=True, mode=0o700)
            self.private.put_json(base + '/worker/request.json', request)
            os.chown(str(worker / 'request.json'), 65534, 65534)
            os.chown(str(worker), 65534, 65534)
            self.private.write(base + '/postgres-password', (password + '\n').encode(), mode=0o600)
            if storage == 'disk':
                data.mkdir(mode=0o700)
            if args.command == 'replay':
                self.registered_input('traces', args.trace)
                baseline = self.registered_input('baselines', args.baseline)
                report['source_state_equivalent'] = baseline['source_state_equivalent']
                extra_mounts += ['--mount', 'type=bind,src=' + self.private.path + '/traces/' + args.trace + ',dst=/run/trace,readonly',
                                 '--mount', 'type=bind,src=' + self.private.path + '/baselines/' + args.baseline + ',dst=/run/baseline,readonly',
                                 '--mount', 'type=bind,src=' + self.private.path + '/traces/' + args.trace + ',dst=/tmp/diagnostic-traces/' + args.trace + ',readonly']
            self.docker(pg)
            self.docker(['start', pg_name])
            deadline = time.monotonic() + 60
            while True:
                try:
                    self.docker(['exec', pg_name, 'pg_isready', '-d', 'postgres'], timeout=5)
                    break
                except Rejected:
                    require(time.monotonic() < deadline, 'temporary_postgres_not_ready')
                    time.sleep(.5)
            actual_limit = self.docker(['exec', pg_name, '/bin/sh', '-c',
                'if [ -f /sys/fs/cgroup/memory.max ]; then cat /sys/fs/cgroup/memory.max; else cat /sys/fs/cgroup/memory/memory.limit_in_bytes; fi']).decode().strip()
            require(actual_limit.isdigit() and 0 < int(actual_limit) <= self.config['pg_memory'], 'pg_memory_limit_not_enforced')
            actual_cpu = self.docker(['exec', pg_name, '/bin/sh', '-c',
                'while IFS=: read -r key value; do if [ "$key" = Cpus_allowed_list ]; then printf "%s\\n" "$value"; fi; done < /proc/self/status']).decode().strip()
            verify_cpu_affinity(actual_cpu, self.config['pg_cpuset'])
            report['pg_cpu_affinity'] = actual_cpu
            self.docker(self.worker_argv(job, pg_name, args.release, extra_mounts))
            for name, cpus in ((pg_name, self.config['pg_cpuset']),
                               ('kiwoom-op-worker-' + job, self.config['worker_cpuset'])):
                value = self.inspect(name)
                require(value['HostConfig']['Memory'] > 0 and not value['HostConfig']['Privileged'] and
                        value['Config']['Labels'][LABEL] == job, 'job_isolation_invalid')
                verify_cpu_affinity(value['HostConfig'].get('CpusetCpus'), cpus)
            with open(str(Path(self.private.path) / base / 'process.log'), 'xb') as log:
                self.docker(['start', '-a', 'kiwoom-op-worker-' + job], timeout=self.config['job_timeout'], log=log)
            value = self.inspect('kiwoom-op-worker-' + job)
            require(value['State']['ExitCode'] == 0 and not value['State'].get('OOMKilled'), 'worker_failed')
            with Tree(str(worker), owners=(65534,)) as output:
                outcome = output.json('result.json')
            require(outcome.get('state') == 'passed', 'worker_gate_failed')
            require(type(outcome.get('memory_limit_bytes')) is int and
                    0 < outcome['memory_limit_bytes'] <= self.config['worker_memory'], 'worker_memory_limit_not_enforced')
            verify_cpu_affinity(outcome.get('cpu_affinity'), self.config['worker_cpuset'])
            self.identities()
            with Tree(self.config['store'], owners=(0, self.config['allowed_uid'])) as store:
                require(store.read('active.json') == active_before, 'active_release_changed_during_job')
            pids = decode(self.docker(['info', '--format', '{{json .PidsLimit}}']))
            report.update(state='passed', outcome=outcome, pids_support=pids is True,
                          active_release_and_containers_unchanged=True)
        except BaseException as error:
            report['error_type'] = type(error).__name__
            if isinstance(error, Rejected):
                report['reason'] = str(error)
            raise
        finally:
            try:
                self.cleanup_job(job)
                report['cleanup_complete'] = True
                self.private.put_json('job.json', {'state': 'complete', 'job_id': job})
            except BaseException:
                report.update(state='failed', cleanup_complete=False)
                raise
            finally:
                self.private.put_json('reports/' + job + '.json', report)
        if args.command == 'test':
            self.private.put_json('gates/' + args.release + '.json',
                                  {'source_hash': content, 'profile': args.profile, 'passed': True})
        return report

    def registered_input(self, kind, input_id):
        identifier(input_id)
        catalog = self.private.json(kind + '/' + input_id + '/registration.json')
        for name, digest in catalog['files'].items():
            data = self.private.read(kind + '/' + input_id + '/' + name, self.config['input_file_limit'])
            require(hashlib.sha256(data).hexdigest() == digest, 'registered_input_changed')
        return catalog

    def register(self, kind, input_id):
        identifier(input_id)
        incoming_path = self.config['trace_dir'] + '/' + input_id if kind == 'traces' else \
            self.config['project'] + '/operator-inputs/baselines/' + input_id
        with Tree(incoming_path, owners=(0, self.config['allowed_uid'])) as incoming:
            if kind == 'traces':
                manifest = incoming.json('manifest.json')
                require(manifest.get('trace_id') == input_id and manifest.get('state') == 'complete' and
                        manifest.get('accepted') == manifest.get('written') and manifest.get('known_dropped') == 0 and
                        manifest.get('input_rejected') == 0 and manifest.get('input_capture_censored') is False,
                        'trace_not_complete')
                names = {'manifest.json'}
                names.update(x['name'] for x in manifest.get('chunks', []))
                chunk_hashes = {item['name']: item['sha256'] for item in manifest.get('chunks', [])}
                for key, blob in manifest.get('blobs', {}).items():
                    names.add(blob.get('name') or 'payload-' + key + '.json')
                catalog = {'source_state_equivalent': False}
            else:
                manifest = incoming.json('baseline.json')
                require(manifest.get('baseline_id') == input_id and HEX.fullmatch(input_id) and
                        manifest.get('database') == 'kiwoom_monitor_replay_test' and
                        manifest.get('version') in (1, 2) and type(manifest.get('source_state_equivalent')) is bool and
                        re.fullmatch('[a-f0-9]{32}', manifest.get('owner_token', '')), 'invalid_baseline_bundle')
                require(type(manifest.get('statements_sha256')) is str and HEX.fullmatch(manifest['statements_sha256']),
                        'invalid_baseline_hash')
                names = {'baseline.json', 'statements.json'}
                catalog = {'source_state_equivalent': manifest['source_state_equivalent']}
            prefix = kind + '/' + input_id
            try:
                existing = self.registered_input(kind, input_id)
            except FileNotFoundError:
                existing = None
            files = {}
            total = 0
            for name in sorted(names):
                require(len(relative(name)) == 1 and WORD.fullmatch(name), 'invalid_bundle_file')
                data = incoming.read(name, self.config['input_file_limit'])
                total += len(data)
                require(total <= self.config['input_total_limit'], 'bundle_size_limit')
                files[name] = hashlib.sha256(data).hexdigest()
                if kind == 'traces' and name in chunk_hashes:
                    require(files[name] == chunk_hashes[name], 'trace_chunk_checksum_mismatch')
                if existing is None:
                    self.private.write(prefix + '/' + name, data, mode=0o644)
            if kind == 'baselines':
                require(files['statements.json'] == manifest['statements_sha256'], 'baseline_hash_mismatch')
            if existing is not None:
                require(existing['files'] == files, 'registered_input_conflict')
                return existing
            incoming.verify_identity()
            catalog.update(files=files)
            self.private.put_json(prefix + '/registration.json', catalog)
            return catalog

    def publish(self, store, manifest):
        release_id = manifest['release_id']
        # Already staged release must exactly match the admitted candidate.
        with Tree(self.config['store'] + '/releases/' + release_id,
                  owners=(0, self.config['allowed_uid'])) as current:
            require(current.json('manifest.json') == manifest, 'published_manifest_changed')
            for name, expected in manifest['files'].items():
                require(hashlib.sha256(current.read(name, 16 * 1024 * 1024)).hexdigest() == expected,
                        'published_source_changed')
            current.verify_identity()
        store.verify_identity()

    def wait_ready(self, release_id, manifest):
        deadline = time.monotonic() + self.config['ready_timeout']
        while time.monotonic() < deadline:
            try:
                self.identities()
                snapshot = self.snapshot()
                require(snapshot.get('health') == 'ok' and snapshot.get('server_build') == manifest['server_build'] and
                        snapshot.get('source_path') == '/app/source-runtime/releases/' + release_id + '/src',
                        'candidate_not_ready')
                return snapshot
            except Rejected:
                time.sleep(1)
        raise Rejected('candidate_readiness_timeout')

    def deploy(self, store, target, rollback=False):
        target = identifier(target)
        old = identifier(store.json('active.json')['release_id'])
        old_manifest, unused = self.source(old)
        manifest, content = self.source(target)
        if target != old:
            if rollback:
                prior = self.journal()
                require(prior.get('state') in ('complete', 'rolled_back') and
                        prior.get('previous') == target and prior.get('previous_hash') == content,
                        'verified_previous_release_required')
            else:
                gate = self.private.json('gates/' + target + '.json')
                require(gate.get('passed') is True and gate.get('source_hash') == content and
                        gate.get('profile') in self.config['deploy_profiles'], 'exact_source_gate_required')
        require(old_manifest['contract'] == manifest['contract'], 'schema_change_requires_admin')
        with store.parent('deploy.lock') as (parent, leaf):
            try:
                os.mkdir(leaf, 0o700, dir_fd=parent)
            except FileExistsError:
                raise Rejected('manual_deployment_busy')
            try:
                self.publish(store, manifest)
                if target == old:
                    return {'state': 'unchanged', 'release_id': old}
                journal = {'state': 'prepared', 'previous': old, 'target': target,
                           'previous_hash': unused, 'target_hash': content,
                           'store_identity': list(store.identity)}
                self.private.put_json('deployment.json', journal)
                try:
                    self.docker(['stop', '--time', '60', self.config['server_id']], timeout=90)
                    require(self.inspect(self.config['server_id'])['State']['Running'] is False, 'server_stop_not_confirmed')
                    journal['state'] = 'stopped'
                    self.private.put_json('deployment.json', journal)
                    store.verify_identity()
                    store.put_json('active.json', {'format': 1, 'release_id': target})
                    journal['state'] = 'selected'
                    self.private.put_json('deployment.json', journal)
                    self.publish(store, manifest)
                    self.docker(['start', self.config['server_id']])
                    self.wait_ready(target, manifest)
                    self.publish(store, manifest)
                    journal['state'] = 'complete'
                    self.private.put_json('deployment.json', journal)
                    return {'state': 'complete', 'release_id': target, 'database_container_unchanged': True,
                            'realtime_loss_verified': False}
                except BaseException:
                    try:
                        self.docker(['stop', '--time', '60', self.config['server_id']], timeout=90)
                        require(self.inspect(self.config['server_id'])['State']['Running'] is False, 'recovery_stop_unconfirmed')
                        store.verify_identity()
                        self.publish(store, old_manifest)
                        store.put_json('active.json', {'format': 1, 'release_id': old})
                        self.docker(['start', self.config['server_id']])
                        self.wait_ready(old, old_manifest)
                        journal['state'] = 'rolled_back'
                        self.private.put_json('deployment.json', journal)
                    except BaseException:
                        journal['state'] = 'needs_admin'
                        self.private.put_json('deployment.json', journal)
                    raise
            finally:
                os.rmdir(leaf, dir_fd=parent)

    def recover(self, store):
        job = self.job_journal()
        if job.get('state') not in ('idle', 'complete'):
            self.cleanup_job(job['job_id'])
            self.private.put_json('reports/' + job['job_id'] + '.json',
                                  {'state': 'failed', 'job_id': job['job_id'],
                                   'reason': 'interrupted_job', 'cleanup_complete': True})
            self.private.put_json('job.json', {'state': 'complete', 'job_id': job['job_id']})
        journal = self.journal()
        if journal.get('state') in ('idle', 'complete', 'rolled_back'):
            return {'state': 'recovered', 'deployment_changed': False}
        require(journal.get('state') in ('prepared', 'stopped', 'selected', 'needs_admin') and
                journal.get('store_identity') == list(store.identity), 'unknown_deployment_recovery')
        old = identifier(journal['previous'])
        target = identifier(journal['target'])
        require(store.json('active.json')['release_id'] in (old, target), 'recovery_pointer_changed')
        manifest, content = self.source(old)
        require(content == journal.get('previous_hash'), 'previous_snapshot_changed')
        self.publish(store, manifest)
        with store.parent('deploy.lock') as (parent, leaf):
            try:
                lock = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                require(stat.S_ISDIR(lock.st_mode) and lock.st_uid == 0 and not lock.st_mode & 0o022,
                        'recovery_lock_unknown')
            except FileNotFoundError:
                os.mkdir(leaf, 0o700, dir_fd=parent)
            try:
                self.docker(['stop', '--time', '60', self.config['server_id']], timeout=90)
                require(self.inspect(self.config['server_id'])['State']['Running'] is False, 'recovery_stop_unconfirmed')
                store.verify_identity()
                store.put_json('active.json', {'format': 1, 'release_id': old})
                self.docker(['start', self.config['server_id']])
                self.wait_ready(old, manifest)
                self.publish(store, manifest)
                journal['state'] = 'rolled_back'
                self.private.put_json('deployment.json', journal)
                return {'state': 'recovered', 'release_id': old, 'database_container_unchanged': True}
            except BaseException:
                journal['state'] = 'needs_admin'
                self.private.put_json('deployment.json', journal)
                raise
            finally:
                os.rmdir(leaf, dir_fd=parent)


def parser():
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('status', allow_abbrev=False)
    commands.add_parser('rollback', allow_abbrev=False)
    commands.add_parser('recover', allow_abbrev=False)
    commands.add_parser('revoke', allow_abbrev=False)
    report = commands.add_parser('report', allow_abbrev=False)
    report.add_argument('job_id', type=identifier)
    deploy = commands.add_parser('deploy', allow_abbrev=False)
    deploy.add_argument('release', type=identifier)
    test = commands.add_parser('test', allow_abbrev=False)
    test.add_argument('release', type=identifier)
    test.add_argument('--profile', default='replay-cache')
    test.add_argument('--test', action='append', default=[])
    for name in ('register-trace', 'register-baseline'):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument('input_id', type=identifier)
    replay = commands.add_parser('replay', allow_abbrev=False)
    replay.add_argument('release', type=identifier)
    replay.add_argument('trace', type=identifier)
    replay.add_argument('--baseline', required=True)
    replay.add_argument('--window-start', type=float, required=True)
    replay.add_argument('--window-end', type=float, required=True)
    replay.add_argument('--mode', choices=('recorded_operations', 'collector_with_background'), default='recorded_operations')
    replay.add_argument('--include-workload', action='append', default=[])
    replay.add_argument('--exclude-workload', action='append', default=[])
    replay.add_argument('--collector-component', action='append', default=[])
    replay.add_argument('--concurrency', type=int, default=8)
    replay.add_argument('--storage', choices=('disk', 'ram'), default='disk')
    return result


def validate_args(args, config):
    if args.command == 'report':
        require(re.fullmatch('[a-f0-9]{32}', args.job_id), 'invalid_job_id')
    if args.command == 'test':
        require(args.profile in config['profiles'], 'unknown_test_profile')
        require(args.profile == 'selected' or not args.test, 'profile_does_not_accept_test_names')
        require(args.profile != 'selected' or 0 < len(args.test) <= 100, 'selected_tests_required')
        require(all(TEST.fullmatch(x) for x in args.test), 'invalid_test_name')
    if args.command == 'replay':
        require(HEX.fullmatch(args.baseline) is not None, 'invalid_baseline_id')
        require(math.isfinite(args.window_start) and math.isfinite(args.window_end) and
                0 <= args.window_start < args.window_end <= 7200, 'invalid_replay_window')
        require(1 <= args.concurrency <= config['max_concurrency'], 'invalid_concurrency')
        for values in (args.include_workload, args.exclude_workload, args.collector_component):
            require(len(values) <= 100 and all(WORD.fullmatch(x) for x in values), 'invalid_workload_selection')
        require(not set(args.include_workload) & set(args.exclude_workload), 'conflicting_workload_selection')


def load_config(revoking=False):
    require(os.name == 'posix' and os.geteuid() == 0, 'installed_root_launcher_required')
    with Tree('/etc/kiwoom-nas', owners=(0,), protected=True) as tree:
        config = tree.json('operator.json')
    require(config.get('format') == 1, 'invalid_operator_config')
    if not revoking:
        validate_cpu_limits(config, set(os.sched_getaffinity(0)))
    sudo_uid = os.environ.get('SUDO_UID')
    require(sudo_uid is None or sudo_uid == str(config['allowed_uid']), 'operator_uid_not_allowed')
    paths = (config['visudo'], HELPER + '/nas_operator.py') if revoking else (
        config['docker'], config['python'], config['helper_dir'] + '/nas_operator.py',
        config['helper_dir'] + '/nas_operator_worker.py')
    for path in paths:
        with Tree(str(Path(path).parent), owners=(0,), protected=True) as tree:
            with tree.parent(Path(path).name) as (parent, leaf):
                fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    tree._check(os.fstat(fd))
                finally:
                    os.close(fd)
    return config


def main(argv=None):
    os.umask(0o077)
    def interrupted(unused_number, unused_frame):
        raise KeyboardInterrupt()
    if os.name == 'posix':
        for number in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(number, interrupted)
    args = None
    try:
        args = parser().parse_args(argv)
        config = load_config(revoking=args.command == 'revoke')
        validate_args(args, config)
        with Tree(config['private'], owners=(0,), protected=True) as private:
            operator = Operator(config, private)
            if args.command == 'status':
                result = operator.status()
            elif args.command == 'report':
                result = private.json('reports/' + args.job_id + '.json')
            elif args.command == 'revoke':
                result = operator.revoke_access()
            else:
                with operator.fence(recovery=args.command == 'recover') as store:
                    if args.command == 'deploy':
                        result = operator.deploy(store, args.release)
                    elif args.command == 'rollback':
                        previous = operator.journal().get('previous')
                        require(previous is not None, 'previous_release_unavailable')
                        result = operator.deploy(store, previous, rollback=True)
                    elif args.command == 'recover':
                        result = operator.recover(store)
                    elif args.command in ('register-trace', 'register-baseline'):
                        kind = 'traces' if args.command == 'register-trace' else 'baselines'
                        result = operator.register(kind, args.input_id)
                    else:
                        manifest, content = operator.source(args.release)
                        result = operator.run_job(args, manifest, content)
            print(json.dumps({'state': 'ok', 'command': args.command, 'result': result}, sort_keys=True))
            return 0
    except Exception as error:
        # Arbitrary library/Docker errors may contain secrets. Only our codes are public.
        print(json.dumps({'state': 'failed', 'command': args.command if args else None, 'error_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, Rejected) else 'operator_internal_error'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
