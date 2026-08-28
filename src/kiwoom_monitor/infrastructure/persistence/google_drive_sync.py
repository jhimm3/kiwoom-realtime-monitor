"""Google Drive 일반 폴더 기반 설정·테마 동기화."""

from __future__ import annotations

import base64
import json
import tempfile
from io import BytesIO
from pathlib import Path

from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import _protect, _unprotect

from .settings_backup import SettingsBackupService
from .news_ai_backup import NewsAIBackupService


class GoogleDriveSyncError(RuntimeError):
    pass


class GoogleDriveSyncService:
    """개인 Drive의 사용자 표시 폴더에 하나의 동기화 문서를 저장한다."""

    SCOPES = ("https://www.googleapis.com/auth/drive.file",)
    FOLDER_NAME = "키움 실시간 모니터"
    LEGACY_REMOTE_NAME = "kiwoom-monitor-sync-v1.json"
    SETTINGS_REMOTE_NAME = "kiwoom-monitor-settings-v1.json"
    THEMES_REMOTE_NAME = "kiwoom-monitor-themes-v1.json"
    NEWS_AI_REMOTE_NAME = "kiwoom-monitor-news-ai-v1.json"
    LOCAL_ONLY_SETTINGS = frozenset({
        "window_width",
        "window_height",
        "settings_dialog_width",
        "settings_dialog_height",
        "google_drive_unsynced_changes",
        "google_drive_local_changed_at",
        "google_drive_last_upload_success_at",
    })

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
        service = self._drive_service(interactive)
        folder_id = self._find_sync_folder(service)
        if not folder_id:
            return ""
        try:
            modified_times = [
                str(info.get("modifiedTime", ""))
                for name, _, _ in self._selected_files(target)
                if (info := self._find_remote_file_info(service, folder_id, name)) is not None
            ]
            if target in {"settings", "both"}:
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

    def upload(self, interactive: bool = False, target: str = "both") -> str:
        service = self._drive_service(interactive)
        folder_id = self._ensure_sync_folder(service)
        files = self._selected_files(target)
        try:
            with tempfile.TemporaryDirectory(prefix="kiwoom_drive_") as directory:
                for name, include_settings, include_themes in files:
                    source = Path(directory) / name
                    SettingsBackupService(self._database_path).export_to(
                        source,
                        include_settings,
                        include_themes,
                        self.LOCAL_ONLY_SETTINGS,
                        include_column_widths=False,
                    )
                    existing = self._find_remote_file(service, folder_id, name)
                    media = self._media_upload(source.read_bytes())
                    if existing:
                        service.files().update(fileId=existing, media_body=media, fields="id,modifiedTime").execute()
                    else:
                        service.files().create(body={"name": name, "parents": [folder_id]}, media_body=media, fields="id,modifiedTime").execute()
                if target in {"settings", "both"}:
                    source = Path(directory) / self.NEWS_AI_REMOTE_NAME
                    NewsAIBackupService(self._news_database_path).export_to(source)
                    existing = self._find_remote_file(service, folder_id, self.NEWS_AI_REMOTE_NAME)
                    media = self._media_upload(source.read_bytes())
                    if existing:
                        service.files().update(fileId=existing, media_body=media, fields="id,modifiedTime").execute()
                    else:
                        service.files().create(
                            body={"name": self.NEWS_AI_REMOTE_NAME, "parents": [folder_id]},
                            media_body=media, fields="id,modifiedTime",
                        ).execute()
        except Exception as error:
            raise GoogleDriveSyncError(f"Google Drive 업로드에 실패했습니다: {error}") from error
        return f"Google Drive에 {self._target_label(target)}을(를) 업로드했습니다."

    def download(self, interactive: bool = False, target: str = "both") -> str:
        service = self._drive_service(interactive)
        folder_id = self._find_sync_folder(service)
        files = self._selected_files(target)
        remote_files = [(name, self._find_remote_file(service, folder_id, name) if folder_id else None, include_settings, include_themes) for name, include_settings, include_themes in files]
        ai_file_id = (
            self._find_remote_file(service, folder_id, self.NEWS_AI_REMOTE_NAME)
            if folder_id and target in {"settings", "both"}
            else None
        )
        # 분리 저장 전의 단일 파일도 한 번은 읽어 기존 업로드를 잃지 않는다.
        if folder_id and not any(file_id for _, file_id, _, _ in remote_files):
            legacy_id = self._find_remote_file(service, folder_id, self.LEGACY_REMOTE_NAME)
            if legacy_id:
                remote_files = [(self.LEGACY_REMOTE_NAME, legacy_id, include_settings, include_themes) for _, include_settings, include_themes in files]
        if not any(file_id for _, file_id, _, _ in remote_files) and not ai_file_id:
            return "Google Drive에 아직 동기화된 설정이 없습니다."
        try:
            from googleapiclient.http import MediaIoBaseDownload
            with tempfile.TemporaryDirectory(prefix="kiwoom_drive_") as directory:
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
                    SettingsBackupService(self._database_path).import_from(
                        source,
                        include_settings,
                        include_themes,
                        self.LOCAL_ONLY_SETTINGS,
                        include_column_widths=False,
                    )
                if target in {"settings", "both"} and folder_id:
                    if ai_file_id:
                        buffer = BytesIO()
                        downloader = MediaIoBaseDownload(buffer, service.files().get_media(fileId=ai_file_id))
                        done = False
                        while not done:
                            _, done = downloader.next_chunk()
                        source = Path(directory) / self.NEWS_AI_REMOTE_NAME
                        source.write_bytes(buffer.getvalue())
                        NewsAIBackupService(self._news_database_path).import_from(source)
        except Exception as error:
            raise GoogleDriveSyncError(f"Google Drive 다운로드에 실패했습니다: {error}") from error
        return f"Google Drive {self._target_label(target)}을(를) 다운로드하고 바로 적용했습니다."

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
            ).execute()
            return str(created["id"])
        except Exception as error:
            raise GoogleDriveSyncError(f"Google Drive 동기화 폴더를 만들지 못했습니다: {error}") from error

    def _find_remote_file(self, service: object, folder_id: str, name: str) -> str | None:
        info = self._find_remote_file_info(service, folder_id, name)
        return str(info.get("id")) if info is not None else None

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

        return MediaIoBaseUpload(BytesIO(content), mimetype="application/json", resumable=False)
