"""Google Drive 일반 폴더 기반 설정·테마 동기화."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from kiwoom_monitor.infrastructure.central_settings_sync import is_shared_setting
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import _protect, _unprotect

from .database import DEFAULT_SETTINGS
from .settings_backup import SettingsBackupError, SettingsBackupService
from .news_ai_backup import NewsAIBackupService
from .strict_restore import StrictRestoreCoordinator


class GoogleDriveSyncError(RuntimeError):
    def __init__(self, message: str, *, local_changes_applied: bool = False) -> None:
        super().__init__(message)
        self.local_changes_applied = local_changes_applied


@dataclass(frozen=True)
class GoogleDriveSyncResult:
    operation: str
    status: str
    message: str
    local_changes_applied: bool = False


class GoogleDriveSyncService:
    """개인 Drive에 legacy 파일과 manifest가 가리키는 백업 세대를 저장한다."""

    SCOPES = ("https://www.googleapis.com/auth/drive.file",)
    FOLDER_NAME = "키움 실시간 모니터"
    LEGACY_REMOTE_NAME = "kiwoom-monitor-sync-v1.json"
    SETTINGS_REMOTE_NAME = "kiwoom-monitor-settings-v1.json"
    THEMES_REMOTE_NAME = "kiwoom-monitor-themes-v1.json"
    NEWS_AI_REMOTE_NAME = "kiwoom-monitor-news-ai-v1.json"
    MANIFEST_REMOTE_NAME = "kiwoom-monitor-manifest-v2.json"
    BUNDLE_FORMAT = "kiwoom-monitor-drive-bundle"
    RESUMABLE_UPLOAD_THRESHOLD_BYTES = 5 * 1024 * 1024
    RESUMABLE_UPLOAD_CHUNK_BYTES = 1024 * 1024
    # NAS와 Google Drive가 서로 다른 의미의 "공통 설정"을 만들지 않는다.
    # 창 위치/크기, 로컬 파일 경로, 동기화 실행 상태처럼 PC에 종속된 값은
    # 두 경계 모두 central_settings_sync의 한 정책으로 제외한다.
    LOCAL_ONLY_SETTINGS = frozenset(
        key for key in DEFAULT_SETTINGS if not is_shared_setting(key)
    )

    def __init__(self, database_path: Path, news_database_path: Path | None = None) -> None:
        self._database_path = database_path
        self._data_dir = database_path.parent
        self._news_database_path = news_database_path or database_path.parent / "news.sqlite3"
        self._client_path = self._data_dir / "google_drive_client.json"
        self._token_path = self._data_dir / "google_drive_token.dat"

    @property
    def configured(self) -> bool:
        return self._client_path.is_file()

    @property
    def connected(self) -> bool:
        return self._token_path.is_file()

    def import_client_file(self, source: Path) -> None:
        try:
            document = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise GoogleDriveSyncError("Google OAuth JSON 파일을 읽을 수 없습니다.") from error
        if not isinstance(document, dict) or not isinstance(document.get("installed"), dict):
            raise GoogleDriveSyncError("데스크톱 앱 유형의 Google OAuth JSON 파일이 아닙니다.")
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._client_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        self._token_path.unlink(missing_ok=True)

    def disconnect(self) -> None:
        self._token_path.unlink(missing_ok=True)

    def latest_modified_time(self, interactive: bool = False, target: str = "both") -> str:
        """선택한 동기화 파일의 최신 수정 시각만 조회한다. 내용은 내려받지 않는다."""
        target = target if target in {"settings", "themes", "both"} else "both"
        service = self._drive_service(interactive)
        folder_id = self._find_sync_folder(service)
        if not folder_id:
            return ""
        try:
            manifest = self._find_remote_file_info(service, folder_id, self.MANIFEST_REMOTE_NAME)
            if manifest is not None:
                return str(manifest.get("modifiedTime", ""))
            modified_times = [
                str(info.get("modifiedTime", ""))
                for name, _, _ in self._selected_files(target)
                if (info := self._find_remote_file_info(service, folder_id, name)) is not None
            ]
            info = self._find_remote_file_info(service, folder_id, self.NEWS_AI_REMOTE_NAME)
            if info is not None:
                modified_times.append(str(info.get("modifiedTime", "")))
            if not modified_times:
                legacy = self._find_remote_file_info(service, folder_id, self.LEGACY_REMOTE_NAME)
                if legacy is not None:
                    modified_times.append(str(legacy.get("modifiedTime", "")))
            return max((value for value in modified_times if value), default="")
        except Exception as error:
            raise GoogleDriveSyncError(f"Google Drive 수정 시각을 읽지 못했습니다: {error}") from error

    def upload(self, interactive: bool = False, target: str = "both") -> GoogleDriveSyncResult:
        target = target if target in {"settings", "themes", "both"} else "both"
        service = self._drive_service(interactive)
        folder_id = self._ensure_sync_folder(service)
        files = self._selected_files(target)
        try:
            with tempfile.TemporaryDirectory(prefix="kiwoom_drive_") as directory:
                snapshot = sqlite3.connect(self._database_path)
                try:
                    snapshot.execute("BEGIN")
                    for name, include_settings, include_themes in files:
                        source = Path(directory) / name
                        SettingsBackupService(self._database_path).export_to(
                            source, include_settings, include_themes,
                            self.LOCAL_ONLY_SETTINGS, include_column_widths=False,
                            connection=snapshot,
                        )
                finally:
                    snapshot.close()
                sources: dict[str, Path] = {}
                for name, include_settings, include_themes in files:
                    if include_settings:
                        sources["settings"] = Path(directory) / name
                    if include_themes:
                        sources["themes"] = Path(directory) / name
                ai_source = Path(directory) / self.NEWS_AI_REMOTE_NAME
                NewsAIBackupService(self._news_database_path).export_to(ai_source)
                sources["news_ai"] = ai_source

                generation = uuid.uuid4().hex
                components = self._components_from_previous_generation(service, folder_id, target, generation)
                for role, source in sources.items():
                    content = source.read_bytes()
                    name = f"kiwoom-monitor-v2-{generation}-{role}.json"
                    created = service.files().create(
                        body={"name": name, "parents": [folder_id]},
                        media_body=self._media_upload(content), fields="id",
                    ).execute(num_retries=3)
                    components[role] = {
                        "file_id": str(created["id"]), "name": name,
                        "sha256": hashlib.sha256(content).hexdigest(), "size": len(content),
                    }
                # Keep older desktop builds working. New builds select v2 by its
                # manifest; these mutable aliases are never used once that exists.
                for role, source in sources.items():
                    legacy_name = {
                        "settings": self.SETTINGS_REMOTE_NAME,
                        "themes": self.THEMES_REMOTE_NAME,
                        "news_ai": self.NEWS_AI_REMOTE_NAME,
                    }[role]
                    existing_legacy = self._find_remote_file(service, folder_id, legacy_name)
                    media = self._media_upload(source.read_bytes())
                    if existing_legacy:
                        service.files().update(
                            fileId=existing_legacy, media_body=media,
                            fields="id,modifiedTime",
                        ).execute(num_retries=3)
                    else:
                        service.files().create(
                            body={"name": legacy_name, "parents": [folder_id]},
                            media_body=media, fields="id,modifiedTime",
                        ).execute(num_retries=3)
                manifest = {
                    "format": self.BUNDLE_FORMAT, "version": 2,
                    "generation": generation,
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "target": target, "components": components,
                }
                manifest_bytes = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
                existing_manifest = self._find_remote_file(service, folder_id, self.MANIFEST_REMOTE_NAME)
                media = self._media_upload(manifest_bytes)
                if existing_manifest:
                    service.files().update(
                        fileId=existing_manifest, media_body=media, fields="id,modifiedTime",
                    ).execute(num_retries=3)
                else:
                    service.files().create(
                        body={"name": self.MANIFEST_REMOTE_NAME, "parents": [folder_id]},
                        media_body=media, fields="id,modifiedTime",
                    ).execute(num_retries=3)
        except Exception as error:
            raise GoogleDriveSyncError(f"Google Drive 업로드에 실패했습니다: {error}") from error
        return GoogleDriveSyncResult(
            "upload", "completed", f"Google Drive에 {self._target_label(target)}을(를) 업로드했습니다.",
        )

    def stage_restore(self, interactive: bool = False, target: str = "both") -> GoogleDriveSyncResult:
        """Download an explicit restore into durable local staging for next start."""
        return self.download(interactive=interactive, target=target, stage_only=True)

    def download(self, interactive: bool = False, target: str = "both", *, stage_only: bool = False) -> GoogleDriveSyncResult:
        target = target if target in {"settings", "themes", "both"} else "both"
        service = self._drive_service(interactive)
        folder_id = self._find_sync_folder(service)
        if folder_id:
            manifest_id = self._find_remote_file(service, folder_id, self.MANIFEST_REMOTE_NAME)
            if manifest_id is not None:
                return self._download_v2(
                    service, manifest_id, target=target,
                    stage_only=stage_only,
                )
        files = self._selected_files(target)
        remote_files = [(name, self._find_remote_file(service, folder_id, name) if folder_id else None, include_settings, include_themes) for name, include_settings, include_themes in files]
        ai_file_id = self._find_remote_file(service, folder_id, self.NEWS_AI_REMOTE_NAME) if folder_id else None
        # 분리 저장 전의 단일 파일도 한 번은 읽어 기존 업로드를 잃지 않는다.
        if folder_id and not any(file_id for _, file_id, _, _ in remote_files):
            legacy_id = self._find_remote_file(service, folder_id, self.LEGACY_REMOTE_NAME)
            if legacy_id:
                remote_files = [(self.LEGACY_REMOTE_NAME, legacy_id, include_settings, include_themes) for _, include_settings, include_themes in files]
        if not any(file_id for _, file_id, _, _ in remote_files) and not ai_file_id:
            return GoogleDriveSyncResult(
                "restore" if stage_only else "download", "empty",
                "Google Drive에 아직 동기화된 설정이 없습니다.",
            )
        local_changes_applied = False
        try:
            from googleapiclient.http import MediaIoBaseDownload
            with tempfile.TemporaryDirectory(prefix="kiwoom_drive_") as directory:
                downloaded: list[tuple[Path, bool, bool]] = []
                for name, file_id, include_settings, include_themes in remote_files:
                    if not file_id:
                        continue
                    buffer = BytesIO()
                    downloader = MediaIoBaseDownload(buffer, service.files().get_media(fileId=file_id))
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()
                    source = Path(directory) / name
                    source.write_bytes(buffer.getvalue())
                    downloaded.append((source, include_settings, include_themes))
                ai_source: Path | None = None
                if ai_file_id:
                    buffer = BytesIO()
                    downloader = MediaIoBaseDownload(buffer, service.files().get_media(fileId=ai_file_id))
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()
                    ai_source = Path(directory) / self.NEWS_AI_REMOTE_NAME
                    ai_source.write_bytes(buffer.getvalue())
                settings_backup = SettingsBackupService(self._database_path)
                if ai_source is not None:
                    NewsAIBackupService.validate_file(ai_source)
                settings_source = next(
                    (source for source, include_settings, _ in downloaded if include_settings),
                    None,
                )
                themes_source = next(
                    (source for source, _, include_themes in downloaded if include_themes),
                    None,
                )
                if stage_only:
                    operation_id = StrictRestoreCoordinator(
                        self._database_path, self._news_database_path,
                    ).stage(
                        settings=settings_source, themes=themes_source,
                        news_ai=ai_source,
                        excluded_setting_keys=self.LOCAL_ONLY_SETTINGS,
                    )
                    return GoogleDriveSyncResult(
                        "restore", "staged",
                        "Google Drive 복원 자료를 확인해 보관했습니다. 앱을 정상 종료하고 다시 시작하면 "
                        f"복원이 적용됩니다. (작업 {operation_id[:8]})",
                    )
                settings_backup.import_from_settings_and_themes(
                    settings_source, themes_source,
                    self.LOCAL_ONLY_SETTINGS, include_column_widths=False,
                )
                local_changes_applied = settings_source is not None or themes_source is not None
                if ai_source is not None:
                    NewsAIBackupService(self._news_database_path).import_from(ai_source)
                    local_changes_applied = True
        except Exception as error:
            local_changes_applied = (
                local_changes_applied
                or bool(getattr(error, "local_database_applied", False))
            )
            raise GoogleDriveSyncError(
                f"Google Drive {'복원 예약' if stage_only else '다운로드'}에 실패했습니다: {error}",
                local_changes_applied=local_changes_applied,
            ) from error
        return GoogleDriveSyncResult(
            "download", "completed", f"Google Drive {self._target_label(target)}을(를) 다운로드하고 바로 적용했습니다.",
            local_changes_applied,
        )

    def _drive_service(self, interactive: bool):
        if not self.configured:
            raise GoogleDriveSyncError("먼저 Google OAuth JSON 파일을 연결하세요.")
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as error:
            raise GoogleDriveSyncError("Google Drive 동기화 구성요소가 설치되지 않았습니다.") from error
        credentials = None
        if self._token_path.is_file():
            try:
                raw = _unprotect(base64.b64decode(self._token_path.read_text(encoding="utf-8")))
                credentials = Credentials.from_authorized_user_info(json.loads(raw.decode("utf-8")), self.SCOPES)
                if not credentials.has_scopes(self.SCOPES):
                    credentials = None
            except Exception:
                self._token_path.unlink(missing_ok=True)
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
            except Exception:
                credentials = None
        if not credentials or not credentials.valid:
            if not interactive:
                raise GoogleDriveSyncError("Google 로그인이 필요합니다.")
            try:
                credentials = InstalledAppFlow.from_client_secrets_file(str(self._client_path), self.SCOPES).run_local_server(port=0)
            except Exception as error:
                raise GoogleDriveSyncError(f"Google 로그인에 실패했습니다: {error}") from error
        try:
            encoded = base64.b64encode(_protect(credentials.to_json().encode("utf-8"))).decode("ascii")
            self._token_path.write_text(encoded, encoding="utf-8")
        except OSError as error:
            raise GoogleDriveSyncError("Google 로그인 정보를 안전하게 저장하지 못했습니다.") from error
        return build("drive", "v3", credentials=credentials, cache_discovery=False)

    def _find_sync_folder(self, service: object) -> str | None:
        try:
            response = service.files().list(
                spaces="drive",
                q=f"name = '{self.FOLDER_NAME}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                fields="files(id,modifiedTime)",
                pageSize=10,
            ).execute()
        except Exception as error:
            raise GoogleDriveSyncError(f"Google Drive 폴더 목록을 읽지 못했습니다: {error}") from error
        files = response.get("files", [])
        if not files:
            return None
        return str(max(files, key=lambda item: str(item.get("modifiedTime", ""))).get("id"))

    def _ensure_sync_folder(self, service: object) -> str:
        folder_id = self._find_sync_folder(service)
        if folder_id:
            return folder_id
        try:
            created = service.files().create(
                body={"name": self.FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
                fields="id",
            ).execute(num_retries=3)
            return str(created["id"])
        except Exception as error:
            raise GoogleDriveSyncError(f"Google Drive 동기화 폴더를 만들지 못했습니다: {error}") from error

    def _find_remote_file(self, service: object, folder_id: str, name: str) -> str | None:
        info = self._find_remote_file_info(service, folder_id, name)
        return str(info.get("id")) if info is not None else None

    def _components_from_previous_generation(
        self, service: object, folder_id: str, target: str, generation: str,
    ) -> dict[str, dict[str, object]]:
        replaced = {"news_ai"}
        if target in {"settings", "both"}:
            replaced.add("settings")
        if target in {"themes", "both"}:
            replaced.add("themes")
        manifest_id = self._find_remote_file(service, folder_id, self.MANIFEST_REMOTE_NAME)
        if manifest_id is not None:
            manifest = self._download_manifest(service, manifest_id)
            components: dict[str, dict[str, object]] = {}
            for role, item in manifest["components"].items():
                if role in replaced:
                    continue
                content = self._download_remote_bytes(service, item["file_id"])
                if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                    raise GoogleDriveSyncError(f"기존 Drive v2 {role} 자료 검증에 실패해 새 묶음을 게시하지 않았습니다.")
                components[role] = dict(item)
            return components

        # Upgrade an existing v1 folder by freezing any untouched v1 component
        # into the first immutable generation before publishing the new pointer.
        legacy_names = {
            "settings": self.SETTINGS_REMOTE_NAME,
            "themes": self.THEMES_REMOTE_NAME,
        }
        components: dict[str, dict[str, object]] = {}
        for role, name in legacy_names.items():
            if role in replaced:
                continue
            file_id = self._find_remote_file(service, folder_id, name)
            if not file_id:
                continue
            content = self._download_remote_bytes(service, file_id)
            with tempfile.TemporaryDirectory(prefix="kiwoom_drive_legacy_") as directory:
                legacy_path = Path(directory) / name
                legacy_path.write_bytes(content)
                if role == "settings":
                    SettingsBackupService(self._database_path)._read_document(
                        legacy_path, True, False, False, True,
                    )
                else:
                    SettingsBackupService(self._database_path)._read_document(
                        legacy_path, False, True, False, False,
                    )
            immutable_name = f"kiwoom-monitor-v2-{generation}-{role}.json"
            created = service.files().create(
                body={"name": immutable_name, "parents": [folder_id]},
                media_body=self._media_upload(content), fields="id",
            ).execute(num_retries=3)
            components[role] = {
                "file_id": str(created["id"]), "name": immutable_name,
                "sha256": hashlib.sha256(content).hexdigest(), "size": len(content),
            }
        return components

    def _download_manifest(self, service: object, file_id: str) -> dict[str, object]:
        content = self._download_remote_bytes(service, file_id)
        try:
            manifest = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise GoogleDriveSyncError("Google Drive v2 manifest를 읽을 수 없습니다.") from error
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != self.BUNDLE_FORMAT
            or manifest.get("version") != 2
            or not isinstance(manifest.get("generation"), str)
            or not isinstance(manifest.get("components"), dict)
        ):
            raise GoogleDriveSyncError("Google Drive v2 manifest 형식이 올바르지 않습니다.")
        components = manifest["components"]
        if any(role not in {"settings", "themes", "news_ai"} for role in components):
            raise GoogleDriveSyncError("Google Drive v2 manifest 구성요소가 올바르지 않습니다.")
        for role, item in components.items():
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("file_id"), str)
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("sha256"), str)
                or len(item["sha256"]) != 64
                or type(item.get("size")) is not int
                or item["size"] < 0
            ):
                raise GoogleDriveSyncError(f"Google Drive v2 manifest의 {role} 정보가 손상됐습니다.")
        return manifest

    @staticmethod
    def _download_remote_bytes(service: object, file_id: str) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload

        buffer = BytesIO()
        downloader = MediaIoBaseDownload(buffer, service.files().get_media(fileId=file_id))
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue()

    def _download_v2(
        self, service: object, manifest_id: str, *, target: str, stage_only: bool,
    ) -> GoogleDriveSyncResult:
        manifest = self._download_manifest(service, manifest_id)
        components = manifest["components"]
        wanted = {"settings", "themes"} if target == "both" else {target}
        wanted.add("news_ai")
        local: dict[str, Path] = {}
        local_changes_applied = False
        try:
            with tempfile.TemporaryDirectory(prefix="kiwoom_drive_v2_") as directory:
                for role in wanted:
                    info = components.get(role)
                    if info is None:
                        continue
                    content = self._download_remote_bytes(service, info["file_id"])
                    if len(content) != info["size"] or hashlib.sha256(content).hexdigest() != info["sha256"]:
                        raise GoogleDriveSyncError(f"Google Drive v2 {role} 파일 검증에 실패했습니다.")
                    path = Path(directory) / f"{role}.json"
                    path.write_bytes(content)
                    local[role] = path
                settings_path = local.get("settings")
                themes_path = local.get("themes")
                news_ai_path = local.get("news_ai")
                if settings_path is not None:
                    SettingsBackupService(self._database_path)._read_document(
                        settings_path, True, False, False, True,
                    )
                if themes_path is not None:
                    SettingsBackupService(self._database_path)._read_document(
                        themes_path, False, True, False, False,
                    )
                if news_ai_path is not None:
                    NewsAIBackupService.validate_file(news_ai_path)
                if stage_only:
                    if not local:
                        return GoogleDriveSyncResult("restore", "empty", "Google Drive에 복원할 자료가 없습니다.")
                    operation_id = StrictRestoreCoordinator(
                        self._database_path, self._news_database_path,
                    ).stage(
                        settings=settings_path, themes=themes_path, news_ai=news_ai_path,
                        excluded_setting_keys=self.LOCAL_ONLY_SETTINGS,
                    )
                    return GoogleDriveSyncResult(
                        "restore", "staged",
                        "Google Drive 복원 자료를 검증해 보관했습니다. 앱을 정상 종료하고 다시 시작하면 "
                        f"복원이 적용됩니다. (작업 {operation_id[:8]})",
                    )
                SettingsBackupService(self._database_path).import_from_settings_and_themes(
                    settings_path, themes_path, self.LOCAL_ONLY_SETTINGS,
                    include_column_widths=False,
                )
                local_changes_applied = settings_path is not None or themes_path is not None
                if news_ai_path is not None:
                    NewsAIBackupService(self._news_database_path).import_from(news_ai_path)
                    local_changes_applied = True
        except Exception as error:
            local_changes_applied = local_changes_applied or bool(
                getattr(error, "local_database_applied", False),
            )
            raise GoogleDriveSyncError(
                f"Google Drive v2 {'복원 예약' if stage_only else '다운로드'}에 실패했습니다: {error}",
                local_changes_applied=local_changes_applied,
            ) from error
        return GoogleDriveSyncResult(
            "download", "completed", "Google Drive v2 백업 묶음을 검증하고 적용했습니다.",
            local_changes_applied,
        )

    @staticmethod
    def _find_remote_file_info(service: object, folder_id: str, name: str) -> dict[str, object] | None:
        try:
            response = service.files().list(
                spaces="drive",
                q=f"name = '{name}' and '{folder_id}' in parents and trashed = false",
                fields="files(id,modifiedTime)",
                pageSize=10,
            ).execute()
        except Exception as error:
            raise GoogleDriveSyncError(f"Google Drive 파일 목록을 읽지 못했습니다: {error}") from error
        files = response.get("files", [])
        if not files:
            return None
        return max(files, key=lambda item: str(item.get("modifiedTime", "")))

    def _selected_files(self, target: str) -> tuple[tuple[str, bool, bool], ...]:
        if target == "settings":
            return ((self.SETTINGS_REMOTE_NAME, True, False),)
        if target == "themes":
            return ((self.THEMES_REMOTE_NAME, False, True),)
        return ((self.SETTINGS_REMOTE_NAME, True, False), (self.THEMES_REMOTE_NAME, False, True))

    @staticmethod
    def _target_label(target: str) -> str:
        return {"settings": "설정", "themes": "테마", "both": "설정과 테마"}.get(target, "설정과 테마")

    @staticmethod
    def _media_upload(content: bytes):
        from googleapiclient.http import MediaIoBaseUpload

        resumable = len(content) >= GoogleDriveSyncService.RESUMABLE_UPLOAD_THRESHOLD_BYTES
        options = {"resumable": resumable}
        if resumable:
            options["chunksize"] = GoogleDriveSyncService.RESUMABLE_UPLOAD_CHUNK_BYTES
        return MediaIoBaseUpload(BytesIO(content), mimetype="application/json", **options)
