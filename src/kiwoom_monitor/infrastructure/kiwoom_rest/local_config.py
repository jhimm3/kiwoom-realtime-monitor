from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from ctypes import wintypes

from .settings import KiwoomSettings
from . import local_secret_keychain as keychain
from .local_secret_keychain import LocalSecretUnavailable


_kernel32 = None
_CONFIG_HEADER = "KIWOOM_CONFIG_ENCRYPTED="
if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p


class _DataBlob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte)))


def _protect(data: bytes, *, path: Path) -> bytes:
    if sys.platform == 'darwin':
        return keychain.protect(data, path)
    if sys.platform != "win32" or _kernel32 is None:
        raise LocalSecretUnavailable("이 환경의 로컬 자격증명 보호 저장소를 사용할 수 없습니다.")
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), "KiwoomRealtimeMonitor", None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _unprotect(data: bytes, *, path: Path) -> bytes:
    if sys.platform == 'darwin':
        return keychain.unprotect(data, path)
    if data.startswith(keychain.MAC_KEYCHAIN_ENVELOPE):
        raise LocalSecretUnavailable('local credential belongs to another protection format')
    if sys.platform != "win32" or _kernel32 is None:
        raise LocalSecretUnavailable("이 환경의 로컬 자격증명 보호 저장소를 사용할 수 없습니다.")
    buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    result = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _write_protected_text(path: Path, text: str) -> None:
    if sys.platform != 'darwin':
        path.write_text(text, encoding='utf-8')
        return
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.')
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class ApiProfiles:
    mock_app_key: str = field(default="", repr=False)
    mock_secret_key: str = field(default="", repr=False)
    real_app_key: str = field(default="", repr=False)
    real_secret_key: str = field(default="", repr=False)
    active_environment: str = "mock"


class LocalApiConfig:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load_profiles(self) -> ApiProfiles:
        if not self._path.exists():
            return ApiProfiles()
        raw = self._path.read_text(encoding="utf-8").strip()
        if raw.startswith(_CONFIG_HEADER):
            values = self._load_protected_profiles(raw)
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

    def _load_protected_profiles(self, raw: str) -> dict:
        encoded = raw.removeprefix(_CONFIG_HEADER)
        return json.loads(_unprotect(base64.b64decode(encoded), path=self._path).decode('utf-8'))

    def save_profiles(self, profiles: ApiProfiles) -> None:
        if sys.platform == 'darwin' and self._path.exists():
            existing = self._path.read_text(encoding='utf-8').strip()
            if not existing.startswith(_CONFIG_HEADER):
                raise LocalSecretUnavailable('local credential belongs to another protection format')
            self._load_protected_profiles(existing)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(profiles.__dict__, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encrypted = base64.b64encode(_protect(payload, path=self._path)).decode("ascii")
        _write_protected_text(self._path, f"{_CONFIG_HEADER}{encrypted}\n")
