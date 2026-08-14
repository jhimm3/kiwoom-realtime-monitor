from __future__ import annotations

import base64
import ctypes
import json
from dataclasses import dataclass
from pathlib import Path
from ctypes import wintypes

from .settings import KiwoomSettings


class _DataBlob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte)))


def _protect(data: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), "KiwoomRealtimeMonitor", None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


def _unprotect(data: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(result.pbData)


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
        if raw.startswith("KIWOOM_CONFIG_ENCRYPTED="):
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
        encrypted = base64.b64encode(_protect(payload)).decode("ascii")
        self._path.write_text(f"KIWOOM_CONFIG_ENCRYPTED={encrypted}\n", encoding="utf-8")
