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

    def test_acl_checks_reject_unknown_nonroot_writes_and_failed_tool(self):
        from scripts import nas_operator_install as installer
        import subprocess
        cases = [(0, b'Linux mode', True),
                 (0, b'ACL version: 1\n[0] user:root:allow:rwxpdDaARWcCo:fd--', True),
                 (0, b'ACL version: 1\n[0] user:k379:allow:rwx:fd--', False),
                 (1, b'No ACL', False), (0, b'unrecognized', False)]
        for code, output, allowed in cases:
            with self.subTest(output=output), patch.object(installer.subprocess, 'run',
                    return_value=subprocess.CompletedProcess(['acl'], code, output, b'')):
                if allowed:
                    installer.check_acl('/protected', '/acl')
                else:
                    with self.assertRaises(op.Rejected):
                        installer.check_acl('/protected', '/acl')

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

    @contextlib.contextmanager
    def revocation_fixture(self):
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
                return subprocess.CompletedProcess(args, 0, b'', b'')
            yield op.Operator({'visudo': '/verified/visudo'}, private, run=run), runs

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
