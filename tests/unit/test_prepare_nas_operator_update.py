import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.prepare_nas_operator_update import bundle_fingerprint, normalize_shell, safe_file


class NasOperatorUpdatePackagingTests(unittest.TestCase):
    def test_shell_gate_is_normalized_and_bundle_identity_tracks_its_content(self):
        crlf = b'#!/bin/sh\r\nset -eu\r\n'
        mixed = b'#!/bin/sh\r\nset -eu\n'
        lf = b'#!/bin/sh\nset -eu\n'

        self.assertEqual(lf, normalize_shell(crlf))
        self.assertEqual(lf, normalize_shell(mixed))
        self.assertEqual(bundle_fingerprint({'gate.sh': lf}),
                         bundle_fingerprint({'gate.sh': normalize_shell(mixed)}))
        self.assertNotEqual(bundle_fingerprint({'gate.sh': lf}),
                            bundle_fingerprint({'gate.sh': lf + b'exit 1\n'}))

    def test_safe_file_accepts_nested_paths_and_rejects_traversal(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / 'scripts' / 'gate.sh'
            nested.parent.mkdir()
            nested.write_text('ok', encoding='utf-8')
            self.assertEqual(nested, safe_file(root, 'scripts/gate.sh'))
            with self.assertRaises(ValueError):
                safe_file(root, '../outside')


if __name__ == '__main__':
    unittest.main()
