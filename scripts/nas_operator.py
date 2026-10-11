"""Installed, narrowly privileged NAS operator. Python 3.8 stdlib only.

Never import a candidate, run a candidate host script, or accept Docker options.
All mutable source is admitted as data before being mounted in a container.
"""
import argparse
import contextlib
from datetime import datetime
import hashlib
import itertools
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
CAPACITY_PROFILES = ('recorder-capacity-smoke', 'recorder-capacity')
CAPACITY_WORKER_MEMORY = 12 * 1024 ** 3
CAPACITY_DISK_FREE = 12 * 1024 ** 3
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


def sudoers_rule(user):
    # Only this literal rule is accepted; no general sudoers parser is invented.
    require(type(user) is str and re.fullmatch('[a-z_][a-z0-9_-]{0,31}', user),
            'invalid_operator_user')
    return (user + ' ALL=(root) NOPASSWD: ' + ROOT_LAUNCHER + '\n').encode()


def validate_sudo_policy(config, rule_present, run=None, suffix=''):
    """Use visudo, or check the native policy for our one fixed generated rule.

    The latter is not a general replacement for visudo. A listing's exit code
    alone is insufficient: reject parser warnings, ambiguous output and a rule
    that is not loaded as exactly our NOPASSWD root command. Installation also
    proves a real cache-independent passwordless status call as the target UID.
    """
    method = config.get('sudoers_validation', 'visudo')
    require(method in ('visudo', 'native_fixed_rule'), 'invalid_sudoers_validator')
    if method == 'visudo':
        command = [config['visudo'], '-c']
    else:
        user = config['allowed_user']
        sudoers_rule(user)
        command = [config['sudo'], '-n', '-l', '-U', user]
    try:
        checked = (run or subprocess.run)(command, env=CLEAN_ENV, cwd='/',
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=15)
    except subprocess.TimeoutExpired:
        raise Rejected('sudoers_validation_timeout' + suffix)
    require(checked.returncode == 0 and not checked.stderr and
            len(checked.stdout) <= 256 * 1024, 'sudoers_validation_failed' + suffix)
    if method == 'native_fixed_rule':
        output = checked.stdout.decode('utf-8', errors='strict')
        require(bool(output.strip()), 'sudoers_validation_failed' + suffix)
        exact = re.findall(r'^[ \t]*\(root\)[ \t]+NOPASSWD:[ \t]+' +
                           re.escape(ROOT_LAUNCHER) + r'[ \t]*$', output, re.MULTILINE)
        require((len(exact) == 1 and output.count(ROOT_LAUNCHER) == 1) if rule_present
                else ROOT_LAUNCHER not in output, 'sudoers_rule_not_verified' + suffix)
    return method


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


def trace_blob_files(manifest, incoming, limit):
    """Validate physical blocks as bounded data; never import candidate code."""
    files = {}
    for digest, root in manifest.get('blobs', {}).items():
        require(type(digest) is str and HEX.fullmatch(digest) and type(root) is dict,
                'invalid_trace_blob')
        if root.get('format') != 'blocks/v1':
            require('format' not in root and 'parts' not in root, 'invalid_trace_blob')
            files.setdefault(root.get('name') or 'payload-' + digest + '.json', None)
            continue
        require(manifest.get('schema_version') == 4 and type(root.get('bytes')) is int and
                0 < root['bytes'] <= 128 * 1024 ** 2 and type(root.get('parts')) is list and
                1 <= len(root['parts']) <= 128, 'invalid_trace_blocks')
        whole, total, previous_name, offset, content = hashlib.sha256(), 0, None, 0, None
        bundle = -1
        for index, part in enumerate(root['parts']):
            require(type(part) is dict and type(part.get('index')) is int and part['index'] == index and
                    type(part.get('bytes')) is int and 0 < part['bytes'] <= 1024 ** 2 and
                    type(part.get('sha256')) is str and HEX.fullmatch(part['sha256']) and
                    type(part.get('name')) is str and
                    re.fullmatch('block-' + digest + r'-[0-9]{3}\.payloads', part['name']) and
                    type(part.get('offset')) is int and part['offset'] >= 0 and
                    part['offset'] + part['bytes'] <= 16 * 1024 ** 2, 'invalid_trace_block_part')
            if part['name'] != previous_name:
                require(content is None or offset == len(content), 'trace_block_layout_mismatch')
                bundle += 1
                require(part['name'] == 'block-' + digest + '-%03d.payloads' % bundle and
                        part['offset'] == 0, 'trace_block_layout_mismatch')
                content = incoming.read(part['name'], min(limit, 16 * 1024 ** 2))
                require(0 < len(content) <= 16 * 1024 ** 2, 'invalid_trace_block_file')
                files[part['name']] = hashlib.sha256(content).hexdigest()
                previous_name, offset = part['name'], 0
            require(part['offset'] == offset, 'trace_block_layout_mismatch')
            block = content[offset:offset + part['bytes']]
            require(len(block) == part['bytes'] and hashlib.sha256(block).hexdigest() == part['sha256'],
                    'trace_block_checksum_mismatch')
            whole.update(block)
            offset += len(block)
            total += len(block)
        require(offset == len(content) and total == root['bytes'] and whole.hexdigest() == digest,
                'trace_block_checksum_mismatch')
    return files


def validate_baseline_clock(manifest):
    if manifest['version'] >= 2:
        try:
            origin = datetime.fromisoformat(manifest.get('source_origin', ''))
            valid = origin.utcoffset() is not None and math.isfinite(origin.timestamp())
        except (ValueError, TypeError, OverflowError):
            valid = False
        require(valid, 'invalid_baseline_source_origin')
    if manifest['version'] == 3:
        context = manifest.get('account_context')
        require(manifest['source_state_equivalent'] is False and type(context) is dict and
                context.get('version') in ('account-context/v1', 'account-context/v2') and
                type(context.get('trace_id')) is str and ID.fullmatch(context['trace_id']) and
                all(type(context.get(key)) is str and HEX.fullmatch(context[key])
                    for key in ('sha256', 'owner_bindings_sha256')) and
                context.get('source_state_equivalent') is False, 'invalid_baseline_account_context')


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
                # Preserve hashed source bytes while accepting Windows CRLF.
                marker = re.search(rb'^SERVER_BUILD = "([A-Za-z0-9._-]+)"\r?$', data, re.M)
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


def _fingerprint_with_mounts(value, mounts):
    profile = {key: value[key] for key in ('Id', 'Image', 'Config', 'HostConfig', 'Mounts')}
    profile['Mounts'] = list(mounts)
    profile['networks'] = {name: item['NetworkID'] for name, item in
                           value['NetworkSettings']['Networks'].items()}
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()


