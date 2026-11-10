from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from scripts.nas_source_runtime import (
    BUILD_FILE, CONTRACT_FILES, CONTRACT_VERSION, atomic_json, checked_path, digest, release, runtime_environment, stage,
    review_schema_update, emit_research_artifact,
)


def selected_research_artifact_fixture(directory: Path) -> tuple[Path, dict]:
    """An explicitly selected real raw20 test source artifact, never current-source auto approval."""
    from kiwoom_monitor.domain.research_contract import RESEARCH_IMPLEMENTATION_PATHS
    root = Path(__file__).resolve().parents[2]
    artifact, output = directory / "research-producer.zip", directory / "research-producer.json"
    with ZipFile(artifact, "w") as archive:
        for path in RESEARCH_IMPLEMENTATION_PATHS:
            archive.writestr(path, (root / path).read_bytes())
    document = emit_research_artifact(artifact, artifact_kind="source-zip", source_ref="test-source-raw20",
                                    profiles=("krx-regular/v1",), output=output)
    return output, document


class SourceRuntimeTests(unittest.TestCase):
    def test_selected_startup_identity_without_installed_producer_source(self):
        directory = Path(self.temporary.name)
        selected_file, metadata = selected_research_artifact_fixture(directory)
        receiver = directory / "receiver"
        source = Path(__file__).resolve().parents[2] / "src"
        for name in ("kiwoom_monitor/__init__.py", "kiwoom_monitor/domain/__init__.py",
                     "kiwoom_monitor/central_server/__init__.py"):
            target = receiver / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"")
        for name in ("kiwoom_monitor/domain/research_contract.py",
                     "kiwoom_monitor/domain/market_data_contract.py", "kiwoom_monitor/domain/time_basis.py",
                     "kiwoom_monitor/central_server/config.py"):
            shutil.copyfile(source / name, receiver / name)
        child = (
            "import json,sys;sys.path.insert(0,sys.argv[1]);"
            "from kiwoom_monitor.central_server.config import CentralServerSettings;"
            "selected=CentralServerSettings('sqlite:///:memory:','test',"
            "selected_research_artifact_file=sys.argv[2]).selected_research_artifact();"
            "print(json.dumps({'hash':selected.expected_implementation_hash('krx-regular/v1'),"
            "'producer_loaded':any(n.startswith('kiwoom_monitor.application') for n in sys.modules)}))"
        )
        result = subprocess.run([sys.executable, "-I", "-c", child, str(receiver), str(selected_file)],
                                cwd=receiver, capture_output=True, text=True, timeout=30, check=True)
        observed = json.loads(result.stdout)
        self.assertEqual(metadata["implementation_hash"], observed["hash"])
        self.assertFalse(observed["producer_loaded"])
        self.assertFalse((receiver / "kiwoom_monitor/application").exists())

    def test_research_artifact_emits_actual_raw_bytes_and_binds_selected_metadata(self):
        from copy import deepcopy
        from kiwoom_monitor.application.research_implementation import research_implementation_hash
        from kiwoom_monitor.domain.research_contract import (
            RESEARCH_IMPLEMENTATION_PATHS, SelectedResearchArtifact, research_artifact_metadata_hash,
        )
        output, document = selected_research_artifact_fixture(Path(self.temporary.name))
        raw = output.with_suffix(".zip").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), document["artifact_sha256"])
        self.assertEqual(research_implementation_hash("krx-regular/v1"), document["implementation_hash"])
        # Independent oracle retains the producer's exact path labels/order and bytes.
        oracle = hashlib.sha256()
        with ZipFile(output.with_suffix(".zip")) as archive:
            for path in RESEARCH_IMPLEMENTATION_PATHS:
                body = archive.read(path)
                oracle.update(path.encode("utf-8") + b"\0" + body + b"\0")
        self.assertEqual(oracle.hexdigest(), document["implementation_hash"])
        selected = SelectedResearchArtifact.from_document(document)
        self.assertEqual(oracle.hexdigest(), selected.expected_implementation_hash("krx-regular/v1"))
        self.assertIsNone(selected.expected_implementation_hash("krx-after/v1"))
        for names in ({"market_entry.py"}, {"market_entry.py", "research_discovery.py"}):
            previous = {**document, "files": [row for row in document["files"] if Path(row["path"]).name not in names],
                        "implementation_hash": "a" * 64}
            previous["metadata_hash"] = research_artifact_metadata_hash(previous)
            self.assertEqual("a" * 64, SelectedResearchArtifact.from_document(previous).implementation_hash)
        for field in ("artifact_sha256", "source_ref", "profiles", "implementation_hash"):
            changed = deepcopy(document)
            changed[field] = ["krx-after/v1"] if field == "profiles" else "b" * 64
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "metadata hash conflict"):
                SelectedResearchArtifact.from_document(changed)
        for field, value in (("recipe", "old-recipe"), ("version", 0), ("files", list(reversed(document["files"])))):
            changed = {**document, field: value}
            changed["metadata_hash"] = research_artifact_metadata_hash(changed)
            with self.subTest(field=field), self.assertRaises(ValueError):
                SelectedResearchArtifact.from_document(changed)
        with self.assertRaises(FileExistsError):
            emit_research_artifact(output.with_suffix(".zip"), artifact_kind="source-zip",
                source_ref="other", profiles=("krx-regular/v1",), output=output)

    def test_research_wheel_emission_preserves_crlf_and_rejects_missing_actual_input(self):
        from kiwoom_monitor.domain.research_contract import RESEARCH_IMPLEMENTATION_PATHS
        root = Path(self.temporary.name)
        artifact, output = root / "producer.whl", root / "producer.json"
        corpus = {path: (path + "\r\n").encode("utf-8") for path in RESEARCH_IMPLEMENTATION_PATHS}
        with ZipFile(artifact, "w") as archive:
            for path, body in corpus.items():
                archive.writestr(path.removeprefix("src/"), body)
        metadata = emit_research_artifact(artifact, artifact_kind="wheel", source_ref="test-wheel",
            profiles=("krx-regular/v1",), output=output)
        oracle = hashlib.sha256()
        for path, body in corpus.items():
            oracle.update(path.encode("utf-8") + b"\0" + body + b"\0")
        self.assertEqual(oracle.hexdigest(), metadata["implementation_hash"])
        with ZipFile(artifact, "w") as archive:
            for path, body in list(corpus.items())[:-1]:
                archive.writestr(path.removeprefix("src/"), body)
        with self.assertRaises(KeyError):
            emit_research_artifact(artifact, artifact_kind="wheel", source_ref="test-wheel",
                profiles=("krx-regular/v1",), output=root / "missing.json")
        self.assertFalse((root / "missing.json").exists())

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
                paths = command[command.index("--") + 1:]
                selected = {name for name in self.names
                            if any(name == path or name.startswith(path + "/") for path in paths)}
                return ("\0".join(sorted(selected)) + "\0").encode()
            if command[1] == "status":
                return ""
            return "test-commit\n"
        with patch("scripts.nas_source_runtime.subprocess.check_output", side_effect=git_output):
            return stage(self.source, self.store, "test-image")

    def test_execution_source_excludes_only_development_review_ledger(self):
        ledger = "tests/test_review_changes.jsonl"
        retained = {
            "tests/unit/test_selected_runtime.py": b"# required ordinary test\r\n",
            "tests/fixtures/captured-events.jsonl": b'{"event":"trade"}\r\n',
            "tests/fixtures/test_review_changes.jsonl": b'{"fixture":"same basename"}\n',
            "tests/test_review_changes.jsonl.backup": b'{"fixture":"suffix"}\n',
            "tests/test_review_baseline.json.gz": b"opaque baseline fixture\0",
        }
        source_bytes = {ledger: b'{"id":"development review"}\n', **retained}
        self.names.update(source_bytes)
        for name, raw in source_bytes.items():
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        first = self.publish()
        path, manifest = release(self.store, first["release_id"])
        self.assertNotIn(ledger, manifest["files"])
        self.assertFalse((path / ledger).exists())
        self.assertEqual(source_bytes[ledger], (self.source / ledger).read_bytes())
        for name, raw in retained.items():
            with self.subTest(name=name):
                self.assertEqual(raw, (path / name).read_bytes())
                self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest["files"][name])
        frozen_manifest = (path / "manifest.json").read_bytes()
        before = {name: (self.store / name).read_bytes() for name in ("runtime.json", "runner.py")}
        changed_ledger = b'{"id":"development review amended"}\n'
        (self.source / ledger).write_bytes(changed_ledger)
        second = self.publish()
        self.assertEqual(first["release_id"], second["release_id"])
        self.assertEqual(manifest, release(self.store, second["release_id"])[1])
        self.assertEqual(frozen_manifest, (path / "manifest.json").read_bytes())
        self.assertEqual(before, {name: (self.store / name).read_bytes() for name in before})
        self.assertEqual(changed_ledger, (self.source / ledger).read_bytes())

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

    def test_dependency_or_ddl_change_requires_runtime_update(self):
        self.publish()
        for name in ("pyproject.toml", "requirements.lock.txt", "src/kiwoom_monitor/central_server/central_schema.py"):
            target = self.source / name
            original = target.read_text()
            target.write_text("changed")
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "prepare a new runtime"):
                self.publish()
            target.write_text(original)

    def test_build_recipe_and_migration_clock_changes_use_existing_runtime(self):
        first = self.publish()
        atomic_json(self.store / "active.json", {"format": 1, "release_id": first["release_id"]})
        before = (self.store / "runtime.json").read_bytes()
        for name in ("deploy/synology/server.Dockerfile",
                     "src/kiwoom_monitor/central_server/schema_migrations.py"):
            (self.source / name).write_text("ordinary build/log/clock change\n")
        (self.source / BUILD_FILE).write_text('SERVER_BUILD = "2026.10.16-code-only"\n')
        second = self.publish()
        self.assertNotEqual(release(self.store, first["release_id"])[1]["contract"],
                            release(self.store, second["release_id"])[1]["contract"])
        self.assertEqual(second["release_id"], release(self.store, second["release_id"])[1]["release_id"])
        self.assertEqual(first["release_id"], release(self.store)[1]["release_id"])
        self.assertEqual(before, (self.store / "runtime.json").read_bytes())
        driver = self.store / "releases" / second["release_id"] / "src/kiwoom_monitor/central_server/schema_migrations.py"
        driver.write_text("changed after publication\n")
        with self.assertRaisesRegex(RuntimeError, "release file changed"):
            release(self.store, second["release_id"])

    def test_path_escape_and_non_code_payload_are_rejected(self):
        self.assertEqual(self.source / "requirements.lock.txt", checked_path(self.source, "requirements.lock.txt"))
        for value in ("../data/secret", "/outside", "data/secret", "src/../../outside", "src\\x",
                      "requirements.txt", "requirements.lock.txt.extra", ".env"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                checked_path(self.source, value)
        with self.assertRaisesRegex(RuntimeError, "invalid release ID"):
            release(self.store, "../outside")

    def test_runtime_environment_pins_candidate_source_without_changing_caller(self):
        import os
        result = self.publish()
        def hash_with_image(path):
            if path.parent == Path("/app") and path.name in ("pyproject.toml", "requirements.lock.txt"):
                return digest(self.source / path.name)
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
        for name in ("pyproject.toml", "requirements.lock.txt"):
            (self.source / name).write_text("new dependency\n")
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "dependency"):
                review_schema_update(self.source, self.store)
            (self.source / name).write_text("original\n")
        with patch("scripts.nas_source_runtime.schema_plan", side_effect=[[[20, "old", [], []]], [[20, "edited", [], []], [21, "shadow_checkpoint_frames", [], []]]]):
            with self.assertRaisesRegex(RuntimeError, "append-only"):
                review_schema_update(self.source, self.store)
        self.assertEqual(before, (self.store / "runtime.json").read_bytes())
        self.assertFalse((self.store / "deploy.lock").exists())

    def test_native_payload_binds_lock_and_lock_only_change_preserves_release(self):
        (self.source / "requirements.lock.txt").write_bytes(b"original\n")
        result = self.publish()
        atomic_json(self.store / "active.json", {"format": 1, "release_id": result["release_id"]})
        path, manifest = release(self.store)
        self.assertEqual(CONTRACT_VERSION, manifest["contract_version"])
        self.assertEqual(b"original\n", (path / "requirements.lock.txt").read_bytes())
        self.assertEqual(digest(path / "requirements.lock.txt"), manifest["contract"]["requirements.lock.txt"])
        before = {name: (self.store / name).read_bytes() for name in ("active.json", "runtime.json", "runner.py")}
        (self.source / "requirements.lock.txt").write_text("fastapi==0.141.1\n")
        with self.assertRaisesRegex(RuntimeError, "prepare a new runtime"):
            self.publish()
        self.assertEqual(before, {name: (self.store / name).read_bytes() for name in before})
        self.assertEqual(manifest, release(self.store)[1])
        self.assertEqual([path], list((self.store / "releases").iterdir()))

    def test_legacy_store_stays_readable_and_cannot_admit_new_contract(self):
        result = self.publish()
        current, manifest = release(self.store, result["release_id"])
        manifest.pop("contract_version")
        manifest["contract"].pop("requirements.lock.txt")
        manifest["files"].pop("requirements.lock.txt")
        content = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True).encode()).hexdigest()
        manifest["release_id"] = manifest["server_build"] + "-" + content[:16]
        legacy = current.with_name(manifest["release_id"])
        current.rename(legacy)
        (legacy / "requirements.lock.txt").unlink()
        atomic_json(legacy / "manifest.json", manifest)
        runtime = json.loads((self.store / "runtime.json").read_text())
        runtime.pop("contract_version")
        runtime["contract"].pop("requirements.lock.txt")
        atomic_json(self.store / "runtime.json", runtime)
        atomic_json(self.store / "active.json", {"format": 1, "release_id": manifest["release_id"]})
        before = {name: (self.store / name).read_bytes() for name in ("active.json", "runtime.json", "runner.py")}
        frozen = (legacy / "manifest.json").read_bytes()
        self.assertEqual(manifest, release(self.store)[1])
        with self.assertRaisesRegex(RuntimeError, "contract version changed; prepare a new runtime"):
            self.publish()
        self.assertEqual(before, {name: (self.store / name).read_bytes() for name in before})
        self.assertEqual(frozen, (legacy / "manifest.json").read_bytes())
        atomic_json(self.store / "runtime.json", {**runtime, "contract_version": CONTRACT_VERSION})
        with self.assertRaisesRegex(RuntimeError, "contract version changed"):
            release(self.store)

    def test_image_lock_mismatch_and_missing_lock_are_rejected(self):
        result = self.publish()
        for missing in (False, True):
            def image_digest(path):
                if path == Path("/app/pyproject.toml"):
                    return digest(self.source / "pyproject.toml")
                if path == Path("/app/requirements.lock.txt"):
                    if missing:
                        raise FileNotFoundError(path)
                    return "0" * 64
                return digest(path)
            with self.subTest(missing=missing), patch("scripts.nas_source_runtime.digest", side_effect=image_digest):
                with self.assertRaisesRegex(RuntimeError, "image dependency specification"):
                    runtime_environment(self.store, result["release_id"])

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
