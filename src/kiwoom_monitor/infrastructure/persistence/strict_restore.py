"""Durable, next-start Google Drive restore for the local desktop data folder.

Only the explicitly requested restore uses this path. Every cooperating desktop
process holds a shared OS file lock while it can access the local databases; the
startup restore holds the exclusive lock until commit or rollback is durable.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .news_ai_backup import NewsAIBackupService
from .settings_backup import SettingsBackupService


class StrictRestoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class RestoreOutcome:
    state: str
    message: str


if sys.platform == "win32":
    from ctypes import wintypes

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]


class AppDataLease:
    """A shared lifetime lease or an exclusive startup restore lease."""

    def __init__(
        self, data_dir: Path, *, exclusive: bool, timeout: float = 30.0,
        filename: str = ".strict_restore.lock",
    ) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self._stream = (data_dir / filename).open("a+b")
        self._exclusive = exclusive
        self._overlapped = _Overlapped() if sys.platform == "win32" else None
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self._try_lock():
                    break
                if time.monotonic() >= deadline:
                    mode = "복원" if exclusive else "앱 시작"
                    raise StrictRestoreError(f"다른 앱 프로세스가 사용 중이어서 {mode} 잠금을 얻지 못했습니다.")
                time.sleep(0.1)
        except BaseException:
            self._stream.close()
            raise

    def _try_lock(self) -> bool:
        if sys.platform == "win32":
            import msvcrt

            kernel = ctypes.windll.kernel32
            kernel.LockFileEx.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped),
            ]
            kernel.LockFileEx.restype = wintypes.BOOL
            flags = 0x00000001 | (0x00000002 if self._exclusive else 0)
            handle = wintypes.HANDLE(msvcrt.get_osfhandle(self._stream.fileno()))
            if kernel.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(self._overlapped)):
                return True
            code = ctypes.get_last_error() or kernel.GetLastError()
            if code in {32, 33, 158}:
                return False
            raise OSError(code, "복원 잠금 파일을 잠글 수 없습니다.")
        import fcntl

        try:
            fcntl.flock(
                self._stream.fileno(),
                (fcntl.LOCK_EX if self._exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB,
            )
            return True
        except BlockingIOError:
            return False

    def close(self) -> None:
        if self._stream.closed:
            return
        if sys.platform == "win32":
            import msvcrt

            kernel = ctypes.windll.kernel32
            kernel.UnlockFileEx.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                wintypes.DWORD, ctypes.POINTER(_Overlapped),
            ]
            kernel.UnlockFileEx.restype = wintypes.BOOL
            handle = wintypes.HANDLE(msvcrt.get_osfhandle(self._stream.fileno()))
            kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(self._overlapped))
        else:
            import fcntl

            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        self._stream.close()

    def __enter__(self) -> AppDataLease:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.",
            suffix=".tmp", delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_document(path: Path, document: dict[str, Any]) -> None:
    _atomic_bytes(
        path,
        (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    if not source_path.is_file():
        raise StrictRestoreError(f"복원 대상 DB가 없습니다: {source_path.name}")
    temporary = destination_path.with_name(destination_path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    source = sqlite3.connect(source_path)
    try:
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    try:
        with temporary.open("r+b") as saved:
            os.fsync(saved.fileno())
        os.replace(temporary, destination_path)
    finally:
        temporary.unlink(missing_ok=True)


class StrictRestoreCoordinator:
    _INPUT_NAMES = {"settings": "settings.json", "themes": "themes.json", "news_ai": "news_ai.json"}
    _ACTIVE_STATES = {"STAGED", "PREPARED", "APPLYING", "ROLLING_BACK"}

    def __init__(self, monitor_database: Path, news_database: Path) -> None:
        self.monitor_database = monitor_database.resolve()
        self.news_database = news_database.resolve()
        self.data_dir = self.monitor_database.parent
        if self.news_database.parent != self.data_dir:
            raise StrictRestoreError("설정과 뉴스 DB가 같은 앱 데이터 폴더에 있어야 합니다.")
        self.root = self.data_dir / "strict_restore"
        self.pointer = self.root / "current.json"

    def stage(
        self, *, settings: Path | None, themes: Path | None,
        news_ai: Path | None, excluded_setting_keys: frozenset[str],
    ) -> str:
        """Freeze validated downloaded inputs without changing operating data."""
        with AppDataLease(
            self.data_dir, exclusive=True, filename=".strict_restore_stage.lock",
        ):
            return self._stage_locked(
                settings=settings, themes=themes, news_ai=news_ai,
                excluded_setting_keys=excluded_setting_keys,
            )

    def _stage_locked(
        self, *, settings: Path | None, themes: Path | None,
        news_ai: Path | None, excluded_setting_keys: frozenset[str],
    ) -> str:
        existing = self._read_pointer()
        if existing is not None and existing["state"] in self._ACTIVE_STATES:
            raise StrictRestoreError("아직 완료되지 않은 엄격 복원 요청이 있습니다.")
        inputs = {"settings": settings, "themes": themes, "news_ai": news_ai}
        if not any(inputs.values()):
            raise StrictRestoreError("복원할 Google Drive 자료가 없습니다.")
        backup = SettingsBackupService(self.monitor_database)
        if settings is not None:
            backup._read_document(settings, True, False, False, True)
        if themes is not None:
            backup._read_document(themes, False, True, False, False)
        if news_ai is not None:
            NewsAIBackupService.validate_file(news_ai)
        operation_id = uuid.uuid4().hex
        operation_dir = self.root / "operations" / operation_id
        operation_dir.mkdir(parents=True, exist_ok=False)
        fixed: dict[str, dict[str, Any]] = {}
        try:
            for role, source in inputs.items():
                if source is None:
                    continue
                target = operation_dir / self._INPUT_NAMES[role]
                _atomic_bytes(target, source.read_bytes())
                fixed[role] = {"sha256": _sha256(target), "size": target.stat().st_size}
            manifest = {
                "version": 1, "operation_id": operation_id, "state": "STAGED",
                "inputs": fixed,
                "excluded_setting_keys": sorted(excluded_setting_keys),
                "before": None,
            }
            _atomic_document(self.pointer, manifest)
        except BaseException:
            shutil.rmtree(operation_dir, ignore_errors=True)
            raise
        return operation_id

    def enter_main(self, *, timeout: float = 30.0) -> tuple[AppDataLease, RestoreOutcome | None]:
        """Finish or undo a pending restore before normal database initialization."""
        pointer = self._read_pointer()
        if pointer is None or pointer["state"] not in self._ACTIVE_STATES:
            lease = AppDataLease(self.data_dir, exclusive=False, timeout=timeout)
            # A request could have been published while acquiring the shared lock.
            current = self._read_pointer()
            if current is None or current["state"] not in self._ACTIVE_STATES:
                return lease, None
            lease.close()
        with AppDataLease(self.data_dir, exclusive=True, timeout=timeout):
            pointer = self._read_pointer()
            outcome = self._complete_pending(pointer) if pointer is not None else None
        return AppDataLease(self.data_dir, exclusive=False, timeout=timeout), outcome

    def enter_child(self, *, timeout: float = 30.0) -> AppDataLease:
        lease = AppDataLease(self.data_dir, exclusive=False, timeout=timeout)
        try:
            pointer = self._read_pointer()
            if pointer is not None and pointer["state"] in {"APPLYING", "ROLLING_BACK"}:
                raise StrictRestoreError("복원 진행 중에는 보조 프로세스를 시작할 수 없습니다.")
            return lease
        except BaseException:
            lease.close()
            raise

    def _read_pointer(self) -> dict[str, Any] | None:
        if not self.pointer.exists():
            return None
        try:
            document = json.loads(self.pointer.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise StrictRestoreError("복원 원장을 읽지 못했습니다. 데이터 변경 전에 시작을 중단합니다.") from error
        if not isinstance(document, dict) or document.get("version") != 1:
            raise StrictRestoreError("지원하지 않는 복원 원장입니다.")
        operation_id = document.get("operation_id")
        if not isinstance(operation_id, str) or len(operation_id) != 32 or any(char not in "0123456789abcdef" for char in operation_id):
            raise StrictRestoreError("복원 작업 식별자가 손상됐습니다.")
        if document.get("state") not in self._ACTIVE_STATES | {"COMMITTED", "ROLLED_BACK"}:
            raise StrictRestoreError("복원 상태가 손상됐습니다.")
        if not isinstance(document.get("inputs"), dict) or any(
            role not in self._INPUT_NAMES for role in document["inputs"]
        ):
            raise StrictRestoreError("복원 입력 목록이 손상됐습니다.")
        excluded = document.get("excluded_setting_keys")
        if not isinstance(excluded, list) or any(not isinstance(key, str) for key in excluded):
            raise StrictRestoreError("복원 설정 제외 목록이 손상됐습니다.")
        return document

    def _input_path(self, manifest: dict[str, Any], role: str) -> Path | None:
        info = manifest["inputs"].get(role)
        if info is None:
            return None
        if not isinstance(info, dict) or type(info.get("size")) is not int or not isinstance(info.get("sha256"), str):
            raise StrictRestoreError("복원 입력 지문이 손상됐습니다.")
        path = self.root / "operations" / manifest["operation_id"] / self._INPUT_NAMES[role]
        if not path.is_file() or path.stat().st_size != info["size"] or _sha256(path) != info["sha256"]:
            raise StrictRestoreError("복원 입력 파일이 손상됐습니다.")
        return path

    def _complete_pending(self, manifest: dict[str, Any]) -> RestoreOutcome | None:
        state = manifest["state"]
        if state in {"COMMITTED", "ROLLED_BACK"}:
            return None
        if state in {"APPLYING", "ROLLING_BACK"}:
            self._rollback(manifest)
            return RestoreOutcome("ROLLED_BACK", "중단된 Google Drive 복원을 이전 상태로 되돌렸습니다.")
        settings = self._input_path(manifest, "settings")
        themes = self._input_path(manifest, "themes")
        news_ai = self._input_path(manifest, "news_ai")
        required = self.monitor_database.stat().st_size if self.monitor_database.is_file() else 0
        if news_ai is not None and self.news_database.is_file():
            required += self.news_database.stat().st_size
        # SQLite backups and journal files need working space before any live write.
        if shutil.disk_usage(self.data_dir).free < required * 2 + 64 * 1024 * 1024:
            raise StrictRestoreError("복구 자료를 안전하게 보존할 디스크 공간이 부족합니다.")
        before = self._capture_before(manifest, settings, themes, news_ai)
        manifest["before"] = before
        manifest["state"] = "PREPARED"
        _atomic_document(self.pointer, manifest)
        manifest["state"] = "APPLYING"
        _atomic_document(self.pointer, manifest)
        try:
            SettingsBackupService(self.monitor_database).import_from_settings_and_themes(
                settings, themes,
                frozenset(manifest.get("excluded_setting_keys", [])),
                include_column_widths=False,
            )
            if news_ai is not None:
                NewsAIBackupService(self.news_database).import_from(news_ai)
            synced_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            connection = sqlite3.connect(self.monitor_database)
            try:
                with connection:
                    connection.executemany(
                        "INSERT INTO settings(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (
                            ("google_drive_unsynced_changes", "0"),
                            ("google_drive_local_changed_at", synced_at),
                            ("google_drive_last_upload_success_at", synced_at),
                        ),
                    )
            finally:
                connection.close()
        except Exception as error:
            self._rollback(manifest)
            return RestoreOutcome("ROLLED_BACK", f"Google Drive 복원 실패로 이전 상태를 되돌렸습니다: {error}")
        manifest["state"] = "COMMITTED"
        _atomic_document(self.pointer, manifest)
        return RestoreOutcome("COMMITTED", "Google Drive 설정·테마·뉴스 AI 복원을 완료했습니다.")

    def _capture_before(
        self, manifest: dict[str, Any], settings: Path | None,
        themes: Path | None, news_ai: Path | None,
    ) -> dict[str, Any]:
        operation_dir = self.root / "operations" / manifest["operation_id"]
        databases: dict[str, dict[str, Any]] = {}
        for role, active in (("monitor", self.monitor_database), ("news", self.news_database)):
            if role == "news" and news_ai is None:
                continue
            if role == "news" and not active.is_file():
                databases[role] = {"existed": False}
                continue
            saved = operation_dir / f"before_{role}.sqlite3"
            _sqlite_backup(active, saved)
            databases[role] = {"existed": True, "sha256": _sha256(saved), "size": saved.stat().st_size}
        files: list[dict[str, Any]] = []
        if settings is not None:
            service = SettingsBackupService(self.monitor_database)
            document = service._read_document(settings, True, False, False, True)
            targets = [self.data_dir / "naver_news.dat"]
            targets.extend(path for path, _ in service._validate_assets(document.get("assets", [])))
            for index, target in enumerate(targets):
                relative = self._safe_relative(target)
                entry: dict[str, Any] = {"path": relative.as_posix(), "existed": target.is_file()}
                if target.is_file():
                    saved = operation_dir / f"before_file_{index}"
                    _atomic_bytes(saved, target.read_bytes())
                    entry.update({"backup": saved.name, "sha256": _sha256(saved), "size": saved.stat().st_size})
                files.append(entry)
        return {"databases": databases, "files": files}

    def _safe_relative(self, path: Path) -> Path:
        try:
            relative = path.resolve().relative_to(self.data_dir.resolve())
        except ValueError as error:
            raise StrictRestoreError("복원 파일이 앱 데이터 폴더 밖을 가리킵니다.") from error
        if relative.as_posix() == "naver_news.dat":
            return relative
        if len(relative.parts) < 2:
            raise StrictRestoreError("복원 자산 경로가 허용 범위를 벗어났습니다.")
        stored = (Path("data") / relative).as_posix()
        resolved = SettingsBackupService._resolve_asset_path(self.data_dir.parent, stored)
        if resolved is None or resolved[0] != path.resolve():
            raise StrictRestoreError("복원 자산 경로가 허용 범위를 벗어났습니다.")
        return relative

    def _rollback(self, manifest: dict[str, Any]) -> None:
        before = manifest.get("before")
        if not isinstance(before, dict) or not isinstance(before.get("databases"), dict) or not isinstance(before.get("files"), list):
            raise StrictRestoreError("변경 전 자료 원장이 손상돼 앱을 시작할 수 없습니다.")
        operation_dir = self.root / "operations" / manifest["operation_id"]
        # Verify every before-image before changing any operating file.
        for role, info in before["databases"].items():
            if role not in {"monitor", "news"}:
                raise StrictRestoreError("복구 DB 목록이 손상됐습니다.")
            if info == {"existed": False} and role == "news":
                continue
            if not isinstance(info, dict) or info.get("existed") is not True:
                raise StrictRestoreError("복구 DB 상태가 손상됐습니다.")
            self._verify_before(operation_dir / f"before_{role}.sqlite3", info)
        for item in before["files"]:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise StrictRestoreError("복구 파일 목록이 손상됐습니다.")
            self._safe_relative(self.data_dir / item["path"])
            if item.get("existed") is True:
                if not isinstance(item.get("backup"), str) or re.fullmatch(r"before_file_\d+", item["backup"]) is None:
                    raise StrictRestoreError("복구 파일 경로가 손상됐습니다.")
                self._verify_before(operation_dir / item["backup"], item)
            elif item.get("existed") is not False:
                raise StrictRestoreError("복구 파일 상태가 손상됐습니다.")
        manifest["state"] = "ROLLING_BACK"
        _atomic_document(self.pointer, manifest)
        for role in before["databases"]:
            saved = operation_dir / f"before_{role}.sqlite3"
            active = self.monitor_database if role == "monitor" else self.news_database
            if before["databases"][role] == {"existed": False}:
                for path in (active, Path(str(active) + "-wal"), Path(str(active) + "-shm")):
                    path.unlink(missing_ok=True)
                continue
            source = sqlite3.connect(saved)
            destination = sqlite3.connect(active)
            try:
                source.backup(destination)
                if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise StrictRestoreError(f"{role} DB 복구 검증에 실패했습니다.")
            finally:
                destination.close()
                source.close()
        for item in before["files"]:
            destination = self.data_dir / item["path"]
            if item["existed"]:
                _atomic_bytes(destination, (operation_dir / item["backup"]).read_bytes())
            else:
                destination.unlink(missing_ok=True)
        manifest["state"] = "ROLLED_BACK"
        _atomic_document(self.pointer, manifest)

    @staticmethod
    def _verify_before(path: Path, info: Any) -> None:
        if not isinstance(info, dict) or type(info.get("size")) is not int or not isinstance(info.get("sha256"), str):
            raise StrictRestoreError("변경 전 자료 지문이 손상됐습니다.")
        if not path.is_file() or path.stat().st_size != info["size"] or _sha256(path) != info["sha256"]:
            raise StrictRestoreError("변경 전 자료 파일이 손상돼 자동 복구할 수 없습니다.")
