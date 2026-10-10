"""Portable contract and state-transition tests; Linux IO has its own gate."""
import argparse
import ast
import contextlib
import copy
import hashlib
import io
import itertools
import json
from pathlib import Path
import subprocess
import tempfile
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

    def __enter__(self):
        return self

    def __exit__(self, unused_type, unused_value, unused_traceback):
        return False

    def json(self, name):
        if name not in self.documents:
            raise FileNotFoundError(name)
        return copy.deepcopy(self.documents[name])

    def put_json(self, name, value):
        self.documents[name] = copy.deepcopy(value)

    def read(self, name, limit=None):
        value = self.documents[name]
        return value if isinstance(value, bytes) else json.dumps(value).encode()

    def write(self, name, value, mode=0o600):
        self.documents[name] = value

    def verify_identity(self):
        pass

    @contextlib.contextmanager
    def parent(self, name, create=False):
        yield self.fd, name


def scoped_trace_fixture():
    identifier = '20261008T000000Z-0123456789ab'
    row = {'event_type': 'input_rejected', 'seq': 1, 'mono_ns': 11,
           'workload_id': 'shadow', 'reason': 'payload_budget_exceeded'}
    data = (json.dumps(row) + '\n').encode()
    manifest = dict(trace_id=identifier, schema_version=3, state='incomplete', reason='expired',
        coverage='observed_paths_only', payload_capture={'store_inputs': True}, started_mono_ns=10,
        finished_mono_ns=20, input_capture_censored=False, known_dropped=0,
        accepted=1, written=1, last_seq=1, payload_accepted=0, input_rejected=1,
        input_rejected_reasons={'payload_budget_exceeded': 1}, drop_reasons={},
        queued=0, pending_events=0, copy_reserved_bytes=0, charged_bytes=0,
        bytes_written=len(data), blobs={}, chunks=[dict(name='000001.jsonl', count=1,
            first_seq=1, last_seq=1, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())])
    return identifier, manifest, {'manifest.json': json.dumps(manifest).encode(), '000001.jsonl': data}


