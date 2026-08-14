from __future__ import annotations

import base64
import ctypes
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .settings import KiwoomSettings


_KEYCHAIN_MARKER = "KIWOOM_CONFIG_KEYCHAIN=1"
_KEYCHAIN_SERVICE = "kiwoom-realtime-monitor"
_KEYCHAIN_ACCOUNT = "api-profiles"


if sys.platform == "win32":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    class _DataBlob(ctypes.Structure):
        _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte)))


def _protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Windows 전용 암호화 함수입니다.")
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), "KiwoomRealtimeMonitor", None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Windows 전용 암호화 함수입니다.")
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


@dataclass(frozen=True)
class ApiProfiles:
    mock_app_key: str = ""
    mock_secret_key: str = ""
    real_app_key: str = ""
    real_secret_key: str = ""
    active_environment: str = "mock"


class LocalApiConfig:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load_profiles(self) -> ApiProfiles:
        if not self._path.exists():
            return ApiProfiles()
        raw = self._path.read_text(encoding="utf-8").strip()
        if raw == _KEYCHAIN_MARKER:
            return self._load_from_keychain()
        if raw.startswith("KIWOOM_CONFIG_ENCRYPTED="):
            if sys.platform != "win32":
                raise ValueError("Windows에서 만든 API 설정입니다. macOS에서 API 키를 다시 입력해 주세요.")
            values = json.loads(_unprotect(base64.b64decode(raw.split("=", 1)[1])).decode("utf-8"))
            if "mock_app_key" in values:
                return ApiProfiles(**values)
            environment = str(values.get("environment", "mock"))
            return ApiProfiles(
                mock_app_key=str(values.get("app_key", "")) if environment == "mock" else "",
                mock_secret_key=str(values.get("secret_key", "")) if environment == "mock" else "",
                real_app_key=str(values.get("app_key", "")) if environment == "real" else "",
                real_secret_key=str(values.get("secret_key", "")) if environment == "real" else "",
                active_environment=environment,
            )
        legacy = KiwoomSettings.from_env_file(self._path)
        return ApiProfiles(
            mock_app_key=legacy.app_key if legacy.environment == "mock" else "",
            mock_secret_key=legacy.secret_key if legacy.environment == "mock" else "",
            real_app_key=legacy.app_key if legacy.environment == "real" else "",
            real_secret_key=legacy.secret_key if legacy.environment == "real" else "",
            active_environment=legacy.environment,
        )

    def load(self) -> KiwoomSettings:
        profiles = self.load_profiles()
        if profiles.active_environment == "real":
            return KiwoomSettings(profiles.real_app_key, profiles.real_secret_key, "real")
        return KiwoomSettings(profiles.mock_app_key, profiles.mock_secret_key, "mock")

    def save_profiles(self, profiles: ApiProfiles) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(profiles.__dict__, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if sys.platform == "darwin":
            self._save_to_keychain(payload.decode("utf-8"))
            self._path.write_text(f"{_KEYCHAIN_MARKER}\n", encoding="utf-8")
            return
        encrypted = base64.b64encode(_protect(payload)).decode("ascii")
        self._path.write_text(f"KIWOOM_CONFIG_ENCRYPTED={encrypted}\n", encoding="utf-8")

    @staticmethod
    def _save_to_keychain(payload: str) -> None:
        result = subprocess.run(
            ["security", "add-generic-password", "-U", "-a", _KEYCHAIN_ACCOUNT, "-s", _KEYCHAIN_SERVICE, "-w", payload],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("macOS 키체인에 API 설정을 저장하지 못했습니다.")

    @staticmethod
    def _load_from_keychain() -> ApiProfiles:
        if sys.platform != "darwin":
            raise ValueError("macOS 키체인 설정은 macOS에서만 열 수 있습니다.")
        result = subprocess.run(
            ["security", "find-generic-password", "-a", _KEYCHAIN_ACCOUNT, "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("macOS 키체인에서 API 설정을 찾지 못했습니다. API 키를 다시 입력해 주세요.")
        try:
            values = json.loads(result.stdout)
            return ApiProfiles(**values)
        except (json.JSONDecodeError, TypeError) as error:
            raise ValueError("macOS 키체인 API 설정 형식이 올바르지 않습니다.") from error
