from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from base64 import b64encode
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.persistence.news_ai_backup import NewsAIBackupService
from kiwoom_monitor.infrastructure.persistence.strict_restore import (
    AppDataLease, StrictRestoreCoordinator, StrictRestoreError, _atomic_document,
)
from kiwoom_monitor.infrastructure.persistence.settings_backup import SettingsBackupService


class StrictRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        # Sandbox accounts may not have a usable Windows DPAPI profile.
        for name in ("_protect", "_unprotect"):
            patcher = patch(f"kiwoom_monitor.infrastructure.naver_news.{name}", side_effect=lambda data: data)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _fixture(self, root: Path) -> tuple[StrictRestoreCoordinator, Path, Path]:
        data = root / "data"
        data.mkdir()
        monitor = data / "monitor.sqlite3"
        Database(monitor).initialize()
        with closing(sqlite3.connect(monitor)) as connection:
            with connection:
                connection.execute(
                    "UPDATE settings SET value='4' WHERE key='decimal_strength'",
                )
        settings = root / "settings.json"
        SettingsBackupService(monitor).export_to(settings, include_themes=False)
        document = json.loads(settings.read_text(encoding="utf-8"))
        document["settings"]["decimal_strength"] = "3"
        icon = data / "strength_icons" / "test.png"
        icon.parent.mkdir()
        icon.write_bytes(b"old-icon")
        document["assets"] = [{
            "path": "data/strength_icons/test.png",
            "content": b64encode(b"new-icon").decode("ascii"),
        }]
        settings.write_text(json.dumps(document), encoding="utf-8")
        return StrictRestoreCoordinator(monitor, data / "news.sqlite3"), settings, icon

    @staticmethod
    def _strength(coordinator: StrictRestoreCoordinator) -> str:
        with closing(sqlite3.connect(coordinator.monitor_database)) as connection:
            return connection.execute(
                "SELECT value FROM settings WHERE key='decimal_strength'",
            ).fetchone()[0]

    @staticmethod
    def _news_ai_backup(root: Path, *, shared_article: bool = False) -> Path:
        # Restore tests consume the current exporter without initializing the target.
        source = root / "news_ai_source.sqlite3"
        service = NewsAIBackupService(source)
        if shared_article:
            with closing(sqlite3.connect(source)) as connection:
                with connection:
                    connection.execute(
                        "INSERT INTO news_ai_shared(identity,provider,model,summary,category,"
                        "positive_evidence,negative_evidence,company_impacts,body_hash,analyzed_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        ("article-1", "test", "fixture", "요약", "기타", "[]", "[]", "[]",
                         "fixture", "2026-09-25T00:00:00Z"),
                    )
        backup = root / "news_ai.json"
        service.export_to(backup)
        return backup

    @staticmethod
    def _provenance(settings: Path, *, backup_set: str = "architecture-v1", generation: str = "first") -> dict:
        content = settings.read_bytes()
        return {"format": "google-drive-v2-manifest/v1", "manifest": {
            "format": "kiwoom-monitor-drive-bundle", "version": 2,
            "backup_set": backup_set, "generation": generation,
            "created_at": "2026-11-02T00:00:00Z", "components": {"settings": {
                "file_id": "remote-settings-" + generation, "name": "original-settings.json",
                "sha256": hashlib.sha256(content).hexdigest(), "size": len(content),
            }},
        }}

    def test_current_provenance_survives_restart_and_terminal_noop_without_reapplying(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, settings, icon = self._fixture(Path(directory))
            required = StrictRestoreCoordinator(coordinator.monitor_database, coordinator.news_database,
                                                expected_backup_set="architecture-v1")
            provenance = self._provenance(settings)
            original_source = json.loads(json.dumps(provenance["manifest"]))
            operation = required.stage(settings=settings, themes=None, news_ai=None,
                                       excluded_setting_keys=frozenset(), provenance=provenance)
            first = required.pointer.read_bytes()
            provenance["manifest"]["generation"] = "caller-mutation"
            self.assertEqual(original_source, required._read_pointer()["provenance"]["manifest"])
            with self.assertRaisesRegex(StrictRestoreError, "완료되지 않은"):
                required.stage(settings=settings, themes=None, news_ai=None,
                               excluded_setting_keys=frozenset(), provenance=self._provenance(settings, generation="second"))
            self.assertEqual(first, required.pointer.read_bytes())
            restarted = StrictRestoreCoordinator(coordinator.monitor_database, coordinator.news_database,
                                                 expected_backup_set="architecture-v1")
            preparation = lambda: self.fail("restore authority must precede startup preparation")
            target_checks = []
            with patch.object(restarted, "_input_path", wraps=restarted._input_path) as inputs:
                def check_target():
                    self.assertEqual(["settings", "themes", "news_ai"],
                                     [call.args[1] for call in inputs.call_args_list])
                    self.assertEqual("STAGED", restarted._read_pointer()["state"])
                    self.assertIsNone(restarted._read_pointer()["before"])
                    target_checks.append("checked")

                lease, outcome = restarted.enter_main(startup_preparation=preparation,
                                                       restore_target_check=check_target)
            self.assertEqual(["checked"], target_checks)
            try:
                self.assertEqual("COMMITTED", outcome.state, outcome.message)
                self.assertEqual("3", self._strength(restarted))
                self.assertEqual(b"new-icon", icon.read_bytes())
            finally:
                lease.close()
            terminal = restarted.pointer.read_bytes()
            self.assertEqual(operation, restarted._read_pointer()["operation_id"])
            self.assertEqual(original_source, restarted._read_pointer()["provenance"]["manifest"])
            icon.unlink()
            with patch.object(SettingsBackupService, "import_from_settings_and_themes",
                              side_effect=AssertionError("terminal restore reapplied")):
                lease, outcome = restarted.enter_main(
                    startup_preparation=preparation,
                    restore_target_check=lambda: self.fail("terminal restart checked restore target"),
                )
                lease.close()
            self.assertIsNone(outcome)
            self.assertFalse(icon.exists())
            self.assertEqual(terminal, restarted.pointer.read_bytes())

    def test_current_terminal_allows_a_new_explicit_operation_with_the_same_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, settings, _ = self._fixture(Path(directory))
            required = StrictRestoreCoordinator(coordinator.monitor_database, coordinator.news_database,
                                                expected_backup_set="architecture-v1")
            first = required.stage(settings=settings, themes=None, news_ai=None,
                                   excluded_setting_keys=frozenset(), provenance=self._provenance(settings))
            lease, outcome = required.enter_main()
            lease.close()
            self.assertEqual("COMMITTED", outcome.state)
            first_inputs = required._read_pointer()["inputs"]
            second = required.stage(settings=settings, themes=None, news_ai=None,
                                    excluded_setting_keys=frozenset(), provenance=self._provenance(settings, generation="second"))
            self.assertNotEqual(first, second)
            self.assertEqual(first_inputs, required._read_pointer()["inputs"])
            self.assertEqual("second", required._read_pointer()["provenance"]["manifest"]["generation"])

    def test_current_provenance_is_preserved_through_failed_apply_rollback_and_terminal_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, settings, icon = self._fixture(Path(directory))
            required = StrictRestoreCoordinator(coordinator.monitor_database, coordinator.news_database,
                                                expected_backup_set="architecture-v1")
            required.stage(settings=settings, themes=None, news_ai=None,
                           excluded_setting_keys=frozenset(), provenance=self._provenance(settings))
            first_provenance = required._read_pointer()["provenance"]
            original = SettingsBackupService.import_from_settings_and_themes

            def fail_after_settings(service, *args, **kwargs):
                original(service, *args, **kwargs)
                raise OSError("controlled CURRENT apply failure")

            with patch.object(SettingsBackupService, "import_from_settings_and_themes", new=fail_after_settings):
                lease, outcome = required.enter_main()
                lease.close()
            self.assertEqual("ROLLED_BACK", outcome.state)
            self.assertIn("controlled CURRENT apply failure", outcome.message)
            self.assertEqual("4", self._strength(required))
            self.assertEqual(b"old-icon", icon.read_bytes())
            self.assertEqual(first_provenance, required._read_pointer()["provenance"])
            terminal = required.pointer.read_bytes()
            lease, outcome = required.enter_main(
                restore_target_check=lambda: self.fail("rolled-back terminal checked restore target"),
            )
            lease.close()
            self.assertIsNone(outcome)
            self.assertEqual(terminal, required.pointer.read_bytes())

    def test_restore_target_check_failure_preserves_staged_and_prepared_inputs_and_releases_lease(self):
        for state in ("STAGED", "PREPARED"):
            for with_preparation in (False, True):
                with self.subTest(state=state, preparation=with_preparation), tempfile.TemporaryDirectory() as directory:
                    coordinator, settings, icon = self._fixture(Path(directory))
                    required = StrictRestoreCoordinator(coordinator.monitor_database, coordinator.news_database,
                                                        expected_backup_set="architecture-v1")
                    required.stage(settings=settings, themes=None, news_ai=None,
                                   excluded_setting_keys=frozenset(), provenance=self._provenance(settings))
                    document = required._read_pointer()
                    operation_dir = required.root / "operations" / document["operation_id"]
                    if state == "PREPARED":
                        document["before"] = required._capture_before(document, settings, None, None)
                        document["state"] = state
                        _atomic_document(required.pointer, document)
                    protected = {path: path.read_bytes() for path in (
                        required.monitor_database, required.pointer, icon, *operation_dir.iterdir(),
                    )}
                    names = sorted(path.name for path in operation_dir.iterdir())
                    original = RuntimeError("restore target is not the owned full role")
                    preparation = (lambda: self.fail("pending restore prepared root")) if with_preparation else None
                    with patch.object(required, "_input_path", wraps=required._input_path) as inputs:
                        def reject_target():
                            self.assertEqual(["settings", "themes", "news_ai"],
                                             [call.args[1] for call in inputs.call_args_list])
                            raise original

                        with patch.object(required, "_capture_before", side_effect=AssertionError("target check captured before-image")), \
                                patch("kiwoom_monitor.infrastructure.persistence.strict_restore.shutil.disk_usage",
                                      side_effect=AssertionError("space check preceded target check")):
                            with self.assertRaises(RuntimeError) as raised:
                                required.enter_main(startup_preparation=preparation,
                                                    restore_target_check=reject_target)
                    self.assertIs(original, raised.exception)
                    self.assertEqual(protected, {path: path.read_bytes() for path in protected})
                    self.assertEqual(names, sorted(path.name for path in operation_dir.iterdir()))
                    with AppDataLease(required.data_dir, exclusive=True, timeout=0.05):
                        pass

    def test_interrupted_current_rollback_bypasses_restore_target_check(self):
        for state in ("APPLYING", "ROLLING_BACK"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                coordinator, settings, icon = self._fixture(Path(directory))
                required = StrictRestoreCoordinator(coordinator.monitor_database, coordinator.news_database,
                                                    expected_backup_set="architecture-v1")
                required.stage(settings=settings, themes=None, news_ai=None,
                               excluded_setting_keys=frozenset(), provenance=self._provenance(settings))
                document = required._read_pointer()
                document["before"] = required._capture_before(document, settings, None, None)
                document["state"] = state
                _atomic_document(required.pointer, document)
                with closing(sqlite3.connect(required.monitor_database)) as connection:
                    with connection:
                        connection.execute("UPDATE settings SET value='9' WHERE key='decimal_strength'")
                icon.write_bytes(b"partially-applied")
                lease, outcome = required.enter_main(
                    restore_target_check=lambda: self.fail("interrupted rollback checked restore target"),
                )
                lease.close()
                self.assertEqual("ROLLED_BACK", outcome.state)
                self.assertEqual("4", self._strength(required))
                self.assertEqual(b"old-icon", icon.read_bytes())
                self.assertEqual(document["provenance"], required._read_pointer()["provenance"])

    def test_current_requirement_refuses_untagged_or_foreign_ledgers_before_any_preparation(self):
        for provenance_kind in ("untagged", "missing_tag", "foreign"):
            for state in ("STAGED", "COMMITTED"):
                with self.subTest(provenance=provenance_kind, state=state), tempfile.TemporaryDirectory() as directory:
                    coordinator, settings, icon = self._fixture(Path(directory))
                    provenance = None if provenance_kind == "untagged" else self._provenance(settings, backup_set="legacy")
                    if provenance_kind == "missing_tag":
                        provenance["manifest"].pop("backup_set")
                    coordinator.stage(settings=settings, themes=None, news_ai=None,
                                      excluded_setting_keys=frozenset(), provenance=provenance)
                    if state == "COMMITTED":
                        lease, outcome = coordinator.enter_main()
                        lease.close()
                        self.assertEqual(state, outcome.state)  # Default/source compatibility remains.
                    protected = {path: path.read_bytes() for path in (coordinator.monitor_database, coordinator.pointer, icon)}
                    required = StrictRestoreCoordinator(coordinator.monitor_database, coordinator.news_database,
                                                        expected_backup_set="architecture-v1")
                    for preparation in (None, lambda: self.fail("unaccepted source prepared")):
                        with self.assertRaisesRegex(StrictRestoreError, "Google Drive"):
                            required.enter_main(
                                startup_preparation=preparation,
                                restore_target_check=lambda: self.fail("unaccepted source checked target"),
                            )
                        self.assertEqual(protected, {path: path.read_bytes() for path in protected})
                        with AppDataLease(coordinator.data_dir, exclusive=True, timeout=0.05):
                            pass

    def test_current_source_and_input_tampering_blocks_before_database_or_asset_write(self):
        for corruption in ("generation", "source_fingerprint", "input_fingerprint", "input_bytes"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as directory:
                coordinator, settings, icon = self._fixture(Path(directory))
                required = StrictRestoreCoordinator(coordinator.monitor_database, coordinator.news_database,
                                                    expected_backup_set="architecture-v1")
                required.stage(settings=settings, themes=None, news_ai=None,
                               excluded_setting_keys=frozenset(), provenance=self._provenance(settings))
                document = required._read_pointer()
                if corruption == "generation":
                    document["provenance"]["manifest"]["generation"] = "changed-source"
                elif corruption == "source_fingerprint":
                    document["provenance"]["manifest"]["components"]["settings"]["sha256"] = "0" * 64
                    # A consistent parsed-manifest checksum still cannot relabel local bytes.
                    document["provenance"]["manifest_canonical_sha256"] = required._manifest_digest(document["provenance"]["manifest"])
                elif corruption == "input_fingerprint":
                    document["inputs"]["settings"]["sha256"] = "0" * 64
                else:
                    (required.root / "operations" / document["operation_id"] / "settings.json").write_bytes(b"corrupt")
                if corruption != "input_bytes":
                    _atomic_document(required.pointer, document)
                protected = {path: path.read_bytes() for path in (required.monitor_database, required.pointer, icon)}
                with self.assertRaises(StrictRestoreError):
                    required.enter_main(
                        startup_preparation=lambda: self.fail("corrupt source prepared"),
                        restore_target_check=lambda: self.fail("corrupt source/input checked target"),
                    )
                self.assertEqual(protected, {path: path.read_bytes() for path in protected})
                with AppDataLease(required.data_dir, exclusive=True, timeout=0.05):
                    pass

    def test_current_stage_requires_matching_source_before_publishing_pointer(self):
        for corruption in ("missing", "missing_tag", "foreign", "size", "sha256"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as directory:
                coordinator, settings, icon = self._fixture(Path(directory))
                required = StrictRestoreCoordinator(coordinator.monitor_database, coordinator.news_database,
                                                    expected_backup_set="architecture-v1")
                provenance = None if corruption == "missing" else self._provenance(settings)
                if corruption == "foreign":
                    provenance["manifest"]["backup_set"] = "legacy"
                elif corruption == "missing_tag":
                    provenance["manifest"].pop("backup_set")
                elif corruption == "size":
                    provenance["manifest"]["components"]["settings"]["size"] += 1
                elif corruption == "sha256":
                    provenance["manifest"]["components"]["settings"]["sha256"] = "0" * 64
                protected = {path: path.read_bytes() for path in (required.monitor_database, icon)}
                with self.assertRaises(StrictRestoreError):
                    required.stage(settings=settings, themes=None, news_ai=None,
                                   excluded_setting_keys=frozenset(), provenance=provenance)
                self.assertFalse(required.pointer.exists())
                self.assertEqual(protected, {path: path.read_bytes() for path in protected})

    def test_invalid_pointer_discovered_after_shared_lease_acquisition_releases_that_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, _, _ = self._fixture(Path(directory))
            with patch.object(coordinator, "_read_pointer", side_effect=[None, StrictRestoreError("source rejected")]):
                with self.assertRaisesRegex(StrictRestoreError, "source rejected"):
                    coordinator.enter_main()
            with AppDataLease(coordinator.data_dir, exclusive=True, timeout=0.05):
                pass

    def test_explicit_role_refuses_pending_full_restore_without_mutating_ledger_data_or_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator, settings, icon = self._fixture(Path(directory))
            coordinator.stage(settings=settings, themes=None, news_ai=None, excluded_setting_keys=frozenset())
            protected = {path: path.read_bytes() for path in (coordinator.monitor_database, coordinator.pointer, icon, settings)}
            for preparation in (None, lambda: (lambda: self.fail('role startup prepared over restore'))):
                with self.subTest(preparation=preparation is not None), self.assertRaisesRegex(StrictRestoreError, '조회 역할 폴더'):
                    coordinator.enter_main(startup_preparation=preparation, allow_restore=False)
                self.assertEqual(protected, {path: path.read_bytes() for path in protected})
                with AppDataLease(coordinator.data_dir, exclusive=True, timeout=0.05):
                    pass

    def test_next_start_applies_staged_settings_and_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator, settings, icon = self._fixture(Path(directory))
            coordinator.stage(settings=settings, themes=None, news_ai=None,
                              excluded_setting_keys=frozenset())
            self.assertEqual("4", self._strength(coordinator))
            self.assertEqual(b"old-icon", icon.read_bytes())

            lease, outcome = coordinator.enter_main()
            try:
                self.assertEqual("COMMITTED", outcome.state, outcome.message)
                self.assertEqual("3", self._strength(coordinator))
                self.assertEqual(b"new-icon", icon.read_bytes())
            finally:
                lease.close()

    def test_failure_after_settings_commit_restores_database_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator, settings, icon = self._fixture(Path(directory))
            ai = self._news_ai_backup(Path(directory))
            NewsAIBackupService(coordinator.news_database)
            coordinator.stage(settings=settings, themes=None, news_ai=ai,
                              excluded_setting_keys=frozenset())

            with patch.object(NewsAIBackupService, "import_from", side_effect=OSError("injected")):
                lease, outcome = coordinator.enter_main()
            try:
                self.assertEqual("ROLLED_BACK", outcome.state)
                self.assertEqual("4", self._strength(coordinator))
                self.assertEqual(b"old-icon", icon.read_bytes())
                self.assertTrue(coordinator.news_database.is_file())
            finally:
                lease.close()

    def test_missing_news_database_can_be_created_by_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator, _, _ = self._fixture(Path(directory))
            ai = self._news_ai_backup(Path(directory))
            self.assertFalse(coordinator.news_database.exists())
            coordinator.stage(settings=None, themes=None, news_ai=ai,
                              excluded_setting_keys=frozenset())
            lease, outcome = coordinator.enter_main()
            try:
                self.assertEqual("COMMITTED", outcome.state)
                self.assertTrue(coordinator.news_database.is_file())
            finally:
                lease.close()

    def test_combined_settings_theme_and_news_ai_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, settings, icon = self._fixture(root)
            themes = root / "themes.json"
            with closing(sqlite3.connect(coordinator.monitor_database)) as connection:
                with connection:
                    connection.execute("INSERT INTO theme_profiles(profile_name) VALUES('복원할 프로필')")
            SettingsBackupService(coordinator.monitor_database).export_to(
                themes, include_settings=False, include_themes=True,
            )
            with closing(sqlite3.connect(coordinator.monitor_database)) as connection:
                with connection:
                    connection.execute("DELETE FROM theme_profiles WHERE profile_name='복원할 프로필'")
            ai = self._news_ai_backup(root, shared_article=True)
            coordinator.stage(settings=settings, themes=themes, news_ai=ai,
                              excluded_setting_keys=frozenset())
            lease, outcome = coordinator.enter_main()
            try:
                self.assertEqual("COMMITTED", outcome.state, outcome.message)
                self.assertEqual("3", self._strength(coordinator))
                self.assertEqual(b"new-icon", icon.read_bytes())
                with closing(sqlite3.connect(coordinator.monitor_database)) as connection:
                    self.assertEqual(1, connection.execute(
                        "SELECT COUNT(*) FROM theme_profiles WHERE profile_name='복원할 프로필'",
                    ).fetchone()[0])
                with closing(sqlite3.connect(coordinator.news_database)) as connection:
                    self.assertEqual("요약", connection.execute(
                        "SELECT summary FROM news_ai_shared WHERE identity='article-1'",
                    ).fetchone()[0])
            finally:
                lease.close()

    def test_failed_restore_removes_newly_created_news_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator, _, _ = self._fixture(Path(directory))
            ai = self._news_ai_backup(Path(directory))
            coordinator.stage(settings=None, themes=None, news_ai=ai,
                              excluded_setting_keys=frozenset())

            def fail_after_create(service: NewsAIBackupService, path: Path) -> None:
                NewsAIBackupService(service._database_path)
                raise OSError("injected")

            with patch.object(NewsAIBackupService, "import_from", fail_after_create):
                lease, outcome = coordinator.enter_main()
            try:
                self.assertEqual("ROLLED_BACK", outcome.state)
                self.assertFalse(coordinator.news_database.exists())
            finally:
                lease.close()

    def test_interrupted_apply_recovers_before_normal_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator, settings, icon = self._fixture(Path(directory))
            coordinator.stage(settings=settings, themes=None, news_ai=None,
                              excluded_setting_keys=frozenset())
            manifest = coordinator._read_pointer()
            manifest["before"] = coordinator._capture_before(manifest, settings, None, None)
            manifest["state"] = "APPLYING"
            _atomic_document(coordinator.pointer, manifest)
            with closing(sqlite3.connect(coordinator.monitor_database)) as connection:
                with connection:
                    connection.execute("UPDATE settings SET value='3' WHERE key='decimal_strength'")
            icon.write_bytes(b"partially-new")

            lease, outcome = coordinator.enter_main()
            try:
                self.assertEqual("ROLLED_BACK", outcome.state)
                self.assertEqual("4", self._strength(coordinator))
                self.assertEqual(b"old-icon", icon.read_bytes())
            finally:
                lease.close()

    def test_process_termination_during_asset_apply_recovers_on_next_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator, settings, icon = self._fixture(Path(directory))
            coordinator.stage(settings=settings, themes=None, news_ai=None,
                              excluded_setting_keys=frozenset())
            code = "\n".join((
                "import os,sys",
                "from pathlib import Path",
                "from kiwoom_monitor.infrastructure.persistence.settings_backup import SettingsBackupService",
                "from kiwoom_monitor.infrastructure.persistence.strict_restore import StrictRestoreCoordinator",
                "original=SettingsBackupService._import_assets",
                "def crash_after_asset(self, assets):",
                "    original(self, assets)",
                "    os._exit(23)",
                "SettingsBackupService._import_assets=crash_after_asset",
                "StrictRestoreCoordinator(Path(sys.argv[1]),Path(sys.argv[2])).enter_main()",
            ))
            environment = dict(os.environ)
            repository = Path(__file__).resolve().parents[2]
            environment["PYTHONPATH"] = str(repository / "src") + os.pathsep + environment.get("PYTHONPATH", "")
            result = subprocess.run(
                [sys.executable, "-c", code, str(coordinator.monitor_database),
                 str(coordinator.news_database)],
                cwd=repository, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(23, result.returncode, result.stderr)
            self.assertEqual("APPLYING", coordinator._read_pointer()["state"])
            self.assertEqual("3", self._strength(coordinator))
            self.assertEqual(b"new-icon", icon.read_bytes())
            lease, outcome = coordinator.enter_main()
            try:
                self.assertEqual("ROLLED_BACK", outcome.state)
                self.assertEqual("4", self._strength(coordinator))
                self.assertEqual(b"old-icon", icon.read_bytes())
            finally:
                lease.close()

    def test_corrupt_before_image_blocks_start_without_guessing_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            coordinator, settings, _ = self._fixture(Path(directory))
            coordinator.stage(settings=settings, themes=None, news_ai=None,
                              excluded_setting_keys=frozenset())
            manifest = coordinator._read_pointer()
            manifest["before"] = coordinator._capture_before(manifest, settings, None, None)
            manifest["state"] = "APPLYING"
            _atomic_document(coordinator.pointer, manifest)
            saved = coordinator.root / "operations" / manifest["operation_id"] / "before_monitor.sqlite3"
            saved.write_bytes(b"damaged")
            with self.assertRaises(StrictRestoreError):
                coordinator.enter_main()
            self.assertEqual("APPLYING", coordinator._read_pointer()["state"])

    def test_active_reader_prevents_restore_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with AppDataLease(root, exclusive=False):
                with self.assertRaises(StrictRestoreError):
                    AppDataLease(root, exclusive=True, timeout=0.05)
            with AppDataLease(root, exclusive=True, timeout=0.05):
                pass

    def test_other_process_reader_blocks_exclusive_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = "\n".join((
                "import sys",
                "from pathlib import Path",
                "from kiwoom_monitor.infrastructure.persistence.strict_restore import AppDataLease,StrictRestoreError",
                "try:",
                "    with AppDataLease(Path(sys.argv[1]),exclusive=True,timeout=0.1): pass",
                "except StrictRestoreError:",
                "    sys.exit(17)",
            ))
            environment = dict(os.environ)
            repository = Path(__file__).resolve().parents[2]
            environment["PYTHONPATH"] = str(repository / "src") + os.pathsep + environment.get("PYTHONPATH", "")
            with AppDataLease(root, exclusive=False):
                result = subprocess.run(
                    [sys.executable, "-c", code, str(root)], cwd=repository,
                    env=environment, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(17, result.returncode, result.stderr)
            result = subprocess.run(
                [sys.executable, "-c", code, str(root)], cwd=repository,
                env=environment, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)

    def _absent_sound_fixture(self, root):
        coordinator, settings, _ = self._fixture(root)
        document = json.loads(settings.read_text(encoding='utf-8'))
        document['assets'] = [{'path': 'data/near_high_sounds/interest.mp3',
                               'content': b64encode(b'accepted-sound').decode('ascii')}]
        settings.write_text(json.dumps(document), encoding='utf-8')
        return coordinator, settings, coordinator.data_dir / 'near_high_sounds/interest.mp3'

    def _frozen_preparation(self, root, coordinator, *, query_only):
        from kiwoom_monitor.infrastructure.app_paths import AppPaths
        executable = root / 'installation/KiwoomMonitor.exe'
        legacy = executable.parent / 'data'
        (legacy / 'near_high_sounds').mkdir(parents=True)
        (legacy / 'near_high_sounds/interest.mp3').write_bytes(b'legacy-default')
        (legacy / 'api.env').write_bytes(b'legacy-private')
        paths = AppPaths(coordinator.data_dir, coordinator.data_dir / 'logs', coordinator.monitor_database)
        self.enterContext(patch.object(sys, 'frozen', True, create=True))
        self.enterContext(patch.object(sys, 'executable', str(executable)))
        from unittest.mock import Mock
        return Mock(wraps=lambda: paths.desktop_startup_preparation(query_only=query_only))

    def test_committed_ledger_preserves_intentionally_absent_asset_on_repeated_query_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, settings, target = self._absent_sound_fixture(root)
            coordinator.stage(settings=settings, themes=None, news_ai=None, excluded_setting_keys=frozenset())
            lease, outcome = coordinator.enter_main()
            lease.close()
            self.assertEqual('COMMITTED', outcome.state)
            self.assertEqual(b'accepted-sound', target.read_bytes())
            before_file = next(item for item in coordinator._read_pointer()['before']['files']
                               if item['path'] == 'near_high_sounds/interest.mp3')
            self.assertIs(False, before_file['existed'])
            target.unlink()  # User's post-restore absence must remain authoritative.
            pointer = coordinator.pointer.read_bytes()
            preparation = self._frozen_preparation(root, coordinator, query_only=True)
            for _ in range(2):
                lease, outcome = coordinator.enter_main(startup_preparation=preparation)
                lease.close()
                self.assertIsNone(outcome)
                self.assertFalse(target.exists())
                self.assertEqual(pointer, coordinator.pointer.read_bytes())
            preparation.assert_not_called()

    def test_rollback_before_absence_is_not_reseeded_in_recovery_or_terminal_restarts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator, settings, target = self._absent_sound_fixture(root)
            coordinator.stage(settings=settings, themes=None, news_ai=None, excluded_setting_keys=frozenset())
            manifest = coordinator._read_pointer()
            manifest['before'] = coordinator._capture_before(manifest, settings, None, None)
            self.assertIs(False, next(item for item in manifest['before']['files']
                                     if item['path'] == 'near_high_sounds/interest.mp3')['existed'])
            manifest['state'] = 'APPLYING'
            _atomic_document(coordinator.pointer, manifest)
            target.parent.mkdir()
            target.write_bytes(b'partially-applied')
            preparation = self._frozen_preparation(root, coordinator, query_only=False)
            lease, outcome = coordinator.enter_main(startup_preparation=preparation)
            lease.close()
            self.assertEqual('ROLLED_BACK', outcome.state)
            self.assertEqual('4', self._strength(coordinator))
            self.assertFalse(target.exists())
            self.assertFalse((coordinator.data_dir / 'api.env').exists())
            pointer = coordinator.pointer.read_bytes()
            for _ in range(2):
                lease, outcome = coordinator.enter_main(startup_preparation=preparation)
                lease.close()
                self.assertIsNone(outcome)
                self.assertFalse(target.exists())
                self.assertFalse((coordinator.data_dir / 'api.env').exists())
                self.assertEqual(pointer, coordinator.pointer.read_bytes())
            preparation.assert_not_called()

    def test_staged_and_prepared_restore_run_without_any_automatic_import(self):
        for state in ('STAGED', 'PREPARED'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                coordinator, settings, target = self._absent_sound_fixture(root)
                coordinator.stage(settings=settings, themes=None, news_ai=None, excluded_setting_keys=frozenset())
                if state == 'PREPARED':
                    manifest = coordinator._read_pointer()
                    manifest['before'] = coordinator._capture_before(manifest, settings, None, None)
                    manifest['state'] = state
                    _atomic_document(coordinator.pointer, manifest)
                preparation = self._frozen_preparation(root, coordinator, query_only=False)
                lease, outcome = coordinator.enter_main(startup_preparation=preparation)
                lease.close()
                self.assertEqual('COMMITTED', outcome.state)
                self.assertEqual('3', self._strength(coordinator))
                self.assertEqual(b'accepted-sound', target.read_bytes())
                self.assertFalse((coordinator.data_dir / 'api.env').exists())
                preparation.assert_not_called()

    def test_corrupt_pointer_and_before_image_reject_start_without_preparation_or_lock_leak(self):
        for failure in ('pointer', 'before-image'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                coordinator, settings, target = self._absent_sound_fixture(root)
                coordinator.stage(settings=settings, themes=None, news_ai=None, excluded_setting_keys=frozenset())
                if failure == 'pointer':
                    coordinator.pointer.write_bytes(b'{invalid')
                else:
                    manifest = coordinator._read_pointer()
                    manifest['before'] = coordinator._capture_before(manifest, settings, None, None)
                    manifest['state'] = 'APPLYING'
                    _atomic_document(coordinator.pointer, manifest)
                    (coordinator.root / 'operations' / manifest['operation_id'] / 'before_monitor.sqlite3').write_bytes(b'corrupt')
                pointer = coordinator.pointer.read_bytes()
                preparation = self._frozen_preparation(root, coordinator, query_only=False)
                with self.assertRaises(StrictRestoreError):
                    coordinator.enter_main(startup_preparation=preparation)
                preparation.assert_not_called()
                self.assertFalse(target.exists())
                self.assertFalse((coordinator.data_dir / 'api.env').exists())
                self.assertEqual(pointer, coordinator.pointer.read_bytes())
                with AppDataLease(coordinator.data_dir, exclusive=True, timeout=0.05):
                    pass

    def test_preparation_probe_and_action_errors_preserve_exception_and_release_native_lease(self):
        for stage in ('probe', 'action'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                coordinator = StrictRestoreCoordinator(root / 'monitor.sqlite3', root / 'news.sqlite3')
                original = RuntimeError('preparation-' + stage)
                def fail(): raise original
                preparation = fail if stage == 'probe' else lambda: fail
                with self.assertRaises(RuntimeError) as caught:
                    coordinator.enter_main(startup_preparation=preparation)
                self.assertIs(original, caught.exception)
                self.assertFalse(coordinator.monitor_database.exists())
                self.assertFalse(coordinator.news_database.exists())
                with AppDataLease(root, exclusive=True, timeout=0.05):
                    pass

    def test_no_work_retains_shared_fastpath_with_an_existing_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = StrictRestoreCoordinator(root / 'monitor.sqlite3', root / 'news.sqlite3')
            native = AppDataLease
            modes = []
            def tracked(*args, **kwargs):
                modes.append(kwargs['exclusive'])
                return native(*args, **kwargs)
            with AppDataLease(root, exclusive=False):
                with patch('kiwoom_monitor.infrastructure.persistence.strict_restore.AppDataLease', side_effect=tracked):
                    lease, outcome = coordinator.enter_main(startup_preparation=lambda: None)
                self.assertEqual([False], modes)
                self.assertIsNone(outcome)
                lease.close()

    def test_two_simultaneous_main_entries_recheck_work_and_register_shared_lifetimes(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'prepared.txt'
            probes = threading.Barrier(2)
            registered = threading.Barrier(2)
            guard = threading.Lock()
            observations, actions, registrations = [], [], []
            def action():
                with self.assertRaises(StrictRestoreError):
                    AppDataLease(root, exclusive=False, timeout=0.05)
                actions.append('copy')
                marker.write_bytes(b'prepared-once')
            def preparation():
                with guard:
                    observations.append(threading.get_ident())
                    first = len(observations) <= 2
                if first: probes.wait(3)
                return None if marker.exists() else action
            def enter():
                coordinator = StrictRestoreCoordinator(root / 'monitor.sqlite3', root / 'news.sqlite3')
                lease, _ = coordinator.enter_main(timeout=3, startup_preparation=preparation)
                try:
                    registrations.append(threading.get_ident())
                    registered.wait(3)  # First main retains its real shared lifetime.
                finally:
                    lease.close()
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(enter) for _ in range(2)]
                for future in futures: future.result(timeout=5)
            self.assertEqual(['copy'], actions)
            self.assertEqual(2, len(registrations))
            self.assertEqual(b'prepared-once', marker.read_bytes())
            with AppDataLease(root, exclusive=True, timeout=0.05):
                pass

    def test_restore_staged_during_preparation_probe_wins_before_exclusive_copy(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            coordinator, settings, target = self._absent_sound_fixture(Path(directory))
            probing, staged = threading.Event(), threading.Event()
            calls = []
            def stage():
                self.assertTrue(probing.wait(3))
                coordinator.stage(settings=settings, themes=None, news_ai=None, excluded_setting_keys=frozenset())
                staged.set()
            def preparation():
                probing.set()
                self.assertTrue(staged.wait(3))
                return lambda: calls.append('forbidden-copy')
            with ThreadPoolExecutor(max_workers=1) as pool:
                other = pool.submit(stage)
                lease, outcome = coordinator.enter_main(startup_preparation=preparation)
                lease.close()
                other.result(timeout=5)
            self.assertEqual([], calls)
            self.assertEqual('COMMITTED', outcome.state)
            self.assertEqual(b'accepted-sound', target.read_bytes())
            self.assertEqual('3', self._strength(coordinator))


    def test_source_restore_closes_before_selective_current_state_seed_and_keeps_recovery_on_source(self):
        from datetime import datetime
        from scripts import preserve_current_settings as operator

        fixed = datetime.fromisoformat("2026-10-27T10:00:00+09:00")
        original_version = "2026-10-26T08:00:00+09:00"
        cases = (("STAGED", "COMMITTED", "3", b"new-icon"),
                 ("APPLYING", "ROLLED_BACK", "4", b"old-icon"))
        for starting_state, terminal_state, expected_value, expected_asset in cases:
            with self.subTest(source_state=starting_state), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                coordinator, settings, icon = self._fixture(root)
                with closing(sqlite3.connect(coordinator.monitor_database)) as connection, connection:
                    connection.execute(
                        "INSERT INTO central_setting_versions(setting_key,updated_at) VALUES(?,?) "
                        "ON CONFLICT(setting_key) DO UPDATE SET updated_at=excluded.updated_at",
                        ("decimal_strength", original_version),
                    )
                coordinator.stage(settings=settings, themes=None, news_ai=None,
                                  excluded_setting_keys=frozenset())
                if starting_state == "APPLYING":
                    # Reuse the existing interrupted-apply fixture; no new crash policy.
                    pending = coordinator._read_pointer()
                    pending["before"] = coordinator._capture_before(pending, settings, None, None)
                    pending["state"] = "APPLYING"
                    _atomic_document(coordinator.pointer, pending)
                    with closing(sqlite3.connect(coordinator.monitor_database)) as connection, connection:
                        connection.execute("UPDATE settings SET value='3' WHERE key='decimal_strength'")
                    icon.write_bytes(b"partially-new")
                self.assertEqual(starting_state, coordinator._read_pointer()["state"])

                # The existing source owner settles its own accepted restore first.
                lease, outcome = coordinator.enter_main()
                try:
                    self.assertEqual(terminal_state, outcome.state, outcome.message)
                    self.assertEqual(expected_value, self._strength(coordinator))
                    self.assertEqual(expected_asset, icon.read_bytes())
                finally:
                    lease.close()
                terminal = coordinator.pointer.read_bytes()
                self.assertEqual(terminal_state, json.loads(terminal)["state"])
                operation_files = tuple(path for path in coordinator.root.rglob("*") if path.is_file())
                protected = {path: path.read_bytes() for path in
                             (coordinator.monitor_database, settings, icon, *operation_files)}
                artifact = root / "selected-cut"
                # Successful exclusive acquisition proves the source lifetime lease ended.
                # This is fixture orchestration, not a new runtime/operator gate.
                with AppDataLease(coordinator.data_dir, exclusive=True, timeout=0.05):
                    operator.capture_current_state(database=coordinator.monitor_database,
                        source_instance="fixture-source", source_owner="fixture-restore-owner",
                        operation_id="fixture-closed-restore-cut", output=artifact,
                        sqlite_frozen=True, artifact_now=fixed)
                manifest, records, digest = operator.verified_current_state_rows(artifact)
                entries = []
                for key, item in records.items():
                    row = tuple(operator.decode_value(value) for value in item["row"])
                    chosen = (item["relation"] in {"settings", "central_setting_versions"}
                              and row[0] == "decimal_strength")
                    entries.append(dict(record_id=key, placement="TRANSFER" if chosen else "RETAIN_SOURCE",
                                        **({"target_role": "query-local"} if chosen else {})))
                selection = dict(format="current-state-selection/v1",
                    source_instance=manifest["source_instance"], source_owner=manifest["source_owner"],
                    operation_id=manifest["operation_id"], manifest_sha256=digest,
                    target_role="query-local", records=entries)
                selected = operator.select_current_state_rows(artifact, selection)
                self.assertEqual("CLOSED", selected["closure"]["state"])
                self.assertEqual(0, selected["closure"]["summary"]["blocking"])
                self.assertEqual([("decimal_strength", expected_value)], selected["rows"]["settings"])
                self.assertEqual([("decimal_strength", original_version)],
                                 selected["rows"]["central_setting_versions"])
                self.assertGreater(selected["binding"]["retained_source"], 0)
                target = root / "query" / "monitor.sqlite3"
                target.parent.mkdir()
                seeded = operator.install_current_state(artifact=artifact, selection=selection,
                    target=target, operation_id="fixture-selective-install",
                    target_id="fixture-query-target", receipt_now=fixed)
                self.assertEqual("SELECTED_USER_STATE", seeded["seed_kind"])
                self.assertEqual("OFFLINE_UNBOUND", seeded["runtime_state"])
                for flag in ("applied", "role_ready", "order_authority"):
                    self.assertFalse(seeded[flag])
                reopened = Database(target)
                self.assertEqual(seeded["receipt"], reopened.initialize_query_role(role="query-local")["receipt"])
                self.assertEqual(expected_value, reopened.settings.get("decimal_strength"))
                with closing(sqlite3.connect(target)) as connection:
                    self.assertEqual((original_version,), connection.execute(
                        "SELECT updated_at FROM central_setting_versions WHERE setting_key='decimal_strength'",
                    ).fetchone())
                    self.assertEqual(1, connection.execute("SELECT COUNT(*) FROM settings").fetchone()[0])
                # Never transplant/replay the legacy restore ledger/assets into a query root.
                target_restore = StrictRestoreCoordinator(target, target.with_name("news.sqlite3"))
                target_lease, target_outcome = target_restore.enter_main(allow_restore=False)
                try:
                    self.assertIsNone(target_outcome)
                    self.assertFalse(target_restore.pointer.exists())
                finally:
                    target_lease.close()
                self.assertFalse((target.parent / "strength_icons").exists())
                self.assertEqual(terminal, coordinator.pointer.read_bytes())
                self.assertEqual(protected, {path: path.read_bytes() for path in protected})
                self.assertEqual(seeded["receipt"], operator.install_current_state(
                    artifact=artifact, selection=selection, target=target,
                    operation_id="fixture-selective-install", target_id="fixture-query-target",
                    receipt_now=fixed)["receipt"])


if __name__ == "__main__":
    unittest.main()
