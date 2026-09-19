"""One mock account's client, execution owner and connection lifetime."""

from __future__ import annotations

import asyncio
import hmac
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kiwoom_monitor.application.account_identity import (
    bind_verified_account_identity, prepare_verified_account_identity,
)
from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope, AccountBinding
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomSettings
from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import (
    KiwoomAccountIdentityReader, account_identity_fingerprint,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import KiwoomMockAccountReader
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import make_mock_transport
from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository

from .execution_runtime import ExecutionRuntime, ManualMockOrderGateway
from .market_observations import as_kst
from .mock_account_monitor import MockAccountMonitor, MockAccountRealtimeCollector
from .rest_broker import CentralRestBroker, MOCK_ACCOUNT_ENDPOINTS
from .credential_runtime import CredentialRuntimeHooks, CredentialOperationError, ValidatedCredential
from .account_query import AccountQuerySessionManager


@dataclass
class _MockCredentialPlan:
    client: Any = field(repr=False)
    broker: Any = field(repr=False)
    prepared: Any = field(repr=False)
    identity: Any = field(repr=False)
    previous: Any = field(repr=False)
    account_ref: str
    run_id: str
    settings_revision: int
    lock: asyncio.Lock = field(repr=False)
    previous_revision: int | None = None
    previous_settings_revision: int | None = None
    previous_monitor_enabled: bool = False
    drained: bool = False
    published: bool = False
    resumed: bool = False
    committed: bool = False
    previous_disabled: bool = False


class MockCredentialOwner:
    """Own profile bundles, account exclusion and the mock activation boundary."""

    def __init__(self, store: Any, vault: Any, *, hmac_key: bytes,
                 legacy_bundle: Any = None, legacy_monitor_enabled: bool = False, on_change: Any = None):
        from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient
        self.store, self.vault, self.hmac_key = store, vault, hmac_key
        self._legacy_monitor_enabled = legacy_monitor_enabled
        self._bundles: dict[str, MockAccountBundle] = {}
        self._contexts = {"nas-mock-default": legacy_bundle} if legacy_bundle is not None else {}
        self._revisions: dict[str, int] = {}
        self._settings_revisions: dict[str, int] = {}
        self._disabled_scopes: dict[str, dict[str, str]] = {}
        self._settings_tasks: set[asyncio.Task] = set()
        self.errors: dict[str, str] = {}
        self.reserved_accounts: set[str] = set()
        self.reserved_profiles: set[str] = set()
        self._account_locks: dict[str, asyncio.Lock] = {}
        self._applying: dict[str, _MockCredentialPlan] = {}
        self._brokers: set[CentralRestBroker] = set()
        self._slots = asyncio.Semaphore(2)
        self._boot_slots = asyncio.Semaphore(2)
        self._probe_lock = asyncio.Lock()
        self._probe = CentralRestBroker(KiwoomRestClient(KiwoomSettings("", "", "mock")),
                                      allowed_endpoints=MOCK_ACCOUNT_ENDPOINTS, namespace="mock-credential-probe")
        self._booting: set[str] = set()
        self._pending_profiles: set[str] = set()
        self._boot_tasks: list[asyncio.Task[None]] = []
        self._start_task = None
        self._closing = False
        self._close_task = None
        self._on_change = on_change or (lambda *_: None)

    def hooks(self) -> CredentialRuntimeHooks:
        return CredentialRuntimeHooks(self.prepare, self._unused_drain, self.publish, self.resume,
                                      self.active_revision, self.drain, self.release, self.begin_commit,
                                      supports_profile=lambda p: p not in self.reserved_profiles)

    def begin_commit(self, profile_id, candidate):
        candidate.prepared.committed = True

    async def _unused_drain(self, profile_id: str) -> None:
        raise RuntimeError("MOCK_CANDIDATE_REQUIRED")

    def bundle(self, profile_id: str = "nas-mock-default"):
        return self._bundles.get(profile_id)

    def account_bindings(self):
        """Snapshot admitted query contexts; dormant/history accounts stay outside."""
        return tuple(b.binding for b in self._bundles.values() if b.binding is not None and b.account_queries is not None)

    def active_revision(self, profile_id: str):
        return self._revisions.get(profile_id)

    def applied_settings_revision(self, scope: dict[str, str]):
        if scope["environment"] != "mock": return None
        bundle = next((b for b in self._bundles.values() if b.account_ref == scope["account_ref"]), None)
        profile = bundle.credential_profile_id if bundle else next(
            (p for p, s in self._disabled_scopes.items() if s == scope), None)
        return self._settings_revisions.get(profile)

    async def start(self) -> None:
        if self._closing: raise RuntimeError("MOCK_OWNER_CLOSED")
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start(), name="mock-owner-start")
        await asyncio.shield(self._start_task)

    async def _start(self) -> None:
        for profile in await asyncio.to_thread(self.store.list_credential_profiles):
            if profile["provider"] != "kiwoom_mock" or profile["lifecycle_state"] != "active": continue
            profile_id = profile["profile_id"]
            if profile_id in self.reserved_profiles: continue
            try:
                record = await asyncio.to_thread(self.vault.load, "kiwoom_mock", profile_id)
            except Exception:
                self.errors[profile_id] = "ACTIVATION_RECOVERY_REQUIRED"; continue
            if record is None: continue
            if record.disabled:
                activation = record.payload.get("activation", {})
                if activation.get("account_ref"):
                    scope = self._scope(activation["account_ref"])
                    settings = await asyncio.to_thread(self.store.load_account_settings, scope)
                    receipt = await asyncio.to_thread(self.store.find_credential_activation,
                                                     operation_id=activation.get("operation_id", ""))
                    if (receipt is None or receipt["credential_revision"] != record.revision
                            or settings["active_profile_id"] is not None or settings["monitor_enabled"]
                            or settings["mock_order_enabled"]):
                        self.errors[profile_id] = "ACTIVATION_RECOVERY_REQUIRED"
                        continue
                    self._disabled_scopes[profile_id] = scope
                    self._revisions[profile_id] = record.revision
                    self._settings_revisions[profile_id] = settings["revision"]
                continue
            self._boot_tasks.append(asyncio.create_task(self._boot(profile_id, record), name="mock-profile-bootstrap"))

    async def _boot(self, profile_id, record) -> None:
        async with self._boot_slots:
            latest = await asyncio.to_thread(self.vault.load, "kiwoom_mock", profile_id)
            if (profile_id in self._pending_profiles or self.active_revision(profile_id) is not None
                    or latest is None or latest.revision != record.revision):
                return  # User activation supersedes a queued bootstrap; never restore its old key.
            self._booting.add(profile_id)
            await self._boot_record(profile_id, record)

    async def _boot_record(self, profile_id, record) -> None:
        candidate = None
        try:
            candidate = await self._prepare(profile_id, record, record.payload["credentials"], bootstrap=True)
            plan = candidate.prepared
            bindings = await asyncio.to_thread(self.store.load_account_bindings)
            if not any(b["credential_profile_id"] == profile_id for b in bindings):
                await asyncio.to_thread(bind_verified_account_identity, plan.identity, self.store,
                                        credential_profile_id=profile_id)
            settings = await asyncio.to_thread(self.store.load_account_settings, self._scope(plan.account_ref))
            if settings["revision"] == 0:
                await asyncio.to_thread(self.store.save_account_settings, {
                    "scope": settings["scope"], "active_profile_id": profile_id,
                    "monitor_enabled": (self._legacy_monitor_enabled
                        if profile_id == "nas-mock-default" and not record.payload.get("activation") else True),
                    "mock_order_enabled": bool(plan.previous and plan.previous.gateway is not None
                                               and not record.payload.get("activation")),
                }, expected_revision=0)
                plan.settings_revision = 1
            await self.drain(profile_id, candidate)
            await self.publish(profile_id, record, candidate)
            await self.resume(profile_id)
        except Exception:
            self.errors[profile_id] = "MOCK_ACCOUNT_STARTUP_FAILED"
            self._on_change(profile_id, None)
        finally:
            self._booting.discard(profile_id)
            if candidate is not None: self.release(profile_id, candidate)

    def _scope(self, account_ref):
        return {"broker": "kiwoom", "environment": "mock", "account_ref": account_ref}

    async def prepare(self, profile_id, current, credentials, disabled):
        if disabled:
            if self._closing or profile_id in self._booting or profile_id in self._pending_profiles:
                raise CredentialOperationError("PROFILE_BUSY")
            self._pending_profiles.add(profile_id)
            try:
                return await self._prepare_disable(profile_id, current)
            except Exception:
                self._pending_profiles.discard(profile_id)
                raise
        return await self._prepare(profile_id, current, credentials)

    async def _prepare_disable(self, profile_id, current):
        if self._closing or profile_id in self._booting:
            raise CredentialOperationError("PROFILE_BUSY")
        if profile_id in self.reserved_profiles:
            raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY", 503)
        previous = self._contexts.get(profile_id)
        activation = current.payload.get("activation", {}) if current else {}
        account_ref = previous.account_ref if previous else activation.get("account_ref")
        run_id = previous.run_id if previous else activation.get("run_id")
        if current is None or not account_ref or not run_id:
            raise CredentialOperationError("ACCOUNT_IDENTITY_UNVERIFIED")
        if activation:
            await asyncio.to_thread(self.store.finalize_credential_activation, {
                **activation, "provider": "kiwoom_mock", "profile_id": profile_id,
                "credential_revision": current.revision,
            })
        configuration = await asyncio.to_thread(self.store.load_account_settings, self._scope(account_ref))
        if configuration["active_profile_id"] not in {None, profile_id}:
            raise CredentialOperationError("ACCOUNT_PROFILE_CONFLICT")
        lock = self._account_locks.setdefault(account_ref, asyncio.Lock())
        if self._closing or lock.locked():
            raise CredentialOperationError("PROFILE_BUSY")
        await lock.acquire()
        self._pending_profiles.add(profile_id)
        from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient
        client = previous.client if previous else KiwoomRestClient(KiwoomSettings("", "", "mock"))
        broker = previous.broker if previous else CentralRestBroker(
            client, allowed_endpoints=MOCK_ACCOUNT_ENDPOINTS, namespace=f"mock-account:{account_ref}")
        plan = _MockCredentialPlan(client, broker, None, None, previous, account_ref, run_id,
            configuration["revision"], lock, self._revisions.get(profile_id),
            self._settings_revisions.get(profile_id), bool(previous and previous.monitor._task is not None))
        plan.previous_disabled = current.disabled
        return ValidatedCredential(account_ref=account_ref, run_id=run_id, prepared=plan)

    async def _prepare(self, profile_id, current, credentials, *, bootstrap=False):
        if profile_id in self.reserved_profiles:
            raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY", 503)
        if self._closing or (profile_id in self._booting and not bootstrap):
            raise CredentialOperationError("PROFILE_BUSY")
        if profile_id in self._pending_profiles:
            raise CredentialOperationError("PROFILE_BUSY")
        self._pending_profiles.add(profile_id)
        try:
            return await self._prepare_account(profile_id, current, credentials)
        except Exception:
            self._pending_profiles.discard(profile_id)
            raise

    async def _prepare_account(self, profile_id, current, credentials):
        settings = KiwoomSettings(credentials["app_key"], credentials["secret_key"], "mock")
        activation = current.payload.get("activation") if current else None
        if activation:
            # Complete the previous durable receipt before allowing a later key
            # to supersede its encrypted activation metadata.
            await asyncio.to_thread(self.store.finalize_credential_activation, {
                **activation, "provider": "kiwoom_mock", "profile_id": profile_id,
                "credential_revision": current.revision,
            })
        previous = self._contexts.get(profile_id)
        async with self._slots:
            if previous is not None:
                if previous.broker._credential_paused:
                    await previous.broker.end_credential_change()
                prepared = await previous.broker.prepare_credentials(settings)
                source = previous.broker
            else:
                async with self._probe_lock:
                    prepared = await self._probe.prepare_credentials(settings)
                    source = self._probe
            identity = KiwoomAccountIdentityReader(source, environment=AccountEnvironment.MOCK,
                hmac_key=self.hmac_key, now_provider=lambda: as_kst(source._client.server_now())
            ).identity_from_payload(prepared.account_payload)
            registered = await asyncio.to_thread(prepare_verified_account_identity, identity, self.store,
                                                 credential_profile_id=profile_id)
            account_ref = registered["account_ref"]
            bindings = await asyncio.to_thread(self.store.load_account_bindings)
            old = [b for b in bindings if b["credential_profile_id"] == profile_id]
            if ((old and old[-1]["account_ref"] != account_ref)
                    or (previous is not None and previous.account_ref != account_ref)):
                raise CredentialOperationError("ACCOUNT_CHANGED")
            configuration = await asyncio.to_thread(self.store.load_account_settings, self._scope(account_ref))
            if (account_ref in self.reserved_accounts
                    or configuration["active_profile_id"] not in {None, profile_id}
                    or any(b.account_ref == account_ref and p != profile_id for p, b in self._bundles.items())):
                raise CredentialOperationError("ACCOUNT_PROFILE_CONFLICT")
            lock = self._account_locks.setdefault(account_ref, asyncio.Lock())
            if lock.locked(): raise CredentialOperationError("PROFILE_BUSY")
            await lock.acquire()
            try:
                if previous is not None:
                    client, broker = previous.client, previous.broker
                else:
                    client = source._client.fork_verified_candidate(prepared)
                    broker = CentralRestBroker(client, allowed_endpoints=MOCK_ACCOUNT_ENDPOINTS,
                                               namespace=f"mock-account:{account_ref}")
                run_id = (previous.run_id if previous else
                          (current.payload.get("activation", {}).get("run_id") if current else None)) or str(uuid.uuid4())
                uuid.UUID(run_id)
                plan = _MockCredentialPlan(client, broker, prepared, identity, previous,
                    account_ref, run_id, configuration["revision"], lock,
                    self._revisions.get(profile_id), self._settings_revisions.get(profile_id),
                    bool(previous and previous.monitor._task is not None))
                plan.previous_disabled = bool(current and current.disabled)
                return ValidatedCredential(account_ref=account_ref, run_id=run_id, prepared=plan)
            except Exception:
                lock.release()
                if not any(b.account_ref == account_ref for b in self._bundles.values()):
                    self._account_locks.pop(account_ref, None)
                raise

    async def drain(self, profile_id, candidate):
        plan = candidate.prepared
        self._brokers.add(plan.broker)
        self._applying[profile_id] = plan
        self._bundles.pop(profile_id, None); self._revisions.pop(profile_id, None)
        self._settings_revisions.pop(profile_id, None)
        self._disabled_scopes.pop(profile_id, None)
        self._on_change(profile_id, None)
        if plan.previous is not None: await plan.previous.close(close_broker=False)
        await plan.broker.begin_credential_change()
        plan.drained = True
        configuration = await asyncio.to_thread(self.store.load_account_settings, self._scope(plan.account_ref))
        if (configuration["revision"] != plan.settings_revision
                or configuration["active_profile_id"] not in {None, profile_id}):
            raise RuntimeError("ACCOUNT_SETTINGS_REVISION_CONFLICT")

    async def publish(self, profile_id, record, candidate):
        plan = candidate.prepared; plan.committed = True
        if record.disabled:
            await asyncio.to_thread(plan.client.disable_credentials)
            configuration = await asyncio.to_thread(self.store.load_account_settings, self._scope(plan.account_ref))
            if configuration["active_profile_id"] is not None or configuration["monitor_enabled"] or configuration["mock_order_enabled"]:
                raise RuntimeError("ACCOUNT_SETTINGS_REVISION_CONFLICT")
            self._disabled_scopes[profile_id] = configuration["scope"]
            self._revisions[profile_id] = record.revision
            self._settings_revisions[profile_id] = configuration["revision"]
            self.errors.pop(profile_id, None)
            plan.published = True
            self._on_change(profile_id, None)
            return
        await plan.broker.activate_prepared_credentials(plan.prepared)
        await plan.broker.end_credential_change()
        bindings = await asyncio.to_thread(self.store.load_account_bindings)
        stored = [b for b in bindings if b["credential_profile_id"] == profile_id][-1]
        binding = AccountBinding(profile_id,
            AccountScope("kiwoom", AccountEnvironment.MOCK, plan.account_ref),
            stored["binding_revision"], datetime.fromisoformat(stored["verified_at"]), "ka00001")
        configuration = await asyncio.to_thread(self.store.load_account_settings, binding.scope.to_dict())
        if configuration["active_profile_id"] not in {None, profile_id}:
            raise RuntimeError("ACCOUNT_PROFILE_CONFLICT")
        bundle = MockAccountBundle(self.store, settings=plan.prepared.settings,
            account_ref=plan.account_ref, run_id=plan.run_id, credential_profile_id=profile_id,
            identity_hmac_key=self.hmac_key, order_transport_enabled=configuration["mock_order_enabled"],
            client=plan.client, broker=plan.broker, identity=plan.identity, binding=binding,
            require_initial_read=True, read_limiter=self._slots)
        self._contexts[profile_id] = bundle  # Retain failed committed transport for explicit recovery.
        try:
            if self._closing: raise RuntimeError("MOCK_OWNER_CLOSED")
            if configuration["monitor_enabled"]: await bundle.start()
            latest = await asyncio.to_thread(self.store.load_account_settings, binding.scope.to_dict())
            if self._closing or latest["revision"] != configuration["revision"]:
                raise RuntimeError("ACCOUNT_SETTINGS_REVISION_CONFLICT")
        except Exception:
            await bundle.close(close_broker=False)
            await plan.broker.begin_credential_change()
            raise
        self._bundles[profile_id] = bundle
        self._revisions[profile_id] = record.revision
        self._settings_revisions[profile_id] = configuration["revision"]
        self.errors.pop(profile_id, None)
        plan.published = True
        self._on_change(profile_id, bundle)

    async def resume(self, profile_id):
        plan = self._applying.get(profile_id)
        if plan is None or plan.published: return
        if plan.committed: raise RuntimeError("ACTIVATION_RECOVERY_REQUIRED")
        if plan.previous_disabled and plan.previous_revision is not None:
            await asyncio.to_thread(plan.client.disable_credentials)
            self._disabled_scopes[profile_id] = self._scope(plan.account_ref)
            self._revisions[profile_id] = plan.previous_revision
            self._settings_revisions[profile_id] = plan.previous_settings_revision
            if plan.previous is None:
                await plan.broker.close()
                self._brokers.discard(plan.broker)
            self._on_change(profile_id, None)
            plan.resumed = True
            return
        if plan.broker._credential_paused: await plan.broker.end_credential_change()
        previous = plan.previous
        if previous is not None and plan.previous_revision is not None and not self._closing:
            restored = MockAccountBundle(self.store, settings=plan.client._settings,
                account_ref=previous.account_ref, run_id=previous.run_id, credential_profile_id=profile_id,
                identity_hmac_key=self.hmac_key, order_transport_enabled=previous.gateway is not None,
                client=plan.client, broker=plan.broker, identity=previous._identity, binding=previous.binding,
                require_initial_read=True, read_limiter=self._slots)
            try:
                if plan.previous_monitor_enabled:
                    await restored.start()
            except Exception:
                await restored.close(close_broker=False)
                await plan.broker.begin_credential_change()
                raise
            self._contexts[profile_id] = restored; self._bundles[profile_id] = restored
            self._revisions[profile_id] = plan.previous_revision
            self._settings_revisions[profile_id] = plan.previous_settings_revision
            self._on_change(profile_id, restored)
        elif previous is None:
            await plan.broker.close()
            self._brokers.discard(plan.broker)
        plan.resumed = True

    def release(self, profile_id, candidate):
        plan = candidate.prepared
        if not isinstance(plan, _MockCredentialPlan): return
        self._pending_profiles.discard(profile_id)
        if self._applying.get(profile_id) is plan:
            self._applying.pop(profile_id)
            if not plan.published and not plan.resumed:
                self.errors[profile_id] = (
                    "ACTIVATION_RECOVERY_REQUIRED" if plan.committed else "MOCK_RUNTIME_RECOVERY_REQUIRED"
                )
        if plan.lock is not None:
            if plan.lock.locked(): plan.lock.release()
            if not any(b.account_ref == plan.account_ref for b in self._bundles.values()):
                self._account_locks.pop(plan.account_ref, None)
        plan.lock = None; plan.prepared = None; plan.identity = None

    async def close(self):
        self._closing = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="mock-credential-owner-close")
        await asyncio.shield(self._close_task)

    async def _close(self):
        if self._start_task is not None:
            await asyncio.gather(asyncio.shield(self._start_task), return_exceptions=True)
        await asyncio.gather(*self._boot_tasks, return_exceptions=True)
        await asyncio.gather(*self._settings_tasks, return_exceptions=True)
        for bundle in list(self._contexts.values()):
            await bundle.close()
            await bundle.broker.close()
        for broker in self._brokers:
            await broker.close()
        await self._probe.close()
        self._bundles.clear(); self._revisions.clear(); self._settings_revisions.clear()

    async def update_settings(self, scope, value, expected_revision):
        """The owned apply survives cancellation of its HTTP waiter."""
        task = asyncio.create_task(self._update_settings(scope, value, expected_revision),
                                   name="mock-account-settings-apply")
        self._settings_tasks.add(task)
        task.add_done_callback(self._settings_tasks.discard)
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        return await asyncio.shield(task)

    async def _update_settings(self, scope, value, expected_revision):
        if (set(value) != {"active_profile_id", "monitor_enabled", "mock_order_enabled"}
                or type(expected_revision) is not int or not 0 <= expected_revision < 2**63
                or type(value["monitor_enabled"]) is not bool or type(value["mock_order_enabled"]) is not bool
                or (value["mock_order_enabled"] and not value["monitor_enabled"])):
            raise CredentialOperationError("ACCOUNT_SETTINGS_INVALID", 422)
        if scope.get("environment") != "mock" or scope.get("broker") != "kiwoom":
            raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY", 503)
        try:
            current = await asyncio.to_thread(self.store.load_account_settings, scope)
        except ValueError as error:
            code = str(error)
            if code not in {"ACCOUNT_IDENTITY_UNVERIFIED", "ACCOUNT_SETTINGS_RECOVERY_REQUIRED"}:
                code = "ACCOUNT_SETTINGS_INVALID"
            raise CredentialOperationError(code, 404 if code == "ACCOUNT_IDENTITY_UNVERIFIED" else 409) from None
        profile_id = current["active_profile_id"]
        if current["revision"] != expected_revision:
            raise CredentialOperationError("ACCOUNT_SETTINGS_REVISION_CONFLICT")
        if value["active_profile_id"] != profile_id or profile_id is None:
            raise CredentialOperationError("ACCOUNT_PROFILE_UNAVAILABLE")
        record = await asyncio.to_thread(self.vault.load, "kiwoom_mock", profile_id)
        old = self._contexts.get(profile_id)
        if old is None or old.binding is None or record is None or record.disabled:
            raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY", 503)
        credentials = record.payload["credentials"]
        if (old.client._settings.app_key != credentials.get("app_key")
                or old.client._settings.secret_key != credentials.get("secret_key")
                or self.active_revision(profile_id) not in {None, record.revision}):
            raise CredentialOperationError("PROFILE_RUNTIME_NOT_READY", 503)
        lock = self._account_locks.setdefault(old.account_ref, asyncio.Lock())
        if self._closing or lock.locked() or profile_id in self._pending_profiles or profile_id in self._booting:
            raise CredentialOperationError("PROFILE_BUSY")
        await lock.acquire()
        self._pending_profiles.add(profile_id)
        new = None
        saved = None
        credential_revision = record.revision
        rollback = _MockCredentialPlan(old.client, old.broker, None, old._identity, old,
            old.account_ref, old.run_id, expected_revision, lock, self._revisions.get(profile_id),
            self._settings_revisions.get(profile_id), old.monitor._task is not None)
        self._applying[profile_id] = rollback
        try:
            if ({**value, "scope": current["scope"], "revision": expected_revision} == current
                    and self._settings_revisions.get(profile_id) == expected_revision):
                return {"settings": current, "applied_revision": self._settings_revisions.get(profile_id)}
            self._bundles.pop(profile_id, None)
            self._revisions.pop(profile_id, None)
            self._settings_revisions.pop(profile_id, None)
            self._on_change(profile_id, None)
            await old.close(close_broker=False)
            await old.broker.begin_credential_change()
            saved = await asyncio.to_thread(self.store.save_account_settings,
                {**value, "scope": scope}, expected_revision=expected_revision)
            if self._closing: raise RuntimeError("MOCK_OWNER_CLOSED")
            await old.broker.end_credential_change()
            new = MockAccountBundle(self.store, settings=old.client._settings, account_ref=old.account_ref,
                run_id=old.run_id, credential_profile_id=profile_id, identity_hmac_key=self.hmac_key,
                order_transport_enabled=saved["mock_order_enabled"], client=old.client, broker=old.broker,
                identity=old._identity, binding=old.binding, require_initial_read=True, read_limiter=self._slots)
            self._contexts[profile_id] = new
            if saved["monitor_enabled"]: await new.start()
            latest = await asyncio.to_thread(self.store.load_account_settings, scope)
            if self._closing or latest["revision"] != saved["revision"]:
                raise RuntimeError("ACCOUNT_SETTINGS_REVISION_CONFLICT")
            self._bundles[profile_id] = new
            self._revisions[profile_id] = credential_revision
            self._settings_revisions[profile_id] = saved["revision"]
            self.errors.pop(profile_id, None)
            self._on_change(profile_id, new)
            return {"settings": saved, "applied_revision": saved["revision"]}
        except Exception:
            if new is not None: await new.close(close_broker=False)
            await old.broker.begin_credential_change()
            if saved is None and not self._closing:
                try:
                    latest = await asyncio.to_thread(self.store.load_account_settings, scope)
                    if latest == current and rollback.previous_revision is not None:
                        await self.resume(profile_id)
                        raise CredentialOperationError("ACCOUNT_SETTINGS_APPLY_FAILED", 503)
                except CredentialOperationError:
                    raise
                except Exception:
                    pass
            self.errors[profile_id] = "ACCOUNT_SETTINGS_RECOVERY_REQUIRED"
            raise CredentialOperationError("ACCOUNT_SETTINGS_RECOVERY_REQUIRED", 503) from None
        finally:
            self._applying.pop(profile_id, None)
            self._pending_profiles.discard(profile_id)
            lock.release()


