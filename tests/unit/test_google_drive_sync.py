from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.persistence.google_drive_sync import GoogleDriveSyncError, GoogleDriveSyncService
from kiwoom_monitor.infrastructure.persistence.news_ai_backup import NewsAIBackupService
from kiwoom_monitor.infrastructure.persistence.settings_backup import SettingsBackupService
from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupService


class GoogleDriveSyncServiceTest(unittest.TestCase):
    def test_large_backup_upload_uses_resumable_chunks(self) -> None:
        from kiwoom_monitor.infrastructure.persistence.google_drive_sync import GoogleDriveSyncService

        with patch(
            "googleapiclient.http.MediaIoBaseUpload",
            side_effect=lambda stream, **kwargs: (stream, kwargs),
            create=True,
        ) as media_upload:
            GoogleDriveSyncService._media_upload(
                b"x" * GoogleDriveSyncService.RESUMABLE_UPLOAD_THRESHOLD_BYTES,
            )

        self.assertEqual(
            {
                "mimetype": "application/json",
                "resumable": True,
                "chunksize": GoogleDriveSyncService.RESUMABLE_UPLOAD_CHUNK_BYTES,
            },
            media_upload.call_args.kwargs,
        )

    def test_component_upload_failure_keeps_previous_manifest(self) -> None:
        class Request:
            def execute(self, **_kwargs) -> dict[str, object]:
                return {"id": "new-component"}

        class Files:
            def __init__(self) -> None:
                self.component_creates = 0
                self.manifest_bytes = b"previous-manifest"
                self.manifest_updates = 0

            def create(self, *, body, media_body, fields):
                name = str(body["name"])
                if name.startswith("kiwoom-monitor-v2-"):
                    self.component_creates += 1
                    if self.component_creates == 2:
                        raise OSError("component upload interrupted")
                return Request()

            def update(self, *, fileId, media_body, **_kwargs):
                if fileId == "manifest-id":
                    self.manifest_bytes = media_body
                    self.manifest_updates += 1
                return Request()

        class Drive:
            def __init__(self) -> None:
                self.api = Files()

            def files(self):
                return self.api

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            drive = Drive()
            service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3")

            def write_backup(_service, path, *_args, **_kwargs) -> None:
                path.write_text("{}", encoding="utf-8")

            with patch.object(service, "_drive_service", return_value=drive), \
                    patch.object(service, "_ensure_sync_folder", return_value="folder"), \
                    patch.object(service, "_find_remote_file", side_effect=lambda _drive, _folder, name: "manifest-id" if name == service.MANIFEST_REMOTE_NAME else None), \
                    patch.object(service, "_components_from_previous_generation", return_value={}), \
                    patch.object(service, "_media_upload", side_effect=lambda content: content), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.export_to", write_backup), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.NewsAIBackupService.export_to", write_backup):
                with self.assertRaises(GoogleDriveSyncError):
                    service.upload(target="settings")

            self.assertEqual(2, drive.api.component_creates)
            self.assertEqual(b"previous-manifest", drive.api.manifest_bytes)
            self.assertEqual(0, drive.api.manifest_updates)

    def test_legacy_alias_failure_does_not_advance_v2_manifest(self) -> None:
        class Request:
            def __init__(self, file_id: str) -> None:
                self.file_id = file_id

            def execute(self, **_kwargs) -> dict[str, object]:
                return {"id": self.file_id}

        class Files:
            def __init__(self) -> None:
                self.manifest_bytes = b"previous-manifest"
                self.manifest_updates = 0
                self.created = 0

            def create(self, *, body, media_body, fields):
                self.created += 1
                return Request(f"new-{self.created}")

            def update(self, *, fileId, media_body, **_kwargs):
                if fileId == "legacy-settings":
                    raise OSError("legacy alias update interrupted")
                if fileId == "manifest-id":
                    self.manifest_bytes = media_body
                    self.manifest_updates += 1
                return Request(fileId)

        class Drive:
            def __init__(self) -> None:
                self.api = Files()

            def files(self):
                return self.api

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            drive = Drive()
            service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3")

            def write_backup(_service, path, *_args, **_kwargs) -> None:
                path.write_text("{}", encoding="utf-8")

            def find_remote(_drive, _folder, name):
                return {
                    service.MANIFEST_REMOTE_NAME: "manifest-id",
                    service.SETTINGS_REMOTE_NAME: "legacy-settings",
                }.get(name)

            with patch.object(service, "_drive_service", return_value=drive), \
                    patch.object(service, "_ensure_sync_folder", return_value="folder"), \
                    patch.object(service, "_find_remote_file", side_effect=find_remote), \
                    patch.object(service, "_components_from_previous_generation", return_value={}), \
                    patch.object(service, "_media_upload", side_effect=lambda content: content), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.export_to", write_backup), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.NewsAIBackupService.export_to", write_backup):
                with self.assertRaises(GoogleDriveSyncError):
                    service.upload(target="settings")

            self.assertEqual(b"previous-manifest", drive.api.manifest_bytes)
            self.assertEqual(0, drive.api.manifest_updates)

    def test_manifest_publish_failure_keeps_previous_generation_selected(self) -> None:
        class Request:
            def __init__(self, file_id: str) -> None:
                self.file_id = file_id

            def execute(self, **_kwargs) -> dict[str, object]:
                return {"id": self.file_id}

        class Files:
            def __init__(self) -> None:
                self.manifest_bytes = b"previous-manifest"
                self.manifest_update_attempts = 0
                self.created_names: list[str] = []

            def create(self, *, body, media_body, fields):
                name = str(body["name"])
                self.created_names.append(name)
                return Request(f"new-{len(self.created_names)}")

            def update(self, *, fileId, media_body, **_kwargs):
                if fileId == "manifest-id":
                    self.manifest_update_attempts += 1
                    raise OSError("manifest publish interrupted")
                return Request(fileId)

        class Drive:
            def __init__(self) -> None:
                self.api = Files()

            def files(self):
                return self.api

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            drive = Drive()
            service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3")

            def write_backup(_service, path, *_args, **_kwargs) -> None:
                path.write_text("{}", encoding="utf-8")

            def find_remote(_drive, _folder, name):
                return {
                    service.MANIFEST_REMOTE_NAME: "manifest-id",
                    service.SETTINGS_REMOTE_NAME: "legacy-settings",
                    service.NEWS_AI_REMOTE_NAME: "legacy-news-ai",
                }.get(name)

            with patch.object(service, "_drive_service", return_value=drive), \
                    patch.object(service, "_ensure_sync_folder", return_value="folder"), \
                    patch.object(service, "_find_remote_file", side_effect=find_remote), \
                    patch.object(service, "_components_from_previous_generation", return_value={}), \
                    patch.object(service, "_media_upload", side_effect=lambda content: content), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.export_to", write_backup), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.NewsAIBackupService.export_to", write_backup):
                with self.assertRaises(GoogleDriveSyncError):
                    service.upload(target="settings")

        self.assertEqual(1, drive.api.manifest_update_attempts)
        self.assertEqual(b"previous-manifest", drive.api.manifest_bytes)
        self.assertEqual(2, len(drive.api.created_names))
        self.assertTrue(all(name.startswith("kiwoom-monitor-v2-") for name in drive.api.created_names))

    def test_v2_component_hash_and_size_mismatch_abort_before_import(self) -> None:
        settings = json.dumps({
            "format": SettingsBackupService.FORMAT,
            "version": SettingsBackupService.VERSION,
            "settings": {}, "columns": [],
        }, separators=(",", ":")).encode("utf-8")
        news_ai = json.dumps({
            "format": NewsAIBackupService.FORMAT,
            "version": NewsAIBackupService.VERSION,
            "analyses": [],
        }, separators=(",", ":")).encode("utf-8")

        class Files:
            def get_media(self, *, fileId):
                return fileId

        class Drive:
            def files(self):
                return Files()

        for failure in ("hash", "size"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3")
                components = {
                    "settings": {
                        "file_id": "settings-id", "name": "settings.json",
                        "sha256": hashlib.sha256(settings).hexdigest(),
                        "size": len(settings) + (1 if failure == "size" else 0),
                    },
                    "news_ai": {
                        "file_id": "news-ai-id", "name": "news-ai.json",
                        "sha256": hashlib.sha256(news_ai).hexdigest(), "size": len(news_ai),
                    },
                }
                manifest = json.dumps({
                    "format": service.BUNDLE_FORMAT, "version": 2,
                    "generation": "generation-1", "components": components,
                }).encode("utf-8")
                remote = {
                    "manifest-id": manifest,
                    "settings-id": b"x" * len(settings) if failure == "hash" else settings,
                    "news-ai-id": news_ai,
                }

                with patch.object(service, "_drive_service", return_value=Drive()), \
                        patch.object(service, "_find_sync_folder", return_value="folder"), \
                        patch.object(service, "_find_remote_file", return_value="manifest-id"), \
                        patch.object(service, "_download_remote_bytes", side_effect=lambda _drive, file_id: remote[file_id]), \
                        patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.import_from_settings_and_themes") as settings_import, \
                        patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.NewsAIBackupService.import_from") as ai_import:
                    with self.assertRaises(GoogleDriveSyncError):
                        service.download(target="settings")

                settings_import.assert_not_called()
                ai_import.assert_not_called()

    def test_v2_download_ignores_unreferenced_orphan_generation(self) -> None:
        settings = json.dumps({
            "format": SettingsBackupService.FORMAT,
            "version": SettingsBackupService.VERSION,
            "settings": {}, "columns": [],
        }).encode("utf-8")
        news_ai = json.dumps({
            "format": NewsAIBackupService.FORMAT,
            "version": NewsAIBackupService.VERSION,
            "analyses": [],
        }).encode("utf-8")
        manifest = json.dumps({
            "format": GoogleDriveSyncService.BUNDLE_FORMAT,
            "version": 2,
            "generation": "previous-generation",
            "components": {
                "settings": {
                    "file_id": "previous-settings", "name": "settings.json",
                    "sha256": hashlib.sha256(settings).hexdigest(), "size": len(settings),
                },
                "news_ai": {
                    "file_id": "previous-news-ai", "name": "news-ai.json",
                    "sha256": hashlib.sha256(news_ai).hexdigest(), "size": len(news_ai),
                },
            },
        }).encode("utf-8")
        remote = {
            "manifest-id": manifest,
            "previous-settings": settings,
            "previous-news-ai": news_ai,
            "orphan-new-generation-settings": b"unpublished",
        }
        requested: list[str] = []

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3")

            def download_bytes(_drive, file_id):
                requested.append(file_id)
                return remote[file_id]

            with patch.object(service, "_drive_service", return_value=object()), \
                    patch.object(service, "_find_sync_folder", return_value="folder"), \
                    patch.object(service, "_find_remote_file", side_effect=lambda _drive, _folder, name: "manifest-id" if name == service.MANIFEST_REMOTE_NAME else None), \
                    patch.object(service, "_download_remote_bytes", side_effect=download_bytes), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.import_from_settings_and_themes"), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.NewsAIBackupService.import_from"):
                result = service.download(target="settings")

        self.assertEqual("completed", result.status)
        self.assertCountEqual(
            ["manifest-id", "previous-settings", "previous-news-ai"], requested,
        )
        self.assertNotIn("orphan-new-generation-settings", requested)

    def test_first_v2_upload_freezes_unselected_v1_component_before_manifest(self) -> None:
        legacy_themes = json.dumps({
            "format": SettingsBackupService.FORMAT,
            "version": SettingsBackupService.VERSION,
            "theme_data": {
                "format": ThemeBackupService.FORMAT,
                "version": ThemeBackupService.VERSION,
                "profiles": [],
            },
        }).encode("utf-8")

        class Request:
            def __init__(self, result: dict[str, object], execute_calls: list[dict[str, object]]) -> None:
                self.result = result
                self.execute_calls = execute_calls

            def execute(self, **kwargs) -> dict[str, object]:
                self.execute_calls.append(kwargs)
                return self.result

        class Files:
            def __init__(self) -> None:
                self.created: list[tuple[str, bytes]] = []
                self.execute_calls: list[dict[str, object]] = []

            def create(self, *, body, media_body, fields):
                name = str(body["name"])
                self.created.append((name, media_body))
                return Request({"id": f"remote-{len(self.created)}"}, self.execute_calls)

        class Drive:
            def __init__(self) -> None:
                self.api = Files()

            def files(self):
                return self.api

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            drive = Drive()
            service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3")

            def write_backup(_service, path, *_args, **_kwargs) -> None:
                path.write_text("{}", encoding="utf-8")

            with patch.object(service, "_drive_service", return_value=drive), \
                    patch.object(service, "_ensure_sync_folder", return_value="folder"), \
                    patch.object(service, "_find_remote_file", side_effect=lambda _drive, _folder, name: "legacy-themes" if name == service.THEMES_REMOTE_NAME else None), \
                    patch.object(service, "_download_remote_bytes", return_value=legacy_themes), \
                    patch.object(service, "_media_upload", side_effect=lambda content: content), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.export_to", write_backup), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.NewsAIBackupService.export_to", write_backup):
                result = service.upload(target="settings")

        self.assertEqual("completed", result.status)
        self.assertTrue(drive.api.execute_calls)
        self.assertTrue(
            all(call.get("num_retries") == 3 for call in drive.api.execute_calls),
            drive.api.execute_calls,
        )
        names = [name for name, _ in drive.api.created]
        self.assertTrue(names[0].startswith("kiwoom-monitor-v2-"))
        self.assertEqual(service.MANIFEST_REMOTE_NAME, names[-1])
        manifest = json.loads(drive.api.created[-1][1].decode("utf-8"))
        frozen = manifest["components"]["themes"]
        self.assertEqual("remote-1", frozen["file_id"])
        self.assertEqual(hashlib.sha256(legacy_themes).hexdigest(), frozen["sha256"])
        self.assertEqual(len(legacy_themes), frozen["size"])

    def test_explicit_restore_stages_without_applying_database(self) -> None:
        class Files:
            def get_media(self, *, fileId):
                return fileId

        class Drive:
            def files(self):
                return Files()

        class Downloader:
            def __init__(self, buffer, file_id):
                self.buffer = buffer
                self.file_id = file_id

            def next_chunk(self):
                self.buffer.write(json.dumps({
                    "format": SettingsBackupService.FORMAT,
                    "version": SettingsBackupService.VERSION,
                    "settings": {}, "columns": [],
                }).encode())
                return None, True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3")
            with patch.object(service, "_drive_service", return_value=Drive()), \
                    patch.object(service, "_find_sync_folder", return_value="folder"), \
                    patch.object(service, "_find_remote_file", side_effect=lambda _drive, _folder, name: "settings-id" if name == service.SETTINGS_REMOTE_NAME else None), \
                    patch("googleapiclient.http.MediaIoBaseDownload", Downloader), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.import_from_settings_and_themes") as apply:
                result = service.stage_restore(target="settings")
            self.assertEqual(("restore", "staged"), (result.operation, result.status))
            self.assertTrue((root / "strict_restore" / "current.json").is_file())
            self.assertFalse((root / "monitor.sqlite3").exists())
            apply.assert_not_called()

    def test_invalid_news_ai_file_is_rejected_before_settings_import(self) -> None:
        class Files:
            def get_media(self, *, fileId):
                return fileId

        class Drive:
            def files(self):
                return Files()

        payloads = {
            "settings-id": json.dumps({
                "format": SettingsBackupService.FORMAT, "version": SettingsBackupService.VERSION,
                "settings": {}, "columns": [],
            }).encode(),
            "themes-id": json.dumps({
                "format": SettingsBackupService.FORMAT, "version": SettingsBackupService.VERSION,
                "theme_data": {"format": ThemeBackupService.FORMAT, "version": ThemeBackupService.VERSION,
                               "profiles": []},
            }).encode(),
            "ai-id": b"\xff",
        }

        class Downloader:
            def __init__(self, buffer, file_id):
                self.buffer = buffer
                self.file_id = file_id

            def next_chunk(self):
                self.buffer.write(payloads[self.file_id])
                return None, True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3")
            ids = {
                service.SETTINGS_REMOTE_NAME: "settings-id",
                service.THEMES_REMOTE_NAME: "themes-id",
                service.NEWS_AI_REMOTE_NAME: "ai-id",
            }
            with patch.object(service, "_drive_service", return_value=Drive()), \
                    patch.object(service, "_find_sync_folder", return_value="folder"), \
                    patch.object(service, "_find_remote_file", side_effect=lambda _drive, _folder, name: ids.get(name)), \
                    patch("googleapiclient.http.MediaIoBaseDownload", Downloader), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.import_from") as settings_import, \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.NewsAIBackupService.import_from", side_effect=ValueError("invalid AI backup")) as ai_import:
                with self.assertRaises(GoogleDriveSyncError):
                    service.download(target="both")
            settings_import.assert_not_called()
            ai_import.assert_not_called()

    def test_download_imports_after_all_selected_files_arrive(self) -> None:
        events: list[str] = []
        payloads = {
            "settings-id": json.dumps({
                "format": SettingsBackupService.FORMAT, "version": SettingsBackupService.VERSION,
                "settings": {}, "columns": [],
            }).encode(),
            "themes-id": json.dumps({
                "format": SettingsBackupService.FORMAT, "version": SettingsBackupService.VERSION,
                "theme_data": {"format": ThemeBackupService.FORMAT, "version": ThemeBackupService.VERSION,
                               "profiles": []},
            }).encode(),
            "ai-id": json.dumps({
                "format": NewsAIBackupService.FORMAT, "version": NewsAIBackupService.VERSION,
                "analyses": [],
            }).encode(),
        }

        class Files:
            def get_media(self, *, fileId):
                return fileId

        class Drive:
            def files(self):
                return Files()

        class Downloader:
            def __init__(self, buffer, file_id):
                self.buffer = buffer
                self.file_id = file_id

            def next_chunk(self):
                events.append(f"download:{self.file_id}")
                self.buffer.write(payloads[self.file_id])
                return None, True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3")
            ids = {
                service.SETTINGS_REMOTE_NAME: "settings-id",
                service.THEMES_REMOTE_NAME: "themes-id",
                service.NEWS_AI_REMOTE_NAME: "ai-id",
            }
            with patch.object(service, "_drive_service", return_value=Drive()), \
                    patch.object(service, "_find_sync_folder", return_value="folder"), \
                    patch.object(service, "_find_remote_file", side_effect=lambda _drive, _folder, name: ids.get(name)), \
                    patch("googleapiclient.http.MediaIoBaseDownload", Downloader), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.import_from_settings_and_themes", side_effect=lambda settings, themes, *_args, **_kwargs: events.append(f"import:{settings.name}+{themes.name}")), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.NewsAIBackupService.import_from", side_effect=lambda path: events.append(f"import:{path.name}")):
                service.download(target="both")

        self.assertEqual(
            ["download:settings-id", "download:themes-id", "download:ai-id",
             f"import:{service.SETTINGS_REMOTE_NAME}+{service.THEMES_REMOTE_NAME}",
             f"import:{service.NEWS_AI_REMOTE_NAME}"],
            events,
        )

    def test_download_failure_before_all_files_arrive_does_not_import_any_file(self) -> None:
        class Files:
            def get_media(self, *, fileId):
                return fileId

        class Drive:
            def files(self):
                return Files()

        class Downloader:
            def __init__(self, buffer, file_id):
                self.buffer = buffer
                self.file_id = file_id

            def next_chunk(self):
                if self.file_id == "themes-id":
                    raise OSError("download failed")
                self.buffer.write(b"{}")
                return None, True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3")
            ids = {
                service.SETTINGS_REMOTE_NAME: "settings-id",
                service.THEMES_REMOTE_NAME: "themes-id",
                service.NEWS_AI_REMOTE_NAME: "ai-id",
            }
            with patch.object(service, "_drive_service", return_value=Drive()), \
                    patch.object(service, "_find_sync_folder", return_value="folder"), \
                    patch.object(service, "_find_remote_file", side_effect=lambda _drive, _folder, name: ids.get(name)), \
                    patch("googleapiclient.http.MediaIoBaseDownload", Downloader), \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.import_from") as settings_import, \
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.NewsAIBackupService.import_from") as ai_import:
                with self.assertRaises(GoogleDriveSyncError):
                    service.download(target="both")
            settings_import.assert_not_called()
            ai_import.assert_not_called()

    def test_every_target_includes_news_ai_backup(self) -> None:
        class Request:
            def __init__(self, file_id: str) -> None: self.file_id = file_id
            def execute(self, **_kwargs) -> dict[str, object]: return {"id": self.file_id}

        class Files:
            def __init__(self) -> None:
                self.names: list[str] = []
                self.next_id = 0
            def create(self, *, body, media_body, fields):
                self.names.append(str(body["name"]))
                self.next_id += 1
                return Request(f"remote-{self.next_id}")
            def update(self, **_kwargs): return Request("existing")

        class Drive:
            def __init__(self) -> None: self.api = Files()
            def files(self): return self.api

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def write_backup(_service, path, *_args, **_kwargs) -> None:
                path.write_text("{}", encoding="utf-8")

            for target in ("settings", "themes", "both"):
                with self.subTest(target=target):
                    drive = Drive()
                    service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3")
                    with patch.object(service, "_drive_service", return_value=drive), \
                            patch.object(service, "_ensure_sync_folder", return_value="folder"), \
                            patch.object(service, "_find_remote_file", return_value=None), \
                            patch.object(service, "_media_upload", side_effect=lambda content: content), \
                            patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.export_to", write_backup), \
                            patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.NewsAIBackupService.export_to", write_backup):
                        service.upload(target=target)

                    self.assertIn(GoogleDriveSyncService.NEWS_AI_REMOTE_NAME, drive.api.names)
                    self.assertEqual(
                        target in {"settings", "both"},
                        GoogleDriveSyncService.SETTINGS_REMOTE_NAME in drive.api.names,
                    )
                    self.assertEqual(
                        target in {"themes", "both"},
                        GoogleDriveSyncService.THEMES_REMOTE_NAME in drive.api.names,
                    )

    def test_only_machine_specific_settings_are_excluded_from_drive_sync(self) -> None:
        self.assertIn("window_width", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("window_height", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("window_x", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("settings_dialog_width", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("theme_manager_stock_column_width", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("google_drive_auto_download", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("google_drive_auto_upload", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("google_drive_auto_upload_on_exit", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("google_drive_sync_target", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertIn("near_high_sound_fire", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertNotIn("trade_value_1m_alert_eok", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        self.assertNotIn("theme_active_profile", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
        for prefix in ("theme_new_import", "theme_text_import", "theme_excel_import", "theme_image_import"):
            self.assertNotIn(f"{prefix}_custom_separators", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
            self.assertNotIn(f"{prefix}_exclusions", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)

    def test_imports_only_desktop_oauth_client_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = GoogleDriveSyncService(root / "data" / "monitor.sqlite3")
            source = root / "desktop.json"
            source.write_text(json.dumps({"installed": {"client_id": "test", "client_secret": "secret"}}), encoding="utf-8")

            service.import_client_file(source)

            self.assertTrue(service.configured)
            self.assertFalse(service.connected)
            self.assertEqual("test", json.loads((root / "data" / "google_drive_client.json").read_text(encoding="utf-8"))["installed"]["client_id"])

    def test_rejects_non_desktop_oauth_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = GoogleDriveSyncService(root / "data" / "monitor.sqlite3")
            source = root / "web.json"
            source.write_text(json.dumps({"web": {"client_id": "test"}}), encoding="utf-8")

            with self.assertRaises(GoogleDriveSyncError):
                service.import_client_file(source)
