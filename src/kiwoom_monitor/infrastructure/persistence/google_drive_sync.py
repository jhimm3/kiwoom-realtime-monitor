"""Google Drive appDataFolder 기반 설정·테마 동기화."""

from __future__ import annotations

import base64
import json
import tempfile
from io import BytesIO
from pathlib import Path

from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import _protect, _unprotect

from .settings_backup import SettingsBackupService


class GoogleDriveSyncError(RuntimeError):
    pass


class GoogleDriveSyncService:
    """개인 Drive의 앱 전용 숨김 폴더에 하나의 동기화 문서를 저장한다."""

    SCOPES = ("https://www.googleapis.com/auth/drive.appdata",)
    REMOTE_NAME = "kiwoom-monitor-sync-v1.json"

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._data_dir = database_path.parent
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

    def upload(self, interactive: bool = False) -> str:
        service = self._drive_service(interactive)
        with tempfile.TemporaryDirectory(prefix="kiwoom_drive_") as directory:
            source = Path(directory) / self.REMOTE_NAME
            SettingsBackupService(self._database_path).export_to(source)
            content = source.read_bytes()
        media = self._media_upload(content)
        existing = self._find_remote_file(service)
        try:
            if existing:
                service.files().update(fileId=existing, media_body=media, fields="id,modifiedTime").execute()
            else:
                service.files().create(
                    body={"name": self.REMOTE_NAME, "parents": ["appDataFolder"]},
                    media_body=media,
                    fields="id,modifiedTime",
                ).execute()
        except Exception as error:
            raise GoogleDriveSyncError(f"Google Drive 업로드에 실패했습니다: {error}") from error
        return "Google Drive에 설정·테마를 업로드했습니다."

    def download(self, interactive: bool = False) -> str:
        service = self._drive_service(interactive)
        file_id = self._find_remote_file(service)
        if not file_id:
            return "Google Drive에 아직 동기화된 설정이 없습니다."
        try:
            from googleapiclient.http import MediaIoBaseDownload

            request = service.files().get_media(fileId=file_id)
            buffer = BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            with tempfile.TemporaryDirectory(prefix="kiwoom_drive_") as directory:
                source = Path(directory) / self.REMOTE_NAME
                source.write_bytes(buffer.getvalue())
                SettingsBackupService(self._database_path).import_from(source)
        except Exception as error:
            raise GoogleDriveSyncError(f"Google Drive 다운로드에 실패했습니다: {error}") from error
        return "Google Drive 설정·테마를 다운로드했습니다. 프로그램을 다시 시작하면 모두 적용됩니다."

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

    def _find_remote_file(self, service: object) -> str | None:
        try:
            response = service.files().list(
                spaces="appDataFolder",
                q=f"name = '{self.REMOTE_NAME}' and trashed = false",
                fields="files(id,modifiedTime)",
                pageSize=10,
            ).execute()
        except Exception as error:
            raise GoogleDriveSyncError(f"Google Drive 파일 목록을 읽지 못했습니다: {error}") from error
        files = response.get("files", [])
        if not files:
            return None
        return str(max(files, key=lambda item: str(item.get("modifiedTime", ""))).get("id"))

    @staticmethod
    def _media_upload(content: bytes):
        from googleapiclient.http import MediaIoBaseUpload

        return MediaIoBaseUpload(BytesIO(content), mimetype="application/json", resumable=False)
