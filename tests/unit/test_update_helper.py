from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from kiwoom_monitor.update_helper import UpdateError, _read_manifest, _safe_relative, apply_archives


class UpdateHelperTest(unittest.TestCase):
    @staticmethod
    def _create_archive(path: Path, changed: dict[str, str], deleted: tuple[str, ...] = ()) -> None:
        manifest = {"version": "test", "changed": list(changed), "deleted": list(deleted)}
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("update_manifest.json", json.dumps(manifest))
            for name, content in changed.items():
                archive.writestr(name, content)

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

    def test_apply_archives_applies_multiple_versions_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app"
            target.mkdir()
            (target / "version.txt").write_text("1.1.1", encoding="utf-8")
            first = root / "1.1.2.zip"
            second = root / "1.1.3.zip"
            third = root / "1.1.4.zip"
            self._create_archive(first, {"version.txt": "1.1.2", "features/one.txt": "one"})
            self._create_archive(second, {"version.txt": "1.1.3", "features/two.txt": "two"}, ("features/one.txt",))
            self._create_archive(third, {"version.txt": "1.1.4", "features/three.txt": "three"})

            apply_archives((first, second, third), target)

            self.assertEqual("1.1.4", (target / "version.txt").read_text(encoding="utf-8"))
            self.assertFalse((target / "features" / "one.txt").exists())
            self.assertEqual("two", (target / "features" / "two.txt").read_text(encoding="utf-8"))
            self.assertEqual("three", (target / "features" / "three.txt").read_text(encoding="utf-8"))
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertFalse(third.exists())