class ScopedOperatorTests(unittest.TestCase):
    def test_capacity_worker_requires_real_headroom_full_window_and_durable_native_counts(self):
        # The administrator's operator gate has no app/capacity source tree.
        # Exercise the worker's acceptance bridge without importing app modules.
        from types import ModuleType, SimpleNamespace
        capacity = ModuleType('scripts.check_causal_capture_capacity')
        capacity.operator_capacity_options = lambda profile, work: SimpleNamespace(capture_seconds=60, messages=1200)
        result = dict(memory_preflight={
            'required_headroom_bytes': 9 * 1024**3, 'host_available_bytes': 10 * 1024**3,
            'container_headroom_bytes': 10 * 1024**3}, memory_preflight_exercised=True,
            actual_capture_wall_seconds=60, messages_completed=1200, trace={'accepted': 21},
            native_counts={'native_delivery_consumed': 12012, 'native_delivery_supported': 12000,
                           'native_delivery_uncovered': 12, 'mixed_rounds': 6},
            account_large_method_counts={'save_shadow_monitor_state': 2},
            native_store_method_counts={'save_query': 1},
            persistence=dict(events=21, state='complete', collector_messages=1206, closed_deliveries=12000,
                native_operation_method_counts={'save_shadow_monitor_state': 2, 'save_query': 1}))
        request = {'profile': 'recorder-capacity-smoke'}
        capacity.run = unittest.mock.AsyncMock(return_value=result)
        with patch.dict(op.sys.modules, {'scripts.check_causal_capture_capacity': capacity}):
            outcome = worker.recorder_capacity(request)
            self.assertEqual('60second_smoke', outcome['capacity_gate'])
            self.assertFalse(outcome['whole_app_performance_accepted'])
        for field, value, reason in (
                ('container_headroom_bytes', None, 'real_headroom'),
                ('actual_capture_wall_seconds', 59, 'real_window'),
                ('collector_messages', 1199, 'durable_count'),
                ('closed_deliveries', 11999, 'durable_count'),
                ('save_shadow_monitor_state', 1, 'native_input_pairs'),
                ('save_query', 0, 'native_store_pairs')):
            changed = copy.deepcopy(result)
            if field == 'container_headroom_bytes':
                changed['memory_preflight'][field] = value
            elif field == 'actual_capture_wall_seconds':
                changed[field] = value
            elif field in ('collector_messages', 'closed_deliveries'):
                changed['persistence'][field] = value
            else:
                changed['persistence']['native_operation_method_counts'][field] = value
            capacity.run = unittest.mock.AsyncMock(return_value=changed)
            with self.subTest(field=field), patch.dict(op.sys.modules, {'scripts.check_causal_capture_capacity': capacity}):
                with self.assertRaisesRegex(RuntimeError, reason):
                    worker.recorder_capacity(request)

    def test_worker_v3_forwards_source_clock_and_refuses_another_trace_before_sql(self):
        # The candidate CLI handles capsule/frontier/baseline verification. This
        # contract tests the fixed worker's version/trace/clock argument bridge.
        from types import ModuleType
        statements = b'["SELECT 1"]'
        baseline = dict(version=3, owner_token='a' * 32, source_state_equivalent=False,
            statements_sha256=hashlib.sha256(statements).hexdigest(),
            source_origin='2026-10-12T09:00:00+09:00', account_context={'trace_id': 'trace'})
        request = dict(trace='trace', baseline='b' * 64, window_start=0, window_end=1,
                       mode='recorded_operations', concurrency=2,
                       include_workload=[], exclude_workload=[], collector_component=[])
        def execute(args, output):
            output.write(json.dumps({'state': 'ok', 'result': {'state': 'complete'}}))
            self.assertEqual('3', args[args.index('--baseline-version') + 1])
            self.assertEqual(baseline['source_origin'], args[args.index('--source-origin') + 1])
            return 0
        package = ModuleType('kiwoom_monitor')
        central = ModuleType('kiwoom_monitor.central_server')
        cli = ModuleType('kiwoom_monitor.central_server.diagnostic_replay_database_cli')
        cli.main = execute
        central.diagnostic_replay_database_cli = cli
        package.central_server = central
        postgres = ModuleType('psycopg')
        postgres.connect = unittest.mock.MagicMock()
        with patch.dict(op.sys.modules, {'kiwoom_monitor': package, 'kiwoom_monitor.central_server': central,
                                        'kiwoom_monitor.central_server.diagnostic_replay_database_cli': cli,
                                        'psycopg': postgres}), \
             patch.object(worker.Path, 'read_text', return_value=json.dumps(baseline)), \
             patch.object(worker.Path, 'read_bytes', return_value=statements), \
             patch.dict(worker.os.environ):
            connect = postgres.connect
            self.assertEqual('passed', worker.replay(request, 'fixture-url')['state'])
            connect.assert_called_once_with('fixture-url')
            connect.reset_mock()
            with self.assertRaisesRegex(RuntimeError, 'baseline_account_trace_mismatch'):
                worker.replay({**request, 'trace': 'other'}, 'fixture-url')
            connect.assert_not_called()

    def block_fixture(self):
        trace_id, manifest, files = scoped_trace_fixture()
        blocks = [b'a' * 1024, b'b' * 1024]
        digest = hashlib.sha256(b''.join(blocks)).hexdigest()
        name = 'block-' + digest + '-000.payloads'
        parts = [dict(index=index, bytes=len(value), sha256=hashlib.sha256(value).hexdigest(),
                      name=name, offset=index * 1024) for index, value in enumerate(blocks)]
        manifest.update(schema_version=4, blobs={digest: dict(format='blocks/v1', bytes=2048, parts=parts)})
        files.update({'manifest.json': json.dumps(manifest).encode(), name: b''.join(blocks)})
        return trace_id, manifest, files, digest, name

    def test_block_registration_copies_physical_bundle_and_rechecks_registered_bytes(self):
        trace_id, manifest, files, digest, name = self.block_fixture()
        incoming, private = MemoryTree(), MemoryTree()
        incoming.documents.update(files)
        operator = op.Operator({'trace_dir': '/incoming', 'allowed_uid': 1000,
            'input_file_limit': 64 * 1024 ** 2, 'input_total_limit': 1024 ** 3}, private)
        with patch.object(op, 'Tree', return_value=incoming):
            result = operator.register('traces', trace_id, 'scoped-operations')
            self.assertIn(name, result['files'])
            self.assertNotIn('payload-' + digest + '.json', result['files'])
            self.assertEqual(result, operator.registered_input('traces', trace_id))
            private.documents['traces/' + trace_id + '/' + name] = b'changed'
            with self.assertRaisesRegex(op.Rejected, 'registered_input_changed'):
                operator.registered_input('traces', trace_id)

    def test_block_corruption_order_offsets_paths_and_root_hash_reject_before_publication(self):
        mutations = ('bytes', 'order', 'offset', 'path', 'index', 'hash', 'trailing', 'schema', 'bool')
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                trace_id, manifest, files, digest, name = self.block_fixture()
                root = manifest['blobs'][digest]
                if mutation == 'bytes':
                    files[name] = b'!' + files[name][1:]
                elif mutation == 'order':
                    root['parts'].reverse()
                elif mutation == 'offset':
                    root['parts'][1]['offset'] = 1023
                elif mutation == 'path':
                    root['parts'][0]['name'] = '../outside'
                elif mutation == 'index':
                    root['parts'][0]['index'] = 1
                elif mutation == 'hash':
                    manifest['blobs'] = {'c' * 64: root}
                elif mutation == 'trailing':
                    files[name] += b'!'
                elif mutation == 'schema':
                    manifest['schema_version'] = 3
                else:
                    root['parts'][0]['bytes'] = True
                files['manifest.json'] = json.dumps(manifest).encode()
                incoming, private = MemoryTree(), MemoryTree()
                incoming.documents.update(files)
                operator = op.Operator({'trace_dir': '/incoming', 'allowed_uid': 1000,
                    'input_file_limit': 64 * 1024 ** 2, 'input_total_limit': 1024 ** 3}, private)
                with patch.object(op, 'Tree', return_value=incoming), self.assertRaises(op.Rejected):
                    operator.register('traces', trace_id, 'scoped-operations')
                self.assertEqual({}, private.documents)

    def test_v3_baseline_registration_pins_context_and_requires_aware_clock(self):
        input_id = 'a' * 64
        statements = b'["SELECT 1"]'
        manifest = dict(baseline_id=input_id, database='kiwoom_monitor_replay_test', version=3,
            source_state_equivalent=False, owner_token='f' * 32,
            statements_sha256=hashlib.sha256(statements).hexdigest(), source_origin='2026-10-12T09:00:00+09:00',
            account_context=dict(version='account-context/v1', trace_id='trace', sha256='b' * 64,
                                 owner_bindings_sha256='c' * 64, source_state_equivalent=False))
        for mutation in ('valid', 'missing', 'naive', 'hash', 'equivalence', 'version'):
            with self.subTest(mutation=mutation):
                value = copy.deepcopy(manifest)
                if mutation == 'missing':
                    value.pop('account_context')
                elif mutation == 'naive':
                    value['source_origin'] = '2026-10-12T09:00:00'
                elif mutation == 'hash':
                    value['account_context']['sha256'] = 'invalid'
                elif mutation == 'equivalence':
                    value['source_state_equivalent'] = True
                elif mutation == 'version':
                    value['version'] = True
                incoming, private = MemoryTree(), MemoryTree()
                incoming.documents.update({'baseline.json': value, 'statements.json': statements})
                operator = op.Operator({'project': '/project', 'allowed_uid': 1000,
                    'input_file_limit': 64 * 1024 ** 2, 'input_total_limit': 1024 ** 3}, private)
                with patch.object(op, 'Tree', return_value=incoming):
                    if mutation == 'valid':
                        result = operator.register('baselines', input_id)
                        self.assertFalse(result['source_state_equivalent'])
                    else:
                        with self.assertRaises(op.Rejected):
                            operator.register('baselines', input_id)
                        self.assertEqual({}, private.documents)

    def test_profile_preflight_and_partial_policy_require_explicit_safe_arguments(self):
        config = {'max_concurrency': 16}
        base = ['replay', 'candidate', 'trace', '--baseline-profile', 'empty-v1',
                '--window-start', '0', '--window-end', '120', '--capture-policy', 'scoped-operations',
                '--include-workload', 'realtime']
        args = op.parser().parse_args(base + ['--preflight-only'])
        op.validate_args(args, config)
        self.assertFalse(args.pause_operational)
        for extras in ([], ['--preflight-only', '--pause-operational'],
                       ['--preflight-only', '--mode', 'collector_with_background'],
                       ['--preflight-only', '--collector-component', 'collector'],
                       ['--expected-baseline-id', 'wrong']):
            with self.subTest(extras=extras), self.assertRaises(op.Rejected):
                op.validate_args(op.parser().parse_args(base + extras), config)
        op.validate_args(op.parser().parse_args(base + ['--expected-baseline-id', 'a' * 64,
                                                       '--pause-operational']), config)
        partial = [value if value != 'scoped-operations' else 'partial-operations' for value in base]
        op.validate_args(op.parser().parse_args(partial + ['--preflight-only']), config)
        with self.assertRaises(op.Rejected):
            op.validate_args(op.parser().parse_args(partial + ['--preflight-only', '--mode', 'collector_with_background']), config)

    def test_incomplete_registration_keeps_original_files_and_requires_matching_policy(self):
        trace_id, manifest, files = scoped_trace_fixture()
        incoming = MemoryTree()
        incoming.documents.update(files)
        private = MemoryTree()
        operator = op.Operator({'trace_dir': '/incoming', 'allowed_uid': 1000,
                                'input_file_limit': 64 * 1024 ** 2, 'input_total_limit': 1024 ** 3}, private)
        with patch.object(op, 'Tree', return_value=incoming):
            with self.assertRaisesRegex(op.Rejected, 'trace_not_complete'):
                operator.register('traces', trace_id)
            result = operator.register('traces', trace_id, 'scoped-operations')
            self.assertEqual('incomplete', result['original_capture_state'])
            self.assertEqual('scoped-operations', result['capture_policy'])
            self.assertEqual(incoming.read('manifest.json'), private.read('traces/' + trace_id + '/manifest.json'))
            self.assertEqual(result, operator.register('traces', trace_id, 'scoped-operations'))
            self.assertEqual(hashlib.sha256(incoming.read('manifest.json')).hexdigest(), result['source_manifest_sha256'])

    def test_scoped_admission_rejects_retained_tail_censored_and_inconsistent_counts(self):
        trace_id, manifest, _ = scoped_trace_fixture()
        op.validate_scoped_trace_manifest(manifest, trace_id)
        for fields in ({'queued': 1}, {'input_capture_censored': True}, {'reason': 'disk_failed'},
                       {'known_dropped': 1}, {'accepted': 2}, {'input_rejected': 0},
                       {'unknown_tail_loss': True}, {'payload_capture': None}):
            with self.subTest(fields=fields), self.assertRaises(op.Rejected):
                op.validate_scoped_trace_manifest({**manifest, **fields}, trace_id)


