from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.infrastructure.persistence.google_drive_sync import (
    CURRENT_BACKUP_SET, GoogleDriveSyncError, GoogleDriveSyncService,
)
from kiwoom_monitor.infrastructure.persistence.news_ai_backup import NewsAIBackupService
from kiwoom_monitor.infrastructure.persistence.settings_backup import SettingsBackupService
from kiwoom_monitor.infrastructure.persistence.theme_backup import ThemeBackupService


class GoogleDriveSyncServiceTest(unittest.TestCase):
    def test_protected_token_unavailable_keeps_original_without_oauth_or_refresh(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient import discovery
        from kiwoom_monitor.infrastructure.kiwoom_rest import local_secret_keychain as keychain

        with tempfile.TemporaryDirectory() as directory:
            service = GoogleDriveSyncService(Path(directory) / 'monitor.sqlite3')
            service._client_path.write_text('{}', encoding='utf-8')
            inputs = (
                ('win32', b'KRM-MAC-KEYCHAIN\x01' + bytes(28)),
                ('darwin', b'legacy-dpapi'),
                ('darwin', b'KRM-MAC-KEYCHAIN\x01' + bytes(28)),
                ('linux', b'legacy-dpapi'),
            )
            for platform, data in inputs:
                with self.subTest(platform=platform, is_keychain=data.startswith(b'KRM')):
                    token = base64.b64encode(data)
                    service._token_path.write_bytes(token)
                    with patch('sys.platform', platform), \
                         patch.object(keychain, '_keychain_key', side_effect=keychain.LocalSecretUnavailable('locked')), \
                         patch.object(InstalledAppFlow, 'from_client_secrets_file') as oauth, \
                         patch.object(Credentials, 'from_authorized_user_info') as deserialize, \
                         patch.object(Request, '__call__') as request, patch.object(discovery, 'build') as build:
                        with self.assertRaises(GoogleDriveSyncError):
                            service._drive_service(interactive=True)
                        for caller in (oauth, deserialize, request, build):
                            caller.assert_not_called()
                    self.assertEqual(token, service._token_path.read_bytes())

    @staticmethod
    def _backup_set_transport():
        from types import SimpleNamespace

        records, queries, writes, reads = {}, [], [], []
        folder_mime = "application/vnd.google-apps.folder"

        def request(value):
            return SimpleNamespace(execute=lambda **_kwargs: value)

        def add(file_id, name, *, parent=None, content=b"", folder=False):
            records[file_id] = {
                "id": file_id, "name": name, "parents": [] if parent is None else [parent],
                "mimeType": folder_mime if folder else "application/json", "content": content,
                "modifiedTime": "2030-01-01T00:00:00Z",
            }

        def list_files(*, q, **_kwargs):
            queries.append(q)
            parts = q.split("'")
            assert len(parts) == 5 and parts[0] == "name = "
            if parts[2] == " and mimeType = ":
                assert parts[3] == folder_mime and parts[4] == " and trashed = false"
                found = [value for value in records.values()
                         if value["name"] == parts[1] and value["mimeType"] == folder_mime]
            else:
                assert parts[2] == " and " and parts[4] == " in parents and trashed = false"
                found = [value for value in records.values()
                         if value["name"] == parts[1] and parts[3] in value["parents"]]
            return request({"files": [{key: value[key] for key in ("id", "name", "modifiedTime")}
                                      for value in found]})

        def create_file(*, body, fields, media_body=b""):
            file_id = f"new-{len(records) + 1}"
            parents = body.get("parents", [])
            add(file_id, body["name"], parent=parents[0] if parents else None,
                content=media_body, folder=body.get("mimeType") == folder_mime)
            writes.append(("create", file_id))
            return request({"id": file_id})

        def update_file(*, fileId, media_body, **_kwargs):
            records[fileId]["content"] = media_body
            writes.append(("update", fileId))
            return request({"id": fileId})

        def download(_drive, file_id):
            reads.append(file_id)
            return records[file_id]["content"]

        api = SimpleNamespace(list=list_files, create=create_file, update=update_file)
        add("legacy-folder", GoogleDriveSyncService.FOLDER_NAME, folder=True)
        settings = json.dumps({
            "format": SettingsBackupService.FORMAT, "version": SettingsBackupService.VERSION,
            "settings": {"rank_query_type": "4"}, "columns": [],
        }).encode()
        news_ai = json.dumps({
            "format": NewsAIBackupService.FORMAT, "version": NewsAIBackupService.VERSION,
            "analyses": [], "shared_analyses": [], "inferences": [],
        }).encode()
        add("legacy-settings", GoogleDriveSyncService.SETTINGS_REMOTE_NAME,
            parent="legacy-folder", content=settings)
        add("legacy-ai", GoogleDriveSyncService.NEWS_AI_REMOTE_NAME,
            parent="legacy-folder", content=news_ai)
        manifest = json.dumps({
            "format": GoogleDriveSyncService.BUNDLE_FORMAT, "version": 2,
            "generation": "original-legacy-generation", "components": {
                role: {"file_id": file_id, "name": records[file_id]["name"],
                       "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
                for role, file_id, content in (("settings", "legacy-settings", settings),
                                               ("news_ai", "legacy-ai", news_ai))
            },
        }).encode()
        add("legacy-manifest", GoogleDriveSyncService.MANIFEST_REMOTE_NAME,
            parent="legacy-folder", content=manifest)
        return SimpleNamespace(drive=SimpleNamespace(files=lambda: api), add=add, download=download,
                               records=records, queries=queries, writes=writes, reads=reads)

    def test_current_backup_set_never_reads_legacy_folder_or_unpublished_aliases(self) -> None:
        from kiwoom_monitor.infrastructure.persistence.database import Database

        for scenario in ("absent", "aliases", "untagged-manifest", "foreign-manifest"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                remote = self._backup_set_transport()
                if scenario != "absent":
                    remote.add("current-folder", GoogleDriveSyncService.CURRENT_FOLDER_NAME, folder=True)
                    for role, name in (("settings", GoogleDriveSyncService.SETTINGS_REMOTE_NAME),
                                       ("ai", GoogleDriveSyncService.NEWS_AI_REMOTE_NAME)):
                        remote.add("unpublished-" + role, name, parent="current-folder",
                                   content=remote.records["legacy-" + role]["content"])
                    remote.add("orphan", "kiwoom-monitor-v2-orphan-settings.json",
                               parent="current-folder", content=b"unpublished")
                    if scenario in {"untagged-manifest", "foreign-manifest"}:
                        copied = json.loads(remote.records["legacy-manifest"]["content"])
                        if scenario == "foreign-manifest":
                            copied["backup_set"] = "legacy"
                        remote.add("copied-manifest", GoogleDriveSyncService.MANIFEST_REMOTE_NAME,
                                   parent="current-folder", content=json.dumps(copied).encode())
                before = {key: {**value, "parents": list(value["parents"])}
                          for key, value in remote.records.items()}
                service = GoogleDriveSyncService(root / "monitor.sqlite3", backup_set=CURRENT_BACKUP_SET)
                legacy = GoogleDriveSyncService(root / "monitor.sqlite3")
                self.assertEqual(legacy._client_path, service._client_path)
                self.assertEqual(legacy._token_path, service._token_path)
                with self.assertRaises(ValueError):
                    GoogleDriveSyncService(root / "monitor.sqlite3", backup_set="unknown-set")
                with patch.object(service, "_drive_service", return_value=remote.drive), \
                        patch.object(service, "_download_remote_bytes", side_effect=remote.download):
                    if scenario in {"untagged-manifest", "foreign-manifest"}:
                        with self.assertRaises(GoogleDriveSyncError):
                            service.download(target="settings")
                        with self.assertRaises(GoogleDriveSyncError):
                            service.stage_restore(target="settings")
                        producer_root = root / "upload-source"
                        producer_root.mkdir()
                        database = Database(producer_root / "monitor.sqlite3")
                        database.initialize()
                        producer = GoogleDriveSyncService(
                            producer_root / "monitor.sqlite3", producer_root / "news.sqlite3",
                            backup_set=CURRENT_BACKUP_SET,
                        )
                        with patch.object(producer, "_drive_service", return_value=remote.drive), \
                                patch.object(producer, "_media_upload", side_effect=lambda content: content), \
                                patch.object(producer, "_download_remote_bytes", side_effect=remote.download):
                            with self.assertRaises(GoogleDriveSyncError):
                                producer.upload(target="settings")
                        self.assertEqual(["copied-manifest"] * 3, remote.reads)
                    else:
                        self.assertEqual("", service.latest_modified_time(target="settings"))
                        result = service.download(target="settings")
                        self.assertEqual(("download", "empty"), (result.operation, result.status))
                        result = service.stage_restore(target="settings")
                        self.assertEqual(("restore", "empty"), (result.operation, result.status))
                        self.assertEqual([], remote.reads)
                self.assertEqual(before, remote.records)
                self.assertEqual([], remote.writes)
                self.assertFalse((root / "monitor.sqlite3").exists())
                self.assertFalse((root / "news.sqlite3").exists())
                self.assertFalse((root / "strict_restore").exists())
                self.assertTrue(remote.queries)
                self.assertTrue(all(query.split("'")[1] in {
                    GoogleDriveSyncService.CURRENT_FOLDER_NAME, GoogleDriveSyncService.MANIFEST_REMOTE_NAME,
                } for query in remote.queries))
                self.assertFalse(any("'legacy-folder'" in query or
                                     query.split("'")[1] == GoogleDriveSyncService.FOLDER_NAME
                                     for query in remote.queries))

    def test_current_backup_set_upload_and_fresh_reconnect_preserve_legacy(self) -> None:
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository

        for unpublished_current_alias in (False, True):
            with self.subTest(unpublished_current_alias=unpublished_current_alias), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                database = Database(root / "monitor.sqlite3")
                database.initialize()
                database.settings.set("rank_query_type", "2")
                remote = self._backup_set_transport()
                legacy_before = {key: {**value, "parents": list(value["parents"])}
                                 for key, value in remote.records.items()}
                if unpublished_current_alias:
                    remote.add("current-folder", GoogleDriveSyncService.CURRENT_FOLDER_NAME, folder=True)
                    remote.add("unpublished-themes", GoogleDriveSyncService.THEMES_REMOTE_NAME,
                               parent="current-folder", content=json.dumps({
                                   "format": SettingsBackupService.FORMAT, "version": SettingsBackupService.VERSION,
                                   "theme_data": {"format": ThemeBackupService.FORMAT,
                                                  "version": ThemeBackupService.VERSION, "profiles": []},
                               }).encode())
                service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3",
                                                 backup_set=CURRENT_BACKUP_SET)
                with patch.object(GoogleDriveSyncService, "_drive_service", return_value=remote.drive), \
                        patch.object(GoogleDriveSyncService, "_media_upload", side_effect=lambda content: content), \
                        patch.object(GoogleDriveSyncService, "_download_remote_bytes", side_effect=remote.download):
                    self.assertEqual("completed", service.upload(target="settings").status)
                    self.assertEqual([], remote.reads)
                    folders = [value for value in remote.records.values()
                               if value["name"] == service.CURRENT_FOLDER_NAME]
                    self.assertEqual(1, len(folders))
                    folder_id = folders[0]["id"]
                    manifests = [value for value in remote.records.values()
                                 if value["name"] == service.MANIFEST_REMOTE_NAME
                                 and value["parents"] == [folder_id]]
                    self.assertEqual(1, len(manifests))
                    manifest = json.loads(manifests[0]["content"])
                    self.assertEqual(CURRENT_BACKUP_SET, manifest["backup_set"])
                    self.assertEqual({"settings", "news_ai"}, set(manifest["components"]))
                    component_ids = {value["file_id"] for value in manifest["components"].values()}
                    for value in manifest["components"].values():
                        stored = remote.records[value["file_id"]]
                        self.assertEqual([folder_id], stored["parents"])
                        self.assertEqual(len(stored["content"]), value["size"])
                        self.assertEqual(hashlib.sha256(stored["content"]).hexdigest(), value["sha256"])
                    ai = json.loads(remote.records[manifest["components"]["news_ai"]["file_id"]]["content"])
                    self.assertEqual(NewsAIBackupService.VERSION, ai["version"])
                    self.assertEqual([[], [], []], [ai[key] for key in ("analyses", "shared_analyses", "inferences")])
                    self.assertTrue(all(remote.records[file_id]["name"] == service.CURRENT_FOLDER_NAME
                                        or remote.records[file_id]["name"] == service.MANIFEST_REMOTE_NAME
                                        or remote.records[file_id]["name"].startswith("kiwoom-monitor-v2-")
                                        for _, file_id in remote.writes))
                    database.settings.set("rank_query_type", "3")
                    reconnected = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3",
                                                         backup_set=CURRENT_BACKUP_SET)
                    self.assertEqual(service._client_path, reconnected._client_path)
                    self.assertEqual(service._token_path, reconnected._token_path)
                    self.assertEqual("2030-01-01T00:00:00Z", reconnected.latest_modified_time(target="settings"))
                    self.assertEqual("completed", reconnected.download(target="settings").status)
                self.assertEqual("2", SettingsRepository(root / "monitor.sqlite3").get("rank_query_type"))
                self.assertCountEqual([manifests[0]["id"], *component_ids], remote.reads)
                self.assertEqual(legacy_before, {key: remote.records[key] for key in legacy_before})
                self.assertFalse(any("'legacy-folder'" in query or
                                     query.split("'")[1] == GoogleDriveSyncService.FOLDER_NAME
                                     for query in remote.queries))

    def test_current_stage_keeps_original_manifest_and_restarts_without_remote_queries(self) -> None:
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.infrastructure.persistence.strict_restore import StrictRestoreCoordinator

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "chosen-full-root"
            root.mkdir()
            database = Database(root / "monitor.sqlite3")
            target = database.initialize_full_role(role="full-local")
            database.settings.set("rank_query_type", "3")
            remote = self._backup_set_transport()
            remote.add("current-folder", GoogleDriveSyncService.CURRENT_FOLDER_NAME, folder=True)
            components = {}
            for role, legacy_id in (("settings", "legacy-settings"), ("news_ai", "legacy-ai")):
                content = remote.records[legacy_id]["content"]
                file_id, name = "current-" + role, "kiwoom-monitor-v2-first-" + role + ".json"
                remote.add(file_id, name, parent="current-folder", content=content)
                components[role] = {"file_id": file_id, "name": name,
                                    "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
            original_manifest = {
                "format": GoogleDriveSyncService.BUNDLE_FORMAT, "version": 2,
                "backup_set": CURRENT_BACKUP_SET, "generation": "first-original-generation",
                "created_at": "2026-11-02T00:00:00Z", "target": "settings",
                "source_header": {"original_utc": "2026-10-30T01:02:03Z", "revision": 7},
                "components": components,
            }
            original_bytes = json.dumps(original_manifest, indent=3).encode()
            remote.add("current-manifest", GoogleDriveSyncService.MANIFEST_REMOTE_NAME,
                       parent="current-folder", content=original_bytes)
            remote_before = {key: {**value, "parents": list(value["parents"])}
                             for key, value in remote.records.items()}
            service = GoogleDriveSyncService(root / "monitor.sqlite3", root / "news.sqlite3",
                                             backup_set=CURRENT_BACKUP_SET)
            with patch.object(service, "_drive_service", return_value=remote.drive), \
                    patch.object(service, "_download_remote_bytes", side_effect=remote.download):
                result = service.stage_restore(target="settings")
            self.assertEqual(("restore", "staged"), (result.operation, result.status))
            self.assertEqual("3", database.settings.get("rank_query_type"))
            coordinator = StrictRestoreCoordinator(root / "monitor.sqlite3", root / "news.sqlite3",
                                                   expected_backup_set=CURRENT_BACKUP_SET)
            staged = coordinator._read_pointer()
            self.assertEqual(original_manifest, staged["provenance"]["manifest"])
            self.assertEqual("google-drive-v2-manifest/v1", staged["provenance"]["format"])
            for role, info in staged["inputs"].items():
                self.assertEqual({key: components[role][key] for key in ("sha256", "size")}, info)
            self.assertCountEqual(["current-manifest", *[value["file_id"] for value in components.values()]], remote.reads)
            completed_remote_work = (list(remote.queries), list(remote.reads), list(remote.writes))
            target_checks = []

            def check_full_target():
                checked = Database(root / "monitor.sqlite3").initialize_full_role(
                    role="full-local", allow_create=False,
                )
                self.assertTrue(checked["replayed"])
                self.assertEqual(target["seed_sha256"], checked["seed_sha256"])
                target_checks.append("full-validated")

            with patch.object(GoogleDriveSyncService, "_drive_service",
                              side_effect=AssertionError("restart queried Drive")):
                lease, outcome = coordinator.enter_main(restore_target_check=check_full_target)
                lease.close()
                self.assertEqual(["full-validated"], target_checks)
                self.assertEqual("COMMITTED", outcome.state, outcome.message)
                self.assertEqual("4", Database(root / "monitor.sqlite3").settings.get("rank_query_type"))
                committed = coordinator.pointer.read_bytes()
                restarted = StrictRestoreCoordinator(root / "monitor.sqlite3", root / "news.sqlite3",
                                                      expected_backup_set=CURRENT_BACKUP_SET)
                lease, outcome = restarted.enter_main(
                    restore_target_check=lambda: self.fail("terminal restore checked full target"),
                )
                lease.close()
            self.assertIsNone(outcome)
            self.assertEqual(committed, coordinator.pointer.read_bytes())
            self.assertEqual(staged["operation_id"], restarted._read_pointer()["operation_id"])
            self.assertEqual(original_manifest, restarted._read_pointer()["provenance"]["manifest"])
            replay = Database(root / "monitor.sqlite3").initialize_full_role(role="full-local")
            self.assertTrue(replay["replayed"])
            self.assertEqual(target["seed_sha256"], replay["seed_sha256"])
            self.assertEqual(("full", "FRESH"), (replay["layout"], replay["seed_kind"]))
            self.assertEqual(completed_remote_work, (remote.queries, remote.reads, remote.writes))
            self.assertEqual(remote_before, remote.records)
            self.assertEqual(original_bytes, remote.records["current-manifest"]["content"])

    def test_v1_and_v2_legacy_restore_use_bound_clock_in_native_catalog(self) -> None:
        import sqlite3
        from contextlib import closing
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from unittest.mock import Mock
        from kiwoom_monitor.infrastructure.persistence.database import Database
        fixed = datetime.fromisoformat("2030-01-02T03:04:05+09:00")
        content = json.dumps({"format": SettingsBackupService.FORMAT, "version": 1,
            "created_at": "1999-01-01", "stock_catalog": [
                {"code": "005930", "name": "fixture", "market": "KOSPI"}]}).encode()
        for version in (1, 2):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "monitor.sqlite3"
                Database(path).initialize()
                service = GoogleDriveSyncService(path, now_provider=Mock(side_effect=AssertionError("old clock")))
                clock = Mock(return_value=fixed.astimezone(timezone.utc))
                service.set_now_provider(clock)
                remote = {"themes-id": content}
                remote["manifest-id"] = json.dumps({"format": service.BUNDLE_FORMAT, "version": 2,
                    "generation": "clock-generation", "components": {"themes": {
                        "file_id": "themes-id", "name": service.THEMES_REMOTE_NAME,
                        "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}}}).encode()
                class Downloader:
                    def __init__(self, buffer, file_id):
                        self.buffer, self.file_id = buffer, file_id
                    def next_chunk(self):
                        self.buffer.write(remote[self.file_id])
                        return None, True
                drive = SimpleNamespace(files=lambda: SimpleNamespace(get_media=lambda *, fileId: fileId))
                def find(_drive, _folder, name):
                    if name == service.MANIFEST_REMOTE_NAME:
                        return "manifest-id" if version == 2 else None
                    return "themes-id" if name == service.THEMES_REMOTE_NAME else None
                with patch.object(service, "_drive_service", return_value=drive), \
                        patch.object(service, "_find_sync_folder", return_value="folder"), \
                        patch.object(service, "_find_remote_file", side_effect=find), \
                        patch.object(service, "_download_remote_bytes", side_effect=lambda _drive, key: remote[key]), \
                        patch("googleapiclient.http.MediaIoBaseDownload", Downloader):
                    result = service.download(target="themes")
                self.assertEqual("completed", result.status)
                clock.assert_called_once_with()
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(("fixture", fixed.isoformat()), connection.execute(
                        "SELECT name,updated_at FROM stocks WHERE code='005930'").fetchone())
                self.assertEqual(content, remote["themes-id"])

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

        self.assertEqual(b"x" * GoogleDriveSyncService.RESUMABLE_UPLOAD_THRESHOLD_BYTES, media_upload.call_args.args[0].getvalue())
        with patch("googleapiclient.http.MediaIoBaseUpload", side_effect=lambda stream, **kwargs: (stream, kwargs), create=True) as media_upload:
            GoogleDriveSyncService._media_upload(b"y" * (GoogleDriveSyncService.RESUMABLE_UPLOAD_THRESHOLD_BYTES - 1))
        self.assertFalse(media_upload.call_args.kwargs["resumable"])

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
            "shared_analyses": [],
            "inferences": [],
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
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.import_from_settings_and_themes") as settings_import, \
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
                "shared_analyses": [],
                "inferences": [],
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
                    patch("kiwoom_monitor.infrastructure.persistence.google_drive_sync.SettingsBackupService.import_from_settings_and_themes") as settings_import, \
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
        # The active selection travels with theme_data/accepted theme generation,
        # not as a duplicate generic setting. It is not a machine-only value.
        self.assertIn("theme_active_profile", GoogleDriveSyncService.LOCAL_ONLY_SETTINGS)
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
