import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import prepare_nas_scoped_replay as publisher


class ScopedReplayPublicationTests(unittest.TestCase):
    def fixture(self, root):
        nas = root / 'nas'
        workspace = root / 'workspace'
        store = nas / 'source-runtime'
        base = store / 'releases' / 'active-fixture'
        source = {
            publisher.APP: b'SERVER_BUILD = "active-fixture"\nOPERATING_POLICY = 17\n',
            'src/native.py': b'original native source\n',
            'scripts/experiment.py': b'old replay source\n',
        }
        for name, data in source.items():
            target = base / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        manifest = dict(release_id='active-fixture', server_build='active-fixture',
                        contract={'runtime_image': 'pinned-image'},
                        files={name: publisher.digest(data) for name, data in source.items()})
        (base / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        (store / 'active.json').write_text(json.dumps({'release_id': 'active-fixture'}), encoding='utf-8')
        (workspace / 'scripts').mkdir(parents=True)
        (workspace / 'scripts/experiment.py').write_bytes(b'new replay source\n')
        return nas, workspace, base, source

    def test_overlay_preserves_operating_source_contract_and_originals_and_is_repeatable(self):
        with TemporaryDirectory() as directory:
            nas, workspace, base, source = self.fixture(Path(directory))
            original = (base / 'manifest.json').read_bytes()
            active = (nas / 'source-runtime/active.json').read_bytes()
            with patch.object(publisher, 'ROOT', workspace), patch.object(publisher, 'FILES', ('scripts/experiment.py',)):
                receipt = publisher.prepare(nas)
                self.assertEqual(receipt, publisher.prepare(nas))
            candidate = nas / 'source-runtime/releases' / receipt['release_id']
            self.assertEqual(source['src/native.py'], (candidate / 'src/native.py').read_bytes())
            self.assertEqual(source[publisher.APP].replace(b'active-fixture', publisher.BUILD.encode()),
                             (candidate / publisher.APP).read_bytes())
            self.assertEqual(b'new replay source\n', (candidate / 'scripts/experiment.py').read_bytes())
            self.assertEqual({'runtime_image': 'pinned-image'}, json.loads((candidate / 'manifest.json').read_bytes())['contract'])
            self.assertEqual(active, (nas / 'source-runtime/active.json').read_bytes())
            self.assertEqual(original, (base / 'manifest.json').read_bytes())
            for name, data in source.items():
                self.assertEqual(data, (base / name).read_bytes())

    def test_corrupt_source_and_existing_candidate_are_rejected_without_repairing_originals(self):
        with TemporaryDirectory() as directory:
            nas, workspace, base, source = self.fixture(Path(directory))
            with patch.object(publisher, 'ROOT', workspace), patch.object(publisher, 'FILES', ('scripts/experiment.py',)):
                receipt = publisher.prepare(nas)
                target = nas / 'source-runtime/releases' / receipt['release_id'] / 'src/native.py'
                target.write_bytes(b'conflicting bytes')
                with self.assertRaisesRegex(ValueError, 'candidate_conflict'):
                    publisher.prepare(nas)
                self.assertEqual(b'conflicting bytes', target.read_bytes())
                (base / publisher.APP).write_bytes(b'changed active app')
                with self.assertRaisesRegex(ValueError, 'active_source_hash_mismatch'):
                    publisher.prepare(nas)
                self.assertEqual(b'changed active app', (base / publisher.APP).read_bytes())

    def test_path_traversal_is_rejected(self):
        with TemporaryDirectory() as directory:
            for name in ('../outside', '/absolute', 'src/../outside', 'src\\outside', 'src//outside'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    publisher.checked(Path(directory), name)


if __name__ == '__main__':
    unittest.main()
