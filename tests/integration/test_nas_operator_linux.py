"""Linux fd/permission tests. Run only in a disposable, offline container."""
import hashlib
import contextlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from scripts import nas_operator as op


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW'), 'Linux directory-fd gate is required')
class NasOperatorLinuxTests(unittest.TestCase):
    def setUp(self):
        root = os.environ.get('KIWOOM_OPERATOR_FS_GATE_ROOT')
        if not root:
            self.skipTest('disposable filesystem gate root is required')
        if os.geteuid() != 0:
            self.skipTest('disposable container root is required')
        self.directory = Path(tempfile.mkdtemp(prefix='case-', dir=root))
        self.old_umask = os.umask(0o077)

    def tearDown(self):
        os.umask(self.old_umask)
        shutil.rmtree(self.directory)

    def tree(self, name, protected=False):
        path = self.directory / name
        path.mkdir(mode=0o700)
        return op.Tree(str(path), owners=(0,), protected=protected)

    def test_umask_keeps_secrets_private_but_admitted_sources_readable(self):
        with self.tree('private', protected=True) as tree:
            tree.write('releases/candidate/src/app.py', b'value', 0o644)
            tree.write('secret.json', b'{}')
            path = Path(tree.path)
            self.assertEqual(0o644, stat.S_IMODE((path / 'releases/candidate/src/app.py').stat().st_mode))
            self.assertEqual(0o755, stat.S_IMODE((path / 'releases/candidate/src').stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE((path / 'secret.json').stat().st_mode))
            self.assertEqual(0o700, stat.S_IMODE(path.stat().st_mode))

    def test_symlink_hardlink_and_fifo_inputs_are_rejected(self):
        with self.tree('input') as tree:
            root = Path(tree.path)
            (root / 'real').write_bytes(b'original')
            os.symlink('real', root / 'link')
            os.link(root / 'real', root / 'hardlink')
            os.mkfifo(root / 'fifo')
            for name in ('link', 'hardlink', 'fifo'):
                with self.subTest(name=name), self.assertRaises((op.Rejected, OSError)):
                    tree.read(name)
            (root / 'folder').mkdir()
            os.symlink('folder', root / 'parent-link')
            with self.assertRaises(OSError):
                tree.write('parent-link/data', b'x')

    def test_block_registration_uses_anchored_files_and_pins_registered_bundle(self):
        from tests.unit.test_nas_operator import ScopedOperatorTests
        trace_id, manifest, files, digest, name = ScopedOperatorTests().block_fixture()
        incoming_root = self.directory / 'incoming'
        incoming = incoming_root / trace_id
        incoming.mkdir(parents=True, mode=0o700)
        for filename, data in files.items():
            (incoming / filename).write_bytes(data)
        with self.tree('private', protected=True) as private:
            operator = op.Operator({'trace_dir': str(incoming_root), 'allowed_uid': 0,
                'input_file_limit': 64 * 1024 ** 2, 'input_total_limit': 1024 ** 3}, private)
            result = operator.register('traces', trace_id, 'scoped-operations')
            self.assertEqual(result, operator.registered_input('traces', trace_id))
            self.assertEqual(files[name], private.read('traces/' + trace_id + '/' + name))
            self.assertEqual(0o644, stat.S_IMODE((Path(private.path) / 'traces' / trace_id / name).stat().st_mode))
            (incoming / name).unlink()
            os.symlink('000001.jsonl', incoming / name)
            with self.assertRaises(OSError):
                operator.register('traces', trace_id, 'scoped-operations')

    def test_parent_replacement_does_not_redirect_anchored_reads(self):
        with self.tree('input') as tree:
            tree.write('value', b'old')
            original = Path(tree.path)
            renamed = original.with_name('detached')
            original.rename(renamed)
            original.mkdir()
            (original / 'value').write_bytes(b'new')
            self.assertEqual(b'old', tree.read('value'))
            with self.assertRaisesRegex(op.Rejected, 'directory_replaced'):
                tree.verify_identity()

    def test_atomic_file_sync_failure_never_replaces_old_value(self):
        with self.tree('private', protected=True) as tree:
            tree.write('active.json', b'old')
            with patch.object(op.os, 'fsync', side_effect=OSError('injected fsync')):
                with self.assertRaises(OSError):
                    tree.write('active.json', b'new')
            self.assertEqual(b'old', tree.read('active.json'))
            self.assertEqual(['active.json'], sorted(os.listdir(tree.fd)))

    def test_lock_contention_and_inode_survive_release(self):
        with self.tree('private', protected=True) as tree:
            with tree.lock('lock'):
                inode = (Path(tree.path) / 'lock').stat().st_ino
                with self.assertRaisesRegex(op.Rejected, 'operation_busy'):
                    with tree.lock('lock'):
                        self.fail('second lock entered')
            self.assertEqual(inode, (Path(tree.path) / 'lock').stat().st_ino)
            with tree.lock('lock'):
                pass

    def test_admission_is_immutable_and_bad_input_never_publishes(self):
        with self.tree('incoming') as incoming, self.tree('private', protected=True) as private:
            content = {name: name.encode() for name in op.CONTRACT if name != op.CONTRACT[1]}
            content['src/kiwoom_monitor/central_server/app.py'] = b'SERVER_BUILD = "build"\n'
            files = {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}
            contract = {name: files[name] if name in files else 'a' * 64 for name in op.CONTRACT}
            digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
            release = 'build-' + digest[:16]
            value = {'format': 1, 'release_id': release, 'server_build': 'build', 'files': files,
                     'contract': contract, 'src_hash': hashlib.sha256(json.dumps(
                         {k: v for k, v in files.items() if k.startswith('src/')}, sort_keys=True).encode()).hexdigest()}
            for name, data in content.items():
                incoming.write(name, data)
            incoming.put_json('manifest.json', value)
            result, actual = op.admit_source(incoming, private, release, contract)
            self.assertEqual(digest, actual)
            self.assertEqual(value, result)
            incoming.write('src/kiwoom_monitor/central_server/app.py', b'changed')
            self.assertEqual(b'SERVER_BUILD = "build"\n', private.read('releases/' + release + '/src/kiwoom_monitor/central_server/app.py'))
            with self.tree('empty-private', protected=True) as empty:
                with self.assertRaisesRegex(op.Rejected, 'source_file_hash_mismatch'):
                    op.admit_source(incoming, empty, release, contract)
                self.assertEqual([], os.listdir(empty.fd))

    def test_crlf_admission_keeps_manifested_bytes_and_rejects_build_and_hash_changes(self):
        app = 'src/kiwoom_monitor/central_server/app.py'
        for number, marker in enumerate((b'SERVER_BUILD = "build"\r\n',
                                        b'SERVER_BUILD = "other"\r\n',
                                        b'SERVER_BUILD = "build" # trailing\r\n')):
            with self.subTest(marker=marker), self.tree('incoming-' + str(number)) as incoming, \
                    self.tree('private-' + str(number), protected=True) as private:
                content = {name: name.encode() for name in op.CONTRACT if name != op.CONTRACT[1]}
                content[app] = marker
                files = {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}
                contract = {name: files.get(name, 'a' * 64) for name in op.CONTRACT}
                digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
                release = 'build-' + digest[:16]
                value = dict(format=1, release_id=release, server_build='build', files=files,
                    contract=contract, src_hash=hashlib.sha256(json.dumps(
                        {k: v for k, v in files.items() if k.startswith('src/')}, sort_keys=True).encode()).hexdigest())
                for name, data in content.items():
                    incoming.write(name, data)
                incoming.put_json('manifest.json', value)
                if number:
                    with self.assertRaisesRegex(op.Rejected, 'build_marker_mismatch'):
                        op.admit_source(incoming, private, release, contract)
                    self.assertEqual([], os.listdir(private.fd))
                else:
                    self.assertEqual((value, digest), op.admit_source(incoming, private, release, contract))
                    self.assertEqual(marker, private.read('releases/' + release + '/' + app))
                    incoming.write(app, marker.replace(b'\r\n', b'\n'))
                    with self.tree('unchanged-hash-private', protected=True) as empty:
                        with self.assertRaisesRegex(op.Rejected, 'source_file_hash_mismatch'):
                            op.admit_source(incoming, empty, release, contract)
                        self.assertEqual([], os.listdir(empty.fd))

    def test_acl_checks_reject_unknown_nonroot_writes_and_failed_tool(self):
        from scripts import nas_operator_install as installer
        import subprocess
        cases = [(0, b'Linux mode', True),
                 (255, b"(synoacltool.c, 596)It's Linux mode\n", True),
                 (0, b"(synoacltool.c, 596)It's Linux mode\n", True),
                 (0, b'ACL version: 1\n[0] user:root:allow:rwxpdDaARWcCo:fd--', True),
                 (0, b'ACL version: 1\n[0] user:k379:allow:rwx:fd--', False),
                 (1, b'No ACL', False), (0, b'unrecognized', False),
                 (255, b'(synoacltool.c, 596)Path not found\n', False),
                 (255, b"warning\n(synoacltool.c, 596)It's Linux mode\n", False),
                 (1, b"(synoacltool.c, 596)It's Linux mode\n", False),
                 (0, b'warning No ACL', False)]
        with self.tree('acl-target', protected=True) as tree:
            for code, output, allowed in cases:
                with self.subTest(code=code, output=output), patch.object(installer.subprocess, 'run',
                        return_value=subprocess.CompletedProcess(['acl'], code, output, b'')):
                    if allowed:
                        installer.check_acl(tree.path, '/acl')
                    else:
                        with self.assertRaises(op.Rejected):
                            installer.check_acl(tree.path, '/acl')

    def test_linux_mode_acl_response_never_bypasses_posix_or_error_checks(self):
        from scripts import nas_operator_install as installer
        response = subprocess.CompletedProcess(['acl'], 255, b"(synoacltool.c, 596)It's Linux mode\n", b'')
        with self.tree('linux-mode', protected=True) as tree, \
                patch.object(installer.subprocess, 'run', return_value=response):
            installer.check_acl(tree.path, '/acl')
            tree.write('safe', b'content', 0o600)
            installer.check_acl(str(Path(tree.path) / 'safe'), '/acl')
            tree.write('writable', b'content', 0o666)
            os.symlink('safe', Path(tree.path) / 'link')
            os.link(Path(tree.path) / 'safe', Path(tree.path) / 'hardlink')
            for name in ('writable', 'link', 'hardlink'):
                with self.subTest(name=name), self.assertRaises(op.Rejected):
                    installer.check_acl(str(Path(tree.path) / name), '/acl')
            with self.assertRaises(FileNotFoundError):
                installer.check_acl(str(Path(tree.path) / 'missing'), '/acl')
            os.chmod(tree.path, 0o777)
            with self.assertRaises(op.Rejected):
                installer.check_acl(tree.path, '/acl')
            os.chmod(tree.path, 0o700)
            response.stderr = b'permission denied'
            with self.assertRaisesRegex(op.Rejected, 'acl_inspection_unavailable'):
                installer.check_acl(tree.path, '/acl')

    def test_resource_probe_cleans_up_on_success_bad_affinity_memory_and_start_failure(self):
        from scripts import nas_operator_install as installer
        from unittest.mock import Mock
        for failure in (None, 'affinity', 'memory', 'start'):
            supervisor = Mock()
            supervisor.config = {'runtime_image_id': 'sha256:pinned', 'worker_cpuset': '0,1', 'pg_cpuset': '0'}
            masks = iter(('0,1', '0'))
            commands = []
            def docker(args, **kwargs):
                commands.append(args)
                if args[0] == 'start':
                    if failure == 'start':
                        raise op.Rejected('docker_command_timeout')
                    return json.dumps({'cpu_affinity': '0-3' if failure == 'affinity' else next(masks),
                                       'memory_limit': 0 if failure == 'memory' else 268435456}).encode()
                return b'created'
            supervisor.docker.side_effect = docker
            with self.subTest(failure=failure):
                if failure:
                    with self.assertRaises(op.Rejected):
                        installer.probe_cpu_limits(supervisor)
                else:
                    result = installer.probe_cpu_limits(supervisor)
                    self.assertEqual({'worker', 'pg'}, set(result))
                    self.assertEqual('0', result['pg']['cpu_affinity'])
                supervisor.cleanup_containers.assert_called_once()
                for args in commands:
                    if args[0] == 'create':
                        self.assertEqual('none', args[args.index('--network') + 1])
                        self.assertEqual('65534:65534', args[args.index('--user') + 1])
                        self.assertIn('--cpuset-cpus', args)
                        self.assertNotIn('--mount', args)
                        self.assertNotIn('--cpus', args)

    def test_interrupted_install_restores_exact_bytes_and_removes_new_file(self):
        from scripts import nas_operator_install as installer
        with self.tree('private', protected=True) as private, self.tree('installed', protected=True) as installed:
            root = Path(installed.path)
            installed.write('existing', b'new')
            installed.write('added', b'new')
            private.write('install-backups/0', b'old')
            private.put_json('install-backup-index.json', {'state': 'prepared', 'entries': [
                {'path': str(root / 'existing'), 'index': 0, 'existed': True, 'mode': 0o644,
                 'sha256': hashlib.sha256(b'old').hexdigest()},
                {'path': str(root / 'added'), 'index': 1, 'existed': False, 'mode': None, 'sha256': None}]})
            installer.recover_install(private, {str(root / 'existing'), str(root / 'added')})
            self.assertEqual(b'old', installed.read('existing'))
            self.assertFalse((root / 'added').exists())
            self.assertEqual('rolled_back', private.json('install-backup-index.json')['state'])

    def test_passwordless_probe_drops_all_ids_ignores_cached_auth_and_checks_native_ack(self):
        from scripts import nas_operator_install as installer
        import pwd
        account = pwd.getpwnam('nobody')
        config = {'allowed_user': account.pw_name, 'allowed_uid': account.pw_uid,
                  'sudo': '/verified/sudo', 'initial_release': 'original'}
        actions = []
        ack = json.dumps({'state': 'ok', 'command': 'status',
                          'result': {'installed': True, 'active_release': 'original'}}).encode()
        def run(args, **kwargs):
            self.assertEqual(['/verified/sudo', '-k', '-n', op.ROOT_LAUNCHER, 'status'], args)
            self.assertEqual(op.CLEAN_ENV, kwargs['env'])
            kwargs['preexec_fn']()
            return subprocess.CompletedProcess(args, 0, ack, b'')
        with patch.object(installer.os, 'initgroups', side_effect=lambda *x: actions.append(('groups', x))), \
                patch.object(installer.os, 'setgid', side_effect=lambda x: actions.append(('gid', x))), \
                patch.object(installer.os, 'setuid', side_effect=lambda x: actions.append(('uid', x))), \
                patch.object(installer.os, 'getresuid', return_value=(account.pw_uid,) * 3), \
                patch.object(installer.os, 'getresgid', return_value=(account.pw_gid,) * 3):
            installer.verify_passwordless_status(config, run)
        self.assertEqual([('groups', (account.pw_name, account.pw_gid)),
                          ('gid', account.pw_gid), ('uid', account.pw_uid)], actions)
        for ack in (b'{}', b'{"state":"ok","command":"status","result":{"installed":true,"active_release":"changed"}}'):
            with self.subTest(ack=ack), self.assertRaises(op.Rejected):
                installer.verify_passwordless_status(config, lambda *a, **k: subprocess.CompletedProcess([], 0, ack, b''))
        with self.assertRaisesRegex(op.Rejected, 'passwordless_status_failed'):
            installer.verify_passwordless_status(config, lambda *a, **k: subprocess.CompletedProcess([], 1, b'', b'password required'))
        with patch.object(installer.os, 'initgroups'), patch.object(installer.os, 'setgid'), \
                patch.object(installer.os, 'setuid'), \
                patch.object(installer.os, 'getresuid', return_value=(account.pw_uid, account.pw_uid, 0)), \
                patch.object(installer.os, '_exit', side_effect=SystemExit(126)), self.assertRaises(SystemExit):
            installer.verify_passwordless_status(config, run)

    def test_missing_visudo_is_optional_but_other_missing_tools_still_reject(self):
        from scripts import nas_operator_install as installer
        with patch.object(installer.shutil, 'which', return_value=None):
            self.assertIsNone(installer.executable('visudo', required=False))
            with self.assertRaisesRegex(op.Rejected, 'required_tool_unavailable:docker'):
                installer.executable('docker')

    def test_helper_update_preserves_revoke_backups_and_rolls_back_failed_probe(self):
        from scripts import nas_operator_install as installer
        source = Path(__file__).resolve().parents[2]
        for failed in (False, True, 'fence'):
            with self.subTest(failed=failed), contextlib.ExitStack() as stack:
                root = Path(tempfile.mkdtemp(prefix='refresh-', dir=self.directory))
                helper = root / 'helper'
                helper.mkdir(mode=0o755)
                private_path = root / 'private'
                private_path.mkdir(mode=0o700)
                paths = {'HELPER': str(helper), 'CONFIG': str(root / 'operator.json'),
                         'ROOT_LAUNCHER': str(root / 'launcher'), 'CLIENT': str(root / 'client'),
                         'SUDOERS': str(root / 'sudoers')}
                stack.enter_context(patch.multiple(op, **paths))
                stack.enter_context(patch.multiple(installer, HELPER=str(helper), PRIVATE=str(private_path)))
                stack.enter_context(patch.object(installer, 'protected_directory'))
                stack.enter_context(patch.object(installer, 'check_acl'))
                stack.enter_context(patch.object(op, 'validate_sudo_policy'))
                store = unittest.mock.MagicMock()
                store.json.return_value = {'release_id': 'active'}
                fence = stack.enter_context(patch.object(op.Operator, 'fence'))
                fence.return_value.__enter__.return_value = store
                if failed == 'fence':
                    fence.return_value.__exit__.side_effect = op.Rejected('injected_fence_failure')
                stack.enter_context(patch.object(op.Operator, 'identities'))
                probe = stack.enter_context(patch.object(installer, 'verify_passwordless_status'))
                if failed is True:
                    probe.side_effect = op.Rejected('injected_probe_failure')
                originals, entries = {}, []
                with op.Tree(str(private_path), owners=(0,), protected=True) as private:
                    for number, path in enumerate(sorted(op.installed_paths())):
                        value = ('installed:' + path).encode()
                        Path(path).write_bytes(value)
                        os.chmod(path, 0o644)
                        originals[path] = value
                        private.write('install-backups/' + str(number), b'original-before-install')
                        entries.append({'path': path, 'index': number, 'existed': True, 'mode': 0o644,
                                        'sha256': hashlib.sha256(b'original-before-install').hexdigest(),
                                        'installed_sha256': hashlib.sha256(value).hexdigest(),
                                        'installed_mode': 0o644})
                    original_index = {'state': 'complete', 'format': 2, 'entries': entries}
                    private.put_json('install-backup-index.json', original_index)
                    config = {'helper_dir': str(helper), 'private': str(private_path)}
                    if failed:
                        with self.assertRaisesRegex(op.Rejected, 'injected_(probe|fence)_failure'):
                            installer.update_helpers(source, config, '/acl')
                        self.assertEqual(original_index, private.json('install-backup-index.json'))
                        self.assertEqual('rolled_back', private.json('helper-update.json')['state'])
                        for path, data in originals.items():
                            self.assertEqual(data, Path(path).read_bytes())
                    else:
                        result = installer.update_helpers(source, config, '/acl')
                        self.assertTrue(result['sudoers_unchanged'])
                        self.assertFalse(result['server_restarted'])
                        current = private.json('install-backup-index.json')
                        for old, new in zip(entries, current['entries']):
                            self.assertEqual(old['sha256'], new['sha256'])
                            self.assertEqual(old['index'], new['index'])
                            self.assertEqual(hashlib.sha256(Path(new['path']).read_bytes()).hexdigest(),
                                             new['installed_sha256'])
                        for name in ('nas_operator.py', 'nas_operator_worker.py'):
                            self.assertEqual((source / 'scripts' / name).read_bytes(), (helper / name).read_bytes())
                        for path, data in originals.items():
                            if Path(path).parent != helper:
                                self.assertEqual(data, Path(path).read_bytes())
                    for number in range(len(entries)):
                        self.assertEqual(b'original-before-install', private.read('install-backups/' + str(number)))

    def test_interrupted_helper_update_validates_all_backups_before_restore(self):
        from scripts import nas_operator_install as installer
        with self.tree('private', protected=True) as private:
            helper = self.directory / 'helper'
            helper.mkdir()
            with patch.object(installer, 'HELPER', str(helper)), patch.object(op, 'HELPER', str(helper)):
                entries = []
                for number, name in enumerate(('nas_operator.py', 'nas_operator_worker.py')):
                    old, new = ('old:' + name).encode(), ('new:' + name).encode()
                    (helper / name).write_bytes(new)
                    private.write('helper-update-backups/' + 'a' * 32 + '/' + str(number), old)
                    entries.append({'path': str(helper / name), 'mode': 0o644,
                                    'previous_sha256': hashlib.sha256(old).hexdigest(),
                                    'new_sha256': hashlib.sha256(new).hexdigest()})
                index = {'state': 'complete', 'entries': [{'path': path} for path in op.installed_paths()]}
                private.put_json('helper-update.json', {'state': 'prepared', 'update_id': 'a' * 32,
                                                       'entries': entries, 'previous_index': index})
                saved = private.read('helper-update-backups/' + 'a' * 32 + '/1')
                private.write('helper-update-backups/' + 'a' * 32 + '/1', b'corrupt')
                with self.assertRaisesRegex(op.Rejected, 'helper_update_backup_changed'):
                    installer.recover_helper_update(private)
                self.assertEqual(b'new:nas_operator.py', (helper / 'nas_operator.py').read_bytes())
                private.write('helper-update-backups/' + 'a' * 32 + '/1', saved)
                installer.recover_helper_update(private)
                self.assertEqual(index, private.json('install-backup-index.json'))
                self.assertEqual('rolled_back', private.json('helper-update.json')['state'])
                for name in ('nas_operator.py', 'nas_operator_worker.py'):
                    self.assertEqual(('old:' + name).encode(), (helper / name).read_bytes())

    def test_native_install_validates_before_grant_and_rolls_back_rule_first_on_failure(self):
        from scripts import nas_operator_install as installer
        import pwd
        account = pwd.getpwnam('nobody')
        for failure in (None, 'baseline', 'parse_warning', 'passwordless', 'rollback_policy'):
            with self.subTest(failure=failure), contextlib.ExitStack() as stack:
                root = Path(tempfile.mkdtemp(prefix='install-', dir=self.directory))
                paths = {'HELPER': str(root / 'helper'), 'CONFIG': str(root / 'config' / 'operator.json'),
                         'ROOT_LAUNCHER': str(root / 'sbin' / 'kiwoom-nas-root'),
                         'CLIENT': str(root / 'bin' / 'kiwoom-nas'),
                         'SUDOERS': str(root / 'sudoers' / 'kiwoom-nas-operator')}
                for folder in ('helper', 'config', 'sbin', 'bin', 'sudoers', 'private'):
                    (root / folder).mkdir(mode=0o700)
                stack.enter_context(patch.multiple(op, **paths))
                stack.enter_context(patch.multiple(installer, **{k: v for k, v in paths.items() if k != 'CONFIG'},
                                                   PRIVATE=str(root / 'private')))
                stack.enter_context(patch.object(installer, 'check_acl'))
                originals = {op.CLIENT: (b'previous client', 0o750),
                             op.ROOT_LAUNCHER: (b'previous launcher', 0o700)}
                for path, (data, mode) in originals.items():
                    with op.Tree(str(Path(path).parent), owners=(0,), protected=True) as tree:
                        tree.write(Path(path).name, data, mode)
                config = {'allowed_user': account.pw_name, 'allowed_uid': account.pw_uid,
                          'sudo': '/verified/sudo', 'sudoers_validation': 'native_fixed_rule',
                          'python': '/verified/python', 'initial_release': 'original', 'private': str(root / 'private')}
                listing_calls = []
                saw_grant = False
                def run(args, **kwargs):
                    nonlocal saw_grant
                    present = Path(op.SUDOERS).exists()
                    if '-l' in args:
                        listing_calls.append(present)
                        baseline = b'User nobody may run the following commands:\n    (ALL) ALL\n'
                        if present:
                            self.assertEqual(op.sudoers_rule('nobody'), Path(op.SUDOERS).read_bytes())
                            saw_grant = True
                            baseline += ('    (root) NOPASSWD: ' + op.ROOT_LAUNCHER + '\n').encode()
                        warning = (failure == 'baseline' or failure == 'parse_warning' and present or
                                   failure == 'rollback_policy' and saw_grant and not present)
                        return subprocess.CompletedProcess(args, 0, baseline, b'parser warning' if warning else b'')
                    if 'preexec_fn' in kwargs:
                        if failure in ('passwordless', 'rollback_policy'):
                            return subprocess.CompletedProcess(args, 1, b'', b'password required')
                        ack = json.dumps({'state': 'ok', 'command': 'status',
                                          'result': {'installed': True, 'active_release': 'original'}}).encode()
                        return subprocess.CompletedProcess(args, 0, ack, b'')
                    self.assertFalse(present)  # Root status runs before sudoers activation.
                    return subprocess.CompletedProcess(args, 0, b'', b'')
                stack.enter_context(patch.object(installer.subprocess, 'run', side_effect=run))
                source = Path(__file__).resolve().parents[2]
                if failure:
                    with self.assertRaises(op.Rejected):
                        installer.install_files(source, config, None, '/acl')
                    self.assertFalse(Path(op.SUDOERS).exists())
                    for path, expected in originals.items():
                        self.assertEqual(expected, op.access_file(path))
                    if failure != 'baseline':
                        with op.Tree(str(root / 'private'), owners=(0,), protected=True) as private:
                            self.assertEqual('prepared' if failure == 'rollback_policy' else 'rolled_back',
                                             private.json('install-backup-index.json')['state'])
                        self.assertEqual([False, True, False], listing_calls)
                else:
                    result = installer.install_files(source, config, None, '/acl')
                    self.assertTrue(result['installed'])
                    self.assertTrue(result['passwordless_status_verified'])
                    self.assertEqual([False, True], listing_calls)
                    with op.Tree(str(root / 'private'), owners=(0,), protected=True) as private:
                        self.assertEqual('complete', private.json('install-backup-index.json')['state'])
                        self.assertEqual('nobody', private.json('installation.json')['passwordless_user_verified'])
                self.assertEqual([], list((root / 'sudoers').glob('.kiwoom-operator-candidate-*')))

    @contextlib.contextmanager
    def revocation_fixture(self, native=False):
        root = Path(tempfile.mkdtemp(prefix='revoke-', dir=self.directory))
        paths = {'HELPER': str(root / 'helper'), 'CONFIG': str(root / 'config' / 'operator.json'),
                 'ROOT_LAUNCHER': str(root / 'sbin' / 'kiwoom-nas-root'),
                 'CLIENT': str(root / 'bin' / 'kiwoom-nas'),
                 'SUDOERS': str(root / 'sudoers' / 'kiwoom-nas-operator')}
        for directory in ('helper', 'config', 'sbin', 'bin', 'sudoers', 'private'):
            (root / directory).mkdir(mode=0o700)
        with patch.multiple(op, **paths), op.Tree(str(root / 'private'), owners=(0,), protected=True) as private:
            entries = []
            for number, path in enumerate(sorted(op.installed_paths())):
                data = ('installed:' + path).encode()
                mode = 0o440 if path == op.SUDOERS else 0o755
                with op.Tree(str(Path(path).parent), owners=(0,), protected=True) as tree:
                    tree.write(Path(path).name, data, mode)
                old = b'previous client' if path == op.CLIENT else None
                if old is not None:
                    private.write('install-backups/' + str(number), old)
                entries.append({'path': path, 'index': number, 'existed': old is not None,
                                'mode': 0o750 if old else None,
                                'sha256': hashlib.sha256(old).hexdigest() if old else None,
                                'installed_sha256': hashlib.sha256(data).hexdigest(), 'installed_mode': mode})
            private.put_json('install-backup-index.json', {'format': 2, 'state': 'complete', 'entries': entries})
            private.write('reports/preserved.json', b'report')
            private.write('traces/preserved/input', b'input')
            runs = []
            def run(args, **kwargs):
                runs.append((args, kwargs))
                return subprocess.CompletedProcess(args, 0, b'User k379 may run:\n    (ALL) ALL\n' if native else b'', b'')
            config = {'sudoers_validation': 'native_fixed_rule', 'sudo': '/verified/sudo', 'allowed_user': 'k379'} if native else {'visudo': '/verified/visudo'}
            yield op.Operator(config, private, run=run), runs

    def test_native_revoke_verifies_rule_absence_and_parser_warning_leaves_access_removed(self):
        with self.revocation_fixture(native=True) as (operator, runs):
            with patch.object(operator, 'run_process', return_value=subprocess.CompletedProcess([], 0, b'allowed', b'parse warning')):
                with self.assertRaisesRegex(op.Rejected, 'sudoers_validation_failed_after_revocation'):
                    operator.revoke_access()
            self.assertFalse(Path(op.SUDOERS).exists())
            self.assertTrue(Path(op.CLIENT).exists())
            self.assertEqual('prepared', operator.access_state()['state'])
            self.assertTrue(operator.revoke_access()['temporary_access_revoked'])
            self.assertEqual(['/verified/sudo', '-n', '-l', '-U', 'k379'], runs[0][0])
            self.assertEqual('revoked', operator.access_state()['state'])

    def test_revoke_restores_original_commands_preserves_inputs_and_is_repeatable(self):
        with self.revocation_fixture() as (operator, runs):
            result = operator.revoke_access()
            self.assertTrue(result['temporary_access_revoked'])
            self.assertFalse(Path(op.SUDOERS).exists())
            self.assertFalse(Path(op.ROOT_LAUNCHER).exists())
            self.assertEqual((b'previous client', 0o750), op.access_file(op.CLIENT))
            self.assertEqual(b'report', operator.private.read('reports/preserved.json'))
            self.assertEqual(b'input', operator.private.read('traces/preserved/input'))
            self.assertTrue(Path(op.CONFIG).exists())
            self.assertTrue(Path(op.HELPER + '/nas_operator.py').exists())
            self.assertEqual('revoked', operator.access_state()['state'])
            self.assertEqual(result, operator.revoke_access())
            self.assertEqual(1, len(runs))
            self.assertEqual(['/verified/visudo', '-c'], runs[0][0])

    def test_interrupted_revoke_never_regrants_access_and_retry_finishes_cleanup(self):
        for failure in ('rule_unlink', 'client_restore', 'final_journal'):
            with self.subTest(failure=failure), self.revocation_fixture() as (operator, runs):
                original_restore = op.restore_access_file
                original_put = operator.private.put_json
                def restore(path, value):
                    original_restore(path, value)
                    if (failure == 'rule_unlink' and path == op.SUDOERS or
                            failure == 'client_restore' and path == op.CLIENT):
                        raise KeyboardInterrupt('injected interruption after mutation')
                def put(name, value):
                    if failure == 'final_journal' and name == 'revocation.json' and value['state'] == 'revoked':
                        raise OSError('injected final fsync failure')
                    original_put(name, value)
                with patch.object(op, 'restore_access_file', side_effect=restore), \
                        patch.object(operator.private, 'put_json', side_effect=put), \
                        self.assertRaises((KeyboardInterrupt, OSError)):
                    operator.revoke_access()
                self.assertFalse(Path(op.SUDOERS).exists())
                self.assertEqual('prepared', operator.access_state()['state'])
                with patch.object(operator, 'identities') as inspect, self.assertRaisesRegex(op.Rejected, 'operator_access_revoked'):
                    with operator.fence():
                        self.fail('revoked operator entered the deployment fence')
                inspect.assert_not_called()
                self.assertTrue(operator.revoke_access()['temporary_access_revoked'])
                self.assertEqual((b'previous client', 0o750), op.access_file(op.CLIENT))
                self.assertFalse(Path(op.ROOT_LAUNCHER).exists())

    def test_failed_sudoers_validation_keeps_access_revoked_until_admin_retry(self):
        with self.revocation_fixture() as (operator, runs):
            for failure in ('nonzero', 'timeout'):
                with self.subTest(failure=failure):
                    def run(args, **kwargs):
                        if failure == 'timeout':
                            raise subprocess.TimeoutExpired(args, 15)
                        return subprocess.CompletedProcess(args, 1, b'', b'sensitive error')
                    with patch.object(operator, 'run_process', side_effect=run), self.assertRaises(op.Rejected):
                        operator.revoke_access()
                    self.assertFalse(Path(op.SUDOERS).exists())
                    self.assertEqual('prepared', operator.access_state()['state'])
            self.assertTrue(operator.revoke_access()['temporary_access_revoked'])

    def test_revoke_rejects_changed_backups_files_and_previous_sudo_rule_before_mutation(self):
        for failure in ('backup', 'file', 'previous_rule', 'scope', 'journal_hash'):
            with self.subTest(failure=failure), self.revocation_fixture() as (operator, runs):
                index = operator.private.json('install-backup-index.json')
                client = next(entry for entry in index['entries'] if entry['path'] == op.CLIENT)
                if failure == 'backup':
                    operator.private.write('install-backups/' + str(client['index']), b'changed backup')
                elif failure == 'file':
                    with op.Tree(str(Path(op.CLIENT).parent), owners=(0,), protected=True) as tree:
                        tree.write(Path(op.CLIENT).name, b'changed command', 0o755)
                elif failure == 'previous_rule':
                    next(entry for entry in index['entries'] if entry['path'] == op.SUDOERS)['existed'] = True
                    operator.private.put_json('install-backup-index.json', index)
                elif failure == 'scope':
                    index['entries'][0]['path'] = '/etc/other-file'
                    operator.private.put_json('install-backup-index.json', index)
                else:
                    operator.private.put_json('revocation.json', {'state': 'prepared', 'index_hash': 'wrong'})
                with self.assertRaises(op.Rejected):
                    operator.revoke_access()
                self.assertTrue(Path(op.SUDOERS).exists())
                self.assertEqual([], runs)

    def test_revoke_refuses_active_lock_job_or_unfinished_deployment(self):
        with self.revocation_fixture() as (operator, runs):
            with operator.private.lock('operator.lock'), self.assertRaisesRegex(op.Rejected, 'operation_busy'):
                operator.revoke_access()
            operator.private.put_json('job.json', {'state': 'running'})
            with self.assertRaisesRegex(op.Rejected, 'job_recovery_required'):
                operator.revoke_access()
            operator.private.put_json('job.json', {'state': 'complete'})
            operator.private.put_json('deployment.json', {'state': 'needs_admin'})
            with self.assertRaisesRegex(op.Rejected, 'deployment_recovery_required'):
                operator.revoke_access()
            self.assertTrue(Path(op.SUDOERS).exists())
            self.assertEqual([], runs)


if __name__ == '__main__':
    unittest.main()
