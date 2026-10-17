"""Administrator-only additive runtime review. Preserve active source and DB.

Build an image from the approved image ID, check the exact candidate with the
installed isolated worker, then replace only the server with its exact saved
configuration. Retain the stopped original container for rollback. The core
dependency/schema contract remains valid; the additive runtime extension has
its own image ID and descriptor hash in runtime.json and the operator approval.
"""
import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

IMAGE = 'kiwoom-monitor-server:2026.10.15-news-chatgpt-runtime-v3'
BASE = '2026.10.14-recorder-shared-10m-v1-af0f8072f960415b'
NAME = 'kiwoom-monitor-server-1'
NEW_NAME = 'kiwoom-monitor-server-chatgpt-runtime-candidate'
OLD_NAME = 'kiwoom-monitor-server-pre-chatgpt-runtime'


def replacement_body(original, image):
    config = copy.deepcopy(original['Config'])
    config['Image'] = image
    config['HostConfig'] = copy.deepcopy(original['HostConfig'])
    config['NetworkingConfig'] = {'EndpointsConfig': {
        name: {key: copy.deepcopy(value[key]) for key in ('IPAMConfig', 'Links', 'Aliases') if value.get(key) is not None}
        for name, value in original['NetworkSettings']['Networks'].items()}}
    return config


def network_identities(operator, original):
    names = sorted(original['NetworkSettings']['Networks'])
    if not names:
        raise RuntimeError('replacement_network_missing')
    values = json.loads(operator.docker(['network', 'inspect'] + names))
    result = {item['Name']: item['Id'] for item in values}
    if len(values) != len(names) or set(result) != set(names):
        raise RuntimeError('replacement_network_changed')
    return result


def check_replacement(original, actual, image, *, created=False, resolved_networks=None):
    expected = copy.deepcopy(original['Config'])
    expected['Image'] = image
    if actual['Config'] != expected or actual['HostConfig'] != original['HostConfig']:
        raise RuntimeError('replacement_configuration_changed')
    def mounts(value):
        return sorted((item['Type'], item['Source'], item['Destination'], item['RW']) for item in value['Mounts'])
    if mounts(original) != mounts(actual): raise RuntimeError('replacement_mounts_changed')
    before = original['NetworkSettings']['Networks']
    after = actual['NetworkSettings']['Networks']
    expected_ids = {name: value['NetworkID'] for name, value in before.items()}
    if not before or set(before) != set(after) or not all(expected_ids.values()):
        raise RuntimeError('replacement_network_changed')
    if created and (actual['State']['Running'] or actual['State']['Status'] != 'created'
                    or resolved_networks != expected_ids):
        raise RuntimeError('replacement_network_changed')
    if not created and not actual['State']['Running']:
        raise RuntimeError('replacement_network_not_running')
    for name, endpoint in after.items():
        # Docker 24 returns an empty NetworkID until the first start. Only the
        # created stage may use the independently resolved, original network ID.
        allowed_ids = ('', expected_ids[name]) if created else (expected_ids[name],)
        if endpoint['NetworkID'] not in allowed_ids:
            raise RuntimeError('replacement_network_changed')
        for key in ('IPAMConfig', 'Links'):
            if endpoint.get(key) != before[name].get(key):
                raise RuntimeError('replacement_network_settings_changed')
        aliases = set(endpoint.get('Aliases') or ())
        original_aliases = set(before[name].get('Aliases') or ())
        # Engine may add the replacement's own short ID when starting it.
        generated = {actual['Id'][:12]} if not created else set()
        if not original_aliases <= aliases or aliases - original_aliases - generated:
            raise RuntimeError('replacement_network_aliases_changed')


def reviewed_index(index, config_path, config_bytes):
    result = copy.deepcopy(index)
    entries = [item for item in result['entries'] if item['path'] == config_path]
    if index.get('state') != 'complete' or len(entries) != 1:
        raise RuntimeError('operator_approval_index_invalid')
    entries[0].update(installed_sha256=hashlib.sha256(config_bytes).hexdigest(), installed_mode=0o600)
    return result


