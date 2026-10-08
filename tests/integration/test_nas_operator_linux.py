"""Linux fd/permission tests. Run only in a disposable, offline container."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch

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


if __name__ == '__main__':
    unittest.main()
