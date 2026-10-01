from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.nas_source_runtime import (
    BUILD_FILE, CONTRACT_FILES, atomic_json, checked_path, digest, release, runtime_environment, stage,
    review_schema_update,
)


class SourceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "working"
        self.store = Path(self.temporary.name) / "releases-store"
        self.names = set(CONTRACT_FILES) | {BUILD_FILE, "tests/__init__.py", "scripts/example.py"}
        for name in self.names:
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('SERVER_BUILD = "2026.10.01-test-v1"\n' if name == BUILD_FILE else "original\n")

    def publish(self):
        def git_output(command, **kwargs):
            if command[1] == "ls-files":
                return ("\0".join(sorted(self.names - {"deploy/synology/server.Dockerfile"})) + "\0").encode()
            if command[1] == "status":
                return ""
            return "test-commit\n"
        with patch("scripts.nas_source_runtime.subprocess.check_output", side_effect=git_output):
            return stage(self.source, self.store, "test-image")

    def test_new_release_keeps_selected_source_pinned_and_supports_previous_release(self):
        first = self.publish()
        atomic_json(self.store / "active.json", {"format": 1, "release_id": first["release_id"]})
        old_path, _ = release(self.store)
        (self.source / BUILD_FILE).write_text('SERVER_BUILD = "2026.10.01-test-v2"\n')
        second = self.publish()
        self.assertNotEqual(first["release_id"], second["release_id"])
        self.assertEqual(old_path, release(self.store)[0])
        self.assertIn("test-v1", (old_path / BUILD_FILE).read_text())
        atomic_json(self.store / "active.json", {"format": 1, "release_id": second["release_id"]})
        self.assertEqual(second["release_id"], release(self.store)[1]["release_id"])
        self.assertEqual(old_path, release(self.store, first["release_id"])[0])

    def test_partial_copy_failure_does_not_publish_or_change_active_release(self):
        first = self.publish()
        atomic_json(self.store / "active.json", {"format": 1, "release_id": first["release_id"]})
        (self.source / BUILD_FILE).write_text('SERVER_BUILD = "2026.10.01-test-v2"\n')
        with patch("scripts.nas_source_runtime.shutil.copyfile", side_effect=OSError("copy failed")):
            with self.assertRaisesRegex(OSError, "copy failed"):
                self.publish()
        self.assertEqual(first["release_id"], release(self.store)[1]["release_id"])
        self.assertEqual(1, len(list((self.store / "releases").iterdir())))

    def test_changed_release_file_is_rejected(self):
        result = self.publish()
        path, _ = release(self.store, result["release_id"])
        (path / "scripts/example.py").write_text("edited")
        with self.assertRaisesRegex(RuntimeError, "release file changed"):
            release(self.store, result["release_id"])

    def test_same_build_marker_cannot_publish_different_server_source(self):
        self.publish()
        (self.source / BUILD_FILE).write_text('SERVER_BUILD = "2026.10.01-test-v1"\n# change\n')
        with self.assertRaisesRegex(RuntimeError, "SERVER_BUILD must change"):
            self.publish()

    def test_dependency_or_schema_change_requires_runtime_update(self):
        self.publish()
        for name in CONTRACT_FILES:
            target = self.source / name
            original = target.read_text()
            target.write_text("changed")
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "prepare a new runtime"):
                self.publish()
            target.write_text(original)

    def test_path_escape_and_non_code_payload_are_rejected(self):
        for value in ("../data/secret", "/outside", "data/secret", "src/../../outside", "src\\x"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                checked_path(self.source, value)
        with self.assertRaisesRegex(RuntimeError, "invalid release ID"):
            release(self.store, "../outside")

    def test_runtime_environment_pins_candidate_source_without_changing_caller(self):
        import os
        result = self.publish()
        def hash_with_image(path):
            if path == Path("/app/pyproject.toml"):
                return digest(self.source / "pyproject.toml")
            return digest(path)
        with patch.dict(os.environ, {"PYTHONPATH": "/app/src"}), patch(
            "scripts.nas_source_runtime.digest", side_effect=hash_with_image,
        ):
            path, _, environment = runtime_environment(self.store, result["release_id"])
            self.assertEqual(str(path / "src"), environment["PYTHONPATH"])
            self.assertEqual("1", environment["PYTHONDONTWRITEBYTECODE"])
            self.assertEqual("/app/src", os.environ["PYTHONPATH"])

    def test_reviewed_append_only_schema_keeps_previous_release_and_active_pointer(self):
        first = self.publish()
        atomic_json(self.store / "active.json", {"format": 1, "release_id": first["release_id"]})
        schema = self.source / "src/kiwoom_monitor/central_server/central_schema.py"
        schema.write_text("reviewed append-only migration\n")
        (self.source / "scripts/check_source_database.py").write_text("guard\n")
        old = [[20, "old", [], []]]
        with patch("scripts.nas_source_runtime.schema_plan", side_effect=[old, [*old, [21, "shadow_checkpoint_frames", [], []]]]):
            result = review_schema_update(self.source, self.store)
        self.assertFalse(result["active_changed"])
        _, previous = release(self.store)
        self.assertEqual(first["release_id"], previous["release_id"])
        self.assertEqual(1, len(list(self.store.glob("runtime.json.before-schema-*"))))
        self.assertEqual(1, len(list(self.store.glob("runner.py.before-schema-*"))))
        self.assertFalse((self.store / "deploy.lock").exists())

    def test_schema_review_rejects_dependency_or_prior_migration_edits_before_writing(self):
        first = self.publish()
        atomic_json(self.store / "active.json", {"format": 1, "release_id": first["release_id"]})
        before = (self.store / "runtime.json").read_bytes()
        (self.source / "pyproject.toml").write_text("new dependency\n")
        with self.assertRaisesRegex(RuntimeError, "dependency"):
            review_schema_update(self.source, self.store)
        (self.source / "pyproject.toml").write_text("original\n")
        with patch("scripts.nas_source_runtime.schema_plan", side_effect=[[[20, "old", [], []]], [[20, "edited", [], []], [21, "shadow_checkpoint_frames", [], []]]]):
            with self.assertRaisesRegex(RuntimeError, "append-only"):
                review_schema_update(self.source, self.store)
        self.assertEqual(before, (self.store / "runtime.json").read_bytes())
        self.assertFalse((self.store / "deploy.lock").exists())

    def test_schema_metadata_replace_failure_restores_launcher_and_previous_contract(self):
        import os
        first = self.publish()
        atomic_json(self.store / "active.json", {"format": 1, "release_id": first["release_id"]})
        before = {name: (self.store / name).read_bytes() for name in ("runtime.json", "runner.py")}
        (self.source / "src/kiwoom_monitor/central_server/central_schema.py").write_text("next schema\n")
        (self.source / "scripts/check_source_database.py").write_text("guard\n")
        old = [[20, "old", [], []]]
        replace = os.replace
        failed = False

        def fail_runtime_once(source, target):
            nonlocal failed
            if Path(target).name == "runtime.json" and not failed:
                failed = True
                raise OSError("metadata replacement failed")
            return replace(source, target)

        with patch("scripts.nas_source_runtime.schema_plan", side_effect=[old, [*old, [21, "shadow_checkpoint_frames", [], []]]]), patch(
            "scripts.nas_source_runtime.os.replace", side_effect=fail_runtime_once,
        ):
            with self.assertRaisesRegex(OSError, "metadata replacement"):
                review_schema_update(self.source, self.store)
        self.assertEqual(before, {name: (self.store / name).read_bytes() for name in before})
        self.assertEqual(first["release_id"], release(self.store)[1]["release_id"])
        self.assertFalse((self.store / "deploy.lock").exists())


if __name__ == "__main__":
    unittest.main()
