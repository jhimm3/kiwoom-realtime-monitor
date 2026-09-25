from __future__ import annotations

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
            ai = Path(directory) / "news_ai.json"
            ai.write_text(json.dumps({
                "format": NewsAIBackupService.FORMAT,
                "version": NewsAIBackupService.VERSION,
                "analyses": [], "shared_analyses": [],
            }), encoding="utf-8")
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
            ai = Path(directory) / "news_ai.json"
            ai.write_text(json.dumps({
                "format": NewsAIBackupService.FORMAT,
                "version": NewsAIBackupService.VERSION,
                "analyses": [], "shared_analyses": [],
            }), encoding="utf-8")
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
            ai = root / "news_ai.json"
            ai.write_text(json.dumps({
                "format": NewsAIBackupService.FORMAT,
                "version": NewsAIBackupService.VERSION,
                "analyses": [],
                "shared_analyses": [{
                    "identity": "article-1", "provider": "test", "model": "fixture",
                    "summary": "요약", "category": "기타", "positive_evidence": "[]",
                    "negative_evidence": "[]", "company_impacts": "[]",
                    "body_hash": "fixture", "analyzed_at": "2026-09-25T00:00:00Z",
                }],
            }), encoding="utf-8")
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
            ai = Path(directory) / "news_ai.json"
            ai.write_text(json.dumps({
                "format": NewsAIBackupService.FORMAT,
                "version": NewsAIBackupService.VERSION,
                "analyses": [], "shared_analyses": [],
            }), encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