class DeployOperator(op.Operator):
    def __init__(self, failure=None):
        self.private_tree = MemoryTree()
        super().__init__({'server_id': 'server-id', 'deploy_profiles': ['storage']}, self.private_tree)
        self.store = MemoryTree()
        self.store.put_json('active.json', {'format': 1, 'release_id': 'old'})
        self.private.put_json('gates/new.json', {'source_hash': 'hash-new', 'profile': 'storage',
                                               'passed': True, 'post_job_fence_verified': True})
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


class ReplayMaintenanceOperator(op.Operator):
    def __init__(self, failure=None):
        self.private_tree = MemoryTree()
        super().__init__({'server_id': 'server-id', 'database_id': 'database-id',
                          'server_fingerprint': 'approved-server', 'allowed_uid': 1000,
                          'store': '/store', 'contract': {}}, self.private_tree)
        self.store = MemoryTree()
        self.store.put_json('active.json', {'format': 1, 'release_id': 'release-a'})
        self.running = True
        self.database_running = True
        self.failure = failure
        self.actions = []

    def source(self, release):
        return {'release_id': release, 'server_build': 'build-a'}, 'source-hash-a'

    def active_release(self):
        return self.store.json('active.json')['release_id']

    def inspect(self, container):
        if container == 'server-id':
            return {'State': {'Running': self.running}}
        if container == 'database-id':
            return {'State': {'Running': self.database_running}}
        raise AssertionError(container)

    def snapshot(self):
        return dict(idle_snapshot(), health='ok', server_build='build-a',
                    source_path='/app/source-runtime/releases/release-a/src')

    def identities(self, running=True):
        self.assertions = getattr(self, 'assertions', [])
        self.assertions.append(running)
        if running:
            assert self.running
        assert self.database_running

    def docker(self, args, timeout=30, log=None):
        self.actions.append(tuple(args))
        if args[0] == 'stop':
            if self.failure == 'stop_ack_lost_after_stop':
                self.running = False
                raise op.Rejected('injected_stop_ack_loss')
            if self.failure == 'stop_ack_lost_running':
                raise op.Rejected('injected_stop_ack_loss')
            self.running = False
        elif args[0] == 'start':
            if self.failure == 'start':
                raise op.Rejected('injected_start_failure')
            self.running = True
        return b''

    def wait_ready(self, release, manifest):
        self.actions.append(('ready', release))
        if self.failure == 'ready':
            raise op.Rejected('injected_readiness_failure')


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
    if state in ('complete', 'incomplete'):
        trace.update({key: 0 for key in ('queued', 'pending_events', 'copy_reserved_bytes', 'charged_bytes',
                                        'packing_events', 'packed_events')})
        trace.update(accepted=10, written=10, known_dropped=0, input_rejected=0, input_capture_censored=False)
    return {'diagnostic_tool': {'enabled': False}, 'trace_capture': {'enabled': False},
            'paused_workloads': [], 'active_runs': [], 'trace': trace}


