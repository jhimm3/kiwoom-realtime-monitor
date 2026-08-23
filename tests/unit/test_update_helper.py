from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.update_helper import UpdateError, _read_manifest, _safe_relative


class UpdateHelperTest(unittest.TestCase):
    def test_safe_relative_rejects_path_escape(self) -> None:
        self.assertEqual(Path("_internal") / "module.pyd", _safe_relative("_internal/module.pyd"))
        for value in ("../outside.txt", "/outside.txt", "C:/outside.txt", ""):
            with self.assertRaises(UpdateError):
                _safe_relative(value)

    def test_manifest_rejects_changed_file_outside_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            (staging / "update_manifest.json").write_text(
                json.dumps({"changed": ["../outside.txt"], "deleted": []}), encoding="utf-8"
            )
            with self.assertRaises(UpdateError):
                _read_manifest(staging)