def fingerprint(value):
    # Docker inspect can enumerate final mounts in different orders. Preserve
    # every mount field while giving new approvals a stable representation.
    mounts = sorted(value['Mounts'], key=lambda item: json.dumps(item, sort_keys=True))
    return _fingerprint_with_mounts(value, mounts)


def fingerprint_matches(value, approved):
    if fingerprint(value) == approved:
        return True
    mounts = value['Mounts']
    if _fingerprint_with_mounts(value, mounts) == approved:
        return True
    # Format-1 installations pinned the unsorted inspect hash. Match that exact
    # hash by changing only Mounts order; no configuration field is discarded and
    # the protected config/revoke snapshot need not be rewritten. Bound the
    # compatibility search rather than allowing factorial work for large lists.
    if len(mounts) > 7:
        return False
    return any(_fingerprint_with_mounts(value, order) == approved
               for order in itertools.permutations(mounts))


def idle(snapshot):
    require(type(snapshot) is dict, 'diagnostic_state_unknown')
    for key in ('diagnostic_tool', 'trace_capture'):
        require(type(snapshot.get(key)) is dict and snapshot[key].get('enabled') is False, 'diagnostics_enabled')
    require(snapshot.get('paused_workloads') == [] and snapshot.get('active_runs') == [], 'workload_or_run_active')
    trace = snapshot.get('trace')
    require(type(trace) is dict and trace.get('state') in ('off', 'complete', 'incomplete'),
            'trace_not_durably_idle')
    if trace['state'] != 'off':
        for key in ('queued', 'pending_events', 'copy_reserved_bytes', 'charged_bytes',
                    'packing_events', 'packed_events'):
            require(type(trace.get(key)) is int and trace[key] == 0, 'trace_retains_memory_or_state_unknown')
        # Both complete and incomplete are published after final manifest sync.
        # Replay eligibility is checked separately on registration; rejected
        # inputs must not permanently disable maintenance after every accepted
        # event is written and all retained memory is drained.
        require(type(trace.get('accepted')) is int and type(trace.get('written')) is int and
                trace['accepted'] >= 0 and trace['accepted'] == trace['written'],
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


def validate_scoped_trace_manifest(manifest, trace_id):
    """Host-only durable admission; the isolated reader verifies the full source."""
    require(type(manifest) is dict and manifest.get('trace_id') == trace_id and
            type(manifest.get('schema_version')) is int and manifest['schema_version'] in (2, 3, 4) and
            manifest.get('state') in ('complete', 'incomplete') and
            manifest.get('coverage') == 'observed_paths_only' and
            manifest.get('input_capture_censored') is False and
            manifest.get('unknown_tail_loss', False) is False and
            type(manifest.get('payload_capture')) is dict and
            manifest['payload_capture'].get('store_inputs') is True, 'scoped_trace_not_durable')
    counters = ('accepted', 'written', 'last_seq', 'known_dropped', 'input_rejected',
                'payload_accepted', 'queued', 'pending_events', 'copy_reserved_bytes', 'bytes_written')
    require(all(type(manifest.get(key)) is int and manifest[key] >= 0 for key in counters) and
            manifest['accepted'] == manifest['written'] == manifest['last_seq'] and
            manifest['written'] <= 5000000 and
            not any(manifest[key] for key in ('known_dropped', 'queued', 'pending_events', 'copy_reserved_bytes')),
            'scoped_trace_not_drained')
    require(all(type(manifest[key]) is int and manifest[key] == 0 for key in
                ('charged_bytes', 'packed_events', 'packing_events', 'raw_charged_bytes',
                 'scalar_charged_bytes', 'pending_bytes', 'packed_bytes', 'packing_bytes') if key in manifest),
            'scoped_trace_not_drained')
    require(manifest['state'] != 'incomplete' or manifest['input_rejected'] > 0 and
            manifest.get('reason') in ('expired', 'manual', 'master_off_or_expired'), 'scoped_trace_terminal_reason_invalid')
    require(type(manifest.get('started_mono_ns')) is int and type(manifest.get('finished_mono_ns')) is int and
            0 <= manifest['started_mono_ns'] < manifest['finished_mono_ns'], 'scoped_trace_clock_invalid')
    for key in ('drop_reasons', 'input_rejected_reasons'):
        bucket = manifest.get(key)
        require(type(bucket) is dict and all(type(name) is str and name and type(count) is int and count >= 0
                for name, count in bucket.items()), 'scoped_trace_rejection_accounting_invalid')
    require(not sum(manifest['drop_reasons'].values()) and
            sum(manifest['input_rejected_reasons'].values()) == manifest['input_rejected'],
            'scoped_trace_rejection_accounting_invalid')
    require(type(manifest.get('chunks')) is list and type(manifest.get('blobs')) is dict,
            'scoped_trace_chunks_invalid')
    sequence, size = 1, 0
    for number, part in enumerate(manifest['chunks'], 1):
        require(type(part) is dict and part.get('name') == '%06d.jsonl' % number and
                all(type(part.get(key)) is int for key in ('count', 'first_seq', 'last_seq', 'bytes')) and
                part['count'] > 0 and part['bytes'] > 0 and part['first_seq'] == sequence and
                part['last_seq'] == sequence + part['count'] - 1 and
                type(part.get('sha256')) is str and HEX.fullmatch(part['sha256']), 'scoped_trace_chunks_invalid')
        sequence += part['count']
        size += part['bytes']
    require(sequence - 1 == manifest['written'] and size <= manifest['bytes_written'], 'scoped_trace_chunks_invalid')


def docker_failure_class(stderr):
    """Map Docker stderr to a small safe enum; never publish raw diagnostics."""
    if isinstance(stderr, bytes):
        message = stderr[:4096].decode('utf-8', 'replace').lower()
    elif isinstance(stderr, str):
        message = stderr[:4096].lower()
    else:
        message = ''
    if any(value in message for value in ('no such image', 'manifest unknown', 'pull access denied')):
        return 'image_unavailable'
    if any(value in message for value in ('invalid mount config', 'bind source path does not exist',
                                         'mounts denied', 'no such file or directory')):
        return 'mount_invalid'
    if any(value in message for value in ('requested cpuset', 'cpuset', 'cpu cfs scheduler')):
        return 'cpu_affinity_unavailable'
    if any(value in message for value in ('pids limit', 'pidslimit', 'pids-limit')):
        return 'pids_limit_unavailable'
    if 'permission denied' in message or 'operation not permitted' in message:
        return 'permission_denied'
    if any(value in message for value in ('already in use', 'is already in use', 'container name')):
        return 'name_conflict'
    if any(value in message for value in ('memory limit', 'memory-swap', 'invalid memory')):
        return 'memory_limit_unavailable'
    return 'other'


class Operator:
    def __init__(self, config, private, run=None):
        self.config = config
        self.private = private
        self.run_process = run or subprocess.run
        self.last_docker_action = None
        self.last_docker_failure_class = None

    def docker(self, args, timeout=30, log=None, capture_stderr=False):
        command = [self.config['docker'], '--host', 'unix:///var/run/docker.sock'] + list(args)
        require(all(type(x) is str for x in command), 'non_string_argument')
        self.last_docker_action = args[0] if args else 'unknown'
        self.last_docker_failure_class = None
        try:
            if log is None:
                result = self.run_process(command, env=CLEAN_ENV, cwd='/', stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT if capture_stderr else subprocess.PIPE,
                                          timeout=timeout)
                require(len(result.stdout) <= 8 * 1024 * 1024, 'docker_response_too_large')
            else:
                result = self.run_process(command, env=CLEAN_ENV, cwd='/', stdin=subprocess.DEVNULL,
                                          stdout=log, stderr=log, timeout=timeout)
        except subprocess.TimeoutExpired:
            self.last_docker_failure_class = 'timeout'
            raise Rejected('docker_command_timeout')
        if result.returncode != 0:
            self.last_docker_failure_class = docker_failure_class(getattr(result, 'stderr', b''))
            raise Rejected('docker_command_failed')
        return result.stdout if log is None else b''

    def inspect(self, container):
        result = decode(self.docker(['inspect', container]))
        require(type(result) is list and len(result) == 1, 'container_inspection_invalid')
        return result[0]

    def postgres_failure_diagnostics(self, report, job):
        """Inspect only this job's PG and report fixed startup markers, never logs."""
        name = 'kiwoom-op-pg-' + job
        summary = {}
        report['postgres_startup'] = summary
        try:
            value = self.inspect(name)
            require(value.get('Config', {}).get('Labels', {}).get(LABEL) == job,
                    'postgres_diagnostic_identity_mismatch')
            state = value.get('State', {})
            for key in ('Running', 'OOMKilled', 'ExitCode'):
                item = state.get(key)
                if type(item) is (int if key == 'ExitCode' else bool):
                    summary[key] = item
            if state.get('Status') in ('created', 'running', 'paused', 'restarting',
                                       'removing', 'exited', 'dead'):
                summary['status'] = state['Status']
            user = value.get('Config', {}).get('User', '')
            summary['container_user'] = (user if type(user) is str and
                (user in ('root', 'postgres') or re.fullmatch(r'[0-9]+(?::[0-9]+)?', user))
                else 'image_default' if user == '' else 'other')
            userns = value.get('HostConfig', {}).get('UsernsMode', '')
            summary['container_userns'] = userns if userns in ('host', 'private') else 'default'
        except Exception as error:
            summary['inspection_error_type'] = type(error).__name__
            return  # A failed identity check must never authorize reading logs.
        # Fixed roles in the generated private job only; never expose paths or
        # file content, and do not change ownership/permissions to make probes work.
        summary['host_file_metadata'] = metadata = {}
        for role, relative in (
                ('data_root', 'postgres'), ('pgdata', 'postgres/pgdata'),
                ('password_file', 'postgres-password')):
            try:
                with self.private.parent('job-' + job + '/' + relative) as (parent, leaf):
                    info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                metadata[role] = {'exists': True, 'uid': info.st_uid, 'gid': info.st_gid,
                    'mode': stat.S_IMODE(info.st_mode),
                    'kind': 'directory' if stat.S_ISDIR(info.st_mode) else
                            'file' if stat.S_ISREG(info.st_mode) else 'other'}
            except FileNotFoundError:
                metadata[role] = {'exists': False}
            except Exception as error:
                metadata[role] = {'error_type': type(error).__name__}
        try:
            options = decode(self.docker(['info', '--format', '{{json .SecurityOptions}}'], timeout=5))
            require(type(options) is list and all(type(item) is str for item in options),
                    'docker_security_options_invalid')
            summary['daemon_userns_enabled'] = any('name=userns' in item for item in options)
        except Exception as error:
            summary['security_options_error_type'] = type(error).__name__
        try:
            raw = self.docker(['logs', '--tail', '100', name], timeout=5, capture_stderr=True)
            tail = raw[-65536:]
            summary.update(log_bytes=len(raw), log_tail_truncated=len(raw) > len(tail),
                           log_tail_sha256=hashlib.sha256(tail).hexdigest())
            lowered = tail.lower()
            markers = {
                'permission_denied': b'permission denied',
                'no_space': b'no space left on device',
                'read_only_filesystem': b'read-only file system',
                'initdb_failed': b'initdb: error:',
                'directory_not_empty': b'exists but is not empty',
                'incompatible_data': b'database files are incompatible with server',
                'configuration_error': b'configuration file contains errors',
                'ready_for_connections': b'database system is ready to accept connections',
                'shutdown_complete': b'database system is shut down',
                'fatal': b'fatal:',
                'panic': b'panic:',
            }
            summary['log_markers'] = [key for key, marker in markers.items() if marker in lowered]
            summary['permission_locations'] = []
            for line in lowered.splitlines():
                if b'permission denied' not in line:
                    continue
                operation = ('mkdir' if line.startswith(b'mkdir:') else
                             'chmod' if line.startswith(b'chmod:') else
                             'chown' if line.startswith(b'chown:') else 'other')
                role = ('pgdata' if b'/var/lib/postgresql/data/pgdata' in line else
                        'data_root' if b'/var/lib/postgresql/data' in line else
                        'password_file' if b'/run/operator-password' in line else 'other')
                item = {'operation': operation, 'role': role}
                if item not in summary['permission_locations']:
                    summary['permission_locations'].append(item)
        except Exception as error:
            summary['log_error_type'] = type(error).__name__

    def worker_failure_diagnostics(self, report, job, worker):
        """Capture bounded, non-sensitive worker outcome details before cleanup."""
        name = 'kiwoom-op-worker-' + job
        try:
            value = self.inspect(name)
            state = value.get('State', {}) if type(value) is dict else {}
            exit_code = state.get('ExitCode')
            if type(exit_code) is int:
                report['worker_exit_code'] = exit_code
            oom_killed = state.get('OOMKilled')
            if type(oom_killed) is bool:
                report['worker_oom_killed'] = oom_killed
        except Exception as error:
            report['worker_inspection_error_type'] = type(error).__name__

        try:
            with Tree(str(worker), owners=(65534,)) as output:
                outcome = output.json('result.json')
            if type(outcome) is not dict:
                report['worker_result_available'] = False
                return
            summary = {}
            reason = outcome.get('failure_reason')
            if (type(reason) is str and
                    re.fullmatch(r'(?:capacity|private|trace|top20)_[a-z0-9_]{1,110}', reason)):
                summary['failure_reason'] = reason
            for key in ('state', 'tests', 'skipped', 'failures', 'errors', 'error_type',
                        'memory_limit_bytes', 'cpu_affinity', 'failed_tests',
                        'failed_tests_truncated'):
                value = outcome.get(key)
                if key == 'failed_tests' and type(value) is list:
                    entries = []
                    for item in value[:20]:
                        if type(item) is not dict:
                            continue
                        test_id = item.get('test_id')
                        error_type = item.get('error_type')
                        if (type(test_id) is str and len(test_id) <= 256 and
                                type(error_type) is str and len(error_type) <= 128):
                            locations = []
                            raw_locations = item.get('traceback_locations')
                            if type(raw_locations) is list:
                                for location in raw_locations[-8:]:
                                    if type(location) is not dict:
                                        continue
                                    file_name = location.get('file')
                                    line = location.get('line')
                                    function = location.get('function')
                                    if (type(file_name) is str and len(file_name) <= 128 and
                                            type(line) is int and line > 0 and
                                            type(function) is str and len(function) <= 128):
                                        locations.append({'file': file_name, 'line': line,
                                                          'function': function})
                            entries.append({'test_id': test_id, 'error_type': error_type,
                                            'traceback_locations': locations})
                    summary[key] = entries
                elif type(value) in (str, int, bool) and (type(value) is not str or len(value) <= 128):
                    summary[key] = value
            report['worker_result_available'] = True
            report['worker_result_summary'] = summary
        except Exception as error:
            report['worker_result_available'] = False
            report['worker_result_read_error_type'] = type(error).__name__

    def identities(self, running=True):
        for kind in ('server', 'database'):
            value = self.inspect(self.config[kind + '_id'])
            actual = fingerprint(value)
            if not fingerprint_matches(value, self.config[kind + '_fingerprint']):
                # Hashes identify the failing boundary without exposing inspect
                # output, which includes operational credentials in Config.Env.
                self.identity_mismatch = {'kind': kind, 'actual_fingerprint': actual,
                                          'approved_fingerprint': self.config[kind + '_fingerprint']}
                raise Rejected('approved_container_changed')
            if running or kind == 'database':
                require(value['State']['Running'] is True, 'required_container_stopped')

    def snapshot(self):
        return decode(self.docker(['exec', self.config['server_id'], 'python', '-I', '-c', SNAPSHOT_CODE]))

    def assert_mount_inode(self, host, destination):
        raw = self.docker(['exec', self.config['server_id'], 'python', '-I', '-c',
                          'import os,json,sys;s=os.stat(sys.argv[1]);print(json.dumps([s.st_dev,s.st_ino]))', destination])
        require(tuple(decode(raw)) == host.identity, 'bind_mount_inode_mismatch')

    @contextlib.contextmanager
    def fence(self, recovery=False, lock_held=False):
        # lock_held is internal to the administrator updater, which retains the
        # same private lock through final verification and rollback. No CLI input
        # can bypass locking.
        with (contextlib.nullcontext() if lock_held else self.private.lock('operator.lock')), \
                contextlib.ExitStack() as stack:
            require(self.access_state().get('state') == 'enabled', 'operator_access_revoked')
            require(self.helper_update_journal().get('state') in ('idle', 'complete', 'rolled_back'),
                    'administrator_helper_update_recovery_required')
            self.identities(running=not recovery)
            journal = self.journal()
            maintenance = self.operational_journal()
            job = self.job_journal()
            deployment_recovery = (journal.get('state') in ('prepared', 'stopped', 'selected', 'needs_admin') and
                                   journal.get('store_identity') is not None)
            replay_recovery = (maintenance.get('state') in
                               ('prepared', 'paused', 'resuming', 'awaiting_job_cleanup', 'resume_pending', 'needs_admin') and
                               maintenance.get('job_id') == job.get('job_id') and
                               job.get('command') == 'replay' and
                               maintenance.get('server_id') == self.config['server_id'] and
                               maintenance.get('server_fingerprint') == self.config['server_fingerprint'])
            if not recovery:
                require(journal.get('state') in ('idle', 'complete', 'rolled_back'), 'deployment_recovery_required')
                require(job.get('state') in ('idle', 'complete'), 'job_recovery_required')
                require(maintenance.get('state') in ('idle', 'complete'), 'operational_resume_required')
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
            server_running = self.inspect(self.config['server_id'])['State']['Running']
            if server_running:
                # A replay recovery may find the container already started but
                # not ready yet. Resume owns that state and verifies health itself.
                if not (recovery and replay_recovery):
                    idle(self.snapshot())
            else:
                deployment_recovery = deployment_recovery and journal.get('store_identity') == list(store.identity)
                require(recovery and (deployment_recovery or replay_recovery),
                        'stopped_server_recovery_unknown')
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

    def operational_journal(self):
        try:
            return self.private.json('replay-maintenance.json')
        except FileNotFoundError:
            return {'state': 'idle'}

    def helper_update_journal(self):
        try:
            return self.private.json('helper-update.json')
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
            require(self.helper_update_journal().get('state') in ('idle', 'complete', 'rolled_back'),
                    'administrator_helper_update_recovery_required')
            require(self.journal().get('state') in ('idle', 'complete', 'rolled_back'), 'deployment_recovery_required')
            require(self.job_journal().get('state') in ('idle', 'complete'), 'job_recovery_required')
            require(self.operational_journal().get('state') in ('idle', 'complete'),
                    'operational_resume_required')
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
                validate_sudo_policy(self.config, False, run=self.run_process, suffix='_after_revocation')
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

    def active_release(self):
        with Tree(self.config['store'], owners=(0, self.config['allowed_uid'])) as store:
            return identifier(store.json('active.json').get('release_id'))

    def status(self):
        self.identities(running=False)
        journal = self.journal()
        operational = self.operational_journal()
        result = {'journal': {k: journal.get(k) for k in ('state', 'target', 'previous')},
                  'job': self.job_journal(), 'installed': True, 'active_release': None,
                  'temporary_access': self.access_state()['state'],
                  'helper_update': {'state': self.helper_update_journal().get('state')},
                  'operational_pause': {k: operational.get(k) for k in
                      ('state', 'job_id', 'active_release', 'server_build', 'paused_at', 'resumed_at')}}
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

    def pause_operational_server(self, job_id, active_release):
        """Gracefully stop only the approved app container for one isolated replay."""
        previous = self.operational_journal()
        require(previous.get('state') in ('idle', 'complete'), 'operational_resume_required')
        server = self.inspect(self.config['server_id'])
        require(server['State']['Running'] is True and
                fingerprint_matches(server, self.config['server_fingerprint']), 'approved_server_not_running')
        manifest, content = self.source(active_release)
        snapshot = self.snapshot()
        require(snapshot.get('health') == 'ok' and
                snapshot.get('server_build') == manifest['server_build'] and
                snapshot.get('source_path') == '/app/source-runtime/releases/' + active_release + '/src',
                'active_server_identity_mismatch')
        idle(snapshot)
        record = {'state': 'prepared', 'job_id': job_id, 'server_id': self.config['server_id'],
                  'server_fingerprint': self.config['server_fingerprint'], 'active_release': active_release,
                  'server_build': manifest['server_build'], 'source_hash': content,
                  'was_running': True, 'collection_gap_expected': True,
                  'database_container_unchanged': True, 'realtime_loss_verified': False}
        self.private.put_json('replay-maintenance.json', record)
        job = self.job_journal()
        job.update(operational_pause='prepared', server_was_running=True)
        self.private.put_json('job.json', job)
        try:
            self.docker(['stop', '--time', '60', self.config['server_id']], timeout=90)
        except BaseException:
            # A lost Docker acknowledgement is ambiguous. Persist enough state for
            # `recover` to inspect and resume without issuing another stop blindly.
            try:
                stopped = self.inspect(self.config['server_id'])['State']['Running'] is False
            except BaseException:
                record['state'] = 'needs_admin'
                self.private.put_json('replay-maintenance.json', record)
                job.update(state='resume_pending', operational_pause='needs_admin')
                self.private.put_json('job.json', job)
                raise
            if stopped:
                record['state'] = 'paused'
                record['paused_at'] = time.time()
            else:
                record['state'] = 'complete'
                record['stop_not_confirmed'] = True
            self.private.put_json('replay-maintenance.json', record)
            job.update(operational_pause=record['state'])
            self.private.put_json('job.json', job)
            raise
        require(self.inspect(self.config['server_id'])['State']['Running'] is False,
                'server_stop_not_confirmed')
        record['state'] = 'paused'
        record['paused_at'] = time.time()
        self.private.put_json('replay-maintenance.json', record)
        job.update(operational_pause='paused')
        self.private.put_json('job.json', job)
        return record

    def resume_operational_server(self):
        """Restore the exact server container/release recorded before replay."""
        record = self.operational_journal()
        if record.get('state') in ('idle', 'complete'):
            return {'state': record.get('state', 'idle'), 'server_restarted': False}
        require(record.get('state') in
                ('prepared', 'paused', 'resuming', 'awaiting_job_cleanup', 'resume_pending', 'needs_admin') and
                record.get('was_running') is True and record.get('server_id') == self.config['server_id'] and
                record.get('server_fingerprint') == self.config['server_fingerprint'],
                'operational_resume_journal_invalid')
        active_release = identifier(record.get('active_release'))
        value = self.inspect(self.config['server_id'])
        require(fingerprint_matches(value, record['server_fingerprint']), 'approved_server_changed_during_replay')
        require(self.active_release() == active_release, 'active_release_changed_during_replay')
        manifest, content = self.source(active_release)
        require(content == record.get('source_hash') and manifest.get('server_build') == record.get('server_build'),
                'active_server_source_changed_during_replay')
        record['state'] = 'resuming'
        self.private.put_json('replay-maintenance.json', record)
        was_running = value['State']['Running'] is True
        if not was_running:
            self.docker(['start', self.config['server_id']])
        require(self.inspect(self.config['server_id'])['State']['Running'] is True,
                'server_start_not_confirmed')
        self.wait_ready(active_release, manifest)
        self.identities()
        require(self.active_release() == active_release, 'active_release_changed_during_resume')
        record.update(state='complete', resumed_at=time.time(), server_restarted=not was_running)
        self.private.put_json('replay-maintenance.json', record)
        job = self.job_journal()
        if job.get('job_id') == record.get('job_id'):
            job.update(operational_pause='complete', server_resumed=True)
            self.private.put_json('job.json', job)
        return {'state': 'complete', 'server_restarted': not was_running,
                'active_release': active_release, 'server_build': manifest['server_build'],
                'database_container_unchanged': True, 'collection_gap_expected': True,
                'realtime_loss_verified': False, 'paused_at': record.get('paused_at'),
                'resumed_at': record['resumed_at']}

    def worker_argv(self, job, pg_name, release_id, extra_mounts=(), *, worker_memory=None):
        root = self.private.path
        memory = self.config['worker_memory'] if worker_memory is None else worker_memory
        require(memory in (self.config['worker_memory'], CAPACITY_WORKER_MEMORY), 'invalid_worker_resource_profile')
        return ['create', '--name', 'kiwoom-op-worker-' + job, '--label', LABEL + '=' + job,
                '--network', 'container:' + pg_name, '--user', '65534:65534', '--read-only',
                '--security-opt', 'no-new-privileges', '--cap-drop', 'ALL',
                '--memory', str(memory), '--memory-swap', str(memory),
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
        self.last_docker_action = None
        self.last_docker_failure_class = None
        resources = job_resources(args, self.config)
        validate_cpu_limits(self.config, set(os.sched_getaffinity(0)))
        require(shutil.disk_usage(self.private.path).free >= resources['minimum_disk_free'], 'insufficient_disk_headroom')
        available = next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))
        require(available >= resources['worker_memory'] + self.config['pg_memory'] + 1024 ** 3,
                'insufficient_memory_headroom')
        with Tree(self.config['store'], owners=(0, self.config['allowed_uid'])) as store:
            active_before = store.read('active.json')
        job = secrets.token_hex(16)
        base = 'job-' + job
        worker = Path(self.private.path) / base / 'worker'
        password = secrets.token_hex(32)
        request = vars(args).copy()
        request.update(admin_password=password, job_id=job)
        pause_operational = bool(getattr(args, 'pause_operational', False))
        require(not (pause_operational and getattr(args, 'preflight_only', False)), 'preflight_cannot_pause_operational')
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
        report.update(resource_profile=resources['profile'], worker_memory_requested=resources['worker_memory'])
        stage = 'prepare_job'
        self.private.put_json('job.json', {'state': 'running', 'job_id': job, 'command': args.command,
                                           'operational_pause': 'not_requested' if not pause_operational else 'pending'})
        try:
            worker.mkdir(parents=True, mode=0o700)
            self.private.put_json(base + '/worker/request.json', request)
            os.chown(str(worker / 'request.json'), 65534, 65534)
            os.chown(str(worker), 65534, 65534)
            self.private.write(base + '/postgres-password', (password + '\n').encode(), mode=0o600)
            if storage == 'disk':
                data.mkdir(mode=0o700)
                # The image changes PGDATA to its PostgreSQL UID, then restarts
                # entrypoint as that UID. It must traverse this root-owned parent.
                # Keep read/write private; only search is granted to other UIDs.
                # fchmod is required because the supervisor umask strips mkdir's
                # group/other bits. Anchor the exact generated child, never follow.
                with self.private.parent(base + '/postgres') as (parent, leaf):
                    fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        info = os.fstat(fd)
                        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and
                                not info.st_mode & 0o022, 'disk_data_parent_identity_invalid')
                        os.fchmod(fd, 0o711)
                        require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o711,
                                'disk_data_parent_mode_not_applied')
                    finally:
                        os.close(fd)
                report['disk_data_parent_mode'] = 0o711
            if args.command == 'replay':
                registered = self.registered_input('traces', args.trace)
                policy = getattr(args, 'capture_policy', 'complete')
                require(registered.get('capture_policy', 'complete') == policy, 'trace_registration_policy_mismatch')
                report.update(capture_policy=policy, preflight_only=bool(getattr(args, 'preflight_only', False)),
                              source_manifest_sha256=registered.get('files', {}).get('manifest.json'))
                if not getattr(args, 'baseline_profile', None):
                    baseline = self.registered_input('baselines', args.baseline)
                    if baseline.get('baseline_version') == 3:
                        context = baseline['account_context']
                        captured = registered.get('account_context', {})
                        require(context['trace_id'] == args.trace and captured.get('state') == 'captured' and
                                captured.get('sha256') == context['sha256'], 'baseline_account_trace_mismatch')
                    report['source_state_equivalent'] = baseline['source_state_equivalent']
                    extra_mounts += ['--mount', 'type=bind,src=' + self.private.path + '/baselines/' + args.baseline + ',dst=/run/baseline,readonly']
                extra_mounts += ['--mount', 'type=bind,src=' + self.private.path + '/traces/' + args.trace + ',dst=/run/trace,readonly',
                                 '--mount', 'type=bind,src=' + self.private.path + '/traces/' + args.trace + ',dst=/tmp/diagnostic-traces/' + args.trace + ',readonly']
            stage = 'postgres_create'
            self.docker(pg)
            stage = 'postgres_start'
            self.docker(['start', pg_name])
            stage = 'postgres_readiness'
            readiness_started = time.monotonic()
            deadline = readiness_started + 60
            report['postgres_readiness_attempts'] = 0
            while True:
                report['postgres_readiness_attempts'] += 1
                try:
                    self.docker(['exec', pg_name, 'pg_isready', '-d', 'postgres'], timeout=5)
                    break
                except Rejected:
                    require(time.monotonic() < deadline, 'temporary_postgres_not_ready')
                    time.sleep(.5)
            report['postgres_readiness_seconds'] = round(time.monotonic() - readiness_started, 3)
            stage = 'postgres_memory_probe'
            actual_limit = self.docker(['exec', pg_name, '/bin/sh', '-c',
                'if [ -f /sys/fs/cgroup/memory.max ]; then cat /sys/fs/cgroup/memory.max; else cat /sys/fs/cgroup/memory/memory.limit_in_bytes; fi']).decode().strip()
            require(actual_limit.isdigit() and 0 < int(actual_limit) <= self.config['pg_memory'], 'pg_memory_limit_not_enforced')
            stage = 'postgres_affinity_probe'
            actual_cpu = self.docker(['exec', pg_name, '/bin/sh', '-c',
                'while IFS=: read -r key value; do if [ "$key" = Cpus_allowed_list ]; then printf "%s\\n" "$value"; fi; done < /proc/self/status']).decode().strip()
            verify_cpu_affinity(actual_cpu, self.config['pg_cpuset'])
            report['pg_cpu_affinity'] = actual_cpu
            stage = 'worker_create'
            self.docker(self.worker_argv(job, pg_name, args.release, extra_mounts,
                                         worker_memory=resources['worker_memory']))
            stage = 'container_isolation_inspection'
            for name, cpus in ((pg_name, self.config['pg_cpuset']),
                               ('kiwoom-op-worker-' + job, self.config['worker_cpuset'])):
                value = self.inspect(name)
                require(value['HostConfig']['Memory'] > 0 and not value['HostConfig']['Privileged'] and
                        value['Config']['Labels'][LABEL] == job, 'job_isolation_invalid')
                if resources['profile'] == 'recorder-capacity':
                    expected_memory = self.config['pg_memory'] if name == pg_name else CAPACITY_WORKER_MEMORY
                    require(value['HostConfig']['Memory'] == expected_memory, 'capacity_memory_limit_not_enforced')
                verify_cpu_affinity(value['HostConfig'].get('CpusetCpus'), cpus)
            if pause_operational:
                stage = 'operational_pause'
                active_release = identifier(decode(active_before).get('release_id'))
                maintenance = self.pause_operational_server(job, active_release)
                report['operational_pause'] = {'requested': True, 'state': maintenance['state'],
                    'active_release': active_release, 'server_build': maintenance['server_build'],
                    'collection_gap_expected': True, 'database_container_unchanged': True,
                    'realtime_loss_verified': False}
            with open(str(Path(self.private.path) / base / 'process.log'), 'xb') as log:
                stage = 'worker_start_and_run'
                self.docker(['start', '-a', 'kiwoom-op-worker-' + job], timeout=resources['job_timeout'], log=log)
            stage = 'worker_result_inspection'
            value = self.inspect('kiwoom-op-worker-' + job)
            require(value['State']['ExitCode'] == 0 and not value['State'].get('OOMKilled'), 'worker_failed')
            stage = 'worker_result_read'
            with Tree(str(worker), owners=(65534,)) as output:
                outcome = output.json('result.json')
            require(outcome.get('state') == 'passed', 'worker_gate_failed')
            require(type(outcome.get('memory_limit_bytes')) is int and
                    0 < outcome['memory_limit_bytes'] <= resources['worker_memory'], 'worker_memory_limit_not_enforced')
            if resources['profile'] == 'recorder-capacity':
                require(outcome['memory_limit_bytes'] == CAPACITY_WORKER_MEMORY, 'capacity_memory_limit_not_enforced')
            verify_cpu_affinity(outcome.get('cpu_affinity'), self.config['worker_cpuset'])
            stage = 'final_identity_fence'
            self.identities(running=not pause_operational)
            if pause_operational:
                require(self.inspect(self.config['server_id'])['State']['Running'] is False,
                        'operational_server_resumed_during_replay')
            stage = 'active_release_fence'
            with Tree(self.config['store'], owners=(0, self.config['allowed_uid'])) as store:
                require(store.read('active.json') == active_before, 'active_release_changed_during_job')
            stage = 'pids_limit_probe'
            pids = decode(self.docker(['info', '--format', '{{json .PidsLimit}}']))
            report.update(state='passed', outcome=outcome, pids_support=pids is True,
                          active_release_and_containers_unchanged=True)
        except BaseException as error:
            report['error_type'] = type(error).__name__
            report['failure_stage'] = stage
            if isinstance(error, Rejected):
                report['reason'] = str(error)
            if str(error) in ('docker_command_failed', 'docker_command_timeout'):
                report['failed_docker_action'] = self.last_docker_action
                report['docker_failure_class'] = self.last_docker_failure_class or 'unknown'
            if stage in ('postgres_start', 'postgres_readiness'):
                failed_action = self.last_docker_action
                failed_class = self.last_docker_failure_class
                if stage == 'postgres_readiness':
                    report['postgres_readiness_seconds'] = round(time.monotonic() - readiness_started, 3)
                self.postgres_failure_diagnostics(report, job)
                self.last_docker_action = failed_action
                self.last_docker_failure_class = failed_class
            if stage == 'worker_start_and_run':
                failed_action = self.last_docker_action
                failed_class = self.last_docker_failure_class
                self.worker_failure_diagnostics(report, job, worker)
                self.last_docker_action = failed_action
                self.last_docker_failure_class = failed_class
            raise
        finally:
            cleanup_error = None
            try:
                self.cleanup_job(job)
                report['cleanup_complete'] = True
            except BaseException as error:
                report.update(state='failed', cleanup_complete=False)
                cleanup_error = error
                if pause_operational:
                    maintenance = self.operational_journal()
                    if maintenance.get('state') not in ('idle', 'complete'):
                        maintenance['state'] = 'awaiting_job_cleanup'
                        self.private.put_json('replay-maintenance.json', maintenance)
                        self.private.put_json('job.json', {'state': 'resume_pending', 'job_id': job,
                                                           'command': args.command,
                                                           'operational_pause': 'awaiting_job_cleanup'})
            if cleanup_error is None and pause_operational:
                try:
                    report['operational_resume'] = self.resume_operational_server()
                except BaseException as error:
                    report.update(state='failed', operational_resume_failed=True)
                    self.private.put_json('job.json', {'state': 'resume_pending', 'job_id': job,
                                                       'command': args.command, 'operational_pause': 'resume_pending'})
                    cleanup_error = error
            if cleanup_error is None:
                self.private.put_json('job.json', {'state': 'complete', 'job_id': job,
                    'command': args.command, 'operational_pause': 'complete' if pause_operational else 'not_requested'})
            self.private.put_json('reports/' + job + '.json', report)
            if cleanup_error is not None:
                raise cleanup_error
        return report

    def registered_input(self, kind, input_id):
        identifier(input_id)
        catalog = self.private.json(kind + '/' + input_id + '/registration.json')
        for name, digest in catalog['files'].items():
            data = self.private.read(kind + '/' + input_id + '/' + name, self.config['input_file_limit'])
            require(hashlib.sha256(data).hexdigest() == digest, 'registered_input_changed')
        return catalog

    def register(self, kind, input_id, capture_policy='complete'):
        identifier(input_id)
        require(capture_policy in ('complete', 'scoped-operations', 'partial-operations') and
                (kind == 'traces' or capture_policy == 'complete'), 'invalid_capture_policy')
        incoming_path = self.config['trace_dir'] + '/' + input_id if kind == 'traces' else \
            self.config['project'] + '/operator-inputs/baselines/' + input_id
        with Tree(incoming_path, owners=(0, self.config['allowed_uid'])) as incoming:
            if kind == 'traces':
                manifest = decode(incoming.read('manifest.json', self.config['input_file_limit']))
                if capture_policy in ('scoped-operations', 'partial-operations'):
                    validate_scoped_trace_manifest(manifest, input_id)
                else:
                    require(manifest.get('trace_id') == input_id and manifest.get('state') == 'complete' and
                            manifest.get('accepted') == manifest.get('written') and manifest.get('known_dropped') == 0 and
                            manifest.get('input_rejected') == 0 and manifest.get('input_capture_censored') is False,
                            'trace_not_complete')
                names = {'manifest.json'}
                names.update(x['name'] for x in manifest.get('chunks', []))
                chunk_hashes = {item['name']: item['sha256'] for item in manifest.get('chunks', [])}
                blob_files = trace_blob_files(manifest, incoming, self.config['input_file_limit'])
                names.update(blob_files)
                catalog = {'source_state_equivalent': False, 'capture_policy': capture_policy,
                           'original_capture_state': manifest['state']}
                if manifest.get('schema_version') == 4:
                    catalog['account_context'] = manifest.get('account_context', {})
            else:
                manifest = incoming.json('baseline.json')
                require(manifest.get('baseline_id') == input_id and HEX.fullmatch(input_id) and
                        manifest.get('database') == 'kiwoom_monitor_replay_test' and
                        type(manifest.get('version')) is int and manifest['version'] in (1, 2, 3) and type(manifest.get('source_state_equivalent')) is bool and
                        re.fullmatch('[a-f0-9]{32}', manifest.get('owner_token', '')), 'invalid_baseline_bundle')
                require(type(manifest.get('statements_sha256')) is str and HEX.fullmatch(manifest['statements_sha256']),
                        'invalid_baseline_hash')
                validate_baseline_clock(manifest)
                names = {'baseline.json', 'statements.json'}
                catalog = {'source_state_equivalent': manifest['source_state_equivalent']}
                if manifest['version'] == 3:
                    catalog.update(baseline_version=3, account_context=manifest['account_context'],
                                   source_origin=manifest['source_origin'])
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
                if kind == 'traces' and blob_files.get(name) is not None:
                    require(files[name] == blob_files[name], 'trace_block_changed_during_registration')
                if existing is None:
                    self.private.write(prefix + '/' + name, data, mode=0o644)
            if kind == 'baselines':
                require(files['statements.json'] == manifest['statements_sha256'], 'baseline_hash_mismatch')
            if existing is not None:
                require(existing['files'] == files and (kind != 'traces' or
                        existing.get('capture_policy', 'complete') == capture_policy), 'registered_input_conflict')
                return existing
            incoming.verify_identity()
            catalog.update(files=files)
            if kind == 'traces':
                catalog['source_manifest_sha256'] = files['manifest.json']
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
                require(gate.get('passed') is True and gate.get('post_job_fence_verified') is True and
                        gate.get('source_hash') == content and
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
            maintenance = self.operational_journal()
            if maintenance.get('state') not in ('idle', 'complete'):
                self.resume_operational_server()
            self.private.put_json('reports/' + job['job_id'] + '.json',
                                  {'state': 'failed', 'job_id': job['job_id'],
                                   'reason': 'interrupted_job', 'cleanup_complete': True})
            self.private.put_json('job.json', {'state': 'complete', 'job_id': job['job_id']})
        elif self.operational_journal().get('state') not in ('idle', 'complete'):
            self.resume_operational_server()
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
    test.add_argument('--pause-operational', action='store_true',
                      help='required only for fixed recorder capacity profiles; resume the approved server after cleanup')
    for name in ('register-trace', 'register-baseline'):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument('input_id', type=identifier)
        if name == 'register-trace':
            command.add_argument('--capture-policy', choices=('complete', 'scoped-operations', 'partial-operations'), default='complete')
    replay = commands.add_parser('replay', allow_abbrev=False)
    replay.add_argument('release', type=identifier)
    replay.add_argument('trace', type=identifier)
    baseline = replay.add_mutually_exclusive_group(required=True)
    baseline.add_argument('--baseline')
    baseline.add_argument('--baseline-profile', choices=('empty-v1',))
    replay.add_argument('--expected-baseline-id')
    replay.add_argument('--preflight-only', action='store_true')
    replay.add_argument('--capture-policy', choices=('complete', 'scoped-operations', 'partial-operations'), default='complete')
    replay.add_argument('--window-start', type=float, required=True)
    replay.add_argument('--window-end', type=float, required=True)
    replay.add_argument('--mode', choices=('recorded_operations', 'collector_with_background'), default='recorded_operations')
    replay.add_argument('--include-workload', action='append', default=[])
    replay.add_argument('--exclude-workload', action='append', default=[])
    replay.add_argument('--collector-component', action='append', default=[])
    replay.add_argument('--concurrency', type=int, default=8)
    replay.add_argument('--storage', choices=('disk', 'ram'), default='disk')
    replay.add_argument('--pause-operational', action='store_true',
                        help='gracefully stop and verify the approved server container during replay, then resume it')
    return result


def job_resources(args, config):
    """Two fixed capacity probes; no caller-controlled Docker resource values."""
    capacity = args.command == 'test' and args.profile in CAPACITY_PROFILES
    return {'profile': 'recorder-capacity' if capacity else 'standard',
            'worker_memory': CAPACITY_WORKER_MEMORY if capacity else config['worker_memory'],
            'job_timeout': 14400 if capacity else config['job_timeout'],
            'minimum_disk_free': max(CAPACITY_DISK_FREE, config['minimum_disk_free'])
                                 if capacity else config['minimum_disk_free']}


def validate_args(args, config):
    if args.command == 'report':
        require(re.fullmatch('[a-f0-9]{32}', args.job_id), 'invalid_job_id')
    if args.command == 'test':
        # Fixed built-in profiles extend only the helper code. The installed
        # root-owned configuration, sudo rule and standard 4GiB jobs stay intact.
        require(args.profile in config['profiles'] or args.profile in CAPACITY_PROFILES, 'unknown_test_profile')
        require(bool(getattr(args, 'pause_operational', False)) == (args.profile in CAPACITY_PROFILES),
                'capacity_requires_operational_pause' if args.profile in CAPACITY_PROFILES else 'test_pause_profile_invalid')
        require(args.profile == 'selected' or not args.test, 'profile_does_not_accept_test_names')
        require(args.profile != 'selected' or 0 < len(args.test) <= 100, 'selected_tests_required')
        require(all(TEST.fullmatch(x) for x in args.test), 'invalid_test_name')
    if args.command == 'replay':
        require(args.baseline is None or HEX.fullmatch(args.baseline) is not None, 'invalid_baseline_id')
        require(args.expected_baseline_id is None or HEX.fullmatch(args.expected_baseline_id) is not None,
                'invalid_baseline_id')
        require(not args.preflight_only or args.baseline_profile == 'empty-v1', 'preflight_requires_empty_profile')
        require(not (args.preflight_only and args.pause_operational), 'preflight_cannot_pause_operational')
        require(not args.baseline_profile or args.preflight_only or args.expected_baseline_id is not None,
                'expected_baseline_required')
        require(args.expected_baseline_id is None or args.baseline_profile == 'empty-v1',
                'expected_baseline_requires_profile')
        require(args.capture_policy not in ('scoped-operations', 'partial-operations') or
                (args.mode == 'recorded_operations' and bool(args.include_workload) and not args.collector_component),
                'scoped_operations_selection_required')
        require(math.isfinite(args.window_start) and math.isfinite(args.window_end) and
                0 <= args.window_start < args.window_end <= 7200, 'invalid_replay_window')
        require(1 <= args.concurrency <= config['max_concurrency'], 'invalid_concurrency')
        for values in (args.include_workload, args.exclude_workload, args.collector_component):
            require(len(values) <= 100 and all(WORD.fullmatch(x) for x in values), 'invalid_workload_selection')
        require(not set(args.include_workload) & set(args.exclude_workload), 'conflicting_workload_selection')
        require(len(set(args.include_workload)) == len(args.include_workload) and
                len(set(args.exclude_workload)) == len(args.exclude_workload), 'duplicate_workload_selection')


def load_config(revoking=False):
    require(os.name == 'posix' and os.geteuid() == 0, 'installed_root_launcher_required')
    with Tree('/etc/kiwoom-nas', owners=(0,), protected=True) as tree:
        config = tree.json('operator.json')
    require(config.get('format') == 1, 'invalid_operator_config')
    require(config.get('sudoers_validation', 'visudo') in ('visudo', 'native_fixed_rule'),
            'invalid_sudoers_validator')
    if not revoking:
        validate_cpu_limits(config, set(os.sched_getaffinity(0)))
    sudo_uid = os.environ.get('SUDO_UID')
    require(sudo_uid is None or sudo_uid == str(config['allowed_uid']), 'operator_uid_not_allowed')
    validator = config['visudo'] if config.get('sudoers_validation', 'visudo') == 'visudo' else config['sudo']
    paths = (validator, HELPER + '/nas_operator.py') if revoking else (
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
                result = None
                try:
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
                            result = operator.register(kind, args.input_id,
                                capture_policy=getattr(args, 'capture_policy', 'complete'))
                        else:
                            manifest, content = operator.source(args.release)
                            result = operator.run_job(args, manifest, content)
                except BaseException as error:
                    if isinstance(result, dict) and re.fullmatch('[a-f0-9]{32}', result.get('job_id', '')):
                        result.update(state='failed', post_job_fence_verified=False,
                                      error_type=type(error).__name__)
                        if isinstance(error, Rejected):
                            result['reason'] = str(error)
                        if getattr(operator, 'identity_mismatch', None):
                            result['identity_mismatch'] = operator.identity_mismatch
                        result['active_release_and_containers_unchanged'] = False
                        private.put_json('reports/' + result['job_id'] + '.json', result)
                    raise
                if args.command in ('test', 'replay'):
                    result['post_job_fence_verified'] = True
                    private.put_json('reports/' + result['job_id'] + '.json', result)
                    if args.command == 'test':
                        private.put_json('gates/' + args.release + '.json',
                                         {'source_hash': result['source_hash'],
                                          'profile': args.profile, 'passed': True,
                                          'post_job_fence_verified': True})
            print(json.dumps({'state': 'ok', 'command': args.command, 'result': result}, sort_keys=True))
            return 0
    except Exception as error:
        # Arbitrary library/Docker errors may contain secrets. Only our codes are public.
        print(json.dumps({'state': 'failed', 'command': args.command if args else None, 'error_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, Rejected) else 'operator_internal_error'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
