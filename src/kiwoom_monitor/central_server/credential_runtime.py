"""Bounded, server-owned credential operations and their secret-safe HTTP boundary."""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable
from starlette.requests import Request

from .credential_store import CredentialStore, CredentialRecord, PROVIDER_FIELDS


class CredentialOperationError(RuntimeError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


@dataclass(frozen=True)
class ValidatedCredential:
    validation: str = "VERIFIED"
    account_ref: str | None = None
    run_id: str | None = None
    prepared: Any = field(default=None, repr=False)


@dataclass(frozen=True)
class CredentialRuntimeHooks:
    # Concrete mock/news/real owners are connected in R3/R5/R6, respectively.
    prepare: Callable[[str, CredentialRecord | None, dict[str, str], bool], Awaitable[ValidatedCredential]]
    drain: Callable[[str], Awaitable[None]]
    publish: Callable[[str, CredentialRecord, ValidatedCredential], Awaitable[None]]
    resume: Callable[[str], Awaitable[None]]
    active_revision: Callable[[str], int | None] = lambda _: None
    drain_candidate: Callable[[str, ValidatedCredential], Awaitable[None]] | None = None
    release_candidate: Callable[[str, ValidatedCredential], None] = lambda *_: None
    begin_commit: Callable[[str, ValidatedCredential], None] = lambda *_: None
    supports_profile: Callable[[str], bool] = lambda _: True
    validation_status: Callable[[str], str | None] = lambda _: None


@dataclass
class _Operation:
    operation_id: str
    provider: str
    profile_id: str
    request_id: str
    digest: str = field(repr=False)
    expected_revision: int
    expires_monotonic: float
    expires_at: str
    credentials: dict[str, str] = field(repr=False)
    disabled: bool = False
    state: str = "VALIDATING"
    error_code: str | None = None
    committed: bool = False
    applying: bool = False
    revision: int | None = None
    binding_revision: int | None = None
    candidate: ValidatedCredential | None = field(default=None, repr=False)
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def document(self) -> dict[str, Any]:
        return {"operation_id": self.operation_id, "provider": self.provider, "profile_id": self.profile_id,
                "state": self.state, "error_code": self.error_code, "expected_revision": self.expected_revision,
                "revision": self.revision, "committed": self.committed, "binding_revision": self.binding_revision,
                "disabled": self.disabled,
                "phase": "APPLY" if self.applying else "PREPARE",
                "expires_at": self.expires_at, "validation": self.candidate.validation if self.candidate else None,
                "target_account_ref": self.candidate.account_ref if self.candidate else None}


class CredentialRuntime:
    MAX_PENDING = 32
    MAX_RETAINED = 256

    def __init__(self, vault: CredentialStore, store: Any, *, ttl: float = 300,
                 deadline: float = 30, monotonic: Callable[[], float] = time.monotonic):
        self.vault, self.store = vault, store
        self._ttl, self._deadline, self._now = ttl, deadline, monotonic
        self._guard = asyncio.Lock()
        self._operations: dict[str, _Operation] = {}
        self._recovery: dict[str, dict[str, Any]] = {}  # Safe activation metadata only, never credentials.
        self._hooks: dict[str, CredentialRuntimeHooks] = {}
        self._sweeper: asyncio.Task[None] | None = None
        self._closing = False

    def register(self, provider: str, hooks: CredentialRuntimeHooks) -> None:
        if provider not in PROVIDER_FIELDS or provider in self._hooks:
            raise ValueError("unsupported or duplicate credential runtime owner")
        self._hooks[provider] = hooks

    async def start(self) -> None:
        await self.recover_committed()
        self._sweeper = asyncio.create_task(self._sweep(), name="credential-operation-expiry")

    async def close(self) -> None:
        self._closing = True
        if self._sweeper is not None:
            self._sweeper.cancel()
            await asyncio.gather(self._sweeper, return_exceptions=True)
            self._sweeper = None
        for operation in tuple(self._operations.values()):
            if operation.state in {"VALIDATING", "READY"} or (operation.state == "BUSY" and not operation.applying):
                operation.state = "EXPIRED"
                self._forget_secrets(operation)
        await asyncio.gather(*(asyncio.shield(op.task) for op in self._operations.values() if op.task), return_exceptions=True)
        for operation in self._operations.values():
            self._forget_secrets(operation)

    async def _sweep(self) -> None:
        while True:
            async with self._guard:
                self._expire()
            await asyncio.sleep(1)

    def _forget_secrets(self, operation: _Operation) -> None:
        operation.credentials = {}
        if operation.candidate is not None:
            if operation.candidate.prepared is not None:
                self._hooks[operation.provider].release_candidate(operation.profile_id, operation.candidate)
            operation.candidate = ValidatedCredential(operation.candidate.validation,
                                                      operation.candidate.account_ref, operation.candidate.run_id)

    def _expire(self) -> None:
        for operation in self._operations.values():
            if (operation.state in {"VALIDATING", "READY"} or (operation.state == "BUSY" and not operation.applying)) and self._now() >= operation.expires_monotonic:
                operation.state = "EXPIRED"
                self._forget_secrets(operation)
        while len(self._operations) >= self.MAX_RETAINED:
            removable = next((key for key, op in self._operations.items()
                              if op.state not in {"VALIDATING", "READY", "DRAINING", "BUSY", "COMMITTING"}
                              and (op.task is None or op.task.done())), None)
            if removable is None:
                break
            del self._operations[removable]

    async def profiles(self) -> dict[str, Any]:
        result = []
        bindings = await asyncio.to_thread(self.store.load_account_bindings)
        for profile in await asyncio.to_thread(self.store.list_credential_profiles):
            if profile["lifecycle_state"] == "archived":
                continue
            provider, profile_id = profile["provider"], profile["profile_id"]
            try:
                record = await asyncio.to_thread(self.vault.load, provider, profile_id)
                revision = record.revision if record else 0
                hooks = self._hooks.get(provider)
                supported = hooks is not None and hooks.supports_profile(profile_id)
                active = hooks is not None and hooks.active_revision(profile_id) == revision
                state = "UNCONFIGURED" if record is None else "ACTIVE" if active else "NOT_SUPPORTED" if not supported else "RECOVERY_REQUIRED"
                result.append({**profile, "configured": bool(record and not record.disabled), "source": "VAULT" if record else "NONE",
                               "revision": revision, "runtime": state, "supported": supported,
                               "disabled": bool(record and record.disabled),
                               "account_ref": (record.payload.get("activation", {}).get("account_ref")
                                    or next((b["account_ref"] for b in reversed(bindings)
                                             if b["credential_profile_id"] == profile_id), None)) if record else None,
                               "validation": record.payload.get("validation", "UNVERIFIED") if record else None})
                result[-1]["runtime_validation"] = hooks.validation_status(profile_id) if active else None
            except Exception:
                result.append({**profile, "configured": False, "source": "VAULT", "revision": None,
                               "runtime": "RECOVERY_REQUIRED", "supported": provider in self._hooks})
        return {"profiles": result, "providers": [{"provider": name, "supported": name in self._hooks}
                                                   for name in PROVIDER_FIELDS]}

    async def create_profile(self, provider: str, request_id: str, label: str) -> dict[str, Any]:
        self._provider(provider)
        digest = await asyncio.to_thread(self.vault.request_digest, {"provider": provider, "request_id": request_id, "label": label})
        try:
            return await asyncio.to_thread(self.store.create_credential_profile, provider, request_id, label, digest)
        except ValueError:
            raise CredentialOperationError("PROFILE_REQUEST_CONFLICT") from None

    async def archive_profile(
        self, provider: str, profile_id: str, expected_revision: int,
    ) -> dict[str, Any]:
        """Hide a disconnected profile while retaining account identity and history."""
        self._provider(provider)
        if type(expected_revision) is not int or not 0 <= expected_revision < 2**63:
            raise CredentialOperationError("INVALID_CREDENTIAL_REQUEST", 422)
        async with self._guard:
            self._expire()
            if any(op.profile_id == profile_id and (
                    op.state in {"VALIDATING", "READY", "DRAINING", "BUSY", "COMMITTING"}
                    or (op.task is not None and not op.task.done())) for op in self._operations.values()):
                raise CredentialOperationError("PROFILE_BUSY")
            record = await asyncio.to_thread(self.vault.load, provider, profile_id)
            current_revision = record.revision if record is not None else 0
            if current_revision != expected_revision:
                raise CredentialOperationError("CREDENTIAL_REVISION_CONFLICT")
            if record is not None and not record.disabled:
                raise CredentialOperationError("PROFILE_MUST_BE_DISABLED")
            try:
                return await asyncio.to_thread(
                    self.store.archive_credential_profile, provider, profile_id,
                )
            except ValueError as error:
                code = str(error) if str(error) == "PROFILE_NOT_FOUND" else "PROFILE_ARCHIVE_FAILED"
                raise CredentialOperationError(code, 404 if code == "PROFILE_NOT_FOUND" else 409) from None

    async def rename_profile(
        self, provider: str, profile_id: str, expected_revision: int, label: str,
    ) -> dict[str, Any]:
        self._provider(provider)
        if provider not in {"kiwoom_mock", "kiwoom_real"}:
            raise CredentialOperationError("UNSUPPORTED_PROVIDER", 404)
        label = label.strip() if isinstance(label, str) else ""
        if not label or len(label) > 120:
            raise CredentialOperationError("INVALID_CREDENTIAL_REQUEST", 422)
        async with self._guard:
            current = await asyncio.to_thread(self.vault.load, provider, profile_id)
            if expected_revision != (current.revision if current else 0):
                raise CredentialOperationError("CREDENTIAL_REVISION_CONFLICT")
            try:
                return await asyncio.to_thread(
                    self.store.rename_credential_profile, provider, profile_id, label,
                )
            except ValueError as error:
                code = str(error)
                if code not in {"PROFILE_NOT_FOUND", "PROFILE_LABEL_INVALID"}:
                    code = "PROFILE_RENAME_FAILED"
                status = 404 if code == "PROFILE_NOT_FOUND" else 422 if code == "PROFILE_LABEL_INVALID" else 409
                raise CredentialOperationError(code, status) from None

    def _provider(self, provider: str) -> None:
        if provider not in PROVIDER_FIELDS:
            raise CredentialOperationError("UNSUPPORTED_PROVIDER", 404)

    async def prepare(self, provider: str, profile_id: str, request_id: str, expected_revision: int,
                      credentials: dict[str, str], *, disabled: bool = False) -> dict[str, Any]:
        self._provider(provider)
        # Validate exactly the allowed credential fields; never accept OAuth tokens.
        self.vault._validate_credentials(provider, credentials, disabled)
        digest = await asyncio.to_thread(self.vault.request_digest, {"provider": provider, "profile_id": profile_id,
            "request_id": request_id, "revision": expected_revision, "credentials": credentials, "disabled": disabled})
        async with self._guard:
            self._expire()
            for operation in self._operations.values():
                if (operation.provider, operation.profile_id, operation.request_id) == (provider, profile_id, request_id):
                    if not hmac.compare_digest(operation.digest, digest):
                        raise CredentialOperationError("REQUEST_ID_CONFLICT")
                    return operation.document()
            committed = await asyncio.to_thread(self.store.find_credential_activation, provider=provider,
                                                 profile_id=profile_id, request_id=request_id)
            if committed is None:
                committed = next((item for item in self._recovery.values() if
                    (item["provider"], item["profile_id"], item["request_id"]) == (provider, profile_id, request_id)), None)
            if committed:
                if not hmac.compare_digest(committed["request_digest"], digest):
                    raise CredentialOperationError("REQUEST_ID_CONFLICT")
                return self._committed_document(committed)
            if self._closing:
                raise CredentialOperationError("SERVER_CLOSING", 503)
            if provider not in self._hooks:
                raise CredentialOperationError("PROVIDER_RUNTIME_NOT_READY", 503)
            if not self._hooks[provider].supports_profile(profile_id):
                raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY", 503)
            profiles = await asyncio.to_thread(self.store.list_credential_profiles)
            if not any(p["provider"] == provider and p["profile_id"] == profile_id and p["lifecycle_state"] != "archived" for p in profiles):
                raise CredentialOperationError("PROFILE_NOT_FOUND", 404)
            pending = [op for op in self._operations.values() if op.state in {"VALIDATING", "READY", "DRAINING", "BUSY", "COMMITTING"}
                       or (op.task is not None and not op.task.done())]
            if len(pending) >= self.MAX_PENDING or any(op.profile_id == profile_id for op in pending):
                raise CredentialOperationError("PROFILE_BUSY")
            current = await asyncio.to_thread(self.vault.load, provider, profile_id)
            if expected_revision != (current.revision if current else 0):
                raise CredentialOperationError("CREDENTIAL_REVISION_CONFLICT")
            operation = _Operation(str(uuid.uuid4()), provider, profile_id, request_id, digest, expected_revision,
                                   self._now() + self._ttl, (datetime.now(UTC) + timedelta(seconds=self._ttl)).isoformat(),
                                   dict(credentials), disabled)
            self._operations[operation.operation_id] = operation
            operation.task = asyncio.create_task(self._validate(operation, current), name="credential-validate")
            return operation.document()

    async def _validate(self, op: _Operation, current: CredentialRecord | None) -> None:
        if op.state in {"EXPIRED", "CANCELLED"}:
            self._forget_secrets(op)
            return
        hooks = self._hooks[op.provider]
        candidate = None
        task = asyncio.create_task(hooks.prepare(op.profile_id, current, op.credentials, op.disabled))
        try:
            done, _ = await asyncio.wait({task}, timeout=self._deadline)
            if not done and op.state == "VALIDATING": op.state = "BUSY"
            candidate = await asyncio.shield(task)
            if op.state in {"EXPIRED", "CANCELLED"}: return
            if not done or self._now() >= op.expires_monotonic:
                op.state, op.error_code = "EXPIRED", "PREPARE_DEADLINE_EXCEEDED"
                return
            if not isinstance(candidate, ValidatedCredential) or candidate.validation not in {"VERIFIED", "UNVERIFIED"}:
                raise ValueError
            if candidate.validation == "UNVERIFIED" and op.provider not in {"dart", "openai", "gemini", "claude"}:
                raise ValueError
            if op.provider.startswith("kiwoom_"):
                if candidate.validation != "VERIFIED" or not candidate.account_ref or not candidate.run_id:
                    raise ValueError
                uuid.UUID(candidate.account_ref)
                uuid.UUID(candidate.run_id)
                scope = await asyncio.to_thread(self.store.resolve_account_scope, "kiwoom",
                                                op.provider.removeprefix("kiwoom_"), candidate.account_ref)
                if not scope["verified"] or scope["canonical_account_ref"] != candidate.account_ref:
                    raise ValueError
                bindings = await asyncio.to_thread(self.store.load_account_bindings)
                old = [binding for binding in bindings if binding["credential_profile_id"] == op.profile_id]
                if old and old[-1]["account_ref"] != candidate.account_ref:
                    op.state, op.error_code = "FAILED", "ACCOUNT_CHANGED"
                    return
            elif candidate.account_ref is not None or candidate.run_id is not None:
                raise ValueError
            op.candidate, op.state = candidate, "READY"
        except Exception as error:
            if op.state not in {"EXPIRED", "CANCELLED"}:
                code = error.code if isinstance(error, CredentialOperationError) and error.code in {
                    "ACCOUNT_CHANGED", "ACCOUNT_PROFILE_CONFLICT", "PROFILE_BUSY", "ACCOUNT_IDENTITY_UNVERIFIED",
                    "PROFILE_RUNTIME_NOT_READY",
                    "MARKET_PROFILE_REQUIRED",
                    "INVALID_CREDENTIAL", "CREDENTIAL_VALIDATION_RETRYABLE", "CREDENTIAL_VALIDATION_FAILED",
                } else "CREDENTIAL_VALIDATION_FAILED"
                op.state, op.error_code = "FAILED", code
        finally:
            if op.state != "READY":
                if isinstance(candidate, ValidatedCredential) and op.candidate is not candidate:
                    hooks.release_candidate(op.profile_id, candidate)
                self._forget_secrets(op)

    def _committed_document(self, stored: dict[str, Any]) -> dict[str, Any]:
        hooks = self._hooks.get(stored["provider"])
        active = (stored["operation_id"] not in self._recovery and hooks is not None
                  and hooks.active_revision(stored["profile_id"]) == stored["credential_revision"])
        return {"operation_id": stored["operation_id"], "provider": stored["provider"], "profile_id": stored["profile_id"],
                "state": "ACTIVE" if active else "RECOVERY_REQUIRED", "committed": True,
                "revision": stored["credential_revision"], "binding_revision": stored["binding_revision"],
                "target_account_ref": stored["account_ref"], "error_code": None if active else "RUNTIME_RECOVERY_REQUIRED"}

    async def status(self, operation_id: str) -> dict[str, Any]:
        async with self._guard:
            self._expire()
            if operation_id in self._operations: return self._operations[operation_id].document()
        committed = await asyncio.to_thread(self.store.find_credential_activation, operation_id=operation_id)
        if committed is None: committed = self._recovery.get(operation_id)
        if committed: return self._committed_document(committed)
        raise CredentialOperationError("OPERATION_EXPIRED_OR_UNKNOWN", 410)

    async def cancel(self, operation_id: str) -> dict[str, Any]:
        async with self._guard:
            self._expire()
            op = self._operations.get(operation_id)
            if op is None: raise CredentialOperationError("OPERATION_EXPIRED_OR_UNKNOWN", 410)
            if op.state == "BUSY" and not op.applying:
                op.state = "CANCELLED"
                self._forget_secrets(op)
                return op.document()
            if op.state not in {"VALIDATING", "READY", "CANCELLED", "EXPIRED"}:
                raise CredentialOperationError("APPLY_CANNOT_BE_CANCELLED")
            if op.state not in {"EXPIRED", "CANCELLED"}: op.state = "CANCELLED"
            self._forget_secrets(op)
            return op.document()

    async def apply(self, operation_id: str, expected_revision: int, target_account_ref: str | None) -> dict[str, Any]:
        async with self._guard:
            self._expire()
            op = self._operations.get(operation_id)
            if op is not None:
                if expected_revision != op.expected_revision:
                    raise CredentialOperationError("CREDENTIAL_REVISION_CONFLICT")
                if op.candidate is not None and op.candidate.account_ref != target_account_ref:
                    raise CredentialOperationError("TARGET_ACCOUNT_CONFIRMATION_REQUIRED")
                if op.applying and op.state in {"DRAINING", "BUSY", "COMMITTING", "ACTIVE", "RECOVERY_REQUIRED"}: return op.document()
                if op.state != "READY": raise CredentialOperationError("OPERATION_NOT_READY")
                op.state, op.applying = "DRAINING", True
                op.task = asyncio.create_task(self._apply(op), name="credential-apply")
                return op.document()
        stored = await asyncio.to_thread(self.store.find_credential_activation, operation_id=operation_id)
        if stored is None: stored = self._recovery.get(operation_id)
        if not stored: raise CredentialOperationError("OPERATION_EXPIRED_OR_UNKNOWN", 410)
        if expected_revision != stored["credential_revision"] - 1:
            raise CredentialOperationError("CREDENTIAL_REVISION_CONFLICT")
        if target_account_ref != stored["account_ref"]:
            raise CredentialOperationError("TARGET_ACCOUNT_CONFIRMATION_REQUIRED")
        return self._committed_document(stored)  # Durable replay never repeats an external apply.

    async def _apply(self, op: _Operation) -> None:
        hooks = self._hooks[op.provider]
        commit_started = False
        try:
            assert op.candidate is not None
            drain = asyncio.create_task(
                hooks.drain_candidate(op.profile_id, op.candidate) if hooks.drain_candidate is not None
                else hooks.drain(op.profile_id)
            )
            done, _ = await asyncio.wait({drain}, timeout=self._deadline)
            if not done: op.state = "BUSY"
            await asyncio.shield(drain)
            if not done or self._closing:
                await hooks.resume(op.profile_id)
                op.state, op.error_code = "FAILED", "APPLY_DEADLINE_EXCEEDED"
                return
            assert op.candidate is not None
            activation = {"operation_id": op.operation_id, "request_id": op.request_id, "request_digest": op.digest,
                          "account_ref": op.candidate.account_ref, "run_id": op.candidate.run_id,
                          "environment": op.provider.removeprefix("kiwoom_") if op.provider.startswith("kiwoom_") else None,
                          "committed_at": datetime.now(UTC).isoformat(), "disabled": op.disabled}
            op.state, commit_started = "COMMITTING", True
            hooks.begin_commit(op.profile_id, op.candidate)
            record = await asyncio.to_thread(self.vault.save, op.provider, op.profile_id, op.credentials,
                                             expected_revision=op.expected_revision, disabled=op.disabled, activation=activation,
                                             validation=op.candidate.validation)
            op.committed, op.revision = True, record.revision
            stored = await asyncio.to_thread(self.store.finalize_credential_activation, {
                **activation, "provider": op.provider, "profile_id": op.profile_id, "credential_revision": record.revision,
            })
            op.binding_revision = stored["binding_revision"]
            await hooks.publish(op.profile_id, record, op.candidate)
            await hooks.resume(op.profile_id)
            if hooks.active_revision(op.profile_id) != record.revision:
                raise RuntimeError("runtime revision unconfirmed")
            op.state = "ACTIVE"
        except Exception:
            if commit_started:
                op.state, op.error_code = "RECOVERY_REQUIRED", "ACTIVATION_RECOVERY_REQUIRED"
                try:
                    record = await asyncio.to_thread(self.vault.load, op.provider, op.profile_id)
                    if record and record.payload.get("activation", {}).get("operation_id") == op.operation_id:
                        op.committed, op.revision = True, record.revision
                except Exception:
                    pass  # Ambiguous commit remains fenced, never claims old-key rollback.
            else:
                op.state, op.error_code = "FAILED", "APPLY_FAILED"
                try: await hooks.resume(op.profile_id)
                except Exception: op.state, op.error_code = "RECOVERY_REQUIRED", "RUNTIME_RECOVERY_REQUIRED"
        finally:
            self._forget_secrets(op)

    async def recover_committed(self) -> None:
        for profile in await asyncio.to_thread(self.store.list_credential_profiles):
            pending = None
            try:
                record = await asyncio.to_thread(self.vault.load, profile["provider"], profile["profile_id"])
                activation = record.payload.get("activation") if record else None
                if activation:
                    pending = {
                        **activation, "provider": record.provider, "profile_id": record.profile_id, "credential_revision": record.revision,
                        "binding_revision": None,
                    }
                    await asyncio.to_thread(self.store.finalize_credential_activation,
                                            {key: value for key, value in pending.items() if key != "binding_revision"})
                    self._recovery.pop(pending["operation_id"], None)
            except Exception:
                if pending is not None: self._recovery[pending["operation_id"]] = pending
                continue  # One damaged provider cannot stop other services.


def install_credential_routes(app: Any, runtime: CredentialRuntime | None, authorize: Any,
                              trusted_proxies: tuple[str, ...] = (), account_owner: Any = None,
                              real_account_owner: Any = None) -> None:
    """Manual bounded parsing avoids Pydantic's raw input in 422 responses."""
    from fastapi import Depends
    from starlette.responses import JSONResponse

    trusted = {str(ipaddress.ip_address(address)) for address in trusted_proxies}
    prefix = "/api/v1/settings/credentials"
    operations = "/api/v1/settings/credential-operations"

    @app.middleware("http")
    async def credential_query_boundary(request: Request, call_next: Any):
        if (request.url.path.startswith(prefix) or request.url.path.startswith(operations)) and request.scope.get("query_string"):
            request.scope["_credential_query_present"] = True
            request.scope["query_string"] = b""  # Also removes query from the normal access-log scope.
        return await call_next(request)

    def enabled() -> CredentialRuntime:
        if runtime is None: raise CredentialOperationError("CREDENTIAL_VAULT_UNAVAILABLE", 503)
        return runtime

    def secure(request: Request) -> None:
        if request.scope.get("_credential_query_present"):
            raise CredentialOperationError("QUERY_NOT_ALLOWED", 400)
        if request.scope.get("scheme") == "https": return
        peer = request.client.host if request.client else ""
        try: peer = str(ipaddress.ip_address(peer))
        except ValueError: peer = ""
        proto = request.headers.getlist("x-forwarded-proto")
        if peer in trusted and proto == ["https"]: return
        raise CredentialOperationError("HTTPS_REQUIRED", 426)

    async def body(request: Request, fields: set[str], required: set[str]) -> dict[str, Any]:
        secure(request)
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 16_384: raise CredentialOperationError("BODY_TOO_LARGE", 413)
        try:
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result: raise ValueError
                    result[key] = value
                return result
            value = json.loads(content, object_pairs_hook=unique)
            if not isinstance(value, dict) or set(value) - fields or not required <= set(value): raise ValueError
            return value
        except Exception:
            raise CredentialOperationError("INVALID_CREDENTIAL_REQUEST", 422) from None

    def identifier(value: Any) -> str:
        try:
            if not isinstance(value, str): raise ValueError
            return str(uuid.UUID(value))
        except Exception:
            raise CredentialOperationError("INVALID_CREDENTIAL_REQUEST", 422) from None

    def revision(value: Any) -> int:
        if type(value) is not int or not 0 <= value < 2**63:
            raise CredentialOperationError("INVALID_CREDENTIAL_REQUEST", 422)
        return value

    @app.exception_handler(CredentialOperationError)
    async def operation_error(_: Request, error: CredentialOperationError):
        return JSONResponse({"detail": {"code": error.code}}, status_code=error.status)

    # Failures from storage/transport are never forwarded verbatim or logged here.
    async def safe_call(task: Awaitable[Any]) -> Any:
        try: return await task
        except CredentialOperationError: raise
        except Exception: raise CredentialOperationError("CREDENTIAL_STORAGE_UNAVAILABLE", 503) from None

    @app.get(prefix, dependencies=[Depends(authorize)])
    async def credential_profiles(request: Request):
        if request.scope.get("_credential_query_present"): raise CredentialOperationError("QUERY_NOT_ALLOWED", 400)
        return await safe_call(enabled().profiles())

    @app.post(prefix + "/{provider}/profiles", dependencies=[Depends(authorize)])
    async def create_profile(provider: str, request: Request):
        value = await body(request, {"request_id", "label"}, {"request_id", "label"})
        if not isinstance(value["label"], str) or len(value["label"]) > 120:
            raise CredentialOperationError("INVALID_CREDENTIAL_REQUEST", 422)
        return await safe_call(enabled().create_profile(provider, identifier(value["request_id"]), value["label"]))

    @app.delete(prefix + "/{provider}/profiles/{profile_id}", dependencies=[Depends(authorize)])
    async def archive_profile(provider: str, profile_id: str, request: Request):
        value = await body(request, {"expected_revision"}, {"expected_revision"})
        return await safe_call(enabled().archive_profile(
            provider, profile_id, revision(value["expected_revision"]),
        ))

    @app.put(prefix + "/{provider}/profiles/{profile_id}", dependencies=[Depends(authorize)])
    async def rename_profile(provider: str, profile_id: str, request: Request):
        value = await body(request, {"expected_revision", "label"}, {"expected_revision", "label"})
        if not isinstance(value["label"], str):
            raise CredentialOperationError("INVALID_CREDENTIAL_REQUEST", 422)
        return await safe_call(enabled().rename_profile(
            provider, profile_id, revision(value["expected_revision"]), value["label"],
        ))

    @app.post(prefix + "/{provider}/profiles/{profile_id}/prepare", dependencies=[Depends(authorize)], status_code=202)
    async def prepare(provider: str, profile_id: str, request: Request):
        value = await body(request, {"request_id", "expected_revision", "replacement", "disable"}, {"request_id", "expected_revision"})
        if type(value.get("disable", False)) is not bool or ("replacement" in value) == (value.get("disable") is True):
            raise CredentialOperationError("INVALID_CREDENTIAL_REQUEST", 422)
        credentials = value.get("replacement", {})
        if not isinstance(credentials, dict) or any(not isinstance(item, str) or len(item) > 4096 for item in credentials.values()):
            raise CredentialOperationError("INVALID_CREDENTIAL_REQUEST", 422)
        if provider not in PROVIDER_FIELDS: raise CredentialOperationError("UNSUPPORTED_PROVIDER", 404)
        if set(credentials) != (set() if value.get("disable") else set(PROVIDER_FIELDS[provider])) or any(not item.strip() for item in credentials.values()):
            raise CredentialOperationError("INVALID_CREDENTIAL_REQUEST", 422)
        return await safe_call(enabled().prepare(provider, profile_id, identifier(value["request_id"]),
                                                 revision(value["expected_revision"]), credentials, disabled=value.get("disable", False)))

    @app.get(operations + "/{operation_id}", dependencies=[Depends(authorize)])
    async def status(operation_id: str, request: Request):
        if request.scope.get("_credential_query_present"): raise CredentialOperationError("QUERY_NOT_ALLOWED", 400)
        return await safe_call(enabled().status(identifier(operation_id)))

    @app.post(operations + "/{operation_id}/apply", dependencies=[Depends(authorize)], status_code=202)
    async def apply(operation_id: str, request: Request):
        value = await body(request, {"expected_revision", "target_account_ref"}, {"expected_revision"})
        target = identifier(value["target_account_ref"]) if value.get("target_account_ref") is not None else None
        return await safe_call(enabled().apply(identifier(operation_id), revision(value["expected_revision"]), target))

    @app.delete(operations + "/{operation_id}", dependencies=[Depends(authorize)])
    async def cancel(operation_id: str, request: Request):
        secure(request)
        return await safe_call(enabled().cancel(identifier(operation_id)))

    if account_owner is not None or real_account_owner is not None:
        @app.put("/api/v1/settings/accounts/{account_ref}", dependencies=[Depends(authorize)])
        async def update_account_settings(account_ref: str, request: Request):
            value = await body(request, {"expected_revision", "active_profile_id", "monitor_enabled", "mock_order_enabled"},
                               {"expected_revision", "active_profile_id", "monitor_enabled", "mock_order_enabled"})
            query = list(request.query_params.multi_items())
            if (any(k not in {"environment", "broker"} for k, _ in query)
                    or len({k for k, _ in query}) != len(query)
                    or request.query_params.get("environment") not in {"mock", "real"}
                    or request.query_params.get("broker", "kiwoom") != "kiwoom"):
                raise CredentialOperationError("ACCOUNT_SETTINGS_INVALID", 422)
            scope = {"broker": "kiwoom", "environment": request.query_params["environment"],
                     "account_ref": identifier(account_ref)}
            expected = revision(value.pop("expected_revision"))
            selected = real_account_owner if scope["environment"] == "real" else account_owner
            if selected is None:
                raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY", 503)
            return await safe_call(selected.update_settings(scope, value, expected))
