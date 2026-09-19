"""NAS-only encrypted credentials. No token persistence or secret-reading API."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROVIDER_FIELDS = {
    "kiwoom_real": ("app_key", "secret_key"),
    "kiwoom_mock": ("app_key", "secret_key"),
    "naver": ("client_id", "client_secret"),
    "dart": ("api_key",), "openai": ("api_key",),
    "gemini": ("api_key",), "claude": ("api_key",),
}


class CredentialStoreError(RuntimeError):
    """Stable error codes only; never include supplied credentials."""


@dataclass(frozen=True)
class CredentialRecord:
    provider: str
    profile_id: str
    revision: int
    disabled: bool
    payload: dict[str, Any] = field(repr=False)


class CredentialStore:
    """One owning server process; threads share a lock, processes cannot share a vault.

    Metadata store contains only initialization and revision fences. The encrypted
    file is the commit point; a failed fence update is reconciled on next load.
    """

    def __init__(self, directory: str | Path, metadata_store: Any):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self._aes_type = AESGCM
        self._metadata = metadata_store
        self._lock = threading.RLock()
        self._directory = Path(directory).absolute()
        for path in (self._directory, *self._directory.parents):
            if path.is_symlink():
                raise CredentialStoreError("UNSAFE_SECRET_PATH")
        self._directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(self._directory, 0o700)
        self._lease = None
        self._acquire_lease()

    def _path(self, name: str) -> Path:
        path = self._directory / name
        if path.is_symlink():
            raise CredentialStoreError("UNSAFE_SECRET_PATH")
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise CredentialStoreError("UNSAFE_SECRET_PATH")
        if path.exists() and os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise CredentialStoreError("UNSAFE_SECRET_PERMISSIONS")
        return path

    def _acquire_lease(self) -> None:
        path = self._path("vault.lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        lease = os.fdopen(fd, "r+b", buffering=0)
        try:
            if os.name == "nt":
                import msvcrt
                if path.stat().st_size == 0:
                    lease.write(b"0")
                lease.seek(0)
                msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lease.close()
            raise CredentialStoreError("VAULT_ALREADY_OWNED") from None
        self._lease = lease

    def close(self) -> None:
        with self._lock:
            if self._lease is not None:
                self._lease.close()
                self._lease = None

    def _identity(self, provider: str, profile_id: str) -> str:
        if provider not in PROVIDER_FIELDS or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", profile_id):
            raise CredentialStoreError("INVALID_CREDENTIAL_PROFILE")
        if self._lease is None:
            raise CredentialStoreError("VAULT_CLOSED")
        return f"{provider}--{profile_id}"

    def _marker(self, identity: str) -> dict[str, Any] | None:
        rows = self._metadata.load_documents("credential_vault_state", identity, 1)
        return dict(rows[0]["document"]) if rows else None

    def _mark(self, identity: str, revision: int) -> None:
        self._metadata.upsert_documents("credential_vault_state", [{
            "owner": identity, "key": "state", "document": {
                "initialized": True, "revision": revision,
            },
        }])

    def _master(self, *, create: bool = False) -> bytes:
        path = self._path("master.key")
        if not path.exists():
            if not create or any(self._directory.glob("*.json")) or self._metadata.load_documents(
                "credential_vault_state", "", 1,
            ):
                raise CredentialStoreError("RECOVERY_REQUIRED")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(self._aes_type.generate_key(bit_length=256))
                stream.flush()
                os.fsync(stream.fileno())
            self._sync_directory()
        value = path.read_bytes()
        if len(value) != 32:
            raise CredentialStoreError("RECOVERY_REQUIRED")
        return value

    def _sync_directory(self) -> None:
        if os.name != "nt":
            fd = os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _atomic_write(self, path: Path, value: bytes) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=self._directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            self._path(path.name)
            os.replace(temporary, path)
            self._sync_directory()
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _aad(self, provider: str, profile_id: str, revision: int) -> bytes:
        return json.dumps([1, provider, profile_id, revision], separators=(",", ":")).encode()

    def load(self, provider: str, profile_id: str) -> CredentialRecord | None:
        with self._lock:
            identity = self._identity(provider, profile_id)
            path = self._path(f"{identity}.json")
            marker = self._marker(identity)
            if not path.exists():
                if marker or self._metadata.load_credential_activations(profile_id):
                    raise CredentialStoreError("RECOVERY_REQUIRED")
                return None
            try:
                envelope = json.loads(path.read_bytes())
                revision = envelope["active_revision"]
                if (envelope["schema_version"] != 1 or envelope["provider"] != provider
                        or envelope["profile_id"] != profile_id or type(revision) is not int
                        or revision < 1 or revision < int((marker or {}).get("revision", 0))):
                    raise ValueError
                active = envelope["active"]
                plaintext = self._aes_type(self._master()).decrypt(
                    base64.b64decode(active["nonce"], validate=True),
                    base64.b64decode(active["ciphertext"], validate=True),
                    self._aad(provider, profile_id, revision),
                )
                payload = json.loads(plaintext)
                if type(payload["disabled"]) is not bool:
                    raise ValueError
                self._validate_credentials(provider, payload["credentials"], payload["disabled"])
                if not marker or int(marker["revision"]) < revision:
                    self._mark(identity, revision)
                return CredentialRecord(provider, profile_id, revision, payload["disabled"], payload)
            except Exception:
                raise CredentialStoreError("RECOVERY_REQUIRED") from None

    def _validate_credentials(self, provider: str, credentials: dict[str, str], disabled: bool) -> None:
        if not isinstance(credentials, dict) or set(credentials) != (set() if disabled else set(PROVIDER_FIELDS[provider])):
            raise CredentialStoreError("INVALID_CREDENTIAL_FIELDS")
        if any(not isinstance(value, str) or not value.strip() for value in credentials.values()):
            raise CredentialStoreError("INCOMPLETE_CREDENTIALS")

    def save(self, provider: str, profile_id: str, credentials: dict[str, str], *,
             expected_revision: int, disabled: bool = False,
             activation: dict[str, Any] | None = None, validation: str = "UNVERIFIED") -> CredentialRecord:
        with self._lock:
            identity = self._identity(provider, profile_id)
            if type(disabled) is not bool or type(expected_revision) is not int or expected_revision < 0:
                raise CredentialStoreError("INVALID_CREDENTIAL_REVISION")
            if validation not in {"VERIFIED", "UNVERIFIED"}:
                raise CredentialStoreError("INVALID_CREDENTIAL_VALIDATION")
            allowed_activation = {"operation_id", "request_id", "request_digest", "account_ref", "run_id",
                                  "environment", "committed_at", "verification_method", "label", "disabled"}
            if activation is not None and (not isinstance(activation, dict) or set(activation) - allowed_activation):
                raise CredentialStoreError("INVALID_ACTIVATION_FIELDS")
            self._validate_credentials(provider, credentials, disabled)
            current = self.load(provider, profile_id)
            if expected_revision != (current.revision if current else 0):
                raise CredentialStoreError("CREDENTIAL_REVISION_CONFLICT")
            revision = expected_revision + 1
            master = self._master(create=current is None)
            payload = {"disabled": disabled, "credentials": dict(credentials), "validation": validation,
                       "activation": dict(activation or {})}
            nonce = os.urandom(12)
            ciphertext = self._aes_type(master).encrypt(
                nonce, json.dumps(payload, sort_keys=True).encode(), self._aad(provider, profile_id, revision),
            )
            path = self._path(f"{identity}.json")
            previous = json.loads(path.read_bytes()) if current else None
            envelope = {"schema_version": 1, "provider": provider, "profile_id": profile_id,
                        "active_revision": revision, "active": {
                            "nonce": base64.b64encode(nonce).decode(),
                            "ciphertext": base64.b64encode(ciphertext).decode(),
                        }, "previous": {"revision": previous["active_revision"],
                                        "active": previous["active"]} if previous else None}
            if current is None:
                self._mark(identity, 0)  # Durable before first file; missing file never revives env.
            self._atomic_write(path, json.dumps(envelope, sort_keys=True).encode())
            self._mark(identity, revision)
            return CredentialRecord(provider, profile_id, revision, disabled, payload)

    def request_digest(self, value: dict[str, Any]) -> str:
        with self._lock:
            if self._lease is None:
                raise CredentialStoreError("VAULT_CLOSED")
            return hmac.new(self._master(create=True), b"request-digest\0" + json.dumps(
                value, sort_keys=True, separators=(",", ":"),
            ).encode(), hashlib.sha256).hexdigest()

    def import_initial(self, provider: str, profile_id: str, credentials: dict[str, str]) -> CredentialRecord | None:
        with self._lock:
            stored = self.load(provider, profile_id)
            if stored is not None or not any(credentials.values()):
                return stored
            return self.save(provider, profile_id, credentials, expected_revision=0)


def compose_credential_settings(settings: Any, vault: CredentialStore, store: Any) -> tuple[Any, dict[str, str]]:
    """Legacy default routes only. Explicit multi-account route selection is R2+.

    A pending encrypted activation is finalized idempotently before using its keys.
    A damaged provider never falls back to env; other providers remain available.
    """
    from dataclasses import replace
    from datetime import datetime, UTC
    field_map = {
        "kiwoom_real": ("kiwoom_app_key", "kiwoom_secret_key"),
        "kiwoom_mock": ("kiwoom_mock_app_key", "kiwoom_mock_secret_key"),
        "naver": ("naver_news_client_id", "naver_news_client_secret"),
        "dart": ("dart_api_key",), "openai": ("openai_api_key",),
        "gemini": ("gemini_api_key",), "claude": ("anthropic_api_key",),
    }
    # KIWOOM_ENVIRONMENT=mock remains a legacy main route, distinct from mock monitor.
    changes: dict[str, Any] = {}
    statuses: dict[str, str] = {}
    entries = [(provider, fields, f"nas-{provider.removeprefix('kiwoom_')}-default")
               for provider, fields in field_map.items()]
    if settings.kiwoom_environment == "mock":
        entries = [entry for entry in entries if entry[0] != "kiwoom_real"]
        entries.append(("kiwoom_mock", ("kiwoom_app_key", "kiwoom_secret_key"), "nas-main-mock-default"))
    for provider, fields, profile_id in entries:
        credentials = dict(zip(PROVIDER_FIELDS[provider], (getattr(settings, name) for name in fields), strict=True))
        try:
            record = vault.import_initial(provider, profile_id, credentials)
            if record is None:
                statuses[provider if profile_id != "nas-main-mock-default" else "legacy_main_mock"] = "UNCONFIGURED"
                continue
            store.register_credential_profile(provider, profile_id, datetime.now(UTC).isoformat())
            activations = store.load_credential_activations(profile_id)
            if activations and int(activations[-1]["credential_revision"]) > record.revision:
                raise CredentialStoreError("RECOVERY_REQUIRED")
            activation = record.payload.get("activation") or {}
            if activation:
                stored_activation = store.finalize_credential_activation({
                    **activation, "provider": provider, "profile_id": profile_id,
                    "credential_revision": record.revision,
                })
                if provider == "kiwoom_mock" and "kiwoom_mock_app_key" in fields:
                    changes.update(mock_account_ref=stored_activation["account_ref"] or "",
                                   mock_execution_run_id=stored_activation["run_id"] or "")
            statuses[provider if profile_id != "nas-main-mock-default" else "legacy_main_mock"] = "DISABLED" if record.disabled else "ACTIVE"
            values = record.payload["credentials"]
            changes.update({name: values.get(key, "") for name, key in zip(fields, PROVIDER_FIELDS[provider], strict=True)})
        except Exception:
            statuses[provider if profile_id != "nas-main-mock-default" else "legacy_main_mock"] = "RECOVERY_REQUIRED"
            changes.update({name: "" for name in fields})
    result = replace(settings, **changes)
    if not result.kiwoom_mock_configured or not result.mock_account_ref or not result.mock_execution_run_id:
        result = replace(result, mock_account_monitor_enabled=False, mock_order_transport_enabled=False)
    return result, statuses