def create_container(body):
    class DockerConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(30)
            self.sock.connect('/var/run/docker.sock')
    connection = DockerConnection('localhost')
    try:
        connection.request('POST', '/v1.24/containers/create?name=' + NEW_NAME,
                           json.dumps(body).encode(), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        data = response.read(65537)
        if response.status != 201 or len(data) > 65536: raise RuntimeError('replacement_create_failed')
        identity = json.loads(data)['Id']
        if not re.fullmatch('[a-f0-9]{64}', identity): raise RuntimeError('replacement_identity_invalid')
        return identity
    finally:
        connection.close()


def install_session_entry(op, config, bundle, descriptor):
    data = (bundle / 'nas_chatgpt_session_operator.py').read_bytes()
    op.require(hashlib.sha256(data).hexdigest() == descriptor['session_operator_sha256'], 'session_operator_changed')
    launcher = b'#!/bin/sh\nexec /usr/bin/python3 -I -S /usr/local/libexec/kiwoom-nas/chatgpt_session_operator.py "$@"\n'
    # Synology's interpreter path is established by the installed approval.
    launcher = launcher.replace(b'/usr/bin/python3', config['python'].encode())
    with op.Tree(config['helper_dir'], owners=(0,), protected=True) as tree:
        tree.write('chatgpt_session_operator.py', data, 0o644)
    with op.Tree('/usr/local/sbin', owners=(0,), protected=True) as tree:
        tree.write('kiwoom-news-chatgpt-session', launcher, 0o755)
    rule = (config['allowed_user'] + ' ALL=(root) NOPASSWD: /usr/local/sbin/kiwoom-news-chatgpt-session\n').encode()
    with op.Tree('/etc/sudoers.d', owners=(0,), protected=True) as tree:
        try:
            previous = tree.read('kiwoom-news-chatgpt-session')
        except FileNotFoundError:
            previous = None
        op.require(previous is None or previous == rule, 'session_sudoers_conflict')
        tree.write('kiwoom-news-chatgpt-session', rule, 0o440)
        command = ([config['visudo'], '-c'] if config.get('visudo') else
                   [config['sudo'], '-n', '-l', '-U', config['allowed_user']])
        result = subprocess.run(command, env=op.CLEAN_ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        valid = result.returncode == 0 and not result.stderr
        if not config.get('visudo'):
            valid = valid and bool(re.search(rb'^\s*\(root\)\s+NOPASSWD: /usr/local/sbin/kiwoom-news-chatgpt-session\s*$', result.stdout, re.M))
        if not valid:
            with tree.parent('kiwoom-news-chatgpt-session') as (parent, leaf): os.unlink(leaf, dir_fd=parent)
            raise RuntimeError('session_sudoers_validation_failed')


def install(bundle, expected_hash):
    sys.path.insert(0, '/usr/local/libexec/kiwoom-nas')
    import nas_operator as op
    op.require(os.geteuid() == 0, 'administrator_required')
    op.require(str(bundle.resolve()) == '/volume1/docker/kiwoom-monitor/artifacts/news-chatgpt-plan-ed9d-runtime-v3', 'installer_path_changed')
    os.umask(0o077)
    raw = (bundle / 'runtime-extension.json').read_bytes()
    op.require(hashlib.sha256(raw).hexdigest() == expected_hash, 'extension_descriptor_changed')
    descriptor = json.loads(raw)
    op.require(descriptor['base_release'] == BASE and descriptor['google_auth'] == '2.41.1', 'extension_scope_changed')
    config = op.load_config()
    with op.Tree(config['private'], owners=(0,), protected=True) as private:
        operator = op.Operator(config, private)
        with private.lock('operator.lock'):
            with operator.fence(lock_held=True) as store:
                op.require(store.json('active.json')['release_id'] == BASE, 'active_release_changed')
                old_runtime = store.read('runtime.json')
                op.require(hashlib.sha256(old_runtime).hexdigest() == descriptor['base_runtime_sha256'], 'base_runtime_changed')
                manifest, content = operator.source(descriptor['candidate_release'])
                op.require(hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest() == descriptor['candidate_manifest_sha256'], 'candidate_manifest_changed')
                original = operator.inspect(config['server_id'])
                op.require('KIWOOM_SERVER_SECRET_DIR=/app/secrets' in original['Config']['Env'] and
                    any(m['Destination'] == '/app/secrets' and m['Source'] == config['project'] + '/deploy/synology/server-secrets'
                        and m['Type'] == 'bind' and m['RW'] for m in original['Mounts']), 'protected_secret_mount_required')
                # The old exact configuration and DB remain active during both gates.
                runtime_base = json.loads(old_runtime)
                base_image = json.loads(operator.docker(['image', 'inspect', runtime_base['image']]))[0]
                op.require(base_image['Id'] == config['runtime_image_id'], 'runtime_base_tag_changed')
                dockerfile = ('FROM ' + runtime_base['image'] + '\nRUN pip install --no-cache-dir "google-auth==2.41.1"\n').encode()
                built = subprocess.run([config['docker'], '--host', 'unix:///var/run/docker.sock', 'build',
                    '--tag', IMAGE, '-'], input=dockerfile, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=op.CLEAN_ENV, timeout=900)
                op.require(built.returncode == 0, 'runtime_extension_build_failed')
                built_image = json.loads(operator.docker(['image', 'inspect', IMAGE]))[0]
                op.require(built_image['RootFS']['Layers'][:len(base_image['RootFS']['Layers'])] == base_image['RootFS']['Layers'], 'runtime_extension_base_changed')
                image = built_image['Id']
                operator.docker(['run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges', '--user', '65534:65534', '--entrypoint', 'python', image,
                    '-I', '-c', 'import google.auth,cryptography; assert google.auth.__version__=="2.41.1"'])
                reviewed = copy.deepcopy(config)
                reviewed['runtime_image_id'] = image
                checker = op.Operator(reviewed, private)
                for profile in ('selected', 'storage'):
                    arguments = ['test', descriptor['candidate_release'], '--profile', profile]
                    if profile == 'selected':
                        arguments += ['--test', 'tests.unit.test_nas_chatgpt_plan', '--test', 'tests.integration.test_chatgpt_linux_store', '--test', 'tests.unit.test_chatgpt_nas_transfer', '--test', 'tests.unit.test_central_ai_service', '--test', 'tests.unit.test_news_jobs', '--test', 'tests.unit.test_morning_market_brief', '--test', 'tests.unit.test_morning_market_ai']
                    result = checker.run_job(op.parser().parse_args(arguments), manifest, content)
                    op.require(result['state'] == 'passed' and result['cleanup_complete'] and result['active_release_and_containers_unchanged'], 'runtime_candidate_gate_failed')
                    operator.identities()
                    op.require(store.json('active.json')['release_id'] == BASE, 'gate_changed_active_source')
                    result['post_job_fence_verified'] = True
                    private.put_json('reports/' + result['job_id'] + '.json', result)
                # Preserve original container and backups; all replacement fields
                # except image must exactly match, including secrets held in memory.
                new_id = create_container(replacement_body(original, image))
                try:
                    check_replacement(original, operator.inspect(new_id), image, created=True,
                                      resolved_networks=network_identities(operator, original))
                except BaseException:
                    op.require(not operator.inspect(new_id)['State']['Running'], 'replacement_unexpectedly_running')
                    operator.docker(['rm', new_id])
                    raise
                old_helper = private.read('helper-update.json')
                with op.Tree(str(Path(op.CONFIG).parent), owners=(0,), protected=True) as configuration:
                    old_configuration = configuration.read(Path(op.CONFIG).name)
                op.require(json.loads(old_configuration) == config, 'operator_configuration_changed')
                old_index = private.read('install-backup-index.json')
                private.write('chatgpt-runtime/config.before.json', old_configuration)
                private.write('chatgpt-runtime/index.before.json', old_index)
                private.write('chatgpt-runtime/runtime.before.json', old_runtime)
                private.write('chatgpt-runtime/helper.before.json', old_helper)
                journal = {'state': 'prepared', 'old_id': config['server_id'], 'new_id': new_id,
                           'active_release': BASE, 'image': image, 'source_changed': False}
                private.put_json('chatgpt-runtime/journal.json', journal)
                private.put_json('helper-update.json', {'state': 'runtime_update_requires_admin_recovery'})
                started = time.monotonic()
                stopped = renamed_old = renamed_new = False
                try:
                    operator.docker(['stop', '--time', '60', config['server_id']], timeout=90)
                    stopped = True
                    op.require(not operator.inspect(config['server_id'])['State']['Running'], 'old_server_stop_unconfirmed')
                    runtime = json.loads(old_runtime)
                    runtime.update(image=IMAGE, extensions={'chatgpt_plan': {
                        'google_auth': '2.41.1', 'descriptor_sha256': expected_hash, 'image_id': image}})
                    store.put_json('runtime.json', runtime)
                    operator.docker(['rename', config['server_id'], OLD_NAME]); renamed_old = True
                    operator.docker(['rename', new_id, NAME]); renamed_new = True
                    operator.docker(['start', new_id])
                    # Never approve/fingerprint an unverified live connection.
                    check_replacement(original, operator.inspect(new_id), image)
                    reviewed.update(server_id=new_id, server_fingerprint=op.fingerprint(operator.inspect(new_id)),
                                    runtime_sha256=hashlib.sha256(store.read('runtime.json')).hexdigest())
                    new_operator = op.Operator(reviewed, private)
                    old_manifest, unused = operator.source(BASE)
                    new_operator.wait_ready(BASE, old_manifest)
                    install_session_entry(op, reviewed, bundle, descriptor)
                    config_bytes = (json.dumps(reviewed, sort_keys=True) + '\n').encode()
                    approval_index = reviewed_index(json.loads(old_index), op.CONFIG, config_bytes)
                    with op.Tree(str(Path(op.CONFIG).parent), owners=(0,), protected=True) as configuration:
                        configuration.write(Path(op.CONFIG).name, config_bytes)
                    private.put_json('install-backup-index.json', approval_index)
                    # Make outer fence validate the new, specifically approved server.
                    operator.config = reviewed
                    private.write('helper-update.json', old_helper)
                    journal.update(state='complete', http_ready_seconds=round(time.monotonic() - started, 3),
                                   database_container_unchanged=True, realtime_loss_verified=False)
                    private.put_json('chatgpt-runtime/journal.json', journal)
                except BaseException:
                    try:
                        operator.config = config
                        if renamed_new:
                            operator.docker(['stop', '--time', '60', new_id], timeout=90)
                            operator.docker(['rename', new_id, NEW_NAME])
                        store.write('runtime.json', old_runtime)
                        if renamed_old: operator.docker(['rename', config['server_id'], NAME])
                        if stopped: operator.docker(['start', config['server_id']])
                        old_manifest, unused = operator.source(BASE)
                        operator.wait_ready(BASE, old_manifest)
                        with op.Tree(str(Path(op.CONFIG).parent), owners=(0,), protected=True) as configuration:
                            configuration.write(Path(op.CONFIG).name, old_configuration)
                        private.write('install-backup-index.json', old_index)
                        private.write('helper-update.json', old_helper)
                        journal['state'] = 'rolled_back'
                        private.put_json('chatgpt-runtime/journal.json', journal)
                    except BaseException:
                        journal['state'] = 'needs_admin'
                        private.put_json('chatgpt-runtime/journal.json', journal)
                    raise
            # The outer identity/mount/control fence has now completed for the
            # reviewed runtime. Reuse this exact isolated storage gate at deploy.
            private.put_json('gates/' + descriptor['candidate_release'] + '.json', {
                'source_hash': content, 'profile': 'storage', 'passed': True,
                'post_job_fence_verified': True, 'runtime_image_id': image,
                'runtime_extension_sha256': expected_hash})
            return journal


def recover():
    sys.path.insert(0, '/usr/local/libexec/kiwoom-nas')
    import nas_operator as op
    op.require(os.geteuid() == 0, 'administrator_required')
    live_config = op.load_config()
    with op.Tree(live_config['private'], owners=(0,), protected=True) as private:
        with private.lock('operator.lock'):
            journal = private.json('chatgpt-runtime/journal.json')
            op.require(journal.get('state') in ('prepared', 'needs_admin'), 'runtime_recovery_not_pending')
            config = private.json('chatgpt-runtime/config.before.json')
            op.require(config['private'] == live_config['private'] and config['server_id'] == journal['old_id']
                       and journal['active_release'] == BASE, 'runtime_recovery_scope_changed')
            operator = op.Operator(config, private)
            old = operator.inspect(journal['old_id'])
            op.require(op.fingerprint_matches(old, config['server_fingerprint']), 'runtime_recovery_original_changed')
            new = operator.inspect(journal['new_id'])
            op.require(new['Name'] in ('/' + NAME, '/' + NEW_NAME), 'runtime_recovery_replacement_changed')
            if new['State']['Running']: operator.docker(['stop', '--time', '60', journal['new_id']], timeout=90)
            if new['Name'] == '/' + NAME: operator.docker(['rename', journal['new_id'], NEW_NAME])
            op.require(old['Name'] in ('/' + NAME, '/' + OLD_NAME), 'runtime_recovery_name_changed')
            if old['Name'] == '/' + OLD_NAME: operator.docker(['rename', journal['old_id'], NAME])
            with op.Tree(config['store'], owners=(0, config['allowed_uid'])) as store:
                op.require(store.json('active.json')['release_id'] == BASE, 'runtime_recovery_source_changed')
                store.write('runtime.json', private.read('chatgpt-runtime/runtime.before.json'))
                manifest, unused = operator.source(BASE)
                if not old['State']['Running']: operator.docker(['start', journal['old_id']])
                operator.wait_ready(BASE, manifest)
            with op.Tree(str(Path(op.CONFIG).parent), owners=(0,), protected=True) as configuration:
                configuration.write(Path(op.CONFIG).name, private.read('chatgpt-runtime/config.before.json'))
            private.write('install-backup-index.json', private.read('chatgpt-runtime/index.before.json'))
            private.write('helper-update.json', private.read('chatgpt-runtime/helper.before.json'))
            journal.update(state='rolled_back', database_container_unchanged=True, realtime_loss_verified=False)
            private.put_json('chatgpt-runtime/journal.json', journal)
            return {'state': 'rolled_back', 'source_changed': False, 'database_container_unchanged': True}


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2: raise RuntimeError('descriptor_hash_required')
        if sys.argv[1] == '--recover':
            print(json.dumps(recover(), sort_keys=True))
        else:
            if not re.fullmatch('[a-f0-9]{64}', sys.argv[1]): raise RuntimeError('descriptor_hash_required')
            print(json.dumps(install(Path(__file__).resolve().parent, sys.argv[1]), sort_keys=True))
    except Exception as error:
        print(json.dumps({'state': 'failed', 'error_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, RuntimeError) and re.fullmatch('[a-z_]{1,96}', str(error)) else 'runtime_extension_failed'}))
        raise SystemExit(1)
