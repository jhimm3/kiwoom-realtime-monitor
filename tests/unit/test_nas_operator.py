"""Portable contract and state-transition tests; Linux IO has its own gate."""
import argparse
import ast
import contextlib
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from scripts import nas_operator as op
from scripts import nas_operator_worker as worker


class MemoryTree:
    def __init__(self):
        self.documents = {}
        self.identity = (100, 200)
        self.fd = 7
        self.path = '/private'

    def json(self, name):
        if name not in self.documents:
            raise FileNotFoundError(name)
        return copy.deepcopy(self.documents[name])

    def put_json(self, name, value):
        self.documents[name] = copy.deepcopy(value)

    def verify_identity(self):
        pass

    @contextlib.contextmanager
    def parent(self, name, create=False):
        yield self.fd, name


class DeployOperator(op.Operator):
    def __init__(self, failure=None):
        self.private_tree = MemoryTree()
        super().__init__({'server_id': 'server-id', 'deploy_profiles': ['storage']}, self.private_tree)
        self.store = MemoryTree()
        self.store.put_json('active.json', {'format': 1, 'release_id': 'old'})
        self.private.put_json('gates/new.json', {'source_hash': 'hash-new', 'profile': 'storage', 'passed': True})
        self.actions = []
        self.running = True
        self.failure = failure

    def source(self, release):
        return {'release_id': release, 'contract': {'schema': 'same'}, 'server_build': release}, 'hash-' + release

    def publish(self, store, manifest):
        self.actions.append(('publish', manifest['release_id']))

    def docker(self, args, timeout=30, log=None):
        self.actions.append(tuple(args))
        if args[0] == 'stop':
            if self.failure == 'first_stop':
                self.failure = None
                raise op.Rejected('injected_stop')
            self.running = False
        elif args[0] == 'start':
            self.running = True
        return b''

    def inspect(self, container):
        return {'State': {'Running': self.running}}

    def wait_ready(self, release, manifest):
        self.actions.append(('ready', release))
        if release == 'new' and self.failure in ('ready', 'both_ready'):
            raise op.Rejected('injected_readiness')
        if release == 'old' and self.failure == 'both_ready':
            raise op.Rejected('injected_rollback')


def manifest():
    contract = {name: hashlib.sha256(name.encode()).hexdigest() for name in op.CONTRACT}
    files = {name: value for name, value in contract.items() if name != op.CONTRACT[1]}
    files['src/kiwoom_monitor/central_server/app.py'] = 'a' * 64
    files['scripts/test_runner.py'] = 'b' * 64
    content = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    result = {'format': 1, 'release_id': 'build-' + content[:16], 'server_build': 'build',
              'files': files, 'contract': contract,
              'src_hash': hashlib.sha256(json.dumps({k: v for k, v in files.items()
                                                    if k.startswith('src/')}, sort_keys=True).encode()).hexdigest()}
    return result, contract, content


def idle_snapshot(state='off'):
    trace = {'state': state}
    if state == 'complete':
        trace.update({key: 0 for key in ('queued', 'pending_events', 'copy_reserved_bytes', 'charged_bytes',
                                        'packing_events', 'packed_events')})
        trace.update(accepted=10, written=10, known_dropped=0, input_rejected=0, input_capture_censored=False)
    return {'diagnostic_tool': {'enabled': False}, 'trace_capture': {'enabled': False},
            'paused_workloads': [], 'active_runs': [], 'trace': trace}


