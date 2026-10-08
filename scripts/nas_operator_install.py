"""One-time root installation. Not included in the NOPASSWD command surface."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys

# Keep one exception/IO implementation when the offline gate imports this package.
# Direct administrator execution imports only from its reviewed bundle directory.
if __package__:
    from . import nas_operator as op
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import nas_operator as op

HELPER = op.HELPER
PRIVATE = '/volume1/@kiwoom-nas-operator'
PROJECT = '/volume1/docker/kiwoom-monitor'
ROOT_LAUNCHER = op.ROOT_LAUNCHER
CLIENT = op.CLIENT
SUDOERS = op.SUDOERS


def executable(name):
    path = shutil.which(name, path=op.CLEAN_ENV['PATH'] + ':/usr/syno/bin:/usr/sbin')
    op.require(path is not None, 'required_tool_unavailable')
    resolved = str(Path(path).resolve(strict=True))
    with op.Tree(str(Path(resolved).parent), owners=(0,), protected=True) as tree:
        with tree.parent(Path(resolved).name) as (parent, leaf):
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                tree._check(os.fstat(fd))
            finally:
                os.close(fd)
    return resolved


def check_acl(path, tool):
    """Synology extended ACLs can override mode bits; unknown output is rejected."""
    result = subprocess.run([tool, '-get', str(path)], env=op.CLEAN_ENV,
                            cwd='/', stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    text = result.stdout.decode('utf-8', errors='replace')
    op.require(result.returncode == 0, 'acl_inspection_unavailable')
    if 'Linux mode' in text or 'No ACL' in text:
        return
    op.require(result.returncode == 0 and 'ACL version:' in text, 'acl_inspection_unavailable')
    entries = re.findall(r'\[\d+\]\s+([^\r\n]+)', text)
    op.require(bool(entries), 'acl_inspection_unknown')
    for entry in entries:
        parts = entry.split(':')
        op.require(len(parts) >= 4 and parts[2] in ('allow', 'deny'), 'acl_entry_unknown')
        if parts[2] == 'allow' and any(x in parts[3] for x in 'wpdDaAWCo'):
            op.require(parts[:2] == ['user', 'root'], 'protected_acl_grants_nonroot_write')


def protected_directory(path, acltool):
    path = Path(path)
    if not path.exists():
        protected_directory(path.parent, acltool)
        mode = 0o700 if str(path).startswith(('/etc/kiwoom-nas', PRIVATE)) else 0o755
        os.mkdir(str(path), mode)
        fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fchmod(fd, mode)
        finally:
            os.close(fd)
    with op.Tree(str(path), owners=(0,), protected=True):
        for parent in (path, *path.parents):
            check_acl(parent, acltool)


def probe_cpu_limits(supervisor):
    """Fail before installing privileges if Docker cannot enforce the pinned masks."""
    job = secrets.token_hex(16)
    code = ('import json,os; from pathlib import Path; '
            'p=Path("/sys/fs/cgroup/memory.max"); '
            'p=p if p.exists() else Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"); '
            'print(json.dumps({"cpu_affinity":",".join(str(x) for x in sorted(os.sched_getaffinity(0))),'
            '"memory_limit":int(p.read_text())}))')
    results = {}
    try:
        for kind in ('worker', 'pg'):
            name = 'kiwoom-op-' + kind + '-' + job
            mask = supervisor.config[kind + '_cpuset']
            supervisor.docker(['create', '--name', name, '--label', op.LABEL + '=' + job,
                               '--network', 'none', '--user', '65534:65534', '--read-only',
                               '--memory', '268435456', '--memory-swap', '268435456',
                               '--cpuset-cpus', mask, '--security-opt', 'no-new-privileges',
                               '--cap-drop', 'ALL', '--entrypoint', 'python',
                               supervisor.config['runtime_image_id'], '-I', '-S', '-c', code])
            result = op.decode(supervisor.docker(['start', '-a', name], timeout=60))
            op.verify_cpu_affinity(result.get('cpu_affinity'), mask)
            op.require(type(result.get('memory_limit')) is int and
                       0 < result['memory_limit'] <= 268435456, 'probe_memory_limit_not_enforced')
            results[kind] = result
    finally:
        supervisor.cleanup_containers(job)
    return results


def install(user):
    op.require(os.name == 'posix' and os.geteuid() == 0, 'administrator_install_required')
    op.require(re.fullmatch('[a-z_][a-z0-9_-]{0,31}', user), 'invalid_operator_user')
    account = pwd.getpwnam(user)
    op.require(account.pw_uid != 0, 'nonroot_operator_required')
    os.umask(0o077)
    docker, python, visudo, acltool, sudo = (executable(x) for x in ('docker', 'python3', 'visudo', 'synoacltool', 'sudo'))
    root = Path(__file__).resolve().parents[1]
    store = PROJECT + '/source-runtime'
    control = PROJECT + '/deploy/synology/server-data/maintenance'
    config = {'format': 1, 'allowed_uid': account.pw_uid, 'docker': docker, 'python': python, 'sudo': sudo,
              'visudo': visudo, 'installation_mode': 'development_only',
              'helper_dir': HELPER, 'private': PRIVATE, 'project': PROJECT, 'store': store,
              'control_dir': control, 'artifacts': PROJECT + '/artifacts',
              'trace_dir': control + '/diagnostic-traces', 'worker_memory': 4 * 1024 ** 3,
              'pg_memory': 768 * 1024 ** 2,
              'job_timeout': 7200, 'ready_timeout': 90, 'max_concurrency': 16,
              'input_file_limit': 64 * 1024 ** 2, 'input_total_limit': 64 * 1024 ** 3,
              'minimum_disk_free': 4 * 1024 ** 3,
              'profiles': ['replay-cache', 'storage', 'selected'], 'deploy_profiles': ['storage', 'replay-cache']}
    config.update(op.cpu_limits(set(os.sched_getaffinity(0))))
    supervisor = op.Operator(config, None)
    server = supervisor.inspect('kiwoom-monitor-server-1')
    database = supervisor.inspect('kiwoom-monitor-database-1')
    for value, kind in ((server, 'server'), (database, 'database')):
        op.require(value['State']['Running'] is True and not value['HostConfig']['Privileged'] and
                   value['HostConfig']['NetworkMode'] != 'host' and not value['HostConfig'].get('Devices') and
                   not any(m['Destination'] in ('/var/run/docker.sock', '/run/docker.sock') or
                           m['Source'] in ('/', '/var/run/docker.sock') for m in value['Mounts']),
                   'unsafe_existing_container_profile')
        config[kind + '_id'] = value['Id']
        config[kind + '_fingerprint'] = op.fingerprint(value)
    config['runtime_image_id'], config['pg_image_id'] = server['Image'], database['Image']
    mounts = {item['Destination']: item for item in server['Mounts']}
    op.require(mounts.get('/app/source-runtime', {}).get('Source') == store and
               mounts['/app/source-runtime']['RW'] is False and
               mounts.get('/app/data', {}).get('Source') == PROJECT + '/deploy/synology/server-data',
               'expected_source_mount_required')
    with op.Tree(store, owners=(0, account.pw_uid)) as source:
        active = source.json('active.json')['release_id']
        op.identifier(active)
        manifest = source.json('releases/' + active + '/manifest.json')
        config['contract'] = manifest['contract']
        config['initial_release'] = active
        config['runner_sha256'] = hashlib.sha256(source.read('runner.py')).hexdigest()
        op.validate_manifest(manifest, active, config['contract'])
        config['runtime_sha256'] = hashlib.sha256(source.read('runtime.json')).hexdigest()
        with op.Tree(control, owners=(0, account.pw_uid)) as diagnostic, \
                op.Tree(PROJECT + '/artifacts', owners=(0, account.pw_uid)) as artifacts, contextlib.ExitStack() as locks:
            supervisor.assert_mount_inode(source, '/app/source-runtime')
            supervisor.assert_mount_inode(diagnostic, '/app/data/maintenance')
            locks.enter_context(diagnostic.lock('diagnostic-run.lock'))
            for name in os.listdir(artifacts.fd):
                if re.fullmatch(r'nas-trace-start-[0-9]{8}\.status\.json\.lock', name):
                    locks.enter_context(artifacts.lock(name))
            op.idle(supervisor.snapshot())
            config['resource_probe'] = probe_cpu_limits(supervisor)
            return install_files(root, config, visudo, acltool)


def install_files(root, config, visudo, acltool):
    # This transaction begins only after capture/scheduler fences and idle checks.
    for name in (HELPER, PRIVATE, '/etc/kiwoom-nas', '/usr/local/sbin', '/usr/local/bin', '/etc/sudoers.d'):
        protected_directory(name, acltool)
    launcher = ('#!/bin/sh\nset -eu\numask 077\ncd /\nexec ' + config['python'] +
                ' -I -S ' + HELPER + '/nas_operator.py "$@"\n').encode()
    files = {
        HELPER + '/nas_operator.py': (root.joinpath('scripts/nas_operator.py').read_bytes(), 0o644),
        HELPER + '/nas_operator_worker.py': (root.joinpath('scripts/nas_operator_worker.py').read_bytes(), 0o644),
        ROOT_LAUNCHER: (launcher, 0o755),
        CLIENT: (('#!/bin/sh\nset -eu\nPATH=/usr/local/bin:/usr/bin:/bin\nexport PATH\nexec ' +
                  config['sudo'] + ' -n ' + ROOT_LAUNCHER + ' "$@"\n').encode(), 0o755),
        op.CONFIG: ((json.dumps(config, sort_keys=True) + '\n').encode(), 0o600),
    }
    rule = (pwd.getpwuid(config['allowed_uid']).pw_name + ' ALL=(root) NOPASSWD: ' + ROOT_LAUNCHER + '\n').encode()
    backups = {}
    with op.Tree(PRIVATE, owners=(0,), protected=True) as private:
        if Path(PRIVATE + '/deployment.json').exists():
            op.require(private.json('deployment.json').get('state') not in ('prepared', 'stopped', 'selected', 'needs_admin'),
                       'unfinished_operator_deployment')
        with private.lock('operator.lock'):
            try:
                job = private.json('job.json')
            except FileNotFoundError:
                job = {'state': 'idle'}
            op.require(job.get('state') in ('idle', 'complete'), 'unfinished_operator_job')
            recover_install(private, set(files) | {SUDOERS})
            try:
                existing = private.json('install-backup-index.json')
            except FileNotFoundError:
                existing = {}
            op.require(existing.get('state') != 'complete', 'operator_already_installed')
            with op.Tree('/etc/sudoers.d', owners=(0,), protected=True) as tree:
                try:
                    tree.read(Path(SUDOERS).name)
                except FileNotFoundError:
                    pass
                else:
                    raise op.Rejected('sudoers_path_already_exists')
            candidate = '/etc/sudoers.d/.kiwoom-operator-candidate-' + os.urandom(8).hex()
            try:
                for path in list(files) + [SUDOERS]:
                    with op.Tree(str(Path(path).parent), owners=(0,), protected=True) as tree:
                        try:
                            old = tree.read(Path(path).name, 8 * 1024 ** 2)
                            mode = stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
                            backups[path] = (old, mode)
                        except FileNotFoundError:
                            backups[path] = None
                for index, (path, old) in enumerate(backups.items()):
                    if old is not None:
                        private.write('install-backups/' + str(index), old[0])
                installed = dict(files)
                installed[SUDOERS] = rule, 0o440
                private.put_json('install-backup-index.json', {'format': 2, 'state': 'prepared', 'entries': [
                    {'path': path, 'index': index, 'existed': old is not None,
                     'mode': old[1] if old else None, 'sha256': hashlib.sha256(old[0]).hexdigest() if old else None,
                     'installed_sha256': hashlib.sha256(installed[path][0]).hexdigest(),
                     'installed_mode': installed[path][1]}
                    for index, (path, old) in enumerate(backups.items())]})
                for path, (data, mode) in files.items():
                    with op.Tree(str(Path(path).parent), owners=(0,), protected=True) as tree:
                        tree.write(Path(path).name, data, mode)
                    check_acl(path, acltool)
                with op.Tree('/etc/sudoers.d', owners=(0,), protected=True) as tree:
                    tree.write(Path(candidate).name, rule, 0o440)
                subprocess.run([visudo, '-cf', candidate], env=op.CLEAN_ENV, cwd='/', check=True, timeout=15)
                subprocess.run([config['python'], '-I', '-S', HELPER + '/nas_operator.py', 'status'],
                               env=op.CLEAN_ENV, cwd='/', check=True, timeout=60)
                with op.Tree('/etc/sudoers.d', owners=(0,), protected=True) as tree:
                    tree.write(Path(SUDOERS).name, rule, 0o440)
                subprocess.run([visudo, '-c'], env=op.CLEAN_ENV, cwd='/', check=True, timeout=15)
                private.put_json('installation.json', {'installed': True, 'allowed_uid': config['allowed_uid'],
                                                       'initial_release': config['initial_release']})
                index = private.json('install-backup-index.json')
                index['state'] = 'complete'
                private.put_json('install-backup-index.json', index)
            except BaseException:
                for path, old in reversed(list(backups.items())):
                    with op.Tree(str(Path(path).parent), owners=(0,), protected=True) as tree:
                        if old is not None:
                            tree.write(Path(path).name, old[0], old[1])
                        else:
                            with tree.parent(Path(path).name) as (parent, leaf):
                                try:
                                    os.unlink(leaf, dir_fd=parent)
                                    os.fsync(parent)
                                except FileNotFoundError:
                                    pass
                if backups:
                    try:
                        index = private.json('install-backup-index.json')
                    except FileNotFoundError:
                        index = None
                    if index is not None and index.get('state') == 'prepared':
                        index['state'] = 'rolled_back'
                        private.put_json('install-backup-index.json', index)
                raise
            finally:
                with op.Tree('/etc/sudoers.d', owners=(0,), protected=True) as tree:
                    with tree.parent(Path(candidate).name) as (parent, leaf):
                        try:
                            os.unlink(leaf, dir_fd=parent)
                        except FileNotFoundError:
                            pass
    return {'installed': True, 'operator_user': pwd.getpwuid(config['allowed_uid']).pw_name,
            'server_restarted': False, 'database_container_unchanged': True}


def recover_install(private, allowed):
    try:
        index = private.json('install-backup-index.json')
    except FileNotFoundError:
        return
    if index.get('state') != 'prepared':
        return
    op.require({item['path'] for item in index['entries']} == allowed, 'install_recovery_scope_mismatch')
    for item in reversed(index['entries']):
        path = Path(item['path'])
        with op.Tree(str(path.parent), owners=(0,), protected=True) as tree:
            if item['existed']:
                data = private.read('install-backups/' + str(item['index']), 8 * 1024 ** 2)
                op.require(hashlib.sha256(data).hexdigest() == item['sha256'], 'install_backup_changed')
                tree.write(path.name, data, item['mode'])
            else:
                with tree.parent(path.name) as (parent, leaf):
                    try:
                        os.unlink(leaf, dir_fd=parent)
                        os.fsync(parent)
                    except FileNotFoundError:
                        pass
    index['state'] = 'rolled_back'
    private.put_json('install-backup-index.json', index)


def main():
    def interrupted(unused_number, unused_frame):
        raise KeyboardInterrupt()
    if os.name == 'posix':
        for number in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(number, interrupted)
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--user', required=True)
    value = args.parse_args()
    try:
        result = install(value.user)
        print(json.dumps({'state': 'passed', 'result': result}, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({'state': 'failed', 'error_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, op.Rejected) else 'install_failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
