"""Separate Linux OAuth store: encrypted records, private files, one flock owner.

This directory is outside the API-key vault and all ordinary backups/builds.
Provisioning/import is an offline SSH operation; HTTP never accepts tokens.
"""
from __future__ import annotations

import json
import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from .chatgpt_auth import ChatGPTCredentialStore, ChatGPTPlanError, _ProtectedRecord


class LinuxChatGPTCredentialStore(ChatGPTCredentialStore):
    def __init__(self, path: Path):
        super().__init__(path)
        self.key_path = self.path.parent / "session.key"

    def _directory(self, *, create=False):
        if create:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Reject symbolic links in the entire directory chain. The dedicated
        # leaf must be private and owned by the running identity.
        for parent in (self.path.parent, *self.path.parent.parents):
            if parent.is_symlink():
                raise ChatGPTPlanError("storage_unavailable")
        metadata = self.path.parent.stat()
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ChatGPTPlanError("storage_unavailable")

    def _read(self, path: Path, limit=1024 * 1024) -> bytes:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                    or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > limit):
                raise ChatGPTPlanError("storage_unavailable")
            return stream.read(limit + 1)

    def _publish(self, path: Path, data: bytes):
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def locked(self):
        import fcntl
        with self._lock:
            descriptor = None
            try:
                self._directory(create=True)
                descriptor = os.open(self.path.with_suffix(".lock"),
                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                metadata = os.fstat(descriptor)
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                        or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600):
                    raise ChatGPTPlanError("storage_unavailable")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ChatGPTPlanError("storage_busy") from None
            except ChatGPTPlanError:
                if descriptor is not None:
                    os.close(descriptor)
                raise
            except Exception:
                if descriptor is not None:
                    os.close(descriptor)
                raise ChatGPTPlanError("storage_unavailable") from None
            try:
                yield
            finally:
                if descriptor is not None:
                    os.close(descriptor)

    def load(self) -> dict:
        try:
            if not self.path.parent.exists():
                return _ProtectedRecord(host_id="", active="", accounts={})
            self._directory()
            if not self.path.exists() and not self.path.is_symlink():
                return _ProtectedRecord(host_id="", active="", accounts={})
            from cryptography.fernet import Fernet
            value = json.loads(Fernet(self._read(self.key_path, 128)).decrypt(self._read(self.path)))
            if not isinstance(value, dict) or not isinstance(value.get("accounts"), dict):
                raise ValueError()
            value["accounts"] = {key: _ProtectedRecord(account) for key, account in value["accounts"].items()}
            return _ProtectedRecord(value)
        except Exception:
            raise ChatGPTPlanError("storage_unavailable") from None

    def save(self, value: dict) -> None:
        try:
            from cryptography.fernet import Fernet
            self._directory(create=True)
            if not self.key_path.exists() and not self.key_path.is_symlink():
                # Called only while the cross-process ownership lock is held.
                self._publish(self.key_path, Fernet.generate_key())
            cipher = Fernet(self._read(self.key_path, 128))
            self._publish(self.path, cipher.encrypt(json.dumps(value, separators=(",", ":")).encode()))
        except Exception:
            raise ChatGPTPlanError("storage_unavailable") from None