class NasOperatorTests(unittest.TestCase):
    def test_identifiers_and_file_paths_reject_traversal_options_and_shell_text(self):
        for value in ('../other', '/etc/passwd', '-v', 'x;id', 'x\ny', 'x\\y', '', 'x' * 161):
            with self.subTest(value=value), self.assertRaises(op.Rejected):
                op.identifier(value)
        for value in ('src/../../etc/passwd', 'src//app.py', 'src\\app.py', '/src/app.py', 'src/./app.py'):
            with self.subTest(value=value), self.assertRaises(op.Rejected):
                op.source_name(value)
        with self.assertRaises(op.Rejected):
            op.source_name('.env')
        self.assertEqual('2026.10.08-example-a1b2', op.identifier('2026.10.08-example-a1b2'))

    def test_manifest_binds_contract_build_src_and_all_file_hashes(self):
        value, contract, content = manifest()
        self.assertEqual(content, op.validate_manifest(value, value['release_id'], contract))
        for key, replacement in (('release_id', 'another'), ('src_hash', '0' * 64),
                                 ('contract', {}), ('server_build', 'different')):
            changed = copy.deepcopy(value)
            changed[key] = replacement
            with self.subTest(key=key), self.assertRaises(op.Rejected):
                op.validate_manifest(changed, value['release_id'], contract)
        for name in ('.env', 'src/../outside', 'pyproject.toml'):
            changed = copy.deepcopy(value)
            changed['files'][name] = '0' * 64
            with self.subTest(name=name), self.assertRaises(op.Rejected):
                op.validate_manifest(changed, changed['release_id'], contract)

    def test_duplicate_json_keys_are_not_silently_overwritten(self):
        with self.assertRaisesRegex(op.Rejected, 'duplicate_json_key'):
            op.decode(b'{"format":1,"format":2}')

    def test_parser_exposes_no_arbitrary_command_mount_dsn_or_env(self):
        with contextlib.redirect_stderr(__import__('io').StringIO()):
            for arguments in (['test', 'r', '--mount', '/'], ['test', 'r', '--image', 'evil'],
                              ['replay', 'r', 't', '--dsn', 'postgresql://prod'], ['exec', 'sh']):
                with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                    op.parser().parse_args(arguments)

    def test_selected_test_names_and_replay_windows_fail_closed(self):
        config = {'profiles': ['selected', 'storage'], 'max_concurrency': 16}
        args = op.parser().parse_args(['test', 'r', '--profile', 'selected', '--test',
                                      'tests.integration.test_storage.Case.test_method'])
        op.validate_args(args, config)
        args.test = ['scripts.execute_admin']
        with self.assertRaises(op.Rejected):
            op.validate_args(args, config)
        replay = op.parser().parse_args(['replay', 'r', 't', '--baseline', 'a' * 64,
                                         '--window-start', '0', '--window-end', '60'])
        op.validate_args(replay, config)
        for key, invalid in (('window_end', float('nan')), ('window_start', -1), ('concurrency', 17),
                             ('include_workload', ['x;id']), ('baseline', '../../prod')):
            value = copy.deepcopy(replay)
            setattr(value, key, invalid)
            with self.subTest(key=key), self.assertRaises(op.Rejected):
                op.validate_args(value, config)
        replay.include_workload = replay.exclude_workload = ['news']
        with self.assertRaises(op.Rejected):
            op.validate_args(replay, config)

    def test_capture_fence_distinguishes_durable_idle_from_retained_ram(self):
        op.idle(idle_snapshot())
        op.idle(idle_snapshot('complete'))
        for state in ('running', 'awaiting_persistence', 'persisting', 'failed'):
            with self.subTest(state=state), self.assertRaises(op.Rejected):
                op.idle(idle_snapshot(state))
        for key in ('queued', 'pending_events', 'charged_bytes', 'copy_reserved_bytes', 'packing_events', 'packed_events'):
            value = idle_snapshot('complete')
            value['trace'][key] = 1
            with self.subTest(key=key), self.assertRaises(op.Rejected):
                op.idle(value)
        incomplete = idle_snapshot('complete')
        del incomplete['trace']['packed_events']
        with self.assertRaises(op.Rejected):
            op.idle(incomplete)
        # Rejected replay inputs do not imply unpersisted RAM after a durable end.
        completed = idle_snapshot('complete')
        completed['trace'].update(input_rejected=217, known_dropped=3, input_capture_censored=True)
        op.idle(completed)

    def test_master_pause_or_run_each_blocks_mutation(self):
        for key, value in (('diagnostic_tool', {'enabled': True}), ('trace_capture', {'enabled': True}),
                           ('paused_workloads', ['news']), ('active_runs', ['run'])):
            snapshot = idle_snapshot()
            snapshot[key] = value
            with self.subTest(key=key), self.assertRaises(op.Rejected):
                op.idle(snapshot)

    def test_docker_uses_fixed_socket_clean_environment_and_no_shell(self):
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, b'[]', b'')
        operator = op.Operator({'docker': '/verified/docker'}, None, run=run)
        with patch.dict('os.environ', {'DOCKER_HOST': 'tcp://evil', 'PYTHONPATH': '/evil'}):
            self.assertEqual(b'[]', operator.docker(['inspect', 'server-id']))
        command, options = calls[0]
        self.assertEqual(['/verified/docker', '--host', 'unix:///var/run/docker.sock', 'inspect', 'server-id'], command)
        self.assertNotIn('shell', options)
        self.assertEqual(op.CLEAN_ENV, options['env'])
        self.assertNotIn('PYTHONPATH', options['env'])

    def test_timeout_is_controlled_and_docker_errors_do_not_publish_stderr_secrets(self):
        def run(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        operator = op.Operator({'docker': '/docker'}, None, run=run)
        with self.assertRaisesRegex(op.Rejected, '^docker_command_timeout$'):
            operator.docker(['inspect', 'server-id'])
        operator.run_process = lambda *a, **k: subprocess.CompletedProcess(a[0], 1, b'', b'TOKEN=secret')
        with self.assertRaisesRegex(op.Rejected, '^docker_command_failed$'):
            operator.docker(['inspect', 'server-id'])

    def test_worker_has_no_live_mount_socket_secrets_or_host_network(self):
        config = {'worker_memory': 4 * 1024 ** 3, 'worker_cpus': 2, 'helper_dir': '/root/helper',
                  'runtime_image_id': 'sha256:fixed'}
        operator = op.Operator(config, MemoryTree())
        args = operator.worker_argv('a' * 32, 'pg', 'release')
        for required in ('65534:65534', '--read-only', '--cap-drop', 'ALL', 'no-new-privileges', 'container:pg'):
            self.assertIn(required, args)
        rendered = ' '.join(args)
        self.assertNotIn('docker.sock', rendered)
        self.assertNotIn('server-data', rendered)
        self.assertNotIn('server-secrets', rendered)
        self.assertNotIn('--privileged', args)
        self.assertEqual(['-I', '/opt/kiwoom-operator/nas_operator_worker.py'], args[-2:])
        self.assertEqual(str(config['worker_memory']), args[args.index('--memory') + 1])

    def test_cleanup_refuses_another_container_with_matching_name_but_wrong_label(self):
        operator = op.Operator({}, None)
        commands = []
        operator.docker = lambda args, **kw: commands.append(args) or b'other-id\n'
        operator.inspect = lambda cid: {'Name': '/kiwoom-op-worker-' + 'a' * 32,
                                       'Config': {'Labels': {op.LABEL: 'wrong'}}}
        with self.assertRaisesRegex(op.Rejected, 'job_cleanup_failed'):
            operator.cleanup_containers('a' * 32)
        self.assertFalse(any(command[0] == 'rm' for command in commands))

    def test_deploy_changes_only_fixed_server_and_preserves_previous_hash(self):
        operator = DeployOperator()
        with patch.object(op.os, 'mkdir'), patch.object(op.os, 'rmdir'):
            result = operator.deploy(operator.store, 'new')
        self.assertEqual('complete', result['state'])
        self.assertEqual('new', operator.store.json('active.json')['release_id'])
        self.assertEqual('hash-old', operator.journal()['previous_hash'])
        self.assertTrue(all(action[-1] == 'server-id' for action in operator.actions if action[0] in ('stop', 'start')))

    def test_failed_readiness_and_failed_first_stop_restore_previous_source(self):
        for failure in ('ready', 'first_stop'):
            operator = DeployOperator(failure)
            with self.subTest(failure=failure), patch.object(op.os, 'mkdir'), patch.object(op.os, 'rmdir'):
                with self.assertRaises(op.Rejected):
                    operator.deploy(operator.store, 'new')
            self.assertEqual('old', operator.store.json('active.json')['release_id'])
            self.assertEqual('rolled_back', operator.journal()['state'])
            self.assertTrue(operator.running)

    def test_failed_rollback_leaves_explicit_admin_recovery_state(self):
        operator = DeployOperator('both_ready')
        with patch.object(op.os, 'mkdir'), patch.object(op.os, 'rmdir'), self.assertRaises(op.Rejected):
            operator.deploy(operator.store, 'new')
        self.assertEqual('needs_admin', operator.journal()['state'])

    def test_rollback_requires_previous_exact_hash_but_no_new_gate_for_initial_source(self):
        operator = DeployOperator()
        with patch.object(op.os, 'mkdir'), patch.object(op.os, 'rmdir'):
            operator.deploy(operator.store, 'new')
            result = operator.deploy(operator.store, 'old', rollback=True)
        self.assertEqual('old', result['release_id'])
        operator = DeployOperator()
        operator.store.put_json('active.json', {'release_id': 'new'})
        operator.private.put_json('deployment.json', {'state': 'complete', 'previous': 'old', 'previous_hash': 'tampered'})
        with self.assertRaisesRegex(op.Rejected, 'verified_previous_release_required'):
            operator.deploy(operator.store, 'old', rollback=True)

    def test_no_gate_or_selected_only_gate_cannot_deploy(self):
        operator = DeployOperator()
        operator.private.documents['gates/new.json']['profile'] = 'selected'
        with self.assertRaisesRegex(op.Rejected, 'exact_source_gate_required'):
            operator.deploy(operator.store, 'new')
        self.assertEqual([], operator.actions)

    def test_recovery_rejects_changed_active_pointer_before_any_stop(self):
        operator = DeployOperator()
        operator.private.put_json('deployment.json', {'state': 'selected', 'previous': 'old', 'target': 'new',
                                                      'store_identity': list(operator.store.identity), 'previous_hash': 'hash-old'})
        operator.store.put_json('active.json', {'release_id': 'foreign'})
        with self.assertRaisesRegex(op.Rejected, 'recovery_pointer_changed'):
            operator.recover(operator.store)
        self.assertFalse(any(action[0] == 'stop' for action in operator.actions))

    def test_skip_only_worker_suite_fails(self):
        class OnlySkipped(unittest.TestCase):
            @unittest.skip('missing fixture')
            def test_missing(self):
                pass
        suite = unittest.TestSuite([OnlySkipped('test_missing')])
        with patch.object(unittest.defaultTestLoader, 'loadTestsFromName', return_value=suite), \
                contextlib.redirect_stderr(__import__('io').StringIO()):
            outcome = worker.run_tests(['fixture'])
        self.assertEqual('failed', outcome['state'])
        self.assertEqual(1, outcome['skipped'])

    def test_host_files_are_python38_compatible(self):
        root = Path(__file__).resolve().parents[2]
        for name in ('nas_operator.py', 'nas_operator_install.py'):
            with self.subTest(name=name):
                ast.parse((root / 'scripts' / name).read_text(encoding='utf-8'), feature_version=(3, 8))


if __name__ == '__main__':
    unittest.main()