class MockAccountBundle:
    """A bundle is never retargeted to another account, run or credential epoch.

    The future activation owner may reuse a drained client/broker, but constructs
    a new bundle for the monitor, lease and WebSocket identity instead of
    changing the identity captured by an already running connection.
    """

    def __init__(
        self, store: Any, *, settings: KiwoomSettings, account_ref: str,
        run_id: str, credential_profile_id: str = "nas-mock-default",
        identity_hmac_key: bytes | None = None,
        order_transport_enabled: bool = False,
        client: Any = None, broker: CentralRestBroker | None = None,
        identity: Any = None, binding: Any = None, require_initial_read: bool = False,
        read_limiter: asyncio.Semaphore | None = None,
    ) -> None:
        from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient

        if settings.environment != "mock":
            raise ValueError("MOCK_ENVIRONMENT_REQUIRED")
        if not account_ref or not run_id or not credential_profile_id:
            raise ValueError("MOCK_ACCOUNT_CONTEXT_REQUIRED")
        self._account_ref = account_ref
        self._run_id = run_id
        self._credential_profile_id = credential_profile_id
        self._store = store
        self._hmac_key = identity_hmac_key
        if (client is None) != (broker is None) or (identity is None) != (binding is None):
            raise ValueError("MOCK_BUNDLE_CONTEXT_INVALID")
        if binding is not None and (binding.scope.account_ref != account_ref
                or binding.credential_profile_id != credential_profile_id
                or binding.scope.environment != AccountEnvironment.MOCK
                or identity.environment != AccountEnvironment.MOCK):
            raise ValueError("MOCK_BUNDLE_CONTEXT_INVALID")
        self._identity = identity
        self._binding = binding
        self._require_initial_read = require_initial_read
        self._closing = False
        self._start_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self.client = client if client is not None else KiwoomRestClient(settings)
        self.broker = broker if broker is not None else CentralRestBroker(
            self.client, allowed_endpoints=MOCK_ACCOUNT_ENDPOINTS,
            namespace=f"mock-account:{account_ref}",
        )
        self.account_queries = AccountQuerySessionManager(self.broker,
            lambda: self._binding if not self._closing else None, read_limiter=read_limiter)
        self.repository = ExecutionRepository(store)
        now = lambda: as_kst(self.client.server_now())
        transport = make_mock_transport(self.client) if order_transport_enabled else None
        self.runtime = ExecutionRuntime(
            OrderLifecycle(self.repository, transport, now_provider=now), self.repository,
            account_ref=account_ref, run_id=run_id, owner_token=uuid.uuid4().hex,
        )
        self.monitor = MockAccountMonitor(
            KiwoomMockAccountReader(
                self.broker, environment="mock", account_ref=account_ref, now_provider=now,
            ), self.runtime, self.repository, now_provider=now, read_limiter=read_limiter,
        )
        async def token_with_limit():
            async with read_limiter:
                return await asyncio.to_thread(self.client.get_access_token)
        self.realtime = MockAccountRealtimeCollector(
            self.client.get_access_token, self.monitor.handle_realtime, now,
            account_scope_resolver=self._resolve_scope if identity_hmac_key is not None else None,
            async_token_provider=token_with_limit if read_limiter is not None else None,
        )
        self.gateway = ManualMockOrderGateway(
            self.runtime, self.repository, self.monitor.refresh_account, now_provider=now,
        ) if order_transport_enabled else None

    @property
    def account_ref(self) -> str:
        return self._account_ref

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def credential_profile_id(self) -> str:
        return self._credential_profile_id

    @property
    def binding(self):
        return self._binding

    def _resolve_scope(self, raw_account_number: str):
        if self._closing or self._identity is None or self._binding is None:
            return None
        try:
            fingerprint = account_identity_fingerprint(
                raw_account_number, AccountEnvironment.MOCK, self._hmac_key,
            )
        except ValueError:
            return None
        return self._binding.scope if hmac.compare_digest(
            fingerprint, self._identity.identity_fingerprint,
        ) else None

    async def start(self) -> None:
        if self._closing:
            raise RuntimeError("MOCK_BUNDLE_CLOSED")
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start(), name="mock-bundle-start")
        await asyncio.shield(self._start_task)

    async def _start(self) -> None:
        await self.broker.start()
        if self._hmac_key is not None and self._binding is None:
            identity = await KiwoomAccountIdentityReader(
                self.broker, environment=AccountEnvironment.MOCK,
                hmac_key=self._hmac_key, now_provider=lambda: as_kst(self.client.server_now()),
            ).verify()
            prepared = await asyncio.to_thread(
                prepare_verified_account_identity, identity, self._store,
                credential_profile_id=self.credential_profile_id,
            )
            if prepared["account_ref"] != self.account_ref:
                raise RuntimeError("ACCOUNT_CONTEXT_MISMATCH")
            self._binding = await asyncio.to_thread(
                bind_verified_account_identity, identity, self._store,
                credential_profile_id=self.credential_profile_id,
            )
            self._identity = identity
        if self._closing:
            return
        if self._require_initial_read:
            await self.monitor.start(initial_read=True)
        else:
            await self.monitor.start()
        if not self._closing:
            await self.realtime.start()

    async def close(self, *, close_broker: bool = True) -> None:
        self._closing = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(close_broker), name="mock-bundle-close")
        await asyncio.shield(self._close_task)

    async def _close(self, close_broker: bool) -> None:
        if self._start_task is not None:
            await asyncio.gather(asyncio.shield(self._start_task), return_exceptions=True)
        # Each stage owns its actual work. Do not release the lease while an
        # accepted gateway command, socket callback or monitor write can finish.
        if self.gateway is not None:
            await self.gateway.close()
        await self.account_queries.close()
        await self.realtime.close()
        await self.monitor.close()
        if close_broker:
            await self.broker.close()
