from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient


_LOCAL_EXACT = {
    "ui_mode", "ui_font_size", "ui_row_height", "theme_badge_font_size", "show_server_clock",
    "google_drive_auto_download", "google_drive_auto_upload", "google_drive_auto_upload_on_exit",
    "google_drive_sync_target", "google_drive_unsynced_changes", "google_drive_local_changed_at",
    "google_drive_last_upload_success_at", "krx_stock_catalog_date", "kind_name_history_sync_date",
    "daily_high_adjusted_basis_version", "historical_high_adjusted_basis_version",
}
_LOCAL_PREFIXES = ("window_", "settings_dialog_", "theme_manager_", "theme_image_import_dir", "theme_excel_import_dir")
_LOCAL_SUFFIXES = ("_image",)


def is_shared_setting(key: str) -> bool:
    """PC 화면·파일·동기화 상태가 아닌 실제 모니터 동작 설정만 공유한다."""
    return (
        key not in _LOCAL_EXACT
        and not key.startswith(_LOCAL_PREFIXES)
        and not key.endswith(_LOCAL_SUFFIXES)
        and not (key.startswith("near_high_sound_") and key not in {
            "near_high_sound_enabled", "near_high_sound_cooldown_seconds",
        })
    )


class CentralSettingsSyncService:
    COLLECTION = "app_settings"
    COLUMN_COLLECTION = "app_column_settings"

    def __init__(self, client: CentralContentClient) -> None:
        self._client = client

    def sync(self, database_path: Path) -> int:
        if not database_path.is_file():
            return 0
        remote = self._client.load_all(self.COLLECTION)
        columns_supported = True
        try:
            remote_columns = self._client.load_all(self.COLUMN_COLLECTION)
        except RuntimeError as error:
            # 새 컬렉션을 아직 지원하지 않는 NAS 이미지와도 설정 동기화는 유지한다.
            if "HTTP 404" not in str(error):
                raise
            columns_supported = False
            remote_columns = []
        connection = sqlite3.connect(database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        column_documents: list[dict[str, Any]] = []
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS central_setting_versions ("
                "setting_key TEXT PRIMARY KEY, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS central_column_setting_versions ("
                "column_name TEXT PRIMARY KEY, updated_at TEXT NOT NULL)"
            )
            has_columns = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='column_settings'"
            ).fetchone() is not None
            with connection:
                if not remote:
                    # 비어 있는 개인 서버의 최초 PC만 현재 설정을 기준본으로 삼는다.
                    now = datetime.now(UTC).isoformat()
                    connection.executemany(
                        "INSERT OR IGNORE INTO central_setting_versions(setting_key,updated_at) VALUES(?,?)",
                        ((str(row[0]), now) for row in connection.execute("SELECT key FROM settings")
                         if is_shared_setting(str(row[0]))),
                    )
                for value in remote:
                    document = value.get("document")
                    if not isinstance(document, dict):
                        continue
                    key = str(document.get("key") or value.get("key") or "")
                    updated_at = str(document.get("updated_at", ""))
                    if not key or not updated_at or not is_shared_setting(key):
                        continue
                    local = connection.execute(
                        "SELECT updated_at FROM central_setting_versions WHERE setting_key=?", (key,),
                    ).fetchone()
                    if local is not None and str(local[0]) >= updated_at:
                        continue
                    connection.execute(
                        "INSERT INTO settings(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, str(document.get("value", ""))),
                    )
                    connection.execute(
                        "INSERT INTO central_setting_versions(setting_key,updated_at) VALUES(?,?) "
                        "ON CONFLICT(setting_key) DO UPDATE SET updated_at=excluded.updated_at",
                        (key, updated_at),
                    )
                if columns_supported and has_columns and not remote_columns:
                    now = datetime.now(UTC).isoformat()
                    names = tuple(str(row[0]) for row in connection.execute("SELECT column_name FROM column_settings"))
                    connection.executemany(
                        "INSERT OR IGNORE INTO central_column_setting_versions(column_name,updated_at) "
                        "SELECT column_name,? FROM column_settings WHERE column_name=?",
                        ((now, name) for name in names),
                    )
                for value in remote_columns if columns_supported and has_columns else ():
                    document = value.get("document")
                    if not isinstance(document, dict):
                        continue
                    name = str(document.get("column_name") or value.get("key") or "")
                    updated_at = str(document.get("updated_at", ""))
                    if not name or not updated_at:
                        continue
                    exists = connection.execute(
                        "SELECT 1 FROM column_settings WHERE column_name=?", (name,),
                    ).fetchone()
                    local = connection.execute(
                        "SELECT updated_at FROM central_column_setting_versions WHERE column_name=?", (name,),
                    ).fetchone()
                    if exists is None or (local is not None and str(local[0]) >= updated_at):
                        continue
                    connection.execute(
                        "UPDATE column_settings SET visible=?,position=? WHERE column_name=?",
                        (int(bool(document.get("visible"))), int(document.get("position", 0)), name),
                    )
                    connection.execute(
                        "INSERT INTO central_column_setting_versions(column_name,updated_at) VALUES(?,?) "
                        "ON CONFLICT(column_name) DO UPDATE SET updated_at=excluded.updated_at",
                        (name, updated_at),
                    )
                documents = [{
                    "owner": "default", "key": str(row[0]),
                    "document": {"key": str(row[0]), "value": str(row[1]), "updated_at": str(row[2])},
                } for row in connection.execute(
                    "SELECT s.key,s.value,v.updated_at FROM settings s "
                    "JOIN central_setting_versions v ON v.setting_key=s.key"
                ) if is_shared_setting(str(row[0]))]
                if columns_supported and has_columns:
                    column_documents = [{
                        "owner": "main_table", "key": str(row[0]),
                        "document": {
                            "column_name": str(row[0]), "visible": bool(row[1]),
                            "position": int(row[2]), "updated_at": str(row[3]),
                        },
                    } for row in connection.execute(
                        "SELECT c.column_name,c.visible,c.position,v.updated_at FROM column_settings c "
                        "JOIN central_column_setting_versions v ON v.column_name=c.column_name"
                    )]
        finally:
            connection.close()
        saved = self._client.upsert(self.COLLECTION, documents) if documents else 0
        return saved + (self._client.upsert(self.COLUMN_COLLECTION, column_documents) if column_documents else 0)