class NasOperatorTests(unittest.TestCase):
    @staticmethod
    def identity_fixture():
        return {'Id': '0' * 64, 'Image': 'sha256:fixture',
                'Config': {'Env': ['FIXTURE_KEY=fixture'], 'Labels': {'owner': 'monitor'}},
                'HostConfig': {'Privileged': False, 'Binds': ['source:/source:ro', 'data:/data:rw']},
                'Mounts': [{'Type': 'bind', 'Source': '/host/data', 'Destination': '/data', 'RW': True},
                           {'Type': 'bind', 'Source': '/host/source', 'Destination': '/source', 'RW': False},
                           {'Type': 'bind', 'Source': '/host/secrets', 'Destination': '/secrets', 'RW': False}],
                'NetworkSettings': {'Networks': {'monitor': {'NetworkID': 'network-id'}}},
                'State': {'Running': True}}

    @staticmethod
    def legacy_identity(value):
        # The exact previously installed approval contract, before normalization.
        profile = {key: value[key] for key in ('Id', 'Image', 'Config', 'HostConfig', 'Mounts')}
        profile['networks'] = {name: item['NetworkID'] for name, item in
                               value['NetworkSettings']['Networks'].items()}
        return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()

    def test_mount_order_is_stable_for_new_and_existing_approvals_without_input_mutation(self):
        original = self.identity_fixture()
        current = op.fingerprint(original)
        previous = self.legacy_identity(original)
        for order in itertools.permutations(original['Mounts']):
            value = copy.deepcopy(original)
            value['Mounts'] = list(order)
            before = copy.deepcopy(value)
            with self.subTest(order=[item['Destination'] for item in order]):
                self.assertEqual(current, op.fingerprint(value))
                self.assertTrue(op.fingerprint_matches(value, current))
                self.assertTrue(op.fingerprint_matches(value, previous))
                self.assertEqual(before, value)

    def test_mount_order_compatibility_still_rejects_real_container_profile_changes(self):
        original = self.identity_fixture()
        approvals = (op.fingerprint(original), self.legacy_identity(original))
        changes = {
            'container': lambda v: v.update(Id='1' * 64),
            'image': lambda v: v.update(Image='sha256:changed'),
            'environment': lambda v: v['Config'].update(Env=['FIXTURE_KEY=changed']),
            'privileged': lambda v: v['HostConfig'].update(Privileged=True),
            'bind_order': lambda v: v['HostConfig']['Binds'].reverse(),
            'mount_source': lambda v: v['Mounts'][0].update(Source='/other/data'),
            'mount_access': lambda v: v['Mounts'][0].update(RW=False),
            'mount_destination': lambda v: v['Mounts'][0].update(Destination='/other'),
            'mount_removed': lambda v: v['Mounts'].pop(),
            'mount_added': lambda v: v['Mounts'].append(copy.deepcopy(v['Mounts'][0])),
            'network': lambda v: v['NetworkSettings']['Networks']['monitor'].update(NetworkID='other-network'),
        }
        for name, mutate in changes.items():
            value = copy.deepcopy(original)
            mutate(value)
            value['Mounts'].reverse()
            for approved in approvals:
                with self.subTest(change=name, approved=approved):
                    self.assertFalse(op.fingerprint_matches(value, approved))

    def test_legacy_identity_is_accepted_at_operator_boundary_without_config_changes(self):
        original = self.identity_fixture()
        config = {'server_id': original['Id'], 'database_id': original['Id'],
                  'server_fingerprint': self.legacy_identity(original),
                  'database_fingerprint': self.legacy_identity(original)}
        before = copy.deepcopy(config)
        operator = op.Operator(config, None)
        reversed_value = copy.deepcopy(original)
        reversed_value['Mounts'].reverse()
        with patch.object(operator, 'inspect', return_value=reversed_value):
            operator.identities()
        self.assertEqual(before, config)
        changed = copy.deepcopy(reversed_value)
        changed['Mounts'][0]['RW'] = True
        with patch.object(operator, 'inspect', return_value=changed), \
                self.assertRaisesRegex(op.Rejected, 'approved_container_changed'):
            operator.identities()
        self.assertEqual('server', operator.identity_mismatch['kind'])

    def test_legacy_mount_search_is_bounded_and_same_order_still_matches(self):
        value = self.identity_fixture()
        value['Mounts'] = [{'Destination': '/mount-' + str(n), 'RW': False} for n in reversed(range(8))]
        previous = self.legacy_identity(value)
        reordered = copy.deepcopy(value)
        reordered['Mounts'][0], reordered['Mounts'][1] = reordered['Mounts'][1], reordered['Mounts'][0]
        with patch.object(op.itertools, 'permutations', side_effect=AssertionError('unbounded legacy search')):
            self.assertTrue(op.fingerprint_matches(value, previous))
            self.assertFalse(op.fingerprint_matches(reordered, previous))

    def test_fixed_sudoers_rule_rejects_arbitrary_users_and_policy_text(self):
        self.assertEqual(b'k379 ALL=(root) NOPASSWD: /usr/local/sbin/kiwoom-nas-root\n',
                         op.sudoers_rule('k379'))
        for user in ('root ALL', 'k379\nroot', 'ALL', 'k379:other', '*', None):
            with self.subTest(user=user), self.assertRaises(op.Rejected):
                op.sudoers_rule(user)

    def test_native_policy_requires_exact_loaded_rule_and_no_parser_warnings(self):
        config = {'sudoers_validation': 'native_fixed_rule', 'sudo': '/verified/sudo', 'allowed_user': 'k379'}
        baseline = b'User k379 may run the following commands:\n    (ALL) ALL\n'
        installed = baseline + b'    (root) NOPASSWD: /usr/local/sbin/kiwoom-nas-root\n'
        cases = [(baseline, b'', 0, False, True), (installed, b'', 0, True, True),
                 (installed, b'', 0, False, False), (baseline, b'', 0, True, False),
                 (installed, b'sudoers: syntax error', 0, True, False),
                 (installed, b'', 1, True, False), (b'', b'', 0, False, False),
                 (installed.replace(b'NOPASSWD:', b'PASSWD:'), b'', 0, True, False),
                 (installed.replace(b'(root)', b'(ALL)'), b'', 0, True, False),
                 (installed.rstrip() + b', /bin/sh\n', b'', 0, True, False),
                 (installed + installed, b'', 0, True, False)]
        for output, stderr, code, present, allowed in cases:
            with self.subTest(output=output, stderr=stderr, code=code, present=present), \
                    patch.object(op.subprocess, 'run', return_value=subprocess.CompletedProcess([], code, output, stderr)) as run:
                if allowed:
                    self.assertEqual('native_fixed_rule', op.validate_sudo_policy(config, present))
                else:
                    with self.assertRaises(op.Rejected):
                        op.validate_sudo_policy(config, present)
                self.assertEqual(['/verified/sudo', '-n', '-l', '-U', 'k379'], run.call_args.args[0])
                self.assertEqual(op.CLEAN_ENV, run.call_args.kwargs['env'])
                self.assertEqual(subprocess.DEVNULL, run.call_args.kwargs['stdin'])

    def test_policy_timeout_or_unknown_validator_never_passes(self):
        config = {'sudoers_validation': 'native_fixed_rule', 'sudo': '/verified/sudo', 'allowed_user': 'k379'}
        with patch.object(op.subprocess, 'run', side_effect=subprocess.TimeoutExpired('sudo', 15)), \
                self.assertRaisesRegex(op.Rejected, 'sudoers_validation_timeout_after_revocation'):
            op.validate_sudo_policy(config, False, suffix='_after_revocation')
        with self.assertRaisesRegex(op.Rejected, 'invalid_sudoers_validator'):
            op.validate_sudo_policy({'sudoers_validation': 'skip'}, False)

    def test_revoke_has_no_path_or_shell_arguments_and_cannot_restore_sudo_access(self):
        self.assertEqual('revoke', op.parser().parse_args(['revoke']).command)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            op.parser().parse_args(['revoke', '--path', '/etc/other'])
        with self.assertRaisesRegex(op.Rejected, 'sudo_access_must_not_be_restored'):
            op.restore_access_file(op.SUDOERS, (b'old sudo rule', 0o440))
        with self.assertRaisesRegex(op.Rejected, 'access_path_outside_scope'):
            op.restore_access_file('/etc/other', None)

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

    def test_replay_pause_option_is_explicit_and_off_by_default(self):
        base = ['replay', 'r', 't', '--baseline', 'a' * 64, '--window-start', '0', '--window-end', '60']
        self.assertFalse(op.parser().parse_args(base).pause_operational)
        self.assertTrue(op.parser().parse_args(base + ['--pause-operational']).pause_operational)

    def test_capacity_profiles_require_pause_and_reject_arbitrary_tests_or_memory(self):
        config = {'profiles': ['selected', 'storage'], 'max_concurrency': 16}
        for profile in op.CAPACITY_PROFILES:
            args = op.parser().parse_args(['test', 'candidate', '--profile', profile])
            with self.assertRaisesRegex(op.Rejected, 'capacity_requires_operational_pause'):
                op.validate_args(args, config)
            args.pause_operational = True
            op.validate_args(args, config)
            args.test = ['tests.unit.arbitrary_work']
            with self.assertRaisesRegex(op.Rejected, 'profile_does_not_accept_test_names'):
                op.validate_args(args, config)
        args = op.parser().parse_args(['test', 'candidate', '--profile', 'storage', '--pause-operational'])
        with self.assertRaisesRegex(op.Rejected, 'test_pause_profile_invalid'):
            op.validate_args(args, config)
        with contextlib.redirect_stderr(__import__('io').StringIO()), self.assertRaises(SystemExit):
            op.parser().parse_args(['test', 'candidate', '--profile', 'recorder-capacity', '--memory', '64g'])

    def test_capacity_resources_are_fixed_and_do_not_mutate_installed_config(self):
        config = dict(worker_memory=4 * 1024**3, minimum_disk_free=4 * 1024**3, job_timeout=7200)
        original = dict(config)
        for profile in op.CAPACITY_PROFILES:
            args = op.parser().parse_args(['test', 'r', '--profile', profile, '--pause-operational'])
            self.assertEqual(dict(profile='recorder-capacity', worker_memory=12 * 1024**3,
                minimum_disk_free=12 * 1024**3, job_timeout=14400), op.job_resources(args, config))
        args = op.parser().parse_args(['test', 'r', '--profile', 'selected', '--test', 'tests.unit.x'])
        self.assertEqual(4 * 1024**3, op.job_resources(args, config)['worker_memory'])
        self.assertEqual(original, config)

    def test_capacity_resource_rejection_happens_before_creating_or_stopping_containers(self):
        from types import SimpleNamespace
        operator = ReplayMaintenanceOperator()
        operator.config.update(worker_memory=4 * 1024**3, pg_memory=768 * 1024**2,
            minimum_disk_free=4 * 1024**3, worker_cpuset='0,1', pg_cpuset='0', job_timeout=7200)
        args = op.parser().parse_args(['test', 'candidate', '--profile', 'recorder-capacity', '--pause-operational'])
        meminfo = unittest.mock.Mock()
        meminfo.read_text.return_value = 'MemAvailable: 10000000 kB\n'
        with patch.object(op.os, 'sched_getaffinity', return_value={0, 1}, create=True), \
             patch.object(op, 'Path', return_value=meminfo), \
             patch.object(op.shutil, 'disk_usage', return_value=SimpleNamespace(free=20 * 1024**3)):
            with self.assertRaisesRegex(op.Rejected, 'insufficient_memory_headroom'):
                operator.run_job(args, {}, 'hash')
        self.assertEqual([], operator.actions)
        self.assertTrue(operator.running)
        with patch.object(op.os, 'sched_getaffinity', return_value={0, 1}, create=True), \
             patch.object(op.shutil, 'disk_usage', return_value=SimpleNamespace(free=5 * 1024**3)):
            with self.assertRaisesRegex(op.Rejected, 'insufficient_disk_headroom'):
                operator.run_job(args, {}, 'hash')
        self.assertEqual([], operator.actions)

    def test_replay_pause_resumes_exact_server_and_never_touches_database(self):
        operator = ReplayMaintenanceOperator()
        job_id = 'a' * 32
        operator.private.put_json('job.json', {'state': 'running', 'job_id': job_id, 'command': 'replay'})
        with patch.object(op, 'fingerprint', return_value='approved-server'), \
                patch.object(op, 'Tree', return_value=operator.store):
            paused = operator.pause_operational_server(job_id, 'release-a')
            self.assertFalse(operator.running)
            self.assertEqual('paused', paused['state'])
            self.assertEqual([], [x for x in operator.actions if x[-1] == 'database-id'])
            result = operator.resume_operational_server()
        self.assertTrue(operator.running)
        self.assertTrue(operator.database_running)
        self.assertEqual('complete', result['state'])
        self.assertTrue(result['collection_gap_expected'])
        self.assertFalse(result['realtime_loss_verified'])
        self.assertEqual([('stop', '--time', '60', 'server-id'), ('start', 'server-id'),
                          ('ready', 'release-a')], operator.actions)

    def test_lost_stop_ack_is_reconciled_by_inspection_then_resumed(self):
        operator = ReplayMaintenanceOperator('stop_ack_lost_after_stop')
        operator.private.put_json('job.json', {'state': 'running', 'job_id': 'b' * 32, 'command': 'replay'})
        with patch.object(op, 'fingerprint', return_value='approved-server'), \
                patch.object(op, 'Tree', return_value=operator.store):
            with self.assertRaisesRegex(op.Rejected, 'injected_stop_ack_loss'):
                operator.pause_operational_server('b' * 32, 'release-a')
            self.assertEqual('paused', operator.operational_journal()['state'])
            result = operator.resume_operational_server()
        self.assertEqual('complete', result['state'])
        self.assertTrue(operator.running)

    def test_recovery_cleans_replay_containers_before_resuming_server(self):
        operator = ReplayMaintenanceOperator()
        job_id = 'e' * 32
        operator.private.put_json('job.json', {'state': 'resume_pending', 'job_id': job_id,
                                               'command': 'replay'})
        operator.private.put_json('replay-maintenance.json', {'state': 'awaiting_job_cleanup',
            'job_id': job_id, 'active_release': 'release-a', 'server_build': 'build-a',
            'server_fingerprint': 'approved-server', 'server_id': 'server-id', 'was_running': True})
        order = []
        operator.cleanup_job = lambda unused_job: order.append('cleanup')
        operator.resume_operational_server = lambda: order.append('resume') or {'state': 'complete'}
        result = operator.recover(operator.store)
        self.assertEqual(['cleanup', 'resume'], order)
        self.assertEqual('recovered', result['state'])

    def test_recovery_cleanup_failure_does_not_resume_operational_server(self):
        operator = ReplayMaintenanceOperator()
        job_id = 'f' * 32
        operator.private.put_json('job.json', {'state': 'resume_pending', 'job_id': job_id,
                                               'command': 'replay'})
        operator.private.put_json('replay-maintenance.json', {'state': 'awaiting_job_cleanup',
            'job_id': job_id, 'active_release': 'release-a', 'server_build': 'build-a',
            'server_fingerprint': 'approved-server', 'server_id': 'server-id', 'was_running': True})
        operator.cleanup_job = lambda unused_job: (_ for _ in ()).throw(op.Rejected('cleanup_failed'))
        resumed = []
        operator.resume_operational_server = lambda: resumed.append(True)
        with self.assertRaisesRegex(op.Rejected, 'cleanup_failed'):
            operator.recover(operator.store)
        self.assertEqual([], resumed)

    def test_resume_fails_closed_on_release_change_and_retains_recovery_state(self):
        operator = ReplayMaintenanceOperator()
        operator.private.put_json('replay-maintenance.json', {
            'state': 'paused', 'job_id': 'c' * 32, 'server_id': 'server-id',
            'server_fingerprint': 'approved-server', 'active_release': 'release-a',
            'server_build': 'build-a', 'source_hash': 'source-hash-a', 'was_running': True})
        operator.store.put_json('active.json', {'release_id': 'release-b'})
        operator.running = False
        with patch.object(op, 'fingerprint', return_value='approved-server'):
            with self.assertRaisesRegex(op.Rejected, 'active_release_changed_during_replay'):
                operator.resume_operational_server()
        self.assertFalse(operator.running)
        self.assertEqual('paused', operator.operational_journal()['state'])
        self.assertEqual([], operator.actions)

    def test_resume_readiness_failure_is_durable_and_retryable(self):
        operator = ReplayMaintenanceOperator('ready')
        operator.private.put_json('replay-maintenance.json', {
            'state': 'paused', 'job_id': 'd' * 32, 'server_id': 'server-id',
            'server_fingerprint': 'approved-server', 'active_release': 'release-a',
            'server_build': 'build-a', 'source_hash': 'source-hash-a', 'was_running': True})
        with patch.object(op, 'fingerprint', return_value='approved-server'), \
                patch.object(op, 'Tree', return_value=operator.store):
            with self.assertRaisesRegex(op.Rejected, 'injected_readiness_failure'):
                operator.resume_operational_server()
            self.assertEqual('resuming', operator.operational_journal()['state'])
            operator.failure = None
            result = operator.resume_operational_server()
        self.assertEqual('complete', result['state'])
        self.assertTrue(operator.running)

    def test_replay_job_drains_temporary_containers_before_resume_on_success_and_failure(self):
        cases = [(failure, False) for failure in (None, 'data_owner', 'data_mode', 'postgres', 'worker', 'cleanup', 'resume')]
        cases += [(failure, True) for failure in (None, 'postgres', 'worker', 'cleanup', 'resume')]
        for failure, capacity in cases:
            with self.subTest(failure=failure, capacity=capacity), tempfile.TemporaryDirectory() as directory:
                operator = ReplayMaintenanceOperator()
                operator.private.path = directory
                operator.config.update(worker_memory=256 * 1024 ** 2, pg_memory=128 * 1024 ** 2,
                                       minimum_disk_free=1, pg_cpuset='0', worker_cpuset='0,1',
                                       pg_image_id='pg', runtime_image_id='runtime', job_timeout=10)
                args = op.parser().parse_args(['replay', 'candidate', 'trace', '--baseline', 'a' * 64,
                                             '--window-start', '0', '--window-end', '1',
                                             '--pause-operational'])
                if capacity:
                    args = op.parser().parse_args(['test', 'candidate', '--profile', 'recorder-capacity',
                                                  '--pause-operational'])
                output = MemoryTree()
                output.put_json('result.json', {'state': 'passed', 'memory_limit_bytes': op.CAPACITY_WORKER_MEMORY if capacity else 1024,
                                              'cpu_affinity': '0,1'})
                original_docker, original_inspect = operator.docker, operator.inspect

                def docker(argv, timeout=30, log=None, capture_stderr=False):
                    if argv[0] == 'stop' or (argv[0] == 'start' and argv[-1] == 'server-id'):
                        if failure == 'resume' and argv[0] == 'start':
                            raise op.Rejected('injected_resume')
                        return original_docker(argv, timeout, log)
                    operator.actions.append(tuple(argv))
                    if failure == 'postgres' and argv[0] == 'exec' and 'pg_isready' in argv:
                        raise op.Rejected('docker_command_failed')
                    if argv[0] == 'logs':
                        return b'initdb: error: Permission denied TOKEN=private\n'
                    if argv[:2] == ['start', '-a']:
                        self.assertEqual(14400 if capacity else 10, timeout)
                        self.assertFalse(operator.running)
                        self.assertTrue(operator.database_running)
                        if failure == 'worker':
                            raise op.Rejected('injected_worker')
                    if argv[0] == 'exec' and argv[-1].startswith('if ['):
                        return str(operator.config['pg_memory']).encode()
                    if argv[0] == 'exec' and argv[-1].startswith('while IFS'):
                        return b'0'
                    return b'true' if argv[0] == 'info' else b''

                def inspect(name):
                    if name in ('server-id', 'database-id'):
                        return original_inspect(name)
                    job_id = name.rsplit('-', 1)[-1]
                    limit = (op.CAPACITY_WORKER_MEMORY if 'worker' in name else operator.config['pg_memory']) if capacity else 1024
                    return {'State': {'ExitCode': 0, 'Running': True},
                            'HostConfig': {'Memory': limit, 'Privileged': False, 'CpusetCpus': '0,1'},
                            'Config': {'Labels': {op.LABEL: job_id}}}

                def cleanup(job_id):
                    operator.actions.append(('cleanup', job_id))
                    if failure == 'cleanup':
                        raise op.Rejected('injected_cleanup')

                operator.docker, operator.inspect, operator.cleanup_job = docker, inspect, cleanup
                operator.registered_input = lambda *unused: {'source_state_equivalent': False}
                operator.worker_argv = lambda job, *unused, **options: ['create', 'kiwoom-op-worker-' + job]
                real_path = Path
                meminfo = unittest.mock.Mock()
                meminfo.read_text.return_value = 'MemAvailable: 20000000 kB\n'
                from types import SimpleNamespace
                data_stats = [SimpleNamespace(st_uid=70 if failure == 'data_owner' else 0,
                                              st_mode=0o40700),
                              SimpleNamespace(st_uid=0, st_mode=0o40700 if failure == 'data_mode' else 0o40711)]
                with patch.object(op.os, 'sched_getaffinity', return_value={0, 1}, create=True), \
                        patch.object(op.shutil, 'disk_usage', return_value=SimpleNamespace(free=20 * 1024**3)), \
                        patch.object(op.os, 'chown', create=True), \
                        patch.object(op.os, 'O_DIRECTORY', 0x10000, create=True), \
                        patch.object(op.os, 'O_NOFOLLOW', 0x20000, create=True), \
                        patch.object(op.os, 'open', return_value=707) as data_open, \
                        patch.object(op.os, 'fstat', side_effect=data_stats), \
                        patch.object(op.os, 'fchmod', create=True) as data_chmod, \
                        patch.object(op.os, 'close') as data_close, \
                        patch.object(op, 'Path', side_effect=lambda p: meminfo if p == '/proc/meminfo' else real_path(p)), \
                        patch.object(op, 'fingerprint', return_value='approved-server'), \
                        patch.object(op, 'Tree', side_effect=lambda p, **unused: operator.store if p == '/store' else output), \
                        patch.object(op, 'verify_cpu_affinity'), \
                        (patch.object(op.time, 'monotonic', side_effect=itertools.count(0, 61))
                         if failure == 'postgres' else contextlib.nullcontext()):
                    if failure:
                        expected = {'postgres': 'temporary_postgres_not_ready',
                                    'data_owner': 'disk_data_parent_identity_invalid',
                                    'data_mode': 'disk_data_parent_mode_not_applied'}.get(failure, 'injected_' + failure)
                        with self.assertRaisesRegex(op.Rejected, expected):
                            operator.run_job(args, {}, 'candidate-hash')
                    else:
                        report = operator.run_job(args, {}, 'candidate-hash')
                        self.assertTrue(report['operational_resume']['collection_gap_expected'])
                    starts = [i for i, x in enumerate(operator.actions) if x == ('start', 'server-id')]
                    cleanups = [i for i, x in enumerate(operator.actions) if x[0] == 'cleanup']
                    if capacity:
                        data_open.assert_not_called()
                        data_close.assert_not_called()
                    else:
                        data_open.assert_called_once()
                        self.assertEqual({'dir_fd': 7}, data_open.call_args.kwargs)
                        self.assertTrue(data_open.call_args.args[0].endswith('/postgres'))
                        self.assertEqual(op.os.O_RDONLY | op.os.O_DIRECTORY | op.os.O_NOFOLLOW,
                                         data_open.call_args.args[1])
                        data_close.assert_called_once_with(707)
                    if failure in ('data_owner', 'data_mode'):
                        self.assertFalse(starts)
                        self.assertTrue(operator.running)
                        self.assertFalse(any(action[0] == 'create' for action in operator.actions))
                        if failure == 'data_owner':
                            data_chmod.assert_not_called()
                        else:
                            data_chmod.assert_called_once_with(707, 0o711)
                    elif failure == 'postgres':
                        self.assertFalse(starts)
                        self.assertTrue(operator.running)
                        logs = [i for i, x in enumerate(operator.actions) if x[0] == 'logs']
                        self.assertLess(logs[0], cleanups[0])
                        failed_report = next(value for key, value in operator.private.documents.items()
                                             if key.startswith('reports/'))
                        self.assertEqual('postgres_readiness', failed_report['failure_stage'])
                        self.assertEqual(1, failed_report['postgres_readiness_attempts'])
                        self.assertEqual(['permission_denied', 'initdb_failed'],
                                         failed_report['postgres_startup']['log_markers'])
                        self.assertTrue(failed_report['cleanup_complete'])
                        self.assertNotIn('TOKEN', repr(failed_report))
                    elif failure == 'cleanup':
                        self.assertFalse(starts)
                        self.assertFalse(operator.running)
                        self.assertEqual('awaiting_job_cleanup', operator.operational_journal()['state'])
                    elif failure == 'resume':
                        self.assertFalse(operator.running)
                        self.assertEqual('resume_pending', operator.job_journal()['state'])
                    else:
                        self.assertTrue(operator.running)
                        self.assertGreater(starts[0], cleanups[0])
                        self.assertEqual('complete', operator.job_journal()['state'])
                    self.assertTrue(operator.database_running)

    def test_disk_parent_fix_grants_only_search_to_postgres_uid(self):
        # Native failed fixture: root parent0700 and already-created PGDATA uid70/0700.
        def permissions(mode, owner, caller):
            return (mode >> 6) & 7 if owner == caller else mode & 7
        self.assertEqual(0, permissions(0o700, 0, 70) & 1)
        self.assertEqual(1, permissions(0o711, 0, 70))
        self.assertEqual(7, permissions(0o700, 70, 70))
        self.assertEqual(0, permissions(0o700, 0, 70))  # Protected private ancestor unchanged.

    def test_final_fence_failure_marks_report_failed_and_never_publishes_gate(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                private = MemoryTree()
                operator = unittest.mock.Mock()
                job = 'e' * 32
                operator.source.return_value = ({}, 'c' * 64)
                operator.run_job.return_value = {'job_id': job, 'source_hash': 'c' * 64,
                                                'state': 'passed', 'cleanup_complete': True,
                                                'active_release_and_containers_unchanged': True}
                operator.identity_mismatch = {'kind': 'server'}
                @contextlib.contextmanager
                def fence(**unused):
                    yield MemoryTree()
                    if failure:
                        raise op.Rejected('approved_container_changed')
                operator.fence = fence
                with patch.object(op, 'load_config', return_value={'private': '/private',
                                     'profiles': ['selected']}), \
                        patch.object(op, 'Tree', return_value=private), \
                        patch.object(op, 'Operator', return_value=operator), \
                        contextlib.redirect_stdout(io.StringIO()):
                    code = op.main(['test', 'candidate', '--profile', 'selected',
                                    '--test', 'tests.unit.test_nas_operator'])
                self.assertEqual(1 if failure else 0, code)
                report = private.json('reports/' + job + '.json')
                self.assertEqual(not failure, report['post_job_fence_verified'])
                self.assertEqual('failed' if failure else 'passed', report['state'])
                if failure:
                    self.assertNotIn('gates/candidate.json', private.documents)
                    self.assertEqual({'kind': 'server'}, report['identity_mismatch'])
                else:
                    self.assertTrue(private.json('gates/candidate.json')['post_job_fence_verified'])

    def test_capture_fence_distinguishes_durable_idle_from_retained_ram(self):
        op.idle(idle_snapshot())
        op.idle(idle_snapshot('complete'))
        for state in ('running', 'stopping', 'awaiting_persistence', 'persisting', 'interrupted', 'failed'):
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

    def test_durable_incomplete_trace_allows_maintenance_only_after_full_drain(self):
        snapshot = idle_snapshot('incomplete')
        snapshot['trace'].update(input_rejected=1188, known_dropped=0,
                                 accepted=2033667, written=2033667)
        op.idle(snapshot)
        for key in ('queued', 'pending_events', 'charged_bytes', 'copy_reserved_bytes',
                    'packing_events', 'packed_events'):
            for missing in (False, True):
                value = copy.deepcopy(snapshot)
                if missing:
                    del value['trace'][key]
                else:
                    value['trace'][key] = 1
                with self.subTest(key=key, missing=missing), self.assertRaises(op.Rejected):
                    op.idle(value)
        for accepted, written in ((10, 9), (10, None), (1, True), (-1, -1)):
            value = copy.deepcopy(snapshot)
            value['trace'].update(accepted=accepted, written=written)
            with self.subTest(accepted=accepted, written=written), self.assertRaises(op.Rejected):
                op.idle(value)

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
        self.assertEqual('other', operator.last_docker_failure_class)
        self.assertEqual('inspect', operator.last_docker_action)
        self.assertNotIn('secret', repr((operator.last_docker_action, operator.last_docker_failure_class)))
        cases = {
            b'failed to create mount: bind source path does not exist': 'mount_invalid',
            b'no such image: private-image': 'image_unavailable',
            b'NanoCPUs can not be set, as your kernel does not support CPU CFS scheduler':
                'cpu_affinity_unavailable',
            b'permission denied': 'permission_denied',
            b'unrecognized Docker failure': 'other',
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(expected, op.docker_failure_class(message))

    def test_worker_start_failure_report_contains_only_bounded_safe_diagnostics(self):
        output = MemoryTree()
        output.put_json('result.json', {'state': 'failed', 'tests': 9, 'skipped': 0,
            'failures': 1, 'errors': 2, 'error_type': 'AssertionError',
            'memory_limit_bytes': 268435456, 'cpu_affinity': '0,1',
            'replay': {'secret': 'must-not-be-reported'}})
        operator = op.Operator({}, None)
        operator.inspect = lambda unused: {'State': {'ExitCode': 1, 'OOMKilled': False}}
        report = {}
        with patch.object(op, 'Tree', return_value=output):
            operator.worker_failure_diagnostics(report, 'a' * 32, '/worker')
        self.assertEqual(1, report['worker_exit_code'])
        self.assertFalse(report['worker_oom_killed'])
        self.assertTrue(report['worker_result_available'])
        self.assertEqual({'state': 'failed', 'tests': 9, 'skipped': 0,
            'failures': 1, 'errors': 2, 'error_type': 'AssertionError',
            'memory_limit_bytes': 268435456, 'cpu_affinity': '0,1'},
            report['worker_result_summary'])
        self.assertNotIn('secret', repr(report))

    def test_worker_failure_reason_preserves_only_controlled_bounded_codes(self):
        for reason in ('private_persistence_timeout', 'capacity_native_workload_incomplete',
                       'TOKEN=secret', 'private_password=secret', 'private_' + 'x' * 111, 5):
            with self.subTest(reason=reason):
                output = MemoryTree()
                output.put_json('result.json', {'state': 'failed', 'failure_reason': reason})
                operator = op.Operator({}, None)
                operator.inspect = lambda unused: {'State': {'ExitCode': 1, 'OOMKilled': False}}
                report = {}
                with patch.object(op, 'Tree', return_value=output):
                    operator.worker_failure_diagnostics(report, 'a' * 32, '/worker')
                summary = report['worker_result_summary']
                if reason in ('private_persistence_timeout', 'capacity_native_workload_incomplete'):
                    self.assertEqual(reason, summary['failure_reason'])
                else:
                    self.assertNotIn('failure_reason', summary)
                self.assertNotIn('secret', repr(report))

    def test_postgres_startup_diagnostics_are_bounded_and_exclude_logs_env_and_paths(self):
        job = 'a' * 32
        operator = op.Operator({}, None)
        operator.inspect = lambda unused: {'Config': {'Labels': {op.LABEL: job},
            'Env': ['PASSWORD=private']}, 'State': {'Running': False, 'ExitCode': 1,
            'OOMKilled': False, 'Status': 'exited', 'Error': 'private /host/path'}}
        raw = (b'private=' + b'x' * 70000 + b'\ninitdb: error: Permission denied /private/path\n'
               b'mkdir: can\x27t create directory \x27/var/lib/postgresql/data/pgdata\x27: Permission denied\n')
        calls = []
        def docker(args, **kwargs):
            calls.append((args, kwargs))
            return b'["name=userns", "name=seccomp,profile=default"]' if args[0] == 'info' else raw
        operator.docker = docker
        report = {}
        operator.postgres_failure_diagnostics(report, job)
        summary = report['postgres_startup']
        self.assertEqual(False, summary['Running'])
        self.assertEqual(1, summary['ExitCode'])
        self.assertEqual(['permission_denied', 'initdb_failed'], summary['log_markers'])
        self.assertTrue(summary['log_tail_truncated'])
        self.assertEqual(hashlib.sha256(raw[-65536:]).hexdigest(), summary['log_tail_sha256'])
        self.assertTrue(summary['daemon_userns_enabled'])
        self.assertEqual('image_default', summary['container_user'])
        self.assertEqual([{'operation': 'other', 'role': 'other'},
                          {'operation': 'mkdir', 'role': 'pgdata'}], summary['permission_locations'])
        self.assertEqual([(['info', '--format', '{{json .SecurityOptions}}'], {'timeout': 5}),
                          (['logs', '--tail', '100', 'kiwoom-op-pg-' + job],
                           {'timeout': 5, 'capture_stderr': True})], calls)
        for secret in ('private', 'PASSWORD', '/host/path'):
            self.assertNotIn(secret, json.dumps(report))

    def test_postgres_diagnostics_report_only_fixed_role_metadata_without_changing_it(self):
        from types import SimpleNamespace
        job = 'a' * 32
        private = MemoryTree()
        operator = op.Operator({}, private)
        operator.inspect = lambda unused: {'Config': {'Labels': {op.LABEL: job}, 'User': '70:70'},
                                           'HostConfig': {'UsernsMode': 'host'}}
        operator.docker = lambda args, **unused: b'[]' if args[0] == 'info' else b''
        stats = [SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o40700), FileNotFoundError(),
                 SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o100600)]
        report = {}
        with patch.object(op.os, 'stat', side_effect=stats) as probe, \
                patch.object(op.os, 'chmod') as chmod, patch.object(op.os, 'chown', create=True) as chown:
            operator.postgres_failure_diagnostics(report, job)
        self.assertEqual(['job-' + job + '/' + leaf for leaf in
                          ('postgres', 'postgres/pgdata', 'postgres-password')],
                         [call.args[0] for call in probe.call_args_list])
        self.assertTrue(all(call.kwargs == {'dir_fd': 7, 'follow_symlinks': False}
                            for call in probe.call_args_list))
        self.assertEqual({'data_root': {'exists': True, 'uid': 0, 'gid': 0, 'mode': 0o700, 'kind': 'directory'},
                          'pgdata': {'exists': False},
                          'password_file': {'exists': True, 'uid': 0, 'gid': 0, 'mode': 0o600, 'kind': 'file'}},
                         report['postgres_startup']['host_file_metadata'])
        self.assertEqual('70:70', report['postgres_startup']['container_user'])
        self.assertEqual('host', report['postgres_startup']['container_userns'])
        self.assertFalse(report['postgres_startup']['daemon_userns_enabled'])
        chmod.assert_not_called()
        chown.assert_not_called()

    def test_postgres_diagnostics_refuse_wrong_job_and_tolerate_missing_logs(self):
        job = 'a' * 32
        operator = op.Operator({}, None)
        operator.docker = unittest.mock.Mock(side_effect=op.Rejected('docker_command_failed'))
        operator.inspect = lambda unused: {'Config': {'Labels': {op.LABEL: 'b' * 32}}}
        report = {}
        operator.postgres_failure_diagnostics(report, job)
        operator.docker.assert_not_called()
        self.assertEqual('Rejected', report['postgres_startup']['inspection_error_type'])
        operator.inspect = lambda unused: {'Config': {'Labels': {op.LABEL: job}}, 'State': {}}
        operator.postgres_failure_diagnostics(report, job)
        self.assertEqual('Rejected', report['postgres_startup']['log_error_type'])

    def test_docker_log_capture_includes_stderr_without_exposing_it(self):
        def run(args, **kwargs):
            self.assertIs(subprocess.STDOUT, kwargs['stderr'])
            self.assertEqual(5, kwargs['timeout'])
            return subprocess.CompletedProcess(args, 0, b'initdb stderr', None)
        operator = op.Operator({'docker': '/docker'}, None, run=run)
        self.assertEqual(b'initdb stderr', operator.docker(['logs', '--tail', '100', 'fixture'],
                                                        timeout=5, capture_stderr=True))

    def test_worker_test_summary_reports_failed_case_and_type_without_traceback_text(self):
        class TestCase:
            def __init__(self, name):
                self.name = name

            def id(self):
                return 'tests.integration.example.' + self.name

        result = unittest.mock.Mock()
        result.wasSuccessful.return_value = False
        result.testsRun = 2
        result.skipped = []
        result.failures = [(TestCase('test_assertion'),
            'Traceback (most recent call last):\n  File "/tmp/test_example.py", line 21, in test_assertion\n'
            'AssertionError: secret payload')]
        result.errors = [(TestCase('test_database'),
            'Traceback (most recent call last):\n  File "/app/database.py", line 44, in save\n'
            'psycopg.OperationalError: TOKEN=secret')]
        runner = unittest.mock.Mock()
        runner.run.return_value = result
        with patch.object(worker.unittest, 'TestSuite'), \
                patch.object(worker.unittest, 'TextTestRunner', return_value=runner):
            summary = worker.run_tests(['tests.integration.example.ExampleTests'])
        self.assertEqual('failed', summary['state'])
        self.assertEqual(2, summary['tests'])
        self.assertEqual([
            {'test_id': 'tests.integration.example.test_assertion', 'error_type': 'AssertionError',
             'traceback_locations': [{'file': 'test_example.py', 'line': 21,
                                      'function': 'test_assertion'}]},
            {'test_id': 'tests.integration.example.test_database', 'error_type': 'OperationalError',
             'traceback_locations': [{'file': 'database.py', 'line': 44, 'function': 'save'}]},
        ], summary['failed_tests'])
        self.assertNotIn('secret', repr(summary))

    def test_worker_has_no_live_mount_socket_secrets_or_host_network(self):
        config = {'worker_memory': 4 * 1024 ** 3, 'worker_cpuset': '0,1', 'helper_dir': '/root/helper',
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
        self.assertEqual('0,1', args[args.index('--cpuset-cpus') + 1])
        self.assertNotIn('--cpus', args)
        capacity = operator.worker_argv('b' * 32, 'pg', 'release', worker_memory=op.CAPACITY_WORKER_MEMORY)
        self.assertEqual(str(12 * 1024**3), capacity[capacity.index('--memory') + 1])
        self.assertEqual(str(12 * 1024**3), capacity[capacity.index('--memory-swap') + 1])
        self.assertEqual(4 * 1024**3, config['worker_memory'])
        with self.assertRaisesRegex(op.Rejected, 'invalid_worker_resource_profile'):
            operator.worker_argv('c' * 32, 'pg', 'release', worker_memory=64 * 1024**3)

    def test_cpu_lists_are_bounded_and_invalid_or_duplicate_ranges_fail(self):
        self.assertEqual({0, 1, 4}, op.cpu_set('0-1,4'))
        for value in (None, '', '0,0', '0-1,1', '2-1', '-1', '0;echo', '4096', '0-9999', ' 0', '0,'):
            with self.subTest(value=value), self.assertRaises(op.Rejected):
                op.cpu_set(value)

    def test_cpu_pool_preserves_sparse_host_affinity_and_caps_both_jobs(self):
        config = op.cpu_limits({2, 4, 6, 8})
        self.assertEqual({'worker_cpuset': '2,4', 'pg_cpuset': '2'}, config)
        self.assertEqual(({2, 4}, {2}), op.validate_cpu_limits(config, {2, 4, 6, 8}))
        op.verify_cpu_affinity('2,4', '2,4')
        op.verify_cpu_affinity('0-1', '0,1')
        for invalid in ({'worker_cpuset': '0-2', 'pg_cpuset': '0'},
                        {'worker_cpuset': '0-1', 'pg_cpuset': '0-1'},
                        {'worker_cpuset': '0-1', 'pg_cpuset': '2'},
                        {'worker_cpuset': '0-1', 'pg_cpuset': '0'}):
            with self.subTest(config=invalid), self.assertRaises(op.Rejected):
                op.validate_cpu_limits(invalid, {2, 4, 6, 8})
        with self.assertRaisesRegex(op.Rejected, 'cpu_limit_not_enforced'):
            op.verify_cpu_affinity('0-3', '0-1')
        for unavailable in ({0}, set(), {0, True}, {0, 5000}):
            with self.subTest(available=unavailable), self.assertRaises(op.Rejected):
                op.cpu_limits(unavailable)

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
